"""Small ANSI helpers for the command line.

Deliberately dependency-free: this package advertises itself as lightweight, so
colour and layout are a couple of hundred lines here rather than a third-party
console library. Colour is suppressed automatically when the output is not a
terminal, so piping to a file or another process yields plain text.
"""

import os
import re
import sys
import time
import shutil
import threading
import itertools
from typing import Iterable, List, Optional, Sequence

RESET = "\033[0m"
CODES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "grey": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
    "bright_white": "97",
    "bg_red": "41",
    "bg_green": "42",
    "bg_yellow": "43",
    "bg_blue": "44",
    "bg_magenta": "45",
    "bg_cyan": "46",
    "bg_grey": "100",
}

SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

# Continuous-integration systems whose log viewers render ANSI escapes. Their
# output is not a TTY, so without this colour would be dropped there.
_ANSI_CI_VARS = (
    "GITHUB_ACTIONS",     # GitHub Actions
    "GITLAB_CI",          # GitLab
    "CIRCLECI",           # CircleCI
    "TRAVIS",             # Travis
    "BUILDKITE",          # Buildkite
    "DRONE",              # Drone
    "APPVEYOR",           # AppVeyor
    "TEAMCITY_VERSION",   # TeamCity
    "CODEBUILD_BUILD_ID",  # AWS CodeBuild
    "AWS_EXECUTION_ENV",   # AWS CodePipeline / ECS / Lambda tooling
)

# Windows terminals that speak ANSI without needing the console mode changed.
_WINDOWS_ANSI_VARS = ("WT_SESSION", "ANSICON", "ConEmuANSI", "TERM_PROGRAM")

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_windows_ansi_ready: Optional[bool] = None


def enable_windows_ansi() -> bool:
    """Turn on ANSI escape handling for the Windows console.

    Windows 10 and later can interpret escape sequences, but only once
    ENABLE_VIRTUAL_TERMINAL_PROCESSING is set on the console handle. Done with
    ctypes so the package keeps no dependency on colorama. Returns True when
    colour is usable; the result is computed once and cached.
    """
    global _windows_ansi_ready
    if _windows_ansi_ready is not None:
        return _windows_ansi_ready
    if os.name != "nt":
        _windows_ansi_ready = True
        return True
    if any(os.environ.get(var) for var in _WINDOWS_ANSI_VARS):
        _windows_ansi_ready = True
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ok = False
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            if mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING:
                ok = True
                continue
            merged = mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if kernel32.SetConsoleMode(handle, merged):
                ok = True
        _windows_ansi_ready = ok
    except Exception:
        # An old console, or a redirected handle: fall back to plain text.
        _windows_ansi_ready = False
    return _windows_ansi_ready


def supports_color(stream=None) -> bool:
    """Decide whether `stream` can take ANSI colour.

    Order of precedence: NO_COLOR, FORCE_COLOR, a dumb terminal, then whether
    this is a terminal at all, and finally CI systems that render ANSI in their
    log viewers — but only for the process's own stdout and stderr. A buffer or
    file the caller passes in (``io.StringIO``, an open log file) stays plain
    on CI too, or captured output would fill with escape codes.
    """
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    forced = bool(os.environ.get("FORCE_COLOR"))
    if os.environ.get("TERM") == "dumb" and not forced:
        return False
    if os.name == "nt" and not enable_windows_ansi() and not forced:
        return False
    if forced:
        return True
    try:
        if stream.isatty():
            return True
    except Exception:
        pass
    return _is_process_output(stream) and any(os.environ.get(var) for var in _ANSI_CI_VARS)


def _is_process_output(stream) -> bool:
    """Whether `stream` writes to this process's stdout or stderr descriptor."""
    try:
        return stream.fileno() in (1, 2)
    except Exception:  # StringIO and friends have no descriptor
        return False


