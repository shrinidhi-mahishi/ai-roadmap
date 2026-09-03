# Deep Agents Execution Environment

**Prep target**: Director/VP AI roles
**Framework**: LangChain Deep Agents >= 0.7.x (released March 2026)
**Package pin**: `deepagents==0.7.12` (PyPI 2026-09-01)

---

## What Is This?

The execution environment is where a Deep Agent actually does work -- not the brain (middleware stack, planning, memory from Module 08), but the *hands and feet*: where the agent reads files, writes code, runs commands, and stores results.

Think of it like renting office space for your employee. The "virtual filesystem" is their desk -- drawers they can organize however they want. The "backends" determine whether that desk is a cardboard box that disappears when they go home (in-memory), a real filing cabinet (local disk), or a cloud drive shared across offices (PostgreSQL store). The "sandbox" is a sealed clean room: the agent can run experiments there without risking the main building's plumbing.

The key design insight is **separation of concerns**: the agent's file operations (read, write, edit, grep) are identical regardless of where files physically live. You swap backends -- in-memory for tests, local disk for CLI tools, cloud store for multi-tenant SaaS -- without changing a single line of agent code.

The Deep Agents docs present the environment as a layered system, not a single sandbox toggle. Official overview: four layers -- **tools**, **virtual filesystem**, **filesystem permissions**, **code execution** -- plus typed streams as the observation plane.

