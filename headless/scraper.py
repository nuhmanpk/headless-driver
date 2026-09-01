import csv
import json
import time
import base64
import threading
from typing import Optional, List, Dict, Callable, Any, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote_plus, parse_qs, unquote

from .core import Headless
from .logs import get_logger, enable_console_logging
from .results import (
    EngineAttempt, SearchResponse, AllEnginesBlocked,
    STATUS_OK, STATUS_EMPTY, STATUS_BLOCKED, STATUS_TIMEOUT,
    STATUS_UNREACHABLE, STATUS_ERROR,
)
from .transport import BrowserTransport, HttpTransport, http_available

log = get_logger("scraper")

# Per-engine endpoint, selectors, and capabilities.
#
# "js" says whether the endpoint needs a browser. The DuckDuckGo HTML and Lite
# endpoints render server-side, so they can be fetched over plain HTTP: far
# faster, far smaller, and thread-safe. "snippets" says whether the engine
# returns description text at all, which callers matching on snippet text need
# to know before they build on it.
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "result": "div.result:not(.result--ad):not(.result--no-result)",
        "link": ["a.result__a"],
        "title": ["a.result__a"],
        "snippet": ["a.result__snippet", ".result__snippet"],
        "js": False,
        "snippets": True,
    },
    "duckduckgo_lite": {
        "url": "https://lite.duckduckgo.com/lite/?q={query}",
        "result": "table tr:has(a.result-link)",
        "link": ["a.result-link"],
        "title": ["a.result-link"],
        "snippet": ["td.result-snippet"],
        "js": False,
        # Verified against the live endpoint: Lite returns titles and URLs but
        # no description text. Callers matching on snippets must know this
        # rather than discover it from empty strings.
        "snippets": False,
    },
    "duckduckgo_js": {
        "url": "https://duckduckgo.com/?q={query}",
        "result": "[data-testid='result']",
        "link": ["[data-testid='result-title-a']"],
        "title": ["[data-testid='result-title-a']"],
        "snippet": ["[data-result='snippet']"],
        "js": True,
        "snippets": True,
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "result": "li.b_algo",
        "link": ["h2 a"],
        "title": ["h2"],
        "snippet": [".b_caption p", "p[class*='b_lineclamp']", ".b_algoSlug", "p"],
        "js": False,
        "snippets": True,
    },
    "mojeek": {
        "url": "https://www.mojeek.com/search?q={query}",
        "result": "ul.results-standard > li, li.result",
        "link": ["a.title", "h2 a"],
        "title": ["a.title", "h2"],
        "snippet": ["p.s", "p"],
        # Serves a results-free stub to plain HTTP clients, so it needs a
        # browser despite the page itself being server-rendered.
        "js": True,
        "snippets": True,
    },
    "google": {
        "url": "https://www.google.com/search?q={query}",
        "result": "div.g, div[data-hveid] div[data-snf]",
        "link": ["a:has(h3)", "a[href^='http']"],
        "title": ["h3"],
        "snippet": ["div[data-sncf] span", "div[style*='webkit-line-clamp']", "span"],
        "js": True,
        "snippets": True,
    },
    "startpage": {
        "url": "https://www.startpage.com/sp/search?q={query}",
        "result": ".w-gl__result, .result",
        "link": ["a.w-gl__result-title", "a.result-link", "a[href^='http']"],
        "title": ["h3", ".w-gl__result-title"],
        "snippet": [".w-gl__description", "p.description"],
        "js": True,
        "snippets": True,
    },
    "yandex": {
        "url": "https://yandex.com/search/?text={query}",
        "result": "li.serp-item, .organic",
        "link": ["a.OrganicTitle-Link", "h2 a", "a[href^='http']"],
        "title": [".OrganicTitleContentSpan", "h2"],
        "snippet": [".OrganicTextContentSpan", ".TextContainer"],
        "js": True,
        "snippets": True,
    },
}

DEFAULT_ENGINE = "duckduckgo"