def supports_unicode(stream=None) -> bool:
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "✓⠋─".encode(encoding or "ascii")
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def visible_width(text: str) -> int:
    """Length of `text` ignoring any ANSI escape sequences it contains."""
    width, in_escape = 0, False
    for ch in text:
        if in_escape:
            in_escape = ch not in "mK"
        elif ch == "\033":
            in_escape = True
        else:
            width += 1
    return width


_ANSI_RE = re.compile(r"\033\[[0-9;]*[mK]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def truncate(text: str, limit: int) -> str:
    """Shorten `text` to `limit` visible characters, ANSI escapes excepted."""
    if limit <= 0:
        return ""
    if visible_width(text) <= limit:
        return text
    # Cutting through an escape sequence would corrupt the terminal state, so
    # a styled cell that does not fit loses its styling rather than its sanity.
    plain = strip_ansi(text)
    return plain[: max(0, limit - 1)] + "…"


class Console:
    """Styled output with graceful degradation to plain ASCII."""

    def __init__(self, stream=None, color: Optional[bool] = None,
                 unicode: Optional[bool] = None, width: Optional[int] = None):
        self.stream = stream or sys.stdout
        self.color = supports_color(self.stream) if color is None else color
        self.unicode = supports_unicode(self.stream) if unicode is None else unicode
        self._width = width

    # -- primitives ------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    @property
    def width(self) -> int:
        if self._width:
            return self._width
        try:
            return max(40, min(shutil.get_terminal_size((80, 24)).columns, 120))
        except Exception:
            return 80

    def style(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(CODES[s] for s in styles if s in CODES)
        return f"\033[{codes}m{text}{RESET}" if codes else text

    def sym(self, name: str) -> str:
        fancy = {"ok": "✓", "fail": "✗", "warn": "!", "arrow": "→",
                 "dot": "•", "line": "─", "bullet": "▸", "info": "ℹ",
                 "debug": "·", "cool": "❄"}
        plain = {"ok": "+", "fail": "x", "warn": "!", "arrow": "->",
                 "dot": "*", "line": "-", "bullet": ">", "info": "i",
                 "debug": ".", "cool": "~"}
        return (fancy if self.unicode else plain)[name]

    def write(self, text: str = "") -> None:
        self.stream.write(text + "\n")
        self.stream.flush()

    # -- composites ------------------------------------------------------
    def rule(self, label: str = "") -> None:
        line = self.sym("line")
        if not label:
            self.write(self.style(line * self.width, "grey"))
            return
        left = self.style(f"{line}{line} ", "grey")
        text = self.style(label, "bold")
        pad = self.width - visible_width(left) - visible_width(text) - 1
        self.write(f"{left}{text} {self.style(line * max(0, pad), 'grey')}")

    def status(self, symbol: str, label: str, detail: str = "",
               color: str = "green") -> None:
        mark = self.style(self.sym(symbol), color, "bold")
        parts = [f" {mark} {self.style(label, 'bold')}"]
        if detail:
            parts.append(self.style(detail, "grey"))
        self.write("  ".join(parts))

    def ok(self, label: str, detail: str = "") -> None:
        self.status("ok", label, detail, "green")

    def fail(self, label: str, detail: str = "") -> None:
        self.status("fail", label, detail, "red")

    def warn(self, label: str, detail: str = "") -> None:
        self.status("warn", label, detail, "yellow")

    def note(self, text: str) -> None:
        self.write(self.style(f"   {text}", "grey"))

    def bar(self, good: int, bad: int = 0, warn: int = 0, width: int = 32) -> str:
        """A git-diffstat-style bar: green passes, yellow warnings, red failures."""
        total = good + bad + warn
        if total <= 0:
            return ""
        slots = max(1, min(width, total))
        segments = []
        used = 0
        for count, char, color in ((good, "+", "green"), (warn, "~", "yellow"),
                                   (bad, "-", "red")):
            if count <= 0:
                continue
            # Never let a non-zero count round away to nothing.
            length = max(1, round(count / total * slots))
            length = min(length, max(1, slots - used))
            segments.append(self.style(char * length, color))
            used += length
        return "".join(segments)

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[str]],
              styles: Optional[Sequence[str]] = None) -> None:
        rows = [[str(c) for c in row] for row in rows]
        if not rows:
            return
        count = len(headers)
        widths = [visible_width(h) for h in headers]
        for row in rows:
            for i in range(count):
                widths[i] = max(widths[i], visible_width(row[i]))

        # Give the last column whatever room is left rather than wrapping.
        budget = self.width - 2 - 2 * (count - 1)
        fixed = sum(widths[:-1])
        widths[-1] = max(8, min(widths[-1], budget - fixed))

        head = "  ".join(self.style(h.ljust(w), "bold", "underline")
                         for h, w in zip(headers, widths))
        self.write(" " + head)
        for row in rows:
            cells = []
            for i, (cell, w) in enumerate(zip(row, widths)):
                text = truncate(cell, w)
                pad = text + " " * max(0, w - visible_width(text)) if i < count - 1 else text
                cells.append(self.style(pad, styles[i]) if styles and styles[i] else pad)
            self.write(" " + "  ".join(cells))

    def spinner(self, label: str) -> "Spinner":
        return Spinner(self, label)


