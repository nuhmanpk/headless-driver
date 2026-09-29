"""Driver resolution, lifecycle and CLI capture paths, with Selenium mocked.

These are the branches a real browser rarely exercises — a stale driver on
PATH, a remote grid, stealth failing, a screenshot that cannot be written — so
they are driven through mocks rather than left to chance.
"""

import io
import os
import json
import tempfile
import contextlib
import unittest
from unittest import mock

from selenium.common.exceptions import SessionNotCreatedException, WebDriverException

from headless import core, manager, cli
from headless.core import Headless, SearchScraper
from headless.manager import ExtendedHeadless, MultiDriverManager
from headless.results import SearchResponse, EngineAttempt, STATUS_OK, STATUS_EMPTY

try:
    from .support import HermeticTestCase
except ImportError:  # run as top-level modules by `discover -s tests`
    from support import HermeticTestCase


def fake_driver(**attrs):
    d = mock.MagicMock()
    d.current_url = "about:blank"
    for k, v in attrs.items():
        setattr(d, k, v)
    return d


class TestDiscovery(HermeticTestCase):
    def test_install_chromedriver_without_webdriver_manager(self):
        with mock.patch.dict("sys.modules", {"webdriver_manager.chrome": None}):
            self.assertIsNone(core.install_chromedriver())

    def test_install_chromedriver_delegates(self):
        fake = mock.Mock()
        fake.ChromeDriverManager.return_value.install.return_value = "/x/chromedriver"
        with mock.patch.dict("sys.modules", {"webdriver_manager.chrome": fake}):
            self.assertEqual(core.install_chromedriver(), "/x/chromedriver")

    def test_chrome_version_parses_and_tolerates_failure(self):
        run = mock.Mock(return_value=mock.Mock(stdout="Google Chrome 152.0.7977.64 \n",
                                               stderr=""))
        with mock.patch("headless.core.subprocess.run", run):
            self.assertEqual(core.chrome_version("/bin/chrome"), "152.0.7977.64")
        with mock.patch("headless.core.subprocess.run", side_effect=OSError):
            self.assertIsNone(core.chrome_version("/bin/chrome"))
        with mock.patch("headless.core.find_chrome_binary", return_value=None):
            self.assertIsNone(core.chrome_version())
        with mock.patch("headless.core.subprocess.run",
                        return_value=mock.Mock(stdout="nonsense", stderr="")):
            self.assertIsNone(core.chrome_version("/bin/chrome"))

    def test_platform_tokens(self):
        for system, token in (("Darwin", "Macintosh"), ("Windows", "Windows NT"),
                              ("Linux", "X11")):
            with self.subTest(system=system), \
                    mock.patch("headless.core.platform.system", return_value=system):
                self.assertIn(token, core._platform_token())

    def test_default_user_agent_falls_back_when_chrome_is_missing(self):
        with mock.patch.object(core, "_user_agent_cache", None), \
                mock.patch("headless.core.chrome_version", return_value=None):
            self.assertIn(core.FALLBACK_CHROME_VERSION, core.default_user_agent())

    def test_binary_and_driver_search_per_platform(self):
        for system in ("Darwin", "Linux", "Windows"):
            with self.subTest(system=system), \
                    mock.patch("headless.core.platform.system", return_value=system), \
                    mock.patch("headless.core.shutil.which", return_value=None), \
                    mock.patch("headless.core.os.path.isfile", return_value=False):
                self.assertIsNone(core.find_chrome_binary())
                self.assertIsNone(core.find_chromedriver_path())
        with mock.patch("headless.core.shutil.which", return_value="/usr/bin/chromedriver"), \
                mock.patch("headless.core.os.path.isfile", return_value=True):
            self.assertEqual(core.find_chromedriver_path(), "/usr/bin/chromedriver")
            self.assertEqual(core.find_chrome_binary(), "/usr/bin/chromedriver")


