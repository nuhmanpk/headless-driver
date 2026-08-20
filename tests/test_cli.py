import io
import os
import json
import argparse
import unittest
import contextlib
from unittest import mock

from headless import ui, cli
from headless.ui import Console, diag, supports_color, visible_width, truncate
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

    def search(self, query, max_results=None, engine=None):
        self.results = list(self.results_to_return)
        self.last_engine = "duckduckgo" if self.results else None
        return self.results

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
                   "--no-fallback", "--timeout", "7"], Recording)
        self.assertEqual(captured["max_results"], 3)
        self.assertEqual(captured["search_engine"], "bing")
        self.assertFalse(captured["fallback"])
        self.assertEqual(captured["page_load_timeout"], 7.0)

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

    def test_no_color_flag_strips_ansi(self):
        code, out = self._run(["--no-color", "engines"])
        self.assertNotIn("\033[", out)


if __name__ == "__main__":
    unittest.main()
