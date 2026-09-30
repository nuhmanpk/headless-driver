"""Tests for 1.1: status classification, impersonation, circuit breaker,
aggregate mode, new engines, normalisation and coloured logging."""

import io
import os
import sys
import json
import time
import logging
import pathlib
import threading
import unittest
import warnings
import contextlib
from unittest import mock

from headless import (
    AdvancedSearchScraper, SearchResponse, EngineAttempt, EngineHealth,
    AllEnginesBlocked, ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES,
    DEFAULT_AGGREGATE_ENGINES, merge_results, normalize_url, normalize_text,
    ColorFormatter, colorize_logging, enable_console_logging,
    disable_console_logging,
)
from headless import cli, logs
from headless.health import default_health
from headless.results import (
    STATUS_OK, STATUS_EMPTY, STATUS_BLOCKED, STATUS_RATE_LIMITED, STATUS_UNPARSED,
    STATUS_TIMEOUT, STATUS_UNREACHABLE, STATUS_ERROR, SKIP_COOLING,
    SKIP_IGNORES_SITE, SKIP_BROWSER_WITHDRAWN, SKIP_DUPLICATE_PROVIDER,
)
from headless.scraper import (
    unwrap_yahoo, tidy_url, android_gsa_user_agent, split_region,
)
from headless.transport import (
    Page, parse_html, parse_retry_after, impersonate_available, http_available,
    ImpersonateTransport, _family_of, _family_of_profile,
)
from headless.ui import Console, highlight, render_diag, component_colour, strip_ansi

try:
    from .support import HermeticTestCase
except ImportError:  # run as top-level modules by `discover -s tests`
    from support import HermeticTestCase

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
HAS_BS4 = http_available()[0]
HAS_CFFI = impersonate_available()[0]


def page_from(html: str, url: str = "https://example.com/search",
              status: int = 200, headers=None) -> Page:
    return Page(parse_html(html, url), url, status, headers or {}, "http")


class FakeTransport:
    """Serves pre-baked pages per engine and records every request."""

    name = "http"

    def __init__(self, pages):
        self.pages = pages          # engine -> Page | callable(request) -> Page | Exception
        self.requests = []
        self.rotated = []

    def fetch(self, url, key="", **kwargs):
        self.requests.append(dict(kwargs, url=url, key=key))
        page = self.pages[key]
        if isinstance(page, Exception):
            raise page
        return page(kwargs) if callable(page) else page

    def rotate(self, key="", profile=None):
        self.rotated.append(key)

    def close(self):
        pass


def scraper_with(pages, **kwargs) -> AdvancedSearchScraper:
    kwargs.setdefault("transport", "http")
    scr = AdvancedSearchScraper(**kwargs)
    transport = FakeTransport(pages)
    scr._transport_for = lambda engine: (transport, False)
    scr._fake = transport
    return scr


RESULTS = """<html><head><title>q - Brave Search</title></head><body>
<div data-type="web"><a href="https://www.linkedin.com/in/jane-doe"><div class="title">Jane Doe &amp; Co</div></a>
<div class="snippet"><div class="content">Partner at Credo\u200b Capital</div></div></div>
<div data-type="web"><a href="https://example.org/other"><div class="title">Other</div></a></div>
</body></html>"""

BRAVE_EMPTY = """<html><head><title>q - Brave Search</title></head><body>
<div id="search-elsewhere">Search elsewhere</div></body></html>"""

UNKNOWN = "<html><head><title>q - Brave Search</title></head><body><main>new layout</main></body></html>"


