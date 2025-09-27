import os
import base64
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from .core import Headless as CoreHeadless

class ExtendedHeadless(CoreHeadless):
    def __init__(
        self,
        proxy: Optional[str] = None,
        stealth: bool = False,
        download_dir: Optional[str] = None,
        auto_install: bool = True,
        profile_dir: Optional[str] = None,
        chrome_driver_path: Optional[str] = None,
        *args,
        **kwargs,
    ):
        if profile_dir:
            kwargs["user_data_dir"] = profile_dir
        if chrome_driver_path:
            kwargs["chrome_driver_path"] = chrome_driver_path
        super().__init__(*args, **kwargs)
        self.proxy = proxy
        self.stealth = stealth
        self.download_dir = download_dir
        self.auto_install = auto_install
        self._applied_stealth = False

    def _build_options(self) -> Options:
        opts = super()._build_options()
        if self.proxy:
            opts.add_argument(f"--proxy-server={self.proxy}")
        prefs = {}
        if self.download_dir:
            os.makedirs(self.download_dir, exist_ok=True)
            prefs.update({
                "download.default_directory": os.path.abspath(self.download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
            })
        if prefs:
            opts.add_experimental_option("prefs", prefs)
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        return opts

    def _auto_install_driver(self):
        if not self.chrome_driver_path and self.auto_install:
            try:
                path = ChromeDriverManager().install()
                self.chrome_driver_path = path
            except Exception:
                pass

    def get_driver(self) -> WebDriver:
        self._auto_install_driver()
        driver = super().get_driver()
        if driver and self.stealth and not self._applied_stealth:
            try:
                from selenium_stealth import stealth as apply_stealth
                apply_stealth(
                    driver,
                    languages=["en-US", "en"],
                    vendor="Google Inc.",
                    platform="Win32",
                    webgl_vendor="Intel Inc.",
                    renderer="Intel Iris OpenGL Engine",
                    fix_hairline=True,
                )
                self._applied_stealth = True
            except Exception:
                try:
                    driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {
                            "source": """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
"""
                        },
                    )
                    self._applied_stealth = True
                except Exception:
                    pass
        return driver

    def screenshot(self, path: str) -> bool:
        d = self.get_driver()
        if not d:
            return False
        try:
            return d.save_screenshot(path)
        except Exception:
            return False

    def save_pdf(self, path: str, print_background: bool = True) -> bool:
        d = self.get_driver()
        if not d:
            return False
        try:
            result = d.execute_cdp_cmd("Page.printToPDF", {"printBackground": print_background})
            data = base64.b64decode(result.get("data", ""))
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            return False

class MultiDriverManager:
    def __init__(self):
        self.instances: Dict[str, ExtendedHeadless] = {}

    def create(
        self,
        name: str,
        proxy: Optional[str] = None,
        stealth: bool = False,
        download_dir: Optional[str] = None,
        auto_install: bool = True,
        profile_dir: Optional[str] = None,
        chrome_driver_path: Optional[str] = None,
        **kwargs,
    ) -> ExtendedHeadless:
        inst = ExtendedHeadless(
            proxy=proxy,
            stealth=stealth,
            download_dir=download_dir,
            auto_install=auto_install,
            profile_dir=profile_dir,
            chrome_driver_path=chrome_driver_path,
            **kwargs,
        )
        self.instances[name] = inst
        return inst

    def get(self, name: str) -> Optional[ExtendedHeadless]:
        return self.instances.get(name)

    def quit(self, name: str) -> None:
        inst = self.instances.pop(name, None)
        if inst:
            inst.quit()

    def quit_all(self) -> None:
        keys = list(self.instances.keys())
        for k in keys:
            self.quit(k)
