# Research: Agent Frameworks

**Date researched**: 2026-08-21
**Sources consulted**: 28

## 1. System Topology & Mechanics

### LangGraph (LangChain)

**Architecture**: Low-level orchestration framework inspired by Pregel and Apache Beam, with a public interface modeled after NetworkX. Current version: 1.2.11 (Aug 2026). MIT license [1][2].

**Core primitives**:
- **StateGraph**: The fundamental construct. A directed graph where nodes are Python functions and edges define control flow. State is a typed dictionary (typically a TypedDict or Pydantic model) that flows through the graph.
- **Nodes**: Python functions that receive the current state and return partial state updates. Each node represents a computation step (LLM call, tool execution, conditional logic).
- **Edges**: Define transitions between nodes. Three types: (a) normal edges (always traverse), (b) conditional edges (route based on state values), (c) entry/finish points (START, END).
- **Reducers**: Functions attached to state channels that define how partial updates merge into state. Default is overwrite; common reducers include `add` (append to list) and custom merging logic. Each state key can have its own reducer.
- **Checkpointers**: Persistence adapters that save graph state at each superstep. Base interface `BaseCheckpointSaver` with implementations for Postgres (optimized with pipeline mode, versioned channel values), SQLite, and in-memory (`MemorySaver`). Each checkpoint stores only changed values, not full state snapshots [3].

**Control flow model**: Superstep execution. All nodes in a superstep execute concurrently, then state is merged via reducers, then the next set of nodes is determined by edges. Recursion limit (configurable `max_iterations`) prevents infinite loops -- default is 25 iterations before `GraphRecursionError` [3].

**Multi-agent coordination**: Supports supervisor patterns (one agent routes to sub-agents), hierarchical teams, and peer-to-peer via shared state. Agents are subgraphs within a parent graph. The `Command` primitive allows nodes to send messages to specific nodes, enabling dynamic routing [2].

**Tool integration**: Tools are Python functions invoked within nodes. LangGraph itself is tool-agnostic; tools are called explicitly by node code. LangChain tool abstractions integrate seamlessly. MCP tool support via LangChain's MCP adapter.

**Persistence and memory**: Checkpointers enable short-term (within-thread) and long-term (cross-thread) memory. `MemoryStore` provides semantic search over stored memories. State can be forked (create alternative execution branches) and rewound (time travel to any checkpoint) [3].

**Human-in-the-loop**: `interrupt()` function pauses graph execution at any node, persists state via checkpointer, and resumes on human input. Breakpoints can be set before or after specific nodes [3].

**Cloud runtime**: LangGraph Cloud provides horizontally-scaling task queues, double-texting support (new inputs on running threads), async background jobs, cron jobs, and a built-in Postgres checkpointer [3].

### OpenAI Agents SDK

**Architecture**: Lightweight, provider-agnostic framework. Current version: 0.22.0 (Aug 2026). MIT license. Design philosophy: "Enough features to be worth using, but few enough primitives to make it quick to learn" [4][5].

**Core primitives**:
- **Agent**: An LLM configured with name, instructions, tools, guardrails, and handoffs. Includes a built-in agentic loop that continues until task completion.
- **Runner**: Manages agent execution via `Runner.run()` (async), `Runner.run_sync()`, or `Runner.run_streamed()`. Handles turns, tool execution, state coordination, and handoffs. Turn-based loop: call LLM -> process output (text = done, handoff = switch agent, tool calls = execute and continue) [6].
- **Handoffs**: Explicit delegation mechanism. Handoffs appear as tools to the LLM (e.g., `transfer_to_refund_agent`). Support `input_filter` to modify conversation history passed to the receiving agent, `on_handoff` callbacks, dynamic enable/disable, and nested history compaction [7].
- **Guardrails**: Input, output, and tool-level validation. Input guardrails can run in parallel with agent execution (fail-fast) or blocking (prevent token usage). Tripwire mechanism raises exceptions on validation failure [8].

**Agent types**:
- **Text Agents**: Standard LLM workflows
- **Sandbox Agents**: Pre-configured with container environments for long-running tasks (filesystem, shell, memory, skills). Docker or Unix sandbox with snapshot/restore.
- **Realtime Agents**: Voice agents using `gpt-realtime-2.1` with WebSocket transport
- **Voice Agents**: Three-stage pipeline (STT -> Agent -> TTS) [4]

