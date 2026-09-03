# Module 14: Deep Agents Ecosystem (ACP / A2A / Code as I/O adapters)

**Study + interview prep.** Grounded in research dated 2026-09-02 (46 sources). Harness pin **`deepagents==0.7.12`** (sibling from 08; Beta; Python `>=3.11,<4.0`; MIT). Product pins: **`deepagents-code==0.1.65`** (Beta; Python `>=3.12,<4.0`; **pins `deepagents==0.7.10`** — one patch behind the SDK); **`deepagents-acp==0.0.11`** (Alpha; `agent-client-protocol>=0.10.1`; `deepagents` unpinned). GitHub `langchain-ai/deepagents` **28,843** stars at fetch; MIT. Official comparison with Claude Agent SDK drafted **2026-04-16** — revisit before treating tenancy/managed-agents claims as current. `$ per 1k` for Code/ACP/A2A sessions is **[inferred]**; LangChain publishes **no** ACP/A2A/Code p50/p95/p99. Missing percentiles are architecture-derived **[inferred] policy targets** and are marked. Do not cite them as vendor SLOs.

**Thesis:** Code, ACP, and A2A are **I/O adapters around `create_deep_agent`**, not new runtimes. The assembler is still `create_deep_agent` → `create_agent` → LangGraph `CompiledStateGraph`. A2A is implemented by **Agent Server** (`POST /a2a/{assistant_id}`), not a `deepagents-a2a` package. Claude Agent SDK is **agent-in-sandbox** (loop inside the guest); Deep Agents Code is **sandbox-as-tool** (loop on the host, `read_file`/`write_file`/`execute` over the network). OpenWiki is a **separate CLI** that writes Markdown other agents read — not middleware. RAG on Deep Agents is retrieve → dump paths onto VFS → subagent `read_file`, **not** a new retriever.

Harness internals (`HarnessProfile`, middleware DAG) live in [08-deep-agents-harness](08-deep-agents-harness.md). Execution backends / `LocalShellBackend` live in [09-deep-agents-execution](09-deep-agents-execution.md). OpenWiki load policy lives in [10-deep-agents-context](10-deep-agents-context.md). RAG chunking/ANN live in [01-rag](01-rag.md). Agent Server leases/crons/MDA live in [13-deep-agents-production](13-deep-agents-production.md). This file owns the **surfaces**.

**Naming collision (do not mix):** LangChain “ACP” is the **Agent Client Protocol** (editor ↔ coding agent, Zed-origin, JSON-RPC over stdio). IBM’s older “Agent Communication Protocol” was folded into Linux Foundation **A2A**. Secondary roundups that say “ACP merged into A2A” mean IBM ACP, not `deepagents-acp`.

| Pin | Why |
| --- | --- |
| Same graph on every surface | `dcode` TUI / `-n` / `--acp`, `AgentServerACP`, Agent Server A2A, MDA — all consume `create_deep_agent` |
| `deepagents-code` 0.1.65 → `deepagents==0.7.10` | New harness behavior in 0.7.11+ may be missing in `dcode` until bump |
| ACP protocol **integer `1`** | Wire `protocolVersion` in `initialize`, not the Rust crate number. v2 is a draft — keep serving v1 peers |
| A2A `contextId` **= `thread_id` UUID** | `session-42` → `-32602` “Failed to create run: Invalid thread ID”. UUID ≠ authorization |
| Never `LocalShellBackend` on untrusted trees | ACP demo uses it against editor `cwd`. Code docs: remote sandbox for untrusted repos |

---

## What Is This?

**The harness is one compiled graph. The ecosystem is how humans and other agents talk to it.**

`create_deep_agent` still returns a LangGraph `CompiledStateGraph`. Products and protocols wrap that object:

| Surface | Package / endpoint | What it is |
| --- | --- | --- |
| **SDK** | `deepagents` `create_deep_agent` | Control: construction. Data: in-process `invoke`/`stream` |
| **Code** | `deepagents-code` / `dcode` | TUI, headless `-n`, or `--acp`. Optional **remote sandbox as a tool** |
| **ACP** | `deepagents-acp` `AgentServerACP` + `dcode --acp` | Editor launches a **stdio** subprocess; JSON-RPC `session/*` |
| **A2A** | Agent Server `POST /a2a/{assistant_id}` | JSON-RPC tasks between deployments / vendors. Enable via Assistants `metadata.a2a` |
| **MCP ingress** | Agent Server `POST /mcp` | Expose **this** agent as a **tool** (stateless per request) |
| **OpenWiki** | `openwiki` npm CLI (Node 22+) | Out-of-band authoring of `openwiki/*.md` on disk |
| **RAG tutorial** | Your `@tool` + VFS | Vector search writes **paths** under `/retrieved/`; same graph |

LangChain’s product taxonomy is three **stacked** layers, not competing products: **runtime** (LangGraph, Temporal, Inngest) → **framework** (`create_agent`, CrewAI, ADK, …) → **harness** (Deep Agents SDK, **Claude Agent SDK**, Manus, coding CLIs). `deepagents` is “a standalone library built on top of LangChain’s core building blocks… It uses the LangGraph runtime.”

Dependency direction in the monorepo (`libs/deepagents`, `libs/code`, `libs/acp`, `libs/evals`, `libs/talon`, `libs/partners`, verified 2026-08-26): `code` / `acp` / `evals` / `talon` **consume** the SDK; `partners` **supply** sandbox backends. `create_deep_agent` still attaches `recursion_limit: 9_999` and `ls_integration` metadata after compile.

Think of a restaurant pass. **The graph is the recipe** (already written in 08). **ACP is the ticket printer in the dining room** (Zed / JetBrains / VS Code). **A2A is the phone to the kitchen next door.** **`dcode` is the chef’s own station** — same recipe, optional prep room (remote sandbox) down the hall. You do not invent a second stove because you bought a phone.

## Why It Matters

Almost every “how do we ship a coding agent / multi-agent fleet?” interview now forks here. Trap answers: “ACP is a new runtime,” “A2A is Deep Agents subagents,” “`dcode` is Claude Code with a LangChain skin,” “put the loop inside the LangSmith sandbox like Claude Agent SDK,” “dump the RAG index into the parent prompt,” “OpenWiki is a second system prompt,” “editor ACP is our Zero-Trust PEP,” “`contextId` can be `session-42`.”

The comparison that interviews quote was drafted **2026-04-16**. Two sandbox patterns are the fork: Claude Agent SDK runs the **loop inside a sandbox**; Deep Agents Code runs the **loop on the laptop or long-lived container** and treats the sandbox as `read_file` / `write_file` / `execute`. A2A `contextId` **is** `thread_id` and needs the same `@auth` as `/runs`. RAG on this stack means **retrieve → VFS → chunk-analysts**, not a new index inside `deepagents`. PyPI gravity from 08 (**5,646,660** last-month downloads for `deepagents`) is adoption, not your cluster; `deepagents-acp` download volume was unpublished (PyPI stats rate-limited at research time).

---

### 1. System Topology & Data Flow

Control plane is construction + session/task identity (LLM-free). Data plane is the same ReAct loop on workers or in the editor subprocess. Persistence is whichever checkpointer that **surface** bound — MemorySaver in the ACP demo, `~/.deepagents/.state/` in `dcode` TUI, Postgres on Agent Server. Tool proxies are FS/`task`/`execute` plus MCP **egress**; ACP and A2A are **ingress** adapters, not PDPs. Telemetry is LangSmith (`deepagents-code` default project; isolate with `DEEPAGENTS_CODE_LANGSMITH_PROJECT`).

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  LangSmith: ls_integration=deepagents ; stream.subagents         │
         │  dcode: project deepagents-code (override DEEPAGENTS_CODE_*)     │
         │  /cost via genai-prices ; /tokens /context /context-doctor       │
         │  A2A: OTel langsmith.metadata.thread_id ← contextId (UUID)       │
         │  ACP: editor ACP logs (Zed “dev: open acp logs”) — PII sink      │
         │  WORM audit: (cid, contextId=thread_id, surface, arg_digest)     │
         │  detect→redact→audit BEFORE editor logs / A2A payloads / traces  │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ /cost + tokens    │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (construction + identity — LLM-free; ACP/A2A are I/O)      │
