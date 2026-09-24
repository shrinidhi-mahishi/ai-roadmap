# Research: Master One Framework

**Date researched**: 2026-09-23
**Sources consulted**: 42

---

## 1. System Topology & Mechanics

### LangGraph: State Machine Model

LangGraph is a low-level orchestration framework built by the LangChain team for stateful, multi-step AI agents. It models workflows as directed graphs built from three primitives: **State**, **Nodes**, and **Edges** ([LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**State** is a typed container (TypedDict or Pydantic model). Every node receives current state, returns a partial update, and LangGraph merges it back using a **reducer** (overwrite, append, or custom merge). State is incrementally updated, not completely overwritten -- this design enables parallel execution where multiple nodes modify different fields simultaneously ([LangGraph Architecture](https://cubxxw.com/projects/langgraph/)).

**Nodes** are Python functions (sync or async) that take state and return a dict. They perform actions (LLM calls, tool execution, computation) but do not manage control flow. That separation is the core design principle ([LangGraph Basics](https://shafiqulai.github.io/blogs/blog_8.html)).

**Edges** define transitions. Fixed edges push state from one node to the next. Conditional edges route based on a function that inspects state and returns a node name or `END`. Every graph must have at least one edge from `START` to a node and at least one edge from a node to `END` ([LangGraph Docs](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**StateGraph** is the central builder class. You register nodes, connect them with edges, then call `.compile()` to create an executable graph supporting `invoke()`, `stream()`, `astream()`, and `ainvoke()`. Compilation performs type checking, edge connectivity validation, and injects the checkpointer. Under the hood, execution follows a **Pregel-style message-passing model**: each step processes nodes whose inputs are ready, applies reducers, and writes checkpoints ([LangGraph Architecture - Medium](https://medium.com/@shuv.sdr/langgraph-architecture-and-design-280c365aaf2c)).

**Subgraphs** allow graph composition -- a node in one graph can be another compiled graph. **Human-in-the-loop** is native: inside a node you call `interrupt(...)`, which saves a checkpoint and returns control; any worker can later resume with `Command(resume=...)` ([LangGraph Persistence Docs](https://docs.langchain.com/oss/python/langgraph/persistence)).

**Streaming modes**: LangGraph supports streaming state updates, events, and custom data during graph execution.

**LangGraph Platform/Cloud**: Managed deployment service with Self-Hosted Lite (free, up to 1M node executions), Cloud SaaS, BYOC (AWS), and Self-Hosted Enterprise tiers. Starts at $39/user/month with 100K node executions included ([LangGraph Pricing](https://www.truefoundry.com/blog/langgraph-pricing)).

LangGraph 1.0 went GA in October 2025. No LangChain dependency required since 1.0 ([LangGraph GitHub](https://github.com/langchain-ai/langgraph)).

### CrewAI: Agent/Task/Crew Model

CrewAI is an open-source Python framework built independently (not wrapping LangChain) that models multi-agent collaboration as a team of role-playing agents ([CrewAI Docs](https://docs.crewai.com/)).

**Four-layer architecture**:

1. **Flows** -- Top-level event-driven orchestration. Defines the overall sequence, routes data between steps, handles conditional branching. Decides which Crew runs when and what happens with results ([CrewAI Docs](https://docs.crewai.com/)).

2. **Crews** -- Collaborative agent groups assigned to a specific objective. Each Crew contains Agents, Tasks, and a process model. Operates as a self-contained unit: receives inputs from a Flow, coordinates agents, returns structured outputs ([CrewAI GitHub](https://github.com/crewaiinc/crewai)).

3. **Agents** -- Individual autonomous units. Each agent gets a `role` (e.g., "Senior Data Analyst"), `goal`, and `backstory` that shapes behavior. Agents can be assigned specific LLMs, tools, and memory configurations independently ([Mastering CrewAI - DEV](https://dev.to/ismail_zamareh_d099419122bc4f/mastering-multi-agent-systems-with-crewai-a-practical-guide-23f0)).

4. **Tasks** -- Specific assignments with guardrails. Each task specifies expected output format, the responsible agent, and optional validation criteria. Tasks can require structured JSON output, set token limits, or chain outputs as inputs to subsequent tasks ([CrewAI Docs](https://docs.crewai.com/)).

**Process types**:
- **Sequential**: Tasks execute one after another, each agent's output feeding into the next. Default mode, most predictable for debugging.
- **Hierarchical**: A manager agent dynamically delegates tasks to specialists. Better for flexible, adaptive workflows. Documented bug: manager sometimes runs tasks in sequence instead of routing to best agent (GitHub issue #4783, March 2026) ([CrewAI Production Guide](https://techjacksolutions.com/ai-tools/crewai/crewai-production-guide/)).
- **Consensual** (planned, not yet released): Agents negotiate and vote on decisions before executing.

**Tool ecosystem**: 60+ built-in tools including web search, file I/O, code execution, database queries, API integrations. Native MCP and A2A protocol support ([CrewAI GitHub](https://github.com/crewaiinc/crewai)).

**Model agnostic**: Supports OpenAI, Anthropic, open-source via Ollama, and any OpenAI-compatible API.

### OpenAI Agents SDK: Handoff-Based Architecture

Released March 2025 as the successor to the experimental Swarm project. Available in Python and TypeScript ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

**Six core primitives**:

1. **Agent** -- An LLM configured with instructions, tools, and optional runtime behavior (handoffs, guardrails, structured outputs) ([Agents Docs](https://openai.github.io/openai-agents-python/agents/)).

2. **Runner** -- Executes the agent loop: call model, run tool, feed result back, repeat until final answer. Manages turns, tools, guardrails, handoffs, and sessions. Uses the Responses API by default for OpenAI models ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

3. **Tools** -- Python functions exposed to the agent with automatic schema generation and Pydantic-powered validation. Also supports hosted tools and MCP servers ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

4. **Handoffs** -- One agent delegates conversation to another specialized agent. Implemented as a tool call (`transfer_to_X`). The Runner switches the active agent and passes full conversation history. The new agent fully takes over rather than returning a result to an orchestrator ([Handoffs Docs](https://openai.github.io/openai-agents-python/handoffs/)).

5. **Guardrails** -- Input and output checks that run alongside the agent and can halt it. Input guardrails intercept user input before processing. Output guardrails validate the final response. Raise a tripwire exception when triggered ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

6. **Sessions** -- Automatic conversation history across runs, with pluggable backends (SQLite, Redis, SQLAlchemy) ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

**Built-in tracing**: Every `Runner.run` call automatically produces a trace -- a structured record of every model call, tool invocation, handoff, and guardrail check. Traces uploaded to OpenAI platform dashboard by default. Supports OpenTelemetry export and custom backends ([Tracing Docs](https://openai.github.io/openai-agents-python/tracing/)).

**April 2026 update**: Added sandboxing primitive (E2B/Modal/Daytona) for safe untrusted code execution, long-horizon harness that checkpoints agent state across turns for tasks spanning hours or days, streaming speech-to-text, and arbitrary message sizes ([OpenAI Blog - Next Evolution](https://openai.com/index/the-next-evolution-of-the-agents-sdk/)).

**Provider support**: Despite early "OpenAI-only" claims, the SDK is now provider-agnostic per the project README, supporting OpenAI Responses and Chat Completion APIs from any compatible provider ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

### Google ADK (Brief Comparison)

Google Agent Development Kit (ADK), introduced at Cloud NEXT 2025, uses an **event-driven runtime architecture** with a **hierarchical agent tree**. A root agent delegates to sub-agents, which can have their own sub-agents ([Google ADK Docs](https://adk.dev/)).

**Built-in workflow agent types**:
- **SequentialAgent**: Execute agents/steps in order
- **ParallelAgent**: Fan-out execution, fan-in results
- **LoopAgent**: Repeat until condition met
- **Custom/Graph-based**: Explicit graph definition for complex flows

Available in Python, TypeScript, Go, and Java. Optimized for Gemini but supports 100+ models via LiteLLM. Supports both MCP (vertical tool integration) and A2A (horizontal agent-to-agent communication) protocols natively -- the only framework with first-class support for both ([Google ADK - New Stack](https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/)).

Deploy to Vertex AI Agent Engine Runtime, Cloud Run, or GKE. Pre-GA in parts as of 2026 with bi-weekly release cadence ([ADK GitHub](https://github.com/google/adk-python)).

### Cross-Framework Comparison

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK | Google ADK |
|-----------|-----------|--------|-------------------|------------|
| **Execution model** | Directed graph (Pregel-style) | Role-based crews | Handoff chains | Agent tree + workflows |
| **Primary abstraction** | State + Nodes + Edges | Agents + Tasks + Crews + Flows | Agent + Runner + Handoffs | Agents + Workflow agents |
| **Control flow** | Explicit (you draw every edge) | Implicit (process type) | Prompt-driven (handoff routing) | Mixed (declarative workflows + LLM delegation) |
| **State management** | First-class typed state with reducers | Task output passing | Session-based conversation history | Session state with pluggable backends |
| **Extensibility** | Highest (arbitrary graph topology) | Medium (3 process types) | Low-medium (linear handoff chains) | High (composable agent types) |
| **Languages** | Python, JS/TS | Python | Python, TypeScript | Python, TypeScript, Go, Java |

---

## 2. Token Economics & NFR Metrics

### Cost Profiles

**LangGraph**: The framework is MIT-licensed and free. LangGraph Platform starts at $39/user/month with 100K node executions; additional executions cost $0.001 each. Self-hosted costs are infrastructure-only (compute, Postgres, monitoring). Token costs are dominated by LLM inference, not framework overhead ([LangGraph Pricing](https://www.truefoundry.com/blog/langgraph-pricing)).

**CrewAI**: Open-source core is free. A typical three-agent sequential crew using GPT-4o costs ~$0.10-$0.20 per run; GPT-4o-mini lowers this to $0.06-$0.12. For 1,000 runs/month: $100-$200 in model costs. Enterprise AMP Suite starts at ~$2,000/month ([CrewAI Review 2026](https://vibecoding.app/blog/crewai-review)).

**OpenAI Agents SDK**: The SDK is free and open-source. Costs are purely model API usage. The handoff pattern uses a cheap triage agent for classification, routing to expensive specialists only when needed -- this naturally optimizes costs ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

### Token Overhead Benchmarks

A standardized 2026 cross-framework benchmark (AIMultiple, 100 queries x 100 runs, identical models/tools) measured framework orchestration overhead ([AIMultiple RAG Frameworks](https://aimultiple.com/rag-frameworks)):

| Framework | Orchestration Latency | Token Overhead/Query |
|-----------|----------------------|---------------------|
| DSPy | ~3.5ms | ~2,030 tokens |
| Haystack | ~5.9ms | ~1,570 tokens |
| LlamaIndex | ~6.0ms | ~1,600 tokens |
| LangChain | ~10ms | ~2,400 tokens |
| LangGraph | ~14ms | ~2,030 tokens |

Key finding: framework overhead is "measurable but small" -- dwarfed by LLM inference time (typically 1-3 seconds per call).

**LangGraph vs CrewAI token efficiency**: Benchmarks show LangGraph achieves **47% lower token costs** than CrewAI due to explicit edge transitions instead of LLM-driven task routing. CrewAI uses ~4,500 tokens per run on tasks where LangGraph uses under 2,000. In hierarchical mode, CrewAI's manager agent consumed 30% of total token budget just for coordination ([LangGraph vs CrewAI - DEV](https://dev.to/jamilxt/langgraph-vs-crewai-vs-openai-agents-sdk-picking-your-agent-framework-in-2026-2heo)).

**LangGraph state efficiency**: Maintains O(1) complexity for conversation history length by passing state deltas rather than full conversation histories. Completed >2x faster than CrewAI in a fixed five-agent workflow benchmark (100 runs) ([LangGraph Token Optimization](https://cloudatler.com/blog/langgraph-token-usage-and-cost-optimization-strategies-a-guide-for-2025-and-beyond)).

### Latency & Streaming

- **LangGraph**: Supports streaming state updates, events, and custom data. Multi-agent workflow latency: ~10,155ms in benchmarks (dominated by LLM calls). A Rust-based alternative (AutoAgents) beat LangGraph by 43.7% on latency ([DEV Benchmark 2026](https://dev.to/saivishwak/benchmarking-ai-agent-frameworks-in-2026-autoagents-rust-vs-langchain-langgraph-llamaindex-338f)).
- **CrewAI**: Streaming support available. Executes tasks 5.76x faster than LangGraph in QA scenarios (JetThoughts benchmark), but LangGraph achieves 62% success rate vs CrewAI's 54% on complex tasks requiring deep reasoning ([CrewAI Review](https://vibecoding.app/blog/crewai-review)).
- **OpenAI Agents SDK**: Native streaming support. Added streaming speech-to-text in April 2026 update.
- **Google ADK**: Built-in streaming with unique bidirectional audio and video streaming capabilities.

### Scalability Limits

- **LangGraph**: Checkpointer state bloat is the primary scaling concern. A 50MB state with 10 steps = 500MB written to Postgres. Mitigation: store references to large artifacts, not the artifacts themselves. Connection pooling via PgBouncer recommended ([LangGraph Persistence Guide](https://fast.io/resources/langgraph-persistence/)).
- **CrewAI**: Agent memory grows linearly with tasks; beyond 200 tasks per crew, context windows overflow and accuracy drops below 60% ([CrewAI Atlan](https://atlan.com/know/ai-agent/what-is-crewai/)).
- **OpenAI Agents SDK**: No built-in horizontal scaling; relies on external infrastructure (Redis sessions for distributed deployments).

### Framework Maturity Indicators (Sep 2026)

| Metric | LangGraph | CrewAI | OpenAI Agents SDK |
|--------|-----------|--------|-------------------|
| **GitHub stars** | ~42K | ~58K | ~29K |
| **Monthly PyPI downloads** | 38.8M | 27M+ | Not reported |
| **GA version** | 1.0 (Oct 2025) | 1.10.x | 0.2.x (Apr 2026) |
| **Enterprise adopters** | Klarna, Replit, Elastic, Lyft, Uber, Coinbase, NVIDIA | 450M monthly workflows, 2B agent runs/year | Rapid growth, primarily OpenAI ecosystem |
| **Learning curve** | 1-2 weeks | 3-5 days | 2-3 days |

Sources: [LangGraph GitHub](https://github.com/langchain-ai/langgraph), [CrewAI Stars](https://theagenttimes.com/articles/crewai-blows-past-44000-github-stars-and-the-solo-agent-era-along-with-it), [OpenAI Agents SDK GitHub](https://github.com/openai/openai-agents-python), [AI Agent Frameworks Ranked 2026](https://dreaming.press/posts/ai-agent-frameworks-github-ranked-by-stars-2026.html).

---

## 3. Distributed Resilience & State

### LangGraph Checkpointers

LangGraph provides two complementary persistence systems ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)):

**Checkpointers** (thread-scoped, short-term):
- **InMemorySaver**: Not durable -- state lost on process restart. For development/testing only.
- **SqliteSaver**: Durable on single machine, zero setup. File contains full state history for every thread including all intermediate checkpoints.
- **PostgresSaver / AsyncPostgresSaver**: Production standard. Supports concurrent requests, transactional integrity, JSONB for complex state. Requires `langgraph-checkpoint-postgres >= 3.0.0`. Run PgBouncer in transaction pooling mode between app and Postgres ([LangGraph + PostgreSQL](https://markaicode.com/integrate/langgraph-with-postgresql/)).
- **RedisSaver**: Available but had SQL injection vulnerability (CVE-2026-27022, patched in `langgraph-checkpoint-redis 1.0.2+`).
- **Custom**: Implement the `BaseCheckpointSaver` interface for any backend.

**Stores** (cross-thread, long-term): For long-term memory shared across threads.

**Rule of thumb**: InMemorySaver for throwaway runs, SqliteSaver for single-machine persistence, PostgresSaver once multiple processes share threads ([LangGraph Persistence Guide](https://fast.io/resources/langgraph-persistence/)).

### CrewAI State Persistence

CrewAI's default LanceDB/SQLite storage is **lost on every container restart**. This is the most commonly reported production issue ([CrewAI Memory in Production](https://activewizards.com/blog/crewai-memory-systems-in-production-persistence-retrieval-and-state-recovery/)).

**Memory types**: Short-term, long-term, and entity memory. Memory stays scoped to a single Crew with no federation, conflict resolution, or ownership metadata across Crews.

**External memory fix**: Configure Mem0 (Cloud or self-hosted with Qdrant/pgvector) as an external memory provider via the `memory_config` parameter.

**Context bleed risk**: Without explicit `user_id` scoping via ExternalMemory API, context bleeds between users -- a data privacy violation in multi-tenant deployments ([CrewAI Memory Docs](https://docs.crewai.com/en/concepts/memory)).

**No built-in checkpointing**: Unlike LangGraph, CrewAI has no mechanism to pause, checkpoint, and resume a crew mid-execution.

### OpenAI Agents SDK State Management

**Sessions** persist conversation state across runs with pluggable backends (SQLite, Redis, SQLAlchemy). Sessions handle short-term conversational context cleanly ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

**Limitations**: The SDK is intentionally minimal -- no built-in answer for what happens when the process dies mid-loop. Durable memory, retrieval, and personalization layers must be added externally ([OpenAI Agents SDK Production - DEV](https://dev.to/gabrielanhaia/the-openai-agents-sdk-in-production-what-it-gives-you-and-what-it-hides-16a9)).

**April 2026 long-horizon harness**: Checkpoints agent state across turns for tasks spanning hours or days, but details on implementation are still emerging ([OpenAI Blog - Next Evolution](https://openai.com/index/the-next-evolution-of-the-agents-sdk/)).

### Fault Tolerance Comparison

| Capability | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Crash recovery** | Automatic via checkpointing -- resumes exactly where left off | No built-in mechanism | No built-in; long-horizon harness (Apr 2026) emerging |
| **Mid-execution pause/resume** | Native (`interrupt()` + `Command(resume=...)`) | Not supported | Sessions provide conversational continuity only |
| **State durability** | Postgres/SQLite/Redis checkpointers | Ephemeral by default; Mem0 for external persistence | Session backends (SQLite/Redis) for conversation; no workflow state |
| **Side-effect safety** | Checkpoint replay can re-trigger side effects (user must handle idempotency) | Memory checkpoint + resume can re-send emails/API calls (same issue) | Not addressed |

### Replay and Time-Travel Debugging

**LangGraph** is the only framework with built-in **time-travel debugging**. You can rewind to a previous checkpoint, inspect the exact state at any step, modify logic, and replay execution. This is critical for debugging complex multi-agent loops ([LangGraph Checkpointing](https://callsphere.ai/blog/langgraph-checkpointing-persistence-time-travel-agent-workflows)).

**CrewAI** and **OpenAI Agents SDK** have no equivalent capability. CrewAI Enterprise provides execution traces for post-hoc inspection but not replay.

### Horizontal Scaling Patterns

- **LangGraph**: PostgresSaver enables multiple processes to share threads. LangGraph Platform handles auto-scaling. The `interrupt()`/resume pattern is stateless -- any worker can resume a paused thread. Connection pooling via PgBouncer is essential ([LangGraph Production](https://aerospike.com/blog/langgraph-production-latency-replay-scale/)).
- **CrewAI**: Single-process by default. CrewAI Enterprise/Factory provides containerized deployment for horizontal scaling. Open-source requires custom infrastructure.
- **OpenAI Agents SDK**: Redis session backend enables distributed deployments. No native scaling primitives. Teams pair with **Temporal** for durable, horizontally-scaled agent workflows -- Temporal child workflows handle multi-agent handoffs with configurable timeout and retry policies ([OpenAI SDK + Temporal](https://baeseokjae.github.io/posts/openai-agents-sdk-temporal-integration-2026/)).

---

## 4. Enterprise Security & Governance

### Authentication & Authorization

| Capability | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **AuthN/AuthZ** | Agent Authorization (beta, all tiers); SSO + RBAC on Enterprise | None in open-source; RBAC + SSO in Enterprise AMP Suite | No built-in auth; relies on application-level implementation |
| **SOC 2 Type II** | Yes (via LangSmith) | Enterprise only [inferred] | Via OpenAI platform |
| **HIPAA** | Yes (BAA on Enterprise tier) | Not documented | Via OpenAI platform (BAA available) |
| **GDPR** | Yes | Enterprise only [inferred] | Via OpenAI platform |

Sources: [LangGraph Platform](https://www.langchain.com/blog/langgraph-platform-announce), [CrewAI Enterprise](https://theneuralbase.com/crewai/learn/advanced/crewai-enterprise-what-it-adds/), [OpenAI Enterprise](https://thesoogroup.com/blog/openai-enterprise-ai-evolution-agents-sdk-security-axios-response).

### Multi-Tenancy

- **LangGraph**: Not natively advertised. Teams implement via infrastructure-level isolation (VPCs, namespace separation) and RBAC. Vector database infrastructure (e.g., Pinecone) provides namespace isolation alongside LangGraph ([LangGraph Platform](https://www.langchain.com/blog/langgraph-platform-announce)).
- **CrewAI**: Open-source has no concept of teams or API keys with scoped permissions. Enterprise AMP adds identity, role-based filtering of execution history, and cost attribution per principal ([CrewAI Governance](https://community.crewai.com/t/how-are-people-handling-execution-auditability-and-governance-in-production-crewai-deployments/7589)).
- **OpenAI Agents SDK**: No built-in multi-tenancy. Application responsibility.

### Audit Logging

- **LangGraph**: LangSmith provides full decision traces node-by-node. One HIPAA auditor specifically cited LangSmith traces as the artifact they needed ([LangGraph vs CrewAI](https://pub.towardsai.net/langgraph-vs-crewai-vs-autogen-which-ai-agent-framework-should-your-enterprise-use-in-2026-3a9ebb407b09)).
- **CrewAI**: No audit logging in open-source (console output only). Enterprise AMP adds immutable audit trails, runtime hooks for PII redaction and policy checks at every LLM and tool call ([CrewAI Enterprise](https://theneuralbase.com/crewai/learn/advanced/crewai-enterprise-what-it-adds/)).
- **OpenAI Agents SDK**: Built-in tracing with visual DAGs, detailed logs, exportable traces for compliance. Sensitive data controls via `trace_include_sensitive_data` flag. Supports OpenTelemetry. Third-party solutions like hoop.dev extend audit capabilities with identity-aware proxying and PII masking ([Tracing Docs](https://openai.github.io/openai-agents-python/tracing/), [hoop.dev audit trails](https://hoop.dev/blog/audit-trails-for-the-openai-agents-sdk)).

### Deployment Options

| Option | LangGraph | CrewAI | OpenAI Agents SDK |
|--------|-----------|--------|-------------------|
| **Self-hosted** | Yes (Enterprise tier) | Yes (CrewAI Factory) | Yes (any infra) |
| **Managed cloud** | LangGraph Cloud SaaS (via LangSmith) | CrewAI AMP Cloud | OpenAI platform |
| **BYOC** | Yes (AWS) | Hybrid (Factory for sensitive, Cloud for non-sensitive) | No managed BYOC; Manifest supports S3/GCS/Azure/R2 |
| **VPC deployment** | Enterprise plan | Factory (containerized) | Application responsibility |

### Tool Sandboxing

- **LangGraph**: No built-in sandboxing. Tools run in-process. Users implement isolation via Docker, E2B, or similar.
- **CrewAI**: Code Interpreter tool's Docker sandbox and SandboxPython fallback were both removed after CVE-2026-2275/2287. Current guidance points to external sandboxes (E2B, Daytona) ([CrewAI Security Review](https://drel.ai/blog/crewai-security-review)).
- **OpenAI Agents SDK**: Native sandbox execution added April 2026 (E2B/Modal/Daytona). Agents work within siloed workspaces accessing only explicitly relevant files ([OpenAI Blog - Next Evolution](https://openai.com/index/the-next-evolution-of-the-agents-sdk/)).

### Security Vulnerabilities (2025-2026)

**LangGraph/LangChain** (coordinated disclosure "LangDrained", March 2026 by Cyera Research):
- CVE-2025-67644: SQL injection in SQLite checkpointer (patched in `langgraph-checkpoint-sqlite 3.0.1+`)
- CVE-2026-28277: Unsafe msgpack deserialization leading to RCE (patched in `langgraph 1.0.10+`)
- CVE-2026-27022: SQL injection in Redis checkpointer (patched in `langgraph-checkpoint-redis 1.0.2+`)
- CVE-2025-64439: Deserialization RCE in JsonPlusSerializer (patched in `langgraph-checkpoint >= 3.0`)
- Mitigation: Set `LANGGRAPH_STRICT_MSGPACK=true` for production ([Check Point Research](https://research.checkpoint.com/2026/from-sqli-to-rce-exploiting-langgraphs-checkpointer/), [CSA Labs](https://labs.cloudsecurityalliance.org/research/csa-research-note-langchain-langgraph-vulnerabilities-202603/)).

**CrewAI** (disclosed by Yarden Porat, Cyata, 2026):
- CVE-2026-2275, CVE-2026-2285, CVE-2026-2286, CVE-2026-2287
- Pattern: Tools falling back to less-isolated execution modes without notification, combined with prompt injection. CVE-2026-2286 is SSRF via unvalidated URLs in RAG search tools ([CrewAI Security Review](https://drel.ai/blog/crewai-security-review)).

**OpenAI Agents SDK**: No publicly disclosed CVEs as of September 2026 [verified via search].

---

## 5. Production Failure Modes

### LangGraph: State Corruption and Schema Drift

**State corruption on historical checkpoints**: Updating state on non-head checkpoints can cause new checkpoints to inherit future values. Partial updates lead to "context leakage" where fields inherit values from the current thread head instead of the intended snapshot. Fix: retrieve full state with `get_state()`, copy, apply updates, submit complete dictionary ([LangGraph State Management](https://altersquare.io/langgraph-state-management-undocumented-issues-after-commit/)).

**Reducer conflicts under concurrency**: When two parallel nodes update the same field, the default reducer replaces the value, causing lost updates. Symptoms: disappearing messages, empty `tool_calls` arrays, inconsistent state after fan-out. Fix: annotate any field where parallel nodes contribute with a merge reducer. These bugs only surface under fan-out -- add tests that trigger parallel branches ([LangGraph Reducer Conflicts](https://itsourcecode.com/runtimeerror/langgraph-state-reducer-conflicts-fix/)).

**Schema evolution breaks existing threads**: Redeploying with updated schemas can corrupt or break existing threads. LangGraph applies the latest graph immediately to every thread (unlike workflow engines that pin runs to code version). Renaming or removing a node while threads are paused at that node causes failures on resume. Fix: use `state.get("field_name", default_value)` for backward compatibility ([LangGraph Backward Compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility)).

**Recursion limit confusion**: Official docs conflict -- Graph API docs state default of 1,000 steps (since v1.0.6), while `langgraph_sdk` config reference shows default of 25. As of August 2026, this discrepancy is unresolved ([LangGraph GitHub Issues](https://github.com/langchain-ai/langgraph/issues)).

**Version compatibility**: `langgraph-prebuilt==1.0.2` introduced a breaking change (required `runtime` parameter to `ToolNode.afunc`) without proper version constraints in `langgraph==1.0.1`. Fix: pin LangGraph and LangChain versions together, upgrade in lockstep ([GitHub Issue #6363](https://github.com/langchain-ai/langgraph/issues/6363)).

**Production incident rate**: LangChain's 2026 State of Agent Engineering report ties >60% of production incidents to state management ([LangGraph State Reducer Conflicts](https://itsourcecode.com/runtimeerror/langgraph-state-reducer-conflicts-fix/)).

### CrewAI: Delegation Loops and Nondeterminism

**Delegation loops**: Manager agents in hierarchical mode get stuck delegating between agents with overlapping roles. The manager cannot determine which agent should handle the task and bounces it between them. The hierarchical process drifted after ~40 production runs -- delegation patterns became unpredictable. Teams switched to sequential mode for predictability ([CrewAI Architecture Pitfalls](https://markaicode.com/architecture/crewai-agent-architecture/)).

**Mitigations**: Make agent roles mutually exclusive. Set `max_iterations=7` on all agents. Set `max_execution_time`. Disable delegation (`allow_delegation=False`) when not needed. Switch to sequential process if delegation is not required.

**Hallucination compounding**: Agent hallucinations compound exponentially beyond 4 sequential tools per agent. Mitigation: `temperature=0.1`, strict `expected_output` JSON schemas, ValidatorAgent, `max_tokens` per response -- reduced hallucination failures from 12% to 3% across 1,000 runs ([CrewAI Production Guide](https://techjacksolutions.com/ai-tools/crewai/crewai-production-guide/)).

**Tool input formatting**: Manager agents sometimes produce malformed JSON for tool calls, causing tool call failures.

**Memory overflow**: Beyond 200 tasks per crew, context windows overflow and accuracy drops below 60% ([CrewAI Explained - Atlan](https://atlan.com/know/ai-agent/what-is-crewai/)).

**Token cost spikes**: In hierarchical mode, manager agent used 30% of total token budget for coordination. A `max_iterations=10` cap avoided runaway loops.

**Debugging gap**: CrewAI gives no native way to isolate whether a failure came from the model, the prompt, or the source data a Crew read.

### OpenAI Agents SDK: Handoff Routing and Durability

**Non-deterministic handoff routing**: Control flow lives in prompts, not code. A router in code that misroutes is a unit-testable bug. A handoff that misroutes is caught in evals, if caught at all. Billing handoffs can mis-fire on refund-policy questions. Output severity can drift. Input guardrails can go silent for days -- yet nothing in generic metrics moves ([Evaluating OpenAI Agents SDK](https://futureagi.com/blog/evaluating-openai-agents-sdk-2026/)).

**Complex routing breaks down**: If routing has more than a handful of branches, or depends on structured state the model should not second-guess (invoice status, legal-hold flags), deterministic code outperforms prompt-based handoffs.

**Compounding step failure rates**: An agent with 85% per-step success rate running 8 sequential steps has only a 27% end-to-end success rate. Every additional step compounds failure probability. An analysis of 847 AI agent deployments in 2026 found 76% failed in production, with 62% tied to authentication and state management ([OpenAI SDK Production Guide](https://niteagent.com/blog/2026-07-08-openai-agents-sdk-production-guide/)).

**No mid-execution recovery**: The SDK has no built-in answer for process death mid-loop. The long-horizon harness (April 2026) partially addresses this.

**Guardrail scope limitations**: Input guardrails apply only to the first agent in a handoff chain. Output guardrails apply only to the agent producing the final output. Intermediate agents are unchecked unless you add tool-specific guardrails ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)).

### Migration Pain Points

**CrewAI to LangGraph** (most common migration path): Teams prototype in CrewAI, validate the concept, then hit CrewAI's control-flow ceiling. The migration requires rethinking the architecture from role-based to graph-based, rewriting agents as nodes, and implementing explicit state management ([LangGraph vs CrewAI](https://dev.to/jamilxt/langgraph-vs-crewai-vs-openai-agents-sdk-picking-your-agent-framework-in-2026-2heo)).

**OpenAI SDK to LangGraph**: Happens when teams need model flexibility or advanced state management beyond sessions.

**Version upgrade friction**: All three frameworks are evolving rapidly. LangGraph's pre-1.0 to 1.0 migration broke patterns like `AgentExecutor` and `compile(recursion_limit=...)`. CrewAI's frequent releases (1.10.x) sometimes change APIs without migration guides.

---

## 6. Enterprise System Design Scenarios

### Decision Matrix

| Scenario | Recommended Framework | Rationale |
|----------|----------------------|-----------|
| **Regulated industry (finance, healthcare)** requiring audit trails and human approval gates | **LangGraph** | Native HITL, durable checkpointing, LangSmith traces for compliance auditors. HIPAA BAA available. |
| **Rapid prototype** of multi-agent system for concept validation | **CrewAI** | Working two-agent crew in ~30 lines. Fastest time-to-first-working-agent. |
| **Simple router + specialists** pattern (customer support, triage) | **OpenAI Agents SDK** | Handoff pattern maps directly. Minimal boilerplate. Built-in tracing. |
| **Complex non-linear workflow** with loops, parallel branches, conditional routing | **LangGraph** | Explicit graph topology. Conditional edges. Fan-out/fan-in. |
| **Content operations** (research, writing, review pipelines) | **CrewAI** | Role-based design maps naturally to content workflows. |
| **Google Cloud shop** with Gemini-first strategy | **Google ADK** | Native Vertex AI deployment. A2A + MCP support. GKE scaling. |
| **Long-running autonomous tasks** (hours/days) | **LangGraph** (or OpenAI SDK + Temporal) | Durable execution with checkpoint-based resume. Temporal integration for OpenAI SDK fills the gap. |
| **Microsoft/Azure shop** | **Microsoft Agent Framework** (successor to AutoGen, GA April 2026) | Native Azure OpenAI integration. |
| **Single agent, 1-2 tools** | **No framework needed** | Plain SDK loop with `max_steps` cap. Framework adds friction, not value. |

Sources: [Decision Guide 2026 - DEV](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h), [Towards AI Enterprise Guide](https://pub.towardsai.net/langgraph-vs-crewai-vs-autogen-which-ai-agent-framework-should-your-enterprise-use-in-2026-3a9ebb407b09), [CodeBridge Guide](https://www.codebridge.tech/articles/choosing-a-multi-agent-framework-langgraph-crewai-microsoft-agent-framework-or-openai-agents-sdk).

### Enterprise Deployment Architectures

**LangGraph Production Architecture**:
```
[Client] -> [API Gateway / Load Balancer]
                |
         [LangGraph API Workers] (stateless, horizontally scaled)
                |
         [Background Queue Workers] (separate from API handlers)
                |
         [PostgreSQL] (checkpoints, state) + [PgBouncer] (connection pooling)
                |
         [LangSmith] (tracing, observability, audit)
                |
         [Secrets Manager] (zero-trust, least-privilege)
```
Key principle: separate API request handling from background queue workers. Secrets managed via environment variables with zero-trust access. LangGraph Platform handles this automatically; self-hosted requires manual setup ([LangGraph Production Architecture - Medium](https://medium.com/@manjunath.kvmc/production-deployment-architecture-and-implementation-strategies-for-langgraph-9569a60ea79c)).

**CrewAI Production Architecture**:
```
[Client] -> [API Layer]
                |
         [CrewAI Runtime] (single-process per crew)
                |
         [Mem0 / External Memory] (Qdrant/pgvector)
                |
         [CrewAI AMP Cloud / Factory] (Enterprise: RBAC, audit, monitoring)
                |
         [LLM Provider APIs] (model-agnostic)
```
Limitation: single-process per crew in open-source. Factory provides containerized deployment for horizontal scaling ([CrewAI Production Guide](https://techjacksolutions.com/ai-tools/crewai/crewai-production-guide/)).

**OpenAI Agents SDK Production Architecture**:
```
[Client] -> [Application Server]
                |
         [OpenAI Agents SDK Runner]
                |
         [Session Store] (Redis for distributed deployments)
                |
         [OpenAI Dashboard] (built-in tracing) / [OpenTelemetry] (custom)
                |
         [Temporal] (optional: durability, retry, crash recovery)
                |
         [Sandbox] (E2B/Modal/Daytona for untrusted code)
```
Teams deploying at scale in 2026 pair the SDK with Temporal for durable multi-agent workflows ([OpenAI SDK + Temporal](https://baeseokjae.github.io/posts/openai-agents-sdk-temporal-integration-2026/)).

### Real-World Production Deployments

- **Klarna** (85M users): LangGraph for agent workflows at scale. Multi-year enterprise LangGraph Platform agreement ([LangGraph Review](https://toolbrain.net/blog/langgraph-review-2026/)).
- **Elastic**: One of LangGraph's earliest enterprise adopters. Multi-year Enterprise LangGraph Platform agreement. Uses LangSmith traces for compliance.
- **Replit, Lyft, Uber, Coinbase, NVIDIA**: All use LangGraph for agent orchestration in production ([LangGraph GitHub](https://github.com/langchain-ai/langgraph)).
- **CrewAI**: 12 million+ daily agent executions in production. 2 billion agent runs in past 12 months. 450 million monthly workflows. Primary use cases: content operations, sales development, recruiting, internal research ([CrewAI Stars - Agent Times](https://digitalbydefault.ai/blog/crewai-multi-agent-orchestration-2026)).
- **OpenAI Agents SDK**: Rapidly growing adoption primarily in the OpenAI ecosystem. Specific enterprise case studies not yet widely published given the framework's relative youth.

### The Hybrid Path (Common Pattern)

A widely recommended approach: **prototype in CrewAI, productionize in LangGraph**. Treat CrewAI as the sketchpad and LangGraph as the engineering. CrewAI validates the multi-agent concept in days; LangGraph provides the durability, control, and human-in-the-loop for production. Using both in sequence is a feature, not indecision ([LangGraph vs CrewAI 2026](https://aliteq.com/langgraph-vs-crewai-2026)).

For simple router + specialist patterns, the OpenAI Agents SDK often replaces both -- the handoff pattern ships faster and reads cleaner than either framework for this specific shape.

### Emerging Convergence (2026)

With MCP standardizing how agents reach tools and A2A standardizing how agents talk to each other, the framework you pick no longer locks in your whole architecture. ADK is the only framework with first-class support for both protocols, but LangGraph and CrewAI are adding community integrations. The framework question is shifting from "which library" to "which execution model matches your workload" ([Google ADK vs LangGraph - Afnexis](https://afnexis.com/articles/google-adk-vs-langgraph)).

---

## Sources

- [1] [LangGraph Graph API Docs](https://docs.langchain.com/oss/python/langgraph/graph-api) -- Official LangGraph API documentation
- [2] [LangGraph StateGraph Reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph) -- StateGraph class reference
- [3] [LangGraph Architecture (cubxxw)](https://cubxxw.com/projects/langgraph/) -- Architecture deep-dive with diagrams
- [4] [LangGraph Architecture (Medium)](https://medium.com/@shuv.sdr/langgraph-architecture-and-design-280c365aaf2c) -- Architecture and design patterns
- [5] [LangGraph Persistence Docs](https://docs.langchain.com/oss/python/langgraph/persistence) -- Official persistence documentation
- [6] [LangGraph GitHub](https://github.com/langchain-ai/langgraph) -- Repository and release notes
- [7] [CrewAI Documentation](https://docs.crewai.com/) -- Official CrewAI docs
- [8] [CrewAI GitHub](https://github.com/crewaiinc/crewai) -- Repository
- [9] [CrewAI on AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-frameworks/crewai.html) -- AWS prescriptive guidance
- [10] [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) -- Official SDK documentation
- [11] [OpenAI Agents SDK - Agents](https://openai.github.io/openai-agents-python/agents/) -- Agent class reference
- [12] [OpenAI Agents SDK - Handoffs](https://openai.github.io/openai-agents-python/handoffs/) -- Handoffs documentation
- [13] [OpenAI Agents SDK - Tracing](https://openai.github.io/openai-agents-python/tracing/) -- Tracing documentation
- [14] [OpenAI Blog - Next Evolution of Agents SDK](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) -- April 2026 update announcement
- [15] [Google ADK Docs](https://adk.dev/) -- Official ADK documentation
- [16] [Google ADK - New Stack](https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/) -- Architectural tour
- [17] [Google ADK GitHub](https://github.com/google/adk-python) -- Repository
- [18] [LangGraph vs CrewAI vs OpenAI Agents SDK (DEV)](https://dev.to/jamilxt/langgraph-vs-crewai-vs-openai-agents-sdk-picking-your-agent-framework-in-2026-2heo) -- 2026 comparison
- [19] [LangGraph vs CrewAI vs OpenAI Agents (Ship Test)](https://techsy.io/en/blog/langgraph-vs-crewai-vs-openai-agents-sdk) -- Hands-on comparison
- [20] [AI Agent Frameworks Compared (Langfuse)](https://langfuse.com/blog/2025-03-19-ai-agent-comparison) -- Cross-framework comparison
- [21] [LangGraph Pricing Breakdown](https://www.truefoundry.com/blog/langgraph-pricing) -- 2026 pricing details
- [22] [LangGraph Platform Announcement](https://www.langchain.com/blog/langgraph-platform-announce) -- Deployment options
- [23] [LangGraph Checkpointing Guide](https://callsphere.ai/blog/langgraph-checkpointing-persistence-time-travel-agent-workflows) -- Checkpointing, persistence, time-travel
- [24] [LangGraph + PostgreSQL](https://markaicode.com/integrate/langgraph-with-postgresql/) -- Production Postgres setup
- [25] [LangGraph Persistence Guide (Fastio)](https://fast.io/resources/langgraph-persistence/) -- Comprehensive persistence guide
- [26] [Check Point Research - LangGraph Checkpointer Exploits](https://research.checkpoint.com/2026/from-sqli-to-rce-exploiting-langgraphs-checkpointer/) -- Security vulnerability analysis
- [27] [CSA Labs - LangChain/LangGraph Vulnerabilities](https://labs.cloudsecurityalliance.org/research/csa-research-note-langchain-langgraph-vulnerabilities-202603/) -- Coordinated disclosure
- [28] [LangGraph State Management Issues](https://altersquare.io/langgraph-state-management-undocumented-issues-after-commit/) -- Undocumented state management issues
- [29] [LangGraph State Reducer Conflicts](https://itsourcecode.com/runtimeerror/langgraph-state-reducer-conflicts-fix/) -- Concurrent update bugs
- [30] [LangGraph Backward Compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility) -- Schema evolution guidance
- [31] [LangGraph Breaking Change Issue #6363](https://github.com/langchain-ai/langgraph/issues/6363) -- Version compatibility bug
- [32] [CrewAI Production Guide](https://techjacksolutions.com/ai-tools/crewai/crewai-production-guide/) -- Deployment, monitoring, scaling
- [33] [CrewAI Memory in Production](https://activewizards.com/blog/crewai-memory-systems-in-production-persistence-retrieval-and-state-recovery/) -- Memory persistence and recovery
- [34] [CrewAI Explained (Atlan)](https://atlan.com/know/ai-agent/what-is-crewai/) -- Architecture, limits, context gap
- [35] [CrewAI Architecture Pitfalls](https://markaicode.com/architecture/crewai-agent-architecture/) -- Production pitfalls
- [36] [CrewAI Security Review (drel.ai)](https://drel.ai/blog/crewai-security-review) -- Security analysis
- [37] [CrewAI Enterprise](https://theneuralbase.com/crewai/learn/advanced/crewai-enterprise-what-it-adds/) -- Enterprise features
- [38] [OpenAI SDK + Temporal Integration](https://baeseokjae.github.io/posts/openai-agents-sdk-temporal-integration-2026/) -- Production durability pattern
- [39] [OpenAI Agents SDK Production (DEV)](https://dev.to/gabrielanhaia/the-openai-agents-sdk-in-production-what-it-gives-you-and-what-it-hides-16a9) -- Production limitations analysis
- [40] [Evaluating OpenAI Agents SDK (FutureAGI)](https://futureagi.com/blog/evaluating-openai-agents-sdk-2026/) -- Handoff evaluation challenges
- [41] [Google ADK vs LangGraph (Afnexis)](https://afnexis.com/articles/google-adk-vs-langgraph) -- 2026 comparison guide
- [42] [AI Agent Frameworks Ranked by Stars 2026](https://dreaming.press/posts/ai-agent-frameworks-github-ranked-by-stars-2026.html) -- GitHub star rankings
