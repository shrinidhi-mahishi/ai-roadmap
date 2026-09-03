# Module 09: Deep Agents Execution Environment (data plane)

**Study + interview prep.** Grounded in research dated 2026-09-02 (48 sources). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01). This file is the **data plane**: built-in FS tools, MCP (`langchain-mcp-adapters`, not bundled), pluggable backends, permissions, sandboxes, interpreters, typed streams. `create_deep_agent` is only the assembler that binds `backend=`, `permissions=`, `tools=`, and `FilesystemMiddleware` — middleware graph, `HarnessProfile`, and factory ordering live in [08-deep-agents-harness](08-deep-agents-harness.md) and are not recopied. OAuth 2.1 / RFC 8707 / no-passthrough taxonomy is in [07-guardrails](07-guardrails.md); here they map onto **this** tool/MCP/execute surface. `$ per 1k runs` is **[inferred]** from published unit prices × stated run shapes, not a SKU. Deep Agents / LangSmith publish **no** p50/p95/p99 of `read_file` / `execute` / MCP RTT — missing percentiles are architecture-derived **[inferred] policy targets** (or third-party cold-start snapshots) and are marked.

Execution-relevant gates: `permissions=` `>=0.5.2`; `interrupt` permission mode `>=0.6.8`; `delete` + `FilesystemMiddleware(tools=)` allowlist `>=0.7`; `delete` exact-match first-match-wins `>=0.7.3`; `excluded_tools` also blocks **execution** `>=0.7.9`; typed event streaming `version="v3"` since `deepagents` 0.6 / LangChain 1.3. Interpreters are **beta** (`deepagents[quickjs]`).

---

## What Is This?

The **execution environment is where the agent acts.** LangGraph still runs the ReAct loop; Deep Agents injects a VFS plus an optional shell/`eval` surface through `FilesystemMiddleware` and `CodeInterpreterMiddleware`. Tool results are **untrusted tokens**. Official overview: four layers — **tools**, **virtual filesystem**, **filesystem permissions**, **code execution** — plus typed streams as the observation plane.

