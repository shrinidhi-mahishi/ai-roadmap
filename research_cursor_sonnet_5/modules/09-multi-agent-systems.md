# 09. Multi-Agent Systems

**Sub-areas covered**: canonical topologies (hub-and-spoke/orchestrator-worker, hierarchical, mesh, flat/swarm) with communication-complexity classes · Anthropic's production orchestrator-worker Research system (LeadResearcher/Subagents/CitationAgent, effort-scaling, the stated synchronous-execution bottleneck) · message-passing protocols (synchronous handoff, agents-as-tools, async actor-model, A2A SSE streaming) · delegation/handoff mechanics (OpenAI Agents SDK, LangGraph `Command`-based handoff, Google A2A Agent Card/Task lifecycle) · capability-based routing · collaboration protocols (group chat, blackboard, shared-context, actor model) · token economics (Anthropic's 4×/15× multiplier, BrowseComp variance decomposition, the routing-tax critique) · a full P50/P95/P99 latency table for parallel vs. sequential multi-agent workflows · concurrent worker-fleet capacity planning and back-pressure · explicit availability/RPO/RTO targets tied to per-agent Child-Workflow checkpoint granularity, with parallelism-vs-coordination and specialization-vs-flexibility trade-offs · durable execution across agent boundaries (Temporal Child Workflows), shared-state consistency (OCC, agentic mutex, fencing tokens, vector clocks), circuit breakers per worker agent, dead-letter/idempotent-consumer handling for failed workers, and a transient/permanent/poison-pill failure taxonomy · Zero-Trust agent-to-agent auth (SPIFFE/SPIRE/mTLS/OAuth Token Exchange), tool-level RBAC per agent role, PII detect→redact→audit across agent boundaries, microVM sandbox isolation, and immutable delegation-chain audit logs · the MAST 14-failure-mode taxonomy and the Replit database-deletion incident · a hardened Python supervisor dispatching to multiple worker agents with retries, per-worker circuit breakers, fallback chains, correlation-ID logging, and graceful degradation · two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production supervisor-worker multi-agent system (MAS) separates five cooperating planes: a **control plane** that decomposes work and decides *who* handles *what*, a **data plane** of isolated worker agents that actually execute subtasks, a **tool proxy layer** that mediates every side effect leaving a worker's sandbox, a **persistence layer** that survives crashes and context-window truncation independently per agent, and a **telemetry layer** that makes every delegation hop auditable after the fact. The diagram below places Anthropic's LeadResearcher/Subagent/CitationAgent roles, capability-based routing, per-worker circuit breakers, and delegation-chain audit logging into the generic planes they occupy.

```
                    ┌──────────────────────────────────────────────────────────────────────────────────────┐
                    │                                    CONTROL PLANE                                        │
                    │                                                                                          │
                    │  ┌─────────────────────┐   ┌──────────────────────┐   ┌───────────────────────────────┐│
                    │  │ Supervisor / Lead     │──▶│ Capability-Based       │──▶│ Delegation-Chain Tracker        ││
                    │  │ Agent (decomposes     │   │ Router (weighted:      │   │ (originSub root-human identity, ││
                    │  │ query, persists plan  │   │ maxConcurrency,        │   │ agentProfileId + agentRunId,    ││
                    │  │ to external memory    │   │ costPerTask,           │   │ scope = intersect(parent,       ││
                    │  │ before 200K-ctx        │   │ avgLatencyMs,          │   │ child) at every hop, §2.5/4.9)  ││
                    │  │ truncation, §2.2)      │   │ success-rate, §2.5)   │   └────────────────┬────────────────┘│
                    │  └──────────┬────────────┘   └───────────┬───────────┘                    │ per-hop record  │
                    │             │ subtask spec                │ selected worker(s)              │                 │
                    │             ▼                              ▼                                 │                 │
                    │  ┌─────────────────────┐   ┌──────────────────────┐                         │                 │
                    │  │ Effort/Budget         │   │ Handoff / Agents-as-   │◀────────────────────────┘                 │
                    │  │ Governor (1 agent/    │   │ Tool Dispatcher (sync  │                                           │
                    │  │ 3-10 calls → 10+      │   │ ownership-transfer vs.│                                           │
                    │  │ agents/complex, §2.2;  │   │ bounded tool-call,    │                                           │
                    │  │ cost-velocity breaker, │   │ §2.3-2.4)              │                                           │
                    │  │ §3.4)                  │   └───────────┬───────────┘                                          │
                    │  └─────────────────────┘                │ parallel fan-out dispatch                             │
                    └──────────────────────────────────────────┼────────────────────────────────────────────────────────┘
                                                                    │
                    ┌──────────────────────────────────────────▼────────────────────────────────────────────────────────┐
                    │                                       DATA PLANE                                                     │
                    │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐         ┌───────────────────────────┐│
                    │  │ Worker #1  │  │ Worker #2  │  │ Worker #3  │  │ Worker #N  │  ...    │ CitationAgent /             ││
                    │  │ (isolated  │  │ (isolated  │  │ (isolated  │  │ (isolated  │         │ Synthesis Agent (final-pass││
                    │  │ context    │  │ context    │  │ context    │  │ context    │         │ trajectory-aware verify,   ││
                    │  │ window,    │  │ window,    │  │ window,    │  │ window,    │         │ decoupled from research    ││
                    │  │ 3+ tools   │  │ 3+ tools   │  │ 3+ tools   │  │ 3+ tools   │         │ loop, §2.2)                ││
                    │  │ parallel)  │  │ parallel)  │  │ parallel)  │  │ parallel)  │         └──────────────┬─────────────┘│
                    │  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘                          │ synthesized  │
                    └────────┼──────────────┼──────────────┼──────────────┼─────────────────────────────────┼──────────────┘
                               │ tool calls    │              │              │                                  │ answer
                    ┌───────────▼──────────────▼──────────────▼──────────────▼──────────────────────────────────▼──────────────┐
                    │                                    TOOL PROXY LAYER                                                          │
                    │  ┌──────────────────────────┐  ┌────────────────────────────┐  ┌────────────────────────────────────┐   │
                    │  │ Zero-Trust MCP Gateway      │  │ Per-Worker Circuit Breaker   │  │ Fallback Chain Dispatcher              │   │
                    │  │ (SPIFFE SVID / mTLS PEP;    │  │ (CLOSED→OPEN→HALF_OPEN,      │  │ same/cheaper model → cached answer →   │   │
                    │  │ tool-level RBAC, scope       │  │ per (provider,model,region)  │  │ heuristic degraded response → skip     │   │
                    │  │ narrows never widens across  │  │ or per tool endpoint, never   │  │ non-critical tool → structured error   │   │
                    │  │ delegation, §4.5-4.6)        │  │ one global breaker, §4.3)     │  │ surfaced to supervisor, §4.3           │   │
                    │  └──────────────────────────┘  └────────────────────────────┘  └────────────────────────────────────┘   │
                    │  ┌──────────────────────────┐                                                                              │
                    │  │ microVM Sandbox per Worker  │  Firecracker/Kata; tenant-scoped ENI/VPC; ephemeral, memory-sanitized on   │
                    │  │ Session (§4.8)              │  teardown; no long-lived creds — borrows caller's JWT per request          │
                    │  └──────────────────────────┘                                                                              │
                    └────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                                          │
                    ┌────────────────────────────────────────────────▼───────────────────────────────────────────────────────────┐
                    │                                     PERSISTENCE LAYER                                                          │
                    │  ┌────────────────────┐  ┌───────────────────────┐  ┌─────────────────────┐  ┌───────────────────────────┐│
                    │  │ Per-Worker Event      │  │ External Plan Memory    │  │ Shared-State Store    │  │ Delegation Record Log       ││
                    │  │ History (Temporal      │  │ (survives 200K-ctx      │  │ (OCC version/ETag CAS,│  │ (append-only, hash-chained, ││
                    │  │ Child Workflow per      │  │ truncation; plan        │  │ or agentic-mutex w/   │  │ origin invariant never       ││
                    │  │ subagent, isolated       │  │ persisted immediately   │  │ TTL + fencing token,  │  │ changes at any hop, §4.9)     ││
                    │  │ failure domain, §4.1)    │  │ on every revision, §4.1)│  │ §4.2)                 │  │                              ││
                    │  └────────────────────┘  └───────────────────────┘  └─────────────────────┘  └───────────────────────────┘│
                    └────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                                          │
                    ┌────────────────────────────────────────────────▼───────────────────────────────────────────────────────────┐
                    │                            TELEMETRY / OBSERVABILITY SINKS                                                     │
                    │  Immutable delegation-chain audit log (delegator→delegatee, scope, constraints, denial-as-event, §4.6/4.9) ·    │
                    │  per-worker circuit-breaker state dashboard (§4.3) · cost-velocity meter (tok-or-$/min vs. planned, §3.4) ·      │
                    │  per-stage P50/P95/P99 latency (§3.3) · decision-pattern tracing without conversation-content capture             │
                    │  (Anthropic's privacy-preserving production tracing, §3.5) · MAST-style failure-mode tagging (§4.4)              │
                    └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A query enters the **Supervisor/Lead Agent**, which analyzes it, immediately persists its decomposition plan to **external memory** — critical because the 200K-token context window truncates on overflow, and losing the plan mid-task is catastrophic (Anthropic's own stated design rationale) — then decides effort level via the **Effort/Budget Governor**: 1 agent/3-10 tool calls for simple lookups, 2-4 subagents/10-15 calls each for direct comparisons, 10+ subagents with divided responsibilities for complex research, an explicit governor against runaway fan-out (§2.2/§3.4). (2) The **Capability-Based Router** matches each subtask to a healthy worker profile weighted by declared capability, current circuit-breaker state, historical success rate, cost, and latency (§2.5) — a subtask is never blindly routed to whichever worker is nearest, it is routed to whichever *healthy, capable* worker scores best. (3) The **Handoff/Agents-as-Tool Dispatcher** chooses the delegation primitive per subtask: a synchronous **handoff** transfers full conversation ownership when the specialist should respond directly, while **agents-as-tools** keeps the supervisor in control and folds each worker's bounded result back into its own context for synthesis (§2.3-2.4) — most orchestrator-worker research/analysis pipelines use the latter. (4) Selected workers are spawned **in parallel** (3-5 concurrently in Anthropic's reference architecture, never serially) as isolated **Data Plane** units, each with its own context window and 3+ tools invoked concurrently internally; the **Delegation-Chain Tracker** stamps every hop with the immutable root-human `originSub`, a stable `agentProfileId`, and a per-execution `agentRunId` so parallel runs of the same worker *type* remain distinguishable in the audit trail (§4.9). (5) Every tool call any worker issues passes through the **Zero-Trust MCP Gateway** — a PEP evaluating the worker's SPIFFE-derived identity against tool-level RBAC before dispatch, enforcing that a delegated worker's effective permission set is always the *intersection* of its own profile and its parent supervisor's effective permissions, never wider (§4.5-4.6) — and through a **per-worker circuit breaker** scoped to that specific (provider, model, region) or tool endpoint, never one global breaker, so one degraded dependency cannot block failover to a healthy alternate (§4.3). (6) Code- or shell-executing steps run inside an ephemeral, tenant-scoped **microVM sandbox** destroyed after use, borrowing the caller's JWT for the life of one request rather than holding standing credentials (§4.8). (7) Anthropic's system currently runs this fan-out **synchronously** — the lead agent waits for a full round of subagents before proceeding, a stated, explicit limitation that simplifies coordination at the cost of blocking on the single slowest subagent and preventing subagents from coordinating with each other mid-flight (§2.2) — a design trade-off that recurs directly in §3.3's latency analysis. (8) Completed worker outputs flow to a dedicated **CitationAgent/Synthesis Agent**, a final-pass specialist that matches every claim back to source evidence, deliberately decoupling citation-correctness verification from the research/synthesis loop itself. (9) Every step of the Persistence Layer is scoped per-agent, not globally: each subagent's Temporal Child Workflow maintains its **own** Event History and failure domain, so one worker crashing does not corrupt sibling workers' state and cancellation can propagate cleanly across the whole agent tree (§4.1); a **shared-state store** used for any cross-worker mutable state is guarded by optimistic concurrency control or an agentic mutex with fencing tokens, never a bare read-modify-write (§4.2). (10) Before any response streams back, the full delegation chain — every hop's delegator, delegatee, scope, and constraints, including denied calls — is written to an **immutable, append-only audit log**, because (per §4.4's Replit incident) an agent's own self-report of what happened must never be the only evidence of what actually happened.

---

## 2. Core Mechanics & Algorithms

### 2.1 Canonical topologies and communication complexity

Four topologies dominate production MAS, differentiated by communication complexity, fault isolation, and observability:

| Topology | Communication complexity | State ownership | SPOF risk | Best scale | Latency floor |
|---|---|---|---|---|---|
| **Hub-and-spoke / orchestrator-worker** | Star; O(n) edges | Centralized; workers get copies | Hub is SPOF | 3-7 spokes/hub | Bound by slowest worker |
| **Hierarchical (tree)** | O(n) edges, O(log n) routing depth | Layered; supervisor owns subtree | Subtree-scoped isolation | 20-500 agents | 6-12s minimum (accumulates per level, even with parallel siblings within a level) |
| **Mesh (peer-to-peer)** | O(n²) — n(n-1)/2 potential edges | Transferred on handoff, no canonical copy | No single SPOF, but also no circuit-breaker chokepoint | 3-8 tightly coupled agents | Highest per-hop token cost |
| **Flat / swarm** | Emergent, shared blackboard | Global state, control shell dispatches | Blackboard is the bottleneck | Dozens (one production system reportedly runs ~100 subagents in parallel) | Depends on blackboard contention |

A full mesh is estimated to cost **2–11.8× more tokens than a simple sequential chain** per a secondhand-cited ICLR 2025 analysis `[inferred — original paper not independently verified]`. Enterprise deployments (Anthropic, AWS Bedrock, LangGraph default patterns) converge on **hierarchical orchestrator-worker as the production default**, and a **two-level hierarchy (orchestrator + workers, no further nesting) is the commonly cited Pareto-optimal point** for cost/latency/behavioral-consistency trade-offs.

**The decisive architectural boundary is task shape, not headcount.** Google DeepMind-cited internal research found centralized supervisor coordination **improved performance by 80.9% over single agents on parallelizable tasks** (e.g., financial analysis) but **degraded performance by 39–70% on sequential-reasoning tasks**, because communication overhead fragments continuous reasoning chains. Decompose by independence of subtasks, not by available compute.

### 2.2 Supervisor-worker orchestration: the Anthropic reference algorithm

Anthropic's production multi-agent Research system is the most detailed public engineering account of a live orchestrator-worker deployment, and its role decomposition is the de facto reference implementation:

- **LeadResearcher (Supervisor)**: analyzes the query, saves its plan to **external memory** immediately (the 200K-token window truncates on overflow — losing the plan mid-task is catastrophic), then spawns **3–5 subagents in parallel**, never serially.
- **Subagents (Workers)**: each given an explicit objective, output format, tool/source guidance, and clear task boundaries; each operates in an **isolated context window** and invokes 3+ tools in parallel internally.
- **CitationAgent**: a final-pass specialist matching every claim in the synthesized report back to source documents, decoupling citation-correctness from the research/synthesis loop.
- **Effort scaling embedded directly in prompts** (the algorithm's core parallelism governor): simple fact-finding → 1 agent, 3-10 tool calls; direct comparisons → 2-4 subagents, 10-15 calls each; complex research → 10+ subagents with divided responsibilities.

```
def orchestrate(query, lead_agent, capability_router, max_effort_tier="auto"):
    plan = lead_agent.decompose(query)                       # O(1) LLM call
    persist_to_external_memory(plan)                          # survives ctx truncation, NOT crash recovery
    tier = classify_effort(query) if max_effort_tier == "auto" else max_effort_tier
    n_subagents = {"simple": 1, "comparison": (2, 4), "complex": 10}[tier]  # governor, Sec 3.4

    subagent_specs = plan.decompose_into(n_subagents)          # explicit objective + boundaries each
    workers = [capability_router.route(spec.capability) for spec in subagent_specs]

    # Synchronous fan-out: lead WAITS for the full round before proceeding.
    # Stated Anthropic limitation -- simplifies coordination, but blocks on
    # the single slowest subagent and prevents mid-flight steering.
    results = parallel_dispatch(workers, subagent_specs)       # O(1) wall-clock rounds, bound by slowest branch

    if coverage_thin(results, plan):
        extra_specs = lead_agent.adaptive_replan(results, plan)
        results += parallel_dispatch(
            [capability_router.route(s.capability) for s in extra_specs], extra_specs
        )

    verified = citation_agent.verify_trajectory(results)       # decoupled final pass, Sec 2.4 module 08 analog
    return synthesize(verified)
