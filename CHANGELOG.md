# Changelog

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
