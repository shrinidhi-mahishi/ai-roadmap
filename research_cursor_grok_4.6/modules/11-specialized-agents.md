# Module 11 — Specialized Agents

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/11-specialized-agents.md` (researched 2026-08-21, 62 sources). Prices are vendor-published token/tool/credit rates as of 2026-08-21. Benchmark numbers are from named papers or official product posts. ⚠️ No unpublished production p50/p95/p99 SLOs are invented. `$ / 1k` figures are **[inferred]** from published SKUs × a stated reference loop — not a vendor “per task” SKU.
**Mandatory topics**: Coding agents · Browser agents · Research agents · Data agents.

The unit of production is not “a coding LLM” or “a SQL prompt.” It is a **control plane** that owns specialty routing, loop/step/$ caps, approval policy, and durable job identity, wrapping a **data plane** that is a **specialty runtime**: worktree sandbox, leased browser context, research job with Memory + artifacts, or warehouse session under RLS. Magentic-One (Fourney et al., arXiv:2411.04468) is the generalist overlay — Orchestrator + Task/Progress ledgers + tool-shaped workers (WebSurfer, FileSurfer, Coder, ComputerTerminal). Ablations: full ledgers off **−31%**; any one worker off **−21%** (Coder/Executor) to **−39%** (FileSurfer). Published GPT-4o-era: **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench**. That result is a reminder: specialties are **runtimes**, not weights.

**Invariant:** the model never owns the runtime. It emits tool calls (ACI commands, `computer_call` / `browser_toolset` members, search/fetch, SQL). A specialty executor enforces policy and returns observations. Collapsing that executor into “the LLM has a terminal” is the dominant enterprise failure. Anthropic (Jun 2025): coding is a **poor** fit for their orchestrator-worker research DAG (few truly parallelizable subtasks; weak real-time coordination). Do not copy-paste a research wave onto a git loop.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, PII detect→redact→audit, tool RBAC, specialty pick, loop/step/subagent/$ caps, Temporal workflow id / Kafka outbox, circuit state, and HITL. Data plane owns four **mutually isolated sandboxes** plus the model forward pass. Persistence is **resume identity**, not the chat: PR/branch for coding, `storageState`+lease id for browser, Memory plan + citation graph for research, warehouse query-history id + semantic-model version for data. Tool proxies are **per-specialty MCP** (`git`/`gh`; Playwright; search/fetch-only; read-only SQL) with signed tickets — Claude Code sandbox and Copilot firewall **do not cover MCP** (2026 audit finding). Telemetry is the only authoritative place for turn/step/git-mutation counts, screenshot retention, citation fetch times, bytes billed, breaker state, and RLS-empty outcomes.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (IDE / @bot comment / analyst UI / ChatGPT / Temporal Signal / HITL)   │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant principal (never sandbox IAM)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Loop budget  │─▶│ Specialty router      │  │
│  │ auth,quota │  │ PII detect→  │  │ max_turns=10 │  │ coding|browser|       │  │
│  │ RPM/TPM    │  │ redact→audit │  │ step / SQL / │  │ research|data         │  │
│  │ breaker    │  │ tool RBAC    │  │ subagent /$  │  │ NEVER research-DAG on │  │
│  │ Retry-After│  │ MCP host     │  │ kill-switch  │  │ a git worktree        │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
│                         │                 ▼                     │               │
│                         │          ┌────────────────┐           │               │
│                         │          │ Orchestrator   │◀──────────┘               │
│                         │          │ Temporal wf /  │  tool_use / computer_call │
│                         │          │ Kafka outbox   │  search+fetch / run_sql   │
│                         │          │ rainbow pin    │  stop / escalate_human    │
│                         │          └───────┬────────┘                           │
└─────────────────────────┼──────────────────┼────────────────────────────────────┘
                          │                  │
                          │                  ▼
┌─────────────────────────┴───────────────────────────────────────────────────────┐
│ DATA PLANE — FOUR SPECIALTY SANDBOXES  (model is untrusted planner only)        │
│                                                                                 │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────────┐  │
│  │ CODING SANDBOX      │  │ BROWSER POOL        │  │ RESEARCH JOB            │  │
│  │ Seatbelt/Landlock/  │  │ one task, one ctx   │  │ Lead plan → Memory      │  │
│  │ bwrap / cloud VM    │  │ --isolated profile  │  │ 3–5 Sonnet subs //      │  │
│  │ ACI: search/view/   │  │ a11y refs XOR       │  │ CitationAgent pass      │  │
│  │ edit/test; git      │  │ pixels+pointer      │  │ search/fetch MCP only   │  │
│  │ net: deny→allowlist │  │ redirect re-check   │  │ site allowlist          │  │
│  │ write: WS; hooks    │  │ 5s Playwright ping  │  │ artifacts on FS (refs)  │  │
│  │ blocked; UID remap  │  │ no javascript_exec  │  │ sync wave; no mid-steer │  │
│  └──────────┬──────────┘  └──────────┬──────────┘  └────────────┬────────────┘  │
│             │                        │                          │               │
│             │         ┌──────────────┴──────────┐               │               │
│             │         │ DATA SESSION            │               │               │
│             │         │ semantic view + dialect │               │               │
│             │         │ read-only SQL allowlist │               │               │
│             │         │ compute ≠ data identity │               │               │
│             │         │ RLS / column masks      │               │               │
│             │         │ bytes fuse + timeout    │               │               │
│             │         │ notebook TTL (optional) │               │               │
│             │         └──────────────┬──────────┘               │               │
│             └───────────┬────────────┴───────────┬──────────────┘               │
│                         ▼                        ▼                              │
│              ┌────────────────────┐   ┌─────────────────────┐                   │
│              │ TOOL PROXIES (MCP) │   │ MODEL FORWARD       │                   │
│              │ git/gh | Playwright│   │ hosted or vLLM      │                   │
│              │ search+fetch | SQL │   │ cache-stable schema │                   │
│              │ signed ticket; no  │   │ tool JSON order pin │                   │
│              │ token passthrough  │   │                     │                   │
│              └─────────┬──────────┘   └──────────┬──────────┘                   │
└────────────────────────┼─────────────────────────┼──────────────────────────────┘
                         │                         │
                         ▼                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────────────┐ │
│  │ Coding saga  │ │ Browser      │ │ Research     │ │ Data                    │ │
│  │ worktree/VM  │ │ storageState │ │ Memory plan  │ │ semantic-model version  │ │
│  │ PR + CI logs │ │ encrypt+TTL  │ │ citation URL │ │ query history = user    │ │
│  │ git branch   │ │ lease id     │ │ + fetch time │ │ warehouse session       │ │
│  │ not chat     │ │ never share  │ │ job id       │ │ notebook snapshot       │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └─────────────────────────┘ │
│  Temporal history = control; Kafka outbox = tool intent before side-effect      │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ Audit (WORM)│  │ Metrics      │  │ Trace spans │  │ Usage (authoritative) │  │
│  │ corr-id,    │  │ turns/steps/ │  │ gateway →   │  │ tokens, cache hits,   │  │
│  │ hashed args,│  │ git muts,    │  │ sandbox →   │  │ search calls, VM-min, │  │
│  │ policy, RLS │  │ breaker,     │  │ MCP → model │  │ warehouse-seconds,    │  │
│  │ empty, cite │  │ pool leases  │  │ citation    │  │ screenshot retain flag│  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (IDE/chat), a GitHub event (`@claude` / Copilot cloud), or a background job (`background: true` + webhook; Gemini `background=True` + `store=True`). Gateway stamps `correlation_id`, authenticates the **tenant principal**, and checks RPM/TPM plus a per-specialty admission fuse (cloud VMs, browser leases, warehouse slots).
2. **Policy.** PII detect→redact **before tokenize**. Tool RBAC attaches only this specialty’s MCP catalog. Coding: Auto-review / allowlist / Run Everything (Cursor 3.6 default Auto-review as of 2026-05-29; **not** a security boundary). Browser: domain allowlist + watch-mode on email. Research: trusted-site restriction (OpenAI Feb 2026). Data: semantic-model version + end-user warehouse role.
3. **Route.** Specialty router is **code**, not a system prompt. Inputs: issue label `agent-eligible`, “browse this SPA,” “brief the board,” “what is net revenue.” Hard no: Magentic research DAG on a git worktree; CUA pixels on an SSO-admin cookie; shared service account “so the data agent can see everything.”
4. **Lease runtime.** Coding: clone into a worktree or cloud VM (Codex cloud: internet **disabled**; Copilot: Actions appliance + org firewall). Browser: `--isolated` context (persistent Playwright profiles **serialize** and cross-tenant). Research: job id + Memory for the plan (200k will truncate). Data: warehouse session with `STATEMENT_TIMEOUT_IN_SECONDS` / `jobTimeoutMs` / `maximumBytesBilled`.
5. **Loop.** Model emits a tool call. Proxy verifies the signed ticket, executes **inside** the sandbox, JSON-encodes the observation, optionally screens for injection, appends `tool_result`. Coding ACI is search/view/edit/test — not unbounded `cat`. Browser prefers a11y refs over coordinates when the tree exists (`read_page` depth **15**, cap **50,000** chars). Research subs get isolated windows and **3+** tools in parallel. Data emits dialect-tagged SQL through the allowlist.
6. **Oracle, not self-report.** Coding: hidden tests (`FAIL_TO_PASS` + `PASS_TO_PASS`) and CI on the agent’s branch. Browser: functional assertion on page/DB state (WebArena), not action-trace match. Research: CitationAgent on **final report + source documents**. Data: execution accuracy + trusted-asset match; **RLS-empty is success**.
7. **Persist and emit.** Coding durable object is the **PR + CI**. Browser: encrypted `storageState`, short TTL. Research: citation graph + fetch timestamps (the web moved — re-run is a **new** job). Data: query history attributed to the **user**. Usage, hashed args, policy decision, and breaker state land in the WORM sink. Compensating action is close-PR / drop-lease / cancel-warehouse-job — not “undo the chat.”

**Interview talking point:** “Specialty = runtime + oracle + identity. The model plans. The sandbox executes. MCP is the bus; sandboxes do not cover MCP.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Coding — ACI, repo maps, oracles, PR loops

**SWE-agent (Yang et al., NeurIPS 2024, arXiv:2405.15793).** The contribution is the **agent-computer interface**, not a new model. Raw shell is a hostile API (unbounded `cat`, no lint-on-edit). SWE-agent ships a small command set: search / view / edit / test. Original SWE-bench (Jimenez et al., ICLR 2024, **2,294** issues, **12** Python repos): **12.47%** resolved (286/2,294), **18.00%** Lite (54/300), HumanEvalFix **87.7%** pass@1. Vs shell-only GPT-4 Turbo: **+64% relative**. Vs RAG on Lite: **8–13×** token cost, **6.7×** resolved-rate. That ratio is still the budget conversation.

**Oracle (SWE-bench harness, Docker since Jun 2024).** Success is not “the patch looks right.” The harness applies gold `test_patch` to expose tests, then runs `model_patch` on a frozen `base_commit` snapshot. Gold = **FAIL_TO_PASS** without regressing **PASS_TO_PASS**. SWE-bench Verified (OpenAI + authors, 2024-08-13): **500** engineer-confirmed instances; GPT-4o on then-best scaffold **33.2%** vs **16%** original; Agentless roughly **16% → 32%**. Difficulty: **196** <15-minute, **45** >1-hour. OpenAI (2026): Verified is **contaminated/saturated**; SWE-bench Pro public split **731**, later estimate **~30%** of Pro tasks broken. Quote **named split + named scaffold + date**. “96% Verified” aggregator pages are not an SLO.

**Agentless (Xia et al., arXiv:2407.01489)** is the anti-agent control: localize → repair → optional reproduction-test rerank. **32.00%** Lite at **$0.70**/instance (later revision; earlier **27.33% / $0.34**). If the job is localize+patch+test, a pipeline with a test oracle is cheaper and more auditable than a 50-turn ReAct loop.

**Repo maps vs agentic search (do not dump the repo).** Aider: tree-sitter tags → file graph → personalized PageRank → `--map-tokens` default **1k**, expanded when no files are in chat; files already in chat are **omitted**. Claude Code: agentic search over the working tree + `CLAUDE.md`. Cursor: index then agent loop. Codex cloud: preload GitHub repo into a **per-task container**. Three context strategies, one job.

**Sandbox table (data plane).**

| Runtime | Isolation | Network default | Write default | Approval |
| --- | --- | --- | --- | --- |
| Cursor v2.0+ (net policy 2.5, Feb 2026) | macOS Seatbelt; Linux Landlock+seccomp (kernel **6.2+**); UID 0 *inside* user ns | Deny, then `sandbox.json` | Workspace RW; `.git/hooks`, `.git/config`, `.vscode`, `.cursor/*.json` blocked | Auto-review default **3.6**; Cloud Agents: **no** Run Modes |
| Claude Code | Seatbelt; Linux/WSL2 **bubblewrap** + `socat`; WSL1 unsupported | **No** pre-allowed domains; `strictAllowlist` v**2.1.219+** (user/managed/CLI — **not** repo settings) | FS policy; `/sandbox` panel | Auto classifier; `failIfUnavailable` if bwrap missing |
| Codex CLI | Seatbelt / `bwrap`+seccomp | `workspace-write` net **off** unless opted in | `read-only` / `workspace-write` / `danger-full-access` | `on-request` / `untrusted` / `never` / `auto_review` |
| Copilot cloud | Actions appliance | Firewall **on**; recommended allowlist **on** (pkg repos, CAs, **Playwright browser hosts**) | Clone + PR branch | Org can lock list (changelog 2026-04-03) |
| Codex cloud (2025-05) | Per-task container | **Internet disabled** | Provided repo + setup deps | Human opens PR after commit |

Cursor `sandbox.json`: deny **beats** allow; RFC1918 + **169.254.169.254** + IPv6 ULA/link-local blocked (SSRF). Team-admin allowlist **replaces** (does not union) local lists. Merge: per-user < per-repo < team-admin < hardcoded. Linux UID 0: scripts must use `CURSOR_ORIG_UID`/`GID` for Docker `--user`. Claude: sandbox applies to **Bash**, not Read/Write/WebFetch/WebSearch/MCP/hooks; `deniedDomains` wins; `WebFetch(domain:)` **widens** Bash egress.

**State machine.** `queued → clone → loop{search,view,edit,test} → patch → push → pr → ci → {merge_by_human | close}`. Caps: OpenAI Agents SDK `max_turns` default **10**; Magentic-One `max_turns=20`, `max_stalls=3` then replan; also cap **wall-clock and git mutations** (e.g. max 20 commits) because a turn-only cap still allows unbounded `git` inside one bash call. Complexity: \(\Theta(I \cdot (T_{\mathrm{map}} + T_{\mathrm{obs}} + T_{\mathrm{test}}))\) with \(I\) iterations. Invariant: one agent run per worktree; parallel cloud tasks are **N VMs**. Test oracle is necessary but leaky (flakes, snapshots, missing coverage) — bind merge to **CI node-ids**, not chat “all tests passed.” Fork PRs: GitHub **withholds secrets** (poisoned-queue defense). Claude Action autonomous runs have **no HITL** — tools not pre-allowed **stall**.

### 2.2 Browser — DOM/a11y vs pixels

Two observation channels, one executor rule: Anthropic defines schemas (`computer_toolset_20260801` **17** members; `browser_toolset_20260801` **27** default, +~880 tokens if all 4 optional members); **your** process runs every call. Not in Claude Managed Agents as of 2026-08 docs. Batch members run **in order**. Validate coordinates against the **viewport you captured**, not the model’s claimed screen size. Downsample CUA frames to **1440×900** / **1600×900** and remap; `detail: "original"` on GPT-5.6 does **not** resize.

| Channel | Examples | Strength | Cost / fragility |
| --- | --- | --- | --- |
| Pixels + pointer | OpenAI CUA (Jan 2025); Anthropic computer toolset (screenshot, click, type, zoom); OSWorld | Any GUI; no a11y required | Every step is an image; coordinate drift; **visible** injection is visible |
| Structured refs | Playwright MCP `browser_snapshot` → `ref=e5`; Anthropic `read_page` `[ref_2]` | Refs survive reflow; cheaper; no vision required for MCP | Canvas/custom widgets missing; `javascript_exec` = **page-privileged RCE**; Playwright `--allowed-origins` **does not** constrain redirects |

CUA official: **OSWorld 38.1%** (prev SOTA 22.0%, human **72.4%**; original OSWorld then-best **12.24%** on **369** tasks); **WebArena 58.1%** (prev CU SOTA 36.2%, browsing 57.1%, human **78.2%**); **WebVoyager 87%** (2024 paper agent **59.1%** on 15 live sites). OSWorld is a **full OS** bench — 38% vs WebVoyager 87% is not a contradiction. Test-time scaling: more steps → higher OSWorld (cost **and** stall knob). Operator reliability is prompt-sensitive (tagvenue: **8/10** with filter hints vs **3/10** without). WebArena (Zhou et al., ICLR 2024): **812** long-horizon tasks on self-hosted Docker sites; GPT-4 agent **14.41%** vs human **78.24%**; eval is **functional**, not sequence match. VisualWebArena: text-only agents fail when the cue is in the screenshot. BrowserGym (TMLR 2025) is the eval bus (Playwright underneath).

Playwright MCP: default **headed**, persistent profile per workspace hash; `--isolated` for ephemeral; HTTP `:8931`, **5 s** heartbeat (`PLAYWRIGHT_MCP_PING_TIMEOUT_MS`). `browser_run_code_unsafe` is RCE-equivalent. Concurrent clients on one persistent profile **conflict**.

**State machine.** `lease → (snapshot → act)* → assert_end_state → release`; stall → screenshot + HITL, not spin. Allowlist at **network layer and after redirects** in `navigate`; block loopback/link-local/private unless required; build reads from **rendered** a11y/text, not raw DOM. Leave `javascript_exec` / `file_upload` off. CUA is trained to **hand back** on CAPTCHA/login — auto-filling passwords voids that control. Complexity: pixels \(\Theta(S \cdot T_{\mathrm{image}})\); a11y \(\Theta(S \cdot C_{\mathrm{tree}})\) with \(C_{\mathrm{tree}} \le 5 \times 10^4\). Invariant: **one task, one context**; `storageState.json` is a refresh token.

### 2.3 Research — breadth, Memory, CitationAgent

Research is **breadth-first compression**: many independent sources, path-dependent next queries, **no** single test oracle. Anthropic (2025-06-13): LeadResearcher writes a plan to **Memory** (200k will truncate) → spawns Subagents with objective, output format, tool list, stop boundary → isolated windows, **3+** tools in parallel → condensed summaries → optional another wave → **CitationAgent** attributes claims to URLs. Multi-agent vs single Opus 4: **+90.2%** on their **internal** research eval (not a public leaderboard). BrowseComp: token usage explains **80%** of variance; tokens + tool-call count + model = **95%**. Agents **~4×** chat tokens; multi-agent **~15×**. Parallel 3–5 subs × 3+ tools: wall-clock **−90%**. Early failure: lead spawned **50** subs; vague brief → duplicated 2025 supply chain + 2021 auto chips. Scale-effort (code, not prompt): simple **1** agent, **3–10** calls; comparison **2–4** subs, **10–15** calls each; complex **>10** with disjoint responsibilities. Tool-description rewrite agent: **−40%** future task time. Sync waves: lead **cannot** steer mid-flight. Rainbow-deploy prompts so in-flight jobs are not killed. Subagent artifacts on a **filesystem** — refs, not telephone-game through the lead.

**Citation is a separate pass.** “Cite as you write” loses the source across summarization hops. CitationAgent reads **final report + source documents**. Failure mode is **URL exists, claim does not** (SEO farms beat PDFs in Anthropic human eval). CitationAgent on summaries cannot catch a fabricated quote.

OpenAI Deep Research (ChatGPT 2025-02-02; API `o3-deep-research` / `o4-mini-deep-research`): inline citations; GAIA pass@1 **67.36** (L1 **74.29**, L2 **69.06**, L3 **47.6**) vs then-SOTA **63.64**; cons@64 **72.57**; Humanity’s Last Exam **26.6%** vs o1 **9.1%**. API **must** include web search and/or remote MCP **search+fetch** and/or file search; other function tools **unsupported**. Recommend `background: true` + webhooks. ChatGPT: **5–30 min**; Plus/Team/Enterprise **25**/month full, Pro **250**, Free **5**. GAIA (Mialon et al.): **466** questions; humans **92%**, GPT-4+plugins **15%**; Magentic-One **38%** (2024). Gemini Deep Research: `background=True` required; **max 60 min**, most **<20 min**; custom function tools **no**; Google’s preview estimates ⚠️: typical ~**80** searches, ~**250k** in (50–70% cached), ~**60k** out → **~$1–$3**/task; Max ~**160** / **900k** / **80k** → **~$3–$7**.

