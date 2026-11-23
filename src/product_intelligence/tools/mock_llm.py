"""
Deterministic, keyword-based stand-in for the two LLM calls (review theme
extraction and final recommendation explanation). This exists so the
project can be tested end-to-end with zero AWS credentials and zero cost -
see spec section 45 ("the first successful milestone must require no AWS")
and the project's own emphasis on reproducibility.

This is NOT a substitute for the real model in production - it's a
fallback path used when Bedrock is unavailable (no credentials/model
access) or when --mock is passed explicitly.
"""
import re
from collections import Counter

_STOPWORDS = {
    "the", "is", "a", "an", "and", "for", "to", "of", "in", "on", "it",
    "with", "this", "that", "are", "was", "were", "i", "my", "very", "but",
    "at", "as", "be", "have", "has", "had", "not", "so", "just", "than",
}

_KNOWN_THEMES = [
    "battery life", "sound quality", "comfort", "value for money",
    "bluetooth connectivity", "anc performance", "ear-cup size",
    "cushioning", "durability", "sizing", "fit", "grip",
]


# Generic words that are never useful as a "theme" - product category nouns,
# brand-ish tokens, and filler that crude word-frequency tends to surface.
_THEME_JUNK = {
    "product", "item", "amazon", "purchase", "bought", "buy", "ordered",
    "laptop", "computer", "phone", "device", "internet", "thing", "things",
    "time", "day", "week", "month", "year", "really", "would", "could",
    "much", "than", "they", "them", "there", "when", "what", "like",
    "good", "great", "nice", "well", "work", "works", "working", "worked",
    "use", "using", "used", "user", "still", "even", "only", "also",
    "after", "before", "first", "last", "back", "came", "come", "will",
}


def _find_themes(reviews: list[dict], limit: int, product_title: str = "") -> list[str]:
    """
    Best effort theme extraction without an LLM:
    1. Match known multi-word theme phrases ("battery life", "sound quality").
    2. Fall back to frequent two-word phrases (bigrams) - more descriptive
       than single words and far less likely to be junk.
    Brand/product words from the title (e.g. "acer", "laptop") are excluded.
    """
    text_blob = " ".join(f"{r.get('title', '')} {r.get('text', '')}" for r in reviews).lower()
    found = [theme for theme in _KNOWN_THEMES if theme in text_blob]
    if found:
        return found[:limit]

    title_words = {w.lower() for w in re.findall(r"[a-z']+", product_title.lower()) if len(w) > 2}
    words = re.findall(r"[a-z']+", text_blob)
    keep = [w for w in words
            if w not in _STOPWORDS and w not in _THEME_JUNK
            and w not in title_words and len(w) > 3]

    # Prefer bigrams ("screen quality", "charging speed") over lone words
    bigrams = [f"{a} {b}" for a, b in zip(keep, keep[1:])]
    common_bigrams = [p for p, n in Counter(bigrams).most_common(limit) if n >= 2]
    if common_bigrams:
        return common_bigrams[:limit]

    common = [w for w, n in Counter(keep).most_common(limit) if n >= 2]
    return common[:limit]


def mock_review_summary(product_title: str, stats: dict, sample: dict) -> dict:
    """Stands in for the Review Intelligence Agent's LLM call."""
    positive_themes = _find_themes(sample.get("positive", []), limit=4, product_title=product_title)
    negative_themes = _find_themes(sample.get("negative", []), limit=3, product_title=product_title)

    sentiment = "positive"
    pos_pct = stats.get("positive_percentage") or 0
    if pos_pct < 50:
        sentiment = "negative"
    elif pos_pct < 75:
        sentiment = "mixed"

    summary = (
        f"{product_title}: {stats.get('review_count', 0)} recent reviews, "
        f"{pos_pct}% positive. Overall sentiment is {sentiment}."
    )

    return {
        "positive_themes": positive_themes,
        "negative_themes": negative_themes,
        "recurring_complaints": negative_themes[:2],
        "recurring_strengths": positive_themes[:2],
        "overall_sentiment": sentiment,
        "summary": summary,
        "source": "mock",
    }


def mock_recommendation(candidates_with_scores: list[dict]) -> dict:
    """Stands in for the Recommendation Agent's LLM call."""
    if not candidates_with_scores:
        return {
            "recommendation": "no_candidates",
            "asin": None,
            "reason": "No candidate products were found for this search.",
            "strengths": [],
            "weaknesses": [],
            "confidence": 0.0,
            "source": "mock",
        }

    best = max(candidates_with_scores, key=lambda c: c["overall_score"])
    label = "strong_candidate" if best["overall_score"] >= 0.7 else (
        "moderate_candidate" if best["overall_score"] >= 0.5 else "weak_candidate"
    )

    strengths = []
    weaknesses = []
    if (best.get("positive_percentage") or 0) >= 80:
        strengths.append("predominantly positive recent reviews")
    if (best.get("price_change_percent") or 0) < 0:
        strengths.append("recent price decrease")
    if (best.get("rating_change") or 0) > 0:
        strengths.append("improving rating trend")
    if (best.get("negative_percentage") or 0) >= 15:
        weaknesses.append("notable share of negative reviews")
    if (best.get("rating_change") or 0) < 0:
        weaknesses.append("declining rating trend")

    return {
        "recommendation": label,
        "asin": best["asin"],
        "reason": (
            f"Overall score {best['overall_score']:.2f} based on review sentiment, "
            f"rating, price position, and trend evidence."
        ),
        "strengths": strengths or ["evidence is neutral across factors"],
        "weaknesses": weaknesses or ["no significant weaknesses identified"],
        "confidence": round(min(0.95, best["overall_score"]), 2),
        "source": "mock",
    }
