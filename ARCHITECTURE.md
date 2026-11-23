# Julie — Technical Architecture

## Overview

Julie is a four-agent Amazon marketplace product intelligence system. Given a product category and a country code, it finds candidate products on the live Amazon retail site, analyses their recent reviews, inspects their pricing and rating trends, and returns a ranked recommendation — all streamed in real time to a browser via Server-Sent Events.

**Design principle:** LLM calls cost money and add latency. Only two of the four agents call an LLM, and both constrain it to reasoning over already-processed, already-reduced evidence — never raw scraped data. Discovery and Price & Rating are pure Python: arithmetic over structured data that an LLM adds nothing to. A FinOps layer sits in front of every Bedrock call: a process-wide budget guard blocks new calls once the configured daily ceiling is reached, and a daily result cache serves repeat searches at zero cost.

---

## System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                         Browser / curl                                 │
└───────────────────────────────┬────────────────────────────────────────┘
                                │  POST /analyze  (SSE stream)
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│               Flask  app.py  (port 5050, threaded)                     │
│                                                                        │
│  GET /         → static/index.html                                     │
│  GET /budget   → today's spend vs ceiling                              │
│  GET /countries→ marketplace + currency map                            │
│  POST /analyze → SSE generator (one generator per request)            │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                  Daily Cache  (finops/daily_cache.py)            │  │
│  │  key = "{product}|w{window_days}" + country                     │  │
│  │  Hit  → replay cached SSE events at zero cost, return           │  │
│  │  Miss → run the 4-agent pipeline, cache result on completion    │  │
│  └───────────────────────────┬──────────────────────────────────────┘  │
└───────────────────────────────┼────────────────────────────────────────┘
                                │
        ┌───────────────────────┼─────────────────────────┐
        │                       │                         │
        ▼                       ▼                         ▼
┌──────────────┐    ┌───────────────────────┐   ┌─────────────────────┐
│   STAGE 1    │    │    STAGES 2 + 3        │   │     STAGE 4         │
│  Discovery   │───▶│  (per ASIN, in order)  │──▶│  Recommendation     │
│  Agent       │    │  Review Intelligence   │   │  Agent              │
│  (no LLM)    │    │  + Price & Rating      │   │  (Claude Haiku 4.5) │
└──────┬───────┘    └───────────┬────────────┘   └─────────────────────┘
       │                        │
       │                        ▼
       │            ┌───────────────────────┐
       │            │  finops/budget.py      │
       │            │  _DailyBudget guard    │
       │            │  → check() before call │
       │            │  → record() after      │
       │            └───────────┬────────────┘
       │                        │
       │                        ▼
       │            ┌───────────────────────┐
       │            │  Amazon Bedrock        │
       │            │  Review: Nova Micro    │
       │            │  Recommend: Haiku 4.5  │
       │            └───────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    AmazonProvider  (providers/amazon.py)               │
│                    Selenium + headless Chrome                          │
│                                                                        │
│  search_products()       → scrapes search results page                 │
│  prefetch_reviews()      → opens N product pages in parallel tabs,     │
│                             caches page sources in-process             │
│  get_reviews()           → reads from in-process cache if warm        │
│  get_rating_histogram()  → reads star distribution from product page  │
│  get_price_history()     → returns current price only (live mode)     │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                       Observability                                    │
│                                                                        │
│  observability/logging.py  → structured JSON events to stdout          │
│                               (log group /julie/app in AWS)           │
│  observability/metrics.py  → CloudWatch PutMetricData                  │
│                               namespace "Julie"                        │
│  CloudWatch alarms (template.yaml):                                    │
│    AgentErrors > 5/min · InputTokens > 50k · EstimatedLLMCost (daily) │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│               AgentCore Runtime  (agentcore/)                          │
│               Optional: packages the same 4-agent pipeline            │
│               as a BedrockAgentCoreApp (HTTP /invocations endpoint)    │
│               and exposes Gateway MCP tools for external agents        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Flask Server (`app.py`)

Single-file entry point. Four routes.

| Route | Method | Purpose |
|:--|:--|:--|
| `/` | GET | Serves `static/index.html` |
| `/budget` | GET | Returns `{spent_today_usd, daily_limit_usd, utilization_percent, calls_today}` |
| `/countries` | GET | Returns all 25 supported marketplace codes, domain names and default currencies |
| `/analyze` | POST | SSE streaming endpoint — runs the 4-agent pipeline per request |