**State machine.** `plan(Memory) → wave(subs) → join → {another_wave | cite} → store`. Idempotency: **none** (the web moved). Persist citation graph + fetch timestamps. Invariant: `max_subagents` in the runtime (ban 50-way fan-out). Judge: Anthropic found **one** LLM-judge call (0–1 + pass/fail) more consistent than a panel; humans still caught SEO-farm bias that evals missed.

### 2.4 Data — schema grounding, SQL allowlist, RLS

Schema-only parsers die on production warehouses. BIRD (Li et al., NeurIPS 2023): **12,751** NL–SQL pairs, **95** DBs, **33.4 GB**, **37** domains; dirty values, external knowledge, efficiency (R-VES). Paper GPT-4 **54.89%** execution accuracy vs human **92.96%**. 2025 leaderboard (dev): Databricks RLVR 32B **75.68**; Snowflake Arctic-Text2SQL-R1-32B **72.20 / 73.84**. Residual errors **look like a dashboard**. Spider 2.0: **632** enterprise workflows; DBs often **>1,000** columns; queries can exceed **100** lines; o1-preview-era **10.1%** vs **86.6%** Spider 1.0. Splits: Snow (547), Lite (547; BigQuery 214 / Snowflake 198 / SQLite 135), DBT (68). Dialect is part of the tool schema.

