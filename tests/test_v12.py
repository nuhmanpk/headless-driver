"""Tests for 1.2: typed models, pagination, caching, Markdown, agent tools, MCP."""

import io
import os
import sys
import json
import asyncio
import tempfile
import unittest
import contextlib
from unittest import mock

from headless import (
    AdvancedSearchScraper, SearchResponse, EngineAttempt, EngineHealth, SearchResult,
    MemoryCache, SQLiteCache, RedisCache, make_cache, Toolkit,
    fetch_markdown, html_to_markdown, chunk_markdown, count_tokens,
)
from headless import cli
from headless.cache import cache_key
from headless.results import STATUS_OK, STATUS_EMPTY, STATUS_BLOCKED, SKIP_COOLING
from headless.transport import Page, parse_html, http_available
from headless import markdown as md_module
from headless import tools as tools_module

try:
    from .support import HermeticTestCase
except ImportError:  # run as top-level modules by `discover -s tests`
    from support import HermeticTestCase

HAS_BS4 = http_available()[0]


def page_from(html, url="https://example.com/search", status=200, headers=None):
    return Page(parse_html(html, url), url, status, headers or {}, "http")


def brave_page(*urls):
    items = "".join(f'<div data-type="web"><a href="{u}"><div class="title">T {u}</div></a>'
                    f'<div class="snippet"><div class="content">about {u}</div></div></div>'
                    for u in urls)
    return f'<html><body>{items}<div id="search-elsewhere"></div></body></html>'


class PagedTransport:
    """Serves different results per page, keyed by each engine's page parameter."""

    name = "http"

    def __init__(self, pages_by_engine):
        self.pages_by_engine = pages_by_engine   # engine -> {page: html}
        self.requests = []

    def fetch(self, url, key="", **kw):
        fields = kw.get("params") or kw.get("data") or {}
        self.requests.append(dict(kw, url=url, key=key))
        page = 1
        if "offset" in fields:
            page = int(fields["offset"]) + 1
        elif "b" in fields:
            page = (int(fields["b"]) - 1) // 7 + 1
        html = self.pages_by_engine[key].get(page, brave_page())
        if isinstance(html, int):
            return page_from("<p/>", status=html)
        return page_from(html)

    def rotate(self, *a, **k):
        pass

    def close(self):
        pass


def paged_scraper(pages_by_engine, **kw):
    kw.setdefault("transport", "http")
    kw.setdefault("health", EngineHealth())
    scr = AdvancedSearchScraper(**kw)
    transport = PagedTransport(pages_by_engine)
    scr._transport_for = lambda engine: (transport, False)
    scr._fake = transport
    return scr


class TestTypedModels(HermeticTestCase):
    def test_from_dict_and_back(self):
        item = {"url": "https://a.com", "title": "A", "snippet": "s", "engine": "brave",
                "favicon": "f", "cached": None, "quick_answer": None, "custom": 7}
        model = SearchResult.from_dict(item)
        self.assertEqual(model.url, "https://a.com")
        self.assertEqual(model.engines, ["brave"])
        self.assertEqual(model.votes, 1)
        self.assertEqual(model["custom"], 7)
        self.assertEqual(model["title"], "A")
        self.assertEqual(model.get("missing", "x"), "x")
        with self.assertRaises(KeyError):
            model["missing"]
        self.assertEqual(model.as_dict()["custom"], 7)
        self.assertIn("url", list(model.keys()))

    def test_response_views(self):
        r = SearchResponse("q", results=[{"url": "https://a.com", "title": "A", "votes": 3,
                                          "engines": ["x", "y", "z"], "ranks": {"x": 1}}])
        (typed,) = r.typed()
        self.assertIsInstance(typed, SearchResult)
        self.assertEqual(typed.votes, 3)
        try:
            (model,) = r.to_pydantic()
        except RuntimeError:
            self.skipTest("pydantic not installed")
        self.assertEqual(model.votes, 3)
        self.assertEqual(model.model_dump()["engines"], ["x", "y", "z"])

    def test_response_round_trip(self):
        r = SearchResponse("q", results=[{"url": "u"}], engine="brave", mode="first",
                           attempts=[EngineAttempt("brave", STATUS_OK, http_status=200, page=2)],
                           skipped=[{"engine": "bing", "reason": "x", "resume_in": 0}])
        back = SearchResponse.from_dict(json.loads(json.dumps(r.as_dict())))
        self.assertEqual(list(back), [{"url": "u"}])
        self.assertEqual(back.attempts[0].page, 2)
        self.assertEqual(back.attempts[0].http_status, 200)
        self.assertEqual(back.skipped[0]["engine"], "bing")


