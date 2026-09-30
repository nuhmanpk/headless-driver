# Playwright

```bash
pip install "headless-driver[playwright]" && playwright install chromium
```

Use Playwright as the scraper's browser — real status codes, resource blocking,
stealth, per-engine contexts:

```python
AdvancedSearchScraper(browser="playwright")
```

Or on its own, as a scraping-oriented browser:

```python
from headless.playwright_driver import PlaywrightBrowser

with PlaywrightBrowser(block_resources=True, proxy="http://user:pass@host:8080") as browser:
    rows = browser.extract("https://news.ycombinator.com",
                           {"title": ".titleline > a", "link": ".titleline > a@href"},
                           item_selector="tr.athing")
    api = browser.capture_json("https://example.com/app", r"/api/")
    browser.screenshot("https://example.com", "page.png", full_page=True)
    browser.pdf("https://example.com", "page.pdf")
```

Full options in the [reference manual](../manual.md#playwright).
