# Module 17: Advanced Autonomous Agents -- Long-Horizon Tasks, Agent Environments, and Self-Improvement

## What Is This?

Most agents today are **short-lived** — they handle a single user request in seconds or minutes (summarize this document, answer this question, fix this bug). **Autonomous agents** are different: they work for **hours or days** without human intervention, tackling complex, multi-step tasks independently.

Think of the difference like this: a short-lived agent is like asking a colleague a question and getting an answer in 5 minutes. An autonomous agent is like assigning a project to a remote contractor who works overnight and delivers results in the morning.

Examples of autonomous agent tasks:
- Migrate a 500-file codebase from Python 2 to Python 3 (takes hours)
- Research a market, analyze competitors, and write a 20-page report (takes a day)
- Monitor a production system, detect anomalies, and fix common issues (runs continuously)

Why is this harder than short-lived agents?
- **Error accumulation**: A 10-second agent can crash and retry cheaply. A 10-hour agent has accumulated state, side effects (files written, APIs called), and costs ($50+) that can't be easily undone.
- **Checkpoint & resume**: The agent must save its progress so it can resume after crashes, deployments, or human interruptions — you can't restart a 6-hour task from scratch.
- **Safety**: A short-lived agent can ask for human approval before every action. An autonomous agent running overnight can't wait for approval — it needs pre-defined safety boundaries, kill switches, and spending limits.
- **Environment management**: Long-running agents need persistent sandboxes (VMs, containers) that maintain state across steps — unlike short-lived agents that use ephemeral environments.

## Why It Matters

Autonomous agents represent the frontier of AI capabilities — the transition from "AI as a tool" to "AI as a worker." Building them reliably requires solving hard problems in checkpointing, safety, cost control, and environment management that don't arise with simpler agents.

---

## 2. Core Concepts

### Bounded Autonomy (Not Binary)

Autonomy is not on/off. It is an explicit data structure representing what an agent may do. The production design is **bounded autonomy**; "run until done with every tool" is an absent design.

**Autonomy envelope** (represent authority as data):

```yaml
run:
  principal: tenant/acme/user/42
  objective: "Upgrade service X to runtime Y and preserve behavior"
  environment: repo-x@commit:abc123 + test-image@sha256:...
  allowed_actions:
    - repo.read
    - worktree.write:/services/x/**
    - test.run:nonprod
  denied_actions:
    - main.merge
    - production.deploy
    - secret.read
  approvals:
    external_write: required
    destructive: required
  budgets:
    wall_clock: 8h
    model_tokens: 8_000_000
    tool_calls: 2000
    spend_usd: 100
  stop:
    success: "required checks pass and verifier accepts diff"
    failure: "budget exhausted or no progress across 3 replans"
```

**Dimensions of autonomy** (each is independent):

| Dimension | Examples | Enforcement point |
|---|---|---|
| Objective | One ticket, one research question, one account operation | Coordinator + verifier |
| Data scope | Tenant, repository paths, records, time range | Data/tool authorization |
| Action scope | Read, draft, mutate sandbox, external write, irreversible act | Capability + action broker |
| Resource scope | Tokens, calls, compute, storage, money | Admission + metering |
| Temporal scope | Start/expiry, deadline, maintenance window | Coordinator + credential expiry |
| Destination | Domains, APIs, branches, recipients, regions | Egress/tool policy |
| Concurrency | Workers, parallel branches, outstanding writes | Scheduler + quotas |
| Escalation | Which exact actions require which approver | Approval service |

Autonomy can increase for **reversible, observable, low-impact** actions and decrease as impact, ambiguity, or irreversibility grows. A code agent may freely edit a worktree and run tests, require review to open a PR, and remain unable to merge or deploy.

### Four Architectural Concerns

The practical architecture separates:

1. **Goal/control**: What outcome, constraints, authority, budget, and stop conditions govern the run?
2. **Cognition**: How does the model plan, act, reflect, retrieve state, and adapt?
3. **Environment**: Which versioned state and actions exist, and what effects do they have?
4. **Evidence/governance**: How are progress, policy decisions, side effects, quality, and human approvals independently recorded and verified?

### Two Planes, Three Clocks, One Environment Lease

| Plane | What it is | Clock | Typical store | Failure if mixed |
|---|---|---|---|---|
| **Control** | Job supervisor, Temporal workflow, Managed Agents session, `maxTurns`/`maxBudgetUsd`, kill switch, env-pool allocator | Durable-execution clock (event history; SSE session; cron) | Temporal persistence / Managed Agents event log / orchestrator DB | HTTP timeout killing a 3h job; KEDA scaling away the worker holding the VM lease |
| **Data (tokens)** | Screenshots, accessibility trees, tool results, condensed conversation, skill embeddings | Token/context clock (compaction, prompt cache TTL) | Model context + cache | Replaying a 200-screenshot history as a fresh prompt without compaction -> context blow-up |
| **Data (side effects)** | VM disk, browser cookies, git working tree, MCP task IDs, purchases, emails | Environment clock (VM TTL, cookie policy, MCP task `ttl`) | Sandbox / browser farm / production SaaS | Retrying the *workflow* re-clicks "Place order" because the LLM call was retried |

**Key insight**: The environment lease is the unit of scheduling, not the HTTP request.

### The Reliability Decay Problem

If a task needs `n` independently correct irreversible steps with per-step correctness `p`, naive completion probability is `p^n`. At `p=0.99`, 100 steps yield ~36.6%; at `p=0.999`, ~90.5%. Real steps are not independent, and errors can be detected or repaired. But this explains why single-call accuracy optimization is insufficient: long-horizon architecture must reduce irreversible steps, verify milestones, retry safely, and recover from errors.

---

## 3. How It Works

### 3.1 Bounded-Autonomy Topology

```text
 user / scheduler / upstream workflow
                 |
     goal contract + principal identity
     scope, success predicate, deadline,
     spend/action limits, approval policy
                 |
       durable run coordinator ---------------- policy/control plane
       lease, checkpoint, cancel, resume         versioned policies
                 |                               capability issuer
        planner / state estimator                artifact registry
          /       |       \                      eval/release gates
     executor  verifier  observer
          |        |        |
      action broker / policy enforcement point -------- audit/evidence ledger
          |
  +-------+------------------+------------------+
  |                          |                  |
 sandbox/code env       web/desktop env     enterprise APIs
 snapshot + limits      browser/VM state    transactional state
  |                          |                  |
  +---------------- versioned environment ------+
                 |
        receipts + state deltas + errors
                 |
       checkpoint / replan / approve / stop
```

The **control plane** owns policies, capability templates, model/tool/environment releases, quotas, evaluation thresholds, and revocation. The **execution plane** owns one run's observations, actions, checkpoints, environment lease, receipts, and terminal state. Policy enforcement sits outside model text: the model may propose an action, but a deterministic action broker authenticates the run, validates schema, evaluates policy, reserves budget, binds approval, invokes the tool, and records the result.

### 3.2 Shipped Products: The Control vs Data Plane in Practice

**OpenAI ChatGPT Agent** (2025-07-17): Runs on its own virtual computer that preserves context across a visual browser, text browser, terminal, and connector APIs. The model chooses the path; the VM is the data plane. Quotas: Pro 400 agent messages/month, other paid 40, extra via credits. Standalone Operator (`operator.chatgpt.com`) was folded in and sunset.

**OpenAI CUA / Operator** (2025-01-23): CUA combines GPT-4o vision with RL. Loop: screenshot -> chain-of-thought over current+past screenshots -> click/scroll/type until done or user input needed. Confirmations for logins and CAPTCHAs. Benchmarks: OSWorld 38.1% (prev SOTA 22.0%; human 72.4%), WebArena 58.1% (prev 36.2%; human 78.2%), WebVoyager 87%. Test-time scaling: more allowed steps raises OSWorld.

