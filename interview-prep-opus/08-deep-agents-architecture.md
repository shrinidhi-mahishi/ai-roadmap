# Module 08: Deep Agents -- Architecture & Design

**Prep target**: Director/VP AI roles
**Prerequisite**: Familiarity with LLM APIs, tool calling, basic agent concepts
**Framework**: LangChain Deep Agents >= 0.7.x (released March 2026)

---

## What Is This?

Imagine you hire a brilliant new employee (an LLM) but they show up on Day 1 with no desk, no laptop, no access badge, no file cabinet, and no idea who to ask for help. They can think -- but they cannot *do* anything.

A "harness" is everything you give that employee so they can actually work: a workspace (filesystem), tools (APIs), a task list (planning), colleagues to delegate to (sub-agents), rules about what they can and cannot touch (permissions), and a memory system so they do not forget what happened yesterday.

**Deep Agents** is LangChain's factory for assembling that entire harness in a single function call -- `create_deep_agent()`. Under the hood, it configures LangGraph (the graph execution engine) with a layered middleware stack. The output is a standard `CompiledStateGraph` -- not a proprietary black box. You can inspect it, extend it, or drop down to raw LangGraph whenever the harness does not fit.

The project was directly inspired by Claude Code: an attempt to understand what makes Claude Code effective and make those patterns model-agnostic.

## Why It Matters

The 2026 industry consensus is that **the model is commodity; the harness is moat**. Two teams using the identical model can see a 40-point difference in task completion rates based purely on harness design. If you are interviewing for a Director/VP role, you need to articulate *why* the orchestration layer matters more than the model choice -- and exactly how to design one for production.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        create_deep_agent()                         │
│                         (Factory Function)                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────── CONTROL PLANE ────────────────────────────┐  │
│  │                                                               │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐  │  │
│  │  │   Harness    │  │   Provider   │  │  Plugin Registry    │  │
│  │  │   Profile    │  │   Profile    │  │  (entry-points)     │  │
│  │  └──────┬──────┘  └──────┬───────┘  └─────────┬───────────┘  │  │
│  │         └────────────┬───┘                     │              │  │
│  │                      v                         │              │  │
│  │            ┌─────────────────┐                 │              │  │
│  │            │  Profile Merger │<────────────────┘              │  │
│  │            └────────┬────────┘                                │  │
│  │                     v                                         │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │              MIDDLEWARE STACK (ordered)                  │  │  │
│  │  │                                                         │  │  │
│  │  │  1. SkillsMiddleware         7. Custom middleware       │  │  │
│  │  │  2. FilesystemMiddleware     8. Profile extras          │  │  │
│  │  │  3. SubAgentMiddleware       9. Excluded-tool filter    │  │  │
│  │  │  4. SummarizationMW        10. Prompt caching           │  │  │
│  │  │  5. PatchToolCallsMW       11. MemoryMiddleware         │  │  │
│  │  │  6. AsyncSubAgentMW        12. HumanInTheLoopMW        │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────── DATA PLANE ───────────────────────────────┐  │
│  │                                                               │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │  │
│  │  │  Tools   │  │ Virtual  │  │   Sub-   │  │    MCP      │  │
│  │  │ (custom) │  │Filesystem│  │  Agents  │  │  Servers    │  │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────── PERSISTENCE ──────────────────────────────┐  │
│  │                                                               │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │  │
│  │  │ Checkpointer │  │    Store     │  │  Backend Storage   │  │
│  │  │ (state)      │  │ (cross-thd)  │  │  (files)           │  │
│  │  │ Postgres/    │  │ Postgres/    │  │  State/Disk/Store/ │  │
│  │  │ Redis/Dynamo │  │ InMemory     │  │  Hub/Composite     │  │
│  │  └──────────────┘  └──────────────┘  └────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────── TELEMETRY ────────────────────────────────┐  │
│  │  LangSmith tracing  │  Per-trace cost  │  Time-travel debug  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│                    Output: CompiledStateGraph                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Three-Layer Hierarchy

The architecture is a strict three-layer stack. Each layer adds capability:

```
┌──────────────────────────────────────────────────┐
│  create_deep_agent()                             │
│  Full harness: filesystem, sub-agents, skills,   │
│  context management, planning, memory            │
├──────────────────────────────────────────────────┤
│  create_agent()                                  │
│  Minimal harness: agent loop + tool interface    │
├──────────────────────────────────────────────────┤
│  LangGraph                                       │
│  Graph runtime: nodes, edges, state,             │
│  checkpointing, streaming, interrupts            │
└──────────────────────────────────────────────────┘
```