# Colour, marker and badge label for each diagnostic level.
LEVELS = {
    "debug": ("magenta", "debug", "DEBUG"),
    "info": ("bright_blue", "info", "INFO"),
    "success": ("green", "ok", "OK"),
    "warn": ("yellow", "warn", "WARN"),
    "error": ("red", "fail", "ERROR"),
}

#: Badge colours: foreground on background, so the level reads at a glance.
_BADGES = {
    "debug": ("bright_magenta",),
    "info": ("bright_blue", "bold"),
    "success": ("black", "bg_green"),
    "warn": ("black", "bg_yellow"),
    "error": ("bright_white", "bg_red", "bold"),
}

#: Fixed colours for this package's own components, so "[scraper]" is always
#: the same colour; anything else is assigned from the palette by name.
_COMPONENT_COLOURS = {
    "scraper": "cyan",
    "transport": "bright_blue",
    "health": "bright_magenta",
    "core": "green",
    "headless": "green",
    "manager": "bright_green",
    "extendedheadless": "bright_green",
    "multidrivermanager": "bright_green",
    "searchscraper": "cyan",
    "cli": "bright_cyan",
    "bench": "bright_yellow",
}
_PALETTE = ("cyan", "magenta", "blue", "green", "bright_cyan", "bright_magenta",
            "bright_blue", "bright_green")

_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*")

# Words worth a colour of their own wherever they appear in a message.
_HIGHLIGHTS = (
    (re.compile(r"https?://[^\s'\")]+"), ("bright_blue", "underline")),
    (re.compile(r"\bHTTP [45]\d\d\b"), ("bright_red", "bold")),
    (re.compile(r"\bHTTP [123]\d\d\b"), ("bright_green", "bold")),
    (re.compile(r"\b(?:blocked|rate_limited|refused|captcha|unparsed|forbidden)\b",
                re.I), ("bright_red", "bold")),
    (re.compile(r"\b(?:cooling(?: down)?|timeout|unreachable|throttled|standing down)\b",
                re.I), ("bright_yellow", "bold")),
    (re.compile(r"\b(?:ok|answering again|healthy|recovered)\b"), ("bright_green", "bold")),
    (re.compile(r"'[^'\n]{1,120}'|\"[^\"\n]{1,120}\""), ("bright_cyan",)),
    (re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?:ms|s|%| KB| MB)?(?![\w.])"), ("bold",)),
)


