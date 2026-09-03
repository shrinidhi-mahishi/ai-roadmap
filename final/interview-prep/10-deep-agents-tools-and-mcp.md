# Deep Agents Tools, MCP & Ecosystem

**Prep target**: Director/VP AI roles
**Framework**: LangChain Deep Agents >= 0.7.x (released March 2026)
**Package pins**: `deepagents==0.7.12` (PyPI 2026-09-01); `deepagents-code==0.1.65` (pins `deepagents==0.7.10`); `deepagents-acp==0.0.11` (Alpha)

---

## What Is This?

**The harness is one compiled graph. The ecosystem is how humans and other agents talk to it.**

Deep Agents becomes useful the moment the model can do something outside pure text generation. That "something" is the tool surface. In practice, the hardest part is not defining one function; it is combining local tools, built-in harness tools, and MCP servers without losing control over auth, session state, or error handling.

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

LangChain's product taxonomy is three **stacked** layers, not competing products: **runtime** (LangGraph, Temporal, Inngest) -> **framework** (`create_agent`, CrewAI, ADK) -> **harness** (Deep Agents SDK, Claude Agent SDK, Manus, coding CLIs).

Think of a restaurant pass. **The graph is the recipe** (already written in 08). **ACP is the ticket printer in the dining room** (Zed / JetBrains / VS Code). **A2A is the phone to the kitchen next door.** **`dcode` is the chef's own station** -- same recipe, optional prep room (remote sandbox) down the hall. You do not invent a second stove because you bought a phone.

## Why It Matters

For interviews, the key insight is that Deep Agents uses one `tools=` surface for both ordinary tools and MCP-loaded tools, but the operational concerns are different. Almost every "how do we ship a coding agent / multi-agent fleet?" interview now forks on ACP/A2A/MCP.

Trap answers: "ACP is a new runtime," "A2A is Deep Agents subagents," "`dcode` is Claude Code with a LangChain skin," "put the loop inside the LangSmith sandbox like Claude Agent SDK," "dump the RAG index into the parent prompt," "editor ACP is our Zero-Trust PEP," "`contextId` can be `session-42`."

---

## Architecture / System Design

### Three Tool Sources

There are three tool sources:

- **Custom callables and LangChain tools** you define in code
- **Built-in harness tools** such as `read_file`, `glob`, `task`, and sometimes `execute`
- **MCP-loaded tools** fetched from external servers

To the agent, these all become tools in the same selection loop. To you, they are not equivalent:

- Local tools run in your process
- Built-in tools are injected by the harness
- MCP tools come from separate processes or remote servers and need transport, auth, and session strategy

```
tool source
  -> plain callable / LangChain tool / MCP server
  -> optional MCP adapter layer
     -> MultiServerMCPClient(...)
     -> client.get_tools() or load_mcp_tools(session)
  -> create_deep_agent(..., tools=[...])
  -> Deep Agents mixes these with built-in tools
  -> model selects tool
  -> result returns as normal tool message or raises, depending on config
```

### System Topology (Full Data Flow)

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  LangSmith: ls_integration=deepagents ; stream.subagents              |
         |  dcode: project deepagents-code (override DEEPAGENTS_CODE_*)          |
         |  /cost via genai-prices ; /tokens /context /context-doctor            |
         |  A2A: OTel langsmith.metadata.thread_id <- contextId (UUID)           |
         |  ACP: editor ACP logs (Zed "dev: open acp logs") -- PII sink          |
         |  WORM audit: (cid, contextId=thread_id, surface, arg_digest)          |
         |  detect->redact->audit BEFORE editor logs / A2A payloads / traces     |
         +----------^---------------------^------------------^------------------+
                    | spans               | /cost + tokens    | audit events
+-------------------+---------------------+-------------------+-----------------+
| CONTROL PLANE  (construction + identity -- LLM-free; ACP/A2A are I/O)          |
|                                                                                |
|  create_deep_agent(...) -> CompiledStateGraph  recursion_limit 9_999           |
|  dcode: managed_config.toml > DEEPAGENTS_CODE_* > ~/.deepagents/config.toml   |
|  ACP: editor initialize(protocolVersion=1) -> session/new (cwd, model, mode)   |
|  A2A: Assistants metadata.a2a ; card GET /.well-known/agent-card.json          |
|  MCP ingress: POST /mcp (stateless). A2A: POST /a2a/{id} (stateful)           |
|  @auth.authenticate on /runs AND A2A/MCP -- without it, API-key owner only    |
+--------------------------------+----------------------------------------------+
                                 | same CompiledStateGraph
                                 v
+-----------------------------------------------------------------------+
| DATA PLANE  (untrusted token stream -- adapters serialize; graph loop) |
|                                                                        |
|  IDE ACP / A2A JSON-RPC / dcode CLI / SDK invoke  ->  SAME graph loop |
|                                                                        |
|  +---- TOOL PROXIES (least privilege -- ACP is NOT a PEP) -----------+|
|  | FS / task / execute (sandbox protocol) / eval (QuickJS)           ||
|  | MCP EGRESS: gateway PEP still required (permissions= != MCP)      ||
|  | dcode --sandbox {langsmith,daytona,modal,...}: sandbox-as-tool     ||
|  | ACP demo: LocalShellBackend on editor cwd -- host blast radius    ||
|  | A2A_ALLOWED_TOOL_CALL_RESULTS: which tool results become DataParts||
|  | RAG @tool writes /retrieved/{id}/chunk_i.md -- YOUR index, not DA ||
|  +-------------------------------------------------------------------+|
+-------+---------------+-------------------+-----------+---------------+
        |               |                   |           |
        v               v                   v           v
+-----------------------------------------------------------------------+
| PERSISTENCE LAYER  (surface chooses the saver)                         |
|  +----------------+ +----------------+ +--------------+ +----------+  |
|  | ACP demo       | | dcode TUI      | | Agent Server | | OpenWiki |  |
|  | MemorySaver()  | | ~/.deepagents/ | | Postgres     | | openwiki/|  |
|  | RAM; dies with | | .state/ threads| | contextId=   | | .claims/ |  |
|  | stdio process  | | file memory    | | thread_id    | | git-res  |  |
|  +----------------+ +----------------+ +--------------+ +----------+  |
+-----------------------------------------------------------------------+
```

### Protocol Triangle (Stackable)

| Protocol | Direction | Attachment |
| --- | --- | --- |
| **MCP** | Agent <-> **tools/data** | Egress: `tools=` from MCP servers. Ingress: `/mcp` exposes the graph as a **tool** (stateless) |
| **ACP** | **Editor** <-> coding agent | `deepagents-acp` / `dcode --acp` stdio |
| **A2A** | **Agent** <-> agent | Agent Server `/a2a/{assistant_id}` (stateful via `contextId`) |

You can stack all three: Zed ACP session -> Deep Agent -> MCP tools; a second fleet agent calls the same graph over A2A. Mixing MCP ingress with A2A is a common design error: MCP = "this agent is a tool"; A2A = "this agent is a conversational peer."

### Claude Agent SDK vs Deep Agents (Sandbox Patterns)

```
Claude Agent SDK / "agent-in-sandbox"
  [sandbox]
    LLM loop + tools + local FS
    API keys typically in the guest
    query() spawns a claude CLI subprocess over stdio
    N concurrent sessions => N subprocesses (isolate cwd + CLAUDE_CONFIG_DIR)

