import os
import re
import uuid
import subprocess
import shutil
import platform
import tempfile
from typing import List, Optional, Dict, Tuple, Callable

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import WebDriverException, SessionNotCreatedException
from .logs import get_logger, enable_console_logging
from .results import SearchResponse

log = get_logger("core")


def install_chromedriver() -> Optional[str]:
    """Download a ChromeDriver matching the installed Chrome.

    Returns the path, or None when webdriver-manager is unavailable.
    """
    try:
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        return None
    return ChromeDriverManager().install()


#: Used when the installed Chrome's version cannot be determined.
FALLBACK_CHROME_VERSION = "140.0.0.0"

_user_agent_cache: Optional[str] = None


def _platform_token() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    if system == "windows":
        return "Windows NT 10.0; Win64; x64"
    return "X11; Linux x86_64"


def chrome_version(binary: Optional[str] = None) -> Optional[str]:
    """Version of the installed Chrome, e.g. ``"152.0.7977.64"``."""
    binary = binary or find_chrome_binary()
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "--version"], capture_output=True,
                             text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", (out.stdout or out.stderr or ""))
    return match.group(1) if match else None


def default_user_agent() -> str:
    """A User-Agent matching the browser and OS actually in use.

    A UA that disagrees with the browser sending it is a stronger bot signal
    than no override at all: Client Hints, ``navigator.userAgentData`` and the
    TLS fingerprint all contradict it. So the version comes from the installed
    Chrome and the platform token from this machine, rather than being frozen at
    whatever was current when this was written. Computed once per process.
    """
    global _user_agent_cache
    if _user_agent_cache is None:
        version = chrome_version() or FALLBACK_CHROME_VERSION
        _user_agent_cache = (
            f"Mozilla/5.0 ({_platform_token()}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")
    return _user_agent_cache