**Grounding artifact = semantic model**, not `information_schema` dump. Snowflake Cortex Analyst: semantic **views** (GRANT/RBAC/sharing) vs legacy YAML on a **stage**. YAML trap: any role with stage access can read the model **without** table SELECT — keep GRANTs in lockstep. Databricks Genie Agents (concepts updated 2026-08-17): Unity Catalog + **knowledge store** (agent-local; does **not** mutate UC) + instructions + example SQL + **trusted assets** (author-verified parameterized SQL; answers tagged trusted). Generated SQL is **read-only**. **Dual credentials:** warehouse compute = **author’s** embedded identity; **data** access = **end user’s** UC identity — row filters and column masks apply; unauthorized → **empty**, attributed in query history to the user. **Inspect** (preview): extra SQL probes then rewrite. **Agent mode:** plan → multiple SQL → cited report; can read UC **volume** files (unstructured RAG inside a SQL agent — pin, virus-scan, do not mix with write-capable notebooks).

**SQL generation threat** is not PHP concat. It is syntactically valid, semantically over-broad SQL: `SELECT *`, missing tenant predicate, `UNION` to `INFORMATION_SCHEMA`, `COPY INTO @evil_stage`. Mitigations: read-only role; **no** `ACCOUNTADMIN`; parser allowlist (`SELECT`/`WITH`/`EXPLAIN` only); `maximumBytesBilled` as a **dry-run fuse**; block `COPY`/`PUT`/`CREATE`; never `EXECUTE IMMEDIATE` of model text in a write role. Timeouts in depth: (1) max statements per question, (2) session timeout, (3) bytes fuse, (4) warehouse auto-suspend, (5) notebook idle TTL. **Do not** blindly retry a timed-out aggregation — the second try is another full scan. Surface `QUERY_CANCELED` with “narrow the date window.”

Notebooks are a third data-plane: long-lived kernel, `df` in RAM, `!pip` to the internet, Spark/warehouse attached. Treat like a browser profile: snapshot, idle TTL, no secrets in cells, no shared kernel across tenants. A notebook agent is a **coding agent with a warehouse credential** — apply both sandboxes. Prefer papermill parameters. Size Genie **author** warehouse for interactive Q&A, not ETL; send Agent-mode fan-out to a **separate** warehouse.

