"""
Model pricing configuration - kept in ONE place (spec section 37) rather
than hardcoded throughout the codebase. Values are illustrative; check the
current Bedrock pricing page and update model_pricing.json for real use.
"""
import json
import os

_PRICING_FILE = os.path.join(os.path.dirname(__file__), "model_pricing.json")

_DEFAULT_PRICING = {
    "anthropic.claude-haiku-4-5-20251001-v1:0": {
        "input_price_per_million": 1.00,
        "output_price_per_million": 5.00,
    },
    "anthropic.claude-sonnet-4-6-20260101-v1:0": {
        "input_price_per_million": 3.00,
        "output_price_per_million": 15.00,
    },
}


def load_pricing() -> dict:
    if os.path.exists(_PRICING_FILE):
        with open(_PRICING_FILE) as f:
            return json.load(f)
    return _DEFAULT_PRICING


def get_model_pricing(model_id: str) -> dict:
    pricing = load_pricing()
    if model_id not in pricing:
        raise KeyError(
            f"No pricing configured for model_id={model_id!r}. "
            f"Add it to src/product_intelligence/finops/model_pricing.json"
        )
    return pricing[model_id]
