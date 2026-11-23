"""
Full pipeline test using MockProvider (never live Amazon) and mock LLM
(no AWS credentials required to run this suite).
"""
from tests.mock_provider import MockProvider
from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.agents.pricing import PriceRatingAgent
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.agents.recommendation import RecommendationAgent, compute_score
from product_intelligence.observability.metrics import MetricsClient


def run_pipeline(product_type: str, country: str):
    metrics = MetricsClient(enabled=False)
    provider = MockProvider()

    discovery = DiscoveryAgent(provider, metrics)
    price_rating = PriceRatingAgent(provider, metrics)
    review_agent = ReviewIntelligenceAgent(provider, metrics, use_mock=True)
    recommender = RecommendationAgent(metrics, use_mock=True)

    candidates = discovery.find_candidates(product_type, country)
    evaluated = []
    for product in candidates:
        asin = product["asin"]
        all_reviews = provider.get_reviews(asin, country)
        review_result = review_agent.analyze(asin, country, product_title=product["title"])
        pricing_result = price_rating.analyze(asin, country, recent_reviews=all_reviews)
        scores = compute_score(product, review_result, pricing_result)
        evaluated.append({"product": product, "reviews": review_result, "pricing": pricing_result, "scores": scores})

    recommendation = recommender.recommend(evaluated)
    return candidates, evaluated, recommendation


def test_full_pipeline_produces_a_recommendation():
    candidates, evaluated, recommendation = run_pipeline("wireless headphones", "US")
    assert len(candidates) > 0
    assert len(evaluated) == len(candidates)
    assert recommendation["asin"] in [c["asin"] for c in candidates]
    assert recommendation["recommendation"] in ("strong_candidate", "moderate_candidate", "weak_candidate")
    assert 0.0 <= recommendation["confidence"] <= 1.0


def test_every_candidate_has_scores_and_review_evidence():
    _, evaluated, _ = run_pipeline("wireless headphones", "US")
    for c in evaluated:
        assert "overall_score" in c["scores"]
        assert "positive_themes" in c["reviews"]
        assert "negative_themes" in c["reviews"]
        assert "tokens" in c["reviews"]


def test_different_country_also_works():
    candidates, evaluated, recommendation = run_pipeline("headphones", "CA")
    assert len(candidates) > 0
    assert recommendation["asin"] is not None


def test_pipeline_handles_empty_country_gracefully():
    """Unknown country returns no candidates — pipeline should not crash."""
    metrics = MetricsClient(enabled=False)
    provider = MockProvider()
    discovery = DiscoveryAgent(provider, metrics)
    candidates = discovery.find_candidates("headphones", "ZZ")
    assert candidates == []