### Request Flow Narrative

1. User sends a message via `agent.invoke({"messages": [...]})`.
2. The `before_agent` middleware hooks fire top-to-bottom (Skills loads SKILL.md, Memory loads AGENTS.md, etc.).
3. The message enters the LangGraph agent loop. The `before_model` hooks assemble the prompt (system prompt + context + summarized history).
4. The model returns a response. If it includes tool calls, `wrap_tool_call` hooks intercept each call (logging, permission checks, cost tracking).
5. Tool results return to the model. If the model requests a sub-agent via the `task` tool, a fresh agent is spawned with isolated context.
6. When the model emits a final response (no more tool calls), `after_model` and `after_agent` hooks fire.
7. At each super-step boundary, the checkpointer writes state to the persistence layer.
8. The final result is returned to the caller.

### Four-Layer Capability Model

| Layer | Purpose | Components |
|-------|---------|------------|
| 1 -- Execution | What the agent can *do* | Tools, virtual filesystem (8 built-in ops), MCP servers, sandbox, multimodal I/O |
| 2 -- Context | What the agent *knows* | Skills (SKILL.md), Memory (AGENTS.md), summarization, prompt caching |
| 3 -- Delegation | How the agent *scales* | TodoListMiddleware (planning), sub-agents (task tool), context isolation |
| 4 -- Steering | How humans *control* it | HITL interrupts, filesystem permissions, double-texting strategies |

---

## Part 2: Core Mechanics & Algorithms

### Middleware Stack -- Deterministic Assembly

The middleware stack is the core extension mechanism. It replaces subclassing. Each middleware can hook into six lifecycle points:

```
before_agent ──> before_model ──> wrap_model_call ──> after_model ──> after_agent
                                       │
                                  wrap_tool_call
                                  (per tool invocation)
```

**Merging rules for custom middleware**:
- Custom middleware is matched by `.name` attribute against built-in middleware.
- If names match: the custom instance *replaces* the built-in, keeping its position in the stack.
- If names do not match: the custom instance inserts after `PatchToolCallsMiddleware` (position 5), before profile extras (position 8).

**What you cannot exclude**: `FilesystemMiddleware`, `SubAgentMiddleware`, and the permission middleware raise `ValueError` if you try to remove them. Use `excluded_tools` to hide their tools instead.

**Critical concurrency rule**: Never mutate `self` attributes inside hooks. Concurrent operations (sub-agents, parallel tool calls) will race. Use graph state for shared mutable data.

### Multi-Provider Model Interface

The `model=` parameter accepts `"provider:model"` strings. Provider profiles handle credential validation, model construction kwargs, and default configuration:

```
model="anthropic:claude-sonnet-4-6"     # Anthropic direct
model="openai:gpt-5.5"                  # OpenAI
model="google_genai:gemini-3.6-flash"   # Google
model="ollama:north-mini-code-1.0"      # Local via Ollama
```

Any model supporting tool calling works. This is the key differentiator versus single-vendor SDKs (Claude Agent SDK = Claude only, OpenAI Agents SDK = OpenAI only).

### Harness Profiles -- Declarative Tuning

Profiles let you change harness behavior per provider or model *without* modifying `create_deep_agent` call sites. Two types:

**HarnessProfile** (agent-level tuning):
- `base_system_prompt`, `system_prompt_suffix` -- prompt assembly
- `tool_description_overrides` -- per-tool description rewrites
- `excluded_tools`, `excluded_middleware` -- feature gating
- `extra_middleware` -- inject additional middleware
- `general_purpose_subagent` -- disable, rename, or re-prompt the default sub-agent

**ProviderProfile** (model construction):
- `init_kwargs` -- passed to chat model constructor
- `pre_init` hooks -- credential validation, env checks
- `runtime_kwargs_factory` -- dynamic kwargs per invocation

**Resolution order**: Registration keys work at provider level (`"openai"`) or model level (`"openai:gpt-5.5"`). When both exist, model-level overrides win. Load order: built-ins, then entry-point plugins, then direct `register()` calls.

### Sub-Agent Architecture

The `task` tool spawns ephemeral sub-agents with:
- **Fresh context**: No conversation history from parent. Prevents context pollution.
- **Autonomous execution**: Runs to completion without parent interaction.
- **Single handoff**: Returns only the final result to parent (typically ~200 tokens).
- **Context isolation**: Heavy outputs stored in virtual filesystem; parent sees summaries only.
- **Permission inheritance**: Sub-agents inherit parent permissions by default. Explicit `permissions` in the sub-agent spec *replaces* parent rules entirely.

