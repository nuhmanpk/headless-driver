import csv
import json
import base64
import threading
from typing import Optional, List, Dict, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote_plus, parse_qs
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support import expected_conditions as EC
from .core import Headless

# Per-engine result container, title-anchor, and snippet selectors.
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q={query}",
        "result": "[data-testid='result']",
        "link": ["[data-testid='result-title-a']"],
        "title": ["[data-testid='result-title-a']"],
        "snippet": ["[data-result='snippet']"],
    },
    "google": {
        "url": "https://www.google.com/search?q={query}",
        "result": "div.g, div[data-hveid] > div > div > div > a[href]:not([href^='#'])",
        "link": ["a:has(h3)", "a[href^='http']"],
        "title": ["h3"],
        "snippet": ["div[data-sncf] span", "div[style*='webkit-line-clamp']", "span"],
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "result": "li.b_algo",
        "link": ["h2 a"],
        "title": ["h2"],
        "snippet": [".b_caption p", "p[class*='b_lineclamp']", ".b_algoSlug", "p"],
    },
}
DEFAULT_ENGINE = "duckduckgo"


class AdvancedSearchScraper:
    def __init__(
        self,
        driver=None,
        max_results: int = 10,
        result_processor: Optional[Callable[[str, Dict[str, Any]], Dict]] = None,
        headless_options: Optional[dict] = None,
        search_engine: str = DEFAULT_ENGINE,
        verbose: bool = False,
    ):
        self.driver = driver
        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = dict(headless_options or {})
        engine = (search_engine or DEFAULT_ENGINE).lower()
        if engine not in ENGINE_SPECS:
            raise ValueError(
                f"Unsupported search_engine {search_engine!r}; "
                f"expected one of {sorted(ENGINE_SPECS)}"
            )
        self.search_engine = engine
        self.verbose = verbose
        self.results: List[Dict] = []
        self._driver_context: Optional[Headless] = None
        self._lock = threading.Lock()

    def default_result_processor(self, query: str, item: Dict[str, Any]) -> Dict:
        return item

    def _spec(self) -> Dict[str, Any]:
        return ENGINE_SPECS[self.search_engine]

    def _engine_url(self, query: str) -> str:
        # The query must be percent-encoded or spaces and '&' corrupt the URL.
        return self._spec()["url"].replace("{query}", quote_plus(query))

    def _favicon_for(self, url: str) -> str:
        try:
            domain = urlparse(url).netloc
            return f"https://www.google.com/s2/favicons?domain={domain}" if domain else ""
        except Exception:
            return ""

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

    @staticmethod
    def _unwrap_redirect(href: str) -> str:
        """Recover the destination behind a Bing /ck/a tracking redirect."""
        if "bing.com/ck/a" not in href:
            return href
        try:
            target = parse_qs(urlparse(href).query).get("u", [""])[0]
            if not target.startswith("a1"):
                return href
            payload = target[2:]
            payload += "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
            return decoded if decoded.startswith(("http://", "https://")) else href
        except Exception:
            return href

    def _find_link(self, elem, selectors: List[str]) -> str:
        for sel in selectors:
            try:
                found = elem.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                # e.g. ':has()' on an engine/browser combination that lacks it
                continue
            for node in found:
                try:
                    href = node.get_attribute("href") or ""
                except WebDriverException:
                    continue
                if href.startswith(("http://", "https://")):
                    return self._unwrap_redirect(href)
        return ""

    def _extract_result(self, elem) -> Dict:
        spec = self._spec()
        out = {"url": "", "title": "", "snippet": "", "favicon": "", "cached": None, "quick_answer": None}

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

    # Interstitials engines serve instead of results when they flag automation.
    _BLOCK_SELECTORS = (
        "form#captcha-form",
        "div#recaptcha",
        "#challenge-running",
        ".anomaly-modal__title",
    )

    def _blocked_reason(self, d) -> str:
        try:
            current = d.current_url or ""
        except WebDriverException:
            return ""
        if "/sorry/" in current or "captcha" in current.lower():
            return f"bot-check page ({current[:80]})"
        for sel in self._BLOCK_SELECTORS:
            try:
                if d.find_elements(By.CSS_SELECTOR, sel):
                    return f"bot-check element {sel!r}"
            except WebDriverException:
                continue
        return ""

    def _get_driver(self):
        if self.driver:
            return self.driver
        options = dict(self.headless_options)
        options.setdefault("verbose", self.verbose)
        hl = Headless(**options)
        self._driver_context = hl
        self.driver = hl.get_driver()
        return self.driver

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict]:
        limit = self.max_results if max_results is None else max_results
        if limit <= 0:
            return []
        spec = self._spec()
        url = self._engine_url(query)

        # A WebDriver session is not thread-safe; serialise navigation + scraping.
        with self._lock:
            d = self._get_driver()
            if self.verbose:
                print(f"[AdvancedSearchScraper] {self.search_engine}: {url}")
            d.get(url)
            try:
                WebDriverWait(d, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, spec["result"]))
                )
            except TimeoutException:
                # An empty list is indistinguishable from "the engine blocked us",
                # which is the far more common cause; say which one happened.
                reason = self._blocked_reason(d)
                if reason:
                    print(
                        f"[AdvancedSearchScraper] {self.search_engine} blocked this "
                        f"request with a {reason}; no results for query: {query}"
                    )
                elif self.verbose:
                    print(f"[AdvancedSearchScraper] No results for query: {query}")
                return []
            elems = d.find_elements(By.CSS_SELECTOR, spec["result"])

            extracted: List[Dict] = []
            seen = set()
            for elem in elems:
                if len(extracted) >= limit:
                    break
                item = self._extract_result(elem)
                if not item["url"] or not item["title"] or item["url"] in seen:
                    continue
                seen.add(item["url"])
                extracted.append(self.result_processor(query, item))

            self.results.extend(extracted)
        return extracted

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
                    if self.verbose:
                        print(f"[AdvancedSearchScraper] Query {q!r} failed: {e}")
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
                if self.verbose:
                    print(f"[AdvancedSearchScraper] JSON export failed: {e}")
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
                if self.verbose:
                    print(f"[AdvancedSearchScraper] CSV export failed: {e}")
                return False
        if self.verbose:
            print(f"[AdvancedSearchScraper] Unsupported export format: {path}")
        return False

    def quit(self):
        if self._driver_context is not None:
            try:
                self._driver_context.quit()
            except Exception as e:
                print(f"Error quitting driver: {e}")
            self._driver_context = None
            self.driver = None
        elif self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def __enter__(self) -> "AdvancedSearchScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()