│                                                                           │
│  create_deep_agent(...) → CompiledStateGraph  recursion_limit 9_999       │
│  dcode: managed_config.toml > DEEPAGENTS_CODE_* > ~/.deepagents/config.toml│
│  ACP: editor initialize(protocolVersion=1) → session/new (cwd, model, mode)│
│  A2A: Assistants metadata.a2a ; card GET /.well-known/agent-card.json     │
│  MCP ingress: POST /mcp (stateless). A2A: POST /a2a/{id} (stateful)       │
│  @auth.authenticate on /runs AND A2A/MCP — without it, API-key owner only │
│  OpenWiki: out-of-band CLI; does NOT compile the graph                    │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ same CompiledStateGraph
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted token stream — adapters serialize; graph disposes) │
│                                                                           │
│  IDE ACP / A2A JSON-RPC / dcode CLI / SDK invoke  →  SAME graph loop      │
│                                                                           │
│  ┌────────────── TOOL PROXIES (least privilege — ACP is NOT a PEP) ─────┐ │
│  │ FS / task / execute (sandbox protocol) / eval (QuickJS)              │ │
│  │ MCP EGRESS: gateway PEP still required (permissions= ≠ MCP)          │ │
│  │ dcode --sandbox {langsmith,daytona,modal,...}: sandbox-as-tool       │ │
│  │ ACP demo: LocalShellBackend on editor cwd — host blast radius        │ │
│  │ A2A_ALLOWED_TOOL_CALL_RESULTS: which tool results become DataParts   │ │
│  │ RAG @tool writes /retrieved/{id}/chunk_i.md — YOUR index, not DA     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (surface chooses the saver — not a new store type)     │
│                                                                           │
│  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌──────────────┐  │
│  │ ACP demo       │ │ dcode TUI      │ │ Agent Server │ │ OpenWiki     │  │
│  │ MemorySaver()  │ │ ~/.deepagents/ │ │ Postgres     │ │ openwiki/*.md│  │
│  │ RAM; dies with │ │ .state/ threads│ │ contextId=   │ │ + .claims/   │  │
│  │ stdio subprocess│ │ file memory    │ │ thread_id    │ │ git-resident │  │
│  └────────────────┘ └────────────────┘ └──────────────┘ └──────────────┘  │
│  InMemory / MemorySaver RPO = empty on restart. thread_id < 255 chars.    │
│  MDA: LangSmith injects Postgres. Do not pass checkpointer= on LSD.       │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control (construct)** | `create_deep_agent` kwargs; `dcode` config precedence; ACP capabilities; A2A `metadata.a2a`; `disable_a2a` / `disable_mcp` | Yes | Treating ACP `session_id` as `thread_id` without a mapping |
| **Control (identity)** | ACP `cwd`/`model`/`mode`; A2A `contextId` UUID; `@auth`; `DEEPAGENTS_HOME` | Yes | `contextId` as authorization; editor as PEP |
| **Data (adapters)** | stdio JSON-RPC; A2A JSON-RPC; TUI keystrokes; `-n` stdin ≤ **10 MiB** | Serialize only | Logging raw ACP frames / A2A history |
| **Data (graph)** | Same loop as 08 — model proposes, tools/VFS dispose | No | Nested Claude SDK `query()` **and** Deep Agents `execute` on one repo |
| **Persistence** | MemorySaver vs `~/.deepagents/.state/` vs Postgres | Bytes of untrusted content | Shipping ACP demo MemorySaver as prod resume |

**Three envelopes, one harness** (MDA vs Code vs ACP):

| Envelope | Who runs the loop | Who sees the UX | Persistence |
| --- | --- | --- | --- |
| **MDA** | LangSmith Agent Server | Channels (Slack), Studio, API | Injected Postgres |
| **`dcode` TUI / `-n`** | Engineer laptop or CI runner | Terminal | `~/.deepagents/.state/` + files |
| **ACP** | Editor-spawned stdio process | Zed / JetBrains / VS Code panel | Demo: MemorySaver; production: **you** attach a real checkpointer |

Shipping all three against **one** `create_deep_agent` graph is the intended composition. Shipping three **different** harnesses is the dual-harness failure in §4 / Common Failure Modes.

**Protocol triangle (stackable):**

| Protocol | Direction | Attachment |
| --- | --- | --- |
| **MCP** | Agent ↔ **tools/data** | Egress: `tools=` from MCP servers. Ingress: `/mcp` exposes the graph as a **tool** (stateless) |
| **ACP** | **Editor** ↔ coding agent | `deepagents-acp` / `dcode --acp` stdio |
| **A2A** | **Agent** ↔ agent | Agent Server `/a2a/{assistant_id}` (stateful via `contextId`) |

You can stack all three: Zed ACP session → Deep Agent → MCP tools; a second fleet agent calls the same graph over A2A. Mixing MCP ingress with A2A is a common design error: MCP = “this agent is a tool”; A2A = “this agent is a conversational peer.” Outbound sibling (not a protocol product): Agent Server **webhooks** POST the run payload on completion when the peer does not speak A2A.

**Request-flow narrative (IDE ACP / A2A JSON-RPC / `dcode` CLI → same graph):**

1. **Control / construction (once).** App or `dcode` calls `create_deep_agent` (08 stack: backend, middleware, GP, `recursion_limit: 9_999`). ACP may use a **factory** `AgentSessionContext(cwd, model, mode) → compiled graph` so Zed session selectors work. `dcode --acp` without factory+`modes`/`models` does not call `_build_config_options()` — GitHub **#4254**, selectors missing.
2. **Admit.**
   - **ACP:** Editor spawns stdio subprocess → `initialize` (integer `protocolVersion`; Client SHOULD close if Agent’s version is unsupported) → `session/new` → `session/prompt`. Default user-visible text is **Markdown**; protocol reuses MCP JSON and adds **diffs**.
   - **A2A:** Client `GET /.well-known/agent-card.json?assistant_id=...` (docs also show `GET /a2a/{id}/.well-known/agent-card.json`) → `POST` `SendMessage` / `message/send`. Omit `contextId` on first message; server mints a UUID; echo it forever. New user turn = new `taskId`. Do **not** resend a completed `taskId` (`-32004`). Foreign `taskId` → `-32001`. Client `metadata.thread_id` is **ignored**.
   - **`dcode`:** TUI (`/threads` resume), `-n "..."` (fresh thread; shell **disabled** unless `-S`), or `--acp`. CI: `--max-turns` and/or `--timeout SECONDS` → exit **124**; without `-n`/stdin → exit **2**.
3. **Data plane.** Same LangGraph loop as 08. ACP/A2A/Code only change **how bytes arrive**. `dcode --sandbox langsmith` (etc.): loop stays on the host; FS/`execute` target the guest working dir (`/root`, `/tmp`, `/home/daytona`, `/workspace`, `/home/user`, `/vercel/sandbox` — provider table in 09/Code docs).
4. **Tool proxy.** Built-in FS + `task` + optional sandbox `execute`. MCP egress still goes through **your** gateway (ACP did not become a PEP). A2A may publish tool results as `DataPart`s unless `A2A_ALLOWED_TOOL_CALL_RESULTS` allowlists names. RAG: `search_documentation` → `similarity_search(k=4)` → `backend.upload_files()` under `/retrieved/{8-hex}/chunk_{i}.md` → parent sees **paths** → up to **3** `chunk-analyst` `task()` calls.
5. **Persistence.** ACP demo: `MemorySaver()` — **dies with the subprocess**. `dcode` interactive: profile `.state/`. A2A: `contextId` **is** `thread_id`, so Agent Server checkpoint/lease/HITL apply as on `/runs` (13). HITL can sleep across process boundaries; A2A clients must tolerate long `WORKING`.
6. **Observe.** `dcode /cost` (docs example **$1.03** = $0.87 assistant + $0.16 subagents on `anthropic:claude-sonnet-4-5`). `/context-doctor` example unattributed delta **+2,987** tokens. A2A default `historyScope=context` replays **whole context** (including tool results) on `SendMessage`/`GetTask`/`ListTasks`; set `historyScope=task` (body or `LangGraph-A2A-History-Scope`). Caps: `historyLength` **max 10** (`-32602` if larger). Streaming **ignores** both with **no error**. Mis-cased `historyscope` is silently ignored.
7. **Stop.** Model stops; ACP `session/cancel` (bug **#5084**: process-wide `_cancelled` flag — cancelling A cancelled B); A2A `CancelTask`; `dcode` budget 124; `GraphRecursionError` at 9,999.

**Claude Agent SDK vs this topology (sandbox patterns):**

```
Claude Agent SDK / “agent-in-sandbox”
  [sandbox]
    LLM loop + tools + local FS
    API keys typically in the guest
    query() spawns a claude CLI subprocess over stdio
    N concurrent sessions ⇒ N subprocesses (isolate cwd + CLAUDE_CONFIG_DIR)

Deep Agents Code / “sandbox-as-tool”
  [laptop or long-lived container]  LLM loop, memory, tool dispatch
           | network
  [remote sandbox]  read_file / write_file / execute
    LangSmith auth proxy injects headers outside the guest
```

Deep Agents SDK supports **both** patterns. `dcode --sandbox` documents the second as the product default. Anthropic hosting later added Managed Agents (hosted loop + sandbox, April 2026) as a **separate** product from the SDK — SDK code does not deploy onto it. LangSmith **Managed Deep Agents (MDA)** is also a **deployment SKU**, not an editor protocol: upload `agent.py` + `instructions.md` + `skills/` + `tools/` + `sandbox/`; identity file `identity.py`.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (adapters, not schedulers)

**I1.** ACP, A2A, and Code introduce **no** new runtime. Durable execution, streaming, interrupts, checkpoints = LangGraph / Agent Server. The loop shape is still `create_agent`’s ReAct-style graph.

**I2.** A2A is **not** the `task` tool. Subagents are in-process child graphs with a fresh context and a single handoff. A2A is a **network protocol** between deployments / vendors.

**I3.** `contextId` **is** `thread_id` and **must be a UUID**. Agent Server does not read top-level JSON-RPC `metadata`. Non-LangGraph peers must copy `params.message.contextId` onto `langsmith.metadata.thread_id` via OTel or the Threads view splits.

**I4.** ACP **session_id** ≠ LangGraph `thread_id` unless you map them. Demo checkpointer is MemorySaver. Production resume = Postgres (or `dcode` `.state/` for the Code product only).

**I5.** OpenWiki does **not** inject wiki pages into the system prompt. Progressive disclosure is the agent’s `read_file` after `AGENTS.md` / `CLAUDE.md` pointers (10 §1.11).

**I6.** Deep Agents RAG does **not** ship an index. The retrieval tool is **your** `@tool` + vector store. Baseline in the tutorial is `create_deep_agent(..., tools=[])` — no retrieval ⇒ generic/outdated answers.

#### 2.2 ACP v1 (Agent Client Protocol)

ACP “standardizes communication between coding agents and code editors or IDEs,” analogous to LSP. **ACP is for agent–editor.** If the agent must call tools hosted by external servers, use **MCP**.

| Axis | Fact |
| --- | --- |
| Transport | Local agents = editor subprocesses, JSON-RPC over **stdio**. Remote HTTP/WebSocket = “work in progress” |
| Version | Stable protocol version **`1`**. Integer `protocolVersion` in `initialize`. **v2** = consolidation draft (`schema/v2/schema.json`) — still labeled draft |
| Session methods (when advertised) | `session/new`, `session/list`, `session/resume`, `session/close`, `session/prompt`, `session/cancel`, `session/update`. Omitted/null session capability ⇒ Agent does not support session methods |
| Version rule | Single integer major; increments only on breaking changes. Agent replies with a version the Client does not support → Client **SHOULD close** |
| UX | User lives in the editor. Default text = Markdown (no HTML requirement). Diffs are first-class |
| Clients named by LangChain | Zed, JetBrains, VS Code (vscode-acp), Neovim plugins. Upstream also: Toad, Emacs, Obsidian, Cursor/Windsurf, “LangChain/LangGraph through Deep Agents ACP” |
| Demo agent | Filesystem + shell; `MemorySaver()`; `CompositeBackend(default=LocalShellBackend, routes={"/memories/": StateBackend, "/conversation_history/": StateBackend})` + `interrupt_on` from session mode |
| Modes | Python reference (older): `ask_before_edits` / `auto`. GitHub demo: `ask_before_edits`, `accept_edits`, `accept_everything` |

`deepagents-acp` starts stdio via `acp.run_agent(AgentServerACP(agent))`. `dcode --acp` runs the coding product as that server instead of the TUI. Zed wiring: clone repo, `uv sync --all-groups` in `libs/acp`, `chmod +x run_demo_agent.sh`, `settings.json` `agent_servers.DeepAgents.command` = absolute path. Toad: `uv tool install -U batrachian-toad` then `toad acp "python path/to/your_server.py" .`.

**Complexity [architecture]:** one ACP prompt is one graph invoke plus JSON-RPC serialize. Extra tokens vs in-process: editor project context **in addition to** `AGENTS.md` **[inferred]**. Concurrent sessions on one `AgentServerACP` are a supported shape; **#5084** made cancel process-wide — treat as a resilience defect until the scoped-set fix lands.

#### 2.3 A2A JSON-RPC (Agent Server)

A2A is Google’s (now Linux Foundation) protocol. Every LangSmith Deployment auto-exposes MCP + A2A so an orchestrator in deployment A can call workers in B without a private HTTP schema. Disable: `langgraph.json` → `"http": { "disable_a2a": true }` (sibling `disable_mcp`).

**Wire:** Speaks A2A **v1.0 JSON-RPC**; also accepts **v0.3 method names**. Agent card `supportedInterfaces[]`: `protocolBinding: "JSONRPC"`, `protocolVersion: "1.0"`. **Only JSON-RPC.** gRPC and HTTP+JSON are **not** implemented. Method-name family selects enum case: v1.0 → `TASK_STATE_WORKING` / `ROLE_AGENT`; v0.3 → `working` / `agent`. Pick **one** family per client.

Envelope: `SendMessage` wraps the task in `result.task`; `GetTask` and all v0.3 methods return the task on `result`; `ListTasks` → `result.tasks`.

| v1.0 | v0.3 | Status |
| --- | --- | --- |
| `SendMessage` | `message/send` | Yes |
| `SendStreamingMessage` | `message/stream` | Yes — SSE. First event is the `Task` |
| `GetTask` | `tasks/get` | Yes |
| `CancelTask` | `tasks/cancel` | Yes |
| `ListTasks` | — | Yes |
| `GetExtendedAgentCard` | — | Yes, **v1.0 name only** |
| `SubscribeToTask` | — | **Not yet** → `-32601` |
| `*TaskPushNotificationConfig` | — | **Not yet** → `-32601` |

Exactly four v0.3 names: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. `agent/getAuthenticatedExtendedCard` and `tasks/resubscribe` → `-32601`.

**Identity mapping:**

| A2A | LangGraph / LangSmith |
| --- | --- |
| `contextId` | **`thread_id`**. UUID. Server mints on first message; echo it |
| `taskId` | One **run** inside the thread. New user turn = new task |
| Client `metadata.thread_id` | **Ignored** |

Graph requirement: state must include a `messages` key or the assistant is rejected.

**Optional `metadata.a2a`** (patch Assistants API **after** deploy; `langgraph.json` cannot set assistant metadata): `input_modes` / `output_modes` are advertisement only — undeclared modes still accepted; empty list rejected. `a2ui: true` → A2UI **v0.9** (`langgraph-api>=0.15.0`; docs: not a stable release yet — check `capabilities.extensions`). File parts: inbound image/audio/video/file → LangChain blocks (`>=0.12.0` inbound / `>=0.13.0` outbound). `A2A_ALLOWED_TOOL_CALL_RESULTS`: unset = **all** tool results published as `DataPart`.

**Version gates (`langgraph-api`):** A2A endpoint `>=0.4.21`; inbound FilePart `0.12.0`; tool-result DataParts `0.12.2`; allowlist `0.12.4`; outbound FilePart + card modes `0.13.0`; A2UI + `historyScope` `0.15.0`.

**Error codes used in Agent Server docs:**

| Code | When |
| --- | --- |
| `-32601` | Method not found (SubscribeToTask, push-notification config, extra v0.3 names) |
| `-32602` | Bad params: `historyLength > 10`, bad `historyScope`, non-UUID `contextId` |
| `-32001` | `taskId` minted by another agent |
| `-32004` | Message names a **terminal** task |
| (spec, **not** implemented as specified) | `-32009` unsupported `A2A-Version` — header **ignored**; `-32003` push config — returns `-32601` instead |

A2A spec: JSON-RPC 2.0 over HTTPS plus gRPC and HTTP+JSON (Agent Server implements JSON-RPC only). Auth via OpenAPI-shaped `securitySchemes` / `security`; credentials in **HTTP headers**, never in the JSON-RPC body. Webhook timeout recommendation **10–30 s** — Agent Server **does not implement** push-notification config yet.

**TCK** (`a2aproject/a2a-tck`): CI against a checked-in known-failure list. Gaps: wire still v0.3 (`kind`/`mimeType`); streaming events flat; `tool_results` snake_case; timestamps `+00:00` not `Z`; SubscribeToTask `-32601` (spec `-32001`); `A2A-Version` header **not read**; card has no cache validators; `GetExtendedAgentCard` served but **not** advertised. CI **fails if a listed failure starts passing**.

Docs two-agent loop: `message/send` `:2024` ↔ `:2025`, shared `contextId`, **3 rounds**. CrewAI client: `timeout=120` s, `max_turns=10`; polling `timeout=300`, `max_polls=100`. LangSmith A2A has **no equivalent documented client SDK defaults**.

#### 2.4 Code CLI (`dcode`)

Product name **Deep Agents Code**; binary **`dcode`**. “Open source coding agent built on the Deep Agents SDK.” Any tool-calling LLM; persistent memory; skills; approval gates. Install: `curl -LsSf https://langch.in/dcode | bash`. Homepage `https://www.langchain.com/dcode`. **Not officially supported on Windows**; WSL suggested. Python **≥ 3.12**. Depends on `deepagents-acp>=0.0.10,<1.0.0`, `langsmith[sandbox]>=0.11.2`, `langchain-quickjs>=0.3.4`, `mcp>=1.28.1`, `genai-prices>=0.1.4`.

| Mode | Entry | Persistence |
| --- | --- | --- |
| Interactive TUI | `dcode` | Threads under profile `.state/`; `/threads` resume |
| Non-interactive | `dcode -n "..."` or piped stdin | Fresh thread per invocation; file memory/skills persist |
| ACP server | `dcode --acp` | stdio to editor |
| CI budget | `-n` + `--max-turns N` and/or `--timeout SECONDS` | Exit **124** on budget; without `-n`/stdin → **2** |

Piped stdin max **10 MiB**. Shell in `-n` disabled unless `-S`/`--shell-allow-list` (`specific`, `recommended`, or `all`). `--startup-cmd` output is **not** added to message history; non-interactive startup-cmd timeout **60 s**. Default model retries: **5** (`--max-retries`; `0` disables). `--sandbox-id` reattaches (skips create/cleanup). `--sandbox-snapshot-name` (langsmith, runloop). `--sandbox-setup PATH` expands `${VAR}` from the **local** env into the guest — deliberate secret copy; prefer proxy + short-lived tokens.

Config precedence: (1) administrator `managed_config.toml` (2) `DEEPAGENTS_CODE_*` env (3) canonical env (4) `~/.deepagents/config.toml` (5) built-in default. `DEEPAGENTS_HOME` relocates the trust root; must be set in the **inherited shell**, never a `.env`. `dcode config get` prints secrets as configured/not configured only.

| Knob | Default / documented value |
| --- | --- |
| `[warnings].session_cost_threshold_usd` | Warns **once per thread** at **$50** (`0` or negative disables) |
| `[warnings].cold_cache_min_delta_usd` | **$0.50** extra (cold vs warm) before asking; Anthropic + OpenAI only |
| `[warnings].trusted_cache_endpoints` | Exact hostnames; gateway otherwise **silences** the warning |
| `[models].allowed` | Unset = all; `provider:*` wildcards; **empty list = no model may be used** |
| `[interpreter]` `js_eval` | `timeout_seconds=5.0`, `memory_limit_mb=64`, `max_ptc_calls=256`; `--interpreter-tools` default **`safe`** |
| `[models].summarization_default` / `auto_classifier` | Else main model |

Approval: `Shift+Tab` cycles modes; `-y`/`--auto-approve` is classifier-backed Auto (interactive local only); `--yolo` skips review after a one-time local risk acknowledgement (interactive only). Named agents: `-a` uses `~/.deepagents/<name>/`. Sandbox provider discovery: config-declared **overrides** third-party entry points (`deepagents_code.sandbox_providers`) **overrides** built-ins. `all-sandboxes` extra does **not** include E2B. Flag parsing footgun: `dcode --sandbox agents` eats `agents` as provider name.

Adjacent hosts (not Code): `libs/talon` = experimental local host (channels, cron); `libs/evals` = Harbor.

#### 2.5 Claude Agent SDK comparison (drafted 2026-04-16)

| Axis | Deep Agents | Claude Agent SDK |
| --- | --- | --- |
| Where the agent **loop** runs | Inside a sandbox **or** outside, using a sandbox as a tool | **Inside** a sandbox only |
| Execution backend | Pluggable: local, VFS, remote sandbox, custom | Local filesystem of that sandbox |
| Model | Any LangChain tool-calling provider (“100+ others”) | Claude via Anthropic, Bedrock, Vertex, Azure |
| Per-provider tuning | `HarnessProfile` (docs: beta) | Configure in code at each call site |
| Deployment | Managed Deep Agents in LangSmith, or `langgraph build` image | Self-host the HTTP/auth/streaming layer. **Claude managed agents is a separate product** |
| Multi-tenancy | Docs: scoped threads, per-user sandboxes, RBAC | Build it yourself (`cwd` + `CLAUDE_CONFIG_DIR`; TS ≥ 0.3.234 / Py ≥ 0.2.140) |
| License | MIT | SDK MIT; Claude Code itself proprietary |
| Named production users | OpenSWE, LangSmith Fleet | — |

Anthropic splits four products: Agent SDK (library), Claude Code CLI, Client SDK (raw API), Managed Agents (hosted REST, separate). Branding: third-party products must not be called “Claude Code.” Permission stack (hooks → deny → ask → mode → allow → `canUseTool`) is **richer** than Deep Agents `interrupt_on` + FS `permissions=` for coding UX. Comparison does **not** claim feature-parity on `bypassPermissions` / `dontAsk` / `plan` / `auto`.

Hosting patterns vs Deep Agents analog: **ephemeral** (`query()` `max_turns=20` / `error_max_turns` ↔ `dcode -n` + `--max-turns`/`--timeout` exit 124); **long-running** (N subprocesses ↔ Agent Server workers, still **one run per `thread_id`**); **hybrid idle** (`SessionStore` required or transcript dies ↔ Postgres checkpointer; HITL frees the worker); **multi-agent container** (N SDK subprocesses ↔ subagents in one graph **or** A2A between deploys). Claude: N sessions ⇒ N subprocesses. Deep Agents on Agent Server: N runs across a worker pool (`N_JOBS_PER_WORKER=10` in 13), **but** still one run per thread. ACP: N editor windows ⇒ N stdio subprocesses **[inferred from stdio model]** unless one `AgentServerACP` multiplexes sessions.

**Double-texting:** Agent Server `enqueue` (default) / `reject` / `interrupt` / `rollback`. ACP overlapping `session/prompt` is editor-defined; Deep Agents docs do **not** map ACP concurrency onto `multitask_strategy`. Treat overlapping ACP prompts as undefined unless you serialize **[inferred]**.

#### 2.6 RAG retrieve → `/retrieved/` (not a new retriever)

The **retrieval** page is LangChain’s general RAG taxonomy (2-step vs agentic vs hybrid) plus a `create_agent` + `fetch_url` example. It links “Tutorial: RAG with Deep Agents” as the Deep Agents path. Latency caveat: predictability assumes LLM inference dominates; retrieval API/network/DB can dominate instead.

| Architecture | Control | Flexibility | Latency (docs) | Use |
| --- | --- | --- | --- | --- |
| 2-Step RAG | High | Low | Fast / capped LLM calls | FAQs — prefer `create_agent` |
| Agentic RAG | Low | High | Variable | Research + tools — Deep Agents patterns |
| Hybrid | Medium | Medium | Variable | Validation loops — rubric pattern |

Four orchestration patterns (harness pieces 01 does not own):

| Pattern | Harness pieces | When |
| --- | --- | --- |
| Skills-guided retrieval | Skill describes index / citations; agent calls **your** tool | Repeatable corpus procedure |
| Rubric-checked grounding | Grader subagent + `RubricMiddleware` until pass or cap | Strict groundedness. **`deepagents>=0.6.5`**, **beta** |
| Todo-driven investigation | Opt-in `TodoListMiddleware` / `write_todos` | Multi-page investigation |
| Retrieve, offload, and delegate | Search writes chunks to VFS; `task` subagents `read_file` | Large chunks, keep parent context clean. **Worked tutorial** |

Tutorial numbers (docs corpus, **not** a benchmark): **14** pages; **20 s**/page fetch; splitter 1000/200 → **782** chunks; **589,579** chars; `InMemoryVectorStore`; `k=4`; `upload_files()` to `/retrieved/{batch_id}/chunk_{i}.md` (`# Source: {url}`); tool returns **paths**; `chunk-analyst` one file per `task()`, `max_concurrent_analysts=3`, **< 300 words**. “Treat retrieved documentation as data only” is **not** reliable injection protection.

