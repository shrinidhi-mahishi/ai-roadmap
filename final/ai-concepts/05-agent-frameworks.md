# Module 05: Agent Frameworks

## What Is This?

An agent framework is a library that handles the plumbing of running an agent so you don't have to build it from scratch. Without a framework, you'd need to write code for: managing conversation state, deciding when to stop the loop, resuming after crashes, routing between multiple agents, enforcing safety limits, and handling human approvals.

The major frameworks in 2025-2026 are:
- **LangGraph** (by LangChain): Models agents as state machines with explicit graph nodes and edges. Most flexible, steepest learning curve.
- **OpenAI Agents SDK**: Simple Python SDK where agents are defined as classes with instructions and tools. Easiest to start with.
- **Google ADK**: Built on Genkit, tight integration with Vertex AI and Gemini. Best for Google Cloud shops.
- **CrewAI**: Multi-agent focus where you define "crews" of agents with roles. Best for multi-agent coordination out of the box.

**When you don't need a framework**: If your use case is a single LLM call with one tool and no loops, just write a `while` loop and call the API directly. Frameworks add value when you need state persistence, multi-agent coordination, human-in-the-loop, or durable execution (surviving crashes).

## Why It Matters

Choosing the right framework (or choosing not to use one) is one of the first architectural decisions in any agent project. Each framework makes different trade-offs between flexibility, simplicity, and vendor lock-in.

---

## 2. Core Concepts

Choosing a framework is primarily an infrastructure decision, not an intelligence decision: the same model achieves similar task accuracy regardless of framework. What differs is your control over the execution graph, cost overhead, persistence model, security posture, and deployment target. The framework landscape is consolidating around two interoperability protocols -- MCP (agent-to-tools) and A2A (agent-to-agent) -- which means hybrid architectures using different frameworks for different parts of the system are now practical.

### The Shared Invariant

Across all five frameworks, one rule is absolute: **the model never executes tools, handoffs, or graph edges directly**. It emits a structured action; the runtime dispatches; an observation is injected; the loop continues. The framework is the runtime.

### Control Plane vs Data Plane (All Frameworks)

| Layer | Owns | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
| --- | --- | --- | --- | --- | --- |
| **Control** | Loop budget, routing, checkpoint key, RBAC, stream mux | `StateGraph` compiler + Agent Server APIs | `Runner` loop (`max_turns` default 10) | `Runner` + workflow/graph agents | Flow event graph + Crew `Process` |
| **Data** | Tool HTTP, MCP, A2A, sandboxes | nodes / `ToolNode` / MCP adapters | function tools, hosted tools, MCP, sandbox | ADK tools, `McpToolset`, Vertex tools | `@tool`, MCP DSL, A2A client/server |
| **Persistence** | Resume identity | `thread_id` + `checkpoint_id` | `session_id` / `RunState` / `conversation_id` | `session_id` + `user_id` + `app_name` | Flow `state.id` / checkpoint id |
| **Managed platform** | Hosted control + data | LangSmith + Agent Server | None (you host SDK; OpenAI hosts Responses/MCP/sandbox) | Agent Runtime + Sessions + Memory Bank | CrewAI AMP (SaaS) / Factory (self-hosted) |

### Framework Metaphors

Each framework has a dominant metaphor that shapes how you think about building:

- **LangGraph**: "It is a typed graph." Nodes are functions, edges are transitions, state flows through reducers. You think in graph topology.
- **OpenAI Agents SDK**: "It is a role loop." Agents have instructions and tools; handoffs transfer control. You think in delegation chains.
- **Google ADK**: "It is an agent tree." Parent agents delegate to sub-agents via templates or graphs. You think in hierarchies.
- **CrewAI**: "It is a team." Agents have roles, goals, and backstories; tasks are assigned. You think in organization charts.
- **Microsoft Agent Framework**: "It is a middleware pipeline." Agents, providers, and orchestrators compose via middleware. You think in enterprise patterns.

### What a Framework Does NOT Do

Frameworks do not improve model intelligence. SWE-bench scores are model-dependent, not framework-dependent. The same Claude Sonnet 4 achieves similar scores whether it runs through LangGraph, OpenAI Agents SDK, or a bare API loop. Framework choice affects latency, cost overhead, and operational capabilities -- not task accuracy.

#### Architecture Diagram: Common Framework Abstraction

```
┌──────────────────────────────────────────────────────────────────┐
│               Common Framework Abstraction Layer                  │
│                                                                  │
│  User Request                                                    │
│      │                                                           │
│      ▼                                                           │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                 Orchestrator / Runner                     │    │
│  │   (manages loop, routing, stop conditions, streaming)    │    │
│  │                                                          │    │
│  │   LangGraph: StateGraph    │  ADK: Runner + Workflow     │    │
│  │   Agents SDK: Runner loop  │  CrewAI: Flow + Crew        │    │
│  └──────┬──────────────┬──────────────────┬─────────────────┘    │
│         │              │                  │                       │
│         ▼              ▼                  ▼                       │
│  ┌────────────┐ ┌─────────────┐ ┌─────────────────┐             │
│  │    LLM     │ │    Tool     │ │   State Store   │             │
│  │  Provider  │ │  Registry   │ │                 │             │
│  │            │ │             │ │  - Checkpoints  │             │
│  │ - Anthropic│ │ - MCP tools │ │  - Sessions     │             │
│  │ - OpenAI   │ │ - Functions │ │  - Memory       │             │
│  │ - Gemini   │ │ - Hosted    │ │  - Thread state │             │
│  │ - Ollama   │ │ - A2A peers │ │                 │             │
│  └──────┬─────┘ └──────┬──────┘ └────────┬────────┘             │
│         │              │                 │                       │
│         └──────────────┼─────────────────┘                       │
│                        │                                         │
│                        ▼                                         │
│                  Output to User                                  │
└──────────────────────────────────────────────────────────────────┘
```

The model emits a structured action; the orchestrator dispatches it to a tool;
the tool result is injected back as an observation; the loop continues. This
invariant holds across all frameworks -- only the primitives and terminology differ.

---

## 3. How It Works

### 3.1 LangGraph (LangChain)

**Version**: 1.2.11 (Aug 2026). MIT license. 40.1k GitHub stars, 6.8k forks. Inspired by Pregel and Apache Beam; public interface modeled after NetworkX.

**Core primitives**:

- **StateGraph**: The fundamental construct. A directed graph where nodes are Python functions and edges define control flow. State is a typed dictionary (TypedDict or Pydantic model) that flows through the graph.

- **Nodes**: Python functions that receive current state and return partial state updates. Each node represents a computation step (LLM call, tool execution, conditional logic).

- **Edges**: Three types: (a) normal edges (always traverse), (b) conditional edges (route based on state), (c) entry/finish points (START, END).

- **Reducers**: Functions attached to state channels that define how partial updates merge. Default is overwrite (LastValue). `Annotated[list, operator.add]` merges lists (required for messages and parallel fan-in). Each state key can have its own reducer. **Without a reducer, concurrent writes to the same key raise `InvalidUpdateError`.**

- **Checkpointers**: Persistence adapters saving graph state at each superstep. Only changed values stored per checkpoint, not full snapshots. Backends: MemorySaver (dev -- dies with process), SqliteSaver (single box -- write lock), PostgresSaver (production -- optimized with pipeline mode).

**Execution model (Pregel super-steps)**: All scheduled nodes in a superstep run concurrently; then reducers merge state; then a checkpoint is written. `Send(node_name, arg_state)` from conditional edges enables dynamic fan-out (map-reduce) with per-child state. Fan-in uses reducers on shared channels.

**Compile-time validation**: `.compile(checkpointer=..., store=..., interrupt_before=..., interrupt_after=...)`. Checks orphaned nodes. Checkpoints are written at super-step boundaries, not mid-function. If a node is retried or resumed after `interrupt()`, the whole node function restarts. Side effects before the pause re-run unless wrapped in Functional API `task`s.

**Streaming (two APIs)**:
- Stream-mode: `updates`, `values`, `messages`, `custom`, `checkpoints`, `tasks`, `debug` (combinable)
- Event streaming v3 (LangGraph >=1.2): typed projections as independent iterators (`stream.messages`, `stream.values`, `stream.interrupts`)