Custom `CompiledStateGraph` instances can be passed as sub-agents, so raw LangGraph orchestration plugs in alongside the harness's defaults.

### Double-Texting Strategies

When a user sends a new message while the agent is processing:

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `enqueue` (default) | Queue new input, process after current run | Chat UIs, sequential workflows |
| `reject` | Refuse new input until current run completes | Critical operations |
| `interrupt` | Halt current run, preserve progress, process new input from preserved state | Interactive editing |
| `rollback` | Halt current run, revert all progress, process from scratch | Fresh-start preference |

---

## Part 3: Token Economics & NFR Analysis

### Cost Control Mechanisms

**SummarizationMiddleware** -- the primary cost lever:
- Trigger: configurable threshold, e.g., `("tokens", 100000)` -- fires when context approaches limit.
- Retention: `("messages", 20)` -- keeps N most recent messages verbatim.
- Older messages are summarized via an internal LLM call and offloaded to the virtual filesystem.
- Agent reads from filesystem when it needs historical detail.

**Prompt Caching** (Anthropic/Bedrock only):
- Auto-enabled on static prompt sections.
- Configurable TTL: `AnthropicPromptCachingMiddleware(ttl="1h")`.
- Reduces cost for repeated system prompt processing across turns.

**Sub-Agent Context Isolation**:
- Each sub-agent gets fresh context, preventing token accumulation.
- Heavy subtask outputs stored in virtual filesystem; parent sees ~200-token summaries.

**Retrieval-Based Memory** (StoreBackend with semantic search):
- 72% token savings versus naive context injection from a 24-entry store.
- Savings grow with store size: naive injection scales linearly, retrieval holds flat at top-K.

### Cost Formulas

```
Single-turn cost:
  C_turn = (input_tokens * P_input) + (output_tokens * P_output)
  With caching: C_turn = (cached_tokens * P_cache) + (uncached_tokens * P_input) + (output_tokens * P_output)

Multi-turn session cost:
  C_session = SUM(C_turn_i) for i = 1..N
  Without summarization: input_tokens_i ~ i * avg_turn_size  (linear growth)
  With summarization: input_tokens_i ~ min(i * avg_turn_size, threshold + K)  (capped)

Model tiering savings:
  C_tiered = C_supervisor(frontier) + SUM(C_worker_j(cheap))
  Reported: ~60% total spend reduction with ~4-point drop in routing accuracy
```

### Latency SLA Targets

| Operation | p50 | p95 | p99 | Notes |
|-----------|-----|-----|-----|-------|
| Simple tool call (single turn) | 1-3s | 5s | 8s | Model inference + tool execution |
| Sub-agent spawn + completion | 5-15s | 30s | 60s | Fresh context assembly + execution |
| Checkpoint write (Postgres) | 5-20ms | 50ms | 100ms | Per super-step |
| Checkpoint write (DynamoDB) | 10-30ms | 80ms | 200ms | <350KB direct, larger via S3 |
| Summarization trigger | 2-5s | 10s | 15s | Internal LLM call for compression |
| Prompt cache hit | 0ms overhead | 0ms | 0ms | Pre-computed, no additional latency |

### Capacity Planning

**The #1 cost risk is runaway agent loops.**
- Documented case: 47-iteration supervisor loop burned $180 on a single request.
- Rate limit errors account for 60% of LLM call failures (Datadog 2026). These are not provider unreliability -- they are agent loops creating concurrency spikes.
- Mitigations: `ToolCallLimitMiddleware`, max iteration counts, convergence detection, per-trace cost visibility in LangSmith.

**Hidden enterprise costs** beyond tokens:
- ML/DevOps engineering salaries for harness maintenance
- Observability tooling (LangSmith, Langfuse)
- Vector database infrastructure for memory
- Custom audit trail build-out for EU AI Act compliance
- These compound over 24 months and are largely invisible at budget approval time.

### Availability, RPO & RTO Targets

| Target | Value | Notes |
|--------|-------|-------|
| **Availability** | 99.9% (3-nines) | Production agent harness uptime target |
| **RPO** | 0 (zero checkpoint data loss) | With PostgresSaver; MemorySaver = total loss on crash |
| **RTO** | <5 min | Restart container + replay from last checkpoint |

**Checkpointer durability trade-offs**:

