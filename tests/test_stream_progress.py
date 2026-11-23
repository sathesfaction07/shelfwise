"""Streaming must remain observable and end even when a stage fails."""
import threading
import time

import app

from product_intelligence.observability.metrics import MetricsClient
from product_intelligence.providers.amazon import AmazonProvider


def setup_local_stream(monkeypatch):
    monkeypatch.setattr(app.daily_cache, "get_cached_result", lambda *args: None)
    monkeypatch.setattr(app, "MetricsClient", lambda: MetricsClient(enabled=False))
    monkeypatch.setattr(app, "HEARTBEAT_SECONDS", 0.01)


def test_waiting_stage_emits_heartbeats_and_real_page_counts(monkeypatch):
    setup_local_stream(monkeypatch)
    provider = AmazonProvider()
    monkeypatch.setattr(app, "AmazonProvider", lambda **kwargs: provider)

    def discover(*args, **kwargs):
        provider.page_stats.update(search_pages=1, product_pages=8, pages_processed=9)
        provider._report_pages()
        time.sleep(0.08)
        return []

    monkeypatch.setattr(app.DiscoveryAgent, "find_candidates", discover)
    response = app.app.test_client().post("/analyze", json={"product": "speakers"})
    text = response.get_data(as_text=True)
    assert "event: heartbeat" in text
    assert '"product_pages": 8' in text
    assert '"pages_processed": 9' in text
    assert "event: error" in text
    assert text.endswith('event: done\ndata: {"incomplete": true}\n\n')


def test_overlapping_analysis_is_rejected_and_lock_recovers(monkeypatch):
    setup_local_stream(monkeypatch)
    release = threading.Event()
    entered = threading.Event()

    def discover(*args, **kwargs):
        entered.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(app.DiscoveryAgent, "find_candidates", discover)
    first = app.app.test_client().post("/analyze", json={"product": "speakers"})
    try:
        assert entered.wait(timeout=1)
        second = app.app.test_client().post("/analyze", json={"product": "headphones"})
        assert second.status_code == 409
    finally:
        release.set()
        first.get_data()
    assert not app._analysis_lock.locked()


def test_unexpected_worker_error_ends_stream(monkeypatch):
    setup_local_stream(monkeypatch)

    def fail(**kwargs):
        raise RuntimeError("provider initialization failed")

    monkeypatch.setattr(app, "AmazonProvider", fail)
    response = app.app.test_client().post("/analyze", json={"product": "speakers"})
    text = response.get_data(as_text=True)
    assert "provider initialization failed" in text
    assert '"incomplete": true' in text
    assert not app._analysis_lock.locked()


def test_complete_pipeline_stream_reaches_recommendation_and_done(monkeypatch):
    setup_local_stream(monkeypatch)
    from product_intelligence.agents.recommendation import RecommendationAgent
    from product_intelligence.agents.review import ReviewIntelligenceAgent
    from tests.mock_provider import MockProvider

    provider = MockProvider()
    provider.prefetch_reviews = lambda *args: None
    monkeypatch.setattr(app, "AmazonProvider", lambda **kwargs: provider)
    monkeypatch.setattr(app, "ReviewIntelligenceAgent",
                        lambda p, m: ReviewIntelligenceAgent(p, m, use_mock=True))
    monkeypatch.setattr(app, "RecommendationAgent",
                        lambda m: RecommendationAgent(m, use_mock=True))
    monkeypatch.setattr(app.daily_cache, "save_result", lambda *args: None)
    response = app.app.test_client().post(
        "/analyze", json={"product": "headphones", "country": "US", "review_window_days": 366},
    )
    text = response.get_data(as_text=True)
    assert '"stage": "recommendation", "status": "done"' in text
    assert "event: recommendation" in text
    assert "event: error" not in text
    assert '"incomplete": true' not in text
    assert not app._analysis_lock.locked()