**State machine.** `ground(semantic_ver) → sql → parse_allowlist → dry_run_fuse → execute(RLS) → {inspect_rewrite | answer}`. Complexity: LLM tokens are the cheap term; warehouse bytes processed dominate. Invariant: compute identity ≠ data identity; empty RLS result is success, not a bug to “fix” with a service account.

---

## 3. Token Economics & NFR Analysis

Published SKUs are **not** task prices. Split the bill: **tokens / search calls / sandbox-minutes / warehouse-seconds**. US-only Claude inference **1.1×**; Fast Opus 5 **2×**; Batch **0.5×**. Claude 4.7+ tokenizer: **~30% more tokens** for the same text vs Sonnet 4.6-and-earlier — agent loops silently inflate. Prompt cache is a coding NFR: ACI schema + repo map resent every turn; Anthropic cache hits **0.1×** input; stabilizing tool JSON order (MCP 2026-07-28) is a **cost** control.

### 3.1 `$ per 1k` reference loops — all **[inferred]**

| Item | Rate (2026-08-21) |
| --- | --- |
| Claude Opus 5 | **$5 / $25** per MTok in/out; cache hit **$0.50**; 5m write **$6.25**; 1h **$10** |
| Claude Sonnet 5 | **$2 / $10** (intro made **permanent** 2026-08-10) |
| Claude Fable 5 / Haiku 4.5 | **$10 / $50** · **$1 / $5** |
| Anthropic web search / fetch | **$10 / 1k searches**; fetch **$0** extra (page tokens; ~2.5k / 10 kB) |
| `computer_toolset` / `browser_toolset` schema | **~4,500** / **~6,600** input tokens/request (`zoom` off −~410; +~880 all optional browser members) |
| o3-deep-research / o4-mini-deep-research | **$10 / $40**; cache **$2.50** · **$2 / $8** (⚠️ verify o4-mini live) |
| OpenAI web search | **$10 / 1k** + content at model rates |
| OpenAI containers | 1/4/16/64 GB **$0.03 / $0.12 / $0.48 / $1.92** per **20-min** session |
| OpenAI file search | **$0.10**/GB-day (1 GB free); **$2.50 / 1k** tool calls |
| Gemini Deep Research (preview) | **~$1–$3** typical; **~$3–$7** Max |

| Specialty | Stated reference loop | Arithmetic | **[inferred] $/1k** |
| --- | --- | --- | --- |
| Coding, Agentless-class | Paper **$0.70**/Lite instance | \(1000 \times 0.70\) | **~$700** (2024 GPT-4o; stale SKU) |
| Coding, SWE-agent-class Sonnet 5 | 40 turns × (30k in + 1.5k out) at $2/$10 | \(40 \times (0.060+0.015)=\$3.00\)/task | **~$3,000** |
| Coding, Opus 5 long refactor | 80 turns × (50k in + 2k out) at $5/$25 | \(80 \times (0.25+0.05)=\$24\)/task | **~$24,000** |
| Research, Anthropic 15× chat | Chat 80k in + 4k out Opus 5 = $0.50; ×15 + 25 searches × $0.01 | **$7.50** + $0.25 | **~$7,800** |
| Research, o3-deep-research | 200k in + 25k out + 20 web calls | $2.00+$1.00+$0.20 | **~$3,200** |
| Research, Gemini typical | Vendor midpoint $2 | — | **~$2,000** (⚠️ preview) |
| Browser, CUA-style | 40 turns; ~4.5k toolset + 8k image + 800 out Sonnet 5 | \(\approx 40 \times \$0.033 \approx \$1.3\)/task **plus VM** | **~$1,300 + pool capex** |
| Data, Cortex/Genie + XS warehouse | Message credits + warehouse-seconds | Dominated by **warehouse** | **Do not** budget like research |

Same week, same lab **[inferred]**: a hard Opus research brief and a medium SWE-agent run land in the **same few-thousand-dollars-per-1k** band; an 80-turn Opus coding session **outruns** typical deep research. Warehouse Q&A can be cheaper in LLM $ and **more expensive in compute $** on a 33 GB BIRD-class scan. Cortex Analyst bills per successful HTTP **200** (standalone) or token credits via Cortex Agents, **plus** warehouse; failed generations are the cheap failure — **executed** bad SQL is the expensive one.

### 3.2 Latency — published envelopes, percentiles **[inferred]**

⚠️ **No vendor publishes production p50/p95/p99** for these loops. Design envelopes:

| Specialty | Published duration | What blows the tail |
| --- | --- | --- |
| OpenAI Deep Research | **5–30 min** (API client hint `timeout: 3600s`) | Extra search waves; PDFs |
| Gemini Deep Research | Most **<20 min**, hard cap **60 min** | Max variant; MCP stalls |
| Anthropic Research | Sequential “hours”; parallel **−90%** | Sync wait on one stuck sub |
| CUA / Operator | Tens to **100+** screenshot turns (Cambridge quiz 150+ UI events) | Popups, CAPTCHA handoff |
| SWE-bench instance | Minutes–tens of minutes (harness + tests) | Flakes, install, infinite edits |
| Warehouse SQL | Platform default often **10 min–6 h** | Cartesian joins |
| GitHub Actions coding | Hosted runners commonly **6 h** max | Unbounded `@claude` retries |

Working SLA (label **[inferred]** — sequential-sum / parallel-max / documented caps, not a vendor SLO):

| Percentile | Coding PR factory | Browser internal RPA | Research brief | Data chat SQL |
| --- | --- | --- | --- | --- |
| p50 | **[inferred]** 8–15 min if tests are the oracle | **[inferred]** 1–3 min (a11y, <20 steps) | **[inferred]** 8–15 min (Gemini “most <20”; OpenAI mid of 5–30) | **[inferred]** 3–15 s if warehouse warm |
| p95 | **[inferred]** 45 min kill → `needs_human` (research §6.2) | **[inferred]** step-cap hit; popup/HITL | **[inferred]** 30 min SLO | **[inferred]** warehouse queue + Inspect probes |
| p99 | Actions **6 h** ceiling — **not** the model | Playwright **5 s** ping death or 100+ pixel steps | Gemini **60 min** kill; OpenAI 1 h client timeout | Warehouse timeout / bytes fuse |

| Tier | Mitigations |
| --- | --- |
| p50 | ACI not raw shell; a11y refs not full screenshots; 1 agent / 3–10 calls for simple research; semantic view + trusted assets; prompt cache on tool JSON |
| p95 | Turn cap 40; step cap + HITL on stall; `max_subagents=8`; `STATEMENT_TIMEOUT_IN_SECONDS=60` chat / 300 Agent mode; separate warehouse for fan-out |
| p99 | Kill at 45 min with label; `--isolated` pool + heartbeat raise behind proxy; rainbow pin so deploys don’t reset the clock; `maximumBytesBilled` dry-run; never retry full-scan timeouts |

### 3.3 Throughput, back-pressure, availability, RPO/RTO, compliance

**Throughput / back-pressure (specified where the research is specified).** Playwright MCP HTTP: **5 s** ping or the session dies. OpenAI containers: billed in **20-min** chunks (5-min minimum on the pricing page). Gemini: cannot chain a new interaction while `in_progress` (**400**). Cortex: only HTTP 200 messages bill. Cursor Auto-review uses Haiku 4.5 or GPT-5.4 Mini; if enterprise policy blocks both, Auto-review **disables**. Admission control: one run per worktree; N cloud tasks = N VMs; browser pool = N isolated contexts; Genie author warehouse is a noisy-neighbor victim of every business user — size for interactive Q&A. Back-pressure originates from **lease exhaustion** (VMs, browsers, warehouse slots), not from the LLM queue alone. Coding retry storms after test timeout are the same failure as SQL retry storms: cap and surface the error.

