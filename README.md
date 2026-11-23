# Shelfwise

For the existing TestFloci + Ollama local stack, see [Floci setup](docs/FLOCI.md).

A four-agent pipeline that discovers products on Amazon, extracts review intelligence, tracks price and rating trends, and produces a ranked recommendation — while spending as little as $0.005 per query and proving the cost controls actually work.

---

## The Problem

You want to know which wireless headphones are worth buying on Amazon right now. The obvious approach is to search Amazon, read a hundred reviews, compare prices across a few products, and form an opinion. That takes forty-five minutes, and you still have not normalised prices across marketplaces or extracted themes from five hundred reviews systematically.

The less obvious problem is the cost structure of doing this with an LLM. Hand every review to Claude and the bill climbs fast. The temptation is to let the model do everything — discovery, filtering, scoring, summarisation — because it is easy to wire up. The result is a system that is expensive, slow, and hard to test because the non-deterministic parts are everywhere.

The real design question is: which of these four stages actually require a language model, and which are just structured data processing that a language model is bad at anyway?

Discovery is filtering and ranking — pattern matching on scrape output, no LLM needed. Price and rating analysis is arithmetic — currency normalisation and weighted scores, no LLM needed. Review theme extraction is fast keyword grouping — Nova Micro at $0.035/1M tokens, not Claude. Recommendation explanation is the one place where generating prose from a pre-computed score earns its keep.

So the system has four agents, two of which never call an LLM.

---

## How It Works

A user submits a product query and a marketplace. The pipeline runs four stages in sequence, each agent handing enriched data to the next:

**Discovery** scrapes Amazon search results via a headless Chrome browser, runs parallel tab fetches to get to five products in about fifty seconds rather than three minutes, and filters by three tiers: trending (monthly-purchase badge + 4★), best-seller rank, and a fallback that blocks products with ≥30% negative reviews. No LLM.

**Review Intelligence** fetches product detail pages, pulls the rating histogram, filters reviews to the configured time window (default 90 days), and then — only here — calls two Bedrock models. Claude Haiku 4.5 writes a sentiment summary and pros/cons. Nova Micro extracts structured themes (topics, keywords) at 29× lower cost.

**Price & Rating** normalises prices to USD using the currency symbol detected from the scraped price string, computes a price-competitiveness score and a raw star-rating score. No LLM.

**Recommendation** combines the three upstream scores with a trend signal using fixed weights (review 0.35 · rating 0.25 · price 0.20 · trend 0.20), sorts products, and streams the ranked output back to the browser via Server-Sent Events. One LLM call explains the composite score in prose.

The Flask web server wraps the pipeline with a daily budget guard, a per-query result cache valid for the UTC day, and a CloudWatch metrics emitter. Every stage logs structured events.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Flask Web Server  (app.py :5050)              │
│                                                                 │
│   POST /analyze        GET /stream (SSE)        budget guard   │
│   daily cache check    structured logging       CloudWatch      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              AGENT 1 — DISCOVERY  (agents/discovery.py)         │
│                                                                 │
│   Selenium Chrome headless, tab pool (default: 8 tabs)          │
│   Sponsored tile filter  →  three-tier product ranking          │
│   monthly-purchase badge extraction                             │
│                                                                 │
│   Output: top-N product stubs (ASIN, title, rating, price)      │
│   LLM calls: NONE                                               │
└──────────────────────────────┬──────────────────────────────────┘
                               │  product list
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│         AGENT 2 — REVIEW INTELLIGENCE  (agents/review.py)       │
│                                                                 │
│   Fetch product page  →  extract rating histogram               │
│   Filter reviews to window (7–366 days, default 90)             │
│   Token optimiser trims review text to budget                   │
│                                                                 │
│   Claude Haiku 4.5  →  sentiment summary, pros/cons             │
│   Nova Micro        →  theme extraction (29× cheaper)           │
│                                                                 │
│   Output: enriched products + review score (weight 0.35)        │
│   LLM calls: 2 per product                                      │
└──────────────────────────────┬──────────────────────────────────┘
                               │  enriched products
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│           AGENT 3 — PRICE & RATING  (agents/pricing.py)         │
│                                                                 │
│   Currency auto-detected from scraped price string              │
│   Normalise to USD  →  price competitiveness score (0.20)       │
│   Star rating score (0.25)                                      │
│                                                                 │
│   Output: fully enriched products                               │
│   LLM calls: NONE                                               │
└──────────────────────────────┬──────────────────────────────────┘
                               │  fully enriched products
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│        AGENT 4 — RECOMMENDATION  (agents/recommendation.py)     │
│                                                                 │
│   Weighted composite: 0.35 review · 0.25 rating ·              │
│                        0.20 price · 0.20 trend                  │
│   Sort products  →  Claude Haiku 4.5 explains the winner        │
│                                                                 │
│   Output: ranked list + explanation  →  SSE → browser           │
│   LLM calls: 1 total                                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
               ┌───────────────┴─────────────────┐
               ▼                                 ▼
