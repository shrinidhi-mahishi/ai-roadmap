# Module 05: Agent Frameworks -- LangGraph, OpenAI Agents SDK, Google ADK, CrewAI, and Enterprise Orchestration

**Scope**: Framework primitives, state management patterns, control flow models, multi-agent coordination, persistence backends, durable execution, framework-specific failure modes, interoperability via MCP/A2A, and enterprise deployment patterns.
**Prerequisite**: Module 04 (Agent Architecture), familiarity with Python async, graph theory basics.
**Last updated**: 2026-08-21 | **Sources consulted**: 28

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Framework Router │  │  Agent Registry  │  │  Config Manager  │  │  RBAC / AuthZ    │  │
 │  │  - Select LG/OAI/ │  │  - Agent specs   │  │  - Model routing │  │  - Per-agent     │  │
 │  │    ADK/CrewAI per │  │  - Tool bindings  │  │  - Param tuning  │  │    permissions   │  │
 │  │    workload type  │  │  - Handoff maps   │  │  - Feature flags │  │  - Tool-level    │  │
 │  │  - A2A gateway    │  │  - Version mgmt   │  │  - Cost budgets  │  │    access ctrl   │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                          DATA PLANE: FRAMEWORK EXECUTION ENGINES                   │    │
 │  │                                                                                    │    │
 │  │  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐           │    │
 │  │  │  LangGraph Engine  │  │  OpenAI Agents SDK │  │  Google ADK Engine │           │    │
 │  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │           │    │
 │  │  │  │ StateGraph   │  │  │  │ Agent + Runner│  │  │  │ LlmAgent     │  │           │    │
 │  │  │  │ - Nodes      │  │  │  │ - Turn loop   │  │  │  │ - Events     │  │           │    │
 │  │  │  │ - Edges      │  │  │  │ - Handoffs    │  │  │  │ - Sessions   │  │           │    │
 │  │  │  │ - Reducers   │  │  │  │ - Guardrails  │  │  │  │ - Workflows  │  │           │    │
 │  │  │  └──────────────┘  │  │  └──────────────┘  │  │  └──────────────┘  │           │    │
 │  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │           │    │
 │  │  │  │ Checkpointer │  │  │  │ Sessions     │  │  │  │ SessionSvc   │  │           │    │
 │  │  │  │ - Superstep  │  │  │  │ - SQLAlchemy │  │  │  │ - InMemory   │  │           │    │
 │  │  │  │   snapshots  │  │  │  │ - Redis      │  │  │  │ - Database   │  │           │    │
 │  │  │  │ - Delta-only │  │  │  │ - MongoDB    │  │  │  │ - Vertex AI  │  │           │    │
 │  │  │  └──────────────┘  │  │  └──────────────┘  │  │  └──────────────┘  │           │    │
 │  │  └────────────────────┘  └────────────────────┘  └────────────────────┘           │    │
 │  │                                                                                    │    │
 │  │  ┌────────────────────┐  ┌────────────────────┐                                   │    │
 │  │  │  CrewAI Engine     │  │  MS Agent Frmwk    │                                   │    │
 │  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │                                   │    │
 │  │  │  │ Agents/Tasks │  │  │  │ Multi-lang   │  │                                   │    │
 │  │  │  │ - Crews      │  │  │  │ - .NET + Py  │  │                                   │    │
 │  │  │  │ - Flows      │  │  │  │ - Foundry    │  │                                   │    │
 │  │  │  │ - @persist   │  │  │  │ - Middleware  │  │                                   │    │
 │  │  │  └──────────────┘  │  │  └──────────────┘  │                                   │    │
 │  │  └────────────────────┘  └────────────────────┘                                   │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ MCP Gateway   │  │ Schema Valid. │  │ Sandbox Pool  │  │ A2A Gateway   │       │    │
 │  │  │ - stdio/HTTP  │  │ - JSON Schema │  │ - Docker      │  │ - Agent-to-   │       │    │
 │  │  │ - Session mgmt│  │ - Strict mode │  │ - gVisor/WASM │  │   Agent proto │       │    │
 │  │  │ - Capability  │  │ - Enum constr.│  │ - E2B/Modal   │  │ - Cross-frmwk │       │    │
 │  │  │   negotiation │  │ - Type coerce │  │ - Snapshot    │  │   delegation  │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ PostgreSQL        │  │ Redis             │  │ Temporal / Dapr   │  │ Object Store   │  │
 │  │ - LG checkpoints  │  │ - OAI sessions    │  │ - Durable exec.  │  │ - WORM audit   │  │
 │  │ - OAI SQLAlchemy  │  │ - Distributed     │  │ - Workflow state  │  │ - Crew checkpts│  │
 │  │ - CrewAI flows    │  │   session state   │  │ - HITL approvals  │  │ - Artifacts    │  │
 │  │ - Pipeline mode   │  │ - Sub-ms latency  │  │ - Retry policies  │  │ - Recordings   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ OpenTelemetry     │  │ Framework Traces  │  │ Cost Tracker      │  │ Alerting       │  │
 │  │ - Spans per node  │  │ - LG supersteps   │  │ - Token usage     │  │ - Recursion    │  │
 │  │ - Agent handoffs  │  │ - OAI trace IDs   │  │ - Per-agent cost  │  │   limit alarms │  │
 │  │ - Tool latency    │  │ - ADK Cloud Trace │  │ - Budget enforce  │  │ - SL violation │  │
 │  │ - Cross-frmwk     │  │ - CrewAI metrics  │  │ - Anomaly detect  │  │ - Circuit open │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Workload Classification**: An incoming request hits the **Framework Router** in the control plane. Based on workload type (complex stateful workflow → LangGraph, triage/handoff → OpenAI Agents SDK, GCP-native → ADK, team-of-specialists → CrewAI), the router dispatches to the appropriate execution engine.

**Step 2 — Agent Resolution**: The **Agent Registry** resolves the agent specification — tool bindings, handoff maps, model configuration. The **Config Manager** applies model routing rules (cost-tiered: Haiku for classification, Sonnet for generation, Opus for reasoning) and budget constraints.

**Step 3 — Framework Execution**: Each engine runs its native execution model:
- **LangGraph**: Compiles the StateGraph, executes nodes in superstep order, merges partial state updates via reducers, evaluates conditional edges for routing.
- **OpenAI Agents SDK**: Runner enters the turn loop — call LLM → inspect output → if tool call, execute and re-enter → if handoff, switch agent context → if text, return result.
- **Google ADK**: LlmAgent processes events through the SessionService, which filters irrelevant context, summarizes older turns, and manages token budgets before each LLM call.
- **CrewAI**: Crew orchestrator assigns Tasks to Agents in sequential or hierarchical order. Each agent executes with role/goal/backstory prompting, optional reasoning mode (reflection + planning), and delegation controls.