**Availability / RPO / RTO (architecture mapping; ⚠️ research publishes no numeric RPO/RTO).** Coding: availability = Actions/VM pool + model provider; **RPO [inferred]** = last pushed commit on the agent branch (chat is not the log); **RTO [inferred]** = new VM clone from that branch + CI. Browser: availability = pool + 5 s heartbeat; **RPO [inferred]** = last encrypted `storageState` (treat as a credential, short TTL); **RTO [inferred]** = re-lease `--isolated` context — do not resume a foreign profile. Research: availability = background job + rainbow pin (in-flight must survive prompt deploys); **RPO [inferred]** = Memory plan + artifact hashes + citation timestamps; **RTO [inferred]** = new job (not idempotent). Data: availability = warehouse + semantic-model version; **RPO [inferred]** = query history (Genie attributes to user); **RTO [inferred]** = new session, never reuse a notebook kernel across tenants.

**Compliance + explicit NFR trade-offs.**

| Trade-off | What you buy | What you pay |
| --- | --- | --- |
| ACI loop vs Agentless pipeline | +64% relative vs shell; 6.7× resolve vs RAG | **8–13×** tokens vs RAG; SWE-agent-class **[inferred] ~$3k/1k** vs Agentless **~$700/1k** |
| 40-turn cap vs 80-turn Opus | p95 kill; cost ceiling | Hard refactors escalate to humans (**[inferred] ~$24k/1k** if you don’t cap) |
| Pixels vs a11y | Canvas / full OS (OSWorld) | Image tokens every step; **[inferred] ~$1.3/task + VM**; injection is visible |
| Vendor browser (Operator) vs self-hosted | Their blocklist + monitor model | Third-party screenshot store; two audit planes if mixed |
| Multi-agent research vs single | +90.2% internal eval; **−90%** wall-clock | **~15×** chat tokens; **[inferred] ~$7.8k/1k** Opus 15× + search |
| Semantic view + RLS vs schema dump | Empty unauthorized rows; GRANT/RBAC | Curate 20 tables; knowledge-store samples must be **synthetic** in regulated tenants |
| Trusted assets vs free-form SQL | Board-pack metrics tagged trusted | Stale parameterized SQL if the metric moved |
| Screenshot retain vs redaction in executor | Debugability | Training-set-shaped PII logs of IdP/email |

Compliance controls that are in-research: do not persist production-IdP screenshots; Operator watch-mode on email; Gemini — malicious files/pages as injection, exfil if the agent browses **while** holding internal docs; OpenAI Deep Research restrict to trusted sites; Genie sends table/column metadata + **sample values** to the model (schema PII); Snowflake stage YAML readable without table SELECT.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Kafka

Coding, research, and Agent-mode SQL are **stateful workflows**, not request/response. Anthropic production notes for Research apply 1:1 to coding: do not restart from turn 0; checkpoint; tell the model the tool is failing and let it adapt; **rainbow-deploy**.

**Temporal.** Workflow id = `tenant:specialty:job_id` (coding: issue/PR id; research: job id; data: conversation id). Activities = model turn, sandbox exec, MCP `tools/call`, warehouse job, citation pass. Replay reconstructs control state; activities must be **idempotent** and return a recorded `ModelTurn` — never re-sample inside a replay-unsafe closure. Non-determinism (temperature, clock, live web) lives **inside** the activity. Continue-As-New at history bounds. Coding compensating action = close PR / revert, not undo chat. Research compensating action = mark job superseded (web moved). Data compensating action = cancel warehouse job (do not re-issue the same aggregation).

**Kafka.** Topics per tenant-shard: `agent.intents`, `agent.observations`, `agent.dlq`. Produce **intent** (`tool_call` + idempotency key) **before** the side effect (outbox). Tool workers consume, execute in the specialty sandbox, produce observation. Compaction on `job_id`. Poison (unparseable payload, identical hash crashing N times, git-mutation cap hit) → DLQ; do not block the partition. PR-loop poison: `@claude` on a public fork whose issue body is prompt injection — treat issue/PR text as **untrusted**, same as a webpage.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB SWE traces or Kafka lag SLOs for browser screenshot buses.

**Resume keys.** Cloud VM per task (Codex/Cursor Cloud/Copilot): task/PR id; machine discarded after. Local worktree: session id + git branch. GitHub Actions: workflow run id; **no** mid-job prompt deploy. Research: Memory + filesystem artifacts. Browser: lease id — persistent profiles are **not** a resume store across tenants. Queue invariant: **one agent run per worktree / one browser context / one notebook kernel**.

### 4.2 Failure taxonomy

| Class | Coding | Browser | Research | Data | Handler |
| --- | --- | --- | --- | --- | --- |
| Transient | 429/503, flake test, bwrap restart | 5 s ping flap, CDP disconnect | Search 429, MCP stall | Warehouse slot, `QUERY_CANCELED` | Full-jitter retry **idempotent** reads; honor `Retry-After`; **do not** retry full-scan SQL or purchases |
| Permanent | Illegal ACI args, missing bwrap + `failIfUnavailable` | Off-allowlist host after redirect | Unsupported function tools on Deep Research API | Dialect error, semantic GRANT miss | Fail the turn; fix schema/policy |
| Poison pill | Runaway `git commit`/`reset`; hook edits; identical crash hash | Shared persistent profile; `javascript_exec` from untrusted page | 50-sub fan-out; SEO-farm citation loop | `SELECT *` / `COPY` / retry-on-timeout storm | Mutation/step/subagent caps; DLQ; never auto-replay |
| Semantic | Delete the failing test (oracle gaming); issue-body injection | Page instructions override the user; hidden DOM vs rendered tree | URL exists, claim does not | RLS-correct lake scan; trusted-asset stale; service-account bypass | Hidden tests; redirect re-check; CitationAgent on sources; dual credentials + allowlist |

**Coding-specific:** Cursor write-protects `.git/hooks` and `.git/config` — other runtimes must too. Codex `untrusted` flags destructive git. Dependency confusion via unsandboxed `npm install` → registry allowlist. Secret exfil via `curl` to a new domain → `strictAllowlist` / Copilot PR comment on blocked dest. Linux Cursor UID 0: `chmod 777` as “root” in the namespace. Worktree collision: two cloud agents, same branch name. **Browser-specific:** `file_upload` + download-dir reuse = exfil; session fixation via shared profiles; drive-by off-allowlist — **redirect re-check** is mandatory. **Research-specific:** duplicate subs waste 15× tokens; lead cannot interrupt a stuck sub (sync). **Data-specific:** Inspect/Agent mode = N verification queries × warehouse seconds; notebook `df` bleed tenant A → B.

### 4.3 Circuit breaker and fallbacks

Per downstream (model provider, Playwright pool, warehouse, search API):

- **Closed:** traffic flows; consecutive failures or error-rate window trip to open.
- **Open:** fail fast; start recovery timer (e.g. 30 s). Interactive routes to fallback; batch/research jobs can wait.
- **Half-open:** one probe (or `half_open_max`). Success → closed; fail → open.

**Fallback chain:** primary specialty path → secondary (cheaper model and/or Agentless pipeline / a11y instead of pixels / o4-mini-deep-research / trusted-asset only) → **deterministic degrade** (`needs_human` with last observation, or cached trusted-asset result). Deterministic degrade must still emit a structured job status so GitHub/CI/warehouse parsers do not crash. Do not fall back from read-only SQL to a write role. Do not fall back from `--isolated` to a shared persistent profile. Do not fall back from search/fetch-only MCP to a general tool host on OpenAI Deep Research.

Provider-side cousins: Gemini `in_progress` **400** is a hard open on chain. Cursor Auto-review **disables** if both classifier models are policy-blocked — that is a silent control-plane open; log it.

### 4.4 Zero-Trust MCP, RBAC, PII, immutable logs, RLS

**Zero-Trust MCP as the tool bus.** Per-specialty servers, not one mega-server: `git`/`gh`; Playwright; search/fetch-only (OpenAI Deep Research constraint); warehouse SQL. Tool RBAC in the **host**, not the prompt. Copilot firewall **does not apply to MCP** (only Bash-started processes in the Actions appliance). Claude Code sandbox **does not apply** to MCP. Network allowlists at OS/proxy (Cursor `sandbox.json`, Claude `allowedDomains`+`strictAllowlist`, Copilot org firewall, Playwright `--allowed-origins` as a *hint* only). No secrets in model context. Claude MITM injects tokens **only** onto allowlisted hosts (`injectHosts`). Cursor blocks metadata IPs. Codex cloud: no internet. Genie: UC identity on the query, not a shared service principal for **data**. Audience-bound short-lived tickets; the LLM never sees the raw secret.

