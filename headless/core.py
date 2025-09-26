import os
import uuid
import shutil
import tempfile
from typing import List, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.webdriver import WebDriver

class Headless:
    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        window_size: Tuple[int, int] = (1920, 1080),
        user_agent: Optional[str] = None,
        headless: bool = True,
        chrome_driver_path: Optional[str] = '/opt/homebrew/bin/chromedriver',
        additional_args: Optional[List[str]] = None,
        remote_url: Optional[str] = None,
    ):
        self.id = uuid.uuid4().hex
        if user_data_dir:
            self.user_data_dir = user_data_dir
            self._cleanup_dir = False
        else:
            prefix = f"chrome-user-data-{self.id}-"
            self.user_data_dir = tempfile.mkdtemp(prefix=prefix)
            self._cleanup_dir = True

        self.window_size = window_size
        self.user_agent = (
            user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36"
        )
        self.headless = headless
        self.additional_args = additional_args or []
        self.chrome_driver_path = chrome_driver_path
        self.remote_url = remote_url
        self._driver: Optional[WebDriver] = None

    def _build_options(self) -> Options:
        opts = Options()
        opts.add_argument(f"--user-data-dir={self.user_data_dir}")
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={self.window_size[0]},{self.window_size[1]}")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument(f"user-agent={self.user_agent}")
        for arg in self.additional_args:
            opts.add_argument(arg)
        return opts

    def get_driver(self) -> WebDriver:
        if self._driver:
            return self._driver

        opts = self._build_options()

        if self.remote_url:
            self._driver = webdriver.Remote(
                command_executor=self.remote_url,
                options=opts
            )
        else:
            if self.chrome_driver_path:
                service = Service(executable_path=self.chrome_driver_path)
                self._driver = webdriver.Chrome(
                    service=service,
                    options=opts
                )
            else:
                self._driver = webdriver.Chrome(
                    options=opts
                )

        return self._driver

    def quit(self) -> None:
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

        if self._cleanup_dir and os.path.isdir(self.user_data_dir):
            shutil.rmtree(self.user_data_dir, ignore_errors=True)

    def __enter__(self) -> WebDriver:
        return self.get_driver()

    def __exit__(self) -> None:
        self.quit()
