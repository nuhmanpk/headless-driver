"""Structured search results.

``search()`` used to return a bare list, which made "no such page exists" and
"the engine refused me" the same value. They call for opposite reactions — move
on, versus slow down and change address — so the outcome of every engine
attempt is reported instead of discarded.

:class:`SearchResponse` behaves like the list it replaces, so existing code that
iterates, indexes, or measures the return value keeps working unchanged.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Why an engine attempt ended.
STATUS_OK = "ok"                      # results were extracted
STATUS_EMPTY = "empty"                # the page parsed, and held no results
STATUS_BLOCKED = "blocked"            # a bot check or refusal was served instead
STATUS_RATE_LIMITED = "rate_limited"  # HTTP 429: asked to slow down
STATUS_UNPARSED = "unparsed"          # a page we do not recognise: not evidence of absence
STATUS_TIMEOUT = "timeout"            # the page did not load in time
STATUS_UNREACHABLE = "unreachable"    # DNS failure, refused connection, TLS error
STATUS_ERROR = "error"                # anything else went wrong

#: Statuses that mean "the engine refused us", as opposed to "it had nothing".
REFUSED_STATUSES = frozenset({STATUS_BLOCKED, STATUS_RATE_LIMITED, STATUS_UNPARSED,
                              STATUS_TIMEOUT, STATUS_UNREACHABLE, STATUS_ERROR})

#: Statuses that mean the engine saw us and chose to refuse. Timeouts and DNS
#: failures are the network's fault; these are the engine's decision, and the
#: ones worth backing off from.
REFUSAL_BY_ENGINE = frozenset({STATUS_BLOCKED, STATUS_RATE_LIMITED})

#: Why an engine was not asked at all (see :attr:`SearchResponse.skipped`).
SKIP_COOLING = "cooling"              # its circuit breaker is open
SKIP_IGNORES_SITE = "ignores_site"    # the query uses site:, which it ignores
SKIP_BROWSER_WITHDRAWN = "browser_withdrawn"  # the address is throttled; Chrome won't help
SKIP_DUPLICATE_PROVIDER = "duplicate_provider"  # a sibling on the same index answered


@dataclass
class EngineAttempt:
    """One engine's turn at answering a query."""

    engine: str
    status: str
    count: int = 0
    reason: str = ""
    elapsed: float = 0.0
    #: The HTTP status code, when the page came over HTTP.
    http_status: Optional[int] = None
    #: Seconds the engine asked us to wait (``Retry-After``), when it said.
    retry_after: Optional[float] = None
    #: Which transport fetched the page: ``impersonate``, ``http`` or ``browser``.
    transport: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def blocked(self) -> bool:
        """True when the engine refused us rather than simply having nothing."""
        return self.status in REFUSED_STATUSES

    @property
    def rate_limited(self) -> bool:
        return self.status == STATUS_RATE_LIMITED

    def __str__(self) -> str:
        refused_by_status = bool(self.http_status) and (
            self.http_status >= 400 or self.http_status == 202)
        detail = f"HTTP {self.http_status}" if refused_by_status and self.blocked else ""
        detail = detail or self.reason
        return f"{self.engine}: {self.status}" + (f" ({detail})" if detail else "")

    def as_dict(self) -> Dict[str, Any]:
        return {"engine": self.engine, "status": self.status, "count": self.count,
                "reason": self.reason, "elapsed": round(self.elapsed, 3),
                "http_status": self.http_status, "retry_after": self.retry_after,
                "transport": self.transport}