```

- **Complexity**: one decomposition call + `k` parallel dispatch rounds (typically `k=1`, occasionally `k=2` under adaptive replanning) + one verification pass — **not** `O(n)` sequential LLM calls, which is the entire amortization argument for the pattern; wall-clock cost is `O(max_i latency(worker_i))` per round, not `O(Σ latency(worker_i))`.
- **Invariant**: the plan **must** be persisted to external memory before any subagent is spawned — a crash or truncation after spawn but before persistence loses the decomposition and forces a full, expensive re-plan from scratch.
- **Stated limitation**: subagents execute synchronously — the lead cannot steer subagents mid-flight, subagents cannot coordinate with each other, and the whole system blocks on the single slowest subagent. Anthropic explicitly states asynchronous execution (concurrent, on-demand subagent spawning) would add parallelism but introduces "challenges in result coordination, state consistency, and error propagation" — this is not a solved problem, it is a documented open trade-off.

### 2.3 Message-passing protocols: sync handoff vs. agents-as-tools vs. async actor model

| Protocol | Ownership model | Mechanism | Best fit |
|---|---|---|---|
| **Synchronous handoff** (OpenAI `handoff()`, LangGraph `Command(goto=agent_name)`) | Transfers completely — receiver becomes "the active agent for the rest of the turn," sees full (or filtered) history | Blocking, ownership-transferring; distinct from a tool call | Routing itself is part of the workflow; the specialist should respond directly |
| **Agents-as-tools** (OpenAI `agent.as_tool()`, LangGraph `@tool`-wrapped subagents) | Calling/manager agent retains control and conversation ownership | Bounded, synchronous function call; result folds back into manager's context | Manager should synthesize a final answer combining multiple specialist outputs |
| **Async / actor-model message passing** (AutoGen) | Agents are independent processes exchanging async messages, can spawn new agents dynamically | No assumption of centralized execution | Distributed, multi-process/multi-region deployment |
| **A2A protocol streaming** | Server-generated `Task` + `contextId`; stream via SSE `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` | Stream closes only at a terminal state (`completed`/`failed`/`canceled`/`rejected`) | Cross-vendor, peer-to-peer agent interoperability |

**A2A Task lifecycle state machine** (the wire-protocol formalization of delegation state):

```
   submitted ──▶ working ──┬──▶ completed  (terminal, immutable)
                             ├──▶ failed     (terminal, immutable, scoped to THIS task only)
                             ├──▶ canceled   (terminal, immutable)
                             ├──▶ rejected   (terminal, immutable)
                             └──▶ input-required / auth-required ──▶ working (resumes on external input)
