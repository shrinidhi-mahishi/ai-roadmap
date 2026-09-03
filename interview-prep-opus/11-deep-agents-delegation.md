# Module 11: Delegation, Subagents & Streaming in LangChain Deep Agents

**Study Module for Director/VP AI Interviews**
**Date**: 2026-09-02 | **Sources**: 20 primary sources

---

## What Is This?

Imagine a senior executive who never does research herself. Instead, she gives clear
assignments to specialists, waits for their one-page summaries, and makes decisions based
on those summaries. She never sees the 50 emails each specialist sent, the 20 documents
they read, or the dead ends they explored. Her desk stays clean. Her decisions stay sharp.

That is exactly how LangChain Deep Agents handles delegation. The main agent (the executive)
delegates via a `task` tool to subagents (the specialists). Each subagent gets a fresh,
clean workspace (context window). It does its work -- sometimes dozens of tool calls -- and
returns a single summary to the parent. The parent never sees the mess. This is called
**context quarantine**, and it is the primary mechanism for keeping long-running agents
from degrading.

The event streaming system (v3) provides typed projections so frontends can show real-time
progress. The protocol stack has converged: MCP for tool access, A2A for inter-agent
communication, and AG-UI for frontend streaming.

## Why It Matters

Multi-agent systems are the default architecture for production AI in 2026, but most teams
add agent hierarchy too early and too deep. Understanding delegation patterns, their failure
modes, and when NOT to use them is what separates architects who ship from architects who
prototype. The streaming architecture is equally critical -- without it, users stare at
blank screens during 15-second computations, driving the abandonment rates that kill AI
product adoption.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        CONTROL PLANE                                         │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ SubAgentMiddleware                                                      │ │
│  │ - Registers `task` tool automatically                                   │ │
│  │ - Manages subagent lifecycle (create, run, collect result)              │ │
│  │ - Filters state keys to prevent context leakage                        │ │
│  │ - Routes to sync (local) or async (remote) execution                   │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐                        │
│  │ Sync SubAgent Pool    │  │ Async SubAgent Pool   │                        │
│  │ - Same process        │  │ - Remote LangGraph     │                        │
│  │ - Parent blocks       │  │   Platform servers     │                        │
│  │ - Direct return       │  │ - task_id + polling    │                        │
│  │                       │  │ - Independent scaling  │                        │
│  └──────────────────────┘  └──────────────────────┘                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                        DATA PLANE                                            │
│                                                                              │
│  ┌─────────── Parent Agent (Coordinator) ──────────────────────────────┐    │
│  │                                                                      │    │
│  │  Context Window:                                                     │    │
│  │  [System Prompt] + [Messages] + [Subagent Results as ToolMessages]  │    │
│  │                                                                      │    │
│  │  Delegates via task(subagent_type="name", task="description")       │    │
│  │                                                                      │    │
│  │  Receives: single ToolMessage (1-2K tokens) per delegation          │    │
│  │  Never sees: subagent tool calls, intermediate reasoning, errors    │    │
│  └──────────────┬──────────────┬──────────────┬────────────────────────┘    │
│                  │              │              │                              │
│        ┌────────v────────┐ ┌──v──────────┐ ┌─v───────────────┐             │
│        │ Subagent A       │ │ Subagent B  │ │ Subagent C       │             │
│        │ "researcher"     │ │ "analyst"   │ │ "writer"         │             │
│        │                  │ │             │ │                   │             │
│        │ Fresh context    │ │ Fresh ctx   │ │ Fresh context     │             │
│        │ Own tools        │ │ Own tools   │ │ Own tools         │             │
│        │ Own model (opt)  │ │ Own model   │ │ Own model (opt)   │             │
│        │ 10-50 tool calls │ │ 5-20 calls  │ │ 3-10 calls       │             │
│        │                  │ │             │ │                   │             │
│        │ Returns: 2K sum  │ │ Returns: 1K │ │ Returns: 3K doc  │             │
│        └─────────────────┘ └─────────────┘ └──────────────────┘             │
├──────────────────────────────────────────────────────────────────────────────┤
│                     EVENT STREAMING (v3)                                      │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  LLM Inference ──> Agent Runtime ──> Frontend (SSE)                  │    │
│  │                        │                                             │    │
│  │                   Subagent Streams (scoped projections)              │    │
│  │                        │                                             │    │
│  │  Typed Events:                                                       │    │
│  │  - stream.messages      (coordinator messages)                       │    │
│  │  - stream.subagents     (subagent lifecycle + content)               │    │
│  │  - stream.tool_calls    (tool invocations + results)                 │    │
│  │  - subagent.subagents   (nested delegation -- recursive)            │    │
│  │                                                                      │    │
│  │  Namespace: empty = coordinator, non-empty = subagent identity      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────────────────┤
│                     PROTOCOL STACK (2026 Consensus)                          │
│                                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                      │
│  │ MCP            │  │ A2A            │  │ AG-UI          │                      │
│  │ Agent-to-Tool  │  │ Agent-to-Agent │  │ Agent-to-UI    │                      │
│  │ Most mature    │  │ 150+ orgs      │  │ 40+ frameworks │                      │
│  │ Tool discovery │  │ IBM ACP merged │  │ SSE transport  │                      │
│  │ + invocation   │  │ Agent Cards    │  │ Typed events   │                      │
│  └──────────────┘  └──────────────┘  └──────────────┘                      │
├──────────────────────────────────────────────────────────────────────────────┤
│                     TELEMETRY                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ LangSmith: {'lc_agent_name': 'subagent-name'} per run               │    │
│  │ Cost attribution per subagent type                                   │    │
│  │ Stream status tracking: started -> completed/failed/interrupted      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

