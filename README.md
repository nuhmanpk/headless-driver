[![PyPI version](https://badge.fury.io/py/headless-driver.svg)](https://badge.fury.io/py/headless-driver)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/headless-driver.svg)](https://pypi.org/project/headless-driver/)
[![Downloads](https://pepy.tech/badge/headless-driver)](https://pepy.tech/project/headless-driver)


# headless-driver

Lightweight Python pacakge to manage Selenium WebDriver in headless mode with proxy support, stealth tweaks, auto-driver installation, multi-driver management, download handling, and advanced search-scraping utilities

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
- Advanced search scraper (DuckDuckGo default) with title, snippet, favicon, cached link, and batch search/export support

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
from headless import SearchScraper, Headless

hl = Headless()
driver = hl.get_driver()

scraper = SearchScraper(driver=driver, max_results=5)

results = scraper.search("Nuhman PK github")
print(results)
```

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

batch = scr.search_batch(["python headless", "selenium stealth"], max_workers=2)

scr.export("results.json")
scr.quit()

```


## API Documentation

### Headless class

```python
Headless(
    user_data_dir: Optional[str] = None,
    window_size: Tuple[int, int] = (1920, 1080),
    user_agent: Optional[str] = None,
    headless: bool = True,
    chrome_driver_path: Optional[str] = '/opt/homebrew/bin/chromedriver',
    additional_args: Optional[List[str]] = None,
    remote_url: Optional[str] = None,
)
```

- `user_data_dir`: Path for Chrome user data (temporary if not provided)
- `window_size`: Browser window size (default: 1920x1080)
- `user_agent`: Custom user agent string
- `headless`: Run Chrome in headless mode (default: True)
- `chrome_driver_path`: Path to chromedriver executable
- `additional_args`: List of extra Chrome arguments
- `remote_url`: Use remote Selenium server if provided

### Methods
- `get_driver()`: Returns a Selenium WebDriver instance
- `quit()`: Quits the driver and cleans up user data

## Running Tests

```bash
python -m unittest discover tests
```

[Happy coding 🚀](https://github.com/nuhmanpk/)

