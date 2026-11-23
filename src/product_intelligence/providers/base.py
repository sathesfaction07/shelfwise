"""
Provider interface. Agents and tools depend ONLY on this interface, never
on Selenium, requests, or any concrete data-access technology directly.

This is what makes the project reproducible: DemoProvider implements the
same contract as AmazonProvider, so everything above this layer is
identical regardless of where the data actually comes from.
"""
from abc import ABC, abstractmethod


class ProductDataProvider(ABC):
    @abstractmethod
    def search_products(self, product_type: str, country: str, max_results: int = 5) -> list[dict]:
        """Return a list of candidate products: [{asin, title, price, rating}, ...]"""
        raise NotImplementedError

    @abstractmethod
    def get_product(self, asin: str, country: str) -> dict | None:
        """Return a single product's current details, or None if not found."""
        raise NotImplementedError

    @abstractmethod
    def get_reviews(self, asin: str, country: str) -> list[dict]:
        """Return normalized review objects for the given ASIN (unfiltered)."""
        raise NotImplementedError

    @abstractmethod
    def get_price_history(self, asin: str, country: str) -> list[dict]:
        """Return price observations: [{asin, date, country, price}, ...] sorted by date."""
        raise NotImplementedError