┌────────────────────────┐         ┌─────────────────────────────┐
│  FINOPS LAYER          │         │  OBSERVABILITY               │
│  finops/budget.py      │         │  observability/logging.py    │
│  finops/daily_cache.py │         │  observability/metrics.py    │
│  finops/pricing.py     │         │  CloudWatch Logs/Metrics     │
│  finops/report.py      │         │  3 alarms → SNS              │
│  finops/usage.py       │         │  EventBridge daily cache     │
└────────────────────────┘         │  flush (00:05 UTC)           │
                                   └─────────────────────────────┘
```

---

## Project Structure

```
shelfwise/
├── app.py                          Flask entry point, SSE /stream, cache + budget guard
├── setup.sh                        venv + deps + smoke test
├── requirements.txt
├── pyproject.toml
│
├── static/
│   └── index.html                  single-page UI, agent dialogue feed, marketplace selector
│
├── src/product_intelligence/
│   ├── config.py                   25 marketplace definitions, score weights, review window bounds
│   ├── llm.py                      shared Bedrock invoke_model wrapper (Haiku + Nova Micro)
│   │
│   ├── agents/
│   │   ├── discovery.py            Agent 1 — Chrome tab pool, tier filtering, badge extraction
│   │   ├── review.py               Agent 2 — histogram, review window, Bedrock calls
│   │   ├── pricing.py              Agent 3 — currency normalisation, price/rating scores
│   │   └── recommendation.py       Agent 4 — weighted scoring, ranking, SSE output
│   │
│   ├── providers/
│   │   ├── base.py                 ProductDataProvider interface
│   │   └── amazon.py               live Selenium scraper (Chrome headless, ToS caveats apply)
│   │
│   ├── tools/
│   │   ├── mock_llm.py             deterministic keyword-based LLM stand-in for tests
│   │   ├── token_optimizer.py      review truncation / batching to stay within token budgets
│   │   ├── product_search.py
│   │   ├── review_fetcher.py
│   │   └── price_fetcher.py
│   │
│   ├── analysis/
│   │   ├── reviews.py              deterministic review stats, histogram parsing, no-data rules
│   │   ├── ratings.py              star-rating score computation
│   │   └── prices.py               price normalisation math
│   │
│   ├── finops/
│   │   ├── budget.py               daily spend cap, UTC-day tracking, LLM call gate
│   │   ├── daily_cache.py          query+marketplace+window key, UTC-day TTL
│   │   ├── pricing.py              per-model token cost estimates
│   │   ├── usage.py                usage accumulator
│   │   ├── report.py               cost breakdown report
│   │   └── model_pricing.json      rate table (Haiku 4.5, Nova Micro)
│   │
│   └── observability/
│       ├── logging.py              structured log events
│       └── metrics.py              CloudWatch custom metrics emission
│
├── agentcore/
│   ├── runtime.py                  AgentCore Runtime entrypoint
│   └── gateway_tools.py            MCP tool schemas for AgentCore Gateway
│
├── lambda/
│   ├── product_search/handler.py
│   ├── review_fetch/handler.py
│   ├── price_fetch/handler.py
│   └── daily_cost_report/handler.py
│
├── infrastructure/
│   └── template.yaml               CloudFormation — 22 resources (Lambda ×4, alarms, SNS,
│                                   EventBridge, AgentCore Runtime + Gateway)
│
├── scripts/
│   ├── billing_report.py           per-day Bedrock cost breakdown
│   ├── cleanup.py                  empty S3 cache bucket + delete CloudFormation stack
│   ├── invoke_agent.py             CLI invocation of deployed AgentCore Runtime
│   ├── token_report.py             raw vs optimised token comparison
│   └── trigger_test_alarm.py       publishes a metric above threshold to prove alarm fires
│
├── tests/
│   ├── conftest.py
│   ├── mock_provider.py            deterministic stand-in for Selenium scraper
│   ├── test_discovery.py
│   ├── test_end_to_end.py
│   ├── test_finops.py
│   ├── test_prices.py
│   ├── test_products.py
│   ├── test_reviews.py
│   └── test_token_optimization.py
│
└── docs/adr/
    ├── 001-deterministic-vs-llm-separation.md
    └── 002-dual-model-strategy-for-cost.md
