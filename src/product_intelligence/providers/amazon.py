"""
AmazonProvider - live/optional data provider using Selenium.

IMPORTANT - read before using:
Automated access to amazon.com is restricted by Amazon's Conditions of Use,
which prohibit robots, spiders, and automated data-mining tools without
Amazon's express written consent. This provider is included for personal,
educational, small-scale, non-commercial demonstration of the provider
interface pattern (spec sections 12, 67-68) - it is NOT intended for
production use, high-volume scraping, or redistribution of scraped data.
For real production access to Amazon catalog/pricing data, use the
official Amazon Product Advertising API (PA-API), which requires an
Amazon Associates account and has its own usage terms.

This module deliberately does NOT attempt to defeat bot detection
(no proxy rotation, no CAPTCHA solving, no header spoofing beyond a
standard desktop user agent). If Amazon presents a CAPTCHA/challenge page
or the page structure has changed, methods here fail gracefully and
return empty results rather than trying to work around the block - the
rest of the application (DemoProvider) must keep working regardless
(spec section 48).

Selectors below are best-effort and WILL break as Amazon changes its page
markup; treat this as a reference implementation, not a maintained scraper.
"""
import atexit
import signal
import sys
import time
import weakref
from urllib.parse import quote_plus

from product_intelligence import config
from product_intelligence.observability.logging import log_error, log_event
from product_intelligence.providers.base import ProductDataProvider


class BrowserSetupError(RuntimeError):
    """Local browser setup failed before Amazon could be queried."""


# ─────────────────────────────────────────────────────────────────────────────
# Global driver registry for graceful cleanup on unexpected termination.
# Prevents zombie Chrome processes if Flask server is killed mid-request.
# ─────────────────────────────────────────────────────────────────────────────
_active_drivers: weakref.WeakSet = weakref.WeakSet()


def _cleanup_all_drivers():
    """Kill ALL active browser instances. Called on exit/signal."""
    for driver in list(_active_drivers):
        try:
            driver.quit()
        except Exception:
            pass


atexit.register(_cleanup_all_drivers)


def _signal_handler(signum, frame):
    _cleanup_all_drivers()
    sys.exit(0)


try:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
except (OSError, ValueError):
    pass  # Signals can't be set in non-main threads


# Currency markers as they appear in Amazon price strings, most specific first.
_CURRENCY_MARKERS = [
    ("INR", "₹"), ("₹", "₹"),
    ("CDN$", "C$"), ("C$", "C$"), ("MX$", "MX$"), ("R$", "R$"),
    ("COP", "COP"), ("COL$", "COP"),  # Colombian peso
    ("US$", "$"), ("S$", "S$"), ("A$", "A$"), ("AUD", "A$"), ("SGD", "S$"),
    ("SAR", "SAR"), ("ر.س", "SAR"), ("AED", "AED"), ("د.إ", "AED"),
    ("EGP", "EGP"), ("ج.م", "EGP"),
    ("NGN", "₦"), ("₦", "₦"),  # Nigerian naira
    ("zł", "zł"), ("kr", "kr"), ("₺", "₺"), ("TL", "₺"),
    ("£", "£"), ("€", "€"), ("¥", "¥"), ("￥", "¥"), ("JPY", "¥"),
    ("ZAR", "R"), ("R", "R"),  # South African rand - checked AFTER R$/SAR/EUR etc.
    ("$", "$"),
]


def _detect_currency(price_text: str) -> str | None:
    """
    Extracts the currency symbol Amazon actually rendered. Amazon geo-detects
    the client IP and may serve converted prices (e.g. INR on amazon.com when
    browsing from India), so the marketplace alone is NOT a reliable currency
    indicator - the scraped string is the source of truth.
    """
    for marker, symbol in _CURRENCY_MARKERS:
        if marker in price_text:
            return symbol
    return None


