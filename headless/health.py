"""Per-engine circuit breaker.

Once an engine has refused a few requests in a row, asking it again straight
away only earns another refusal — and teaches it that this address keeps
coming back. :class:`EngineHealth` tracks each engine's recent outcomes and
tells the scraper to stand an engine down for a while, escalating the pause
when it fails again straight after resuming, and honouring ``Retry-After``
when the engine says how long to wait.

One instance is shared by every scraper in the process by default (see
:func:`default_health`), because the address being throttled is shared too.
"""

import time
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .logs import get_logger
from .results import EngineAttempt, REFUSAL_BY_ENGINE, STATUS_RATE_LIMITED

log = get_logger("health")

#: The longest Retry-After honoured, in seconds.
MAX_RETRY_AFTER = 24 * 3600.0

STATE_OK = "ok"
STATE_COOLING = "cooling"
#: Resumed after a stand-down: one more refusal re-trips the breaker at once.
STATE_PROBATION = "probation"


@dataclass
class _Engine:
    failures: int = 0
    pause: float = 0.0
    quiet_until: float = 0.0
    resumed_at: float = 0.0
    tripped: bool = False
    last_status: str = ""
    http_status: Optional[int] = None


class EngineHealth:
    """Thread-safe record of which engines are answering.

    ``failures_before_backoff`` consecutive refusals stand an engine down for
    ``backoff_base`` seconds. If it refuses again within one pause of resuming,
    the next pause doubles, up to ``backoff_max``; a refusal after a quiet
    spell starts again from the base. A 429 stands the engine down immediately,
    for as long as its ``Retry-After`` asks when it gives one.

    Defaults are the values measured in production against datacentre
    addresses: 3 refusals, 15 s, doubling to 120 s.
    """

    def __init__(self, backoff_base: float = 15.0, backoff_max: float = 120.0,
                 failures_before_backoff: int = 3,
                 clock: Callable[[], float] = time.monotonic):
        if failures_before_backoff < 1:
            raise ValueError("failures_before_backoff must be at least 1")
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(max(backoff_max, backoff_base))
        self.failures_before_backoff = int(failures_before_backoff)
        self._clock = clock
        self._engines: Dict[str, _Engine] = {}
        self._browser_quiet_until = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ queries
    def _get(self, engine: str) -> _Engine:
        state = self._engines.get(engine)
        if state is None:
            state = self._engines[engine] = _Engine()
        return state

    def resume_in(self, engine: str) -> float:
        """Seconds until `engine` may be asked again; 0 when it may be now."""
        with self._lock:
            state = self._engines.get(engine)
            if state is None:
                return 0.0
            return max(0.0, state.quiet_until - self._clock())

    def is_cooling(self, engine: str) -> bool:
        return self.resume_in(engine) > 0

    def state(self, engine: str) -> Dict[str, Any]:
        with self._lock:
            state = self._engines.get(engine) or _Engine()
            now = self._clock()
            remaining = max(0.0, state.quiet_until - now)
            if remaining > 0:
                label = STATE_COOLING
            elif state.tripped:
                label = STATE_PROBATION
            else:
                label = STATE_OK
            return {"state": label, "resume_in": round(remaining, 1),
                    "failures": state.failures, "pause": state.pause,
                    "last_status": state.last_status or None,
                    "http_status": state.http_status}

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Every engine seen so far, as ``{engine: state()}``."""
        with self._lock:
            names = list(self._engines)
        return {name: self.state(name) for name in names}

    # ------------------------------------------------------------ updates
    def record(self, attempt: EngineAttempt) -> Optional[str]:
        """Fold one attempt in. Returns the new state when it changed."""
        engine = attempt.engine
        with self._lock:
            state = self._get(engine)
            now = self._clock()
            state.last_status = attempt.status
            state.http_status = attempt.http_status

            if attempt.status not in REFUSAL_BY_ENGINE:
                # Timeouts and unreachable hosts are the network's doing, and
                # `unparsed` may be our own selector rot: none of them say the
                # engine is refusing this address, so they neither trip nor
                # reset the breaker. Only a real answer resets it.
                if attempt.blocked:
                    return None
                recovered = state.tripped
                state.failures = 0
                state.pause = 0.0
                state.tripped = False
                if recovered:
                    log.info("%s is answering again", engine)
                    return STATE_OK
                return None

            if now < state.quiet_until:
                # A request already in flight when the breaker opened.
                return None
            state.failures += 1
            rate_limited = attempt.status == STATUS_RATE_LIMITED
            threshold = 1 if (state.tripped or rate_limited) else self.failures_before_backoff
            if state.failures < threshold:
                return None

            if state.tripped and state.pause and now - state.resumed_at <= state.pause:
                pause = min(state.pause * 2, self.backoff_max)
            else:
                pause = self.backoff_base
            if rate_limited and attempt.retry_after:
                # The engine said how long; that beats any guess of ours —
                # within reason: a day at most, and never a non-number.
                asked = float(attempt.retry_after)
                if asked == asked and asked != float("inf"):
                    pause = max(pause, min(asked, MAX_RETRY_AFTER))
            state.pause = pause
            state.quiet_until = now + pause
            state.resumed_at = state.quiet_until
            state.failures = 0
            state.tripped = True
            why = f"HTTP {attempt.http_status}" if attempt.http_status else attempt.status
            log.warning("%s cooling down for %.0fs (%s)", engine, pause, why)
            return STATE_COOLING

    def trip(self, engine: str, seconds: Optional[float] = None) -> None:
        """Stand `engine` down now, e.g. after an out-of-band signal."""
        with self._lock:
            state = self._get(engine)
            pause = self.backoff_base if seconds is None else float(seconds)
            state.pause = pause
            state.quiet_until = self._clock() + pause
            state.resumed_at = state.quiet_until
            state.tripped = True
            state.failures = 0

    def reset(self, engine: Optional[str] = None) -> None:
        """Forget everything about `engine`, or about every engine."""
        with self._lock:
            if engine is None:
                self._engines.clear()
                self._browser_quiet_until = 0.0
            else:
                self._engines.pop(engine, None)

    # ------------------------------------------------------ browser switch
    def withdraw_browser(self, seconds: float) -> None:
        """Stop launching Chrome for `seconds`: it cannot get past an IP block."""
        with self._lock:
            until = self._clock() + seconds
            if until > self._browser_quiet_until:
                if self._browser_quiet_until <= self._clock():
                    log.warning("address looks throttled; not launching a browser "
                                "for %.0fs", seconds)
                self._browser_quiet_until = until

    def restore_browser(self) -> None:
        with self._lock:
            self._browser_quiet_until = 0.0

    def browser_resume_in(self) -> float:
        with self._lock:
            return max(0.0, self._browser_quiet_until - self._clock())

    def browser_allowed(self) -> bool:
        return self.browser_resume_in() <= 0


_default: Optional[EngineHealth] = None
_default_lock = threading.Lock()


def default_health() -> EngineHealth:
    """The process-wide :class:`EngineHealth` scrapers share unless told otherwise."""
    global _default
    with _default_lock:
        if _default is None:
            _default = EngineHealth()
        return _default


def reset_default_health() -> None:
    """Clear the shared breaker — chiefly for tests and long-lived REPLs."""
    default_health().reset()