**Step 4 — Tool Dispatch**: Tool calls pass through the **Tool Proxy Layer**. The MCP Gateway routes to local (stdio) or remote (HTTP/SSE) tool servers. Schema Validator enforces JSON Schema constraints before execution. Sandboxes isolate code execution. The A2A Gateway enables cross-framework agent delegation.

**Step 5 — State Persistence**: After each execution step, state is persisted to the appropriate backend — LangGraph writes delta-only checkpoints to Postgres, OpenAI sessions save to Redis/SQLAlchemy, ADK updates the SessionService, CrewAI persists via `@persist` to SQLite. Durable execution integrations (Temporal, Dapr) wrap long-running workflows for crash recovery.

**Step 6 — Telemetry**: OpenTelemetry collectors capture spans per node/turn/event, tool latencies, token usage, and cost metrics. Framework-specific traces (LangGraph superstep IDs, OpenAI trace IDs, ADK Cloud Trace) feed into the unified telemetry plane for cross-framework observability.

---

## 2. Core Mechanics & Algorithms

### 2.1 Framework Primitive Comparison

| Dimension | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|-----------|-----------|-------------------|------------|--------|
| **Agent unit** | Node (Python function) | Agent (LLM + tools + handoffs) | LlmAgent / WorkflowAgent | Agent (role + goal + backstory) |
| **State model** | TypedDict with reducers | Session (list of input items) | SessionService (events + KV state) | Structured (Pydantic) or dict |
| **Control flow** | Conditional edges + START/END | Turn loop + handoffs | Event actions + graph routes | Sequential / hierarchical process |
| **Persistence** | Checkpointer (delta-only) | Session backends (6+ adapters) | SessionService (3 adapters) | @persist + crew checkpoints |
| **Multi-agent** | Subgraphs + shared state | Handoffs + agents-as-tools | Agent routing + A2A | Crews + delegation |
| **HITL** | `interrupt()` at any node | Guardrail tripwires + approval gates | Action confirmation | `@human_feedback` + `human_input=True` |
| **Abstraction level** | Low (graph primitives) | Medium (opinionated turn loop) | Medium-high (managed context) | High (role-playing metaphor) |
| **Learning curve** | Steep — graph theory required | Gentle — 4 primitives | Moderate — event-driven model | Gentle — declarative roles |

### 2.2 State Management Deep Dive

#### LangGraph: Reducer-Based State

State is a typed dictionary. Each key has an associated **reducer** that defines how partial updates merge:

```python
from typing import Annotated
from langgraph.graph import StateGraph, START, END
from operator import add

class AgentState(TypedDict):
    messages: Annotated[list, add]      # append reducer — new messages extend list
    next_agent: str                      # overwrite reducer (default) — last write wins
    tool_results: Annotated[list, add]  # append reducer
    iteration_count: int                 # overwrite reducer
```

**Superstep execution**: All nodes in a superstep run concurrently. Their partial state updates are collected, then merged via reducers. If two concurrent nodes both append to `messages`, the `add` reducer concatenates both lists. If two nodes both set `next_agent`, last-write-wins applies — a race condition the developer must prevent through graph design.

#### OpenAI Agents SDK: Session-Based State

State is a chronological list of `TResponseInputItem` objects (messages, tool calls, tool results, handoff events). Sessions abstract storage:

```python
from agents import Agent, Runner
from agents.extensions.sessions import SQLAlchemySession

session = SQLAlchemySession("postgresql://...", agent)
result = await Runner.run(agent, "Process this order", session_id="order-123")
# Session auto-retrieves history pre-run, auto-persists post-run
```

**History compaction**: `nest_handoff_history=True` wraps prior agent's history into a summary segment when handing off, preventing token explosion across long handoff chains.

#### Google ADK: Event-Driven Context Management

ADK's distinguishing feature is active context management. Unlike frameworks that blindly pass full history, ADK filters irrelevant events, summarizes older turns, and lazy-loads artifacts:

```python
from google.adk import LlmAgent, SequentialAgent, ParallelAgent

research_agent = LlmAgent(
    name="researcher",
    model="gemini-flash-latest",
    instruction="Research the given topic thoroughly",
    tools=[web_search, document_reader]
)
# ADK automatically tracks token usage per event and evicts low-value context
```

#### CrewAI: Decorator-Based Persistence

```python
from crewai.flow.flow import Flow, start, listen, router
from crewai.flow.persistence import persist, SQLiteFlowPersistence

@persist(SQLiteFlowPersistence())
class ResearchFlow(Flow):
    @start()
    def gather_requirements(self):
        return {"topic": self.state["input"]}

    @listen(gather_requirements)
    def execute_research(self, requirements):
        crew = Crew(agents=[analyst], tasks=[research_task])
        return crew.kickoff(inputs=requirements)

    @router(execute_research)
    def quality_gate(self, result):
        if result.score > 0.8:
            return "publish"
        return "revise"
```

### 2.3 Control Flow Patterns

#### LangGraph: Conditional Edges

```python
def route_by_intent(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if "refund" in last_message.content.lower():
        return "refund_agent"
    elif "technical" in last_message.content.lower():
        return "tech_support"
    return "general_agent"

graph.add_conditional_edges("classifier", route_by_intent, {
    "refund_agent": "refund_node",
    "tech_support": "tech_node",
    "general_agent": "general_node"
})
```

Supports cycles (node A → B → A) with `recursion_limit` as a safety valve. The `Command` primitive enables dynamic routing — a node can send messages to arbitrary other nodes at runtime, bypassing static edge definitions.

#### OpenAI Agents SDK: Handoffs

```python
refund_agent = Agent(name="Refund Specialist", instructions="Handle refund requests...")
tech_agent = Agent(name="Tech Support", instructions="Resolve technical issues...")

triage_agent = Agent(
    name="Triage",
    instructions="Route customers to the right specialist",
    handoffs=[refund_agent, tech_agent]
)
# Handoffs appear as tools: transfer_to_refund_specialist, transfer_to_tech_support
# LLM chooses which handoff to invoke based on conversation context
```

Handoffs support `input_filter` to modify conversation history before passing to the receiving agent, `on_handoff` callbacks for logging/metrics, and dynamic enable/disable.

#### Google ADK: Workflow Agents

```python
from google.adk import SequentialAgent, ParallelAgent, LoopAgent

pipeline = SequentialAgent(
    name="document_processor",
    sub_agents=[
        ParallelAgent(name="extractors", sub_agents=[
            text_extractor, image_extractor, table_extractor
        ]),
        LoopAgent(name="quality_loop", sub_agents=[
            validator, corrector
        ], max_iterations=3),
        publisher
    ]
)
```

ADK 2.0 adds explicit graph-based workflows with routes, human-in-the-loop integration, and hybrid deterministic + adaptive reasoning.

#### CrewAI: Process Types

**Sequential**: Tasks execute in order. Output of task N becomes context for task N+1.

**Hierarchical**: A manager agent (auto-created or specified) coordinates delegation. The manager decides which agent handles which task based on agent capabilities and task requirements. Risk: delegation loops when `allow_delegation=True` — Agent A delegates to B, B delegates back to A, consuming up to `max_iter=20` LLM calls before stopping.

