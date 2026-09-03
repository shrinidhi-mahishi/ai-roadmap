# Delegation

## Why It Matters
Delegation is how Deep Agents keeps one model from carrying every subproblem in one context window. The benefit is not only parallelism. The biggest win is context quarantine: a child agent can do heavy work, use tools freely, and return one compressed result to the coordinator.

In interviews, the right thesis is that delegation turns a single monolithic agent into a coordinator-plus-workers system.

## Mental Model
The docs split delegation into two layers:

- task planning
- subagents

The main coordinator primitive is the built-in `task` tool for synchronous subagents. Async subagents are the separate non-blocking variant when the supervisor should keep talking to the user while background work continues.

Think of delegation as three possible modes:

- synchronous `task(...)`: isolate and wait
- multiple synchronous `task(...)` calls: isolate and parallelize within a turn
- `AsyncSubAgent`: launch, return immediately, and manage later

## Architecture / Flow
```text
user request
  -> coordinator agent
  -> optional write_todos plan
  -> delegate work
     -> sync path: task(name=..., task=...)
     -> async path: start_async_task(...)
  -> child agent executes in isolated context
  -> visibility
     -> stream.subagents for sync delegated work
     -> separate async task status / traces for background work
  -> coordinator synthesizes final answer
```

For async work, the supervisor also gets lifecycle tools such as `check_async_task`, `update_async_task`, `cancel_async_task`, and `list_async_tasks`.

## Key Concepts
- Synchronous delegation is built into Deep Agents through the `task` tool. The child subagent is ephemeral and returns one final report to the parent.

- The value proposition of sync delegation is fresh context plus compression. Intermediate child messages and tool calls do not have to pollute the coordinator's window.

- Async delegation uses `AsyncSubAgent` specs and a different control model. The supervisor launches a background task, gets a task ID immediately, and can check or update it later.

- Async subagents are stateful. They keep their own thread and can be resumed, updated, or cancelled while work is in flight.

- Deployment topology matters for async work:
  - ASGI transport when `url` is omitted and the agents are co-deployed
  - HTTP transport when `url` is set and the child lives on a remote Agent Protocol server

- Tracing is part of the design. The async docs emphasize that supervisor traces show launch, check, update, cancel, and list tool calls, while each async child run appears as its own trace linked by thread ID.

- Task planning is related but separate. The presence of `write_todos` does not perform delegation by itself; it gives the coordinator a structured plan surface.

## Metrics and Formulas to Memorize
- Delegation stack:
  - planning -> `write_todos`
  - synchronous execution -> `task`
  - asynchronous execution -> `AsyncSubAgent`
- Async supervisor tool count: `5`
  - `start_async_task`
  - `check_async_task`
  - `update_async_task`
  - `cancel_async_task`
  - `list_async_tasks`
- Worker-pool sizing rule from the docs:
  - `1 supervisor + N active async subagents = N + 1 worker slots`
- Async subagents are marked as a preview feature starting in `deepagents 0.5.0`
- The docs do not provide universal latency or throughput benchmarks for delegation quality across models

## Trade-offs and Failure Modes
- Over-delegation creates orchestration overhead. Not every task should become a subagent.

- Synchronous delegation is clean for context, but it blocks the parent until the child finishes.

- Async delegation avoids blocking but adds task IDs, worker-pool sizing, transport choices, and cross-trace correlation work.

- Polling immediately after `start_async_task` defeats the point of async execution. The docs explicitly call this out as a troubleshooting pattern.

- Delegation does not automatically imply specialization. A poorly scoped child agent can still do noisy or redundant work; the main benefit then becomes isolation rather than better reasoning.

## Interview Q&A
**Q: What does delegation buy you in Deep Agents?**  
A: Context isolation, specialization, and parallelism. The biggest practical win is often isolating heavy tool use from the coordinator's context window.

**Q: What is the default delegation primitive?**  
A: The built-in synchronous `task` tool.

**Q: How is `AsyncSubAgent` different from a normal subagent?**  
A: It launches non-blocking background work, maintains state on its own thread, and can later be checked, updated, or cancelled.

**Q: When should I use ASGI versus HTTP transport for async subagents?**  
A: Use ASGI when the graphs are co-deployed and you want in-process calls. Use HTTP when subagents live on a remote Agent Protocol server or need independent scaling.

**Q: How do you observe delegated work?**  
A: For sync subagents, use streaming such as `stream.subagents`. For async work, use the async task tools and follow traces by thread ID.

**Q: What is the most common async anti-pattern?**  
A: Launching a background task and then immediately polling it in a loop, which turns async delegation back into blocking.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents.md)
- [Async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents.md)
- [Streaming](https://docs.langchain.com/oss/python/deepagents/streaming.md)
