# Deep Agents Overview

## Why It Matters
Deep Agents is LangChain's opinionated answer to a common production problem: the model loop is easy, but filesystem access, delegation, context control, approvals, and persistence are where real agents get complicated. In interviews, this lets you explain not just "how to call tools," but how to package a tool-calling agent into something that can survive long, multi-step work.

The strongest framing is that Deep Agents is not the framework and not the runtime. It is the harness that assembles those pieces into a usable default.

## Mental Model
Think in three layers:

- LangChain is the framework: models, tools, middleware, and agent primitives.
- LangGraph is the runtime: durable execution, streaming, checkpoints, interrupts, and threads.
- Deep Agents is the harness: an opinionated bundle on top of both that wires in filesystem tools, subagents, context compression, memory, skills, and approvals.

The main constructor is `create_deep_agent(...)`. It is the "one surface" that lets you bind:

- `model=`
- `system_prompt=`
- `tools=`
- `memory=`
- `skills=`
- `backend=`
- `subagents=`

Everything else is layered around that core loop.

## Architecture / Flow
```text
user request
  -> create_deep_agent(
       model=..., system_prompt=..., tools=...,
       memory=..., skills=..., backend=..., subagents=...
     )
  -> Deep Agents harness
     -> execution environment
     -> context management
     -> delegation
     -> steering / approvals
  -> LangChain tool-calling loop
  -> LangGraph runtime services
     -> checkpoints
     -> streaming
     -> interrupts
     -> durable state
  -> final answer
```

A practical way to describe the default stack is:

1. File access is always scaffolded through filesystem middleware and a backend.
2. A general-purpose synchronous subagent is auto-added unless you disable or replace it.
3. Context compression is built in, so long runs offload and summarize automatically.
4. Prompt caching is auto-wired for supported Anthropic and Bedrock models.
5. Memory, skills, task planning, async subagents, and HITL are opt-in or conditional layers.

## Key Concepts
- Deep Agents is an "agent harness," not a raw agent primitive. It packages defaults around the same core tool loop you could otherwise build directly with `create_agent` or LangGraph.

- The harness groups capabilities into four product-facing buckets:
  - execution environment
  - context management
  - delegation
  - steering

- `create_deep_agent` is intentionally broad. It is not only "pick a model and tools." It is also where you decide whether the agent has memory, skills, a routed filesystem, subagents, structured output, runtime context, and approval gates.

- The default backend is `StateBackend()`, which means the filesystem is thread-scoped unless you swap in something more durable.

- Deep Agents is provider-flexible. The docs position it against Claude Agent SDK by emphasizing pluggable models, pluggable execution backends, and managed or self-hosted deployment paths.

- The right time to choose Deep Agents is when you want the opinionated bundle: file operations, long-task context management, delegated workers, and approval flows. If you only need a simple tool-using chatbot, plain LangChain `create_agent` is usually simpler. If you need a custom orchestration graph, build directly in LangGraph.

## Metrics and Formulas to Memorize
- `Execution environment = tools + virtual filesystem + permissions + code execution`
- `Context management = skills + memory + summarization/context offloading + prompt caching`
- `Delegation = task planning + subagents`
- `Steering = interrupts + human approval`
- `StateBackend()` is the default backend if you do not pass `backend=`.
- Task planning is opt-in starting in `deepagents v0.7`.
- The official docs are conceptual here. They do not publish universal latency, throughput, or cost benchmarks for the harness itself.

## Trade-offs and Failure Modes
- Using Deep Agents for a trivial single-step assistant adds scaffolding you may not need.

- Leaving built-in capabilities exposed by default can enlarge prompt size and expand the agent's action surface beyond what the task requires.

- Confusing the layers leads to bad explanations: durability, checkpoints, and interrupts are runtime properties from LangGraph, while Deep Agents is the harness that wires them in.

- The auto-added `general-purpose` subagent is easy to forget. If you do not understand that default, the `task` tool can feel "mysteriously present."

- The harness removes boilerplate, not architecture. You still need to choose the right backend, tool set, approval policy, and data-isolation strategy.

## Interview Q&A
**Q: What is Deep Agents in one sentence?**  
A: It is LangChain's opinionated agent harness on top of LangChain and LangGraph that bundles filesystem access, context management, delegation, streaming, and human approval into one constructor.

**Q: How is Deep Agents different from LangChain?**  
A: LangChain is the framework with core building blocks. Deep Agents is a higher-level harness that prepackages those blocks into a stronger default agent.

**Q: How is Deep Agents different from LangGraph?**  
A: LangGraph is the runtime for durable execution, streaming, threads, and interrupts. Deep Agents uses that runtime but adds agent-specific conventions and middleware.

**Q: When should I choose Deep Agents over `create_agent`?**  
A: Choose it when you need filesystem access, long-running context compression, delegated subagents, or approval flows. Use `create_agent` when a minimal tool-calling loop is enough.

**Q: Is Deep Agents tied to one model provider?**  
A: No. The docs explicitly position it as provider-flexible, unlike harnesses tightly coupled to a single model ecosystem.

**Q: What are the main capability buckets to memorize?**  
A: Execution environment, context management, delegation, and steering.

## Sources
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview.md)
- [Customize Deep Agents](https://docs.langchain.com/oss/python/deepagents/customization.md)
- [Comparison with Claude Agent SDK](https://docs.langchain.com/oss/python/deepagents/comparison.md)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends.md)