def fixture(name: str, engine: str, status: int = 200) -> Page:
    html = (FIXTURES / f"{name}_20260929.html").read_text(encoding="utf-8")
    return page_from(html, ENGINE_SPECS[engine]["url"], status)


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestStatusClassification(HermeticTestCase):
    """HTTP status codes decide before the body gets a say (doc §4.1)."""

    def _status(self, code, engine="mojeek", html="<p>refused</p>", headers=None):
        scr = scraper_with({engine: page_from(html, status=code, headers=headers)},
                           circuit_breaker=False)
        _, attempt = scr._search_one(engine, "q", 5)
        return attempt

    def test_refusal_codes_are_blocked_not_empty(self):
        for code in (401, 403, 407, 503):
            with self.subTest(code=code):
                attempt = self._status(code)
                self.assertEqual(attempt.status, STATUS_BLOCKED)
                self.assertEqual(attempt.http_status, code)
                self.assertTrue(attempt.blocked)
                self.assertIn(f"HTTP {code}", str(attempt))

    def test_429_is_rate_limited_and_carries_retry_after(self):
        attempt = self._status(429, headers={"Retry-After": "42"})
        self.assertEqual(attempt.status, STATUS_RATE_LIMITED)
        self.assertEqual(attempt.retry_after, 42.0)
        self.assertTrue(attempt.blocked)
        self.assertTrue(attempt.rate_limited)

    def test_duckduckgo_202_is_its_anomaly_page(self):
        self.assertEqual(self._status(202, engine="duckduckgo").status, STATUS_BLOCKED)
        # ...but 202 is not a refusal for engines that never use it that way.
        self.assertNotEqual(self._status(202, engine="brave").status, STATUS_BLOCKED)

    def test_other_client_and_server_errors_are_errors(self):
        for code in (400, 404, 500, 502):
            with self.subTest(code=code):
                self.assertEqual(self._status(code).status, STATUS_ERROR)

    def test_a_blocked_response_reports_blocked(self):
        scr = scraper_with({"mojeek": page_from("<p>no</p>", status=403)},
                           search_engine="mojeek", fallback=False, circuit_breaker=False)
        response = scr.search("site:linkedin.com/in x")
        self.assertTrue(response.blocked)
        self.assertEqual(response.attempts[0].http_status, 403)

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after("7"), 7.0)
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("soon"))
        self.assertGreaterEqual(parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT"), 0)
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestUnparsedAndSoftBlocks(HermeticTestCase):
    def _attempt(self, html, engine="brave", query="q"):
        scr = scraper_with({engine: page_from(html)}, circuit_breaker=False)
        return scr._search_one(engine, query, 5)

    def test_unrecognised_page_is_unparsed_not_empty(self):
        _, attempt = self._attempt(UNKNOWN)
        self.assertEqual(attempt.status, STATUS_UNPARSED)
        self.assertTrue(attempt.blocked)

    def test_recognised_no_results_page_is_empty(self):
        _, attempt = self._attempt(BRAVE_EMPTY)
        self.assertEqual(attempt.status, STATUS_EMPTY)
        self.assertFalse(attempt.blocked)

    def test_text_marker_counts_as_no_results(self):
        _, attempt = self._attempt(
            "<html><body><div>Not many great matches came back</div></body></html>")
        self.assertEqual(attempt.status, STATUS_EMPTY)

    def test_engine_without_markers_keeps_the_old_empty_behaviour(self):
        scr = scraper_with({"mine": page_from("<html><body>nothing</body></html>")},
                           circuit_breaker=False)
        scr.register_engine("mine", dict(ENGINE_SPECS["google"], url="https://x/?q={query}",
                                         js=False))
        _, attempt = scr._search_one("mine", "q", 5)
        self.assertEqual(attempt.status, STATUS_EMPTY)

    def test_captcha_title_is_a_block(self):
        _, attempt = self._attempt(
            "<html><head><title>Captcha</title></head><body>solve me</body></html>",
            engine="mojeek")
        self.assertEqual(attempt.status, STATUS_BLOCKED)

    def test_a_query_about_captchas_is_not_a_captcha(self):
        # The title of a genuine results page contains the query.
        html = RESULTS.replace("q - Brave Search", "captcha solver - Brave Search")
        scr = scraper_with({"brave": page_from(
            html, "https://search.brave.com/search?q=captcha+solver")},
            circuit_breaker=False)
        results, attempt = scr._search_one("brave", "captcha solver", 5)
        self.assertEqual(attempt.status, STATUS_OK)
        self.assertEqual(len(results), 2)

    def test_block_url_markers_ignore_the_query_string(self):
        scr = AdvancedSearchScraper(transport="http")
        self.assertEqual(scr._blocked_reason(page_from(
            "<p>x</p>", "https://search.brave.com/search?q=recaptcha")), "")
        self.assertIn("bot-check", scr._blocked_reason(page_from(
            "<p>x</p>", "https://www.google.com/sorry/index?continue=x")))


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestDatedFixtures(HermeticTestCase):
    """Live pages captured 2026-09-29, one per engine and outcome (doc §7.2)."""

    CASES = [
        ("brave_ok", "brave", 200, STATUS_OK),
        ("brave_captcha429", "brave", 429, STATUS_RATE_LIMITED),
        ("yahoo_ok", "yahoo", 200, STATUS_OK),
        ("yahoo_empty", "yahoo", 200, STATUS_EMPTY),
        ("duckduckgo_post_ok", "duckduckgo", 200, STATUS_OK),
        ("duckduckgo_403", "duckduckgo", 403, STATUS_BLOCKED),
        ("duckduckgo_lite_ok", "duckduckgo_lite", 200, STATUS_OK),
        ("mojeek_403", "mojeek", 403, STATUS_BLOCKED),
        ("mojeek_captcha", "mojeek", 200, STATUS_BLOCKED),
        ("google_basic_enablejs", "google_basic", 200, STATUS_BLOCKED),
    ]

    def test_every_fixture_is_classified_correctly(self):
        for name, engine, code, expected in self.CASES:
            with self.subTest(fixture=name):
                scr = scraper_with({engine: fixture(name, engine, code)},
                                   circuit_breaker=False)
                results, attempt = scr._search_one(engine, "python programming", 10)
                self.assertEqual(attempt.status, expected, attempt)
                if expected == STATUS_OK:
                    self.assertTrue(results)
                    for r in results:
                        self.assertTrue(r["url"].startswith("http"))
                        self.assertTrue(r["title"])
                        self.assertEqual(r["engine"], engine)

    def test_brave_extracts_titles_and_snippets(self):
        scr = scraper_with({"brave": fixture("brave_ok", "brave")}, circuit_breaker=False)
        results, _ = scr._search_one("brave", "python programming", 5)
        self.assertEqual(results[0]["url"], "https://www.python.org/")
        self.assertEqual(results[0]["title"], "Welcome to Python.org")
        self.assertTrue(all(r["snippet"] for r in results))

    def test_yahoo_redirects_are_unwrapped(self):
        scr = scraper_with({"yahoo": fixture("yahoo_ok", "yahoo")}, circuit_breaker=False)
        results, _ = scr._search_one("yahoo", 'site:linkedin.com/in "satya nadella"', 5)
        self.assertEqual(results[0]["url"], "https://www.linkedin.com/in/satyanadella")
        for r in results:
            self.assertNotIn("r.search.yahoo.com", r["url"])
            self.assertIn("linkedin.com/in/", r["url"])

    def test_duckduckgo_post_page_has_direct_links(self):
        scr = scraper_with({"duckduckgo": fixture("duckduckgo_post_ok", "duckduckgo")},
                           circuit_breaker=False)
        results, _ = scr._search_one("duckduckgo", "python programming", 10)
        self.assertEqual(results[0]["url"], "https://www.python.org/")
        self.assertFalse(any("duckduckgo.com/y.js" in r["url"] for r in results))


class TestRequestShapes(HermeticTestCase):
    """Each engine is asked the way its own front end asks (doc §5.4)."""

    def setUp(self):
        self.scr = AdvancedSearchScraper(transport="http", region="uk-en")

    def test_duckduckgo_is_a_post_form_with_region(self):
        req = self.scr._build_request("a b", "duckduckgo")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["data"], {"q": "a b", "b": "", "l": "uk-en"})
        self.assertIsNone(req["params"])

    def test_yahoo_path_tokens_are_fresh_every_request(self):
        a = self.scr._build_request("x", "yahoo")["url"]
        b = self.scr._build_request("x", "yahoo")["url"]
        self.assertIn(";_ylt=", a)
        self.assertIn(";_ylu=", a)
        self.assertNotEqual(a, b)
        self.assertEqual(self.scr._build_request("x", "yahoo")["params"], {"p": "x"})

    def test_google_basic_uses_the_search_app_client(self):
        req = self.scr._build_request("x", "google_basic")
        self.assertTrue(req["headers"]["User-Agent"].endswith("NSTNWV"))
        self.assertIn("Android", req["headers"]["User-Agent"])
        self.assertEqual(req["cookies"], {"CONSENT": "YES+"})
        self.assertEqual(req["params"]["hl"], "en-GB")
        self.assertEqual(req["params"]["cr"], "countryGB")
        self.assertEqual(ENGINE_SPECS["google_basic"]["impersonate"], "chrome_android")

    def test_brave_and_mojeek_cookies_follow_the_region(self):
        self.assertEqual(self.scr._build_request("x", "brave")["cookies"],
                         {"useLocation": "0", "country": "gb"})
        self.assertEqual(self.scr._build_request("x", "mojeek")["cookies"],
                         {"arc": "uk", "lb": "en"})
        plain = AdvancedSearchScraper(transport="http")
        self.assertIsNone(plain._build_request("x", "mojeek")["cookies"])
        self.assertEqual(plain._build_request("x", "duckduckgo")["data"]["l"], "wt-wt")

    def test_legacy_url_templates_still_work(self):
        req = self.scr._build_request("a&b c", "bing")
        self.assertEqual(req["url"], "https://www.bing.com/search?q=a%26b+c")
        self.assertIsNone(req["params"])

    def test_engine_url_is_one_get_for_the_browser(self):
        url = self.scr._engine_url("a b", "duckduckgo")
        self.assertTrue(url.startswith("https://html.duckduckgo.com/html/?"))
        self.assertIn("q=a+b", url)

    def test_regions(self):
        self.assertEqual(split_region("uk-en"), ("uk", "en"))
        self.assertEqual(split_region("wt-wt"), (None, None))
        self.assertEqual(split_region(None), (None, None))

    def test_android_user_agent(self):
        ua = android_gsa_user_agent()
        self.assertIn("Chrome/", ua)
        major = int(ua.split("Chrome/")[1].split(".")[0])
        self.assertTrue(39 <= major <= 60)


