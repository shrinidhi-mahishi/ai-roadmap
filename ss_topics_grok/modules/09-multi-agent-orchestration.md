# Module 09 — Multi-Agent Orchestration

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `ss_topics_grok/research/09-multi-agent-orchestration.md` (researched 2026-09-23, 92 sources). Vendor list prices used in §3 are from [`01-python-llm-foundations.md`](01-python-llm-foundations.md) (2026-09-23). Single-agent loop fuses, ReAct/P&E math, and `max_turns` / `recursion_limit` live in [`04-agent-loop-patterns.md`](04-agent-loop-patterns.md). Pregel super-steps, channel reducers, `Command` / `Send`, interrupt node-restart, subgraph checkpointers, and `HumanInTheLoopMiddleware` resume shapes live in [`05-langgraph-state-machines.md`](05-langgraph-state-machines.md). MCP JSON-RPC, OAuth 2.1 / RFC 8707, token passthrough, and Zero-Trust tool-plane rules live in [`08-mcp-integrations.md`](08-mcp-integrations.md). This file does **not** recopy those APIs. It is the **multi-agent control plane**: who may be next, what state crosses the boundary, how agents agree, when a human or a specialist is required.
**Mandatory topics**: Supervisor / swarm / hierarchical topologies · typed state handoffs · consensus τ · HITL timeout-deny · four escalation edges (SLA, confidence, cost, security).

