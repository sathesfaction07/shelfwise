"""
Review Intelligence Agent.

Python does: fetch, 90-day filter, dedupe, statistics, representative
sampling (spec sections 6, 15-17).
LLM does: theme extraction, summarization, sentiment interpretation -
and ONLY over the small, already-reduced sample (never raw reviews).
"""
from product_intelligence import config
from product_intelligence import llm as llm_client
from product_intelligence.analysis import reviews as review_analysis
from product_intelligence.observability.logging import log_event
from product_intelligence.observability.metrics import MetricsClient
from product_intelligence.tools import mock_llm
from product_intelligence.tools.token_optimizer import measure_reduction


class ReviewIntelligenceAgent:
    def __init__(
        self, provider, metrics_client: MetricsClient | None = None, use_mock: bool = False
    ):
        self.provider = provider
        self.metrics = metrics_client or MetricsClient()
        self.use_mock = use_mock
        self.allow_partial = False

    def analyze(self, asin: str, country: str, product_title: str = "",
                window_days: int | None = None) -> dict:
        window_days = window_days or config.REVIEW_WINDOW_DAYS
        all_reviews = self.provider.get_reviews(asin, country)
        self.metrics.publish(
            "ReviewsFetched", len(all_reviews), dimensions={"AgentName": "review_intelligence"}
        )

        recent = review_analysis.filter_recent(all_reviews, window_days)
        deduped = review_analysis.deduplicate(recent)
        stats = review_analysis.calculate_statistics(deduped)
        sample = review_analysis.sample_representative(deduped)
        if config.AWS_MODE == "floci":
            # Keep statistics over all reviews; bound only the evidence sent to
            # the local model and measure token reduction against that sample.
            sample = {
                sentiment: [
                    {**review, "text": review.get("text", "")[:config.LOCAL_REVIEW_EXCERPT_CHARS]}
                    for review in items[:config.LOCAL_REVIEW_SAMPLE_PER_SENTIMENT]
                ]
                for sentiment, items in sample.items()
            }

        self.metrics.publish(
            "ReviewsAnalyzed", len(deduped), dimensions={"AgentName": "review_intelligence"}
        )

        if not deduped:
            log_event("review_analysis_no_data", asin=asin, country=country)
            return {
                "asin": asin,
                "review_period_days": window_days,
                **stats,
                "positive_themes": [],
                "negative_themes": [],
                "summary": "No reviews were found for this ASIN within the requested period. "
                            "Recommendation confidence has been reduced accordingly.",
                "tokens": {"raw_estimate": 0, "optimized_estimate": 0, "reduction_percent": 0.0},
            }

        reduction = measure_reduction(all_reviews, sample)

        llm_output = self._summarize(product_title, stats, sample)

        log_event(
            "review_analysis",
            asin=asin,
            country=country,
            review_count=stats["review_count"],
            raw_estimated_tokens=reduction.raw_estimate,
            optimized_estimated_tokens=reduction.optimized_estimate,
            token_reduction_percent=reduction.reduction_percent,
        )
        self.metrics.publish(
            "RawEstimatedTokens",
            reduction.raw_estimate,
            dimensions={"AgentName": "review_intelligence"},
        )
        self.metrics.publish(
            "OptimizedEstimatedTokens",
            reduction.optimized_estimate,
            dimensions={"AgentName": "review_intelligence"},
        )
        self.metrics.publish("TokenReductionPercent", reduction.reduction_percent, unit="Percent",
                              dimensions={"AgentName": "review_intelligence"})

        return {
            "asin": asin,
            "review_period_days": window_days,
            **stats,
            "positive_themes": llm_output["positive_themes"],
            "negative_themes": llm_output["negative_themes"],
            "summary": llm_output["summary"],
            "overall_sentiment": llm_output["overall_sentiment"],
            "analysis_source": llm_output.get("source", "llm"),
            "analysis_error": llm_output.get("error"),
            "tokens": reduction.as_dict(),
        }

    def _summarize(self, product_title: str, stats: dict, sample: dict) -> dict:
        if self.use_mock:
            return mock_llm.mock_review_summary(product_title, stats, sample)

        prompt = self._build_prompt(product_title, stats, sample)
        system_prompt = (
            "You analyze customer reviews. Use only the supplied review evidence. "
            "Return JSON with keys: positive_themes (list), negative_themes (list), "
            "recurring_complaints (list), recurring_strengths (list), overall_sentiment "
            "(positive/mixed/negative), summary (one short sentence). Do not invent information. "
            "Respond with ONLY the raw JSON object — no markdown fences, no prose."
        )
        if config.AWS_MODE == "floci":
            system_prompt = (
                "Use only the supplied review excerpts and statistics. Return ONLY JSON: "
                "positive_themes and negative_themes (each at most 2 short phrases), "
                "overall_sentiment (positive/mixed/negative), summary (at most 20 words). "
                "Do not add other keys or invent facts."
            )
            prompt += (
                "\nReturn ONE JSON object in exactly this structure, "
                "filling values from the evidence: "
                '{"positive_themes": [], "negative_themes": [], '
                '"overall_sentiment": "mixed", "summary": ""}. '
                "Each list: at most 2 short phrases. Summary: at most 20 words. "
                "Do not use headings, separate objects or markdown."
            )
        try:
            # Use Nova Micro for theme extraction (97% cheaper than Haiku).
            # Falls back to Haiku if MODEL_ID_TOOLS is unset or same as MODEL_ID.
            result = llm_client.invoke(
                prompt=prompt,
                system_prompt=system_prompt,
                model_id=config.MODEL_ID_TOOLS,
                max_tokens=300,
                agent_name="review_intelligence",
                metrics_client=self.metrics,
            )
            parsed = llm_client.extract_json(result.text)
            if (not isinstance(parsed.get("positive_themes"), list)
                    or not isinstance(parsed.get("negative_themes"), list)
                    or not isinstance(parsed.get("summary"), str)
                    or parsed.get("overall_sentiment") not in {"positive", "mixed", "negative"}):
                raise ValueError("Review model response did not match the required JSON schema")
            return parsed
        except Exception as exc:
            if self.allow_partial:
                log_event("review_summary_unavailable", reason=str(exc))
                return {
                    "positive_themes": [],
                    "negative_themes": [],
                    "overall_sentiment": "unavailable",
                    "source": "unavailable",
                    "error": str(exc),
                    "summary": (
                        "AI summary unavailable. Review statistics use the collected evidence."
                    ),
                }
            if config.REQUIRE_LLM_SUCCESS:
                raise
            log_event("review_llm_fallback_to_mock", reason=str(exc))
            return mock_llm.mock_review_summary(product_title, stats, sample)

    @staticmethod
    def _build_prompt(product_title: str, stats: dict, sample: dict) -> str:
        positive_excerpts = "\n".join(f"- {r['text']}" for r in sample["positive"])
        negative_excerpts = "\n".join(f"- {r['text']}" for r in sample["negative"])
        return (
            f"Product: {product_title}\n"
            f"Review statistics: {stats}\n\n"
            f"Representative positive reviews:\n{positive_excerpts}\n\n"
            f"Representative negative reviews:\n{negative_excerpts}\n\n"
            "Identify: 1) top positive themes 2) top negative themes "
            "3) recurring complaints 4) recurring strengths 5) overall recent sentiment."
        )
