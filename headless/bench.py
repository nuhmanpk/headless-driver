"""Per-engine, per-transport health matrix.

Accuracy claims for a scraper only mean something when measured from where it
will run. A laptop on a residential address gets answers from almost any
engine; a datacentre address is where the differences show. This runs a fixed
set of ``site:`` queries against each engine on its own — no fallback — for
each transport, and reports how each combination fared::

    python -m headless.bench --transports impersonate,http
    headless-driver bench --engines brave,duckduckgo --min-ok-rate 0.5

Run it on a schedule from CI (GitHub Actions runners are Azure addresses) to
catch selector rot and new blocks before users do.
"""

import sys
import json
import time
import argparse
import statistics
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence

from .results import (
    STATUS_OK, STATUS_EMPTY, STATUS_UNPARSED, REFUSAL_BY_ENGINE,
)

#: Public figures whose profiles exist, so ``empty`` from a site:-honouring
#: engine is a finding rather than the truth.
DEFAULT_QUERIES: List[str] = [
    'site:linkedin.com/in "Satya Nadella" Microsoft',
    'site:linkedin.com/in "Sundar Pichai" Google',
    'site:linkedin.com/in "Reid Hoffman"',
    'site:linkedin.com/in "Jensen Huang" NVIDIA',
    'site:linkedin.com/in "Lisa Su" AMD',
    'site:linkedin.com/in "Arvind Krishna" IBM',
    'site:linkedin.com/in "Marc Benioff" Salesforce',
    'site:linkedin.com/in "Shantanu Narayen" Adobe',
    'site:github.com torvalds linux',
    'site:wikipedia.org python programming language',
    'site:python.org tutorial',
    'site:docs.python.org asyncio',
    'site:stackoverflow.com python list comprehension',
    'site:developer.mozilla.org fetch api',
    'site:bbc.co.uk news',
    'site:gov.uk passport',
    'site:arxiv.org transformer attention',
    'site:rust-lang.org book',
    'site:nasa.gov artemis',
    'site:who.int malaria',
]


@dataclass
class Cell:
    engine: str
    transport: str
    ok: int = 0
    empty: int = 0
    blocked: int = 0
    unparsed: int = 0
    other: int = 0
    site_rows: int = 0
    times_ms: List[float] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.ok + self.empty + self.blocked + self.unparsed + self.other

    @property
    def ok_rate(self) -> float:
        return self.ok / self.total if self.total else 0.0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.times_ms) if self.times_ms else 0.0

    def as_dict(self) -> Dict:
        out = asdict(self)
        out.pop("times_ms")
        out.update(total=self.total, ok_rate=round(self.ok_rate, 3),
                   p50_ms=round(self.p50_ms))
        return out


def run_bench(engines: Sequence[str], transports: Sequence[str],
              queries: Sequence[str] = DEFAULT_QUERIES, pause: float = 1.0,
              proxy: Optional[str] = None, region: Optional[str] = None,
              progress=None) -> List[Cell]:
    """Ask every engine every query over every transport, one at a time.

    The circuit breaker is off: the point is to observe refusals, not to
    avoid them. `pause` spaces requests to one engine so the benchmark does
    not become the reason it is blocked.
    """
    from .scraper import AdvancedSearchScraper

    cells: List[Cell] = []
    for transport in transports:
        for engine in engines:
            cell = Cell(engine=engine, transport=transport)
            scraper = AdvancedSearchScraper(
                search_engine=engine, fallback=False, transport=transport,
                max_results=10, proxy=proxy, region=region, circuit_breaker=False,
                withdraw_browser_on_block=False)
            try:
                for index, query in enumerate(queries):
                    if progress:
                        progress(engine, transport, index + 1, len(queries))
                    response = scraper.search(query)
                    attempt = response.attempts[0] if response.attempts else None
                    status = attempt.status if attempt else "error"
                    if attempt:
                        cell.times_ms.append(attempt.elapsed * 1000)
                    if status == STATUS_OK:
                        cell.ok += 1
                        cell.site_rows += len(response)
                    elif status == STATUS_EMPTY:
                        cell.empty += 1
                    elif status in REFUSAL_BY_ENGINE:
                        cell.blocked += 1
                    elif status == STATUS_UNPARSED:
                        cell.unparsed += 1
                    else:
                        cell.other += 1
                    if pause and index + 1 < len(queries):
                        time.sleep(pause)
            finally:
                scraper.quit()
            cells.append(cell)
    return cells


