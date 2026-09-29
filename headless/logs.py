"""Logging for the library.

A library must not write to the console uninvited: it is embedded in someone
else's daemon, whose log stream it has no right to. Everything the package has
to say goes through the ``headless`` logger, which carries a ``NullHandler`` so
it stays silent until the application asks for output — either by configuring
``logging`` itself, by passing ``verbose=True``, which calls
:func:`enable_console_logging` on the caller's behalf, or by setting
``HEADLESS_DRIVER_LOG=debug|info|warning`` in the environment.

Output is coloured by level whenever the stream is a terminal. Applications
that configure logging themselves can have the same colours with
:class:`ColorFormatter`, or turn them on for the console handlers they already
have with :func:`colorize_logging`.
"""

import os
import sys
import time
import logging
import warnings
from typing import Dict, Iterable, List, Optional

LOGGER_NAME = "headless"

#: Other loggers the CLI (or ``third_party=True``) renders in the same style,
#: so a warning from webdriver-manager or urllib3 does not arrive as a plain,
#: uncoloured line between coloured ones.
THIRD_PARTY_LOGGERS = ("WDM", "selenium", "urllib3", "curl_cffi", "py.warnings")

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())

_console_handler: Optional[logging.Handler] = None
_third_party_state: Dict[str, tuple] = {}
_captured_warnings = False

_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


