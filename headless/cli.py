"""Command line interface for headless-driver."""

import os
import re
import sys
import json
import time
import socket
import argparse
import subprocess
from typing import List, Optional
from urllib.parse import urlparse

from .ui import Console
from .core import find_chromedriver_path, find_chrome_binary
from .manager import ExtendedHeadless
from .scraper import (
    AdvancedSearchScraper,
    ENGINE_SPECS,
    DEFAULT_ENGINE,
    DEFAULT_FALLBACK_ENGINES,
)

EXIT_OK = 0
EXIT_FAILED = 1


def _version() -> str:
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:  # pragma: no cover - Python < 3.8
        return "unknown"
    try:
        return version("headless-driver")
    except PackageNotFoundError:
        return "dev"


def _binary_version(path: Optional[str]) -> Optional[str]:
    """Ask an executable for its version string."""
    if not path:
        return None
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or out.stderr or "").strip()
    if not text:
        return None
    # Drop trailing build metadata, e.g. "(abc123-refs/branch-heads/...)".
    return re.sub(r"\s*\(.*\)\s*$", "", text.splitlines()[0]).strip()


def _major(version_text: Optional[str]) -> Optional[str]:
    if not version_text:
        return None
    match = re.search(r"(\d+)\.", version_text)
    return match.group(1) if match else None


def _driver_options(args) -> dict:
    opts = {"headless": not getattr(args, "show", False)}
    if getattr(args, "window", None):
        opts["window_size"] = args.window
    if getattr(args, "proxy", None):
        opts["proxy"] = args.proxy
    if getattr(args, "timeout", None):
        opts["page_load_timeout"] = args.timeout
    return opts