**Tool RBAC (least privilege per specialty).** Coding: no `danger-full-access` on fork `pull_request`; never `--dangerously-skip-permissions`. Browser: disable `javascript_exec` / `file_upload` / `browser_run_code_unsafe`. Research: lead has Memory+spawn only; CitationAgent read-only; `max_subagents=8`. Data: `SELECT`/`WITH`/`EXPLAIN` only; no `ACCOUNTADMIN`; trusted assets for “revenue.” HITL: Auto-review / watch-mode / plan approval / trusted-asset gating.

**PII pipeline:** detect → redact **before tokenize** → audit placeholder map (hash, never raw). Browser/computer-use screenshots of email/PII are **training-set-shaped logs** — redaction in the executor before pixels hit a vendor API if the contract requires it; do not persist production-IdP captures. Research: restrict to trusted sites; CitationAgent does not solve exfil. Data: knowledge-store sample values **synthetic** in regulated tenants.

**Immutable logs.** Hash-chain WORM rows: `correlation_id`, specialty, hashed args, policy version, sandbox decision, breaker state, RLS-empty flag, citation URL+fetch time, git commit SHAs, `call_id`. Streaming usage only on the terminal event. Reconstruct: policy snapshot + model id + sampled turn + observations + human interrupt.

**Data-agent RLS (correct vs wrong).** Correct (Genie): compute identity ≠ data identity; filters/masks on **tables**; empty unauthorized. Correct (Snowflake): SELECT + RBAC on semantic **views**; do not leave the only semantic copy on a stage readable without table SELECT. Wrong: service account that bypasses RLS “so the agent can see everything,” then filter in the LLM — rows leak in CoT and prompt cache.

| Agent | Who the model thinks it is | Who the runtime actually is |
| --- | --- | --- |
| Cursor local | The developer | Seatbelt/Landlock child; team-admin can deny egress |
| Claude Code CI | `@claude` | GitHub App + API key; fork PRs have **no** secrets |
| Codex cloud | The task | Isolated container, **no internet** |
| Copilot cloud | PR author / bot | Actions + org firewall; MCP **unfiltered** |
| Operator / ChatGPT agent | ChatGPT user | Vendor-hosted browser; watch-mode on email |
| Anthropic browser use | Your app’s user | **Your** Playwright/CDP; you own allowlist + redirect checks |
| Genie | The business user | Author warehouse + **user** UC identity |
| Cortex Analyst | Snowflake role on the token | That role’s SELECT + semantic-view GRANTs |

Cursor Auto-review is **explicitly not a security boundary**. Copilot firewall: “sophisticated attacks may bypass”; does not cover setup scripts. Claude `failIfUnavailable: true` so missing bwrap cannot silently unsandbox CI.

---

## 5. Production Enterprise Code

Stdlib-only specialty runtime: full-jitter retries, circuit breaker (closed → open → half-open), primary → secondary → deterministic `needs_human`, correlation-id JSON logs, PII detect→redact→audit, hash-chained WORM, specialty router, sandbox policy (deny-by-default, metadata/RFC1918 block, git-hooks write-block), SQL allowlist (`SELECT`/`WITH`/`EXPLAIN`, dialect required, no `COPY`/`UNION` to `INFORMATION_SCHEMA`). Run: `python specialty_runtime.py`.

