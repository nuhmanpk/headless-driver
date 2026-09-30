"""A Model Context Protocol server: web search and page reading for any agent.

``headless-driver mcp`` exposes this package as MCP tools, so Claude Desktop,
Claude Code, Cursor, VS Code and any other MCP client can search the web and
read pages — with impersonation, block detection and consensus ranking intact:

==================  ===========================================================
``search``          Search the web; results with title, URL and snippet
``search_aggregate``  Ask several independent engines at once; rank by agreement
``fetch_page``      A page's main content as clean Markdown
``extract``         Structured data from a rendered page, by CSS selectors
``screenshot``      A PNG screenshot of a page
==================  ===========================================================

Configure a client (Claude Desktop, Cursor, …)::

    {"mcpServers": {"web": {"command": "headless-driver", "args": ["mcp"]}}}

or, without installing anything first::

    {"mcpServers": {"web": {"command": "uvx",
                            "args": ["--from", "headless-driver[mcp]", "headless-driver", "mcp"]}}}

Needs the ``mcp`` extra (Python 3.10+): ``pip install "headless-driver[mcp]"``.
Over stdio nothing but protocol messages may reach stdout, which this package
guarantees — every diagnostic goes to stderr.
"""

import json
from typing import Any, Dict, List, Optional

from .logs import get_logger

log = get_logger("mcp")

INSTRUCTIONS = (
    "Web search and page reading. Use `search` for quick lookups and "
    "`search_aggregate` when accuracy matters (finding a specific person, company "
    "or document): it asks several independent search engines and ranks results by "
    "agreement. Then use `fetch_page` to read a result as Markdown. A result with "
    "blocked=true means the engines refused the request — it is not evidence that "
    "nothing exists; wait and retry, or rephrase. Supports site:, quotes and other "
    "search operators."
)


def mcp_available():
    try:
        _server_class()
        return True, ""
    except ImportError as e:
        return False, str(e)


def _server_class():
    """The SDK's server class: ``MCPServer`` in mcp 2.x, ``FastMCP`` in 1.x."""
    try:
        from mcp.server.mcpserver import MCPServer, Image
        return MCPServer, Image
    except ImportError:
        pass
    from mcp.server.fastmcp import FastMCP, Image
    return FastMCP, Image


def _tool_error_class():
    for module in ("mcp.server.mcpserver.exceptions", "mcp.server.fastmcp.exceptions"):
        try:
            return __import__(module, fromlist=["ToolError"]).ToolError
        except (ImportError, AttributeError):
            continue
    return RuntimeError


def build_server(toolkit=None, name: str = "headless-driver", **toolkit_kwargs):
    """Build (but do not run) the MCP server around a :class:`~headless.tools.Toolkit`."""
    try:
        Server, Image = _server_class()
    except ImportError as e:
        raise RuntimeError('the MCP server needs the mcp extra (Python 3.10+): '
                           'pip install "headless-driver[mcp]"') from e
    from .tools import Toolkit
    from . import __version__

    toolkit = toolkit or Toolkit(**toolkit_kwargs)
    # The SDK configures the *root* logger when a server is constructed (a Rich
    # handler), which would hijack the logging of any program embedding this.
    # Put the root logger back exactly as it was.
    import logging
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        try:
            server = Server(name, instructions=INSTRUCTIONS, version=__version__)
        except TypeError:  # mcp 1.x FastMCP has no `version`
            server = Server(name, instructions=INSTRUCTIONS)
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    server.toolkit = toolkit

    from .tools import is_error
    ToolError = _tool_error_class()

    def _result(text: str) -> Any:
        if is_error(text):
            # The SDK passes a ToolError's message to the client; any other
            # exception becomes an opaque "Error executing tool".
            raise ToolError(json.loads(text)["error"])
        return json.loads(text)

    @server.tool(description="Search the web (Brave, DuckDuckGo, Yahoo, Mojeek, Google, "
                             "Bing) and return the top results with title, URL and "
                             "snippet. Supports site:, quotes and other operators.")
    def search(query: str, max_results: int = 8, region: Optional[str] = None,
               pages: int = 1) -> Dict[str, Any]:
        return _result(toolkit.call("web_search", _args(query=query, max_results=max_results,
                                                        region=region, pages=pages)))

    @server.tool(description="Search several independent engines at once and rank results "
                             "by how many agree (votes). Slower than `search` but far more "
                             "reliable for finding a specific person, company or page.")
    def search_aggregate(query: str, max_results: int = 8,
                         region: Optional[str] = None) -> Dict[str, Any]:
        return _result(toolkit.call("web_search", _args(query=query, max_results=max_results,
                                                        region=region, mode="aggregate")))

    @server.tool(description="Fetch a web page and return its main content as clean "
                             "Markdown (navigation, ads and boilerplate removed; links "
                             "absolute). Renders JavaScript pages when needed.")
    def fetch_page(url: str, max_tokens: int = 4000, render: str = "auto") -> Dict[str, Any]:
        return _result(toolkit.call("fetch_page", _args(url=url, max_tokens=max_tokens,
                                                        render=render)))

    @server.tool(description="Render a page in a browser and extract structured data. "
                             "`fields` maps names to CSS selectors; end a selector with "
                             "@attr to read an attribute. With `item_selector`, returns "
                             "one record per matching element.")
    def extract(url: str, fields: Dict[str, str], item_selector: Optional[str] = None,
                scroll: int = 0) -> Any:
        return _result(toolkit.call("extract_data", _args(url=url, fields=fields,
                                                          item_selector=item_selector,
                                                          scroll=scroll)))

    @server.tool(description="Take a PNG screenshot of a web page in a real browser.")
    def screenshot(url: str, full_page: bool = True):
        data = _result(toolkit.call("screenshot", _args(url=url, full_page=full_page)))
        with open(data["path"], "rb") as f:
            return Image(data=f.read(), format="png")

    return server


def _args(**kwargs) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000,
          **toolkit_kwargs) -> None:
    """Run the server until the client disconnects (stdio) or it is stopped."""
    server = build_server(**toolkit_kwargs)
    log.info("MCP server starting (%s)", transport)
    try:
        if transport == "stdio":
            server.run("stdio")
        else:
            try:
                server.run(transport, host=host, port=port)
            except TypeError:  # mcp 1.x: host/port live in settings
                server.settings.host, server.settings.port = host, port
                server.run(transport)
    finally:
        server.toolkit.close()


def claude_desktop_config(name: str = "web", uvx: bool = False) -> Dict[str, Any]:
    """The ``mcpServers`` entry to paste into an MCP client's configuration."""
    if uvx:
        entry = {"command": "uvx",
                 "args": ["--from", "headless-driver[mcp]", "headless-driver", "mcp"]}
    else:
        entry = {"command": "headless-driver", "args": ["mcp"]}
    return {"mcpServers": {name: entry}}


def tool_names() -> List[str]:
    return ["search", "search_aggregate", "fetch_page", "extract", "screenshot"]
