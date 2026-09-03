# Module 11: Specialized Agents

## What Is This?

A **specialized agent** is an agent designed for one specific type of task, with tools and evaluation methods tailored to that domain. The specialization isn't in the model weights -- it's in the **runtime**: the sandbox it runs in, the tools it has access to, and how its output is verified.

The four main specialties are:
- **Coding agents** (e.g., Claude Code, Cursor, GitHub Copilot): Write, edit, test, and debug code. They run in sandboxed environments with access to terminals, file systems, and test suites. Their work is verified by running the tests -- if the tests pass, the code is probably correct.
- **Browser agents** (e.g., Claude CUA, Anthropic's computer use): Navigate websites, fill out forms, click buttons, extract data. They see the screen (either as structured HTML or as pixel screenshots) and generate mouse/keyboard actions.
- **Research agents**: Search the web, read documents, synthesize findings into reports. They're evaluated on factual accuracy and citation quality -- every claim should trace back to a source.
- **Data agents**: Query databases, run analyses, generate charts. They write SQL or Python, execute it against real data, and return results. They need strict guardrails because a bad SQL query can be destructive.

A simple example: A coding agent tasked with "fix the login bug" might (1) read the error logs, (2) find the relevant source file, (3) write a failing test that reproduces the bug, (4) edit the code to fix it, (5) run the test suite to verify, (6) create a pull request.

## Why It Matters

Most production AI applications use specialized agents, not general-purpose ones. Understanding the unique challenges of each specialty -- how to sandbox them, what tools they need, how to evaluate their output -- is essential for building reliable AI systems.

---

## 2. Core Concepts

### The invariant: the model never owns the runtime

Think of the LLM as a chess player who calls out moves and a referee who actually moves the pieces, enforces rules, and describes the resulting board. The model emits tool calls (ACI commands, `computer_call`, `browser_toolset` members, SQL, search/fetch). A specialty runtime -- sandbox, browser pool, warehouse session, research job -- executes them and returns observations. If you let the chess player also move pieces, you have no referee. Every production failure mode in this module traces back to violating this invariant.

### Four specialties = four runtimes, not four weights

You do not need four fine-tuned models. You need four execution environments:
- **Coding**: sandboxed bash + editor + tests + git
- **Browser**: screenshot+pointer or a11y-tree+refs in an isolated browser
- **Research**: web search, fetch, MCP search/fetch, files
- **Data**: read-only SQL, notebooks, warehouse APIs

The LLM is (often) the same. The runtime is what changes.

### Specialty = runtime + oracle + identity

Each specialty is defined by three things:
1. **Runtime** -- where the tool calls execute (Docker container, browser profile, warehouse session, search API)
2. **Oracle** -- what "done" means (hidden tests pass, page state matches, citation accuracy, execution accuracy + RLS)
3. **Identity** -- who the runtime acts as (developer, bot account, end-user UC identity, Snowflake role)

This is the single most important interview frame for this module.

### Shared control plane vs domain data planes

A useful enterprise topology separates a shared control plane from specialized execution planes. The control plane handles identity, tenant, goal, risk tier, budget, deadline, and model routing. Each domain implements its own data plane for planning, observation, action, verification, durable state, and governance.

| Layer | Shared responsibility | Domain-specific implementation |
|---|---|---|
| Intake/control | identity, tenant, goal, risk tier, budget, deadline, model routing | repository scope; permitted sites/account; research question; permitted datasets |
| Planning | decomposition, dependency graph, retry/stop rules | change plan; navigation plan; source plan; analysis/query plan |
| Observation | normalize tool results into model-readable state | files/symbols/tests; DOM/accessibility tree/screenshots; search results/pages/PDFs; catalog/schema/samples |
| Action | schema-validated tool dispatch | search/edit/build/shell; navigate/click/type/download; search/open/extract/calculate; SQL/Python/notebook |
| Verification | outcome and trajectory graders | tests/static analysis/diff review; DOM/business-state checks; claim-evidence checks; data-quality/statistical checks |
| Durable state | run event log, checkpoints, artifacts, provenance | worktree/commit; browser context and receipt; source snapshot/evidence ledger; query job/data snapshot/report |
| Governance | authorization, secret brokerage, policy, audit | repository/CI scopes; origin/action scopes; source/privacy rules; row/column policies |

This is compatible with an orchestrator-worker pattern when subtasks cannot be predicted in advance. It does **not** imply that every task should be agentic: documented guidance recommends fixed workflows or simpler calls where paths are known, because autonomy exchanges predictability, latency, and cost for flexibility.

---

## 3. How It Works

### 3.1 System Topology

#### Four specialties, four planes

| Specialty | Control plane | Tool / data plane | Persistence | Oracle (what "done" means) |
| --- | --- | --- | --- | --- |
| **Coding** | Loop budget, approval policy, PR state machine | Sandboxed bash + editor + tests + git | Worktree / cloud VM / checkpoint | Hidden tests (`FAIL_TO_PASS` + `PASS_TO_PASS`); CI green |
| **Browser** | Step cap, watch-mode, domain allowlist | Screenshot+pointer **or** a11y-tree+refs | Browser profile / storage-state / remote VM | Functional assertion on page/DB state (WebArena-style), not action-trace match |
| **Research** | Lead plan, subagent fan-out, citation pass | Web search, fetch, MCP search/fetch, files | Memory / files / background job id | Rubric (factuality, citation accuracy, completeness); GAIA string-match on a subset |
| **Data** | Semantic model version, warehouse timeout, row filter | Read-only SQL, notebooks, warehouse APIs | Warehouse session + query history | Execution accuracy + trusted-asset match; RLS-empty is success, not a bug |

#### Magentic-One and why specialized agents are specialized runtimes

Microsoft's Magentic-One (Fourney et al., arXiv:2411.04468) is the explicit **generalist overlay**: an Orchestrator with a Task Ledger + Progress Ledger plus tool-shaped workers (WebSurfer, FileSurfer, Coder, ComputerTerminal). Ablation results:

- Removing full ledgers: **-31%**
- Removing any one worker: **-21%** (Coder/Executor) to **-39%** (FileSurfer)
- Published GPT-4o-era completion: **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench**

That topology is a reminder: "specialized agents" are usually **specialized runtimes**, not specialized weights. Anthropic's Jun 2025 research post is the complementary lesson: coding is a **poor** fit for their orchestrator-worker research pattern (few truly parallelizable subtasks; agents weak at real-time coordination). Do not copy-paste a research DAG onto a git loop.

### 3.2 Coding Agents

#### SWE-agent ACI

**SWE-agent (Yang et al., NeurIPS 2024, arXiv:2405.15793).** The contribution is the **agent-computer interface (ACI)**, not a new model. Raw shell is a hostile API for LMs (unbounded `cat`, no lint-on-edit, no editor cursor). SWE-agent ships a small command set for search / view / edit / test. On the original SWE-bench test set (**2,294** GitHub issues from **12** Python repos, Jimenez et al., ICLR 2024, arXiv:2310.06770):

- **12.47%** resolved (286/2,294) and **18.00%** on Lite (54/300)
- HumanEvalFix **87.7%** pass@1
- Vs shell-only with the same GPT-4 Turbo: **+64% relative**
- Vs RAG on Lite: **8-13x more costly**, **6.7x** resolved-rate

That cost/accuracy trade-off is still the coding-agent budget conversation. Anthropic reports that changing a tool from relative to required absolute paths eliminated a repeated failure in its SWE-bench agent experiments -- interface design materially affects performance.

The observation stream should include task specification, repository tree, targeted file excerpts, symbol/reference search, command output, test failures, and current diff. Actions should be narrow, typed operations even if implemented through a general shell: `search`, `read`, `patch`, `test`, `lint`, `build`, `git_diff`, and `submit`. Absolute repository paths, bounded outputs, command deadlines, and explicit working directories reduce interface ambiguity.

The maintained SWE-agent repository now directs new users toward the smaller mini-SWE-agent implementation, so older SWE-agent examples should not be assumed to represent the current recommended scaffold. **OpenHands / Devin-class products** use the same topology as SWE-agent (event loop + sandbox + editor + browser + PR), different packaging. Treat them as **cloud coding runtimes** in the Copilot/Codex-cloud column: per-task VM, firewall, PR as the saga log. Do not mix their unpublished marketing scores with SWE-bench Verified aggregator pages.

#### Sandboxed terminals (the actual coding data plane)

| Runtime | Isolation | Network default | Write default | Approval overlay |
| --- | --- | --- | --- | --- |
| **Cursor** (v2.0+; network policy since 2.5, Feb 2026) | macOS Seatbelt; Linux Landlock+seccomp (kernel **6.2+**); UID 0 *inside* Linux user namespace | Deny, then `sandbox.json` + Cursor package-manager defaults | Workspace RW; `.git/hooks`, `.git/config`, `.vscode`, `.cursor/*.json` write-blocked | Run Modes: Auto-review (default as of **3.6**, 2026-05-29), Allowlist, Run Everything. Cloud Agents: **no** Run Modes (dedicated VM) |
| **Claude Code** | macOS Seatbelt; Linux/WSL2 **bubblewrap** + `socat` proxy; WSL1 unsupported | **No pre-allowed domains**; prompt / classifier, or `strictAllowlist` (v**2.1.219+**, user/managed/CLI settings only -- **repo** `.claude/settings.json` cannot set it) | Workspace via FS policy; `/sandbox` panel | Permission rules + Auto mode classifier. `failIfUnavailable` blocks start if bwrap missing |
| **Codex CLI** | macOS Seatbelt; Linux `bwrap`+seccomp; WSL2 Linux path; native Windows sandbox | `workspace-write` **network off** unless `[sandbox_workspace_write].network_access = true` | `read-only` / `workspace-write` / `danger-full-access` | `on-request` / `untrusted` / `never` / `auto_review` |
| **Copilot cloud agent** | GitHub Actions appliance | Firewall **on**; recommended allowlist **on** (pkg repos, registries, CAs, **Playwright browser download hosts**) | Repo clone + PR branch | Org can lock firewall / recommended list / whether repos may add custom rules (changelog 2026-04-03) |
| **SWE-bench / SWE-agent eval** | Docker per instance | Harness-controlled | Ephemeral container | N/A (batch eval) |

Cursor `sandbox.json`: `networkPolicy.default` **deny**; deny **beats** allow; RFC1918 + **169.254.169.254** + IPv6 ULA/link-local blocked (SSRF). Team-admin allowlist **replaces** (does not union) local allow lists. Merge order: per-user < per-repo < team-admin < hardcoded. Linux sandbox remaps UID to 0 -- scripts must use `CURSOR_ORIG_UID`/`GID` for Docker `--user`.

Claude Code: sandbox applies to **Bash**, not Read/Write/WebFetch/WebSearch/MCP/hooks. `deniedDomains` wins over `allowedDomains` wildcards. `WebFetch(domain:...)` allow rules **widen** Bash egress. MITM proxy can inject tokens only onto allowlisted hosts (`injectHosts`). Nested Docker: `enableWeakerNestedSandbox` bind-mounts container `/proc`. Anthropic's 2026 containment review reports OS-level sandboxing reduced Claude Code permission prompts by **84%** in its telemetry, and approximately **93%** user approval of prompts, illustrating approval fatigue rather than proving approvals are safe.

Codex cloud (2025-05 preview): internet **disabled** during the task; only the provided GitHub repo + setup-script deps. That is a different threat model than laptop CLI.

#### Test oracles (FAIL_TO_PASS, PASS_TO_PASS)

SWE-bench gold is **not** "the patch looks right." It is execution of **FAIL_TO_PASS** (the failing tests that define the issue) without regressing **PASS_TO_PASS**. The harness applies the gold `test_patch` to expose those tests, then runs the agent's `model_patch` in a frozen Docker snapshot of `base_commit`. That is why "it compiled on my laptop" is not an eval. Agentless adds **generated reproduction tests** as a cheap filter before submitting a candidate -- a second, weaker oracle that is allowed to be noisy because the hidden tests still decide.

Production PR loops should treat unit tests as a **necessary but leaky** oracle: flaky tests, snapshot tests, and missing coverage all create false greens. Bind merge to **CI on the agent's branch**, not to the model's self-report. Log the exact pytest node-ids the agent ran; "all tests passed" in the chat is not evidence.

Agent-evaluation guidance distinguishes the transcript from the actual environment outcome and recommends combining code-based, model-based, and human graders.

#### SWE-bench splits and Verified saturation

**SWE-bench Verified** (OpenAI + authors, 2024-08-13): **500** engineer-confirmed solvable instances. GPT-4o on the then-best scaffold: **33.2%** (vs **16%** on original SWE-bench); Agentless roughly doubled **16% -> 32%**. Difficulty slices: **196** <15-minute, **45** >1-hour. Official 2025+ "model vs scaffold" split: the Verified leaderboard compares arbitrary systems; a **mini-SWE-agent + bash-only** track exists to compare LMs without ACI sugar.

OpenAI (2026) later argued Verified is **contaminated / saturated** for frontier reporting and pointed at **SWE-bench Pro** (731-task public split; they also later estimated **~30%** of Pro tasks are broken). Interview takeaway: quote a **named split + named scaffold + date**; do not treat "96% SWE-bench Verified" aggregator pages as an SLO.

#### Agentless as anti-agent control

**Agentless (Xia et al., arXiv:2407.01489)** is the anti-agent control: localize -> repair -> (optional) reproduction-test rerank. No autonomous tool loop. Reported **32.00%** on Lite at **$0.70**/instance (later revision; earlier abstract **27.33% / $0.34**). Production meaning: if your "coding agent" is really localize+patch+test, a **pipeline** with a test oracle is cheaper and more auditable than a 50-turn ReAct loop.

#### Repo maps vs agentic search

Three context strategies, one job: **do not dump the repo into the prompt**.

- **Aider** (Gauthier) builds a **token-budgeted** map: tree-sitter tags -> file graph -> personalized PageRank -> `--map-tokens` default **1k**, expanded when no files are in chat. Files already in chat are **omitted** from the map.
- **Claude Code** uses the opposite topology: **agentic search** over the working tree (no user file picker required) plus `CLAUDE.md` as standing instructions.
- **Cursor** indexes the repo for retrieval and then runs an **agent loop** with a sandboxed terminal.
- **Codex** (OpenAI, 2025-05 cloud preview + CLI) preloads the GitHub repo into a **per-task cloud container**; CLI uses local worktrees.

#### PR loops

Claude Code GitHub Action (`anthropics/claude-code-action`): `@claude` on issue/PR comments, or `prompt:` on any GitHub event; auth via `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; also Bedrock / Vertex / Foundry. Autonomous runs have **no HITL**, so tools not pre-allowed **stall**. Fork PRs on public repos: GitHub **withholds secrets**. Copilot cloud agent: opens a PR, firewall-blocked destinations are **commented on the PR**. Codex: task completes -> commit in the sandbox -> human opens GitHub PR. Cursor Cloud Agents: dedicated machine, no local approval prompts. The durable object is the **PR + CI**, not the chat transcript.

PR loop as the durable saga: issue comment -> enqueue -> sandbox clone -> patch -> tests -> push branch -> `gh pr create` -> CI -> human merge. Compensating action is **close the PR / revert**, not "undo the chat." Fork-PR secret withholding is a **poisoned-queue** defense.

#### Long-horizon mechanics

Long tasks need a persistent task ledger, current plan, completed-work record, test status, and clean checkpoint rather than relying on conversation history. Anthropic's long-running-agent pattern uses an initializer followed by coding sessions that leave structured progress artifacts for the next context window. Its later full-application harness separates planner, generator, and evaluator roles and uses browser-based verification; the published cost/time examples are implementation anecdotes, not general service-level benchmarks.

#### Coding verification checklist

Merged from both sources -- use this as a production gate:

1. **FAIL_TO_PASS tests pass** -- the hidden tests that define the issue
2. **PASS_TO_PASS tests pass** -- no regressions
3. **Type / lint / security checks clean** -- static analysis
4. **Dependency and generated-file policy** -- no unexpected deps added
5. **Diff scope** -- every changed line traces to the task
6. **CI green on the agent's branch** -- not the model's self-report
7. **Human review for architectural intent** -- the model cannot judge org conventions
8. **Log exact test node-ids** -- "all tests passed" in chat is not evidence
9. **Mutation / property tests** when available -- visible tests can be gamed
10. **Named reviewer for auth, crypto, permissions, migrations, secrets** -- never auto-merge these

### 3.3 Browser Agents

#### Two observation channels

| Channel | Examples | Strength | Cost / fragility |
| --- | --- | --- | --- |
| **Pixels + pointer** | OpenAI CUA (Jan 2025); Anthropic `computer_toolset_20260801` (17 members: screenshot, click, type, zoom); OSWorld | Works on any GUI; no a11y required | Every step is an image. Coordinate drift. Hidden a11y text invisible; **visible** prompt-injection text is visible |
| **Structured (a11y / DOM refs)** | Playwright MCP (`browser_snapshot` -> `ref=e5`); Anthropic `browser_toolset_20260801` (27 default members: `navigate`, `read_page`, `left_click`, `screenshot`; optional `javascript_exec`, `file_upload`, `read_console`, `read_network`) | Refs survive reflow; cheaper than full screenshots; no vision model required for MCP | Canvas/custom widgets missing from a11y; `javascript_exec` is **page-privileged RCE**; Playwright `--allowed-origins` **does not** constrain redirects (docs: not a security boundary) |

#### Anthropic browser/computer toolsets

Anthropic's own split (2026-08 toolsets): **browser use** for page-scoped work (a11y + pixels); **computer use** for a full desktop. Both are **client** toolsets: Anthropic defines the schema; **your executor** runs every call. Not available in Claude Managed Agents as of the 2026-08 docs.

- Computer-use models on the current toolset: Fable 5, Mythos 5, Opus 5, Sonnet 5, Opus 4.8; older Opus/Sonnet 4.x stay on `computer_20251124` + beta header
- Batch actions: several members per turn, run **in order**
- `read_page` returns an a11y tree with `[ref_2]` tags, default depth **15**, cap **50,000** characters
- Prefer refs over coordinates when the tree exists
- Executor must validate coordinates against the **viewport you actually captured**, not the model's claimed screen size

#### OpenAI CUA with all benchmark numbers

OpenAI CUA (Operator research preview, 2025-01-23; July 2025 folded into **ChatGPT agent mode**; `operator.chatgpt.com` sunset). Loop: screenshot -> CoT -> mouse/keyboard -> confirm on sensitive actions. Official numbers:

- **OSWorld 38.1%** (prev SOTA 22.0%, human **72.4%**; original OSWorld paper: then-best model **12.24%** on **369** tasks)
- **WebArena 58.1%** (prev computer-use SOTA 36.2%, browsing SOTA 57.1%, human **78.2%**)
- **WebVoyager 87%** (the 2024 paper's own agent was **59.1%** on 15 live sites -- CUA's 87% is a later SOTA on that bench, not the paper baseline)

OSWorld (Xie et al.) is a **full OS** bench (Ubuntu/Windows/macOS), not a browser bench -- that is why 38% vs WebVoyager 87% is not a contradiction: WebVoyager tasks are shorter live-web tasks (Amazon, GitHub, Maps). Test-time scaling: more allowed steps -> higher OSWorld. Operator reliability is **prompt-sensitive** (tagvenue concert-hall: **8/10** with filter hints vs **3/10** without).

Safety layers: refusals, site blocklist, realtime moderation, confirm-before-side-effect, watch-mode on email, extra monitor model, Preparedness evals showing **no incremental** biorisk/autonomy vs GPT-4o. 2026 API: `gpt-5.4`+ trained for mixed harnesses -- built-in `computer` tool, custom Playwright/Selenium/VNC/MCP, or **code-execution** that mixes visual + DOM. `detail: "original"` screenshots: GPT-5.6 does **not** resize; large frames blow input tokens. Recommended downsample: **1440x900** / **1600x900** with coordinate remap.

#### WebArena, BrowserGym

**WebArena (Zhou et al., ICLR 2024, arXiv:2307.13854):** self-hosted Docker sites (shopping, CMS, GitLab-like, Reddit-like) + maps/calculator/Wikipedia; **812** long-horizon tasks; functional eval, not action-sequence match. Paper GPT-4 agent **14.41%** vs human **78.24%**. VisualWebArena (Koh et al., ACL 2024) adds visually grounded tasks; text-only agents fail when the cue is in the screenshot.

BrowserGym (ServiceNow, TMLR 2025) is the current **eval bus**: MiniWoB, WebArena, WebArenaVerified, VisualWebArena, WorkArena, AssistantBench, WebLINX, OpenApps, TimeWarp -- Playwright underneath, parallelizable.

**Mind2Web** collected 2,350 tasks across 137 sites for cross-task, cross-site, and cross-domain generalization, but offline action prediction does not validate a complete live transaction. **Mind2Web 2** evaluates 130 real-time, long-horizon research tasks and required more than 1,000 hours of human work to construct; live task state remains a reproducibility constraint.

**OSWorld 2.0** adds 108 longer workflows involving streaming, dynamic content, cross-source integration, implicit state, and visual precision to the original 369-task benchmark.

#### Playwright MCP

**Playwright MCP** (`@playwright/mcp`): MCP server, a11y snapshots, no vision required. Default **headed**, persistent profile per workspace hash; `--isolated` for ephemeral; `--extension` to attach to a real browser. HTTP transport `:8931`, **5 s** heartbeat (`PLAYWRIGHT_MCP_PING_TIMEOUT_MS`). `browser_run_code_unsafe` is **RCE-equivalent** -- trusted clients only. Copilot cloud agent's recommended firewall allowlist **includes Playwright browser-download hosts** -- the coding agent and the browser agent share a pool. Concurrent clients on one persistent profile **conflict**; use `--isolated` or distinct `--user-data-dir`.

#### Allowlists

- **Anthropic computer-use docs**: dedicated VM, no secrets in the environment, **domain allowlist**, HITL on purchases/ToS/cookies; classifiers on screenshots steer toward user confirmation on suspected injections (opt-out via support). Browser-use docs: enforce allowlist at **network layer and after redirects** in `navigate`; block loopback/link-local/private unless required; build reads from **rendered** a11y/text, not raw DOM (hidden-instruction defense); leave `javascript_exec` / `file_upload` off.
- **Operator**: site **blocklist** (gambling, adult, weapons), watch-mode on email, confirm-before-side-effect, extra monitor model that **pauses** on suspicious pixels.
- **Cursor**: Browser Protection can require approval for Browser tools separately from shell.
- **Playwright** `--allowed-origins`: trusted origins for requests; **not** redirect-safe.

#### BrowserContext isolation

Browser context must be part of task state. Playwright BrowserContexts provide isolated, incognito-like profiles, enabling clean sessions per task or tenant. Stored authentication state can contain cookies and headers capable of impersonating a user and must not be committed or attached to ordinary traces. Parallel tests or agents must use distinct backend accounts or other unique state because isolated browser storage does not prevent collisions in the target application.

Use separate processes or containers where a browser compromise or extension boundary is in scope. One writer per user/account workflow; isolate task contexts; serialize scarce or destructive resources.

Financial, destructive, externally visible, credential-changing, or legally consequential actions need preview and approval. Separate navigation/read permission from form-fill, upload, message, purchase, delete, credential, and publish permissions. Reconfirm target origin, account, amount, recipients, and side effects immediately before a consequential action.

Browser same-origin policy does not automatically constrain an agent that can read one origin and navigate or transmit to another. University of Washington researchers demonstrated cross-origin exfiltration paths in agentic browsers and disclosed findings to vendors. Enforce an agent-level information-flow policy across origins.

#### Verification: judge by end-state, not action trace

Browser success should be judged by resulting application state, not the agent's final statement: order ID exists, ticket status changed, file downloaded with expected hash, or form persisted. WebArena's functional evaluators are the model -- they check page/DB state, not whether the agent clicked the right buttons in the right order. A pixel agent that took 40 steps but left the page in the correct state succeeded; one that reported "done" while the form was unsaved failed.

Browser and desktop latency is often dominated by environment and deliberation rather than raw generation. OSWorld-Human provides human reference trajectories for all 369 original OSWorld tasks; it reports that agent trajectories can add up to 30 actions and that later steps can take roughly three times as long as early steps because context and reasoning accumulate.

### 3.4 Research Agents

#### Why a different topology than coding

Research is **breadth-first compression**: many independent sources, path-dependent next queries, no single test oracle. A coding agent has a tight feedback loop (edit -> test -> pass/fail). A research agent must discover what it does not know, fan out to independent sources, and then compress into a coherent synthesis with citations. That is why a coding loop's linear ReAct does not work for research, and why a research DAG does not work for git.

#### Anthropic multi-agent architecture (Lead/Sub/Citation)

Anthropic (2025-06-13): LeadResearcher (Opus 4 then) writes a plan to **Memory** because **200k** context will truncate -> spawns Subagents (Sonnet 4 then) with objective, output format, tool list, stop boundary -> each subagent has an **isolated** window, **3+** tools in parallel -> condensed summaries back to lead -> optional another wave -> **CitationAgent** attributes claims to URLs.

**All numbers:**
- Multi-agent vs single Opus 4: **+90.2%** on their internal research eval
- BrowseComp: **token usage explains 80%** of variance; token + tool-call count + model = **95%**
- Agents **~4x** chat tokens; multi-agent **~15x** chat
- Parallel 3-5 subs x 3+ tools: wall-clock **-90%**

**Scale-effort rules:**
- Simple: **1** agent, **3-10** tool calls
- Comparison: **2-4** subs, **10-15** calls each
- Complex: **>10** subs with disjoint responsibilities

Early failure: lead spawned **50** subagents; vague "research the semiconductor shortage" -> three subs duplicated 2025 supply chain, one wandered into 2021 auto chips. Tool-description rewrite agent: **-40%** future task time. Synchronous subagent waves: lead **cannot** steer mid-flight; they flag async as future work. Rainbow deploys so in-flight research is not killed. Subagent artifacts on a **filesystem** to avoid telephone-game through the lead.

#### Citation as a separate pass

Single-agent "cite as you write" loses the source across summarization hops. CitationAgent reads the **final report + source documents**. OpenAI's approach uses inline citations + source metadata in their deep research products.

#### OpenAI Deep Research

OpenAI Deep Research (ChatGPT 2025-02-02; API `o3-deep-research` / `o4-mini-deep-research` Jun 2025): inline citations + source metadata. GAIA results:

- pass@1 **67.36** avg (L1 **74.29**, L2 **69.06**, L3 **47.6**) vs then-SOTA **63.64**
- cons@64 **72.57**
- Humanity's Last Exam **26.6%** vs o1 **9.1%**

API: Responses API, **must** include web search and/or remote MCP **search+fetch** and/or file search; code interpreter optional; **other function tools unsupported**. MCP for deep research is a **specialized search/fetch server**, not a general tool host. Recommend `background: true` + webhooks (timeouts). ChatGPT: 5-30 min; Plus/Team/Enterprise **25**/month full, Pro **250**, Free **5**, then o4-mini lightweight (2025-04-24). Feb 2026: restrict web search to trusted sites; MCP/app connectors.

OpenAI's deep-research system card identifies prompt injection, privacy, code execution, and hallucination among relevant risks, supporting layered controls rather than citation formatting alone.

#### Gemini Deep Research

Gemini Deep Research (Gemini Advanced, 2024-12-11; API Interactions, preview `deep-research-preview-04-2026` / `deep-research-max-preview-04-2026`): `background=True` required; **max 60 min**, most **<20 min**; `store=True` with background; remote MCP yes, custom function tools **no**; Google Search on by default.

Google's own preview estimates (subject to change): typical ~**80** searches, ~**250k** in (50-70% cached), ~**60k** out -> **~$1-$3**/task; Max ~**160** searches, ~**900k** in, ~**80k** out -> **~$3-$7**/task.

#### GAIA benchmark

GAIA (Mialon et al., ICLR 2024, arXiv:2311.12983): **466** questions (165 val + 300 hidden-answer test); humans **92%**, GPT-4+plugins **15%**. Magentic-One **38%** (2024). Deep Research **67.36** pass@1 (2025). Judge rubrics (Anthropic): factual accuracy, citation accuracy, completeness, source quality, tool efficiency; they found **one** LLM-judge call (0-1 + pass/fail) more consistent than a panel. Human testers caught SEO-farm bias that evals missed.

**BrowseComp** contains 1,266 difficult information-seeking questions designed to require persistent browsing; the launch report's historical Deep Research score was **51.5%**, while GPT-4o with browsing scored **1.9%**. OpenAI explicitly noted that Deep Research had been trained on tasks similar to BrowseComp, so the figure is not a clean unseen-generalization estimate.

**Deep Research Bench** defines 89 tasks over a frozen RetroSearch corpus and includes trajectory checks for hallucination, tool use, and forgetting. **DeepResearch Bench** (separate) proposes 100 PhD-level tasks across 22 fields; its LLM-judge results require human calibration.

#### Stopping rules

Research cannot rely on "no more search ideas." A practical stop policy uses:
- Coverage of required facets
- Minimum independent evidence for material claims
- Unresolved contradiction severity
- Marginal yield from recent searches
- Deadline
- Cost cap

The output should distinguish facts, source claims, calculations, and recommendations. A frozen source bundle is required for reproducible evaluation, because live-web results and pages drift.

#### Evidence ledger pattern

Each claim should link to an evidence-ledger record containing:
- Source URL or document ID
- Publication and access dates
- Relevant passage location
- Source type
- Confidence
- Contradiction status

Search results are discovery material, not evidence; claims should cite opened primary sources. Calculations should run in a code tool with inputs and outputs retained. Persist the **citation graph + fetch timestamps**, not just the prose. Re-run is a new job (research is not idempotent -- the web moved).

### 3.5 Data Agents

#### Schema-only parsers die on production warehouses

A `SELECT * FROM information_schema.columns` dump is not a semantic model. Production warehouses have dirty values, external knowledge requirements, ambiguous column names, and joins that require domain expertise.

#### BIRD benchmark with numbers

**BIRD (Li et al., NeurIPS 2023, arXiv:2305.03111):** **12,751** NL-SQL pairs, **95** DBs, **33.4 GB**, **37** domains. Challenges: dirty values, external knowledge, **efficiency** (R-VES), not just schema matching. Paper: GPT-4 **54.89%** execution accuracy vs human **92.96%**. 2025 leaderboard (dev): Databricks RLVR 32B **75.68**; Snowflake Arctic-Text2SQL-R1-32B **72.20 / 73.84**.

BIRD's original GPT-4 **54.89%** EX vs **92.96%** human is the residual risk even after 2025 70%+ specialist models: the remaining errors are the ones that look like a dashboard.

#### Spider 2.0

**Spider 2.0:** **632** enterprise workflow problems; DBs often **>1,000** columns on BigQuery/Snowflake; queries can exceed **100** lines; o1-preview-era **10.1%** vs **86.6%** on Spider 1.0. Splits: Snow (547, Snowflake, no eval cost), Lite (547; BigQuery 214 / Snowflake 198 / SQLite 135), DBT (68, DuckDB). Historical code-agent/o1-preview result **21.3%**, versus reported **91.2%** Spider 1.0 and **73.0%** BIRD. Enterprise SQL workflows are harder than classic text-to-SQL.

#### Snowflake Cortex Analyst (semantic views, YAML trap)

Snowflake Cortex Analyst: semantic **views** (recommended, first-class schema objects, GRANT/RBAC/sharing) vs legacy YAML on a **stage**. Privileges: `SNOWFLAKE.CORTEX_USER` or `CORTEX_ANALYST_USER`; **SELECT** on referenced tables; READ/WRITE on stage for YAML; USAGE on Cortex Search services. YAML-on-stage trap: any role with stage access can read the model even without table SELECT -- Snowflake docs tell you to keep those in lockstep. Semantic views do not need legacy `join_type` / `relationship_type`. Custom instructions steer SQL generation.

#### Databricks Genie (dual credentials, trusted assets, Agent mode)

Databricks **Genie Agents** (formerly Genie Spaces; concepts page updated **2026-08-17**): Unity Catalog tables/views/metric views + **knowledge store** (agent-local descriptions, synonyms, joins, SQL expressions -- does **not** mutate UC metadata) + instructions + example SQL + **trusted assets** (parameterized queries/functions whose SQL is **author-verified**; answers tagged trusted). Generated SQL is **read-only**.

**Dual credentials**: warehouse compute uses the **author's embedded** warehouse identity (users need not have CAN USE on the warehouse); **data** access is the **end user's** UC identity -- row filters and column masks apply; unauthorized data -> **empty**, attributed in query history to the user.

**Inspect** (preview): extra SQL probes (filters, date windows, joins) then rewrite.

**Agent mode** (ex-Research Agent): plan -> multiple SQL -> iterate -> cited report; can read UC **volume** files; Americas/EU/AU/NZ/JP without cross-Geo; elsewhere needs cross-Geo. Chat mode: structured data only.

#### Notebooks as a third data-plane

Notebooks are a third data-plane: papermill / Databricks notebooks / Colab / Jupyter as **stateful kernels**. The kernel is a long-lived process with `df` in RAM, `!pip` to the internet, and often a Spark or warehouse session attached. Treat it like a browser profile: snapshot, idle TTL, no secrets in cells the model can `print`, no shared kernel across tenants. A data agent that "just opens a notebook" is a **coding agent with a warehouse credential** -- apply both the coding sandbox (Seatbelt/bwrap, registry allowlist) and RLS. Prefer parameterized notebooks (papermill parameters) over "the model types into cells until the chart looks right." Databricks Genie Agent mode can read UC **volume** files; that is unstructured RAG inside a SQL agent -- pin volumes, virus-scan, and do not mix them with write-capable notebooks.

#### Warehouse tools and timeout defense-in-depth

Statement timeouts live on the **warehouse**, not the LLM: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` (account/user/session/warehouse); BigQuery `jobTimeoutMs` / `maximumBytesBilled` (bytes cap is a **dry-run fuse** before a 33 GB BIRD-class scan); Databricks SQL warehouse limits and Genie budgets. BigQuery jobs are regional and billed by bytes processed; an agent that emits `SELECT *` without a partition filter is a **FinOps incident**, not an NLP incident. Concurrency is the warehouse's problem; the agent's problem is **retry storms** after timeout.

Dialect is part of the tool schema: Spider 2.0's split across BigQuery / Snowflake / SQLite is the production warning that one `run_sql` tool without `dialect=` and warehouse docs will loop on syntax errors until the job timeout.

BigQuery dry runs estimate bytes processed, although federated-source dry runs can return a lower-bound estimate of zero; `maximumBytesBilled` can reject a query above a cost threshold.

#### DAB, BLADE, ScienceAgentBench benchmarks

**Data Agent Benchmark (DAB)** includes 54 queries over 12 datasets, nine domains, and four database-management systems, with multi-database integration, irregular join identifiers, unstructured transformation, and domain knowledge. Best evaluated baseline: Gemini-3-Pro, **38%** pass@1.

**BLADE** targets open-ended, data-driven scientific analysis where multiple analysis decisions can be defensible, illustrating why exact-match grading alone is inadequate for data agents.

**ScienceAgentBench** contains 102 executable data-analysis tasks derived from 44 papers across four disciplines and evaluates generated Python; its historical best result was **32.4%** independently and **34.3%** with expert knowledge over three attempts.

#### Analysis contract pattern

The agent should produce an explicit **analysis contract** before executing: business definition, grain, filters, time zone, eligible population, missing-value rule, output columns, and expected checks. It then retrieves only relevant schemas and approved samples, compiles a query plan, dry-runs or explains it, enforces scan/cost/row/time limits, executes under a scoped identity, and validates cardinality, nulls, reconciliation totals, and statistical assumptions. This prevents the "syntactically valid but semantically wrong" failure mode that benchmarks still struggle to catch.

---

## 4. Key Patterns & Best Practices

### Build/buy/mix trade-off matrix

| Decision | Prefer A when | Prefer B when | Hard no |
| --- | --- | --- | --- |
| **ACI loop vs Agentless pipeline** | Novel bugs, need shell, multi-file refactors | Localized, well-specified tickets, cost cap | Uncapped ReAct on prod with write credentials |
| **Repo map vs agentic search** | Deterministic context, audit "what the model saw" (Aider dump) | Huge monorepos, unknown start files (Claude Code) | Dumping the repo into 1M context every turn |
| **Local sandbox vs cloud VM** | Secrets stay on laptop; HITL; Cursor/Claude/Codex CLI | Parallel tasks; no local toolchain; Copilot/Codex cloud | Cloud VM **with** prod `.env` and open egress |
| **Pixels vs a11y browser** | Canvas, remote desktop, no DOM (CUA / computer use) | Internal apps with good a11y (Playwright MCP / browser_toolset) | Pixel agent on SSO-admin session without watch-mode |
| **Vendor-hosted browser (Operator/agent mode) vs self-hosted** | Consumer tasks; you want their blocklist + monitor model | Regulated; allowlist you control; no third-party screenshot store | Either, with banking (CUA declines high-risk; still don't) |
| **Single research agent vs Anthropic-style multi-agent** | Narrow question, 3-10 tool calls | Breadth-first, many independent sources, $ justifies 15x tokens | Coding tasks forced into subagent DAG |
| **CitationAgent pass vs inline citations** | High-stakes briefs, legal/policy | Fast internal memos | Publishing without URL **and** quote-level check |
| **Semantic view + RLS vs raw schema prompting** | Enterprise BI (Cortex / Genie) | Throwaway SQL on a public SQLite | Shared service account "for the agent" |
| **Trusted assets vs free-form SQL** | "What is net revenue?" | Exploratory Agent mode | Free-form `COPY`/`INSERT` |
| **Sync subagents vs async** | Simpler consistency (Anthropic today) | Long-tail stragglers | Async without a ledger (Magentic-One Progress Ledger exists for a reason) |

### Architecture trade-off matrix

| Choice | Best fit | Benefit | Cost/risk | Decision rule |
|---|---|---|---|---|
| Fixed workflow | repeated known path | predictable, cheap, auditable | brittle on novel steps | default when branch set is enumerable |
| Single specialized loop | variable but cohesive task | simple ownership and trace | context growth, serial latency | use while one agent can hold state and one verifier can grade |
| Orchestrator-workers | independent file/source/data facets | parallel coverage and isolation | token/cost growth, merge conflict | use only after decomposition and synthesis evals show gain |
| Structured observation | accessible DOM, schemas, AST/symbols | compact, deterministic references | misses visual/implicit state | prefer; add pixels/raw artifacts when verifier shows gaps |
| General shell/browser | broad adaptability | rapid capability coverage | very broad authority | sandbox and wrap with policy; replace common writes with typed tools |
| Deterministic grader | code/tests/state/invariants | cheap, reproducible | can be gamed or incomplete | primary where outcome is machine-checkable |
| Model grader | research quality/semantic fit | handles valid variation | bias, cost, nondeterminism | calibrate against experts; never sole high-impact gate |
| Human approval | ambiguous/consequential action | accountable judgment | latency and fatigue | reserve for high-risk boundary, show concise evidence |

### Context strategies: do not dump the repo into the prompt

Three context strategies exist (repo map, agentic search, pre-loaded container), but they all agree: the model should see **targeted excerpts**, not the whole codebase. Symbol-scoped retrieval, incremental tests, cached dependencies, and routing final review upward are the safe optimizations.

### Prompt cache as coding-agent NFR

SWE-agent and Cursor loops resend the ACI/tool schema and repo map every turn. Anthropic cache hits are **0.1x** input. Stabilizing tool JSON order (MCP 2026-07-28 guidance) is a **cost** control, not just a correctness control.

### Scale-effort rules for research

- Simple: **1** agent, **3-10** tool calls
- Comparison: **2-4** subagents, **10-15** calls each
- Complex: **>10** subagents with disjoint responsibilities
- Ban 50-subagent fan-out via prompt **and** a hard `max_subagents=8`

### Domain checkpoint and recovery semantics

| Domain | Authoritative checkpoint | Replay/recovery rule | Concurrency rule |
|---|---|---|---|
| Coding | base commit, worktree, patch/commit, test manifest and logs | recreate exact image/dependencies; replay from last green commit, not an uncommitted conversational summary | one writer per worktree; merge/rebase through normal source-control conflict handling |
| Browser | clean context template, encrypted session reference, URL, last verified business state, receipt/operation ID | reopen and re-observe; never assume a prior click failed because its response was lost | one writer per user/account workflow; isolate task contexts; serialize scarce or destructive resources |
| Research | query plan, source URL/hash/snapshot, extracted evidence, claim ledger, synthesis version | re-fetch only with explicit freshness policy; preserve the cited snapshot | parallel workers may append evidence; synthesis owns claim resolution; dedupe by canonical URL/content hash |
| Data | catalog/schema version, query text/hash, warehouse job ID, snapshot/time-travel reference, result artifact | look up existing job/result before resubmission; rerun only against declared snapshot or label changed data | read queries may parallelize under quotas; writes require transaction/lock and should usually leave autonomous scope |

Git worktrees provide multiple linked working trees backed by one repository, making one isolated worktree per coding run a practical concurrency primitive. They isolate checked-out files, not CPU, network, secrets, or malicious processes, so they are not a security sandbox.

For browser runs, Playwright contexts isolate cookies and local storage within a browser process. For data queries, persist the warehouse job ID and result artifact before asking a model to interpret results. A timeout can mean "completed but response lost," so status lookup precedes resubmission. Cap bytes, rows, wall time, and concurrency; a SQL `LIMIT` is not a reliable scan-cost control in columnar systems, while warehouse-native dry-run and maximum-byte controls are.

---

## 5. System Design Considerations

### 5.1 Token Economics

#### Published SKUs table

| Item | Rate (2026-08-21) | Source |
| --- | --- | --- |
| Claude Opus 5 | **$5 / $25** per MTok in/out; cache hit **$0.50**; 5m cache write **$6.25**; 1h **$10** | Anthropic pricing |
| Claude Sonnet 5 | **$2 / $10** (introductory made **permanent** 2026-08-10; $3/$15 hike cancelled) | Anthropic pricing + Sonnet 5 post |
| Claude Fable 5 | **$10 / $50** | Anthropic pricing |
| Claude Haiku 4.5 | **$1 / $5** | Anthropic pricing |
| Anthropic web search | **$10 / 1k searches** + search text as input tokens; errors not billed | Anthropic pricing |
| Anthropic web fetch | **$0** extra; page tokens only (~2.5k / 10 kB page; ~125k / 500 kB PDF) | Anthropic pricing |
| `computer_toolset_20260801` schema | **~4,500** input tokens/request (disable `zoom`: -~410) + screenshot image tokens | Anthropic pricing |
| `browser_toolset_20260801` schema | **~6,600** input tokens; +~880 if all 4 optional members enabled | Anthropic pricing |
| o3-deep-research | **$10 / $40** per MTok; cache **$2.50** | OpenAI model card |
| o4-mini-deep-research | **$2 / $8** (verify live) | OpenAI community |
| OpenAI web search | **$10 / 1k calls** + search content at **model** rates (reasoning/deep-research); preview non-reasoning historically **$25 / 1k** with free content tokens | OpenAI pricing |
| OpenAI containers (shell / code interpreter) | 1 GB **$0.03**, 4 GB **$0.12**, 16 GB **$0.48**, 64 GB **$1.92** per **20-min** session | OpenAI pricing |
| OpenAI file search | **$0.10**/GB-day (1 GB free); **$2.50 / 1k** tool calls | OpenAI pricing |
| Agentless Lite (2024 paper) | **$0.34-$0.70** per instance (GPT-4o era) | arXiv:2407.01489 |
| SWE-agent vs RAG (Lite, 2024) | **8-13x** token cost for **6.7x** resolve | arXiv:2405.15793 |
| Gemini Deep Research (preview estimate) | **~$1-$3** typical; **~$3-$7** Max | Gemini Deep Research |
| Cortex Analyst | Per **successful HTTP 200 message** (standalone API); **token** AI Credits if invoked via Cortex Agents; **plus warehouse** for executing SQL | Snowflake docs |

US-only inference / regional endpoints: **1.1x** on Claude (Sonnet 4.5+). Fast mode Opus 5: **2x**. Batch: **0.5x**. Claude 4.7+ tokenizer: **~30% more tokens** for the same text vs Sonnet 4.6-and-earlier -- agent loops on new tokenizers are silently more expensive.

#### $ per 1k tasks -- reference loops, all [inferred]

No vendor sells "1k coding tasks." These loops exist to **compare specialties**, not to quote a customer.

| Specialty | Stated reference loop | Arithmetic | **[inferred] $/1k** |
| --- | --- | --- | --- |
| **Coding, Agentless-class** | Paper cost **$0.70**/Lite instance | 1000 x 0.70 | **~$700** (2024 GPT-4o; stale SKU) |
| **Coding, SWE-agent-class Sonnet 5** | 40 turns x (30k in + 1.5k out); $2/$10 | 40 x ($0.060 + $0.015) = **$3.00**/task | **~$3,000** |
| **Coding, Opus 5 long refactor** | 80 turns x (50k in + 2k out); $5/$25 | 80 x ($0.25 + $0.05) = **$24**/task | **~$24,000** |
| **Research, Anthropic 15x chat** | Chat baseline 80k in + 4k out Opus 5 = $0.40+$0.10=$0.50; x15 | **$7.50**/task + 25 searches x $0.01 = $0.25 | **~$7,800** |
| **Research, o3-deep-research** | 200k in + 25k out + 20 web calls | $2.00 + $1.00 + $0.20 | **~$3,200** |
| **Research, Gemini typical** | Vendor estimate $1-$3 | midpoint $2 | **~$2,000** (preview) |
| **Browser, CUA-style** | 40 screenshot turns; ~4.5k toolset + 8k image + 800 out Sonnet 5 | ~40 x ($0.009+$0.016+$0.008) = **$1.3**/task **plus** VM | **~$1,300 + browser-pool capex** |
| **Data, Cortex Analyst + XS warehouse** | Message credits + seconds of warehouse | Dominated by **warehouse**, not tokens | **Do not** budget like research |

**Coding vs research, same week, same lab [inferred].** Anthropic: multi-agent research is **~15x** a chat; a coding ReAct loop is often **4x** a chat *plus* test execution wall-clock you do not pay in tokens. On Opus 5, a **hard research brief** and a **medium SWE-agent run** land in the **same few-thousand-dollars-per-1k** band; a **long Opus coding session** (80 turns, fat repo map) **outruns** a typical deep-research job. Warehouse data Q&A can be **cheaper than both in LLM $** and **more expensive in compute $** if the generated SQL scans a 33 GB BIRD-class fact table. Always split the bill: **tokens / search calls / sandbox-minutes / warehouse-seconds**.

#### Cost model formula

```text
model_cost = sum_calls((uncached_input_tokens * input_rate)
                     + (cache_write_tokens * cache_write_rate)
                     + (cache_read_tokens * cache_read_rate)
                     + (output_tokens * output_rate))

run_cost = model_cost
         + search_or_browser_fees
         + sandbox_vcpu_seconds * compute_rate
         + storage_and_egress
         + warehouse_bytes_scanned * scan_rate
         + human_review_minutes * loaded_labor_rate

cost_per_1k_successes = 1000 * sum(run_cost) / successful_runs
```

The denominator matters: cheaper attempts can be more expensive per successful outcome if retries or review rise. Track cost by task class and risk tier, not only aggregate tokens.

#### Workload-specific cost drivers

| Agent | Dominant input growth | External/runtime cost | Critical latency metric | Safe optimization |
|---|---|---|---|---|
| Coding | repository excerpts, command output, repeated diffs | sandbox CPU/RAM, builds, tests, CI | time-to-green; p95 command and full-task duration | symbol-scoped retrieval, incremental tests, cache dependencies, route final review upward |
| Browser | screenshots/accessibility trees, action history | browser workers, page/network waits, anti-bot friction | p95 action-to-observation; successful task duration | stable locators, event waits, reuse approved read-only sessions, parallelize independent tabs |
| Research | search results, long pages/PDFs, worker syntheses | search APIs, document parsing, parallel workers | time to sufficient evidence; citation-validation duration | query deduplication, source-content hash cache, parallel independent facets |
| Data | schemas, samples, query results, notebook output | warehouse scans, Python compute, BI rendering | time-to-first-valid-query; time-to-verified-report | catalog retrieval, dry-run, materialized aggregate reuse, smaller-model SQL lint |

Browser and desktop latency is often dominated by environment and deliberation rather than raw generation. Coding evaluation is also sensitive to infrastructure: Anthropic reported an internal Terminal-Bench 2.0 study where resource configuration moved scores by **six percentage points** with statistical significance, pod errors reached roughly **6%** in some settings, and approximately **three times** the baseline resources were needed before infrastructure stabilized.

#### Latency ranges

No vendor publishes production p50/p95/p99 for these loops. Use these as **design envelopes**, not SLOs:

| Specialty | Published / documented duration | What blows p99 |
| --- | --- | --- |
| OpenAI Deep Research (ChatGPT) | **5-30 min** | Extra search waves; PDF-heavy sources |
| Gemini Deep Research | Most **<20 min**, hard cap **60 min** | Max variant; MCP stalls |
| Anthropic Research | Sequential search was "hours"; parallelization **-90%** wall-clock | Sync wait on one stuck subagent |
| CUA / Operator | Tens to **100+** screenshot turns (Cambridge quiz trajectory is 150+ UI events) | Popups, novel UIs, CAPTCHA handoff |
| SWE-bench instance | Minutes-tens of minutes in Docker (harness + tests) | Flaky tests, install scripts, infinite edit loops |
| Warehouse SQL | Warehouse timeout (often **10 min-6 h** by platform default) | Cartesian joins from bad text-to-SQL |
| GitHub Actions coding agent | Actions job limits (hosted runners commonly **6 h** max) | Unbounded `@claude` retries |

OpenAI Deep Research API docs set client `timeout: 3600 * 1000` (1 h) even with background mode -- that is a **client** hint, not a p99.

#### Throughput NFRs

- Playwright MCP HTTP: **5 s** ping or the session dies.
- OpenAI containers: billed in **20-min** chunks (5-min minimum called out on the pricing page).
- Gemini background interactions: cannot chain a new interaction while `in_progress` (**400**).
- Cortex Analyst: only **HTTP 200** messages bill; failed generations are the cheap failure -- **executed** bad SQL is the expensive one.
- Cursor Auto-review classifier: Haiku 4.5 or GPT-5.4 Mini; if enterprise model policy blocks both, Auto-review **disables**.

### 5.2 Distributed Resilience

#### Job queues and long-running coding sessions

Coding agents are **stateful workflows**, not request/response. Do not restart from turn 0; checkpoint; tell the model the tool is failing and let it adapt; **rainbow-deploy** so a prompt/tool change does not kill in-flight sessions.

| Pattern | Who uses it | Resume key |
| --- | --- | --- |
| Cloud VM per task | Codex cloud, Cursor Cloud Agents, Copilot coding agent | Task / PR id; machine discarded after |
| Local worktree + session | Claude Code, Codex CLI, Cursor local, Aider | Chat/session id + git branch |
| GitHub Actions job | Claude Code Action, Copilot cloud | Workflow run id; **no** mid-job prompt deploy |
| Eval harness | SWE-bench Docker | Instance id; ephemeral |

Queue design: **one agent run per worktree**. Parallel Codex/Cursor cloud tasks are **N VMs**, not N threads on one repo. Local parallel agents on one working tree corrupt each other (Playwright persistent profile has the same bug).

Loop caps: OpenAI Agents SDK `max_turns` default **10**. Magentic-One `max_turns=20`, `max_stalls=3` then replan. SWE-agent yaml: max iterations. Without a cap you get **runaway git loops**.

#### Browser pools (5 invariants)

A browser is a **leased VM with cookies**. Pool invariants:

1. **One task, one context.** Persistent Playwright profiles serialize; `--isolated` or unique `--user-data-dir`.
2. **Storage-state is a credential.** Treat `storageState.json` like a refresh token: encrypt at rest, short TTL, no sharing across tenants.
3. **Heartbeat.** Playwright MCP HTTP **5 s**; raise `PLAYWRIGHT_MCP_PING_TIMEOUT_MS` behind a proxy or you flap healthy browsers.
4. **Remote vs local.** Operator/CUA-in-ChatGPT: browser on **vendor** servers (watch-mode, blocklist). Anthropic computer/browser use: **your** Docker/VM (you own allowlist and screenshot classifiers). Mixing them in one product means two audit planes.
5. **Step budget.** CUA OSWorld improves with more steps -- that is a **cost and stall** knob. Cap steps; on stall take a screenshot and hand to HITL rather than spinning.

#### Research jobs

OpenAI: `background: true` + webhooks; specialized MCP search/fetch only. Gemini: `background=True` + `store=True`; 60 min kill. Anthropic: Memory for the plan; filesystem artifacts for subagent dumps; **sync** subagent waves (lead blocked); tracing **without** reading conversation contents (privacy). Deployments: keep old prompt/tool versions alive until in-flight jobs drain (rainbow).

Idempotency: research is **not** idempotent (the web moved). Persist the **citation graph + fetch timestamps**, not just the prose. Re-run is a new job.

#### Warehouse query timeouts

Timeouts must be **defense in depth**:

1. Agent-level: max SQL statements per question (Genie Agent mode is multi-query by design -- cap it).
2. Session: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS`; BigQuery `jobTimeoutMs`.
3. Bytes: BigQuery `maximumBytesBilled` as a **dry-run fuse** before execution.
4. Warehouse: cluster concurrency + auto-suspend so a stuck agent does not hold slots overnight.
5. Notebook kernel: idle TTL; do not leave a Spark session attached to an LLM loop.

Retries: **do not** blindly retry a timed-out aggregation; the second try is another full scan. Surface `QUERY_CANCELED` to the model with "narrow the date window" instructions.

Genie dual-credential means the **author's** warehouse can be a noisy-neighbor victim of every business user. Size the warehouse for **interactive** Q&A, not ETL; send Agent-mode fan-out to a **separate** warehouse with a tighter timeout.

#### Durable run model

Persist an append-only run record containing immutable input, policy decision, model/tool versions, event sequence, external operation IDs, artifact hashes, budgets, approvals, and terminal status. Separate durable control state from disposable execution workers.

Every step should carry `run_id`, `step_id`, `attempt`, `tenant_id`, `deadline`, and idempotency key. Workers acquire time-bounded leases; the orchestrator renews, cancels, or requeues them. External writes require operation-specific idempotency or a preflight/read-after-write check. Use exponential backoff with jitter for transient reads, but do not automatically retry ambiguous writes such as "Submit order" after a lost response.

#### Degradation, circuit breaking, and cancellation

- Apply **deadline propagation**: each child receives less than the parent's remaining deadline, leaving time to checkpoint and synthesize.
- **Circuit-break by dependency and operation class**. Search-read failure can fall back to another approved source; repository writes, browser transactions, and data mutations should fail closed.
- Maintain **separate quotas** for model tokens, browser slots, sandbox CPU, search calls, and warehouse bytes. A single scalar "iteration limit" cannot protect all budgets.
- **Checkpoint before context compression.** Retain structured decisions, unresolved items, artifact references, and policy decisions; discard reproducible verbose tool output after hashing and storage.
- **Cancellation must propagate** to subprocesses, browser downloads, worker searches, and warehouse jobs. Mark any external operation whose completion is unknown as `reconciliation_required`, not `failed`.

### 5.3 Enterprise Security & Governance

#### Zero-Trust MCP as the tool bus

Specialized agents should not each grow a private plugin ecosystem. MCP is the **tool plane** (host/client/server; OAuth 2.1; Resource Indicators RFC 8707). Zero-Trust rules that matter here:

- **Per-specialty servers**, not one mega-server: `git`/`gh` for coding; Playwright for browser; warehouse SQL for data; search/fetch-only MCP for OpenAI Deep Research.
- **Tool RBAC in the host**, not in the prompt. Cursor/Claude/Copilot all have allow/deny lists. Copilot firewall **does not apply to MCP** (GitHub docs: only Bash-started processes in the Actions appliance). Claude Code sandbox **does not apply** to MCP. Those two sentences are the 2026 audit finding.
- **Network allowlists** at the OS/proxy (Cursor `sandbox.json`, Claude `allowedDomains`+`strictAllowlist`, Copilot org firewall, Playwright `--allowed-origins` as a *hint* only).
- **No secrets in the model context.** Claude Code can inject tokens via MITM **only** onto allowlisted hosts. Cursor blocks metadata IPs. Codex cloud: no internet. Genie: UC identity on the query, not a shared service principal for **data**.

Agent containment must assume three threat sources: malicious or mistaken users, model misbehavior, and hostile content or dependencies from the environment. Issue short-lived workload identities after policy evaluation. Bind permissions to tenant, task, resource set, operation, and expiry. A credential broker should inject secrets directly into the tool process; do not expose raw values in the model context, logs, screenshots, command output, or artifacts. Egress allowlists are capability grants: an allowed host can still be used for exfiltration, so pair domain controls with request schemas, destination accounts, content inspection, and byte limits.

#### PII, screenshots, and research corpora

Browser/computer-use: screenshots of email/PII are **training-set-shaped logs**. Operator: watch-mode on email; confirm before send; extra monitor model. Anthropic: classifiers on screenshots; still "won't be ideal without HITL." Policy: **do not** persist screenshots of production IdP; redaction in the executor before the pixels hit the vendor API if the contract requires it.

Research: Gemini docs -- malicious **files** and **web pages** as prompt injection; exfiltration if you let the agent browse **while** holding internal docs. OpenAI Deep Research Feb 2026: **restrict to trusted sites**. CitationAgent does not solve exfil. OpenAI's deep-research system card identifies prompt injection, privacy, code execution, and hallucination among relevant risks.

Data: Genie sends table/column metadata + sample values to the model. That is **schema PII** (customer names in sample values). Knowledge-store sample values must be synthetic in regulated tenants.

#### Sandbox + audit table (all 4 specialties)

| Control | Coding | Browser | Research | Data |
| --- | --- | --- | --- | --- |
| FS isolation | Seatbelt / Landlock / bwrap / Docker | Browser profile isolation; no host FS (except downloads dir) | Artifact bucket, not laptop | No FS; warehouse only |
| Egress | Domain allowlist + metadata block | Domain allowlist **and** redirect re-check | Search API + site allowlist | PrivateLink to warehouse; no web |
| Identity | Developer laptop vs CI app vs cloud VM OIDC | Low-priv site account; never admin SSO cookie | Connector OAuth per user | **End-user** UC / Snowflake role |
| Audit | PR + CI logs + sandbox env (`CURSOR_SANDBOX`) | Session recording / trace viewer | Citation URLs + fetch times | Query history attributed to user (Genie) |
| HITL | Auto-review / approvals | Watch-mode, purchase confirm | Plan approval (Gemini collaborative planning) | Trusted assets for high-stakes metrics |

Cursor Auto-review is **explicitly not a security boundary**. Copilot firewall: "sophisticated attacks may bypass"; does not cover setup scripts. Claude `failIfUnavailable: true` so missing bwrap cannot silently unsandbox CI.

#### Data-agent RLS (correct vs wrong patterns)

**Correct pattern (Databricks Genie):** compute identity != data identity. Row filters / column masks on **tables**, not in the prompt. Empty result for unauthorized rows.

**Correct pattern (Snowflake):** SELECT on tables + RBAC on semantic **views**. Do not put the only copy of the semantic model on a stage that is readable by roles without table SELECT. Snowflake row access policies can evaluate role/context to filter rows, and Access History can support object/column lineage and audit analysis.

**Wrong pattern:** service account that bypasses RLS "so the agent can see everything," then filter in the LLM. The model will leak rows in chain-of-thought and in cached prompts. PostgreSQL row-level security defaults to deny when enabled without an applicable policy, while owners and roles with `BYPASSRLS` normally bypass it; agent roles must not inherit those bypasses.

#### Identity cheat-sheet table

| Agent | Who the model thinks it is | Who the runtime actually is |
| --- | --- | --- |
| Cursor local | The developer | Seatbelt/Landlock child of the IDE; team-admin can still deny egress |
| Claude Code CI | `@claude` | GitHub App + API key; fork PRs have **no** secrets |
| Codex cloud | The task | Isolated container, **no internet** |
| Copilot cloud | The PR author / bot | Actions appliance + org firewall; MCP **unfiltered** |
| Operator / ChatGPT agent | The ChatGPT user | Vendor-hosted browser; watch-mode on email |
| Anthropic browser use | Your app's user | **Your** Playwright/CDP; you own allowlist + redirect checks |
| Genie | The business user | Author warehouse + **user** UC identity |
| Cortex Analyst | The Snowflake role on the token | That role's SELECT + semantic-view GRANTs |

#### SQL injection-like generation

The threat is not classic string-concat PHP; it is **LLM-authored SQL** that is syntactically valid and semantically over-broad (`SELECT *`, missing tenant predicate, `UNION` to `INFORMATION_SCHEMA`, `COPY INTO @evil_stage`). Mitigations: read-only warehouse role; **no** `ACCOUNTADMIN`; bind trusted assets for "revenue"; parser allowlist (SELECT/WITH/EXPLAIN only); `maximumBytesBilled`; block `COPY`/`PUT`/`CREATE`; never `EXECUTE IMMEDIATE` of model text in a write role.

Notebooks: kernel runs as the user; still wrap with warehouse RLS; do not `!pip` from the open internet inside the same kernel that has warehouse creds (that is a coding-agent exfil path).

#### Governance audit schema

At minimum, an immutable audit event should contain: timestamp, tenant/user/workload identity, run/step/attempt IDs, requested objective, model and prompt-policy version, tool and schema version, normalized arguments or protected hash, resource and origin, authorization decision and policy ID, approval actor, outcome/status, external operation ID, token/compute/scan usage, artifact/source hashes, and redaction classification. Reasoning text is not a reliable or necessary audit control; record observable inputs, tool calls, decisions, outputs, and environment outcomes.

Use a risk register and evaluation evidence across the lifecycle. NIST's AI Risk Management Framework is voluntary and its Generative AI Profile supplies a cross-sector companion for generative-AI risks; it does not prescribe a specialized-agent architecture or certify compliance.

---

## 6. Code Examples

### Cost model formula

```text
model_cost = sum_calls((uncached_input_tokens * input_rate)
                     + (cache_write_tokens * cache_write_rate)
                     + (cache_read_tokens * cache_read_rate)
                     + (output_tokens * output_rate))

run_cost = model_cost
         + search_or_browser_fees
         + sandbox_vcpu_seconds * compute_rate
         + storage_and_egress
         + warehouse_bytes_scanned * scan_rate
         + human_review_minutes * loaded_labor_rate

cost_per_1k_successes = 1000 * sum(run_cost) / successful_runs
```

Key insight: the denominator matters. Cheaper attempts can be more expensive per successful outcome if retries or review costs rise. Track cost by task class and risk tier, not only aggregate tokens.

### SLO metrics structure

Define separate SLOs for admission, each tool class, and end-to-end outcome:

**All agents:**
- Task success rate with confidence interval and repeated trials
- Unsafe-action rate
- Human intervention rate
- p50/p95/p99 duration
- Cost per success
- Tool-error rate and retry rate
- Deadline/cost-cap termination rate

**Coding-specific:**
- Accepted-without-major-rework rate
- Fail-to-pass and pass-to-pass test results
- Revert/incident rate
- Changed lines per accepted task
- Build minutes per success

**Browser-specific:**
- Outcome success rate
- Wrong-site/wrong-account action rate
- Confirmation-screen mismatch rate
- Actions per success
- Stale-element/recovery rate

**Research-specific:**
- Claim support rate
- Citation precision and coverage
- Authoritative-source share
- Contradiction resolution rate
- Freshness
- Reviewer correction rate

**Data-specific:**
- Executable-query success rate
- Semantic correctness rate
- Reconciliation pass rate
- Bytes scanned per success
- Policy-denial rate
- Reproducibility from snapshot and code

Route low-risk tasks to cheaper models only after per-stage evals. Route ambiguous specifications, security-sensitive diffs, cross-site browser writes, contradictory research, and high-impact analyses to stronger models or humans. Back-pressure should reject or defer work at admission rather than allowing thousands of open loops to hold browser, sandbox, or warehouse capacity.

### Coding agent tool loop

The generate-execute-validate loop is the core pattern for coding agents. The LLM writes code, a sandboxed subprocess runs it, and errors feed back into the next iteration.

```python
import subprocess
from openai import OpenAI

client = OpenAI()  # expects OPENAI_API_KEY in env

def coding_agent(task: str, max_iterations: int = 3) -> dict:
    """Minimal coding agent: generate -> execute -> validate loop."""
    system = (
        "You are a coding agent. Write Python code to solve the task. "
        "Return ONLY executable Python code, no markdown fences, no explanation."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]

    for attempt in range(max_iterations):
        # 1. Generate: LLM produces code
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0
        )
        code = response.choices[0].message.content.strip()
        print(f"--- Attempt {attempt + 1} ---\n{code}\n")

        # 2. Execute: run in a subprocess sandbox (timeout prevents hangs)
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True, text=True, timeout=10
        )

        # 3. Validate: check exit code
        if result.returncode == 0:
            return {"status": "success", "code": code, "output": result.stdout,
                    "attempts": attempt + 1}

        # 4. Feed error back for the next iteration
        messages.append({"role": "assistant", "content": code})
        messages.append({"role": "user", "content": (
            f"Execution failed with error:\n{result.stderr}\n"
            "Fix the code. Return ONLY the corrected Python code."
        )})

    return {"status": "max_iterations", "code": code, "error": result.stderr,
            "attempts": max_iterations}

