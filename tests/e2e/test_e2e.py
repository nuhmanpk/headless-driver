"""End to end: scrape a real HTTP site built from known data, and compare.

Unlike the unit tests, nothing here is mocked. A local server (``server.py``)
serves pages in each engine's real markup, generated from ``data.json``; the
scraper fetches them over real sockets through each transport, and whatever it
collects must equal the ground truth exactly — titles unescaped, URLs
unwrapped and tidied, ads dropped.

In CI this runs against the package *installed from its wheel*, not the source
tree, so it also proves the published artefact works::

    python -m build && pip install dist/*.whl[impersonate]
    cd /tmp && E2E_REQUIRE_INSTALLED=1 python -m unittest discover -s <repo>/tests/e2e
"""

import os
import csv
import json
import shutil
import pathlib
import tempfile
import unittest

import headless
from headless import AdvancedSearchScraper, EngineHealth
from headless.transport import http_available, impersonate_available

try:
    from .server import SearchSite, point_at, TOKEN
except ImportError:  # run as a top-level module by `discover -s tests/e2e`
    from server import SearchSite, point_at, TOKEN

HERE = pathlib.Path(__file__).parent
DATA = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
EXPECTED = [(r["url"], r["title"], r["snippet"]) for r in DATA["results"]]
LINKEDIN = [row for row in EXPECTED if "linkedin.com/in/" in row[0]]

BROWSERLESS = [t for t, ok in (("impersonate", impersonate_available()[0]),
                               ("http", http_available()[0])) if ok]
ENGINES = ["brave", "duckduckgo", "duckduckgo_lite", "yahoo", "mojeek",
           "google_basic", "bing", "startpage"]
NO_SNIPPETS = {"duckduckgo_lite"}


def setUpModule():
    global SITE
    SITE = SearchSite().start()


def tearDownModule():
    SITE.stop()


class E2ECase(unittest.TestCase):
    def setUp(self):
        SITE.clear()
        headless.reset_default_health()

    def scraper(self, transport, **kwargs):
        kwargs.setdefault("health", EngineHealth())
        kwargs.setdefault("max_results", 10)
        scr = AdvancedSearchScraper(transport=transport, **kwargs)
        point_at(scr, SITE.url)
        self.addCleanup(scr.quit)
        return scr

    def assertGroundTruth(self, results, engine, expected=EXPECTED):
        got = [(r["url"], r["title"], r["snippet"]) for r in results]
        if engine in NO_SNIPPETS:
            expected = [(u, t, "") for u, t, _ in expected]
        self.assertEqual(got, expected, f"{engine} did not return the ground truth")
        for r in results:
            self.assertEqual(r["engine"], engine)
            self.assertNotIn("Sponsored", r["title"])
            self.assertTrue(r["favicon"].startswith("https://www.google.com/s2/favicons"))


class TestInstalledPackage(E2ECase):
    def test_runs_against_the_installed_wheel_when_required(self):
        if not os.environ.get("E2E_REQUIRE_INSTALLED"):
            self.skipTest("set E2E_REQUIRE_INSTALLED=1 to insist on an installed package")
        location = pathlib.Path(headless.__file__).resolve()
        self.assertIn("site-packages", str(location),
                      f"imported headless from {location}, not an installed wheel")
        self.assertNotEqual(headless.__version__, "0.0.0.dev0")

    def test_site_serves_its_index(self):
        from urllib.request import urlopen
        with urlopen(SITE.url + "/") as response:
            body = response.read().decode("utf-8")
        self.assertIn("end-to-end test site", body)
        for row in DATA["results"]:
            self.assertIn(row["snippet_html"].split("<")[0][:20], body)


class TestEveryEngineReturnsTheGroundTruth(E2ECase):
    def test_each_engine_over_each_browserless_transport(self):
        for transport in BROWSERLESS:
            for engine in ENGINES:
                with self.subTest(transport=transport, engine=engine):
                    scr = self.scraper(transport)
                    response = scr.search("credo capital", engine=engine, fallback=False)
                    self.assertEqual(response.engine, engine, response.attempts)
                    self.assertEqual(response.attempts[0].http_status, 200)
                    self.assertEqual(response.attempts[0].transport, transport)
                    self.assertGroundTruth(response, engine)

    def test_site_operator_keeps_only_matching_results(self):
        transport = BROWSERLESS[0]
        for engine in ("brave", "duckduckgo", "yahoo", "mojeek", "google_basic"):
            with self.subTest(engine=engine):
                scr = self.scraper(transport)
                response = scr.search("site:linkedin.com/in credo capital",
                                      engine=engine, fallback=False)
                self.assertGroundTruth(response, engine, LINKEDIN)

    def test_max_results_is_respected(self):
        scr = self.scraper(BROWSERLESS[0], max_results=2)
        self.assertEqual(len(scr.search("credo", engine="yahoo", fallback=False)), 2)