# Tried in order when the primary engine is blocked or returns nothing.
DEFAULT_FALLBACK_ENGINES: List[str] = [
    "duckduckgo_lite",
    "bing",
    "mojeek",
    "duckduckgo_js",
    "startpage",
    "google",
    "yandex",
]


class AdvancedSearchScraper:
    """Search the web, walking a chain of engines until one answers."""

    # Interstitials engines serve instead of results when they flag automation.
    _BLOCK_SELECTORS = (
        "#challenge-form",
        "form[action*='anomaly']",
        ".anomaly-modal__title",
        "form#captcha-form",
        "div#recaptcha",
        "#challenge-running",
        ".CheckboxCaptcha",
        ".AdvancedCaptcha",
    )
    _BLOCK_URL_MARKERS = ("/sorry/", "captcha", "showcaptcha", "anomaly", "/challenge")

    def __init__(
        self,
        driver=None,
        max_results: int = 10,
        result_processor: Optional[Callable[[str, Dict[str, Any]], Dict]] = None,
        headless_options: Optional[dict] = None,
        search_engine: str = DEFAULT_ENGINE,
        verbose: bool = False,
        fallback: bool = True,
        fallback_engines: Optional[Sequence[str]] = None,
        page_load_timeout: float = 20.0,
        wait_timeout: float = 8.0,
        proxy: Optional[str] = None,
        transport: str = "auto",
        keep_history: bool = False,
        history_limit: int = 1000,
        raise_on_block: bool = False,
        user_agent: Optional[str] = None,
    ):
        self.driver = driver
        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = dict(headless_options or {})
        self.engines: Dict[str, Dict[str, Any]] = {
            name: dict(spec) for name, spec in ENGINE_SPECS.items()
        }
        self.search_engine = self._validate_engine(search_engine or DEFAULT_ENGINE)
        self.verbose = verbose
        if verbose:
            # `verbose` is the caller explicitly asking for output, which is the
            # only circumstance in which a library may write to the console.
            enable_console_logging()
        self.fallback = fallback
        self.fallback_engines = list(
            DEFAULT_FALLBACK_ENGINES if fallback_engines is None else fallback_engines
        )
        self.page_load_timeout = page_load_timeout
        self.wait_timeout = wait_timeout
        self.proxy = proxy
        if transport not in ("auto", "http", "browser"):
            raise ValueError(
                f"transport must be 'auto', 'http' or 'browser', not {transport!r}")
        self.transport = transport
        self.user_agent = user_agent

        # Retaining every result forever leaks in a long-lived worker, so history
        # is opt-in and bounded.
        self.keep_history = keep_history
        self.history_limit = history_limit
        self.results: List[Dict] = []
        self.last_response: Optional[SearchResponse] = None
        self.last_engine: Optional[str] = None
        self.raise_on_block = raise_on_block

        self._driver_context: Optional[Headless] = None
        self._timeouts_applied = False
        self._http: Optional[HttpTransport] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ engines
    def _validate_engine(self, engine: str) -> str:
        name = (engine or "").lower()
        if name not in self.engines:
            raise ValueError(
                f"Unsupported search_engine {engine!r}; "
                f"expected one of {sorted(self.engines)}")
        return name

    def default_result_processor(self, query: str, item: Dict[str, Any]) -> Dict:
        return item

    def register_engine(self, name: str, spec: Dict[str, Any]) -> None:
        """Add or override an engine definition on this instance."""
        missing = {"url", "result", "link", "title", "snippet"} - set(spec)
        if missing:
            raise ValueError(f"engine spec missing keys: {sorted(missing)}")
        spec = dict(spec)
        spec.setdefault("js", True)          # assume a browser is needed
        spec.setdefault("snippets", True)
        self.engines[name.lower()] = spec

    def capabilities(self, engine: Optional[str] = None) -> Dict[str, Any]:
        """What an engine can do: whether it needs a browser, returns snippets."""
        spec = self._spec(engine)
        return {"js": bool(spec.get("js", True)),
                "snippets": bool(spec.get("snippets", True)),
                "url": spec["url"]}

    def _spec(self, engine: Optional[str] = None) -> Dict[str, Any]:
        return self.engines[engine or self.search_engine]

    def _engine_url(self, query: str, engine: Optional[str] = None) -> str:
        # The query must be percent-encoded or spaces and '&' corrupt the URL.
        return self._spec(engine)["url"].replace("{query}", quote_plus(query))

    def _engine_order(self, engine: Optional[str] = None,
                      fallback: Optional[bool] = None) -> List[str]:
        primary = self._validate_engine(engine) if engine else self.search_engine
        use_fallback = self.fallback if fallback is None else fallback
        order = [primary]
        if use_fallback:
            for name in self.fallback_engines:
                name = name.lower()
                if name in self.engines and name not in order:
                    order.append(name)
        if self.transport == "http":
            # Browser-only engines cannot be served by the HTTP transport.
            order = [n for n in order if not self.engines[n].get("js", True)] or order[:1]
        return order

    # ---------------------------------------------------------- extraction
    def _favicon_for(self, url: str) -> str:
        try:
            domain = urlparse(url).netloc
            return f"https://www.google.com/s2/favicons?domain={domain}" if domain else ""
        except Exception:
            return ""

    @staticmethod
    def _unwrap_redirect(href: str) -> str:
        """Recover the destination behind an engine's click-tracking redirect."""
        try:
            parts = urlparse(href)
            params = parse_qs(parts.query)
        except Exception:
            return href
        host = parts.netloc.lower()

        # DuckDuckGo: /l/?uddg=<percent-encoded target>
        if "duckduckgo.com" in host and parts.path.startswith("/l/"):
            target = unquote(params.get("uddg", [""])[0])
            if target.startswith(("http://", "https://")):
                return target
        # Google: /url?q=<percent-encoded target>
        if "google." in host and parts.path in ("/url", "/imgres"):
            target = unquote(params.get("q", params.get("url", [""]))[0])
            if target.startswith(("http://", "https://")):
                return target
        # Bing: /ck/a?...&u=a1<base64url of target>
        if "bing.com" in host and parts.path.startswith("/ck/a"):
            target = params.get("u", [""])[0]
            if target.startswith("a1"):
                payload = target[2:]
                payload += "=" * (-len(payload) % 4)
                try:
                    decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
                except Exception:
                    return href
                if decoded.startswith(("http://", "https://")):
                    return decoded
        return href

    @staticmethod
    def _first_text(node, selectors: List[str]) -> str:
        for sel in selectors:
            for found in node.select(sel):
                text = found.text()
                if text:
                    return text
        return ""

    def _find_link(self, node, selectors: List[str]) -> str:
        for sel in selectors:
            for found in node.select(sel):
                href = found.attr("href")
                if href.startswith(("http://", "https://")):
                    return self._unwrap_redirect(href)
        return ""

    def _extract_result(self, node, engine: str) -> Dict:
        spec = self._spec(engine)
        out = {"url": "", "title": "", "snippet": "", "favicon": "",
               "cached": None, "quick_answer": None, "engine": engine}

        href = self._find_link(node, spec["link"])
        if not href:
            # Last resort: the first plain outbound link in the container.
            href = self._find_link(node, ["a[href]"])
        out["url"] = href
        out["title"] = self._first_text(node, spec["title"])
        out["snippet"] = self._first_text(node, spec["snippet"])
        out["favicon"] = self._favicon_for(href) if href else ""

        cached = node.select("a.result__more-link")
        if cached:
            out["cached"] = cached[0].attr("href")
        return out

    def _blocked_reason(self, page) -> str:
        current = (page.url or "").lower()
        for marker in self._BLOCK_URL_MARKERS:
            if marker in current:
                return f"bot-check page ({current[:80]})"
        for sel in self._BLOCK_SELECTORS:
            if page.select(sel):
                return f"bot-check element {sel!r}"
        return ""

    # ------------------------------------------------------------- drivers
    def _http_transport(self) -> HttpTransport:
        if self._http is None:
            self._http = HttpTransport(timeout=self.page_load_timeout,
                                       user_agent=self.user_agent or "",
                                       proxy=self.proxy)
        return self._http

    def _driver_alive(self) -> bool:
        if not self.driver:
            return False
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False

    def _get_driver(self):
        """Return a live WebDriver, replacing one that has died."""
        if self.driver is not None and not self._driver_alive():
            # A crashed browser otherwise poisons every later call.
            log.warning("WebDriver session is dead; rebuilding it")
            self.recycle()
        if not self.driver:
            options = dict(self.headless_options)
            if self.proxy:
                args = list(options.get("additional_args") or [])
                args.append(f"--proxy-server={self.proxy}")
                options["additional_args"] = args
            if self.user_agent:
                options.setdefault("user_agent", self.user_agent)
            options.setdefault("verbose", self.verbose)
            options.setdefault("page_load_timeout", self.page_load_timeout)
            hl = Headless(**options)
            self._driver_context = hl
            self.driver = hl.get_driver()
            self._timeouts_applied = False
        if not self._timeouts_applied:
            # Without this a wedged page load blocks for Selenium's 300s default.
            try:
                self.driver.set_page_load_timeout(self.page_load_timeout)
                self.driver.set_script_timeout(self.page_load_timeout)
            except Exception:
                pass
            self._timeouts_applied = True
        return self.driver

    def recycle(self) -> None:
        """Throw away the current browser; the next search builds a fresh one."""
        with self._lock:
            context, driver = self._driver_context, self.driver
            self._driver_context = None
            self.driver = None
            self._timeouts_applied = False
        for closer in (context, driver):
            if closer is None:
                continue
            try:
                closer.quit()
            except Exception:
                pass

    def _transport_for(self, engine: str):
        """Pick the cheapest transport that can serve this engine."""
        spec = self._spec(engine)
        needs_js = spec.get("js", True)
        # The HTTP client speaks http(s) only; anything else (file://, data:)
        # has to go through the browser whatever the engine declares.
        if not str(spec.get("url", "")).startswith(("http://", "https://")):
            needs_js = True
        if self.transport == "browser" or needs_js:
            return BrowserTransport(self._get_driver(), self.wait_timeout), True
        if self.transport in ("auto", "http"):
            ok, _ = http_available()
            if ok:
                return self._http_transport(), False
            if self.transport == "http":
                raise RuntimeError(
                    'transport="http" needs the http extra: '
                    'pip install "headless-driver[http]"')
        return BrowserTransport(self._get_driver(), self.wait_timeout), True

    # -------------------------------------------------------------- search
    def _search_one(self, engine: str, query: str, limit: int) -> tuple:
        """Scrape one engine. Returns (results, EngineAttempt)."""
        url = self._engine_url(query, engine)
        spec = self._spec(engine)
        started = time.time()

        def attempt(status, count=0, reason=""):
            return EngineAttempt(engine=engine, status=status, count=count,
                                 reason=reason, elapsed=time.time() - started)

        try:
            transport, is_browser = self._transport_for(engine)
        except Exception as e:
            log.error("%s transport unavailable: %s", engine, e)
            return [], attempt(STATUS_ERROR, reason=str(e))

        log.debug("%s: %s (%s)", engine, url, getattr(transport, "name", "?"))
        try:
            if is_browser:
                page = transport.fetch(url, spec["result"])
            else:
                page = transport.fetch(url)
        except Exception as e:
            name = type(e).__name__
            if "Timeout" in name:
                log.debug("%s exceeded the %ss page load timeout",
                          engine, self.page_load_timeout)
                if is_browser:
                    try:
                        self.driver.execute_script("window.stop();")
                    except Exception:
                        pass
                return [], attempt(STATUS_TIMEOUT, reason=f"{name}: {e}")
            # DNS failure, refused connection, TLS error: try the next engine
            # rather than letting one unreachable host abort the whole search.
            log.debug("%s could not be loaded: %s", engine, name)
            return [], attempt(STATUS_UNREACHABLE, reason=f"{name}: {e}")

        reason = self._blocked_reason(page)
        if reason:
            log.info("%s blocked this request with a %s", engine, reason)
            return [], attempt(STATUS_BLOCKED, reason=reason)

        nodes = page.select(spec["result"])
        extracted: List[Dict] = []
        seen = set()
        for node in nodes:
            if len(extracted) >= limit:
                break
            item = self._extract_result(node, engine)
            if not item["url"] or not item["title"] or item["url"] in seen:
                continue
            seen.add(item["url"])
            extracted.append(self.result_processor(query, item))

        if not extracted:
            log.debug("%s returned no results for: %s", engine, query)
            return [], attempt(STATUS_EMPTY)
        return extracted, attempt(STATUS_OK, count=len(extracted))

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        engine: Optional[str] = None,
        fallback: Optional[bool] = None,
    ) -> SearchResponse:
        """Search `query`, walking the fallback chain until an engine answers.

        Returns a :class:`~headless.results.SearchResponse`, which behaves like
        the list of results it contains and additionally reports which engine
        answered and what every other engine did.

        `engine` starts the chain somewhere else; `fallback` overrides whether
        the rest of the chain is tried at all.
        """
        started = time.time()
        limit = self.max_results if max_results is None else max_results
        response = SearchResponse(query=query)
        if limit <= 0:
            self.last_response = response
            return response

        order = self._engine_order(engine, fallback)
        # Stale state must not survive a failed search: a caller reading
        # last_engine after an empty result would otherwise see the previous one.
        self.last_engine = None

        with self._lock:
            for name in order:
                results, attempt = self._search_one(name, query, limit)
                response.attempts.append(attempt)
                if results:
                    response.results = results
                    response.engine = name
                    self.last_engine = name
                    if self.keep_history:
                        self.results.extend(results)
                        if self.history_limit and len(self.results) > self.history_limit:
                            del self.results[:-self.history_limit]
                    log.debug("%s results from %s", len(results), name)
                    break

        response.elapsed = time.time() - started
        self.last_response = response
        if not response.results:
            if response.blocked:
                log.warning("every engine refused %r (%s)", query,
                            ", ".join(f"{a.engine}:{a.status}" for a in response.attempts))
                if self.raise_on_block:
                    raise AllEnginesBlocked(response)
            else:
                log.info("no results for %r (tried %s)", query, ", ".join(order))
        return response

    def search_batch(self, queries: List[str], max_workers: int = 4,
                     per_query: Optional[int] = None) -> Dict[str, SearchResponse]:
        """Search several queries.

        One scraper owns one WebDriver session, and a session is not thread-safe,
        so this is sequential by default. Pass ``max_workers > 1`` only when this
        scraper can use the HTTP transport for every engine in its chain, which
        has no such constraint. For browser engines use
        :class:`ScraperPool`, which gives each worker its own driver.
        """
        out: Dict[str, SearchResponse] = {}
        if not queries:
            return out
        if max_workers > 1 and not self._batch_is_parallel_safe():
            log.debug("running %s queries sequentially: a WebDriver session "
                      "cannot be shared across threads (use ScraperPool)",
                      len(queries))
            max_workers = 1
        max_workers = max(1, min(max_workers, len(queries)))

        if max_workers == 1:
            for q in queries:
                try:
                    out[q] = self.search(q, per_query)
                except Exception as e:
                    log.warning("query %r failed: %s", q, e)
                    out[q] = SearchResponse(query=q)
            return out

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.search, q, per_query): q for q in queries}
            for fut in as_completed(futures):
                q = futures[fut]
                try:
                    out[q] = fut.result()
                except Exception as e:
                    log.warning("query %r failed: %s", q, e)
                    out[q] = SearchResponse(query=q)
        return out

    def _batch_is_parallel_safe(self) -> bool:
        """True when no engine in the chain would need the shared browser."""
        if self.transport == "browser" or self.driver is not None:
            return False
        ok, _ = http_available()
        return ok and all(
            not self.engines[n].get("js", True)
            and str(self.engines[n].get("url", "")).startswith(("http://", "https://"))
            for n in self._engine_order())

    # -------------------------------------------------------------- output
    def export(self, path: str, results: Optional[Sequence[Dict]] = None) -> bool:
        """Write results to ``.json`` or ``.csv``.

        Writes `results` when given, otherwise the accumulated history (which is
        empty unless ``keep_history=True``), otherwise the last response.
        """
        if results is None:
            results = self.results or (
                self.last_response.results if self.last_response else [])
        results = list(results)
        lowered = path.lower()

        if lowered.endswith(".json"):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                log.error("JSON export failed: %s", e)
                return False
        if lowered.endswith(".csv"):
            if not results:
                log.warning("nothing to export to %s", path)
                return False
            # Preserve first-seen key order so CSV columns are stable across runs.
            keys: List[str] = []
            for r in results:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    for r in results:
                        writer.writerow({k: r.get(k, "") for k in keys})
                return True
            except Exception as e:
                log.error("CSV export failed: %s", e)
                return False
        log.error("unsupported export format: %s (use .json or .csv)", path)
        return False

    def quit(self):
        with self._lock:
            context, driver, http = self._driver_context, self.driver, self._http
            self._driver_context = None
            self.driver = None
            self._http = None
            self._timeouts_applied = False
        if context is not None:
            try:
                context.quit()
            except Exception as e:
                log.warning("error quitting driver: %s", e)
        elif driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if http is not None:
            http.close()

    def __enter__(self) -> "AdvancedSearchScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()