### 2.4 Multi-Agent Coordination Patterns

| Pattern | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---------|-----------|-------------------|------------|--------|
| **Supervisor** | Parent graph routes to subgraphs via conditional edges | Triage agent with handoffs to specialists | Agent routing with delegation | Hierarchical process with manager |
| **Swarm** | Peer nodes with shared state and `Command` for dynamic routing | Handoff chains — each agent can hand off to any other | Event-driven multi-agent with A2A | Sequential with delegation enabled |
| **Hierarchical** | Nested subgraphs (team lead → sub-team) | Agents-as-tools (manager calls specialists as tools) | SequentialAgent wrapping ParallelAgent | Crews within Flows |
| **Pipeline** | Linear graph: A → B → C → END | Not native — simulated via sequential handoffs | SequentialAgent | Sequential process |

### 2.5 Tool Integration Comparison

| Aspect | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|--------|-----------|-------------------|------------|--------|
| **Native tools** | Python functions in nodes | Function tools (auto-schema) | Function, OpenAPI, MCP tools | BaseTool subclass |
| **MCP support** | Via LangChain adapter | Built-in MCP client | Native MCP integration | Via toolkit connectors |
| **Schema gen** | Manual or LangChain | Automatic from type hints | Automatic from docstrings | Manual `_run()` override |
| **Error handling** | Developer-implemented | `tool_not_found_behavior` returns error to model for self-correction | Action confirmation workflow | `max_iter` retry loop |
| **Sandboxing** | External (Docker, E2B) | Built-in sandbox agents (Docker/Unix) | External recommended | External (E2B, Modal) |

---

## 3. Token Economics & NFR Analysis

### 3.1 Framework Overhead Cost Model

**Base cost formula** (per 1k agent runs):

```
Total cost = (base_LLM_tokens + framework_overhead_tokens) × model_price_per_token × avg_turns × 1000

Where framework_overhead_tokens varies:
  LangGraph:        ~0 extra tokens (developer-controlled prompts)
  OpenAI Agents SDK: +50–100 tokens per handoff target (tool schema injection)
  Google ADK:       −10–30% tokens (context compression savings)
  CrewAI:           +200–500 tokens per agent (role/goal/backstory system prompt)
```

**Worked example** — 3-agent customer service pipeline, Claude Sonnet 4 ($3/$15 per MTok), avg 4 turns/run, 2k input + 500 output tokens/turn:

| Framework | Overhead/run | Input cost/1k | Output cost/1k | Total/1k runs |
|-----------|-------------|---------------|----------------|---------------|
| LangGraph | +0 tokens | $24.00 | $30.00 | **$54.00** |
| OpenAI SDK | +200 tokens (2 handoffs) | $24.60 | $30.00 | **$54.60** |
| Google ADK | −600 tokens (compression) | $22.20 | $30.00 | **$52.20** |
| CrewAI | +1200 tokens (3 agents × 400) | $27.60 | $30.00 | **$57.60** |

> Assumptions: 3 agents, 4 turns avg, Sonnet 4 pricing. Actual costs vary by task complexity, model choice, and caching strategy.

### 3.2 Adoption Metrics (August 2026)

| Framework | GitHub Stars | PyPI Version | Enterprise Users | Certified Devs |
|-----------|-------------|-------------|------------------|----------------|
| AutoGen (maintenance) | 60.6k | 0.7.5 | — | — |
| CrewAI | 57.4k | 1.15.17 | AMP Suite customers | 100,000+ |
| LangGraph | 40.1k | 1.2.11 | Klarna, Replit, Elastic | — |
| OpenAI Agents SDK | 28.8k | 0.22.0 | — | — |
| Google ADK | 21.2k | 2.7.1 | GCP enterprises | — |
| MS Agent Framework | 13.0k | 1.14.0 | Azure enterprises | — |

AutoGen's 60.6k stars reflect cumulative interest before maintenance mode; active development shifted to MS Agent Framework.

### 3.3 Benchmark Performance

**SWE-bench insight**: Framework choice does not meaningfully affect task accuracy — the same model achieves similar SWE-bench scores regardless of scaffold. Claude Sonnet 4 leads at ~72% resolved, GPT-4.1 at ~55%, achieved via direct API + custom scaffolding. Framework value is in developer productivity, state management, and operational concerns — not raw accuracy.

**CrewAI vendor claim**: 5.76× faster execution than LangGraph on QA tasks with higher evaluation scores on coding tasks. Independently unverified — treat as directional, not authoritative.

### 3.4 Framework-Specific Caching Strategies

| Framework | Caching Mechanism | Hit Rate Impact | Implementation |
|-----------|-------------------|-----------------|----------------|
| LangGraph | LLM provider caching (Anthropic prompt cache) | Up to 90% input cost reduction on repeated prefixes | Automatic with compatible providers |
| OpenAI SDK | `previous_response_id` server-side chaining | Eliminates resending conversation history | `Runner.run()` with sessions |
| OpenAI SDK | `OpenAIResponsesCompactionSession` | 40-60% token reduction on long conversations | `responses.compact` API |
| Google ADK | Apigee AI Gateway model-level caching | Variable — depends on context pattern repetition | Platform-managed |
| CrewAI | Tool result caching (`cache=True` default) | Eliminates duplicate tool calls within a run | Per-tool configuration |
| CrewAI | LanceDB semantic memory | Cross-run deduplication of similar queries | `memory=True` on Crew |

### 3.5 Latency SLA Targets

| Metric | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|--------|-----------|-------------------|------------|--------|
| **p50 per turn** | 350ms | 300ms | 280ms | 400ms |
| **p95 per turn** | 1.5s | 1.2s | 1.0s | 2.0s |
| **p99 per turn** | 4.0s | 3.5s | 3.0s | 5.0s |
| **Cold start** | 50-200ms (graph compile) | <50ms | <100ms | 100-300ms (agent init) |
| **Checkpoint write** | 5-15ms (Postgres pipeline) | 2-10ms (Redis) / 10-30ms (SQLAlchemy) | 5-20ms (VertexAI) | 5-15ms (SQLite) |

**Mitigation strategies by tier**:
- **p50**: Model routing — use Haiku/Flash for classification nodes, Sonnet for generation. LangGraph: parallelize independent nodes in same superstep. ADK: leverage context compression to reduce prompt size.
- **p95**: Streaming responses to reduce perceived latency. LangGraph: optimize reducer complexity. OpenAI SDK: enable `run_in_parallel=True` for guardrails. CrewAI: reduce `max_iter` for well-defined tasks.
- **p99**: Circuit breakers (Section 4.2) to fast-fail on degraded backends. Timeout budgets per node/turn (LangGraph: per-node timeout; OpenAI SDK: `max_turns`; CrewAI: `execution_timeout`). Fallback to cached/deterministic responses.

### 3.6 Throughput & Back-Pressure

