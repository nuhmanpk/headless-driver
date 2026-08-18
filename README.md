[![PyPI version](https://badge.fury.io/py/headless-driver.svg)](https://badge.fury.io/py/headless-driver)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/headless-driver.svg)](https://pypi.org/project/headless-driver/)
[![Downloads](https://pepy.tech/badge/headless-driver)](https://pepy.tech/project/headless-driver)


# headless-driver

Lightweight Python package to manage Selenium WebDriver in headless mode with proxy support, stealth tweaks, auto-driver installation, multi-driver management, download handling, and advanced search-scraping utilities

<center>
<img src="https://raw.githubusercontent.com/nuhmanpk/headless-driver/main/images/logo.png" />
</center>


## Features
- Headless and non-headless Chrome management
- Temporary or persistent user-data directories (profiles)
- HTTP and SOCKS proxy support
- Optional stealth mode (integrates with selenium-stealth when available)
- Automatic ChromeDriver install (via webdriver-manager) as an optional dependency
- Download folder management and automatic cleanup
- Screenshot and PDF export via Chrome DevTools
- Multi-driver manager to run many isolated browser instances
- Advanced search scraper with title, snippet, favicon, cached link, and batch search/export support
- DuckDuckGo's no-JavaScript endpoint by default, with automatic fallback to Bing, Mojeek, Startpage, Google and Yandex when an engine blocks the request
- Bounded page-load timeouts, so a stalled engine fails over instead of hanging

## Installation

```bash
pip install headless-driver
```

## Usage

```python
from headless import Headless

hl = Headless()
driver = hl.get_driver()
driver.get("https://example.com")
print(driver.title)
hl.quit()
```

Or use as a context manager:

```python
from headless import Headless

with Headless() as driver:
    driver.get("https://example.com")
    print(driver.title)
```

## Search

```python
from headless import SearchScraper

with SearchScraper(max_results=5) as scraper:
    results = scraper.search("Nuhman PK github")
    print(results)                # [{"url": ..., "snippet": ...}, ...]
    print(scraper.last_engine)    # which engine actually answered
```

Searching starts at DuckDuckGo's no-JavaScript endpoint
(`html.duckduckgo.com`), which returns server-rendered HTML and so is markedly
faster than the JavaScript front end. If an engine serves a bot check, returns
nothing, or stalls, the next one in the chain is tried automatically:

    duckduckgo -> duckduckgo_lite -> bing -> mojeek
               -> duckduckgo_js -> startpage -> google -> yandex

`search()` returns `[]` only once every engine has been tried.

## Stealth

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(stealth=True)

driver = hl.get_driver()
driver.get("https://example.com")

print(driver.title)

hl.quit()
```

## Proxy

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(proxy="socks5://127.0.0.1:9050")

driver = hl.get_driver()
driver.get("https://example.com")

print(driver.title)

hl.quit()
```

## Take Screen / Export PDF

```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(download_dir="/tmp/hd_downloads")
d = hl.get_driver()

d.get("https://example.com")

hl.screenshot("/tmp/example.png")
hl.save_pdf("/tmp/example.pdf")

hl.quit()
```

## Auto Install Driver
```python
from headless import ExtendedHeadless

hl = ExtendedHeadless(auto_install=True)
d = hl.get_driver()

d.get("https://example.com")

hl.screenshot("/tmp/example.png")
hl.save_pdf("/tmp/example.pdf")

hl.quit()
```

## Multi driver manager

```python
from headless import MultiDriverManager

mgr = MultiDriverManager()
a = mgr.create("bot1", stealth=True, auto_install=True)
b = mgr.create("bot2", proxy="http://1.2.3.4:3128", download_dir="/tmp/d2", auto_install=True)
da = a.get_driver()
db = b.get_driver()
mgr.quit_all()

```

## Advanced scraper

```python
from headless import AdvancedSearchScraper

scr = AdvancedSearchScraper(headless_options={"headless": True}, max_results=5)

res = scr.search("python headless")
print(scr.last_engine)       # engine that produced `res`

batch = scr.search_batch(["python headless", "selenium stealth"], max_workers=2)

scr.export("results.json")   # .json and .csv are supported
scr.quit()
```

Each result is a dict of `url`, `title`, `snippet`, `favicon`, `cached`,
`quick_answer` and `engine`. Click-tracking redirects (DuckDuckGo `/l/?uddg=`,
Bing `/ck/a`, Google `/url?q=`) are resolved to the real destination.

### Choosing engines

```python
# Start somewhere else; the rest of the chain still applies.
AdvancedSearchScraper(search_engine="bing")

# One engine only, no fallback.
AdvancedSearchScraper(fallback=False)
scr.search("python headless", engine="bing")

# Your own order.
AdvancedSearchScraper(search_engine="duckduckgo", fallback_engines=["bing", "mojeek"])
```

Available engines: `duckduckgo` (default), `duckduckgo_lite`, `duckduckgo_js`,
`bing`, `mojeek`, `google`, `startpage`, `yandex`. Add your own with
`register_engine(name, spec)`.

### Timeouts

```python
AdvancedSearchScraper(page_load_timeout=20.0, wait_timeout=8.0)
```

`page_load_timeout` bounds how long one engine may take to load, and
`wait_timeout` how long to wait for its results to appear. Selenium's own
default is 300 seconds, so both are set well below it to keep a wedged engine
from stalling the whole search. `Headless(page_load_timeout=30.0)` applies the
same bound to any driver it hands out.

Search engines defend aggressively against automation, and Google in particular
serves a CAPTCHA to headless browsers on most networks. That is why the fallback
chain exists; blocks are reported when `verbose=True`.



## API Documentation

### Headless class

```python
Headless(
    user_data_dir: Optional[str] = None,
    window_size: Tuple[int, int] = (1920, 1080),
    user_agent: Optional[str] = None,
    headless: bool = True,
    chrome_driver_path: Optional[str] = None,
    additional_args: Optional[List[str]] = None,
    remote_url: Optional[str] = None,
    verbose: bool = False,
    page_load_timeout: Optional[float] = 30.0,
)
```

- `user_data_dir`: Path for Chrome user data (temporary if not provided)
- `window_size`: Browser window size (default: 1920x1080)
- `user_agent`: Custom user agent string
- `headless`: Run Chrome in headless mode (default: True)
- `chrome_driver_path`: Path to chromedriver executable. When omitted, one is
  auto-detected on PATH and common install locations; if that driver turns out
  to mismatch the installed Chrome, a matching one is downloaded automatically
- `additional_args`: List of extra Chrome arguments
- `remote_url`: Use remote Selenium server if provided
- `verbose`: Print driver setup and teardown diagnostics
- `page_load_timeout`: Seconds a page load may take before it is aborted (`None` disables)

### Methods
- `get_driver()`: Returns a Selenium WebDriver instance
- `quit()`: Quits the driver and cleans up user data

## Install Driver on Linux
```bash
sudo apt update
sudo apt install -y chromium chromium-driver
```

[Happy coding 🚀](https://github.com/nuhmanpk/)

