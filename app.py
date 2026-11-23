"""
app.py — Flask web server for Julie.

Endpoints:
  GET  /          — Serves the frontend (static/index.html)
  GET  /countries — Supported marketplaces + Amazon domain map
  GET  /budget    — Today's estimated LLM spend vs daily ceiling
  POST /analyze   — SSE streaming endpoint: runs the 4-agent pipeline
                    against the LIVE country-specific Amazon retail site
                    and streams progress + agent dialogue in real time.

LLM calls go to Amazon Bedrock (Claude Haiku 4.5) — ensure AWS
credentials are configured (e.g. AWS_PROFILE=bedrock-deployer).
"""

import json
import os
import sys
import queue
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, Response, request, send_from_directory

from product_intelligence.providers.amazon import AmazonProvider, AmazonAccessError
from product_intelligence.agents.discovery import DiscoveryAgent
from product_intelligence.agents.pricing import PriceRatingAgent
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.agents.recommendation import RecommendationAgent, compute_score
from product_intelligence.observability.metrics import MetricsClient
from product_intelligence.finops import daily_cache
from product_intelligence.finops.budget import daily_budget
from product_intelligence import config

app = Flask(__name__, static_folder="static")
_analysis_lock = threading.Lock()
HEARTBEAT_SECONDS = 2


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/budget")
def budget():
    """Today's LLM spend vs the configured daily ceiling."""
    limit = config.DAILY_COST_BUDGET_USD
    spent = daily_budget.spent_today_usd
    return {
        "spent_today_usd": round(spent, 6),
        "daily_limit_usd": limit,
        "utilization_percent": round(spent / limit * 100, 2) if limit else 0,
        "calls_today": daily_budget.calls_today,
    }


@app.route("/countries")
def countries():
    """Return supported marketplaces for the dropdown."""
    return {
        "countries": list(config.AMAZON_DOMAINS.keys()),
        "domains": config.AMAZON_DOMAINS,
        "currencies": config.DEFAULT_CURRENCY,
    }


