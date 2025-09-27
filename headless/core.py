import os
import uuid
import shutil
import tempfile
from typing import List, Optional, Dict, Tuple, Callable


import sys
import platform
from selenium import webdriver
from urllib.parse import quote_plus
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC



def find_chromedriver_path() -> Optional[str]:
    """
    Try to find the chromedriver executable based on OS.
    Returns the path if found, else None.
    """
    import shutil
    # Common locations
    candidates = []
    system = platform.system().lower()
    if system == "linux":
        candidates = [
            shutil.which("chromedriver"),
            "/usr/bin/chromedriver",
            "/usr/local/bin/chromedriver",
        ]
    elif system == "darwin":
        candidates = [
            shutil.which("chromedriver"),
            "/opt/homebrew/bin/chromedriver",
            "/usr/local/bin/chromedriver",
        ]
    elif system == "windows":
        candidates = [
            shutil.which("chromedriver"),
            "C:\\Program Files\\ChromeDriver\\chromedriver.exe",
            "C:\\chromedriver\\chromedriver.exe",
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None

class Headless:
    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        window_size: Tuple[int, int] = (1920, 1080),
        user_agent: Optional[str] = None,
        headless: bool = True,
        chrome_driver_path: Optional[str] = '/opt/homebrew/bin/chromedriver',
        additional_args: Optional[List[str]] = None,
        remote_url: Optional[str] = None,
        verbose: bool = False,
    ):
        self.id = uuid.uuid4().hex
        self.verbose = verbose
        if user_data_dir:
            self.user_data_dir = user_data_dir
            self._cleanup_dir = False
        else:
            prefix = f"chrome-user-data-{self.id}-"
            self.user_data_dir = tempfile.mkdtemp(prefix=prefix)
            self._cleanup_dir = True

        self.window_size = window_size
        self.user_agent = (
            user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36"
        )
        self.headless = headless
        self.additional_args = additional_args or []
        self.chrome_driver_path = chrome_driver_path or find_chromedriver_path()
        self.remote_url = remote_url
        self._driver = None
        if self.verbose:
            print(f"[Headless] Initialized with user_data_dir={self.user_data_dir}, window_size={self.window_size}, headless={self.headless}")

    def _build_options(self) -> Options:
        if self.verbose:
            print("[Headless] Building Chrome options...")
        opts = Options()
        opts.add_argument(f"--user-data-dir={self.user_data_dir}")
        if self.headless:
            opts.add_argument("--headless=new")
            if self.verbose:
                print("[Headless] Headless mode enabled.")
        opts.add_argument(f"--window-size={self.window_size[0]},{self.window_size[1]}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument(f"user-agent={self.user_agent}")
        for arg in self.additional_args:
            opts.add_argument(arg)
            if self.verbose:
                print(f"[Headless] Additional Chrome arg: {arg}")
        return opts

    def get_driver(self) -> WebDriver:
        if self._driver:
            if self.verbose:
                print("[Headless] Returning cached WebDriver instance.")
            return self._driver

        opts = self._build_options()
        try:
            if self.remote_url:
                if self.verbose:
                    print(f"[Headless] Connecting to remote WebDriver at {self.remote_url}")
                self._driver = webdriver.Remote(
                    command_executor=self.remote_url,
                    options=opts
                )
            else:
                if self.chrome_driver_path:
                    if self.verbose:
                        print(f"[Headless] Using ChromeDriver at {self.chrome_driver_path}")
                    service = Service(executable_path=self.chrome_driver_path)
                    self._driver = webdriver.Chrome(
                        service=service,
                        options=opts
                    )
                else:
                    if self.verbose:
                        print("[Headless] Using default ChromeDriver.")
                    self._driver = webdriver.Chrome(
                        options=opts
                    )
            if self.verbose:
                print("[Headless] WebDriver started successfully.")
        except Exception as e:
            import traceback
            from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
            if self.verbose:
                print(f"[Headless] WebDriver startup failed: {e}")
            if isinstance(e, SessionNotCreatedException):
                print("Error: ChromeDriver and Chrome browser versions are incompatible. Please update ChromeDriver to match your browser version.")
            elif isinstance(e, WebDriverException):
                print(f"WebDriver error: {e}")
            else:
                print(f"Failed to start Chrome WebDriver: {e}\n{traceback.format_exc()}")
            self._driver = None
            raise
        return self._driver

    def quit(self) -> None:
        if self._driver:
            if self.verbose:
                print("[Headless] Quitting WebDriver...")
            try:
                self._driver.quit()
                if self.verbose:
                    print("[Headless] WebDriver quit successfully.")
            except Exception as e:
                print(f"Error quitting WebDriver: {e}")
            self._driver = None

        if self._cleanup_dir and os.path.isdir(self.user_data_dir):
            if self.verbose:
                print(f"[Headless] Cleaning up user data directory: {self.user_data_dir}")
            try:
                shutil.rmtree(self.user_data_dir, ignore_errors=True)
                if self.verbose:
                    print("[Headless] User data directory cleaned up.")
            except Exception as e:
                print(f"Error cleaning up user data directory: {e}")

    def __enter__(self) -> WebDriver:
        if self.verbose:
            print("[Headless] Entering context manager.")
        try:
            return self.get_driver()
        except Exception as e:
            print(f"Error entering context: {e}")
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.verbose:
            print("[Headless] Exiting context manager.")
        try:
            self.quit()
        except Exception as e:
            print(f"Error exiting context: {e}")

class SearchScraper:
    def __init__(
        self,
        driver=None,
        max_results: int = 10,
        result_processor: Optional[Callable[[str, str], Dict]] = None,
        headless_options: Optional[dict] = None,
        search_engine_url: str = "https://duckduckgo.com/?q={query}",
        verbose: bool = False
    ):
        self.driver = driver
        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = headless_options or {}
        self.search_engine_url = search_engine_url
        self.results: List[Dict] = []
        self.verbose = verbose
        if self.verbose:
            print(f"[SearchScraper] Initialized with max_results={self.max_results}, search_engine_url={self.search_engine_url}")

    def default_result_processor(self, url: str, snippet: str) -> Dict:
        return {"url": url, "snippet": snippet}

    def get_driver(self):
        if not self.driver:
            if self.verbose:
                print("[SearchScraper] Creating Headless driver...")
            from_headless = Headless(**self.headless_options, verbose=self.verbose)
            self.driver_context = from_headless
            self.driver = from_headless.get_driver()
            if self.verbose:
                print("[SearchScraper] Headless driver created.")
        return self.driver

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict]:
        if self.verbose:
            print(f"[SearchScraper] Searching for: {query}")
        driver = self.get_driver()
        max_results = max_results or self.max_results
        search_url = self.search_engine_url.format(query=quote_plus(query))
        if self.verbose:
            print(f"[SearchScraper] Navigating to: {search_url}")
        driver.get(search_url)

        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='result']"))
            )
            if self.verbose:
                print("[SearchScraper] Results loaded.")
        except TimeoutException:
            print(f"No results found for query: {query}")
            return []

        results_elements = driver.find_elements(By.CSS_SELECTOR, "[data-testid='result']")
        extracted_results: List[Tuple[str, str]] = []

        for elem in results_elements[:max_results]:
            try:
                link_elem = elem.find_element(By.CSS_SELECTOR, "[data-testid='result-title-a']")
                snippet_elem = elem.find_element(By.CSS_SELECTOR, "[data-result='snippet']")
                href = link_elem.get_attribute("href")
                snippet = snippet_elem.text if snippet_elem else ""
                if href:
                    extracted_results.append((href, snippet))
                    if self.verbose:
                        print(f"[SearchScraper] Found result: {href}")
            except Exception:
                continue

        unique_results = []
        seen = set()
        for url, snippet in extracted_results:
            if url not in seen:
                seen.add(url)
                unique_results.append(self.result_processor(url, snippet))
                if len(unique_results) >= max_results:
                    break

        self.results.extend(unique_results)
        if self.verbose:
            print(f"[SearchScraper] Returning {len(unique_results)} unique results.")
        return unique_results

    def quit(self):
        if self.verbose:
            print("[SearchScraper] Quitting driver...")
        if hasattr(self, "driver_context"):
            self.driver_context.quit()
            if self.verbose:
                print("[SearchScraper] Driver context quit.")
        elif self.driver:
            try:
                self.driver.quit()
                if self.verbose:
                    print("[SearchScraper] Driver quit.")
            except Exception:
                pass