**OpenAI Responses API computer tool**: `computer-use-preview` is the specialized model; docs migrate the tool onto frontier models with a first-party `computer` tool. Custom harnesses must keep Playwright `browser/context/page` alive across steps. Language-level sandboxes (`vm`, restricted Python globals) are **explicitly not** security boundaries.

**Anthropic computer use**: Public beta 2024-10-22 on Claude 3.5 Sonnet: OSWorld screenshot-only 14.9% (next-best 7.8%); 22.0% with more steps. As of 2026-08, Messages API ships GA `computer_toolset_20260801` (no beta header): 17 member tools (`screenshot`, `left_click`, `type`, `zoom`, ...), batch actions. Prompt-injection classifiers on screenshots steer the model to ask user confirmation; HITL-free loops require support contact to opt out. HIPAA-eligible computer use under BAA -- distinct from Managed Agents which is NOT ZDR/HIPAA eligible.

**Anthropic Agent SDK and Managed Agents**: Three SKUs: Messages API (you own the loop), Agent SDK (Claude Code's loop in your process with `maxTurns`/`maxBudgetUsd`), Managed Agents (Anthropic owns harness + sandbox + session log; sessions persist server-side).

**Claude Code / Cowork / Dispatch**: Computer use in the CLI is a built-in MCP server, off by default; interactive only (no `-p`). Desktop Cowork + Dispatch: assign from phone, require the desktop app awake. Overnight worker implication: phone-dispatch is NOT an unattended worker unless the desktop is up and permissions persist.

**Temporal + Agent Sandboxes**: Wraps `SandboxAgent` so LLM calls, sandbox lifecycle, and shell are Activities. `workflow.wait_condition` idles at zero compute between user messages. The control plane for overnight work.

### 3.3 The Autonomous Loop: Perceive -> Reason -> Act -> Stop

**Control loop with horizon management** (beyond basic ReAct):

```text
load goal + last accepted checkpoint
 -> observe authoritative environment state
 -> reconcile state with checkpoint and pending effects
 -> choose next milestone or request approval
 -> propose bounded action batch
 -> policy/budget/deadline admission
 -> execute and collect typed receipts/state deltas
 -> verify local postconditions and global invariants
 -> update evidence, progress and checkpoint
 -> continue, replan, wait, escalate, or terminate
```

ReAct interleaves reasoning and environment actions so observations can update a plan. That is a local loop, not sufficient long-horizon architecture. Add explicit milestones, state estimation, independent verification, durable checkpoints, and bounded replanning.

**Reflection** may improve subsequent attempts, but it must be grounded. Reflexion stores linguistic feedback in episodic memory. In production, self-critique is a hypothesis; a compiler, test, database predicate, simulator state, policy engine, or human review is evidence. Storing an agent's unverified explanation as memory can institutionalize its error.

**Use a receding-horizon plan**: Keep a coarse end-to-end dependency map, but commit only the next verifiable milestone. Replan after material state changes or failed assumptions. Freeze invariant constraints separately from mutable tactics so compaction and reflection cannot silently weaken them.

### 3.4 Safety Stop Conditions (Product, Not Slogans)

| Stop | Who implements it | Trigger |
|---|---|---|
| User confirmation / Watch Mode | OpenAI Operator & ChatGPT agent | Side-effecting actions (purchase, email); sensitive sites |
| Task refusal | CUA training + usage policy | Banking transfers, stocks, illicit goods; Operator 97% refuse on illicit-activity eval |
| Prompt-injection pause | Operator extra monitor model; Anthropic screenshot classifiers | Suspicious on-screen instructions |
| Spend / turn cap | Claude Agent SDK `maxTurns`, `maxBudgetUsd`; SWE-agent $4/instance auto-submit | Open-ended "improve the codebase" |
| Env TTL | E2B 1h Hobby / 24h Pro; MCP task `ttl` (ms) | Lease expiry |
| History limit | Temporal 51,200 events / 50 MB -> terminate unless Continue-As-New | Multi-hour tool spam |
| Human interrupt | ChatGPT agent take-over / pause / partial results | User steer |
| Kill switch | Your control plane (Temporal Cancel, session delete, sandbox kill) | Policy / SOC |

**Overnight cost cap** (production pattern): `maxBudgetUsd` + env TTL + Temporal `ScheduleToClose` on the workflow + provider message quota. Without all four, a looping computer-use agent is an unbounded image-token meter.

### 3.5 Lineage: Self-Starting Exploration vs Contracted SWE Loops

**Voyager** (Wang et al., 2023): Lifelong Minecraft agent with: (1) automatic curriculum ("discover as many diverse things as possible"), (2) skill library of executable JavaScript indexed by description embeddings, (3) iterative prompting with env feedback + interpreter errors + GPT-4 self-verification. Code is the action space, not pixels. Results vs ReAct/Reflexion/AutoGPT: 3.3x unique items, 2.3x distance, wood tools 15.3x faster; only Voyager unlocks diamond. Ablation: random curriculum -> -93% items; skill library prevents late-stage plateau. Stuck after 4 refinement rounds -> ask curriculum for new task. Production lesson: **promote successful traces into typed skills, not chat summaries.**

**Generative Agents** (Park et al., UIST 2023): 25 agents in Smallville. Architecture: memory stream of NL observations; retrieval = recency + relevance (cosine) + importance (LLM 1-10); reflections when recent importance sum > 150 (~2-3x/day). Emergent: information diffusion, relationship memory, Valentine's party coordination. Failure mode: retrieval of *wrong* memories, not missing a tool API. This is the long-horizon **memory** paper; it is not a computer-use paper.

**AutoGPT lineage**: Classic AutoGPT = goal -> subgoals -> ReAct loop, no durable env, no ACI. GAIA evaluated AutoGPT at 14.4% Level 1, 0.4% Level 2, 0% Level 3 vs humans 93.9 / 91.8 / 87.3. 2026 AutoGPT is a hosted platform; the research meaning is still the unsupervised decomposer.

**SWE-agent ACI** (Yang et al., NeurIPS 2024): Purpose-built agent-computer interface -- search, file viewer, editor, context manager -- on a Linux shell. GPT-4 Turbo: 12.47% SWE-bench, 18.00% Lite; 64% relative gain vs shell-only. Per-instance cap $4; successes finish earlier (median $1.21/12 steps) than failures (mean $2.52/21 steps); 93% of resolved runs submit before budget exhaust vs 69% overall. **Interface design IS the capability.** Raising the cap is a weak lever.

**OpenHands**: Stateless single-step agent: each `step()` reads event history, optionally condenses, queries LLM, then executes or waits. Supervisor pattern holds the overall plan and interrupts subordinates that run too long.

### 3.6 MCP as Environment (Not Just a Tool Plugin)

MCP 2025-11-25 authorization: servers are OAuth 2.1 resource servers; clients MUST use RFC 9728 Protected Resource Metadata; tokens MUST carry RFC 8707 resource indicators bound to the canonical MCP URI.

**Experimental Tasks** (SEP-1686): Any request can return a durable state machine. States: `working` -> `input_required` | `completed` | `failed` | `cancelled`; `ttl` in milliseconds; poll `tasks/get`, block on `tasks/result`, cancel idempotently. Tools declare `execution.taskSupport`: `required` | `optional` | `forbidden` (default forbidden).

**MCP is an environment ABI**: The same agent can be pointed at a sim gym, a cloud browser, or prod Salesforce without changing the loop -- which is exactly the sim-to-prod footgun.

**OSWorld-MCP**: 158 MCP tools (7 apps; 25 distractors; RAG-selected because 158 tools blow context). 69% of 250 tasks are tool-beneficial; OpenAI o3 8.3% -> 17.6% at 15 steps.

### 3.7 Agent Environment Contract

The minimal Gymnasium contract is `reset(seed, options) -> observation, info` and `step(action) -> observation, reward, terminated, truncated, info`. `terminated` means the task reached an end state; `truncated` means an external limit ended the episode.

**Environment pools** -- four commercially distinct types:

| Pool | Observation | Action | Isolation | Typical TTL |
|---|---|---|---|---|
| **Gym / eval farm** | Gymnasium `reset/step`; BrowserGym DOM+a11y+screenshot | Discrete / browser primitives | Docker per episode | Episode (minutes) |
| **Code sandbox** | Files + stdout | bash / Python | E2B, Daytona, Modal, Runloop, Anthropic sandbox-runtime | 1h Hobby / 24h Pro (E2B) |
| **Computer-use VM** | Screenshot (+ optional a11y) | Mouse/keyboard/17-tool toolset | Xvfb + you-owned desktop, or vendor VM | Session; cookies persist per site policy |
| **Browser farm** | DOM / Stagehand observe | Click/type or CUA pixels | Browserbase, Steel, Playwright grid | Keep-alive session + context ID |

**Production environments need** (beyond Gymnasium):

1. **Identity/version**: environment name, image/data/task/grader digest, source provenance
2. **Initial state/reset**: seeded fixture, snapshot, account identities, cleanup guarantees
3. **Observation**: schema, partial observability, freshness, adversarial trust label
4. **Action**: typed schema, preconditions, authority, idempotency, effect class, timeout, receipt
5. **Time**: logical vs wall clock; independent events; leases; deadline
6. **Concurrency**: other agents/users, ordering, conflict rules, isolation level
7. **Lifecycle**: running, waiting, terminated-success, terminated-failure, truncated-budget/time, cancelled
8. **Scoring**: hidden/public predicates, partial credit, safety violations, evaluator access
9. **Snapshot/fork**: what state is captured, external exclusions, uniqueness reset
10. **Network/data**: egress allowlist, credentials, retention and destruction

### 3.8 Sim-to-Prod Gap

| Benchmark | Setup | Agent Score | Human Score | Key Insight |
|---|---|---|---|---|
| **OSWorld** (2024) | 369 tasks, real OS (Ubuntu/Win/Mac), execution-based graders | Best agent 12.24% (2024); CUA later 38.1% | 72.36% | Real OS, not a toy gym |
| **OSWorld 2.0** | 108 long-horizon workflows, ~318 tool calls, human median ~1.6h | Leader 20.6% binary / 54.8% partial (Opus 4.8, 500 steps) | ~1.6h median | Do not mix v1 and v2 scores |
| **WebArena** | 812 tasks on self-hosted sites | GPT-4 14.41% -> CUA 58.1% | 78.24% | Scaffold + model, not easier web |
| **TheAgentCompany** | 175 professional tasks, GitLab+OwnCloud+Plane+RocketChat | Gemini 2.5 Pro 30.3% full / 39.3% partial | Professional baseline | Enterprise complexity beyond benchmarks |
| **SWE-bench Pro** | 1,865 problems, 41 repos, gold patches mean 107.4 LOC / 4.1 files | GPT-5 23.3% public, Opus 4.1 22.7%; commercial best 17.8% | Human-confirmed | Enterprise codebases harder than public repos |
| **GAIA** | Multi-step reasoning + browsing + tools | GPT-4+plugins 15%; Level 3 = 0% | 92% | "Arbitrarily long sequences of actions" |

**Sim-to-prod rule**: Execution-based graders on a resettable intranet are not SSO + flaky third-party + irreversible money.

### 3.9 Self-Starting and Overnight Workers

Four distinct "it runs while you sleep" topologies -- do not conflate them:

| Pattern | Who starts it | Who must stay awake | Durable wait | Example |
|---|---|---|---|---|
| **Interactive computer use** | User in session | Desktop/CLI | Process RAM | Phone-dispatch is NOT a worker unless host is up |
| **Scheduled consumer agent** | Cron / "repeat this task" | Vendor VM | Vendor session | ChatGPT agent weekly metrics |
| **Durable workflow + sandbox** | Temporal Schedule / signal | Worker fleet (can scale to zero while waiting) | Event history; `wait_condition` = 0 activity CPU | OpenAI Agents SDK + E2B |
| **Eval episode** | Harness `reset()` | Gym node until `terminated`/`truncated` | None (by design) | OSWorld / WebArena |

Self-starting in the AutoGPT sense (agent proposes the next goal) is **curriculum**, not orchestration. Voyager's automatic curriculum is the reference; production analog is a ticket queue, not a novelty search. If the overnight job can mint its own tickets, you have unbounded spend AND unbounded scope.

**Supervisor split that scales**: Control plane = one Workflow per job (goal contract, budget, kill switch, Continue-As-New). Data plane pool = warm sandbox images. Do not put the LLM call inside the Workflow function (breaks determinism); do not put `rm` inside an Activity without idempotency and a confirm Signal.

---

## 4. Key Patterns & Best Practices

### Durable Resumability Across Context Windows

Context continuation, application checkpointing, and environment snapshotting solve different problems:

| Mechanism | Preserves | Does not prove |
|---|---|---|
| Model conversation/compaction | Task-relevant conversational state | External effects or full semantic fidelity |
| Agent checkpoint | Explicit goal, plan, facts, evidence, budgets | Live environment still matches |
| Workflow event history | Durable control decisions/results | Activities were externally exactly once |
| Environment snapshot | Filesystem/VM/application state at a point | External SaaS/database state or current auth |
| Artifact commit | Durable output version | Task success or policy compliance |

**Semantic checkpoint contents**:

```text
run/attempt ID, principal, signed objective and invariant constraints
environment/task/artifact/policy/model/tool/schema versions
last authoritative state digest and logical/environment clock
completed milestones with evidence and verifier status
current plan/dependencies and rejected hypotheses
working artifacts/commits and resumable sandbox/snapshot reference
issued actions: idempotency key, request, receipt, postcondition
pending/ambiguous effects and reconciliation procedure
remaining token/call/time/spend budget and deadline
active capabilities, expiry, approvals and revocations
memory facts with provenance, confidence, valid-from/to
terminal/next-state reason
```

**On resume**: Never blindly continue from prose. Acquire/fence the environment lease, re-authenticate, re-evaluate policy and budget, inspect every ambiguous action, compare current state to saved digest, invalidate stale observations/plans, then continue or replan.

**Memory is not checkpoint**: Generative Agents retrieve; Voyager promotes code; SWE-agent truncates history; Claude Code compacts. A resume that restores tokens but not the VM (cookies, `node_modules`, failed migration) is a new task wearing the old goal.

### Checkpoint / Interrupt / Resume Product Map

| System | Checkpoint | Interrupt | Resume |
|---|---|---|---|
| ChatGPT agent | VM state + tool mix | Take over browser, pause, stop -> partial results | Continues with new instructions without losing progress |
| Operator CUA | Screenshot history in model context | Confirmation / Watch Mode | User provides input; loop continues |
| Claude Agent SDK | Sessions; `--resume` / JSONL transcripts | Permissions, hooks | Session id |
| Managed Agents | Server-side event history + sandbox FS | User events mid-execution | Session; scheduled cron deployments |
| OpenHands TaskToolSet | Conversation saved to disk | Parent blocks on sub-agent | `resume` + task id |
| Voyager | Skill library + Chroma | 4-round stuck -> new curriculum task | Skills transfer to a new world |
| Generative Agents | Memory stream + reflections | n/a | Retrieval, not VM resume |
| MCP Tasks | `task_id` + server TTL | `tasks/cancel` | Poll after disconnect |
| Temporal + sandbox | Activity results + workspace snapshot | Cancel / signal | Replay; `/switch` provider with portable snapshot |

### Three Wait States (First-Class)

Long jobs spend most of their calendar time waiting, not decoding. Treat waits as first-class states:

| Wait | Meaning | Resume token | Failure if ignored |
|---|---|---|---|
| **Model-requested HITL** | CUA confirmation, Watch Mode, injection classifier, `input_required` | User Signal / event | Silent stall overnight |
| **Tool-async** | MCP Task `working`; CI; compile | `task_id`; poll `tasks/get` | HTTP timeout; lost audit join |
| **Idle durable** | Temporal `wait_condition`; Managed Agents pause; ChatGPT between runs | Workflow Id | Billing a GPU for sleep; losing sandbox TTL while workflow thinks lease is live |

**Interrupt hierarchy** (safest first): (1) user take-over of the environment (ChatGPT browser -- model never sees passwords), (2) workflow Cancel (does not automatically roll back completed purchase Activity), (3) sandbox kill (drops unsynced FS), (4) token revoke (stops next MCP call, not in-flight click).

### Credit Assignment on Long Horizons

*Let's Verify Step by Step* (Lightman et al., 2023): Process-supervised reward models (PRMs) beat outcome-supervised RMs on MATH; PRM800K = 800k step labels / 75k solutions / 12k problems. PRM score = product of per-step correctness -- one bad step kills the trajectory. Active learning 2.6x data-efficient.

Map to agents: TheAgentCompany checkpoints and OSWorld 2.0's ~27.25 scoring checkpoints/task are the productized form of process credit. Voyager's critic is an LLM-as-process-supervisor with a boolean gate into the skill library. SWE-bench Pro clusters failures: large models fail semantic/algorithmic multi-file edits; small models fail syntax, tools, context. Outcome-only RL on "tests passed" will reinforce lucky patches.

Production: Store per-checkpoint telemetry (pass/fail, $, turns). Do not assign the entire overnight bill to the final `submit`.

### Horizon Drift Detection

Detect goal/constraint divergence over time using:

- Goal/constraint restatement compared structurally with signed original
- Milestone completion predicates and remaining-work graph
- Repeated actions, edit/revert cycles, revisited URLs, and tool-argument similarity
- Progress velocity and verifier delta per token/action/wall-clock interval
- Plan churn without new environment evidence
- Contradiction between checkpoint claims and authoritative state
- Scope/permission requests expanding after setbacks
- Summary or memory facts without source/receipt provenance
- Growing fraction of actions devoted to recovering from agent's own changes

### Governance by Autonomy Tier

| Tier | Authority | Required evidence |
|---|---|---|
| 0 advise | No tool effect | Answer quality/privacy eval |
| 1 observe | Read scoped systems | Access audit, injection/privacy test |
| 2 act in sandbox | Reversible isolated changes | Sandbox escape, correctness, resource tests |
| 3 draft external | Creates reviewable artifact/request | Provenance, diff/effect preview, approval binding |
| 4 bounded external commit | Narrow reversible transaction | Policy, idempotency, canary, reconciliation, human override |
| 5 high-impact/irreversible | Exceptional, multi-party authorization | Formal risk acceptance, independent verification, recovery proof |

Increase authority only from production evidence at the lower tier; model benchmark improvement does not automatically widen deployed permissions.

---

## 5. System Design Considerations

### 5.1 Scenario A: Overnight SWE Worker (Repo -> PR)

**Goal**: Unattended 4-12h issue resolution.

| Option | Horizon fit | $ | Resume | Blast radius |
|---|---|---|---|---|
| SWE-agent / OpenHands + Docker | SWE-bench Pro <25% Pass@1 | Paper $1-4/issue era; 2026 thinking costs more | Disk conversation / Temporal | Repo + tests |
| Claude Agent SDK in cluster | Same ACI class; `maxBudgetUsd` | Tokens + your GPU/CPU | Session | As above |
| Managed Agents | Hours; Anthropic sandbox | Tokens; no ZDR | First-class | No local FS; MCP only |
| Computer use on a desktop | Wrong interface (Operator failed terminal/OCR) | Vision tax | Weak | Entire GUI |

**Pick**: ACI in a disposable repo sandbox + Temporal + fail2pass tests as process credit. Computer-use is a fallback when the IDE/GUI has no API -- not the default overnight coder.

### 5.2 Scenario B: Computer-Use RPA on Internal Web (No API)

| Option | Reliability | Oversight | Cost driver |
|---|---|---|---|
| Playwright/BrowserGym selectors | High if DOM stable | Classic RPA | Engineer time |
| Stagehand + Browserbase MCP | Self-healing NL actions | Farm recordings | Browser hours + LLM |
| Anthropic computer + browser toolsets | Batch actions; page structure on browser tool | Classifier HITL | Screenshots |
| OpenAI CUA / ChatGPT agent | Watch Mode on email-class sites | Strong product HITL | Quotas / vision |

**Pick**: DOM/MCP first; pixels for the long tail. Never skip confirmation on checkout. Sim: WebArena clone of the internal app; prod: allowlisted host + dedicated browser context per tenant.

### 5.3 Scenario C: Multi-Day Codebase Migration

**Goal**: Migrate a large service across thousands of files, preserve behavior, survive context/pod restarts, deliver a reviewable PR but never merge/deploy.

**Design**: Allocate an isolated worktree in a gVisor/microVM environment pinned to repo/base-image/dependency digests. Signed goal enumerates target runtime, in-scope paths, invariant tests, banned changes, and budgets. Each session loads the last semantic checkpoint, validates worktree commit and tests, completes one coherent milestone, commits, updates evidence, and exits. Parallel agents receive disjoint packages; a merge agent resolves only after ownership checks. Hidden tests and independent verifier gate completion. Network allows approved registries through proxy; no production or merge credentials exist.

**Failure exercise**: Kill before/after commit/checkpoint, change base branch while paused, corrupt a summary, revoke a package, inject instructions into a repository file, verify no main/production action is possible.

### 5.4 Scenario D: Generalist Overnight "Operator" (Research + Act)

ChatGPT agent is the reference topology: one VM, visual+text+terminal+connectors, interruptible, scheduled recurrence. Treat as High residual risk if connectors + login takeover are on. Disable connectors when unused. Cap with message quota AND an external Temporal watchdog (vendor quota is not your kill switch).

### 5.5 Scenario E: Multi-Hour MCP Tool (Migrate / Compile / Browser Job)

Use MCP Tasks (`taskSupport=required`) so the planner is not holding an HTTP socket for 30h. SIEM on `task_id`. `ttl` must exceed the job; `tasks/cancel` wired to the same kill switch as Temporal Cancel. Do not store migrate credentials in the sandbox.

### 5.6 Scenario F: Lifelong Skill Agent (Voyager-Shaped)

Skill library in vector DB with promotion gated by tests (Voyager critic). Curriculum is a cost amplifier (novelty search). Enterprise analog: approved runbooks, not open-ended "discover tools." Generative Agents memory is for simulation/UX personas, not for prod change management.

### 5.7 Scenario G: Long-Running Monitoring and Conditional Action

**Goal**: Watch a procurement portal for 7 days, then draft a response within 10 minutes; no autonomous purchase.

**Design**: Durable workflow registers event subscription or scheduled wake rather than occupying continuous model context. Low-cost observer extracts typed changes; policy engine checks qualification. Capable model prepares draft with cited evidence. Human approval binds exact response and destination. Terminate as success, expired/no-event, cancelled, or failed -- never "still thinking."

**Metrics**: Event recall/precision, reaction time, polling calls, idle compute, false actions, draft acceptance rate. Evaluate with time-compressed scripted timelines (SentinelBench-style).

### Architecture Trade-Off Matrix

| Choice | Strength | Cost/risk | Use when |
|---|---|---|---|
| One continuous context | Simple, full recent trace | Token growth, context limit/drift | Short bounded tasks |
| Compaction + semantic checkpoint | Spans context windows, inspectable state | Summary validation/migration | Hours/days of work |
| Durable workflow (Temporal) | Timers, replay, crash recovery | Determinism/activity discipline | External waits/effects |
| Event subscription | Low idle cost, fast response | Integration/trust complexity | Env supports reliable events |
| Polling | Universal | Cost, latency, churn/rate limits | No event source, bounded cadence |
| Browser replica | Realistic and resettable | Maintenance/version drift | Web behavior evaluation |
| Live production shadow | Highest realism | Privacy/safety/nondeterminism | After sandbox gates, read-only |
| Standard container | High density/compatibility | Shared kernel | Trusted code |
| gVisor | Stronger isolation, container workflow | Compatibility/performance | Untrusted common workloads |
| microVM (Firecracker) | Separate guest kernel | Operations/startup/memory | Hostile or cross-tenant code |
| Single agent | Coherent ownership | One perspective/bottleneck | Tightly coupled task |
| Parallel agents | Wall-clock/diversity | Token cost/conflicts/verification | Independent partitions |
| Self-verification | Cheap and contextual | Correlated blind spots | Low-impact interim feedback |
| Independent verifier/human | Lower correlated risk | Cost/latency/capacity | Milestones/high-impact effects |

### Trade-Off: Low Autonomy vs High Autonomy

| Axis | Low autonomy | High autonomy |
|---|---|---|
| **Loop owner** | Your Messages API | Managed Agents / ChatGPT agent |
| **Observation** | ACI / a11y / MCP | Pixels |
| **Credit** | Unit tests + checkpoints | Outcome-only "looks done" |
| **Memory** | Frozen skills + CAN | Unbounded chat log |
| **Env** | Resettable gym / sandbox | Logged-in prod browser |
| **Stop** | `maxBudgetUsd` + tests | Model "I'm finished" |
| **Audit** | Event history + principal | Screenshot dump |
| **HIPAA/ZDR** | Messages API computer use (BAA eligible) | Managed Agents: NO |
| **METR reading** | 80% horizon for SLOs | 50% horizon for research PR |

**Decision rule**: If the side effect is irreversible or PII-bearing, the control plane must be able to stop the data plane without the model's cooperation (Cancel + destroy lease + revoke token). If it cannot, it is a demo, not an overnight worker.

---

## 6. Code Examples

### Durable Agent Workflow with Temporal (Python pseudocode)

```python
@workflow.defn
class AgentWorkflow:
    """Durable supervisor for a long-horizon agent job."""

    def __init__(self):
        self.checkpoint = None
        self.budget_remaining_usd = 0
        self.cancelled = False

    @workflow.run
    async def run(self, goal: GoalContract) -> RunResult:
        self.budget_remaining_usd = goal.max_budget_usd

        # 1. Allocate environment (Activity -- non-deterministic)
        env_lease = await workflow.execute_activity(
            allocate_sandbox,
            args=[goal.environment_spec],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        try:
            while not self.cancelled:
                # 2. Check budget and history limits
                if self.budget_remaining_usd <= 0:
                    return RunResult(status="TRUNCATED_BUDGET")

                if workflow.info().get_current_history_length() > 10_000:
                    # Continue-As-New to avoid 51,200 event limit
                    workflow.continue_as_new(
                        args=[goal],
                        # Compact state, NOT full screenshot log
                    )

                # 3. Plan next milestone (Activity -- LLM call)
                plan = await workflow.execute_activity(
                    plan_next_milestone,
                    args=[goal, self.checkpoint, env_lease.id],
                    start_to_close_timeout=timedelta(minutes=2),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        maximum_attempts=1  # Temporal owns retry, not SDK
                    ),
                )

                if plan.is_complete:
                    # 4. Verify completion independently
                    verified = await workflow.execute_activity(
                        verify_completion,
                        args=[goal, env_lease.id],
                        start_to_close_timeout=timedelta(minutes=5),
                    )
                    return RunResult(
                        status="SUCCEEDED_VERIFIED" if verified
                        else "FAILED_VERIFICATION"
                    )

                # 5. Execute action batch with idempotency
                result = await workflow.execute_activity(
                    execute_actions,
                    args=[plan.actions, env_lease.id],
                    start_to_close_timeout=timedelta(minutes=10),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )

                # 6. Update checkpoint and budget
                self.checkpoint = result.checkpoint
                self.budget_remaining_usd -= result.cost_usd

        finally:
            # 7. Always clean up environment
            await workflow.execute_activity(
                destroy_sandbox,
                args=[env_lease.id],
                start_to_close_timeout=timedelta(minutes=2),
            )

    @workflow.signal
    async def cancel(self):
        """Kill switch -- control plane can stop without model cooperation."""
        self.cancelled = True
```

### Autonomy Envelope Enforcement (Action Broker)

```python
class ActionBroker:
    """Deterministic policy enforcement point -- sits outside model text."""

    def __init__(self, envelope: AutonomyEnvelope, policy_engine):
        self.envelope = envelope
        self.policy = policy_engine

    async def execute(self, action: ProposedAction) -> ActionReceipt:
        # 1. Schema validation
        if not action.matches_schema(self.envelope.action_schemas):
            return ActionReceipt(denied=True, reason="schema_invalid")

        # 2. Allowlist / denylist check
        if action.type in self.envelope.denied_actions:
            return ActionReceipt(denied=True, reason="action_denied")
        if action.type not in self.envelope.allowed_actions:
            return ActionReceipt(denied=True, reason="action_not_allowed")

        # 3. Budget check (atomic reservation)
        cost_estimate = self.estimate_cost(action)
        if not self.envelope.reserve_budget(cost_estimate):
            return ActionReceipt(denied=True, reason="budget_exhausted")

        # 4. Approval check for destructive/external actions
        if self.requires_approval(action):
            approval = await self.request_approval(
                action, timeout=self.envelope.approval_timeout
            )
            if not approval.granted:
                self.envelope.release_budget(cost_estimate)
                return ActionReceipt(denied=True, reason="approval_denied")

        # 5. Execute with idempotency key
        try:
            result = await self.tool_executor.execute(
                action=action,
                idempotency_key=f"{self.envelope.run_id}:{action.action_id}",
                timeout=min(action.timeout, self.envelope.remaining_deadline),
            )
            actual_cost = self.measure_cost(result)
            self.envelope.charge_budget(actual_cost)
            return ActionReceipt(
                success=True,
                result=result,
                idempotency_key=action.idempotency_key,
                postcondition=result.observed_state,
            )
        except TimeoutError:
            # Ambiguous -- enter reconciliation, do NOT blindly retry
            return ActionReceipt(
                status="AMBIGUOUS",
                reason="timeout_after_dispatch",
                reconciliation_required=True,
            )

    def requires_approval(self, action: ProposedAction) -> bool:
        """Approval for external writes, destructive actions, purchases."""
        if action.effect_class in ("irreversible", "external_write"):
            return True
        if action.type in self.envelope.approval_required_actions:
            return True
        return False
```

### Computer-Use Loop Structure (Anthropic)

```python
async def computer_use_loop(
    task: str,
    max_turns: int = 50,
    max_budget_usd: float = 5.0,
):
    """Bounded computer-use loop with cost tracking and safety stops."""
    messages = [{"role": "user", "content": task}]
    total_cost = 0.0
    tools = [{"type": "computer_toolset_20260801"}]

    for turn in range(max_turns):
        # Place instruction text BEFORE screenshot (saves tokens)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=tools,
            messages=messages,
            # medium thinking -- avoid max (extra tokens, no UI accuracy gain)
            thinking={"type": "enabled", "budget_tokens": 2048},
        )

        # Track cost
        total_cost += estimate_cost(response.usage)
        if total_cost >= max_budget_usd:
            break  # Budget cap

        # Check for completion
        if response.stop_reason == "end_turn":
            break

        # Process tool calls -- execute in order, not parallel
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                # Injection classifier may trigger HITL pause here
                result = await execute_computer_action(block)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        # End batch with screenshot for next turn
        screenshot = await take_screenshot()
        tool_results[-1]["content"].append({
            "type": "image",
            "source": {"type": "base64", "data": screenshot},
        })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
```

---

## 7. Common Pitfalls & Failure Modes

| Failure | Mechanism / Symptom | Prevention & Recovery |
|---|---|---|
| **Runaway spend** | Fail-slow loops; screenshot every step; `thinking=max`; subagents; retrying Activities with LLM calls | `maxBudgetUsd`, cache, batch actions, `thinking=medium`, Temporal `attempts=1`, env TTL |
| **Goal drift** | Open-ended curriculum / subgoals rewrite the contract; retrieval of wrong memories | Frozen goal artifact + checkpoint acceptance tests; critic gated on spec, not vibes |
| **Environment leak** | Sim MCP schema in prod; cookies in browser pool; MCP token audience skip | Separate pools; RFC 8707; no Docker socket mount |
| **Unattended destructive tools** | Overnight worker + `rm`, migrate, send, purchase without HITL | Watch Mode class of tools; deny-by-default write; two-person rule for prod data plane |
| **Silent stall** | Flipbook misses toasts; waiting on `tasks/result` with dead worker; Watch Mode with user asleep | Heartbeats, `ScheduleToClose`, progress checkpoints, page the on-call on `input_required` |
| **Prompt injection -> exfil** | On-screen / HTML / metadata instructions; connectors + logged-in browser | Classifier + confirm; disable unused connectors; no secrets in GUI |
| **OCR / visual edit collapse** | Random strings (API keys, DNA) read from pixels; nano/VS Code visual edits loop to timeout | Prefer a11y/DOM/API over pixels for secrets; bash ACI for code |
| **Double side-effect on resume** | Replay LLM, not Activity result; HTTP retry of `computer_call` | Idempotency keys; Activities for tools; never retry "click Pay" |
| **Partial-success theater** | 54.8% partial / 20.6% binary | Gate prod on binary + safety report, not leaderboard partial |
| **Activity without progress** | Repeated search/edit/test or polling churn | Progress budget, loop fingerprint, wait state, terminate/replan |
| **In-context locking** | Persists with early hypothesis despite evidence | Alternative hypotheses, checkpoint reset, fresh verifier |
| **Context compaction loss** | Omitted constraint or pending effect after compaction | Inspectable semantic checkpoint, invariant block, resume audit |
| **Memory poisoning** | Untrusted observation becomes durable authority | Provenance/trust labels, validated writes, revocation/rebuild |
| **Stale resume** | Environment changed while run paused | Lease/fence, state digest, re-observe/reconcile |
| **Replay nondeterminism** | Workflow diverges after code/time/random change | Deterministic workflow, versioning, replay tests |
| **Approval TOCTOU** | Action changes after human approval | Sign canonical effect + target version + expiry |
| **Approval laundering** | Model fabricates/interprets prose as permission | Separate approval identity/service |
| **Capability creep** | Agent requests broader tools after failure | Tiered immutable envelope, explicit new authorization |
| **Secret exfiltration** | Code/tool reads secret then sends/logs it | No ambient secrets, egress/data-flow controls, canaries |
| **Sandbox escape** | Generated code reaches host/other tenant | gVisor/microVM, patching, no host mounts, destruction |
| **Grader/benchmark hacking** | Reads hidden tests/answers or exploits scorer | Isolated grader, no solution access, exploit classification |
| **Environment leakage** | Prior run state contaminates next | Reset assertions, per-run fixtures/credentials |
| **Concurrent agents conflict** | Branches overwrite or duplicate | Ownership partitions, optimistic version/fencing, merge verifier |
| **Monitor blind spot** | Harmful sequence looks benign stepwise | Cumulative/sequence policy, state-delta monitor |
| **Quota cliff** | 40 ChatGPT agent messages; E2B 1h Hobby kill | Separate overnight SKU; 24h sandbox; durable wait |
| **Eval-prod skew** | Self-hosted WebArena vs live web; LibreOffice vs Excel | Declare the env in the SLO |
| **Wait churn** | Monitoring agent continuously refreshes when nothing happened | Event subscription/scheduled wake, duty-cycle budget |
| **Bad reflection** | Agent rationalizes error and persists it | External evidence, bounded reflection memory |
| **Skill-library supply chain** | Learned code reused outside valid scope | Provenance, tests, versioned dependencies |
| **Cancellation illusion** | UI says cancelled but effects continue | Revoke/fence/cancel downstream, effect-status terminal state |

**Stall vs spend (coupled tails)**: SWE-agent successes are short; failures eat the $4 cap. Computer use stalls still take screenshots (Operator 400-step autonomy eval). Design overnight cap as `min(token budget, env TTL, workflow ScheduleToClose, vendor quota)` -- the first trip should page, not the last.

**Destructive-tool taxonomy**: Read-only (search, screenshot) / reversible (local git) / reversible-with-SLA (email recall) / irreversible (wire, prod DROP, public social). Your copies of those lists belong in the activity interceptor, because a new MCP server will not inherit the model's refusal training.

---

## 8. Interview Questions & Answers

**Q1: "What is a 'time horizon' for agents, and what does METR actually measure?"**

METR's 50%-time horizon is the *human-expert completion time* of tasks the agent is predicted to finish with 50% success -- not how long the agent runs. Agents that succeed are typically several times faster than those humans. The historical trend found an approximately seven-month doubling period on predominantly software/research/reasoning tasks (2019-early 2025). Critical caveats: the metric has error bars roughly a factor of two in each direction, differs across domains by orders of magnitude, and does not directly predict labor automation. An 8-hour horizon does not mean all 8-hour jobs are automatable. The suite tasks are much cleaner than most real work. METR's GPT-5.6 Sol report showed the 50% horizon estimate swung from ~11.3 to 270+ hours depending on how they classified benchmark-exploitation attempts -- direct evidence that grader access and exploit classification can dominate an autonomy score. For SLOs, use the 80% horizon (roughly 5x shorter) rather than the 50% research headline.

**Q2: "How do you design durable execution for an agent that runs for hours?"**

Temporal is the reference pattern. Workflow Executions have no time limit but have history limits: 51,200 events / 50 MB. Use Continue-As-New to checkpoint latest state into a new Run with the same Workflow ID, typically every 100-1,000 iterations. The critical design rules: LLM calls and tools MUST be Activities (non-deterministic), not in the Workflow function; disable SDK retries (`attempts=1`) so Temporal owns retry policy; always set `Start-To-Close` timeout (server cannot detect a dead worker otherwise); use heartbeats for long tools. Replay restores state without re-executing completed Activities -- this is the difference between "resume" and "double-charge the customer." On resume, never blindly continue from prose: acquire the environment lease, re-authenticate, compare current state to saved digest, invalidate stale observations, and reconcile any ambiguous effects. The three clocks must nest: env TTL > history rotation > step budget.

**Q3: "Compare computer-use (pixels) vs ACI (structured tools) for an overnight coding agent."**

Computer use is wrong for overnight coding. The evidence is clear: Operator's autonomy evaluation showed failures in terminal/OCR tasks -- random strings (API keys, DNA sequences) read from pixels are unreliable, and nano/VS Code visual edits loop to timeout. SWE-agent's purpose-built ACI (search, file viewer, editor, context manager) achieved 64% relative gain vs shell-only at a fraction of the token cost. Computer-use burns image tokens at ~1,300 tokens per screenshot per turn; ACI burns text tokens at much lower rates (SWE-agent median $1.21 success vs ~$9 for 318-turn computer-use). The decision rule: ACI/MCP when the tool has an API; pixels for the long tail where there is no API (internal web apps without DOM access). For the overnight coder specifically: ACI in a disposable repo sandbox + Temporal + fail-to-pass tests as process credit. Computer-use is a fallback, not the default.

**Q4: "How do you prevent goal drift in a long-running autonomous agent?"**

Goal drift means the active interpretation of the objective, constraints, or success criteria diverges over time -- the agent starts pursuing a different goal than what was authorized. Detection signals: goal restatement diverges structurally from the signed original, scope/permission requests expand after setbacks, plan churn without new environment evidence, growing fraction of actions devoted to recovering from the agent's own changes, and summary/memory facts without source provenance. Prevention: freeze the goal and invariant constraints as an immutable artifact separate from mutable tactics. Use a receding-horizon plan -- commit only the next verifiable milestone. The verifier/critic must be gated on the spec, not vibes. Compaction and reflection must not silently weaken invariant constraints. Voyager explicitly WANTS drift (novelty search for curriculum), which is fine in Minecraft but disastrous in production. AutoGPT's open-ended subgoal rewriting is the canonical anti-pattern.

**Q5: "What are the different 'stop conditions' for autonomous agents, and which ones are reliable?"**

There is a hierarchy. Model-level refusal (CUA refusing banking) is trained behavior, not a guarantee -- Operator achieves 97% on illicit-activity eval, which means 3% get through. Product-level confirmation (Watch Mode, classifier HITL) is stronger but depends on a human being awake -- Watch Mode with the laptop lid closed is a stall, not a stop. Budget caps (`maxBudgetUsd`, `maxTurns`, SWE-agent $4 cap) are hard limits but the model can burn tokens up to the cap. Environment TTL (E2B 1h/24h, MCP task `ttl`) kills the sandbox but does not roll back effects already committed. Workflow limits (Temporal 51,200 events, `ScheduleToClose`) are the infrastructure kill switch. The reliable pattern for overnight work: layer all four -- `maxBudgetUsd` + env TTL + Temporal `ScheduleToClose` + provider message quota. The critical design principle: the control plane must be able to stop the data plane without the model's cooperation (Cancel + destroy lease + revoke token).

**Q6: "How would you handle the sim-to-prod gap for an agent system?"**

The gap is fundamental: execution-based graders on a resettable intranet are not SSO + flaky third-party + irreversible money. OSWorld-MCP's 25 distractor tools show even tool selection degrades with realistic noise. SWE-bench Pro's commercial split (17.8% vs 23.3% public) proves enterprise codebases are harder. The promotion path: Gymnasium-style gym (deterministic, fast reset) -> staging MCP with fake IdP -> prod MCP with resource indicators. Gate on: (a) binary grader (not partial credit -- OSWorld 2.0 is 54.8% partial but only 20.6% binary), (b) injection drill on the prod-like DOM, (c) duplicate-side-effect test (kill at every commit boundary), (d) kill-switch drill that does not need the model. Never promote a WebArena GitLab token policy to corp GitLab. Separate pools; same MCP tool schema, different allowlists, credentials, and irreversible-action policy.

**Q7: "Explain how Voyager's architecture applies to enterprise agents."**

Voyager's three innovations map directly: (1) Automatic curriculum -> enterprise analog is a ticket queue or runbook library, not open-ended novelty search. Letting the agent propose its own goals is a cost amplifier and scope risk. (2) Skill library of executable code indexed by embeddings -> promote successful agent traces into typed, tested skills. The key is Voyager's critic gate: code enters the library only after self-verification succeeds. Enterprise equivalent: promotion requires unit tests + human review. (3) Iterative refinement with environment feedback -> production agents should use compiler/test output as evidence, not self-critique. The critical lesson from Voyager's ablation: random curriculum loses 93% of items, and the skill library prevents late-stage plateau. Without a curated skill library, long-running agents plateau because they keep re-solving known problems. But Voyager's curriculum is by design unbounded -- enterprise copies must cap scope, budget, and especially the set of skills the agent can add.

**Q8: "What is the difference between checkpoint, memory, and compaction?"**

They solve different problems and lose different things. A checkpoint (Temporal event history, semantic checkpoint) preserves durable control state -- goals, milestones, action receipts, budgets. It survives process death. A memory (Generative Agents retrieval, Voyager skill library) is a long-term knowledge store that persists across tasks. Compaction (OpenAI `/responses/compact`, Claude Code's context compaction) reduces token count by summarizing history, losing detail. The critical failure modes: compaction can drop invariant constraints or pending effects -- always keep an inspectable invariant block that compaction cannot touch. Memory can be poisoned by untrusted observations becoming durable facts -- require provenance, trust labels, and validated writes. A checkpoint without an environment snapshot is incomplete -- restoring tokens but not the VM state (cookies, `node_modules`, failed migration) means you are starting a new task wearing the old goal. On resume: re-observe the environment, compare to saved digest, reconcile any drift, then continue or replan.

**Q9: "How do you audit and monitor an overnight autonomous agent?"**

The audit minimum viable stream needs five join keys: workflow events (`workflow_id`, `run_id`, principal via Temporal Principal Attribution), model traces (`response_id`/`previous_response_id`), tool calls (`tool_use_id` / MCP `task_id`), environment lease (`sandbox_id`, `contextId`), and confirmation outcomes (`approved|denied|timeout`). If any hop is missing, overnight incidents become "the model did something." For monitoring: SHADE-Arena showed that final-output review and a single LLM monitor are insufficient -- the best monitor achieved only 0.87 AUC, and hidden scratchpad access materially affected sabotage success. Use independent controls: deterministic capability enforcement before effects, state-delta monitoring at tool boundaries, canary resources/secrets, separate verifier model with only needed evidence (not agent persuasion), and kill/revoke/fence mechanisms outside agent control. PII surfaces unique to this domain: screenshots of corporate desktops, browser recordings, Temporal Principal emails, MCP elicitation fields. Retention: you must delete your object store of screenshots even after the vendor session is deleted.

**Q10: "How do you handle approval in an autonomous system so the model can't game it?"**

A human approval is a capability for an exact proposed effect, not a conversational "yes." Bind the approval to: principal + tenant + run + action name + canonical arguments + target current version/state digest + expected state delta + maximum amount/scope + policy version + expiry + nonce. Show the approver the material effect, evidence, uncertainty, and alternatives. After approval, re-check preconditions -- if target state or arguments changed, approval expires (TOCTOU prevention). Prevent the model from supplying its own approval text (approval laundering). Use separation of duties for high-impact actions. Make deny/cancel paths as available as approve. The default for Watch Mode-class tools should be deny-on-timeout, not approve-on-timeout. And critically: a new MCP server will not inherit the model's refusal training, so your approval requirements belong in the activity interceptor of the control plane, not in the model prompt.

**Q11: "What should you measure for a long-horizon agent system?"**

Seven dimensions. (1) Human-time horizon of the task distribution (METR method) vs required reliability -- 50% is a research headline; use the 80% trend (~5x shorter) for SLOs. (2) Binary vs partial success -- OSWorld 2.0 shows 54.8% partial but only 20.6% binary; gate production on binary. (3) $/success vs $/fail -- SWE-agent shows failures are 2x more expensive ($2.52 vs $1.21) because agents fail slowly. (4) Step budget vs Continue-As-New frequency vs env TTL -- the three clocks must nest. (5) Injection path: pixels, DOM, MCP tool output, elicitation URL. (6) Resume test: kill worker at 50% checkpoints; no duplicate side effects; env state matches token state. (7) Public vs commercial benchmark gap -- SWE-bench Pro shows contamination + enterprise messiness degrade scores. On the SLO dashboard: $/successful task, $/failed task, turns-to-submit, cache hit rate, env-lease hours, % jobs hitting maxBudgetUsd, % Continue-As-New.

**Q12: "How do you choose the right sandbox isolation level for an agent?"**

Match threat model to isolation strength. Language/process restrictions (Python sandbox): only for trusted transformation, not arbitrary code -- OpenAI explicitly says this is not a security boundary. Standard container: packaged trusted workload but shared kernel; not adequate alone for hostile code. gVisor: intercepts syscalls at the userspace level, providing stronger isolation with container ergonomics; good for untrusted common workloads but needs compatibility/performance testing. Firecracker microVM: separate guest kernel, strong tenant boundary; production guidance says to use the jailer and patch host/guest kernels. Dedicated host/account: highest-impact or regulated separation; expensive and slower. Regardless of runtime: ephemeral filesystem, read-only base, no host socket/mount, non-root, seccomp, PID/CPU/memory/disk/time limits, no ambient metadata credentials, default-deny network, allowlisted destinations, per-run secrets, output scanning, and destruction receipt. Treat artifacts crossing out of the sandbox as untrusted.

**Q13: "How do you handle the cost of computer-use agents at scale?"**

Computer use is an image-token factory. Anthropic: no separate computer-use SKU; screenshots bill as vision tokens. A 50-step GUI task on Opus 4.8 costs roughly $1.4 before history growth; a 318-call OSWorld 2.0-shaped job costs roughly $9. History accumulation is the real p99 cost driver: later turns re-send the screenshot AND prior tool JSON. Optimizations: (1) use medium thinking, not max (extra tokens, no UI accuracy gain); low thinking can actually cost less than thinking-off because it avoids retries. (2) Batch actions to cut round trips (the expensive unit), not just pixels. (3) Place instruction text before the screenshot. (4) End batches with a screenshot to save a turn. (5) Use prompt caching (10% of input on cache hits). (6) Browser-use tool (page structure, not pixels only) for applicable pages. (7) SWE-agent's $4 cap teaches the core NFR: failures are more expensive than successes because agents fail slowly. Budget the fail tail, not the success median. Environment costs are separate: E2B at $0.1008/h for 2 vCPU means an 8h overnight job costs ~$0.81 sandbox plus LLM. Temporal workers while `wait_condition`: $0 compute.

---

## 9. Key Numbers to Memorize

| Metric | Value | Context |
|---|---|---|
| METR 50% horizon doubling | **~7 months** (2019-early 2025) | Historical trend; ~20% faster in 2023-2025 |
| METR 80% horizon vs 50% | **~5x shorter** | Use 80% for SLOs, not 50% for research PR |
| METR >16h tasks | **Unreliable** on current suite | Measurement limitation |
| OSWorld human vs best agent | **72.36%** vs **38.1%** (CUA, 2025) | Real OS benchmark |
| OSWorld 2.0 leader | **20.6% binary / 54.8% partial** (500 steps) | Partial credit is not binary |
| OSWorld 2.0 tool calls | **~318** (max-thinking agent) | History growth driver |
| OSWorld 2.0 human median | **~1.6 hours** | Long-horizon definition |
| WebArena GPT-4 -> CUA | **14.41% -> 58.1%** | Scaffold+model improvement |
| SWE-bench Pro best (public) | **23.3%** Pass@1 (GPT-5) | Commercial repos: 17.8% |
| SWE-agent cost: success vs fail | **$1.21 / 12 steps** vs **$2.52 / 21 steps** | Failures are 2x more expensive |
| SWE-agent budget exhaust | **93%** resolved submit before cap vs **69%** overall | Raising cap is a weak lever |
| GAIA human vs GPT-4+plugins | **92%** vs **15%** | Multi-step reasoning gap |
| TheAgentCompany best | **30.3%** full / **39.3%** partial | Professional tasks baseline |
| Temporal history limits | **51,200 events / 50 MB** | Hard stop; CAN every 100-1,000 iterations |
| Temporal CAN checkpoint pattern | Every **100-1,000** iterations | Design pattern recommendation |
| E2B sandbox pricing | **$0.1008/h** (2 vCPU); Hobby 1h / Pro 24h TTL | Environment cost separate from tokens |
| E2B Pro concurrent limit | **100** default (buy to **1,100**) | Pool sizing constraint |
| ChatGPT agent quota | **400** Pro / **40** paid messages/month | Not $/task |
| Anthropic Opus 4.8 pricing | **$5/$25** per MTok in/out; cache hit **$0.50** | Computer-use bill driver |
| Computer-use screenshot tokens | **~1,300** tokens per ~1000x1000 screenshot | Order-of-magnitude; version-sensitive |
| PRM800K (process rewards) | **800k** step labels / **75k** solutions / **12k** problems | Process supervision dataset |
| Voyager vs baselines | **3.3x** unique items; diamond only by Voyager | Skill library + curriculum impact |
| Generative Agents reflection trigger | Importance sum **>150** | ~2-3x reflections per day |
| tau-bench retail pass^8 | **<25%** | Consistency matters more than best-of-many |

---

## 10. Quick Reference

### Production-Ready Autonomous Agent Checklist

1. **Bound**: What objective, data, actions, destinations, resources, duration, and escalation are authorized?
2. **Enforce**: Which non-model component denies an action, reserves budget, and revokes authority?
3. **Prove**: What authoritative predicate distinguishes verified success, failure, waiting, and truncation?
4. **Persist**: What semantic state, evidence, ambiguous effects, budgets, and permissions survive context/process loss?
5. **Resume**: How are environment drift, expired capabilities, changed policy, and pending effects reconciled?
6. **Environment**: Are reset, clocks, partial observation, concurrency, versions, snapshots, grader, and teardown explicit?
7. **Contain**: Can generated code/content reach host, secrets, other tenants, or unrestricted network?
8. **Measure**: Are progress, consistency, recovery, safety, reaction time, and cost per accepted outcome measured by horizon?
9. **Evaluate**: Is the exact model + harness + policy + tool + environment system tested, including adversarial and stateful cases?
10. **Govern**: Who owns the autonomy tier, reviews evidence, handles incidents, and approves any authority increase?

### Sandbox Isolation Comparison

| Isolation | Strength | Cost | GPU? | Use |
|---|---|---|---|---|
| Language/process | Weak | None | N/A | Trusted code only |
| Standard container | Shared kernel | Low | Yes | Trusted workloads |
| gVisor | Syscall intercept | Medium | Limited | Untrusted common code |
| Kata + Confidential Containers | UVM + TEE | High | Passthrough (Hopper+SEV-SNP) | Regulated GPU |
| Firecracker microVM | Separate kernel | Medium-high | N/A | Hostile/cross-tenant |
| Dedicated host | Full separation | Highest | Yes | Highest-impact |

### What to Measure in a Design Review

1. Human-time horizon of the task distribution (METR method) vs required reliability
2. Binary vs partial (OSWorld 2.0)
3. Public vs commercial SWE-bench Pro gap (contamination + enterprise messiness)
4. $/success vs $/fail (SWE-agent)
5. Step budget vs Continue-As-New frequency vs env TTL (three clocks must nest)
6. Injection path: pixels, DOM, MCP tool output, elicitation URL
7. Resume test: kill worker at 50% checkpoints; no duplicate side effects; env state matches token state

### The Interview Close

The advanced design standard is not "the agent ran for a long time." It is: the agent remained inside delegated authority, made verified progress in a versioned environment, survived interruption without duplicating effects, stopped for the right reason, and produced evidence strong enough for the outcome's risk. The decision rule: if the side effect is irreversible or PII-bearing, the control plane must be able to **stop the data plane without the model's cooperation** (Cancel + destroy lease + revoke token). If it cannot, it is a demo, not an overnight worker.
