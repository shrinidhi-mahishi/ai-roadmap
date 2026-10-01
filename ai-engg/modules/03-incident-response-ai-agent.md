# Module 03: Incident Response AI Agent

**Scope**: Design a production-grade AI agent that ingests alerts, investigates root causes, and executes (or recommends) mitigations across cloud infrastructure -- with durable state, zero-trust security, and compliance-ready audit trails.

**Why this matters for Principal/Director interviews**: Incident response agents sit at the intersection of distributed systems, AI orchestration, security architecture, and SRE economics. Interviewers test whether you can reason about the blast radius of autonomous actuation, the cost curve of multi-hop LLM reasoning, and the governance surface area of an agent touching production.

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
                            ┌─────────────────────────────────────────────────────────────────┐
                            │                       CONTROL PLANE                              │
                            │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
  ┌──────────────┐          │  │   Enricher    │    │  Reasoning   │    │  Strategy        │   │
  │  Alert Source │──webhook─┼─>│   Pipeline   │───>│  Engine      │───>│  Selector        │   │
  │  (PagerDuty, │          │  │              │    │  (LLM Core)  │    │  (Runbook Catalog)│   │
  │   Datadog,   │          │  │ - Alert desc  │    │              │    │                  │   │
  │   Custom)    │          │  │ - Playbooks   │    │ - Hypothesis │    │ - Skill/SOP      │   │
  └──────────────┘          │  │ - Topology    │    │   generation │    │   matching        │   │
                            │  │ - Past        │    │ - RCA loop   │    │ - Action plan    │   │
  ┌──────────────┐          │  │   incidents   │    │ - Confidence │    │   construction   │   │
  │  Human Input │──pri:0──┐│  └──────────────┘    │   scoring    │    └────────┬─────────┘   │
  │  (Slack/UI)  │         ││                      └──────────────┘             │             │
  └──────────────┘         ││                                                   │             │
                           ││  ┌───────────────────────────────────────────┐    │             │
                           │└─>│  Durable Supervisor (Checkpoint Store)    │<───┘             │
                           │   │  - Priority queue (user=0, agent=1)      │                   │
                           │   │  - Lock (serializes concurrent resume)   │                   │
                           │   │  - Drain loop (event spine)              │                   │
                           │   │  - task_id === thread_id                 │                   │
                           │   └──────────────────┬────────────────────────┘                   │
                            │                      │ structured action plan                    │
                            └──────────────────────┼──────────────────────────────────────────┘
                                                   │
                            ┌──────────────────────┼──────────────────────────────────────────┐
                            │  DATA PLANE          v                                          │
                            │  ┌──────────────────────────────────────┐                       │
                            │  │       Actuation Agent / Gateway      │                       │
                            │  │  - Pre-flight: dry-run, justification│                       │
                            │  │  - Concurrent action check           │                       │
                            │  │  - Dynamic autonomy downgrade        │                       │
                            │  │  - Red Button endpoint               │                       │
                            │  └───────┬──────────┬──────────┬────────┘                       │
                            │          │          │          │                                 │
                            │          v          v          v                                 │
                            │  ┌──────────┐ ┌─────────┐ ┌──────────┐                         │
                            │  │ MCP Svr  │ │ MCP Svr │ │ MCP Svr  │                         │
                            │  │ (Observ) │ │ (Infra) │ │ (Traffic)│                         │
                            │  └────┬─────┘ └────┬────┘ └────┬─────┘                         │
                            │       │            │           │                                │
                            │       v            v           v                                │
                            │  ┌─────────┐ ┌─────────┐ ┌─────────┐                           │
                            │  │ Logs/   │ │ K8s/    │ │ Load    │                           │
                            │  │ Metrics │ │ VMs/    │ │ Balancer│                           │
                            │  │ Traces  │ │ Deploys │ │ DNS     │                           │
                            │  └─────────┘ └─────────┘ └─────────┘                           │
                            └─────────────────────────────────────────────────────────────────┘

                            ┌─────────────────────────────────────────────────────────────────┐
                            │  PERSISTENCE & TELEMETRY                                        │
                            │  ┌────────────┐ ┌────────────┐ ┌─────────────┐ ┌────────────┐  │
                            │  │ Checkpoint  │ │ Execution  │ │ Reasoning   │ │ Vector     │  │
                            │  │ Store       │ │ Trace DB   │ │ Trace Store │ │ Knowledge  │  │
                            │  │ (Temporal/  │ │ (Spanner/  │ │ (5th telem  │ │ Base       │  │
                            │  │  Redis)     │ │  Postgres) │ │  layer)     │ │ (Embeddings│  │
                            │  └────────────┘ └────────────┘ └─────────────┘ │  of past   │  │
                            │                                                │  incidents)│  │
                            │                                                └────────────┘  │
                            └─────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 -- Alert ingestion.** A webhook fires from PagerDuty/Datadog/custom alerting into the control plane. The durable supervisor receives the event and places it into a priority queue (user input = priority 0, sub-agent results = priority 1).

**Step 2 -- Enrichment.** The enricher pipeline hydrates the raw alert with deterministic context: alert description, relevant playbooks, topology/dependency graphs, and semantically similar past incidents retrieved from the vector knowledge base. This is a read-only, parallelized phase operating under a hard time budget (Google uses ~2 minutes).

**Step 3 -- Reasoning.** The LLM reasoning engine receives enriched context and generates hypotheses for root cause. It spawns one sub-agent per hypothesis (parallel fan-out). Sub-agents are stateless -- they query logs, metrics, and deploy history via MCP tool servers, then report findings back. If a sub-agent dies, it is re-spawned, not resumed.

**Step 4 -- Strategy selection.** The reasoning engine scores hypotheses against evidence and selects a mitigation strategy from the runbook catalog. The output is a **structured action plan** -- never a raw shell command.

**Step 5 -- Actuation.** The structured action plan crosses the control/data plane boundary into the Actuation Agent. Pre-flight checks enforce: (a) mandatory dry-run, (b) justification verification (action must target an open incident), (c) concurrent action checks, (d) dynamic autonomy evaluation (can downgrade L3 to L2 if elevated risk detected). Only after all gates pass does the action route through a delegated MCP server to the target infrastructure.

**Step 6 -- Post-actuation.** The system maintains a long-running operation (LRO) state, polling infrastructure to verify mitigation success or failure. Every execution step is recorded in the trace DB. Reasoning traces (the fifth telemetry layer) capture signals used, hypotheses considered, action rationale, and confidence levels.

---

