# Streaming

## Why It Matters
Agent UX breaks down fast if all you can show is a final answer. Deep Agents is opinionated about streaming because real tasks involve tool calls, delegated workers, and long waits. In interview terms, the important upgrade is not "token streaming"; it is observability into subagents and state.

The docs position streaming as part of the runtime contract, with Deep Agents adding a first-class subagent view on top.

## Mental Model
There are two streaming surfaces worth knowing:

- raw `agent.stream(...)`, where you consume chunked events by `stream_mode`
- typed `agent.stream_events(...)`, where you get projection-specific iterators such as `stream.messages` and `stream.subagents`

The newer typed API is the recommended default. The older raw stream is still useful when you need exact chunk order or low-level control.

## Architecture / Flow
```text
agent run
  -> coordinator emits updates
  -> delegated subagents emit their own updates
  -> namespace tags identify the source

two consumption styles:
  1. raw stream
     -> agent.stream(..., stream_mode=..., subgraphs=True, version="v2")
  2. typed event stream
     -> agent.stream_events(..., version="v3")
     -> stream.messages
     -> stream.tool_calls
     -> stream.values
     -> stream.subagents
     -> stream.output
```

Custom progress updates can also come from inside tools through `get_stream_writer()`.

## Key Concepts
- For new applications, the docs recommend event streaming: `agent.stream_events(..., version="v3")`.

- Typed projections let you consume different surfaces independently:
  - `stream.messages`
  - `stream.tool_calls`
  - `stream.values`
  - `stream.subagents`
  - `stream.output`

- `stream.subagents` is the Deep Agents-specific feature. Each delegated task gets its own handle with `.name`, `.path`, `.status`, `.messages`, `.tool_calls`, `.values`, `.subagents`, and `.output`.

- The lower-level streaming API is still important:
  - `agent.stream(..., stream_mode="updates", subgraphs=True, version="v2")`
  - `agent.stream(..., stream_mode="messages", subgraphs=True, version="v2")`
  - `agent.stream(..., stream_mode="custom", subgraphs=True, version="v2")`

- `subgraphs=True` is how LangGraph surfaces subagent events into the raw stream. Without it, coordinator-only streaming can make delegated work invisible.

- Namespaces matter. In the raw stream, `chunk["ns"]` or event metadata tells you whether an event came from the main agent or a subagent.

- `stream.subgraphs` and `stream.subagents` are not the same abstraction. `stream.subgraphs` exposes graph structure; `stream.subagents` exposes product-level delegated tasks and is the better UI surface for humans.

- Use `get_stream_writer()` inside a tool or subagent node when you want custom progress events that are not just tokens or tool messages.

- Typed streams support controlled interleaving. The docs show `stream.interleave("messages", "subagents")` when you want a single read loop without flattening everything into raw chunk parsing.

## Metrics and Formulas to Memorize
- Event streaming is the recommended API starting in Deep Agents `v0.6`
- Typed event examples use `version="v3"`
- Raw streaming examples use `version="v2"` plus `subgraphs=True`
- Useful mapping:
  - `updates` -> state snapshots and progress
  - `messages` -> model token/message flow
  - `custom` -> app-defined events from `get_stream_writer()`
- The docs do not publish benchmark TTFT or per-event overhead numbers for Deep Agents streaming

## Trade-offs and Failure Modes
- Parsing only raw chunks makes application code brittle because you depend on low-level event shape instead of stable projections.

- Ignoring namespaces mixes coordinator and subagent output into an unreadable stream.

- Using `stream.subgraphs` directly for product UI can expose internal graph mechanics that users do not care about.

- Streaming everything is not free. Long-running agents may also stream summarization tokens; if you do not filter those, the UI can show compression work as if it were user-facing output.

- If your app delegates deeply, you may need to think about recursion limits in the client-side stream consumer, not just on the server.

## Interview Q&A
**Q: What does Deep Agents add to LangGraph streaming?**  
A: A first-class subagent projection, exposed as `stream.subagents`, so delegated work is observable as named child streams instead of only raw graph events.

**Q: What is the difference between `agent.stream(...)` and `agent.stream_events(...)`?**  
A: `agent.stream(...)` is the lower-level chunk stream organized by `stream_mode`; `agent.stream_events(...)` is the higher-level typed projection API with separate iterators for messages, tool calls, values, and subagents.

**Q: Why do I need `subgraphs=True`?**  
A: Because raw streaming needs LangGraph subgraph events enabled in order to surface subagent activity.

**Q: How do I tell whether a token came from a subagent?**  
A: Use the namespace metadata. In the raw stream, the namespace identifies the producing agent.

**Q: When should I use `get_stream_writer()`?**  
A: When a tool or subagent node should emit structured progress events such as percentage complete, stage changes, or custom status markers.

**Q: Which stream surface is better for a human-facing UI?**  
A: `stream.subagents`, because it reflects the product concept of delegated tasks rather than low-level graph internals.

## Sources
- [Streaming](https://docs.langchain.com/oss/python/deepagents/streaming.md)
- [Event streaming](https://docs.langchain.com/oss/python/deepagents/event-streaming.md)
- [Subagent streaming](https://docs.langchain.com/oss/python/deepagents/frontend/subagent-streaming.md)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
