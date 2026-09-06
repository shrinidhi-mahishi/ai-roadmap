# Module 09: Deep Agents Execution Environment

## What Is This?

Module 08 built the *brain* of the agent -- the middleware stack, profiles, sub-agents, prompt assembly. This module is about the *hands and feet*: where the agent actually reads files, writes code, runs commands, and stores results. The execution environment is the **data plane** -- four stacked layers (tools, virtual filesystem, filesystem permissions, code execution) plus typed streams as the observation plane. LangGraph still runs the ReAct loop; Deep Agents injects a VFS plus an optional shell/eval surface through `FilesystemMiddleware` and `CodeInterpreterMiddleware`.

Think of it like renting office space for your employee. The "virtual filesystem" is their desk -- drawers they can organize however they want. The "backends" determine whether that desk is a cardboard box that disappears when they go home (in-memory StateBackend), a real filing cabinet (FilesystemBackend), or a cloud drive shared across offices (StoreBackend). The "sandbox" is a sealed clean room: the agent can run experiments there without risking the main building's plumbing.

The key design insight is **separation of concerns**: the agent's file operations (read, write, edit, grep) are identical regardless of where files physically live. You swap backends without changing agent code. **Permissions are fail-open and FS-tools-only.** First-match-wins; no match means allow. They do **not** cover MCP, custom tools, `execute`, `task`, direct `backend.*`, or PTC.

**Package pin**: `deepagents==0.7.12`. Key gates: `permissions=` `>=0.5.2`; `interrupt` mode `>=0.6.8`; `delete` + FS allowlist `>=0.7`; exact-match delete `>=0.7.3`; `excluded_tools` blocks execution `>=0.7.9`; typed streaming `version="v3"` since 0.6.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
+---------------------------------------------------------------------------+
|                    Deep Agent Execution Layer (Data Plane)                 |
+---------------------------------------------------------------------------+
|  VIRTUAL FILESYSTEM TOOLS (model-facing surface)                          |
|                                                                           |
|  ls  read_file  write_file  edit_file  delete  glob  grep  execute       |
|  |-- read ops: ls, read_file, glob, grep                                 |
|  |-- write ops: write_file, edit_file, delete                            |
|  |-- execute: SandboxBackendProtocol only                                |
|  |-- task (SubAgentMiddleware, NOT FsToolName)                           |
|  |-- eval (CodeInterpreterMiddleware, beta)                              |
|  |-- MCP/custom on tools= (additive; permissions= DOES NOT APPLY)        |
+---------------------------------------------------------------------------+
|  PERMISSION LAYER (first-match-wins, fail-open, FS-tools-only)            |
|  FilesystemPermission(operations, paths, mode)                            |
|  allow -> deny -> interrupt -> default (allow if no match)                |
+---------------------------------------------------------------------------+
|  BACKEND ROUTER (BackendProtocol dispatch)                                |
|  +----------+----------+----------+----------+----------+                 |
|  | State    | Filesys  | Store    | Context  | Sandbox  |                 |
|  | Backend  | Backend  | Backend  | Hub      | Backend  |                 |
|  | (default)| (disk)   | (cross-  | (Hub     | (E2B/    |                 |
|  |          | virtual  |  thread) |  commits)| Modal/   |                 |
|  |          | mode)    | (Postgr) |          | LangSmth)|                 |
|  +----------+----------+----------+----------+----------+                 |
|  CompositeBackend: longest prefix wins; unmatched -> default (NOT 404)    |
+---------------------------------------------------------------------------+
|  CODE INTERPRETER (QuickJS, in-process, beta)                             |
|  64 MiB heap | 5.0s timeout | 4000 max_result_chars | 256 max_ptc_calls  |
|  PTC: tools.* namespace (camelCase); bypasses interrupt_on                |
|  Modes: thread (default) | turn | call                                   |
+---------------------------------------------------------------------------+
|  STREAMING / OBSERVABILITY                                                |
|  v2: agent.stream(stream_mode, subgraphs=True, version="v2")             |
|  v3: agent.stream_events(version="v3") -> .messages .tool_calls .values  |
|      .subagents .output (Deep Agents adds stream.subagents)               |
|  get_stream_writer() for custom progress events                           |
+---------------------------------------------------------------------------+
|  PERSISTENCE (independent lifetimes -- no XA transaction)                 |
|  Checkpointer | BaseStore | VFS backends | Sandbox guest FS              |
|  (thread_id)  | (Store)   | (State dflt) | (until stop/TTL/delete)       |
|               |           | Filesystem*  | Interpreter snapshot           |
|               |           | Composite    | mode=thread in state           |
|  *FilesystemBackend / LocalShellBackend: local CLI/CI only, not prod      |
|  Internal VFS: /large_tool_results/ /conversation_history/                |
+---------------------------------------------------------------------------+
```

### CompositeBackend Routing

```
  path -> sort routes by prefix length DESC -> first startswith(prefix) wins
               |
               +-- none match -> default   (NO route-miss error -- silent)

  Example:
    /workspace/  -> FilesystemBackend(virtual_mode=True)
    /memories/   -> StoreBackend(ns=user_id)
    /skills/     -> ContextHubBackend()
    (default)    -> StateBackend()

  "/workspace/src/main.py" matches /workspace/ (10 chars)
  "/memory/prefs.md" matches NOTHING -> falls to default StateBackend (SILENT BUG)
  Internal data (/large_tool_results/, /conversation_history/) always hits default
