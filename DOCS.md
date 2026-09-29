# headless-driver documentation

Complete reference for the Python API and the `headless-driver` command line
tool. For a quick tour, see the [README](README.md).

- [Install](#install)
- [Command line](#command-line)
  - [search](#search)
  - [engines](#engines)
  - [doctor](#doctor)
  - [bench](#bench)
  - [extract](#extract)
  - [shot and pdf](#shot-and-pdf)
  - [Colour, piping and exit codes](#colour-piping-and-exit-codes)
- [Python API](#python-api)
  - [Headless](#headless)
  - [ExtendedHeadless](#extendedheadless)
  - [MultiDriverManager](#multidrivermanager)
  - [AdvancedSearchScraper](#advancedsearchscraper)
  - [SearchScraper](#searchscraper)
  - [Driver discovery helpers](#driver-discovery-helpers)
  - [Console UI helpers](#console-ui-helpers)
- [Search results](#search-results)
- [Transports and browserless mode](#transports-and-browserless-mode)
- [Aggregate mode](#aggregate-mode)
- [Circuit breaker](#circuit-breaker)
- [Playwright](#playwright)
- [Parallel searching](#parallel-searching)
- [Search engines](#search-engines)
- [Timeouts](#timeouts)
- [Diagnostics and logging](#diagnostics-and-logging)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)
- [Deployment](#deployment)

---

## Install

```bash
pip install "headless-driver[impersonate]"   # recommended
pip install "headless-driver[http]"          # plain-requests browserless mode
pip install "headless-driver[playwright]"    # Playwright backend; then: playwright install chromium
pip install "headless-driver[fast]"          # lxml parser
pip install "headless-driver[all]"           # all of the above
pip install headless-driver                  # Selenium only
```

The `impersonate` extra is strongly recommended: most engines are
server-rendered, and fetching those without a browser is roughly two orders of
magnitude cheaper — and doing it with a real browser's TLS fingerprint is what
gets answers from cloud addresses. See
[Transports](#transports-and-browserless-mode).

`requirements.txt` lists every runtime dependency including the extras, and
`requirements-dev.txt` adds the test and release tooling.

Requires Python 3.9+. Browser automation needs an installed Chrome or Chromium
(a matching ChromeDriver is downloaded automatically when needed) or, for the
Playwright backend, `playwright install chromium`.

From a checkout:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

On Debian/Ubuntu, a system browser and driver can be installed with:

```bash
sudo apt update && sudo apt install -y chromium chromium-driver
```

---

## Command line

```
headless-driver [--version] [--no-color] [-v/--verbose] <command> [options]
```

Global flags belong **before** the subcommand — `headless-driver -v search "x"`,
not `headless-driver search "x" -v`.

| Command | Purpose |
| --- | --- |
| `search QUERY` | Search the web and print results |
| `engines` | List the engines and the fallback order |
| `doctor` | Check Chrome, ChromeDriver, transports and connectivity |
| `bench` | Measure which engines answer from this address |
| `extract URL` | Pull structured data out of a page (Playwright) |
| `shot URL` | Save a screenshot |
| `pdf URL` | Save the page as PDF |

`python -m headless` is the same as `headless-driver`.

### search

```bash
headless-driver search "python headless browser"
headless-driver search "selenium stealth" -n 10 -e yahoo
headless-driver search "web scraping" --save results.csv
headless-driver search "python" --json | jq -r '.results[].url'
headless-driver search 'site:linkedin.com/in "jane doe"' --mode aggregate --region uk-en
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-n`, `--number` | `5` | Results to return |
| `-e`, `--engine` | `brave` | Engine to start with |
| `--no-fallback` | off | Do not try other engines if this one fails |
| `--mode` | `first` | `first` walks the chain; `aggregate` asks several engines at once and ranks by agreement |
| `--engines LIST` | see [Aggregate mode](#aggregate-mode) | Comma-separated engines for `--mode aggregate` |
| `--deadline` | `8.0` | Seconds to wait for engines in aggregate mode |
| `--region` | – | Region such as `uk-en`, fed into each engine's parameters and cookies |
| `--transport` | `auto` | `auto`, `impersonate`, `http` or `browser` |
| `--browser` | `selenium` | `selenium` or `playwright`, for engines that need JavaScript |
| `--proxy` | – | Proxy server URL |
| `--save PATH` | – | Also write results to `.json` or `.csv` |
| `--timeout` | `20.0` | Page load timeout in seconds |
| `--json` | off | Print JSON instead of formatted output |

In aggregate mode each result is printed with the engines that returned it,
`[3 engines: brave, duckduckgo, mojeek]`, and engines that were not asked are
listed with the reason (`cooling`, `ignores_site`, `duplicate_provider`).

`--json` emits a single object:

```json
{
  "query": "python headless",
  "engine": "brave",
  "engines": ["brave"],
  "mode": "first",
  "elapsed": 0.9,
  "blocked": false,
  "cooling": false,
  "results": [
    {
      "url": "https://example.com/page",
      "title": "Example page",
      "snippet": "…",
      "favicon": "https://www.google.com/s2/favicons?domain=example.com",
      "cached": null,
      "quick_answer": null,
      "engine": "brave"
    }
  ],
  "attempts": [
    {"engine": "brave", "status": "ok", "count": 5, "reason": "", "elapsed": 0.88,
     "http_status": 200, "retry_after": null, "transport": "impersonate"}
  ],
  "skipped": []
}
```

### engines

```bash
headless-driver engines
headless-driver engines --json
```

Prints each engine, its endpoint host, its position in the fallback chain,
whether it needs a browser, whether it returns snippets, whether it honours
`site:`, and which index (`provider`) it serves — DuckDuckGo and Yahoo are
both Bing underneath. `--json` includes the aggregate-mode engine list.

### doctor

```bash
headless-driver doctor
```

The quickest way to explain a failing run. It reports the installed versions,
flags a Chrome/ChromeDriver mismatch, reports which transports are available —
warning when only plain `requests` is, because its TLS fingerprint will not
match the User-Agent — checks Playwright, checks that each engine host is
reachable, and finally launches a browser to confirm the whole path works.

```
── browser ──────────────────────────────────────────────
 ✓ chrome              Google Chrome 151.0.7922.140
 ✓ chromedriver        ChromeDriver 148.0.7778.179
 ! version match       chrome 151 vs driver 148 - a matching driver will be downloaded
── connectivity ─────────────────────────────────────────
 ✓ html.duckduckgo.com  reachable
 ✓ www.bing.com         reachable
── smoke test ───────────────────────────────────────────
 ✓ launch and navigate  page title 'ok'

 13 passed, 1 warning  +++++++++++++~
```

A version mismatch and an unreachable engine are warnings, not failures: a
matching driver is fetched on demand, and the chain routes around dead engines.
Only a genuinely broken setup exits non-zero.

### bench

```bash
headless-driver bench                                   # 20 site: queries, 7 engines, 2 transports
headless-driver bench --engines brave,yahoo --transports impersonate --limit 5
headless-driver bench --min-ok-rate 0.5 --json > bench.json   # non-zero exit below 50%
python -m headless.bench --queries-file my-queries.txt
```

Runs a fixed set of `site:` queries against each engine **on its own** (no
fallback, circuit breaker off) for each transport, and prints a health matrix:

```
 engine        transport    ok  empty  blocked  unparsed  error  site-rows  p50 ms
 brave         impersonate  18  1      1        0         0      121        640
 duckduckgo    impersonate  15  2      3        0         0      98         710
 duckduckgo    http         2   0      18       0         0      12         410
```

Accuracy claims only mean something measured from where the code will run. A
laptop on a residential address gets answers from almost any engine; run this
from the cloud (or on a schedule from CI — see `.github/workflows/engine-bench.yml`)
to see what your servers see.

| Option | Default | Meaning |
| --- | --- | --- |
| `--engines` | `brave,duckduckgo,yahoo,mojeek,google_basic,duckduckgo_lite,bing` | Engines to measure |
| `--transports` | `impersonate,http` | Transports to compare; unavailable ones are skipped |
| `--queries-file PATH` | built-in 20 | One query per line, `#` for comments |
| `--limit N` | all | Use only the first N queries |
| `--pause` | `1.0` | Seconds between requests to one engine |
| `--proxy`, `--region` | – | As for `search` |
| `--min-ok-rate` | `0` | Exit 1 when any cell falls below this |
| `--json` | off | Print JSON instead |

### extract

```bash
headless-driver extract https://news.ycombinator.com --item tr.athing \
    -f title=".titleline > a" -f link=".titleline > a@href" --json
headless-driver extract https://example.com -f heading=h1 -f first_link=a@href
```

Renders the page with Playwright and applies a declarative schema: each
`-f NAME=CSS` reads the text of the first match, `NAME=CSS@ATTR` reads an
attribute (`href` and `src` are returned absolute). With `--item`, the schema
is applied inside every matching element and a table (or JSON list) is printed.
`--scroll N` scrolls to the bottom up to N times first, for pages that load more
as you go. Needs the `playwright` extra.

### shot and pdf

```bash
headless-driver shot https://example.com -o page.png --window 1280x720
headless-driver pdf  https://example.com -o page.pdf
headless-driver shot https://example.com --proxy socks5://127.0.0.1:9050
headless-driver shot https://example.com --browser playwright --full-page
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--output` | `screenshot.png` / `page.pdf` | File to write |
| `--window WxH` | `1920x1080` | Browser window size |
| `--proxy` | – | Proxy server URL |
| `--browser` | `selenium` | `selenium` or `playwright` |
| `--full-page` | off | Capture the whole scrollable page (Playwright) |
| `--show` | off | Show the browser window instead of running headless |
| `--timeout` | `30.0` | Page load timeout in seconds |

Missing parent directories are created for you.

### Colour, piping and exit codes

Colour is enabled only when stdout is a terminal, so piping or redirecting
yields plain text automatically.

| Control | Effect |
| --- | --- |
| `--no-color` | Disable colour for this run |
| `NO_COLOR=1` | Disable colour; wins over everything else |
| `FORCE_COLOR=1` | Enable colour even when not a terminal |
| `TERM=dumb` | Disable colour |

Colour works on macOS, Linux and Windows, with no third-party dependency:

- **macOS and Linux** — coloured whenever the stream is a terminal.
- **Windows** — Windows Terminal, ConEmu and ANSICON are detected directly. On
  a classic console host, `ENABLE_VIRTUAL_TERMINAL_PROCESSING` is switched on
  through `ctypes`, which is what makes Windows 10+ interpret escape sequences.
  A pre-Windows-10 console that rejects the change falls back to plain text.
- **CI and cloud logs** — GitHub Actions, GitLab, CircleCI, Travis, Buildkite,
  Drone, AppVeyor, TeamCity and AWS CodeBuild render ANSI in their log viewers
  but are not terminals, so they are detected by environment variable and get
  colour anyway. `NO_COLOR=1` still overrides that.

Only the 16 basic ANSI colours (and their bright variants) are used, so output
stays readable on low-colour terminals, and every symbol degrades to ASCII when the stream encoding cannot
represent it — a Windows code page or a non-UTF-8 POSIX locale prints `+`, `x`
and `!` instead of `✓`, `✗` and `!`.

Non-ASCII symbols degrade to ASCII when the output encoding cannot represent
them, and the spinner is skipped entirely off a terminal so logs stay clean.

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Nothing could be produced (no results, capture failed) |
| `2` | Usage error |
| `130` | Interrupted with Ctrl-C |

---

## Python API

Everything below is importable straight from the package:

```python
from headless import (
    Headless, ExtendedHeadless, MultiDriverManager,
    AdvancedSearchScraper, SearchScraper, ScraperPool,
    SearchResponse, EngineAttempt, AllEnginesBlocked,
    EngineHealth, default_health, reset_default_health,
    ImpersonateTransport, HttpTransport,
    merge_results, normalize_url, normalize_text,
    ColorFormatter, colorize_logging, enable_console_logging,
    find_chrome_binary, find_chromedriver_path, install_chromedriver,
    ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES, DEFAULT_AGGREGATE_ENGINES,
)
from headless.playwright_driver import PlaywrightBrowser, PlaywrightTransport
```

### Headless

Manages one Chrome WebDriver and its profile directory.

```python
Headless(
    user_data_dir: Optional[str] = None,
    window_size: Tuple[int, int] = (1920, 1080),
    user_agent: Optional[str] = None,
    headless: bool = True,
    chrome_driver_path: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
    remote_url: Optional[str] = None,
    verbose: bool = False,
    page_load_timeout: Optional[float] = 30.0,
)
```

| Argument | Meaning |
| --- | --- |
| `user_data_dir` | Chrome profile directory. When omitted a temporary one is created and deleted on `quit()`. |
| `window_size` | `(width, height)`. A value that is not a two-item pair raises `ValueError`. |
| `user_agent` | Custom `--user-agent` string. |
| `headless` | Run without a visible window. |
| `chrome_driver_path` | Explicit chromedriver path. When omitted, one is auto-detected; see [driver resolution](#driver-resolution). |
| `additional_args` | Extra Chrome switches, e.g. `["--lang=de", "--mute-audio"]`. |
| `remote_url` | Connect to a remote Selenium server instead of launching locally. |
| `verbose` | Print setup and teardown diagnostics to stderr. |
| `page_load_timeout` | Seconds a page load may take before it aborts. `None` keeps Selenium's 300s default. |

**Methods**

| Method | Returns | Notes |
| --- | --- | --- |
| `get_driver()` | `WebDriver` | Creates the driver on first call and returns the same instance afterwards. |
| `quit()` | `None` | Quits the driver and removes the temporary profile. Safe to call twice. |

Usable as a context manager, which yields the driver:

```python
from headless import Headless

with Headless(window_size=(1280, 720)) as driver:
    driver.get("https://example.com")
    print(driver.title)
```

Or managed by hand:

```python
hl = Headless(headless=False)          # visible window
driver = hl.get_driver()
driver.get("https://example.com")
hl.quit()
```

Persistent profile, so logins survive between runs:

```python
hl = Headless(user_data_dir="~/.cache/my-bot-profile")
```

A remote Selenium grid:

```python
hl = Headless(remote_url="http://localhost:4444/wd/hub")
```

#### Driver resolution

`Headless` resolves a driver in this order:

1. `remote_url`, if given.
2. `chrome_driver_path`, if given.
3. A chromedriver found on `PATH` or in a common install location.
4. Selenium Manager, which resolves one itself.

If a driver found by step 3 turns out to be built for a different Chrome major
version, a matching one is downloaded and used instead — a version mismatch is
by far the most common cause of a failed launch. An explicit path from step 2 is
never silently replaced.

### ExtendedHeadless

`Headless` plus proxy, stealth, downloads, capture and automatic driver
installation. Accepts every `Headless` argument as well as:

```python
ExtendedHeadless(
    proxy: Optional[str] = None,
    stealth: bool = False,
    download_dir: Optional[str] = None,
    auto_install: bool = True,
    profile_dir: Optional[str] = None,
    chrome_driver_path: Optional[str] = None,
    chrome_binary_path: Optional[str] = "/usr/bin/chromium-browser",
    verbose: bool = False,
    **headless_kwargs,
)
```

| Argument | Meaning |
| --- | --- |
| `proxy` | Proxy URL, e.g. `http://host:3128` or `socks5://127.0.0.1:9050`. |
| `stealth` | Apply anti-detection tweaks via `selenium-stealth`, falling back to a CDP script that hides `navigator.webdriver`. |
| `download_dir` | Directory for downloads; created if missing, and PDFs save instead of opening in the viewer. |
| `auto_install` | Download a ChromeDriver matching the installed Chrome via `webdriver-manager`. |
| `profile_dir` | Alias for `user_data_dir`. |
| `chrome_binary_path` | Explicit browser binary. Ignored when the path does not exist, so the default is harmless off Linux. Pass `None` to always let Chrome be found normally. |

**Extra methods**

| Method | Returns | Notes |
| --- | --- | --- |
| `screenshot(path)` | `bool` | PNG of the current page. Creates parent directories. |
| `save_pdf(path, print_background=True)` | `bool` | PDF via Chrome DevTools `Page.printToPDF`. |

Both act on the **current page** of the existing driver, so navigate first:

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(stealth=True, download_dir="~/Downloads/bot")
driver = hl.get_driver()
driver.get("https://example.com")

hl.screenshot("out/example.png")
hl.save_pdf("out/example.pdf")
hl.quit()
```

Driver resolution differs slightly from `Headless`: an explicit
`chrome_driver_path` wins, then `auto_install` (a version-matched download),
then a system driver, then Selenium Manager. Auto-install is preferred over a
system driver precisely because a driver already on `PATH` is so often stale.

### MultiDriverManager

Runs several isolated browsers side by side, each with its own profile.

```python
MultiDriverManager(verbose: bool = False)
```

| Method | Returns | Notes |
| --- | --- | --- |
| `create(name, **extended_headless_kwargs)` | `ExtendedHeadless` | Registers an instance under `name`. Reusing a name quits the previous instance first, so a browser is never orphaned. |
| `get(name)` | `ExtendedHeadless \| None` | Look up an instance. |
| `quit(name)` | `None` | Quit and forget one instance. |
| `quit_all()` | `None` | Quit and forget everything. |

```python
from headless import MultiDriverManager

with MultiDriverManager() as mgr:
    a = mgr.create("scraper", stealth=True)
    b = mgr.create("proxied", proxy="socks5://127.0.0.1:9050")
    a.get_driver().get("https://example.com")
    b.get_driver().get("https://example.org")
# quit_all() runs on exit
```

### AdvancedSearchScraper

Scrapes search results, walking a chain of engines until one answers — or,
with `mode="aggregate"`, asking several at once and ranking by agreement.

```python
AdvancedSearchScraper(
    driver=None,
    max_results: int = 10,
    result_processor: Optional[Callable[[str, Dict], Dict]] = None,
    headless_options: Optional[dict] = None,
    search_engine: str = "brave",
    verbose: bool = False,
    fallback: bool = True,
    fallback_engines: Optional[Sequence[str]] = None,
    page_load_timeout: float = 20.0,
    wait_timeout: float = 8.0,
    proxy: Optional[str] = None,
    transport: str = "auto",            # "auto" | "impersonate" | "http" | "browser"
    keep_history: bool = False,
    history_limit: int = 1000,
    raise_on_block: bool = False,
    user_agent: Optional[str] = None,
    *,                                   # 1.1 options are keyword-only
    mode: str = "first",                 # or "aggregate"
    aggregate_engines: Optional[Sequence[str]] = None,
    deadline: float = 8.0,
    min_engines: int = 2,
    strict_site: bool = True,
    region: Optional[str] = None,        # e.g. "uk-en"
    http_timeout: Optional[float] = None,  # default min(8, page_load_timeout)
    circuit_breaker: bool = True,
    health: Optional[EngineHealth] = None,
    withdraw_browser_on_block: bool = True,
    browser_cooldown: float = 120.0,
    verify_empty: bool = False,
    probe_query: str = "site:wikipedia.org python",
    normalize: Optional[Callable[[str], str]] = None,
    impersonate_profiles: Optional[Sequence[str]] = None,
    browser: str = "selenium",           # or "playwright"
    playwright_options: Optional[dict] = None,
)
```

| Argument | Meaning |
| --- | --- |
| `driver` | Reuse an existing WebDriver. When omitted one is created from `headless_options` and owned by this object. |
| `max_results` | Default result cap per search. |
| `result_processor` | `fn(query, item) -> dict`, applied to every result. |
| `headless_options` | Keyword arguments forwarded to `Headless` when creating a driver. |
| `search_engine` | Engine to start with. An unknown name raises `ValueError`. |
| `fallback` | Try the other engines when the first fails. |
| `fallback_engines` | Custom fallback order; defaults to `DEFAULT_FALLBACK_ENGINES`. |
| `page_load_timeout` | Seconds a browser page may take to load before it is abandoned. |
| `wait_timeout` | Seconds to wait for results to appear once the page has loaded. |
| `proxy` | Proxy for every transport. Credentials in the URL work over `impersonate`, `http` and Playwright (not Selenium's Chrome switch). |
| `transport` | How server-rendered engines are fetched; see [Transports](#transports-and-browserless-mode). |
| `raise_on_block` | Raise `AllEnginesBlocked` instead of returning an empty, blocked (or cooling) response. |
| `user_agent` | Override the User-Agent. Under impersonation this narrows the profiles to the same browser family. |
| `mode` | Default search mode: `"first"` or `"aggregate"`. |
| `aggregate_engines`, `deadline`, `min_engines` | Aggregate-mode defaults; see [Aggregate mode](#aggregate-mode). |
| `strict_site` | For `site:` queries, skip fallback engines known to ignore `site:` (Bing) instead of fetching and discarding. |
| `region` | `country-lang`, e.g. `uk-en`; sets Google `hl/lr/cr`, Brave's country cookie, Mojeek `arc/lb`, DuckDuckGo `l`. |
| `http_timeout` | Timeout for browserless fetches, separate from the browser's. |
| `circuit_breaker`, `health` | Per-engine stand-down after refusals; see [Circuit breaker](#circuit-breaker). |
| `withdraw_browser_on_block`, `browser_cooldown` | Stop launching a browser for this long once two independent indexes refuse in one search. |
| `verify_empty`, `probe_query` | Check an `empty` from a `site:`-honouring engine with a control query; see [Soft blocks](#soft-blocks). |
| `normalize` | URL-normalisation hook used to merge results in aggregate mode. |
| `impersonate_profiles` | Browser profiles to rotate between (default Chrome, Edge, Safari, Firefox, Chrome Android, Safari iOS). |
| `browser`, `playwright_options` | Which browser serves JavaScript engines, and options for [PlaywrightBrowser](#playwright). |

**Attributes**

| Attribute | Meaning |
| --- | --- |
| `results` | Accumulated history; empty unless `keep_history=True`. |
| `last_response` | The most recent `SearchResponse`. |
| `last_engine` | Engine that answered most recently, or `None`. Cleared on a failed search. |
| `engines` | This instance's engine registry, a copy of `ENGINE_SPECS`. |

**Methods**

| Method | Returns | Notes |
| --- | --- | --- |
| `search(query, max_results=None, engine=None, fallback=None, mode=None, engines=None, deadline=None, min_engines=None)` | `SearchResponse` | Walks the chain, or fans out with `mode="aggregate"`. `engine` chooses where to start; `fallback` decides whether the rest is tried. |
| `probe(engine=None, query=None)` | `EngineAttempt` | Ask one engine a query that cannot be empty; `empty` means soft-blocked. |
| `health()` | `Dict` | Circuit-breaker state per engine: `state`, `resume_in`, `last_status`, `http_status`. |
| `reset_health(engine=None)` | `None` | Stand every engine (or one) back up now. |
| `engines_honoring_site()` | `Set[str]` | Engines whose `empty` answer to a `site:` query is evidence of absence. |
| `browserless_transport_name()` | `str` or `None` | `"impersonate"`, `"http"`, or `None`. |
| `search_batch(queries, max_workers=4, per_query=None)` | `Dict[str, SearchResponse]` | Sequential unless the chain is HTTP-only; see [Parallel searching](#parallel-searching). |
| `export(path, results=None)` | `bool` | Writes `results`, else the history, else the last response, to `.json` or `.csv`. |
| `register_engine(name, spec)` | `None` | Adds or overrides an engine. Unspecified `js` defaults to True. |
| `capabilities(engine=None)` | `Dict` | `js`, `snippets`, `honors_site`, `provider`, `method`, `url`. |
| `recycle()` | `None` | Discard the browser; the next search builds a fresh one. |
| `default_result_processor(query, item)` | `Dict` | The identity processor used when `result_processor` is not supplied. |
| `quit()` | `None` | Quits the driver if this object created it, and closes the HTTP, impersonation and Playwright sessions. |

Each result is a dict:

| Key | Meaning |
| --- | --- |
| `url` | Destination URL, with click-tracking redirects resolved and a percent-encoded path decoded where lossless |
| `title` | Result heading, entity-unescaped, NFC-normalised, control and zero-width characters removed |
| `snippet` | Description text, cleaned the same way; `""` when the engine omits one |
| `favicon` | Favicon URL derived from the domain |
| `cached` | Cached-page link when the engine offers one |
| `quick_answer` | Reserved, currently `None` |
| `engine` | Engine that produced this result — reliable per result, unlike a shared `last_engine` |
| `votes`, `engines`, `ranks` | Aggregate mode only: how many engines returned it, which, and at what position |

```python
from headless import AdvancedSearchScraper

with AdvancedSearchScraper(max_results=5) as scr:
    for item in scr.search("python headless browser"):
        print(item["title"], item["url"])
    print("answered by", scr.last_engine)
```

Choosing engines:

```python
AdvancedSearchScraper(search_engine="yahoo")                # different start
AdvancedSearchScraper(fallback=False)                       # no chain
AdvancedSearchScraper(fallback_engines=["mojeek", "yahoo"]) # custom order
scr.search("query", engine="yahoo")                         # start here, chain still applies
scr.search("query", engine="yahoo", fallback=False)         # this engine only
```

`engine` and `fallback` are independent. In 0.x, `search(engine=…)` silently
disabled the chain while `search_engine=` did not; that asymmetry is gone.

Batch searches and export:

```python
scr = AdvancedSearchScraper(max_results=3)
batch = scr.search_batch(["python asyncio", "python typing"], max_workers=2)
scr.export("results.json")
scr.export("results.csv")
scr.quit()
```

A WebDriver session is not thread-safe, so `search_batch` runs in parallel only
when every engine in the chain can be fetched without a browser; otherwise it
is sequential. Use `ScraperPool` for parallel browser work.

Custom result shape:

```python
def only_domain(query, item):
    from urllib.parse import urlparse
    return {"query": query, "domain": urlparse(item["url"]).netloc}

scr = AdvancedSearchScraper(result_processor=only_domain)
```

Adding your own engine — `spec` needs `url`, `result`, `link`, `title` and
`snippet`; the selector lists are tried in order. The URL either contains
`{query}` or a `params` callable builds the query string (or POST form). See
[Engine specs](#engine-specs) for every optional key:

```python
scr = AdvancedSearchScraper()
scr.register_engine("mysearch", {
    "url": "https://search.example.com/?q={query}",
    "result": "div.result",
    "link": ["a.result-link"],
    "title": ["h3"],
    "snippet": ["p.desc"],
})
scr.search("python", engine="mysearch")
```

### SearchScraper

A thinner interface over the same engine chain, returning only `url` and
`snippet`.

```python
SearchScraper(
    driver=None,
    max_results: int = 10,
    result_processor: Optional[Callable[[str, str], Dict]] = None,
    headless_options: Optional[dict] = None,
    search_engine_url: Optional[str] = None,
    verbose: bool = False,
    fallback: bool = True,
    page_load_timeout: float = 20.0,
    wait_timeout: float = 8.0,
)
```

`result_processor` here is `fn(url, snippet) -> dict`, defaulting to
`default_result_processor`, which returns `{"url": …, "snippet": …}`. Supplying
`search_engine_url` scrapes that one URL with the DuckDuckGo front end's
selectors and disables the fallback chain.

`search()`, `get_driver()`, `quit()` and the `driver` and `last_engine`
attributes behave as they do on
[`AdvancedSearchScraper`](#advancedsearchscraper), and the class is a context
manager.

```python
from headless import SearchScraper

with SearchScraper(max_results=5) as scraper:
    for item in scraper.search("Nuhman PK github"):
        print(item["url"])
    print(scraper.last_engine)
```

### Driver discovery helpers

```python
from headless import find_chrome_binary, find_chromedriver_path, install_chromedriver

find_chrome_binary()      # -> path to Chrome/Chromium, or None
find_chromedriver_path()  # -> chromedriver on PATH or a common location, or None
install_chromedriver()    # -> path to a driver matching the installed Chrome, or None
```

```python
from headless import chrome_version, default_user_agent, __version__

chrome_version()      # -> "152.0.7977.65", or None
default_user_agent()  # -> a User-Agent matching that browser and this OS
__version__           # -> the installed package version
```

`install_chromedriver()` needs `webdriver-manager` and returns `None` when it is
not installed. All of these work on Linux, macOS and Windows.

### Exceptions

```python
from headless import HeadlessDriverError, AllEnginesBlocked
```

`HeadlessDriverError` is the base class for this package's exceptions.
`AllEnginesBlocked` subclasses it and carries the `SearchResponse` on
`.response`; it is raised only when `raise_on_block=True` and every engine
refused.

### Console UI helpers

`headless.ui` powers the CLI and is dependency-free. It is useful if you want
the same look in your own scripts.

```python
from headless.ui import Console

con = Console()                 # colour auto-detected from the stream
con.rule("results")
con.ok("chrome", "151.0")
con.warn("driver", "version mismatch")
con.fail("network", "unreachable")
con.table(["engine", "host"], [["bing", "www.bing.com"]], styles=["bold", "cyan"])
con.write(con.bar(good=12, bad=1, warn=2))
with con.spinner("working"):
    ...
```

`Console(stream=None, color=None, unicode=None, width=None)` — pass `color=` or
`unicode=` to override detection, and `width=` to fix the layout width.

| Member | Purpose |
| --- | --- |
| `style(text, *styles)` | Wrap in ANSI codes; a no-op when colour is off |
| `sym(name)` | `ok`, `fail`, `warn`, `arrow`, `dot`, `line`, `bullet`, with ASCII fallbacks |
| `write(text)`, `note(text)` | Plain and dimmed lines |
| `rule(label)` | Full-width heading |
| `status/ok/warn/fail(label, detail)` | Marked status lines |
| `table(headers, rows, styles)` | Width-aware table, last column truncated to fit; pre-coloured cells are measured by visible width |
| `bar(good, bad, warn, width)` | Diffstat-style bar: green `+`, yellow `~`, red `-` |
| `spinner(label)` | Context manager; inert off a terminal |
| `is_terminal`, `width` | Stream properties |

Style names: `bold`, `dim`, `italic`, `underline`, `black`, `red`, `green`,
`yellow`, `blue`, `magenta`, `cyan`, `white`, `grey`, their `bright_` variants,
and backgrounds `bg_red`, `bg_green`, `bg_yellow`, `bg_blue`, `bg_magenta`,
`bg_cyan`, `bg_grey`.

Module functions: `supports_color(stream)`, `supports_unicode(stream)`,
`visible_width(text)` (ignores ANSI), `truncate(text, limit)` (ANSI-aware),
`strip_ansi(text)`, `highlight(console, text, base)` (colours statuses, HTTP
codes, URLs, quotes and numbers), `render_diag(message, level, console)`,
`component_colour(name)`,
`enable_windows_ansi()` (turns on the Windows console's ANSI mode and caches
the result), the `LEVELS` table, and the stderr writers `diag(message, level)`,
`debug`, `info`, `success`, `warn` and `error`.

---

## Search results

`search()` returns a :class:`SearchResponse`. It behaves like the list of
results it contains, so existing code is unaffected:

```python
for hit in scraper.search("python headless"):    # iterates results
    print(hit["url"])

results = scraper.search("python headless")
if not results:                                  # falsey when empty
    ...
len(results), results[0], results == []          # all work
```

What it adds is **what happened**:

```python
response = scraper.search("python headless")

response.engine        # engine that answered, or None
response.blocked       # True only if every engine refused
response.attempts      # one EngineAttempt per engine tried, in order
response.refused       # just the refusals
response.engines_tried # their names
response.elapsed       # seconds
response.as_dict()     # JSON-serialisable
```

This distinction is the point of the release. An empty list previously meant any
of: nothing matched, a bot check, a timeout, or an unreachable host. The first
means move on; the rest mean slow down, change address, or retry.

```python
response = scraper.search(query)
if response.blocked:
    back_off_and_rotate_proxy()      # not the query's fault
elif not response:
    record_no_such_page()            # genuinely nothing to find
```

### EngineAttempt

| Field | Meaning |
| --- | --- |
| `engine` | Which engine |
| `status` | `ok`, `empty`, `blocked`, `rate_limited`, `unparsed`, `timeout`, `unreachable`, `error` |
| `count` | Results extracted |
| `reason` | Detail, e.g. `HTTP 403` or `bot-check element '#challenge-form'` |
| `elapsed` | Seconds |
| `http_status` | The HTTP status code, when the page came over HTTP or Playwright (Selenium cannot see it) |
| `retry_after` | Seconds from a `Retry-After` header on a 429 |
| `transport` | `impersonate`, `http`, `browser` or `playwright` |
| `blocked` | True for every status except `ok` and `empty` |

`str(attempt)` reads `mojeek: blocked (HTTP 403)`.

How a page is classified, in order:

1. **HTTP status first.** 429 is `rate_limited` (with `Retry-After`); 401, 403,
   407 and 503 are `blocked`; DuckDuckGo's 202 anomaly page is `blocked`; any
   other 4xx/5xx is `error`. A refusal is never reported as "no results",
   whatever its body looks like.
2. **Known bot checks** — challenge forms, CAPTCHA widgets, Google's
   `enablejs` wall, `/sorry/` pages — are `blocked`. URL markers are matched
   against the host and path only, so a search *for* "captcha" is not a captcha.
3. **No result containers at all** — then the page's wording decides: a
   CAPTCHA title or "unusual traffic" text is `blocked`; the engine's own
   "no results" marker is `empty`; anything else is **`unparsed`** — a block
   page with unknown markup, or a layout change. Neither is evidence of absence.
4. Results found but all filtered out (ads, off-`site:` rows) is `empty`.

| Status | Meaning | Evidence nothing exists? |
| --- | --- | --- |
| `ok` | Results extracted | – |
| `empty` | The engine looked and found nothing | Yes, from an engine in `engines_honoring_site()` |
| `blocked` | Refused: status code, bot check, soft block | No |
| `rate_limited` | HTTP 429; see `retry_after` | No |
| `unparsed` | A page we do not recognise | No |
| `timeout`, `unreachable`, `error` | Network or local failure | No |

`response.blocked` is True only when **every** attempt was a refusal. One engine
that genuinely had nothing makes the search an honest empty result.

### SearchResponse fields added in 1.1

| Field | Meaning |
| --- | --- |
| `mode` | `"first"` or `"aggregate"` |
| `engines` | Engines that contributed results |
| `skipped` | Engines not asked: `{"engine", "reason", "resume_in"}` with reason `cooling`, `ignores_site`, `browser_withdrawn`, `duplicate_provider` or `needs_browser` |
| `cooling` | True when nobody was asked because every eligible engine is standing down — "we chose not to ask", as opposed to `blocked`, "they refused us" |
| `rate_limited`, `retry_after` | Whether any engine sent 429, and the longest wait any asked for |
| `answered` | Attempts that were `ok` or `empty` |

```python
response = scraper.search(query)
if response.cooling:
    wait(min(s["resume_in"] for s in response.skipped))   # patience, not new evidence
elif response.blocked:
    back_off(response.retry_after or 60)                  # not the query's fault
elif not response:
    record_no_such_page()                                  # genuinely nothing to find
```

### Strict mode

```python
scraper = AdvancedSearchScraper(raise_on_block=True)
try:
    results = scraper.search(query)
except AllEnginesBlocked as e:
    e.response.attempts      # what each engine did
```

A blanket refusal raises, and so does a search where every engine was cooling;
an ordinary empty result never does.

---

## Transports and browserless mode

Most engines render results on the server, so they need an HTTP client and an
HTML parser rather than a browser.

| | Impersonate | HTTP | Browser (Selenium / Playwright) |
| --- | --- | --- | --- |
| Library | `curl_cffi` | `requests` | Chrome |
| TLS / HTTP/2 fingerprint | A real browser's | Python's | A real browser's |
| Typical search | ~1 s | sub-second, often refused | 1–9 s |
| Memory | a few MB | a few MB | 300 MB – 1 GB |
| Thread-safe | yes | yes | one session per thread |
| Needs Chrome installed | no | no | yes |

```python
AdvancedSearchScraper(transport="auto")         # default: impersonate, else http, else browser
AdvancedSearchScraper(transport="impersonate")  # browser fingerprint, never a browser
AdvancedSearchScraper(transport="http")         # plain requests, never a browser
AdvancedSearchScraper(transport="browser")      # always a browser
AdvancedSearchScraper(browser="playwright")     # which browser serves JS engines
```

**Why impersonation.** Anti-bot front ends (Cloudflare, Akamai, Google's own)
score the TLS ClientHello and HTTP/2 SETTINGS before they look at headers or
IP reputation. `requests` presents Python's OpenSSL on HTTP/1.1 while its
User-Agent claims to be Chrome — a mismatch detectable on sight, and acted on
from datacentre ranges. `ImpersonateTransport` presents a complete,
self-consistent browser: TLS, HTTP/2, header order and a matching User-Agent.

- Each engine gets its own session per thread, with a randomly chosen profile
  (`chrome`, `edge`, `safari`, `firefox`, `chrome_android`, `safari_ios`), so
  cookies never cross engines and a fleet does not share one fingerprint.
- After a refusal, that engine's session is rebuilt as a **different** browser.
- The User-Agent is never overridden unless you pass `user_agent=`; then the
  profiles are narrowed to the same browser family.
- An engine can pin a profile — `google_basic` pins `chrome_android` to match
  its mobile User-Agent.
- Proxy credentials (`http://user:pass@host:port`) work.

`transport="auto"` logs once, at INFO, which transport it chose, and suggests
the `impersonate` extra when it had to fall back to plain `requests`.
`headless-driver doctor` warns about the same thing.

Browser-only engines are dropped from the chain under `impersonate` and
`http`, which suits a container with no Chrome in it. Non-HTTP URLs
(`file://`, `data:`) always go through the browser.

A proxy set either way — `proxy=` or the older
`headless_options={"additional_args": ["--proxy-server=..."]}` — applies to
every transport, so switching transport never changes where your traffic comes
from.

Pages are parsed with `lxml` when it is installed (`[fast]` extra), otherwise
Python's own `html.parser`.

### Operators and ads

Sponsored slots are excluded: DuckDuckGo's `y.js` links, Bing's, Yahoo's and
Google's `aclick`/`aclk` endpoints and Brave's ad redirects are never returned
as results.

`site:` is enforced on the results, not merely passed to the engine. Bing
honours `site:example.com` but ignores a path like `site:example.com/in` and
answers with unrelated pages; such rows are dropped. With `strict_site=True`
(the default), engines known to ignore `site:` are not even asked for a
`site:` query unless you chose that engine explicitly.

```python
response = scraper.search('site:linkedin.com/in "Ada Lovelace"')
# every URL is under linkedin.com/in, or the response is empty
scraper.engines_honoring_site()   # {'brave', 'duckduckgo', 'yahoo', 'mojeek', ...}
```

---

## Aggregate mode

```python
response = scraper.search('site:linkedin.com/in "Jonnie Quinn" Credo Capital',
                          mode="aggregate",
                          engines=["brave", "duckduckgo", "yahoo", "mojeek", "google_basic"],
                          deadline=8.0, min_engines=2)
for hit in response:
    print(hit["votes"], hit["engines"], hit["ranks"], hit["url"])
```

One query goes to several engines at once and results are ranked by how many
agree. For identity search that is a strong signal: a profile returned by
Brave *and* DuckDuckGo *and* Mojeek is much likelier to be right than one only
Yahoo found.

1. Engines that are cooling down, ignore `site:` (for `site:` queries) or need a
   browser are left out and listed in `response.skipped`.
2. **One engine per provider.** DuckDuckGo and Yahoo are both Bing underneath,
   so asking both proves nothing: the first listed is asked, and its sibling
   only if it refuses.
3. Engines run in a thread pool; whatever has not answered by `deadline`
   seconds is recorded as `timeout`.
4. Results are merged by a normalised URL — host lower-cased, `www.`/`m.`
   dropped, LinkedIn country subdomains folded (`uk.linkedin.com` →
   `linkedin.com`), scheme, fragments, trailing slashes and tracking parameters
   (`utm_*`, `gclid`, `trk`, …) ignored. Pass `normalize=` to change it.
5. Each result keeps its best-ranked copy and the longest snippet any engine
   offered, and gains `votes`, `engines` and `ranks`.
6. Order: most votes, then best mean rank.
7. **Early exit:** once `min_engines` have answered and the top three results
   each have two or more votes, the rest are not waited for.

`response.engine` is `"aggregate"` and `response.engines` lists the
contributors. Aggregate mode needs a browserless transport. The same merge is
available on its own:

```python
from headless import merge_results, normalize_url
merge_results({"brave": [...], "mojeek": [...]}, engine_order=["brave", "mojeek"])
```

Two `site:`-honouring engines both answering `empty` is much stronger
evidence of absence than one.

---

## Circuit breaker

Once an engine refuses, asking it again straight away earns another refusal
and teaches it that this address keeps coming back. `EngineHealth` stands an
engine down:

- after `failures_before_backoff` (3) consecutive refusals (`blocked` or
  `rate_limited`), for `backoff_base` (15) seconds;
- a 429 stands it down at once, for as long as `Retry-After` asks;
- if it refuses again within one pause of resuming, the pause doubles, up to
  `backoff_max` (120); a refusal after a quiet spell starts from the base again;
- any real answer (`ok` or `empty`) resets it. Timeouts, unreachable hosts and
  `unparsed` pages neither trip nor reset it — they are not the engine's decision.

One instance is shared by every scraper in the process (`default_health()`),
because the address being throttled is shared too; `ScraperPool` workers
therefore stand down together.

```python
scraper.health()          # {"brave": {"state": "cooling", "resume_in": 12.0, "last_status": "rate_limited", ...}}
scraper.reset_health()    # stand everything back up
AdvancedSearchScraper(circuit_breaker=False)                    # opt out
AdvancedSearchScraper(health=EngineHealth(backoff_base=30))     # your own policy
```

State changes are logged once each — a WARNING when an engine starts cooling,
INFO when it answers again — rather than one line per blocked request.

**Browser withdrawal.** When two *independent* indexes refuse in the same
search, the address itself is probably throttled, and starting Chrome cannot
get past that (on ECS it cost up to 120 s and about 1 GB per query). With
`withdraw_browser_on_block=True` (the default) browser engines are then skipped
for `browser_cooldown` seconds (`browser_withdrawn` in `response.skipped`).

### Soft blocks

DuckDuckGo throttles by serving an ordinary results page with no results,
which looks exactly like a genuine absence. Two tools:

```python
scraper.probe("duckduckgo")          # EngineAttempt for a query that cannot be empty
AdvancedSearchScraper(verify_empty=True)
```

With `verify_empty=True`, an `empty` from a `site:`-honouring engine triggers
one control query on that engine (cached for 60 s). If the control query is
empty too, the original attempt is rewritten to `blocked` with reason
`soft block (control query empty)`.

---

## Playwright

Playwright drives the browser over its devtools protocol, which sees the HTTP
layer Selenium cannot. Install with `pip install "headless-driver[playwright]"`
and `playwright install chromium`.

**As the scraper's browser.** `AdvancedSearchScraper(browser="playwright")`
serves JavaScript engines (and `transport="browser"`) through Playwright:

- real **status codes and headers** from the browser, so a 429 with
  `Retry-After` is classified exactly as over HTTP;
- **resource blocking** — images, media and fonts are never downloaded;
- **stealth** — `navigator.webdriver` hidden, plugins, languages,
  `window.chrome` and WebGL vendor filled in, and the `HeadlessChrome`
  User-Agent replaced by the one the same build sends with a window;
- **one isolated context per engine**, rebuilt after a refusal;
- **proxies with credentials**.

**On its own**, `PlaywrightBrowser` is a scraping-oriented browser:

```python
from headless.playwright_driver import PlaywrightBrowser

with PlaywrightBrowser(headless=True, block_resources=True, proxy="http://u:p@host:8080",
                       locale="en-GB", timezone_id="Europe/London") as browser:
    html, status = browser.fetch_html("https://example.com")
    page = browser.fetch_page("https://example.com")        # a Page, with .status and .headers

    rows = browser.extract("https://news.ycombinator.com",
                           {"title": ".titleline > a", "link": ".titleline > a@href"},
                           item_selector="tr.athing", scroll=0)

    api = browser.capture_json("https://example.com/app", r"/api/",
                               action=lambda page: page.click("text=Load more"))

    browser.screenshot("https://example.com", "page.png", full_page=True)
    browser.pdf("https://example.com", "page.pdf")
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `browser` | `"chromium"` | `chromium`, `firefox` or `webkit` |
| `headless` | `True` | Run without a window |
| `proxy` | – | `scheme://user:pass@host:port` |
| `user_agent`, `locale`, `timezone_id` | derived, `en-US`, – | Set together so they agree |
| `viewport` | `(1366, 768)` | Window size |
| `device` | – | A Playwright device name, e.g. `"iPhone 13"` |
| `stealth` | `True` | Inject the stealth script and fix the headless User-Agent |
| `block_resources` | `True` | `True` blocks images, media and fonts; or pass resource types |
| `timeout` | `20.0` | Seconds for navigation and waits |
| `trace_dir` | – | Record a Playwright trace per context, saved as `<key>.zip` on close |
| `extra_headers`, `launch_args` | – | Passed through |

| Method | Returns | Notes |
| --- | --- | --- |
| `fetch_html(url, key, wait_for)` | `(html, status)` | |
| `fetch_page(url, key, wait_for)` | `Page` | Status, headers, parsed document |
| `extract(url, schema, item_selector=None, key, scroll=0)` | `dict` or `list` | Schema values are `css` or `css@attr`; text is normalised |
| `capture_json(url, pattern, key, wait=1.5, action=None)` | `list` | JSON bodies of responses whose URL matches |
| `scroll_to_bottom(page, rounds=10)` | `int` | Stops when the page stops growing |
| `screenshot(url, path, full_page=True)` | `bool` | Loads images for this even when blocking |
| `pdf(url, path)` | `bool` | Chromium only |
| `context(key)`, `rotate(key)` | – | Per-key isolated contexts; `rotate` discards cookies and storage |
| `close()` | – | Also works as a context manager |

Every method is safe to call from any thread: each thread gets its own
Playwright driver, shared by every `PlaywrightBrowser` on that thread.

---

## Parallel searching

One WebDriver session cannot be driven from several threads, so a single
browser-backed scraper cannot search in parallel — `search_batch` runs
sequentially and says so rather than pretending otherwise.

Three ways to get real concurrency:

**A browserless transport** — no session, no constraint:

```python
scraper = AdvancedSearchScraper(transport="impersonate")
responses = scraper.search_batch(queries, max_workers=8)
```

**Aggregate mode** — one query, several engines at once (see
[Aggregate mode](#aggregate-mode)).

**`ScraperPool`** — one browser per worker, for engines that need one:

```python
from headless import ScraperPool

with ScraperPool(size=4, proxy="http://…", search_engine="duckduckgo_js") as pool:
    for query, response in pool.map(queries):
        print(query, response.engine, len(response))
```

`ScraperPool(size, **scraper_kwargs)` passes everything else through to
`AdvancedSearchScraper`. A worker whose browser dies is recycled rather than
failing every later query. Workers share one circuit breaker.

| Method | Returns |
| --- | --- |
| `search(query, **kw)` | One `SearchResponse`, on this thread's scraper |
| `map(queries, **kw)` | Yields `(query, response)` as each finishes |
| `search_batch(queries, **kw)` | `{query: response}` |
| `quit()` | Closes every browser the pool opened |

---

## Search engines

`brave` is the default: it answers from datacentre addresses, honours `site:`
paths and returns snippets. Browserless engines come first, browser-only ones
late, and Bing — which ignores `site:` paths — last.

| # | Engine | Endpoint | Needs | Honours `site:` | Index |
| --- | --- | --- | --- | --- | --- |
| 1 | `brave` | `search.brave.com` | http | yes | brave |
| 2 | `duckduckgo` | `html.duckduckgo.com` (POST form) | http | yes | bing |
| 3 | `mojeek` | `www.mojeek.com` | http | yes | mojeek |
| 4 | `yahoo` | `search.yahoo.com` (random path tokens) | http | yes | bing |
| 5 | `google_basic` | `www.google.com` (Search-App client) | http | yes | google |
| 6 | `duckduckgo_lite` | `lite.duckduckgo.com` | http | yes | bing |
| 7 | `duckduckgo_js` | `duckduckgo.com` | browser | yes | bing |
| 8 | `startpage` | `www.startpage.com` | browser | yes | google |
| 9 | `google` | `www.google.com` | browser | yes | google |
| 10 | `yandex` | `yandex.com` | browser | yes | yandex |
| 11 | `bing` | `www.bing.com` | http | **no** | bing |

Notes, verified against live endpoints on 2026-09-29:

- **`duckduckgo`** now POSTs the HTML front end's own form (`q`, `b`, `l`),
  as the page itself does. Its 202 anomaly page is a block.
- **`yahoo`** needs fresh `_ylt`/`_ylu` path tokens per request; its
  `/RU=…/RK=` redirects are unwrapped. Bing-backed, but unlike scraped Bing it
  honours `site:` paths.
- **`google_basic`** asks as an old Android Chrome webview carrying the Google
  Search App token, with `CONSENT=YES+`, which Google serves a server-rendered
  page instead of its JavaScript wall — from some addresses. When it does not,
  the `enablejs` wall is detected as `blocked`. Keep the browser `google`
  engine for callers who want it.
- **`mojeek`** is now browserless. What looked like a "results-free stub" for
  HTTP clients is a JavaScript captcha, and is detected as a block.
- **`startpage`** fetches the home page's `sc` token before POSTing its form
  when used over HTTP (set `js: False` on your instance to try it; it stays
  browser-first by default).
- **`duckduckgo_lite`** returns titles and URLs but **no snippets**.

An engine is abandoned and the next one tried when it refuses, returns no
results, exceeds its timeout, or cannot be reached at all. The result is empty
only after every eligible engine has been tried — and `response.blocked` /
`response.cooling` say whether that was a refusal or a genuine absence.

### Capabilities

```python
scraper.capabilities("yahoo")
# {"js": False, "snippets": True, "honors_site": True, "provider": "bing",
#  "method": "GET", "url": "https://search.yahoo.com/search"}
```

### Engine specs

`ENGINE_SPECS` entries (and `register_engine()` specs) take:

| Key | Meaning |
| --- | --- |
| `url` | Endpoint. May contain `{query}` (legacy templates still work) |
| `method` | `"GET"` (default) or `"POST"` |
| `params` | `callable(query, region) -> dict`: the query string for GET, the form for POST |
| `headers`, `cookies` | A dict, or `callable(region) -> dict`, sent with each request |
| `url_builder` | `callable() -> url`, for per-request URL parts (Yahoo's tokens) |
| `prepare` | `callable(ctx)` pre-flight hook; `ctx` has `request`, `fetch`, `spec`, `query`, `region` |
| `impersonate` | Pin a TLS profile, e.g. `"chrome_android"` |
| `result`, `link`, `title`, `snippet` | CSS selectors; lists are tried in order |
| `unwrap` | `callable(href) -> href`, a redirect decoder |
| `no_results` | Selectors, or `"text:..."` substrings, marking a genuine "no results" page. With it, an unrecognised page is `unparsed`; without it, `empty` as before |
| `block_statuses` | Extra HTTP statuses that mean "refused" (DuckDuckGo's 202) |
| `js` | Needs a browser. Defaults to `True` for registered engines |
| `snippets` | Returns description text |
| `honors_site` | Respects `site:` paths. Registered engines default to `None`: neither trusted for absence nor skipped |
| `provider` | Whose index it is, for aggregate de-duplication |

Verify the engines still parse — selectors are someone else's markup and rot
without warning:

```bash
headless-driver doctor --engines
headless-driver doctor --engines --transport impersonate --query wikipedia
headless-driver bench --min-ok-rate 0.5
```

Click-tracking redirects are resolved for DuckDuckGo (`/l/?uddg=`), Bing
(`/ck/a`), Google (`/url?q=`) and Yahoo (`/RU=`).

```python
from headless import ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES, DEFAULT_AGGREGATE_ENGINES
```

---

## Timeouts

Selenium's own page load timeout is 300 seconds, long enough that one wedged
page looks like a hang. Every layer here sets a bound well below it.

| Setting | Default | Bounds |
| --- | --- | --- |
| `Headless(page_load_timeout=…)` | `30.0` | Any page load on that driver |
| `AdvancedSearchScraper(page_load_timeout=…)` | `20.0` | One engine's browser page load |
| `AdvancedSearchScraper(http_timeout=…)` | `min(8, page_load_timeout)` | One browserless fetch |
| `AdvancedSearchScraper(wait_timeout=…)` | `8.0` | Waiting for results to render |
| `AdvancedSearchScraper(deadline=…)` | `8.0` | A whole aggregate search |
| `SearchScraper(page_load_timeout=…, wait_timeout=…)` | `20.0`, `8.0` | As above |

Pass `page_load_timeout=None` to `Headless` to restore Selenium's default.

---

## Diagnostics and logging

Every library diagnostic goes to **stderr**, never stdout, so piping stdout
stays safe:

```bash
headless-driver search "python" --json 2>/dev/null | jq .
```

Everything the library has to say goes through the `headless` logger, which
carries a `NullHandler`, so an embedded copy stays silent until you ask:

```python
import logging

logging.getLogger("headless").setLevel(logging.INFO)      # route into your own logs
logging.getLogger("headless.scraper")                     # per-component children
```

Child loggers are `headless.core`, `headless.manager`, `headless.scraper`,
`headless.transport`, `headless.health`, `headless.playwright` and
`headless.bench`.

### Colour

Console output is coloured by level with a badge, the component tag gets a
stable colour of its own, and the message highlights what matters: HTTP status
codes, statuses (`blocked` and `rate_limited` in red, `cooling down` and
`timeout` in yellow, `ok` in green), URLs, quoted queries and numbers.

```
! WARN  [health] brave cooling down for 15s (HTTP 429)
ℹ INFO  [transport] using the impersonate transport
✗ ERROR [scraper] every engine refused 'site:linkedin.com/in x' (brave:rate_limited, duckduckgo:blocked)
· DEBUG [scraper] 10 results from brave
```

Three ways to turn it on:

```python
from headless import enable_console_logging, colorize_logging, ColorFormatter

enable_console_logging()                     # DEBUG to stderr, coloured
enable_console_logging(logging.WARNING, timestamps=True,
                       third_party=True,     # webdriver-manager, selenium, urllib3, curl_cffi too
                       capture_warnings=True)  # Python warnings, in the same style

logging.basicConfig(level=logging.INFO)
colorize_logging()                           # colour the app's own console handlers (terminals only)

handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter())       # or wire the formatter in yourself
```

Or from the environment, with no code change:

```bash
HEADLESS_DRIVER_LOG=info python my_app.py    # debug | info | warning | error | off
```

`verbose=True` on any class calls `enable_console_logging()` for you. The CLI
also renders third-party loggers and Python warnings, so nothing arrives as a
plain line between coloured ones. `colorize_logging()` only touches handlers
writing to a terminal (or with `FORCE_COLOR` set), so log files stay plain.
`NO_COLOR`, `FORCE_COLOR` and the other rules under
[Colour, piping and exit codes](#colour-piping-and-exit-codes) apply.

Emit your own with the same formatting:

```python
from headless.ui import diag, debug, info, success, warn, error

warn("[MyBot] retrying in 5s")
error("[MyBot] giving up")
diag("[MyBot] custom", level="success")
```

| Level | Badge | Body | Marker |
| --- | --- | --- | --- |
| `debug` | magenta | grey | `·` |
| `info` | bright blue | default, highlighted | `ℹ` |
| `success` | black on green | green | `✓` |
| `warn` | black on yellow | yellow | `!` |
| `error` | white on red | red | `✗` |

---

## Running the tests

From the repository root:

```bash
pip install -e ".[dev,fast]" && playwright install chromium
python -m unittest discover -s tests          # everything: unit, fixtures, e2e (360+ tests)
python -m unittest tests.test_v11             # 1.1 features, offline, fast
python -m unittest tests.test_cli             # CLI and UI, offline, instant
python -m unittest discover -s tests/e2e      # the end-to-end suite alone
coverage run -m unittest discover -s tests && coverage report   # ~90%
```

`SKIP_LIVE_TESTS=1` skips the few tests that hit real engines. Everything else
is hermetic:

- **Dated fixtures** in `tests/fixtures/<engine>_<case>_<YYYYMMDD>.html` are
  live pages captured from each engine — results, "no results", 403, 429,
  captcha and JavaScript-wall pages — so the date of the markup is visible.
- **The end-to-end suite** (`tests/e2e/`) serves a local site whose pages
  reproduce every engine's real markup, generated from `data.json` by
  `build_site.py`. The server behaves like the engines where it matters
  (DuckDuckGo's POST form, Yahoo's path tokens, Google's Search-App check,
  Startpage's `sc` token) and misbehaves on demand (`zzzblock`, `zzzslow`,
  `zzzcaptcha`, …). The scraper must return exactly the ground truth through
  every transport, Selenium and Playwright. `python tests/e2e/server.py` serves
  it for browsing.
- **From the installed wheel:** `scripts/e2e.sh` builds the wheel, installs it
  into a fresh virtualenv and runs the end-to-end suite from outside the source
  tree (`E2E_REQUIRE_INSTALLED=1` asserts the import came from
  `site-packages`). CI does this on Linux, macOS and Windows.

A single class or test:

```bash
python -m unittest tests.test_headless.TestFallbackChain
python -m unittest tests.test_v11.TestAggregate.test_results_are_ranked_by_agreement
```

`pytest` works unchanged if you prefer it.

---

## Deployment

Getting Chrome and a matching driver into a container is most of the work of
deploying browser automation. The [Dockerfile](Dockerfile) in the repository
does it, with the `impersonate`, `http` and `fast` extras:

```bash
docker build -t headless-driver .
docker run --rm --shm-size=1g headless-driver search "python headless"
docker run --rm headless-driver search 'site:linkedin.com/in "jane doe"' --mode aggregate
```

It pins Chrome and a matching chromedriver and bakes both in, so nothing is
downloaded at runtime; runs as a non-root user with a writable `HOME`; and uses
`tini` to reap the processes Chrome leaves behind.

`--shm-size` matters: Chrome's default `/dev/shm` in Docker is 64 MB and it
crashes on real pages without more.

**Or skip Chrome entirely.** Search needs no browser:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir "headless-driver[impersonate]"
```

That covers every engine marked `js: False` — the whole default chain up to
`duckduckgo_js`, and all of aggregate mode.

**Measure from where you deploy.** Run `headless-driver bench` once from the
target environment (an ECS task, a Lambda, a CI runner); a laptop cannot show
what a datacentre address sees. `.github/workflows/engine-bench.yml` runs it
weekly from GitHub's Azure runners.

---

## Troubleshooting

**Run `headless-driver doctor` first.** It answers most of the questions below.

| Symptom | Cause and fix |
| --- | --- |
| Searches return `[]` from a datacentre but work locally | Engines refuse cloud address ranges. Install `headless-driver[impersonate]` (doctor warns when it is missing), check `response.blocked`/`response.rate_limited` rather than treating it as "not found", try `mode="aggregate"`, and use a proxy or residential egress if every engine refuses. |
| `response` is empty and `response.cooling` is True | Every eligible engine is standing down after refusals. Wait `resume_in` seconds, or `scraper.reset_health()` if you know the block has lifted. |
| An engine reports `unparsed` | Its page had no result containers and no "no results" marker: an unknown block page or a layout change. Run `headless-driver doctor --engines`; if it persists, the selectors need updating. |
| `empty` from DuckDuckGo that seems wrong | A soft block. Use `verify_empty=True`, or check with `scraper.probe("duckduckgo")`. |
| A long-running worker grows in memory | `keep_history=True` retains every result. Leave it off (the default), or lower `history_limit`. |
| `SessionNotCreatedException`, "only supports Chrome version N" | The chromedriver on `PATH` is stale. A matching one is downloaded automatically. |
| Playwright: "Executable doesn't exist" | Run `playwright install chromium`. The attempt is reported as `error` with that hint. |
| Searches feel slow | Install `[impersonate]` so no browser is started; lower `http_timeout`; use `mode="aggregate"` with a `deadline`. Browser engines are what cost time. |
| Screenshot or PDF is blank | Navigate before capturing: `driver.get(url)` then `hl.screenshot(path)`; or use `PlaywrightBrowser.screenshot(url, path)`. |
| Chrome fails to start on a server or in Docker | Already handled by `--no-sandbox` and `--disable-dev-shm-usage`. Make sure a browser is installed. |
| Detected as a bot | Prefer `transport="impersonate"`, or `browser="playwright"` (stealth on by default). With Selenium, `ExtendedHeadless(stealth=True)`. Add a `proxy`. No approach is reliable against every engine. |
| Plain, uncoloured log lines from your own logging setup | Call `headless.colorize_logging()` after `logging.basicConfig()`, or use `ColorFormatter`. |
