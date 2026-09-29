# Roadmap

What is planned after 1.1. Nothing here is promised for a particular date;
items move up when someone needs them. Ideas and pull requests are welcome —
open an issue to discuss one before starting on it.

Legend: **P1** next up · **P2** planned · **P3** exploring

---

## 1.2 — async and agents

- [ ] **P1 · Async API.** `AsyncSearchScraper` with `await scraper.search(q)`,
      on `curl_cffi`'s `AsyncSession` and Playwright's async API. Aggregate mode
      becomes `asyncio.gather` with a deadline instead of a thread pool, so
      thousands of concurrent queries cost one event loop.
- [ ] **P1 · MCP server.** `headless-driver mcp` exposes `search`,
      `search_aggregate`, `extract`, `screenshot` and `fetch_page` as Model
      Context Protocol tools, so Claude, Cursor and other agents can search the
      web and read pages through this package with its block handling intact.
- [ ] **P1 · LLM-ready page fetch.** `fetch_markdown(url)`: render (HTTP or
      Playwright), strip navigation and boilerplate, return clean Markdown with
      links, token-counted and chunked for RAG pipelines.
- [ ] **P2 · Tool definitions for agent frameworks.** Drop-in tools for
      LangChain, LlamaIndex, CrewAI, the OpenAI and Anthropic tool formats — one
      import, no glue code.
- [ ] **P2 · Result caching.** Optional on-disk / Redis cache keyed by
      normalised query and engine, with TTLs, so repeated queries in a pipeline
      never hit an engine twice.
- [ ] **P2 · Pagination.** `search(q, pages=3)` per engine, deduplicated across pages.

## 1.3 — more browsers, more automation

- [ ] **P1 · Puppeteer bridge.** `browser="puppeteer"` via
      `pyppeteer`/a Node sidecar, for teams whose scraping stack is already
      Puppeteer scripts: run an existing `.js` scraper and get a
      `SearchResponse` back.
- [ ] **P2 · Cypress-style recorder.** `headless-driver record URL` opens a
      headed Playwright window, records clicks and inputs, and writes a replayable
      Python script with stable selectors — the Cypress/Playwright codegen
      experience for scraping flows.
- [ ] **P2 · Declarative flows.** YAML/JSON flows (`goto`, `click`, `fill`,
      `wait_for`, `extract`, `paginate`) runnable from the CLI and CI, with
      screenshots on failure — Cypress-like end-to-end checks for scrapers.
- [ ] **P2 · Selenium Grid / Browserless / remote CDP.** Connect Playwright to a
      remote `ws://` endpoint and Selenium to a grid with the same scraper API.
- [ ] **P2 · WebDriver BiDi.** Use Selenium's BiDi protocol for network
      interception and real status codes, closing the gap with Playwright.
- [ ] **P3 · Firefox and WebKit parity** for every Playwright feature, and
      `undetected-chromedriver`-style patching as an opt-in Selenium mode.

## Anti-blocking and reliability

- [ ] **P1 · Proxy pool with health.** Rotate proxies per engine, retire ones
      that keep getting refused, and pin sessions to a proxy so the TLS
      fingerprint, cookies and IP stay consistent.
- [ ] **P1 · Client-hint consistency under Playwright.** Match `sec-ch-ua`
      brands to the replaced User-Agent (today the headless brand list can still
      say `HeadlessChrome`).
- [ ] **P2 · Adaptive pacing.** A fleet-wide token bucket per engine that learns
      each engine's tolerated rate from its 429s, instead of fixed pauses.
- [ ] **P2 · CAPTCHA hooks.** A callback interface for solver services and for
      human-in-the-loop solving in a headed browser — the library detects, the
      caller decides.
- [ ] **P2 · Self-healing selectors.** When an engine returns `unparsed`, try
      structural heuristics (repeated link-and-heading blocks) and report the
      candidate selector, so layout changes degrade instead of breaking.
- [ ] **P3 · Fingerprint diversity report.** `doctor --fingerprint` showing the
      JA3/JA4, HTTP/2 and header fingerprint each transport presents.

## More engines and verticals

- [ ] **P2** Ecosia, Qwant, SearXNG instances, Kagi (with a user token),
      Marginalia, Wikipedia as a first-class engine.
- [ ] **P2** News, images and video verticals with typed result models.
- [ ] **P3** Regional engines: Baidu, Naver, Seznam, Yandex over HTTP.

## Developer experience

- [ ] **P1 · Typed result models.** `SearchResult` dataclass / optional
      Pydantic model alongside the dict, with IDE completion.
- [ ] **P2 · OpenTelemetry.** Spans per engine attempt with status, HTTP code
      and transport, so blocks show up on existing dashboards.
- [ ] **P2 · Hosted docs site** (MkDocs Material) with a searchable API reference
      generated from docstrings.
- [ ] **P2 · Interactive TUI.** `headless-driver tui`: live engine health,
      circuit-breaker states and a search box.
- [ ] **P3 · Docker images** published per release: `slim` (browserless), `full`
      (Chrome + Playwright).

## Done in 1.1

- [x] HTTP status classification (`blocked`, `rate_limited`, `unparsed`)
- [x] Browser-impersonating transport (`curl_cffi`) with profile rotation
- [x] Brave, Yahoo and basic Google engines; DuckDuckGo POST form; browserless Mojeek
- [x] Aggregate mode with consensus ranking
- [x] Per-engine circuit breaker and browser withdrawal
- [x] Soft-block probe and `verify_empty`
- [x] Playwright backend: status codes, stealth, resource blocking, `extract()`,
      `capture_json()`, screenshots, PDF, tracing
- [x] `bench`, `extract`, colourful logging, end-to-end suite from the installed wheel