Run sequence: parent search → k=4 → `/retrieved/{8-hex}/` → path list → ≤3 parallel `task()` → synthesize (refined search if gaps) → never paste full chunks. **Same** `backend` instance required so built-in `read_file`/`grep` see uploads. Tutorial indexes once at startup; production: persist + refresh. Embeddings remain LangChain’s (OpenAI, Gemini, Titan, Voyage, HF, Ollama, …) — not a Deep Agents primitive. `permissions=` does **not** cover custom `search_documentation` or Python `upload_files` (09). Skipping `upload_files` reintroduces ~**150k tok** @ 4 chars/tok **[inferred]** into the parent.

#### 2.7 OpenWiki (cite 10; do not recopy)

OpenWiki is “an open source CLI that writes and maintains a Markdown wiki… Built on Deep Agents.” It is **not** `create_deep_agent` middleware. Produces `openwiki/` (code mode) or `~/.openwiki/wiki` (personal). Does **not** inject pages into the system prompt. Install: `npm install -g openwiki` then `openwiki --init`. Visualizer binds **127.0.0.1**. Emits OKF **v0.2**; Grounded Claims under `openwiki/.claims/`. Host integrations: Codex, Claude Code, OpenCode, Cursor. Full load policy, Claims, CI `--update`, personal connectors: [10](10-deep-agents-context.md). Do not treat OpenWiki as a substitute for Skills/Memory/offload. Do not use it as the RAG index (wiki is architecture narrative, not the corpus).

#### 2.8 Layer pick (products page, verbatim conditions)

- **`create_agent`:** quick start; standard model/tool/loop; straightforward apps without complex orchestration.
- **LangGraph:** fine-grained orchestration; durable long-running stateful agents; mix deterministic and agentic steps.
- **Deep Agents SDK:** agents that run over long time periods; complex multi-step tasks; want predefined filesystem/bash/context-engineering tools; want predefined prompts and subagents.
- **Claude Agent SDK:** Claude-only, accept agent-in-sandbox + you build server/tenancy; or Anthropic Managed Agents as a **different** SKU.
- **CrewAI:** role/crew prototyping; A2A is first-class (`A2AClientConfig` timeout examples **120 s**, `max_turns=10`).
- **Google ADK:** hierarchical agents; native A2A (`to_a2a()`, `/.well-known/agent-card.json`); Vertex/Agent Engine gravity.

Same capabilities, different integration layer: short-term memory is `StateBackend` on Deep Agents; HITL is `interrupt_on`; subagents are the `task` tool vs LangGraph subgraphs vs LangChain multi-agent subagents.

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: **Limited public data** for ACP/A2A/Code p50/p95/p99 and a published **`$ per 1k` SKU** for an ACP or Code session versus a chat harness. Neither LangChain nor Anthropic publishes editor-round-trip or A2A hop percentiles. Do not invent measured RTTs. `$ per 1k` below restates the 08/13 run **mix inline** (2k cached prefix / 3k uncached / 800 out / 5m cache) **plus adapter overhead**. Latency percentiles are architecture-derived **[inferred] policy targets**. Measure with LangSmith traces; do not cite this table as a vendor SLO.

#### 3.1 Extra hops (architecture, not measured RTT)

| Path | Hops before first model token **[inferred from topology]** | Extra tokens vs in-process `invoke` |
| --- | --- | --- |
| `create_deep_agent.invoke` in-process | 0 network (app → model API) | Baseline harness prefix ~**2k** after 0.7 (08) |
| `dcode` TUI local, `--sandbox none` | 0 extra; same process as harness | TUI injects skills index + MCP schemas (`/context-doctor`) |
| `dcode --sandbox langsmith` | + sandbox create/ready (LangSmith wait default **30 s** upper bound in 09, **not** an SLO) + per-tool RTT | File bytes off host; model still on laptop |
| ACP stdio | + JSON-RPC serialize through editor; editor may inject buffers/diffs | Editor project context **in addition to** `AGENTS.md` **[inferred]** |
| A2A `message/send` | Client → Agent Server JSON-RPC → queue → worker → model | Default `historyScope=context` **replays prior tasks** into the RPC response (not necessarily into the model). `historyLength` cap **10** |
| A2A `message/stream` | Same + SSE | Streaming **ignores** `historyScope`/`historyLength` |
| A2A orchestrator ↔ worker (2 deploys) | **Two** full agent loops + two model bills per round (3-round demo) | Each hop is a full Deep Agent (or ADK) run |

No ACP/A2A **protocol surcharge** is billed by LangChain. CrewAI A2A client `timeout=120` s; Agent Server A2A docs **do not** publish a JSON-RPC timeout. Spec webhook 10–30 s is irrelevant until push-notification config exists (`-32601`).

ACP/A2A/Code do **not** add a new cache SKU. Anthropic/Bedrock prompt-cache middleware still attaches inside `create_deep_agent` (08). A2A full-context history can bust a **client-side** assumption that each task is small; it does not by itself bust the model-provider prefix unless **model** messages grow **[inferred]**.

#### 3.2 `$ cost per 1k` — chat harness mix **inline**, then adapter tax **[inferred]**

Assumptions (not a vendor SKU). Model: **`anthropic:claude-sonnet-4-6`**. List prices used in 08/13: input **$3 / MTok**, output **$15 / MTok**. Anthropic cache multipliers (current Claude except Fable/Mythos 5.1 footnote in 08): **5m write = 1.25×** → **$3.75 / MTok**; **read = 0.1×** → **$0.30 / MTok**. Default Deep Agents cache TTL **5m**. Task shape (08/13 medium research run): **10** model calls inside one 5-minute window; GP **off**; cached prefix **2,000** tokens (v0.7 tools + empty authored prompt); dynamic uncached **3,000** tokens/call; output **800** tokens/call. Cache: **1× 5m write** of the 2k prefix + **9× reads** of the same 2k. Dynamic 3k never cached.