class SearchResponse(list):
    """What a search found, and what happened on the way.

    This **is** a list of result dictionaries, so everything callers did with the
    old return value keeps working — ``json.dumps`` it, concatenate it, append to
    it, ``.count()`` it — while the extra attributes describe the search itself::

        response = scraper.search("query")
        for hit in response:
            print(hit["url"])
        if response.blocked:
            back_off()
        elif response.cooling:
            pass   # we chose not to ask: every engine is standing down
    """

    def __init__(self, query: str = "", results: Optional[List[Dict[str, Any]]] = None,
                 engine: Optional[str] = None,
                 attempts: Optional[List[EngineAttempt]] = None,
                 elapsed: float = 0.0,
                 mode: str = "first",
                 engines: Optional[List[str]] = None,
                 skipped: Optional[List[Dict[str, Any]]] = None):
        super().__init__(results or [])
        self.query = query
        self.engine = engine
        self.attempts: List[EngineAttempt] = attempts or []
        self.elapsed = elapsed
        #: ``"first"`` (walk the chain) or ``"aggregate"`` (ask several at once).
        self.mode = mode
        #: Engines that contributed results. One entry in ``"first"`` mode.
        self.engines: List[str] = engines or ([engine] if engine else [])
        #: Engines that were not asked, each ``{"engine", "reason", "resume_in"}``.
        self.skipped: List[Dict[str, Any]] = skipped or []

    # `results` stays a real attribute name for code that reads or assigns it.
    @property
    def results(self) -> List[Dict[str, Any]]:
        return self

    @results.setter
    def results(self, value: List[Dict[str, Any]]) -> None:
        self[:] = value or []

    def __repr__(self) -> str:
        return (f"SearchResponse(query={self.query!r}, engine={self.engine!r}, "
                f"results={len(self)}, blocked={self.blocked}, cooling={self.cooling})")

    # -- what actually happened ------------------------------------------
    @property
    def answered(self) -> List[EngineAttempt]:
        """Attempts where the engine really looked: ``ok`` or ``empty``."""
        return [a for a in self.attempts if not a.blocked]

    @property
    def blocked(self) -> bool:
        """True when every engine asked refused us and none merely came up empty.

        A query nobody has an answer for returns False here: that is an empty
        result, not a refusal. Engines that were skipped are not counted, so a
        search where nothing was asked is not "blocked" either — see
        :attr:`cooling`.
        """
        return bool(self.attempts) and all(a.blocked for a in self.attempts)

    @property
    def cooling(self) -> bool:
        """True when nobody was asked because engines were deliberately skipped.

        "We chose not to ask" (every eligible engine is standing down after
        earlier refusals) and "they refused us" call for different handling —
        the first needs only patience, the second is new evidence of a block.
        """
        if self.attempts or self:
            return False
        return any(s.get("reason") == SKIP_COOLING for s in self.skipped)

    @property
    def refused(self) -> List[EngineAttempt]:
        """Attempts that were refused rather than empty."""
        return [a for a in self.attempts if a.blocked]

    @property
    def rate_limited(self) -> bool:
        return any(a.rate_limited for a in self.attempts)

    @property
    def retry_after(self) -> Optional[float]:
        """The longest ``Retry-After`` any engine asked for, if any did."""
        waits = [a.retry_after for a in self.attempts if a.retry_after]
        return max(waits) if waits else None

    @property
    def engines_tried(self) -> List[str]:
        return [a.engine for a in self.attempts]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "engine": self.engine,
            "engines": list(self.engines),
            "mode": self.mode,
            "elapsed": round(self.elapsed, 3),
            "blocked": self.blocked,
            "cooling": self.cooling,
            "results": list(self),
            "attempts": [a.as_dict() for a in self.attempts],
            "skipped": list(self.skipped),
        }


class HeadlessDriverError(Exception):
    """Base class for this package's exceptions."""


class AllEnginesBlocked(HeadlessDriverError):
    """Raised when every engine refused, and ``raise_on_block`` is set."""

    def __init__(self, response: SearchResponse):
        self.response = response
        engines = ", ".join(
            f"{a.engine} ({a.status})" for a in response.attempts) or "none"
        if response.cooling and not response.attempts:
            engines = "none asked; every eligible engine is cooling down"
        super().__init__(f"every engine refused {response.query!r}: {engines}")