Deep Agents Code / "sandbox-as-tool"
  [laptop or long-lived container]  LLM loop, memory, tool dispatch
           | network
  [remote sandbox]  read_file / write_file / execute
    LangSmith auth proxy injects headers outside the guest
```

| Axis | Deep Agents | Claude Agent SDK |
| --- | --- | --- |
| Where the agent **loop** runs | Inside a sandbox **or** outside, using a sandbox as a tool | **Inside** a sandbox only |
| Execution backend | Pluggable: local, VFS, remote sandbox, custom | Local filesystem of that sandbox |
| Model | Any LangChain tool-calling provider ("100+ others") | Claude via Anthropic, Bedrock, Vertex, Azure |
| Deployment | Managed Deep Agents in LangSmith, or `langgraph build` image | Self-host the HTTP/auth/streaming layer. Claude managed agents is a **separate** product |
| Multi-tenancy | Docs: scoped threads, per-user sandboxes, RBAC | Build it yourself (`cwd` + `CLAUDE_CONFIG_DIR`) |
| License | MIT | SDK MIT; Claude Code itself proprietary |

---

## Core Concepts & Algorithms

### Tool Sources and Harness Injection

Deep Agents accepts plain Python callables, LangChain `@tool`-decorated functions, `BaseTool` instances, and tool dicts in `tools=`.

The harness also injects built-ins. A typical Deep Agent gets:
- `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`
- `task` (subagent spawning)
- `execute` when the backend supports shell execution

`tools=` is **additive** and never removes a built-in.

### MCP Integration

MCP support uses `langchain-mcp-adapters`. The standard entry point is `MultiServerMCPClient(...)`, whose connection map can define servers with transports such as `http` or `stdio`.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
    "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
}) as client:
    tools = await client.get_tools()
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )
```

**MCP Constructor Args:**

| Constructor arg | Default | Role |
| --- | --- | --- |
| `connections` | -- | `dict[name, Connection]` |
| `callbacks` | `None` | Progress, logging, elicitation |
| `tool_interceptors` | `None` | Onion around `tools/call`; **first interceptor is outermost** |
| `tool_name_prefix` | `False` | `"server_tool"` against collisions (`"math_add"`) |
| `handle_tool_errors` | `True` | `isError=True` -> `ToolMessage(status="error")` instead of raise |

### MCP Session Model

**Transports:** `"stdio"` (client spawns subprocess; **stateless client still opens a new session per tool call** unless `client.session()`); `"http"` / `"streamable_http"` (spec 2025-03-26; replaces 2024-11-05 HTTP+SSE); `"sse"` deprecated by spec, still accepted. Streamable HTTP: single endpoint POST+GET; `Accept: application/json, text/event-stream`; optional SSE; `MCP-Protocol-Version`; `Mcp-Session-Id` for stateful sessions.

**Stateful vs stateless:** default **stateless** = fresh `ClientSession` per invocation. `async with client.session("server_name")` + `load_mcp_tools(session)` when the server keeps context. Transport/session failures **always raise**. `handle_tool_errors=True` lets the **model** retry semantic failures; it will not retry TCP errors unless an interceptor catches them. Exception-catching interceptors **do not** fire on `isError=True` unless `handle_tool_errors=False`.

For the MCP control path:

1. Configure transports and auth
2. Load tools with `MultiServerMCPClient`
3. Optionally manage a persistent session with `client.session(...)`
4. Optionally inject runtime context or retries with `tool_interceptors=[...]`
5. Pass the resulting tools into `create_deep_agent`

### MCP Interceptors

Interceptors bridge MCP tools and LangGraph runtime state. They can:
- Inject user IDs or API keys from runtime context
- Read from the store
- Inspect or update runtime state
- Add headers dynamically via `request.override(headers=...)`
- Retry or short-circuit calls
- Rate limit

```python
@wrap_tool_call
def audit_all_tools(request, handler):
    log.info("Tool: %s, Args: %s", request["name"], request["args"])
    result = handler(request)
    log.info("Result size: %d bytes", len(str(result)))
    return result
```

Interceptor bridges (MCP servers cannot see LangGraph store/state): `request.runtime.context` / `store` / `state`; `request.override(args=..., headers=...)`; return `Command(update=..., goto=...)`. Do **not** pass the user OAuth token through. Official HTTP example `headers={"Authorization": "Bearer ..."}` is **static bearer** -- enterprise anti-pattern vs OAuth 2.1 + RFC 8707. Built-in `auth=` path: MCP SDK `OAuthClientProvider` (PRM, DCR, PKCE, 401 replay).

**Filtering:** `get_tools(server_name=...)`; application allowlist after `get_tools()` (not a first-class Deep Agents PDP); interceptor short-circuit. `structuredContent` is wrapped as `MCPToolArtifact` on `ToolMessage.artifact` and is **invisible to the model** unless an interceptor appends it -- default keeps context smaller.

### ACP v1 (Agent Client Protocol)

ACP "standardizes communication between coding agents and code editors or IDEs," analogous to LSP. **ACP is for agent-editor.** If the agent must call tools hosted by external servers, use **MCP**.

| Axis | Fact |
| --- | --- |
| Transport | Local agents = editor subprocesses, JSON-RPC over **stdio**. Remote HTTP/WebSocket = "work in progress" |
| Version | Stable protocol version **`1`**. Integer `protocolVersion` in `initialize`. **v2** draft |
| Session methods | `session/new`, `session/list`, `session/resume`, `session/close`, `session/prompt`, `session/cancel`, `session/update` |
| UX | User lives in the editor. Default text = Markdown. Diffs are first-class |
| Clients | Zed, JetBrains, VS Code (vscode-acp), Neovim, Toad, Emacs, Obsidian, Cursor/Windsurf |
| Demo agent | `CompositeBackend(default=LocalShellBackend, routes={"/memories/": StateBackend, "/conversation_history/": StateBackend})` + `interrupt_on` from session mode |
| Modes | `ask_before_edits`, `accept_edits`, `accept_everything` |

