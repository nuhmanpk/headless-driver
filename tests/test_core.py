import unittest
from headless.core import Headless

class TestHeadless(unittest.TestCase):
    def test_driver_creation_and_quit(self):
        hl = Headless(headless=True)
        driver = hl.get_driver()
        self.assertIsNotNone(driver)
        hl.quit()
        self.assertIsNone(hl._driver)

if __name__ == "__main__":
    unittest.main()