class TestRequestsLookLikeTheRealFrontEnds(E2ECase):
    def test_duckduckgo_posts_its_form_with_the_region(self):
        scr = self.scraper(BROWSERLESS[0], region="uk-en")
        scr.search("credo", engine="duckduckgo", fallback=False)
        (req,) = SITE.requests_to("duckduckgo")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["fields"], {"q": "credo", "b": "", "l": "uk-en"})

    def test_yahoo_path_tokens_change_every_request(self):
        scr = self.scraper(BROWSERLESS[0])
        scr.search("credo", engine="yahoo", fallback=False)
        scr.search("credo again", engine="yahoo", fallback=False)
        paths = [r["path"] for r in SITE.requests_to("yahoo")]
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(";_ylt=" in p and ";_ylu=" in p for p in paths))
        self.assertNotEqual(paths[0], paths[1])

    def test_google_is_asked_as_the_search_app(self):
        scr = self.scraper(BROWSERLESS[0], region="uk-en")
        scr.search("credo", engine="google_basic", fallback=False)
        (req,) = SITE.requests_to("google_basic")
        self.assertTrue(req["user_agent"].endswith("NSTNWV"))
        self.assertEqual(req["cookies"].get("CONSENT"), "YES+")
        self.assertEqual(req["fields"]["hl"], "en-GB")

    def test_startpage_fetches_its_token_first(self):
        scr = self.scraper(BROWSERLESS[0])
        response = scr.search("credo", engine="startpage", fallback=False)
        self.assertTrue(response)
        home, search = SITE.requests_to("startpage")
        self.assertEqual(home["method"], "GET")
        self.assertEqual(search["method"], "POST")
        self.assertEqual(search["fields"]["sc"], TOKEN)
        self.assertEqual(search["referer"], "https://www.startpage.com/")

    def test_regional_cookies(self):
        scr = self.scraper(BROWSERLESS[0], region="uk-en")
        scr.search("credo", engine="mojeek", fallback=False)
        scr.search("credo", engine="brave", fallback=False)
        self.assertEqual(SITE.requests_to("mojeek")[0]["cookies"], {"arc": "uk", "lb": "en"})
        self.assertEqual(SITE.requests_to("brave")[0]["cookies"].get("country"), "gb")

    @unittest.skipUnless("impersonate" in BROWSERLESS, "needs curl_cffi")
    def test_impersonation_sends_a_real_browser_user_agent(self):
        scr = self.scraper("impersonate")
        scr.search("credo", engine="brave", fallback=False)
        ua = SITE.requests_to("brave")[0]["user_agent"]
        self.assertIn("Mozilla/5.0", ua)
        self.assertNotIn("python", ua.lower())


class TestRefusalsAreReportedHonestly(E2ECase):
    CASES = [
        ("zzznothing", "empty", 200),
        ("zzzblock", "blocked", 403),
        ("zzzslow", "rate_limited", 429),
        ("zzzcaptcha", "blocked", 200),
        ("zzzlayout", "unparsed", 200),
    ]

    def test_each_failure_mode(self):
        scr = self.scraper(BROWSERLESS[0], circuit_breaker=False)
        for word, status, code in self.CASES:
            for engine in ("brave", "duckduckgo", "yahoo", "mojeek"):
                with self.subTest(case=word, engine=engine):
                    response = scr.search(f"credo {word}", engine=engine, fallback=False)
                    attempt = response.attempts[0]
                    self.assertEqual(attempt.status, status, attempt)
                    self.assertEqual(attempt.http_status, code)
                    self.assertEqual(response, [])
                    self.assertEqual(response.blocked, status != "empty")
        response = scr.search("credo zzzslow", engine="brave", fallback=False)
        self.assertEqual(response.retry_after, 30.0)

    def test_google_javascript_wall_is_a_block(self):
        # Asked without the Search App client, Google serves its JS wall.
        scr = self.scraper(BROWSERLESS[0], circuit_breaker=False)
        scr.engines["google_basic"]["headers"] = {"User-Agent": "Mozilla/5.0 Chrome/140"}
        attempt = scr.search("credo", engine="google_basic", fallback=False).attempts[0]
        self.assertEqual(attempt.status, "blocked")
        self.assertIn("enablejs", attempt.reason)