| Backend | Durability | Cost | Use Case |
|---------|-----------|------|----------|
| MemorySaver | Zero -- total state loss on crash | $0 | Dev/test only |
| PostgresSaver | ACID-compliant, point-in-time recovery | ~$50/mo (managed RDS) | Production default |
| DynamoDBSaver | Auto-scaling, multi-region replication | Higher (pay-per-request) | AWS-native, global deployments |

**Compliance requirements**: EU AI Act Article 14 mandates human oversight for high-risk AI systems (effective August 2026). SOC 2 audit trail requirements demand queryable logs of every agent action, model call, and tool invocation -- PostgresSaver satisfies this via SQL queryability; MemorySaver and RedisSaver do not.

---

## Part 4: Distributed Resilience & Security

### Checkpointing Architecture

Each super-step writes a checkpoint to the persistence layer, keyed by `thread_id`.

```
┌─────────────────────────────────────────────────────────┐
│                   Checkpointer Backends                 │
├──────────────────┬──────────────────────────────────────┤
│ MemorySaver      │ In-process. Any restart = total      │
│                  │ state loss. Dev only.                 │
├──────────────────┼──────────────────────────────────────┤
│ SqliteSaver      │ Single-process. File-backed.          │
│                  │ Good for local dev with persistence.  │
├──────────────────┼──────────────────────────────────────┤
│ PostgresSaver    │ Multi-process production. Most        │
│                  │ common choice. Full SQL queryability.  │
├──────────────────┼──────────────────────────────────────┤
│ RedisSaver       │ High-throughput, low-latency.         │
│                  │ Good for high-frequency checkpoints.  │
├──────────────────┼──────────────────────────────────────┤
│ DynamoDBSaver    │ AWS-native. <350KB direct in Dynamo;  │
│                  │ larger payloads uploaded to S3 with    │
│                  │ DynamoDB storing reference pointer.    │
└──────────────────┴──────────────────────────────────────┘
```

### Dual-Layer Memory Model

**Short-term (thread-scoped)**: Lives in checkpoint state tied to `thread_id`. Provides conversation continuity, HITL workflow state, fault tolerance, and time-travel debugging.

**Long-term (cross-conversation)**: Key-value store organized by namespace tuples (e.g., `(user_id, "memories")`). Backed by PostgreSQL with semantic search. Queryable via API.

### Checkpoint Granularity -- The Waste Problem

LangGraph defaults to checkpointing after every node. The 2026 "Crab" checkpoint/restore study found:
- Over 75% of agent turns produce no recovery-relevant state.
- Blanket checkpointing is mostly waste.
- Semantics-aware checkpointing (only at phase boundaries) raised recovery correctness from 8% to 100% while cutting checkpoint traffic by up to 87%.

**Production recommendation**: Checkpoint at major phase boundaries (search -> synthesize -> review), not at every micro-step.

### Checkpointing vs True Durable Execution

This is a critical architectural distinction for VP-level interviews:

- **Checkpointing** (LangGraph): Saves state. Developer is responsible for detecting the need to restore, triggering it, and coordinating at scale.
- **Durable execution** (Temporal, AWS Step Functions, Restate, DBOS, Inngest): The runtime itself guarantees exactly-once semantics, automatic recovery, and side-effect deduplication.

Session memory is not durable execution. Saving chat history helps an agent remember, but does not prove which shell command ran, which email was sent, or whether a retry would duplicate a side effect.

Production reference architectures in 2026 combine both: durable execution primitives for side-effect guarantees + LangGraph checkpointing for conversation state.

### Failure Taxonomy

| Category | Failure Mode | Detection | Mitigation |
|----------|-------------|-----------|------------|
| **Transient** | Rate limit (429) | HTTP status monitoring | Exponential backoff, model tiering |
| **Transient** | Worker crash mid-run | Lease timeout | Checkpoint resume on new worker |
| **Permanent** | Runaway agent loop | Token/cost alerts, iteration count | ToolCallLimitMiddleware, hard caps |
| **Permanent** | State loss (MemorySaver) | Missing context in follow-ups | Durable checkpointer backend |
| **Silent** | Non-deterministic output | Trace comparison, output validation | Schema validation per node boundary |
| **Silent** | Tool call fails silently | Trace analysis | Structured error results, not exceptions |
| **Security** | Context injection in sandbox | Audit logs, output scanning | Network blocking, HITL, treat output as untrusted |

### Multi-Tenancy -- Three Auth Layers

