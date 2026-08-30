# Module 17 — Advanced Autonomous Agents

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/17-advanced-autonomous-agents.md` (researched 2026-08-21, 63 sources). Prices are vendor list pages (Anthropic MTok, E2B per-second) or paper-reported inference spend (SWE-agent **$4** cap). `$ per 1k long-horizon tasks` is **[inferred]** from a named SKU × a stated loop shape — not a market rate. ⚠️ No vendor publishes p50/p95/p99 wall-clock SLOs for multi-hour agent jobs; latency claims are benchmark step budgets or paper medians, labeled **[inferred]** when used as SLO proxies.
**Mandatory topics**: Autonomous agents · Long-horizon tasks · Agent environments.

The unit of production is not “a chat completion with tools.” It is a **supervisor of a long-running job** sitting in front of a **pool of mutable environments**. The **control plane** (job scheduler, Temporal workflow, Managed Agents session, `maxTurns` / `maxBudgetUsd`, kill switch, env-pool allocator) decides *whether the loop may continue*. The **data plane** (VM/screenshot stream, sandbox filesystem, browser cookies, MCP session, skill library) holds *side effects that cannot be replayed as tokens*. Collapsing those planes — treating a 90-minute computer-use loop as a 30-second HTTP request, retrying `rm -rf` the way you retry a 429, or letting the model’s goal statement mutate without a checkpointed contract — is how teams get runaway spend, goal drift, and unattended destructive tools in the same incident.

**Invariant:** the **environment lease** is the unit of scheduling, not the HTTP request. If the control plane cannot stop the data plane without the model’s cooperation (Cancel + destroy lease + revoke token), it is a demo, not an overnight worker.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, frozen goal contract, spend fuse, kill switch, Temporal workflow id, MCP OAuth audience, env-pool allocator, and Continue-As-New. Data plane is **two stores that must not be mixed**: (1) **tokens** — screenshots, a11y trees, condensed conversation, skill embeddings; (2) **side effects** — VM disk, cookies, git working tree, MCP `task_id`s, purchases, emails. Persistence is **three clocks**: durable-execution (event history / SSE session / cron), token/context (compaction, prompt-cache TTL), environment (VM TTL, cookie policy, MCP task `ttl`). Tool proxies execute side effects; the model never holds IAM. Telemetry is the only place `$`, turns-to-submit, cache hit rate, env-lease hours, and `% jobs hitting maxBudgetUsd` are authoritative.

Four commercially distinct **env pools** — not one “sandbox”: gym/eval farm (Gymnasium `reset`/`step`, episode TTL), code sandbox (E2B / Daytona / Anthropic sandbox-runtime), computer-use VM (screenshot + mouse/keyboard), browser farm (Browserbase `contextId` + Stagehand). ChatGPT agent (2025-07-17) runs on **its own virtual computer** (visual browser + text browser + terminal + connectors). Anthropic splits ownership: **Messages API** (you own the loop), **Agent SDK** (Claude Code’s loop in *your* process), **Managed Agents** (Anthropic owns harness + sandbox + session log; beta `managed-agents-2026-04-01`). Temporal wraps `SandboxAgent` so LLM, sandbox lifecycle, and shell are Activities; `workflow.wait_condition` idles at **zero compute**.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (IDE / ChatGPT agent / cron Schedule / Temporal Signal / HITL Watch)   │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + principal (never sandbox IAM)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  — durable-execution clock (event history / SSE session / cron)   │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Gateway    │─▶│ Policy       │─▶│ Overnight    │─▶│ JOB SUPERVISOR        │  │
│  │ auth,quota │  │ PII detect→  │  │ fuse:        │  │ 1 Workflow per job    │  │
│  │ RPM/TPM    │  │ redact→audit │  │ maxBudgetUsd │  │ frozen goal contract  │  │
│  │ breaker    │  │ tool RBAC    │  │ env TTL      │  │ kill switch           │  │
│  │ Retry-After│  │ Watch Mode   │  │ ScheduleTo-  │  │ Continue-As-New       │  │
│  │            │  │ irreversible │  │ Close + quota│  │ env-pool allocator    │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
│                         │                 ▼                     │               │
│                         │          ┌────────────────┐           │               │
│                         │          │ Temporal /     │◀──────────┘               │
│                         │          │ Managed Agents │  Signal: HITL / cancel    │
│                         │          │ wait_condition │  Activity: LLM + tools    │
│                         │          │ = 0 worker CPU │  attempts=1 (Temporal     │
│                         │          └───────┬────────┘  owns retry)              │
└─────────────────────────┼──────────────────┼────────────────────────────────────┘
                          │                  │
                          │                  ▼
┌─────────────────────────┴───────────────────────────────────────────────────────┐
│ DATA PLANE — tokens (context clock) + side effects (environment clock)          │
│                                                                                 │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐  │
│  │ TOKEN STREAM        │  │ ENV POOLS (leased)  │  │ MODEL (untrusted        │  │
│  │ screenshot / a11y   │  │  ┌─────┐ ┌────────┐ │  │ planner only)           │  │
│  │ condensed history   │  │  │Gym  │ │Code    │ │  │ CUA / ACI / MCP tools   │  │
│  │ skill embeddings    │  │  │reset│ │sandbox │ │  │ never holds long-lived  │  │
│  │ prompt cache TTL    │  │  │step │ │E2B 1h/ │ │  │ SaaS keys               │  │
│  │ compact ≠ VM snap   │  │  │     │ │24h Pro │ │  │                         │  │
│  └──────────┬──────────┘  │  └─────┘ └────────┘ │  └────────────┬────────────┘  │
│             │             │  ┌─────┐ ┌────────┐ │               │               │
│             │             │  │CUA  │ │Browser │ │               │               │
│             │             │  │VM   │ │farm    │ │               │               │
│             │             │  │pix  │ │ctxId   │ │               │               │
│             │             │  └─────┘ └────────┘ │               │               │
│             │             └──────────┬──────────┘               │               │
└─────────────┼────────────────────────┼──────────────────────────┼───────────────┘
              │                        │                          │
              │                        ▼                          │
              │             ┌─────────────────────────────────────┤
              │             │ stop_reason = tool_use / computer_  │
              │             │ call / MCP task                     │
              ▼             ▼                                     ▼
┌─────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ TOOL PROXIES (PEP)              │   │ PERSISTENCE                              │
│  never hold IAM / GPU           │   │                                          │
│  ┌──────────┐  ┌─────────────┐  │   │  ┌──────────────┐  ┌──────────────────┐  │
│  │ MCP PEP  │─▶│ Executor    │──┼──▶│  │ HARD         │  │ STICKY / SOFT    │  │
│  │ OAuth2.1 │  │ ACI / bash  │  │   │  │ Temporal     │  │ prompt cache     │  │
│  │ RFC 8707 │  │ Playwright  │  │   │  │ history;     │  │ screenshot log   │  │
│  │ audience │  │ computer    │  │   │  │ Kafka outbox │  │ (compact on CAN) │  │
│  │ task_id  │  │ 17-tool set │  │   │  │ MCP task ttl │  │ env snapshot     │  │
│  └──────────┘  │ idempotency │  │   │  │ goal artifact│  │ cookies/contextId│  │
│                └─────────────┘  │   │  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────┘   └──────────────────────┬───────────────────┘
                                                             │
┌────────────────────────────────────────────────────────────┴───────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐ │
│  │ Audit WORM  │  │ Metrics      │  │ Trace spans │  │ Usage (authoritative) │ │
│  │ workflow_id │  │ $/success vs │  │ gw→supervisor│ │ turns, cached_tokens, │ │
│  │ run_id,     │  │ $/fail,      │  │ →LLM→proxy  │  │ env-lease hours,      │ │
│  │ principal,  │  │ cache hit %, │  │ →env lease  │  │ % maxBudgetUsd,       │ │
│  │ task_id,    │  │ Continue-As- │  │             │  │ % CAN, binary vs      │ │
│  │ sandbox_id, │  │ New rate     │  │             │  │ partial credit        │ │
│  │ tool hash   │  │ breaker state│  │             │  │                       │ │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Two planes, three clocks, one lease

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | Job supervisor, Temporal workflow, Claude Managed Agents session, `maxTurns` / `maxBudgetUsd`, kill switch, env-pool allocator | Durable-execution (event history; SSE session; cron) | Temporal persistence / Managed Agents event log / orchestrator DB | HTTP timeout killing a 3 h job; KEDA scaling away the worker holding the VM lease |
| **Data (tokens)** | Screenshots, a11y trees, tool results, condensed conversation, skill embeddings | Token/context (compaction, prompt-cache TTL) | Model context + cache | Replaying a 200-screenshot history as a fresh prompt without compaction → context blow-up |
| **Data (side effects)** | VM disk, browser cookies, git working tree, MCP task IDs, purchases, emails | Environment (VM TTL, cookie policy, MCP task `ttl`) | Sandbox / browser farm / production SaaS | Retrying the *workflow* re-clicks “Place order” because the LLM call was retried |

**Pool types (observation / action / isolation / TTL):**

| Pool | Observation | Action | Isolation | Typical TTL |
| --- | --- | --- | --- | --- |
| **Gym / eval farm** | Gymnasium `reset`/`step`; BrowserGym DOM+a11y+screenshot | Discrete / browser primitives | Docker per episode | Episode (minutes) |
| **Code sandbox** | Files + stdout | bash / Python | E2B, Daytona, Modal, Runloop, Anthropic sandbox-runtime | 1 h Hobby / **24 h Pro** on E2B |
| **Computer-use VM** | Screenshot (+ optional a11y) | Mouse/keyboard / 17-tool computer toolset | Xvfb + owned desktop, or vendor VM | Session; ChatGPT agent cookies persist per site policy |
| **Browser farm** | DOM / Stagehand `observe` | Click/type or CUA pixels | Browserbase hosted SHTTP, Steel, Playwright grid | Keep-alive session + `contextId` |

Gymnasium is the RL contract (`terminated` vs `truncated`) that BrowserGym inherits for MiniWoB++, WebArena, VisualWebArena, WorkArena, AssistantBench. OSWorld is a **real OS** (Ubuntu/Windows/macOS), **369** tasks, execution-based graders — humans **72.36%**, 2024 best agent **12.24%**. OSWorld 2.0 is a *different* protocol: **108** long-horizon workflows, human median **~1.6 h**, **~318** tool calls with Claude Opus 4.7 max-thinking vs **~30** in OSWorld 1.0. Mixing OSWorld-Verified scores with OSWorld 2.0 scores is a methodology error the maintainers flag explicitly.

**Four overnight topologies — do not conflate:**

| Pattern | Who starts it | Who must stay awake | Durable wait | Example |
| --- | --- | --- | --- | --- |
| **Interactive computer use** | User in session | Desktop/CLI (Claude Code: no `-p`; Cowork desktop must be awake for Dispatch) | Process RAM | Phone-dispatch is **not** a worker unless the host is up |
| **Scheduled consumer agent** | Cron / “repeat this task” | Vendor VM | Vendor session | ChatGPT agent weekly metrics; Managed Agents scheduled deployments |
| **Durable workflow + sandbox** | Temporal Schedule / signal | Worker fleet (scale to zero while waiting) | Event history; `wait_condition` = **0** activity CPU | OpenAI Agents SDK + E2B/Daytona/Docker |
| **Eval episode** | Harness `reset()` | Gym node until `terminated`/`truncated` | None (by design) | OSWorld / WebArena / TheAgentCompany |

Self-starting in the AutoGPT sense (agent proposes the next goal) is **curriculum**, not orchestration. Voyager’s automatic curriculum is a GPT-4 loop (temperature **0.1**) for diversity; production analog is a **ticket queue**. If the overnight job can mint its own tickets, you have unbounded spend *and* unbounded scope.

### 1.3 End-to-end request flow

1. **Ingress.** Client is a human session, Temporal Schedule, Managed Agents cron, or gym `reset()`. Gateway stamps `correlation_id`, authenticates the **principal** (Temporal Principal Attribution is the audit join key), checks vendor message quota (ChatGPT agent Pro **400**/mo, other paid **40**) and your own RPM/TPM. A closed circuit breaker on the primary model is already a routing input.
2. **Policy.** Control plane redacts PII **before** tokenize and **before** Temporal payload. Screenshots of mail/EHR/HRIS **are** PII/PHI. Tool RBAC binds `(user, agent_id, tool_name, resource_indicator, time_window, spend_cap)`. Irreversible tools (wire, prod `DROP`, public social, purchase) require Watch Mode / confirmation; deny-on-timeout is the default.
3. **Admit supervisor.** Allocate **one Workflow per job** with a frozen goal artifact. Do not put the LLM call inside the Workflow function (breaks determinism). Do not put `rm` inside an Activity without idempotency and a confirm Signal. Workflow id = `tenant:job`. `Idempotency-Key` on start.
4. **Allocate env lease.** Pool allocator picks gym / code sandbox / CUA VM / browser farm. Lease carries `sandbox_id` (or `contextId`), **generation**, TTL (E2B Hobby **1 h** will kill an 8 h job; Pro **24 h**). Warm images (OpenHands action server, E2B snapshot, OSWorld AMI) are the data-plane analog of KV cache: sticky, expensive to cold-start.
5. **Perceive.** Observation is pool-specific: Gymnasium step result, ACI file viewer, screenshot (+ optional a11y), Stagehand `observe`, MCP tool result. Place instruction text **before** the screenshot (Anthropic computer-use guidance). Language-level `vm` sandboxes are **explicitly not** security boundaries (OpenAI computer-use docs).
6. **Reason.** Model emits tool calls, `computer_call.actions[]` **in order**, or MCP `tools/call`. Computer use is an **image-token factory**; ACI burns text. Anthropic: **17** member tools (`computer_toolset_20260801`), **batch actions** (sequential in one response, not concurrent). Thinking=`medium` on Sonnet 4.6 / Opus 4.6; `max` adds tokens with no UI accuracy gain.
7. **Act via proxy.** Host validates schema, checks signed ticket / MCP audience (RFC 8707), classifies tool (read-only / reversible / irreversible), executes in the leased env, JSON-encodes results. Non-idempotent tools are Activities with `attempts=1`. MCP long tools declare `execution.taskSupport: required` so the planner is not holding an HTTP socket for 30 h.
8. **Stop check.** User confirmation / Watch Mode; task refusal (Operator **97%** refuse on internal illicit-activity eval — not 100% in production); prompt-injection pause (Operator extra monitor; Anthropic screenshot classifiers); `maxTurns` / `maxBudgetUsd` → `error_max_turns` / `error_max_budget_usd`; env TTL; Temporal history **51,200** events / **50 MB**; human interrupt; kill switch. First trip of `min(token budget, env TTL, ScheduleToClose, vendor quota)` **pages**.
9. **Wait as a first-class state.** (a) Model-requested HITL → user Signal; (b) tool-async MCP `working` → poll `tasks/get` on `task_id`; (c) idle durable → `wait_condition` at **0** activity CPU. Silent stall overnight is the default bug if Watch Mode has the lid closed or `input_required` has no pager.
10. **Checkpoint, CAN, emit.** Persist compacted state (not the full screenshot log). Continue-As-New every **100–1,000** iterations (Temporal pattern) with env snapshot generation matching workflow generation. Destroy lease on Cancel. Telemetry: `$/success`, `$/fail`, turns-to-submit, cache hit %, screenshot resolution, env-lease hours, `% maxBudgetUsd`, `% CAN`, binary vs partial.

**Interview talking point:** “The environment lease is the unit of scheduling. Tokens compact; VMs snapshot; purchases are Activities with idempotency keys. Resume that restores tokens but not the VM is a new task wearing the old goal.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Goal loops — perception → reason → act → stop

**Contracted SWE loop vs unsupervised decomposer.** AutoGPT (2023) is the public ancestor of the unsupervised goal loop: decompose a high-level goal into subgoals, ReAct-style execute, repeat. Voyager notes AutoGPT lacks a skill library, self-verification, and an automatic curriculum. SWE-agent (Yang et al., NeurIPS 2024) is the opposite: a **purpose-built agent-computer interface (ACI)** — search, file viewer, editor, context manager — on a Linux shell. GPT-4 Turbo: **12.47%** (286/2,294) SWE-bench, **18.00%** Lite; **64%** relative gain vs shell-only. Interface design *is* the capability. OpenHands is a **stateless single-step** agent: each `step()` reads event history, optionally condenses, queries the LLM, then either executes or waits for confirmation; supervisor PR #4449 holds the overall plan and interrupts subordinates that run too long.

**Computer-use loop (pixels).** OpenAI CUA / Operator (2025-01-23): screenshot → chain-of-thought over current+past screenshots → click/scroll/type until done **or** user input needed. Confirmations for logins and CAPTCHAs. Launch: **OSWorld 38.1%** (prev SOTA 22.0%; human 72.4%), **WebArena 58.1%** (prev 36.2% computer-use / 57.1% web-agent SOTA; human 78.2%), **WebVoyager 87%**. Test-time scaling: more allowed steps raises OSWorld. Reliability is UI-specific: 10/10 on simple Todoist/Spotify loops; 3/10 on underspecified venue search. Standalone Operator folded into **ChatGPT agent mode** on 2025-07-17. Responses API: send task → run `computer_call.actions[]` **in order** → return `computer_call_output` screenshot → repeat. Custom harnesses must keep Playwright `browser/context/page` alive across steps.

**Anthropic computer use.** Public beta 2024-10-22 on Claude 3.5 Sonnet: OSWorld screenshot-only **14.9%** vs next-best **7.8%**; **22.0%** with more steps. Training used a few simple apps **without internet**; pixel-counting for cursor targeting; flipbook screenshots miss transient UI; researchers recorded the model wandering into Yellowstone photos mid-demo. As of 2026-08, Messages API ships GA `computer_toolset_20260801`: **17** member tools, batch actions sequential in one response. Coordinate space = screenshot pixels; zoom does **not** change the coordinate frame. Prompt-injection classifiers **steer the model to ask user confirmation**; HITL-free loops must contact support to opt out. Computer use is HIPAA-eligible under BAA; **Managed Agents is not** ZDR/HIPAA eligible.

**Lineage that actually transfers:**

| System | Action space | Memory / skill | Stop / stuck |
| --- | --- | --- | --- |
| **Voyager** | Executable JavaScript (Mineflayer), not pixels | Skill library indexed by `text-embedding-ada-002`; promote traces into typed skills | 4 refinement rounds → new curriculum task. Ablation: random curriculum **−93%** items |
| **Generative Agents** | NL social acts in Smallville | Memory stream; retrieval = recency + relevance (cosine) + importance (LLM 1–10); reflect when recent importance sum **>150** (~2–3×/day) | Failure they measured: **wrong memories**, not missing a tool API |
| **SWE-agent** | ACI on Linux | Truncates history; `$4` cap auto-submit | Successes finish earlier (median **$1.21 / 12** steps) than failures (mean **$2.52 / 21** steps) |
| **MCP Tasks** | Any MCP request as durable SM | `task_id` + server `ttl` (ms) | `working` → `input_required` \| `completed` \| `failed` \| `cancelled` |

Production lesson from Voyager: **promote successful traces into typed skills**, not chat summaries. Production lesson from Generative Agents: this is the long-horizon **memory** paper; it is not a computer-use paper. Do not put Smallville-style memory streams on real employees without retention + ACL.

**State machine (control plane, one job):**

```
  ADMIT --(lease ok, goal frozen)--> WORKING
     │                                  │
     │                                  ├-- perceive/reason/act --> WORKING
     │                                  ├-- HITL / classifier ----> INPUT_REQUIRED --(Signal)--> WORKING
     │                                  ├-- MCP taskSupport ------> TOOL_ASYNC --(tasks/get)--> WORKING
     │                                  ├-- wait_condition -------> IDLE_DURABLE --(timer/cron)--> WORKING
     │                                  ├-- maxBudget / maxTurns -> FAILED (error_max_budget_usd)
     │                                  ├-- env TTL / lease gen --> FAILED (lease_expired)
     │                                  ├-- kill / Cancel --------> CANCELLED
     │                                  └-- grader / submit ------> COMPLETED
     └-- policy deny ------------------> FAILED
