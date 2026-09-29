import os
import base64
from typing import Optional, Dict
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver
from .core import (Headless as CoreHeadless, find_chromedriver_path,
                   install_chromedriver, find_chrome_binary)
from .logs import get_logger, enable_console_logging

log = get_logger("manager")


class ExtendedHeadless(CoreHeadless):
    def __init__(
        self,
        proxy: Optional[str] = None,
        stealth: bool = False,
        download_dir: Optional[str] = None,
        auto_install: bool = True,
        profile_dir: Optional[str] = None,
        chrome_driver_path: Optional[str] = None,
        chrome_binary_path: Optional[str] = None,
        verbose: bool = False,
        *args,
        **kwargs,
    ):
        if verbose:
            enable_console_logging()
        if profile_dir:
            kwargs["user_data_dir"] = profile_dir
        kwargs["chrome_driver_path"] = chrome_driver_path
        kwargs["verbose"] = verbose

        super().__init__(*args, **kwargs)
        # The base class falls back to a system chromedriver; undo that so
        # _auto_install_driver() can apply this class's own resolution order.
        self.chrome_driver_path = chrome_driver_path
        self.proxy = proxy
        self.stealth = stealth
        self.download_dir = download_dir
        self.auto_install = auto_install
        # None means "detect it": a Linux-only path is not an honest default
        # for a cross-platform API.
        self.chrome_binary_path = chrome_binary_path or find_chrome_binary()
        self._applied_stealth = False

    def _build_options(self) -> Options:
        if self.verbose:
            log.debug("[ExtendedHeadless] Building Chrome options...")
        # Build on top of the base options so user_data_dir, window_size,
        # user_agent, additional_args and the `headless` flag are honoured.
        opts = super()._build_options()

        if self.proxy:
            opts.add_argument(f"--proxy-server={self.proxy}")
            if self.verbose:
                log.debug(f"[ExtendedHeadless] Proxy set: {self.proxy}")

        if self.download_dir:
            os.makedirs(self.download_dir, exist_ok=True)
            prefs = {
                "download.default_directory": os.path.abspath(self.download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
            }
            opts.add_experimental_option("prefs", prefs)
            if self.verbose:
                log.debug(f"[ExtendedHeadless] Download directory set: {self.download_dir}")

        opts.add_experimental_option("excludeSwitches", ["enable-automation"])

        if self.chrome_binary_path and os.path.exists(self.chrome_binary_path):
            opts.binary_location = self.chrome_binary_path
            if self.verbose:
                log.debug(f"[ExtendedHeadless] Chrome binary location set: {self.chrome_binary_path}")

        return opts

    def _auto_install_driver(self) -> None:
        """Resolve a driver: explicit path > auto-install > system > Selenium Manager.

        Auto-install is preferred over a system driver because webdriver-manager
        fetches a build matching the installed Chrome, whereas a driver already
        on PATH is frequently a stale major version.
        """
        if self.chrome_driver_path or self.remote_url:
            return
        if self.auto_install:
            if self.verbose:
                log.debug("[ExtendedHeadless] Auto-installing ChromeDriver...")
            try:
                self.chrome_driver_path = install_chromedriver()
                if self.verbose:
                    log.debug(f"[ExtendedHeadless] ChromeDriver installed at: {self.chrome_driver_path}")
            except Exception as e:
                if self.verbose:
                    log.debug(f"[ExtendedHeadless] ChromeDriver auto-install failed: {e}")
        if not self.chrome_driver_path:
            # Leaving this unset lets Selenium Manager resolve a driver itself.
            self.chrome_driver_path = find_chromedriver_path()

    def get_driver(self) -> WebDriver:
        if self._driver:
            if self.verbose:
                log.debug("[ExtendedHeadless] Returning cached WebDriver instance.")
            return self._driver

        if self.verbose:
            log.debug("[ExtendedHeadless] Getting Chrome WebDriver...")
        self._auto_install_driver()
        driver = super().get_driver()
        self._apply_stealth(driver)
        if self.verbose:
            log.debug("[ExtendedHeadless] WebDriver ready.")
        return driver

    def _apply_stealth(self, driver: WebDriver) -> None:
        if not (driver and self.stealth) or self._applied_stealth:
            return
        if self.verbose:
            log.debug("[ExtendedHeadless] Applying stealth options...")
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
            if self.verbose:
                log.debug("[ExtendedHeadless] Stealth applied via selenium-stealth.")
        except Exception:
            try:
                driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {
                        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    },
                )
                self._applied_stealth = True
                if self.verbose:
                    log.debug("[ExtendedHeadless] Stealth applied via CDP script.")
            except Exception as e:
                if self.verbose:
                    log.debug(f"[ExtendedHeadless] Stealth application failed: {e}")

    def quit(self) -> None:
        super().quit()
        # A new driver would need stealth re-applied.
        self._applied_stealth = False

    def screenshot(self, path: str) -> bool:
        if self.verbose:
            log.debug(f"[ExtendedHeadless] Taking screenshot: {path}")
        try:
            d = self.get_driver()
        except Exception as e:
            if self.verbose:
                log.debug(f"[ExtendedHeadless] WebDriver not available for screenshot: {e}")
            return False
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            result = bool(d.save_screenshot(path))
            if self.verbose:
                log.debug(f"[ExtendedHeadless] Screenshot saved: {path}")
            return result
        except Exception as e:
            if self.verbose:
                log.debug(f"[ExtendedHeadless] Screenshot failed: {e}")
            return False

    def save_pdf(self, path: str, print_background: bool = True) -> bool:
        if self.verbose:
            log.debug(f"[ExtendedHeadless] Saving PDF: {path}")
        try:
            d = self.get_driver()
        except Exception as e:
            if self.verbose:
                log.debug(f"[ExtendedHeadless] WebDriver not available for PDF: {e}")
            return False
        try:
            result = d.execute_cdp_cmd("Page.printToPDF", {"printBackground": print_background})
            encoded = result.get("data") if isinstance(result, dict) else None
            if not encoded:
                if self.verbose:
                    log.debug("[ExtendedHeadless] Page.printToPDF returned no data.")
                return False
            data = base64.b64decode(encoded)
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            if self.verbose:
                log.debug(f"[ExtendedHeadless] PDF saved: {path}")
            return True
        except Exception as e:
            if self.verbose:
                log.debug(f"[ExtendedHeadless] PDF save failed: {e}")
            return False