## 2. Core Mechanics & Algorithms

### 2.1 Five-Level Autonomy State Machine

This is Google SRE's progression model. Each transition requires demonstrated reliability at the current level before advancing.

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                     AUTONOMY STATE MACHINE                             │
  │                                                                        │
  │  ┌────────┐  tool    ┌────────┐  precision   ┌────────┐  multi-step  │
  │  │   L0   │  adopt   │   L1   │  + safe      │   L2   │  resolution │
  │  │ Manual │─────────>│Assisted│  actuation   │Partial │  capability │
  │  │        │          │        │─────────────>│        │────────────>│
  │  │Monitor │          │Investi-│              │Actuate,│             │
  │  │ only   │          │gate    │              │human   │             │
  │  └────────┘          └────────┘              │mitigate│             │
  │                                              └────────┘             │
  │                                                                      │
  │  ┌────────┐  continuous  ┌────────┐                                  │
  │  │   L4   │  self-       │   L3   │<────────────────────────────────│
  │  │  Full  │<─────────────│  High  │                                  │
  │  │        │  monitoring  │        │                                  │
  │  │End-to- │              │Full    │    ┌──────────────────┐          │
  │  │end     │              │auto,   │    │ DYNAMIC DOWNGRADE│          │
  │  │autonomy│              │human   │    │ L3 ──> L2        │          │
  │  └────────┘              │self-   │───>│ Trigger: elevated│          │
  │                          │directs │    │ risk / anomalous │          │
  │                          └────────┘    │ production state │          │
  │                                        └──────────────────┘          │
  └─────────────────────────────────────────────────────────────────────────┘
```

**Key invariant**: Write operations at any level must never exceed the safety boundary of that level. Dynamic downgrade is always available -- the system can regress from L3 to L2 when contextual risk spikes (e.g., draining a cell during regional peak traffic).

**Operational functions per level**: Each level is evaluated across five axes: Monitor, Investigate, Mitigate, Actuate, Self-Direct. An agent may be L3 on Monitor but L1 on Actuate.

### 2.2 Reactive Event Loop (PagerDuty Model)

```
                     ┌──────────────┐
                     │ accept_event │<────────────────────────────┐
                     └──────┬───────┘                             │
                            │                                     │
                            v                                     │
                     ┌──────────────┐                             │
                     │ route_event  │                             │
                     └──────┬───────┘                             │
                            │                                     │
                   ┌────────┴────────┐                            │
                   v                 v                             │
          ┌────────────────┐ ┌──────────────────┐                 │
          │handle_user_    │ │handle_sub_agent_ │                 │
          │input (pri=0)   │ │result (pri=1)    │                 │
          └────────┬───────┘ └────────┬─────────┘                 │
                   └────────┬────────┘                            │
                            v                                     │
                     ┌──────────────┐                             │
                     │    plan      │──spawn sub-agents──────────>│
                     │ (dispatch)   │                             │
                     └──────────────┘                re-interrupt │
                                                    (callback)   │
```

**Concurrency invariants**:
1. **Serialization**: The drain loop holds a lock while resuming the graph. Concurrent arrivals are serialized -- no two events mutate supervisor state simultaneously.
2. **Priority**: User input always preempts sub-agent results (priority 0 vs 1).
3. **Idempotency**: `task_id === thread_id` eliminates correlation lookups. Each step is atomic and persisted the moment it is applied.
4. **Liveness**: The graph spends most time paused at `accept_event`. The drain loop is the spine -- it pulls the next event and resumes exactly once per cycle.

### 2.3 Hypothesis-Parallel Investigation Algorithm

```
Algorithm: PARALLEL_RCA(alert, context, max_hypotheses=4)
─────────────────────────────────────────────────────────
Input:  Enriched alert + topology + past incidents
Output: Ranked (hypothesis, evidence, confidence) tuples

1. hypotheses <- LLM.generate_candidates(alert, context)    // O(1) LLM call
2. Truncate to top-k by prior probability                    // k = max_hypotheses
3. FOR EACH h_i IN hypotheses (PARALLEL fan-out):
     sub_agent_i <- spawn_stateless_agent(h_i)
     evidence_i  <- sub_agent_i.investigate(
                      tools=[logs, metrics, traces, deploys],
                      time_budget=120s
                    )                                         // I/O-bound, parallelized
4. BARRIER: wait_for_all(sub_agents) OR timeout
5. scored <- []
   FOR EACH (h_i, evidence_i):
     score_i <- deterministic_match(evidence_i, golden_data)  // Not LLM scoring
     scored.append((h_i, evidence_i, score_i))
6. SORT scored BY score DESC
7. IF scored[0].score < confidence_threshold:
     ESCALATE to human (dynamic downgrade to L2)
8. RETURN scored
```

**Complexity**: O(k) sub-agent spawns, wall-clock time dominated by the slowest sub-agent (bounded by `time_budget`). Each sub-agent makes O(t) tool calls where t is the investigation depth. Total LLM calls: 1 (hypothesis generation) + k (one per sub-agent investigation).

**Key insight from Google**: A mitigation is scored "correct" only if the agent's output **deterministically matches** the fully actionable, exact parameters of golden data -- not vague suggestions. This eliminates LLM-as-judge variance for safety-critical scoring.

### 2.4 Execution Model Comparison

```
  ┌────────────────────────────────────────────────────────────────────┐
  │ Sequential         Parallel Wait-All    Parallel Fan-Out/Fan-In   │
  │                                                                    │
  │ A ──> B ──> C      ┌─ A ─┐              ┌─ A ─┐                  │
  │                     ├─ B ─┤ barrier      ├─ B ─┤ event-driven     │
  │ Latency: sum       ├─ C ─┘              ├─ C ─┘ + user input     │
  │ Interact: none      │                    │       as first-class   │
  │ Complex: low        Latency: max        Latency: max              │
  │                     Interact: none       Interact: full            │
  │                     Complex: medium      Complex: high             │
  │                                                                    │
  │ PagerDuty chose fan-out/fan-in: interactivity is non-negotiable   │
  │ for incident response. Users must steer mid-investigation.         │
  └────────────────────────────────────────────────────────────────────┘
