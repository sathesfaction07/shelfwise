"""Browser startup errors must not be presented as Amazon rate limits."""
import pytest
from selenium.common.exceptions import NoSuchDriverException

from product_intelligence.providers.amazon import AmazonProvider, BrowserSetupError


def test_missing_driver_is_not_an_empty_search(monkeypatch):
    provider = AmazonProvider()
    attempts = []

    def missing_driver():
        attempts.append(1)
        raise NoSuchDriverException("driver cache inaccessible")

    monkeypatch.setattr(provider, "_get_driver", missing_driver)
    with pytest.raises(BrowserSetupError, match="did not reach Amazon"):
        provider.search_products("bluetooth speaker", "US")
    assert len(attempts) == 1


def test_ui_explains_driver_failure(monkeypatch):
    import app

    provider = AmazonProvider()

    def missing_driver():
        raise NoSuchDriverException("driver cache inaccessible")

    monkeypatch.setattr(provider, "_get_driver", missing_driver)
    monkeypatch.setattr(app, "AmazonProvider", lambda **kwargs: provider)
    monkeypatch.setattr(app.daily_cache, "get_cached_result", lambda *args: None)
    response = app.app.test_client().post(
        "/analyze", json={"product": "bluetooth speaker", "country": "US"},
    )
    text = response.get_data(as_text=True)
    assert "event: error" in text
    assert "Chrome could not start" in text
    assert "rate-limiting" not in text


def test_browser_does_not_inherit_server_sockets(monkeypatch):
    from unittest.mock import Mock
    from selenium import webdriver

    chrome = Mock()
    monkeypatch.setattr(webdriver, "Chrome", chrome)
    provider = AmazonProvider()
    provider._get_driver()
    assert chrome.call_args.kwargs["service"].popen_kw["close_fds"] is True
    arguments = chrome.call_args.kwargs["options"].arguments
    assert not any("user-agent" in arg or "imagesEnabled=false" in arg for arg in arguments)
    provider.close()