```

**Complexity.** \(T\) = turns, \(C\) = context tokens after compaction, \(E\) = env-lease seconds, \(H\) = Temporal history events.

- LLM calls: \(\Theta(T)\). Computer-use \(T\) is screenshot-bound (OSWorld 2.0 **~318** calls vs **~30** in 1.0). ACI \(T\) is edit-bound (SWE-agent success **12** vs fail **21**).
- Tokens/turn: \(\Theta(C + S)\) where \(S\) is screenshot tokens (~1,300 for ~1000×1000, engineering-note order-of-magnitude, version-sensitive). History accumulation is the p99: later turns re-send the screenshot *and* prior tool JSON.
- Checkpoint size: \(\Theta(\text{compacted state})\), **not** \(\Theta(\text{screenshot log})\). CAN when \(H\) approaches **10,240** (warn) / **51,200** (hard) or **50 MB**.
- Credit assignment: process RM score = **product** of per-step correctness (*Let’s Verify Step by Step*, Lightman et al., 2023) — one bad step kills the trajectory. Map: TheAgentCompany checkpoint graders; OSWorld 2.0 **~27.25** scoring checkpoints/task.

### 2.2 Checkpoint / interrupt / resume

**Memory ≠ checkpoint.** Generative Agents retrieve; Voyager *promotes code*; SWE-agent *truncates history*; Claude Code *compacts*. A resume that restores tokens but not the VM (cookies, `node_modules`, failed migration) is a new task wearing the old goal.

| System | Checkpoint | Interrupt | Resume |
| --- | --- | --- | --- |
| ChatGPT agent | VM state + tool mix | Take over browser, pause, stop → partial results | Continues with new instructions without losing progress |
| Operator CUA | Screenshot history in model context | Confirmation / Watch Mode | User provides input; loop continues |
| Claude Agent SDK | Sessions; `--resume` / JSONL transcripts | Permissions, hooks | Session id |
| Managed Agents | Server-side event history + sandbox FS | User events mid-execution | “Resume cleanly after pauses”; scheduled cron |
| OpenHands TaskToolSet | Conversation saved to disk | Parent **blocks** on sub-agent | `resume` + task id — wrong tool for a 12 h migrate unless parent is a Temporal Workflow |
| TheAgentCompany | Checkpoint graders (partial points) | n/a (eval) | Episode reset |
| Voyager | Skill library + Chroma | 4-round stuck → new curriculum | Skills transfer to a **new world** |
| MCP Tasks | `task_id` + server TTL | `tasks/cancel` | Poll after disconnect |
| Temporal + sandbox | Activity results + workspace snapshot | Cancel / signal | Replay; portable snapshot (`/switch` provider) |

**Checkpoint dict (minimum viable, control plane):**

```
checkpoint = {
  "goal_hash": "...",          # frozen contract; model cannot rewrite
  "env_generation": 7,         # must equal lease.generation on resume
  "step": 41,
  "spent_usd": 1.21,
  "last_tool_id": "toolu_...",
  "mcp_task_id": None,
  "compacted_obs": "...",      # not the screenshot stack
  "status": "working",
}
```

**Interrupt hierarchy (safest first):** (1) user take-over of the *environment* (ChatGPT agent browser take-over — model never sees passwords), (2) workflow Cancel (does **not** automatically roll back a completed purchase Activity), (3) sandbox kill (drops unsynced FS), (4) token revoke (stops the *next* MCP call, not the in-flight click). Resume tests must assert **env generation == workflow generation**.

**Three wait states.** Long jobs spend most calendar time **waiting**, not decoding: HITL (`input_required` / Watch Mode), tool-async (`tasks/get`), idle durable (`wait_condition`). Billing a GPU for sleep, or losing sandbox TTL while the workflow still thinks the lease is live, is a control-plane bug.

### 2.3 Env gyms, kill switches, invariants

**MCP as environment ABI, not a plugin.** MCP 2025-11-25: servers are OAuth 2.1 **resource servers**; clients **MUST** use RFC 9728 PRM; tokens **MUST** carry RFC 8707 resource indicators bound to the canonical MCP URI. Experimental **Tasks** (SEP-1686): `working` → `input_required` | `completed` | `failed` | `cancelled`; `ttl` in milliseconds; tools declare `execution.taskSupport`: `required` | `optional` | `forbidden` (default forbidden). OSWorld-MCP adds **158** MCP tools (7 apps; **25** distractors; RAG-selected because 158 tools blow context). **69%** of 250 tasks are tool-beneficial; OpenAI o3 **8.3% → 17.6%** at 15 steps; highest tool-invocation rate cited **33.3%** (Claude-4-Sonnet, 50 steps). Same agent can be pointed at a sim gym, a cloud browser, or prod Salesforce *without* changing the loop — which is the sim-to-prod footgun.

**Sim-to-prod gap.** WebArena: **812** tasks on self-hosted clones; GPT-4 agent **14.41%** vs human **78.24%**. CUA later **58.1%** on the same benchmark using pixels — scaffold + model, not “the web got easier.” TheAgentCompany: **175** professional tasks, GitLab+OwnCloud+Plane+RocketChat, Sotopia LLM colleagues; Gemini 2.5 Pro **30.3%** full / **39.3%** partial. SWE-bench Pro: **1,865** problems, **41** repos; gold patches mean **107.4** LOC / **4.1** files; GPT-5 **23.3%** public Pass@1, Opus 4.1 **22.7%**; commercial best **17.8%** (Opus 4.1). GAIA Level 3 is “arbitrarily long sequences of actions”; GPT-4+plugins scored **0**. Rule: **execution-based graders on a resettable intranet ≠ SSO + flaky third-party + irreversible money.**

**Kill switches (product, then yours):**

| Stop | Who | Trigger |
| --- | --- | --- |
| User confirmation / Watch Mode | OpenAI Operator & ChatGPT agent | Side-effecting actions; sensitive sites require active supervision |
| Task refusal | CUA training + usage policy | Banking transfers, stocks, illicit goods |
| Prompt-injection pause | Operator monitor; Anthropic screenshot classifiers | Suspicious on-screen instructions |
| Spend / turn cap | Claude Agent SDK `maxTurns`, `maxBudgetUsd`; SWE-agent **$4**/instance | Open-ended “improve the codebase” |
| Env TTL | E2B 1 h / 24 h; MCP task `ttl` | Lease expiry |
| History limit | Temporal **51,200** events / **50 MB** | Multi-hour tool spam → CAN or terminate |
| Human interrupt | ChatGPT take-over / pause; Managed Agents mid-session events | User steer |
| Kill switch | **Your** control plane | Temporal Cancel + destroy sandbox + revoke MCP token |

**Invariants (fail the interview if you drop one):**

1. **Lease, not request.** HTTP timeout must not kill a 3 h job; KEDA must not evict the worker holding the VM.
2. **Frozen goal.** Curriculum/AutoGPT subgoals cannot rewrite the checkpointed contract. Critic is gated on **spec**, not vibes.
3. **Activities for tools, not Workflow code.** Replay restores completed Activities **without re-executing** them — the difference between “resume” and “double-charge.”
4. **Do not retry non-idempotent tools.** `attempts=1` on click-Pay / `rm` / send. Idempotency key = `sha256(tenant|job|tool|canonical_args|turn)`.
5. **Three clocks nest:** env TTL **>** history rotation (CAN) **>** step budget.
6. **Env generation == workflow generation** on resume. Compacted screenshot log does not imply a live VM.
7. **Control plane stops data plane without the model.** Cancel + destroy lease + revoke token. If any hop requires the model’s cooperation, it is not a kill switch.

---

## 3. Token Economics & NFR Analysis

### 3.1 `$ per 1k runs` — named SKU × named shape

METR’s **50%-time horizon** is the *human expert duration* of tasks the agent is predicted to finish with 50% success — **not** wall-clock autonomy time. Agents that succeed are typically **several times faster** than those humans; METR does not publish agent wall-clock (scaffold- and provider-dependent). Anchors: historical doubling **~7 months** (2019–early 2025) at 50% horizon; o3 **~110 min**; 80% horizon **~5× shorter**; GPT-5 FAQ illustration **~2 h 17 min**; Claude 3.7 Sonnet (Mar 2025 blog) **~1 hour**; TH1.1 suite **228** tasks, **31** of them 8 h+; **>16 h unreliable** on current suite. Messier / holistically scored tasks: agents do **substantially worse**. An 8-hour horizon ≠ automate an 8-hour professional’s day. **50% horizon sizes the research bet; 80% horizon sizes the SLO.**

**Computer use is an image-token factory.** Anthropic: no separate computer-use SKU; screenshots bill as vision tokens. List prices (2026-08-21): Opus 4.8 **$5 / $25** per MTok in/out; cache hit **$0.50**; Sonnet 4.6 **$3 / $15**, cache hit **$0.30**; Sonnet 5 **$2 / $10**; Haiku 4.5 **$1 / $5**. Fast mode Opus 4.8/5: **$10 / $50**. Batch API **50%**. Claude 4.7+ tokenizer **~30% more tokens** for the same text. Third-party notes (⚠️ version-sensitive, not a contract): **466–499** system-prompt overhead tokens, **735** tool-definition tokens, ~**1,300** tokens for a ~1000×1000 screenshot.

**[inferred] per-turn computer-use (Opus 4.8, 4k input + 350 output, no cache):** \(4{,}000/10^6 \times 5 + 350/10^6 \times 25 \approx \$0.029\)/turn. A **50-step** GUI task ≈ **$1.4** before history growth; a **318-call** OSWorld 2.0-shaped job ≈ **$9** if every call is a full vision turn. Prompt cache (10% of input on hits) plus batching can cut this sharply. History accumulation is the real p99.

**ACI / coding agents** burn text, not screenshots. SWE-agent published medians (**$1.21** success / **$2.52** fail, **$4** cap) are **2024 GPT-4 Turbo** dollars, not 2026 Opus. NFR they still teach: **failures are more expensive than successes** because agents fail slowly. **93%** of resolved runs submit before budget exhaust vs **69%** overall — raising the cap is a weak lever.

**Env SKUs (not tokens).** E2B: Hobby **1 h** session, **20** concurrent; Pro **$150/mo**, **24 h** session, **100** concurrent (buy up to **1,100**). Usage **$0.000028/s** for default **2 vCPU** (= **$0.1008/h**), **$0.0000045/GiB/s** RAM. **[inferred] overnight 8 h, 2 vCPU + 2 GiB:** \(8 \times (0.1008 + 2 \times 0.0000045 \times 3600) \approx \$0.81\) sandbox + LLM. Temporal workers while `wait_condition`: **$0** compute on the activity worker. ChatGPT agent: **400 / 40** monthly *messages*, not dollars-per-task. ⚠️ converting messages to “tasks” without telemetry is fiction. Claude Agent SDK: `maxBudgetUsd` covers **subagents**; hitting the cap stops background subagents (Claude Code **≥ v2.1.217**).

Define a **long-horizon task** as “OSWorld 2.0-class”: ~1.6 h human, hundreds of tool calls, binary success ≪ 50% at 500 steps. OSWorld 2.0 leader **20.6%** binary / **54.8%** partial (Claude Opus 4.8 batched, 500 steps) — you are buying **partial credit**, not 1k completions.

| Shape | Token $ **[inferred]** | Env $ **[inferred]** | 1k tasks | Notes |
| --- | --- | --- | --- | --- |
| SWE-agent-like ACI, 2024 paper medians | ~$1.2–$2.5 | Docker-local ~0 | **$1.2k–$2.5k** | Inflate for 2026 frontier + thinking; cap still required |
| Computer-use 50 vision turns, Opus 4.8, no cache | ~$1.4 | E2B 0.3 h ≈ $0.03 | **~$1.4k** | Cheap relative to 318-call jobs |
| Computer-use 318 vision turns, Opus 4.8, no cache | ~$9 | E2B ~1.6 h ≈ $0.16 | **~$9k** | Cache/batch/browser-use tool can drop this; ⚠️ |
| Failed-slow coding agent | ~2× success | same | **budget to the fail tail** | SWE-agent empirical |
| ChatGPT agent consumer | n/a (quota) | included | n/a | 40–400 msgs/mo is the NFR, not $/task |

Dashboard NFRs (because p50/p95/p99 wall-clock is unpublished): **$/successful task**, **$/failed task**, **turns-to-submit**, **cache hit rate**, **screenshot resolution**, **env-lease hours**, **% jobs hitting `maxBudgetUsd`**, **% Continue-As-New**.

### 3.2 Latency — p50 / p95 / p99 (label **[inferred]**)

⚠️ **Not in METR, not in Operator, not in Managed Agents public docs.** Proxies: OSWorld 2.0 step budgets **150 / 300 / 500**; SWE-agent **12 vs 21** steps; GAIA human **6.8 / 10.5 / 17.7** min (Level 1/2/3); TheAgentCompany “hours of professional work” without a published agent-hour histogram.

| Percentile | Wall-clock job **[inferred proxy]** | Token / turn **[inferred]** | Env / wait |
| --- | --- | --- | --- |
| **p50** | SWE-agent **success** median **12** steps / **$1.21**; GAIA L1 human **6.8 min** as *task* scale not agent SLO | Cache-hit ACI turn; computer-use **[inferred] ~$0.029**/turn Opus 4.8 4k/350 no cache | Warm lease; `wait_condition` = **0** worker CPU |
| **p95** | SWE-agent **fail** mean **21** steps / **$2.52**; OSWorld 2.0 **300**-step budget; GAIA L3 human **17.7 min** | History growth: screenshot *plus* prior tool JSON every turn | MCP `working` poll; HITL queue; CAN compaction pause |
| **p99** | OSWorld 2.0 **500**-step cap; Operator autonomy eval **400**-step timeout; METR **>16 h** measurements unreliable; E2B Hobby **1 h** kill of an 8 h job | Tokenizer +**30%** (Claude 4.7+); thinking=`max`; subagents under `maxBudgetUsd` | Silent stall (Watch Mode lid closed); flipbook miss of toasts; quota cliff (40 ChatGPT agent messages); Temporal history **50 MB** terminate |

| Tier | Mitigations |
| --- | --- |
| p50 | ACI / a11y / MCP over pixels when an API exists; prompt cache; batch computer-use actions (cuts **round trips**, not pixels); thinking=`medium`; instruction text **before** screenshot |
| p95 | Compact / CAN before history blow-up; MCP Tasks so tool latency is off the LLM HTTP timeout; heartbeats; process checkpoints (~27.25/task OSWorld 2.0) so you do not assign the overnight bill to the final `submit` |
| p99 | Overnight fuse = `min(maxBudgetUsd, env TTL, ScheduleToClose, vendor quota)` — first trip pages; 24 h sandbox SKU for 8 h jobs; deny-on-timeout Watch Mode; pager on `input_required`; do not opt out of injection classifiers without a support ticket and a two-person rule |

Track **$/success vs $/fail** separately. SWE-agent: successes are short; failures eat the cap. Computer use: stalls still take screenshots.

### 3.3 Throughput, back-pressure, availability, RPO/RTO, overnight caps

\[
\mathrm{throughput}=\min(\mathrm{model\ RPM/TPM},\ \mathrm{env\ pool\ concurrency},\ \mathrm{lease\ hours}/\mathrm{wall},\ \mathrm{MCP\ task\ ttl},\ \mathrm{Temporal\ history\ headroom},\ \mathrm{HITL\ pager\ capacity})
\]

**[inferred] pool math, not a vendor SLO:** concurrent overnight jobs \(N\) × (lease hours / wall hours) = required warm capacity. E2B Pro default **100** concurrent (buy to **1,100**); Hobby **20** and **1 h** TTL will kill an 8 h job. OSWorld-style VMs are heavier than E2B 2 vCPU: budget AMI pull + Xvfb + browser RAM. Browser farms: concurrency is **sessions**, not vCPU — keep-alive is sticky and expensive to cold-start if you re-login every resume.

**Back-pressure protocol:**

1. Admit iff breaker ∈ {closed, half-open} **and** spend fuse has room **and** pool has a lease **and** kill switch is down **and** vendor quota remains.
2. Saturation → **429 Retry-After** on *new* jobs, not kill of in-flight leases. Quota 429 ≠ overload 429 ≠ 503 empty pool.
3. MCP `working` tasks hold env CPU (E2B per-second) until `ttl` — cap concurrent tasks per tenant.
4. Temporal: disable SDK retries (`attempts=1`); Start-To-Close always; Schedule-To-Start is a **page**. Continue-As-New at **100–1,000** iterations, not at 51,199 events.
5. HITL `input_required` without a pager is silent stall. Page the on-call; do not let Watch Mode run with the laptop lid closed.
6. Client abort still bills generated vision tokens if the loop ignored cancel — count them against `maxBudgetUsd`.

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | Supervisor 99.9% on **job admit + lease hold**, not HTTP 200 of a single LLM call. Interactive computer use requires the desktop awake — that is **not** 99.9% | Durable wait (`wait_condition`) vs env TTL: workflow alive + dead VM = false availability |
| RPO | Goal contract + Activity results + env snapshot: **0** for control state. KV/prompt cache and screenshot log: **lossy** on CAN | Treating screenshot history as RPO=0 blows Temporal **50 MB** |
| RTO | Resume from last checkpoint with env generation match. Replay **must not** re-execute completed purchase Activities | Fast resume vs identical GUI (pixels drift). Reset = rewind to a `WorkflowTask*`, not a substitute for env snapshots |
| Consistency | Tools: exactly-once via idempotency keys. Tokens: at-least-once retry may change text. Cookies: per-tenant `contextId` | Sticky browser context ↑ resume, ↑ session-fixation risk |
| Compliance | Screenshots are PII/PHI. Computer use HIPAA-eligible under BAA (Anthropic 2026 blog). **Managed Agents ineligible** for ZDR and HIPAA BAA (stateful sessions on Anthropic infra). Principal emails in Temporal Cloud Event History are PII — ACL the namespace | Residency vs vendor-owned VM. ChatGPT agent High bio treatment (terminal + connectors) is a **higher** bar than Operator Low |
| Cost vs latency | Overnight fuse; thinking=`medium`; batch actions; cache; ACI default, pixels for the long tail | **$1.4k vs $9k / 1k** is loop shape (50 vs 318 vision turns), not a SKU |
| Autonomy vs safety | Watch Mode / classifier HITL / deny-by-default writes | Operator **55%** `not_overrefuse` vs GPT-4o **90%**: autonomy was tuned **cautious** — stalls, not only safety wins |

**Overnight cost cap (all four knobs, sourced):** `maxBudgetUsd` + env TTL + Temporal `ScheduleToClose` on the *workflow* + provider message quota. Without all four, a looping computer-use agent is an unbounded image-token meter. Design as **min(...)** — the first trip pages, not the last.

**Explicit NFR trade-offs.**

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| Loop owner | Messages API, you own overnight | Agent SDK in *your* process + Temporal | Managed Agents / ChatGPT agent (vendor harness; **no** ZDR on Managed Agents) |
| Observation | ACI / a11y / MCP | DOM first, pixels for long tail | Pixels + classifier HITL + recordings as audit **and** PII store |
| Credit | Outcome-only “looks done” | Unit tests + process checkpoints | Binary grader + safety report (not OSWorld 2.0 **54.8%** partial) |
| Env | Gym episode; Hobby 1 h | E2B Pro 24 h; disposable repo sandbox | Isolated per-tenant browser context; no sim MCP schema in prod |
| Stop | Model “I’m finished” | `maxBudgetUsd` + tests + TTL | Cancel + destroy lease + revoke token **without** model cooperation |
| METR reading | 50% horizon for the research PR | 80% horizon (~5× shorter) for the SLO | Gate prod on binary success of the *commercial* SWE-bench Pro gap |

---

## 4. Distributed Resilience & Security

### 4.1 Temporal / Kafka and failure taxonomy

Temporal: Workflow Executions have **no time limit**; they **do** have history limits — warning **10,240** events; hard stop **51,200** events, **2,000** Updates, **10,000** Signals, **50 MB**. **Continue-As-New** checkpoints latest state into a new Run with the same Workflow Id (pattern: every **100–1,000** iterations). LLM calls and tools **must** be Activities (non-deterministic); disable SDK retries (`attempts=1`) so Temporal owns retry. Replay restores state **without re-executing completed Activities**. Reset: terminate + copy history to a `WorkflowTask*` event — use for “rewind the agent to before it went off-policy,” not as a substitute for env snapshots. Principal Attribution stamps **who** started/signaled/cancelled.

**Kafka.** Topics: `agent.jobs`, `agent.tool_intent`, `agent.dlq`. Produce **intent** (`tool_call` + idempotency key) **before** the side effect (outbox). Poison → DLQ after N; do not block the partition. Online chat does **not** wait on Kafka; effectful overnight tools **do** wait on WORM. SIEM for MCP must join on **`task_id`**, not only JSON-RPC `id`, or async tools vanish from the log.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB screenshot histories. Do not store full screenshots in workflow history — store blob refs + compacted obs.

**Failure taxonomy.**

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, 5xx, TLS reset, MCP poll blip, browser crash, screenshot all-black | Full-jitter retry on **idempotent** Activities only; honor `Retry-After`; recreate **lease**, do not CAN LLM history into a dead VM |
| Permanent | 400 illegal body, `error_max_budget_usd`, policy deny, frozen-goal hash mismatch | Fail the job; do not retry |
| Poison pill | Same payload crashes the env; Kafka HOL zombie; `maxReceiveCount=1` | Hash + N crashes → DLQ; pause partition; never unbounded HTTP retry of `computer_call` |
| Semantic | Goal drift (AutoGPT subgoals rewrite contract); partial-success theater (54.8% partial / 20.6% binary); silent prefix/screenshot miss; 429-quota counted as availability | Frozen goal + checkpoint tests; gate prod on **binary** + safety report; Watch Mode deny-on-timeout |

| Mode | Mechanism | Evidence | Control |
| --- | --- | --- | --- |
| **Runaway spend** | Fail-slow loops; screenshot every step; thinking=`max`; subagents; retrying Activities that include LLM calls | SWE-agent fail **$2.52** vs success **$1.21**; OSWorld 2.0 **318** calls; tokenizer +30% | `maxBudgetUsd`, cache, batch actions, thinking=`medium`, Temporal `attempts=1`, env TTL |
| **Goal drift** | Open-ended curriculum / AutoGPT subgoals; wrong-memory retrieval | Voyager *wants* drift (novelty search); Generative Agents breakdown = bad retrieval; Claude Yellowstone tangent | Frozen goal artifact + checkpoint acceptance tests |
| **Environment leak** | Sim MCP schema in prod; cookies in browser pool; MCP token audience skip | OSWorld-MCP distractor tools; NIST ZT | Separate pools; RFC 8707; no Docker socket mount |
| **Unattended destructive tools** | Overnight worker + `rm` / migrate / send / purchase without HITL; classifier opt-out | Operator over-refusal **by design**; Anthropic classifier opt-out is **support-ticket** | Watch Mode class; deny-by-default write; two-person rule for prod data plane |
| **Silent stall** | Flipbook misses toasts; dead `tasks/result` worker; Watch Mode user asleep; laptop lid closed | Anthropic short-lived UI; Dispatch requires desktop awake; MCP `input_required` without pager | Heartbeats, `ScheduleToClose`, page on-call |
| **Prompt injection → exfil** | On-screen / HTML / metadata instructions; connectors + logged-in browser | Operator extra monitor; ChatGPT agent High emphasis | Classifier + confirm; disable unused connectors; no secrets in GUI |
| **OCR / visual edit collapse** | Random strings (API keys, DNA, Bitcoin) from pixels | Operator autonomy eval: copy-paste avoided, OCR fails; nano/VS Code visual edits loop to 400-step cap | Prefer a11y/DOM/API for secrets; bash ACI for code |
| **Double side-effect on resume** | Replay LLM, not Activity result; HTTP retry of `computer_call` | Temporal tutorial exists because this is the default bug | Idempotency keys; Activities for tools; never retry “click Pay” |
| **Quota cliff** | 40 ChatGPT agent messages; E2B 1 h Hobby kill | Product docs | Separate overnight SKU; 24 h sandbox; durable wait |
| **Partial-success theater** | 54.8% partial / 20.6% binary | OSWorld 2.0 | Gate prod on **binary** + safety report |
| **Eval-prod skew** | WebArena vs live WebVoyager; LibreOffice vs Excel | ChatGPT agent SpreadsheetBench **45.5%** `.xlsx` vs Copilot **20%** but **OSX+LibreOffice** vs authors’ Windows+Excel | Declare the env in the SLO |

**Destructive-tool taxonomy (unattended):** read-only (search, screenshot) / reversible (local git) / reversible-with-SLA (email recall, ticket comment) / irreversible (wire, prod `DROP`, public social). Operator refuses stock trading; ChatGPT agent refuses bank transfers and requires confirmation before purchase. Copies of those lists belong in the **activity interceptor** — a new MCP server will not inherit the model’s refusal training.

**Chaos (minimum).** (1) Kill worker at 50% checkpoints — no duplicate side effects; env state matches token state. (2) Screenshot all-black / browser crash — new lease, not CAN into dead VM. (3) `input_required` with pager down — job must not look “healthy.” (4) Temporal Cancel mid-purchase Activity — compensating action is a **new** turn, not overwrite WORM. (5) Env TTL while workflow still running. (6) Injection drill on *prod-like* DOM. (7) Kill-switch drill that does **not** need the model.

### 4.2 Circuit breaker closed → open → half-open, fallbacks

Per **downstream** (supervisor → model, supervisor → MCP, supervisor → env pool), not per process:

- **Closed:** traffic flows; consecutive failures or error-rate window trips to open. Retry **budgets** so retries cannot explode vision-token spend.
- **Open:** fail fast; start a timer. Overnight jobs **do not** busy-spin on an open breaker — `wait_condition` or fail the step. Effectful tools **fail-closed** without WORM.
- **Half-open:** allow a probe (one request). Success → closed; fail → open.

```
  CLOSED --(failures≥N or error-rate window)--> OPEN --(timer)--> HALF_OPEN
    ▲                                              │                    │
    │            success probe                     │ fail probe         │
    └──────────────────────────────────────────────┴────────────────────┘