Think shop floor, not floor plan. Module 08 is how the factory is wired. This module is the machines: eight `FsToolName`s (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`), MCP `tools/call` riding additive `tools=`, backends that persist bytes, a remote sandbox whose primitive is `execute()`, and `stream.subagents` so a UI can watch a `task` without parsing Pregel namespaces. `task` is `SubAgentMiddleware`, not an FS allowlist name. Opt-in `write_todos` is a tenth tool if `TodoListMiddleware` is restored.

**Permissions are fail-open and FS-tools-only.** First matching glob wins; **no match → allow**. They do **not** cover MCP, custom tools, `execute`, `task`, direct `backend.*`, or a sandbox shell that can `cat` any guest path. **Never `LocalShellBackend` in production** — `subprocess.run(shell=True)`; `virtual_mode` jails FS tools, not the shell.

## Why It Matters

Interviews fork on whether you can name the four layers, refuse host shell, and put a **gateway PEP in front of MCP** because `permissions=` will not. Trap answers: “path globs constrain `execute`,” “Composite unmatched paths 404,” “sandbox is a secret vault,” “streaming saves tokens,” “`excluded_tools` before 0.7.9 is a control.” The cost story on this plane is **grep dumps and idle sandbox TTL**, not the assembler prefix (that was v0.7 in 08). Pin `>=0.7.9` if exclusion is a control; pin `>=0.7.10` if sandbox glob failures must surface.

---

### 1. System Topology & Data Flow

Four stacked concerns, **one** LangGraph loop. Construction + FS PDP + MCP client config are control. Bytes, stdout, MCP results, `eval` output, and stream projections are data. Persistence is checkpointer **and** store **and** whatever backend/sandbox/Hub you bound — they are **not** one transaction. Tool proxies are the eight FS names, `task`, optional `eval`, and additive MCP/custom. Telemetry is LangSmith traces, `ExecuteArtifact`, auth-proxy egress, `stream.subagents` — Deep Agents does not ship WORM or syscall audit; you add that.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  LangSmith traces (FS tools + shell when tracing on)             │
         │  ExecuteArtifact / exit code on ToolMessage.artifact (>=0.7.4)   │
         │  stream.subagents | stream.tool_calls.output_deltas | lifecycle  │
         │  Auth-proxy egress (policy-visible; NOT in the LLM transcript)   │
         │  0.7.9: tracing inputs disabled on middleware                    │
         │  WORM you build: (cid, thread_id, tool, arg_digest, exit, perm)  │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ stream events     │ audit
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (LLM-free assembly; FS PDP before backend; MCP PEP is YOURS)│
│  create_deep_agent binds: backend=  permissions=  tools= (additive)       │
│  FilesystemMiddleware(tools=) allowlist   HarnessProfile.excluded_tools   │
│  MCP MultiServerMCPClient connections + interceptors + gateway (not DA)   │
│  interrupt_on / permission mode=interrupt  (needs checkpointer)           │
│  Graph factory: thread_id from config["configurable"] — not full Runtime  │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ CompiledStateGraph (LangGraph ReAct)
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted token stream — model proposes, PEPs/backends dispose)│
│                                                                           │
│  model → tool_calls → PEP → backend/sandbox/MCP → ToolMessage → stream    │
│                                                                           │
│  ┌────────────── TOOL PROXIES ──────────────────────────────────────────┐ │
│  │ FS (FsToolName): ls read_file write_file edit_file delete glob grep  │ │
│  │                  execute (SandboxBackendProtocol only)               │ │
│  │ task (SubAgentMiddleware)   eval (CodeInterpreterMiddleware, beta)   │ │
│  │ MCP/custom on tools=  — permissions= DOES NOT APPLY                  │ │
│  │ read="ls,read_file,glob,grep"  write="write_file,edit_file,delete"   │ │
│  │ execute + task OUTSIDE _DEFAULT_FS_TOOL_OPS  (#2894 declined)        │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────┬──────────────────┬──────────────────┬─────────────────┬───────────┘
        │                  │                  │                 │
        ▼                  ▼                  ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (independent lifetimes — no XA)                        │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐ ┌───────────────────────┐ │
│  │Checkpointer│ │ BaseStore  │ │ VFS backends │ │ Sandbox guest FS      │ │
│  │thread_id   │ │ StoreBack- │ │ State (dflt) │ │ until stop/TTL/delete │ │
│  │StateBackend│ │ end ns=    │ │ Filesystem*  │ │ Interpreter snapshot  │ │
│  │DeltaChannel│ │ ContextHub │ │ Composite    │ │ mode=thread in state  │ │
│  └────────────┘ └────────────┘ └──────────────┘ └───────────────────────┘ │
│  *FilesystemBackend / LocalShellBackend: local CLI/CI only — not servers. │
│  Internal VFS: /large_tool_results/  /conversation_history/               │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `backend`, `permissions`, FS allowlist, `excluded_tools`, MCP connection map, interceptor PDP, sandbox factory, checkpointer/store handles | Yes for assembly. FS allow/deny runs in middleware **before** a built-in FS tool hits the backend | Putting execute/MCP authz in the prompt; treating `permissions=` as covering MCP |
| **Data** | User messages, FS bytes, `execute` stdout/stderr, MCP `tools/call` results, interpreter `eval` output, stream projections | No — untrusted token stream | Letting the model pick `user_id`, store namespace, or sandbox name |

Control cannot rewrite a tool **after** the model has chosen it except by wrapping (`wrap_tool_call`, MCP interceptors, HITL interrupt). A callable in `create_deep_agent(tools=)` only runs after selection. `tools=` is **additive** and never removes a built-in.

**Request-flow narrative (tool call → permission/MCP PEP → backend/sandbox → stream):**

1. **Model proposes.** Coordinator (or a `task` child) emits `tool_calls`. Observation plane: `stream.tool_calls` (v3) or v2 `updates`/`messages`. Subagent work is `stream.subagents`, not `stream.subgraphs` (Pregel nodes).
2. **Name filter.** `>=0.7.9` `_ToolExclusionMiddleware` drops **and** blocks execution of `excluded_tools`. Capability filter still hides `execute`/`delete` when the backend cannot support them (listing them on a non-capable backend is a **no-op**, not an error). If `execute` is somehow still invoked on a non-executable resolved backend: `ToolMessage` error string, not a raise.
3. **PEP — two doors, not one.**
   - **Built-in FS tools** (`ls`/`read_file`/`write_file`/`edit_file`/`delete`/`glob`/`grep`): `FilesystemPermission` first-match-wins, **no match → allow**. Then the backend. Policy wrappers (`GuardedBackend` / `PolicyWrapper`) see only what permissions already allowed. Direct `backend.write()` from app code **bypasses** `permissions=`.
   - **MCP / custom / `execute` / `task` / PTC:** `permissions=` **does not run**. MCP must hit a **gateway PEP** (allowlist, hash-pin, audience-bound token, interceptor). `execute` hits `SandboxBackendProtocol.execute` (or LocalShell — prod forbid). `interrupt_on={"execute": True}` is a review queue on the **tool path**; PTC-invoked tools **skip** it.
4. **Backend / sandbox / MCP dispose.**
   - Composite: **longest prefix wins**; unmatched path (including a typo `/memory/` vs `/memories/`) hits **default** — silent, not a 404. `execute` runs on **default only**.
   - Sandbox-as-tool (documented default): each FS/`execute` call is a remote API; POSIX scripts (`awk`/`grep`/`find`/`stat`) implement FS tools via `execute()`. App `upload_files` / `download_files` are a **second** plane (seed/harvest), not the agent tools.
   - MCP default: **new `ClientSession` per `tools/call`** unless `client.session()`. Transport/session failures **raise**; `handle_tool_errors=True` only maps `isError=True` → `ToolMessage(status="error")`.
5. **Observe / persist.** Combined stdout/stderr (truncated; large execute may land in a sandbox artifact path — `LangSmithSandbox` opts in by default as of 0.7.0). Exit code on `ToolMessage.artifact` `>=0.7.4`. Results over **20,000** tokens offload to VFS (path + first **10** lines). Stream: `output_deltas` for incremental tool stdout. Agent Server: worker continues after SSE disconnect unless the client **cancels**; rejoin needs persisted `thread_id`. Redis pub/sub is ephemeral; run data is Postgres.
6. **Stop.** Model stops calling tools, HITL interrupt (checkpointer required), sandbox `timeout` / `max_execute_timeout` (default **3600 s**), interpreter **5.0 s** `eval` cap, or provider teardown (`idle_ttl`).

**Four execution-environment layers (official):**

| Layer | What | Default / catch |
| --- | --- | --- |
| **Tools** | Eight `FsToolName`s + `task` + additive MCP/custom + optional `eval` / `write_todos` | Unused schemas still billed every turn |
| **VFS** | `BackendProtocol` instance (`StateBackend()` default). Factories **removed** in 0.7 | Skills/memory are **files** on this backend |
| **Filesystem permissions** | Path glob PDP, fail-open, FS-tools-only | Composite+sandbox: paths must sit on a **route** or construction `NotImplementedError` (including `/**`) |
| **Code execution** | Remote `BaseSandbox` **or** `LocalShellBackend` **or** QuickJS `eval` | Production = remote sandbox. Never LocalShell. Interpreter is not pytest/git |

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (data plane)

**I1.** Execution is **not** a new scheduler. LangGraph runs the loop; this plane is tools + VFS + optional shell/eval + streams.

**I2.** `permissions=` is first-match-wins, **fail-open**, **FS-tools-only**. `execute` / `task` / MCP / `backend.*` / PTC are outside. Issue #2894 (`ExecutePermission` / `TaskPermission`) was **declined** — custom middleware.

**I3.** `read_file` is **mandatory** on any `FilesystemMiddleware(tools=)` list (`ValueError` if omitted) because offload is useless if the model cannot page the blob back. `excluded_tools` can still drop `read_file` (visibility+execution `>=0.7.9`) — a silent capability hole versus the allowlist hard error **[inferred from the two knobs]**.

**I4.** Composite unmatched paths **fall through to default**. That is a silent persistence bug, not HTTP 404.

**I5.** `LocalShellBackend.virtual_mode` is an FS-tool jail, **not** a sandbox. `subprocess.run(shell=True)`. Production: `StateBackend` / `StoreBackend` / `BaseSandbox`.

**I6.** Sandbox stops **host** FS/env/process access. It does **not** stop context injection **inside** the guest or network exfil unless the provider blocks egress. **Never put secrets in the sandbox.** Auth proxy **fails closed**.

**I7.** MCP has **no native runtime** in Deep Agents. `permissions=` will not save you. Zero-Trust is a **gateway PEP**.

#### 2.2 FS tools — schemas, pagination, search

| # | Tool | Role | Availability |
| --- | --- | --- | --- |
| 1 | `ls` | Directory + metadata (size, mtime) | Always unless hidden |
| 2 | `read_file` | Paginated read; multimodal blocks | **Required** on `FilesystemMiddleware(tools=)` |
| 3 | `write_file` | Create **or overwrite** (`>=0.7`; previously create-only) | Always unless hidden |
| 4 | `edit_file` | Exact string replace; optional global | Always unless hidden |
| 5 | `delete` | File or recursive directory | **`>=0.7`**. Auto-hidden if backend lacks `delete` |
| 6 | `glob` | Path glob; brace expansion on disk backends | Always unless hidden |
| 7 | `grep` | Content search | Always unless hidden |
| 8 | `execute` | Shell in sandbox / LocalShell | Only if `SandboxBackendProtocol` |
| — | `task` | Spawn subagent | Harness GP; **not** `FsToolName` |

Agent-facing caps:

| Tool | Notable args | Defaults / caps |
| --- | --- | --- |
| `read_file` | `file_path`, `offset`, `limit` | Tool: `offset=0`, **`limit=100` lines** (`DEFAULT_READ_LIMIT`). Protocol `read(..., limit=2000)` is **direct** backend, not the model tool. Video extra: `offset`/`limit` in **seconds** |
| `grep` | `pattern`, `path`, `glob`, `output_mode`, `max_count` | `output_mode` ∈ `{files_with_matches, content, count}`; middleware `grep_max_count=1000` |
| `glob` | `pattern`, `path` | `truncated` when cap/deadline hit |
| `execute` | `command`, `timeout` | Rejected if `> max_execute_timeout` (**3600 s**) or negative; `0` may mean no timeout on supporting backends (`0.7.9` clarified) |
| `delete` | `file_path` | Recursive; all-or-nothing vs descendants |

Empty `ls`/`glob` tool strings are **`No files found`** (not `[]`) since 0.7 — `json.loads` parsers break. `read_file` no longer uses a fixed-width `cat -n` gutter. Negative offsets clamp to 0 with a notice.

Pagination (`>=0.7`): source-line range, `next_offset`, remaining lines when length is known. Middleware truncation **adjusts** `next_offset` so resume does not skip unseen lines. Character budget: `NUM_CHARS_PER_TOKEN = 4`; truncation threshold `4 × token_limit`.

`FilesystemBackend` may use **ripgrep**. Sandbox `grep`/`glob` are POSIX scripts via `execute()`, constrained to the search root (`/` is search-root-relative; `..` rejected; symlink matches outside root filtered) (`>=0.7`).

**Complexity [architecture, not a paper]:** `ls`/`read_file` page = \(O(\text{page})\) I/O. `grep content` with `max_count=1000` still serializes match **text** into the tool message until the 20k evictor fires — \(O(\text{matches} \times \text{line})\) tokens. Composite `ls`/`glob`/`grep` **aggregate** children and preserve prefixes. Offload is \(O(1)\) path swap above 20k tokens.

Multimodal `read_file` (v0.5+ / v0.7 video extra): image (png/jpeg/gif/webp/heic/heif — png/jpeg/gif/webp native on **all** backends), video, audio, pdf/ppt. Video extra `deepagents[video]`: `offset`/`limit` in **seconds** → JPEG frames. `0.7.2` scrubs blocks the model profile does not support. Vision tokens, not 4-chars-per-token — **no** published DA conversion table.

#### 2.3 Two hide mechanisms (allowlists)

| Mechanism | Layer | What it does | `read_file` required? | Affects user `tools=`? |
| --- | --- | --- | --- | --- |
| `HarnessProfile.excluded_tools` | Post-injection name filter | Drops from **model-visible** list. **0.7.9+** also **blocks execution** | No | **Yes** — caller tools **and** harness tools |
| `FilesystemMiddleware(tools=[...])` | Construction allowlist `>=0.7` | Only listed `FsToolName`s registered | **Yes** — else `ValueError` | **No** |
| `excluded_middleware={"FilesystemMiddleware"}` | Rejected | Offload/permissions/skills/memory need the VFS | n/a | n/a |

Passing your own `FilesystemMiddleware` **replaces** the default for the **main** agent; the general-purpose subagent inherits it. Declarative `subagents=` do **not** inherit — put a `FilesystemMiddleware(tools=...)` on that spec.

#### 2.4 Pluggable backends

Pass a `BackendProtocol` **instance** (factories removed in 0.7). Default: `StateBackend()`.

| Backend | Persistence | Isolation | `execute`? | Typical use |
| --- | --- | --- | --- | --- |
| **StateBackend** | Thread state + checkpointer. Shared with subagents | Per-thread | No | Scratch, offload |
| **FilesystemBackend** | Host disk under **absolute** `root_dir`. `virtual_mode=True` **default since 0.7** | Path jail for **FS tools only** when `virtual_mode=True`. `False` “provides no security even with `root_dir` set” | No | Local CLI, CI. Docs: **not** for web servers |
| **LocalShellBackend** | Extends FilesystemBackend | **None for shell** | Yes — `subprocess.run(shell=True)` | Trusted local/CI **only** |
| **StoreBackend** | LangGraph `BaseStore`; `namespace` factory required going forward | Per namespace tuple. Legacy no-factory = `assistant_id` → **all users of one assistant share storage** | No | Memories, org policies |
| **ContextHubBackend** | Hub repo; lazy pull + cache; writes = Hub commits, optimistic `parent_commit` | Repo ACL + `LANGSMITH_API_KEY`. UTF-8 only on `upload_files()` | No | Durable skills/AGENTS.md |
| **Sandbox (`BaseSandbox`)** | Isolated container/VM FS | Provider isolation | Yes — `execute()` is the primitive | Production code exec |
| **CompositeBackend** | `default` + `routes={prefix: backend}` | Per child. `supports_execution` = default is `SandboxBackendProtocol` | Default only | `/memories/` durable + rest ephemeral |

`StoreBackend` namespace factory receives LangGraph `Runtime` (`>=0.5.2`): `rt.context`, `rt.server_info`, `rt.execution_info`. Components: alphanumeric, hyphen, underscore, dot, `@`, `+`, colon, tilde. Wildcards `*` `?` **rejected** (glob injection).

**Python `BackendProtocol`:** `ls`, `read`, `grep`, `glob`, `write`, `edit`; optional `delete`. Always return structured `*Result` with `error` field; **do not raise**. **`SandboxBackendProtocol`:** `execute(command, *, timeout=None) -> ExecuteResponse`, `aexecute()`, `id`. JS `SandboxBackendProtocolV2` is the result-typed evolution (`uploadFiles`/`downloadFiles`); Python detection is `isinstance(..., SandboxBackendProtocol)` / `supports_execution()`.

#### 2.5 Composite routing (longest prefix)

```
  path ──► sort routes by prefix length DESC ──► first startswith(prefix) wins
                │
                └─ none match ──► default   (NO route-miss error)
```

- Longest prefix wins (`"/memories/projects/"` overrides `"/memories/"`).
- Unmatched paths (docs example: `/workspace/plan.md` if that prefix is not a route) hit **default**.
- `ls` / `glob` / `grep` aggregate and preserve original prefixes.
- `0.7`: `ls("/")` / root `glob` propagate **default-backend failures** instead of returning route-only successes; `glob` on an explicit default path (e.g. `/tools`) no longer also returns `/memories`.
- `delete` on a routed sub-backend that cannot delete → **unsupported-operation error** (not silent hide).
- `execute` on Composite: shell on **default only**. Virtual prefixes (`/memories/`) may not exist in the guest. Host-path substitution hints inject **only** when default is `LocalShellBackend` and the route is a local `FilesystemBackend`. Remote sandbox default → agent must use **file tools**, not `cat /memories/...` in `execute`.

Internal artifacts under `/large_tool_results/` and `/conversation_history/` follow the **default** backend. Bare `FilesystemBackend` writes those onto real disk under `root_dir`. Pattern: `CompositeBackend(default=StateBackend(), routes={"/workspace/": FilesystemBackend(..., virtual_mode=True)})`.

**Consistency:** a Store write under `/memories/` and a checkpointed StateBackend write are **not** one transaction. Crash between them → split brain **[inferred from independent stores; no XA in docs]**.

#### 2.6 Permissions — first-match, fail-open, FS-only

`FilesystemPermission(operations, paths, mode)`:

- Declaration order; **first matching rule wins**.
- **No match → allow.**
- `mode`: `"allow"` (default) | `"deny"` | `"interrupt"` (`>=0.6.8`; requires checkpointer; auto-wires HITL; merges with `interrupt_on`).
- Globs: `**`, `{a,b}` alternation.
- Subagent `permissions` **replace** the parent (do not merge).

`"read"` → `ls`, `read_file`, `glob`, `grep`. `"write"` → `write_file`, `edit_file`, `delete`.

**`delete` semantics:** directory delete is **all-or-nothing** (`write` on target **and every descendant**). Plain-file delete is exact-match first-match-wins (`>=0.7.3`). Because `delete` is **write**, an existing “allow write on `/workspace/**`” also authorizes recursive delete unless a narrower deny/interrupt exists — **0.7 breaking change**.

Interrupt glob caveat: bulk tools (`ls`, `glob`, `grep`, directory `delete`) fire when the search subtree **could overlap** an anchored prefix. Fully unanchored `/**/secrets` **over-fires**. Anchor: `/secrets/**`.

**Composite + sandbox default:** every permission path **must** sit under a known **route** prefix. Paths that hit the sandbox default (including `/**`) raise `NotImplementedError` at construction — path rules cannot constrain `execute`. Exception: allowed if *every* permission path is scoped to a route.

#### 2.7 Sandbox vs `LocalShellBackend` vs QuickJS interpreter

| | Remote sandbox (`BaseSandbox`) | `LocalShellBackend` | Interpreter (`CodeInterpreterMiddleware`) |
| --- | --- | --- | --- |
| Primitive | `execute` (POSIX shell) | `execute` via `subprocess.run(shell=True)` | `eval` (JS in QuickJS) |
| FS tools | Implemented **on top of** `execute()` | Real host FS | **No** FS unless PTC-allowlisted |
| Network / packages / git | Provider policy | Host network, unlimited CPU/mem/disk | **No** by default |
| Isolation | Container/VM vs host. Not vs injection **inside** | **None** | Same-process `quickjs-rs` heap — not a VM |
| HITL | `interrupt_on={"execute": True}` on the **tool path**. PTC `tools.execute` would **not** | Same; docs **strongly recommend** HITL | PTC calls **bypass** `interrupt_on` |
| When | Production coding / data analysis | Local CLI / trusted CI only | Loops, batching, deterministic transforms, fan-out `task()` from code |

**Sandbox-as-tool** (documented default): agent process stays on your server; API keys stay outside; trade-off = RTT per call. **Agent-in-sandbox**: `deepagents` in the guest; **API keys must live in the guest** — docs flag as a security risk.

Providers (swap is a backend instance, not a loop change): LangSmith (`LangSmithSandbox`, `client.delete_sandbox`), Daytona (`sandbox.stop()`), E2B (`kill()`), Modal (`terminate()`), Runloop (`devbox.shutdown()`), Vercel (`sandbox.stop()`; `Sandbox.create(runtime="python3.13")`), AgentCore (`interpreter.stop()`), NVIDIA OpenShell (context manager `delete_on_exit=True`).

LangSmith `create_sandbox` wait-for-ready `timeout` default **30 s**. Size defaults: **0.5 vCPU**, memory **4 GiB per vCPU** (so **~2 GiB** at default CPU **[inferred from ratio]**; range 0.05–16 vCPU, mem up to 64 GiB). Burst to **2×** requested CPU if host has spare. Resize applies at **next start**. `[sandbox]` extra installs `websockets` for streaming `run()`; without it, `run()` falls back to HTTP. Lifecycle: `running --(idle_ttl)--> stopped --(delete_after_stop)--> deleted`. Stopped box **wakes** on next command. `idle_ttl_seconds` default **600** (multiple of 60); Deep Agents prod snippet example **3600**. `delete_after_stop_seconds` typically **14 days** if omitted. `kill_on_disconnect=True` kills a streamed **command**, not the LangGraph worker.

Interpreter defaults: `memory_limit` **64 MiB**; `timeout` **5.0 s**; `tool_name` `"eval"`; `capture_console=True`; `max_result_chars` **4000**; `ptc=None`; `max_ptc_calls` **256** per `eval`; `subagents=True`; `mode="thread"` (also `"turn"`, `"call"`); `max_snapshot_bytes` defaults to `memory_limit`. Requires `langchain-quickjs>=0.2.0` (overview extra cites `>=0.3.5` in 08) and Python `>=3.11`. PTC names camelCase (`web_search` → `tools.webSearch`). `mode="thread"`: snapshot after each **agent turn**, not between `eval`s in the same turn. Snapshot restore **does not undo PTC side effects**. Unserializable JS becomes accessors that throw.

`LocalShellBackend`: `timeout` default **120 s**; `max_output_bytes` **100,000**; `inherit_env=False` (default) still leaves `env=` and does not block `/proc` or absolute paths in the command string.

#### 2.8 MCP session model

Deep Agents has **no native MCP runtime**. Pattern: `MultiServerMCPClient` → `await client.get_tools()` → `create_deep_agent(tools=tools)`.

| Constructor arg | Default | Role |
| --- | --- | --- |
| `connections` | — | `dict[name, Connection]` |
| `callbacks` | `None` | Progress, logging, elicitation |
| `tool_interceptors` | `None` | Onion around `tools/call`; **first interceptor is outermost** |
| `tool_name_prefix` | `False` | `"server_tool"` against collisions (`"math_add"`) |
| `handle_tool_errors` | `True` | `isError=True` → `ToolMessage(status="error")` instead of raise (`langchain-mcp-adapters>=0.3.0`; earlier: `ToolException`) |

**Transports:** `"stdio"` (client spawns subprocess; **stateless client still opens a new session per tool call** unless `client.session()`); `"http"` / `"streamable_http"` (spec 2025-03-26, retained 2025-11-25; replaces 2024-11-05 HTTP+SSE); `"sse"` deprecated by spec, still accepted. Streamable HTTP: single endpoint POST+GET; `Accept: application/json, text/event-stream`; optional SSE; `MCP-Protocol-Version`; `Mcp-Session-Id` for stateful sessions. Spec 2026-07-28 moves toward **stateless core** (initialize / `Mcp-Session-Id` removed at protocol layer — pin version at the gateway; see 07).

**Stateful vs stateless:** default **stateless** = fresh `ClientSession` per invocation. `async with client.session("server_name")` + `load_mcp_tools(session)` when the server keeps context. stdio **process** may live with the client object even when sessions are per-call. Transport/session/content-conversion failures **always raise**. Retry interceptor with exponential backoff is an **example, not a default**. `handle_tool_errors=True` lets the **model** retry semantic failures; it will not retry TCP errors unless an interceptor catches them. Exception-catching interceptors **do not** fire on `isError=True` unless `handle_tool_errors=False`.

Interceptor bridges (MCP servers cannot see LangGraph store/state): `request.runtime.context` / `store` / `state`; `request.override(args=..., headers=...)`; return `Command(update=..., goto=...)`. Do **not** pass the user OAuth token through. Official HTTP example `headers={"Authorization": "Bearer ..."}` is **static bearer** — enterprise anti-pattern vs OAuth 2.1 + RFC 8707 (07 §4.2). Built-in `auth=` path: MCP SDK `OAuthClientProvider` (PRM, DCR, PKCE, 401 replay).

Filtering: `get_tools(server_name=...)`; application allowlist after `get_tools()` (not a first-class Deep Agents PDP); interceptor short-circuit. `structuredContent` is wrapped as `MCPToolArtifact` on `ToolMessage.artifact` and is **invisible to the model** unless an interceptor appends it — default keeps context smaller. Resources/prompts/elicitation are usually **not** auto-bound as agent tools. Elicitation: bind to a **human UI**; auto-`accept` can forge content. Resources/`resources/read` are untrusted (07).

#### 2.9 Streaming: v2 vs v3 and `stream.subagents`

Two APIs: legacy `agent.stream(..., stream_mode="updates"|"messages"|"custom", subgraphs=True, version="v2")` with namespace tuples `()` = main, `("tools:<tool_call_id>",)` = subagent; **recommended** `agent.stream_events(..., version="v3")` (Deep Agents ≥0.6 / LangChain ≥1.3 / LangGraph 1.2).

v3 projections: `messages`, `values`, `tool_calls`, `subgraphs`, `output`, plus transformers (updates/custom/checkpoints/tasks/debug). Deep Agents adds **`stream.subagents`**: one handle per delegated `task`. Lightweight — discovers tasks first; message/tool/value streams open only when accessed. Docs: use **subagents** for user-facing UI; `subgraphs` is graph-node structure.

Handle fields: `name` (`subagent_type`), `path`, `status` (`started`/`completed`/`failed`/`interrupted`), `messages`, `tool_calls` (`tool_name`, `input`, `output_deltas`, `completed`, `error`, `output`), nested `subagents`, `output`. Concurrency: `asyncio.gather` on projections, or `stream.interleave("messages", "subagents")`. Total order: raw protocol events; empty `namespace` = coordinator. Lifecycle-only UIs iterate `stream.subagents` and await `subagent.output` without token subscribe. Frontend: `useStream` local `http://localhost:2024`; production = LangSmith Deployment; rejoin requires persisted `threadId`. Streaming does **not** reduce billed tokens — it changes TTFT / time-to-first-tool.

#### 2.10 Version gates that change this plane

| Version | Behavioral gate |
| --- | --- |
| `>=0.5.0` / `>=0.5.2` | Multimodal; `permissions=`; Store `Runtime` namespace factory |
| `>=0.6` / LangChain 1.3 | `stream_events` v3; `DeltaChannel`; interpreters experimental |
| `>=0.6.8` | Permission `mode="interrupt"` |
| **`>=0.7.0`** | `delete`; FS `tools=` allowlist; `write_file` overwrites; `virtual_mode` default True; backend factories removed; execute offload into sandbox artifact (`LangSmithSandbox` opts in); empty ls/glob string change |
| `>=0.7.2` | Multimodal scrub vs model profile |
| `>=0.7.3` | Exact-match `delete` first-match-wins |
| `>=0.7.4` | Execute exit code on `ToolMessage.artifact` |
| `>=0.7.7` | ContextHub concurrent mutations batched |
| `>=0.7.9` | `excluded_tools` blocks **execution**; tracing inputs off on middleware; zero-timeout semantics |
| `>=0.7.10` | Sandbox glob failures no longer swallowed |

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: **Neither Deep Agents nor LangSmith publishes p50/p95/p99 of `read_file` / `execute` / MCP RTT as a harness SLO**, nor sandbox-pool replenish interval, nor warm-pool size (product blog mentions warm pools — **no numbers**). Unit prices, middleware thresholds, vendor sandbox rates, and one third-party cold-start table are published. `$ per 1k runs` is **[inferred]**. Latency percentiles below are that table (converted to **ms**) plus architecture-derived **[inferred] policy targets**. Do not cite inferred rows as a vendor SLO.

#### 3.1 Context tax of the data plane

| Knob | Default | Effect |
| --- | --- | --- |
| `tool_token_limit_before_evict` | **20,000** tokens | Tool **results** over threshold → backend, replace with path + **first 10 lines** |
| `human_message_token_limit_before_evict` | **50,000** tokens | Human-message eviction |
| Write/edit **inputs** | same 20k | Offload delayed until session crosses **85%** of window; results over 20k offload **immediately** |
| `grep_max_count` | **1,000** (`None` disables) | Model can override per call via `max_count` |
| `max_execute_timeout` | **3600 s** | Cap on `execute` `timeout` arg; `<=0` → `ValueError` at construction |
| `read_file` `limit` | **100 lines** | Pagination; not a token cap by itself |
| Interpreter `max_result_chars` | **4000** | Truncates `eval` text returned to the model |
| `LocalShellBackend.max_output_bytes` | **100,000** | Truncate host-shell capture |
| `LocalShellBackend.timeout` | **120 s** | Default command wall clock |

Unused built-in tools still send **full JSON schemas every turn**. v0.7 isolated tool-description tokens **4,005 → 2,302 (−43%)**; default-agent turn **5,395 → 1,895 (−65%)**. `grep` `output_mode="content"` is the classic blow-up; `files_with_matches` / `count` are cheaper. Partial results set `truncated=True`.

MCP: `structuredContent` in the artifact is **invisible** unless an interceptor appends it. PTC/interpreter: intermediates stay in QuickJS; the model sees one `eval` return ≤4000 chars — the intended token win versus N ReAct hops.

Streaming vs `invoke`: **identical billed tokens**. `invoke` waits for the full graph; `stream_events(version="v3")` yields projections concurrently. Production Agent Server: API enqueues; **queue worker** executes; Redis pub/sub → `/stream` SSE; checkpoints → Postgres. Disconnect does **not** cancel the worker unless the client calls cancel.

Multimodal **[inferred]**: a 10-frame video window at typical vision rates can exceed a 100-line text page; prefer short `limit` (seconds). Scrubbed blocks still cost a wasted tool round-trip.

#### 3.2 Model unit prices (worked examples)

Claude Sonnet 4.6 (Deep Agents docs’ default Anthropic string), USD / million tokens:

| | Input | 5m cache write | Cache read | Output |
| --- | --- | --- | --- | --- |
| Sonnet 4.6 | $3 | $3.75 | $0.30 | $15 |

#### 3.3 Worked example A — grep dump vs paginated VFS **[inferred]**

Assumptions: Sonnet 4.6; **1,000 identical runs**; each run does **one** large search then **7** follow-up model calls that re-send history (no prompt cache on the tool blob; 8 total model calls). Output tokens ignored (same either path).

| Path | Tokens into context | Input $ / run | / 1k runs |
| --- | --- | --- | --- |
| `grep` `content` dumps **50,000** tokens, retained all 8 calls | 50,000 × 8 × $3 / 1e6 | **$1.200** | **$1,200** |
| Offload at 20k: preview **~200** tokens (10 lines) × 8; one `read_file` page **~400** tokens once | (200×8 + 400) × $3 / 1e6 | **$0.006** | **$6** |

Delta ≈ **$1,194 / 1k runs** for a single undisciplined dump. If prompt caching held the dump as a prefix, cache reads are $0.30/MTok still **$0.120 / run** for 50k × 8 — **$120 / 1k**, 20× the offload path.

#### 3.4 Worked example B — `$ per 1k runs` coding agent (model + sandbox) **[inferred]**

Assumptions: Sonnet 4.6; v0.7 prefix **2,000** tokens cached (5m TTL, 1 write + 7 reads across **8** calls); uncached **4,000** / call; output **600** / call. LangSmith sandbox **0.5 vCPU, 2 GiB**; useful work **90 s**; then either destroy immediately or sit until **600 s** idle TTL.

Published LangSmith rates: compute **0.0384 LCU / vCPU-hr**, memory **0.0123 LCU / GiB-hr**, storage **0.000123 LSU / GiB-hr**; **1 LCU = $1.50**, **1 LSU = $1.00**. Implied **[inferred]**: **$0.0576 / vCPU-hr**, **$0.01845 / GiB-hr**. Developer/Plus: **5 LCU + 1 LSU / mo included**; Developer **capped at 10 sandboxes**.

**Model / run:** cache write 2,000 × $3.75/1e6 = $0.00750; cache reads 7 × 2,000 × $0.30/1e6 = $0.00420; uncached in 8 × 4,000 × $3/1e6 = $0.09600; output 8 × 600 × $15/1e6 = $0.07200 → **$0.1797 / run → $180 / 1k**.

| Billing window | Hours | CPU $ | Mem $ | / run | / 1k |
| --- | --- | --- | --- | --- | --- |
| 90 s work only | 0.025 | 0.5×0.025×0.0576 = 0.00072 | 2×0.025×0.01845 = 0.00092 | **$0.00164** | **$1.64** |
| 90 s + 600 s idle TTL | 0.1917 | 0.00552 | 0.00707 | **$0.0126** | **$13** |

Idle TTL **~8×** the execute-time bill if you keep thread-scoped boxes warm for 10 minutes. Assistant-scoped shared boxes amortize idle across conversations but leak tenant state.

E2B published: `cost = (vCPU × $0.000014 + RAM_GiB × $0.0000045) × seconds` = **$0.0504 / vCPU-hr** + **$0.0162 / GiB-hr**. Default **2 vCPU, 512 MiB**. 90 s: `(2×0.000014 + 0.5×0.0000045)×90 ≈ $0.00272` → **$2.72 / 1k** (Pro floor **$150/mo** not included). Daytona compute rates reported identical to E2B in 2026 roundups. Interpreter-only: sandbox line **$0**; you still pay model tokens; `eval` 5 s / 64 MiB is process-local.

#### 3.5 Latency SLA — p50 / p95 / p99 numeric ms

Clock-split: (a) parent streaming TTFT — **VFS is not on this path**; (b) local FS tool extra; (c) **cold** sandbox start **in series** with the model call that produced `execute`; (d) **warm** execute = provider exec API + command time, capped by `timeout`; (e) MCP `tools/call` RTT; (f) HITL — a **different clock**; (g) interpreter `eval` cap.

**Published third-party cold-start** (MarkTechPost, Aug 2026; **not** LangChain; concurrency/region/image unspecified). Vendor marketing (Daytona <90 ms, E2B ~80–200 ms) is **not** this table. Daytona row: **37% success** in that run.

| Path | **p50** | **p95** | **p99** | Grounding |
| --- | --- | --- | --- | --- |
| **Vercel Sandbox cold start** (third-party) | **670 ms** | **1,040 ms** | **1,120 ms** | Table; 100% success that run |
| **Modal cold start** (third-party) | **880 ms** | **1,000 ms** | **1,080 ms** | Table |
| **Runloop cold start** (third-party) | **890 ms** | **3,270 ms** | **3,500 ms** | Table |
| **E2B cold start** (third-party) | **1,610 ms** | **1,770 ms** | **1,810 ms** | Table |
| **Cloudflare cold start** (third-party) | **5,060 ms** | **6,040 ms** | **6,480 ms** | Table |
| **Daytona cold start** (third-party; 37% success) | **270 ms** | **430 ms** | **440 ms** | Table — **not** an SLO; vendor <90 ms claim ≠ this snapshot |
| **LangSmith sandbox start** **[inferred policy]** | **5,000 ms** | **15,000 ms** | **30,000 ms** | **Unpublished** actuals. p99 = ready-wait default **30 s** (still-creating fuse, not a measured SLO) |
| **Streaming TTFT, parent** **[inferred policy]** | **640 ms** | **2,560 ms** | **5,120 ms** | Same inner-chat class as 08; research: TTFT dominated by first model token, **not** VFS |
| **One ReAct cycle (model + StateBackend FS tool)** **[inferred]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Local VFS extra is not the tail; model + provider queue is |
| **StateBackend / permission check extra** **[inferred policy]** | **5 ms** | **20 ms** | **80 ms** | Local CPU; unpublished — not the tail |
| **Warm sandbox `execute` API extra, excluding command** **[inferred policy]** | **100 ms** | **400 ms** | **1,500 ms** | Provider exec HTTP after the box is running; command time **adds**, capped by `timeout` |
| **First `execute` on cold E2B (cycle + table)** **[inferred]** | **3,610 ms** | **9,770 ms** | **21,810 ms** | **In series**: ReAct cycle **2,000/8,000/20,000** + E2B cold **1,610/1,770/1,810**. Swap the addend for another provider. Not a product SLO |
| **MCP `tools/call` Streamable HTTP** **[inferred policy]** | **80 ms** | **400 ms** | **2,000 ms** | Unpublished in DA; HTTP tool class. stdio spawn is a different unpublished clock — policy **200 / 1,000 / 5,000 ms** if you must bound it |
| **Interpreter `eval`** **[inferred policy]** | **20 ms** | **200 ms** | **5,000 ms** | Same-process QuickJS; p99 = library default timeout **5.0 s** fuse |
| **HITL `interrupt_on` execute** **[inferred policy]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds–minutes; expire → **deny**, not auto-approve |
| **`LocalShellBackend` spawn extra (forbid in prod)** **[inferred]** | **1 ms** | **5 ms** | **20 ms** | ~0 vs remote cold start — that speed **is** the incident. Default command fuse **120,000 ms** (`timeout` 120 s) |

Hard fuses (not SLOs): `max_execute_timeout` **3,600,000 ms** (3600 s); interpreter **5,000 ms**; LocalShell **120,000 ms**.

**Mitigations mapped to percentiles:**

- **p50:** stream v3; keep thread-scoped sandbox **warm** for the session (pay idle TTL — §3.4); `read_file` pages of 100 lines; `grep` `files_with_matches`; cache-warm model prefix.
- **p95:** named sandbox lookup (documented “pool”) vs create; `kill_on_disconnect` only for streamed **commands**; do not interceptor-append fat `structuredContent`; timeout `execute` independently of the parent graph.
- **p99:** HITL off the HTTP thread; cold-start **is** the tail for first `execute` — do not put it on a 5 s API gateway timeout; LangSmith 30 s ready-wait is a **fuse**; sandbox 503 → **queue**, never LocalShell; MCP p99 → disable those tools (degrade), do not unsandbox.

#### 3.6 Throughput / back-pressure

> ⚠️ Gap: Deep Agents publishes **no RPM/TPM**. Provider account limits apply.

| Ceiling | Number | Effect |
| --- | --- | --- |
| Agent Server runs per `thread_id` | **at most one** | Second invoke waits / undefined overlap — serialize on thread |
| LangSmith Developer sandboxes | **10** | 100 concurrent thread-scoped tenants **does not fit** |
| E2B concurrent sandboxes | Hobby **20** / Pro **100** / purchasable **1,100** | Plan limit, not a Deep Agents limit. Session caps 1 h / 24 h |
| `grep_max_count` | **1,000** | Local safety valve, not cluster QoS |
| `max_ptc_calls` | **256** / `eval` | Interpreter fan-out cap |
| `max_execute_timeout` | **3600 s** | Runaway shell fuse |
| Interpreter heap / eval | **64 MiB** / **5.0 s** | Process-local; not a VM quota |
| LangSmith included | **5 LCU + 1 LSU / mo** | Then on-demand |
| Warm pools | **unpublished size / replenish** | Product mention only |

**Back-pressure design:** (1) admit with a sandbox **lease** + queue when allocate returns 503 — **never** fail-open to host shell; (2) bulkhead **model** vs **sandbox pool** vs **MCP egress** vs **checkpointer writes**; (3) `grep`/`glob` `truncated` is VFS back-pressure — do not loop until “complete”; (4) SSE consumer is not the worker — cancel explicitly; (5) `idle_ttl` is both a $ control and a recycle valve; (6) one-run-per-thread is admission control — do not build a second scheduler on the same `thread_id`; (7) MCP interceptor rate-limit **fail closed** on the tool, not on the whole agent.

#### 3.7 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of execute/MCP** | Product SLO is the parent loop + FS tools on State/Store. Sandbox allocate and MCP servers are **bulkheaded**: 503/queue or disable those tools. Circuit-open sandbox → **queue/refuse**, not LocalShell | Coding-agent completeness vs host RCE |
| **RPO of checkpointer / StateBackend** | Files **are** checkpoint payload (`DeltaChannel` incremental since 0.6). `InMemorySaver` RPO = **empty on restart**. Rollback across 0.6 boundary **unsupported** without dump script | Crash-consistency vs checkpoint size (do not put large blobs in StateBackend) |
| **RTO of checkpointer** | Resume `thread_id`. Restoring a checkpoint does **not** restore guest packages | Time-to-resume vs sandbox reality |
| **RPO of Store / Hub / disk** | Last Store put / Hub commit / host file. ContextHub stale `parent_commit` → fail; re-pull and retry. Concurrent Hub mutations batched in **0.7.7** | Lifelong memory vs split-brain vs Composite (no XA) |
| **RPO of sandbox guest** | Until stop / `idle_ttl` / `delete_after_stop` (typically 14 days) / provider snapshot. **Orthogonal** to LangGraph checkpoint. Killing the box does not roll back checkpoints | Idle $ vs cold start |
| **RTO of sandbox** | Wake-from-stopped (LangSmith: next command; you still pay) or create (cold-start table). Agent server must survive guest OOM | p50 UX vs isolation |
| **RPO of interpreter snapshot** | `mode="thread"` in graph state; with checkpointer if present. Restore does **not** undo PTC | Cross-turn JS vs world |
| **RPO of traces** | Sampled LangSmith is lossy. 0.7.9 disabled tracing **inputs** on middleware | Debug vs PII |
| **Compliance** | **Not provided by `deepagents`.** Traces, checkpoints, VFS bytes, sandbox disks, MCP args are subprocessors if they hold prompts. GDPR erasure = checkpointer + store + sandbox + trace + MCP-side purge, not `thread_id` TTL | Time-to-debug vs residency |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_VFS_state = last durable super-step. RPO_store = last put (survives process death). RPO_sandbox = last snapshot or **empty after TTL**. RTO_sandbox = create/wake (hundreds of ms to tens of s) vs LocalShell **0 ms and a CVE**. RPO_MCP_stateless = **none** (no sticky server memory) — retries safe **if** the server is idempotent. RPO_MCP_stateful_session = lost on connection death.

---

### 4. Distributed Resilience & Security

> ⚠️ Gap: **`deepagents` does not ship circuit breakers, leader election, or sandbox-pool replenishment SLOs.** Resilience here is **lifetime + routing + reconnect** from docs. Breakers are yours.

#### 4.1 Durable execution: backend lifetimes vs checkpointer vs disk vs sandbox recycle

| Store | Lifetime | Survives process restart? | Shared across threads? | Relation to checkpointer |
| --- | --- | --- | --- | --- |
| `StateBackend` files | Thread | Only if a **checkpointer** persists `DeepAgentState` (incl. `DeltaChannel` since 0.6) | No (subagents **share** the parent thread VFS; scratch remains after `task` returns) | Files **are** checkpoint payload |
| `FilesystemBackend` | Disk | Yes | Anyone with host path | Independent. Checkpoint may point at paths that still exist |
| `StoreBackend` | Store TTL / DB | Yes | Per `namespace` | `store=` required; LangSmith provisions one |
| `ContextHubBackend` | Hub commits | Yes | Anyone with repo access | Optimistic concurrency; conflict → retry |
| Sandbox guest FS | Until stop/delete/TTL | Provider snapshot if used | Thread- vs assistant-scoped | **Orthogonal**. Restore checkpoint ≠ restore `pip` packages |
| Interpreter snapshot | `mode="thread"` in graph state | With checkpointer | Per thread | Does not undo PTC side effects |

**Sandbox scopes:** thread (recommended) keyed `thread-{thread_id}`, `idle_ttl_seconds` (SDK default **600**; DA prod snippet **3600**); assistant keyed `assistant-{assistant_id}` — shared packages/repos across users of that assistant; docs require TTL / snapshots / cleanup. Graph factories must be **async**; factory reads `config["configurable"]["thread_id"]` — **not** full `Runtime` (`server_info` / `execution_info` unavailable). Named lookup + create is the documented “pool.” Skill scripts that must **execute** inside the guest must be `upload_files`’d **before** the run (`before_agent` / `after_agent`; sample `_safe_filename` rejects `..` / glob).

Streaming without `thread_id` + checkpointer: run is **stateless**; disconnect loses in-flight observation; HITL interrupt-mode permissions cannot pause; sandbox factory cannot key `thread-{id}`. Agent Server: remount `useStream({ threadId })`; persist `threadId` in `sessionStorage` via `onThreadId`.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx, sandbox allocate **503**, MCP TCP, ContextHub stale commit, LangSmith stopped→wake | Error rate; 503; 401 replay | Full-jitter retries on **idempotent** reads / Hub re-pull. MCP transport always **raises** — interceptor retry is yours. Do **not** retry `write_file`/`edit_file`/`delete`/`execute` without an idempotency key (`write_file` **overwrites** since 0.7) |
| **Permanent** | `ValueError` (`read_file` omitted from allowlist; `max_execute_timeout <= 0`); Composite+sandbox `NotImplementedError` on `/**`; `StoreBackend` without `store`; 4xx auth | Construction / non-retryable | Fail closed. Never “add LocalShell so execute works” |
| **Poison-pill MCP tools** | Hallucinated / MCPoison-altered `tools/list` (CVE-2025-54136); elicitation auto-accept; `structuredContent` PAN dump; stdio inheriting host env tokens | Hash mismatch; DLP on args; unexpected egress | Gateway hash-pin; fail-closed DLP on MCP args; human elicitation UI; **disable those tools** if the server is down or drifted — do not passthrough |
| **Poison-pill execute** | `LocalShellBackend` in prod; advertising `execute` the backend cannot run; custom middleware re-adds a similarly named tool; pre-0.7.9 hidden-but-callable `execute`; PTC-allowlisted `execute` | Host RCE; `ToolMessage` “Execution not available…”; HITL skipped | Pin `>=0.7.9`; never PTC `execute`; capability hide is not a PEP |
| **Poison-pill paths** | Composite `/memory/` vs `/memories/`; fail-open new paths; write-allow authorizing recursive `delete` (`>=0.7`) | “Memory gone next thread”; subtree wiped | Trailing-slash routes; deny **before** allow; omit `delete` from allowlist if you want 0.6 semantics |
| **Idempotency of execute** | Resume replays `execute`; HITL approve then mutated `command` (TOCTOU); timeout `0` = no timeout on some backends | Duplicate side effects; hash mismatch | Idempotency key + command digest at **execute** time (app-level; **not** in OSS `deepagents`). `interrupt_on` is not a binding. Issue #2894 did not add Cedar on the command string |
| **Denial of wallet** | `grep content` 50k blob; idle TTL 600 s on a 90 s job; assistant-scoped box never reaped | Token ledger; LCU burn | Offload; `files_with_matches`; size idle TTL to session; thread-scoped boxes |

#### 4.3 Circuit breaker closed → open → half-open — MUST NOT fail-open to LocalShell

Independent breakers: **sandbox allocate**, **sandbox execute**, **MCP per server**, **Store put/get**, **parent model** (08). A sandbox 503 must **not** stall a support agent that only needs MCP (**bulkhead**) **and** must not enable host shell.

```
        sandbox 503 / execute 5xx | MCP transport | error-rate window
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  call    │  success resets consecutive count                     │ FAIL FAST│
  └────┬─────┘                                                       │ fallback │
       ▲                                                             │ chain    │
       │ probe OK                                                    └────┬─────┘
       │                                                                  │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 synthetic│
                                                                    │ probe      │
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip condition | Closed → open | Half-open probe | Fallback (**never** unsandboxed execute) |
| --- | --- | --- | --- |
| Sandbox allocate 503 / pool empty | consecutive ≥ **3** or 503 rate window | One `create`/`lookup` | **Queue** the run (or 503 the client). **Never** `LocalShellBackend` |
| Sandbox `execute` 5xx / timeout storm | error-rate + p99 | One `true`/`echo` | Retry with jitter if idempotent; else ToolMessage error; **never** host `subprocess` |
| MCP server down / hash drift | transport raise or pin mismatch | One `tools/list` re-hash | **Disable those tools** (omit from the model-visible list this turn). Agent continues on VFS/MCP-siblings. **Never** strip the gateway |
| Store down | put/get errors | One KV get | Disable `/memories/` writes; keep thread StateBackend |
| Interpreter heap / timeout | `eval` 5 s / snapshot drop | n/a | Return truncated error to model; do not PTC-fallback to `execute` |

**Fallback chain (required interview answer):** **remote sandbox → queue/refuse.** MCP down → **disable those tools**. Store down → **StateBackend only**. Interpreter fail → **no shell consolation prize**. Never: sandbox 503 → LocalShell. Never: HITL timeout → auto-approve `execute`. Never: circuit open → `virtual_mode=False`. Never: MCP 401 → passthrough user bearer.

#### 4.4 Zero-Trust MCP + tool-level RBAC

`permissions=` **will not** save you. MCP tools are additive `tools=` items. An MCP filesystem server is **not** the VFS PDP. Zero-Trust is a **gateway PEP in front of MCP**, not glob rules.

| Zero-Trust control | Spec / 07 | On this data plane |
| --- | --- | --- |
| **Transport** | Authenticated channel. OAuth 2.1 + PKCE `S256`. Clients **MUST** send RFC **8707** `resource` = **canonical MCP server URI** on authorize *and* token. Servers **MUST** accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token to upstream APIs (mint a new token; typically RFC **8693**). stdio is **outside** this OAuth profile (host-env secrets — often worse) | `headers=` static Bearer in the official example is an anti-pattern. Use `OAuthClientProvider` / gateway. Interceptor `override(headers=)` must mint an **MCP-audience** token, not the user’s IdP token |
| **Server allowlist** | Only approved connections | Only put approved entries in `MultiServerMCPClient` connections |
| **Tool allowlist / prefix** | Least privilege | Filter `get_tools()`; `tool_name_prefix=True` against shadowing (`"add"` vs `"math_add"`) |
| **Hash-pin descriptions** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify every `tools/call`. Mismatch → pause / re-consent. CVE-2025-54136 (MCPoison) CVSS **8.8**. 2026-07-28 `ttlMs` without re-hash = rug-pull window | **Not** in adapters. Pin in the **gateway**. Name filter ≠ hash pin |
| **Interceptor PDP** | Model proposes; PEP disposes | `runtime.context` user id; deny list; rate limit; `runtime.state.authenticated`; short-circuit `ToolMessage`. Onion: first interceptor outermost |
| **Identity** | Verified access token. **Never** the LLM | Bind from IdP into RunContext. `user_id` in model JSON is a **proposal** |
| **Elicitation** | Human intent | `accept` / `decline` / `cancel` from a **human UI**; auto-fill forges |

**Tool-level RBAC (what exists vs what you build):**

| Control | What it is | What it is not |
| --- | --- | --- |
| `permissions=` | Path glob PDP, `read`/`write`, `allow\|deny\|interrupt`, first-match, **fail-open** | Per-principal RBAC; `execute`; MCP; `backend.*`; guest `cat` |
| `FilesystemMiddleware(tools=)` | Construction allowlist of `FsToolName`s | MCP catalog |
| `excluded_tools` | Blunt name allowlist (+ execution block `>=0.7.9`) | Per-user roles |
| `interrupt_on` | Review queue on **named** tools (including MCP names if you list them) | An authorization PDP. PTC skips it |
| Gateway / Cedar / OPA | **The** MCP PEP | Not in `deepagents` |
| CLI `ShellAllowListMiddleware` | Mentioned in issues | **Not** in the OSS `create_deep_agent` API |
| LangSmith auth proxy | Sidecar injects headers on matching egress (workspace secrets / opaque creds / AWS SigV4 / GCP SA / **callback** URL). Callback **fails closed** (non-2xx, transport error, malformed JSON → reject, do not forward unauthenticated). First-match-wins **within proxy rules**. GitHub App tokens expire — refresh per run | Not a substitute for never putting secrets **in** the guest |

Correct FS permission ordering: deny `.env` **before** allow `/workspace/**`. Do not set `/**` on a Composite whose default is a sandbox.

#### 4.5 PII pipeline — detect → redact → audit (files and tool args)

Going-to-production `PIIMiddleware("email"|"credit_card", strategy="redact"|"mask", apply_to_input=True)` is LangChain prebuilt middleware — **not FS-aware**. It will not scan a file the agent `read_file`s unless that text re-enters a checked channel. Deep Agents does **not** ship DLP on MCP args. VFS + Store memories are a **retention and injection** store: shared `/policies/` write-denied to the agent; org memory writable only by app code. Offloaded dumps under `/large_tool_results/` may contain PII **in the checkpointer or disk** even after the model sees only a preview. MCP tool **arguments** are the exfil channel (lethal trifecta — 07). DLP on args to external MCP should **fail closed**.

**Pipeline (explicit) — same three steps on four sinks (model I/O, VFS bytes, MCP/execute args, traces):**

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner (Presidio/gateway). Scan: user input, model output, **tool args** (especially MCP), **file contents** on `write_file`/`edit_file`/`read_file` pages that will re-enter context, offload candidates, sandbox env, log/trace payloads, HITL UI. If ML is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on MCP args, `execute` env, and VFS writes — do not send raw PAN to a third-party MCP server, into a checkpoint, or into a guest file.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the task can continue; `block` when the field must not exist (secrets paths, MCP args, sandbox env). Strip the value from VFS **and** the message channel. Do not interceptor-append raw `structuredContent` containing PAN. Do **not** persist raw PAN in traces (sampled APM is not this step). `0.7.9` disabled tracing **inputs** on middleware — reduces accidental PII in LangSmith input fields; **not** a substitute for DLP.
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`redact` / `mask` / `hash` / `block-from-fs` / `block-from-mcp` / `block-from-execute`), detector (`regex` | `pii-middleware` | `gateway`), `correlation_id`, `tenant`, `thread_id`, permission decision, **tool arg digest**, **execute command digest + exit code** (not stdout). A tool call without an audit row is a control-plane bug. Retention: security evidence *and* a sensitive-data asset — GDPR erasure vs legal hold is digest-level.

#### 4.6 Audit of `execute` (immutable logs)

**What exists:** LangSmith traces of shell commands + FS tools when tracing is on; `ExecuteArtifact` / exit code on `ToolMessage.artifact` (`>=0.7.4`); combined stdout/stderr in the tool message (truncated / offloaded); auth-proxy egress policy-visible at the proxy, not in the LLM transcript.

**What does not exist in-tree:** immutable WORM audit log, syscall-level trace, or Cedar/OPA on the command string. `interrupt_on={"execute": True}` is the documented human audit gate. Policy wrapper can log `write`/`edit`; logging `execute` requires wrapping `SandboxBackendProtocol.execute` yourself. Treat sandbox outputs as **untrusted input** to the next model call (ATPA — 07).

---

### 5. Production Enterprise Code

Self-contained. Optional `deepagents` / `langchain-mcp-adapters` imports. Stdlib path runs the same control flow: retries + full jitter, circuit breaker, fallback **sandbox → queue/refuse (never LocalShell)**, MCP down → disable those tools, PII detect→redact→audit on files/args, structured logs with correlation IDs, Composite longest-prefix routing, immutable execute audit. Run: `python deep_agents_execution.py`.

```python
#!/usr/bin/env python3
"""Execution data plane: sandbox execute, MCP PEP, VFS composite, stdlib fallbacks.

Fallback: sandbox 503 → queue; MCP down → disable those tools.
NEVER fail-open to LocalShell / unsandboxed execute.
Run: python deep_agents_execution.py
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
#   from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
#   from langchain_mcp_adapters.client import MultiServerMCPClient
#   from langsmith.sandbox import LangSmithSandbox


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("thread_id", "-"),
            ("tool", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("da_exec")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"thread":"%(thread_id)s","tool":"%(tool)s",'
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


# --- retries + full jitter -------------------------------------------------

def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.2,
    cap_s: float = 2.0,
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
            slog(logging.WARNING, f"retry_backoff attempt={i + 1} sleep_s={sleep_s:.3f}")
            time.sleep(sleep_s)
    assert last is not None
    raise last


# --- circuit breaker closed → open → half-open -----------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    cooldown_s: float = 30.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    @property
    def state(self) -> CircuitState:
        return self._state

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


# --- PII: detect → redact → audit ------------------------------------------

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
    sink: str,
    block_on_pan: bool = True,
) -> str:
    kinds: list[str] = []
    if EMAIL_RE.search(text):
        kinds.append("email")
    if PAN_RE.search(text):
        kinds.append("pan")
    pre = _sha(text)
    block_sinks = {"mcp_args", "execute_env", "vfs_write", "sandbox_env"}
    if "pan" in kinds and block_on_pan and sink in block_sinks:
        audit.append(
            {
                "cid": correlation_id,
                "tenant": tenant_id,
                "sink": sink,
                "kinds": kinds,
                "action": "block",
                "pre": pre,
                "post": _sha(""),
                "detector": "regex",
            }
        )
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    action = "redact" if redacted != text else "allow"
    audit.append(
        {
            "cid": correlation_id,
            "tenant": tenant_id,
            "sink": sink,
            "kinds": kinds,
            "action": action,
            "pre": pre,
            "post": _sha(redacted),
            "detector": "regex",
        }
    )
    return redacted


# --- Composite longest-prefix router ---------------------------------------

@dataclass
class CompositeRouter:
    """Longest prefix wins; unmatched → default (silent — not a 404)."""

    default: str
    routes: dict[str, str]

    def resolve(self, path: str) -> str:
        hits = [p for p in self.routes if path.startswith(p)]
        if not hits:
            return self.default
        return self.routes[max(hits, key=len)]


# --- Sandbox pool: 503 → queue; LocalShell is not a port -------------------

class SandboxUnavailable(RuntimeError):
    """Allocate/execute 503. Caller must queue — never LocalShell."""


class LocalShellForbidden(RuntimeError):
    """Invariant: circuit-open sandbox MUST NOT fail-open to host shell."""


@dataclass
class ExecuteResult:
    stdout: str
    exit_code: int
    queued: bool = False
    refused: bool = False


@dataclass
class SandboxPool:
    capacity: int = 2
    _in_use: int = 0
    fail_allocate: bool = False
    fail_execute: bool = False

    def allocate(self) -> None:
        if self.fail_allocate or self._in_use >= self.capacity:
            raise SandboxUnavailable("sandbox_503")
        self._in_use += 1

    def release(self) -> None:
        self._in_use = max(0, self._in_use - 1)

    def execute(self, command: str, timeout_s: float | None) -> ExecuteResult:
        if self.fail_execute:
            raise ConnectionError("sandbox_execute_5xx")
        return ExecuteResult(stdout=f"ok:{command[:80]}", exit_code=0)


def local_shell_execute(command: str) -> ExecuteResult:
    raise LocalShellForbidden(f"refused_local_shell:{command[:40]}")


# --- MCP gateway PEP (permissions= does not cover MCP) ---------------------

@dataclass
class McpGateway:
    allowed_tools: set[str]
    surface_hash: dict[str, str]
    breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("mcp"))
    disabled: set[str] = field(default_factory=set)
    fail_transport: bool = False

    def _pin(self, name: str, description: str, schema: str) -> str:
        blob = json.dumps(
            {"name": name, "description": description, "inputSchema": schema},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def call(
        self,
        name: str,
        args: str,
        *,
        description: str,
        schema: str,
        extra: dict[str, Any],
    ) -> str:
        if name in self.disabled or name not in self.allowed_tools:
            slog(logging.WARNING, f"mcp_disabled:{name}", **extra)
            return json.dumps({"status": "error", "reason": "mcp_tool_disabled", "tool": name})
        expected = self.surface_hash.get(name)
        got = self._pin(name, description, schema)
        if expected is not None and got != expected:
            self.disabled.add(name)
            slog(logging.ERROR, f"mcp_hash_drift:{name}", **extra)
            return json.dumps({"status": "error", "reason": "mcp_hash_drift", "tool": name})
        try:
            self.breaker.allow()

            def _once() -> str:
                if self.fail_transport:
                    raise ConnectionError("mcp_transport")
                return json.dumps({"status": "ok", "tool": name, "echo": args[:120]})

            out = retry_call(_once)
            self.breaker.record_success()
            return out
        except (CircuitOpenError, ConnectionError, TimeoutError) as exc:
            self.breaker.record_failure()
            self.disabled.add(name)
            slog(logging.ERROR, f"mcp_degrade:{type(exc).__name__}", **extra)
            return json.dumps({"status": "error", "reason": "mcp_disabled_after_failure", "tool": name})


# --- Runtime ----------------------------------------------------------------

@dataclass
class ExecutionRuntime:
    pool: SandboxPool
    mcp: McpGateway
    router: CompositeRouter
    sandbox_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("sandbox"))
    pii_audit: list[dict[str, Any]] = field(default_factory=list)
    execute_audit: list[dict[str, Any]] = field(default_factory=list)  # append-only WORM stand-in
    queue: list[dict[str, Any]] = field(default_factory=list)
    allow_local_shell_fallback: bool = False  # MUST stay False in prod

    def _cid(self, correlation_id: str | None) -> str:
        return correlation_id or str(uuid.uuid4())

    def vfs_write(
        self,
        path: str,
        content: str,
        *,
        tenant_id: str,
        thread_id: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        cid = self._cid(correlation_id)
        extra = {"correlation_id": cid, "tenant_id": tenant_id, "thread_id": thread_id, "tool": "write_file"}
        backend = self.router.resolve(path)
        safe = pii_detect_redact_audit(
            content,
            audit=self.pii_audit,
            correlation_id=cid,
            tenant_id=tenant_id,
            sink="vfs_write",
            block_on_pan=True,
        )
        slog(logging.INFO, f"vfs_write backend={backend} path={path}", **extra)
        return {"backend": backend, "path": path, "bytes": len(safe), "cid": cid}

    def execute(
        self,
        command: str,
        *,
        tenant_id: str,
        thread_id: str,
        timeout_s: float | None = 30.0,
        correlation_id: str | None = None,
    ) -> ExecuteResult:
        cid = self._cid(correlation_id)
        extra = {"correlation_id": cid, "tenant_id": tenant_id, "thread_id": thread_id, "tool": "execute"}
        digest = _sha(command)
        slog(logging.INFO, "execute_start", **extra)

        def _audit(**fields: Any) -> None:
            row = {"cid": cid, "tenant": tenant_id, "thread": thread_id, "arg_digest": digest, **fields}
            self.execute_audit.append(row)  # immutable log: append only, no rewrite API

        try:
            pii_detect_redact_audit(
                command,
                audit=self.pii_audit,
                correlation_id=cid,
                tenant_id=tenant_id,
                sink="execute_env",
                block_on_pan=True,
            )
        except PermissionError:
            _audit(action="pii_block", exit_code=-1)
            slog(logging.ERROR, "execute_pii_block", **extra)
            return ExecuteResult(stdout="refused:pii", exit_code=-1, refused=True)

        try:
            self.sandbox_breaker.allow()
            self.pool.allocate()
            try:
                result = retry_call(lambda: self.pool.execute(command, timeout_s))
            finally:
                self.pool.release()
            self.sandbox_breaker.record_success()
            _audit(action="ok", exit_code=result.exit_code)
            slog(logging.INFO, "execute_ok", **extra)
            return result
        except CircuitOpenError:
            _audit(action="queued_circuit_open", exit_code=-1)
            return self._queue_or_refuse("circuit_open", extra, command)
        except SandboxUnavailable:
            self.sandbox_breaker.record_failure()
            _audit(action="queued_503", exit_code=-1)
            return self._queue_or_refuse("sandbox_503", extra, command)
        except (ConnectionError, TimeoutError) as exc:
            self.sandbox_breaker.record_failure()
            _audit(action=f"queued_{type(exc).__name__}", exit_code=-1)
            return self._queue_or_refuse(type(exc).__name__, extra, command)

    def _queue_or_refuse(self, reason: str, extra: dict[str, Any], command: str) -> ExecuteResult:
        if self.allow_local_shell_fallback:
            # Prod invariant: this branch must never be enabled.
            return local_shell_execute(command)
        self.queue.append({"reason": reason, "command_digest": _sha(command), "cid": extra["correlation_id"]})
        slog(logging.WARNING, f"execute_queued:{reason}", **extra)
        return ExecuteResult(stdout=f"queued:{reason}", exit_code=-1, queued=True)

    def mcp_call(
        self,
        name: str,
        args: str,
        *,
        description: str,
        schema: str,
        tenant_id: str,
        thread_id: str,
        correlation_id: str | None = None,
    ) -> str:
        cid = self._cid(correlation_id)
        extra = {"correlation_id": cid, "tenant_id": tenant_id, "thread_id": thread_id, "tool": name}
        safe = pii_detect_redact_audit(
            args,
            audit=self.pii_audit,
            correlation_id=cid,
            tenant_id=tenant_id,
            sink="mcp_args",
            block_on_pan=True,
        )
        return self.mcp.call(name, safe, description=description, schema=schema, extra=extra)


def build_runtime() -> ExecutionRuntime:
    pin = hashlib.sha256(
        json.dumps(
            {"name": "crm_get", "description": "get ticket", "inputSchema": "id"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ExecutionRuntime(
        pool=SandboxPool(capacity=1),
        mcp=McpGateway(allowed_tools={"crm_get"}, surface_hash={"crm_get": pin}),
        router=CompositeRouter(default="state", routes={"/memories/": "store", "/policies/": "store_org"}),
    )


if __name__ == "__main__":
    rt = build_runtime()

    w = rt.vfs_write("/memories/prefs.md", "theme=dark", tenant_id="acme", thread_id="t-1", correlation_id="cid-1")
    assert w["backend"] == "store"
    miss = rt.vfs_write("/memory/prefs.md", "oops", tenant_id="acme", thread_id="t-1", correlation_id="cid-1b")
    assert miss["backend"] == "state"  # silent default — the Composite footgun

    r1 = rt.execute("pytest -q", tenant_id="acme", thread_id="t-1", correlation_id="cid-2")
    assert r1.exit_code == 0 and r1.queued is False
    assert any(row["action"] == "ok" for row in rt.execute_audit)

    rt.pool.fail_allocate = True
    r2 = rt.execute("pytest -q", tenant_id="acme", thread_id="t-1", correlation_id="cid-3")
    assert r2.queued is True and r2.stdout.startswith("queued:")
    assert rt.allow_local_shell_fallback is False

    try:
        local_shell_execute("rm -rf /")
        raise AssertionError("LocalShell must be forbidden")
    except LocalShellForbidden:
        pass

    out = rt.mcp_call(
        "crm_get",
        '{"id":"55","note":"ada@example.com"}',
        description="get ticket",
        schema="id",
        tenant_id="acme",
        thread_id="t-1",
        correlation_id="cid-4",
    )
    assert "[EMAIL_" in out or "ok" in out
    assert any(row["sink"] == "mcp_args" for row in rt.pii_audit)

    try:
        rt.mcp_call(
            "crm_get",
            '{"pan":"4111111111111111"}',
            description="get ticket",
            schema="id",
            tenant_id="acme",
            thread_id="t-1",
            correlation_id="cid-5",
        )
        raise AssertionError("PAN on MCP args must block")
    except PermissionError as exc:
        assert "pii_block:mcp_args" in str(exc)

    rt.mcp.fail_transport = True
    degraded = rt.mcp_call(
        "crm_get",
        '{"id":"55"}',
        description="get ticket",
        schema="id",
        tenant_id="acme",
        thread_id="t-1",
        correlation_id="cid-6",
    )
    assert "mcp_disabled" in degraded
    assert "crm_get" in rt.mcp.disabled

    print("ok", len(rt.execute_audit), "execute audit rows,", len(rt.pii_audit), "pii rows,", len(rt.queue), "queued")
```

**Wiring notes (not in the script):** production `create_deep_agent` gets a **sandbox instance** (async factory, `thread-{thread_id}`, `idle_ttl_seconds` sized to session), `CompositeBackend` routes for `/memories/`, permissions scoped to **routes only** when default is sandbox, `interrupt_on={"execute": True, "delete": True}`, MCP tools from `get_tools()` **after** gateway allowlist + hash-pin, `PIIMiddleware` on the chat channel **plus** the file/args pipeline above, auth proxy for GitHub/npm — never `GITHUB_TOKEN` in the guest. Pin `deepagents>=0.7.9` (and `>=0.7.10` if you rely on sandbox glob errors). `handle_tool_errors=True` does not retry TCP. Do not PTC-allowlist `execute`.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Multi-tenant coding agent: remote sandbox vs LocalShell vs interpreter-only

**Problem.** Per-user “fix my repo / run tests” copilot. Untrusted prompt + untrusted repo bits. Need `pip install`, pytest, maybe git. Multi-tenant SaaS. Security forbids host shell. Platform split: “thread-scoped LangSmith/E2B/Daytona sandbox,” “`LocalShellBackend` on a worker VM — it is faster,” “interpreter-only so we never shell.”

**Proposed architecture (recommended):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: async graph factory                            │
  │ JWT →   │   │   LangSmithSandbox (or peer) name=thread-{thread_id}    │
  │ user_id │   │   idle_ttl sized to session (600 vs 3600 $ vs cold)     │
  │         │   │   Composite default=sandbox                             │
  │         │   │     /memories/ → StoreBackend ns=(user.identity,)       │
  │         │   │   permissions: routes only — NEVER /**  (raises)        │
  │         │   │   interrupt_on execute+delete   pin >=0.7.9             │
  │         │   │   PII detect→redact→audit; WORM execute arg_digest      │
  │         │   │   auth proxy (fail-closed callback) — no keys in guest  │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: sandbox-as-tool (agent process on your server) │
                    │   FS tools = POSIX scripts over execute()            │
                    │   upload_files seed / download_files harvest         │
                    │   stream v3 + ExecuteArtifact exit code              │
                    │   interpreter OPTIONAL companion, PTC ≠ execute      │
                    │   sandbox 503 → queue  (breaker; never LocalShell)   │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** Remote sandbox as the **default** Composite child so `execute` exists; Store route for durable memory the guest cannot `cat`; HITL on execute; auth proxy for GitHub/npm; `FilesystemMiddleware` allowlist may list all eight names (`read_file` required) or omit the allowlist; seed from object storage; never mount host `$HOME`. Interpreter as **companion** (`CodeInterpreterMiddleware(ptc=["grep","read_file"], max_ptc_calls=64)`), not a replacement — do not PTC `execute`. Capacity: 100 concurrent tenants × thread-scoped boxes × LangSmith Developer **10-sandbox cap** does not work — Plus/Enterprise or E2B Pro (100 concurrent, purchasable 1,100). Agent Server one-run-per-thread still applies.

**Trade-off matrix:**

| Axis | **A1 Thread-scoped remote sandbox (recommended)** | **A2 `LocalShellBackend` on a worker VM** | **A3 Interpreter-only (no `execute`)** |
| --- | --- | --- | --- |
| **Cost** | Model **[inferred] $180 / 1k** (8-call Sonnet 4.6) + sandbox **$1.64 / 1k** (90 s) or **$13 / 1k** (90 s + 600 s TTL). E2B 90 s **$2.72 / 1k** | Worker VM amortized; **$0** sandbox SKU until the incident | Sandbox line **$0**; still pay model tokens |
| **Latency** | Cold start **270–6,480 ms** p99 depending on provider table; warm execute extra **100 / 400 / 1,500 ms [inferred]** + command; LangSmith ready fuse **30,000 ms**. Parent TTFT still **640 / 2,560 / 5,120 ms [inferred]** | **~0 ms** extra to spawn shell — that is the incident. Default timeout fuse **120,000 ms** | `eval` fuse **5,000 ms**; ~0 cold start; **cannot** pytest |
| **Ops complexity** | TTL, snapshots, auth proxy, async factory, pool/queue | Looks simple; production docs forbid it on servers | Low; PTC allowlist is the whole boundary |
| **Security posture** | Strong vs host if **one box per thread/user**; useless PDP on `execute`; secrets via proxy fail-closed; context injection **inside** the guest remains | **None.** `virtual_mode` does not jail `execute()`. Host `.env` / SSH keys / SSRF | Strong vs OS; weak vs process (`quickjs-rs` same-process). No git/pip |
| **Scalability ceiling** | Provider concurrency (Developer **10**; E2B Pro **100**/1,100) + one-run-per-thread | One host user for all tenants — not a ceiling, a CVE | Process heap **64 MiB**; `max_ptc_calls` **256** |

