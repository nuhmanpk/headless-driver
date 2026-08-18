import os
import csv
import json
import time
import base64
import shutil
import socket
import tempfile
import unittest
from urllib.parse import quote

from headless import (
    Headless,
    SearchScraper,
    ExtendedHeadless,
    MultiDriverManager,
    AdvancedSearchScraper,
    find_chromedriver_path,
)
from headless.scraper import ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES

# Markup mirroring the html.duckduckgo.com endpoint, so the real default
# selectors are what the fixtures exercise.
RESULTS_HTML = """
<html><body>
<div class="result results_links">
  <a class="result__a" href="https://a.example.com/one">Title One</a>
  <a class="result__snippet">Snippet one</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://b.example.com/two">Title Two</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://a.example.com/one">Duplicate</a>
  <a class="result__snippet">dup snippet</a>
</div>
<div class="result result--ad">
  <a class="result__a" href="https://ad.example.com/skip">Sponsored</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://c.example.com/three">Title Three</a>
  <a class="result__snippet">Snippet three</a>
</div>
</body></html>
"""

# What DuckDuckGo actually serves when it decides a client is a bot.
BLOCKED_HTML = """
<html><body>
  <h1>Unfortunately, bots use DuckDuckGo too.</h1>
  <form id="challenge-form" method="POST"><input type="submit"></form>
</body></html>
"""

EMPTY_HTML = "<html><body><p>No results.</p></body></html>"

DDG_SPEC = ENGINE_SPECS["duckduckgo"]


def chrome_available() -> bool:
    if find_chromedriver_path():
        return True
    try:
        from webdriver_manager.chrome import ChromeDriverManager  # noqa: F401
        return True
    except ImportError:
        return False


def network_available() -> bool:
    try:
        socket.create_connection(("html.duckduckgo.com", 443), timeout=5).close()
        return True
    except OSError:
        return False


class TestEngineRegistry(unittest.TestCase):
    """Engine wiring, needing neither a browser nor the network."""

    def test_duckduckgo_is_the_default_and_uses_the_no_js_endpoint(self):
        self.assertEqual(DEFAULT_ENGINE, "duckduckgo")
        self.assertIn("html.duckduckgo.com", DDG_SPEC["url"])

    def test_every_engine_spec_is_complete(self):
        for name, spec in ENGINE_SPECS.items():
            with self.subTest(engine=name):
                self.assertLessEqual({"url", "result", "link", "title", "snippet"},
                                     set(spec))
                self.assertIn("{query}", spec["url"])

    def test_fallback_chain_is_tried_after_the_primary(self):
        scr = AdvancedSearchScraper()
        order = scr._engine_order()
        self.assertEqual(order[0], "duckduckgo")
        # Every documented fallback participates, exactly once each.
        for name in DEFAULT_FALLBACK_ENGINES:
            self.assertIn(name, order)
        self.assertEqual(len(order), len(set(order)))

    def test_bing_google_and_yandex_are_in_the_chain(self):
        order = AdvancedSearchScraper()._engine_order()
        for name in ("bing", "google", "yandex"):
            self.assertIn(name, order)

    def test_fallback_can_be_disabled(self):
        scr = AdvancedSearchScraper(fallback=False)
        self.assertEqual(scr._engine_order(), ["duckduckgo"])

    def test_forcing_an_engine_skips_the_chain(self):
        scr = AdvancedSearchScraper()
        self.assertEqual(scr._engine_order("bing"), ["bing"])

    def test_custom_fallback_order_is_honoured(self):
        scr = AdvancedSearchScraper(search_engine="bing", fallback_engines=["mojeek"])
        self.assertEqual(scr._engine_order(), ["bing", "mojeek"])

    def test_unknown_engine_rejected(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(search_engine="askjeeves")
        with self.assertRaises(ValueError):
            AdvancedSearchScraper().search("q", engine="askjeeves")

    def test_register_engine_validates_the_spec(self):
        scr = AdvancedSearchScraper()
        with self.assertRaises(ValueError):
            scr.register_engine("broken", {"url": "https://x/?q={query}"})
        scr.register_engine("mine", dict(DDG_SPEC, url="https://x/?q={query}"))
        self.assertIn("mine", scr._engine_order("mine"))

    def test_query_is_percent_encoded_for_every_engine(self):
        scr = AdvancedSearchScraper()
        for name in ENGINE_SPECS:
            with self.subTest(engine=name):
                url = scr._engine_url("python headless & c++", name)
                self.assertNotIn(" ", url)
                self.assertIn("%26", url)


class TestRedirectUnwrapping(unittest.TestCase):
    unwrap = staticmethod(AdvancedSearchScraper._unwrap_redirect)

    def test_duckduckgo_redirect(self):
        target = "https://www.python.org/downloads/"
        href = f"https://duckduckgo.com/l/?uddg={quote(target, safe='')}&rut=abc"
        self.assertEqual(self.unwrap(href), target)

    def test_bing_redirect(self):
        target = "https://www.python.org/downloads/"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        href = f"https://www.bing.com/ck/a?!&&p=abc&u=a1{encoded}&ntb=1"
        self.assertEqual(self.unwrap(href), target)

    def test_google_redirect(self):
        target = "https://docs.python.org/3/"
        href = f"https://www.google.com/url?q={quote(target, safe='')}&sa=U"
        self.assertEqual(self.unwrap(href), target)

    def test_plain_url_passes_through(self):
        for url in ("https://example.com/page?u=a1notbase64",
                    "https://example.com/l/?uddg=not-a-url",
                    "https://example.com/"):
            self.assertEqual(self.unwrap(url), url)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.scr = AdvancedSearchScraper()
        self.scr.results = [
            {"url": "https://a.example.com", "title": "A", "snippet": "sa"},
            {"url": "https://b.example.com", "title": "B", "snippet": "sb", "extra": 1},
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_json_export(self):
        path = os.path.join(self.tmp, "r.json")
        self.assertTrue(self.scr.export(path))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 2)

    def test_csv_export_has_stable_column_order(self):
        path = os.path.join(self.tmp, "r.csv")
        self.assertTrue(self.scr.export(path))
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["url", "title", "snippet", "extra"])
        self.assertEqual(len(rows), 3)

    def test_unsupported_extension_returns_false(self):
        self.assertFalse(self.scr.export(os.path.join(self.tmp, "r.txt")))

    def test_csv_export_with_no_results_returns_false(self):
        self.scr.results = []
        self.assertFalse(self.scr.export(os.path.join(self.tmp, "r.csv")))


