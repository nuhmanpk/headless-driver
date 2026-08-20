import csv
import json
import base64
import threading
from typing import Optional, List, Dict, Callable, Any, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote_plus, parse_qs, unquote
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support import expected_conditions as EC
from .core import Headless
from .ui import diag

# Per-engine result container, link, title and snippet selectors.
#
# The DuckDuckGo entries come first because their endpoints render server-side:
# no JavaScript to execute means a page load measured in hundreds of
# milliseconds rather than the seconds the JS front end needs.
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={query}",
        "result": "div.result:not(.result--ad):not(.result--no-result)",
        "link": ["a.result__a"],
        "title": ["a.result__a"],
        "snippet": ["a.result__snippet", ".result__snippet"],
    },
    "duckduckgo_lite": {
        "url": "https://lite.duckduckgo.com/lite/?q={query}",
        "result": "table tr:has(a.result-link)",
        "link": ["a.result-link"],
        "title": ["a.result-link"],
        "snippet": ["td.result-snippet"],
    },
    "duckduckgo_js": {
        "url": "https://duckduckgo.com/?q={query}",
        "result": "[data-testid='result']",
        "link": ["[data-testid='result-title-a']"],
        "title": ["[data-testid='result-title-a']"],
        "snippet": ["[data-result='snippet']"],
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "result": "li.b_algo",
        "link": ["h2 a"],
        "title": ["h2"],
        "snippet": [".b_caption p", "p[class*='b_lineclamp']", ".b_algoSlug", "p"],
    },
    "mojeek": {
        "url": "https://www.mojeek.com/search?q={query}",
        "result": "ul.results-standard > li, li.result",
        "link": ["a.title", "h2 a"],
        "title": ["a.title", "h2"],
        "snippet": ["p.s", "p"],
    },
    "google": {
        "url": "https://www.google.com/search?q={query}",
        "result": "div.g, div[data-hveid] div[data-snf]",
        "link": ["a:has(h3)", "a[href^='http']"],
        "title": ["h3"],
        "snippet": ["div[data-sncf] span", "div[style*='webkit-line-clamp']", "span"],
    },
    "startpage": {
        "url": "https://www.startpage.com/sp/search?q={query}",
        "result": ".w-gl__result, .result",
        "link": ["a.w-gl__result-title", "a.result-link", "a[href^='http']"],
        "title": ["h3", ".w-gl__result-title"],
        "snippet": [".w-gl__description", "p.description"],
    },
    "yandex": {
        "url": "https://yandex.com/search/?text={query}",
        "result": "li.serp-item, .organic",
        "link": ["a.OrganicTitle-Link", "h2 a", "a[href^='http']"],
        "title": [".OrganicTitleContentSpan", "h2"],
        "snippet": [".OrganicTextContentSpan", ".TextContainer"],
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
    ):
        self.driver = driver
        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = dict(headless_options or {})
        self.engines: Dict[str, Dict[str, Any]] = dict(ENGINE_SPECS)
        self.search_engine = self._validate_engine(search_engine or DEFAULT_ENGINE)
        self.verbose = verbose
        self.fallback = fallback
        self.fallback_engines = list(
            DEFAULT_FALLBACK_ENGINES if fallback_engines is None else fallback_engines
        )
        self.page_load_timeout = page_load_timeout
        self.wait_timeout = wait_timeout
        self.results: List[Dict] = []
        # Which engine actually produced the most recent results.
        self.last_engine: Optional[str] = None
        self._driver_context: Optional[Headless] = None
        self._timeouts_applied = False
        self._lock = threading.Lock()

    def _validate_engine(self, engine: str) -> str:
        name = (engine or "").lower()
        if name not in self.engines:
            raise ValueError(
                f"Unsupported search_engine {engine!r}; "
                f"expected one of {sorted(self.engines)}"
            )
        return name

    def default_result_processor(self, query: str, item: Dict[str, Any]) -> Dict:
        return item

    def register_engine(self, name: str, spec: Dict[str, Any]) -> None:
        """Add or override an engine definition on this instance."""
        missing = {"url", "result", "link", "title", "snippet"} - set(spec)
        if missing:
            raise ValueError(f"engine spec missing keys: {sorted(missing)}")
        self.engines[name.lower()] = spec

    def _spec(self, engine: Optional[str] = None) -> Dict[str, Any]:
        return self.engines[engine or self.search_engine]

    def _engine_url(self, query: str, engine: Optional[str] = None) -> str:
        # The query must be percent-encoded or spaces and '&' corrupt the URL.
        return self._spec(engine)["url"].replace("{query}", quote_plus(query))

    def _engine_order(self, engine: Optional[str] = None) -> List[str]:
        primary = self._validate_engine(engine) if engine else self.search_engine
        order = [primary]
        if self.fallback and engine is None:
            for name in self.fallback_engines:
                name = name.lower()
                if name in self.engines and name not in order:
                    order.append(name)
        return order

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
    def _node_text(node) -> str:
        try:
            text = (node.text or "").strip()
        except WebDriverException:
            return ""
        if text:
            return text
        # `.text` yields "" for anything Selenium deems not displayed, which many
        # engines' result headings are; textContent still has the real string.
        try:
            return " ".join(((node.get_attribute("textContent") or "").split()))
        except WebDriverException:
            return ""

    def _first_text(self, elem, selectors: List[str]) -> str:
        for sel in selectors:
            try:
                found = elem.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                continue
            for node in found:
                text = self._node_text(node)
                if text:
                    return text
        return ""

    def _find_link(self, elem, selectors: List[str]) -> str:
        for sel in selectors:
            try:
                found = elem.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                # e.g. ':has()' on a browser build that lacks support for it
                continue
            for node in found:
                try:
                    href = node.get_attribute("href") or ""
                except WebDriverException:
                    continue
                if href.startswith(("http://", "https://")):
                    return self._unwrap_redirect(href)
        return ""

    def _extract_result(self, elem, engine: str) -> Dict:
        spec = self._spec(engine)
        out = {"url": "", "title": "", "snippet": "", "favicon": "",
               "cached": None, "quick_answer": None, "engine": engine}

        href = self._find_link(elem, spec["link"])
        if not href:
            # Last resort: the first plain outbound link in the container.
            href = self._find_link(elem, ["a[href]"])
        out["url"] = href
        out["title"] = self._first_text(elem, spec["title"])
        out["snippet"] = self._first_text(elem, spec["snippet"])
        out["favicon"] = self._favicon_for(href) if href else ""

        try:
            cached = elem.find_elements(By.CSS_SELECTOR, "a.result__more-link")
            if cached:
                out["cached"] = cached[0].get_attribute("href")
        except WebDriverException:
            pass
        return out

    def _blocked_reason(self, d) -> str:
        try:
            current = (d.current_url or "").lower()
        except WebDriverException:
            return ""
        for marker in self._BLOCK_URL_MARKERS:
            if marker in current:
                return f"bot-check page ({current[:80]})"
        for sel in self._BLOCK_SELECTORS:
            try:
                if d.find_elements(By.CSS_SELECTOR, sel):
                    return f"bot-check element {sel!r}"
            except WebDriverException:
                continue
        return ""

    def _get_driver(self):
        if not self.driver:
            options = dict(self.headless_options)
            options.setdefault("verbose", self.verbose)
            hl = Headless(**options)
            self._driver_context = hl
            self.driver = hl.get_driver()
        if not self._timeouts_applied:
            # Without this a wedged page load blocks for Selenium's 300s default,
            # which is what made a failing search appear to hang forever.
            try:
                self.driver.set_page_load_timeout(self.page_load_timeout)
                self.driver.set_script_timeout(self.page_load_timeout)
            except WebDriverException:
                pass
            self._timeouts_applied = True
        return self.driver

    def _log(self, message: str) -> None:
        if self.verbose:
            diag(f"[AdvancedSearchScraper] {message}")

    def _search_one(self, d, engine: str, query: str, limit: int) -> List[Dict]:
        """Scrape a single engine. Returns [] when blocked, slow or empty."""
        url = self._engine_url(query, engine)
        self._log(f"{engine}: {url}")
        try:
            d.get(url)
        except TimeoutException:
            self._log(f"{engine} exceeded the {self.page_load_timeout}s page load timeout")
            try:
                d.execute_script("window.stop();")
            except WebDriverException:
                pass
            return []
        except WebDriverException as e:
            # DNS failure, refused connection, TLS error: try the next engine
            # rather than letting one unreachable host abort the whole search.
            self._log(f"{engine} could not be loaded: {type(e).__name__}")
            return []

        spec = self._spec(engine)
        try:
            WebDriverWait(d, self.wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, spec["result"]))
            )
        except (TimeoutException, WebDriverException):
            # An empty list is indistinguishable from "the engine blocked us",
            # which is the far more common cause; say which one happened.
            reason = self._blocked_reason(d)
            if reason:
                self._log(f"{engine} blocked this request with a {reason}")
            else:
                self._log(f"{engine} returned no results for: {query}")
            return []

        try:
            elems = d.find_elements(By.CSS_SELECTOR, spec["result"])
        except WebDriverException as e:
            self._log(f"{engine} result lookup failed: {e}")
            return []

        extracted: List[Dict] = []
        seen = set()
        for elem in elems:
            if len(extracted) >= limit:
                break
            item = self._extract_result(elem, engine)
            if not item["url"] or not item["title"] or item["url"] in seen:
                continue
            seen.add(item["url"])
            extracted.append(self.result_processor(query, item))
        return extracted

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        engine: Optional[str] = None,
    ) -> List[Dict]:
        """Search `query`, walking the fallback chain until an engine answers.

        Pass `engine` to force one engine and skip the fallback chain entirely.
        """
        limit = self.max_results if max_results is None else max_results
        if limit <= 0:
            return []
        order = self._engine_order(engine)

        # A WebDriver session is not thread-safe; serialise navigation + scraping.
        with self._lock:
            d = self._get_driver()
            for name in order:
                extracted = self._search_one(d, name, query, limit)
                if extracted:
                    self.last_engine = name
                    self.results.extend(extracted)
                    self._log(f"{len(extracted)} results from {name}")
                    return extracted
            diag(
                f"No results for query: {query} "
                f"(tried {', '.join(order)})"
            )
            return []

    def search_batch(self, queries: List[str], max_workers: int = 4, per_query: Optional[int] = None) -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        if not queries:
            return out
        max_workers = max(1, min(max_workers, len(queries)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.search, q, per_query): q for q in queries}
            for fut in as_completed(futures):
                q = futures[fut]
                try:
                    out[q] = fut.result()
                except Exception as e:
                    self._log(f"Query {q!r} failed: {e}")
                    out[q] = []
        return out

    def export(self, path: str) -> bool:
        lowered = path.lower()
        if lowered.endswith(".json"):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.results, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                self._log(f"JSON export failed: {e}")
                return False
        if lowered.endswith(".csv"):
            if not self.results:
                return False
            # Preserve first-seen key order so CSV columns are stable across runs.
            keys: List[str] = []
            for r in self.results:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    for r in self.results:
                        writer.writerow({k: r.get(k, "") for k in keys})
                return True
            except Exception as e:
                self._log(f"CSV export failed: {e}")
                return False
        self._log(f"Unsupported export format: {path}")
        return False

    def quit(self):
        if self._driver_context is not None:
            try:
                self._driver_context.quit()
            except Exception as e:
                diag(f"Error quitting driver: {e}")
            self._driver_context = None
            self.driver = None
        elif self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
        self._timeouts_applied = False

    def __enter__(self) -> "AdvancedSearchScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()
