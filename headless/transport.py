"""Ways of fetching a search results page.

Most engines in :data:`~headless.scraper.ENGINE_SPECS` render their results on
the server, so fetching them needs an HTTP client and an HTML parser, not a
browser. :class:`HttpTransport` is roughly two orders of magnitude cheaper than
driving Chrome — hundreds of milliseconds and a few megabytes, against seconds
and a gigabyte — and, having no WebDriver session, it is thread-safe.

Both transports expose the same tiny document model, so the extraction code in
:mod:`headless.scraper` does not care which one produced the page.
"""

from typing import List, Optional, Tuple
from urllib.parse import urljoin

from .logs import get_logger

log = get_logger("transport")

#: Sent by :class:`HttpTransport`. Chrome's own headers, minus the ones a real
#: browser would add per-connection.
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def http_available() -> Tuple[bool, str]:
    """Whether the optional HTTP transport dependencies are importable."""
    try:
        import requests  # noqa: F401
    except ImportError:
        return False, "requests is not installed"
    try:
        import bs4  # noqa: F401
    except ImportError:
        return False, "beautifulsoup4 is not installed"
    return True, ""


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

    def __init__(self, root: Node, url: str, status: int = 200):
        self.root = root
        self.url = url
        self.status = status

    def select(self, css: str) -> List[Node]:
        return self.root.select(css)


class HttpTransport:
    """Fetch server-rendered result pages with an HTTP client.

    Requires the optional ``http`` extra (``pip install headless-driver[http]``).
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

    def fetch(self, url: str) -> Page:
        from bs4 import BeautifulSoup

        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        # A parser that ships with Python, so the extra stays to one package.
        soup = BeautifulSoup(response.text, "html.parser")
        final_url = str(response.url)
        return Page(HtmlNode(soup, final_url), final_url, response.status_code)

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass


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
        return Page(root, self.driver.current_url or url)

    def close(self) -> None:
        pass
