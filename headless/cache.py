"""Result caching, so a pipeline never asks an engine the same thing twice.

The cache sits under each engine: what is stored is one engine's answer to one
query (and page, region, result limit), not a whole search. So a cached Brave
answer serves a later ``mode="first"`` search *and* an aggregate search that
includes Brave — and a cached engine can answer even while its circuit
breaker is open, because no request is made.

Only real answers are cached: ``ok`` for ``ttl`` seconds and ``empty`` for
``empty_ttl`` (shorter, because an empty answer is more likely to be a soft
block). Refusals are never cached.

::

    AdvancedSearchScraper(cache="memory")                  # per process
    AdvancedSearchScraper(cache="sqlite:///~/.cache/hd.db")  # on disk, shared by processes
    AdvancedSearchScraper(cache="redis://localhost:6379/0")  # shared by a fleet
    AdvancedSearchScraper(cache=MyCache())                 # anything with get/set
"""

import os
import json
import time
import hashlib
import sqlite3
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Union

from .logs import get_logger

log = get_logger("cache")

DEFAULT_TTL = 24 * 3600.0
DEFAULT_EMPTY_TTL = 3600.0


def cache_key(engine: str, query: str, limit: int, page: int = 1,
              region: Optional[str] = None) -> str:
    """A stable key for one engine's answer: queries differing only in case or
    spacing share it."""
    normalised = " ".join(query.split()).lower()
    raw = json.dumps([engine, normalised, int(limit), int(page), region or ""],
                     separators=(",", ":"))
    return "hd:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


class SearchCache:
    """The interface: ``get`` returns a stored value or None; ``set`` stores one.

    Values are JSON-serialisable dicts. Any object with these two methods can be
    passed as ``cache=``.
    """

    def get(self, key: str) -> Optional[Dict[str, Any]]:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, key: str, value: Dict[str, Any], ttl: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def clear(self) -> None:  # pragma: no cover - optional
        pass

    def close(self) -> None:
        pass


class MemoryCache(SearchCache):
    """A bounded, thread-safe, in-process LRU cache."""

    def __init__(self, maxsize: int = 10000, clock=time.time):
        self.maxsize = maxsize
        self._clock = clock
        self._data: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires, value = entry
            if expires < self._clock():
                del self._data[key]
                return None
            self._data.move_to_end(key)
        return json.loads(value)

    def set(self, key: str, value: Dict[str, Any], ttl: float) -> None:
        # Store an independent copy, as the other backends do: a caller editing
        # its results must not edit the cache, and what cannot be serialised
        # fails here exactly as it would in SQLite or Redis.
        frozen = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._data[key] = (self._clock() + ttl, frozen)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class SQLiteCache(SearchCache):
    """An on-disk cache in one SQLite file, safe across threads and processes."""

    def __init__(self, path: str, clock=time.time):
        self.path = os.path.expanduser(path)
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("CREATE TABLE IF NOT EXISTS results ("
                             "key TEXT PRIMARY KEY, expires REAL, value TEXT)")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT expires, value FROM results WHERE key = ?",
                                   (key,)).fetchone()
        if row is None:
            return None
        now = self._clock()
        if row[0] < now:
            # Only if still expired: another process may have just refreshed it.
            with self._lock, self._db:
                self._db.execute("DELETE FROM results WHERE key = ? AND expires < ?", (key, now))
            return None
        return json.loads(row[1])

    def set(self, key: str, value: Dict[str, Any], ttl: float) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                             (key, self._clock() + ttl, json.dumps(value, ensure_ascii=False)))

    def purge(self) -> int:
        """Delete expired rows; returns how many."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM results WHERE expires < ?",
                                    (self._clock(),)).rowcount

    def clear(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM results")

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass


class RedisCache(SearchCache):
    """A cache shared by every worker that can reach one Redis.

    Pass a URL (needs the ``redis`` package) or an existing client — anything
    with ``get``, ``set(key, value, ex=seconds)`` and ``scan_iter``.
    """

    def __init__(self, url_or_client: Union[str, Any] = "redis://localhost:6379/0",
                 prefix: str = "headless-driver:"):
        if isinstance(url_or_client, str):
            try:
                import redis
            except ImportError as e:
                raise RuntimeError('the Redis cache needs the redis package: '
                                   'pip install "headless-driver[redis]"') from e
            self.client = redis.Redis.from_url(url_or_client)
        else:
            self.client = url_or_client
        self.prefix = prefix

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self.client.get(self.prefix + key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def set(self, key: str, value: Dict[str, Any], ttl: float) -> None:
        self.client.set(self.prefix + key, json.dumps(value, ensure_ascii=False),
                        ex=max(1, int(ttl)))

    def clear(self) -> None:
        for key in self.client.scan_iter(match=self.prefix + "*"):
            self.client.delete(key)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass


def make_cache(spec: Union[None, bool, str, SearchCache]) -> Optional[SearchCache]:
    """Build a cache from ``cache=``: None/False, True/"memory", "sqlite:///path",
    a bare ``.db``/``.sqlite`` path, "redis://…", or a cache object."""
    if spec is None or spec is False:
        return None
    if spec is True or spec == "memory":
        return MemoryCache()
    if isinstance(spec, str):
        if spec.startswith(("redis://", "rediss://", "unix://")):
            return RedisCache(spec)
        if spec.startswith("sqlite:///"):
            return SQLiteCache(spec[len("sqlite:///"):])
        if spec.endswith((".db", ".sqlite", ".sqlite3")):
            return SQLiteCache(spec)
        raise ValueError(f"unrecognised cache {spec!r}: use 'memory', "
                         "'sqlite:///path.db' or 'redis://host:port/db'")
    if hasattr(spec, "get") and hasattr(spec, "set"):
        return spec
    raise TypeError("cache must be None, 'memory', a sqlite/redis URL, or an "
                    "object with get() and set()")
