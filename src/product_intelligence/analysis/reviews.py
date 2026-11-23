"""
Deterministic review processing: filtering, deduplication, statistics, and
representative sampling. This is the module that does the heavy lifting
described in spec sections 15-17 - Python compresses the evidence before
anything is sent to an LLM.

Positive/negative/neutral definitions (kept explicit per spec section 16):
    positive = rating >= 4
    negative = rating <= 2
    neutral  = rating == 3
"""
from datetime import date, datetime, timedelta

from product_intelligence import config


def filter_recent(reviews: list[dict], window_days: int | None = None) -> list[dict]:
    """Keep only reviews with date >= cutoff (today - window_days)."""
    window_days = window_days if window_days is not None else config.REVIEW_WINDOW_DAYS
    cutoff = date.today() - timedelta(days=window_days)

    recent = []
    for r in reviews:
        try:
            review_date = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            # Invalid/missing date - skip rather than guess (spec section 61)
            continue
        if review_date >= cutoff:
            recent.append(r)
    return recent


def deduplicate(reviews: list[dict]) -> list[dict]:
    """Removes duplicate reviews by review_id, keeping the first occurrence."""
    seen = set()
    deduped = []
    for r in reviews:
        rid = r.get("review_id")
        if rid in seen:
            continue
        seen.add(rid)
        deduped.append(r)
    return deduped


def calculate_statistics(reviews: list[dict]) -> dict:
    """
    Computes rating distribution and positive/negative percentages.
    This is pure arithmetic - never delegate this to the LLM (spec section 3/16).
    """
    if not reviews:
        return {
            "review_count": 0,
            "rating_distribution": {str(i): 0 for i in range(1, 6)},
            "positive_percentage": None,
            "negative_percentage": None,
            "average_rating": None,
        }

    distribution = {str(i): 0 for i in range(1, 6)}
    for r in reviews:
        rating = r["rating"]
        if 1 <= rating <= 5:
            distribution[str(rating)] += 1

    total = len(reviews)
    positive = sum(1 for r in reviews if r["rating"] >= 4)
    negative = sum(1 for r in reviews if r["rating"] <= 2)
    average_rating = sum(r["rating"] for r in reviews) / total

    return {
        "review_count": total,
        "rating_distribution": distribution,
        "positive_percentage": round(positive / total * 100, 1),
        "negative_percentage": round(negative / total * 100, 1),
        "average_rating": round(average_rating, 2),
    }


def sample_representative(reviews: list[dict], caps: dict[int, int] | None = None) -> dict:
    """
    Splits reviews into positive/negative representative samples, capped per
    star rating (spec section 17). Prefers reviews with more helpful_votes
    (a reasonable proxy for "representative") and longer text (more signal).

    Returns {"positive": [...], "negative": [...]} - this reduced set is
    the ONLY review content that should ever reach the LLM prompt.
    """
    caps = caps if caps is not None else config.RATING_SAMPLE_CAPS
    by_rating: dict[int, list[dict]] = {}
    for r in reviews:
        by_rating.setdefault(r["rating"], []).append(r)

    for rating, items in by_rating.items():
        items.sort(key=lambda r: (r.get("helpful_votes", 0), len(r.get("text", ""))), reverse=True)

    sampled_positive = []
    sampled_negative = []
    for rating in (5, 4):
        cap = caps.get(rating, 0)
        sampled_positive.extend(by_rating.get(rating, [])[:cap])
    for rating in (1, 2):
        cap = caps.get(rating, 0)
        sampled_negative.extend(by_rating.get(rating, [])[:cap])

    return {"positive": sampled_positive, "negative": sampled_negative}
