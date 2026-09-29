"""A local stand-in for every search engine, serving the pages in ``site/``.

It behaves like the real engines where the scraper depends on it: DuckDuckGo
and Startpage answer POSTed forms, Yahoo insists on its path tokens, Google
serves its JavaScript wall unless asked by the Search App client, Startpage
refuses a search without the ``sc`` token from its home page — and every
request is logged so tests can assert on exactly what was sent.

Magic words in a query make an engine misbehave, either every engine
(``zzzblock``) or one (``zzzblock_brave``):

=============  ==============================================
``nothing``    the engine's genuine "no results" page
``block``      HTTP 403
``slow``       HTTP 429 with ``Retry-After: 30``
``captcha``    a 200 captcha page
``layout``     markup the scraper has never seen
``sleep``      answers after two seconds
=============  ==============================================

Run it by hand to browse the site: ``python tests/e2e/server.py``.
"""

import sys
import html
import time
import threading
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

SITE = pathlib.Path(__file__).parent / "site"
TOKEN = "e2e-sc-token-2026"

ENGINES = ("brave", "duckduckgo", "duckduckgo_lite", "yahoo", "mojeek",
           "google_basic", "bing", "startpage")

# Which page each engine serves for a normal answer and for "no results".
PAGES = {
    "brave": ("brave.html", "brave_empty.html"),
    "duckduckgo": ("duckduckgo.html", "duckduckgo_empty.html"),
    "duckduckgo_lite": ("duckduckgo_lite.html", "duckduckgo_empty.html"),
    "yahoo": ("yahoo.html", "yahoo_empty.html"),
    "mojeek": ("mojeek.html", "mojeek_empty.html"),
    "google_basic": ("google_basic.html", "google_basic.html"),
    "bing": ("bing.html", "bing.html"),
    "startpage": ("startpage.html", "startpage.html"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "e2e-search/1.0"

    def log_message(self, *args):  # keep test output clean
        pass

    # ---------------------------------------------------------------- utils
    def _send(self, status, body, headers=None, content_type="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _page(self, name, query=""):
        text = (SITE / name).read_text(encoding="utf-8")
        return text.replace("{query}", html.escape(query))

    def _fields(self):
        parts = urlparse(self.path)
        fields = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            fields.update({k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()})
        return parts, fields

    def _cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    # ------------------------------------------------------------- routing
    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        parts, fields = self._fields()
        segments = parts.path.strip("/").split("/")
        engine = segments[0] if segments else ""
        if engine in ("", "index.html"):
            return self._send(200, self._page("index.html"))
        if engine.endswith(".html") and (SITE / engine).exists():
            return self._send(200, self._page(engine))
        if engine == "api":
            # A JSON API the pages could render from, for capture_json().
            import json
            data = json.loads((SITE.parent / "data.json").read_text(encoding="utf-8"))
            body = json.dumps({"results": [{"url": r["url"], "title": r["title"]}
                                           for r in data["results"]]})
            return self._send(200, body, content_type="application/json")
        if engine not in ENGINES:
            return self._send(404, "<h1>404</h1>")

        query = fields.get("q") or fields.get("p") or fields.get("query") or ""
        # The raw path: urlparse() would move Yahoo's ";_ylt=" into .params.
        raw_path = self.path.split("?", 1)[0]
        record = {"engine": engine, "method": self.command, "path": raw_path,
                  "fields": fields, "cookies": self._cookies(),
                  "user_agent": self.headers.get("User-Agent", ""),
                  "referer": self.headers.get("Referer", ""), "query": query}
        with self.server.lock:
            self.server.log.append(record)

        # Startpage's home page hands out the token its search form needs.
        if engine == "startpage" and len(segments) == 1:
            return self._send(200, self._page("startpage_home.html"))
        if engine == "startpage" and fields.get("sc") != TOKEN and self.command == "POST":
            return self._send(403, "<h1>403 - missing sc token</h1>")
        # Yahoo's links always carry fresh path tokens.
        if engine == "yahoo" and not (";_ylt=" in raw_path and ";_ylu=" in raw_path):
            return self._send(400, "<h1>400 - missing path tokens</h1>")
        # Google shows its JavaScript wall to anything but the Search App.
        if engine == "google_basic":
            ua = self.headers.get("User-Agent", "")
            if not ua.endswith("NSTNWV") or self._cookies().get("CONSENT") != "YES+":
                return self._send(200, self._page("google_enablejs.html"))

        def trigger(word):
            return f"zzz{word}" in query.split() or f"zzz{word}_{engine}" in query.split()

        if trigger("sleep"):
            time.sleep(2)
        if trigger("block"):
            return self._send(403, "<html><head><title>403 - Forbidden</title></head>"
                                   "<body><h1>403 - Forbidden</h1></body></html>")
        if trigger("slow"):
            return self._send(429, "<h1>Too Many Requests</h1>", {"Retry-After": "30"})
        if trigger("captcha"):
            return self._send(200, self._page("captcha.html"))
        if trigger("layout"):
            return self._send(200, self._page("new_layout.html", query))
        ok, empty = PAGES[engine]
        return self._send(200, self._page(empty if trigger("nothing") else ok, query))


class SearchSite:
    """Start the site on a free port; ``with SearchSite() as site: site.url``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.log = []
        self.httpd.lock = threading.Lock()
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def log(self):
        return self.httpd.log

    def requests_to(self, engine: str):
        return [r for r in self.httpd.log if r["engine"] == engine]

    def clear(self) -> None:
        with self.httpd.lock:
            self.httpd.log.clear()

    def start(self) -> "SearchSite":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def point_at(scraper, base: str, js_free_startpage: bool = True) -> None:
    """Re-aim a scraper's engines at the local site, keeping every other part
    of each engine's definition — request shape, selectors, unwrappers — real."""
    import secrets
    for name in ENGINES:
        spec = dict(scraper.engines[name])
        if name == "yahoo":
            spec["url"] = f"{base}/yahoo/search"
            spec["url_builder"] = (lambda: f"{base}/yahoo/search"
                                   f";_ylt={secrets.token_urlsafe(18)}"
                                   f";_ylu={secrets.token_urlsafe(35)}")
        elif name == "startpage":
            spec["url"] = f"{base}/startpage/sp/search"
            spec["home"] = f"{base}/startpage"
            if js_free_startpage:
                spec["js"] = False
        elif name == "duckduckgo_lite":
            spec["url"] = f"{base}/duckduckgo_lite/lite/"
        elif name == "duckduckgo":
            spec["url"] = f"{base}/duckduckgo/html/"
        else:
            spec["url"] = f"{base}/{name}/search"
        scraper.engines[name] = spec


if __name__ == "__main__":  # pragma: no cover
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    site = SearchSite(port=port).start()
    print(f"serving {SITE} at {site.url}  (ctrl-c to stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        site.stop()