def get_logger(name: str = "") -> logging.Logger:
    """Return the package logger, or a child of it (``headless.scraper``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def _level_name(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


def _component(record: logging.LogRecord) -> str:
    name = record.name
    if name == LOGGER_NAME:
        return ""
    if name.startswith(LOGGER_NAME + "."):
        return name.split(".")[-1]
    if name == "py.warnings":
        return "warning"
    return name.split(".")[0]


class ColorFormatter(logging.Formatter):
    """Render any log record the way this package's console output looks.

    Drop it onto a handler the application already owns::

        handler = logging.StreamHandler()
        handler.setFormatter(ColorFormatter())
        logging.basicConfig(handlers=[handler], level=logging.INFO)

    `color` defaults to "whatever the handler's stream supports" (honouring
    ``NO_COLOR`` and ``FORCE_COLOR``); `timestamps` prefixes ``HH:MM:SS``.
    Exceptions and stack traces are appended, dimmed.
    """

    def __init__(self, color: Optional[bool] = None, timestamps: bool = True,
                 stream=None, fmt: Optional[str] = None):
        super().__init__(fmt or "%(message)s")
        self.color = color
        self.timestamps = timestamps
        self.stream = stream

    def _console(self):
        from .ui import Console  # imported late: ui must not depend on logs
        return Console(stream=self.stream if self.stream is not None else sys.stderr,
                       color=self.color)

    def format(self, record: logging.LogRecord) -> str:
        from .ui import render_diag

        con = self._console()
        message = record.getMessage()
        stamp = ""
        if self.timestamps:
            stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        component = "" if message.startswith("[") else _component(record)
        line = render_diag(message, _level_name(record.levelno), con,
                           timestamp=stamp, component=component)
        extra = []
        if record.exc_info:
            extra.append(self.formatException(record.exc_info))
        if record.stack_info:
            extra.append(self.formatStack(record.stack_info))
        for block in extra:
            line += "\n" + con.style(block, "grey")
        return line


class ConsoleHandler(logging.Handler):
    """Write records to stderr, coloured by level.

    The stream is resolved on every record rather than captured once, so
    redirecting ``sys.stderr`` (as test runners and the CLI's spinners do)
    takes effect immediately.
    """

    _LEVELS = _LEVEL_NAMES

    def __init__(self, level: int = logging.NOTSET, timestamps: bool = False):
        super().__init__(level)
        self.timestamps = timestamps

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stderr
            formatter = ColorFormatter(timestamps=self.timestamps, stream=stream)
            stream.write(formatter.format(record) + "\n")
            stream.flush()
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def enable_console_logging(level: int = logging.DEBUG, *, third_party: bool = False,
                           capture_warnings: bool = False,
                           timestamps: bool = False) -> logging.Handler:
    """Send this package's log records to stderr, coloured.

    Idempotent, and scoped to the ``headless`` logger, so it never touches the
    application's root logger configuration.

    `third_party` also renders webdriver-manager, Selenium, urllib3 and
    curl_cffi warnings in the same style; `capture_warnings` routes Python
    ``warnings`` through it too. Both are meant for programs that own the
    console — the CLI turns them on — not for libraries embedding this one.
    """
    global _console_handler
    if _console_handler is None:
        _console_handler = ConsoleHandler()
        logger.addHandler(_console_handler)
    _console_handler.timestamps = timestamps
    _console_handler.setLevel(level)
    if logger.level == logging.NOTSET or logger.level > level:
        logger.setLevel(level)
    if third_party:
        _attach_third_party(max(level, logging.WARNING))
    if capture_warnings:
        _capture_warnings()
    return _console_handler


def _attach_third_party(level: int) -> None:
    for name in THIRD_PARTY_LOGGERS:
        other = logging.getLogger(name)
        if name not in _third_party_state:
            _third_party_state[name] = (other.level, other.propagate)
            other.addHandler(_console_handler)
            # Otherwise a root handler the program set up prints it twice.
            other.propagate = False
        other.setLevel(level)


def _capture_warnings() -> None:
    global _captured_warnings
    if not _captured_warnings:
        logging.captureWarnings(True)
        _captured_warnings = True
        if "py.warnings" not in _third_party_state and _console_handler is not None:
            other = logging.getLogger("py.warnings")
            _third_party_state["py.warnings"] = (other.level, other.propagate)
            other.addHandler(_console_handler)
            other.propagate = False


def disable_console_logging() -> None:
    """Undo :func:`enable_console_logging`."""
    global _console_handler, _captured_warnings
    if _console_handler is not None:
        logger.removeHandler(_console_handler)
        for name, (level, propagate) in _third_party_state.items():
            other = logging.getLogger(name)
            other.removeHandler(_console_handler)
            other.setLevel(level)
            other.propagate = propagate
        _third_party_state.clear()
        _console_handler = None
    if _captured_warnings:
        logging.captureWarnings(False)
        _captured_warnings = False


def colorize_logging(target: Optional[logging.Logger] = None,
                     timestamps: bool = True) -> List[logging.Handler]:
    """Give the console handlers an application already has this package's colours.

    Walks `target` (the root logger by default) and swaps the formatter on every
    ``StreamHandler`` that writes to a terminal — files and pipes are left
    alone, so log files stay plain. Returns the handlers it changed. Call it
    after ``logging.basicConfig()``::

        logging.basicConfig(level=logging.INFO)
        headless.colorize_logging()
    """
    target = target if target is not None else logging.getLogger()
    changed = []
    for handler in list(target.handlers):
        if not isinstance(handler, logging.StreamHandler):
            continue
        if isinstance(handler, logging.FileHandler):
            continue
        stream = getattr(handler, "stream", None)
        try:
            is_tty = bool(stream is not None and stream.isatty())
        except Exception:
            is_tty = False
        if not (is_tty or os.environ.get("FORCE_COLOR")):
            continue
        handler.setFormatter(ColorFormatter(timestamps=timestamps, stream=stream))
        changed.append(handler)
    return changed


def _level_from_env(value: str) -> Optional[int]:
    value = (value or "").strip().upper()
    if not value or value in ("0", "OFF", "FALSE", "NO", "NONE"):
        return None
    if value in ("1", "TRUE", "YES", "ON"):
        return logging.INFO
    if value == "WARN":
        value = "WARNING"
    level = logging.getLevelName(value)
    return level if isinstance(level, int) else None


def configure_from_env(environ: Optional[Iterable] = None) -> Optional[logging.Handler]:
    """Honour ``HEADLESS_DRIVER_LOG`` — an explicit, out-of-code opt-in."""
    env = os.environ if environ is None else environ
    level = _level_from_env(env.get("HEADLESS_DRIVER_LOG", ""))
    if level is None:
        return None
    return enable_console_logging(level, timestamps=True)


def _quiet_import_warnings() -> None:
    # urllib3 warns once at import on LibreSSL builds of Python; that is not
    # something a user of this package can act on, and it is printed plain,
    # before any of this module's colouring is in place.
    warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")


_quiet_import_warnings()
configure_from_env()
