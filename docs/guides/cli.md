# Command line

```bash
headless-driver search "python asyncio" -n 5 [--mode aggregate] [--pages 2] [--cache memory] [--json]
headless-driver fetch https://example.com [--max-tokens 2000] [--chunk 500] [--json]
headless-driver mcp [--transport stdio|sse|streamable-http] [--print-config]
headless-driver extract URL --item CSS -f name=css -f link=a@href [--json]
headless-driver engines
headless-driver doctor [--engines]
headless-driver bench [--min-ok-rate 0.5]
headless-driver shot|pdf URL -o FILE [--browser playwright --full-page]
```

`python -m headless` is the same command. Output is coloured on a terminal and
plain when piped; `--json` output and `fetch`'s Markdown go to stdout, all
diagnostics to stderr.

Every command and flag: [reference manual](../manual.md#command-line).
