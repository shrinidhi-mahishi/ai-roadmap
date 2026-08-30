# Module 09 — Multi-Agent Systems

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/09-multi-agent-systems.md` (researched 2026-08-21, 52 sources).
**Mandatory topics**: Supervisor · Worker · Collaboration · Delegation.

The unit of production is not “more LLMs.” It is a **control plane** that owns loop budget, next-agent, hop/$ caps, kill-switch, HITL, and circuit state, wrapping a **data plane** that runs isolated worker contexts, MCP `tools/call`, and A2A tasks. LangChain (2026 OSS): “multi-agent” is usually a request for **context management**, **distributed development**, or **parallelization**. If context were infinite and latency zero, a single agent with all tools would dominate. Skills (progressive disclosure) are often the cheaper substitute for a second agent. Interview answers that skip this split fail when the follow-up is “who owns the next user-visible token, and whose principal is on the worker’s write?”

**Invariant:** the model never routes, never hands off, never grants authority. It emits a structured action (`transfer_to_*`, A2A `SendMessage`, graph `Command`/`Send`). A **runtime** interprets that action, mutates durable state, and decides the next node. Collapsing “who may act” into the LLM prompt is the dominant enterprise failure.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, policy, topology pick (router / supervisor / swarm / A2A), `max_turns` / hop fuse / AISVS 9.1.2 $ cap, Magentic Task+Progress ledgers, and circuit open/close. Data plane owns worker LLM windows, MCP HTTP, A2A artifacts, sandboxes, blackboard blobs. Persistence is **resume identity**, not the model: `thread_id`/`checkpoint_id`, OpenAI `RunState`/`session_id`, A2A `contextId`+`taskId`, Temporal workflow id. Tool proxies execute side effects under **per-worker** IAM; the lead must not hold the union of worker tools. Telemetry is the only place hop count, subagent count, `$ / task`, breaker state, and delegation rows are authoritative.

Microsoft Learn (2026-07-06): **prefer platform-native orchestration for internal subagents**; **MCP for tools/data**; **A2A for opaque, cross-platform, cross-org agents**. MCP is the tool bus; A2A is the agent bus.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE chat / A2A SendMessage / Temporal Signal / HITL resume)           │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant principal (never worker IAM)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Loop budget  │─▶│ Supervisor /          │  │
│  │ auth,quota │  │ PII detect→  │  │ max_turns=10 │  │ Orchestrator          │  │
│  │ RPM/TPM    │  │ redact→audit │  │ hop fuse     │  │ create_supervisor /   │  │
│  │ breaker    │  │ tool RBAC    │  │ $ / task cap │  │ Magentic ledgers      │  │
│  │ Retry-After│  │ downscope    │  │ kill-switch  │  │ effort 1 | 2–4 | >10  │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
│                         │                 ▼                     │               │
│                         │          ┌────────────────┐           │               │
│                         │          │ Topology pick  │◀──────────┘               │
│                         │          │ router | super │  FINISH / Send / handoff  │
│                         │          │ swarm | hier.  │  parallel_tool_calls      │
│                         │          └───────┬────────┘                           │
│  ┌─────────────────────────────────────────┴──────────────────────────────────┐ │
│  │ A2A CONTROL (agent bus)  Agent Card │ Task SM │ contextId+taskId │ AUTH    │ │
│  └────────────────────────────────────────────────────────────────────────────┘ │
└────────┬───────────────────────────────┬────────────────────────┬───────────────┘
         │ spawn / join (sync wave)      │ SendMessage            │ tools/call
         ▼                               ▼                        ▼
┌────────────────────────────────┐ ┌─────────────────────┐ ┌──────────────────────┐
│ DATA PLANE — WORKERS           │ │ A2A PEER (opaque)   │ │ TOOL PROXIES (MCP)   │
│ Isolated context windows       │ │ Callee has own CoT  │ │ OAuth 2.1 resource   │
│  ┌──────────┐  ┌────────────┐  │ │ skills on Card      │ │ RFC 8707 audience    │
│  │ Billing  │  │ Research   │  │ │ Artifact / Part     │ │ NO token passthrough │
│  │ tools=…  │  │ tools=…    │  │ │ mTLS / OAuth2/OIDC  │ │ per-agent allowlist  │
│  │ principal│  │ principal  │  │ │ extended Card gated │ │ STS / signed ticket  │
│  └────┬─────┘  └─────┬──────┘  │ └──────────┬──────────┘ └──────────┬───────────┘
│       │ never talk   │         │            │                       │
│       └──────┬───────┘         │            │  refinement = new     │
│              ▼                 │            │  taskId, same context │
│  ┌───────────────────────────┐ │            │                       │
│  │ Blackboard / Store blobs  │ │            │                       │
│  │ refs, not telephone game  │ │            │                       │
│  └───────────────────────────┘ │            │                       │
└──────────────┬─────────────────┘            │                       │
               │                              │                       │
               ▼                              ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌─────────────────────┐ ┌───────────────┐ ┌───────────────┐ ┌────────────────┐ │
│  │ Graph checkpoint    │ │ A2A identity  │ │ Temporal hist.│ │ Blob / WORM    │ │
│  │ thread_id PK        │ │ contextId +   │ │ Workflow=loop │ │ artifacts; MCP │ │
│  │ checkpoint_id etag  │ │ taskId        │ │ Activity=LLM  │ │ payloads; hash │ │
│  │ active_agent        │ │ immutable     │ │  / MCP / A2A  │ │ in checkpoint  │ │
│  │ OpenAI RunState     │ │ terminal task │ │ Continue-As-  │ │                │ │
│  │ Magentic ledgers    │ │               │ │ New @ bound   │ │                │ │
│  └─────────────────────┘ └───────────────┘ └───────────────┘ └────────────────┘ │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐ │
│  │ Audit (WORM)│  │ Metrics      │  │ Traces      │  │ Usage (terminal event) │ │
│  │ from→to,    │  │ hops, subs,  │  │ gateway →   │  │ tokens, cache hit,     │ │
│  │ mechanism,  │  │ $ / task,    │  │ supervisor  │  │ web_search count,      │ │
│  │ principal,  │  │ breaker,     │  │ → worker →  │  │ filtered-history hash  │ │
│  │ token_jti,  │  │ stall, fuse  │  │ MCP / A2A   │  │                        │ │
│  │ human_gate  │  │              │  │             │  │                        │ │
│  └─────────────┘  └──────────────┘  └─────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

| Layer | Owns | Typical objects | Failure if fused into the LLM |
| --- | --- | --- | --- |
| **Control** | Loop budget, next-agent, max hops, kill-switch, HITL, circuit | LangGraph compiler + checkpointer; OpenAI `Runner` (`max_turns` default **10**); Temporal Workflow; A2A `TaskState`; CrewAI `Process`; MAF Orchestration | Infinite ping-pong; 50-subagent fan-out; spend unbounded |
| **Data** | Tool HTTP, MCP `tools/call`, A2A artifacts, sandboxes, blackboard | Worker tools, MCP servers, A2A `Artifact`/`Part`, LangGraph `Store` | PII in every hop; confused-deputy token passthrough |
| **Persistence** | Resume identity | `thread_id`/`checkpoint_id`; `RunState`; A2A `contextId`+`taskId`; Temporal workflow id | Restart from scratch after a 500; rainbow-deploy kills in-flight research |
| **Policy** | Who may call which tool under which principal | Per-agent tool lists, RFC 8707, A2A `securitySchemes`, OPA/Cedar | Worker inherits supervisor’s OAuth cookie |

### 1.2 Five topologies (what they serialize)

| Topology | Who picks the next hop | User-facing owner | Parallelism | Typical product |
| --- | --- | --- | --- | --- |
| **Router** | One classification step | Synthesizer or specialist | `Send` fan-out | LangChain Router + `Send` |
| **Supervisor / orchestrator-worker** | Central LLM (or ledger) every round | Supervisor synthesizes | Optional (`parallel_tool_calls`) | LangGraph `create_supervisor`; Anthropic Research; Magentic-One |
| **Hierarchical supervisors** | Supervisor of compiled supervisors | Top-level only | Per-team | `create_supervisor([research_team, writing_team])` |
| **Swarm / mesh / handoff** | Currently active agent | Whoever is `active_agent` | Sequential by default | LangGraph swarm; OpenAI `handoffs`; MAF handoff |
| **Custom / blackboard / Network** | State schema, Hub, or blackboard controller | Defined by workflow | Mixed | LangGraph custom; AG2 Hub+channels; LbMAS |

Workers in a **supervisor** never talk to each other; all routing returns to the lead. A **swarm** persists `active_agent` so turn 2 **skips the router**. **A2A** is the inter-process collaboration plane: callee CoT is opaque; the unit of work is Task + Message + Artifact.

### 1.3 End-to-end request flow

1. **Ingress.** Client opens SSE, sync HTTP, A2A `SendMessage`, or a Temporal Signal. Gateway stamps `correlation_id`, authenticates the **user principal**, checks RPM/TPM. Provider/worker circuit state is already a routing input. Closed breaker on search MCP is an input, not a surprise.
2. **Policy.** Detect → redact PII **before** any worker sees text. Attach **only** the tools this topology and this principal may use. Hierarchical IAM: team supervisor has **delegation** rights, not the union of worker write tools. Do not put a long-lived shared API key on the lead.
3. **Route (control plane, not the model).** (i) Known domains, parallel retrieval, no sticky owner → **router** + `Send`. (ii) Breadth-first unknown DAG → **orchestrator-worker** (Anthropic lead + Memory + effort rules). (iii) Sticky support with policy split → **handoff** (`active_agent`). (iv) Cross-org opaque callee → **A2A**. (v) `<10` tools, one domain, or sequential coding → **single agent + skills**. Putting Opus on a coffee-order router is Loop-A waste.
4. **Compile action.** Runtime binds the model’s tool call to a legal transition: `Command(goto=…)` / `list[Send]` / `transfer_to_*` / A2A skill on the Agent Card. Illegal dest or disabled `is_enabled` predicate → PermanentError, not a retry. Register **one handoff per destination**.
5. **Downscope then dispatch (data plane).** `on_handoff` (or equivalent) mints a short-lived token whose **audience** is the worker’s MCP servers and whose **scopes** match the brief. Worker runs in an **isolated** window (Anthropic subagents; Magentic tool-shaped workers). MCP: OAuth 2.1 resource server + RFC 8707; **no** client-token passthrough.
6. **Collaborate.** In-process: `messages` reducer or Magentic inner loop (one worker per step). Parallel wave: LangGraph `Send` + `operator.add`; Anthropic 3–5 subs × 3+ tools, **synchronous** join (lead cannot mid-course-correct a wave). Cross-process: A2A Task SM — blocking `SendMessage` waits until terminal or interrupt; non-blocking polls / streams / push. Blackboard: specialists **opt in** after a post; serialize if one writer per cycle.
7. **Join / synthesize.** Supervisor `output_mode='last_message'` (default; not `full_history`). Anthropic: write sub output to **filesystem**, pass **references** — not the telephone game through the lead. Optional CitationAgent / synthesizer **off** the interactive critical path when possible.
8. **Interrupt.** HITL on irreversible tools (OWASP AISVS C9: timeout → **block**, not proceed). OpenAI `needs_approval`; LangGraph `interrupt()`; A2A `INPUT_REQUIRED` / `AUTH_REQUIRED`; Temporal Signal parks at **zero compute**. ASI09: HITL is an attack surface — friction-by-design, approval budget, structured risk badges.
9. **Persist.** Checkpoint at LangGraph super-step / Temporal Activity completion / A2A task transition. Optimistic concurrency on `checkpoint_id` / task etag. Terminal A2A tasks **never restart**; refinements = new `taskId` in the same `contextId`. Rainbow deploys: pin prompt versions on the thread so in-flight graphs survive cutover.
10. **Degrade and emit.** Worker 500: fail that identity, replan remaining (do not narrate a dead search API with Opus tokens). Hop fuse / `max_turns=10` / `max_stalls=3` → human or Magentic replan, **not** another `transfer_to_*`. Audit the delegation row (including hash of **filtered** history). Usage on the terminal event is the bill.

**Interview talking point:** “The model is an untrusted planner. Routing, IAM, and hop caps live in the runtime. MCP is tools; A2A is agents; Temporal is the control-plane clock.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Supervisor — router vs orchestrator vs hierarchical

These three words are three **control-plane clocks**, not one topology.

| Role | Clock | Decision | State | When it wins |
| --- | --- | --- | --- | --- |
| **Router** | Once per user turn | Classify → 1..K specialists | Optional | Known domains, parallel retrieval, no multi-hop ownership |
| **Orchestrator (lead)** | Every round until “enough” | Decompose, spawn, synthesize, re-spawn | Plan in Memory / Task Ledger / Progress Ledger | Breadth-first research, unknown search DAG |
| **Supervisor (LangGraph)** | Every worker return | Which worker tool next, or FINISH | Shared `messages` (+ optional private scratch) | Tool isolation + centralized reply |
| **Hierarchical supervisor** | Per level | Which *team* next | Nested graphs, nested checkpoints | Org/IAM boundaries, **not** token savings |

**Router.** `Command(goto=agent)` for one specialist; `list[Send(agent, {query})]` for parallel. Tutorial: GitHub + Notion + Slack then synthesizer. Router LLM call is **pure overhead** on repeat turns (**3** calls every time vs handoffs’ **2**).

**LangGraph `create_supervisor`.** Compiles a `StateGraph` whose supervisor LLM is bound to **handoff tools**. Production defaults: `output_mode='last_message'`; `parallel_tool_calls=False`; `add_handoff_messages=True`. Flip `parallel_tool_calls=True` and the supervisor becomes a **fan-out orchestrator for one tick**. LangChain 1.0 **recommends implementing the supervisor as ordinary tools** rather than the `langgraph-supervisor` package — more control over context engineering.

**Supervisor state machine (Pregel super-step):**

```
  ┌─────────┐  structured action   ┌────────────┐  worker return   ┌─────────────┐
  │ ROUTE   │─────────────────────▶│ DISPATCH   │─────────────────▶│ JOIN        │
  │ (lead)  │  FINISH              │ isolated   │  500 / timeout   │ last_message│
  └────┬────┘                      │ worker(s)  │                  └──────┬──────┘
       │                           └────────────┘                         │
       │ FINISH                                                           │ next tool
       ▼                                                                  ▼
  ┌─────────┐                                                        ROUTE (loop)
  │ TERMINAL│  fuse: max_turns | $ cap | hop cap | kill-switch
  └─────────┘
