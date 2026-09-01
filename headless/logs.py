"""Logging for the library.

A library must not write to the console uninvited: it is embedded in someone
else's daemon, whose log stream it has no right to. Everything the package has
to say goes through the ``headless`` logger, which carries a ``NullHandler`` so
it stays silent until the application asks for output — either by configuring
``logging`` itself, or by passing ``verbose=True``, which calls
:func:`enable_console_logging` on the caller's behalf.
"""

import logging
from typing import Optional

LOGGER_NAME = "headless"

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())

_console_handler: Optional[logging.Handler] = None


def get_logger(name: str = "") -> logging.Logger:
    """Return the package logger, or a child of it (``headless.scraper``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


class ConsoleHandler(logging.Handler):
    """Render records through the terminal UI, coloured by level."""

    _LEVELS = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def emit(self, record: logging.LogRecord) -> None:
        from .ui import diag  # imported late: ui must not depend on logs

        try:
            message = self.format(record)
            if not message.startswith("["):
                component = record.name.split(".")[-1]
                if component and component != LOGGER_NAME:
                    message = f"[{component}] {message}"
            diag(message, self._LEVELS.get(record.levelno, "info"))
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def enable_console_logging(level: int = logging.DEBUG) -> logging.Handler:
    """Send this package's log records to stderr, coloured.

    Idempotent, and scoped to the ``headless`` logger, so it never touches the
    application's root logger configuration.
    """
    global _console_handler
    if _console_handler is None:
        _console_handler = ConsoleHandler()
        _console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(_console_handler)
    _console_handler.setLevel(level)
    if logger.level == logging.NOTSET or logger.level > level:
        logger.setLevel(level)
    return _console_handler


def disable_console_logging() -> None:
    """Undo :func:`enable_console_logging`."""
    global _console_handler
    if _console_handler is not None:
        logger.removeHandler(_console_handler)
        _console_handler = None