```

**Key routing rules**: `ls`/`glob`/`grep` aggregate children and preserve prefixes. `execute` runs on **default only**. A remote sandbox default will not see Store-backed `/memories/` as a host path. `delete` on a routed child that cannot delete returns an error, not silent hide. Writes to two children are **not** one transaction.

### Request Flow: File Operation

1. Agent model emits `read_file(path="/workspace/src/main.py")`.
2. `FilesystemMiddleware` intercepts via `wrap_tool_call`.
3. Permission layer evaluates path against rules (first-match-wins). `deny` returns error. `interrupt` pauses for human approval. No match means allow.
4. `CompositeBackend` resolves the path to the matching route backend via longest prefix.
5. Backend executes the operation. Returns structured result (content, error, metadata). Never raises -- returns `error` field.
6. For multimodal files (images, PDFs, video), `read_file` returns content blocks the model can process natively (v0.5+; video extra `deepagents[video]`).
7. Results over **20,000 tokens** offload to VFS (path + first **10 lines** preview).

### Request Flow: Sandbox Execution

1. Agent model emits `execute(command="python test_suite.py")`.
2. Middleware checks: does the backend implement `SandboxBackendProtocol`? If not, tool is **capability-hidden** -- agent never sees it. If invoked anyway: `ToolMessage` error string, not a raise.
3. If sandbox backend: command sent to isolated environment (Firecracker microVM, gVisor, or managed sandbox).
4. Sandbox runs command with configured timeout (capped by `max_execute_timeout` = **3600s**). `<=0` is `ValueError`.
5. Structured result returns: stdout/stderr (combined), exit code on `ToolMessage.artifact` (`>=0.7.4`), truncation flag.
6. If output exceeds caps, remainder is auto-saved to a sandbox artifact path.

### Four Execution-Environment Layers (Official)

| Layer | What | Default / Catch |
|-------|------|-----------------|
| **Tools** | 8 `FsToolName`s + `task` + additive MCP/custom + optional `eval`/`write_todos` | Unused schemas still billed every turn |
| **VFS** | `BackendProtocol` instance (`StateBackend()` default). Factories removed in 0.7 | Skills/memory are files on this backend |
| **Filesystem Permissions** | Path glob PDP, fail-open, FS-tools-only | Composite+sandbox: paths must sit on a route or construction `NotImplementedError` |
| **Code Execution** | Remote `BaseSandbox` OR `LocalShellBackend` OR QuickJS `eval` | Production = remote sandbox. Never LocalShell. Interpreter is not pytest/git |

---

## Part 2: Core Mechanics & Algorithms

### Key Invariants

**I1.** Execution is **not** a new scheduler. LangGraph runs the loop; this plane is tools + VFS + optional shell/eval + streams.

**I2.** `permissions=` is first-match-wins, **fail-open**, **FS-tools-only**. `execute`, `task`, MCP, `backend.*`, PTC are outside. Issue #2894 (`ExecutePermission`/`TaskPermission`) was **declined**.

**I3.** `read_file` is **mandatory** on any `FilesystemMiddleware(tools=)` list (`ValueError` if omitted) because offload is useless if the model cannot page the blob back. `excluded_tools` can still drop `read_file` (visibility+execution `>=0.7.9`).

**I4.** Composite unmatched paths fall through to **default** -- silent persistence bug, not HTTP 404.

**I5.** `LocalShellBackend.virtual_mode` is an FS-tool jail, **not** a sandbox. `subprocess.run(shell=True)`. Never in production.

**I6.** Sandbox stops **host** FS/env/process access. It does **not** stop context injection **inside** the guest or network exfil unless the provider blocks egress. **Never put secrets in the sandbox.** Auth proxy **fails closed**.

**I7.** MCP has **no native runtime** in Deep Agents. `permissions=` will not protect MCP. Zero-Trust is a **gateway PEP**.

### FS Tools -- Schemas, Pagination, Search

| # | Tool | Role | Key Details |
|---|------|------|-------------|
| 1 | `ls` | Directory + metadata | Empty returns `"No files found"` since 0.7 (not `[]`) |
| 2 | `read_file` | Paginated read; multimodal | **Required** on allowlist. Default limit 100 lines. `offset`/`limit` for video in seconds |
| 3 | `write_file` | Create **or overwrite** | Overwrites since 0.7 (previously create-only). **Breaking change** |
| 4 | `edit_file` | Exact string replace | Optional global flag |
| 5 | `delete` | File or recursive directory | `>=0.7`. Auto-hidden if backend lacks `delete` |
| 6 | `glob` | Path glob | Brace expansion on disk backends. `truncated` flag on cap |
| 7 | `grep` | Content search | `max_count=1000`. `output_mode`: files_with_matches, content, count |
| 8 | `execute` | Shell in sandbox | Only if `SandboxBackendProtocol`. Timeout capped at 3600s |

Pagination (`>=0.7`): source-line range, `next_offset`, remaining lines. Character budget: `NUM_CHARS_PER_TOKEN = 4`; truncation threshold `4 x token_limit`. `grep content` is the classic blow-up -- serializes match text into the tool message until the 20k evictor fires. Use `files_with_matches` or `count`.

### Two Hide Mechanisms

| Mechanism | Layer | `read_file` required? | Affects user `tools=`? |
|-----------|-------|----------------------|----------------------|
| `HarnessProfile.excluded_tools` | Post-injection name filter. **0.7.9+** also blocks execution | No | **Yes** -- caller and harness tools |
| `FilesystemMiddleware(tools=[...])` | Construction allowlist `>=0.7` | **Yes** -- else `ValueError` | **No** |
| `excluded_middleware={"FilesystemMiddleware"}` | **Rejected** -- `ValueError` | n/a | n/a |

### Six Backend Types

| Backend | Persistence | `execute`? | Key Caveat |
|---------|-------------|-----------|------------|
| **StateBackend** (default) | Thread state + checkpointer | No | Ephemeral with MemorySaver |
| **FilesystemBackend** | Local disk, `root_dir` | No | `virtual_mode=True` default since 0.7. Not for web servers |
| **LocalShellBackend** | Extends Filesystem | Yes -- `subprocess.run(shell=True)` | **Never in production.** `inherit_env=False` is insufficient |
| **StoreBackend** | LangGraph `BaseStore` | No | Without namespace factory, all users share storage |
| **ContextHubBackend** | LangSmith Hub commits | No | UTF-8 only. Optimistic concurrency |
| **CompositeBackend** | Routes to children | Default only | Always use to separate internal data from user workspace |
| **Sandbox (BaseSandbox)** | Isolated container/VM | Yes -- `execute()` primitive | Provider isolation. FS tools built on top of `execute()` |

**BackendProtocol methods**: `ls`, `read`, `grep`, `glob`, `write`, `edit`; optional `delete`. Always return structured results with `error` field. **Never raise**. `SandboxBackendProtocol` adds `execute(command, *, timeout) -> ExecuteResponse`.

**StoreBackend namespace factory** receives `Runtime` (`>=0.5.2`): `rt.context`, `rt.server_info`, `rt.execution_info`. Namespace components: alphanumeric, hyphen, underscore, dot, `@`, `+`, colon, tilde. Wildcards `*`, `?` are **rejected** (glob injection).

### Permissions -- First-Match, Fail-Open, FS-Only

`FilesystemPermission(operations, paths, mode)`:

- Declaration order; **first matching rule wins**
- **No match means allow** (fail-open)
- `mode`: `"allow"` (default) | `"deny"` | `"interrupt"` (`>=0.6.8`; requires checkpointer)
- `operations`: `"read"` (ls, read_file, glob, grep) | `"write"` (write_file, edit_file, delete)
- Subagent `permissions` **replace** the parent (do not merge)

**`delete` semantics**: Directory delete is **all-or-nothing** (checks target and every descendant). Plain-file delete is exact-match first-match-wins (`>=0.7.3`). Since `delete` is a write, an existing "allow write on `/workspace/**`" also authorizes recursive delete -- **0.7 breaking change**.

**Composite + sandbox default**: Every permission path must sit under a known route prefix. Paths that hit the sandbox default (including `/**`) raise `NotImplementedError` at construction.

**Critical scope limitation**: Permissions cover ONLY the 8 built-in filesystem tools. They do NOT cover custom tools, MCP tools, sandbox `execute`, `task`, PTC, or direct `backend.*`.

### Sandbox vs LocalShellBackend vs QuickJS Interpreter

| | Remote Sandbox (`BaseSandbox`) | `LocalShellBackend` | Interpreter (`CodeInterpreterMiddleware`) |
|--|-------------------------------|---------------------|------------------------------------------|
| **Primitive** | `execute` (POSIX shell) | `execute` via `subprocess.run(shell=True)` | `eval` (JS in QuickJS) |
| **FS tools** | Implemented **on top of** `execute()` | Real host FS | **No** FS unless PTC-allowlisted |
| **Network/packages** | Provider policy | Host network, unlimited | **No** by default |
| **Isolation** | Container/VM vs host | **None** for shell | Same-process `quickjs-rs` heap |
| **HITL** | `interrupt_on={"execute": True}` on tool path. PTC `tools.execute` would skip | Same; docs strongly recommend HITL | PTC calls **bypass** `interrupt_on` |
| **When** | Production coding/data analysis | Local CLI / trusted CI **only** | Loops, batching, deterministic transforms, fan-out `task()` from code |

**Sandbox-as-tool** (documented default): agent process stays on your server; API keys stay outside; trade-off = RTT per call. **Agent-in-sandbox**: `deepagents` in the guest; API keys must live in the guest -- docs flag as a security risk.

**Sandbox providers** (swap is a backend instance): LangSmith, Daytona, E2B, Modal, Runloop, Vercel, AgentCore, NVIDIA OpenShell.

**LangSmith sandbox defaults**: **0.5 vCPU**, memory **~2 GiB** (4 GiB per vCPU ratio), burst to 2x CPU. Lifecycle: `running -> (idle_ttl) -> stopped -> (delete_after_stop) -> deleted`. `idle_ttl_seconds` default **600**; prod snippet example **3600**. `delete_after_stop_seconds` typically **14 days**. `create_sandbox` wait default **30s**. `kill_on_disconnect=True` kills a streamed command, not the LangGraph worker.

**Interpreter defaults**: `memory_limit` **64 MiB**; `timeout` **5.0s**; `max_result_chars` **4000**; `max_ptc_calls` **256**; `mode="thread"`. PTC names are camelCase (`web_search` becomes `tools.webSearch`). Snapshot restore does **not** undo PTC side effects. Requires `langchain-quickjs>=0.2.0`, Python `>=3.11`.

**PTC Token Savings**: The biggest cost lever after SummarizationMiddleware. Without PTC, every intermediate tool call grows context (quadratic). With PTC, N tool calls happen in one model turn:

```
Without PTC (3 search calls): 3 model turns, growing context each time
With PTC (same 3 searches):   1 model turn with Promise.all(), flat context
Break-even: N >= 2 tools. At N=5 with 2000-token results: ~60% input savings.
```

### Streaming: v2 vs v3

| Surface | API | Key Difference |
|---------|-----|----------------|
| **v2 (raw)** | `agent.stream(stream_mode, subgraphs=True, version="v2")` | Chunk-level control; namespace tuples identify source |
| **v3 (typed, recommended)** | `agent.stream_events(version="v3")` | Typed projections: `.messages`, `.tool_calls`, `.values`, `.subagents`, `.output` |

Deep Agents adds **`stream.subagents`**: one handle per delegated `task` with `.name`, `.path`, `.status`, `.messages`, `.tool_calls`, nested `.subagents`, `.output`. This is the correct UI surface for humans. `stream.subgraphs` exposes graph-node structure -- use for debugging, not product UX.

`get_stream_writer()` inside tools/nodes for custom progress events. `stream.interleave("messages", "subagents")` for a single read loop. Streaming does **not** reduce billed tokens -- it changes TTFT / time-to-first-tool. Disconnect does not cancel the Agent Server worker; rejoin needs `thread_id`.

---

## Part 3: Token Economics & NFR Analysis

### Context Tax of the Data Plane

| Knob | Default | Effect |
|------|---------|--------|
| Tool-result offload | **20,000** tokens | Path + first **10 lines** preview |
| Human-message eviction | **50,000** tokens | |
| Write/edit input offload | Same 20k | Delayed until 85% of window; results offload immediately |
| `grep_max_count` | **1,000** | Model can override per call via `max_count` |
| `max_execute_timeout` | **3600s** | Cap on `execute` timeout arg |
| `read_file` limit | **100 lines** | Pagination, not a token cap |
| Interpreter `max_result_chars` | **4000** | Truncates `eval` text |
| LocalShell `max_output_bytes` | **100,000** | Truncate host-shell capture |
| LocalShell timeout | **120s** | Default command wall clock |

v0.7 reduced tool-description tokens **4,005 -> 2,302 (-43%)**; default-agent turn **5,395 -> 1,895 (-65%)**.

### Worked Example A: grep dump vs paginated VFS (Inferred)

Assumptions: Sonnet 4.6; 1,000 runs; each run does one large search then 7 follow-up calls (8 total). Output tokens ignored.

| Path | Input tokens | USD/run | USD/1k runs |
|------|-------------|---------|-------------|
| `grep content` dumps 50,000 tokens, retained all 8 calls | 50,000 x 8 x $3/MTok | **$1.200** | **$1,200** |
| Offload at 20k: preview ~200 tokens x 8 + one `read_file` page ~400 tokens once | (200x8 + 400) x $3/MTok | **$0.006** | **$6** |

**Delta: $1,194/1k runs for a single undisciplined dump.** Even if prompt caching held the dump as a prefix: $0.120/run -> $120/1k (still 20x the offload path).

### Worked Example B: Coding Agent Model + Sandbox (Inferred)

Assumptions: Sonnet 4.6; v0.7 prefix 2,000 tokens cached; 8 calls; 4,000 uncached/call; 600 output/call. LangSmith sandbox 0.5 vCPU, 2 GiB; useful work 90s.

**Model cost**: $0.1797/run -> **$180/1k** (cache write + reads + uncached + output).

| Billing Window | CPU $ | Mem $ | Sandbox/run | Sandbox/1k |
|----------------|-------|-------|-------------|------------|
| 90s work only | $0.00072 | $0.00092 | **$0.00164** | **$1.64** |
| 90s + 600s idle TTL | $0.00552 | $0.00707 | **$0.0126** | **$13** |

**Idle TTL is ~8x the execute-time bill if you keep boxes warm for 10 minutes.** E2B default 2 vCPU / 0.5 GiB x 90s: **$2.72/1k** (Pro floor $150/mo not included). Interpreter sandbox line: **$0**.

### Sandbox Cold Start (Third-Party Benchmark, Aug 2026)

| Provider | p50 | p95 | p99 | Success Rate |
|----------|-----|-----|-----|-------------|
| Vercel | **670ms** | 1,040ms | 1,120ms | 100% |
| Modal | **880ms** | 1,000ms | 1,080ms | 100% |
| Runloop | **890ms** | 3,270ms | 3,500ms | -- |
| E2B | **1,610ms** | 1,770ms | 1,810ms | -- |
| Cloudflare | **5,060ms** | 6,040ms | 6,480ms | -- |
| Daytona | **270ms** | 430ms | 440ms | **37%** (not an SLO) |
| LangSmith (inferred) | **5,000ms** | 15,000ms | 30,000ms | p99 = 30s ready-wait fuse |

### Latency SLA Targets (Inferred Policy -- No Vendor SLO Published)

| Path | p50 | p95 | p99 |
|------|-----|-----|-----|
| Streaming TTFT, parent | 640ms | 2,560ms | 5,120ms |
| StateBackend / permission check extra | 5ms | 20ms | 80ms |
| One ReAct cycle (model + StateBackend FS) | 2,000ms | 8,000ms | 20,000ms |
| Warm sandbox `execute` API extra | 100ms | 400ms | 1,500ms |
| First `execute` on cold E2B (cycle + cold) | 3,610ms | 9,770ms | 21,810ms |
| MCP `tools/call` Streamable HTTP | 80ms | 400ms | 2,000ms |
| Interpreter `eval` | 20ms | 200ms | 5,000ms |
| HITL execute clock | 30,000ms | 180,000ms | 600,000ms |

### Three-Tier Sandbox Isolation

```
TIER 3: MicroVMs (Firecracker) -- GOLD STANDARD
  Each workload gets its own kernel on hardware virtualization (KVM).
  Kernel exploit inside one VM cannot reach host or other VMs.
  ~125ms boot, ~5MB memory overhead.
  Powers: AWS Lambda, E2B, Vercel Sandbox.

TIER 2: User-Space Kernels (gVisor) -- MID-TIER
  Intercepts and re-implements syscalls in user space.
  Agent never talks to real kernel. Less overhead than VM.
  Tradeoff: not all syscalls perfectly emulated.
  Used by: Google Agent Sandbox (GKE), Modal.

TIER 1: Containers (Docker/runc) -- WEAKEST
  Shared kernel. Kernel vulnerabilities allow escape.
  Microsoft May 2026 CVE: prompt injection achieved host RCE via container escape.
  Consensus: INSUFFICIENT for untrusted AI agent code.
```

### Availability, RPO & RTO

| Target | Filesystem Ops | Sandbox Ops |
|--------|---------------|-------------|
| **Availability** | 99.9% | 99.5% (cold start variance) |
| **RPO** | Backend-dependent | Session-scoped |
| **RTO** | <30s reconnect/remount | 2-5 min full rebuild |

| Store | Lifetime | Survives restart? | Shared across threads? |
|-------|----------|------------------|----------------------|
| StateBackend | Thread | Only with durable checkpointer | No (subagents share parent VFS) |
| FilesystemBackend | Disk | Yes | Anyone with host path |
| StoreBackend | Store TTL/DB | Yes | Per namespace |
| ContextHubBackend | Hub commits | Yes | Anyone with repo access |
| Sandbox guest FS | Until stop/TTL/delete | Provider snapshot | Thread- vs assistant-scoped |
| Interpreter snapshot | `mode="thread"` in state | With checkpointer | Per thread |

---

## Part 4: Distributed Resilience & Security

### What Sandboxes Protect vs Do Not Protect

**Protects**: Host filesystem isolation, process isolation, resource boundaries.

**Does NOT protect against**:
- **Context injection**: Attacker controlling part of agent input instructs it to run arbitrary commands *inside* the sandbox
- **Network exfiltration**: Unless network is blocked, injected agent can send data out via HTTP/DNS
- **Credential theft**: If credentials are in the sandbox, a context-injected agent can read and exfiltrate them

### Credential Handling -- The Cardinal Rule

**Never put secrets inside a sandbox.**

```
Pattern 1: Tools Outside Sandbox (RECOMMENDED)
  [Agent in Sandbox]  ---tool call--->  [Tool on Host]
  (no credentials)    <---result----    (has API keys, handles auth)

Pattern 2: Auth Proxy with Credential Injection
  [Agent in Sandbox]  ---HTTP req--->   [Auth Proxy]  ---authed req--->
  (no credentials)    <---response---   (injects creds, on host/edge)
```

LangSmith auth proxy: sidecar injects headers on matching egress (workspace secrets, opaque creds, AWS SigV4, GCP SA, callback URL). Callback **fails closed** (non-2xx, transport error, malformed JSON -> reject, do not forward unauthenticated).

### Execution-Layer RBAC

| Role | Tools Available | Path Access | Sandbox |
|------|----------------|-------------|---------|
| **Analyst** | `read_file`, `glob`, `grep` | Read-only `/workspace/**`, `/shared/**` | No |
| **Engineer** | + `write_file`, `edit_file` | Read/write `/workspace/**` | No |
| **Admin** | All tools including `execute` | Full access | Yes, with network restrictions |
| **Auditor** | `grep`, `glob` | Read-only, all paths | No |

Enforce by constructing different `permissions` lists and `HarnessProfile.excluded_tools` sets per role at agent creation time.

### Circuit Breaker -- Must NOT Fail-Open to LocalShell

Independent breakers: **sandbox allocate**, **sandbox execute**, **MCP per server**, **Store put/get**, **parent model**.

| Trip Condition | Closed -> Open | Half-Open Probe | Fallback |
|----------------|---------------|-----------------|----------|
| Sandbox allocate 503 / pool empty | consecutive >= 3 | One `create`/`lookup` | **Queue** the run. **Never** `LocalShellBackend` |
| Sandbox `execute` 5xx / timeout | error-rate + p99 | One `echo ok` | Retry if idempotent; else error. **Never** host subprocess |
| MCP server down / hash drift | transport raise or pin mismatch | One `tools/list` re-hash | **Disable those tools**. Agent continues on VFS |
| Store down | put/get errors | One KV get | Disable `/memories/` writes; keep StateBackend |
| Interpreter heap/timeout | `eval` 5s / snapshot drop | n/a | Return truncated error. Do not PTC-fallback to `execute` |

**Fallback chain**: **remote sandbox -> queue/refuse.** MCP down -> **disable those tools.** Store down -> **StateBackend only.** Interpreter fail -> **no shell consolation prize.** Never: sandbox 503 -> LocalShell. Never: HITL timeout -> auto-approve `execute`. Never: circuit open -> `virtual_mode=False`.

### PII Pipeline -- Detect, Redact, Audit (Files and Tool Args)

Three sinks, three steps:

1. **Detection**: Dual-gate regex (email, PAN, SSN) + optional ML NER. Scan: user input, model output, **tool args** (especially MCP), file contents on read/write, offload candidates, sandbox env, log/trace payloads. If ML down: **fail closed to mask** on chat; **block** on MCP args, `execute` env, VFS writes.

2. **Redaction**: `redact`/`mask`/`hash` to stable tokens (`[EMAIL_<hash12>]`). Strip from VFS and message channel. Do not interceptor-append raw `structuredContent` containing PAN. `0.7.9` disabled tracing inputs on middleware -- reduces accidental PII, but is not DLP.

3. **Audit trail (WORM)**: Log decisions, not values: `content_sha256` pre/post, entity types + counts, action, detector, `correlation_id`, `tenant`, `thread_id`, tool arg digest, execute command digest + exit code.

`PIIMiddleware` is **not** in the default stack and is not FS-aware. It will not scan files the agent `read_file`s unless that text re-enters a checked channel.

---

## Part 5: Production Enterprise Code

```python
"""
Production execution environment: CompositeBackend, E2B sandbox,
permissions, QuickJS interpreter, and file seeding.

pip install deepagents langgraph-checkpoint-postgres e2b-code-interpreter
"""
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.interpreter import InterpreterMiddleware
from deepagents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware
from deepagents.permissions import FilesystemPermission
from deepagents.sandboxes import E2BSandboxBackend
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"
checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()
store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# -- Sandbox backend: Firecracker microVM via E2B, keys from vault
sandbox = E2BSandboxBackend(
    api_key="e2b_key_from_vault",  # never hardcoded
    template="python-3.12",
    idle_ttl_seconds=3600,         # reclaim after 1 hour idle
    timeout=120,                   # 2-minute command timeout
    max_output_bytes=100_000,
)

# -- CompositeBackend: route workspace to sandbox, memories to store
backend = CompositeBackend(
    default=StateBackend(),  # internal data: summaries, tool results
    routes={
        "/workspace/": sandbox,
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (rt.server_info.user.identity, "memories"),
        ),
        "/shared/": FilesystemBackend(root_dir="./shared", virtual_mode=True),
    },
)

# -- Permissions: deny secrets BEFORE allow workspace (first-match-wins)
permissions = [
    FilesystemPermission(  # Block all secret files everywhere
        operations=["read", "write"],
        paths=["/**/.env", "/**/.env.*", "/**/credentials*", "/**/*.key"],
        mode="deny",
    ),
    FilesystemPermission(  # Full access to sandbox workspace
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    FilesystemPermission(  # Read-only shared resources
        operations=["read"], paths=["/shared/**"], mode="allow",
    ),
    FilesystemPermission(
        operations=["write"], paths=["/shared/**"], mode="deny",
    ),
    FilesystemPermission(  # Human approval for memory writes
        operations=["write"], paths=["/memories/**"], mode="interrupt",
    ),
    FilesystemPermission(  # Deny-all catch-all
        operations=["read", "write"], paths=["/**"], mode="deny",
    ),
]

