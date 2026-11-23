"""
Provider interface tests using the in-memory MockProvider.
No Chrome, no CSV files, no network — pure unit tests.
"""
from tests.mock_provider import MockProvider


def test_search_returns_products():
    provider = MockProvider()
    results = provider.search_products("wireless headphones", "US")
    assert len(results) > 0
    assert all("asin" in r and "title" in r for r in results)


def test_search_filters_by_country():
    provider = MockProvider()
    us_results = provider.search_products("wireless headphones", "US")
    ca_results = provider.search_products("wireless headphones", "CA")
    us_asins = {r["asin"] for r in us_results}
    ca_asins = {r["asin"] for r in ca_results}
    assert us_asins.isdisjoint(ca_asins)


def test_search_respects_max_results():
    provider = MockProvider()
    results = provider.search_products("wireless headphones", "US", max_results=2)
    assert len(results) <= 2


def test_unknown_country_returns_empty():
    provider = MockProvider()
    results = provider.search_products("headphones", "ZZ")
    assert results == []


def test_products_have_required_fields():
    provider = MockProvider()
    results = provider.search_products("headphones", "US")
    for p in results:
        assert "asin" in p
        assert "title" in p
        assert "price" in p
        assert "currency" in p
        assert "rating" in p
        assert "review_count" in p


def test_get_product_returns_matching_asin():
    provider = MockProvider()
    product = provider.get_product("B09TEST001", "US")
    assert product is not None
    assert product["asin"] == "B09TEST001"
    assert product["title"] == "ProMax Wireless Headphones"


def test_get_product_unknown_asin_returns_none():
    provider = MockProvider()
    assert provider.get_product("NOT-A-REAL-ASIN", "US") is None


def test_get_reviews_returns_list():
    provider = MockProvider()
    reviews = provider.get_reviews("B09TEST001", "US")
    assert len(reviews) == 3
    assert all("rating" in r and "text" in r and "date" in r for r in reviews)


def test_get_price_history_returns_data():
    provider = MockProvider()
    history = provider.get_price_history("B09TEST001", "US")
    assert len(history) > 0
    assert history[0]["price"] == 49.99


def test_search_results_are_deterministic():
    a = MockProvider().search_products("headphones", "US")
    b = MockProvider().search_products("headphones", "US")
    assert a == b
