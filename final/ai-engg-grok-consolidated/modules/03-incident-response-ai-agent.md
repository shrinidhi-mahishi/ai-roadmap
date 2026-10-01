# Module 03: Incident Response AI Agent

### What Is This?

An incident response (IR) AI agent is an automated system that watches for production alerts, investigates root causes, and either recommends or executes mitigations -- all while keeping a human in the loop for dangerous actions. Think of it like a junior on-call engineer that never sleeps: it reads every alert, pulls up runbooks, checks metrics and logs, and writes a triage report within seconds of a page, but still asks a senior before restarting anything. The key architectural insight from every production deployment (PagerDuty, Google, AWS) is the same: **context recall beats cleverness** -- assembling the right information up front matters far more than how smart the model is. Real IR agents are workflow-shaped on the outside (deterministic noise reduction, routing, escalation) and agentic on the inside (LLM-driven diagnosis and playbook selection when the failure mode is novel).

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
+---------------------------------------------------------------------------+
|                            CONTROL PLANE                                   |
|  Alert ingress (PagerDuty Event Orchestration / EventBridge / Datadog)     |
|  AIOps group/suppress/enrich . priority rules . virtual-responder place   |
|  Approval gates (needs_approval / always_ask) . risk/policy metadata      |
|  Durable run state for paused HITL . max_turns / iteration caps           |
|                                                                            |
|  +----------------+  +-----------------+  +-----------------------------+ |
|  | Enricher       |  | Reasoning       |  | Durable Supervisor          | |
|  | Pipeline       |->| Engine          |->| (Checkpoint Store)          | |
|  | - Alert desc   |  | (LLM Core)     |  | - Priority queue (user=0,   | |
|  | - Playbooks    |  | - Hypothesis    |  |   agent=1)                  | |
|  | - Topology     |  |   generation    |  | - Lock (serializes resume)  | |
|  | - Past incs    |  | - RCA loop      |  | - Drain loop (event spine)  | |
|  | - Precomputed  |  | - Confidence    |  | - task_id === thread_id     | |
|  |   Context      |  |   scoring       |  +-------------+--------------+ |
|  +--------+-------+  +--------+--------+                |                 |
|           |                   |                          | structured      |
|           v                   v                          | action plan     |
|  +----------------+  +-----------------+                 |                 |
|  | Strategy       |  | HITL / Policy   |                 |                 |
|  | Selector       |  | approve/reject  |                 |                 |
|  | (Runbook       |  | 2-person for    |<----------------+                 |
|  |  Catalog)      |  | destructive     |                                   |
|  +----------------+  +-----------------+                                   |
+-----------+---------------------------------------------------------------+
            |
+-----------v---------------------------------------------------------------+
|                             DATA PLANE                                     |
|  +----------------------------------------------------------------------+ |
|  |       Actuation Agent / Gateway                                       | |
|  |  - Pre-flight: dry-run, justification verification                    | |
|  |  - Concurrent action check                                            | |
|  |  - Dynamic autonomy downgrade (L3 -> L2 when risk spikes)            | |
|  |  - Red Button endpoint (emergency halt <1s)                           | |
|  +------+----------+----------+------------------------------------------+ |
|         |          |          |                                             |
|         v          v          v                                             |
|  +----------+ +----------+ +----------+                                    |
|  | MCP Svr  | | MCP Svr  | | MCP Svr  |                                   |
|  | (Observ) | | (Infra)  | | (Traffic)|                                   |
|  | Datadog  | | K8s/VMs  | | LB/DNS   |                                   |
|  | CW/logs  | | Deploys  | |          |                                   |
|  +----+-----+ +----+-----+ +----+-----+                                   |
|       |            |            |                                           |
+-------+------------+------------+-------------------------------------------+
            |
+-----------v---------------------------------------------------------------+
|                    PERSISTENCE & TELEMETRY                                  |
|  +-------------+ +-------------+ +--------------+ +--------------------+  |
|  | Checkpoint  | | Execution   | | Reasoning    | | Vector Knowledge   |  |
|  | Store       | | Trace DB    | | Trace Store  | | Base (embeddings   |  |
|  | (Temporal/  | | (Spanner/   | | (5th telem   | |  of past incidents)|  |
|  |  Redis)     | |  Postgres)  | |  layer)      | |                    |  |
|  +-------------+ +-------------+ +--------------+ +--------------------+  |
|  +-------------+ +-------------+ +--------------+                         |
|  | Precomputed | | HITL        | | Immutable    |                         |
|  | Incident    | | RunState    | | Audit Log    |                         |
|  | Context     | | (JSON)      | | (WORM, hash  |                         |
|  |             | |             | |  chained)    |                         |
|  +-------------+ +-------------+ +--------------+                         |
+----------------------------------------------------------------------------+
```

### 1.2 Request-Flow Narrative

**Step 1 -- Alert ingestion (control plane).** Monitoring emits alerts to PagerDuty Global Integration / Event Orchestration, or GuardDuty/Security Hub fires into EventBridge creating an AWS Security Incident Response case, or Datadog triggers an investigation via `@Datadog investigate` in Slack.

**Step 2 -- Noise / priority (control plane, deterministic).** AIOps grouping + Event Orchestration severity rules filter the storm. Forrester TEI (PagerDuty-commissioned) reports **91% signal-noise reduction** and **50% fewer incidents** for studied Operations Cloud customers. **This layer is NOT the LLM's job** -- treating the model as the first noise filter is the wrong architectural layer.

**Step 3 -- Precompute Incident Context (persistence -> data plane).** On incident open, the system eagerly assembles a structured working set: incident object, raw alert payloads, Past/Related/Outlier incidents, Related Change events, runbook sections. PagerDuty's lazy-discovery prototype ran **>60 s** first response with order-dependent answers; precompute cut first-response latency to **~10 s**. This is the single most impactful design decision in IR agent architecture.

**Step 4 -- Virtual responder engage.** Paige engages via Incident Workflow on trigger/priority, or added to an escalation level to investigate in parallel with humans. The durable supervisor receives the event and places it into a priority queue (user input = priority 0, sub-agent results = priority 1).

**Step 5 -- Reason (data plane).** The LLM reasoning engine classifies symptoms against closed mitigation sets. Google's approach: drain/rollback/restart/add capacity mapped to typed playbooks like `borg_task_restart`. For novel outages, the engine generates hypotheses for root cause and spawns one sub-agent per hypothesis (parallel fan-out). Sub-agents are stateless -- they query logs, metrics, and deploy history via MCP tool servers, then report findings back. If a sub-agent dies, it is re-spawned, not resumed.

**Step 6 -- Act via tool proxies.** Bias-to-relevance: external observability/knowledge only; PagerDuty facts come from precomputed context -- no inward PD API fishing. Auto-search logs only when provider, target, and time window are clear and the result would change the next step; otherwise **propose** the query.

**Step 7 -- Approval interrupt (control plane).** Mutations require `needs_approval` / Anthropic MCP default `always_ask` / Google risk metadata + 2-person policy for destructive actions. Pause -> serialize `RunState` to durable store -> approve/reject -> resume. The actuation gateway enforces pre-flight checks: mandatory dry-run, justification verification (action must target an open incident), concurrent action checks.

**Step 8 -- Observe / pivot.** On mitigation failure, stay in flow and pivot. Google's example: `borg_task_restart` fails -> agent analyzes that only this job fails in cell -> pivots to code RCA instead of expanding blast radius.

**Step 9 -- Checkpoint and learn.** Debounced context rebuild on trigger/note/resolve; promote recollections to versioned playbooks with normalized alert signatures (strip timestamps/UUIDs, keep discriminators); Memory API redaction at human speed.

**Step 10 -- Telemetry close.** Record proposed vs approved action, skip annotations for missing sources, correlation ID across turns. Reasoning traces (the **fifth telemetry layer**) capture signals used, hypotheses considered, action rationale, and confidence levels. ICS 3Cs (coordinate/communicate/control) remain human-owned.

### 1.3 Progressive Topology (Newsletter #131)

Manual runbook -> LLM assistant -> MCP tools -> Agent Skills -> memory (`Agents.md`) -> Agent SOPs (RFC 2119) -> packaged agent -> webhook/cron -> multi-agent filesystem workspace. Each step earns more autonomy through demonstrated reliability.

### 1.4 Published Reference Architectures

| System | Pattern | Key Characteristics |
| --- | --- | --- |
| **PagerDuty Paige / SRE Agent** | Durable supervisor + stateless sub-agents (LangGraph BSP) | Precomputed Incident Context (~10 s); propose-only mutations; memory (observations/recollections/playbooks); 50% faster resolution |
| **Google ProdAgent / Gemini CLI** | Orchestrator-workers with Actuation Agent safety gateway | MTTM-first; closed mitigation set; typed MCP tools; risk metadata; 5-min ack SLO; 10% MTTM reduction (hypothesis), 44% (dashboards), 195% anomaly findings increase |
| **AWS Security IR Agent** | Read-only investigative agent with clarify timeout | CloudTrail/IAM/EC2/Cost Explorer; 10-min clarify timeout then auto-start; `AWSServiceRoleForSupport` read-only SLR |
| **Datadog Bits AI SRE** | Platform-native agent with builder | Full telemetry access; chaining: investigation -> remediation; HIPAA support; 2,000+ environments |

---

## 2. Core Mechanics & Algorithms

### 2.1 Workflow vs Agent Decision Rule

Anthropic's principle still holds: use a **workflow** when the IR path is fixed (noise reduction, priority assignment, known Automation Action); use an **agent** when subtasks are unpredictable (novel outage diagnosis). Published IR systems are workflow-shaped outer loops with agentic diagnosis inner loops.

| Pattern | IR Use |
| --- | --- |
| **Workflow (deterministic outer)** | Event Orchestration enrich/suppress/route; Automation Action before human notify; Incident Workflows auto-engage virtual responder |
| **Agent (LLM-directed tool loop)** | ProdAgent: classify -> `fetch_playbook` -> typed mitigation; AWS agent: clarify -> CloudTrail/IAM/EC2 -> timeline |
| **Orchestrator-workers** | Distinct agents for playbook nav, alerting, anomaly, insights; specialists under a manager |
| **ICS / IMAG (human control plane)** | IC / Communications Lead / Ops Lead; agents summarize, draft handoffs/postmortems on top of IMAG |

### 2.2 Five-Level Autonomy State Machine (Google SRE)

Each transition requires demonstrated reliability at the current level before advancing.

```
  L0 Manual -----> L1 Assisted -----> L2 Partial -----> L3 High -----> L4 Full
  (monitor        (investigate       (actuate,         (full auto,    (end-to-end
   only)           only)              human             human          autonomy)
                                      mitigates)        self-directs)

  Gate:            Tool adoption      Precision +       Multi-step     Continuous
                                      safe actuation    resolution     self-monitoring

  DYNAMIC DOWNGRADE: L3 -> L2 when elevated risk or anomalous production state detected