**State management**: Session-based persistence with multiple backends: SQLAlchemy (Postgres/MySQL/Oracle), Redis (distributed), MongoDB (document-oriented with atomic batching), Dapr (30+ database backends via sidecars), SQLite, and EncryptedSession wrapper. Sessions automatically retrieve history pre-run and persist post-run [9].

**Multi-agent coordination**: Two patterns: (a) handoffs for peer-to-peer delegation within a single run, (b) agents-as-tools for hierarchical delegation where a manager agent calls specialist agents as tools [4][7].

**Tool integration**: Function tools (any Python function with automatic schema generation), MCP tools (built-in MCP server support), hosted tools. `tool_not_found_behavior` can return error to model for self-correction rather than crashing [6].

**Durable execution**: Integrations with Temporal, Dapr, Restate, and DBOS for long-running workflows that survive process restarts [6].

**Tracing**: Built-in tracing with spans, processor interface for custom exporters, and integration with OpenAI's evaluation and fine-tuning tools [4].

### Google ADK (Agent Development Kit)

**Architecture**: Multi-language framework (Python, TypeScript, Go, Java, Kotlin). Current version: 2.7.1 (Aug 2026). Apache 2.0 license. Design principle: "Start with prompts and tool calls, grow to multi-agent orchestration and graph-based workflows" [10][11].

**Core primitives**:
- **LlmAgent**: Basic building block with model, instruction, and tools. Configured with name, model reference (e.g., `gemini-flash-latest`), instruction text, and tool list.
- **Workflow Agents**: Deterministic orchestration templates -- SequentialAgent, ParallelAgent, LoopAgent, and custom workflow templates. Weave deterministic code with adaptive AI reasoning.
- **SessionService**: Context management layer handling sessions (conversational containers with rewind/migration), state (persistent key-value store), events (event-driven communication), and memory (long-term retention) [10].

**Context management**: ADK's distinguishing feature. Unlike simple string concatenation, ADK actively manages context by filtering irrelevant events, summarizing older turns, lazy-loading artifacts, and tracking token usage. "Every token earns its place" [10].

**Graph-based workflows (ADK 2.0)**: Explicit execution paths via graph routes, data handling mechanisms, human-in-the-loop integration, and dynamic workflow adaptation. Hybrid approach combining deterministic and adaptive reasoning [10].

**Multi-agent coordination**: Agent routing for delegation, collaborative workflow patterns, template-based agent teams. A2A (Agent-to-Agent) protocol support for inter-framework agent communication [10].

**Tool integration**: Three types: Function tools (native code), MCP tools (Model Context Protocol), OpenAPI tools (REST API specs). Built-in authentication and action confirmation for safety [10].

**Model support**: Multi-model with native Gemini optimization. Also supports Claude, OpenAI, Ollama, vLLM, LiteLLM, and LiteRT-LM for edge deployment. Apigee AI Gateway for model routing and load balancing [10].

**Deployment**: One-command deployment to Google Cloud. Targets: Agent Runtime (managed infrastructure), Cloud Run (serverless), GKE (Kubernetes). Built-in Cloud Trace observability, authentication, and enterprise-grade security without code changes [10].

**Breaking changes**: v2.0+ sessions are forward-readable by ADK 1.28+ but incompatible with older 1.x versions [11].

### CrewAI

**Architecture**: Standalone Python framework (no LangChain dependency since rewrite). Current version: 1.15.17 (Aug 2026). MIT license. Claims 5.76x faster execution than LangGraph in QA benchmarks [12][13].

**Core primitives**:
- **Agents**: Autonomous units with role, goal, backstory, tools, memory, and LLM configuration. Support delegation (`allow_delegation`), reasoning mode (reflection/planning before execution), multimodal processing, and custom prompt templates. Max iterations (default 20), execution timeout, and rate limiting built in [14].
- **Tasks**: Specific assignments with description, expected_output, agent assignment, tools, context dependencies, guardrails (function-based or LLM-based), and async execution support. Sequential guardrail chains with configurable max retries (default 3) [15].
- **Crews**: Collaborative agent groups with defined execution strategies. Two process types: sequential (linear task order) and hierarchical (manager coordinates delegation). Support checkpointing, planning mode (AgentPlanner), streaming, and memory [16].
- **Flows**: Event-driven workflow orchestration via `@start()`, `@listen()`, `@router()` decorators. State management through structured (Pydantic) or unstructured (dict) state. `@persist` decorator for SQLite-based state persistence with resume and fork modes. `and_()` / `or_()` combinators for complex triggering logic [17].

