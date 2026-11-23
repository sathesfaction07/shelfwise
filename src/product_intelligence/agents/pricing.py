"""
Price & Rating Agent - deterministic quantitative analysis only.
No LLM call (spec section 7): arithmetic belongs to Python.
"""
from product_intelligence.analysis.prices import analyze_price_history
from product_intelligence.analysis.ratings import rating_change
from product_intelligence.observability.logging import log_event
from product_intelligence.observability.metrics import MetricsClient


class PriceRatingAgent:
    def __init__(self, provider, metrics_client: MetricsClient | None = None):
        self.provider = provider
        self.metrics = metrics_client or MetricsClient()

    def analyze(self, asin: str, country: str, recent_reviews: list[dict]) -> dict:
        price_history = self.provider.get_price_history(asin, country)
        price_trend = analyze_price_history(price_history)
        rating_trend = rating_change(recent_reviews)

        result = {
            "asin": asin,
            "current_price": price_trend["current_price"],
            "previous_price": price_trend["previous_price"],
            "price_change_percent": price_trend["price_change_percent"],
            "price_trend_available": price_trend["trend_available"],
            "current_rating": rating_trend["current_rating"],
            "previous_rating": rating_trend["previous_rating"],
            "rating_change": rating_trend["rating_change"],
            "rating_trend_available": rating_trend["trend_available"],
            "monthly_rating_trend": rating_trend["monthly_trend"],
        }

        log_event("price_rating_analysis", asin=asin, country=country,
                   price_change_percent=result["price_change_percent"],
                   rating_change=result["rating_change"])
        self.metrics.publish("ToolInvocations", 1, dimensions={"AgentName": "price_rating"})

        return result