**Capacity formula**:
```
max_concurrent_agents = min(
    api_rpm_limit / avg_calls_per_agent_run,
    memory_budget_gb / per_agent_memory_mb × 1024,
    db_connection_pool_size / connections_per_agent
)
```

**Back-pressure mechanisms per framework**:

| Framework | Mechanism | Configuration |
|-----------|-----------|---------------|
| LangGraph | Recursion limit | `recursion_limit=25` (default) — raises `GraphRecursionError` |
| LangGraph | LangGraph Cloud task queues | Horizontal scaling with double-texting support |
| OpenAI SDK | `max_turns` | Per-run turn cap — raises `MaxTurnsExceeded` |
| OpenAI SDK | Guardrail tripwires | Fail-fast on budget/safety violations before consuming tokens |
| Google ADK | Token budget tracking | Per-session token accounting with eviction |
| Google ADK | Apigee AI Gateway | Rate limiting + load balancing across model endpoints |
| CrewAI | `max_iter` per agent | Default 20 — prevents runaway loops |
| CrewAI | `max_rpm` per agent | API rate limit cap per agent instance |

### 3.7 NFR Trade-offs

| NFR | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|-----|-----------|-------------------|------------|--------|
| **Availability** | Self-managed; Cloud adds HA | Depends on backend (Redis HA, Postgres replicas) | GCP-managed HA on Agent Runtime/Cloud Run | Enterprise AMP for managed HA |
| **RPO** | Superstep-level (every checkpoint) | Turn-level (every session save) | Event-level | Task-level (checkpoint events) |
| **RTO** | Resume from last checkpoint (<1s) | Reload session + re-run (<5s) | Session rewind (<3s) | `from_checkpoint()` (<5s) |
| **Compliance** | Audit via checkpoint history | Tracing + encrypted sessions | GCP compliance certifications | Enterprise RBAC + security config |

**Key trade-off — Control vs. Convenience**:
- LangGraph offers maximum control at the cost of operational complexity. The developer manages every aspect of state, persistence, and execution.
- OpenAI Agents SDK balances simplicity with extensibility — few primitives to learn, but deep customization via sessions, guardrails, and durable execution integrations.
- ADK optimizes for GCP-native deployments — one-command deploy trades portability for zero-ops.
- CrewAI prioritizes developer velocity — high-level abstractions sacrifice fine-grained control for rapid prototyping.

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution Patterns

#### LangGraph: Checkpoint-Based Recovery

```
Execution Timeline:
  Node A ──checkpoint──▶ Node B ──checkpoint──▶ Node C ──💥 crash
                                                          │
                                    Resume from ◀─────────┘
                                    Node C checkpoint
```

- Checkpointer saves state after every superstep (configurable granularity).
- Only changed channel values are stored (delta compression), not full state snapshots.
- **Postgres pipeline mode**: Batches multiple checkpoint writes into a single round-trip, reducing I/O by 3-5×.
- **Time travel**: Any historical checkpoint can be loaded, inspected, or forked into an alternative execution branch.
- **Thread forking**: Create divergent execution paths from any point in history — essential for A/B testing agent strategies.

#### OpenAI Agents SDK: Durable Execution Integrations

Four officially supported integrations:

| Integration | Mechanism | Best For |
|-------------|-----------|----------|
| **Temporal** | Workflow orchestration with HITL approval steps | Complex multi-step workflows with human gates |
| **Dapr** | CNCF sidecar with 30+ backend stores, auto-retry | Cloud-native microservice deployments |
| **Restate** | Single-binary runtime, durable function calls | Lightweight self-hosted durability |
| **DBOS** | SQLite/Postgres-backed reliability | Simple persistence with minimal infrastructure |

All integrations preserve progress across process restarts and support tool approval workflows.

#### CrewAI: Dual-Layer Persistence

**Layer 1 — Flow persistence** (`@persist`): SQLite-based with transaction integrity. Two resume modes:
- **Resume**: Continue from existing state snapshot (same execution ID).
- **Fork**: Create new execution from a prior state snapshot (new execution ID, shared history).

**Layer 2 — Crew checkpointing** (`checkpoint=True`): Saves at configurable events (`on_events=["task_completed"]`). Resume via `Crew.from_checkpoint("path")`. Max checkpoint count is configurable to bound storage.

### 4.2 Circuit Breaker Pattern for Framework Failures

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Fast-fail│    │ Probe       │
         │ execution│    │ all calls│    │ 2 test runs │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │          │
              │     after 30s
              │     recovery timeout
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 failures within 60s window (sliding window counter).
- **Open duration**: 30s recovery timeout with exponential backoff (30s → 60s → 120s on repeated trips).
- **Half-Open → Closed**: 2 consecutive successful probe requests.
- **Half-Open → Open**: Any probe failure immediately re-opens the circuit.

#### 4.2.2 Framework-Specific Circuit Breaker Applications

| Failure Type | Framework | Breaker Scope | Fallback Strategy |
|-------------|-----------|---------------|-------------------|
| LLM API 429/500 | All | Per-model-endpoint | Route to backup model (Sonnet → Haiku, GPT-4.1 → GPT-4.1-mini) |
| Checkpoint write failure | LangGraph | Per-checkpointer-backend | Fall back to MemorySaver (volatile) + alert |
| Session backend unavailable | OpenAI SDK | Per-session-backend | In-memory session + persist-on-recovery |
| Tool server timeout | All (via MCP) | Per-tool-server | Return cached last-known-good result |
| Recursion limit hit | LangGraph | Per-graph-instance | Return partial result + escalate to human |
| Delegation loop | CrewAI | Per-crew | Force `allow_delegation=False` + log incident |
| Context overflow | ADK/CrewAI | Per-session | Aggressive summarization + flag quality degradation |

### 4.3 Failure Taxonomy

| Failure | Class | Framework(s) | Detection | Mitigation |
|---------|-------|-------------|-----------|------------|
| `GraphRecursionError` | **Transient** (if caused by complex input) / **Permanent** (if graph design flaw) | LangGraph | Iteration counter ≥ `recursion_limit` | Increase limit for transient; redesign graph for permanent |
| `MaxTurnsExceeded` | **Transient** | OpenAI SDK | Turn counter ≥ `max_turns` | Increase limit or add early-exit logic |
| Delegation loop | **Permanent** (design flaw) | CrewAI | `max_iter` exhaustion without progress | Disable delegation or redesign agent roles |
| Checkpoint serialization failure | **Permanent** | LangGraph | `SerializationError` on non-JSON-serializable state | Implement custom `SerializationProtocol` |
| Session backend atomicity failure | **Transient** | OpenAI SDK (MongoDB) | Oversized batch rejected atomically | Reduce batch size; retry with smaller payload |
| Context window overflow | **Transient** | All | Token count exceeds model limit | Auto-summarization (CrewAI), context filtering (ADK), manual truncation (LangGraph) |
| Version incompatibility | **Permanent** | ADK (v1.x → v2.0) | Session read failure on older version | Coordinate upgrades; use forward-compatible session format |
| Guardrail tripwire (parallel) | **Transient** | OpenAI SDK | Tripwire fires after agent has consumed tokens | Use `run_in_parallel=False` for cost-sensitive scenarios |
| JSON crew code execution | **Permanent** (security) | CrewAI | `{"python": "module.attribute"}` in untrusted config | Only run JSON crew projects from trusted sources |
| State merge conflict | **Transient** | LangGraph | Silent data loss from incorrect reducer | Define explicit reducers for all concurrently-written keys |
| Conversation explosion | **Permanent** (architecture) | AutoGen | Quadratic token growth with turn count | Migrate to MS Agent Framework or add summarization |

