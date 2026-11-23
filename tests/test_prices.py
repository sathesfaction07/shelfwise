from product_intelligence.analysis.prices import analyze_price_history


def test_price_change_known_values():
    history = [
        {"asin": "B001", "date": "2026-06-01", "country": "US", "price": 100.0},
        {"asin": "B001", "date": "2026-07-01", "country": "US", "price": 80.0},
    ]
    result = analyze_price_history(history)
    assert result["current_price"] == 80.0
    assert result["previous_price"] == 100.0
    assert result["price_change_percent"] == -20.0


def test_price_history_unsorted_input_still_correct():
    history = [
        {"asin": "B001", "date": "2026-07-01", "country": "US", "price": 80.0},
        {"asin": "B001", "date": "2026-06-01", "country": "US", "price": 100.0},
    ]
    result = analyze_price_history(history)
    assert result["current_price"] == 80.0


def test_empty_price_history_marks_unavailable_not_invented():
    result = analyze_price_history([])
    assert result["current_price"] is None
    assert result["trend_available"] is False


def test_single_price_point_has_no_trend():
    history = [{"asin": "B001", "date": "2026-07-01", "country": "US", "price": 80.0}]
    result = analyze_price_history(history)
    assert result["current_price"] == 80.0
    assert result["previous_price"] is None
    assert result["trend_available"] is False


def test_min_max_avg():
    history = [
        {"asin": "B001", "date": "2026-05-01", "country": "US", "price": 100.0},
        {"asin": "B001", "date": "2026-06-01", "country": "US", "price": 90.0},
        {"asin": "B001", "date": "2026-07-01", "country": "US", "price": 80.0},
    ]
    result = analyze_price_history(history)
    assert result["min_price"] == 80.0
    assert result["max_price"] == 100.0
    assert result["avg_price"] == 90.0
