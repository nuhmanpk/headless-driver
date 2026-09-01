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

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture_page(name: str) -> Page:
    """Parse a recorded results page, exactly as the HTTP transport would."""
    from bs4 import BeautifulSoup
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    url = ENGINE_SPECS[name]["url"].replace("{query}", "wikipedia")
    return Page(HtmlNode(BeautifulSoup(html, "html.parser"), url), url)


class TestSearchResponse(unittest.TestCase):
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


@unittest.skipUnless(http_available()[0], "needs the http extra")
class TestExtractionFromFixtures(unittest.TestCase):
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


class TestEngineCapabilities(unittest.TestCase):
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


class TestSearchSemantics(unittest.TestCase):
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


class TestExport(unittest.TestCase):
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


class TestLibrarySilence(unittest.TestCase):
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


class TestUserAgent(unittest.TestCase):
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


class TestProxyPlumbing(unittest.TestCase):
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


class TestDriverRecovery(unittest.TestCase):
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


class TestScraperPool(unittest.TestCase):
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


class TestBatchHonesty(unittest.TestCase):
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


class TestPackaging(unittest.TestCase):
    def test_version_is_exposed(self):
        self.assertTrue(__version__)
        self.assertRegex(__version__, r"^\d+\.\d+")

    def test_py_typed_marker_ships(self):
        import headless
        marker = pathlib.Path(headless.__file__).parent / "py.typed"
        self.assertTrue(marker.exists(), "py.typed missing: downstream mypy sees Any")


if __name__ == "__main__":
    unittest.main()
