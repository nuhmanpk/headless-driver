"""headless-driver: headless Chrome automation and multi-engine search scraping."""

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
)
from .results import (
    SearchResponse,
    EngineAttempt,
    AllEnginesBlocked,
    HeadlessDriverError,
)
from .logs import get_logger, enable_console_logging, disable_console_logging

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
    "find_chromedriver_path",
    "find_chrome_binary",
    "install_chromedriver",
    "chrome_version",
    "default_user_agent",
    "get_logger",
    "enable_console_logging",
    "disable_console_logging",
    "ENGINE_SPECS",
    "DEFAULT_ENGINE",
    "DEFAULT_FALLBACK_ENGINES",
    "__version__",
]