def find_chrome_binary() -> Optional[str]:
    """Locate the installed Chrome/Chromium executable, or None."""
    system = platform.system().lower()
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    if system == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "linux":
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]
    elif system == "windows":
        candidates += [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


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
        page_load_timeout: Optional[float] = 30.0,
        proxy: Optional[str] = None,
    ):
        self.id = uuid.uuid4().hex
        self.verbose = verbose
        if verbose:
            # The caller explicitly asking for output is the only circumstance
            # in which a library may write to the console.
            enable_console_logging()
        self.proxy = proxy
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

        self.user_agent = user_agent or default_user_agent()
        self.headless = headless
        self.additional_args = list(additional_args or [])
        # Fall back to auto-detection only when no explicit path was given.
        self.chrome_driver_path = chrome_driver_path or find_chromedriver_path()
        # A path we guessed may be a stale major version; one we were handed is
        # the caller's choice and must not be silently replaced.
        self._driver_path_is_guess = not chrome_driver_path
        self.remote_url = remote_url
        # Selenium defaults to 300s, so one wedged page load looks like a hang.
        self.page_load_timeout = page_load_timeout
        self._driver: Optional[WebDriver] = None
        if self.verbose:
            log.debug(f"[Headless] Initialized with user_data_dir={self.user_data_dir}, window_size={self.window_size}, headless={self.headless}")

    def _build_options(self) -> Options:
        if self.verbose:
            log.debug("[Headless] Building Chrome options...")
        opts = Options()
        opts.add_argument(f"--user-data-dir={self.user_data_dir}")
        if self.headless:
            opts.add_argument("--headless=new")
            if self.verbose:
                log.debug("[Headless] Headless mode enabled.")
        opts.add_argument(f"--window-size={self.window_size[0]},{self.window_size[1]}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument(f"--user-agent={self.user_agent}")
        if self.proxy:
            opts.add_argument(f"--proxy-server={self.proxy}")
        for arg in self.additional_args:
            opts.add_argument(arg)
            if self.verbose:
                log.debug(f"[Headless] Additional Chrome arg: {arg}")
        return opts

    def get_driver(self) -> WebDriver:
        if self._driver:
            if self.verbose:
                log.debug("[Headless] Returning cached WebDriver instance.")
            return self._driver

        opts = self._build_options()
        try:
            if self.remote_url:
                if self.verbose:
                    log.debug(f"[Headless] Connecting to remote WebDriver at {self.remote_url}")
                self._driver = webdriver.Remote(
                    command_executor=self.remote_url,
                    options=opts
                )
            elif self.chrome_driver_path:
                if self.verbose:
                    log.debug(f"[Headless] Using ChromeDriver at {self.chrome_driver_path}")
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
                    log.warning(
                        "ChromeDriver at %s is incompatible with the installed "
                        "Chrome; using %s instead.",
                        self.chrome_driver_path, replacement,
                    )
                    self.chrome_driver_path = replacement
                    service = Service(executable_path=replacement)
                    self._driver = webdriver.Chrome(
                        service=service, options=self._build_options()
                    )
            else:
                if self.verbose:
                    log.warning("[Headless] Using default ChromeDriver (Selenium Manager).")
                self._driver = webdriver.Chrome(options=opts)
            self._apply_timeouts(self._driver)
            if self.verbose:
                log.debug("[Headless] WebDriver started successfully.")
        except Exception as e:
            import traceback
            if self.verbose:
                log.debug(f"[Headless] WebDriver startup failed: {e}")
            if isinstance(e, SessionNotCreatedException):
                log.error("ChromeDriver and Chrome browser versions are incompatible. "
                          "Please update ChromeDriver to match your browser version.")
            elif isinstance(e, WebDriverException):
                log.error(f"WebDriver error: {e}")
            else:
                log.error(f"Failed to start Chrome WebDriver: {e}\n{traceback.format_exc()}")
            self._driver = None
            raise
        return self._driver

    def _apply_timeouts(self, driver: Optional[WebDriver]) -> None:
        if not driver or not self.page_load_timeout:
            return
        try:
            driver.set_page_load_timeout(self.page_load_timeout)
            driver.set_script_timeout(self.page_load_timeout)
        except WebDriverException:
            # Not fatal: the driver just keeps its own default timeouts.
            if self.verbose:
                log.debug("[Headless] Could not set page load timeout.")

    def quit(self) -> None:
        if self._driver:
            if self.verbose:
                log.debug("[Headless] Quitting WebDriver...")
            try:
                self._driver.quit()
                if self.verbose:
                    log.debug("[Headless] WebDriver quit successfully.")
            except Exception as e:
                log.debug(f"Error quitting WebDriver: {e}")
            finally:
                self._driver = None

        if getattr(self, "_cleanup_dir", False) and os.path.isdir(self.user_data_dir):
            if self.verbose:
                log.warning(f"[Headless] Cleaning up user data directory: {self.user_data_dir}")
            try:
                shutil.rmtree(self.user_data_dir, ignore_errors=True)
                if self.verbose:
                    log.debug("[Headless] User data directory cleaned up.")
            except Exception as e:
                log.debug(f"Error cleaning up user data directory: {e}")

    def __enter__(self) -> WebDriver:
        if self.verbose:
            log.warning("[Headless] Entering context manager.")
        try:
            return self.get_driver()
        except Exception:
            # Never leak the temp profile dir when startup fails.
            self.quit()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.verbose:
            log.debug("[Headless] Exiting context manager.")
        try:
            self.quit()
        except Exception as e:
            log.debug(f"Error exiting context: {e}")


class SearchScraper:
    """Simple ``{"url", "snippet"}`` search results.

    Scraping is delegated to :class:`~headless.scraper.AdvancedSearchScraper`, so
    this class inherits its engine fallback chain and page load timeouts.
    """

    def __init__(
        self,
        driver=None,
        max_results: int = 10,
        result_processor: Optional[Callable[[str, str], Dict]] = None,
        headless_options: Optional[dict] = None,
        search_engine_url: Optional[str] = None,
        verbose: bool = False,
        fallback: bool = True,
        page_load_timeout: float = 20.0,
        wait_timeout: float = 8.0,
        proxy: Optional[str] = None,
        transport: str = "auto",
        keep_history: bool = False,
    ):
        # Imported here because scraper.py imports this module.
        from .scraper import AdvancedSearchScraper

        self.max_results = max_results
        self.result_processor = result_processor or self.default_result_processor
        self.headless_options = dict(headless_options or {})
        self.search_engine_url = search_engine_url
        self.verbose = verbose
        self.keep_history = keep_history
        self.results: List[Dict] = []

        self._scraper = AdvancedSearchScraper(
            driver=driver,
            max_results=max_results,
            headless_options=self.headless_options,
            verbose=verbose,
            fallback=fallback,
            page_load_timeout=page_load_timeout,
            wait_timeout=wait_timeout,
            proxy=proxy,
            transport=transport,
            keep_history=keep_history,
        )
        if search_engine_url:
            # A caller-supplied URL is scraped with the DuckDuckGo JS front end's
            # selectors, which is what this class targeted before.
            spec = dict(self._scraper.engines["duckduckgo_js"])
            spec["url"] = search_engine_url
            self._scraper.register_engine("custom", spec)
            self._scraper.search_engine = "custom"
            # A one-off URL has no meaningful fallback chain.
            self._scraper.fallback = False

    def default_result_processor(self, url: str, snippet: str) -> Dict:
        return {"url": url, "snippet": snippet}

    @property
    def driver(self):
        return self._scraper.driver

    @property
    def last_engine(self) -> Optional[str]:
        return self._scraper.last_engine

    @property
    def last_response(self):
        return self._scraper.last_response

    def recycle(self) -> None:
        """Discard the browser; the next search builds a fresh one."""
        self._scraper.recycle()

    def get_driver(self) -> WebDriver:
        return self._scraper._get_driver()

    def search(self, query: str, max_results: Optional[int] = None,
               engine: Optional[str] = None,
               fallback: Optional[bool] = None) -> SearchResponse:
        """Search, returning ``{"url", "snippet"}`` results.

        The return value is a :class:`~headless.results.SearchResponse`, so it
        still iterates as a list of results while also reporting whether every
        engine refused the query.
        """
        log.debug("[SearchScraper] Searching for: %s", query)
        response = self._scraper.search(query, max_results, engine, fallback)
        response.results = [self.result_processor(i["url"], i["snippet"])
                            for i in response.results]
        if self.keep_history:
            self.results.extend(response.results)
        log.debug("[SearchScraper] Returning %s results from %s.",
                  len(response.results), response.engine)
        return response

    def quit(self) -> None:
        log.debug("[SearchScraper] Quitting driver...")
        self._scraper.quit()

    def __enter__(self) -> "SearchScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()
