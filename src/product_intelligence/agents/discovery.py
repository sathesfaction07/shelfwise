"""
Discovery Agent - finds candidate products for a product type + marketplace.

No LLM call here: this is pure data + arithmetic (spec section 3's
"separate deterministic work from LLM reasoning").

Candidate selection strategy (in priority order):

  Tier A - TRENDING (last REVIEW_WINDOW_DAYS days):
      Products with real recent review activity: at least
      TRENDING_MIN_RECENT_REVIEWS reviews in the window AND a recent
      average rating >= TRENDING_MIN_AVG_RATING. Ranked by recent review
      volume (momentum), then recent average rating.

  Tier B - BEST SELLERS:
      No (or not enough) trending products. Falls back to proven
      performers: overall rating >= BESTSELLER_MIN_RATING (i.e. 4+ stars),
      ranked by a combination of rating and total review count
      (rating * log10(review_count) - a high-rating product with 10,000
      reviews beats a 5-star product with 3 reviews).

  Tier C - GUARDED FALLBACK:
      Whatever the provider returned (fuzzy/synthetic results included),
      minus anything with significantly negative recent sentiment:
      candidates whose recent negative-review share >=
      DISCOVERY_MAX_NEGATIVE_PERCENT are excluded.

All tiers top up from the next tier if they can't fill MAX_PRODUCTS slots,
and every emitted candidate carries a "discovery_tier" marker so the UI
and logs can show WHY a product was selected.
"""
import math

from product_intelligence import config
from product_intelligence.analysis import reviews as review_analysis
from product_intelligence.observability.logging import log_event
from product_intelligence.observability.metrics import MetricsClient