# Example usage
if __name__ == "__main__":
    result = coding_agent("Write a function that checks if a number is prime, "
                          "then print whether 97 and 100 are prime.")
    print(f"\nResult: {result['status']} after {result['attempts']} attempt(s)")
    print(result.get("output", result.get("error", "")))
```

Key points: the subprocess is the sandbox (production agents use Docker or Seatbelt), the error-feedback loop is what makes this an agent rather than a single completion, and the `max_iterations` cap prevents runaway loops.

### Browser agent with Playwright

A browser agent navigates to a page, extracts its content via accessibility snapshot, and feeds that to an LLM for question answering. This mirrors the structured-observation channel described in Section 3.3.

```python
import asyncio
from playwright.async_api import async_playwright
from openai import OpenAI

client = OpenAI()  # expects OPENAI_API_KEY in env

async def browser_agent(url: str, question: str) -> dict:
    """Minimal browser agent: navigate -> extract -> answer."""
    async with async_playwright() as pw:
        # 1. Launch an isolated browser context (one task, one context)
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 2. Navigate and wait for content to load
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = await page.title()

        # 3. Extract page content via accessibility snapshot (structured channel)
        # This is cheaper and more robust than screenshots for text-heavy pages
        a11y_tree = await page.accessibility.snapshot()

        # 4. Also grab visible text as a fallback
        body_text = await page.inner_text("body")
        # Truncate to avoid blowing context window
        body_text = body_text[:8000] if len(body_text) > 8000 else body_text

        await browser.close()

    # 5. Feed extracted content to LLM for question answering
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": (
                "You are a browser agent. Answer the user's question based ONLY "
                "on the page content provided. If the answer is not in the content, "
                "say so. Cite the page title as your source."
            )},
            {"role": "user", "content": (
                f"Page title: {title}\nPage URL: {url}\n\n"
                f"Page content:\n{body_text}\n\n"
                f"Question: {question}"
            )},
        ],
        temperature=0,
    )
    answer = response.choices[0].message.content
    return {"answer": answer, "source_url": url, "page_title": title}