```

| Breaker | Trip | Action |
| --- | --- | --- |
| Activity retry policy | 429 / 5xx / timeout | Exponential backoff + **full jitter**; **do not** retry non-idempotent tools |
| `start_to_close_timeout` | LLM 2 min vs timeout 1 min | False retries → duplicate spend |
| `maxBudgetUsd` / message quota | Spend | `error_max_budget_usd`; stop subagents |
| Env health | Screenshot all-black, browser crash | Recreate lease; **do not** CAN LLM history into a dead VM |
| Prompt-injection monitor | Classifier hit | Pause; require HITL |
| History size | `GetContinueAsNewSuggested()` | CAN with compacted state, not full screenshot log |
| Kill switch | Policy | `Cancel` workflow + destroy sandbox + revoke MCP token |

**Retry rule.** Exponential backoff + **full jitter** (`sleep = U(0, min(cap, base·2^attempt))`) on **idempotent** 503/429. Honor `Retry-After`. **Do not** retry streaming computer-use screenshots as if they were JSON, and **do not** retry “click Pay.”

**Fallback chain:** primary model (frontier + computer tool / ACI) → secondary model (cheaper / higher availability, **cold** cache) → **deterministic degrade** (schema-valid JSON: `status=needs_human`, last checkpoint, spend so far). Do not fall back from Temporal Activity failure to fire-and-forget HTTP. Do not fall back from classifier-pause to “just click.” Do not fall back from Managed Agents (no ZDR) onto a HIPAA workload. Cascade **counts** in the error budget.

### 4.3 Zero-Trust MCP, tool RBAC, PII, kill switches, immutable logs

**Zero-Trust MCP.** NIST SP 800-207: no implicit trust from network location; authenticate **and** authorize per session to a **resource**. MCP 2025-11-25 is the agent-shaped instance: resource indicators bind tokens to **one** server; audience validation stops token passthrough; PRM discovery via `WWW-Authenticate`; HTTPS; no more default `/authorize` fallback (removed June 2025). Enterprise pattern: **IdP issues tokens, MCP server enforces RBAC, agent never sees long-lived SaaS keys.** Managed Agents: credentials stay out of the sandbox; a proxy fetches secrets when Claude calls MCP.

**Tool RBAC (minimum viable):**

1. **Install-time allowlist** of MCP servers (MDM / managed settings). Claude Code / Cowork: approved connectors, plugin allowlists — enforce via admin, not a blog.
2. **Per-tool `taskSupport` + elicitation** for step-up auth. MCP URL-mode elicitation is phishing-sensitive — treat URLs as untrusted.
3. **Runtime:** OpenHands security analyzer (low / medium / high → confirm); Claude permissions + hooks; Operator Watch Mode on email. Policy engine in the **control plane** that the model cannot tool-call around (Agent SDK hooks; Temporal activity interceptor).
4. **Data:** ChatGPT agent one-click delete browsing data + logout; takeover mode **does not** send passwords to the model. Anthropic: do not put passwords in prompts unless you accept injection risk; `<robot_credentials>` is documented **and** warned.

**Tool RBAC is not IdP RBAC.** A user who may *read* Salesforce may not allow an agent to *bulk-update* it at 03:00. Bind: `(user, agent_id, tool_name, resource_indicator, time_window, spend_cap)`. MCP OAuth scopes are necessary and insufficient.

**Sandbox vs computer-use VM vs host.**

| Boundary | Stops | Does not stop |
| --- | --- | --- |
| Bash-only sandbox | Shell exfil via curl | `Write` tool, MCP, hooks on host |
| Process sandbox-runtime | FS/net for *all* child processes | User-granted Screen Recording + Accessibility (Claude Code computer use) |
| Cloud sandbox (E2B, Managed Agents) | Host disk | Data the agent was given; egress if network on |
| Computer-use VM (ChatGPT agent) | Host OS | Anything in the VM after user login takeover |
| Language `vm` | Nothing that matters | OpenAI: not a security boundary |

Claude Code warning: built-in Bash sandbox **does not** constrain file tools, MCP, or hooks unless the **whole process** is inside the runtime / devcontainer. Browserbase `contextId` + `--persist` (default true) + `--keepAlive`: cookies survive the agent process — resume **and** session fixation. Per-tenant contexts; TTL the context on job Cancel; recordings are an audit log **and** a PII store.

**PII pipeline:** detect → redact **before tokenize / before Temporal payload / before cache key / before screenshot retention** → audit placeholder (hash, never raw). Surfaces unique to this topic: (1) screenshots of mail, EHR, HRIS; (2) browser recordings; (3) Operator/ChatGPT cookies; (4) Temporal Principal emails; (5) MCP elicitation fields; (6) Generative Agents–style memory streams on real employees. Retention: ChatGPT one-click logout; Managed Agents session delete API; you must still delete **your** object store of screenshots.

**Kill switches for autonomous actions (layered, all required):** (1) model-level refusal, (2) product confirmation / Watch Mode, (3) classifier pause, (4) workflow Cancel, (5) destroy env, (6) revoke OAuth refresh, (7) blocklist domains (Operator gambling/adult/weapons). Operator also: real-time moderation, offline detection for CSAM/deception, site blocklist. Election-period extra: Anthropic monitored social posting / domain registration / government sites during 2024 beta. ASL-2 (Claude 3.5 Sonnet + computer use, 2024): Anthropic judged computer use **lowers the barrier** to applying existing skills, not a jump to ASL-3, and argued **shipping at ASL-2** is safer than waiting for ASL-3 models. ChatGPT agent’s High bio treatment is the counter-example when terminal + web + connectors stack.

**Immutable audit tuple (join keys in parens):** workflow events (`workflow_id`, `run_id`, principal), model traces (`response_id` / `previous_response_id`), tool calls (`tool_use_id` / MCP `task_id`), env lease (`sandbox_id`, `contextId`), confirmation outcomes (`approved|denied|timeout`). Hash-chained WORM; **not** sampling-eligible. If any hop is missing, overnight incidents become “the model did something.” Deny-on-timeout is a **product** control, not a prompt suggestion.

**Sim-to-prod promotion gate:** same MCP tool schema, **different** allowlists, credentials, and irreversible-action policy. Never promote a WebArena GitLab token policy to corp GitLab. Gate on: (a) binary grader, (b) injection drill on the *prod-like* DOM, (c) duplicate-side-effect test, (d) kill-switch drill that does not need the model.

---

## 5. Production Enterprise Code

Stdlib-only overnight job supervisor: full-jitter retries, circuit breaker closed→open→half-open, primary→secondary→deterministic `needs_human`, correlation-id JSON logs, PII detect→redact→audit, `maxBudgetUsd` spend fuse, checkpoint dict with env generation, env lease + TTL, kill switch (Cancel + destroy lease, no model cooperation), idempotent irreversible tools, graceful degrade. Run: copy the block; `python overnight_supervisor.py` (do not add a repo `.py`).

```python
#!/usr/bin/env python3
"""Overnight autonomous-job supervisor (stdlib only). Run: python overnight_supervisor.py"""
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