# -- Interpreter: QuickJS with PTC for batched read-only operations
interpreter = InterpreterMiddleware(
    mode="thread",
    memory_limit=64 * 1024 * 1024,  # 64 MiB
    timeout=5.0,
    max_result_chars=4000,
    max_ptc_calls=256,
    ptc_allowlist=["read_file", "grep", "glob"],  # NEVER add execute or delete
)

# -- Assemble the agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[],  # add your custom tools here
    system_prompt="Senior DevOps assistant. Test in sandbox before recommending.",
    middleware=[
        SummarizationMiddleware(trigger=("tokens", 80_000), retention=("messages", 15)),
        ToolCallLimitMiddleware(max_calls=150),
        interpreter,
    ],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["deploy_service"]},
    checkpointer=checkpointer,
    store=store,
)
```

---

## Part 6: System Design Scenarios

### Scenario A: Multi-Tenant Coding Agent

**Problem**: Per-user "fix my repo / run tests" copilot. Untrusted prompt + untrusted repo. Need `pip install`, pytest. Multi-tenant SaaS. Security forbids host shell.

| Axis | Thread-Scoped Remote Sandbox (rec.) | LocalShellBackend on VM | Interpreter-Only |
|------|-------------------------------------|------------------------|-------------------|
| **Cost** | Model $180/1k + sandbox $1.64-$13/1k | $0 sandbox until incident | Sandbox $0; still pay model |
| **Latency** | Cold 270-6,480ms; warm extra 100-1,500ms | ~0ms extra (that is the incident) | `eval` 5s cap; no pytest |
| **Security** | Strong vs host; auth proxy; keys outside | **None.** `virtual_mode` irrelevant to `execute()` | Strong vs OS; weak vs process |
| **Scale** | Provider concurrency (Dev 10; E2B Pro 100/1,100) | One host for all tenants = CVE | 64 MiB heap; 256 PTC |

**Decision**: Remote sandbox wins. LocalShell is local CLI only. Interpreter sits **beside** sandbox for batch review loops. Never share assistant-scoped sandbox across tenants.

### Scenario B: Regulated Healthcare Document Processing

**Problem**: Clinical trial documents with PHI. HIPAA: no data leaves VPC. 10-minute SLA. Complete audit trail.

**Architecture**: Self-hosted LangGraph on EKS in VPC. DLP pre-processing to de-identify before LLM. No external sandbox (data cannot leave VPC) -- `FilesystemBackend(virtual_mode=True)` sufficient since code execution not needed. Semantic firewall (secondary haiku model) checks for PHI leakage. Phase-boundary checkpointing (3 phases).

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deployment | Self-hosted EKS | PHI cannot leave VPC |
| PHI handling | DLP pre-processing | Defense-in-depth even with self-hosted |
| Sandbox | None (FilesystemBackend) | No external providers allowed |
| Validation | Semantic firewall | Catches hallucinated citations, PHI leakage |

---

## Common Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| Host RCE | `LocalShellBackend` in production | Never on shared hosts; `BaseSandbox` + auth proxy |
| Composite route miss | `/memory/` vs `/memories/`; silent default | Trailing-slash routes; test prefix typos |
| `execute` cannot see Store files | Shell on default only; no mapping for remote sandbox | Use file tools, not shell, against virtual prefixes |
| `delete` surprises | write-allow authorizes recursive delete since 0.7 | Narrower deny/interrupt; omit `delete` from allowlist |
| Fail-open FS leak | No matching permission rule means allow | Deny secrets before allow workspace; deny-all catch-all |
| MCP token passthrough | Docs `headers` Bearer example is static | OAuth 2.1 + RFC 8707 audience; never copy user tokens |
| Stream / HITL key lost | No `thread_id` + checkpointer | Always `config={"configurable": {"thread_id": ...}}` |
| `read_file` omitted from allowlist | `ValueError` at construction | Keep `read_file` always |
| Grep context overflow | 20k evictor / summarization excluded | Keep compression; `files_with_matches`; `grep_max_count` |
| `write_file` clobber | Overwrite since 0.7 (no file-exists error) | `edit_file` + permissions/HITL |
| PTC interrupt bypass | PTC tools skip `interrupt_on` | Never PTC `execute`, `delete`, or privileged tools |
| Interpreter snapshot vs PTC | Restore JS vars, not world | Idempotent tools; `mode="call"` if needed |
| Empty ls/glob JSON parse | `"No files found"` since 0.7, not `[]` | Do not `json.loads` the tool string |

---

## Interview Q&A

**Q1: What is the Deep Agents execution environment?**
I treat it as the data plane -- where the agent acts. Four layers: tools, virtual filesystem, filesystem permissions, code execution. Typed streams to observe. LangGraph still runs the ReAct loop. `create_deep_agent` binds `backend=`, `permissions=`, additive `tools=`, and `FilesystemMiddleware`. Eight built-in FS tools; `task` is subagent middleware; MCP is adapters. Permissions are fail-open and FS-only. I never ship `LocalShellBackend`.

**Q2: Walk a tool call from model to stream event.**
Model emits `tool_calls`. Name filter (`excluded_tools` also blocks execution since 0.7.9) and capability hide (`execute`/`delete` if backend cannot). Built-in FS tools hit the path PDP -- first match wins, no match allows -- then the backend. MCP and `execute` skip that PDP: MCP needs a gateway PEP; `execute` goes to `SandboxBackendProtocol`. Composite longest-prefix routes; miss falls to default. Result is an untrusted `ToolMessage`; large blobs offload at 20k; v3 `stream.tool_calls.output_deltas` and `stream.subagents` for `task`.

**Q3: `excluded_tools` vs `FilesystemMiddleware(tools=)`.**
Allowlist is construction: only listed FS names exist, and `read_file` is required or I get `ValueError`. `excluded_tools` is a post-injection name filter that can also drop caller tools; since 0.7.9 it blocks execution too. Capability filter still hides `execute` on StateBackend even if I list it. Declarative subagents do not inherit my allowlist.

**Q4: Give me $ per 1k for this plane.**
Inferred, not a SKU. Undisciplined `grep content` 50k tokens retained across 8 Sonnet 4.6 calls: **$1,200/1k** vs offload+page **$6/1k**. Coding agent model: **$180/1k** (8 calls, 2k cached prefix). LangSmith sandbox 0.5 vCPU 2 GiB 90s: **$1.64/1k**, or **$13/1k** with 600s idle TTL -- idle is ~8x the work bill. E2B 2 vCPU 0.5 GiB 90s: **$2.72/1k**. Interpreter line is $0.

**Q5: Sandbox cold start latency?**
Deep Agents publishes none. Third-party snapshot in ms: Vercel **670/1,040/1,120**, Modal **880/1,000/1,080**, E2B **1,610/1,770/1,810**, Cloudflare **5,060/6,040/6,480**. Daytona **270/430/440** at 37% success -- not an SLO. LangSmith actuals unpublished; I budget **5,000/15,000/30,000** (p99 = 30s ready-wait fuse). First execute on cold E2B: **3,610/9,770/21,810** (cycle + cold start, in series). I do not put first-execute on a 5s gateway timeout.

**Q6: Are permissions Zero Trust for MCP and shell?**
No. Fail-open path PDP for built-in FS tools only. `execute`, `task`, MCP, PTC, and `backend.*` are uncovered. Issue #2894 declined `ExecutePermission`. Composite+sandbox: I cannot pretend `/**` constrains the shell -- construction raises. Zero-Trust MCP is a gateway PEP: server allowlist, tool allowlist, hash-pin name+description+schema, OAuth 2.1, RFC 8707 audience.

**Q7: PII pipeline on this plane.**
`PIIMiddleware` is not FS-aware and misses `read_file` bytes until they re-enter a checked channel. I scan user I/O, VFS writes, MCP args, execute env. Detect with regex plus optional ML. If ML is down I still mask chat and I block PAN into MCP args, VFS writes, execute. Redact to stable hashes. Audit WORM of pre/post sha256, entity types, action, detector, cid, thread, arg digest. Offloaded `/large_tool_results/` can hold PII in the checkpointer.

**Q8: Circuit breaker when sandbox is 503.**
Library does not ship breakers. Closed -> open -> half-open with one probe. Sandbox 503 or execute 5xx: queue or refuse. MCP transport/hash drift: disable those tools. Never fail-open to `LocalShellBackend`, never unsandboxed execute, never HITL-timeout auto-approve. Independent breakers so a sandbox outage does not take down an MCP-only support bot.

**Q9: CompositeBackend routing?**
Longest prefix wins; no match uses default with no error. `ls`/`glob`/`grep` aggregate. `execute` is default-only; remote sandbox will not see Store-backed paths. `/memory/foo` is not `/memories/`. Writes to two children are not one transaction. v0.7 stopped false-empty listings when the default errors.

**Q10: Sandbox vs LocalShell vs interpreter -- pick for a coding copilot.**
Remote thread-scoped sandbox. LocalShell is unrestricted `subprocess.run(shell=True)` -- local CLI only. Interpreter is QuickJS `eval`, 64 MiB / 5s / 4000 chars, no pip/git, PTC bypasses HITL. I may run interpreter beside the sandbox for batch loops with PTC on read-only tools. Secrets stay in auth proxy that fails closed; never env-in-box.

---

## Key Numbers to Memorize

| Number | What |
|--------|------|
| **8** | Built-in `FsToolName`s (ls, read_file, write_file, edit_file, delete, glob, grep, execute) |
| **`read_file` required** | `FilesystemMiddleware(tools=)` else `ValueError` |
| **>=0.7.9** | `excluded_tools` blocks execution; tracing inputs off |
| **>=0.7.3** | Exact-match delete first-match-wins |
| **20,000 / 10 lines** | Tool-result offload threshold / preview |
| **1,000** | `grep_max_count` |
| **100 lines** | `read_file` default limit |
| **3600s** | `max_execute_timeout` |
| **64 MiB / 5.0s / 4000 / 256** | Interpreter heap / eval timeout / max_result_chars / max_ptc_calls |
| **120s / 100,000** | LocalShell timeout / max_output_bytes |
| **fail-open** | `permissions=` when no rule matches |
| **$1,200 vs $6 / 1k** | grep dump 50k tokens vs offload + page |
| **$180 / 1k** | Coding agent model (8 calls, 2k prefix) |
| **$1.64 vs $13 / 1k** | 90s sandbox vs 90s + 600s idle TTL |
| **670 / 1,610 / 5,060 ms** | Vercel / E2B / Cloudflare cold start p50 |
| **30s** | LangSmith sandbox create_sandbox ready-wait default |

## Quick Reference

| When to use | Choose |
|-------------|--------|
| OS-level code execution, tests, packages | Remote sandbox backend |
| In-memory loops, batched tool calls, transforms | QuickJS interpreter + PTC |
| Local development only | LocalShellBackend (never production) |
| Thread-scoped scratch files | StateBackend (default) |
| Cross-thread durable storage | StoreBackend with namespace factory |
| Mixed persistence needs | CompositeBackend (always in production) |
| Real project files on disk | FilesystemBackend inside CompositeBackend |