def render(cells: Sequence[Cell], con) -> None:
    rows = []
    styles = ["bold", "cyan", "", "", "", "", "", "", "grey"]
    for c in cells:
        rate = c.ok_rate
        colour = "green" if rate >= 0.75 else "yellow" if rate >= 0.25 else "red"
        rows.append([
            c.engine, c.transport,
            con.style(str(c.ok), colour, "bold"),
            str(c.empty),
            con.style(str(c.blocked), "red") if c.blocked else "0",
            con.style(str(c.unparsed), "yellow") if c.unparsed else "0",
            str(c.other),
            str(c.site_rows),
            f"{c.p50_ms:.0f}",
        ])
    con.table(["engine", "transport", "ok", "empty", "blocked", "unparsed",
               "error", "site-rows", "p50 ms"], rows, styles=styles)


def build_parser(parser: Optional[argparse.ArgumentParser] = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="python -m headless.bench",
        description="Per-engine health matrix: which engines answer from here.")
    parser.add_argument("--engines", default="brave,duckduckgo,yahoo,mojeek,google_basic,"
                        "duckduckgo_lite,bing",
                        help="comma-separated engines to measure")
    parser.add_argument("--transports", default="impersonate,http",
                        help="comma-separated transports to compare")
    parser.add_argument("--queries-file", metavar="PATH",
                        help="one query per line (default: 20 built-in site: queries)")
    parser.add_argument("--limit", type=int, default=0,
                        help="use only the first N queries")
    parser.add_argument("--pause", type=float, default=1.0,
                        help="seconds between requests to one engine")
    parser.add_argument("--proxy", help="proxy server to measure through")
    parser.add_argument("--region", help="region such as uk-en")
    parser.add_argument("--min-ok-rate", type=float, default=0.0,
                        help="exit 1 if any engine/transport falls below this")
    parser.add_argument("--json", action="store_true", help="print JSON instead")
    return parser


def main_with_args(args, con=None) -> int:
    from .ui import Console
    from .transport import http_available, impersonate_available

    con = con or Console(color=False if args.json else None)
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    transports = [t.strip() for t in args.transports.split(",") if t.strip()]
    available = {"impersonate": impersonate_available()[0], "http": http_available()[0],
                 "browser": True}
    missing = [t for t in transports if not available.get(t, False)]
    if missing and not args.json:
        con.warn("skipping unavailable transports", ", ".join(missing))
    transports = [t for t in transports if available.get(t, False)]
    if not transports:
        # Measuring nothing must not pass a --min-ok-rate gate.
        message = "no requested transport is available: install headless-driver[impersonate]"
        if args.json:
            json.dump({"error": message, "cells": [], "failing": []}, sys.stdout)
            sys.stdout.write("\n")
        else:
            con.fail(message)
        return 1
    queries = list(DEFAULT_QUERIES)
    if args.queries_file:
        with open(args.queries_file, encoding="utf-8") as f:
            queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if args.limit:
        queries = queries[: args.limit]

    def progress(engine, transport, i, n):
        if not args.json and con.is_terminal:
            con.stream.write(f"\r\033[K {con.style('…', 'cyan')} {engine} over "
                             f"{transport}: {i}/{n}")
            con.stream.flush()

    if not args.json:
        con.rule(f"bench {con.sym('arrow')} {len(queries)} queries × "
                 f"{len(engines)} engines × {len(transports)} transports")
    cells = run_bench(engines, transports, queries, pause=args.pause,
                      proxy=args.proxy, region=args.region, progress=progress)
    if not args.json and con.is_terminal:
        con.stream.write("\r\033[K")
    failing = [c for c in cells if c.ok_rate < args.min_ok_rate]
    if args.json:
        json.dump({"queries": len(queries), "cells": [c.as_dict() for c in cells],
                   "failing": [f"{c.engine}/{c.transport}" for c in failing]},
                  sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        render(cells, con)
        con.write()
        if failing:
            con.fail(f"{len(failing)} below the {args.min_ok_rate:.0%} ok-rate threshold",
                     ", ".join(f"{c.engine}/{c.transport}" for c in failing))
        else:
            con.ok("every engine/transport met the threshold" if args.min_ok_rate
                   else "done")
    return 1 if failing else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return main_with_args(build_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
