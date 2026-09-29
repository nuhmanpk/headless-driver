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

import logging

from .ui import Console
from .logs import enable_console_logging, get_logger
from .core import find_chromedriver_path, find_chrome_binary, chrome_version
from .transport import http_available, impersonate_available
from .manager import ExtendedHeadless
from .results import STATUS_OK
from .scraper import (
    AdvancedSearchScraper,
    ENGINE_SPECS,
    DEFAULT_ENGINE,
    DEFAULT_FALLBACK_ENGINES,
    DEFAULT_AGGREGATE_ENGINES,
    TRANSPORTS,
    BROWSERLESS_TRANSPORTS,
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
        transport=args.transport,
        proxy=args.proxy,
        headless_options={"page_load_timeout": args.timeout},
        mode=args.mode,
        region=args.region,
        deadline=args.deadline,
        aggregate_engines=(args.engines.split(",") if args.engines else None),
        browser=args.browser,
    )
    try:
        started = time.time()
        if args.json:
            response = scraper.search(args.query)
        else:
            con.rule(f"search {con.sym('arrow')} {args.query}")
            label = (f"asking {', '.join(scraper.aggregate_engines)} at once"
                     if args.mode == "aggregate" else f"querying {args.engine}")
            with con.spinner(label):
                response = scraper.search(args.query)
        results = list(response)
        elapsed = time.time() - started

        if args.json:
            json.dump(response.as_dict(), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return EXIT_OK if results else EXIT_FAILED

        if not results:
            # "Nobody has an answer" and "everybody refused me" call for
            # opposite reactions, so never report them the same way.
            if response.blocked:
                con.fail("every engine refused this query",
                         f"{len(response.attempts)} tried")
                for a in response.attempts:
                    con.note(str(a))
                con.note("try a proxy, a different network, or wait and retry")
            elif response.cooling:
                con.warn("every eligible engine is cooling down after refusals",
                         "nothing was asked")
                for s in response.skipped:
                    con.note(f"{s['engine']}: {s['reason']} "
                             f"({s.get('resume_in', 0):.0f}s)")
            else:
                con.fail("no results", f"tried {len(response.attempts)} engines")
                con.note("run 'headless-driver doctor' to check connectivity")
            return EXIT_FAILED

        for index, item in enumerate(results, 1):
            number = con.style(f"{index:>2}.", "grey")
            title = con.style(item.get("title") or "(untitled)", "bold")
            votes = item.get("votes")
            badge = ""
            if votes:
                colour = "green" if votes > 1 else "grey"
                badge = " " + con.style(f"[{votes} {'engine' if votes == 1 else 'engines'}: "
                                        f"{', '.join(item.get('engines', []))}]", colour)
            con.write(f" {number} {title}{badge}")
            con.write(f"     {con.style(item.get('url', ''), 'cyan', 'underline')}")
            snippet = " ".join((item.get("snippet") or "").split())
            if snippet:
                con.write(f"     {con.style(snippet[:160], 'grey')}")
            con.write()

        skipped = [a for a in response.attempts if a.status != STATUS_OK]
        via = (", ".join(response.engines) if response.mode == "aggregate"
               else response.engine)
        con.write(" ".join([
            con.style(f" {len(results)} results", "green", "bold"),
            con.style(f"via {via}", "cyan"),
            con.style(f"in {elapsed:.2f}s", "grey"),
        ]))
        if skipped:
            con.note("also tried " + ", ".join(
                f"{a.engine} ({a.status})" for a in skipped))
        if response.skipped:
            con.note("not asked " + ", ".join(
                f"{s['engine']} ({s['reason']})" for s in response.skipped))
        if args.save:
            saved = scraper.export(args.save)
            (con.ok if saved else con.fail)(
                f"{'saved' if saved else 'could not save'} {args.save}")
        return EXIT_OK
    finally:
        scraper.quit()


def cmd_engines(args, con: Console) -> int:
    if args.json:
        json.dump({"default": DEFAULT_ENGINE,
                   "chain": [DEFAULT_ENGINE] + DEFAULT_FALLBACK_ENGINES,
                   "aggregate": list(DEFAULT_AGGREGATE_ENGINES),
                   "engines": {n: {"url": sp["url"],
                                   "method": sp.get("method", "GET"),
                                   "js": bool(sp.get("js", True)),
                                   "snippets": bool(sp.get("snippets", True)),
                                   "honors_site": sp.get("honors_site"),
                                   "provider": sp.get("provider")}
                               for n, sp in ENGINE_SPECS.items()}},
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
            con.style("browser", "yellow") if spec.get("js", True)
            else con.style("http", "green"),
            "yes" if spec.get("snippets", True) else con.style("no", "grey"),
            con.style("yes", "green") if spec.get("honors_site")
            else con.style("no", "red"),
            spec.get("provider") or name,
        ])
    rows.sort(key=lambda r: (r[0] == "-", int(r[0]) if r[0].isdigit() else 99))
    con.table(["#", "engine", "endpoint", "needs", "snippets", "site:", "index"], rows,
              styles=["grey", "bold", "cyan", "", "", "", "magenta"])
    con.write()
    con.note(f"tried in order: {' → '.join(chain)}")
    con.note(f"aggregate mode asks: {', '.join(DEFAULT_AGGREGATE_ENGINES)}")
    return EXIT_OK


