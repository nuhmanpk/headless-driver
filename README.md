<div align="center">

<img src="https://raw.githubusercontent.com/nuhmanpk/headless-driver/main/images/logo.png" alt="headless-driver" width="560" />

# headless-driver

**Headless Chrome automation and a multi-engine search scraper for Python.**<br/>
Proxy and stealth support, automatic driver installation, screenshots and PDF export,
multi-driver management, and a colourful CLI.

<a href="https://pypi.org/project/headless-driver/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/headless-driver?style=flat-square&logo=pypi&logoColor=white&color=3775A9" /></a>
<a href="https://pypi.org/project/headless-driver/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/headless-driver?style=flat-square&logo=python&logoColor=white" /></a>
<a href="https://pepy.tech/project/headless-driver"><img alt="Downloads" src="https://img.shields.io/pypi/dm/headless-driver?style=flat-square&logo=pypi&logoColor=white&color=orange" /></a>
<a href="https://pypi.org/project/headless-driver/"><img alt="Package status" src="https://img.shields.io/pypi/status/headless-driver?style=flat-square" /></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/pypi/l/headless-driver?style=flat-square&color=yellow" /></a>

<a href="https://github.com/nuhmanpk/headless-driver/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/nuhmanpk/headless-driver/tests.yml?style=flat-square&logo=githubactions&logoColor=white&label=tests" /></a>
<img alt="Selenium" src="https://img.shields.io/badge/selenium-4.35-43B02A?style=flat-square&logo=selenium&logoColor=white" />
<img alt="Chrome headless" src="https://img.shields.io/badge/chrome-headless-4285F4?style=flat-square&logo=googlechrome&logoColor=white" />
<img alt="Platforms" src="https://img.shields.io/badge/platform-linux%20%7C%20macOS%20%7C%20windows-lightgrey?style=flat-square" />

<a href="https://github.com/nuhmanpk/headless-driver/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/nuhmanpk/headless-driver?style=flat-square&logo=github&logoColor=white&color=f5c518" /></a>
<a href="https://github.com/nuhmanpk/headless-driver/network/members"><img alt="Forks" src="https://img.shields.io/github/forks/nuhmanpk/headless-driver?style=flat-square&logo=github&logoColor=white" /></a>
<a href="https://github.com/nuhmanpk/headless-driver/issues"><img alt="Issues" src="https://img.shields.io/github/issues/nuhmanpk/headless-driver?style=flat-square&logo=github&logoColor=white" /></a>
<a href="https://github.com/nuhmanpk/headless-driver/commits/main"><img alt="Last commit" src="https://img.shields.io/github/last-commit/nuhmanpk/headless-driver?style=flat-square&logo=git&logoColor=white" /></a>
<a href="https://github.com/nuhmanpk/headless-driver/pulls"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" /></a>

<a href="https://github.com/nuhmanpk"><img alt="Sponsor" src="https://img.shields.io/badge/sponsor-nuhmanpk-EA4AAA?style=flat-square&logo=githubsponsors&logoColor=white" /></a>
<a href="https://buymeacoffee.com/nuhmanpk"><img alt="Buy me a coffee" src="https://img.shields.io/badge/buy%20me%20a%20coffee-nuhmanpk-FFDD00?style=flat-square&logo=buymeacoffee&logoColor=black" /></a>

<br/>