class TestTheChainAndTheBreaker(E2ECase):
    def test_fallback_walks_past_refusals_to_an_answer(self):
        scr = self.scraper(BROWSERLESS[0], fallback_engines=["duckduckgo", "yahoo"])
        response = scr.search("credo zzzblock_brave zzzslow_duckduckgo")
        self.assertEqual(response.engine, "yahoo")
        self.assertEqual([a.status for a in response.attempts],
                         ["blocked", "rate_limited", "ok"])
        self.assertGroundTruth(response, "yahoo")

    def test_a_refusing_engine_stands_down(self):
        health = EngineHealth(failures_before_backoff=3)
        scr = self.scraper(BROWSERLESS[0], health=health, fallback_engines=["mojeek"])
        for _ in range(3):
            self.assertEqual(scr.search("credo zzzblock_brave").engine, "mojeek")
        self.assertEqual(len(SITE.requests_to("brave")), 3)
        response = scr.search("credo zzzblock_brave")
        self.assertEqual(response.engine, "mojeek")
        self.assertEqual(len(SITE.requests_to("brave")), 3)    # not asked a 4th time
        self.assertEqual(response.skipped[0]["engine"], "brave")
        self.assertEqual(scr.health()["brave"]["state"], "cooling")

    def test_bing_is_skipped_for_site_queries(self):
        scr = self.scraper(BROWSERLESS[0], search_engine="brave", fallback_engines=["bing"])
        response = scr.search("site:linkedin.com/in credo zzznothing_brave")
        self.assertEqual(response.engines_tried, ["brave"])
        self.assertEqual(SITE.requests_to("bing"), [])


class TestAggregateMode(E2ECase):
    def test_engines_agree_and_are_counted(self):
        scr = self.scraper(BROWSERLESS[0])
        response = scr.search("credo capital", mode="aggregate",
                              engines=["brave", "duckduckgo", "yahoo", "mojeek",
                                       "google_basic"], min_engines=4)
        self.assertEqual(response.engine, "aggregate")
        self.assertEqual(sorted(response.engines),
                         ["brave", "duckduckgo", "google_basic", "mojeek"])
        # Yahoo and DuckDuckGo are one index: only one of them is asked.
        self.assertEqual(SITE.requests_to("yahoo"), [])
        self.assertEqual(len(response), len(EXPECTED))
        for item in response:
            self.assertEqual(item["votes"], 4)
            self.assertEqual(len(item["ranks"]), 4)
        # Consensus preserves the order every engine agreed on.
        self.assertEqual([r["url"] for r in response], [u for u, _, _ in EXPECTED])

    def test_a_blocked_engine_is_replaced_by_its_sibling(self):
        scr = self.scraper(BROWSERLESS[0])
        response = scr.search("credo zzzblock_duckduckgo", mode="aggregate",
                              engines=["brave", "duckduckgo", "yahoo"])
        self.assertEqual(len(SITE.requests_to("yahoo")), 1)
        self.assertIn("yahoo", response.engines)
        self.assertEqual({r["votes"] for r in response}, {2})

    def test_deadline_bounds_the_search(self):
        import time
        scr = self.scraper(BROWSERLESS[0])
        started = time.time()
        response = scr.search("credo zzzsleep_brave", mode="aggregate",
                              engines=["brave", "mojeek"], deadline=1.0)
        self.assertLess(time.time() - started, 1.9)
        statuses = {a.engine: a.status for a in response.attempts}
        self.assertEqual(statuses, {"brave": "timeout", "mojeek": "ok"})
        self.assertEqual(len(response), len(EXPECTED))


class TestExportRoundTrip(E2ECase):
    def test_json_and_csv_hold_exactly_what_was_scraped(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        scr = self.scraper(BROWSERLESS[0])
        response = scr.search("credo", engine="brave", fallback=False)
        path_json, path_csv = os.path.join(tmp, "r.json"), os.path.join(tmp, "r.csv")
        self.assertTrue(scr.export(path_json))
        self.assertTrue(scr.export(path_csv))
        with open(path_json, encoding="utf-8") as f:
            self.assertEqual([(r["url"], r["title"], r["snippet"]) for r in json.load(f)],
                             EXPECTED)
        with open(path_csv, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r["url"], r["title"], r["snippet"]) for r in rows], EXPECTED)
        self.assertEqual(json.loads(json.dumps(response.as_dict()))["engine"], "brave")


def chrome_usable() -> bool:
    if os.environ.get("E2E_BROWSER", "1") == "0":
        return False
    try:
        from headless import ExtendedHeadless
        hl = ExtendedHeadless(auto_install=True)
        try:
            hl.get_driver()
            return True
        finally:
            hl.quit()
    except Exception:
        return False


class TestThroughARealBrowser(E2ECase):
    """The same pages through Chrome: the browser transport's selectors and
    text extraction must agree with the HTTP transports'."""

    @classmethod
    def setUpClass(cls):
        if not chrome_usable():
            raise unittest.SkipTest("Chrome is not available")

    def test_browser_transport_returns_the_ground_truth(self):
        scr = self.scraper("browser")
        for engine in ("brave", "duckduckgo", "yahoo", "mojeek", "bing"):
            with self.subTest(engine=engine):
                response = scr.search("credo capital", engine=engine, fallback=False)
                self.assertEqual(response.attempts[0].transport, "browser")
                self.assertGroundTruth(response, engine)


