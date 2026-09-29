"""headless-driver: a fast multi-engine search scraper, and headless Chrome automation.

Scrape Brave, DuckDuckGo, Yahoo, Mojeek, Google, Bing, Startpage and Yandex
results with browser TLS impersonation, per-engine circuit breakers, block
detection, and consensus ranking across engines — no browser needed for most
engines — plus Selenium-driven Chrome for screenshots, PDFs and the rest.
"""

# Imported first: it sets up logging (and quiets an unactionable urllib3
# import-time warning) before Selenium pulls urllib3 in.
from .logs import (
    get_logger,
    enable_console_logging,
    disable_console_logging,
    colorize_logging,
    ColorFormatter,
)
from .core import (
    Headless,
    SearchScraper,
    find_chromedriver_path,
    find_chrome_binary,
    install_chromedriver,
    chrome_version,
    default_user_agent,
)
from .manager import ExtendedHeadless, MultiDriverManager
from .scraper import (
    AdvancedSearchScraper,
    ScraperPool,
    ENGINE_SPECS,
    DEFAULT_ENGINE,
    DEFAULT_FALLBACK_ENGINES,
    DEFAULT_AGGREGATE_ENGINES,
    merge_results,
    normalize_url,
    normalize_text,
)
from .results import (
    SearchResponse,
    EngineAttempt,
    AllEnginesBlocked,
    HeadlessDriverError,
    STATUS_OK,
    STATUS_EMPTY,
    STATUS_BLOCKED,
    STATUS_RATE_LIMITED,
    STATUS_UNPARSED,
    STATUS_TIMEOUT,
    STATUS_UNREACHABLE,
    STATUS_ERROR,
)
from .health import EngineHealth, default_health, reset_default_health
from .transport import ImpersonateTransport, HttpTransport, impersonate_available

try:  # pragma: no cover - trivial
    from importlib.metadata import version as _version, PackageNotFoundError
    __version__ = _version("headless-driver")
except Exception:  # pragma: no cover
    __version__ = "0.0.0.dev0"

__all__ = [
    "Headless",
    "SearchScraper",
    "ExtendedHeadless",
    "MultiDriverManager",
    "AdvancedSearchScraper",
    "ScraperPool",
    "SearchResponse",
    "EngineAttempt",
    "AllEnginesBlocked",
    "HeadlessDriverError",
    "EngineHealth",
    "default_health",
    "reset_default_health",
    "ImpersonateTransport",
    "HttpTransport",
    "impersonate_available",
    "merge_results",
    "normalize_url",
    "normalize_text",
    "find_chromedriver_path",
    "find_chrome_binary",
    "install_chromedriver",
    "chrome_version",
    "default_user_agent",
    "get_logger",
    "enable_console_logging",
    "disable_console_logging",
    "colorize_logging",
    "ColorFormatter",
    "ENGINE_SPECS",
    "DEFAULT_ENGINE",
    "DEFAULT_FALLBACK_ENGINES",
    "DEFAULT_AGGREGATE_ENGINES",
    "STATUS_OK",
    "STATUS_EMPTY",
    "STATUS_BLOCKED",
    "STATUS_RATE_LIMITED",
    "STATUS_UNPARSED",
    "STATUS_TIMEOUT",
    "STATUS_UNREACHABLE",
    "STATUS_ERROR",
    "__version__",
]
