# Tools and MCP

## Why It Matters
Deep Agents becomes useful the moment the model can do something outside pure text generation. That "something" is the tool surface. In practice, the hardest part is not defining one function; it is combining local tools, built-in harness tools, and MCP servers without losing control over auth, session state, or error handling.

For interviews, the key insight is that Deep Agents uses one `tools=` surface for both ordinary tools and MCP-loaded tools, but the operational concerns are different.

## Mental Model
There are three tool sources:

- custom callables and LangChain tools you define in code
- built-in harness tools such as `read_file`, `glob`, `task`, and sometimes `execute`
- MCP-loaded tools fetched from external servers

To the agent, these all become tools in the same selection loop. To you, they are not equivalent:

- local tools run in your process
- built-in tools are injected by the harness
- MCP tools come from separate processes or remote servers and need transport, auth, and session strategy

## Architecture / Flow
```text
tool source
  -> plain callable / LangChain tool / MCP server
  -> optional MCP adapter layer
     -> MultiServerMCPClient(...)
     -> client.get_tools() or load_mcp_tools(session)
  -> create_deep_agent(..., tools=[...])
  -> Deep Agents mixes these with built-in tools
  -> model selects tool
  -> result returns as normal tool message or raises, depending on config
```

For MCP, the control path is usually:

1. configure transports and auth
2. load tools with `MultiServerMCPClient`
3. optionally manage a persistent session with `client.session(...)`
4. optionally inject runtime context or retries with `tool_interceptors=[...]`
5. pass the resulting tools into `create_deep_agent`

## Key Concepts
- Deep Agents accepts plain Python callables, LangChain `@tool`-decorated functions, `BaseTool` instances, and tool dicts in `tools=`.

- The harness also injects built-ins. A typical Deep Agent gets:
  - `ls`
  - `read_file`
  - `write_file`
  - `edit_file`
  - `delete`
  - `glob`
  - `grep`
  - `task`
  - `execute` when the backend supports shell execution

- MCP support uses `langchain-mcp-adapters`. The standard entry point is `MultiServerMCPClient(...)`, whose connection map can define servers with transports such as `http` or `stdio`.

- By default, `MultiServerMCPClient` is stateless. Each MCP tool invocation creates a fresh `ClientSession`, runs the tool, then cleans up.

- If the server is stateful, use a persistent session:
  - `async with client.session("server_name") as session:`
  - `tools = await load_mcp_tools(session)`

- MCP error handling is intentionally agent-friendly by default. A tool execution failure becomes a tool message with `status="error"` so the model can inspect it and recover. Set `handle_tool_errors=False` to raise instead.

- `tool_interceptors=[...]` is the main bridge between MCP tools and LangGraph runtime state. Interceptors can:
  - inject user IDs or API keys from runtime context
  - read from the store
  - inspect or update runtime state
  - add headers dynamically
  - retry or short-circuit calls

- MCP tool results can include structured content and multimodal content. The adapter normalizes these into LangChain artifacts and content blocks.

## Metrics and Formulas to Memorize
- `2` transport styles appear most often in the docs: `http` and `stdio`
- Default session model: `1` fresh MCP session per tool call
- `handle_tool_errors=False` flips tool execution failures from model-visible error messages to Python exceptions
- Tool-call recovery formula:
  - default: `CallToolResult(isError=True) -> ToolMessage(status="error")`
  - strict mode: `CallToolResult(isError=True) -> exception`
- The docs are conceptual here and do not provide benchmark latencies for MCP transports or session strategies

## Trade-offs and Failure Modes
- Stateless MCP sessions are easy, but they break stateful workflows. If the server expects continuity, `client.get_tools()` alone may not be enough.

- Large tool catalogs bloat the prompt and make tool selection worse. "More tools" is not a free upgrade.

- MCP servers cannot see LangGraph runtime context on their own. If auth or tenancy depends on runtime values, you need interceptors or explicit tool arguments.

- Returning structured content only as an artifact is great for app code, but the model will not see that structure unless you surface it intentionally, for example through an interceptor.

- Mixing external side effects with loose auth headers is dangerous. Auth should come from deterministic runtime context or connection config, not from prompt text.

## Interview Q&A
**Q: Do MCP tools use a different Deep Agents API than normal tools?**  
A: No. They end up on the same `tools=` surface. The difference is in how you load and manage them.

**Q: What is `MultiServerMCPClient` for?**  
A: It connects to one or more MCP servers, loads their tool definitions, and returns LangChain-compatible tools.

**Q: When should I use `client.session(...)` plus `load_mcp_tools(session)`?**  
A: When the MCP server is stateful and you need a persistent session instead of a fresh session per call.

**Q: How are MCP tool failures handled by default?**  
A: Tool execution errors are returned to the model as failed tool messages. If you want exceptions instead, set `handle_tool_errors=False`.

**Q: Why are interceptors important?**  
A: They bridge runtime context into MCP calls and give you middleware-like control for retries, auth injection, and request rewriting.

**Q: What is the cleanest way to explain MCP in an interview?**  
A: It is a standard adapter layer that lets agents load tools from external services without writing one custom integration per service.

## Sources
- [Tools](https://docs.langchain.com/oss/python/deepagents/tools.md)
- [Model Context Protocol](https://docs.langchain.com/oss/python/deepagents/mcp.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