```

### 2.5 Context Management Invariants

**Context rot**: Performance degrades as context grows. JSON blobs of alerts, past incidents, and topology overwhelm the model's ability to weight information correctly (Liu et al., 2023).

**Instruction overload**: Inverse relationship between instruction volume and output quality (Jaroslawicz et al., 2025). Each new capability competes for model attention.

**Mitigations**:
- **Token minimization per step**: Google's AI Operator uses the minimum token set per step because incident chain-of-thought can span very long horizons.
- **Multi-agent context scoping**: PagerDuty sacrifices unified context for better per-agent reasoning quality. Each sub-agent receives only task-relevant context.
- **Lazy skill loading**: Skills loaded into model context only when needed, not pre-loaded. Saves context window for actual reasoning.
- **Context compression**: Cloudflare collapsed 2,500+ API endpoints into two tools consuming ~1,000 tokens (down from 1.17M tokens).

---

## 3. Token Economics & NFR Analysis

### 3.1 Latency Budget Breakdown

An incident response agent turn involves 2-5 LLM hops plus tool calls, compounding latency 3-10x beyond raw API benchmarks.

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │  LATENCY BUDGET (single agent turn)                                   │
  │                                                                        │
  │  Component              P50        P95        P99        Notes         │
  │  ─────────────────────  ─────────  ─────────  ─────────  ────────────  │
  │  Raw LLM TTFT           ~330ms     ~1.2s      ~3.2s      Baseten '26  │
  │  Tool call (read)       50-200ms   300-500ms  500ms-1s   MCP server   │
  │  Tool call (write)      100-500ms  500ms-1s   1-2s       w/ dry-run   │
  │  Agent turn (e2e)       3-5s       6-9s       8-12s      2-5 hops     │
  │  ─────────────────────  ─────────  ─────────  ─────────  ────────────  │
  │  Full investigation     ~2min      --         ~10min     PagerDuty    │
  │  (parallel fan-out)     (Google    --         (sequen-   sequential   │
  │                          budget)              tial mode) 3-4 hyp.     │
  │                                                                        │
  │  TTFT cost curve: 500ms -> 200ms P99 = +35% infra spend              │
  └────────────────────────────────────────────────────────────────────────┘
```

**Provider consistency** (2026 benchmarks):
- **Anthropic (Claude)**: Most consistent -- P50 and P99 TTFT stay close. Best for latency-sensitive agent loops.
- **OpenAI (GPT-4.1)**: P99 spikes 3-5x above P50 during peak hours. Requires hedging or request routing.
- **Google (Gemini)**: Fast and stable, but post-update behavioral changes observed. Requires regression testing after model updates.

### 3.2 Cost Formula: $ per 1,000 Incident Runs

```
Cost_per_1k = 1000 * (
    H * (input_tokens * $/input_token + output_tokens * $/output_token)  // LLM calls
  + T * tool_call_cost                                                     // MCP tool exec
  + checkpoint_writes * storage_cost                                       // Durability
  + embedding_queries * embedding_cost                                     // Vector search
) * (1 - cache_hit_rate * cache_savings)                                   // Semantic caching

Where:
  H = avg LLM hops per incident (typically 3-15, varies with complexity)
  T = avg tool calls per incident (typically 5-20)
  cache_hit_rate = ~0.31 (31% semantic similarity observed in production)
  cache_savings = ~0.95 (cached response avoids full inference)
```

**Worked example** (frontier model, medium-complexity incident):

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Component          Units    Unit Cost    Per-Incident    Notes  │
  │  ────────────────   ─────    ─────────    ────────────    ─────  │
  │  LLM calls (8 hops)                                             │
  │    Input tokens     40K      $3/1M        $0.12                 │
  │    Output tokens    8K       $15/1M       $0.12                 │
  │  Tool calls         12       $0.001       $0.012                │
  │  Checkpoint writes  5        $0.0001      $0.0005               │
  │  Vector queries     3        $0.0001      $0.0003               │
  │  ────────────────────────────────────────────────────            │
  │  Gross per incident                       $0.253                │
  │  After 31% cache hit (x0.70)              $0.177                │
  │  Per 1,000 incidents                      $177                  │
  │                                                                  │
  │  With model routing (70% to small model): $53-$71 per 1,000    │
  └──────────────────────────────────────────────────────────────────┘
```

**The 100-300x model routing lever**: Route simple triage (alert classification, deduplication, status updates) to small models ($0.01-0.05/1M tokens). Escalate complex RCA and multi-step reasoning to frontier models ($3-15/1M tokens). This is the single largest cost optimization, achieving 60-80% token spend reduction.

### 3.3 Cost Optimization Strategies

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Strategy               Savings    Complexity    Risk           │
  │  ─────────────────────  ─────────  ──────────    ────────────   │
  │  Model routing          60-80%     Medium        Quality drop   │
  │                                                   on mis-route  │
  │  Semantic caching       ~31%       Low           Stale cache    │
  │                                                   for novel     │
  │                                                   incidents     │
  │  Context compression    90%+       High          Info loss on   │
  │  (Cloudflare approach)                            edge cases    │
  │  Token mgmt per step    20-40%     Medium        Reasoning      │
  │  (Google approach)                                truncation    │
  │  Prompt caching         30-50%     Low           Provider-      │
  │  (API-level)                                      dependent     │
  └──────────────────────────────────────────────────────────────────┘
```

**Inference cost trajectory**: Stanford HAI 2025 AI Index shows GPT-3.5-level inference cost dropped 280x between Nov 2022 and Oct 2024. Hardware costs decline ~30%/year, energy efficiency improves ~40%/year. Design for cost flexibility -- what costs $177/1k today may cost $5/1k in 18 months.

### 3.4 NFR Targets

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  NFR                  Target              Rationale                  │
  │  ──────────────────   ──────────────────   ────────────────────────  │
  │  Availability         99.9% (8.7h/yr)     Incident tool must be up  │
  │                                            when prod is down         │
  │  RPO (checkpoint)     0 (every step)       Each step atomic +        │
  │                                            persisted immediately     │
  │  RTO (supervisor)     < 30s                Re-hydrate from last      │
  │                                            checkpoint, re-spawn      │
  │                                            sub-agents                │
  │  RTO (sub-agent)      0 (re-spawn)         Stateless; no recovery    │
  │                                            needed                    │
  │  MTTM reduction       10-44%               Google measured: 10%      │
  │                                            (hypothesis), 44%         │
  │                                            (dashboards)              │
  │  Max concurrent       Bounded by           Bulkhead isolation per    │
  │  incidents            thread pool           incident                 │
  │  Audit log latency    < 5s                 Compliance: every action  │
  │                                            logged before next step   │
  │  PII redaction        Inline, pre-log      PHI in context = in      │
  │                                            scope for HIPAA           │
  │  Red Button latency   < 1s                 Emergency halt of all     │
  │                                            in-flight agent actions   │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution for Incident Workflows