def playwright_usable() -> bool:
    if os.environ.get("E2E_PLAYWRIGHT", "1") == "0":
        return False
    try:
        from headless.playwright_driver import PlaywrightBrowser
        with PlaywrightBrowser() as browser:
            browser.fetch_html("about:blank")
        return True
    except Exception:
        return False


class TestThroughPlaywright(E2ECase):
    """Playwright sees the HTTP layer, so refusals are classified by status
    even through a browser — something Selenium cannot do."""

    @classmethod
    def setUpClass(cls):
        if not playwright_usable():
            raise unittest.SkipTest("Playwright or its Chromium build is not installed")
        from headless.playwright_driver import PlaywrightBrowser
        cls.browser = PlaywrightBrowser()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def test_every_engine_returns_the_ground_truth(self):
        scr = self.scraper("browser", browser="playwright")
        for engine in ("brave", "duckduckgo", "yahoo", "mojeek", "bing"):
            with self.subTest(engine=engine):
                response = scr.search("credo capital", engine=engine, fallback=False)
                attempt = response.attempts[0]
                self.assertEqual(attempt.transport, "playwright")
                self.assertEqual(attempt.http_status, 200)
                self.assertGroundTruth(response, engine)

    def test_status_codes_are_seen_through_the_browser(self):
        scr = self.scraper("browser", browser="playwright", circuit_breaker=False)
        response = scr.search("credo zzzslow", engine="yahoo", fallback=False)
        self.assertEqual(response.attempts[0].status, "rate_limited")
        self.assertEqual(response.retry_after, 30.0)
        response = scr.search("credo zzzblock", engine="brave", fallback=False)
        self.assertEqual(response.attempts[0].http_status, 403)

    def test_browser_engines_in_auto_mode_use_playwright(self):
        scr = self.scraper(BROWSERLESS[0], browser="playwright")
        scr.engines["mojeek"]["js"] = True
        response = scr.search("credo", engine="mojeek", fallback=False)
        self.assertEqual(response.attempts[0].transport, "playwright")
        self.assertGroundTruth(response, "mojeek")

    def test_headless_is_not_announced(self):
        scr = self.scraper("browser", browser="playwright")
        scr.search("credo", engine="brave", fallback=False)
        ua = SITE.requests_to("brave")[0]["user_agent"]
        self.assertIn("Chrome/", ua)
        self.assertNotIn("HeadlessChrome", ua)
        page = self.browser.context("stealth").new_page()
        try:
            page.goto(SITE.url + "/")
            self.assertIsNone(page.evaluate("navigator.webdriver"))
            self.assertTrue(page.evaluate("!!window.chrome"))
            self.assertGreater(page.evaluate("navigator.plugins.length"), 0)
        finally:
            page.close()

    def test_schema_extraction_matches_the_ground_truth(self):
        rows = self.browser.extract(
            SITE.url + "/brave.html",
            {"url": "a@href", "title": "div.title", "snippet": "div.content"},
            item_selector="div[data-type='web']")
        self.assertEqual([(r["url"], r["title"], r["snippet"]) for r in rows],
                         [(r.get("href") or r["url"], t, sn) for r, (_, t, sn)
                          in zip(DATA["results"], EXPECTED)])
        page = self.browser.extract(SITE.url + "/", {"heading": "h1"})
        self.assertEqual(page, {"heading": "headless-driver end-to-end test site"})

    def test_screenshot_and_pdf(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        shot, pdf = os.path.join(tmp, "s.png"), os.path.join(tmp, "p.pdf")
        self.assertTrue(self.browser.screenshot(SITE.url + "/", shot))
        self.assertTrue(self.browser.pdf(SITE.url + "/", pdf))
        with open(shot, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        with open(pdf, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")

    def test_json_capture_and_blocked_resources(self):
        def fetch_api(page):
            page.evaluate("fetch('/api/results.json').then(r => r.json())")
        captured = self.browser.capture_json(SITE.url + "/", r"/api/", action=fetch_api)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["status"], 200)
        self.assertEqual([r["url"] for r in captured[0]["json"]["results"]],
                         [u for u, _, _ in EXPECTED])

    def test_rotate_discards_cookies(self):
        ctx = self.browser.context("rot")
        ctx.add_cookies([{"name": "seen", "value": "1", "url": SITE.url}])
        self.browser.rotate("rot")
        self.assertEqual(self.browser.context("rot").cookies(), [])


if __name__ == "__main__":
    unittest.main()
