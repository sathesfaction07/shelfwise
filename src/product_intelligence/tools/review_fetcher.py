"""
ReviewFetcher - the pipeline described in spec section 14:
ASIN + country -> review URL -> browser/client -> HTML -> parsed review objects.

For DemoProvider this collapses to a CSV read; for AmazonProvider it's the
real Selenium fetch. Either way, callers only ever see normalized review dicts.
"""


def fetch_reviews(asin: str, country: str, provider=None) -> list[dict]:
    provider = provider or _default_provider()
    return provider.get_reviews(asin, country)


def _default_provider():
    from product_intelligence.providers.amazon import AmazonProvider
    return AmazonProvider()