class TestCacheBackends(HermeticTestCase):
    def _exercise(self, cache, clock):
        cache.set("k", {"status": "ok", "results": [{"url": "é"}]}, ttl=10)
        self.assertEqual(cache.get("k")["results"][0]["url"], "é")
        self.assertIsNone(cache.get("nope"))
        clock[0] += 11
        self.assertIsNone(cache.get("k"))
        cache.set("k2", {"a": 1}, ttl=10)
        cache.clear()
        self.assertIsNone(cache.get("k2"))

    def test_memory(self):
        clock = [0.0]
        cache = MemoryCache(maxsize=2, clock=lambda: clock[0])
        self._exercise(cache, clock)
        for key in ("a", "b", "c"):
            cache.set(key, {"k": key}, 100)
        self.assertIsNone(cache.get("a"))          # least recently used went first
        self.assertEqual(len(cache), 2)
        got = cache.get("c")
        got["k"] = "edited"
        self.assertEqual(cache.get("c"), {"k": "c"})   # a copy, not the stored value

    def test_sqlite(self):
        clock = [0.0]
        path = os.path.join(tempfile.mkdtemp(), "sub", "c.db")
        cache = SQLiteCache(path, clock=lambda: clock[0])
        self._exercise(cache, clock)
        cache.set("old", {"x": 1}, ttl=1)
        clock[0] += 5
        self.assertEqual(cache.purge(), 1)
        cache.set("keep", {"x": 2}, ttl=100)
        cache.close()
        again = SQLiteCache(path, clock=lambda: clock[0])       # persists on disk
        self.assertEqual(again.get("keep"), {"x": 2})
        again.close()

    def test_redis(self):
        try:
            import fakeredis
        except ImportError:
            self.skipTest("fakeredis not installed")
        cache = RedisCache(fakeredis.FakeRedis(), prefix="t:")
        cache.set("k", {"status": "ok", "results": []}, ttl=30)
        self.assertEqual(cache.get("k"), {"status": "ok", "results": []})
        self.assertIsNone(cache.get("other"))
        cache.clear()
        self.assertIsNone(cache.get("k"))
        cache.close()
        with mock.patch.dict("sys.modules", {"redis": None}):
            with self.assertRaises(RuntimeError):
                RedisCache("redis://localhost")

    def test_make_cache(self):
        self.assertIsNone(make_cache(None))
        self.assertIsNone(make_cache(False))
        self.assertIsInstance(make_cache(True), MemoryCache)
        self.assertIsInstance(make_cache("memory"), MemoryCache)
        tmp = tempfile.mkdtemp()
        self.assertIsInstance(make_cache(f"sqlite:///{tmp}/a.db"), SQLiteCache)
        self.assertIsInstance(make_cache(os.path.join(tmp, "b.sqlite")), SQLiteCache)
        mine = MemoryCache()
        self.assertIs(make_cache(mine), mine)
        with self.assertRaises(ValueError):
            make_cache("postgres://x")
        with self.assertRaises(TypeError):
            make_cache(42)
        with mock.patch("headless.cache.RedisCache") as RC:
            make_cache("redis://h:6379/0")
        RC.assert_called_once_with("redis://h:6379/0")

    def test_keys_ignore_case_and_spacing(self):
        self.assertEqual(cache_key("brave", "Python  Asyncio", 10),
                         cache_key("brave", "python asyncio", 10))
        self.assertNotEqual(cache_key("brave", "q", 10), cache_key("yahoo", "q", 10))
        self.assertNotEqual(cache_key("brave", "q", 10, page=2), cache_key("brave", "q", 10))
        self.assertNotEqual(cache_key("brave", "q", 10, region="uk-en"),
                            cache_key("brave", "q", 10))


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestPagination(HermeticTestCase):
    def test_first_mode_walks_pages_and_tags_them(self):
        scr = paged_scraper({"brave": {1: brave_page("https://a.com", "https://b.com"),
                                       2: brave_page("https://c.com"),
                                       3: brave_page()}},
                            search_engine="brave", fallback=False, max_results=5)
        response = scr.search("q", pages=3)
        self.assertEqual([r["url"] for r in response], ["https://a.com", "https://b.com",
                                                        "https://c.com"])
        self.assertEqual([r["page"] for r in response], [1, 1, 2])
        self.assertEqual([a.page for a in response.attempts], [1, 2, 3])
        self.assertEqual(response.engines_tried, ["brave"])
        offsets = [(r.get("params") or {}).get("offset") for r in scr._fake.requests]
        self.assertEqual(offsets, [None, "1", "2"])

    def test_single_page_results_are_unchanged(self):
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}},
                            search_engine="brave", fallback=False)
        response = scr.search("q")
        self.assertNotIn("page", response[0])
        self.assertEqual(len(scr._fake.requests), 1)

    def test_stops_on_repeats_and_refusals(self):
        scr = paged_scraper({"brave": {1: brave_page("https://a.com"),
                                       2: brave_page("https://a.com")}},
                            search_engine="brave", fallback=False)
        self.assertEqual(len(scr.search("q", pages=5)), 1)
        self.assertEqual(len(scr._fake.requests), 2)      # page 2 added nothing: stop
        scr = paged_scraper({"brave": {1: brave_page("https://a.com"), 2: 429}},
                            search_engine="brave", fallback=False)
        response = scr.search("q", pages=3)
        self.assertEqual(len(response), 1)
        self.assertEqual(response.attempts[-1].status, "rate_limited")

    def test_engines_without_paging_return_one_page(self):
        scr = AdvancedSearchScraper(transport="http")
        self.assertFalse(scr.paginates("duckduckgo_js"))
        self.assertTrue(scr.paginates("brave"))
        self.assertIn("first=11", scr._engine_url("q", "bing", page=2))
        self.assertIn("start=10", scr._engine_url("q", "google", page=2))
        self.assertEqual(scr._build_request("q", "yahoo", page=3)["params"]["b"], "15")
        self.assertEqual(scr._build_request("q", "duckduckgo", page=2)["data"]["s"], "10")
        self.assertNotIn("s", scr._build_request("q", "duckduckgo")["data"])

    def test_aggregate_paginates_each_engine(self):
        yahoo = lambda *u: ("<html><body>" + "".join(
            f"<div class='relsrch'><div class='compTitle'><h3><a href='{x}'>Y</a></h3></div></div>"
            for x in u) + "</body></html>")
        scr = paged_scraper({"brave": {1: brave_page("https://a.com"), 2: brave_page("https://b.com")},
                             "yahoo": {1: yahoo("https://b.com"), 2: yahoo("https://a.com")}},
                            circuit_breaker=False, max_results=5)
        response = scr.search("q", mode="aggregate", engines=["brave", "yahoo"], pages=2)
        self.assertEqual({r["url"]: r["votes"] for r in response},
                         {"https://a.com": 2, "https://b.com": 2})

    def test_validation(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(pages=0)
        with self.assertRaises(ValueError):
            AdvancedSearchScraper().search("q", pages=0)


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestCachingInSearch(HermeticTestCase):
    def test_second_search_asks_nobody(self):
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}},
                            search_engine="brave", fallback=False, cache="memory")
        first = scr.search("Python  Asyncio")
        second = scr.search("python asyncio")
        self.assertEqual(list(first), list(second))
        self.assertEqual(len(scr._fake.requests), 1)
        self.assertTrue(second.cached)
        self.assertFalse(first.cached)
        self.assertEqual(second.attempts[0].transport, "cache")
        scr.clear_cache()
        scr.search("python asyncio")
        self.assertEqual(len(scr._fake.requests), 2)
        scr.quit()

    def test_refusals_are_never_cached(self):
        scr = paged_scraper({"brave": {1: 403}}, search_engine="brave", fallback=False,
                            cache="memory", circuit_breaker=False)
        scr.search("q")
        scr.search("q")
        self.assertEqual(len(scr._fake.requests), 2)

    def test_empty_uses_its_own_ttl(self):
        cache = MemoryCache()
        scr = paged_scraper({"brave": {1: brave_page()}}, search_engine="brave",
                            fallback=False, cache=cache, cache_empty_ttl=0)
        scr.search("q")
        scr.search("q")
        self.assertEqual(len(scr._fake.requests), 2)       # ttl 0: not cached
        self.assertEqual(len(cache), 0)

    def test_cached_engine_answers_while_cooling(self):
        health = EngineHealth()
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}}, search_engine="brave",
                            fallback=False, cache="memory", health=health)
        scr.search("q")
        health.trip("brave", 60)
        response = scr.search("q")
        self.assertEqual(response[0]["url"], "https://a.com")
        self.assertFalse(response.skipped)
        other = scr.search("different")
        self.assertTrue(other.cooling)
        self.assertEqual(other.skipped[0]["reason"], SKIP_COOLING)

    def test_aggregate_uses_the_cache(self):
        cache = MemoryCache()
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")},
                             "mojeek": {1: "<ul class='results-standard'><li><h2><a href="
                                           "'https://a.com'>M</a></h2></li></ul>"}},
                            cache=cache, circuit_breaker=False)
        scr.search("q", mode="aggregate", engines=["brave", "mojeek"])
        before = len(scr._fake.requests)
        again = scr.search("q", mode="aggregate", engines=["brave", "mojeek"])
        self.assertEqual(len(scr._fake.requests), before)
        self.assertEqual(again[0]["votes"], 2)
        self.assertTrue(again.cached)

    def test_unserialisable_results_are_skipped_not_fatal(self):
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}}, search_engine="brave",
                            fallback=False, cache="memory",
                            result_processor=lambda q, item: dict(item, obj=object()))
        self.assertEqual(len(scr.search("q")), 1)
        self.assertEqual(len(scr.search("q")), 1)
        self.assertEqual(len(scr._fake.requests), 2)

    def test_broken_cache_does_not_break_search(self):
        broken = mock.Mock()
        broken.get.side_effect = ConnectionError("down")
        broken.set.side_effect = ConnectionError("down")
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}}, search_engine="brave",
                            fallback=False, cache=broken)
        self.assertEqual(len(scr.search("q")), 1)

    def test_owned_cache_is_closed(self):
        path = os.path.join(tempfile.mkdtemp(), "c.db")
        scr = AdvancedSearchScraper(cache=f"sqlite:///{path}")
        with mock.patch.object(scr.cache, "close") as close:
            scr.quit()
        close.assert_called_once()
        mine = MemoryCache()
        with mock.patch.object(mine, "close") as close:
            AdvancedSearchScraper(cache=mine).quit()
        close.assert_not_called()


