"""Web search and page reading as tools for LLM agents.

Each tool is defined once here and exported in every common format, so an
agent can search the web and read pages with this package's block handling,
consensus ranking and clean Markdown — no glue code:

=====================  ==============================================
OpenAI                 ``toolkit.openai_tools()``, ``handle_openai_tool_calls()``
Anthropic              ``toolkit.anthropic_tools()``, ``handle_anthropic_tool_use()``
LangChain              ``toolkit.langchain_tools()``
LlamaIndex             ``toolkit.llamaindex_tools()``
CrewAI                 ``toolkit.crewai_tools()``
MCP                    ``headless-driver mcp`` (see :mod:`headless.mcp_server`)
=====================  ==============================================

::

    from headless.tools import Toolkit

    toolkit = Toolkit(region="us-en")
    response = client.messages.create(model=..., tools=toolkit.anthropic_tools(), ...)
    results = toolkit.handle_anthropic_tool_use(response.content)

Tools: ``web_search``, ``fetch_page``, ``extract_data`` (Playwright) and
``screenshot`` (Playwright). Every tool returns JSON text, compact enough for a
context window, and reports blocks honestly instead of pretending the web had
nothing.
"""

import os
import json
import uuid
import base64
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .logs import get_logger

log = get_logger("tools")


@dataclass(frozen=True)
class Param:
    name: str
    type: str                     # "string" | "integer" | "boolean" | "object"
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[Sequence[str]] = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: Sequence[Param]
    method: str

    def json_schema(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        for p in self.params:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = list(p.enum)
            if p.type == "object":
                prop["additionalProperties"] = {"type": "string"}
            if p.default is not None:
                prop["default"] = p.default
            props[p.name] = prop
        return {"type": "object", "properties": props,
                "required": [p.name for p in self.params if p.required],
                "additionalProperties": False}


TOOL_SPECS: List[ToolSpec] = [
    ToolSpec(
        "web_search",
        "Search the web (Brave, DuckDuckGo, Yahoo, Mojeek, Google, Bing) and return "
        "the top results with title, URL and snippet. Use mode='aggregate' to ask "
        "several independent engines at once and rank results by how many agree — "
        "slower but more reliable for finding a specific person, company or page. "
        "Supports operators such as site:example.com and \"exact phrase\". If the "
        "result says blocked=true, the engines refused the request: that is not "
        "evidence that nothing exists.",
        [Param("query", "string", "The search query.", required=True),
         Param("max_results", "integer", "How many results to return (1-30).", default=8),
         Param("mode", "string", "'first' asks engines in turn until one answers; "
               "'aggregate' asks several at once and ranks by agreement.",
               default="first", enum=("first", "aggregate")),
         Param("region", "string", "Region such as 'us-en', 'uk-en' or 'de-de'."),
         Param("pages", "integer", "Results pages to fetch per engine (1-5).", default=1)],
        "web_search"),
    ToolSpec(
        "fetch_page",
        "Fetch a web page and return its main content as clean Markdown, with "
        "navigation, ads and boilerplate removed and links kept absolute. Renders "
        "JavaScript-heavy pages in a browser automatically when needed. Use it to "
        "read a search result in full.",
        [Param("url", "string", "The page URL (http or https).", required=True),
         Param("max_tokens", "integer", "Truncate the Markdown to about this many tokens.",
               default=4000),
         Param("render", "string", "'auto' (default), 'never' or 'always' render JavaScript.",
               default="auto", enum=("auto", "never", "always"))],
        "fetch_page"),
    ToolSpec(
        "extract_data",
        "Render a page in a real browser and extract structured data with CSS "
        "selectors. 'fields' maps output names to selectors; end a selector with "
        "@attr to read an attribute (e.g. {\"title\": \"h2\", \"link\": \"a@href\"}). "
        "With 'item_selector', returns one record per matching element.",
        [Param("url", "string", "The page URL.", required=True),
         Param("fields", "object", "Output field name -> CSS selector (optionally @attr).",
               required=True),
         Param("item_selector", "string", "CSS selector for repeated items, e.g. 'article'."),
         Param("scroll", "integer", "Scroll to the bottom this many times first.", default=0)],
        "extract_data"),
    ToolSpec(
        "screenshot",
        "Take a screenshot of a web page in a real browser and save it as PNG. "
        "Returns the file path.",
        [Param("url", "string", "The page URL.", required=True),
         Param("full_page", "boolean", "Capture the whole scrollable page.", default=True)],
        "screenshot"),
]

_SPECS = {spec.name: spec for spec in TOOL_SPECS}


class Toolkit:
    """The tools, bound to one scraper configuration.

    `scraper_kwargs` go to :class:`~headless.scraper.AdvancedSearchScraper`
    (``transport``, ``proxy``, ``cache``, ``browser`` …). A cache is on by
    default (``cache="memory"``), because agents repeat themselves.
    """

    def __init__(self, scraper=None, *, max_results: int = 8, region: Optional[str] = None,
                 page_tokens: int = 4000, screenshot_dir: Optional[str] = None,
                 tools: Optional[Sequence[str]] = None, **scraper_kwargs):
        self._scraper = scraper
        self.scraper_kwargs = dict(scraper_kwargs)
        self.scraper_kwargs.setdefault("cache", "memory")
        self.max_results = max_results
        self.region = region
        self.page_tokens = page_tokens
        self.screenshot_dir = screenshot_dir
        names = list(tools) if tools else [s.name for s in TOOL_SPECS]
        unknown = set(names) - set(_SPECS)
        if unknown:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        self.specs = [_SPECS[n] for n in names]
        self._lock = threading.Lock()
        self._browser = None
        self._tmp_shots: Optional[str] = None

    # ----------------------------------------------------------- plumbing
    @property
    def scraper(self):
        with self._lock:
            if self._scraper is None:
                from .scraper import AdvancedSearchScraper
                kwargs = dict(self.scraper_kwargs)
                kwargs.setdefault("region", self.region)
                self._scraper = AdvancedSearchScraper(**kwargs)
            return self._scraper

    def _playwright(self):
        with self._lock:
            if self._browser is None:
                from .playwright_driver import PlaywrightBrowser
                # One browser thread, whichever thread (or MCP worker) calls.
                self._browser = PlaywrightBrowser(proxy=self.scraper_kwargs.get("proxy"),
                                                  dedicated_thread=True)
            return self._browser

    def _shots_dir(self) -> str:
        """One temporary folder per toolkit, not one per screenshot."""
        with self._lock:
            if self._tmp_shots is None:
                self._tmp_shots = tempfile.mkdtemp(prefix="headless-shots-")
            return self._tmp_shots

    def close(self) -> None:
        with self._lock:
            scraper, browser = self._scraper, self._browser
            self._browser = None
        if scraper is not None:
            scraper.quit()
        if browser is not None:
            browser.close()

    def __enter__(self) -> "Toolkit":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -------------------------------------------------------------- tools
    def web_search(self, query: str, max_results: Optional[int] = None, mode: str = "first",
                   region: Optional[str] = None, pages: int = 1) -> Dict[str, Any]:
        """Search the web; returns results and an honest account of what happened."""
        limit = max(1, min(int(max_results or self.max_results), 30))
        pages = max(1, min(int(pages or 1), 5))
        kwargs: Dict[str, Any] = {"max_results": limit, "mode": mode, "pages": pages}
        if region:
            # Per call, not by editing the shared scraper: tools run concurrently.
            kwargs["region"] = region
        response = self.scraper.search(query, **kwargs)
        results = []
        for item in list(response)[: limit * pages]:
            row = {"title": item.get("title", ""), "url": item.get("url", ""),
                   "snippet": item.get("snippet", "")}
            if item.get("votes"):
                row["votes"] = item["votes"]
                row["engines"] = item.get("engines", [])
            results.append(row)
        out: Dict[str, Any] = {"query": query, "results": results,
                               "engine": response.engine, "blocked": response.blocked}
        if response.cooling:
            out["note"] = "every engine is cooling down after refusals; try again shortly"
        elif response.blocked:
            out["note"] = ("the search engines refused this request (rate limit or bot "
                           "check); this is not evidence that no results exist")
        elif not results:
            out["note"] = "the engines answered but found nothing for this query"
        if response.cached:
            out["cached"] = True
        return out

    def fetch_page(self, url: str, max_tokens: Optional[int] = None,
                   render: str = "auto") -> Dict[str, Any]:
        """A page's main content as Markdown."""
        from .markdown import fetch_markdown
        _check_url(url)
        doc = fetch_markdown(url, render=render,
                             max_tokens=int(max_tokens or self.page_tokens),
                             proxy=self.scraper_kwargs.get("proxy"))
        return {"url": doc.final_url or url, "title": doc.title, "status": doc.status,
                "tokens": doc.tokens, "markdown": doc.markdown}

    def extract_data(self, url: str, fields: Dict[str, str],
                     item_selector: Optional[str] = None, scroll: int = 0) -> Any:
        """Structured data from a rendered page."""
        _check_url(url)
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields must be a non-empty object of name -> CSS selector")
        return self._playwright().extract(url, fields, item_selector=item_selector,
                                          scroll=int(scroll or 0))

    def screenshot(self, url: str, full_page: bool = True,
                   include_base64: bool = False) -> Dict[str, Any]:
        """Save a PNG screenshot; returns its path (and the bytes, if asked)."""
        _check_url(url)
        folder = self.screenshot_dir or self._shots_dir()
        path = os.path.join(folder, f"shot-{uuid.uuid4().hex[:12]}.png")
        if not self._playwright().screenshot(url, path, full_page=full_page):
            raise RuntimeError(f"could not capture {url}")
        out = {"url": url, "path": path, "bytes": os.path.getsize(path)}
        if include_base64:
            with open(path, "rb") as f:
                out["png_base64"] = base64.b64encode(f.read()).decode("ascii")
        return out

    # ---------------------------------------------------------- dispatch
    def call(self, name: str, arguments: Any = None) -> str:
        """Run tool `name` with `arguments` (a dict or JSON text); returns JSON text.

        Errors are returned as ``{"ok": false, "error": ...}`` rather than raised, which is
        what an agent loop needs: the model sees the failure and can adapt.
        """
        spec = _SPECS.get(name)
        if spec is None or spec not in self.specs:
            return _error(f"unknown tool {name!r}")
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            arguments = dict(arguments or {})
            allowed = {p.name for p in spec.params}
            unexpected = set(arguments) - allowed
            if unexpected:
                raise ValueError(f"unexpected arguments: {sorted(unexpected)}")
            missing = [p.name for p in spec.params if p.required and p.name not in arguments]
            if missing:
                raise ValueError(f"missing required arguments: {missing}")
            result = getattr(self, spec.method)(**arguments)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.debug("tool %s failed: %s", name, e)
            return _error(f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------ formats
    def openai_tools(self) -> List[Dict[str, Any]]:
        """OpenAI Chat Completions / Responses ``tools=`` entries."""
        return [{"type": "function",
                 "function": {"name": s.name, "description": s.description,
                              "parameters": s.json_schema()}} for s in self.specs]

    def anthropic_tools(self) -> List[Dict[str, Any]]:
        """Anthropic Messages API ``tools=`` entries."""
        return [{"name": s.name, "description": s.description,
                 "input_schema": s.json_schema()} for s in self.specs]

    def handle_openai_tool_calls(self, tool_calls) -> List[Dict[str, Any]]:
        """Run the ``tool_calls`` of an OpenAI assistant message; returns the
        ``role: "tool"`` messages to append."""
        out = []
        for call in tool_calls or []:
            function = _get(call, "function")
            out.append({"role": "tool", "tool_call_id": _get(call, "id"),
                        "content": self.call(_get(function, "name"),
                                             _get(function, "arguments"))})
        return out

    def handle_anthropic_tool_use(self, content) -> List[Dict[str, Any]]:
        """Run the ``tool_use`` blocks of an Anthropic response; returns the
        ``tool_result`` blocks for the next user message."""
        out = []
        for block in content or []:
            if _get(block, "type") != "tool_use":
                continue
            result = self.call(_get(block, "name"), _get(block, "input"))
            item = {"type": "tool_result", "tool_use_id": _get(block, "id"), "content": result}
            if is_error(result):
                item["is_error"] = True
            out.append(item)
        return out

    def _functions(self) -> List[Callable[..., str]]:
        """One plain, typed, documented function per tool, returning JSON text."""
        functions = []
        for spec in self.specs:
            functions.append(_typed_function(self, spec))
        return functions

    def langchain_tools(self) -> list:
        """LangChain ``StructuredTool`` objects (needs ``langchain-core``)."""
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as e:
            raise RuntimeError("LangChain tools need langchain-core: "
                               'pip install "headless-driver[agents]"') from e
        return [StructuredTool.from_function(func=fn, name=spec.name,
                                             description=spec.description,
                                             args_schema=pydantic_schema(spec))
                for fn, spec in zip(self._functions(), self.specs)]

    def llamaindex_tools(self) -> list:
        """LlamaIndex ``FunctionTool`` objects (needs ``llama-index-core``)."""
        try:
            from llama_index.core.tools import FunctionTool
        except ImportError as e:
            raise RuntimeError("LlamaIndex tools need llama-index-core: "
                               "pip install llama-index-core") from e
        return [FunctionTool.from_defaults(fn=fn, name=spec.name, description=spec.description,
                                           fn_schema=pydantic_schema(spec))
                for fn, spec in zip(self._functions(), self.specs)]

    def crewai_tools(self) -> list:
        """CrewAI ``BaseTool`` instances (needs ``crewai``)."""
        try:
            from crewai.tools import BaseTool
        except ImportError as e:
            raise RuntimeError("CrewAI tools need crewai: pip install crewai") from e
        tools = []
        for fn, spec in zip(self._functions(), self.specs):
            def _run(self_, _fn=fn, **kwargs):
                return _fn(**kwargs)
            cls = type(
                "".join(part.title() for part in spec.name.split("_")) + "Tool",
                (BaseTool,),
                {"__annotations__": {"name": str, "description": str},
                 "name": spec.name, "description": spec.description,
                 "args_schema": pydantic_schema(spec), "_run": _run},
            )
            tools.append(cls())
        return tools


def _error(message: str) -> str:
    # `ok: false` marks a tool failure unambiguously: a tool's own data may
    # well contain a field called "error".
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def is_error(result: str) -> bool:
    """Whether a :meth:`Toolkit.call` result is a failure report."""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("ok") is False and "error" in data


def _get(obj, key):
    """Read `key` from a dict or an SDK object alike."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _check_url(url: str) -> None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")


_PY_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict}


def _typed_function(toolkit: Toolkit, spec: ToolSpec) -> Callable[..., str]:
    import inspect

    def run(**kwargs) -> str:
        return toolkit.call(spec.name, {k: v for k, v in kwargs.items() if v is not None})

    parameters = []
    for p in spec.params:
        annotation = _PY_TYPES[p.type]
        if p.type == "object":
            annotation = Dict[str, str]
        default = inspect.Parameter.empty if p.required else p.default
        if not p.required:
            annotation = Optional[annotation]
        parameters.append(inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY,
                                            default=default, annotation=annotation))
    run.__signature__ = inspect.Signature(parameters, return_annotation=str)
    run.__name__ = spec.name
    run.__qualname__ = spec.name
    run.__doc__ = spec.description
    run.__annotations__ = {p.name: q.annotation for p, q in zip(spec.params, parameters)}
    run.__annotations__["return"] = str
    return run


_schema_cache: Dict[str, Any] = {}


def pydantic_schema(spec: ToolSpec):
    """A pydantic model describing `spec`'s arguments (needs pydantic)."""
    if spec.name in _schema_cache:
        return _schema_cache[spec.name]
    from pydantic import Field, create_model
    fields: Dict[str, Any] = {}
    for p in spec.params:
        annotation: Any = _PY_TYPES[p.type]
        if p.type == "object":
            annotation = Dict[str, str]
        if p.required:
            fields[p.name] = (annotation, Field(..., description=p.description))
        else:
            fields[p.name] = (Optional[annotation], Field(p.default, description=p.description))
    model = create_model("".join(x.title() for x in spec.name.split("_")) + "Args", **fields)
    _schema_cache[spec.name] = model
    return model


# ------------------------------------------------ module-level convenience
_default: Optional[Toolkit] = None


def default_toolkit() -> Toolkit:
    global _default
    if _default is None:
        _default = Toolkit()
    return _default


def openai_tools() -> List[Dict[str, Any]]:
    return default_toolkit().openai_tools()


def anthropic_tools() -> List[Dict[str, Any]]:
    return default_toolkit().anthropic_tools()


def langchain_tools() -> list:
    return default_toolkit().langchain_tools()


def llamaindex_tools() -> list:
    return default_toolkit().llamaindex_tools()


def crewai_tools() -> list:
    return default_toolkit().crewai_tools()


def call_tool(name: str, arguments: Any = None) -> str:
    return default_toolkit().call(name, arguments)
