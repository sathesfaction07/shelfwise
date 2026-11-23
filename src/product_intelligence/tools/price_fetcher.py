"""Thin tool wrapper around provider.get_price_history."""


def fetch_price_history(asin: str, country: str, provider=None) -> list[dict]:
    provider = provider or _default_provider()
    return provider.get_price_history(asin, country)


def _default_provider():
    from product_intelligence.providers.amazon import AmazonProvider
    return AmazonProvider()
