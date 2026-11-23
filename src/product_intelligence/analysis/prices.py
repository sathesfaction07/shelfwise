"""
Deterministic price analysis. No LLM involved - see spec section 21.
"""


def analyze_price_history(price_history: list[dict]) -> dict:
    """
    price_history: list of {asin, date, country, price} sorted or unsorted, ISO date strings.
    Returns current/previous/min/max/avg price and percent change.
    If there are fewer than 2 observations, trend fields are marked unavailable
    rather than invented (see spec section 62 - No-Data Rule).
    """
    if not price_history:
        return {
            "current_price": None,
            "previous_price": None,
            "min_price": None,
            "max_price": None,
            "avg_price": None,
            "price_change_percent": None,
            "trend_available": False,
        }

    sorted_prices = sorted(price_history, key=lambda p: p["date"])
    prices = [p["price"] for p in sorted_prices]

    current_price = prices[-1]
    previous_price = prices[-2] if len(prices) >= 2 else None

    price_change_percent = None
    if previous_price is not None and previous_price != 0:
        price_change_percent = round((current_price - previous_price) / previous_price * 100, 2)

    return {
        "current_price": current_price,
        "previous_price": previous_price,
        "min_price": min(prices),
        "max_price": max(prices),
        "avg_price": round(sum(prices) / len(prices), 2),
        "price_change_percent": price_change_percent,
        "trend_available": previous_price is not None,
    }
