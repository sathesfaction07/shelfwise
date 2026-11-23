"""
Lambda: price_fetch
Single responsibility: fetch price history for one ASIN. See spec section 26.

Expected event shape:
  {"asin": "B001", "country": "US"}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from product_intelligence.tools.price_fetcher import fetch_price_history


def handler(event, context):
    asin = event["asin"]
    country = event["country"]

    price_history = fetch_price_history(asin, country)

    return {
        "asin": asin,
        "price_history": price_history,
    }
