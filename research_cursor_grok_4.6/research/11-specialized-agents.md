# Research: Specialized Agents
**Date researched**: 2026-08-21
**Sources consulted**: 62

Scope: four production specialties — **coding** (SWE-agent ACI, Cursor / Claude Code / Codex / Copilot cloud agent, Aider repo maps, test oracles, sandboxed terminals, PR loops), **browser** (Anthropic Computer Use + Browser Use, OpenAI CUA / Operator → ChatGPT agent, Playwright MCP, DOM/a11y vs screenshot, allowlists), **research** (OpenAI / Gemini / Claude Deep Research, CitationAgent, multi-source synthesis, Anthropic Jun 2025 multi-agent system), **data** (text-to-SQL on BIRD / Spider 2.0, notebooks, warehouse tools, schema grounding, RLS). Prices are vendor-published token/tool/credit rates as of 2026-08-21. Benchmark numbers are from named papers or official product posts. ⚠️ No unpublished production p50/p95/p99 SLOs are invented. `$ per 1k tasks` figures are **[inferred]** from published SKUs × a stated reference loop — not a vendor “per task” SKU.

Invariant across specialties: **the model never owns the runtime**. It emits tool calls (ACI commands, `computer_call` / `browser_toolset` members, search/fetch, SQL). A **specialty runtime** (sandbox, browser pool, warehouse session, research job) executes them, enforces policy, and returns observations. Collapsing that runtime into “the LLM has a terminal” is the dominant enterprise failure.

---

## 1. System Topology & Mechanics

### 1.1 Four specialties, four planes

| Specialty | Control plane | Tool / data plane | Persistence | Oracle (what “done” means) |
| --- | --- | --- | --- | --- |
| **Coding** | Loop budget, approval policy, PR state machine | Sandboxed bash + editor + tests + git | Worktree / cloud VM / checkpoint | Hidden tests (`FAIL_TO_PASS` + `PASS_TO_PASS`); CI green |
| **Browser** | Step cap, watch-mode, domain allowlist | Screenshot+pointer **or** a11y-tree+refs | Browser profile / storage-state / remote VM | Functional assertion on page/DB state (WebArena-style), not action-trace match |
| **Research** | Lead plan, subagent fan-out, citation pass | Web search, fetch, MCP search/fetch, files | Memory / files / background job id | Rubric (factuality, citation accuracy, completeness); GAIA string-match on a subset |
| **Data** | Semantic model version, warehouse timeout, row filter | Read-only SQL, notebooks, warehouse APIs | Warehouse session + query history | Execution accuracy + trusted-asset match; RLS-empty is success, not a bug |

Microsoft’s Magentic-One (Fourney et al., arXiv:2411.04468) is the explicit **generalist overlay**: an Orchestrator with a Task Ledger + Progress Ledger plus tool-shaped workers (WebSurfer, FileSurfer, Coder, ComputerTerminal). Ablations: removing full ledgers **−31%**; removing any one worker **−21%** (Coder/Executor) to **−39%** (FileSurfer). Published GPT-4o-era completion: **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench**. That topology is a reminder: “specialized agents” are usually **specialized *runtimes***, not specialized *weights*. Anthropic’s Jun 2025 research post is the complementary lesson: coding is a **poor** fit for their orchestrator-worker research pattern (few truly parallelizable subtasks; agents weak at real-time coordination). Do not copy-paste a research DAG onto a git loop.

### 1.2 Coding: ACI, repo maps, oracles, sandboxes, PR loops

**SWE-agent (Yang et al., NeurIPS 2024, arXiv:2405.15793).** The contribution is the **agent-computer interface**, not a new model. Raw shell is a hostile API for LMs (unbounded `cat`, no lint-on-edit, no editor cursor). SWE-agent ships a small command set for search / view / edit / test. On the original SWE-bench test set (**2,294** GitHub issues from **12** Python repos, Jimenez et al., ICLR 2024, arXiv:2310.06770): **12.47%** resolved (286/2,294) and **18.00%** on Lite (54/300); HumanEvalFix **87.7%** pass@1. Vs shell-only with the same GPT-4 Turbo: **+64% relative**. Vs RAG on Lite: **8–13× more costly**, **6.7×** resolved-rate. That cost/accuracy trade-off is still the coding-agent budget conversation.

**SWE-bench as the oracle, not the product.** Eval is Docker-containerized (Jun 2024 harness). Success = generated patch makes hidden tests pass. **SWE-bench Verified** (OpenAI + authors, 2024-08-13): **500** engineer-confirmed solvable instances. GPT-4o on the then-best scaffold: **33.2%** (vs **16%** on original SWE-bench); Agentless roughly doubled **16% → 32%**. Difficulty slices: **196** <15-minute, **45** >1-hour. Official 2025+ “model vs scaffold” split: the Verified leaderboard compares arbitrary systems; a **mini-SWE-agent + bash-only** track exists to compare LMs without ACI sugar. OpenAI (2026) later argued Verified is **contaminated / saturated** for frontier reporting and pointed at **SWE-bench Pro** (731-task public split; they also later estimated **~30%** of Pro tasks are broken). Interview takeaway: quote a **named split + named scaffold + date**; do not treat “96% SWE-bench Verified” aggregator pages as an SLO.

**Agentless (Xia et al., arXiv:2407.01489)** is the anti-agent control: localize → repair → (optional) reproduction-test rerank. No autonomous tool loop. Reported **32.00%** on Lite at **$0.70**/instance (later revision; earlier abstract **27.33% / $0.34**). Production meaning: if your “coding agent” is really localize+patch+test, a **pipeline** with a test oracle is cheaper and more auditable than a 50-turn ReAct loop.

**Repo maps vs agentic search.** Aider (Gauthier) builds a **token-budgeted** map: tree-sitter tags → file graph → personalized PageRank → `--map-tokens` default **1k**, expanded when no files are in chat. Files already in chat are **omitted** from the map. Claude Code’s public product surface is the opposite topology: **agentic search** over the working tree (no user file picker required) plus `CLAUDE.md` as standing instructions. Cursor indexes the repo for retrieval and then runs an **agent loop** with a sandboxed terminal. Codex (OpenAI, 2025-05 cloud preview + CLI) preloads the GitHub repo into a **per-task cloud container**; CLI uses local worktrees. Three context strategies, one job: **do not dump the repo into the prompt**.

**Sandboxed terminals (the actual coding data plane).**

