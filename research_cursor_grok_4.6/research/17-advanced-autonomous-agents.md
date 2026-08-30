# Research: Advanced Autonomous Agents
**Date researched**: 2026-08-21
**Sources consulted**: 63

Scope: **autonomous agents** (goal-directed loops, self-starting / overnight workers, Operator → ChatGPT agent, Claude computer use, AutoGPT lineage, safety stop conditions), **long-horizon tasks** (hours-to-days, checkpointing, memory, credit assignment, SWE-bench Pro / OSWorld / WebArena / GAIA / TheAgentCompany, interrupt/resume), **agent environments** (gyms, sandboxes, computer-use VMs, browser farms, OSWorld, TheAgentCompany, MCP-as-environment, sim-to-prod). Primary papers: Voyager, Generative Agents, AutoGPT, SWE-agent, OSWorld, WebArena, GAIA, METR time-horizon (Kwa et al.), SWE-bench Pro, TheAgentCompany, OSWorld 2.0, OSWorld-MCP, *Let’s Verify Step by Step*. Primary 2025–2026 products: OpenAI Operator / CUA / ChatGPT agent, Anthropic computer use + Claude Code Agent SDK + Managed Agents, Temporal durable agents, MCP 2025-11-25 Tasks, E2B / Daytona / Browserbase. Prices below are **vendor list pages** (Anthropic MTok, E2B per-second) or **paper-reported inference spend** (SWE-agent $4 cap). `$ per 1k long-horizon tasks` is **[inferred]** from a named SKU × a stated loop shape — not a market rate. ⚠️ No vendor publishes p50/p95/p99 wall-clock SLOs for multi-hour agent jobs; latency claims below are either benchmark step budgets or paper medians.

Invariant: **an autonomous agent is a supervisor of a long-running job sitting in front of a pool of mutable environments, not a chat completion.** The control plane (job scheduler, Temporal workflow, Managed Agents session, kill switch, spend cap) decides *whether the loop may continue*. The data plane (VM/screenshot stream, sandbox filesystem, browser cookies, MCP session, skill library) holds *side effects that cannot be replayed as tokens*. Collapsing those planes — treating a 90-minute computer-use loop as a 30-second HTTP request, retrying a `rm -rf` the way you retry a 429, or letting the model’s goal statement mutate without a checkpointed contract — is how teams get runaway spend, goal drift, and unattended destructive tools in the same incident.

---

## 1. System Topology & Mechanics

### 1.1 Two planes, three clocks, one environment lease

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | Job supervisor, Temporal workflow, Claude Managed Agents session, `maxTurns` / `maxBudgetUsd`, kill switch, env-pool allocator | Durable-execution clock (event history; SSE session; cron) | Temporal persistence / Managed Agents event log / your orchestrator DB | HTTP timeout killing a 3h job; KEDA scaling away the worker holding the VM lease |
| **Data (tokens)** | Screenshots, accessibility trees, tool results, condensed conversation, skill embeddings | Token/context clock (compaction, prompt cache TTL) | Model context + cache | Replaying a 200-screenshot history as a fresh prompt without compaction → context blow-up |
| **Data (side effects)** | VM disk, browser cookies, git working tree, MCP task IDs, purchases, emails | Environment clock (VM TTL, cookie policy, MCP task `ttl`) | Sandbox / browser farm / production SaaS | Retrying the *workflow* re-clicks “Place order” because the LLM call was retried |

**Control vs data (shipped products).** OpenAI’s ChatGPT agent (2025-07-17) runs on **its own virtual computer** that preserves context across a visual browser, a text browser, a terminal, and connector APIs — the model chooses the path; the VM is the data plane ([ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)). Anthropic splits the same idea into three SKUs: **Messages API** (you own the loop), **Agent SDK** (Claude Code’s loop in *your* process), **Managed Agents** (Anthropic owns harness + sandbox + session log; beta header `managed-agents-2026-04-01`) ([Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview); [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)). Temporal’s OpenAI Agents SDK extension wraps `SandboxAgent` so LLM calls, sandbox lifecycle, and shell are Activities; `workflow.wait_condition` idles at **zero compute** between user messages ([Temporal + agentic sandboxes](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk)). Interview move: **the environment lease is the unit of scheduling, not the HTTP request.**