```python
#!/usr/bin/env python3
"""Specialty-agent runtime (stdlib only). Run: python specialty_runtime.py"""
from __future__ import annotations

import hashlib
import ipaddress
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
from urllib.parse import urlparse

POLICY_VERSION = "specialty-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
MAX_TURNS = 10
MAX_GIT_MUTATIONS = 20
MAX_BROWSER_STEPS = 40
MAX_SUBAGENTS = 8
MAX_SQL_STATEMENTS = 6
SQL_TIMEOUT_S = 60

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
            "specialty": getattr(record, "specialty", None),
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


def build_logger(correlation_id: str, tenant: str, job_id: str) -> CorrelationAdapter:
    base = logging.getLogger("specialty.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "job_id": job_id}
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


class PolicyDenied(PermanentError):
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
    base_seconds: float = 0.01,
    max_seconds: float = 0.08,
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


class Specialty(Enum):
    CODING = "coding"
    BROWSER = "browser"
    RESEARCH = "research"
    DATA = "data"


def route_specialty(text: str) -> Specialty:
    t = text.lower()
    if any(k in t for k in ("pr", "pytest", "refactor", "worktree", "swe-")):
        return Specialty.CODING
    if any(k in t for k in ("browse", "click", "dom", "playwright", "screenshot")):
        return Specialty.BROWSER
    if any(k in t for k in ("brief", "cite", "competitor", "deep research")):
        return Specialty.RESEARCH
    if any(k in t for k in ("sql", "revenue", "warehouse", "snowflake", "genie")):
        return Specialty.DATA
    raise PermanentError("unroutable specialty")


_BLOCKED_NET = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)
_WRITE_BLOCK = (".git/hooks", ".git/config", ".vscode", ".cursor/")
_SQL_OK = frozenset({"select", "with", "explain"})
_SQL_BAN = re.compile(
    r"\b(copy|put|create|insert|update|delete|merge|drop|grant|execute)\b", re.I
)


@dataclass(frozen=True)
class SandboxPolicy:
    specialty: Specialty
    allowed_hosts: frozenset[str]
    network_default_deny: bool = True

    def check_net(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise PolicyDenied("empty host")
        try:
            ip = ipaddress.ip_address(host)
            if any(ip in n for n in _BLOCKED_NET):
                raise PolicyDenied(f"blocked addr {host}")
        except ValueError:
            pass
        if host == "169.254.169.254" or host.endswith(".internal"):
            raise PolicyDenied(f"metadata/ssrf {host}")
        if self.network_default_deny and host not in self.allowed_hosts:
            raise PolicyDenied(f"egress deny {host}")

    def check_write(self, path: str) -> None:
        norm = path.replace("\\", "/").lower()
        if any(b in norm for b in _WRITE_BLOCK):
            raise PolicyDenied(f"write-block {path}")
        if self.specialty is Specialty.DATA and not norm.startswith("/semantic/"):
            raise PolicyDenied("data agent has no host FS")


def check_sql(sql: str, dialect: str, statements_so_far: int) -> str:
    if dialect not in {"snowflake", "bigquery", "sqlite"}:
        raise PolicyDenied(f"unknown dialect {dialect}")
    if statements_so_far >= MAX_SQL_STATEMENTS:
        raise PolicyDenied("sql statement cap")
    stripped = sql.strip().rstrip(";")
    if ";" in stripped:
        raise PolicyDenied("multi-statement sql")
    if "--" in stripped or "/*" in stripped:
        raise PolicyDenied("comments not allowed in generated sql")
    if re.search(r"\bunion\b", stripped, re.I) and re.search(
        r"information_schema", stripped, re.I
    ):
        raise PolicyDenied("information_schema union")
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    if first not in _SQL_OK or _SQL_BAN.search(stripped):
        raise PolicyDenied(f"sql allowlist deny first={first}")
    return stripped


class WormLog:
    def __init__(self) -> None:
        self._chain: list[dict[str, Any]] = []
        self._prev = "0" * 16
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = json.dumps(row, sort_keys=True, default=str)
            digest = hashlib.sha256((self._prev + payload).encode()).hexdigest()[:16]
            entry = {**row, "prev": self._prev, "hash": digest, "policy": POLICY_VERSION}
            self._chain.append(entry)
            self._prev = digest
            return entry


@dataclass
class JobResult:
    specialty: Specialty
    status: str
    observation: str
    degraded: bool = False
    pii_audit: list[dict[str, str]] = field(default_factory=list)


class SpecialtyRuntime:
    def __init__(self) -> None:
        self.breakers = {
            "primary": CircuitBreaker(),
            "secondary": CircuitBreaker(),
            "warehouse": CircuitBreaker(),
            "browser_pool": CircuitBreaker(),
        }
        self.worm = WormLog()
        self._git_muts: dict[str, int] = {}
        self._lock = threading.Lock()

    def run(
        self,
        prompt: str,
        *,
        tenant: str,
        url: str | None = None,
        sql: str | None = None,
        dialect: str = "snowflake",
        path: str | None = None,
        planner: str = "primary",
    ) -> JobResult:
        cid = str(uuid.uuid4())
        job_id = f"{tenant}:{uuid.uuid4().hex[:8]}"
        log = build_logger(cid, tenant, job_id)
        redacted, pii = redact_pii(prompt)
        spec = route_specialty(redacted)
        policy = SandboxPolicy(
            spec,
            frozenset(
                {
                    "pypi.org",
                    "registry.npmjs.org",
                    "github.com",
                    "corp.example",
                    "api.search.example",
                }
            ),
        )
        log.info("routed", extra={"specialty": spec.value, "degraded": False})
        self.worm.append(
            {
                "correlation_id": cid,
                "job_id": job_id,
                "specialty": spec.value,
                "prompt_hash": hashlib.sha256(redacted.encode()).hexdigest()[:16],
                "pii_events": len(pii),
            }
        )
        try:
            obs = self._dispatch(
                spec, policy, log, job_id, url=url, sql=sql, dialect=dialect, path=path,
                planner=planner,
            )
            return JobResult(spec, "ok", obs, pii_audit=pii)
        except CircuitOpenError:
            log.warning("primary open; fallback", extra={"specialty": spec.value, "breaker": "open"})
            try:
                obs = self._dispatch(
                    spec, policy, log, job_id, url=url, sql=sql, dialect=dialect, path=path,
                    planner="secondary",
                )
                return JobResult(spec, "ok", obs, degraded=True, pii_audit=pii)
            except (CircuitOpenError, TransientError, PermanentError) as exc:
                degrade = f"needs_human:{type(exc).__name__}:{exc}"
                log.error("degraded", extra={"specialty": spec.value, "degraded": True})
                self.worm.append({"job_id": job_id, "status": "needs_human", "err": type(exc).__name__})
                return JobResult(spec, "needs_human", degrade, degraded=True, pii_audit=pii)
        except PermanentError as exc:
            log.error("permanent", extra={"specialty": spec.value})
            raise exc

    def _dispatch(
        self,
        spec: Specialty,
        policy: SandboxPolicy,
        log: CorrelationAdapter,
        job_id: str,
        *,
        url: str | None,
        sql: str | None,
        dialect: str,
        path: str | None,
        planner: str,
    ) -> str:
        if spec is Specialty.CODING:
            return self._coding(policy, log, job_id, path or "src/app.py", planner)
        if spec is Specialty.BROWSER:
            return self._browser(policy, log, url or "https://corp.example/app", planner)
        if spec is Specialty.RESEARCH:
            return self._research(policy, log, planner)
        return self._data(policy, log, sql or "SELECT 1", dialect, planner)

    def _call_planner(self, planner: str, payload: dict[str, Any]) -> dict[str, Any]:
        br = self.breakers[planner if planner in self.breakers else "primary"]
        br.allow()

        def _once() -> dict[str, Any]:
            if payload.get("force_transient"):
                raise TransientError("429")
            if planner == "primary" and payload.get("fail_primary"):
                raise TransientError("primary down")
            return {"ok": True, "planner": planner, **payload}

        try:
            out = retry_call(_once)
            br.record_success()
            return out
        except (TransientError, CircuitOpenError):
            br.record_failure()
            raise

    def _coding(
        self, policy: SandboxPolicy, log: CorrelationAdapter, job_id: str, path: str, planner: str
    ) -> str:
        policy.check_write(path)
        with self._lock:
            n = self._git_muts.get(job_id, 0) + 1
            if n > MAX_GIT_MUTATIONS or n > MAX_TURNS:
                raise PermanentError("git/turn cap")
            self._git_muts[job_id] = n
        self._call_planner(planner, {"aci": "test", "oracle": "FAIL_TO_PASS"})
        log.info("coding oracle ci", extra={"specialty": "coding", "breaker": self.breakers[planner].state.value})
        return "ci_green:FAIL_TO_PASS+PASS_TO_PASS"

    def _browser(
        self, policy: SandboxPolicy, log: CorrelationAdapter, url: str, planner: str
    ) -> str:
        self.breakers["browser_pool"].allow()
        policy.check_net(url)
        self._call_planner(planner, {"channel": "a11y", "steps": 1})
        self.breakers["browser_pool"].record_success()
        log.info("browser asserted", extra={"specialty": "browser"})
        return f"end_state_ok:{urlparse(url).hostname}"

    def _research(self, policy: SandboxPolicy, log: CorrelationAdapter, planner: str) -> str:
        policy.check_net("https://api.search.example/v1")
        self._call_planner(planner, {"subs": 3, "cap": MAX_SUBAGENTS})
        cites = [{"url": "https://api.search.example/src/1", "fetched_at": int(time.time())}]
        log.info("citation pass", extra={"specialty": "research"})
        return json.dumps({"subs": 3, "citations": cites, "cap": MAX_SUBAGENTS})

    def _data(
        self, policy: SandboxPolicy, log: CorrelationAdapter, sql: str, dialect: str, planner: str
    ) -> str:
        self.breakers["warehouse"].allow()
        checked = check_sql(sql, dialect, 0)
        policy.check_write("/semantic/metrics.yaml")
        self._call_planner(planner, {"sql": checked, "timeout_s": SQL_TIMEOUT_S, "rls": "user"})
        self.breakers["warehouse"].record_success()
        log.info("sql executed rls", extra={"specialty": "data"})
        return "rows=[]:rls_empty_success"


def _demo() -> None:
    rt = SpecialtyRuntime()
    coding = rt.run("open a PR after pytest on the refactor", tenant="acme")
    assert coding.status == "ok" and coding.specialty is Specialty.CODING
    try:
        rt.run("refactor hooks", tenant="acme", path=".git/hooks/pre-commit")
        raise SystemExit("hooks write should deny")
    except PolicyDenied:
        pass
    browser = rt.run("browse the internal DOM app", tenant="acme", url="https://corp.example/app")
    assert browser.status == "ok"
    try:
        rt.run("browse metadata", tenant="acme", url="http://169.254.169.254/latest")
        raise SystemExit("ssrf should deny")
    except PolicyDenied:
        pass
    research = rt.run("cite competitor brief for the board", tenant="acme")
    assert "citations" in research.observation
    data = rt.run(
        "what is net revenue in the warehouse?",
        tenant="acme",
        sql="SELECT metric FROM trusted_revenue WHERE as_of = '2026-08-01'",
    )
    assert "rls_empty_success" in data.observation
    try:
        check_sql("COPY INTO @evil FROM t", "snowflake", 0)
        raise SystemExit("copy should deny")
    except PolicyDenied:
        pass
    pii_job = rt.run("pytest contact jane@example.com about the PR", tenant="acme")
    assert pii_job.pii_audit and pii_job.pii_audit[0]["type"] == "email"
    rt.breakers["primary"].record_failure()
    rt.breakers["primary"].record_failure()
    rt.breakers["primary"].record_failure()
    assert rt.breakers["primary"].state is BreakerState.OPEN
    degraded = rt.run("open a PR after pytest", tenant="acme", planner="primary")
    assert degraded.degraded and degraded.status in {"ok", "needs_human"}
    print(json.dumps({"worm": len(rt.worm._chain), "degraded": degraded.status}, indent=2))


if __name__ == "__main__":
    _demo()
```

Invariants enforced in the stub: deny-beats-allow egress; metadata and RFC1918 blocked; `.git/hooks` write-block; SQL first-token allowlist + no `UNION INFORMATION_SCHEMA`; RLS-empty is `ok`; primary open → secondary → `needs_human`; WORM hash chain on every route.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rule: **specialty = runtime + oracle + identity**. Do not run a research DAG on a git loop. Do not point a pixel agent at corporate IdP. Do not give a data agent a service account that bypasses RLS.

### Scenario 1 — PR factory for a 400-dev org