The `/analyze` generator function emits typed SSE frames: `progress`, `dialogue`, `candidates`, `product_result`, `recommendation`, `error`, and `done`. The `dialogue` frames are agent-to-agent commentary shown in the UI's "Agent Activity" feed. A `try/finally` block in the generator ensures `provider.close()` is always called to release the Chrome instance, even on mid-stream errors.

Filter parameters (`min_rating`, `max_price`) are applied client-side on the candidate list before the per-ASIN stages run. `review_window_days` is validated to `[7, 366]`.

### 2. Provider Layer (`providers/`)

`ProductDataProvider` is an abstract base class with four methods: `search_products`, `get_product`, `get_reviews`, `get_price_history`. Agents never import Selenium or any concrete data technology — they always receive a provider instance.

**`AmazonProvider`** (607 lines) is the live implementation:

- Lazily builds a headless Chrome `webdriver.Chrome()` instance on first use via `_get_driver()`. A global `atexit` and signal handler (`_cleanup_all_drivers`) ensures the browser is killed on exit.
- `search_products()` navigates to `https://www.{domain}/s?k={product_type}`, parses search tiles, and skips sponsored ads that carry no genuine rating or review data.
- `prefetch_reviews()` opens all requested ASINs as parallel browser tabs in sequence (one tab per ASIN), letting the browser fetch them concurrently rather than waiting for each page in turn. Results are held in an in-process dict keyed by ASIN.
- `get_reviews()` returns the prefetched page's parsed reviews if available, otherwise navigates directly to the product page.
- `get_rating_histogram()` reads Amazon's star-distribution widget from the product page — used by Discovery's guarded-fallback tier as a wider negative-sentiment signal than the visible reviews alone.
- `get_price_history()` returns a single-entry list containing the current scraped price. There is no historical price series in live mode.

A `DemoProvider` is also referenced (`agentcore/runtime.py`) for the AgentCore runtime's non-live path.

### 3. Discovery Agent (`agents/discovery.py`)

**No LLM.** Finds candidate products using a three-tier ranked selection strategy.

The agent requests `DISCOVERY_POOL_SIZE` (default 8) products from the provider, then enriches each one with recent-review statistics. Statistics are computed in pure Python (`analysis/reviews.py`): filter by `window_days`, deduplicate, count, bucket by star rating.

**Tier A — Trending:** Products with Amazon's "bought in past month" badge ≥ `TRENDING_MIN_MONTHLY_PURCHASES` (100) and rating ≥ 4.0, *or* products with ≥ 3 recent reviews averaging ≥ 4.0. Ranked by monthly purchase volume, then recent review count.

**Tier B — Best Sellers:** Overall rating ≥ 4.0, ranked by `rating × log10(review_count + 1)` — a high-rating product with 10,000 reviews scores higher than a 5-star product with 3.

**Tier C — Guarded Fallback:** Anything not already selected, *excluding* products whose recent negative-review share or lifetime histogram negative share (1-star + 2-star %) exceeds `DISCOVERY_MAX_NEGATIVE_PERCENT` (30%).

Candidates carry a `discovery_tier` marker so the UI and logs show why each product was selected.

### 4. Review Intelligence Agent (`agents/review.py`)

**Uses Amazon Nova Micro** (`MODEL_ID_TOOLS`, default `amazon.nova-micro-v1:0`).

Python does: fetch all reviews from the provider, filter to `window_days` (default 90), deduplicate, calculate statistics (review count, positive/negative percentages, average rating), and sample a representative set (`analysis/reviews.py::sample_representative`). The LLM receives only this small sample — never the full unfiltered review list.

The prompt asks for: `positive_themes`, `negative_themes`, `recurring_complaints`, `recurring_strengths`, `overall_sentiment`, `summary`. Max tokens: 300. Nova Micro is used here specifically because it is ~97% cheaper than Haiku for this pattern-extraction task.

`token_optimizer.py` computes and logs the raw-vs-optimised token count ratio so the reduction is visible in CloudWatch.

On any Bedrock error the agent falls back to `mock_llm.mock_review_summary()`, which derives themes from review text heuristically without making an LLM call.

### 5. Price & Rating Agent (`agents/pricing.py`)

**No LLM.** Pure arithmetic.

