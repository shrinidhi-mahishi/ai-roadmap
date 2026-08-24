# Research: Multi-Agent Systems

**Date researched**: 2026-08-21
**Sources consulted**: 52

Scope: supervisor (router, orchestrator, LangGraph `create_supervisor`, hierarchical supervisors-of-supervisors), worker (specialist agents, tool-scoped workers, skill isolation), collaboration (shared blackboard, Google/Linux Foundation A2A, message passing, debate, sequential vs parallel), delegation (OpenAI Agents SDK handoffs vs `Agent.as_tool()`, task assignment, authority, escalation, human handoff). Overlay: LangChain 1.x multi-agent patterns, LangGraph swarm, CrewAI hierarchical process, AutoGen Magentic-One / Microsoft Agent Framework 1.0, AG2 Classic GroupChat vs AG2 Network, Anthropic Research (Jun 2025), MCP as tool plane, Temporal as durable control plane. Prices and eval numbers below are from vendor docs, protocol specs, or named papers as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 multi-agent loop SLOs are invented; missing percentiles are marked. `$ per 1k tasks` figures are **[inferred]** from published token SKUs × a stated reference loop — not a vendor “per task” product.

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

Invariant: **the model never routes, never hands off, never grants authority**. It emits a structured action (tool call named `transfer_to_*`, A2A `SendMessage`, graph `Command`/`Send`). A **runtime** interprets that action, mutates durable state, and decides the next node. Collapsing “who may act” into the LLM prompt is the dominant enterprise failure.

| Layer | Owns | Typical objects | Failure if fused into the LLM |
| --- | --- | --- | --- |
| **Control** | Loop budget, next-agent, max hops, kill-switch, HITL gates, circuit open/close | LangGraph compiler + checkpointer `thread_id`; OpenAI `Runner` (`max_turns` default **10**); Temporal Workflow; A2A `TaskState`; CrewAI `Process`; MAF Orchestration | Infinite ping-pong; 50-subagent fan-out; spend unbounded |
| **Data** | Tool HTTP, MCP `tools/call`, A2A artifacts, sandboxes, blackboard blobs | Worker tools, MCP servers, A2A `Artifact`/`Part`, LangGraph `Store`, filesystem refs | PII in every hop; confused-deputy token passthrough |
| **Persistence control** | Resume identity | `thread_id`/`checkpoint_id`; OpenAI `RunState`/`session_id`; A2A `contextId`+`taskId`; Temporal workflow id | Restart from scratch after a 500; rainbow-deploy kills in-flight research |
| **Policy** | Who may call which tool under which principal | Per-agent tool lists, MCP Resource Indicators (RFC 8707), A2A `securitySchemes`, OPA/Cedar gates | Worker inherits supervisor’s OAuth cookie |

LangChain’s own framing (2026 OSS docs): “multi-agent” is usually a request for **context management**, **distributed development**, or **parallelization** — not a request for more LLMs. If context were infinite and latency zero, a single agent with all tools would dominate. Skills (progressive disclosure) are often the cheaper substitute for a second agent.

Microsoft Learn (updated 2026-07-06): **prefer platform-native orchestration for internal subagents**; use **MCP for tools/data**; use **A2A for opaque, cross-platform, cross-org agents**. That is the control/data split in protocol form: MCP is the tool bus; A2A is the agent bus.

### 1.2 Five topologies (and what they actually serialize)

LangGraph JS concepts + LangChain 1.x patterns + Microsoft Agent Framework 1.0 (Apr 2026) converge on the same five shapes:

| Topology | Who picks the next hop | User-facing owner | Parallelism | Typical product |
| --- | --- | --- | --- | --- |
| **Router** | One classification step, then specialist(s) | Synthesizer or the specialist | `Send` fan-out | LangChain Router + `Send` |
| **Supervisor / orchestrator-worker** | Central LLM (or ledger) every round | Supervisor synthesizes | Optional (`parallel_tool_calls`) | LangGraph `create_supervisor`; Anthropic Research; Magentic-One |
| **Hierarchical supervisors** | Supervisor of compiled supervisors | Top-level only | Per-team | `create_supervisor([research_team, writing_team])` |
| **Swarm / mesh / handoff** | Currently active agent | Whoever is `active_agent` | Sequential by default | LangGraph swarm; OpenAI `handoffs`; MAF handoff |
| **Custom / blackboard / Network** | State schema, Hub, or blackboard controller | Defined by workflow | Mixed | LangGraph custom graph; AG2 Hub+channels; LbMAS |

**Network (mesh).** Each agent can address every other. AG2 Classic: `GroupChat` + `GroupChatManager` broadcasts every utterance to all members; speaker selection is `auto` (LLM), `round_robin`, `random`, `manual`, or a callable / allowed-transition graph. Cost: **N−1 extra context injections per turn**. AG2 (2026 repo, `import ag2`) replaces GroupChat/swarm/nested-chat with a **Network**: a `Hub` owns registry, write-ahead log, and audit; typed **channels** are `conversation`, `consulting` (one-question-one-reply, auto-close), `discussion` (round-robin), `workflow` (`TransitionGraph` — closest analogue to GroupChat).

**Supervisor.** Workers never talk to each other. All routing returns to the supervisor. LangGraph `create_supervisor(agents, model, …)` compiles a `StateGraph` whose supervisor LLM is bound to **handoff tools**. Defaults that matter in production: `output_mode='last_message'` (not `full_history`); `parallel_tool_calls=False` (OpenAI/Anthropic only if you flip it); `add_handoff_messages=True`. LangChain 1.0 **recommends implementing the supervisor as ordinary tools** rather than the `langgraph-supervisor` package — more control over context engineering; the package is kept for 1.0 compatibility.

**Hierarchical.** A compiled supervisor is itself a Pregel object and can sit in another supervisor’s `agents=` list. Example from the library: `research_team` (researcher+math) and `writing_team` (writer+publisher) under `top_level_supervisor`. This is **not** free: each level adds at least one model call and a context splice. Use it when **teams have separate checkpointers, tool IAM, and release cadences**, not because the org chart looks like a tree.

**Swarm / handoff mesh.** `active_agent` persists across turns. LangGraph swarm: `create_handoff_tool(agent_name=…)` returns `Command(goto=agent_name, graph=Command.PARENT, update={messages, active_agent})`. Next user turn **skips the router** and resumes the last specialist — this is why LangChain’s repeat-request table shows handoffs at **2 calls** on turn 2 vs subagents’ **4**. Failure mode: two specialists with reciprocal `transfer_to_*` and no hop cap.

**Custom / blackboard.** Control is a schema + reducer, not a chat. LangGraph: `Send(node, arg_state)` from a conditional edge for **data-dependent fan-out**; fan-in via reducers (`Annotated[list, operator.add]`). Blackboard research (LbMAS, arXiv:2507.01701; data-science blackboard, arXiv:2510.01285): agents **do not receive assigned tasks**; they **elect to contribute** after a request is posted. That is a different authority model than supervisor task assignment.