### 4.3.1 Idempotency in Durable Execution

Durable execution frameworks (Temporal, Dapr, Restate) replay activities after crashes. If a tool call has side effects (send email, create ticket, charge payment), replayed calls must be **idempotent** — producing the same result whether executed once or many times.

**Delivery semantics by integration**:

| Integration | Delivery Guarantee | Idempotency Mechanism |
|-------------|-------------------|----------------------|
| **Temporal** | At-least-once (activities replay on worker failure) | Idempotency key in activity input; tool server deduplicates by key |
| **Dapr** | At-least-once (auto-retry on transient failure) | Sidecar provides exactly-once via state store transactions |
| **Restate** | Effectively-once (journaled function calls) | Runtime journals call results; replays return cached result |
| **DBOS** | At-least-once (Postgres-backed retry) | Application-level idempotency key per tool invocation |

**Implementation pattern** — idempotency key injection at the Tool Proxy Layer:

```
Tool call from agent:  create_ticket(title="Bug #42", priority="high")
                                    │
                          ┌─────────▼──────────┐
                          │ Idempotency Guard   │
                          │ key = hash(agent_id │
                          │   + run_id + call_  │
                          │   sequence_number)  │
                          └─────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ Tool Server (Jira MCP)       │
                     │ IF key in idempotency_store: │
                     │   RETURN cached_result       │
                     │ ELSE:                        │
                     │   execute + store result     │
                     └─────────────────────────────┘
```

The key is deterministic: `hash(agent_id + run_id + call_sequence_number)`. On replay, the same key is generated, and the tool server returns the cached result without re-executing.

### 4.3.2 Poison-Pill Detection and Quarantine

A **poison pill** is an input that deterministically causes agent failure regardless of retry count — e.g., an input that always triggers context overflow, serialization errors, or infinite delegation loops. Without detection, these inputs consume retry budgets indefinitely.

**Detection and isolation pattern**:

```
                    ┌──────────────┐
     Input ────────▶│ Agent Runner │──── success ────▶ Result
                    └──────┬───────┘
                           │ failure
                           ▼
                    ┌──────────────┐
                    │ Retry Counter│
                    │ attempt < 3  │──── retry ──────▶ Agent Runner
                    └──────┬───────┘
                           │ attempt ≥ 3
                           ▼
                    ┌──────────────┐
                    │ Poison-Pill  │
                    │ Classifier   │
                    │ - Same error │
                    │   3x in row? │
                    │ - Known bad  │
                    │   pattern?   │
                    └──────┬───────┘
                           │ classified as poison
                           ▼
                    ┌──────────────┐
                    │ Dead-Letter  │
                    │ Queue (DLQ)  │
                    │ - Quarantine │
                    │ - Alert ops  │
                    │ - Audit log  │
                    └──────────────┘
```

**Framework-specific poison-pill patterns**:

| Framework | Poison-Pill Trigger | Detection Heuristic | Quarantine Action |
|-----------|--------------------|--------------------|-------------------|
| LangGraph | Input that always hits `recursion_limit` | Same `GraphRecursionError` on 3 consecutive retries with identical state at failure point | Route to DLQ; flag graph for design review |
| OpenAI SDK | Input that always triggers guardrail tripwire | Same tripwire on 3 retries with no model self-correction | Route to DLQ; escalate to human review |
| CrewAI | Input that causes delegation loop every time | `max_iter` exhausted with zero task progress across 3 retries | Route to DLQ; force `allow_delegation=False` on retry |
| All | Input causing serialization crash | Identical `SerializationError` stack trace on 3 retries | Route to DLQ; log offending state shape for schema fix |

The retry budget (3 attempts) is configurable per deployment. After quarantine, ops receives an alert with the input, error traces, and agent state at failure — enabling root cause analysis without the poison pill continuing to consume compute.

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Framework Security Feature Matrix

| Capability | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|-----------|-----------|-------------------|------------|--------|
| **HITL gates** | `interrupt()` at any node | 3-tier guardrails + tool approval | Action confirmation workflow | `@human_feedback` + `human_input=True` |
| **Input validation** | Developer-implemented | Input guardrails (parallel or blocking) | Built-in safety mechanisms | Task-level guardrails (fn or LLM) |
| **Output validation** | Developer-implemented | Output guardrails with tripwires | — | Guardrail chains with max retries |
| **Tool-level RBAC** | Manual per-node | `ToolExecutionConfig` pre-approval | IAM + tool authentication | `allow_delegation=False` default |
| **Sandboxing** | External (Docker, E2B) | Built-in sandbox agents (Docker/Unix with snapshot/restore) | External recommended | External (E2B, Modal) |
| **Encryption** | Developer-implemented | `EncryptedSession` with TTL | GCP-managed encryption | Enterprise `security_config` |
| **Audit trail** | Checkpoint history (full state at every superstep) | Built-in tracing (spans, trace IDs, group IDs) | Cloud Trace integration | Enterprise AMP monitoring |

#### 4.4.2 Zero-Trust Tool Execution Architecture

Regardless of framework, production deployments should enforce:

1. **Transport security**: mTLS between agent process and MCP tool servers. All framework-to-tool communication over encrypted channels.
2. **Capability negotiation**: MCP's capability handshake ensures agents only see tools they're authorized to use. Tool schemas are filtered by the RBAC engine before injection into agent prompts.
3. **Least privilege**: Each agent role gets a scoped tool set. LangGraph: tools are node-scoped (only available in specific graph nodes). OpenAI SDK: tools are agent-scoped (per-agent tool lists). CrewAI: tools are agent-scoped with delegation controls.
4. **PII filtering pipeline**: Detection (regex + NER classifiers) → Redaction (mask/hash/remove) → Audit event (immutable log of what was redacted, by whom, when). Applied at the Tool Proxy Layer before results re-enter the agent context.
5. **Immutable audit logs**: Every agent decision, tool call, handoff, and state transition logged to WORM storage. Chain-of-custody for compliance (SOC2, HIPAA, GDPR).

---

## 5. Production Enterprise Code

### 5.1 LangGraph: Stateful Agent with Checkpointing and HITL

