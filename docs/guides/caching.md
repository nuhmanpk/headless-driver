# Caching & pagination

## Caching

```python
AdvancedSearchScraper(cache="memory")                       # per process
AdvancedSearchScraper(cache="sqlite:///~/.cache/hd.db")     # on disk, shared by processes
AdvancedSearchScraper(cache="redis://localhost:6379/0")     # shared by a fleet
AdvancedSearchScraper(cache=my_cache)                       # anything with get()/set()
```

The cache holds **one engine's answer to one query** — keyed by engine, query
(ignoring case and spacing), page, result limit and region. So a cached Brave
answer serves a later `first`-mode search *and* an aggregate search that
includes Brave, and a cached engine answers even while its circuit breaker is
open.

| | TTL |
| --- | --- |
| `ok` answers | `cache_ttl` (24 h) |
| `empty` answers | `cache_empty_ttl` (1 h) — shorter, since empty can be a soft block |
| refusals | never cached |

```python
response.cached          # True when no request was made
scraper.clear_cache()
```

Redis needs `pip install "headless-driver[redis]"`. A cache that fails (Redis
down) is logged and bypassed, never fatal.

## Pagination

```python
response = scraper.search("python web frameworks", pages=3)   # up to 3 pages per engine
for hit in response:
    hit["page"]
```

Each page is up to `max_results`; pages are de-duplicated, and paging stops
early when a page is refused, empty, or adds nothing new. In aggregate mode
every engine pages, and ranks run on across pages. Every engine except
`duckduckgo_js` supports it (`scraper.paginates(engine)`).