class ScraperPool:
    """A pool of scrapers, one browser per worker, for parallel searching.

    ``search_batch`` on a single scraper cannot be parallel, because one
    WebDriver session cannot be driven from several threads. This gives each
    worker its own scraper, and replaces one whose browser has died.

    ::

        with ScraperPool(size=4, proxy="http://…") as pool:
            for query, response in pool.map(queries):
                print(query, response.engine, len(response))
    """

    def __init__(self, size: int = 4, **scraper_kwargs):
        if size < 1:
            raise ValueError("pool size must be at least 1")
        self.size = size
        self.scraper_kwargs = scraper_kwargs
        self._local = threading.local()
        self._all: List[AdvancedSearchScraper] = []
        self._lock = threading.Lock()

    def _scraper(self) -> AdvancedSearchScraper:
        scraper = getattr(self._local, "scraper", None)
        if scraper is None:
            scraper = AdvancedSearchScraper(**self.scraper_kwargs)
            self._local.scraper = scraper
            with self._lock:
                self._all.append(scraper)
        return scraper

    def search(self, query: str, **kwargs) -> SearchResponse:
        """Search on this thread's own scraper."""
        scraper = self._scraper()
        try:
            return scraper.search(query, **kwargs)
        except Exception as e:
            # A dead browser must not take the whole pool with it.
            log.warning("search %r failed (%s); recycling this worker", query, e)
            scraper.recycle()
            raise

    def map(self, queries: Sequence[str], **kwargs):
        """Yield ``(query, response)`` as each search finishes."""
        queries = list(queries)
        if not queries:
            return
        workers = max(1, min(self.size, len(queries)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self.search, q, **kwargs): q for q in queries}
            for fut in as_completed(futures):
                query = futures[fut]
                try:
                    yield query, fut.result()
                except Exception:
                    yield query, SearchResponse(query=query)

    def search_batch(self, queries: Sequence[str], **kwargs) -> Dict[str, SearchResponse]:
        return dict(self.map(queries, **kwargs))

    def quit(self) -> None:
        with self._lock:
            scrapers, self._all = self._all, []
        for scraper in scrapers:
            try:
                scraper.quit()
            except Exception:
                pass
        self._local = threading.local()

    def __enter__(self) -> "ScraperPool":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()
