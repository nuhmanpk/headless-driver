<div align="center">

<img src="https://raw.githubusercontent.com/nuhmanpk/headless-driver/main/images/logo.png" alt="headless-driver" width="560" />

# headless-driver

### Web search for Python and AI agents

**Search the web, read any page as clean Markdown, and give your AI agent both.**<br/>
Free, fast, no API key.

<a href="https://pypi.org/project/headless-driver/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/headless-driver?style=flat-square&logo=pypi&logoColor=white&color=3775A9" /></a>
<a href="https://pypi.org/project/headless-driver/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/headless-driver?style=flat-square&logo=python&logoColor=white" /></a>
<a href="https://pepy.tech/project/headless-driver"><img alt="Downloads" src="https://img.shields.io/pypi/dm/headless-driver?style=flat-square&logo=pypi&logoColor=white&color=orange" /></a>
<a href="https://pypi.org/project/headless-driver/"><img alt="Package status" src="https://img.shields.io/pypi/status/headless-driver?style=flat-square" /></a>
<a href="LICENSE"><img alt="License" src="https://img.shields.io/pypi/l/headless-driver?style=flat-square&color=yellow" /></a>

<a href="https://github.com/nuhmanpk/headless-driver/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/nuhmanpk/headless-driver/tests.yml?style=flat-square&logo=githubactions&logoColor=white&label=tests" /></a>
<a href="https://codecov.io/gh/nuhmanpk/headless-driver"><img alt="Coverage" src="https://img.shields.io/codecov/c/github/nuhmanpk/headless-driver?style=flat-square&logo=codecov&logoColor=white" /></a>
<img alt="Selenium" src="https://img.shields.io/badge/selenium-4.35-43B02A?style=flat-square&logo=selenium&logoColor=white" />
<img alt="Playwright" src="https://img.shields.io/badge/playwright-supported-2EAD33?style=flat-square&logo=playwright&logoColor=white" />
<img alt="MCP" src="https://img.shields.io/badge/MCP-server-8A2BE2?style=flat-square" />
<a href="https://nuhmanpk.github.io/headless-driver/"><img alt="Docs" src="https://img.shields.io/badge/docs-site-blue?style=flat-square&logo=materialformkdocs&logoColor=white" /></a>
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