class TestDriverOptions(unittest.TestCase):
    def test_default_driver_path_is_not_hardcoded(self):
        hl = Headless()
        try:
            self.assertIn(hl.chrome_driver_path, (None, find_chromedriver_path()))
        finally:
            hl.quit()

    def test_page_load_timeout_defaults_to_a_bounded_value(self):
        # Selenium's own default is 300s, which is what made searches hang.
        hl = Headless()
        try:
            self.assertIsNotNone(hl.page_load_timeout)
            self.assertLessEqual(hl.page_load_timeout, 60)
        finally:
            hl.quit()

    def test_options_respect_constructor_arguments(self):
        hl = Headless(headless=False, window_size=(800, 600),
                      user_agent="UA/1.0", additional_args=["--mute-audio"])
        try:
            args = hl._build_options().arguments
            self.assertFalse(any(a.startswith("--headless") for a in args))
            self.assertIn("--window-size=800,600", args)
            self.assertIn("--user-agent=UA/1.0", args)
            self.assertIn("--mute-audio", args)
        finally:
            hl.quit()

    def test_extended_options_extend_rather_than_replace_base(self):
        hl = ExtendedHeadless(headless=False, window_size=(1024, 768),
                              user_agent="UA/2.0", proxy="http://127.0.0.1:8080")
        try:
            args = hl._build_options().arguments
            self.assertFalse(any(a.startswith("--headless") for a in args))
            self.assertIn("--window-size=1024,768", args)
            self.assertIn("--user-agent=UA/2.0", args)
            self.assertIn("--proxy-server=http://127.0.0.1:8080", args)
            self.assertTrue(any(a.startswith("--user-data-dir=") for a in args))
        finally:
            hl.quit()

    def test_invalid_window_size_rejected(self):
        with self.assertRaises(ValueError):
            Headless(window_size=(1920,))


