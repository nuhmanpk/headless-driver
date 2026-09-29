"""Tests for the 1.0 contract: structured responses, transports, logging."""

import io
import os
import json
import logging
import pathlib
import tempfile
import unittest
from unittest import mock

from headless import (
    AdvancedSearchScraper, ScraperPool, SearchResponse, EngineAttempt,
    AllEnginesBlocked, ENGINE_SPECS, default_user_agent, Headless,
    enable_console_logging, disable_console_logging, __version__,
)
from headless.results import (
    STATUS_OK, STATUS_EMPTY, STATUS_BLOCKED, STATUS_TIMEOUT,
    STATUS_UNREACHABLE, STATUS_ERROR,
)
from headless.transport import HtmlNode, Page, http_available
try:
    from .support import HermeticTestCase
except ImportError:  # run as top-level modules by `discover -s tests`
    from support import HermeticTestCase

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture_page(name: str) -> Page:
    """Parse a recorded results page, exactly as the HTTP transport would."""
    from bs4 import BeautifulSoup
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    url = ENGINE_SPECS[name]["url"].replace("{query}", "wikipedia")
    return Page(HtmlNode(BeautifulSoup(html, "html.parser"), url), url)


class TestSearchResponse(HermeticTestCase):
    """The response must stay a drop-in for the list it replaced."""

    def _response(self, n=2, **kw):
        results = [{"url": f"https://e{i}.com", "title": f"T{i}", "snippet": "s"}
                   for i in range(n)]
        return SearchResponse(query="q", results=results, engine="bing", **kw)

    def test_behaves_like_a_list(self):
        r = self._response(3)
        self.assertEqual(len(r), 3)
        self.assertEqual(len([x for x in r]), 3)
        self.assertEqual(r[0]["title"], "T0")
        self.assertTrue(r)
        self.assertIn(r[1], r)

    def test_empty_response_is_falsey_and_equals_empty_list(self):
        r = SearchResponse(query="q")
        self.assertFalse(r)
        self.assertEqual(len(r), 0)
        self.assertEqual(r, [])

    def test_equals_a_plain_list_of_results(self):
        r = self._response(1)
        self.assertEqual(r, r.results)

    def test_blocked_only_when_every_attempt_was_refused(self):
        blocked = SearchResponse(query="q", attempts=[
            EngineAttempt("a", STATUS_BLOCKED), EngineAttempt("b", STATUS_TIMEOUT)])
        self.assertTrue(blocked.blocked)

        mixed = SearchResponse(query="q", attempts=[
            EngineAttempt("a", STATUS_BLOCKED), EngineAttempt("b", STATUS_EMPTY)])
        # One engine genuinely had nothing, so this is not a blanket refusal.
        self.assertFalse(mixed.blocked)

        self.assertFalse(SearchResponse(query="q").blocked)

    def test_every_refusal_status_counts_as_blocked(self):
        for status in (STATUS_BLOCKED, STATUS_TIMEOUT, STATUS_UNREACHABLE, STATUS_ERROR):
            with self.subTest(status=status):
                self.assertTrue(EngineAttempt("e", status).blocked)
        for status in (STATUS_OK, STATUS_EMPTY):
            with self.subTest(status=status):
                self.assertFalse(EngineAttempt("e", status).blocked)

    def test_refused_and_engines_tried(self):
        r = SearchResponse(query="q", attempts=[
            EngineAttempt("a", STATUS_BLOCKED, reason="captcha"),
            EngineAttempt("b", STATUS_EMPTY)])
        self.assertEqual([a.engine for a in r.refused], ["a"])
        self.assertEqual(r.engines_tried, ["a", "b"])

    def test_as_dict_is_json_serialisable(self):
        r = self._response(1, attempts=[EngineAttempt("a", STATUS_OK, count=1)])
        payload = json.loads(json.dumps(r.as_dict()))
        self.assertEqual(payload["engine"], "bing")
        self.assertEqual(payload["attempts"][0]["status"], STATUS_OK)
        self.assertFalse(payload["blocked"])