**Decision.** **A1 wins** for this problem statement. A2 is local CLI / isolated CI runner **only**. A3 is transformations, not a coding agent. Interpreter may sit **beside** A1 for batch review loops. Do not share an **assistant-scoped** sandbox across tenants.

#### Scenario B — MCP-heavy support agent with `CompositeBackend` `/memories/`

**Problem.** Ticket copilot. Tools from CRM, knowledge base, order API via MCP. Must remember per-customer preferences. **Must not** shell the host. Team temptation: “add `LocalShellBackend` just in case they want a script,” and “VFS permissions will cover the MCP filesystem server.”

**Proposed architecture (recommended):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent                              │
  │ JWT     │   │   Composite default=StateBackend()                      │
  │ user_id │   │     /memories/ → Store ns=(user.identity,)              │
  │ org_id  │   │     /policies/ → Store ns=(org_id,)  agent write-deny   │
  │         │   │   execute OFF (StateBackend default → tool hidden)      │
  │         │   │   MultiServerMCPClient HTTP via enterprise gateway      │
  │         │   │     get_tools → name allowlist; tool_name_prefix=True   │
  │         │   │     OAuthClientProvider; RFC 8707 audience=that server  │
  │         │   │     NO headers passthrough of the user token            │
  │         │   │   interceptors: per-user args + rate limit + hash-pin   │
  │         │   │   PII on input + fail-closed DLP on MCP args            │
  │         │   │   stream v3 + thread_id; MCP down → disable those tools │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: FS tools on State/Store (permissions apply)    │
                    │   MCP tools/call on additive tools= (permissions NO) │
                    │   stateless sessions unless a server is documented   │
                    │     stateful — then client.session scoped to the RUN │
                    │   stream.subagents if a research child fetches KB    │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** StateBackend default so `execute` is **capability-hidden**. Store routes for memories/policies. Permissions: deny write on `/policies/**`; optionally allow `/memories/**` write or keep app-owned. MCP only over **http** through the gateway; `tool_name_prefix=True`; interceptors for authz. Stateless default. `PIIMiddleware` on input; gateway DLP fail-closed on MCP args; do not interceptor-append raw `structuredContent` containing PAN. Persist `thread_id`.

