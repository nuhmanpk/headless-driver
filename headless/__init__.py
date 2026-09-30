"""headless-driver: web search, page reading and browser automation for Python and AI agents.

Search Brave, DuckDuckGo, Yahoo, Mojeek, Google and Bing without an API key,
read pages as clean Markdown, and hand both to an agent as tools (OpenAI,
Anthropic, LangChain, LlamaIndex, CrewAI) or over MCP — with browser TLS
impersonation, consensus ranking, honest block detection, caching and
Selenium or Playwright automation underneath.
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
from .models import SearchResult
from .cache import SearchCache, MemoryCache, SQLiteCache, RedisCache, make_cache
from .markdown import fetch_markdown, html_to_markdown, chunk_markdown, count_tokens, MarkdownDocument, Chunk
from .tools import Toolkit
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
    "SearchResult",
    "SearchCache",
    "MemoryCache",
    "SQLiteCache",
    "RedisCache",
    "make_cache",
    "fetch_markdown",
    "html_to_markdown",
    "chunk_markdown",
    "count_tokens",
    "MarkdownDocument",
    "Chunk",
    "Toolkit",
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
