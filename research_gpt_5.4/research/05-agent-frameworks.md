# Research: Agent Frameworks - LangGraph, OpenAI Agents SDK, Google ADK, CrewAI

**Date researched**: 2026-08-21
**Sources consulted**: 31

---

## 1. System Topology & Mechanics

`LangGraph` is a **stateful graph runtime** built on a Pregel-style message-passing model: nodes exchange state updates across edges, nodes that receive messages in the same super-step can execute in parallel, and execution ends when all nodes are inactive and no messages remain in transit ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). Checkpoints are taken at **super-step boundaries**, not mid-node, so if a run resumes after an interrupt or retry, the affected node re-executes from the start of its function ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

`OpenAI Agents SDK` is a **turn-driven runner** rather than a graph runtime. The documented loop is: call model, inspect output, then either stop on final output, execute tool calls and continue, switch agents via handoff and continue, or raise `MaxTurnsExceeded` if `max_turns` is hit ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). The SDK supports normal async runs, sync runs, streamed runs, and a websocket transport; on websocket, the service processes **one response at a time per connection** ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`Google ADK` supports several orchestration families. The current docs describe **graph-based**, **dynamic**, **collaborative**, and older **template** workflows; for Python and Go, template workflows are explicitly described as superseded by graph-based and dynamic workflows in ADK 2.0 ([ADK multi-agent workflows](https://adk.dev/agents/multi-agents/), [ADK workflow agents](https://adk.dev/agents/workflow-agents/)). The template `SequentialAgent`, `ParallelAgent`, and `LoopAgent` execute by predefined logic and do **not** ask an LLM to decide orchestration order, so they are deterministic by construction ([ADK workflow agents](https://adk.dev/agents/workflow-agents/)).

`CrewAI` is **workflow-first and event-driven**. Its production guidance says to start with a `Flow`, use typed state as the backbone, and treat `Crew`s as focused units of work invoked from the flow ([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)). Flow control is expressed through decorators such as `@start`, `@listen`, and `@router`; multiple satisfied `@start()` methods can run "often in parallel" when the flow begins or resumes ([CrewAI Flows](https://docs.crewai.com/en/concepts/flows)).

For multi-agent communication, the frameworks differ materially:

- `LangGraph` uses graph edges and shared state updates as the native coordination primitive, so agent-to-agent interaction is usually encoded as graph transitions plus reducers rather than explicit delegation verbs ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)) [inferred].
- `OpenAI Agents SDK` distinguishes **handoffs** from **agents as tools**. Handoffs transfer control to another agent inside the runner loop, while `Agent.as_tool()` keeps a supervising agent in the loop and surfaces nested approvals on the outer run ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- `Google ADK` supports **collaborative workflows** plus explicit multi-agent composition, and its graph/dynamic workflow families can mix AI-powered agents with deterministic nodes inside one application graph ([ADK multi-agent workflows](https://adk.dev/agents/multi-agents/)).
- `CrewAI` exposes two external coordination protocols in public docs: **A2A** as a first-class delegation primitive for remote agent collaboration, and **MCP** for external tool/server integration ([CrewAI A2A protocol](https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation), [CrewAI MCP overview](https://docs.crewai.com/v1.15.10/en/mcp/overview)).

Architecturally, the cleanest high-level split is:

- `LangGraph`: state machine first.
- `OpenAI Agents SDK`: run loop first.
- `Google ADK`: workflow/runtime platform first.
- `CrewAI`: evented application workflow first.

That difference matters because it determines where control-plane concerns naturally live: checkpoints and reducers in LangGraph, run/session/approval objects in OpenAI Agents SDK, session-memory-services plus workflow types in ADK, and Flow state plus persistence in CrewAI ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [ADK sessions overview](https://adk.dev/sessions/), [CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for framework-level **p50/p95/p99 end-to-end latency SLAs**. None of the four official doc sets publishes stable, production-grade percentile distributions for comparable multi-step workloads.

The most important cost driver is not the framework license but **how much prompt state the framework replays and how many model turns it induces**. Graph fan-out, planner/executor separation, session compaction, and agent-role scaffolding all change total token volume even when the underlying model stays constant ([LangGraph use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/), [ADK context compaction](https://adk.dev/context/compaction/), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)) [inferred].

`OpenAI Agents SDK` exposes the most explicit first-party run accounting of the four. `Usage` tracks `requests`, `input_tokens`, `output_tokens`, `total_tokens`, and `request_usage_entries`, with per-request detail fields including `cached_tokens`, `cache_write_tokens`, and `reasoning_tokens` ([OpenAI usage](https://openai.github.io/openai-agents-python/usage/), [OpenAI usage reference](https://openai.github.io/openai-agents-python/ref/usage/)). That makes framework-side cost computation straightforward:

```text
run_cost ~= Σ(request_usage_entry_tokens x provider price schedule)
```

([OpenAI usage](https://openai.github.io/openai-agents-python/usage/)) [inferred]

OpenAI's state-carry options also affect cost. A local `Session` prepends stored history before each run, so replay cost grows unless the app trims or compacts history; alternatively, `conversation_id` or `previous_response_id` lets the Responses API manage continuation server-side so the client does not resend all prior turns ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`LangGraph` gives explicit cost/latency levers at the node level. Nodes can opt into caching with `CachePolicy(ttl=...)`, and a repeated invocation can return immediately with `__metadata__.cached = True`; supported cache backends include in-memory and SQLite examples in the docs ([LangGraph use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api), [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). Separately, durability mode is an NFR knob:

- `exit`: highest performance, weakest crash recovery.
- `async`: overlaps checkpoint writes with the next step.
- `sync`: strongest durability, explicit performance overhead.

([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers))

`Google ADK` is the strongest official source on **context compaction** as a latency/cost control. Its compaction system summarizes older session history once either a **token threshold** or a **sliding window / turn threshold** is reached, and `event_retention_size` preserves the most recent raw events for local coherence ([ADK context compaction](https://adk.dev/context/compaction/)). ADK also stores large binary payloads as **artifacts** outside session state and only injects them when the model calls `load_artifacts`, which reduces repeated prompt bloat on later turns ([ADK artifacts](https://adk.dev/artifacts/)). ADK usage metadata includes `promptTokenCount`, `candidatesTokenCount`, `totalTokenCount`, `thoughtsTokenCount`, `toolUsePromptTokenCount`, and `cachedContentTokenCount` ([ADK usage metadata](https://adk.dev/api-reference/kotlin/google-adk-kotlin-core/com.google.adk.kt.types/-usage-metadata/index.html)).

`CrewAI` exposes an aggregated `flow.usage_metrics` object after a run. The public docs say this rolls up **every LLM call** made by the flow, including crew calls, tool-internal calls, and direct `LLM.call(...)` calls from flow methods; the surfaced fields include `total_tokens`, `prompt_tokens`, `completion_tokens`, `cached_prompt_tokens`, `cache_creation_tokens`, and `reasoning_tokens` ([CrewAI Flows](https://docs.crewai.com/en/concepts/flows)). That is good for whole-flow cost visibility, but the docs do not publish first-party percentile latency distributions or throughput ceilings.

For throughput, the official documentation is again more architectural than benchmark-driven:

- `OpenAI Agents SDK`: websocket transport reuses a connection, but each connection processes **one response at a time** and is limited to **60 minutes** ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).
- `LangGraph`: parallel nodes inside a super-step can reduce critical-path latency when the graph structure allows it ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).
- `Google ADK`: `ParallelAgent` and graph-based workflows enable explicit parallelism, but official docs do not publish a framework-level concurrency or RPM ceiling ([ADK workflow agents](https://adk.dev/agents/workflow-agents/), [ADK multi-agent workflows](https://adk.dev/agents/multi-agents/)).
- `CrewAI`: multiple `@start()` methods may execute in parallel, and `kickoff_async` exists for long-running flows, but official docs likewise stop short of quantified throughput limits ([CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)).

Public benchmark coverage is uneven. A source-adjacent GitHub benchmark measured **LangGraph** at `p50/p95/p99 = 18.7/64.5/102.3 ms` and **CrewAI** at `31.4/110.2/178.6 ms`, with LangGraph using `2814` input / `1905` output tokens versus CrewAI `4440` / `2808` on that harness; however, it did **not** include OpenAI Agents SDK or Google ADK, so it is informative but not decision-complete ([agent-orchestration-benchmark](https://github.com/KIM3310/agent-orchestration-benchmark)). For these four frameworks specifically, there is still no widely accepted, official apples-to-apples benchmark suite.

On model routing, only `Google ADK` currently documents an **experimental Agent Routing** feature for fallback, A/B testing, and auto-routing between agents ([ADK multi-agent workflows](https://adk.dev/agents/multi-agents/)). `LangGraph`, `OpenAI Agents SDK`, and `CrewAI` all support the building blocks for routing, but their public docs do not describe a first-party complexity-based model router [inferred].

## 3. Distributed Resilience & State

`LangGraph` persists execution with **checkpointers**. A checkpoint stores graph state per super-step, keyed by `thread_id`, and LangGraph also persists **pending writes** from sibling nodes that completed successfully when another sibling fails, so a resumed super-step does not have to re-run every successful sibling ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Production persistence is expected to use durable savers such as PostgreSQL or SQLite rather than in-memory savers ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

The main replay caveat in LangGraph is explicit: because checkpoints are taken at step boundaries, **node code before the pause can run again** after resume ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). That makes idempotent tool wrappers and side-effect isolation mandatory in production [inferred]. There is also source-level evidence that `durability="async"` needs operational care: a 2026 pull request fixed **unbounded checkpoint task buildup** when graph execution outran persistence throughput ([LangGraph PR #7112](https://github.com/langchain-ai/langgraph/pull/7112)).

`OpenAI Agents SDK` treats durable state as a combination of **session persistence** and **serializable run state**. Sessions automatically retrieve history before each run and persist new items after each run ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/)). When a run pauses for approval, `RunState` can be serialized to JSON/string and resumed later, carrying approvals, usage, nested-agent resumption state, and server-managed conversation settings ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)). For longer-lived and failure-tolerant execution, the core docs point users to external durable orchestrators such as **Dapr, Temporal, Restate, and DBOS** rather than claiming the basic runner itself is a full workflow engine ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`Google ADK` has the clearest documented **concurrency control** of the four. `DatabaseSessionService` uses:

- **in-process locking** to serialize `append_event` calls for the same session inside one process.
- **row-level locking** with `SELECT ... FOR UPDATE` for PostgreSQL, MySQL, and MariaDB across multiple processes/replicas.

([ADK session service](https://adk.dev/sessions/session/))

ADK also makes its state domains explicit: `Session` is one conversation thread, `State` is session-scoped scratch data, and `Memory` is searchable cross-session knowledge managed by a separate `MemoryService` ([ADK sessions overview](https://adk.dev/sessions/), [ADK memory](https://adk.dev/sessions/memory/)). That separation is cleaner than treating all continuity as one rolling transcript [inferred].

`CrewAI` persists at the workflow layer. The `@persist` decorator can persist all flow methods or selected methods, defaults to a **SQLite** backend, resumes a prior lineage when `kickoff(inputs={"id": ...})` is used, and **forks** into a new lineage when `restore_from_state_id` is used ([CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)). `@human_feedback` pauses the flow for human input and can route based on structured outcomes such as `approved`, `rejected`, or `needs_revision` ([CrewAI human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows)).

What is **not** equally documented across frameworks is distributed locking and replay semantics:

- Strongly documented: `Google ADK` row locks; `LangGraph` super-step checkpoints and pending writes.
- Moderately documented: `OpenAI Agents SDK` serializable run state, best-effort local rollback of recently persisted input items on retry, and server-managed conversation retries for `conversation_locked` ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).
- Weakly documented: `CrewAI` public docs explain persistence and resume/fork behavior, but do not publish low-level distributed lock or exactly-once side-effect guarantees.

## 4. Enterprise Security & Governance

`OpenAI Agents SDK` exposes the richest documented **approval surface**. `needs_approval` applies to function tools, `Agent.as_tool()`, `ShellTool`, and `ApplyPatchTool`; local MCP servers support `require_approval`; hosted MCP tools support `tool_config={"require_approval": "always"}` plus optional programmatic approval callbacks ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/), [OpenAI MCP server reference](https://openai.github.io/openai-agents-python/ref/mcp/server/)). Approval decisions can persist for the rest of a run via sticky `always_approve` / `always_reject` decisions ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

OpenAI also documents an important governance caveat on observability: tracing is **enabled by default**, traces can include model and tool inputs/outputs, and organizations using **Zero Data Retention** cannot use tracing ([OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/)). That is useful because it makes the privacy trade-off explicit rather than hidden.

`CrewAI` has a broader but less centralized governance model. Official production guidance recommends:

- **task guardrails** to validate outputs,
- **structured outputs** (`output_pydantic` / `output_json`) for deterministic handoffs,
- **LLM hooks** to inspect or sanitize messages,
- **human feedback** checkpoints for approval or revision loops.

([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [CrewAI human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows))

CrewAI also has public protocol-level security knobs:

- `A2A` client/server config supports **Bearer**, **OAuth2**, **API key**, and **HTTP auth** schemes, plus transport selection such as `JSONRPC`, `gRPC`, or `HTTP+JSON` ([CrewAI A2A protocol](https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation)).
- `MCP` integration supports direct `mcps` DSL configuration or `MCPServerAdapter`, with at least **stdio** and **SSE** transports documented ([CrewAI MCP overview](https://docs.crewai.com/v1.15.10/en/mcp/overview)).

`Google ADK` documents security more at the **platform deployment** layer than at the individual tool-policy layer. Its homepage emphasizes built-in authentication, Cloud Trace observability, and enterprise-grade security when deployed to Google Cloud infrastructure ([ADK homepage](https://adk.dev/)). That is valuable for teams already standardizing on Cloud Run/GKE/Agent Runtime, but the public docs consulted here do **not** provide equally explicit first-party material on tool-level RBAC matrices, PII redaction pipelines, or sandbox-isolation trade-offs.

`LangGraph` core OSS docs are the sparsest on governance features. They document state, interrupts, caching, and persistence very well, but there is no first-party OSS equivalent of OpenAI's approval system or CrewAI's built-in human-feedback decorator in the sources consulted. In LangGraph, enterprise governance is usually assembled in the surrounding application and observability stack rather than in a built-in policy plane ([LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) [inferred].

> ⚠️ Limited public data available for built-in **PII redaction**, **tool-level RBAC hierarchies**, **sandbox isolation guarantees** (container vs process vs WASM), and **immutable audit-log schemas** across these four frameworks. The public material is much stronger on approvals, state, tracing, and protocol integration than on deep compliance implementation details.

## 5. Production Failure Modes

### Context-window degradation

`Google ADK` has the clearest first-party mitigation. Context compaction can trigger on **token threshold** or **turn count**, summarizes older events, and retains a configurable tail of recent raw events ([ADK context compaction](https://adk.dev/context/compaction/)). Its artifact model also keeps large blobs out of the default session transcript unless the model explicitly loads them ([ADK artifacts](https://adk.dev/artifacts/)).

`OpenAI Agents SDK` gives several manual controls rather than one global compactor: `SessionSettings(limit=N)` can bound retrieved history; `session_input_callback` can prune or reorder session history before a call; `call_model_input_filter` can rewrite the final model input; and `nest_handoff_history` can compact nested handoff transcripts into ordered summary segments ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`LangGraph` has persistence and cache primitives but the core docs consulted do not describe a built-in automatic transcript compactor for agent history. In practice, teams usually summarize inside nodes, move durable facts to stores, or use node caching to avoid redoing expensive work ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [LangGraph use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)) [inferred].

`CrewAI` docs stress keeping Flow state minimal and structured, and they surface whole-flow token usage, but they do not publish a comparable first-party history-compaction mechanism in the core Flow docs consulted here ([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)).

### Infinite loops

- `OpenAI Agents SDK`: explicit `max_turns`; exceeding it raises `MaxTurnsExceeded` ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).
- `LangGraph`: explicit `recursion_limit`, with default **1000** steps from version `1.0.6+`; exceeding it raises `GraphRecursionError` ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).
- `Google ADK`: `LoopAgent.maxIterations` stops the loop after a fixed count; if omitted, the loop can run indefinitely until a sub-agent escalates ([ADK LoopAgent](https://adk.dev/api-reference/typescript/interfaces/LoopAgentConfig.html), [ADK LoopAgent Java](https://adk.dev/api-reference/java/com/google/adk/agents/LoopAgent.html)).
- `CrewAI`: the framework supports self-loops and revision loops through routing and `@human_feedback`, so explicit end conditions are an application responsibility ([CrewAI human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)) [inferred].

### State drift and replay divergence

`LangGraph` is explicit that resumed nodes re-run from the start of the node function, so non-idempotent side effects can duplicate unless isolated ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). `OpenAI Agents SDK` documents best-effort rollback of recently persisted input items in local-session retries, which reduces duplicate history but does not guarantee perfect external side-effect rollback ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). `CrewAI` differentiates **resume** from **fork**, which helps preserve lineage semantics, but side-effect idempotency is still outside the framework contract ([CrewAI Flows](https://docs.crewai.com/en/concepts/flows)). `Google ADK` centralizes state updates through session events and explicit storage services, which reduces ambiguity in where state lives ([ADK session service](https://adk.dev/sessions/session/), [ADK sessions overview](https://adk.dev/sessions/)) [inferred].

### Cascading timeouts, backpressure, and degraded responsiveness

Official circuit-breaker and deadline-budget recipes are sparse across all four frameworks. The clearest concrete warnings are:

- `OpenAI Agents SDK`: websocket reliability trade-off is explicit; when reliability matters more than websocket latency, the docs recommend **HTTP/SSE**, and long reasoning turns may require adjusting `ping_timeout` ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).
- `LangGraph`: async durability can build operational pressure if persistence lags execution; this was serious enough to warrant a fix for checkpoint backlog in source code ([LangGraph PR #7112](https://github.com/langchain-ai/langgraph/pull/7112)).
- `Google ADK` and `CrewAI`: both expose async and parallel workflow primitives, but the consulted docs do not publish first-party timeout-budget formulas or built-in circuit-breaker thresholds.

### Hallucinated tool parameters and unsafe actions

`OpenAI Agents SDK` mitigates this with approvals plus MCP/tool approval policies, but the SDK is still downstream of model parameter generation, so schema rigor remains important ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/)). `CrewAI` mitigates via task guardrails, structured outputs, and hooks that can reject or rewrite messages before execution ([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)). `Google ADK` reduces orchestration ambiguity with deterministic workflow nodes, but the consulted docs do not advertise a public strict-schema guarantee equivalent to OpenAI's approval surface or Anthropic-style strict tool guarantees [inferred]. `LangGraph` similarly depends on the surrounding model/tool stack for JSON-schema enforcement rather than providing it as a core runtime feature [inferred].

### Incident and post-mortem coverage

> ⚠️ Limited public data available for detailed RCA-style incident writeups specific to these frameworks. The source sets are predominantly product docs, API references, and repository materials, not production post-mortems.

## 6. Enterprise System Design Scenarios

### 6.1 Decision matrix

| Framework | Best fit | Strongest documented strengths | Main trade-offs / blind spots |
| --- | --- | --- | --- |
| `LangGraph` | Complex, stateful, branching workflows that need explicit control | Super-step graph semantics, checkpointing, pending writes, node caching, persistent savers ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) | Replays from node start after resume; governance/security largely assembled outside core runtime ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)) |
| `OpenAI Agents SDK` | OpenAI-centric assistants with tool approvals, MCP, and strong built-in tracing | Lightweight runner loop, handoffs, sessions, serializable `RunState`, approvals across tools/MCP, tracing on by default ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/)) | Durable execution beyond pause/resume is mostly delegated to external orchestrators; websocket transport is one-response-per-connection and 60-minute bounded ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| `Google ADK` | Enterprise agent platforms, especially multi-language and Google Cloud deployments | Multiple workflow families, explicit Session/State/Memory split, context compaction, artifact isolation, documented row-level locking ([ADK multi-agent workflows](https://adk.dev/agents/multi-agents/), [ADK sessions overview](https://adk.dev/sessions/), [ADK session service](https://adk.dev/sessions/session/), [ADK context compaction](https://adk.dev/context/compaction/), [ADK artifacts](https://adk.dev/artifacts/)) | Public benchmark and fine-grained security-governance data are limited; docs are evolving quickly around workflow generations |
| `CrewAI` | Rapid delivery of role-based multi-agent apps with evented application logic | Flow-first architecture, aggregated usage metrics, built-in human feedback, first-class A2A and MCP docs ([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [CrewAI human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows), [CrewAI A2A protocol](https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation), [CrewAI MCP overview](https://docs.crewai.com/v1.15.10/en/mcp/overview)) | Distributed locking, exactly-once side effects, and first-party performance benchmarks are less explicit than ADK/LangGraph docs |

### 6.2 Recommended deployment patterns

**Pattern A: User-facing SaaS copilot with human approval for sensitive actions**

- Prefer `OpenAI Agents SDK` when the stack is already OpenAI-centric and side-effecting tools need a first-party approval mechanism across nested agents and MCP ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/)).
- Prefer `LangGraph` if the same product also needs graph-native branching, time-travel-style inspection, or durable graph checkpoints for long conversations ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

**Pattern B: Long-running operations workflow with pauses, retries, and resume**

- `Google ADK` is the most complete out-of-the-box choice in this source set when you want explicit session services, context compaction, memory separation, and documented DB locking ([ADK sessions overview](https://adk.dev/sessions/), [ADK session service](https://adk.dev/sessions/session/), [ADK context compaction](https://adk.dev/context/compaction/)).
- `OpenAI Agents SDK` is viable here only if paired with an external durable orchestrator such as Temporal, Dapr, Restate, or DBOS ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

**Pattern C: Internal automation platform with heterogeneous tools and remote agents**

- `CrewAI` stands out if **A2A** and **MCP** interoperability are first-order requirements and the team is comfortable centering the app on Flows ([CrewAI A2A protocol](https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation), [CrewAI MCP overview](https://docs.crewai.com/v1.15.10/en/mcp/overview)).
- `OpenAI Agents SDK` also supports MCP strongly, but more from the perspective of tool invocation and approval than general workflow/app composition ([OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/)).

**Pattern D: Workflow that must minimize prompt bloat over many turns**

- `Google ADK` has the strongest documented native answer because it explicitly ships token-threshold and sliding-window compaction plus artifact lazy-loading ([ADK context compaction](https://adk.dev/context/compaction/), [ADK artifacts](https://adk.dev/artifacts/)).
- `OpenAI Agents SDK` can achieve similar outcomes, but mostly through callbacks and explicit history shaping rather than one dominant built-in compaction mode ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

### 6.3 Capacity-planning heuristics

Useful first-order planning formulas:

```text
critical_path_latency
  ~= Σ(serial LLM/tool steps)
   + max(parallel branch durations)
   + checkpoint / approval / network overhead
```

```text
run_cost
  ~= Σ(all model requests in the orchestration)
   + external tool / transport surcharges
```

For the four frameworks, the practical multiplier is usually:

```text
effective_prompt_tokens
  ~= raw user/task tokens
   + orchestration scaffold
   + replayed history
   + tool outputs reinjected into later turns
   - cached / compacted / lazy-loaded content avoided
```

([OpenAI usage](https://openai.github.io/openai-agents-python/usage/), [ADK context compaction](https://adk.dev/context/compaction/), [ADK artifacts](https://adk.dev/artifacts/), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [LangGraph use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)) [inferred]

### 6.4 Strongest practical conclusions

1. `Google ADK` has the strongest **documented context-management story** of the four: token-threshold compaction, turn-based compaction, artifact lazy-loading, and explicit Session/State/Memory separation.
2. `OpenAI Agents SDK` has the strongest **documented approval and tracing story** of the four, especially for sensitive tool and MCP calls.
3. `LangGraph` has the strongest **state-machine / checkpointing semantics** in OSS documentation, but security and policy controls are less first-class in the core runtime.
4. `CrewAI` is the most explicit about **flow-first application composition** and external agent/tool interoperability through A2A and MCP, but public data on low-level durability guarantees and framework-level benchmarks is thinner.

## Sources

- [1] https://docs.langchain.com/oss/python/langgraph/graph-api - LangGraph super-step execution model, recursion limits, and node replay semantics.
- [2] https://docs.langchain.com/oss/python/langgraph/checkpointers - LangGraph checkpoints, durability modes, and pending writes.
- [3] https://docs.langchain.com/oss/python/langgraph/persistence - LangGraph short-term vs long-term persistence and production savers.
- [4] https://docs.langchain.com/oss/python/langgraph/interrupts - LangGraph pause/resume behavior for human-in-the-loop.
- [5] https://docs.langchain.com/oss/python/langgraph/use-graph-api - LangGraph node caching and graph patterns.
- [6] https://github.com/langchain-ai/langgraph/pull/7112 - Source-level fix for async durability checkpoint backlog.
- [7] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI Agents SDK runner loop, transport behavior, continuation models, and durable-execution integrations.
- [8] https://openai.github.io/openai-agents-python/sessions/ - OpenAI session memory behavior, history shaping, and session backends.
- [9] https://openai.github.io/openai-agents-python/human_in_the_loop/ - OpenAI approval flow, resumable run state, and tool/MCP approval coverage.
- [10] https://openai.github.io/openai-agents-python/tracing/ - OpenAI tracing defaults, sensitive-data capture, and export behavior.
- [11] https://openai.github.io/openai-agents-python/usage/ - OpenAI usage accounting and per-request token breakdowns.
- [12] https://openai.github.io/openai-agents-python/ref/usage/ - OpenAI usage data model including request usage entries and token-detail fields.
- [13] https://openai.github.io/openai-agents-python/mcp/ - OpenAI MCP integration modes and approval policies.
- [14] https://openai.github.io/openai-agents-python/ref/mcp/server/ - OpenAI MCP server approval configuration details.
- [15] https://adk.dev/ - ADK overview, deployment posture, and context-management summary.
- [16] https://adk.dev/agents/multi-agents/ - ADK workflow families and experimental agent routing.
- [17] https://adk.dev/agents/workflow-agents/ - ADK template workflow agents and deterministic orchestration.
- [18] https://adk.dev/sessions/ - ADK Session, State, and Memory model.
- [19] https://adk.dev/sessions/session/ - ADK session lifecycle and DatabaseSessionService concurrency/locking.
- [20] https://adk.dev/sessions/memory/ - ADK long-term memory model and service abstractions.
- [21] https://adk.dev/context/compaction/ - ADK token-threshold and sliding-window context compaction.
- [22] https://adk.dev/artifacts/ - ADK artifact storage and lazy loading into model context.
- [23] https://adk.dev/api-reference/kotlin/google-adk-kotlin-core/com.google.adk.kt.types/-usage-metadata/index.html - ADK usage metadata fields including cached content and thought tokens.
- [24] https://adk.dev/api-reference/typescript/interfaces/LoopAgentConfig.html - ADK loop-agent max-iteration semantics.
- [25] https://adk.dev/api-reference/java/com/google/adk/agents/LoopAgent.html - ADK loop-agent stop conditions and iteration behavior.
- [26] https://docs.crewai.com/en/concepts/production-architecture - CrewAI production guidance and flow-first architecture.
- [27] https://docs.crewai.com/en/concepts/flows - CrewAI Flows state, persistence, usage metrics, and control primitives.
- [28] https://docs.crewai.com/en/learn/human-feedback-in-flows - CrewAI human-in-the-loop flow pattern.
- [29] https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation - CrewAI A2A protocol, transports, and auth schemes.
- [30] https://docs.crewai.com/v1.15.10/en/mcp/overview - CrewAI MCP integration modes and transports.
- [31] https://github.com/KIM3310/agent-orchestration-benchmark - Source-adjacent benchmark with latency, token, cost, and replay metrics for LangGraph and CrewAI.