@app.route("/analyze", methods=["POST"])
def analyze():
    """SSE streaming endpoint. Live 4-agent pipeline with agent dialogue."""
    data = request.get_json()
    product = (data.get("product") or "").strip()
    country = (data.get("country") or "US").strip()
    min_rating = data.get("min_rating")
    max_price = data.get("max_price")

    # Review window: optional, default 90; must be within [7, 366] days.
    raw_window = data.get("review_window_days")
    if raw_window in (None, ""):
        window_days = config.REVIEW_WINDOW_DAYS
    else:
        try:
            window_days = int(raw_window)
        except (TypeError, ValueError):
            return Response("review_window_days must be a whole number", status=400)
        if window_days < 7 or window_days > 366:
            return Response("review_window_days must be between 7 and 366", status=400)

    if not product:
        return Response("No product type provided", status=400)
    if not _analysis_lock.acquire(blocking=False):
        return Response("An analysis is already running. Wait for it to finish before starting another.", status=409)
    events = queue.Queue()
    cancelled = threading.Event()

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    def dialogue(speaker, message):
        """Agent-to-agent dialogue line for the Agent Activity feed."""
        return sse("dialogue", {"agent": speaker, "message": message})

    def generate():
        domain = config.AMAZON_DOMAINS.get(country.upper(), "amazon.com")
        currency = config.DEFAULT_CURRENCY.get(country.upper(), "$")

        # ── Cache-first: same-day repeat searches cost $0 ──────────────
        cache_key = f"v3|{config.AWS_MODE}|{config.MODEL_ID}|{config.MODEL_ID_TOOLS}|{product}|w{window_days}"
        cached = daily_cache.get_cached_result(cache_key, country)
        if cached and min_rating is None and max_price is None:
            if cached.get("scrape"):
                yield sse("scrape", {**cached["scrape"], "cached": True})
            yield dialogue("System", f"Found today's analysis of '{product}' ({country}) in cache — serving instantly at zero cost.")
            yield sse("candidates", {"products": cached["candidates"]})
            for stage in ("discovery", "review", "pricing", "recommendation"):
                yield sse("progress", {"stage": stage, "status": "done", "message": f"{stage} (cached)"})
            for entry in cached["evaluated"]:
                yield sse("product_result", entry)
            yield sse("recommendation", cached["recommendation"])
            yield sse("done", {"analyzed": len(cached["evaluated"]), "cached": True})
            return

        metrics = MetricsClient()
        provider = AmazonProvider(headless=True)
        provider.progress_callback = lambda stats: events.put(sse("scrape", stats))

        discovery = DiscoveryAgent(provider, metrics)
        price_rating = PriceRatingAgent(provider, metrics)
        review_agent = ReviewIntelligenceAgent(provider, metrics)
        recommender = RecommendationAgent(metrics)
        # Interactive results retain measured evidence even when local prose fails.
        # Strict integration checks keep these options disabled.
        review_agent.allow_partial = True
        recommender.allow_partial = True

        try:
            # ── Stage 1: Discovery ──────────────────────────────────────
            yield sse("progress", {"stage": "discovery", "status": "active",
                                   "message": f"Searching www.{domain} for '{product}'..."})
            yield dialogue("Discovery Agent", f"Searching www.{domain} for '{product}' — pulling the live search results page now. Review window: last {window_days} days.")

            try:
                candidates = discovery.find_candidates(product, country, window_days=window_days)
            except AmazonAccessError as e:
                payload = {"error": str(e), "code": e.code, "country": country}
                if country.upper() == "US":
                    payload["alternative_country"] = "IN"
                yield sse("error", payload)
                return
            except Exception as e:
                yield sse("error", {"error": f"Discovery failed: {e}"})
                return

            unfiltered_count = len(candidates)
            if min_rating is not None:
                candidates = [c for c in candidates if (c.get("rating") or 0) >= float(min_rating)]
            if max_price is not None:
                candidates = [c for c in candidates if (c.get("price") or 0) <= float(max_price)]

            if not candidates:
                message = (
                    "No products match your rating or price filters. Loosen the filters and try again."
                    if unfiltered_count else
                    f"No usable product listings were found on {domain} for this search. "
                    "Try a different query or choose another marketplace."
                )
                yield sse("error", {"error": message})
                return

            skipped = getattr(provider, "last_sponsored_skipped", 0)
            if skipped:
                yield dialogue("Discovery Agent",
                               f"Skipped {skipped} sponsored ad tile{'s' if skipped != 1 else ''} that carried no genuine rating or review data — only organically ranked products move forward.")


            tiers = {}
            for c in candidates:
                tiers[c.get("discovery_tier", "match")] = tiers.get(c.get("discovery_tier", "match"), 0) + 1
            tier_summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in tiers.items())
            yield dialogue("Discovery Agent", f"Found {len(candidates)} candidates ({tier_summary}). Handing the ASINs to the Review Intelligence Agent.")

            yield sse("candidates", {"products": candidates})
            yield sse("progress", {"stage": "discovery", "status": "done",
                                   "message": f"Found {len(candidates)} candidates"})

            # ── Speed: load ALL product pages in parallel browser tabs ──
            asins = [c["asin"] for c in candidates]
            yield sse("progress", {"stage": "review", "status": "active",
                                   "message": f"Loading {len(asins)} product pages in parallel..."})
            yield dialogue("Review Intelligence Agent", f"Opening {len(asins)} product pages in parallel browser tabs — one wait instead of {len(asins)}.")
            provider.prefetch_reviews(asins, country)

            # ── Stage 2 + 3: Review Intelligence + Price & Rating ───────
            evaluated = []
            for i, prod in enumerate(candidates, 1):
                asin = prod["asin"]

                yield sse("progress", {"stage": "review", "status": "active", "asin": asin,
                                       "message": f"Analyzing reviews for {asin} ({i}/{len(candidates)})..."})
                try:
                    all_reviews = provider.get_reviews(asin, country)
                    review_result = review_agent.analyze(asin, country, product_title=prod["title"],
                                                         window_days=window_days)
                except Exception as e:
                    yield sse("error", {"asin": asin, "error": f"Review analysis failed: {e}"})
                    continue

                n = review_result.get("review_count", 0)
                if review_result.get("analysis_error"):
                    yield sse("warning", {"asin": asin, "error": "AI summary unavailable; review statistics retained."})
                pos = review_result.get("positive_percentage")
                if n and review_result.get("analysis_error"):
                    yield dialogue("Review Intelligence Agent",
                                   f"{asin}: statistics retained from {n} recent reviews; AI summary unavailable.")
                elif n:
                    sentiment = f"{pos}% positive" if pos is not None else "sentiment mixed"
                    themes = ", ".join(review_result.get("positive_themes", [])[:3]) or "no clear themes"
                    yield dialogue("Review Intelligence Agent",
                                   f"{asin}: {n} recent reviews, {sentiment}. Customers highlight: {themes}. The configured model summarized the evidence.")
                else:
                    yield dialogue("Review Intelligence Agent",
                                   f"{asin}: no recent reviews found on the product page — flagging low confidence for this one.")

                yield sse("progress", {"stage": "pricing", "status": "active", "asin": asin,
                                       "message": f"Checking price & rating for {asin}..."})
                try:
                    pricing_result = price_rating.analyze(asin, country, recent_reviews=all_reviews)
                except Exception as e:
                    yield sse("error", {"asin": asin, "error": f"Price analysis failed: {e}"})
                    continue

                price = pricing_result.get("current_price")
                scraped_cur = prod.get("currency") or currency
                yield dialogue("Price & Rating Agent",
                               f"{asin}: current price {scraped_cur + str(price) if price is not None else 'unavailable'}. "
                               f"Live mode sees today's price only, so no trend math — passing what I have to the Recommendation Agent.")

                scores = compute_score(prod, review_result, pricing_result)
                entry = {"product": prod, "reviews": review_result,
                         "pricing": pricing_result, "scores": scores}
                evaluated.append(entry)
                yield sse("product_result", entry)

            yield sse("progress", {"stage": "review", "status": "done", "message": "Review analysis complete"})
            yield sse("progress", {"stage": "pricing", "status": "done", "message": "Price check complete"})

            if not evaluated:
                yield sse("error", {"error": "No products could be analyzed."})
                return

            # ── Stage 4: Recommendation ─────────────────────────────────
            yield sse("progress", {"stage": "recommendation", "status": "active",
                                   "message": "Weighing all evidence..."})
            best = max(evaluated, key=lambda e: e["scores"]["overall_score"])
            yield dialogue("Recommendation Agent",
                           f"Comparing {len(evaluated)} scored candidates — {best['product']['asin']} leads on combined review, rating and price signals. Asking the configured model for the final verdict.")
            try:
                recommendation = recommender.recommend(evaluated)
            except Exception as e:
                yield sse("error", {"error": f"Recommendation failed: {e}"})
                return

            yield dialogue("Recommendation Agent",
                           f"Verdict: {recommendation['asin']} ({recommendation.get('recommendation', '').replace('_', ' ')}, "
                           + (f"{round(float(recommendation['confidence']) * 100)}% confidence)."
                              if recommendation.get("confidence") is not None else "confidence not estimated)."))
            yield sse("progress", {"stage": "recommendation", "status": "done", "message": "Recommendation ready"})
            yield sse("recommendation", recommendation)

            if min_rating is None and max_price is None:
                daily_cache.save_result(cache_key, country, {
                    "candidates": candidates,
                    "evaluated": evaluated,
                    "recommendation": recommendation,
                    "scrape": getattr(provider, "page_stats", {}),
                })

            yield sse("done", {"analyzed": len(evaluated)})

        finally:
            # Always release the Chrome instance, even on mid-stream errors
            try:
                provider.close()
            except Exception:
                pass

    def work():
        try:
            for event in generate():
                if cancelled.is_set():
                    break
                events.put(event)
        except Exception as exc:
            events.put(sse("error", {"error": f"Analysis failed: {exc}"}))
        finally:
            _analysis_lock.release()
            events.put(None)

    def stream():
        started = time.monotonic()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        finished = False
        try:
            while True:
                try:
                    event = events.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    if not finished:
                        yield sse("heartbeat", {"elapsed_seconds": int(time.monotonic() - started)})
                    continue
                if event is None:
                    if not finished:
                        yield sse("done", {"incomplete": True})
                    break
                if event.startswith("event: done\n"):
                    finished = True
                yield event
        finally:
            cancelled.set()

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print("\n--- Julie ---")
    print(f"    http://localhost:{port}")
    print(f"    Live data: country-specific Amazon retail sites")
    print(f"    Press Ctrl+C to stop\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
