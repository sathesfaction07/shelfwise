# ADR 001: Separate deterministic processing from LLM calls

## Status

Accepted

## Context

The pipeline runs four agents over every product. Two of those agents—Discovery and Price & Rating—do work that is entirely computable from structured data: arithmetic over star ratings and review counts, price-change percentages, tiered ranking by a weighted formula. Nothing in that work requires language understanding. Running it through a model would cost tokens, add latency, and introduce non-determinism into outputs that are straightforwardly reproducible.

The other two agents—Review Intelligence and Recommendation—have a different character. Review Intelligence must extract themes and summarise sentiment from unstructured natural-language text: that is precisely what language models are for. Recommendation must translate a set of numeric scores into a plain-language judgement a person can act on.

The risk with LLM-heavy pipelines is boundary creep: progressively moving decisions into prompts because it is convenient, until the pipeline is expensive, slow, and hard to test. The design names the boundary explicitly and enforces it per agent.

## Decision

Two agents call an LLM. Two do not.

**DiscoveryAgent** (`discovery.py`) uses no LLM. It calls `self.provider.search_products()` and `self.provider.get_reviews()`, then runs three deterministic ranking tiers:

- **Tier A – Trending:** products with `monthly_purchases >= TRENDING_MIN_MONTHLY_PURCHASES` and rating `>= TRENDING_MIN_AVG_RATING`, or with recent-review velocity above `TRENDING_MIN_RECENT_REVIEWS`. Sorted by `(monthly_purchases, recent_review_count, recent_avg_rating)`.
- **Tier B – Best Sellers:** products rated `>= BESTSELLER_MIN_RATING`, ranked by `rating * log10(review_count + 1)`.
- **Tier C – Guarded Fallback:** any remaining product whose worst negative-review signal (recent window or lifetime rating histogram) is below `DISCOVERY_MAX_NEGATIVE_PERCENT`.

All thresholds live in `product_intelligence.config`. The agent imports no `llm` module. The module docstring records the rationale explicitly: "No LLM call here: this is pure data + arithmetic."

**PriceRatingAgent** (`pricing.py`) uses no LLM. It calls `analyze_price_history()` and `rating_change()`—both pure functions over structured provider data—and returns price-change percentage, current/previous price, and monthly rating trend. The module docstring states: "arithmetic belongs to Python."

**ReviewIntelligenceAgent** (`review.py`) uses an LLM for one step only. Python handles everything up to and including the model input: `get_reviews()` → 90-day filter → `deduplicate()` → `calculate_statistics()` → `sample_representative()`. The LLM receives a structured prompt containing only the computed statistics and a small representative sample—never the raw review corpus—and is asked to return JSON with keys `positive_themes`, `negative_themes`, `recurring_complaints`, `recurring_strengths`, `overall_sentiment`, and `summary`. The model is `config.MODEL_ID_TOOLS` (Nova Micro by default; the code notes this is 97% cheaper than Haiku). The `_summarize()` method catches all exceptions and falls back to `mock_llm.mock_review_summary()`, so a model outage degrades gracefully rather than crashing.

**RecommendationAgent** (`recommendation.py`) uses an LLM for explanation only. The module-level `compute_score()` function is a pure deterministic weighted sum of four normalised sub-scores:

| Sub-score | Source | Input range |
|:--|:--|:--|
| `review_score` | `positive_percentage` from ReviewIntelligenceAgent | 0–100 → 0.0–1.0 |
| `rating_score` | product star rating | 1–5 → 0.0–1.0 |
| `price_score` | negated price-change % (discount is good) | −30–+30 % → 0.0–1.0 |
| `trend_score` | `rating_change` from PriceRatingAgent | −1.0–+1.0 → 0.0–1.0 |

Weights are read from `config.SCORE_WEIGHTS` so they can be adjusted without touching the function. The `_explain()` method passes the already-computed scores to the LLM with an explicit instruction: "already scored by deterministic analysis, do not recompute the score, only interpret it." The model selects a winner and writes a reason in natural language; it never produces or modifies a number. Falls back to `mock_llm.mock_recommendation()` on any exception.

The structural enforcement is deliberate: agents that must not call a model do not import `product_intelligence.llm`. The absence of that import is machine-checkable and does not rely on developer discipline.

## Consequences

**Good**

- Discovery and Price & Rating are fully unit-testable without mocking a model: construct inputs, call the function, assert on outputs. This is the cheapest and fastest class of test, and neither agent has a probabilistic output to account for.
- LLM cost is bounded and predictable. Only two agents ever invoice a model, both cap requests at `max_tokens=300`, and neither sends raw review text. ReviewIntelligenceAgent sends a representative sample, so token consumption scales with sample size rather than total review volume.
- The overall score for a product is reproducible across runs. The LLM can change the wording of the explanation, not the underlying number. Debugging a score means inspecting `compute_score()` inputs; no prompt archaeology required.
- Both LLM agents accept `use_mock=True`, letting the full pipeline run in CI and local development with no model dependency or cost. Mock paths are first-class code, not test scaffolding.

**Bad**

- ReviewIntelligenceAgent's `_summarize()` output is non-deterministic: two identical inputs can produce different themes. Downstream consumers (Recommendation, the output formatter) must treat extracted themes as advisory text, not stable identifiers.
- Recommendation's explanation is only as good as the weights. The LLM interprets four numbers honestly; if `config.SCORE_WEIGHTS` are poorly calibrated, the explanation will accurately describe a misleading score. The model cannot compensate for a bad weighting.
- Both LLM agents fall back silently to mock output on failure. The mock result is structurally identical to a real result except for `analysis_source: "mock"` (review) or the absence of a live-model flag (recommendation). Callers that do not check that field will not know they received a mock.

**Neutral**

- The split produces two distinct latency profiles. Discovery and Price & Rating are provider-call + arithmetic speed. ReviewIntelligenceAgent and RecommendationAgent each add one model round-trip. End-to-end latency is dominated by those two calls, and running Discovery and Price & Rating concurrently with the LLM agents yields limited parallelism benefit.
- `compute_score()` is a module-level function, not a method, which makes it importable and independently testable without instantiating `RecommendationAgent`. This is a useful side-effect of making scoring deterministic, not a stated goal.