1. **Custom Authentication**: `@auth.authenticate` handler validates credentials, returns user identity and permissions.
2. **Agent Auth**: Handles OAuth for third-party services, manages token refresh automatically.
3. **RBAC**: Controls operator-level access for team members.

Built-in: scoped threads, per-user sandboxes, run history.

### Filesystem Permissions

Path-based allow/deny rules. First-match-wins evaluation. Three modes: `allow`, `deny`, `interrupt` (pauses for human approval; requires checkpointer). Default when no rule matches: operations allowed (permissive).

### Zero-Trust Agent Architecture

Assume-breach posture applied to the agent harness:
- **Every tool call verified**: Permission rules evaluate each invocation against the caller's identity, role, and target resource. No implicit trust between agent and backends.
- **Credentials never in agent context**: Use the auth proxy pattern (see Scenario 2 code review pipeline). Credentials are injected at the network boundary, never inside the agent's message history or tool arguments.
- **Mutual TLS between agent and backends**: Encrypt and authenticate all agent-to-backend communication. Prevents man-in-the-middle between the harness and PostgresSaver/StoreBackend.
- **Signed checkpoint artifacts**: Checkpoints include HMAC signatures to detect tampering. A corrupted or replayed checkpoint fails verification before state restoration.

### RBAC with Least-Privilege

Map organizational roles to Deep Agents capabilities via FilesystemMiddleware tool restrictions and harness profiles:

| Role | Allowed Tools | Filesystem Access | Notes |
|------|--------------|-------------------|-------|
| **Viewer** | `read_file`, `ls`, `glob` | Read-only, scoped paths | Dashboards, reporting |
| **Developer** | `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep` | Read/write workspace | No `execute`, no sandbox |
| **Admin** | All tools including `execute` | Full access + sandbox | Sandbox access for testing |
| **Auditor** | `read_file`, `grep`, `glob` | Read-only + trace access | Compliance checks, audit trail review |

Enforce via `HarnessProfile.excluded_tools` per role and `FilesystemPermission` rules scoped to role-specific path prefixes.

### PII Filtering Pipeline

Three-stage pipeline integrated into the middleware stack:

1. **Detection**: Scan agent messages for PII before tool calls using regex patterns (SSN, email, phone, credit card) + NER model (spaCy or presidio) for names, addresses, medical terms. Runs in `wrap_tool_call` hook.
2. **Redaction**: Replace detected PII with typed placeholders (`[SSN-1]`, `[EMAIL-2]`, `[NAME-3]`) before writing to filesystem, memory, or checkpoint state. Maintain a reversible mapping in a secure, access-controlled side-channel for authorized re-identification.
3. **Audit trail**: Log all PII detections with `trace_id`, action taken (redacted/blocked/passed), entity type, and timestamp. Publish to LangSmith traces for compliance review. Alert on anomalous PII volume (potential data breach or prompt injection exfiltrating PII).

### Open Protocol Support

| Protocol | Purpose |
|----------|---------|
| MCP (Model Context Protocol) | Standardized tool/data source connections |
| A2A (Agent-to-Agent) | Cross-deployment agent communication |
| Webhooks | POST run payload on completion |

### Regulatory Context

EU AI Act enforcement for high-risk AI systems applies from August 2026. OWASP Top 10 for Agentic Applications 2026 ranks Tool Misuse (ASI02), Supply Chain Vulnerabilities (ASI04), and Unexpected Code Execution (ASI05) as critical risks.

---

## Part 5: Production Enterprise Code

### Complete Production Agent Setup