| Runtime | Isolation | Network default | Write default | Approval overlay |
| --- | --- | --- | --- | --- |
| **Cursor** (v2.0+; network policy since 2.5, Feb 2026) | macOS Seatbelt; Linux Landlock+seccomp (kernel **6.2+**); UID 0 *inside* Linux user namespace | Deny, then `sandbox.json` ± Cursor package-manager defaults | Workspace RW; `.git/hooks`, `.git/config`, `.vscode`, `.cursor/*.json` write-blocked | Run Modes: Auto-review (default as of **3.6**, 2026-05-29), Allowlist, Run Everything. Cloud Agents: **no** Run Modes (dedicated VM) |
| **Claude Code** | macOS Seatbelt; Linux/WSL2 **bubblewrap** + `socat` proxy; WSL1 unsupported | **No pre-allowed domains**; prompt / classifier, or `strictAllowlist` (v**2.1.219+**, user/managed/CLI settings only — **repo** `.claude/settings.json` cannot set it) | Workspace via FS policy; `/sandbox` panel | Permission rules + Auto mode classifier. `failIfUnavailable` blocks start if bwrap missing |
| **Codex CLI** | macOS Seatbelt; Linux `bwrap`+seccomp; WSL2 Linux path; native Windows sandbox | `workspace-write` **network off** unless `[sandbox_workspace_write].network_access = true` | `read-only` / `workspace-write` / `danger-full-access` | `on-request` / `untrusted` / `never` / `auto_review` |
| **Copilot cloud agent** | GitHub Actions appliance | Firewall **on**; recommended allowlist **on** (pkg repos, registries, CAs, **Playwright browser download hosts**) | Repo clone + PR branch | Org can lock firewall / recommended list / whether repos may add custom rules (changelog 2026-04-03) |
| **SWE-bench / SWE-agent eval** | Docker per instance | Harness-controlled | Ephemeral container | N/A (batch eval) |

Cursor `sandbox.json`: `networkPolicy.default` **deny**; deny **beats** allow; RFC1918 + **169.254.169.254** + IPv6 ULA/link-local blocked (SSRF). Team-admin allowlist **replaces** (does not union) local allow lists. Merge order: per-user < per-repo < team-admin < hardcoded. Linux sandbox remaps UID to 0 — scripts must use `CURSOR_ORIG_UID`/`GID` for Docker `--user`.

Claude Code: sandbox applies to **Bash**, not Read/Write/WebFetch/WebSearch/MCP/hooks. `deniedDomains` wins over `allowedDomains` wildcards. `WebFetch(domain:…)` allow rules **widen** Bash egress. MITM proxy can inject tokens only onto allowlisted hosts (`injectHosts`). Nested Docker: `enableWeakerNestedSandbox` bind-mounts container `/proc`.

Codex cloud (2025-05 preview): internet **disabled** during the task; only the provided GitHub repo + setup-script deps. That is a different threat model than laptop CLI.

**Test oracles.** SWE-bench gold is **not** “the patch looks right.” It is execution of **FAIL_TO_PASS** (the failing tests that define the issue) without regressing **PASS_TO_PASS**. The harness applies the gold `test_patch` to expose those tests, then runs the agent’s `model_patch` in a frozen Docker snapshot of `base_commit`. That is why “it compiled on my laptop” is not an eval. Agentless adds **generated reproduction tests** as a cheap filter before submitting a candidate — a second, weaker oracle that is allowed to be noisy because the hidden tests still decide. Production PR loops should treat unit tests as a **necessary but leaky** oracle: flaky tests, snapshot tests, and missing coverage all create false greens. Bind merge to **CI on the agent’s branch**, not to the model’s self-report. Log the exact pytest node-ids the agent ran; “all tests passed” in the chat is not evidence.

**OpenHands / Devin-class products.** Same topology as SWE-agent (event loop + sandbox + editor + browser + PR), different packaging. Treat them as **cloud coding runtimes** in the Copilot/Codex-cloud column: per-task VM, firewall, PR as the saga log. Do not mix their unpublished marketing scores with SWE-bench Verified aggregator pages.

