# MCP server

`headless-driver mcp` serves web search and page reading over the
[Model Context Protocol](https://modelcontextprotocol.io), so Claude Desktop,
Claude Code, Cursor, VS Code and any other MCP client can use them.

```bash
pip install "headless-driver[mcp]"    # Python 3.10+
```

## Tools

| Tool | What it does |
| --- | --- |
| `search` | Search the web; title, URL and snippet per result |
| `search_aggregate` | Ask several independent engines at once; rank by agreement |
| `fetch_page` | A page's main content as clean Markdown |
| `extract` | Structured data from a rendered page, by CSS selectors (Playwright) |
| `screenshot` | A PNG screenshot of a page (Playwright) |

## Connect a client

Print the configuration to paste:

```bash
headless-driver mcp --print-config          # uses the installed command
headless-driver mcp --print-config --uvx    # runs through uvx, nothing to install
```

=== "Claude Desktop / Cursor"

    ```json
    {
      "mcpServers": {
        "web": {"command": "headless-driver", "args": ["mcp"]}
      }
    }
    ```

=== "Claude Code"

    ```bash
    claude mcp add web -- headless-driver mcp
    ```

=== "uvx (no install)"

    ```json
    {
      "mcpServers": {
        "web": {"command": "uvx",
                "args": ["--from", "headless-driver[mcp]", "headless-driver", "mcp"]}
      }
    }
    ```

## Remote / HTTP

```bash
headless-driver mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

## Options

`--region uk-en`, `--proxy http://user:pass@host:port` and
`--cache sqlite:///~/.cache/headless.db` apply to every tool call. Results are
cached in memory by default.

## From Python

```python
from headless.mcp_server import build_server
server = build_server(region="us-en")   # an MCPServer/FastMCP you can extend
server.run("stdio")
```

Works with MCP SDK 1.x (`FastMCP`) and 2.x (`MCPServer`). Over stdio, all
diagnostics go to stderr, so the protocol stream stays clean.