```python
from typing import Annotated, TypedDict
from operator import add
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt, Command
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage


class OrderState(TypedDict):
    messages: Annotated[list, add]
    order_id: str
    refund_amount: float
    approved: bool


llm = ChatAnthropic(model="claude-sonnet-4-20250514")


def classify_intent(state: OrderState) -> dict:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}


def calculate_refund(state: OrderState) -> dict:
    order_id = state["order_id"]
    amount = lookup_order_total(order_id) * 0.9  # 90% refund policy
    return {"refund_amount": amount}


def human_approval(state: OrderState) -> dict:
    decision = interrupt({
        "question": f"Approve refund of ${state['refund_amount']:.2f} for order {state['order_id']}?",
        "options": ["approve", "deny"]
    })
    return {"approved": decision == "approve"}


def process_refund(state: OrderState) -> dict:
    if state["approved"]:
        execute_refund(state["order_id"], state["refund_amount"])
        return {"messages": [AIMessage(content=f"Refund of ${state['refund_amount']:.2f} processed.")]}
    return {"messages": [AIMessage(content="Refund denied by reviewer.")]}


def route_after_approval(state: OrderState) -> str:
    return "process_refund" if state.get("approved") else END


graph = StateGraph(OrderState)
graph.add_node("classify", classify_intent)
graph.add_node("calculate", calculate_refund)
graph.add_node("approve", human_approval)
graph.add_node("process_refund", process_refund)

graph.add_edge(START, "classify")
graph.add_edge("classify", "calculate")
graph.add_edge("calculate", "approve")
graph.add_conditional_edges("approve", route_after_approval, {
    "process_refund": "process_refund",
    END: END
})
graph.add_edge("process_refund", END)

checkpointer = PostgresSaver.from_conn_string("postgresql://user:pass@localhost/agents")
app = graph.compile(checkpointer=checkpointer)

# Run with thread ID for persistence
config = {"configurable": {"thread_id": "order-456"}}
result = app.invoke(
    {"messages": [HumanMessage(content="I want a refund for order #456")], "order_id": "456"},
    config=config
)
# Execution pauses at human_approval interrupt
# Resume after human decision:
# result = app.invoke(Command(resume="approve"), config=config)
```

### 5.2 OpenAI Agents SDK: Multi-Agent Triage with Guardrails

```python
from agents import Agent, Runner, InputGuardrail, OutputGuardrail, GuardrailFunctionOutput
from agents.extensions.sessions import SQLAlchemySession
from pydantic import BaseModel


class SafetyCheck(BaseModel):
    is_safe: bool
    reason: str


safety_agent = Agent(
    name="Safety Classifier",
    instructions="Determine if the user input is safe and appropriate for processing.",
    output_type=SafetyCheck
)


async def safety_guardrail(ctx, agent, input_data):
    result = await Runner.run(safety_agent, input_data, context=ctx.context)
    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered=not result.final_output.is_safe
    )


billing_agent = Agent(
    name="Billing Specialist",
    instructions="Handle billing inquiries, payment issues, and invoice requests.",
    tools=[lookup_invoice, process_payment, generate_receipt]
)

tech_agent = Agent(
    name="Technical Support",
    instructions="Resolve technical issues with step-by-step troubleshooting.",
    tools=[check_system_status, run_diagnostics, create_ticket]
)

triage_agent = Agent(
    name="Customer Triage",
    instructions=(
        "You are the first point of contact. Understand the customer's need "
        "and route to the appropriate specialist. Never attempt to resolve "
        "issues yourself — always hand off."
    ),
    handoffs=[billing_agent, tech_agent],
    input_guardrails=[InputGuardrail(guardrail_function=safety_guardrail)]
)


async def handle_customer_request(user_message: str, session_id: str):
    session = SQLAlchemySession(
        "postgresql://user:pass@localhost/agents", triage_agent
    )
    result = await Runner.run(
        triage_agent,
        user_message,
        session_id=session_id,
        max_turns=15
    )
    return result.final_output
```

### 5.3 Google ADK: Event-Driven Research Pipeline

```python
from google.adk import LlmAgent, SequentialAgent, ParallelAgent, LoopAgent


web_researcher = LlmAgent(
    name="web_researcher",
    model="gemini-2.5-flash",
    instruction=(
        "Search the web for recent information on the given topic. "
        "Return structured findings with source URLs."
    ),
    tools=[google_search, web_fetch]
)

paper_analyst = LlmAgent(
    name="paper_analyst",
    model="gemini-2.5-pro",
    instruction=(
        "Analyze academic papers and technical documentation. "
        "Extract key claims, methodologies, and results."
    ),
    tools=[arxiv_search, pdf_reader]
)

synthesizer = LlmAgent(
    name="synthesizer",
    model="gemini-2.5-pro",
    instruction=(
        "Synthesize findings from web research and paper analysis "
        "into a coherent report with citations."
    )
)

fact_checker = LlmAgent(
    name="fact_checker",
    model="gemini-2.5-flash",
    instruction=(
        "Verify each claim in the report against source material. "
        "Flag unverified or contradictory claims."
    ),
    tools=[web_fetch]
)

research_pipeline = SequentialAgent(
    name="research_pipeline",
    sub_agents=[
        ParallelAgent(
            name="gather_sources",
            sub_agents=[web_researcher, paper_analyst]
        ),
        synthesizer,
        LoopAgent(
            name="verify_loop",
            sub_agents=[fact_checker, synthesizer],
            max_iterations=2
        )
    ]
)
```

### 5.4 CrewAI: Production Flow with Persistence and Guardrails

```python
from crewai import Agent, Task, Crew
from crewai.flow.flow import Flow, start, listen, router
from crewai.flow.persistence import persist, SQLiteFlowPersistence
from pydantic import BaseModel


class AnalysisState(BaseModel):
    company: str = ""
    research: str = ""
    analysis: str = ""
    quality_score: float = 0.0
    revision_count: int = 0


researcher = Agent(
    role="Market Research Analyst",
    goal="Gather comprehensive market data on the target company",
    backstory="Senior analyst with 15 years of experience in equity research",
    tools=[web_search, sec_filing_reader, financial_data_api],
    max_iter=10,
    max_rpm=30,
    allow_delegation=False
)

analyst = Agent(
    role="Financial Analyst",
    goal="Produce investment-grade analysis with clear recommendations",
    backstory="CFA charterholder specializing in technology sector valuations",
    tools=[financial_model, comparable_analysis],
    max_iter=10,
    max_rpm=30,
    allow_delegation=False
)


def quality_guardrail(output):
    required_sections = ["Executive Summary", "Valuation", "Risks", "Recommendation"]
    missing = [s for s in required_sections if s not in output.raw]
    if missing:
        return (False, f"Missing sections: {', '.join(missing)}")
    return (True, output)


@persist(SQLiteFlowPersistence(db_path="flows.db"))
class CompanyAnalysisFlow(Flow[AnalysisState]):

    @start()
    def research_company(self):
        research_task = Task(
            description=f"Research {self.state.company}: financials, market position, competitors",
            expected_output="Structured research report with data points and sources",
            agent=researcher
        )
        crew = Crew(agents=[researcher], tasks=[research_task], verbose=False)
        result = crew.kickoff()
        self.state.research = result.raw
        return result

    @listen(research_company)
    def analyze_company(self, research_result):
        analysis_task = Task(
            description=f"Analyze {self.state.company} using the research provided",
            expected_output="Investment analysis with valuation, risks, and recommendation",
            agent=analyst,
            context=[research_result],
            guardrail=quality_guardrail,
            max_guardrail_retries=2
        )
        crew = Crew(agents=[analyst], tasks=[analysis_task], verbose=False)
        result = crew.kickoff()
        self.state.analysis = result.raw
        return result

    @router(analyze_company)
    def quality_check(self, analysis_result):
        self.state.revision_count += 1
        if self.state.revision_count > 3:
            return "publish"
        score = evaluate_analysis_quality(analysis_result.raw)
        self.state.quality_score = score
        if score >= 0.85:
            return "publish"
        return "revise"

    @listen("revise")
    def revise_analysis(self):
        return self.analyze_company(self.state.research)

    @listen("publish")
    def publish_report(self):
        save_to_database(self.state.company, self.state.analysis, self.state.quality_score)
        return {"status": "published", "quality": self.state.quality_score}


# Usage:
# flow = CompanyAnalysisFlow()
# flow.kickoff(inputs={"company": "NVIDIA"})
# On crash: flow resumes from last @persist snapshot automatically
```