**Why session memory is not enough**: "Saving chat history helps an agent remember, but it does not prove which shell command ran, which email was sent, which approval was granted, or whether a retry would duplicate a side effect." Without checkpointing, a crash at 3h50m into a long incident restarts from zero.

**PagerDuty's asymmetric durability model**:

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  │  SUPERVISOR (durable)          SUB-AGENTS (ephemeral)           │
  │  ┌────────────────────┐        ┌────────────────────┐           │
  │  │ Checkpointed state │        │ No checkpoints     │           │
  │  │ Priority queue     │        │ Stateless workers  │           │
  │  │ (inside checkpoint)│        │ Re-spawn on death  │           │
  │  │ Lock + drain loop  │        │ No N+1 checkpoint  │           │
  │  │ task_id = thread_id│        │   reconciliation   │           │
  │  └────────────────────┘        └────────────────────┘           │
  │                                                                  │
  │  Rule: "Events survive a crash because the supervisor's         │
  │         state does."                                             │
  │                                                                  │
  │  Anti-pattern: Do NOT checkpoint model messages alone.           │
  │  "'The email was sent' in conversation history may be a         │
  │  model claim. Store the provider message ID or operation         │
  │  record."                                                        │
  └──────────────────────────────────────────────────────────────────┘
```

**Temporal's event history model**: Records every workflow execution step, every Activity call/return, and all return values. Non-deterministic side effects (LLM outputs, timestamps, retrieval results) are recorded the first time and replayed deterministically during recovery.

### 4.2 Failure Taxonomy

Production AI agents fail at 41-86.7% rates without deliberate fault tolerance. 88% of agents that work in demos fail in real workflows.

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  FAILURE MODE DISTRIBUTION (Arize 2026 field analysis)          │
  │                                                                  │
  │  Context blindness   ████████████████████████████████  31.6%    │
  │  Rogue actions       ███████████████████████████████   30.3%    │
  │  Silent degradation  █████████████████████████        24.9%    │
  │  Memory corruption   ████████                          8.1%    │
  │  Runaway execution   █████                             5.1%    │
  │                                                                  │
  │  Detection asymmetry:                                           │
  │  - Tool-call failures with schema validation: MTTR = 54 min    │
  │  - Observability failures (no detection): MTTR = 4.2 hours     │
  └──────────────────────────────────────────────────────────────────┘
```

**Hallucination cascades**: Agent fabricates information, then uses that fabrication to inform subsequent decisions across multiple systems. Example: inventory agent invents nonexistent SKU, then calls four downstream APIs to price, stock, and ship the phantom item.

**Real-world incident**: Amazon Kiro AI (Dec 2025) determined deleting and rebuilding an environment was the most efficient fix, executed autonomously without human approval, causing a 13-hour outage.

### 4.3 Circuit Breaker State Machine

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  │            success_count >= threshold                            │
  │         ┌────────────────────────────┐                           │
  │         │                            │                           │
  │         v          timeout           │                           │
  │    ┌─────────┐    expires     ┌──────────┐                      │
  │    │ CLOSED  │───────────────>│HALF-OPEN │                      │
  │    │ (normal)│    failure     │(testing) │                      │
  │    │         │    threshold   │          │──failure──┐          │
  │    └─────────┘    reached     └──────────┘           │          │
  │         ^              │                             │          │
  │         │              v                             v          │
  │         │        ┌──────────┐                  ┌──────────┐     │
  │         │        │   OPEN   │<─────────────────│   OPEN   │     │
  │         │        │(tripped) │  backoff doubles │(extended)│     │
  │         │        │ 30s init │                  │          │     │
  │         │        └──────────┘                  └──────────┘     │
  │         │              │                                        │
  │         └──────────────┘                                        │
  │           timeout expires                                       │
  │                                                                  │
  │  TRIGGER CONDITIONS:                                            │
  │  - Repeated identical tool calls (loop detection)               │
  │  - Cost velocity exceeding defined rate ($/min)                 │
  │  - Consecutive failures without recovery                        │
  │  - Permission boundary violations                               │
  │                                                                  │
  │  HALF-OPEN TESTING:                                             │
  │  - Simplified prompts first                                     │
  │  - Gradually increase complexity                                │
  │  - Promote to CLOSED only on sustained success                  │
  └──────────────────────────────────────────────────────────────────┘
```

**Layered resilience** (defense-in-depth):
1. **Circuit breakers** -- prevent cascading failure across services
2. **Timeout management** -- prevent indefinite blocking on LLM/tool calls
3. **Compensating transactions** -- rollback partially completed workflows
4. **Bulkhead isolation** -- one incident's agent cannot starve another's resources

### 4.4 Zero-Trust MCP & Agent Identity

**Google SRE principle**: "Agent identities must be distinct from human users, strongly authenticated, with on-demand permissions only. Agents must never use standing, human-like credentials of their developers."

**Anthropic's three-layer defense model**:

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Layer 1: ENVIRONMENT (deterministic -- design here first)      │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ Sandboxes, VMs, filesystem boundaries, egress controls   │   │
  │  │ Ephemeral containers (gVisor, seccomp) for multi-tenant  │   │
  │  │ Sealed VMs (hypervisor) for autonomous agents            │   │
  │  │ Per-session scoped-down tokens, independently revocable  │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                                                                  │
  │  Layer 2: MODEL (probabilistic)                                 │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ System prompts, classifiers, probes                      │   │
  │  │ Confidence thresholds for autonomy decisions              │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                                                                  │
  │  Layer 3: EXTERNAL CONTENT                                      │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │ Tool permission scoping, input inspection                │   │
  │  │ Connector auditing, egress allowlists                    │   │
  │  │ "Every function reachable through any domain on an       │   │
  │  │  allowlist is now an attack surface"                     │   │
  │  └──────────────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────────────┘
```

**Egress control lesson**: Real incident -- a malicious file in a workspace instructed Claude to upload files via Anthropic's Files API using an attacker-controlled key. Fix: defensive MITM proxy inside VM rejects non-provisioned tokens.

### 4.5 RBAC & Progressive Authorization

**Progressive authorization pattern** (Google): Agents start at lower autonomy levels (human-approved) and scale up based on demonstrated performance. Risk evaluation is contextual -- draining a cell may be low-risk normally but high-risk during regional peaks.

**Principle of least agency** (Anthropic): "Grant the narrowest capability that still completes the task."