| Component | Tokens × unit | USD / run |
| --- | --- | --- |
| Cache write | 2,000 × $3.75 / 1e6 | $0.00750 |
| Cache reads | 9 × 2,000 × $0.30 / 1e6 | $0.00540 |
| Uncached input | 10 × 3,000 × $3 / 1e6 | $0.09000 |
| Output | 10 × 800 × $15 / 1e6 | $0.12000 |
| **Chat harness total / run** | | **$0.2229** |
| **Chat harness / 1,000 runs** | | **$223** |

Same run **without** prompt caching (5,000 input × 10 × $3/MTok + same output) = **$0.270 / run → $270 / 1k**. Caching saves ~**$47 / 1k** at a 2k prefix.

**What actually differs in the Code/ACP bill** vs that chat mix: (1) MCP tool schemas in every turn (`/context-doctor`; docs example unattributed delta **+2,987** tokens); (2) optional Auto classifier model calls (counted in `/cost`); (3) rubric grader iterations (`--rubric-max-iterations`); (4) summarization-model calls if `[models].summarization_default` ≠ main. ACP adds editor-injected buffers; those tokens are **not** in `/cost` if they never reach the model — they do when the editor includes them in `session/prompt` **[inferred]**.

**ACP/Code prefix tax [inferred].** If `/context-doctor`’s **+2,987** lands on the **cached** prefix (2,000 + 2,987 = **4,987** ≈ 5k):

| Component | Tokens × unit | USD / run |
| --- | --- | --- |
| Cache write | 4,987 × $3.75 / 1e6 | $0.01870 |
| Cache reads | 9 × 4,987 × $0.30 / 1e6 | $0.01346 |
| Uncached input | 10 × 3,000 × $3 / 1e6 | $0.09000 |
| Output | 10 × 800 × $15 / 1e6 | $0.12000 |
| **ACP/Code same 10-call shape / run** | | **$0.2422** |
| ** / 1,000 runs** | | **$242** |

Adapter overhead vs chat harness ≈ **$19 / 1k** at that prefix — **not** a protocol fee. If the editor also injects **+2,000 uncached** tokens/turn (`@file` / diffs) **[inferred, unpublished]**: uncached becomes 10 × 5,000 × $3/1e6 = $0.15000 → **$0.3022 / run → $302 / 1k**.

| Session type | Token sketch | / run | / 1k |
| --- | --- | --- | --- |
| Chat harness 10-call, 5m cache, GP off | 2k prefix + 30k uncached + 8k out | **$0.2229 [inferred]** | **$223** |
| Same + MCP schemas on prefix (~+3k) | 5k prefix, same uncached/out | **$0.2422 [inferred]** | **$242** |
| Same + editor @file +2k uncached/turn | 5k prefix + 50k uncached + 8k out | **$0.3022 [inferred]** | **$302** |
| `dcode` coding hour (`/cost` docs example) | Sonnet 4.5 thread; $0.87+$0.16 subagents | **$1.03 published example** | **$1,030** of *that* thread shape — **not** a SKU |
| A2A 3-round ping-pong (docs demo) | **2 agents × 3 rounds = 6** full runs | 6 × $0.2229 = **$1.34 [inferred]** cached; 6 × $0.27 = **$1.62** uncached (research’s ~$1.6) | **$1,337 / $1,620** per 1k *conversations of that shape* |
| ACP hour-equivalent thread | ≈ `dcode` TUI + unpublished @file | Budget **$1–3 / thread [inferred]** | **$1,000–$3,000** — **not** published |
| RAG retrieve-offload-delegate | 1 search + ≤3 analysts + parent synthesize; k=4; <300 words each | Cheaper than stuffing 782 chunks; **no SKU** | — |
| Naive dump 589,579 chars into parent | ~150k tok @ 4 chars/tok **[inferred]** | ~$0.45 input **once** at $3/MTok **plus** window blow | Tutorial exists to avoid this |

LangSmith sandbox **unit** prices and 0.5 vCPU defaults: [09](09-deep-agents-execution.md) §2 — do not double-count here. Cold-cache UX: `dcode` prompts if Anthropic/OpenAI re-warm delta ≥ **$0.50** (default). Through a gateway the warning is **silent** unless hostname ∈ `trusted_cache_endpoints`. Cross-format LangSmith gateway routes stay silent even when trusted because translation rewrites cache settings. This is a UX gate, not a provider SLO. Session cost warning: **$50 / thread** once (configurable).

#### 3.3 Latency SLA — p50 / p95 / p99 numeric ms

> ⚠️ Gap: **Deep Agents publishes no ACP stdio p99, no A2A hop p99, no `dcode` vs Claude Code token-tax histogram, and no editor-round-trip SLO.** Provider TPM/RPM are account limits. The **30 s** LangSmith sandbox wait in 09 is an **upper bound**, not an SLO. CrewAI **120 s** is a **client timeout**, not Agent Server p99. HITL is a **different clock** (08/13).

Clock-split: (a) in-process parent TTFT (08); (b) ACP stdio / A2A JSON-RPC+queue hop; (c) sandbox create; (d) one ReAct cycle; (e) A2A two-deploy round; (f) documented CLI budgets (timeouts, not percentiles).

**[inferred] policy targets — numeric ms.** Anchors: 08 inner-chat TTFT **640 / 2,560 / 5,120**; 13 Dedicated API hop ≈ **160 / 640 / 1,280** → warm SSE **800 / 3,200 / 6,400**; ReAct cycle **2,000 / 8,000 / 20,000**; 10-call run **20,000 / 80,000 / 200,000**. ACP stdio is local IPC (typically tighter than HTTP) **[inferred]**; A2A shares the Agent Server admit+queue path with 13.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **In-process `invoke` streaming TTFT** **[inferred policy, 08]** | **640 ms** | **2,560 ms** | **5,120 ms** | Stream; 5m cache-warm 2k prefix. Adapters add hops **on top** |
| **ACP stdio extra serialize hop** **[inferred]** | **80 ms** | **320 ms** | **1,280 ms** | Local JSON-RPC; unpublished. Editor buffer injection is **tokens**, not this row |
| **ACP first model token (stdio + parent TTFT)** **[inferred policy]** | **720 ms** | **2,880 ms** | **6,400 ms** | 640+80 / 2,560+320 / 5,120+1,280. Stream `session/update`; do not wait on editor log flush |
| **A2A `SendMessage` first-event (Dedicated warm)** **[inferred policy, 13 path]** | **800 ms** | **3,200 ms** | **6,400 ms** | JSON-RPC + persist + Redis wake + worker + TTFT. `Accept: text/event-stream` for `SendStreamingMessage` |
| **One ReAct cycle (model + local FS)** **[inferred, 08]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Local VFS is not the tail. Remote sandbox **adds per-tool RTT** (unpublished) |
| **10-call research run, GP off, no summarize** **[inferred, 08]** | **20,000 ms** | **80,000 ms** | **200,000 ms** | Cost-section shape. Do not put it on an ACP HTTP timeout that does not exist (ACP is stdio) |
| **A2A one round, 2 deploys** **[inferred]** | **40,000 ms** | **160,000 ms** | **400,000 ms** | 2 × 10-call run class. CrewAI `timeout=120,000 ms` is a **client cap**, not p99 |
| **A2A 3-round docs demo (6 hops)** **[inferred]** | **120,000 ms** | **480,000 ms** | **1,200,000 ms** | 3 × two-agent round **if** each hop is the 10-call shape. Unpublished; do not put on a 30 s webhook |
| **Sandbox create/ready (LangSmith wait bound)** **[inferred policy]** | **5,000 ms** | **15,000 ms** | **30,000 ms** | p99 = documented **30 s** wait **upper bound** (09), **not** a measured percentile. `--sandbox-id` skips create |
| **`dcode --startup-cmd` in `-n`** | — | — | **60,000 ms** | Documented **timeout**, not a percentile. Output **not** in message history |
| **`js_eval` interpreter** | — | — | **5,000 ms** | Documented `timeout_seconds=5.0` |
| **LocalShell command (ACP demo / `--sandbox none`)** | — | — | **120,000 ms** | Default **120 s** / **100,000** output bytes (09) — host blast radius |
| **CrewAI A2A client timeout (peer)** | — | — | **120,000 ms** | Documented example; polling example **300,000 ms**. Not Agent Server |
| **A2A spec webhook (unimplemented)** | — | — | **10,000–30,000 ms** | Spec guidance. Agent Server returns `-32601` for push-notification config — **poll `GetTask` or hold SSE** |
| **Checkpointer `sync` extra per super-step** **[inferred policy, 08]** | **10 ms** | **50 ms** | **200 ms** | ACP MemorySaver is RAM (**0 ms** fsync) and **empty on crash** |
| **HITL interrupt clock** **[inferred policy, 08/13]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | A2A task stays non-terminal until `Command(resume=...)`. Expire → **deny**, not auto-approve |
| **`dcode -n --timeout` budget** | — | — | **your flag × 1,000 ms** | Exit **124**. Analogous to SDK `max_turns=20` / `error_max_turns` |

**Mitigations mapped to percentiles:**

- **p50:** in-process or warm Dedicated A2A; stream ACP `session/update` / A2A SSE; `--sandbox-id` reuse; 5m cache; do not default `historyScope=context` on fat peers.
- **p95:** `historyScope=task` + `historyLength≤10`; cheaper summarizer; timeout `task` independently; serialize ACP prompts until #5084 is fixed.
- **p99:** HITL off the JSON-RPC timeout (clients that retry `SendMessage` with a new `messageId` on an interrupted thread will **duplicate** turns — `enqueue` vs `reject` matters); sandbox create **is** the tail for cold `--sandbox`; never wait on LangSmith export; product hop cap ≪ 9,999; CrewAI `max_turns=10` on mixed fleets.

#### 3.4 Throughput / back-pressure

> ⚠️ Gap: Deep Agents publishes **no** harness RPM/TPM. Agent Server: **at most one run per `thread_id` at a time** (13). A2A `ListTasks` + context-scoped history can amplify **response size** (cap 10 messages after scope). `dcode -n` is one thread per process invocation. Piped stdin **10 MiB**. `recursion_limit` **9,999**. Interpreter **256** PTC calls. Session cost warn **$50**.

**Back-pressure design:** (1) A2A: one run per `contextId`; send `taskId` only to **add to a still-running** task (e.g. waiting on input); new turn = new `taskId`. (2) Editor sessions: N windows ⇒ N stdio processes **or** one multiplexed `AgentServerACP` (cancel bug until scoped). (3) `dcode -n` + `--max-turns` / `--timeout` exit 124 in CI — analog of SDK `max_turns=20`. (4) `[models].allowed` empty list = **no model** (admit deny). (5) `historyLength>10` is `-32602` — fail closed, not truncate silently (streaming **does** ignore — pager lie). (6) Bulkhead parent model vs Auto classifier vs rubric grader vs sandbox pool vs editor log I/O. (7) Circuit on provider 429 so `--max-retries 5` does not become a token amplifier. (8) MCP ingress is **stateless** — do not use it when you needed `contextId` continuity.

#### 3.5 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of adapters** | Product SLO is the **graph**. ACP subprocess / A2A hop / sandbox create are **best-effort extra hops**. Circuit-open on A2A → **direct `invoke`** → refuse — not LocalShell, not “strip ACP and run host shell” | Editor UX vs worker availability |
| **RPO ACP MemorySaver** | **Empty on subprocess death** (editor crash, `dcode --acp` restart). Official ACP quickstart does **not** wire Agent Server Postgres | Demo velocity vs resume |
| **RTO ACP MemorySaver** | **Cannot restore.** Re-`session/new`. `session/resume` is capability-negotiated and still needs a real checkpointer behind the agent | Editor reload vs “conversation survived” |
| **RPO `dcode` `.state/`** | Last persisted thread under profile dir (product, not `AgentServerACP` snippet) | Laptop disk vs tenant isolation (CLI is **single-player** profile dir) |
| **RPO/RTO Postgres / Agent Server** | Last durable super-step (`sync`/`async`/`exit` as 08/13). RTO happy = resume `thread_id`/`contextId`. RTO crash ≈ sweeper **2 min** (13). `thread_id` **< 255** chars | Crash-consistency vs p50 (`sync` **10 / 50 / 200 ms [inferred]**) |
| **RPO A2A history** | Default full-context replay is a **disclosure** RPO: task 2 re-shows task 1 secrets to the **client**. Mitigate `historyScope=task` | Debugger convenience vs exfil |
| **RPO sandbox** | Last snapshot (`--sandbox-snapshot-name`) / provider TTL. `--sandbox-id` skips cleanup — orphan spend | Reattach vs leak |
| **RPO OpenWiki** | Git-resident `openwiki/` — world-readable to anyone with repo read. Claims under `.claims/` (10) | Durable architecture narrative vs secrets-in-wiki |
| **RPO traces / editor logs** | Sampled LangSmith is lossy. Zed ACP logs can hold prompts, diffs, secrets the agent read | Debug vs PII |
| **Compliance** | **Not provided by ACP/A2A packages.** Editor is TCB on stdio. A2A without `@auth` = anyone with the URL. SOC2: editor logs, A2A payloads, checkpoints, VFS, traces are subprocessors if they hold prompts. GDPR erasure of a `contextId` is checkpointer+store+sandbox+trace+**editor log** purge | Time-to-debug vs residency |
| **Correctness vs $** | RAG offload+3 analysts vs 589k-char dump. A2A ping-pong **2×** model bills per round **[inferred]**. `dcode` $50 warn / $0.50 cold-cache are UX, not SLOs | Agency vs wallet |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_ACP_demo = empty. RTO_ACP_demo = new session. RPO_A2A = last Agent Server checkpoint for that UUID `thread_id`. RTO_A2A_happy = echo `contextId`. RTO_A2A_client_timeout = **dangerous** if the run is HITL-interrupted — retry with new `messageId` may enqueue a duplicate turn. RPO_Code_state = last `.state/` write. A `GraphRecursionError` is a **completed refuse**, not an RPO hole.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: ACP session vs LangGraph thread; A2A timeouts