def _window_size(text: str):
    match = re.fullmatch(r"(\d+)x(\d+)", text.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError("window size must look like 1280x720")
    return int(match.group(1)), int(match.group(2))


# ---------------------------------------------------------------- commands
def cmd_search(args, con: Console) -> int:
    scraper = AdvancedSearchScraper(
        max_results=args.number,
        search_engine=args.engine,
        fallback=not args.no_fallback,
        verbose=args.verbose,
        page_load_timeout=args.timeout,
        headless_options={"page_load_timeout": args.timeout},
    )
    try:
        started = time.time()
        if args.json:
            results = scraper.search(args.query)
        else:
            con.rule(f"search {con.sym('arrow')} {args.query}")
            with con.spinner(f"querying {args.engine}"):
                results = scraper.search(args.query)
        elapsed = time.time() - started

        if args.json:
            json.dump({"query": args.query, "engine": scraper.last_engine,
                       "elapsed": round(elapsed, 2), "results": results},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return EXIT_OK if results else EXIT_FAILED

        if not results:
            con.fail("no results", f"tried {len(scraper._engine_order())} engines")
            con.note("run 'headless-driver doctor' to check connectivity")
            return EXIT_FAILED

        for index, item in enumerate(results, 1):
            number = con.style(f"{index:>2}.", "grey")
            title = con.style(item.get("title") or "(untitled)", "bold")
            con.write(f" {number} {title}")
            con.write(f"     {con.style(item.get('url', ''), 'cyan', 'underline')}")
            snippet = " ".join((item.get("snippet") or "").split())
            if snippet:
                con.write(f"     {con.style(snippet[:160], 'grey')}")
            con.write()

        con.write(" ".join([
            con.style(f" {len(results)} results", "green", "bold"),
            con.style(f"via {scraper.last_engine}", "grey"),
            con.style(f"in {elapsed:.1f}s", "grey"),
        ]))
        if args.save:
            saved = scraper.export(args.save)
            (con.ok if saved else con.fail)(
                f"{'saved' if saved else 'could not save'} {args.save}")
        return EXIT_OK
    finally:
        scraper.quit()


def cmd_engines(args, con: Console) -> int:
    if args.json:
        json.dump({"default": DEFAULT_ENGINE, "chain": [DEFAULT_ENGINE] + DEFAULT_FALLBACK_ENGINES,
                   "engines": {n: s["url"] for n, s in ENGINE_SPECS.items()}},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_OK

    chain = [DEFAULT_ENGINE] + [e for e in DEFAULT_FALLBACK_ENGINES if e != DEFAULT_ENGINE]
    con.rule("engines")
    rows = []
    for name, spec in ENGINE_SPECS.items():
        position = chain.index(name) + 1 if name in chain else None
        rows.append([
            str(position) if position else "-",
            name + (" (default)" if name == DEFAULT_ENGINE else ""),
            urlparse(spec["url"]).netloc,
        ])
    rows.sort(key=lambda r: (r[0] == "-", r[0]))
    con.table(["#", "engine", "endpoint"], rows, styles=["grey", "bold", "cyan"])
    con.write()
    con.note(f"tried in order: {' → '.join(chain)}")
    return EXIT_OK


def _reachable(host: str, timeout: float = 5.0) -> bool:
    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def cmd_doctor(args, con: Console) -> int:
    checks: List[str] = []

    def record(ok: bool, label: str, detail: str = "", fatal: bool = True) -> None:
        if ok:
            checks.append("pass")
            con.ok(label, detail)
        elif fatal:
            checks.append("fail")
            con.fail(label, detail)
        else:
            checks.append("warn")
            con.warn(label, detail)

    con.rule("environment")
    con.status("dot", "headless-driver", _version(), "cyan")
    con.status("dot", "python", sys.version.split()[0], "cyan")
    try:
        import selenium
        con.status("dot", "selenium", selenium.__version__, "cyan")
    except ImportError:
        con.fail("selenium", "not installed")
        return EXIT_FAILED

    con.rule("browser")
    chrome = find_chrome_binary()
    chrome_version = _binary_version(chrome)
    record(bool(chrome), "chrome", chrome_version or chrome or "not found")

    driver = find_chromedriver_path()
    driver_version = _binary_version(driver)
    if driver:
        record(True, "chromedriver", driver_version or driver)
    else:
        record(True, "chromedriver", "none on PATH; will be downloaded", fatal=False)

    chrome_major, driver_major = _major(chrome_version), _major(driver_version)
    if chrome_major and driver_major:
        matched = chrome_major == driver_major
        record(matched, "version match",
               f"chrome {chrome_major} vs driver {driver_major}"
               + ("" if matched else " - a matching driver will be downloaded"),
               fatal=False)

    for module, label in (("webdriver_manager", "webdriver-manager"),
                          ("selenium_stealth", "selenium-stealth")):
        try:
            __import__(module)
            record(True, label, "available")
        except ImportError:
            record(True, label, "not installed (optional)", fatal=False)

    con.rule("connectivity")
    hosts = []
    for name, spec in ENGINE_SPECS.items():
        host = urlparse(spec["url"]).netloc
        if host not in hosts:
            hosts.append(host)
    for host in hosts:
        up = _reachable(host)
        # An unreachable engine is not a failure: the chain routes around it.
        record(up, host, "reachable" if up else "unreachable", fatal=False)

    con.rule("smoke test")
    hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None,
                          verbose=args.verbose)
    try:
        with con.spinner("launching chrome"):
            d = hl.get_driver()
            d.get("data:text/html,<title>ok</title>")
            title = d.title
        record(title == "ok", "launch and navigate", f"page title {title!r}")
    except Exception as e:
        record(False, "launch and navigate", f"{type(e).__name__}: {e}")
    finally:
        hl.quit()

    passed = checks.count("pass")
    warned = checks.count("warn")
    failed = checks.count("fail")
    con.write()
    summary = con.style(f" {passed} passed", "green", "bold")
    if warned:
        summary += con.style(f", {warned} warning{'s' * (warned != 1)}", "yellow", "bold")
    if failed:
        summary += con.style(f", {failed} failed", "red", "bold")
    con.write(f"{summary}  {con.bar(passed, failed, warned)}")
    return EXIT_OK if not failed else EXIT_FAILED


def _capture(args, con: Console, kind: str) -> int:
    hl = ExtendedHeadless(auto_install=True, chrome_binary_path=None,
                          verbose=args.verbose, **_driver_options(args))
    try:
        con.rule(f"{kind} {con.sym('arrow')} {args.url}")
        with con.spinner("loading page"):
            driver = hl.get_driver()
            driver.get(args.url)
            title = driver.title
            saved = (hl.screenshot(args.output) if kind == "screenshot"
                     else hl.save_pdf(args.output))
        if not saved:
            con.fail(f"could not write {args.output}")
            return EXIT_FAILED
        size = os.path.getsize(args.output)
        con.ok(title or args.url, f"{size / 1024:.0f} KB")
        con.write(f"     {con.style(os.path.abspath(args.output), 'cyan')}")
        return EXIT_OK
    except Exception as e:
        con.fail(kind, f"{type(e).__name__}: {e}")
        return EXIT_FAILED
    finally:
        hl.quit()


def cmd_shot(args, con: Console) -> int:
    return _capture(args, con, "screenshot")


def cmd_pdf(args, con: Console) -> int:
    return _capture(args, con, "pdf")


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="headless-driver",
        description="Headless Chrome automation and search scraping.",
    )
    parser.add_argument("--version", action="version", version=_version())
    parser.add_argument("--no-color", action="store_true", help="disable coloured output")
    parser.add_argument("-v", "--verbose", action="store_true", help="show driver diagnostics")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    search = sub.add_parser("search", help="search the web and print results")
    search.add_argument("query", help="what to search for")
    search.add_argument("-n", "--number", type=int, default=5, help="results to return")
    search.add_argument("-e", "--engine", default=DEFAULT_ENGINE,
                        choices=sorted(ENGINE_SPECS), help="engine to start with")
    search.add_argument("--no-fallback", action="store_true",
                        help="do not try other engines if this one fails")
    search.add_argument("--save", metavar="PATH", help="also write results to .json or .csv")
    search.add_argument("--timeout", type=float, default=20.0, help="page load timeout")
    search.add_argument("--json", action="store_true", help="print JSON instead")
    search.set_defaults(func=cmd_search)

    engines = sub.add_parser("engines", help="list the search engines and their order")
    engines.add_argument("--json", action="store_true", help="print JSON instead")
    engines.set_defaults(func=cmd_engines)

    doctor = sub.add_parser("doctor", help="check chrome, driver and connectivity")
    doctor.set_defaults(func=cmd_doctor)

    for name, help_text, default_out in (
        ("shot", "save a screenshot of a page", "screenshot.png"),
        ("pdf", "save a page as PDF", "page.pdf"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("url", help="page to load")
        cmd.add_argument("-o", "--output", default=default_out, help="file to write")
        cmd.add_argument("--window", type=_window_size, metavar="WxH",
                         help="browser window size, e.g. 1280x720")
        cmd.add_argument("--proxy", help="proxy server, e.g. socks5://127.0.0.1:9050")
        cmd.add_argument("--timeout", type=float, default=30.0, help="page load timeout")
        cmd.set_defaults(func=cmd_shot if name == "shot" else cmd_pdf)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    # JSON output must stay machine-readable, so never colour it.
    color = False if (args.no_color or getattr(args, "json", False)) else None
    con = Console(color=color)
    try:
        return args.func(args, con)
    except KeyboardInterrupt:
        con.write()
        con.warn("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