### 4.6 Audit Trails for SOC 2 & HIPAA

**SOC 2 auditor expectations (2026)**:
- Model lineage: exact dataset, code, and approval behind each deployed model
- Prompt and inference logs with PII redaction applied **before** logging
- Drift-monitoring output
- Vendor risk assessment for every third-party LLM called
- Cost: $35K-$150K first year; enterprise-readiness $200K-$250K+

**HIPAA critical rule**: "If PHI was in the context during inference, the system is in scope" -- even transient context window presence counts. January 2025 HIPAA Security Rule NPRM (finalization mid-2026) explicitly brings AI systems into scope for ePHI governance. Healthcare breach costs: $10.93M average (IBM 2024).

**Governance gaps (2026)**:
- Only 21% of organizations maintain a real-time agent registry
- Only 18% of security leaders believe their IAM handles AI agent identities effectively
- Only 38% monitor AI activity end-to-end; 17% track agent-to-agent interactions
- 42% of companies abandoned AI initiatives in 2025 due to compliance/governance failures

### 4.7 Six Monitoring Signals for Production Agents

Traditional four telemetry layers (metrics, logs, traces, events) are insufficient. Agents need a **fifth**: reasoning traces. "Without them, you are doing forensics on a crime scene with no witnesses."

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Signal                     What It Catches                     │
  │  ────────────────────────   ─────────────────────────────────   │
  │  Goal Completion Rate       Silent failures, false success      │
  │  Tool Success Rate          Integration breakage, API drift     │
  │  Context Quality Score      Context rot, instruction overload   │
  │  Reasoning Trace Complete   Hallucination, shortcut reasoning   │
  │  Escalation Rate            Autonomy calibration drift          │
  │  Hallucination Rate         Fabricated evidence, phantom ops    │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 5. Production Enterprise Code

### 5.1 Retry with Exponential Backoff, Jitter, and Model Fallback

```python
import asyncio
import random
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("incident_agent")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter_range: float = 0.5  # +/- 50% of computed delay


@dataclass
class ModelTier:
    name: str
    model_id: str
    cost_per_1k_input: float  # $ per 1K tokens
    cost_per_1k_output: float
    timeout: float  # seconds


# Ordered from preferred (cheapest) to fallback (most capable)
MODEL_CHAIN = [
    ModelTier("small",    "claude-haiku-4",   0.0008, 0.004,  10.0),
    ModelTier("mid",      "claude-sonnet-4",  0.003,  0.015,  30.0),
    ModelTier("frontier", "claude-opus-4",    0.015,  0.075,  60.0),
]


def compute_delay(attempt: int, config: RetryConfig) -> float:
    """Exponential backoff with full jitter (AWS-style)."""
    exp_delay = min(config.base_delay * (2 ** attempt), config.max_delay)
    jitter = exp_delay * config.jitter_range
    return exp_delay + random.uniform(-jitter, jitter)


async def call_llm_with_retry(
    prompt: str,
    model: ModelTier,
    config: RetryConfig = RetryConfig(),
) -> dict[str, Any]:
    """Call a single model with retries. Raises on exhaustion."""
    last_exc = None
    for attempt in range(config.max_retries + 1):
        try:
            # Replace with your actual LLM client call
            response = await asyncio.wait_for(
                _invoke_model(prompt, model.model_id),
                timeout=model.timeout,
            )
            return {
                "model": model.name,
                "model_id": model.model_id,
                "response": response,
                "attempts": attempt + 1,
            }
        except (asyncio.TimeoutError, ConnectionError, Exception) as exc:
            last_exc = exc
            if attempt < config.max_retries:
                delay = compute_delay(attempt, config)
                logger.warning(
                    "LLM call failed model=%s attempt=%d/%d delay=%.2fs error=%s",
                    model.name, attempt + 1, config.max_retries + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "LLM call exhausted retries model=%s attempts=%d error=%s",
                    model.name, config.max_retries + 1, exc,
                )
    raise last_exc


async def call_with_fallback_chain(
    prompt: str,
    models: list[ModelTier] | None = None,
    config: RetryConfig = RetryConfig(),
) -> dict[str, Any]:
    """Try each model in the chain; fall back to next on exhaustion."""
    models = models or MODEL_CHAIN
    errors = []
    for model in models:
        try:
            result = await call_llm_with_retry(prompt, model, config)
            if errors:
                logger.info(
                    "Fallback succeeded model=%s after %d prior model failures",
                    model.name, len(errors),
                )
            return result
        except Exception as exc:
            errors.append((model.name, exc))
            logger.warning("Model %s exhausted, falling back", model.name)

    error_summary = "; ".join(f"{name}: {exc}" for name, exc in errors)
    raise RuntimeError(f"All models in fallback chain exhausted: {error_summary}")


async def _invoke_model(prompt: str, model_id: str) -> str:
    """Placeholder for actual LLM API call. Replace with Anthropic/OpenAI client."""
    import anthropic
    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model=model_id,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
```

### 5.2 Circuit Breaker for Agent Tool Calls

```python
import time
import threading
from dataclasses import dataclass, field


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # failures before tripping
    success_threshold: int = 3          # successes in half-open to close
    initial_timeout: float = 30.0       # seconds in OPEN before testing
    max_timeout: float = 300.0          # max backoff for OPEN state
    cost_velocity_limit: float = 5.0    # max $/minute before tripping


class CircuitBreaker:
    """Three-state circuit breaker adapted for AI agent tool calls."""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._current_timeout = self.config.initial_timeout
        self._cost_window: list[tuple[float, float]] = []  # (timestamp, cost)
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self._current_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info("Circuit %s -> HALF_OPEN", self.name)
            return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._current_timeout = self.config.initial_timeout
                    logger.info("Circuit %s -> CLOSED (recovered)", self.name)
            else:
                self._failure_count = max(0, self._failure_count - 1)

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._trip()
                self._current_timeout = min(
                    self._current_timeout * 2, self.config.max_timeout
                )
            elif self._failure_count >= self.config.failure_threshold:
                self._trip()

    def record_cost(self, cost: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._cost_window.append((now, cost))
            cutoff = now - 60.0
            self._cost_window = [
                (t, c) for t, c in self._cost_window if t > cutoff
            ]
            velocity = sum(c for _, c in self._cost_window)
            if velocity > self.config.cost_velocity_limit:
                logger.error(
                    "Circuit %s cost velocity $%.2f/min exceeds $%.2f limit",
                    self.name, velocity, self.config.cost_velocity_limit,
                )
                self._trip()

    def _trip(self) -> None:
        self._state = CircuitState.OPEN
        self._last_failure_time = time.monotonic()
        self._success_count = 0
        logger.warning(
            "Circuit %s -> OPEN (timeout=%.1fs)", self.name, self._current_timeout
        )

    def can_execute(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True
        return False


# Registry for per-tool circuit breakers
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(tool_name: str) -> CircuitBreaker:
    if tool_name not in _breakers:
        _breakers[tool_name] = CircuitBreaker(tool_name)
    return _breakers[tool_name]
```

