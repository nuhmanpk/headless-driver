# Changelog

## 1.1.0

The theme of this release is **getting answers from the cloud**. On a laptop
almost any engine answers; from AWS, ECS or a CI runner most refused, and the
library sometimes reported those refusals as "no results". 1.1 fixes the
reporting, fetches like a real browser, adds the engines that still answer from
datacentre addresses, and asks several at once. It includes every fix listed
under 1.0.1.

### Fixed

- **HTTP 403, 429 and 503 are no longer reported as `empty`.** The status code
  was stored and never read, so a Mojeek 403 read as "Mojeek looked and found
  nothing" — a false negative for any caller treating `empty` from a
  `site:`-honouring engine as evidence of absence. Statuses now decide first:
  401/403/407/503 are `blocked`, 429 is the new `rate_limited` (with
  `Retry-After`), DuckDuckGo's 202 anomaly page is `blocked`, other 4xx/5xx
  are `error`.
- **A page with no result containers and no "no results" marker is `unparsed`,
  not `empty`.** An unrecognised page is a block with unknown markup or a
  layout change — never evidence that nothing exists.
- **A search for "captcha" is no longer a captcha.** Block markers were matched
  against the whole URL, query string included, so queries containing
  `captcha`, `anomaly` or `challenge` were reported blocked on every engine.
  They now match the host and path only.
- **Mojeek's "results-free stub" is detected as what it is**, a JavaScript
  captcha (`<title>Captcha</title>`), and Google's `enablejs` wall as a block.
- **Google's `/url?q=` redirects are unwrapped wherever the page lives**, not
  only on `google.*` hosts.
- Two logging calls passed a stray argument (`log.debug(msg, "error")`, left
  over from the old `diag` signature) and raised `TypeError` inside logging
  whenever ChromeDriver was incompatible or an instance failed to quit.
- `headless-driver shot/pdf` read an `args.show` that no option defined; `--show`
  now exists.
- Table cells that were already coloured were truncated through their escape
  codes, garbling the terminal; truncation and padding now measure visible width.

### Added

- **`transport="impersonate"`** (`pip install "headless-driver[impersonate]"`),
  backed by `curl_cffi`: a real browser's TLS ClientHello, HTTP/2 SETTINGS and
  header order, with a matching User-Agent. `transport="auto"` prefers it.
  Sessions are per engine and per thread, the profile rotates (Chrome, Edge,
  Safari, Firefox, Chrome Android, Safari iOS) and is replaced after a
  refusal, and proxy credentials work.
- **New engines: `brave`, `yahoo`, `google_basic`**, and DuckDuckGo now POSTs
  its HTML form as the page itself does. `mojeek` no longer needs a browser.
  `startpage` can fetch its `sc` token over HTTP.
- **`mode="aggregate"`**: one query to one engine per independent index at once,
  merged by normalised URL, ranked by `votes` then mean rank, with `engines`
  and `ranks` on every result, a `deadline`, sibling fallback within a
  provider, and early exit on consensus. Also `merge_results()` and
  `normalize_url()` on their own.
- **Per-engine circuit breaker** (`EngineHealth`, shared per process): stands an
  engine down after refusals, escalates the pause when it refuses again right
  after resuming, honours `Retry-After`. `scraper.health()`,
  `scraper.reset_health()`, `SearchResponse.cooling` and `.skipped`.
- **Browser withdrawal**: once two independent indexes refuse in one search, no
  browser is launched for `browser_cooldown` seconds — Chrome cannot get past an
  address-level block and costs a gigabyte trying.
- **Strict `site:`**: engines known to ignore `site:` (Bing) are skipped for
  `site:` queries; `engines_honoring_site()`; `honors_site` and `provider` on
  every engine spec.
- **Soft-block detection**: `scraper.probe(engine)` and `verify_empty=True`.
- **Playwright** (`pip install "headless-driver[playwright]"`):
  `AdvancedSearchScraper(browser="playwright")` serves JavaScript engines with
  real status codes, resource blocking, stealth (including replacing the
  `HeadlessChrome` User-Agent), per-engine contexts and credentialed proxies.
  `PlaywrightBrowser` adds declarative `extract()`, `capture_json()` for a
  site's own API responses, `scroll_to_bottom()`, screenshots, PDF and tracing.