```

- **Invariant**: a `Task` reaching `failed`/`canceled`/`rejected` does not take down the calling client's own task/session — partial-failure semantics are first-class in the wire protocol, not bolted on (§4.4).

### 2.4 Delegation/handoff mechanics — concrete implementations

**OpenAI Agents SDK** — two primitives chosen based on who should own the final answer, with a **documented guardrail gap**: guardrails apply only to the first agent in a handoff chain (input) and the last (output) — mid-chain agents are not guardrail-covered by default. Customization surface: `tool_name_override` (default `transfer_to_<agent_name>`), `on_handoff` callback, `input_filter` (e.g., `handoff_filters.remove_all_tools` strips tool artifacts before forwarding), `input_type` (structured metadata carried through the handoff).

**LangGraph** — the dedicated `langgraph-supervisor` package is now unmaintained; the recommended pattern wraps each worker as an `@tool`-decorated function the supervisor calls via `create_agent`. The legacy `create_handoff_tool` returns `Command(goto=agent_name, graph=Command.PARENT, update={...})` — handoff is a **graph-level control-transfer command**, not just a message.

**Google A2A protocol** (50+ backing partners including Atlassian, SAP, ServiceNow, Salesforce, Accenture, Deloitte, McKinsey):

- **Agent Card**: a JSON manifest at `/.well-known/agent.json` describing identity, capabilities/skills, service endpoint, supported auth (OAuth, mTLS), and I/O modes — the capability-discovery mechanism, letting a client find the best remote agent for a task without hardcoding agent IDs.
- **Task**: the fundamental stateful unit, identified by a server-generated ID plus a `contextId` correlating multi-turn interactions.
- **Artifact**: the tangible output of a task, streamable incrementally.
- A2A is the **horizontal (peer-to-peer, cross-vendor) integration layer**, complementary to MCP's **vertical (agent-to-tool) integration layer** — an agent might use A2A Agent Cards to discover peers, then MCP to call each peer's internal tools.

### 2.5 Capability-based routing

Production agent-routing implementations expose explicit per-worker profile fields as first-class capacity-planning and routing inputs: `maxConcurrency` (default 1), `costPerTask`, `avgLatencyMs`, and historical success-rate tracking, feeding a weighted routing/load-balancing strategy rather than naive round-robin or keyword matching (naive keyword routing to workers is a documented anti-pattern).

```
def route(capability, worker_profiles, circuit_breakers):
    candidates = [w for w in worker_profiles
                  if w.capability == capability and circuit_breakers[w.name].allow_request()]
    if not candidates:
        return None                                    # triggers fallback chain, Sec 4.3
    return max(candidates,
               key=lambda w: w.success_rate - COST_WEIGHT * w.cost_per_task
                             - LATENCY_WEIGHT * w.avg_latency_ms)
```

- **Complexity**: `O(k)` per routing decision, `k` = number of candidate workers registered for that capability — trivial compared to any LLM call in the pipeline, which is why routing logic belongs in deterministic code, never in an LLM call itself.
- **Invariant**: a worker whose circuit breaker is `OPEN` must never be a routing candidate — routing and circuit-breaking are two views of the same health state and must share a single source of truth to avoid dispatching into a known-degraded dependency (§4.3).

### 2.6 Collaboration protocols: message-passing vs. blackboard vs. shared-state vs. actor model

| Protocol | Mechanism | Framework example | Strength | Weakness |
|---|---|---|---|---|
| **Direct message-passing / group chat** | Agents broadcast to a shared transcript; every agent re-reads the growing conversation | AutoGen `GroupChat` | Simple mental model for small teams (< ~6 agents) | Token cost compounds linearly with turns as every agent re-reads the full transcript |
| **Blackboard** | Shared workspace holds partial state; a control shell triggers whichever "knowledge source" can contribute given current board contents; no agent addresses another directly | Classic AI (Hearsay-II); Redis-backed implementations | Agents read only the relevant board slice → lean context; order self-organizes; add/remove sources without rewiring who-talks-to-whom | No native locking — requires explicit conflict resolution (priority-wins, first-commit-wins) layered on top |
| **Shared context object** | Structured shared state; framework manages turn-taking | CrewAI | Low orchestration code | Less flexible for dynamic/emergent workflows |
| **Actor model / independent processes** | Independent processes exchanging async messages, can spawn agents dynamically | AutoGen | Scales across servers/regions; no centralized-execution assumption | Higher engineering complexity for consistency |

A hybrid is common in practice: a durable shared scratchpad/blackboard for facts, plus targeted point-to-point handoffs when agents genuinely need to address one another directly.

---

## 3. Token Economics & NFR Analysis

### 3.1 The published multiplier and cost formula

Anthropic's headline, load-bearing number for this entire architecture class:

> "Agents typically use about **4× more tokens** than chat interactions, and multi-agent systems use about **15× more tokens** than chats." — Anthropic Engineering, June 13, 2025

Mechanistically: each subagent carries its own system prompt, tool schema, and full input/output token cost against its own context window; a lead agent + N subagents + a synthesis/citation pass each independently accrue tokens — a structural property of the orchestrator-worker pattern, not an inefficiency to optimize away.

```
Cost_multiagent(1k runs) = 1000 × [ Cost_lead_decomposition
                                     + Σ(i=1..N) Cost_subagent_i
                                     + Cost_citation_or_synthesis_pass ]