@unittest.skipUnless(chrome_available(), "no chromedriver available")
class TestFallbackChain(unittest.TestCase):
    """The chain is driven from local files, so it needs no network at all."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None)
        cls.driver = cls.hl.get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.hl.quit()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _fixture(self, name, html):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return "file://" + path

    def _scraper(self, engines, primary, **kwargs):
        """A scraper whose engines all point at local fixture files."""
        scr = AdvancedSearchScraper(driver=self.driver, max_results=5, **kwargs)
        for name, url in engines.items():
            scr.register_engine(name, dict(DDG_SPEC, url=url))
        scr.search_engine = primary
        scr.fallback_engines = [n for n in engines if n != primary]
        return scr

    def test_primary_engine_results_are_used(self):
        url = self._fixture("ok.html", RESULTS_HTML)
        scr = self._scraper({"primary": url, "backup": url}, "primary")
        results = scr.search("q")
        self.assertEqual(scr.last_engine, "primary")
        self.assertEqual([r["title"] for r in results],
                         ["Title One", "Title Two", "Title Three"])
        # Ads are excluded and the duplicate URL collapses.
        self.assertNotIn("https://ad.example.com/skip", [r["url"] for r in results])
        self.assertEqual(len({r["url"] for r in results}), 3)
        # A result whose snippet is missing must still be returned.
        self.assertEqual(sum(1 for r in results if r["snippet"] == ""), 1)
        self.assertTrue(all(r["engine"] == "primary" for r in results))

    def test_blocked_primary_falls_through_to_the_next_engine(self):
        blocked = self._fixture("blocked.html", BLOCKED_HTML)
        ok = self._fixture("ok2.html", RESULTS_HTML)
        scr = self._scraper({"primary": blocked, "backup": ok}, "primary")
        results = scr.search("q")
        self.assertEqual(scr.last_engine, "backup")
        self.assertEqual(len(results), 3)

    def test_block_is_detected_by_its_challenge_form(self):
        blocked = self._fixture("blocked2.html", BLOCKED_HTML)
        scr = self._scraper({"primary": blocked}, "primary", fallback=False)
        self.assertEqual(scr.search("q"), [])
        self.assertIn("bot-check", scr._blocked_reason(self.driver))

    def test_empty_primary_falls_through(self):
        empty = self._fixture("empty.html", EMPTY_HTML)
        ok = self._fixture("ok3.html", RESULTS_HTML)
        scr = self._scraper({"primary": empty, "backup": ok}, "primary",
                            wait_timeout=2.0)
        self.assertEqual(len(scr.search("q")), 3)
        self.assertEqual(scr.last_engine, "backup")

    def test_chain_walks_past_several_dead_engines(self):
        blocked = self._fixture("b3.html", BLOCKED_HTML)
        empty = self._fixture("e3.html", EMPTY_HTML)
        ok = self._fixture("ok4.html", RESULTS_HTML)
        scr = AdvancedSearchScraper(driver=self.driver, max_results=5, wait_timeout=2.0)
        for name, url in (("e1", blocked), ("e2", empty), ("e3", ok)):
            scr.register_engine(name, dict(DDG_SPEC, url=url))
        scr.search_engine = "e1"
        scr.fallback_engines = ["e2", "e3"]
        self.assertEqual(len(scr.search("q")), 3)
        self.assertEqual(scr.last_engine, "e3")

    def test_all_engines_failing_returns_empty_list(self):
        blocked = self._fixture("b4.html", BLOCKED_HTML)
        empty = self._fixture("e4.html", EMPTY_HTML)
        scr = self._scraper({"primary": blocked, "backup": empty}, "primary",
                            wait_timeout=2.0)
        self.assertEqual(scr.search("q"), [])
        self.assertIsNone(scr.last_engine)

    def test_unreachable_engine_times_out_instead_of_hanging(self):
        ok = self._fixture("ok5.html", RESULTS_HTML)
        scr = AdvancedSearchScraper(driver=self.driver, max_results=5,
                                    page_load_timeout=5.0, wait_timeout=2.0)
        # 10.255.255.1 is non-routable, so the load stalls until it is cut off.
        scr.register_engine("blackhole", dict(DDG_SPEC, url="http://10.255.255.1/?q={query}"))
        scr.register_engine("backup", dict(DDG_SPEC, url=ok))
        scr.search_engine = "blackhole"
        scr.fallback_engines = ["backup"]
        started = time.time()
        results = scr.search("q")
        elapsed = time.time() - started
        self.assertEqual(len(results), 3)
        self.assertEqual(scr.last_engine, "backup")
        # Selenium's 300s default would blow straight past this.
        self.assertLess(elapsed, 60, f"search took {elapsed:.1f}s")
        # The timeout must not poison the session for the next engine.
        self.assertEqual(len(scr.search("q", engine="backup")), 3)

    def test_forced_engine_does_not_fall_back(self):
        blocked = self._fixture("b5.html", BLOCKED_HTML)
        ok = self._fixture("ok6.html", RESULTS_HTML)
        scr = self._scraper({"primary": blocked, "backup": ok}, "primary")
        self.assertEqual(scr.search("q", engine="primary"), [])
        self.assertEqual(len(scr.search("q", engine="backup")), 3)

    def test_max_results_is_respected(self):
        ok = self._fixture("ok7.html", RESULTS_HTML)
        scr = self._scraper({"primary": ok}, "primary", fallback=False)
        self.assertEqual(len(scr.search("q", max_results=2)), 2)
        self.assertEqual(scr.search("q", max_results=0), [])

    def test_search_batch_is_serialised_over_one_driver(self):
        ok = self._fixture("ok8.html", RESULTS_HTML)
        scr = self._scraper({"primary": ok}, "primary", fallback=False)
        batch = scr.search_batch(["q1", "q2", "q3"], max_workers=3, per_query=2)
        self.assertEqual(sorted(batch), ["q1", "q2", "q3"])
        self.assertTrue(all(len(v) == 2 for v in batch.values()))

    def test_search_scraper_delegates_and_falls_back(self):
        blocked = self._fixture("b6.html", BLOCKED_HTML)
        ok = self._fixture("ok9.html", RESULTS_HTML)
        scraper = SearchScraper(driver=self.driver, max_results=5)
        inner = scraper._scraper
        for name, url in (("primary", blocked), ("backup", ok)):
            inner.register_engine(name, dict(DDG_SPEC, url=url))
        inner.search_engine = "primary"
        inner.fallback_engines = ["backup"]
        results = scraper.search("q")
        self.assertEqual(len(results), 3)
        self.assertEqual(scraper.last_engine, "backup")
        # This class's contract is the simpler {"url", "snippet"} shape.
        self.assertEqual(sorted(results[0]), ["snippet", "url"])


@unittest.skipUnless(chrome_available(), "no chromedriver available")
class TestBrowserLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.page = os.path.join(cls.tmp, "page.html")
        with open(cls.page, "w", encoding="utf-8") as f:
            f.write(RESULTS_HTML)
        cls.hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None)
        cls.driver = cls.hl.get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.hl.quit()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_driver_is_cached_across_calls(self):
        self.assertEqual(self.hl.get_driver().session_id, self.driver.session_id)

    def test_capture_uses_the_current_page(self):
        self.driver.get("file://" + self.page)
        png = os.path.join(self.tmp, "shot.png")
        self.assertTrue(self.hl.screenshot(png))
        self.assertGreater(os.path.getsize(png), 0)
        self.assertEqual(self.driver.session_id, self.hl.get_driver().session_id)
        self.assertTrue(self.driver.current_url.endswith("page.html"))

    def test_save_pdf(self):
        self.driver.get("file://" + self.page)
        pdf = os.path.join(self.tmp, "page.pdf")
        self.assertTrue(self.hl.save_pdf(pdf))
        with open(pdf, "rb") as f:
            self.assertEqual(f.read(4), b"%PDF")

    def test_quit_clears_driver_and_temp_profile(self):
        hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None)
        hl.get_driver()
        profile = hl.user_data_dir
        hl.quit()
        self.assertIsNone(hl._driver)
        self.assertFalse(os.path.isdir(profile))

    def test_manager_replacing_a_name_quits_the_old_instance(self):
        mgr = MultiDriverManager()
        try:
            first = mgr.create("bot", auto_install=True, chrome_binary_path=None)
            first.get_driver()
            mgr.create("bot", auto_install=True, chrome_binary_path=None)
            self.assertIsNone(first._driver)
            self.assertEqual(len(mgr.instances), 1)
        finally:
            mgr.quit_all()


@unittest.skipUnless(chrome_available() and network_available(),
                     "needs a chromedriver and internet access")
class TestLiveSearch(unittest.TestCase):
    """End-to-end against the real engines."""

    def test_search_returns_real_results_quickly(self):
        scr = AdvancedSearchScraper(max_results=5)
        try:
            started = time.time()
            results = scr.search("python headless browser")
            elapsed = time.time() - started
            self.assertTrue(results, "no engine in the chain returned results")
            self.assertIsNotNone(scr.last_engine)
            for r in results:
                self.assertTrue(r["url"].startswith(("http://", "https://")))
                self.assertTrue(r["title"])
                # Click-tracking redirects must be resolved to real destinations.
                self.assertNotIn("/l/?uddg=", r["url"])
                self.assertNotIn("bing.com/ck/a", r["url"])
            self.assertLess(elapsed, 120, f"search took {elapsed:.1f}s")
        finally:
            scr.quit()

    def test_search_scraper_end_to_end(self):
        with SearchScraper(max_results=3) as scraper:
            results = scraper.search("selenium stealth")
            self.assertTrue(results)
            self.assertTrue(all(r["url"].startswith("http") for r in results))


if __name__ == "__main__":
    unittest.main()