### 5.5 Framework-Agnostic Wrapper Pattern

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class AgentResult:
    output: str
    token_usage: dict
    latency_ms: float
    framework: str
    trace_id: str


class AgentRunner(ABC):
    @abstractmethod
    async def run(self, prompt: str, session_id: str, **kwargs) -> AgentResult:
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...


class LangGraphRunner(AgentRunner):
    def __init__(self, compiled_graph, checkpointer):
        self.graph = compiled_graph
        self.checkpointer = checkpointer

    async def run(self, prompt: str, session_id: str, **kwargs) -> AgentResult:
        config = {"configurable": {"thread_id": session_id}}
        start = time.monotonic()
        result = await self.graph.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=config
        )
        latency = (time.monotonic() - start) * 1000
        return AgentResult(
            output=result["messages"][-1].content,
            token_usage=extract_usage(result),
            latency_ms=latency,
            framework="langgraph",
            trace_id=config["configurable"]["thread_id"]
        )

    async def health_check(self) -> bool:
        return await self.checkpointer.ping()


class OpenAIAgentRunner(AgentRunner):
    def __init__(self, agent, session_backend):
        self.agent = agent
        self.session = session_backend

    async def run(self, prompt: str, session_id: str, **kwargs) -> AgentResult:
        start = time.monotonic()
        result = await Runner.run(
            self.agent, prompt,
            session_id=session_id,
            max_turns=kwargs.get("max_turns", 10)
        )
        latency = (time.monotonic() - start) * 1000
        return AgentResult(
            output=str(result.final_output),
            token_usage=extract_usage(result),
            latency_ms=latency,
            framework="openai_agents_sdk",
            trace_id=result.trace_id
        )

    async def health_check(self) -> bool:
        return await self.session.ping()


class FrameworkRouter:
    def __init__(self):
        self.runners: dict[str, AgentRunner] = {}
        self.circuit_breakers: dict[str, CircuitBreaker] = {}

    def register(self, workload_type: str, runner: AgentRunner):
        self.runners[workload_type] = runner
        self.circuit_breakers[workload_type] = CircuitBreaker(
            failure_threshold=5, recovery_timeout=30, probe_count=2
        )

    async def dispatch(self, workload_type: str, prompt: str, session_id: str) -> AgentResult:
        cb = self.circuit_breakers[workload_type]
        if cb.state == "open":
            raise FrameworkUnavailableError(f"{workload_type} circuit is open")
        if cb.state == "half-open":
            pass  # allow probe request through
        runner = self.runners[workload_type]
        try:
            result = await runner.run(prompt, session_id)
            cb.record_success()
            return result
        except Exception as e:
            cb.record_failure()
            raise
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Multi-Workload Framework Selection

**Business context**: An enterprise deploys 5 agent workloads (customer service, document processing, code review, research, data analysis) across 3 teams. Each team has different skill sets and infrastructure preferences. Goal: minimize total cost of ownership while maximizing developer productivity and operational reliability.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                        API GATEWAY                                   │
 │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │
 │  │ Workload   │  │ Auth/AuthZ │  │ Rate Limit │  │ Cost Budget  │  │
 │  │ Classifier │  │ (OAuth2)   │  │ (per-team) │  │ Enforcer     │  │
 │  └──────┬─────┘  └────────────┘  └────────────┘  └──────────────┘  │
 └─────────┼────────────────────────────────────────────────────────────┘
           │
           ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                     FRAMEWORK EXECUTION LAYER                           │
 │                                                                         │
 │  Team A (Platform)              Team B (ML)            Team C (Product) │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
 │  │ LangGraph        │  │ Google ADK       │  │ CrewAI           │      │
 │  │ ├─ Code review   │  │ ├─ Research      │  │ ├─ Customer svc  │      │
 │  │ │  (complex DAG) │  │ │  (context-     │  │ │  (triage crew) │      │
 │  │ └─ Doc processing│  │ │   intensive)   │  │ └─ Data analysis │      │
 │  │    (pipeline)     │  │ └─ Data analysis │  │    (analyst crew)│      │
 │  └──────────────────┘  │    (parallel)     │  └──────────────────┘      │
 │                         └──────────────────┘                            │
 │                                                                         │
 │  ┌─────────────────────────────────────────────────────────────────┐    │
 │  │                   SHARED MCP TOOL SERVERS                       │    │
 │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │    │
 │  │  │ GitHub   │  │ Jira     │  │ Slack    │  │ Database │       │    │
 │  │  │ MCP Svr  │  │ MCP Svr  │  │ MCP Svr  │  │ MCP Svr  │       │    │
 │  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │    │
 │  └─────────────────────────────────────────────────────────────────┘    │
 └─────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single Framework (LangGraph) | B: Best-of-Breed per Workload | C: Framework-Agnostic Abstraction Layer |