**Problem statement.** 1k `agent-eligible` tickets/month. Mix of localize+patch (Agentless-shaped) and multi-file refactors. Must not merge. Org wants Copilot cloud **or** Claude Code Action on a dedicated runner. Threats: fork-PR secret theft, unsandboxed `npm install`, runaway git loops, MCP bypassing the Copilot firewall. Cost ceiling: if tickets are Agentless-shaped, **[inferred] ~$700–$3k** LLM/month (2024 $0.70 SKU vs Sonnet 5 40-turn **~$3k/1k**); if they are 80-turn Opus, **[inferred] ~$24k** LLM before CI. p99 is the Actions **6 h** timeout, not the model. A PM wants Magentic-style subagents “because Anthropic +90.2%.”

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ GitHub     │ evt │ CONTROL PLANE                                             │
│ issue/PR   │────▶│ Gateway: org SSO, correlation-id, admission=1 VM/task     │
│ @agent     │     │ Policy: PII redact; treat issue body as untrusted         │
└────────────┘     │ Router: Agentless pipeline if localize+test; else ACI     │
                   │ Budget: max_turns=40; git muts≤20; 45 min kill            │
                   │ Orchestrator: Temporal wf id=tenant:pr; Kafka outbox      │
                   │ HITL: required reviewers + CODEOWNERS; agent cannot merge │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ clone / ACI                  │ MCP git/gh + tests
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ CODING SANDBOX   │        │ TOOL PROXIES                 │
                   │ Copilot appliance│        │ pytest oracle; Artifactory   │
                   │ or Claude bwrap  │        │ no metadata; strictAllowlist │
                   │ firewall = rec.  │        │ MCP host RBAC (firewall ≠    │
                   │ list + internal  │        │ MCP); secrets withheld forks │
                   │ registry; no     │        │                              │
                   │ cloud metadata   │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Durable object = PR + CI node-ids; WORM: patch hash,      │
                   │  policy, breaker; RPO=last push; RTO=new VM from branch   │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Uncapped Opus ReAct on prod creds / Magentic research DAG | B. Recommended: Agentless-when-local + ACI cap 40 + cloud VM firewall + PR/CI oracle | C. Laptop-only Cursor/Claude CLI, Run Everything, repo `.claude` allowlist |
| --- | --- | --- | --- |
| Cost | **[inferred] ~$24k/1k** 80-turn Opus; 15× research tokens wasted on non-parallel git | Agentless **[inferred] ~$700/1k** (stale) to SWE-agent Sonnet **~$3k/1k**; cap prevents Opus blowup | Cache-friendly but no parallelism; SWE-agent vs RAG still **8–13×** if you agent-loop anyway |
| Latency | p99 = 6 h Actions or infinite edit loop | p50 tests-bound; p95 **45 min** kill → `needs_human`; p99 bounded by kill not model | HITL every egress; p95 is the human |
| Ops | Rainbow-deploy forks in-flight; worktree collisions | 1 VM/task; Temporal resume; node-id logs; dedicated runner image | N developers, N Seatbelt policies; Linux UID 0 surprises |
| Security | MCP unfiltered on Copilot; fork secrets; hook persistence | Org-locked firewall; `strictAllowlist` in **managed** settings; fork secret withhold; hooks write-block | Auto-review **not** a security boundary; repo settings cannot set Claude `strictAllowlist` |
| Scalability | One noisy repo; retry storms | Horizontal VMs; admission on VM pool; do not share a worktree | Scales with laptops, not with 1k tickets |

**Decision rationale.** **B** matches the research PR-factory topology: queue → dedicated sandbox → tests in-job → PR → humans merge; cap turns at 40; escalate. A copies a research DAG onto git (Anthropic: coding is a poor fit) and pays Opus 80-turn economics. C is the right threat model for secrets-on-laptop, wrong throughput model for 400 developers. Interview close: “Oracle is CI on the agent’s branch. The PR is the saga log. MCP RBAC is a separate control from the Actions firewall.”

### Scenario 2 — Self-serve BI (data agent) with a research-desk anti-pattern nearby

**Problem statement.** Finance wants “ChatGPT for the warehouse”: 20 curated tables, 15 board-pack metrics, row-level tenancy. Databricks Genie or Snowflake Cortex Analyst on the table. Temptations: dump `information_schema` (BIRD GPT-4 **54.89%** EX vs human **92.96%**; Spider 2.0 **10.1%** vs Spider 1.0 **86.6%**); shared service principal “so the agent can see everything”; Agent-mode fan-out on the same XS warehouse as chat; notebooks with `!pip` plus warehouse creds; pointing CUA at the BI SPA with an SSO-admin cookie; using Anthropic 15× multi-agent (**[inferred] ~$7.8k/1k**) to “research the revenue number.” Cost: message credits + warehouse — set a Genie budget so Agent-mode cannot become ETL. Chat timeout **60 s**; Agent mode **300 s**.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Analyst UI │ SSE │ CONTROL PLANE                                             │
│ / Slack    │────▶│ Gateway: SSO = end-user UC/Snowflake role; $ / bytes cap  │
└────────────┘     │ Policy: PII redact; synthetic knowledge-store samples     │
                   │ Router: trusted-asset if metric named; else read-only SQL │
                   │          NEVER research-wave; NEVER pixel agent on IdP    │
                   │ Budget: ≤6 SQL; Inspect on for finance; Agent-mode        │
                   │         on a separate warehouse                           │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ semantic version             │ run_sql / trusted asset
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA SESSION     │        │ TOOL PROXIES                 │
                   │ semantic views   │        │ dialect=snowflake|bigquery   │
                   │ compute=author   │        │ allowlist SELECT/WITH/EXPLAIN│
                   │ data=user RLS    │        │ maximumBytesBilled dry-run   │
                   │ empty=success    │        │ no COPY/PUT/CREATE; no MCP   │
                   │ notebook TTL off │        │ mega-server; PrivateLink     │
                   │ the chat path    │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Query history attributed to user; semantic-model version; │
                   │ WORM: sql hash, rls_empty, bytes; RPO=history; RTO=new    │
                   │ session (no shared kernel)                                │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Shared service account + `information_schema` dump + free-form `run_sql` | B. Recommended: semantic views + dual credentials + SQL allowlist + trusted assets + split warehouse | C. Deep-research multi-agent or CUA pixels on the BI SPA (SSO cookie) |
| --- | --- | --- | --- |
| Cost | LLM cheap; **FinOps incident** on 33 GB BIRD-class `SELECT *`; retry-on-timeout doubles scans | Tokens second-order; warehouse-seconds first-order; Genie budget; Inspect = N extra queries on purpose | Research **[inferred] ~$2k–$7.8k/1k** briefs; CUA **~$1.3/task + VM** to click a dashboard you already own |
| Latency | Cartesian join until platform 10 min–6 h default | p50 warm warehouse **[inferred] 3–15 s**; p95 Inspect; p99 = 60 s chat / 300 s Agent kill | 5–30 min Deep Research or 40 screenshot turns — wrong envelope for “net revenue” |
| Ops | Dialect drift (Spider 2.0 BQ/Snowflake/SQLite) until job timeout | Pin semantic version; regression benches **eval-only**; auto-suspend | Sync research waves cannot steer; browser 5 s ping on a BI session |
| Security | CoT + cache leak of RLS rows; YAML-on-stage readable without SELECT | Empty unauthorized; GRANT on views; no host FS; synthetic samples | Screenshot PII of finance; page-injection; javascript_exec RCE; IdP cookie in a vendor or pixel log |
| Scalability | Author warehouse noisy-neighbor | Chat vs Agent-mode warehouses; bytes fuse admission | Subagent fan-out and browser pools do not help a semantic layer |

**Decision rationale.** **B** is the Genie/Cortex pattern in the research: curated semantic layer, trusted assets for the 15 board questions, RLS on tables, compute ≠ data identity, timeouts in depth, Inspect on for finance. A is the wrong pattern the research names (filter in the LLM). C applies research/browser topologies to a data question — expensive, slow, and an identity incident. Notebook path, if required, is a **coding sandbox plus warehouse RLS**, no internet in the kernel. Interview close: “BIRD 75% is not safe for revenue. Empty RLS is success. Budget warehouse-seconds, not just tokens.”

---

*End of module. Six sections. Four specialties (coding, browser, research, data). Token `$ / 1k` tables are **[inferred]** from published SKUs × stated reference loops dated 2026-08-21. No unpublished specialty e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented duration envelopes (Deep Research 5–30 min / Gemini 60 min / Actions 6 h / Playwright 5 s ping).*