Deep Agents still does **not** wrap Temporal. Durability is the checkpointer the **adapter bound**.

| Concept | ACP | LangGraph / Deep Agents / A2A |
| --- | --- | --- |
| Connection | One stdio subprocess per editor agent server | Process or Agent Server worker |
| Conversation | ACP `session_id` from `session/new` | `thread_id` on checkpointer; A2A `contextId` **is** that UUID |
| Workspace | `cwd` on `session/new` → `AgentSessionContext.cwd` | `FilesystemBackend(root_dir=...)` / sandbox working dir |
| Persistence | Demo **`MemorySaver()`** — RAM; dies with subprocess | Production: Postgres via Agent Server or `PostgresSaver`. `dcode` TUI: `~/.deepagents/.state/` |
| Cancel | `session/cancel` (**#5084** process-wide flag) | Run cancel / graph interrupt; A2A `CancelTask` |
| Resume | ACP `session/resume` (capability-negotiated) | Same `thread_id` + checkpointer |

A2A continuity: omit `contextId` on first message; reuse server UUID forever; **omit** completed `taskId`. Durability: checkpoint/lease/HITL apply to A2A conversations the same as `/runs`. Push notifications / resubscribe: unimplemented (`-32601`) — poll `GetTask` or hold SSE. Disable `disable_a2a` for air-gapped deploys. Cross-agent crash: if agent A mints `contextId` and agent B is not LangGraph, B must stamp OTel `thread_id` or traces split.

CrewAI mixed-fleet knobs: `fail_fast=False`; `max_turns`; `PollingConfig(timeout=300, max_polls=100)`. Use them on the **Crew** side; do not assume LangSmith has the same client defaults.

**Code sandbox vs LocalShell:**

| | `dcode --sandbox <provider>` | `LocalShellBackend` (ACP demo / `--sandbox none`) |
| --- | --- | --- |
| Loop location | Host (`dcode` process) | Host |
| FS/`execute` | Remote guest | `subprocess.run(shell=True)` on the **editor/laptop** (09) |
| Isolation | Provider VM/container | **None** for shell |
| Reattach / snapshots | `--sandbox-id`; langsmith/runloop snapshots | N/A |
| Default timeout | Provider + command; LocalShell default **120 s** / **100_000** output bytes | Same if using LocalShell |
| `kill_on_disconnect` | LangSmith: **command-scoped**, not graph-scoped (09) | Process kill = all |

ACP demo **uses LocalShell** against the editor’s `cwd` — same blast radius as `dcode` without `--sandbox`. Code docs: “Running `dcode` inside an untrusted project directory exposes you to project-controlled files… use a remote sandbox for untrusted repositories.”

A2A + HITL: `interrupt()` writes checkpoint and releases the worker; the A2A task stays non-terminal until `Command(resume=...)`. Clients that treat HTTP timeout as failure will **duplicate** turns if they retry `SendMessage` with a new `messageId` on the same `contextId` while the run is interrupted.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx; A2A worker blip; ACP stdio stall; sandbox allocate; `--max-retries` 5 path | Error rate; p99 window | Full-jitter retries on **idempotent** reads / `GetTask`. **Do not** retry `SendMessage` with a new `messageId` on an interrupted thread without `reject`/`enqueue` policy. `dcode` model retries are **not** A2A retries |
| **Permanent** | Non-UUID `contextId` (`-32602`); completed `taskId` (`-32004`); gRPC/HTTP+JSON against Agent Server (not implemented); ACP v2-only client against v1 agent; `deepagents-code` pin stuck on 0.7.10 | Non-retryable JSON-RPC / construction | Fail closed. Negotiate ACP v1. Do not mint `session-42` |
| **Poison-pill A2A without identity** | Open `/a2a/{uuid}` if auth is none; stolen `assistant_id` (card is public); `contextId` replay from a leaked trace; default **all** tool results as `DataPart`; full-context history replay | Unauthenticated `SendMessage`; cross-tenant thread join; peer sees internal tool dumps | `@auth` + `@auth.on.threads` owner filter; `A2A_ALLOWED_TOOL_CALL_RESULTS`; `historyScope=task`; `disable_a2a` when the deploy must not be a peer. **UUID ≠ authorization** |
| **Poison-pill ACP** | Malicious project `.env` / Makefile; `class_path` RCE in `config.toml`; skill symlink escape; `--trust-project-mcp` in CI; `-S all`; `accept_everything` auto-approves shell; #5084 cancel bleed | Unexpected host commands; cross-session cancel | Remote sandbox; admin `managed_config.toml`; empty skill allowlist; never `-S all` on untrusted ACP; one ACP server per window until #5084 lands **[inferred ops]** |
| **Poison-pill RAG** | k=50 full text to parent; write 589k chars to `/docs/all.md`; index without ACL on shared `StateBackend`; trust `# Source:` header | Context blow; cross-user retrieve | Paths only; k=4; per-tenant store + tool filter; citation checks. Delimiters are **not** sufficient (tutorial says so) |
| **Idempotency** | Duplicate A2A turn on HITL timeout; ACP re-prompt after cancel bleed; `write_file` overwrite on resume | Duplicate diffs / two shells | Idempotency keys on mutating tools; new `taskId` per turn; do not reuse terminal `taskId`. Deep Agents does **not** publish a binding/hash of approved HITL args — treat as **[inferred] gap** |
| **Dual harness** | Claude SDK `query()` subprocess **and** Deep Agents `execute` on the same repo | Duplicate diffs; two `/cost`; two permission models | One inner harness; wrap the other as MCP/A2A **peer**, not nested loops |
| **Denial of wallet** | A2A 6-run ping-pong; default history replay; GP on; 9,999 loop; `--max-retries 5` on 429 | Token ledger; `$50` warn never fires through a gateway | `max_turns`; `historyScope=task`; product cap; breaker on retry loops |

#### 4.3 Circuit breaker closed → open → half-open

> ⚠️ Gap: **Limited public data** for ACP/A2A-specific circuit breakers. `dcode --max-retries` default **5** is **model-call** retry, not A2A. Agent Server node retry policies are production-runtime (13), not protocol-level. Put breakers in the **client wrapper** around A2A JSON-RPC, ACP subprocess, and fallback `invoke`.

Independent breakers: **A2A backend**, **ACP stdio/session**, **direct invoke**, **sandbox allocate**, **checkpointer**. An A2A 429 must not fail open to LocalShell **and** must not skip `@auth` on the fallback invoke.

```
        A2A JSON-RPC 5xx/timeout | ACP stdio dead | provider 429 | sandbox 503
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  adapter │  success resets consecutive count                     │ FAIL FAST│
  └────┬─────┘                                                       │ fallback │
       ▲                                                             │ chain    │
       │ probe OK                                                    └────┬─────┘
       │                                                                  │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 synthetic│
                                                                    │ probe; stay│
                                                                    │ OPEN if fail│
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip condition | Closed → open | Half-open probe | Fallback |
| --- | --- | --- | --- |
| A2A JSON-RPC 5xx / timeout | consecutive ≥ **5** or error-rate window | One `GetTask` or tiny `SendMessage` on a **new** UUID | **A2A → direct `graph.invoke` (same compiled graph) → deterministic refuse** |
| ACP stdio EOF / subprocess death | consecutive ≥ **3** | One `initialize` | **ACP → HTTP/SDK `invoke` → refuse**. Never LocalShell “so the editor still works” |
| Parent model 429/5xx | consecutive ≥ **5** | One tiny invoke, GP off | Same chain; `dcode --max-retries` is not this breaker |
| Sandbox pool empty / 503 | allocate ≥ **3** | One allocate | Queue or 503 — **never** `--sandbox none` / LocalShell |
| Checkpointer timeout | consecutive ≥ **3** | One checkpoint write | Fail closed for “must resume”; **do not** silently switch to MemorySaver in prod |
| MCP gateway 5xx | error-rate | One `tools/list` | Fail closed on egress tools; ACP session is **not** a bypass |

**Fallback chain (required interview answer):** **ACP/A2A adapter → direct `create_deep_agent` `invoke` (same graph) → deterministic refuse.** Never: circuit open → LocalShell. Never: HITL timeout → auto-approve. Never: A2A down → skip `@auth`. Never: ACP down → `--trust-project-mcp` + `-S all`. Parent-only fallback for A2A: if the **peer** is down, the orchestrator continues with **subagents only** (`fail_fast=False` spirit) rather than hanging the user — that is **A2A → parent-only**, not a second harness.

#### 4.4 Zero-Trust MCP (editor ACP is not a PEP)

ACP stdio means the **IDE process parents the agent**, reads stdout, and can log every JSON-RPC frame. The editor is the **TCB**, not a policy enforcement point. `permissions=` still covers **built-in FS tools only**. MCP `tools/call`, custom tools, `execute`, `backend.upload_files`, and A2A `DataPart` publication are **out of that PDP**. Shipping ACP does **not** retire the gateway from 07/08.

**Egress MCP (agent → tools)** — same PEP as 08 §4.4, still required when the session arrived via Zed:

| Zero-Trust control | What you implement |
| --- | --- |
| **Transport** | Authenticated channel. OAuth 2.1 + PKCE `S256`. Clients **MUST** send RFC **8707** `resource` = **canonical MCP server URI** on authorize *and* token. Servers **MUST** accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token to upstream APIs (typically RFC **8693** exchange). stdio MCP is **outside** this OAuth profile (host-env secrets) — `-n` silently skips unapproved project MCP unless `--trust-project-mcp` |
| **Hash-pin / allowlist** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify on every `tools/call`. Mismatch → pause / re-consent. CVE-2025-54136 (MCPoison) CVSS **8.8**. Adapter **name** filter ≠ hash pin |
| **Identity** | Verified access token / `runtime.server_info.user.identity`. **Never** the LLM. ACP `AgentSessionContext` is editor-supplied cwd/model/mode — **not** authn. A2A `contextId` UUID is a thread key, **not** a principal |
| **Capability** | Model proposes; **PEP disposes**. `interrupt_on` is a review queue, not authz. `dcode --allow-fs-tools` / `[models].allowed` are **CLI PDPs** — they do not replace Agent Server `@auth` when the same graph is served over A2A |

**Ingress MCP** (`POST /mcp`) and **ingress A2A** are sibling Agent Server surfaces. Without `@auth.authenticate`, LangGraph sees only the **API-key owner**. LangSmith **product** login (email/password, GitHub/Google, Enterprise SAML+SCIM) authenticates **operators**, not A2A peer agents. Self-hosted “None” auth is install-verification only, slated for removal after basic auth. CrewAI AMP: agent cards are **intentionally public** — “Do not include sensitive information in agent names, descriptions, or skill definitions.” CrewAI OSS default `SimpleTokenAuth` via `AUTH_TOKEN`.

**Tool-level RBAC (what exists vs what you build):**

| Control | What it binds | What it is not |
| --- | --- | --- |
| `permissions=` | Built-in FS path PDP, fail-open | MCP, `execute`, custom search, `upload_files` |
| `interrupt_on` / ACP modes | Review queue (`ask_before_edits` … `accept_everything`) | Authorization. `accept_everything` auto-approves shell |
| `A2A_ALLOWED_TOOL_CALL_RESULTS` | Which tool names become `DataPart`s | Authn |
| `@auth.on.threads` | Owner filter / 403 | `contextId` format check |
| `managed_config.toml` / `[models].allowed` | Admin beats user; empty allowlist = no model | Agent Server PEP |
| `--allow-fs-tools` / skill `extra_allowed_dirs` | Code-product FS/skill containment | Gateway hash-pin |
| `--trust-project-mcp` | Explicit opt-in for repo-controlled MCP | Default-deny in CI for unknown repos |

Correct story: **gateway PEP on egress MCP** + **`@auth` on ingress A2A/MCP/`/runs`** + **remote sandbox** for untrusted ACP cwd + **never LocalShell**. Claude SDK permission modes are richer for coding UX; do not mix `bypassPermissions` with Deep Agents `interrupt_on` across nested agents.

**Sandbox-as-tool vs credentials:** credentials inside a sandbox are compromised under prompt injection; LangSmith auth **proxy** injects headers outside the guest. Claude SDK / agent-in-sandbox typically **puts keys in the guest** unless you add a proxy yourself. Comparison’s “LangSmith Sandbox auth proxy” is a Deep Agents+LangSmith advantage, **not** an ACP default. Setup scripts expanding `${GITHUB_TOKEN}` from local env into the sandbox are a deliberate secret copy.

#### 4.5 PII pipeline — detect → redact → audit

ACP frames include prompts, diffs, and possibly secrets from files the agent reads. A2A default history replays tool results. `dcode auth` / `config get` refuse to print secrets — **editors may still capture stdio**. Client-side LangSmith redaction is a config-file tracing section — not DLP on Zed logs.

Scan sinks: **editor ACP logs**, **A2A JSON-RPC payloads** (including `historyScope=context` replays), LangSmith traces, checkpoints/VFS, model I/O, webhook bodies, HITL UI, sandbox setup env expansions.

**Pipeline (explicit — three steps, all required):**

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner (Bedrock/Presidio/gateway). Scan: `session/prompt` bodies, `@file` buffers, A2A `SendMessage`/`GetTask` results, tool `DataPart`s, VFS writes, `/cost` UX strings, editor log files. If ML is down: **fail closed to mask** on user-facing chat/ACP Markdown; **fail closed (block)** on tool args to external MCP / sandbox env / A2A publication — do not send raw PAN to a foreign agent or into an editor support session.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the task can continue; `block` when the field must not exist (secrets paths, MCP args, A2A allowlist miss). Strip the value from VFS **and** from the message channel **and** from ACP log exports. `historyScope=task` is a **disclosure** control, not redaction — still run this pipeline. Do **not** persist raw PAN in traces (sampled APM is not this step).
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`redact` / `mask` / `hash` / `block` / `historyScope=task`), detector (`regex` | `pii-middleware` | `gateway` | `editor-log-policy`), `correlation_id`, `tenant`, **`contextId`=`thread_id`**, surface (`acp`|`a2a`|`dcode`|`invoke`), tool **arg digest**. A tool call without an audit row is a control-plane bug. Retention: security evidence *and* a sensitive-data asset — GDPR erasure vs legal hold is digest-level. Chain-of-custody: checkpointer `checkpoint_id` + A2A `taskId` + ACP `session_id` mapping + arg digest — **not** “Zed has the ACP log so we are SOX-ready.”

OpenWiki: treat `openwiki/` as **world-readable to anyone with repo read**. Do not put secrets in wiki pages or `INSTRUCTIONS.md`. `.openwikiignore` is a **read** boundary for the generator, not an ACL. LangSmith connector writes project names to committed `openwiki/.langsmith.json`, never the key (`OPENWIKI_LANGSMITH_API_KEY`). Visualizer is localhost-only — exporting static **is** a publication event.

RAG tutorial indexes **public** docs into an **in-memory** store with **no tenant filter**. Index-time ACL belongs to the vector DB (01); tool-time `user_id` filter is **your** `@tool`; VFS isolation is `permissions=` on `/retrieved/**` **or** per-user backend. Output validation: “Check that answers cite expected documentation paths.”

#### 4.6 Immutable logs / version skew

Combine: git SHA of the graph + Agent Server **revision** (13) + PII/decision WORM + `checkpoint_id` + A2A `taskId`. Syscall audit does not exist in-tree (09). ACP editor logs are **mutable support artifacts** unless you ship them to WORM yourself.

Version skew: `deepagents-code==0.1.65` pins **0.7.10** vs SDK **0.7.12**. `deepagents-acp` 0.0.11 is **Alpha**. A2A gRPC/HTTP+JSON clients will fail. ACP v2 draft vs `agent-client-protocol>=0.10.1` — negotiate v1. IBM ACP vs Agent Client Protocol confusion in vendor comparisons. TCK known-failure list is a **compatibility contract** — a “fix” can break live A2UI clients (`tool_results` snake_case kept on purpose).

---

### 5. Production Enterprise Code

Self-contained. Optional `deepagents` / `langgraph` imports. Stdlib path runs the same control flow: retries + full jitter, circuit breakers (A2A / ACP / direct invoke), fallback **ACP/A2A → direct `invoke` → refuse**, UUID `contextId`=`thread_id`, PII detect→redact→audit, structured logs with `correlation_id` / `contextId`. Never LocalShell. Run: `python deep_agents_ecosystem.py`.

```python
#!/usr/bin/env python3
"""Ecosystem adapters around one compiled Deep Agents graph.

Fallback: ACP/A2A surface → direct graph.invoke → deterministic refuse.
contextId is thread_id and MUST be a UUID. Never LocalShellBackend.
Run: python deep_agents_ecosystem.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# Optional (not required to run this file):
#   from deepagents import create_deep_agent
#   from langgraph.checkpoint.postgres import PostgresSaver
#   import httpx  # A2A JSON-RPC client


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("thread_id", "-"),
            ("context_id", "-"),
            ("surface", "-"),
            ("breaker", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("da_ecosystem")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"thread":"%(thread_id)s","contextId":"%(context_id)s",'
            '"surface":"%(surface)s","breaker":"%(breaker)s",'
            '"msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, **extra: Any) -> None:
    LOG.log(level, msg, extra=extra)


def require_thread_uuid(context_id: str) -> str:
    """A2A contextId == LangGraph thread_id. Non-UUID → -32602 class error."""
    try:
        return str(uuid.UUID(context_id))
    except ValueError as exc:
        raise InvokeError("permanent", "a2a_-32602_invalid_thread_id") from exc


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.5,
    cap_s: float = 8.0,
    retryable: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = min(cap_s, base_s * (2**i))
            sleep_s = random.random() * sleep_s  # full jitter
            slog(logging.WARNING, f"retry_backoff attempt={i + 1} sleep_s={sleep_s:.3f}", surface="client")
            time.sleep(sleep_s)
    assert last is not None
    raise last


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pii_detect_redact_audit(
    text: str,
    *,
    audit: list[dict[str, Any]],
    correlation_id: str,
    tenant_id: str,
    thread_id: str,
    sink: str,
    block_on_pan: bool = True,
) -> str:
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)

    def _row(action: str, post: str) -> None:
        audit.append(
            {
                "cid": correlation_id,
                "tenant": tenant_id,
                "thread_id": thread_id,
                "contextId": thread_id,
                "sink": sink,
                "kinds": kinds,
                "action": action,
                "pre": pre,
                "post": post,
                "detector": "regex",
            }
        )

    if "pan" in kinds and block_on_pan and sink in {
        "mcp_args",
        "sandbox_env",
        "a2a_publish",
        "acp_log",
        "vfs_write",
    }:
        _row("block", _sha(""))
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    _row("redact" if redacted != text else "allow", _sha(redacted))
    return redacted


class InvokeError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "transient" | "permanent"


@dataclass
class RunResult:
    text: str
    surface: str
    degraded: bool
    thread_id: str


class SurfacePort:
    name: str

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str: ...


@dataclass
class ScriptedPort(SurfacePort):
    """Stdlib stand-in so this file runs without deepagents / Agent Server."""

    name: str
    fail_kind: str | None = None

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        if self.fail_kind == "transient":
            raise InvokeError("transient", f"{self.name}_unavailable")
        if self.fail_kind == "permanent":
            raise InvokeError("permanent", f"{self.name}_rejected")
        user = payload.get("user") or ""
        return f"ok:{self.name}:{user[:80]}"


def deterministic_refuse(reason: str) -> str:
    return json.dumps({"status": "refused", "reason": reason})


def try_build_direct_graph() -> SurfacePort | None:
    """Same create_deep_agent graph the adapters wrap — never LocalShell here."""
    try:
        from deepagents import create_deep_agent  # type: ignore
        from langgraph.checkpoint.memory import InMemorySaver  # type: ignore

        graph = create_deep_agent(
            model="anthropic:claude-sonnet-4-6",
            tools=[],
            checkpointer=InMemorySaver(),  # prod: PostgresSaver / Agent Server injects
            excluded_tools={"execute"},
            name="ecosystem-direct",
        )
    except Exception:
        return None

    class _G(SurfacePort):
        name = "direct_invoke"

        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
            result = graph.invoke(payload, config=config)
            messages = result.get("messages") or []
            last = messages[-1] if messages else ""
            return getattr(last, "content", str(last))

    return _G()


@dataclass
class AdapterRuntime:
    """Fallback: ACP/A2A → direct invoke → refuse. Parent-only if A2A peer is down."""

    a2a: SurfacePort
    acp: SurfacePort
    direct: SurfacePort
    a2a_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("a2a"))
    acp_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("acp"))
    direct_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("direct"))
    audit: list[dict[str, Any]] = field(default_factory=list)
    prefer: str = "a2a"  # "a2a" | "acp"

    def run(
        self,
        user_text: str,
        *,
        tenant_id: str,
        context_id: str,
        correlation_id: str | None = None,
        sink_block: str = "model_input",
    ) -> RunResult:
        cid = correlation_id or str(uuid.uuid4())
        thread_id = require_thread_uuid(context_id)
        extra = {
            "correlation_id": cid,
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "context_id": thread_id,
        }
        safe = pii_detect_redact_audit(
            user_text,
            audit=self.audit,
            correlation_id=cid,
            tenant_id=tenant_id,
            thread_id=thread_id,
            sink=sink_block,
            block_on_pan=sink_block in {"a2a_publish", "acp_log", "mcp_args"},
        )
        payload = {"messages": [{"role": "user", "content": safe}], "user": safe}
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"cid": cid, "tenant_id": tenant_id, "contextId": thread_id},
        }

        def _call(port: SurfacePort, breaker: CircuitBreaker) -> str:
            extra["surface"] = port.name
            extra["breaker"] = breaker.name
            slog(logging.INFO, "invoke_start", **extra)

            def _once() -> str:
                return port.invoke(payload, config)

            try:
                breaker.allow()
                text = retry_call(_once)
                breaker.record_success()
                out = pii_detect_redact_audit(
                    text,
                    audit=self.audit,
                    correlation_id=cid,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    sink="model_output",
                    block_on_pan=False,
                )
                slog(logging.INFO, "invoke_ok", **extra)
                return out
            except CircuitOpenError:
                raise
            except InvokeError as exc:
                if exc.kind == "transient":
                    breaker.record_failure()
                slog(logging.ERROR, f"invoke_fail:{exc.kind}", **extra)
                raise
            except Exception as exc:
                breaker.record_failure()
                slog(logging.ERROR, "invoke_fail:unexpected", **extra)
                raise InvokeError("transient", type(exc).__name__) from exc

        primary = self.a2a if self.prefer == "a2a" else self.acp
        primary_br = self.a2a_breaker if self.prefer == "a2a" else self.acp_breaker
        try:
            text = _call(primary, primary_br)
            return RunResult(text, primary.name, False, thread_id)
        except InvokeError as exc:
            if exc.kind == "permanent":
                slog(logging.ERROR, "fallback_refuse_permanent", **{**extra, "surface": "refuse"})
                return RunResult(deterministic_refuse(str(exc)), "refuse", True, thread_id)
            slog(logging.WARNING, "fallback_direct_invoke", **{**extra, "surface": "direct_invoke"})
        except (CircuitOpenError, TimeoutError, ConnectionError):
            slog(logging.WARNING, "fallback_direct_invoke", **{**extra, "surface": "direct_invoke"})

        try:
            text = _call(self.direct, self.direct_breaker)
            return RunResult(text, self.direct.name, True, thread_id)
        except (CircuitOpenError, InvokeError, TimeoutError, ConnectionError) as exc:
            slog(logging.ERROR, "fallback_refuse", **{**extra, "surface": "refuse"})
            return RunResult(deterministic_refuse(type(exc).__name__), "refuse", True, thread_id)


def build_runtime() -> AdapterRuntime:
    direct = try_build_direct_graph() or ScriptedPort(name="direct_invoke")
    return AdapterRuntime(
        a2a=ScriptedPort(name="a2a"),
        acp=ScriptedPort(name="acp"),
        direct=direct,
        prefer="a2a",
    )


if __name__ == "__main__":
    rt = build_runtime()
    tid = str(uuid.uuid4())
    r1 = rt.run(
        "Summarize ticket 55 for ada@example.com",
        tenant_id="acme",
        context_id=tid,
        correlation_id="cid-1",
    )
    print(r1)
    assert r1.thread_id == tid
    assert "[EMAIL_" in r1.text
    assert any(row["contextId"] == tid for row in rt.audit)

    try:
        rt.run("hello", tenant_id="acme", context_id="session-42", correlation_id="cid-bad")
        raise SystemExit("expected -32602 class error")
    except InvokeError as exc:
        assert exc.kind == "permanent"

    rt.a2a = ScriptedPort(name="a2a", fail_kind="transient")
    rt.a2a_breaker = CircuitBreaker("a2a", failure_threshold=1, cooldown_s=60)
    r2 = rt.run("hello", tenant_id="acme", context_id=str(uuid.uuid4()), correlation_id="cid-2")
    print(r2)
    assert r2.degraded is True
    assert r2.surface in {"direct_invoke", "refuse"}

    rt.direct = ScriptedPort(name="direct_invoke", fail_kind="transient")
    rt.direct_breaker = CircuitBreaker("direct", failure_threshold=1, cooldown_s=60)
    r3 = rt.run("hello", tenant_id="acme", context_id=str(uuid.uuid4()), correlation_id="cid-3")
    print(r3)
    assert r3.surface == "refuse"
    print("ok", len(rt.audit), "audit rows")
```

**Wiring notes (not in the script):** production A2A client sends `contextId` = `thread_id` UUID, new `taskId` per turn, `historyScope=task`, `A2A_ALLOWED_TOOL_CALL_RESULTS` allowlist, `@auth` on both deploys. ACP: factory + `modes`/`models` (#4254); PostgresSaver not MemorySaver; `CompositeBackend` default = **sandbox**, not LocalShell; one server per editor window until #5084. Direct invoke: same compiled graph, `excluded_tools={"execute"}` unless sandbox + auth proxy, pin `deepagents>=0.7.9`. PIIMiddleware is **not** default — append it. `thread_id` **< 255** chars. Never nest Claude Agent SDK `query()` inside this loop.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Internal coding assistant: Deep Agents Code + remote sandbox vs Claude Agent SDK

**Problem.** Multi-tenant internal IDE/CLI coding agent for **~500 engineers**. Untrusted customer repos (SOC2). Board wants **model flexibility** (Claude + Gemini + self-host GLM) *or* the org is already Claude-standardized. Must not run host shell. Same graph should serve Zed ACP, `dcode` CI (`-n` + exit 124), and Agent Server for Slack/web. Comparison page drafted **2026-04-16**. Named production users on that page: **OpenSWE**, **LangSmith Fleet**. Claude Managed Agents ≠ LangSmith MDA.

**Proposed architecture (recommended when portability + LangSmith tenancy matter):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: one create_deep_agent graph                    │
  │ JWT →   │   │   dcode --acp OR AgentServerACP(factory, modes/models)  │
  │ identity│   │   CompositeBackend default=REMOTE SANDBOX not LocalShell│
  │         │   │   PostgresSaver (not MemorySaver)  thread_id UUID       │
  │         │   │   managed_config.toml ; [models].allowed                │
  │         │   │   MCP EGRESS gateway PEP (RFC 8707, hash-pin)           │
  │         │   │   PII detect→redact→audit on ACP logs + traces          │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ DATA: loop on gateway/laptop ; execute/read_file on guest             │
  │   --sandbox langsmith|daytona|modal  --sandbox-id reattach            │
  │   auth PROXY — keys never in guest (Claude SDK typically in-guest)    │
  │   CI: dcode -n --max-turns --timeout → exit 124                       │
  │   Slack/web: SAME graph on Agent Server (13)                          │
  │   OpenWiki pointer in AGENTS.md — not stuffed into system prompt      │
  │   Optional: Claude Code as MCP/A2A PEER — do not nest two harnesses   │
  └───────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A1 `dcode` / ACP + remote sandbox (recommended)** | **A2 Claude Agent SDK self-hosted** | **A3 Claude Managed Agents** |
| --- | --- | --- | --- |
| **Cost** | Chat mix **$223 / 1k [inferred]** + MCP prefix tax **~$242 / 1k**; `/cost` example **$1.03**/thread; 1k ACP hour-threads **$1k–$3k [inferred]**; sandbox units in 09 | Same Claude tokens + **you** pay the subprocess fleet (N sessions ⇒ N processes). `max_turns=20` example caps runaway | Anthropic runtime SKU (unpublished here). SDK code does **not** deploy onto it |
| **Latency** | ACP first token **720 / 2,880 / 6,400 ms [inferred]**; sandbox create p99 **30,000 ms** (09 wait bound); 10-call **20,000 / 80,000 / 200,000 ms [inferred]** | Subprocess TTFT unpublished; ephemeral `max_turns=20`. Shared `cwd` unless you isolate | Hosted REST; unpublished editor p99 |
| **Ops complexity** | Pin `deepagents-code` (0.7.10 vs SDK 0.7.12 skew); ACP Alpha; factory for Zed selectors | You write HTTP/SSE/auth/tenancy (`CLAUDE_CONFIG_DIR` per tenant) | Lowest host burden; Claude-only |
| **Security posture** | Sandbox-as-tool + LangSmith **auth proxy**; editor still TCB on ACP stdio; Zero-Trust MCP gateway still required; never LocalShell | Keys typically **in the guest**; richer permission modes (`dontAsk`, `bypassPermissions`); you build the PEP | Anthropic session isolation; Claude-only data path |
| **Scalability** | LSD/MDA threads + per-user sandbox **if** on Agent Server; CLI alone is **single-player** `~/.deepagents/` | Linear subprocesses; isolate `cwd` | Anthropic control plane |

**Decision.** **A1 wins** for 500 engineers, mixed models, untrusted customer repos: **`dcode` or ACP-wrapped `create_deep_agent` + remote sandbox, never LocalShell**; deploy the **same graph** on Agent Server for web/Slack; ACP for IDE. Map Anthropic hosting onto LSD: ephemeral → `dcode -n`; long-running → Agent Server + thread stream; hybrid idle → Postgres (not MemorySaver); multi-agent → subagents inside the trust domain, A2A across domains. Claude SDK as an **optional MCP/A2A peer** if a team insists on Claude Code UX — **do not nest two harnesses**. **A2 wins** for a Claude-standardized org of ~20 engineers who will own the gateway and want permission-mode UX. **A3 wins** only if you want zero host for the loop and accept Claude-only + a **separate** SKU. OpenWiki CI `--update` supplies durable repo context without stuffing the wiki into the system prompt.

#### Scenario B — Multi-agent: A2A vs subagents-only

**Problem.** A platform must compose research, billing, and HR specialists. Research and coding subtasks are **the same trust domain** (parallel chunk analysis, the RAG tutorial shape). Billing lives in **another compliance zone** (PCI). HR must **not** be a peer (`disable_a2a`). Some RFP partners already speak CrewAI/ADK A2A. Budget ping-pong tokens. Unified LangSmith Threads view required. Trap: “A2A is how Deep Agents does subagents.”

**Proposed architecture (recommended hybrid):**

```
  ┌───────────────────────────────────────────────────────────────────────┐
  │ CONTROL: orchestrator Deep Agent  (one trust domain)                  │
  │   subagents/task for private decomposition (chunk-analysts, k=4)      │
  │   A2A ONLY to billing assistant in zone B                             │
  │   contextId UUID = thread_id ; @auth.on.threads owner filter          │
  │   historyScope=task ; A2A_ALLOWED_TOOL_CALL_RESULTS=ui_tool           │
  │   disable_a2a on HR deploy                                            │
  │   public cards without secrets                                        │
  │   PII detect→redact→audit on A2A payloads before publish              │
  └──────────────────────────────┬────────────────────────────────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     ┌────────────┐      ┌────────────┐       ┌────────────┐
     │ in-process │      │ A2A JSON-  │       │ HR LSD     │
     │ task()     │      │ RPC zone B │       │ disable_   │
     │ chunk-     │      │ billing    │       │ a2a        │
     │ analysts   │      │ PCI        │       │ parent-only│
     └────────────┘      └────────────┘       └────────────┘
```

**Trade-off matrix:**

| Axis | **B1 Hybrid: subagents inside + A2A across zones (recommended)** | **B2 A2A for every specialist** | **B3 Subagents-only (no A2A)** |
| --- | --- | --- | --- |
| **Cost** | In-process `task` ≈ GP isolation tax (08 **+0.8–1.0×** when used). A2A 3-round **~$1.34–$1.62 / conversation [inferred]** only on the billing hop | **2× tokens** per ping-pong round **[inferred]**; 6 full runs in the 3-round demo. CrewAI `max_turns=10` cap | No extra HTTP; still pay child prefixes. Cannot reach another cluster without inventing a private HTTP schema |
| **Latency** | Child `task` = no extra HTTP (08 GP 8-call **16,000 / 64,000 / 160,000 ms [inferred]** if parent waits). A2A hop **800 / 3,200 / 6,400 ms [inferred]** first-event + possible full-history replay | JSON-RPC + queue + **possible full history replay** + CrewAI **120,000 ms** client budget | Lowest hop count. Failure isolation = child error **string** to parent (11) |
| **Ops complexity** | Two deploys + `@auth` + TCK drift watch | Card version skew (AMP 0.2/0.3 vs LSD v1.0+v0.3 names; **no gRPC** on LSD). Schema mismatch CrewAI `DataPart` vs LSD text artifacts | One graph. No peer discovery |
| **Security posture** | Network + `@auth` only where a **zone boundary** exists; tool-result allowlist; HR not a peer | Confused deputy if `contextId` leaks; public cards; default all tool results published | Same process, **same credentials** — wrong for PCI billing |
| **Scalability** | Parallel chunk analysis stays in-process (`max_concurrent_analysts=3`). Billing scales on its own Agent Server | One run per `contextId` per hop; `ListTasks` history amplification | Fan-out is `task` gather — verify sibling-cancel semantics (08 #694 / #1698) |

**Decision.** **B1 wins** for enterprise: orchestrator uses **subagents** for private decomposition; **A2A** only for the billing agent in another compliance zone; `historyScope=task`, `A2A_ALLOWED_TOOL_CALL_RESULTS` to the UI tool only, `@auth` on both deployments; `disable_a2a` on HR. **B2 wins** when the specialists are already Crew/ADK in other clusters — A2A is the lingua franca; pin JSON-RPC; budget 2× tokens; CrewAI `timeout=120`, `max_turns=10`; unified trace via shared UUID `contextId`. **B3 wins** only inside one trust domain (RAG chunk-analysts, coding subtasks). Do not assume CrewAI AMP gRPC works against LangSmith. Fallback if billing A2A is open-circuit: **parent-only** (degraded quote, no card charge), not LocalShell and not a nested Claude loop.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| Dual harness | SDK `query()` **and** DA `execute` on one repo; mixed `bypassPermissions` vs `interrupt_on` | Duplicate diffs / two `/cost` / destructive `rm` | One inner harness; other as MCP/A2A **peer** |
| Split memory / tenancy | `CLAUDE.md` vs `AGENTS.md` vs OpenWiki; Claude shared `cwd`; CLI profile as “multi-tenant” | Agent ignores wiki; tenant B files in A | One instruction file (OpenWiki writes both); per-tenant `cwd`/`CLAUDE_CONFIG_DIR` or LSD threads |
| Managed Agents ≠ MDA | Anthropic Managed Agents ≠ LangSmith MDA | “We deployed the SDK to managed” | Distinct SKUs |
| A2A without identity / history exfil | Missing `@auth`; public card; UUID replay; `historyScope=context`; mis-cased `historyscope`; streaming ignore | Unauthenticated `SendMessage`; task 2 re-shows task 1 secrets | `@auth.on.threads`; DataPart allowlist; `historyScope=task`; `disable_a2a` |
| Bad `contextId`/`taskId` | `session-42`; completed id reused | `-32602` / `-32004` | Server-minted UUID; new task per turn |
| ACP MemorySaver / cancel / selectors | Demo saver; #5084 process-wide cancel; #4254 no factory | Reload wipes state; session B dies; Zed missing model picker | Postgres; one server/window until fix; factory+`modes`/`models` |
| LocalShell / `--sandbox agents` / project MCP | ACP demo host shell; flag eats token; `--trust-project-mcp`; `class_path` RCE | Host `execute`; wrong provider; unexpected tools | Remote sandbox; pin flags; admin `managed_config.toml`; never trust-project in unknown CI |
| RAG dump / MCP vs A2A mix-up | Skip `upload_files`; expect `contextId` on `/mcp` | Window blow; no continuity | Paths, k=4, 3 analysts; `/mcp` stateless, `/a2a` threaded |
| Version / naming skew | `dcode` pins 0.7.10; ACP Alpha; gRPC client; “ACP merged into A2A” | Missing harness behavior; `-32601`; wrong protocol | Pin matrix; ACP v1; JSON-RPC only; LangChain ACP = editor stdio |
| Ops | OpenWiki stale; `dcode -n` without `-S`; Windows; HITL timeout → new `messageId` | Wrong architecture; tests never run; install fail; double spend | CI `--update`; `-S` only in trusted CI; WSL; poll `GetTask` while `WORKING` |

No public Deep Agents ACP/A2A post-mortem corpus. Do not invent incidents. GitHub **#4254** and **#5084** are the concrete ACP defects.

---

## Key Takeaways

- Deep Agents is the **harness**; Code / ACP / A2A are **I/O adapters** around the same `CompiledStateGraph`. None of them replace `create_deep_agent`. A2A is **Agent Server**, not a `deepagents-a2a` package.
- **Sandbox-as-tool** (`dcode --sandbox`): loop on the host, tools on the guest, keys via **auth proxy**. **Agent-in-sandbox** (Claude Agent SDK): loop **inside** the guest, N sessions ⇒ N subprocesses, you build tenancy. Comparison drafted **2026-04-16**.
- A2A `contextId` **is** `thread_id` and **must be a UUID**. UUID ≠ authorization. Same `@auth` as `/runs`. Default history **replays the whole context**; set `historyScope=task`. Streaming silently ignores history options.
- ACP is **agent–editor** JSON-RPC over stdio (protocol **1**; v2 draft). The editor is the TCB, **not** a Zero-Trust PEP. Demo `MemorySaver` dies with the subprocess. #4254 (no selectors) and #5084 (cancel bleed) are real.
- RAG on Deep Agents = **your** retriever → `/retrieved/` paths → ≤3 chunk-analysts. Not a new index. OpenWiki is a **git-resident wiki** other agents `read_file`, not a second system prompt.
- Fallback: **ACP/A2A → direct `invoke` → refuse**. A2A peer down → **parent-only**. Code without sandbox → **no LocalShell**. PII is **detect → redact → audit** on editor logs **and** A2A payloads. Circuit breakers are **yours**.
- `$ per 1k` chat mix **[inferred] $223** (Sonnet 4.6, 10 calls, 2k/3k/800, 5m cache). ACP/Code MCP prefix ≈ **$242 / 1k**. A2A 3-round ≈ **$1.34–$1.62 / conversation**. `/cost` example **$1.03**/thread. No published editor p99 — use **[inferred] ms**.
- One harness per inner loop. Stack MCP (tools) × ACP (editor) × A2A (peers). MDA vs `dcode` vs ACP are **three ops envelopes**, not three runtimes.

---

## Interview Q&A

**Q1. What is the Deep Agents ecosystem, in one minute?**  
I treat Code, ACP, and A2A as I/O adapters around `create_deep_agent`, not new runtimes. The assembler still returns a LangGraph `CompiledStateGraph` with `recursion_limit` 9,999. `dcode` is that graph in a TUI, headless `-n`, or `--acp`. ACP is JSON-RPC over stdio to Zed/JetBrains/VS Code — protocol version 1. A2A is Agent Server `POST /a2a/{assistant_id}`, JSON-RPC only. OpenWiki is a separate CLI that writes Markdown other agents read. I do not confuse LangChain ACP with IBM’s old ACP that merged into A2A.

**Q2. Walk IDE ACP / A2A / `dcode` to the same graph.**  
Control plane: one `create_deep_agent` compile. ACP: editor `initialize` → `session/new` → `session/prompt` into `AgentServerACP` (prefer a factory so modes/models appear). A2A: agent card, then `SendMessage` with a UUID `contextId` that **is** `thread_id`. `dcode`: TUI, `-n` (shell off unless `-S`), or `--acp`. Data plane is the same ReAct loop. Persistence depends on the envelope — MemorySaver in the ACP demo, `.state/` in `dcode`, Postgres on Agent Server. I never bind LocalShell on untrusted cwd.

**Q3. Claude Agent SDK vs Deep Agents Code — sandbox story?**  
Claude Agent SDK is agent-in-sandbox: `query()` spawns a `claude` subprocess; the loop lives in the guest; keys usually sit there unless I add a proxy; N sessions are N processes; I build HTTP/auth/tenancy. Deep Agents Code is sandbox-as-tool: the LLM loop stays on my machine or gateway; `read_file`/`write_file`/`execute` target a remote sandbox; LangSmith can inject credentials via an auth proxy. SDK MIT, Claude Code proprietary. Managed Agents is a different Anthropic SKU — SDK code does not deploy onto it. Comparison drafted 2026-04-16. I do not nest both harnesses on one repo.

**Q4. Give me `$ per 1k` for Code/ACP vs the chat harness.**  
Inferred, not a SKU. I use the 08/13 mix inline: Claude Sonnet 4.6 at $3/$15 per MTok, 5m cache write 1.25× ($3.75) and read 0.1× ($0.30), 10 calls, 2,000-token cached prefix, 3,000 uncached in, 800 out. That is $0.2229/run → **$223 / 1k** cached, **$270 / 1k** uncached. Code/ACP add MCP schemas; `/context-doctor` showed +2,987 tokens — if that sits on the prefix I land around **$242 / 1k**. Unpublished `@file` buffers at +2k uncached/turn push toward **$302 / 1k**. A published `/cost` example is **$1.03** for one Sonnet 4.5 thread ($0.87+$0.16 subagents) → $1,030 of *that* shape, not a protocol fee. A2A 3-round demo is 6 full runs → about **$1.34** cached or **$1.62** uncached per conversation. I budget ACP hour-threads at **$1–3** each, so **$1k–$3k / 1k**, unpublished.

**Q5. What p50/p95/p99 do you put on ACP and A2A?**  
Nobody publishes editor or A2A hop percentiles. I contract in-process TTFT at **640 / 2,560 / 5,120 ms** inferred. ACP stdio extra **80 / 320 / 1,280 ms**, so first token **720 / 2,880 / 6,400 ms**. A2A Dedicated first-event **800 / 3,200 / 6,400 ms** (same path as Agent Server). A 10-call run **20,000 / 80,000 / 200,000 ms**. One A2A round (2 deploys) **40,000 / 160,000 / 400,000 ms**; the docs 3-round demo is **120,000 / 480,000 / 1,200,000 ms** if each hop is that 10-call shape. Sandbox create p99 **30,000 ms** from the documented 30 s wait bound. HITL **30,000 / 180,000 / 600,000 ms**, expire-deny. CrewAI’s 120 s is a client timeout, not LangSmith p99. I measure on my graph; I do not claim a vendor SLO.

**Q6. A2A `contextId` vs `taskId` vs ACP `session_id`.**  
`contextId` is `thread_id` and must be a UUID — `session-42` is `-32602`. Server mints on first message; I echo it. `taskId` is one run inside that thread; a new user turn gets a new task; completed ids return `-32004`; foreign ids `-32001`. JSON-RPC `metadata.thread_id` is ignored. ACP `session_id` is the editor session — I map it to a UUID `thread_id` if I need a checkpointer. UUID is not authorization; `@auth.on.threads` is.

**Q7. Is editor ACP a Zero-Trust PEP?**  
No. Stdio ACP makes the IDE the TCB — it can log every frame. `permissions=` is still fail-open FS-tools-only. MCP egress still needs a gateway: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, no token passthrough (RFC 8693), hash-pin name+description+schemas every `tools/call` (CVE-2025-54136). `--trust-project-mcp` and `class_path` are footguns. Ingress `/mcp` and `/a2a` need the same `@auth` as `/runs`. CLI allowlists do not replace that. Identity never comes from model JSON or from a UUID format check.

**Q8. PII — detect → redact → audit on this stack.**  
I scan ACP logs, A2A payloads (including history replay), traces, checkpoints, VFS, and sandbox env expansions. Detect with regex plus optional ML before persist. Redact/mask/hash so work continues; block PAN into MCP args, sandbox env, A2A publication, and ACP log export. Audit WORM of decisions — pre/post hashes, entity types, counts, detector, cid, `contextId`=`thread_id`, surface — not raw PAN. `historyScope=task` reduces disclosure but is not redaction. If ML is down I still regex-mask chat and I block PAN to foreign agents.

**Q9. Circuit breaker and fallback for ACP/A2A.**  
The libraries do not ship protocol breakers. I wrap A2A JSON-RPC and ACP stdio: closed → open → half-open with one probe. Fallback is **ACP/A2A → direct `invoke` of the same graph → deterministic refuse**. If the A2A **peer** is down I degrade to **parent-only** (subagents in-process). I never fail open to LocalShell, never auto-approve HITL, never skip `@auth` on the fallback. `dcode --max-retries 5` is model retry, not this breaker.

**Q10. When do I pick A2A vs `task` subagents?**  
Subagents for private decomposition in one process — RAG chunk-analysts, coding subtasks, fresh child window, single handoff. A2A when the peer is another team, another framework, or another compliance zone. Hybrid is the enterprise default: subagents inside, A2A only across the zone boundary, `historyScope=task`, tool-result allowlist, `disable_a2a` on HR. I do not use A2A as “network subagents” for k=4 chunk analysis.

**Q11. How does Deep Agents RAG differ from 01-rag?**  
01 owns loaders, splitters, embeddings, ANN, ACL at the index. Deep Agents RAG is orchestration: my `@tool` searches, I `upload_files` to `/retrieved/{id}/chunk_i.md`, I return paths, I fan out up to three `chunk-analyst` subagents, parent synthesizes. Tutorial: 14 pages, 589,579 chars, 782 chunks, k=4, 20 s fetch, <300 word summaries. I do not dump the corpus into the parent. Delimiters are not injection-proof. `permissions=` does not cover my custom search tool.

**Q12. What are the ACP/A2A footguns you actually pin?**  
`dcode` pins `deepagents==0.7.10` while I study 0.7.12. ACP is Alpha; cancel is process-wide (#5084); `--acp` without a factory hides Zed selectors (#4254). A2A: only JSON-RPC; `A2A-Version` ignored; streaming ignores `historyLength`; default publishes all tool results; one run per thread; TCK known-failures are a live contract. MemorySaver in the ACP quickstart is not production. Windows unsupported. I pin protocol 1, UUID `contextId`, remote sandbox, and one harness per inner loop.

---

## Key Numbers to Memorize

### Package / protocol / versions
| Number | What |
| --- | --- |
| **0.7.12** | SDK pin (08); Beta; Python `>=3.11,<4.0`; MIT |
| **0.1.65 / 0.7.10** | `deepagents-code` pin → `deepagents==0.7.10` (one patch behind) |
| **0.0.11** | `deepagents-acp` Alpha; `agent-client-protocol>=0.10.1` |
| **28,843** | GitHub stars at research fetch |
| **5,646,660** | `deepagents` PyPI last-month (08) — adoption, not cluster |
| **2026-04-16** | Deep Agents vs Claude Agent SDK comparison drafted |
| **ACP protocol 1 / v2 draft** | Wire `protocolVersion`; keep serving v1 peers |
| **A2A v1.0 JSON-RPC + v0.3 names** | gRPC / HTTP+JSON **not** implemented on Agent Server |
| **`langgraph-api>=0.4.21`** | A2A endpoint minimum (FilePart 0.12.0 / 0.13.0; A2UI/`historyScope` 0.15.0) |
| **#4254 / #5084** | `dcode --acp` missing selectors / ACP cancel not session-scoped |

### Tokens / RAG / CLI budgets
| Number | What |
| --- | --- |
| **~2k** | v0.7 harness prefix (08) — adapters add MCP schemas on top |
| **+2,987** | `/context-doctor` example unattributed token delta |
| **14 / 589,579 / 782 / k=4** | RAG tutorial pages / chars / chunks / search k |
| **20 s** | RAG HTTP fetch timeout per page |
| **3 / <300 words** | `max_concurrent_analysts` / analyst summary cap |
| **~150k tok** | **[inferred]** 589k chars @ 4 chars/tok if naively dumped |
| **10** | A2A `historyLength` max (`-32602` if larger) |
| **10 MiB** | Piped `dcode` stdin cap |
| **124 / 2** | `dcode -n` budget exit / missing `-n` exit |
| **5** | `dcode --max-retries` default (`0` disables) |
| **60 s / 5.0 s / 120 s** | `--startup-cmd` in `-n` / `js_eval` timeout / LocalShell default |
| **64 MiB / 256** | Interpreter memory / max PTC calls |
| **9,999** | Bound `recursion_limit` |
| **255** | Postgres `thread_id` max chars |
| **+13.7** | Terminal Bench 2.0 harness-only (not an ACP/A2A number) |

### $ / SKUs **[inferred]** where marked
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok (08/13 mix, restated) |
| **$3.75 / $0.30** | 5m cache write / cache read per MTok (1.25× / 0.1×) |
| **2,000 / 3,000 / 800** | Cached prefix / uncached in / out per call (10-call, 5m window, GP off) |
| **[inferred] $223 / $270 per 1k** | Chat harness cached / uncached |
| **[inferred] $242 / $302 per 1k** | +MCP prefix (~5k) / +2k uncached `@file` per turn |
| **$1.03** | `/cost` docs example (Sonnet 4.5 thread; $0.87+$0.16) |
| **[inferred] $1.34 / $1.62** | A2A 3-round (6 runs) cached mix / uncached $0.27×6 |
| **[inferred] $1k–$3k / 1k ACP threads** | $1–3 hour-equivalent; **not** published |
| **$50 / $0.50** | Session-cost warn / cold-cache warn delta |
| **120 s / 10–30 s** | CrewAI A2A client timeout example / spec webhook (unimplemented) |

### Latency (numeric ms) / security
| Number | What |
| --- | --- |
| **640 / 2,560 / 5,120 ms** | **[inferred policy]** in-process TTFT p50/p95/p99 |
| **80 / 320 / 1,280 ms** | **[inferred]** ACP stdio extra hop |
| **720 / 2,880 / 6,400 ms** | **[inferred policy]** ACP first model token |
| **800 / 3,200 / 6,400 ms** | **[inferred policy]** A2A Dedicated first-event |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** one ReAct cycle |
| **20,000 / 80,000 / 200,000 ms** | **[inferred]** 10-call run, GP off |
| **40,000 / 160,000 / 400,000 ms** | **[inferred]** A2A one round, 2 deploys (2× 10-call class) |
| **120,000 / 480,000 / 1,200,000 ms** | **[inferred]** A2A 3-round docs demo if each hop is 10-call |
| **5,000 / 15,000 / 30,000 ms** | **[inferred policy]** sandbox create; p99 = 09 **30 s** wait bound |
| **10 / 50 / 200 ms** | **[inferred policy]** checkpointer `sync` extra |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** HITL clock; p99 expire-deny |
| **60,000 / 5,000 / 120,000 ms** | Documented timeouts: startup-cmd / js_eval / LocalShell (not percentiles) |
| **detect → redact → audit** | PII on **editor logs and A2A payloads** before persist |
| **RFC 8707 / 8693** | MCP audience on authorize+token / no client-token passthrough |
| **8.8** | CVE-2025-54136 MCPoison (hash-pin every `tools/call`) |
| **`-32601` / `-32602` / `-32001` / `-32004`** | Method not found / bad params or non-UUID / foreign task / terminal task |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs. Revisit the 2026-04-16 comparison before a tenancy/managed-agents claim in H2 2026.