**Trade-off matrix:**

| Axis | **B1 State+Store composite + gateway MCP (recommended)** | **B2 Same + `LocalShellBackend` “just in case”** | **B3 Disk `FilesystemBackend` + stdio MCP** |
| --- | --- | --- | --- |
| **Cost** | Model tokens + MCP vendor; **$0** sandbox. Offload keeps grep dumps at **[inferred] $6 / 1k** vs **$1,200** undisciplined | Adds host blast radius; no sandbox SKU | Disk ops + stdio process tax; `.env` readable under `root_dir` |
| **Latency** | Parent TTFT **640 / 2,560 / 5,120 ms [inferred]**; MCP **80 / 400 / 2,000 ms [inferred policy]**; no cold start | Fast `execute` on the API host — the incident | stdio session/process lifetime ≠ stateless client (unpublished spawn) |
| **Ops complexity** | Gateway, hash-pins, namespace factories, thread_id | Looks like a feature flag; becomes host RCE | stdio OAuth profile **does not apply**; host-env creds |
| **Security posture** | FS PDP on VFS; **separate** MCP PEP; execute absent; PII block on args | `permissions=` does not cover shell; `virtual_mode` irrelevant to `execute()` | MCP filesystem server ≠ VFS PDP; fail-open globs; secrets on disk |
| **Scalability ceiling** | Store + provider MCP RPM; one-run-per-thread | One VM for all tenants | Process-per-server stdio does not tenant |

