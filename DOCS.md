# headless-driver documentation

Complete reference for the Python API and the `headless-driver` command line
tool. For a quick tour, see the [README](README.md).

- [Install](#install)
- [Command line](#command-line)
  - [search](#search)
  - [engines](#engines)
  - [doctor](#doctor)
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
- [Search engines](#search-engines)
- [Timeouts](#timeouts)
- [Diagnostics and logging](#diagnostics-and-logging)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)

---

## Install

```bash
pip install headless-driver
```

Requires Python 3.9+ and an installed Chrome or Chromium. A matching
ChromeDriver is downloaded automatically when needed, so you usually do not
have to install one yourself.

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
| `doctor` | Check Chrome, ChromeDriver and connectivity |
| `shot URL` | Save a screenshot |
| `pdf URL` | Save the page as PDF |

### search

```bash
headless-driver search "python headless browser"
headless-driver search "selenium stealth" -n 10 -e bing
headless-driver search "web scraping" --save results.csv
headless-driver search "python" --json | jq -r '.results[].url'
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-n`, `--number` | `5` | Results to return |
| `-e`, `--engine` | `duckduckgo` | Engine to start with |
| `--no-fallback` | off | Do not try other engines if this one fails |
| `--save PATH` | – | Also write results to `.json` or `.csv` |
| `--timeout` | `20.0` | Page load timeout in seconds |
| `--json` | off | Print JSON instead of formatted output |

`--json` emits a single object:

```json
{
  "query": "python headless",
  "engine": "duckduckgo",
  "elapsed": 4.7,
  "results": [
    {
      "url": "https://example.com/page",
      "title": "Example page",
      "snippet": "…",
      "favicon": "https://www.google.com/s2/favicons?domain=example.com",
      "cached": null,
      "quick_answer": null,
      "engine": "duckduckgo"
    }
  ]
}
```

### engines

```bash
headless-driver engines
headless-driver engines --json
```

Prints each engine, its endpoint host, and its position in the fallback chain.

### doctor

```bash
headless-driver doctor
```

The quickest way to explain a failing run. It reports the installed versions,
flags a Chrome/ChromeDriver mismatch, checks that each engine host is
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

### shot and pdf

```bash
headless-driver shot https://example.com -o page.png --window 1280x720
headless-driver pdf  https://example.com -o page.pdf
headless-driver shot https://example.com --proxy socks5://127.0.0.1:9050
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--output` | `screenshot.png` / `page.pdf` | File to write |
| `--window WxH` | `1920x1080` | Browser window size |
| `--proxy` | – | Proxy server URL |
| `--timeout` | `30.0` | Page load timeout in seconds |

Missing parent directories are created for you.

### Colour, piping and exit codes

Colour is enabled only when stdout is a terminal, so piping or redirecting
yields plain text automatically.

| Control | Effect |
| --- | --- |
| `--no-color` | Disable colour for this run |
| `NO_COLOR=1` | Disable colour (takes precedence over `FORCE_COLOR`) |
| `FORCE_COLOR=1` | Enable colour even when not a terminal |
| `TERM=dumb` | Disable colour |

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
    AdvancedSearchScraper, SearchScraper,
    find_chrome_binary, find_chromedriver_path, install_chromedriver,
    ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES,
)
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

Scrapes search results, walking a chain of engines until one answers.

```python
AdvancedSearchScraper(
    driver=None,
    max_results: int = 10,
    result_processor: Optional[Callable[[str, Dict], Dict]] = None,
    headless_options: Optional[dict] = None,
    search_engine: str = "duckduckgo",
    verbose: bool = False,
    fallback: bool = True,
    fallback_engines: Optional[Sequence[str]] = None,
    page_load_timeout: float = 20.0,
    wait_timeout: float = 8.0,
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
| `page_load_timeout` | Seconds one engine may take to load before it is abandoned. |
| `wait_timeout` | Seconds to wait for results to appear once the page has loaded. |

**Attributes**

| Attribute | Meaning |
| --- | --- |
| `results` | Every result accumulated across all searches, for `export()`. |
| `last_engine` | Engine that produced the most recent results, or `None`. |
| `engines` | This instance's engine registry, a copy of `ENGINE_SPECS`. |

**Methods**

| Method | Returns | Notes |
| --- | --- | --- |
| `search(query, max_results=None, engine=None)` | `List[Dict]` | Walks the chain. Passing `engine` forces one engine and skips the chain. |
| `search_batch(queries, max_workers=4, per_query=None)` | `Dict[str, List[Dict]]` | Runs several queries; a failing query maps to `[]`. |
| `export(path)` | `bool` | Writes `results` to `.json` or `.csv`. Any other extension returns `False`. |
| `register_engine(name, spec)` | `None` | Adds or overrides an engine on this instance. |
| `default_result_processor(query, item)` | `Dict` | The identity processor used when `result_processor` is not supplied. |
| `quit()` | `None` | Quits the driver if this object created it. |

Each result is a dict:

| Key | Meaning |
| --- | --- |
| `url` | Destination URL, with click-tracking redirects resolved |
| `title` | Result heading |
| `snippet` | Description text, `""` when the engine omits one |
| `favicon` | Favicon URL derived from the domain |
| `cached` | Cached-page link when the engine offers one |
| `quick_answer` | Reserved, currently `None` |
| `engine` | Engine that produced this result |

```python
from headless import AdvancedSearchScraper

with AdvancedSearchScraper(max_results=5) as scr:
    for item in scr.search("python headless browser"):
        print(item["title"], item["url"])
    print("answered by", scr.last_engine)
```

Choosing engines:

```python
AdvancedSearchScraper(search_engine="bing")                        # different start
AdvancedSearchScraper(fallback=False)                              # no chain
AdvancedSearchScraper(fallback_engines=["bing", "mojeek"])          # custom order
scr.search("query", engine="bing")                                 # force, one-off
```

Batch searches and export:

```python
scr = AdvancedSearchScraper(max_results=3)
batch = scr.search_batch(["python asyncio", "python typing"], max_workers=2)
scr.export("results.json")
scr.export("results.csv")
scr.quit()
```

A WebDriver session is not thread-safe, so `search_batch` serialises access to
the browser. Threads shorten the wait around a single driver rather than
driving several at once; use `MultiDriverManager` with one scraper per driver
for true parallelism.

Custom result shape:

```python
def only_domain(query, item):
    from urllib.parse import urlparse
    return {"query": query, "domain": urlparse(item["url"]).netloc}

scr = AdvancedSearchScraper(result_processor=only_domain)
```

Adding your own engine — `spec` needs `url` (containing `{query}`), `result`,
`link`, `title` and `snippet`; the selector lists are tried in order:

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

`install_chromedriver()` needs `webdriver-manager` and returns `None` when it is
not installed. All three work on Linux, macOS and Windows.

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
| `table(headers, rows, styles)` | Width-aware table, last column truncated to fit |
| `bar(good, bad, warn, width)` | Diffstat-style bar: green `+`, yellow `~`, red `-` |
| `spinner(label)` | Context manager; inert off a terminal |
| `is_terminal`, `width` | Stream properties |

Style names: `bold`, `dim`, `italic`, `underline`, `red`, `green`, `yellow`,
`blue`, `magenta`, `cyan`, `white`, `grey`.

Module functions: `supports_color(stream)`, `supports_unicode(stream)`,
`visible_width(text)` (ignores ANSI), `truncate(text, limit)`, and
`diag(message)` which writes to stderr.

---

## Search engines

`duckduckgo` is the default because its endpoint renders server-side: there is
no JavaScript to execute, so a search takes a fraction of the time the
JavaScript front end needs.

| # | Engine | Endpoint |
| --- | --- | --- |
| 1 | `duckduckgo` | `html.duckduckgo.com` |
| 2 | `duckduckgo_lite` | `lite.duckduckgo.com` |
| 3 | `bing` | `www.bing.com` |
| 4 | `mojeek` | `www.mojeek.com` |
| 5 | `duckduckgo_js` | `duckduckgo.com` |
| 6 | `startpage` | `www.startpage.com` |
| 7 | `google` | `www.google.com` |
| 8 | `yandex` | `yandex.com` |

An engine is abandoned and the next one tried when it serves a bot check,
returns no results, exceeds `page_load_timeout`, or cannot be reached at all.
`search()` returns `[]` only after every engine has been tried.

Search engines defend aggressively against automation, and Google in particular
serves a CAPTCHA to headless browsers on most networks — which is exactly why
the chain exists. Blocks are reported when `verbose=True`.

Click-tracking redirects are resolved to real destinations for DuckDuckGo
(`/l/?uddg=`), Bing (`/ck/a`) and Google (`/url?q=`).

The constants are importable:

```python
from headless import ENGINE_SPECS, DEFAULT_ENGINE, DEFAULT_FALLBACK_ENGINES
```

---

## Timeouts

Selenium's own page load timeout is 300 seconds, long enough that one wedged
page looks like a hang. Every layer here sets a bound well below it.

| Setting | Default | Bounds |
| --- | --- | --- |
| `Headless(page_load_timeout=…)` | `30.0` | Any page load on that driver |
| `AdvancedSearchScraper(page_load_timeout=…)` | `20.0` | One engine's page load |
| `AdvancedSearchScraper(wait_timeout=…)` | `8.0` | Waiting for results to render |
| `SearchScraper(page_load_timeout=…, wait_timeout=…)` | `20.0`, `8.0` | As above |

Pass `page_load_timeout=None` to `Headless` to restore Selenium's default.

---

## Diagnostics and logging

Every library diagnostic goes to **stderr**, never stdout, so piping stdout
stays safe:

```bash
headless-driver search "python" --json 2>/dev/null | jq .
```

`verbose=True` on any class prints its setup and teardown steps. In your own
code, `headless.ui.diag(message)` writes one diagnostic line to stderr.

---

## Running the tests

From the repository root:

```bash
python -m unittest discover -s tests          # everything, 84 tests
python -m unittest tests.test_cli             # CLI and UI, offline, instant
python -m unittest tests.test_headless        # driver and scraping
python -m unittest discover -s tests -v       # name every test
```

A single class or test:

```bash
python -m unittest tests.test_headless.TestFallbackChain
python -m unittest tests.test_cli.TestConsole.test_bar_shows_every_non_zero_segment
```

`pytest` works unchanged if you prefer it:

```bash
pytest tests -q
pytest tests/test_cli.py -k bar -v
```

The fallback tests drive local HTML fixtures over `file://`, so they need no
network. Browser and live-search tests skip themselves when Chrome or the
network is unavailable.

---

## Troubleshooting

**Run `headless-driver doctor` first.** It answers most of the questions below.

| Symptom | Cause and fix |
| --- | --- |
| `SessionNotCreatedException`, "only supports Chrome version N" | The chromedriver on `PATH` is stale. A matching one is downloaded automatically; `brew upgrade chromedriver` or `apt install --only-upgrade chromium-driver` silences the warning. |
| Search returns `[]` | Every engine was blocked or unreachable. Run with `verbose=True` to see which, and check `doctor`'s connectivity section. |
| Searches feel slow | Lower `page_load_timeout` and `wait_timeout`, or pin a fast engine with `fallback=False`. Blocked engines are what cost time, since each is attempted in turn. |
| Screenshot or PDF is blank | Navigate before capturing: `driver.get(url)` then `hl.screenshot(path)`. |
| Chrome fails to start on a server or in Docker | Already handled by `--no-sandbox` and `--disable-dev-shm-usage`. Make sure a browser is installed: `apt install -y chromium chromium-driver`. |
| `--json` output will not parse | Fixed in current versions, where diagnostics go to stderr. Add `2>/dev/null` if an older version is installed. |
| Detected as a bot | Try `ExtendedHeadless(stealth=True)`, a real `user_agent`, and a `proxy`. No approach is reliable against every engine. |