`deepagents-acp` starts stdio via `acp.run_agent(AgentServerACP(agent))`. `dcode --acp` runs the coding product as that server instead of the TUI.

**Known issues:** #4254 -- `dcode --acp` without factory hides Zed selectors. #5084 -- `session/cancel` has a process-wide `_cancelled` flag (cancelling session A cancels session B).

### A2A JSON-RPC (Agent Server)

A2A is Google's (now Linux Foundation) protocol. Every LangSmith Deployment auto-exposes MCP + A2A so an orchestrator in deployment A can call workers in B without a private HTTP schema.

**Wire:** Speaks A2A **v1.0 JSON-RPC**; also accepts **v0.3 method names**. **Only JSON-RPC.** gRPC and HTTP+JSON are **not** implemented.

| v1.0 | v0.3 | Status |
| --- | --- | --- |
| `SendMessage` | `message/send` | Yes |
| `SendStreamingMessage` | `message/stream` | Yes -- SSE |
| `GetTask` | `tasks/get` | Yes |
| `CancelTask` | `tasks/cancel` | Yes |
| `ListTasks` | -- | Yes |
| `GetExtendedAgentCard` | -- | Yes |
| `SubscribeToTask` | -- | **Not yet** -> `-32601` |
| `*TaskPushNotificationConfig` | -- | **Not yet** -> `-32601` |

**Identity mapping:**

| A2A | LangGraph / LangSmith |
| --- | --- |
| `contextId` | **`thread_id`**. UUID. Server mints on first message; echo it |
| `taskId` | One **run** inside the thread. New user turn = new task |
| Client `metadata.thread_id` | **Ignored** |

**Error codes:**

| Code | When |
| --- | --- |
| `-32601` | Method not found (SubscribeToTask, push-notification config) |
| `-32602` | Bad params: `historyLength > 10`, bad `historyScope`, non-UUID `contextId` |
| `-32001` | `taskId` minted by another agent |
| `-32004` | Message names a **terminal** task |

**History control:** Default `historyScope=context` replays **whole context** on responses. Set `historyScope=task` to reduce disclosure. `historyLength` **max 10** (`-32602` if larger). Streaming **ignores** both with **no error**. Mis-cased `historyscope` is silently ignored.

Disable: `langgraph.json` -> `"http": { "disable_a2a": true }` (sibling `disable_mcp`).

### Code CLI (`dcode`)

Product name **Deep Agents Code**; binary **`dcode`**. "Open source coding agent built on the Deep Agents SDK." Any tool-calling LLM; persistent memory; skills; approval gates. **Not officially supported on Windows**; WSL suggested. Python **>= 3.12**.

| Mode | Entry | Persistence |
| --- | --- | --- |
| Interactive TUI | `dcode` | Threads under profile `.state/`; `/threads` resume |
| Non-interactive | `dcode -n "..."` or piped stdin | Fresh thread per invocation; file memory/skills persist |
| ACP server | `dcode --acp` | stdio to editor |
| CI budget | `-n` + `--max-turns N` and/or `--timeout SECONDS` | Exit **124** on budget; without `-n`/stdin -> **2** |

| Knob | Default / documented value |
| --- | --- |
| `session_cost_threshold_usd` | Warns **once per thread** at **$50** |
| `cold_cache_min_delta_usd` | **$0.50** extra before asking |
| `[models].allowed` | Unset = all; **empty list = no model may be used** |
| `[interpreter]` `js_eval` | `timeout_seconds=5.0`, `memory_limit_mb=64`, `max_ptc_calls=256` |
| `--max-retries` | Default **5** (model-call retry, not A2A) |
| piped stdin | Max **10 MiB** |

Config precedence: (1) administrator `managed_config.toml` (2) `DEEPAGENTS_CODE_*` env (3) canonical env (4) `~/.deepagents/config.toml` (5) built-in default.

### RAG on Deep Agents

Deep Agents RAG does **not** ship an index. The retrieval tool is **your** `@tool` + vector store. The harness pattern:

1. Your `search_documentation` tool calls `similarity_search(k=4)`
2. Results are uploaded via `backend.upload_files()` under `/retrieved/{8-hex}/chunk_{i}.md`
3. Tool returns **paths** (not full content) to the agent
4. Agent fans out up to **3** `chunk-analyst` subagents via `task()`, each reading one file
5. Parent synthesizes. Never paste full chunks into the parent context.

Tutorial numbers (docs corpus): **14** pages; **20 s**/page fetch; splitter 1000/200 -> **782** chunks; **589,579** chars; `InMemoryVectorStore`; `k=4`; `max_concurrent_analysts=3`, **< 300 words** per analyst.

Skipping `upload_files` reintroduces ~**150k tok** @ 4 chars/tok into the parent -- the tutorial exists to avoid this.

Four orchestration patterns:

| Pattern | When |
| --- | --- |
| Skills-guided retrieval | Repeatable corpus procedure |
| Rubric-checked grounding | Strict groundedness (`deepagents>=0.6.5`, beta) |
| Todo-driven investigation | Multi-page investigation |
| Retrieve, offload, and delegate | Large chunks, keep parent context clean (worked tutorial) |

### Layer Pick (Products Page Conditions)

- **`create_agent`:** quick start; standard model/tool/loop; straightforward apps.
- **LangGraph:** fine-grained orchestration; durable long-running stateful agents.
- **Deep Agents SDK:** agents that run over long periods; complex multi-step tasks; predefined filesystem/bash/context-engineering tools.
- **Claude Agent SDK:** Claude-only, accept agent-in-sandbox + you build server/tenancy.
- **CrewAI:** role/crew prototyping; A2A is first-class.
- **Google ADK:** hierarchical agents; native A2A; Vertex/Agent Engine gravity.

---

## Token Economics & Cost Analysis

### Extra Hops by Surface

| Path | Hops before first model token [inferred] | Extra tokens vs in-process |
| --- | --- | --- |
| `create_deep_agent.invoke` in-process | 0 network | Baseline harness prefix ~**2k** after 0.7 |
| `dcode` TUI local, `--sandbox none` | 0 extra | TUI injects skills index + MCP schemas |
| `dcode --sandbox langsmith` | + sandbox create/ready (LangSmith wait default **30 s**) + per-tool RTT | File bytes off host; model still on laptop |
| ACP stdio | + JSON-RPC serialize through editor | Editor project context in addition to `AGENTS.md` |
| A2A `message/send` | Client -> Agent Server -> queue -> worker -> model | Default history replays prior tasks |
| A2A `message/stream` | Same + SSE | Streaming **ignores** `historyScope`/`historyLength` |
| A2A orchestrator <-> worker (2 deploys) | **Two** full agent loops + two model bills per round | Each hop is a full Deep Agent run |