class TestEngineFacts(HermeticTestCase):
    def test_every_engine_declares_site_and_provider(self):
        for name, spec in ENGINE_SPECS.items():
            with self.subTest(engine=name):
                self.assertIn(spec.get("honors_site"), (True, False))
                self.assertTrue(spec.get("provider"))

    def test_bing_is_last_and_the_only_one_ignoring_site(self):
        self.assertEqual(DEFAULT_FALLBACK_ENGINES[-1], "bing")
        honoring = AdvancedSearchScraper().engines_honoring_site()
        self.assertNotIn("bing", honoring)
        self.assertLessEqual({"brave", "duckduckgo", "yahoo", "mojeek", "google_basic"},
                             honoring)

    def test_new_default_chain(self):
        self.assertEqual(DEFAULT_ENGINE, "brave")
        self.assertEqual(DEFAULT_FALLBACK_ENGINES[:4],
                         ["duckduckgo", "mojeek", "yahoo", "google_basic"])
        self.assertFalse(ENGINE_SPECS["mojeek"]["js"])

    def test_capabilities_report_the_new_facts(self):
        caps = AdvancedSearchScraper().capabilities("yahoo")
        self.assertEqual(caps["provider"], "bing")
        self.assertTrue(caps["honors_site"])
        self.assertEqual(AdvancedSearchScraper().capabilities("duckduckgo")["method"], "POST")

    def test_custom_engines_are_neither_trusted_nor_skipped(self):
        scr = AdvancedSearchScraper()
        scr.register_engine("mine", {"url": "https://x/?q={query}", "result": "li",
                                     "link": ["a"], "title": ["a"], "snippet": ["p"]})
        self.assertIsNone(scr.engines["mine"]["honors_site"])
        self.assertNotIn("mine", scr.engines_honoring_site())
        self.assertIsNone(scr._skip_reason("mine", "site:x.com y", explicit=False))

    def test_invalid_mode_and_transport_rejected(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(mode="fastest")
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(transport="carrier-pigeon")
        with self.assertRaises(ValueError):
            AdvancedSearchScraper().search("q", mode="nope")


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestStrictSite(HermeticTestCase):
    def test_site_queries_skip_engines_that_ignore_site(self):
        scr = scraper_with({"brave": page_from(BRAVE_EMPTY), "bing": page_from("")},
                           search_engine="brave", fallback_engines=["bing"])
        response = scr.search("site:linkedin.com/in jane")
        self.assertEqual(response.engines_tried, ["brave"])
        self.assertEqual(response.skipped[0]["engine"], "bing")
        self.assertEqual(response.skipped[0]["reason"], SKIP_IGNORES_SITE)

    def test_plain_queries_still_use_bing(self):
        scr = scraper_with({"brave": page_from(BRAVE_EMPTY),
                            "bing": page_from("<li class='b_algo'><h2><a href='https://a.com'>A</a></h2></li>")},
                           search_engine="brave", fallback_engines=["bing"])
        response = scr.search("jane doe")
        self.assertEqual(response.engine, "bing")

    def test_explicitly_chosen_engine_is_never_skipped(self):
        scr = scraper_with({"bing": page_from("<li class='b_no'>none</li>")},
                           search_engine="bing", fallback=False)
        response = scr.search("site:linkedin.com/in jane")
        self.assertEqual(response.engines_tried, ["bing"])

    def test_strict_site_can_be_turned_off(self):
        scr = scraper_with({"brave": page_from(BRAVE_EMPTY), "bing": page_from("")},
                           search_engine="brave", fallback_engines=["bing"],
                           strict_site=False)
        self.assertEqual(scr.search("site:x.com y").engines_tried, ["brave", "bing"])


class TestEngineHealth(HermeticTestCase):
    def setUp(self):
        self.now = [1000.0]
        self.health = EngineHealth(backoff_base=15, backoff_max=120,
                                   failures_before_backoff=3, clock=lambda: self.now[0])

    def refuse(self, engine="brave", status=STATUS_BLOCKED, **kw):
        return self.health.record(EngineAttempt(engine, status, **kw))

    def test_trips_after_n_consecutive_refusals(self):
        self.assertIsNone(self.refuse())
        self.assertIsNone(self.refuse())
        self.assertEqual(self.refuse(), "cooling")
        self.assertAlmostEqual(self.health.resume_in("brave"), 15)
        self.assertEqual(self.health.state("brave")["state"], "cooling")

    def test_a_real_answer_resets_the_count(self):
        self.refuse(); self.refuse()
        self.health.record(EngineAttempt("brave", STATUS_EMPTY))
        self.assertIsNone(self.refuse())
        self.assertFalse(self.health.is_cooling("brave"))

    def test_escalates_when_it_fails_again_right_after_resuming(self):
        for _ in range(3):
            self.refuse()
        self.now[0] += 15                  # resume
        self.assertEqual(self.health.state("brave")["state"], "probation")
        self.refuse()                      # one refusal re-trips at once
        self.assertAlmostEqual(self.health.resume_in("brave"), 30)
        self.now[0] += 30
        self.refuse()
        self.assertAlmostEqual(self.health.resume_in("brave"), 60)
        for _ in range(3):
            self.now[0] += self.health.resume_in("brave")
            self.refuse()
        self.assertAlmostEqual(self.health.resume_in("brave"), 120)   # capped

    def test_a_refusal_after_a_quiet_spell_starts_from_base(self):
        for _ in range(3):
            self.refuse()
        self.now[0] += 15 + 300
        self.refuse()
        self.assertAlmostEqual(self.health.resume_in("brave"), 15)

    def test_recovery_is_reported_once(self):
        for _ in range(3):
            self.refuse()
        self.now[0] += 20
        self.assertEqual(self.health.record(EngineAttempt("brave", STATUS_OK)), "ok")
        self.assertEqual(self.health.state("brave")["state"], "ok")
        self.assertIsNone(self.health.record(EngineAttempt("brave", STATUS_OK)))

    def test_429_stands_down_at_once_for_retry_after(self):
        self.assertEqual(self.refuse(status=STATUS_RATE_LIMITED, retry_after=90,
                                     http_status=429), "cooling")
        self.assertAlmostEqual(self.health.resume_in("brave"), 90)
        self.assertEqual(self.health.state("brave")["http_status"], 429)

    def test_network_failures_and_unparsed_neither_trip_nor_reset(self):
        self.refuse(); self.refuse()
        for status in (STATUS_TIMEOUT, STATUS_UNREACHABLE, STATUS_UNPARSED, STATUS_ERROR):
            self.assertIsNone(self.health.record(EngineAttempt("brave", status)))
        self.assertEqual(self.refuse(), "cooling")

    def test_refusals_during_cooldown_do_not_extend_it(self):
        for _ in range(3):
            self.refuse()
        self.now[0] += 5
        self.refuse()
        self.assertAlmostEqual(self.health.resume_in("brave"), 10)

    def test_trip_reset_snapshot(self):
        self.health.trip("yahoo", 40)
        self.assertAlmostEqual(self.health.resume_in("yahoo"), 40)
        self.assertIn("yahoo", self.health.snapshot())
        self.health.reset("yahoo")
        self.assertEqual(self.health.resume_in("yahoo"), 0)
        self.health.trip("yahoo")
        self.health.reset()
        self.assertEqual(self.health.snapshot(), {})
        self.assertEqual(self.health.resume_in("never-seen"), 0)

    def test_browser_withdrawal(self):
        self.assertTrue(self.health.browser_allowed())
        self.health.withdraw_browser(60)
        self.assertFalse(self.health.browser_allowed())
        self.now[0] += 61
        self.assertTrue(self.health.browser_allowed())
        self.health.withdraw_browser(60)
        self.health.restore_browser()
        self.assertTrue(self.health.browser_allowed())

    def test_invalid_threshold(self):
        with self.assertRaises(ValueError):
            EngineHealth(failures_before_backoff=0)

    def test_thread_safe(self):
        health = EngineHealth(failures_before_backoff=1000)

        def hammer():
            for _ in range(200):
                health.record(EngineAttempt("e", STATUS_BLOCKED))
        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Exactly 1000 of the 1600 refusals counted, tripping it once: none lost
        # to a race, none double-counted, and the rest ignored while cooling.
        self.assertTrue(health.is_cooling("e"))
        self.assertEqual(health.state("e")["failures"], 0)


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestCircuitBreakerInSearch(HermeticTestCase):
    def test_cooling_engines_are_skipped_and_reported(self):
        scr = scraper_with({"brave": page_from("<p>x</p>", status=403)},
                           search_engine="brave", fallback=False,
                           health=EngineHealth(failures_before_backoff=1))
        first = scr.search("q")
        self.assertTrue(first.blocked)
        second = scr.search("q")
        self.assertEqual(second.attempts, [])
        self.assertFalse(second.blocked)
        self.assertTrue(second.cooling)
        self.assertEqual(second.skipped[0]["reason"], SKIP_COOLING)
        self.assertEqual(len(scr._fake.requests), 1)     # nothing sent the second time
        self.assertEqual(scr.health()["brave"]["state"], "cooling")
        scr.reset_health()
        self.assertEqual(scr.health()["brave"]["state"], "ok")

    def test_raise_on_block_covers_cooling(self):
        scr = scraper_with({"brave": page_from("<p>x</p>", status=429)},
                           search_engine="brave", fallback=False, raise_on_block=True,
                           health=EngineHealth())
        with self.assertRaises(AllEnginesBlocked):
            scr.search("q")
        with self.assertRaises(AllEnginesBlocked) as ctx:
            scr.search("q")
        self.assertIn("cooling", str(ctx.exception))

    def test_circuit_breaker_can_be_disabled(self):
        scr = scraper_with({"brave": page_from("<p>x</p>", status=429)},
                           search_engine="brave", fallback=False, circuit_breaker=False)
        for _ in range(3):
            self.assertTrue(scr.search("q").blocked)
        self.assertEqual(len(scr._fake.requests), 3)
        self.assertFalse(default_health().is_cooling("brave"))

    def test_default_breaker_is_shared_across_scrapers(self):
        a = AdvancedSearchScraper()
        b = AdvancedSearchScraper()
        self.assertIs(a.engine_health, b.engine_health)
        self.assertIsNot(AdvancedSearchScraper(circuit_breaker=False).engine_health,
                         a.engine_health)

    def test_refusal_rotates_the_fingerprint(self):
        scr = scraper_with({"brave": page_from("<p>x</p>", status=403),
                            "duckduckgo": page_from(RESULTS)},
                           search_engine="brave", fallback_engines=["duckduckgo"])
        imp = mock.Mock()
        scr._impersonate = imp
        attempt = EngineAttempt("brave", STATUS_BLOCKED, transport="impersonate")
        scr._settle(attempt, set())
        imp.rotate.assert_called_once_with("brave", None)

    def test_two_independent_refusals_withdraw_the_browser(self):
        health = EngineHealth()
        scr = scraper_with({"brave": page_from("<p/>", status=403),
                            "mojeek": page_from("<p/>", status=403),
                            "duckduckgo_js": page_from(RESULTS)},
                           search_engine="brave",
                           fallback_engines=["mojeek", "duckduckgo_js"],
                           transport="auto", health=health)
        response = scr.search("q")
        self.assertFalse(health.browser_allowed())
        reasons = {s["engine"]: s["reason"] for s in response.skipped}
        self.assertEqual(reasons.get("duckduckgo_js"), SKIP_BROWSER_WITHDRAWN)

    def test_refusals_from_one_index_do_not_withdraw_the_browser(self):
        health = EngineHealth()
        scr = scraper_with({"brave": page_from("<p/>", status=403),
                            "duckduckgo": page_from("<p/>", status=403)},
                           search_engine="brave", fallback_engines=["duckduckgo"],
                           health=health)
        scr.search("q")       # brave and duckduckgo: two providers, but ...
        self.assertFalse(health.browser_allowed())
        health2 = EngineHealth()
        scr2 = scraper_with({"duckduckgo": page_from("<p/>", status=403),
                             "yahoo": page_from("<p/>", status=403)},
                            search_engine="duckduckgo", fallback_engines=["yahoo"],
                            health=health2)
        scr2.search("q")      # ... duckduckgo and yahoo are one index (Bing)
        self.assertTrue(health2.browser_allowed())


WIKI = RESULTS.replace("https://www.linkedin.com/in/jane-doe",
                       "https://en.wikipedia.org/wiki/Python")


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestVerifyEmptyAndProbe(HermeticTestCase):
    def _scraper(self, main, control, **kw):
        def page(req):
            q = (req.get("params") or req.get("data") or {}).get("q", "")
            return control if q == "site:wikipedia.org python" else main
        return scraper_with({"brave": page}, search_engine="brave", fallback=False,
                            verify_empty=True, **kw)

    def test_empty_with_a_dead_control_query_is_a_soft_block(self):
        scr = self._scraper(page_from(BRAVE_EMPTY), page_from(BRAVE_EMPTY))
        response = scr.search("site:linkedin.com/in nobody")
        self.assertEqual(response.attempts[0].status, STATUS_BLOCKED)
        self.assertIn("soft block", response.attempts[0].reason)

    def test_empty_with_a_live_control_query_is_real(self):
        scr = self._scraper(page_from(BRAVE_EMPTY), page_from(WIKI))
        response = scr.search("site:linkedin.com/in nobody")
        self.assertEqual(response.attempts[0].status, STATUS_EMPTY)
        # The probe result is cached, so a second empty costs one request.
        before = len(scr._fake.requests)
        scr.search("site:linkedin.com/in nobody else")
        self.assertEqual(len(scr._fake.requests), before + 1)

    def test_probe(self):
        scr = self._scraper(page_from(BRAVE_EMPTY), page_from(WIKI))
        self.assertEqual(scr.probe("brave").status, STATUS_OK)
        scr2 = self._scraper(page_from(BRAVE_EMPTY), page_from(BRAVE_EMPTY))
        self.assertEqual(scr2.probe().status, STATUS_EMPTY)


@unittest.skipUnless(HAS_BS4, "needs beautifulsoup4")
class TestAggregate(HermeticTestCase):
    def _results(self, *urls):
        items = "".join(f'<div data-type="web"><a href="{u}"><div class="title">T {u}</div></a>'
                        f'<div class="snippet"><div class="content">about {u}</div></div></div>'
                        for u in urls)
        return f"<html><body>{items}</body></html>"

    def _yahoo(self, *urls):
        items = "".join(f"<div class='relsrch'><div class='compTitle'><h3><a href='{u}'>"
                        f"Y {u}</a></h3></div><div class='compText'>longer yahoo snippet "
                        f"for {u}</div></div>" for u in urls)
        return f"<html><body>{items}</body></html>"

    def _ddg(self, *urls):
        items = "".join(f"<div class='result'><a class='result__a' href='{u}'>D {u}</a></div>"
                        for u in urls)
        return f"<html><body>{items}</body></html>"

    def test_results_are_ranked_by_agreement(self):
        scr = scraper_with({
            "brave": page_from(self._results("https://a.com/", "https://b.com/")),
            "duckduckgo": page_from(self._ddg("https://www.b.com", "https://c.com")),
            "mojeek": page_from("<ul class='results-standard'><li><h2><a href='https://b.com'>M</a></h2></li></ul>"),
        }, circuit_breaker=False)
        response = scr.search("q", mode="aggregate",
                              engines=["brave", "duckduckgo", "mojeek"], min_engines=3)
        self.assertEqual(response.mode, "aggregate")
        self.assertEqual(response.engine, "aggregate")
        self.assertEqual(sorted(response.engines), ["brave", "duckduckgo", "mojeek"])
        top = response[0]
        self.assertEqual(normalize_url(top["url"]), "b.com")
        self.assertEqual(top["votes"], 3)
        self.assertEqual(set(top["engines"]), {"brave", "duckduckgo", "mojeek"})
        self.assertEqual(top["ranks"]["brave"], 2)
        self.assertEqual([r["votes"] for r in response], sorted(
            [r["votes"] for r in response], reverse=True))

    def test_one_engine_per_provider_with_sibling_fallback(self):
        scr = scraper_with({
            "brave": page_from(self._results("https://a.com")),
            "duckduckgo": page_from("<p/>", status=403),
            "yahoo": page_from(self._yahoo("https://a.com")),
        }, circuit_breaker=False)
        response = scr.search("q", mode="aggregate",
                              engines=["brave", "duckduckgo", "yahoo"])
        tried = response.engines_tried
        self.assertIn("duckduckgo", tried)
        self.assertIn("yahoo", tried)        # stood in for its blocked sibling
        self.assertEqual(response[0]["votes"], 2)
        # the longest snippet any engine offered is kept
        self.assertIn("longer yahoo snippet", response[0]["snippet"])

    def test_sibling_is_not_asked_when_the_first_answers(self):
        scr = scraper_with({
            "duckduckgo": page_from(self._ddg("https://a.com")),
            "yahoo": page_from(self._yahoo("https://a.com")),
        }, circuit_breaker=False)
        response = scr.search("q", mode="aggregate", engines=["duckduckgo", "yahoo"])
        self.assertEqual(response.engines_tried, ["duckduckgo"])
        self.assertEqual(response.skipped[0]["reason"], SKIP_DUPLICATE_PROVIDER)

    def test_deadline_turns_slow_engines_into_timeouts(self):
        def slow(req):
            time.sleep(1.5)
            return page_from(self._results("https://slow.com"))
        scr = scraper_with({"brave": slow,
                            "mojeek": page_from("<ul class='results-standard'><li><h2>"
                                                "<a href='https://fast.com'>F</a></h2></li></ul>")},
                           circuit_breaker=False)
        started = time.time()
        response = scr.search("q", mode="aggregate", engines=["brave", "mojeek"],
                              deadline=0.5)
        self.assertLess(time.time() - started, 1.4)
        statuses = {a.engine: a.status for a in response.attempts}
        self.assertEqual(statuses["brave"], STATUS_TIMEOUT)
        self.assertEqual(statuses["mojeek"], STATUS_OK)
        self.assertEqual(response[0]["url"], "https://fast.com")

    def test_browser_engines_are_left_out(self):
        scr = scraper_with({"brave": page_from(self._results("https://a.com"))},
                           circuit_breaker=False)
        response = scr.search("q", mode="aggregate", engines=["brave", "google"])
        self.assertEqual(response.skipped[0], {"engine": "google", "reason": "needs_browser",
                                               "resume_in": 0})

    def test_needs_a_browserless_transport(self):
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(transport="browser").search("q", mode="aggregate")

    def test_mode_can_be_the_default(self):
        scr = scraper_with({"brave": page_from(self._results("https://a.com")),
                            "mojeek": page_from("<p/>", status=403)},
                           mode="aggregate", aggregate_engines=["brave", "mojeek"],
                           circuit_breaker=False, max_results=1)
        response = scr.search("q")
        self.assertEqual(response.mode, "aggregate")
        self.assertEqual(len(response), 1)
        self.assertEqual(response.as_dict()["mode"], "aggregate")

    def test_all_refused_is_blocked(self):
        scr = scraper_with({"brave": page_from("<p/>", status=403),
                            "mojeek": page_from("<p/>", status=429)}, circuit_breaker=False)
        response = scr.search("q", mode="aggregate", engines=["brave", "mojeek"])
        self.assertTrue(response.blocked)
        self.assertEqual(response, [])
        self.assertIsNone(response.engine)

    def test_zero_limit(self):
        scr = AdvancedSearchScraper(transport="http")
        self.assertEqual(scr.search("q", max_results=0, mode="aggregate"), [])

    def test_export_flattens_aggregate_fields(self):
        import tempfile, csv
        merged = merge_results({"a": [{"url": "https://x.com", "title": "X"}],
                                "b": [{"url": "https://x.com/", "title": "X"}]}, ["a", "b"])
        path = os.path.join(tempfile.mkdtemp(), "r.csv")
        self.assertTrue(AdvancedSearchScraper(transport="http").export(path, merged))
        row = next(csv.DictReader(open(path, encoding="utf-8")))
        self.assertEqual(row["engines"], "a b")
        self.assertEqual(json.loads(row["ranks"]), {"a": 1, "b": 1})


class TestMergeAndNormalise(HermeticTestCase):
    def test_normalize_url(self):
        same = ["https://www.linkedin.com/in/jane/", "http://uk.linkedin.com/in/jane",
                "https://linkedin.com/in/jane?trk=abc&utm_source=x",
                "https://m.linkedin.com/in/jane#about",
                "https://www.linkedin.com:443/in/jane?originalSubdomain=uk"]
        self.assertEqual({normalize_url(u) for u in same}, {"linkedin.com/in/jane"})
        # Language subdomains elsewhere are different pages.
        self.assertNotEqual(normalize_url("https://en.wikipedia.org/wiki/X"),
                            normalize_url("https://de.wikipedia.org/wiki/X"))
        self.assertEqual(normalize_url("https://a.com/p?b=2&a=1"), "a.com/p?a=1&b=2")

    def test_merge_breaks_ties_by_mean_rank_then_engine_order(self):
        merged = merge_results({
            "b": [{"url": "https://2.com"}, {"url": "https://1.com"}],
            "a": [{"url": "https://1.com"}, {"url": "https://2.com"}],
        }, ["a", "b"])
        self.assertEqual([m["votes"] for m in merged], [2, 2])
        self.assertEqual(merged[0]["url"], "https://1.com")
        self.assertEqual(merged[0]["engines"], ["a", "b"])

    def test_merge_keeps_best_ranked_copy(self):
        merged = merge_results({
            "a": [{"url": "https://z.com"}, {"url": "https://x.com", "title": "late"}],
            "b": [{"url": "https://x.com/", "title": "early", "snippet": ""}],
        }, ["a", "b"])
        x = next(m for m in merged if normalize_url(m["url"]) == "x.com")
        self.assertEqual(x["title"], "early")
        self.assertEqual(x["ranks"], {"a": 2, "b": 1})

    def test_merge_skips_missing_urls_and_duplicates_within_one_engine(self):
        merged = merge_results({"a": [{"url": ""}, {"url": "https://x.com"},
                                      {"url": "https://x.com/"}]}, ["a"])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["votes"], 1)

    def test_custom_normaliser(self):
        merged = merge_results({"a": [{"url": "https://A.com"}], "b": [{"url": "https://B.com"}]},
                               ["a", "b"], normalize=lambda u: "same")
        self.assertEqual(len(merged), 1)

    def test_normalize_text(self):
        self.assertEqual(normalize_text("Jane&amp;Doe\u200b  \n Ltd"), "Jane&Doe Ltd")
        self.assertEqual(normalize_text("Cafe\u0301"), "Café")
        self.assertEqual(normalize_text(""), "")

    def test_tidy_url_decodes_only_when_lossless(self):
        self.assertEqual(tidy_url("https://x.com/in/Jos%C3%A9"), "https://x.com/in/José")
        self.assertEqual(tidy_url("https://x.com/a%20b"), "https://x.com/a%20b")
        self.assertEqual(tidy_url("https://x.com/a%2Fb%3Fc"), "https://x.com/a%2Fb%3Fc")
        self.assertEqual(tidy_url("https://x.com/plain"), "https://x.com/plain")

    def test_unwrap_yahoo(self):
        href = ("https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/RE=1/RO=10/"
                "RU=https%3a%2f%2fwww.linkedin.com%2fin%2fjane/RK=2/RS=xyz-")
        self.assertEqual(unwrap_yahoo(href), "https://www.linkedin.com/in/jane")
        self.assertEqual(unwrap_yahoo("https://plain.com"), "https://plain.com")
        self.assertEqual(AdvancedSearchScraper._unwrap_redirect(href),
                         "https://www.linkedin.com/in/jane")


