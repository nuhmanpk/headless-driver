"""Ways of fetching a search results page.

Most engines in :data:`~headless.scraper.ENGINE_SPECS` render their results on
the server, so fetching them needs an HTTP client and an HTML parser, not a
browser. That is roughly two orders of magnitude cheaper than driving Chrome —
hundreds of milliseconds and a few megabytes, against seconds and a gigabyte —
and, having no WebDriver session, it is thread-safe.

Three transports share one tiny document model, so the extraction code in
:mod:`headless.scraper` does not care which one produced the page:

* :class:`ImpersonateTransport` — ``curl_cffi``, presenting a real browser's
  TLS ClientHello, HTTP/2 SETTINGS and header order. The one to use from a
  datacentre address, where anti-bot front ends score the TLS fingerprint
  before they look at anything else.
* :class:`HttpTransport` — plain ``requests``. Honest, portable, and easily
  fingerprinted as Python.
* :class:`BrowserTransport` — Selenium, for engines that render in JavaScript.
"""

import random
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

from .logs import get_logger

log = get_logger("transport")

#: Sent by :class:`HttpTransport`. Chrome's own headers, minus the ones a real
#: browser would add per-connection. ``br`` is left out because ``requests``
#: cannot decode it without an extra package, and advertising an encoding the
#: client then fails to read is its own tell.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

#: Browser profiles :class:`ImpersonateTransport` rotates between. Each names a
#: complete, self-consistent fingerprint: TLS, HTTP/2 and default headers.
DEFAULT_PROFILES: Tuple[str, ...] = (
    "chrome", "edge", "safari", "firefox", "chrome_android", "safari_ios",
)


def http_available() -> Tuple[bool, str]:
    """Whether the optional plain-HTTP transport dependencies are importable."""
    try:
        import requests  # noqa: F401
    except ImportError:
        return False, "requests is not installed"
    try:
        import bs4  # noqa: F401
    except ImportError:
        return False, "beautifulsoup4 is not installed"
    return True, ""


def impersonate_available() -> Tuple[bool, str]:
    """Whether the browser-impersonating transport can be used."""
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return False, "curl_cffi is not installed"
    try:
        import bs4  # noqa: F401
    except ImportError:
        return False, "beautifulsoup4 is not installed"
    return True, ""


_parser_name: Optional[str] = None


def html_parser() -> str:
    """``lxml`` when installed — several times faster at volume — else the stdlib."""
    global _parser_name
    if _parser_name is None:
        try:
            import lxml  # noqa: F401
            _parser_name = "lxml"
        except ImportError:
            _parser_name = "html.parser"
    return _parser_name


def parse_html(text: str, url: str = "") -> "HtmlNode":
    from bs4 import BeautifulSoup
    return HtmlNode(BeautifulSoup(text or "", html_parser()), url)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Seconds from a ``Retry-After`` header (delta-seconds or an HTTP date)."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class Node:
    """One element of a fetched page."""

    def select(self, css: str) -> List["Node"]:
        raise NotImplementedError

    def text(self) -> str:
        raise NotImplementedError

    def attr(self, name: str) -> str:
        raise NotImplementedError


class BrowserNode(Node):
    """A Selenium element."""

    __slots__ = ("_element",)

    def __init__(self, element):
        self._element = element

    def select(self, css: str) -> List[Node]:
        from selenium.webdriver.common.by import By
        from selenium.common.exceptions import WebDriverException
        try:
            return [BrowserNode(e) for e in
                    self._element.find_elements(By.CSS_SELECTOR, css)]
        except WebDriverException:
            # e.g. ':has()' on a browser build without support for it
            return []

    def text(self) -> str:
        from selenium.common.exceptions import WebDriverException
        try:
            value = (self._element.text or "").strip()
        except WebDriverException:
            return ""
        if value:
            return value
        # `.text` yields "" for anything Selenium deems not displayed, which many
        # engines' result headings are; textContent still has the real string.
        try:
            return " ".join((self._element.get_attribute("textContent") or "").split())
        except WebDriverException:
            return ""

    def attr(self, name: str) -> str:
        from selenium.common.exceptions import WebDriverException
        try:
            return self._element.get_attribute(name) or ""
        except WebDriverException:
            return ""


