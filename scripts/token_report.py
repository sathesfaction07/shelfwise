#!/usr/bin/env python3
"""
Runs the review pipeline for one product/country and prints the raw vs
optimized token comparison (spec section 39 - the "cost comparison
demonstration", one of the strongest reviewer-facing pieces of evidence).

Usage:
  python scripts/token_report.py --product "wireless headphones" --country US
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from product_intelligence.providers.demo import DemoProvider
from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.finops.usage import TokenUsage
from product_intelligence.observability.metrics import MetricsClient
from product_intelligence import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--mock", action="store_true", default=True,
                         help="Uses the mock LLM path by default so this works with no AWS credentials.")
    args = parser.parse_args()

    provider = DemoProvider()
    metrics = MetricsClient(enabled=False)
    discovery = DiscoveryAgent(provider, metrics)
    review_agent = ReviewIntelligenceAgent(provider, metrics, use_mock=args.mock)

    candidates = discovery.find_candidates(args.product, args.country)
    if not candidates:
        print("No products found.")
        return

    total_raw = 0
    total_optimized = 0
    print(f"{'ASIN':<8}{'Raw Est.':>12}{'Optimized':>12}{'Reduction':>12}")
    print("-" * 44)
    for product in candidates:
        result = review_agent.analyze(product["asin"], args.country, product_title=product["title"])
        tokens = result["tokens"]
        total_raw += tokens["raw_estimate"]
        total_optimized += tokens["optimized_estimate"]
        print(f"{product['asin']:<8}{tokens['raw_estimate']:>12}{tokens['optimized_estimate']:>12}{tokens['reduction_percent']:>11}%")

    overall_reduction = round((total_raw - total_optimized) / total_raw * 100, 1) if total_raw else 0.0
    print("-" * 44)
    print(f"{'TOTAL':<8}{total_raw:>12}{total_optimized:>12}{overall_reduction:>11}%")

    # Illustrative cost comparison using the optimized-model pricing
    usage_without = TokenUsage(config.MODEL_ID, input_tokens=total_raw, output_tokens=200)
    usage_with = TokenUsage(config.MODEL_ID, input_tokens=total_optimized, output_tokens=200)

    print("\nWITHOUT OPTIMIZATION")
    print(f"Input tokens: {total_raw}")
    print(f"Estimated cost: ${usage_without.cost_breakdown()['estimated_total_cost']:.6f}")

    print("\nWITH OPTIMIZATION")
    print(f"Input tokens: {total_optimized}")
    print(f"Estimated cost: ${usage_with.cost_breakdown()['estimated_total_cost']:.6f}")

    print(f"\nReduction: {overall_reduction}%")


if __name__ == "__main__":
    main()