```python
"""
Production Deep Agent with full middleware stack, model tiering,
durable checkpointing, permissions, HITL, and observability.

Requirements:
  pip install deepagents langgraph-checkpoint-postgres langchain-anthropic
"""

import asyncio
import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.middleware import (
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from deepagents.permissions import FilesystemPermission
from deepagents.profiles import HarnessProfile, register_harness_profile
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Persistence layer -- durable checkpointing + cross-thread store
# ---------------------------------------------------------------------------
DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"

checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()  # creates tables if not present

store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# ---------------------------------------------------------------------------
# 2. Backend -- composite routing separates internal data from user workspace
# ---------------------------------------------------------------------------
backend = CompositeBackend(
    default=StateBackend(),  # internal data (summaries, tool results) stays ephemeral
    routes={
        "/workspace/": FilesystemBackend(root_dir="./workspace", virtual_mode=True),
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (rt.server_info.user.identity, "memories"),
        ),
    },
)

# ---------------------------------------------------------------------------
# 3. Permissions -- first-match-wins, most specific first
# ---------------------------------------------------------------------------
permissions = [
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/credentials*", "/**/*.key"],
        mode="deny",
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["read"],
        paths=["/memories/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/memories/**"],
        mode="interrupt",  # human approval for memory writes
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**"],
        mode="deny",  # deny-all catch-all
    ),
]

# ---------------------------------------------------------------------------
# 4. Custom middleware -- cost guardrails + summarization tuning
# ---------------------------------------------------------------------------
summarization_mw = SummarizationMiddleware(
    trigger=("tokens", 80_000),  # fire at 80K tokens (tune per model context window)
    retention=("messages", 15),  # keep 15 most recent messages verbatim
)

tool_limit_mw = ToolCallLimitMiddleware(max_calls=200)  # prevent runaway loops

# ---------------------------------------------------------------------------
# 5. Harness profile -- model-specific tuning without changing call site
# ---------------------------------------------------------------------------
production_profile = HarnessProfile(
    system_prompt_suffix=(
        "You are a production research assistant. "
        "Always cite sources. Never fabricate data. "
        "If you are unsure, say so explicitly."
    ),
    excluded_tools=frozenset(["execute"]),  # no shell access for this agent
)
register_harness_profile("anthropic:claude-sonnet-4-6", production_profile)

# ---------------------------------------------------------------------------
# 6. Custom tools
# ---------------------------------------------------------------------------
def search_knowledge_base(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the internal knowledge base for relevant documents."""
    # Production implementation would query a vector store
    return {
        "results": [
            {"title": f"Doc about {query}", "relevance": 0.92, "snippet": "..."}
        ],
        "total_matches": 1,
    }


def create_support_ticket(
    title: str, description: str, priority: str = "medium"
) -> dict[str, str]:
    """Create a support ticket in the ticketing system."""
    # Production implementation would call Jira/ServiceNow API
    return {
        "ticket_id": "SUPP-1234",
        "status": "created",
        "priority": priority,
    }


# ---------------------------------------------------------------------------
# 7. Assemble the agent
# ---------------------------------------------------------------------------
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_knowledge_base, create_support_ticket],
    system_prompt=(
        "You are a senior support engineer. Use the knowledge base to answer "
        "questions. Create tickets for issues that need human follow-up."
    ),
    middleware=[summarization_mw, tool_limit_mw],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["create_support_ticket"]},  # HITL for ticket creation
    checkpointer=checkpointer,
    store=store,
)

# ---------------------------------------------------------------------------
# 8. Invoke with thread tracking
# ---------------------------------------------------------------------------
def handle_user_request(user_id: str, thread_id: str, message: str) -> str:
    """Handle a user request with full thread tracking and error handling."""
    config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": user_id,
        }
    }
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
        )
        final_message = result["messages"][-1]
        return final_message.content
    except Exception as e:
        logger.error("Agent invocation failed: %s", e, exc_info=True)
        return f"I encountered an error processing your request. Error: {e}"


# ---------------------------------------------------------------------------
# 9. Model tiering -- frontier supervisor + cheap workers
# ---------------------------------------------------------------------------
tiered_agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",  # frontier model for supervisor
    tools=[search_knowledge_base],
    system_prompt="You are a research coordinator. Delegate subtasks to workers.",
    subagents=[
        {
            "name": "data_gatherer",
            "model": "anthropic:claude-haiku-4",  # cheap model for worker
            "instructions": "Gather and summarize data. Return concise findings.",
            "tools": [search_knowledge_base],
        },
    ],
    middleware=[summarization_mw, tool_limit_mw],
    checkpointer=checkpointer,
    store=store,
)


# ---------------------------------------------------------------------------
# 10. Streaming usage for real-time UIs
# ---------------------------------------------------------------------------
async def stream_response(user_id: str, thread_id: str, message: str):
    """Stream agent response for real-time UI updates."""
    config = {
        "configurable": {"thread_id": thread_id, "user_id": user_id}
    }
    async for event in agent.astream(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
        stream_mode=["messages", "values"],
    ):
        if event.get("type") == "message_chunk":
            yield event["content"]


if __name__ == "__main__":
    response = handle_user_request(
        user_id="user-42",
        thread_id="thread-abc-123",
        message="What is our refund policy for enterprise customers?",
    )
    print(response)
```

### Custom Middleware Example

