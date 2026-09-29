import csv
import json
import html
import time
import base64
import random
import secrets
import threading
import unicodedata
from typing import Optional, List, Dict, Callable, Any, Sequence, Set, Tuple
from concurrent.futures import (
    ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED,
)
from urllib.parse import (
    urlparse, urlunparse, quote_plus, parse_qs, parse_qsl, unquote, unquote_plus,
    urlencode,
)

from .core import Headless
from .logs import get_logger, enable_console_logging
from .health import EngineHealth, default_health
from .results import (
    EngineAttempt, SearchResponse, AllEnginesBlocked,
    STATUS_OK, STATUS_EMPTY, STATUS_BLOCKED, STATUS_RATE_LIMITED, STATUS_UNPARSED,
    STATUS_TIMEOUT, STATUS_UNREACHABLE, STATUS_ERROR, REFUSAL_BY_ENGINE,
    SKIP_COOLING, SKIP_IGNORES_SITE, SKIP_BROWSER_WITHDRAWN, SKIP_DUPLICATE_PROVIDER,
)
from .transport import (
    BrowserTransport, HttpTransport, ImpersonateTransport, http_available,
    impersonate_available,
)

log = get_logger("scraper")

#: Transports that fetch without a browser.
BROWSERLESS_TRANSPORTS = ("impersonate", "http")
TRANSPORTS = ("auto", "impersonate", "http", "browser")

#: Responses that mean "refused", whatever the body looks like.
BLOCK_HTTP_STATUSES = frozenset({401, 403, 407, 503})


# ------------------------------------------------------------------ helpers
def split_region(region: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``"uk-en"`` → ``("uk", "en")``; ``None`` → ``(None, None)``."""
    if not region:
        return None, None
    country, _, lang = region.lower().partition("-")
    if country in ("wt", "xa", "xx"):   # "worldwide" in DuckDuckGo's scheme
        country = None
    return country or None, lang or "en"


def _iso_country(country: Optional[str]) -> Optional[str]:
    # The DuckDuckGo-style regions use "uk"; ISO 3166 (Google, Brave) says "gb".
    return {"uk": "gb"}.get(country or "", country)


def android_gsa_user_agent() -> str:
    """An old Android Chrome webview carrying the Google Search App token.

    Google serves this client a basic, server-rendered results page instead of
    its JavaScript challenge. The ``NSTNWV`` suffix is what marks the Search
    App's webview; the Chrome build is deliberately old.
    """
    devices = (
        ("5.0", "SM-G900P Build/LRX21T"),
        ("6.0", "Nexus 5 Build/MRA58N"),
        ("8.0", "Pixel 2 Build/OPD3.170816.012"),
    )
    android, device = random.choice(devices)
    chrome = f"{random.randint(39, 60)}.0.{random.randint(1000, 9999)}.{random.randint(1000, 1999)}"
    return (f"Mozilla/5.0 (Linux; Android {android}; {device}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36"
            + bytes.fromhex("4e53544e5756").decode())


def unwrap_yahoo(href: str) -> str:
    """Yahoo wraps every result as ``…/RU=<target>/RK=…/RS=…``."""
    if "/RU=" not in href:
        return href
    target = href.split("/RU=", 1)[1]
    target = target.split("/RK=", 1)[0].split("/RS=", 1)[0]
    target = unquote_plus(target)
    return target if target.startswith(("http://", "https://")) else href


def unwrap_google(href: str) -> str:
    """Google's basic page links every result as ``/url?q=<target>&sa=…``."""
    try:
        parts = urlparse(href)
    except Exception:
        return href
    if parts.path not in ("/url", "/imgres"):
        return href
    params = parse_qs(parts.query)
    target = (params.get("q") or params.get("url") or [""])[0]
    return target if target.startswith(("http://", "https://")) else href


def normalize_text(text: str) -> str:
    """Unescape entities, NFC-normalise, drop control and zero-width
    characters, collapse whitespace — so names compare equal across engines."""
    if not text:
        return ""
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text
                   if not unicodedata.category(ch).startswith("C") or ch in "\t\n\r ")
    return " ".join(text.split())


def tidy_url(url: str) -> str:
    """Percent-decode a URL's path for readability, where that is lossless."""
    try:
        parts = urlparse(url)
    except Exception:
        return url
    if "%" not in parts.path:
        return url
    path = unquote(parts.path)
    # Decoding must not change the URL's meaning or make it invalid.
    if any(ch in path for ch in "?#%") or any(ch.isspace() for ch in path):
        return url
    return urlunparse(parts._replace(path=path))


#: Query parameters that identify a click, not a page.
TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid", "mc_eid",
    "_hsenc", "_hsmi", "ref", "ref_src", "trk", "trkinfo", "originalsubdomain",
    "si", "spm",
})

#: Sites that serve the same page on per-country subdomains
#: (``uk.linkedin.com/in/x`` is ``www.linkedin.com/in/x``).
COUNTRY_MIRRORED_DOMAINS = {"linkedin.com"}


def normalize_url(url: str) -> str:
    """A key under which the same page from different engines compares equal.

    Lower-cases the host and drops ``www.``/``m.``, country mirrors of the
    domains in :data:`COUNTRY_MIRRORED_DOMAINS`, the scheme, default ports,
    fragments, trailing slashes and tracking parameters. Not a URL to fetch.
    """
    try:
        parts = urlparse(url.strip())
    except Exception:
        return url
    host = (parts.netloc or "").lower().split("@")[-1]
    for port in (":80", ":443"):
        if host.endswith(port):
            host = host[: -len(port)]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[0]) == 2 and ".".join(labels[1:]) in COUNTRY_MIRRORED_DOMAINS:
        host = ".".join(labels[1:])
    path = unquote(parts.path or "").rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS)
    return host + path + (("?" + urlencode(query)) if query else "")


