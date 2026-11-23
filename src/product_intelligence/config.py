"""
Central configuration loader.

Priority order: environment variables -> .env file (via python-dotenv) -> defaults below.
Nothing in this file should be hardcoded elsewhere in the codebase - always import from here.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv is optional at runtime if the environment already has the vars set
    pass


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Data provider ---
# Provider is always live (AmazonProvider). The DATA_DIR below is retained
# for any future local-data lookups but the app uses Selenium exclusively.
DATA_DIR = _env_str("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data"))

# --- Review / sampling behavior ---
REVIEW_WINDOW_DAYS = _env_int("REVIEW_WINDOW_DAYS", 90)
MAX_REVIEWS_PER_ASIN = _env_int("MAX_REVIEWS_PER_ASIN", 33)
MAX_PRODUCTS = _env_int("MAX_PRODUCTS", 5)

# Per-rating sampling caps used by analysis/reviews.py::sample_representative
RATING_SAMPLE_CAPS = {
    5: _env_int("SAMPLE_CAP_5_STAR", 10),
    4: _env_int("SAMPLE_CAP_4_STAR", 5),
    3: _env_int("SAMPLE_CAP_3_STAR", 3),
    2: _env_int("SAMPLE_CAP_2_STAR", 5),
    1: _env_int("SAMPLE_CAP_1_STAR", 10),
}

# --- AWS / Bedrock ---
AWS_REGION = _env_str("AWS_REGION", "us-east-1")
AWS_MODE = _env_str("AWS_MODE", "aws").lower()
AWS_ENDPOINT_URL = _env_str("AWS_ENDPOINT_URL", "")
LOCAL_LLM_TIMEOUT_SECONDS = _env_int("LOCAL_LLM_TIMEOUT_SECONDS", 75)
LOCAL_REVIEW_EXCERPT_CHARS = _env_int("LOCAL_REVIEW_EXCERPT_CHARS", 300)
LOCAL_REVIEW_SAMPLE_PER_SENTIMENT = _env_int("LOCAL_REVIEW_SAMPLE_PER_SENTIMENT", 3)
# Floci/Ollama integration checks must not succeed through mock fallbacks.
REQUIRE_LLM_SUCCESS = _env_str("REQUIRE_LLM_SUCCESS", str(AWS_MODE == "floci")).lower() == "true"
MODEL_ID = _env_str("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
# Nova Micro: ~97% cheaper than Haiku for simple tasks (pattern/theme extraction)
MODEL_ID_TOOLS = _env_str("MODEL_ID_TOOLS", "amazon.nova-micro-v1:0")

# --- CloudWatch namespace / SNS ---
METRICS_NAMESPACE = _env_str("METRICS_NAMESPACE", "Julie")
LOG_GROUP_NAME = _env_str("LOG_GROUP_NAME", "/julie/app")
SNS_TOPIC_ARN = _env_str("SNS_TOPIC_ARN", "")

# --- Alarm thresholds (also used by the CloudFormation template as defaults) ---
AGENT_ERROR_THRESHOLD = _env_int("AGENT_ERROR_THRESHOLD", 5)
INPUT_TOKEN_ALARM_THRESHOLD = _env_int("INPUT_TOKEN_ALARM_THRESHOLD", 50000)
DAILY_COST_BUDGET_USD = _env_float("DAILY_COST_BUDGET_USD", 5.0)

# --- Discovery candidate selection (agents/discovery.py) ---
# Pool size: how many products to pull from the provider before ranking.
# Each pooled product costs a browser page fetch, so keep this lean.
DISCOVERY_POOL_SIZE = _env_int("DISCOVERY_POOL_SIZE", 8)
# "Trending" gate: needs at least this many reviews inside REVIEW_WINDOW_DAYS...
TRENDING_MIN_RECENT_REVIEWS = _env_int("TRENDING_MIN_RECENT_REVIEWS", 3)
# ...with at least this recent average rating.
TRENDING_MIN_AVG_RATING = _env_float("TRENDING_MIN_AVG_RATING", 4.0)
# "Bought in past month" gate: Amazon's own sales-velocity badge. Products
# at/above this monthly purchase volume (and rating-qualified) are trending.
TRENDING_MIN_MONTHLY_PURCHASES = _env_int("TRENDING_MIN_MONTHLY_PURCHASES", 100)
# Best-seller gate: overall rating must be >= this to count as a best seller.
BESTSELLER_MIN_RATING = _env_float("BESTSELLER_MIN_RATING", 4.0)
# Safety guard (hard filter in fallback tier): candidates whose recent
# negative-review share meets/exceeds this percentage are excluded.
DISCOVERY_MAX_NEGATIVE_PERCENT = _env_float("DISCOVERY_MAX_NEGATIVE_PERCENT", 30.0)


# --- Recommendation score weights (must sum to 1.0) ---
SCORE_WEIGHTS = {
    "review": _env_float("SCORE_WEIGHT_REVIEW", 0.35),
    "rating": _env_float("SCORE_WEIGHT_RATING", 0.25),
    "price": _env_float("SCORE_WEIGHT_PRICE", 0.20),
    "trend": _env_float("SCORE_WEIGHT_TREND", 0.20),
}

# --- Amazon domain mapping (used by the live provider) ---
# All Amazon retail marketplaces in operation (amazon.cn excluded — Amazon
# closed its China domestic marketplace; the domain no longer serves search).
AMAZON_DOMAINS = {
    # Americas
    "US": "amazon.com",
    "CA": "amazon.ca",
    "MX": "amazon.com.mx",
    "BR": "amazon.com.br",
    "CO": "amazon.com.co",
    # Europe
    "UK": "amazon.co.uk",
    "DE": "amazon.de",
    "FR": "amazon.fr",
    "IT": "amazon.it",
    "ES": "amazon.es",
    "NL": "amazon.nl",
    "SE": "amazon.se",
    "PL": "amazon.pl",
    "BE": "amazon.com.be",
    "IE": "amazon.ie",
    "TR": "amazon.com.tr",
    # Middle East & Africa
    "AE": "amazon.ae",
    "SA": "amazon.sa",
    "EG": "amazon.eg",
    "ZA": "amazon.co.za",
    "NG": "amazon.com.ng",
    # Asia-Pacific
    "IN": "amazon.in",
    "JP": "amazon.co.jp",
    "SG": "amazon.sg",
    "AU": "amazon.com.au",
}

# Default currency per marketplace (fallback when _detect_currency returns None).
# This is the expected local currency for each marketplace - used ONLY when the
# scraped price string doesn't contain a recognizable currency marker.
DEFAULT_CURRENCY = {
    # Americas
    "US": "$",
    "CA": "C$",
    "MX": "MX$",
    "BR": "R$",
    "CO": "COP",
    # Europe
    "UK": "£",
    "DE": "€",
    "FR": "€",
    "IT": "€",
    "ES": "€",
    "NL": "€",
    "SE": "kr",
    "PL": "zł",
    "BE": "€",
    "IE": "€",
    "TR": "₺",
    # Middle East & Africa
    "AE": "AED",
    "SA": "SAR",
    "EG": "EGP",
    "ZA": "R",
    "NG": "₦",
    # Asia-Pacific
    "IN": "₹",
    "JP": "¥",
    "SG": "S$",
    "AU": "A$",
}