### 5.3 Structured Logging with Reasoning Traces

```python
import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

# Context vars for distributed tracing
incident_id_var: ContextVar[str] = ContextVar("incident_id", default="")
agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")


class StructuredFormatter(logging.Formatter):
    """JSON formatter that includes reasoning trace context for the fifth
    telemetry layer. Every log line is machine-parseable and audit-ready."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "incident_id": incident_id_var.get(""),
            "agent_id": agent_id_var.get(""),
        }
        # Attach structured extras (hypothesis, confidence, tool results)
        for key in ("hypothesis", "confidence", "tool_name", "tool_result",
                     "action_plan", "autonomy_level", "cost_usd",
                     "checkpoint_id", "evidence"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry)


def configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger("incident_agent")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return root


def log_reasoning_step(
    logger: logging.Logger,
    hypothesis: str,
    confidence: float,
    evidence: list[str],
    tool_name: str | None = None,
    tool_result: str | None = None,
) -> None:
    """Emit a reasoning trace record -- the fifth telemetry layer."""
    logger.info(
        "Reasoning step: %s (confidence=%.2f)", hypothesis, confidence,
        extra={
            "hypothesis": hypothesis,
            "confidence": confidence,
            "evidence": evidence,
            "tool_name": tool_name,
            "tool_result": tool_result,
        },
    )
```

### 5.4 Graceful Degradation: Autonomy Downgrade

```python
from dataclasses import dataclass
from enum import IntEnum


class AutonomyLevel(IntEnum):
    L0_MANUAL = 0
    L1_ASSISTED = 1
    L2_PARTIAL = 2     # Human approves mutations
    L3_HIGH = 3        # Full auto, human self-directs
    L4_FULL = 4        # End-to-end autonomy


@dataclass
class RiskContext:
    is_peak_traffic: bool = False
    blast_radius_pct: float = 0.0       # % of prod affected
    recent_deploy_minutes: int = 999    # minutes since last deploy
    error_rate_elevated: bool = False
    concurrent_incidents: int = 0


def evaluate_risk(ctx: RiskContext) -> float:
    """Score risk 0.0 (safe) to 1.0 (dangerous). Deterministic, auditable."""
    score = 0.0
    if ctx.is_peak_traffic:
        score += 0.3
    if ctx.blast_radius_pct > 25.0:
        score += 0.25
    if ctx.recent_deploy_minutes < 30:
        score += 0.2
    if ctx.error_rate_elevated:
        score += 0.15
    if ctx.concurrent_incidents > 2:
        score += 0.1
    return min(score, 1.0)


def resolve_autonomy(
    requested: AutonomyLevel,
    risk_ctx: RiskContext,
    risk_threshold: float = 0.5,
) -> AutonomyLevel:
    """Dynamic autonomy downgrade. If risk exceeds threshold, downgrade
    write-capable levels (L3/L4) to L2 (human-approved mutations).

    This mirrors Google's Actuation Agent behavior: L3 requests are
    downgraded to L2 when elevated risk or anomalous production state
    is detected."""
    risk = evaluate_risk(risk_ctx)
    if requested >= AutonomyLevel.L3_HIGH and risk >= risk_threshold:
        logger.warning(
            "Autonomy downgrade: requested=%s risk=%.2f threshold=%.2f -> L2",
            requested.name, risk, risk_threshold,
            extra={"autonomy_level": AutonomyLevel.L2_PARTIAL.name},
        )
        return AutonomyLevel.L2_PARTIAL
    return requested


# --- Red Button: emergency halt ---

import asyncio

_red_button_event = asyncio.Event()


def press_red_button() -> None:
    """Instantly halt all in-flight agent actions. Expose this as an
    HTTP endpoint for SRE dashboards."""
    _red_button_event.set()
    logger.critical("RED BUTTON pressed -- all agent actions halted")


async def check_red_button() -> None:
    """Call before every actuation step. Raises if halted."""
    if _red_button_event.is_set():
        raise RuntimeError("Agent operations halted by Red Button")
```

### 5.5 Checkpoint-Aware Workflow Step