```

---

## Running

```bash
bash setup.sh
source .venv/bin/activate

# Run all tests (mock provider, no AWS required)
pytest

# Start the Flask UI on http://localhost:5050
python app.py
```

With AWS credentials and Bedrock model access configured:

```bash
export AWS_PROFILE=bedrock-deployer
export AWS_REGION=us-east-1
export MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
export MODEL_ID_TOOLS=amazon.nova-micro-v1:0

python app.py
```

Open **http://localhost:5050**, enter a product query (e.g., `wireless earbuds`), select a marketplace, and click **Analyze**. Results stream back as SSE in ~50 seconds for a cold-cache query.

To prove the CloudWatch alarm pipeline works:

```bash
python scripts/trigger_test_alarm.py --metric AgentErrors --value 10
```

Then check CloudWatch → Alarms → `AgentErrorsAlarm` transitions to ALARM and SNS delivers the notification.

---

## Testing

```bash
pytest                              # all 41 tests
pytest tests/ -v                    # verbose, shows each test name
pytest tests/test_finops.py -v      # budget guard and cache logic only
pytest tests/test_reviews.py -v     # review window filtering, histogram extraction
pytest tests/ --cov=src --cov-report=term-missing
```

Every test runs against `mock_provider.py` — a deterministic, keyword-based stand-in for the Selenium scraper and the two Bedrock models. The suite needs no Chrome, no ChromeDriver, no AWS credentials, and no network access. The test for "budget exhausted returns cached result" actually exhausts a budget object; the test for "review window filters old reviews" constructs timestamped review fixtures. Nothing is mocked at the assertion boundary — the business logic runs for real, only the external I/O is replaced.

---

## Configuration

All settings are environment variables. Create a `.env` file in the project root (never commit it):

```bash
# Bedrock models — both required for live LLM calls
MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
MODEL_ID_TOOLS=amazon.nova-micro-v1:0

# AWS — required for live LLM calls and CloudFormation deployment
AWS_PROFILE=bedrock-deployer
AWS_REGION=us-east-1

# Pipeline tuning
REVIEW_WINDOW_DAYS=90          # review lookback window (7–366)
DISCOVERY_POOL_SIZE=8          # parallel Chrome tab pool size

# FinOps
DAILY_COST_BUDGET_USD=5.0      # max Bedrock spend per UTC day