class DiscoveryAgent:
    def __init__(self, provider, metrics_client: MetricsClient | None = None):
        self.provider = provider
        self.metrics = metrics_client or MetricsClient()

    def find_candidates(self, product_type: str, country: str, max_products: int | None = None,
                        window_days: int | None = None) -> list[dict]:
        max_products = max_products or config.MAX_PRODUCTS

        # Pull a wider pool than we need so the tiers have something to rank.
        pool = self.provider.search_products(
            product_type, country, max_results=config.DISCOVERY_POOL_SIZE
        )

        # Parallel prefetch: providers that support it (AmazonProvider) load
        # every pooled product page concurrently in browser tabs, so the
        # per-product get_reviews() calls below hit an in-memory cache
        # instead of doing N sequential page fetches.
        if hasattr(self.provider, "prefetch_reviews"):
            self.provider.prefetch_reviews([p["asin"] for p in pool], country)

        # Enrich each product with recent-review statistics (pure Python).
        enriched = [self._enrich(p, country, window_days) for p in pool]

        trending = self._rank_trending(enriched)
        bestsellers = self._rank_bestsellers(enriched)
        fallback = self._guarded_fallback(enriched)

        # Assemble: trending first, then best sellers, then guarded fallback.
        # No duplicates; stop once we have max_products.
        candidates: list[dict] = []
        seen: set[str] = set()
        for tier_name, tier in (("trending", trending),
                                ("best_seller", bestsellers),
                                ("fallback", fallback)):
            for item in tier:
                if len(candidates) >= max_products:
                    break
                asin = item["product"]["asin"]
                if asin in seen:
                    continue
                seen.add(asin)
                candidate = dict(item["product"])
                candidate["discovery_tier"] = tier_name
                candidates.append(candidate)

        log_event(
            "product_discovery",
            product_type=product_type,
            country=country,
            pool_size=len(pool),
            trending_count=sum(1 for c in candidates if c["discovery_tier"] == "trending"),
            best_seller_count=sum(1 for c in candidates if c["discovery_tier"] == "best_seller"),
            fallback_count=sum(1 for c in candidates if c["discovery_tier"] == "fallback"),
            candidate_count=len(candidates),
        )
        self.metrics.publish("ToolInvocations", 1, dimensions={"AgentName": "discovery"})

        return candidates

    # ------------------------------------------------------------------
    def _enrich(self, product: dict, country: str, window_days: int | None = None) -> dict:
        """Attaches recent-review stats to a product. Never raises: products
        whose reviews can't be fetched just get zeroed stats."""
        try:
            all_reviews = self.provider.get_reviews(product["asin"], country)
        except Exception:  # provider hiccup - treat as no review signal
            all_reviews = []
        recent = review_analysis.filter_recent(review_analysis.deduplicate(all_reviews), window_days)
        stats = review_analysis.calculate_statistics(recent)
        # Histogram = Amazon's own star distribution over ALL ratings ever -
        # a far wider negative-share signal than the visible reviews alone.
        histogram_info = None
        if hasattr(self.provider, "get_rating_histogram"):
            histogram_info = self.provider.get_rating_histogram(product["asin"])
        return {"product": product, "recent_stats": stats, "histogram": histogram_info or {}}

    @staticmethod
    def _rank_trending(enriched: list[dict]) -> list[dict]:
        """Tier A: real momentum. Amazon's own 'bought in past month' badge is
        the strongest available proxy for sales velocity (covers ALL buyers,
        not just the visible reviews); recent-review momentum is the backup
        gate for products without the badge."""
        def is_trending(e: dict) -> bool:
            product_rating = float(e["product"].get("rating") or 0)
            monthly = e["product"].get("monthly_purchases") or 0
            # Path 1: verified sales velocity + acceptable rating
            if monthly >= config.TRENDING_MIN_MONTHLY_PURCHASES and product_rating >= config.TRENDING_MIN_AVG_RATING:
                return True
            # Path 2: visible recent-review momentum (original gate)
            return (
                e["recent_stats"]["review_count"] >= config.TRENDING_MIN_RECENT_REVIEWS
                and (e["recent_stats"]["average_rating"] or 0) >= config.TRENDING_MIN_AVG_RATING
            )

        trending = [e for e in enriched if is_trending(e)]
        trending.sort(
            key=lambda e: (e["product"].get("monthly_purchases") or 0,
                           e["recent_stats"]["review_count"],
                           e["recent_stats"]["average_rating"] or 0),
            reverse=True,
        )
        return trending

    @staticmethod
    def _rank_bestsellers(enriched: list[dict]) -> list[dict]:
        """Tier B: proven performers - 4+ stars weighted by review volume."""
        qualified = [
            e for e in enriched
            if float(e["product"].get("rating") or 0) >= config.BESTSELLER_MIN_RATING
        ]

        def bestseller_score(e: dict) -> float:
            rating = float(e["product"].get("rating") or 0)
            # review_count comes from the catalog when available; recent count
            # is a weaker fallback so the score still works for synthetics.
            count = e["product"].get("review_count") or e["recent_stats"]["review_count"] or 1
            return rating * math.log10(max(count, 1) + 1)

        qualified.sort(key=bestseller_score, reverse=True)
        return qualified

    @staticmethod
    def _guarded_fallback(enriched: list[dict]) -> list[dict]:
        """Tier C: anything left, EXCEPT products with significantly negative
        recent sentiment (negative share >= DISCOVERY_MAX_NEGATIVE_PERCENT)."""
        safe = []
        for e in enriched:
            # Signal 1: negative share among visible recent reviews
            negative_pct = e["recent_stats"]["negative_percentage"]
            # Signal 2: negative share across the ENTIRE rating history
            # (Amazon's histogram: % of 1-star + % of 2-star ratings)
            hist = (e.get("histogram") or {}).get("histogram") or {}
            hist_negative_pct = (hist.get(1, 0) + hist.get(2, 0)) if hist else None

            signals = [p for p in (negative_pct, hist_negative_pct) if p is not None]
            worst = max(signals) if signals else 0
            if worst >= config.DISCOVERY_MAX_NEGATIVE_PERCENT:
                log_event(
                    "discovery_candidate_excluded",
                    asin=e["product"]["asin"],
                    reason="negative_review_share",
                    recent_negative_percentage=negative_pct,
                    lifetime_negative_percentage=hist_negative_pct,
                    threshold=config.DISCOVERY_MAX_NEGATIVE_PERCENT,
                )
                continue
            safe.append(e)
        safe.sort(key=lambda e: float(e["product"].get("rating") or 0), reverse=True)
        return safe
