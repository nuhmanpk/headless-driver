"""Shared test scaffolding."""

import logging
import unittest

from headless.health import reset_default_health
from headless.logs import disable_console_logging


class HermeticTestCase(unittest.TestCase):
    """A test case that starts with a clean process-wide circuit breaker.

    Scrapers share one :class:`~headless.health.EngineHealth` by default, which
    is right in production and wrong between tests: refusals recorded by one
    test would otherwise stand engines down for the next. Console logging is
    reset for the same reason.
    """

    def run(self, result=None):
        reset_default_health()
        try:
            return super().run(result)
        finally:
            reset_default_health()
            # `verbose=True` in one test must not leave output on for the next.
            disable_console_logging()
            logging.getLogger("headless").setLevel(logging.NOTSET)
