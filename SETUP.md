# Julie — Setup Guide

> **Live-only platform.** Every search scrapes real Amazon marketplaces in real time via a headless Chrome browser. There is no demo or mock data mode for product discovery — the platform always queries live Amazon retail sites.

---

## Table of Contents

1. [Overview](#overview)
2. [What Julie Does (Simple English)](#what-julie-does-simple-english)
3. [Architecture Diagram](#architecture-diagram)
4. [Project Structure](#project-structure)
5. [Key File Reference](#key-file-reference)
6. [What Each Agent Does](#what-each-agent-does)
7. [Prerequisites](#prerequisites)
8. [Quick Start (Local)](#quick-start-local)
9. [Full AWS Setup (CloudFormation)](#full-aws-setup-cloudformation)
10. [Switching AWS Accounts](#switching-aws-accounts)
11. [Environment Variables](#environment-variables)
12. [Commands Reference](#commands-reference)
13. [Supported Marketplaces](#supported-marketplaces)
14. [Review Window Feature](#review-window-feature)
15. [FinOps & Cost Management](#finops--cost-management)
16. [Testing](#testing)
17. [Troubleshooting](#troubleshooting)
18. [Cleanup](#cleanup)

---

## Overview

**Julie** is a 4-stage pipeline (2 AI agents + 2 processing stages) that discovers trending products across 25 Amazon marketplaces, analyzes review sentiment, compares prices, and generates ranked purchase recommendations — all in real time.

Key highlights:

- **25 Amazon marketplaces** across the Americas, Europe, Middle East & Africa, and Asia-Pacific
- **Parallel browser scraping** — ~50 seconds per search (vs. ~3 minutes sequential) via Selenium Chrome headless
- **Dual-model AI** — Claude Haiku 4.5 for reasoning & summaries, Nova Micro for theme extraction (29× cheaper)
- **Real-time streaming** via Server-Sent Events (SSE) — watch agents communicate as they work
- **FinOps built-in** — daily cost budget, result caching, token optimization
- **Zero-cost repeat searches** via daily cache keyed to query + marketplace + review window

---

## What Julie Does (Simple English)

When you type a product query and click **Analyze**, here is exactly what happens:

1. **Julie opens a headless Chrome browser** and searches Amazon for your query in the selected marketplace — just like a real user would, but faster. Sponsored/ad tiles are silently skipped. Multiple browser tabs run in parallel to fetch ~5 products at once (takes ~50 seconds total).

2. **Julie filters the results** using three tiers: products with a monthly-purchases badge + 4★ or higher go to the top ("Trending"), followed by Best Sellers ranked by review volume, with a safety fallback that blocks anything with ≥ 30% negative reviews.

3. **Julie reads the customer reviews** for each product, filtering to only those posted within your chosen review window (default: last 90 days). It sends those reviews to Amazon Bedrock — Claude Haiku 4.5 for sentiment summaries and Nova Micro for fast theme extraction — to produce pros/cons and a review score.

4. **Julie checks the price and star rating**, normalises prices to USD for comparison, and produces a price-competitiveness score and a rating score.

5. **Julie scores and ranks all products** using four weighted factors: review quality (35%), star rating (25%), price (20%), and trend signal (20%). The final ranked list — along with every agent's "thoughts" — streams back to your browser via the live dialogue feed.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Browser  (index.html)                           │
│          Query + Marketplace + Review Window → SSE Stream           │
└───────────────────────────┬─────────────────────────────────────────┘
                            │  HTTP POST /analyze  (Flask :5050)
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Flask Web Server  (app.py)                       │
│   • SSE /stream endpoint   • Daily cache check   • Budget guard     │
└───────┬───────────────────────────────────────────────────────────┬─┘
        │                                                           │
        ▼                                                           ▼
┌───────────────────┐                                   ┌───────────────────┐
│  Agent 1          │                                   │  FinOps Layer     │
│  DISCOVERY        │  ─── product list ──►             │  budget.py        │
│  (discovery.py)   │                                   │  daily_cache.py   │
│                   │                                   │  pricing.py       │
│  Selenium Chrome  │                                   └───────────────────┘
│  headless scraper │
│  amazon.py        │
└───────┬───────────┘
        │  top-N products
        ▼
┌───────────────────┐
│  Agent 2          │
│  REVIEW           │  ─── review scores ──►
│  INTELLIGENCE     │
│  (review.py)      │
│                   │
│  Claude Haiku 4.5 │  ← sentiment & summary
│  Nova Micro       │  ← theme extraction
│  + histogram      │
└───────┬───────────┘
        │  enriched products
        ▼
┌───────────────────┐
│  Agent 3          │
│  PRICE & RATING   │  ─── price/rating data ──►
│  (pricing.py)     │
│                   │
│  Multi-marketplace│
│  price fetch      │
└───────┬───────────┘
        │  fully enriched products
        ▼
┌───────────────────┐
│  Agent 4          │
│  RECOMMENDATION   │  ─── final ranked list ──►  SSE → Browser
│  (recommendation  │
│  .py)             │
│                   │
│  Score weighting  │
│  0.35/0.25/0.20/  │
│  0.20             │
└───────────────────┘
```

**Score weights:** Review 0.35 · Rating 0.25 · Price 0.20 · Trend 0.20

---

## Project Structure

```
amazon-julie/
│
├── app.py                          # Flask web server (port 5050, SSE streaming)
├── requirements.txt                # Python dependencies
├── SETUP.md                        # This file
│
├── static/
│   └── index.html                  # Single-page UI (~56 KB)
│
├── src/
│   └── product_intelligence/
│       ├── config.py               # Central config: 25 marketplaces, currencies, defaults
│       │
│       ├── agents/
│       │   ├── discovery.py        # Agent 1 — trending product discovery
│       │   ├── review.py           # Agent 2 — review sentiment & histogram
│       │   ├── pricing.py          # Agent 3 — price & rating comparison
│       │   └── recommendation.py  # Agent 4 — scoring & ranking
│       │
│       ├── providers/
│       │   └── amazon.py           # Live Selenium scraper (Chrome headless)
│       │
│       ├── finops/
│       │   ├── budget.py           # Daily cost budget guard
│       │   ├── daily_cache.py      # Zero-cost repeat search cache
│       │   └── pricing.py          # Token cost estimation
│       │
│       └── observability/
│           ├── logging.py          # Structured logging
│           └── metrics.py          # CloudWatch metrics emission
│
├── infrastructure/
│   └── template.yaml               # CloudFormation (22 resources)
│
├── scripts/
│   ├── billing_report.py           # Generate cost report
│   ├── cleanup.py                  # Tear down AWS resources
│   ├── invoke_agent.py             # CLI agent invocation
│   └── run_local_demo.py           # Local run helper
│
└── tests/
    ├── test_discovery.py
    ├── test_end_to_end.py
    ├── test_finops.py
    ├── test_prices.py
    ├── test_products.py
    ├── test_reviews.py
    └── test_token_optimization.py
```

---

## Key File Reference

| File | Purpose |
|---|---|
| `app.py` | Flask entry point — SSE `/stream` endpoint, daily cache check, budget guard, signal handlers for graceful Chrome cleanup on crash |
| `src/.../agents/discovery.py` | Agent 1 — Chrome headless scraper, parallel tab pool, tier filtering, sponsored tile skip, monthly-purchases badge extraction |
| `src/.../agents/review.py` | Agent 2 — review window filtering, rating histogram extraction, Bedrock LLM calls (Haiku 4.5 + Nova Micro) |
| `src/.../agents/pricing.py` | Agent 3 — price normalisation, currency auto-detection from scraped price strings, rating score calculation |
| `src/.../agents/recommendation.py` | Agent 4 — weighted scoring (0.35/0.25/0.20/0.20), final ranking, SSE stream output |
| `src/.../providers/amazon.py` | Core Selenium driver — Chrome headless session management, retry logic, multi-marketplace URL routing |
| `src/.../config.py` | Master config — 25 marketplace definitions with currency symbols, default weights, review window bounds |
| `src/.../finops/budget.py` | Daily budget guard — tracks Bedrock spend per UTC day, blocks new LLM calls when cap is hit |
| `src/.../finops/daily_cache.py` | Zero-cost cache — keyed by `query + marketplace + review_window_days`, valid per UTC calendar day |
| `src/.../finops/pricing.py` | Token cost estimator — uses per-model pricing rates to pre-flight token budgets |
| `infrastructure/template.yaml` | CloudFormation template — 22 resources including Lambda ×4, CloudWatch alarms, SNS, EventBridge, AgentCore |
| `scripts/billing_report.py` | Generates a per-day Bedrock cost breakdown |
| `scripts/cleanup.py` | Empties S3 cache bucket then deletes the CloudFormation stack |
| `static/index.html` | Single-page UI — agent dialogue feed, marketplace selector, review window slider |

---

## What Each Agent Does

### Agent 1 — Discovery (`discovery.py`)

Launches a headless Chrome browser and scrapes Amazon search result pages for the given query + marketplace. Runs **multiple tabs in parallel** (pool size 8 by default) to drastically reduce latency.

**Discovery tiers** (products must pass at least one):

| Tier | Criteria |
|------|----------|
| **Trending** | ≥ 100 monthly purchases badge **and** ≥ 4.0★, **OR** ≥ 3 recent reviews **and** ≥ 4.0★ avg |
| **Best Seller** | Ranked by `4.0★ × log(review_count)` |
| **Guarded Fallback** | Admits products that do **not** have ≥ 30% negative reviews |

> **Sponsored tile filtering:** Sponsored/ad tiles are silently skipped unless they carry a genuine organic star rating **and** review count. This prevents ads from contaminating the results.

> **Monthly purchases badge:** The scraper extracts "X bought in past month" badges where present — this feeds the Trending tier gate.

---

### Agent 2 — Review Intelligence (`review.py`)

For each discovered product, fetches the product detail page and extracts:

- **Rating histogram** (1★–5★ counts)
- **Recent reviews** filtered to the configured review window (7–366 days)
- Sends review text to **Amazon Bedrock Claude Haiku 4.5** for sentiment analysis and a synthesised pros/cons summary
- Sends review text to **Amazon Bedrock Nova Micro** for fast, cheap theme extraction (topics, keywords)

The review score (weight 0.35) is the dominant factor in the final recommendation score.

---

### Agent 3 — Price & Rating (`pricing.py`)

Cross-checks the product's price and rating against the scraped marketplace data. Normalises prices to USD for comparison, detects the currency from the scraped price string (see [Currency Auto-detection](#environment-variables)), and produces a normalised price score (weight 0.20) plus a raw rating score (weight 0.25).

---

### Agent 4 — Recommendation (`recommendation.py`)

Combines the three upstream scores with the trend signal (weight 0.20) into a single composite score, sorts products, and streams the final ranked list back to the browser via SSE. The **agent dialogue feed** in the UI shows every inter-agent message in real time so you can watch the pipeline think.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.10+** | `python3 --version` |
| **Google Chrome** | Must be installed system-wide. **Not Firefox.** Uses Chrome headless mode. |
| **ChromeDriver** | Must match your Chrome version. See [chromedriver.chromium.org](https://chromedriver.chromium.org/downloads) |
| **AWS CLI v2** | `aws --version` should show `aws-cli/2.x` |
| **AWS Account** | Account **YOUR_AWS_ACCOUNT_ID**, region **us-east-1**, with Bedrock access enabled |
| **Bedrock model access** | Enable `claude-haiku-4-5` and `amazon.nova-micro-v1` in the Bedrock console (us-east-1) |
| **pip packages** | `pip install -r requirements.txt` |

### Installing ChromeDriver (macOS)

```bash
# Using Homebrew
brew install --cask chromedriver

# Verify
chromedriver --version
```

### Installing ChromeDriver (Ubuntu/Debian)

```bash
# Match your Chrome version
CHROME_VER=$(google-chrome --version | awk '{print $3}' | cut -d. -f1)
apt-get install -y chromium-driver
```

---

## Quick Start (Local)

### 1. Clone and install

```bash
git clone <repo-url> amazon-julie
cd amazon-julie
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure AWS credentials

```bash
# Create/edit ~/.aws/credentials
[bedrock-deployer]
aws_access_key_id     = YOUR_ACCESS_KEY
aws_secret_access_key = YOUR_SECRET_KEY
region                = us-east-1
```

Or use `aws configure --profile bedrock-deployer`.

### 3. Set environment variables

```bash
export AWS_PROFILE=bedrock-deployer
export AWS_REGION=us-east-1
export MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0   # us. prefix REQUIRED
export MODEL_ID_TOOLS=amazon.nova-micro-v1:0                  # Nova Micro for theme extraction
```

> ⚠️ **The `us.` prefix on `MODEL_ID` is mandatory.** Omitting it causes a `ValidationException` from the Bedrock API. This is the cross-region inference prefix.

### 4. Run the Flask server

```bash
python app.py
```

Open **http://localhost:5050** in your browser.

### 5. Run your first search

1. Type a product query (e.g., `wireless earbuds`)
2. Select a marketplace (e.g., `US ($)`)
3. Optionally adjust the review window (default 90 days)
4. Click **Analyze**
5. Watch the agent dialogue feed stream in real time — results appear in ~50 seconds

---

## Full AWS Setup (CloudFormation)

The `infrastructure/template.yaml` deploys a serverless monitoring and alerting stack on top of the local platform.

### CloudFormation Parameters

| Parameter | Default | Description |
|---|---|---|
| `NotificationEmail` | *(required)* | SNS alert destination |
| `DailyCostBudgetUsd` | `5` | Daily Bedrock spend cap (USD) |
| `AgentErrorThreshold` | `5` | Alarm when agent errors exceed this count |
| `InputTokenAlarmThreshold` | `50000` | CloudWatch alarm threshold for daily input tokens |
| `ModelId` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model ID (Claude Haiku 4.5) |
| `ReviewWindowDays` | `90` | Default review window (7–366) |
| `DiscoveryPoolSize` | `8` | Parallel browser tab pool size |

> **Note:** There is no `DataProvider` parameter. Julie is always live — there is no demo or mock scraping mode.

### Resources Created (22 total)

| Type | Resource |
|---|---|
| S3 | Result cache bucket |
| IAM | Execution roles for Lambda + AgentCore |
| Lambda (×4) | One per agent (Discovery, Review, Pricing, Recommendation) |
| CloudWatch Logs | Log groups for all Lambdas |
| CloudWatch Metrics | Custom metric namespace |
| CloudWatch Alarms | Cost alarm, error rate alarm, token alarm |
| SNS | Alert topic → `NotificationEmail` |
| EventBridge | Scheduled cache flush rule |
| AgentCore Runtime | AWS AgentCore agent runtime |
| AgentCore Gateway | HTTP gateway for AgentCore |

### Deploy

```bash
# Package and deploy
aws cloudformation package \
  --template-file infrastructure/template.yaml \
  --s3-bucket YOUR-DEPLOYMENT-BUCKET \
  --output-template-file packaged.yaml \
  --profile bedrock-deployer

aws cloudformation deploy \
  --template-file packaged.yaml \
  --stack-name julie \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      NotificationEmail=you@example.com \
      DailyCostBudgetUsd=5 \
  --profile bedrock-deployer \
  --region us-east-1
```

### Check stack status

```bash
aws cloudformation describe-stacks \
  --stack-name julie \
  --query "Stacks[0].StackStatus" \
  --profile bedrock-deployer
```

---

## Switching AWS Accounts

Julie has no hard-coded AWS account references in application code. Switching to a different account requires changing **only 2–3 environment variables** — no code changes.

| Variable | Current value | What to change |
|---|---|---|
| `AWS_PROFILE` | `bedrock-deployer` | A profile in `~/.aws/credentials` pointing at the new account |
| `AWS_REGION` | `us-east-1` | The region where the target account has Bedrock access |
| `AWS_ACCOUNT_ID` *(optional)* | `YOUR_AWS_ACCOUNT_ID` | Only needed if your scripts reference the account ID explicitly |

**Steps:**

```bash
# 1. Add a new profile for the target account
aws configure --profile my-other-account
#    → enter the new account's Access Key, Secret Key, region

# 2. Point Julie at it
export AWS_PROFILE=my-other-account
export AWS_REGION=us-east-1   # or the new region

# 3. Confirm Bedrock model access in the new account
aws bedrock list-foundation-models \
  --region us-east-1 \
  --profile my-other-account \
  --query "modelSummaries[?contains(modelId,'claude-haiku')]"

# 4. Restart Julie — no code changes needed
python app.py
```

> ⚠️ If you deploy the CloudFormation stack to the new account, re-run the `aws cloudformation deploy` command with `--profile my-other-account`. The stack name `julie` and all 22 resources are fully portable.

---

## Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `MODEL_ID` | — | ✅ | `us.anthropic.claude-haiku-4-5-20251001-v1:0` — Claude Haiku 4.5 for reasoning & summaries. **`us.` prefix is mandatory** |
| `MODEL_ID_TOOLS` | — | ✅ | `amazon.nova-micro-v1:0` — Nova Micro ($0.035/1M tokens) for theme extraction; 29× cheaper than Haiku |
| `AWS_PROFILE` | `default` | ✅ | AWS credentials profile — use `bedrock-deployer` |
| `AWS_REGION` | `us-east-1` | ✅ | Must be `us-east-1` for Bedrock Claude Haiku 4.5 |
| `REVIEW_WINDOW_DAYS` | `90` | ❌ | Default review lookback period (7–366). Can be overridden per request. |
| `DISCOVERY_POOL_SIZE` | `8` | ❌ | Number of parallel browser tabs for live discovery |
| `DAILY_COST_BUDGET_USD` | `5.0` | ❌ | Maximum Bedrock spend per UTC day (USD) |
| `SCORE_WEIGHT_REVIEW` | `0.35` | ❌ | Weight of review score in final composite |
| `SCORE_WEIGHT_RATING` | `0.25` | ❌ | Weight of star rating score |
| `SCORE_WEIGHT_PRICE` | `0.20` | ❌ | Weight of price competitiveness score |
| `SCORE_WEIGHT_TREND` | `0.20` | ❌ | Weight of trend/purchase-volume score |

### `.env` file (optional)

Create a `.env` file in the project root (never commit this):

```dotenv
MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
MODEL_ID_TOOLS=amazon.nova-micro-v1:0
AWS_PROFILE=bedrock-deployer
AWS_REGION=us-east-1
REVIEW_WINDOW_DAYS=90
DISCOVERY_POOL_SIZE=8
DAILY_COST_BUDGET_USD=5.0
```

Then load it before starting the server:

```bash
set -a && source .env && set +a
python app.py
```

### Currency auto-detection

Prices are shown in the **currency detected from the scraped price string**, not assumed from the marketplace. For example, a UK listing might show "£24.99" — the scraper parses the `£` symbol and records GBP. If the currency symbol cannot be parsed, a per-marketplace default currency from `config.py` is used as fallback.

---

## Commands Reference

### Local development

```bash
# Start the web server
python app.py

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_discovery.py -v

# Run tests with coverage
pytest tests/ --cov=src --cov-report=term-missing
```

### Scripts

```bash
# Generate a billing / cost report
python scripts/billing_report.py

# Invoke an agent directly from the CLI
python scripts/invoke_agent.py --query "bluetooth speakers" --marketplace US

# Run the local pipeline helper
python scripts/run_local_demo.py

# Clean up AWS resources (stack + S3 objects)
python scripts/cleanup.py
```

### AWS CLI shortcuts

```bash
# Tail Lambda logs (replace FUNCTION_NAME)
aws logs tail /aws/lambda/FUNCTION_NAME --follow --profile bedrock-deployer

# Describe CloudFormation stack outputs
aws cloudformation describe-stacks \
  --stack-name julie \
  --query "Stacks[0].Outputs" \
  --profile bedrock-deployer

# Check daily Bedrock cost
python scripts/billing_report.py --date today
```

---

## Supported Marketplaces

25 Amazon marketplaces are supported. Select the marketplace in the UI dropdown or pass it as a parameter to the API/scripts.

### Americas (5)

| Code | Country | Currency | Symbol |
|---|---|---|---|
| US | United States | USD | $ |
| CA | Canada | CAD | C$ |
| MX | Mexico | MXN | MX$ |
| BR | Brazil | BRL | R$ |
| CO | Colombia | COP | COP |

### Europe (11)

| Code | Country | Currency | Symbol |
|---|---|---|---|
| UK | United Kingdom | GBP | £ |
| DE | Germany | EUR | € |
| FR | France | EUR | € |
| IT | Italy | EUR | € |
| ES | Spain | EUR | € |
| NL | Netherlands | EUR | € |
| SE | Sweden | SEK | kr |
| PL | Poland | PLN | zł |
| BE | Belgium | EUR | € |
| IE | Ireland | EUR | € |
| TR | Turkey | TRY | ₺ |

### Middle East & Africa (5)

| Code | Country | Currency | Symbol |
|---|---|---|---|
| AE | United Arab Emirates | AED | AED |
| SA | Saudi Arabia | SAR | SAR |
| EG | Egypt | EGP | EGP |
| ZA | South Africa | ZAR | R |
| NG | Nigeria | NGN | ₦ |

### Asia-Pacific (4)

| Code | Country | Currency | Symbol |
|---|---|---|---|
| IN | India | INR | ₹ |
| JP | Japan | JPY | ¥ |
| SG | Singapore | SGD | S$ |
| AU | Australia | AUD | A$ |

---

## Review Window Feature

The **review window** controls how far back in time reviews are considered by Agent 2 (Review Intelligence). This is a first-class feature with several implications:

| Setting | Range | Default |
|---|---|---|
| `REVIEW_WINDOW_DAYS` env var | 7–366 days | 90 days |
| Per-request override | 7–366 days | Uses env var default |

### How the window affects the pipeline

1. **Discovery (Agent 1):** The Trending tier gate counts "recent reviews" within the window. A product with 10 reviews in the past 7 days may qualify as Trending on a short window but not on a long one.

2. **Review Intelligence (Agent 2):** Only reviews posted within the window are sent to the LLM for sentiment analysis. This keeps analysis focused on the most current customer sentiment.

3. **Daily cache keying:** The cache key includes the window value. Searching for `wireless earbuds / US / 30 days` and `wireless earbuds / US / 90 days` produce **separate cache entries** — they do not collide.

### Choosing a window

| Use case | Recommended window |
|---|---|
| Catching viral / newly trending products | 7–14 days |
| General product research | 90 days (default) |
| Long-term quality assessment | 180–366 days |

---

## FinOps & Cost Management

Julie has three layers of cost control, and uses the cheapest available model for each task to keep per-query costs well under a cent.

### 1. Daily budget guard

Set by `DAILY_COST_BUDGET_USD` (default `$5.00`). The `finops/budget.py` module tracks Bedrock API spend per UTC day. Once the budget is exhausted, the platform returns cached results only or declines new LLM calls until midnight UTC.

### 2. Daily result cache

`finops/daily_cache.py` caches complete pipeline results keyed by:

```
query + marketplace + review_window_days
```

Cache entries are valid for the UTC calendar day. A repeat search within the same day costs **$0** in Bedrock tokens. The CloudFormation EventBridge rule flushes the S3 cache at `00:05 UTC` daily.

### 3. Token optimisation

`finops/pricing.py` estimates token cost before each LLM call. Reviews are truncated or batched to stay within per-call token budgets. The `test_token_optimization.py` test suite validates this logic.

### Per-Stage Cost Breakdown

| Agent Stage | Model Used | Cost per Run |
|---|---|---|
| Agent 1 — Discovery | Selenium Chrome headless (no LLM) | $0.000 |
| Agent 2 — Theme extraction (per product) | **Nova Micro** ($0.035 / 1M tokens) | ~$0.001–$0.003 |
| Agent 2 — Sentiment summary (per product) | Claude Haiku 4.5 | ~$0.001–$0.002 |
| Agent 3 — Price & Rating | Computation only | $0.000 |
| Agent 4 — Final recommendation | Claude Haiku 4.5 | ~$0.001–$0.002 total |
| **Full query (5 products, cold cache)** | | **~$0.005–$0.01** |

> Nova Micro is used specifically for theme extraction because it is **29× cheaper** than Claude Haiku 4.5 while being equally effective at structured keyword/topic tasks.

### Estimated costs

| Scenario | Approximate cost |
|---|---|
| Single search, cold cache (5 products, 90-day window) | ~$0.005–$0.01 |
| 20 searches/day, varied queries | ~$0.10–$0.20 |
| Daily budget cap hit | $5.00 max |

Actual costs depend on review volume and product count. Monitor with `scripts/billing_report.py`.

---

## Testing

The test suite uses **pytest** and covers all major subsystems. Tests use a **mock provider** — Chrome and a live internet connection are **not required** to run the suite.

```bash
# Run all 41 tests
pytest tests/ -v

# Run with coverage report
pytest tests/ -v --cov=src --cov-report=term-missing

# Run a single module
pytest tests/test_discovery.py -v
pytest tests/test_finops.py -v
```

### Test files

| File | What it covers |
|---|---|
| `test_discovery.py` | Discovery tiers, sponsored tile filtering, pool logic |
| `test_end_to_end.py` | Full pipeline integration (mocked scraper) |
| `test_finops.py` | Budget guard, cache keying, cache hit/miss |
| `test_prices.py` | Price normalisation, currency detection |
| `test_products.py` | Product model validation |
| `test_reviews.py` | Review window filtering, histogram extraction |
| `test_token_optimization.py` | Token budget estimation, review truncation |

**Current status:** 41 tests passing ✅

> Tests use a mocked Selenium/Chrome scraper (mock provider) so they do not require Chrome or a live internet connection.

---

## Troubleshooting

### `ValidationException` from Bedrock

```
ValidationException: The provided model identifier is invalid.
```

**Cause:** `MODEL_ID` is missing the `us.` cross-region inference prefix.

**Fix:**
```bash
export MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
#                ^^ this prefix is REQUIRED
```

---

### Empty search results

**Symptom:** The UI shows "No products found" or the discovery agent returns an empty list.

**Causes & fixes:**

| Cause | Fix |
|---|---|
| Amazon rate-limiting your IP | The scraper automatically retries with a fresh session. Wait 30–60 seconds and try again. |
| ChromeDriver version mismatch | Run `google-chrome --version` and `chromedriver --version` — major versions must match. |
| Network/proxy blocking | Try a different network. Some corporate networks block headless browser traffic. |
| Query too niche | Try a broader search term. |

---

### Chrome / ChromeDriver not found

```
WebDriverException: 'chromedriver' executable needs to be in PATH
```

**Fix:**
```bash
# macOS
brew install --cask chromedriver
xattr -d com.apple.quarantine $(which chromedriver)   # remove Gatekeeper quarantine

# Ubuntu
sudo apt-get install -y chromium-driver
```

---

### Bedrock access denied

```
AccessDeniedException: User is not authorized to perform: bedrock:InvokeModel
```

**Fixes:**
1. Confirm you are using the `bedrock-deployer` profile: `export AWS_PROFILE=bedrock-deployer`
2. Confirm the IAM user/role has `bedrock:InvokeModel` permission in `us-east-1`
3. Confirm Claude Haiku 4.5 **and** Nova Micro are enabled in the Bedrock console → Model Access page (Account YOUR_AWS_ACCOUNT_ID, us-east-1)

---

### LLM calls fall back to mock responses

This is **expected behaviour** when Bedrock is unreachable (e.g., no AWS credentials, network issue, or budget exhausted). Julie continues to return product results — they will just lack LLM-generated review summaries. Check the server logs for `[MOCK]` or `[FALLBACK]` messages.

---

### Daily budget exhausted

```
BudgetExhaustedError: Daily Bedrock budget of $5.00 exceeded
```

**Options:**
- Wait until midnight UTC for the budget to reset
- Increase `DAILY_COST_BUDGET_USD` and restart the server
- Use cached results (repeat a previous search with the same query + marketplace + window)

---

### Port 5050 already in use

```
OSError: [Errno 48] Address already in use
```

```bash
# Find the process using port 5050
lsof -i :5050
# Kill it
kill -9 <PID>
# Or use a different port
PORT=5051 python app.py
```

---

## Cleanup

### Local cleanup

```bash
# Deactivate virtual environment
deactivate

# Remove virtual environment and cache
rm -rf .venv __pycache__ src/**/__pycache__ .pytest_cache
```

### AWS cleanup

```bash
# Delete the CloudFormation stack and all resources
python scripts/cleanup.py

# Or manually via AWS CLI
aws cloudformation delete-stack \
  --stack-name julie \
  --profile bedrock-deployer \
  --region us-east-1

# Monitor deletion
aws cloudformation wait stack-delete-complete \
  --stack-name julie \
  --profile bedrock-deployer
```

> ⚠️ **Note:** The S3 cache bucket must be **empty** before the stack can be deleted. `scripts/cleanup.py` handles this automatically. If deleting manually, empty the bucket first:
>
> ```bash
> aws s3 rm s3://YOUR-CACHE-BUCKET --recursive --profile bedrock-deployer
> ```

---

*Last updated: see git log for latest revision.*

---

## Tradeoffs and Decisions Made Along the Way

| Decision | Alternatives Considered | Why This Choice |
|----------|------------------------|----------------|
| Chrome over Firefox | Firefox/GeckoDriver | Faster headless mode, `page_load_strategy='eager'`, more commonly pre-installed |
| Nova Micro for theme extraction | Haiku for all LLM calls | 29× cheaper ($0.035 vs $1/1M tokens); theme grouping is pattern matching, not reasoning |
| Live-only (no demo mode) | Demo CSV provider for offline | Real Amazon data is the purpose; mock used only in tests and as LLM fallback |
| Multi-tab parallel loading | Sequential page fetches | ~50s vs ~3min; single browser, N tabs fired via `window.open()` |
| Daily cache keyed by product+window+country | Simple query-only key | Different windows produce different results; avoids stale cross-window conflicts |
| Signal handlers + weakref.WeakSet | Try/finally only | Covers crashes, SIGTERM, and atexit — prevents zombie Chrome in all exit paths |
| SSE streaming over WebSocket | WebSocket, polling | Simpler (unidirectional), native `EventSource` in browsers, no extra library |
| Review window as user-configurable (7–366) | Fixed 90-day window | Different use cases (trending vs historical); cache isolation per window |
| Sponsored tile filtering with pass-through | Block all ads, or allow all | Ads WITH organic rating data are genuinely useful; ads without aren't evaluable |
| Score weights as env vars | Hardcoded weights | Allows tuning without code changes; review > rating > price = trend |
| Currency detection from scraped string | Marketplace-based assumption | Amazon geo-converts prices based on IP; the rendered string is the source of truth |
| Budget guard with mock fallback | Hard stop on budget breach | Service stays available; users still get deterministic results when budget exhausted |

---

*These decisions reflect the constraints of educational demonstration: simplicity, testability with real data, beginner-friendly Python, and clear demonstration of Bedrock AgentCore + tokenization + CloudWatch alarm competencies.*
