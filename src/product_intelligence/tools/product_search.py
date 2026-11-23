"""Thin tool wrapper around provider.search_products - used by the Lambda handler and AgentCore Gateway tool."""
from product_intelligence import config


def search_products(product_type: str, country: str, provider=None, max_results: int | None = None) -> list[dict]:
    provider = provider or _default_provider()
    return provider.search_products(product_type, country, max_results=max_results or config.MAX_PRODUCTS)


def _default_provider():
    from product_intelligence.providers.amazon import AmazonProvider
    return AmazonProvider()
