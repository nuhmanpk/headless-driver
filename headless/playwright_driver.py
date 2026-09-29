"""Playwright as a browser backend.

Selenium drives a browser through WebDriver, which cannot see the HTTP layer:
no status codes, no response headers, no way to stop a page downloading its
images. Playwright talks to the browser over its devtools protocol and can do
all of that, which matters for scraping:

* **Real status codes and headers from the browser** — a 403 or a 429 with
  ``Retry-After`` is classified exactly as it is over HTTP, instead of being
  guessed from the page body.
* **Resource blocking** — images, media and fonts are never downloaded, so a
  results page loads in a fraction of the time and bandwidth.
* **Stealth** — an init script hides the usual automation tells
  (``navigator.webdriver``, empty plugin lists, missing ``window.chrome``),
  and locale, timezone and User-Agent are set together so they agree.
* **One isolated context per engine** — cookies never leak between engines,
  and a refused context is thrown away and rebuilt (:meth:`rotate`).
* **Proxies with credentials** — ``http://user:pass@host:port`` works, which
  Chrome's ``--proxy-server`` switch silently ignores.
* **Structured extraction, JSON capture, infinite scroll, tracing** — see
  :class:`PlaywrightBrowser`.

Requires the optional extra and a browser build::

    pip install "headless-driver[playwright]"
    playwright install chromium
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Pattern, Sequence, Tuple, Union
from urllib.parse import urlparse, unquote

from .logs import get_logger
from .transport import Page, parse_html

log = get_logger("playwright")

#: Resource types not worth downloading to read a results page.
DEFAULT_BLOCKED_RESOURCES = ("image", "media", "font")

#: Hides the automation tells headless Chromium exposes to page scripts.
STEALTH_SCRIPT = r"""
(() => {
  const define = (obj, prop, value) => {
    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); }
    catch (e) {}
  };
  define(Navigator.prototype, 'webdriver', undefined);
  if (!window.chrome) { window.chrome = { runtime: {}, app: { isInstalled: false } }; }
  if (!navigator.plugins || navigator.plugins.length === 0) {
    define(Navigator.prototype, 'plugins', [1, 2, 3, 4, 5].map(i => ({ name: 'Plugin ' + i })));
  }
  if (!navigator.languages || navigator.languages.length === 0) {
    define(Navigator.prototype, 'languages', ['en-US', 'en']);
  }
  const query = window.navigator.permissions && window.navigator.permissions.query;
  if (query) {
    window.navigator.permissions.query = (p) => p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : query.call(window.navigator.permissions, p);
  }
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Intel Inc.';
      if (p === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.call(this, p);
    };
  } catch (e) {}
})();
"""


# Playwright's sync API allows one driver per thread, so every
# PlaywrightBrowser on a thread shares it, reference-counted.
_driver_local = threading.local()


def _acquire_driver() -> Dict[str, Any]:
    shared = getattr(_driver_local, "shared", None)
    if shared is None:
        from playwright.sync_api import sync_playwright
        shared = _driver_local.shared = {"pw": sync_playwright().start(), "refs": 0,
                                         "thread": threading.get_ident()}
    shared["refs"] += 1
    return shared


def _release_driver(shared: Dict[str, Any]) -> None:
    shared["refs"] -= 1
    # Only the owning thread may stop it; another thread's driver is left to exit
    # with the process rather than crash trying.
    if shared["refs"] <= 0 and shared["thread"] == threading.get_ident():
        try:
            shared["pw"].stop()
        except Exception:
            pass
        if getattr(_driver_local, "shared", None) is shared:
            _driver_local.shared = None


def playwright_available() -> Tuple[bool, str]:
    """Whether the Playwright extra is importable (browsers are checked on launch)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False, "playwright is not installed"
    try:
        import bs4  # noqa: F401
    except ImportError:
        return False, "beautifulsoup4 is not installed"
    return True, ""