POLICY_VERSION = "aa-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
CAN_EVERY_STEPS = 4
INFERRED_TURN_USD = 0.029
MAX_TURNS = 12


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "job_id": getattr(record, "job_id", None),
            "breaker": getattr(record, "breaker", None),
            "spent_usd": getattr(record, "spent_usd", None),
            "env_generation": getattr(record, "env_generation", None),
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


def build_logger(correlation_id: str, tenant: str, job_id: str) -> CorrelationAdapter:
    base = logging.getLogger("aa.supervisor")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "job_id": job_id}
    )


_PII = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII:
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


class CircuitOpenError(PermanentError):
    pass


class SpendCapError(PermanentError):
    pass


class KillSwitchError(PermanentError):
    pass


class LeaseExpiredError(PermanentError):
    pass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class PoolType(Enum):
    GYM = "gym"
    CODE_SANDBOX = "code_sandbox"
    COMPUTER_USE_VM = "computer_use_vm"
    BROWSER_FARM = "browser_farm"


class ToolClass(Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class JobStatus(Enum):
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    TOOL_ASYNC = "tool_async"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_HUMAN = "needs_human"


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.05,
    max_seconds: float = 1.0,
) -> Any:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if attempt == attempts - 1:
                raise
            cap = min(max_seconds, base_seconds * (2 ** attempt))
            time.sleep(random.uniform(0, cap))
    raise last if last else TransientError("retry exhausted")


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = BREAKER_FAILURES,
        recovery_seconds: float = BREAKER_RECOVERY_S,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> None:
        with self._lock:
            if self.state is CircuitState.CLOSED:
                return
            if self.state is CircuitState.OPEN:
                if time.monotonic() - self.opened_at >= self.recovery_seconds:
                    self.state = CircuitState.HALF_OPEN
                    return
                raise CircuitOpenError("circuit open")
            return

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()