@unittest.skipUnless(HAS_CFFI, "needs curl_cffi")
class TestImpersonateTransport(HermeticTestCase):
    def test_sessions_are_per_engine_and_rotate_to_a_different_browser(self):
        t = ImpersonateTransport(profiles=["chrome", "safari", "firefox"])
        try:
            a = t.profile_for("brave")
            self.assertIs(t.session_for("brave")[0], t.session_for("brave")[0])
            self.assertIsNot(t.session_for("brave")[0], t.session_for("yahoo")[0])
            rotated = t.rotate("brave")
            self.assertNotEqual(rotated, a)
            self.assertEqual(t.profile_for("brave"), rotated)
        finally:
            t.close()

    def test_pinned_profile(self):
        t = ImpersonateTransport()
        try:
            self.assertEqual(t.profile_for("google_basic", "chrome_android"), "chrome_android")
            self.assertEqual(t.rotate("google_basic", "chrome_android"), "chrome_android")
        finally:
            t.close()

    def test_no_user_agent_override_by_default(self):
        t = ImpersonateTransport(profiles=["chrome"])
        try:
            self.assertNotIn("User-Agent", dict(t.session_for("x")[0].headers))
        finally:
            t.close()

    def test_explicit_user_agent_narrows_to_its_browser_family(self):
        ua = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"
        t = ImpersonateTransport(user_agent=ua)
        try:
            self.assertEqual({_family_of_profile(p) for p in t.profiles}, {"firefox"})
            self.assertEqual(t.session_for("x")[0].headers["User-Agent"], ua)
        finally:
            t.close()

    def test_family_detection(self):
        self.assertEqual(_family_of("Mozilla/5.0 ... Chrome/140.0 Safari/537.36 Edg/140"), "edge")
        self.assertEqual(_family_of("Mozilla/5.0 ... Chrome/140.0 Safari/537.36"), "chrome")
        self.assertEqual(_family_of("Mozilla/5.0 ... Version/18 Safari/605.1.15"), "safari")
        self.assertEqual(_family_of("curl/8"), "")
        self.assertEqual(_family_of_profile("chrome_android"), "chrome")

    def test_unsupported_profile_is_dropped(self):
        t = ImpersonateTransport(profiles=["bogus", "chrome"])
        calls = []

        class FakeSession:
            def __init__(self, profile):
                self.profile = profile
                self.headers = {}

            def request(self, *a, **k):
                calls.append(self.profile)
                if self.profile == "bogus":
                    raise ValueError("Impersonating bogus is not supported")
                return mock.Mock(url="https://x/", text="<p>ok</p>", status_code=200,
                                 headers={"Retry-After": "3"})

            def close(self):
                pass

        t._new_session = FakeSession
        t._pick = lambda avoid="": t.profiles[0]
        page = t.fetch("https://x/", key="k")
        self.assertEqual(page.status, 200)
        self.assertEqual(page.retry_after, 3.0)
        self.assertEqual(page.transport, "impersonate")
        self.assertEqual(t.profiles, ["chrome"])
        self.assertEqual(calls, ["bogus", "chrome"])

    def test_auto_prefers_impersonation(self):
        scr = AdvancedSearchScraper()
        self.assertEqual(scr.browserless_transport_name(), "impersonate")
        transport, is_browser = scr._transport_for("brave")
        self.assertFalse(is_browser)
        self.assertIsInstance(transport, ImpersonateTransport)
        scr.quit()
        self.assertIsNone(scr._impersonate)

    def test_explicit_http_uses_requests(self):
        scr = AdvancedSearchScraper(transport="http")
        self.assertEqual(scr._transport_for("brave")[0].name, "http")
        scr.quit()