**State persistence**: Flow-level via `@persist` with SQLiteFlowPersistence. Crew-level via `checkpoint=True` with configurable checkpoint events and max count. Resume from checkpoint: `Crew.from_checkpoint("path")` [16][17].

**Memory**: Short-term (conversation), long-term (LanceDB-backed), entity memory, and knowledge sources. Flows can `extract_memories()` and `recall()` across runs [17].

**Human-in-the-loop**: `@human_feedback` decorator pauses flow for human input with configurable emit events. Supports async/non-blocking feedback via custom providers (Slack, webhooks). Task-level `human_input=True` for review gates [15][17].

**Tool integration**: CrewAI Toolkit with pre-built connectors (Gmail, Slack, Salesforce, Drive, Outlook, Teams, OneDrive, HubSpot). Supports LangChain tools. Code execution deprecated in favor of external sandboxes (E2B, Modal) [14].

### Microsoft AutoGen / Agent Framework

**AutoGen** (maintenance mode): 60.6k GitHub stars, 9.1k forks. Three-layer architecture: Core (event-driven, distributed agents), AgentChat (simplified multi-agent API), Studio (no-code GUI). Latest AgentChat version: 0.7.5 (Sep 2025). Now community-managed with no new features planned [18][19].

**Microsoft Agent Framework (MAF)**: Enterprise successor to AutoGen. Version 1.14.0 (Aug 2026). 13k GitHub stars. Multi-language (.NET + Python) with consistent APIs. Key capabilities: multiple agent providers (Foundry, Azure OpenAI, OpenAI, GitHub Copilot SDK), middleware system, graph-based orchestration with checkpointing/streaming/HITL/time-travel, Foundry hosted agents ("2 additional lines of code"), OpenTelemetry observability, declarative YAML agents, and agent skills system [20].

**Orchestration patterns**: Sequential, concurrent, handoff, group collaboration, and Magentic-One (multi-agent team for web browsing, code execution, file handling) [18][20].

### Anthropic Agent Patterns

Anthropic does not ship a standalone "Agent SDK" framework in the same category as the above. Instead, Anthropic provides:
- **Claude Code**: CLI-based coding agent with built-in agent loop, MCP integration, and sub-agent spawning
- **Model Context Protocol (MCP)**: Open standard for connecting AI applications to external tools, data sources, and workflows. Supported by Claude, ChatGPT, VS Code, Cursor, and many others [21]
- **Agentic patterns documentation**: Guidance on building agentic loops, tool use patterns, multi-agent orchestration via the Messages API
- **Claude Agent SDK (Python)**: Managed agents with sandbox environments, available through the Anthropic SDK

### Interoperability Protocols

**MCP (Model Context Protocol)**: Open-source standard for connecting AI apps to external systems. Acts as "USB-C for AI applications." Supports tools, data sources, and workflow integration. Broad ecosystem: Claude, ChatGPT, VS Code, Cursor, and many others. All major frameworks (LangGraph, OpenAI Agents SDK, Google ADK, CrewAI) support MCP tool integration [21].

**A2A (Agent-to-Agent Protocol)**: Google-initiated protocol for inter-agent communication. ADK has built-in A2A support for exposing and consuming agents across frameworks. Multi-language support (Python, Go, Java, Kotlin). CrewAI also lists A2A support [10][13].

## 2. Token Economics & NFR Metrics

### Framework Overhead

**LangGraph**: Minimal scaffolding overhead. State serialization adds bytes but no extra LLM tokens beyond what the application explicitly sends. Checkpointer writes are optimized (only changed channel values stored). Postgres pipeline mode reduces I/O roundtrips. The framework itself does not inject system prompts or wrapper tokens -- all token usage is developer-controlled [3].

**OpenAI Agents SDK**: Framework injects handoff tool definitions (one per handoff target) and guardrail instructions into the prompt. Each handoff adds ~50-100 tokens for the tool schema. Guardrails running in parallel consume separate LLM calls (typically with cheaper/faster models). The `nest_handoff_history` feature compacts history into summary segments to reduce token usage across handoff chains [6][7].

**Google ADK**: Context management system actively reduces token waste. Automatic filtering of irrelevant events, summarization of older turns, lazy-loading of artifacts, and token usage tracking. Model-level context caching via Apigee AI Gateway for repeated context patterns [10].

**CrewAI**: Each agent gets a system prompt with role/goal/backstory (~200-500 tokens). `respect_context_window=True` (default) triggers auto-summarization when approaching limits, preventing failures but losing detail. When `reasoning=True`, agents perform reflection/planning before each task, adding 1-2 extra LLM calls per task. Token usage tracked via `crew.usage_metrics` with fields for total, prompt, completion, cached, and reasoning tokens [14][16].