**HITL**: `interrupt(value)` raises a resumable `GraphInterrupt`; client resumes with `Command(resume=...)`. Requires a checkpointer + `thread_id`. Graph waits indefinitely until resume.

**Threads**: `configurable.thread_id` is the primary key. No `thread_id` = no save, no interrupt resume. Production pattern: `thread_id = f"{tenant}:{user}:{session}"`. A constant string shares history across users (documented failure mode).

**Durability modes**:
- `sync`: persist before next step (slowest, safest)
- `async` (default): persist while next step runs
- `exit`: persist only when graph exits (lose mid-run on pod kill)

**DeltaChannel (beta)**: Store a sentinel in checkpoint blobs; reconstruct by replaying ancestor writes through a deterministic reducer. Makes accumulating `messages` O(1) blob size per step instead of O(N). On-disk format not stable.

**Stores (cross-thread memory)**: Namespace tuple + key + JSON value. PostgresStore/Mongo/Redis/InMemory. Semantic search if configured with embeddings. Separate from checkpointers (thread-scoped). Use Store for cross-thread facts, not stuffed into the graph blob.

**Tools**: Nodes call tools; `langgraph.prebuilt` ReAct (`create_react_agent`) + `ToolNode` is the stock agent. MCP via `langchain-mcp-adapters`. Agent Server exposes graphs as MCP tools at `/mcp` (Streamable HTTP).

**A2A**: Not native to the OSS graph runtime. Expose via Agent Server MCP, or wrap in A2A protocol. Vertex Agent Runtime can host LangGraph as a deploy type.

**Retries**: `RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2, jitter=True)`. Does not parse `Retry-After`. Node restart = non-idempotent tools double-charge unless tools are idempotent or wrapped in `task`s.

**Cloud runtime**: LangSmith Deployment (renamed from LangGraph Platform, Oct 2025). Control plane never connects to data plane. Cloud: LangChain hosts both on AWS/GCP. Hybrid: SaaS control, your VPC data. Self-hosted: both in your cluster (Enterprise). Standalone Agent Server: Docker/K8s, no control plane.

**Temporal plugin (Public Preview)**: Graph as Workflow; nodes as Activities (timeouts, retries, at-least-once). `interrupt()` becomes durable wait (no compute). Streaming via `streaming_topic` + `WorkflowStream`.

### 3.2 OpenAI Agents SDK

**Version**: 0.22.0 (Aug 2026). MIT license. 28.8k GitHub stars, 4.5k forks. Design philosophy: "Enough features to be worth using, but few enough primitives to make it quick to learn."

**Core primitives**:

- **Agent**: An LLM configured with name, instructions, tools, guardrails, handoffs, and `output_type` (Pydantic for typed outputs). Built-in agentic loop that continues until task completion.

- **Runner**: Manages execution via `Runner.run()` (async), `Runner.run_sync()`, or `Runner.run_streamed()`. Turn-based loop: call LLM -> process output (text = done, handoff = switch agent, tool calls = execute and continue).

- **Handoffs**: Explicit delegation mechanism. Handoffs appear as tools to the LLM (e.g., `transfer_to_refund_agent`). Support `input_filter` to modify conversation history, `on_handoff` callbacks, dynamic enable/disable, and nested history compaction (`nest_handoff_history=True`, opt-in beta).

- **Guardrails**: Three-tier validation: input (first agent only), output (last agent only), tool (every `@function_tool` invocation). Input `run_in_parallel=True` (default) = better latency, possible wasted tokens if tripwire fires late. Tripwire mechanism halts execution on violation. **Tool guardrails do not wrap hosted tools, handoffs, `Agent.as_tool()`, Shell, or Computer.**

**Agent types**:
- Text Agents: Standard LLM workflows
- Sandbox Agents: Pre-configured with container environments for long-running tasks (Docker or Unix sandbox with snapshot/restore)
- Realtime Agents: Voice via WebSocket transport
- Voice Agents: Three-stage pipeline (STT -> Agent -> TTS)

**Two multi-agent patterns (official)**:

| Pattern | Ownership of Reply | Mechanism |
| --- | --- | --- |
| **Handoffs** | Specialist takes over | Control moves; `AgentUpdatedStreamEvent` |
| **Agents as tools** | Manager keeps reply | Nested run, bounded |

Official guidance: split only when instructions, tools, or policy actually change -- extra agents multiply prompts, traces, and approval surfaces.

**Tools (five categories)**:
1. Hosted OpenAI tools on Responses (`WebSearchTool`, `FileSearchTool`, `CodeInterpreterTool`, `HostedMCPTool`, `ImageGenerationTool`)
2. Local tools (`ComputerTool`, `ApplyPatchTool`, `ShellTool`)
3. Function tools (`@function_tool` with schema + Pydantic)
4. Agents-as-tools
5. Experimental Codex tool

**Hosted Tool Search**: `ToolSearchTool` + `defer_loading=True` loads subsets of a large tool surface at runtime. Rule of thumb: <10 functions per namespace.

**Programmatic Tool Calling**: Runs model-generated JS in a hosted V8 with no Node/fs/net -- only allowlisted tools.

**State management**: Session-based persistence. Backends: SQLite (dev), SQLAlchemy/Postgres (production), Redis (distributed low-latency), MongoDB (horizontal scaling with atomic batching), Dapr (30+ stores via sidecars), EncryptedSession wrapper with TTL, OpenAI Conversations API. Sessions automatically retrieve history pre-run and persist post-run.

**HITL**: `needs_approval` on function tools, `Agent.as_tool`, Shell, ApplyPatch; MCP `require_approval`; HostedMCP `tool_config`. Pause -> `result.interruptions` -> `state.approve()` / `reject()` -> `Runner.run(agent, state, session=same)`.

**Durable execution integrations**: Temporal (GA March 2026), Dapr, Restate, DBOS. All preserve progress across failures/restarts and support tool approval workflows. SDK is NOT Temporal by itself -- process crash without session/RunState = lost in-flight turn.

**Tracing**: On by default. Spans: `trace` -> `task_span` -> `turn_span` -> `agent_span` / `generation_span` / `function_span`. `trace_include_sensitive_data` gates I/O in traces. Custom processors via `add_trace_processor`.

**Streaming**: `Runner.run_streamed()` -> `stream_events()` until iterator ends. Events: raw `ResponsesStreamEvent`, `RunItemStreamEvent`, `AgentUpdatedStreamEvent`. Cancel: immediate or `cancel("after_turn")`. Not consuming `stream_events()` to completion -> session/approval bookkeeping incomplete.

### 3.3 Google ADK (Agent Development Kit)

**Version**: 2.7.1 (Aug 2026). Apache 2.0 license. 21.2k GitHub stars, 3.9k forks. Multi-language: Python, TypeScript, Go, Java, Kotlin. Design principle: "Start with prompts and tool calls, grow to multi-agent orchestration and graph-based workflows."

**Core primitives**:

- **LlmAgent / Agent**: Basic building block with model, instruction, and tools. Configured with name, model reference (e.g., `gemini-flash-latest`), instruction text, and tool list.

- **Workflow Agents (deterministic, not LLM-routed)**:

| Class | Semantics | Stop Condition |
| --- | --- | --- |
| `SequentialAgent` | Sub-agents in list order; shared `InvocationContext` / session state | End of list |
| `ParallelAgent` | Concurrent independent sub-agents | All complete |
| `LoopAgent` | Repeat sub-agents | You must set `max_iterations` and/or exit signal -- LoopAgent does not infer "good enough" |

ADK 2.0: template workflows are superseded for new work by graph-based workflows (explicit edges, HITL nodes). Templates remain supported.

- **SessionService**: Context management layer handling sessions (conversational containers with rewind/migration), state (persistent key-value store), events (event-driven communication), and memory (long-term retention).

