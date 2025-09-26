[![PyPI version](https://badge.fury.io/py/headless-driver.svg)](https://badge.fury.io/py/headless-driver)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Versions](https://img.shields.io/pypi/pyversions/headless-driver.svg)](https://pypi.org/project/headless-driver/)
[![Build Status](https://github.com/nuhmanpk/headless-driver/workflows/CI/badge.svg)](https://github.com/nuhmanpk/headless-driver/actions)
[![Coverage Status](https://coveralls.io/repos/github/nuhmanpk/headless-driver/badge.svg?branch=main)](https://coveralls.io/github/nuhmanpk/headless-driver?branch=main)
[![Downloads](https://pepy.tech/badge/headless-driver)](https://pepy.tech/project/headless-driver)


# headless-driver

A simple Python package for managing Selenium Chrome drivers in headless mode.

<center>
<img src="./images/logo.png">
</center>


## Features
- Headless Chrome driver management
- Temporary user data directory
- Custom window size and user agent
- Easy cleanup and context manager support
- Remote and local driver support

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

## License
MIT
