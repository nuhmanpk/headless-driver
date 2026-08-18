import os
import uuid
import shutil
import platform
import tempfile
from typing import List, Optional, Dict, Tuple, Callable

from selenium import webdriver
from urllib.parse import quote_plus
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    SessionNotCreatedException,
)


def install_chromedriver() -> Optional[str]:
    """Download a ChromeDriver matching the installed Chrome.

    Returns the path, or None when webdriver-manager is unavailable.
    """
    try:
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        return None
    return ChromeDriverManager().install()


def find_chromedriver_path() -> Optional[str]:
    """
    Try to find the chromedriver executable based on OS.
    Returns the path if found, else None.
    """
    system = platform.system().lower()
    candidates = [shutil.which("chromedriver")]
    if system == "linux":
        candidates += [
            "/usr/bin/chromedriver",
            "/usr/local/bin/chromedriver",
            "/usr/lib/chromium-browser/chromedriver",
        ]
    elif system == "darwin":
        candidates += [
            "/opt/homebrew/bin/chromedriver",
            "/usr/local/bin/chromedriver",
        ]
    elif system == "windows":
        candidates += [
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
        chrome_driver_path: Optional[str] = None,
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

        if not (isinstance(window_size, (tuple, list)) and len(window_size) == 2):
            raise ValueError("window_size must be a (width, height) pair")
        self.window_size = (int(window_size[0]), int(window_size[1]))

        self.user_agent = (
            user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36"
        )
        self.headless = headless
        self.additional_args = list(additional_args or [])
        # Fall back to auto-detection only when no explicit path was given.
        self.chrome_driver_path = chrome_driver_path or find_chromedriver_path()
        # A path we guessed may be a stale major version; one we were handed is
        # the caller's choice and must not be silently replaced.
        self._driver_path_is_guess = not chrome_driver_path
        self.remote_url = remote_url
        self._driver: Optional[WebDriver] = None
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
        opts.add_argument(f"--user-agent={self.user_agent}")
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
            elif self.chrome_driver_path:
                if self.verbose:
                    print(f"[Headless] Using ChromeDriver at {self.chrome_driver_path}")
                try:
                    service = Service(executable_path=self.chrome_driver_path)
                    self._driver = webdriver.Chrome(service=service, options=opts)
                except SessionNotCreatedException:
                    # An explicit path is the caller's choice; only retry a guess.
                    # Selenium Manager is no help here because it also honours the
                    # mismatched driver on PATH, so fetch a matching build instead.
                    replacement = None
                    if self._driver_path_is_guess:
                        try:
                            replacement = install_chromedriver()
                        except Exception:
                            # Report the version mismatch, not the download failure.
                            replacement = None
                    if not replacement or replacement == self.chrome_driver_path:
                        raise
                    print(
                        f"ChromeDriver at {self.chrome_driver_path} is incompatible with "
                        f"the installed Chrome; using {replacement} instead."
                    )
                    self.chrome_driver_path = replacement
                    service = Service(executable_path=replacement)
                    self._driver = webdriver.Chrome(
                        service=service, options=self._build_options()
                    )
            else:
                if self.verbose:
                    print("[Headless] Using default ChromeDriver (Selenium Manager).")
                self._driver = webdriver.Chrome(options=opts)
            if self.verbose:
                print("[Headless] WebDriver started successfully.")
        except Exception as e:
            import traceback
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
            finally:
                self._driver = None

        if getattr(self, "_cleanup_dir", False) and os.path.isdir(self.user_data_dir):
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
        except Exception:
            # Never leak the temp profile dir when startup fails.
            self.quit()
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
        self.headless_options = dict(headless_options or {})
        self.search_engine_url = search_engine_url
        self.results: List[Dict] = []
        self.verbose = verbose
        self.driver_context: Optional[Headless] = None
        if self.verbose:
            print(f"[SearchScraper] Initialized with max_results={self.max_results}, search_engine_url={self.search_engine_url}")

    def default_result_processor(self, url: str, snippet: str) -> Dict:
        return {"url": url, "snippet": snippet}

    def get_driver(self):
        if not self.driver:
            if self.verbose:
                print("[SearchScraper] Creating Headless driver...")
            options = dict(self.headless_options)
            # Caller-supplied verbose in headless_options wins; avoid duplicate kwarg.
            options.setdefault("verbose", self.verbose)
            from_headless = Headless(**options)
            self.driver_context = from_headless
            self.driver = from_headless.get_driver()
            if self.verbose:
                print("[SearchScraper] Headless driver created.")
        return self.driver

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict]:
        if self.verbose:
            print(f"[SearchScraper] Searching for: {query}")
        driver = self.get_driver()
        max_results = self.max_results if max_results is None else max_results
        if max_results <= 0:
            return []
        search_url = self.search_engine_url.replace("{query}", quote_plus(query))
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

        unique_results: List[Dict] = []
        seen = set()
        for elem in results_elements:
            if len(unique_results) >= max_results:
                break
            try:
                link_elem = elem.find_element(By.CSS_SELECTOR, "[data-testid='result-title-a']")
                href = link_elem.get_attribute("href")
            except Exception:
                continue
            if not href or href in seen:
                continue
            # A missing snippet must not discard an otherwise valid result.
            snippet_elems = elem.find_elements(By.CSS_SELECTOR, "[data-result='snippet']")
            snippet = snippet_elems[0].text if snippet_elems else ""
            seen.add(href)
            unique_results.append(self.result_processor(href, snippet))
            if self.verbose:
                print(f"[SearchScraper] Found result: {href}")

        self.results.extend(unique_results)
        if self.verbose:
            print(f"[SearchScraper] Returning {len(unique_results)} unique results.")
        return unique_results

    def quit(self):
        if self.verbose:
            print("[SearchScraper] Quitting driver...")
        if self.driver_context is not None:
            try:
                self.driver_context.quit()
            except Exception as e:
                print(f"Error quitting driver: {e}")
            self.driver_context = None
            self.driver = None
            if self.verbose:
                print("[SearchScraper] Driver context quit.")
        elif self.driver:
            try:
                self.driver.quit()
                if self.verbose:
                    print("[SearchScraper] Driver quit.")
            except Exception:
                pass
            self.driver = None
