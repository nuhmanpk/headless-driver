"""Typed views of search results.

Results are plain dicts, because that is what serialises, exports and
survives a round trip through a queue. For code that wants IDE completion and
type checking, :class:`SearchResult` is the same data as a dataclass::

    for hit in scraper.search("python").typed():
        hit.url, hit.title, hit.votes      # completion and type checking

It also reads like the dict (``hit["url"]``), so migrating is incremental.
With pydantic installed, :func:`pydantic_model` returns an equivalent model for
validation and JSON schema generation.
"""

from dataclasses import dataclass, field, fields, asdict
from typing import Any, Dict, Iterator, List, Mapping, Optional

_KNOWN = ("url", "title", "snippet", "engine", "favicon", "cached", "quick_answer",
          "votes", "engines", "ranks", "page")


@dataclass
class SearchResult:
    """One search result."""

    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    favicon: str = ""
    cached: Optional[str] = None
    quick_answer: Optional[str] = None
    #: Aggregate mode: how many engines returned it, which, and where.
    votes: int = 1
    engines: List[str] = field(default_factory=list)
    ranks: Dict[str, int] = field(default_factory=dict)
    #: Which results page it came from, when ``pages > 1``.
    page: int = 1
    #: Keys a custom ``result_processor`` added, preserved as they were.
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, item: Mapping[str, Any]) -> "SearchResult":
        data = {k: item[k] for k in _KNOWN if item.get(k) is not None}
        data.setdefault("url", "")
        if not data.get("engines") and item.get("engine"):
            data["engines"] = [item["engine"]]
        data["extra"] = {k: v for k, v in item.items() if k not in _KNOWN}
        return cls(**data)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        extra = out.pop("extra")
        out.update(extra)
        return out

    # -- dict-style access, for gradual migration --------------------------
    def __getitem__(self, key: str) -> Any:
        if key in self.extra:
            return self.extra[key]
        if key in {f.name for f in fields(self)}:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> Iterator[str]:
        return iter(self.as_dict())


def to_models(items) -> List[SearchResult]:
    return [SearchResult.from_dict(i) for i in items]


_pydantic_model = None


def pydantic_model():
    """A pydantic model equivalent to :class:`SearchResult` (needs pydantic)."""
    global _pydantic_model
    if _pydantic_model is None:
        try:
            from pydantic import BaseModel, ConfigDict, Field
        except ImportError as e:  # pragma: no cover - depends on environment
            raise RuntimeError("pydantic is not installed: pip install pydantic") from e

        class SearchResultModel(BaseModel):
            """One search result."""

            model_config = ConfigDict(extra="allow")

            url: str
            title: str = ""
            snippet: str = ""
            engine: str = ""
            favicon: str = ""
            cached: Optional[str] = None
            quick_answer: Optional[str] = None
            votes: int = 1
            engines: List[str] = Field(default_factory=list)
            ranks: Dict[str, int] = Field(default_factory=dict)
            page: int = 1

        _pydantic_model = SearchResultModel
    return _pydantic_model
