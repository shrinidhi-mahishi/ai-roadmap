# Topic 9: Multi-Agent Orchestration
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Multi-agent orchestration is the discipline of **coordinating multiple AI agents** to solve problems that no single agent can handle alone. This topic covers five orchestration topologies (supervisor, hierarchical, swarm, pipeline, parallel), typed state handoffs across frameworks, agent communication protocols (A2A, MCP, message passing), consensus mechanisms (voting, debate — with ICLR 2025 MAD findings showing debate doesn't consistently outperform CoT), human-in-the-loop with risk-tiered approval gates, escalation patterns, and the full OWASP Agentic Top 10 security framework. It bridges the gap between building individual agents (Topics 3-4) and deploying enterprise agent systems.

### Why Study This

- **The scaling question**: Single agents hit capability ceilings. Multi-agent systems catch 40% more bugs and reduce human review time by 60% (code review benchmarks). But they also cost 3-15x more and introduce coordination failures. The Director-level skill is knowing when multi-agent is worth the complexity.
- **Production danger zone**: MAST taxonomy (NeurIPS 2025) documents 41-86% failure rates across multi-agent systems. The $47K runaway bill, Amazon Kiro's 13-hour outage, and 82% of enterprise agents being unregistered are real incidents. This topic covers how to prevent them.
- **Governance gap**: Only 21% of organizations have mature AI governance models. The EU AI Act (August 2026), California SB-833, and ISO 42001 are creating new compliance requirements specifically for multi-agent systems. Knowing this landscape is essential for senior roles.
- **Architecture portfolio**: Being able to whiteboard a supervisor pattern, explain why you chose it over a swarm, size the cost ($0.22/PR at 400 PRs/day for a code review pipeline), and articulate the OWASP risks — that's the full stack of a Principal/Director AI architect.

### What Details Are Included

- Five orchestration topologies with benchmarked trade-offs (94% supervisor routing accuracy, ~2,800 tokens)
- Typed state handoffs across LangGraph, OpenAI Agents SDK, CrewAI, Google ADK, AutoGen
- ICLR 2025 MAD consensus benchmarks
- Erlang-inspired supervisor trees (one-for-one, one-for-all, rest-for-one)
- Five-level graceful degradation hierarchy
- Full OWASP Agentic Top 10 with priority sequence
- MAST failure taxonomy (specification 42%, coordination 37%, verification 21%)
- Risk-tiered approval matrix (LOW/MEDIUM/HIGH/CRITICAL)
- Production Python code for supervisor, hierarchical, consensus, HITL, circuit breaker, tracing
- Two enterprise system design scenarios with trade-off matrices and ROI calculations

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Draw each of the five topologies from memory. Study the topology selection decision tree. Understand why supervisor is the default and when alternatives win.
>
> **Second pass (2-3 hours)**: Sections 5-8. Work through the cost multiplier math ($24-$4,000/1k range). Study the OWASP Agentic Top 10 — this is the security framework interviewers reference. Run the supervisor and consensus code.
>
> **Interview prep (1 hour)**: Section 10. Practice the "design a multi-agent system" answer: pick a topology, articulate the cost, explain the failure modes, describe the governance model. Memorize the key incidents.
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

## 1. Concept Overview

### What Is Multi-Agent Orchestration?

A production multi-agent system adds a **second clock** around the inner ReAct/Plan-and-Execute loop of a single agent: it decides **who** is allowed to run that loop next, and **what** of their state is visible to the next hop. The system splits into two planes that must never be coupled:

- **Control plane** -- owns next-agent selection, hop budget, kill-switch, HITL gates, vote aggregation, and dollar caps. Backed by an orchestrator runtime (your process, LangGraph Agent Server, Temporal, etc.).
- **Data plane** -- owns specialist inner loops, MCP `tools/call`, A2A artifacts, and blackboard blobs. Each specialist runs its own ReAct/P&E loop with isolated or shared context.

**The foundational invariant:** The model **never routes, never hands off, never grants authority, never counts a vote, never times out HITL**. It emits a structured action (`transfer_to_*`, `Command(goto=...)`, A2A `SendMessage`, a vote JSON). A **runtime** mutates durable state, enforces hop/dollar/tool allowlists, and decides the next node. Collapsing "who may act" into the prompt is the dominant enterprise failure (AISVS C9.5.3).

### Why It Matters

**Cost amplification is real.** Anthropic's published token multipliers: chat to single-agent is **~4x** tokens; chat to multi-agent is **~15x** tokens. On a stated L1 support ticket (supervisor + 2 specialists, 2,000 in + 400 out per call, Claude Sonnet 5 at $2/$10 per MTok), the coordination tax is **$24/1k tickets** with sticky handoffs (3 calls) vs. **$32/1k** with subagents that always return to the supervisor (4 calls). That +$8/1k/turn buys a single user-facing owner and centralized policy. Unbounded fan-out (50 subs x 10 calls) reaches **$4,000/1k** -- a cost catastrophe (AISVS 9.1.2).

**The burden of proof is on multi-agent.** At equal token budgets, single-agent matches or beats multi-agent on reasoning tasks (ICLR 2025). Multi-agent earns its complexity only when:
- Each role has narrow, non-overlapping responsibility
- Task requires tools from **different security domains** (compliance-driven isolation)
- Parallel isolated context is the product (research across disjoint sources)
- Two teams ship independently (separate release cadences)

LangChain 2026: teams ask for "multi-agent" when they need context management, distributed development, or parallelization. If context were infinite and latency zero, a single agent with all tools would dominate. Skills (progressive disclosure) are often the cheaper substitute. Microsoft Learn 2026: platform-native orchestration for internal subagents; MCP for tools/data; A2A for opaque, cross-platform, cross-org agents.

### Key Industry Numbers

| Metric | Value | Source |
|--------|-------|--------|
| Enterprise LLM spending H1 2025 | $8.4B | Stanford |
| Enterprises exceeding initial cost projections | 96% | Industry survey |
| Multi-agent failure rate (complex tasks) | 41-86% | MAST / industry |
| 5 agents at 95% individual accuracy, e2e success | ~77% | Statistical compounding |
| Enterprises with unknown AI agents | 82% | Security audit |
| Orgs with formal agent accountability | 7.2% | Governance survey |
| Agentic AI projects cancelled by 2027 (Gartner) | >40% | Cost overruns |

---

## 2. Core Concepts

### 2.1 Six Orchestration Topologies

Five topologies serialize the "second clock" differently. Consensus/debate is a **join function** on a fan-in, not a topology. HITL is a **durable wait** with an authenticated resume, not "ask the model to be careful."

#### Topology Comparison

| Topology | Clock | User-Facing Owner | Parallelism | Token Shape | Best For |
|----------|-------|-------------------|-------------|-------------|----------|
| **Router** | Once per user turn | Synthesizer or specialist | `Send` fan-out | Classify + dispatch | Explicit classify + parallel |
| **Supervisor** | Every worker return | Supervisor synthesizes | Default serial (`parallel_tool_calls=False`) | Route + N workers + synth | Dynamic routing, audit trails |
| **Orchestrator-Worker** | Every wave until "enough" | Lead | 3-5 subs x 3+ tools; Magentic assigns one worker/inner tick | Plan + waves + join | Research across disjoint sources |
| **Hierarchical** | Per level: which team | Top-level only | Per-team | Extra splice per level | 6+ workers, resilience-critical, separate IAM |
| **Swarm / Handoff** | Currently `active_agent` | Whoever holds the conversation | Sequential; disable parallel tool calls | Sticky + handoff | Sticky UX, repeat requests |
| **Pipeline** | Deterministic linear | Stage output | None (or fan-out variant) | sum(stages) | Fixed-order workflows |

#### Benchmarks (Focused.io, July 2026)

| Topology | Routing Accuracy | Latency (single) | Latency (handoff) | Tokens/req | Failure Degradation |
|----------|-----------------|-------------------|--------------------|-----------|--------------------|
| Supervisor | 94% | ~4.2s | ~9.1s | ~2,800 | SPOF risk |
| Swarm | 91% | ~2.8s | ~5.4s | ~1,900 | 31% on 1 failure |
| Hierarchical | ~94% (layered) | ~5s | ~12s | ~3,500 | 5.5% on 1 failure |
| Pipeline | 100% (deterministic) | sum(stages) | N/A | sum(stages) | 23% on 1 failure |

#### Call-Count Intuition (LangChain pedagogical table, 2k-token specialists)

| Workload | Subagents | Handoffs | Skills | Router |
|----------|-----------|----------|--------|--------|
| One-shot | **4** | **3** | **3** | **3** |
| Repeat same request | **8** | **5** | **5** | **6** |
| Multi-domain, parallel OK | **5** calls, ~9K tok | **7+** / ~14K+ sequential | **3** / ~15K | **5** / ~9K |

**Key insight:** Subagents win isolation + parallel. Handoffs win sticky UX. Skills win "one agent, many playbooks." Router wins explicit classify + parallel without sticky ownership. `parallel_tool_calls=True` on a supervisor turns one tick into a fan-out orchestrator.

#### Topology Selection Decision Tree

```
Is the task order fixed?
|-- YES --> Pipeline (Sequential)
+-- NO
    |-- Are agents peers with equal authority?
    |   |-- YES --> Swarm (ensure observability + hop cap)
    |   +-- NO
    |       |-- >6 workers?
    |       |   |-- YES --> Hierarchical
    |       |   +-- NO --> Supervisor
    |       +-- Need iterative refinement?
    |           +-- YES --> Reflection Loop
    |
    +-- <3 distinct domains? --> Skip multi-agent entirely.
        Single agent with tools is cheaper and simpler.
```

### 2.2 Supervisor / Subagents-as-Tools

A central LLM (or ledger) runs every round. Workers are **tools**; the supervisor keeps the user-facing reply. `langgraph-supervisor.create_supervisor` is **compatibility-only** in LangChain 1.x (no longer actively maintained); implement the supervisor as ordinary tools via `create_agent`.

**Key defaults that change topology:** `output_mode='last_message'` (not `full_history`); `parallel_tool_calls=False`; `add_handoff_messages=True`. Give the supervisor a `forward_message` tool so it does not paraphrase the specialist (telephone-game mitigation).

**Concrete example:** A customer asks "I need a refund for order #4521." The supervisor classifies intent (LLM call 1), routes to the billing worker (LLM call 2 for lookup + refund processing), control returns to supervisor for synthesis (LLM call 3). Total: 3 LLM calls, ~4.2-9.1s latency.

### 2.3 Orchestrator-Worker (Anthropic Research, Magentic-One)

A supervisor with a **plan ledger** and **waves**. The lead writes the plan to Memory (200k windows truncate). Subs get objective, output format, tool list, stop boundary; isolated windows; condensed summaries. CitationAgent is a separate hop.

Magentic-One: outer Task Ledger (facts, guesses, plan) + inner Progress Ledger (done? looping? who next?). Stall detector: paper max 2 stalls; AutoGen default `max_stalls=3`, `max_turns=20`. Stall triggers **replan**, not another reciprocal handoff. AutoGen is maintenance mode (2026); new work = Microsoft Agent Framework 1.0.

**Key Anthropic principle:** Write subagent output to a **filesystem** and pass **references** -- avoids the telephone game and the cost of copying large artifacts through the coordinator.

### 2.4 Swarm / Typed Handoff

Currently active agent picks the next hop. LangGraph: `create_handoff_tool` produces `Command(goto=agent_name, graph=Command.PARENT, update={messages, active_agent})`. Each specialist holds its own tools plus handoff tools for every peer.

**Concrete example:** User asks Agent A (billing) about a shipping question. Agent A realizes it's out of scope, issues `Command(goto="Agent B")`. Agent B (shipping) resolves the query and returns directly to the user. Total: 2 LLM calls vs. supervisor's 3-4. No SPOF. Risk: harder to observe/debug; 91% routing accuracy vs. supervisor's 94%.

**Failure mode:** Reciprocal `transfer_to_sales` <-> `transfer_to_support` with no hop cap has **no p99** -- the system loops forever.

### 2.5 Hierarchical (Supervisor-of-Supervisors)

A compiled supervisor sits in another supervisor's `agents=` list. Use it when teams have **separate checkpointers, tool IAM, and release cadences** -- not because the org chart is a tree. Each level adds at least 1 model call and a context splice.

**Resilience advantage:** Under single-agent failure, hierarchical degrades only **5.5%** vs. 23% for linear pipelines and 31% for flat swarms.

**Erlang/OTP-Inspired Restart Strategies:**

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| **one-for-one** | Only the failed child restarts | Independent children |
| **one-for-all** | All children restart when any fails | Interdependent children |
| **rest-for-one** | Failed child + all subsequently-started children restart | Ordered pipelines |

Supervisors enforce restart tolerance via MaxRestarts within MaxTime seconds. Exceeding this causes the supervisor to terminate and escalate upward -- the "let it crash" philosophy isolates failure at the correct hierarchy level.

```
┌────────────────────────────────────────────────────────────┐
│                    SUPERVISOR TREE                          │
│                                                            │
│              ┌──────────────┐                              │
│              │  Root Sup.    │                              │
│              │  max: 5/60s  │  (5 restarts per 60s max)   │
│              └──────┬───────┘                              │
│           ┌─────────┼─────────┐                            │
│           v         v         v                            │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│    │ Team Sup. │ │ Team Sup. │ │ Team Sup. │                │
│    │ one-for-  │ │ one-for-  │ │ rest-for- │                │
│    │ one       │ │ all       │ │ one       │                │
│    └────┬─────┘ └────┬─────┘ └────┬─────┘                │
│     ┌───┼───┐    ┌───┼───┐    ┌───┼───┐                  │
│     v   v   v    v   v   v    v   v   v                  │
│    A1  A2  A3   B1  B2  B3   C1  C2  C3                  │
│                                                            │
│  A2 fails -> only A2 restarts (one-for-one)               │
│  B2 fails -> B1, B2, B3 all restart (one-for-all)         │
│  C2 fails -> C2, C3 restart; C1 untouched (rest-for-one) │
│  Team Sup exceeds 5/60s -> terminates, Root Sup decides   │
└────────────────────────────────────────────────────────────┘
```

### 2.6 Pipeline (Sequential)

Deterministic linear execution: Agent A -> B -> C with no branching. Implemented by CrewAI Sequential process and Google ADK `SequentialAgent`. Ideal for fixed-order workflows (research -> write -> review).

**Reliability math:** 10 stages at 85%/stage = ~20% end-to-end success. Mitigation: validation gates between stages, retry with fallback model.

### 2.7 Framework Comparison (2026)

| Aspect | LangGraph | OpenAI Agents SDK | CrewAI | Google ADK | AutoGen/MAF |
|--------|-----------|-------------------|--------|------------|-------------|
| **Orchestration** | Graph-based state machine | Single-agent loop + handoffs | Role-based crews + Flows | Tree hierarchy + workflow agents | Conversation -> Graph (MAF) |
| **State** | Typed, checkpointed, immutable | Shared conversation + Sessions | Task outputs (Crews) + event-sourced (Flows) | Shared session state | Conversation history -> typed nodes |
| **Communication** | Shared state + handoff tools | Handoff = tool returning Agent | Sequential task output passing | Shared state + LLM delegation | Natural language messages |
| **HITL** | `interrupt()` | `execute_tools=False` / `needs_approval` | Callbacks, triggers | Pause/resume anywhere | `human_input_mode` |
| **Model support** | Any LLM | 100+ via LiteLLM | Fully agnostic | Gemini-optimized, others via LiteLLM | Any LLM |
| **Complex task success** | 62% | Production (v0.17.1) | 54% (5.76x faster) | Production (v1.x) | Maintenance mode |

### 2.8 Agent Communication Protocols

**A2A (Agent-to-Agent Protocol):** Google-introduced (April 2025), Linux Foundation governance, v1.0 early 2026, 150+ organizations. Agents advertise capabilities via Agent Cards, exchange tasks via JSON-RPC over HTTPS, stream updates via SSE. Task lifecycle: SUBMITTED -> WORKING -> COMPLETED | FAILED | CANCELED | REJECTED | INPUT_REQUIRED | AUTH_REQUIRED. **Terminal tasks are immutable** -- refinements create a new `taskId` in the same `contextId`.

**MCP (Model Context Protocol):** Anthropic-created. Standardizes agent-to-tool and agent-to-data connections. Client-server design with schema consistency, access control, auditability.

**Critical distinction:** MCP is the **tool bus**; A2A is the **agent bus**. Do not flatten a partner agent into `tools/call`. Terminal A2A tasks are immutable. Microsoft Learn 2026: platform-native orchestration for internal subagents; MCP for tools/data; A2A for opaque, cross-platform, cross-org agents.

---

## 3. Architecture & System Design

### 3.1 Control Plane / Data Plane Architecture

```
+----------------------------------------------------------------------------------+
| CLIENTS / SURFACES                                                                |
|  chat / support widget | research desk | A2A partner | L2 / approver console      |
+------------+-----------------------------------------------------------------------------+
             | TLS + session JWT (tenant/roles FROM TOKEN) + correlation-id
             | structured action JSON -- MODEL NEVER ROUTES / VOTES / TIMES HITL
             v
+----------------------------------------------------------------------------------+
| CONTROL PLANE (orchestrator runtime -- your process / Agent Server / Temporal)    |
|                                                                                   |
|  +------------+  +------------+  +------------+  +------------+  +-----------+   |
|  | Edge       |->| Policy     |->| SUPERVISOR |->| JOIN /     |->| HITL GATE |   |
|  | SSO, cid,  |  | PII redact |  | ROUTER     |  | VOTE tau   |  | timeout-  |   |
|  | thread_id  |  | BEFORE any |  | next agent,|  | accept |   |  | DENY      |   |
|  |            |  | hop copies |  | hop cap, $ |  | extra rnd  |  | (9.6.2);  |   |
|  |            |  | the trans- |  | cap, fan-  |  | | ESCALATE |  | write     |   |
|  |            |  | cript      |  | out cap    |  | never avg  |  | tools     |   |
|  +------------+  +-----+------+  +-----+------+  +-----+------+  +-----+-----+   |
|                        |               |               |               |          |
|                        v               v               v               v          |
|                 +---------------------------------------------------------------+ |
|                 | ORCHESTRATION LEDGER                                           | |
|                 |  typed Handoff (reason, priority, artifact ids)                | |
|                 |  downscope token in on_handoff -- RAISE on authz fail          | |
|                 |  four edges: SLA/hops - confidence/tau - cost/$ - security     | |
|                 |  ping-pong detector; stall -> replan not re-hop                | |
|                 +---------------------------------------------------------------+ |
|  +------------+  +------------+                        +-------------------+      |
|  | Circuit    |  | Fallback   |                        | SIGTERM / drain   |      |
|  | ONE per    |  | primary    |                        | park HITL; do not |      |
|  | specialist |  | specialist |                        | cut in-flight     |      |
|  | / provider |  | -> peer -> |                        | research (rainbow |      |
|  | (not one   |  | degraded   |                        | pin prompt ver)   |      |
|  | global)    |  | JSON       |                        |                   |      |
|  +------------+  +------------+                        +-------------------+      |
+----------------------------------------------------------------------------------+
             |                                    |
             | inner ReAct / MCP / A2A            | durable wait (zero compute)
             v                                    v
+---------------------------------+  +--------------------------------------------+
| DATA PLANE  SPECIALISTS         |  | DATA PLANE  HITL QUEUE + A2A               |
| (each inner loop = ReAct/P&E)   |  | model NEVER holds Stripe or partner PAT   |
|                                 |  |                                            |
|  +----------+  +-------------+  |  |  Temporal Signal / Kafka hitl.approvals    |
|  | FAQ / L1 |  | Billing /   |  |  |  ticket = thread_id + interrupt id         |
|  | kb_search|  | L2 refund   |  |  |  SLA timer OUTSIDE the graph               |
|  +----------+  +-------------+  |  |  overflow: shed low-pri; NEVER auto-approve|
|  +----------+  +-------------+  |  |  A2A Task: SUBMITTED->WORKING->COMPLETED|  |
|  | Research |  | Judge /     |  |  |    FAILED|CANCELED|REJECTED|INPUT_REQUIRED |
|  | sub + fs |  | Citation    |  |  |    |AUTH_REQUIRED; terminal = immutable    |
|  +----------+  +-------------+  |  |                                            |
|  isolated window OR sticky hist |  |                                            |
+------------+--------------------+  +--------------------------------------------+
             |                                    |
             v                                    v
+---------------------------------+  +--------------------------------------------+
| TOOL PROXIES (per-agent MCP)    |  | PERSISTENCE (resume identity)              |
| Zero-Trust wrap; RFC 8707 aud.  |  |                                            |
| tenant NEVER from tool args     |  |  thread_id / RunState / contextId+taskId   |
| downscope jti at handoff        |  |  HITL row (sla_s, decision, timeout-deny)  |
+---------------------------------+  |  Artifacts (fs refs, A2A ids)              |
                                     |  Vote log (vector, tau, dropped hash)       |
                                     |  token vault NOT in messages                |
                                     +--------------------------------------------+
                                                  |
+----------------------------------------------------------------------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                   |
|  +------------+  +------------+  +------------+  +--------------------+           |
|  | Audit WORM |  | Metrics    |  | Traces     |  | Usage              |           |
|  | cid,from/to|  | hops, $,   |  | supervisor |  | calls x SKU,       |           |
|  | mechanism, |  | p50/p95,   |  | -> worker  |  | search SKU,        |           |
|  | jti, tau,  |  | hitl wait, |  | -> join;   |  | total_cost_usd,    |           |
|  | vote vector|  | breaker    |  | OTel spans |  | remaining_usd      |           |
|  +------------+  +------------+  +------------+  +--------------------+           |
+----------------------------------------------------------------------------------+
```

### 3.2 Supervisor Pattern Request Flow

```
User Query: "I need a refund for order #4521"
    |
    v
+----------+  1. Classify intent     +--------------+
|  User    | ----------------------> |  Supervisor   |
|          |                         |              |
|          |                         |  LLM call 1: |
|          |                         |  route to     |
|          |                         |  Billing      |
|          |                         +------+-------+
|          |                                |
|          |                                v  2. Delegated work
|          |                         +--------------+
|          |                         |  Billing     |
|          |                         |  Worker      |
|          |                         |              |
|          |                         |  LLM call 2: |
|          |                         |  lookup order |
|          |                         |  process refund
|          |                         +------+-------+
|          |                                |
|          |                                v  3. Return to supervisor
|          |                         +--------------+
|          |  4. Synthesized response |  Supervisor   |
|          | <-----------------------|              |
|          |                         |  LLM call 3: |
|          |     Total: 3 LLM calls  |  synthesize  |
+----------+     ~4.2-9.1s latency   +--------------+
```

### 3.3 Hierarchical Pattern with Fault Isolation

```
+-------------------------------------------------------------+
|                     HIERARCHICAL PATTERN                      |
|                                                              |
|                 +-------------------+                        |
|                 |  ROOT SUPERVISOR   |                        |
|                 |  (strategic layer) |                        |
|                 +--------+----------+                        |
|            +-------------+-------------+                     |
|            v             v             v                     |
| +-------------+ +-------------+ +-------------+             |
| | TEAM SUP. A  | | TEAM SUP. B  | | TEAM SUP. C  |             |
| | (Sales)      | | (Support)    | | (Ops)        |             |
| +------+------+ +------+------+ +------+------+             |
|   +----+----+     +----+----+     +----+----+               |
|   v    v    v     v    v    v     v    v    v               |
|  W1   W2   W3   W4   W5   W6   W7   W8   W9               |
|                                                              |
| ESCALATION PATH:                                            |
| W5 fails -> Team B retries with W4 or W6                    |
| Team B exhausts retries -> Root reassigns to Team C         |
| Root fails -> system halt + alert (SPOF: hot standby)       |
|                                                              |
| DEGRADATION ON 1 FAILURE:                                   |
| Hierarchical: 5.5% | Pipeline: 23% | Flat Swarm: 31%       |
+-------------------------------------------------------------+
```

### 3.4 Swarm Pattern (Peer-to-Peer Topology)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          SWARM PATTERN                                       │
│                                                                              │
│     ┌─────────┐  handoff   ┌─────────┐  handoff   ┌─────────┐             │
│     │ Agent A  │<─────────>│ Agent B  │<─────────>│ Agent C  │             │
│     │(Billing) │           │(Shipping)│           │(Returns) │             │
│     └────┬────┘           └────┬────┘           └────┬────┘             │
│          │                     │                     │                     │
│          └─────────────────────┼─────────────────────┘                     │
│                                │                                            │
│                           ┌────v────┐                                      │
│                           │ Agent D  │                                      │
│                           │(Tech Sup)│                                      │
│                           └─────────┘                                      │
│                                                                              │
│  Communication: Command(goto="agent_b", graph=Command.PARENT)              │
│  State: each agent sees full conversation + can write to shared state       │
│  No SPOF: any agent can be entry point                                      │
│  Risk: harder to observe/debug, lower routing accuracy (91% vs 94%)         │
│                                                                              │
│  DATA FLOW:                                                                 │
│  User -> Agent A (handles billing) -> realizes shipping question             │
│       -> Command(goto="Agent B") -> Agent B resolves -> returns to user      │
│  Total: 2 LLM calls (vs. supervisor's 3-4)                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Parallel Fan-Out / Fan-In Pattern

```
                 +----------+
                 | Dispatch  |
                 +----+-----+
           +----------+----------+
           v          v          v
    +----------+ +----------+ +----------+
    | Worker 1  | | Worker 2  | | Worker 3  |    (concurrent)
    +----+-----+ +----+-----+ +----+-----+
         +-------------+----------+
                  +----v-----+
                  | Reducer   |  Deterministic merge function
                  | (merge)  |  resolves parallel state updates
                  +----------+
```

### 3.6 End-to-End Request Flow (Detailed)

1. **Ingress.** Gateway stamps `correlation_id`. Bind `tenant_id`/roles from the **verified token**, never from tool JSON the model invented. Pin `thread_id` (or A2A `contextId`).

2. **Policy.** DLP-redact PII **before** any specialist sees the transcript. Classify reversibility from the **tool manifest**, not the agent's self-description (AISVS 9.2.3-9.2.4). **Worst class in the chain wins** (9.2.10).

3. **Supervisor tick.** Model emits a structured action (`transfer_to_billing`, `Spawn(n=3)`, `FINISH`, vote JSON). Runtime -- not the prompt -- checks hop cap, `$ remaining`, fan-out cap, `is_enabled` predicates, and the allowed-transition graph.

4. **Typed handoff.** Construct `Handoff(from, to, reason, priority, artifact_ids)`. `on_handoff` mints a **downscoped** token (audience = worker MCP servers; scopes = brief). Log hash of dropped history. Authorization failure **raises** -- the SDK continues the transfer if `on_handoff` returns successfully.

5. **Specialist inner loop.** ReAct/P&E inside an isolated window (research) or sticky transcript (support). Tools go through MCP PEP. Subagents-as-tools: supervisor keeps the user-facing reply. Swarm handoff: specialist becomes owner; next user turn skips the router.

6. **Join.** `last_message` vs synthesizer vs **vote**. Parse votes into a closed answer set. If agreement >= tau_high and independently checkable, accept. If < tau_low, do not average; escalate to a position-swapped judge, then a human.

7. **HITL branch.** Irreversible/write tools park on a **work queue** (ticket = `thread_id` + interrupt id). Temporal `wait_condition` (zero compute). Show **canonicalized** parameters (AISVS 9.2.2) -- not a supervisor paraphrase. ASI09: friction, approval-budget per session, structured risk badges.

8. **Four escalation edges** (runtime detectors): SLA/hops, confidence/tau, cost/$, security/class. Capability miss (`REJECTED`, unbound skill) is a fifth cousin: specialist or human, not a silent tool invent.

9. **A2A (cross-org).** `SendMessage` on a skill from the Agent Card. Terminal tasks never restart; refinements create a new `taskId` in the same `contextId`.

10. **Halt + WORM.** cid, from/to, mechanism, `principal_id`, `token_jti`, `tools_enabled`, `policy_version`, `human_gate`, artifact ids, vote_vector, tau.

### 3.7 Planes Separation (Do Not Couple)

| Plane | Owns | Typical Backing | Failure If Coupled |
|-------|------|-----------------|-------------------|
| **Control** | Next agent, hop/$/fan-out caps, vote join, HITL timeout-deny, kill-switch | Orchestrator + Temporal/Agent Server + IdP | Prompt-as-router; 50-sub fan-out; timeout-allow refunds |
| **Data (specialists)** | Inner ReAct, worker tools, condensed summaries | Model APIs + MCP servers | PII copied on every hop; telephone game through the lead |
| **Data (A2A/HITL)** | Partner Task lifecycle; approval queue | A2A + Temporal Signal / Kafka | Blocking `SendMessage` as your only HITL; console `input()` as SLA |
| **Tool proxies** | Per-agent MCP allowlist; downscoped outbound token | MCP gateway PEP | Worker inherits supervisor GitHub admin (agent confused deputy) |
| **Persistence** | `thread_id` / `RunState` / `contextId+taskId` / HITL row / artifact refs | Postgres checkpointer + queue + object store | Laptop `invoke()`; restart from scratch after 500 or a 15-min approval |
| **Telemetry** | WORM of handoff / vote / tau / jti / human_gate | SIEM + OTel | Finance dashboards that ignore search SKU and hop count |

---

## 4. Key Algorithms & Mechanics

### 4.1 Typed State Handoffs

#### OpenAI Agents SDK -- Two Official Primitives

| Pattern | Primitive | Next User-Visible Token | Guardrails | Use |
|---------|-----------|------------------------|------------|-----|
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool `transfer_to_<agent>` | Specialist | Input = first only; output = last only | Conversation ownership changes |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Nested run; `needs_approval` supported | Bounded subtask; manager synthesizes |

**Typed knobs (not prompt):** `tool_name_override`, `tool_description_override`, `on_handoff` (log, prefetch, downscope token), `input_type` (Pydantic metadata: `reason`, `priority` -- does not choose destination and does not replace the next agent's input), `input_filter` / `RunConfig.handoff_input_filter`, `is_enabled` (predicate, evaluated **before** args), `nest_handoff_history` (opt-in beta; does not redact PII).

**Critical guardrail gap:** Input guardrails = **first** agent only; output guardrails = **last** only; tool-input guardrails **do not wrap** handoffs. Policy must live on the **worker**.

#### LangGraph

Two handoff patterns: (1) single agent + middleware (`@wrap_model_call` swaps prompt/tools -- recommended default); (2) subgraph agents + `Command.PARENT`. Subgraph handoffs must pass the triggering `AIMessage` **and** a `ToolMessage` with matching `tool_call_id`. Full subagent history is optional and usually wrong.

#### Google ADK

Tree hierarchy with two rules -- parent manages sub-agents, each agent has exactly one parent. `LlmAgent` uses AutoFlow (LLM-driven delegation based on description fields). Three workflow agents: `SequentialAgent`, `ParallelAgent`, `LoopAgent`. Communication through shared session state (a "digital whiteboard").

#### CrewAI

Role-based DSL: Agent (role, goal, backstory) + Task + Tool + Crew. Two orchestration layers -- Crews (autonomous collaboration) and Flows (event-driven with `@start`, `@listen`, `@router` decorators providing state threading, persistence, branching). CrewAI `Process.hierarchical` requires `manager_llm` / `manager_agent`; manager must not sit in `agents=` and must not hold ordinary tools.

#### Shared vs. Partitioned State

| Style | What is Shared | Isolation | Typical |
|-------|---------------|-----------|---------|
| Shared transcript | One `messages` reducer | Weak unless filtered | Swarm, OpenAI default handoff |
| Partitioned windows | Brief + summary / filesystem ref | Strong | Anthropic subagents |
| Blackboard + private scratch | Public board + debate spaces | Medium | LbMAS |
| A2A artifacts | Opaque callee; caller sees Task + Artifact | Strongest across orgs | Cross-company |

### 4.2 Consensus Tau (Join, Not Topology)

Consensus attaches to a fan-in: `Send` workers, debate rounds, or self-consistency samples. The **runtime** decides: accept, another round, or **escalate**.

#### Published Results

| Join Method | Mechanism | Known Numbers | Failure Mode |
|-------------|-----------|---------------|-------------|
| Majority / self-consistency | Sample k, vote on parsed answer | PaLM-540B GSM8K 56.5% -> 74.4% at k=40 (Wang) | Correlated errors lock a wrong majority |
| Multi-agent majority, no debate | Independent agents, vote once | Du arithmetic 69.0% vs single 67.0% -- weak | Same |
| Debate (Du 2023/ICML 2024) | N propose, R critique; majority if split | 3x2: arithmetic **81.8%** vs reflection 72.1% vs majority 69.0% | Agreeable RLHF collapse; shared hallucination survives |
| MAD + judge (Liang) | Two debaters; judge discriminative or extractive | Adaptive break; LLM judges unfair across model families | Judge self-preference |
| LLM-as-judge (Zheng) | Pairwise / rubric | GPT-4 >80% human agreement; verbosity/self-enhancement bias | Verbosity wins |
| Anthropic research judge | One call, 0.0-1.0 + pass/fail | **Multiple judges were worse** | Rubric injection |
| MoA (Wang ICLR 2025) | Layered; each layer reads prior | OSS MoA 65.1% vs GPT-4o 57.5% AlpacaEval 2.0 LC | Sequential layer latency |

#### ICLR 2025 MAD Findings (5 frameworks vs. CoT and Self-Consistency)

- MAD does **not** consistently outperform single-agent strategies
- GPT-4o-mini: CoT scored 80.73% on MMLU vs. best MAD at 80.40%
- Self-Consistency scored 95.67% on GSM8k vs. best MAD at 94.93%
- Exceptions: Exchange-of-Thoughts on MATH (75.93% vs. 72.87% CoT), AgentVerse on HumanEval (85.37% vs. 78.05% CoT)
- MAD is "overly aggressive" -- frequently flips correct answers to incorrect ones
- AgentVerse collapsed to 5.47% on GSM8k with Llama 3.1-8b (strict formatting failures)
- **Mixed-model configurations show promise:** GPT-4o-mini + Llama 3.1-70b yielded 95.00% on GSM8k and 88.20% on MMLU -- structural decorrelation of blind spots

**2025 consensus:** Majority voting is optimal for reasoning tasks; consensus is optimal for knowledge tasks.

#### Disagreement Is a Signal

- Du: agents **omit** facts they disagree on
- Estornell & Liu: **tyranny of the majority** -- similar models converge on a shared misconception; diversity pruning (k=5 distinct) reduces the echo
- Minority Sentinel (2026): majority already correct in 74.5% of divergent cases; overturn only when P(minority-truth) > per-dataset tau with >=95% majority-correct preservation
- Selective self-reference: 3/5 -> 35.5% majority correctness; 4/5 -> 57.7%; 5/5 -> 78.7% -- **tau is task-specific**
- Voting-ensemble abstention: raising the threshold can lift precision (73.1% -> 93.9%) at the cost of yield -- production mapping is **abstain -> escalate**, not "guess anyway"

#### Production Join Policy

1. Parse votes into a **closed** answer set (schema, not prose)
2. If agreement >= tau_high (often unanimous or k-1) **and** answers are independently checkable -> accept
3. If agreement < tau_low -> **do not average**. Position-swapped judge, then human if the judge disagrees or the domain is irreversible (AISVS 9.2.10)
4. Never let the same model family both propose and judge without a position-swap + second family

Use debate as a **verifier** on high-value, non-parallelizable answers (legal memo), not as the default topology.

### 4.3 Human-in-the-Loop (Timeout-Deny)

HITL is a **durable wait** with an authenticated resume, not "ask the model to be careful."

#### HITL Mechanisms Across Frameworks

| Mechanism | Pause | Resume | Durable Wait? | Multi-Agent Note |
|-----------|-------|--------|---------------|-----------------|
| LangGraph `interrupt()` | GraphInterrupt | `Command(resume=...)` + checkpointer | Only if Agent Server / Temporal | Interrupt inside a worker must propagate to the parent |
| `HumanInTheLoopMiddleware` | After model, before listed tools | approve / edit / reject / respond | Same checkpointer rule | **Missing `interrupt_on` key = auto-approve**; privilege bug if new write tool added |
| OpenAI `needs_approval` | `result.interruptions` | `state.approve()/reject()` + same session | Process-held unless you persist `RunState` | `_max_turns=10` is NOT paused by wall-clock HITL |
| A2A `INPUT_REQUIRED` / `AUTH_REQUIRED` | Task interrupted | Client `SendMessage` on same `taskId` | Yes, by spec | Client may re-delegate `AUTH_REQUIRED` up the chain |
| Temporal Signal + `wait_condition` | Workflow parks (zero compute) | Signal | Yes | Wait + timeout -> escalate -> second wait -> auto-reject |
| CrewAI / AG2 `HumanClient` | Console / UI | Human types | No unless Hub WAL | Not an SLA |

**AISVS 9.6.2:** If approval time is not met, **block** the pending action -- timeout-**deny**, not timeout-allow. EU AI Act Art. 14 is the legal twin. LangGraph does not put a TTL on `interrupt()` -- you must add one via Temporal or queue TTL.

#### Risk-Tiered Action Classification

| Tier | Action Type | Approval | Example |
|------|-------------|----------|---------|
| 1 | Read-only | Autonomous | Database query, log search |
| 2 | Reversible | Notify | Draft email, create ticket |
| 3 | External | Pre-approve | Send email, API call to partner |
| 4 | High-risk / Irreversible | Mandatory human | Financial transaction, production deploy |

**Gate decision variables:** Reversibility, Blast radius, Confidence threshold. If any two are elevated, add a gate.

#### Tiered Escalation SLAs

| Tier | Trigger | SLA | Escalation Target |
|------|---------|-----|--------------------|
| 1 | Confidence 0.6-0.8 | 4 hours | Team member |
| 2 | Confidence <0.6 or high blast radius | 1 hour | Team lead |
| 3 | Compliance/legal/critical infrastructure | 15 minutes | Designated authority (auto-page) |

#### Confidence Compounding Problem

Miscalibration compounds across chains. Three agents each off by ~15pp: claimed 90% per-step confidence implies only ~42% probability all three steps are correct. RLHF-trained models tend to express highest confidence on incorrect outputs -- claimed 90% can correspond to ~75% real-world accuracy.

### 4.4 Four Escalation Edges

Escalation is a **control-plane edge**, not a prompt. Destinations: another specialist or a human.

| Trigger | Detector (Runtime, Not LLM) | Typical Action | Source |
|---------|----------------------------|----------------|--------|
| **SLA / hop budget** | Hop counter; OpenAI `max_turns` default 10; Magentic 20; LangGraph `recursion_limit` | Force `escalate_to_human` after N transfers; stall -> replan | SDK defaults; AISVS 9.1.2 |
| **Confidence / disagreement** | Vote fraction < tau; judge score < threshold; Magentic "looping?" | Extra debate round **or** human; do not silently majority | Du; Estornell |
| **Cost** | Runtime `$` / token counter on the thread | Cap subagents; 80% budget **forbids** new `Send` | Anthropic; AISVS 9.1.2 |
| **Security / reversibility** | Tool-manifest class; **worst class in the chain** wins (9.2.10) | HITL before write; `AUTH_REQUIRED` for user gesture | AISVS C9.2 |

### 4.5 Complexity Analysis

Let H = hops, W = parallel workers in a wave, N = debate agents, R = critique rounds, L = hierarchy levels, C = A2A sibling tasks.

| Pattern | Model Calls | Wall-Clock | $ Shape |
|---------|-------------|------------|---------|
| Supervisor serial | Theta(H) on critical path | sum(worker p99) + supervisor ticks | Serial cost |
| Parallel `Send` / wave | Theta(W) completions | max(W) + join | Multiplied RPM |
| Swarm sticky turn 2 | Theta(1) specialist call (no router) | Single specialist p99 | Cheapest repeat |
| Hierarchical | Theta(H x L) extra splices | Multiplied per-level | IAM/SLO isolation |
| Debate | Theta(N x (R+1)) utterances | rounds x max(speaker) if sequential | Verifier tax |
| Vote join | O(N) parse + fraction; O(1) policy | Negligible | No model call to "average" |
| HITL park | O(1) worker CPU (zero compute) | Queue SLO, orthogonal to model p99 | No model cost |
| Fan-out catastrophe | Theta(50 x 10) | No p99 | $4,000/1k |

### 4.6 Core Invariants (Memorize for Interviews)

1. **The model never routes, hands off, grants authority, counts a vote, or times out HITL.** Runtime mutates durable state (AISVS 9.5.3).
2. **Hop cap, $ cap, fan-out cap, and tool allowlists live in the runtime.** Prompts are advisory.
3. **Typed `input_type` is metadata, not authorization.** `is_enabled` cannot see parsed args. Check in `on_handoff` and raise.
4. **Downscope at handoff.** Worker token audience = worker MCP servers; scopes = brief. Cannot be widened by asking the supervisor.
5. **Worst reversibility class in the chain wins** (AISVS 9.2.10). FAQ auto-approve does not survive `issue_refund`.
6. **HITL timeout-deny** (AISVS 9.6.2). LangGraph interrupt has no TTL; you add one. Overflow never auto-approves irreversible class.
7. **Join at tau; never average below tau_low.** Same-family proposer+judge without position-swap is Zheng bias. tau is task-specific.
8. **Per-agent tool policy** (AISVS 9.5.1). Lead does not hold production write tools. Judge reads artifacts; does not mutate Stripe.
9. **MCP is the tool bus; A2A is the agent bus.** Do not flatten partners into `tools/call`. Terminal A2A tasks are immutable.
10. **Interrupt in a worker surfaces on the outermost graph.** Resume the top-level agent.
11. **Kill-switch is out-of-band** (AISVS 9.1.3 / 9.6.3), not a prompt token the looping agents must choose to emit.
12. **Secrets never in model-observable / checkpointed state** (AISVS 9.5.4).

---

## 5. Token Economics & Cost Analysis

### 5.1 Working Prices (2026)

| Model | Input / MTok | Output / MTok | Cache Hit / MTok |
|-------|-------------|---------------|-----------------|
| Claude Sonnet 5 | $2 | $10 | $0.20 |
| Claude Opus 5 | $5 | $25 | $0.50 |
| GPT-5.4 | $2.50 | $15 | $0.25 |
| Claude Sonnet 4 | $3 | $15 | $0.30 |
| Claude Opus 4 | $15 | $75 | $1.50 |
| GPT-4.1-mini | $0.40 | $1.60 | -- |

### 5.2 Agentic Cost Multipliers

- Single chat -> Single agent: **~4x** tokens (Anthropic)
- Single chat -> Multi-agent: **~15x** tokens (Anthropic)
- Gartner (March 2026): agentic models require **5-30x** more tokens per task than standard chatbot
- Unconstrained software engineering agent: **$5-8 per task** in API fees alone

### 5.3 Cost Per Topology (Loop S -- L1 Ticket, Sonnet 5)

Reference call: 2,000 input + 400 output. Per-call cost: $0.008.

| Pattern | Calls | $/ticket | **$/1k tickets** |
|---------|-------|----------|------------------|
| Handoffs / sticky specialist | 3 | $0.024 | **$24** |
| Subagents (supervisor join) | 4 | $0.032 | **$32** |
| Router + parallel two specialists + synth | 5 | $0.040 | **$40** |
| Same 4-call subagents, GPT-5.4 | 4 x $0.011 | $0.044 | **$44** |
| Repeat turn 2, handoffs | 2 | $0.016 | **$16 extra** |
| Repeat turn 2, subagents | 4 | $0.032 | **$32 extra** |

**Coordination tax:** "Always return to supervisor" costs **+$8/1k/turn** vs. a sticky specialist. That tax buys centralized policy and a single user-facing owner.

### 5.4 Research Loop Costs (Loop R)

Chat baseline: 2,000 in + 500 out on Sonnet 5 = $0.009/chat. Single-agent research 4x = $0.036 -> **$36/1k**. Multi-agent 15x = $0.135 -> **$135/1k**. 30% Opus 5 + 70% Sonnet 5 on the 15x pile: **~$240/1k**.

Claude web search: **$10/1K searches**. 3 subs x 8 searches = **$0.24/task** -- often **larger than Sonnet tokens** on Loop S. Count it.

### 5.5 Fan-Out Catastrophe (Loop D)

50 subagents x 10 calls x $0.008 = $4/ticket -> **$4,000/1k**. AISVS 9.1.2 is an NFR, not a nice-to-have.

### 5.6 Debate Cost

Each debate utterance: 1,500 in + 400 out on Sonnet 5 = $0.007/utterance.

| Consensus Recipe | Utterances | Extra vs. 1 Greedy CoT | $/1k Extra |
|-----------------|------------|------------------------|-----------|
| Majority N=3, no debate | 3 | 2 | **$14** |
| Du debate 3x2 | 9 | 8 | **$56** |
| MAD 2 debaters x ~3 rounds + judge | ~9 | 8 | **$56** |
| Self-consistency k=5 | 5 | 4 | **$28** |
| Self-consistency k=40 (Wang) | 40 | 39 | **$273** |

### 5.7 The Re-Sent Context Problem

The single biggest invisible cost in agentic systems. Re-sent context (system prompts, tool definitions, state history repeated across multiple calls) accounts for **62% of total agent inference bills** (Stanford Digital Economy Lab).

**Worked example:**
```
System prompt + tool schemas: ~4,000 tokens (sent every LLM call)
Conversation history growth: ~500 tokens/turn
5-turn agent workflow = 5 calls

Call 1: 4,000 + 0     = 4,000 input tokens
Call 2: 4,000 + 500   = 4,500
Call 3: 4,000 + 1,000 = 5,000
Call 4: 4,000 + 1,500 = 5,500
Call 5: 4,000 + 2,000 = 6,000
                        ------
Total input:            25,000 tokens
Without re-sent:         6,500 tokens
Re-sent overhead:       18,500 tokens (74% of total)

At Opus 4 ($15/1M input): $0.375 actual vs. $0.098 minimal = 3.8x waste
```

### 5.8 Cost Optimization Strategies

| Strategy | Effort | Cost Reduction | Quality Impact |
|----------|--------|---------------|---------------|
| **Prompt caching** | Low | 59-70% | None |
| **Model routing** (cheap triage + premium specialist) | Medium | 70-90% | Minimal if well-calibrated |
| **Structured summaries** (not full history) | Medium | 40-60% | Risk of information loss |
| **Step/token limits** | Low | Variable | Hard ceiling prevents runaway |
| **Batch scheduling** | Medium | 20-30% | Adds latency |
| **Combined** | High | 60-80% | None if properly tuned |

**Prompt-cache shape:** Supervisor system prompt + worker playbooks should be prompt-cached. Sonnet 5 cache hit $0.20/MTok vs $2 is a 10x input discount for the static prefix. Hierarchical supervisors with shared team prompts are the best cache shape; swarms that rewrite `active_agent` prompts every hop cache worse.

### 5.9 The Falling Price, Rising Bill Paradox

Token prices fell ~80% between 2025 and 2026, yet enterprise LLM API spend passed $8.4B in 2025 and is on track to double. Weekly token processing on OpenRouter surged from 0.4 trillion (Dec 2024) to 27.0 trillion (March 2026) -- a **68x** increase in 15 months.

### 5.10 Latency SLA Targets

No vendor publishes supervisor-worker p50/p95/p99 as of 2026-09-23.

| Pattern | p99 (Conceptual) | Notes |
|---------|------------------|-------|
| Sequential pipeline / swarm handoff | sum(stage p99) | Sticky support OK; three-domain research not |
| Supervisor, `parallel_tool_calls=False` | sum(worker p99) + supervisor ticks | Default serializes workers |
| Parallel `Send` / wave | max(worker p99) + join + lead | Vague briefs duplicate work; join waits on slowest |
| Debate / MoA layers | rounds x (max speaker or sequential layer) | Verification tax on critical path |
| A2A blocking | Inherits callee p99 + auth | Cold Agent Card / OAuth dominates LLM time |
| HITL | Not an LLM SLO | Measure decision latency separately |

**Worked p99 envelope for Loop S:** Supervisor route 800ms p99, specialist (LLM+tools) 3.5s p99, join/synth 1.2s p99. Sequential: 800+3500+3500+1200 = **~9.0s**. Parallel `Send`: 800+3500+1200 = **~5.5s**. Swarm sticky turn 2: **~3.5s**. Debate 3x2: **~+12s** extra.

Without multi-turn reasoning, accuracy on complex tasks plateaus at ~60-70%. Achieving 95%+ accuracy required for enterprise processes demands longer thinking -- multi-agent is inherently slower but more accurate for complex tasks.

### 5.11 Non-Functional Requirements

| NFR | Working Target | Tension |
|-----|---------------|---------|
| **Availability** | 99.9% control plane. Per-specialist breaker. One worker 500 does not kill FAQ. A2A `FAILED` does not kill `contextId` | Supervisor is SPOF on serial topology; failover busts prefix cache |
| **RPO** | Interactive: last checkpointed super-step. HITL: last queue row. A2A: last Task status (terminal is immutable) | `InMemorySaver` / process-held OpenAI run: RPO = crash loses the approval |
| **RTO** | Stateless resume from checkpoint. Interactive hop timeout 2-8s then breaker. HITL RTO is the queue SLA | Fast degraded JSON vs. bit-identical specialist result |
| **Consistency** | `active_agent` LastValue -- disable parallel handoffs or you race. Vote join is a pure function of the parsed vector + tau | Eventual artifact write vs. lead synthesizing a stale summary |
| **Compliance** | AISVS C9: budgets 9.1.2, kill-switch 9.1.3/9.6.3, timeout-deny 9.6.2, per-agent tools 9.5.1, downscoped token 9.5.2, policy engine 9.5.3, delegation policy 9.5.5, crypto agent identity 9.4.1 | Hosted lead vs. VPC tools; nested handoff re-embeds PII |
| **Cost vs latency** | Loop S $24-32/1k; Loop R $135-240/1k; debate +$56/1k; Loop D $4,000/1k. Parallel Send cuts ~9.0s -> ~5.5s | Sticky handoff saves $8/1k/turn but loses centralized join |

---

## 6. Production Patterns & Code

### 6.1 Multi-Agent Control Plane (Orchestrator Runtime)

A single, self-testing runtime that encodes all invariants from sections 1-5: hop caps, dollar caps, fan-out caps, typed handoffs, downscoped tokens, vote joins at tau, HITL timeout-deny, per-specialist circuit breakers, PII redaction, and WORM audit logging.

```python
#!/usr/bin/env python3
"""Multi-agent control plane. Python 3.11+.

  python orchestrator_runtime.py

Offline self-test: no network, no LLM, no Temporal cluster.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

# --- Configuration constants (all budgets enforced by RUNTIME, not prompt) ---
INITIAL_RETRY_DELAY, MAX_RETRY_DELAY, SDK_DEFAULT_MAX_RETRIES = 0.5, 8.0, 2
HOP_CAP, USD_CAP, USD_FREEZE_FRAC = 3, 0.05, 0.80
TAU_HIGH, TAU_LOW, HITL_SLA_S = 1.0, 0.50, 900.0
CALL_USD = 0.008  # Loop S Sonnet 5 2k/400 per call
GATEWAY_AUD = "https://mcp.gateway.example"

T = TypeVar("T")

# --- Per-agent tool policy (AISVS 9.5.1) ---
# Lead does NOT hold production write tools. Judge reads; never mutates Stripe.
AGENT_TOOLS: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"route", "forward_message"}),
    "faq": frozenset({"kb_search"}),
    "billing": frozenset({"lookup_invoice"}),
    "refund": frozenset({"lookup_invoice", "issue_refund"}),
    "researcher": frozenset({"web_search", "fs_write"}),
    "judge": frozenset({"read_artifact"}),
}

# --- Allowed-transition graph (prevents arbitrary routing) ---
TRANSITIONS: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"faq", "billing", "refund", "researcher", "judge", "human"}),
    "faq": frozenset({"billing", "human", "supervisor"}),
    "billing": frozenset({"refund", "human", "supervisor"}),
    "refund": frozenset({"human", "supervisor"}),
    "researcher": frozenset({"judge", "human", "supervisor"}),
    "judge": frozenset({"human", "supervisor"}),
    "human": frozenset(),
}


# --- Structured JSON logging with correlation ---
class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname, "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "from_agent": getattr(record, "from_agent", None),
            "to_agent": getattr(record, "to_agent", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str, **kw) -> CorrelationAdapter:
    base = logging.getLogger("orch.runtime")
    if not base.handlers:
        h = logging.StreamHandler()
        h.setFormatter(JsonLogFormatter())
        base.addHandler(h)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra = {"correlation_id": correlation_id, "tenant": tenant, **kw}
    return CorrelationAdapter(base, extra)


# --- Error taxonomy ---
class TransientError(Exception):
    """408/429/5xx/529, TLS reset. Retry with jitter."""
    def __init__(self, msg, retry_after=None, status=None):
        super().__init__(msg)
        self.retry_after, self.status = retry_after, status

class PermanentError(Exception):
    """400 schema, 401/403, hop cap, $ cap, tau_low, RBAC deny. Never retry."""

class CircuitOpenError(TransientError):
    """Circuit breaker is open. Do not call the specialist."""


# --- Circuit Breaker (one per specialist, not one global) ---
class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """Do not trip on 429-with-Retry-After. Trip on 5xx/529/timeout rate."""
    def __init__(self, name, failure_threshold=5, recovery_seconds=30.0):
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state, self._failures, self._opened_at = BreakerState.CLOSED, 0, 0.0
        self._lock = asyncio.Lock()

    async def allow(self):
        async with self._lock:
            if self._state is BreakerState.OPEN:
                if (time.monotonic() - self._opened_at) >= self.recovery_seconds:
                    self._state = BreakerState.HALF_OPEN
                else:
                    raise CircuitOpenError(f"circuit_open:{self.name}")

    async def record_success(self):
        async with self._lock:
            self._failures, self._state = 0, BreakerState.CLOSED

    async def record_failure(self, *, trip=True):
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state, self._opened_at = BreakerState.OPEN, time.monotonic()

    @property
    def state(self):
        return self._state


# --- Retry with full jitter (HTTP/transport only) ---
async def retry_with_jitter(fn, *, log, attempts=SDK_DEFAULT_MAX_RETRIES + 1,
                            base=INITIAL_RETRY_DELAY, cap=MAX_RETRY_DELAY) -> T:
    """Never wraps PermanentError / CircuitOpenError."""
    last = None
    for i in range(attempts):
        try:
            return await fn()
        except (PermanentError, CircuitOpenError):
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = ra if ra and 0 < ra <= 60 else random.random() * min(cap, base * (2 ** i))
            log.warning("http_retry attempt=%s sleep=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    raise last


# --- PII redaction (before every hop splice) ---
def redact_pii(text: str) -> str:
    return re.sub(r"(?<!\d)(?:\d[\- ]*){8,}\d(?!\d)", "[PII]", text)


# --- Structured action the model emitted (runtime decides execution) ---
class RouteKind(Enum):
    FINISH = "finish"
    HANDOFF = "handoff"
    SPAWN = "spawn"
    VOTE = "vote"
    ESCALATE_HUMAN = "escalate_human"


@dataclass(frozen=True)
class RouteAction:
    kind: RouteKind
    target: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    votes: tuple[str, ...] = ()
    spawn_targets: tuple[str, ...] = ()


# --- Typed Handoff record (reason, priority, artifact ids, downscoped jti) ---
@dataclass(frozen=True)
class Handoff:
    from_agent: str
    to_agent: str
    reason: str
    priority: str
    order_id: str | None
    artifact_ids: tuple[str, ...]
    dropped_hash: str      # hash of omitted history for IR reconstruction
    token_jti: str          # downscoped token ID
    tools_enabled: tuple[str, ...]


# --- HITL queue row (durable wait, timeout-deny) ---
@dataclass
class HitlRow:
    interrupt_id: str
    thread_id: str
    tool: str
    canonical_args: dict[str, Any]
    queued_at: float
    sla_s: float
    decision: str | None = None


# --- Thread state (persisted across crashes) ---
@dataclass
class ThreadState:
    thread_id: str
    tenant: str
    active_agent: str = "supervisor"
    hops: int = 0
    spent_usd: float = 0.0
    remaining_usd: float = USD_CAP
    last_from: str | None = None
    ping_pong: int = 0
    worm: list[dict[str, Any]] = field(default_factory=list)


# --- Access token with per-agent scoping ---
@dataclass(frozen=True)
class AccessToken:
    raw: str
    aud: tuple[str, ...]
    sub: str
    agent: str
    tools: frozenset[str]
    jti: str


def mint_downscoped(inbound: AccessToken, to_agent: str) -> AccessToken:
    """MUST NOT passthrough inbound.raw. Worker tools <= AGENT_TOOLS[to_agent]."""
    if not inbound.raw:
        raise PermanentError("missing_inbound")
    tools = AGENT_TOOLS[to_agent]
    material = f"obo:{inbound.sub}:{to_agent}:{GATEWAY_AUD}:{inbound.raw}"
    outbound_raw = hashlib.sha256(material.encode()).hexdigest()
    if outbound_raw == inbound.raw:
        raise PermanentError("token_passthrough")
    jti = hashlib.sha256(f"{outbound_raw}:{to_agent}".encode()).hexdigest()[:16]
    return AccessToken(outbound_raw, (GATEWAY_AUD, f"mcp://{to_agent}"),
                       inbound.sub, to_agent, tools, jti)


# --- Vote join: closed-set majority, never average below tau_low ---
def join_votes(answers: tuple[str, ...], *, tau_high=TAU_HIGH, tau_low=TAU_LOW):
    if not answers:
        raise PermanentError("empty_votes")
    counts: dict[str, int] = {}
    for a in answers:
        key = a.strip().lower()
        if not key:
            raise PermanentError("unparsed_vote")
        counts[key] = counts.get(key, 0) + 1
    winner, n = max(counts.items(), key=lambda kv: kv[1])
    frac = n / len(answers)
    if frac >= tau_high:
        return {"status": "accept", "answer": winner, "frac": frac, "vector": counts}
    if frac < tau_low:
        return {"status": "escalate", "answer": None, "frac": frac, "vector": counts}
    return {"status": "judge", "answer": winner, "frac": frac, "vector": counts}


# --- HITL queue: timeout-deny (AISVS 9.6.2), never timeout-allow ---
class HitlQueue:
    def __init__(self):
        self._rows: dict[str, HitlRow] = {}

    def enqueue(self, row: HitlRow) -> HitlRow:
        # Only write tools can park. Read tools auto-proceed.
        if row.tool not in {"issue_refund", "send_email", "account_mutate"}:
            raise PermanentError("hitl_not_write_tool")
        self._rows[row.interrupt_id] = row
        return row

    def resolve(self, interrupt_id: str, now: float, decision: str | None = None) -> str:
        row = self._rows[interrupt_id]
        if row.decision:
            return row.decision
        if decision in {"approve", "deny"}:
            row.decision = decision
            return decision
        # SLA miss -> timeout-DENY (never timeout-allow)
        if now - row.queued_at >= row.sla_s:
            row.decision = "timeout_deny"
            return "timeout_deny"
        return "pending"


# --- Orchestrator: runtime that executes RouteAction ---
class Orchestrator:
    """The model never calls this implicitly. It emits JSON; runtime decides."""

    def __init__(self, state: ThreadState, inbound: AccessToken, log: CorrelationAdapter):
        self.state, self.inbound, self.log = state, inbound, log
        self.hitl = HitlQueue()
        self.tokens: dict[str, AccessToken] = {"supervisor": inbound}
        self.breakers = {n: BreakerStateMachine(n, failure_threshold=1) for n in AGENT_TOOLS}

    def _charge(self, calls=1):
        self.state.spent_usd += CALL_USD * calls
        self.state.remaining_usd = USD_CAP - self.state.spent_usd
        if self.state.remaining_usd < 0:
            raise PermanentError("usd_cap")

    def _audit(self, **row):
        rec = {"ts": time.time(), "trace_id": self.log.extra.get("correlation_id"),
               "from_agent": self.state.active_agent, "hops": self.state.hops,
               "remaining_usd": round(self.state.remaining_usd, 6), **row}
        self.state.worm.append(rec)

    def _assert_transition(self, to_agent: str):
        allowed = TRANSITIONS.get(self.state.active_agent, frozenset())
        if to_agent not in allowed:
            raise PermanentError(f"transition_denied:{self.state.active_agent}->{to_agent}")

    def _ping_pong(self, to_agent: str):
        """Detect reciprocal handoffs and enforce hop cap."""
        if self.state.last_from == to_agent and self.state.active_agent != to_agent:
            self.state.ping_pong += 1
        self.state.last_from = self.state.active_agent
        if self.state.ping_pong >= 1 or self.state.hops >= HOP_CAP:
            raise PermanentError("hop_cap_escalate_human")

    def handoff(self, action: RouteAction, omitted: str) -> Handoff:
        if action.target is None:
            raise PermanentError("handoff_missing_target")
        if action.target == "refund" and not action.metadata.get("order_id"):
            raise PermanentError("refund_requires_order_id")
        self._assert_transition(action.target)
        self._ping_pong(action.target)
        self._charge(1)
        self.state.hops += 1
        src = self.state.active_agent
        # Downscope token: billing gets NO issue_refund, etc.
        token = mint_downscoped(self.inbound, action.target)
        if "issue_refund" in token.tools and action.target != "refund":
            raise PermanentError("rbac_union_leak")
        self.tokens[action.target] = token
        dropped_hash = hashlib.sha256(omitted.encode()).hexdigest()[:16]
        rec = Handoff(src, action.target, action.reason,
                      str(action.metadata.get("priority", "p3")),
                      action.metadata.get("order_id"),
                      tuple(action.metadata.get("artifact_ids") or ()),
                      dropped_hash, token.jti, tuple(sorted(token.tools)))
        self.state.active_agent = action.target
        self._audit(mechanism="handoff", to_agent=rec.to_agent,
                    token_jti=rec.token_jti, tools_enabled=rec.tools_enabled,
                    dropped_hash=rec.dropped_hash)
        return rec

    def spawn(self, action: RouteAction) -> dict[str, Any]:
        targets = action.spawn_targets
        if not targets:
            raise PermanentError("empty_spawn")
        if len(targets) > 4:
            raise PermanentError("fanout_cap")  # prevents Loop D
        if self.state.spent_usd >= USD_CAP * USD_FREEZE_FRAC:
            raise PermanentError("usd_freeze_no_spawn")  # 80% $ freeze
        self._charge(1 + len(targets))
        self.state.hops += 1
        self._audit(mechanism="Send", to_agent=",".join(targets))
        return {"status": "spawned", "targets": targets}

    def vote(self, action: RouteAction) -> dict[str, Any]:
        joined = join_votes(action.votes)
        self._charge(len(action.votes))
        self._audit(mechanism="vote", vote_vector=joined["vector"])
        if joined["status"] == "escalate":
            return self.escalate("tau_low")
        return joined

    def escalate(self, reason: str, *, tool=None, args=None, now=None):
        interrupt_id = str(uuid.uuid4())
        if tool:
            row = HitlRow(interrupt_id, self.state.thread_id, tool,
                          args or {}, now or time.time(), HITL_SLA_S)
            self.hitl.enqueue(row)
        self.state.active_agent = "human"
        self._audit(mechanism="escalate_human", to_agent="human",
                    human_gate=reason, interrupt_id=interrupt_id)
        return {"status": "escalated", "reason": reason, "interrupt_id": interrupt_id}

    async def call_tool(self, agent: str, name: str,
                        fn: Callable[[], Awaitable[dict]]) -> dict:
        token = self.tokens.get(agent)
        if token is None or name not in token.tools:
            raise PermanentError("rbac_deny")
        breaker = self.breakers[agent]

        async def _once():
            await breaker.allow()
            try:
                out = await fn()
            except TransientError:
                await breaker.record_failure()
                raise
            await breaker.record_success()
            return out

        try:
            return await retry_with_jitter(_once, log=self.log)
        except (CircuitOpenError, TransientError):
            self.log.warning("degraded agent=%s tool=%s", agent, name)
            return {"status": "degraded", "agent": agent, "tool": name}


# --- Offline self-test (validates all invariants without network) ---
async def _offline():
    cid, tenant, user = str(uuid.uuid4()), "acme", "usr_1"
    log = build_logger(cid, tenant, plane="control")
    inbound = AccessToken("sup_raw_token", (GATEWAY_AUD,), user,
                          "supervisor", AGENT_TOOLS["supervisor"], "jti-sup")
    st = ThreadState(thread_id="thr-1", tenant=tenant)
    orch = Orchestrator(st, inbound, log)

    # Handoff: billing gets no issue_refund
    rec = orch.handoff(
        RouteAction(RouteKind.HANDOFF, "billing", "invoice lookup",
                    {"priority": "p2", "order_id": "ord_1"}),
        omitted="prior tool payloads")
    assert rec.to_agent == "billing" and "issue_refund" not in rec.tools_enabled

    # Refund requires order_id
    try:
        orch.handoff(RouteAction(RouteKind.HANDOFF, "refund", "no order"), "x")
        raise AssertionError("order_id")
    except PermanentError as exc:
        assert "refund_requires_order_id" in str(exc)

    # PII redaction
    assert redact_pii("card 4111111111111111") == "card [PII]"

    # Vote join: unanimous -> accept; 1/3 -> escalate
    assert join_votes(("ok", "ok", "ok"))["status"] == "accept"
    low = Orchestrator(ThreadState("thr-v", tenant), inbound, log).vote(
        RouteAction(RouteKind.VOTE, votes=("x", "y", "z")))
    assert low["status"] == "escalated" and low["reason"] == "tau_low"

    # HITL: timeout-deny, not timeout-allow
    parked = orch.escalate("security_write", tool="issue_refund",
                           args={"amount": 40.0}, now=0.0)
    assert orch.hitl.resolve(parked["interrupt_id"], now=HITL_SLA_S + 1) == "timeout_deny"

    # Read tools cannot park on HITL queue
    try:
        orch.hitl.enqueue(HitlRow("i3", "thr-1", "kb_search", {}, 0, 1))
        raise AssertionError("read-hitl")
    except PermanentError as exc:
        assert "hitl_not_write_tool" in str(exc)

    # Circuit breaker: billing 529 -> degraded; FAQ still serves
    async def boom():
        raise TransientError("529", status=529)
    async def ok():
        return {"ok": True}

    degraded = await orch.call_tool("billing", "lookup_invoice", boom)
    assert degraded["status"] == "degraded"
    assert orch.breakers["billing"].state is BreakerState.OPEN

    # Fan-out cap: >4 targets rejected
    try:
        Orchestrator(ThreadState("thr-w", tenant), inbound, log).spawn(
            RouteAction(RouteKind.SPAWN, spawn_targets=tuple(f"s{i}" for i in range(50))))
        raise AssertionError("fanout")
    except PermanentError as exc:
        assert "fanout_cap" in str(exc)

    print(json.dumps({"ok": True, "cid": cid, "worm_rows": len(orch.state.worm)}, indent=2))


if __name__ == "__main__":
    asyncio.run(_offline())
```

**Behavior encoded:** Full-jitter HTTP retries (Retry-After honored iff 0 < t <= 60); PermanentError/CircuitOpenError never retried; supervisor router executes RouteAction (model payload never mutates `active_agent`); typed Handoff carries reason/priority/order_id/artifact_ids/dropped_hash/downscoped jti; hop cap 3 and ping-pong -> `hop_cap_escalate_human`; $ freeze at 80% forbids new Send; fan-out >4 (50-sub Loop D) is fanout_cap; vote join parses a closed set (unanimous -> accept, 1/3 -> escalate, 2/3 -> judge); HITL enqueues write tools only, SLA miss -> timeout_deny; mint_downscoped ensures billing has no issue_refund; per-specialist breaker; JSON logs carry correlation_id + from_agent/to_agent; WORM rows append on every mechanism.

### 6.2 HITL Approval Gate with Risk Tiers

```python
"""Risk-tiered action classification for HITL gates.
Integrates with LangGraph interrupt() or Temporal wait_condition.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class RiskTier(IntEnum):
    READ_ONLY = 1        # autonomous
    REVERSIBLE = 2       # notify
    EXTERNAL = 3         # pre-approve
    IRREVERSIBLE = 4     # mandatory human approval


ESCALATION_SLA = {
    RiskTier.READ_ONLY: float("inf"),
    RiskTier.REVERSIBLE: 14400.0,        # 4 hours
    RiskTier.EXTERNAL: 3600.0,           # 1 hour
    RiskTier.IRREVERSIBLE: 900.0,        # 15 minutes
}


@dataclass
class ActionRequest:
    action_type: str
    parameters: dict[str, Any]
    agent_id: str
    confidence: float
    risk_tier: RiskTier
    blast_radius: str     # "single_user" | "team" | "org" | "public"
    reversible: bool


@dataclass
class ApprovalDecision:
    approved: bool
    approver: str         # "auto" | human identifier
    reason: str
    timestamp: float
    sla_met: bool


def classify_risk(action: ActionRequest) -> RiskTier:
    """If any two of (not reversible, high blast radius, low confidence)
    are true, escalate by one tier."""
    risk_signals = sum([
        not action.reversible,
        action.blast_radius in ("org", "public"),
        action.confidence < 0.7,
    ])
    base = action.risk_tier
    if risk_signals >= 2 and base < RiskTier.IRREVERSIBLE:
        return RiskTier(base + 1)
    return base


def evaluate_approval(action: ActionRequest) -> ApprovalDecision:
    """Evaluate whether an action can proceed autonomously or needs human gate."""
    tier = classify_risk(action)
    now = time.time()

    if tier == RiskTier.READ_ONLY:
        return ApprovalDecision(True, "auto", "Read-only -- autonomous", now, True)

    if tier == RiskTier.REVERSIBLE:
        return ApprovalDecision(True, "auto_with_notify",
                                "Reversible -- approved with notification", now, True)

    # EXTERNAL and IRREVERSIBLE: park for human approval.
    # In production: interrupt() or Temporal wait_condition with timeout-DENY.
    return ApprovalDecision(
        False, "pending_human",
        f"Risk tier {tier.name} requires human approval", now, False)
```

### 6.3 Consensus Mechanism (Parallel Reviewers with Structural Decorrelation)

```python
"""Multi-agent consensus via majority voting with structural decorrelation.
Uses different models for each reviewer to decorrelate blind spots.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, Field


class ReviewVerdict(BaseModel):
    severity: Literal["critical", "major", "minor", "informational"]
    category: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_fix: str | None = None


class ReviewerVote(BaseModel):
    approve: bool
    findings: list[ReviewVerdict]
    reviewer_model: str
    overall_confidence: float


@dataclass
class ConsensusResult:
    approved: bool
    vote_count: dict[str, int]
    agreement_ratio: float
    merged_findings: list[ReviewVerdict]
    requires_human: bool
    human_reason: str | None = None


async def consensus_review(
    diff: str,
    reviewer_configs: list[dict[str, str]] | None = None,
    approval_threshold: float = 0.6,
    confidence_floor: float = 0.5,
) -> ConsensusResult:
    """Run parallel reviewers, aggregate via weighted majority vote.
    Low agreement -> escalate to human (never silently average)."""
    if reviewer_configs is None:
        # Structural decorrelation: different models for different blind spots
        reviewer_configs = [
            {"model": "claude-sonnet-4-20250514", "focus": "security and correctness"},
            {"model": "gpt-4o", "focus": "architecture and performance"},
            {"model": "claude-opus-4-20250514", "focus": "edge cases and error handling"},
        ]

    # Fan-out: run all reviewers concurrently
    # In production: replace run_reviewer with actual LLM calls
    votes: list[ReviewerVote] = await asyncio.gather(
        *[_run_reviewer(cfg, diff) for cfg in reviewer_configs])

    approve_count = sum(1 for v in votes if v.approve)
    agreement_ratio = max(approve_count, len(votes) - approve_count) / len(votes)

    # Merge: deduplicate by category, keep highest confidence
    all_findings = [f for v in votes for f in v.findings if f.confidence >= confidence_floor]
    merged = _deduplicate_findings(all_findings)

    # Any critical finding blocks regardless of vote
    has_critical = any(f.severity == "critical" for f in merged)
    approved = (approve_count / len(votes)) >= approval_threshold and not has_critical

    # Low agreement -> human review (never silently average)
    requires_human = agreement_ratio < 0.6 or has_critical
    human_reason = None
    if has_critical:
        human_reason = "Critical finding detected -- human review required"
    elif agreement_ratio < 0.6:
        human_reason = f"Low agreement ({agreement_ratio:.0%}) -- escalate"

    return ConsensusResult(approved, {"approve": approve_count,
        "reject": len(votes) - approve_count}, agreement_ratio,
        merged, requires_human, human_reason)


async def _run_reviewer(cfg: dict, diff: str) -> ReviewerVote:
    """Stub -- wire to actual LLM in production."""
    raise NotImplementedError("Wire to actual LLM")


def _deduplicate_findings(findings: list[ReviewVerdict]) -> list[ReviewVerdict]:
    best: dict[str, ReviewVerdict] = {}
    for f in findings:
        key = f"{f.category}:{f.severity}"
        if key not in best or f.confidence > best[key].confidence:
            best[key] = f
    order = {"critical": 0, "major": 1, "minor": 2, "informational": 3}
    return sorted(best.values(), key=lambda f: order.get(f.severity, 99))
```

### 6.4 Circuit Breaker and Graceful Degradation

```python
"""
Circuit breaker for LLM API calls with five-level graceful degradation.
Production-grade: thread-safe, configurable thresholds, structured logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DegradationLevel(Enum):
    FULL = 1
    REDUCED_MODEL = 2
    CACHED = 3
    STATIC_FALLBACK = 4
    QUEUED = 5


@dataclass
class CircuitBreaker:
    """Three-state circuit breaker for LLM API calls.

    Closed  -> Open:      failure_count >= failure_threshold
    Open    -> Half-Open:  timeout_seconds elapsed
    Half-Open -> Closed:   success_count >= success_threshold
    Half-Open -> Open:     any failure
    """
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout_seconds: float = 60.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(
        self,
        primary_fn: Callable[..., Awaitable[Any]],
        fallback_fn: Callable[..., Awaitable[Any]] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, DegradationLevel]:
        """Execute primary_fn through the circuit breaker.

        Returns (result, degradation_level) so callers can tag responses.
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.timeout_seconds:
                    logger.info("Circuit transitioning OPEN -> HALF_OPEN")
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                else:
                    # Still open -- use fallback
                    if fallback_fn is not None:
                        result = await fallback_fn(*args, **kwargs)
                        return result, DegradationLevel.REDUCED_MODEL
                    raise CircuitOpenError(
                        f"Circuit open, no fallback. Retry in "
                        f"{self.timeout_seconds - (time.time() - self._last_failure_time):.0f}s"
                    )

        # CLOSED or HALF_OPEN: attempt the primary call
        try:
            result = await primary_fn(*args, **kwargs)
            await self._record_success()
            return result, DegradationLevel.FULL
        except Exception as exc:
            await self._record_failure()
            if fallback_fn is not None:
                logger.warning(
                    "Primary failed (%s), using fallback. State: %s",
                    exc, self._state.value,
                )
                result = await fallback_fn(*args, **kwargs)
                return result, DegradationLevel.REDUCED_MODEL
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info("Circuit transitioning HALF_OPEN -> CLOSED")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            else:
                self._failure_count = 0

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                logger.warning("Circuit transitioning HALF_OPEN -> OPEN")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                logger.warning(
                    "Circuit transitioning CLOSED -> OPEN after %d failures",
                    self._failure_count,
                )
                self._state = CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when the circuit is open and no fallback is available."""


# --- Graceful Degradation Cascade ---

@dataclass
class DegradationCascade:
    """Five-level graceful degradation for multi-agent systems.

    Each level tries the next fallback strategy. Every response is
    tagged with its degradation level for observability.
    """
    primary_model: str = "claude-opus-4-20250514"
    fallback_model: str = "claude-haiku-4-20250514"
    cache_similarity_threshold: float = 0.92
    queue_timeout_seconds: float = 300.0

    async def execute(
        self,
        query: str,
        primary_fn: Callable[..., Awaitable[str]],
        fallback_fn: Callable[..., Awaitable[str]],
        cache_fn: Callable[[str, float], Awaitable[str | None]],
        static_response: str = "We're experiencing high demand. A team member will follow up shortly.",
    ) -> tuple[str, DegradationLevel]:
        """Try each degradation level in sequence until one succeeds."""

        # Level 1: Full capability
        try:
            result = await asyncio.wait_for(primary_fn(query), timeout=30.0)
            return result, DegradationLevel.FULL
        except Exception as exc:
            logger.warning("Level 1 (primary) failed: %s", exc)

        # Level 2: Reduced model
        try:
            result = await asyncio.wait_for(fallback_fn(query), timeout=15.0)
            return result, DegradationLevel.REDUCED_MODEL
        except Exception as exc:
            logger.warning("Level 2 (fallback model) failed: %s", exc)

        # Level 3: Cached response
        try:
            cached = await cache_fn(query, self.cache_similarity_threshold)
            if cached is not None:
                return cached, DegradationLevel.CACHED
            logger.info("Level 3: no cache hit above threshold %.2f",
                       self.cache_similarity_threshold)
        except Exception as exc:
            logger.warning("Level 3 (cache) failed: %s", exc)

        # Level 4: Static fallback
        logger.warning("Level 4: returning static fallback response")
        return static_response, DegradationLevel.STATIC_FALLBACK

        # Level 5 (queue for later) would be implemented as:
        # await queue_service.enqueue(query)
        # return ack_message, DegradationLevel.QUEUED
```

### 6.5 Distributed Tracing with Span Management

```python
"""
Distributed tracing across multi-agent systems.
Propagates trace_id and span_id through agent handoffs for
end-to-end observability. Compatible with OpenTelemetry export.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Generator


@dataclass
class Span:
    """A single span in a distributed trace."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    agent_id: str
    operation: str
    start_time: float
    end_time: float | None = None
    status: str = "in_progress"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000


class AgentTracer:
    """Distributed tracer for multi-agent orchestration.

    Maintains a trace context that propagates through agent handoffs.
    Each agent creates child spans under the shared trace_id.
    """

    def __init__(self, service_name: str = "multi-agent-system"):
        self.service_name = service_name
        self._spans: list[Span] = []
        self._logger = logging.getLogger(f"tracer.{service_name}")

    def new_trace(self) -> str:
        """Start a new trace. Returns the trace_id."""
        return uuid.uuid4().hex[:16]

    @contextmanager
    def span(
        self,
        trace_id: str,
        agent_id: str,
        operation: str,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Context manager that creates, yields, and finalizes a span."""
        span = Span(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id,
            agent_id=agent_id,
            operation=operation,
            start_time=time.time(),
            attributes=attributes or {},
        )

        self._logger.info(
            json.dumps({
                "event": "span_start",
                "trace_id": trace_id,
                "span_id": span.span_id,
                "parent_span_id": parent_span_id,
                "agent_id": agent_id,
                "operation": operation,
                "service": self.service_name,
            })
        )

        try:
            yield span
            span.status = "ok"
        except Exception as exc:
            span.status = "error"
            span.events.append({
                "name": "exception",
                "timestamp": time.time(),
                "attributes": {
                    "exception.type": type(exc).__name__,
                    "exception.message": str(exc),
                },
            })
            raise
        finally:
            span.end_time = time.time()
            self._spans.append(span)

            self._logger.info(
                json.dumps({
                    "event": "span_end",
                    "trace_id": trace_id,
                    "span_id": span.span_id,
                    "agent_id": agent_id,
                    "operation": operation,
                    "status": span.status,
                    "duration_ms": span.duration_ms,
                    "service": self.service_name,
                    "attributes": span.attributes,
                })
            )

    def add_event(self, span: Span, name: str, attributes: dict | None = None) -> None:
        """Add a timestamped event to a span (e.g., tool call, handoff)."""
        span.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def get_trace(self, trace_id: str) -> list[dict]:
        """Export all spans for a trace in OpenTelemetry-compatible format."""
        return [asdict(s) for s in self._spans if s.trace_id == trace_id]


# --- Usage Example ---

def traced_supervisor_flow(user_query: str) -> dict:
    """Demonstrates trace propagation through a supervisor -> worker flow."""
    tracer = AgentTracer(service_name="customer-support")
    trace_id = tracer.new_trace()

    with tracer.span(trace_id, "supervisor", "classify_intent") as sup_span:
        sup_span.attributes["user_query_length"] = len(user_query)
        sup_span.attributes["routing_model"] = "gpt-4o"
        tracer.add_event(sup_span, "routing_decision", {"target": "billing"})

        # Worker span is a child of the supervisor span
        with tracer.span(
            trace_id, "billing_agent", "process_refund",
            parent_span_id=sup_span.span_id,
        ) as worker_span:
            worker_span.attributes["tools_used"] = ["lookup_invoice", "process_refund"]
            worker_span.attributes["tokens_consumed"] = 1200
            tracer.add_event(worker_span, "tool_call", {"tool": "lookup_invoice"})
            tracer.add_event(worker_span, "tool_call", {"tool": "process_refund"})

        # Back to supervisor for synthesis
        with tracer.span(
            trace_id, "supervisor", "synthesize_response",
            parent_span_id=sup_span.span_id,
        ) as synth_span:
            synth_span.attributes["tokens_consumed"] = 450

    # Export full trace
    trace = tracer.get_trace(trace_id)
    total_duration = sum(s.get("duration_ms", 0) or 0 for s in trace)
    total_tokens = sum(
        s.get("attributes", {}).get("tokens_consumed", 0) for s in trace
    )
    return {
        "trace_id": trace_id,
        "spans": len(trace),
        "total_duration_ms": total_duration,
        "total_tokens": total_tokens,
        "trace": trace,
    }
```

---

## 7. Failure Modes & Mitigations

### 7.1 Overall Failure Rates

- Multi-agent LLM systems fail **41-86%** of the time depending on task complexity
- Five agents at 95% individual accuracy yield only **~77%** end-to-end success
- **40%** of multi-agent pilots fail within 6 months
- Gartner predicts **>40%** of agentic AI projects cancelled by 2027

### 7.2 MAST Failure Taxonomy (NeurIPS 2025, 1,600+ traces, kappa = 0.88)

| Category | Frequency | Examples |
|----------|-----------|---------|
| **Specification Problems** | 41.77% | Role ambiguity, unclear tasks, missing constraints |
| **Coordination Failures** | 36.94% | Communication breakdowns, state sync, conflicting objectives |
| **Verification Gaps** | 21.30% | Inadequate testing, missing validation |

### 7.3 Complete Failure Taxonomy

| Class | Examples | Handler |
|-------|---------|---------|
| **Transient** | 408/429/5xx/529, TLS reset, one specialist timeout | Full jitter; same idempotency key; do not retry RBAC deny |
| **Permanent** | 400 schema, 401/403, hop cap, $ cap, tau_low, RBAC deny, A2A `REJECTED` | Fail the hop or escalate. Do not failover a 400 to an unauthenticated specialist |
| **Ping-pong** | `transfer_to_sales` <-> `transfer_to_support`; CrewAI manager delegating to itself; CAMEL thank-you loop | Hop counter; after N transfers force human; allowed-transition graph |
| **Fan-out catastrophe** | 50 subs on trivia; vague briefs -> duplicate searches | Effort rules + hard W cap; brief template: objective, sources, out of scope |
| **Vote poison** | Three agents agree on shared hallucination; same-family judge | Diversity pruning; cross-family proposers; position-swap; escalate on gray |
| **HITL poison** | Every tool `interrupt_on`; operators rubber-stamp (ASI09); `MaxTurnsExceeded` while waiting | Interrupt write tools only; `when` on amount/ACL; approval budget; timeout-deny |
| **Confused deputy** | Supervisor has GitHub admin; worker executes with supervisor creds (CSA 2026-03) | Downscope at handoff; never "lead calls all tools on behalf of workers" |
| **Context loss** | Aggressive `input_filter`; missing AIMessage+ToolMessage pair | Typed metadata + brief; log dropped hashes; filesystem refs |
| **Rainbow-unsafe deploy** | In-flight graph schema mismatch after cutover | Dual-run old/new; pin prompt version on the thread |
| **Cascading errors** | Agent treats flawed upstream output as ground truth | Schema validation at every agent boundary |
| **Context drift** | Shared goal degrades through free-text handoffs | Immutable objective field in typed task object |
| **Silent partial failure** | Intermediate agent hits tool error, pipeline reports success | Output evaluation gates at every handoff |
| **Magentic password-reset spiral** | Login misconfig -> agents reset the account password after lockout | Circuit on auth tools; human on account mutation |
| **o1/policy refusals** | o1 refused 26% WebArena Gitlab, 12% Shopping Admin | Don't put the policy-heavy model on write tools |

### 7.4 Seven Critical Failure Patterns (Quick Reference)

| Pattern | Root Cause | Detection Signal | Mitigation |
|---------|-----------|-----------------|-----------|
| Cascading errors | Agent treats flawed upstream output as ground truth | Monotonic quality degradation | Schema validation at every agent boundary |
| Coordination deadlock | Circular dependencies (A waits B, B waits C, C waits A) | No explicit error, just timeout | Enforce explicit topology + hard timeouts |
| Context drift | Shared goal degrades through free-text handoffs | Goal divergence over turns | Immutable objective field in typed task object |
| Infinite loops | Unbounded refinement cycle between agents | Token/cost spike, no convergence | Step limits + token caps + diminishing-returns detection (<5% improvement x3 -> terminate) |
| Silent partial failure | Intermediate agent hits tool error, pipeline reports success | Missing data in final output | Output evaluation gates at every handoff |
| Inter-agent misalignment | Conflicting implicit role assumptions | Format mismatches downstream | Explicit output contracts + alignment tests before integration |
| Tool/data corruption | External tools return stale or poisoned data | Stale timestamps, data anomalies | MCP with strict validation, least-privilege scoping |

### 7.5 Deadlock Patterns Unique to Multi-Agent

1. **Reciprocal handoffs** with no hop cap
2. **GroupChat** `auto` + fully connected graph + `allow_repeat_speaker=True` -- no progress predicate
3. **Parallel `Send` workers** waiting on each other's unwritten keys (no reducer / cyclic dependence)
4. **A2A client blocking** on `AUTH_REQUIRED` while HITL queue is full -- overflow, not a graph cycle
5. **CAMEL thank-you loop** -- agents know they are looping and still fail to stop; fuse on repeat tokens

**Deadlock prevention:** Resource ordering (agents acquire shared resources in globally agreed order); mediator pattern (dedicated orchestrator brokers all resource requests with enforced timeouts, e.g. 30s); idempotency guards (verify same logical task is not already running before spawning subagent).

### 7.6 Notable Production Incidents

| Incident | Impact | Root Cause |
|----------|--------|-----------|
| Nov 2025: Two LangChain agents in infinite conversation cycle | **$47,000 bill** over 11 days | No loop detection, no cost cap |
| Dec 2025: Amazon Kiro agent with operator-level permissions | **13-hour outage** in mainland China | Deleted and rebuilt AWS Cost Explorer to "fix" a minor issue |
| Galileo simulation | **87% of downstream decisions** poisoned within 4 hours | Single compromised agent in multi-agent chain |
| IAL-Scan analysis: 6,549 LLM agent repos | **68 confirmed** infinite loop failures across 47 projects (91.9% precision) | No termination conditions |

### 7.7 Circuit Breaker (Per-Specialist)

```
        5xx/529/timeout rate >= threshold           probe success
  +--------+  ---------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                       | OPEN |
  +---+----+  429 with Retry-After = throttle      +--+---+
      |       (stay CLOSED; sleep)                    | timer (e.g. 30s)
      | success resets window                         v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | read     |
                                                 +----------+
```

**Key rules:** One breaker per specialist, plus one per provider, plus one per A2A callee. Do NOT open solely on 429-with-Retry-After. Do NOT open the billing breaker because web-search 429d. Half-open: probe with a cheap read (`kb_search` / `tasks/get`), not `issue_refund`.

### 7.8 Graceful Degradation (Five-Level Cascade)

| Level | Strategy | Description |
|-------|----------|-------------|
| 1 | Full capability | Primary model, all tools, real-time data |
| 2 | Reduced model | Fallback to smaller/cheaper model (e.g., Haiku instead of Opus) |
| 3 | Cached responses | Semantically similar cached results (threshold >= 0.92) |
| 4 | Static fallback | Pre-defined error response with actionable guidance |
| 5 | Queue for later | Accept and acknowledge, process when capacity restores |

**Back-pressure shed order:** Skip debate on L1 -> cap Send width -> forbid new handoffs at 80% $ -> HITL-shed low-priority reads -> deterministic degraded JSON. **Never** 50-retry a poison specialist into the transcript. **Never** auto-approve irreversible class to drain HITL.

### 7.9 Context Compaction

Without active compaction, agents lose coherent access to original task objectives by approximately the **60% context mark** (Factory AI research). Strategies: anchored iterative summarization, dropping old tool outputs, offloading intermediate findings to external storage. Trigger compaction at 75% of context limit.

---

## 8. Security & Governance

### 8.1 OWASP Top 10 for Agentic Applications (Dec 2025)

Key distinction from LLM Top 10: LLM Top 10 governs model-level risks (what a model says). Agentic Top 10 governs system-level risks (what an agent does). Prompt injection in a chatbot is a content problem; in an agent it becomes a **control problem**.

| ID | Vulnerability | Core Risk | Priority |
|----|--------------|-----------|----------|
| ASI01 | Agent Goal Hijack | Manipulating objectives via prompt injection | **P0** |
| ASI02 | Tool Misuse & Exploitation | Incorrect or malicious tool arguments | P1 |
| ASI03 | Agent Identity & Privilege Abuse | Missing per-agent credentials, shared sessions | **P0** |
| ASI04 | Agentic Supply Chain Compromise | Compromised third-party frameworks or MCP servers | P1 |
| ASI05 | Unexpected Code Execution | Agents generating and running unvalidated code | P1 |
| ASI06 | Memory & Context Poisoning | Hallucinated data stored in shared memory as fact | P2 |
| ASI07 | Insecure Inter-Agent Communication | Spoofed identities, replayed messages, forged consensus | P1 |
| ASI08 | Cascading Agent Failures | Error propagation across agent chains | P2 |
| ASI09 | Human-Agent Trust Exploitation | Social engineering through agent interfaces | P2 |
| ASI10 | Rogue Agents | Agents acting outside intended scope undetected | P2 |

**Priority sequence:** ASI01 + ASI03 first (load-bearing), then ASI04 + ASI07 as agent fleets grow. Four items concentrate in this module: ASI03 (delegation without downscope), ASI07 (A2A/MCP without mTLS/audience), ASI08 (fan-out, ping-pong, retry storms), ASI09 (HITL exploitation).

### 8.2 Zero-Trust Per-Agent Tools & RBAC Downscope

**Two confused-deputy layers:**
1. **OAuth proxy deputy** -- static IdP `client_id` + DCR + consent cookie (MCP layer)
2. **Agent deputy** -- supervisor has GitHub admin; user asks a worker to "update the README"; worker issues a tool call that the supervisor executes with supervisor credentials (CSA 2026-03)

**Fix:** Downscope at handoff. `on_handoff` mints a token whose audience is the worker's MCP servers and whose scopes match the brief. A2A `AUTH_REQUIRED` when the callee needs a user gesture. Never "the lead calls all tools on behalf of workers."

| Principal | May | Must Not |
|-----------|-----|----------|
| Router / lead | Spawn workers, read summaries, write plan Memory | Hold production write tools (Stripe, email send) |
| Domain specialist | Its tool allowlist | Other specialists' tools; raw user refresh tokens |
| Citation / judge | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail (ASI09) |
| A2A callee | Skills on its Agent Card | Your VPC except via published artifacts |

**RBAC tuple at handoff:** `(principal, tenant, from_agent, to_agent, tools_enabled, jti, policy_version)`. Resume / `Command(resume=...)` values must not concatenate into a new write without re-RBAC.

**Isolation ladder (cheapest to strongest):**
1. OAuth scope filter (cheapest; app-bug can omit)
2. Gateway `Mcp-Name` allowlist
3. Per-agent allowlist in the orchestrator (this module)
4. Separate MCP servers for payments vs. FAQ
5. Separate conversations for secrets-bearing specialists (prompt isolation)

### 8.3 Agent Identity & Trust Boundaries

Every agent must have a distinct, managed identity -- not inherited from a user session or shared across instances. Per ISACA (2025): every AI agent must be provisioned as a named service account; shared credentials are an audit finding. AISVS 9.4.1: cryptographic agent identity.

**GS Consulting trust boundary scoring (weighted 0-100):** identity ambiguity (20%), delegated authority (20%), action propagation (20%), data reach (15%), recovery coupling (15%), state persistence (10%).

**ASI07 mitigations:** Mutual TLS and signed payloads for all inter-agent communication; authenticate every message, not just initial handshake; message integrity verification and replay protection; zero-trust: verify every agent interaction before execution.

### 8.4 PII in Transcripts

Every extra hop is a **copy**. Subagents with isolated windows are better for PII minimization if the brief strips identifiers and the sub returns aggregates. OpenAI default handoff passes full history -- prior-turn PII lands in the refund agent.

**Information compartmentalization:** Single-agent guardrails (input/output filters, system prompt hardening) do not address propagation pathways in multi-agent architectures. A single compromised agent poisoned **87% of downstream decision-making** within 4 hours (Galileo, December 2025).

**PII pipeline:** detect -> redact -> audit at ingress **and** before each handoff splice **and** before trace export. Never log raw transcripts in shared SaaS traces. PCI: PAN never in `messages`. Judge/CitationAgent should see artifact ids + redacted claims, not the raw ticket dump.

### 8.5 Audit Trails (WORM)

Immutable append-only row per control-plane event:

`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send|vote), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate, artifact_ids, vote_vector, disagreement_tau, dropped_hash, remaining_usd, hops`

Supports investigation and live containment: find active descendants, revoke credentials, cancel callbacks, quarantine artifacts, block downstream action.

### 8.6 The Governance Gap

- **82%** of enterprises already have AI agents their security teams did not know existed
- Only **7.2%** of organizations have a named individual with formal accountability for agent behavior
- **88%** of organizations deploying agents reported at least one security incident in 2025
- Only **38%** monitor AI traffic end-to-end (prompts, tool calls, outputs)
- Only **17%** continuously monitor agent-to-agent interactions
- **48%** of all AI agents in production are running unsecured

### 8.7 Regulatory Compliance

| Regulation | Key Requirement | Timeline |
|-----------|----------------|----------|
| **EU AI Act** | Model cards, data lineage, continuous quality monitoring, demonstrable human oversight (Article 14) | Full enforcement Aug 2, 2026 |
| **California SB-833** | State-level AI agent requirements | July 1, 2026 |
| **ISO/IEC 42001** | AI Management System standard | Required by enterprise procurement 2026+ |
| **NIST AI Agent Standards** | Federal standards initiative | Feb 2026 |

Complete governance requires six control layers: identity/auth, least-privilege access, behavioral monitoring, human oversight checkpoints, audit logging, and supply chain security.

---

## 9. System Design Scenarios

### 9.1 Scenario: L1/L2 Support with HITL Escalation

**Problem.** B2B SaaS support: 1k-50k tickets/day, L1 FAQ + billing lookup, L2 refund writes, SOC2/EU AI Act compliance. User-facing owner must stay sticky across follow-ups. Irreversible `issue_refund` needs a human on a 15 min L1 / 4 h L2 SLA with timeout-deny.

**Budget:** Sonnet 5 Loop S handoff ~$24/1k vs. subagents ~$32/1k vs. GPT-5.4 subagents ~$44/1k. Hop cap 3 -> human.

**Eval success criteria:** Tenant B cannot issue tenant A's refund even when the model emits `transfer_to_refund`; a 16-minute silence does not pay out; a reciprocal FAQ<->billing loop dies at hop 3.

**Proposed architecture:**

```
                 +----------------------------------------------------------+
                 | EDGE  SSO, cid, tenant FROM TOKEN                        |
                 | MODEL emits transfer_to_* / needs_approval JSON          |
                 +-----------------------------+----------------------------+
                                               |
                 +-----------------------------v----------------------------+
                 | CONTROL  TRIAGE HANDOFF (sticky specialist)              |
                 |  handoffDescription one sentence; is_enabled hides       |
                 |    refund unless order_id in state                       |
                 |  input_filter=remove_all_tools; dropped_hash WORM        |
                 |  hop cap 3 -> human; ping-pong detector                  |
                 |  BREAKER faq != billing != refund; cheap Haiku triage    |
                 |  HITL: Temporal wait_condition 900s/4h -> timeout-deny   |
                 +------+----------------------------------+----------------+
                        |                                  |
                        v                                  v
                 +-----------------+            +---------------------+
                 | DATA Generation |            | TOOL PROXIES        |
                 | Sonnet 5 cached |            | faq: kb_search      |
                 | playbooks; L2   |            | billing: lookup_inv |
                 | refund as_tool  |            | refund: issue_refund|
                 | for policy agent|            |   + HITL; downscoped|
                 +--------+-------+            |   jti at on_handoff |
                          |                    +----------+-----------+
                 +--------v--------+           +----------v-----------+
                 | PERSIST thread  |           | TELEMETRY WORM       |
                 | + RunState +    |           | from/to, jti, hops,$ |
                 | HITL row + idem.|           | human_gate, drop hash|
                 +-----------------+           | Loop S $24/1k        |
                                               +----------------------+
```

**Trade-off matrix:**

| Dimension | A: GroupChat 8 personas + vote on refunds | **B: Recommended -- sticky triage handoff + HITL timeout-deny** | C: Supervisor-worker + lead holds Stripe |
|-----------|------------------------------------------|--------------------------------------------------------------|----------------------------------------|
| **Cost/1k** | GroupChat N^2 + debate +$56/1k | **$24/1k handoff; repeat $16 extra** | $32/1k (+$8 tax); lead write tools = fraud risk |
| **Latency** | Broadcast every utterance; vote on critical path | **p95 = specialist; sticky turn 2 ~3.5s** | Serial default ~9.0s; process-held HITL is p99 |
| **Security** | Union tools on manager; vote of shared hallucination | **Per-agent allowlist; refund hidden without order_id; timeout-deny; canonical amount card** | Agent confused deputy: lead executes with admin creds |
| **Scalability** | N personas x 1k tickets | **Hop cap + queue capacity; shed low-pri; Stripe idempotency** | Fan-out on support ticket is Loop D ($4,000/1k) |

**Decision:** B is the only design that treats L1/L2 as conversation ownership + a write gate, matching OpenAI's own split (handoff for sticky UX, `as_tool` for bounded policy). A fails isolation, cost, and refund correctness (do not majority-vote a payout). C pays the +$8/1k supervisor tax and puts Stripe on the lead -- the worst of both clocks.

**Supplementary: General Topology Comparison for Customer Service (B2C, 50K req/day)**

| Dimension | A: Supervisor (recommended) | B: Swarm | C: Single Agent + Tools |
|-----------|-----------------------------|----------|------------------------|
| **Routing accuracy** | 94% | 91% (-3%) | N/A (one agent) |
| **Latency (P95)** | ~9s (handoff case) | ~5.4s (faster) | ~3s (fastest) |
| **Token cost / request** | ~2,800 | ~1,900 (-32%) | ~900 (-68%) |
| **Daily cost at 50K req (Sonnet)** | ~$2,100 | ~$1,450 | ~$700 |
| **Context drift resilience** | High (typed state, reducers) | Medium (shared context) | Low (single long context) |
| **Failure degradation** | SPOF risk (mitigate w/ standby) | 31% on 1 agent failure | N/A |
| **Observability** | Excellent (centralized routing) | Poor (peer-to-peer) | Simple |
| **Ops complexity** | Medium | High (debug difficulty) | Low |
| **Scalability ceiling** | High (add teams via hierarchy) | Medium (O(n^2) handoff tools) | Low (context window bound) |
| **Compliance audit** | Strong (supervisor audit trail) | Weak (distributed decisions) | Medium |

Supervisor wins despite higher cost and latency because (1) 94% routing accuracy reduces misrouted tickets (each misroute costs ~$8 in human agent time), (2) centralized routing produces complete audit trails required for financial services compliance (EU AI Act Article 14), (3) typed state with reducers prevents the context drift that causes single-agent 40% escalation rates, and (4) the SPOF risk is mitigated by hot-standby supervisor and the hierarchical extension path for future scaling beyond 4 domains. The 32% token savings of swarm does not justify the 31% failure degradation and poor observability in a 50K req/day environment.

### 9.2 Scenario: Parallel Research Agents + Judge

**Problem.** Analyst desk: breadth-first research across disjoint sources (filings, news, internal KB), citations required, task value must beat Loop R. Opus/GPT-5.4 lead, Sonnet/Haiku subs, Memory plan, filesystem artifacts, hard subagent cap, runtime $ cap. Joining free-form summaries by majority is forbidden (shared hallucination / omitted disagreement).

**Budget:** Sonnet 15x ~$135/1k; 30/70 Opus+Sonnet ~$240/1k; web search 3x8 = $0.24/task on the $10/1K SKU. Optional Du 3x2 only on contested final claims (+$56/1k, ~12s).

**Proposed architecture:**

```
                 +----------------------------------------------------------+
                 | EDGE  analyst SSO, cid, complexity score                  |
                 | MODEL emits plan JSON + Spawn(n<=5) -- runtime caps W     |
                 +-----------------------------+----------------------------+
                                               |
                 +-----------------------------v----------------------------+
                 | CONTROL  ORCHESTRATOR-WORKER (Anthropic-shaped)          |
                 |  Opus/GPT-5.4 lead writes plan to Memory                 |
                 |  effort rules AND hard cap: 1 | 2-4 | <=5 disjoint      |
                 |  80% remaining_usd forbids new Send                      |
                 |  isolated windows; briefs = objective, sources, OOS      |
                 |  JOIN: CitationAgent / one judge 0-1 (not multi-judge)   |
                 |    score < tau or missing cites -> wave 2 OR human        |
                 |  optional 3x2 debate on contested claims only            |
                 |  BREAKER search != kb != judge; stall -> replan          |
                 |  rainbow: pin prompt ver on thread; dual-run old/new     |
                 +------+----------------------------------+----------------+
                        |                                  |
                        v                                  v
                 +-----------------+            +---------------------+
                 | DATA waves      |            | TOOL PROXIES        |
                 | 3-5 subs x 3+  |            | web_search (SKU cap)|
                 | tools parallel; |            | fs_write artifact   |
                 | lead CANNOT steer|           | judge: read_artifact|
                 | in-flight (sync)|            |   NEVER mutate      |
                 +--------+-------+            +----------+-----------+
                 +--------v--------+           +----------v-----------+
                 | PERSIST Memory  |           | TELEMETRY WORM       |
                 | plan + artifact |           | W, $, overlap metric |
                 | store (refs not |           | judge score, citation|
                 | blobs thru lead)|           | Loop R $135-240/1k   |
                 +-----------------+           +----------------------+
```

**Trade-off matrix:**

| Dimension | A: Handoff swarm / sequential | **B: Recommended -- orchestrator-worker + judge** | C: Unbounded 50 subs + majority vote |
|-----------|------------------------------|--------------------------------------------------|--------------------------------------|
| **Cost/1k** | Sequential 14K+ tok; Skills 15K in one window | **$135/1k Sonnet 15x; $240/1k 30% Opus; search $0.24/task** | Loop D $4,000/1k; k=40 SC +$273/1k |
| **Latency** | sum(domain p99); cannot parallelize | **Wave = max(sub) + join; <= 90% cut vs sequential** | Join waits on slowest of 50 |
| **Security** | Shared transcript copies PII every hop | **Isolated windows + refs; judge cannot mutate** | Majority of shared hallucination ships as fact |
| **Scalability** | History growth; Skills sludge hits window | **Admission-control W; size in-flight completions** | No p99; AISVS 9.1.2 violated |

**Decision:** B is the only design that treats research as bounded parallel retrieve + a verifier, matching Anthropic's published system (plan Memory, isolated subs, one judge, filesystem refs). A is the sticky-support clock applied to the wrong product. C is the early Anthropic failure mode (50 subs) plus tyranny of the majority.

### 9.3 Scenario: Multi-Agent Code Review Pipeline

**Problem.** Engineering org (200 developers, ~400 PRs/day). AI-generated code increased PR size by 154% and review time by 91%. Bug rate up 9%. Single-model review misses domain-specific issues. Target: catch 40%+ more bugs, reduce human review time by 50%, keep cost under $0.50/PR.

**Proposed architecture:**

```
  PR Webhook --> Version Pin (lock model + tool versions)
                     |
                +----v--------------------+
                | Diff Preprocessor       |  Split into reviewable chunks
                | (deterministic, no LLM) |  Extract: files, functions, coverage delta
                +----+--------------------+
                     |
      +--------------+---------------+------------------+
      v              v               v                  v
+-----------+  +-----------+  +-----------+  +-----------+
| Architect |  | Security  |  | QA Agent  |  | Perf      |
| (Sonnet 4)|  | (Opus 4)  |  | (Sonnet 4)|  | (GPT-4o)  |
| patterns, |  | OWASP,    |  | test gaps,|  | O(n) vs   |
| SOLID     |  | injection |  | edge cases|  | O(n^2)    |
+-----+-----+  +-----+-----+  +-----+-----+  +-----+-----+
      +---------------+---------------+-----------+
                      |
                +-----v-----------------+
                | Adversarial Critic    |  Different model from the
                | (Claude Opus 4)       |  reviewer -- decorrelation
                | Challenges findings,  |
                | discards < threshold  |
                +-----+-----------------+
                      |
                +-----v-----------------+
                | Consensus Aggregator  |  Deterministic logic
                | Deduplicate, resolve  |  Priority: security > correctness > perf
                | Apply org policy      |
                +-----+-----------------+
                      |
                +-----v-----------------+
                | Output Formatter      |  Inline PR comments,
                | (no LLM)             |  summary with scores, CI status
                +-----------------------+
```

**Cost breakdown per PR:**
```
4 parallel reviewers:
  Sonnet 4 x 2:  $0.018 x 2 = $0.036
  Opus 4 x 1:    $0.090
  GPT-4o x 1:    $0.013

Adversarial critic (Opus 4): $0.158
Subtotal: ~$0.30
With prompt caching (84% hit rate): ~$0.22
At 400 PRs/day: ~$88/day, ~$2,640/month

Savings vs. human review (200 devs x 30 min saved/day x $80/hr): ~$4,000/day, 66:1 ROI
```

**Trade-off matrix:**

| Dimension | **A: Recommended -- Parallel + Critic** | B: Single Premium Model | C: Sequential Pipeline |
|-----------|-----------------------------------------|------------------------|----------------------|
| **Bug detection** | **+40% vs. single model** | Baseline | +25% (no decorrelation) |
| **False positive rate** | **Low (critic filters)** | Medium | High (no critic) |
| **Latency** | **~15s (parallel + critic)** | ~8s | ~30s (sequential) |
| **Cost per PR** | **$0.22-0.45** | $0.15 | $0.40 |
| **Blind spots** | **High coverage (structural decorrelation)** | Low (single model bias) | Medium |
| **Ops complexity** | High (4 agents + critic + aggregator) | Low | Medium (4 sequential stages) |
| **Scalability** | High (add specialist agents) | Low (context window bound) | Medium (add stages) |
| **Feedback loop** | Rich (per-agent metrics) | Single signal | Per-stage metrics |

**Decision:** Parallel specialists with adversarial critic wins because structural decorrelation across different models catches blind spots any single model misses (ICLR 2025: mixed-model configurations yield highest accuracy), and the adversarial critic filters false positives before reaching developers (tools abandoned at >20% false positive rate).

---

## 10. Interview Quick Reference

### Key Numbers to Memorize

| Number | What |
|--------|------|
| **$24 / $32 / $40 / $44 /1k** | Loop S: handoff / subagents / router-parallel / GPT-5.4 subagents |
| **+$8/1k/turn** | Supervisor-join tax vs. sticky specialist |
| **$135 / $240 /1k** | Loop R: Sonnet 15x / 30% Opus mix |
| **$4,000/1k** | Loop D: 50 subs x 10 calls (fan-out catastrophe) |
| **+$56/1k** | Du 3x2 debate extra cost |
| **$0.24/task** | Web search: 3 subs x 8 searches x $10/1K SKU |
| **~4x / ~15x** | Anthropic chat->agent / chat->multi-agent token multipliers |
| **62%** | Re-sent context share of total agent inference bills (Stanford) |
| **94% / 91%** | Supervisor / swarm routing accuracy (Focused.io) |
| **~9.0s / ~5.5s / ~3.5s** | Loop S serial / parallel Send / swarm turn 2 latency |
| **5.5% / 23% / 31%** | Hierarchical / pipeline / swarm degradation on 1 failure |
| **41-86%** | Multi-agent failure rate range (complex tasks) |
| **77%** | End-to-end success with 5 agents at 95% each |
| **87%** | Downstream decisions poisoned by single compromised agent (4 hrs) |
| **81.8% vs 69.0%** | Du 3x2 debate vs. majority (arithmetic) |
| **$47,000** | LangChain infinite loop incident (11 days) |

### Topology Decision Matrix

| Need | Choose | Why |
|------|--------|-----|
| Audit trail + dynamic routing | **Supervisor** | 94% routing accuracy, centralized policy |
| Sticky UX + repeat requests | **Swarm/Handoff** | No router overhead on turn 2 (~3.5s) |
| 6+ workers + resilience | **Hierarchical** | 5.5% degradation vs. 31% flat swarm |
| Fixed-order workflow | **Pipeline** | Deterministic, no routing LLM call |
| Research across disjoint sources | **Orchestrator-Worker** | Isolated windows, parallel waves, filesystem refs |
| Cross-org opaque agent | **A2A** | Agent bus, not tool bus |
| <3 distinct domains | **Skip multi-agent** | Single agent + tools is cheaper and simpler |

### Interview Talking Points

**Opener:** "The model never routes. I enforce hop and dollar caps in the runtime, I mint a downscoped token in `on_handoff`, I join votes at tau or escalate, and HITL is a Temporal park with timeout-deny -- not a prompt that says 'be careful.'"

**Cost framing:** "I pick the clock: sticky handoff at $24/1k vs. supervisor join at $32/1k vs. research 15x at $135-240/1k. Fan-out and $ live in the runtime, or you ship Loop D at $4,000/1k."

**Security framing:** "Two confused-deputy layers. The OAuth proxy (MCP layer) and the agent deputy (supervisor credentials propagated to workers). Fix: downscope at handoff, per-agent tool policy, worst reversibility class in the chain wins."

**Consensus framing:** "I never average below tau_low. Same-family proposer+judge without position-swap is Zheng bias. Debate is a verifier on high-value answers, not the default topology. Mixed-model configurations decorrelate blind spots."

### Interview Traps (Fail These, Fail the Round)

1. "The model routes / hands off / counts the vote / times out HITL." -- It emits structured JSON. The **runtime** mutates state.
2. Router, supervisor, and orchestrator used as synonyms. -- Three different clocks.
3. Shipping `langgraph-supervisor` for new work. -- Compatibility-only in LangChain 1.x.
4. `input_type` / `EscalationData` treated as authorization. -- It is metadata. Check in `on_handoff` and raise.
5. OpenAI input/output guardrails wrapping handoffs. -- Input = first agent only; output = last only; tool-input guardrails do not wrap handoffs. Policy at the worker.
6. HITL timeout-allow ("if nobody answers, issue the refund"). -- AISVS 9.6.2 = **block**.
7. Majority vote of a shared hallucination. -- Agreement < tau_low -> do not average; escalate.
8. Supervisor holds the union of worker tools "so it can help." -- Destroys isolation. Hierarchical IAM = delegation rights, not Stripe+email on the lead.
9. GroupChat of 8 personas for L1 support. -- Tokens proportional to N^2; use supervisor or swarm.
10. Flattening A2A into MCP `tools/call`. -- MCP = tool bus; A2A = agent bus. Terminal tasks immutable.
11. `HumanInTheLoopMiddleware` with tools absent from `interrupt_on`. -- Auto-approve. A new write tool you forgot to list is a privilege bug.
12. OpenAI `_max_turns=10` burning while a human is at lunch. -- Raise max_turns / None for approval-gated runs; put the SLA on the queue.

### AISVS Quick Reference

| Code | Rule |
|------|------|
| **9.1.2** | Budgets (hop, $, fan-out) |
| **9.1.3 / 9.6.3** | Kill-switch (out-of-band) |
| **9.2.2** | Canonicalized parameters shown to human |
| **9.2.10** | Worst reversibility class in the chain |
| **9.4.1** | Cryptographic agent identity |
| **9.5.1** | Per-agent tool+parameter policy |
| **9.5.2** | Downscoped user token at every hop |
| **9.5.3** | Policy engine, not the model |
| **9.5.4** | Secrets never in model-observable state |
| **9.5.5** | Explicit inter-agent delegation policy |
| **9.6.2** | Timeout-deny (HITL) |