- **Richer engine specs**: `method`, `params`, `headers`, `cookies`,
  `url_builder`, `prepare`, `unwrap`, `no_results`, `block_statuses`,
  `impersonate`, `honors_site`, `provider`.
- **`region="uk-en"`** feeds each engine's own region parameters and cookies.
- **`http_timeout`** (default 8 s), separate from the browser's page load timeout.
- **Text and URL normalisation**: entities unescaped, NFC, control and
  zero-width characters removed, percent-encoded paths decoded where lossless.
- `EngineAttempt.http_status`, `.retry_after`, `.transport` and a readable `str()`.
- **CLI**: `search --mode/--engines/--deadline/--region/--browser`,
  `--transport impersonate`; `engines` shows `site:` support and index;
  `doctor` reports transports and warns when the TLS fingerprint cannot match
  the User-Agent; new `bench` (per-engine health matrix, `python -m
  headless.bench`) and `extract`; `shot/pdf --browser playwright --full-page`;
  `python -m headless`.
- **Colourful logging everywhere**: levelled badges, a stable colour per
  component, highlighted HTTP codes, statuses, URLs, quotes and numbers.
  `ColorFormatter` for any handler, `colorize_logging()` for an application's
  existing console handlers, `enable_console_logging(third_party=True,
  capture_warnings=True, timestamps=True)`, and `HEADLESS_DRIVER_LOG=info` to
  turn it on from the environment. The CLI renders webdriver-manager,
  Selenium, urllib3 and Python warnings in the same style. The unactionable
  urllib3 LibreSSL import warning is silenced.
- `lxml` is used for parsing when installed (`[fast]` extra); `[all]` extra.
- **Tests**: 360+ (from 180), ~90% coverage, dated live fixtures per engine and
  outcome, and an end-to-end suite that scrapes a local site built from ground
  truth — through every transport, Selenium and Playwright — using the wheel
  installed into a fresh virtualenv, on Linux, macOS and Windows in CI. A
  weekly workflow benchmarks the engines from GitHub's datacentre addresses.

### Changed

- **The default engine is `brave`** and the chain is
  `brave → duckduckgo → mojeek → yahoo → google_basic → duckduckgo_lite →
  duckduckgo_js → startpage → google → yandex → bing`. Pass
  `search_engine="duckduckgo"` for the old start.
- **Refusals stand engines down.** After three refusals in a row (or one 429) an
  engine is skipped for a while, process-wide. `circuit_breaker=False` restores
  the old always-ask behaviour.
- `EngineAttempt.blocked` is also True for the new `rate_limited` and `unparsed`.
- Per-request "blocked this request" messages moved from INFO to DEBUG; state
  changes (an engine cooling down, answering again) are logged once each.
- New `AdvancedSearchScraper` options are keyword-only.

## 1.0.1

Fixes for regressions 1.0.0 introduced in working 0.x code. Never released on
its own; shipped as part of 1.1.0.

### Fixed

- **`SearchResponse` is now a `list` subclass.** As a dataclass it broke
  `json.dumps(results)`, `results + [...]`, `[...] + results`, `.append()`,
  `.extend()`, `.count()` and `.index()` — the ordinary things a pipeline does
  with search results. It is a real list again, keeping `.blocked`, `.engine`
  and `.attempts` alongside.
- **A proxy configured the pre-1.0 way is no longer bypassed.** Setting
  `headless_options={"additional_args": ["--proxy-server=..."]}` reached the
  browser but not the new HTTP transport, so with `transport="auto"` requests
  left from the host's own address — leaking the real IP and getting blocked
  from datacentre ranges. The HTTP transport now inherits that proxy, and the
  `user_agent` from `headless_options` too.
- **Sponsored results are no longer scraped as organic ones.** DuckDuckGo's
  `y.js` ad slots, and Bing's and Google's `aclick`/`aclk` endpoints, were being
  returned as results — a scrape would silently collect
  `duckduckgo.com/y.js?ad_domain=...` instead of a real URL.