ARTICLE = """<html><head><title>Guide &amp; Notes</title></head><body>
<header><nav><a href="/">Home</a></nav></header>
<div id="cookie-consent">Accept cookies</div>
<main><h1>Guide &amp; Notes</h1><p>Intro with <a href="/more">a link</a> and <code>code()</code>.</p>
<h2>Setup <a class="headerlink" href="#setup">¶</a></h2>
<pre><code class="language-python">def f():
    return   1</code></pre>
<ol><li>Step <b>one</b></li><li>Step two</li></ol>
<dl><dt>Term</dt><dd>Definition</dd></dl>
<p><img src="/i.png" alt="diagram"> Caption<br>next line</p>
<p style="display:none">hidden text</p>
</main><footer>Footer</footer></body></html>"""


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestMarkdown(HermeticTestCase):
    def test_conversion(self):
        md, title, links = html_to_markdown(ARTICLE, "https://site.io/docs/")
        self.assertEqual(title, "Guide & Notes")
        self.assertTrue(md.startswith("# Guide & Notes"))
        self.assertIn("[a link](https://site.io/more)", md)
        self.assertIn("`code()`", md)
        self.assertIn("## Setup", md)
        self.assertNotIn("¶", md)
        self.assertIn("```python\ndef f():\n    return   1\n```", md)
        self.assertIn("1. Step **one**", md)
        self.assertIn("**Term**", md)
        for gone in ("Home", "cookies", "Footer", "hidden text", "diagram"):
            self.assertNotIn(gone, md)
        self.assertEqual(links, [("a link", "https://site.io/more")])

    def test_options(self):
        md, _, links = html_to_markdown(ARTICLE, "https://site.io/", include_links=False,
                                        include_images=True)
        self.assertIn("![diagram](https://site.io/i.png)", md)
        self.assertNotIn("](https://site.io/more)", md)
        self.assertEqual(links, [])
        whole, _, _ = html_to_markdown(ARTICLE, main_content=False)
        self.assertIn("Home", whole)

    def test_main_content_found_by_density(self):
        body = ("<div class='x'>" + "<a href='/a'>link</a> " * 30 + "</div>"
                "<div class='y'>" + "<p>" + "real prose sentence. " * 40 + "</p>" * 3 + "</div>")
        md, _, _ = html_to_markdown(f"<html><body>{body}</body></html>")
        self.assertIn("real prose", md)

    def test_tokens_and_chunks(self):
        self.assertEqual(count_tokens(""), 0)
        self.assertGreater(count_tokens("hello world " * 50), 50)
        with mock.patch.object(md_module, "_encoder", False):
            self.assertEqual(count_tokens("abcd" * 10), 10)
        text = "# A\n\n" + "\n\n".join(f"Para {i} " + "word " * 40 for i in range(6)) + \
               "\n\n## B\n\n" + "tail " * 30
        chunks = chunk_markdown(text, max_tokens=80, overlap=10)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(c.tokens <= 80 for c in chunks))
        self.assertEqual(chunks[0].heading, "A")
        self.assertEqual(chunks[-1].heading, "A > B")
        self.assertEqual([c.index for c in chunks], list(range(len(chunks))))
        long_block = "# H\n\n" + "sentence here. " * 400
        self.assertTrue(all(c.tokens <= 50 for c in chunk_markdown(long_block, 50, 0)))
        with self.assertRaises(ValueError):
            chunk_markdown("x", 0)

    def test_truncate_and_client_rendered_detection(self):
        md = "\n\n".join("para " * 50 for _ in range(10))
        self.assertLessEqual(count_tokens(md_module.truncate_tokens(md, 100)), 100)
        self.assertTrue(md_module.looks_client_rendered('<div id="root"></div>', ""))
        self.assertTrue(md_module.looks_client_rendered("<script></script>" * 3, "x"))
        self.assertFalse(md_module.looks_client_rendered("<p>small static page</p>", "small"))
        self.assertFalse(md_module.looks_client_rendered('<div id="root"></div>', "x" * 500))

    def test_fetch_markdown_over_http(self):
        page = page_from(ARTICLE, "https://site.io/docs/")
        transport = mock.Mock(name="t", fetch=mock.Mock(return_value=page))
        transport.name = "impersonate"
        with mock.patch("headless.transport.ImpersonateTransport", return_value=transport), \
                mock.patch("headless.transport.impersonate_available", return_value=(True, "")):
            doc = fetch_markdown("https://site.io/docs/", chunk_tokens=20, overlap=0,
                                 max_tokens=500)
        self.assertEqual(doc.title, "Guide & Notes")
        self.assertEqual(doc.transport, "impersonate")
        self.assertTrue(doc.chunks)
        self.assertEqual(doc.tokens, count_tokens(doc.markdown))
        self.assertEqual(str(doc), doc.markdown)
        self.assertEqual(doc.as_dict()["links"][0]["url"], "https://site.io/more")
        transport.close.assert_called_once()

    def test_fetch_markdown_renders_client_side_pages(self):
        shell = page_from('<html><body><div id="root"></div></body></html>')
        transport = mock.Mock(fetch=mock.Mock(return_value=shell))
        transport.name = "impersonate"
        with mock.patch("headless.transport.ImpersonateTransport", return_value=transport), \
                mock.patch("headless.transport.impersonate_available", return_value=(True, "")), \
                mock.patch.object(md_module, "_playwright_ready", return_value=True), \
                mock.patch.object(md_module, "_render",
                                  return_value=(ARTICLE, "https://x/", 200, "playwright")):
            doc = fetch_markdown("https://x/")
        self.assertEqual(doc.transport, "playwright")
        self.assertIn("Guide & Notes", doc.markdown)
        with self.assertRaises(ValueError):
            fetch_markdown("https://x/", render="sometimes")

    def test_fetch_markdown_without_any_transport(self):
        with mock.patch("headless.transport.impersonate_available", return_value=(False, "")), \
                mock.patch("headless.transport.http_available", return_value=(False, "")):
            with self.assertRaises(RuntimeError):
                fetch_markdown("https://x/", render="never")