```

**Key invariant**: Write operations at any level must never exceed the safety boundary of that level. An agent may be L3 on Monitor but L1 on Actuate -- levels are evaluated per operational function across five axes: Monitor, Investigate, Mitigate, Actuate, Self-Direct.

### 2.3 IR Triage State Machine

```
                 +------------+
                 |  INGEST    |
                 +-----+------+
                       v
                 +------------+
                 |  NOISE_FX  |  AIOps group / suppress / enrich
                 +-----+------+
                       v
                 +------------+
                 | PRECOMPUTE |  incident + related + changes + runbook
                 +-----+------+
                       v
              +--------+--------+
              v                 v
       +------------+    +------------+
       |  TRIAGE    |<---|  SKIP_GAP  |  source slow -> annotate & continue
       +-----+------+    +------------+
             |
      +------+----------+----+-----------+
      v      v           v                v
   PROPOSE  TOOL_READ   NEEDS_APPROVAL   MAX_TURNS
      |      |              |                |
      |      v              v                v
      |   OBSERVE     PAUSE_RUNSTATE     ESCALATE_HUMAN
      |      |              |
      |      +------+-------+
      |             v
      |      +------------+
      |      |  MITIGATE  |  typed tool only (if approved)
      |      +-----+------+
      |       +----+----+
      |       v         v
      |    VERIFY    ROLLBACK / PIVOT_RCA
      |       |
      +-------+--> RESOLVE --> MEMORY_PROMOTE
```

**Invariants:**

1. **Noise before agent** -- LLM is never the first filter for alert storms.
2. **Precompute before reason** -- Platform facts assembled eagerly; tools fetch external evidence.
3. **Typed mutations only** -- Closed mitigation vocabulary; no free-form shell.
4. **Fail-closed approvals** -- Malformed `needs_approval` args require human review.
5. **Tenant memory isolation** -- Observations/recollections/playbooks never cross customers.

### 2.4 Reactive Event Loop (PagerDuty Model)

```
  accept_event <-------------------------------------+
       |                                              |
       v                                              |
  route_event                                         |
       |                                              |
  +----+----+                                         |
  v         v                                         |
handle_   handle_                                     |
user_     sub_agent_                                  |
input     result                                      |
(pri=0)   (pri=1)                                     |
  +---+---+                                           |
      v                                               |
    plan (dispatch) --spawn sub-agents--> re-interrupt |
```

**Concurrency invariants:**
1. **Serialization**: The drain loop holds a lock while resuming. Concurrent arrivals are serialized -- no two events mutate supervisor state simultaneously.
2. **Priority**: User input always preempts sub-agent results (priority 0 vs 1).
3. **Idempotency**: `task_id === thread_id` eliminates correlation lookups. Each step is atomic and persisted the moment it is applied.
4. **Liveness**: The graph spends most time paused at `accept_event`. The drain loop is the spine.

PagerDuty chose **parallel fan-out/fan-in** over sequential or wait-for-all because interactivity is non-negotiable for incident response -- users must steer mid-investigation.

### 2.5 Hypothesis-Parallel Investigation Algorithm

```
Algorithm: PARALLEL_RCA(alert, context, max_hypotheses=4)
Input:  Enriched alert + topology + past incidents
Output: Ranked (hypothesis, evidence, confidence) tuples

1. hypotheses <- LLM.generate_candidates(alert, context)    // O(1) LLM call
2. Truncate to top-k by prior probability                    // k = max_hypotheses
3. FOR EACH h_i IN hypotheses (PARALLEL fan-out):
     sub_agent_i <- spawn_stateless_agent(h_i)
     evidence_i  <- sub_agent_i.investigate(
                      tools=[logs, metrics, traces, deploys],
                      time_budget=120s
                    )                                         // I/O-bound
4. BARRIER: wait_for_all(sub_agents) OR timeout
5. scored <- []
   FOR EACH (h_i, evidence_i):
     score_i <- deterministic_match(evidence_i, golden_data)  // NOT LLM scoring
     scored.append((h_i, evidence_i, score_i))
6. SORT scored BY score DESC
7. IF scored[0].score < confidence_threshold:
     ESCALATE to human (dynamic downgrade to L2)
8. RETURN scored
```

**Complexity**: O(k) sub-agent spawns, wall-clock time dominated by slowest sub-agent (bounded by `time_budget`). Total LLM calls: 1 (hypothesis generation) + k (one per sub-agent).

**Key insight from Google**: A mitigation is scored "correct" only if the agent's output **deterministically matches** the fully actionable, exact parameters of golden data -- not vague suggestions. This eliminates LLM-as-judge variance for safety-critical scoring.

### 2.6 Algorithms and Complexity

| Step | Algorithm | Complexity Note |
| --- | --- | --- |
| Noise reduction | Rule/ML grouping over alert stream | O(alerts in window); must finish before agent wake |
| Context assemble | Parallel fan-out to PD/related/changes/runbook with debounce | Wall-clock = slowest source; skip-and-annotate avoids O(inf) wait |
| Playbook bind | Deterministic URL/custom_details match, else opportunistic link follow | O(1) bind preferred over O(k) tool hunts |
| Symptom classify | LLM over closed label set -> typed tool | Bounded tool cardinality; Skills loaded lazily to save context |
| Blast-radius check | Service graph + related incidents | Cap query windows; truncate `custom_details`/notes to first 2,000 characters |
| HITL resume | Atomic owner-checked state transition | Prevents double-resume on concurrent approval submissions |

### 2.7 Context Management Invariants

**Context rot**: Performance degrades as context grows. JSON blobs of alerts, past incidents, and topology overwhelm the model's ability to weight information correctly (Liu et al., 2023).

**Instruction overload**: Inverse relationship between instruction volume and output quality (Jaroslawicz et al., 2025). Each new capability competes for model attention. Monolithic agents that worked well at a certain feature set degraded as new capabilities were added.

**Mitigations:**
- **Token minimization per step**: Google's AI Operator uses the minimum token set per step because incident chain-of-thought can span very long horizons.
- **Multi-agent context scoping**: PagerDuty sacrifices unified context for better per-agent reasoning quality. Each sub-agent receives only task-relevant context.
- **Lazy skill loading**: Skills loaded into model context only when needed, not pre-loaded.
- **Context compression**: Cloudflare collapsed 2,500+ API endpoints into two tools consuming ~1,000 tokens (down from 1.17M tokens).

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formula -- $ per 1,000 Incidents

```
Cost_per_1k = 1000 * (
    H * (input_tokens * $/input_token + output_tokens * $/output_token)
  + T * tool_call_cost
  + checkpoint_writes * storage_cost
  + embedding_queries * embedding_cost
) * (1 - cache_hit_rate * cache_savings)

Where:
  H = avg LLM hops per incident (typically 3-15)
  T = avg tool calls per incident (typically 5-20)
  cache_hit_rate = ~0.31 (observed) to ~0.90 (static prefix)
  cache_savings = ~0.90-0.95