No ACP/A2A **protocol surcharge** is billed by LangChain.

### $ per 1k Runs -- Chat Harness Mix [inferred]

Assumptions: Model **`anthropic:claude-sonnet-4-6`**; **10** model calls inside one 5-minute window; GP **off**; cached prefix **2,000** tokens; dynamic uncached **3,000** tokens/call; output **800** tokens/call. Cache: **1x 5m write** + **9x reads** of the 2k prefix.

| Component | Tokens x unit | USD / run |
| --- | --- | --- |
| Cache write | 2,000 x $3.75 / 1e6 | $0.00750 |
| Cache reads | 9 x 2,000 x $0.30 / 1e6 | $0.00540 |
| Uncached input | 10 x 3,000 x $3 / 1e6 | $0.09000 |
| Output | 10 x 800 x $15 / 1e6 | $0.12000 |
| **Chat harness total / run** | | **$0.2229** |
| **Chat harness / 1,000 runs** | | **$223** |

Same run **without** prompt caching = **$0.270 / run -> $270 / 1k**. Caching saves ~**$47 / 1k** at a 2k prefix.

### ACP/Code Prefix Tax [inferred]

If MCP schemas add +2,987 tokens to the cached prefix (2,000 + 2,987 = ~5k):

| Session type | Token sketch | / run | / 1k |
| --- | --- | --- | --- |
| Chat harness 10-call, 5m cache, GP off | 2k prefix + 30k uncached + 8k out | **$0.2229** | **$223** |
| Same + MCP schemas on prefix (~+3k) | 5k prefix, same uncached/out | **$0.2422** | **$242** |
| Same + editor @file +2k uncached/turn | 5k prefix + 50k uncached + 8k out | **$0.3022** | **$302** |
| `dcode` coding hour (`/cost` docs example) | Sonnet 4.5 thread; $0.87+$0.16 subagents | **$1.03** | **$1,030** |
| A2A 3-round ping-pong (docs demo) | **2 agents x 3 rounds = 6** full runs | **$1.34-$1.62** | **$1,337-$1,620** |
| ACP hour-equivalent thread | dcode TUI + unpublished @file | **$1-3** [inferred] | **$1,000-$3,000** |
| RAG retrieve-offload-delegate | 1 search + <=3 analysts + parent synthesize; k=4 | Cheaper than stuffing 782 chunks | -- |
| Naive dump 589,579 chars into parent | ~150k tok @ 4 chars/tok | ~$0.45 input once + window blow | -- |

### Latency SLA Targets [inferred policy]

| Path | p50 | p95 | p99 | Grounding |
| --- | --- | --- | --- | --- |
| **In-process `invoke` streaming TTFT** | **640 ms** | **2,560 ms** | **5,120 ms** | Stream; cache-warm prefix |
| **ACP stdio extra serialize hop** | **80 ms** | **320 ms** | **1,280 ms** | Local JSON-RPC |
| **ACP first model token** | **720 ms** | **2,880 ms** | **6,400 ms** | 640+80 / 2,560+320 / 5,120+1,280 |
| **A2A `SendMessage` first-event (warm)** | **800 ms** | **3,200 ms** | **6,400 ms** | JSON-RPC + persist + queue + worker + TTFT |
| **One ReAct cycle (model + local FS)** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Local VFS is not the tail |
| **10-call research run** | **20,000 ms** | **80,000 ms** | **200,000 ms** | Cost-section shape |
| **A2A one round, 2 deploys** | **40,000 ms** | **160,000 ms** | **400,000 ms** | 2 x 10-call run class |
| **A2A 3-round docs demo (6 hops)** | **120,000 ms** | **480,000 ms** | **1,200,000 ms** | 3 x two-agent round |
| **Sandbox create/ready** | **5,000 ms** | **15,000 ms** | **30,000 ms** | p99 = documented 30 s wait bound |
| **MCP `tools/call` HTTP** | **80 ms** | **400 ms** | **2,000 ms** | HTTP tool class |
| **HITL `interrupt_on`** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds-minutes; expire -> deny |
| **Checkpointer `sync` extra** | **10 ms** | **50 ms** | **200 ms** | ACP MemorySaver is RAM (0 ms) |

### Throughput / Back-Pressure

- A2A: one run per `contextId`; new turn = new `taskId`
- Editor sessions: N windows => N stdio processes **or** one multiplexed `AgentServerACP`
- `dcode -n` + `--max-turns` / `--timeout` exit 124 in CI
- `[models].allowed` empty list = **no model** (admit deny)
- `historyLength>10` is `-32602` -- fail closed
- Streaming **ignores** history options
- `recursion_limit` **9,999**
- Piped stdin max **10 MiB**

---

## Trade-offs & Failure Modes

### Failure Taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | Provider 429/5xx; A2A worker blip; ACP stdio stall; sandbox allocate | Full-jitter retries on **idempotent** reads. **Do not** retry `SendMessage` with a new `messageId` on an interrupted thread |
| **Permanent** | Non-UUID `contextId` (`-32602`); completed `taskId` (`-32004`); gRPC client against Agent Server (not implemented); ACP v2-only client against v1 agent | Fail closed. Negotiate ACP v1. Do not mint `session-42` |
| **Poison-pill A2A** | Open `/a2a/{uuid}` if auth is none; `contextId` replay; default **all** tool results as `DataPart`; full-context history replay | `@auth` + `@auth.on.threads` owner filter; `A2A_ALLOWED_TOOL_CALL_RESULTS`; `historyScope=task`; `disable_a2a` |
| **Poison-pill ACP** | Malicious project `.env` / Makefile; skill symlink escape; `--trust-project-mcp` in CI; `-S all`; `accept_everything` auto-approves shell; #5084 cancel bleed | Remote sandbox; admin `managed_config.toml`; never `-S all` on untrusted ACP |
| **Poison-pill RAG** | k=50 full text to parent; index without ACL on shared StateBackend; trust `# Source:` header | Paths only; k=4; per-tenant store + tool filter |
| **Dual harness** | Claude SDK `query()` subprocess **and** Deep Agents `execute` on same repo | One inner harness; wrap the other as MCP/A2A **peer** |
| **Denial of wallet** | A2A 6-run ping-pong; default history replay; 9,999 loop; `--max-retries 5` on 429 | `max_turns`; `historyScope=task`; product cap |