```

**Invariant S1:** workers do not address each other. **Invariant S2:** the lead’s tool list is **delegation tools**, not Stripe/email. **Invariant S3:** `Send` length and subagent count are **runtime** caps (Anthropic: simple → **1** agent, **3–10** tool calls; comparison → **2–4** subs, **10–15** calls each; complex → **>10** with disjoint responsibilities). Early failure: lead spawned **50** subagents for trivia.

**Orchestrator (Anthropic Research, 2025-06-13).** Lead (Opus-class) writes a plan to **Memory** (200k will truncate). Spawns Sonnet-class subs with objective, output format, tool list, stop boundary. Subs search in **isolated** windows, **3+ tools in parallel**, return condensed summaries. Separate **CitationAgent**. Official: Opus-lead + Sonnet-subs **+90.2%** vs single Opus 4 on an **internal** research eval; token usage explains **80%** of BrowseComp variance (tool-call count + model choice complete a three-factor model covering **95%**); agents **~4×** chat tokens; multi-agent **~15×**; parallel 3–5 × 3+ tools cut wall-clock **up to 90%**. Coding is a **poor** fit (few parallelizable subtasks; weak live coordination). Openlayer 2026 commentary: supervisor-style parallelism helped some Google-reported parallel tasks (~**80%**) and **hurt** sequential reasoning (~**70%**).

**Magentic-One (Fourney et al.; MAF 1.0).** Outer **Task Ledger** (facts, guesses, plan). Inner **Progress Ledger** (done? who next?). Stall: `max_stalls=3` then replan. Default `max_turns=20`. Workers are **tool-shaped**: WebSurfer, FileSurfer, Coder, ComputerTerminal. Ablations (GAIA validation): removing full ledgers **−31%**; removing any one worker **−21%** (Coder) to **−39%** (FileSurfer). Published (GPT-4o era): **38%** GAIA, **32.8%** WebArena, **27.7%** AssistantBench. o1 **refused 26%** of WebArena Gitlab and **12%** of Shopping Admin — a smarter orchestrator model can **shrink** coverage.

**Hierarchical.** A compiled supervisor is a Pregel object and can sit in another `agents=` list (`research_team` + `writing_team` under `top_level_supervisor`). Each level adds ≥1 model call and a context splice. Use when teams have **separate checkpointers, tool IAM, and release cadences**.

**Complexity.** Router: \(\Theta(1)\) classify + \(K\) specialists. Supervisor tick: \(1 + W\) model calls if serial; \(1 + 1\) wall-clock waves if `parallel_tool_calls` and join. Hierarchical: add one call **per level**. Orchestrator: waves × (lead + \(S\) subs + optional citation); **unbounded** unless effort rules + hard cap live in the **runtime**.

### 2.2 Worker — specialists, tool-scoped isolation, skills

**Specialist agents:** prompt + tool set + **policy** change together. OpenAI: split only when those three actually change — extra agents multiply prompts, traces, and approval surfaces.

**Tool-scoped workers** (Magentic-One, Anthropic subs): defined by **what they can touch** (browser, files, code interpreter, web search), not a business domain. Maps to IAM: FileSurfer must not hold Stripe scopes. Ablations: you cannot cheaply drop a worker and hope another compensates (scores still dropped 21–39% even when WebSurfer found an online PDF viewer).

**Skill isolation is the non-agent alternative.** LangChain Skills / [Agent Skills](https://agentskills.io/): `load_skill` injects a playbook; the **same** agent stays in control. Extensions: dynamic tool registration on load; hierarchical skills; reference awareness (read files when needed). Token profile: few extra calls, **high** context once many skills load (**~15K** vs subagents’ **~9K** on the three-language comparison). Isolation is **prompt-deep, not process-deep**: no sandbox, identity, or rate limit unless you add them.

**Deep Agents** (LangChain harness): subagents + skills + planning + virtual filesystem + context management — Anthropic-style lead + filesystem artifacts without inventing Memory + CitationAgent.

**CrewAI.** `Process.sequential` (default): task list order. `Process.hierarchical`: **requires** `manager_llm` or `manager_agent`; tasks **not** pre-assigned; manager plans, delegates, validates. `allow_delegation=True` is necessary but **not sufficient** (GitHub #4783: delegation tool populated with the manager’s own role — “coworker not found”).

**LangChain documented call counts** (pedagogical; not your prod mix):

| Workload | Subagents | Handoffs | Skills | Router |
| --- | --- | --- | --- | --- |
| One-shot “buy coffee” | **4** | **3** | **3** | **3** |
| Repeat same request | **4+4=8** | **3+2=5** | **3+2=5** | **3+3=6** |
| Multi-domain (3× ~2k-token specialists, parallel OK) | **5** calls, **~9K** tokens | **7+** calls, **~14K+** (sequential) | **3** calls, **~15K** | **5** calls, **~9K** |

Subagents win isolation + parallel. Handoffs win sticky conversations. Skills win “one agent, many playbooks.” Router wins explicit classification + parallel without a sticky specialist.

**Invariant W1:** a worker’s tool allowlist is the **intersection** of (brief, agent spec, principal scopes) — not the lead’s catalog. **Invariant W2:** poisoned context is isolated iff the sub has its own window (Anthropic win); a swarm **contaminates** sticky history. **Invariant W3:** filesystem **refs** beat copying artifacts through the coordinator.

### 2.3 Collaboration — blackboard, A2A, message passing, debate, seq vs parallel

**Message passing (in-process).** LangGraph `messages` + reducer; OpenAI session items; AG2 Classic broadcast; MAF group chat. Cheap inside one deployable. No discovery, no cross-language contract, no task lifecycle.

**AG2.** Classic `GroupChat` broadcasts every utterance to all members; speaker selection `auto` / `round_robin` / `random` / `manual` / allowed-transition graph. Cost: **N−1 extra context injections per turn** (tokens \(\propto N^2\)). AG2 Network (2026, `import ag2`): a **Hub** owns registry, WAL, audit; typed channels `conversation`, `consulting` (one-question-one-reply, auto-close), `discussion` (round-robin), `workflow` (`TransitionGraph`).

**A2A 1.0.0** (Google → Linux Foundation; TSC includes AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow). Normative model is `spec/a2a.proto`. Complementary to MCP:

| | MCP | A2A |
| --- | --- | --- |
| Problem | Agent → tool/data | Agent → agent (opaque) |
| Discovery | Tool list | **Agent Card** (skills, caps, security) |
| Unit of work | `tools/call` | **Task** + **Message** + **Artifact** |
| Orchestration | Host chooses tools | Callee has its own CoT; tools opaque |
| Multi-turn | Context stays on host | `contextId` groups tasks; `INPUT_REQUIRED` ≈ MCP elicit |
| Auth | OAuth 2.1 + RFC 8707 | OpenAPI `securitySchemes` (API key, HTTP, OAuth2, OIDC, **mTLS**); skill-level `securityRequirements`; signed cards (JWS) |

**A2A task state machine** (proto enums; JSON may show `TASK_STATE_*`):

```
SUBMITTED ──▶ WORKING ──▶ COMPLETED
                 │           FAILED
                 │           CANCELED
                 │           REJECTED
                 ├── INPUT_REQUIRED  ──▶ WORKING  (client SendMessage, same taskId)
                 └── AUTH_REQUIRED   ──▶ WORKING  (user gesture; not an error)
