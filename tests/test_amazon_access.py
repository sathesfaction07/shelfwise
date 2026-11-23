"""Distinguish upstream error pages from an empty product search."""
from unittest.mock import Mock

import pytest

from product_intelligence.providers.amazon import (
    AmazonAccessError,
    AmazonProvider,
    check_search_page,
)


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


class _Node:
    """Minimal stand-in for a Selenium WebElement inside one search-result tile."""

    def __init__(self, text="", attrs=None, children=None):
        self.text = text
        self._attrs = attrs or {}
        self._children = children or {}

    def get_attribute(self, name):
        return self._attrs.get(name)

    def find_element(self, _by, selector):
        if selector not in self._children:
            raise LookupError(selector)  # Selenium raises NoSuchElementException here
        return self._children[selector]

    def find_elements(self, _by, selector):
        return [self._children[selector]] if selector in self._children else []


def _search_page_with_price(price_text):
    tile = _Node(
        attrs={"data-asin": "B0TEST0001", "textContent": ""},
        children={
            "h2 span": _Node(text="Widget"),
            "span.a-price span.a-offscreen": _Node(attrs={"textContent": price_text}),
        },
    )
    page = Mock()
    page.find_elements.side_effect = lambda _by, sel: (
        [tile] if sel == "div[data-component-type='s-search-result']" else []
    )
    return page


@pytest.mark.parametrize(("country", "currency"), [("US", "$"), ("de", "€")])
def test_a_price_without_a_currency_symbol_takes_the_country_default(country, currency):
    """The fallback used to reference an undefined `country`. The NameError was swallowed by
    the price block's `except Exception`, so the price was silently discarded as well."""
    provider = AmazonProvider()
    [product] = provider._extract_search_tiles(_search_page_with_price("38.07"), 5, country)
    assert product["price"] == 38.07
    assert product["currency"] == currency


def test_a_currency_symbol_in_the_text_still_wins_over_the_country_default():
    provider = AmazonProvider()
    [product] = provider._extract_search_tiles(_search_page_with_price("£38.07"), 5, "US")
    assert product["price"] == 38.07
    assert product["currency"] == "£"