class TestTransportSelection(HermeticTestCase):
    def test_auto_falls_back_to_http_then_browser(self):
        scr = AdvancedSearchScraper()
        with mock.patch("headless.scraper.impersonate_available", return_value=(False, "x")):
            self.assertEqual(scr.browserless_transport_name(),
                             "http" if HAS_BS4 else None)
            with mock.patch("headless.scraper.http_available", return_value=(False, "x")):
                self.assertIsNone(scr.browserless_transport_name())
        self.assertIsNone(AdvancedSearchScraper(transport="browser").browserless_transport_name())

    def test_missing_impersonate_extra_is_an_error_attempt(self):
        scr = AdvancedSearchScraper(transport="impersonate")
        with mock.patch("headless.scraper.impersonate_available",
                        return_value=(False, "curl_cffi is not installed")):
            _, attempt = scr._search_one("brave", "q", 3)
        self.assertEqual(attempt.status, STATUS_ERROR)
        self.assertIn("impersonate", attempt.reason)

    def test_browserless_transports_drop_browser_engines(self):
        order = AdvancedSearchScraper(transport="impersonate")._engine_order()
        self.assertNotIn("google", order)
        self.assertIn("google_basic", order)

    def test_http_timeout_defaults(self):
        self.assertEqual(AdvancedSearchScraper().http_timeout, 8.0)
        self.assertEqual(AdvancedSearchScraper(page_load_timeout=5).http_timeout, 5)
        self.assertEqual(AdvancedSearchScraper(http_timeout=3).http_timeout, 3)

    def test_unreachable_and_timeout(self):
        class Timeout(Exception):
            pass
        scr = scraper_with({"brave": Timeout("slow"), "mojeek": OSError("dns")},
                           circuit_breaker=False)
        self.assertEqual(scr._search_one("brave", "q", 1)[1].status, STATUS_TIMEOUT)
        self.assertEqual(scr._search_one("mojeek", "q", 1)[1].status, STATUS_UNREACHABLE)