class MultiDriverManager:
    def __init__(self, verbose: bool = False):
        self.instances: Dict[str, ExtendedHeadless] = {}
        self.verbose = verbose

    def create(
        self,
        name: str,
        proxy: Optional[str] = None,
        stealth: bool = False,
        download_dir: Optional[str] = None,
        auto_install: bool = True,
        profile_dir: Optional[str] = None,
        chrome_driver_path: Optional[str] = None,
        chrome_binary_path: Optional[str] = None,
        verbose: Optional[bool] = None,
        **kwargs,
    ) -> ExtendedHeadless:
        if verbose is None:
            verbose = self.verbose
        if self.verbose:
            log.debug(f"[MultiDriverManager] Creating instance '{name}'...")
        # Replacing a name must not orphan the browser it was bound to.
        if name in self.instances:
            self.quit(name)
        inst = ExtendedHeadless(
            proxy=proxy,
            stealth=stealth,
            download_dir=download_dir,
            auto_install=auto_install,
            profile_dir=profile_dir,
            chrome_driver_path=chrome_driver_path,
            chrome_binary_path=chrome_binary_path,
            verbose=verbose,
            **kwargs,
        )
        self.instances[name] = inst
        if self.verbose:
            log.debug(f"[MultiDriverManager] Instance '{name}' created.")
        return inst

    def get(self, name: str) -> Optional[ExtendedHeadless]:
        if self.verbose:
            log.debug(f"[MultiDriverManager] Getting instance '{name}'...")
        return self.instances.get(name)

    def quit(self, name: str) -> None:
        if self.verbose:
            log.debug(f"[MultiDriverManager] Quitting instance '{name}'...")
        inst = self.instances.pop(name, None)
        if inst:
            try:
                inst.quit()
            except Exception as e:
                log.warning("error quitting instance %r: %s", name, e)
            if self.verbose:
                log.debug("[MultiDriverManager] Instance %s quit.", name)

    def quit_all(self) -> None:
        if self.verbose:
            log.debug("[MultiDriverManager] Quitting all instances...")
        for k in list(self.instances.keys()):
            self.quit(k)
        if self.verbose:
            log.debug("[MultiDriverManager] All instances quit.")

    def __enter__(self) -> "MultiDriverManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.quit_all()
