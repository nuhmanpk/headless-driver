import json
import csv
from typing import Optional, List, Dict, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from .core import SearchScraper, Headless

class AdvancedSearchScraper:
    def __init__(
        self,
        driver=None,
        max_results: int = 10,
        result_processor: Optional[Callable[[str, Dict[str, Any]], Dict]] = None,
        headless_options: Optional[dict] = None,
        search_engine: str = "duckduckgo",
    ):
        self.driver = driver
        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = headless_options or {}
        self.search_engine = search_engine.lower()
        self.results: List[Dict] = []

    def default_result_processor(self, query: str, item: Dict[str, Any]) -> Dict:
        return item

    def _engine_url(self, query: str) -> str:
        if self.search_engine == "duckduckgo":
            return f"https://duckduckgo.com/?q={query}"
        if self.search_engine == "google":
            return f"https://www.google.com/search?q={query}"
        if self.search_engine == "bing":
            return f"https://www.bing.com/search?q={query}"
        return f"https://duckduckgo.com/?q={query}"

    def _favicon_for(self, url: str) -> str:
        try:
            domain = urlparse(url).netloc
            return f"https://www.google.com/s2/favicons?domain={domain}"
        except Exception:
            return ""

    def _extract_from_ddg_result(self, elem) -> Dict:
        out = {"url": "", "title": "", "snippet": "", "favicon": "", "cached": None, "quick_answer": None}
        try:
            link_elem = elem.find_element(By.CSS_SELECTOR, "[data-testid='result-title-a']")
            out["url"] = link_elem.get_attribute("href") or ""
            out["title"] = link_elem.text or ""
        except Exception:
            pass
        try:
            snippet_elem = elem.find_element(By.CSS_SELECTOR, "[data-result='snippet']")
            out["snippet"] = snippet_elem.text or ""
        except Exception:
            pass
        try:
            out["favicon"] = self._favicon_for(out["url"]) if out["url"] else ""
        except Exception:
            out["favicon"] = ""
        try:
            cached = elem.find_elements(By.CSS_SELECTOR, "a.result__more-link")
            if cached:
                out["cached"] = cached[0].get_attribute("href")
        except Exception:
            out["cached"] = None
        return out

    def _get_driver(self):
        if self.driver:
            return self.driver
        hl = Headless(**self.headless_options)
        self._driver_context = hl
        self.driver = hl.get_driver()
        return self.driver

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict]:
        d = self._get_driver()
        max_results = max_results or self.max_results
        url = self._engine_url(query)
        d.get(url)
        try:
            WebDriverWait(d, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='result']")))
        except TimeoutException:
            return []
        elems = d.find_elements(By.CSS_SELECTOR, "[data-testid='result']")
        extracted = []
        for elem in elems[:max_results]:
            item = self._extract_from_ddg_result(elem) if self.search_engine == "duckduckgo" else {"url": "", "title": "", "snippet": ""}
            processed = self.result_processor(query, item)
            extracted.append(processed)
        self.results.extend(extracted)
        return extracted

    def search_batch(self, queries: List[str], max_workers: int = 4, per_query: Optional[int] = None) -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.search, q, per_query): q for q in queries}
            for fut in as_completed(futures):
                q = futures[fut]
                try:
                    out[q] = fut.result()
                except Exception:
                    out[q] = []
        return out

    def export(self, path: str) -> bool:
        if path.lower().endswith(".json"):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.results, f, ensure_ascii=False, indent=2)
                return True
            except Exception:
                return False
        if path.lower().endswith(".csv"):
            if not self.results:
                return False
            keys = set()
            for r in self.results:
                keys.update(r.keys())
            keys = list(keys)
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    for r in self.results:
                        writer.writerow({k: r.get(k, "") for k in keys})
                return True
            except Exception:
                return False
        return False

    def quit(self):
        if hasattr(self, "_driver_context"):
            self._driver_context.quit()
        elif self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