**PR loops.** Claude Code GitHub Action (`anthropics/claude-code-action`): `@claude` on issue/PR comments, or `prompt:` on any GitHub event; auth via `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; also Bedrock / Vertex / Foundry. Autonomous runs have **no HITL**, so tools not pre-allowed **stall**. Fork PRs on public repos: GitHub **withholds secrets**. Copilot cloud agent: opens a PR, firewall-blocked destinations are **commented on the PR**. Codex: task completes → commit in the sandbox → human opens GitHub PR. Cursor Cloud Agents: dedicated machine, no local approval prompts. The durable object is the **PR + CI**, not the chat transcript.

### 1.3 Browser: Computer Use, Playwright, Operator-style, DOM vs pixels

**Two observation channels.**

| Channel | Examples | Strength | Cost / fragility |
| --- | --- | --- | --- |
| **Pixels + pointer** | OpenAI CUA (Jan 2025); Anthropic `computer_toolset_20260801` (17 members: screenshot, click, type, zoom); OSWorld | Works on any GUI; no a11y required | Every step is an image. Coordinate drift. Hidden a11y text invisible; **visible** prompt-injection text is visible |
| **Structured (a11y / DOM refs)** | Playwright MCP (`browser_snapshot` → `ref=e5`); Anthropic `browser_toolset_20260801` (27 default members: `navigate`, `read_page`, `left_click`, `screenshot`; optional `javascript_exec`, `file_upload`, `read_console`, `read_network`) | Refs survive reflow; cheaper than full screenshots; no vision model required for MCP | Canvas/custom widgets missing from a11y; `javascript_exec` is **page-privileged RCE**; Playwright `--allowed-origins` **does not** constrain redirects (docs: not a security boundary) |

Anthropic’s own split (2026-08 toolsets): **browser use** for page-scoped work (a11y + pixels); **computer use** for a full desktop. Both are **client** toolsets: Anthropic defines the schema; **your executor** runs every call. Not available in Claude Managed Agents as of the 2026-08 docs. Computer-use models on the current toolset: Fable 5, Mythos 5, Opus 5, Sonnet 5, Opus 4.8; older Opus/Sonnet 4.x stay on `computer_20251124` + beta header. Batch actions: several members per turn, run **in order**. `read_page` returns an a11y tree with `[ref_2]` tags, default depth **15**, cap **50,000** characters. Prefer refs over coordinates when the tree exists. Executor must validate coordinates against the **viewport you actually captured**, not the model’s claimed screen size.

OpenAI CUA (Operator research preview, 2025-01-23; July 2025 folded into **ChatGPT agent mode**; `operator.chatgpt.com` sunset). Loop: screenshot → CoT → mouse/keyboard → confirm on sensitive actions. Official numbers: **OSWorld 38.1%** (prev SOTA 22.0%, human **72.4%**; original OSWorld paper: then-best model **12.24%** on **369** tasks); **WebArena 58.1%** (prev computer-use SOTA 36.2%, browsing SOTA 57.1%, human **78.2%**); **WebVoyager 87%** (the 2024 paper’s own agent was **59.1%** on 15 live sites — CUA’s 87% is a later SOTA on that bench, not the paper baseline). OSWorld (Xie et al.) is a **full OS** bench (Ubuntu/Windows/macOS), not a browser bench — that is why 38% vs WebVoyager 87% is not a contradiction: WebVoyager tasks are shorter live-web tasks (Amazon, GitHub, Maps). Test-time scaling: more allowed steps → higher OSWorld. Operator reliability is **prompt-sensitive** (tagvenue concert-hall: **8/10** with filter hints vs **3/10** without). Safety layers: refusals, site blocklist, realtime moderation, confirm-before-side-effect, watch-mode on email, extra monitor model, Preparedness evals showing **no incremental** biorisk/autonomy vs GPT-4o (CUA post). 2026 API: `gpt-5.4`+ trained for mixed harnesses — built-in `computer` tool, custom Playwright/Selenium/VNC/MCP, or **code-execution** that mixes visual + DOM. `detail: "original"` screenshots: GPT-5.6 does **not** resize; large frames blow input tokens. Recommended downsample: **1440×900** / **1600×900** with coordinate remap.

**WebArena (Zhou et al., ICLR 2024, arXiv:2307.13854):** self-hosted Docker sites (shopping, CMS, GitLab-like, Reddit-like) + maps/calculator/Wikipedia; **812** long-horizon tasks; functional eval, not action-sequence match. Paper GPT-4 agent **14.41%** vs human **78.24%**. VisualWebArena (Koh et al., ACL 2024) adds visually grounded tasks; text-only agents fail when the cue is in the screenshot. BrowserGym (ServiceNow, TMLR 2025) is the current **eval bus**: MiniWoB, WebArena, WebArenaVerified, VisualWebArena, WorkArena, AssistantBench, WebLINX, OpenApps, TimeWarp — Playwright underneath, parallelizable.

**Playwright MCP** (`@playwright/mcp`): MCP server, a11y snapshots, no vision required. Default **headed**, persistent profile per workspace hash; `--isolated` for ephemeral; `--extension` to attach to a real browser. HTTP transport `:8931`, **5 s** heartbeat (`PLAYWRIGHT_MCP_PING_TIMEOUT_MS`). `browser_run_code_unsafe` is **RCE-equivalent** — trusted clients only. Copilot cloud agent’s recommended firewall allowlist **includes Playwright browser-download hosts** — the coding agent and the browser agent share a pool. Concurrent clients on one persistent profile **conflict**; use `--isolated` or distinct `--user-data-dir`.

**Allowlists.** Anthropic computer-use docs: dedicated VM, no secrets in the environment, **domain allowlist**, HITL on purchases/ToS/cookies; classifiers on screenshots steer toward user confirmation on suspected injections (opt-out via support). Browser-use docs: enforce allowlist at **network layer and after redirects** in `navigate`; block loopback/link-local/private unless required; build reads from **rendered** a11y/text, not raw DOM (hidden-instruction defense); leave `javascript_exec` / `file_upload` off. Operator: site **blocklist** (gambling, adult, weapons), watch-mode on email, confirm-before-side-effect, extra monitor model that **pauses** on suspicious pixels. Cursor: Browser Protection can require approval for Browser tools separately from shell. Playwright `--allowed-origins`: trusted origins for requests; **not** redirect-safe.

### 1.4 Research: deep research products, citations, Anthropic multi-agent

**Why a different topology than coding.** Research is **breadth-first compression**: many independent sources, path-dependent next queries, no single test oracle. Anthropic (2025-06-13): LeadResearcher (Opus 4 then) writes a plan to **Memory** because **200k** context will truncate → spawns Subagents (Sonnet 4 then) with objective, output format, tool list, stop boundary → each subagent has an **isolated** window, **3+** tools in parallel → condensed summaries back to lead → optional another wave → **CitationAgent** attributes claims to URLs. Official: multi-agent vs single Opus 4 **+90.2%** on their internal research eval. BrowseComp: **token usage explains 80%** of variance; token + tool-call count + model = **95%**. Agents **~4×** chat tokens; multi-agent **~15×** chat. Parallel 3–5 subs × 3+ tools: wall-clock **−90%**. Early failure: lead spawned **50** subagents; vague “research the semiconductor shortage” → three subs duplicated 2025 supply chain, one wandered into 2021 auto chips. Scale-effort rules: simple **1** agent, **3–10** tool calls; comparison **2–4** subs, **10–15** calls each; complex **>10** subs with disjoint responsibilities. Tool-description rewrite agent: **−40%** future task time. Synchronous subagent waves: lead **cannot** steer mid-flight; they flag async as future work. Rainbow deploys so in-flight research is not killed. Subagent artifacts on a **filesystem** to avoid telephone-game through the lead.

**Citation as a separate pass.** Single-agent “cite as you write” loses the source across summarization hops. CitationAgent reads the **final report + source documents**. OpenAI Deep Research (ChatGPT 2025-02-02; API `o3-deep-research` / `o4-mini-deep-research` Jun 2025): inline citations + source metadata; GAIA pass@1 **67.36** avg (L1 **74.29**, L2 **69.06**, L3 **47.6**) vs then-SOTA **63.64**; cons@64 **72.57**; Humanity’s Last Exam **26.6%** vs o1 **9.1%**. API: Responses API, **must** include web search and/or remote MCP **search+fetch** and/or file search; code interpreter optional; **other function tools unsupported**. MCP for deep research is a **specialized search/fetch server**, not a general tool host. Recommend `background: true` + webhooks (timeouts). ChatGPT: 5–30 min; Plus/Team/Enterprise **25**/month full, Pro **250**, Free **5**, then o4-mini lightweight (2025-04-24). Feb 2026: restrict web search to trusted sites; MCP/app connectors.

**Gemini Deep Research** (Gemini Advanced, 2024-12-11; API Interactions, preview `deep-research-preview-04-2026` / `deep-research-max-preview-04-2026`): `background=True` required; **max 60 min**, most **<20 min**; `store=True` with background; remote MCP yes, custom function tools **no**; Google Search on by default. Google’s own preview estimates (⚠️ subject to change): typical ~**80** searches, ~**250k** in (50–70% cached), ~**60k** out → **~$1–$3**/task; Max ~**160** searches, ~**900k** in, ~**80k** out → **~$3–$7**/task.

**Eval.** GAIA (Mialon et al., ICLR 2024, arXiv:2311.12983): **466** questions (165 val + 300 hidden-answer test); humans **92%**, GPT-4+plugins **15%**. Magentic-One **38%** (2024). Deep Research **67.36** pass@1 (2025). Judge rubrics (Anthropic): factual accuracy, citation accuracy, completeness, source quality, tool efficiency; they found **one** LLM-judge call (0–1 + pass/fail) more consistent than a panel. Human testers caught SEO-farm bias that evals missed.

### 1.5 Data agents: text-to-SQL, notebooks, warehouses, schema, RLS

**Schema-only parsers die on production warehouses.** BIRD (Li et al., NeurIPS 2023, arXiv:2305.03111): **12,751** NL–SQL pairs, **95** DBs, **33.4 GB**, **37** domains. Challenges: dirty values, external knowledge, **efficiency** (R-VES), not just schema matching. Paper: GPT-4 **54.89%** execution accuracy vs human **92.96%**. 2025 leaderboard (dev): Databricks RLVR 32B **75.68**; Snowflake Arctic-Text2SQL-R1-32B **72.20 / 73.84**. Spider 2.0: **632** enterprise workflow problems; DBs often **>1,000** columns on BigQuery/Snowflake; queries can exceed **100** lines; o1-preview-era **10.1%** vs **86.6%** on Spider 1.0. Splits: Snow (547, Snowflake, no eval cost), Lite (547; BigQuery 214 / Snowflake 198 / SQLite 135), DBT (68, DuckDB).

**Grounding artifact = semantic model, not `information_schema` dump.** Snowflake Cortex Analyst: semantic **views** (recommended, first-class schema objects, GRANT/RBAC/sharing) vs legacy YAML on a **stage**. Privileges: `SNOWFLAKE.CORTEX_USER` or `CORTEX_ANALYST_USER`; **SELECT** on referenced tables; READ/WRITE on stage for YAML; USAGE on Cortex Search services. YAML-on-stage trap: any role with stage access can read the model even without table SELECT — Snowflake docs tell you to keep those in lockstep. Semantic views do not need legacy `join_type` / `relationship_type`. Custom instructions steer SQL generation.

Databricks **Genie Agents** (formerly Genie Spaces; concepts page updated **2026-08-17**): Unity Catalog tables/views/metric views + **knowledge store** (agent-local descriptions, synonyms, joins, SQL expressions — does **not** mutate UC metadata) + instructions + example SQL + **trusted assets** (parameterized queries/functions whose SQL is **author-verified**; answers tagged trusted). Generated SQL is **read-only**. **Dual credentials**: warehouse compute uses the **author’s embedded** warehouse identity (users need not have CAN USE on the warehouse); **data** access is the **end user’s** UC identity — row filters and column masks apply; unauthorized data → **empty**, attributed in query history to the user. **Inspect** (preview): extra SQL probes (filters, date windows, joins) then rewrite. **Agent mode** (ex-Research Agent): plan → multiple SQL → iterate → cited report; can read UC **volume** files; Americas/EU/AU/NZ/JP without cross-Geo; elsewhere needs cross-Geo. Chat mode: structured data only.

**Notebooks** are a third data-plane: papermill / Databricks notebooks / Colab / Jupyter as **stateful kernels**. The kernel is a long-lived process with `df` in RAM, `!pip` to the internet, and often a Spark or warehouse session attached. Treat it like a browser profile: snapshot, idle TTL, no secrets in cells the model can `print`, no shared kernel across tenants. A data agent that “just opens a notebook” is a **coding agent with a warehouse credential** — apply both the coding sandbox (Seatbelt/bwrap, registry allowlist) and RLS. Prefer parameterized notebooks (papermill parameters) over “the model types into cells until the chart looks right.” Databricks Genie Agent mode can read UC **volume** files; that is unstructured RAG inside a SQL agent — pin volumes, virus-scan, and do not mix them with write-capable notebooks.

**Warehouse tools.** Statement timeouts live on the **warehouse**, not the LLM: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` (account/user/session/warehouse); BigQuery `jobTimeoutMs` / `maximumBytesBilled` (bytes cap is a **dry-run fuse** before a 33 GB BIRD-class scan); Databricks SQL warehouse limits and Genie budgets. BigQuery jobs are regional and billed by bytes processed; an agent that emits `SELECT *` without a partition filter is a **FinOps incident**, not an NLP incident. Concurrency is the warehouse’s problem; the agent’s problem is **retry storms** after timeout (see §3, §5). Dialect is part of the tool schema: Spider 2.0’s split across BigQuery / Snowflake / SQLite is the production warning that one `run_sql` tool without `dialect=` and warehouse docs will loop on syntax errors until the job timeout.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published SKUs (not task prices)

