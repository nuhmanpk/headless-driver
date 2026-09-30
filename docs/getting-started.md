# Getting started

## Install

```bash
pip install "headless-driver[impersonate]"   # search and page reading (recommended)
pip install "headless-driver[mcp]"           # + the MCP server for AI agents (Python 3.10+)
pip install "headless-driver[agents]"        # + LangChain tools and pydantic schemas
pip install "headless-driver[playwright]"    # + Playwright; then: playwright install chromium
pip install "headless-driver[all]"           # everything
```

Python 3.9 or newer. Searching and reading pages need no browser.

## Your first search

```python
from headless import AdvancedSearchScraper

with AdvancedSearchScraper(max_results=5) as scraper:
    response = scraper.search("python asyncio tutorial")

    for hit in response:
        print(hit["title"], hit["url"], hit["snippet"])

    print("answered by", response.engine)
```

`response` is a list of result dicts that also tells you what happened:

```python
if response.blocked:        # the engines refused (rate limit, captcha) — not "nothing found"
    ...
elif not response:          # the engines looked and found nothing
    ...
```

Prefer attributes to dict keys? `response.typed()` gives dataclasses with IDE
completion.

## More reliable answers

Ask several independent engines at once and rank by agreement:

```python
response = scraper.search('site:linkedin.com/in "Jane Doe" Credo Capital', mode="aggregate")
for hit in response:
    print(hit["votes"], hit["engines"], hit["url"])
```

## Read a result

```python
from headless import fetch_markdown

doc = fetch_markdown(response[0]["url"])
print(doc.markdown)
```

## From the command line

```bash
headless-driver search "python asyncio" -n 5
headless-driver search 'site:linkedin.com/in "jane doe"' --mode aggregate
headless-driver fetch https://example.com > page.md
headless-driver doctor
```

Next: [search in depth](guides/search.md), [AI agents](guides/agents.md),
[MCP](guides/mcp.md).