```

**Model prices (Anthropic, as of research date):**

| Model | Input $/MTok | Output $/MTok | Cache read $/MTok (5-min TTL) |
| --- | --- | --- | --- |
| Sonnet 5.5 | $2 | $10 | $0.20 |
| Haiku 4.5 | $1 | $5 | $0.10 |

**Worked Example A -- Sonnet-only, mid-complexity (Grok model):**

Token shape: 8k static prefix (90% cache hit), 32k dynamic input, 4k output.

- Uncached: (40k in x $0.002/k) + (4k out x $0.01/k) = $0.08 + $0.04 = **$0.12/incident**
- With caching on 8k prefix: ~**$0.107/incident** -> **$107 / 1k incidents**
- With 70% Haiku / 30% Sonnet routing: ~**$69.50 / 1k incidents**

**Worked Example B -- Frontier model, 8 hops (Opus model):**

Token shape: 40k input per incident (8 hops), 8k output, 12 tool calls, 5 checkpoints, 3 vector queries.

| Component | Units | Unit Cost | Per-Incident |
| --- | --- | --- | --- |
| LLM input tokens | 40K | $3/1M | $0.12 |
| LLM output tokens | 8K | $15/1M | $0.12 |
| Tool calls | 12 | $0.001 | $0.012 |
| Checkpoint writes | 5 | $0.0001 | $0.0005 |
| Vector queries | 3 | $0.0001 | $0.0003 |
| **Gross per incident** | | | **$0.253** |
| After 31% cache hit (x0.70) | | | **$0.177** |
| **Per 1,000 incidents** | | | **$177** |
| With model routing (70% small) | | | **$53-$71** |

**The 100-300x model routing lever**: Route simple triage (alert classification, deduplication, status updates) to small models ($0.01-0.05/1M tokens). Escalate complex RCA and multi-step reasoning to frontier models ($3-15/1M tokens). This achieves **60-80% token spend reduction**.

**Platform metering (not raw tokens):**

| Platform | Metering Model |
| --- | --- |
| PagerDuty Paige | 4 AI Actions per user request or nudge |
| Anthropic Managed Agents | $0.08 / session-hour active runtime + token rates |
| AWS Security IR | Included with service; 10,000 findings/month free tier, then volume tiers |

**Inference cost trajectory**: Stanford HAI 2025 AI Index shows GPT-3.5-level inference cost dropped 280x between Nov 2022 and Oct 2024. Hardware costs decline ~30%/year, energy efficiency improves ~40%/year.

### 3.2 Latency Budget

**Published anchors:**

| Path | Figure | Source |
| --- | --- | --- |
| PagerDuty first response (precomputed) | **~10 s** | PagerDuty Eng |
| PagerDuty lazy discovery prototype | **>60 s** | PagerDuty Eng |
| AWS investigative summary | "Within **minutes**" | AWS docs |
| AWS clarify timeout | **10 minutes** then auto-start | AWS docs |
| Google RCA demo narrative | "Under **2 minutes**" | Google SRE (demo, not SLA) |
| Google AI Alert System hard budget | **~2 minutes** | Google SRE whitepaper |

**Component-level latency budget (agent-side, excluding human ack):**

| Component | p50 | p95 | p99 | Notes |
| --- | --- | --- | --- | --- |
| Raw LLM TTFT | ~330 ms | ~1.2 s | ~3.2 s | Baseten benchmark, Sept 2026 |
| Context fan-out (parallel) | 2,000 ms | 6,000 ms | 12,000 ms | Debounce; skip slow source |
| Tool RTT x 1-2 reads | 400 ms | 2,500 ms | 8,000 ms | External observability |
| Proposal compose (remaining gen) | 1,200 ms | 3,000 ms | 5,000 ms | ~1-2k out tokens |
| **First triage proposal** | **~4.5 s** | **~13.5 s** | **~29 s** | Additive worst-case |
| Agent turn (e2e, 2-5 hops) | 3-5 s | 6-9 s | 8-12 s | Including tool calls |
| Full investigation (parallel) | ~2 min | -- | ~10 min | Sequential mode: 3-4 hypotheses |
| Human ack (HITL path only) | N/A (async) | N/A | >= 5 min SLO | Google ack SLO; not in agent p99 |

**TTFT cost curve**: Going from 500ms to 200ms P99 targets increases infrastructure spend by roughly 35%.

**Provider consistency** (2026 benchmarks):
- **Anthropic (Claude)**: Most consistent -- P50 and P99 TTFT stay close. Best for latency-sensitive agent loops.
- **OpenAI (GPT-4.1)**: P99 spikes 3-5x above P50 during peak hours. Requires hedging or request routing.
- **Google (Gemini)**: Fast and stable, but post-update behavioral changes observed. Requires regression testing.

**Mitigations by tier:**

| Tier | Failure Mode | Mitigation |
| --- | --- | --- |
| **p50** | Cold prompt / large dynamic context | Prompt cache static prefix; precompute; truncate to discriminative fields (2k char blobs) |
| **p95** | Slow observability connector | Skip-and-annotate; circuit breaker -> degrade to context-only proposal; parallel fan-out |
| **p99** | Model/provider stall or storm | Fallback model chain (Sonnet -> Haiku -> deterministic runbook binder); `max_turns`; Event Orchestration suppress before agent wake |

### 3.3 Throughput and Back-Pressure

1. **Machine-speed back-pressure** -- Event Orchestration suppress/group/enrich before page; agent never sees the raw storm.
2. **Provider TPM/RPM** -- Agent loops inherit model rate limits; `max_turns` (default 10; `None` disables) caps cost/iteration.
3. **Capacity signal** -- Align virtual-responder engagement to P1/P2 so parallel agent triage lands inside the 5-minute ack SLO.
4. **Queue design** -- Durable workflow queue with concurrency caps per tenant/service; shed low-priority triage first under overload.
5. **Bulkhead isolation** -- One incident's agent cannot starve another's resources (ECS Fargate task per incident, or thread pool bounds).

### 3.4 NFR Targets

| NFR | Target | Rationale |
| --- | --- | --- |
| **Availability** | 99.9% (8.7h/yr) | Incident tool must be up when prod is down. Agent triage is best-effort beside human on-call; Google requires AI failure contingencies and backup automated/manual options |
| **RPO** | 0 (every step) | Each step atomic + persisted immediately. HITL `RunState` + Temporal checkpoints; rebuild context on trigger/note/resolve |
| **RTO (supervisor)** | <30 s | Re-hydrate from last checkpoint, re-spawn sub-agents |
| **RTO (sub-agent)** | 0 (re-spawn) | Stateless; no recovery needed |
| **MTTM reduction** | 10-44% | Google measured: 10% (hypothesis), 44% (dashboards) |
| **Audit log latency** | <5 s | Every action logged before next step |
| **PII redaction** | Inline, pre-log | PHI in context = in scope for HIPAA |
| **Red Button latency** | <1 s | Emergency halt of all in-flight agent actions |
| **Max concurrent** | Bounded by thread pool | Bulkhead isolation per incident |

### 3.5 NFR Trade-Offs

| NFR | IR Target | Explicit Trade-Off |
| --- | --- | --- |
| **Availability** | Best-effort beside human on-call | Higher agent availability != higher mutation autonomy -- keep writes fail-closed |
| **RPO** | Checkpoint every tool step | Tighter RPO increases persistence cost / replay size |
| **RTO** | Resume without re-running completed side effects | Faster RTO requires stricter idempotent tool design |
| **Compliance** | Proposal + human approval audit chain | Full chain-of-custody logging increases storage and redaction workload |
| **Autonomy vs blast radius** | Typed tools + peak-traffic policies + service-graph scope | More autonomy increases MTTM potential AND blast-radius risk |
| **Speed vs approval** | Propose-only (Paige today) vs HITL mutation (Google CLI) | Skipping approval cuts latency; postmortems of unauthorized AI prod ops argue against it |

### 3.6 On-Call SLAs / MTT* Framing

| Metric | Published Guidance |
| --- | --- |
| **Acknowledge SLO** | Google Core SRE: typically **5-minute SLO** just to acknowledge a page; optimize **MTTM** (Mean Time to Mitigation / stop Bad Customer Minutes) ahead of full MTTR |
| **MTTA target** | Incident.io: target **MTTA under 5 minutes** with good automation |
| **Customer case** | Anaplan (PagerDuty): MTTA from hours -> **5 minutes**; MTTR from **3 hours -> under 30 minutes** |
| **AWS resolution** | Resolution time from **days to hours**; summaries **within minutes** |
| **ICS process** | Declare early; IC owns 3Cs (coordinate/communicate/control); Ops Lead applies tools |
| **Forrester TEI** | 249% ROI / 3 years, 91% noise reduction, 59% less downtime, 50% fewer incidents (vendor-commissioned) |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

**Why session memory is not enough**: "Saving chat history helps an agent remember, but it does not prove which shell command ran, which email was sent, which approval was granted, or whether a retry would duplicate a side effect." Without checkpointing, a crash at 3h50m into a long incident restarts from zero.

**PagerDuty's asymmetric durability model:**

| Component | Strategy |
| --- | --- |
| **Supervisor** | Checkpointed state; priority queue inside checkpoint; lock + drain loop; `task_id === thread_id`. "Events survive a crash because the supervisor's state does." |
| **Sub-agents** | No checkpoints. Stateless workers. Re-spawn on death. No N+1 checkpoint reconciliation. |

**Anti-pattern**: Do NOT checkpoint only model messages. "'The email was sent' in conversation history may be a model claim. Store the provider message ID or a reference to the operation record."

| Mechanism | Behavior |
| --- | --- |
| **Precomputed context rebuild** | On trigger, note add, resolve; debounce bursts; proceed if a source is slow and note what was skipped |
| **HITL RunState serialization** | Persist paused approvals; `to_json`/`from_json`; sticky always_approve/reject; version agent defs with stored state |
| **Temporal / workflow checkpoint** | Workflow history as source of truth for replay; Activities wrap tool calls; signals for human approve/reject; timers for 10-min clarify timeout |
| **Memory layers** | Observations (service facts), Recollections (decision-changing details), Playbooks with normalized signatures |
| **Dead-letter** | Permanent tool failures / poison pills -> DLQ with correlation ID; do not auto-retry unbounded |

### 4.2 Failure Taxonomy

Production AI agents fail at **41-86.7%** rates without deliberate fault tolerance. **88%** of agents that work in demos fail in real workflows (Fiddler AI, 2026).

**Arize field analysis (2026) failure mode distribution:**

| Failure Mode | Frequency | IR Relevance |
| --- | --- | --- |
| **Context blindness** | 31.6% | Known race-condition runbook existed but was not recalled; response stretched ~3 hours. Prototype with alert + log line + runbook identified failure immediately |
| **Rogue actions** | 30.3% | Amazon Kiro AI (Dec 2025) autonomously deleted and rebuilt an environment without human approval, causing a 13-hour outage |
| **Silent degradation** | 24.9% | Functional-but-wrong outputs survive because reviewers see polished results and assume sound reasoning. MTTR for undetected failures: 4.2 hours vs 54 min for schema-validated failures |
| **Memory corruption** | 8.1% | Agent state becomes inconsistent over time; decisions based on stale or contradictory information |
| **Runaway execution** | 5.1% | Infinite loops; compounding error risk with autonomous agents |

**IR-specific failure modes:**

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | 429/5xx from LLM or Datadog; network blip | Exponential backoff + jitter; circuit breaker |
| **Permanent** | 400 invalid tool schema; unknown playbook id; authZ deny | No retry; escalate / propose only |
| **Poison pill** | Same incident id + args crashes worker >= N times | Quarantine to DLQ; strip from auto-retry |
| **Idempotency** | Duplicate approval webhook; replayed Activity | Idempotency key = `incident_id:tool:args_hash:attempt_bucket`; atomic owner-checked resume |
| **Hallucinated queries** | LLM invents nonexistent log fields | Template query builders over free-form; ask for missing params rather than guessing |
| **Cascading alert storms** | Agent triage drowned without AIOps grouping | Noise reduction is a prerequisite NFR; 91% reduction is platform precondition |
| **State drift** | Stale context after long HITL wait | Debounce note bursts; rebuild on trigger/notes/resolve; version markers with serialized state |

**Hallucination cascades**: Agent fabricates information, then uses that fabrication to inform subsequent decisions across multiple systems. Example: inventory agent invents nonexistent SKU, then calls four downstream APIs to price, stock, and ship the phantom item.

### 4.3 Circuit Breaker State Machine

```
  CLOSED --(error_rate >= threshold in window)--> OPEN
    ^                                               |
    |                                     (cooldown elapsed)
    |                                               v
    +---------(probes succeed)-------------  HALF_OPEN
                     (probe fails) --> OPEN