**Decision.** **B1 wins.** Do **not** add LocalShell “just in case.” Do **not** put MCP filesystem servers alongside VFS permissions and assume they are the same PDP. If a ticket workflow later needs a script, that is **Scenario A** (remote sandbox), not a fallback from B1.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| Host RCE (“works on my laptop”) | `LocalShellBackend` / `virtual_mode` thought to jail shell | Incident; `.env` / SSH keys / `curl` exfil | Never on shared hosts; `BaseSandbox` + auth proxy; `interrupt_on` execute; `inherit_env=False` is **insufficient** |
| Composite route miss | `/memory/` or `/Memories/` vs `/memories/`; unmatched → **default** | “Memory gone next thread”; no exception | Trailing-slash routes; deny writes outside `/workspace/` + `/memories/`; tests for prefix typos |
| `execute` cannot see Store files | Shell on Composite **default** only; no host mapping for remote sandbox | Agent `cat /memories/...` fails / empty | Use file tools, not execute, against virtual prefixes |
| `delete` surprises | Missing tool pre-0.7; write-allow ⇒ recursive delete `>=0.7`; all-or-nothing directory refuse | Subtree wiped or “delete broken” | Pin 0.7+; narrower deny/interrupt; omit `delete` from allowlist for 0.6 semantics |
| `execute` advertised but cannot run | Custom tool same name; Composite routed backend ≠ default; pre-0.7.9 hide-only; allowlist on StateBackend is **no-op hide** | `ToolMessage` “Execution not available…”; wasted turn | Pin `>=0.7.9`; don’t re-add; timeout-incapable backend → upgrade package or omit timeout |
| MCP token passthrough | Docs `headers` Bearer example; interceptor copies user token; stdio inherits env | Wrong-audience rate limits; stolen-token proxy | OAuthClientProvider / gateway; audience = MCP server; never copy `runtime` user tokens upstream |
| Stream / HITL / sandbox key lost | No `thread_id` + checkpointer | Refresh loses run; interrupt cannot pause | Always `config={"configurable": {"thread_id": ...}}`; persist client `threadId` |
| `read_file` omitted from allowlist | Construction `ValueError` | Agent never starts | Keep `read_file`. Hiding it via `excluded_tools` while leaving FS middleware = offload to a VFS the model cannot read |
| Grep/execute context overflow | 20k evictor / 100k byte cap / sandbox file offload; summarization excluded | Window dies; $ spike (**[inferred] $1,200 / 1k**) | Keep compression; `grep_max_count`; `output_mode`; `files_with_matches` |
| `write_file` clobber | Overwrite since 0.7 (no file-exists error) | Silent last-write-wins | `edit_file` + permissions/HITL |
| Sandbox glob “no matches” | Pre-0.7.10 swallowed glob failures | False empty | Pin `>=0.7.10` |
| ContextHub stale commit | Optimistic concurrency | Write fail | Re-pull/retry |
| Store namespace glob injection | User string in namespace | Cross-tenant | Factory rejects `*` `?`; don’t interpolate user strings |
| Interpreter snapshot vs PTC | Restore JS vars, not world | Duplicate side effects | Idempotent tools; `mode="call"` if needed; never PTC privileged tools |
| Multimodal scrub | Model lacks vision; 0.7.2 strips blocks | Text notice; wasted round-trip | Check `model.profile` |
| Agent-in-sandbox keys | Keys in guest | Injection exfil | Sandbox-as-tool + auth proxy (fail-closed) |
| Shared assistant sandbox | Cross-user files + memory poisoning | Tenant leak | Thread-scoped boxes; namespaced store |
| `DeltaChannel` downgrade | 0.6+ checkpoints unreadable by older deepagents | Resume fail | Never mixed-version a thread |
| Elicitation auto-accept | Callback forges user details | Bad CRM writes | Human UI; treat as HITL |
| Unanchored interrupt glob | `/**/secrets` over-fires on bulk ls/glob/grep/delete | Reviewer fatigue | Anchor `/secrets/**` |
| Empty ls/glob JSON parse | `"No files found"` since 0.7 | Parser exception | Do not `json.loads` the tool string |
| MCP `handle_tool_errors` confusion | TCP still raises; `isError` does not hit exception interceptors | Uncaught transport; silent semantic error | Interceptor retry on transport; don’t expect default retries |