def merge_results(per_engine: Dict[str, List[Dict[str, Any]]],
                  engine_order: Sequence[str],
                  normalize: Callable[[str], str] = normalize_url) -> List[Dict[str, Any]]:
    """Merge several engines' results, ranking by how many engines agree.

    Each merged result carries ``votes`` (engines that returned it),
    ``engines`` and ``ranks`` (its 1-based position in each). The copy kept is
    the best-ranked one, with the longest snippet any engine offered. Ordered
    by votes, then mean rank, then by `engine_order` as a tiebreak.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    priority = {name: i for i, name in enumerate(engine_order)}
    for engine in sorted(per_engine, key=lambda e: priority.get(e, len(priority))):
        for rank, item in enumerate(per_engine[engine], 1):
            url = item.get("url") or ""
            if not url:
                continue
            key = normalize(url)
            entry = merged.get(key)
            if entry is None:
                entry = dict(item)
                entry["engines"], entry["ranks"] = [], {}
                merged[key] = entry
            elif rank < min(entry["ranks"].values()):
                keep = entry["engines"], entry["ranks"], entry.get("snippet", "")
                entry.update(item)
                entry["engines"], entry["ranks"], entry["snippet"] = keep
            if engine in entry["ranks"]:
                continue
            entry["engines"].append(engine)
            entry["ranks"][engine] = rank
            if len(item.get("snippet") or "") > len(entry.get("snippet") or ""):
                entry["snippet"] = item["snippet"]
    out = list(merged.values())
    for entry in out:
        entry["votes"] = len(entry["engines"])
    out.sort(key=lambda e: (-e["votes"],
                            sum(e["ranks"].values()) / len(e["ranks"]),
                            priority.get(e["engines"][0], len(priority))))
    return out


# ------------------------------------------------------------------ engines
# Per-engine endpoint, request shape, selectors and capabilities.
#
# Request:  "url" (legacy: may contain "{query}"), "method", "params"
#           (callable(query, region) -> dict: the query string for GET, the
#           form for POST), "headers" / "cookies" (dict or callable(region)),
#           "url_builder" (callable() -> url, for per-request URL parts),
#           "prepare" (a pre-flight hook), "impersonate" (pin a TLS profile).
# Parsing:  "result", "link", "title", "snippet" (CSS), "unwrap"
#           (callable(href) -> href), "no_results" (selectors, or "text:..."
#           substrings, that mark a genuine absence), "block_statuses".
# Facts:    "js" (needs a browser), "snippets" (returns description text),
#           "honors_site" (respects site:), "provider" (whose index it is).
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "brave": {
        "url": "https://search.brave.com/search",
        "params": lambda q, region: {"q": q, "source": "web"},
        "cookies": lambda region: _brave_cookies(region),
        "result": "div[data-type='web']",
        "link": ["a:has(div.title)", "a[href^='http']"],
        "title": ["div.title", "div.sitename-container"],
        "snippet": ["div.snippet div.content", "div.generic-snippet div.content",
                    "div.snippet"],
        # "Search elsewhere" sits under every real results page, empty or not.
        "no_results": ["#search-elsewhere", "text:Not many great matches",
                       "text:No results found"],
        "js": False,
        "snippets": True,
        "honors_site": True,
        "provider": "brave",
    },
    "duckduckgo": {
        # The HTML front end is a form; POSTing it is what the page itself does.
        "url": "https://html.duckduckgo.com/html/",
        "method": "POST",
        "params": lambda q, region: {"q": q, "b": "", "l": region or "wt-wt"},
        "result": "div.result:not(.result--ad):not(.result--no-result)",
        "link": ["a.result__a"],
        "title": ["a.result__a"],
        "snippet": ["a.result__snippet", ".result__snippet"],
        "no_results": [".result--no-result", "div.no-results", "text:No results."],
        # A 202 with an "anomaly" page is DuckDuckGo's way of saying no.
        "block_statuses": [202],
        "js": False,
        "snippets": True,
        "honors_site": True,
        "provider": "bing",
    },
    "duckduckgo_lite": {
        "url": "https://lite.duckduckgo.com/lite/",
        "params": lambda q, region: dict({"q": q}, **({"kl": region} if region else {})),
        "result": "table tr:has(a.result-link)",
        "link": ["a.result-link"],
        "title": ["a.result-link"],
        "snippet": ["td.result-snippet"],
        "no_results": ["text:No results.", "text:No more results"],
        "block_statuses": [202],
        "js": False,
        # Verified against the live endpoint: Lite returns titles and URLs but
        # no description text. Callers matching on snippets must know this
        # rather than discover it from empty strings.
        "snippets": False,
        "honors_site": True,
        "provider": "bing",
    },
    "duckduckgo_js": {
        "url": "https://duckduckgo.com/?q={query}",
        "result": "[data-testid='result']",
        "link": ["[data-testid='result-title-a']"],
        "title": ["[data-testid='result-title-a']"],
        "snippet": ["[data-result='snippet']"],
        "js": True,
        "snippets": True,
        "honors_site": True,
        "provider": "bing",
    },
    "yahoo": {
        # Fresh random path tokens on every request, as the site's own links carry.
        "url": "https://search.yahoo.com/search",
        "url_builder": lambda: ("https://search.yahoo.com/search"
                                f";_ylt={secrets.token_urlsafe(18)}"
                                f";_ylu={secrets.token_urlsafe(35)}"),
        "params": lambda q, region: {"p": q},
        "result": "div.relsrch",
        "link": ["div[class*='Title'] a", "h3 a"],
        "title": ["div[class*='Title'] h3", "h3"],
        "snippet": ["div[class*='Text']", "p"],
        "unwrap": unwrap_yahoo,
        "no_results": ["div.zrp", "text:We did not find results"],
        "js": False,
        "snippets": True,
        "honors_site": True,
        # Yahoo's organic results are Bing's; Yahoo honours site: paths anyway.
        "provider": "bing",
    },
    "mojeek": {
        "url": "https://www.mojeek.com/search",
        "params": lambda q, region: {"q": q},
        "cookies": lambda region: (
            {"arc": split_region(region)[0], "lb": split_region(region)[1]}
            if split_region(region)[0] else {}),
        "result": "ul.results-standard > li, li.result",
        "link": ["a.title", "h2 a"],
        "title": ["a.title", "h2"],
        "snippet": ["p.s", "p"],
        "no_results": ["text:No pages found matching", "text:No results found"],
        # Server-rendered. What looked like a "results-free stub" served to
        # plain HTTP clients is a JavaScript captcha, i.e. a block, and is
        # detected as one.
        "js": False,
        "snippets": True,
        "honors_site": True,
        "provider": "mojeek",
    },
    "google_basic": {
        "url": "https://www.google.com/search",
        "params": lambda q, region: _google_params(q, region),
        "headers": lambda region: {"User-Agent": android_gsa_user_agent()},
        "cookies": {"CONSENT": "YES+"},
        # The UA above is mobile, so the TLS fingerprint must be too.
        "impersonate": "chrome_android",
        "result": "div[data-hveid]:has(h3)",
        "link": ["a:has(h3)", "a[href^='http']"],
        "title": ["h3"],
        "snippet": ["div[data-sncf]", "div > div:last-child"],
        "unwrap": unwrap_google,
        "no_results": ["text:did not match any documents", "text:No results found for"],
        "js": False,
        "snippets": True,
        "honors_site": True,
        "provider": "google",
    },
    "startpage": {
        "url": "https://www.startpage.com/sp/search",
        "method": "POST",
        "params": lambda q, region: _startpage_params(q, region),
        "headers": {"Referer": "https://www.startpage.com/"},
        "home": "https://www.startpage.com/",
        "prepare": lambda ctx: _startpage_prepare(ctx),
        "result": ".w-gl__result, div.result:has(> a)",
        "link": ["a.w-gl__result-title", "a.result-title", "a.result-link",
                 ":scope > a", "a[href^='http']"],
        "title": ["h2", "h3", ".w-gl__result-title"],
        "snippet": [".w-gl__description", "p.description", "p"],
        "no_results": ["text:did not match any", "text:No results found"],
        "js": True,
        "snippets": True,
        "honors_site": True,
        "provider": "google",
    },
    "google": {
        "url": "https://www.google.com/search?q={query}",
        "result": "div.g, div[data-hveid] div[data-snf]",
        "link": ["a:has(h3)", "a[href^='http']"],
        "title": ["h3"],
        "snippet": ["div[data-sncf] span", "div[style*='webkit-line-clamp']", "span"],
        "js": True,
        "snippets": True,
        "honors_site": True,
        "provider": "google",
    },
    "yandex": {
        "url": "https://yandex.com/search/?text={query}",
        "result": "li.serp-item, .organic",
        "link": ["a.OrganicTitle-Link", "h2 a", "a[href^='http']"],
        "title": [".OrganicTitleContentSpan", "h2"],
        "snippet": [".OrganicTextContentSpan", ".TextContainer"],
        "js": True,
        "snippets": True,
        "honors_site": True,
        "provider": "yandex",
    },
    "bing": {
        "url": "https://www.bing.com/search?q={query}",
        "result": "li.b_algo",
        "link": ["h2 a"],
        "title": ["h2"],
        "snippet": [".b_caption p", "p[class*='b_lineclamp']", ".b_algoSlug", "p"],
        "no_results": ["li.b_no", "text:There are no results for"],
        "js": False,
        "snippets": True,
        # Honours site:example.com but not site:example.com/path.
        "honors_site": False,
        "provider": "bing",
    },
}


def _brave_cookies(region: Optional[str]) -> Dict[str, str]:
    cookies = {"useLocation": "0"}
    country = _iso_country(split_region(region)[0])
    if country:
        cookies["country"] = country
    return cookies


def _google_params(query: str, region: Optional[str]) -> Dict[str, str]:
    params = {"q": query, "filter": "1", "start": "0"}
    country, lang = split_region(region)
    if lang:
        params["hl"] = f"{lang}-{(_iso_country(country) or 'us').upper()}"
        params["lr"] = f"lang_{lang}"
    if country:
        params["cr"] = f"country{_iso_country(country).upper()}"
    return params


def _startpage_params(query: str, region: Optional[str]) -> Dict[str, str]:
    country, lang = split_region(region)
    params = {"query": query, "cat": "web", "t": "device", "segment": "organic",
              "abp": "1", "abd": "0", "abe": "0"}
    if country:
        params["qsr"] = f"{lang}_{(_iso_country(country) or '').upper()}"
    return params


def _startpage_prepare(ctx: Dict[str, Any]) -> None:
    """Startpage's form carries a per-visit ``sc`` token; fetch it first."""
    page = ctx["fetch"](ctx["spec"].get("home") or "https://www.startpage.com/")
    found = page.select("form#search input[name='sc'], input[name='sc']")
    if found:
        ctx["request"]["data"]["sc"] = found[0].attr("value")