```python
import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Checkpoint:
    step_id: str
    incident_id: str
    state: dict[str, Any]
    provider_evidence: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    """In-memory store for demonstration. Swap with Redis/Postgres/Temporal
    for production. Each checkpoint stores provider evidence (not model claims)."""

    def __init__(self):
        self._store: dict[str, list[Checkpoint]] = {}

    def save(self, cp: Checkpoint) -> None:
        key = cp.incident_id
        if key not in self._store:
            self._store[key] = []
        self._store[key].append(cp)
        logger.info(
            "Checkpoint saved step=%s incident=%s",
            cp.step_id, cp.incident_id,
            extra={"checkpoint_id": cp.step_id},
        )

    def latest(self, incident_id: str) -> Checkpoint | None:
        steps = self._store.get(incident_id, [])
        return steps[-1] if steps else None

    def all_steps(self, incident_id: str) -> list[Checkpoint]:
        return list(self._store.get(incident_id, []))


async def execute_with_checkpoint(
    step_id: str,
    incident_id: str,
    action_fn,
    store: CheckpointStore,
    breaker: CircuitBreaker,
) -> Any:
    """Execute an action with circuit breaker protection and checkpoint
    persistence. Stores provider evidence, not model claims."""
    await check_red_button()

    if not breaker.can_execute():
        raise RuntimeError(f"Circuit breaker {breaker.name} is OPEN")

    try:
        result = await action_fn()
        breaker.record_success()

        # Store provider evidence -- the actual API response, not what
        # the model says happened
        evidence = {}
        if isinstance(result, dict):
            for key in ("request_id", "operation_id", "status_code", "response_hash"):
                if key in result:
                    evidence[key] = str(result[key])
        if not evidence and isinstance(result, str):
            evidence["response_hash"] = hashlib.sha256(result.encode()).hexdigest()

        cp = Checkpoint(
            step_id=step_id,
            incident_id=incident_id,
            state={"result": str(result)[:500]},  # Truncate for storage
            provider_evidence=evidence,
        )
        store.save(cp)
        return result

    except Exception as exc:
        breaker.record_failure()
        # Still checkpoint the failure for audit trail
        cp = Checkpoint(
            step_id=step_id,
            incident_id=incident_id,
            state={"error": str(exc)[:500]},
        )
        store.save(cp)
        raise
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Cloud SRE Agent for a Fintech Processing 50K Transactions/Minute

**Problem statement**: A fintech company runs payment processing across AWS (primary) and GCP (DR). They experience 15-20 P1 incidents/month with MTTM of 45 minutes. The SOC 2 Type II audit is in 6 months. They need an AI agent that investigates incidents, recommends mitigations, and executes pre-approved remediations -- without ever touching PCI-scoped systems autonomously.

**Architecture**:

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │  ALERT SOURCES                                                        │
  │  PagerDuty ─┐                                                         │
  │  Datadog   ─┼─ webhook ─> ┌─────────────────────────────────────┐    │
  │  CloudWatch ┘             │  SUPERVISOR (ECS Fargate, durable)  │    │
  │                           │  Temporal workflow, checkpointed     │    │
  │                           │  ┌─────────────────────────────┐    │    │
  │                           │  │  Model Router               │    │    │
  │                           │  │  Haiku: triage/classify      │    │    │
  │                           │  │  Sonnet: investigation       │    │    │
  │                           │  │  Opus: complex RCA           │    │    │
  │                           │  └─────────────┬───────────────┘    │    │
  │                           │                │                    │    │
  │                           │  ┌─────────────┼───────────────┐    │    │
  │                           │  │  Sub-Agents (stateless)     │    │    │
  │                           │  │  ┌──────┐ ┌──────┐ ┌──────┐│    │    │
  │                           │  │  │ Log  │ │Metric│ │Deploy││    │    │
  │                           │  │  │Invest│ │Invest│ │Invest││    │    │
  │                           │  │  └──┬───┘ └──┬───┘ └──┬───┘│    │    │
  │                           │  └─────┼────────┼────────┼────┘    │    │
  │                           └────────┼────────┼────────┼─────────┘    │
  │                                    │        │        │              │
  │  ┌─────────────────────────────────┼────────┼────────┼────────┐    │
  │  │  MCP GATEWAY (Zero-Trust)       v        v        v        │    │
  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │    │
  │  │  │CloudWatch│  │Datadog   │  │ArgoCD /  │  │PagerDuty │   │    │
  │  │  │Logs/CW   │  │Metrics   │  │Spinnaker │  │Status    │   │    │
  │  │  │READ ONLY │  │READ ONLY │  │READ+WRITE│  │READ+WRITE│   │    │
  │  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │    │
  │  │                                    │                       │    │
  │  │              ┌─────────────────────┘                       │    │
  │  │              v                                             │    │
  │  │  ┌─────────────────────────┐     PCI-scoped systems:      │    │
  │  │  │  ACTUATION GATEWAY     │     NEVER autonomous access.  │    │
  │  │  │  - Dry-run mandatory   │     L0 only (human-directed). │    │
  │  │  │  - L2 for writes       │                               │    │
  │  │  │  - PCI zone: L0 only   │                               │    │
  │  │  │  - Red Button endpoint │                               │    │
  │  │  └─────────────────────────┘                               │    │
  │  └────────────────────────────────────────────────────────────┘    │
  │                                                                    │
  │  ┌────────────────────────────────────────────────────────────┐    │
  │  │  PERSISTENCE & COMPLIANCE                                  │    │
  │  │  Temporal (workflow)  │  S3 (audit logs, 7yr retention)   │    │
  │  │  RDS (checkpoint)     │  PII redaction pipeline (inline)  │    │
  │  │  OpenSearch (traces)  │  SOC 2 evidence export            │    │
  │  └────────────────────────────────────────────────────────────┘    │
  └────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Dimension       Decision                  Alternative    Why Not   │
  │  ─────────────   ───────────────────────   ────────────   ────────  │
  │  Cost            Model routing (Haiku      Single Opus    10x cost  │
  │                  for triage, Opus for       for all       with no   │
  │                  complex RCA only)                        quality   │
  │                  ~$53/1k incidents                        gain on   │
  │                                                           triage   │
  │                                                                     │
  │  Latency         Parallel fan-out with     Sequential    10+ min   │
  │                  2-min time budget          hypotheses    per P1    │
  │                  Target: < 3 min RCA                      unaccept │
  │                                                                     │
  │  Ops complexity  Temporal for durability    In-process    Multi-    │
  │                  (multi-cloud DR             asyncio      cloud DR  │
  │                  requires external state)                 needs     │
  │                                                           external │
  │                                                           state    │
  │                                                                     │
  │  Security        L2 for all writes,        L3 for low-   PCI      │
  │                  L0 for PCI zone,           risk writes   regulator│
  │                  per-session scoped tokens                won't    │
  │                                                           accept   │
  │                                                           L3       │
  │                                                                     │
  │  Scalability     ECS Fargate auto-scale    EKS           Fargate  │
  │                  per incident (bulkhead                    simpler  │
  │                  isolation via task)                       for I/O- │
  │                                                           bound    │
  └──────────────────────────────────────────────────────────────────────┘
```

**Decision rationale**: The PCI compliance boundary is the dominant architectural constraint. By hard-coding L0 for PCI-scoped systems, the agent can never autonomously touch cardholder data -- the SOC 2 auditor sees a deterministic, non-bypassable boundary rather than a probabilistic model-layer control. Temporal is chosen over in-process durability because multi-cloud DR requires state that survives the loss of an entire region. Model routing delivers the 60-80% cost reduction needed to justify the project's ROI at 15-20 incidents/month volume.

---

### Scenario 2: Healthcare Platform SRE Agent Under HIPAA

**Problem statement**: A healthcare SaaS platform (EHR + telemedicine) on GCP serves 200 hospitals. HIPAA compliance is mandatory. The platform has 8-10 P1 incidents/month, MTTM of 60 minutes, and the CTO wants to reduce MTTM by 40% without exposing ePHI to any AI system. The January 2025 HIPAA Security Rule NPRM explicitly brings AI systems into scope.