# Example usage
if __name__ == "__main__":
    result = asyncio.run(browser_agent(
        url="https://en.wikipedia.org/wiki/Python_(programming_language)",
        question="Who created Python and in what year?"
    ))
    print(f"Source: {result['page_title']} ({result['source_url']})")
    print(f"Answer: {result['answer']}")
```

Key points: the browser runs headless in an isolated context (no cookie leakage between tasks), content extraction uses the accessibility tree or visible text (cheaper than screenshots for text pages), and the LLM never controls the browser directly -- the harness navigates and extracts, then the LLM answers. A production agent would add a navigation loop, domain allowlists, and step budgets.

### Research agent with search tool

A research agent takes a question, searches multiple sources, synthesizes findings, and returns an answer with citations. This implements the search-extract-synthesize pattern from Section 3.4.

```python
import json
from openai import OpenAI

client = OpenAI()  # expects OPENAI_API_KEY in env

def web_search(query: str) -> list[dict]:
    """Stub for a real search API (SerpAPI, Tavily, Brave, etc.).
    Replace this with an actual API call in production."""
    # In production: requests.get("https://api.tavily.com/search", ...)
    return [
        {"title": f"Result for: {query}", "url": f"https://example.com/{i}",
         "snippet": f"Simulated search result {i} for '{query}'."}
        for i in range(1, 4)
    ]