class FakeScraperForTools:
    def __init__(self, response):
        self.response = response
        self.region = None
        self.calls = []

    def search(self, query, **kw):
        self.calls.append((query, kw, kw.get("region", self.region)))
        return self.response

    def quit(self):
        pass


class TestToolkit(HermeticTestCase):
    def _toolkit(self, response=None, **kw):
        response = response if response is not None else SearchResponse(
            "q", engine="brave", results=[{"url": "https://a.com", "title": "A",
                                           "snippet": "s", "favicon": "f"}],
            attempts=[EngineAttempt("brave", STATUS_OK)])
        return Toolkit(scraper=FakeScraperForTools(response), **kw)

    def test_schemas_in_every_format(self):
        tk = self._toolkit()
        openai = tk.openai_tools()
        anthropic = tk.anthropic_tools()
        self.assertEqual([t["function"]["name"] for t in openai],
                         ["web_search", "fetch_page", "extract_data", "screenshot"])
        self.assertEqual(anthropic[0]["input_schema"]["required"], ["query"])
        schema = openai[0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["mode"]["enum"], ["first", "aggregate"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(anthropic[2]["input_schema"]["properties"]["fields"]["type"], "object")
        self.assertEqual([t["name"] for t in Toolkit(tools=["fetch_page"]).anthropic_tools()],
                         ["fetch_page"])
        with self.assertRaises(ValueError):
            Toolkit(tools=["telepathy"])

    def test_web_search_is_compact_and_honest(self):
        tk = self._toolkit()
        out = json.loads(tk.call("web_search", '{"query": "q", "max_results": 99, "region": "uk-en"}'))
        self.assertEqual(out["results"], [{"title": "A", "url": "https://a.com", "snippet": "s"}])
        self.assertFalse(out["blocked"])
        query, kw, region = tk.scraper.calls[0]
        self.assertEqual(kw["max_results"], 30)
        self.assertEqual(region, "uk-en")
        self.assertIsNone(tk.scraper.region)        # never mutated: passed per call

        blocked = self._toolkit(SearchResponse("q", attempts=[EngineAttempt("brave", STATUS_BLOCKED)]))
        self.assertIn("not evidence", json.loads(blocked.call("web_search", {"query": "q"}))["note"])
        cooling = self._toolkit(SearchResponse("q", skipped=[
            {"engine": "b", "reason": SKIP_COOLING, "resume_in": 3}]))
        self.assertIn("cooling", json.loads(cooling.call("web_search", {"query": "q"}))["note"])
        empty = self._toolkit(SearchResponse("q", attempts=[EngineAttempt("brave", STATUS_EMPTY)]))
        self.assertIn("found nothing", json.loads(empty.call("web_search", {"query": "q"}))["note"])
        agg = self._toolkit(SearchResponse("q", engine="aggregate", results=[
            {"url": "u", "title": "t", "snippet": "", "votes": 2, "engines": ["a", "b"]}],
            attempts=[EngineAttempt("a", STATUS_OK, transport="cache")]))
        out = json.loads(agg.call("web_search", {"query": "q", "mode": "aggregate"}))
        self.assertEqual(out["results"][0]["votes"], 2)
        self.assertTrue(out["cached"])

    def test_errors_are_returned_not_raised(self):
        tk = self._toolkit()
        for name, args, needle in [
            ("nope", {}, "unknown tool"),
            ("web_search", {}, "missing required"),
            ("web_search", {"query": "q", "evil": 1}, "unexpected"),
            ("web_search", "{not json", "JSONDecodeError"),
            ("fetch_page", {"url": "file:///etc/passwd"}, "http:// or https://"),
            ("extract_data", {"url": "https://x", "fields": {}}, "non-empty"),
        ]:
            with self.subTest(name=name):
                self.assertIn(needle, json.loads(tk.call(name, args))["error"])

    def test_fetch_extract_and_screenshot_delegate(self):
        tk = self._toolkit()
        doc = mock.Mock(final_url="https://x/", title="T", status=200, tokens=3, markdown="# T")
        with mock.patch("headless.markdown.fetch_markdown", return_value=doc) as fm:
            out = json.loads(tk.call("fetch_page", {"url": "https://x/"}))
        self.assertEqual(out["markdown"], "# T")
        self.assertEqual(fm.call_args.kwargs["max_tokens"], 4000)
        browser = mock.Mock()
        browser.extract.return_value = [{"t": "a"}]
        tmp = tempfile.mkdtemp()

        def shoot(url, path, full_page=True):
            open(path, "wb").write(b"\x89PNG")
            return True
        browser.screenshot.side_effect = shoot
        tk._browser = browser
        tk.screenshot_dir = tmp
        self.assertEqual(json.loads(tk.call("extract_data", {"url": "https://x", "fields": {"t": "h1"}})),
                         [{"t": "a"}])
        shot = tk.screenshot("https://x", include_base64=True)
        self.assertEqual(shot["bytes"], 4)
        self.assertTrue(shot["png_base64"])
        browser.screenshot.side_effect = None
        browser.screenshot.return_value = False
        self.assertIn("could not capture", json.loads(tk.call("screenshot", {"url": "https://x"}))["error"])
        with tk:
            pass
        browser.close.assert_called_once()

    def test_tool_call_handlers(self):
        tk = self._toolkit()
        msgs = tk.handle_openai_tool_calls([
            {"id": "c1", "function": {"name": "web_search", "arguments": '{"query": "q"}'}},
            mock.Mock(id="c2", function=mock.Mock(arguments="{}", **{"name": "web_search"}))])
        self.assertEqual([m["tool_call_id"] for m in msgs], ["c1", "c2"])
        self.assertEqual(msgs[0]["role"], "tool")
        self.assertIn("error", json.loads(msgs[1]["content"]))
        blocks = tk.handle_anthropic_tool_use([
            {"type": "text", "text": "thinking"},
            {"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "q"}},
            {"type": "tool_use", "id": "t2", "name": "web_search", "input": {}}])
        self.assertEqual([b["tool_use_id"] for b in blocks], ["t1", "t2"])
        self.assertNotIn("is_error", blocks[0])
        self.assertTrue(blocks[1]["is_error"])

    def test_typed_functions_and_pydantic_schemas(self):
        tk = self._toolkit()
        fn = tk._functions()[0]
        self.assertEqual(fn.__name__, "web_search")
        self.assertIn("query", fn.__signature__.parameters)
        self.assertEqual(json.loads(fn(query="q"))["engine"], "brave")
        try:
            model = tools_module.pydantic_schema(tools_module.TOOL_SPECS[0])
        except ImportError:
            self.skipTest("pydantic not installed")
        self.assertEqual(model.model_json_schema()["required"], ["query"])

    def test_langchain(self):
        try:
            import langchain_core  # noqa: F401
        except ImportError:
            with self.assertRaises(RuntimeError):
                self._toolkit().langchain_tools()
            return
        (search, *_rest) = self._toolkit().langchain_tools()
        self.assertEqual(search.name, "web_search")
        self.assertEqual(json.loads(search.invoke({"query": "q"}))["engine"], "brave")

    def test_llamaindex(self):
        try:
            import llama_index.core  # noqa: F401
        except ImportError:
            with self.assertRaises(RuntimeError):
                self._toolkit().llamaindex_tools()
            return
        search = self._toolkit().llamaindex_tools()[0]
        self.assertEqual(search.metadata.name, "web_search")
        self.assertEqual(json.loads(search.call(query="q").content)["engine"], "brave")

    def test_crewai_with_a_stand_in(self):
        class BaseTool:
            def run(self, **kw):
                return self._run(**kw)
        fake = mock.Mock()
        fake.tools.BaseTool = BaseTool
        # Import pydantic (and build the schemas) *before* patching sys.modules:
        # patch.dict removes every module first imported inside it, and a
        # re-imported pydantic has a different BaseModel, which would break the
        # cached schemas for every later test.
        try:
            import pydantic  # noqa: F401
            for spec in tools_module.TOOL_SPECS:
                tools_module.pydantic_schema(spec)
        except ImportError:
            self.skipTest("pydantic not installed")
        with mock.patch.dict("sys.modules", {"crewai": fake, "crewai.tools": fake.tools}):
            try:
                tools = self._toolkit().crewai_tools()
            except ImportError:
                self.skipTest("pydantic not installed")
        self.assertEqual(type(tools[0]).__name__, "WebSearchTool")
        self.assertEqual(tools[0].name, "web_search")
        self.assertEqual(json.loads(tools[0].run(query="q"))["engine"], "brave")
        with mock.patch.dict("sys.modules", {"crewai": None, "crewai.tools": None}):
            with self.assertRaises(RuntimeError):
                self._toolkit().crewai_tools()

    def test_module_level_helpers(self):
        with mock.patch.object(tools_module, "_default", self._toolkit()):
            self.assertEqual(len(tools_module.openai_tools()), 4)
            self.assertEqual(len(tools_module.anthropic_tools()), 4)
            self.assertEqual(json.loads(tools_module.call_tool("web_search", {"query": "q"}))["engine"],
                             "brave")

    def test_default_scraper_is_built_lazily_with_a_cache(self):
        tk = Toolkit(region="uk-en", transport="http")
        self.assertIsNone(tk._scraper)
        scraper = tk.scraper
        self.assertEqual(scraper.region, "uk-en")
        self.assertIsInstance(scraper.cache, MemoryCache)
        tk.close()


def mcp_ready() -> bool:
    try:
        from headless.mcp_server import mcp_available
        return mcp_available()[0]
    except Exception:
        return False


class TestMcpServer(HermeticTestCase):
    def test_config_helper(self):
        from headless.mcp_server import claude_desktop_config, tool_names
        self.assertEqual(claude_desktop_config()["mcpServers"]["web"],
                         {"command": "headless-driver", "args": ["mcp"]})
        self.assertEqual(claude_desktop_config(uvx=True)["mcpServers"]["web"]["command"], "uvx")
        self.assertIn("search_aggregate", tool_names())

    def test_missing_sdk_is_a_clear_error(self):
        from headless import mcp_server
        with mock.patch.object(mcp_server, "_server_class", side_effect=ImportError("no mcp")):
            with self.assertRaises(RuntimeError):
                mcp_server.build_server()
            self.assertFalse(mcp_server.mcp_available()[0])

    @unittest.skipUnless(mcp_ready(), "needs the mcp SDK")
    def test_tools_over_a_real_mcp_session(self):
        from headless.mcp_server import build_server
        try:
            from mcp.client import Client
        except ImportError:
            self.skipTest("this mcp SDK has no in-memory client")
        response = SearchResponse("q", engine="brave", results=[
            {"url": "https://a.com", "title": "A", "snippet": "s"}],
            attempts=[EngineAttempt("brave", STATUS_OK)])
        server = build_server(Toolkit(scraper=FakeScraperForTools(response)))

        async def session():
            async with Client(server) as client:
                names = sorted(t.name for t in (await client.list_tools()).tools)
                found = await client.call_tool("search", {"query": "q"})
                agg = await client.call_tool("search_aggregate", {"query": "q"})
                bad = await client.call_tool("fetch_page", {"url": "ftp://x"})
                return names, found, agg, bad

        import logging
        quiet = logging.getLogger("mcp")
        level = quiet.level
        quiet.setLevel(logging.CRITICAL)      # the SDK logs the deliberate error
        try:
            names, found, agg, bad = asyncio.run(session())
        finally:
            quiet.setLevel(level)
        self.assertEqual(names, ["extract", "fetch_page", "screenshot", "search",
                                 "search_aggregate"])
        self.assertEqual(json.loads(found.content[0].text)["results"][0]["url"], "https://a.com")
        self.assertEqual(server.toolkit._scraper.calls[1][1]["mode"], "aggregate")
        self.assertTrue(getattr(bad, "is_error", getattr(bad, "isError", False)))

    @unittest.skipUnless(mcp_ready(), "needs the mcp SDK")
    def test_building_a_server_leaves_root_logging_alone(self):
        import logging
        from headless.mcp_server import build_server
        root = logging.getLogger()
        before = (list(root.handlers), root.level)
        build_server(Toolkit(scraper=FakeScraperForTools(SearchResponse("q"))))
        self.assertEqual((list(root.handlers), root.level), before)

    def test_serve_runs_and_closes(self):
        from headless import mcp_server
        server = mock.Mock()
        server.run.side_effect = [TypeError("old sdk"), None]
        with mock.patch.object(mcp_server, "build_server", return_value=server):
            mcp_server.serve("sse", host="0.0.0.0", port=9000)
        self.assertEqual(server.settings.port, 9000)
        server.toolkit.close.assert_called_once()
        server2 = mock.Mock()
        with mock.patch.object(mcp_server, "build_server", return_value=server2):
            mcp_server.serve("stdio")
        server2.run.assert_called_once_with("stdio")


class TestCli12(HermeticTestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_fetch(self):
        doc = md_module.MarkdownDocument(url="https://x/", markdown="# T\n\nbody", title="T",
                                         tokens=4, transport="impersonate",
                                         chunks=[md_module.Chunk(0, "# T", 2, "T")])
        with mock.patch("headless.markdown.fetch_markdown", return_value=doc) as fm:
            code, out, err = self._run(["fetch", "https://x/", "--chunk", "100"])
            self.assertEqual(code, cli.EXIT_OK)
            self.assertEqual(out, "# T\n\nbody\n")
            self.assertIn("4 tokens via impersonate, 1 chunks", err)
            self.assertEqual(fm.call_args.kwargs["chunk_tokens"], 100)
            code, out, _ = self._run(["fetch", "https://x/", "--json"])
            self.assertEqual(json.loads(out)["chunks"][0]["heading"], "T")
            path = os.path.join(tempfile.mkdtemp(), "o.md")
            code, out, _ = self._run(["--no-color", "fetch", "https://x/", "-o", path])
            self.assertEqual(open(path).read(), "# T\n\nbody\n")
            fm.side_effect = RuntimeError("offline")
            code, out, _ = self._run(["--no-color", "fetch", "https://x/"])
            self.assertEqual(code, cli.EXIT_FAILED)

    def test_mcp_command(self):
        code, out, _ = self._run(["mcp", "--print-config", "--uvx"])
        self.assertEqual(json.loads(out)["mcpServers"]["web"]["command"], "uvx")
        with mock.patch("headless.mcp_server.serve") as serve:
            code, _, _ = self._run(["mcp", "--transport", "streamable-http", "--port", "9", "--region",
                                    "uk-en", "--proxy", "http://p"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(serve.call_args.args, ("streamable-http",))
        self.assertEqual(serve.call_args.kwargs["port"], 9)
        self.assertEqual(serve.call_args.kwargs["region"], "uk-en")
        with mock.patch("headless.mcp_server.serve", side_effect=RuntimeError("no mcp")):
            code, out, _ = self._run(["--no-color", "mcp"])
        self.assertEqual(code, cli.EXIT_FAILED)

    def test_search_passes_pages_and_cache(self):
        args = cli.build_parser().parse_args(["search", "q", "--pages", "3", "--cache", "memory"])
        self.assertEqual((args.pages, args.cache), (3, "memory"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestReviewRegressions(HermeticTestCase):
    """One test per bug found in the 1.2 review, each reproduced first."""

    def test_aggregate_refuses_to_share_a_browser(self):
        scr = AdvancedSearchScraper()
        with mock.patch.object(scr, "browserless_transport_name", return_value=None):
            with self.assertRaises(ValueError):
                scr.search("q", mode="aggregate")

    def test_search_batch_really_runs_in_parallel(self):
        import time as _time
        import threading

        active, peak, lock = [0], [0], threading.Lock()

        def slow(req):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            _time.sleep(0.2)
            with lock:
                active[0] -= 1
            return page_from(brave_page("https://a.com"))

        scr = paged_scraper({"brave": {}}, search_engine="brave", fallback=False)
        scr._fake.fetch = lambda url, key="", **kw: slow(kw)
        with mock.patch.object(scr, "browserless_transport_name", return_value="http"):
            scr.search_batch(["a", "b", "c", "d"], max_workers=4)
        self.assertGreater(peak[0], 1)

    def test_probe_attempt_is_json_serialisable(self):
        scr = paged_scraper({"brave": {1: brave_page("https://en.wikipedia.org/wiki/Python")}},
                            search_engine="brave")
        attempt = scr.probe("brave")
        self.assertEqual(attempt.page, 1)
        json.dumps(attempt.as_dict())

    def test_redirects_are_decoded_once(self):
        unwrap = AdvancedSearchScraper._unwrap_redirect
        target = "https://x.com/s?q=a%26b&x=1"
        from urllib.parse import quote
        self.assertEqual(unwrap("https://duckduckgo.com/l/?uddg=" + quote(target, safe="")), target)
        self.assertEqual(unwrap("https://www.google.com/url?q=" + quote(target, safe="") + "&sa=U"),
                         target)

    def test_later_pages_respect_a_cooling_engine(self):
        health = EngineHealth()
        scr = paged_scraper({"brave": {1: brave_page("https://a.com"), 2: brave_page("https://b.com")}},
                            search_engine="brave", fallback=False, cache="memory", health=health)
        scr.search("q")                                  # page 1 cached
        health.trip("brave", 300)
        response = scr.search("q", pages=2)
        self.assertEqual([r["url"] for r in response], ["https://a.com"])
        self.assertEqual(len(scr._fake.requests), 1)     # page 2 never requested

    def test_worldwide_region_has_no_language(self):
        from headless.scraper import split_region, _google_params
        self.assertEqual(split_region("wt-wt"), (None, None))
        self.assertEqual(split_region("xa-ar"), (None, "ar"))
        self.assertNotIn("hl", _google_params("q", "wt-wt"))

    def test_browserless_transport_never_starts_a_browser(self):
        scr = AdvancedSearchScraper(transport="http", search_engine="google", fallback=False)
        with mock.patch.object(scr, "_get_driver", side_effect=AssertionError("browser!")):
            response = scr.search("q")
        self.assertEqual(response.attempts, [])
        self.assertEqual(response.skipped[0]["reason"], "needs_browser")

    def test_extraction_errors_fall_through_to_the_next_engine(self):
        def processor(query, item):
            if item["engine"] == "brave":
                raise KeyError("boom")
            return item
        mojeek = "<ul class='results-standard'><li><h2><a href='https://m.com'>M</a></h2></li></ul>"
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}, "mojeek": {1: mojeek}},
                            search_engine="brave", fallback_engines=["mojeek"],
                            result_processor=processor)
        with self.assertLogs("headless", level="WARNING"):
            response = scr.search("q")
        self.assertEqual(response.engine, "mojeek")
        self.assertEqual(response.attempts[0].status, "error")

    def test_consensus_cut_off_is_a_skip_not_a_timeout(self):
        import time as _time
        fast = lambda *u: page_from(brave_page(*u))
        mojeek = page_from("<ul class='results-standard'>" + "".join(
            f"<li><h2><a href='{u}'>M</a></h2></li>" for u in ("https://a.com", "https://b.com",
                                                             "https://c.com")) + "</ul>")

        class T(PagedTransport):
            def fetch(self, url, key="", **kw):
                if key == "yahoo":
                    _time.sleep(1.0)
                return {"brave": fast("https://a.com", "https://b.com", "https://c.com"),
                        "mojeek": mojeek}.get(key) or page_from("<p/>")
        scr = AdvancedSearchScraper(transport="http", circuit_breaker=False)
        transport = T({})
        scr._transport_for = lambda e: (transport, False)
        response = scr.search("q", mode="aggregate", engines=["brave", "mojeek", "yahoo"],
                              deadline=5, min_engines=2)
        self.assertNotIn("yahoo", [a.engine for a in response.refused])
        self.assertIn({"engine": "yahoo", "resume_in": 0, "reason": "consensus_reached"},
                      response.skipped)

    def test_tidy_url_is_lossless(self):
        from headless.scraper import tidy_url
        for url in ("https://gitlab.com/group%2Fproject", "https://x.com/caf%E9",
                    "https://x.com/a%3Bb", "https://x.com/a%3Fb", "https://x.com/a%20b"):
            with self.subTest(url=url):
                self.assertEqual(tidy_url(url), url)
        self.assertEqual(tidy_url("https://x.com/in/Jos%C3%A9"), "https://x.com/in/José")

    def test_sqlite_expiry_does_not_delete_a_fresh_entry(self):
        clock = [100.0]
        path = os.path.join(tempfile.mkdtemp(), "c.db")
        cache = SQLiteCache(path, clock=lambda: clock[0])
        cache.set("k", {"v": "old"}, ttl=1)
        clock[0] = 200.0
        real_execute = cache._db.execute

        class Racing:
            """Another process refreshes the key between our SELECT and DELETE."""
            def __getattr__(self, name):
                return getattr(cache._db_real, name)

            def __enter__(self):
                return cache._db_real.__enter__()

            def __exit__(self, *exc):
                return cache._db_real.__exit__(*exc)

            def execute(self, sql, params=()):
                if sql.startswith("DELETE"):
                    real_execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                                 ("k", 10_000.0, json.dumps({"v": "fresh"})))
                return real_execute(sql, params)
        cache._db_real = cache._db
        cache._db = Racing()
        self.assertIsNone(cache.get("k"))
        cache._db = cache._db_real
        self.assertEqual(cache.get("k"), {"v": "fresh"})
        cache.close()

    def test_custom_cache_values_are_not_aliased(self):
        class Plain:
            def __init__(self):
                self.data = {}

            def get(self, key):
                return self.data.get(key)

            def set(self, key, value, ttl):
                self.data[key] = value
        scr = paged_scraper({"brave": {1: brave_page("https://a.com")}}, search_engine="brave",
                            fallback=False, cache=Plain())
        scr.search("q")
        first = scr.search("q")
        first[0]["title"] = "EDITED BY CALLER"
        self.assertNotEqual(scr.search("q")[0]["title"], "EDITED BY CALLER")


FILLER = "<p>" + "Lorem ipsum dolor sit amet consectetur. " * 10 + "</p>"


def md(body, **kw):
    return html_to_markdown(f"<html><body>{body}</body></html>", **kw)[0]


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestMarkdownRegressions(HermeticTestCase):
    """Each case below lost or corrupted content before the 1.2 review."""

    def test_spaces_around_inline_markup_survive(self):
        out = md(FILLER + "<p>This is<b> really </b>important and<a href='https://x/'> this link </a>too.</p>")
        self.assertIn("This is **really** important and [this link](https://x/) too.", out)

    def test_entities_and_inline_code_are_not_rewritten(self):
        out = md(FILLER + "<p>Write &amp;lt;div&amp;gt;. Code: <code>&amp;amp;</code> "
                          "and <code>a  =   b</code>.</p>")
        self.assertIn("Write &lt;div&gt;.", out)
        self.assertIn("`&amp;`", out)
        self.assertIn("`a  =   b`", out)

    def test_hard_breaks_and_quoted_code_keep_their_shape(self):
        out = md(FILLER + "<p>line one<br>line two</p><blockquote><pre>def f():\n    return  1</pre></blockquote>")
        self.assertIn("line one  \nline two", out)
        self.assertIn(">     return  1", out)

    def test_whole_article_survives_wrapper_classes_and_forms(self):
        text = "Real article text. " * 30
        self.assertIn("Real article text.", md(f"<main><div class='container has-sidebar'><p>{text}</p></div></main>"))
        self.assertIn("Real article text.", md(f"<form id='aspnetForm'><div><p>{text}</p></div></form>"))

    def test_article_header_keeps_its_title(self):
        out = html_to_markdown(f"<html><head><title>Site | Real</title></head><body><article>"
                               f"<header><h1>Real Title</h1><p>By Jane</p></header>{FILLER}"
                               f"</article></body></html>")[0]
        self.assertTrue(out.startswith("# Real Title"))
        self.assertIn("By Jane", out)

    def test_lists_keep_code_and_nesting(self):
        out = md(FILLER + "<ul><li>Step:<pre>x = 1\n\ny = 2</pre></li>"
                          "<li><div>Parent<ul><li>Child1</li><li>Child2</li></ul></div></li></ul>"
                          "<ol start='5'><li>five</li><li>six</li></ol>")
        self.assertIn("  ```\n  x = 1\n\n  y = 2\n  ```", out)
        self.assertIn("- Parent", out)
        self.assertIn("  - Child1\n  - Child2", out)
        self.assertIn("5. five\n6. six", out)

    def test_nested_tables_and_base_href(self):
        out = md(FILLER + "<table><tr><td>A<table><tr><td>inner</td></tr></table></td><td>B</td></tr></table>")
        self.assertEqual(out.count("| inner |"), 0)
        self.assertEqual(out.count("inner"), 1)
        page = ("<html><head><base href='https://cdn.ex.com/docs/'></head><body>" + FILLER +
                "<p><a href='page.html'>link</a></p></body></html>")
        self.assertIn("(https://cdn.ex.com/docs/page.html)", html_to_markdown(page, "https://ex.com/a/")[0])

    def test_the_post_beats_a_comment_article(self):
        post = "The actual post body sentence. " * 30
        out = md(f"<div class='entry-content'><h1>Post</h1><p>{post}</p></div>"
                 f"<article class='comment-body'><p>{'A comment. ' * 30}</p></article>")
        self.assertIn("The actual post body", out)

    def test_very_deep_markup_does_not_crash(self):
        deep = "<div>" * 1200 + "deep text" + "</div>" * 1200
        self.assertIn("deep text", md(deep))

    def test_chunks_never_exceed_the_limit(self):
        many = "\n\n".join(f"Short block {i}." for i in range(200))
        self.assertLessEqual(max(c.tokens for c in chunk_markdown(many, 50, 10)), 50)
        cjk = "这是一个很长的中文句子没有空格也没有英文标点" * 150
        self.assertLessEqual(max(c.tokens for c in chunk_markdown(cjk, 100, 0)), 100)
        numbers = " ".join(str(10 ** 12 + i) for i in range(400))
        self.assertLessEqual(max(c.tokens for c in chunk_markdown(numbers, 100, 0)), 100)

    def test_oversized_code_keeps_lines_and_fences(self):
        code = "```python\n" + "\n".join(f"def f{i}(x):  return x + {i}" for i in range(200)) + "\n```"
        chunks = chunk_markdown("# Guide\n\n" + code, 80, 0)
        self.assertTrue(all(c.text.count("```") == 2 for c in chunks))
        self.assertTrue(all("\n" in c.text for c in chunks))
        self.assertTrue(chunks[0].text.startswith("# Guide"))   # heading travels with its code

    def test_hash_comments_in_code_are_not_headings(self):
        doc = "# Guide\n\n```bash\necho hi\n\n# not a heading\necho bye\n```\n\nAfter."
        self.assertEqual({c.heading for c in chunk_markdown(doc, 500, 0)}, {"Guide"})

    def test_truncation_is_bounded_and_never_opens_a_fence(self):
        self.assertEqual(md_module.truncate_tokens("Hello world.\n\nMore.", -1), "")
        cut = md_module.truncate_tokens("Intro.\n\n```\nline1\n\nline2\n```", 5)
        self.assertEqual(cut.count("```") % 2, 0)
        cjk = "这是中文" * 400
        self.assertLessEqual(count_tokens(md_module.truncate_tokens(cjk, 50)), 50)
        with self.assertRaises(ValueError):
            fetch_markdown("https://x/", max_tokens=-5)
        with self.assertRaises(ValueError):
            fetch_markdown("https://x/", chunk_tokens=0)


class TestToolAndTransportRegressions(HermeticTestCase):
    def test_a_field_named_error_is_not_a_failure(self):
        tk = Toolkit(scraper=FakeScraperForTools(SearchResponse("q")))
        browser = mock.Mock()
        browser.extract.return_value = {"error": "text on the page"}
        tk._browser = browser
        result = tk.call("extract_data", {"url": "https://x", "fields": {"error": ".err"}})
        self.assertFalse(tools_module.is_error(result))
        blocks = tk.handle_anthropic_tool_use([{"type": "tool_use", "id": "t", "name": "extract_data",
                                                "input": {"url": "https://x", "fields": {"e": "p"}}}])
        self.assertNotIn("is_error", blocks[0])
        self.assertTrue(tools_module.is_error(tk.call("nope")))
        self.assertFalse(tools_module.is_error("not json"))

    def test_screenshots_never_overwrite_each_other(self):
        tk = Toolkit(scraper=FakeScraperForTools(SearchResponse("q")))
        browser = mock.Mock()
        browser.screenshot.side_effect = lambda url, path, full_page=True: open(path, "wb").write(b"x") or True
        tk._browser = browser
        a = tk.screenshot("https://x")["path"]
        b = tk.screenshot("https://x")["path"]
        self.assertNotEqual(a, b)
        self.assertEqual(os.path.dirname(a), os.path.dirname(b))   # one temp folder

    def test_concurrent_regions_do_not_leak(self):
        import threading
        seen = []

        class Recording(FakeScraperForTools):
            def search(self, query, **kw):
                seen.append((query, kw.get("region")))
                return SearchResponse(query)
        tk = Toolkit(scraper=Recording(None))
        threads = [threading.Thread(target=tk.web_search, args=(f"q{i}",),
                                    kwargs={"region": "de-de" if i % 2 else None})
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for query, region in seen:
            self.assertEqual(region, "de-de" if int(query[1:]) % 2 else None)

    def test_scraper_region_is_per_call(self):
        scr = AdvancedSearchScraper(transport="http", region="uk-en")
        with scr._region_scope("de-de"):
            self.assertEqual(scr._build_request("q", "mojeek")["cookies"], {"arc": "de", "lb": "de"})
        self.assertEqual(scr.effective_region(), "uk-en")

    def test_selector_and_proxy_parsing(self):
        from headless.playwright_driver import _split_selector, proxy_settings
        self.assertEqual(_split_selector("a[href*='@']"), ["a[href*='@']", ""])
        self.assertEqual(_split_selector('a[title="x@y"]@href'), ['a[title="x@y"]', "href"])
        self.assertEqual(proxy_settings("http://[::1]:8080")["server"], "http://[::1]:8080")

    def test_non_finite_retry_after(self):
        from headless.transport import parse_retry_after
        for value in ("inf", "-inf", "nan"):
            self.assertIsNone(parse_retry_after(value))
        health = EngineHealth()
        health.record(EngineAttempt("e", "rate_limited", retry_after=10 ** 9))
        self.assertLessEqual(health.resume_in("e"), 24 * 3600)
        json.dumps(health.snapshot())

    @unittest.skipUnless(HAS_BS4, "needs requests and bs4")
    def test_http_transport_reads_utf8_without_a_charset_header(self):
        from headless.transport import HttpTransport
        body = "<html><head><meta charset='utf-8'><title>Café – naïve</title></head></html>".encode()
        response = mock.Mock(url="https://x/", status_code=200, content=body,
                             text=body.decode("latin-1"), headers={"Content-Type": "text/html"})
        transport = HttpTransport()
        transport.session.request = mock.Mock(return_value=response)
        self.assertEqual(transport.fetch("https://x/").title(), "Café – naïve")
        transport.close()

    def test_rotated_sessions_are_forgotten(self):
        from headless.transport import impersonate_available, ImpersonateTransport
        if not impersonate_available()[0]:
            self.skipTest("needs curl_cffi")
        t = ImpersonateTransport(profiles=["chrome", "safari"])
        for _ in range(30):
            t.rotate("brave")
        self.assertLessEqual(len(t._all), 1)
        t.close()


class TestCliRegressions(HermeticTestCase):
    def _run(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, env or {}):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_fetch_errors_stay_off_stdout(self):
        with mock.patch("headless.markdown.fetch_markdown", side_effect=ConnectionError("down")):
            code, out, err = self._run(["fetch", "https://x/", "--json"])
        self.assertEqual((code, out), (cli.EXIT_FAILED, ""))
        self.assertIn("down", err)

    def test_json_search_still_saves(self):
        path = os.path.join(tempfile.mkdtemp(), "r.json")

        class Fake:
            def __init__(self, **kw):
                pass

            def search(self, q):
                return SearchResponse(q, results=[{"url": "u"}], engine="brave",
                                      attempts=[EngineAttempt("brave", STATUS_OK)])

            def export(self, p):
                open(p, "w").write("[]")
                return True

            def quit(self):
                pass
        with mock.patch("headless.cli.AdvancedSearchScraper", Fake):
            code, out, _ = self._run(["search", "q", "--json", "--save", path])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(os.path.exists(path))
        json.loads(out)

    def test_bad_option_combinations_are_usage_errors(self):
        code, out, err = self._run(["search", "q", "--mode", "aggregate", "--transport", "browser"])
        self.assertEqual(code, 2)
        self.assertIn("aggregate", err)
        code, _, err = self._run(["search", "q", "--cache", "bogus://x"])
        self.assertEqual(code, 2)

    def test_env_log_level_is_respected(self):
        import logging
        from headless import logs
        with mock.patch("headless.cli.cmd_engines", return_value=0):
            parser = cli.build_parser()
            parser._subparsers._group_actions[0].choices["engines"].set_defaults(func=cli.cmd_engines)
            with mock.patch.object(cli, "build_parser", return_value=parser):
                self._run(["engines"], env={"HEADLESS_DRIVER_LOG": "debug"})
        self.assertEqual(logs._console_handler.level, logging.DEBUG)

    def test_bench_without_any_transport_fails(self):
        with mock.patch("headless.bench.run_bench") as run:
            code, out, _ = self._run(["bench", "--transports", "nope", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("no requested transport", json.loads(out)["error"])
        run.assert_not_called()


@unittest.skipUnless(mcp_ready(), "needs the mcp SDK")
class TestMcpErrorText(HermeticTestCase):
    def test_clients_see_the_error_message(self):
        import logging
        from headless.mcp_server import build_server
        try:
            from mcp.client import Client
        except ImportError:
            self.skipTest("no in-memory client in this SDK")
        server = build_server(Toolkit(scraper=FakeScraperForTools(SearchResponse("q"))))

        async def call():
            async with Client(server) as client:
                return await client.call_tool("fetch_page", {"url": "ftp://x"})
        quiet = logging.getLogger("mcp")
        level = quiet.level
        quiet.setLevel(logging.CRITICAL)
        try:
            result = asyncio.run(call())
        finally:
            quiet.setLevel(level)
        self.assertIn("url must start with http", result.content[0].text)
