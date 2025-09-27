import unittest
from headless.manager import ExtendedHeadless
from headless.core import SearchScraper

class TestHeadlessAuto(unittest.TestCase):

    def test_driver_creation_and_quit(self):
        hl = ExtendedHeadless(auto_install=True)  # auto install driver
        driver = hl.get_driver()
        self.assertIsNotNone(driver, "Driver should be created")
        hl.quit()
        self.assertIsNone(hl._driver, "Driver should be None after quit")

    def test_search_scraper_basic(self):
        hl = ExtendedHeadless(auto_install=True)
        scraper = SearchScraper(driver=hl.get_driver(), max_results=1)
        results = scraper.search("python headless")
        self.assertIsInstance(results, list)
        if results:  # check keys only if results returned
            self.assertIn("url", results[0])
            self.assertIn("snippet", results[0])
        scraper.quit()
        hl.quit()

if __name__ == "__main__":
    unittest.main()