def proxy_settings(proxy: Optional[str]) -> Optional[Dict[str, str]]:
    """Split ``scheme://user:pass@host:port`` into Playwright's proxy dict."""
    if not proxy:
        return None
    parts = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    out = {"server": f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")}
    if parts.username:
        out["username"] = unquote(parts.username)
    if parts.password:
        out["password"] = unquote(parts.password)
    return out


class PlaywrightBrowser:
    """A Playwright-driven browser with scraping conveniences built in.

    Every public method is safe to call from any thread: each thread gets its
    own Playwright instance, because Playwright's sync API is bound to the
    thread that started it.

    ::

        with PlaywrightBrowser(block_resources=True) as browser:
            html, status = browser.fetch_html("https://example.com")
            browser.screenshot("https://example.com", "shot.png", full_page=True)
            rows = browser.extract("https://news.ycombinator.com",
                                   {"title": ".titleline a", "link": ".titleline a@href"},
                                   item_selector="tr.athing")
            api = browser.capture_json("https://example.com/app", r"/api/")
    """

    def __init__(self, browser: str = "chromium", headless: bool = True,
                 proxy: Optional[str] = None, user_agent: Optional[str] = None,
                 locale: str = "en-US", timezone_id: Optional[str] = None,
                 viewport: Optional[Tuple[int, int]] = (1366, 768),
                 device: Optional[str] = None, stealth: bool = True,
                 block_resources: Union[bool, Sequence[str]] = True,
                 timeout: float = 20.0, trace_dir: Optional[str] = None,
                 extra_headers: Optional[Dict[str, str]] = None,
                 launch_args: Optional[Sequence[str]] = None):
        ok, why = playwright_available()
        if not ok:
            raise RuntimeError(
                f"the Playwright backend needs the 'playwright' extra ({why}); install it "
                'with: pip install "headless-driver[playwright]" && playwright install chromium')
        if browser not in ("chromium", "firefox", "webkit"):
            raise ValueError("browser must be 'chromium', 'firefox' or 'webkit'")
        self.browser_name = browser
        self.headless = headless
        self.proxy = proxy
        self.user_agent = user_agent
        self.locale = locale
        self.timezone_id = timezone_id
        self.viewport = viewport
        self.device = device
        self.stealth = stealth
        if block_resources is True:
            self.blocked_resources = set(DEFAULT_BLOCKED_RESOURCES)
        elif block_resources:
            self.blocked_resources = set(block_resources)
        else:
            self.blocked_resources = set()
        self.timeout_ms = int(timeout * 1000)
        self.trace_dir = trace_dir
        self.extra_headers = dict(extra_headers or {})
        self.launch_args = list(launch_args or [])
        self._local = threading.local()
        self._all: List[Tuple[Any, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    def _browser(self):
        state = getattr(self._local, "state", None)
        if state is None:
            shared = _acquire_driver()
            pw = shared["pw"]
            launcher = getattr(pw, self.browser_name)
            args = list(self.launch_args)
            if self.browser_name == "chromium":
                args.append("--disable-blink-features=AutomationControlled")
            browser = launcher.launch(headless=self.headless, args=args,
                                      proxy=proxy_settings(self.proxy))
            state = self._local.state = {"pw": pw, "browser": browser, "contexts": {}}
            with self._lock:
                self._all.append((shared, browser))
            log.debug("launched %s (headless=%s)", self.browser_name, self.headless)
        return state

    def _context_options(self) -> Dict[str, Any]:
        state = self._browser()
        options: Dict[str, Any] = {}
        if self.device:
            options.update(state["pw"].devices[self.device])
        if self.viewport and not self.device:
            options["viewport"] = {"width": self.viewport[0], "height": self.viewport[1]}
        if self.user_agent:
            options["user_agent"] = self.user_agent
        elif self.stealth and not self.device and self.browser_name == "chromium":
            # Headless Chromium announces itself as "HeadlessChrome"; send the
            # User-Agent the same build sends with a window.
            options["user_agent"] = headful_user_agent(state["browser"].version)
        if self.locale:
            options["locale"] = self.locale
        if self.timezone_id:
            options["timezone_id"] = self.timezone_id
        if self.extra_headers:
            options["extra_http_headers"] = self.extra_headers
        return options

    def context(self, key: str = "default"):
        """This thread's browser context for `key`, created on first use."""
        state = self._browser()
        ctx = state["contexts"].get(key)
        if ctx is None:
            ctx = state["browser"].new_context(**self._context_options())
            ctx.set_default_timeout(self.timeout_ms)
            ctx.set_default_navigation_timeout(self.timeout_ms)
            if self.stealth:
                ctx.add_init_script(STEALTH_SCRIPT)
            if self.blocked_resources:
                blocked = self.blocked_resources

                def route(r):
                    if r.request.resource_type in blocked:
                        return r.abort()
                    return r.continue_()
                ctx.route("**/*", route)
            if self.trace_dir:
                ctx.tracing.start(screenshots=True, snapshots=True)
            state["contexts"][key] = ctx
        return ctx

    def rotate(self, key: str = "default") -> None:
        """Discard `key`'s context — cookies, storage, fingerprint state — for a fresh one."""
        state = getattr(self._local, "state", None)
        if not state:
            return
        ctx = state["contexts"].pop(key, None)
        if ctx is not None:
            self._close_context(ctx, key)

    def _close_context(self, ctx, key: str) -> None:
        if self.trace_dir:
            import os
            os.makedirs(self.trace_dir, exist_ok=True)
            try:
                ctx.tracing.stop(path=os.path.join(self.trace_dir, f"{key}.zip"))
            except Exception:
                pass
        try:
            ctx.close()
        except Exception:
            pass

    def _open(self, url: str, key: str, wait_for: str = "", wait_until: str = "domcontentloaded"):
        page = self.context(key).new_page()
        response = page.goto(url, wait_until=wait_until)
        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=min(self.timeout_ms, 8000))
            except Exception:
                pass  # the caller inspects the page and decides what happened
        return page, response

    # -------------------------------------------------------------- basics
    def fetch_html(self, url: str, key: str = "default", wait_for: str = "") -> Tuple[str, int]:
        """Load `url` and return ``(html, status)``."""
        page, response = self._open(url, key, wait_for)
        try:
            return page.content(), (response.status if response else 200)
        finally:
            page.close()

    def fetch_page(self, url: str, key: str = "default", wait_for: str = "") -> Page:
        """Load `url` as a :class:`~headless.transport.Page`, with status and headers."""
        page, response = self._open(url, key, wait_for)
        try:
            html = page.content()
            final_url = page.url or url
            status = response.status if response else 200
            headers = dict(response.headers) if response else {}
        finally:
            page.close()
        return Page(parse_html(html, final_url), final_url, status, headers, "playwright")

    def screenshot(self, url: str, path: str, full_page: bool = True,
                   key: str = "default", wait_for: str = "") -> bool:
        """Save a screenshot of `url`. Images are loaded for this, whatever
        `block_resources` says, because a screenshot without them is useless."""
        import os
        key = f"{key}#media"
        saved_block, self.blocked_resources = self.blocked_resources, set()
        try:
            page, _ = self._open(url, key, wait_for, wait_until="load")
        finally:
            self.blocked_resources = saved_block
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            page.screenshot(path=path, full_page=full_page)
            return True
        except Exception as e:
            log.warning("screenshot of %s failed: %s", url, e)
            return False
        finally:
            page.close()

    def pdf(self, url: str, path: str, key: str = "default") -> bool:
        """Save `url` as PDF (Chromium only)."""
        import os
        if self.browser_name != "chromium":
            raise RuntimeError("PDF export needs chromium")
        page, _ = self._open(url, f"{key}#media", wait_until="load")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            page.pdf(path=path, print_background=True)
            return True
        except Exception as e:
            log.warning("PDF of %s failed: %s", url, e)
            return False
        finally:
            page.close()

    # ------------------------------------------------------ scraping extras
    def extract(self, url: str, schema: Dict[str, str], item_selector: Optional[str] = None,
                key: str = "default", scroll: int = 0) -> Union[Dict[str, str], List[Dict[str, str]]]:
        """Pull structured data out of a page with a declarative schema.

        Each schema value is a CSS selector, optionally ending ``@attr`` to read
        an attribute instead of text: ``{"title": "h2", "link": "a@href"}``.
        With `item_selector`, the schema is applied inside every matching
        element and a list is returned; without it, once to the whole page.
        `scroll` scrolls to the bottom that many times first, for pages that
        load more as you go.
        """
        page, _ = self._open(url, key, item_selector or "")
        try:
            if scroll:
                self.scroll_to_bottom(page, rounds=scroll)
            fields = {name: _split_selector(sel) for name, sel in schema.items()}
            script = """([item, fields]) => {
                const read = (root, [css, attr]) => {
                  const el = css ? root.querySelector(css) : root;
                  if (!el) return '';
                  if (attr) { const v = attr === 'href' || attr === 'src' ? el[attr] : el.getAttribute(attr);
                              return (v || '').toString().trim(); }
                  return (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                };
                const one = (root) => Object.fromEntries(
                  Object.entries(fields).map(([k, f]) => [k, read(root, f)]));
                if (!item) return one(document);
                return Array.from(document.querySelectorAll(item)).map(one);
            }"""
            data = page.evaluate(script, [item_selector, fields])
            return _normalize(data)
        finally:
            page.close()

    def capture_json(self, url: str, pattern: Union[str, Pattern] = "",
                     key: str = "default", wait: float = 1.5,
                     action: Optional[Callable[[Any], None]] = None) -> List[Dict[str, Any]]:
        """Load `url` and return the JSON bodies of responses whose URL matches.

        Many modern sites render from their own JSON API; reading that is more
        reliable than scraping the HTML it produces. `action(page)` can click
        or scroll to trigger more requests before collecting.
        """
        import re
        regex = re.compile(pattern) if isinstance(pattern, str) else pattern
        captured: List[Dict[str, Any]] = []
        page = self.context(key).new_page()

        def on_response(response):
            if not regex.search(response.url):
                return
            if "json" not in (response.headers.get("content-type") or ""):
                return
            try:
                captured.append({"url": response.url, "status": response.status,
                                 "json": response.json()})
            except Exception:
                pass
        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded")
            if action:
                action(page)
            page.wait_for_timeout(int(wait * 1000))
            return captured
        finally:
            page.close()

    @staticmethod
    def scroll_to_bottom(page, rounds: int = 10, pause_ms: int = 400) -> int:
        """Scroll until the page stops growing or `rounds` runs out; returns rounds used."""
        last = -1
        for i in range(rounds):
            height = page.evaluate("document.body ? document.body.scrollHeight : 0")
            if height == last:
                return i
            last = height
            page.mouse.wheel(0, height)
            page.wait_for_timeout(pause_ms)
        return rounds

    # ---------------------------------------------------------- lifecycle
    def close(self) -> None:
        state = getattr(self._local, "state", None)
        if state:
            for key, ctx in list(state["contexts"].items()):
                self._close_context(ctx, key)
        with self._lock:
            instances, self._all = self._all, []
        for shared, browser in instances:
            try:
                browser.close()
            except Exception:
                pass
            _release_driver(shared)
        self._local = threading.local()

    def __enter__(self) -> "PlaywrightBrowser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def headful_user_agent(version: str) -> str:
    """The User-Agent a windowed Chrome of `version` sends on this OS."""
    from .core import _platform_token
    return (f"Mozilla/5.0 ({_platform_token()}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")


def _normalize(data):
    from .scraper import normalize_text
    if isinstance(data, list):
        return [_normalize(d) for d in data]
    if isinstance(data, dict):
        return {k: normalize_text(v) if isinstance(v, str) else v for k, v in data.items()}
    return data


def _split_selector(selector: str) -> List[str]:
    """``"a.title@href"`` → ``["a.title", "href"]``; ``"h2"`` → ``["h2", ""]``."""
    if "@" in selector:
        css, attr = selector.rsplit("@", 1)
        return [css.strip(), attr.strip()]
    return [selector, ""]


class PlaywrightTransport:
    """Serve browser engines through Playwright, with real status codes."""

    name = "playwright"

    def __init__(self, browser: Optional[PlaywrightBrowser] = None, **options):
        self.browser = browser or PlaywrightBrowser(**options)

    def fetch(self, url: str, ready_selector: str = "", key: str = "default") -> Page:
        return self.browser.fetch_page(url, key=key, wait_for=ready_selector)

    def rotate(self, key: str = "default", profile: Optional[str] = None) -> None:
        self.browser.rotate(key)

    def close(self) -> None:
        self.browser.close()