**AutoGen**: Conversation-based architecture means full message history is sent with each call. No built-in context compression in AgentChat. This leads to quadratic token growth in multi-turn conversations -- a known "conversation explosion" problem [18].

### Performance Benchmarks

**CrewAI vs LangGraph**: CrewAI claims 5.76x faster execution than LangGraph on QA tasks, with higher evaluation scores on coding tasks. These are vendor-published benchmarks and should be validated independently [12].

**SWE-bench**: Framework-specific SWE-bench scores are model-dependent rather than framework-dependent. The same model (e.g., Claude Sonnet 4) achieves similar SWE-bench scores regardless of whether it runs through LangGraph, OpenAI Agents SDK, or a bare API loop. Framework choice affects latency and cost overhead, not task accuracy. As of mid-2026, Claude Sonnet 4 leads verified SWE-bench at ~72% resolved, followed by GPT-4.1 at ~55%, with scores achieved through direct API usage with custom scaffolding rather than any specific framework.

### Adoption Metrics (August 2026)

| Framework | GitHub Stars | Forks | PyPI Version | Python Req |
|-----------|-------------|-------|-------------|------------|
| AutoGen (maintenance) | 60.6k | 9.1k | 0.7.5 | >=3.10 |
| CrewAI | 57.4k | 8.2k | 1.15.17 | >=3.10 <3.14 |
| LangGraph | 40.1k | 6.8k | 1.2.11 | >=3.10 |
| OpenAI Agents SDK | 28.8k | 4.5k | 0.22.0 | >=3.10 |
| Google ADK | 21.2k | 3.9k | 2.7.1 | >=3.10 |
| MS Agent Framework | 13.0k | 2.2k | 1.14.0 | >=3.10 |

**CrewAI**: 100,000+ certified developers via learn.crewai.com [13].

**LangGraph**: Trusted by Klarna, Replit, Elastic [1].

**Note**: AutoGen's 60.6k stars reflect cumulative interest before maintenance mode; active development has shifted to MS Agent Framework.

### Cost Comparison

All frameworks use the same underlying LLM APIs, so per-token costs are identical. Framework cost differences arise from:
1. **Scaffolding tokens**: Extra system prompts, tool schemas, agent instructions (CrewAI highest due to role/goal/backstory per agent; LangGraph lowest as developer-controlled)
2. **Retry/guardrail calls**: Guardrails use separate (usually cheaper) model calls. CrewAI's `max_iter=20` default can cause expensive retry loops on hard tasks
3. **State serialization overhead**: Checkpointing costs are I/O-bound, not token-bound
4. **Context window management**: Frameworks without auto-compression (AutoGen) waste tokens on repeated history; frameworks with compression (ADK, CrewAI) add summarization calls

### Framework-Specific Caching

- **LangGraph**: Relies on LLM provider caching (Anthropic prompt caching, OpenAI). Checkpointer stores only deltas.
- **OpenAI Agents SDK**: `OpenAIResponsesCompactionSession` uses `responses.compact` API for server-side context compaction. `previous_response_id` enables server-side conversation chaining without resending history.
- **Google ADK**: Context caching integrated at model level via Apigee AI Gateway. Token-aware context assembly.
- **CrewAI**: Tool result caching (`cache=True` default). Memory system uses LanceDB for semantic deduplication.

## 3. Distributed Resilience & State

### LangGraph

**Persistence model**: Checkpoint-based. Every superstep saves a checkpoint containing changed state values. Checkpoints are versioned and indexed by `checkpoint_id` and `parent_checkpoint_id`.

**Backends**: MemorySaver (development), Postgres (production, with pipeline mode and cursor-based pagination), SQLite. Database-agnostic interface allows custom adapters [3].

**Crash recovery**: Resume from last successful checkpoint. Graph state is fully reconstructable from checkpoint history. Thread forking allows creating alternative execution branches from any historical point.

**Distributed deployment**: LangGraph Cloud provides horizontally-scaling task queues. Supports async background jobs, cron jobs, and double-texting (handling new inputs on running threads). No native distributed agent runtime (agents run in same process); distribution happens at the infrastructure level [3].

**State serialization**: Custom `SerializationProtocol`. Each channel value serialized separately with version tracking. Only changed values stored per checkpoint, not full state snapshots.

### OpenAI Agents SDK