def _parse_price(price_text: str) -> float | None:
    """
    Locale-aware price string -> float. Handles both decimal conventions:
      "$1,234.56" / "£38.07"   (dot decimal, comma thousands)
      "1.234,56 €" / "38,07 €"  (comma decimal, dot thousands)
      "￥3,807" / "1 234,56 zł"  (thousands only / space thousands)
    Heuristic: when both separators appear, the LAST one is the decimal
    point; a lone separator followed by exactly two digits is a decimal.
    """
    import re as _re
    s = _re.sub(r"[^\d.,]", "", price_text.replace("\u00a0", "").replace("\u202f", ""))
    if not s:
        return None
    last_dot, last_com = s.rfind("."), s.rfind(",")
    if last_dot != -1 and last_com != -1:
        if last_com > last_dot:          # 1.234,56 -> comma is decimal
            s = s.replace(".", "").replace(",", ".")
        else:                            # 1,234.56 -> dot is decimal
            s = s.replace(",", "")
    elif last_com != -1:
        if s.count(",") == 1 and len(s) - last_com - 1 == 2:
            s = s.replace(",", ".")     # 38,07 -> decimal comma
        else:
            s = s.replace(",", "")      # 3,807 / 1,234,567 -> thousands
    elif last_dot != -1:
        if not (s.count(".") == 1 and len(s) - last_dot - 1 == 2):
            s = s.replace(".", "")      # 1.234 / 1.234.567 -> thousands
    try:
        return float(s)
    except ValueError:
        return None



class AmazonAccessError(Exception):
    """Raised when Amazon presents a challenge/CAPTCHA or the page structure is unrecognized."""

    def __init__(self, message: str, code: str = "amazon_access_error"):
        super().__init__(message)
        self.code = code


def check_search_page(driver, country: str):
    """Identify observed upstream failures before interpreting an empty grid."""
    from selenium.webdriver.common.by import By

    domain = _domain_for_country(country)
    title = driver.title.lower()
    if "sorry! something went wrong" in title or "service unavailable" in title:
        raise AmazonAccessError(
            f"{domain} returned its '{driver.title}' error page, with no product listings. "
            "Try this marketplace later or explicitly choose another marketplace.",
            code="amazon_error_page",
        )
    if "robot check" in title or driver.find_elements(
        By.CSS_SELECTOR, "#captchacharacters, form[action*='validateCaptcha']",
    ):
        raise AmazonAccessError(
            f"{domain} returned a verification challenge instead of search results. "
            "Automated collection stopped. Try later or choose another marketplace.",
            code="amazon_verification_required",
        )


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_review_date(date_text: str) -> str:
    """
    Converts Amazon's review date line to ISO format (YYYY-MM-DD).
    Handles 'Reviewed in the United States on July 15, 2025' and similar
    English-locale formats. Returns '' when unparseable - the 90-day
    filter skips reviews with invalid dates rather than guessing.
    """
    import re
    m = re.search(r"on\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", date_text)
    if not m:
        m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", date_text)
    if not m:
        return ""
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return ""
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"



def _domain_for_country(country: str) -> str:
    domain = config.AMAZON_DOMAINS.get(country.upper())
    if not domain:
        raise ValueError(f"No Amazon domain configured for country={country!r}")
    return domain


def build_search_url(product_type: str, country: str) -> str:
    domain = _domain_for_country(country)
    return f"https://www.{domain}/s?k={quote_plus(product_type)}"


def build_review_url(asin: str, country: str) -> str:
    """
    Canonical review endpoint - constructed from the ASIN, never stored or
    passed as a full search-result URL with tracking query params
    (spec section 13).
    """
    domain = _domain_for_country(country)
    return f"https://www.{domain}/product-reviews/{asin}/"