def research_agent(question: str, max_searches: int = 3) -> dict:
    """Minimal research agent: plan -> search -> synthesize -> cite."""
    tools = [{"type": "function", "function": {
        "name": "web_search", "description": "Search the web for information.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query"}
        }, "required": ["query"]}
    }}]
    messages = [
        {"role": "system", "content": (
            "You are a research agent. To answer the user's question:\n"
            "1. Call web_search with targeted queries (max 3 searches).\n"
            "2. After gathering evidence, write a synthesis with inline citations.\n"
            "Format citations as [Source Title](URL)."
        )},
        {"role": "user", "content": question},
    ]

    all_sources = []
    for _ in range(max_searches + 1):  # +1 for final synthesis turn
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, tools=tools, temperature=0
        )
        msg = response.choices[0].message
        messages.append(msg)

        # If the model is done searching and returns a text answer
        if not msg.tool_calls:
            return {"answer": msg.content, "sources": all_sources,
                    "searches_used": len(all_sources)}

        # Execute each search tool call
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            results = web_search(args["query"])
            all_sources.extend(results)
            # Feed search results back to the model
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(results)})

    return {"answer": messages[-1].content if hasattr(messages[-1], 'content')
            else "Max searches reached.", "sources": all_sources,
            "searches_used": len(all_sources)}

