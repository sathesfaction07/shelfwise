# ADR 002: Dual-model strategy — Claude Haiku for reasoning, Nova Micro for pattern matching

## Status

Accepted

## Context

The pipeline calls an LLM in exactly two places: ReviewIntelligenceAgent (`review.py`) and RecommendationAgent (`recommendation.py`). Both are capped at `max_tokens=300` and receive a pre-reduced prompt (never raw data), so the per-call token volume is modest. Despite that, model selection still has a material unit-economics effect once the pipeline runs across many ASINs or accounts.

The two tasks are qualitatively different:

**Theme extraction** (ReviewIntelligenceAgent) receives a structured prompt containing pre-computed statistics and a small representative sample. It must output a fixed JSON schema (`positive_themes`, `negative_themes`, `recurring_complaints`, `recurring_strengths`, `overall_sentiment`, `summary`). This is pattern-matching and slot-filling over constrained input — the kind of work where a small, fast model is sufficient and a larger model adds no measurable accuracy.

**Reasoning and explanation** (RecommendationAgent) receives four normalised sub-scores already computed by deterministic code and must: (a) select the strongest candidate from a set, and (b) write a plain-language justification grounded in those scores. This requires light reasoning over multiple values and the production of coherent natural language, which benefits from a higher-capability model.

Running the same model for both tasks means either overpaying for pattern-matching or under-serving the reasoning step. The pricing data makes the gap concrete:

| Model | Input (per MTok) | Output (per MTok) |
|:---|---:|---:|
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | $1.00 | $5.00 |
| `amazon.nova-micro-v1:0` | $0.035 | $0.14 |

Nova Micro is **~29× cheaper on input tokens** ($1.00 vs $0.035) and **~36× cheaper on output tokens** ($5.00 vs $0.14) — approximately 97% cheaper across both dimensions. Using it for every LLM call would leave reasoning quality at risk; using Haiku everywhere would multiply theme-extraction costs by nearly 29× for no capability gain.

## Decision

Two Bedrock model IDs are configured: `MODEL_ID` (Claude Haiku 4.5) for reasoning tasks and `MODEL_ID_TOOLS` (Nova Micro) for pattern-matching tasks. Each defaults in `config.py`:

```python
MODEL_ID       = "us.anthropic.claude-haiku-4-5-20251001-v1:0"   # reasoning / explanation
MODEL_ID_TOOLS = "amazon.nova-micro-v1:0"                         # theme extraction / slot-fill
```

**ReviewIntelligenceAgent** explicitly passes `model_id=config.MODEL_ID_TOOLS` when calling `llm_client.invoke()`. The code comment records the rationale: `# Use Nova Micro for theme extraction (97% cheaper than Haiku)`. If `MODEL_ID_TOOLS` is unset or set to the same value as `MODEL_ID`, the agent falls back to Haiku transparently.

**RecommendationAgent** calls `llm_client.invoke()` without a `model_id` argument, so it inherits the default `config.MODEL_ID` — Claude Haiku 4.5. It receives already-computed scores and is instructed not to recompute them, but must reason across candidates and generate a grounded explanation, which requires more language capacity than structured slot-filling.

The split is enforced by configuration, not by conditional logic inside agents: swapping a model is an environment-variable change, and the agents do not need to know which model they are actually calling.

## Consequences

**Good**

- Theme extraction runs at $0.035/$0.14 per MTok instead of $1.00/$5.00 — a ~29× reduction in cost for the highest-frequency LLM call in the pipeline. Because ReviewIntelligenceAgent runs once per ASIN and is the only agent that processes review text, this is the dominant cost driver at scale.
- Reasoning quality is preserved where it matters. RecommendationAgent selects a winner and writes a justification; that output is the primary consumer-facing artefact from the pipeline. Haiku's capability headroom is applied there rather than on structured JSON extraction.
- Both model IDs are overridable via environment variables, so the assignment can be adjusted — or unified to a single model — without touching application code. This makes cost/quality experiments trivial.
- Pricing is tracked in `finops/model_pricing.json` alongside both model IDs. Cost estimates in `scripts/token_report.py` can reference either model directly, making the cost difference auditable.

**Bad**

- Two model IDs increase operational surface area. Deployments must configure and validate both `MODEL_ID` and `MODEL_ID_TOOLS`; a misconfigured `MODEL_ID_TOOLS` silently falls back to Haiku (correct behaviour, but the cost saving disappears without an obvious signal).
- Nova Micro's output quality for the theme-extraction task has not been formally benchmarked against Haiku. The decision rests on the structural argument that structured slot-filling does not require high reasoning capacity, not on a held-out evaluation set. If Nova Micro produces materially worse themes in production, the fallback path (set `MODEL_ID_TOOLS` to the Haiku ID) is straightforward but must be detected first.
- The dual-model setup adds a conceptual burden for new contributors, who must understand why two config values exist and which agent uses which. The code comment in `review.py` and this ADR together document the intent, but that is one more thing to keep accurate.

**Neutral**

- Both agents retain `use_mock=True` and exception-triggered fallback to mock output, independent of model selection. The cost strategy does not affect testability or resilience characteristics established in ADR 001.
- The `MODEL_ID_TOOLS` fallback (fall back to `MODEL_ID` when unset or equal) means a single-model deployment is always valid. Teams that do not require cost optimisation can simply leave `MODEL_ID_TOOLS` unset and the pipeline behaves as if only one model exists.