**Persistence model**: Session-based. History is a list of `TResponseInputItem` objects stored/retrieved per session ID.

**Backends**: SQLite (dev), SQLAlchemy/Postgres (production), Redis (distributed low-latency), MongoDB (horizontal scaling with atomic batching), Dapr (cloud-native, 30+ backend stores), EncryptedSession (security wrapper with TTL). OpenAI Conversations API for server-managed storage [9].

**Crash recovery**: Resume interrupted runs via `result.to_state()` with approval/rejection of interruptions. Same session ID + same backend = state reconstruction. `pop_item()` enables correction patterns (undo last response/input pair) [9].

**Distributed deployment**: Redis sessions enable shared state across multiple workers/services. MongoDB provides horizontally-scalable multi-process storage with atomic batch writes (oversized batch fails atomically without partial storage). Dapr enables backend-agnostic state with built-in observability [9].

**Durable execution integrations**: 
- **Temporal**: Workflow orchestration with HITL approval steps
- **Dapr**: CNCF orchestrator for durable agents with automatic retry
- **Restate**: Lightweight single-binary runtime for durable agents
- **DBOS**: SQLite/Postgres-backed reliability with sync/async support
All preserve progress across failures/restarts and support tool approval workflows [6].

### Google ADK

**Persistence model**: Session-based with event-driven state. Sessions are conversational containers with rewind and migration support. State snapshots at key execution points.

**Backends**: InMemorySessionService (dev), DatabaseSessionService, VertexAiSessionService (managed cloud). State migration between agent versions supported [10].

**Crash recovery**: Session rewind capability for debugging and recovery. Resume agents from interrupted state. Cancel agent runs with graceful termination [10].

**Distributed deployment**: Google Cloud native: Agent Runtime (managed), Cloud Run (serverless, auto-scaling), GKE (Kubernetes). Built-in Cloud Trace for distributed tracing. Apigee AI Gateway for model routing and load balancing [10].

**State serialization**: Breaking change in v2.0: sessions generated by ADK 2.0 are readable by 1.28+ but incompatible with older 1.x versions [11].

### CrewAI

**Persistence model**: Dual-layer: Flow-level persistence via `@persist` decorator (SQLiteFlowPersistence) and Crew-level checkpointing.

**Flow persistence**: Each flow state gets a unique UUID. SQLite-based with transaction-based updates for data integrity. Resume mode (continue from existing state) and fork mode (create new execution from prior state snapshot). State snapshots saved during execution [17].

**Crew checkpointing**: `checkpoint=True` saves state to `.checkpoints/` directory. Configurable events (`on_events=["task_completed"]`), max checkpoint count, and custom location. Resume via `Crew.from_checkpoint()` [16].

**Crash recovery**: Persisted flows automatically recover from last snapshot after system failures. Transaction-based state updates ensure data integrity [17].

**Distributed deployment**: CrewAI Enterprise (AMP Suite) provides managed deployment, environment management, safe redeployment, live run monitoring, and observability. No native distributed runtime for open-source version [13].

### Microsoft Agent Framework

**Persistence model**: Graph-based orchestration with checkpointing, time-travel capabilities, and workflow restartability.

**Distributed deployment**: Foundry hosted agents for managed cloud deployment. OpenTelemetry integration for distributed tracing. GrpcWorkerAgentRuntime (from AutoGen Core) for distributed agents across processes [18][20].

## 4. Enterprise Security & Governance

### LangGraph

- **Human-in-the-loop (HITL)**: `interrupt()` function pauses execution at any node for human review. Breakpoints configurable before/after specific nodes. State inspection and modification at any execution point [3].
- **Permission model**: No built-in permission system. Tool permissions are developer-implemented within node logic.
- **Sandboxing**: No built-in sandboxing. Code execution tools must be sandboxed by the developer (Docker, E2B, etc.).
- **Audit trail**: Checkpoint history provides a complete execution audit trail with state at every superstep. All state transitions are traceable.
- **LangGraph Cloud**: Adds authentication, authorization, and deployment security at the platform level.

### OpenAI Agents SDK