class AmazonProvider(ProductDataProvider):
    def __init__(self, headless: bool = True, page_load_timeout: int = 25):
        self.headless = headless
        self.page_load_timeout = page_load_timeout
        self._driver = None
        # ASIN -> reviews, filled by prefetch_reviews() (parallel tabs);
        # get_reviews() serves from here when available.
        self._reviews_cache: dict[str, list[dict]] = {}
        # ASIN -> {"histogram": {5: pct...}, "total_ratings": int} pulled from
        # the product page - covers ALL ratings ever, not just visible reviews.
        self._histogram_cache: dict[str, dict] = {}
        # ASIN -> product dict from the last search_products() call, so
        # get_product()/get_price_history() don't re-fetch the search page.
        self._product_cache: dict[str, dict] = {}
        # (product_type, country) -> search results, so DiscoveryAgent's
        # second search_products() call doesn't re-fetch the search page.
        self._search_cache: dict[tuple, list[dict]] = {}
        # How many ad tiles the last search skipped (surfaced in the UI dialogue).
        self.last_sponsored_skipped = 0
        self.page_stats = {"search_pages": 0, "product_pages": 0,
                           "pages_processed": 0, "reviews_extracted": 0}
        self.progress_callback = None

    def _report_pages(self):
        if self.progress_callback:
            self.progress_callback(dict(self.page_stats))

    def _get_driver(self):
        if self._driver is not None:
            return self._driver
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # Use the installed browser's real settings. Keep eager navigation,
        # which returns after the DOM is interactive without blocking images.
        options.page_load_strategy = "eager"
        # Selenium defaults close_fds=False on Windows. Otherwise ChromeDriver
        # inherits Flask's listening socket and can keep port 5050 alive after
        # the server stops, making the replacement server appear unresponsive.
        service = Service(popen_kw={"close_fds": True})
        self._driver = webdriver.Chrome(options=options, service=service)
        self._driver.set_page_load_timeout(self.page_load_timeout)
        _active_drivers.add(self._driver)  # Register for graceful cleanup
        return self._driver

    def close(self):
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    def search_products(self, product_type: str, country: str, max_results: int = 5) -> list[dict]:
        """
        Extracts ASINs and basic info from Amazon search-result elements.
        Returns [] on any access failure rather than raising, so the caller
        (DiscoveryAgent) can be swapped back to DemoProvider without code
        changes elsewhere (spec section 48).
        """
        from selenium.common.exceptions import (
            NoSuchDriverException,
            TimeoutException,
            WebDriverException,
        )
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC  # noqa: N812 -- Selenium's documented alias
        from selenium.webdriver.support.ui import WebDriverWait

        cache_key = (product_type.strip().lower(), country.upper())
        if cache_key in self._search_cache:
            return self._search_cache[cache_key][:max_results]

        url = build_search_url(product_type, country)
        # Amazon intermittently serves a degraded page (normal title, empty
        # result grid) to automated sessions - a soft rate-limit. A fresh
        # browser session usually clears it, so retry once with a new driver.
        for attempt in range(2):
            try:
                driver = self._get_driver()
                driver.get(url)
                self.page_stats["search_pages"] += 1
                self._report_pages()
                check_search_page(driver, country)
                # Explicit wait beats a flat sleep: returns the moment results
                # render, tolerates slow loads up to 12s.
                try:
                    WebDriverWait(driver, 12).until(
                        EC.presence_of_element_located((
                            By.CSS_SELECTOR, "div[data-component-type='s-search-result']"))
                    )
                except TimeoutException:
                    pass  # extraction below handles the empty case

                results = self._extract_search_tiles(driver, max_results, country)
                self.page_stats["pages_processed"] += 1
                self._report_pages()
                if results:
                    self._search_cache[cache_key] = results
                    log_event("amazon_search", product_type=product_type, country=country,
                              result_count=len(results), attempt=attempt)
                    return results

                log_event("amazon_search_empty_retry", product_type=product_type,
                          country=country, attempt=attempt)
                self.close()  # fresh session for the retry

            except AmazonAccessError as exc:
                if exc.code == "amazon_error_page" and attempt == 0:
                    log_event("amazon_error_page_retry", country=country, attempt=attempt)
                    if self.progress_callback:
                        self.progress_callback({
                            **self.page_stats,
                            "message": "Amazon returned an error page. Retrying once in 2 seconds.",
                        })
                    self.close()
                    time.sleep(2)
                    continue
                raise
            except NoSuchDriverException as exc:
                log_error("amazon_browser_setup_failed", exc)
                raise BrowserSetupError(
                    "Chrome could not start because its driver is unavailable. "
                    "Run the server with permission to access Selenium's driver cache "
                    "and download the matching Chrome driver. This request did not reach Amazon."
                ) from exc
            except (TimeoutException, WebDriverException) as exc:
                log_error("amazon_search_failed", exc, product_type=product_type, country=country)
                self.close()
            except Exception as exc:  # noqa: BLE001 - unexpected page-structure change fails soft
                log_error(
                    "amazon_search_unexpected_error",
                    exc,
                    product_type=product_type,
                    country=country,
                )
                return []

        log_event("amazon_search", product_type=product_type, country=country, result_count=0)
        return []

    def _extract_search_tiles(self, driver, max_results: int, country: str) -> list[dict]:
        """
        Pulls organic products from the current search-results page.
        `country` supplies the default currency when the price text carries no
        recognisable symbol (it was previously an undefined name here, and the
        NameError -- swallowed by the price block's `except` -- discarded the price).
        Primary selector: div[data-component-type='s-search-result'].
        Fallback: any div[data-asin] that contains an h2 (Amazon A/B-tests
        layouts where the component-type attribute is absent).
        """
        from selenium.webdriver.common.by import By

        result_elements = driver.find_elements(
            By.CSS_SELECTOR, "div[data-component-type='s-search-result']")
        if not result_elements:
            result_elements = [
                el for el in driver.find_elements(By.CSS_SELECTOR, "div[data-asin]")
                if el.get_attribute("data-asin") and el.find_elements(By.CSS_SELECTOR, "h2")
            ]

        results = []
        self.last_sponsored_skipped = 0
        seen_asins: set[str] = set()
        for el in result_elements:
            asin = el.get_attribute("data-asin")
            if not asin or asin in seen_asins:
                continue

            # --- Sponsored-tile guard -------------------------------
            # Amazon marks ads with a 'Sponsored' label and/or /sspa/
            # click-redirect links. Ads are allowed through ONLY when
            # they still expose organic rating data (rare); otherwise
            # they'd enter the ranking with no review/rating signal.
            is_sponsored = bool(el.find_elements(
                By.CSS_SELECTOR, "span.puis-sponsored-label-text"))
            if not is_sponsored:
                first_links = el.find_elements(By.TAG_NAME, "a")[:3]
                is_sponsored = any(
                    "/sspa/click" in (a.get_attribute("href") or "")
                    for a in first_links
                )

            try:
                title = el.find_element(By.CSS_SELECTOR, "h2 span").text
            except Exception:
                title = ""
            try:
                # .a-offscreen holds the accessible price string. Amazon
                # geo-detects the client and may use ANY currency format
                # ("$38.07", "38,07 €", "￥3,807", "INR 3,807.37") - parse
                # with locale-aware separator handling.
                price_text = el.find_element(
                    By.CSS_SELECTOR, "span.a-price span.a-offscreen"
                ).get_attribute("textContent")
                price = _parse_price(price_text)
                currency = _detect_currency(price_text)
                if currency is None and price is not None:
                    currency = config.DEFAULT_CURRENCY.get(country.upper())
            except Exception:
                price = None
                currency = None
            try:
                rating_text = el.find_element(By.CSS_SELECTOR, "span.a-icon-alt").get_attribute(
                    "innerText"
                )
                rating = float(rating_text.split(" ")[0])
            except Exception:
                rating = None
            try:
                # Total review count: the ratings link carries an
                # aria-label like '21 ratings'; the visible text is '(21)'.
                count_el = el.find_elements(By.CSS_SELECTOR, "a[aria-label*='rating']")
                count_text = count_el[0].get_attribute("aria-label") if count_el else ""
                review_count = int("".join(c for c in count_text if c.isdigit()) or 0) or None
            except Exception:
                review_count = None

            # "X bought in past month" - Amazon's own purchase-velocity badge.
            # This is REAL sales momentum for the whole product, far stronger
            # than counting the handful of visible reviews. "20K+" -> 20000.
            monthly_purchases = None
            try:
                import re as _re
                tile_text = el.get_attribute("textContent") or ""
                m = _re.search(r"([\d,.]+)(K?)\+?\s*bought in past month", tile_text)
                if m:
                    base = float(m.group(1).replace(",", ""))
                    monthly_purchases = int(base * (1000 if m.group(2) == "K" else 1))
            except Exception:
                monthly_purchases = None

            # Sponsored tiles without genuine rating/review data are
            # skipped entirely - they can't be evaluated fairly.
            if is_sponsored and (rating is None or not review_count):
                self.last_sponsored_skipped += 1
                log_event("amazon_sponsored_tile_skipped", asin=asin, title=title[:60])
                continue

            if title:
                seen_asins.add(asin)
                product = {"asin": asin, "title": title, "price": price,
                           "currency": currency,
                           "rating": rating, "review_count": review_count,
                           "sponsored": is_sponsored,
                           "monthly_purchases": monthly_purchases}
                self._product_cache[asin] = dict(product)
                results.append(product)
            if len(results) >= max_results:
                break

        return results

    def get_product(self, asin: str, country: str) -> dict | None:
        # Serve from the last search's cache - avoids an extra page fetch.
        if asin in self._product_cache:
            return dict(self._product_cache[asin])
        results = self.search_products(asin, country, max_results=1)
        return results[0] if results else None

    def prefetch_reviews(self, asins: list[str], country: str) -> None:
        """
        Loads all ASINs' product pages CONCURRENTLY in separate browser tabs
        (window.open is non-blocking), then extracts reviews from each.
        Turns N x page-load-time into roughly ONE page-load-time total -
        the single biggest latency win for live mode.
        Results land in self._reviews_cache; get_reviews() reads from it.
        """
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC  # noqa: N812 -- Selenium's documented alias
        from selenium.webdriver.support.ui import WebDriverWait

        todo = [a for a in asins if a not in self._reviews_cache]
        if not todo:
            return
        start = time.perf_counter()
        try:
            driver = self._get_driver()
            domain = _domain_for_country(country)

            # Fire all tabs at t=0: tab 0 via get(), rest via window.open()
            driver.get(f"https://www.{domain}/dp/{todo[0]}")
            self.page_stats["product_pages"] += 1
            self._report_pages()
            for asin in todo[1:]:
                driver.execute_script(f"window.open('https://www.{domain}/dp/{asin}');")
                self.page_stats["product_pages"] += 1
                self._report_pages()

            handles = driver.window_handles[-len(todo):]
            for idx, (asin, handle) in enumerate(zip(todo, handles)):
                driver.switch_to.window(handle)
                try:
                    # Wait until the page is usable: either reviews are present
                    # OR the product title rendered (pages with zero reviews
                    # would otherwise burn the full timeout). Tabs load in
                    # parallel, so later tabs are usually ready instantly -
                    # give the first tab longer, the rest a short grace.
                    WebDriverWait(driver, 10 if idx == 0 else 3).until(
                        EC.presence_of_element_located((
                            By.CSS_SELECTOR, "div[data-hook='review'], #productTitle"))
                    )
                except TimeoutException:
                    pass  # extract whatever is there
                self._reviews_cache[asin] = self._extract_reviews_from_current_page(driver, asin)
                self._histogram_cache[asin] = self._extract_histogram_from_current_page(driver)
                self.page_stats["pages_processed"] += 1
                self.page_stats["reviews_extracted"] += len(self._reviews_cache[asin])
                self._report_pages()

            # Close extra tabs, keep one for future navigation
            while len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                driver.close()
            driver.switch_to.window(driver.window_handles[0])

            log_event("amazon_prefetch_complete", asin_count=len(todo),
                      elapsed_ms=int((time.perf_counter() - start) * 1000))
        except Exception as exc:  # noqa: BLE001 - prefetch is best-effort
            log_error("amazon_prefetch_failed", exc, asin_count=len(todo))

    def get_reviews(self, asin: str, country: str) -> list[dict]:
        """
        Serves from the prefetch cache when available (see prefetch_reviews);
        otherwise fetches the product page directly. Returns [] on failure -
        never raises up to the agent layer.
        """
        if asin in self._reviews_cache:
            return list(self._reviews_cache[asin])

        from selenium.common.exceptions import TimeoutException, WebDriverException
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC  # noqa: N812 -- Selenium's documented alias
        from selenium.webdriver.support.ui import WebDriverWait

        try:
            driver = self._get_driver()
            domain = _domain_for_country(country)
            # /dp/ shows top reviews without sign-in (the /product-reviews/
            # endpoint is usually login-gated for anonymous sessions).
            driver.get(f"https://www.{domain}/dp/{asin}")
            self.page_stats["product_pages"] += 1
            self._report_pages()
            try:
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-hook='review']"))
                )
            except TimeoutException:
                pass
            reviews = self._extract_reviews_from_current_page(driver, asin)
            self._histogram_cache[asin] = self._extract_histogram_from_current_page(driver)
            self._reviews_cache[asin] = reviews
            self.page_stats["pages_processed"] += 1
            self.page_stats["reviews_extracted"] += len(reviews)
            self._report_pages()
            log_event(
                "amazon_reviews_fetched", asin=asin, country=country, review_count=len(reviews)
            )
            return reviews

        except (TimeoutException, WebDriverException) as exc:
            log_error("amazon_reviews_failed", exc, asin=asin, country=country)
            return []
        except Exception as exc:  # noqa: BLE001
            log_error("amazon_reviews_unexpected_error", exc, asin=asin, country=country)
            return []

    def get_rating_histogram(self, asin: str) -> dict | None:
        """
        Returns {"histogram": {5: 71, 4: 14, 3: 6, 2: 2, 1: 7}, "total_ratings": 49933}
        for an ASIN whose product page was already fetched, else None.
        Percentages cover the product's ENTIRE rating history - a much wider
        negative-share signal than the handful of visible reviews.
        """
        return self._histogram_cache.get(asin)

    @staticmethod
    def _extract_histogram_from_current_page(driver) -> dict:
        """
        Reads Amazon's star-distribution widget (aria-labels like
        '71 percent of reviews have 5 stars') and the global ratings count.
        Works without login. Returns {} on any failure.
        """
        import re

        from selenium.webdriver.common.by import By

        out: dict = {}
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, "[aria-label*='percent of reviews have']")
            histogram = {}
            for r in rows:
                label = r.get_attribute("aria-label") or ""
                m = re.search(r"(\d+)\s*percent of reviews have\s*(\d)\s*star", label)
                if m:
                    histogram[int(m.group(2))] = int(m.group(1))
            if histogram:
                out["histogram"] = histogram

            m = re.search(r"([\d,]+)\s+global ratings", driver.page_source)
            if m:
                out["total_ratings"] = int(m.group(1).replace(",", ""))
        except Exception:
            pass
        return out


    def _extract_reviews_from_current_page(self, driver, asin: str) -> list[dict]:
        """Extracts normalized review dicts from whatever page the driver is on."""
        from selenium.webdriver.common.by import By

        review_elements = driver.find_elements(By.CSS_SELECTOR, "div[data-hook='review']")
        reviews = []
        for el in review_elements:
            try:
                review_id = el.get_attribute("id") or ""
                # Star rating: /product-reviews/ uses review-star-rating,
                # /dp/ pages use cmps-review-star-rating. The span is
                # visually hidden, so read textContent (not .text).
                rating_el = el.find_elements(
                    By.CSS_SELECTOR,
                    "i[data-hook='review-star-rating'] span, "
                    "i[data-hook='cmps-review-star-rating'] span",
                )
                if not rating_el:
                    continue
                rating_text = rating_el[0].get_attribute("textContent")
                rating = int(float(rating_text.split(" ")[0]))
                # Title/body hooks differ between /product-reviews/ pages
                # (review-title, review-body) and /dp/ pages
                # (reviewTitle, reviewRichContentContainer).
                title_els = el.find_elements(
                    By.CSS_SELECTOR,
                    "a[data-hook='review-title'] span:not([class*='a-letter-space']), "
                    "span[data-hook='review-title'] span:not([class*='a-letter-space']), "
                    "[data-hook='reviewTitle']",
                )
                title = title_els[-1].get_attribute("textContent").strip() if title_els else ""
                body_els = el.find_elements(
                    By.CSS_SELECTOR,
                    "span[data-hook='review-body'], [data-hook='reviewRichContentContainer']",
                )
                if not body_els:
                    continue
                text = body_els[-1].get_attribute("textContent").strip()
                # /dp/ pages append expander UI text; strip it
                for noise in ("Read moreRead less", "Read more", "Read less"):
                    text = text.replace(noise, "").strip()
                raw_date = el.find_element(
                    By.CSS_SELECTOR, "span[data-hook='review-date']"
                ).get_attribute("textContent")
                date_text = parse_review_date(raw_date)
                verified = bool(el.find_elements(By.CSS_SELECTOR, "span[data-hook='avp-badge']"))
                try:
                    helpful_text = el.find_element(
                        By.CSS_SELECTOR, "span[data-hook='helpful-vote-statement']"
                    ).get_attribute("textContent")
                    helpful_votes = int("".join(c for c in helpful_text if c.isdigit()) or 0)
                except Exception:
                    helpful_votes = 0

                reviews.append({
                    "asin": asin,
                    "review_id": review_id,
                    "rating": rating,
                    "title": title,
                    "text": text,
                    "date": date_text,  # ISO YYYY-MM-DD ('' when unparseable)
                    "verified_purchase": verified,
                    "helpful_votes": helpful_votes,
                })
            except Exception:
                continue  # skip individual malformed review elements
        return reviews

    def get_price_history(self, asin: str, country: str) -> list[dict]:
        """
        Amazon does not expose historical pricing to unauthenticated
        scraping - only the current price is available live. This method
        returns a single-point "history" (today's price) rather than
        fabricating past prices. For real price-trend data, use a
        dedicated price-tracking service/API (e.g. Keepa) or your own
        stored observations over time.
        """
        product = self.get_product(asin, country)
        if not product or product.get("price") is None:
            return []
        from datetime import date

        return [
            {
                "asin": asin,
                "date": date.today().isoformat(),
                "country": country,
                "price": product["price"],
            }
        ]
