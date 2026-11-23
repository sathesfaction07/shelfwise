"""
Tests for the tiered Discovery Agent candidate selection:
trending -> best sellers -> guarded fallback (negative-heavy excluded).

Uses a stub provider so each tier can be exercised deterministically
without touching CSV data or the network.
"""
from datetime import date, timedelta

from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.observability.metrics import MetricsClient
from product_intelligence.providers.base import ProductDataProvider


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _review(asin, rid, rating, days_ago):
    return {
        "asin": asin, "review_id": rid, "rating": rating,
        "title": "t", "text": "some review text here",
        "date": _days_ago(days_ago), "verified_purchase": True, "helpful_votes": 1,
    }


class StubProvider(ProductDataProvider):
    """Products + reviews injected per test."""

    def __init__(self, products, reviews_by_asin):
        self._products = products
        self._reviews_by_asin = reviews_by_asin

    def search_products(self, product_type, country, max_results=5):
        return self._products[:max_results]

    def get_product(self, asin, country):
        return next((p for p in self._products if p["asin"] == asin), None)

    def get_reviews(self, asin, country):
        return list(self._reviews_by_asin.get(asin, []))

    def get_price_history(self, asin, country):
        return []


def _agent(provider):
    return DiscoveryAgent(provider, MetricsClient(enabled=False))


def test_trending_products_rank_first():
    """A product with heavy recent review activity outranks a higher-rated
    one with no recent reviews."""
    products = [
        {
            "asin": "OLD",
            "title": "Old Favorite",
            "price": 50.0,
            "rating": 4.9,
            "review_count": 20000,
        },
        {"asin": "HOT", "title": "Hot Newcomer", "price": 45.0, "rating": 4.2, "review_count": 300},
    ]
    reviews = {
        # HOT: 5 recent reviews, avg 4.6 -> trending
        "HOT": [_review("HOT", f"r{i}", 5 if i % 2 == 0 else 4, days_ago=i * 10) for i in range(5)],
        # OLD: reviews exist but are all outside the 90-day window
        "OLD": [_review("OLD", f"o{i}", 5, days_ago=200 + i) for i in range(5)],
    }
    candidates = _agent(StubProvider(products, reviews)).find_candidates("widget", "US")

    assert candidates[0]["asin"] == "HOT"
    assert candidates[0]["discovery_tier"] == "trending"
    # OLD still shows up, but as a best seller (4.9 stars, huge review count)
    old = next(c for c in candidates if c["asin"] == "OLD")
    assert old["discovery_tier"] == "best_seller"


def test_bestsellers_fill_when_nothing_trends():
    """No recent reviews anywhere -> best sellers ranked by rating x volume."""
    products = [
        {"asin": "SMALL", "title": "Niche 5-star", "price": 10.0, "rating": 5.0, "review_count": 3},
        {
            "asin": "BIG",
            "title": "Popular 4.6-star",
            "price": 20.0,
            "rating": 4.6,
            "review_count": 12000,
        },
        {"asin": "MEH", "title": "Mediocre", "price": 5.0, "rating": 3.2, "review_count": 9000},
    ]
    candidates = _agent(StubProvider(products, {})).find_candidates("widget", "US")

    # BIG beats SMALL: 4.6*log10(12001) > 5.0*log10(4)
    assert candidates[0]["asin"] == "BIG"
    assert candidates[0]["discovery_tier"] == "best_seller"
    # MEH (3.2 stars) can't be a best seller; it arrives via fallback
    meh = next(c for c in candidates if c["asin"] == "MEH")
    assert meh["discovery_tier"] == "fallback"


def test_fallback_excludes_negative_heavy_products():
    """A product whose recent reviews are mostly negative must NOT appear,
    even when it's the only option."""
    products = [
        {"asin": "BAD", "title": "Broken Thing", "price": 15.0, "rating": 3.0, "review_count": 50},
    ]
    reviews = {
        # 2 recent reviews (below trending minimum of 3), both 1-star -> 100% negative
        "BAD": [_review("BAD", "b1", 1, 5), _review("BAD", "b2", 1, 15)],
    }
    candidates = _agent(StubProvider(products, reviews)).find_candidates("widget", "US")
    assert candidates == []


def test_negative_share_below_threshold_is_allowed():
    """Products with SOME negative reviews (below the threshold) still pass."""
    products = [
        {"asin": "OK", "title": "Decent Thing", "price": 15.0, "rating": 3.5, "review_count": 50},
    ]
    reviews = {
        # 1 negative out of 10 recent = 10% negative, below the 30% default
        "OK": [_review("OK", f"k{i}", 1 if i == 0 else 4, days_ago=i * 5) for i in range(10)],
    }
    candidates = _agent(StubProvider(products, reviews)).find_candidates("widget", "US")
    assert len(candidates) == 1
    assert candidates[0]["asin"] == "OK"


def test_every_candidate_carries_a_tier_marker():
    products = [
        {"asin": f"P{i}", "title": f"Product {i}", "price": 10.0 + i, "rating": 4.0 + i * 0.1,
         "review_count": 100 * (i + 1)}
        for i in range(5)
    ]
    candidates = _agent(StubProvider(products, {})).find_candidates("widget", "US")
    assert len(candidates) > 0
    assert all(
        c.get("discovery_tier") in ("trending", "best_seller", "fallback") for c in candidates
    )


def test_respects_max_products_cap():
    products = [
        {
            "asin": f"P{i}",
            "title": f"Product {i}",
            "price": 10.0,
            "rating": 4.5,
            "review_count": 1000,
        }
        for i in range(10)
    ]
    candidates = _agent(StubProvider(products, {})).find_candidates("widget", "US", max_products=3)
    assert len(candidates) == 3