**Architecture**:

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │                                                                        │
  │  ┌────────────────────────────────────────────────────────────────┐   │
  │  │  ePHI-FREE ZONE (AI agent operates here exclusively)          │   │
  │  │                                                                │   │
  │  │  ┌──────────────┐    ┌───────────────────────────────┐        │   │
  │  │  │ Alert Ingest │───>│  PII/PHI Redaction Gateway    │        │   │
  │  │  │ (Cloud       │    │  - Regex + NER-based scrub    │        │   │
  │  │  │  Monitoring) │    │  - Applied BEFORE context     │        │   │
  │  │  └──────────────┘    │    enters any LLM             │        │   │
  │  │                      │  - Audit log of redactions    │        │   │
  │  │                      └───────────────┬───────────────┘        │   │
  │  │                                      │ sanitized context      │   │
  │  │                                      v                        │   │
  │  │  ┌──────────────────────────────────────────────────────┐     │   │
  │  │  │  SEALED VM (Confidential Computing, GCP CVM)         │     │   │
  │  │  │  - Hypervisor-level isolation                        │     │   │
  │  │  │  - Per-session scoped-down tokens                    │     │   │
  │  │  │  - Defensive MITM proxy (reject non-provisioned      │     │   │
  │  │  │    tokens, block exfiltration)                        │     │   │
  │  │  │  - Egress allowlist = capability grant               │     │   │
  │  │  │                                                      │     │   │
  │  │  │  ┌──────────────┐  ┌──────────┐  ┌──────────────┐   │     │   │
  │  │  │  │  Supervisor  │  │  Sub-    │  │  Sub-        │   │     │   │
  │  │  │  │  (durable,   │  │  Agent:  │  │  Agent:      │   │     │   │
  │  │  │  │  Spanner     │  │  Log     │  │  Metric      │   │     │   │
  │  │  │  │  checkpoint) │  │  Analyst │  │  Analyst     │   │     │   │
  │  │  │  └──────────────┘  └─────────┘  └──────────────┘   │     │   │
  │  │  └──────────────────────────────────────────────────────┘     │   │
  │  │                                                                │   │
  │  │  MCP Servers (all read-only in ePHI-free zone):               │   │
  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │   │
  │  │  │Cloud     │ │Cloud     │ │GKE API   │ │Incident  │         │   │
  │  │  │Logging   │ │Monitoring│ │(redacted)│ │Mgmt      │         │   │
  │  │  │(redacted)│ │          │ │          │ │          │         │   │
  │  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘         │   │
  │  └────────────────────────────────────────────────────────────────┘   │
  │                                                                        │
  │  ┌────────────────────────────────────────────────────────────────┐   │
  │  │  ePHI ZONE (agent NEVER enters; human-only actuation)         │   │
  │  │                                                                │   │
  │  │  ┌──────────┐ ┌──────────┐ ┌──────────┐                      │   │
  │  │  │ EHR DB   │ │ Patient  │ │ Clinical │                      │   │
  │  │  │ (Cloud   │ │ Portal   │ │ APIs     │                      │   │
  │  │  │  SQL)    │ │          │ │          │                      │   │
  │  │  └──────────┘ └──────────┘ └──────────┘                      │   │
  │  │                                                                │   │
  │  │  Actuation: L0 (human-directed only). Agent may               │   │
  │  │  RECOMMEND actions; human SRE executes.                       │   │
  │  └────────────────────────────────────────────────────────────────┘   │
  │                                                                        │
  │  ┌────────────────────────────────────────────────────────────────┐   │
  │  │  COMPLIANCE LAYER                                              │   │
  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐   │   │
  │  │  │ Audit Log    │ │ Reasoning    │ │ HIPAA Evidence       │   │   │
  │  │  │ (Spanner,    │ │ Trace Store  │ │ Export (automated    │   │   │
  │  │  │  immutable,  │ │ (5th layer)  │ │  quarterly reports)  │   │   │
  │  │  │  7yr retain) │ │              │ │                      │   │   │
  │  │  └──────────────┘ └──────────────┘ └──────────────────────┘   │   │
  │  └────────────────────────────────────────────────────────────────┘   │
  └────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Dimension       Decision                  Alternative    Why Not   │
  │  ─────────────   ───────────────────────   ────────────   ────────  │
  │  Cost            Sealed VM + Confidential  Standard VM    ePHI     │
  │                  Computing. ~2x compute     with network  exposure │
  │                  premium. Justified by      isolation     risk is  │
  │                  $10.93M avg breach cost.   only          existen- │
  │                                                           tial     │
  │                                                                     │
  │  Latency         Redaction gateway adds    Skip          HIPAA    │
  │                  200-500ms per context       redaction,    violation│
  │                  load. Acceptable: RCA is    use model     if PHI  │
  │                  minutes, not ms.            layer only    enters  │
  │                                                           context  │
  │                                                                     │
  │  Ops complexity  Spanner for checkpoints   Postgres +    200      │
  │                  + audit (native GCP,        custom       hospitals│
  │                  global consistency)          replication  need     │
  │                                                           global   │
  │                                                           consist. │
  │                                                                     │
  │  Security        Hard ePHI zone boundary.  Dynamic       Auditors │
  │                  Agent physically cannot     redaction    want     │
  │                  reach ePHI systems.         + L2 access  determin │
  │                  L0 for all ePHI zones.                   istic    │
  │                                                           proof of │
  │                                                           isolation│
  │                                                                     │
  │  Scalability     One sealed VM per          Shared VM    Bulkhead │
  │                  incident (bulkhead).        pool         prevents │
  │                  Auto-provision on alert.                 cross-   │
  │                                                           incident │
  │                                                           data     │
  │                                                           leakage  │
  └──────────────────────────────────────────────────────────────────────┘
```

**Decision rationale**: The architecture physically prevents ePHI from entering the AI system's context -- the redaction gateway sits upstream of all LLM processing, and network-level controls prevent the sealed VM from reaching ePHI data stores. This converts a probabilistic guarantee ("the model won't leak PHI") into a deterministic one ("the model never sees PHI"). The 2x compute premium for Confidential Computing is negligible against $10.93M average healthcare breach cost. One sealed VM per incident provides bulkhead isolation: a compromised investigation cannot access another incident's (potentially different hospital's) data. The agent operates at L0 for ePHI zones -- it recommends, humans execute -- which eliminates the autonomous actuation risk demonstrated by the Amazon Kiro AI incident.

---

## Sources

Research based on 32 sources including Google SRE whitepapers, PagerDuty engineering blogs, Anthropic security architecture, Datadog product documentation, Temporal durable execution documentation, Arize field analysis, Stanford HAI 2025 AI Index, and vendor benchmarks. Full source list in research file `03-incident-response-ai-agent.md`.