class TestListCompatibility(HermeticTestCase):
    """A response must be usable everywhere the old list was.

    These are the patterns a real pipeline uses; each one broke when the
    response was a dataclass rather than a list.
    """

    def setUp(self):
        self.r = SearchResponse(
            query="q", engine="bing",
            results=[{"url": "https://a.com", "title": "A"},
                     {"url": "https://b.com", "title": "B"}],
            attempts=[EngineAttempt("bing", STATUS_OK, 2)])

    def test_is_a_list(self):
        self.assertIsInstance(self.r, list)

    def test_json_serialisable(self):
        # A scraper that cannot json.dumps its results is useless in a pipeline.
        payload = json.loads(json.dumps(self.r))
        self.assertEqual(payload[0]["url"], "https://a.com")

    def test_concatenation_both_ways(self):
        self.assertEqual(len(self.r + [{"url": "c"}]), 3)
        self.assertEqual(len([{"url": "c"}] + self.r), 3)

    def test_mutation(self):
        self.r.append({"url": "https://c.com"})
        self.r.extend([{"url": "https://d.com"}])
        self.assertEqual(len(self.r), 4)

    def test_list_methods(self):
        self.assertEqual(self.r.count(self.r[0]), 1)
        self.assertEqual(self.r.index(self.r[1]), 1)
        self.assertEqual(len(list(reversed(self.r))), 2)

    def test_slicing_sorting_and_membership(self):
        self.assertEqual(len(self.r[:1]), 1)
        self.assertEqual(sorted(self.r, key=lambda h: h["url"])[0]["url"], "https://a.com")
        self.assertIn(self.r[0], self.r)

    def test_copy_and_pickle(self):
        import copy, pickle
        self.assertEqual(len(copy.deepcopy(self.r)), 2)
        self.assertEqual(len(pickle.loads(pickle.dumps(self.r))), 2)

    def test_equality_with_a_plain_list(self):
        self.assertEqual(self.r, [{"url": "https://a.com", "title": "A"},
                                  {"url": "https://b.com", "title": "B"}])
        self.assertEqual(SearchResponse(query="q"), [])

    def test_results_attribute_reads_and_writes(self):
        self.assertEqual(self.r.results, list(self.r))
        self.r.results = [{"url": "https://z.com"}]
        self.assertEqual(len(self.r), 1)
        self.assertEqual(self.r[0]["url"], "https://z.com")

    def test_metadata_survives_list_behaviour(self):
        self.r.append({"url": "https://c.com"})
        self.assertEqual(self.r.engine, "bing")
        self.assertEqual(self.r.query, "q")
        self.assertFalse(self.r.blocked)


class TestProxyIsNeverBypassed(HermeticTestCase):
    """Traffic must never leave from the host when a proxy was configured."""

    LEGACY = {"additional_args": ["--proxy-server=http://user:pass@proxy:8080"]}

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_http_transport_honours_a_proxy_given_as_a_chrome_switch(self):
        # The pre-1.0 way to set a proxy. The HTTP transport cannot see Chrome
        # switches, so without explicit handling this silently leaked the host IP.
        scr = AdvancedSearchScraper(headless_options=dict(self.LEGACY))
        session = scr._http_transport().session
        self.assertEqual(session.proxies.get("https"), "http://user:pass@proxy:8080")

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_http_transport_honours_the_proxy_argument(self):
        scr = AdvancedSearchScraper(proxy="socks5://127.0.0.1:9050")
        self.assertEqual(scr._http_transport().session.proxies.get("http"),
                         "socks5://127.0.0.1:9050")

    def test_explicit_proxy_wins_over_the_legacy_switch(self):
        scr = AdvancedSearchScraper(proxy="http://explicit:1",
                                    headless_options=dict(self.LEGACY))
        self.assertEqual(scr._effective_proxy(), "http://explicit:1")

    def test_no_proxy_configured_means_none(self):
        self.assertIsNone(AdvancedSearchScraper()._effective_proxy())

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_user_agent_from_headless_options_reaches_the_http_session(self):
        scr = AdvancedSearchScraper(headless_options={"user_agent": "Custom/1.0"})
        self.assertEqual(scr._http_transport().session.headers["User-Agent"],
                         "Custom/1.0")


