"""Distinguish upstream error pages from an empty product search."""
from unittest.mock import Mock

import pytest

from product_intelligence.providers.amazon import AmazonAccessError, AmazonProvider, check_search_page


def test_persistent_amazon_error_page_stops_after_one_retry(monkeypatch):
    driver = Mock()
    driver.title = "Sorry! Something went wrong!"
    provider = AmazonProvider()
    monkeypatch.setattr(provider, "_get_driver", lambda: driver)
    monkeypatch.setattr("product_intelligence.providers.amazon.time.sleep", lambda seconds: None)
    try:
        with pytest.raises(AmazonAccessError, match="amazon.com returned") as error:
            provider.search_products("bluetooth speaker", "US")
        assert error.value.code == "amazon_error_page"
        assert driver.get.call_count == 2
        assert provider.page_stats["search_pages"] == 2
    finally:
        provider.close()


def test_verification_challenge_stops_collection():
    driver = Mock()
    driver.title = "Robot Check"
    with pytest.raises(AmazonAccessError) as error:
        check_search_page(driver, "US")
    assert error.value.code == "amazon_verification_required"


def test_transient_error_page_recovers_on_next_attempt(monkeypatch):
    from unittest.mock import PropertyMock
    from selenium.webdriver.support.ui import WebDriverWait

    driver = Mock()
    type(driver).title = PropertyMock(side_effect=["Sorry! Something went wrong!",
                                                  "Sorry! Something went wrong!",
                                                  "Amazon.com : speaker"])
    driver.find_elements.return_value = []
    provider = AmazonProvider()
    monkeypatch.setattr(provider, "_get_driver", lambda: driver)
    monkeypatch.setattr(provider, "_extract_search_tiles", lambda *args: [{"asin": "TEST"}])
    monkeypatch.setattr("product_intelligence.providers.amazon.time.sleep", lambda seconds: None)
    monkeypatch.setattr(WebDriverWait, "until", lambda *args: True)
    assert provider.search_products("speaker", "US") == [{"asin": "TEST"}]
    assert driver.get.call_count == 2


def test_regular_search_is_not_classified_as_error():
    driver = Mock()
    driver.title = "Amazon.com : bluetooth speaker"
    driver.find_elements.return_value = []
    check_search_page(driver, "US")


def test_error_stream_offers_explicit_marketplace_switch(monkeypatch):
    import app
    from product_intelligence.observability.metrics import MetricsClient

    monkeypatch.setattr(app.daily_cache, "get_cached_result", lambda *args: None)
    monkeypatch.setattr(app, "MetricsClient", lambda: MetricsClient(enabled=False))

    def fail(*args, **kwargs):
        raise AmazonAccessError("Amazon returned its error page", "amazon_error_page")

    monkeypatch.setattr(app.DiscoveryAgent, "find_candidates", fail)
    response = app.app.test_client().post("/analyze", json={"product": "speakers", "country": "US"})
    text = response.get_data(as_text=True)
    assert '"code": "amazon_error_page"' in text
    assert '"country": "US"' in text
    assert '"alternative_country": "IN"' in text
    assert '"incomplete": true' in text
