"""
Lambda: product_search
Single responsibility: search for candidate products. Thin wrapper around
product_intelligence.tools.product_search - see spec section 26.

Expected event shape (from AgentCore Gateway / MCP tool call):
  {"product_type": "wireless headphones", "country": "US", "max_results": 5}
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from product_intelligence.tools.product_search import search_products


def handler(event, context):
    product_type = event["product_type"]
    country = event["country"]
    max_results = event.get("max_results")

    results = search_products(product_type, country, max_results=max_results)

    return {
        "product_type": product_type,
        "country": country,
        "products": results,
    }