class SpendFuse:
    def __init__(self, max_budget_usd: float) -> None:
        self.max_budget_usd = max_budget_usd
        self.spent = 0.0
        self._lock = threading.Lock()

    def charge(self, usd: float) -> None:
        with self._lock:
            if self.spent + usd > self.max_budget_usd + 1e-12:
                raise SpendCapError(
                    f"maxBudgetUsd {self.max_budget_usd} spent={self.spent:.4f} next={usd:.4f}"
                )
            self.spent += usd


class KillSwitch:
    def __init__(self) -> None:
        self._tripped = False
        self.reason = ""
        self._lock = threading.Lock()

    def trip(self, reason: str) -> None:
        with self._lock:
            self._tripped = True
            self.reason = reason

    def assert_live(self) -> None:
        with self._lock:
            if self._tripped:
                raise KillSwitchError(self.reason)


@dataclass
class EnvLease:
    lease_id: str
    pool: PoolType
    generation: int
    sandbox_id: str
    context_id: str | None
    expires_at: float
    destroyed: bool = False

    def assert_live(self) -> None:
        if self.destroyed:
            raise LeaseExpiredError("lease destroyed")
        if time.monotonic() > self.expires_at:
            raise LeaseExpiredError("env TTL exceeded")


@dataclass
class Checkpoint:
    goal_hash: str
    env_generation: int
    step: int
    spent_usd: float
    last_tool_id: str
    mcp_task_id: str | None
    compacted_obs: str
    status: str
    idempotency_seen: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_hash": self.goal_hash,
            "env_generation": self.env_generation,
            "step": self.step,
            "spent_usd": self.spent_usd,
            "last_tool_id": self.last_tool_id,
            "mcp_task_id": self.mcp_task_id,
            "compacted_obs": self.compacted_obs,
            "status": self.status,
            "idempotency_seen": dict(self.idempotency_seen),
        }


