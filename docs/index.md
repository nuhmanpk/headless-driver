---
hide:
  - navigation
---

# headless-driver

**Web search for Python and AI agents. Free, fast, no API key.**

Search Brave, DuckDuckGo, Yahoo, Mojeek, Google and Bing from your code, read
any page as clean Markdown, and hand both to an AI agent — as OpenAI,
Anthropic, LangChain, LlamaIndex or CrewAI tools, or over MCP.

```bash
pip install "headless-driver[impersonate]"
```

=== "Search"

    ```python
    from headless import AdvancedSearchScraper

    with AdvancedSearchScraper() as scraper:
        for hit in scraper.search("python asyncio tutorial"):
            print(hit["title"], hit["url"])
    ```

=== "Read a page"

    ```python
    from headless import fetch_markdown

    doc = fetch_markdown("https://docs.python.org/3/library/asyncio.html", chunk_tokens=500)
    print(doc.title, doc.tokens)
    print(doc.markdown)
    ```

=== "Give an agent the web"

    ```python
    from headless import Toolkit

    toolkit = Toolkit()
    tools = toolkit.anthropic_tools()        # or openai_tools(), langchain_tools() …
    results = toolkit.handle_anthropic_tool_use(response.content)
    ```

=== "MCP (Claude, Cursor…)"

    ```json
    {"mcpServers": {"web": {"command": "headless-driver", "args": ["mcp"]}}}
    ```

## Why it works where other scrapers get blocked

<div class="grid cards" markdown>

- **Looks like a real browser.** Requests carry a genuine Chrome, Safari or
  Firefox TLS fingerprint, not Python's — the thing anti-bot systems check first.
- **Asks the right engines the right way.** Each engine is queried the way its
  own front end does, and several at once in `mode="aggregate"`, ranked by how
  many agree.
- **Never lies about "no results".** A refusal (403, 429, captcha) is reported
  as `blocked` or `rate_limited`, never as an empty answer.
- **Backs off by itself.** Engines that refuse are stood down and retried
  later; results are cached so pipelines don't ask twice.

</div>

About a second per search, a few megabytes of memory, no browser needed —
and Selenium or Playwright when a page really does need one.

[Get started](getting-started.md){ .md-button .md-button--primary }
[AI agents](guides/agents.md){ .md-button }
[MCP server](guides/mcp.md){ .md-button }