class TestHeadless(HermeticTestCase):
    def _hl(self, **kw):
        kw.setdefault("user_agent", "UA")
        hl = Headless(verbose=True, **kw)
        self.addCleanup(hl.quit)
        return hl

    def test_options(self):
        hl = self._hl(proxy="http://p:1", additional_args=["--lang=en"], headless=True,
                      window_size=(800, 600))
        args = hl._build_options().arguments
        for expected in ("--headless=new", "--window-size=800,600", "--user-agent=UA",
                         "--proxy-server=http://p:1", "--lang=en"):
            self.assertIn(expected, args)
        with self.assertRaises(ValueError):
            Headless(window_size=(1,))

    def test_remote_driver(self):
        hl = self._hl(remote_url="http://grid:4444")
        with mock.patch("headless.core.webdriver.Remote", return_value=fake_driver()) as remote:
            d = hl.get_driver()
            self.assertIs(hl.get_driver(), d)       # cached
        remote.assert_called_once()

    def test_explicit_driver_path(self):
        hl = self._hl(chrome_driver_path="/x/chromedriver")
        with mock.patch("headless.core.webdriver.Chrome", return_value=fake_driver()) as chrome, \
                mock.patch("headless.core.Service"):
            hl.get_driver()
        chrome.assert_called_once()

    def test_mismatched_guessed_driver_is_replaced(self):
        with mock.patch("headless.core.find_chromedriver_path", return_value="/old/driver"):
            hl = self._hl()
        calls = []

        def chrome(service=None, options=None):
            calls.append(service)
            if len(calls) == 1:
                raise SessionNotCreatedException("version mismatch")
            return fake_driver()
        with mock.patch("headless.core.webdriver.Chrome", side_effect=chrome), \
                mock.patch("headless.core.Service"), \
                mock.patch("headless.core.install_chromedriver", return_value="/new/driver"):
            hl.get_driver()
        self.assertEqual(hl.chrome_driver_path, "/new/driver")
        self.assertEqual(len(calls), 2)

    def test_explicit_driver_is_never_replaced(self):
        hl = self._hl(chrome_driver_path="/mine")
        with mock.patch("headless.core.webdriver.Chrome",
                        side_effect=SessionNotCreatedException("old")), \
                mock.patch("headless.core.Service"), \
                mock.patch("headless.core.install_chromedriver") as install:
            with self.assertRaises(SessionNotCreatedException):
                hl.get_driver()
        install.assert_not_called()

    def test_failed_download_reports_the_mismatch(self):
        with mock.patch("headless.core.find_chromedriver_path", return_value="/old"):
            hl = self._hl()
        with mock.patch("headless.core.webdriver.Chrome",
                        side_effect=SessionNotCreatedException("old")), \
                mock.patch("headless.core.Service"), \
                mock.patch("headless.core.install_chromedriver", side_effect=OSError):
            with self.assertRaises(SessionNotCreatedException):
                hl.get_driver()

    def test_selenium_manager_and_errors(self):
        with mock.patch("headless.core.find_chromedriver_path", return_value=None):
            hl = self._hl()
        with mock.patch("headless.core.webdriver.Chrome", return_value=fake_driver()):
            hl.get_driver()
        hl.quit()
        for exc in (WebDriverException("boom"), RuntimeError("worse")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch("headless.core.webdriver.Chrome", side_effect=exc), \
                    self.assertLogs("headless", level="ERROR") as logged:
                with self.assertRaises(type(exc)):
                    hl.get_driver()
            self.assertTrue(any(str(exc) in line for line in logged.output))

    def test_timeouts_failure_is_not_fatal(self):
        hl = self._hl()
        d = fake_driver()
        d.set_page_load_timeout.side_effect = WebDriverException("no")
        hl._apply_timeouts(d)
        hl._apply_timeouts(None)

    def test_quit_tolerates_errors_and_cleans_up(self):
        hl = self._hl()
        d = fake_driver()
        d.quit.side_effect = RuntimeError("gone")
        hl._driver = d
        profile = hl.user_data_dir
        hl.quit()
        self.assertIsNone(hl._driver)
        self.assertFalse(os.path.isdir(profile))

    def test_context_manager_quits_on_startup_failure(self):
        hl = self._hl()
        with mock.patch.object(hl, "get_driver", side_effect=RuntimeError("no chrome")):
            with self.assertRaises(RuntimeError):
                with hl:
                    pass
        with mock.patch.object(hl, "get_driver", return_value=fake_driver()):
            with hl as d:
                self.assertIsNotNone(d)

    def test_supplied_profile_dir_is_kept(self):
        tmp = tempfile.mkdtemp()
        hl = Headless(user_data_dir=tmp)
        hl.quit()
        self.assertTrue(os.path.isdir(tmp))
        os.rmdir(tmp)


class TestSearchScraper(HermeticTestCase):
    def test_delegates_and_reshapes_results(self):
        s = SearchScraper(transport="http", keep_history=True, verbose=True,
                          result_processor=lambda url, snip: {"u": url})
        response = SearchResponse(query="q", engine="brave", results=[
            {"url": "https://a.com", "snippet": "s", "title": "A"}],
            attempts=[EngineAttempt("brave", STATUS_OK)])
        with mock.patch.object(s._scraper, "search", return_value=response):
            out = s.search("q")
        self.assertEqual(list(out), [{"u": "https://a.com"}])
        self.assertEqual(s.results, [{"u": "https://a.com"}])
        self.assertIsNone(s.driver)
        self.assertIsNone(s.last_engine)
        s.recycle()
        with s:
            pass

    def test_custom_url_becomes_the_only_engine(self):
        s = SearchScraper(search_engine_url="https://my.search/?q={query}")
        self.assertEqual(s._scraper.search_engine, "custom")
        self.assertFalse(s._scraper.fallback)
        self.assertIsNone(s.last_response)
        with mock.patch.object(s._scraper, "_get_driver", return_value="drv"):
            self.assertEqual(s.get_driver(), "drv")
        s.quit()


class TestExtendedHeadless(HermeticTestCase):
    def _eh(self, **kw):
        kw.setdefault("auto_install", False)
        eh = ExtendedHeadless(verbose=True, user_agent="UA", **kw)
        self.addCleanup(eh.quit)
        return eh

    def test_options(self):
        tmp = tempfile.mkdtemp()
        binary = tempfile.NamedTemporaryFile(delete=False)
        self.addCleanup(os.unlink, binary.name)
        eh = self._eh(proxy="socks5://h:1", download_dir=os.path.join(tmp, "dl"),
                      chrome_binary_path=binary.name, profile_dir=tmp)
        opts = eh._build_options()
        self.assertIn("--proxy-server=socks5://h:1", opts.arguments)
        self.assertEqual(opts.binary_location, binary.name)
        self.assertIn("prefs", opts.experimental_options)
        self.assertTrue(os.path.isdir(os.path.join(tmp, "dl")))

    def test_driver_resolution_order(self):
        eh = self._eh(auto_install=True)
        with mock.patch("headless.manager.install_chromedriver", return_value="/auto"):
            eh._auto_install_driver()
        self.assertEqual(eh.chrome_driver_path, "/auto")
        eh2 = self._eh(auto_install=True)
        with mock.patch("headless.manager.install_chromedriver", side_effect=OSError), \
                mock.patch("headless.manager.find_chromedriver_path", return_value="/sys"):
            eh2._auto_install_driver()
        self.assertEqual(eh2.chrome_driver_path, "/sys")
        eh3 = self._eh(chrome_driver_path="/given")
        eh3._auto_install_driver()
        self.assertEqual(eh3.chrome_driver_path, "/given")

    def test_get_driver_applies_stealth_once(self):
        eh = self._eh(stealth=True)
        d = fake_driver()
        with mock.patch("headless.core.Headless.get_driver", return_value=d), \
                mock.patch.dict("sys.modules", {"selenium_stealth": None}):
            eh.get_driver()
        d.execute_cdp_cmd.assert_called_once()       # CDP fallback
        self.assertTrue(eh._applied_stealth)
        eh._driver = d
        self.assertIs(eh.get_driver(), d)

    def test_stealth_failure_is_not_fatal(self):
        eh = self._eh(stealth=True)
        d = fake_driver()
        d.execute_cdp_cmd.side_effect = WebDriverException("no cdp")
        with mock.patch.dict("sys.modules", {"selenium_stealth": None}):
            eh._apply_stealth(d)
        self.assertFalse(eh._applied_stealth)

    def test_selenium_stealth_is_used_when_installed(self):
        eh = self._eh(stealth=True)
        fake = mock.Mock()
        with mock.patch.dict("sys.modules", {"selenium_stealth": fake}):
            eh._apply_stealth(fake_driver())
        fake.stealth.assert_called_once()

    def test_screenshot_and_pdf(self):
        tmp = tempfile.mkdtemp()
        eh = self._eh()
        d = fake_driver()
        d.save_screenshot.return_value = True
        d.execute_cdp_cmd.return_value = {"data": "JVBERi0="}   # "%PDF-"
        with mock.patch.object(eh, "get_driver", return_value=d):
            self.assertTrue(eh.screenshot(os.path.join(tmp, "a", "s.png")))
            self.assertTrue(eh.save_pdf(os.path.join(tmp, "b", "p.pdf")))
            with open(os.path.join(tmp, "b", "p.pdf"), "rb") as f:
                self.assertEqual(f.read(), b"%PDF-")
            d.execute_cdp_cmd.return_value = {}
            self.assertFalse(eh.save_pdf(os.path.join(tmp, "c.pdf")))
            d.execute_cdp_cmd.side_effect = WebDriverException("x")
            self.assertFalse(eh.save_pdf(os.path.join(tmp, "c.pdf")))
            d.save_screenshot.side_effect = WebDriverException("x")
            self.assertFalse(eh.screenshot(os.path.join(tmp, "s.png")))
        with mock.patch.object(eh, "get_driver", side_effect=RuntimeError("no browser")):
            self.assertFalse(eh.screenshot(os.path.join(tmp, "s.png")))
            self.assertFalse(eh.save_pdf(os.path.join(tmp, "p.pdf")))


class TestMultiDriverManager(HermeticTestCase):
    def test_lifecycle(self):
        with mock.patch("headless.manager.ExtendedHeadless") as EH:
            first, second = mock.Mock(), mock.Mock()
            second.quit.side_effect = RuntimeError("already gone")
            EH.side_effect = [first, second, mock.Mock()]
            with MultiDriverManager(verbose=True) as mgr:
                self.assertIs(mgr.create("a"), first)
                self.assertIs(mgr.create("a"), second)   # replacing quits the old one
                first.quit.assert_called_once()
                self.assertIs(mgr.get("a"), second)
                mgr.create("b", verbose=False)
                mgr.quit("missing")
            self.assertEqual(mgr.instances, {})


class TestCliCapture(HermeticTestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_screenshot_and_pdf(self):
        tmp = tempfile.mkdtemp()
        target = os.path.join(tmp, "shot.png")
        with mock.patch("headless.cli.ExtendedHeadless") as EH:
            inst = EH.return_value
            inst.get_driver.return_value.title = "Example"

            def write(path):
                open(path, "wb").write(b"x" * 2048)
                return True
            inst.screenshot.side_effect = write
            inst.save_pdf.side_effect = write
            code, out = self._run(["--no-color", "shot", "https://example.com", "-o", target,
                                   "--window", "800x600", "--proxy", "http://p:1"])
            self.assertEqual(code, cli.EXIT_OK)
            self.assertIn("Example", out)
            self.assertEqual(EH.call_args.kwargs["window_size"], (800, 600))
            code, _ = self._run(["pdf", "https://example.com", "-o",
                                 os.path.join(tmp, "p.pdf"), "--show"])
            self.assertEqual(code, cli.EXIT_OK)
            inst.screenshot.side_effect = None
            inst.screenshot.return_value = False
            code, out = self._run(["shot", "https://example.com", "-o", target])
            self.assertEqual(code, cli.EXIT_FAILED)
            inst.get_driver.side_effect = RuntimeError("no chrome")
            code, out = self._run(["shot", "https://example.com", "-o", target])
            self.assertEqual(code, cli.EXIT_FAILED)
            self.assertIn("RuntimeError", out)

    def test_window_size_validation(self):
        self.assertEqual(cli._window_size("1280x720"), (1280, 720))
        with self.assertRaises(Exception):
            cli._window_size("big")

    def test_version_helpers(self):
        self.assertIsNone(cli._binary_version(None))
        with mock.patch("headless.cli.subprocess.run", side_effect=OSError):
            self.assertIsNone(cli._binary_version("/x"))
        with mock.patch("headless.cli.subprocess.run",
                        return_value=mock.Mock(stdout="", stderr="")):
            self.assertIsNone(cli._binary_version("/x"))
        self.assertIsNone(cli._major("no digits"))

    def test_engine_check(self):
        def fake(**kw):
            s = mock.Mock()
            engine = kw["search_engine"]
            if engine == "yandex":
                s.search.side_effect = RuntimeError("kaput")
            elif engine == "brave":
                s.search.return_value = SearchResponse(
                    "q", engine="brave", results=[{"url": "u", "title": "t", "snippet": ""}],
                    attempts=[EngineAttempt("brave", STATUS_OK, elapsed=0.2)])
            else:
                s.search.return_value = SearchResponse(
                    "q", attempts=[EngineAttempt(engine, "blocked", http_status=403)])
            return s
        with mock.patch("headless.cli.AdvancedSearchScraper", side_effect=fake):
            code, out = self._run(["--no-color", "doctor", "--engines"])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("blocked (403)", out)
        self.assertIn("brave returned no snippets", out)
        self.assertIn("1 healthy", out)
        with mock.patch("headless.cli.AdvancedSearchScraper", side_effect=fake):
            code, out = self._run(["--no-color", "doctor", "--engines", "--transport",
                                   "impersonate"])
        self.assertIn("need a browser", out)

    def test_doctor_version_mismatch_and_missing_chrome(self):
        with mock.patch("headless.cli.find_chrome_binary", return_value="/c"), \
                mock.patch("headless.cli.find_chromedriver_path", return_value="/d"), \
                mock.patch("headless.cli._binary_version",
                           side_effect=lambda p: "Google Chrome 150.0.1" if p == "/c"
                           else "ChromeDriver 149.0.2"), \
                mock.patch("headless.cli._reachable", return_value=False), \
                mock.patch("headless.cli.ExtendedHeadless") as EH:
            EH.return_value.get_driver.side_effect = RuntimeError("cannot start")
            code, out = self._run(["--no-color", "doctor"])
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("chrome 150 vs driver 149", out)
        self.assertIn("unreachable", out)

    def test_search_json_and_empty(self):
        class Fake:
            def __init__(self, **kw):
                pass

            def search(self, q):
                return SearchResponse(q, attempts=[EngineAttempt("brave", STATUS_EMPTY)])

            def quit(self):
                pass
        with mock.patch("headless.cli.AdvancedSearchScraper", Fake):
            code, out = self._run(["search", "q", "--json"])
            self.assertEqual(code, cli.EXIT_FAILED)
            self.assertEqual(json.loads(out)["results"], [])
            code, out = self._run(["search", "q"])
            self.assertIn("no results", out)

    def test_keyboard_interrupt(self):
        with mock.patch("headless.cli.cmd_engines", side_effect=KeyboardInterrupt):
            parser = cli.build_parser()
            with mock.patch.object(cli, "build_parser", return_value=parser):
                parser._subparsers._group_actions[0].choices["engines"].set_defaults(
                    func=cli.cmd_engines)
                code, out = self._run(["engines"])
        self.assertEqual(code, 130)
        self.assertIn("interrupted", out)


if __name__ == "__main__":
    unittest.main()