class TestAdFiltering(HermeticTestCase):
    """Sponsored slots sit among organic results and must not be scraped."""

    def test_known_ad_endpoints_are_recognised(self):
        for url in ("https://duckduckgo.com/y.js?ad_domain=coursera.org&ad_provider=bing",
                    "https://www.bing.com/aclick?ld=abc",
                    "https://www.bing.com/aclk?foo=1",
                    "https://www.google.com/aclk?sa=l",
                    "https://googleadservices.com/pagead/x",
                    "https://ad.doubleclick.net/x"):
            with self.subTest(url=url):
                self.assertTrue(AdvancedSearchScraper._is_ad_url(url))

    def test_organic_results_are_not_mistaken_for_ads(self):
        for url in ("https://www.linkedin.com/in/peterdemin",
                    "https://en.wikipedia.org/wiki/Advertising",
                    "https://duckduckgo.com/about",
                    "https://example.com/products/adapter"):
            with self.subTest(url=url):
                self.assertFalse(AdvancedSearchScraper._is_ad_url(url))

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_an_ad_link_is_skipped_during_extraction(self):
        from bs4 import BeautifulSoup
        from headless.transport import HtmlNode
        html = ("<div class='result'>"
                "<a class='result__a' href='https://duckduckgo.com/y.js?ad_domain=x'>Ad</a>"
                "<a class='result__a' href='https://real.example.com/page'>Real</a>"
                "</div>")
        node = HtmlNode(BeautifulSoup(html, "html.parser"), "https://html.duckduckgo.com/")
        scr = AdvancedSearchScraper(transport="http")
        item = scr._extract_result(node.select("div.result")[0], "duckduckgo")
        self.assertEqual(item["url"], "https://real.example.com/page")


class TestSiteOperator(HermeticTestCase):
    """Engines disagree about `site:`; results that ignore it are not results."""

    def test_constraints_are_parsed(self):
        self.assertEqual(
            AdvancedSearchScraper._site_constraints('site:linkedin.com/in "a b" x'),
            ["linkedin.com/in"])
        self.assertEqual(AdvancedSearchScraper._site_constraints("no operator"), [])

    def test_matching_accepts_subdomains_and_paths(self):
        m = AdvancedSearchScraper._matches_site
        self.assertTrue(m("https://www.linkedin.com/in/x", ["linkedin.com/in"]))
        self.assertTrue(m("https://in.linkedin.com/in/x", ["linkedin.com/in"]))
        self.assertTrue(m("https://linkedin.com/company/x", ["linkedin.com"]))

    def test_matching_rejects_the_wrong_path_or_host(self):
        m = AdvancedSearchScraper._matches_site
        # Bing honours site:domain but ignores the path, returning these.
        self.assertFalse(m("https://www.linkedin.com/company/x", ["linkedin.com/in"]))
        self.assertFalse(m("https://en.wikipedia.org/wiki/Software", ["linkedin.com/in"]))
        self.assertFalse(m("https://notlinkedin.com/in/x", ["linkedin.com/in"]))

    def test_several_constraints_are_an_or(self):
        m = AdvancedSearchScraper._matches_site
        self.assertTrue(m("https://github.com/x", ["github.com", "linkedin.com"]))

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_off_target_results_are_dropped_and_the_engine_reads_as_empty(self):
        from bs4 import BeautifulSoup
        from headless.transport import HtmlNode, Page
        html = "".join(
            f"<div class='result'><a class='result__a' href='{u}'>T{i}</a></div>"
            for i, u in enumerate(["https://en.wikipedia.org/wiki/Software",
                                   "https://www.microsoft.com/download"]))
        page = Page(HtmlNode(BeautifulSoup(html, "html.parser"),
                             "https://html.duckduckgo.com/"), "https://html.duckduckgo.com/")
        scr = AdvancedSearchScraper(transport="http")
        scr._transport_for = lambda e: (mock.Mock(fetch=lambda *a, **k: page, name="http"), False)
        response = scr.search("site:linkedin.com/in engineer", engine="duckduckgo",
                              fallback=False)
        # Plausible-looking off-target results are worse than none at all.
        self.assertEqual(len(response), 0)
        self.assertEqual(response.attempts[0].status, STATUS_EMPTY)

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_on_target_results_survive(self):
        from bs4 import BeautifulSoup
        from headless.transport import HtmlNode, Page
        html = ("<div class='result'>"
                "<a class='result__a' href='https://www.linkedin.com/in/peterdemin'>P</a>"
                "</div>")
        page = Page(HtmlNode(BeautifulSoup(html, "html.parser"),
                             "https://html.duckduckgo.com/"), "https://html.duckduckgo.com/")
        scr = AdvancedSearchScraper(transport="http")
        scr._transport_for = lambda e: (mock.Mock(fetch=lambda *a, **k: page, name="http"), False)
        response = scr.search("site:linkedin.com/in peter", engine="duckduckgo",
                              fallback=False)
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["url"], "https://www.linkedin.com/in/peterdemin")

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_queries_without_the_operator_are_unfiltered(self):
        from bs4 import BeautifulSoup
        from headless.transport import HtmlNode, Page
        html = ("<div class='result'>"
                "<a class='result__a' href='https://anything.example.com/x'>X</a></div>")
        page = Page(HtmlNode(BeautifulSoup(html, "html.parser"),
                             "https://html.duckduckgo.com/"), "https://html.duckduckgo.com/")
        scr = AdvancedSearchScraper(transport="http")
        scr._transport_for = lambda e: (mock.Mock(fetch=lambda *a, **k: page, name="http"), False)
        self.assertEqual(len(scr.search("plain query", engine="duckduckgo", fallback=False)), 1)