@dataclass
class ModelTurn:
    text: str | None
    tool_name: str | None
    tool_args: dict[str, Any]
    tool_class: ToolClass
    usd: float = INFERRED_TURN_USD


class StaticClient:
    def __init__(self, name: str, turns: list[ModelTurn], fail: type[Exception] | None = None) -> None:
        self.name = name
        self.turns = list(turns)
        self.fail = fail
        self.i = 0

    def complete(self, prompt: str) -> ModelTurn:
        if self.fail is not None and self.i == 0:
            self.i += 1
            raise self.fail(f"{self.name} transient")
        if self.i >= len(self.turns):
            raise PermanentError(f"{self.name} empty")
        turn = self.turns[self.i]
        self.i += 1
        _ = prompt
        return turn


class FallbackChain:
    def __init__(
        self,
        primary: StaticClient,
        secondary: StaticClient,
        breaker: CircuitBreaker,
        *,
        retry_attempts: int = 3,
        retry_base: float = 0.01,
        retry_max: float = 0.04,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.retry_attempts = retry_attempts
        self.retry_base = retry_base
        self.retry_max = retry_max

    def complete(self, prompt: str, log: CorrelationAdapter) -> tuple[ModelTurn, str]:
        kwargs = {
            "attempts": self.retry_attempts,
            "base_seconds": self.retry_base,
            "max_seconds": self.retry_max,
        }
        try:
            self.breaker.allow()
            turn = retry_call(lambda: self.primary.complete(prompt), **kwargs)
            self.breaker.record_success()
            log.info("primary_ok model=%s", self.primary.name)
            return turn, self.primary.name
        except (CircuitOpenError, TransientError, PermanentError) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.record_failure()
            log.warning("primary_fail err=%s breaker=%s", exc, self.breaker.state.value)
            try:
                turn = retry_call(lambda: self.secondary.complete(prompt), **kwargs)
                log.info("secondary_ok model=%s", self.secondary.name)
                return turn, self.secondary.name
            except (TransientError, PermanentError) as sec:
                log.error("degraded err=%s", sec)
                return (
                    ModelTurn(
                        text=json.dumps({"status": "needs_human", "reason": str(sec)}),
                        tool_name=None,
                        tool_args={},
                        tool_class=ToolClass.READ_ONLY,
                        usd=0.0,
                    ),
                    "degraded",
                )


class EnvPool:
    def __init__(self) -> None:
        self._gen = 0
        self._lock = threading.Lock()

    def acquire(self, pool: PoolType, ttl_s: float) -> EnvLease:
        with self._lock:
            self._gen += 1
            gen = self._gen
        return EnvLease(
            lease_id=str(uuid.uuid4()),
            pool=pool,
            generation=gen,
            sandbox_id=f"sbx_{gen}",
            context_id=f"ctx_{gen}" if pool is PoolType.BROWSER_FARM else None,
            expires_at=time.monotonic() + ttl_s,
        )

    def destroy(self, lease: EnvLease) -> None:
        lease.destroyed = True


class ToolProxy:
    def __init__(self) -> None:
        self.effects: list[str] = []

    def execute(
        self,
        *,
        tenant: str,
        job_id: str,
        turn_index: int,
        name: str,
        args: dict[str, Any],
        tool_class: ToolClass,
        checkpoint: Checkpoint,
        confirmed: bool,
    ) -> str:
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(
            f"{tenant}|{job_id}|{name}|{canonical}|{turn_index}".encode()
        ).hexdigest()
        if key in checkpoint.idempotency_seen:
            return checkpoint.idempotency_seen[key]
        if tool_class is ToolClass.IRREVERSIBLE and not confirmed:
            raise PermanentError("irreversible tool denied: Watch Mode / deny-on-timeout")
        payload = json.dumps({"ok": True, "tool": name, "args": args})
        self.effects.append(name)
        checkpoint.idempotency_seen[key] = payload
        checkpoint.last_tool_id = key[:16]
        return payload


class JobSupervisor:
    def __init__(
        self,
        chain: FallbackChain,
        pool: EnvPool,
        proxy: ToolProxy,
        *,
        max_budget_usd: float,
        env_ttl_s: float,
        max_turns: int = MAX_TURNS,
        schedule_to_close_s: float = 3600.0,
        vendor_quota: int = 400,
    ) -> None:
        self.chain = chain
        self.pool = pool
        self.proxy = proxy
        self.fuse = SpendFuse(max_budget_usd)
        self.kill = KillSwitch()
        self.env_ttl_s = env_ttl_s
        self.max_turns = max_turns
        self.schedule_to_close_s = schedule_to_close_s
        self.vendor_quota = vendor_quota

    def run(
        self,
        goal: str,
        *,
        tenant: str,
        pool_type: PoolType = PoolType.CODE_SANDBOX,
        confirm_irreversible: bool = False,
        resume: Checkpoint | None = None,
        kill_after_step: int | None = None,
        expire_lease: bool = False,
    ) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        job_id = f"{tenant}:{uuid.uuid4().hex[:8]}"
        log = build_logger(correlation_id, tenant, job_id)
        redacted, pii_audit = redact_pii(goal)
        goal_hash = hashlib.sha256(redacted.encode()).hexdigest()[:16]
        log.info("pii_redactions count=%s policy=%s", len(pii_audit), POLICY_VERSION)

        started = time.monotonic()
        lease = self.pool.acquire(pool_type, 0.0 if expire_lease else self.env_ttl_s)
        if resume is not None:
            if resume.goal_hash != goal_hash:
                raise PermanentError("frozen goal mismatch on resume")
            if resume.env_generation != lease.generation:
                raise PermanentError("env generation != workflow generation")
            ckpt = resume
            self.fuse.spent = ckpt.spent_usd
        else:
            ckpt = Checkpoint(
                goal_hash=goal_hash,
                env_generation=lease.generation,
                step=0,
                spent_usd=0.0,
                last_tool_id="",
                mcp_task_id=None,
                compacted_obs="reset",
                status=JobStatus.WORKING.value,
            )

        messages = redacted
        model_used = "none"
        try:
            while ckpt.step < self.max_turns:
                self.kill.assert_live()
                lease.assert_live()
                if time.monotonic() - started > self.schedule_to_close_s:
                    raise PermanentError("ScheduleToClose")
                if ckpt.step >= self.vendor_quota:
                    raise PermanentError("vendor message quota")
                if kill_after_step is not None and ckpt.step >= kill_after_step:
                    self.kill.trip("policy kill")
                    self.pool.destroy(lease)
                    raise KillSwitchError("policy kill")

                turn, model_used = self.chain.complete(messages, log)
                if model_used == "degraded":
                    ckpt.status = JobStatus.NEEDS_HUMAN.value
                    ckpt.compacted_obs = turn.text or ""
                    log.info("graceful_degrade spent_usd=%s", self.fuse.spent)
                    break

                self.fuse.charge(turn.usd)
                ckpt.spent_usd = self.fuse.spent
                ckpt.step += 1

                if turn.tool_name:
                    result = self.proxy.execute(
                        tenant=tenant,
                        job_id=job_id,
                        turn_index=ckpt.step,
                        name=turn.tool_name,
                        args=turn.tool_args,
                        tool_class=turn.tool_class,
                        checkpoint=ckpt,
                        confirmed=confirm_irreversible,
                    )
                    replay = self.proxy.execute(
                        tenant=tenant,
                        job_id=job_id,
                        turn_index=ckpt.step,
                        name=turn.tool_name,
                        args=turn.tool_args,
                        tool_class=turn.tool_class,
                        checkpoint=ckpt,
                        confirmed=confirm_irreversible,
                    )
                    if replay != result:
                        raise PermanentError("idempotency hole")
                    messages += f"\n<tool_result>{result}</tool_result>"
                    ckpt.compacted_obs = f"step={ckpt.step} tool={turn.tool_name}"
                    if ckpt.step % CAN_EVERY_STEPS == 0:
                        ckpt.compacted_obs = f"can:{ckpt.compacted_obs}"
                        log.info("continue_as_new step=%s", ckpt.step)
                    continue

                parsed = json.loads(turn.text or "{}")
                ckpt.status = parsed.get("status", JobStatus.COMPLETED.value)
                ckpt.compacted_obs = turn.text or ""
                break
            else:
                raise PermanentError("error_max_turns")
        except (SpendCapError, KillSwitchError, LeaseExpiredError, PermanentError) as exc:
            ckpt.status = (
                JobStatus.CANCELLED.value
                if isinstance(exc, KillSwitchError)
                else JobStatus.FAILED.value
            )
            self.pool.destroy(lease)
            log.error("job_stop err=%s status=%s spent_usd=%s", exc, ckpt.status, ckpt.spent_usd)
            return {
                "correlation_id": correlation_id,
                "job_id": job_id,
                "status": ckpt.status,
                "checkpoint": ckpt.as_dict(),
                "pii_audit": pii_audit,
                "model": model_used,
                "effects": list(self.proxy.effects),
                "error": str(exc),
                "lease_destroyed": lease.destroyed,
            }

        self.pool.destroy(lease)
        log.info(
            "job_done status=%s spent_usd=%s steps=%s breaker=%s",
            ckpt.status,
            ckpt.spent_usd,
            ckpt.step,
            self.chain.breaker.state.value,
        )
        return {
            "correlation_id": correlation_id,
            "job_id": job_id,
            "status": ckpt.status,
            "checkpoint": ckpt.as_dict(),
            "pii_audit": pii_audit,
            "model": model_used,
            "effects": list(self.proxy.effects),
            "error": None,
            "lease_destroyed": lease.destroyed,
        }


def _aci_turns() -> list[ModelTurn]:
    return [
        ModelTurn("search", "aci_search", {"q": "failing test"}, ToolClass.READ_ONLY, 0.02),
        ModelTurn("edit", "aci_edit", {"path": "app.py", "patch": "fix"}, ToolClass.REVERSIBLE, 0.02),
        ModelTurn(
            json.dumps({"status": "completed", "pr": "ready"}),
            None,
            {},
            ToolClass.READ_ONLY,
            0.02,
        ),
    ]


def _demo() -> None:
    retry = dict(retry_attempts=2, retry_base=0.01, retry_max=0.03)

    happy = JobSupervisor(
        FallbackChain(
            StaticClient("opus-aci", _aci_turns()),
            StaticClient("sonnet-aci", _aci_turns()),
            CircuitBreaker(failure_threshold=2),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    out = happy.run("Fix failing test for user@example.com ssn 123-45-6789", tenant="t1")
    assert out["status"] == "completed"
    assert out["checkpoint"]["step"] == 3
    assert any(x["type"] == "email" for x in out["pii_audit"])
    assert out["effects"] == ["aci_search", "aci_edit"]
    assert out["lease_destroyed"] is True

    fuse = JobSupervisor(
        FallbackChain(
            StaticClient(
                "vision",
                [ModelTurn("shot", "screenshot", {}, ToolClass.READ_ONLY, 0.5) for _ in range(8)],
            ),
            StaticClient("unused", []),
            CircuitBreaker(failure_threshold=5),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=1.0,
        env_ttl_s=24.0,
        max_turns=8,
    )
    capped = fuse.run("overnight GUI", tenant="t1", pool_type=PoolType.COMPUTER_USE_VM)
    assert capped["status"] == "failed"
    assert "maxBudgetUsd" in (capped["error"] or "")
    assert capped["lease_destroyed"] is True

    killer = JobSupervisor(
        FallbackChain(
            StaticClient("opus-aci", _aci_turns()),
            StaticClient("sonnet-aci", _aci_turns()),
            CircuitBreaker(),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    killed = killer.run("goal", tenant="t1", kill_after_step=1)
    assert killed["status"] == "cancelled"
    assert killed["lease_destroyed"] is True

    expired = JobSupervisor(
        FallbackChain(
            StaticClient("opus-aci", _aci_turns()),
            StaticClient("sonnet-aci", _aci_turns()),
            CircuitBreaker(),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    dead = expired.run("goal", tenant="t1", expire_lease=True)
    assert dead["status"] == "failed"
    assert "TTL" in (dead["error"] or "") or "destroyed" in (dead["error"] or "")

    pay = JobSupervisor(
        FallbackChain(
            StaticClient(
                "cua",
                [ModelTurn("pay", "click_pay", {"amt": 12}, ToolClass.IRREVERSIBLE, 0.03)],
            ),
            StaticClient("unused", []),
            CircuitBreaker(),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    denied = pay.run("buy", tenant="t1", pool_type=PoolType.BROWSER_FARM)
    assert denied["status"] == "failed"
    assert "irreversible" in (denied["error"] or "")
    allowed = JobSupervisor(
        FallbackChain(
            StaticClient(
                "cua",
                [
                    ModelTurn("pay", "click_pay", {"amt": 12}, ToolClass.IRREVERSIBLE, 0.03),
                    ModelTurn(json.dumps({"status": "completed"}), None, {}, ToolClass.READ_ONLY, 0.03),
                ],
            ),
            StaticClient("unused", []),
            CircuitBreaker(),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    paid = allowed.run(
        "buy", tenant="t1", pool_type=PoolType.BROWSER_FARM, confirm_irreversible=True
    )
    assert paid["status"] == "completed"
    assert paid["effects"].count("click_pay") == 1

    degrade = JobSupervisor(
        FallbackChain(
            StaticClient("dead", [], fail=TransientError),
            StaticClient("also_dead", [], fail=TransientError),
            CircuitBreaker(failure_threshold=1),
            **retry,
        ),
        EnvPool(),
        ToolProxy(),
        max_budget_usd=4.0,
        env_ttl_s=24.0,
    )
    human = degrade.run("goal", tenant="t1")
    assert human["status"] == "needs_human"
    assert human["model"] == "degraded"

    print(
        json.dumps(
            {
                "ok": True,
                "happy_steps": out["checkpoint"]["step"],
                "capped": capped["status"],
                "killed": killed["status"],
                "degraded": human["status"],
                "pay_once": paid["effects"].count("click_pay"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _demo()
```

**Behavior encoded (maps to §§2–4):**

- Frozen `goal_hash`; resume rejects goal or env-generation mismatch.
- Overnight fuse is `min(maxBudgetUsd, env TTL, ScheduleToClose, vendor quota)`; first trip fails the job and **destroys the lease**.
- Kill switch trips without the model: `Cancel` analog + `pool.destroy`.
- Irreversible `click_pay` is deny-on-timeout unless HITL `confirmed`; replay hits the idempotency map so resume cannot double-charge.
- Primary 429-class `TransientError` + jittered retry; breaker closed→open; secondary; dual failure emits schema-valid `needs_human`.
- Continue-As-New every `CAN_EVERY_STEPS` compacts obs; checkpoint dict is the durable artifact, not the screenshot stack.
- PII detect→redact→audit before the loop. Correlation-id JSON logs on every stop.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rules: **ACI in a disposable sandbox is the default overnight coder**; **pixels are the long tail when there is no API**; **the control plane must stop the data plane without the model**; **overnight fuse = min(maxBudgetUsd, env TTL, ScheduleToClose, vendor quota)**; **gate prod on binary success + safety report, not leaderboard partial**.

### Scenario 1 — Overnight SWE worker (repo → PR)

**Problem statement.** Unattended 4–12 h issue resolution on an internal monorepo. SWE-bench Pro (2025): GPT-5 **23.3%** public Pass@1, Opus 4.1 **22.7%**, commercial best **17.8%** — enterprise codebases are harder, not just “more files.” Gold patches mean **107.4** LOC / **4.1** files. Failures cost more than successes (SWE-agent **$2.52** fail vs **$1.21** success, **$4** cap, 2024 GPT-4 Turbo). The job must survive worker death without re-running `git push` or tests that mutate staging. Computer-use on a desktop is the wrong interface (Operator failed terminal/OCR; visual edits looped to a 400-step cap). HIPAA/ZDR: do not put this on Managed Agents. METR 50% horizon is not the SLO; use the **80%** trend (~5× shorter).

**Proposed architecture.**

```
┌────────────┐  Signal/cron  ┌───────────────────────────────────────────────────┐
│ Ticket Q / │──────────────▶│ CONTROL PLANE                                     │
│ GitLab id  │               │ Temporal Workflow-Id = tenant:issue               │
└────────────┘               │ frozen goal artifact + fail2pass tests            │
                             │ maxBudgetUsd · maxTurns · ScheduleToClose         │
                             │ kill switch: Cancel + destroy sandbox + PAT revoke│
                             │ Activities: LLM (attempts=1) · ACI tools          │
                             │ Continue-As-New every 100–1,000 steps             │
                             └────┬─────────────────────────────┬────────────────┘
                                  │ lease                       │ HITL Signal
                                  ▼                             ▼
                             ┌─────────────────┐         ┌──────────────────────┐
                             │ ENV POOL        │         │ Watch / confirm      │
                             │ disposable repo │         │ irreversible: merge  │
                             │ sandbox (E2B    │         │ to default, prod     │
                             │ Pro 24 h, not   │         │ migrate, secrets     │
                             │ Hobby 1 h)      │         └──────────────────────┘
                             │ ACI: search /   │
                             │ viewer / editor │
                             └────────┬────────┘
                                      │
                                      ▼
                             ┌───────────────────────────────────────────────────┐
                             │ WORM: workflow_id, principal, tool hash,          │
                             │ checkpoint graders, $/success vs $/fail           │
                             └───────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Computer use on a logged-in desktop (Operator / Cowork Dispatch) | B. Recommended: ACI in disposable repo sandbox + Temporal + fail2pass process credit + `maxBudgetUsd` | C. Managed Agents / ChatGPT agent as the overnight coder |
| --- | --- | --- | --- |
| Cost | Vision tax; **[inferred] ~$1.4–$9**/job (50 vs 318 turns) plus desktop must stay awake | Paper **$1–4**/issue era; ⚠️ 2026 thinking models cost more; budget the **fail tail** | Tokens + vendor sandbox; ChatGPT **40–400** msgs/mo is a quota cliff, not $/task |
| Latency | p99 = OCR/visual-edit collapse (400-step cap); Dispatch is not unattended if lid closed | p50 on short successes (**12** steps); p95 is fail-slow (**21** steps) bounded by cap | Vendor session SLO unpublished; no `wait_condition` you own |
| Ops | Desktop awake; no `-p`; permissions persist | Warm sandbox images; CAN; env generation == workflow generation | Least ops; you do not own harness or session log format |
| Security | Entire GUI blast radius; passwords in takeover VM | Repo + tests; PAT via proxy; bash sandbox ≠ MCP/file tools unless whole process boxed | Managed Agents **not** ZDR/HIPAA; credentials out of sandbox but sessions stateful |
| Scalability | One desktop ≠ a pool | E2B Pro **100** concurrent (buy to **1,100**); lease hours / wall hours | Vendor concurrency + message quota |

**Decision rationale.** **B** is research §6.1: ACI in a **disposable** repo sandbox + Temporal + fail2pass tests as process credit. Computer-use is a fallback when the IDE/GUI has no API — not the default overnight coder. A fails the unattended requirement (Claude Code computer use is interactive-only; Cowork desktop must be awake) and puts OCR on a code path that SWE-agent already solved with a file viewer. C is the wrong compliance box (Managed Agents ineligible for ZDR/HIPAA) and the wrong stop condition (vendor quota is not your kill switch — pair it with an external Temporal watchdog if you must use it). Interview close: “Raising the SWE-agent **$4** cap is a weak lever — 93% of resolved runs already submit early. Cap the fail tail, checkpoint the tests, and make merge irreversible-with-HITL.”

### Scenario 2 — Computer-use RPA on internal web (no API)

**Problem statement.** Multi-hour internal-web workflows with no public API (legacy HRIS / procurement). Human median on OSWorld 2.0-class work is **~1.6 h** and **~318** tool calls; 2026 leader **20.6%** binary / **54.8%** partial at 500 steps — do not promise “automate the analyst’s afternoon” off the leaderboard. WebArena GPT-4 was **14.41%** vs human **78.24%**; CUA later **58.1%** with pixels — scaffold + model. Checkout, email, and payroll are irreversible. Prompt injection from on-screen instructions is in-scope (Operator extra monitor; Anthropic screenshot classifiers; HITL-free opt-out is a support ticket). Browser cookies in a farm are **prod credentials in a pool**. Sim: WebArena-style clone. Prod: allowlisted host + dedicated `contextId` per tenant.

**Proposed architecture.**

```
┌────────────┐   cron/Signal  ┌──────────────────────────────────────────────────┐
│ RPA ticket │───────────────▶│ CONTROL PLANE                                    │
│ + allowlist│                │ Temporal Workflow + overnight fuse (4 knobs)     │
│ hostnames  │                │ Watch Mode class: purchase / email / payroll     │
└────────────┘                │ deny-on-timeout; classifier pause → pager        │
                              │ MCP PEP: OAuth 2.1 + RFC 8707 audience           │
                              │ kill: Cancel + TTL contextId + revoke refresh    │
                              └────┬──────────────────────────┬──────────────────┘
                                   │                          │
                                   ▼                          ▼
                              ┌────────────────┐       ┌─────────────────────────┐
                              │ DATA PLANE     │       │ HITL / Watch Mode       │
                              │ Browser farm   │       │ user take-over of env   │
                              │ Stagehand MCP  │       │ (model never sees pw)   │
                              │ act/observe/   │       └─────────────────────────┘
                              │ extract first  │
                              │ pixels only on │
                              │ the long tail  │
                              │ per-tenant     │
                              │ contextId TTL  │
                              └───────┬────────┘
                                      │ computer / browser toolset
                                      ▼
                              ┌──────────────────────────────────────────────────┐
                              │ Recordings = audit AND PII store · WORM task_id  │
                              │ Sim gym (WebArena clone) ≠ prod SSO cookies      │
                              └──────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Playwright/BrowserGym selectors only (classic RPA) | B. Recommended: DOM/MCP (Stagehand + Browserbase) first; Anthropic computer + **browser** toolset for the long tail; Watch Mode on checkout | C. OpenAI CUA / ChatGPT agent as the generalist overnight operator |
| --- | --- | --- | --- |
| Cost | Engineer time on selectors; cheap at runtime | Browser hours + LLM; batch actions cut round trips; **[inferred] ~$1.4k/1k** at 50 vision turns vs **~$9k/1k** at 318 | Included in quota; extra via credits; not a $/task NFR |
| Latency | High if DOM stable; brittle on redesign | Self-healing NL `act`/`observe`; p99 still 500-step / injection pause | Strong product HITL; unpublished job SLO; 400-step autonomy eval timeout |
| Ops | Classic RPA maintenance | Farm recordings; per-tenant context TTL on Cancel; sim→prod allowlists | Least harness ops; you do not own the VM; `operator.chatgpt.com` already sunset |
| Security | No model on the DOM; still SSO cookies | Classifier HITL; RFC 8707; recordings are PII; no secrets in GUI | Watch Mode on email-class sites; High bio residual if connectors + login takeover; disable unused connectors |
| Scalability | Engineer bottleneck | Sessions, not vCPU; keep-alive expensive if re-login every resume | Vendor message quota **40 / 400**; not a pool you size |

**Decision rationale.** **B** is research §6.2: DOM/MCP first; pixels for the long tail; never skip confirmation on checkout. Sim is a WebArena clone of the internal app; prod is allowlisted host + dedicated browser context per tenant. A wins when the DOM is stable and the cost of engineers is lower than vision tokens — keep it as the **fast path**, promote to Stagehand only when selectors rot. C is the reference topology for a **generalist** (one VM, visual+text+terminal+connectors, interruptible, scheduled recurrence) but treat it as **High** residual risk if connectors + login takeover are on; vendor quota is not your kill switch — add an external Temporal watchdog. Interview close: “Same MCP schema in sim and prod is the feature and the footgun. Promote allowlists, credentials, and irreversible-action policy independently. If Cancel cannot destroy the `contextId` and revoke the refresh token, it is not an overnight worker.”

---

*End of module. Six sections. Three mandatory topics (autonomous agents, long-horizon tasks, agent environments). `$ / 1k` tables use Anthropic Opus 4.8 **$5/$25** MTok and E2B **$0.000028/s** (2 vCPU = **$0.1008/h**) with **[inferred]** loop shapes (50 vision turns → ~$1.4k/1k; 318 vision turns → ~$9k/1k; SWE-agent paper **$1.2k–$2.5k**/1k). No unpublished production job-latency p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented mechanics (OSWorld 2.0 **150/300/500** step budgets; SWE-agent **12 vs 21** steps; GAIA human **6.8–17.7 min**; E2B Hobby **1 h** / Pro **24 h**; Temporal **51,200** events / **50 MB**).*
