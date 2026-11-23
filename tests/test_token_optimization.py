from product_intelligence.tools.token_optimizer import estimate_token_count, measure_reduction


def test_estimate_token_count_scales_with_length():
    short = estimate_token_count("hello world")
    long = estimate_token_count("hello world " * 50)
    assert long > short


def test_empty_text_returns_zero():
    assert estimate_token_count("") == 0


def test_reduction_is_positive_when_sample_is_smaller():
    raw_reviews = [{"title": "t", "text": "word " * 200} for _ in range(20)]
    sample = {"positive": [{"title": "t", "text": "word " * 200} for _ in range(3)], "negative": []}
    reduction = measure_reduction(raw_reviews, sample)
    assert reduction.raw_estimate > reduction.optimized_estimate
    assert reduction.reduction_percent > 0


def test_reduction_percent_zero_when_nothing_reduced():
    raw_reviews = [{"title": "t", "text": "same text"}]
    sample = {"positive": [{"title": "t", "text": "same text"}], "negative": []}
    reduction = measure_reduction(raw_reviews, sample)
    assert reduction.reduction_percent == 0.0