class TestResponseModel(HermeticTestCase):
    def test_new_fields_serialise(self):
        a = EngineAttempt("brave", STATUS_RATE_LIMITED, http_status=429, retry_after=5,
                          transport="impersonate")
        r = SearchResponse(query="q", attempts=[a],
                           skipped=[{"engine": "x", "reason": SKIP_COOLING, "resume_in": 3}])
        d = r.as_dict()
        self.assertEqual(d["attempts"][0]["http_status"], 429)
        self.assertEqual(d["attempts"][0]["transport"], "impersonate")
        self.assertTrue(d["blocked"])
        self.assertFalse(d["cooling"])        # somebody was asked, and refused
        self.assertTrue(r.rate_limited)
        self.assertEqual(r.retry_after, 5)
        self.assertIn("cooling=False", repr(r))
        json.dumps(d)

    def test_cooling_only_when_nothing_answered(self):
        skipped = [{"engine": "x", "reason": SKIP_COOLING, "resume_in": 3}]
        self.assertTrue(SearchResponse(query="q", skipped=skipped).cooling)
        answered = SearchResponse(query="q", skipped=skipped,
                                  attempts=[EngineAttempt("y", STATUS_EMPTY)])
        self.assertFalse(answered.cooling)
        self.assertFalse(SearchResponse(query="q").cooling)

    def test_str(self):
        self.assertEqual(str(EngineAttempt("m", STATUS_BLOCKED, http_status=403)),
                         "m: blocked (HTTP 403)")
        self.assertEqual(str(EngineAttempt("m", STATUS_BLOCKED, reason="captcha",
                                           http_status=200)), "m: blocked (captcha)")
        self.assertEqual(str(EngineAttempt("m", STATUS_OK)), "m: ok")