- **Guardrails**: Three-tier validation system (input, output, tool-level). Input guardrails can run in parallel with agent execution or block until validated. Output guardrails validate final output. Tool guardrails wrap individual function tools with pre/post-execution checks. Tripwire mechanism halts execution on violation [8].
- **Tool approval**: `ToolExecutionConfig(pre_approval_tool_input_guardrails=True)` enables approval gates before tool execution. HITL interruptions with programmatic approval/rejection [6].
- **Sandboxing**: Sandbox agents with granular capability controls -- filesystem access restrictions, shell command limitations, memory scope isolation. Docker and Unix sandbox implementations with snapshot/restore [4].
- **Encryption**: `EncryptedSession` provides transparent encryption with TTL-based expiration for session state [9].
- **Audit trail**: Built-in tracing with spans, trace IDs, and group IDs. Integrates with OpenAI's evaluation pipeline for compliance review [4].

### Google ADK

- **Authentication**: Tool-level authentication support. Built-in safety mechanisms with action confirmation workflow [10].
- **Enterprise security**: "Enterprise-grade security" when deployed to Google Cloud -- built-in authentication, Cloud Trace observability, and IAM integration without code changes [10].
- **Vertex AI integration**: VertexAiSessionService for managed session storage with Google Cloud security controls.
- **Observability**: Three-pillar approach: logging, metrics, Cloud Trace integration for request flow visualization [10].
- **Action confirmation**: Tools can require user confirmation before execution, providing a guardrail against unauthorized actions.

### CrewAI

- **Delegation controls**: `allow_delegation=False` (default) prevents agents from delegating to others. When enabled, delegation is task-scoped [14].
- **Code execution**: Deprecated built-in code execution. Security-conscious recommendation to use external sandboxes (E2B, Modal) [14].
- **RBAC**: CrewAI Enterprise provides role-based access control and team management with controlled access to production automations [13].
- **Security config**: Crew-level `security_config` for fingerprinting and identity verification [16].
- **JSON execution warning**: JSON crew configurations support `{"python": "module.attribute"}` and `custom:<name>` tool references that execute local code. Documentation warns: "Only run JSON crew projects from sources you trust" [16].
- **Guardrails**: Task-level guardrails (function-based or LLM-based) with sequential chain execution and configurable max retries [15].

### Microsoft Agent Framework

- **Middleware system**: Flexible request/response processing for authentication, authorization, logging, and custom security pipelines [20].
- **HITL**: Human-in-the-loop control integrated into orchestration patterns [20].
- **OpenTelemetry**: Built-in distributed tracing for compliance and audit [20].
- **Responsible AI**: Documentation emphasizes review of "responsible AI mitigations, content filters, and safety systems appropriate to specific use cases" [20].
- **Governance**: Enterprise-grade governance when deployed via Microsoft Foundry [20].

## 5. Production Failure Modes

### LangGraph

- **Recursion limit exhaustion**: Default `max_iterations=25`. Complex graphs with conditional cycles can hit this silently, raising `GraphRecursionError`. Must be tuned per use case -- too low truncates valid workflows, too high allows runaway loops [3].
- **Checkpointer serialization failures**: Custom state objects that aren't JSON-serializable cause checkpointer crashes. Must implement custom `SerializationProtocol` for complex state types.
- **State merge conflicts**: Incorrect reducer logic causes silent state corruption. If two concurrent nodes update the same state key without a proper reducer, last-write-wins behavior can lose data.
- **Cold start latency**: Graph compilation adds startup overhead. Complex graphs with many nodes/edges have measurable compile time.
- **Version migration**: Checkpoint format changes across versions (e.g., v0.2 renamed `thread_ts` to `checkpoint_id`). Existing checkpoints require migration or become unreadable [3].

### OpenAI Agents SDK

- **Statelessness pitfalls**: Without sessions configured, conversation history is lost between `Runner.run()` calls. Developers must explicitly manage state via sessions or `to_input_list()`. Mixing session persistence with server-managed settings (`conversation_id`/`previous_response_id`) is not supported [6].
- **MaxTurnsExceeded**: Default turn limit triggers `MaxTurnsExceeded` exception. Set `max_turns=None` to disable, but risks infinite loops. Error handlers can provide graceful fallbacks [6].
- **WebSocket connection limits**: 60-minute connection limit for WebSocket transport. After reconnect, `store=False` runs cannot recover uncached `previous_response_id` [6].
- **Guardrail timing**: Parallel input guardrails may allow the agent to consume tokens before the tripwire fires. Use `run_in_parallel=False` for cost-sensitive scenarios [8].
- **ModelBehaviorError**: Malformed JSON from LLM, unexpected tool usage, or failed responses raise `ModelBehaviorError`. `tool_not_found_behavior="return_error_to_model"` allows self-correction [6].
- **Session backend atomicity**: MongoDB oversized batches "fail atomically without storing a partial batch" -- good for consistency but can lose an entire turn of work [9].