# Example usage
if __name__ == "__main__":
    result = research_agent("What are the key differences between coding agents "
                            "and browser agents in production AI systems?")
    print(f"Answer ({result['searches_used']} searches used):\n{result['answer']}")
    print(f"\nSources cited: {len(result['sources'])}")
    for s in result["sources"]:
        print(f"  - {s['title']}: {s['url']}")
```

Key points: the agent controls the search loop (not a single completion), tool calls are schema-validated via the function-calling API, and sources are accumulated for citation. In production, replace `web_search` with a real API (Tavily, SerpAPI, Brave), add a CitationAgent pass to verify claims against sources, and enforce stopping rules (coverage, cost cap, deadline) per Section 3.4.

---

## 7. Common Pitfalls & Failure Modes

### Coding failures

- **Runaway git loops**: agent `git commit`s a failing patch, `git reset`, recommit; or force-pushes the same branch; or edits `.git/hooks` to persist. Cursor write-protects `.git/hooks` and `.git/config` -- other runtimes must too. Codex `untrusted` approval flags destructive git / config-override flags. A loop cap that only counts **model turns** still allows unbounded `git` inside one turn if the bash tool is a shell; cap **wall-clock and git mutations** (e.g. max 20 commits per task).
- **Test oracle gaming**: delete the failing test; SWE-bench hidden tests exist because of this. Patch overfits visible tests; hidden/regression/security checks fail. Use independent hidden tests, mutation/property tests, pass-to-pass suite, and review.
- **Dependency confusion**: via unsandboxed `npm install` (allowlist registries). Treat package metadata and dependency installation scripts as untrusted.
- **Secret exfil**: via `curl` to a new domain (strictAllowlist / Copilot PR warning). Masked brokered secrets, no secrets for untrusted PR jobs, workflow approval.
- **UID 0 surprises**: on Linux Cursor (chmod 777 as "root" in the namespace).
- **Hook persistence**: across rainbow deploys (Anthropic: stateful agents + new code = trajectory fork).
- **Worktree collision**: when two cloud agents push the same branch name.
- **PR-loop poison**: `@claude` on a public fork PR that includes a `prompt injection` in the issue body -- treat issue/PR text as **untrusted**, same as a webpage.
- **Destructive shell/dependency**: unexpected file/network/process change. Mitigate with ephemeral sandbox, syscall/egress/resource policy, immutable base, discard run.
- **Concurrent patch conflict**: overlapping files/base commits. One worktree per run, ownership/lease, rebase and rerun full verification.
- **CI secret exposure**: secret-like output, outbound call, modified workflow. Masked brokered secrets, no secrets for untrusted PR jobs.

### Browser failures

- **Hijack/injection**: page instructions override the user. Untrusted-content boundary, information-flow policy, deny side effects from page content. AgentDojo includes 97 tasks and 629 security test cases for prompt injection through tool data. AgentDyn expands to 60 open-ended tasks and 560 injection cases.
- **Playwright RCE**: `javascript_exec` / `browser_run_code_unsafe` = attacker-controlled JS with cookie privilege. `file_upload` + download dir reuse = exfil of anything the browser saved.
- **Session fixation**: via persistent profiles shared across tenants. Use isolated contexts per task/tenant.
- **CAPTCHA / login**: CUA is trained to **hand back** -- if your harness auto-fills passwords, you void that control. Stop and hand off; do not evade site controls.
- **Drive-by**: agent follows a "verify your account" link off-allowlist -- **redirect re-check** is mandatory; `--allowed-origins` is not enough.
- **Wrong account/origin**: account/origin differs from plan. Origin/account assertion before write; isolated sessions; approval.
- **Stale element/layout drift**: locator invalid, screenshot/DOM disagreement. Re-observe, semantic locator fallback, bounded replanning; never blind-click.
- **Duplicate transaction**: timeout after submit; second confirmation attempt. Idempotency key/receipt lookup; mark ambiguous and reconcile.
- **Cross-origin exfiltration**: browser same-origin policy does not constrain an agent that can read one origin and navigate to another. Enforce agent-level information-flow policy across origins.

### Research failures

- **Hallucination with citations**: failure is **not** "no URL." It is **URL exists, claim does not**. SEO farms beat PDFs (Anthropic human eval). CitationAgent that only sees summaries cannot catch a fabricated quote.
- **Duplicate subagents**: waste 15x tokens and still miss the board-member list. Ban 50-subagent fan-out via prompt **and** a hard `max_subagents=8`.
- **Stuck subagent**: lead cannot interrupt a stuck subagent (sync architecture). Apply deadline propagation.
- **Source laundering**: many reports trace to one origin. Provenance graph, canonical-source retrieval, independent-source count.
- **Search tunnel vision**: repeated query vocabulary/source domain. Query diversification, contradiction search, coverage matrix, stop rule.
- **Stale/live-web drift**: source hash/date changed. Snapshot cited content and access date; refresh under explicit freshness SLA.
- **Judge bias**: model grader stable but expert disagrees. Blind human calibration, multiple graders, code-based citation checks.
- **Fabricated quotes**: CitationAgent that only sees summaries cannot verify quote accuracy. Need quote-location records.
- **GAIA cons@64 72.57 vs pass@1 67.36**: sampling helps, costs 64x. Unbounded spend.

### Data failures

- **Over-broad SQL**: RLS-correct but scans the lake (cost, not leak). `maximumBytesBilled` as dry-run fuse.
- **RLS bypass**: via author warehouse identity if someone implements Genie wrong (using author for **data**). Non-bypass identity required.
- **Semantic-model / table GRANT skew**: Snowflake stage YAML accessible to roles without table SELECT.
- **Trusted-asset mismatch**: model answers from a stale parameterized query while the metric definition moved.
- **Dialect drift**: Spider 2.0 BigQuery vs Snowflake vs SQLite -- a single "SQL tool" without dialect + warehouse docs will emit invalid jobs that retry until timeout.
- **Notebook state bleed**: prior cell defined `df` from tenant A; question from tenant B uses it.
- **Inspect/Agent mode amplification**: N verification queries x warehouse seconds. Set Genie budget so Agent-mode loop cannot become ETL.
- **Join explosion/double counting**: cardinality or totals spike. Pre/post-join cardinality checks; keys/grain assertions.
- **Syntactically valid, semantically wrong query**: row counts/totals/grain violate invariant. Analysis contract, semantic layer, reconciliation, SME review.
- **Policy bypass**: results include forbidden tenant/column. Warehouse RLS/masking, non-bypass identity, canary policy tests.
- **Temporal non-reproducibility**: rerun changes without code change. Snapshot/time-travel ID, schema/model version, query and artifact hash.
- **Statistical misuse**: leakage, invalid denominator, multiple-testing issue. Predeclared checks, statistical test library, SME review.

### Cross-cutting failures

- **Wrong or underspecified task**: repeated plan changes; low requirement coverage. Clarify before write actions; persist acceptance criteria; human checkpoint.
- **Context degradation**: repeated searches/actions; contradiction with earlier state; growing latency. Checkpoint structured state; retrieve artifacts on demand; reset context after verification.
- **Infinite loop**: repeated state/action hash; no verifier delta; budget burn. Max steps plus no-progress detector, tool-specific budgets, terminate/escalate.
- **Hallucinated tool parameters**: schema validation failure; nonexistent path/selector/table. Strict schemas, enum/resource lookup, reject-and-correct once, then escalate.
- **Cascading timeouts**: shrinking deadline; dependency p95 rise; orphan jobs. Deadline propagation, bulkheads, cancellation, circuit breakers, reconciliation.
- **False completion claim**: agent says done but environment grader fails. Grade outcome state independently from transcript.
- **Infrastructure-skewed eval**: pass rate changes with CPU/RAM/timeouts. Pin images/resources; record infra failures separately; repeat trials.
- **Benchmark contamination**: implausible benchmark-production gap; memorized patches. Private temporal holdouts, contamination analysis, new tasks; retire saturated benchmarks.

The highest-risk common failure is confusing a plausible trajectory with a correct outcome. Multi-turn errors compound, and graders themselves can be brittle or non-deterministic.

---

## 8. Interview Questions & Answers

### Q1: What makes a specialized agent specialized?

**A:** Not the model weights -- the runtime. A specialized agent is defined by three things: its **runtime** (where tool calls execute -- a sandboxed terminal, a browser pool, a warehouse session, a search API), its **oracle** (what "done" means -- tests pass, page state matches, citations are accurate, SQL returns correct rows), and its **identity** (who the runtime acts as -- a developer, a bot account, an end-user with RLS). You can use the same foundation model for all four specialties. What changes is the execution environment, not the system prompt. Magentic-One's ablations prove this: removing any one worker drops performance 21-39%, and those workers are runtimes (WebSurfer, FileSurfer, Coder, ComputerTerminal), not fine-tuned models.

### Q2: How would you design a PR factory for a 400-dev org?

**A:** Issues labeled `agent-eligible` go into a queue. Each dequeued issue provisions a Copilot cloud agent or Claude Code Action on a **dedicated** runner image. The sandbox firewall uses the recommended allowlist plus internal Artifactory but **no** cloud metadata endpoint. Tests run in the same job. Output is a PR, not a merge -- required reviewers and CODEOWNERS still apply. Do **not** let the agent merge.

Economics: if 1k tickets/month are Agentless-shaped (localize+patch), budget ~$700-$3k in LLM costs (Sonnet 5 / pipeline) plus Actions minutes. If they are 80-turn Opus refactors, ~$24k LLM before CI. Cap turns at 40; escalate to humans.

p99 is the Actions 6h timeout, not the model. Kill at 45 min with a "needs human" label.

Security: org-locked Copilot firewall; Claude `strictAllowlist` in **managed** settings; secrets withheld from fork PRs; never `--dangerously-skip-permissions` on `pull_request` from forks. The durable object is the PR + CI, not the chat transcript.

Capacity: provision build workers from observed CPU-minute distributions, not request rate alone. If arrival rate is `lambda` tasks/minute and mean occupied sandbox duration is `W`, Little's Law gives mean concurrency `L=lambda*W`; add measured p95 burst and retry headroom. Maintain separate pools by trust and resource class.

Go/no-go: ship draft-PR mode when private temporal evals meet task success, no-regression, unsafe-action, cost, and review-rework thresholds. Do not use current SWE-bench Verified rank as the sole gate.

### Q3: Compare pixels vs a11y for browser agents

**A:** Pixels (screenshots + pointer clicks) work on any GUI including canvas, remote desktops, and apps with no accessibility tree. The cost is high: every step is an image (~8k tokens), coordinate drift breaks actions, and visible prompt injections are visible to the model. Structured observation (a11y tree / DOM refs) is cheaper, refs survive reflow, and you do not need a vision model. But canvas and custom widgets are missing from the a11y tree, and `javascript_exec` on a structured channel is page-privileged RCE.

Anthropic's own product split reflects this: browser_toolset for page-scoped work (a11y + pixels), computer_toolset for a full desktop. The decision rule: use structured when the app has good a11y (internal tools); use pixels for canvas, remote desktop, or no-DOM situations. Never use a pixel agent on an SSO-admin session without watch-mode.

### Q4: Why is SWE-bench Verified no longer a frontier metric?

**A:** OpenAI argued in 2026 that Verified is contaminated and saturated for frontier reporting. Three problems: (1) training data contamination -- models may have seen the patches, (2) only 500 tasks, so variance is high at 90%+ scores, and (3) ~30% of the newer Pro split's tasks are estimated to be broken (ambiguous specs or flaky tests). The interview takeaway: always quote a **named split + named scaffold + date**. "96% SWE-bench Verified" on an aggregator page is not an SLO. SWE-bench Pro (731 tasks) was proposed as a replacement, but even it has quality issues. The eval conversation has moved to private temporal holdouts and contamination analysis.

### Q5: Design a self-serve BI data agent

**A:** Start with a **curated** 20-table semantic layer (Cortex Analyst semantic views or Genie knowledge store), not the raw EDW. Trusted assets for the 15 questions that hit the board pack -- "What is net revenue?" should resolve to an author-verified parameterized query, not free-form SQL. RLS on tables via UC/Snowflake roles, not in the prompt. Separate warehouse for Agent mode (multi-query fan-out) from interactive chat, with `STATEMENT_TIMEOUT_IN_SECONDS=60` for chat and 300 for Agent mode. Inspect on for finance. Set a Genie budget so an Agent-mode loop cannot become ETL.

The identity model: warehouse compute uses the author's embedded identity; data access uses the end-user's UC identity. Unauthorized data returns empty, attributed in query history to the user.

For analyst notebooks: coding-agent sandbox plus warehouse RLS; no internet in the kernel. Benchmarks (Genie) for regression -- they are eval-only, not extra context.

The analysis contract pattern is critical: before executing, the agent states business definition, grain, filters, time zone, eligible population, missing-value rule, output columns, and expected checks.

### Q6: How do you secure browser agent sessions?

**A:** Five controls. (1) One task, one BrowserContext -- no sharing cookies across tenants; treat `storageState.json` like a refresh token (encrypt at rest, short TTL). (2) Domain allowlist at the network layer **and** redirect re-check in every `navigate` -- `--allowed-origins` is not redirect-safe. (3) Separate read permission from write permission: navigation/read is different from form-fill/purchase/credential-change. Reconfirm target origin, account, amount, and recipients immediately before a consequential action. (4) Treat all page content as potentially adversarial -- page instructions must not override system policy, tool permissions, or output destination. AgentDojo (97 tasks, 629 security test cases) and AgentDyn (60 tasks, 560 injection cases) are the red-team harnesses. (5) Step budget with stall detection: on stall, take a screenshot and hand to HITL rather than spinning. Encrypt browser authentication state and make it task/tenant scoped.

### Q7: What is the identity model for data agents?

**A:** The critical insight is **dual credentials** (Databricks Genie model): compute identity != data identity. Warehouse compute uses the author's embedded warehouse identity (users need not have CAN USE on the warehouse), but data access uses the end-user's UC identity -- row filters and column masks apply. The wrong pattern is a service account that bypasses RLS "so the agent can see everything" then filters in the LLM -- the model leaks rows in chain-of-thought and cached prompts. For Snowflake: SELECT on tables + RBAC on semantic views; never `ACCOUNTADMIN`. PostgreSQL: agent roles must not inherit `BYPASSRLS`. The generated SQL threat is not classic SQL injection but LLM-authored SQL that is syntactically valid and semantically over-broad -- `SELECT *`, missing tenant predicate, `UNION` to `INFORMATION_SCHEMA`.

### Q8: Explain the Anthropic multi-agent research architecture

**A:** Three roles: LeadResearcher, Subagents, CitationAgent. The Lead writes a plan to Memory (because 200k context will truncate), then spawns Subagents with an objective, output format, tool list, and stop boundary. Each subagent has an isolated window and runs 3+ tools in parallel. Condensed summaries flow back to the Lead, which can spawn another wave. Finally, CitationAgent reads the final report plus source documents and attributes claims to URLs.

The numbers: +90.2% vs single-agent on their internal eval; 15x chat tokens; 4x single-agent tokens; wall-clock -90% from parallelization. BrowseComp: token usage explains 80% of variance; token + tool-call count + model = 95%. The key design lessons: (1) simple questions get 1 agent, complex get >10 subs with disjoint responsibilities, (2) cap at 8 subagents -- 50 caused duplication, (3) subagent artifacts go on the filesystem to avoid telephone-game through the Lead, (4) sync waves mean the Lead cannot steer mid-flight, (5) rainbow deploys prevent killing in-flight research.

### Q9: When should you use Agentless vs an agent loop for coding?

**A:** Agentless (localize -> repair -> optional reproduction-test rerank) is the right choice when: the bug is localized, the ticket is well-specified, you have a cost cap, and you want auditability. It scored 32% on SWE-bench Lite at $0.70/instance -- far cheaper than a 50-turn ReAct loop. Use a full agent loop when: the fix spans multiple files, requires shell interaction, needs iterative debugging, or the starting point is unknown. The interview frame: if your "coding agent" is really localize+patch+test, a pipeline with a test oracle is cheaper and more auditable. The anti-agent control exists to keep you honest about whether you actually need autonomy.

### Q10: How should you version evaluations across specialized agents?

**A:** Record everything: task set, date, model, scaffold, prompts, tools, environment image, resources, attempts, hints, and grader version. Do not compare live-web, frozen, original, "verified," hinted, and pass@k scores as if they were the same measurement. Anthropic's Terminal-Bench 2.0 showed that resource configuration alone moved scores by six percentage points -- infrastructure is part of the evaluated system. The practical rule: the unit of comparison is {model + scaffold + prompt + tools + environment image + resource limits + retry count + hints + grader + dataset version}, not just the model name.

### Q11: Why does MCP as a tool bus create a security gap?

**A:** MCP is the right architecture -- per-specialty servers (git/gh for coding, Playwright for browser, SQL for data, search/fetch for research). But the 2026 audit finding is two sentences: Copilot firewall does not apply to MCP (only Bash-started processes). Claude Code sandbox does not apply to MCP. So you have a tool bus with no toll booth. The fix: enforce tool RBAC in the host, not in the prompt; per-specialty MCP servers with their own auth; network allowlists at the OS/proxy level; and never put secrets in the model context.

### Q12: How do you handle warehouse cost control for data agents?

**A:** Five layers of defense in depth: (1) Agent-level: cap max SQL statements per question. (2) Session: `STATEMENT_TIMEOUT_IN_SECONDS` or `jobTimeoutMs`. (3) Bytes: BigQuery `maximumBytesBilled` as a dry-run fuse before execution. (4) Warehouse: cluster concurrency + auto-suspend so a stuck agent does not hold slots overnight. (5) Budget: Genie budgets or equivalent. Critical anti-pattern: do not blindly retry a timed-out aggregation -- the second try is another full scan. Surface QUERY_CANCELED to the model with "narrow the date window" instructions. An agent that emits `SELECT *` without a partition filter is a FinOps incident, not an NLP incident.

### Q13: Start with the verifier -- what does that mean for each specialty?

**A:** Coding has the strongest deterministic feedback surface -- hidden tests, CI, static analysis. You know if it worked. Browser agents can often inspect business state (order ID exists, ticket status changed) -- weaker than tests but still checkable. Data agents can execute and reconcile (cardinality checks, totals match, RLS returns empty for unauthorized rows) but still miss semantic intent ("revenue" means different things to different teams). Research agents depend most on provenance and calibrated judgment -- there is no `pytest` for "is this claim true?" The implication: invest most heavily in oracle design for the specialty with the weakest verifier (research), and do not assume strong verifiers (coding tests) are sufficient alone (flaky tests, missing coverage, test oracle gaming).

### Q14: What is the "analysis contract" pattern for data agents?

**A:** Before executing any SQL, the data agent produces an explicit contract: business definition of the metric, grain (what each row represents), filters, time zone, eligible population, missing-value rule, output columns, and expected validation checks. This prevents the dominant data-agent failure: syntactically valid, semantically wrong queries that pass execution but produce wrong numbers. The contract makes the agent's assumptions visible and checkable before warehouse costs are incurred. After execution, validate cardinality, nulls, reconciliation totals, and statistical assumptions against the contract.

### Q15: What are the key benchmarks for data agents beyond BIRD and Spider?

**A:** Three newer benchmarks fill gaps. **DAB** (Data Agent Benchmark): 54 queries over 12 datasets, nine domains, four DBMS -- tests multi-database integration, irregular join identifiers, unstructured transformation, and domain knowledge; best baseline is 38% pass@1. **BLADE**: open-ended scientific data analysis where multiple analysis decisions can be defensible -- shows why exact-match grading is inadequate. **ScienceAgentBench**: 102 executable data-analysis tasks from 44 papers across four disciplines; best result 32.4% independently, 34.3% with expert knowledge. These complement BIRD (schema+dirty-value challenge) and Spider 2.0 (enterprise scale challenge) by testing analysis reasoning and domain knowledge, not just SQL generation.

---

## 9. Key Numbers to Memorize

| Category | Number | Context |
| --- | --- | --- |
| **SWE-bench** | **2,294** tasks, **12** Python repos | Original benchmark (Jimenez et al., ICLR 2024) |
| **SWE-bench Verified** | **500** engineer-confirmed instances | Human-filtered subset; **196** <15-min, **45** >1-hour |
| **SWE-bench Lite** | **300** tasks | Lightweight evaluation split |
| **SWE-bench Pro** | **731** tasks | Proposed replacement; ~**30%** estimated broken |
| **SWE-agent** | **12.47%** resolved (original), **18%** Lite | +**64%** vs shell-only; **87.7%** HumanEvalFix |
| **SWE-agent vs RAG** | **8-13x** cost, **6.7x** resolve | Cost/accuracy trade-off on Lite |
| **Agentless** | **$0.70**/instance, **32%** Lite | Anti-agent control; earlier: $0.34, 27.33% |
| **SWE-bench Verified GPT-4o** | **33.2%** | vs 16% on original SWE-bench |
| **WebArena** | **812** tasks, GPT-4 **14.41%**, human **78.24%** | Functional web eval (Zhou et al., ICLR 2024) |
| **CUA OSWorld** | **38.1%** (prev SOTA 22.0%, human **72.4%**) | Full OS bench, 369 tasks |
| **CUA WebArena** | **58.1%** (prev SOTA 36.2%, human **78.2%**) | Browser tasks |
| **CUA WebVoyager** | **87%** | Short live-web tasks (15 sites) |
| **Original OSWorld** | **12.24%** best model, **72.36%** human | 369 tasks across Ubuntu/Windows/macOS |
| **OSWorld 2.0** | **108** additional long-horizon workflows | Streaming, dynamic content, cross-source |
| **Mind2Web** | **2,350** tasks, **137** sites | Cross-domain web tasks |
| **Mind2Web 2** | **130** real-time long-horizon tasks | 1,000+ hours to construct |
| **BrowseComp** | **1,266** questions; DR **51.5%**, GPT-4o **1.9%** | Training overlap disclosed |
| **GAIA** | **466** questions, human **92%**, GPT-4+plugins **15%** | Factual Q&A (Mialon et al., ICLR 2024) |
| **Deep Research GAIA** | pass@1 **67.36**, cons@64 **72.57** | L1 74.29, L2 69.06, L3 47.6 |
| **Humanity's Last Exam** | Deep Research **26.6%** vs o1 **9.1%** | Difficulty anchor |
| **Magentic-One** | **38%** GAIA, **32.8%** WebArena, **27.7%** AssistantBench | Generalist overlay; ablation: -31% without ledgers |
| **Anthropic multi-agent** | **+90.2%** vs single, **15x** tokens, **-90%** wall-clock | BrowseComp: tokens explain **80%** of variance |
| **BIRD** | **12,751** pairs, **95** DBs, GPT-4 **54.89%**, human **92.96%** | NL-SQL benchmark (Li et al., NeurIPS 2023) |
| **BIRD 2025 leaderboard** | Databricks RLVR 32B **75.68** | Arctic-Text2SQL-R1-32B **72.20/73.84** |
| **Spider 2.0** | **632** tasks, **10.1%** vs **86.6%** Spider 1.0 | Enterprise SQL; >1,000 columns, >100-line queries |
| **Spider 2.0 code-agent** | **21.3%** vs Spider 1.0 **91.2%** and BIRD **73.0%** | Shows enterprise SQL is much harder |
| **DAB** | **54** queries, **12** datasets, best **38%** pass@1 | Multi-database, domain knowledge |
| **ScienceAgentBench** | **102** tasks, best **32.4%** / **34.3%** with expert | Executable data-analysis from papers |
| **Gemini DR typical** | **~$1-$3**/task; Max **~$3-$7** | ~80 searches typical, ~160 Max |
| **Opus 5 pricing** | **$5/$25** MTok; cache hit **$0.50** | 5m write $6.25; 1h write $10 |
| **Sonnet 5 pricing** | **$2/$10** MTok | Permanent introductory rate |
| **Fable 5 pricing** | **$10/$50** MTok | Premium tier |
| **Haiku 4.5 pricing** | **$1/$5** MTok | Budget tier |
| **Web search** | **$10/1k** calls (Anthropic & OpenAI) | Plus content tokens at model rates |
| **computer_toolset schema** | **~4,500** input tokens/request | +screenshot image tokens |
| **browser_toolset schema** | **~6,600** input tokens | +~880 if all 4 optional members |
| **Playwright MCP heartbeat** | **5 s** | Session dies on miss |
| **OpenAI container billing** | **20-min** chunks | 1 GB $0.03 to 64 GB $1.92 |
| **Claude 4.7+ tokenizer** | **~30% more tokens** vs older | Agent loops silently more expensive |
| **Prompt cache hit** | **0.1x** input cost | Key coding-agent NFR |
| **Infrastructure noise** | **6 pp** score swing in Terminal-Bench 2.0 | ~6% pod errors; ~3x resources to stabilize |
| **Sandbox permission reduction** | **84%** fewer prompts (Claude Code) | Vendor telemetry, not independent result |
| **Approval fatigue** | **~93%** user approval rate (Claude Code) | Illustrates fatigue, not safety |
| **US-only inference** | **1.1x** on Claude (Sonnet 4.5+) | Regional pricing modifier |
| **Fast mode Opus 5** | **2x** | Speed-for-cost trade-off |
| **Batch mode** | **0.5x** | Cost discount for async |

---

## 10. Quick Reference

### One-page cheat sheet

**The invariant:** The model never owns the runtime. It emits tool calls; a specialty runtime executes them.

**Specialty = runtime + oracle + identity**
- Coding: sandbox + hidden tests + developer/CI identity
- Browser: browser pool + end-state assertion + low-priv account
- Research: search/fetch + citation accuracy rubric + user OAuth
- Data: warehouse session + execution accuracy + end-user RLS role

**Key architecture rules:**
1. Do not dump the repo into the prompt (use repo maps, agentic search, or pre-loaded containers)
2. One agent run per worktree; one task per BrowserContext; one session per warehouse identity
3. Cap loop iterations AND wall-clock AND git mutations AND warehouse bytes
4. PR + CI is the durable object, not the chat transcript
5. MCP is the tool bus, but Copilot firewall and Claude Code sandbox do NOT cover MCP
6. Prompt cache (0.1x) is the coding-agent cost control
7. Service account bypassing RLS is always wrong for data agents
8. Grade outcomes by environment state, not by the agent's self-report

**Build/buy decision shortcuts:**
- Localized bug + cost cap -> Agentless pipeline (~$700/1k)
- Multi-file refactor + shell needed -> Agent loop (~$3k-$24k/1k)
- Internal app + good a11y -> Playwright MCP (structured refs)
- No DOM + remote desktop -> CUA / pixel agent
- Narrow question -> 1 research agent (3-10 calls)
- Breadth-first + many sources -> Multi-agent (15x tokens, -90% wall-clock)
- Board-pack metric -> Trusted assets, not free-form SQL
- Analysis before execution -> Analysis contract pattern

**Critical benchmarks (always quote split + scaffold + date):**
- SWE-bench: 2,294/Verified 500/Lite 300/Pro 731 -- Verified is saturated
- WebArena: 812 tasks, human 78.24% -- functional eval, not action match
- GAIA: 466 questions, human 92%, Deep Research 67.36 pass@1
- BIRD: 12,751 pairs, human 92.96%, GPT-4 54.89%
- Spider 2.0: 632 tasks, 10.1% vs 86.6% Spider 1.0
- OSWorld: 369 tasks, CUA 38.1%, human 72.4%
- DAB: 54 queries, best 38% -- multi-database integration
- ScienceAgentBench: 102 tasks, best 32.4% -- executable analysis

**Cost bands (per 1k tasks, inferred):**
- Agentless coding: ~$700
- SWE-agent Sonnet 5: ~$3,000
- Opus 5 long refactor: ~$24,000
- Research (Anthropic multi-agent): ~$7,800
- Research (o3-deep-research): ~$3,200
- Browser (CUA-style): ~$1,300 + browser pool
- Data: dominated by warehouse, not tokens

**Security checklist:**
- Runtime isolation (Seatbelt/bwrap/Docker/BrowserContext)
- Egress allowlist at OS/proxy, not just in prompt
- No secrets in model context
- Tool RBAC in host, not in prompt
- Data identity = end-user, not service account
- Redirect re-check for browser agents
- `failIfUnavailable: true` for sandbox in CI
- Fork PRs get no secrets
- Cancellation propagates to subprocesses, browsers, warehouse jobs
- Audit: timestamp, identity, run/step IDs, tool calls, outcomes, artifact hashes

**Principal-architect close:**
1. Start with the verifier -- coding has the strongest, research the weakest
2. Treat the harness as part of the model (infra moves scores by 6pp)
3. Constrain authority at the environment, not the prompt
4. Design for ambiguity, not generic retries (lost purchase != lost read)
5. Version every evaluation (model + scaffold + prompts + tools + env + grader)
6. Optimize cost per accepted outcome, not cost per attempt