class TestColourfulLogging(HermeticTestCase):
    def tearDown(self):
        disable_console_logging()
        logging.getLogger("headless").setLevel(logging.NOTSET)

    def _record(self, level=logging.WARNING, msg="brave cooling down for 15s (HTTP 429)",
                name="headless.health"):
        return logging.LogRecord(name, level, __file__, 1, msg, None, None)

    def test_formatter_colours_level_component_and_highlights(self):
        out = ColorFormatter(color=True, timestamps=False).format(self._record())
        self.assertIn("\033[30;43m", out)            # WARN badge
        self.assertIn("[health]", out)
        self.assertIn("\033[91;1mHTTP 429", out)     # red status code
        self.assertIn("\033[93;1mcooling down", out)
        self.assertIn("\033[1m15s", out)

    def test_formatter_is_plain_without_colour(self):
        out = ColorFormatter(color=False, timestamps=True).format(
            self._record(logging.ERROR, "boom"))
        self.assertNotIn("\033[", out)
        self.assertRegex(out, r"^\d\d:\d\d:\d\d [x✗] \[health\] boom$")

    def test_formatter_appends_tracebacks(self):
        try:
            raise RuntimeError("kaput")
        except RuntimeError:
            record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed", None,
                                       sys.exc_info())
        out = ColorFormatter(color=False, timestamps=False).format(record)
        self.assertIn("RuntimeError: kaput", out)
        self.assertIn("[app]", out)

    def test_every_level_renders_differently(self):
        con = Console(stream=io.StringIO(), color=True)
        lines = {lvl: render_diag("x", lvl, con) for lvl in
                 ("debug", "info", "success", "warn", "error")}
        self.assertEqual(len(set(lines.values())), 5)

    def test_highlight_restores_the_base_colour(self):
        con = Console(stream=io.StringIO(), color=True)
        out = highlight(con, "engine blocked again", ("yellow",))
        self.assertEqual(strip_ansi(out), "engine blocked again")
        self.assertTrue(out.endswith("\033[33m again\033[0m"))
        self.assertEqual(highlight(Console(stream=io.StringIO(), color=False), "a", ()), "a")

    def test_component_colours_are_stable(self):
        self.assertEqual(component_colour("scraper"), component_colour("Scraper"))
        self.assertEqual(component_colour("somethingelse"), component_colour("somethingelse"))

    def test_console_handler_timestamps_and_stderr(self):
        enable_console_logging(logging.INFO, timestamps=True)
        err = io.StringIO()
        with mock.patch("sys.stderr", err), mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            logs.get_logger("scraper").info("hello")
        self.assertRegex(err.getvalue(), r"^\d\d:\d\d:\d\d \[scraper\] hello")

    def test_third_party_loggers_are_rendered_and_restored(self):
        wdm = logging.getLogger("WDM")
        before = (wdm.level, wdm.propagate, list(wdm.handlers))
        enable_console_logging(logging.WARNING, third_party=True)
        self.assertFalse(wdm.propagate)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            wdm.warning("driver downloaded")
        self.assertIn("[WDM] driver downloaded", err.getvalue())
        disable_console_logging()
        self.assertEqual((wdm.level, wdm.propagate, list(wdm.handlers)), before)

    def test_python_warnings_are_captured(self):
        enable_console_logging(logging.WARNING, capture_warnings=True)
        err = io.StringIO()
        with mock.patch("sys.stderr", err), warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("deprecated thing", DeprecationWarning)
        self.assertIn("deprecated thing", err.getvalue())
        self.assertIn("[warning]", err.getvalue())

    def test_colorize_logging_only_touches_terminals(self):
        root = logging.Logger("isolated")
        tty = io.StringIO()
        tty.isatty = lambda: True
        term = logging.StreamHandler(tty)
        pipe = logging.StreamHandler(io.StringIO())
        root.addHandler(term)
        root.addHandler(pipe)
        root.addHandler(logging.NullHandler())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            changed = colorize_logging(root)
        self.assertEqual(changed, [term])
        self.assertIsInstance(term.formatter, ColorFormatter)
        self.assertNotIsInstance(pipe.formatter, ColorFormatter)

    def test_env_opt_in(self):
        self.assertIsNone(logs.configure_from_env({}))
        self.assertIsNone(logs.configure_from_env({"HEADLESS_DRIVER_LOG": "off"}))
        handler = logs.configure_from_env({"HEADLESS_DRIVER_LOG": "warning"})
        self.assertEqual(handler.level, logging.WARNING)
        self.assertEqual(logs._level_from_env("1"), logging.INFO)
        self.assertEqual(logs._level_from_env("warn"), logging.WARNING)
        self.assertIsNone(logs._level_from_env("loud"))