|-----------|-------------------------------|-------------------------------|----------------------------------------|
| **Developer productivity** | ⬛⬛⬜ — All teams learn one framework; LangGraph's steep curve slows non-platform teams | ⬛⬛⬛ — Each team uses the framework matching their skill set and workload | ⬛⬛⬜ — Abstraction hides framework details but adds a layer to debug |
| **Operational complexity** | ⬛⬛⬛ — Single deployment pipeline, one monitoring stack, unified on-call | ⬛⬜⬜ — 3 deployment pipelines, 3 monitoring integrations, framework-specific expertise needed | ⬛⬛⬜ — Single deployment pipeline but abstraction layer itself needs maintenance |
| **Cost efficiency** | ⬛⬛⬜ — LangGraph's low overhead, but overkill for simple workloads (customer service) | ⬛⬛⬛ — Each framework's strengths minimize waste (ADK context compression for research, CrewAI speed for simple crews) | ⬛⬛⬜ — Abstraction overhead adds ~5-10% latency per call |
| **Migration risk** | ⬛⬛⬛ — No migration needed; single framework from day one | ⬛⬜⬜ — Framework lock-in per workload; MCP tools are portable, orchestration is not | ⬛⬛⬛ — Frameworks are swappable behind the abstraction — low lock-in |
| **Scalability** | ⬛⬛⬜ — LangGraph Cloud scales, but not all workloads need graph complexity | ⬛⬛⬛ — Each framework's native scaling (Cloud Run for ADK, AMP for CrewAI, LG Cloud) | ⬛⬛⬜ — Scaling depends on abstraction layer's ability to route efficiently |

**Recommended approach**: **B (Best-of-Breed)** with MCP as the shared tool layer.

**Decision rationale**: The 5 workloads span a wide complexity range — code review requires LangGraph's graph cycles and HITL, while customer service maps naturally to CrewAI's role-playing crew pattern. A single framework either over-engineers simple workloads or under-serves complex ones. MCP tool servers provide the interoperability layer, ensuring tools are shared across frameworks without duplication. The operational complexity cost (3 deployment pipelines) is mitigated by containerized deployments and unified telemetry via OpenTelemetry. Each team owns their framework choice, reducing cross-team dependencies.

### 6.2 Scenario: CrewAI Monolith to Hybrid LangGraph + MCP Migration

**Business context**: A company runs a monolithic CrewAI deployment handling 50K daily agent runs across 12 crews. Pain points: SQLite persistence bottleneck under concurrent load, delegation loops causing 15% of runs to hit `max_iter`, and inability to implement complex conditional workflows. Goal: migrate to LangGraph for complex workflows + MCP for tool portability, with zero downtime.

#### Component Diagram (Target State)

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                       TRAFFIC ROUTER (NGINX)                             │
 │  ┌──────────────────────────────────────────────────────────────────┐   │
 │  │  Feature Flag: route_to_langgraph = 0% → 25% → 50% → 100%      │   │
 │  └──────────┬──────────────────────────────────────┬────────────────┘   │
 └─────────────┼──────────────────────────────────────┼────────────────────┘
               │                                      │
     ┌─────────▼──────────┐             ┌─────────────▼──────────┐
     │  CrewAI (Legacy)   │             │  LangGraph (New)       │
     │  ├─ Simple crews   │             │  ├─ Complex workflows  │
     │  │  (keep running) │             │  │  (conditional DAGs) │
     │  └─ Complex crews  │             │  └─ HITL workflows     │
     │     (draining)     │             │     (interrupt-based)  │
     └─────────┬──────────┘             └─────────────┬──────────┘
               │                                      │
     ┌─────────▼──────────────────────────────────────▼──────────┐
     │                   SHARED MCP TOOL LAYER                    │
     │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
     │  │ CRM      │  │ Email    │  │ Analytics│  │ Database │  │
     │  │ MCP Svr  │  │ MCP Svr  │  │ MCP Svr  │  │ MCP Svr  │  │
     │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
     └───────────────────────────────────────────────────────────┘
     ┌───────────────────────────────────────────────────────────┐
     │                   SHARED PERSISTENCE                      │
     │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
     │  │ PostgreSQL   │  │ Redis        │  │ Temporal       │  │
     │  │ - LG checkpts│  │ - Session    │  │ - Durable exec │  │
     │  │ - CrewAI     │  │   cache      │  │ - HITL gates   │  │
     │  │   (migrated) │  │              │  │                │  │
     │  └──────────────┘  └──────────────┘  └────────────────┘  │
     └───────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Big-Bang Rewrite | B: Strangler Fig (Gradual) | C: Adapter Layer (CrewAI inside LangGraph) |
|-----------|--------------------|-----------------------------|-------------------------------------------|
| **Downtime risk** | ⬛⬜⬜ — Full cutover requires freeze; any bug blocks all 50K runs | ⬛⬛⬛ — Feature-flag routing enables incremental migration with instant rollback | ⬛⬛⬜ — Adapter bugs affect all workflows using the bridge |
| **Migration speed** | ⬛⬛⬛ — Fastest if it works; 4-6 weeks | ⬛⬛⬜ — Slowest; 12-16 weeks for full migration | ⬛⬛⬜ — Medium; 8-10 weeks but adapter maintenance is ongoing |
| **Code quality** | ⬛⬛⬛ — Clean LangGraph codebase, no legacy baggage | ⬛⬛⬜ — Temporary dual-framework complexity during transition | ⬛⬜⬜ — Permanent adapter layer adds indirection and maintenance |
| **Operational risk** | ⬛⬜⬜ — All-or-nothing; hard to validate at scale before cutover | ⬛⬛⬛ — Each crew migrates independently; issues are scoped | ⬛⬛⬜ — CrewAI quirks (delegation loops) persist inside adapter |
| **Team ramp-up** | ⬛⬜⬜ — Entire team must learn LangGraph before migration | ⬛⬛⬛ — Team learns LangGraph incrementally as crews migrate | ⬛⬛⬛ — Existing CrewAI knowledge still applies via adapter |
| **Long-term TCO** | ⬛⬛⬛ — Single framework, no adapter tax | ⬛⬛⬛ — Same end state as big-bang, just slower to reach | ⬛⬜⬜ — Permanent adapter maintenance + two framework runtimes |

**Recommended approach**: **B (Strangler Fig)** with feature-flag-controlled routing.

**Decision rationale**: At 50K daily runs, any migration failure has high blast radius. The Strangler Fig pattern mitigates this:

1. **Phase 1 (Weeks 1-3)**: Extract all CrewAI tools into MCP servers. Both CrewAI and LangGraph consume tools via MCP — this decouples tool logic from framework logic and enables parallel operation.

2. **Phase 2 (Weeks 4-8)**: Migrate crews one at a time, starting with the simplest (lowest delegation, no complex branching). Each crew gets a LangGraph equivalent deployed behind the feature flag. Route 5% → 25% → 50% → 100% traffic per crew, validating at each step.

3. **Phase 3 (Weeks 9-12)**: Migrate complex crews that were hitting delegation loops. These benefit most from LangGraph's explicit graph control — replace implicit delegation with conditional edges and `interrupt()` for human gates.

4. **Phase 4 (Weeks 13-16)**: Decommission CrewAI runtime. Migrate persistence from SQLite to Postgres checkpointers. Remove feature flags.

The key insight: MCP tool servers are the migration bridge. Once tools are in MCP, the framework is just orchestration — and orchestration can be swapped incrementally without touching tool logic.

---

*Module 05 complete. Covers LangGraph, OpenAI Agents SDK, Google ADK, CrewAI, and MS Agent Framework with framework-agnostic patterns for enterprise deployment.*
