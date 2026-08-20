"""Small ANSI helpers for the command line.

Deliberately dependency-free: this package advertises itself as lightweight, so
colour and layout are a couple of hundred lines here rather than a third-party
console library. Colour is suppressed automatically when the output is not a
terminal, so piping to a file or another process yields plain text.
"""

import os
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
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "grey": "90",
}

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def diag(message: str) -> None:
    """Write a library diagnostic to stderr.

    Diagnostics must never land on stdout: callers pipe stdout to consumers that
    expect only real output (the CLI's ``--json`` mode, for one).
    """
    print(message, file=sys.stderr)


def supports_color(stream=None) -> bool:
    """Follow the informal NO_COLOR / FORCE_COLOR conventions."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
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


def truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


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
                 "dot": "•", "line": "─", "bullet": "▸"}
        plain = {"ok": "+", "fail": "x", "warn": "!", "arrow": "->",
                 "dot": "*", "line": "-", "bullet": ">"}
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
                pad = text.ljust(w) if i < count - 1 else text
                cells.append(self.style(pad, styles[i]) if styles and styles[i] else pad)
            self.write(" " + "  ".join(cells))

    def spinner(self, label: str) -> "Spinner":
        return Spinner(self, label)


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
