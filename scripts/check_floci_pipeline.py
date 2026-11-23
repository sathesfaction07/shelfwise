"""Full four-stage check: synthetic catalogue fixtures, real Floci/Ollama calls."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from product_intelligence import config
from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.agents.pricing import PriceRatingAgent
from product_intelligence.agents.recommendation import RecommendationAgent, compute_score
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.observability.metrics import MetricsClient
from tests.mock_provider import MockProvider


def main():
    if config.AWS_MODE != "floci" or not config.REQUIRE_LLM_SUCCESS:
        raise ValueError("Set AWS_MODE=floci and REQUIRE_LLM_SUCCESS=true")
    provider = MockProvider()
    metrics = MetricsClient()
    candidates = DiscoveryAgent(provider, metrics).find_candidates("headphones", "US")[:2]
    if not candidates:
        raise ValueError("Synthetic fixture produced no candidates")
    evaluated = []
    for product in candidates:
        asin = product["asin"]
        reviews = ReviewIntelligenceAgent(provider, metrics).analyze(
            asin, "US", product_title=product["title"], window_days=366,
        )
        if not reviews["review_count"] or reviews["analysis_source"] != "llm":
            raise ValueError("Review model inference was not exercised")
        pricing = PriceRatingAgent(provider, metrics).analyze(
            asin, "US", recent_reviews=provider.get_reviews(asin, "US"),
        )
        evaluated.append({"product": product, "reviews": reviews, "pricing": pricing,
                          "scores": compute_score(product, reviews, pricing)})
    recommendation = RecommendationAgent(metrics).recommend(evaluated)
    if recommendation["asin"] not in {p["asin"] for p in candidates}:
        raise ValueError("Recommendation selected a product outside the candidate set")
    report = {
        "status": "passed", "data_source": "synthetic test fixtures",
        "inference": "Floci Bedrock Converse proxy to configured Ollama model",
        "evaluated": evaluated, "recommendation": recommendation,
    }
    output = ROOT / "output" / "floci_pipeline_check.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Four-stage check passed; evidence: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
