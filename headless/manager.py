import os
import base64
from typing import Optional, Dict
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
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
        chrome_binary_path: Optional[str] = "/usr/bin/chromium-browser",
        verbose: bool = False,
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
        self.chrome_driver_path = chrome_driver_path
        self.chrome_binary_path = chrome_binary_path
        self.verbose = verbose
        self._applied_stealth = False

    def _build_options(self) -> Options:
        if self.verbose:
            print("[ExtendedHeadless] Building Chrome options...")
        opts = Options()
        if self.proxy:
            opts.add_argument(f"--proxy-server={self.proxy}")
            if self.verbose:
                print(f"[ExtendedHeadless] Proxy set: {self.proxy}")

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
                print(f"[ExtendedHeadless] Download directory set: {self.download_dir}")

        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        # --- Linux headless / Codespaces fixes ---
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        if self.chrome_binary_path and os.path.exists(self.chrome_binary_path):
            opts.binary_location = self.chrome_binary_path
            if self.verbose:
                print(f"[ExtendedHeadless] Chrome binary location set: {self.chrome_binary_path}")

        return opts

    def _auto_install_driver(self):
        if not self.chrome_driver_path and self.auto_install:
            if self.verbose:
                print("[ExtendedHeadless] Auto-installing ChromeDriver...")
            try:
                path = ChromeDriverManager().install()
                self.chrome_driver_path = path
                if self.verbose:
                    print(f"[ExtendedHeadless] ChromeDriver installed at: {path}")
            except Exception as e:
                if self.verbose:
                    print(f"[ExtendedHeadless] ChromeDriver auto-install failed: {e}")

    def get_driver(self) -> WebDriver:
        if self.verbose:
            print("[ExtendedHeadless] Getting Chrome WebDriver...")
        self._auto_install_driver()
        service = Service(self.chrome_driver_path) if self.chrome_driver_path else Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=self._build_options())

        if driver and self.stealth and not self._applied_stealth:
            if self.verbose:
                print("[ExtendedHeadless] Applying stealth options...")
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
                    print("[ExtendedHeadless] Stealth applied via selenium-stealth.")
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
                        print("[ExtendedHeadless] Stealth applied via CDP script.")
                except Exception as e:
                    if self.verbose:
                        print(f"[ExtendedHeadless] Stealth application failed: {e}")

        self._driver = driver
        if self.verbose:
            print("[ExtendedHeadless] WebDriver ready.")
        return driver

    def screenshot(self, path: str) -> bool:
        if self.verbose:
            print(f"[ExtendedHeadless] Taking screenshot: {path}")
        d = self.get_driver()
        if not d:
            if self.verbose:
                print("[ExtendedHeadless] WebDriver not available for screenshot.")
            return False
        try:
            result = d.save_screenshot(path)
            if self.verbose:
                print(f"[ExtendedHeadless] Screenshot saved: {path}")
            return result
        except Exception as e:
            if self.verbose:
                print(f"[ExtendedHeadless] Screenshot failed: {e}")
            return False

    def save_pdf(self, path: str, print_background: bool = True) -> bool:
        if self.verbose:
            print(f"[ExtendedHeadless] Saving PDF: {path}")
        d = self.get_driver()
        if not d:
            if self.verbose:
                print("[ExtendedHeadless] WebDriver not available for PDF.")
            return False
        try:
            result = d.execute_cdp_cmd("Page.printToPDF", {"printBackground": print_background})
            data = base64.b64decode(result.get("data", ""))
            with open(path, "wb") as f:
                f.write(data)
            if self.verbose:
                print(f"[ExtendedHeadless] PDF saved: {path}")
            return True
        except Exception as e:
            if self.verbose:
                print(f"[ExtendedHeadless] PDF save failed: {e}")
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
        chrome_binary_path: Optional[str] = "/usr/bin/chromium-browser",
        verbose: bool = None,
        **kwargs,
    ) -> ExtendedHeadless:
        if verbose is None:
            verbose = self.verbose
        if self.verbose:
            print(f"[MultiDriverManager] Creating instance '{name}'...")
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
            print(f"[MultiDriverManager] Instance '{name}' created.")
        return inst

    def get(self, name: str) -> Optional[ExtendedHeadless]:
        if self.verbose:
            print(f"[MultiDriverManager] Getting instance '{name}'...")
        return self.instances.get(name)

    def quit(self, name: str) -> None:
        if self.verbose:
            print(f"[MultiDriverManager] Quitting instance '{name}'...")
        inst = self.instances.pop(name, None)
        if inst:
            inst.quit()
            if self.verbose:
                print(f"[MultiDriverManager] Instance '{name}' quit.")

    def quit_all(self) -> None:
        if self.verbose:
            print("[MultiDriverManager] Quitting all instances...")
        keys = list(self.instances.keys())
        for k in keys:
            self.quit(k)
        if self.verbose:
            print("[MultiDriverManager] All instances quit.")