# Score weights (must sum to 1.0)
SCORE_WEIGHT_REVIEW=0.35
SCORE_WEIGHT_RATING=0.25
SCORE_WEIGHT_PRICE=0.20
SCORE_WEIGHT_TREND=0.20
```

Load and start:

```bash
set -a && source .env && set +a
python app.py
```

The `us.` prefix on `MODEL_ID` is mandatory — it is the cross-region inference prefix and Bedrock will return `ValidationException` without it.

---

## Tradeoffs and Decisions I Made Along the Way

**The deterministic/LLM split was the most important design decision.** Two agents call an LLM; two do not. I drew the line by asking: does this step require generating language, or does it require computing a number or matching a pattern? Discovery is a filter. Price/rating is arithmetic. Theme extraction is structured keyword grouping — Nova Micro at $0.035/1M tokens is the right tool, not Claude. The only genuine prose tasks are the review summary and the recommendation explanation. Keeping that boundary explicit means the non-LLM stages are fully deterministic and testable by construction, the LLM stages are isolated and their outputs are easy to mock, and the cost per query stays under a cent.

**I chose two models rather than one.** Nova Micro is 29× cheaper than Claude Haiku 4.5 for theme extraction because theme extraction is pattern matching, not reasoning. The temptation was to use Haiku for everything and keep the code simpler. I resisted it because the cost difference is large enough to matter in aggregate and the task genuinely does not need reasoning — Nova Micro is not a compromise, it is the right tool. The tradeoff is an extra environment variable and two different invocation paths in `llm.py`, which is a small price.

**SSE over WebSocket was the right call for this.** The stream is unidirectional — server pushes agent progress events to the browser, the browser never sends anything back mid-stream. WebSocket adds bidirectional state for no benefit here. `EventSource` is native in every modern browser. SSE fit in about twenty lines of Flask code; a WebSocket implementation would have needed an extra library and more server-side connection management. The one real limitation is that SSE reconnects automatically on drop, which means a slow query can double-stream if the client drops and reconnects before the server finishes — I guard against this in the cache key check but it is not airtight.

**The daily cache key includes the review window.** An early version keyed only on `query + marketplace`. That meant a search for `wireless earbuds / US / 7 days` would return the same cached result as `wireless earbuds / US / 90 days`, which is wrong — the short-window run has fewer reviews and different trend signals. Adding the window to the key means each combination is independently cached, which is correct, and the EventBridge flush at 00:05 UTC resets all of them anyway.

**The budget guard degrades gracefully instead of halting hard.** When the daily Bedrock spend cap is hit, the system returns cached results if they exist, or continues without LLM calls (returning deterministic scores only) if they do not. A hard stop would be cleaner to reason about but it means the UI goes dark at budget exhaustion. The current behaviour means something always comes back. The honest cost is that a user hitting the budget cap gets results without LLM-generated summaries and there is no obvious UI signal that this happened — the results look the same. I should surface that more clearly.

**Selenium Chrome headless is the live provider, and it is not a production scraper.** I used Selenium because it renders the full page including dynamically loaded content, which is what you need to get product badge text and review histograms. It also requires a real browser binary, which is a heavy dependency. The honest alternative would be the Product Advertising API, which is the right tool for production access to Amazon catalog data, requires an Associates account, and has its own terms. The Selenium path exists to demonstrate the provider-interface pattern — `base.py` defines the interface, `amazon.py` is one implementation, `mock_provider.py` in tests is another.

**Score weights are environment variables.** My first version hardcoded `0.35 / 0.25 / 0.20 / 0.20`. The weights encode a real editorial judgement (reviews matter more than price), but they are also arguable. Making them configurable means you can tune them without touching code, and making them explicit in `.env` forces you to think about them rather than accepting defaults silently. The test suite uses the default weights, so if you change them, some assertions about absolute score values will break — that is intentional.

**The Lambda functions in `template.yaml` ship with placeholder inline code.** CloudFormation's `ZipFile` property cannot package a multi-file `src/` tree. The template proves the infrastructure shape — IAM roles, CloudWatch alarms, SNS, EventBridge, AgentCore wiring — and validates cleanly with `cfn-lint`. Actual deployment requires zipping each `lambda/*/handler.py` with `src/` and referencing it via S3. I did not automate that step because it is a packaging concern, not an architecture concern, and adding a SAM or CDK layer would obscure the CloudFormation structure.

**Limitations:**
- The test suite uses `mock_provider.py`. The live Selenium provider is not tested in CI. It works manually but any change to Amazon's page structure will silently break it.
- LLM provider is locked to Bedrock. There is no abstraction layer for OpenAI, Anthropic direct, or anything else — swapping would require changes in `llm.py` and the two calling agents.
- No batch or CSV input mode. The system takes one query at a time through the UI or `scripts/invoke_agent.py`. Running a list of queries requires scripting the CLI invocation.
- Chrome and ChromeDriver must be installed system-wide and their major versions must match. `setup.sh` warns on mismatch but does not fix it.
- `AmazonProvider.get_price_history()` returns only today's price as a single observation. Amazon does not expose historical pricing to scraping, so there is no trend to compute from the live provider — price trend analysis requires stored observations over time or a service like Keepa.
- The live provider should only be used for personal, educational exploration of the architecture. Amazon's Conditions of Use prohibit automated data-mining without express permission.

---

## Stack

| Concern | Choice |
|:--|:--|
| Language | Python 3.11+ |
| Web framework | Flask (SSE streaming) |
| LLM (reasoning) | Amazon Bedrock — Claude Haiku 4.5 |
| LLM (pattern matching) | Amazon Bedrock — Nova Micro (29× cheaper) |
| Live scraping | Selenium Chrome headless |
| Config | Environment variables / `.env` |
| FinOps | Custom: budget guard, daily cache, token optimiser |
| Observability | CloudWatch Logs + Metrics + 3 alarms → SNS |
| Deployment | CloudFormation (22 resources), AgentCore Runtime + Gateway |
| Tests | pytest (41 tests, mock provider, no AWS required) |
| Lint | ruff |