**Context management (ADK's distinguishing feature)**: Unlike simple string concatenation, ADK actively manages context by filtering irrelevant events, summarizing older turns, lazy-loading artifacts, and tracking token usage. "Every token earns its place." This adds hidden model calls -- budget as extra Flash calls in traces.

**State**: Session `state` dict with key prefixes for scoping. Tools write state. Output keys pass between SequentialAgent stages. `ParallelAgent` sub-agents share `session.state` -- use distinct keys or you get race conditions.

**HITL (two mechanisms)**:
1. Graph `RequestInput` / Go `ResumeOrRequestInput` -> persist, resume
2. Tool confirmation: `RequireConfirmation: true` -> `adk_request_confirmation`

Rewind invalidates later turns (polluted-context recovery).

**Tools**: Function tools, Google Search, Vertex ecosystem, OpenAPI, `McpToolset` (stdio/SSE; `tool_filter`; `getstate`/`setstate` for Cloud Run/GKE -- active MCP sockets are not restored, reconnect on demand). Cloud API Registry + ADK `ApiRegistry` for org-curated tools. Callbacks: `before_tool_callback` / `after_tool_callback` / `on_tool_error_callback`.

**A2A (native)**: First-class support. AgentCard discovery (`/.well-known/agent-card.json`), `RemoteA2aAgent(agent_card=URL)`, `to_a2a(root_agent)`, `adk api_server --a2a`. Agent Runtime deploys A2A agents.

**Model support**: Multi-model with native Gemini optimization. Also supports Claude, OpenAI, Ollama, vLLM, LiteLLM. Apigee AI Gateway for model routing and load balancing.

**Deployment**: One-command to Google Cloud. Targets: Agent Runtime (managed), Cloud Run (serverless), GKE (Kubernetes). Built-in Cloud Trace, authentication, enterprise security without code changes.

**Breaking changes**: v2.0+ sessions incompatible with pre-1.28 versions. Production systems must coordinate upgrades carefully.

### 3.4 CrewAI

**Version**: 1.15.17 (Aug 2026). MIT license. 57.4k GitHub stars, 8.2k forks. Standalone Python framework (no LangChain dependency since rewrite). Claims 5.76x faster execution than LangGraph in QA benchmarks (vendor claim -- validate independently). 100,000+ certified developers.

**Official production shape**: Start with a Flow; invoke a Crew only when a step needs autonomous multi-role work.

| Primitive | Job | State |
| --- | --- | --- |
| **Flow** | Event-driven backbone: `@start`, `@listen`, `@router`, `@persist`, `@human_feedback` | Typed Flow state; `usage_metrics` aggregates every LLM call |
| **Crew** | Team of Agents + Tasks | `Process.sequential` (default) or `Process.hierarchical` (requires `manager_llm`) |
| **Agent** | `role`, `goal`, `backstory`, tools, LLM, optional memory | Max iterations default 20, execution timeout, rate limiting |
| **Task** | Description, agent, context deps, `output_pydantic`/`output_json`, `human_input` flag | Sequential guardrail chains with max retries (default 3) |

**Memory (unified architecture)**: One `Memory` class. LLM infers scope/categories/importance on save; recall blends semantic + recency + importance. Default embedder: OpenAI `text-embedding-3-large` (not free -- budget the cost). After each task: extract facts; before each task: inject recall. Persistence: LanceDB on disk (survives process restarts on same volume; not a multi-region consensus store).

**MCP**: `agent.mcps=[url, "catalog#tool", MCPServerStdio/HTTP/SSE]` or `MCPServerAdapter`. AMP can export workflow as MCP server.

**A2A**: First-class (`pip install 'crewai[a2a]'`). `A2AClientConfig` (remote) / `A2AServerConfig` (expose). Auth: Bearer, OAuth2, API key, HTTP.

**HITL**: Flow `@human_feedback` (local/console, v1.8.0+); webhook HITL on crew kickoff (`human_input` task + Bearer webhook); AMP: in-platform review, assignment, escalation, SLA.

**State persistence (dual-layer)**:
- Flow-level: `@persist` with SQLiteFlowPersistence. Each state gets unique UUID. Resume mode (continue) or fork mode (new execution from prior snapshot).
- Crew-level: `checkpoint=True` saves to `.checkpoints/`. Configurable events (`on_events=["task_completed"]`). Resume via `Crew.from_checkpoint()`.

**Delegation controls**: `allow_delegation=False` (default) prevents agents from delegating. When enabled, delegation is task-scoped. Workers in hierarchical process should set `allow_delegation=False` or manager/worker ping-pong is unbounded.

### 3.5 Microsoft Agent Framework (MAF)

**Version**: 1.14.0 (Aug 2026). 13.0k GitHub stars, 2.2k forks. Enterprise successor to AutoGen (60.6k stars but now in maintenance mode, community-managed, no new features).

**Architecture**: Multi-language (.NET + Python) with consistent APIs. Key capabilities: multiple agent providers (Foundry, Azure OpenAI, OpenAI, GitHub Copilot SDK), middleware system, graph-based orchestration with checkpointing/streaming/HITL/time-travel, Foundry hosted agents ("2 additional lines of code"), OpenTelemetry observability, declarative YAML agents, and agent skills system.

**Orchestration patterns**: Sequential, concurrent, handoff, group collaboration, and Magentic-One (multi-agent team for web browsing, code execution, file handling).

**AutoGen legacy**: Three-layer architecture: Core (event-driven, distributed agents), AgentChat (simplified multi-agent API), Studio (no-code GUI). Known issue: conversation-based architecture sends full message history with each call, causing quadratic token growth ("conversation explosion"). Production users advised to migrate to MAF.

### 3.6 Anthropic Agent Patterns

Anthropic does not ship a standalone framework. Instead:
- **Claude Code**: CLI-based coding agent with built-in agent loop, MCP integration, and sub-agent spawning
- **MCP**: Open standard for connecting AI to external tools. Supported by Claude, ChatGPT, VS Code, Cursor, and many others
- **Claude Agent SDK (Python)**: Managed agents with sandbox environments via the Anthropic SDK
- **Agentic patterns documentation**: Guidance on building loops, tool use, multi-agent orchestration via the Messages API

### 3.7 Interoperability Protocols

**MCP (Model Context Protocol)**: Open-source standard. "USB-C for AI applications." All major frameworks support MCP tool integration. HTTP auth (spec 2025-11-25): MCP server = OAuth 2.1 resource server; PKCE mandatory; no implicit/password grants.

Hard rules for MCP security:
- No token passthrough to downstream APIs (confused deputy)
- Audience-validate: token for `mcp.other.com` must fail
- Scopes at tool grain (`mcp:tool:{name}:{read|execute}`), not server-wide
- Separate read MCP from write MCP so retrieved tickets cannot instruct `send_email`

**A2A (Agent-to-Agent Protocol)**: Google-initiated, Linux Foundation, Apache-2.0. Agent Card discovery, task lifecycle, messages, artifacts, streaming, push notifications. Spec 1.0.0. A2A is NOT an agent framework and NOT a replacement for MCP. A2A is for opaque agent-to-agent communication across trust domains; MCP is for agent-to-tool communication.

| Mechanism | Problem It Solves | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- | --- |
| Native multi-agent | In-process orchestration | Graph nodes/subgraphs/Send | Handoffs + as_tool | Sub-agents, templates, graphs | Crew processes + Flow steps |
| **MCP** | Agent -> tools | Consume + serve at `/mcp` | HostedMCP + local MCP | `McpToolset` + serve | `mcps` DSL; AMP export |
| **A2A** | Agent -> opaque remote agent | Indirect (wrap) | Indirect (HTTP/MCP) | **First-class** | **First-class** |

---

## 4. Key Patterns & Best Practices

### Framework Selection Decision Rule

Choose **LangGraph** when the product *is* a state machine (cycles, fan-out, time-travel, multi-week HITL). Choose **OpenAI Agents SDK** when the product *is* a tool-using assistant on OpenAI's hosted surface and you want traces/evals without a graph compiler. Choose **ADK** when the control plane must be Google Cloud (IAM, CMEK, A2A mesh, Memory Bank, registry). Choose **CrewAI** when the unit of work is a role team and you want Flow as the outer app plus AMP for ops. Choose **MAF** when you are in a .NET enterprise with Azure infrastructure.

### Detailed Selection Criteria

**Use LangGraph when**:
- You need fine-grained control over agent execution flow
- The workflow has complex conditional logic, cycles, or parallel branches
- You need production-grade persistence with point-in-time recovery (time travel)
- You need map-reduce patterns with dynamic fan-out (Send)
- The team has strong Python skills and can handle low-level abstractions
- Best for: Complex stateful workflows, research applications, custom architectures

**Use OpenAI Agents SDK when**:
- You want a lightweight framework with minimal learning curve
- The use case involves multiple specialist agents with clear handoff patterns
- You need built-in sandboxing for code execution tasks
- Voice/realtime agents are part of the requirement
- Provider flexibility needed (100+ LLM support via LiteLLM)
- Best for: Customer service agents, triage/routing systems, voice applications

**Use Google ADK when**:
- You are deploying on Google Cloud (native GCP integration)
- Context efficiency is critical (ADK's context management is best-in-class)
- You need multi-language support (Python, Go, Java, Kotlin, TypeScript)
- You want built-in A2A protocol support for agent interoperability
- Best for: Google Cloud enterprises, multi-language teams, context-intensive applications

**Use CrewAI when**:
- You want high-level abstractions with minimal boilerplate
- The use case maps naturally to "team of specialists" metaphor
- You need built-in memory, knowledge, and learning across runs
- Enterprise features (RBAC, monitoring, managed deployment) needed via AMP Suite
- Best for: Content generation, research automation, business process automation

**Use Microsoft Agent Framework when**:
- You are in a .NET-heavy enterprise with Azure infrastructure
- You need cross-language consistency (C# + Python)
- You are migrating from AutoGen
- Best for: .NET enterprises, Azure-native deployments

### Hybrid Approaches

**MCP as the integration layer**: Use different frameworks for different agent types, with MCP providing tool interoperability. Example: LangGraph for complex orchestration, OpenAI Agents SDK for customer-facing voice agents, both connecting to the same MCP tool servers.

**A2A for framework interop**: A CrewAI research crew can delegate to a LangGraph analysis pipeline via A2A, with each framework handling what it does best. Do not share checkpointers across frameworks.

**Orchestrator + specialist**: Use a lightweight framework (Agents SDK) for the top-level orchestrator, with heavyweight frameworks (LangGraph) for specialist sub-workflows needing complex state management.

### Production Architecture Patterns

The universal production pattern: wrap agent cognition in durable execution.

- **LangGraph + Temporal**: Graph as Workflow; nodes as Activities. Checkpoint at every node. `interrupt()` becomes durable wait (no compute). Best mechanics for regulated HITL.
- **Agents SDK + Redis sessions + Temporal**: Session-based persistence with durable execution for long-running workflows.
- **ADK + Agent Runtime**: Managed serverless deployment with Sessions and Memory Bank on GCP.
- **CrewAI + AMP**: Managed deployment with enterprise features. Still add caps (process, max iterations, persist) before production traffic.

### Token Overhead by Framework

All frameworks use the same underlying LLM APIs, so per-token costs are identical. The differences:

| Overhead Source | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- |
| Scaffolding tokens (system prompts, schemas) | Lowest (developer-controlled) | +50-100 tokens per handoff schema | Context summaries (hidden model calls) | Highest (~200-500 tokens per agent role/goal/backstory) |
| Extra LLM calls | 0 (checkpointer is infra, not tokens) | +1 for input guardrail (blocking) | Unmetered context summarization | +1 extract + embed per task (memory); +N for hierarchical manager |
| Context management | Manual | `OpenAIResponsesCompactionSession` for server-side compaction | Active filtering/summarization (best-in-class) | `respect_context_window=True` auto-summarization (lossy) |
| Caching | Relies on provider caching | `previous_response_id` for server-side chaining | Apigee AI Gateway model-level cache | Tool result caching (`cache=True` default) |

**CrewAI memory tax**: ~2 x (500-token extract) + ~2k embedding tokens per 2-task crew. Order of $0.001-$0.01/execution at cheap model rates -- small vs frontier model costs, material vs cheap-model-only agents.

**Worst-case nested costs**: Agents SDK handoffs + as_tool each with 10 turns -> up to 100 model calls. CrewAI hierarchical manager can double calls vs sequential.

---

## 5. System Design Considerations

### Token Economics -- Platform SKUs

**LangSmith (langchain.com/pricing, Aug 2026)**:

| Item | Published |
| --- | --- |
| Developer | $0/seat, 5k base traces/mo, 1 seat |
| Plus | $39/seat/mo, 10k base traces/mo, Deployment + Engine |
| Enterprise | Custom; hybrid/self-host; custom SSO, ABAC, RBAC |
| LCU (compute unit) | $1.50 |
| LSU (storage unit) | $1.00 |
| Runtime compute | ~$0.0675/vCPU-hr (inferred from 0.045 LCU/vCPU-hr) |
| Runtime memory | ~$0.009/GiB-hr |
| Sandbox | Per-second billing; Cloud only |
| Trace retention | Base: 14-day; Extended: 400-day |

**Gemini Enterprise Agent Platform (Aug 2026)**:

| Item | Published |
| --- | --- |
| Runtime vCPU | First 50 vCPU-h/mo free, then $0.085/vCPU-h |
| RAM | First 100 GiB-h free, then $0.009/GiB-h |
| Session storage | $0.30/GiB-month (1 GiB-mo free) |
| Memory Bank | Storage + ops; retrieval can exceed runtime cost |
| Idle billing | Not billed (to nearest second of usage) |

**CrewAI AMP**: Basic: 50 executions/mo free. Enterprise: custom pricing (SSO, RBAC, PII redaction, VPC). Cannot publish $/1k for AMP without a vendor quote.

**OpenAI hosted extras**: Web search ($10/1k calls), file search ($2.50/1k calls + $0.10/GB-day), code interpreter, container minutes -- all separate from model token costs.

### Reference Cost Comparison (4-call skeleton, inferred)

Definition: 1 user task, 4 model calls (route + 2 tool turns + synthesize), 3,000 input + 800 output tokens per call.

| Model | $/execution (inferred) | $/1k executions (inferred) |
| --- | --- | --- |
| gpt-4.1 | $0.050 | ~$50 |
| gpt-5.6-terra | $0.062 | ~$62 |
| gpt-5.6-luna | $0.006 | ~$6 |
| Gemini 2.5 Flash | $0.012 | ~$12 |
| Gemini 3.6 Flash | $0.042 | ~$42 |
| Gemini 2.5 Pro (<=200k) | $0.047 | ~$47 |

Platform overhead adds ~$0.04-$0.75/1k on top, except Memory Bank retrieval and verbose session-event SKUs which can exceed runtime costs.

### Adoption Metrics (August 2026)

| Framework | GitHub Stars | PyPI Version | License |
| --- | --- | --- | --- |
| AutoGen (maintenance) | 60.6k | 0.7.5 | MIT |
| CrewAI | 57.4k | 1.15.17 | MIT |
| LangGraph | 40.1k | 1.2.11 | MIT |
| OpenAI Agents SDK | 28.8k | 0.22.0 | MIT |
| Google ADK | 21.2k | 2.7.1 | Apache-2.0 |
| MS Agent Framework | 13.0k | 1.14.0 | MIT |

Note: AutoGen's 60.6k stars reflect cumulative interest before maintenance mode; active development shifted to MAF.

### Enterprise Security & Governance

**Identity, SSO, RBAC comparison**:

| Capability | LangGraph OSS | LangSmith | Agents SDK | ADK/Agent Platform | CrewAI AMP |
| --- | --- | --- | --- | --- | --- |
| SSO | DIY | Plus: Google/GitHub; Enterprise: custom SSO | Your app | GCP IAM, OAuth, API keys | SAML 2.0 + OIDC (Entra/Okta/Auth0) |
| RBAC | DIY | Org User/Admin (Plus); custom RBAC+ABAC (Enterprise) | Your app | IAM + IAM Conditions | Feature RBAC + entity RBAC |
| Agent identity | `langgraph_auth_user` | Same | Pass tokens in context | IAM agent identity | Workload identity (Enterprise) |

**Network and data protection**:

| Capability | LangSmith | Agent Platform | Agents SDK | CrewAI AMP Enterprise |
| --- | --- | --- | --- | --- |
| VPC isolation | Cloud US/EU; Hybrid/Self-Hosted in VPC | VPC-SC, Private Service Connect | Hosted tools outside your VPC | Dedicated VPC, NAT |
| Encryption | Platform encryption | CMEK, DRZ at rest | EncryptedSession wrapper | PII redaction, policies |
| HIPAA | Enterprise | Yes (Runtime, Sessions, Memory Bank) | Account-level | Listed (verify ATO) |
| PII handling | LLM Gateway redaction; traces `HIDE_INPUTS/OUTPUTS` | Access Transparency/Approval | `trace_include_sensitive_data=False` | PII redaction |

**Audit and observability**:

| Framework | Traces | Integration |
| --- | --- | --- |
| LangGraph | LangSmith traces (14d base / 400d extended) + Agent Server access logs | OpenTelemetry, LangSmith |
| Agents SDK | OpenAI Traces dashboard + custom processors | OpenTelemetry |
| ADK | Cloud Audit + Cloud Trace + Feedback service | Native GCP observability |
| CrewAI | AMP tracing + OpenTelemetry | OTel, custom |

Critical: Map traces to identity (LangSmith auth user, GCP IAM, AMP RBAC actor) or they are useless for SOX/compliance.

### Distributed Resilience Comparison

| Capability | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- |
| Thread checkpoint + time travel | Yes (super-step granularity) | RunState snapshot (not full graph history) | Session events; rewind | Flow persist + checkpoints (early release) |
| Cross-thread memory | Store (PostgresStore/Mongo/Redis) | DIY / Conversations / your DB | Memory Bank (cross-session, topic-based) | Unified Memory + LanceDB |
| Durable HITL wait (days, ~$0) | Agent Server or Temporal | Park RunState in your DB | Session + RequestInput | AMP webhooks / persist |
| Distributed workers | Agent Server queue / Temporal | Redis/SQL session + your fleet | Agent Runtime (serverless) | AMP / your FastAPI |
| At-least-once node semantics | Yes (restart node) | Tool retry policies you write | Tool callbacks | Checkpoint skip completed tasks |
| Native Temporal integration | Public Preview plugin | GA (March 2026) | No (use Cloud Tasks/Workflows) | No (wrap yourself) |

### Rate Limits and Circuit Breaking

**429 vs circuit breaker (critical distinction)**:
- **429**: Your quota. Honor `Retry-After` headers. Do NOT trip the circuit breaker or fail over (you replicate the spike). Exception: billing 429 -> halt spend.
- **5xx / 529 / timeout / mid-stream**: Trip the breaker (closed -> open -> half-open). Fail fast vs waiting full LLM timeout.

**Critical rule**: Retry exactly one layer (SDK OR gateway). Nested 3x3x3 = 27 upstream calls (SRE amplification).

**Gateway pattern (TrueFoundry 3-layer)**: (1) token bucket per (user, repo, model); (2) pattern breaker (identical-prompt loop, cost velocity, consecutive 429s, >50% errors/60s); (3) fallback chain: primary -> cheaper model -> semantic cache -> 503.

---

## 6. Code Examples

### LangGraph: ReAct Agent with Checkpointing

```python
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres import PostgresSaver
from typing import TypedDict, Annotated
import operator

# Option 1: Use the prebuilt ReAct agent
agent = create_react_agent(
    model="claude-sonnet-4",
    tools=[search_tool, calculator_tool],
    checkpointer=PostgresSaver(conn_pool)
)

# Option 2: Build a custom graph with typed state
class State(TypedDict):
    messages: Annotated[list, operator.add]   # Reducer: append, don't overwrite
    plan: str                                  # LastValue (default): overwrite
    results: Annotated[list, operator.add]     # Merge parallel worker outputs

graph = StateGraph(State)
graph.add_node("planner", plan_node)
graph.add_node("worker", work_node)
graph.add_node("synthesizer", synth_node)

# Conditional edges for dynamic routing
graph.add_edge(START, "planner")
graph.add_conditional_edges("planner", route_fn, 
    {"execute": "worker", "done": END})
graph.add_edge("worker", "synthesizer")

# Compile with persistence and HITL
app = graph.compile(
    checkpointer=PostgresSaver(conn_pool),
    interrupt_before=["worker"]  # Pause for human review before execution
)

# Invoke with thread_id (required for checkpointing)
config = {"configurable": {"thread_id": f"tenant_1:user_42:session_abc"}}
result = app.invoke({"messages": [user_input]}, config=config)

# Resume after HITL approval
from langgraph.types import Command
app.invoke(Command(resume={"approved": True}), config=config)

# Time travel: fork from any historical checkpoint
state = app.get_state(config)
app.update_state(config, {"plan": "revised plan"})  # Creates new checkpoint
```

### OpenAI Agents SDK: Multi-Agent with Guardrails

```python
from agents import Agent, Runner, handoff, InputGuardrail, GuardrailFunctionOutput
from agents.sessions import SQLAlchemySession

# Guardrail: check for harmful content before processing
async def content_filter(ctx, agent, input):
    result = await Runner.run(
        Agent(name="filter", instructions="Check for harmful content",
              model="gpt-5.6-luna"),  # Cheap model for guardrail
        input, context=ctx.context
    )
    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered="harmful" in result.final_output.lower()
    )

# Specialist agents with scoped tools
refund_agent = Agent(
    name="refund_specialist",
    instructions="Handle refund requests. Max refund $500.",
    tools=[process_refund, check_order],
    output_type=RefundResult  # Typed output
)

billing_agent = Agent(
    name="billing_specialist",
    instructions="Handle billing inquiries.",
    tools=[get_invoice, update_payment]
)

# Triage with guardrails and handoffs
triage = Agent(
    name="triage",
    instructions="Route customer requests to the right specialist.",
    handoffs=[refund_agent, billing_agent],
    input_guardrails=[InputGuardrail(guardrail_function=content_filter)]
)

# Session persistence (Postgres for production)
session = SQLAlchemySession(
    url="postgresql://...",
    session_id="user_42_session_abc"
)

# Run with safety controls
result = await Runner.run(
    triage,
    input="I need a refund for order #12345",
    max_turns=8,        # Financial safety: hard stop
    session=session     # Persist conversation
)

# HITL: if a tool needs approval
if result.interruptions:
    result.state.approve(result.interruptions[0])
    result = await Runner.run(triage, state=result.state, session=session)
```

### Google ADK: Workflow with HITL

```python
from google.adk import LlmAgent, SequentialAgent, ParallelAgent, LoopAgent

# Individual specialist agents
researcher = LlmAgent(
    name="researcher",
    model="gemini-flash-latest",
    instruction="Research the given topic using available tools.",
    tools=[web_search, doc_retriever]
)

analyst = LlmAgent(
    name="analyst",
    model="gemini-flash-latest",
    instruction="Analyze research findings and produce insights.",
    tools=[data_analyzer]
)

# Deterministic workflow composition
research_pipeline = SequentialAgent(
    name="pipeline",
    sub_agents=[researcher, analyst]  # Shared InvocationContext
)

# Parallel research with independent context
multi_source = ParallelAgent(
    name="multi_source",
    sub_agents=[web_researcher, paper_researcher, news_researcher]
    # WARNING: sub-agents share session.state -- use distinct keys!
)

# Iterative refinement with hard cap
refinement_loop = LoopAgent(
    name="refine",
    sub_agents=[drafter, critic],
    max_iterations=5  # ALWAYS set this -- LoopAgent won't stop on its own
)

# Session management with state scoping
from google.adk import InMemorySessionService  # Dev only
session_service = InMemorySessionService()

# Production: use VertexAiSessionService
# from google.adk import VertexAiSessionService
# session_service = VertexAiSessionService(
#     project="my-project", location="us-central1",
#     agent_engine_id="my-engine-id"
# )

# State key prefixes for scoping
# session.state["current_topic"]           -> this session only
# session.state["user:language"]           -> all sessions for this user
# session.state["app:api_endpoint"]        -> all users of the app
# session.state["temp:raw_response"]       -> this invocation only
```

### CrewAI: Flow with Crew Inside

```python
from crewai import Agent, Task, Crew, Flow, Process
from crewai.flow.flow import start, listen, router
from crewai.flow.persistence import persist

# Agents with roles
researcher = Agent(
    role="Senior Research Analyst",
    goal="Find comprehensive data on the topic",
    backstory="You have 15 years of research experience...",
    tools=[web_search, doc_analyzer],
    max_iter=10,  # Circuit breaker (default 20 is too generous)
    allow_delegation=False  # Prevent delegation loops
)

writer = Agent(
    role="Technical Writer",
    goal="Write clear, accurate reports",
    backstory="You specialize in technical communication...",
    tools=[],
    allow_delegation=False
)

# Tasks with dependencies
research_task = Task(
    description="Research {topic} thoroughly",
    expected_output="Detailed research findings",
    agent=researcher
)

write_task = Task(
    description="Write a report based on research findings",
    expected_output="Polished report",
    agent=writer,
    context=[research_task]  # Depends on research output
)

# Crew = team of agents working on tasks
research_crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    process=Process.sequential,  # Not hierarchical (avoids manager overhead)
    memory=True,  # Enable cross-run memory (costs embedding tokens)
    checkpoint=True  # Enable crash recovery
)

# Flow = event-driven backbone wrapping Crews
class ReportFlow(Flow):
    @start()
    @persist  # SQLite-based state persistence
    def gather_requirements(self):
        return {"topic": self.state.topic, "depth": "comprehensive"}
    
    @listen(gather_requirements)
    def run_research(self, requirements):
        result = research_crew.kickoff(inputs=requirements)
        return result
    
    @listen(run_research)
    def review_and_publish(self, research_output):
        # HITL gate before publishing
        return {"report": research_output, "status": "pending_review"}

# Resume from crash
flow = ReportFlow()
flow.kickoff(inputs={"topic": "Agent Architectures 2026"})
# After crash: flow.kickoff(inputs={"id": existing_flow_uuid})  # Resume
```

---

## 7. Common Pitfalls & Failure Modes

### LangGraph-Specific

- **Recursion limit exhaustion**: Default is 25 (was lower historically; newer sources say 1000 since v1.0.6 -- check your version). Complex graphs with conditional cycles hit this silently. Must tune per use case.
- **State merge conflicts**: Incorrect reducer logic causes silent state corruption. Two concurrent nodes updating the same key without a reducer -> `InvalidUpdateError` or last-write-wins data loss.
- **Checkpointer serialization**: Custom state objects that are not JSON-serializable crash the checkpointer. Need custom `SerializationProtocol`.
- **Cold start**: Graph compilation adds startup overhead. Complex graphs have measurable compile time.
- **Version migration**: Checkpoint format changes across versions (e.g., v0.2 renamed `thread_ts` to `checkpoint_id`). Old checkpoints require migration.
- **MemorySaver in production**: Dies with the process. HITL interrupts are lost. SQLite has write lock under concurrency. Use PostgresSaver with connection pool.
- **Send explosion**: Dynamic fan-out writing into a cycle -> unbounded worker spawning.

### OpenAI Agents SDK-Specific

- **Statelessness by default**: Without sessions configured, conversation history is lost between `Runner.run()` calls. Must explicitly manage via sessions or `to_input_list()`.
- **Handoff ping-pong**: Overlapping `handoffDescription` -> specialists bounce between each other. `MaxTurnsExceeded` at default 10.
- **Nested cost explosion**: Handoffs + as_tool each with 10 turns -> up to 100 model calls (worst case). Set `max_turns` per run.
- **WebSocket limits**: 60-minute connection limit. After reconnect, `store=False` runs cannot recover uncached `previous_response_id`.
- **Guardrail timing**: Parallel input guardrails may allow token consumption before tripwire fires. Use `run_in_parallel=False` for cost-sensitive scenarios.
- **Hosted tool gap**: Tool guardrails do not wrap hosted tools -> policy holes.
- **Streaming incomplete**: Not consuming `stream_events()` to completion -> session/approval bookkeeping incomplete.
- **Session backend atomicity**: MongoDB oversized batches fail atomically -- good for consistency but loses entire turn.

### Google ADK-Specific

- **LoopAgent infinite loop**: No `max_iterations` and no `exit_loop` = infinite. LoopAgent does not infer "good enough."
- **SequentialAgent bloat**: Quality collapse from bloated shared state as agents pass everything through.
- **ParallelAgent races**: Sub-agents share `session.state` -- same key = race condition.
- **Breaking version changes**: ADK 2.0 sessions incompatible with pre-1.28 versions.
- **Model lock-in risk**: Optimized for Gemini. Non-Gemini models may not support all features.
- **Context compression loss**: Automatic filtering can remove information that later proves relevant. No "pin" mechanism.
- **MCP statefulness**: Persistent MCP sockets vs Cloud Run scale-out -- restored agent without live MCP socket.
- **Quota limits**: 90 QPM Query/StreamQuery. Burst fan-out fails closed.
- **A2A stale cards**: Discovery via stale AgentCard URL; auth mismatch; executor `cancel` not implemented -> zombie tasks.

### CrewAI-Specific

- **Delegation loops**: `allow_delegation=True` -> agents delegate in circles (A -> B -> A). `max_iter=20` default may consume 20 LLM calls before stopping.
- **Hierarchical deadlock**: Manager delegates to agent A; A waits for human/webhook; manager blocks; no timeout -> AMP execution hung.
- **Context window overflow**: Despite `respect_context_window=True`, auto-summarization is lossy. Tasks requiring precise recall may fail silently.
- **Rate limiting**: Without `max_rpm`, concurrent agents exhaust API rate limits. No built-in backoff beyond `max_retry_limit=2`.
- **SQLite persistence**: Write concurrency limitations. Not suitable for high-throughput distributed deployments.
- **Memory cost surprise**: Default `text-embedding-3-large` + LLM extract on every task adds cost. Stale "facts" can prompt-inject future runs.
- **Flow persist fork bugs**: Combining `restore_from_state_id` + `from_checkpoint` raises `ValueError`.
- **Checkpoint `*`**: Disk fill; resume from mid-LLM checkpoint with new prompt = version skew.
- **Security in JSON configs**: `{"python": "module.attribute"}` references execute arbitrary code. Supply chain risk with untrusted crew definitions.
- **MCP secrets in source**: String URLs with API keys in `mcps=[...]` -> secrets in source code.

### Cross-Framework Issues

- **Non-idempotent tools + retry/resume**: Double refund, double email across all frameworks.
- **Unbounded context**: Quadratic cost (context re-billed each turn).
- **Eval/trace PII**: Training-data and GDPR incidents if thoughts/observations logged unredacted.
- **State format incompatibility**: Each framework uses proprietary serialization. No standard format means migration = rebuild.
- **Tool definition mismatch**: Despite MCP, tool schemas and invocation patterns differ. LangGraph tools are node-scoped; SDK tools are agent-scoped; CrewAI uses BaseTool inheritance.
- **Multi-framework A2A**: Schema/version skew of AgentCard / protocol versions.

---

## 8. Interview Questions & Answers

**Q1: Compare LangGraph and OpenAI Agents SDK. When would you choose each?**

They solve different problems. LangGraph is a typed graph runtime -- you define nodes, edges, reducers, and checkpoints. It gives you cycles, dynamic fan-out via `Send`, time-travel debugging, and durable HITL interrupts. Choose it when your workflow IS a state machine: complex conditional logic, parallel branches, multi-week approval waits, or when you need point-in-time recovery.

The Agents SDK is a role-based loop with handoffs. You define agents with instructions and tools; the Runner manages the ReAct-like loop. It is deliberately minimal -- "few enough primitives to learn quickly." Choose it when your product is a tool-using assistant, especially if you want OpenAI's hosted tools (web search, file search, code interpreter, hosted MCP) and integrated tracing/evals without building graph infrastructure.

The key difference: LangGraph gives you control at the graph level (you decide every edge), while the Agents SDK gives you control at the agent level (the model decides what to do within each agent, you decide when to hand off between agents). Framework choice affects latency and operational burden, not model accuracy -- the same Claude Sonnet achieves similar SWE-bench scores through either framework.

**Q2: How does Google ADK's context management differ from other frameworks?**

ADK is the only framework that makes context management a first-class architectural feature rather than an afterthought. While LangGraph concatenates messages and CrewAI's `respect_context_window=True` triggers lossy auto-summarization when you are already near the limit, ADK actively filters irrelevant events, summarizes older turns, lazy-loads artifacts, and tracks token usage. Their principle is "every token earns its place."

The trade-off: this adds hidden model calls for summarization that are not metered in the docs -- you need to budget extra Gemini Flash calls in your traces. And there is no mechanism to "pin" certain context as non-compressible, so information you need later might get filtered. But for context-intensive applications, ADK's approach can significantly reduce token waste compared to the naive "stuff everything in the window" approach other frameworks use.

**Q3: Explain the difference between MCP and A2A. Why do we need both?**

MCP (Model Context Protocol) is agent-to-tools. It is JSON-RPC for `tools/list` and `tools/call` -- think of it as "USB-C for AI applications." Every major framework supports it. When your agent needs to query a database, call an API, or read a file, it uses MCP.

A2A (Agent-to-Agent Protocol) is agent-to-agent. It handles Agent Card discovery, task lifecycle, messages, artifacts, and streaming. It is for when agents from different trust domains, vendors, or languages need to communicate as opaque peers. A2A deliberately does not share memory, tools, or weights between agents.

We need both because they solve different problems. MCP is vertical (agent reaches down to tools); A2A is horizontal (agent reaches across to peer agents). In a Google enterprise pattern: ADK orchestrator on Cloud Run uses MCP servers as anti-corruption layers to backends, and A2A for remote agents built by different teams or vendors. ADK and CrewAI have first-class A2A support; LangGraph and Agents SDK support it indirectly by wrapping agents in the A2A protocol.

**Q4: What are the durability/persistence trade-offs between frameworks?**

LangGraph has the strongest persistence model: checkpoint-based with super-step granularity, time travel to any historical state, thread forking, and a Temporal plugin (public preview) for true durable execution. The weakness: checkpointing alone is not durable execution -- no automatic failure detection, no watchdog. You need to compose LangGraph inside Temporal for infrastructure-level resilience.

OpenAI Agents SDK is session-based: retrieve history before run, persist after. Multiple backends (Redis, Postgres, MongoDB, Dapr). `RunState` serialization supports HITL interruption/resume. But it is NOT Temporal -- process crash without saved state = lost in-flight turn. GA Temporal integration since March 2026 fills this gap.

ADK has managed Sessions on Agent Platform with rewind/migration, plus Memory Bank for cross-session knowledge. No first-party Temporal-equivalent -- you use Cloud Tasks or Workflows around the Runner.

CrewAI has dual-layer persistence: Flow-level `@persist` (SQLite-based with resume and fork modes) and Crew-level checkpointing (early release, APIs may change). Not a durable execution framework; wrap Flow steps yourself for retries.

Bottom line: for regulated HITL that might take weeks, LangGraph + Temporal or ADK on GCP. For standard chatbot persistence, any framework with a proper session backend works.

**Q5: How do guardrails differ across frameworks?**

OpenAI Agents SDK has the most structured guardrail system: three tiers (input, output, tool-level). Input guardrails can run in parallel with agent execution (fail-fast, but possible wasted tokens) or blocking. Output guardrails validate final results. Tool guardrails wrap individual function tools. Tripwire mechanism halts execution on violation. Important gap: tool guardrails do NOT wrap hosted tools, handoffs, or Agent.as_tool -- so hosted MCP tools bypass your validation pipeline.

LangGraph has no built-in guardrail system -- you implement validation within node logic. This is both its weakness (more work) and strength (no gaps in coverage).

CrewAI has task-level guardrails (function-based or LLM-based) with sequential chain execution and configurable max retries. Plus `allow_delegation=False` to prevent delegation loops.

ADK has tool-level callbacks (`before_tool_callback`, `after_tool_callback`) and action confirmation for safety. `RequireConfirmation: true` creates HITL gates.

For production: I would layer a gateway-level guardrail (content filter, rate limiter) on top of whatever framework-level guardrails exist, because no single framework covers every tool invocation path.

**Q6: Design a multi-framework agent system where a CrewAI research team feeds results to a LangGraph analysis pipeline.**

I would use A2A as the contract between them. The CrewAI research crew would expose itself as an A2A server using `A2AServerConfig`. The LangGraph pipeline would consume it via an A2A client wrapped as a node. Key decisions:

1. Do NOT share checkpointers. Each framework manages its own state. The A2A protocol handles the handoff.
2. AgentCard URLs + OIDC/mTLS for auth between services. Version the AgentCards.
3. MCP only for tools (database, APIs), not for agent-to-agent communication.
4. Cost cap on the CrewAI crew side (max_iter, max_rpm) because the LangGraph pipeline cannot control the Crew's internal costs.
5. Timeout on the A2A task from the LangGraph side so a hung Crew does not block the pipeline.

This works because A2A is opaque: the LangGraph pipeline does not need to know that the research service uses CrewAI internally. It just sends a task and gets results back.

**Q7: What is `max_turns=10` vs `recursion_limit=25` and why does it matter for cost?**

They measure different units. In OpenAI Agents SDK, a "turn" is one model invocation including any tool calls with it. Default 10 turns means at most 10 model calls. In LangGraph, a "superstep" is one round of the Pregel execution model where all scheduled nodes run, then reducers merge, then checkpoint. A typical ReAct tool cycle takes 2 supersteps (model node + tool node), so `recursion_limit=25` is roughly 12 tool rounds.

The cost implication: a default-25 LangGraph graph can cost 2.5x more per run than a default-10 Agents SDK runner on the same task, just from the higher iteration cap. With a mid-tier model, the difference is $87/1k runs vs $36/1k runs. This is why `max_turns` is a financial control, not just a correctness fuse.

**Q8: How would you handle a production deployment needing 10k concurrent agent sessions?**

First, the persistence layer: PostgresSaver (LangGraph) or Redis sessions (Agents SDK) or Agent Runtime (ADK). Never SQLite or in-memory at this scale. Capacity math: 10k sessions x 2 supersteps/turn x 4 turns/min = ~80k writes/min to the checkpoint store. Postgres handles this; add TTL to prevent unbounded growth.

Second, token throughput: 10k agents x 8k prefix per turn x 4 turns/min = 320M TPM if uncached. That exceeds OpenAI T5 limits (40M TPM for sol). Prompt caching is a capacity feature, not just a cost feature: 90% cache hit drops uncached to ~32M TPM.

Third, compute: Agent Server dedicated workers (LangGraph) or Agent Runtime (ADK) scale independently from API pods. Fan-out cap hard-coded at max_workers=8 per orchestrator.

Fourth, cost control: per-task and per-hour token budgets enforced at the platform level. Model routing (luna/Flash for 70% easy, terra/Pro for 30% hard) for 40-70% cost reduction. Alert on cost per successful outcome.

**Q9: What are the anti-patterns when using agent frameworks?**

Eight anti-patterns I have seen or would warn against:

1. LangGraph without a durable checkpointer in production + HITL = lost interrupts on restart.
2. Agents SDK handoffs AND as_tool AND a third graph framework for one product surface = unnecessary complexity.
3. ADK LoopAgent as "until quality is good" with no max_iterations = infinite loop.
4. CrewAI Process.hierarchical as the only control plane = use Flow as the outer app, Crew for autonomous islands.
5. Shared MCP PAT (Personal Access Token) in graph state or crew YAML = credential leak.
6. Mixing old pricing eras (LangSmith node-era vs LCU-era, Agent Engine per-event vs GiB metering) in the same budget model.
7. Assuming Agent Platform idle is free AND Dedicated LangSmith DB uptime is free = they have opposite billing shapes.
8. Using AutoGen for new projects = it is in maintenance mode. Migrate to MAF.

**Q10: Compare the memory systems across frameworks.**

LangGraph separates checkpointers (thread-scoped, short-term, within a conversation) from Stores (cross-thread, long-term, key-value with optional semantic search). This is the cleanest separation. You choose your own embedding model and storage backend.

ADK has Memory Bank (cross-session, topic-based memory using an ACL 2025 method). It is managed on Agent Platform with IAM controls. The distinction from sessions is clear but the retrieval cost ($0.50/1k memories on the older SKU) can exceed runtime cost.

CrewAI has unified Memory (one class, LLM infers scope/categories/importance). Default embedder is OpenAI text-embedding-3-large (not free). After each task: extract facts; before each task: inject recall. Persistence via LanceDB on disk. The risk: stale "facts" from old tasks can prompt-inject future runs. Scope memory per user.

Agents SDK has no built-in memory beyond sessions. Use OpenAI Conversations API for server-managed storage or build your own.

For production: I would start with the simplest memory that works (usually just session history) and add cross-run memory only when evals show it improves outcomes. Memory systems add cost (embedding tokens), latency (retrieval), and risk (stale/incorrect facts) that often outweigh the benefit.

**Q11: How do you migrate between frameworks?**

There is no standardized migration path. Each framework uses proprietary state serialization and different tool schemas. The recommended approach:

1. Extract tool definitions into MCP servers (framework-agnostic). This is the most valuable step because MCP tools work with every framework.
2. Document agent behaviors as specifications (role, capabilities, constraints, tools).
3. Rebuild orchestration logic in the target framework. Translating a LangGraph graph to OpenAI handoffs requires redesigning coordination logic, not porting code.
4. Migrate state by replaying conversations through the new system. There is no checkpoint format converter.

Specific migrations: AutoGen to MAF has an official guide (patterns map). LangChain Agents to LangGraph is incremental (already built on LangGraph under the hood).

**Q12: What is the total cost picture when choosing a framework?**

Framework cost = model tokens + platform SKU + operational overhead.

Model tokens are identical across frameworks (same API, same prices). The differences are:
- Scaffolding tokens: CrewAI highest (role/goal/backstory per agent, ~200-500 tokens each). LangGraph lowest (developer-controlled).
- Extra LLM calls: CrewAI memory extract + embed per task. Agents SDK guardrail calls. ADK hidden context summarization calls.
- Platform: LangSmith Plus $39/seat/mo + LCU/LSU. Agent Platform first 50 vCPU-h free then $0.085/vCPU-h. CrewAI AMP Basic free (50 execs/mo).
- Operational: self-hosting LangGraph + Postgres + Temporal is most control but most burden. Managed platforms (Agent Runtime, AMP, LangSmith Cloud) reduce ops but add cost and lock-in.

For a 1k-conversations/day support bot using model routing (70% luna, 25% terra 3-turn, 5% terra 10-turn), model cost is roughly $15-20/day. Platform adds $2-5/day. The dominant cost lever is max_turns and cache hit rate, not framework choice.

---

## 9. Key Numbers to Memorize

| Metric | Value | Context |
| --- | --- | --- |
| LangGraph GitHub stars | 40.1k | v1.2.11 (Aug 2026) |
| OpenAI Agents SDK stars | 28.8k | v0.22.0 (Aug 2026) |
| Google ADK stars | 21.2k | v2.7.1 (Aug 2026) |
| CrewAI stars | 57.4k | v1.15.17 (Aug 2026) |
| MS Agent Framework stars | 13.0k | v1.14.0 (Aug 2026) |
| Agents SDK default max_turns | 10 | `MaxTurnsExceeded` error |
| LangGraph recursion_limit | 25 supersteps (check version; may be 1000 since v1.0.6) | `GraphRecursionError` |
| CrewAI default max_iter | 20 | Per agent |
| ADK LoopAgent | No default -- you MUST set max_iterations | Infinite without it |
| LangGraph 2 supersteps = 1 tool round | ~12 tool rounds at limit 25 | Convert between frameworks |
| LangSmith Plus | $39/seat/month | + LCU/LSU consumption |
| Agent Platform Runtime | $0.085/vCPU-hr (50 free) | Plus session/memory storage |
| CrewAI AMP Basic | 50 free executions/month | Enterprise: custom |
| Postgres checkpoint write | ~5-15ms, ~3-8ms pooled | Field reports, not SLO |
| Temporal history limit | 10,240 events warn / 51,200 terminate | Continue-As-New to reset |
| gpt-5.6-luna reference cost | ~$6/1k executions (4-call skeleton) | Cheapest viable |
| gpt-4.1 reference cost | ~$50/1k executions | Mid-tier |
| Prompt caching savings | 40-80% | When stable prefix dominates |
| Model routing savings | 40-70% | Cheap 70% / frontier 30% |
| OpenAI web search | $10/1k calls | Separate from model tokens |
| CrewAI agent scaffolding | ~200-500 tokens per agent | role + goal + backstory |

---

## 10. Quick Reference

### Framework Decision Tree

```
Need typed cyclic graphs, time-travel, map-reduce, multi-week HITL?
  YES -> LangGraph (+Temporal for durability, +LangSmith for enterprise)

Need lightweight tool-using assistant with hosted tools and traces?
  YES -> OpenAI Agents SDK (+Redis/Postgres sessions for prod)

Need GCP IAM/CMEK/VPC-SC, A2A mesh, Memory Bank, multi-language?
  YES -> Google ADK + Agent Platform

Need role-team metaphor, cross-run memory, managed deploy?
  YES -> CrewAI + AMP (use Flow as outer app, Crew for autonomous work)

Need .NET + Python consistency, Azure, migrating from AutoGen?
  YES -> Microsoft Agent Framework
```

### Capability Matrix (Architect View)

| Dimension | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- |
| Orchestration metaphor | Typed graph (cyclic DAG + Send) | Role loop + handoff/as_tool | Agent tree + templates + graphs | Roles (Crew) inside events (Flow) |
| Typed state | Best (reducers, channels) | Outputs typed; graph state DIY | Session dict + graph payloads | Flow state + pydantic tasks |
| Durability | Best OSS checkpoint + Temporal | Session + RunState; you operate | Managed Sessions/Memory Bank | Flow persist + early checkpoints |
| HITL | interrupt/Command; durable on platform | Approval interruptions | RequestInput + tool confirm | Decorator + webhooks + AMP |
| Multi-agent | Subgraphs, Send fan-out | Handoff vs manager | Native + A2A | Crew hierarchical/sequential |
| MCP | Consume + serve `/mcp` | Hosted + local; richest hosted tools | Consume + serve; API Registry | Consume + AMP export |
| A2A | Indirect | Indirect | **Native** | **Native** |
| Enterprise SSO/RBAC | LangSmith Enterprise | Your IdP | GCP IAM | AMP Enterprise |
| License | MIT + paid platform | MIT + API | Apache-2.0 + GCP | MIT + paid AMP |
| Languages | Python (primary), JS | Python + JS/TS | Py/TS/Go/Java/Kotlin | Python |

### Production Checklist (All Frameworks)

- [ ] Set hard iteration/turn limits (never unbounded)
- [ ] Use durable persistence backend (Postgres/Redis, not in-memory)
- [ ] Configure HITL gates on irreversible actions
- [ ] Set per-task token/cost budgets
- [ ] Enable tracing with PII redaction
- [ ] Map traces to user identity for compliance
- [ ] Make tools idempotent (retry/resume will re-execute)
- [ ] Test reducer logic for parallel state merges (LangGraph)
- [ ] Set `allow_delegation=False` on workers (CrewAI)
- [ ] Consume stream events to completion (Agents SDK)
- [ ] Set `max_iterations` on LoopAgent (ADK)
- [ ] Put MCP credentials in env/vault, never in code

### Enterprise Deployment Patterns

| Pattern | Stack | Best For |
| --- | --- | --- |
| Managed cloud | ADK -> Agent Runtime; CrewAI -> AMP; MAF -> Foundry | Least ops overhead |
| Self-hosted durable | LangGraph + Postgres + Temporal | Most control, most burden |
| Serverless | Agents SDK + Dapr + cloud functions | Bursty workloads |
| Hybrid cloud | MAF + Azure + on-premise | Regulated data residency |
| Multi-framework mesh | A2A between frameworks; MCP for tools | Best-of-breed per workload |

### Migration Cheat Sheet

1. Extract tools into MCP servers (framework-agnostic -- highest ROI step)
2. Document agent behaviors as specs (role, capabilities, constraints)
3. Rebuild orchestration in target framework (no code port -- redesign)
4. Replay conversations through new system (no checkpoint converter exists)
5. AutoGen -> MAF: official Microsoft migration guide available
6. LangChain Agents -> LangGraph: incremental (already built on LangGraph)