### Common Failure Modes

| Failure | Cause | Mitigation |
| --- | --- | --- |
| Dual harness | SDK `query()` **and** DA `execute` on one repo | One inner harness; other as MCP/A2A **peer** |
| Split memory / tenancy | `CLAUDE.md` vs `AGENTS.md` vs OpenWiki | One instruction file; per-tenant `cwd`/`CLAUDE_CONFIG_DIR` |
| Managed Agents != MDA | Anthropic Managed Agents != LangSmith MDA | Distinct SKUs |
| A2A without identity | Missing `@auth`; public card; UUID replay | `@auth.on.threads`; DataPart allowlist; `disable_a2a` |
| Bad `contextId`/`taskId` | `session-42`; completed id reused | Server-minted UUID; new task per turn |
| ACP MemorySaver / cancel | Demo saver; #5084 process-wide cancel | Postgres; one server/window until fix |
| LocalShell in ACP demo | ACP demo host shell; flag eats token | Remote sandbox; pin flags; admin config |
| RAG dump | Skip `upload_files`; expect `contextId` on `/mcp` | Paths, k=4, 3 analysts; `/mcp` stateless |
| Version skew | `dcode` pins 0.7.10; ACP Alpha; gRPC client | Pin matrix; ACP v1; JSON-RPC only |
| MCP stateless confusion | `client.get_tools()` alone breaks stateful servers | Use `client.session(...)` for stateful servers |
| Large tool catalogs | Bloats prompt and worsens tool selection | Filter `get_tools()`; use `tool_name_prefix=True` |

### NFRs and Explicit Trade-offs

| NFR | Production Stance | Competes With |
| --- | --- | --- |
| **Availability** | Product SLO is the **graph**. ACP/A2A/sandbox are best-effort extra hops. Circuit-open on A2A -> **direct `invoke`** -> refuse | Editor UX vs worker availability |
| **RPO ACP MemorySaver** | **Empty on subprocess death**. Official quickstart does **not** wire Postgres | Demo velocity vs resume |
| **RPO `dcode` `.state/`** | Last persisted thread under profile dir | Laptop disk vs tenant isolation |
| **RPO A2A history** | Default full-context replay is a **disclosure** RPO: task 2 re-shows task 1 secrets | Debugger convenience vs exfil |
| **RPO sandbox** | Last snapshot / provider TTL. `--sandbox-id` skips cleanup -- orphan spend | Reattach vs leak |
| **Compliance** | **Not provided by ACP/A2A packages.** Editor is TCB on stdio. A2A without `@auth` = anyone with URL | Time-to-debug vs residency |

---

## Production Patterns & Best Practices

### Circuit Breaker for ACP/A2A

Independent breakers: **A2A backend**, **ACP stdio/session**, **direct invoke**, **sandbox allocate**, **checkpointer**. An A2A 429 must not fail open to LocalShell **and** must not skip `@auth`.

```
        A2A JSON-RPC 5xx/timeout | ACP stdio dead | provider 429 | sandbox 503
  +----------+  ------------------------------------------------>  +----------+
  |  CLOSED  |                                                       |   OPEN   |
  |  adapter |  success resets count                                 | FAIL FAST|
  +----+-----+                                                       | fallback |
       ^                                                             +----+-----+
       | probe OK                                                         | cooldown
       |                                                            +-----v------+
       +----------- probe allow ----------------------------------- | HALF-OPEN  |
                    probe fail -> stay OPEN                          | 1 probe   |
                                                                    +------------+
```

**Thresholds [policy]:**

| Trip condition | Closed -> open | Half-open probe | Fallback |
| --- | --- | --- | --- |
| A2A JSON-RPC 5xx / timeout | consecutive >= **5** | One `GetTask` | **A2A -> direct `graph.invoke` -> refuse** |
| ACP stdio EOF / subprocess death | consecutive >= **3** | One `initialize` | **ACP -> HTTP/SDK `invoke` -> refuse**. Never LocalShell |
| Sandbox pool empty / 503 | allocate >= **3** | One allocate | Queue or 503. **Never** `--sandbox none` |
| MCP gateway 5xx | error-rate | One `tools/list` | Fail closed on egress tools |
| Checkpointer timeout | consecutive >= **3** | One write | Fail closed; do **not** switch to MemorySaver |

**Fallback chain (required interview answer):** **ACP/A2A adapter -> direct `create_deep_agent` `invoke` (same graph) -> deterministic refuse.** Never: circuit open -> LocalShell. Never: HITL timeout -> auto-approve. Never: A2A down -> skip `@auth`. Parent-only fallback for A2A: if the **peer** is down, orchestrator continues with **subagents only** rather than hanging the user.

### Zero-Trust MCP (Editor ACP is NOT a PEP)

ACP stdio means the **IDE process parents the agent**, reads stdout, and can log every JSON-RPC frame. The editor is the **TCB**, not a policy enforcement point. `permissions=` still covers **built-in FS tools only**. MCP `tools/call`, custom tools, `execute`, and A2A `DataPart` publication are **out of that PDP**.

| Zero-Trust control | What you implement |
| --- | --- |
| **Transport** | OAuth 2.1 + PKCE `S256`. RFC **8707** `resource` = MCP server URI. **MUST NOT** passthrough the client token upstream (RFC **8693** exchange). stdio is **outside** this OAuth profile |
| **Hash-pin / allowlist** | `toolSurfaceHash` over canonical JSON. Re-verify every `tools/call`. CVE-2025-54136 (MCPoison) CVSS **8.8**. Name filter != hash pin |
| **Identity** | Verified access token / `runtime.server_info.user.identity`. **Never** the LLM. ACP `AgentSessionContext` is not authn. A2A `contextId` UUID is not a principal |
| **Capability** | Model proposes; **PEP disposes**. `interrupt_on` is review queue, not authz |

**Tool-level RBAC:**

| Control | What it binds | What it is not |
| --- | --- | --- |
| `permissions=` | Built-in FS path PDP, fail-open | MCP, `execute`, custom tools |
| `interrupt_on` / ACP modes | Review queue | Authorization. `accept_everything` auto-approves shell |
| `A2A_ALLOWED_TOOL_CALL_RESULTS` | Which tool names become `DataPart`s | Authn |
| `@auth.on.threads` | Owner filter / 403 | `contextId` format check |
| `managed_config.toml` / `[models].allowed` | Admin beats user | Agent Server PEP |
| `--trust-project-mcp` | Explicit opt-in for repo-controlled MCP | Default-deny in CI |