- **`site:` is enforced on results.** Bing honours `site:example.com` but
  ignores a path such as `site:example.com/in`, answering with unrelated pages.
  Those look like success and are worse than an error, so results that do not
  satisfy the query's `site:` constraint are dropped and the engine is treated
  as having nothing, letting the chain continue to one that respects it.

### Note on upgrading from 0.x

1.0 changed the default transport to `"auto"`, which fetches server-rendered
engines over HTTP instead of driving Chrome. It is much faster, but engines
rate-limit plain HTTP clients more aggressively than a real browser. If a
workload behaves differently after upgrading, `transport="browser"` restores the
0.x path exactly:

```python
AdvancedSearchScraper(transport="browser")
```

## 1.0.0

The theme of this release is **observability**: the library already knew which
engine answered, which refused, and why — and told the caller none of it.

### Breaking changes

Both are source-compatible for code that treats results as a list.

- **`search()` returns a `SearchResponse`, not a `list`.** It iterates, indexes,
  compares equal to a list, and is falsey when empty, so
  `for hit in scraper.search(q)` and `if not results:` keep working. New code can
  ask `response.blocked`, `response.engine` and `response.attempts`.
- **`search(engine=…)` no longer disables the fallback chain.** Previously
  `search_engine="bing"` fell back while `search(q, engine="bing")` silently did
  not. `fallback=` now decides, on the constructor or per call; pass
  `fallback=False` for the old behaviour.

Also changed, with no source impact:

- `AdvancedSearchScraper.results` no longer accumulates by default. Pass
  `keep_history=True` (bounded by `history_limit`) to restore it. `export()`
  takes an explicit `results=` argument and otherwise writes the last response.
- `ExtendedHeadless(chrome_binary_path=…)` defaults to `None` (detect) rather
  than the Linux-only `/usr/bin/chromium-browser`.
- The default User-Agent now reports the Chrome version and OS actually in use,
  instead of a fixed Chrome 87 on Windows.

### Added

- **Structured responses** — `SearchResponse`, `EngineAttempt`, and per-attempt
  statuses (`ok`, `empty`, `blocked`, `timeout`, `unreachable`, `error`), so
  "nobody has an answer" is distinguishable from "everybody refused me".
  `raise_on_block=True` turns a blanket refusal into `AllEnginesBlocked`.
- **Browserless HTTP transport** — `pip install "headless-driver[http]"`, then
  `transport="http"`. Server-rendered engines are fetched with an HTTP client
  instead of Chrome: sub-second instead of seconds, megabytes instead of a
  gigabyte, and thread-safe. `transport="auto"` (the default) uses it wherever
  the engine allows and falls back to the browser for the rest.
- **`ScraperPool`** — one browser per worker, for genuinely parallel searching.
- **`logging`** — everything goes through the `headless` logger, which carries a
  `NullHandler` and stays silent until the application configures logging or
  passes `verbose=True`. The library no longer prints uninvited.
- **`proxy=`** on `Headless`, `SearchScraper` and `AdvancedSearchScraper`.
- **Engine capabilities** — `ENGINE_SPECS` records `js` (needs a browser) and
  `snippets` (returns description text) per engine, exposed via
  `scraper.capabilities(engine)`. `duckduckgo_lite` returns no snippets, which
  was previously discoverable only by observation.
- **`headless-driver doctor --engines`** — runs a probe query through every
  engine and reports which still parse, catching selector rot that the
  environment checks cannot see.
- **Driver recovery** — a crashed browser is detected and rebuilt instead of
  poisoning every later call. `recycle()` makes it explicit.
- `headless.__version__`, a `py.typed` marker, `--transport` and `--proxy` on
  the CLI, and offline HTML fixtures so extractor tests need no network.

### Fixed

- `search_batch(max_workers=N)` claimed concurrency it could not deliver: every
  worker blocked on the same lock and shared one WebDriver. It now runs in
  parallel when the chain is HTTP-only, and says so when it cannot.
- `last_engine` kept naming the previous query's engine after a failed search.
- The lock is no longer held across an entire fallback walk on behalf of other
  threads; use `ScraperPool` for concurrency.
