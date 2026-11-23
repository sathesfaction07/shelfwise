"""
Deterministic rating-trend analysis, derived from review timestamps.
No LLM involved - see spec section 22.
"""
from collections import defaultdict


def monthly_rating_trend(reviews: list[dict]) -> list[dict]:
    """
    Groups reviews by calendar month (YYYY-MM) and returns the average rating
    per month, sorted chronologically. Used to show a rating trend without
    requiring a separate ratings-history dataset.
    """
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in reviews:
        month_key = r["date"][:7]  # "YYYY-MM"
        buckets[month_key].append(r["rating"])

    trend = []
    for month in sorted(buckets.keys()):
        ratings = buckets[month]
        trend.append({
            "month": month,
            "avg_rating": round(sum(ratings) / len(ratings), 2),
            "review_count": len(ratings),
        })
    return trend


def rating_change(reviews: list[dict]) -> dict:
    """
    Returns current vs. previous month average rating and the delta.
    Marks trend unavailable rather than inventing values if there's
    less than two months of data (spec section 62 - No-Data Rule).
    """
    trend = monthly_rating_trend(reviews)
    if len(trend) < 2:
        current = trend[-1]["avg_rating"] if trend else None
        return {
            "current_rating": current,
            "previous_rating": None,
            "rating_change": None,
            "trend_available": False,
            "monthly_trend": trend,
        }

    current_rating = trend[-1]["avg_rating"]
    previous_rating = trend[-2]["avg_rating"]
    return {
        "current_rating": current_rating,
        "previous_rating": previous_rating,
        "rating_change": round(current_rating - previous_rating, 2),
        "trend_available": True,
        "monthly_trend": trend,
    }