No public Deep Agents post-mortem corpus beyond GitHub issues (#2894, changelog gates). Do not invent incidents.

---

## Key Takeaways

- The execution environment is the **data plane** (four layers: tools, VFS, FS permissions, code execution + streams). LangGraph is still the scheduler. `create_deep_agent` only **binds** `backend=` / `permissions=` / `tools=` / `FilesystemMiddleware`.
- `permissions=` is first-match-wins and **fail-open**, and it does **not** cover MCP, `execute`, `task`, PTC, or `backend.*`. Composite+sandbox `/**` is a construction **interlock**, not a runtime PEP.
- `read_file` is mandatory on the FS allowlist because offload is useless if the model cannot page the blob back. Pin **`>=0.7.9`** so `excluded_tools` also blocks execution.
- Composite unmatched paths fall through to **default** — a silent persistence bug, not an HTTP 404. `execute` runs on Composite **default** only.
- **Never `LocalShellBackend` in prod.** `virtual_mode` is an FS-tool jail. Sandbox ≠ secret vault; auth proxy **fails closed**. Sandbox 503 → **queue**; MCP down → **disable those tools**; never unsandboxed execute.
- Idle TTL, not `pytest` duration, dominates sandbox **$**. Grep `content` dumps dominate **tokens** (offload **[inferred] $6 vs $1,200 / 1k**).
- v3 `stream.subagents` is the UX handle; `subgraphs` is Pregel. Disconnect ≠ cancel; rejoin needs `thread_id`.
- Zero-Trust MCP is a **gateway PEP**: OAuth 2.1 + RFC 8707 audience, **no** token passthrough, hash-pinned tool JSON, interceptor PDP. PII is **detect → redact → audit** on files **and** tool args, fail-closed on MCP/`execute`/VFS writes.

---

## Interview Q&A

**Q1. What is the Deep Agents execution environment, in one minute?**  
I treat it as the data plane — where the agent acts — not a second runtime. Four layers: tools, virtual filesystem, filesystem permissions, code execution; typed streams to observe. LangGraph still runs ReAct. `create_deep_agent` only binds `backend=`, `permissions=`, additive `tools=`, and `FilesystemMiddleware`. Eight `FsToolName`s; `task` is subagent middleware; MCP is adapters, not a native runtime. Permissions are fail-open and FS-only. I never ship `LocalShellBackend`.

**Q2. Walk a tool call to a stream event.**  
Model emits `tool_calls`. Name filter (`excluded_tools` also blocks execution as of 0.7.9) and capability hide (`execute`/`delete` if the backend cannot). Built-in FS tools hit the path PDP — first match wins, no match allows — then the backend. MCP and `execute` skip that PDP: MCP goes through my gateway PEP; `execute` goes to `SandboxBackendProtocol`. Composite longest-prefix routes; miss falls through to default. Result is an untrusted `ToolMessage`; large blobs offload at 20k tokens; v3 `stream.tool_calls.output_deltas` and `stream.subagents` for `task`. Agent Server: SSE drop does not cancel the worker.

**Q3. `excluded_tools` vs `FilesystemMiddleware(tools=)`.**  
Allowlist is construction: only listed FS names exist, and `read_file` is required or I get `ValueError`. `excluded_tools` is a post-injection name filter that can also drop caller tools; since 0.7.9 it blocks execution. Capability filter still hides `execute` on StateBackend even if I list it — that’s a no-op, not an error. Declarative subagents do not inherit my allowlist.

**Q4. Give me `$ per 1k` for this plane.**  
Inferred, not a SKU. Undisciplined `grep content` of 50k tokens retained across 8 Sonnet 4.6 calls: **$1,200 / 1k** vs offload+page **$6 / 1k**. Coding agent: model **$180 / 1k** (8 calls, 2k cached prefix); LangSmith 0.5 vCPU / 2 GiB × 90 s **$1.64 / 1k**, or **$13 / 1k** if it sits for the 600 s idle TTL — idle is ~8× the work bill. E2B default 2 vCPU / 0.5 GiB × 90 s **$2.72 / 1k**. Interpreter sandbox line is $0.

**Q5. What p50/p95/p99 do you put on execute / MCP / VFS?**  
Deep Agents publishes none. I quote the independent cold-start snapshot in ms: Vercel **670 / 1,040 / 1,120**, Modal **880 / 1,000 / 1,080**, E2B **1,610 / 1,770 / 1,810**, Cloudflare **5,060 / 6,040 / 6,480**; Daytona **270 / 430 / 440** in that run at **37% success** — not an SLO. LangSmith actuals unpublished; I budget **5,000 / 15,000 / 30,000** (p99 = 30 s ready-wait fuse). Policy: parent TTFT **640 / 2,560 / 5,120**; ReAct+local FS **2,000 / 8,000 / 20,000**; warm execute API extra **100 / 400 / 1,500**; first execute on cold E2B **3,610 / 9,770 / 21,810**; MCP HTTP **80 / 400 / 2,000**; `eval` **20 / 200 / 5,000**; HITL **30,000 / 180,000 / 600,000** expire-deny. I do not put first-`execute` on a 5 s gateway timeout.

**Q6. Permissions — is that Zero Trust for MCP and shell?**  
No. Fail-open path PDP for built-in FS tools only. `execute`, `task`, MCP, PTC, and `backend.*` are uncovered; #2894 declined SDK `ExecutePermission`. Composite+sandbox: I cannot pretend `/**` constrains the shell — construction raises. Zero-Trust MCP is a gateway PEP: server allowlist, tool allowlist, hash-pin name+description+schema every `tools/call` (CVE-2025-54136), OAuth 2.1, RFC 8707 audience = that MCP server, no user-token passthrough. Identity from the verified token / RunContext, never model JSON. Official `Authorization: Bearer` example is static passthrough-shaped — I do not copy it.

**Q7. PII — detect → redact → audit on this plane.**  
`PIIMiddleware` is not FS-aware and will miss `read_file` bytes until they re-enter a checked channel. I scan user I/O, VFS writes, MCP args, and execute/env. Detect regex + optional ML; if ML is down I still mask chat and I **block** PAN into MCP args, VFS writes, and execute. Redact to stable hashes; audit WORM of pre/post sha256, entity types, action, detector, cid, thread, arg digest — not raw PAN. Offloaded `/large_tool_results/` can still hold PII in the checkpointer. 0.7.9 hiding middleware trace inputs is not DLP.

**Q8. Circuit breaker and fallback when the sandbox is 503.**  
The library does not ship a breaker. Closed → open → half-open with one probe. Sandbox 503 or execute 5xx: queue or refuse. MCP transport/hash drift: disable those tools and keep the agent on VFS. I never fail-open to `LocalShellBackend`, never unsandboxed `execute`, never HITL-timeout auto-approve. Independent breakers so a sandbox outage does not take down an MCP-only support bot.

**Q9. CompositeBackend — how does routing actually work?**  
Longest prefix wins; no match uses default with **no error**. `ls`/`glob`/`grep` aggregate. 0.7 stopped false-empty listings when the default backend errors. `delete` on a child that cannot delete errors rather than hiding. `execute` is default-only; a remote sandbox will not see Store-backed `/memories/` as a host path. `/memory/foo` is not `/memories/`. Writes to two children are not one transaction.

**Q10. Sandbox vs LocalShell vs interpreter — pick for a coding copilot.**  
Remote thread-scoped sandbox. LocalShell is unrestricted `subprocess.run(shell=True)` — local CLI/CI only. Interpreter is QuickJS `eval`, 64 MiB / 5 s / 4000 chars, no pip/git, PTC bypasses HITL, same-process boundary. I may run interpreter **beside** the sandbox for batch loops with PTC `grep`/`read_file` only. Secrets stay in an auth proxy that fails closed; never env-in-box, never agent-in-sandbox keys.

**Q11. MCP sessions and streaming — what do people get wrong?**  
Adapter default is a **new session per `tools/call`**. That is not the stdio process lifetime — the child can live with the client object while sessions are per-call. Stateful `client.session()` is for servers that keep context, scoped to the **run**. `handle_tool_errors` maps semantic `isError`, not TCP. v3 `stream.subagents` is the product UI; `subgraphs` is Pregel. Disconnect does not cancel the Agent Server worker; rejoin needs `thread_id`. Streaming does not save tokens.

**Q12. What did 0.7.x change on this plane that still bites?**  
`delete` exists and write-allow authorizes recursive delete; `write_file` overwrites; empty ls/glob is the string `No files found`; `virtual_mode` default true but still not a shell jail; allowlist requires `read_file`; execute artifacts and in-sandbox offload; 0.7.9 exclusion-is-enforcement and tracing inputs off; 0.7.10 glob failures surface. Footguns that remain: fail-open permissions, Composite fallthrough, idle TTL dollars, MCP uncovered by the FS PDP, no in-tree WORM for execute.

---

## Key Numbers to Memorize

### Package / tools / versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **8 / 9 / 10** | `FsToolName`s / + `task` / + opt-in `write_todos` |
| **`read_file` required** | `FilesystemMiddleware(tools=)` else `ValueError` |
| **`>=0.7.9`** | `excluded_tools` blocks **execution**; middleware tracing inputs off |
| **`>=0.7.10`** | Sandbox glob failures no longer swallowed |
| **`>=0.7.4`** | Execute exit code on `ToolMessage.artifact` |
| **`>=0.7.3`** | Exact-match `delete` first-match-wins |
| **0.6 / LangChain 1.3** | `stream_events` `version="v3"`; `stream.subagents` |
| **#2894** | `ExecutePermission` / `TaskPermission` **declined** |

### Tokens / knobs
| Number | What |
| --- | --- |
| **20,000 / 10 lines** | Tool-result offload / preview |
| **50,000** | Human-message eviction |
| **85%** | Delay write/edit-input offload until this window fraction |
| **1,000** | `grep_max_count` |
| **100 lines / 2000** | Model `read_file` limit vs protocol `read` default |
| **4** | `NUM_CHARS_PER_TOKEN` |
| **4,005 → 2,302 / 5,395 → 1,895** | v0.7 tool-description tokens (−43%); default-agent turn (−65%) |
| **4000 / 256 / 64 MiB / 5.0 s** | Interpreter `max_result_chars` / `max_ptc_calls` / heap / eval timeout |
| **100,000 / 120 s** | LocalShell `max_output_bytes` / default timeout |
| **3600 s** | `max_execute_timeout` |

### $ / SKUs **[inferred]** where marked
| Number | What |
| --- | --- |
| **$3 / $15 / $3.75 / $0.30** | Sonnet 4.6 in / out / 5m write / cache read per MTok |
| **[inferred] $1,200 vs $6 per 1k** | 50k grep dump × 8 calls vs 20k offload + one page |
| **[inferred] $120 / 1k** | Same dump if cached at $0.30/MTok (still 20× offload) |
| **[inferred] $180 / 1k** | Coding-agent model (8 calls, 2k prefix) |
| **[inferred] $1.64 / $13 per 1k** | LangSmith 0.5 vCPU / 2 GiB × 90 s vs 90 s+600 s TTL |
| **[inferred] $2.72 / 1k** | E2B 2 vCPU / 0.5 GiB × 90 s |
| **$0.0576 / $0.01845** | **[inferred]** LangSmith $/vCPU-hr / $/GiB-hr from 0.0384×$1.50, 0.0123×$1.50 |
| **0.0384 LCU / 0.0123 LCU / $1.50** | Published LCU rates / 1 LCU |
| **$0.000014 / $0.0000045** | E2B per vCPU-s / GiB-s |
| **5 LCU + 1 LSU / 10 sandboxes** | LangSmith Developer included / cap |

### Sandbox / MCP / production
| Number | What |
| --- | --- |
| **30 s / 600 s / 3600 s / 14 days** | LangSmith ready-wait default / idle TTL default / DA prod snippet TTL / typical delete-after-stop |
| **0.5 vCPU / 4 GiB per vCPU / ~2 GiB** | LangSmith size default / ratio / **[inferred]** memory at default CPU |
| **2× CPU burst** | If host has spare; resize at next start |
| **20 / 100 / 1,100** | E2B Hobby / Pro / purchasable concurrency |
| **at most one run / `thread_id`** | Agent Server |
| **localhost:2024** | Local `useStream` |
| **fail-open** | `permissions=` when no rule matches |
| **RFC 8707 / RFC 8693** | MCP `resource` audience / no passthrough (exchange) |
| **8.8** | CVE-2025-54136 MCPoison CVSS |

### Latency / security (numeric ms)
| Number | What |
| --- | --- |
| **670 / 1,040 / 1,120 ms** | Vercel cold start p50/p95/p99 (third-party table) |
| **880 / 1,000 / 1,080 ms** | Modal cold start |
| **890 / 3,270 / 3,500 ms** | Runloop cold start |
| **1,610 / 1,770 / 1,810 ms** | E2B cold start |
| **5,060 / 6,040 / 6,480 ms** | Cloudflare cold start |
| **270 / 430 / 440 ms (37%)** | Daytona that run — **not** vendor <90 ms |
| **5,000 / 15,000 / 30,000 ms** | **[inferred policy]** LangSmith sandbox start (p99 = 30 s ready-wait fuse; actuals unpublished) |
| **640 / 2,560 / 5,120 ms** | **[inferred policy]** streaming TTFT |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** ReAct cycle + local FS |
| **5 / 20 / 80 ms** | **[inferred policy]** StateBackend/permission extra |
| **100 / 400 / 1,500 ms** | **[inferred policy]** warm execute API extra (excl. command) |
| **3,610 / 9,770 / 21,810 ms** | **[inferred]** first execute on cold E2B (cycle + table) |
| **80 / 400 / 2,000 ms** | **[inferred policy]** MCP Streamable HTTP `tools/call` |
| **20 / 200 / 5,000 ms** | **[inferred policy]** interpreter `eval` (p99 = 5.0 s fuse) |
| **1 / 5 / 20 ms** | **[inferred]** LocalShell spawn extra (forbid in prod); command fuse **120,000 ms** |
| **3,600,000 ms** | `max_execute_timeout` fuse |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** HITL execute clock; p99 expire-deny |
| **detect → redact → audit** | PII on model I/O, VFS, MCP/execute args, traces **before** persist |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs.

---

*End of module. Practice the Q&A out loud; recode the sandbox→queue breaker and MCP disable path from memory; recompute the grep $1,200 vs $6 and the 90 s vs 600 s TTL sandbox bill on a whiteboard with the assumptions listed.*
