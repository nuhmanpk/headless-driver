# Reading pages: Markdown for LLMs

`fetch_markdown()` turns any web page into clean Markdown an LLM can use:
navigation, headers, footers, cookie banners, ads and share widgets removed,
links made absolute, code blocks and tables kept.

```python
from headless import fetch_markdown

doc = fetch_markdown("https://docs.python.org/3/library/asyncio-task.html")
doc.title        # "Coroutines and tasks"
doc.tokens       # token count (exact with tiktoken, estimated without)
doc.markdown     # the content
doc.links        # [(text, absolute_url), ...]
doc.transport    # "impersonate", "http" or "playwright"
```

## JavaScript pages

`render="auto"` (the default) fetches over HTTP first and switches to
Playwright only when the page is an empty app shell (`<div id="root"></div>`,
"please enable JavaScript", mostly scripts). Force it either way with
`render="always"` or `render="never"`.

## Fitting a context window

```python
doc = fetch_markdown(url, max_tokens=4000)   # truncated at a paragraph boundary
```

## Chunks for RAG

```python
doc = fetch_markdown(url, chunk_tokens=500, overlap=50)
for chunk in doc.chunks:
    store.add(chunk.text, metadata={"url": doc.url, "heading": chunk.heading,
                                    "tokens": chunk.tokens})
```

Chunks split on headings and paragraphs, never exceed `chunk_tokens`, repeat
`overlap` tokens of the previous chunk, and carry their heading path
(`"Install > From source"`).

## HTML you already have

```python
from headless import html_to_markdown, chunk_markdown, count_tokens

markdown, title, links = html_to_markdown(html, base_url="https://example.com/")
chunks = chunk_markdown(markdown, max_tokens=300)
```

From the command line: `headless-driver fetch URL [--max-tokens N] [--chunk N] [--json]`.