### 1.3 Supervisor: router vs orchestrator vs hierarchical

These three words are not synonyms. Interviewers who treat them as one topology are describing three different **control-plane clocks**.

| Role | Clock | Decision | State | When it wins |
| --- | --- | --- | --- | --- |
| **Router** | Once per user turn (stateless unless wrapped) | Classify → 1..K specialists | Optional; often none | Known domains, parallel retrieval, no multi-hop ownership |
| **Orchestrator (lead)** | Every round until “enough” | Decompose, spawn, synthesize, re-spawn | Plan in Memory / Task Ledger / Progress Ledger | Breadth-first research, unknown search DAG |
| **Supervisor (LangGraph)** | Every worker return | Which worker tool next, or FINISH | Shared `messages` (+ optional private scratch) | Tool isolation + centralized reply |
| **Hierarchical supervisor** | Per level | Which *team* next | Nested graphs, nested checkpoints | Org/IAM boundaries, not token savings |

**Router mechanics (LangChain).** `Command(goto=agent)` for one specialist; `list[Send(agent, {query})]` for parallel. Tutorial pattern: GitHub + Notion + Slack in parallel, then a synthesizer. Router LLM call is **pure overhead** on repeat turns (3 calls every time vs handoffs’ 2).

**Orchestrator mechanics (Anthropic Research, published 2025-06-13).** Orchestrator-worker, not a chat swarm. LeadResearcher (Claude Opus 4 in the paper; today’s SKU analogue is Opus 5 at **$5 / $25 per MTok**) writes a plan to **Memory** because the 200k context will truncate. It spawns specialized Subagents (Sonnet 4 then; Sonnet 5 now at **$2 / $10 per MTok**) with: objective, output format, tool list, stop boundary. Subagents search in **isolated context windows**, call **3+ tools in parallel**, return **condensed summaries**. Lead decides whether to spawn another wave. A separate **CitationAgent** attributes claims to URLs. Official numbers: multi-agent Opus-lead + Sonnet-subs **+90.2%** vs single-agent Opus 4 on their internal research eval; token usage alone explains **80%** of BrowseComp variance (tool-call count + model choice are the other two factors in a three-factor model covering **95%**); agents use **~4×** chat tokens; multi-agent **~15×** chat; parallel 3–5 subagents × 3+ tools cut research wall-clock **up to 90%**. They **explicitly** say coding is a poor fit today (few truly parallelizable subtasks; agents are weak at real-time coordination). Early failure: lead spawned **50 subagents** for simple queries; vague “research the semiconductor shortage” caused three subs to duplicate 2025 supply-chain search while one wandered into 2021 auto chips. Mitigation: scale-effort rules in the prompt (simple: **1** agent, **3–10** tool calls; comparison: **2–4** subs, **10–15** calls each; complex: **>10** subs with disjoint responsibilities).

**Magentic-One orchestrator (Fourney et al., arXiv:2411.04468; now `MagenticOneGroupChat` in AutoGen AgentChat and a stable MAF 1.0 pattern).** Outer loop: **Task Ledger** (facts, guesses, plan). Inner loop: **Progress Ledger** (is it done? who next?). Stall detector: `max_stalls=3` default; then replan. Default `max_turns=20`. Workers are **tool-shaped**, not domain-shaped: WebSurfer, FileSurfer, Coder, ComputerTerminal. Ablations on GAIA validation: **removing full ledgers −31%**; removing any one worker **−21%** (Coder/Executor) to **−39%** (FileSurfer). Published task-completion (GPT-4o era, tests 2024-08..10, leaderboards as of 2024-10-21): **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench** (exact match). GPT-4o+o1-preview improved GAIA more than AssistantBench; o1 **refused 26%** of WebArena Gitlab tasks and **12%** of Shopping Admin — a reminder that a “smarter” orchestrator model can **shrink** coverage. Microsoft (2026): AutoGen is **maintenance mode**; new work should use **Microsoft Agent Framework 1.0** (sequential, concurrent, handoff, group chat, Magentic — all with streaming, checkpointing, HITL, pause/resume). Magentic remains the least hand-wired: goal + manager + specialists; manager owns plan/assign/stall/replan.

**LangGraph supervisor vs “agents as tools” (LangChain Subagents pattern).** Subagents: main agent keeps the user-facing reply; workers are tools. Handoffs: worker becomes the user-facing owner. LangChain’s documented call counts:

| Workload | Subagents | Handoffs | Skills | Router |
| --- | --- | --- | --- | --- |
| One-shot “buy coffee” | **4** calls | **3** | **3** | **3** |
| Repeat same request | **4+4=8** | **3+2=5** | **3+2=5** | **3+3=6** |
| Multi-domain (3× ~2k-token specialists, parallel OK) | **5** calls, **~9K** tokens | **7+** calls, **~14K+** (sequential, growing history) | **3** calls, **~15K** (all skill docs stay in context) | **5** calls, **~9K** |

Subagents win isolation + parallel. Handoffs win sticky conversations. Skills win “one agent, many playbooks.” Router wins explicit classification + parallel without a sticky specialist.

`create_supervisor` knobs that change topology: `output_mode=full_history` vs `last_message` (token vs fidelity); `include_agent_name='inline'` (XML tags — needed when the provider does not honor `name` on AI messages; OpenAI does); `parallel_tool_calls=True` turns a supervisor into a **fan-out orchestrator** for one tick.

### 1.4 Worker: specialists, tool-scoped workers, skill isolation

**Specialist agents** are workers whose **prompt + tool set + policy** change together. OpenAI’s orchestration guide: split only when instructions, tools, or **policy** actually change — extra agents multiply prompts, traces, and approval surfaces. CrewAI: tools at **agent** level and/or **task** level; hierarchical manager is supposed to assign by capability, but see §5 for the “coworker not found” class of bugs.

**Tool-scoped workers** (Magentic-One, Anthropic subs): the specialist is defined by **what it can touch** (browser, files, code interpreter, web search), not by a business domain. This maps cleanly to **IAM**: a FileSurfer identity should not hold Stripe scopes. Magentic-One ablations show you cannot cheaply drop a worker and hope another compensates — they sometimes did (FileSurfer reading code when Coder was absent; WebSurfer finding an online PDF viewer when FileSurfer was absent), but scores still dropped 21–39%.