The model **never routes, never hands off, never grants authority, never counts a vote, never times out HITL**. It emits a structured action (`transfer_to_*`, `Command(goto=…)`, A2A `SendMessage`, a vote JSON). A **runtime** mutates durable state, enforces hop / $ / tool allowlists, and decides the next node. Collapsing “who may act” into the prompt is the dominant enterprise failure ([LangChain multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent); [AISVS C9.5.3](https://github.com/OWASP/AISVS/blob/main/1.0/en/0x10-C09-Orchestration-and-Agentic-Action.md)).

---

## What Is This?

A production **multi-agent system** is a **second clock** around the inner ReAct/P&E loop of **04**: who is allowed to run that loop next, and what of their state is visible to the next hop. The **control plane** owns next-agent, hop budget, kill-switch, HITL gates, and vote aggregation. The **data plane** owns specialist tool HTTP, MCP `tools/call` (**08**), A2A artifacts, and blackboard blobs. Persistence identity (`thread_id`, OpenAI `RunState` / `session_id`, A2A `contextId`+`taskId`) is control, not chat text. Tool proxies are **per-agent** MCP allowlists with downscoped tokens minted at handoff — never the supervisor’s OAuth cookie. Telemetry is the only place hop count, `$ remaining`, vote vectors, disagreement τ, and HITL timeout-deny are authoritative.

Five topologies serialize that clock differently: **router** (once per user turn), **supervisor / orchestrator-worker** (every worker return or every research wave), **hierarchical supervisors** (which *team* next), **swarm / handoff** (`active_agent` owns the next user-visible token), **custom / blackboard / A2A Network**. Router vs supervisor vs orchestrator are **not** synonyms. Consensus is **not** a topology — it is a **join function** on a fan-in. HITL is a **durable wait** with an authenticated resume, not “ask the model to be careful.” Escalation is a **control-plane edge** to a specialist **or** a human; do not collapse them.

LangChain 2026: teams ask for “multi-agent” when they need **context management**, **distributed development**, or **parallelization**. If context were infinite and latency zero, a single agent with all tools would dominate. Skills (progressive disclosure) are often the cheaper substitute ([Agent Skills](https://agentskills.io/)). Microsoft Learn (2026): **platform-native orchestration for internal subagents**; **MCP for tools/data**; **A2A for opaque, cross-platform, cross-org agents**. Do not flatten a partner agent into `tools/call`.

## Why It Matters

On a stated L1 ticket (supervisor + 2 specialists; **2,000 in + 400 out** per call; Claude Sonnet 5 **$2 / $10** per MTok from 01) the coordination tax is **[inferred] $24 / 1k** (sticky handoffs, 3 calls) vs **$32 / 1k** (subagents that always return to the supervisor, 4 calls). That **+$8 / 1k / turn** buys a single user-facing owner and centralized policy. Anthropic’s published envelope: chat → agent **~4×** tokens; chat → multi-agent **~15×** → **[inferred] $135 / 1k** Sonnet-only research, **~$240 / 1k** on a 30% Opus 5 mix — before web-search SKUs (`$10 / 1K` searches; 3 subs × 8 = **$0.24 / task**, often larger than Loop S tokens). Unbounded Loop D (50 subs × 10 calls) is **[inferred] $4,000 / 1k**. Du 3×2 debate is **[inferred] +$56 / 1k** extra — a verifier, not a default topology. No vendor publishes supervisor-worker p50/p95/p99 as of 2026-09-23; architecture bounds Loop S sequential **~9.0 s** vs parallel `Send` **~5.5 s** **[inferred]**. A reciprocal `transfer_to_sales` ↔ `transfer_to_support` with no hop cap has **no p99**. AISVS **9.6.2**: HITL timeout **denies**, it does not proceed.

## Interview traps (fail these, fail the round)

- “The model routes / hands off / counts the vote / times out HITL.” It emits structured JSON. The **runtime** mutates state (AISVS **9.5.3**).
- Router, supervisor, and orchestrator as synonyms. Three **clocks**: once-per-turn classify vs every-worker-return vs every-wave decompose.
- Shipping `langgraph-supervisor` for new work. LangChain 1.x: implement the supervisor as **ordinary tools**; the package is **compatibility-only**, no longer actively maintained.
- `input_type` / `EscalationData` as **authorization**. It is metadata. `is_enabled` cannot gate on parsed fields (evaluated **before** the model returns arguments). Authorize in `on_handoff` and **raise**.
- OpenAI input/output guardrails wrapping the **handoff**. Input = **first** agent only; output = **last** only; tool-input guardrails **do not wrap** handoffs. Policy lives on the **worker**.
- HITL **timeout-allow** (“if nobody answers, issue the refund”). AISVS **9.6.2** = **block**. LangGraph `interrupt()` has **no** TTL — you add Temporal / queue TTL.
- Majority vote of a **shared hallucination** (Estornell tyranny of the majority). Agreement < τ_low → **do not average**; escalate. Same-family proposer+judge without position-swap re-introduces Zheng self-enhancement.
- Supervisor holds the **union** of worker tools “so it can help.” CrewAI/LangGraph anti-pattern. Hierarchical IAM = **delegation rights**, not Stripe+email on the lead.
- GroupChat of 8 personas for L1 support; spawning 50 research subs for “where is my laptop”; voting on a refund.
- Flattening A2A into MCP `tools/call`. MCP = tool bus (**08**); A2A = agent bus. Terminal A2A tasks are **immutable**.
- Treating Anthropic **+90.2%** (internal research eval) or Magentic GAIA **38%** (GPT-4o, 2024) as your production SLO.
- `HumanInTheLoopMiddleware`: tools **absent** from `interrupt_on` **auto-approve**. A new write tool you forgot to list is a privilege bug.
- OpenAI `_max_turns=10` burning while a human is at lunch → `MaxTurnsExceeded`. Raise `max_turns` / `None` for approval-gated runs; put the **SLA on the queue**.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns **next-agent**, **max hops**, **parallel fan-out cap**, **HITL timeout-deny**, **disagreement → escalate**, and **kill-switch** (AISVS **9.1.3** / **9.6.3**, out-of-band). It does **not** own transformer weights, Stripe, or the partner’s Agent Card keys. Data plane is specialist inner loops (**04**), MCP `tools/call` (**08**), and A2A `Artifact` / `Part`. Persistence is resume identity after crash or approval wait (`thread_id`, `RunState`, A2A `contextId`+`taskId`, HITL queue row). Tool proxies mint **downscoped** outbound credentials at handoff. Telemetry is the WORM of `from_agent` / `to_agent` / mechanism / `token_jti` / vote_vector / τ / `human_gate`.

Cardinality: **1** user-facing owner per turn (supervisor join **or** sticky `active_agent`); **N** specialists; **1** HITL work-queue per tenant; **0..K** A2A callees as sibling tasks in one `contextId`.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS / SURFACES                                                              │
│  chat / support widget │ research desk │ A2A partner │ L2 / approver console    │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ TLS + session JWT (tenant/roles FROM TOKEN) + correlation-id
             │ structured action JSON  —  MODEL NEVER ROUTES / VOTES / TIMES HITL
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (orchestrator runtime — your process / Agent Server / Temporal)  │
│                                                                                 │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Edge       │─▶│ Policy     │─▶│ SUPERVISOR │─▶│ JOIN /     │─▶│ HITL GATE │  │
│  │ SSO, cid,  │  │ PII redact │  │ ROUTER     │  │ VOTE τ     │  │ timeout-  │  │
│  │ thread_id  │  │ BEFORE any │  │ next agent,│  │ accept |   │  │ DENY      │  │
│  │            │  │ hop copies │  │ hop cap, $ │  │ extra rnd  │  │ (9.6.2);  │  │
│  │            │  │ the trans- │  │ cap, fan-  │  │ | ESCALATE │  │ write     │  │
│  │            │  │ cript      │  │ out cap    │  │ never avg  │  │ tools     │  │
│  └────────────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬─────┘  │
│                        │               │               │               │        │
│                        ▼               ▼               ▼               ▼        │
│                 ┌──────────────────────────────────────────────────────────┐    │
│                 │ ORCHESTRATION LEDGER                                     │    │
│                 │  typed Handoff (reason, priority, artifact ids)          │    │
│                 │  downscope token in on_handoff — RAISE on authz fail     │    │
│                 │  four edges: SLA/hops · confidence/τ · cost/$ · security │    │
│                 │  ping-pong detector; Magentic stall → replan not re-hop  │    │
│                 │  A2A: SendMessage; AUTH_REQUIRED / INPUT_REQUIRED park   │    │
│                 └──────────────────────────┬───────────────────────────────┘    │
│  ┌────────────┐  ┌────────────┐            │            ┌──────────────────┐    │
│  │ Circuit    │  │ Fallback   │◀───────────┘───────────▶│ SIGTERM / drain  │    │
│  │ ONE per    │  │ primary    │                         │ park HITL; do    │    │
│  │ specialist │  │ specialist │                         │ not cut in-flight│    │
│  │ / provider │  │ → peer →   │                         │ research (rainbow│    │
│  │  (not one  │  │ degraded   │                         │ pin prompt ver)  │    │
│  │  global)   │  │ JSON       │                         │                  │    │
│  └────────────┘  └────────────┘                         └────────┬─────────┘    │
└──────────────────────────────────────────────────────────────────┼──────────────┘
                                                                   │
     ┌──────────────────────────────────┬──────────────────────────┘
     │ inner ReAct / MCP / A2A          │ durable wait (zero compute)
     ▼                                  ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ DATA PLANE  SPECIALISTS         │  │ DATA PLANE  HITL QUEUE + A2A               │
│ (each inner loop = 04)          │  │ model NEVER holds Stripe or partner PAT    │
│                                 │  │                                            │
│  ┌──────────┐  ┌─────────────┐  │  │  Temporal Signal / Kafka hitl.approvals    │
│  │ FAQ / L1 │  │ Billing /   │  │  │  ticket = thread_id + interrupt id         │
│  │ kb_search│  │ L2 refund   │  │  │  SLA timer OUTSIDE the graph               │
│  └──────────┘  └─────────────┘  │  │  overflow: shed low-pri; NEVER auto-approve│
│  ┌──────────┐  ┌─────────────┐  │  │  irreversible class                        │
│  │ Research │  │ Judge /     │  │  │  A2A Task: SUBMITTED→WORKING→COMPLETED|    │
│  │ sub + fs │  │ Citation    │  │  │    FAILED|CANCELED|REJECTED|INPUT_REQUIRED │
│  └──────────┘  └─────────────┘  │  │    |AUTH_REQUIRED; terminal = immutable    │
│  isolated window OR sticky hist │  │  sibling tasks share contextId             │
└────────────┬────────────────────┘  └─────────────────────┬──────────────────────┘
             │  untrusted planner (action JSON)            │ side effects / wait
             ▼                                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ TOOL PROXIES  (per-agent MCP)   │  │ PERSISTENCE  (resume identity)             │
│ Zero-Trust wrap; RFC 8707 aud.  │  │                                            │
│ tenant NEVER from tool args     │  │  ┌─────────────┐  ┌─────────────┐          │
│  ┌──────────┐  ┌─────────────┐  │  │  │ THREAD /    │  │ HITL ROW    │          │
│  │ FAQ MCP  │  │ payments    │──┼──│  │ RunState    │  │ sla_s,      │          │
│  │ read-only│  │ write +HITL │  │  │  │ active_     │  │ decision,   │          │
│  └──────────┘  └─────────────┘  │  │  │ agent, hops,│  │ timeout-deny│          │
│  researcher / judge: artifact        │  │  │  $ spent     │  │             │          │
│  read; NEVER mutate Stripe      │  │  └─────────────┘  └─────────────┘          │
│  downscope jti at handoff       │  │  ┌─────────────┐  ┌─────────────┐          │
│                                 │  │  │ ARTIFACTS   │  │ VOTE LOG    │          │
│                                 │  │  │ fs refs,    │  │ vector, τ,  │          │
│                                 │  │  │ A2A ids     │  │ dropped hash│          │
│                                 │  │  └─────────────┘  └─────────────┘          │
└─────────────────────────────────┘  │  token vault NOT in messages               │
                                     └────────────────────────────────────────────┘
                                                            │
┌───────────────────────────────────────────────────────────┴─────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics      │  │ Traces       │  │ Usage (authoritative │ │
│  │ cid, from/to,│  │ hops, $,     │  │ supervisor→  │  │ on terminal event)   │ │
│  │ mechanism,   │  │ p50/p95/p99  │  │ specialist→  │  │ calls × SKU, search  │ │
│  │ jti, tools_  │  │ [policy],    │  │ join; A2A    │  │ SKU, total_cost_usd, │ │
│  │ enabled, τ,  │  │ hitl wait,   │  │ taskId; OTel │  │ remaining_usd        │ │
│  │ vote_vector, │  │ ping-pong,   │  │              │  │                      │ │
│  │ human_gate,  │  │ breaker per  │  │ PII stripped │  │                      │ │
│  │ artifact ids │  │ agent        │  │              │  │                      │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Typical backing | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | Next agent, hop/$/fan-out caps, vote join, HITL timeout-deny, kill-switch | Orchestrator + Temporal/Agent Server + IdP | Prompt-as-router; 50-sub fan-out; timeout-allow refunds |
| **Data (specialists)** | Inner ReAct, worker tools, condensed summaries | Model APIs + MCP servers | PII copied on every hop; telephone game through the lead |
| **Data (A2A / HITL)** | Partner Task lifecycle; approval queue | A2A + Temporal Signal / Kafka | Blocking `SendMessage` as your only HITL; console `input()` as SLA |
| **Tool proxies** | Per-agent MCP allowlist; downscoped outbound token | **08** gateway PEP | Worker inherits supervisor GitHub admin (agent confused deputy) |
| **Persistence** | `thread_id` / `RunState` / `contextId`+`taskId` / HITL row / artifact refs | Postgres checkpointer + queue + object store | Laptop `invoke()`; restart from scratch after 500 or a 15-min approval |
| **Telemetry** | WORM of handoff / vote / τ / jti / human_gate | SIEM + OTel | Finance dashboards that ignore search SKU and hop count |

**Who owns the next user-visible token (do not fuse).**

| Role | Write/control path | User-facing owner | Failure if fused |
| --- | --- | --- | --- |
| Supervisor / lead | Handoff tools or workers-as-tools; synthesizes | Supervisor (subagents) | Telephone game; lead holds write tools |
| Sticky specialist | `active_agent` / OpenAI `handoffs=` | The specialist | Reciprocal ping-pong; no hop cap |
| Judge / CitationAgent | Read artifacts; rubric 0–1 | Never the user-facing owner | Same-family self-enhancement |
| Human approver | `approve` / `edit` / `reject` / `respond` | Parks the graph | ASI09 rubber-stamp; auto-approve missing `interrupt_on` |
| A2A callee | Skills on its Agent Card | Opaque; you see Task + Artifact | Flattened into MCP; your VPC via their tools |

### 1.2 End-to-end request flow

**Interactive support / research path:**

1. **Ingress.** Gateway stamps `correlation_id`. Bind `tenant_id` / roles from the **verified token**, never from tool JSON the model invented. Pin `thread_id` (or A2A `contextId`).
2. **Policy.** DLP-redact PII **before** any specialist sees the transcript. Classify reversibility from the **tool manifest**, not the agent’s self-description (AISVS **9.2.3–9.2.4**). Worst class in the **chain** wins (**9.2.10**).
3. **Supervisor tick.** Model emits a structured action (`transfer_to_billing`, `Spawn(n=3)`, `FINISH`, vote JSON). Runtime — not the prompt — checks hop cap, `$ remaining`, fan-out cap, `is_enabled` predicates, and the allowed-transition graph.
4. **Typed handoff.** Construct `Handoff(from, to, reason, priority, artifact_ids)`. `on_handoff` mints a **downscoped** token (audience = worker MCP servers; scopes = brief). Log hash of **dropped** history (`input_filter` / `remove_all_tools`). Authorization failure **raises** — the SDK continues the transfer if `on_handoff` returns successfully.
5. **Specialist inner loop.** **04** ReAct/P&E inside an isolated window (research) or sticky transcript (support). Tools go through **08** PEP. Subagents-as-tools: supervisor keeps the user-facing reply. Swarm handoff: specialist becomes owner; next user turn **skips** the router (`add_active_agent_router`).
6. **Join.** `last_message` vs synthesizer vs **vote**. Parse votes into a closed answer set. If agreement ≥ τ_high **and** independently checkable → accept. If < τ_low → **do not average**; escalate to a position-swapped judge, then a human. CitationAgent attributes claims to URLs / artifact ids — a different worker, not a majority of summaries.
7. **HITL branch.** Irreversible / write tools park on a **work queue** (ticket = `thread_id` + interrupt id). Temporal `wait_condition` (zero compute) or Kafka `hitl.approvals`. Show **canonicalized** parameters (AISVS **9.2.2**) — not a supervisor paraphrase. ASI09: friction, approval-budget per session, structured risk badges.
8. **Four escalation edges** (runtime detectors): **SLA/hops** → force human after N transfers; **confidence/τ** → extra debate round or human; **cost** → forbid new `Send` / handoff at 80% of ticket budget; **security/class** → HITL before write, `AUTH_REQUIRED` for user gesture. Capability miss (`REJECTED`, unbound skill) is a fifth cousin: specialist or human, not a silent tool invent.
9. **A2A (cross-org).** `SendMessage` on a skill from the Agent Card. Blocking waits until terminal or `INPUT_REQUIRED` / `AUTH_REQUIRED`. Client may re-delegate `AUTH_REQUIRED` up a chain. Terminal tasks **never restart**; refinements create a **new** `taskId` in the same `contextId`.
10. **Halt + WORM.** cid, from/to, mechanism (`handoff|as_tool|A2A|Send|vote`), `input_type` metadata, `principal_id`, `token_jti`, `tools_enabled`, `policy_version`, `human_gate`, artifact ids, vote_vector, τ. Reconstruct a refund as: policy snapshot + typed Handoff + hashed omitted history + downscoped jti + HITL decision (or timeout-deny) + Stripe status.

**Durable HITL dual-path (async, independently scaled):**

11. Graph raises `__interrupt__` / `ToolApprovalItem` / `needs_approval`. Persist `RunState` / checkpoint **before** the process can die. Do **not** hold an OSS worker on `input()`.
12. Queue worker notifies the L1/L2 console. SLA timer is **outside** the graph (Temporal timeout → escalate to manager Activity → second timeout → **auto-reject**).
13. Resume the **outermost** graph (`Command(resume=…)` / `state.approve()`). Interrupt inside a subagent tool still surfaces on the parent ([migrate](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)). Idempotent `@task` around side effects (**05**) — node restart would otherwise re-send email.
14. Overflow: shed new **low-priority** interrupts or coarsen (batch). **Never** auto-approve irreversible class to drain the queue.

**Interview talking point:** “The model never routes. I enforce hop and dollar caps in the runtime, I mint a downscoped token in `on_handoff`, I join votes at τ or escalate, and HITL is a Temporal park with timeout-deny — not a prompt that says ‘be careful.’”

### 1.3 Contrast only: clocks and buses

| Shape | Clock | User-facing owner | Parallelism | Bus |
| --- | --- | --- | --- | --- |
| **Router** | Once per user turn | Synthesizer or specialist | `Send` fan-out | In-process |
| **Supervisor (LangGraph)** | Every worker return | Supervisor synthesizes | Default **serial** (`parallel_tool_calls=False`) | In-process |
| **Orchestrator (Anthropic / Magentic)** | Every wave until “enough” | Lead | 3–5 subs × 3+ tools; Magentic assigns **one** worker / inner tick | In-process + Memory |
| **Hierarchical** | Per level: which *team* | Top-level only | Per-team | Nested compiled graphs (**05** IAM, not token savings) |
| **Swarm / OpenAI handoff** | Currently `active_agent` | Whoever holds the conversation | Sequential; disable parallel tool calls (LastValue race) | In-process |
| **A2A mesh** | Task state enum | Callee opaque | Sibling tasks in one `contextId` | Agent bus (not MCP) |
| **Debate / judge** | Join function on a fan-in | Unchanged | Speakers sequential unless you parallelize | Same as parent |

---

## 2. Core Mechanics & Algorithms

### 2.1 Supervisor vs swarm vs hierarchical

**Supervisor / subagents-as-tools.** Central LLM (or ledger) every round. Workers are **tools**; the supervisor keeps the user-facing reply. `langgraph-supervisor.create_supervisor` defaults that change topology: `output_mode='last_message'` (not `full_history`); `parallel_tool_calls=False`; `add_handoff_messages=True`. LangChain 1.x replacement: `create_agent` with workers wrapped as `@tool`. Give the supervisor a `forward_message` tool so it does **not** paraphrase the specialist (telephone-game mitigation); strip routing messages from the worker’s view; `delegate_to_*` vs `transfer_to_*` is an eval surface.

**Orchestrator-worker (Anthropic Research, Magentic-One)** is a supervisor with a **plan ledger** and **waves**. Lead writes the plan to Memory (200k windows truncate). Subs get objective, output format, tool list, stop boundary; isolated windows; condensed summaries. CitationAgent is a separate hop. Magentic: outer **Task Ledger** (facts, guesses, plan) + inner **Progress Ledger** (done? looping? who next?). Stall detector: paper ≤**2**; AutoGen default `max_stalls=3`, `max_turns=20`. Stall → **replan**, not another reciprocal handoff. AutoGen is **maintenance mode** (2026); new work = **Microsoft Agent Framework 1.0** (Magentic still least hand-wired).

**Swarm / typed handoff.** Currently active agent picks the next hop. LangGraph: `create_handoff_tool` → `Command(goto=agent_name, graph=Command.PARENT, update={messages, active_agent})`. Repeat-request table: handoffs **2** calls on turn 2 vs subagents **4**. Failure: reciprocal `transfer_to_*` and no hop cap.

**Hierarchical.** A compiled supervisor sits in another supervisor’s `agents=` list (`research_team`, `writing_team` under `top_level_supervisor`). Use it when teams have **separate checkpointers, tool IAM, and release cadences** — not because the org chart is a tree. Each level adds ≥1 model call and a context splice. CrewAI `Process.hierarchical` **requires** `manager_llm` / `manager_agent`; manager **must not** sit in `agents=` and **must not** hold ordinary tools. `allow_delegation=True` is necessary **not** sufficient (“coworker not found” when the delegation tool lists the manager’s own role).

**Call-count intuition (LangChain pedagogical table, 2k-token specialists):**

| Workload | Subagents | Handoffs | Skills | Router |
| --- | --- | --- | --- | --- |
| One-shot | **4** | **3** | **3** | **3** |
| Repeat same request | **8** | **5** | **5** | **6** |
| Multi-domain, parallel OK | **5** calls, **~9K** tok | **7+** / **~14K+** sequential | **3** / **~15K** | **5** / **~9K** |

Subagents win isolation + parallel. Handoffs win sticky UX. Skills win “one agent, many playbooks.” Router wins explicit classify + parallel without sticky ownership. `parallel_tool_calls=True` on a supervisor turns one tick into a **fan-out orchestrator**.

### 2.2 Typed state handoffs

OpenAI Agents SDK — two official primitives ([handoffs](https://openai.github.io/openai-agents-python/handoffs/)):

| Pattern | Primitive | Next user-visible token | Guardrails | Use |
| --- | --- | --- | --- | --- |
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool `transfer_to_<agent>` | Specialist | Input = **first** only; output = **last** only | Conversation ownership changes |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Nested run; `needs_approval` supported | Bounded subtask; manager synthesizes |

Typed knobs (**not** prompt): `tool_name_override`, `tool_description_override`, `on_handoff` (log, prefetch, **downscope token**), `input_type` (Pydantic metadata: `reason`, `priority` — **does not** choose destination and **does not** replace the next agent’s input), `input_filter` / `RunConfig.handoff_input_filter`, `is_enabled` (predicate, **before** args), `nest_handoff_history` (opt-in beta; **does not** redact PII). Register **one** handoff per destination. Combine: triage **hands off** to refund; refund **calls** a policy agent as a tool. Nested `as_tool` runs **do not inherit** parent conversation unless you pass the same `session`. Server-managed `conversation_id` **does not** support handoff input filters.

LangChain independently adopted the word: (1) single agent + middleware (`@wrap_model_call` swaps prompt/tools — recommended default); (2) subgraph agents + `Command.PARENT`. Subgraph handoffs **must** pass the triggering `AIMessage` **and** a `ToolMessage` with matching `tool_call_id`. Full subagent history is optional and usually wrong. Parent `messages` needs `add_messages`; `active_agent` is LastValue; parallel `Send` workers writing a scalar without a reducer raise `InvalidUpdateError` (**05**).

**Shared vs partitioned state.**

| Style | What is shared | Isolation | Typical |
| --- | --- | --- | --- |
| Shared transcript | One `messages` reducer | Weak unless filtered | Swarm, OpenAI default handoff |
| Partitioned windows | Brief + summary / filesystem ref | Strong | Anthropic subagents |
| Blackboard + private scratch | Public board + debate spaces | Medium | LbMAS |
| A2A artifacts | Opaque callee; caller sees Task + Artifact | Strongest across orgs | Cross-company |

Anthropic appendix: **write subagent output to a filesystem** and pass **references** — avoids the telephone game and the cost of copying large artifacts through the coordinator.

### 2.3 Consensus τ (join, not topology)

Consensus attaches to a fan-in: `Send` workers, debate rounds, or self-consistency samples. The **runtime** decides: accept, another round, or **escalate**.

| Join | Mechanism | Token shape | Known numbers | Failure |
| --- | --- | --- | --- | --- |
| Majority / self-consistency | Sample k, vote on **parsed** answer | k × one CoT | PaLM-540B GSM8K **56.5% → 74.4%** at **k=40** ([Wang](https://arxiv.org/abs/2203.11171)) | Correlated errors lock a wrong majority |
| Multi-agent majority, no debate | Independent agents, vote once | N × one gen | Du arithmetic **69.0%** vs single **67.0%** — weak | Same |
| Debate (Du 2023/ICML 2024) | N propose, R critique; majority if split | ≈ N × (R+1) × ctx | **3×2**: arithmetic **81.8%** vs reflection **72.1%** vs majority **69.0%** | Agreeable RLHF collapse; shared hallucination survives |
| MAD + judge (Liang) | Two debaters; judge discriminative or extractive | 2 × rounds + judge | Adaptive break; LLM judges **unfair** across model families | Judge self-preference |
| LLM-as-judge (Zheng) | Pairwise / rubric | 1–2 calls, **swap positions** | GPT-4 **>80%** human agreement; verbosity / self-enhancement bias | Verbosity wins |
| Anthropic research judge | One call, 0.0–1.0 + pass/fail | 1 call | **Multiple judges were worse** | Rubric injection |
| MoA (Wang ICLR 2025) | Layered; each layer reads prior | layers × width | OSS MoA **65.1%** vs GPT-4o **57.5%** AlpacaEval 2.0 LC | Sequential layer latency |

**Disagreement is a signal.** Du: agents **omit** facts they disagree on. Estornell & Liu: **tyranny of the majority** — similar models converge on a shared misconception; diversity pruning (k=5 distinct) reduces the echo. Minority Sentinel (2026): majority already correct in **74.5%** of divergent cases; overturn only when P(minority-truth) > per-dataset τ with **≥95%** majority-correct preservation. Selective self-reference: 3/5 → **35.5%** majority correctness; 4/5 → **57.7%**; 5/5 → **78.7%** on that paper’s task — **τ is task-specific**. Voting-ensemble abstention: raising the threshold can lift precision (**73.1% → 93.9%** example) at the cost of yield → production mapping is **abstain → escalate**, not “guess anyway.”

**Production join policy [inferred productization]:**

1. Parse votes into a **closed** answer set (schema, not prose).
2. If agreement ≥ τ_high (often unanimous or k−1) **and** answers are independently checkable → accept.
3. If agreement < τ_low → **do not average**. Position-swapped judge, then human if the judge disagrees or the domain is irreversible (AISVS **9.2.10**).
4. Never let the same model family both propose and judge without a position-swap + second family.

Cite **04** for CoT-SC as a *single-agent* hybrid. Multi-agent debate is the **cross-instance** version of that vote. Use debate as a **verifier** on high-value, non-parallelizable answers (legal memo), not as the default topology.

### 2.4 HITL timeout-deny

HITL is a **durable wait** with an authenticated resume. Primitive tables for `interrupt()` vs `interrupt_before` and node-restart: **05**. Multi-agent additions:

| Mechanism | Pause | Resume | Durable wait? | Multi-agent note |
| --- | --- | --- | --- | --- |
| LangGraph `interrupt()` | GraphInterrupt | `Command(resume=…)` + checkpointer | Only if Agent Server / Temporal | Interrupt **inside a worker** must propagate to the parent |
| `HumanInTheLoopMiddleware` | After model, before listed tools | `approve` / `edit` / `reject` / `respond` | Same checkpointer rule | Missing `interrupt_on` key = **auto-approve**; `when` gates on args |
| OpenAI `needs_approval` | `result.interruptions` | `state.approve()/reject()` + same session | Process-held unless you persist `RunState` | Nested `as_tool` / post-handoff still surface on the **outer** run; `_max_turns=10` is **not** paused by wall-clock HITL |
| A2A `INPUT_REQUIRED` / `AUTH_REQUIRED` | Task interrupted | Client `SendMessage` on same `taskId` | Yes, by spec | Client-agent may **re-delegate** `AUTH_REQUIRED` up the chain |
| Temporal Signal + `wait_condition` | Workflow parks (zero compute) | Signal | Yes | Wait + timeout → escalate → second wait → **auto-reject** ([approval](https://docs.temporal.io/design-patterns/approval)) |
| CrewAI / AG2 `HumanClient` | Console / UI | Human types | No unless Hub WAL | Not an SLA |

LangGraph **does not** put a TTL on `interrupt()`. AISVS **9.6.2**: if approval time is not met, **block** the pending action — timeout-**deny**, not timeout-allow. EU AI Act Art. 14 is the legal twin; it does not specify the UI. OWASP **ASI09**: HITL is an **attack surface** (automation bias, confirmation fatigue). Mitigations: friction-by-design for irreversible actions, approval-budget per session, structured diffs, out-of-band confirm. Approval queues are **enterprise, not a LangGraph primitive**: capacity limit; overflow sheds low-priority, never auto-approves high-impact.

### 2.5 Four escalation edges

Escalation is a **control-plane edge**, not a prompt. Destinations: another **specialist** (`handoff` + `EscalationData` logged in `on_handoff`) or a **human** (`interrupt` / `needs_approval` / A2A `INPUT_REQUIRED`). A specialist hop is still an agent with tools; a human hop is a durable wait.

| Trigger | Detector (runtime, not LLM) | Typical action | Source |
| --- | --- | --- | --- |
| **SLA / hop budget** | Hop counter; OpenAI `max_turns` default **10**; Magentic **20**; LangGraph `recursion_limit` (**04**/**05**) | Force `escalate_to_human` after N transfers; stall → replan | SDK defaults; AISVS **9.1.2** |
| **Confidence / disagreement** | Vote fraction < τ; judge score < threshold; Magentic “looping?” | Extra debate round **or** human; do not silently majority | Du; Estornell; §2.3 |
| **Cost** | Runtime `$` / token counter on the thread | Cap subagents (Anthropic effort rules **and** hard cap); 80% budget **forbids** new `Send` | Anthropic; AISVS **9.1.2** |
| **Security / reversibility** | Tool-manifest class; **worst class in the chain** wins (**9.2.10**) | HITL before write; `AUTH_REQUIRED` for user gesture | AISVS C9.2 |

Cost-based escalation is a budget check on the **control plane**, not “the lead decides it has spent enough.” Anthropic prompt-side effort rules (simple: **1** agent, **3–10** calls; comparison: **2–4** subs, **10–15** each; complex: **>10** disjoint) are the **soft** twin; without the hard cap you get Loop D. Security-based escalation is **class promotion**: a FAQ swarm that later calls `issue_refund` must not inherit FAQ’s auto-approve. Downscope cannot be reversed by the specialist asking the supervisor to “just run it.”

### 2.6 Complexity

Let \(H\) be hops, \(W\) parallel workers in a wave, \(N\) debate agents, \(R\) critique rounds, \(L\) hierarchy levels, \(C\) A2A sibling tasks.

- **Supervisor serial** (`parallel_tool_calls=False`): \(\Theta(H)\) model calls on the critical path; wall-clock \(\approx \sum\) worker p99 + supervisor ticks.
- **Parallel `Send` / Anthropic wave:** cost \(\Theta(W)\) completions; wall-clock \(\approx \max(W)\) + join. Vague briefs duplicate work — still \(\Theta(W)\) dollars.
- **Swarm sticky turn 2:** \(\Theta(1)\) specialist call (no router) — why handoffs win on repeats.
- **Hierarchical:** \(\Theta(H \times L)\) extra splices. Pay this for IAM/SLO isolation, not for the org chart.
- **Debate:** \(\Theta(N \times (R+1))\) utterances. MoA: sequential **layers** add to p99; `Send` fan-out does not.
- **Vote join:** \(O(N)\) parse + fraction; **O(1)** policy (τ_high / τ_low). Do not call the model to “average.”
- **HITL park:** Temporal wait is **O(1)** worker CPU (zero compute). Decision latency is a **queue SLO**, orthogonal to model p99.
- **A2A:** blocking `SendMessage` inherits callee p99 + auth. Parallel siblings \(O(C)\) client-side artifact version maps.
- **Fan-out catastrophe:** Anthropic early failure — lead spawned **50** subs. Admission-control **W in code**, not in the prompt. AISVS **9.1.2**.

### 2.7 Invariants

1. **The model never routes, hands off, grants authority, counts a vote, or times out HITL.** Runtime mutates durable state (AISVS **9.5.3**).
2. **Hop cap, `$` cap, fan-out cap, and tool allowlists live in the runtime.** Prompts are advisory (Anthropic effort rules **and** a hard cap).
3. **Typed `input_type` is metadata, not authorization.** `is_enabled` cannot see parsed args. Check in `on_handoff` and **raise**.
4. **Downscope at handoff.** Worker token audience = worker MCP servers; scopes = brief. Cannot be widened by asking the supervisor.
5. **Worst reversibility class in the chain wins** (AISVS **9.2.10**). FAQ auto-approve does not survive `issue_refund`.
6. **HITL timeout-deny** (AISVS **9.6.2**). LangGraph interrupt has no TTL; you add one. Overflow never auto-approves irreversible class.
7. **Join at τ; never average below τ_low.** Same-family proposer+judge without position-swap is Zheng bias. τ is **task-specific**.
8. **Per-agent tool policy** (AISVS **9.5.1**). Lead does not hold production write tools. Judge/CitationAgent reads artifacts; does not mutate Stripe.
9. **MCP is the tool bus; A2A is the agent bus.** Do not flatten partners into `tools/call`. Terminal A2A tasks are immutable.
10. **Interrupt in a worker surfaces on the outermost graph.** Resume the top-level agent. Persist `RunState` / checkpoint before the process dies.
11. **Kill-switch is out-of-band** (AISVS **9.1.3** / **9.6.3**), not a prompt token the looping agents must choose to emit.
12. **Secrets never in model-observable / checkpointed state** (AISVS **9.5.4**; `Runtime.context` / `UntrackedValue` — **05**).

---

## 3. Token Economics & NFR Analysis

List prices: **see 01**. Working set (2026-09-23): Claude Sonnet 5 **$2 / $10** per MTok (cache hit **$0.20**); Claude Opus 5 **$5 / $25** (cache **$0.50**); GPT-5.4 **$2.50 / $15** (cache **$0.25**). ReAct fuses: **04**. MCP SKU **$0**: **08**. ⚠️ **No** vendor publishes agent-loop latency percentiles for supervisor-worker systems as of 2026-09-23. Bound them from architecture (§3.2). `$ / 1k tickets` figures are **[inferred]** from a stated hop skeleton × list prices — **not** a vendor SKU. Temporal waiting for HITL is **not** a latency SLO (parked workflows consume no worker CPU).

**Stated Loop S (not a SKU):** L1 ticket, supervisor + 2 specialists (calendar + email, or billing + refund). Reference call **2,000 input + 400 output**. Sonnet 5: \(2000\times\$2 + 400\times\$10\) per 1M = **$0.008 / call**. All figures are **model tokens only** (no web-search SKU, no Managed Agents session-hour, no LangSmith seat) unless a row says otherwise.

> ⚠️ Gap: no unpublished production p50/p95/p99 is invented here. Magentic GAIA **38%** is GPT-4o-era (tests 2024-08..10). Anthropic **+90.2%** is an **internal** research eval on Opus 4 / Sonnet 4 generation. Minority Sentinel τ is per-dataset. `langgraph-supervisor` is compatibility-only.

### 3.1 `$ per 1k` — supervisor + 2 specialists **[inferred]**

**Loop S — L1 ticket.**

| Pattern | Calls (LangChain table) | $/ticket | **$/1k tickets** |
| --- | --- | --- | --- |
| Subagents (supervisor join) | 4 | $0.032 | **$32** |
| Handoffs / sticky specialist | 3 | $0.024 | **$24** |
| Router + parallel two specialists + synth | 5 | $0.040 | **$40** |
| Same 4-call subagents, GPT-5.4 | 4 × $0.011 | $0.044 | **$44** |
| Repeat turn 2, handoffs | 2 | $0.016 | **$16 extra** |
| Repeat turn 2, subagents | 4 | $0.032 | **$32 extra** |

Coordination tax of “always return to supervisor” is **+$8 / 1k / turn** vs a sticky specialist **[inferred]**. That tax **buys** centralized policy and a single user-facing owner.

**Loop R — Anthropic 15× research.** Chat baseline 2,000 in + 500 out on Sonnet 5 = **$0.009 / chat**. Single-agent research **4×** = **$0.036** → **$36 / 1k**. Multi-agent **15×** = **$0.135** → **$135 / 1k**. Mix **30% Opus 5 + 70% Sonnet 5** on the 15× pile **[inferred]**: ~**$240 / 1k**. Anthropic: task value must exceed this; they do not publish a break-even. Claude web search **$10 / 1K searches**: 3 subs × 8 searches = **$0.24 / task** — often **larger than Sonnet tokens** on Loop S. Count it. Token use alone explains **80%** of BrowseComp variance; multi-agent **+90.2%** vs single Opus 4 on **internal** eval; parallel 3–5 subs × 3+ tools cut wall-clock **up to 90%**; better MCP descriptions **−40%** completion time. Coding is a **poor** fit (few parallelizable subtasks).

**Loop D — fan-out catastrophe.** 50 subagents × 10 calls × $0.008 = **$4 / ticket** → **$4,000 / 1k** plus the lead. AISVS **9.1.2** is an NFR, not a nice-to-have.

**Debate extra tokens [inferred from Du’s 3×2 skeleton].** Each debate utterance **1,500 in + 400 out** on Sonnet 5 = **$0.007 / utterance**.

| Consensus recipe | Utterances | Extra vs 1 greedy CoT | **$/1k extra** |
| --- | --- | --- | --- |
| Majority N=3, no debate | 3 | 2 | **$14** |
| Du debate 3 agents propose + 2 critique rounds | 3 + 6 = **9** | 8 | **$56** |
| MAD 2 debaters × ~3 adaptive rounds + judge | ~**9** | 8 | **$56** |
| Self-consistency k=5 (not 40) | 5 | 4 | **$28** |
| Self-consistency k=40 (Wang) | 40 | 39 | **$273** |

**Prompt-cache:** supervisor system prompt + worker playbooks should be **prompt-cached**. Sonnet 5 cache hit **$0.20 / MTok** vs **$2** is a **10×** input discount for the static prefix (**01**). Hierarchical supervisors with shared team prompts are the best cache shape; swarms that rewrite `active_agent` prompts every hop cache worse. Prefix-stability: **02**. `full_history` vs `last_message` is a splice tax; missing `input_filter` copies every tool payload onto the next hop.

### 3.2 Latency SLA targets

No published p99. Architecture bounds:

| Pattern | p99 (conceptual) | When it hurts |
| --- | --- | --- |
| Sequential pipeline / swarm handoff | Σ stage p99 | Sticky support is OK; three-domain research is not |
| Supervisor, `parallel_tool_calls=False` (default) | Σ worker p99 + supervisor ticks | Default `create_supervisor` **serializes** workers |
| Parallel `Send` / Anthropic wave | max(worker p99) + join + lead | Vague briefs duplicate work; join waits on the slowest |
| Debate / MoA layers | rounds × (max speaker or sequential layer) | Verification tax on the critical path |
| A2A blocking | inherits callee p99 + auth | Cold Agent Card / OAuth dominates LLM time |
| HITL | **not** an LLM SLO | Parked wait; measure **decision latency** separately |

**Worked p99 envelope for Loop S [inferred, not measured].** Supervisor route **800 ms p99**, specialist (LLM+tools) **3.5 s p99**, join/synth **1.2 s p99**. Sequential supervisor→S1→S2→join: **800+3500+3500+1200 ≈ 9.0 s**. Parallel `Send` of S1∥S2: **800+3500+1200 ≈ 5.5 s**. Swarm sticky turn 2 (no router): **3.5 s**. Debate 3×2 with sequential speakers adds **~6 × 2.0 s ≈ 12 s** extra if not parallelized. If specialists are **not** independent (S2 needs S1’s artifact), parallelization is a bug — typed handoff of the artifact id, then S2. Anthropic Research product is **synchronous** waves — the lead cannot mid-course-correct. GPT-5.4 Fast mode **99% of 5-min windows > 50 tok/s** is a **decode** SLO, not a multi-agent loop SLO (**01**). Openlayer 2026 commentary (not a lab result): supervisor-style parallelism helped some Google-reported parallel tasks (~**80%**) and **hurt** sequential reasoning (~**70%**). Direction matches Anthropic: **do not multi-agent a tightly coupled chain**.

**[inferred policy]** targets (not vendor guarantees):

| Metric | Target **[inferred policy]** | Mitigation |
| --- | --- | --- |
| **p50** Loop S (no HITL) | **2–4 s** parallel `Send`; **3–6 s** serial supervisor | `parallel_tool_calls` / `Send` when specialists are independent; cache supervisor prefix; `last_message` + filesystem refs |
| **p95** Loop S | **5–8 s** parallel; **8–12 s** serial | Cap tool I/O; disable extra debate on L1; sticky swarm on turn 2 skips the router |
| **p99** Loop S | **Fail closed at 9–15 s** with hop cap | Hop cap **3** (support) / effort cap (research); per-specialist breaker; degraded JSON. Unbounded `Send` / ping-pong has **no** p99 |
| **p50** research wave | Dominated by max(sub) | 3–5 isolated subs × 3+ tools; Anthropic ≤**90%** wall-clock vs sequential |
| **HITL decision p95** | Queue SLO (e.g. 15 min L1 / 4 h L2) | Temporal timeout-deny; overflow shed; **not** added to model p99 |
| **A2A first-byte** | Auth + Agent Card | Warm cards; mTLS; do not block the user SSE on a cold callee |

### 3.3 Throughput and back-pressure

| Knob | Value | $ / latency effect |
| --- | --- | --- |
| In-flight completions / ticket | Supervisor + 2 parallel specialists = **3** during the wave | Multiplies provider RPM/ITPM (**01**). Anthropic Start OTPM can bind before CPU |
| `Send` width | Admission-control **W** in code (simple 1; comparison 2–4; hard cap <<50) | Loop D **$4,000 / 1k** if the lead invents 50 |
| OpenAI `max_turns` | Default **10** | Not a HITL SLA; raise / `None` while parked |
| Magentic | `max_stalls=3`, `max_turns=20` | Stall → replan, not re-handoff |
| Debate | 3×2 = **9** utterances | **+$56 / 1k** and **~12 s** sequential — verifier role only |
| GroupChat | **N−1** extra context injections / turn | Tokens ∝ N²; use Network channels / supervisor |
| HITL queue | Capacity + overflow policy | Rubber-stamp (ASI09) if every tool is `interrupt_on` |
| Web search SKU | **$10 / 1K**; 3×8 = **$0.24 / task** | Cap searches; can exceed Loop S tokens |

**Back-pressure design:**

1. Admit the **ticket** iff the supervisor breaker ∈ {closed, half-open} **or** you take the cheap-router / degraded path. A dead supervisor with `parallel_tool_calls=False` stalls the whole ticket — subgraph isolation (**05**) lets a **team** fail without poisoning the parent **if** the parent does not join that channel.
2. Shed in order: skip debate on L1 → cap `Send` width → forbid new handoffs at 80% `$` → HITL-shed low-priority reads → deterministic `status: "degraded"` JSON. **Never** 50-retry a poison specialist into the transcript. **Never** auto-approve irreversible class to drain HITL.
3. Size **in-flight completions** (W+1 per ticket in a wave), not just user QPS. Provider RPM tables live in **01**.
4. A2A: one remote `FAILED` does not kill `contextId`; spawn a refinement task. Do not convert `AUTH_REQUIRED` into a graph deadlock when the human queue is full — that is **queue overflow**.

### 3.4 Non-functional requirements

| NFR | Working target | Tension |
| --- | --- | --- |
| **Availability** | **99.9% control plane [policy]**. Per-specialist breaker; Cursor-style isolation (one worker 500s does not kill FAQ). Hierarchical: other teams proceed if top-level does not join. A2A `FAILED` ↛ kill `contextId` | Supervisor is a **SPOF** on serial topology; cheap Haiku router + Opus lead-on-complexity vs one Opus on every ticket; failover busts prefix cache (**02**) |
| **RPO** | Interactive: last **checkpointed** super-step / persisted `RunState`. HITL: last queue row (interrupt id + canonical args). A2A: last Task status; terminal is immutable so refinements are new `taskId`. Artifacts: object-store refs, not blobs in the transcript | `InMemorySaver` / process-held OpenAI run: RPO = **crash loses the approval**. Laptop `invoke()` is not a checkpointer |
| **RTO** | Stateless resume from checkpoint / `RunState` / `taskId`. Interactive hop timeout **2–8 s [policy]** then breaker. HITL RTO is the **queue SLA** (minutes–hours), not model RTO. Rainbow deploy: dual-run old/new; **pin prompt version on the thread** so in-flight research survives a cutover (Anthropic) | Fast degraded JSON vs bit-identical specialist result; cutting over a Pregel schema mid-plan (rainbow-unsafe) |
| **Consistency** | `active_agent` LastValue — disable parallel handoffs or you race (**05**). Vote join is a **pure function** of the parsed vector + τ. Artifact hash must equal cited content (CitationAgent). A2A list-tasks **authorization-scoped** | Eventual artifact write vs lead synthesizing a stale summary; `output_mode=last_message` dropping the only fact the next team needed |
| **Compliance** | AISVS C9: budgets **9.1.2**, kill-switch **9.1.3/9.6.3**, approval manifests **9.2**, timeout-deny **9.6.2**, per-agent tools **9.5.1**, downscoped user token **9.5.2**, policy engine not the model **9.5.3**, delegation policy **9.5.5**, crypto agent identity **9.4.1**. OWASP ASI03/07/08/09. EU AI Act Art. 14. WORM of handoff/vote/HITL. MCP **MUST NOT** passthrough (**08**) | Hosted lead (claude.ai) vs VPC tools; nested handoff history re-embeds PII in summaries; A2A artifacts crossing org boundaries |
| **Cost vs latency** | Loop S **[inferred] $24–32 / 1k**; Loop R **$135–240 / 1k** before search SKU; debate **+$56 / 1k**; Loop D **$4,000 / 1k**. Parallel `Send` cuts Loop S **~9.0 s → ~5.5 s** and **multiplies** in-flight RPM | Sticky handoff saves **$8 / 1k / turn** and **loses** centralized join; debate as default topology buys factuality and **burns** p99 |
| **Isolation vs tokens** | Partitioned windows + filesystem refs minimize PII copies and telephone game; shared transcript is cheaper on sticky support | `input_filter` too aggressive → specialist asks questions the user already answered; must log **hash of dropped items** for IR |

---

## 4. Distributed Resilience & Security

### 4.1 Durable HITL as Temporal / Kafka

Application state ≠ KV cache and ≠ chat text. The multi-agent **equivalent** of a Temporal Workflow + Kafka compacted log is:

- **Start Workflow** = first supervisor tick that writes `(thread_id, active_agent, hops=0, remaining_usd, policy_version)` **before** any specialist tool. **Idempotency key** for mutating tools is a **required argument** minted by the **session**, never the model (**03**).
- **Activity** = specialist inner loop / MCP `tools/call`. Temporal RetryPolicy is **not** a breaker (**04**/**05**). LLM calls must **not** re-run on workflow **replay** — map graph nodes → Activities (Temporal LangGraph plugin).
- **HITL Signal** = `__interrupt__` / `ToolApprovalItem` / `needs_approval` → persist checkpoint / `RunState` → `wait_condition(..., timeout=)` (zero compute). Notify Activity to the L1/L2 console. Timeout → escalate to manager → second wait → **auto-reject** (AISVS **9.6.2**). Documented Temporal approval pattern + [HITL cookbook](https://docs.temporal.io/ai/cookbook/human-in-the-loop-python).
- **A2A as saga:** remote agents expose a public state enum. `AUTH_REQUIRED` is an interrupt, not an error. Terminal tasks **immutable** — spawn a refinement in the same `contextId` with `referenceTaskIds`. Push-notification capability must be declared.
- **DLQ** = ping-pong traces (reciprocal transfers ≥ hop cap), repeating specialist 500s, HITL rows past second timeout, A2A `FAILED` with no refinement, CrewAI “coworker not found” (control-plane page, **not** an LLM retry).

**Kafka / outbox mapping:** `orch.intents` (thread_id + hop + remaining_usd **before** side effect), `orch.handoffs` (typed Handoff + dropped_hash + jti), `orch.votes`, `hitl.approvals` → Signal the waiter, `orch.dlq`, `orch.artifacts`. Compaction on `thread_id` keeps a snapshot; the full log is chain-of-custody. AG2 Network: Hub WAL is the same idea. Temporal Event History **is** the audit.

**Replay vs resume:** graph time-travel **re-fires** interrupts (**05**). Safe to retry: reads, `tasks/get`, artifact GET by id. **Not** safe: `issue_refund`, `send_email` — map duplicates to the original receipt. OpenAI: serialize `RunState`; do not let `_current_turn` burn the default 10 turns while a human is at lunch.

**When work is durable:**

| System | Durable after | Mitigation |
| --- | --- | --- |
| Laptop `invoke()` interrupt | Nowhere — process death drops it | Agent Server / Temporal / persist `RunState` |
| OpenAI `needs_approval` | After you serialize `RunState` | Resume **top-level** agent; raise `max_turns` |
| LangGraph + Postgres checkpointer | After super-step commit (**05**) | Idempotent `@task` around sends |
| Temporal `wait_condition` | After Workflow task complete | Timeout-deny; not an LLM SLO |
| A2A Task | After server accepts `taskId` | Refinement ≠ restart completed |
| Console `HumanClient` | Nowhere | Not an SLA |

### 4.2 Supervisor as SPOF, failure taxonomy, ping-pong poison, breaker, fallbacks

**Supervisor SPOF symptoms:** p99 ≈ lead think time + max(slowest worker); lead context fills with summaries; cannot steer in-flight subs (Anthropic: **synchronous** waves). Hierarchical: top-level waits on entire teams. Magentic inner loop assigns **one** worker — slower but bounded. A dead supervisor with `parallel_tool_calls=False` stalls the whole ticket.

**Mitigations:** effort-scaling in the prompt **and** a hard cap in the runtime; Haiku/cheap **router** + Opus **lead** only when a complexity score fires; `last_message` + filesystem artifacts; A2A/Temporal **async** tasks with progress; split citation to async post-process; Magentic `max_stalls` then **replan**. Rainbow deploys so in-flight graphs are not cut over mid-plan.

| Failure | Supervisor-worker (sync wave) | Handoff swarm | A2A remote | Hierarchical team |
| --- | --- | --- | --- | --- |
| One worker 500s | Whole wave blocks | Conversation stuck on that agent | Task `FAILED`; context continues | Other teams proceed if top-level does not join |
| Infinite tool loop | `max_turns` / AISVS budget | Same | Server-side timeout | Team-level `max_turns` |
| Poisoned context | Isolated if sub has own window (Anthropic win) | **Contaminates** sticky history | Opaque — callee’s problem; you see artifacts | Team checkpointer isolates |
| Kill one worker identity | Remaining workers + replan | Need a handoff off the dead agent | New Agent Card version | Replace compiled subgraph |

**Deadlock patterns unique to multi-agent:** (1) reciprocal handoffs with no hop cap; (2) GroupChat `auto` + fully connected graph + `allow_repeat_speaker=True` — no progress predicate; (3) parallel `Send` workers waiting on each other’s unwritten keys (no reducer / cyclic dependence); (4) A2A client blocking on `AUTH_REQUIRED` while the HITL queue is full — **overflow**, not a graph cycle; (5) CAMEL thank-you loop — agents *know* they are looping and still fail to stop; fuse on repeat tokens.

**Failure taxonomy.**

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408/429/5xx/529, TLS reset, one specialist timeout | Full jitter; same idempotency key; do not retry `isError` / RBAC deny |
| **Permanent** | 400 schema, 401/403, hop cap, `$` cap, τ_low, RBAC deny, A2A `REJECTED`, `aud` miss | Fail the **hop** or escalate. Do not failover a 400 to an unauthenticated specialist |
| **Poison pill (ping-pong)** | `transfer_to_sales` ↔ `transfer_to_support`; CrewAI manager delegating to itself; CAMEL `Instruction:` in assistant output | Hop counter; after **N** transfers **force human**; `is_enabled` predicates; allowed-transition graph; disable parallel tool calls so two handoffs cannot fire in one tick |
| **Poison pill (fan-out)** | 50 subs on trivia; vague briefs → duplicate 2025 supply-chain search | Effort rules + **hard** W cap; brief template: objective, sources, **out of scope**; overlap metric on query embeddings |
| **Poison pill (vote)** | Three agents agree on a shared hallucination; same-family judge | Diversity pruning; cross-family proposers; position-swap; escalate on gray; citation checks independent of the vote |
| **Poison pill (HITL)** | Every tool `interrupt_on`; operators rubber-stamp (ASI09); `MaxTurnsExceeded` while waiting | Interrupt **write** tools only; `when` on amount/ACL; approval budget; timeout-deny; overflow shed — never auto-approve irreversible |
| **Confused deputy (agent)** | Supervisor has GitHub admin; worker “update the README” executed with supervisor creds ([CSA 2026-03](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/03/CSA_research_note_ai-agent-confused-deputy-prompt-injection-chains_20260323-csa-styled.pdf)) | Downscope at handoff; never “lead calls all tools on behalf of workers” |
| **Context loss** | Aggressive `input_filter`; missing `AIMessage`+`ToolMessage` pair; A2A missing `referenceTaskIds` | Typed metadata **plus** a brief; log dropped hashes; filesystem refs |
| **Rainbow-unsafe deploy** | In-flight graph schema mismatch | Dual-run old/new; pin prompt version on the thread |
| **Magentic password-reset spiral** | Login misconfig → agents reset the account password after lockout | Circuit on auth tools; human on account mutation |
| **o1/policy refusals** | Magentic: o1 **refused 26%** WebArena Gitlab, **12%** Shopping Admin | Don’t put the policy-heavy model on write tools — coverage **shrinks** |

**Ping-pong** is the multi-agent poison pill: hop count explodes; user sees “let me transfer you” loops; token burn without a final `AIMessage`. Causes: overlapping prompts; reciprocal handoff tools always enabled; no `max_turns`; swarm without hop cap. Mitigations above. CAMEL role flipping and infinite thank-you loops are the 2023 ancestor of the same fuse.

**Circuit breaker** (one per **specialist**, plus one per **provider**, plus one per **A2A callee**). Open on high **5xx/529/timeout** rate. **Do not** open solely on 429-with-Retry-After. **Do not** open the billing breaker because web-search 429d. Half-open: probe with a **cheap read** (`kb_search` / `tasks/get` of a canary), not `issue_refund`. After N consecutive **transport** failures, trip **that** agent for T seconds, **keep others**; do not poison the model with a 50-retry loop. CrewAI “coworker not found” is a **control-plane** page — do not wrap it in LLM retry.

```
           5xx/529/timeout rate ≥ threshold           probe success
  ┌────────┐  ──────────────────────────────────▶  ┌──────┐  ──────▶ CLOSED
  │ CLOSED │                                       │ OPEN │
  └───┬────┘  429 with Retry-After = throttle      └──┬───┘
      │       (stay CLOSED; sleep)                    │ timer (e.g. 30 s)
      │ success resets window                         ▼
      │                                          ┌──────────┐
      └──────────────────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                                 │ 1 cheap  │
                                                 │ read     │
                                                 └──────────┘
```

**Fallback chain:** primary specialist (audience-bound, downscoped) → secondary **equivalent** specialist (same IR, **new** jti) → **deterministic** schema-valid decline (`status: "degraded"`, no refund, no email). Supervisor-class open: cheap router or degraded JSON — **do not** invent a second payments processor. **PermanentError** on hop cap / `$` cap / τ_low / RBAC **does not** failover to the supervisor’s union toolset or to token passthrough. A2A `FAILED`: refinement task, not restart. Search API dead: tell the model the tool is failing (Anthropic) **and** trip the breaker so the lead is not spending Opus tokens narrating a 529.

### 4.3 Zero-Trust per-agent tools, RBAC downscope on handoff

Two confused-deputy layers. **OAuth proxy deputy** is **08** (static IdP `client_id` + DCR + consent cookie). **Agent deputy (this file):** supervisor has GitHub admin; user asks a worker to “update the README”; worker issues a tool call that the supervisor **executes with supervisor credentials**. CSA (2026-03): most documented multi-agent architectures **propagate authority implicitly**. A single injection into the orchestrator becomes **lateral authority propagation** across every worker. Magentic-One appendix: alignment that works on a single turn failed across a multi-agent escalation chain; **crescendo** jailbreaks are more dangerous when one agent’s compliance teaches the next.

Fix: **downscope at handoff** — `on_handoff` mints a token whose audience is the worker’s MCP servers and whose scopes match the brief. A2A `AUTH_REQUIRED` when the callee needs a user gesture. Never “the lead calls all tools on behalf of workers.” AISVS **9.5.1** per-agent tool+parameter policy; **9.5.2** integrity-protected, scope-limited user token at every hop; **9.5.3** policy engine, never the model; **9.5.5** explicit inter-agent delegation policy; **9.4.1** cryptographic agent identity. MCP **MUST NOT** passthrough the client token (**08**). RFC 8707 `aud` on **every** MCP hop.

| Principal | May | Must not |
| --- | --- | --- |
| Router / lead | Spawn workers, read summaries, write plan Memory | Hold production write tools (Stripe, email send) |
| Domain specialist | Its tool allowlist | Other specialists’ tools; raw user refresh tokens |
| Citation / critic / judge | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail (ASI09) |
| A2A callee | Skills on its Agent Card | Your VPC except via published artifacts |

CrewAI/LangGraph “give the manager all tools so it can help” **destroys** isolation. Hierarchical IAM: team supervisor has **delegation** rights, not **union of worker tools**. A2A skill-level `securityRequirements` is the protocol’s RBAC hook. Extended Agent Cards hide sensitive skills until authenticated. `HumanInTheLoopMiddleware`: tools **absent** from `interrupt_on` auto-approve — privilege bug if a new write tool is registered and forgotten. OpenAI: tool-input guardrails **do not wrap handoffs** — policy at the **worker**.

Zero-Trust: short-lived, per-agent, per-session credentials. Least privilege on **every** MCP `tools/call` (**08**). AISVS **9.5.4**: secrets not in the model-observable context. `is_enabled` hides `transfer_to_refund` unless `order_id` in state — necessary, **not** sufficient; `on_handoff` still re-checks.

**RBAC tuple at handoff:** `(principal, tenant, from_agent, to_agent, tools_enabled, jti, policy_version)`. Resume / `Command(resume=)` values must not concatenate into a new write without **re-RBAC**. Identity (`tenant_id`, `Authorization`) from specialist **arguments** is untrusted — **03** dispatcher rule still applies.

Isolation ladder: OAuth scope filter (cheapest; app-bug can omit) → **gateway `Mcp-Name` allowlist** (**08**) → **per-agent allowlist in the orchestrator** (this file) → **separate MCP servers** for payments vs FAQ → **separate conversations** for secrets-bearing specialists (prompt isolation). Prefixing tool names reduces accidental collision; it is **not** a security boundary against a supervisor that still holds the PAT.

### 4.4 PII in transcripts, WORM provenance

Every extra hop is a **copy**. Subagents with isolated windows are **better** for PII minimization if the brief strips identifiers and the sub returns aggregates. OpenAI default handoff passes **full history** — prior-turn PII lands in the refund agent. Filters: `input_filter`, LangChain “pass only the handoff pair,” Anthropic filesystem refs. Nested handoff history can **re-embed** tool args in summaries. Blackboards are **worse** unless partitioned. A2A artifacts may be files — classify before crossing org boundaries. Handoffs that **filter history** must log **what was dropped** (hash of omitted items), or IR cannot reconstruct why the specialist lacked context.

**PII pipeline:** detect → redact → audit at ingress **and** before each handoff splice **and** before trace export. Deterministic + ML DLP **after** the specialist result, **before** the next model turn. Never log raw transcripts in shared SaaS traces. PCI: PAN never in `messages`. Judge/CitationAgent should see **artifact ids + redacted claims**, not the raw ticket dump.

**Immutable WORM audit** (append-only row):

`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send|vote), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate, artifact_ids, vote_vector, disagreement_τ, dropped_hash, remaining_usd, hops`

OpenAI: `handoff` spans; `on_handoff` for business metadata. LangSmith: graph node + tool spans. A2A: `taskId`+`contextId`+status transitions. Temporal: Event History **is** the audit. AG2 Network: Hub WAL. AISVS **9.4.2**: bind actions cryptographically along the chain. Provider traces (`store=true`, 30-day retention unless ZDR) are **not** a SIEM.

Delete/tombstone: revoking the user’s grant **must** stop worker writes even if a sticky `active_agent` is still in the prompt; handle TTL + re-check authz every hop. A shared blackboard that retains User A’s ticket in User B’s research wave is a tenancy incident, not a cache tuning miss.

**OWASP Agentic Top 10 concentrated here:** **ASI07** Insecure Inter-Agent Communication (A2A/MCP without mTLS/audience; unsigned envelopes); **ASI08** Cascading Agent Failures (fan-out, ping-pong, retry storms); **ASI03** Identity/Privilege Abuse (delegation without downscope); **ASI09** HITL exploitation. AISVS C9 is the control catalog.

---

## 5. Production Enterprise Code

Assumptions match research: HTTP `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2`; Loop S hop cap **3**; `$` cap **$0.05**/ticket with 80% spawn freeze; τ_high **1.0**, τ_low **0.5**; HITL SLA **900 s** then timeout-deny; per-specialist breaker; fallback → `degraded`. Run: `python orchestrator_runtime.py`.

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

INITIAL_RETRY_DELAY, MAX_RETRY_DELAY, SDK_DEFAULT_MAX_RETRIES = 0.5, 8.0, 2
HOP_CAP, USD_CAP, USD_FREEZE_FRAC = 3, 0.05, 0.80
TAU_HIGH, TAU_LOW, HITL_SLA_S = 1.0, 0.50, 900.0
CALL_USD = 0.008  # Loop S Sonnet 5 2k/400 [inferred]
GATEWAY_AUD = "https://mcp.gateway.example"

T = TypeVar("T")

AGENT_TOOLS: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"route", "forward_message"}),
    "faq": frozenset({"kb_search"}),
    "billing": frozenset({"lookup_invoice"}),
    "refund": frozenset({"lookup_invoice", "issue_refund"}),
    "researcher": frozenset({"web_search", "fs_write"}),
    "judge": frozenset({"read_artifact"}),
}
TRANSITIONS: dict[str, frozenset[str]] = {
    "supervisor": frozenset({"faq", "billing", "refund", "researcher", "judge", "human"}),
    "faq": frozenset({"billing", "human", "supervisor"}),
    "billing": frozenset({"refund", "human", "supervisor"}),
    "refund": frozenset({"human", "supervisor"}),
    "researcher": frozenset({"judge", "human", "supervisor"}),
    "judge": frozenset({"human", "supervisor"}),
    "human": frozenset(),
}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
            "msg": record.getMessage(), "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None), "user_hash": getattr(record, "user_hash", None),
            "plane": getattr(record, "plane", None), "from_agent": getattr(record, "from_agent", None),
            "to_agent": getattr(record, "to_agent", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(
    correlation_id: str, tenant: str, user_id: str | None = None,
    plane: str | None = None, from_agent: str | None = None, to_agent: str | None = None,
) -> CorrelationAdapter:
    base = logging.getLogger("orch.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if user_id:
        extra["user_hash"] = hashlib.sha256(user_id.encode()).hexdigest()[:12]
    if plane:
        extra["plane"] = plane
    if from_agent:
        extra["from_agent"] = from_agent
    if to_agent:
        extra["to_agent"] = to_agent
    return CorrelationAdapter(base, extra)


class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after, self.status = retry_after, status


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """One breaker per specialist / provider. Do not trip on 429-with-Retry-After."""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_seconds, self.half_open_max = recovery_seconds, half_open_max
        self._state, self._failures, self._opened_at = BreakerState.CLOSED, 0, 0.0
        self._half_open_inflight, self._lock = 0, asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
                self._state, self._half_open_inflight = BreakerState.HALF_OPEN, 0
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    async def record_success(self) -> None:
        async with self._lock:
            self._failures, self._half_open_inflight, self._state = 0, 0, BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state, self._opened_at, self._half_open_inflight = BreakerState.OPEN, time.monotonic(), 0

    @property
    def state(self) -> BreakerState:
        return self._state


async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """HTTP/transport loop ONLY. Full jitter. Never wrap PermanentError / CircuitOpenError."""
    last: Exception | None = None
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
            sleep_s = ra if ra is not None and 0 < ra <= 60 else random.random() * min(cap, base * (2 ** i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


def redact_pii(text: str) -> str:
    return re.sub(r"(?<!\d)(?:\d[\- ]*){8,}\d(?!\d)", "[PII]", text)


class RouteKind(Enum):
    FINISH = "finish"
    HANDOFF = "handoff"
    SPAWN = "spawn"
    VOTE = "vote"
    ESCALATE_HUMAN = "escalate_human"


@dataclass(frozen=True)
class RouteAction:
    """Structured action the model emitted. Runtime decides whether it executes."""
    kind: RouteKind
    target: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    votes: tuple[str, ...] = ()
    spawn_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Handoff:
    from_agent: str
    to_agent: str
    reason: str
    priority: str
    order_id: str | None
    artifact_ids: tuple[str, ...]
    dropped_hash: str
    token_jti: str
    tools_enabled: tuple[str, ...]


@dataclass
class HitlRow:
    interrupt_id: str
    thread_id: str
    tool: str
    canonical_args: dict[str, Any]
    queued_at: float
    sla_s: float
    decision: str | None = None


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


@dataclass(frozen=True)
class AccessToken:
    raw: str
    aud: tuple[str, ...]
    sub: str
    agent: str
    tools: frozenset[str]
    jti: str


def mint_downscoped(inbound: AccessToken, to_agent: str) -> AccessToken:
    """MUST NOT passthrough inbound.raw. Worker tools ⊆ AGENT_TOOLS[to_agent]."""
    if not inbound.raw:
        raise PermanentError("missing_inbound")
    tools = AGENT_TOOLS[to_agent]
    material = f"obo:{inbound.sub}:{to_agent}:{GATEWAY_AUD}:{inbound.raw}"
    outbound_raw = hashlib.sha256(material.encode()).hexdigest()
    if outbound_raw == inbound.raw:
        raise PermanentError("token_passthrough")
    jti = hashlib.sha256(f"{outbound_raw}:{to_agent}".encode()).hexdigest()[:16]
    return AccessToken(outbound_raw, (GATEWAY_AUD, f"mcp://{to_agent}"), inbound.sub, to_agent, tools, jti)


def join_votes(answers: tuple[str, ...], *, tau_high: float = TAU_HIGH, tau_low: float = TAU_LOW) -> dict[str, Any]:
    """Closed-set majority. Never average below tau_low."""
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


class HitlQueue:
    """Durable wait stand-in. Timeout-deny (AISVS 9.6.2), never timeout-allow."""

    def __init__(self) -> None:
        self._rows: dict[str, HitlRow] = {}

    def enqueue(self, row: HitlRow) -> HitlRow:
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
        if now - row.queued_at >= row.sla_s:
            row.decision = "timeout_deny"
            return "timeout_deny"
        return "pending"


class Orchestrator:
    """Runtime that executes RouteAction. The model never calls this implicitly."""

    def __init__(self, state: ThreadState, inbound: AccessToken, log: CorrelationAdapter) -> None:
        self.state, self.inbound, self.log = state, inbound, log
        self.hitl = HitlQueue()
        self.tokens: dict[str, AccessToken] = {"supervisor": inbound}
        self.breakers = {n: BreakerStateMachine(n, failure_threshold=1) for n in AGENT_TOOLS}

    def _charge(self, calls: int = 1) -> None:
        delta = CALL_USD * calls
        self.state.spent_usd += delta
        self.state.remaining_usd = USD_CAP - self.state.spent_usd
        if self.state.remaining_usd < 0:
            raise PermanentError("usd_cap")

    def _audit(self, **row: Any) -> None:
        rec = {
            "ts": time.time(), "trace_id": self.log.extra.get("correlation_id"),
            "from_agent": self.state.active_agent, "hops": self.state.hops,
            "remaining_usd": round(self.state.remaining_usd, 6), **row,
        }
        self.state.worm.append(rec)
        self.log.info("worm %s", rec.get("mechanism"), extra={"from_agent": rec.get("from_agent"), "to_agent": rec.get("to_agent")})

    def _assert_transition(self, to_agent: str) -> None:
        allowed = TRANSITIONS.get(self.state.active_agent, frozenset())
        if to_agent not in allowed:
            raise PermanentError(f"transition_denied:{self.state.active_agent}->{to_agent}")

    def _ping_pong(self, to_agent: str) -> None:
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
        token = mint_downscoped(self.inbound, action.target)
        if "issue_refund" in token.tools and action.target != "refund":
            raise PermanentError("rbac_union_leak")
        self.tokens[action.target] = token
        dropped_hash = hashlib.sha256(omitted.encode()).hexdigest()[:16]
        rec = Handoff(
            src, action.target, action.reason,
            str(action.metadata.get("priority", "p3")),
            action.metadata.get("order_id"),
            tuple(action.metadata.get("artifact_ids") or ()),
            dropped_hash, token.jti, tuple(sorted(token.tools)),
        )
        self.state.active_agent = action.target
        self._audit(
            mechanism="handoff", from_agent=src, to_agent=rec.to_agent, token_jti=rec.token_jti,
            tools_enabled=rec.tools_enabled, dropped_hash=rec.dropped_hash,
            input_type={"reason": rec.reason, "priority": rec.priority, "order_id": rec.order_id},
            human_gate=None,
        )
        return rec

    def spawn(self, action: RouteAction) -> dict[str, Any]:
        targets = action.spawn_targets
        if not targets:
            raise PermanentError("empty_spawn")
        if len(targets) > 4:
            raise PermanentError("fanout_cap")
        if self.state.spent_usd >= USD_CAP * USD_FREEZE_FRAC:
            raise PermanentError("usd_freeze_no_spawn")
        self._charge(1 + len(targets))
        self.state.hops += 1
        self._audit(mechanism="Send", to_agent=",".join(targets), human_gate=None, vote_vector=None)
        return {"status": "spawned", "targets": targets, "spent_usd": self.state.spent_usd}

    def vote(self, action: RouteAction) -> dict[str, Any]:
        joined = join_votes(action.votes)
        self._charge(len(action.votes))
        self._audit(mechanism="vote", vote_vector=joined["vector"], disagreement_tau=TAU_HIGH, human_gate=None)
        if joined["status"] == "escalate":
            return self.escalate("tau_low")
        return joined

    def escalate(self, reason: str, *, tool: str | None = None, args: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
        interrupt_id = str(uuid.uuid4())
        if tool:
            queued = now if now is not None else time.time()
            row = HitlRow(interrupt_id, self.state.thread_id, tool, args or {}, queued, HITL_SLA_S)
            self.hitl.enqueue(row)
        self.state.active_agent = "human"
        self._audit(mechanism="escalate_human", to_agent="human", human_gate=reason, interrupt_id=interrupt_id)
        return {"status": "escalated", "reason": reason, "interrupt_id": interrupt_id}

    async def call_tool(self, agent: str, name: str, fn: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        token = self.tokens.get(agent)
        if token is None or name not in token.tools:
            raise PermanentError("rbac_deny")
        breaker = self.breakers[agent]

        async def _once() -> dict[str, Any]:
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


async def _offline() -> None:
    cid, tenant, user = str(uuid.uuid4()), "acme", "usr_1"
    log = build_logger(cid, tenant, user, plane="control")
    inbound = AccessToken("sup_raw_token", (GATEWAY_AUD,), user, "supervisor", AGENT_TOOLS["supervisor"], "jti-sup")
    st = ThreadState(thread_id="thr-1", tenant=tenant)
    orch = Orchestrator(st, inbound, log)

    rec = orch.handoff(
        RouteAction(RouteKind.HANDOFF, "billing", "invoice lookup", {"priority": "p2", "order_id": "ord_1"}),
        omitted="prior tool payloads",
    )
    assert rec.to_agent == "billing" and rec.order_id == "ord_1"
    assert rec.token_jti != inbound.jti and "issue_refund" not in rec.tools_enabled
    assert redact_pii("card 4111111111111111") == "card [PII]"

    try:
        orch.handoff(RouteAction(RouteKind.HANDOFF, "refund", "no order"), "x")
        raise AssertionError("order_id")
    except PermanentError as exc:
        assert "refund_requires_order_id" in str(exc)

    rec2 = orch.handoff(
        RouteAction(RouteKind.HANDOFF, "refund", "L2", {"priority": "p1", "order_id": "ord_1"}),
        omitted="billing tools",
    )
    assert rec2.to_agent == "refund" and "issue_refund" in rec2.tools_enabled
    try:
        orch.handoff(RouteAction(RouteKind.HANDOFF, "faq", "ping"), "x")
        raise AssertionError("pong")
    except PermanentError as exc:
        assert "transition_denied" in str(exc)

    st_pp = ThreadState(thread_id="thr-pp", tenant=tenant)
    orch_pp = Orchestrator(st_pp, inbound, log)
    orch_pp.handoff(RouteAction(RouteKind.HANDOFF, "faq", "sticky"), "omit")
    try:
        orch_pp.handoff(RouteAction(RouteKind.HANDOFF, "supervisor", "back"), "omit")
        raise AssertionError("pong")
    except PermanentError as exc:
        assert "hop_cap_escalate_human" in str(exc)

    st2 = ThreadState(thread_id="thr-2", tenant=tenant)
    orch2 = Orchestrator(st2, inbound, log)
    for target in ("faq", "billing", "refund"):
        meta = {"order_id": "ord_9"} if target == "refund" else {}
        orch2.handoff(RouteAction(RouteKind.HANDOFF, target, "hop", meta), "omit")
    try:
        orch2.handoff(RouteAction(RouteKind.HANDOFF, "human", "cap"), "omit")
        raise AssertionError("cap")
    except PermanentError as exc:
        assert "hop_cap" in str(exc)

    st3 = ThreadState(thread_id="thr-3", tenant=tenant, spent_usd=USD_CAP * 0.85)
    orch3 = Orchestrator(st3, inbound, log)
    try:
        orch3.spawn(RouteAction(RouteKind.SPAWN, spawn_targets=("researcher", "researcher")))
        raise AssertionError("freeze")
    except PermanentError as exc:
        assert "usd_freeze_no_spawn" in str(exc)
    try:
        Orchestrator(ThreadState("thr-w", tenant), inbound, log).spawn(
            RouteAction(RouteKind.SPAWN, spawn_targets=tuple(f"s{i}" for i in range(50)))
        )
        raise AssertionError("fanout")
    except PermanentError as exc:
        assert "fanout_cap" in str(exc)

    accept = join_votes(("ok", "ok", "ok"))
    assert accept["status"] == "accept" and accept["frac"] == 1.0
    gray = join_votes(("a", "a", "b"))
    assert gray["status"] == "judge"
    low = Orchestrator(ThreadState("thr-v", tenant), inbound, log).vote(
        RouteAction(RouteKind.VOTE, votes=("x", "y", "z"))
    )
    assert low["status"] == "escalated" and low["reason"] == "tau_low"

    parked = orch.escalate("security_write", tool="issue_refund", args={"amount": 40.0, "order_id": "ord_1"}, now=0.0)
    assert orch.hitl.resolve(parked["interrupt_id"], now=HITL_SLA_S + 1) == "timeout_deny"
    row_ok = orch.hitl.enqueue(HitlRow("i2", "thr-1", "issue_refund", {"amount": 5}, queued_at=time.time(), sla_s=HITL_SLA_S))
    assert orch.hitl.resolve(row_ok.interrupt_id, now=time.time(), decision="approve") == "approve"
    try:
        orch.hitl.enqueue(HitlRow("i3", "thr-1", "kb_search", {}, queued_at=0, sla_s=1))
        raise AssertionError("read-hitl")
    except PermanentError as exc:
        assert "hitl_not_write_tool" in str(exc)

    async def boom() -> dict[str, Any]:
        raise TransientError("529", status=529)

    async def ok() -> dict[str, Any]:
        return {"ok": True}

    degraded = await orch.call_tool("billing", "lookup_invoice", boom)
    assert degraded["status"] == "degraded" and orch.breakers["billing"].state is BreakerState.OPEN
    orch4 = Orchestrator(ThreadState("thr-4", tenant), inbound, log)
    orch4.handoff(RouteAction(RouteKind.HANDOFF, "faq", "q"), "omit")
    faqs = await orch4.call_tool("faq", "kb_search", ok)
    assert faqs == {"ok": True}
    try:
        await orch.call_tool("billing", "issue_refund", ok)
        raise AssertionError("rbac")
    except PermanentError as exc:
        assert "rbac_deny" in str(exc)
    try:
        mint_downscoped(AccessToken("", (GATEWAY_AUD,), user, "supervisor", AGENT_TOOLS["supervisor"], "x"), "faq")
        raise AssertionError("empty")
    except PermanentError as exc:
        assert "missing_inbound" in str(exc)

    rec_log = logging.LogRecord("orch.runtime", logging.INFO, __file__, 0, "probe", (), None)
    rec_log.correlation_id, rec_log.tenant, rec_log.user_hash = cid, tenant, "abc"
    rec_log.from_agent, rec_log.to_agent, rec_log.plane = "supervisor", "billing", "control"
    parsed = json.loads(JsonLogFormatter().format(rec_log))
    assert parsed["correlation_id"] == cid and parsed["from_agent"] == "supervisor"
    assert any(r.get("mechanism") == "handoff" for r in orch.state.worm)

    print(json.dumps({
        "ok": True, "cid": cid, "loop_s_subagents_per_1k": 32, "loop_s_handoffs_per_1k": 24,
        "billing_tools": rec.tools_enabled, "refund_jti": rec2.token_jti,
        "tau_low": low["status"], "hitl": "timeout_deny", "breaker": orch.breakers["billing"].state.value,
        "degraded": degraded["status"], "worm_rows": len(orch.state.worm),
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(_offline())
```

**Behavior encoded (maps to §§1–4):**

- Full-jitter **HTTP** retries; `Retry-After` honored iff \(0 < t \le 60\); `PermanentError` / `CircuitOpenError` are **not** retried.
- Supervisor **router** executes `RouteAction`; the model payload never mutates `active_agent`.
- Typed `Handoff` carries `reason` / `priority` / `order_id` / `artifact_ids` / `dropped_hash` / downscoped `jti`.
- Hop cap **3** and reciprocal ping-pong → `hop_cap_escalate_human`. `$` freeze at **80%** forbids new `Send`; fan-out **>4** (50-sub Loop D) is `fanout_cap`.
- Vote join parses a closed set; unanimous → accept; 1/3 → **escalate** (never average); 2/3 → judge band.
- HITL enqueues **write** tools only; SLA miss → **`timeout_deny`**; `kb_search` cannot park.
- `mint_downscoped`: billing token has **no** `issue_refund`; refund cannot be handed off without `order_id`; FAQ cannot call `issue_refund` (`rbac_deny`).
- Per-specialist breaker: billing 529 → **`degraded`**; FAQ still serves.
- JSON logs carry `correlation_id` + `from_agent` / `to_agent`. PII digit-runs redacted before splice. WORM rows append on every mechanism.

**Interview talking point:** retries with jitter handle 529 on one specialist; they do not let the model count the vote, they do not timeout-allow a refund, and they do not pass the supervisor’s token to Stripe.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers from the research file; scale-up arithmetic marked **[inferred]**. Decision rule: **start with one agent + skills**. Add a second agent only when (a) tool/policy isolation is a compliance requirement, (b) parallel isolated context is the product, or (c) two teams ship independently ([OpenAI](https://openai.github.io/openai-agents-python/multi_agent/); [LangChain](https://docs.langchain.com/oss/python/langchain/multi-agent); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Scenario 1 — L1/L2 support with HITL escalation

**Problem statement.** B2B SaaS support: 1k tickets/day, L1 FAQ + billing lookup, L2 refund writes, SOC2. User-facing owner must stay sticky across “where is invoice 4412?” follow-ups. Irreversible `issue_refund` needs a human on a 15 min L1 / 4 h L2 SLA with **timeout-deny**. Budget **[inferred]:** Sonnet 5 Loop S handoff **~$24 / 1k** (3 calls) vs subagents **~$32 / 1k** (4 calls) vs GPT-5.4 subagents **~$44 / 1k**. Hop cap **3** → human. p95 dominated by the specialist, not triage — ⚠️ measure your own. Eval success = tenant B cannot issue tenant A’s refund even when the model emits `transfer_to_refund`, a 16-minute silence **does not** pay out, and a reciprocal FAQ↔billing loop dies at hop 3 — **not** “we have eight personas in a GroupChat.”

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  SSO, cid, tenant FROM TOKEN                        │
                    │ MODEL emits transfer_to_* / needs_approval JSON          │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  OpenAI-style TRIAGE HANDOFF (or LC middleware)  │
                    │  handoffDescription one sentence; is_enabled hides       │
                    │    refund unless order_id in state                       │
                    │  input_filter=remove_all_tools; dropped_hash WORM         │
                    │  L1 = active_agent (sticky); L2 = EscalationData         │
                    │    logged in on_handoff (reason, priority)               │
                    │  hop cap 3 → human; ping-pong detector                   │
                    │  BREAKER faq ≠ billing ≠ refund; cheap Haiku triage      │
                    │  FALLBACK sticky specialist → supervisor join → degraded │
                    │  HITL: Temporal wait_condition 900s / 4h → timeout-deny  │
                    │  ASI09 friction: amount + recipient on structured card   │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │                               │
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  Generation │            │ TOOL PROXIES            │
                    │ Sonnet 5 cached  │            │ faq: kb_search          │
                    │ playbooks; L2    │            │ billing: lookup_invoice │
                    │ refund as_tool   │            │ refund: issue_refund    │
                    │  for policy agent│            │   + HITL; downscoped    │
                    └────────┬─────────┘            │   jti at on_handoff     │
                             │                      └──────────┬──────────────┘
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ PERSIST  thread  │            │ TELEMETRY  WORM         │
                    │ + RunState +     │            │ from/to, jti, hops, $   │
                    │ HITL row + Stripe│            │ human_gate, dropped_hash│
                    │ idempotency key  │            │ Loop S $24/1k [inferred]│
                    └──────────────────┘            └─────────────────────────┘
```

**Technology choices.** OpenAI-style **triage handoff** (or LangChain middleware handoffs) to FAQ / billing / refund — **not** a research orchestrator. `handoffDescription` one sentence each. Nested **policy agent as_tool** under refund (manager synthesizes; write still HITL). Temporal HITL cookbook: wait → manager notify → second wait → auto-reject. Interrupt **write** tools only; `when` on amount / ACL. Escalation ladder: classifier < τ → L2 specialist → write-tool HITL → security/PII → security agent with **narrower** tools, not the supervisor’s union set. Failure drills: ping-pong FAQ↔billing; `order_id` missing (`is_enabled`); HITL 16 min; new write tool forgotten on `interrupt_on` (auto-approve bug); supervisor asked to “just run” refund with FAQ token.

**Trade-off evaluation matrix.**

| Dimension | A. GroupChat of 8 personas; CrewAI hierarchical manager with union tools; majority vote on refunds | B. Recommended: sticky triage **handoff**; typed `EscalationData`; hop cap 3; Temporal HITL timeout-deny; per-agent downscope | C. Supervisor-worker subagents; `parallel_tool_calls=True`; lead holds Stripe “so it can help”; laptop `interrupt()` |
| --- | --- | --- | --- |
| **Cost / 1k** | GroupChat tokens ∝ N² plus debate **+$56 / 1k** if you vote; CrewAI manager extra call | Loop S handoff **[inferred] $24 / 1k**; repeat turn 2 **$16 extra** vs subagents **$32 extra** | Loop S subagents **[inferred] $32 / 1k** (+$8 tax); parallel tools multiply RPM; lead write tools do not add a SKU — they add **fraud** |
| **Latency** | Broadcast every utterance; no sticky owner; vote on the critical path | p95 = specialist, not triage; sticky turn 2 **~3.5 s [inferred]**; HITL is a **queue SLO** | Serial default **~9.0 s**; flipping parallel helps only independent tools; process-held HITL **is** p99 |
| **Ops complexity** | Speaker graph + “coworker not found” + CAMEL thank-you loops | Medium (transition graph, Temporal, downscope, ASI09 cards) | Looks like one graph until `InMemorySaver` drops a 4 h L2 interrupt |
| **Security posture** | Union tools on the manager; vote of a shared policy hallucination; ASI09 rubber-stamp | Per-agent allowlist; refund hidden without `order_id`; timeout-deny; canonical amount card (**9.2.2**) | Agent confused deputy: lead executes worker calls with admin creds; missing `interrupt_on` auto-approves |
| **Scalability ceiling** | N personas × 1k tickets; HITL console cannot scan paraphrases | Hop cap + queue capacity; shed low-pri; Stripe idempotency from **session** | Fan-out on a support ticket is Loop D; OpenAI `max_turns=10` dies during lunch |

**Decision rationale.** **B** is the only design that treats L1/L2 as **conversation ownership + a write gate**, matching OpenAI’s own split (handoff for sticky UX, `as_tool` for bounded policy). A fails isolation, cost, and refund correctness (do not majority-vote a payout). C pays the **+$8 / 1k** supervisor tax **and** puts Stripe on the lead — the worst of both clocks. Quote: **[inferred] ~$24 / 1k** handoff vs **$32** subagents vs **$4,000** unbounded; hop cap **3**; timeout-**deny**.

### Scenario 2 — Parallel research agents + judge

**Problem statement.** Analyst desk: breadth-first research across disjoint sources (filings, news, internal KB), citations required, task value must beat Loop R. Constraint: Opus (or GPT-5.4) lead, Sonnet/Haiku subs, Memory plan, filesystem artifacts, **hard** subagent cap, runtime `$` cap. Joining free-form summaries by majority is forbidden (shared hallucination / omitted disagreement). Budget **[inferred]:** Sonnet 15× **~$135 / 1k**; 30/70 Opus+Sonnet **~$240 / 1k**; web search 3×8 = **$0.24 / task** on the **$10 / 1K** SKU — often larger than Loop S tokens. Optional Du 3×2 **only** on contested final claims (**+$56 / 1k**, **~12 s**). Eval success = judge score < τ or missing citations → extra wave **or** human analyst, never a silent majority; 50-sub trivia spawn is impossible in code; in-flight graphs survive a prompt rainbow.

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  analyst SSO, cid, complexity score                 │
                    │ MODEL emits plan JSON + Spawn(n≤5) — runtime caps W      │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  ORCHESTRATOR-WORKER (Anthropic-shaped)          │
                    │  Opus/GPT-5.4 lead writes plan to Memory                 │
                    │  effort rules AND hard cap: 1 | 2–4 | ≤5 disjoint        │
                    │  80% remaining_usd forbids new Send                      │
                    │  isolated windows; briefs = objective, sources, OOS      │
                    │  JOIN: CitationAgent / one judge 0–1 (not multi-judge)   │
                    │    score < τ or missing cites → wave 2 OR human          │
                    │  optional 3×2 debate on contested claims only            │
                    │  BREAKER search ≠ kb ≠ judge; stall → replan             │
                    │  FALLBACK remaining subs + degraded; never union tools   │
                    │  rainbow: pin prompt ver on thread; dual-run old/new     │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │                               │
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  waves      │            │ TOOL PROXIES            │
                    │ 3–5 subs × 3+    │            │ web_search (SKU capped) │
                    │ tools parallel;  │            │ fs_write artifact       │
                    │ lead CANNOT steer│            │ judge: read_artifact    │
                    │ in-flight (sync) │            │   NEVER mutate Stripe   │
                    └────────┬─────────┘            └──────────┬──────────────┘
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ PERSIST  Memory  │            │ TELEMETRY  WORM         │
                    │ plan + artifact  │            │ W, $, overlap of query  │
                    │ store (refs, not │            │ embeddings, judge score,│
                    │ blobs through    │            │ citation∉artifact rate  │
                    │ the lead)        │            │ Loop R $135–240/1k      │
                    └──────────────────┘            └─────────────────────────┘
```

**Technology choices.** Anthropic-shaped **orchestrator-worker**: isolated sub windows, condensed summaries, filesystem refs (telephone-game + PII). Join is a **CitationAgent / single judge** with rubric (factuality, citation, completeness, source quality, tool efficiency) — Anthropic: **one** judge beat multi-judge. Estornell diversity pruning if proposers are the same family; position-swap if a second-family judge is added. Magentic stall counter if you need a ledger instead of a research DAG. Deploy: rainbow + tracing of **structures** not contents. Cap searches in **code**. Failure drills: 50-sub spawn (must `fanout_cap`); vague brief overlap; SEO-farm sources (source-quality rubric); lead as only judge without swap; handoff swarm attempted for parallel domains (LangChain **14K+** sequential).

**Trade-off evaluation matrix.**

| Dimension | A. Handoff swarm / sticky specialists across domains; Skills-only 15K context sludge | B. Recommended: orchestrator-worker; isolated windows; filesystem refs; hard W cap; one CitationAgent/judge; `$` freeze | C. Unbounded `Send` of 50 subs; majority of summaries; lead model as only judge; no search SKU cap |
| --- | --- | --- | --- |
| **Cost / 1k** | Sequential handoffs **~14K+** tok (LangChain multi-domain); Skills **~15K** in one window | Loop R **[inferred] $135 / 1k** Sonnet 15× or **~$240 / 1k** 30% Opus; search **$0.24 / task** if uncapped at 3×8 | Loop D **[inferred] $4,000 / 1k** plus lead; k=40 self-consistency **+$273 / 1k**; search SKU dominates tokens |
| **Latency** | Σ domain p99; cannot parallelize (LangChain table) | Wave wall-clock ≈ max(sub) + join; Anthropic ≤**90%** cut vs sequential; lead cannot mid-correct (sync) | Join waits on the slowest of 50; M1-Parallel 2.2× **multiplies cost** unless cancelled |
| **Ops complexity** | One transcript to debug; telephone game through whoever is `active_agent` | Medium (Memory, artifact store, overlap metric, rainbow pin) | Looks like “just add Send”; IR cannot tell which sub invented the claim |
| **Security posture** | Shared transcript copies PII every hop; no partitioned ACL | Isolated windows + refs; judge cannot mutate source systems; downscope per sub MCP | Majority of a shared hallucination ships as fact; SEO-farm + injection in one sub contaminates the vote |
| **Scalability ceiling** | History growth; Skills sludge hits the window before the corpus | Admission-control W; OTPM binds before CPU — size **in-flight** completions | No p99; AISVS **9.1.2** violated by construction |

**Decision rationale.** **B** is the only design that treats research as **bounded parallel retrieve + a verifier**, matching Anthropic’s published system (plan Memory, isolated subs, one judge, filesystem refs) and the join policy in §2.3 (never average below τ). A is the sticky-support clock applied to the wrong product — sequential, bloated, un-citable. C is the early Anthropic failure mode (50 subs, vague briefs) plus Estornell’s tyranny of the majority. Quote: **[inferred] $135–240 / 1k** before search SKU; **one** judge; hard W; debate **only** on contested claims.

---

## Key numbers to memorize

| Number | What |
| --- | --- |
| **$24 / $32 / $40 / $44 / 1k** | Loop S handoff / subagents / router-parallel / GPT-5.4 subagents **[inferred]** |
| **+$8 / 1k / turn** | Supervisor-join tax vs sticky specialist **[inferred]** |
| **$16 vs $32 extra** | Repeat turn 2 handoff vs subagents **[inferred]** |
| **$36 / $135 / $240 / 1k** | Loop R 4× chat / 15× Sonnet / 30% Opus mix **[inferred]** |
| **$4,000 / 1k** | Loop D 50 subs × 10 calls **[inferred]** |
| **+$14 / +$56 / +$28 / +$273 / 1k** | Majority N=3 / Du 3×2 / SC k=5 / SC k=40 extra **[inferred]** |
| **$0.008 / call** | Sonnet 5 × 2,000 in + 400 out (Loop S reference) |
| **$0.24 / task** | 3 subs × 8 searches × **$10 / 1K** web-search SKU |
| **~4× / ~15×** | Anthropic chat→agent / chat→multi-agent token multipliers |
| **+90.2% / 80% / ≤90% / −40%** | Internal eval vs Opus 4 / BrowseComp variance from tokens / wall-clock parallel / MCP-description time cut |
| **~9.0 s / ~5.5 s / ~3.5 s / ~12 s** | Loop S serial / parallel Send / swarm turn 2 / Du 3×2 extra **[inferred envelope]** |
| **3 in-flight** | Supervisor + 2 parallel specialists per ticket during a wave |
| **hop cap 3 / max_turns 10 / max_stalls 3 / max_turns 20** | Support fuse / OpenAI default / Magentic stall / Magentic turn |
| **τ task-specific** | 3/5 **35.5%** vs 5/5 **78.7%** majority correctness (that paper); 74.5% majority already right (Minority Sentinel) |
| **81.8% vs 72.1% vs 69.0%** | Du 3×2 arithmetic vs reflection vs majority |
| **38% / 32.8% / 27.7% / −31%** | Magentic GAIA / WebArena / AssistantBench / ledger ablation (GPT-4o, 2024) |
| **AISVS 9.6.2 / 9.2.10 / 9.5.3** | Timeout-deny / worst class in chain / policy engine not the model |

**Interview closer:** “The model never routes. I pick the clock (sticky handoff **[inferred] $24 / 1k** vs supervisor join **$32 / 1k** vs research 15× **$135–240 / 1k**), I mint a downscoped token in `on_handoff`, I join at τ or escalate — I never average a shared hallucination — and HITL is a Temporal park with timeout-deny, not a prompt. Fan-out and `$` live in the runtime, or you ship Loop D at **$4,000 / 1k**.”