- `analyze_price_history()` — computes `price_change_percent` between earliest and latest price observations in the history list. In live mode, the history contains one entry (today's price), so `trend_available` is `False`.
- `rating_change()` — groups recent reviews by ISO month, computes per-month average ratings, and returns `current_rating`, `previous_rating`, `rating_change`, and a `monthly_trend` list.

The agent intentionally does no LLM reasoning on numbers: arithmetic belongs to Python.

### 6. Recommendation Agent (`agents/recommendation.py`)

**Uses Claude Haiku 4.5** (`MODEL_ID`, default `us.anthropic.claude-haiku-4-5-20251001-v1:0`).

Split into two parts. First, **`compute_score()`** — a module-level pure function, not a method — computes a deterministic weighted score:

```
overall_score = review_score  × 0.35   (positive_percentage, normalised 0–100)
              + rating_score  × 0.25   (product rating, normalised 1–5)
              + price_score   × 0.20   (price_change_percent, lower is better)
              + trend_score   × 0.20   (rating_change, normalised -1 to +1)
```

Weights are config-driven (`SCORE_WEIGHTS`). Missing data maps to a neutral 0.5 via `_normalize()`.

Second, **`_explain()`** passes the already-scored candidate list to Claude Haiku. The prompt instructs the model to select the strongest candidate and return `{recommendation, asin, reason, strengths, weaknesses, confidence}`. The model is explicitly told not to recompute the score — only interpret it. Max tokens: 300.

On any Bedrock error, falls back to `mock_llm.mock_recommendation()`, which returns the highest-scored candidate deterministically.

### 7. FinOps Layer (`finops/`)

Three cost-control mechanisms that work at different layers:

**`budget.py` — Daily budget guard.** `_DailyBudget` is a process-wide singleton. `check()` raises `BudgetExceededError` before every Bedrock call if today's estimated spend has reached `DAILY_COST_BUDGET_USD` (default $5.00). `record()` accumulates cost after each successful invocation. Auto-resets at calendar-day rollover. This is an application-side estimate; the CloudWatch `EstimatedLLMCost` alarm is the authoritative cloud-side guard.

**`daily_cache.py` — Daily result cache.** Stores full pipeline results (`candidates`, `evaluated`, `recommendation`) in a single JSON file per calendar day under `.cache/results_{YYYY-MM-DD}.json`. The cache key is `{product_type}__{country}` (normalised). A cache hit bypasses all four agents, the provider, and every Bedrock call — cost is zero. Stale files from previous days are purged automatically on first access. Cache is only saved when no filters (`min_rating`, `max_price`) were applied so cached results represent the unfiltered run.

**`token_optimizer.py` — Token measurement.** `measure_reduction()` estimates the raw token count if all reviews had been sent vs the actual optimised sample count. The ratio is logged and published as a CloudWatch metric (`TokenReductionPercent`). This is measurement, not enforcement — the reduction comes from `sample_representative()` in the review analysis layer.

**`model_pricing.json` — Pricing table.** Per-million-token input/output costs for Haiku 4.5 ($1.00/$5.00), Sonnet ($3.00/$15.00), and Nova Micro ($0.035/$0.14). `TokenUsage.cost_breakdown()` uses this to compute `estimated_total_cost` after each invocation.

**`report.py` — Cost report builder.** `CostReportBuilder` accumulates per-agent `TokenUsage` objects and can write a `last_run_report.json` summary. Used by the standalone `scripts/` runner, not the Flask server.

### 8. Observability (`observability/`)

**`logging.py`** — `log_event(event, **fields)` and `log_error(event, error, **fields)` emit JSON lines to stdout. In AWS, these flow to CloudWatch Logs under `/julie/app`.

**`metrics.py`** — `MetricsClient.publish(metric_name, value, unit, dimensions)` calls `cloudwatch.put_metric_data()` in the `Julie` namespace. boto3 is imported lazily so the client can be imported without AWS credentials. Publishing failures are swallowed (metrics are best-effort).

Key metrics published per pipeline run:

| Metric | Agent | Notes |
|:--|:--|:--|
| `ReviewsFetched` | review_intelligence | Raw count before filtering |
| `ReviewsAnalyzed` | review_intelligence | After filter + dedupe |
| `RawEstimatedTokens` | review_intelligence | Before sampling |
| `OptimizedEstimatedTokens` | review_intelligence | After sampling |
| `TokenReductionPercent` | review_intelligence | Ratio |
| `InputTokens` / `OutputTokens` | both LLM agents | Actual Bedrock usage |
| `EstimatedLLMCost` | both LLM agents | Accumulated daily |
| `AgentInvocations` | both LLM agents | Per successful call |
| `AgentErrors` | both LLM agents | Per retry-exhausted failure |
| `ToolInvocations` | discovery, price_rating | Per run |

CloudFormation (`infrastructure/template.yaml`) provisions a log group, CloudWatch alarms on `AgentErrors` > 5, `InputTokens` > 50,000, and `EstimatedLLMCost` > budget ceiling, and optionally an SNS notification topic.

### 9. AgentCore Runtime (`agentcore/`)

**`runtime.py`** wraps the identical 4-agent pipeline in a `BedrockAgentCoreApp` instance. The `@app.entrypoint` handler runs discovery → review → pricing → recommendation and returns a structured JSON response. This is the deployment path for packaging Julie as an AgentCore Runtime container.

**`gateway_tools.py`** defines five MCP tool schemas (`search_products`, `get_product`, `get_reviews`, `get_price_history`, `calculate_review_statistics`) that the AgentCore Gateway exposes. The schemas reference Lambda handler paths for the data-fetch tools; `calculate_review_statistics` is implemented in-process.

---

## Data Flow

```
1.  Browser POST /analyze {product: "wireless headphones", country: "US", review_window_days: 90}

2.  Flask checks daily_cache.get_cached_result("wireless headphones|w90", "US")
    → cache miss (first run today): continue
    → cache hit: replay SSE frames from cache, return — no agents, no Bedrock

3.  AmazonProvider instantiated with headless=True

4.  Stage 1 — Discovery
    discovery.find_candidates("wireless headphones", "US", window_days=90)
    provider.search_products() → scrapes amazon.com, returns up to 8 products
    provider.prefetch_reviews([asin1..8], "US") → opens 8 tabs in parallel
    For each product: provider.get_reviews() → hits in-process cache (warm)
                      review_analysis.filter_recent() + deduplicate() + calculate_statistics()
    Tier A ranking → Tier B ranking → Tier C fallback (negative exclusion)
    Returns ≤ MAX_PRODUCTS (5) candidates with discovery_tier markers
    SSE: "candidates" frame with product list

5.  Stage 2+3 — per ASIN (sequential)
    For each candidate ASIN:

    Review Intelligence:
      provider.get_reviews(asin) → in-process cache (already warm from prefetch)
      filter_recent() → deduplicate() → calculate_statistics() → sample_representative()
      token_optimizer.measure_reduction() → log token reduction metrics
      daily_budget.check() → OK
      bedrock.invoke_model(nova-micro, 300 max_tokens) → themes + summary + sentiment
      daily_budget.record(estimated_cost)
      metrics: ReviewsFetched, ReviewsAnalyzed, InputTokens, OutputTokens, EstimatedLLMCost
      SSE: "progress" (review done for this ASIN)

    Price & Rating:
      provider.get_price_history(asin) → current price only (live mode, no history)
      analyze_price_history() → price_change_percent = None (trend_available=False)
      rating_change(recent_reviews) → monthly trend from review dates
      SSE: "progress" (pricing done for this ASIN)

    compute_score(product, review_result, pricing_result) → {overall_score, ...}
    SSE: "product_result" frame with product + reviews + pricing + scores

6.  Stage 4 — Recommendation
    All evaluated candidates passed to recommender.recommend()
    compute_score already done; recommender passes condensed {asin, scores, percentages} to LLM
    daily_budget.check() → OK
    bedrock.invoke_model(claude-haiku-4-5, 300 max_tokens) → {asin, recommendation, reason, confidence}
    daily_budget.record(estimated_cost)
    SSE: "recommendation" frame

7.  daily_cache.save_result() → persists result to .cache/results_{today}.json
    SSE: "done" frame {analyzed: N}

8.  provider.close() in finally block → Chrome driver quit
```

---

## LLM Usage Design

The question "does this step need an LLM?" is answered differently for each agent.

**Discovery** is a ranking problem over structured signals: review counts, ratings, dates, purchase badges. All inputs are numbers or categories; the decision rule is an explicit threshold function. An LLM cannot outperform arithmetic here and would add latency and cost on the hot path where N browser fetches are already waiting.

**Review Intelligence** genuinely needs language understanding. The inputs are review texts; the outputs are thematic clusters and a sentiment label. Python could count keywords but cannot reliably distinguish "battery drains fast" from "battery life is great" without reading. The design still limits what the LLM sees: `sample_representative()` selects a fixed cap per star band (10 × 5-star, 5 × 4-star, 3 × 3-star, 5 × 2-star, 10 × 1-star) so the prompt is bounded regardless of how many reviews exist. Nova Micro is used for this task because theme extraction is a simple classification task; Haiku is not needed.

**Price & Rating** is entirely arithmetic: percentage changes and monthly averages. There is nothing for an LLM to reason about; the outputs feed a weighted formula downstream.

**Recommendation** needs an LLM for explanation, not for scoring. `compute_score()` computes a deterministic weighted sum in pure Python first. The LLM receives the already-scored candidate list and is explicitly instructed not to recompute the score — only to interpret it: which candidate is strongest, why, what are the weaknesses, and how confident is the verdict. This ordering — Python scores, LLM explains — is deliberate. A score that only an LLM can explain is not auditable.

The result: two Bedrock calls per analyzed product (Nova Micro for review, Haiku once for the final recommendation), regardless of pool size.

---

## FinOps Design

### Budget guard: process-wide, pre-call

`_DailyBudget.check()` is called inside `llm.invoke()` before every Bedrock request. `BudgetExceededError` propagates up to the agent, which falls back to its mock implementation. The guard fires even if the ceiling was crossed by a parallel request since the last `check()` — the singleton's `_total_cost_usd` is updated in-process after every successful call.

The guard auto-resets at midnight. "Yesterday I spent $4.80" does not affect today's run.

This is **application-side** enforcement only — it tracks estimated token costs, not AWS invoice values. The CloudWatch `EstimatedLLMCost` alarm in `infrastructure/template.yaml` is the authoritative out-of-band guard.

### Daily cache: same-day repeats cost $0

`daily_cache.get_cached_result()` is called in the SSE generator *before* any agents are instantiated or any browser is opened. A cache hit skips the entire pipeline: no Selenium, no Bedrock, no cost.

The cache file is one JSON dict per calendar day (`results_YYYY-MM-DD.json`). All entries for the day coexist in one file; there is no TTL shorter than a calendar day. Old files are purged on first access each day.

Cache is only stored when no filter parameters were applied (`min_rating is None and max_price is None`). Filtered results are not cached because a subsequent unfiltered search for the same product would incorrectly receive the filtered candidate list.

### Token reduction through sampling

`sample_representative()` enforces per-star-band caps before the review prompt is built. The `token_optimizer.measure_reduction()` call after sampling measures how many tokens would have been sent if all reviews had been included, and how many are actually sent. This ratio is logged and published to CloudWatch as `TokenReductionPercent`. It is diagnostic, not enforced — the reduction is a consequence of sampling, not a hard token budget.

### Model selection

Nova Micro ($0.035/$0.14 per million tokens) handles theme extraction in the Review Intelligence Agent — a task that requires pattern recognition but not complex reasoning. Claude Haiku 4.5 ($1.00/$5.00 per million tokens) handles the final recommendation, which requires comparative reasoning over multiple candidates. Anthropic Claude Sonnet is included in `model_pricing.json` but is not used by any agent in the default configuration.

---

## Configuration

All settings are environment variables (optionally via `.env`). No typed settings class is used — `config.py` reads them with plain `os.environ.get()` with defaults.

| Variable | Default | Effect |
|:--|:--|:--|
| `MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model for Recommendation |
| `MODEL_ID_TOOLS` | `amazon.nova-micro-v1:0` | Bedrock model for Review Intelligence |
| `AWS_REGION` | `us-east-1` | Bedrock + CloudWatch region |
| `AWS_PROFILE` | _(unset)_ | boto3 credential profile |
| `DAILY_COST_BUDGET_USD` | `5.0` | Daily LLM spend ceiling before `BudgetExceededError` |
| `REVIEW_WINDOW_DAYS` | `90` | Days of reviews to consider (overridable per-request) |
| `MAX_REVIEWS_PER_ASIN` | `33` | Max reviews fetched from the provider per ASIN |
| `MAX_PRODUCTS` | `5` | Max candidates returned by Discovery |
| `DISCOVERY_POOL_SIZE` | `8` | How many products to pull before tier ranking |
| `TRENDING_MIN_RECENT_REVIEWS` | `3` | Minimum recent reviews for Tier A |
| `TRENDING_MIN_AVG_RATING` | `4.0` | Minimum recent average rating for Tier A |
| `TRENDING_MIN_MONTHLY_PURCHASES` | `100` | Monthly badge threshold for Tier A |
| `BESTSELLER_MIN_RATING` | `4.0` | Minimum overall rating for Tier B |
| `DISCOVERY_MAX_NEGATIVE_PERCENT` | `30.0` | Negative share threshold for Tier C exclusion |
| `SCORE_WEIGHT_REVIEW` | `0.35` | Weight of review positivity in `compute_score` |
| `SCORE_WEIGHT_RATING` | `0.25` | Weight of overall star rating |
| `SCORE_WEIGHT_PRICE` | `0.20` | Weight of price change (lower change is better) |
| `SCORE_WEIGHT_TREND` | `0.20` | Weight of recent rating change direction |
| `METRICS_NAMESPACE` | `Julie` | CloudWatch metric namespace |
| `LOG_GROUP_NAME` | `/julie/app` | CloudWatch log group (for deployed mode) |
| `SNS_TOPIC_ARN` | _(empty)_ | Optional SNS topic for CloudWatch alarm notifications |
| `AGENT_ERROR_THRESHOLD` | `5` | AgentErrors count that triggers the CloudWatch alarm |
| `INPUT_TOKEN_ALARM_THRESHOLD` | `50000` | Input token count that triggers the alarm |
| `PORT` | `5050` | Flask server port |

CloudFormation parameters in `infrastructure/parameters.json` mirror a subset of these for the AWS deployment path.

---

## Running

```bash
# Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install flask boto3 python-dotenv pandas selenium pytest

# AWS credentials (needed for Bedrock calls)
export AWS_PROFILE=bedrock-deployer      # or configure ~/.aws/credentials

# Required: Chrome + chromedriver must be on PATH
# macOS:  brew install --cask chromedriver
# Ubuntu: see SETUP.md

# Start the Flask server
python app.py
# → http://localhost:5050

# Query via curl
curl -s -N -X POST http://localhost:5050/analyze \
  -H "Content-Type: application/json" \
  -d '{"product": "wireless headphones", "country": "US"}' \
  | grep '^data:' | head -30

# Check today's spend
curl http://localhost:5050/budget

# Run without Bedrock (mock LLM path - agents fall back automatically when budget is $0)
DAILY_COST_BUDGET_USD=0 python app.py

# AgentCore runtime (deploys the same pipeline as a BedrockAgentCoreApp)
pip install bedrock-agentcore
python agentcore/runtime.py
# → POST http://localhost:8080/invocations
```

**AWS CloudFormation deployment:**
```bash
aws cloudformation deploy \
  --template-file infrastructure/template.yaml \
  --stack-name julie \
  --parameter-overrides file://infrastructure/parameters.json \
  --capabilities CAPABILITY_IAM
```

---

## Testing

```bash
pytest                           # all tests
pytest tests/test_discovery.py  # tier ranking, negative exclusion, candidate assembly
pytest tests/test_reviews.py    # filter, dedupe, statistics, sampling
pytest tests/test_finops.py     # budget guard, day rollover, cache hit/miss, purge
pytest tests/test_prices.py     # price history analysis, price change percent
pytest tests/test_products.py   # provider contract compliance
pytest tests/test_token_optimization.py  # token count estimation and reduction ratio
pytest tests/test_end_to_end.py # full pipeline under mock provider, no Bedrock
```

All tests use `tests/mock_provider.py`, which implements `ProductDataProvider` with in-memory fixture data. `ReviewIntelligenceAgent(use_mock=True)` and `RecommendationAgent(use_mock=True)` route through `mock_llm` instead of `llm.invoke()`, so no AWS credentials are needed for any test.

The mock LLM (`tools/mock_llm.py`) is not a stub that returns fixed strings: `mock_review_summary()` derives themes from the review text heuristically (keyword frequency), and `mock_recommendation()` returns the highest-scored candidate by `overall_score`. This means tests validate agent logic — tier ranking, score computation, budget guard firing — without testing Bedrock.

Not covered by automated tests: `AmazonProvider` against the live Amazon retail sites (verified manually), and the AgentCore runtime's HTTP `/invocations` endpoint.
