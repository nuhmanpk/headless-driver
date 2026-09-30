# Search

```python
from headless import AdvancedSearchScraper

scraper = AdvancedSearchScraper(region="us-en", max_results=10)
response = scraper.search("rust async runtime")
```

## Two modes

| Mode | What it does | Use it for |
| --- | --- | --- |
| `first` (default) | Asks engines in turn — Brave, DuckDuckGo, Mojeek, Yahoo, … — until one answers | Speed |
| `aggregate` | Asks one engine per independent index at once, merges, ranks by `votes` | Accuracy: people, companies, specific documents |

```python
scraper.search(query, mode="aggregate", deadline=8, engines=["brave", "duckduckgo", "mojeek"])
```

## What a response tells you

| | Meaning |
| --- | --- |
| `response.engine` | Which engine answered (`"aggregate"` in aggregate mode) |
| `response.blocked` | Every engine refused — back off, don't record "not found" |
| `response.rate_limited`, `response.retry_after` | Someone sent HTTP 429, and how long to wait |
| `response.cooling` | Nothing was asked: every engine is standing down after refusals |
| `response.cached` | Served from the cache; no request was made |
| `response.attempts` | One `EngineAttempt` per engine and page: status, HTTP code, transport, time |
| `response.skipped` | Engines not asked, and why |

Statuses are `ok`, `empty`, `blocked`, `rate_limited`, `unparsed`, `timeout`,
`unreachable` and `error`. Only `empty` from an engine in
`scraper.engines_honoring_site()` is evidence that nothing exists.

## Typed results

```python
for hit in response.typed():          # SearchResult dataclasses
    hit.url, hit.title, hit.votes
models = response.to_pydantic()       # with pydantic installed
```

## Operators

`site:`, `"exact phrase"` and the rest are passed through, and `site:` is also
enforced on the results: off-site rows are dropped, and engines that ignore
`site:` (Bing) are skipped for such queries.

See the [reference manual](../manual.md#search-results) for every option,
engine and status.