Correct story: **gateway PEP on egress MCP** + **`@auth` on ingress A2A/MCP/`/runs`** + **remote sandbox** for untrusted ACP cwd + **never LocalShell**.

### PII Pipeline -- detect -> redact -> audit

ACP frames include prompts, diffs, and possibly secrets. A2A default history replays tool results.

Scan sinks: **editor ACP logs**, **A2A JSON-RPC payloads** (including `historyScope=context` replays), LangSmith traces, checkpoints/VFS, model I/O, webhook bodies, HITL UI, sandbox setup env expansions.

1. **Detection.** Dual-gate: regex + ML NER. Scan `session/prompt` bodies, `@file` buffers, A2A results, tool `DataPart`s. If ML down: **fail closed to mask** on chat; **block** on MCP args / A2A publication.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens. Strip from VFS **and** message channel **and** ACP log exports. `historyScope=task` reduces disclosure but is not redaction.
3. **Audit trail (WORM).** Log decisions: pre/post hashes, entity types, action, detector, `correlation_id`, `contextId`=`thread_id`, surface. A tool call without an audit row is a control-plane bug.

### Durable Execution: ACP Session vs LangGraph Thread

| Concept | ACP | LangGraph / A2A |
| --- | --- | --- |
| Connection | One stdio subprocess per editor | Process or Agent Server worker |
| Conversation | ACP `session_id` from `session/new` | `thread_id` on checkpointer; A2A `contextId` **is** that UUID |
| Persistence | Demo **`MemorySaver()`** -- dies with subprocess | Production: Postgres. `dcode` TUI: `~/.deepagents/.state/` |
| Cancel | `session/cancel` (#5084 process-wide) | Run cancel / graph interrupt; A2A `CancelTask` |
| Resume | ACP `session/resume` (capability-negotiated) | Same `thread_id` + checkpointer |

A2A + HITL: `interrupt()` writes checkpoint and releases the worker; the A2A task stays non-terminal until `Command(resume=...)`. Clients that treat HTTP timeout as failure will **duplicate** turns if they retry `SendMessage` on the same `contextId` while the run is interrupted.

---

## Code Examples

### MCP Integration with Interceptors

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
    "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
}) as client:
    tools = await client.get_tools()
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )
```

### Stateful MCP Session

```python
# For servers that maintain state across calls:
async with client.session("github") as session:
    tools = await load_mcp_tools(session)
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )
```

### Production Adapter Runtime with Circuit Breakers

```python
#!/usr/bin/env python3
"""Ecosystem adapters around one compiled Deep Agents graph.

Fallback: ACP/A2A surface -> direct graph.invoke -> deterministic refuse.
contextId is thread_id and MUST be a UUID. Never LocalShellBackend.
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- UUID validation for A2A contextId ---
def require_thread_uuid(context_id: str) -> str:
    """A2A contextId == LangGraph thread_id. Non-UUID -> -32602 class error."""
    try:
        return str(uuid.UUID(context_id))
    except ValueError as exc:
        raise InvokeError("permanent", "a2a_-32602_invalid_thread_id") from exc

# --- Circuit breaker ---
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else:
                raise RuntimeError(f"circuit_open:{self.name}")

    def record_success(self):
        self._failures = 0; self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# --- Adapter fallback chain ---
class InvokeError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "transient" | "permanent"

@dataclass
class AdapterRuntime:
    """Fallback: ACP/A2A -> direct invoke -> refuse."""
    a2a_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("a2a"))
    acp_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("acp"))
    direct_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("direct"))

    def run(self, user_text, *, context_id, tenant_id):
        thread_id = require_thread_uuid(context_id)
        # Try A2A first, then direct invoke, then refuse
        for breaker, surface in [
            (self.a2a_breaker, "a2a"),
            (self.direct_breaker, "direct"),
        ]:
            try:
                breaker.allow()
                result = self._invoke(surface, user_text, thread_id)
                breaker.record_success()
                return result
            except Exception:
                breaker.record_failure()
                continue
        return {"status": "refused", "reason": "all_surfaces_failed"}
```

### MCP Gateway PEP (Hash-Pinned Tools)

```python
@dataclass
class McpGateway:
    allowed_tools: set[str]
    surface_hash: dict[str, str]
    disabled: set[str] = field(default_factory=set)

    def _pin(self, name, description, schema):
        blob = json.dumps(
            {"name": name, "description": description, "inputSchema": schema},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def call(self, name, args, *, description, schema):
        if name in self.disabled or name not in self.allowed_tools:
            return {"status": "error", "reason": "mcp_tool_disabled"}
        expected = self.surface_hash.get(name)
        got = self._pin(name, description, schema)
        if expected and got != expected:
            self.disabled.add(name)
            return {"status": "error", "reason": "mcp_hash_drift"}
        return self._execute_tool(name, args)
```

---

## Interview Q&A

**Q1. What is the Deep Agents ecosystem, in one minute?**
I treat Code, ACP, and A2A as I/O adapters around `create_deep_agent`, not new runtimes. The assembler still returns a LangGraph `CompiledStateGraph` with `recursion_limit` 9,999. `dcode` is that graph in a TUI, headless `-n`, or `--acp`. ACP is JSON-RPC over stdio to Zed/JetBrains/VS Code -- protocol version 1. A2A is Agent Server `POST /a2a/{assistant_id}`, JSON-RPC only. I do not confuse LangChain ACP with IBM's old ACP that merged into A2A.

**Q2. Do MCP tools use a different Deep Agents API than normal tools?**
No. They end up on the same `tools=` surface. The difference is in how you load and manage them: `MultiServerMCPClient` connects to MCP servers, loads their tool definitions, and returns LangChain-compatible tools.

**Q3. When should I use `client.session(...)` plus `load_mcp_tools(session)`?**
When the MCP server is stateful and you need a persistent session instead of a fresh session per call.

**Q4. How are MCP tool failures handled by default?**
Tool execution errors are returned to the model as failed tool messages (`ToolMessage(status="error")`). If you want exceptions instead, set `handle_tool_errors=False`. `handle_tool_errors` maps semantic `isError`, not TCP. Transport failures always raise.

**Q5. Why are interceptors important?**
They bridge runtime context into MCP calls and give you middleware-like control for retries, auth injection, rate limiting, and request rewriting. MCP servers cannot see LangGraph store/state on their own.

**Q6. Claude Agent SDK vs Deep Agents Code -- sandbox story?**
Claude Agent SDK is agent-in-sandbox: `query()` spawns a `claude` subprocess; the loop lives in the guest; keys usually sit there unless I add a proxy; N sessions are N processes. Deep Agents Code is sandbox-as-tool: the LLM loop stays on my machine or gateway; `read_file`/`write_file`/`execute` target a remote sandbox; LangSmith can inject credentials via an auth proxy. SDK MIT, Claude Code proprietary. Managed Agents is a different Anthropic SKU. Comparison drafted 2026-04-16.

**Q7. A2A `contextId` vs `taskId` vs ACP `session_id`.**
`contextId` is `thread_id` and must be a UUID -- `session-42` is `-32602`. Server mints on first message; I echo it. `taskId` is one run inside that thread; a new user turn gets a new task; completed ids return `-32004`; foreign ids `-32001`. JSON-RPC `metadata.thread_id` is ignored. ACP `session_id` is the editor session -- I map it to a UUID `thread_id` if I need a checkpointer. UUID is not authorization; `@auth.on.threads` is.

**Q8. Is editor ACP a Zero-Trust PEP?**
No. Stdio ACP makes the IDE the TCB -- it can log every frame. `permissions=` is still fail-open FS-tools-only. MCP egress still needs a gateway: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, no token passthrough, hash-pin every `tools/call` (CVE-2025-54136). Ingress `/mcp` and `/a2a` need the same `@auth` as `/runs`.

**Q9. Give me `$ per 1k` for Code/ACP vs the chat harness.**
Inferred, not a SKU. Chat harness: **$223 / 1k** cached, **$270 / 1k** uncached. Code/ACP with MCP schemas: **$242 / 1k**. With editor `@file` buffers: **$302 / 1k**. Published `/cost` example: **$1.03** per Sonnet 4.5 thread. A2A 3-round demo: **$1.34-$1.62** per conversation. ACP hour-threads: **$1-3** each, so **$1k-$3k / 1k**. No published editor p99.

**Q10. What p50/p95/p99 do you put on ACP and A2A?**
Nobody publishes these. I contract in-process TTFT at **640 / 2,560 / 5,120 ms**. ACP stdio extra **80 / 320 / 1,280 ms**, so first token **720 / 2,880 / 6,400 ms**. A2A first-event **800 / 3,200 / 6,400 ms**. A 10-call run **20,000 / 80,000 / 200,000 ms**. One A2A round (2 deploys) **40,000 / 160,000 / 400,000 ms**. HITL **30,000 / 180,000 / 600,000 ms**, expire-deny.

**Q11. MCP sessions and streaming -- what do people get wrong?**
Adapter default is a **new session per `tools/call`**. That is not the stdio process lifetime. Stateful `client.session()` is for servers that keep context, scoped to the **run**. `handle_tool_errors` maps semantic `isError`, not TCP. v3 `stream.subagents` is the product UI; `subgraphs` is Pregel. Disconnect does not cancel the Agent Server worker; rejoin needs `thread_id`. Streaming does not save tokens.

**Q12. Circuit breaker and fallback for ACP/A2A.**
The libraries do not ship protocol breakers. I wrap A2A JSON-RPC and ACP stdio: closed -> open -> half-open with one probe. Fallback: **ACP/A2A -> direct `invoke` of the same graph -> deterministic refuse**. If the A2A **peer** is down I degrade to **parent-only** (subagents in-process). I never fail open to LocalShell, never auto-approve HITL, never skip `@auth`.

**Q13. When do I pick A2A vs `task` subagents?**
Subagents for private decomposition in one process -- RAG chunk-analysts, coding subtasks. A2A when the peer is another team, another framework, or another compliance zone. Hybrid is the enterprise default: subagents inside, A2A only across the zone boundary, `historyScope=task`, tool-result allowlist, `disable_a2a` on HR. I do not use A2A as "network subagents" for k=4 chunk analysis.

**Q14. How does Deep Agents RAG differ from standard RAG?**
Standard RAG owns loaders, splitters, embeddings, ANN. Deep Agents RAG is orchestration: my `@tool` searches, I `upload_files` to `/retrieved/{id}/chunk_i.md`, I return paths, I fan out up to three `chunk-analyst` subagents, parent synthesizes. I do not dump the corpus into the parent. Delimiters are not injection-proof. `permissions=` does not cover my custom search tool.

**Q15. What are the ACP/A2A footguns you actually pin?**
`dcode` pins `deepagents==0.7.10` while I study 0.7.12. ACP is Alpha; cancel is process-wide (#5084); `--acp` without a factory hides Zed selectors (#4254). A2A: only JSON-RPC; `A2A-Version` ignored; streaming ignores `historyLength`; default publishes all tool results; one run per thread. MemorySaver in the ACP quickstart is not production. I pin protocol 1, UUID `contextId`, remote sandbox, and one harness per inner loop.

---

## System Design Scenarios

### Scenario 1: Internal Coding Assistant (Deep Agents Code + Remote Sandbox)

**Problem.** Multi-tenant internal IDE/CLI coding agent for ~500 engineers. Untrusted customer repos (SOC2). Board wants model flexibility. Same graph should serve Zed ACP, `dcode` CI, and Agent Server for Slack/web.

**Recommended: `dcode` or ACP-wrapped `create_deep_agent` + remote sandbox, never LocalShell.**

```
  +----------+   +-------------------------------------------------------------+
  | IdP/PEP  |-->| CONTROL: one create_deep_agent graph                        |
  | JWT ->   |   |   dcode --acp OR AgentServerACP(factory, modes/models)      |
  | identity |   |   CompositeBackend default=REMOTE SANDBOX not LocalShell    |
  |          |   |   PostgresSaver (not MemorySaver)  thread_id UUID           |
  |          |   |   managed_config.toml ; [models].allowed                    |
  |          |   |   MCP EGRESS gateway PEP (RFC 8707, hash-pin)               |
  +----------+   +------------------------------+------------------------------+
                                                 v
  +---------------------------------------------------------------------------+
  | DATA: loop on gateway/laptop ; execute/read_file on guest                  |
  |   --sandbox langsmith|daytona|modal  --sandbox-id reattach                 |
  |   auth PROXY -- keys never in guest                                        |
  |   CI: dcode -n --max-turns --timeout -> exit 124                           |
  |   Slack/web: SAME graph on Agent Server                                    |
  +---------------------------------------------------------------------------+
```

| Axis | `dcode` / ACP + remote sandbox | Claude Agent SDK self-hosted | Claude Managed Agents |
| --- | --- | --- | --- |
| **Cost** | $223-$302 / 1k + sandbox units | Same Claude tokens + subprocess fleet | Anthropic runtime SKU |
| **Latency** | ACP first token 720/2,880/6,400 ms; sandbox create p99 30,000 ms | Subprocess TTFT unpublished | Hosted REST; unpublished |
| **Security** | Sandbox-as-tool + auth proxy; editor TCB | Keys typically **in guest**; richer permission modes | Anthropic session isolation |
| **Scalability** | Agent Server threads + per-user sandbox | Linear subprocesses | Anthropic control plane |

**Decision.** `dcode` + remote sandbox wins for 500 engineers, mixed models, untrusted repos. Deploy the **same graph** on Agent Server for web/Slack. Claude SDK as an optional MCP/A2A peer -- do not nest two harnesses.

### Scenario 2: Multi-Agent Fleet (A2A vs Subagents)

**Problem.** Platform composes research, billing, and HR specialists. Research is same trust domain. Billing is another compliance zone (PCI). HR must not be a peer.

**Recommended: Hybrid -- subagents inside, A2A across zones.**

```
  +---------------------------------------------------------------------------+
  | CONTROL: orchestrator Deep Agent  (one trust domain)                       |
  |   subagents/task for private decomposition (chunk-analysts, k=4)           |
  |   A2A ONLY to billing assistant in zone B                                  |
  |   contextId UUID = thread_id ; @auth.on.threads owner filter               |
  |   historyScope=task ; A2A_ALLOWED_TOOL_CALL_RESULTS=ui_tool               |
  |   disable_a2a on HR deploy                                                |
  +---------------------------------------------------------------------------+
                                 |
            +--------------------+--------------------+
            v                    v                    v
     +------------+      +------------+       +------------+
     | in-process |      | A2A JSON-  |       | HR LSD     |
     | task()     |      | RPC zone B |       | disable_   |
     | chunk-     |      | billing    |       | a2a        |
     | analysts   |      | PCI        |       | parent-only|
     +------------+      +------------+       +------------+
```

| Axis | Hybrid (recommended) | A2A for every specialist | Subagents-only |
| --- | --- | --- | --- |
| **Cost** | In-process `task` + A2A only on billing hop | **2x tokens** per ping-pong round; 6 full runs in 3-round demo | No extra HTTP; cannot reach another cluster |
| **Latency** | No extra HTTP for children; A2A hop 800-6,400 ms first-event | JSON-RPC + queue + possible full history replay | Lowest hop count |
| **Security** | Network + `@auth` only at zone boundary; tool-result allowlist | Confused deputy if `contextId` leaks; public cards | Same process, same credentials -- wrong for PCI |

**Decision.** Hybrid wins for enterprise. B2 wins when specialists are already Crew/ADK in other clusters. B3 wins only inside one trust domain. Fallback if billing A2A is open-circuit: **parent-only** (degraded quote), not LocalShell.

---

## Key Numbers to Memorize

### Package / Protocol / Versions
| Number | What |
| --- | --- |
| **0.7.12** | SDK pin (Beta; Python >=3.11; MIT) |
| **0.1.65 / 0.7.10** | `deepagents-code` pin -> `deepagents==0.7.10` (one patch behind) |
| **0.0.11** | `deepagents-acp` Alpha |
| **28,843** | GitHub stars at research fetch |
| **2026-04-16** | Deep Agents vs Claude Agent SDK comparison drafted |
| **ACP protocol 1 / v2 draft** | Wire `protocolVersion`; keep serving v1 peers |
| **A2A v1.0 JSON-RPC + v0.3 names** | gRPC / HTTP+JSON **not** implemented |
| **#4254 / #5084** | ACP missing selectors / cancel not session-scoped |

### Tokens / RAG / CLI Budgets
| Number | What |
| --- | --- |
| **~2k** | v0.7 harness prefix -- adapters add MCP schemas on top |
| **+2,987** | `/context-doctor` example unattributed token delta |
| **14 / 589,579 / 782 / k=4** | RAG tutorial pages / chars / chunks / search k |
| **3 / <300 words** | `max_concurrent_analysts` / analyst summary cap |
| **~150k tok** | 589k chars @ 4 chars/tok if naively dumped |
| **10** | A2A `historyLength` max (`-32602` if larger) |
| **10 MiB** | Piped `dcode` stdin cap |
| **124 / 2** | `dcode -n` budget exit / missing `-n` exit |
| **5** | `dcode --max-retries` default |
| **9,999** | `recursion_limit` |

### $ / SKUs [inferred]
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$3.75 / $0.30** | 5m cache write / cache read per MTok |
| **$223 / $270 per 1k** | Chat harness cached / uncached |
| **$242 / $302 per 1k** | +MCP prefix / +editor @file |
| **$1.03** | `/cost` docs example (Sonnet 4.5 thread) |
| **$1.34-$1.62** | A2A 3-round cached / uncached per conversation |
| **$1k-$3k / 1k** | ACP hour-equivalent threads (not published) |
| **$50 / $0.50** | Session-cost warn / cold-cache warn delta |

### Latency (numeric ms) / Security
| Number | What |
| --- | --- |
| **640 / 2,560 / 5,120 ms** | [inferred] in-process TTFT p50/p95/p99 |
| **80 / 320 / 1,280 ms** | [inferred] ACP stdio extra hop |
| **720 / 2,880 / 6,400 ms** | [inferred] ACP first model token |
| **800 / 3,200 / 6,400 ms** | [inferred] A2A Dedicated first-event |
| **2,000 / 8,000 / 20,000 ms** | [inferred] one ReAct cycle |
| **20,000 / 80,000 / 200,000 ms** | [inferred] 10-call run |
| **40,000 / 160,000 / 400,000 ms** | [inferred] A2A one round, 2 deploys |
| **120,000 / 480,000 / 1,200,000 ms** | [inferred] A2A 3-round demo |
| **80 / 400 / 2,000 ms** | [inferred] MCP HTTP `tools/call` |
| **30,000 / 180,000 / 600,000 ms** | [inferred] HITL clock; expire-deny |
| **RFC 8707 / 8693** | MCP audience / no passthrough |
| **8.8** | CVE-2025-54136 MCPoison CVSS |
| **-32601 / -32602 / -32001 / -32004** | Method not found / bad params / foreign task / terminal task |

---

*Practice the Q&A out loud; walk the ACP/A2A/`dcode` paths to the same graph; recompute the adapter token tax; draw the fallback chain from memory.*

**Sources**: LangChain Deep Agents docs (tools, MCP, ACP, A2A, Code), `langchain-mcp-adapters` docs, A2A spec (Linux Foundation), MCP spec (Anthropic), CrewAI A2A integration, Google ADK docs.
