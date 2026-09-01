"""Structured search results.

``search()`` used to return a bare list, which made "no such page exists" and
"the engine refused me" the same value. They call for opposite reactions — move
on, versus slow down and change address — so the outcome of every engine
attempt is reported instead of discarded.

:class:`SearchResponse` behaves like the list it replaces, so existing code that
iterates, indexes, or measures the return value keeps working unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

# Why an engine attempt ended.
STATUS_OK = "ok"                    # results were extracted
STATUS_EMPTY = "empty"              # the page parsed, and held no results
STATUS_BLOCKED = "blocked"          # a bot check was served instead of results
STATUS_TIMEOUT = "timeout"          # the page did not load in time
STATUS_UNREACHABLE = "unreachable"  # DNS failure, refused connection, TLS error
STATUS_ERROR = "error"              # anything else went wrong

#: Statuses that mean "the engine refused us", as opposed to "it had nothing".
REFUSED_STATUSES = frozenset({STATUS_BLOCKED, STATUS_TIMEOUT, STATUS_UNREACHABLE,
                              STATUS_ERROR})


@dataclass
class EngineAttempt:
    """One engine's turn at answering a query."""

    engine: str
    status: str
    count: int = 0
    reason: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def blocked(self) -> bool:
        """True when the engine refused us rather than simply having nothing."""
        return self.status in REFUSED_STATUSES

    def as_dict(self) -> Dict[str, Any]:
        return {"engine": self.engine, "status": self.status, "count": self.count,
                "reason": self.reason, "elapsed": round(self.elapsed, 3)}


@dataclass
class SearchResponse:
    """What a search found, and what happened on the way.

    Acts as a sequence of result dictionaries for backwards compatibility::

        for hit in scraper.search("query"):     # still works
            print(hit["url"])

        response = scraper.search("query")      # and now this works too
        if response.blocked:
            back_off()
    """

    query: str = ""
    results: List[Dict[str, Any]] = field(default_factory=list)
    engine: Optional[str] = None
    attempts: List[EngineAttempt] = field(default_factory=list)
    elapsed: float = 0.0

    # -- sequence protocol, so this is a drop-in for the old list ---------
    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index):
        return self.results[index]

    def __bool__(self) -> bool:
        return bool(self.results)

    def __contains__(self, item) -> bool:
        return item in self.results

    def __eq__(self, other) -> bool:
        # Lets existing assertions like `assert response == []` keep passing.
        if isinstance(other, SearchResponse):
            return (self.query, self.results, self.engine) == (
                other.query, other.results, other.engine)
        if isinstance(other, list):
            return self.results == other
        return NotImplemented

    def __repr__(self) -> str:
        return (f"SearchResponse(query={self.query!r}, engine={self.engine!r}, "
                f"results={len(self.results)}, blocked={self.blocked})")

    # -- what actually happened ------------------------------------------
    @property
    def blocked(self) -> bool:
        """True when every engine refused us and none merely came up empty.

        A query nobody has an answer for returns False here: that is an empty
        result, not a refusal.
        """
        return bool(self.attempts) and all(a.blocked for a in self.attempts)

    @property
    def refused(self) -> List[EngineAttempt]:
        """Attempts that were refused rather than empty."""
        return [a for a in self.attempts if a.blocked]

    @property
    def engines_tried(self) -> List[str]:
        return [a.engine for a in self.attempts]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "engine": self.engine,
            "elapsed": round(self.elapsed, 3),
            "blocked": self.blocked,
            "results": self.results,
            "attempts": [a.as_dict() for a in self.attempts],
        }


class HeadlessDriverError(Exception):
    """Base class for this package's exceptions."""


class AllEnginesBlocked(HeadlessDriverError):
    """Raised when every engine refused, and ``raise_on_block`` is set."""

    def __init__(self, response: SearchResponse):
        self.response = response
        engines = ", ".join(
            f"{a.engine} ({a.status})" for a in response.attempts) or "none"
        super().__init__(f"every engine refused {response.query!r}: {engines}")