def _reachable(host: str, timeout: float = 5.0) -> bool:
    try:
        socket.create_connection((host, 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def cmd_engine_check(args, con: Console) -> int:
    """Run a known-good query through every engine and report what came back.

    `doctor` proves the environment works; this proves the *product* does. An
    engine whose markup has changed still resolves, still connects, and still
    returns a page — and silently yields nothing. Only a real query catches it.
    """
    probe = args.query
    con.rule(f"engine check {con.sym('arrow')} {probe!r}")
    rows, healthy = [], 0

    for name in sorted(ENGINE_SPECS):
        spec = ENGINE_SPECS[name]
        if args.transport in BROWSERLESS_TRANSPORTS and spec.get("js", True):
            # Not a failure: this engine simply cannot be served without a browser.
            rows.append([name, "skipped", "-", "-", "-", "-"])
            continue
        scraper = AdvancedSearchScraper(
            max_results=3, search_engine=name, fallback=False,
            transport=args.transport, proxy=args.proxy,
            page_load_timeout=args.timeout, verbose=args.verbose,
            circuit_breaker=False, withdraw_browser_on_block=False)
        try:
            with con.spinner(f"checking {name}"):
                response = scraper.search(probe)
            attempt = response.attempts[0] if response.attempts else None
            status = attempt.status if attempt else "error"
            titles = sum(1 for r in response if r.get("title"))
            snippets = sum(1 for r in response if r.get("snippet"))
            if status == STATUS_OK and titles:
                healthy += 1
            if attempt and attempt.http_status and attempt.http_status >= 400:
                status = f"{status} ({attempt.http_status})"
            rows.append([
                name,
                status,
                str(len(response)),
                f"{titles}/{len(response)}" if response else "-",
                f"{snippets}/{len(response)}" if response else "-",
                f"{attempt.elapsed:.1f}s" if attempt else "-",
            ])
        except Exception as e:
            rows.append([name, "error", "0", "-", "-", f"{type(e).__name__}"])
        finally:
            scraper.quit()

    con.table(["engine", "status", "results", "titles", "snippets", "time"], rows,
              styles=["bold", "", "", "", "", "grey"])
    con.write()

    # An engine that declares snippets and returns none has probably rotted.
    for row, name in zip(rows, sorted(ENGINE_SPECS)):
        if row[1] == STATUS_OK and row[4].startswith("0/"):
            if ENGINE_SPECS[name].get("snippets", True):
                con.warn(f"{name} returned no snippets", "selector may have changed")

    skipped = sum(1 for r in rows if r[1] == "skipped")
    failed = len(rows) - healthy - skipped
    summary = con.style(f" {healthy} healthy", "green", "bold")
    if failed:
        summary += con.style(f", {failed} not answering", "yellow", "bold")
    if skipped:
        summary += con.style(f", {skipped} need a browser", "grey")
    con.write(f"{summary}  {con.bar(healthy, 0, failed)}")
    # Engines block by design, so a refusal is not a failing build; no engine
    # answering at all is.
    return EXIT_OK if healthy else EXIT_FAILED


def cmd_doctor(args, con: Console) -> int:
    if getattr(args, "engines", False):
        return cmd_engine_check(args, con)
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

    imp_ok, imp_why = impersonate_available()
    http_ok, why = http_available()
    if imp_ok:
        import curl_cffi
        record(True, "impersonate transport",
               f"curl_cffi {getattr(curl_cffi, '__version__', '?')} - browser TLS "
               "fingerprint (used by transport=auto)")
    else:
        record(False, "impersonate transport",
               f"{imp_why} - install headless-driver[impersonate]", fatal=False)
    record(http_ok or imp_ok, "http transport",
           ("available" + ("" if imp_ok else " (used by transport=auto)")) if http_ok
           else f"{why} - install headless-driver[http]",
           fatal=False)
    if http_ok and not imp_ok:
        con.warn("TLS fingerprint will not match the User-Agent",
                 "expect blocks from datacentre IPs; pip install \"headless-driver[impersonate]\"")

    try:
        from .playwright_driver import playwright_available
        pw_ok, pw_why = playwright_available()
    except Exception as e:  # pragma: no cover
        pw_ok, pw_why = False, str(e)
    record(True if pw_ok else False, "playwright",
           "available (browser='playwright', extract)" if pw_ok
           else f"{pw_why} (optional) - install headless-driver[playwright]", fatal=False)

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


def _capture_playwright(args, con: Console, kind: str) -> int:
    from .playwright_driver import PlaywrightBrowser
    options = {"headless": not args.show, "proxy": args.proxy, "timeout": args.timeout}
    if args.window:
        options["viewport"] = args.window
    try:
        con.rule(f"{kind} {con.sym('arrow')} {args.url} (playwright)")
        with PlaywrightBrowser(**options) as browser, con.spinner("loading page"):
            saved = (browser.screenshot(args.url, args.output, full_page=args.full_page)
                     if kind == "screenshot" else browser.pdf(args.url, args.output))
        if not saved:
            con.fail(f"could not write {args.output}")
            return EXIT_FAILED
        con.ok(args.url, f"{os.path.getsize(args.output) / 1024:.0f} KB")
        con.write(f"     {con.style(os.path.abspath(args.output), 'cyan')}")
        return EXIT_OK
    except Exception as e:
        con.fail(kind, f"{type(e).__name__}: {e}")
        return EXIT_FAILED


def _capture(args, con: Console, kind: str) -> int:
    if getattr(args, "browser", "selenium") == "playwright":
        return _capture_playwright(args, con, kind)
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


def _field(text: str):
    name, sep, selector = text.partition("=")
    if not sep or not name or not selector:
        raise argparse.ArgumentTypeError("fields look like name=css or name=css@attr")
    return name.strip(), selector.strip()


def cmd_extract(args, con: Console) -> int:
    """Structured extraction from any page, through Playwright."""
    from .playwright_driver import PlaywrightBrowser
    schema = dict(args.field)
    try:
        with PlaywrightBrowser(headless=not args.show, proxy=args.proxy,
                               timeout=args.timeout) as browser:
            if args.json:
                data = browser.extract(args.url, schema, item_selector=args.item,
                                       scroll=args.scroll)
            else:
                con.rule(f"extract {con.sym('arrow')} {args.url}")
                with con.spinner("rendering page"):
                    data = browser.extract(args.url, schema, item_selector=args.item,
                                           scroll=args.scroll)
    except Exception as e:
        con.fail("extract", f"{type(e).__name__}: {e}")
        return EXIT_FAILED
    rows = data if isinstance(data, list) else [data]
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        con.table(list(schema), [[row.get(k, "") for k in schema] for row in rows],
                  styles=["bold"] + ["" for _ in list(schema)[1:]])
        con.write()
        con.ok(f"{len(rows)} {'row' if len(rows) == 1 else 'rows'}")
    return EXIT_OK if any(any(r.values()) for r in rows) else EXIT_FAILED


def cmd_shot(args, con: Console) -> int:
    return _capture(args, con, "screenshot")


def cmd_pdf(args, con: Console) -> int:
    return _capture(args, con, "pdf")


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="headless-driver",
        description="Fast multi-engine search scraper (Brave, DuckDuckGo, Yahoo, "
                    "Mojeek, Google, Bing...) with browser TLS impersonation, "
                    "plus headless Chrome automation.",
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
    search.add_argument("--transport", choices=TRANSPORTS, default="auto",
                        help="how to fetch pages; impersonate/http skip the browser")
    search.add_argument("--mode", choices=("first", "aggregate"), default="first",
                        help="first: walk the chain; aggregate: ask several engines "
                             "at once and rank by agreement")
    search.add_argument("--engines", metavar="LIST",
                        help="comma-separated engines for --mode aggregate")
    search.add_argument("--deadline", type=float, default=8.0,
                        help="seconds to wait for engines in aggregate mode")
    search.add_argument("--region", help="region such as uk-en or us-en")
    search.add_argument("--browser", choices=("selenium", "playwright"), default="selenium",
                        help="browser for engines that need JavaScript")
    search.add_argument("--proxy", help="proxy server, e.g. socks5://127.0.0.1:9050")
    search.add_argument("--json", action="store_true", help="print JSON instead")
    search.set_defaults(func=cmd_search)

    engines = sub.add_parser("engines", help="list the search engines and their order")
    engines.add_argument("--json", action="store_true", help="print JSON instead")
    engines.set_defaults(func=cmd_engines)

    doctor = sub.add_parser("doctor", help="check chrome, driver and connectivity")
    doctor.add_argument("--engines", action="store_true",
                        help="query every engine and report which still parse")
    doctor.add_argument("--query", default="wikipedia",
                        help="probe query for --engines")
    doctor.add_argument("--transport", choices=TRANSPORTS, default="auto",
                        help="how to fetch pages for --engines")
    doctor.add_argument("--proxy", help="proxy server to test through")
    doctor.add_argument("--timeout", type=float, default=20.0, help="page load timeout")
    doctor.set_defaults(func=cmd_doctor)

    extract = sub.add_parser("extract", help="pull structured data out of a page (playwright)")
    extract.add_argument("url", help="page to load")
    extract.add_argument("-f", "--field", action="append", type=_field, required=True,
                         metavar="NAME=CSS[@ATTR]",
                         help="a field to read, e.g. title=h2 or link=a@href (repeatable)")
    extract.add_argument("--item", metavar="CSS",
                         help="apply the fields inside every element matching this")
    extract.add_argument("--scroll", type=int, default=0,
                         help="scroll to the bottom this many times first")
    extract.add_argument("--proxy", help="proxy server; user:pass@ credentials work")
    extract.add_argument("--timeout", type=float, default=30.0, help="page load timeout")
    extract.add_argument("--show", action="store_true", help="show the browser window")
    extract.add_argument("--json", action="store_true", help="print JSON instead")
    extract.set_defaults(func=cmd_extract)

    from . import bench as _bench
    bench = sub.add_parser("bench", help="measure which engines answer from this address")
    _bench.build_parser(bench)
    bench.set_defaults(func=lambda a, c: _bench.main_with_args(a, c))

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
        cmd.add_argument("--show", action="store_true",
                         help="show the browser window instead of running headless")
        cmd.add_argument("--browser", choices=("selenium", "playwright"), default="selenium",
                         help="which browser automation to capture with")
        cmd.add_argument("--full-page", action="store_true",
                         help="capture the whole scrollable page (playwright)")
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
    # The library is silent unless asked; the CLI is the caller doing the asking.
    enable_console_logging(logging.DEBUG if args.verbose else logging.WARNING,
                           third_party=True, capture_warnings=True,
                           timestamps=args.verbose)
    try:
        return args.func(args, con)
    except KeyboardInterrupt:
        con.write()
        con.warn("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