**Skill isolation** is the **non-agent** alternative. LangChain Skills = progressive disclosure of prompts/knowledge (same idea as [Agent Skills](https://agentskills.io/) / llms.txt). A `load_skill` tool injects a playbook; the **same** agent stays in control. Extensions: dynamic tool registration on load (loading `database_admin` also registers backup/restore); hierarchical skills (`data_science` → `pandas_expert`); reference awareness (prompt points at scripts/files; agent reads them when needed). Token profile: few extra calls, **high** context once many skills are loaded (LangChain: **~15K** vs subagents’ **~9K** on the three-language comparison). Isolation is **prompt-deep, not process-deep**: a loaded skill does not get its own sandbox, identity, or rate limit unless you add them.

**Deep Agents** (LangChain harness on top of LangChain): ships subagents, skills, planning, virtual filesystem, context management as a packaged supervisor-worker kit. Use when you want the Anthropic-style “lead + filesystem artifacts” pattern without inventing Memory + CitationAgent from scratch. Anthropic’s appendix: **write subagent output to a filesystem** and pass **references** to the lead — avoids the telephone game and the cost of copying large artifacts through the coordinator’s context.

**CrewAI workers.** `Process.sequential` (default): task list order; prior output is next context. `Process.hierarchical`: **requires** `manager_llm` or `manager_agent`; tasks are **not** pre-assigned; manager plans, delegates, validates. Docs still say tasks then progress in a logical order under the manager — i.e. hierarchical is **assignment + review**, not automatic parallelism. `allow_delegation=True` on the manager is necessary but **not sufficient** (GitHub issue #4783, community “coworker not found”: delegation tool populated with the manager’s own role).

### 1.5 Collaboration: blackboard, A2A, message passing, debate, sequential vs parallel

**Message passing (in-process).** LangGraph `messages` channel with a reducer; OpenAI session items; AG2 Classic broadcast; MAF group chat. Cheap inside one runtime. No discovery, no cross-language contract, no task lifecycle. Fine for a single deployable.

**A2A (Agent2Agent) — the inter-process collaboration plane.** Originated at Google; donated to the Linux Foundation; TSC includes AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow. Spec **1.0.0** is the first production-stable version. Normative model is `spec/a2a.proto` (JSON is a generated convenience). Complementary to MCP by design:

| | MCP | A2A |
| --- | --- | --- |
| Problem | Agent → tool/data | Agent → agent (opaque) |
| Discovery | Tool list | **Agent Card** (skills, caps, security) |
| Unit of work | `tools/call` | **Task** + **Message** + **Artifact** |
| Orchestration | Host chooses tools, synthesizes | Callee has its own CoT; tools opaque to caller |
| Multi-turn | Optional elicitation; context stays on host | `contextId` groups tasks; `INPUT_REQUIRED` ≈ MCP elicit |
| Auth | OAuth 2.1 resource server + RFC 8707 | OpenAPI-style `securitySchemes` (API key, HTTP, OAuth2, OIDC, **mTLS**); skill-level `securityRequirements`; signed cards (JWS) |

A2A is **not** an ADK, **not** a sub-agent protocol, **not** Slack. Task states (proto enums; JSON may show `TASK_STATE_*`): `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`, `INPUT_REQUIRED`, `AUTH_REQUIRED`. `SendMessage` blocking (`returnImmediately=false`, default) waits until terminal or interrupted; non-blocking returns immediately — caller polls, subscribes (`streaming` capability), or takes **push notifications**. Extended Agent Card is **auth-gated** (`GetExtendedAgentCard`) when `capabilities.extendedAgentCard=true`. **Task immutability:** terminal tasks never restart; refinements create a **new** `taskId` in the same `contextId`, optionally with `referenceTaskIds`. Parallel follow-ups are first-class (flight + hotel + activity as sibling tasks). Artifact mutation tracking is **client-side** (same `artifact-name`, new `artifactId`). Google Cloud (v0.3 era, still relevant ops): gRPC binding, signed cards, ADK native A2A, deploy to Agent Engine / Cloud Run / GKE. MAF .NET packages updated to A2A v1 for both client and hosting.

**Blackboard.** Classical AI: knowledge sources write partial solutions to a shared board; a controller picks who runs next. LLM variants:

- LbMAS (arXiv:2507.01701): public board + private debate spaces; planner/decider/critic/cleaner/conflict-resolver; LLM controller selects agents from the current board; authors claim **token-economical** vs workflow-search MAS because they skip a supervised workflow-search stage.
- Data-science blackboard (arXiv:2510.01285): central agent **posts a request**; subs **opt in**. Reported: runtime **132.0–145.2 s** across RAG / master–slave / blackboard (no latency win); blackboard **~2.3×** RAG cost and **~1.8×** master–slave cost; quality **+54.1%** vs RAG, **+18.8%** vs master–slave. ⚠️ Single paper, one domain — do not treat as a universal cost law.

Blackboards **serialize by design** if only one knowledge source is active per cycle. They shine when specialists arrive at different rates and the solution is a **revision history**. They fail when you needed a DAG of independent searches (use `Send` / Anthropic-style parallel subs instead).

**Debate.** Multiagent Debate (Du et al. and follow-ons) and Mixture-of-Agents: multiple proposers, then rounds of critique, then a judge. This is **collaboration as verification**, not as work-splitting. Token cost is roughly **rounds × agents × context**. Use as a **verifier role** on high-value, non-parallelizable answers (legal memo, medical differential), not as a default topology. Combine with Anthropic’s CitationAgent: debate on claims, then a citation pass — two different workers.

**Sequential vs parallel.**

| Pattern | Mechanism | Latency | Token | Correctness risk |
| --- | --- | --- | --- | --- |
| Sequential pipeline | CrewAI sequential; LangGraph linear edges; MAF sequential | p99 ≈ sum of stages | Low duplication | Error compounds; no breadth |
| Sequential handoff | Swarm `active_agent` | Sticky; skip router on turn 2 | Grows unless filtered | Ping-pong |
| Parallel workers, sync join | Anthropic lead waits for the wave; LangGraph `Send` + reducer | p99 ≈ max(workers) + join | High (isolated contexts) | Duplicate search if brief is vague |
| Parallel + async | Anthropic’s **stated next step**; A2A parallel tasks | Lower blocking | Coordination bugs | Steerability loss; the lead cannot mid-course-correct a wave (Anthropic: current Research is **synchronous** for that reason) |
| Speculative parallel teams | M1-Parallel (arXiv:2507.08944) | **up to 2.2×** with early termination | Multiplies team cost | Need a cancellation protocol |

Openlayer’s 2026 architecture note (treat as industry commentary, not a lab result): supervisor-style parallelism helped some Google-reported parallel tasks (~**80%**) and **hurt** sequential reasoning (~**70%**). The directional claim matches Anthropic: **do not multi-agent a tightly coupled chain**.

### 1.6 Delegation: OpenAI handoffs, assignment, authority, escalation, human handoff

**Two official OpenAI SDK patterns (Python + JS, 2025–2026 docs).**

| Pattern | Primitive | Who owns the next user-visible token | Guardrails | Use |
| --- | --- | --- | --- | --- |
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool name `transfer_to_<agent>` | Specialist | Input guardrails = **first** agent only; output = **last** agent only | Conversation ownership changes (refund vs FAQ) |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Nested run; `needs_approval` supported on `as_tool` | Bounded subtask; manager synthesizes |

`handoff()` knobs: `tool_name_override`, `tool_description_override`, `on_handoff` (side effects at the instant of transfer — log, prefetch), `input_type` (Pydantic metadata: `reason`, `priority` — **does not** choose destination and **does not** replace the next agent’s input), `input_filter` / `RunConfig.handoff_input_filter`, `is_enabled` (predicate), `nest_handoff_history` (opt-in beta compaction). Helper `handoff_filters.remove_all_tools` strips tool I/O so the specialist does not drown in prior function calls. Register **one handoff per destination**; a custom `Handoff` object is only for code that picks the target at invocation time. Combine: triage **hands off** to refund; refund **calls** a policy agent as a tool.

LangChain independently adopted the same word: tools that `Command(update={current_step|active_agent})`. Two implementations: (1) **single agent + middleware** (`@wrap_model_call` swaps prompt/tools — recommended default); (2) **subgraph agents** + `Command.PARENT` — you **must** pass the triggering `AIMessage` **and** a `ToolMessage` with matching `tool_call_id` or the next model sees a malformed transcript.

**Task assignment (not the same as handoff).** Orchestrator writes a **brief** (Anthropic: objective, format, tools, boundary). Magentic-One assigns **one** worker per inner-loop step (not a swarm). CrewAI manager allocates unassigned tasks. A2A assignment is `SendMessage` creating a `Task` on a remote Agent Card skill. The brief is the **contract**; without it you get duplicated search (Anthropic) or the manager doing the work itself (CrewAI bugs).

**Authority.** Three layers that must not collapse:

1. **Routing authority** — who may be next (`handoffs` list, A2A skill, supervisor tool list).
2. **Tool authority** — which MCP/tools that worker may call (per-agent allowlist; skill-level A2A `securityRequirements`).
3. **Principal authority** — on whose behalf (user OAuth vs agent service account). MCP **MUST NOT** passthrough the client’s token to a downstream API; exchange for a correctly audenced token. A worker that inherits the supervisor’s cookie is a confused deputy waiting to happen.

**Escalation.** OpenAI `input_type=EscalationData(reason=...)` + `on_handoff` for an audit row **before** the escalation agent speaks. A2A: `TASK_STATE_AUTH_REQUIRED` / `INPUT_REQUIRED` as protocol-level interrupts. OWASP AISVS C9: privileged/irreversible actions block until human approval; approval timeout → **block**, not proceed; swarm-level kill-switch; per-execution budgets (recursion, tokens, $). Classification of reversibility must live in the **tool manifest**, not in the agent’s self-description (AISVS 9.2.6/9.2.7: worst-case governs across a multi-step chain).

**Human handoff.** Distinct from agent handoff:

| Mechanism | Pause | Resume | Durable wait? |
| --- | --- | --- | --- |
| LangGraph `interrupt()` / `interrupt_before` | GraphInterrupt | `Command(resume=…)` + checkpointer | Only if Agent Server / Temporal, not a laptop `invoke()` |
| OpenAI `needs_approval` on tools / `as_tool` / Hosted MCP | `result.interruptions` | `state.approve()/reject()` + same session | Process-held unless you persist `RunState` yourself |
| A2A `INPUT_REQUIRED` | Task interrupted | Client `SendMessage` on same `taskId` | Yes, by spec |
| Temporal Signals/Updates | Workflow parks (zero compute) | Signal | Yes |
| CrewAI / AG2 `manual` speaker | Console | Human types | No |

OWASP Top 10 for Agentic Applications 2026 **ASI09** (Human-Agent Trust Exploitation): HITL is an **attack surface** (automation bias, authority deference, confirmation fatigue). Mitigations: friction-by-design for irreversible actions, approval-budget per session, structured risk badges, out-of-band confirm. EU AI Act Art. 14 (high-risk oversight) is the legal twin; it does not specify the UI.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published multipliers (do not invent others)

| Source | Claim | Caveat |
| --- | --- | --- |
| Anthropic Research | Chat → agent **~4×** tokens; chat → multi-agent **~15×**; token use explains **80%** of BrowseComp variance; **+90.2%** vs single Opus 4 on **internal** research eval; parallelization **≤90%** wall-clock cut; better MCP tool descriptions **−40%** completion time | Internal eval, not a public leaderboard; 2025 Opus 4 / Sonnet 4 generation |
| LangChain multi-agent docs | Call/token table in §1.3 (4 vs 3 calls; 9K vs 14K vs 15K) | Pedagogical “buy coffee” / 2k-doc specialists — not your prod mix |
| Magentic-One | GAIA **38%**, WebArena **32.8%**, AssistantBench **27.7%**; ledger ablation **−31%**; worker ablation **−21..−39%** | GPT-4o / o1-preview, 2024 runs |
| M1-Parallel | **≤2.2×** latency via parallel teams + early stop | Multiplies **cost** unless cancelled |
| Data-science blackboard | **~1.8–2.3×** $ vs master–slave/RAG; **no** latency win in that paper | One domain |
| Anthropic Managed Agents | **$0.08 / session-hour** active runtime **plus** token SKUs; web search **$10 / 1K searches**; code-exec extra **$0.05 / container-hour** after 50 free org-hours/day | Platform SKU, not OSS |

⚠️ **p50/p95/p99:** no vendor publishes agent-loop latency percentiles for supervisor-worker systems as of 2026-08-21. Bound them from architecture: sequential p99 ≈ Σ stage p99; parallel-sync p99 ≈ max(worker p99)+join+lead; A2A blocking calls inherit callee p99; Temporal waiting for HITL is **not** a latency SLO (parked workflows consume no worker CPU).

### 2.2 Official token SKUs used below (2026-08-21)

Claude API ([platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing)):

| Model | Input | Output | Cache hit |
| --- | --- | --- | --- |
| Sonnet 5 | **$2 / MTok** | **$10 / MTok** | **$0.20 / MTok** |
| Opus 5 | **$5 / MTok** | **$25 / MTok** | **$0.50 / MTok** |
| Haiku 4.5 | **$1 / MTok** | **$5 / MTok** | **$0.10 / MTok** |
| Fable 5 | **$10 / MTok** | **$50 / MTok** | **$1 / MTok** |

OpenAI API list price (openai.com/api/pricing, GPT-5.6 family): Sol **$5 / $30**, Terra **$2 / $12**, Luna **$0.20 / $1.20** per MTok (cached input 10% of input). Fast mode / long-context bands exist; do not use them in the base inference. US-only Claude inference is **1.1×**; Opus 5 fast mode is **2×** standard.

### 2.3 `$ per 1k tasks` — explicit reference loops **[inferred]**

All figures are **model tokens only** (no web-search SKU, no Managed Agents session-hour, no LangSmith seat).

**Loop A — LangChain one-shot “buy coffee”.** Assume **2,000 input + 400 output tokens per model call** (short tool-using turn). Sonnet 5: \(2000\times\$2 + 400\times\$10\) per 1M = **$0.008 / call**.

| Pattern | Calls | $/task | **$/1k tasks** |
| --- | --- | --- | --- |
| Handoffs / Skills / Router | 3 | $0.024 | **$24** |
| Subagents (extra join through main) | 4 | $0.032 | **$32** |
| Same, GPT-5.6 Terra ($2/$12) | 3 | $0.0088 | **$9** |
| Same, GPT-5.6 Sol ($5/$30) | 3 | $0.022 | **$22** |

Repeat-request (turn 2): handoffs **2** calls → **$16 / 1k extra**; subagents still **4** → **$32 / 1k extra**. Coordination tax of “always return to supervisor” is **+$8 / 1k / turn** on this loop **[inferred]**.

**Loop B — LangChain multi-domain 9K vs 14K vs 15K tokens.** Split **70% input / 30% output** **[inferred]**.

| Pattern | Tokens | Sonnet 5 $/task | **$/1k** |
| --- | --- | --- | --- |
| Subagents / Router (~9K) | 6.3K in + 2.7K out | $0.0396 | **$40** |
| Handoffs (~14K, sequential) | 9.8K + 4.2K | $0.0616 | **$62** |
| Skills (~15K accumulated) | 10.5K + 4.5K | $0.066 | **$66** |

Handoffs’ sequential inability to research three domains in parallel is a **~$22 / 1k** tax vs subagents on this pedagogical workload **[inferred]** — before counting extra latency.

**Loop C — Anthropic 15× research.** Chat baseline **2,000 in + 500 out** on Sonnet 5 = **$0.009 / chat**. Single-agent research **4×** = **$0.036** → **$36 / 1k**. Multi-agent **15×** = **$0.135** → **$135 / 1k**. Mix **30% Opus 5 + 70% Sonnet 5** on the 15× token pile (lead vs subs) **[inferred]**: blended ≈ \(0.3\times(15\times 2000\times\$5 + 15\times 500\times\$25)+0.7\times(15\times 2000\times\$2 + 15\times 500\times\$10)\) / 1e6 ≈ **$0.24 / task** → **~$240 / 1k**. Anthropic’s own viability rule: the **task value must exceed this**; they do not publish a break-even.

**Loop D — fan-out catastrophe.** Early Anthropic failure: **50** subagents on a simple query. If each sub does **10** calls like Loop A: \(50\times 10\times \$0.008 = \$4 / task\) → **$4,000 / 1k** plus the lead. This is why AISVS 9.1.2 (per-execution token/$ budgets) is an NFR, not a nice-to-have.

**Web search add-on:** Claude web search **$10 / 1K searches**. A research wave of 3 subs × 8 searches = 24 searches → **$0.24 / task** — often **larger than Sonnet tokens** on Loop A. Count it.

### 2.4 Coordination overhead (what you actually pay extra for)

1. **Router/supervisor tokens** — 1 extra call per hop (Loop A: +33% calls vs a sticky specialist).
2. **History splicing** — `full_history` vs `last_message`; missing `input_filter` copies every tool payload into the next agent.
3. **Duplicate work** — vague briefs (Anthropic semiconductor example).
4. **Join/synthesize** — CitationAgent, Magentic final-answer prompt, LangChain synthesizer.
5. **Retries** — Temporal retry on 429 without a circuit breaker stampeding the provider (see §3).
6. **Protocol wrappers** — A2A Agent Card fetch, OAuth, streaming heartbeats: usually << LLM $ but dominate **p99** if the callee is cold.

Cache: supervisor system prompt + worker playbooks should be **prompt-cached**. Sonnet 5 cache hit **$0.20 / MTok** vs **$2** is a **10×** input discount for the static prefix. Hierarchical supervisors with shared team prompts are the best cache shape; swarms that rewrite `active_agent` prompts every hop cache worse.

---

## 3. Distributed Resilience & State

### 3.1 Durable execution is the control plane

Anthropic production notes: agents are **stateful**; a mid-loop crash cannot “just restart” (too expensive, user-visible). They combine model-driven adaptation (“the search tool is failing, try another”) with **retries + checkpoints**, and **rainbow deploys** so in-flight agents are not cut over mid-plan.

Temporal mapping (Agent Harness 2026; OpenAI Agents SDK sandbox example; AI reference architecture):

| Agent concept | Temporal | Why |
| --- | --- | --- |
| Lead loop / supervisor graph | **Workflow** (deterministic) | Replay from Event History; idle HITL = **zero compute** |
| LLM call, MCP, A2A, browser | **Activity** | Recorded once; replay must **not** re-call the LLM (determinism) |
| Human approval | Signal / Update + `wait_condition` | Durable wait |
| Long transcript | **Continue-As-New** | Unbounded history will kill replay |
| Handoff / tool / approval events | Agent Harness `AgentEvent` stream | One audit spine |

OpenAI+Temporal demo: `AgentWorkflow` wraps `SandboxAgent`; `Runner.run()` unchanged; sandbox + model calls become Activities; **fork** snapshots workspace + history into a new workflow.

LangGraph: checkpoints at **super-step** boundaries. After `interrupt()`, the **whole node restarts** — side effects before the pause re-run unless wrapped in Functional API `task`s. This is a footgun for “send email then interrupt for approval.”

### 3.2 Saga (compensating transactions) for worker side effects

Temporal saga docs: register **compensation before** the forward Activity (so a lost response still rolls back); compensations **LIFO**; all compensations **idempotent** (may run when forward never committed). Agent translation:

| Forward worker action | Compensation | Irreversible? |
| --- | --- | --- |
| Create CRM record | Archive / delete | Usually reversible |
| Charge card | Refund | Partial; money movement has its own saga |
| Send customer email | Apology email | **Cannot unsend** — compensation is an apology (Garcia-Molina 1987 + Fowler) |
| A2A `COMPLETED` artifact published | New refinement task, not mutate old task | Spec: tasks immutable |
| MCP `tools/call` with write | Compensating tool | Must be in the **worker’s** allowlist, not the lead’s |

Do **not** ask the LLM to invent compensations at failure time. Put them in the **workflow**, keyed by `workflow_id`. If compensation itself fails non-retryably, park `ROLLBACK_PENDING_FIX` for a human — AISVS C9.6.

### 3.3 Locking and shared-state races

| Shared resource | Race | Mitigation |
| --- | --- | --- |
| LangGraph channel without reducer | Last-write-wins on parallel `Send` | `operator.add` / custom reducer; per-worker private state |
| Blackboard document | Lost update / conflicting patches | Single writer per cycle, or CRDT/version + conflict-resolver agent (LbMAS) |
| `active_agent` in a swarm | Two handoff tools in one parallel tool-call batch | Disable parallel tool calls on swarms; or last-handoff-wins is defined |
| A2A artifact name | Two parallel refinements | Client owns version history; pass explicit `artifactId` |
| CrewAI shared memory | Stale task context | Treat hierarchical output as **immutable messages**, not a mutable global |
| MCP session vs graph checkpoint | `/mcp` stateless per request (LangGraph Agent Server) | Memory in checkpointer/store, **not** MCP session |

Optimistic concurrency: store `checkpoint_id` / A2A task etag; reject stale writes.

### 3.4 Circuit breakers (Temporal has none)

Nygard/Fowler breaker: closed → open on failure rate → half-open probe. Temporal **RetryPolicy** is **not** a breaker: hundreds of workflows retrying 429s **amplify** the outage. Pattern: workflow-level consecutive-failure counter per **provider/tool**; open → fail fast / fallback model / skip that worker; cooldown; one probe. Classify:

| Error | Retry Activity? | Open breaker? |
| --- | --- | --- |
| 429, 500, 503, timeout | Yes, exponential backoff, high max interval | If consecutive across executions |
| 400, 401, 422, content policy | **No** (wastes $) | No (logic bug) |
| Worker exception “coworker not found” | No | Page the control plane |

AISVS 9.1.1: per-tool quotas and timeouts. 9.1.3: swarm kill-switch.

### 3.5 Isolation of worker failure

| Failure | Supervisor-worker (sync wave) | Handoff swarm | A2A remote | Hierarchical team |
| --- | --- | --- | --- | --- |
| One worker 500s | Whole wave blocks (Anthropic’s stated bottleneck) | Conversation stuck on that agent | Task `FAILED`; context continues with new task | Other teams proceed if top-level does not join |
| Infinite tool loop | `max_turns` / AISVS budget | Same | Server-side timeout | Team-level `max_turns` |
| Poisoned context | Isolated if sub has own window (Anthropic win) | **Contaminates** sticky history | Opaque — callee’s problem; you see artifacts | Team checkpointer isolates |
| Kill one worker identity | Remaining workers + replan | Need a handoff off the dead agent | New Agent Card version | Replace compiled subgraph |

Anthropic: tell the model the tool is failing — it adapts. That is **necessary and insufficient**; pair with Activity retries and a breaker so the lead is not spending Opus tokens narrating a dead search API.

### 3.6 A2A as distributed state machine

Treat remote agents as **sagas with a public state enum**. `AUTH_REQUIRED` is not an error; it is an interrupt. Push-notification capability must be declared or the client gets `PushNotificationNotSupportedError`. List-tasks **must** be authorization-scoped (spec §13.1). Do not implement your own “restart completed task”; the spec forbids it — spawn a refinement.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP (tool plane)

MCP 2025-06-18 onward: server is an **OAuth 2.1 resource server**, not a token issuer. 2026-07-28 SEPs harden clients (e.g. SEP-2468 `iss` on the authorization response — mix-up defense; RFC 9207). Non-negotiable controls from MCP Security Best Practices:

1. **Audience binding (RFC 8707 Resource Indicators)** — token is for **this** MCP server.
2. **No token passthrough** — never forward the client token downstream; **token exchange**.
3. **Per-client consent** on MCP **proxies** that use a **static** third-party `client_id` plus Dynamic Client Registration plus IdP **consent cookies** — the textbook **confused deputy** (see §4.4).
4. `state` stored **only after** MCP-side consent; not before redirect.

Zero-trust for agents: **never** a long-lived shared API key in the supervisor that all workers reuse. Short-lived, per-agent, per-session credentials (Microsoft Agent 365 / Teleport-style X.509 commentary; A2A mTLS scheme). Least privilege on **every** MCP `tools/call`.

### 4.2 Per-agent RBAC

Map topology → identity:

| Principal | May | Must not |
| --- | --- | --- |
| Router / lead | Spawn workers, read summaries, write plan Memory | Hold production write tools (Stripe, email send) |
| Domain specialist | Its tool allowlist | Other specialists’ tools; raw user refresh tokens |
| Citation / critic | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail (ASI09) |
| A2A callee | Skills on its Agent Card | Your VPC except via published artifacts |

CrewAI/LangGraph “give the manager all tools so it can help” **destroys** isolation. Hierarchical IAM: team supervisor has **delegation** rights, not **union of worker tools**.

A2A skill-level `securityRequirements` is the protocol’s RBAC hook — use it. Extended Agent Cards hide sensitive skills until authenticated.

### 4.3 PII and context leakage

Every extra hop is a **copy**. Subagents with isolated windows are **better** for PII minimization if the brief strips identifiers and the sub returns aggregates. Handoffs that pass **full history** (OpenAI default) leak prior-turn PII into the refund agent. Filters: `input_filter`, LangChain “pass only the handoff pair,” Anthropic filesystem refs.

Blackboards are **worse**: the public board is a PII lake unless you partition (LbMAS private spaces for debate). A2A artifacts may be files — classify before crossing org boundaries. Prompt/trace backends (LangSmith, OpenAI traces): `trace_include_sensitive_data` gates; Anthropic production tracing of **decision patterns without conversation contents**.

Retention: Enterprise Claude lists audit logs, custom retention, HIPAA-ready SKU — that is the **chat** product; your **agent runtime** still needs its own DPA-covered store for checkpoints.

### 4.4 Confused deputy (two layers)

**OAuth proxy deputy (MCP spec).** Conditions: static IdP `client_id` + DCR + consent cookie + no per-MCP-client consent. Attacker registers `redirect_uri=attacker.com`, rides the cookie, skips consent, steals a code. Fix: per-client consent, exact redirect match, single-use `state` after consent.

**Agent deputy (multi-agent specific).** Supervisor has GitHub admin. User asks a worker to “update the README.” Worker issues a tool call that the supervisor **executes with supervisor credentials**. The worker is the confused deputy **or** the supervisor is, depending on who holds the token. Fix: **downscope at handoff** — `on_handoff` mints a token whose audience is the worker’s MCP servers and whose scopes match the brief. A2A `AUTH_REQUIRED` when the callee needs a user gesture. Never “the lead calls all tools on behalf of workers.”

Token passthrough is the same bug in both layers.

### 4.5 Audit of delegation

Minimum viable audit row (append-only, hash-chained if you need ASI10/AGT-style tamper evidence):

`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate (none|pending|approved|rejected), artifact_ids`

OpenAI: `handoff` spans in the default tracer; `on_handoff` for business metadata. LangSmith: graph node + tool spans. A2A: `taskId`+`contextId`+status transitions. Temporal: Event History **is** the audit. AG2 Network: Hub WAL.

Handoffs that **filter history** must log **what was dropped** (hash of omitted items), or incident response cannot reconstruct why the specialist lacked context.

### 4.6 OWASP Agentic Top 10 (2026) mapped to this topic

Official list: [OWASP Top 10 for Agentic Applications for 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/). Microsoft’s Agent Governance Toolkit mapping (ASI01–ASI10): Goal Hijack, Tool Misuse, Identity/Privilege Abuse, Supply Chain, Unexpected Code Execution, Memory Poisoning, **Insecure Inter-Agent Communication (ASI07)**, **Cascading Agent Failures (ASI08)**, Human-Agent Trust (ASI09), Rogue Agents (ASI10). Multi-agent systems concentrate **ASI07** (A2A/MCP without mTLS/audience), **ASI08** (fan-out, ping-pong, retry storms), **ASI03** (delegation without downscope). AISVS C9 is the control catalog: budgets, kill-switch, approval manifests, timeout-deny, out-of-band kill.

---

## 5. Production Failure Modes

### 5.1 Supervisor bottleneck

**Symptoms:** p99 ≈ lead think time + max(slowest worker); lead context fills with summaries; cannot steer in-flight subs (Anthropic: **synchronous** waves). Hierarchical: top-level waits on entire teams.

**Causes:** `parallel_tool_calls=False` (default) serializes workers; lead model oversized for routing; `output_mode=full_history`; CitationAgent on the critical path for every query.

**Mitigations:** effort-scaling in the prompt (1 vs 2–4 vs >10); Haiku/Luna **router** + Opus **lead** only when complexity score fires; `last_message` + filesystem artifacts; A2A/Temporal **async** tasks with progress; Magentic inner loop assigns **one** worker — slower but bounded; split citation to async post-process.

### 5.2 Ping-pong handoffs

**Symptoms:** `transfer_to_sales` ↔ `transfer_to_support`; hop count explodes; user sees “let me transfer you” loops; token burn without a final `AIMessage`.

**Causes:** overlapping prompts; reciprocal handoff tools always enabled; no `max_turns`; swarm without hop cap; CrewAI manager delegating to itself.

**Mitigations:** `is_enabled` predicates; hop counter in state; after **N** transfers, force `escalate_to_human`; OpenAI `max_turns=10`; Magentic `max_stalls=3` then replan (not re-handoff); allowed-transition graph (AG2 Classic `allowed_or_disallowed_speaker_transitions`); disable parallel tool calls so two handoffs cannot fire in one tick.

### 5.3 Shared-state races

Covered in §3.3. Production signature: **non-reproducible** “wrong specialist answered” after a parallel supervisor tick; duplicate CRM records from two workers; A2A client using a stale artifact because two refinements shared a name.

**Mitigations:** reducers; single-writer blackboard cycles; idempotency keys = `workflow_id + step`; A2A client version map; **no** global mutable memory as the collaboration bus.

### 5.4 Cascading fan-out cost

**Symptoms:** $ per task jumps 10–50× (Loop D); 429 storms; provider breaker never opens because each workflow’s RetryPolicy looks locally reasonable.

**Causes:** lead without effort cap; `Send` over an unbounded list; M1-Parallel without cancellation; nested supervisors each fan out; MCP tool that itself spawns agents.

**Mitigations:** hard caps (Anthropic: simple queries **must not** spawn 10+); AISVS 9.1.2 monetary budget in the **runtime**, not the prompt (prompts are advisory); circuit breaker **per provider** across the fleet; admission control on `Send` length; treat “agent as tool that can spawn agents” as **recursive agency** — depth limit 1 unless productized.

### 5.5 Other production modes (seen in named sources)

| Mode | Source | Detection | Fix |
| --- | --- | --- | --- |
| 50 subs on a trivia question | Anthropic | Subagent-count metric | Effort rules + hard cap |
| Vague briefs → duplicate search | Anthropic | Overlap of query embeddings across subs | Brief template: objective, sources, **out of scope** |
| Telephone game through the lead | Anthropic appendix | Artifact hash ≠ cited content | Filesystem refs + CitationAgent |
| SEO-farm sources | Anthropic human eval | Source-quality rubric | Prompt heuristics + judge |
| Rainbow-unsafe deploys | Anthropic | In-flight graph schema mismatch | Dual-run old/new; pin prompt versions on the thread |
| o1/policy refusals shrink coverage | Magentic-One WebArena | Refusal rate by site | Don’t put the policy-heavy model on write tools |
| Hierarchical “manager does all work” | CrewAI #4783 | `task.delegations==0` | Fix coworker injection; don’t trust YAML manager |
| GroupChat broadcast cost | AG2 Classic | Tokens ∝ N² | Switch to Network channels / supervisor |
| Guardrail gap on handoffs | OpenAI docs | Bypass via transfer | Tool guardrails don’t wrap handoffs/`as_tool` hosted tools — add policy at the **worker** |
| ASI09 rubber-stamp HITL | OWASP | Approval time <1s, high volume | Approval budgets, friction, structured diffs |

---

## 6. Enterprise System Design Scenarios

Decision rule used below: **start with one agent + skills**. Add a second agent only when (a) tool/policy isolation is a compliance requirement, (b) parallel isolated context is the product, or (c) two teams ship independently. OpenAI, LangChain, and Anthropic independently say the same thing.

### 6.1 Trade-off matrix — topology vs requirement

| Requirement | Single + Skills | Router + parallel | Supervisor-worker | Hierarchical | Swarm/handoff | A2A mesh | Blackboard |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Lowest $ (Loop A) | **Best** | Good | Extra join call | Worst | Good on repeats | Protocol tax | High $ (paper) |
| Parallel breadth research | Weak (context pile-up) | **Best** | **Best** if `parallel_tool_calls` | OK per team | Poor (sequential) | Good (parallel tasks) | Sequential cycles |
| Sticky UX (support) | Good | Re-routes every turn | User talks to lead | Heavy | **Best** | `contextId` sticky | Poor |
| Team autonomy / IAM | Weak | Medium | Strong | **Strongest** | Medium | **Strongest** (opaque) | Weak unless partitioned |
| Cross-company | No | No | No | No | No | **Yes** | No |
| Auditability of delegation | Skill load events | Router span | Handoff tools | Nested traces | `active_agent` log | Task SM | Board diffs |
| Sequential reasoning / coding | **Best** (Anthropic) | Harmful if oversplit | Harmful if oversplit | Harmful | Harmful | Harmful | Mixed |
| HITL high-impact | Middleware interrupt | After join | Lead gate | Top-level gate | Easy to skip | `INPUT_REQUIRED` | Controller gate |

### 6.2 Scenario A — Internal IT helpdesk (sticky, policy isolation)

**Choose:** OpenAI-style **triage handoff** to billing/refund/FAQ specialists; **not** a research orchestrator. `handoffDescription` one sentence each. `input_filter=remove_all_tools`. `is_enabled` hides refund unless `order_id` in state. Human: `needs_approval` on refund **write** tools; ASI09 friction (amount in a structured card, not chat prose). NFR: hop cap 3 → human. Cost: Loop A handoff **~$24 / 1k** Sonnet 5 **[inferred]**; p95 dominated by the specialist, not triage. ⚠️ Measure your own p95.

**Avoid:** GroupChat of 8 personas; CrewAI hierarchical until delegation telemetry is green; spawning web-research subs for “where is my laptop.”

### 6.3 Scenario B — Competitive research / due diligence (breadth)

**Choose:** Anthropic-shaped **orchestrator-worker**: Opus (or Sol) lead, Sonnet/Terra/Haiku subs, Memory plan, filesystem artifacts, CitationAgent, **hard** subagent cap, effort rules. Parallel wave of 3–5. Token budget runtime-enforced (Loop C **~$135–240 / 1k** before search SKUs). Web search SKU can exceed tokens — cap searches. Eval: LLM-as-judge on factuality/citation/completeness (Anthropic: one judge 0–1 beat multi-judge). Deploy: rainbow + tracing of **structures** not contents.

**Avoid:** Handoff swarm (cannot parallelize domains — LangChain 14K+ sequential). Skills-only (15K context sludge). Unbounded `Send`.

### 6.4 Scenario C — Multi-org supply chain (opaque callees)

**Choose:** **A2A 1.0** between company agents; **MCP** inside each company. Publish Agent Cards with mTLS or OAuth2; skill-level security; signed cards. Client owns artifact versions. Tasks immutable; refinements = new `taskId`. Control plane: Temporal saga around “book + pay + notify” with compensations. Human on `AUTH_REQUIRED` and on money movement. Microsoft Learn: do not reimplement the partner’s agent as MCP tools if you need their **orchestration opacity**.

**Avoid:** Sharing a blackboard across legal entities (PII + IP). Passing your IdP token through their MCP.

### 6.5 Scenario D — Platform team offering “agents as a product”

**Choose:** Hierarchical **only** at the **team** boundary (research platform vs writing platform), each a compiled graph with its own checkpointer, IAM, and SLO. Top-level supervisor is a **thin router** (Haiku/Luna), not Opus. Expose **A2A** and/or MCP (`/mcp` stateless — memory in the graph). MAF 1.0 if .NET/Python enterprise standardization + Magentic for ad-hoc internal goals. AG2 Network if you are already on AG2 and need WAL/audit in the Hub.

**NFR contract to publish:** max workers, max hops, max $ / task, p99 of **your** control plane (not the LLM), kill-switch, data residency of checkpoints.

### 6.6 Scenario E — When **not** to multi-agent (write this on the whiteboard)

- Tight sequential coding / refactor (Anthropic: agents not yet good at live delegation; single agent + skills + tests).
- <10 tools in one domain (LangChain: tool overload is the trigger; below that, split is net loss).
- Task value < Loop C cost (research-style 15×).
- You cannot name the **principal** for each worker’s writes.
- You cannot cap fan-out in **code**.

### 6.7 Interview control-plane checklist

1. Who owns the user-visible token after hop 1? (handoff vs as_tool vs supervisor join)
2. Where is the hop cap / $ cap enforced? (runtime > prompt)
3. What identity is on the wire for worker writes? (downscoped token)
4. What is the compensation for the last side-effecting tool?
5. What is logged on delegation (including filtered history hashes)?
6. How does a dead worker fail **closed** without killing the saga?
7. MCP vs A2A: which bus is this hop on?
8. HITL: timeout-deny, approval budget, ASI09 friction?
9. Parallelism: sync join or async tasks — and can the lead steer?
10. Deploy: can an in-flight graph survive a prompt change (rainbow / pin)?

If the candidate cannot answer (3), (4), and (6), they have a demo, not a system.

---

## Sources

1. https://docs.langchain.com/oss/python/langchain/multi-agent
2. https://docs.langchain.com/oss/python/langchain/supervisor
3. https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
4. https://docs.langchain.com/oss/python/langchain/multi-agent/skills
5. https://docs.langchain.com/oss/python/langchain/multi-agent/router
6. https://docs.langchain.com/oss/python/langgraph/graph-api
7. https://docs.langchain.com/oss/python/langgraph/use-graph-api
8. https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor
9. https://github.com/langchain-ai/langgraph-supervisor-py
10. https://github.com/langchain-ai/langgraph-swarm-py
11. https://langchain-ai.github.io/langgraphjs/reference/modules/langgraph-supervisor.html
12. https://github.com/langchain-ai/langgraphjs/blob/86389fa3/docs/docs/concepts/multi_agent.md
13. https://openai.github.io/openai-agents-python/handoffs/
14. https://openai.github.io/openai-agents-python/tools/
15. https://openai.github.io/openai-agents-js/guides/handoffs/
16. https://developers.openai.com/api/docs/guides/agents/orchestration
17. https://github.com/openai/openai-agents-python/blob/cae28f06/docs/multi_agent.md
18. https://openai.com/api/pricing/
19. https://a2a-protocol.org/latest/
20. https://a2a-protocol.org/v1.0.0/specification/
21. https://a2a-protocol.org/latest/topics/life-of-a-task/
22. https://github.com/a2aproject/A2A
23. https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade
24. https://www.anthropic.com/engineering/multi-agent-research-system
25. https://platform.claude.com/docs/en/about-claude/pricing
26. https://www.anthropic.com/pricing
27. https://docs.crewai.com/en/concepts/processes
28. https://docs.crewai.com/edge/en/learn/hierarchical-process
29. https://docs.crewai.com/edge/en/concepts/crews
30. https://github.com/crewAIInc/crewAI/issues/4783
31. https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
32. https://arxiv.org/abs/2411.04468
33. https://github.com/microsoft/autogen
34. https://github.com/ag2ai/ag2
35. https://docs.ag2.ai/latest/docs/user-guide/advanced-concepts/groupchat/groupchat/
36. https://docs.ag2.ai/docs/user-guide/network/overview/
37. https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/
38. https://devblogs.microsoft.com/agent-framework/agent-frameworks-orchestration-patterns-reach-1-0/
39. https://devblogs.microsoft.com/agent-framework/a2a-v1-is-here-cross-platform-agent-communication-in-microsoft-agent-framework-for-net/
40. https://learn.microsoft.com/en-us/agent-framework/overview/
41. https://learn.microsoft.com/en-us/agents/architecture/multi-agent-patterns
42. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices
43. https://temporal.io/blog/temporal-agent-harness-durable-agent-infrastructure
44. https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk
45. https://docs.temporal.io/design-patterns/saga-pattern
46. https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture
47. https://arxiv.org/abs/2507.01701
48. https://arxiv.org/html/2510.01285v2
49. https://arxiv.org/pdf/2507.08944
50. https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
51. https://github.com/OWASP/AISVS/blob/main/1.0/en/0x10-C09-Orchestration-and-Agentic-Action.md
52. https://agentskills.io/
