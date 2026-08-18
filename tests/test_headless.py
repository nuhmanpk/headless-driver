import os
import csv
import json
import shutil
import tempfile
import unittest

from headless import (
    Headless,
    SearchScraper,
    ExtendedHeadless,
    MultiDriverManager,
    AdvancedSearchScraper,
    find_chromedriver_path,
)

FIXTURE_HTML = """
<html><body>
<div data-testid="result">
  <a data-testid="result-title-a" href="https://a.example.com/one">Title One</a>
  <div data-result="snippet">Snippet one</div>
</div>
<div data-testid="result">
  <a data-testid="result-title-a" href="https://b.example.com/two">Title Two</a>
</div>
<div data-testid="result">
  <a data-testid="result-title-a" href="https://a.example.com/one">Dup</a>
  <div data-result="snippet">dup snippet</div>
</div>
<div data-testid="result">
  <a data-testid="result-title-a" href="https://c.example.com/three">Title Three</a>
  <div data-result="snippet">Snippet three</div>
</div>
</body></html>
"""


def chrome_available() -> bool:
    if find_chromedriver_path():
        return True
    try:
        from webdriver_manager.chrome import ChromeDriverManager  # noqa: F401
        return True
    except ImportError:
        return False


class TestOptionsAndUrls(unittest.TestCase):
    """Pure-logic checks that need neither a browser nor the network."""

    def test_default_driver_path_is_not_hardcoded(self):
        # A hardcoded Homebrew path made the library unusable off macOS.
        hl = Headless()
        try:
            self.assertIn(hl.chrome_driver_path, (None, find_chromedriver_path()))
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
        # ExtendedHeadless used to rebuild options from scratch, silently
        # dropping window size, user agent and the `headless` flag.
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

    def test_query_is_percent_encoded(self):
        scr = AdvancedSearchScraper(search_engine="bing")
        url = scr._engine_url("python headless & stuff")
        self.assertIn("python+headless+%26+stuff", url)
        self.assertNotIn(" ", url)

    def test_unknown_engine_rejected(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(search_engine="yahoo")

    def test_bing_redirect_is_unwrapped(self):
        import base64
        target = "https://www.python.org/downloads/"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        href = f"https://www.bing.com/ck/a?!&&p=abc&u=a1{encoded}&ntb=1"
        self.assertEqual(AdvancedSearchScraper._unwrap_redirect(href), target)

    def test_non_redirect_url_passes_through(self):
        url = "https://example.com/page?u=a1notbase64"
        self.assertEqual(AdvancedSearchScraper._unwrap_redirect(url), url)


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
        # First-seen key order, not set iteration order.
        self.assertEqual(rows[0], ["url", "title", "snippet", "extra"])
        self.assertEqual(len(rows), 3)

    def test_unsupported_extension_returns_false(self):
        self.assertFalse(self.scr.export(os.path.join(self.tmp, "r.txt")))

    def test_csv_export_with_no_results_returns_false(self):
        self.scr.results = []
        self.assertFalse(self.scr.export(os.path.join(self.tmp, "r.csv")))


@unittest.skipUnless(chrome_available(), "no chromedriver available")
class TestWithBrowser(unittest.TestCase):
    """Scraping checks driven from a local fixture, so no network is needed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fixture = os.path.join(cls.tmp, "results.html")
        with open(cls.fixture, "w", encoding="utf-8") as f:
            f.write(FIXTURE_HTML)
        cls.fixture_url = "file://" + cls.fixture
        cls.hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None)
        cls.driver = cls.hl.get_driver()

    @classmethod
    def tearDownClass(cls):
        cls.hl.quit()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_driver_is_cached_across_calls(self):
        # get_driver() used to spawn a brand new browser on every call.
        self.assertEqual(self.hl.get_driver().session_id, self.driver.session_id)

    def test_capture_uses_the_current_page(self):
        self.driver.get(self.fixture_url)
        png = os.path.join(self.tmp, "shot.png")
        self.assertTrue(self.hl.screenshot(png))
        self.assertGreater(os.path.getsize(png), 0)
        # Capturing must not navigate away or open a second browser.
        self.assertEqual(self.driver.session_id, self.hl.get_driver().session_id)
        self.assertTrue(self.driver.current_url.endswith("results.html"))

    def test_save_pdf(self):
        self.driver.get(self.fixture_url)
        pdf = os.path.join(self.tmp, "page.pdf")
        self.assertTrue(self.hl.save_pdf(pdf))
        with open(pdf, "rb") as f:
            self.assertTrue(f.read(4) == b"%PDF")

    def test_search_keeps_results_without_a_snippet(self):
        scraper = SearchScraper(driver=self.driver, max_results=5,
                                search_engine_url=self.fixture_url)
        results = scraper.search("ignored")
        # Three unique URLs; the snippet-less one must not be discarded.
        self.assertEqual(len(results), 3)
        self.assertEqual(sum(1 for r in results if r["snippet"] == ""), 1)
        self.assertEqual(len({r["url"] for r in results}), 3)

    def test_search_respects_max_results(self):
        scraper = SearchScraper(driver=self.driver, max_results=5,
                                search_engine_url=self.fixture_url)
        self.assertEqual(len(scraper.search("ignored", max_results=2)), 2)
        self.assertEqual(scraper.search("ignored", max_results=0), [])

    def test_advanced_scraper_extracts_titles(self):
        scr = AdvancedSearchScraper(driver=self.driver, max_results=5)
        scr._engine_url = lambda q: self.fixture_url
        results = scr.search("ignored")
        self.assertEqual([r["title"] for r in results],
                         ["Title One", "Title Two", "Title Three"])
        self.assertTrue(all(r["favicon"].startswith("https://") for r in results))

    def test_search_batch_is_serialised_over_one_driver(self):
        scr = AdvancedSearchScraper(driver=self.driver, max_results=2)
        scr._engine_url = lambda q: self.fixture_url
        batch = scr.search_batch(["q1", "q2", "q3"], max_workers=3)
        self.assertEqual(sorted(batch), ["q1", "q2", "q3"])
        self.assertTrue(all(len(v) == 2 for v in batch.values()))

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
            # Replacing the name must not orphan the first browser.
            self.assertIsNone(first._driver)
            self.assertEqual(len(mgr.instances), 1)
        finally:
            mgr.quit_all()


if __name__ == "__main__":
    unittest.main()