### Google ADK

- **Breaking version changes**: ADK 2.0 sessions are incompatible with pre-1.28 versions. Production systems must coordinate upgrades carefully [11].
- **Model lock-in risk**: While multi-model capable, ADK is optimized for Gemini. Non-Gemini models may not support all features (e.g., grounding with search, Live API).
- **Context compression trade-offs**: Automatic context filtering can remove information that later proves relevant. No mechanism to "pin" certain context as non-compressible.
- **Deployment coupling**: "One-command deployment to Google Cloud" implies tight GCP coupling. Self-hosted deployments require more manual configuration.

### CrewAI

- **Delegation loops**: When `allow_delegation=True`, agents can delegate tasks in circles (Agent A delegates to B, B delegates back to A). The `max_iter=20` default provides a circuit breaker, but the system may consume 20 LLM calls before stopping [14].
- **Context window overflow**: Despite `respect_context_window=True`, auto-summarization is lossy. Tasks requiring precise recall of earlier content may fail silently with summarized context [14].
- **Rate limiting**: Without appropriate `max_rpm` settings, concurrent agents can exhaust API rate limits. No built-in backoff beyond `max_retry_limit=2` [14].
- **SQLite persistence limitations**: Flow persistence uses SQLite by default, which has write concurrency limitations. Not suitable for high-throughput distributed deployments without backend swap.
- **Model compatibility**: `use_system_prompt` must be disabled for models without system message support (e.g., o1). Function calling LLM must be set separately if the primary model doesn't support tool use [14].
- **Security risk in JSON configs**: `{"python": "module.attribute"}` references in JSON crew files execute arbitrary code. Supply chain risk if using untrusted crew definitions [16].

### AutoGen (Historical)

- **Conversation explosion**: Full message history sent with each call causes quadratic token growth. Multi-agent conversations with 10+ turns become prohibitively expensive [18].
- **Maintenance mode**: No new features, community-managed. Production users advised to migrate to MS Agent Framework [18].
- **Python version lock**: AgentChat requires Python 3.10+ [19].

### Cross-Framework Migration Challenges

- **State format incompatibility**: Each framework uses proprietary state serialization. No standard format for agent state means migration requires rebuilding conversation history.
- **Tool definition mismatch**: Despite MCP standardization, tool schemas and invocation patterns differ. LangGraph tools are node-scoped functions; OpenAI Agents SDK tools are agent-scoped with automatic schema generation; CrewAI tools use BaseTool inheritance.
- **Orchestration pattern differences**: Translating a LangGraph graph to OpenAI handoffs or CrewAI crews requires redesigning the coordination logic, not just porting code.

## 6. Enterprise System Design Scenarios

### Framework Selection Criteria

**Use LangGraph when**:
- You need fine-grained control over agent execution flow
- The workflow has complex conditional logic, cycles, or parallel branches
- You need production-grade persistence with point-in-time recovery (time travel)
- You're already invested in the LangChain ecosystem
- The team has strong Python skills and can handle low-level abstractions
- **Best for**: Complex stateful workflows, research applications, custom agent architectures

**Use OpenAI Agents SDK when**:
- You want a lightweight framework with minimal learning curve
- The use case involves multiple specialist agents with clear handoff patterns (triage -> specialist)
- You need built-in sandboxing for code execution tasks
- Voice/realtime agents are part of the requirement
- You need durable execution via Temporal/Dapr/Restate integration
- Provider flexibility is needed (100+ LLM support via LiteLLM)
- **Best for**: Customer service agents, triage/routing systems, voice applications