```

Blocking `SendMessage` (`returnImmediately=false`, default) waits until terminal or interrupted. Non-blocking: poll, subscribe (`streaming` capability), or push (must be declared or `PushNotificationNotSupportedError`). **Task immutability:** terminal tasks never restart; refinements create a **new** `taskId` in the same `contextId`, optionally `referenceTaskIds`. Parallel follow-ups are first-class (flight + hotel + activity as siblings). Artifact mutation tracking is **client-side** (same `artifact-name`, new `artifactId`). Extended Agent Card is auth-gated (`GetExtendedAgentCard`) when `capabilities.extendedAgentCard=true`. List-tasks **must** be authorization-scoped (spec §13.1).

**Blackboard.** Knowledge sources write partial solutions; a controller picks who runs next. LbMAS (arXiv:2507.01701): public board + private debate spaces; LLM controller selects from the board; authors claim token-economical vs workflow-search MAS. Data-science blackboard (arXiv:2510.01285): central agent **posts a request**; subs **opt in**. Reported: runtime **132.0–145.2 s** across RAG / master–slave / blackboard (**no** latency win); blackboard **~2.3×** RAG $ and **~1.8×** master–slave $; quality **+54.1%** vs RAG, **+18.8%** vs master–slave. ⚠️ Single paper, one domain. Blackboards **serialize** if one knowledge source is active per cycle. They shine as a **revision history**; they fail when you needed a DAG of independent searches.

**Debate.** Multiagent Debate / Mixture-of-Agents: proposers → critique rounds → judge. Collaboration as **verification**, not work-splitting. Cost \(\approx\) rounds × agents × context. Use as a verifier on high-value non-parallelizable answers; combine with CitationAgent (debate on claims, then cite).

**Sequential vs parallel:**

| Pattern | Mechanism | Latency | Token | Correctness risk |
| --- | --- | --- | --- | --- |
| Sequential pipeline | CrewAI sequential; linear edges; MAF sequential | p99 ≈ Σ stages | Low duplication | Error compounds |
| Sequential handoff | Swarm `active_agent` | Sticky; skip router turn 2 | Grows unless filtered | Ping-pong |
| Parallel workers, sync join | Anthropic wave; `Send` + reducer | p99 ≈ max(workers) + join | High (isolated contexts) | Duplicate search if brief is vague |
| Parallel + async | Anthropic’s **stated next step**; A2A parallel tasks | Lower blocking | Coordination bugs | Lead cannot mid-course-correct (Research is **synchronous** for that reason) |
| Speculative parallel teams | M1-Parallel (arXiv:2507.08944) | **up to 2.2×** with early stop | Multiplies team $ | Need cancellation |

**Invariant C1:** in-process `messages` is not a cross-org contract. **Invariant C2:** A2A terminal ≠ restart. **Invariant C3:** do not multi-agent a tightly coupled chain (Anthropic + Openlayer).

### 2.4 Delegation — handoffs, assignment, authority, escalation, human

**Two official OpenAI SDK patterns** (Python + JS, 2025–2026):

| Pattern | Primitive | Who owns the next user-visible token | Guardrails | Use |
| --- | --- | --- | --- | --- |
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool `transfer_to_<agent>` | Specialist | Input guardrails = **first** agent only; output = **last** agent only | Conversation ownership changes |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Nested run; `needs_approval` supported on `as_tool` | Bounded subtask; manager synthesizes |

`handoff()` knobs: `tool_name_override`, `tool_description_override`, `on_handoff` (log, prefetch, **mint downscoped token**), `input_type` (Pydantic metadata: `reason`, `priority` — **does not** choose destination and **does not** replace the next agent’s input), `input_filter` / `RunConfig.handoff_input_filter`, `is_enabled`, `nest_handoff_history`. Helper `handoff_filters.remove_all_tools` strips tool I/O. Combine: triage **hands off** to refund; refund **calls** a policy agent as a tool. Guardrail gap: tool guardrails **do not wrap** handoffs / hosted `as_tool` — add policy at the **worker**.

LangChain: tools that `Command(update={current_step|active_agent})`. (1) **single agent + middleware** (`@wrap_model_call` swaps prompt/tools — recommended default). (2) **subgraph agents** + `Command.PARENT` — you **must** pass the triggering `AIMessage` **and** a `ToolMessage` with matching `tool_call_id` or the next model sees a malformed transcript. LangGraph swarm: `create_handoff_tool` returns `Command(goto=agent_name, graph=Command.PARENT, update={messages, active_agent})`. Failure mode: reciprocal `transfer_to_*` and no hop cap.

**Handoff / swarm state machine:**

```
active_agent = triage
        │ user turn (skip router if sticky)
        ▼
   ┌────────────┐  transfer_to_X, hop < N   ┌────────────┐
   │ SPECIALIST │──────────────────────────▶│ SPECIALIST │
   │ X          │  hop ≥ N → human          │ Y          │
   └────────────┘  is_enabled == false      └────────────┘
        │ FINISH / no transfer                     │
        ▼                                          ▼
   user-visible tokens from the *current* owner (not the supervisor)
