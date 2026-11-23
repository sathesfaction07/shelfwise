"""
AgentCore Runtime entrypoint.

This wraps the same 4-agent pipeline used by scripts/run_local_demo.py in
the BedrockAgentCoreApp interface so it can be packaged and deployed to
AgentCore Runtime (spec section 24). It deliberately does NOT reimplement
any agent logic - it imports the same agents/providers used locally, so
"works locally" and "works deployed" mean the same codebase.

Local test (per AgentCore's standard CLI workflow - build, run locally,
invoke with sample input):
  pip install -r agentcore/requirements.txt
  python agentcore/runtime.py
  # in another terminal:
  curl -X POST http://localhost:8080/invocations \
    -H "Content-Type: application/json" \
    -d '{"product": "wireless headphones", "country": "US"}'

Deploy (via the AgentCore CLI/starter toolkit, or via the CloudFormation
AgentCoreRuntime resource in infrastructure/template.yaml):
  agentcore configure --entrypoint agentcore/runtime.py
  agentcore launch
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from product_intelligence import config
from product_intelligence.providers.amazon import AmazonProvider
from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.agents.pricing import PriceRatingAgent
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.agents.recommendation import RecommendationAgent, compute_score
from product_intelligence.observability.metrics import MetricsClient

app = BedrockAgentCoreApp()


def _get_provider():
    return AmazonProvider()


@app.entrypoint
def invoke(payload: dict) -> dict:
    product_type = payload.get("product")
    country = payload.get("country")
    min_rating = payload.get("min_rating")
    max_price = payload.get("max_price")

    if not product_type or not country:
        return {"error": "Both 'product' and 'country' are required."}

    metrics = MetricsClient()
    provider = _get_provider()

    discovery = DiscoveryAgent(provider, metrics)
    price_rating = PriceRatingAgent(provider, metrics)
    review_agent = ReviewIntelligenceAgent(provider, metrics, use_mock=False)
    recommender = RecommendationAgent(metrics, use_mock=False)

    candidates = discovery.find_candidates(product_type, country)
    if min_rating is not None:
        candidates = [c for c in candidates if c["rating"] >= min_rating]
    if max_price is not None:
        candidates = [c for c in candidates if c["price"] <= max_price]

    if not candidates:
        return {
            "query": {"product": product_type, "country": country},
            "recommendation": {"recommendation": "no_candidates", "asin": None},
        }

    evaluated = []
    for product in candidates:
        asin = product["asin"]
        all_reviews = provider.get_reviews(asin, country)
        review_result = review_agent.analyze(asin, country, product_title=product["title"])
        pricing_result = price_rating.analyze(asin, country, recent_reviews=all_reviews)
        scores = compute_score(product, review_result, pricing_result)
        evaluated.append({"product": product, "reviews": review_result, "pricing": pricing_result, "scores": scores})

    recommendation = recommender.recommend(evaluated)

    return {
        "query": {"product": product_type, "country": country},
        "candidates": evaluated,
        "recommendation": recommendation,
    }


if __name__ == "__main__":
    app.run()
