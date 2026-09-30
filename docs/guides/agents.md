# AI agents & tools

`Toolkit` gives an agent four tools — **`web_search`**, **`fetch_page`**,
**`extract_data`** and **`screenshot`** — defined once and exported in every
common format. Tools return compact JSON, report blocks honestly ("the engines
refused; this is not evidence nothing exists"), and cache results by default.

```python
from headless import Toolkit

toolkit = Toolkit(region="us-en")        # any AdvancedSearchScraper option works too
```

=== "Anthropic"

    ```python
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": "What changed in Python 3.13?"}]

    while True:
        response = client.messages.create(model="claude-sonnet-5", max_tokens=2048,
                                          tools=toolkit.anthropic_tools(), messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "user",
                         "content": toolkit.handle_anthropic_tool_use(response.content)})
    ```

=== "OpenAI"

    ```python
    from openai import OpenAI
    client = OpenAI()
    messages = [{"role": "user", "content": "What changed in Python 3.13?"}]

    while True:
        reply = client.chat.completions.create(model="gpt-5", tools=toolkit.openai_tools(),
                                               messages=messages).choices[0].message
        messages.append(reply)
        if not reply.tool_calls:
            break
        messages.extend(toolkit.handle_openai_tool_calls(reply.tool_calls))
    ```

=== "LangChain"

    ```python
    tools = toolkit.langchain_tools()      # StructuredTool objects
    agent = create_react_agent(llm, tools)
    ```

=== "LlamaIndex"

    ```python
    tools = toolkit.llamaindex_tools()     # FunctionTool objects
    agent = ReActAgent.from_tools(tools, llm=llm)
    ```

=== "CrewAI"

    ```python
    researcher = Agent(role="Researcher", goal="...", tools=toolkit.crewai_tools())
    ```

## Choosing tools

```python
Toolkit(tools=["web_search", "fetch_page"])     # browserless only
```

`extract_data` and `screenshot` need the `playwright` extra.

## Calling tools yourself

```python
toolkit.call("web_search", {"query": "rust 2024 edition", "mode": "aggregate"})  # -> JSON text
```

Errors come back as `{"error": "..."}` so the model can adapt, rather than
crashing the loop.

For Claude Desktop, Cursor and other MCP clients, see the [MCP server](mcp.md).