1. **Parent receives user message**. The coordinator LLM decides whether to handle directly
   or delegate. Clear subagent descriptions are critical -- they are the LLM's decision
   criteria for routing.

2. **Delegation via `task` tool**. The parent calls `task(subagent_type="researcher",
   task="Find the top 5 open-source alternatives to X")`. SubAgentMiddleware intercepts.

3. **Subagent context assembly**. A fresh context window is created. The subagent gets its
   own system prompt (no parent inheritance), its own tools (inherits parent's by default
   unless overridden), and optionally a different model. No parent message history leaks in.

4. **Subagent execution**. The subagent runs autonomously -- it may make 10-50 tool calls,
   hit compression thresholds, even delegate to nested subagents. All of this is invisible
   to the parent.

5. **Result return**. The subagent's final output becomes a single `ToolMessage` in the
   parent's context. Typical compression: 50K tokens of subagent work compressed to 2K
   tokens of summary (25:1 ratio).

6. **State filtering**. SubAgentMiddleware explicitly filters: `messages` (only final
   returned), `todos` (excluded), `structured_response` (excluded), `skills_metadata`
   (filtered), `memory_contents` (filtered). Runtime context propagates intentionally.

7. **Streaming**. Throughout execution, typed events flow via SSE to the frontend.
   `stream.subagents` provides product-level visibility (user-facing); `stream.subgraphs`
   provides graph-level visibility (internal debugging).

---

## Part 2: Core Mechanics & Algorithms

### Subagent Lifecycle

```
Parent Decision
    │
    v
┌───────────────────┐
│ task() tool call    │
│ subagent_type       │
│ task description    │
└────────┬────────────┘
         │
         v
┌───────────────────────────────────────┐
│ SubAgentMiddleware                     │
│                                       │
│ 1. Look up subagent config by name    │
│ 2. Create fresh context window        │
│ 3. Assemble system prompt (no parent) │
│ 4. Apply tools (inherit or override)  │
│ 5. Apply permissions (inherit or      │
│    replace -- no merging)             │
│ 6. Apply model (inherit or override)  │
│ 7. Filter state keys                  │
│ 8. Launch execution                   │
└────────┬──────────────────────────────┘
         │
    ┌────v────┐              ┌────────────┐
    │  Sync   │              │   Async     │
    │ SubAgent│              │  SubAgent   │
    │         │              │             │
    │ Runs    │              │ Returns     │
    │ locally │              │ task_id     │
    │ Blocks  │              │ immediately │
    │ parent  │              │             │
    └────┬────┘              └──────┬─────┘
         │                          │
         v                          v
┌────────────────┐     ┌─────────────────────┐
│ Final message   │     │ Async tools:         │
│ as ToolMessage  │     │ - check_async_task   │
│ to parent       │     │ - update_async_task  │
└────────────────┘     │ - cancel_async_task  │
                        │ - list_async_tasks   │
                        └─────────────────────┘
```

### Subagent Configuration: Inheritance Rules

| Property | General-Purpose Subagent | Custom Subagent | Design Rationale |
|----------|--------------------------|-----------------|------------------|
| Tools | Inherits parent | Inherits (unless overridden) | Specialists may need restricted toolsets |
| Model | Inherits parent | Inherits (unless overridden) | Cost optimization: use Haiku for summarization |
| Skills | Inherits parent | Must specify explicitly | Prevents unintended skill activation |
| Middleware | None inherited | None inherited | Clean execution environment |
| Permissions | Inherits parent | Inherits (unless overridden) | When overridden, replaces entirely -- no merge |
| System prompt | N/A (auto-generated) | Must define explicitly | No parent context inheritance by design |

**Key design decision**: Permissions replace, they do not merge. If you specify permissions
on a custom subagent, it gets exactly those permissions and nothing from the parent. This
prevents privilege escalation through additive merging.

### Delegation Patterns (Production 2026)

**Pattern 1: Supervisor (default choice)**

Central orchestrator delegates to specialized workers. Widest framework support (Claude
Agent SDK, LangGraph, OpenAI Agents SDK, CrewAI). Best-understood failure mode
(over-delegation, bounded by iteration ceilings).

```
Orchestrator
  ├── Researcher (web search, document analysis)
  ├── Analyst (data processing, calculations)
  └── Writer (report generation, formatting)
```

**Pattern 2: Fan-Out (parallel independent tasks)**

Parallel independent subtasks with results aggregated. Effective for 3-10 parallel tasks.
Deep Agents supports this via multiple subagent calls or dynamic subagents with
CodeInterpreterMiddleware.

**Pattern 3: Pipeline (sequential stages)**

Each stage transforms output for the next. Linear workflows with clear boundaries. All
frameworks support this natively.

**Pattern 4: Debate (adversarial validation)**

Multiple agents argue, a critic selects the best output. Reserved for quality-critical
decisions. Higher cost but catches hallucination cascading.

**Pattern 5: Swarm (genuine scale)**

Dynamic peer agents with shared memory/message bus. Practical at 100 agents (Kimi K2.5),
demonstrated at 300 (K2.6). Experimental; not recommended for most production systems.

**Industry consensus**: Start with supervisor. Add fan-out for parallelizable subtasks.
Pipeline for linear workflows. Debate only when quality justifies 2-3x cost. Swarm almost
never.

### Event Streaming Architecture

Deep Agents extends LangGraph streaming with typed projections via `stream_events(version="v3")`.

**Stream projections**:

| Projection | Source | Content | UI Use |
|-----------|--------|---------|--------|
| `stream.messages` | Coordinator | Main agent text output | Primary chat display |
| `stream.subagents` | SubAgentMiddleware | Subagent lifecycle + content | Status indicators, nested views |
| `stream.tool_calls` | ToolRuntime | Tool invocations and results | Debug panels, progress indicators |
| `subagent.messages` | Individual subagent | Subagent-scoped text | Expandable detail views |
| `subagent.tool_calls` | Individual subagent | Subagent-scoped tool calls | Nested debug panels |
| `subagent.subagents` | Nested delegation | Recursive subagent events | Deep hierarchy views |

**Content-block-centric streaming**: Events are typed (text, reasoning, media, tool-call
data). UIs no longer guess the chunk type. Each event has a lifecycle: Start (create
placeholder, show spinner) -> Data (stream content) -> End (finalize rendering).

**Why streaming is architectural, not a feature**: Without streaming, users see blank screens
during 15-second agent computations. This is the single biggest driver of abandonment rates
on AI products. Streaming must be designed in from the start -- it shapes node structure,
error handling, and frontend contracts.

**Transport recommendation**: SSE for agent-to-frontend (works through every HTTP proxy,
native browser reconnection). WebSocket or HTTP/2 for inference-to-runtime. gRPC for
backend-to-backend (up to 77% lower latency on small payloads vs REST).

### ACP (Agent Client Protocol)

ACP standardizes agent-to-editor communication via stdio. It is NOT for inter-agent
communication (that is A2A).

Supported clients: Zed (native), JetBrains IDEs (built-in), VSCode (via extension),
Neovim (via plugins).

**Disambiguation**: LangChain's ACP (Agent Client Protocol) is for agent-editor integration
via stdio. IBM's ACP (Agent Communication Protocol) was for agent-to-agent and has merged
into Google's A2A. Same acronym, different protocols.

### Protocol Stack (2026 Consensus)

| Layer | Protocol | Purpose | Status |
|-------|----------|---------|--------|
| Agent-to-Tool | **MCP** | Tool access, server discovery | Most mature, broadest adoption |
| Agent-to-Agent | **A2A** | Cross-vendor agent coordination | 150+ org support; IBM ACP merged in |
| Agent-to-Frontend | **AG-UI** | Real-time event streaming to UIs | 40+ framework integrations |

A2A discovery uses **Agent Cards** -- JSON documents at well-known URLs describing identity,
skills, API endpoint, supported modalities, authentication requirements, and streaming
capabilities.

---

## Part 3: Token Economics & NFR Analysis

### Context Quarantine ROI

| Scenario | Without Subagents | With Subagents | Savings |
|----------|-------------------|----------------|---------|
| 10 web searches | 10 full results in context (~50K tokens) | 1 summary (~2K tokens) | ~96% |
| File analysis (20 files) | All file contents loaded (~100K tokens) | Condensed findings (~3K tokens) | ~97% |
| Multi-step research | Cumulative tool outputs | Per-topic subagent, summary only | 10:1 to 50:1 |

### Cost Formulas

**Per-delegation cost**:
```
C_delegation = C_subagent_system_prompt_assembly
             + C_subagent_inference (all turns within subagent)
             + C_parent_result_ingestion (ToolMessage, typically 1-2K tokens)
```

**Net cost comparison**:
```
Single agent (no delegation):
  C_total = sum(input_tokens_per_turn * P_input) + sum(output_tokens * P_output)
  Context grows monotonically; later turns are expensive.

With delegation:
  C_total = C_parent_turns + sum(C_delegation_i for each subagent)
  Parent context stays bounded; subagent costs are independent.
```

**Key insight**: Total token usage is often **higher** with subagents (each gets a fresh
system prompt). But parent context pressure is dramatically lower, sustaining many more
turns before hitting compression. The ROI is in agent longevity, not per-turn cost.

**Model-tier optimization**: Use cheaper models for subagents doing routine work.

| Subagent Role | Recommended Model | Rationale |
|---------------|-------------------|-----------|
| Web search summarization | Haiku | 90% cheaper; summarization is low-complexity |
| Data analysis | Sonnet | Needs reasoning for correct calculations |
| Code generation | Sonnet/Opus | Needs strong coding capability |
| Report writing | Sonnet | Balance of quality and cost |

### Latency Analysis

| Component | Latency Contribution | Occurrence |
|-----------|---------------------|------------|
| Subagent system prompt assembly | 50-200ms | Per delegation |
| Subagent first LLM call (TTFT) | 1-3s | Per delegation |
| Subagent total execution | 5-60s | Per delegation (depends on tool calls) |
| Sync subagent blocking | Full subagent duration | Parent waits |
| Async subagent launch | <500ms (returns task_id) | Per delegation |
| SSE event delivery to frontend | <100ms per event | Continuous |

**Latency SLA targets** (production guidance):

| Metric | Sync Delegation | Async Delegation | Streaming TTFB |
|--------|----------------|-----------------|----------------|
| p50 | <10s | <500ms (launch) | <200ms |
| p95 | <30s | <2s (launch) | <500ms |
| p99 | <60s | <5s (launch) | <1s |
| p50 result ready | N/A | <30s | N/A |
| p95 result ready | N/A | <120s | N/A |

### Streaming Latency Budget

| Layer | Transport | Typical Latency |
|-------|-----------|-----------------|
| Inference to Runtime | WebSocket / HTTP/2 | Base model TTFT |
| Runtime to Tools (MCP) | Streamable HTTP | Tool-dependent |
| Runtime to Frontend (AG-UI) | SSE | <100ms per event |
| Backend-to-Backend | gRPC | Up to 77% lower vs REST on small payloads |

### TodoListMiddleware: Measured Impact