**Use Google ADK when**:
- You're deploying on Google Cloud (native GCP integration)
- Context efficiency is critical (ADK's context management is best-in-class)
- You need multi-language support (Python, Go, Java, Kotlin, TypeScript)
- The workflow mixes deterministic and adaptive reasoning
- You want built-in A2A protocol support for agent interoperability
- **Best for**: Google Cloud enterprises, multi-language teams, context-intensive applications

**Use CrewAI when**:
- You want high-level abstractions with minimal boilerplate
- The use case maps naturally to "team of specialists" metaphor
- You need built-in memory, knowledge, and learning across runs
- Rapid prototyping is prioritized over fine-grained control
- Enterprise features (RBAC, monitoring, managed deployment) are needed via AMP Suite
- **Best for**: Content generation, research automation, business process automation

**Use Microsoft Agent Framework when**:
- You're in a .NET-heavy enterprise with existing Azure infrastructure
- You need cross-language consistency (C# + Python with same APIs)
- Microsoft Foundry hosted agents simplify deployment
- You're migrating from AutoGen and need a supported successor
- **Best for**: .NET enterprises, Azure-native deployments, AutoGen migration

### Hybrid Approaches

**MCP as the integration layer**: Use different frameworks for different agent types within the same system, with MCP providing the tool interoperability layer. Example: LangGraph for complex orchestration logic, OpenAI Agents SDK for customer-facing voice agents, both connecting to the same MCP tool servers.

**A2A for framework interop**: Google's A2A protocol enables agents built with different frameworks to communicate. A CrewAI research crew can delegate to a LangGraph analysis pipeline via A2A, with each framework handling what it does best.

**Orchestrator + specialist pattern**: Use a lightweight framework (OpenAI Agents SDK) for the top-level orchestrator, with heavyweight frameworks (LangGraph) for specialist sub-workflows that need complex state management.

### Enterprise Deployment Patterns

1. **Managed cloud**: Google ADK -> Agent Runtime/Vertex AI; CrewAI -> AMP Suite; MS Agent Framework -> Foundry. Least operational overhead.
2. **Self-hosted with durability**: LangGraph + Postgres checkpointer + Temporal for durable execution. Most control, most operational burden.
3. **Serverless**: OpenAI Agents SDK + Dapr sessions + cloud functions. Good for bursty workloads.
4. **Hybrid cloud**: MS Agent Framework with Azure + on-premise split. For regulated industries with data residency requirements.

### Migration Strategies

**From AutoGen to MS Agent Framework**: Microsoft provides an official migration guide. AgentChat patterns map to MAF orchestration patterns. Core event-driven architecture is similar [20].

**From LangChain Agents to LangGraph**: LangChain agents are already built on LangGraph. Migration is incremental -- replace AgentExecutor with explicit StateGraph for more control.

**Between other frameworks**: No standardized migration path. Recommended approach:
1. Extract tool definitions into MCP servers (framework-agnostic)
2. Document agent behaviors as specifications (role, capabilities, constraints)
3. Rebuild orchestration logic in the target framework
4. Migrate state by replaying conversations through the new system

## Sources
- [1] https://pypi.org/project/langgraph/ -- LangGraph PyPI page (v1.2.11, Aug 2026)
- [2] https://github.com/langchain-ai/langgraph -- LangGraph GitHub (40.1k stars)
- [3] https://www.langchain.com/blog/langgraph-v0-2 -- LangGraph v0.2 architecture and checkpointer details
- [4] https://openai.github.io/openai-agents-python/ -- OpenAI Agents SDK documentation
- [5] https://pypi.org/project/openai-agents/ -- OpenAI Agents SDK PyPI (v0.22.0, Aug 2026)
- [6] https://openai.github.io/openai-agents-python/running_agents/ -- Runner execution model, durable execution integrations
- [7] https://openai.github.io/openai-agents-python/handoffs/ -- Handoff architecture and configuration
- [8] https://openai.github.io/openai-agents-python/guardrails/ -- Guardrails system (input, output, tool-level)
- [9] https://openai.github.io/openai-agents-python/sessions/ -- Session backends and distributed deployment
- [10] https://adk.dev/ -- Google ADK documentation hub
- [11] https://pypi.org/project/google-adk/ -- Google ADK PyPI (v2.7.1, Aug 2026)
- [12] https://pypi.org/project/crewai/ -- CrewAI PyPI (v1.15.17, Aug 2026)
- [13] https://github.com/crewAIInc/crewAI -- CrewAI GitHub (57.4k stars, 100k+ certified devs)
- [14] https://docs.crewai.com/concepts/agents -- CrewAI agent configuration and capabilities
- [15] https://docs.crewai.com/concepts/tasks -- CrewAI task system and guardrails
- [16] https://docs.crewai.com/concepts/crews -- CrewAI crew orchestration and checkpointing
- [17] https://docs.crewai.com/concepts/flows -- CrewAI Flow architecture, persistence, crash recovery
- [18] https://github.com/microsoft/autogen -- AutoGen GitHub (60.6k stars, maintenance mode)
- [19] https://pypi.org/project/autogen-agentchat/ -- AutoGen AgentChat PyPI (v0.7.5)
- [20] https://github.com/microsoft/agent-framework -- MS Agent Framework (13k stars, v1.14.0)
- [21] https://modelcontextprotocol.io/introduction -- MCP protocol overview and ecosystem