@unittest.skipUnless(http_available()[0], "needs the http extra")
class TestExtractionFromFixtures(HermeticTestCase):
    """Extractors run against recorded markup: no browser, no network."""

    def _extract(self, engine):
        scr = AdvancedSearchScraper(transport="http")
        page = fixture_page(engine)
        nodes = page.select(ENGINE_SPECS[engine]["result"])
        return scr, [scr._extract_result(n, engine) for n in nodes]

    def test_every_recorded_engine_parses(self):
        for engine in ("duckduckgo", "duckduckgo_lite", "bing"):
            with self.subTest(engine=engine):
                _, items = self._extract(engine)
                self.assertTrue(items, f"{engine}: no result containers matched")
                for item in items:
                    self.assertTrue(item["url"].startswith("http"), item["url"])
                    self.assertTrue(item["title"], f"{engine}: empty title")
                    self.assertEqual(item["engine"], engine)

    def test_recorded_pages_are_not_block_pages(self):
        scr = AdvancedSearchScraper(transport="http")
        for engine in ("duckduckgo", "duckduckgo_lite", "bing"):
            with self.subTest(engine=engine):
                self.assertEqual(scr._blocked_reason(fixture_page(engine)), "")

    def test_tracking_redirects_are_unwrapped(self):
        for engine in ("duckduckgo", "bing"):
            with self.subTest(engine=engine):
                _, items = self._extract(engine)
                for item in items:
                    self.assertNotIn("/l/?uddg=", item["url"])
                    self.assertNotIn("bing.com/ck/a", item["url"])
                    self.assertNotIn("duckduckgo.com/l/", item["url"])

    def test_declared_snippet_capability_matches_reality(self):
        # duckduckgo_lite really does return no descriptions; the others do.
        for engine in ("duckduckgo", "duckduckgo_lite", "bing"):
            with self.subTest(engine=engine):
                _, items = self._extract(engine)
                has_snippets = any(i["snippet"] for i in items)
                self.assertEqual(has_snippets,
                                 ENGINE_SPECS[engine]["snippets"],
                                 f"{engine} snippets flag disagrees with its markup")


