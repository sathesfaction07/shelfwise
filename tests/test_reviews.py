from datetime import date, timedelta

from product_intelligence.analysis.reviews import (
    filter_recent, deduplicate, calculate_statistics, sample_representative,
)


def _review(review_id, rating, days_ago, text="some review text", helpful_votes=0):
    review_date = (date.today() - timedelta(days=days_ago)).isoformat()
    return {
        "review_id": review_id, "rating": rating, "title": "t", "text": text,
        "date": review_date, "verified_purchase": True, "helpful_votes": helpful_votes,
    }


def test_old_review_excluded_by_default_window():
    reviews = [_review("R1", 5, days_ago=200)]
    assert filter_recent(reviews) == []


def test_recent_review_included():
    reviews = [_review("R1", 5, days_ago=10)]
    assert len(filter_recent(reviews)) == 1


def test_filter_respects_custom_window():
    reviews = [_review("R1", 5, days_ago=45)]
    assert filter_recent(reviews, window_days=30) == []
    assert len(filter_recent(reviews, window_days=60)) == 1


def test_invalid_date_is_skipped_not_crashed():
    reviews = [{"review_id": "R1", "rating": 5, "date": "not-a-date"}]
    assert filter_recent(reviews) == []


def test_deduplicate_removes_repeated_ids():
    reviews = [_review("R1", 5, 1), _review("R1", 5, 1), _review("R2", 4, 1)]
    deduped = deduplicate(reviews)
    assert len(deduped) == 2


def test_rating_statistics_known_distribution():
    reviews = [_review("R1", 5, 1), _review("R2", 5, 1), _review("R3", 1, 1)]
    stats = calculate_statistics(reviews)
    assert stats["review_count"] == 3
    assert stats["rating_distribution"]["5"] == 2
    assert stats["rating_distribution"]["1"] == 1
    assert stats["positive_percentage"] == round(2 / 3 * 100, 1)
    assert stats["negative_percentage"] == round(1 / 3 * 100, 1)


def test_statistics_empty_reviews_returns_none_not_zero():
    stats = calculate_statistics([])
    assert stats["review_count"] == 0
    assert stats["positive_percentage"] is None


def test_sample_representative_respects_caps():
    reviews = [_review(f"R{i}", 5, 1) for i in range(20)]
    sample = sample_representative(reviews, caps={5: 3, 4: 5, 3: 3, 2: 5, 1: 10})
    assert len(sample["positive"]) == 3


def test_sample_representative_splits_pos_neg():
    reviews = [_review("R1", 5, 1), _review("R2", 1, 1), _review("R3", 3, 1)]
    sample = sample_representative(reviews)
    assert len(sample["positive"]) == 1
    assert len(sample["negative"]) == 1
    # neutral (3-star) reviews are excluded from both LLM-facing samples
    all_sampled_ids = {r["review_id"] for r in sample["positive"] + sample["negative"]}
    assert "R3" not in all_sampled_ids