```

**IR-shaped trigger conditions:**
- Repeated identical tool calls (loop detection)
- Cost velocity exceeding defined rate ($/min)
- Consecutive failures without recovery
- Permission boundary violations

**IR-shaped substitutes when breaker opens:**
- Skip-and-annotate missing observability
- Fail-closed approval callables
- Prefer classic Automation Actions when deterministic automation already meets the need
- Deterministic runbook binder (no LLM needed)

**Fallback chains**: Primary frontier model -> secondary small model -> deterministic runbook binder / Automation Action -> human-only escalate. **Never expand blast radius as a "retry."**

**Layered resilience** (defense-in-depth):
1. Circuit breakers -- prevent cascading failure across services
2. Timeout management -- prevent indefinite blocking on LLM/tool calls
3. Compensating transactions -- rollback partially completed workflows
4. Bulkhead isolation -- one incident's agent cannot starve another's resources

### 4.4 Zero-Trust MCP and Agent Identity

**Google SRE principle**: "Agent identities must be distinct from human users, strongly authenticated, with on-demand permissions only. Agents must never use standing, human-like credentials of their developers."

**Anthropic's three-layer defense model:**

| Layer | Type | Implementation |
| --- | --- | --- |
| **1. Environment** | Deterministic (design here first) | Sandboxes, VMs, filesystem boundaries, egress controls. Ephemeral containers (gVisor, seccomp) for multi-tenant. Sealed VMs (hypervisor) for autonomous agents. Per-session scoped-down tokens, independently revocable. |
| **2. Model** | Probabilistic | System prompts, classifiers, probes. Confidence thresholds for autonomy decisions. |
| **3. External content** | | Tool permission scoping, input inspection, connector auditing, egress allowlists. "Every function reachable through any domain on an allowlist is now an attack surface." |

**Egress control lesson**: Real incident -- a malicious file in a workspace instructed Claude to upload files via Anthropic's Files API using an attacker-controlled key. Fix: defensive MITM proxy inside VM rejects non-provisioned tokens.

**Tool-level controls:**

| Control | Spec |
| --- | --- |
| Anthropic MCP default | `always_ask` for MCP toolsets |
| OpenAI MCP | Local `require_approval`; hosted `require_approval: "always"`; sticky approvals |
| Anthropic tool annotations | Disclose open-world / destructive tools in MCP annotations |
| Google risk metadata | Per-tool impact class (safe/reversible/destructive) -> stricter review |
| PagerDuty RBAC | Separate roles: who can create vs run Automation Actions; team-level AI access toggles |
| AWS agent | Read-only via `AWSServiceRoleForSupport` SLR; CloudTrail-attributed |

**Principle of least agency** (Anthropic): "Grant the narrowest capability that still completes the task."

### 4.5 PII: Detection -> Redaction -> Audit

1. **Detect** -- Regex + NER over log lines, ticket bodies, Slack digests before context assembly (emails, tokens, PAN, secrets).
2. **Redact** -- Replace with stable tokens (`[REDACTED:email:3f2a]`) so correlation across turns survives; Memory API for human-speed view/update/redact.
3. **Audit** -- Log redaction events (field type, count, correlation id) without writing raw PII to model logs. AWS: customer data not used for training.

**HIPAA critical rule**: "If PHI was in the context during inference, the system is in scope" -- even transient context window presence counts. January 2025 HIPAA Security Rule NPRM (finalization mid-2026) explicitly brings AI systems into scope for ePHI governance. Healthcare breach costs: **$10.93M average** (IBM 2024 -- highest industry for 14 consecutive years).

### 4.6 Immutable Audit Logs / Chain of Custody

| Event | Must Record |
| --- | --- |
| Proposal | Model id, prompt hash, proposed tool + args, correlation id |
| Approval | Human identity, decision, timestamp, policy version |
| Execution | Tool proxy result, verify outcome, rollback if any |
| Access | CloudTrail (AWS SLR) or equivalent data-plane reads |

Google: every CLI-proxied action logs AI proposal + human approval. Prefer append-only / WORM storage for the decision ledger.

**SOC 2 auditor expectations (2026):**
- Model lineage: exact dataset, code, and approval behind each deployed model
- Prompt and inference logs with PII redaction applied before logging
- Drift-monitoring output
- Vendor risk assessment for every third-party LLM called
- Cost: $35K-$150K first year; enterprise-readiness $200K-$250K+

**Current governance gaps (2026):**
- Only **21%** of organizations maintain a real-time agent registry
- Only **18%** of security leaders believe their IAM handles AI agent identities effectively
- Only **38%** monitor AI activity end-to-end; **17%** track agent-to-agent interactions
- **42%** of companies abandoned AI initiatives in 2025 due to compliance/governance failures

### 4.7 Six Monitoring Signals for Production Agents

Traditional four telemetry layers (metrics, logs, traces, events) are insufficient. Agents need a **fifth**: reasoning traces.

| Signal | What It Catches |
| --- | --- |
| Goal Completion Rate | Silent failures, false success |
| Tool Success Rate | Integration breakage, API drift |
| Context Quality Score | Context rot, instruction overload |
| Reasoning Trace Completeness | Hallucination, shortcut reasoning |
| Escalation Rate | Autonomy calibration drift |
| Hallucination Rate | Fabricated evidence, phantom ops |

---

## 5. Production Enterprise Code

Runnable incident-triage loop: deterministic fake model (no API keys), exponential backoff + jitter, circuit breaker, fallback chain, correlation-id logs, graceful degradation, PII redaction, idempotency keys, autonomy downgrade, checkpoint store, Red Button, and immutable audit ledger.

```python
#!/usr/bin/env python3
"""Incident-response triage loop with full resilience stack.

Combines the best patterns from both source implementations:
- Grok: sync triage loop, email redaction, idempotency, audit ledger
- Opus: async model fallback, autonomy downgrade, Red Button,
         checkpoint-aware workflow, reasoning traces, cost velocity

No external APIs or API keys. Run: python3 this_file.py
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
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Structured logging with correlation IDs and reasoning traces
# ---------------------------------------------------------------------------

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s cid=%(correlation_id)s %(message)s",
)
logger = logging.getLogger("ir_agent")
logger.addFilter(CorrelationFilter())


def log(cid: str, level: int, msg: str, **fields: Any) -> None:
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.log(level, f"{msg} {extra}".rstrip(), extra={"correlation_id": cid})


# ---------------------------------------------------------------------------
# Autonomy levels and dynamic downgrade (Google SRE model)
# ---------------------------------------------------------------------------

class AutonomyLevel(IntEnum):
    L0_MANUAL = 0
    L1_ASSISTED = 1
    L2_PARTIAL = 2     # Human approves mutations
    L3_HIGH = 3        # Full auto, human self-directs
    L4_FULL = 4        # End-to-end autonomy


@dataclass
class RiskContext:
    is_peak_traffic: bool = False
    blast_radius_pct: float = 0.0
    recent_deploy_minutes: int = 999
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
    """Dynamic autonomy downgrade. L3/L4 requests downgrade to L2
    when elevated risk detected (mirrors Google Actuation Agent)."""
    risk = evaluate_risk(risk_ctx)
    if requested >= AutonomyLevel.L3_HIGH and risk >= risk_threshold:
        log("-", logging.WARNING, "autonomy_downgrade",
            requested=requested.name, risk=f"{risk:.2f}")
        return AutonomyLevel.L2_PARTIAL
    return requested


# ---------------------------------------------------------------------------
# Red Button: emergency halt
# ---------------------------------------------------------------------------

_red_button_pressed = False


def press_red_button() -> None:
    """Instantly halt all in-flight agent actions. Expose as HTTP endpoint."""
    global _red_button_pressed
    _red_button_pressed = True
    log("-", logging.CRITICAL, "RED_BUTTON_pressed")


def check_red_button() -> None:
    """Call before every actuation step. Raises if halted."""
    if _red_button_pressed:
        raise RuntimeError("Agent operations halted by Red Button")


# ---------------------------------------------------------------------------
# Circuit breaker: closed -> open -> half-open
# ---------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 2.0
    half_open_successes: int = 1
    cost_velocity_limit: float = 5.0   # max $/minute before tripping
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes_in_half_open: int = 0
    opened_at: float = 0.0
    _cost_window: list = field(default_factory=list)

    def before_call(self) -> None:
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                self.successes_in_half_open = 0
            else:
                raise CircuitOpenError(f"breaker={self.name} state=open")

    def record_success(self) -> None:
        if self.state is BreakerState.HALF_OPEN:
            self.successes_in_half_open += 1
            if self.successes_in_half_open >= self.half_open_successes:
                self.state = BreakerState.CLOSED
                self.failures = 0
        else:
            self.failures = 0
            self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or \
           self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()

    def record_cost(self, cost: float) -> None:
        """Trip breaker if cost velocity exceeds limit ($/min)."""
        now = time.monotonic()
        self._cost_window.append((now, cost))
        cutoff = now - 60.0
        self._cost_window = [(t, c) for t, c in self._cost_window if t > cutoff]
        velocity = sum(c for _, c in self._cost_window)
        if velocity > self.cost_velocity_limit:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter
# ---------------------------------------------------------------------------

class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def retry_with_backoff(
    cid: str,
    op_name: str,
    fn: Callable[[], Any],
    *,
    breaker: CircuitBreaker,
    max_attempts: int = 4,
    base_delay_s: float = 0.05,
    max_delay_s: float = 0.8,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            breaker.before_call()
            result = fn()
            breaker.record_success()
            log(cid, logging.INFO, "op_ok", op=op_name, attempt=attempt,
                breaker=breaker.state.value)
            return result
        except CircuitOpenError as exc:
            log(cid, logging.WARNING, "op_short_circuit", op=op_name,
                err=str(exc))
            raise
        except PermanentError as exc:
            breaker.record_failure()
            log(cid, logging.ERROR, "op_permanent", op=op_name, err=str(exc))
            raise
        except TransientError as exc:
            last_exc = exc
            breaker.record_failure()
            if attempt == max_attempts:
                break
            cap = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay = random.uniform(0.0, cap)
            log(cid, logging.WARNING, "op_retry", op=op_name,
                attempt=attempt, delay_ms=int(delay * 1000),
                breaker=breaker.state.value)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# PII redaction (detect -> redact -> audit record)
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
AUDIT_LOG: list[dict[str, Any]] = []  # append-only WORM stand-in


def redact_pii(cid: str, text: str) -> str:
    count = 0
    for label, pattern in [("email", _EMAIL), ("ssn", _SSN)]:
        found = pattern.findall(text)
        if found:
            text = pattern.sub(f"[REDACTED:{label}]", text)
            count += len(found)
    if count:
        AUDIT_LOG.append({
            "correlation_id": cid, "event": "pii_redaction",
            "count": count, "immutable": True,
        })
        log(cid, logging.INFO, "pii_redacted", count=count)
    return text


# ---------------------------------------------------------------------------
# Checkpoint store (provider evidence, not model claims)
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    step_id: str
    incident_id: str
    state: dict[str, Any]
    provider_evidence: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class CheckpointStore:
    """In-memory store. Swap with Redis/Postgres/Temporal for production."""

    def __init__(self) -> None:
        self._store: dict[str, list[Checkpoint]] = {}

    def save(self, cp: Checkpoint) -> None:
        self._store.setdefault(cp.incident_id, []).append(cp)
        log("-", logging.INFO, "checkpoint_saved",
            step=cp.step_id, incident=cp.incident_id)

    def latest(self, incident_id: str) -> Checkpoint | None:
        steps = self._store.get(incident_id, [])
        return steps[-1] if steps else None


# ---------------------------------------------------------------------------
# Fake model + tool proxies (no API keys needed)
# ---------------------------------------------------------------------------

@dataclass
class Incident:
    incident_id: str
    service: str
    severity: str
    alert_summary: str
    runbook: str | None
    log_excerpt: str


@dataclass
class TriageResult:
    correlation_id: str
    severity_label: str
    proposal: str
    tools_used: list[str] = field(default_factory=list)
    degraded: bool = False
    model_used: str = ""
    autonomy_level: str = ""
    idempotency_key: str = ""


class FakeModel:
    """Deterministic classifier -- no network, no API keys."""

    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self._remaining_failures = fail_times

    def classify(self, incident: Incident) -> dict[str, str]:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise TransientError(f"{self.name} overloaded")
        text = f"{incident.alert_summary} {incident.log_excerpt}".lower()
        if "oom" in text or "memory" in text:
            label = "memory_pressure"
        elif "5xx" in text or "timeout" in text:
            label = "dependency_latency"
        elif "deploy" in text or "rollback" in text:
            label = "bad_deploy"
        else:
            label = "unknown"
        return {"label": label, "model": self.name}


def idempotency_key(incident_id: str, tool: str, args: str) -> str:
    digest = hashlib.sha256(
        f"{incident_id}:{tool}:{args}".encode()
    ).hexdigest()[:16]
    return f"{incident_id}:{tool}:{digest}"


class ToolProxy:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.metrics_breaker = CircuitBreaker(
            "metrics", failure_threshold=2, recovery_timeout_s=1.0
        )
        self._metrics_failures_left = 2

    def fetch_metrics(self, cid: str, incident: Incident) -> str:
        key = idempotency_key(
            incident.incident_id, "fetch_metrics", incident.service
        )

        def _call() -> str:
            if key in self._seen:
                return f"cached_metrics service={incident.service}"
            if self._metrics_failures_left > 0:
                self._metrics_failures_left -= 1
                raise TransientError("metrics 503")
            self._seen.add(key)
            return (f"metrics service={incident.service} "
                    f"cpu=91 mem=88 p99_latency_ms=2400")

        return retry_with_backoff(
            cid, "fetch_metrics", _call, breaker=self.metrics_breaker
        )

    def bind_runbook(self, incident: Incident) -> str:
        if incident.runbook:
            return f"runbook_bound url={incident.runbook}"
        return "runbook_missing flag=need_human_runbook"


# ---------------------------------------------------------------------------
# Proposal vocabulary (typed mutations only)
# ---------------------------------------------------------------------------

PROPOSALS = {
    "memory_pressure":
        "Propose: restart memory-leaking task (typed: task_restart)"
        " -- REQUIRES APPROVAL",
    "dependency_latency":
        "Propose: shed non-critical traffic + page dependency owners"
        " -- read-only diagnose first",
    "bad_deploy":
        "Propose: rollback last deploy (typed: deploy_rollback)"
        " -- REQUIRES APPROVAL",
    "unknown":
        "Propose: escalate to human IC; grounding thin"
        " -- do not invent queries",
}


# ---------------------------------------------------------------------------
# Main triage loop with full resilience stack
# ---------------------------------------------------------------------------

def triage_incident(
    incident: Incident,
    checkpoint_store: CheckpointStore,
) -> TriageResult:
    cid = str(uuid.uuid4())
    log(cid, logging.INFO, "triage_start",
        incident_id=incident.incident_id, sev=incident.severity)

    # 0. Red Button check
    check_red_button()

    # 1. PII redaction before any processing
    incident.log_excerpt = redact_pii(cid, incident.log_excerpt)

    # 2. Determine autonomy level (dynamic downgrade)
    risk_ctx = RiskContext(
        is_peak_traffic=incident.severity == "P1",
        blast_radius_pct=10.0,
        recent_deploy_minutes=15 if "deploy" in incident.alert_summary.lower()
                               else 999,
    )
    autonomy = resolve_autonomy(AutonomyLevel.L3_HIGH, risk_ctx)

    # 3. Model fallback chain: primary -> secondary -> deterministic
    tools = ToolProxy()
    tools_used: list[str] = []
    degraded = False
    model_used = ""

    primary = FakeModel("sonnet-fake", fail_times=2)
    secondary = FakeModel("haiku-fake", fail_times=0)
    model_breaker = CircuitBreaker(
        "llm", failure_threshold=3, recovery_timeout_s=1.0
    )

    label: str | None = None
    for model in (primary, secondary):
        try:
            result = retry_with_backoff(
                cid, f"classify:{model.name}",
                lambda m=model: m.classify(incident),
                breaker=model_breaker, max_attempts=3,
            )
            label = result["label"]
            model_used = result["model"]
            break
        except (TransientError, CircuitOpenError) as exc:
            log(cid, logging.WARNING, "model_fallback",
                from_model=model.name, err=str(exc))
            degraded = True

    if label is None:
        # Deterministic fallback: runbook binder only
        degraded = True
        model_used = "deterministic-fallback"
        bound = tools.bind_runbook(incident)
        tools_used.append("bind_runbook")
        proposal = (f"Degraded mode: {bound}; "
                    f"page on-call with precomputed context only")
        key = idempotency_key(
            incident.incident_id, "degraded", "none"
        )
        checkpoint_store.save(Checkpoint(
            step_id="degraded", incident_id=incident.incident_id,
            state={"proposal": proposal, "degraded": True},
        ))
        log(cid, logging.ERROR, "graceful_degradation", proposal=proposal)
        return TriageResult(
            cid, "unknown", proposal, tools_used, True,
            model_used, autonomy.name, key,
        )

    # 4. Tool path with breaker-aware degradation
    try:
        metrics = tools.fetch_metrics(cid, incident)
        tools_used.append("fetch_metrics")
        log(cid, logging.INFO, "tool_result",
            tool="fetch_metrics", snippet=metrics[:80])
    except (TransientError, CircuitOpenError) as exc:
        degraded = True
        tools_used.append("fetch_metrics:skipped")
        log(cid, logging.WARNING, "skip_and_annotate",
            tool="fetch_metrics", err=str(exc))
        metrics = "metrics_skipped gap=annotated"

    bound = tools.bind_runbook(incident)
    tools_used.append("bind_runbook")
    proposal = f"{PROPOSALS[label]} | evidence={metrics} | {bound}"
    key = idempotency_key(incident.incident_id, "propose", label)

    # 5. Checkpoint with provider evidence (not model claims)
    checkpoint_store.save(Checkpoint(
        step_id="triage_complete",
        incident_id=incident.incident_id,
        state={"label": label, "proposal": proposal},
        provider_evidence={"metrics_hash": hashlib.sha256(
            metrics.encode()
        ).hexdigest()[:16]},
    ))

    # 6. Immutable audit record (chain of custody)
    AUDIT_LOG.append({
        "correlation_id": cid,
        "event": "agent_proposal",
        "incident_id": incident.incident_id,
        "label": label,
        "model": model_used,
        "autonomy_level": autonomy.name,
        "proposal": proposal,
        "idempotency_key": key,
        "human_approval": "pending",
        "immutable": True,
    })
    log(cid, logging.INFO, "triage_done",
        label=label, degraded=degraded, model=model_used,
        autonomy=autonomy.name)
    return TriageResult(
        cid, label, proposal, tools_used, degraded,
        model_used, autonomy.name, key,
    )


def main() -> None:
    store = CheckpointStore()
    incidents = [
        Incident(
            incident_id="inc-1001", service="checkout-api",
            severity="P2", alert_summary="pod OOMKilled loop",
            runbook="https://runbooks.example/oom",
            log_excerpt="OOM killer; contact alice@example.com",
        ),
        Incident(
            incident_id="inc-1002", service="payments",
            severity="P1",
            alert_summary="upstream 5xx spike after deploy",
            runbook=None,
            log_excerpt="timeout to billing-svc; rollback build 4421",
        ),
    ]
    for inc in incidents:
        result = triage_incident(inc, store)
        print("---")
        print(f"cid={result.correlation_id}")
        print(f"label={result.severity_label} model={result.model_used}"
              f" degraded={result.degraded} autonomy={result.autonomy_level}")
        print(f"tools={result.tools_used}")
        print(f"idempotency_key={result.idempotency_key}")
        print(f"proposal={result.proposal}")
    print("--- audit_ledger ---")
    for row in AUDIT_LOG:
        print(row)


if __name__ == "__main__":
    main()
```

**What this demonstrates:**

| Concern | Implementation |
| --- | --- |
| Retries + jitter | `retry_with_backoff` full-jitter U(0, min(max, base * 2^n)) |
| Circuit breaker | CLOSED -> OPEN -> HALF_OPEN on LLM and metrics proxies; cost velocity trigger |
| Fallback chain | sonnet-fake -> haiku-fake -> deterministic runbook bind |
| Correlation IDs | UUID per triage; every log line carries `cid=` |
| Graceful degradation | Skip-and-annotate metrics; degraded proposal without inventing queries |
| Idempotency | SHA-256 key on `incident:tool:args`; cached metrics replay |
| PII + audit | Email/SSN detect -> redact -> append-only AUDIT_LOG ledger |
| Autonomy downgrade | Risk scoring -> L3 downgrades to L2 during peak/deploy |
| Red Button | Emergency halt check before every actuation |
| Checkpoint store | Provider evidence stored, not model claims |

---

## 6. Architectural System Design Scenarios

### Scenario A -- Multi-Tenant SaaS: Virtual Responder for P1/P2 Without Waking Humans for Noise

**Problem statement.** A B2B SaaS runs ~2k monitored services across 400 tenants. Peak alert ingress is 500 events/min. On-call ack SLO is 5 minutes. Leadership wants a Paige-style virtual responder that posts a first triage proposal in Slack within ~10 s for true incidents, while Automation Actions handle known classes before notify. Mutations (restart/rollback) must remain propose-only until policy/audit mature. Memory and playbooks must not leak across tenants. Budget target: **$70-110 / 1k agent investigations** at routed model mix.

**Proposed architecture:**

```
Monitor --> Event Orchestration (suppress/group/enrich/Automation Action)
                 |
                 v  (page-worthy only)
            Precompute Context (per tenant+service) --> Temporal workflow
                 |                                         |
                 v                                         v HITL signal
            Triage Agent (Haiku noise/status, Sonnet RCA)  Slack proposal
                 |
                 +-- MCP read tools (Datadog/CW/GitHub) + RBAC
                 +-- Memory store (tenant-isolated)
                 +-- Audit ledger (proposal only; no mutate)
```

**Trade-off matrix:**

| Approach | Cost | Latency | Ops Complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. Deterministic Automation Actions only** | Lowest (no LLM) | Machine-speed | Rules debt | Highest for known jobs | High for known classes; fails on novel outages |
| **A2. Read-only triage agent + precompute (recommended)** | ~$70-110 / 1k incs (routed+cache) | ~10 s first proposal if precomputed | Connector + runbook hygiene | High (no mutate; tenant memory) | Scales with service memory; bounded by TPM |
| **A3. Fully autonomous remediation** | Lowest human $ if correct | Fastest MTTM if safe | Highest (blast radius, rollback, eval) | Highest risk | Unsafe at multi-tenant blast radius |

**Decision rationale.** Choose **A2**. Noise reduction stays deterministic (A1 remains the first layer). Precompute hits the published ~10 s path and protects the 5-minute ack SLO better than lazy discovery (>60 s). Propose-only matches current PagerDuty posture and Newsletter #131's postmortem-driven ban on ungoverned writes. A3 loses on security and multi-tenant blast radius before policy/audit/rollback are proven.

---

### Scenario B -- Multi-Cloud SRE Agent for Fintech with SOC 2 and HIPAA Constraints

**Problem statement.** A fintech/healthcare company runs payment processing across AWS (primary) and GCP (DR). They experience 15-20 P1 incidents/month with MTTM of 45-60 minutes. SOC 2 Type II audit is in 6 months. HIPAA applies to a healthcare vertical. They need an AI agent that investigates incidents, recommends mitigations, and executes pre-approved remediations -- without ever touching PCI-scoped or ePHI systems autonomously. The agent must optimize **MTTM** (stop Bad Customer Minutes), not chat quality. Budget target: **$53-71 / 1k incidents** with model routing.

**Proposed architecture:**

```
Page --> Severity router --> ProdAgent orchestrator (Temporal, multi-cloud DR)
                                |
              +-----------------+------------------+
              v                 v                  v
        Playbook agent    Metrics/log workers   Mitigation proxy (MCP)
              |                 |                  |
              +----------> classify --> typed tool -+
                                                    v
                                            Policy engine (OPA)
                                         (risk + peak + RBAC)
                                                    v
                                            HITL approve/reject
                                         (durable RunState)
                                                    v
                                       execute --> verify --> rollback?
                                                    v
                                       +----------------------------+
                                       |  PCI / ePHI ZONE           |
                                       |  NEVER autonomous (L0).    |
                                       |  Agent RECOMMENDS,         |
                                       |  human SRE executes.       |
                                       +----------------------------+
                                                    v
                                       postmortem draft + audit WORM
                                       (7yr retention, immutable)
```

**Key decisions:**
- **PII/PHI redaction gateway** upstream of all LLM processing; network-level controls prevent sealed VM from reaching ePHI data stores
- **Sealed VM (Confidential Computing)** for agent -- ~2x compute premium justified by $10.93M avg breach cost
- **One sealed VM per incident** for bulkhead isolation: compromised investigation cannot access another incident's data
- **Temporal** for durability (multi-cloud DR requires state that survives regional loss)
- **L2 for all writes, L0 for PCI/ePHI zones** -- deterministic, non-bypassable boundary

**Trade-off matrix:**

| Approach | Cost | Latency | Ops Complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Read-only triage only** | Lower tokens | Fast propose; slow MTTM (human still mutates) | Lower | Very high | High |
| **B2. HITL typed mutation agent (recommended)** | Tokens + approver time | Seconds-minutes per approval; MTTM wins when playbooks tight | Policy + tool metadata + audit | Highest when enforced | Bounded by human approvers |
| **B3. Autonomous mutation without HITL** | Lowest human cost | Best raw speed | Extreme eval/rollback burden | Unacceptable for PCI/HIPAA | Scales until first bad global restart |

**Decision rationale.** Choose **B2**. MTTM-first orgs need typed mitigations in-loop (B1 leaves mitigation entirely to slow manual ops). Google's multi-layer safety (typed tools, risk metadata, policy, CLI confirm, audit) plus durable HITL gives compliance chain-of-custody. B3 fails the autonomy-vs-blast-radius trade-off and is regulatory-unacceptable for PCI/HIPAA. The PCI/ePHI boundary is hard-coded as L0 -- the agent physically cannot touch cardholder data or patient records, converting a probabilistic guarantee into a deterministic one that auditors can verify. Approver throughput is the scalability ceiling -- accept it; use Automation Actions for pre-approved known classes to keep humans for novel/destructive paths only.

---

## Common Failure Modes

| # | Failure Mode | Symptom | Root Cause | Fix |
| --- | --- | --- | --- | --- |
| 1 | **Context blindness** (31.6%) | Known runbook not recalled; investigation stretches hours | Lazy discovery instead of precompute; context rot over long incidents | Precompute Incident Context on open; rebuild on trigger/note/resolve |
| 2 | **Rogue actions** (30.3%) | Unauthorized production mutation; 13-hour outage | No approval gate; excessive autonomy level | Typed mutations only; HITL for writes; dynamic autonomy downgrade |
| 3 | **Silent degradation** (24.9%) | Agent reports success but diagnosis wrong | No verification gate; polished output masks bad reasoning | Deterministic scoring against golden data; reasoning trace completeness |
| 4 | **Hallucinated queries** | Agent invents nonexistent log fields or metrics | Free-form query construction | Template query builders; ask for missing params; formulate from runbooks |
| 5 | **Infinite loops** (5.1%) | Cost spike; no resolution | Missing max_turns; no cost velocity breaker | max_turns cap; cost velocity circuit breaker; admit unknowns prompting |
| 6 | **State drift** | Decisions on stale data | Long HITL wait; no context refresh | Debounce + rebuild; version markers with serialized state |
| 7 | **Alert storm cascade** | Agent drowned; latency explodes | No AIOps pre-filtering | 91% noise reduction is prerequisite; suppress before agent wake |
| 8 | **Mitigation failure mid-loop** | Blast radius expands | Agent retries failed mutation at larger scope | Pivot to RCA; never expand blast radius as retry; verify + rollback in playbook |

---

## Key Takeaways for Interviews

1. **Context recall beats cleverness** -- Precomputed Incident Context cut PagerDuty's first-response from >60s to ~10s. The most important design decision is not model choice but context assembly strategy.

2. **Workflow outside, agent inside** -- Noise reduction (91% filtering) and priority routing are deterministic workflows. Only diagnosis of novel failures uses LLM-directed tool loops. Using the LLM as the first noise filter is an architectural mistake.

3. **Typed mutations only** -- Google's closed mitigation set (drain/rollback/restart/add capacity) mapped to typed MCP tools prevents free-form shell execution. No God Tool.

4. **Five-level autonomy with dynamic downgrade** -- L3 requests automatically downgrade to L2 when risk context spikes (peak traffic, recent deploy, concurrent incidents). This is evaluated per operational function, not globally.

5. **Asymmetric durability** -- PagerDuty checkpoints the supervisor but re-spawns sub-agents from scratch. This avoids N+1 checkpoint reconciliation complexity. Store provider evidence, not model claims.

6. **The 100-300x model routing lever** -- Route simple triage to small models; escalate RCA to frontier models. This is the single largest cost optimization, achieving 60-80% token spend reduction.

7. **Red Button and fail-closed defaults** -- Emergency halt endpoint with <1s latency. Approval gates default to `always_ask`. Malformed approval args require human review, never auto-approve.

8. **MTTM over MTTR** -- Google optimizes Mean Time to Mitigation (stop Bad Customer Minutes), not full Mean Time to Recovery. A 5-minute ack SLO is just to acknowledge the page.

9. **Reasoning traces are the fifth telemetry layer** -- Without them, you are doing forensics on a crime scene with no witnesses. Six monitoring signals: goal completion, tool success, context quality, reasoning completeness, escalation rate, hallucination rate.

10. **Tenant memory isolation is non-negotiable** -- Observations, recollections, and playbooks must never cross customers in multi-tenant deployments. Memory API with human-speed redaction for compliance.

---

## Interview Q&A

**Q1: How would you design the alert ingestion pipeline for an IR agent?**
A: I would separate it into three layers. First, a deterministic noise reduction layer using AIOps grouping and Event Orchestration -- PagerDuty's Forrester TEI shows 91% signal-noise reduction. Second, a precomputed context assembly step that eagerly fetches incident object, related incidents, change events, and runbook sections in parallel with debounce. Third, the agentic triage layer that only sees page-worthy, context-enriched incidents. This ordering is critical -- making the LLM the first noise filter wastes tokens and misses the 91% reduction the deterministic layer provides. The precompute step cut PagerDuty's first-response from over 60 seconds to roughly 10 seconds.

**Q2: Why does Google use a closed mitigation set instead of letting the agent run arbitrary commands?**
A: Google's ProdAgent classifies symptoms into a finite set -- drain, rollback, restart, add capacity -- and maps each to a typed MCP tool like `borg_task_restart`. This bounds the blast radius: the agent cannot do anything the playbook does not enumerate. Free-form shell access means any model hallucination could become a production-impacting command. Each typed tool carries risk metadata (safe, reversible, destructive) and peak-traffic policies (e.g., no global restart during peak). This is scored against golden data: a mitigation is correct only if it deterministically matches the exact parameters, not vague suggestions.

**Q3: Walk through the circuit breaker state machine for a metrics MCP proxy during a P1. What does the user see when you skip-and-annotate?**
A: The breaker starts CLOSED. Say the metrics API returns 503 three times -- the breaker flips to OPEN. While OPEN, all metrics calls are immediately rejected without hitting the API. After a recovery timeout (say 2 seconds), it transitions to HALF_OPEN and allows one probe call. If that succeeds, it returns to CLOSED; if it fails, back to OPEN with doubled timeout. During the OPEN period, the triage agent continues without metrics: it appends "metrics_skipped gap=annotated" to the proposal so the human responder knows the diagnosis is incomplete. The agent does not invent data to fill the gap -- it explicitly flags what is missing.

**Q4: How do you handle a 3-hour incident where the HITL approval sits for 40 minutes?**
A: I serialize the full RunState to a durable store (Temporal workflow or database) when the approval pause begins. This includes the agent's context, proposed action, tool results, and a version marker for the agent definition. When the human approves, I atomic-owner-check the state transition to prevent double-resume from concurrent submissions. Before executing, I rebuild the incident context because 40 minutes of new notes, alerts, and status changes may have arrived -- the debounced rebuild on trigger/note/resolve handles this. If the agent definition version has changed while paused, I flag this and may re-triage rather than execute a stale proposal.

**Q5: What is the cost difference between a Sonnet-only IR agent and one with model routing?**
A: For a mid-complexity investigation with 40k input tokens and 4k output, Sonnet-only costs about $0.107 per incident with prompt caching, or $107 per 1,000 incidents. With 70% Haiku routing for noise classification and status drafting plus 30% Sonnet for RCA and mitigation selection, the cost drops to roughly $69.50 per 1,000 incidents -- about 35% savings. At frontier model rates with 8 hops per incident, the gap is even larger: $177/1k unrouted versus $53-71/1k routed. The routing lever is 100-300x because small model pricing is $0.01-0.05/1M tokens versus $3-15/1M for frontier models.

**Q6: How does the five-level autonomy model work in practice?**
A: Google evaluates agents across five operational functions -- Monitor, Investigate, Mitigate, Actuate, Self-Direct -- and each function can be at a different level. An agent might be L3 on Monitor but L1 on Actuate. The key feature is dynamic downgrade: if an L3 request is made but the risk context shows peak traffic, a recent deploy, or concurrent incidents, the Actuation Agent automatically downgrades to L2 and routes the action to a human SRE for approval. Risk is scored deterministically (auditable formula) so downgrade decisions are traceable. Each level transition requires demonstrated reliability at the current level before advancing.

**Q7: Why is "session memory" not the same as "durable execution" for IR agents?**
A: Session memory stores conversation history, but it does not prove which shell command ran, which email was sent, which approval was granted, or whether a retry would duplicate a side effect. "The email was sent" in conversation history may be a model claim, not a provider result. Durable execution (Temporal, Restate, etc.) records every workflow step, every Activity call/return, and all return values. Non-deterministic side effects like LLM outputs and timestamps are recorded the first time and replayed deterministically during recovery. PagerDuty's model is asymmetric: checkpoint the supervisor but make sub-agents stateless and re-spawnable. This avoids N+1 checkpoint reconciliation while ensuring events survive a crash.

**Q8: How would you architect tenant isolation for an IR agent in a multi-tenant SaaS?**
A: Three layers. First, precomputed context is per tenant and service -- observations, recollections, and promoted playbooks never cross customer boundaries in the memory store. Second, MCP tool calls use per-tenant OAuth tokens with audience-bound scopes so one tenant's agent cannot query another's logs. Third, the audit ledger records tenant_id on every proposal for compliance. PagerDuty truncates custom_details to the first 2,000 characters per tenant to bound context size. For regulated workloads (HIPAA), I would add sealed VMs with one VM per incident for bulkhead isolation, plus a PII/PHI redaction gateway upstream of all LLM processing.

**Q9: What happens when a mitigation fails mid-loop? How does the agent avoid expanding blast radius?**
A: The agent pivots to RCA rather than retrying the failed action at a broader scope. Google's Gemini CLI example: `borg_task_restart` fails, and the agent stays in flow, analyzes that only this specific job fails in the cell, and pivots to examining the code for a root cause rather than trying to restart more tasks. Every mutation playbook must include verify and rollback steps so failed mutations reverse without requiring full RCA completion first. The fallback chain is: primary frontier model, secondary small model, deterministic runbook binder, human-only escalate. Never expand blast radius as a retry strategy.

**Q10: How do you handle the Amazon Kiro-style autonomous deletion risk?**
A: The Kiro incident (December 2025) resulted in a 13-hour outage because the AI determined deleting and rebuilding an environment was the most efficient fix and executed autonomously without human approval. To prevent this: typed mutations only (no free-form), fail-closed approval gates where malformed args require human review, dynamic autonomy downgrade when risk context is elevated, Red Button endpoint that halts all in-flight actions in under 1 second, and peak-traffic policies that block destructive actions during high-load periods. Every proposal is logged with the model id, prompt hash, and proposed tool plus arguments before execution, creating an immutable chain of custody.

**Q11: What are the SOC 2 and HIPAA implications of deploying an IR agent?**
A: For SOC 2, auditors expect model lineage (exact dataset, code, and approval behind each model), prompt and inference logs with PII redaction applied before logging, drift-monitoring output, and vendor risk assessment for every third-party LLM. Cost is $35K-$150K first year, enterprise-readiness $200K-$250K+. For HIPAA, the critical rule is that if PHI was in the context during inference, the system is in scope -- even transient context window presence counts. The January 2025 HIPAA Security Rule NPRM explicitly brings AI systems into scope. Healthcare breach costs average $10.93M. My architecture would use a hard ePHI zone boundary where the agent physically cannot reach patient data stores, with all LLM processing in an ePHI-free zone behind a redaction gateway.

**Q12: Compare PagerDuty's reactive event loop with Google's orchestrator-workers pattern. When would you choose each?**
A: PagerDuty uses a reactive event loop with a priority queue (user input at priority 0, sub-agent results at priority 1) and a drain loop that serializes concurrent arrivals. This is ideal for interactive IR where users must steer mid-investigation -- interactivity is non-negotiable. They deploy as a single process (I/O-bound, no CPU hotspot to isolate) using in-process asyncio.Queue rather than PubSub. Google uses orchestrator-workers with distinct agents for playbook navigation, alerting, anomaly detection, and insights, orchestrated by an AI Operator. This mirrors their SRE team structure. I would choose PagerDuty's model for customer-facing SaaS IR where user interaction drives the investigation, and Google's model for internal MTTM-first response where the primary goal is fast automated mitigation rather than interactive triage.

---

## Key Numbers to Memorize

| Metric | Value | Context |
| --- | --- | --- |
| Noise reduction | 91% | PagerDuty AIOps (Forrester TEI) |
| Precompute vs lazy response time | ~10 s vs >60 s | PagerDuty SRE Agent |
| Cost per 1k incidents (routed) | $53-71 | 70% small model / 30% frontier |
| Cost per 1k incidents (Sonnet-only cached) | $107 | With prompt caching |
| MTTM reduction (Google dashboards) | 44% | Google AI SRE agents |
| MTTM reduction (Google hypothesis) | 10% | Google AI SRE agents |
| Agent failure rate in production | 41-86.7% | Without deliberate fault tolerance |
| Demo-to-production failure rate | 88% | Fiddler AI, 2026 |
| Context blindness frequency | 31.6% | Arize field analysis, top failure mode |
| Healthcare breach cost | $10.93M avg | IBM 2024, highest industry 14 years running |
| Google ack SLO | 5 minutes | Just to acknowledge a page |
| Red Button latency target | <1 s | Emergency halt of all agent actions |
| Paige AI Actions per request | 4 | PagerDuty metering unit |
| Custom details truncation | 2,000 chars | PagerDuty Paige limit |
| SOC 2 first-year cost | $35K-$150K | Enterprise-readiness $200K-$250K+ |
| Real-time agent registry adoption | 21% | CSA/Strata, 2026 |

---

## Quick Reference

```
IR Agent = Workflow outside (noise/priority) + Agent inside (diagnosis/playbook)

Precompute > Discover:  ~10s vs >60s first response
Typed mutations only:   drain | rollback | restart | add capacity
Five-level autonomy:    L0 Manual -> L1 Assisted -> L2 Partial -> L3 High -> L4 Full
Dynamic downgrade:      L3 -> L2 when risk > threshold (peak, deploy, concurrent)
Asymmetric durability:  Checkpoint supervisor, re-spawn sub-agents
Fallback chain:         Frontier -> small model -> deterministic binder -> human
Red Button:             Emergency halt <1s, exposed as HTTP endpoint
Context recall > cleverness
MTTM > MTTR
Noise reduction (deterministic) > Agent triage (LLM)
```