```

*Stated assumptions* (illustrative calibration built from Anthropic's ratio, not raw Anthropic telemetry `[inferred]`): a single chat turn ≈ 1,580 tokens (1×, ~1,000 in / 580 out); a single ReAct-style agent ≈ 6,320 tokens (4×, ~4,000 in / 2,320 out); an orchestrator+subagents multi-agent run ≈ 23,700 tokens (15×, ~15,000 in / 8,700 out). Pricing assumption: a 2026 mid-tier reasoning-capable model at **$3/1M input, $15/1M output**.

```
Chat (1×):         1000 × (1,000 × $3/1M + 580 × $15/1M)   = 1000 × $0.0117  = $11.70 / 1k runs
Single agent (4×):  1000 × (4,000 × $3/1M + 2,320 × $15/1M) = 1000 × $0.0468  = $46.80 / 1k runs
Multi-agent (15×):  1000 × (15,000 × $3/1M + 8,700 × $15/1M) = 1000 × $0.1755  = $175.50 / 1k runs
```

### 3.2 Why the multiplier is justified (when it is) — and the routing-tax gap

Anthropic's own variance decomposition on the BrowseComp evaluation: **three factors explain 95% of performance variance** in multi-agent research quality — **token usage alone explains 80%**, tool-call count and model choice explain the rest. Multi-agent systems primarily work *because* they buy more parallel token/compute budget, not because of emergent "collaboration intelligence." Model upgrades are a larger lever than raw token-budget doubling. Anthropic's explicit economic rule: multi-agent systems are viable **only when task value exceeds the ~15× token cost**, and are a poor fit for (a) tasks requiring shared context/dense inter-agent dependencies (most coding tasks) because LLM agents "are not yet great at coordinating and delegating to other agents in real time," and (b) low-parallelizability tasks.

A widely-cited practitioner critique: **most production multi-agent codebases route every subagent to the same (usually largest/most expensive) model as the orchestrator**, regardless of subtask complexity — a bounded lookup subagent and the orchestrator get billed identically by default. Illustrative example of the fixable savings `[inferred illustrative estimate, not vendor-audited]`: if the delegated-worker share of the 23,700-token multi-agent run (≈70%, or ~16,590 tokens) is routed to a smaller model at $0.25/1M in, $1.25/1M out instead of the $3/$15 orchestrator-tier pricing, the delegated portion's cost per 1k runs drops from roughly $124 to roughly $11 — a **>90% reduction on the routed portion alone**, turning the "15× tax" into closer to a "5-6× tax" without any architecture change, purely from a routing-layer fix.

> ⚠️ **Data gap**: no public, audited multi-vendor benchmark quantifies per-subagent routing savings at scale; the 15× figure itself is Anthropic's self-reported internal telemetry, not independently reproduced by a third party with a controlled methodology.

### 3.3 Latency: P50/P95/P99 for parallel vs. sequential multi-agent workflows

| Dispatch mode | Latency scaling | Observed speedup | Error propagation |
|---|---|---|---|
| Sequential / pipeline | Additive — grows linearly with agent/step count | Baseline | Unidirectional, compounds downstream — an upstream error corrupts everything after it |
| Parallel (fan-out/fan-in) | Bound by the slowest single branch, plus aggregation overhead | 1.8×–3.7× wall-clock speedup; up to 6× cost reduction via batching; 36–50% wall-clock reduction in common content/research workflows | Isolated per branch — one failed branch doesn't block completion of siblings |

**LAMaS** (latency-aware multi-agent orchestration, arXiv 2601.10560) shows that explicitly optimizing the *critical path* of a parallel execution graph — not just task assignment — reduces critical-path length by **38–46%** vs. the prior SOTA multi-agent architecture-search baseline (MaAS), with comparable or better accuracy on GSM8K, HumanEval, MATH. Most production systems today are **not** critical-path-optimized: topology itself, not just parallelism, is a tunable latency variable.

No public source discloses a formal, composed P99 SLA spanning decomposition → parallel dispatch → synthesis → verification as a single pipeline for multi-agent systems specifically. The table below anchors every **measured** cell to the research above and derives **inferred** cells using tail-compounding (a chain that wins on single-call latency can still lose materially at P95/P99 because tail latency compounds multiplicatively across a serial dependency, not additively):

| Stage | P50 | P95 | P99 | Dominant tail cause | Mitigation |
|---|---|---|---|---|---|
| Supervisor decomposition (single lead-agent planning call) | ~2s `[inferred]` | ~4.5s `[inferred]` | ~7s `[inferred]` | Reasoning-tier model provider queueing; output-length variance (plan/subagent-spec count) | Cache/reuse decomposition for structurally identical query shapes |
| Single worker subagent (isolated context, 3-10 internal tool calls, simple tier) | ~3s `[inferred, anchored to Anthropic's 3-10 tool-call effort-tier guidance]` | ~7s `[inferred]` | ~12s `[inferred]` | Sequential internal tool-call chain within one worker | Cap tool calls per worker per the effort-scaling governor (§2.2) |
| Sequential pipeline, N=5 workers, no parallelism (baseline) | **~15s** `[derived: 5 × 3s P50]` | **~35s** `[derived, tail-compounded]` | **~60s** `[derived, tail-compounded]` | Tail latency compounds multiplicatively down a serial chain, not additively | Switch to fan-out/fan-in parallel dispatch — the single largest lever in this table |
| Parallel fan-out/fan-in, N=5 workers (Anthropic-style, 3-5 concurrent) | **~3.5s** `[derived: slowest-branch P50 + aggregation overhead]` | **~8s** `[inferred]` | **~14s** `[inferred]` | Bound by the single slowest branch + synchronous full-round wait (§2.2's stated limitation), not by N | LAMaS-style critical-path optimization (measured 38–46% additional reduction over naive fan-out) |
| Hierarchical topology floor (multi-level tree, parallel siblings per level) | **6-12s minimum** `[measured, §2.1]` | — | — | Latency accumulates per tree *level* even when siblings within a level run in parallel | Prefer a two-level hierarchy (orchestrator + workers, no further nesting) — the Pareto-optimal default |
| CitationAgent / synthesis final pass | ~2s `[inferred]` | ~4s `[inferred]` | ~7s `[inferred]` | Trajectory-wide claim-matching against source documents | Decouple from the main research loop so it doesn't block subagent completion |
| **Composed cycle — Anthropic-style** (decomposition + 3-5 parallel subagents + citation pass, no adaptive replan) | **~8s** `[derived]` | **~16s** `[inferred]` | **~26s** `[inferred]` | Supervisor's synchronous full-round wait for the slowest subagent before proceeding (§2.2) | Async on-demand spawning would add parallelism but — per Anthropic's own stated caveat — introduces "challenges in result coordination, state consistency, and error propagation," not yet a solved trade-off |
| Mesh / peer-to-peer (3-8 tightly coupled agents, iterative refinement) | No stable P50/P95/P99 anchor available `> ⚠️ Gap` | — | — | O(n²) edges; **no circuit-breaker chokepoint** — a corrupted claim from one peer propagates peer-to-peer until the exchange ends | Bound iteration count explicitly; prefer hierarchical topology for any latency-sensitive path |
| Framework orchestration overhead (multi-agent, 3-5 agents, community benchmark) | — | — | Community benchmarks report **3.1–3.5× higher P99** for heavier orchestration frameworks vs. leaner ones for equivalent workflows `> ⚠️ vendor/community benchmark, not independently reproduced` | Graph/message-passing traversal overhead, not model latency | Prefer lean orchestration layers for latency-sensitive paths; reserve heavier frameworks where the overhead is amortized by coordination complexity actually needed |

**Mitigation strategies (composed across the table):** (1) parallelize independent subtasks via fan-out/fan-in rather than a serial chain — the single largest lever on composed P50/P95; (2) optimize the **critical path** of the dispatch graph explicitly (LAMaS), not just which agent gets which task; (3) treat the supervisor's synchronous full-round wait as a deliberate, documented trade-off, not an oversight — moving to async spawning trades this latency ceiling for coordination/consistency risk that must be engineered for, not assumed away; (4) route verification/citation work as a decoupled final pass so it never blocks subagent completion; (5) bound mesh-topology iteration counts explicitly, since mesh has no latency-floor anchor and no circuit-breaker chokepoint to stop a runaway exchange.

### 3.4 Throughput: concurrent worker-fleet capacity planning and back-pressure

**Capacity-planning formula:**

```
Sustained_worker_fleet_throughput = min(
    Supervisor_LLM_TPM_limit / avg_tokens_per_decomposition_call,
    Σ(worker_type_i) WorkerLLM_TPM_limit_i / avg_tokens_per_worker_task,
    ToolAPI_rate_limit_per_dependency,
    Concurrent_worker_ceiling            # bounded by supervisor context-window accumulation, Sec 3.5
)
```

- **Effort-to-parallelism governor as explicit back-pressure design**: Anthropic's embedded rule (1 agent/3-10 calls simple → 10+ subagents/complex, §2.2) exists specifically to prevent runaway fan-out — the documented early failure mode is spawning 50 subagents for a simple query.
- **Isolated failure domains as a scaling enabler, not just a reliability feature**: a production case study (Temporal-backed AI app-builder) reports **1 billion+ agent Actions per month**, with each build involving dozens of LLM calls, hundreds of tool executions, and multiple specialized agents — each subagent running as an isolated Child Workflow with its own failure domain, timeout, and execution history is the concrete mechanism enabling that throughput without a shared-state bottleneck.
- **Coordinated back-pressure**: when a downstream worker's circuit breaker opens, upstream/supervisor agents should receive a back-pressure signal through the orchestration layer and proportionally reduce their own dispatch rate — preventing one degraded worker from triggering a retry storm from its callers (§4.3).
- **Task-shape dependency**: the same Google-cited research from §2.1 applies directly here — supervisor patterns boosted parallel-task throughput by **80.9%** but degraded sequential-task throughput by **39-70%** — concurrency gains are task-shape-dependent, not universal, and capacity plans must be built per workload class, not as one fleet-wide number.

> ⚠️ **Data gap**: no industry-standard "multi-agent task throughput" benchmark exists; published numbers are mostly model-serving throughput (tokens/sec), not end-to-end delegation-chain throughput. Treat fleet-level throughput estimates as `[inferred]`, bounded by provider rate limits and the cost-velocity circuit breaker (§4.3).

### 3.5 NFR analysis: availability, RPO/RTO tied to per-agent checkpoint granularity, and compliance trade-offs

No vendor publishes an availability SLA scoped to "a composed supervisor + N-worker delegation chain" as a unit. Every figure below beyond the topology-level SPOF findings (§2.1) is an **`[inferred/recommended]`** design target, stated explicitly because this is the section most commonly audited for exactly these numbers.

**Availability targets by deployment pattern:**

| Deployment pattern | Availability target | Basis |
|---|---|---|
| Single agent, no decomposition | **~99%** (~87.6h/year) `[inferred]` baseline | One failure = total failure; reference point for the multipliers below |
| Hub-and-spoke, single supervisor, no supervisor HA | **~99.5%** `[inferred]` | The hub is a documented structural SPOF (§2.1) — if the supervisor's own reasoning fails or its context window is exhausted managing worker outputs, the entire subtree stalls regardless of individual worker health (§5.2 findings echoed in §4.4) |
| Hierarchical, supervisor + isolated per-worker Child Workflows | **99.9%** `[inferred]` | Subtree-scoped fault isolation — a failed branch doesn't corrupt or block sibling branches (§4.1/§4.4) |
| Hierarchical + per-worker circuit breakers and fallback chains | **99.95%** `[inferred]` | The fallback chain absorbs single-provider LLM outages — a provider incident degrades quality, not availability (§4.3) |
| Mesh / peer-to-peer | Lower **effective** availability despite "no single SPOF" `[inferred]` | Failure *occurrence* has no single point, but failure *containment* does not exist either — a corrupted claim propagates until the exchange ends with no chokepoint to stop it (§2.1/§5.2) |
| Multi-region durable execution, replicated per-agent checkpoint store | **99.99%** (~52min/year) `[inferred]` | Cross-region failover removes single-region infra as a common-mode failure; residual risk is a correlated multi-provider LLM outage affecting all regions simultaneously |

**RPO/RTO tied to per-agent checkpoint granularity:**

| Checkpoint tier | Mechanism | Granularity | RPO | RTO |
|---|---|---|---|---|
| Per-subagent Temporal Child Workflow Event History | Every subagent's Activity *result* persisted independently, not just a flag | Per-Activity, per-worker | **Near-zero** — a completed worker's result is durably recorded before the supervisor aggregates it | **Seconds–minutes** — a crashed subagent's Child Workflow replays independently without corrupting sibling workflows or re-asking the LLM for decisions already made |
| Supervisor/lead plan checkpoint (external memory) | Plan persisted outside the 200K context window on every decomposition/replan | Per plan/replan revision | **One decomposition revision** if persisted only at initial creation `> ⚠️ Gap: a real design risk, not just theoretical` — **near-zero** if persisted after every adaptive replan | **Minutes** — reload the plan artifact and re-spawn only the incomplete subagent branches, not a full restart (Anthropic's stated "resume from where the agent was" account) |
| Shared-state store (OCC-versioned) | Version/ETag compare-and-swap writes | Per write | **Near-zero** — a version mismatch fails the write loudly rather than silently corrupting shared state | **Seconds** — the losing writer re-reads fresh state and retries |
| Delegation Record / audit log | Append-only, hash-chained | Per hop | **Zero** — fail-closed means no delegation-relevant action executes before the log write is durable | **N/A** — the chain is re-derivable from the log itself, not "recovered" |

**Trade-off 1 — parallelism vs. coordination cost.** The 80.9%/-39-70% split (§2.1) is the single sharpest empirical statement of this trade-off: supervisor coordination is a large net positive on independently-decomposable tasks and a large net *negative* on tasks requiring continuous reasoning chains, because communication overhead fragments exactly the continuity those tasks depend on. Anthropic's own economic rule reinforces this at the cost layer — multi-agent decomposition is justified **only** when task value exceeds the ~15× token cost, and is explicitly a poor fit for dense-dependency tasks like most coding work. There is no universal "more agents = more reliable/more capable" answer; each additional agent is a deliberate cost/latency/coordination trade, evaluated per task class.

**Trade-off 2 — worker specialization vs. flexibility.** A hierarchical system with **four or more concurrently active workers routinely hits the supervisor's context-window ceiling** as it accumulates every worker's output — a direct, quantifiable capacity-planning input, not an abstract concern, and the reason a two-level hierarchy is the recommended default scale point before adding depth. Specialized workers (narrow tool access, narrow role) are easier to secure (§4.6's RBAC scoping) and easier to reason about individually, but a system built entirely around named specialist roles must be re-tuned whenever team composition changes. Microsoft's Magentic-One claims its four-agent team (Orchestrator, Coder, Terminal, WebSurfer, FileSurfer) can have agents added or removed "without additional prompt tuning or training" `[vendor claim, not independently stress-tested at scale in the source material]` — a claimed structural advantage over tightly-coupled designs that require re-tuning the whole system on membership change; treat this as directional pending independent verification.

**Compliance.** Delegation-chain audit requirements map to the same governed-data-surface logic as any AI-agent processing record: **GDPR Article 30** (records of processing where agents are processors) and the **EU AI Act Articles 12–14** (logging requirements for high-risk AI systems) both require exactly the kind of append-only, per-hop Delegation Record described in §4.9 — a policy document without technical enforcement evidence is treated as insufficient under both. This is the direct justification for the audit log being an enforcement-layer artifact, not agent-generated narration.

> ⚠️ **Data gap**: no delegation-chain audit spec (ADCS, PEDIGREE, ACAP) has vendor-neutral ratified status as of this writing (2026-08-21) — treat §4.9's mechanisms as directional best practice, not settled compliance-certified standard.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution across agent boundaries

Multi-agent systems are, per multiple independent sources, fundamentally distributed-systems problems wearing an LLM costume — "the non-deterministic nature of LLMs makes Durable Execution not just useful but essential."

**Temporal's model** (the most mature public reference architecture): orchestration logic runs as a **deterministic Workflow function**; every step is journaled to an immutable **Event History**. LLM calls, tool executions, and I/O are wrapped as **Activities** — retryable, idempotent, side-effecting units recorded once in history. On crash/restart, Temporal **replays** the Event History to reconstruct exact state — a completed Activity's recorded result is returned directly, **not re-executed**, preventing duplicate LLM billing and non-deterministic behavior divergence after a crash. **Critical constraint**: calling an LLM directly inside a Workflow (not wrapped in an Activity) breaks determinism — replay would re-issue the call and could get a different response, corrupting the Workflow's state.

**Multi-agent-specific mechanism**: a supervisor spawns subagents as **Child Workflows**, each with its own failure domain, timeout, and execution history — isolated parallel work and clean cancellation propagation across the whole agent tree. Anthropic's own account (independent of Temporal) confirms the same philosophy without naming Temporal: "we built systems that can resume from where the agent was when the errors occurred... instead of restarting from the beginning," and uses **rainbow deployments** (gradually shifting traffic between old/new agent code versions while both run simultaneously) because agents are highly stateful and mid-execution at any given deploy moment.

### 4.2 Shared-state consistency and distributed locking

Independent practitioner sources converge on the same root cause: **LLM reasoning cycles are multi-second "critical sections,"** far longer than a normal thread's read-modify-write window, making classic race conditions dramatically more likely and more damaging in multi-agent systems than in traditional concurrent software. **An LLM cannot reason its way out of a race condition** — the race exists in the gap between read and write, not in reasoning quality — coordination must be enforced atomically at the tool/orchestration layer, below the model.

**Production-viable patterns, ranked by use case:**

1. **Optimistic concurrency control (OCC)** — version/ETag on shared state; writes are compare-and-swap (`UPDATE ... SET value=?, version=version+1 WHERE id=? AND version=?`); a version mismatch fails the write loudly, forcing re-read-and-retry. **Recommended default** for agent operations lasting 5-15 seconds, because holding a traditional lock across that duration causes lock convoys.
2. **Agentic mutex / semantic locking** — a distributed, orchestration-layer lock keyed on a semantic domain boundary (e.g., `account:12345`), always paired with **TTLs** (so a dead/crashed agent doesn't hold a lock forever) and **fencing tokens** (monotonically increasing identifiers preventing a stale agent — one whose lease expired during a GC pause or network partition — from overwriting newer data).
3. **Single-writer-by-routing** — route every operation on a given resource to one worker/queue partition keyed by resource ID; concurrency across distinct resources is untouched, concurrency on the *same* resource becomes structurally impossible (at the cost of a hot-resource bottleneck).
4. **Structural isolation / workspace branching** — for complex work (e.g., coding agents), avoid shared state entirely: each agent works in an isolated sandbox/branch and compiles changes into a structured PR/patch; the control plane resolves collisions deterministically at a merge boundary.
5. **Idempotency keys** — every tool call carries a unique operation ID so duplicate/retried operations are detected and discarded rather than double-executed — essential because non-idempotent operations ("charge the customer," "send the email") executed twice cannot be cleanly rolled back.
6. **Global lock-ordering rule** — sort every lock set by canonical resource identifier before acquisition, enforce mandatory acquisition timeouts, never hold a lock across a model call.
7. **Vector clocks** — for causal-ordering awareness beyond OCC: each agent maintains a vector `[n1, n2, n3, ...]` of applied-event counts per peer; element-wise comparison determines whether one update causally preceded another or the updates are genuinely concurrent and need a merge strategy.

**Coordination-layer choice**: Redis for low-latency, non-critical-integrity tasks; etcd or ZooKeeper for high-integrity requirements where correctness is non-negotiable — financial/compliance-sensitive operations should prefer pessimistic queues over optimistic retries.

### 4.3 Circuit breakers per worker agent

Standard three-state circuit breaker (CLOSED → OPEN → HALF_OPEN), scoped **per dependency** — critically, **per (provider, model, region) tuple** for LLM calls and **per tool endpoint** for tool calls, **never one global breaker** (a global breaker would incorrectly block fallback to a healthy alternate provider when only one is degraded).

**Trigger design**: use error rate **and** latency together, not raw error count alone. Practitioner thresholds: open on a trailing-window (e.g., 1 minute) error rate exceeding ~30%, cool down 30-60 seconds before probing recovery, exponential backoff on repeated trips.

**Agent-specific trigger signatures beyond standard 5xx/timeout**:
- **Semantic loops** — repeated identical prompts or the same tool call with the same arguments in a tight loop.
- **Cost velocity** — spend rate exceeding a configured budget × multiplier (e.g., a $50/day workload suddenly spending $5/minute should trip before it becomes a line item).
- **Context growth pathology** — identical contexts with monotonically growing token counts (a stuck reasoning loop that "obeys" the rate limit but is still wasteful).

**Fallback hierarchy** (priority order): same prompt on a cheaper/alternate model → cached/previously-computed answer → rule-based/heuristic degraded response → skip non-critical tool call and continue with reduced capability → structured "dependency unavailable" error surfaced one layer up. **Coordinated back-pressure**: when a downstream worker's breaker opens, upstream agents should receive a signal and proportionally reduce dispatch rate, preventing one degraded worker from triggering a retry storm.

**Rate limiting as a complementary (not substitute) layer**: (1) token bucket per (user, resource, model) to catch volume; (2) circuit breakers on pattern signatures (cost velocity, repeated calls, error rate) to catch runaways under the volume ceiling; (3) declarative fallback chain (primary → cheaper model → semantic cache → 503). Goal: not eliminate all runaways, but **bound blast radius** so one misbehaving worker never breaks other callers, the shared budget, or on-call sleep.

### 4.4 Failure taxonomy, dead-letter handling, and partial-failure handling

| Class | Definition | Multi-agent-specific examples | Mitigation |
|---|---|---|---|
| **Transient** | Resolves on retry without intervention | Worker-LLM 5xx/timeout, tool-API 503, rate-limit 429 | Retry with jittered exponential backoff; honor `Retry-After`; never re-delegate for this class |
| **Permanent** | Fails identically on every retry | A worker's required tool no longer exists; a delegated precondition is permanently violated | Never retry — re-route to an alternate worker or escalate, carrying the failure reason forward |
| **Poison-pill** | A specific (worker, task) pair deterministically breaks every attempt | A malformed subtask that crashes a specific worker's parser every time; a duplicate/replayed message from at-least-once delivery | Idempotency-keyed claim-before-execute + **dead-letter after N attempts** + broker-level dedup (below) |

**Message loss/duplication and the dead-letter path**: multi-agent systems inherit the **at-least-once delivery problem** from underlying message infrastructure (Kafka, SQS, RabbitMQ, Pub/Sub) — brokers cannot distinguish "the consumer never received this" from "the consumer processed it and the ack was lost," so they redeliver, meaning **any inter-agent message or tool call can be delivered more than once**. The production-grade fix combines **broker-level deduplication** (SQS FIFO `MessageDeduplicationId` with a 5-minute window; Kafka idempotent/transactional producers; RabbitMQ named producers with publishing IDs) with **consumer-side idempotency** via an **inbox pattern**: before executing a side effect, atomically check-and-claim a durable dedup key in the same transaction as the business write, so "have I seen this?" and "apply the effect" either both commit or both roll back. A worker/task pair that exhausts its retry budget without success routes to a **dead-letter queue** rather than looping indefinitely or silently dropping — surfaced to the supervisor as a structured failure, not a silent gap in results.

**Partial-failure handling**:
- **Isolation-by-construction**: per-worker Child Workflows give each subagent its own failure domain, so a failed worker doesn't corrupt sibling workers' state; cancellation propagates cleanly across the tree.
- **Graceful degradation via general-purpose fallback agents**: when a specialized worker fails, a general-purpose agent can pick up the request with reduced capability rather than failing the whole delegation — the orchestration layer maintains fallback routing tables keyed on circuit-breaker state.
- **A2A's terminal-state model** gives partial failure a first-class place in the wire protocol: a `Task` can independently reach `failed`/`canceled`/`rejected` without taking down the calling client's own task/session (§2.3).
- **Real documented cascading-failure case**: a LangGraph-based customer-support workflow began looping/responding irrelevantly; root cause was a downstream order-data service outage the workflow was tightly coupled to with no failure-detection mechanism — one downstream outage cascaded into total unavailability. Fix: an MCP `Tool`-wrapped `ServiceChecker` that proactively checks downstream health and lets the workflow branch into graceful recovery instead of hanging/looping.
- **Real documented infra-hygiene incident**: a major agent-framework vendor's API suffered **55% request failures for 28 minutes** (monthly uptime dropped from a typical 99.93-99.99% to 95.09%) because an SSL certificate silently failed to auto-renew for months, root-caused to a stale/conflicting DNS record from a dangling Terraform config — illustrating that agent-fleet reliability is frequently gated by mundane infrastructure hygiene, not exotic agent-reasoning failures.

### 4.5 Zero-Trust agent-to-agent authentication

The converging industry pattern treats agents as **non-human workload identities**, not extensions of a human session:

- **SPIFFE (Secure Production Identity Framework for Everyone)** is emerging as the standard identity substrate: cryptographically verifiable **SVIDs** (X.509 or JWT) issued per workload, replacing static API keys/passwords. IDs take the form `spiffe://<trust-domain>/agent/<agent-type>/<instance-id>`.
- **mTLS** between agents provides mutual authentication and cryptographic proof of key possession — both endpoints present certificates during the handshake, no credentials transmitted over the wire.
- **SPIRE** (SPIFFE's runtime implementation) handles attestation and automatic short-lived certificate rotation, eliminating long-lived secrets and the manual-rotation burden unworkable for thousands of ephemeral, autonomously-spawned agents.
- **OAuth 2.0 Token Exchange (RFC 8693)** layers on top for *delegated* access: an agent presents its SPIFFE SVID as an "actor token" to obtain a narrow, short-lived downstream token.
- Emerging IETF drafts extend this for agent-specific delegation: **KAIF** (token exchange + SPIFFE attestation + operator-assigned authorization tiers + delegation-depth tracking + real-time revocation); **PEDIGREE** (cryptographic per-hop delegation, monotonic scope attenuation enforced at mint- and verify-time); **ACAP** (a short-lived JWT bound to a SHA-256 hash of the originating human instruction, with each delegation narrowing scope and extending a tamper-evident token-ID chain).

> ⚠️ **Data gap**: as of 2026-08-21 these agent-specific identity/delegation drafts (KAIF, PEDIGREE, ACAP) are IETF **Internet-Drafts**, not ratified standards — treat as directional, not settled cross-vendor practice.

### 4.6 Tool-level RBAC per agent role

Consistent enterprise pattern: **access is gated at the infrastructure/tool layer, not via model-level instructions** — "don't rely on prompt engineering to prevent this" is a recurring, near-verbatim theme.

- Each agent (or agent *type*) is assigned scoped, role-specific tool access — e.g., a "sales agent" is denied HR-data access and blocked from destructive writes at the RBAC layer, independent of what the agent's prompt claims it should do.
- Policy engines (Cedar, OPA/ABAC) evaluate authorization using the agent's cryptographic (SPIFFE) identity as the subject, enabling fine-grained, auditable "which agents can call which tools/talk to which peers" rules.
- **Trust must narrow, never widen, across a delegation chain** — a child/delegated agent's effective permission set is the *intersection* of its own declared profile and its parent's effective permissions, enforced structurally: `effective_child = intersect(effective_parent, profile_child)`.
- Real production reference implementation: every tool call is authenticated, authorized against tool-level RBAC, PII-redacted, and audited — **including denied calls** (denial is an auditable event, not a silent drop).

### 4.7 PII filtering across agent boundaries (detect → redact → audit)

- A central **policy-enforcement/guardrails layer intercepts requests and responses** between agents and tools, masking/tokenizing PII fields based on the calling role's permissions *before* the data reaches either the model context or the persistent log store — e.g., a CRM lookup returns a customer record, but `email`/`phone` are redacted unless the calling agent's role + policy explicitly allow raw access.
- Some architectures run PII/prompt-injection detection as an **independent "Guard-In" agent separate from the executing orchestrator** — so the guardrail doesn't "answer to" the same orchestrator it checks, preserving separation-of-duties.
- For embedding-based/RAG-adjacent pipelines, sensitive data should be **pseudonymized before embedding generation** to prevent embedding-inversion attacks `[inferred from source framing, not independently verified against a specific CVE]`.

### 4.8 Sandbox isolation per worker

Two isolation tiers are in active production use, with multiple sources warning that **software-only isolation (containers + network proxy) has already failed** at major AI labs `[vendor claim, unverified against specific incident reports]`.

- **Hardware-level isolation via microVMs** (Firecracker/Kata Containers, AWS Bedrock AgentCore): each session/agent gets a dedicated microVM with isolated CPU, memory, filesystem, network namespace, terminated and memory-sanitized after the session.
- **Software isolation via gVisor** (a user-space kernel): lower overhead, more agents per unit of compute, at the cost of a smaller but non-zero escape surface vs. a true microVM.
- **Axonius** (cybersecurity asset-inventory, multi-tenant B2B): dedicated AgentCore runtime + ENI per customer VPC/subnet, session-isolated microVMs — "one customer's agent has no network path to another customer's data" — and agents hold **no long-lived credentials**, borrowing the user's JWT for the life of a single request.
- **Cohere Health** (clinical policy digitization): the same AgentCore microVM isolation for multi-tenant healthcare data, reporting **30% reduction in policy digitization time** (2h15m → 1h35m/policy) and deployment velocity improving from **3-4 months to 2-6 weeks** per deployment cycle.

### 4.9 Auditability of delegation chains

This is an active IETF/industry standardization area converging on a common shape before a ratified standard exists:

- **Delegation Record** as the atomic audit unit: captures delegator, delegatee, scope, and constraints at each hop; records are **append-only** — no actor may remove or reorder a prior actor's entry.
- **Origin invariant**: the root human identity (`originSub`) must never change at any depth of the delegation chain — every downstream action must trace back to exactly one accountable human.
- **Scope-narrowing invariant**: at every hop, effective permissions are the intersection of the parent's effective scope and the child's own declared profile — enforced structurally, not just as policy.
- **Cycle prevention**: an agent must not appear twice in its own delegation chain.
- **Cryptographic binding**: proposals (PEDIGREE, ACAP) use hash-chained, append-only logs where each entry commits to the previous entry's hash, giving tamper-evidence; some additionally require a "completion block" cryptographically binding an action's outcome to the chain that authorized it.
- **Dual identity per agent**: a stable `agentProfileId` (agent *type*) plus a per-execution `agentRunId`, so audit logs correlate calls from one specific concurrent run even when many instances of the same agent type run in parallel.
- Concrete audit-record fields cited in production governance tooling: which agents were involved (`Planning Agent → Provisioning Agent`), whether sensitive data was touched (`No PII detected, risk score 8/100`), duration (`847ms`), and a `runChain` linking the full execution trace.

---

## 5. Production Enterprise Code

The implementation below is a hardened Python supervisor dispatching to multiple worker agents, wiring together every pattern from §3–§4: retries with exponential backoff + full jitter, a per-worker circuit breaker (CLOSED→OPEN→HALF_OPEN), a fallback chain (alternate specialist worker → generalist degraded-capability worker → structured partial failure), content-hash idempotency keys per delegation hop, capability-based routing weighted by success rate/cost/latency, and structured JSON logging correlated by a delegation-chain identity (`originSub` root-human + `agentRunId`) that survives across thread-pool worker boundaries. Standard library only.

```python
"""
supervisor_worker_orchestrator.py

A production-hardened supervisor dispatching to multiple worker agents,
demonstrating every pattern from Module 09 (Multi-Agent Systems) Sec 3-4:

  - retries with exponential backoff + full jitter for transient
    worker/tool failures (Sec 4.4 transient/permanent/poison-pill taxonomy)
  - a per-worker circuit breaker: CLOSED -> OPEN -> HALF_OPEN, scoped
    per (worker, capability) never global (Sec 4.3)
  - capability-based routing weighted by success rate / cost / latency,
    skipping any worker whose breaker is open (Sec 2.5)
  - a fallback chain: alternate specialist worker -> generalist
    reduced-capability worker -> structured partial failure (Sec 4.3/4.4)
  - content-hash idempotency keys per delegation hop (Sec 4.2/4.4)
  - structured JSON logging correlated by delegation-chain identity
    (originSub + agentRunId, Sec 4.9) that survives thread-pool
    execution, where Python's contextvars do NOT auto-propagate
  - graceful degradation for partial worker-fleet failure: the
    supervisor returns a "partial_degraded" result with an explicit
    list of which subtasks fell back, rather than failing outright

Install:  no dependencies (stdlib only; swap Mock* worker functions
          for real LLM/tool-calling clients in production)
Run:      python supervisor_worker_orchestrator.py
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import logging
import random
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------
# 1. Structured logging correlated by delegation-chain identity (Sec 4.9)
# --------------------------------------------------------------------------

_origin_sub: ContextVar[str] = ContextVar("origin_sub", default="-")
_agent_run_id: ContextVar[str] = ContextVar("agent_run_id", default="-")


class DelegationChainFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.origin_sub = _origin_sub.get()
        record.agent_run_id = _agent_run_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mas_supervisor")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"origin_sub":"%(origin_sub)s","agent_run_id":"%(agent_run_id)s",'
            '"msg":%(message)s}'
        )
    )
    handler.addFilter(DelegationChainFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


def bind_delegation_context(origin_sub: str, run_id: str) -> None:
    """Sets the delegation-chain correlation context for the CURRENT
    thread. contextvars are per-OS-thread by default and are NOT
    automatically copied into concurrent.futures.ThreadPoolExecutor
    worker threads -- unlike asyncio's run_in_executor, plain
    ThreadPoolExecutor.submit() does not copy the caller's Context.
    Every worker-dispatch function below re-binds explicitly at entry
    so every log line -- even ones emitted deep inside a pooled worker
    thread -- carries the correct origin_sub/agent_run_id (Sec 4.9's
    origin invariant: every downstream action traces to one human)."""
    _origin_sub.set(origin_sub)
    _agent_run_id.set(run_id)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.4): transient vs. permanent
# --------------------------------------------------------------------------

class AgentError(Exception):
    """`transient=False` marks permanent errors that must never be
    retried against the same worker (a tool that no longer exists, a
    permanently violated precondition) -- these route straight to the
    fallback chain instead."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


# --------------------------------------------------------------------------
# 3. Retry with exponential backoff + full jitter (Sec 4.3/4.4)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.05, cap_s: float = 1.5) -> float:
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], dict], worker_name: str,
                     max_attempts: int = 3, base_s: float = 0.05, cap_s: float = 1.5) -> dict:
    last_error: Optional[AgentError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except AgentError as exc:
            last_error = exc
            if not exc.transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error",
                                      "worker": worker_name, "reason": str(exc)}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff", "worker": worker_name,
                                      "attempt": attempt + 1, "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per worker agent (Sec 4.3)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold_ratio: float = 0.6
    window_size: int = 5
    cooldown_s: float = 6.0
    half_open_max_probes: int = 1

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: list = field(default_factory=list, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open", "worker": self.name}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self) -> None:
        self._outcomes.append(True)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "worker": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "worker": self.name, "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(worker_name: str) -> CircuitBreaker:
    if worker_name not in _BREAKERS:
        _BREAKERS[worker_name] = CircuitBreaker(name=worker_name)
    return _BREAKERS[worker_name]


# --------------------------------------------------------------------------
# 5. Idempotency keys per delegation hop (Sec 4.2/4.4)
# --------------------------------------------------------------------------

def dispatch_idempotency_key(subtask_id: str, worker_name: str, args: dict) -> str:
    payload = f"{subtask_id}:{worker_name}:{json.dumps(args, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# 6. Capability-based routing (Sec 2.5)
# --------------------------------------------------------------------------

@dataclass
class WorkerProfile:
    name: str
    capability: str
    cost_per_task: float
    avg_latency_ms: float
    success_rate: float
    fn: Callable[[dict], dict]


class CapabilityRouter:
    """Weighted routing by capability match, then success-rate/cost/
    latency (Sec 2.5), skipping any worker whose breaker is OPEN --
    routing and circuit-breaking share one source of truth (Sec 4.3)."""

    COST_WEIGHT = 0.05
    LATENCY_WEIGHT = 0.00005

    def __init__(self, profiles: list[WorkerProfile]):
        self._by_capability: dict[str, list[WorkerProfile]] = {}
        for p in profiles:
            self._by_capability.setdefault(p.capability, []).append(p)

    def route(self, capability: str, exclude: Optional[set] = None) -> Optional[WorkerProfile]:
        exclude = exclude or set()
        candidates = [
            w for w in self._by_capability.get(capability, [])
            if w.name not in exclude and get_breaker(w.name).allow_request()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda w: w.success_rate
                    - self.COST_WEIGHT * w.cost_per_task
                    - self.LATENCY_WEIGHT * w.avg_latency_ms)


# --------------------------------------------------------------------------
# 7. Mock worker agents (specialists + generalist fallback)
# --------------------------------------------------------------------------

def make_worker(name: str, fail_rate: float) -> Callable[[dict], dict]:
    def _run(task_args: dict) -> dict:
        if random.random() < fail_rate:
            raise AgentError(f"{name} tool/LLM call failed", transient=True)
        return {"worker": name, "result": f"{name}_output_for::{task_args.get('query', '?')}"}
    return _run


def generalist_fallback_worker(task_args: dict) -> dict:
    """Reduced-capability fallback -- picks up the request when every
    specialist for this capability is degraded, rather than failing
    the whole delegation outright (Sec 4.4 graceful degradation)."""
    return {"worker": "generalist_fallback",
            "result": f"best_effort_for::{task_args.get('query', '?')}",
            "degraded": True}


# --------------------------------------------------------------------------
# 8. Supervisor dispatch: one subtask, with fallback chain (Sec 4.3/4.4)
# --------------------------------------------------------------------------

@dataclass
class DelegationResult:
    subtask_id: str
    worker: str
    status: str
    result: Optional[dict] = None
    degraded: bool = False


def dispatch_subtask(router: CapabilityRouter, capability: str, subtask_id: str,
                      args: dict, origin_sub: str, run_id: str) -> DelegationResult:
    bind_delegation_context(origin_sub, run_id)   # re-bind: may run in a pool thread

    worker = router.route(capability)
    if worker is None:
        log.info(json.dumps({"event": "delegation_hop", "subtask_id": subtask_id,
                              "capability": capability, "outcome": "no_healthy_worker_fallback"}))
        return DelegationResult(subtask_id, "generalist_fallback", "degraded",
                                 generalist_fallback_worker(args), degraded=True)

    idem_key = dispatch_idempotency_key(subtask_id, worker.name, args)
    breaker = get_breaker(worker.name)
    try:
        result = call_with_retry(lambda: worker.fn(args), worker.name, max_attempts=3)
        breaker.record_success()
        log.info(json.dumps({"event": "delegation_hop", "subtask_id": subtask_id,
                              "delegatee": worker.name, "idempotency_key": idem_key,
                              "outcome": "success"}))
        return DelegationResult(subtask_id, worker.name, "success", result)
    except AgentError as exc:
        breaker.record_failure()
        log.info(json.dumps({"event": "delegation_hop", "subtask_id": subtask_id,
                              "delegatee": worker.name, "idempotency_key": idem_key,
                              "outcome": "failed", "reason": str(exc)}))

        # Fallback chain: alternate specialist -> generalist -> partial failure (Sec 4.3)
        alt = router.route(capability, exclude={worker.name})
        if alt is not None:
            try:
                alt_result = call_with_retry(lambda: alt.fn(args), alt.name, max_attempts=2)
                get_breaker(alt.name).record_success()
                log.info(json.dumps({"event": "delegation_hop", "subtask_id": subtask_id,
                                      "delegatee": alt.name, "outcome": "success_on_fallback_worker"}))
                return DelegationResult(subtask_id, alt.name, "success", alt_result)
            except AgentError:
                get_breaker(alt.name).record_failure()

        log.info(json.dumps({"event": "delegation_hop", "subtask_id": subtask_id,
                              "delegatee": "generalist_fallback",
                              "outcome": "degraded_after_specialist_exhaustion"}))
        return DelegationResult(subtask_id, "generalist_fallback", "degraded",
                                 generalist_fallback_worker(args), degraded=True)


# --------------------------------------------------------------------------
# 9. Supervisor: decompose, fan out in parallel, synthesize (Sec 2.2)
# --------------------------------------------------------------------------

def run_supervisor(query: str, origin_sub: str = "user-42") -> dict:
    profiles = [
        WorkerProfile("search_worker_a", "search", cost_per_task=0.008,
                      avg_latency_ms=600, success_rate=0.90, fn=make_worker("search_worker_a", 0.20)),
        WorkerProfile("search_worker_b", "search", cost_per_task=0.010,
                      avg_latency_ms=550, success_rate=0.92, fn=make_worker("search_worker_b", 0.15)),
        WorkerProfile("analysis_worker", "analysis", cost_per_task=0.020,
                      avg_latency_ms=1200, success_rate=0.90, fn=make_worker("analysis_worker", 0.20)),
        WorkerProfile("citation_worker", "citation", cost_per_task=0.015,
                      avg_latency_ms=900, success_rate=0.95, fn=make_worker("citation_worker", 0.10)),
    ]
    router = CapabilityRouter(profiles)
    run_id = str(uuid.uuid4())
    bind_delegation_context(origin_sub, run_id)

    log.info(json.dumps({"event": "supervisor_start", "query": query, "run_id": run_id}))

    # Effort-scaling governor (Sec 2.2/3.4): a "comparison"-tier query
    # gets 2-4 parallel subagents, never an unbounded fan-out.
    subtasks = [
        ("s1", "search", {"query": f"{query} :: part A"}),
        ("s2", "search", {"query": f"{query} :: part B"}),
        ("s3", "analysis", {"query": query}),
    ]

    results: list[DelegationResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(subtasks)) as pool:
        futures = [
            pool.submit(dispatch_subtask, router, cap, sid, args, origin_sub, run_id)
            for sid, cap, args in subtasks
        ]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    degraded = [r for r in results if r.degraded]
    if len(degraded) == len(results):
        log.info(json.dumps({"event": "supervisor_all_workers_degraded", "run_id": run_id}))
        return {"status": "degraded_total", "run_id": run_id,
                "results": [dataclasses.asdict(r) for r in results]}

    # CitationAgent-style final synthesis pass, decoupled from the
    # fan-out loop above (Sec 2.2) -- runs against whatever evidence
    # the fan-out actually produced, degraded or not.
    citation = dispatch_subtask(
        router, "citation", "s4-citation",
        {"query": query, "evidence": [r.result for r in results if r.result]},
        origin_sub, run_id,
    )
    results.append(citation)

    status = "complete" if not degraded else "partial_degraded"
    log.info(json.dumps({"event": "supervisor_complete", "status": status,
                          "degraded_subtasks": [r.subtask_id for r in degraded], "run_id": run_id}))
    return {"status": status, "run_id": run_id,
            "results": [dataclasses.asdict(r) for r in results]}


# --------------------------------------------------------------------------
# 10. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(11)
    output = run_supervisor("assess competitor X's pricing strategy shift")
    print(json.dumps(output, indent=2))
```

**What each pattern buys, mapped back to §2–§4.** `bind_delegation_context()` solves a real, easy-to-miss production bug: Python's `contextvars` do **not** automatically propagate into `ThreadPoolExecutor` worker threads (unlike `asyncio.loop.run_in_executor`, which copies the caller's `Context`), so a naive implementation would silently lose the `originSub`/`agentRunId` correlation on every parallel-dispatched worker's log lines — exactly the failure mode §4.9's origin invariant exists to prevent. `CapabilityRouter.route()` shares one source of truth with `CircuitBreaker` (an `OPEN` breaker is never a routing candidate), which is the concrete mechanism behind §2.5's invariant. The fallback chain inside `dispatch_subtask()` — alternate specialist → generalist degraded worker → structured partial result — is a direct implementation of §4.3's fallback hierarchy and §4.4's graceful-degradation pattern: a customer never sees a hard failure because one of four workers had a bad run; they see a `partial_degraded` status with an explicit, auditable list of which subtasks fell back. The per-hop `idempotency_key` is the content-hash pattern from §4.2/4.4, needed because at-least-once delivery means any dispatch could be retried by infrastructure above this code. Finally, `run_supervisor()`'s parallel fan-out via `ThreadPoolExecutor` — followed by a decoupled citation/synthesis pass that runs against whatever evidence actually came back, degraded or not — mirrors Anthropic's own architecture (§2.2): the system does not block a partial result on every worker succeeding, it synthesizes the best available answer from whatever succeeded and reports the rest as a structured, logged degradation, never a silent gap.

---

## 6. Architectural System Design Scenarios

### Scenario A — Enterprise competitive-intelligence research platform

**Problem statement.** A B2B SaaS company needs an internal research agent that decomposes broad, ambiguous questions ("assess competitor X's pricing strategy shift over the last two quarters") into a multi-source investigation, executes dozens of parallel searches/tool calls, and returns a verified, cited report. This is modeled directly on Anthropic's production system, which outperforms a single-agent baseline by **90.2%** on complex research tasks but at a **4×–15× token cost multiplier** over a single chat interaction (§3.1). The design question: capture the quality win without the cost multiplier becoming unbounded or unauditable.

**Proposed architecture.**

```
Query → LeadResearcher (Supervisor): decomposes into a research strategy,
        persists the plan to external memory immediately (Sec 4.1 --
        protects against 200K-context truncation on long tasks)
                                                    │
                                                    ▼
        Effort governor selects tier: 3-5 subagents spawned in PARALLEL
        via capability-based routing (Sec 2.5), each an isolated-context
        worker running its own search/evaluate/refine loop, returning
        CONDENSED findings, not raw traces
                                                    │
                                                    ▼
        LeadResearcher: adaptive replan if coverage is thin (spawn more
        subagents), then hand off to a dedicated CitationAgent (Sec 2.2)
        for trajectory-aware verification of every citation
                                                    │
                                                    ▼
        Cost-velocity circuit breaker (Sec 3.4/4.3) caps total spend per
        research task at a hard multiple of the planned budget; immutable
        delegation-chain audit log records every subagent spawn decision
        and every citation the CitationAgent validated or rejected (Sec 4.9)
```

Tech choices: Temporal Child Workflows for per-subagent failure isolation and crash recovery (§4.1); a capability router with `maxConcurrency`/`costPerTask`/`avgLatencyMs`/success-rate fields (§2.5) so subagent-model selection can be tiered by subtask complexity (directly addressing the routing-tax critique in §3.2); a per-(provider, model) circuit breaker per worker; an append-only Delegation Record log for every spawn/citation decision (§4.9).

**Trade-off matrix:**

| Dimension | Proposed: orchestrator-worker + dedicated CitationAgent | Single large-context agent, no decomposition | Mesh: 3-8 peer research agents negotiating directly |
|---|---|---|---|
| Cost / 1k runs | Highest raw spend (~15× a single chat interaction, measured, §3.1), but the only pattern shown to hit the 90.2% quality bar; cost-velocity breaker bounds the worst case | Lowest token cost per task, but frequently fails to complete broad tasks at all within one context window | Estimated 15-25×+ — peers re-state shared state to each other with no canonical copy, and the 2-11.8× mesh-vs-sequential token multiplier compounds on top of decomposition overhead `[inferred/secondhand]` |
| Latency | Parallel subagent dispatch cuts wall-clock time by up to 90% vs. serial investigation (measured), despite higher token spend; composed P95 ~16s (§3.3) | Fastest for narrow questions, degrades sharply (or fails outright) as breadth grows | No stable latency floor — O(n²) edges and no circuit-breaker chokepoint mean one bad peer's re-litigation can extend the exchange indefinitely (§3.3) |
| Ops complexity | Highest — external plan-memory persistence, subagent lifecycle management, dedicated verification agent | Lowest — a single agent loop | Highest of all three in practice — no natural supervisor to enforce budgets, dedup, or termination conditions |
| Security / auditability | Strong — CitationAgent verification + immutable delegation-chain audit log gives a defensible "every claim was checked" trail (§4.9) | Weak — no structured verification gate; a hallucinated citation has no independent check | Weakest — no natural chokepoint for policy enforcement (§2.1); a corrupted claim propagates peer-to-peer with nothing positioned to circuit-break it |
| Scalability | Scales to arbitrarily broad questions by adding subagents, bounded only by the cost-velocity ceiling and the supervisor's context-window ceiling at ~4 concurrent workers (§3.5) | Does not scale past what fits in one context window | Does not scale past 3-8 tightly coupled agents before coordination overhead dominates (§2.1) |

**Decision rationale.** The orchestrator-worker pattern with a dedicated trajectory-aware verification agent is selected because it is the only *documented, shipped* architecture at this exact task shape, with measured 90.2% quality improvement and measured 90% latency reduction from parallelization. The single-agent alternative is rejected on a hard capability ceiling, not cost: it cannot decompose past its own context window, which is exactly the failure mode Anthropic's external-plan-memory design exists to solve. The mesh alternative is rejected because it inherits the same 90.2%-quality-driving decomposition benefit only partially (peer negotiation is not the same as clean parallel decomposition) while paying a higher, less-bounded token cost and losing the one structural advantage a supervisor topology provides — a circuit-breaker chokepoint to stop a bad claim before it propagates. The 15× cost multiplier is accepted explicitly as the price of the quality/latency win, made governable — not unbounded — by the cost-velocity circuit breaker and the immutable per-hop audit log.

### Scenario B — Regulated multi-tenant financial-services underwriting pipeline

**Problem statement.** A regulated fintech needs a multi-agent underwriting pipeline where a supervisor delegates document analysis, compliance checks, and risk scoring to specialized worker agents, across multiple customer tenants, with strict data isolation, Zero-Trust agent-to-agent authentication, and a delegation-chain audit trail that regulators can inspect independently of the agents' own self-reported outcomes — informed directly by the Replit database-deletion incident's core lesson (§4.4): an agent's self-report must never be the only signal a human or auditor acts on.

**Proposed architecture.**

```
Underwriting request → Supervisor (per-tenant scoped): classifies
        document type, routes via capability-based router (Sec 2.5) to
        specialist workers -- DocumentExtractionWorker, ComplianceWorker,
        RiskScoringWorker -- each with tool-level RBAC scoped to exactly
        its role (Sec 4.6)
                                                    │
                                                    ▼
        Every worker call crosses the Zero-Trust MCP Gateway: SPIFFE
        SVID + mTLS identity, RBAC evaluation, PII detect-and-redact
        BEFORE the document content reaches any worker's context
        (Sec 4.5-4.7) -- effective permission = intersect(supervisor's
        scope, worker's declared profile), never wider
                                                    │
                                                    ▼
        Each worker executes inside a tenant-scoped microVM (AWS
        Bedrock AgentCore-style, Sec 4.8): dedicated runtime + ENI per
        customer VPC, no long-lived credentials -- borrows the
        request's JWT for its lifetime only
                                                    │
                                                    ▼
        Per-worker circuit breaker (Sec 4.3) isolates a degraded
        ComplianceWorker model from RiskScoringWorker; on partial
        failure, the supervisor returns a structured "manual review
        required" outcome rather than a silently incomplete decision
                                                    │
                                                    ▼
        Delegation Record log (Sec 4.9): every hop's delegator,
        delegatee, scope, and PII-touch flag is written by the
        enforcement layer -- independent of worker self-report --
        giving regulators a chain-of-custody trail for every
        underwriting decision
```

Tech choices: SPIFFE/SPIRE for workload identity and short-lived cert rotation across a fleet of ephemeral workers (§4.5); OPA/Cedar for RBAC policy evaluation keyed on SPIFFE identity (§4.6); Microsoft Presidio-style PII detection at the context-delivery layer, before document text enters any worker's prompt (§4.7); AWS Bedrock AgentCore-style microVM-per-session isolation for tenant boundary enforcement (§4.8), following the same pattern independently converged on by Axonius and Cohere Health.

**Trade-off matrix:**

| Dimension | Proposed: Zero-Trust supervisor-worker + microVM tenant isolation | Shared agent pool, no per-tenant isolation, prompt-level PII rules only | Full mesh: tenant agents negotiate directly with a shared compliance-checking peer |
|---|---|---|---|
| Cost / 1k runs | Moderate-high — microVM-per-session overhead and per-hop RBAC evaluation add fixed cost per underwriting run, but this is the isolation/audit tax, not a multi-agent-decomposition tax | Lowest nominal cost — no isolation infrastructure — but externalizes tail risk of a cross-tenant data leak, which is a regulatory and reputational cost with no cap | High — mesh's estimated 15-25×+ token multiplier (§2.1/6.1 Scenario A) compounds with the compliance-checking peer becoming a de facto shared bottleneck anyway |
| Latency | Read/extraction steps proceed at normal agentic speed; compliance/risk steps add RBAC-evaluation and PII-redaction latency, a small fixed overhead per hop (§4.7) | Fastest — no isolation or redaction overhead, which is precisely the design gap that makes cross-tenant leakage possible | Unbounded — no stable latency floor exists for mesh topologies (§3.3), and a shared compliance peer becomes a serialization point despite the mesh's nominal peer-to-peer design |
| Ops complexity | Highest — requires SPIFFE/SPIRE infrastructure, per-tenant microVM provisioning, and a maintained RBAC policy set per worker role | Lowest — a single shared agent pool with no per-tenant infra | High — mesh coordination has no natural supervisor to own budget, dedup, or RBAC enforcement, pushing complexity into ad hoc peer protocols |
| Security | Strong — cryptographic workload identity, RBAC that narrows (never widens) across delegation, tenant-isolated microVMs with no standing credentials, PII redacted before model context (§4.5-4.8) | Weakest — "don't expose tenant B's data" as a prompt instruction is advisory, not enforced, and the source material is explicit that prompt-level controls have already failed in comparable real incidents (§4.4/§4.6) | Weak — no natural chokepoint for RBAC or PII policy enforcement in an open mesh (§2.1); a leaked or misrouted claim propagates peer-to-peer with nothing positioned to stop it |
| Scalability | Scales per-tenant cleanly — each tenant's worker fleet and microVM boundary is independent, matching the Axonius/Cohere Health production pattern | Scales in raw throughput terms, but every added tenant increases blast radius of the shared, unisolated pool | Does not scale past a handful of tightly coupled peer agents before coordination and compliance-checking bottlenecks dominate (§2.1) |

**Decision rationale.** Zero-Trust, per-tenant microVM-isolated supervisor-worker is selected because it is the pattern two independent production case studies (Axonius, Cohere Health) converged on for regulated/multi-tenant agentic workloads, and because its core property — RBAC and tenant isolation enforced at the infrastructure layer, not by agent behavior — does not depend on any worker "choosing correctly," directly addressing the Replit incident's central lesson that prompt-level controls are not a control. The shared-pool alternative is rejected specifically because it makes cross-tenant data exposure a matter of agent behavior rather than architecture, an unacceptable risk profile in a regulated underwriting context where a single leak is a compliance event, not a quality regression. The mesh alternative is rejected because it does not actually remove the need for a compliance chokepoint — a shared compliance-checking peer re-creates a supervisor-like bottleneck without any of a supervisor's structural enforcement guarantees (RBAC evaluation point, audit log ownership, circuit-breaker chokepoint), while paying mesh's higher token cost and having no bounded latency floor. The moderate-high cost of per-session microVM isolation is accepted as the direct, quantifiable price of tenant isolation and regulatory auditability — a cost category distinct from (and additive to) the multi-agent decomposition cost discussed in Scenario A.

---

> ⚠️ Data gaps carried over from the primary source: no public, audited multi-vendor benchmark quantifies per-subagent model-routing savings at scale (§3.2), so the illustrative routing-tax recovery figure is architect-derived, not vendor-measured; no industry-standard multi-agent task-throughput or composed-availability SLA benchmark exists (§3.4/3.5), so every figure beyond the topology-level SPOF and BrowseComp/LAMaS findings is an inferred design target; none of the agent-specific delegation-chain identity/audit specs (KAIF, PEDIGREE, ACAP, ADCS) has vendor-neutral ratified status as of 2026-08-21 (§4.5/4.9); and the mesh topology has no published P50/P95/P99 latency anchor in the source material at all (§3.3), only a qualitative "highest per-hop token cost, no circuit-breaker chokepoint" characterization.