class TestCli11(HermeticTestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_engines_json_reports_site_and_provider(self):
        code, out = self._run(["engines", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["default"], "brave")
        self.assertEqual(payload["aggregate"], DEFAULT_AGGREGATE_ENGINES)
        self.assertFalse(payload["engines"]["bing"]["honors_site"])
        self.assertEqual(payload["engines"]["duckduckgo"]["method"], "POST")

    def test_engines_table_has_the_new_columns(self):
        code, out = self._run(["engines"])
        self.assertIn("site:", out)
        self.assertIn("index", out)
        self.assertIn("aggregate mode asks", out)

    def test_search_accepts_the_new_options(self):
        args = cli.build_parser().parse_args(
            ["search", "q", "--mode", "aggregate", "--region", "uk-en",
             "--transport", "impersonate", "--deadline", "3", "--engines", "brave,yahoo"])
        self.assertEqual(args.mode, "aggregate")
        self.assertEqual(args.transport, "impersonate")

    def test_aggregate_search_output_shows_votes(self):
        class Fake:
            aggregate_engines = ["brave", "yahoo"]

            def __init__(self, **kw):
                self.kw = kw

            def search(self, q):
                return SearchResponse(query=q, mode="aggregate", engine="aggregate",
                                      engines=["brave", "yahoo"], results=[
                                          {"url": "https://a.com", "title": "A", "snippet": "",
                                           "votes": 2, "engines": ["brave", "yahoo"]}],
                                      attempts=[EngineAttempt("brave", STATUS_OK),
                                                EngineAttempt("yahoo", STATUS_OK)],
                                      skipped=[{"engine": "duckduckgo", "resume_in": 0,
                                                "reason": SKIP_DUPLICATE_PROVIDER}])

            def quit(self):
                pass
        with mock.patch("headless.cli.AdvancedSearchScraper", Fake):
            code, out = self._run(["search", "q", "--mode", "aggregate"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("[2 engines: brave, yahoo]", out)
        self.assertIn("via brave, yahoo", out)
        self.assertIn("not asked duckduckgo", out)

    def test_cooling_search_says_so(self):
        class Fake:
            def __init__(self, **kw):
                pass

            def search(self, q):
                return SearchResponse(query=q, skipped=[
                    {"engine": "brave", "reason": SKIP_COOLING, "resume_in": 12}])

            def quit(self):
                pass
        with mock.patch("headless.cli.AdvancedSearchScraper", Fake):
            code, out = self._run(["search", "q"])
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("cooling down", out)
        self.assertIn("brave: cooling (12s)", out)

    def test_bench_command(self):
        from headless import bench
        cells = [bench.Cell("brave", "impersonate", ok=3, empty=1, site_rows=20,
                            times_ms=[100, 300, 200]),
                 bench.Cell("bing", "impersonate", blocked=4)]
        with mock.patch("headless.bench.run_bench", return_value=cells):
            code, out = self._run(["bench", "--json", "--transports", "impersonate",
                                   "--min-ok-rate", "0.5", "--limit", "2"])
        payload = json.loads(out)
        self.assertEqual(code, 1)                      # bing is below threshold
        self.assertEqual(payload["failing"], ["bing/impersonate"])
        self.assertEqual(payload["cells"][0]["p50_ms"], 200)
        self.assertEqual(payload["cells"][0]["ok_rate"], 0.75)
        with mock.patch("headless.bench.run_bench", return_value=cells[:1]):
            code, out = self._run(["bench", "--transports", "impersonate"])
        self.assertEqual(code, 0)
        self.assertIn("brave", out)
        self.assertIn("p50 ms", out)

    def test_run_bench_counts_statuses(self):
        from headless import bench
        responses = iter([
            SearchResponse("q", results=[{"url": "x"}], attempts=[EngineAttempt("e", STATUS_OK)]),
            SearchResponse("q", attempts=[EngineAttempt("e", STATUS_EMPTY)]),
            SearchResponse("q", attempts=[EngineAttempt("e", STATUS_RATE_LIMITED)]),
            SearchResponse("q", attempts=[EngineAttempt("e", STATUS_UNPARSED)]),
            SearchResponse("q", attempts=[EngineAttempt("e", STATUS_TIMEOUT)]),
        ])
        fake = mock.Mock()
        fake.search.side_effect = lambda q: next(responses)
        with mock.patch("headless.scraper.AdvancedSearchScraper", return_value=fake):
            (cell,) = bench.run_bench(["e"], ["http"], ["a", "b", "c", "d", "e"], pause=0)
        self.assertEqual((cell.ok, cell.empty, cell.blocked, cell.unparsed, cell.other),
                         (1, 1, 1, 1, 1))
        self.assertEqual(cell.site_rows, 1)
        fake.quit.assert_called_once()

    def test_doctor_reports_transports(self):
        with mock.patch("headless.cli.ExtendedHeadless") as eh, \
                mock.patch("headless.cli._reachable", return_value=True), \
                mock.patch("headless.cli.impersonate_available", return_value=(False, "nope")):
            eh.return_value.get_driver.return_value.title = "ok"
            code, out = self._run(["doctor"])
        self.assertIn("impersonate transport", out)
        if HAS_BS4:
            self.assertIn("TLS fingerprint will not match", out)


class TestPlaywrightHelpers(HermeticTestCase):
    def test_proxy_credentials_are_split_out(self):
        from headless.playwright_driver import proxy_settings
        self.assertIsNone(proxy_settings(None))
        self.assertEqual(proxy_settings("http://u%40x:p%3Aw@proxy.io:8080"),
                         {"server": "http://proxy.io:8080", "username": "u@x", "password": "p:w"})
        self.assertEqual(proxy_settings("socks5://h:1080"), {"server": "socks5://h:1080"})
        self.assertEqual(proxy_settings("h:3128"), {"server": "http://h:3128"})

    def test_selector_split(self):
        from headless.playwright_driver import _split_selector
        self.assertEqual(_split_selector("a.title@href"), ["a.title", "href"])
        self.assertEqual(_split_selector("h2"), ["h2", ""])
        self.assertEqual(_split_selector("@data-id"), ["", "data-id"])

    def test_headful_user_agent(self):
        from headless.playwright_driver import headful_user_agent
        ua = headful_user_agent("153.0.1.2")
        self.assertIn("Chrome/153.0.1.2", ua)
        self.assertNotIn("Headless", ua)

    def test_extracted_text_is_normalised(self):
        from headless.playwright_driver import _normalize
        self.assertEqual(_normalize([{"a": "x\u200b y", "n": 3}]), [{"a": "x y", "n": 3}])

    def test_missing_extra_is_a_clear_error(self):
        from headless import playwright_driver as pd
        with mock.patch.object(pd, "playwright_available", return_value=(False, "nope")):
            with self.assertRaises(RuntimeError) as ctx:
                pd.PlaywrightBrowser()
        self.assertIn("playwright install chromium", str(ctx.exception))
        with mock.patch.dict("sys.modules", {"playwright.sync_api": None}):
            self.assertFalse(pd.playwright_available()[0])

    def test_validation(self):
        from headless import playwright_driver as pd
        if pd.playwright_available()[0]:
            with self.assertRaises(ValueError):
                pd.PlaywrightBrowser(browser="netscape")
        with self.assertRaises(ValueError):
            AdvancedSearchScraper(browser="lynx")

    def test_scraper_routes_browser_engines_to_playwright(self):
        scr = AdvancedSearchScraper(browser="playwright", transport="http")
        fake = mock.Mock(name="pw")
        fake.name = "playwright"
        with mock.patch("headless.playwright_driver.PlaywrightTransport", return_value=fake) as PT:
            transport, is_browser = scr._transport_for("google")
            self.assertIs(transport, fake)
            self.assertTrue(is_browser)
            self.assertIs(scr._transport_for("yandex")[0], fake)   # built once
        PT.assert_called_once()
        self.assertEqual(PT.call_args.kwargs["timeout"], scr.page_load_timeout)
        scr.quit()
        fake.close.assert_called_once()

    def test_playwright_page_status_is_classified(self):
        scr = AdvancedSearchScraper(browser="playwright")
        transport = mock.Mock()
        transport.name = "playwright"
        transport.fetch.return_value = page_from("<p/>", status=429, headers={"Retry-After": "9"})
        scr._transport_for = lambda e: (transport, True)
        _, attempt = scr._search_one("google", "q", 3)
        self.assertEqual(attempt.status, STATUS_RATE_LIMITED)
        self.assertEqual(attempt.retry_after, 9.0)
        self.assertEqual(transport.fetch.call_args.kwargs["key"], "google")
        scr._playwright = transport
        scr._settle(attempt, set())
        transport.rotate.assert_called_once_with("google")

    def test_missing_browser_build_is_an_error_with_a_hint(self):
        scr = AdvancedSearchScraper(browser="playwright")
        transport = mock.Mock()
        transport.name = "playwright"
        transport.fetch.side_effect = Exception("Executable doesn't exist at /x")
        scr._transport_for = lambda e: (transport, True)
        _, attempt = scr._search_one("google", "q", 3)
        self.assertEqual(attempt.status, STATUS_ERROR)
        self.assertIn("playwright install", attempt.reason)


class TestCliPlaywright(HermeticTestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        self.stderr = err.getvalue()
        return code, out.getvalue()

    def test_extract(self):
        with mock.patch("headless.playwright_driver.PlaywrightBrowser") as PB:
            PB.return_value.__enter__.return_value.extract.return_value = [
                {"title": "A", "link": "https://a"}, {"title": "B", "link": ""}]
            code, out = self._run(["--no-color", "extract", "https://x", "--item", "li",
                                   "-f", "title=h2", "-f", "link=a@href"])
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn("2 rows", out)
            code, out = self._run(["extract", "https://x", "-f", "t=h1", "--json"])
            self.assertEqual(json.loads(out)[0]["title"], "A")
            PB.return_value.__enter__.return_value.extract.return_value = {"t": ""}
            code, _ = self._run(["extract", "https://x", "-f", "t=h1", "--json"])
            self.assertEqual(code, cli.EXIT_FAILED)
            PB.side_effect = RuntimeError("no playwright")
            code, out = self._run(["--no-color", "extract", "https://x", "-f", "t=h1"])
            self.assertEqual(code, cli.EXIT_FAILED)
            self.assertIn("no playwright", self.stderr)   # errors never pollute stdout
            self.assertEqual(out, "")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                cli.build_parser().parse_args(["extract", "https://x", "-f", "bad"])

    def test_shot_and_pdf_through_playwright(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "s.png")
        with mock.patch("headless.playwright_driver.PlaywrightBrowser") as PB:
            browser = PB.return_value.__enter__.return_value

            def write(url, out, **kw):
                open(out, "wb").write(b"x" * 1024)
                return True
            browser.screenshot.side_effect = write
            browser.pdf.side_effect = write
            code, out = self._run(["--no-color", "shot", "https://x", "-o", path,
                                   "--browser", "playwright", "--full-page", "--window", "800x600"])
            self.assertEqual(code, cli.EXIT_OK)
            self.assertTrue(browser.screenshot.call_args.kwargs["full_page"])
            self.assertEqual(PB.call_args.kwargs["viewport"], (800, 600))
            code, _ = self._run(["pdf", "https://x", "-o", path, "--browser", "playwright"])
            self.assertEqual(code, cli.EXIT_OK)
            browser.pdf.side_effect = None
            browser.pdf.return_value = False
            code, _ = self._run(["pdf", "https://x", "-o", path, "--browser", "playwright"])
            self.assertEqual(code, cli.EXIT_FAILED)
            PB.side_effect = RuntimeError("boom")
            code, _ = self._run(["pdf", "https://x", "-o", path, "--browser", "playwright"])
            self.assertEqual(code, cli.EXIT_FAILED)


if __name__ == "__main__":
    unittest.main()