class TestEngineCapabilities(HermeticTestCase):
    def test_every_engine_declares_its_capabilities(self):
        for name, spec in ENGINE_SPECS.items():
            with self.subTest(engine=name):
                self.assertIn("js", spec)
                self.assertIn("snippets", spec)
                self.assertIsInstance(spec["js"], bool)

    def test_capabilities_are_exposed(self):
        caps = AdvancedSearchScraper().capabilities("duckduckgo")
        self.assertFalse(caps["js"])
        self.assertTrue(caps["snippets"])
        self.assertFalse(AdvancedSearchScraper().capabilities("duckduckgo_lite")["snippets"])

    def test_registered_engines_default_to_needing_a_browser(self):
        scr = AdvancedSearchScraper()
        scr.register_engine("mine", {"url": "https://x/?q={query}", "result": "div",
                                     "link": ["a"], "title": ["h3"], "snippet": ["p"]})
        self.assertTrue(scr.capabilities("mine")["js"])

    def test_http_transport_drops_browser_only_engines_from_the_chain(self):
        scr = AdvancedSearchScraper(transport="http")
        for name in scr._engine_order():
            self.assertFalse(ENGINE_SPECS[name]["js"], f"{name} needs a browser")

    def test_invalid_transport_rejected(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(transport="carrier-pigeon")


class TestSearchSemantics(HermeticTestCase):
    """search() drives the chain without touching the network here."""

    def _scraper(self, outcomes, **kw):
        """A scraper whose engines return canned (results, status) pairs."""
        scr = AdvancedSearchScraper(transport="http", **kw)
        scr.search_engine = list(outcomes)[0]
        scr.fallback_engines = list(outcomes)[1:]
        for name in outcomes:
            scr.register_engine(name, dict(ENGINE_SPECS["duckduckgo"]))

        def fake(engine, query, limit):
            results, status = outcomes[engine]
            items = [{"url": f"https://{engine}.com/{i}", "title": f"{engine}{i}",
                      "snippet": "", "engine": engine} for i in range(results)]
            return items, EngineAttempt(engine, status, count=results,
                                        reason="captcha" if status == STATUS_BLOCKED else "")
        scr._search_one = fake
        return scr

    def test_first_answering_engine_wins_and_is_reported(self):
        scr = self._scraper({"a": (0, STATUS_BLOCKED), "b": (2, STATUS_OK),
                             "c": (5, STATUS_OK)})
        r = scr.search("q")
        self.assertEqual(len(r), 2)
        self.assertEqual(r.engine, "b")
        self.assertEqual(scr.last_engine, "b")
        self.assertEqual([a.engine for a in r.attempts], ["a", "b"])

    def test_all_blocked_is_distinguishable_from_all_empty(self):
        blocked = self._scraper({"a": (0, STATUS_BLOCKED), "b": (0, STATUS_TIMEOUT)}).search("q")
        self.assertTrue(blocked.blocked)
        self.assertEqual(blocked.refused[0].reason, "captcha")

        empty = self._scraper({"a": (0, STATUS_EMPTY), "b": (0, STATUS_EMPTY)}).search("q")
        self.assertFalse(empty.blocked)
        # Both are falsey, which is what keeps old code working.
        self.assertFalse(blocked)
        self.assertFalse(empty)

    def test_last_engine_is_cleared_on_a_failed_search(self):
        scr = self._scraper({"a": (2, STATUS_OK)})
        self.assertEqual(scr.search("q").engine, "a")
        self.assertEqual(scr.last_engine, "a")
        # A later miss must not keep attributing results to the old engine.
        scr._search_one = lambda e, q, l: ([], EngineAttempt(e, STATUS_EMPTY))
        self.assertIsNone(scr.search("q2").engine)
        self.assertIsNone(scr.last_engine)

    def test_raise_on_block_is_opt_in(self):
        scr = self._scraper({"a": (0, STATUS_BLOCKED)}, raise_on_block=True)
        with self.assertRaises(AllEnginesBlocked) as ctx:
            scr.search("q")
        self.assertTrue(ctx.exception.response.blocked)
        # Without the flag the same search simply returns empty.
        self.assertFalse(self._scraper({"a": (0, STATUS_BLOCKED)}).search("q"))

    def test_empty_results_do_not_raise_even_when_strict(self):
        scr = self._scraper({"a": (0, STATUS_EMPTY)}, raise_on_block=True)
        self.assertFalse(scr.search("q"))

    def test_history_is_off_by_default(self):
        scr = self._scraper({"a": (3, STATUS_OK)})
        for _ in range(5):
            scr.search("q")
        # Retaining every result forever leaks in a long-lived worker.
        self.assertEqual(scr.results, [])

    def test_history_is_bounded_when_enabled(self):
        scr = self._scraper({"a": (3, STATUS_OK)}, keep_history=True, history_limit=7)
        for _ in range(10):
            scr.search("q")
        self.assertEqual(len(scr.results), 7)

    def test_zero_max_results_short_circuits(self):
        scr = self._scraper({"a": (3, STATUS_OK)})
        r = scr.search("q", max_results=0)
        self.assertEqual(len(r), 0)
        self.assertEqual(r.attempts, [])


class TestExport(HermeticTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rows = [{"url": "https://a.com", "title": "A"},
                     {"url": "https://b.com", "title": "B", "extra": 1}]

    def test_export_accepts_explicit_results(self):
        scr = AdvancedSearchScraper(transport="http")
        path = os.path.join(self.tmp, "r.json")
        self.assertTrue(scr.export(path, self.rows))
        with open(path) as f:
            self.assertEqual(len(json.load(f)), 2)

    def test_export_falls_back_to_the_last_response(self):
        scr = AdvancedSearchScraper(transport="http")
        scr.last_response = SearchResponse(query="q", results=self.rows)
        path = os.path.join(self.tmp, "r.csv")
        self.assertTrue(scr.export(path))
        with open(path) as f:
            self.assertIn("url,title,extra", f.read())

    def test_unsupported_extension_returns_false(self):
        scr = AdvancedSearchScraper(transport="http")
        self.assertFalse(scr.export(os.path.join(self.tmp, "r.txt"), self.rows))


class TestLibrarySilence(HermeticTestCase):
    """A library embedded in someone else's daemon must not print uninvited."""

    def tearDown(self):
        disable_console_logging()
        logging.getLogger("headless").setLevel(logging.NOTSET)

    def test_nothing_is_printed_without_verbose(self):
        from headless.logs import get_logger
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            log = get_logger("scraper")
            log.warning("this must not reach the console")
            log.error("nor this")
        self.assertEqual(err.getvalue(), "")

    def test_verbose_opts_the_caller_in(self):
        from headless.logs import get_logger
        enable_console_logging(logging.DEBUG)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            get_logger("scraper").warning("now visible")
        self.assertIn("now visible", err.getvalue())

    def test_records_carry_the_component_name(self):
        from headless.logs import get_logger
        enable_console_logging(logging.DEBUG)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            get_logger("scraper").info("hello")
        self.assertIn("[scraper]", err.getvalue())

    def test_enable_is_idempotent(self):
        log = logging.getLogger("headless")
        before = len(log.handlers)
        enable_console_logging()
        enable_console_logging()
        enable_console_logging()
        self.assertEqual(len(log.handlers), before + 1)

    def test_application_logging_config_is_respected(self):
        # Records must reach a handler the application installed itself.
        log = logging.getLogger("headless")
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        try:
            logging.getLogger("headless.scraper").info("routed to the app")
        finally:
            log.removeHandler(handler)
        self.assertIn("routed to the app", records)


class TestUserAgent(HermeticTestCase):
    def test_default_ua_matches_this_machine(self):
        ua = default_user_agent()
        self.assertIn("Chrome/", ua)
        # The old default claimed Chrome 87 on Windows regardless of reality.
        self.assertNotIn("Chrome/87.", ua)
        import platform
        system = platform.system().lower()
        expected = {"darwin": "Macintosh", "windows": "Windows NT", "linux": "X11"}
        if system in expected:
            self.assertIn(expected[system], ua)

    def test_ua_is_used_by_default(self):
        hl = Headless()
        try:
            self.assertEqual(hl.user_agent, default_user_agent())
        finally:
            hl.quit()

    def test_explicit_ua_wins(self):
        hl = Headless(user_agent="Custom/1.0")
        try:
            self.assertIn("--user-agent=Custom/1.0", hl._build_options().arguments)
        finally:
            hl.quit()


class TestProxyPlumbing(HermeticTestCase):
    def test_headless_accepts_a_proxy(self):
        hl = Headless(proxy="socks5://127.0.0.1:9050")
        try:
            self.assertIn("--proxy-server=socks5://127.0.0.1:9050",
                          hl._build_options().arguments)
        finally:
            hl.quit()

    def test_scraper_forwards_its_proxy_to_the_browser(self):
        scr = AdvancedSearchScraper(proxy="http://127.0.0.1:8080", transport="browser")
        captured = {}

        class FakeHeadless:
            def __init__(self, **kwargs):
                captured.update(kwargs)
            def get_driver(self):
                return mock.Mock()

        with mock.patch("headless.scraper.Headless", FakeHeadless):
            scr._get_driver()
        self.assertIn("--proxy-server=http://127.0.0.1:8080",
                      captured.get("additional_args", []))


class TestDriverRecovery(HermeticTestCase):
    def test_a_dead_session_is_rebuilt_rather_than_poisoning_every_call(self):
        scr = AdvancedSearchScraper(transport="browser")
        dead = mock.Mock()
        type(dead).current_url = mock.PropertyMock(side_effect=Exception("dead"))
        scr.driver = dead

        fresh = mock.Mock()
        fresh.current_url = "about:blank"

        class FakeHeadless:
            def __init__(self, **kwargs): pass
            def get_driver(self): return fresh
            def quit(self): pass

        with mock.patch("headless.scraper.Headless", FakeHeadless):
            self.assertIs(scr._get_driver(), fresh)

    def test_recycle_discards_the_driver(self):
        scr = AdvancedSearchScraper(transport="browser")
        scr.driver = mock.Mock()
        scr.recycle()
        self.assertIsNone(scr.driver)


class TestScraperPool(HermeticTestCase):
    def test_each_thread_gets_its_own_scraper(self):
        import threading
        pool = ScraperPool(size=3, transport="http")
        seen = set()
        lock = threading.Lock()

        def record():
            with lock:
                seen.add(id(pool._scraper()))

        threads = [threading.Thread(target=record) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        try:
            self.assertEqual(len(seen), 3, "workers shared a scraper")
        finally:
            pool.quit()

    def test_same_thread_reuses_its_scraper(self):
        pool = ScraperPool(size=2, transport="http")
        try:
            self.assertIs(pool._scraper(), pool._scraper())
        finally:
            pool.quit()

    def test_rejects_a_zero_size(self):
        with self.assertRaises(ValueError):
            ScraperPool(size=0)

    def test_quit_closes_every_scraper(self):
        pool = ScraperPool(size=2, transport="http")
        scraper = pool._scraper()
        scraper.quit = mock.Mock()
        pool.quit()
        scraper.quit.assert_called_once()


class TestBatchHonesty(HermeticTestCase):
    def test_browser_batches_are_not_pretended_to_be_parallel(self):
        # One WebDriver session cannot be driven from several threads, so a
        # browser-backed batch must not claim concurrency it cannot deliver.
        scr = AdvancedSearchScraper(transport="browser")
        self.assertFalse(scr._batch_is_parallel_safe())

    @unittest.skipUnless(http_available()[0], "needs the http extra")
    def test_http_batches_can_be_parallel(self):
        self.assertTrue(AdvancedSearchScraper(transport="http")._batch_is_parallel_safe())

    def test_batch_returns_a_response_per_query(self):
        scr = AdvancedSearchScraper(transport="http")
        scr.search = lambda q, n=None: SearchResponse(query=q, results=[{"url": "u"}])
        out = scr.search_batch(["a", "b"], max_workers=2)
        self.assertEqual(sorted(out), ["a", "b"])
        self.assertTrue(all(isinstance(v, SearchResponse) for v in out.values()))

    def test_a_failing_query_does_not_sink_the_batch(self):
        scr = AdvancedSearchScraper(transport="http")

        def flaky(q, n=None):
            if q == "bad":
                raise RuntimeError("boom")
            return SearchResponse(query=q, results=[{"url": "u"}])

        scr.search = flaky
        out = scr.search_batch(["good", "bad"], max_workers=1)
        self.assertTrue(out["good"])
        self.assertFalse(out["bad"])


class TestPackaging(HermeticTestCase):
    def test_version_is_exposed(self):
        self.assertTrue(__version__)
        self.assertRegex(__version__, r"^\d+\.\d+")

    def test_py_typed_marker_ships(self):
        import headless
        marker = pathlib.Path(headless.__file__).parent / "py.typed"
        self.assertTrue(marker.exists(), "py.typed missing: downstream mypy sees Any")


if __name__ == "__main__":
    unittest.main()
