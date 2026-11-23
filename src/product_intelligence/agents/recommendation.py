"""
Recommendation Agent.

Python computes a deterministic overall_score first (spec section 23) -
the LLM never invents the score, it only explains it. This is the last
stop: it reasons over already-processed evidence from the other three
agents, it does not re-fetch reviews itself (spec section 8).
"""
import json

from product_intelligence import config
from product_intelligence.tools import mock_llm
from product_intelligence.observability.logging import log_event
from product_intelligence.observability.metrics import MetricsClient
from product_intelligence import llm as llm_client


def _normalize(value: float | None, lo: float, hi: float) -> float:
    """Maps value into 0..1 given an expected [lo, hi] range. Missing data -> neutral 0.5."""
    if value is None:
        return 0.5
    if hi == lo:
        return 0.5
    normalized = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, normalized))


def compute_score(product: dict, review_result: dict, pricing_result: dict) -> dict:
    """
    Deterministic weighted score - see spec section 23. Weights are
    config-driven (product_intelligence.config.SCORE_WEIGHTS) so they can
    be tuned without touching this function.
    """
    review_score = _normalize(review_result.get("positive_percentage"), 0, 100)
    rating_score = _normalize(product.get("rating"), 1, 5)

    # Lower price change (bigger discount) is better; clamp to a +/-30% range
    price_change = pricing_result.get("price_change_percent")
    price_score = _normalize(-price_change if price_change is not None else None, -30, 30)

    rating_change = pricing_result.get("rating_change")
    trend_score = _normalize(rating_change, -1.0, 1.0)

    weights = config.SCORE_WEIGHTS
    overall_score = (
        review_score * weights["review"]
        + rating_score * weights["rating"]
        + price_score * weights["price"]
        + trend_score * weights["trend"]
    )

    return {
        "review_score": round(review_score, 3),
        "rating_score": round(rating_score, 3),
        "price_score": round(price_score, 3),
        "trend_score": round(trend_score, 3),
        "overall_score": round(overall_score, 3),
    }


class RecommendationAgent:
    def __init__(self, metrics_client: MetricsClient | None = None, use_mock: bool = False):
        self.metrics = metrics_client or MetricsClient()
        self.use_mock = use_mock
        self.allow_partial = False

    def recommend(self, evaluated_candidates: list[dict]) -> dict:
        """
        evaluated_candidates: list of dicts, each combining {product, reviews,
        pricing, scores} for one ASIN (already scored via compute_score).
        """
        if not evaluated_candidates:
            log_event("recommendation_no_candidates")
            return mock_llm.mock_recommendation([])

        scored_for_llm = [
            {
                "asin": c["product"]["asin"],
                "overall_score": c["scores"]["overall_score"],
                "positive_percentage": c["reviews"].get("positive_percentage"),
                "negative_percentage": c["reviews"].get("negative_percentage"),
                "price_change_percent": c["pricing"].get("price_change_percent"),
                "rating_change": c["pricing"].get("rating_change"),
            }
            for c in evaluated_candidates
        ]

        try:
            result = self._explain(scored_for_llm)
        except Exception as exc:
            if not self.allow_partial:
                raise
            best = max(evaluated_candidates, key=lambda c: c["scores"]["overall_score"])
            score = best["scores"]["overall_score"]
            result = {
                "asin": best["product"]["asin"], "recommendation": "score_based",
                "reason": f"Highest computed score ({score:.3f}) among {len(evaluated_candidates)} candidates. AI explanation unavailable.",
                "strengths": ["Highest score under the configured ranking weights"],
                "weaknesses": ["Model explanation unavailable", "Ranking uses only available evidence"],
                "confidence": None, "source": "deterministic", "analysis_error": str(exc),
            }

        log_event(
            "recommendation",
            asin=result["asin"],
            recommendation=result["recommendation"],
            confidence=result["confidence"],
        )
        self.metrics.publish("ToolInvocations", 1, dimensions={"AgentName": "recommendation"})

        return result

    def _explain(self, scored_candidates: list[dict]) -> dict:
        if self.use_mock:
            return mock_llm.mock_recommendation(scored_candidates)

        prompt = (
            "You are a product intelligence analyst. Use only the supplied "
            "product, review, rating and price evidence. Select the strongest "
            "candidate and explain: strongest evidence, major weaknesses, price "
            "position, recent review sentiment, and confidence.\n\n"
            f"Candidates (already scored by deterministic analysis, do not "
            f"recompute the score, only interpret it):\n{scored_candidates}\n\n"
            "Return JSON with keys: recommendation (strong_candidate/moderate_candidate/"
            "weak_candidate), asin, reason, strengths (list), weaknesses (list), confidence (0-1). "
            "Respond with ONLY the raw JSON object — no markdown fences, no prose."
        )
        try:
            if config.AWS_MODE == "floci":
                prompt += " Keep reason under 25 words and strengths/weaknesses to at most 2 short phrases each."
                prompt += (' Return exactly ONE JSON object: {"recommendation":"moderate_candidate",'
                           '"asin":"<choose from candidates>","reason":"",'
                           '"strengths":[],"weaknesses":[],"confidence":0.0}. Fill from the evidence.')
            result = llm_client.invoke(
                prompt=prompt,
                system_prompt="Do not invent facts. Only reason over the supplied evidence.",
                max_tokens=300,
                agent_name="recommendation",
                metrics_client=self.metrics,
            )
            parsed = llm_client.extract_json(result.text)
            if (parsed.get("asin") not in {c["asin"] for c in scored_candidates}
                    or parsed.get("recommendation") not in {"strong_candidate", "moderate_candidate", "weak_candidate"}
                    or not isinstance(parsed.get("reason"), str)
                    or not isinstance(parsed.get("strengths"), list)
                    or not isinstance(parsed.get("weaknesses"), list)
                    or not isinstance(parsed.get("confidence"), (int, float))
                    or not 0 <= parsed["confidence"] <= 1):
                raise ValueError("Recommendation model response did not match the required JSON schema")
            return parsed
        except Exception as exc:
            if config.REQUIRE_LLM_SUCCESS or self.allow_partial:
                raise
            log_event("recommendation_llm_fallback_to_mock", reason=str(exc))
            return mock_llm.mock_recommendation(scored_candidates)