[**Documentation**](DOCS.md) · [Quick start](#quick-start) · [CLI](#command-line) · [Engines](#search-engines) · [PyPI](https://pypi.org/project/headless-driver/) · [Issues](https://github.com/nuhmanpk/headless-driver/issues)

</div>

---

## Features

- Headless and windowed Chrome management, with temporary or persistent profiles
- HTTP and SOCKS proxy support, plus optional stealth mode
- Automatic ChromeDriver installation, and recovery when the installed driver does not match your Chrome
- Screenshot and PDF export via Chrome DevTools
- Download folder management
- Multi-driver manager for many isolated browser instances
- Search scraping across 8 engines, starting with DuckDuckGo's no-JavaScript endpoint and falling back automatically when one blocks you
- **Tells you when you were blocked** — "nobody has an answer" and "everybody refused me" are different results, not both an empty list
- **Browserless mode** — server-rendered engines fetched over HTTP: sub-second instead of seconds, megabytes instead of a gigabyte, and thread-safe
- `ScraperPool` for genuinely parallel searching, one browser per worker
- Standard `logging` throughout: silent until your application asks
- Bounded page-load timeouts, so a stalled engine fails over instead of hanging
- A `headless-driver` CLI with coloured output, `--json` for piping, and a `doctor` command that diagnoses your setup
- Colour everywhere it makes sense — macOS, Linux, Windows consoles and CI log viewers — with no extra dependency, degrading to plain ASCII when it does not

## Install

```bash
pip install headless-driver              # browser only
pip install "headless-driver[http]"      # recommended: adds browserless mode
```

Needs Python 3.9+ and an installed Chrome or Chromium. A matching ChromeDriver
is downloaded automatically when required.

On Debian/Ubuntu you can install a system browser and driver with:

```bash
sudo apt update && sudo apt install -y chromium chromium-driver
```

## Quick start

```python
from headless import Headless

with Headless() as driver:
    driver.get("https://example.com")
    print(driver.title)
```

Search the web — no browser needed for most engines:

```python
from headless import AdvancedSearchScraper

with AdvancedSearchScraper(max_results=5) as scraper:
    response = scraper.search("python headless browser")

    for item in response:                 # iterates like a list
        print(item["title"], item["url"])

    print("answered by", response.engine)
    if response.blocked:                  # every engine refused, not "no results"
        print("blocked:", [a.reason for a in response.refused])
```

Being told you were blocked is the difference between backing off and recording
a false negative. Engines CAPTCHA cloud address ranges, so the same code that
works on a laptop returns nothing from ECS — `response.blocked` says which
happened.

Search in parallel:

```python
from headless import ScraperPool

with ScraperPool(size=4) as pool:
    for query, response in pool.map(["python asyncio", "python typing"]):
        print(query, response.engine, len(response))
```

Screenshot and PDF:

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(stealth=True)
driver = hl.get_driver()
driver.get("https://example.com")

hl.screenshot("example.png")
hl.save_pdf("example.pdf")
hl.quit()
```

## Command line

```bash
headless-driver search "python headless browser" -n 5
headless-driver search "selenium stealth" --json | jq -r '.results[].url'
headless-driver search "python" --transport http   # no browser at all
headless-driver engines                    # engines, capabilities, fallback order
headless-driver doctor                     # check chrome, driver, connectivity
headless-driver doctor --engines           # check the engines still parse
headless-driver shot https://example.com -o page.png --window 1280x720
headless-driver pdf  https://example.com -o page.pdf
```

`doctor` is the quickest way to explain a failing run:

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

Colour switches off automatically when output is not a terminal, so piping
gives plain text. `NO_COLOR=1` or `--no-color` disables it; `FORCE_COLOR=1`
forces it on. Diagnostics go to stderr, keeping `--json` on stdout clean.
Exit status is `0` on success, `1` when nothing could be produced, `2` for a
usage error.

See [DOCS.md](DOCS.md#command-line) for every command and flag.

## Search engines

Searching starts at DuckDuckGo's no-JavaScript endpoint, which returns
server-rendered HTML and so is much faster than the JavaScript front end. If an
engine serves a bot check, returns nothing, or stalls, the next one is tried:

```
duckduckgo → duckduckgo_lite → bing → mojeek
           → duckduckgo_js → startpage → google → yandex
```

`search()` returns `[]` only once every engine has been tried.

```python
AdvancedSearchScraper(search_engine="bing")                # start elsewhere
AdvancedSearchScraper(fallback=False)                      # single engine
AdvancedSearchScraper(fallback_engines=["bing", "mojeek"])  # custom order
scraper.search("query", engine="bing")                     # force, one-off
```

Add your own engine with `register_engine()`. Full details, including the result
dict and redirect handling, are in
[DOCS.md](DOCS.md#search-engines).

Each engine declares what it can do — whether it needs a browser (`js`) and
whether it returns description text (`snippets`) — so you do not have to
discover it by observation:

```python
scraper.capabilities("duckduckgo_lite")
# {"js": False, "snippets": False, ...}   -> fast, but match on titles
```

## More

- [Full documentation](DOCS.md) — every class, argument and CLI flag
- [Changelog](CHANGELOG.md) — what changed in 1.0, and the two breaking changes
- [Search results](DOCS.md#search-results) — `SearchResponse`, `blocked`, per-engine attempts
- [Transports](DOCS.md#transports-and-browserless-mode) — running without Chrome
- [Deployment](DOCS.md#deployment) — the Dockerfile, and skipping Chrome entirely
- [Python API](DOCS.md#python-api) — `Headless`, `ExtendedHeadless`, `MultiDriverManager`, `AdvancedSearchScraper`, `SearchScraper`
- [Timeouts](DOCS.md#timeouts) and [troubleshooting](DOCS.md#troubleshooting)
- [Running the tests](DOCS.md#running-the-tests)

## Support

If this project saves you time, you can support its development:

[![Sponsor](https://img.shields.io/badge/sponsor-nuhmanpk-EA4AAA?style=flat-square&logo=githubsponsors&logoColor=white)](https://github.com/nuhmanpk)
[![Buy me a coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-nuhmanpk-FFDD00?style=flat-square&logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/nuhmanpk)

- Sponsor on GitHub — [github.com/nuhmanpk](https://github.com/nuhmanpk)
- Buy me a coffee — [buymeacoffee.com/nuhmanpk](https://buymeacoffee.com/nuhmanpk)

Starring the [repository](https://github.com/nuhmanpk/headless-driver) helps too.

## License

MIT — see [LICENSE](LICENSE).

[Happy coding 🚀](https://github.com/nuhmanpk/)