| Item | Rate (2026-08-21) | Source |
| --- | --- | --- |
| Claude Opus 5 | **$5 / $25** per MTok in/out; cache hit **$0.50**; 5m cache write **$6.25**; 1h **$10** | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Claude Sonnet 5 | **$2 / $10** (introductory made **permanent** 2026-08-10; $3/$15 hike cancelled) | same + [Sonnet 5 post](https://www.anthropic.com/news/claude-sonnet-5) |
| Claude Fable 5 | **$10 / $50** | Anthropic pricing |
| Claude Haiku 4.5 | **$1 / $5** | Anthropic pricing |
| Anthropic web search | **$10 / 1k searches** + search text as input tokens; errors not billed | Anthropic pricing |
| Anthropic web fetch | **$0** extra; page tokens only (~2.5k / 10 kB page; ~125k / 500 kB PDF) | Anthropic pricing |
| `computer_toolset_20260801` schema | **~4,500** input tokens/request (disable `zoom`: −~410) + screenshot image tokens | Anthropic pricing |
| `browser_toolset_20260801` schema | **~6,600** input tokens; +~880 if all 4 optional members enabled | Anthropic pricing |
| o3-deep-research | **$10 / $40** per MTok; cache **$2.50** | [OpenAI model card](https://developers.openai.com/api/docs/models/o3-deep-research) |
| o4-mini-deep-research | **$2 / $8** (OpenAI community + third-party calculators; confirm on [pricing](https://developers.openai.com/api/docs/pricing) at purchase time) | ⚠️ verify live |
| OpenAI web search | **$10 / 1k calls** + search content at **model** rates (reasoning/deep-research); preview non-reasoning historically **$25 / 1k** with free content tokens | OpenAI pricing |
| OpenAI containers (shell / code interpreter) | 1 GB **$0.03**, 4 GB **$0.12**, 16 GB **$0.48**, 64 GB **$1.92** per **20-min** session | OpenAI pricing |
| OpenAI file search | **$0.10**/GB-day (1 GB free); **$2.50 / 1k** tool calls | OpenAI pricing |
| Agentless Lite (2024 paper) | **$0.34–$0.70** per instance (GPT-4o era) | arXiv:2407.01489 |
| SWE-agent vs RAG (Lite, 2024) | **8–13×** token cost for **6.7×** resolve | arXiv:2405.15793 |
| Gemini Deep Research (preview estimate) | **~$1–$3** typical; **~$3–$7** Max | [Gemini Deep Research](https://ai.google.dev/gemini-api/docs/deep-research) ⚠️ preview |
| Cortex Analyst | Per **successful HTTP 200 message** (standalone API) per [Service Consumption Table](https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf); **token** AI Credits if invoked via Cortex Agents; **plus warehouse** for executing SQL | Snowflake docs |

US-only inference / regional endpoints: **1.1×** on Claude (Sonnet 4.5+). Fast mode Opus 5: **2×**. Batch: **0.5×**. Claude 4.7+ tokenizer: **~30% more tokens** for the same text vs Sonnet 4.6-and-earlier — agent loops on new tokenizers are silently more expensive.

### 2.2 `$ per 1k tasks` — reference loops, all [inferred]

No vendor sells “1k coding tasks.” These loops exist to **compare specialties**, not to quote a customer.

| Specialty | Stated reference loop | Arithmetic | **[inferred] $/1k** |
| --- | --- | --- | --- |
| **Coding, Agentless-class** | Paper cost **$0.70**/Lite instance | 1000 × 0.70 | **~$700** (2024 GPT-4o; stale SKU) |
| **Coding, SWE-agent-class Sonnet 5** | 40 turns × (30k in + 1.5k out); $2/$10 | 40 × ($0.060 + $0.015) = **$3.00**/task | **~$3,000** |
| **Coding, Opus 5 long refactor** | 80 turns × (50k in + 2k out); $5/$25 | 80 × ($0.25 + $0.05) = **$24**/task | **~$24,000** |
| **Research, Anthropic 15× chat** | Chat baseline 80k in + 4k out Opus 5 = $0.40+$0.10=$0.50; ×15 | **$7.50**/task + 25 searches × $0.01 = $0.25 | **~$7,800** |
| **Research, o3-deep-research** | 200k in + 25k out + 20 web calls | $2.00 + $1.00 + $0.20 | **~$3,200** |
| **Research, Gemini typical** | Vendor estimate $1–$3 | midpoint $2 | **~$2,000** (⚠️ preview) |
| **Browser, CUA-style** | 40 screenshot turns; ~4.5k toolset + 8k image + 800 out Sonnet 5 | ~40 × ($0.009+$0.016+$0.008) ≈ **$1.3**/task **plus** VM | **~$1,300 + browser-pool capex** |
| **Data, Cortex Analyst + XS warehouse** | Message credits (table; not reproduced here) + seconds of warehouse | Dominated by **warehouse**, not tokens | **Do not** budget like research |

**Coding vs research, same week, same lab [inferred].** Anthropic: multi-agent research is **~15×** a chat; a coding ReAct loop is often **4×** a chat *plus* test execution wall-clock you do not pay in tokens. On Opus 5, a **hard research brief** and a **medium SWE-agent run** land in the **same few-thousand-dollars-per-1k** band; a **long Opus coding session** (80 turns, fat repo map) **outruns** a typical deep-research job. Warehouse data Q&A can be **cheaper than both in LLM $** and **more expensive in compute $** if the generated SQL scans a 33 GB BIRD-class fact table. Always split the bill: **tokens / search calls / sandbox-minutes / warehouse-seconds**.

Prompt cache is the coding-agent NFR: SWE-agent and Cursor loops resend the ACI/tool schema and repo map every turn. Anthropic cache hits are **0.1×** input. Stabilizing tool JSON order (MCP 2026-07-28 guidance) is a **cost** control, not just a correctness control.

### 2.3 Latency — published ranges, not percentiles

⚠️ **No vendor publishes production p50/p95/p99** for these loops. Use these as **design envelopes**, not SLOs:

| Specialty | Published / documented duration | What blows p99 |
| --- | --- | --- |
| OpenAI Deep Research (ChatGPT) | **5–30 min** | Extra search waves; PDF-heavy sources |
| Gemini Deep Research | Most **<20 min**, hard cap **60 min** | Max variant; MCP stalls |
| Anthropic Research | Sequential search was “hours”; parallelization **−90%** wall-clock | Sync wait on one stuck subagent |
| CUA / Operator | Tens to **100+** screenshot turns (Cambridge quiz trajectory is 150+ UI events) | Popups, novel UIs, CAPTCHA handoff |
| SWE-bench instance | Minutes–tens of minutes in Docker (harness + tests) | Flaky tests, install scripts, infinite edit loops |
| Warehouse SQL | Warehouse timeout (often **10 min–6 h** by platform default) | Cartesian joins from bad text-to-SQL |
| GitHub Actions coding agent | Actions job limits (hosted runners commonly **6 h** max) | Unbounded `@claude` retries |

OpenAI Deep Research API docs set client `timeout: 3600 * 1000` (1 h) even with background mode — that is a **client** hint, not a p99.

### 2.4 Throughput NFRs that *are* specified

- Playwright MCP HTTP: **5 s** ping or the session dies.
- OpenAI containers: billed in **20-min** chunks (5-min minimum called out on the pricing page).
- Gemini background interactions: cannot chain a new interaction while `in_progress` (**400**).
- Cortex Analyst: only **HTTP 200** messages bill; failed generations are the cheap failure — **executed** bad SQL is the expensive one.
- Cursor Auto-review classifier: Haiku 4.5 or GPT-5.4 Mini; if enterprise model policy blocks both, Auto-review **disables**.

---

## 3. Distributed Resilience & State

### 3.1 Job queues and long-running coding sessions

Coding agents are **stateful workflows**, not request/response. Anthropic’s production notes for Research apply 1:1 to coding: do not restart from turn 0; checkpoint; tell the model the tool is failing and let it adapt; **rainbow-deploy** so a prompt/tool change does not kill in-flight sessions.

| Pattern | Who uses it | Resume key |
| --- | --- | --- |
| Cloud VM per task | Codex cloud, Cursor Cloud Agents, Copilot coding agent | Task / PR id; machine discarded after |
| Local worktree + session | Claude Code, Codex CLI, Cursor local, Aider | Chat/session id + git branch |
| GitHub Actions job | Claude Code Action, Copilot cloud | Workflow run id; **no** mid-job prompt deploy |
| Eval harness | SWE-bench Docker | Instance id; ephemeral |

Queue design: **one agent run per worktree**. Parallel Codex/Cursor cloud tasks are **N VMs**, not N threads on one repo. Local parallel agents on one working tree corrupt each other (Playwright persistent profile has the same bug).

Loop caps: OpenAI Agents SDK `max_turns` default **10** (from sibling multi-agent work — still the right control). Magentic-One `max_turns=20`, `max_stalls=3` then replan. SWE-agent yaml: max iterations. Without a cap you get **runaway git loops** (§5).

PR loop as the durable saga: issue comment → enqueue → sandbox clone → patch → tests → push branch → `gh pr create` → CI → human merge. Compensating action is **close the PR / revert**, not “undo the chat.” Fork-PR secret withholding is a **poisoned-queue** defense.

### 3.2 Browser pools

A browser is a **leased VM with cookies**. Pool invariants:

1. **One task, one context.** Persistent Playwright profiles serialize; `--isolated` or unique `--user-data-dir`.
2. **Storage-state is a credential.** Treat `storageState.json` like a refresh token: encrypt at rest, short TTL, no sharing across tenants.
3. **Heartbeat.** Playwright MCP HTTP **5 s**; raise `PLAYWRIGHT_MCP_PING_TIMEOUT_MS` behind a proxy or you flap healthy browsers.
4. **Remote vs local.** Operator/CUA-in-ChatGPT: browser on **vendor** servers (watch-mode, blocklist). Anthropic computer/browser use: **your** Docker/VM (you own allowlist and screenshot classifiers). Mixing them in one product means two audit planes.
5. **Step budget.** CUA OSWorld improves with more steps — that is a **cost and stall** knob. Cap steps; on stall take a screenshot and hand to HITL rather than spinning.

### 3.3 Research jobs

OpenAI: `background: true` + webhooks; specialized MCP search/fetch only. Gemini: `background=True` + `store=True`; 60 min kill. Anthropic: Memory for the plan; filesystem artifacts for subagent dumps; **sync** subagent waves (lead blocked); tracing **without** reading conversation contents (privacy). Deployments: keep old prompt/tool versions alive until in-flight jobs drain (rainbow).

Idempotency: research is **not** idempotent (the web moved). Persist the **citation graph + fetch timestamps**, not just the prose. Re-run is a new job.

### 3.4 Warehouse query timeouts and notebooks

Timeouts must be **defense in depth**:

1. Agent-level: max SQL statements per question (Genie Agent mode is multi-query by design — cap it).
2. Session: Snowflake `STATEMENT_TIMEOUT_IN_SECONDS`; BigQuery `jobTimeoutMs`.
3. Bytes: BigQuery `maximumBytesBilled` as a **dry-run fuse** before execution.
4. Warehouse: cluster concurrency + auto-suspend so a stuck agent does not hold slots overnight.
5. Notebook kernel: idle TTL; do not leave a Spark session attached to an LLM loop.

Retries: **do not** blindly retry a timed-out aggregation; the second try is another full scan. Surface `QUERY_CANCELED` to the model with “narrow the date window” instructions (Anthropic’s “tell the agent the tool failed” pattern).

Genie dual-credential means the **author’s** warehouse can be a noisy-neighbor victim of every business user. Size the warehouse for **interactive** Q&A, not ETL; send Agent-mode fan-out to a **separate** warehouse with a tighter timeout.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP as the tool bus

Specialized agents should not each grow a private plugin ecosystem. MCP is the **tool plane** (host/client/server; OAuth 2.1; Resource Indicators RFC 8707 — see protocol research). Zero-Trust rules that matter here:

- **Per-specialty servers**, not one mega-server: `git`/`gh` for coding; Playwright for browser; warehouse SQL for data; search/fetch-only MCP for OpenAI Deep Research.
- **Tool RBAC in the host**, not in the prompt. Cursor/Claude/Copilot all have allow/deny lists. Copilot firewall **does not apply to MCP** (GitHub docs: only Bash-started processes in the Actions appliance). Claude Code sandbox **does not apply** to MCP. Those two sentences are the 2026 audit finding.
- **Network allowlists** at the OS/proxy (Cursor `sandbox.json`, Claude `allowedDomains`+`strictAllowlist`, Copilot org firewall, Playwright `--allowed-origins` as a *hint* only).
- **No secrets in the model context.** Claude Code can inject tokens via MITM **only** onto allowlisted hosts. Cursor blocks metadata IPs. Codex cloud: no internet. Genie: UC identity on the query, not a shared service principal for **data**.

### 4.2 PII, screenshots, and research corpora

Browser/computer-use: screenshots of email/PII are **training-set-shaped logs**. Operator: watch-mode on email; confirm before send; extra monitor model. Anthropic: classifiers on screenshots; still “won’t be ideal without HITL.” Policy: **do not** persist screenshots of production IdP; redaction in the executor before the pixels hit the vendor API if the contract requires it.

Research: Gemini docs — malicious **files** and **web pages** as prompt injection; exfiltration if you let the agent browse **while** holding internal docs. OpenAI Deep Research Feb 2026: **restrict to trusted sites**. CitationAgent does not solve exfil.

Data: Genie sends table/column metadata + sample values to the model (see Databricks AI assistive trust docs). That is **schema PII** (customer names in sample values). Knowledge-store sample values must be synthetic in regulated tenants.

### 4.3 Sandbox + audit

| Control | Coding | Browser | Research | Data |
| --- | --- | --- | --- | --- |
| FS isolation | Seatbelt / Landlock / bwrap / Docker | Browser profile isolation; no host FS (except downloads dir) | Artifact bucket, not laptop | No FS; warehouse only |
| Egress | Domain allowlist + metadata block | Domain allowlist **and** redirect re-check | Search API + site allowlist | PrivateLink to warehouse; no web |
| Identity | Developer laptop vs CI app vs cloud VM OIDC | Low-priv site account; never admin SSO cookie | Connector OAuth per user | **End-user** UC / Snowflake role |
| Audit | PR + CI logs + sandbox env (`CURSOR_SANDBOX`) | Session recording / trace viewer | Citation URLs + fetch times | Query history attributed to user (Genie) |
| HITL | Auto-review / approvals | Watch-mode, purchase confirm | Plan approval (Gemini collaborative planning) | Trusted assets for high-stakes metrics |

Cursor Auto-review is **explicitly not a security boundary**. Copilot firewall: “sophisticated attacks may bypass”; does not cover setup scripts. Claude `failIfUnavailable: true` so missing bwrap cannot silently unsandbox CI.

### 4.4 Data-agent RLS

**Correct pattern (Databricks Genie):** compute identity ≠ data identity. Row filters / column masks on **tables**, not in the prompt. Empty result for unauthorized rows.

**Correct pattern (Snowflake):** SELECT on tables + RBAC on semantic **views**. Do not put the only copy of the semantic model on a stage that is readable by roles without table SELECT.

**Wrong pattern:** service account that bypasses RLS “so the agent can see everything,” then filter in the LLM. The model will leak rows in chain-of-thought and in cached prompts.

**Identity cheat-sheet (interview table).**

| Agent | Who the model thinks it is | Who the runtime actually is |
| --- | --- | --- |
| Cursor local | The developer | Seatbelt/Landlock child of the IDE; team-admin can still deny egress |
| Claude Code CI | `@claude` | GitHub App + API key; fork PRs have **no** secrets |
| Codex cloud | The task | Isolated container, **no internet** |
| Copilot cloud | The PR author / bot | Actions appliance + org firewall; MCP **unfiltered** |
| Operator / ChatGPT agent | The ChatGPT user | Vendor-hosted browser; watch-mode on email |
| Anthropic browser use | Your app’s user | **Your** Playwright/CDP; you own allowlist + redirect checks |
| Genie | The business user | Author warehouse + **user** UC identity |
| Cortex Analyst | The Snowflake role on the token | That role’s SELECT + semantic-view GRANTs |

**SQL injection-like generation:** the threat is not classic string-concat PHP; it is **LLM-authored SQL** that is syntactically valid and semantically over-broad (`SELECT *`, missing tenant predicate, `UNION` to `INFORMATION_SCHEMA`, `COPY INTO @evil_stage`). Mitigations: read-only warehouse role; **no** `ACCOUNTADMIN`; bind trusted assets for “revenue”; parser allowlist (SELECT/WITH/EXPLAIN only); `maximumBytesBilled`; block `COPY`/`PUT`/`CREATE`; never `EXECUTE IMMEDIATE` of model text in a write role.

Notebooks: kernel runs as the user; still wrap with warehouse RLS; do not `!pip` from the open internet inside the same kernel that has warehouse creds (that is a coding-agent exfil path).

---

## 5. Production Failure Modes

### 5.1 Coding: runaway git loops

Symptoms: agent `git commit`s a failing patch, `git reset`, recommit; or force-pushes the same branch; or edits `.git/hooks` to persist. Cursor write-protects `.git/hooks` and `.git/config` — other runtimes must too. Codex `untrusted` approval flags destructive git / config-override flags. A loop cap that only counts **model turns** still allows unbounded `git` inside one turn if the bash tool is a shell; cap **wall-clock and git mutations** (e.g. max 20 commits per task).

Other coding failures: **test oracle gaming** (delete the failing test; SWE-bench hidden tests exist because of this); **dependency confusion** via unsandboxed `npm install` (allowlist registries); **secret exfil** via `curl` to a new domain (strictAllowlist / Copilot PR warning); **UID 0 surprises** on Linux Cursor (chmod 777 as “root” in the namespace); **hook persistence** across rainbow deploys (Anthropic: stateful agents + new code = trajectory fork); **worktree collision** when two cloud agents push the same branch name. PR-loop poison: `@claude` on a public fork PR that includes a `prompt injection` in the issue body — treat issue/PR text as **untrusted**, same as a webpage.

### 5.2 Browser: hijack and injection

Anthropic computer-use / browser-use: **page instructions override the user**. Operator: prompt injection, jailbreaks, phishing; mitigations = cautious navigation, monitor model, detection pipeline hours-scale. Playwright `javascript_exec` / `browser_run_code_unsafe` = attacker-controlled JS with cookie privilege. `file_upload` + download dir reuse = exfil of anything the browser saved. Hidden DOM text vs rendered-tree policy. Session fixation via persistent profiles shared across tenants. CAPTCHA / login: CUA is trained to **hand back** — if your harness auto-fills passwords, you void that control. Drive-by: agent follows a “verify your account” link off-allowlist — **redirect re-check** is mandatory; `--allowed-origins` is not enough.

VisualWebArena / WASP-style evals (dynamic injection in VisualWebArena-class sites) are the red-team harness; do not wait for a production ticket.

### 5.3 Research: hallucination with citations

Failure is **not** “no URL.” It is **URL exists, claim does not**. SEO farms beat PDFs (Anthropic human eval). Duplicate subagents waste 15× tokens and still miss the board-member list. Lead cannot interrupt a stuck subagent (sync architecture). CitationAgent that only sees summaries cannot catch a fabricated quote. OpenAI GAIA cons@64 **72.57** vs pass@1 **67.36** — sampling helps, costs 64×. Gemini: review `citations`; malicious uploaded PDFs. BrowseComp lesson: **spend tokens** or lose; also: unbounded spend.

### 5.4 Data: generated-query disasters

- **Over-broad SQL** that is RLS-correct but scans the lake (cost, not leak).
- **RLS bypass** via author warehouse identity if someone implements Genie wrong (using author for **data**).
- **Semantic-model / table GRANT skew** (Snowflake stage YAML).
- **Trusted-asset mismatch**: model answers from a stale parameterized query while the metric definition moved.
- **Dialect drift**: Spider 2.0 is BigQuery vs Snowflake vs SQLite — a single “SQL tool” without dialect + warehouse docs will emit invalid jobs that retry until timeout.
- **Notebook state bleed**: prior cell defined `df` from tenant A; question from tenant B uses it.
- **Inspect/Agent mode amplification**: N verification queries × warehouse seconds.

BIRD’s original GPT-4 **54.89%** EX vs **92.96%** human is the residual risk even after 2025 70%+ specialist models: the remaining errors are the ones that look like a dashboard.

---

## 6. Enterprise System Design Scenarios

### 6.1 Trade-off matrix (build / buy / mix)

| Decision | Prefer A when | Prefer B when | Hard no |
| --- | --- | --- | --- |
| **ACI loop vs Agentless pipeline** | Novel bugs, need shell, multi-file refactors | Localized, well-specified tickets, cost cap | Uncapped ReAct on prod with write credentials |
| **Repo map vs agentic search** | Deterministic context, audit “what the model saw” (Aider dump) | Huge monorepos, unknown start files (Claude Code) | Dumping the repo into 1M context every turn |
| **Local sandbox vs cloud VM** | Secrets stay on laptop; HITL; Cursor/Claude/Codex CLI | Parallel tasks; no local toolchain; Copilot/Codex cloud | Cloud VM **with** prod `.env` and open egress |
| **Pixels vs a11y browser** | Canvas, remote desktop, no DOM (CUA / computer use) | Internal apps with good a11y (Playwright MCP / browser_toolset) | Pixel agent on SSO-admin session without watch-mode |
| **Vendor-hosted browser (Operator/agent mode) vs self-hosted** | Consumer tasks; you want their blocklist + monitor model | Regulated; allowlist you control; no third-party screenshot store | Either, with banking (CUA declines high-risk; still don’t) |
| **Single research agent vs Anthropic-style multi-agent** | Narrow question, 3–10 tool calls | Breadth-first, many independent sources, $ justifies 15× tokens | Coding tasks forced into subagent DAG |
| **CitationAgent pass vs inline citations** | High-stakes briefs, legal/policy | Fast internal memos | Publishing without URL **and** quote-level check |
| **Semantic view + RLS vs raw schema prompting** | Enterprise BI (Cortex / Genie) | Throwaway SQL on a public SQLite | Shared service account “for the agent” |
| **Trusted assets vs free-form SQL** | “What is net revenue?” | Exploratory Agent mode | Free-form `COPY`/`INSERT` |
| **Sync subagents vs async** | Simpler consistency (Anthropic today) | Long-tail stragglers | Async without a ledger (Magentic-One Progress Ledger exists for a reason) |

### 6.2 Scenario: “PR factory” for a 400-dev org

**Topology.** Issues labeled `agent-eligible` → queue → Copilot cloud agent **or** Claude Code Action on a **dedicated** runner image → sandbox firewall = recommended allowlist + internal Artifactory + **no** cloud metadata → tests in the same job → PR → required reviewers + CODEOWNERS. Do **not** let the agent merge.

**Economics [inferred].** If 1k tickets/month are Agentless-shaped, budget **~$700–$3k** LLM (Sonnet 5 / pipeline) plus Actions minutes. If they are 80-turn Opus refactors, **~$24k** LLM before CI. Cap turns at 40; escalate to humans.

**NFR.** p99 is the Actions **6 h** timeout, not the model. Kill at 45 min with a “needs human” label.

**Security.** Org-locked Copilot firewall; Claude `strictAllowlist` in **managed** settings; secrets withheld from fork PRs; never `--dangerously-skip-permissions` on `pull_request` from forks.

### 6.3 Scenario: internal-app browser RPA vs public-web operator

Internal: Playwright MCP + a11y + `--isolated` + network allowlist to `*.corp.example` **and** redirect checks + disable `javascript_exec` + storage-state from a **bot account**. Eval on a WebArena-like **staging** clone (functional oracles).

Public: ChatGPT agent / CUA for long-tail consumer sites **without** SSO cookies; watch-mode; blocklist. Do not point CUA at the corporate IdP.

Hybrid failure: screenshot agent on an internal app **and** Playwright on the same cookie jar.

### 6.4 Scenario: competitive-intel research desk

Lead (Opus 5) + 3–5 Sonnet 5 subs + CitationAgent, **or** `o3-deep-research` with **trusted-site** restriction + internal file search. Job queue with 30 min SLO, 60 min kill (Gemini’s cap is a good default even on other vendors). Persist citations. Budget **[inferred] $2k–$8k / 1k briefs**. Ban 50-subagent fan-out via prompt **and** a hard `max_subagents=8`. Rainbow-deploy prompts. Do not use this topology to “also fix the code.”

### 6.5 Scenario: self-serve BI (data agent)

Genie or Cortex Analyst on a **curated** 20-table semantic layer, not the raw EDW. Trusted assets for the 15 questions that hit the board pack. UC/Snowflake RLS on tables. Separate warehouse for Agent mode. `STATEMENT_TIMEOUT_IN_SECONDS=60` for chat; 300 for Agent mode. Inspect on for finance. Benchmarks (Genie) for regression — they are **eval-only**, not extra context. Cost: message credits + warehouse; set a **Genie budget** (Databricks: Manage budgets for Genie) so an Agent-mode loop cannot become an ETL.

Notebook path for analysts: coding-agent sandbox **plus** warehouse RLS; no internet in the kernel.

### 6.6 Principal-architect interview close

1. **Specialty = runtime + oracle + identity**, not a system prompt.  
2. **Coding oracles are tests; research oracles are citations; data oracles are execution + RLS; browser oracles are end-state.**  
3. **Token SKUs understate research (search calls, 15× chats) and understate data (warehouse).**  
4. **MCP is the bus; sandboxes do not cover MCP.**  
5. **SWE-bench Verified saturation is not an SLO; WebArena 58% is not “browser solved”; BIRD 75% is not “safe for revenue.”**

---

## Sources

1. https://arxiv.org/abs/2405.15793 — SWE-agent (Yang et al., NeurIPS 2024)
2. https://arxiv.org/abs/2310.06770 — SWE-bench (Jimenez et al., ICLR 2024)
3. https://www.swebench.com/SWE-bench/ — SWE-bench overview / Docker harness
4. https://www.swebench.com/verified — SWE-bench Verified (500)
5. https://openai.com/index/introducing-swe-bench-verified/ — Verified launch; GPT-4o 33.2%
6. https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ — contamination; Pro recommendation
7. https://openai.com/index/separating-signal-from-noise-coding-evaluations/ — SWE-bench Pro 731; ~30% broken estimate
8. https://arxiv.org/abs/2407.01489 — Agentless (Xia et al.)
9. https://github.com/princeton-nlp/SWE-agent/ — SWE-agent / mini-SWE-agent notes
10. https://aider.chat/docs/repomap.html — Aider repo map, `--map-tokens` 1k
11. https://code.claude.com/docs/en/overview — Claude Code product
12. https://code.claude.com/docs/en/github-actions — Claude Code GitHub Action / PR loop
13. https://github.com/anthropics/claude-code-action — action implementation
14. https://code.claude.com/docs/en/sandboxing — Seatbelt/bwrap, `strictAllowlist`, MCP out of scope
15. https://www.anthropic.com/product/claude-code — Claude Code surfaces (terminal, GitHub, computer use)
16. https://cursor.com/docs/reference/sandbox — `sandbox.json` networkPolicy
17. https://cursor.com/docs/agent/security/run-modes — Auto-review, Seatbelt/Landlock, default domains
18. https://cursor.com/blog/agent-sandboxing — sandbox engineering
19. https://openai.com/index/introducing-codex/ — Codex cloud sandbox, internet disabled
20. https://developers.openai.com/codex/concepts/sandboxing — Codex CLI Seatbelt/bwrap modes
21. https://developers.openai.com/codex/agent-approvals-security — approvals × sandbox matrix
22. https://github.com/openai/codex — Codex CLI
23. https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-firewall — Copilot firewall limitations
24. https://github.blog/changelog/2026-04-03-organization-firewall-settings-for-copilot-cloud-agent/ — org-level firewall
25. https://docs.github.com/en/copilot/reference/copilot-allowlist-reference — recommended allowlist (incl. Playwright browsers)
26. https://arxiv.org/abs/2307.13854 — WebArena (Zhou et al.); 812 tasks; GPT-4 14.41%; human 78.24%
27. https://webarena.dev/ — WebArena environment
28. https://github.com/web-arena-x/webarena — canonical impl; BrowserGym recommendation
29. https://github.com/ServiceNow/BrowserGym — BrowserGym ecosystem
30. https://jykoh.com/vwa — VisualWebArena (Koh et al., ACL 2024)
31. https://openai.com/index/computer-using-agent/ — CUA; OSWorld 38.1%; WebArena 58.1%; WebVoyager 87%
32. https://openai.com/index/introducing-operator/ — Operator preview; 2025-07 ChatGPT agent
33. https://developers.openai.com/api/docs/guides/tools-computer-use — computer tool loop; screenshot `detail: original`
34. https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool — `computer_toolset_20260801`; 17 members; allowlist guidance
35. https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool — `browser_toolset_20260801`; refs vs coordinates; optional JS
36. https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/README.md — computer-use Docker demo
37. https://playwright.dev/docs/getting-started-mcp — Playwright MCP a11y snapshots
38. https://github.com/microsoft/playwright-mcp — `--allowed-origins`, `--isolated`, `browser_run_code_unsafe`
39. https://www.anthropic.com/engineering/multi-agent-research-system — Lead/Subagents/CitationAgent; +90.2%; 4×/15× tokens; 80% BrowseComp
40. https://openai.com/index/introducing-deep-research/ — Deep Research; GAIA 67.36; HLE 26.6%; 5–30 min
41. https://developers.openai.com/api/docs/guides/deep-research — o3/o4-mini-deep-research; background; search/fetch MCP only
42. https://developers.openai.com/api/docs/models/o3-deep-research — $10/$40
43. https://blog.google/products-and-platforms/products/gemini/google-gemini-deep-research/ — Gemini Deep Research launch 2024-12-11
44. https://ai.google.dev/gemini-api/docs/deep-research — 60 min cap; MCP; $1–$7 preview estimates
45. https://ai.google.dev/gemini-api/docs/background-execution — background interactions
46. https://arxiv.org/abs/2311.12983 — GAIA (Mialon et al.); humans 92%; GPT-4+plugins 15%
47. https://arxiv.org/abs/2411.04468 — Magentic-One; 38% GAIA; 32.8% WebArena
48. https://arxiv.org/abs/2305.03111 — BIRD (Li et al.); GPT-4 54.89%; human 92.96%
49. https://bird-bench.github.io/ — BIRD leaderboard (Databricks RLVR 75.68, Arctic-Text2SQL)
50. https://spider2-sql.github.io/ — Spider 2.0; 632 tasks; 10.1% vs 86.6% Spider 1.0
51. https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst — Cortex Analyst; SELECT; stage YAML trap; credits
52. https://docs.snowflake.com/en/user-guide/views-semantic/overview — semantic views
53. https://docs.snowflake.com/en/user-guide/snowflake-cortex/pricing — Analyst standalone vs Agents token billing
54. https://www.snowflake.com/legal-files/CreditConsumptionTable.pdf — credit rates (authoritative $ table)
55. https://docs.databricks.com/aws/en/genie-agents/concepts — dual credentials; RLS; trusted assets; Agent mode
56. https://docs.databricks.com/aws/en/genie-agents/set-up — warehouse embedding; CAN USE vs SELECT
57. https://platform.claude.com/docs/en/about-claude/pricing — model + web search $10/1k + toolset token overheads
58. https://developers.openai.com/api/docs/pricing — web search, containers, file search
59. https://www.anthropic.com/news/claude-sonnet-5 — Sonnet 5 $2/$10 made permanent
60. https://osworld-project.github.io/ — OSWorld project site (human 72.4% cited in CUA post)
61. https://arxiv.org/abs/2404.07972 — OSWorld (Xie et al.), full-OS computer-use bench
62. https://arxiv.org/abs/2401.13919 — WebVoyager (He et al.)

Optional follow-ups not counted in the core 62: OpenAI Operator System Card; InjecAgent / WASP prompt-injection benches; Databricks Genie budget docs; Snowflake `STATEMENT_TIMEOUT_IN_SECONDS` parameter reference.