DEFAULT_ENGINE = "brave"

# Tried in order when the primary engine is refused or has nothing. Engines
# that answer from datacentre addresses come first, browser-only ones late,
# and Bing — which ignores site: paths — last.
DEFAULT_FALLBACK_ENGINES: List[str] = [
    "duckduckgo",
    "mojeek",
    "yahoo",
    "google_basic",
    "duckduckgo_lite",
    "duckduckgo_js",
    "startpage",
    "google",
    "yandex",
    "bing",
]

#: Asked at once by ``mode="aggregate"``: one per independent index, with a
#: same-index sibling (Yahoo for DuckDuckGo) held back as a substitute.
DEFAULT_AGGREGATE_ENGINES: List[str] = [
    "brave", "duckduckgo", "yahoo", "mojeek", "google_basic",
]

#: A query that cannot legitimately be empty, used to tell a soft block (an
#: ordinary page with no results) from a genuine absence.
DEFAULT_PROBE_QUERY = "site:wikipedia.org python"

MODES = ("first", "aggregate")


class AdvancedSearchScraper:
    """Search the web, walking a chain of engines until one answers —
    or, with ``mode="aggregate"``, asking several at once and ranking by
    agreement."""

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
        "a[href*='/httpservice/retry/enablejs']",
        "meta[content*='/httpservice/retry/enablejs']",
    )
    # Matched against the host and path only: the query string holds the
    # user's own words, and a search for "captcha" is not a captcha.
    _BLOCK_URL_MARKERS = ("/sorry/", "captcha", "showcaptcha", "anomaly", "/challenge")
    # Only consulted when a page has no results, because a results page's
    # title and text contain the query too.
    _BLOCK_TITLE_MARKERS = (
        "captcha", "just a moment", "attention required", "access denied",
        "are you a robot", "403 forbidden", "403 - forbidden", "429 too many",
        "too many requests", "unusual traffic", "security check",
    )
    _BLOCK_TEXT_MARKERS = (
        "unusual traffic from your computer",
        "sending automated queries",
        "bots use duckduckgo too",
        "confirm this search was made by a human",
        "javascript is required to complete this challenge",
        "flagged as being suspicious",
        "please enable javascript and cookies to continue",
    )

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
        *,
        mode: str = "first",
        aggregate_engines: Optional[Sequence[str]] = None,
        deadline: float = 8.0,
        min_engines: int = 2,
        strict_site: bool = True,
        region: Optional[str] = None,
        http_timeout: Optional[float] = None,
        circuit_breaker: bool = True,
        health: Optional[EngineHealth] = None,
        withdraw_browser_on_block: bool = True,
        browser_cooldown: float = 120.0,
        verify_empty: bool = False,
        probe_query: str = DEFAULT_PROBE_QUERY,
        normalize: Optional[Callable[[str], str]] = None,
        impersonate_profiles: Optional[Sequence[str]] = None,
        browser: str = "selenium",
        playwright_options: Optional[Dict[str, Any]] = None,
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
        # Browserless fetches should fail fast: a refusing engine is better
        # skipped in 8 s than waited on for the browser's 20.
        self.http_timeout = (min(8.0, page_load_timeout) if http_timeout is None
                             else http_timeout)
        self.proxy = proxy
        if transport not in TRANSPORTS:
            raise ValueError(
                f"transport must be one of {', '.join(repr(t) for t in TRANSPORTS)}, "
                f"not {transport!r}")
        self.transport = transport
        self.user_agent = user_agent
        self.impersonate_profiles = impersonate_profiles
        if browser not in ("selenium", "playwright"):
            raise ValueError(f"browser must be 'selenium' or 'playwright', not {browser!r}")
        # Which browser serves engines that need JavaScript (and transport="browser").
        self.browser = browser
        self.playwright_options = dict(playwright_options or {})
        self._playwright = None

        if mode not in MODES:
            raise ValueError(f"mode must be 'first' or 'aggregate', not {mode!r}")
        self.mode = mode
        self.aggregate_engines = list(aggregate_engines or DEFAULT_AGGREGATE_ENGINES)
        self.deadline = deadline
        self.min_engines = min_engines
        self.strict_site = strict_site
        self.region = region
        self.normalize_url = normalize or normalize_url

        # Refusal tracking is shared process-wide by default, because the
        # address being throttled is shared by every scraper in the process.
        self.circuit_breaker = circuit_breaker
        if health is not None:
            self.engine_health = health
        elif circuit_breaker:
            self.engine_health = default_health()
        else:
            self.engine_health = EngineHealth()
        self.withdraw_browser_on_block = withdraw_browser_on_block
        self.browser_cooldown = browser_cooldown
        self.verify_empty = verify_empty
        self.probe_query = probe_query
        self._probe_cache: Dict[str, Tuple[float, bool]] = {}

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
        self._impersonate: Optional[ImpersonateTransport] = None
        self._lock = threading.RLock()
        self._transport_lock = threading.Lock()
        self._announced_transport = False

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
        # Unknown until the caller says: neither trusted as authoritative for
        # site: queries nor skipped by strict_site.
        spec.setdefault("honors_site", None)
        spec.setdefault("provider", name.lower())
        self.engines[name.lower()] = spec

    def capabilities(self, engine: Optional[str] = None) -> Dict[str, Any]:
        """What an engine can do: whether it needs a browser, returns snippets,
        honours ``site:``, and whose index it serves."""
        spec = self._spec(engine)
        return {"js": bool(spec.get("js", True)),
                "snippets": bool(spec.get("snippets", True)),
                "honors_site": spec.get("honors_site"),
                "provider": spec.get("provider") or (engine or self.search_engine),
                "method": spec.get("method", "GET"),
                "url": spec["url"]}

    def engines_honoring_site(self) -> Set[str]:
        """Engines known to respect ``site:``, including paths.

        An ``empty`` answer from one of these to a ``site:`` query is evidence
        that nothing matching exists; from anything else it is not.
        """
        return {name for name, spec in self.engines.items() if spec.get("honors_site")}

    def _spec(self, engine: Optional[str] = None) -> Dict[str, Any]:
        return self.engines[engine or self.search_engine]

    def _provider(self, engine: str) -> str:
        return self.engines[engine].get("provider") or engine

    def _build_request(self, query: str, engine: str) -> Dict[str, Any]:
        """Method, URL, params/form, headers and cookies for one search."""
        spec = self._spec(engine)
        region = self.region

        def resolve(value):
            return value(region) if callable(value) else dict(value or {})

        url = spec["url"]
        builder = spec.get("url_builder")
        if builder:
            url = builder()
        method = str(spec.get("method", "GET")).upper()
        fields = None
        if "{query}" in url:
            # The query must be percent-encoded or spaces and '&' corrupt the URL.
            url = url.replace("{query}", quote_plus(query))
        else:
            maker = spec.get("params")
            fields = maker(query, region) if maker else {"q": query}
        return {
            "method": method,
            "url": url,
            "params": fields if method == "GET" else None,
            "data": fields if method != "GET" else None,
            "headers": resolve(spec.get("headers")) or None,
            "cookies": resolve(spec.get("cookies")) or None,
        }

    def _engine_url(self, query: str, engine: Optional[str] = None) -> str:
        """The engine's results URL as a single GET (what a browser loads)."""
        request = self._build_request(query, engine or self.search_engine)
        fields = request["params"] or request["data"]
        if not fields:
            return request["url"]
        joiner = "&" if "?" in request["url"] else "?"
        return request["url"] + joiner + urlencode(fields)

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
        if self.transport in BROWSERLESS_TRANSPORTS:
            # Browser-only engines cannot be served without a browser.
            order = [n for n in order if not self._needs_browser(n)] or order[:1]
        return order

    def _needs_browser(self, engine: str) -> bool:
        spec = self.engines[engine]
        # The HTTP clients speak http(s) only; anything else (file://, data:)
        # has to go through the browser whatever the engine declares.
        return bool(spec.get("js", True)) or not str(spec.get("url", "")).startswith(
            ("http://", "https://"))

    def _skip_reason(self, engine: str, query: str, explicit: bool) -> Optional[Dict[str, Any]]:
        """Why `engine` should not be asked right now, if it should not."""
        if (self.strict_site and not explicit and self._site_constraints(query)
                and self.engines[engine].get("honors_site") is False):
            return {"engine": engine, "reason": SKIP_IGNORES_SITE, "resume_in": 0}
        if self.circuit_breaker:
            wait_for = self.engine_health.resume_in(engine)
            if wait_for > 0:
                return {"engine": engine, "reason": SKIP_COOLING,
                        "resume_in": round(wait_for, 1)}
        if (self.withdraw_browser_on_block and self._needs_browser(engine)
                and self.transport != "browser"
                and not self.engine_health.browser_allowed()):
            return {"engine": engine, "reason": SKIP_BROWSER_WITHDRAWN,
                    "resume_in": round(self.engine_health.browser_resume_in(), 1)}
        return None

    # ---------------------------------------------------------- extraction
    def _favicon_for(self, url: str) -> str:
        try:
            domain = urlparse(url).netloc
            return f"https://www.google.com/s2/favicons?domain={domain}" if domain else ""
        except Exception:
            return ""

    #: Ad and click-tracking endpoints that sit among organic results. They are
    #: not search results, and letting one through poisons a scrape silently.
    _AD_URL_MARKERS = (
        "duckduckgo.com/y.js",       # DuckDuckGo sponsored slot
        "duckduckgo.com/l/?ad_",     # ad variant of the redirect
        "bing.com/aclick",           # Bing ads, also on Yahoo
        "bing.com/aclk",
        "google.com/aclk",           # Google ads
        "googleadservices.com",
        "doubleclick.net",
        "/adclick",
        "search.brave.com/a/redirect",  # Brave sponsored results
    )

    @staticmethod
    def _site_constraints(query: str) -> List[str]:
        """Extract ``site:`` targets from a query, e.g. ``linkedin.com/in``."""
        out = []
        for token in query.split():
            token = token.strip('"\'')
            if token.lower().startswith("site:") and len(token) > 5:
                out.append(token[5:].lower().lstrip("."))
        return out

    @staticmethod
    def _matches_site(url: str, constraints: List[str]) -> bool:
        """Whether `url` satisfies at least one ``site:`` constraint.

        Engines disagree about this operator — notably, Bing honours
        ``site:example.com`` but ignores a path like ``site:example.com/in`` and
        answers with unrelated pages. Results that look plausible but do not
        match are worse for a scrape than no results at all, so they are
        dropped and the engine is treated as having nothing.
        """
        try:
            parts = urlparse(url)
        except Exception:
            return False
        host = (parts.netloc or "").lower().split("@")[-1].split(":")[0]
        path = (parts.path or "/").lower()
        for constraint in constraints:
            domain, _, prefix = constraint.partition("/")
            if not (host == domain or host.endswith("." + domain)):
                continue
            if prefix and not path.lstrip("/").startswith(prefix):
                continue
            return True
        return False

    @classmethod
    def _is_ad_url(cls, url: str) -> bool:
        lowered = url.lower()
        return any(marker in lowered for marker in cls._AD_URL_MARKERS)

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
        # Yahoo: r.search.yahoo.com/…/RU=<target>/RK=…
        if "yahoo.com" in host and "/RU=" in href:
            return unwrap_yahoo(href)
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

    def _find_link(self, node, selectors: List[str],
                   unwrap: Optional[Callable[[str], str]] = None) -> str:
        for sel in selectors:
            for found in node.select(sel):
                href = found.attr("href")
                if not href.startswith(("http://", "https://")):
                    continue
                if self._is_ad_url(href):
                    continue
                resolved = unwrap(href) if unwrap else href
                resolved = self._unwrap_redirect(resolved)
                # An ad can hide behind a redirect, so re-check after unwrapping.
                if not self._is_ad_url(resolved):
                    return tidy_url(resolved)
        return ""

    def _extract_result(self, node, engine: str) -> Dict:
        spec = self._spec(engine)
        out = {"url": "", "title": "", "snippet": "", "favicon": "",
               "cached": None, "quick_answer": None, "engine": engine}

        unwrap = spec.get("unwrap")
        href = self._find_link(node, spec["link"], unwrap)
        if not href:
            # Last resort: the first plain outbound link in the container.
            href = self._find_link(node, ["a[href]"], unwrap)
        out["url"] = href
        out["title"] = normalize_text(self._first_text(node, spec["title"]))
        out["snippet"] = normalize_text(self._first_text(node, spec["snippet"]))
        out["favicon"] = self._favicon_for(href) if href else ""

        cached = node.select("a.result__more-link")
        if cached:
            out["cached"] = cached[0].attr("href")
        return out

    def _blocked_reason(self, page) -> str:
        """A bot check recognisable by where it lives or what it contains."""
        try:
            parts = urlparse(page.url or "")
            where = f"{parts.netloc}{parts.path}".lower()
        except Exception:
            where = (page.url or "").lower()
        for marker in self._BLOCK_URL_MARKERS:
            if marker in where:
                return f"bot-check page ({where[:80]})"
        for sel in self._BLOCK_SELECTORS:
            if page.select(sel):
                return f"bot-check element {sel!r}"
        return ""

    def _soft_block_reason(self, page) -> str:
        """A refusal page recognisable only by its wording (no results found)."""
        title = ""
        try:
            title = (page.title() or "").lower() if hasattr(page, "title") else ""
        except Exception:
            title = ""
        for marker in self._BLOCK_TITLE_MARKERS:
            if marker in title:
                return f"bot-check page (title {title[:60]!r})"
        text = page.text().lower() if hasattr(page, "text") else ""
        for marker in self._BLOCK_TEXT_MARKERS:
            if marker in text:
                return f"bot-check page ({marker!r})"
        return ""

    @staticmethod
    def _looks_empty(page, markers: Sequence[str]) -> bool:
        """Whether the page says, in a way we recognise, that it found nothing."""
        text = None
        for marker in markers:
            if marker.startswith("text:"):
                if text is None:
                    text = page.text().lower() if hasattr(page, "text") else ""
                if marker[5:].lower() in text:
                    return True
            elif page.select(marker):
                return True
        return False

    # ------------------------------------------------------------- drivers
    @staticmethod
    def _proxy_from_args(options: Dict[str, Any]) -> Optional[str]:
        """Recover a proxy passed the pre-1.0 way, as a raw Chrome switch.

        Before `proxy=` existed the only way to reach the browser's proxy was
        ``headless_options={"additional_args": ["--proxy-server=..."]}``. The
        HTTP transports cannot see Chrome switches, so without this such a
        proxy would be silently bypassed and the request would leave from the
        host's own address.
        """
        for arg in options.get("additional_args") or []:
            if str(arg).startswith("--proxy-server="):
                return str(arg).split("=", 1)[1]
        return None

    def _effective_proxy(self) -> Optional[str]:
        return self.proxy or self._proxy_from_args(self.headless_options)

    def _http_transport(self) -> HttpTransport:
        with self._transport_lock:
            if self._http is None:
                # Whatever the browser would have used must apply here too, or
                # the transports would disagree about where traffic comes from.
                self._http = HttpTransport(
                    timeout=self.http_timeout,
                    user_agent=self.user_agent or self.headless_options.get("user_agent") or "",
                    proxy=self._effective_proxy())
            return self._http

    def _impersonate_transport(self) -> ImpersonateTransport:
        with self._transport_lock:
            if self._impersonate is None:
                # No User-Agent override unless the caller explicitly chose one:
                # the profile's own headers match its TLS fingerprint.
                self._impersonate = ImpersonateTransport(
                    timeout=self.http_timeout, proxy=self._effective_proxy(),
                    profiles=self.impersonate_profiles,
                    user_agent=self.user_agent or "")
            return self._impersonate

    def _playwright_transport(self):
        with self._transport_lock:
            if self._playwright is None:
                from .playwright_driver import PlaywrightTransport
                options = dict(self.playwright_options)
                options.setdefault("proxy", self._effective_proxy())
                options.setdefault("timeout", self.page_load_timeout)
                if self.user_agent:
                    options.setdefault("user_agent", self.user_agent)
                options.setdefault("headless", self.headless_options.get("headless", True))
                self._playwright = PlaywrightTransport(**options)
            return self._playwright

    def _browser_transport(self):
        if self.browser == "playwright":
            return self._playwright_transport(), True
        return BrowserTransport(self._get_driver(), self.wait_timeout), True

    def browserless_transport_name(self) -> Optional[str]:
        """Which transport serves non-browser engines: ``impersonate``,
        ``http``, or None when neither is installed (or ``transport="browser"``)."""
        if self.transport == "browser":
            return None
        if self.transport == "impersonate":
            return "impersonate"
        if self.transport == "http":
            return "http"
        if impersonate_available()[0]:
            return "impersonate"
        if http_available()[0]:
            return "http"
        return None

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
        if self.transport == "browser" or self._needs_browser(engine):
            return self._browser_transport()
        name = self.browserless_transport_name()
        if name == "impersonate":
            ok, why = impersonate_available()
            if not ok:
                raise RuntimeError(
                    f'transport="impersonate" needs the impersonate extra ({why}): '
                    'pip install "headless-driver[impersonate]"')
            transport = self._impersonate_transport()
        elif name == "http":
            ok, why = http_available()
            if not ok:
                raise RuntimeError(
                    'transport="http" needs the http extra: '
                    'pip install "headless-driver[http]"')
            transport = self._http_transport()
        else:
            return self._browser_transport()
        if not self._announced_transport:
            self._announced_transport = True
            if name == "http" and self.transport == "auto":
                log.info("using the http transport; install headless-driver[impersonate] "
                         "for a browser TLS fingerprint (requests is easily "
                         "fingerprinted from datacentre addresses)")
            else:
                log.info("using the %s transport", name)
        return transport, False

    # -------------------------------------------------------------- search
    def _classify_status(self, engine: str, page) -> Optional[Tuple[str, str]]:
        status = getattr(page, "status", 200) or 200
        spec = self._spec(engine)
        if status == 429:
            return STATUS_RATE_LIMITED, "HTTP 429"
        if status in BLOCK_HTTP_STATUSES or status in (spec.get("block_statuses") or ()):
            return STATUS_BLOCKED, f"HTTP {status}"
        if status >= 400:
            return STATUS_ERROR, f"HTTP {status}"
        return None

    def _search_one(self, engine: str, query: str, limit: int) -> tuple:
        """Scrape one engine. Returns (results, EngineAttempt)."""
        spec = self._spec(engine)
        started = time.time()
        meta: Dict[str, Any] = {"transport": ""}

        def attempt(status, count=0, reason="", http_status=None, retry_after=None):
            return EngineAttempt(engine=engine, status=status, count=count,
                                 reason=reason, elapsed=time.time() - started,
                                 http_status=http_status, retry_after=retry_after,
                                 transport=str(meta["transport"]))

        try:
            transport, is_browser = self._transport_for(engine)
        except Exception as e:
            log.error("%s transport unavailable: %s", engine, e)
            return [], attempt(STATUS_ERROR, reason=str(e))
        name = getattr(transport, "name", "")
        meta["transport"] = name if isinstance(name, str) else ""

        try:
            if is_browser:
                url = self._engine_url(query, engine)
                log.debug("%s: %s (%s)", engine, url, meta["transport"])
                if meta["transport"] == "playwright":
                    # One context per engine: cookies never cross engines.
                    page = transport.fetch(url, spec["result"], key=engine)
                else:
                    page = transport.fetch(url, spec["result"])
            else:
                request = self._build_request(query, engine)
                profile = spec.get("impersonate")
                if spec.get("prepare"):
                    def fetch(url, **kw):
                        return transport.fetch(url, key=engine, profile=profile, **kw)
                    spec["prepare"]({"engine": engine, "query": query, "region": self.region,
                                     "request": request, "fetch": fetch, "scraper": self,
                                     "spec": spec})
                log.debug("%s: %s %s (%s)", engine, request["method"], request["url"],
                          meta["transport"])
                page = transport.fetch(request.pop("url"), key=engine, profile=profile,
                                       **request)
        except Exception as e:
            kind = type(e).__name__
            if "Timeout" in kind or "timed out" in str(e).lower():
                log.debug("%s timed out", engine)
                if is_browser:
                    try:
                        self.driver.execute_script("window.stop();")
                    except Exception:
                        pass
                return [], attempt(STATUS_TIMEOUT, reason=f"{kind}: {e}")
            if "playwright install" in str(e) or "Executable doesn't exist" in str(e):
                log.error("Playwright has no browser build: run `playwright install chromium`")
                return [], attempt(STATUS_ERROR, reason="Playwright browser not installed; "
                                                        "run: playwright install chromium")
            # DNS failure, refused connection, TLS error: try the next engine
            # rather than letting one unreachable host abort the whole search.
            log.debug("%s could not be loaded: %s", engine, kind)
            return [], attempt(STATUS_UNREACHABLE, reason=f"{kind}: {e}")

        # Selenium cannot see HTTP status codes; Playwright and the HTTP clients can.
        http_status = (None if meta["transport"] == "browser"
                       else getattr(page, "status", None))
        verdict = self._classify_status(engine, page)
        if verdict:
            status, reason = verdict
            retry_after = getattr(page, "retry_after", None) if status == STATUS_RATE_LIMITED else None
            log.debug("%s refused: %s", engine, reason)
            return [], attempt(status, reason=reason, http_status=http_status,
                               retry_after=retry_after)

        reason = self._blocked_reason(page)
        if reason:
            log.debug("%s served a %s", engine, reason)
            return [], attempt(STATUS_BLOCKED, reason=reason, http_status=http_status)

        nodes = page.select(spec["result"])
        if not nodes:
            # No result containers at all: a refusal, a real "no results"
            # page, or markup we no longer recognise. Only the second one is
            # evidence that nothing exists.
            reason = self._soft_block_reason(page)
            if reason:
                log.debug("%s served a %s", engine, reason)
                return [], attempt(STATUS_BLOCKED, reason=reason, http_status=http_status)
            markers = spec.get("no_results")
            if markers and not self._looks_empty(page, markers):
                log.debug("%s returned a page with no results and no "
                          "'no results' marker", engine)
                return [], attempt(STATUS_UNPARSED, http_status=http_status,
                                   reason="no result containers and no 'no results' marker")
            log.debug("%s returned no results for: %s", engine, query)
            return [], attempt(STATUS_EMPTY, http_status=http_status)

        constraints = self._site_constraints(query)
        extracted: List[Dict] = []
        seen = set()
        dropped_offsite = 0
        for node in nodes:
            if len(extracted) >= limit:
                break
            item = self._extract_result(node, engine)
            if not item["url"] or not item["title"] or item["url"] in seen:
                continue
            if constraints and not self._matches_site(item["url"], constraints):
                dropped_offsite += 1
                continue
            seen.add(item["url"])
            extracted.append(self.result_processor(query, item))

        if not extracted and dropped_offsite:
            log.debug("%s ignored the site: operator (%s off-target results)",
                      engine, dropped_offsite)
        if not extracted:
            log.debug("%s returned no usable results for: %s", engine, query)
            return [], attempt(STATUS_EMPTY, http_status=http_status)
        return extracted, attempt(STATUS_OK, count=len(extracted), http_status=http_status)

    def _settle(self, attempt: EngineAttempt, refused_providers: Set[str]) -> EngineAttempt:
        """Everything that follows an attempt: soft-block checks, the circuit
        breaker, fingerprint rotation and browser withdrawal."""
        if (attempt.status == STATUS_EMPTY and self.verify_empty
                and self.engines.get(attempt.engine, {}).get("honors_site")):
            attempt = self._verify_empty(attempt)

        if self.circuit_breaker:
            self.engine_health.record(attempt)
        if attempt.status in REFUSAL_BY_ENGINE or attempt.status == STATUS_UNPARSED:
            if self._playwright is not None and attempt.transport == "playwright":
                self._playwright.rotate(attempt.engine)
            transport = self._impersonate
            if transport is not None and attempt.transport == "impersonate":
                # Present as a different browser next time; the refusal may
                # have been aimed at this fingerprint rather than the address.
                transport.rotate(attempt.engine, self.engines[attempt.engine].get("impersonate"))
        if attempt.status in REFUSAL_BY_ENGINE and attempt.engine in self.engines:
            refused_providers.add(self._provider(attempt.engine))
            # One refusal might be about a fingerprint or one engine's mood;
            # two unrelated indexes refusing in the same search is about the
            # address — and starting Chrome cannot get past that.
            if (self.withdraw_browser_on_block and self.transport != "browser"
                    and len(refused_providers) >= 2):
                self.engine_health.withdraw_browser(self.browser_cooldown)
        return attempt

    def _verify_empty(self, attempt: EngineAttempt) -> EngineAttempt:
        """Rewrite an ``empty`` to ``blocked`` when the engine cannot even
        answer a query that cannot be empty (DuckDuckGo's soft block)."""
        cached = self._probe_cache.get(attempt.engine)
        now = time.monotonic()
        if cached and now - cached[0] < 60:
            answering = cached[1]
        else:
            _, check = self._search_one(attempt.engine, self.probe_query, 1)
            answering = check.ok
            self._probe_cache[attempt.engine] = (now, answering)
        if answering:
            return attempt
        attempt.status = STATUS_BLOCKED
        attempt.reason = "soft block (control query empty)"
        return attempt

    def probe(self, engine: Optional[str] = None, query: Optional[str] = None) -> EngineAttempt:
        """Ask one engine a query that cannot be empty, and report how it went.

        A cheap health check: ``ok`` means the engine is answering, ``empty``
        means it is soft-blocking (serving ordinary pages with no results), and
        anything else is an outright refusal.
        """
        name = self._validate_engine(engine) if engine else self.search_engine
        _, attempt = self._search_one(name, query or self.probe_query, 3)
        if self.circuit_breaker:
            self.engine_health.record(attempt)
        return attempt

    def health(self) -> Dict[str, Dict[str, Any]]:
        """Circuit-breaker state for every engine this scraper knows about."""
        return {name: self.engine_health.state(name) for name in self.engines}

    def reset_health(self, engine: Optional[str] = None) -> None:
        """Forget refusals: stand every engine (or one) back up now."""
        self.engine_health.reset(engine)
        if engine is None:
            self.engine_health.restore_browser()
        self._probe_cache.clear()

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        engine: Optional[str] = None,
        fallback: Optional[bool] = None,
        mode: Optional[str] = None,
        engines: Optional[Sequence[str]] = None,
        deadline: Optional[float] = None,
        min_engines: Optional[int] = None,
    ) -> SearchResponse:
        """Search `query`.

        ``mode="first"`` (the default) walks the fallback chain until an engine
        answers. ``mode="aggregate"`` asks several engines at once (`engines`,
        within `deadline` seconds) and ranks results by how many agree.

        Returns a :class:`~headless.results.SearchResponse`, which behaves like
        the list of results it contains and additionally reports which engine
        answered, what every other engine did, and which were skipped.

        `engine` starts the chain somewhere else; `fallback` overrides whether
        the rest of the chain is tried at all.
        """
        mode = mode or self.mode
        if mode not in MODES:
            raise ValueError(f"mode must be 'first' or 'aggregate', not {mode!r}")
        limit = self.max_results if max_results is None else max_results
        if mode == "aggregate":
            return self._search_aggregate(
                query, limit, engines, self.deadline if deadline is None else deadline,
                self.min_engines if min_engines is None else min_engines)

        started = time.time()
        response = SearchResponse(query=query)
        if limit <= 0:
            self.last_response = response
            return response

        order = self._engine_order(engine, fallback)
        # Stale state must not survive a failed search: a caller reading
        # last_engine after an empty result would otherwise see the previous one.
        self.last_engine = None
        refused_providers: Set[str] = set()

        with self._lock:
            for index, name in enumerate(order):
                skip = self._skip_reason(name, query, explicit=(index == 0 and len(order) == 1))
                if skip:
                    log.debug("skipping %s (%s)", name, skip["reason"])
                    response.skipped.append(skip)
                    continue
                results, attempt = self._search_one(name, query, limit)
                attempt = self._settle(attempt, refused_providers)
                response.attempts.append(attempt)
                if results and attempt.ok:
                    response.results = results
                    response.engine = name
                    response.engines = [name]
                    self.last_engine = name
                    self._remember(results)
                    log.debug("%s results from %s", len(results), name)
                    break

        return self._finish(response, started, order)

    def _remember(self, results: List[Dict]) -> None:
        if self.keep_history:
            self.results.extend(results)
            if self.history_limit and len(self.results) > self.history_limit:
                del self.results[:-self.history_limit]

    def _finish(self, response: SearchResponse, started: float,
                order: Sequence[str]) -> SearchResponse:
        response.elapsed = time.time() - started
        self.last_response = response
        if not response.results:
            if response.blocked:
                log.warning("every engine refused %r (%s)", response.query,
                            ", ".join(f"{a.engine}:{a.status}" for a in response.attempts))
                if self.raise_on_block:
                    raise AllEnginesBlocked(response)
            elif response.cooling:
                soonest = min((s["resume_in"] for s in response.skipped
                               if s["reason"] == SKIP_COOLING), default=0)
                log.info("no engine asked for %r: all cooling down (next in %.0fs)",
                         response.query, soonest)
                if self.raise_on_block:
                    raise AllEnginesBlocked(response)
            else:
                log.info("no results for %r (tried %s)", response.query,
                         ", ".join(response.engines_tried) or ", ".join(order))
        return response

    # ------------------------------------------------------------ aggregate
    def _search_aggregate(self, query: str, limit: int,
                          engines: Optional[Sequence[str]], deadline: float,
                          min_engines: int) -> SearchResponse:
        """Ask one engine per independent index at once; rank by agreement."""
        if self.transport == "browser":
            raise ValueError('mode="aggregate" needs a browserless transport; '
                             'a WebDriver session cannot be shared across threads')
        started = time.time()
        response = SearchResponse(query=query, mode="aggregate")
        if limit <= 0:
            self.last_response = response
            return response
        self.last_engine = None
        names = [self._validate_engine(e) for e in (engines or self.aggregate_engines)]

        # One queue per provider: the first engine is asked, the rest wait in
        # case it refuses. Asking two front ends of one index proves nothing.
        groups: Dict[str, List[str]] = {}
        for name in dict.fromkeys(names):
            if self._needs_browser(name):
                response.skipped.append({"engine": name, "reason": "needs_browser",
                                         "resume_in": 0})
                continue
            skip = self._skip_reason(name, query, explicit=False)
            if skip:
                response.skipped.append(skip)
                continue
            groups.setdefault(self._provider(name), []).append(name)

        per_engine: Dict[str, List[Dict]] = {}
        refused_providers: Set[str] = set()
        if groups:
            executor = ThreadPoolExecutor(max_workers=len(groups),
                                          thread_name_prefix="headless-aggregate")
            pending: Dict[Any, Tuple[str, str]] = {}

            def submit(provider: str) -> None:
                queue = groups[provider]
                if queue:
                    name = queue.pop(0)
                    pending[executor.submit(self._search_one, name, query, limit)] = (provider, name)

            for provider in list(groups):
                submit(provider)
            try:
                while pending:
                    remaining = deadline - (time.time() - started)
                    if remaining <= 0:
                        break
                    done, _ = wait(list(pending), timeout=remaining,
                                   return_when=FIRST_COMPLETED)
                    for future in done:
                        provider, name = pending.pop(future)
                        try:
                            results, attempt = future.result()
                        except Exception as e:  # pragma: no cover - defensive
                            results, attempt = [], EngineAttempt(name, STATUS_ERROR,
                                                                 reason=str(e))
                        attempt = self._settle(attempt, refused_providers)
                        response.attempts.append(attempt)
                        if attempt.ok and results:
                            per_engine[name] = results
                        elif attempt.blocked and time.time() - started < deadline:
                            submit(provider)  # a sibling on the same index
                    if self._consensus_reached(per_engine, names, min_engines):
                        log.debug("aggregate: consensus after %s engines", len(per_engine))
                        break
            finally:
                for future, (provider, name) in pending.items():
                    response.attempts.append(EngineAttempt(
                        name, STATUS_TIMEOUT, reason=f"not back within the {deadline:g}s deadline"
                        if time.time() - started >= deadline else "stopped early: consensus reached",
                        elapsed=time.time() - started))
                executor.shutdown(wait=False, cancel_futures=True)
            for provider, queue in groups.items():
                for name in queue:
                    response.skipped.append({"engine": name, "resume_in": 0,
                                             "reason": SKIP_DUPLICATE_PROVIDER})

        merged = merge_results(per_engine, names, self.normalize_url)[:limit]
        response.results = merged
        response.engines = [n for n in names if n in per_engine]
        response.engine = "aggregate" if merged else None
        self.last_engine = response.engine
        self._remember(merged)
        return self._finish(response, started, names)

    def _consensus_reached(self, per_engine: Dict[str, List[Dict]],
                           order: Sequence[str], min_engines: int) -> bool:
        if len(per_engine) < max(2, min_engines):
            return False
        top = merge_results(per_engine, order, self.normalize_url)[:3]
        return bool(top) and all(item["votes"] >= 2 for item in top)

    def search_batch(self, queries: List[str], max_workers: int = 4,
                     per_query: Optional[int] = None) -> Dict[str, SearchResponse]:
        """Search several queries.

        One scraper owns one WebDriver session, and a session is not thread-safe,
        so this is sequential by default. Pass ``max_workers > 1`` only when this
        scraper can fetch every engine in its chain without a browser, which
        has no such constraint. For browser engines use :class:`ScraperPool`,
        which gives each worker its own driver.
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
        if self.browserless_transport_name() is None:
            return False
        return all(not self._needs_browser(n) for n in self._engine_order())

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
                        writer.writerow({k: _csv_cell(r.get(k, "")) for k in keys})
                return True
            except Exception as e:
                log.error("CSV export failed: %s", e)
                return False
        log.error("unsupported export format: %s (use .json or .csv)", path)
        return False

    def quit(self):
        with self._lock:
            context, driver = self._driver_context, self.driver
            http, impersonate = self._http, self._impersonate
            playwright = self._playwright
            self._driver_context = None
            self.driver = None
            self._http = None
            self._impersonate = None
            self._playwright = None
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
        for transport in (http, impersonate, playwright):
            if transport is not None:
                transport.close()

    def __enter__(self) -> "AdvancedSearchScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit()


def _csv_cell(value: Any) -> Any:
    # Aggregate results carry lists and dicts; CSV wants flat text.
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


class ScraperPool:
    """A pool of scrapers, one browser per worker, for parallel searching.

    ``search_batch`` on a single scraper cannot be parallel, because one
    WebDriver session cannot be driven from several threads. This gives each
    worker its own scraper, and replaces one whose browser has died. Workers
    share one circuit breaker, so a refusal seen by one stands the engine down
    for all of them.

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
