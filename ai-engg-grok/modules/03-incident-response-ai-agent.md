# Module 03 — How to Design an Incident Response AI Agent

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 03 (IR-specialized agent after general agent runtime foundations)  
**Grounded in**: `research/03-incident-response-ai-agent.md` (34 sources, 2026-09-29)

Sibling modules cover specs (01) and general agent runtimes (02). This module focuses on **incident-response (IR) agents**: alert ingestion → noise reduction → precomputed context → triage → runbooks/tools → human approval → blast-radius limits → on-call SLAs (MTTA / MTTM). Anthropic’s rule still holds: use a **workflow** when the IR path is fixed (suppress, enrich, known Automation Action); use an **agent** when diagnosis is unpredictable ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Published IR systems (PagerDuty Paige, Google ProdAgent, AWS Security IR investigative agent) share one architectural bias: **context recall beats cleverness**, and mutations stay behind policy + HITL until guardrails are proven ([PagerDuty Eng — Context Over Cleverness](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                    │
│  Alert ingress (PagerDuty Event Orchestration / EventBridge case create)    │
│  AIOps group/suppress/enrich · priority rules · virtual-responder placement │
│  Approval gates (needs_approval / always_ask) · risk/policy metadata        │
│  Durable run state for paused HITL · max_turns / iteration caps             │
│                                                                             │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────────────┐  │
│  │ Event        │  │ Incident       │  │ HITL / Policy                   │  │
│  │ Orchestration│──│ Workflows      │──│ approve · reject · 2-person     │  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────┬────────────────┘  │
└─────────┼──────────────────┼────────────────────────────┼───────────────────┘
          │                  │                            │
          ▼                  ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DATA PLANE                                      │
│  LLM reasoning turns · classify symptoms · select typed playbook            │
│  Observation append (log lines, CloudTrail, Slack digests, related incs)    │
│  Precomputed Incident Context → first triage proposal (~10 s path)          │
└───┬───────────────────────────────┬───────────────────────────────┬─────────┘
    │                               │                               │
    ▼                               ▼                               ▼
┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
│   TOOL PROXIES    │   │     PERSISTENCE       │   │      TELEMETRY          │
├───────────────────┤   ├───────────────────────┤   ├─────────────────────────┤
│ MCP HTTPS+OAuth   │   │ Precomputed context   │   │ MTTA / MTTM / Bad CM    │
│ schema validate   │   │ HITL RunState (JSON)  │   │ token/$ · AI Actions×4  │
│ typed mitigations │   │ Temporal / workflow   │   │ proposal+approval audit │
│ Datadog/CW/GitHub │   │ checkpoints + DLQ     │   │ correlation IDs         │
│ risk: safe|rev|   │   │ Memory: obs/recollect │   │ skip-and-annotate gaps  │
│   destructive     │   │   /playbook (tenant)  │   │ circuit-breaker state   │
│ least-priv RBAC   │   │ idempotency keys      │   │ CloudTrail / immutable  │
└───────────────────┘   └───────────────────────┘   └─────────────────────────┘
```

**Plane responsibilities (IR-specific)**

| Plane | IR role | Truth source |
| --- | --- | --- |
| **CONTROL PLANE** | Ingress, escalation placement, approval gates, policy/risk on tools, durable paused HITL, audit of proposed vs approved | [PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages) |
| **DATA PLANE** | LLM turns + MCP/tool calls against logs/metrics/runbooks; observations appended into working context | [Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work); [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) |
| **PERSISTENCE** | Precomputed Incident Context; serialized `RunState`; Temporal/workflow history; tenant-scoped memory (observations / recollections / playbooks) | [PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/) |
| **TOOL PROXIES** | Typed MCP tools (`fetch_playbook`, `borg_task_restart`, CloudTrail/IAM queries); schema validation; external-signal bias | [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent); [Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html) |
| **TELEMETRY** | Ack/mitigation clocks, token/$ and AI Action meters, proposal+approval chain, correlation IDs, skip annotations | Google 5-min ack SLO / MTTM; Paige 4 AI Actions; CloudTrail attribution |

### End-to-end request-flow narrative

1. **Ingest (control plane)** — Monitoring emits to PagerDuty Global Integration / Event Orchestration, or GuardDuty/Security Hub → EventBridge → AWS Security Incident Response case ([PagerDuty AIOps Quickstart](https://support.pagerduty.com/main/docs/pagerduty-aiops-quickstart-guide); [AWS Security Blog](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)).
2. **Noise / priority (control plane, deterministic)** — AIOps grouping + Event Orchestration severity rules; Forrester TEI (PagerDuty-commissioned) reports **91%** signal-noise reduction for studied Operations Cloud customers ([PagerDuty Forrester TEI](https://www.pagerduty.com/newsroom/forrester-tei/)). This layer is **not** the LLM’s job.
3. **Precompute Incident Context (persistence → data plane)** — On open, assemble incident object, raw alert payloads, Past/Related/Outlier incidents, Related Change events, runbook sections. Lazy PD API discovery prototypes ran **>60 s** first response with order-dependent answers; precompute cut first-response latency to **~10 s** ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
4. **Virtual responder engage** — Paige via Incident Workflow on trigger/priority, or escalation-level parallel investigate with humans ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
5. **Reason (data plane)** — Model classifies symptoms against closed mitigation sets (Google: drain/rollback/restart/add capacity → typed playbook like `borg_task_restart`) or drafts triage proposal from precomputed context ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
6. **Act via tool proxies** — Bias-to-relevance: external observability/knowledge only; PagerDuty facts come from precomputed context—no inward PD API fishing. Auto-search logs only when provider, target, and window are clear; otherwise **propose** the query ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
7. **Approval interrupt (control plane)** — Mutations: `needs_approval` / Anthropic MCP default `always_ask` / Google risk metadata + 2-person policy. Pause → serialize `RunState` to durable store → approve/reject → resume ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Anthropic permission policies](https://platform.claude.com/docs/en/managed-agents/permission-policies)).
8. **Observe / pivot** — On mitigation failure, stay in flow and pivot (Google: `borg_task_restart` fails → cell-local RCA instead of expanding blast radius) ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
9. **Checkpoint & learn** — Debounced context rebuild on trigger/note/resolve; promote recollections → versioned playbooks with normalized alert signatures; Memory API redaction at human speed ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
10. **Telemetry close** — Record proposed vs approved action, skip annotations for missing sources, correlation ID across turns; ICS 3Cs (coordinate/communicate/control) remain human-owned ([Google SRE Workbook](https://sre.google/workbook/incident-response/)).

Progressive topology from Newsletter #131: manual runbook → LLM assistant → MCP tools → Agent Skills → memory (`Agents.md`) → Agent SOPs (RFC 2119) → packaged agent → webhook/cron → multi-agent filesystem workspace ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)).

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

IR agents sit on top of the same ReAct loop as general agents (Thought → Action → Observation), but the **outer loop is workflow-shaped**: noise reduction and priority are deterministic; only diagnosis and playbook selection are agentic ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Google’s MTTM framing (Mean Time to Mitigation / stop Bad Customer Minutes) dominates MTTR as the optimization target; Core SRE typically holds a **5-minute acknowledge SLO** just to page-ack ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)). industry runbook automation targets **MTTA under 5 minutes** ([incident.io automated runbook guide](https://incident.io/blog/automated-runbook-guide)).

Orchestration patterns in published IR agents:

| Pattern | IR use |
| --- | --- |
| **Workflow (deterministic outer)** | Event Orchestration enrich/suppress/route; Automation Action before human notify; Incident Workflows auto-engage virtual responder ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration); [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) |
| **Agent (LLM-directed tool loop)** | ProdAgent: classify → `fetch_playbook` → typed mitigation; AWS investigative agent: clarify → CloudTrail/IAM/EC2/Cost Explorer → timeline ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)) |
| **Orchestrator–workers** | Distinct agents for playbook nav, alerting, anomaly, insights; Newsletter #131 Step 10 specialists under a manager ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations); [Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)) |
| **ICS / IMAG (human control plane)** | IC / Communications Lead / Ops Lead; agents summarize chat, draft handoffs/postmortems **on top of** IMAG ([Google SRE Workbook](https://sre.google/workbook/incident-response/)) |

### IR triage state machine

```
                 ┌────────────┐
                 │  INGEST    │
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │  NOISE_FX  │  AIOps group / suppress / enrich
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │ PRECOMPUTE │  incident + related + changes + runbook
                 └─────┬──────┘
                       ▼
              ┌────────┴────────┐
              ▼                 ▼
       ┌────────────┐    ┌────────────┐
       │  TRIAGE    │◄───│  SKIP_GAP  │  source slow → annotate & continue
       └─────┬──────┘    └────────────┘
             │
      ┌──────┼──────────────┬────────────────┐
      ▼      ▼              ▼                ▼
   PROPOSE  TOOL_READ   NEEDS_APPROVAL    MAX_TURNS
      │      │              │                │
      │      ▼              ▼                ▼
      │   OBSERVE     PAUSE_RUNSTATE     ESCALATE_HUMAN
      │      │              │
      │      └──────┬───────┘
      │             ▼
      │      ┌────────────┐
      │      │  MITIGATE  │  typed tool only (if approved)
      │      └─────┬──────┘
      │       ┌────┴────┐
      │       ▼         ▼
      │    VERIFY    ROLLBACK / PIVOT_RCA
      │       │
      └───────┴──► RESOLVE → MEMORY_PROMOTE
```

**Invariants**

1. **Noise before agent** — LLM is never the first filter for alert storms ([PagerDuty Forrester TEI](https://www.pagerduty.com/newsroom/forrester-tei/); research [inferred] framing).
2. **Precompute before reason** — Platform facts are assembled eagerly; tools fetch *external* evidence ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
3. **Typed mutations only** — Closed mitigation vocabulary; no free-form shell ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
4. **Fail-closed approvals** — Malformed `needs_approval` args require human review ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
5. **Tenant memory isolation** — Observations/recollections/playbooks never cross customers ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

### Algorithms & complexity

| Step | Algorithm | Complexity note |
| --- | --- | --- |
| Noise reduction | Rule/ML grouping over alert stream | O(alerts in window); must finish before agent wake |
| Context assemble | Parallel fan-out to PD/related/changes/runbook with debounce | Wall-clock dominated by slowest source; skip-and-annotate avoids O(∞) wait |
| Playbook bind | Deterministic URL/custom_details match, else opportunistic link follow | O(1) bind preferred over O(k) tool hunts |
| Symptom classify | LLM over closed label set → typed tool | Bounded tool cardinality; Skills loaded lazily to save context ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)) |
| Blast-radius check | Service graph + related incidents | Cap query windows; Paige truncates `custom_details`/notes to **first 2,000 characters** ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) |
| HITL resume | Atomic owner-checked state transition | Prevents double-resume on concurrent approval submissions ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |

**Convergence**: loops terminate on final proposal, verified mitigation, human reject, `max_turns`, or poison-pill quarantine. Error compounding is bounded by max-iteration guards and “admit unknowns” prompting ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).

---

## Part 3 — Token Economics & NFR Analysis

> ⚠️ Gap: No vendor publishes p50/p95/p99 end-to-end latency SLAs, RPM/TPM ceilings, or per-incident token budgets specifically for production IR agents. Available figures: first-response latency experiments (~10 s vs >60 s), AI Action metering, model list prices, and qualitative “within minutes” investigation claims ([research/03](../research/03-incident-response-ai-agent.md)).

### Cost formula — `$ per 1k incidents` (self-hosted IR agent)

**Assumptions** (Anthropic list prices as of research date; not a vendor IR benchmark):

| Model | Input $/MTok | Output $/MTok | Cache read $/MTok (5-min TTL) |
| --- | --- | --- | --- |
| Sonnet 5.5 | $2 | $10 | $0.20 |
| Haiku 4.5 | $1 | $5 | $0.10 |

([Anthropic pricing](https://www.anthropic.com/pricing#api))

**Mid-complexity investigation token budget** [inferred from research worked example]:

- Static prefix (system + tool schemas): **8k** tokens, **90%** prompt-cache hit rate after warm
- Dynamic input (precomputed context + runbook + 2–3 tool rounds): **32k** uncached input
- Output: **4k** tokens
- Routing mix: **70%** Haiku (noise/status), **30%** Sonnet (RCA/mitigation select) [inferred routing pattern]

**Per-incident cost (Sonnet-only baseline, matching research example):**

\[
\begin{align*}
C_{\text{uncached}} &= 40\,\text{k in} \times \$0.002/\text{k} + 4\,\text{k out} \times \$0.01/\text{k} \\
&= \$0.08 + \$0.04 = \$0.12
\end{align*}
\]

**With caching on 8k static prefix (90% hits):**

\[
\begin{align*}
C_{\text{cache}} &= (8\,\text{k} \times 0.9) \times \$0.0002/\text{k} + (8\,\text{k} \times 0.1 + 32\,\text{k}) \times \$0.002/\text{k} + 4\,\text{k} \times \$0.01/\text{k} \\
&= \$0.00144 + \$0.0656 + \$0.04 \approx \$0.107
\end{align*}
\]

**`$ per 1k incidents` (Sonnet-only, cached):** \(1000 \times \$0.107 = \mathbf{\$107}\)

**`$ per 1k incidents` (routed 70% Haiku / 30% Sonnet, same shape, cache on both)** [inferred]:

\[
C_{\text{routed}} \approx 0.7 \times \$0.0535 + 0.3 \times \$0.107 \approx \$0.0695 \Rightarrow \mathbf{\$69.50\ /\ 1k\ incidents}
\]

(Haiku half of Sonnet I/O rates → roughly half the Sonnet cached cost for the same token shape.)

**Platform metering (not raw tokens):** Paige consumes **4 AI Actions** per user request or nudge ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)). Anthropic Managed Agents: **$0.08 / session-hour** active runtime + token rates ([Anthropic pricing](https://www.anthropic.com/pricing#api)). AWS investigative agent: included with service; findings free tier **10,000/month** then volume tiers ([AWS Security Blog](https://aws.amazon.com/blogs/security/accelerate-investigations-with-aws-security-incident-response-ai-powered-capabilities/)).

### Latency — published anchors + [inferred] percentile budget

**Published (not percentiles):**

| Path | Figure | Source |
| --- | --- | --- |
| Precompute first response | **~10 s** | [PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/) |
| Lazy discovery prototype | **>60 s** | same |
| AWS summary | “Within **minutes**” | [AWS docs](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html) |
| Clarify timeout | **10 minutes** then auto-start | same |
| Google RCA demo narrative | “Under **two minutes**” | [Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages) — **demo, not SLA** |

**[inferred] Latency budget for first triage proposal (precompute path)** — component assumptions:

| Component | p50 | p95 | p99 | Notes |
| --- | --- | --- | --- | --- |
| Context fan-out (parallel) | 2_000 ms | 6_000 ms | 12_000 ms | debounce; skip slow source |
| TTFT (first model token) | 800 ms | 2_000 ms | 4_000 ms | frontier model, warm cache |
| Tool RTT × 1–2 reads | 400 ms | 2_500 ms | 8_000 ms | external observability |
| Proposal compose (remaining gen) | 1_200 ms | 3_000 ms | 5_000 ms | ~1–2k out tokens |
| Human ack (HITL path only) | N/A (async) | N/A | ≥ 5_min SLO | Google ack SLO; not in agent p99 |

**Arithmetic (agent-side, excluding human ack):**

| Tier | Sum of components | Rounded SLA target |
| --- | --- | --- |
| **p50** | \(2000 + 800 + 400 + 1200 = 4400\) ms | **~4.5 s** (aligns under published ~10 s envelope) |
| **p95** | \(6000 + 2000 + 2500 + 3000 = 13500\) ms | **~13.5 s** |
| **p99** | \(12000 + 4000 + 8000 + 5000 = 29000\) ms | **~29 s** |

Lazy-discovery path replaces fan-out+tools with sequential PD fishing → research **>60 s** first response, treated as **p99 floor breach** relative to the 5-minute ack SLO budget.

**Mitigations by tier**

| Tier | Failure mode | Mitigation |
| --- | --- | --- |
| **p50** | Cold prompt / large dynamic context | Prompt cache static prefix; precompute; truncate to discriminative fields (2k char blobs) |
| **p95** | Slow observability connector | Skip-and-annotate; circuit breaker → degrade to context-only proposal; parallel fan-out |
| **p99** | Model/provider stall or storm | Fallback model chain (Sonnet → Haiku → deterministic runbook binder); `max_turns`; Event Orchestration suppress before agent wake |

### Throughput & back-pressure

1. **Machine-speed back-pressure** — Event Orchestration suppress/group/enrich before page; agent never sees the raw storm ([PagerDuty Event Orchestration](https://support.pagerduty.com/main/docs/event-orchestration)).
2. **Provider TPM/RPM** — Agent loops inherit model rate limits; OpenAI `max_turns` (default 10; `None` disables) caps cost/iteration ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/) — see sibling module 02).
3. **Capacity signal** — Align virtual-responder engagement to P1/P2 so parallel agent triage lands inside the **5-minute ack SLO** ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [Paige virtual responder](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
4. **Queue design** — Prefer durable workflow queue with concurrency caps per tenant/service; shed low-priority triage first under overload (fail soft to human-only escalate).

### NFR trade-offs

| NFR | IR target / practice | Explicit trade-off |
| --- | --- | --- |
| **Availability** | Agent triage is best-effort beside human on-call; Google requires AI failure contingencies and backup automated/manual options ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)) | Higher agent availability ≠ higher mutation autonomy — keep writes fail-closed |
| **RPO** | HITL `RunState` + Temporal checkpoints; rebuild context on trigger/note/resolve | Tighter RPO (checkpoint every tool step) ↑ persistence cost / replay size |
| **RTO** | Resume paused approval without re-running completed tool side effects (idempotency keys) | Faster RTO requires stricter idempotent tool design |
| **Compliance** | Proposal + human approval audit; CloudTrail for AWS SLR; tenant memory redaction API ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages); [AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html); [Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)) | Full chain-of-custody logging ↑ storage and redaction workload |
| **Autonomy vs blast radius** | Typed tools + peak-traffic policies (“no global restart”) + service-graph scope | More autonomy ↑ MTTM potential and ↑ blast-radius risk |
| **Speed vs approval** | Propose-only (Paige today) vs HITL mutation (Google CLI) | Skipping approval cuts latency; postmortems of unauthorized AI prod ops argue against it ([Newsletter #131](https://newsletter.systemdesign.one/p/how-do-ai-agents-work)) |

---

## Part 4 — Distributed Resilience & Security

### Durable execution (Temporal or equivalent + checkpoints)

| Mechanism | Behavior |
| --- | --- |
| **Precomputed context rebuild** | On trigger, note add, resolve; debounce bursts; proceed if a source is slow and **note what was skipped** ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)) |
| **HITL RunState serialization** | Persist paused approvals; `to_json`/`from_json`; sticky always_approve/reject; version agent defs with stored state ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |
| **Temporal / workflow checkpoint** | Workflow history as source of truth for replay; Activities wrap tool calls; signals for human approve/reject; timers for AWS-style 10-min clarify timeout |
| **Memory layers** | Observations (service facts), Recollections (decision-changing details), Playbooks with normalized signatures ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)) |
| **Dead-letter** | Permanent tool failures / poison pills → DLQ with correlation ID; do not auto-retry unbounded |

Mitigation playbooks should include **verify + rollback** so failed mutations reverse without full RCA completion ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages) — [inferred] design implication).

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | 429/5xx from LLM or Datadog; network blip | Exponential backoff + jitter; circuit breaker |
| **Permanent** | 400 invalid tool schema; unknown playbook id; authZ deny | No retry; escalate / propose only |
| **Poison pill** | Same incident id + args crashes worker ≥ N times | Quarantine to DLQ; strip from auto-retry |
| **Idempotency** | Duplicate approval webhook; replayed Activity | Idempotency key = `incident_id:tool:args_hash:attempt_bucket`; atomic owner-checked resume ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) |

### Circuit breaker: closed → open → half-open

> ⚠️ Gap: No published breaker thresholds (error %, half-open intervals) for IR agent runtimes. Use the standard state machine below with **stated defaults** for self-hosted agents.

```
  CLOSED ──(error_rate ≥ threshold in window)──► OPEN
    ▲                                              │
    │                                    (cooldown elapsed)
    │                                              ▼
    └────────(probes succeed)──────────────  HALF_OPEN
                     (probe fails) ──► OPEN
```

IR-shaped substitutes when breaker opens: skip-and-annotate missing observability; fail-closed approval callables; prefer classic Automation Actions when deterministic automation already meets the need ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations)).

**Fallback chains**: primary frontier model → secondary small model → deterministic runbook binder / Automation Action → human-only escalate. Never expand blast radius as a “retry.”

### Zero-Trust MCP

1. **Authenticate every tool hop** — MCP over HTTPS + OAuth Bearer; no ambient trust from the LLM host to prod APIs ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector) pattern; sibling runtime module).
2. **Default deny / ask** — Anthropic MCP toolsets default **`always_ask`** so newly added tools do not auto-execute; OpenAI hosted MCP `require_approval: "always"` ([Anthropic permission policies](https://platform.claude.com/docs/en/managed-agents/permission-policies); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
3. **Annotate destructive / open-world tools** — Disclose impact in MCP tool annotations ([Anthropic — Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)); Google risk metadata: safe / reversible / destructive ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).
4. **Strong agent identity** — Same security bar as humans/systems; AWS investigative agent uses read-only `AWSServiceRoleForSupport` SLR, CloudTrail-attributed ([Google SRE agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations); [AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).

### Tool-level RBAC (least privilege)

- Scope tools to **external** observability/knowledge; withhold inward platform fishing ([PagerDuty Eng](https://www.pagerduty.com/eng/context-over-cleverness-building-pagerdutys-sre-agent/)).
- Separate roles: who can **create** Automation Actions vs who can **run** them ([PagerDuty Automation Actions](https://support.pagerduty.com/main/docs/automation-actions)).
- Team-level AI access toggles; Account Owner/Global Admin enable agents ([PagerDuty AI](https://docs.pagerduty.com/ai-automation/advance.md)).
- Mutation tools require explicit role + policy (e.g. no global restart at peak; 2-person approval) ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)).

### PII: detection → redaction → audit

> ⚠️ Gap: No IR-vendor papers publishing NER/regex PII redaction F1 for log/chat pipelines.

**Required pipeline for self-hosted IR agents:**

1. **Detect** — Regex + NER over log lines, ticket bodies, Slack digests before context assembly (emails, tokens, PAN, secrets).
2. **Redact** — Replace with stable tokens (`[REDACTED:email:3f2a]`) so correlation across turns survives; Memory API for human-speed view/update/redact ([Paige docs](https://docs.pagerduty.com/ai-automation/advance/sre-agent)).
3. **Audit** — Log redaction events (field type, count, correlation id) without writing raw PII to model logs; AWS: customer data not used for training ([AWS AI Investigative Agent](https://docs.aws.amazon.com/security-ir/latest/userguide/ai-investigative-agent.html)).

Treat tool names/args as untrusted display content; keep full `RunState` server-side; authenticate reviewers via app session ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

### Immutable audit logs / chain of custody

| Event | Must record |
| --- | --- |
| Proposal | Model id, prompt hash, proposed tool + args, correlation id |
| Approval | Human identity, decision, timestamp, policy version |
| Execution | Tool proxy result, verify outcome, rollback if any |
| Access | CloudTrail (AWS SLR) or equivalent data-plane reads |

Google: every CLI-proxied action logs AI proposal + human approval ([Google SRE Gemini CLI](https://cloud.google.com/blog/topics/developers-practitioners/how-google-sres-use-gemini-cli-to-solve-real-world-outages)). ICS: keep working record of debugging/mitigation for timeline reconstruction ([Google SRE Workbook](https://sre.google/workbook/incident-response/)). Prefer append-only / WORM storage for the decision ledger.

---

## Part 5 — Production Enterprise Code

Runnable incident-triage loop: deterministic fake model (no API keys), exponential backoff + jitter, circuit breaker, fallback chain, correlation-id logs, graceful degradation.

```python
#!/usr/bin/env python3
"""Incident-response triage loop with resilience primitives.

No external APIs or API keys. Run: python3 this_file.py
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s cid=%(correlation_id)s msg=%(message)s",
)
logger = logging.getLogger("ir_agent")
logger.addFilter(CorrelationFilter())


def log(cid: str, level: int, msg: str, **fields: Any) -> None:
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.log(level, f"{msg} {extra}".rstrip(), extra={"correlation_id": cid})


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
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
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes_in_half_open: int = 0
    opened_at: float = 0.0

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
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


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
            log(cid, logging.INFO, "op_ok", op=op_name, attempt=attempt, breaker=breaker.state.value)
            return result
        except CircuitOpenError as exc:
            log(cid, logging.WARNING, "op_short_circuit", op=op_name, err=str(exc))
            raise
        except PermanentError as exc:
            breaker.record_failure()
            log(cid, logging.ERROR, "op_permanent", op=op_name, attempt=attempt, err=str(exc))
            raise
        except TransientError as exc:
            last_exc = exc
            breaker.record_failure()
            if attempt == max_attempts:
                break
            # Full jitter: sleep ~ U(0, min(max, base * 2^(attempt-1)))
            cap = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay = random.uniform(0.0, cap)
            log(
                cid,
                logging.WARNING,
                "op_retry",
                op=op_name,
                attempt=attempt,
                delay_ms=int(delay * 1000),
                err=str(exc),
                breaker=breaker.state.value,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Deterministic fake model + tool proxies
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
    idempotency_key: str = ""


class FakeModel:
    """Deterministic classifier — no network, no API keys."""

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
    digest = hashlib.sha256(f"{incident_id}:{tool}:{args}".encode()).hexdigest()[:16]
    return f"{incident_id}:{tool}:{digest}"


class ToolProxy:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.metrics_breaker = CircuitBreaker("metrics", failure_threshold=2, recovery_timeout_s=1.0)
        self._metrics_failures_left = 2

    def fetch_metrics(self, cid: str, incident: Incident) -> str:
        key = idempotency_key(incident.incident_id, "fetch_metrics", incident.service)

        def _call() -> str:
            if key in self._seen:
                return f"cached_metrics service={incident.service} cpu=91 mem=88"
            if self._metrics_failures_left > 0:
                self._metrics_failures_left -= 1
                raise TransientError("metrics 503")
            self._seen.add(key)
            return f"metrics service={incident.service} cpu=91 mem=88 p99_latency_ms=2400"

        return retry_with_backoff(cid, "fetch_metrics", _call, breaker=self.metrics_breaker)

    def bind_runbook(self, incident: Incident) -> str:
        if incident.runbook:
            return f"runbook_bound url={incident.runbook}"
        return "runbook_missing flag=need_human_runbook"


# ---------------------------------------------------------------------------
# PII redaction (detect → redact → audit record)
# ---------------------------------------------------------------------------

import re

_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_AUDIT: list[dict[str, Any]] = []


def redact_pii(cid: str, text: str) -> str:
    found = _EMAIL.findall(text)
    redacted = _EMAIL.sub("[REDACTED:email]", text)
    if found:
        _AUDIT.append(
            {
                "correlation_id": cid,
                "event": "pii_redaction",
                "field_type": "email",
                "count": len(found),
                "immutable": True,
            }
        )
        log(cid, logging.INFO, "pii_redacted", count=len(found))
    return redacted


# ---------------------------------------------------------------------------
# Fallback chain + graceful degradation triage loop
# ---------------------------------------------------------------------------

PROPOSALS = {
    "memory_pressure": "Propose: restart memory-leaking task (typed: task_restart) — REQUIRES APPROVAL",
    "dependency_latency": "Propose: shed non-critical traffic + page dependency owners — read-only diagnose first",
    "bad_deploy": "Propose: rollback last deploy (typed: deploy_rollback) — REQUIRES APPROVAL",
    "unknown": "Propose: escalate to human IC; grounding thin — do not invent queries",
}


def triage_incident(incident: Incident) -> TriageResult:
    cid = str(uuid.uuid4())
    log(cid, logging.INFO, "triage_start", incident_id=incident.incident_id, sev=incident.severity)

    incident.log_excerpt = redact_pii(cid, incident.log_excerpt)
    tools = ToolProxy()
    tools_used: list[str] = []
    degraded = False
    model_used = ""

    primary = FakeModel("sonnet-fake", fail_times=2)
    secondary = FakeModel("haiku-fake", fail_times=0)
    model_breaker = CircuitBreaker("llm", failure_threshold=3, recovery_timeout_s=1.0)

    # Model fallback chain: primary → secondary → deterministic
    label: str | None = None
    for model in (primary, secondary):
        try:
            result = retry_with_backoff(
                cid,
                f"classify:{model.name}",
                lambda m=model: m.classify(incident),
                breaker=model_breaker,
                max_attempts=3,
            )
            label = result["label"]
            model_used = result["model"]
            break
        except (TransientError, CircuitOpenError) as exc:
            log(cid, logging.WARNING, "model_fallback", from_model=model.name, err=str(exc))
            degraded = True

    if label is None:
        # Deterministic fallback: runbook binder only
        degraded = True
        model_used = "deterministic-fallback"
        bound = tools.bind_runbook(incident)
        tools_used.append("bind_runbook")
        proposal = f"Degraded mode: {bound}; page on-call with precomputed context only"
        key = idempotency_key(incident.incident_id, "degraded", label or "none")
        log(cid, logging.ERROR, "graceful_degradation", proposal=proposal)
        return TriageResult(cid, "unknown", proposal, tools_used, True, model_used, key)

    # Tool path with breaker-aware degradation
    try:
        metrics = tools.fetch_metrics(cid, incident)
        tools_used.append("fetch_metrics")
        log(cid, logging.INFO, "tool_result", tool="fetch_metrics", snippet=metrics[:80])
    except (TransientError, CircuitOpenError) as exc:
        degraded = True
        tools_used.append("fetch_metrics:skipped")
        log(cid, logging.WARNING, "skip_and_annotate", tool="fetch_metrics", err=str(exc))
        metrics = "metrics_skipped gap=annotated"

    bound = tools.bind_runbook(incident)
    tools_used.append("bind_runbook")
    proposal = f"{PROPOSALS[label]} | evidence={metrics} | {bound}"
    key = idempotency_key(incident.incident_id, "propose", label)

    # Immutable audit (chain of custody for the proposal)
    _AUDIT.append(
        {
            "correlation_id": cid,
            "event": "agent_proposal",
            "incident_id": incident.incident_id,
            "label": label,
            "model": model_used,
            "proposal": proposal,
            "idempotency_key": key,
            "human_approval": "pending",
            "immutable": True,
        }
    )
    log(cid, logging.INFO, "triage_done", label=label, degraded=degraded, model=model_used)
    return TriageResult(cid, label, proposal, tools_used, degraded, model_used, key)


def main() -> None:
    incidents = [
        Incident(
            incident_id="inc-1001",
            service="checkout-api",
            severity="P2",
            alert_summary="pod OOMKilled loop",
            runbook="https://runbooks.example/oom",
            log_excerpt="OOM killer on checkout-api; contact alice@example.com",
        ),
        Incident(
            incident_id="inc-1002",
            service="payments",
            severity="P1",
            alert_summary="upstream 5xx spike after deploy",
            runbook=None,
            log_excerpt="timeout to billing-svc; rollback candidate build 4421",
        ),
    ]
    for inc in incidents:
        result = triage_incident(inc)
        print("---")
        print(f"cid={result.correlation_id}")
        print(f"label={result.severity_label} model={result.model_used} degraded={result.degraded}")
        print(f"tools={result.tools_used}")
        print(f"idempotency_key={result.idempotency_key}")
        print(f"proposal={result.proposal}")
    print("--- audit_ledger ---")
    for row in _AUDIT:
        print(row)


if __name__ == "__main__":
    main()
```

**What this demonstrates**

| Concern | Implementation |
| --- | --- |
| Retries + jitter | `retry_with_backoff` full-jitter \(U(0, \min(max, base \cdot 2^{n}))\) |
| Circuit breaker | `CLOSED → OPEN → HALF_OPEN` on LLM and metrics proxies |
| Fallback chain | `sonnet-fake → haiku-fake → deterministic runbook bind` |
| Correlation IDs | UUID per triage; every log line carries `cid=` |
| Graceful degradation | Skip-and-annotate metrics; degraded proposal without inventing queries |
| Idempotency | SHA-256 key on `incident:tool:args`; cached metrics replay |
| PII + audit | Email detect→redact→append-only `_AUDIT` ledger |

---

## Part 6 — Architectural System Design Scenarios

Exactly **two** scenarios. Interview prompts follow only after both are complete.

### Scenario A — Multi-tenant SaaS: virtual responder for P1/P2 without waking humans for noise

**Problem statement**  
A B2B SaaS runs ~2k monitored services across 400 tenants. Peak alert ingress is 500 events/min. On-call ack SLO is **5 minutes**. Leadership wants a Paige-style virtual responder that posts a first triage proposal in Slack within ~10 s for true incidents, while Automation Actions handle known classes **before** notify. Mutations (restart/rollback) must remain propose-only until policy/audit mature. Memory and playbooks must not leak across tenants. Budget target ≈ **$70–110 / 1k agent investigations** at routed model mix.

**Proposed architecture**

```
Monitor ──► Event Orchestration (suppress/group/enrich/Automation Action)
                 │
                 ▼  (page-worthy only)
            Precompute Context (per tenant+service) ──► Temporal workflow
                 │                                         │
                 ▼                                         ▼ HITL signal
            Triage Agent (Haiku noise/status, Sonnet RCA)  Slack proposal
                 │
                 ├── MCP read tools (Datadog/CW/GitHub) + RBAC
                 ├── Memory store (tenant-isolated)
                 └── Audit ledger (proposal only; no mutate)
```

Technology choices: Event Orchestration + AIOps for back-pressure; precomputed Incident Context; Temporal for durable HITL later; MCP tools default `always_ask` even though writes are disabled; prompt cache on system+schemas; 2k-char discriminative alert fields.

**Trade-off matrix**

| Approach | Cost | Latency | Ops complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. Deterministic Automation Actions only** | Lowest (no LLM) | Machine-speed | Rules debt | Highest for known jobs | High for known classes; fails on novel outages |
| **A2. Read-only triage agent + precompute (recommended)** | ~$70–110 / 1k incs (routed+cache) | ~10 s first proposal if precomputed | Connector + runbook hygiene | High (no mutate; tenant memory) | Scales with service memory; bounded by TPM |
| **A3. Fully autonomous remediation** | Lowest human $ if correct | Fastest MTTM **if safe** | Highest (blast radius, rollback, eval) | Highest risk | Unsafe at multi-tenant blast radius |

**Decision rationale**  
Choose **A2**. Noise reduction stays deterministic (A1 remains the first layer). Precompute hits the published ~10 s path and protects the 5-minute ack SLO better than lazy discovery (>60 s). Propose-only matches current PagerDuty posture and Newsletter #131’s postmortem-driven ban on ungoverned writes. A3 loses on security and multi-tenant blast radius before policy/audit/rollback are proven.

---

### Scenario B — Large cloud SRE org: HITL typed mitigations for MTTM-first response

**Problem statement**  
An internal SRE org (Google-like ICS/IMAG) pages on customer-impacting errors. Goal: optimize **MTTM** (stop Bad Customer Minutes), not chat quality. Agents must classify into a **closed** mitigation set (drain, rollback, restart, add capacity), execute only via typed MCP tools with risk metadata, enforce peak-traffic policies (e.g. no global restart), and require human approval (2-person for destructive). On mitigation failure, pivot to RCA without expanding blast radius. Clarify-style waits must timeout (e.g. 10 min) and proceed. Compliance needs immutable proposal+approval logs.

**Proposed architecture**

```
Page ──► Severity router ──► ProdAgent orchestrator (Temporal workflow)
                                │
              ┌─────────────────┼──────────────────────┐
              ▼                 ▼                      ▼
        Playbook agent    Metrics/log workers    Mitigation proxy (MCP)
              │                 │                      │
              └─────────► classify → typed tool ───────┤
                                                       ▼
                                              Policy engine
                                           (risk + peak + RBAC)
                                                       ▼
                                              HITL approve/reject
                                           (durable RunState)
                                                       ▼
                                              execute → verify → rollback?
                                                       ▼
                                              postmortem draft + audit WORM
```

Technology choices: orchestrator–workers; Temporal Activities for tools; OpenAI-style `needs_approval` + Anthropic `always_ask` defaults; risk classes safe/reversible/destructive; CloudTrail-equivalent attribution; verify+rollback in every mutation playbook.

**Trade-off matrix**

| Approach | Cost | Latency | Ops complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Read-only triage only (Scenario A style)** | Lower tokens | Fast propose; slow MTTM (human still mutates manually) | Lower | Very high | High |
| **B2. HITL typed mutation agent (recommended)** | Tokens + approver time | Seconds–minutes per approval; MTTM wins when playbooks tight | Policy + tool metadata + audit | Highest when enforced | Bounded by human approvers |
| **B3. Autonomous mutation without HITL** | Lowest human latency cost | Best raw speed | Extreme eval/rollback burden | Unacceptable for destructive class | Scales until first bad global restart |

**Decision rationale**  
Choose **B2**. MTTM-first orgs need typed mitigations in-loop (B1 leaves mitigation entirely to slow manual ops). Google’s multi-layer safety (typed tools, risk metadata, policy, CLI confirm, audit) plus durable HITL gives compliance chain-of-custody. B3 fails the autonomy-vs-blast-radius trade-off: a command safe in one state may be unsafe during config push. Approver throughput is the scalability ceiling—accept it; use Automation Actions for pre-approved known classes to keep humans for novel/destructive paths only.

---

### Interview prompts (after both scenarios)

1. Why is AIOps noise reduction a **control-plane** concern rather than the first LLM turn—and what fails if you invert that?
2. Derive a `$ / 1k incidents` estimate given your token shape, cache hit rate, and Haiku/Sonnet routing mix. What breaks the estimate in an alert storm?
3. Walk closed → open → half-open for a metrics MCP proxy during a P1. What does the user see when you skip-and-annotate?
4. Place HITL gates for `fetch_logs` vs `deploy_rollback`. Argue fail-closed vs sticky always-approve.
5. Design tenant-isolated memory promotion: when does a recollection become a versioned playbook, and how do you redact PII before promotion?
6. Compare Scenario A vs B: which NFR (MTTA vs MTTM vs compliance) forces you from propose-only to typed HITL mutation?

---

## Auditor checklist (self-verify)

| # | Requirement | Present |
| --- | --- | --- |
| 1 | ASCII box-drawing diagram (`┌─┐│└┘├┤┬┴┼`) labeling CONTROL PLANE, DATA PLANE, PERSISTENCE, TOOL PROXIES, TELEMETRY + request-flow narrative | Yes — Part 1 |
| 2 | `$ per 1k` cost formula with model/tokens/caching; concrete p50/p95/p99 ms; ⚠️ Gap + [inferred] latency budget with TTFT/tool RTT/human ack arithmetic; mitigations; throughput/back-pressure | Yes — Part 3 |
| 3 | NFR trade-offs: availability, RPO/RTO, compliance, plus autonomy vs blast radius and speed vs approval | Yes — Part 3 |
| 4 | Durable execution (Temporal + checkpoints), failure taxonomy, circuit breaker closed→open→half-open, fallback chains | Yes — Part 4 (+ Part 5 code) |
| 5 | Zero-Trust MCP, tool-level RBAC least privilege, PII detect→redact→audit, immutable audit / chain of custody | Yes — Part 4 |
| 6 | Exactly 2 design scenarios with problem, architecture, 2–3-alt trade-off matrix, decision rationale; interview prompts only after both | Yes — Part 6 |