```python
"""
Custom middleware for per-request cost tracking and alerting.
Demonstrates the middleware hook lifecycle.
"""

from deepagents.middleware import AgentMiddleware


class CostTrackingMiddleware(AgentMiddleware):
    name = "cost_tracking"

    def __init__(self, alert_threshold_usd: float = 1.0):
        self.alert_threshold = alert_threshold_usd

    def before_agent(self, state, config):
        """Initialize cost accumulator in graph state at run start."""
        state["accumulated_cost_usd"] = 0.0
        return state

    def after_model(self, response, state, config):
        """Track token usage after each model call."""
        usage = getattr(response, "usage_metadata", None)
        if usage:
            input_cost = (usage.get("input_tokens", 0) / 1_000_000) * 3.00
            output_cost = (usage.get("output_tokens", 0) / 1_000_000) * 15.00
            turn_cost = input_cost + output_cost
            state["accumulated_cost_usd"] += turn_cost

            if state["accumulated_cost_usd"] > self.alert_threshold:
                # In production: send to PagerDuty, Slack, etc.
                print(
                    f"COST ALERT: ${state['accumulated_cost_usd']:.4f} "
                    f"exceeds threshold ${self.alert_threshold:.2f}"
                )
        return response

    def wrap_tool_call(self, tool_call, handler, state, config):
        """Log every tool call for audit trail."""
        tool_name = tool_call.get("name", "unknown")
        print(f"AUDIT: Tool call -> {tool_name}")
        result = handler(tool_call)
        return result
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Multi-Tenant Customer Support Platform

**Problem Statement**: A B2B SaaS company with 500 enterprise customers wants to deploy AI agents that can search each customer's private knowledge base, create support tickets, and escalate to human agents. Each customer's data must be strictly isolated. The system must handle 10,000 concurrent conversations and comply with SOC 2 requirements.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────┐
│                      API Gateway / Auth                      │
│              (JWT validation, tenant extraction)             │
└────────────────────────────┬─────────────────────────────────┘
                             v
┌──────────────────────────────────────────────────────────────┐
│                   LangGraph Cloud / K8s                      │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  create_deep_agent() per tenant config                 │  │
│  │  model="anthropic:claude-sonnet-4-6" (supervisor)      │  │
│  │  + model="anthropic:claude-haiku-4" (workers)          │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  CompositeBackend                                      │  │
│  │    /workspace/ -> StoreBackend(ns=tenant_id)           │  │
│  │    /shared/    -> StoreBackend(ns="global_kb")         │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  Permissions: deny /**/.env, allow /workspace/**,      │  │
│  │  read-only /shared/**, deny-all catch-all              │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  HITL: interrupt on create_ticket, escalate_to_human   │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  PostgresSaver (checkpoints)  │  PostgresStore (memories)    │
│  Per-tenant thread isolation  │  Per-tenant namespace        │
└──────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Model tiering | Single frontier model | Frontier supervisor + Haiku workers | B | 60% cost reduction, ~4-point accuracy loss acceptable for KB lookups |
| Tenant isolation | Separate agent instances per tenant | Shared agent pool with namespace isolation | B | 500 tenants x dedicated instances is operationally expensive; namespace isolation in StoreBackend is sufficient with proper auth |
| Checkpointer | RedisSaver | PostgresSaver | Postgres | SOC 2 requires durable audit trail; Postgres supports SQL queries for compliance reporting |
| HITL scope | All tool calls | Only ticket creation + escalation | B | Approving every search would destroy UX; limit to actions with real-world consequences |
| Summarization threshold | 50K tokens (aggressive) | 100K tokens (conservative) | 80K | Balance between cost control and context quality; tune per customer feedback |

**Decision Rationale**: The key architectural decision is namespace-based tenant isolation over separate agent instances. StoreBackend with per-tenant namespace factories (`namespace=lambda rt: (rt.server_info.user.identity, "workspace")`) provides data isolation without the operational burden of managing 500 separate deployments. PostgresSaver gives SOC 2-compliant audit trails via SQL queryability. Model tiering (Sonnet supervisor + Haiku workers) controls cost at scale.

---

### Scenario 2: Autonomous Code Review Pipeline

**Problem Statement**: An engineering organization (200 developers, 50 repos) wants AI-assisted code review. The agent must: clone the PR branch, run static analysis, read relevant documentation, produce a structured review, and post comments to GitHub. Reviews must complete in under 3 minutes. Code must never leave the organization's infrastructure.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────┐
│                     GitHub Webhook Handler                    │
│                (PR opened / updated events)                  │
└────────────────────────────┬─────────────────────────────────┘
                             v
┌──────────────────────────────────────────────────────────────┐
│                   Self-Hosted LangGraph                       │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Supervisor Agent (claude-sonnet-4-6)                  │  │
│  │  Plans review strategy, delegates to workers           │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │  Worker: code_analyzer (claude-haiku-4)                │  │
│  │    Sandbox: E2B (Firecracker) -- git clone, lint, test │  │
│  │    Network: blocked except internal git + registry     │  │
│  │                                                        │  │
│  │  Worker: doc_reader (claude-haiku-4)                   │  │
│  │    Backend: FilesystemBackend (virtual_mode=True)       │  │
│  │    Reads: repo docs, style guides, ADRs                │  │
│  │                                                        │  │
│  │  Worker: review_writer (claude-sonnet-4-6)             │  │
│  │    Synthesizes findings into structured review          │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  PostgresSaver (checkpoints)  │  GitHub API (MCP server)     │
└──────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Deployment | LangGraph Cloud (managed) | Self-hosted on internal K8s | B | Code must never leave org infrastructure; managed service sends code to external endpoints |
| Sandbox provider | Modal (gVisor) | E2B (Firecracker) | E2B | Firecracker provides strongest isolation for running untrusted PR code; no GPU needed for code review |
| Review model | Single agent, one pass | Supervisor + 3 specialized workers | B | Parallelism needed for 3-minute SLA; code_analyzer and doc_reader run concurrently |
| Network policy | Allow all within sandbox | Block all except internal git + npm registry | B | PR code is semi-trusted; block exfiltration while allowing dependency resolution |
| Credential handling | Inject GitHub token into sandbox | Auth proxy outside sandbox | B | Token inside sandbox + context injection = exfiltration risk; auth proxy injects credentials at network boundary |

**Decision Rationale**: Self-hosting is non-negotiable given the data residency requirement. E2B Firecracker sandboxes provide the strongest isolation tier for executing arbitrary PR code (git clone, lint, test runs). The 3-minute SLA drives the multi-worker architecture: `code_analyzer` and `doc_reader` run in parallel as sub-agents, then `review_writer` synthesizes. Auth proxy pattern keeps GitHub tokens outside the sandbox, eliminating the most common credential exfiltration vector. The supervisor uses a frontier model (Sonnet) because review quality directly impacts developer trust, while workers use Haiku for cost efficiency on data gathering tasks.

---

## Quick Reference: Framework Comparison Matrix

| Dimension | Deep Agents | Claude Agent SDK | OpenAI Agents SDK | CrewAI |
|-----------|-------------|------------------|-------------------|--------|
| Model support | Any (100+ providers) | Claude only | OpenAI only | Any |
| Deployment | Managed or self-host | Self-host only | OpenAI platform | CrewAI cloud |
| Multi-tenancy | Built-in (RBAC) | Build yourself | Limited | Enterprise tier |
| Checkpointing | Postgres/Redis/DynamoDB | None built-in | None built-in | Limited |
| HITL | Dynamic interrupts anywhere | Manual | Limited | Manual |
| LOC (basic) | ~10-20 | ~10-20 | ~10 | ~30-60 |
| LOC (production) | ~50-100 | ~200+ | ~100+ | ~100-200 |
| License | MIT | MIT (SDK) | MIT | MIT |

**When to use what**:
- Maximum control, custom workflows: raw LangGraph
- Long-running agents with sub-agents: Deep Agents
- Anthropic-only, self-hosted: Claude Agent SDK
- Fast multi-agent prototyping: CrewAI
- Simplest single-agent path: OpenAI Agents SDK

## Production Deployment Checklist

1. Replace `MemorySaver` with durable checkpointer (PostgresSaver or DynamoDBSaver)
2. Configure `SummarizationMiddleware` trigger threshold per workload
3. Set filesystem permissions (default is permissive -- everything allowed)
4. Enable HITL for high-stakes tool calls (writes, deletes, execute)
5. Configure retry policies per-node
6. Set up LangSmith tracing on day one
7. Build evaluators in parallel with first agent, not after first production incident
8. Implement model tiering (frontier for supervisor, cheaper for workers)
9. Set `ToolCallLimitMiddleware` to prevent cost explosions
10. Configure double-texting strategy appropriate to UX

---

**Sources**: LangChain Deep Agents docs, LangChain blog, Addy Osmani (harness engineering), Anthropic (harness design), arXiv papers on agent harnesses, Datadog 2026 report, LangChain 2026 State of Agent Engineering, Zylos Research (durable execution), OWASP Top 10 Agentic 2026.
