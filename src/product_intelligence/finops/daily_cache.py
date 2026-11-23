"""
Daily result cache (ported from bedrock-review-intel).

Caches full pipeline results per (product_type, country) for the current
calendar day under .cache/. Same-day repeat searches are served instantly
with zero LLM spend; stale files from previous days are purged on access.
"""
import json
import os
import re
from datetime import date

from product_intelligence.observability.logging import log_event

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".cache")


def _cache_path() -> str:
    return os.path.join(CACHE_DIR, f"results_{date.today().isoformat()}.json")


def _safe_key(product_type: str, country: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", product_type.strip().lower())
    return f"{normalized}__{country.upper()}"


def _purge_old_caches():
    """Deletes cache files that aren't for today."""
    if not os.path.isdir(CACHE_DIR):
        return
    today_name = os.path.basename(_cache_path())
    for name in os.listdir(CACHE_DIR):
        if name.startswith("results_") and name != today_name:
            try:
                os.remove(os.path.join(CACHE_DIR, name))
                log_event("cache_purged", file=name)
            except OSError:
                pass


def get_cached_result(product_type: str, country: str) -> dict | None:
    """Returns today's cached pipeline result for this search, or None."""
    _purge_old_caches()
    path = _cache_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    hit = data.get(_safe_key(product_type, country))
    if hit:
        log_event("cache_hit", product_type=product_type, country=country)
    return hit


def save_result(product_type: str, country: str, result: dict):
    """Stores a completed pipeline result in today's cache file."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    data[_safe_key(product_type, country)] = result
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, default=str)
    log_event("cache_saved", product_type=product_type, country=country)