```

**Task assignment ≠ handoff.** Orchestrator writes a **brief** (objective, format, tools, boundary). Magentic-One assigns **one** worker per inner-loop step. CrewAI manager allocates unassigned tasks. A2A assignment is `SendMessage` creating a `Task` on a remote skill. Without a brief: duplicated search (Anthropic semiconductor: three subs on 2025 supply chain, one wandered into 2021 auto chips) or the manager doing the work itself (CrewAI bugs).

**Authority — three layers that must not collapse:**

1. **Routing authority** — who may be next (`handoffs` list, A2A skill, supervisor tool list).
2. **Tool authority** — which MCP/tools that worker may call (per-agent allowlist; skill-level A2A `securityRequirements`).
3. **Principal authority** — on whose behalf (user OAuth vs agent service account). MCP **MUST NOT** passthrough the client’s token; **token exchange** for a correctly audenced token.

**Escalation.** OpenAI `input_type=EscalationData(reason=...)` + `on_handoff` for an audit row **before** the escalation agent speaks. A2A: `AUTH_REQUIRED` / `INPUT_REQUIRED`. OWASP AISVS C9: privileged/irreversible actions block until human approval; approval timeout → **block**; swarm-level kill-switch; per-execution budgets. Reversibility lives in the **tool manifest**, not the agent’s self-description (AISVS 9.2.6/9.2.7: worst-case governs across a multi-step chain).

**Human handoff ≠ agent handoff:**

| Mechanism | Pause | Resume | Durable wait? |
| --- | --- | --- | --- |
| LangGraph `interrupt()` / `interrupt_before` | GraphInterrupt | `Command(resume=…)` + checkpointer | Only if Agent Server / Temporal, not laptop `invoke()` |
| OpenAI `needs_approval` | `result.interruptions` | `state.approve()/reject()` + same session | Process-held unless you persist `RunState` |
| A2A `INPUT_REQUIRED` | Task interrupted | Client `SendMessage` on same `taskId` | Yes, by spec |
| Temporal Signals/Updates | Workflow parks (zero compute) | Signal | Yes |
| CrewAI / AG2 `manual` speaker | Console | Human types | No |

**Complexity.** Handoff hop fuse: \(\Theta(H_{\max})\) transfers then human. Agent-as-tool: nested run depth; treat “agent as tool that can spawn agents” as **recursive agency** — depth limit **1** unless productized. GroupChat: \(\Theta(N)\) injections/turn.

**Invariant D1:** input guardrails on the first agent do not protect the specialist after handoff. **Invariant D2:** `input_type` metadata is not routing. **Invariant D3:** downscope at the instant of transfer (`on_handoff`), not in the prompt.

---

## 3. Token Economics & NFR Analysis

Prices and eval numbers are from vendor docs, protocol specs, or named papers as of **2026-08-21**. ⚠️ No unpublished production p50/p95/p99 multi-agent loop SLOs are invented; missing percentiles are marked. `$ per 1k tasks` figures are **[inferred]** from published token SKUs × a stated reference loop — not a vendor “per task” product. Model tokens only unless a SKU is named.

**SKU anchors (2026-08-21).** Claude: Sonnet 5 **$2 / $10** per MTok (cache hit **$0.20**); Opus 5 **$5 / $25** (hit **$0.50**); Haiku 4.5 **$1 / $5** (hit **$0.10**); Fable 5 **$10 / $50** (hit **$1**). OpenAI GPT-5.6: Sol **$5 / $30**, Terra **$2 / $12**, Luna **$0.20 / $1.20** (cached input 10% of input). US-only Claude inference **1.1×**; Opus 5 fast mode **2×**. Anthropic Managed Agents: **$0.08 / session-hour** plus tokens; web search **$10 / 1K searches**; code-exec extra **$0.05 / container-hour** after 50 free org-hours/day.

### 3.1 Cost per 1k runs

**Loop A — LangChain one-shot “buy coffee.”** Assume **2,000 input + 400 output tokens per model call**. Sonnet 5: \(2000\times\$2 + 400\times\$10\) per 1M = **$0.008 / call**.

| Pattern | Calls | $/task | **$/1k tasks [inferred]** |
| --- | --- | --- | --- |
| Handoffs / Skills / Router | 3 | $0.024 | **$24** |
| Subagents (extra join through main) | 4 | $0.032 | **$32** |
| Same, GPT-5.6 Terra ($2/$12) | 3 | $0.0088 | **$9** |
| Same, GPT-5.6 Sol ($5/$30) | 3 | $0.022 | **$22** |

Repeat-request (turn 2): handoffs **2** calls → **$16 / 1k extra**; subagents still **4** → **$32 / 1k extra**. Coordination tax of “always return to supervisor” is **+$8 / 1k / turn [inferred]**.

**Loop B — multi-domain 9K vs 14K vs 15K.** Split **70% input / 30% output [inferred]**.

| Pattern | Tokens | Sonnet 5 $/task | **$/1k [inferred]** |
| --- | --- | --- | --- |
| Subagents / Router (~9K) | 6.3K in + 2.7K out | $0.0396 | **$40** |
| Handoffs (~14K, sequential) | 9.8K + 4.2K | $0.0616 | **$62** |
| Skills (~15K accumulated) | 10.5K + 4.5K | $0.066 | **$66** |

Handoffs’ inability to research three domains in parallel is a **~$22 / 1k** tax vs subagents on this pedagogical workload **[inferred]** — before extra latency.

**Loop C — Anthropic 15× research.** Chat baseline **2,000 in + 500 out** on Sonnet 5 = **$0.009 / chat**. Single-agent research **4×** = **$0.036** → **$36 / 1k**. Multi-agent **15×** = **$0.135** → **$135 / 1k**. Mix **30% Opus 5 + 70% Sonnet 5** on the 15× pile **[inferred]**: **~$0.24 / task** → **~$240 / 1k**. Anthropic: **task value must exceed this**; they do not publish a break-even.

**Loop D — fan-out catastrophe.** 50 subs × 10 Loop-A calls: \(50\times 10\times \$0.008 = \$4 / task\) → **$4,000 / 1k** plus the lead. AISVS 9.1.2 (per-execution token/$ budgets) is an NFR.

**Web search add-on:** 3 subs × 8 searches = 24 searches → **$0.24 / task** at **$10 / 1K searches** — often **larger than Sonnet tokens** on Loop A.

**Other published multipliers (do not invent others):** Magentic ledger ablation **−31%** quality (not $); M1-Parallel **≤2.2×** latency **and** multiplies cost unless cancelled; data-science blackboard **~1.8–2.3×** $ vs master–slave/RAG (one domain). Better MCP tool descriptions **−40%** completion time (Anthropic).

**Coordination overhead you actually pay:** (1) router/supervisor tokens (+33% calls vs sticky specialist on Loop A); (2) history splicing (`full_history`; missing `input_filter`); (3) duplicate work from vague briefs; (4) join/CitationAgent; (5) retries without a fleet breaker (429 stampede); (6) A2A Card fetch / OAuth / heartbeats — usually ≪ LLM $ but dominate **p99** if the callee is cold.

**Cache.** Sonnet 5 cache hit **$0.20 / MTok** vs **$2** = **10×** input discount on the static prefix. Hierarchical supervisors with shared team prompts cache best; swarms that rewrite `active_agent` prompts every hop cache worse.

### 3.2 Latency SLA targets and mitigations

⚠️ **No vendor publishes agent-loop p50/p95/p99 for supervisor-worker systems as of 2026-08-21.** Bound them from architecture. Temporal waiting for HITL is **not** a latency SLO (parked workflows consume no worker CPU). A2A blocking calls inherit callee p99.

| Percentile | Sequential pipeline / handoff | Parallel-sync supervisor wave | Mitigation |
| --- | --- | --- | --- |
| **p50 [inferred]** | Sum of mean stages; handoff turn 2 skips router (LangChain **2** calls vs subagents’ **4**) | ≈ mean(max(workers)) + join + lead think | Haiku/Luna **router**; Opus/Sol lead only when complexity fires; `last_message` + filesystem refs |
| **p95 [inferred]** | Sticky specialist dominates (helpdesk: not triage). Vague briefs duplicate work inside the wave | Straggler worker ≈ wave; `parallel_tool_calls=False` serializes and **inflates** this | Per-worker deadline; effort rules (1 vs 2–4 vs >10); skip CitationAgent on the interactive path |
| **p99 [inferred]** | Ping-pong until `max_turns=10`; GroupChat \(\propto N^2\); cold A2A callee | Whole wave blocks on one 500 (Anthropic bottleneck); 50-sub fan-out; 429 retry storm | Hop fuse → human; Magentic `max_stalls=3` then **replan not re-handoff**; fleet circuit breaker; admission control on `Send` length |

Published fragments, **not** SLOs: Anthropic parallelization **≤90%** wall-clock cut; M1-Parallel **≤2.2×** with early stop; blackboard paper **132.0–145.2 s** and **no** latency win vs RAG/master–slave; Openlayer: parallelism helped ~**80%** of some parallel tasks, hurt ~**70%** of sequential reasoning.

| Tier | Mitigations |
| --- | --- |
| p50 | Sticky handoff on repeat UX; cache supervisor+worker playbooks; Haiku/Luna classify |
| p95 | Sync-wave size 3–5; per-worker timers; `output_mode=last_message`; brief template with **out of scope** |
| p99 | AISVS budgets in **code**; swarm hop cap; kill-switch; A2A async + progress; never LATS/M1-Parallel without cancellation on the interactive fuse |

### 3.3 Throughput and back-pressure

The supervisor is a **single-writer join**. `parallel_tool_calls=False` (default) serializes workers and caps throughput at lead-think + Σ workers. Fan-out without a fleet breaker turns 429s into a **retry amplification** (Temporal RetryPolicy is **not** a breaker).

**Back-pressure design:**

1. Gateway admits interactive traffic if the **provider** breaker is closed/half-open **or** a degraded path exists (cached `active_agent` + allowlist tools, or `escalate_to_human`). A single worker breaker does **not** shed the whole saga if hierarchical teams do not join.
2. Bulkhead: lead pool vs worker pool vs citation vs A2A client vs MCP search. A 32k-token synthesizer must not steal slots from `handoff`.
3. Honor 429 with **full jitter**; thinking tokens count toward TPM. Cap `Send` length and subagent count in the runtime (Loop D).
4. Shed order: drop debate/M1-Parallel first, then CitationAgent, then extra subs (effort down to 1), then sticky specialist without router, then human. Never shed RBAC/downscope. Never auto-enable refund handoff when shed.
5. LangGraph checkpointers serialize concurrent updates per `thread_id`. Swarm: **disable parallel tool calls** so two `transfer_to_*` cannot fire in one tick (`active_agent` race).
6. A2A: non-blocking + push for cross-org; blocking `SendMessage` holds a worker slot for callee p99.

**Worked capacity [inferred].** 10 Loop-A handoff tasks/s on Sonnet 5 (**$24 / 1k**) → **$0.24 / s** ≈ **~$21k/mo** model spend before search SKUs. Same QPS on Loop C mixed Opus/Sonnet (**~$240 / 1k**) → **~$2.1M/mo**. Loop D uncapped 50-sub is **$4 / task** — 10/s would be **$3.5M/day**. Web search at 24 searches/task adds **$0.24 / task** = another **~$21k/mo** at 10/s.

### 3.4 Availability, RPO/RTO, compliance — explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **gateway** with degrade: skip dead worker → replan remaining → Haiku router → `escalate_to_human`. Provider is a dependency | Degraded ≠ “the swarm finished”; log `worker_skipped` / `hop_fuse` as product metrics |
| RPO | Last checkpointed super-step / completed Temporal Activity / A2A task transition. App state **0** before irreversible tools | Treating in-flight lead tokens as durable violates RPO; A2A artifacts are files — classify before crossing orgs |
| RTO | Interactive: fail over **<1 s** to secondary model or sticky specialist. Research jobs: resume thread, do **not** re-spawn the wave | Fast failover vs identical research DAG; rainbow-unsafe deploys kill in-flight graphs |
| Consistency | Reducers on parallel `Send`; single-writer blackboard cycles; A2A client version map; idempotency `workflow_id + step` | Last-write-wins without reducer; two refinements sharing `artifact-name` |
| Compliance | Delegation audit (from, to, mechanism, principal, `token_jti`, filtered-history **hash**). Checkpoints in a DPA-covered store — Enterprise Claude audit is the **chat** product, not your agent runtime. HIPAA-ready SKU ≠ agent RPO | Extra hops = extra PII copies; subagent isolation **helps** if the brief strips identifiers |
| Cost vs latency | Loop A **$9–32 / 1k**; Loop B **$40–66**; Loop C **$135–240**; Loop D **$4,000 / 1k**; search SKU can exceed tokens | Paying 15× research tokens for a sticky FAQ; handoff **+$22 / 1k** vs parallel subagents on Loop B |
| Isolation vs $ | Subagents 4 calls / 9K vs skills 3 calls / 15K vs handoff sticky 2 calls on repeat | Process-deep IAM vs prompt-deep playbooks |
| Parallelism vs steerability | Sync wave (Anthropic today) vs async A2A tasks | 90% wall-clock cut vs mid-wave control |
| Cache vs swarm | Shared team prompts vs rewriting `active_agent` prefixes every hop | 10× input discount vs sticky UX |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution (Temporal / Kafka)

Agents are **stateful**; a mid-loop crash cannot “just restart” (Anthropic: too expensive, user-visible). Pair model-driven adaptation (“search is failing, try another”) with **retries + checkpoints** and **rainbow deploys** so in-flight agents are not cut over mid-plan.

| Agent concept | Temporal | Why |
| --- | --- | --- |
| Lead loop / supervisor graph | **Workflow** (deterministic) | Replay from Event History; idle HITL = **zero compute** |
| LLM call, MCP, A2A, browser | **Activity** | Recorded once; replay must **not** re-call the LLM |
| Human approval | Signal / Update + `wait_condition` | Durable wait |
| Long transcript | **Continue-As-New** | Unbounded history kills replay |
| Handoff / tool / approval | Agent Harness `AgentEvent` stream | One audit spine |

OpenAI+Temporal demo: `AgentWorkflow` wraps `SandboxAgent`; `Runner.run()` unchanged; sandbox + model calls become Activities; **fork** snapshots workspace + history into a new workflow. LangGraph: checkpoints at **super-step** boundaries. After `interrupt()`, the **whole node restarts** — side effects before the pause re-run unless wrapped in Functional API `task`s. Footgun: “send email then interrupt for approval.”

**Kafka (log = chain of custody).** Topics per tenant-shard: `mas.delegate`, `mas.worker.result`, `mas.a2a.transition`, `mas.dlq`. Produce the **intent** (handoff + downscoped `token_jti` + idempotency key) **before** the worker writes (outbox). Compact on `thread_id` / `contextId`. Poison (malformed `Command`, repeated crash on same `worker_id`) → DLQ after \(N\); do not block the partition. Temporal Event History **is** the audit; AG2 Network Hub WAL is the AG2 analogue.

**Saga (compensating transactions).** Register **compensation before** the forward Activity (lost response still rolls back); compensations **LIFO**; all **idempotent**. Do **not** ask the LLM to invent compensations at failure time — put them in the workflow, keyed by `workflow_id`. If compensation fails non-retryably, park `ROLLBACK_PENDING_FIX` (AISVS C9.6).

| Forward worker action | Compensation | Irreversible? |
| --- | --- | --- |
| Create CRM record | Archive / delete | Usually reversible |
| Charge card | Refund | Partial; money has its own saga |
| Send customer email | Apology email | **Cannot unsend** (Garcia-Molina 1987 + Fowler) |
| A2A `COMPLETED` artifact published | New refinement task, not mutate old task | Spec: tasks immutable |
| MCP `tools/call` with write | Compensating tool | Must be on the **worker’s** allowlist, not the lead’s |

**Locking / races:** LangGraph channel without reducer → last-write-wins on parallel `Send` (`operator.add` / private scratch). Blackboard lost update → single writer per cycle or CRDT + conflict-resolver (LbMAS). `active_agent` + parallel tool-call batch → disable parallel on swarms. A2A artifact name clash → client version map. MCP session vs graph checkpoint: `/mcp` stateless — memory in checkpointer/store, **not** MCP session. Optimistic concurrency: `checkpoint_id` / task etag; reject stale writes.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, 500, 503, timeout, cold A2A callee | Exponential backoff + **full jitter** on **idempotent** Activities; honor Retry-After; **do** trip fleet breaker if consecutive **across** executions |
| Permanent | 400, 401, 422, content policy, unknown coworker, illegal `Command` dest | **No** retry (wastes $); fail the hop; page control plane for “coworker not found” |
| Poison pill | Reciprocal `transfer_to_*`; 50 subs on trivia; same worker crashes every replay; MCP that itself spawns agents | Hop fuse; subagent cap; payload hash + \(N\) crashes → DLQ; recursive-agency depth 1 |
| Semantic | Vague brief → duplicate search; telephone game through the lead; SEO-farm sources; o1 refusals shrinking coverage | Brief template + overlap metric; filesystem refs + CitationAgent; source-quality rubric; do not put policy-heavy models on write tools |

| Failure | Supervisor-worker (sync wave) | Handoff swarm | A2A remote | Hierarchical team |
| --- | --- | --- | --- | --- |
| One worker 500s | Whole wave blocks | Conversation stuck on that agent | Task `FAILED`; new task in same `contextId` | Other teams proceed if top-level does not join |
| Infinite tool loop | `max_turns` / AISVS budget | Same | Server-side timeout | Team-level `max_turns` |
| Poisoned context | Isolated if own window | **Contaminates** sticky history | Opaque — you see artifacts | Team checkpointer isolates |
| Kill one worker identity | Remaining + replan | Need a handoff off the dead agent | New Agent Card version | Replace compiled subgraph |

Tell the model the tool is failing — necessary and **insufficient**. Pair with Activity retries and a breaker so the lead is not spending Opus tokens narrating a dead search API.

**Named production modes:** 50 subs (subagent-count metric + hard cap); vague briefs (query-embedding overlap); telephone game (artifact hash ≠ cited content); rainbow-unsafe deploys (dual-run old/new; pin prompt versions); CrewAI manager does all work (`task.delegations==0`); GroupChat broadcast (switch to Network / supervisor); ASI09 rubber-stamp (approval time <1 s).

### 4.3 Circuit breaker and fallback chain

Nygard/Fowler: **closed → open** on failure rate → **half-open** probe. Temporal **RetryPolicy is not a breaker**: hundreds of workflows retrying 429s **amplify** the outage. Pattern: workflow-level consecutive-failure counter per **provider/tool**; open → fail fast / fallback model / skip that worker; cooldown; one probe.

```
CLOSED ──(consecutive failures ≥ N across executions)──▶ OPEN ──(cooldown)──▶ HALF_OPEN
  ▲                                                      │ fail fast                    │
  │                                                      │ skip worker / fallback       ├── probe OK ──▶ CLOSED
  └──────────────────────────────────────────────────────┴──────────────────────────────┘ probe fail ──▶ OPEN
