"""
Mock provider for tests — lightweight in-memory implementation of ProductDataProvider.
No CSV files, no Chrome, no network. Deterministic fixture data for unit tests.
"""
from product_intelligence.providers.base import ProductDataProvider

_CATALOG = {
    "US": [
        {
            "asin": "B09TEST001",
            "title": "ProMax Wireless Headphones",
            "price": 49.99,
            "currency": "$",
            "rating": 4.5,
            "review_count": 1200,
            "sponsored": False,
            "monthly_purchases": 500,
        },
        {
            "asin": "B09TEST002",
            "title": "BassBoost Over-Ear Headphones",
            "price": 79.99,
            "currency": "$",
            "rating": 4.2,
            "review_count": 850,
            "sponsored": False,
            "monthly_purchases": 200,
        },
        {
            "asin": "B09TEST003",
            "title": "AirLite Earbuds Pro",
            "price": 34.99,
            "currency": "$",
            "rating": 4.7,
            "review_count": 3200,
            "sponsored": False,
            "monthly_purchases": 1000,
        },
        {
            "asin": "B09TEST004",
            "title": "StudioPro Noise-Cancelling",
            "price": 129.99,
            "currency": "$",
            "rating": 4.3,
            "review_count": 620,
            "sponsored": False,
            "monthly_purchases": 150,
        },
        {
            "asin": "B09TEST005",
            "title": "BudgetBuds Wireless",
            "price": 19.99,
            "currency": "$",
            "rating": 3.8,
            "review_count": 4500,
            "sponsored": False,
            "monthly_purchases": 2000,
        },
    ],
    "CA": [
        {
            "asin": "B09CA001",
            "title": "MaplePods Wireless",
            "price": 59.99,
            "currency": "C$",
            "rating": 4.4,
            "review_count": 300,
            "sponsored": False,
            "monthly_purchases": 100,
        },
        {
            "asin": "B09CA002",
            "title": "NorthSound Over-Ear",
            "price": 89.99,
            "currency": "C$",
            "rating": 4.1,
            "review_count": 210,
            "sponsored": False,
            "monthly_purchases": 80,
        },
    ],
}

_REVIEWS = {
    "B09TEST001": [
        {
            "asin": "B09TEST001",
            "review_id": "R001A",
            "rating": 5,
            "title": "Great sound quality",
            "text": (
                "These headphones have excellent bass and clear highs. "
                "Very comfortable for long sessions."
            ),
            "date": "2026-07-20",
            "verified_purchase": True,
            "helpful_votes": 12,
        },
        {
            "asin": "B09TEST001",
            "review_id": "R001B",
            "rating": 4,
            "title": "Good value",
            "text": "Solid build quality for the price. Battery life could be better.",
            "date": "2026-07-15",
            "verified_purchase": True,
            "helpful_votes": 5,
        },
        {
            "asin": "B09TEST001",
            "review_id": "R001C",
            "rating": 2,
            "title": "Connection drops",
            "text": "Bluetooth keeps disconnecting. Returned after a week.",
            "date": "2026-06-30",
            "verified_purchase": True,
            "helpful_votes": 8,
        },
    ],
    "B09TEST002": [
        {
            "asin": "B09TEST002",
            "review_id": "R002A",
            "rating": 5,
            "title": "Amazing bass",
            "text": "The over-ear design gives incredible bass response. Love them.",
            "date": "2026-07-18",
            "verified_purchase": True,
            "helpful_votes": 3,
        },
        {
            "asin": "B09TEST002",
            "review_id": "R002B",
            "rating": 3,
            "title": "Bulky",
            "text": "Sound is good but they're too heavy for commuting.",
            "date": "2026-07-10",
            "verified_purchase": True,
            "helpful_votes": 2,
        },
    ],
    "B09TEST003": [
        {
            "asin": "B09TEST003",
            "review_id": "R003A",
            "rating": 5,
            "title": "Perfect fit",
            "text": "Lightweight, great noise isolation, stays in during workouts.",
            "date": "2026-07-22",
            "verified_purchase": True,
            "helpful_votes": 20,
        },
        {
            "asin": "B09TEST003",
            "review_id": "R003B",
            "rating": 5,
            "title": "Best in class",
            "text": "Replaced my expensive brand earbuds. These are just as good.",
            "date": "2026-07-19",
            "verified_purchase": True,
            "helpful_votes": 15,
        },
        {
            "asin": "B09TEST003",
            "review_id": "R003C",
            "rating": 4,
            "title": "Almost perfect",
            "text": "Great earbuds. Only issue is the case is a fingerprint magnet.",
            "date": "2026-07-05",
            "verified_purchase": True,
            "helpful_votes": 4,
        },
    ],
    "B09TEST004": [
        {
            "asin": "B09TEST004",
            "review_id": "R004A",
            "rating": 4,
            "title": "Good ANC",
            "text": "Noise cancellation works well on flights. Comfortable too.",
            "date": "2026-07-12",
            "verified_purchase": True,
            "helpful_votes": 6,
        },
    ],
    "B09TEST005": [
        {
            "asin": "B09TEST005",
            "review_id": "R005A",
            "rating": 4,
            "title": "Budget champion",
            "text": "You can't beat this for the price. Decent sound, decent battery.",
            "date": "2026-07-25",
            "verified_purchase": True,
            "helpful_votes": 30,
        },
        {
            "asin": "B09TEST005",
            "review_id": "R005B",
            "rating": 1,
            "title": "Broke in a week",
            "text": "Left earbud stopped working after 6 days. Cheap build.",
            "date": "2026-07-01",
            "verified_purchase": True,
            "helpful_votes": 45,
        },
    ],
    "B09CA001": [
        {
            "asin": "B09CA001",
            "review_id": "RCA01",
            "rating": 5,
            "title": "Eh, pretty good",
            "text": "Great product, fast shipping to Toronto.",
            "date": "2026-07-20",
            "verified_purchase": True,
            "helpful_votes": 2,
        },
    ],
}


class MockProvider(ProductDataProvider):
    """In-memory test provider. No files, no network, deterministic."""

    def search_products(self, product_type: str, country: str, max_results: int = 5) -> list[dict]:
        products = _CATALOG.get(country.upper(), [])
        return products[:max_results]

    def get_product(self, asin: str, country: str) -> dict | None:
        for products in _CATALOG.values():
            for p in products:
                if p["asin"] == asin:
                    return dict(p)
        return None

    def get_reviews(self, asin: str, country: str) -> list[dict]:
        return list(_REVIEWS.get(asin, []))

    def get_price_history(self, asin: str, country: str) -> list[dict]:
        product = self.get_product(asin, country)
        if not product or product.get("price") is None:
            return []
        return [{"asin": asin, "date": "2026-08-05", "country": country, "price": product["price"]}]

    def prefetch_reviews(self, asins: list[str], country: str) -> None:
        pass  # no-op for mock

    def get_rating_histogram(self, asin: str) -> dict | None:
        return {"histogram": {5: 50, 4: 25, 3: 10, 2: 8, 1: 7}, "total_ratings": 1000}
