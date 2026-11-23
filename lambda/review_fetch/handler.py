"""
Lambda: review_fetch
Single responsibility: fetch reviews for one ASIN. See spec section 26.

Expected event shape:
  {"asin": "B001", "country": "US"}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from product_intelligence.tools.review_fetcher import fetch_reviews


def handler(event, context):
    asin = event["asin"]
    country = event["country"]

    reviews = fetch_reviews(asin, country)

    return {
        "asin": asin,
        "reviews": reviews,
    }