def component_colour(name: str) -> str:
    """A stable colour for a component tag such as ``scraper``."""
    key = name.strip().lower()
    if key in _COMPONENT_COLOURS:
        return _COMPONENT_COLOURS[key]
    # A deterministic hash: Python's own is salted per process.
    return _PALETTE[sum(ord(c) for c in key) % len(_PALETTE)]


def highlight(con: "Console", text: str, base: Sequence[str] = ()) -> str:
    """Colour `text` in `base`, picking out URLs, statuses, quotes and numbers.

    Each highlighted span restores `base` after itself, so the rest of the
    line keeps its level colour instead of dropping back to the default.
    """
    if not con.color:
        return text
    spans = []
    taken = [False] * len(text)
    for pattern, styles in _HIGHLIGHTS:
        for m in pattern.finditer(text):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            spans.append((m.start(), m.end(), styles))
    spans.sort()
    out, pos = [], 0
    for start, end, styles in spans:
        if start > pos:
            out.append(con.style(text[pos:start], *base))
        out.append(con.style(text[start:end], *styles))
        pos = end
    if pos < len(text):
        out.append(con.style(text[pos:], *base))
    return "".join(out)


def render_diag(message: str, level: str = "info", con: Optional["Console"] = None,
                timestamp: str = "", component: str = "") -> str:
    """Build one coloured diagnostic line without writing it."""
    con = con or Console(stream=sys.stderr)
    colour, symbol, label = LEVELS.get(level, LEVELS["info"])
    if level not in LEVELS:
        level = "info"

    # A leading "[Component]" tag gets its own colour so the message stands out.
    tag = component
    match = _TAG_RE.match(message)
    if match:
        tag = tag or match.group(1)
        message = message[match.end():]

    parts = []
    if timestamp:
        parts.append(con.style(timestamp, "grey"))
    marker = con.sym(symbol)
    if con.color:
        parts.append(con.style(f"{marker} {label:<5}", *_BADGES[level]))
    elif level in ("warn", "error", "success"):
        # Plain output keeps only the markers that carry meaning.
        parts.append(marker)
    if tag:
        parts.append(con.style(f"[{tag}]", component_colour(tag), "bold"))
    body_style = (colour,) if level in ("warn", "error", "success") else ()
    if level == "debug":
        body_style = ("grey",)
    parts.append(highlight(con, message, body_style))
    return " ".join(p for p in parts if p)


def diag(message: str, level: str = "info", stream=None) -> None:
    """Write a library diagnostic to stderr, coloured by level.

    Diagnostics must never land on stdout: callers pipe stdout to consumers that
    expect only real output (the CLI's ``--json`` mode, for one). Colour is
    detected per call rather than cached, so changing NO_COLOR or redirecting
    the stream takes effect immediately.
    """
    con = Console(stream=stream if stream is not None else sys.stderr)
    con.write(render_diag(message, level, con))


def debug(message: str) -> None:
    diag(message, "debug")


def info(message: str) -> None:
    diag(message, "info")


def success(message: str) -> None:
    diag(message, "success")


def warn(message: str) -> None:
    diag(message, "warn")


def error(message: str) -> None:
    diag(message, "error")


class Spinner:
    """Braille spinner that no-ops when output is not an interactive terminal."""

    def __init__(self, console: Console, label: str):
        self.console = console
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Redrawing in place only makes sense on a real terminal; piped
        # output would otherwise collect hundreds of spinner frames.
        self._active = console.is_terminal and console.unicode

    def __enter__(self) -> "Spinner":
        if not self._active:
            self.console.write(self.console.style(f" {self.label}…", "grey"))
            return self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self) -> None:
        for frame in itertools.cycle(SPINNER_FRAMES):
            if self._stop.is_set():
                return
            text = f" {self.console.style(frame, 'cyan')} {self.label}"
            self.console.stream.write(f"\r\033[K{text}")
            self.console.stream.flush()
            time.sleep(0.08)

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
            self.console.stream.write("\r\033[K")
            self.console.stream.flush()