Think shop floor, not floor plan. Module 08 is how the factory is wired. This module is the machines: eight `FsToolName`s (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute`), MCP `tools/call` riding additive `tools=`, backends that persist bytes, a remote sandbox whose primitive is `execute()`, and `stream.subagents` so a UI can watch a `task` without parsing Pregel namespaces.

**Permissions are fail-open and FS-tools-only.** First matching glob wins; **no match -> allow**. They do **not** cover MCP, custom tools, `execute`, `task`, direct `backend.*`, or a sandbox shell that can `cat` any guest path. **Never `LocalShellBackend` in production** -- `subprocess.run(shell=True)`; `virtual_mode` jails FS tools, not the shell.

## Why It Matters

Execution environment decisions are where Director/VP candidates win or lose system design interviews. The wrong backend choice leaks customer data across tenants. The wrong sandbox tier lets a prompt injection achieve host-level remote code execution. The wrong credential handling pattern puts API keys inside a container that an attacker can exfiltrate. These are not theoretical risks -- Microsoft disclosed CVEs in May 2026 showing exactly this attack chain.

Interviews fork on whether you can name the four layers, refuse host shell, and put a **gateway PEP in front of MCP** because `permissions=` will not. Trap answers: "path globs constrain `execute`," "Composite unmatched paths 404," "sandbox is a secret vault," "streaming saves tokens," "`excluded_tools` before 0.7.9 is a control."

The cost story on this plane is **grep dumps and idle sandbox TTL**, not the assembler prefix.

---

## Architecture / System Design

### Four-Layer Mental Model

Use a four-layer model:

- **Tools**: custom functions, APIs, databases, and built-ins
- **Virtual filesystem**: the model-facing file surface
- **Filesystem permissions**: declarative rules over built-in file tools
- **Code execution**: shell execution or in-process interpretation

Then map those layers onto the backend you choose:

- `StateBackend` for thread-scoped scratch space
- `FilesystemBackend` for local disk
- `StoreBackend` for cross-thread durable storage
- `ContextHubBackend` for LangSmith Context Hub repos
- `CompositeBackend` for path-based routing
- `LocalShellBackend` for host shell execution (dev only)
- Sandbox backends for isolated shell execution

### System Topology (Full Data Flow)

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  LangSmith traces (FS tools + shell when tracing on)                  |
         |  ExecuteArtifact / exit code on ToolMessage.artifact (>=0.7.4)        |
         |  stream.subagents | stream.tool_calls.output_deltas | lifecycle       |
         |  Auth-proxy egress (policy-visible; NOT in the LLM transcript)        |
         |  0.7.9: tracing inputs disabled on middleware                         |
         |  WORM you build: (cid, thread_id, tool, arg_digest, exit, perm)       |
         +----------^---------------------^------------------^------------------+
                    | spans               | stream events     | audit
+-------------------+---------------------+-------------------+-----------------+
| CONTROL PLANE  (LLM-free assembly; FS PDP before backend; MCP PEP is YOURS)   |
|  create_deep_agent binds: backend=  permissions=  tools= (additive)            |
|  FilesystemMiddleware(tools=) allowlist   HarnessProfile.excluded_tools        |
|  MCP MultiServerMCPClient connections + interceptors + gateway (not DA)        |
|  interrupt_on / permission mode=interrupt  (needs checkpointer)                |
|  Graph factory: thread_id from config["configurable"] -- not full Runtime      |
+--------------------------------+----------------------------------------------+
                                 | CompiledStateGraph (LangGraph ReAct)
                                 v
+-----------------------------------------------------------------------+
| DATA PLANE  (untrusted token stream -- model proposes, PEPs dispose)   |
|                                                                        |
|  model -> tool_calls -> PEP -> backend/sandbox/MCP -> ToolMessage      |
|                                                                        |
|  +-------------- TOOL PROXIES ----------------------------------------+|
|  | FS (FsToolName): ls read_file write_file edit_file delete glob grep ||
|  |                  execute (SandboxBackendProtocol only)              ||
|  | task (SubAgentMiddleware)   eval (CodeInterpreterMiddleware, beta)  ||
|  | MCP/custom on tools=  -- permissions= DOES NOT APPLY               ||
|  | read="ls,read_file,glob,grep"  write="write_file,edit_file,delete" ||
|  | execute + task OUTSIDE _DEFAULT_FS_TOOL_OPS  (#2894 declined)      ||
|  +--------------------------------------------------------------------+|
+-------+------------------+------------------+---------+---------------+
        |                  |                  |         |
        v                  v                  v         v
+-----------------------------------------------------------------------+
| PERSISTENCE LAYER  (independent lifetimes -- no XA)                    |
|  +------------+ +------------+ +--------------+ +-------------------+ |
|  |Checkpointer| | BaseStore  | | VFS backends | | Sandbox guest FS  | |
|  |thread_id   | | StoreBack- | | State (dflt) | | until stop/TTL    | |
|  |StateBackend| | end ns=    | | Filesystem*  | | Interpreter snap  | |
|  |DeltaChannel| | ContextHub | | Composite    | | mode=thread state | |
|  +------------+ +------------+ +--------------+ +-------------------+ |
|  *FilesystemBackend / LocalShellBackend: local CLI/CI only            |
|  Internal VFS: /large_tool_results/  /conversation_history/           |
+-----------------------------------------------------------------------+
```

### Deep Agent Execution Layer (Detailed View)

```
+---------------------------------------------------------------------+
|                    Deep Agent Execution Layer                         |
+---------------------------------------------------------------------+
|                                                                      |
|  +-------------- VIRTUAL FILESYSTEM TOOLS --------------------------+|
|  |                                                                  ||
|  |  +--------+ +----------+ +----------+ +--------+ +--------+     ||
|  |  |   ls   | |read_file | |write_file| |edit_file| | delete |     ||
|  |  +----+---+ +----+-----+ +----+-----+ +----+---+ +---+----+     ||
|  |       |          |            |             |         |          ||
|  |  +--------+ +--------+  +---------+                              ||
|  |  |  glob  | |  grep  |  | execute | (sandbox only)               ||
|  |  +----+---+ +----+---+  +----+----+                              ||
|  |       |          |           |                                    ||
|  +-------+----------+-----------+-----------------------------------+|
|          |          |           |                                     |
|          v          v           v                                     |
|  +--------------------------------------------------------------+   |
|  |              PERMISSION LAYER (first-match-wins)              |   |
|  |    allow -> deny -> interrupt -> default (permissive)         |   |
|  +---------------------------+----------------------------------+   |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------------------+   |
|  |                    BACKEND ROUTER                             |   |
|  |              (BackendProtocol dispatch)                       |   |
|  +----------+----------+----------+----------+------------------+   |
|  |  State   |Filesystem|   Store  | Context  |  Sandbox         |   |
|  | Backend  |  Backend |  Backend |Hub Bkend |  Backend         |   |
|  |(default) |(local dsk|(cross-th)|(LangSmth)|(E2B/Modal/       |   |
|  |          |vrtml_mode|(Postgres)|(Hub cmts)| LangSmith)        |   |
|  +----+-----+-----+----+-----+----+-----+----+------+----------+   |
|       |           |          |          |           |               |
|       v           v          v          v           v               |
|  +---------+ +---------+ +---------+ +--------+ +--------------+   |
|  |Checkpt  | |  Disk   | |Postgres | |Context | | Firecracker/ |   |
|  | State   | |  I/O    | |  Store  | |  Hub   | | gVisor VM    |   |
|  +---------+ +---------+ +---------+ +--------+ +--------------+   |
|                                                                      |
|  +-------------- CODE INTERPRETER ----------------------------------+|
|  |  QuickJS (embedded, capability-scoped)                           ||
|  |  PTC: tools.* namespace for batched tool calls                   ||
|  |  Modes: thread | turn | call                                     ||
|  |  Limits: 64MB heap, 5s timeout, 4000 char result, 256 PTC       ||
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-------------- MCP INTEGRATION -----------------------------------+|
|  |  MultiServerMCPClient (stdio, HTTP, OAuth)                       ||
|  |  Stateful sessions, tool filtering, auto-discovery               ||
|  +------------------------------------------------------------------+|
|                                                                      |
|  +-------------- TELEMETRY PLANE -----------------------------------+|
|  |  LangSmith traces      |  Token/cost metrics per trace           ||
|  |  Execution logs         |  Tool call audit trail (who/what/      ||
|  |  (stdout/stderr/exit)   |  when/path/operation/result)           ||
|  +------------------------------------------------------------------+|
+----------------------------------------------------------------------+
```

### CompositeBackend Routing

```
+-------------------------------------------------------------+
|                   CompositeBackend                            |
|                                                              |
|  Incoming path: "/workspace/src/main.py"                     |
|                                                              |
|  Route table (longest-prefix match):                         |
|    /workspace/  --> FilesystemBackend(virtual_mode=True)      |
|    /memories/   --> StoreBackend(ns=user_id)                 |
|    /skills/     --> ContextHubBackend()                       |
|    (default)    --> StateBackend()                            |
|                                                              |
|  Match: /workspace/ (14 chars) wins                          |
|  Dispatch: FilesystemBackend.read("src/main.py")             |
|                                                              |
|  Internal data (/large_tool_results/,                        |
|  /conversation_history/) hits default StateBackend           |
|  -- never mixes with user data                               |
+--------------------------------------------------------------+
```

### Middleware Stack (Main Agent)

1. `SkillsMiddleware` when `skills=` is configured
2. `FilesystemMiddleware`
3. `SubAgentMiddleware` when synchronous subagents exist
4. `SummarizationMiddleware`
5. `PatchToolCallsMiddleware`
6. `AsyncSubAgentMiddleware` when async subagents are configured
7. user-supplied `middleware=`
8. harness profile extras
9. excluded-tool filtering
10. `AnthropicPromptCachingMiddleware` and `BedrockPromptCachingMiddleware`
11. `MemoryMiddleware`

When `interrupt_on` is configured, Deep Agents also adds `HumanInTheLoopMiddleware`.

### Request Flow: File Operation

1. Agent model emits a tool call: `read_file(path="/workspace/src/main.py")`.
2. `FilesystemMiddleware` intercepts via `wrap_tool_call`.
3. Permission layer evaluates path against rules (first-match-wins). If `deny`, returns error immediately. If `interrupt`, pauses for human approval.
4. If allowed, `CompositeBackend` resolves the path to the matching route backend.
5. Backend executes the operation and returns a structured result (content, error, metadata).
6. For multimodal files (images, PDFs), `read_file` returns content blocks the model can process natively.
7. Result flows back through middleware hooks to the model.

### Request Flow: Sandbox Execution

1. Agent model emits `execute(command="python test_suite.py")`.
2. Middleware checks: does the backend implement `SandboxBackendProtocol`? If not, tool is hidden -- agent never sees it.
3. If sandbox backend: command is sent to isolated environment (Firecracker microVM, gVisor, or managed sandbox).
4. Sandbox runs command with configured timeout (default 120s) and output cap (default 100,000 bytes).
5. Structured result returns: `{stdout, stderr, exit_code, truncated}`.
6. If output exceeds cap, remainder is auto-saved to a file in the backend; agent is instructed to use `read_file` for incremental access.

### Request Flow Narrative (Detailed)

1. **Model proposes.** Coordinator (or a `task` child) emits `tool_calls`. Observation plane: `stream.tool_calls` (v3) or v2 `updates`/`messages`. Subagent work is `stream.subagents`, not `stream.subgraphs` (Pregel nodes).
2. **Name filter.** `>=0.7.9` `_ToolExclusionMiddleware` drops **and** blocks execution of `excluded_tools`. Capability filter still hides `execute`/`delete` when the backend cannot support them (listing them on a non-capable backend is a **no-op**, not an error). If `execute` is somehow still invoked on a non-executable resolved backend: `ToolMessage` error string, not a raise.
3. **PEP -- two doors, not one.**
   - **Built-in FS tools** (`ls`/`read_file`/`write_file`/`edit_file`/`delete`/`glob`/`grep`): `FilesystemPermission` first-match-wins, **no match -> allow**. Then the backend.
   - **MCP / custom / `execute` / `task` / PTC:** `permissions=` **does not run**. MCP must hit a **gateway PEP** (allowlist, hash-pin, audience-bound token, interceptor). `execute` hits `SandboxBackendProtocol.execute` (or LocalShell -- prod forbid). `interrupt_on={"execute": True}` is a review queue on the **tool path**; PTC-invoked tools **skip** it.
4. **Backend / sandbox / MCP dispose.**
   - Composite: **longest prefix wins**; unmatched path (including a typo `/memory/` vs `/memories/`) hits **default** -- silent, not a 404. `execute` runs on **default only**.
   - Sandbox-as-tool (documented default): each FS/`execute` call is a remote API; POSIX scripts (`awk`/`grep`/`find`/`stat`) implement FS tools via `execute()`. App `upload_files` / `download_files` are a **second** plane (seed/harvest), not the agent tools.
   - MCP default: **new `ClientSession` per `tools/call`** unless `client.session()`. Transport/session failures **raise**; `handle_tool_errors=True` only maps `isError=True` -> `ToolMessage(status="error")`.
5. **Observe / persist.** Combined stdout/stderr (truncated; large execute may land in a sandbox artifact path). Exit code on `ToolMessage.artifact` `>=0.7.4`. Results over **20,000** tokens offload to VFS (path + first **10** lines). Stream: `output_deltas` for incremental tool stdout.
6. **Stop.** Model stops calling tools, HITL interrupt (checkpointer required), sandbox `timeout` / `max_execute_timeout` (default **3600 s**), interpreter **5.0 s** `eval` cap, or provider teardown (`idle_ttl`).

### Planes (Do Not Couple)

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `backend`, `permissions`, FS allowlist, `excluded_tools`, MCP connection map, interceptor PDP, sandbox factory, checkpointer/store handles | Yes for assembly. FS allow/deny runs in middleware **before** a built-in FS tool hits the backend | Putting execute/MCP authz in the prompt; treating `permissions=` as covering MCP |
| **Data** | User messages, FS bytes, `execute` stdout/stderr, MCP `tools/call` results, interpreter `eval` output, stream projections | No -- untrusted token stream | Letting the model pick `user_id`, store namespace, or sandbox name |

---

## Core Concepts & Algorithms

### Virtual Filesystem

Deep Agents treats files as a first-class agent primitive. Instead of forcing everything through prompt text, the harness gives the model a file namespace it can search, read, write, and reuse across turns. Memory, skills, context offloading, and code execution all build on the same file surface.

The model-facing filesystem is abstracted from storage. The agent sees paths; the backend decides where those paths live.

```
model
  -> built-in file tools
     -> ls
     -> read_file
     -> write_file
     -> edit_file
     -> delete
     -> glob
     -> grep
  -> FilesystemMiddleware
  -> backend path resolution
  -> file content or artifact
  -> other Deep Agents features reuse the same namespace
     -> /skills/
     -> /memories/
     -> /large_tool_results/
     -> /conversation_history/
```

The VFS is not only for "user files." Deep Agents uses it internally for offloaded tool output and preserved conversation history.

**FS Tool Schemas & Details:**

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
| -- | `task` | Spawn subagent | Harness GP; **not** `FsToolName` |

**Agent-facing parameters:**

| Tool | Notable args | Defaults / caps |
| --- | --- | --- |
| `read_file` | `file_path`, `offset`, `limit` | Tool: `offset=0`, **`limit=100` lines** (`DEFAULT_READ_LIMIT`). Protocol `read(..., limit=2000)` is **direct** backend. Video extra: `offset`/`limit` in **seconds** |
| `grep` | `pattern`, `path`, `glob`, `output_mode`, `max_count` | `output_mode` in `{files_with_matches, content, count}`; middleware `grep_max_count=1000` |
| `glob` | `pattern`, `path` | `truncated` when cap/deadline hit |
| `execute` | `command`, `timeout` | Rejected if `> max_execute_timeout` (**3600 s**) or negative; `0` may mean no timeout on supporting backends |
| `delete` | `file_path` | Recursive; all-or-nothing vs descendants |

Empty `ls`/`glob` tool strings are **`No files found`** (not `[]`) since 0.7 -- `json.loads` parsers break. Negative offsets clamp to 0.

**Pagination (`>=0.7`):** source-line range, `next_offset`, remaining lines when length is known. Middleware truncation **adjusts** `next_offset` so resume does not skip unseen lines. Character budget: `NUM_CHARS_PER_TOKEN = 4`; truncation threshold `4 * token_limit`.

**Multimodal `read_file`** (v0.5+ / v0.7 video extra): image (png/jpeg/gif/webp/heic/heif), video, audio, pdf/ppt. Video extra `deepagents[video]`: `offset`/`limit` in **seconds** -> JPEG frames. `0.7.2` scrubs blocks the model profile does not support.

### Two Hide Mechanisms (Allowlists)

| Mechanism | Layer | What it does | `read_file` required? | Affects user `tools=`? |
| --- | --- | --- | --- | --- |
| `HarnessProfile.excluded_tools` | Post-injection name filter | Drops from **model-visible** list. **0.7.9+** also **blocks execution** | No | **Yes** -- caller tools **and** harness tools |
| `FilesystemMiddleware(tools=[...])` | Construction allowlist `>=0.7` | Only listed `FsToolName`s registered | **Yes** -- else `ValueError` | **No** |
| `excluded_middleware={"FilesystemMiddleware"}` | Rejected | Offload/permissions/skills/memory need the VFS | n/a | n/a |

Passing your own `FilesystemMiddleware` **replaces** the default for the **main** agent; the general-purpose subagent inherits it. Declarative `subagents=` do **not** inherit -- put a `FilesystemMiddleware(tools=...)` on that spec.

If you want only a subset of file tools, pass your own `FilesystemMiddleware(tools=[...])`. The docs call out an important invariant: `read_file` must always be included or agent creation raises `ValueError`.

### Pluggable Backends

Pass a `BackendProtocol` **instance** (factories removed in 0.7). Default: `StateBackend()`.

| Backend | Storage | Persistence | Isolation | `execute`? | Key Caveat |
|---------|---------|-------------|-----------|------------|------------|
| **StateBackend** (default) | Graph state, keyed by `thread_id` | Thread-scoped via checkpointer | Per-thread | No | Ephemeral with MemorySaver. Subagents **share** the parent thread VFS |
| **FilesystemBackend** | Local disk under **absolute** `root_dir` | Permanent | Path jail for **FS tools only** when `virtual_mode=True` (default since 0.7). `False` "provides no security even with `root_dir` set" | No | Docs: **not** for web servers. Writes internal artifacts into `root_dir` |
| **LocalShellBackend** | Extends FilesystemBackend + `execute` | Permanent | **None for shell** -- `subprocess.run(shell=True)` | Yes | Trusted local/CI **only**. HITL mandatory |
| **StoreBackend** | LangGraph `BaseStore` (Postgres prod); `namespace` factory required | Cross-thread per namespace | Per namespace tuple | No | Without namespace factory, all users of one assistant share storage |
| **ContextHubBackend** | LangSmith Hub (version-controlled) | Hub commits | Repo ACL + API key. UTF-8 only on `upload_files()` | No | Optimistic concurrency; stale `parent_commit` -> fail; re-pull and retry |
| **Sandbox (`BaseSandbox`)** | Isolated container/VM FS | Until stop/delete/TTL | Provider isolation | Yes -- `execute()` is the primitive | Production code exec |
| **CompositeBackend** | `default` + `routes={prefix: backend}` | Mixed per child | Per child. `supports_execution` = default is `SandboxBackendProtocol` | Default only | `/memories/` durable + rest ephemeral |

**Backend Protocol:**

```
BackendProtocol
  ls(path) -> LsResult
  read(file_path, offset, limit) -> ReadResult
  write(file_path, content) -> WriteResult
  edit(file_path, old_string, new_string, replace_all) -> EditResult
  glob(pattern, path?) -> GlobResult
  grep(pattern, path?, glob?) -> GrepResult
  delete(file_path) -> DeleteResult     # optional; tool hidden if unsupported

SandboxBackendProtocol extends BackendProtocol
  execute(command, timeout?) -> ExecuteResult
```

**Error handling convention**: Return structured results with an `error` field. Do not raise exceptions. This lets the agent reason about errors and retry or adapt.

**Namespace Isolation in StoreBackend:**

```python
# Per-user isolation (most common for multi-tenant):
namespace = lambda rt: (rt.server_info.user.identity,)

# Per-thread isolation (conversation-scoped):
namespace = lambda rt: (rt.execution_info.thread_id,)

# Combined (user + thread + domain):
namespace = lambda rt: (
    rt.server_info.user.identity,
    rt.execution_info.thread_id,
    "filesystem",
)
```

Namespace validation: alphanumeric, hyphens, underscores, dots, `@`, `+`, colons, tildes. Wildcards `*` `?` **rejected** (glob injection).

### Composite Routing (Longest Prefix)

```
  path --> sort routes by prefix length DESC --> first startswith(prefix) wins
                |
                +-- none match --> default   (NO route-miss error)
```

- Longest prefix wins (`"/memories/projects/"` overrides `"/memories/"`).
- Unmatched paths hit **default** -- silent, not HTTP 404.
- `ls` / `glob` / `grep` aggregate and preserve original prefixes.
- 0.7: `ls("/")` / root `glob` propagate **default-backend failures** instead of returning route-only successes.
- `delete` on a routed sub-backend that cannot delete -> **unsupported-operation error** (not silent hide).
- `execute` on Composite: shell on **default only**. Virtual prefixes (`/memories/`) may not exist in the guest.

Internal artifacts under `/large_tool_results/` and `/conversation_history/` follow the **default** backend. Bare `FilesystemBackend` writes those onto real disk under `root_dir`. Pattern: `CompositeBackend(default=StateBackend(), routes={"/workspace/": FilesystemBackend(..., virtual_mode=True)})`.

**Consistency:** a Store write under `/memories/` and a checkpointed StateBackend write are **not** one transaction. Crash between them -> split brain.

### Filesystem Permissions -- First-Match, Fail-Open, FS-Only

`FilesystemPermission(operations, paths, mode)`:

- Declaration order; **first matching rule wins**.
- **No match -> allow.**
- `operations` accepts `"read"` and/or `"write"`.
  - `"read"` covers `ls`, `read_file`, `glob`, and `grep`
  - `"write"` covers `write_file`, `edit_file`, and `delete`
- `paths` is a list of glob patterns such as `"/workspace/**"` or `"/workspace/.env"`.
- `mode` can be: `"allow"` | `"deny"` | `"interrupt"` (`>=0.6.8`; requires checkpointer; auto-wires HITL).
- Globs: `**`, `{a,b}` alternation.
- Subagent `permissions` **replace** the parent (do not merge).

```
built-in filesystem tool call
  -> FilesystemMiddleware
  -> map tool to operation
     -> read: ls, read_file, glob, grep
     -> write: write_file, edit_file, delete
  -> evaluate FilesystemPermission rules in order
  -> first match wins
     -> allow: run tool
     -> deny: block tool
     -> interrupt: pause for human review
  -> return result or rejection
```

**`delete` semantics:** directory delete is **all-or-nothing** (`write` on target **and every descendant**). Plain-file delete is exact-match first-match-wins (`>=0.7.3`). Because `delete` is **write**, an existing "allow write on `/workspace/**`" also authorizes recursive delete unless a narrower deny/interrupt exists -- **0.7 breaking change**.

**Interrupt glob caveat:** bulk tools (`ls`, `glob`, `grep`, directory `delete`) fire when the search subtree **could overlap** an anchored prefix. Fully unanchored `/**/secrets` **over-fires**. Anchor: `/secrets/**`.

**Composite + sandbox default:** every permission path **must** sit under a known **route** prefix. Paths that hit the sandbox default (including `/**`) raise `NotImplementedError` at construction -- path rules cannot constrain `execute`.

**Critical scope limitation**: Permissions only cover the 8 built-in filesystem tools. They do NOT cover:
- Custom tools (your own functions)
- MCP tools (external servers)
- Sandbox `execute` (arbitrary shell commands bypass file permissions entirely)
- `task`, PTC, or `backend.*`

### Code Execution: Sandbox vs LocalShell vs QuickJS Interpreter

Deep Agents supports code execution in two fundamentally different ways:

- **Shell execution** through `execute` on sandbox-like backends
- **In-process JavaScript execution** through `CodeInterpreterMiddleware`

Those solve different problems:
- Sandbox backends are for OS-level work: install deps, run tests, call CLIs, manipulate files
- Interpreters are for control-flow work: loops, filtering, batching, deterministic transforms, and programmatic tool calling

The interview trap is assuming the interpreter is a sandbox. It is not.

```
agent needs code execution
  -> choose shell path or interpreter path

shell path:
  model -> execute
        -> sandbox backend or LocalShellBackend
        -> command runs in OS environment

interpreter path:
  model -> eval
        -> CodeInterpreterMiddleware
        -> QuickJS runtime
        -> optional bridges
           -> tools.* via PTC
           -> task() via dynamic subagents
```

| | Remote sandbox (`BaseSandbox`) | `LocalShellBackend` | Interpreter (`CodeInterpreterMiddleware`) |
| --- | --- | --- | --- |
| Primitive | `execute` (POSIX shell) | `execute` via `subprocess.run(shell=True)` | `eval` (JS in QuickJS) |
| FS tools | Implemented **on top of** `execute()` | Real host FS | **No** FS unless PTC-allowlisted |
| Network / packages / git | Provider policy | Host network, unlimited CPU/mem/disk | **No** by default |
| Isolation | Container/VM vs host. Not vs injection **inside** | **None** for shell | Same-process `quickjs-rs` heap -- not a VM |
| HITL | `interrupt_on={"execute": True}` on the **tool path**. PTC `tools.execute` would **not** | Same; docs **strongly recommend** HITL | PTC calls **bypass** `interrupt_on` |
| When | Production coding / data analysis | Local CLI / trusted CI only | Loops, batching, deterministic transforms, fan-out `task()` from code |

**Sandbox providers** (swap is a backend instance, not a loop change): LangSmith, Daytona, E2B, Modal, Runloop, Vercel, AgentCore, NVIDIA OpenShell.

**LangSmith sandbox details:** `create_sandbox` wait-for-ready `timeout` default **30 s**. Size defaults: **0.5 vCPU**, memory **~2 GiB** at default CPU. Burst to **2x** requested CPU if host has spare. Lifecycle: `running -> (idle_ttl) -> stopped -> (delete_after_stop) -> deleted`. `idle_ttl_seconds` default **600** (SDK); Deep Agents prod snippet example **3600**. `delete_after_stop_seconds` typically **14 days**.

**QuickJS Code Interpreter:**

Embedded JavaScript interpreter that keeps intermediate results out of model context. The agent writes JavaScript, executes locally, and only the final result enters the conversation.

**Programmatic Tool Calling (PTC):** Selected agent tools are exposed inside QuickJS under `tools.*` namespace. The agent can batch multiple tool calls into a single model turn:

```javascript
// Three tool calls in one model turn instead of three sequential turns
const topics = ["retrieval", "memory", "evaluation"];
const results = await Promise.all(
    topics.map(t => tools.webSearch({ query: `${t} best practices 2025` }))
);
results.join("\n\n");
```

Each saved turn avoids re-processing the full context window -- significant cost savings at scale.

**Persistence modes:**

| Mode | Behavior | Use Case |
|------|----------|----------|
| `"thread"` (default) | State persists across eval calls and agent turns via snapshots | Multi-turn computation |
| `"turn"` | Persists within one turn, resets on next | Single-task computation |
| `"call"` | Fresh REPL each eval, no carry-over | Stateless transforms |

Thread mode lifecycle: turn starts -> restore latest snapshot -> agent calls eval -> turn finishes -> write snapshot to state -> next turn resumes.

**Serialization caveat**: Snapshots preserve serializable data only. Functions, classes, and unserializable objects silently become inaccessible after restore. Snapshot restore **does not undo PTC side effects**.

**Interpreter defaults:** `memory_limit` **64 MiB**; `timeout` **5.0 s**; `tool_name` `"eval"`; `capture_console=True`; `max_result_chars` **4000**; `ptc=None`; `max_ptc_calls` **256** per `eval`; `subagents=True`; `mode="thread"`. Requires `langchain-quickjs>=0.2.0` and Python `>=3.11`. PTC names camelCase (`web_search` -> `tools.webSearch`).

**QuickJS Security Boundary:**

| Capability | Default | How to Expose |
|------------|---------|---------------|
| JavaScript execution | Yes | Add interpreter middleware |
| Top-level await | Yes | Use promises |
| Console capture | Yes | Disable with `capture_console=False` |
| Agent tools | No | Add PTC allowlist |
| Filesystem | No | Add built-in filesystem tools via PTC |
| Network | No | Expose specific network tool via PTC |
| Shell/packages/OS | No | Use sandbox backend instead |

**PTC interrupt gap**: PTC calls inside QuickJS do not enforce `interrupt_on` approval workflows per tool call. Tool calls from the interpreter bypass HITL. Mitigation: careful PTC allowlist curation -- never expose high-risk tools via PTC.

### MCP Session Model

Deep Agents has **no native MCP runtime**. Pattern: `MultiServerMCPClient` -> `await client.get_tools()` -> `create_deep_agent(tools=tools)`.

| Constructor arg | Default | Role |
| --- | --- | --- |
| `connections` | -- | `dict[name, Connection]` |
| `callbacks` | `None` | Progress, logging, elicitation |
| `tool_interceptors` | `None` | Onion around `tools/call`; **first interceptor is outermost** |
| `tool_name_prefix` | `False` | `"server_tool"` against collisions (`"math_add"`) |
| `handle_tool_errors` | `True` | `isError=True` -> `ToolMessage(status="error")` instead of raise |

**Transports:** `"stdio"` (client spawns subprocess; **stateless client still opens a new session per tool call** unless `client.session()`); `"http"` / `"streamable_http"` (spec 2025-03-26); `"sse"` deprecated but still accepted.

**Stateful vs stateless:** default **stateless** = fresh `ClientSession` per invocation. `async with client.session("server_name")` + `load_mcp_tools(session)` when the server keeps context. Transport/session failures **always raise**. `handle_tool_errors=True` lets the **model** retry semantic failures; it will not retry TCP errors unless an interceptor catches them.

### Streaming: v2 vs v3 and `stream.subagents`

Two APIs: legacy `agent.stream(version="v2")` with namespace tuples; **recommended** `agent.stream_events(version="v3")` (Deep Agents >=0.6 / LangChain >=1.3).

v3 projections: `messages`, `values`, `tool_calls`, `subgraphs`, `output`, plus transformers. Deep Agents adds **`stream.subagents`**: one handle per delegated `task`. Lightweight -- discovers tasks first; message/tool/value streams open only when accessed. Docs: use **subagents** for user-facing UI; `subgraphs` is graph-node structure.

Streaming does **not** reduce billed tokens -- it changes TTFT / time-to-first-tool.

---

## Token Economics & Cost Analysis

### Context Tax of the Data Plane

| Knob | Default | Effect |
| --- | --- | --- |
| `tool_token_limit_before_evict` | **20,000** tokens | Tool **results** over threshold -> backend, replace with path + **first 10 lines** |
| `human_message_token_limit_before_evict` | **50,000** tokens | Human-message eviction |
| Write/edit **inputs** | same 20k | Offload delayed until session crosses **85%** of window; results over 20k offload **immediately** |
| `grep_max_count` | **1,000** (`None` disables) | Model can override per call via `max_count` |
| `max_execute_timeout` | **3600 s** | Cap on `execute` `timeout` arg |
| `read_file` `limit` | **100 lines** | Pagination; not a token cap by itself |
| Interpreter `max_result_chars` | **4000** | Truncates `eval` text returned to the model |
| `LocalShellBackend.max_output_bytes` | **100,000** | Truncate host-shell capture |
| `LocalShellBackend.timeout` | **120 s** | Default command wall clock |

Unused built-in tools still send **full JSON schemas every turn**. v0.7 isolated tool-description tokens **4,005 -> 2,302 (-43%)**; default-agent turn **5,395 -> 1,895 (-65%)**. `grep` `output_mode="content"` is the classic blow-up; `files_with_matches` / `count` are cheaper.

### Code Interpreter Token Savings

The QuickJS interpreter is the second-most important cost lever (after SummarizationMiddleware). Without it, every intermediate computation step enters the model context:

```
Without interpreter (3 search calls):
  Turn 1: [context] + model decides to search topic A       -> input_tokens grows
  Turn 2: [context + result A] + model decides to search B  -> input_tokens grows
  Turn 3: [context + result A + B] + model decides search C -> input_tokens grows
  Total model calls: 3, each with growing context

With PTC interpreter (same 3 searches):
  Turn 1: [context] + model writes JS with Promise.all()    -> 1 model call
  Interpreter executes 3 tool calls, returns combined result
  Total model calls: 1, context does not grow between searches
```

### Cost Formula: Interpreter vs Direct Tool Calls

```
Direct sequential calls (N tools):
  C_direct = SUM(i=1..N) [ (base_context + i*avg_result_size) * P_input + response_tokens * P_output ]
  Grows quadratically: each turn re-reads all prior results.

PTC batched calls (N tools in 1 eval):
  C_ptc = (base_context * P_input) + (js_code_tokens * P_output) + (summary_result * P_input)
  Flat: one model turn regardless of N.

Break-even: PTC wins at N >= 2 for typical result sizes.
At N = 5 with 2000-token results: PTC saves ~60% of input token cost.
```

### Model Unit Prices (Worked Examples)

Claude Sonnet 4.6 (Deep Agents docs' default Anthropic string), USD / million tokens:

| | Input | 5m cache write | Cache read | Output |
| --- | --- | --- | --- | --- |
| Sonnet 4.6 | $3 | $3.75 | $0.30 | $15 |

### Worked Example A -- grep dump vs paginated VFS [inferred]

Assumptions: Sonnet 4.6; **1,000 identical runs**; each run does **one** large search then **7** follow-up model calls that re-send history (8 total model calls).

| Path | Tokens into context | Input $ / run | / 1k runs |
| --- | --- | --- | --- |
| `grep` `content` dumps **50,000** tokens, retained all 8 calls | 50,000 x 8 x $3 / 1e6 | **$1.200** | **$1,200** |
| Offload at 20k: preview **~200** tokens (10 lines) x 8; one `read_file` page **~400** tokens once | (200x8 + 400) x $3 / 1e6 | **$0.006** | **$6** |

Delta: **$1,194 / 1k runs** for a single undisciplined dump. If prompt caching held the dump as a prefix, cache reads at $0.30/MTok still **$0.120 / run** -> **$120 / 1k**, 20x the offload path.

### Worked Example B -- Coding Agent (Model + Sandbox) [inferred]

Assumptions: Sonnet 4.6; v0.7 prefix **2,000** tokens cached (5m TTL, 1 write + 7 reads across **8** calls); uncached **4,000** / call; output **600** / call. LangSmith sandbox **0.5 vCPU, 2 GiB**; useful work **90 s**.

Published LangSmith rates: compute **0.0384 LCU / vCPU-hr**, memory **0.0123 LCU / GiB-hr**, storage **0.000123 LSU / GiB-hr**; **1 LCU = $1.50**, **1 LSU = $1.00**. Implied: **$0.0576 / vCPU-hr**, **$0.01845 / GiB-hr**.

**Model / run:** cache write + cache reads + uncached in + output -> **$0.1797 / run -> $180 / 1k**.

| Billing window | Hours | CPU $ | Mem $ | / run | / 1k |
| --- | --- | --- | --- | --- | --- |
| 90 s work only | 0.025 | 0.00072 | 0.00092 | **$0.00164** | **$1.64** |
| 90 s + 600 s idle TTL | 0.1917 | 0.00552 | 0.00707 | **$0.0126** | **$13** |

Idle TTL **~8x** the execute-time bill if you keep thread-scoped boxes warm for 10 minutes.

E2B published: `cost = (vCPU x $0.000014 + RAM_GiB x $0.0000045) x seconds`. Default **2 vCPU, 512 MiB**. 90 s: **$0.00272** -> **$2.72 / 1k**.

### Sandbox Cold Start Costs (Third-Party Benchmarks)

| Provider | p50 | p95 | p99 | Notes |
|----------|-----|-----|-----|-------|
| **Vercel** | **670 ms** | **1,040 ms** | **1,120 ms** | 100% success that run |
| **Modal** | **880 ms** | **1,000 ms** | **1,080 ms** | -- |
| **Runloop** | **890 ms** | **3,270 ms** | **3,500 ms** | -- |
| **E2B** | **1,610 ms** | **1,770 ms** | **1,810 ms** | -- |
| **Cloudflare** | **5,060 ms** | **6,040 ms** | **6,480 ms** | -- |
| **Daytona** | **270 ms** | **430 ms** | **440 ms** | 37% success; vendor <90 ms claim != this snapshot |
| **LangSmith** [inferred] | **5,000 ms** | **15,000 ms** | **30,000 ms** | Unpublished actuals. p99 = ready-wait default 30 s |

### Full Latency SLA Targets

| Path | p50 | p95 | p99 | Grounding |
| --- | --- | --- | --- | --- |
| **Streaming TTFT, parent** [inferred] | **640 ms** | **2,560 ms** | **5,120 ms** | Model token dominant, not VFS |
| **One ReAct cycle (model + StateBackend FS tool)** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | Model + provider queue |
| **StateBackend / permission check extra** [inferred] | **5 ms** | **20 ms** | **80 ms** | Local CPU |
| **Warm sandbox `execute` API extra** [inferred] | **100 ms** | **400 ms** | **1,500 ms** | Provider exec HTTP; command time adds |
| **First `execute` on cold E2B** [inferred] | **3,610 ms** | **9,770 ms** | **21,810 ms** | ReAct cycle + E2B cold in series |
| **MCP `tools/call` Streamable HTTP** [inferred] | **80 ms** | **400 ms** | **2,000 ms** | HTTP tool class |
| **Interpreter `eval`** [inferred] | **20 ms** | **200 ms** | **5,000 ms** | Same-process QuickJS; p99 = 5.0 s fuse |
| **HITL `interrupt_on` execute** [inferred] | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds-minutes; expire -> **deny** |
| **`LocalShellBackend` spawn** [inferred] | **1 ms** | **5 ms** | **20 ms** | ~0 vs remote -- that speed **is** the incident |

Hard fuses (not SLOs): `max_execute_timeout` **3,600,000 ms**; interpreter **5,000 ms**; LocalShell **120,000 ms**.

### Backend Performance Characteristics

| Backend | Latency Profile | Persistence | Scalability |
|---------|----------------|-------------|-------------|
| StateBackend | In-process, fastest | Thread-scoped via checkpointer | Single thread |
| FilesystemBackend | Local disk I/O | Permanent | Single machine |
| StoreBackend | Depends on store impl | Cross-thread | Scales with store backend |
| ContextHubBackend | Network + cache | Hub commits | LangSmith-managed |
| Sandbox backends | Network roundtrip | Session-scoped | Provider-dependent |
| CompositeBackend | Route overhead + backend | Mixed | Route-dependent |

### Throughput / Back-Pressure

| Ceiling | Number | Effect |
| --- | --- | --- |
| Agent Server runs per `thread_id` | **at most one** | Second invoke waits / undefined overlap |
| LangSmith Developer sandboxes | **10** | 100 concurrent tenants does not fit |
| E2B concurrent sandboxes | Hobby **20** / Pro **100** / purchasable **1,100** | Plan limit |
| `grep_max_count` | **1,000** | Local safety valve |
| `max_ptc_calls` | **256** / `eval` | Interpreter fan-out cap |
| `max_execute_timeout` | **3600 s** | Runaway shell fuse |
| Interpreter heap / eval | **64 MiB** / **5.0 s** | Process-local |
| LangSmith included | **5 LCU + 1 LSU / mo** | Then on-demand |

---

## Trade-offs & Failure Modes

### Execution-Layer Failure Taxonomy

| Category | Example | Detection | Response |
|----------|---------|-----------|----------|
| **Transient** | Provider 429/5xx, sandbox allocate **503**, MCP TCP, ContextHub stale commit | Error rate; 503; 401 replay | Full-jitter retries on **idempotent** reads. Do **not** retry `write_file`/`edit_file`/`delete`/`execute` without idempotency key (`write_file` **overwrites** since 0.7) |
| **Permanent** | `ValueError` (`read_file` omitted from allowlist; `max_execute_timeout <= 0`); Composite+sandbox `NotImplementedError` on `/**`; `StoreBackend` without `store`; 4xx auth | Construction / non-retryable | Fail closed. Never "add LocalShell so execute works" |
| **Poison-pill MCP** | Hallucinated / MCPoison-altered `tools/list` (CVE-2025-54136 CVSS 8.8); elicitation auto-accept; `structuredContent` PAN dump; stdio inheriting host env tokens | Hash mismatch; DLP on args; unexpected egress | Gateway hash-pin; fail-closed DLP on MCP args; human elicitation UI; **disable those tools** |
| **Poison-pill execute** | `LocalShellBackend` in prod; pre-0.7.9 hidden-but-callable `execute`; PTC-allowlisted `execute` | Host RCE | Pin `>=0.7.9`; never PTC `execute`; capability hide is not a PEP |
| **Poison-pill paths** | Composite `/memory/` vs `/memories/`; fail-open new paths; write-allow authorizing recursive `delete` | "Memory gone next thread"; subtree wiped | Trailing-slash routes; deny **before** allow |
| **Denial of wallet** | `grep content` 50k blob; idle TTL 600 s on a 90 s job; assistant-scoped box never reaped | Token ledger; LCU burn | Offload; `files_with_matches`; size idle TTL; thread-scoped boxes |
| **Adversarial** | Path traversal (`../../etc/passwd`), code injection via interpreter | Pattern matching, audit log anomaly | Block + alert + full trace |

### Common Failure Modes

| Failure | Cause | Mitigation |
| --- | --- | --- |
| Host RCE ("works on my laptop") | `LocalShellBackend` / `virtual_mode` thought to jail shell | Never on shared hosts; `BaseSandbox` + auth proxy |
| Composite route miss | `/memory/` vs `/memories/`; unmatched -> **default** | Trailing-slash routes; tests for prefix typos |
| `execute` cannot see Store files | Shell on Composite **default** only; no host mapping | Use file tools, not execute, against virtual prefixes |
| `delete` surprises | Missing pre-0.7; write-allow -> recursive delete `>=0.7` | Pin 0.7+; narrower deny/interrupt |
| MCP token passthrough | Docs `headers` Bearer example; interceptor copies user token | OAuthClientProvider / gateway; audience = MCP server |
| `read_file` omitted from allowlist | Construction `ValueError` | Keep `read_file` |
| Grep context overflow | 20k evictor triggers; $ spike | `grep_max_count`; `files_with_matches` |
| `write_file` clobber | Overwrite since 0.7 (no file-exists error) | `edit_file` + permissions/HITL |
| Interpreter snapshot vs PTC | Restore JS vars, not world | Idempotent tools; `mode="call"` if needed |
| Agent-in-sandbox keys | Keys in guest; injection exfil | Sandbox-as-tool + auth proxy (fail-closed) |
| Shared assistant sandbox | Cross-user files + memory poisoning | Thread-scoped boxes; namespaced store |

### NFRs and Explicit Trade-offs

| NFR | Production Stance | Competes With |
| --- | --- | --- |
| **Availability** | Product SLO is the parent loop + FS tools on State/Store. Sandbox allocate and MCP servers are **bulkheaded**: 503/queue or disable those tools. Circuit-open sandbox -> **queue/refuse**, not LocalShell | Coding-agent completeness vs host RCE |
| **RPO checkpointer** | Files **are** checkpoint payload (`DeltaChannel` incremental since 0.6). `InMemorySaver` RPO = **empty on restart** | Crash-consistency vs checkpoint size |
| **RTO checkpointer** | Resume `thread_id`. Restoring a checkpoint does **not** restore guest packages | Time-to-resume vs sandbox reality |
| **RPO sandbox** | Until stop / `idle_ttl` / `delete_after_stop` (typically 14 days). **Orthogonal** to LangGraph checkpoint | Idle $ vs cold start |
| **Compliance** | **Not provided by `deepagents`.** Traces, checkpoints, VFS bytes, sandbox disks, MCP args are subprocessors. GDPR erasure = checkpointer + store + sandbox + trace + MCP-side purge | Time-to-debug vs residency |

---

## Production Patterns & Best Practices

### Three-Tier Sandbox Isolation

This is the most interview-critical security concept in this module.

```
+-------------------------------------------------------------+
|  TIER 3: MicroVMs (Firecracker) -- GOLD STANDARD            |
|  Each workload gets its own kernel on hardware               |
|  virtualization (KVM). Kernel exploit inside one VM          |
|  cannot reach host or other VMs.                             |
|  Boots in ~125ms, ~5MB memory overhead.                      |
|  Powers: AWS Lambda, E2B, Vercel Sandbox.                    |
+-------------------------------------------------------------+
|  TIER 2: User-Space Kernels (gVisor) -- MID-TIER            |
|  Intercepts and re-implements syscalls in user space.        |
|  Agent never talks to real kernel. Less overhead than VM.    |
|  Tradeoff: not all syscalls perfectly emulated.              |
|  Used by: Google Agent Sandbox (GKE), Modal.                 |
+-------------------------------------------------------------+
|  TIER 1: Containers (Docker/runc) -- WEAKEST                |
|  Shared kernel. Kernel vulnerabilities allow escape.         |
|  Microsoft May 2026 CVE: prompt injection in Semantic        |
|  Kernel achieved host-level RCE via container escape.        |
|  Consensus: INSUFFICIENT for untrusted AI agent code.        |
+-------------------------------------------------------------+
```

### What Sandboxes Protect vs Do Not Protect

**Protects**: Host filesystem isolation, process isolation, resource boundaries (CPU, memory, disk).

**Does NOT protect against**:
- **Context injection**: Attacker controlling part of agent input can instruct it to run arbitrary commands *inside* the sandbox.
- **Network exfiltration**: Unless network is blocked, an injected agent can send data out via HTTP/DNS. Some providers support `block_network=True`.

### Credential Handling -- The Cardinal Rule

**Never put secrets inside a sandbox.** API keys, tokens, and database credentials can be read and exfiltrated by a context-injected agent.

**Safe approaches:**

```
Pattern 1: Tools Outside Sandbox (RECOMMENDED)
+--------------------+     tool call     +------------------+
|  Agent in Sandbox  | ----------------> |  Tool on Host    |
|  (no credentials)  | <---------------- |  (has API keys)  |
|                    |     result        |  (handles auth)  |
+--------------------+                   +------------------+

Pattern 2: Auth Proxy with Credential Injection
+--------------------+     HTTP request  +------------------+     authed request
|  Agent in Sandbox  | ----------------> |  Auth Proxy      | ----------------->
|  (no credentials)  | <---------------- |  (injects creds) | <-----------------
|                    |     response      |  (on host/edge)  |     response
+--------------------+                   +------------------+
```

LangSmith auth proxy: sidecar injects headers on matching egress (workspace secrets / opaque creds / AWS SigV4 / GCP SA). Callback **fails closed** (non-2xx, transport error, malformed JSON -> reject, do not forward unauthenticated).

### Circuit Breaker: closed -> open -> half-open

Independent breakers: **sandbox allocate**, **sandbox execute**, **MCP per server**, **Store put/get**, **parent model**. A sandbox 503 must **not** stall a support agent that only needs MCP (**bulkhead**) **and** must not enable host shell.

```
        sandbox 503 / execute 5xx | MCP transport | error-rate window
  +----------+  --------------------------------------------->  +----------+
  |  CLOSED  |                                                   |   OPEN   |
  |  call    |  success resets consecutive count                 | FAIL FAST|
  +----+-----+                                                   | fallback |
       ^                                                         | chain    |
       | probe OK                                                +----+-----+
       |                                                              | cooldown
       |                                                        +-----v------+
       +----------- probe allow --------------------------------| HALF-OPEN  |
                    probe fail -> stay OPEN                      | 1 probe   |
                                                                +------------+
```

**Thresholds [policy]:**

| Trip condition | Closed -> open | Half-open probe | Fallback (**never** unsandboxed execute) |
| --- | --- | --- | --- |
| Sandbox allocate 503 / pool empty | consecutive >= **3** | One `create`/`lookup` | **Queue** the run. **Never** `LocalShellBackend` |
| Sandbox `execute` 5xx / timeout | error-rate + p99 | One `echo` | Retry with jitter if idempotent; else ToolMessage error |
| MCP server down / hash drift | transport raise or pin mismatch | One `tools/list` re-hash | **Disable those tools**. Agent continues on VFS |
| Store down | put/get errors | One KV get | Disable `/memories/` writes; keep StateBackend |
| Interpreter heap / timeout | `eval` 5 s / snapshot drop | n/a | Return error to model; do not PTC-fallback to `execute` |

**Fallback chain (required interview answer):** **remote sandbox -> queue/refuse.** MCP down -> **disable those tools**. Store down -> **StateBackend only**. Interpreter fail -> **no shell consolation prize**. Never: sandbox 503 -> LocalShell. Never: HITL timeout -> auto-approve. Never: circuit open -> `virtual_mode=False`. Never: MCP 401 -> passthrough user bearer.

### Zero-Trust MCP + Tool-Level RBAC

`permissions=` **will not** save you. MCP tools are additive `tools=` items. Zero-Trust is a **gateway PEP in front of MCP**, not glob rules.

| Zero-Trust control | On this data plane |
| --- | --- |
| **Transport** | OAuth 2.1 + PKCE `S256`. Clients **MUST** send RFC **8707** `resource` = **canonical MCP server URI**. **MUST NOT** passthrough the client token upstream (mint new token; RFC **8693**). `headers=` static Bearer in docs is an anti-pattern |
| **Server allowlist** | Only approved entries in `MultiServerMCPClient` connections |
| **Tool allowlist / prefix** | Filter `get_tools()`; `tool_name_prefix=True` against shadowing |
| **Hash-pin descriptions** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify every `tools/call`. CVE-2025-54136 (MCPoison) CVSS **8.8** |
| **Interceptor PDP** | `runtime.context` user id; deny list; rate limit; short-circuit `ToolMessage` |
| **Identity** | Verified access token. **Never** the LLM. `user_id` in model JSON is a **proposal** |

### Execution-Layer RBAC

| Role | Tools Available | Path Access | Sandbox |
|------|----------------|-------------|---------|
| **Analyst** | `read_file`, `glob`, `grep` | Read-only `/workspace/**`, `/shared/**` | No |
| **Engineer** | `read_file`, `write_file`, `edit_file`, `glob`, `grep` | Read/write `/workspace/**` | No |
| **Admin** | All tools including `execute` | Full access | Yes, with network restrictions |
| **Auditor** | `grep`, `glob` | Read-only, all paths | No |

### PII Pipeline for File Operations

Three-stage pipeline applied at the execution layer:

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner (Presidio/gateway). Scan: user input, model output, **tool args** (especially MCP), **file contents** on `write_file`/`edit_file`/`read_file` pages, offload candidates, sandbox env, traces. If ML is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on MCP args, `execute` env, and VFS writes.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the task can continue; `block` when the field must not exist. Strip from VFS **and** message channel. Do not persist raw PAN in traces. `0.7.9` disabled tracing **inputs** on middleware -- reduces accidental PII, **not** a substitute for DLP.
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action, detector, `correlation_id`, `tenant`, `thread_id`, permission decision, **tool arg digest**, **execute command digest + exit code**. A tool call without an audit row is a control-plane bug.

### Production Readiness Checklist

1. Replace default StateBackend with durable backend matching your persistence needs
2. Always wrap with CompositeBackend to separate internal data from user workspace
3. Set `virtual_mode=True` on every FilesystemBackend -- `False` is never safe
4. Define explicit permission rules; default permissive policy is inappropriate for multi-user
5. Match sandbox isolation tier to threat model (Firecracker for untrusted, never raw Docker)
6. Never put credentials inside sandboxes -- use auth proxy or tools-outside-sandbox
7. Block network by default in sandbox, allowlist specific endpoints
8. Keep PTC allowlists minimal -- PTC calls bypass HITL
9. Set interpreter resource limits (`memory_limit`, `timeout`, `max_ptc_calls`)
10. Configure sandbox TTL; monitor for unbounded growth in assistant-scoped sandboxes
11. Pin `deepagents>=0.7.9` so `excluded_tools` blocks execution
12. Pin `>=0.7.10` if sandbox glob failures must surface

### Backend Selection Decision Tree

```
Need cross-thread persistence?
  YES -> Need semantic search?
    YES -> StoreBackend (PostgreSQL-backed)
    NO  -> StoreBackend or ContextHubBackend
  NO  -> Need shell/code execution?
    YES -> Need security for untrusted code?
      YES -> Sandbox backend (E2B for max isolation, Modal for GPU)
      NO  -> LocalShellBackend (dev only, HITL mandatory)
    NO  -> Need file permanence beyond session?
      YES -> FilesystemBackend (virtual_mode=True, always)
      NO  -> StateBackend (default, simplest)

ALWAYS -> Wrap with CompositeBackend to isolate internal data from user data
```

### Retry with Exponential Backoff

For transient backend failures:

- **Max retries**: 3
- **Backoff**: Exponential with full jitter -- `delay = min(base * 2^attempt, max_delay) * random()`
- **Base delay**: 200-500ms, **max delay**: 2-10s
- **Dead-letter**: After exhaustion, log with full context and return structured error. Do not silently swallow.
- **Idempotency**: `write_file` and `edit_file` are naturally idempotent. `execute` is NOT -- retrying may duplicate side effects. Use circuit breaker for `execute`.

### Version Gates That Change This Plane

| Version | Behavioral gate |
| --- | --- |
| `>=0.5.0` / `>=0.5.2` | Multimodal; `permissions=`; Store `Runtime` namespace factory |
| `>=0.6` / LangChain 1.3 | `stream_events` v3; `DeltaChannel`; interpreters experimental |
| `>=0.6.8` | Permission `mode="interrupt"` |
| **`>=0.7.0`** | `delete`; FS `tools=` allowlist; `write_file` overwrites; `virtual_mode` default True; backend factories removed; empty ls/glob string change |
| `>=0.7.2` | Multimodal scrub vs model profile |
| `>=0.7.3` | Exact-match `delete` first-match-wins |
| `>=0.7.4` | Execute exit code on `ToolMessage.artifact` |
| `>=0.7.7` | ContextHub concurrent mutations batched |
| `>=0.7.9` | `excluded_tools` blocks **execution**; tracing inputs off |
| `>=0.7.10` | Sandbox glob failures no longer swallowed |

---

## Code Examples

### Complete Production Execution Environment Setup

```python
"""
Production execution environment with CompositeBackend, sandbox,
permissions, interpreter, and MCP integration.

Requirements:
  pip install deepagents langgraph-checkpoint-postgres
  pip install langchain-mcp-adapters e2b-code-interpreter
"""

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
    StoreBackend,
)
from deepagents.interpreter import InterpreterMiddleware
from deepagents.middleware import (
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from deepagents.permissions import FilesystemPermission
from deepagents.sandboxes import E2BSandboxBackend
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"

checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()

store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# 1. Sandbox backend -- Firecracker microVM via E2B
sandbox_backend = E2BSandboxBackend(
    api_key="e2b_api_key_from_vault",  # from secrets manager, never hardcoded
    template="python-3.12",
    idle_ttl_seconds=3600,
    timeout=120,
    max_output_bytes=100_000,
)

# 2. CompositeBackend -- route user workspace to sandbox, memories to store
backend = CompositeBackend(
    default=StateBackend(),  # internal data: summaries, tool results
    routes={
        "/workspace/": sandbox_backend,
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (
                rt.server_info.user.identity,
                "memories",
            ),
        ),
        "/shared/": FilesystemBackend(
            root_dir="./shared_resources",
            virtual_mode=True,
        ),
    },
)

# 3. Permissions -- defense in depth
permissions = [
    # Block all secret files everywhere
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/.env.*", "/**/credentials*", "/**/*.key", "/**/*.pem"],
        mode="deny",
    ),
    # Full access to user workspace (sandbox-backed)
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    # Read-only access to shared resources
    FilesystemPermission(
        operations=["read"],
        paths=["/shared/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/shared/**"],
        mode="deny",
    ),
    # Human approval for memory writes
    FilesystemPermission(
        operations=["read"],
        paths=["/memories/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/memories/**"],
        mode="interrupt",
    ),
    # Deny everything else
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**"],
        mode="deny",
    ),
]

# 4. Interpreter -- QuickJS with PTC for batched operations
interpreter_mw = InterpreterMiddleware(
    mode="thread",
    memory_limit=64 * 1024 * 1024,  # 64 MB heap
    timeout=5.0,
    max_result_chars=4000,
    max_ptc_calls=256,
    ptc_allowlist=[
        "read_file",
        "grep",
        "glob",
        "search_docs",
    ],
    # NOTE: do NOT add write_file, delete, or execute to ptc_allowlist
    # PTC calls bypass HITL interrupt_on checks
)

# 5. Assemble the agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_docs, deploy_service],
    system_prompt="You are a senior DevOps engineer assistant...",
    middleware=[
        SummarizationMiddleware(trigger=("tokens", 80_000), retention=("messages", 15)),
        ToolCallLimitMiddleware(max_calls=150),
        interpreter_mw,
    ],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["deploy_service"]},
    checkpointer=checkpointer,
    store=store,
)
```

### Custom Backend Implementation (S3)

```python
"""Custom backend wrapping S3. Shows the BackendProtocol contract.
Key rule: return structured dicts with 'error' field -- never raise exceptions."""

import boto3
from typing import Any

class S3Backend:
    def __init__(self, bucket: str, prefix: str = "agent-files/"):
        self.bucket = bucket
        self.prefix = prefix
        self.s3 = boto3.client("s3")

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path.lstrip('/')}"

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(file_path))
            content = obj["Body"].read().decode("utf-8")
            lines = content.splitlines(keepends=True)
            return {"content": "".join(lines[offset : offset + limit]), "error": ""}
        except self.s3.exceptions.NoSuchKey:
            return {"content": "", "error": f"File not found: {file_path}"}

    def write(self, file_path: str, content: str) -> dict[str, Any]:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(file_path), Body=content.encode())
        return {"error": ""}

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> dict[str, Any]:
        result = self.read(file_path, offset=0, limit=100_000)
        if result["error"]:
            return result
        content = result["content"]
        if old_string not in content:
            return {"error": f"String not found in {file_path}"}
        updated = (content.replace(old_string, new_string) if replace_all
                   else content.replace(old_string, new_string, 1))
        return self.write(file_path, updated)
```

### Production Sandbox + Circuit Breaker + PII Pipeline

```python
#!/usr/bin/env python3
"""Execution data plane: sandbox execute, MCP PEP, VFS composite, stdlib fallbacks.

Fallback: sandbox 503 -> queue; MCP down -> disable those tools.
NEVER fail-open to LocalShell / unsandboxed execute.
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- retries + full jitter ---
def retry_call(fn, *, attempts=3, base_s=0.2, cap_s=2.0,
               retryable=(TimeoutError, ConnectionError)):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = random.random() * min(cap_s, base_s * (2**i))
            time.sleep(sleep_s)
    raise last

# --- circuit breaker ---
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
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
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# --- PII: detect -> redact -> audit ---
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_detect_redact_audit(text, *, audit, correlation_id, tenant_id,
                             sink, block_on_pan=True):
    kinds = []
    if EMAIL_RE.search(text): kinds.append("email")
    if PAN_RE.search(text): kinds.append("pan")
    pre = hashlib.sha256(text.encode()).hexdigest()
    if "pan" in kinds and block_on_pan and sink in {"mcp_args", "execute_env", "vfs_write"}:
        audit.append({"cid": correlation_id, "sink": sink, "action": "block"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": correlation_id, "sink": sink,
                  "action": "redact" if redacted != text else "allow",
                  "pre": pre, "post": hashlib.sha256(redacted.encode()).hexdigest()})
    return redacted

# --- Composite longest-prefix router ---
@dataclass
class CompositeRouter:
    default: str
    routes: dict[str, str]
    def resolve(self, path: str) -> str:
        hits = [p for p in self.routes if path.startswith(p)]
        return self.routes[max(hits, key=len)] if hits else self.default

# --- Sandbox pool: 503 -> queue; LocalShell is not a port ---
class LocalShellForbidden(RuntimeError):
    """Invariant: circuit-open sandbox MUST NOT fail-open to host shell."""

def local_shell_execute(command):
    raise LocalShellForbidden(f"refused_local_shell:{command[:40]}")
```

---

## Interview Q&A

**Q1. What is the Deep Agents execution environment, in one minute?**
I treat it as the data plane -- where the agent acts -- not a second runtime. Four layers: tools, virtual filesystem, filesystem permissions, code execution; typed streams to observe. LangGraph still runs ReAct. `create_deep_agent` only binds `backend=`, `permissions=`, additive `tools=`, and `FilesystemMiddleware`. Eight `FsToolName`s; `task` is subagent middleware; MCP is adapters, not a native runtime. Permissions are fail-open and FS-only. I never ship `LocalShellBackend`.

**Q2. Walk a tool call to a stream event.**
Model emits `tool_calls`. Name filter (`excluded_tools` also blocks execution as of 0.7.9) and capability hide (`execute`/`delete` if the backend cannot). Built-in FS tools hit the path PDP -- first match wins, no match allows -- then the backend. MCP and `execute` skip that PDP: MCP goes through my gateway PEP; `execute` goes to `SandboxBackendProtocol`. Composite longest-prefix routes; miss falls through to default. Result is an untrusted `ToolMessage`; large blobs offload at 20k tokens; v3 `stream.tool_calls.output_deltas` and `stream.subagents` for `task`.

**Q3. What are the four execution-environment layers in Deep Agents?**
Tools, virtual filesystem, filesystem permissions, and code execution.

**Q4. What backend do I get by default?**
`StateBackend()`, which is thread-scoped and checkpoint-backed.

**Q5. When should I use `CompositeBackend`?**
When different path prefixes need different persistence or safety properties, such as ephemeral `/workspace/` plus durable `/memories/`. Also always use it to separate internal artifacts from your repository tree.

**Q6. What is the difference between `LocalShellBackend` and a sandbox backend?**
Both expose `execute`, but `LocalShellBackend` runs on the host without isolation via `subprocess.run(shell=True)` while sandbox backends execute in isolated environments (Firecracker, gVisor). Never use `LocalShellBackend` in production.

**Q7. `excluded_tools` vs `FilesystemMiddleware(tools=)`.**
Allowlist is construction: only listed FS names exist, and `read_file` is required or I get `ValueError`. `excluded_tools` is a post-injection name filter that can also drop caller tools; since 0.7.9 it blocks execution. Capability filter still hides `execute` on StateBackend even if I list it. Declarative subagents do not inherit my allowlist.

**Q8. How do Deep Agents filesystem permissions work?**
They are declarative `FilesystemPermission` rules evaluated top to bottom with first-match-wins semantics on the built-in filesystem tools. `read` covers `ls`, `read_file`, `glob`, and `grep`; `write` covers `write_file`, `edit_file`, and `delete`. No match -> allow (fail-open). They do NOT cover `execute`, MCP, custom tools, or `backend.*`.

**Q9. What does `mode="interrupt"` do?**
It turns a matching file operation into a human-review pause instead of an automatic allow or deny. Requires a checkpointer.

**Q10. How do permissions behave with subagents?**
Subagents inherit the parent rules by default, but a subagent's own `permissions=` list replaces the parent's rules entirely.

**Q11. What are the two code-execution paths in Deep Agents?**
Shell execution through `execute` on a shell-capable backend, and JavaScript execution through `CodeInterpreterMiddleware`.

**Q12. When should I use a sandbox instead of the interpreter?**
Use a sandbox for OS-level work such as tests, package installs, and CLI calls. Use the interpreter for lightweight orchestration, loops, and data transforms.

**Q13. What is PTC?**
Programmatic tool calling. It exposes an allowlisted subset of tools inside the interpreter as async `tools.*` functions. PTC calls bypass `interrupt_on`.

**Q14. Does `interrupt_on` protect PTC tool calls?**
No. The docs explicitly say PTC-invoked tool calls do not use the normal approval path. Never PTC-expose high-risk tools.

**Q15. Give me `$ per 1k` for this plane.**
Inferred, not a SKU. Undisciplined `grep content` of 50k tokens retained across 8 Sonnet 4.6 calls: **$1,200 / 1k** vs offload+page **$6 / 1k**. Coding agent: model **$180 / 1k** (8 calls, 2k cached prefix); LangSmith sandbox 90 s **$1.64 / 1k**, or **$13 / 1k** if it sits for the 600 s idle TTL. E2B 90 s **$2.72 / 1k**. Interpreter sandbox line is $0.

**Q16. Permissions -- is that Zero Trust for MCP and shell?**
No. Fail-open path PDP for built-in FS tools only. `execute`, `task`, MCP, PTC, and `backend.*` are uncovered; #2894 declined `ExecutePermission`. Zero-Trust MCP is a gateway PEP: server allowlist, tool allowlist, hash-pin every `tools/call` (CVE-2025-54136), OAuth 2.1, RFC 8707 audience = that MCP server, no user-token passthrough.

**Q17. Circuit breaker and fallback when the sandbox is 503.**
The library does not ship a breaker. I implement closed -> open -> half-open with one probe. Sandbox 503 or execute 5xx: queue or refuse. MCP transport/hash drift: disable those tools and keep the agent on VFS. I never fail-open to `LocalShellBackend`, never unsandboxed `execute`, never HITL-timeout auto-approve.

**Q18. CompositeBackend -- how does routing actually work?**
Longest prefix wins; no match uses default with **no error**. `ls`/`glob`/`grep` aggregate. `execute` is default-only. `/memory/foo` is not `/memories/`. Writes to two children are not one transaction.

**Q19. Sandbox vs LocalShell vs interpreter -- pick for a coding copilot.**
Remote thread-scoped sandbox. LocalShell is unrestricted `subprocess.run(shell=True)` -- local CLI/CI only. Interpreter is QuickJS `eval`, 64 MiB / 5 s / 4000 chars, no pip/git, PTC bypasses HITL, same-process boundary. I may run interpreter **beside** the sandbox for batch loops with PTC `grep`/`read_file` only. Secrets stay in an auth proxy that fails closed.

**Q20. What did 0.7.x change on this plane that still bites?**
`delete` exists and write-allow authorizes recursive delete; `write_file` overwrites; empty ls/glob is the string `No files found`; `virtual_mode` default true but still not a shell jail; allowlist requires `read_file`; 0.7.9 exclusion-is-enforcement and tracing inputs off; 0.7.10 glob failures surface.

---

## System Design Scenarios

### Scenario 1: Multi-Tenant Data Analysis Platform

**Problem**: A fintech company wants to offer AI-assisted data analysis to 200 enterprise clients. Each client uploads CSV/Parquet datasets (up to 500MB). The agent must load data, run Python analysis, generate visualizations, and return results. Client data must be strictly isolated. 500 concurrent analysis sessions.

**Architecture:**

```
+-------------------------------------------------------------+
|                    API Gateway (JWT + tenant ID)              |
+-----------------------------+-------------------------------+
                              v
+-------------------------------------------------------------+
|                    LangGraph Cloud (K8s)                      |
|  +-----------------------------------------------------------+
|  |  Supervisor Agent (claude-sonnet-4-6)                     |
|  +-----------------------------------------------------------+
|  |  Worker: data_analyst (claude-haiku-4)                    |
|  |    Sandbox: Modal (gVisor) -- Python + pandas + plotly    |
|  |    GPU: available for ML model inference                  |
|  |    Network: blocked except internal APIs                  |
|  |                                                           |
|  |  Worker: report_writer (claude-sonnet-4-6)                |
|  |    No sandbox, read-only filesystem access                |
|  +-----------------------------------------------------------+
|  |  CompositeBackend                                         |
|  |    /data/     -> S3Backend(bucket=tenant_bucket)          |
|  |    /results/  -> StoreBackend(ns=(tenant_id, thread_id))  |
|  |    /shared/   -> FilesystemBackend(templates, virtual)    |
|  |    (default)  -> StateBackend()                           |
|  +-----------------------------------------------------------+
|  |  Interpreter: QuickJS with PTC [read_file, glob, grep]   |
|  |    Batches data exploration before heavy sandbox compute  |
|  +-----------------------------------------------------------+
|  PostgresSaver (checkpoints)  |  Warm sandbox pool (Modal)   |
+-------------------------------------------------------------+
```

**Trade-Off Matrix:**

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Sandbox provider | E2B (Firecracker) | Modal (gVisor) | Modal | GPU support needed for client ML models; gVisor sufficient for semi-trusted paying clients |
| Data storage | FilesystemBackend | Custom S3Backend | S3Backend | 500MB datasets on local disk do not scale to 500 concurrent sessions |
| Credential handling | Inject into sandbox | Auth proxy | Auth proxy | Credentials inside sandbox + prompt injection = data exfiltration |
| Interpreter | Skip | QuickJS for exploration, sandbox for compute | QuickJS + sandbox | Exploration (columns, shapes) should not require sandbox cold start; ~40-60% sandbox reduction |

### Scenario 2: Multi-Tenant Coding Agent

**Problem.** Per-user "fix my repo / run tests" copilot. Untrusted prompt + untrusted repo. Need `pip install`, pytest. Multi-tenant SaaS. Security forbids host shell.

**Recommended: Thread-scoped remote sandbox.**

```
  +----------+   +-------------------------------------------------------------+
  | IdP/PEP  |-->| CONTROL: async graph factory                                |
  | JWT ->   |   |   LangSmithSandbox name=thread-{thread_id}                  |
  | user_id  |   |   idle_ttl sized to session (600 vs 3600 $ vs cold)         |
  |          |   |   Composite default=sandbox                                 |
  |          |   |     /memories/ -> StoreBackend ns=(user.identity,)           |
  |          |   |   permissions: routes only -- NEVER /**  (raises)            |
  |          |   |   interrupt_on execute+delete   pin >=0.7.9                  |
  |          |   |   PII detect->redact->audit; WORM execute arg_digest        |
  |          |   |   auth proxy (fail-closed callback) -- no keys in guest     |
  +----------+   +------------------------------+------------------------------+
                                                 v
                    +------------------------------------------------------+
                    | DATA: sandbox-as-tool (agent process on your server)  |
                    |   FS tools = POSIX scripts over execute()             |
                    |   upload_files seed / download_files harvest          |
                    |   interpreter OPTIONAL companion, PTC != execute      |
                    |   sandbox 503 -> queue  (breaker; never LocalShell)   |
                    +------------------------------------------------------+
```

| Axis | Remote sandbox | `LocalShellBackend` on VM | Interpreter-only |
| --- | --- | --- | --- |
| **Cost** | Model $180/1k + sandbox $1.64-$13/1k | $0 sandbox until incident | $0 sandbox; still pay model |
| **Security** | Strong vs host; useless PDP on `execute`; secrets via proxy | **None.** Host `.env` / SSH keys | Strong vs OS; no git/pip |
| **Scalability** | Provider concurrency limits | One host user for all tenants -- a CVE | 64 MiB heap; 256 PTC calls |

**Decision.** Remote sandbox wins. LocalShell is dev-only. Interpreter-only cannot run tests.

### Scenario 3: Regulated Document Processing (Healthcare)

**Problem**: Healthcare org needs AI agents to process clinical trial documents containing PHI (HIPAA). Complete audit trail required. No data may leave VPC.

**Architecture:** Self-hosted on EKS in VPC. DLP pre-processing to de-identify before LLM. No external sandbox (data cannot leave VPC). Semantic firewall (secondary model checks for PHI leakage, hallucinated citations). Phase-boundary checkpointing for 10-minute SLA.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deployment | Self-hosted EKS | HIPAA: PHI cannot leave VPC |
| PHI handling | DLP pre-processing | Defense-in-depth even with self-hosted LLM |
| Sandbox | No sandbox (FilesystemBackend, virtual_mode=True) | No external providers; code execution not needed for document processing |
| Output validation | Semantic firewall (secondary haiku model) | Healthcare compliance requires verifiable outputs |
| Checkpointing | Phase boundaries only | 10-minute SLA; full-node adds ~15% overhead for no benefit within a phase |

---

## Key Numbers to Memorize

### Package / Tools / Versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **8 / 9 / 10** | `FsToolName`s / + `task` / + opt-in `write_todos` |
| **`read_file` required** | `FilesystemMiddleware(tools=)` else `ValueError` |
| **`>=0.7.9`** | `excluded_tools` blocks **execution** |
| **`>=0.7.10`** | Sandbox glob failures surface |
| **`>=0.7.4`** | Execute exit code on `ToolMessage.artifact` |
| **`>=0.7.3`** | Exact-match `delete` first-match-wins |

### Tokens / Knobs
| Number | What |
| --- | --- |
| **20,000 / 10 lines** | Tool-result offload / preview |
| **50,000** | Human-message eviction |
| **85%** | Delay write/edit-input offload until this window fraction |
| **1,000** | `grep_max_count` |
| **100 lines / 2000** | Model `read_file` limit vs protocol `read` default |
| **4** | `NUM_CHARS_PER_TOKEN` |
| **4,005 -> 2,302 / 5,395 -> 1,895** | v0.7 tool-description tokens (-43%); default-agent turn (-65%) |
| **4000 / 256 / 64 MiB / 5.0 s** | Interpreter `max_result_chars` / `max_ptc_calls` / heap / eval timeout |
| **100,000 / 120 s** | LocalShell `max_output_bytes` / default timeout |
| **3600 s** | `max_execute_timeout` |

### $ / SKUs [inferred]
| Number | What |
| --- | --- |
| **$3 / $15 / $3.75 / $0.30** | Sonnet 4.6 in / out / 5m write / cache read per MTok |
| **$1,200 vs $6 per 1k** | 50k grep dump x 8 calls vs 20k offload + one page |
| **$180 / 1k** | Coding-agent model (8 calls, 2k prefix) |
| **$1.64 / $13 per 1k** | LangSmith sandbox 90 s vs 90 s+600 s TTL |
| **$2.72 / 1k** | E2B 2 vCPU / 0.5 GiB x 90 s |

### Sandbox / MCP / Production
| Number | What |
| --- | --- |
| **30 s / 600 s / 3600 s / 14 days** | LangSmith ready-wait / idle TTL / DA prod TTL / delete-after-stop |
| **0.5 vCPU / ~2 GiB** | LangSmith sandbox defaults |
| **fail-open** | `permissions=` when no rule matches |
| **RFC 8707 / RFC 8693** | MCP audience / no passthrough (exchange) |
| **8.8** | CVE-2025-54136 MCPoison CVSS |

### Latency (numeric ms)
| Number | What |
| --- | --- |
| **670 / 1,040 / 1,120 ms** | Vercel cold start p50/p95/p99 |
| **880 / 1,000 / 1,080 ms** | Modal cold start |
| **1,610 / 1,770 / 1,810 ms** | E2B cold start |
| **5,060 / 6,040 / 6,480 ms** | Cloudflare cold start |
| **640 / 2,560 / 5,120 ms** | [inferred] streaming TTFT |
| **2,000 / 8,000 / 20,000 ms** | [inferred] ReAct cycle + local FS |
| **100 / 400 / 1,500 ms** | [inferred] warm execute API extra |
| **80 / 400 / 2,000 ms** | [inferred] MCP HTTP `tools/call` |
| **20 / 200 / 5,000 ms** | [inferred] interpreter `eval` (p99 = 5.0 s fuse) |
| **30,000 / 180,000 / 600,000 ms** | [inferred] HITL execute clock; expire-deny |

---

*Practice the Q&A out loud; recode the sandbox->queue breaker and MCP disable path from memory; recompute the grep $1,200 vs $6 and the 90 s vs 600 s TTL sandbox bill on a whiteboard.*

**Sources**: LangChain Deep Agents docs (tools, backends, sandboxes, interpreters, permissions), E2B/Modal/Daytona sandbox docs, OWASP Top 10 Agentic 2026, NSA/CISA agentic AI adoption guidance, Microsoft Semantic Kernel CVE disclosures, MarkTechPost cold-start benchmarks (Aug 2026).