[**Documentation**](DOCS.md) · [Quick start](#quick-start) · [Performance](#performance) · [CLI](#command-line) · [Engines](#search-engines) · [Roadmap](TODO.md) · [PyPI](https://pypi.org/project/headless-driver/)

</div>

---

## What you can do

- **Search the web from Python** — Brave, DuckDuckGo, Yahoo, Mojeek, Google
  and Bing. No API key, about a second per search, no browser needed.
- **Get answers you can trust** — ask several engines at once and rank results
  by how many agree (`mode="aggregate"`).
- **Read any page as Markdown** — `fetch_markdown(url)` strips menus, ads and
  cookie banners, keeps the content, counts tokens and chunks it for RAG.
- **Give your AI agent the web** — ready-made tools for OpenAI, Anthropic,
  LangChain, LlamaIndex and CrewAI, and an MCP server for Claude, Cursor and
  VS Code: `headless-driver mcp`.
- **Know when you're blocked** — a refusal is reported as `blocked` or
  `rate_limited`, never as "no results".
- **Automate a browser when you need one** — Selenium or Playwright,
  screenshots, PDF, structured extraction.

### How it keeps working

- **Looks like a real browser.** Requests carry a genuine Chrome, Edge, Safari
  or Firefox TLS and HTTP/2 fingerprint (`curl_cffi`) — the first thing
  anti-bot systems check, and why plain Python scrapers are refused from cloud
  servers. The profile rotates after a refusal.
- **Asks each engine the way its own website does** — DuckDuckGo's POST form,
  Yahoo's path tokens, Google's Search-App client, regional cookies.
- **Backs off by itself.** An engine that refuses is stood down and retried
  later, with longer pauses if it keeps refusing; Chrome is not launched when
  the address itself is throttled.
- **Caches answers** in memory, SQLite or Redis, so pipelines and agents never
  ask the same thing twice.

### Features

- 11 engines: **Brave**, **DuckDuckGo** (HTML POST form, Lite, JS), **Yahoo**,
  **Mojeek**, **Google** (basic Search-App endpoint and browser), **Startpage**,
  **Yandex**, **Bing**
- Search modes `first` (fallback chain) and `aggregate` (parallel fan-out,
  consensus `votes`, deadline); `pages=N` pagination; `site:` enforced
- `fetch_markdown()`: main-content extraction, absolute links, code and tables
  kept, token counts, overlapping chunks with heading paths, automatic
  JavaScript rendering for app-shell pages
- Agent tools: `Toolkit` → `openai_tools()`, `anthropic_tools()`,
  `langchain_tools()`, `llamaindex_tools()`, `crewai_tools()`, tool-call handlers
- MCP server: `search`, `search_aggregate`, `fetch_page`, `extract`,
  `screenshot` over stdio or HTTP (MCP SDK 1.x and 2.x)
- Result cache: `cache="memory"`, `"sqlite:///path.db"`, `"redis://…"`
- Typed results: `response.typed()` dataclasses, `response.to_pydantic()`
- Honest `SearchResponse`: per-engine attempts with status, HTTP code,
  `Retry-After`, transport and timing; `blocked`, `cooling`, `cached`, `skipped`
- Per-engine circuit breaker, soft-block detection, regional results
- Transports: `impersonate` (default), plain `http`, Selenium or Playwright
- CLI: `search`, `fetch`, `mcp`, `extract`, `engines`, `doctor`, `bench`,
  `shot`, `pdf` — coloured output, `--json` for piping
- Tested: 410+ tests, ~90% coverage, and an end-to-end suite run from the
  installed wheel on Linux, macOS and Windows
- [Documentation site](https://nuhmanpk.github.io/headless-driver/) with guides
  and an API reference

## Install

```bash
pip install "headless-driver[impersonate]"   # recommended: fast browserless search
pip install "headless-driver[playwright]"    # Playwright backend (then: playwright install chromium)
pip install "headless-driver[all]"           # everything
pip install headless-driver                  # Selenium only
```

| Extra | Adds | Use it for |
| --- | --- | --- |
| `impersonate` | `curl_cffi`, `beautifulsoup4` | Browser TLS fingerprint; what `transport="auto"` prefers. The one to use from the cloud. |
| `http` | `requests`, `beautifulsoup4` | Plain browserless fallback |
| `playwright` | `playwright` | `browser="playwright"`, `extract()`, `capture_json()`, rendering JavaScript pages in `fetch_markdown()` |
| `mcp` | `mcp` | `headless-driver mcp` (Python 3.10+) |
| `agents` | `langchain-core`, `pydantic` | `Toolkit.langchain_tools()` and typed tool schemas |
| `tokens` | `tiktoken` | Exact token counts in `fetch_markdown()` |
| `redis` | `redis` | `cache="redis://…"` |
| `fast` | `lxml` | Faster parsing at volume, used automatically |

Needs Python 3.9+. Search needs no browser at all; browser automation needs an
installed Chrome or Chromium (a matching ChromeDriver is downloaded
automatically), or Playwright's own build via `playwright install chromium`.

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

Search the web — no browser needed:

```python
from headless import AdvancedSearchScraper

with AdvancedSearchScraper(max_results=5) as scraper:
    response = scraper.search("python headless browser")

    for item in response:                 # iterates like a list
        print(item["title"], item["url"])

    print("answered by", response.engine)
    if response.blocked:                  # every engine refused, not "no results"
        print("blocked:", [str(a) for a in response.refused])
```

Read a page as Markdown, ready for an LLM:

```python
from headless import fetch_markdown

doc = fetch_markdown("https://docs.python.org/3/library/asyncio.html", chunk_tokens=500)
print(doc.title, doc.tokens)
for chunk in doc.chunks:
    print(chunk.heading, chunk.tokens)
```

Give an agent web search and page reading:

```python
from headless import Toolkit

toolkit = Toolkit()
response = client.messages.create(model="claude-sonnet-5", tools=toolkit.anthropic_tools(),
                                  messages=messages, max_tokens=2048)
messages.append({"role": "user", "content": toolkit.handle_anthropic_tool_use(response.content)})
# also: openai_tools() / handle_openai_tool_calls(), langchain_tools(),
#       llamaindex_tools(), crewai_tools()
```

Or connect Claude Desktop, Claude Code, Cursor or VS Code over MCP:

```json
{"mcpServers": {"web": {"command": "headless-driver", "args": ["mcp"]}}}
```

Ask several engines at once and rank by agreement:

```python
with AdvancedSearchScraper(region="uk-en") as scraper:
    response = scraper.search('site:linkedin.com/in "Jane Doe" Credo Capital',
                              mode="aggregate", deadline=8)
    for hit in response:
        print(hit["votes"], hit["engines"], hit["url"])
    # 3 ['brave', 'duckduckgo', 'mojeek'] https://www.linkedin.com/in/jane-doe
```

Being told you were blocked is the difference between backing off and recording
a false negative. Engines CAPTCHA cloud address ranges, so the same code that
works on a laptop can get nothing from ECS — `response.blocked`,
`response.rate_limited`, `response.retry_after` and `response.cooling` say which
happened.

Search in parallel:

```python
from headless import ScraperPool

with ScraperPool(size=4) as pool:
    for query, response in pool.map(["python asyncio", "python typing"]):
        print(query, response.engine, len(response))
```

Playwright, with structured extraction:

```python
from headless.playwright_driver import PlaywrightBrowser

with PlaywrightBrowser(block_resources=True) as browser:
    rows = browser.extract("https://news.ycombinator.com",
                           {"title": ".titleline > a", "link": ".titleline > a@href"},
                           item_selector="tr.athing")
    api = browser.capture_json("https://example.com/app", r"/api/")   # read the site's own JSON
    browser.screenshot("https://example.com", "page.png", full_page=True)
```

Screenshot and PDF with Selenium:

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(stealth=True)
driver = hl.get_driver()
driver.get("https://example.com")

hl.screenshot("example.png")
hl.save_pdf("example.pdf")
hl.quit()
```

## Performance

Measured 2026-09-29 from a laptop, three queries against Yahoo, then a
five-engine aggregate search. Your numbers will differ; the ratios are the point.

| How | Time per query | Answered | Memory |
| --- | --- | --- | --- |
| `transport="impersonate"` (default with the extra) | **~1.1 s** | 2/3 | a few MB |
| `transport="http"` (plain `requests`) | 0.2 s | **0/3** — refused | a few MB |
| Playwright (`browser="playwright"`) | 1.2 s warm, 2.9 s cold | 3/3 | ~300 MB |
| Selenium Chrome (`transport="browser"`) | 6–9 s | 1/3 | ~1 GB |
| `mode="aggregate"`, 5 engines in parallel | **1.07 s** total | 2 engines agreed on all 10 results | a few MB |

The plain-`requests` row is why impersonation is the default: a Python TLS
handshake behind a Chrome User-Agent is refused on sight. Measure it from your
own servers with `headless-driver bench`.

## Command line

```bash
headless-driver search "python headless browser" -n 5
headless-driver search 'site:linkedin.com/in "jane doe"' --mode aggregate --region uk-en
headless-driver search "python web frameworks" --pages 3 --cache memory
headless-driver fetch https://example.com > page.md      # clean Markdown
headless-driver mcp                                      # serve tools to AI agents
headless-driver search "selenium stealth" --json | jq -r '.results[].url'
headless-driver search "python" --transport impersonate   # no browser at all
headless-driver engines                    # engines, capabilities, fallback order
headless-driver doctor                     # check chrome, driver, transports, connectivity
headless-driver doctor --engines           # check the engines still parse
headless-driver bench --min-ok-rate 0.5    # which engines answer from this address
headless-driver extract https://example.com -f title=h1 -f link=a@href --json
headless-driver shot https://example.com -o page.png --window 1280x720
headless-driver pdf  https://example.com -o page.pdf --browser playwright
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

Searching starts at Brave, which answers from datacentre addresses and honours
`site:` paths. If an engine refuses, has nothing, or stalls, the next is tried —
browserless engines first, browser-only ones late, and Bing, which ignores
`site:` paths, last:

```
brave → duckduckgo → mojeek → yahoo → google_basic → duckduckgo_lite
      → duckduckgo_js → startpage → google → yandex → bing
```

```python
AdvancedSearchScraper(search_engine="yahoo")                 # start elsewhere
AdvancedSearchScraper(fallback=False)                        # single engine
AdvancedSearchScraper(fallback_engines=["mojeek", "yahoo"])  # custom order
AdvancedSearchScraper(mode="aggregate")                      # fan out by default
scraper.search("query", engine="duckduckgo")                 # force, one-off
scraper.engines_honoring_site()                              # trust these for absence
```

Each engine declares what it can do, so you do not have to discover it by
observation:

```python
scraper.capabilities("yahoo")
# {"js": False, "snippets": True, "honors_site": True, "provider": "bing", "method": "GET", ...}
```

Add your own engine with `register_engine()`: a spec can set the HTTP method,
form or query builder, cookies, headers, a per-request URL builder, a pre-flight
hook, a redirect unwrapper and "no results" markers. Details in
[DOCS.md](DOCS.md#search-engines).

## More

- [Documentation site](https://nuhmanpk.github.io/headless-driver/) — guides and API reference
- [Full reference](DOCS.md) — every class, argument and CLI flag
- [AI agents](DOCS.md#ai-agents-and-tools) and the [MCP server](DOCS.md#mcp-server)
- [Reading pages as Markdown](DOCS.md#reading-pages-as-markdown) and [caching](DOCS.md#caching-and-pagination)
- [Changelog](CHANGELOG.md) — what changed in 1.2, 1.1 and 1.0
- [Roadmap](TODO.md) — what is coming: async API, Puppeteer/Cypress bridges, proxy pools and more
- [llms.txt](llms.txt) — a compact API summary for LLMs and coding assistants
- [Search results](DOCS.md#search-results) — `SearchResponse`, `blocked`, per-engine attempts
- [Transports](DOCS.md#transports-and-browserless-mode) — impersonation, plain HTTP, Selenium, Playwright
- [Aggregate mode](DOCS.md#aggregate-mode) and the [circuit breaker](DOCS.md#circuit-breaker)
- [Playwright](DOCS.md#playwright) — `extract()`, `capture_json()`, stealth, tracing
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