class HtmlNode(Node):
    """A BeautifulSoup tag, with hrefs resolved against the page URL."""

    __slots__ = ("_tag", "_base")

    def __init__(self, tag, base_url: str = ""):
        self._tag = tag
        self._base = base_url

    def select(self, css: str) -> List[Node]:
        try:
            return [HtmlNode(t, self._base) for t in self._tag.select(css)]
        except Exception:
            # soupsieve raises on selectors it cannot compile; treat as no match
            # so one exotic selector cannot abort a whole extraction.
            return []

    def text(self) -> str:
        return " ".join(self._tag.get_text(" ", strip=True).split())

    def attr(self, name: str) -> str:
        value = self._tag.get(name) or ""
        if isinstance(value, list):
            value = " ".join(value)
        if name == "href" and value and self._base:
            # Selenium hands back absolute URLs; match that.
            return urljoin(self._base, value)
        return value


class Page:
    """A fetched results page."""

    def __init__(self, root: Node, url: str, status: int = 200,
                 headers: Optional[Dict[str, str]] = None, transport: str = ""):
        self.root = root
        self.url = url
        self.status = status
        # Header names are case-insensitive; store them lower-cased.
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.transport = transport

    def select(self, css: str) -> List[Node]:
        return self.root.select(css)

    def title(self) -> str:
        found = self.root.select("title")
        return found[0].text() if found else ""

    def text(self) -> str:
        try:
            return self.root.text()
        except Exception:
            return ""

    @property
    def retry_after(self) -> Optional[float]:
        return parse_retry_after(self.headers.get("retry-after"))


class HttpTransport:
    """Fetch server-rendered result pages with ``requests``.

    Requires the optional ``http`` extra (``pip install headless-driver[http]``).
    Its TLS fingerprint is Python's, whatever User-Agent it sends, so prefer
    :class:`ImpersonateTransport` from datacentre addresses.
    """

    name = "http"

    def __init__(self, timeout: float = 15.0, user_agent: str = "",
                 proxy: Optional[str] = None, headers: Optional[dict] = None):
        ok, why = http_available()
        if not ok:
            raise RuntimeError(
                f"the HTTP transport needs the 'http' extra ({why}); "
                'install it with: pip install "headless-driver[http]"')
        import requests

        from .core import default_user_agent

        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        # Without a browser User-Agent the client announces itself as
        # python-requests and every engine refuses on the first request.
        self.session.headers["User-Agent"] = user_agent or default_user_agent()
        if headers:
            self.session.headers.update(headers)
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def fetch(self, url: str, method: str = "GET", params: Optional[dict] = None,
              data: Optional[dict] = None, headers: Optional[dict] = None,
              cookies: Optional[dict] = None, key: str = "",
              profile: Optional[str] = None) -> Page:
        response = self.session.request(
            method, url, params=params, data=data, headers=headers, cookies=cookies,
            timeout=self.timeout, allow_redirects=True)
        final_url = str(response.url)
        return Page(parse_html(response.text, final_url), final_url,
                    response.status_code, dict(response.headers), self.name)

    def rotate(self, key: str = "") -> None:
        """Nothing to rotate: plain ``requests`` has one fingerprint."""

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