**Supervisor of long-running jobs.** AutoGPT (2023) is the public ancestor of the unsupervised goal loop: decompose a high-level goal into subgoals, ReAct-style execute, repeat ([AutoGPT](https://github.com/Significant-Gravitas/AutoGPT); Voyager’s reimplementation notes AutoGPT lacks a skill library, self-verification, and an automatic curriculum — [Voyager](https://arxiv.org/abs/2305.16291)). SWE-agent (Yang et al., NeurIPS 2024) is the opposite design: a **purpose-built agent-computer interface (ACI)** — search, file viewer, editor, context manager — on a Linux shell, not a free-form goal crawler ([SWE-agent](https://arxiv.org/abs/2405.15793)). OpenHands documents a **stateless single-step** agent: each `step()` reads event history, optionally condenses, queries the LLM, then either executes or waits for confirmation; a supervisor pattern (PR #4449) holds the overall plan and interrupts subordinates that run too long ([OpenHands agent](https://docs.openhands.dev/sdk/arch/agent); [OpenHands](https://github.com/All-Hands-AI/OpenHands)). TheAgentCompany’s baseline uses OpenHands with bash + IPython + Playwright/BrowserGym primitives against a self-hosted company intranet ([TheAgentCompany](https://arxiv.org/abs/2412.14161)). Production topology that survives overnight: **one durable supervisor per job**, **N pooled environments**, **never** a request-scoped Python process that holds the VM.

**Env pools.** Four commercially distinct pool types, not one “sandbox”:

| Pool | Observation | Action | Isolation | Typical TTL |
| --- | --- | --- | --- | --- |
| **Gym / eval farm** | Gymnasium `reset/step`; BrowserGym DOM+a11y+screenshot | Discrete / browser primitives | Docker per episode | Episode (minutes) |
| **Code sandbox** | Files + stdout | bash / Python | E2B, Daytona, Modal, Runloop, Anthropic sandbox-runtime | 1h Hobby / **24h Pro** on E2B |
| **Computer-use VM** | Screenshot (+ optional a11y) | Mouse/keyboard/17-tool computer toolset | Xvfb + you-owned desktop, or vendor VM | Session; ChatGPT agent cookies persist per site policy |
| **Browser farm** | DOM / Stagehand observe | Click/type or CUA pixels | Browserbase hosted SHTTP, Steel, Playwright grid | Keep-alive session + context ID |

Gymnasium is the RL contract (`terminated` vs `truncated`) that BrowserGym inherits for MiniWoB++, WebArena, VisualWebArena, WorkArena, AssistantBench ([Gymnasium](https://gymnasium.farama.org/); [BrowserGym](https://github.com/ServiceNow/BrowserGym)). OSWorld is a **real OS** (Ubuntu/Windows/macOS) with 369 tasks and execution-based graders, not a toy gym ([OSWorld](https://arxiv.org/abs/2404.07972); humans **72.36%**, 2024 best agent **12.24%**). OSWorld 2.0 is a *different* protocol: **108** long-horizon workflows, human median **~1.6 h**, **~318** tool calls with Claude Opus 4.7 max-thinking vs **~30** in OSWorld 1.0 ([OSWorld 2.0](https://osworld-v2.xlang.ai/); [arXiv:2606.29537](https://arxiv.org/abs/2606.29537)). Mixing OSWorld-Verified scores with OSWorld 2.0 scores is a methodology error the maintainers flag explicitly.

### 1.2 Autonomous loop: perception → reason → act → stop

**OpenAI CUA / Operator (2025-01-23).** CUA combines GPT-4o vision with RL; loop is screenshot → chain-of-thought over current+past screenshots → click/scroll/type until done **or user input needed**. Confirmations for logins and CAPTCHAs. Benchmarks at launch: **OSWorld 38.1%** (prev SOTA 22.0%; human 72.4%), **WebArena 58.1%** (prev 36.2% computer-use / 57.1% web-agent SOTA; human 78.2%), **WebVoyager 87%**. Test-time scaling: more allowed steps raises OSWorld. Reliability is UI-specific: 10/10 on simple Todoist/Spotify loops; 3/10 on underspecified venue search without filter hints ([CUA](https://openai.com/index/computer-using-agent/); [Operator](https://openai.com/index/introducing-operator/); [Operator System Card](https://cdn.openai.com/operator_system_card.pdf)). Standalone Operator (Pro, US; TechCrunch: ChatGPT **$200**/mo Pro) was folded into **ChatGPT agent mode** on 2025-07-17; `operator.chatgpt.com` sunset weeks later. Quotas at agent launch: Pro **400** agent messages/month, other paid **40**, extra via credits ([ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/); [TechCrunch](https://techcrunch.com/2025/01/23/openai-launches-operator-an-ai-agent-that-performs-tasks-autonomously/)).

**OpenAI Responses API computer tool (2026).** `computer-use-preview` (8,192-token context, Responses-only) is the 2025 specialized model; docs now migrate that tool onto frontier models (example: `gpt-5.5`) with a first-party `computer` tool: send task → run `computer_call.actions[]` **in order** → return `computer_call_output` screenshot → repeat. Custom harnesses must keep Playwright `browser/context/page` alive across steps. Language-level sandboxes (`vm`, restricted Python globals) are **explicitly not** security boundaries ([Computer use guide](https://developers.openai.com/api/docs/guides/tools-computer-use); [computer-use-preview](https://developers.openai.com/api/docs/models/computer-use-preview)).

**Anthropic computer use.** Public beta 2024-10-22 on Claude 3.5 Sonnet: OSWorld screenshot-only **14.9%** vs next-best **7.8%**; **22.0%** with more steps. Training used a few simple apps (calculator, text editor) **without internet**; pixel-counting for cursor targeting; flipbook screenshots miss transient UI; researchers recorded the model wandering into Yellowstone photos mid-demo ([computer use announcement](https://www.anthropic.com/news/3-5-models-and-computer-use); [Developing computer use](https://www.anthropic.com/research/developing-computer-use)). As of 2026-08, the Messages API ships **generally available** `computer_toolset_20260801` (no beta header): **17 member tools** (`screenshot`, `left_click`, `type`, `zoom`, …), **batch actions** (sequential in one response, not concurrent parallel tools). Not available in Managed Agents. Coordinate space = screenshot pixels; zoom does not change the coordinate frame. Prompt-injection classifiers on screenshots **steer the model to ask user confirmation**; HITL-free loops must contact support to opt out. Anthropic’s 2026 platform blog: multi-action turns, a separate **browser use** tool (page structure, not pixels only), HIPAA-eligible **computer use** under BAA — distinct from Managed Agents, which is **not** ZDR/HIPAA eligible ([computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool); [computer use + Skills + Files](https://claude.com/blog/computer-use-skills-api-files-api)).

**Claude Code / Cowork / Dispatch.** Computer use in the CLI is a built-in MCP server `computer-use`, off by default; Pro/Max; interactive only (no `-p`); macOS Accessibility + Screen Recording. Desktop Cowork + Dispatch: assign from phone, require the desktop app awake; connectors first, GUI when no API ([Claude Code computer use](https://code.claude.com/docs/en/computer-use); [Dispatch](https://claude.com/blog/dispatch-and-computer-use)). Overnight worker implication: **phone-dispatch is not an unattended worker** unless the desktop is up and permissions persist.

**Safety stop conditions (product, not research slogans).**

| Stop | Who implements it | Trigger |
| --- | --- | --- |
| User confirmation / Watch Mode | OpenAI Operator & ChatGPT agent | Side-effecting actions (purchase, email); sensitive sites require active supervision |
| Task refusal | CUA training + usage policy | Banking transfers, stocks, illicit goods; Operator **97%** refuse on internal illicit-activity eval (not 100% in production) |
| Prompt-injection pause | Operator extra monitor model; Anthropic screenshot classifiers | Suspicious on-screen instructions |
| Spend / turn cap | Claude Agent SDK `maxTurns`, `maxBudgetUsd` → `error_max_turns` / `error_max_budget_usd`; SWE-agent **$4**/instance auto-submit | Open-ended “improve the codebase” |
| Env TTL | E2B 1h Hobby / 24h Pro; MCP task `ttl` (ms) | Lease expiry |
| History limit | Temporal **51,200** events / **50 MB** → terminate unless Continue-As-New | Multi-hour tool spam |
| Human interrupt | ChatGPT agent take-over / pause / partial results; Managed Agents mid-session events | User steer |
| Kill switch | Your control plane (Temporal Cancel, session delete, sandbox kill) | Policy / SOC |

Operator System Card frontier evals (pre-mitigation CUA on GPT-4o base): biorisk tooling **1%**; autonomy main tasks **not >10%** → Preparedness **Low**, matching GPT-4o. ChatGPT agent (2025-07) was treated as **High** Biological and Chemical under the Preparedness Framework *by caution*, with dual-use refusal + always-on classifiers — a **higher** bar than Operator because of terminal + connectors + broader reach ([Operator System Card](https://cdn.openai.com/operator_system_card.pdf); [ChatGPT agent](https://openai.com/index/introducing-chatgpt-agent/)).

### 1.3 Lineage: self-starting exploration vs contracted SWE loops

**Voyager (Wang et al., 2023).** Lifelong Minecraft agent: (1) automatic curriculum (“discover as many diverse things as possible”), (2) skill library of **executable JavaScript** indexed by description embeddings (`text-embedding-ada-002`), (3) iterative prompting with env feedback + interpreter errors + GPT-4 **self-verification**. Code is the action space (Mineflayer), not pixels. Results vs ReAct / Reflexion / AutoGPT in MineDojo: **3.3×** unique items (63 items / 160 prompting iterations), **2.3×** distance, wood tools **15.3×** faster; only Voyager unlocks diamond. Ablation: random curriculum → **−93%** items; skill library prevents late-stage plateau. Stuck after **4** refinement rounds → ask curriculum for a new task ([Voyager](https://arxiv.org/abs/2305.16291)). Production lesson: **promote successful traces into typed skills**, not chat summaries.

**Generative Agents (Park et al., UIST 2023).** 25 agents in Smallville. Architecture: memory stream of NL observations; retrieval = recency + relevance (cosine) + importance (LLM 1–10); reflections when recent importance sum **>150** (~2–3×/day); plans condition on retrieved memories. Seed: one paragraph persona. Emergent: information diffusion (mayoral candidacy), relationship memory, Valentine’s party coordination. Failure mode they measured: retrieval of the *wrong* memories, not missing a tool API ([Generative Agents](https://arxiv.org/abs/2304.03442)). This is the long-horizon **memory** paper; it is not a computer-use paper.

**AutoGPT lineage.** Classic AutoGPT = goal → subgoals → ReAct loop, no durable env, no ACI. GAIA (Mialon et al., 2023; AutoGPT co-author Swift) evaluated AutoGPT (GPT-4, git `ed172dec`) at **14.4%** Level 1, **0.4%** Level 2, **0%** Level 3 vs GPT-4+plugins **30.3 / 9.7 / 0** and humans **93.9 / 91.8 / 87.3** (times **6.8 / 10.5 / 17.7** min) ([GAIA](https://arxiv.org/abs/2311.12983)). 2026 AutoGPT is a hosted platform (schedule/trigger) plus MIT `classic/`; the research meaning of “AutoGPT” is still the **unsupervised decomposer**, not the SaaS.

**SWE-agent ACI.** GPT-4 Turbo: **12.47%** (286/2,294) SWE-bench, **18.00%** Lite; **64%** relative gain vs shell-only. Cost: **8–13×** RAG on Lite for **6.7×** resolve. Per-instance cap **$4**; successes finish earlier (median **$1.21 / 12 steps**) than failures (mean **$2.52 / 21 steps**); **93%** of resolved runs submit before budget exhaust vs **69%** overall — raising the cap is a weak lever ([SWE-agent](https://arxiv.org/abs/2405.15793)). Interface design *is* the capability.

### 1.4 MCP as environment (not just a tool plugin)

MCP 2025-11-25 authorization: MCP servers are OAuth 2.1 **resource servers**; clients **MUST** use RFC 9728 Protected Resource Metadata; tokens **MUST** carry RFC 8707 resource indicators bound to the canonical MCP URI; PKCE for public clients ([MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)). Experimental **Tasks** (SEP-1686): any request can return a durable state machine. States: `working` → `input_required` | `completed` | `failed` | `cancelled`; `ttl` in milliseconds; poll `tasks/get`, block on `tasks/result`, cancel idempotently. Tools declare `execution.taskSupport`: `required` | `optional` | `forbidden` (default forbidden) ([MCP Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)). OSWorld-MCP adds **158** MCP tools (7 apps; **25** distractors; RAG-selected because 158 tools blow context). **69%** of 250 tasks are tool-beneficial; OpenAI o3 **8.3% → 17.6%** at 15 steps; highest tool-invocation rate cited **33.3%** (Claude-4-Sonnet, 50 steps) ([OSWorld-MCP](https://arxiv.org/abs/2510.24563)). Browser farms as MCP: Browserbase hosted Streamable HTTP + Stagehand (`act`/`observe`/`extract`) ([Browserbase MCP](https://www.browserbase.com/mcp); [Stagehand](https://github.com/browserbase/stagehand)). **MCP is an environment ABI**: the same agent can be pointed at a sim gym, a cloud browser, or prod Salesforce *without* changing the loop — which is exactly the sim-to-prod footgun in §5.

### 1.5 Sim-to-prod gap (environment fidelity)

WebArena: **812** tasks on self-hosted shopping / CMS / Reddit-like / GitLab clones + maps/scratchpad; GPT-4 agent **14.41%** vs human **78.24%** ([WebArena](https://arxiv.org/abs/2307.13854)). CUA later **58.1%** on the same benchmark using pixels — scaffold + model, not “the web got easier.” TheAgentCompany: **175** professional tasks, GitLab+OwnCloud+Plane+RocketChat, Sotopia LLM colleagues (default Claude 3.5 Sonnet), checkpoint graders; Gemini 2.5 Pro **30.3%** full / **39.3%** partial ([TheAgentCompany](https://arxiv.org/abs/2412.14161)). SWE-bench Pro: **1,865** problems, **41** repos, GPL public (**731**) + held-out (**858**) + commercial startups (**276**); gold patches mean **107.4** LOC / **4.1** files; GPT-5 **23.3%** public Pass@1, Opus 4.1 **22.7%**; commercial best **17.8%** (Opus 4.1) vs public — enterprise codebases are harder, not just “more files” ([SWE-bench Pro](https://arxiv.org/abs/2509.16941)). GAIA Level 3 is “arbitrarily long sequences of actions”; GPT-4+plugins scored **0**. Sim-to-prod rule: **execution-based graders on a resettable intranet ≠ SSO + flaky third-party + irreversible money.**

### 1.6 Self-starting and overnight workers (control-plane patterns)

Four distinct “it runs while you sleep” topologies — do not conflate them in an interview:

| Pattern | Who starts it | Who must stay awake | Durable wait | Example |
| --- | --- | --- | --- | --- |
| **Interactive computer use** | User in session | Desktop/CLI (Claude Code: no `-p`; Cowork desktop must be awake for Dispatch) | Process RAM | Phone-dispatch is **not** a worker unless the host is up |
| **Scheduled consumer agent** | Cron / “repeat this task” | Vendor VM | Vendor session | ChatGPT agent weekly metrics; Managed Agents scheduled deployments |
| **Durable workflow + sandbox** | Temporal Schedule / signal | Worker fleet (can scale to zero while waiting) | Event history; `wait_condition` = **0** activity CPU | OpenAI Agents SDK + E2B/Daytona/Docker ([Temporal sandbox blog](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk)) |
| **Eval episode** | Harness `reset()` | Gym node until `terminated`/`truncated` | None (by design) | OSWorld / WebArena / TheAgentCompany |

Self-starting in the AutoGPT sense (agent proposes the next goal) is **curriculum**, not orchestration. Voyager’s automatic curriculum is a GPT-4 loop with temperature **0.1** for diversity; production analog is a **ticket queue**, not a novelty search. If the overnight job can mint its own tickets, you have unbounded spend *and* unbounded scope.

**Supervisor split that scales.** Control plane: one Workflow per job (goal contract, budget, kill switch, Continue-As-New). Data plane pool: warm sandbox images (OpenHands action server, E2B snapshot, OSWorld AMI). Do not put the LLM call inside the Workflow function (breaks determinism); do not put `rm` inside an Activity without idempotency and a confirm Signal. OpenHands TaskToolSet is a **synchronous** sub-agent (parent blocks) with disk resume — useful for scoped subtasks, the wrong tool for a 12-hour migrate unless the parent is itself a Temporal Workflow.

---

## 2. Token Economics & NFR Metrics

### 2.1 What “long-horizon” means (METR)

METR’s **50%-time horizon** is the *human expert duration* of tasks the agent is predicted to finish with 50% success — **not** wall-clock autonomy time. Agents that succeed are typically **several times faster** than those humans; METR does not publish agent wall-clock because it is scaffold- and provider-dependent ([time horizons](https://metr.org/time-horizons/); [blog](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/); [arXiv:2503.14499](https://arxiv.org/abs/2503.14499)).

Published anchors (do not mix TH1.0 text with TH1.1 interactive chart without checking the page date):

| Claim | Number | Source / caveat |
| --- | --- | --- |
| Historical doubling (2019–early 2025) | **~7 months** at 50% horizon | Kwa et al. / METR blog; paper: o3 **~110 min**; 80% horizon **~5× shorter** |
| 2023–2025 vs 2019–2025 | **~20% faster** growth | Paper §F.1 |
| SWE-bench Verified doubling (exploratory) | **~70 days** vs **143 days** on HCAST+SWAA+RE-Bench for 2024 models | Annotator times **exclude** codebase familiarization → short tasks look shorter → doubling looks faster. ⚠️ |
| TH1.1 suite | **228** tasks (was 170); **8h+** tasks **31** (was 14) | [TH1.1](https://metr.substack.com/p/2026-1-29-time-horizon-1-1) |
| Reliability of very long measurements | **>16 h unreliable** on current suite | METR time-horizons, 2026-05-08 note |
| GPT-5 example on TH page | **~2 h 17 min** 50% horizon | FAQ illustration; 90 min–3 h band: ~⅓ always succeed, ~⅓ always fail, ~⅓ mixed |
| Claude 3.7 Sonnet (Mar 2025 blog) | **~1 hour** 50% horizon | Blog logistic-curve example |
| Elicitation cost | **6** independent runs/task; **~1,000** runs; **1–2 weeks** calendar | Infra restarts, reward-hack review |

Messier / holistically scored tasks: agents do **substantially worse** (paper + FAQ). An 8-hour horizon ≠ automate an 8-hour professional’s day (low-context contractors, SWE/ML/cyber distribution, algorithmically scored).

### 2.2 Token shape of computer-use vs ACI vs MCP

**Computer use is an image-token factory.** Anthropic: no separate computer-use SKU; screenshots bill as vision tokens; docs recommend **medium** thinking on Sonnet 4.6 / Opus 4.6 (avoid `max` — extra tokens, no UI accuracy gain); `low` thinking can use *fewer* output tokens than thinking-off because retries dominate. Batch actions cut **round trips** (the expensive unit) not pixels. Place instruction text **before** the screenshot. End batches with a screenshot or attach one to the last `tool_result` to save a turn ([computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)).

Anthropic list prices (2026-08-21 pricing page): Opus 4.8 **$5 / $25** per MTok in/out; cache hit **$0.50**; Sonnet 4.6 **$3 / $15**, cache hit **$0.30**; Sonnet 5 **$2 / $10** (introductory $2/$10 made permanent; scheduled 2026-09-01 hike **cancelled**); Haiku 4.5 **$1 / $5**. Fast mode Opus 4.8/5: **$10 / $50**. Batch API **50%**. Claude 4.7+ tokenizer **~30% more tokens** for the same text ([Anthropic pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)). Third-party engineering notes cite **466–499** system-prompt overhead tokens and **735** tool-definition tokens on Claude 4.x plus ~1,300 tokens for a ~1000×1000 screenshot — use those as **order-of-magnitude**, not a contract ([computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) for the thinking-cost guidance; ⚠️ exact overhead tokens are version-sensitive).

**[inferred] per-turn computer-use (Opus 4.8, 4k input + 350 output, no cache):** \(4{,}000/10^6 \times 5 + 350/10^6 \times 25 \approx \$0.029\)/turn. A **50-step** GUI task ≈ **$1.4** before history growth; a **318-call** OSWorld 2.0-shaped job ≈ **$9** if every call is a full vision turn — **[inferred]**, and prompt cache (10% of input on hits) plus batching can cut this sharply. History accumulation is the real p99: later turns re-send the screenshot *and* prior tool JSON.

**ACI / coding agents** burn text, not screenshots. SWE-agent’s published medians (**$1.21** success / **$2.52** fail, **$4** cap) are **2024 GPT-4 Turbo** dollars, not 2026 Opus. They still teach the NFR: **failures are more expensive than successes** because agents fail slowly.

**MCP Tasks** move *tool* latency off the LLM HTTP timeout. They do **not** reduce token cost of the planner that polls `tasks/get`.

### 2.3 Environment SKUs (not tokens)

E2B (2026 pricing page): Hobby free + usage, **1 h** session, **20** concurrent, **$100** usage credits; Pro **$150/mo**, **24 h** session, **100** concurrent (buy up to **1,100**). Usage: **$0.000028/s** for default **2 vCPU** (= **$0.1008/h**), **$0.0000045/GiB/s** RAM, Hobby **10 GiB** / Pro **20 GiB** storage included ([E2B pricing](https://e2b.dev/pricing)). **[inferred] overnight 8 h, 2 vCPU + 2 GiB:** \(8 \times (0.1008 + 2 \times 0.0000045 \times 3600) \approx \$0.81\) sandbox + LLM. Temporal workers while `wait_condition`: **$0** compute on the activity worker.

ChatGPT agent: **400 / 40** monthly *messages*, not dollars-per-task. ⚠️ converting messages to “tasks” without telemetry is fiction.

Claude Agent SDK: `maxBudgetUsd` covers **subagents**; hitting the cap stops background subagents (Claude Code **≥ v2.1.217**) ([agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)). Managed Agents: tokens + Anthropic sandbox; **not** ZDR/HIPAA; sessions persist server-side ([Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview)). ⚠️ do not quote unofficial `$ / session-hour` blogs as list price.

### 2.4 `$ per 1k long-horizon tasks` (explicitly inferred)

Define a **long-horizon task** as “OSWorld 2.0-class”: ~1.6 h human, hundreds of tool calls, binary success ≪ 50% at 500 steps.

| Shape | Token $ [inferred] | Env $ [inferred] | 1k tasks | Notes |
| --- | --- | --- | --- | --- |
| SWE-agent-like ACI, 2024 paper medians | ~$1.2–$2.5 | Docker-local ~0 | **$1.2k–$2.5k** | Inflate for 2026 frontier + thinking; cap still required |
| Computer-use 50 vision turns, Opus 4.8, no cache | ~$1.4 | E2B 0.3 h ≈ $0.03 | **~$1.4k** | Cheap relative to 318-call jobs |
| Computer-use 318 vision turns, Opus 4.8, no cache | ~$9 | E2B ~1.6 h ≈ $0.16 | **~$9k** | Cache/batch/browser-use tool can drop this; ⚠️ |
| ChatGPT agent consumer | n/a (quota) | included | n/a | 40–400 msgs/mo is the NFR, not $/task |
| Failed-slow coding agent | ~2× success | same | **budget to the fail tail** | SWE-agent empirical |

NFR to put on the SLO dashboard (because p50/p95/p99 wall-clock is unpublished): **$/successful task**, **$/failed task**, **turns-to-submit**, **cache hit rate**, **screenshot resolution**, **env-lease hours**, **% jobs hitting `maxBudgetUsd`**, **% Continue-As-New**. OSWorld 2.0: even the 2026 leader **20.6%** binary / **54.8%** partial (Claude Opus 4.8 batched, 500 steps) — you are buying **partial credit**, not 1k completions ([OSWorld 2.0](https://osworld-v2.xlang.ai/)).

⚠️ **p50/p95/p99 job latency:** not in METR, not in Operator, not in Managed Agents public docs. Proxy: step budgets (OSWorld 2.0 **150 / 300 / 500**), SWE-agent **12 vs 21** steps, GAIA human **6.8–17.7 min**, TheAgentCompany “hours of professional work” without a published agent-hour histogram.

Overnight cost cap (production pattern, all sourced knobs): `maxBudgetUsd` + env TTL + Temporal `ScheduleToClose` on the *workflow* + provider message quota. Without all four, a looping computer-use agent is an unbounded image-token meter.

---

## 3. Distributed Resilience & State

### 3.1 Durable execution is the control plane for hours-to-days

Temporal: Workflow Executions have **no time limit**; they **do** have history limits — warning **10,240** events; hard stop **51,200** events, **2,000** Updates, **10,000** Signals, **50 MB** history ([Events](https://docs.temporal.io/workflow-execution/event); [very long-running workflows](https://temporal.io/blog/very-long-running-workflows)). **Continue-As-New** checkpoints latest state into a new Run with the same Workflow Id ([Continue-As-New](https://docs.temporal.io/workflow-execution/continue-as-new); design pattern: every **100–1,000** iterations ([pattern](https://docs.temporal.io/design-patterns/continue-as-new))). LLM calls and tools **must** be Activities (non-deterministic); disable SDK retries (`attempts=1`) so Temporal owns retry ([Gemini + Temporal](https://ai.google.dev/gemini-api/docs/temporal-example); [durable AI agent tutorial](https://learn.temporal.io/tutorials/ai/durable-ai-agent/)). Replay restores state **without re-executing completed Activities** — the difference between “resume” and “double-charge the customer.”

Reset: terminate + copy history to a `WorkflowTask*` event; optional signal copy. Use for “rewind the agent to before it went off-policy,” not as a substitute for env snapshots. Principal Attribution (Cloud / self-hosted `frontend.enablePrincipalPropagation`): stamps **who** started/signaled/cancelled — the audit join key for autonomous actions ([Events](https://docs.temporal.io/workflow-execution/event)).

Circuit breakers for this domain:

| Breaker | Trip | Action |
| --- | --- | --- |
| Activity retry policy | 429 / 5xx / timeout | Exponential backoff; **do not** retry non-idempotent tools |
| `start_to_close_timeout` | LLM 2 min vs timeout 1 min | False retries → duplicate spend |
| `maxBudgetUsd` / message quota | Spend | `error_max_budget_usd`; stop subagents |
| Env health | Screenshot all-black, browser crash | Recreate lease; **do not** Continue-As-New the LLM history into a dead VM |
| Prompt-injection monitor | Classifier hit | Pause; require HITL (Operator / Anthropic) |
| History size | `GetContinueAsNewSuggested()` | CAN with compacted state, not full screenshot log |
| Kill switch | Policy | `Cancel` workflow + destroy sandbox + revoke MCP token |

### 3.2 Checkpoint / interrupt / resume (product map)

| System | Checkpoint | Interrupt | Resume |
| --- | --- | --- | --- |
| ChatGPT agent | VM state + tool mix | Take over browser, pause, stop → partial results; phone notify on done | Continues with new instructions without losing progress ([agent](https://openai.com/index/introducing-chatgpt-agent/)) |
| Operator CUA | Screenshot history in model context | Confirmation / Watch Mode | User provides input; loop continues |
| Claude Agent SDK | Sessions; `--resume` / JSONL transcripts (AutoGPT PR pattern) | Permissions, hooks | Session id |
| Managed Agents | Server-side event history + sandbox FS | User events mid-execution | “Resume cleanly after pauses”; scheduled cron deployments; **dreaming** = limited research preview |
| OpenHands TaskToolSet | Conversation saved to disk | Parent blocks on sub-agent | `resume` + task id |
| TheAgentCompany | **Checkpoint graders** (partial points) | n/a (eval) | Episode reset |
| Voyager | Skill library + Chroma | 4-round stuck → new curriculum task | Skills transfer to a **new world** |
| Generative Agents | Memory stream + reflections | n/a | Retrieval, not VM resume |
| MCP Tasks | `task_id` + server TTL | `tasks/cancel` | Poll after disconnect |
| Temporal + sandbox | Activity results + workspace snapshot | Cancel / signal | Replay; `/switch` provider with portable snapshot ([Temporal sandbox blog](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk)) |

**Memory ≠ checkpoint.** Generative Agents retrieve; Voyager *promotes code*; SWE-agent *truncates history*; Claude Code *compacts*. A resume that restores tokens but not the VM (cookies, `node_modules`, failed migration) is a new task wearing the old goal.

### 3.3 Credit assignment on long horizons

Classical RL: delayed reward, eligibility traces (Sutton & Barto). LLM agents mostly get **outcome** labels (unit tests, OSWorld execution scripts, GAIA short answers).

*Let’s Verify Step by Step* (Lightman et al., 2023): process-supervised reward models beat outcome-supervised RMs on MATH; large-scale PRM **78.2%** on a 500-problem MATH subset; PRM800K = **800k** step labels / **75k** solutions / **12k** problems; active learning **2.6×** data-efficient; PRM score = product of per-step correctness — one bad step kills the trajectory ([arXiv:2305.20050](https://arxiv.org/abs/2305.20050)). Map to agents: **TheAgentCompany checkpoints** and **OSWorld 2.0 ~27.25 scoring checkpoints/task** are the productized form of process credit. Voyager’s critic is an LLM-as-process-supervisor with a boolean gate into the skill library. SWE-bench Pro clusters failures: large models fail **semantic/algorithmic** multi-file edits; small models fail **syntax, tools, context**. Outcome-only RL on “tests passed” will reinforce lucky patches (SWE-bench Pro’s human-augmented requirements exist to shrink that false-negative space).

Production: store **per-checkpoint** telemetry (pass/fail, $, turns). Do not assign the entire overnight bill to the final `submit`.

### 3.4 Env-pool resilience

E2B/Daytona/Modal/Runloop as OpenHands runtimes: create/attach sandbox, start action server (often **:4444**), execute, cleanup; E2B is direct SDK vs HTTP action server ([OpenHands third-party runtimes](https://github.com/All-Hands-AI/OpenHands)). Anthropic sandbox-runtime: Seatbelt / bubblewrap / Windows WFP+ACLs; designed to wrap **MCP servers**, not only bash ([sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)). Claude Code warning: built-in Bash sandbox **does not** constrain file tools, MCP, or hooks unless the **whole process** is inside the runtime / devcontainer ([sandbox environments](https://code.claude.com/docs/en/sandbox-environments)). Browser farms: persist `contextId`, stealth, recording; cookies are **prod credentials in a pool** — isolate per tenant.

Sim-to-prod promotion gate: same MCP tool schema, **different** allowlists, credentials, and irreversible-action policy. Never promote a WebArena GitLab token policy to corp GitLab.

### 3.5 Interrupt, resume, and the three wait states

Long jobs spend most of their calendar time **waiting**, not decoding. Treat waits as first-class states:

| Wait | Meaning | Resume token | Failure if ignored |
| --- | --- | --- | --- |
| **Model-requested HITL** | CUA confirmation, Watch Mode, Anthropic injection classifier, OpenHands `WAITING_FOR_CONFIRMATION`, MCP `input_required` | User Signal / event | Silent stall overnight; Watch Mode with lid closed |
| **Tool-async** | MCP Task `working`; CI; compile | `task_id`; poll `tasks/get` | HTTP timeout; SIEM lost if join is JSON-RPC `id` only |
| **Idle durable** | Temporal `wait_condition`; Managed Agents pause; ChatGPT agent between scheduled runs | Workflow Id | Billing a GPU for sleep; losing sandbox TTL while the workflow still thinks the lease is live |

**Interrupt hierarchy (safest first):** (1) user take-over of the *environment* (ChatGPT agent browser take-over — model never sees passwords), (2) workflow Cancel (does not automatically roll back a completed purchase Activity), (3) sandbox kill (drops unsynced FS), (4) token revoke (stops the *next* MCP call, not the in-flight click). Resume tests must assert **env generation == workflow generation**: if Continue-As-New compacted away the screenshot log, the VM snapshot must still match the last committed checkpoint.

**Browser-farm specific:** Browserbase `contextId` + `--persist` (default true) + `--keepAlive` means cookies survive the agent process. That is resume **and** session fixation. Per-tenant contexts; TTL the context on job Cancel; recordings are an audit log and a PII store.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP

NIST SP 800-207: no implicit trust from network location; authenticate **and** authorize per session to a **resource** ([NIST SP 800-207](https://doi.org/10.6028/NIST.SP.800-207)). MCP 2025-11-25 is the agent-shaped instance: resource indicators bind tokens to **one** server; audience validation stops token passthrough; PRM discovery via `WWW-Authenticate`; HTTPS; no more default `/authorize` fallback (removed June 2025). Enterprise pattern: **IdP issues tokens, MCP server enforces RBAC, agent never sees long-lived SaaS keys.** Managed Agents: credentials stay out of the sandbox; a proxy fetches secrets when Claude calls MCP ([Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) + Anthropic engineering posts referenced from that overview).

Tool RBAC (minimum viable):

1. **Install-time allowlist** of MCP servers (managed settings / MDM) — Claude Code / Cowork CISO guidance: approved connectors, plugin allowlists ([Backslash CISO manual](https://www.backslash.security/blog/ciso-field-manual-for-claude-cowork-security) as practitioner overlay; enforce via Anthropic admin, not the blog).
2. **Per-tool `taskSupport` + elicitation** for step-up auth (MCP URL-mode elicitation is phishing-sensitive — treat URLs as untrusted).
3. **Runtime**: OpenHands security analyzer (low / medium / high → confirm); Claude permissions + hooks; Operator Watch Mode on email.
4. **Data**: ChatGPT agent one-click delete browsing data + logout; takeover mode **does not** send passwords to the model. Anthropic: do not put passwords in prompts unless you accept injection risk; `<robot_credentials>` is documented **and** warned ([computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)).

PII: Principal Attribution emails in Temporal Cloud Event History are PII — ACL the namespace. Screenshots of corporate desktops **are** PII/PHI; computer-use BAA eligibility ≠ Managed Agents ZDR. ⚠️ Managed Agents **ineligible** for ZDR and HIPAA BAA because sessions are stateful on Anthropic infra.

### 4.2 Sandbox vs computer-use VM vs host

| Boundary | Stops | Does not stop |
| --- | --- | --- |
| Bash-only sandbox | Shell exfil via curl | `Write` tool, MCP, hooks on host |
| Process sandbox-runtime | FS/net for *all* child processes | User-granted Screen Recording + Accessibility (Claude Code computer use) |
| Cloud sandbox (E2B, Managed Agents) | Host disk | Data the agent was given; egress if network on |
| Computer-use VM (ChatGPT agent) | Host OS | Anything in the VM after user login takeover |
| Language `vm` | Nothing that matters | OpenAI: not a security boundary |

Kill switches: (1) model-level refusal, (2) product confirmation, (3) classifier pause, (4) workflow Cancel, (5) destroy env, (6) revoke OAuth refresh, (7) blocklist domains (Operator gambling/adult/weapons). Operator also: real-time moderation, offline detection for CSAM/deception, site blocklist. Election-period extra: Anthropic monitored social posting / domain registration / government sites during 2024 beta ([Developing computer use](https://www.anthropic.com/research/developing-computer-use)).

Audit of autonomous actions: Temporal Event History + Principal; ChatGPT on-screen narration; Operator trajectories; OSWorld 2.0 **safety reports** on safety-sensitive tasks; MCP SIEM must join on **`task_id`**, not only JSON-RPC `id`, or async tools vanish from the log.

ASL-2 (Claude 3.5 Sonnet + computer use, 2024): Anthropic judged computer use **lowers the barrier** to applying existing skills, not a jump to ASL-3; they argued **shipping computer use at ASL-2** is safer than waiting for ASL-3 models. ChatGPT agent’s High bio treatment is the counter-example when terminal + web + connectors stack.

### 4.3 RBAC, PII, and audit of autonomous actions (checklist)

**Tool RBAC is not IdP RBAC.** A user who may *read* Salesforce may not allow an agent to *bulk-update* it at 03:00. Bind: (user, agent_id, tool_name, resource_indicator, time_window, spend_cap). MCP OAuth scopes are necessary and insufficient — add a **policy engine in the control plane** that the model cannot tool-call around (hooks in Claude Agent SDK; OpenHands analyzer; Temporal activity interceptor).

**PII surfaces unique to this topic:** (1) screenshots of mail, EHR, HRIS; (2) browser recordings (Browserbase); (3) Operator/ChatGPT cookies; (4) Temporal Principal emails; (5) MCP elicitation fields; (6) Generative Agents–style memory streams if used on real employees. Retention: ChatGPT one-click logout; Managed Agents session delete API; you must still delete **your** object store of screenshots.

**Audit minimum viable stream** (join keys in parens): workflow events (`workflow_id`, `run_id`, principal), model traces (`response_id` / `previous_response_id`), tool calls (`tool_use_id` / MCP `task_id`), env lease (`sandbox_id`, `contextId`), confirmation outcomes (`approved|denied|timeout`). If any hop is missing, overnight incidents become “the model did something.” Operator System Card and ChatGPT agent posts both treat confirmation as a **product** control, not a prompt suggestion — enterprise copies that by making deny-on-timeout the default for Watch Mode–class tools.

---

## 5. Production Failure Modes

| Mode | Mechanism | Evidence | Control |
| --- | --- | --- | --- |
| **Runaway spend** | Fail-slow loops; screenshot every step; thinking=`max`; subagents; retrying Activities that include LLM calls | SWE-agent fail **$2.52** vs success **$1.21**; OSWorld 2.0 **318** calls; tokenizer +30% on Claude 4.7+ | `maxBudgetUsd`, cache, batch actions, thinking=`medium`, Temporal attempts=1, env TTL |
| **Goal drift** | Open-ended curriculum / AutoGPT subgoals rewrite the contract; retrieval of wrong memories | Voyager *wants* drift (novelty search); Generative Agents breakdown = bad retrieval; Claude Yellowstone tangent | Frozen goal artifact + checkpoint acceptance tests; critic gated on **spec**, not vibes |
| **Environment leak** | Sim MCP schema in prod; cookies in browser pool; MCP token audience skip | OSWorld-MCP distractor tools; NIST ZT | Separate pools; RFC 8707; no Docker socket mount |
| **Unattended destructive tools** | Overnight worker + `rm`, migrate, send, purchase without HITL; opt-out of injection classifiers | Operator over-refusal **by design**; Anthropic classifier opt-out is **support-ticket**, not a flag in a YAML | Watch Mode class of tools; deny-by-default write; two-person rule for prod data plane |
| **Silent stall** | Flipbook misses toasts; waiting on `tasks/result` with dead worker; Watch Mode with user asleep; desktop computer-use with laptop lid closed | Anthropic: misses short-lived UI; Dispatch requires desktop awake; MCP `input_required` without pager | Heartbeats, `ScheduleToClose`, progress checkpoints, page the on-call on `input_required` |
| **Prompt injection → exfil** | On-screen / HTML / metadata instructions; connectors + logged-in browser | Operator extra monitor; ChatGPT agent High emphasis; computer-use docs | Classifier + confirm; disable unused connectors; no secrets in GUI |
| **OCR / visual edit collapse** | Random strings (API keys, DNA, Bitcoin) read from pixels | Operator autonomy eval: copy-paste avoided, OCR fails; nano/VS Code visual edits loop to timeout (400-step cap) | Prefer a11y/DOM/API over pixels for secrets; bash ACI for code |
| **Reward hacking / contamination** | SWE-bench saturation; GPL+private sets in SWE-bench Pro | METR reward-hack review; Pro commercial gap | Held-out repos; process graders |
| **Partial-success theater** | 54.8% partial / 20.6% binary | OSWorld 2.0 | Gate prod on **binary** + safety report, not leaderboard partial |
| **Double side-effect on resume** | Replay LLM, not Activity result; HTTP retry of `computer_call` | Temporal tutorial exists because this is the default bug | Idempotency keys; Activities for tools; never retry “click Pay” |
| **Quota cliff** | 40 ChatGPT agent messages; E2B 1h Hobby kill | Product docs | Separate overnight SKU; 24h sandbox; durable wait |
| **Eval-prod skew** | Self-hosted WebArena vs live WebVoyager; LibreOffice vs Excel | ChatGPT agent SpreadsheetBench **45.5%** `.xlsx` vs Copilot **20%** but **OSX+LibreOffice** vs authors’ Windows+Excel | Declare the env in the SLO |

OpenAI CUA prompt-injection red-team: model ignored **all but one** early internal case — ⚠️ that is a lab number, not a residual-risk SLO. Operator **55%** `not_overrefuse` vs GPT-4o **90%** on ChatGPT over-refusal eval: autonomy was tuned **cautious**, which shows up as stalled jobs, not only as safety wins.

**Stall vs spend (coupled tails).** SWE-agent: successes are short; failures eat the **$4** cap. Computer use: stalls still take screenshots (Operator 400-step autonomy eval). MCP Tasks: a forgotten `working` task holds env CPU (E2B per-second) until `ttl`. Design the overnight cap as **min(token budget, env TTL, workflow ScheduleToClose, vendor quota)** — the first trip should page, not the last.

**Destructive-tool taxonomy (unattended).** Read-only (search, screenshot) / reversible (local git) / reversible-with-SLA (email recall, ticket comment) / irreversible (wire, prod `DROP`, public social). Operator refuses stock trading and similar; ChatGPT agent refuses bank transfers and requires confirmation before purchase. Your copies of those lists belong in the **activity interceptor**, because a new MCP server will not inherit the model’s refusal training.

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A — Overnight SWE worker (repo → PR)

**Goal:** unattended 4–12 h issue resolution.

| Option | Horizon fit | $ | Resume | Blast radius |
| --- | --- | --- | --- | --- |
| SWE-agent / OpenHands + Docker | SWE-bench Pro **<25%** Pass@1 at GPT-5/Opus 4.1 (2025 paper) | Paper **$1–4**/issue era; ⚠️ 2026 thinking models cost more | Disk conversation / Temporal | Repo + tests |
| Claude Agent SDK in cluster | Same ACI class; `maxBudgetUsd` | Tokens + your GPU/CPU | Session | As above |
| Managed Agents | Hours; Anthropic sandbox | Tokens; no ZDR | First-class | No local FS; MCP only |
| Computer use on a desktop | Wrong interface (Operator failed terminal/OCR) | Vision tax | Weak | Entire GUI |

**Pick:** ACI in a **disposable** repo sandbox + Temporal + fail2pass tests as process credit. Computer-use is a fallback when the IDE/GUI has no API — not the default overnight coder.

### 6.2 Scenario B — Computer-use RPA on internal web (no API)

| Option | Reliability | Oversight | Cost driver |
| --- | --- | --- | --- |
| Playwright/BrowserGym selectors | High if DOM stable | Classic RPA | Engineer time |
| Stagehand + Browserbase MCP | Self-healing NL actions | Farm recordings | Browser hours + LLM |
| Anthropic computer + browser toolsets | Batch actions; page structure on browser tool | Classifier HITL | Screenshots |
| OpenAI CUA / ChatGPT agent | Watch Mode on email-class sites | Strong product HITL | Quotas / vision |

**Pick:** DOM/MCP first; pixels for the long tail. Never skip confirmation on checkout. Sim: WebArena clone of the internal app; prod: allowlisted host + dedicated browser context per tenant.

### 6.3 Scenario C — Generalist overnight “operator” (research + act)

ChatGPT agent is the reference topology: **one VM**, visual+text+terminal+connectors, interruptible, scheduled recurrence (weekly metrics). Treat as **High** residual risk if connectors + login takeover are on. Disable connectors when unused. Cap with message quota **and** an external Temporal watchdog (vendor quota is not your kill switch).

### 6.4 Scenario D — Multi-hour MCP tool (migrate / compile / browser job)

Use MCP Tasks (`taskSupport=required`) so the planner is not holding an HTTP socket for 30 h. SIEM on `task_id`. `ttl` must exceed the job; `tasks/cancel` wired to the same kill switch as Temporal Cancel. Do not store migrate credentials in the sandbox.

### 6.5 Scenario E — Lifelong skill agent (Voyager-shaped)

Skill library in vector DB **with** promotion gated by tests (Voyager critic). Curriculum is a **cost amplifier** (novelty search). Enterprise analog: approved runbooks, not open-ended “discover tools.” Generative Agents memory is for **simulation / UX personas**, not for prod change management.

### 6.6 Trade-off matrix (interview)

| Axis | Low autonomy | High autonomy |
| --- | --- | --- |
| **Loop owner** | Your Messages API | Managed Agents / ChatGPT agent |
| **Observation** | ACI / a11y / MCP | Pixels |
| **Credit** | Unit tests + checkpoints | Outcome-only “looks done” |
| **Memory** | Frozen skills + CAN | Unbounded chat log |
| **Env** | Resettable gym / sandbox | Logged-in prod browser |
| **Stop** | `maxBudgetUsd` + tests | Model “I’m finished” |
| **Audit** | Event history + principal | Screenshot dump |
| **HIPAA/ZDR** | Messages API computer use (BAA per Anthropic 2026 blog) | Managed Agents **no** |
| **METR reading** | 80% horizon for SLOs | 50% horizon for research PR |

**Decision rule:** if the side effect is irreversible or PII-bearing, the control plane must be able to **stop the data plane without the model’s cooperation** (Cancel + destroy lease + revoke token). If it cannot, it is a demo, not an overnight worker.

### 6.7 What to measure in a design review

1. Human-time horizon of the *task distribution* (METR method) vs required **reliability** (50% is not an SLO; use 80% trend, ~5× shorter).
2. Binary vs partial (OSWorld 2.0).
3. Public vs commercial SWE-bench Pro gap (contamination + enterprise messiness).
4. $/success vs $/fail (SWE-agent).
5. Step budget vs Continue-As-New frequency vs env TTL (the three clocks must nest: TTL > history rotation > step budget).
6. Injection path: pixels, DOM, MCP tool output, elicitation URL.
7. Resume test: kill worker at 50% checkpoints; **no duplicate side effects**; env state matches token state.

### 6.8 Environment-pool sizing (control vs data)

**[inferred] pool math, not a vendor SLO:** concurrent overnight jobs \(N\) × (lease hours / wall hours) = required warm capacity. E2B Pro default **100** concurrent (buy to **1,100**); Hobby **20** and **1 h** TTL will kill an 8 h job. OSWorld-style VMs are heavier than E2B 2 vCPU: budget AMI pull + display (Xvfb) + browser RAM, not just the LLM replica count. Browser farms: concurrency is **sessions**, not vCPU — Stagehand/Browserbase keep-alive sessions are the data plane analog of KV cache: sticky, tenant-scoped, expensive to cold-start if you re-login every resume.

**Sim-to-prod promotion:** gym (Gymnasium/`terminated`) → staging MCP + fake IdP → prod MCP with resource indicators. Gate on: (a) binary grader, (b) injection drill on the *prod-like* DOM, (c) duplicate-side-effect test, (d) kill-switch drill that does not need the model. METR 50% horizon sizes the *research* bet; **80% horizon (~5× shorter in the 2025 paper)** sizes the SLO. OSWorld 2.0 **20.6%** binary at 500 steps is the honesty check for “we will automate the analyst’s afternoon.”

---

## Sources

1. https://metr.org/time-horizons/
2. https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/
3. https://arxiv.org/abs/2503.14499
4. https://metr.substack.com/p/2026-1-29-time-horizon-1-1
5. https://openai.com/index/introducing-operator/
6. https://openai.com/index/computer-using-agent/
7. https://cdn.openai.com/operator_system_card.pdf
8. https://openai.com/index/introducing-chatgpt-agent/
9. https://developers.openai.com/api/docs/guides/tools-computer-use
10. https://developers.openai.com/api/docs/models/computer-use-preview
11. https://techcrunch.com/2025/01/23/openai-launches-operator-an-ai-agent-that-performs-tasks-autonomously/
12. https://www.anthropic.com/news/3-5-models-and-computer-use
13. https://www.anthropic.com/research/developing-computer-use
14. https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
15. https://claude.com/blog/computer-use-skills-api-files-api
16. https://claude.com/blog/dispatch-and-computer-use
17. https://code.claude.com/docs/en/computer-use
18. https://code.claude.com/docs/en/agent-sdk/overview
19. https://code.claude.com/docs/en/agent-sdk/agent-loop
20. https://platform.claude.com/docs/en/managed-agents/overview
21. https://docs.anthropic.com/en/docs/about-claude/pricing
22. https://arxiv.org/abs/2404.07972
23. https://os-world.github.io/
24. https://github.com/xlang-ai/OSWorld
25. https://osworld-v2.xlang.ai/
26. https://arxiv.org/abs/2606.29537
27. https://arxiv.org/abs/2510.24563
28. https://arxiv.org/abs/2509.16941
29. https://labs.scale.com/papers/swe-bench-pro
30. https://arxiv.org/abs/2405.15793
31. https://github.com/SWE-agent/SWE-agent
32. https://arxiv.org/abs/2307.13854
33. https://github.com/web-arena-x/webarena
34. https://arxiv.org/abs/2311.12983
35. https://arxiv.org/abs/2412.14161
36. https://the-agent-company.com
37. https://arxiv.org/abs/2305.16291
38. https://voyager.minedojo.org/
39. https://arxiv.org/abs/2304.03442
40. https://github.com/Significant-Gravitas/AutoGPT
41. https://learn.temporal.io/tutorials/ai/durable-ai-agent/
42. https://docs.temporal.io/workflow-execution
43. https://docs.temporal.io/workflow-execution/event
44. https://docs.temporal.io/workflow-execution/continue-as-new
45. https://docs.temporal.io/design-patterns/continue-as-new
46. https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk
47. https://temporal.io/blog/very-long-running-workflows
48. https://ai.google.dev/gemini-api/docs/temporal-example
49. https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
50. https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks
51. https://github.com/anthropic-experimental/sandbox-runtime
52. https://code.claude.com/docs/en/sandbox-environments
53. https://gymnasium.farama.org/
54. https://github.com/ServiceNow/BrowserGym
55. https://docs.openhands.dev/sdk/arch/agent
56. https://github.com/All-Hands-AI/OpenHands
57. https://e2b.dev/pricing
58. https://www.e2b.dev/docs/agents/openai-agents-sdk
59. https://www.browserbase.com/mcp
60. https://github.com/browserbase/stagehand
61. https://arxiv.org/abs/2305.20050
62. https://doi.org/10.6028/NIST.SP.800-207
63. https://www.backslash.security/blog/ciso-field-manual-for-claude-cowork-security
