import io
import os
import sys
import types
import ctypes
import json
import argparse
import unittest
import contextlib
from unittest import mock

from headless import ui, cli
from headless.ui import (
    Console, diag, supports_color, supports_unicode,
    visible_width, truncate, enable_windows_ansi, LEVELS,
)
from headless.scraper import ENGINE_SPECS, DEFAULT_ENGINE


def buffer_console(**kwargs) -> tuple:
    """A Console writing to a StringIO, with deterministic width."""
    stream = io.StringIO()
    kwargs.setdefault("width", 80)
    return Console(stream=stream, **kwargs), stream


class TestColorDetection(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in ("NO_COLOR", "FORCE_COLOR", "TERM"):
            os.environ.pop(key, None)

    def tearDown(self):
        self.env.stop()

    def test_no_color_env_wins(self):
        os.environ["NO_COLOR"] = "1"
        self.assertFalse(supports_color(io.StringIO()))

    def test_force_color_env_enables_it(self):
        os.environ["FORCE_COLOR"] = "1"
        self.assertTrue(supports_color(io.StringIO()))

    def test_no_color_beats_force_color(self):
        os.environ["NO_COLOR"] = "1"
        os.environ["FORCE_COLOR"] = "1"
        self.assertFalse(supports_color(io.StringIO()))

    def test_dumb_terminal_disables_color(self):
        os.environ["TERM"] = "dumb"
        self.assertFalse(supports_color(io.StringIO()))

    def test_non_tty_disables_color(self):
        # Piping to a file or another process must yield plain text.
        self.assertFalse(supports_color(io.StringIO()))


class TestPortability(unittest.TestCase):
    """Colour must behave on macOS, Linux, Windows and CI log viewers."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        for key in ("NO_COLOR", "FORCE_COLOR", "TERM", "WT_SESSION", "ANSICON",
                    "ConEmuANSI", "TERM_PROGRAM", *ui._ANSI_CI_VARS):
            os.environ.pop(key, None)
        ui._windows_ansi_ready = None

    def tearDown(self):
        self.env.stop()
        ui._windows_ansi_ready = None

    def _tty(self):
        stream = io.StringIO()
        stream.isatty = lambda: True
        return stream

    def test_posix_terminal_gets_colour(self):
        with mock.patch.object(os, "name", "posix"):
            self.assertTrue(supports_color(self._tty()))

    def test_windows_terminal_env_needs_no_console_call(self):
        os.environ["WT_SESSION"] = "1"
        with mock.patch.object(os, "name", "nt"):
            self.assertTrue(enable_windows_ansi())
            self.assertTrue(supports_color(self._tty()))

    @staticmethod
    def _fake_ctypes(kernel32):
        """Stand in for ctypes: the real one cannot be poked at off Windows."""
        module = types.ModuleType("ctypes")
        module.windll = mock.Mock(kernel32=kernel32)
        module.c_uint32 = ctypes.c_uint32
        module.byref = ctypes.byref
        return module

    def test_windows_console_mode_is_enabled_once(self):
        calls = []

        class FakeKernel32:
            def GetStdHandle(self, which): return 7
            def GetConsoleMode(self, handle, ref):
                ref._obj.value = 0x0001
                return 1
            def SetConsoleMode(self, handle, mode):
                calls.append(mode)
                return 1

        fake = self._fake_ctypes(FakeKernel32())
        with mock.patch.object(os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertTrue(enable_windows_ansi())
            enable_windows_ansi()   # cached, must not re-enter the console API
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x4) merged into the existing mode.
        self.assertTrue(calls and all(m & 0x0004 for m in calls))
        self.assertTrue(all(m & 0x0001 for m in calls), "existing mode bits dropped")
        self.assertEqual(len(calls), 2, "expected one call per std handle")

    def test_legacy_windows_console_falls_back_to_plain_text(self):
        # A pre-Windows-10 console rejects the mode change.
        class FailingKernel32:
            def GetStdHandle(self, which): return 7
            def GetConsoleMode(self, handle, ref): return 0
            def SetConsoleMode(self, handle, mode): return 0

        fake = self._fake_ctypes(FailingKernel32())
        with mock.patch.object(os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertFalse(enable_windows_ansi())
            self.assertFalse(supports_color(self._tty()))

    def test_force_color_wins_on_a_legacy_windows_console(self):
        os.environ["FORCE_COLOR"] = "1"

        class FailingKernel32:
            def GetStdHandle(self, which): return 7
            def GetConsoleMode(self, handle, ref): return 0
            def SetConsoleMode(self, handle, mode): return 0

        fake = self._fake_ctypes(FailingKernel32())
        with mock.patch.object(os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertTrue(supports_color(io.StringIO()))

    def test_ci_log_viewers_get_colour_without_a_tty(self):
        for var in ("GITHUB_ACTIONS", "GITLAB_CI", "CODEBUILD_BUILD_ID"):
            with self.subTest(ci=var):
                os.environ.pop("NO_COLOR", None)
                os.environ[var] = "true"
                try:
                    with mock.patch.object(os, "name", "posix"):
                        self.assertTrue(supports_color(io.StringIO()))
                finally:
                    os.environ.pop(var)

    def test_no_color_still_wins_on_ci(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        os.environ["NO_COLOR"] = "1"
        self.assertFalse(supports_color(io.StringIO()))

    def test_plain_redirected_output_stays_plain(self):
        with mock.patch.object(os, "name", "posix"):
            self.assertFalse(supports_color(io.StringIO()))

    def test_unicode_detection_falls_back_for_ascii_streams(self):
        class Ascii(io.StringIO):
            encoding = "ascii"
        self.assertFalse(supports_unicode(Ascii()))
        class Utf8(io.StringIO):
            encoding = "utf-8"
        self.assertTrue(supports_unicode(Utf8()))


class TestDiagnostics(unittest.TestCase):
    @staticmethod
    def _stream(encoding=None):
        # StringIO.encoding is read-only, so declare it on a subclass.
        return type("Stream", (io.StringIO,), {"encoding": encoding})()

    def _emit(self, message, level="info", encoding=None):
        stream = self._stream(encoding)
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            diag(message, level, stream=stream)
        return stream.getvalue()

    def test_every_level_is_defined(self):
        self.assertEqual(set(LEVELS), {"debug", "info", "success", "warn", "error"})

    def test_levels_use_distinct_colours(self):
        seen = {}
        for level in LEVELS:
            seen[level] = self._emit("hello", level)
        self.assertIn("\033[31m", seen["error"])     # red
        self.assertIn("\033[33m", seen["warn"])      # yellow
        self.assertIn("\033[32m", seen["success"])   # green
        self.assertIn("\033[90m", seen["info"])      # grey

    def test_warn_and_error_carry_a_marker(self):
        self.assertIn("!", self._emit("careful", "warn"))
        self.assertIn("✗", self._emit("broken", "error", encoding="utf-8"))
        self.assertNotIn("✗", self._emit("routine", "info", encoding="utf-8"))

    def test_marker_degrades_to_ascii_on_a_non_unicode_terminal(self):
        # A Windows code page or a POSIX locale without UTF-8 must not raise.
        out = self._emit("broken", "error", encoding="ascii")
        self.assertIn("x", out)
        self.assertNotIn("✗", out)
        out.encode("ascii")  # must be writable to such a stream

    def test_component_tag_is_highlighted_separately(self):
        out = self._emit("[Headless] starting up", "info")
        self.assertIn("\033[36m[Headless]\033[0m", out)   # cyan tag
        self.assertIn("starting up", out)

    def test_message_without_a_tag_still_renders(self):
        self.assertIn("no tag here", self._emit("no tag here", "warn"))

    def test_unknown_level_falls_back_to_info(self):
        self.assertIn("mystery", self._emit("mystery", "bogus"))

    def test_output_is_plain_when_colour_is_disabled(self):
        stream = io.StringIO()
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            diag("[Headless] plain please", "error", stream=stream)
        out = stream.getvalue()
        self.assertNotIn("\033[", out)
        self.assertIn("[Headless]", out)
        self.assertIn("plain please", out)

    def test_diag_defaults_to_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            diag("a diagnostic", "warn")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("a diagnostic", err.getvalue())


class TestConsole(unittest.TestCase):
    def test_style_emits_ansi_only_when_colour_is_on(self):
        colored, _ = buffer_console(color=True)
        plain, _ = buffer_console(color=False)
        self.assertEqual(colored.style("hi", "green"), "\033[32mhi\033[0m")
        self.assertEqual(plain.style("hi", "green"), "hi")

    def test_style_without_styles_is_a_noop(self):
        con, _ = buffer_console(color=True)
        self.assertEqual(con.style("hi"), "hi")

    def test_unknown_style_name_is_ignored(self):
        con, _ = buffer_console(color=True)
        self.assertEqual(con.style("hi", "chartreuse"), "hi")

    def test_visible_width_ignores_escape_sequences(self):
        con, _ = buffer_console(color=True)
        self.assertEqual(visible_width(con.style("abcd", "bold", "red")), 4)

    def test_truncate(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")
        self.assertEqual(truncate("abc", 10), "abc")
        self.assertEqual(truncate("abc", 0), "")

    def test_symbols_fall_back_to_ascii(self):
        fancy, _ = buffer_console(unicode=True)
        plain, _ = buffer_console(unicode=False)
        self.assertEqual(fancy.sym("ok"), "✓")
        self.assertEqual(plain.sym("ok"), "+")
        self.assertTrue(plain.sym("arrow").isascii())

    def test_status_lines_carry_label_and_detail(self):
        con, stream = buffer_console(color=False)
        con.ok("chrome", "151.0")
        con.fail("driver", "missing")
        con.warn("mismatch", "148 vs 151")
        out = stream.getvalue()
        for fragment in ("chrome", "151.0", "driver", "missing", "mismatch"):
            self.assertIn(fragment, out)
        self.assertEqual(len(out.strip().splitlines()), 3)

    def test_rule_fits_the_width(self):
        con, stream = buffer_console(color=False, width=40)
        con.rule("engines")
        line = stream.getvalue().rstrip("\n")
        self.assertIn("engines", line)
        self.assertLessEqual(visible_width(line), 40)

    def test_bar_shows_every_non_zero_segment(self):
        con, _ = buffer_console(color=False)
        self.assertEqual(con.bar(4, 0, 0, width=4), "++++")
        self.assertEqual(con.bar(0, 4, 0, width=4), "----")
        bar = con.bar(10, 1, 1, width=12)
        # A single failure among many passes must still be visible.
        self.assertIn("+", bar)
        self.assertIn("-", bar)
        self.assertIn("~", bar)

    def test_bar_respects_width_and_empty_input(self):
        con, _ = buffer_console(color=False)
        self.assertEqual(con.bar(0, 0, 0), "")
        self.assertLessEqual(len(con.bar(100, 50, 10, width=16)), 16)

    def test_bar_is_coloured_when_enabled(self):
        con, _ = buffer_console(color=True)
        bar = con.bar(1, 1, 1)
        self.assertIn("\033[32m", bar)   # green passes
        self.assertIn("\033[33m", bar)   # yellow warnings
        self.assertIn("\033[31m", bar)   # red failures

    def test_table_prints_headers_and_every_row(self):
        con, stream = buffer_console(color=False, width=60)
        con.table(["a", "b"], [["1", "one"], ["2", "two"]])
        lines = stream.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("one", lines[1])
        self.assertIn("two", lines[2])

    def test_table_keeps_rows_inside_the_terminal_width(self):
        con, stream = buffer_console(color=False, width=40)
        con.table(["k", "v"], [["key", "x" * 200]])
        for line in stream.getvalue().splitlines():
            self.assertLessEqual(visible_width(line), 41)

    def test_empty_table_prints_nothing(self):
        con, stream = buffer_console(color=False)
        con.table(["a"], [])
        self.assertEqual(stream.getvalue(), "")

    def test_spinner_is_inert_off_a_terminal(self):
        con, stream = buffer_console(color=True)
        with con.spinner("working"):
            pass
        # One static line, and no cursor-control escapes.
        self.assertIn("working", stream.getvalue())
        self.assertNotIn("\r", stream.getvalue())

    def test_diag_writes_to_stderr_not_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            diag("a diagnostic")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("a diagnostic", err.getvalue())


class TestVersionHelpers(unittest.TestCase):
    def test_major_version_extraction(self):
        self.assertEqual(cli._major("Google Chrome 151.0.7922.140"), "151")
        self.assertEqual(cli._major("ChromeDriver 148.0.7778.179"), "148")
        self.assertIsNone(cli._major(None))
        self.assertIsNone(cli._major("no digits here"))

    def test_binary_version_of_a_missing_executable(self):
        self.assertIsNone(cli._binary_version(None))
        self.assertIsNone(cli._binary_version("/nonexistent/chromedriver"))

    def test_binary_version_strips_build_metadata(self):
        completed = mock.Mock(stdout="ChromeDriver 148.0.1 (abc-refs/branch-heads/1)\n",
                              stderr="")
        with mock.patch("headless.cli.subprocess.run", return_value=completed):
            self.assertEqual(cli._binary_version("/bin/chromedriver"),
                             "ChromeDriver 148.0.1")


class TestParser(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def test_search_defaults(self):
        args = self.parser.parse_args(["search", "hello world"])
        self.assertEqual(args.query, "hello world")
        self.assertEqual(args.engine, DEFAULT_ENGINE)
        self.assertEqual(args.number, 5)
        self.assertFalse(args.no_fallback)
        self.assertFalse(args.json)

    def test_search_accepts_every_registered_engine(self):
        for name in ENGINE_SPECS:
            args = self.parser.parse_args(["search", "q", "-e", name])
            self.assertEqual(args.engine, name)

    def test_search_rejects_an_unknown_engine(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.parser.parse_args(["search", "q", "-e", "askjeeves"])

    def test_window_size_parsing(self):
        args = self.parser.parse_args(["shot", "https://x", "--window", "1280x720"])
        self.assertEqual(args.window, (1280, 720))

    def test_window_size_rejects_junk(self):
        for bad in ("bogus", "1280", "1280*720", "x"):
            with self.subTest(value=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    cli._window_size(bad)

    def test_every_subcommand_is_wired_to_a_function(self):
        for argv in (["search", "q"], ["engines"], ["doctor"],
                     ["shot", "https://x"], ["pdf", "https://x"]):
            with self.subTest(argv=argv):
                self.assertTrue(callable(self.parser.parse_args(argv).func))


class FakeScraper:
    """Stands in for AdvancedSearchScraper so the CLI can be tested offline."""

    results_to_return = [
        {"url": "https://a.example.com", "title": "Alpha",
         "snippet": "first snippet", "engine": "duckduckgo"},
        {"url": "https://b.example.com", "title": "Beta",
         "snippet": "", "engine": "duckduckgo"},
    ]
    exported = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.last_engine = None
        self.quit_calls = 0
        self.results = []

    def search(self, query, max_results=None, engine=None, fallback=None):
        from headless.results import (SearchResponse, EngineAttempt,
                                      STATUS_OK, STATUS_EMPTY)
        self.results = list(self.results_to_return)
        self.last_engine = "duckduckgo" if self.results else None
        status = STATUS_OK if self.results else STATUS_EMPTY
        return SearchResponse(
            query=query, results=self.results, engine=self.last_engine,
            attempts=[EngineAttempt("duckduckgo", status, count=len(self.results))],
            elapsed=0.1)

    def _engine_order(self, engine=None):
        return ["duckduckgo", "bing"]

    def export(self, path):
        type(self).exported = path
        return True

    def quit(self):
        self.quit_calls += 1


class TestCommands(unittest.TestCase):
    def _run(self, argv, scraper_cls=None):
        out = io.StringIO()
        patch = (mock.patch("headless.cli.AdvancedSearchScraper", scraper_cls)
                 if scraper_cls else contextlib.nullcontext())
        with patch, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_no_arguments_prints_help(self):
        code, out = self._run([])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("search", out)
        self.assertIn("doctor", out)

    def test_engines_lists_every_engine_and_the_chain(self):
        code, out = self._run(["engines"])
        self.assertEqual(code, cli.EXIT_OK)
        for name in ENGINE_SPECS:
            self.assertIn(name, out)
        self.assertIn("tried in order", out)

    def test_engines_json_is_machine_readable(self):
        code, out = self._run(["engines", "--json"])
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["default"], DEFAULT_ENGINE)
        self.assertEqual(sorted(payload["engines"]), sorted(ENGINE_SPECS))

    def test_search_renders_results_and_exits_zero(self):
        code, out = self._run(["search", "python"], FakeScraper)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Alpha", out)
        self.assertIn("https://b.example.com", out)
        self.assertIn("2 results", out)
        self.assertIn("duckduckgo", out)

    def test_search_json_output_parses(self):
        code, out = self._run(["search", "python", "--json"], FakeScraper)
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["query"], "python")
        self.assertEqual(payload["engine"], "duckduckgo")
        self.assertEqual(len(payload["results"]), 2)

    def test_search_without_results_reports_failure(self):
        class Empty(FakeScraper):
            results_to_return: list = []
        code, out = self._run(["search", "python"], Empty)
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("no results", out)

    def test_search_json_without_results_still_emits_json(self):
        class Empty(FakeScraper):
            results_to_return: list = []
        code, out = self._run(["search", "python", "--json"], Empty)
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(json.loads(out)["results"], [])

    def test_search_save_calls_export(self):
        FakeScraper.exported = None
        code, out = self._run(["search", "python", "--save", "out.json"], FakeScraper)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(FakeScraper.exported, "out.json")

    def test_search_passes_flags_through_to_the_scraper(self):
        captured = {}

        class Recording(FakeScraper):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                captured.update(kwargs)

        self._run(["search", "python", "-n", "3", "-e", "bing",
                   "--no-fallback", "--timeout", "7",
                   "--transport", "http", "--proxy", "http://127.0.0.1:8080"], Recording)
        self.assertEqual(captured["max_results"], 3)
        self.assertEqual(captured["search_engine"], "bing")
        self.assertFalse(captured["fallback"])
        self.assertEqual(captured["page_load_timeout"], 7.0)
        self.assertEqual(captured["transport"], "http")
        self.assertEqual(captured["proxy"], "http://127.0.0.1:8080")

    def test_search_always_quits_the_driver(self):
        instances = []

        class Tracking(FakeScraper):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                instances.append(self)

        self._run(["search", "python"], Tracking)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].quit_calls, 1)

    def test_search_quits_the_driver_even_when_it_raises(self):
        instances = []

        class Boom(FakeScraper):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                instances.append(self)

            def search(self, *a, **k):
                raise RuntimeError("engine exploded")

        with self.assertRaises(RuntimeError):
            self._run(["search", "python"], Boom)
        self.assertEqual(instances[0].quit_calls, 1)

    def test_json_mode_never_colours_its_output(self):
        code, out = self._run(["search", "python", "--json"], FakeScraper)
        self.assertNotIn("\033[", out)

    def test_blocked_and_empty_are_reported_differently(self):
        from headless.results import SearchResponse, EngineAttempt, STATUS_BLOCKED

        class Blocked(FakeScraper):
            def search(self, query, max_results=None, engine=None, fallback=None):
                self.results = []
                return SearchResponse(query=query, attempts=[
                    EngineAttempt("duckduckgo", STATUS_BLOCKED, reason="captcha")])

        code, out = self._run(["search", "python"], Blocked)
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("refused", out)
        # The plain-empty wording must not be used for a refusal.
        self.assertNotIn("run 'headless-driver doctor'", out)

    def test_search_json_reports_attempts_and_blocked(self):
        code, out = self._run(["search", "python", "--json"], FakeScraper)
        payload = json.loads(out)
        self.assertIn("attempts", payload)
        self.assertIn("blocked", payload)
        self.assertEqual(payload["attempts"][0]["engine"], "duckduckgo")

    def test_engines_json_reports_capabilities(self):
        code, out = self._run(["engines", "--json"])
        payload = json.loads(out)
        self.assertFalse(payload["engines"]["duckduckgo"]["js"])
        self.assertFalse(payload["engines"]["duckduckgo_lite"]["snippets"])

    def test_no_color_flag_strips_ansi(self):
        code, out = self._run(["--no-color", "engines"])
        self.assertNotIn("\033[", out)


if __name__ == "__main__":
    unittest.main()