```

| Error | Retry Activity? | Open breaker? |
| --- | --- | --- |
| 429, 500, 503, timeout | Yes, exponential backoff, high max interval | If consecutive **across** executions |
| 400, 401, 422, content policy | **No** | No (logic bug) |
| Worker exception “coworker not found” | No | Page the control plane |

AISVS 9.1.1: per-tool quotas and timeouts. 9.1.3: swarm kill-switch.

**Fallback chain (research order):**

1. **Primary worker + primary model** behind a closed breaker.
2. **Skip that worker / secondary model** (Haiku/Luna router, Terra/Sonnet specialist); Magentic replan from Progress Ledger.
3. **Sticky degrade:** last `active_agent` allowlist tools only, or supervisor `last_message` without a new wave.
4. **Deterministic `escalate_to_human`** — valid structured output so parsers do not crash. Timeout-deny on irreversible tools. Never fall back to “lead calls all tools on behalf of workers.”

Hedging: duplicate a straggler **read** tool; cancel loser. Do not hedge `payments.charge`.

### 4.4 Zero-Trust MCP, tool RBAC, PII, immutable logs, confused deputy

**Zero-Trust MCP (tool plane).** MCP 2025-06-18 onward: server is an **OAuth 2.1 resource server**, not a token issuer. 2026-07-28 SEPs harden clients (SEP-2468 `iss` on the authorization response — mix-up defense; RFC 9207).

1. **Audience binding (RFC 8707)** — token is for **this** MCP server.
2. **No token passthrough** — never forward the client token downstream; **token exchange**.
3. **Per-client consent** on MCP **proxies** that use a **static** third-party `client_id` plus DCR plus IdP **consent cookies** — textbook confused deputy.
4. `state` stored **only after** MCP-side consent; not before redirect.

Never a long-lived shared API key on the supervisor that all workers reuse. Short-lived, per-agent, per-session credentials (A2A mTLS scheme). Least privilege on **every** `tools/call`.

**Per-agent RBAC:**

| Principal | May | Must not |
| --- | --- | --- |
| Router / lead | Spawn workers, read summaries, write plan Memory | Hold production write tools (Stripe, email send) |
| Domain specialist | Its tool allowlist | Other specialists’ tools; raw user refresh tokens |
| Citation / critic | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail (ASI09) |
| A2A callee | Skills on its Agent Card | Your VPC except via published artifacts |

CrewAI/LangGraph “give the manager all tools so it can help” **destroys** isolation. A2A skill-level `securityRequirements` is the protocol RBAC hook. Extended Agent Cards hide sensitive skills until authenticated.

**PII pipeline:** detect → redact **before** any hop → audit placeholders (never raw). Every extra hop is a **copy**. Isolated subagent windows help if the brief strips identifiers and the sub returns aggregates. Handoffs that pass **full history** (OpenAI default) leak prior-turn PII into the refund agent — use `input_filter` / “pass only the handoff pair” / filesystem refs. Blackboards are **worse** unless partitioned (LbMAS private spaces). A2A artifacts may be files — classify before org boundaries. Traces: `trace_include_sensitive_data` gates; Anthropic production tracing of **decision patterns without conversation contents**. Retention: your **agent runtime** needs its own DPA-covered checkpoint store.

**Confused deputy — two layers.**

*OAuth proxy deputy (MCP spec).* Conditions: static IdP `client_id` + DCR + consent cookie + no per-MCP-client consent. Attacker registers `redirect_uri=attacker.com`, rides the cookie, skips consent, steals a code. Fix: per-client consent, exact redirect match, single-use `state` after consent.

*Agent deputy (multi-agent specific).* Supervisor has GitHub admin. User asks a worker to “update the README.” Worker issues a tool call that the supervisor **executes with supervisor credentials**. Fix: **downscope at handoff** — `on_handoff` mints a token whose audience is the worker’s MCP servers and whose scopes match the brief. A2A `AUTH_REQUIRED` when the callee needs a user gesture. Never “the lead calls all tools on behalf of workers.” Token passthrough is the same bug in both layers.

**Delegation audit (append-only, hash-chained for ASI10/AGT-style tamper evidence):**

`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate (none|pending|approved|rejected), artifact_ids, omitted_history_hash`

OpenAI: `handoff` spans + `on_handoff`. LangSmith: graph node + tool spans. A2A: `taskId`+`contextId`+status. Temporal: Event History. AG2: Hub WAL. Filters **must** log what was dropped (hash of omitted items) or IR cannot reconstruct why the specialist lacked context.

**OWASP Agentic Top 10 (2026) concentrated here:** **ASI07** Insecure Inter-Agent Communication (A2A/MCP without mTLS/audience), **ASI08** Cascading Agent Failures (fan-out, ping-pong, retry storms), **ASI03** Identity/Privilege Abuse (delegation without downscope), **ASI09** Human-Agent Trust. AISVS C9: budgets, kill-switch, approval manifests, timeout-deny, out-of-band kill.

---

## 5. Production Enterprise Code

Stdlib-only runtime: full-jitter retries, circuit breaker (closed → open → half-open), primary → secondary → deterministic `escalate_to_human`, correlation-id JSON logs, PII detect→redact→audit, supervisor router, per-worker tool/principal isolation, downscoped `token_jti` at handoff, hop-fuse, hash-chained delegation audit, graceful skip of an open worker breaker. Run: `python mas_runtime.py`.

```python
#!/usr/bin/env python3
"""Supervisor-worker-handoff runtime (stdlib only). Run: python mas_runtime.py"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

MAX_HOPS = 3
MAX_WORKER_TURNS = 4
BREAKER_FAILURES = 5
BREAKER_RECOVERY_S = 30.0
POLICY_VERSION = "mas-2026-08-21"

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "active_agent": getattr(record, "active_agent", None),
            "hop": getattr(record, "hop", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
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


def build_logger(correlation_id: str, tenant: str, thread_id: str) -> CorrelationAdapter:
    base = logging.getLogger("mas.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "thread_id": thread_id}
    )


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = BREAKER_FAILURES,
        recovery_seconds: float = BREAKER_RECOVERY_S,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (
            time.monotonic() - self._opened_at
        ) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            time.sleep(random.random() * cap)
    assert last is not None
    raise last


class ActionKind(Enum):
    FINISH = "finish"
    DELEGATE = "as_tool"
    HANDOFF = "handoff"
    ESCALATE = "escalate_to_human"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    dest: str | None = None
    text: str = ""
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    tools: frozenset[str]
    principal: str
    handoffs: frozenset[str]


@dataclass
class ToolResult:
    name: str
    payload: str
    idempotency_key: str


class ToolProxy:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        self._executors = executors
        self._done: dict[str, ToolResult] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        allowed: frozenset[str],
        principal: str,
        thread_id: str,
        turn: int,
    ) -> ToolResult:
        if name not in allowed:
            raise PermanentError(f"rbac deny {name} principal={principal}")
        if name not in self._executors:
            raise PermanentError(f"unknown tool {name}")
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(
            f"{principal}|{thread_id}|{name}|{canonical}|{turn}".encode()
        ).hexdigest()
        with self._lock:
            hit = self._done.get(key)
        if hit is not None:
            return hit
        raw = self._executors[name](args)
        result = ToolResult(name, json.dumps(raw, default=str), key)
        with self._lock:
            self._done[key] = result
        return result


def mint_token(principal: str, tools: frozenset[str]) -> str:
    material = f"{principal}|{POLICY_VERSION}|{','.join(sorted(tools))}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def hash_omitted(items: list[str]) -> str:
    blob = json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class DelegationLog:
    def __init__(self) -> None:
        self._chain: list[dict[str, Any]] = []
        self._prev = "0" * 16
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = json.dumps(row, sort_keys=True, default=str)
            digest = hashlib.sha256((self._prev + payload).encode()).hexdigest()[:16]
            entry = {**row, "prev": self._prev, "hash": digest}
            self._chain.append(entry)
            self._prev = digest
            return entry

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self._chain)


class Planner(Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


@dataclass
class ScriptedPlanner:
    name: Planner
    script: dict[tuple[str, int], Action]
    fail: type[Exception] | None = None

    def act(self, agent: str, turn: int) -> Action:
        if self.fail is not None:
            raise self.fail(f"{self.name.value} down")
        key = (agent, turn)
        if key not in self.script:
            raise PermanentError(f"no script for {key}")
        return self.script[key]


class FallbackPlanner:
    def __init__(
        self,
        primary: ScriptedPlanner,
        secondary: ScriptedPlanner,
        breaker: CircuitBreaker,
        *,
        retry_attempts: int = 4,
        retry_base: float = 0.25,
        retry_max: float = 8.0,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.retry_attempts = retry_attempts
        self.retry_base = retry_base
        self.retry_max = retry_max

    def act(self, agent: str, turn: int, log: CorrelationAdapter) -> Action:
        kwargs = {
            "attempts": self.retry_attempts,
            "base_seconds": self.retry_base,
            "max_seconds": self.retry_max,
        }
        try:
            self.breaker.allow()
            action = retry_call(lambda: self.primary.act(agent, turn), **kwargs)
            self.breaker.record_success()
            log.info("primary_ok planner=%s", self.primary.name.value)
            return action
        except (CircuitOpenError, TransientError, PermanentError) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.record_failure()
            log.warning("primary_fail err=%s", exc)
            try:
                action = retry_call(lambda: self.secondary.act(agent, turn), **kwargs)
                log.info("secondary_ok planner=%s", self.secondary.name.value)
                return action
            except (TransientError, PermanentError) as sec:
                log.error("degraded err=%s", sec)
                return Action(
                    ActionKind.ESCALATE,
                    text="degraded: cannot complete this turn",
                    reason=str(sec),
                )


def _lookup_invoice(args: dict[str, Any]) -> dict[str, Any]:
    return {"invoice": args.get("order_id", "unknown"), "status": "open", "amount": 42}


def _charge(_args: dict[str, Any]) -> dict[str, Any]:
    raise PermanentError("charge is HITL-only")


WORKERS: dict[str, WorkerSpec] = {
    "supervisor": WorkerSpec("supervisor", frozenset(), "lead-svc", frozenset({"billing", "support"})),
    "billing": WorkerSpec(
        "billing",
        frozenset({"lookup_invoice"}),
        "billing-svc",
        frozenset({"support"}),
    ),
    "support": WorkerSpec(
        "support",
        frozenset({"lookup_invoice"}),
        "support-svc",
        frozenset({"billing"}),
    ),
}


class MultiAgentRuntime:
    def __init__(
        self,
        planner: FallbackPlanner,
        proxy: ToolProxy,
        workers: dict[str, WorkerSpec],
        audit: DelegationLog,
        worker_breakers: dict[str, CircuitBreaker] | None = None,
        max_hops: int = MAX_HOPS,
        max_worker_turns: int = MAX_WORKER_TURNS,
    ) -> None:
        self.planner = planner
        self.proxy = proxy
        self.workers = workers
        self.audit = audit
        self.worker_breakers = worker_breakers or {}
        self.max_hops = max_hops
        self.max_worker_turns = max_worker_turns

    def _enabled(self, spec: WorkerSpec, dest: str, state: dict[str, Any]) -> bool:
        if dest not in spec.handoffs:
            return False
        if dest == "billing" and not state.get("order_id"):
            return False
        return True

    def _delegate_row(
        self,
        *,
        correlation_id: str,
        src: str,
        dest: str,
        mechanism: str,
        principal: str,
        token_jti: str,
        tools: frozenset[str],
        omitted: list[str],
        reason: str,
        human_gate: str,
    ) -> dict[str, Any]:
        return self.audit.append(
            {
                "trace_id": correlation_id,
                "from_agent": src,
                "to_agent": dest,
                "mechanism": mechanism,
                "input_type": {"reason": reason},
                "principal_id": principal,
                "token_jti": token_jti,
                "tools_enabled": sorted(tools),
                "policy_version": POLICY_VERSION,
                "human_gate": human_gate,
                "omitted_history_hash": hash_omitted(omitted),
            }
        )

    def run(self, user_text: str, *, tenant: str, order_id: str | None = None) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        thread_id = f"{tenant}:mas"
        log = build_logger(correlation_id, tenant, thread_id)
        prompt, pii_audit = redact_pii(user_text)
        log.info("pii_redactions count=%s", len(pii_audit))
        state: dict[str, Any] = {
            "order_id": order_id,
            "messages": [prompt],
            "active_agent": "supervisor",
            "hop": 0,
            "owner": "supervisor",
        }
        observations: list[str] = []
        turn_by_agent: dict[str, int] = {}

        for _ in range(self.max_hops + self.max_worker_turns + 4):
            agent = state["active_agent"]
            spec = self.workers[agent]
            turn_by_agent[agent] = turn_by_agent.get(agent, 0)
            local_turn = turn_by_agent[agent]
            if agent != "supervisor" and local_turn >= self.max_worker_turns:
                raise PermanentError("worker turn cap")
            log.extra["active_agent"] = agent
            log.extra["hop"] = state["hop"]
            log.extra["breaker"] = self.planner.breaker.state.value

            wb = self.worker_breakers.get(agent)
            if wb is not None:
                try:
                    wb.allow()
                except CircuitOpenError:
                    log.warning("worker_breaker_open skip=%s", agent)
                    state["active_agent"] = "supervisor"
                    state["owner"] = "supervisor"
                    observations.append(f"skipped:{agent}")
                    continue

            action = self.planner.act(agent, local_turn, log)
            turn_by_agent[agent] = local_turn + 1

            if action.kind is ActionKind.ESCALATE:
                log.warning("escalate reason=%s", action.reason)
                self._delegate_row(
                    correlation_id=correlation_id,
                    src=agent,
                    dest="human",
                    mechanism="escalate",
                    principal=spec.principal,
                    token_jti="none",
                    tools=frozenset(),
                    omitted=[],
                    reason=action.reason or action.text,
                    human_gate="pending",
                )
                return {
                    "correlation_id": correlation_id,
                    "owner": "human",
                    "text": action.text,
                    "degraded": True,
                    "pii_audit": pii_audit,
                    "observations": observations,
                    "audit": self.audit.rows,
                }

            if action.kind is ActionKind.FINISH:
                if wb is not None:
                    wb.record_success()
                return {
                    "correlation_id": correlation_id,
                    "owner": state["owner"],
                    "text": action.text,
                    "degraded": False,
                    "pii_audit": pii_audit,
                    "observations": observations,
                    "audit": self.audit.rows,
                    "hops": state["hop"],
                }

            if action.kind is ActionKind.HANDOFF:
                dest = action.dest or ""
                if state["hop"] >= self.max_hops:
                    log.error("hop_fuse hops=%s", state["hop"])
                    return self.run_escalate_path(
                        correlation_id, pii_audit, observations, "hop_fuse", log
                    )
                if not self._enabled(spec, dest, state):
                    raise PermanentError(f"handoff disabled {agent}->{dest}")
                dest_spec = self.workers[dest]
                token_jti = mint_token(dest_spec.principal, dest_spec.tools)
                omitted = [m for m in state["messages"][:-1]]
                self._delegate_row(
                    correlation_id=correlation_id,
                    src=agent,
                    dest=dest,
                    mechanism="handoff",
                    principal=dest_spec.principal,
                    token_jti=token_jti,
                    tools=dest_spec.tools,
                    omitted=omitted,
                    reason=action.reason,
                    human_gate="none",
                )
                state["messages"] = [state["messages"][-1]]
                state["active_agent"] = dest
                state["owner"] = dest
                state["hop"] += 1
                log.info("handoff dest=%s jti=%s hop=%s", dest, token_jti, state["hop"])
                continue

            if action.kind is ActionKind.DELEGATE:
                dest = action.dest or ""
                dest_spec = self.workers[dest]
                dest_wb = self.worker_breakers.get(dest)
                if dest_wb is not None:
                    try:
                        dest_wb.allow()
                    except CircuitOpenError:
                        observations.append(f"skipped:{dest}")
                        log.warning("delegate_skip dest=%s", dest)
                        continue
                token_jti = mint_token(dest_spec.principal, dest_spec.tools)
                self._delegate_row(
                    correlation_id=correlation_id,
                    src=agent,
                    dest=dest,
                    mechanism="as_tool",
                    principal=dest_spec.principal,
                    token_jti=token_jti,
                    tools=dest_spec.tools,
                    omitted=[],
                    reason=action.reason,
                    human_gate="none",
                )
                if action.tool:
                    result = self.proxy.execute(
                        action.tool,
                        action.args,
                        allowed=dest_spec.tools,
                        principal=dest_spec.principal,
                        thread_id=thread_id,
                        turn=local_turn,
                    )
                    observations.append(result.payload)
                    state["messages"].append(result.payload)
                if dest_wb is not None:
                    dest_wb.record_success()
                state["active_agent"] = "supervisor"
                state["owner"] = "supervisor"
                log.info("as_tool dest=%s jti=%s", dest, token_jti)
                continue

            raise PermanentError(f"unknown action {action.kind}")

        raise PermanentError("runtime loop cap")

    def run_escalate_path(
        self,
        correlation_id: str,
        pii_audit: list[dict[str, str]],
        observations: list[str],
        reason: str,
        log: CorrelationAdapter,
    ) -> dict[str, Any]:
        log.error("graceful_degrade reason=%s", reason)
        self._delegate_row(
            correlation_id=correlation_id,
            src="runtime",
            dest="human",
            mechanism="escalate",
            principal="runtime",
            token_jti="none",
            tools=frozenset(),
            omitted=[],
            reason=reason,
            human_gate="pending",
        )
        return {
            "correlation_id": correlation_id,
            "owner": "human",
            "text": "degraded: escalate_to_human",
            "degraded": True,
            "pii_audit": pii_audit,
            "observations": observations,
            "audit": self.audit.rows,
        }


def _retry() -> dict[str, Any]:
    return dict(retry_attempts=2, retry_base=0.01, retry_max=0.04)


def _demo() -> None:
    proxy = ToolProxy({"lookup_invoice": _lookup_invoice, "charge_card": _charge})

    happy_primary = ScriptedPlanner(
        Planner.PRIMARY,
        {
            ("supervisor", 0): Action(
                ActionKind.DELEGATE,
                dest="billing",
                tool="lookup_invoice",
                args={"order_id": "o-1"},
                reason="invoice lookup",
            ),
            ("supervisor", 1): Action(ActionKind.FINISH, text="invoice o-1 is open for $42"),
        },
    )
    happy = MultiAgentRuntime(
        FallbackPlanner(happy_primary, ScriptedPlanner(Planner.SECONDARY, {}), CircuitBreaker(), **_retry()),
        proxy,
        WORKERS,
        DelegationLog(),
    )
    out = happy.run("status for user@example.com ssn 123-45-6789", tenant="t1", order_id="o-1")
    assert out["owner"] == "supervisor"
    assert out["degraded"] is False
    assert any(x["type"] == "email" for x in out["pii_audit"])
    assert out["audit"][0]["mechanism"] == "as_tool"
    assert out["audit"][0]["principal_id"] == "billing-svc"
    assert out["audit"][0]["hash"]

    isolated = ScriptedPlanner(
        Planner.PRIMARY,
        {
            ("supervisor", 0): Action(
                ActionKind.DELEGATE,
                dest="billing",
                tool="charge_card",
                args={"order_id": "o-1"},
                reason="should deny",
            ),
        },
    )
    iso_rt = MultiAgentRuntime(
        FallbackPlanner(isolated, ScriptedPlanner(Planner.SECONDARY, {}), CircuitBreaker(), **_retry()),
        proxy,
        WORKERS,
        DelegationLog(),
    )
    try:
        iso_rt.run("charge", tenant="t1", order_id="o-1")
        raise AssertionError("expected rbac deny")
    except PermanentError as exc:
        assert "rbac deny charge_card" in str(exc)

    ping = ScriptedPlanner(
        Planner.PRIMARY,
        {
            ("supervisor", 0): Action(ActionKind.HANDOFF, dest="billing", reason="triage"),
            ("billing", 0): Action(ActionKind.HANDOFF, dest="support", reason="bounce"),
            ("support", 0): Action(ActionKind.HANDOFF, dest="billing", reason="bounce"),
            ("billing", 1): Action(ActionKind.HANDOFF, dest="support", reason="bounce"),
        },
    )
    ping_rt = MultiAgentRuntime(
        FallbackPlanner(ping, ScriptedPlanner(Planner.SECONDARY, {}), CircuitBreaker(), **_retry()),
        proxy,
        WORKERS,
        DelegationLog(),
        max_hops=3,
    )
    fused = ping_rt.run("where is my laptop", tenant="t1", order_id="o-1")
    assert fused["degraded"] is True
    assert fused["owner"] == "human"
    assert any(r.get("input_type", {}).get("reason") == "hop_fuse" for r in fused["audit"])

    dead = ScriptedPlanner(Planner.PRIMARY, {}, fail=TransientError)
    also_dead = ScriptedPlanner(Planner.SECONDARY, {}, fail=TransientError)
    deg = MultiAgentRuntime(
        FallbackPlanner(
            dead,
            also_dead,
            CircuitBreaker(failure_threshold=1),
            **_retry(),
        ),
        proxy,
        WORKERS,
        DelegationLog(),
    )
    degraded = deg.run("hello", tenant="t1", order_id="o-1")
    assert degraded["degraded"] is True
    assert degraded["owner"] == "human"

    skip_breaker = CircuitBreaker(failure_threshold=1)
    skip_breaker.record_failure()
    skip_planner = ScriptedPlanner(
        Planner.PRIMARY,
        {
            ("supervisor", 0): Action(
                ActionKind.DELEGATE, dest="billing", tool="lookup_invoice", args={"order_id": "o-1"}
            ),
            ("supervisor", 1): Action(ActionKind.FINISH, text="answered without billing"),
        },
    )
    skip_rt = MultiAgentRuntime(
        FallbackPlanner(skip_planner, ScriptedPlanner(Planner.SECONDARY, {}), CircuitBreaker(), **_retry()),
        proxy,
        WORKERS,
        DelegationLog(),
        worker_breakers={"billing": skip_breaker},
    )
    skipped = skip_rt.run("status", tenant="t1", order_id="o-1")
    assert skipped["text"] == "answered without billing"
    assert any("skipped:billing" in x for x in skipped["observations"])

    print(
        json.dumps(
            {
                "ok": True,
                "happy_owner": out["owner"],
                "hops_fused": fused["degraded"],
                "fallback_human": degraded["owner"],
                "worker_skipped": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _demo()
```

**Behavior encoded (maps to §§2–4):**

- Supervisor `as_tool` keeps user-facing ownership; handoff flips `owner` to the specialist.
- Worker isolation: `charge_card` is not on billing’s allowlist even if the lead asked; principal is `billing-svc`, not `lead-svc`.
- `on_handoff` analogue mints `token_jti` from principal+tools+policy; omitted history is hashed into the audit chain.
- Hop fuse (`MAX_HOPS=3`, OpenAI-class `max_turns` idea) breaks billing ↔ support ping-pong → structured human escalate.
- Primary `TransientError` trips the provider breaker; dual failure emits schema-stable `escalate_to_human`.
- Open **worker** breaker skips that identity and joins without killing the saga (hierarchical/fail-closed worker).
- PII redacted before any hop; idempotency key is `sha256(principal|thread|tool|args|turn)`.

**Interview talking point:** jittered retries handle 429; they do not make a confused deputy safe. Downscope + hop fuse + worker RBAC are three different classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file. Decision rule used below (OpenAI, LangChain, and Anthropic independently): **start with one agent + skills**. Add a second agent only when (a) tool/policy isolation is a compliance requirement, (b) parallel isolated context is the product, or (c) two teams ship independently.

### Scenario 1 — Internal IT helpdesk (sticky, policy isolation)

**Problem statement.** Multi-tenant IT helpdesk: FAQ, laptop status, billing/refund. Sticky specialist after triage (user should not re-explain on turn 2). Refund **write** is irreversible → HITL with ASI09 friction (amount on a structured card). Hop cap **3** then human. Cost target near Loop A handoff **~$24 / 1k** Sonnet 5 **[inferred]** (Terra **~$9 / 1k**). Must not be a research orchestrator. GroupChat of 8 personas is on the table because “collaboration.” CrewAI hierarchical is proposed before delegation telemetry is green.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Slack /    │ SSE │ CONTROL PLANE                                             │
│ portal     │────▶│ Gateway: SSO, correlation-id, TPM, provider CB            │
└────────────┘     │ Policy: PII detect→redact→audit; refund is_enabled        │
                   │          iff order_id in state                            │
                   │ Router: OpenAI-style triage handoff (not Magentic lead)   │
                   │ Orchestrator: Runner max_turns=10; hop fuse=3 → human     │
                   │ needs_approval on refund write; input_filter=remove_tools │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ transfer_to_* + downscope    │ MCP tools/call
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ FAQ / billing /  │        │ ITSM read (billing-svc)      │
                   │ refund workers   │        │ refund write: HITL + ticket  │
                   │ isolated tools   │        │ JSON-encode; no lead Stripe  │
                   │ active_agent     │        │                              │
                   │ persists turn 2  │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ RunState / thread_id; WORM: from→to, token_jti,           │
                   │ omitted_history_hash; hop_fuse and approval-time metrics  │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Handoff triage with one-sentence `handoffDescription` each. `input_filter=remove_all_tools`. `is_enabled` hides refund unless `order_id` in state. Combine: triage hands off to refund; refund calls policy **as_tool**. NFR: hop cap 3 → human. p95 dominated by the specialist, not triage ⚠️ measure your own. Avoid: GroupChat of 8; CrewAI hierarchical until `task.delegations>0`; spawning web-research subs for “where is my laptop.”

**Trade-off evaluation matrix.**

| Dimension | A. AG2 Classic GroupChat of 8 personas | B. Recommended: triage **handoff** + worker IAM + hop fuse 3 + refund HITL | C. LangGraph supervisor-worker (`output_mode=full_history`, join every turn) |
| --- | --- | --- | --- |
| Cost | Tokens \(\propto N^2\); N−1 injections/turn | Loop A **[inferred] $24 / 1k** Sonnet ($9 Terra); turn 2 **$16 / 1k extra** (2 calls) | Extra join every turn: **+$8 / 1k / turn [inferred]**; `full_history` splices tool I/O |
| Latency | Broadcast stall; no sticky skip | Turn 2 skips router (3+2=5 vs subagents 8 calls on repeat) | Re-route every turn (router **3** vs handoff **2**) |
| Ops | Speaker-selection debug; Hub WAL if you migrate to Network | `active_agent` log; `is_enabled` predicates; disable parallel tool calls on swarm | Supervisor bottleneck; `parallel_tool_calls=False` default serializes |
| Security | Every persona sees every utterance (PII lake) | Downscope at `on_handoff`; filters log omitted hashes; ASI09 friction on refund | Lead must **not** hold refund write tools; easy to “give manager all tools” |
| Scalability | N² context kills TPM | Sticky specialist scales with specialist pool; refund gated | Lead context fills with summaries; bad fit for sticky UX |

**Decision rationale.** **B** is the only option that matches sticky UX (LangChain repeat-request table), Loop A economics, and policy isolation (refund hidden until `order_id`). A is the AG2 Classic cost/PII anti-pattern the Network/supervisor exists to replace. C is the right isolation model for **research**, not for “let me transfer you.” Interview close: “Ownership moves; IAM downscopes; hops are a fuse, not a conversation strategy.”

### Scenario 2 — Competitive research / due diligence (breadth)

**Problem statement.** Internal due-diligence copilot: breadth-first web research, citations, Memory plan that survives 200k truncation. Quality bar is Anthropic-class research (**+90.2%** vs single Opus on their **internal** eval — not a public leaderboard). Token budget runtime-enforced: Loop C **~$135 / 1k** Sonnet 15× or **~$240 / 1k** at 30% Opus + 70% Sonnet **[inferred]**, **before** web search SKUs (3×8 searches = **$0.24 / task**). Hard subagent cap. Must survive rainbow deploys. A PM wants a handoff swarm “because sticky” and unbounded `Send`. Skills-only is proposed to save hops (Loop B **~$66 / 1k** but **15K** sludge, no parallel isolation).

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Analyst UI │ SSE │ CONTROL PLANE                                             │
│ / batch    │────▶│ Gateway: SSO, $ / task cap, subagent cap, provider CB     │
└────────────┘     │ Policy: PII redact; lead has Memory+spawn only            │
                   │ Router: Haiku/Luna complexity → 1 | 2–4 | cap>10          │
                   │ Orchestrator: Temporal Workflow; Activities=LLM/MCP/A2A   │
                   │ Magentic-style ledgers optional; max_stalls=3 replan      │
                   │ CitationAgent async post-process; rainbow pin prompts     │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ briefs + filesystem refs     │ MCP / optional A2A
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ Opus/Sol lead    │        │ web_search SKU capped        │
                   │ Sonnet/Terra/    │        │ filesystem artifacts (refs)  │
                   │ Haiku subs       │        │ CitationAgent read-only      │
                   │ isolated windows │        │ compensating tools on worker │
                   │ sync wave 3–5    │        │                              │
                   │ 3+ tools //      │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ Memory plan + thread_id; artifact hashes in checkpoint    │
                   │ Temporal history = control; WORM: spawn count, briefs,    │
                   │  citation map; traces of structures not contents          │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Anthropic-shaped orchestrator-worker: Opus (or Sol) lead, Sonnet/Terra/Haiku subs, Memory plan, filesystem artifacts, CitationAgent, **hard** subagent cap, effort rules in **code**. Parallel wave of 3–5. Eval: LLM-as-judge on factuality/citation/completeness (Anthropic: one judge 0–1 beat multi-judge). Deploy: rainbow + tracing of **structures** not contents. Cross-org sources: A2A 1.0 to a partner Agent Card; MCP stays inside your org. Avoid: handoff swarm (cannot parallelize domains — Loop B **14K+** sequential, **~$62 / 1k**); skills-only (15K context); unbounded `Send`; putting o1/policy-heavy models on write tools (Magentic WebArena refusals).

**Trade-off evaluation matrix.**

| Dimension | A. Handoff swarm / sticky specialists across domains | B. Recommended: orchestrator-worker, effort caps, filesystem refs, Temporal, search SKU cap | C. Skills-only single agent (load three 2k playbooks) |
| --- | --- | --- | --- |
| Cost | Loop B handoff **[inferred] ~$62 / 1k** + sequential tax **~$22 / 1k** vs subagents; no 15× research budget | Loop C **[inferred] $135 / 1k** Sonnet 15× or **~$240 / 1k** mixed Opus/Sonnet; Loop D **$4,000 / 1k** if uncapped; search **$0.24 / task** at 24 queries | Loop B skills **[inferred] ~$66 / 1k** (15K); cheap hops, expensive context; fails when isolation is the product |
| Latency | Sequential domains; skip-router helps **repeat FAQ**, not first-wave research | Parallel 3–5 × 3+ tools **≤90%** wall-clock (Anthropic); p99 ≈ max(worker)+join+lead **[inferred]**; sync = no mid-wave steer | 3 calls but 15K prefill; Lost-in-the-middle risk on piled skills |
| Ops | Ping-pong; `active_agent` races if parallel handoffs | Subagent-count metric; Magentic stall→replan; rainbow pin; Temporal no re-bill | One graph; no team IAM/release cadence |
| Security | Full-history leak across domains; one principal unless downscoped each hop | Isolated windows + stripped briefs; lead has no write tools; citation read-only; ASI08 fan-out capped in runtime | Prompt-deep only — no sandbox/identity/rate limit per skill |
| Scalability | Cannot `Send` three domains; history grows | Horizontal subs behind bulkheads; admission control on wave size; A2A for opaque partners | Cache-friendly prefix until the third skill lands |

**Decision rationale.** **B** is the only option that buys Anthropic’s actual product shape (isolated parallel search + Memory + citations + effort cap) and treats **$ cap / subagent cap as code**. A is the sticky-UX topology applied to a breadth problem — LangChain’s own 9K vs 14K table is the cost/latency proof. C wins for `<10` tools in one domain; it is not process-deep isolation and cannot cut wall-clock 90%. Interview close: “Cap the wave in the runtime. Pass refs, not transcripts. Search SKUs are first-class NFRs.”

---

*End of module. Six sections. Four topics (supervisor, worker, collaboration, delegation). Token `$ / 1k` tables are **[inferred]** from the stated Loops A–D and list prices dated 2026-08-21. No unpublished multi-agent e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from sequential-sum / parallel-max architecture plus Anthropic ≤90% / M1-Parallel ≤2.2× fragments.*