class ImpersonateTransport:
    """Fetch pages with a real browser's TLS and HTTP/2 fingerprint.

    Backed by ``curl_cffi`` (``pip install "headless-driver[impersonate]"``).
    Each engine gets its own session with a randomly chosen browser profile,
    per thread, so cookies never leak between engines and a fleet does not
    present one fingerprint that can be rate-limited as a unit. After a
    refusal, :meth:`rotate` rebuilds that engine's session as a different
    browser.

    The profile supplies a matching ``User-Agent``, ``Accept-*`` and
    ``sec-ch-ua`` set, so none are overridden unless the caller passes
    ``user_agent`` explicitly — overriding them would re-create exactly the
    mismatch this transport exists to avoid.
    """

    name = "impersonate"

    def __init__(self, timeout: float = 8.0, proxy: Optional[str] = None,
                 profiles: Optional[Sequence[str]] = None, user_agent: str = "",
                 headers: Optional[dict] = None, verify: bool = True):
        ok, why = impersonate_available()
        if not ok:
            raise RuntimeError(
                f"the impersonating transport needs the 'impersonate' extra ({why}); "
                'install it with: pip install "headless-driver[impersonate]"')
        self.timeout = timeout
        # curl handles `user:pass@` in proxy URLs, unlike Chrome's --proxy-server.
        self.proxy = proxy
        self.profiles: List[str] = list(profiles or DEFAULT_PROFILES)
        if user_agent:
            # A caller-chosen UA must at least come from the same browser family.
            family = _family_of(user_agent)
            matching = [p for p in self.profiles if _family_of_profile(p) == family]
            self.profiles = matching or self.profiles
        self.user_agent = user_agent
        self.headers = dict(headers or {})
        self.verify = verify
        self._local = threading.local()
        self._all: List[Any] = []
        self._lock = threading.Lock()
        self._rng = random.Random()

    # ---------------------------------------------------------- sessions
    def _sessions(self) -> Dict[str, Tuple[Any, str]]:
        sessions = getattr(self._local, "sessions", None)
        if sessions is None:
            sessions = self._local.sessions = {}
        return sessions

    def _new_session(self, profile: str):
        from curl_cffi import requests as cr
        kwargs: Dict[str, Any] = {"impersonate": profile, "timeout": self.timeout,
                                  "verify": self.verify}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        session = cr.Session(**kwargs)
        if self.user_agent:
            session.headers["User-Agent"] = self.user_agent
        if self.headers:
            session.headers.update(self.headers)
        with self._lock:
            self._all.append(session)
        return session

    def _pick(self, avoid: str = "") -> str:
        choices = [p for p in self.profiles if p != avoid] or self.profiles
        return self._rng.choice(choices)

    def session_for(self, key: str = "", profile: Optional[str] = None):
        """This thread's session for `key`, built on first use."""
        sessions = self._sessions()
        slot = f"{key}|{profile or ''}"
        current = sessions.get(slot)
        if current is None:
            chosen = profile or self._pick()
            current = sessions[slot] = (self._new_session(chosen), chosen)
            log.debug("%s: new %s session", key or "impersonate", chosen)
        return current

    def profile_for(self, key: str = "", profile: Optional[str] = None) -> str:
        return self.session_for(key, profile)[1]

    def rotate(self, key: str = "", profile: Optional[str] = None) -> str:
        """Replace `key`'s session with one presenting a different browser."""
        sessions = self._sessions()
        slot = f"{key}|{profile or ''}"
        old = sessions.pop(slot, None)
        if old is not None:
            _close_quietly(old[0])
        if profile:
            # A pinned profile cannot change browser, only its session and cookies.
            return profile
        chosen = self._pick(avoid=old[1] if old else "")
        sessions[slot] = (self._new_session(chosen), chosen)
        log.debug("%s: rotated fingerprint to %s", key or "impersonate", chosen)
        return chosen

    # ------------------------------------------------------------- fetch
    def fetch(self, url: str, method: str = "GET", params: Optional[dict] = None,
              data: Optional[dict] = None, headers: Optional[dict] = None,
              cookies: Optional[dict] = None, key: str = "",
              profile: Optional[str] = None) -> Page:
        for _ in range(len(self.profiles) + 1):
            session, chosen = self.session_for(key, profile)
            try:
                response = session.request(
                    method, url, params=params, data=data, headers=headers,
                    cookies=cookies, allow_redirects=True)
                break
            except Exception as e:
                if "mpersonat" not in str(e) or profile:
                    raise
                # This curl_cffi build does not know the profile; drop it.
                log.debug("profile %s unsupported here; dropping it", chosen)
                if chosen in self.profiles and len(self.profiles) > 1:
                    self.profiles.remove(chosen)
                self._sessions().pop(f"{key}|", None)
        else:  # pragma: no cover - every profile failed
            raise RuntimeError("no usable impersonation profile")
        final_url = str(response.url)
        return Page(parse_html(response.text, final_url), final_url,
                    response.status_code, dict(response.headers), self.name)

    def close(self) -> None:
        with self._lock:
            sessions, self._all = self._all, []
        for session in sessions:
            _close_quietly(session)
        self._local = threading.local()


def _close_quietly(session) -> None:
    try:
        session.close()
    except Exception:
        pass


def _family_of(user_agent: str) -> str:
    ua = user_agent.lower()
    if "firefox/" in ua:
        return "firefox"
    if "edg/" in ua:
        return "edge"
    if "chrome/" in ua or "crios/" in ua:
        return "chrome"
    if "safari/" in ua:
        return "safari"
    return ""


def _family_of_profile(profile: str) -> str:
    p = profile.lower()
    for family in ("firefox", "edge", "chrome", "safari"):
        if p.startswith(family):
            return family
    return ""


class BrowserTransport:
    """Drive a page load through Selenium."""

    name = "browser"

    def __init__(self, driver, wait_timeout: float = 8.0):
        self.driver = driver
        self.wait_timeout = wait_timeout

    def fetch(self, url: str, ready_selector: str = "") -> Page:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException

        self.driver.get(url)
        if ready_selector:
            try:
                WebDriverWait(self.driver, self.wait_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ready_selector)))
            except TimeoutException:
                pass  # the caller inspects the page and decides what happened
        root = BrowserNode(self.driver.find_element(By.TAG_NAME, "html"))
        return Page(root, self.driver.current_url or url, transport=self.name)

    def rotate(self, key: str = "") -> None:
        """A browser's fingerprint is the browser's own."""

    def close(self) -> None:
        pass