Benchmarking (PR #4929, July 2026): no statistically significant accuracy improvement,
higher token usage on 2 of 3 models. Moved from default to opt-in. Include only when
measured improvement exists for your specific workload.

### Availability, RPO/RTO & Compliance Targets

| NFR | Target | Rationale |
|-----|--------|-----------|
| Availability (sync delegation) | 99.9% | Blocking path; sync subagent failure = parent failure for that turn |
| Availability (async delegation) | 99.5% | Depends on task queue durability and remote LangGraph Platform uptime |
| RPO (LangGraph Platform-backed async tasks) | 0 (no data loss) | Every async task is checkpointed; crash recovery replays from last checkpoint |
| RPO (in-memory async) | 1 task (lost on crash) | MemorySaver-backed async tasks are not durable; use only for dev/test |
| RTO (sync subagent restart) | <10s | Fresh context assembly + first LLM call; no state to recover |
| RTO (async task recovery from checkpoint) | <2 min | Reload task state from checkpoint, re-establish remote connection, resume execution |

**Compliance**:
- **SOC 2 (delegation audit trail)**: Every delegation must be logged with: who delegated (parent agent ID + user_id), what was delegated (task description hash), when (timestamp), to which subagent (subagent type + instance ID). LangSmith tags (`lc_agent_name`) provide this automatically.
- **GDPR**: Subagent must not persist user PII beyond the scope of the delegated task. Async task results containing PII must be purged after parent retrieval or within a configurable TTL.

**Key trade-off**: Sync delegation (simple, blocking, limited scale) vs async delegation (complex, non-blocking, durable) vs remote execution on LangGraph Platform (most durable, highest cost, multi-region capability). Production recommendation: default to sync for interactive workflows (<30s expected duration), async for background processing (>30s), remote only when cross-region or independent scaling is required.

---

## Part 4: Distributed Resilience & Security

### Durable Execution & Checkpointing

**Async subagent state persistence**: `AsyncTask` TypedDict tracks `task_id`, `run_id`,
`status`, timestamps. The `async_tasks` field uses `_tasks_reducer` for merging updates.
Survives checkpointing -- parent can resume and check on previously launched async tasks.

**DeltaChannel**: Storage primitive supporting checkpoints for long-running, long-context
agents. Custom state schemas must subclass `DeepAgentState` to preserve the DeltaChannel
reducer.

**Remote execution**: Async subagents on remote LangGraph Platform or self-hosted servers.
Enables cross-machine delegation, independent scaling of subagent compute, and fault
isolation (remote crash does not crash parent).

### Failure Taxonomy

**Transient failures** (automatic or semi-automatic recovery):

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Subagent execution failure | Stream status = `failed` | Parent receives error as ToolMessage; can retry or handle |
| Network timeout (async) | `check_async_task` returns timeout | Retry with backoff; task state persisted |
| Context overflow in subagent | `ContextOverflowError` | Subagent's own summarization handles it |

**Permanent failures** (require architectural mitigation):

| Failure | Description | Severity | Mitigation |
|---------|-------------|----------|------------|
| Over-delegation | Parent delegates too aggressively; unnecessary latency and cost | Medium | Clear subagent descriptions; make general-purpose a catch-all, not default path |
| Delegation loops | Infinite re-dispatch cycles between agents | High | Subagents do not inherit `task` tool by default; explicit inclusion required |
| Supervisor saturation | Routing accuracy drops after 8-12 subagent round trips | Medium | Summarization at 85%; subagent results as single ToolMessages |
| Hallucination cascading | One agent's hallucinated output treated as ground truth downstream | Critical | Structured output (Pydantic), tool evidence verification, HITL at decision points |
| Async task orphaning | Remote subagent completes after parent session ends | Medium | `async_tasks` state + checkpointing; idempotent result retrieval |
| Premature hierarchy | Adding 3+ layers when 2 suffice | Medium | Two layers handle vast majority of cases; third layer rarely justified |
| Stream rendering failure | Flattening subagent streams obscures hierarchy | Low | Separate first-class projections per subagent; handle nested recursion in frontend |

### Circuit Breaker Pattern for Delegation

```
Delegation Circuit Breaker:
  CLOSED (normal):
    - Delegate as normal
    - Track failure count per subagent type

  OPEN (tripped after N consecutive failures):
    - Return fallback response to parent
    - Log alert for operator review
    - Timer starts for half-open attempt

  HALF-OPEN (testing recovery):
    - Allow one delegation through
    - Success: return to CLOSED
    - Failure: return to OPEN, extend timer
```

### Security Architecture

**Permission isolation**: Subagent permissions replace parent permissions entirely when
specified (no additive merging). This is a deliberate security choice to prevent privilege
escalation.

```python
# Restricted subagent: read everywhere, write only to /output/
{
    "name": "restricted-writer",
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        FilesystemPermission(operations=["write"], paths=["/output/**"]),
    ],
}
```

**Context leakage prevention**: SubAgentMiddleware explicitly filters state keys. Only the
final message crosses the boundary. Todos, structured_response, skills_metadata, and
memory_contents are all filtered out.

**Runtime context propagation**: Runtime context (user_id, API keys, org_id) propagates
intentionally to all subagents. Use namespaced fields in the context schema for
per-subagent configuration when needed.

**Structured output enforcement**: `response_format` with Pydantic models forces JSON
output. Prevents subagents from injecting unexpected content. Enables programmatic
validation of subagent outputs.

**Audit trail**: LangSmith tags (`lc_agent_name`) enable filtering by subagent identity,
cost attribution per subagent type, and performance analysis per delegation pattern.

**Stream namespace isolation**: Event namespace field distinguishes coordinator from
subagent events, preventing UI rendering confusion and enabling per-subagent access control.

### Zero-Trust Delegation

Parent agent permissions are never inherited by default -- each subagent gets an explicit, minimal permission set:
- **Replace, not merge**: When permissions are specified on a subagent, they completely replace the parent's permissions. This prevents privilege escalation through additive merging.
- **Subagent output validation**: Subagent output is validated before injection into parent context. Structured output enforcement via Pydantic prevents data exfiltration (subagent cannot inject arbitrary content into parent's context window).
- **Minimal permission principle**: Each subagent receives only the tools and filesystem access required for its specific task. A research subagent gets read-only access; a writer gets write access to `/output/` only.

### RBAC for Delegation

| Role | Delegation Authority | Subagent Access |
|------|---------------------|-----------------|
| **Analyst** | Can delegate to research subagents only | Read-only tools; no code execution |
| **Engineer** | Delegate to coding + research subagents | Read-write tools; sandboxed code execution |
| **Lead** | Delegate to any subagent type + approve async tasks | Full tool access; async task management |
| **Admin** | Configure subagent definitions + modify delegation policies | Full access; subagent CRUD; permission management |

Enforcement: Role is extracted from TenantContext at delegation time. SubAgentMiddleware checks the user's role against the requested subagent type before creating the subagent. Unauthorized delegation attempts return a structured error to the parent (not silently dropped).

### PII Filtering in Delegation

```
Parent Context ──> [1. Pre-Delegation Scan] ──> Subagent Execution ──> [2. Post-Delegation Filter] ──> Parent Context
                         │                                                       │
                         v                                                       v
                    Detect PII in task                                    Redact PII from
                    prompt before sending                                subagent response
                    to subagent                                          before injecting
                                                                         into parent context

[3. Cross-Boundary Audit]: Log every delegation with:
    - parent_id (coordinator agent instance)
    - subagent_id (subagent type + instance)
    - task_hash (SHA-256 of task description)
    - PII_detected (boolean + count by type)
    - PII_action (redacted | blocked | passed_with_consent)
    - timestamp
```

**Critical for multi-tenant**: A subagent processing Tenant A's data must not leak PII into Tenant B's context. Namespace isolation in CompositeBackend (scoped by org_id from TenantContext) enforces this at the storage layer. At the context layer, subagent results are scanned for cross-tenant PII before the parent ingests them.

---

## Part 5: Production Enterprise Code

### Complete Multi-Agent System with Delegation and Streaming

```python
"""
Production multi-agent system with hierarchical delegation, async subagents,
event streaming, and structured output validation.
Requires: pip install langchain-deepagents pydantic
"""
import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.middleware import (
    SubAgentMiddleware,
    AsyncSubAgentMiddleware,
    CodeInterpreterMiddleware,
)
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver


# --- Structured Output Schemas ---

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ResearchFinding(BaseModel):
    """Structured output from the research subagent."""
    title: str = Field(description="One-line summary of finding")
    evidence: list[str] = Field(description="Supporting evidence from tools")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score")
    sources: list[str] = Field(description="URLs or document references")


class AnalysisReport(BaseModel):
    """Structured output from the analysis subagent."""
    summary: str = Field(description="Executive summary in 2-3 sentences")
    findings: list[ResearchFinding]
    severity: Severity
    recommended_actions: list[str]
    estimated_impact: str


# --- Runtime Context ---

@dataclass
class AgentContext:
    user_id: str
    org_id: str
    session_id: str


# --- Subagent Definitions ---

RESEARCHER_SUBAGENT = {
    "name": "researcher",
    "description": (
        "Use for tasks requiring web search, document retrieval, or "
        "gathering information from external sources. Returns structured "
        "findings with evidence and confidence scores."
    ),
    "system_prompt": (
        "You are a research specialist. Your job is to find accurate, "
        "well-sourced information. Always cite your sources. Return "
        "structured findings with evidence and confidence scores. "
        "Never speculate without marking it as low-confidence."
    ),
    "model": "anthropic:claude-haiku-4.5",  # Cost optimization: Haiku for search
    "response_format": ResearchFinding,
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        # No write access for researcher
    ],
}

ANALYST_SUBAGENT = {
    "name": "analyst",
    "description": (
        "Use for tasks requiring data analysis, calculations, comparisons, "
        "or synthesizing multiple research findings into a coherent report. "
        "Returns a structured analysis report with severity and actions."
    ),
    "system_prompt": (
        "You are a data analyst. Synthesize research findings into "
        "actionable analysis. Classify severity accurately. Recommend "
        "concrete actions. Every claim must trace to evidence provided "
        "in the input."
    ),
    "model": "anthropic:claude-sonnet-4-6",  # Needs reasoning capability
    "response_format": AnalysisReport,
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        FilesystemPermission(operations=["write"], paths=["/output/**"]),
    ],
}

ASYNC_PROCESSOR_SUBAGENT = {
    "name": "background-processor",
    "description": (
        "Use for long-running tasks that do not need immediate results. "
        "The parent can continue other work while this runs remotely."
    ),
    "graph_id": "background-processor-v2",
    "url": "https://agent-server.example.com",
}


# --- Agent Factory ---

def create_orchestrator(
    tools: list[Any] | None = None,
    enable_async: bool = False,
    enable_dynamic: bool = False,
) -> Any:
    """
    Create a production orchestrator with sync and optionally async subagents.

    Args:
        tools: Custom tools for the parent agent.
        enable_async: Enable async subagent delegation to remote servers.
        enable_dynamic: Enable CodeInterpreterMiddleware for programmatic fan-out.
    """
    subagents = [RESEARCHER_SUBAGENT, ANALYST_SUBAGENT]

    middleware = []
    if enable_dynamic:
        middleware.append(CodeInterpreterMiddleware())

    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=(
            "You are a senior technical coordinator. Your job is to decompose "
            "complex requests into subtasks and delegate to specialists.\n\n"
            "DELEGATION GUIDELINES:\n"
            "- Use 'researcher' for information gathering from external sources.\n"
            "- Use 'analyst' for data synthesis and report generation.\n"
            "- Handle simple questions directly -- do NOT over-delegate.\n"
            "- If a task requires tight sequential reasoning across all context, "
            "  do it yourself instead of delegating.\n"
            "- Never delegate a task that would take you fewer than 3 tool calls.\n\n"
            "QUALITY RULES:\n"
            "- Verify subagent outputs against known facts before presenting.\n"
            "- If a subagent returns low-confidence findings, acknowledge uncertainty.\n"
            "- Combine multiple subagent results into a coherent response."
        ),
        tools=tools or [],
        subagents=subagents,
        middleware=middleware,
        checkpointer=MemorySaver(),
        interrupt_on={"delete_file": True},
    )

    return agent


# --- Event Streaming Consumer ---

async def stream_agent_response(
    agent: Any,
    user_message: str,
    context: AgentContext,
    thread_id: str,
) -> None:
    """
    Stream agent response with typed event handling.
    Demonstrates the three consumption patterns for production UIs.
    """
    config = {"configurable": {"thread_id": thread_id}}

    stream = agent.stream_events(
        {"messages": [{"role": "user", "content": user_message}]},
        config=config,
        context=context,
        version="v3",
    )

    # Pattern 1: Coordinator messages (main chat output)
    async for message in stream.messages:
        if hasattr(message, "content"):
            print(f"[Coordinator] {message.content}", end="", flush=True)

    # Pattern 2: Subagent lifecycle tracking
    for subagent in stream.subagents:
        print(f"\n[Subagent: {subagent.name}] Status: {subagent.status}")

        # Stream subagent messages for detail views
        for msg in subagent.messages:
            if hasattr(msg, "content"):
                print(f"  [{subagent.name}] {msg.content[:100]}...")

        # Check completion
        output = subagent.output
        print(f"  [{subagent.name}] Completed: {subagent.status}")

    # Pattern 3: Interleaved consumption (mixed coordinator + subagent)
    # Useful for unified timeline UIs
    for event in stream.interleave("messages", "subagents"):
        if hasattr(event, "name"):  # subagent event
            print(f"[Subagent {event.name}] {event.status}")
        else:  # coordinator message
            print(f"[Main] {getattr(event, 'content', '')}")


# --- Async Subagent Manager ---

class AsyncTaskManager:
    """
    Manage async subagent tasks with status tracking and result retrieval.
    Wraps the async tools registered by AsyncSubAgentMiddleware.
    """

    def __init__(self, agent: Any):
        self.agent = agent
        self.active_tasks: dict[str, dict] = {}

    def launch_task(
        self,
        task_description: str,
        thread_id: str,
        context: AgentContext,
    ) -> str:
        """Launch a background task, return task_id immediately."""
        config = {"configurable": {"thread_id": thread_id}}

        result = self.agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Use start_async_task to launch background-processor "
                            f"with this task: {task_description}"
                        ),
                    }
                ]
            },
            config=config,
            context=context,
        )

        # Extract task_id from the response
        last_message = result["messages"][-1].content
        task_id = self._extract_task_id(last_message)
        self.active_tasks[task_id] = {"status": "running", "description": task_description}
        return task_id

    def check_task(self, task_id: str, thread_id: str, context: AgentContext) -> dict:
        """Check status of a background task."""
        config = {"configurable": {"thread_id": thread_id}}

        result = self.agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Use check_async_task for task_id: {task_id}",
                    }
                ]
            },
            config=config,
            context=context,
        )

        status = result["messages"][-1].content
        return {"task_id": task_id, "status": status}

    def _extract_task_id(self, message: str) -> str:
        """Extract task_id from agent response. Production: parse structured output."""
        # In production, use response_format to get structured task_id
        for word in message.split():
            if word.startswith("task-") or word.startswith("run-"):
                return word.strip(".,;:")
        return "unknown"


# --- Delegation Decision Framework ---

class DelegationDecisionFramework:
    """
    Codifies when to delegate vs. handle directly.
    Use as documentation and runtime guidance for the orchestrator.
    """

    DELEGATE_WHEN = [
        "Single agent context regularly approaches 65% of window capacity",
        "Tasks naturally decompose into independent subtopics",
        "Different subtasks benefit from different models (cost optimization)",
        "Need to parallelize work (async subagents)",
        "Task requires 5+ distinct tool calls in a focused area",
    ]

    DO_NOT_DELEGATE_WHEN = [
        "Task requires tight sequential reasoning across all context",
        "Subagent overhead exceeds benefit (fewer than 3 tool calls)",
        "Fewer than 3-5 distinct subtask types in the workload",
        "Task is a simple question answerable from current context",
        "Azure guidance: flow-control overhead often exceeds benefits for <5 responsibilities",
    ]

    SCALING_LIMITS = {
        "supervisor_degradation": "Noticeable after 8-12 subagent round trips",
        "practical_swarm_limit": "100 agents (Kimi K2.5)",
        "demonstrated_swarm_limit": "300 agents (Kimi K2.6)",
        "recommended_max_layers": 2,  # Orchestrator + workers
        "third_layer_justified": "Rarely; significantly increases debugging complexity",
    }

    @classmethod
    def should_delegate(cls, tool_calls_needed: int, context_utilization: float) -> bool:
        """Simple heuristic: delegate if enough work and context pressure."""
        return tool_calls_needed >= 3 and context_utilization < 0.65


# --- Usage Example ---

if __name__ == "__main__":
    # Create orchestrator with sync subagents
    agent = create_orchestrator(enable_async=False, enable_dynamic=False)

    context = AgentContext(
        user_id="user-42",
        org_id="acme-corp",
        session_id="session-abc",
    )

    # Synchronous invocation
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Research the top 5 open-source LLM frameworks released in 2026, "
                        "then analyze which is best suited for a regulated healthcare "
                        "environment. Consider security, auditability, and compliance."
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "research-001"}},
        context=context,
    )
    print(result["messages"][-1].content)

    # Streaming invocation
    asyncio.run(
        stream_agent_response(
            agent=agent,
            user_message="Compare the latency characteristics of the top 3 frameworks.",
            context=context,
            thread_id="research-001",
        )
    )
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Enterprise Document Processing Pipeline with Multi-Model Delegation

**Problem Statement**: Design an agent system that processes incoming legal contracts
(20-100 pages each), extracts key terms, flags risks, and generates compliance summaries.
Volume: 200 contracts/day. Each contract requires 3 specialized analyses (financial terms,
legal risk, regulatory compliance). Latency target: complete analysis within 10 minutes
per contract. Cost target: under $2 per contract.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────┐
│                    INGESTION GATEWAY                             │
│  - Receives contract PDF                                        │
│  - Chunks into sections (financial, legal, regulatory)          │
│  - Assigns thread_id per contract                               │
├──────────────────────────────────────────────────────────────────┤
│                    COORDINATOR AGENT (Sonnet)                    │
│                                                                  │
│  System Prompt: Contract analysis orchestrator                   │
│  Context Window: Stays light (~20K tokens)                       │
│  - Receives section metadata (not full text)                     │
│  - Delegates each section to appropriate subagent                │
│  - Aggregates structured outputs into final report               │
│                                                                  │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐          │
│  │ Financial      │ │ Legal Risk     │ │ Regulatory     │         │
│  │ Terms Analyst  │ │ Analyst        │ │ Compliance     │         │
│  │ (Haiku)        │ │ (Sonnet)       │ │ (Sonnet)       │         │
│  │                │ │                │ │                 │         │
│  │ Structured:    │ │ Structured:    │ │ Structured:     │        │
│  │ FinancialTerms │ │ RiskFlags      │ │ ComplianceCheck │        │
│  │ Pydantic model │ │ Pydantic model │ │ Pydantic model  │        │
│  │                │ │                │ │                 │         │
│  │ Permissions:   │ │ Permissions:   │ │ Permissions:    │        │
│  │ Read-only      │ │ Read-only      │ │ Read-only       │        │
│  └───────────────┘ └───────────────┘ └───────────────────┘       │
│                                                                  │
│  Fan-Out: All 3 subagents run concurrently (async subagents)     │
│  Aggregation: Coordinator merges 3 structured outputs            │
│  Output: ComplianceReport Pydantic model                         │
├──────────────────────────────────────────────────────────────────┤
│                    STREAMING LAYER (AG-UI / SSE)                 │
│  - Real-time progress: "Analyzing financial terms..." (30%)      │
│  - Per-subagent status indicators                                │
│  - Final report streamed as it assembles                         │
├──────────────────────────────────────────────────────────────────┤
│                    PERSISTENCE & AUDIT                           │
│  - LangSmith: per-contract trace, per-subagent cost attribution  │
│  - PostgresStore: contract analysis history (cross-thread)       │
│  - Checkpointing: resume on failure without re-analysis          │
└──────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Execution model | Sequential subagents | Parallel async fan-out | **Parallel (B)** | 3 independent analyses; parallel cuts latency from 30min to 10min |
| Financial analyst model | Sonnet ($$$) | Haiku ($) | **Haiku (B)** | Term extraction is pattern-matching; Haiku handles it at 1/10 cost |
| Legal/regulatory model | Haiku ($) | Sonnet ($$$) | **Sonnet (B)** | Risk assessment requires nuanced reasoning; accuracy is non-negotiable |
| Output validation | Free text | Pydantic structured output | **Pydantic (B)** | Prevents hallucinated risk flags; enables programmatic downstream processing |
| Coordinator context | Full contract text | Section metadata only | **Metadata only (A)** | Coordinator needs to route, not analyze; keeps context at ~20K tokens |

**Decision Rationale**: The fan-out pattern with model-tier differentiation is the key
architecture. Financial term extraction is a high-volume, low-complexity task where Haiku
provides sufficient accuracy at 10x lower cost. Legal and regulatory analysis requires
Sonnet's reasoning capability because false negatives (missed risks) have high business
cost. Structured output via Pydantic prevents hallucination cascading -- if a subagent
returns an invalid structure, the error is caught before the coordinator aggregates it.
The coordinator stays light by receiving only section metadata and structured subagent
outputs, never the full 100-page contract text.

**Cost estimate** (per contract):
- Coordinator: ~20K input + ~2K output per turn, 3 turns = ~$0.20
- Financial (Haiku): ~30K input + ~1K output = ~$0.01
- Legal (Sonnet): ~50K input + ~3K output = ~$0.20
- Regulatory (Sonnet): ~40K input + ~3K output = ~$0.17
- Total: ~$0.58 per contract (well under $2 target)

---

### Scenario 2: Real-Time Customer Support Agent with Escalation Hierarchy

**Problem Statement**: Design a customer support agent system for a B2B SaaS platform
(50,000 active users, ~2,000 support conversations/day). The system must handle L1
(FAQ/password reset), L2 (technical troubleshooting), and L3 (escalation to human).
Requirements: <5s TTFB for all interactions, streaming responses, conversation history
across sessions, and full audit trail for SOC2 compliance.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────┐
│                    ENTRY POINT (AG-UI / SSE)                    │
│  - User message arrives via SSE connection                      │
│  - Session context loaded from checkpointer                     │
│  - TenantContext injected (user_id, org_id, plan_tier)          │
├──────────────────────────────────────────────────────────────────┤
│                    TRIAGE COORDINATOR (Sonnet)                   │
│                                                                  │
│  Skills (progressive disclosure):                                │
│  - password-reset (~100 tok metadata at startup)                │
│  - billing-inquiry (~100 tok metadata at startup)                │
│  - api-troubleshooting (~100 tok metadata at startup)            │
│  - escalation-protocol (~100 tok metadata at startup)            │
│                                                                  │
│  Memory:                                                         │
│  - AGENTS.md: product knowledge base summary (compact)           │
│  - StoreBackend: per-user interaction history (cross-session)    │
│                                                                  │
│  Decision Logic:                                                 │
│  - Simple query + matching skill? Handle directly (L1)           │
│  - Technical issue? Delegate to diagnostic subagent (L2)         │
│  - Unresolved after subagent? Escalate to human (L3)             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                 L2: Diagnostic Subagent (Sonnet)          │    │
│  │                                                           │    │
│  │  Tools: log_search, metric_query, config_checker          │    │
│  │  Permissions: Read-only on all customer data              │    │
│  │  response_format: DiagnosticReport (Pydantic)             │    │
│  │                                                           │    │
│  │  Returns structured diagnosis:                            │    │
│  │  - Root cause (with evidence)                             │    │
│  │  - Recommended fix                                        │    │
│  │  - Confidence level                                       │    │
│  │  - Escalation recommendation (bool)                       │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  L3 Escalation:                                                  │
│  - HITL interrupt (interrupt_on={"escalate_to_human": True})     │
│  - Full conversation context preserved via checkpointer          │
│  - Human agent receives structured handoff summary               │
├──────────────────────────────────────────────────────────────────┤
│                    STREAMING & LATENCY                            │
│  - SSE for all responses (<100ms event delivery)                 │
│  - Prompt caching: 1-hour TTL on Bedrock for stable system prompt│
│  - L1 responses: direct from coordinator, no delegation overhead │
│  - L2 responses: streaming subagent progress indicators          │
│  - <5s TTFB guaranteed by prompt cache hits                      │
├──────────────────────────────────────────────────────────────────┤
│                    AUDIT & COMPLIANCE (SOC2)                     │
│  - LangSmith: every interaction traced with user_id, org_id      │
│  - Structured outputs: all diagnostic findings are auditable JSON│
│  - Permission isolation: subagent cannot write to customer data  │
│  - HITL checkpoint: escalation decisions are human-approved       │
│  - ContextHubBackend: versioned prompt history for compliance     │
└──────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| L1 handling | Dedicated L1 subagent | Coordinator handles directly via skills | **Skills (B)** | Avoids delegation overhead for simple tasks; skill identity pattern is faster |
| L2 handling | Coordinator does diagnosis | Dedicated diagnostic subagent | **Subagent (B)** | Diagnosis requires 5-15 tool calls; quarantine keeps coordinator context clean |
| Conversation memory | Full history in AGENTS.md | Compact summary + RAG for history | **RAG (B)** | 2,000 conversations/day; in-prompt memory does not scale |
| Streaming transport | WebSocket | SSE | **SSE (B)** | Works through every HTTP proxy; native browser reconnection; sufficient for support |
| Escalation trigger | LLM decides autonomously | HITL interrupt on escalation | **HITL (B)** | SOC2 requires human approval for escalation decisions; checkpointer preserves state |

**Decision Rationale**: The skill identity pattern for L1 is the critical performance
optimization. Most support queries (60-70%) are L1 -- password resets, billing questions,
feature explanations. These do not benefit from delegation overhead. The coordinator loads
the relevant skill instructions on demand (~5K tokens) and handles directly.

L2 delegation to a diagnostic subagent is justified because diagnosis requires 5-15 tool
calls (log search, metric query, config check) that would pollute the coordinator's
context. The subagent returns a structured DiagnosticReport, enabling programmatic
validation and preventing hallucination cascading.

The <5s TTFB target is met by prompt caching (1-hour TTL on Bedrock) and SSE transport
(<100ms event delivery). L1 queries hit the cache and respond without delegation.
L2 queries show streaming progress indicators while the diagnostic subagent works.

SOC2 compliance drives the HITL interrupt pattern for L3 escalation: the checkpointer
preserves full conversation state during the human review pause, and the ContextHubBackend
maintains versioned prompt history for audit.

---

## Key Interview Talking Points

1. **Start with supervisor, add complexity only when measured**. Two layers (orchestrator +
   workers) handle the vast majority of production cases. A third layer is rarely justified.

2. **Subagents trade total tokens for bounded context pressure**. Total usage may increase,
   but parent agent sustains many more turns. The ROI is longevity, not per-turn cost.

3. **Over-delegation is the most common supervisor failure**. Clear subagent descriptions
   and a "3+ tool calls" heuristic prevent unnecessary delegation overhead.

4. **Permissions replace, they do not merge**. This prevents privilege escalation but
   requires explicit permission sets on restricted subagents.

5. **Streaming is architectural, not a feature**. Design it in from the start. 15-second
   blank screens kill AI product adoption.

6. **The protocol stack has converged**: MCP (tools), A2A (inter-agent), AG-UI (frontend).
   IBM's ACP merged into A2A. LangChain's ACP is a separate stdio-based agent-editor
   protocol.

7. **Hallucination cascading is the most dangerous multi-agent failure**. Structured output
   (Pydantic), tool evidence verification, and HITL at decision points are the mitigations.

8. **Context quarantine ROI**: 96-97% token savings on tool-heavy workflows. The parent
   receives 2K tokens instead of 100K.
