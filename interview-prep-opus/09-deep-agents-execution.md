# Module 09: Deep Agents -- Execution Environment

**Prep target**: Director/VP AI roles
**Prerequisite**: Module 08 (Deep Agents Architecture)
**Framework**: LangChain Deep Agents >= 0.7.x (released March 2026)

---

## What Is This?

In Module 08, we built the *brain* of the agent -- the middleware stack, the planning layer, the memory. This module is about the *hands and feet*: where the agent actually reads files, writes code, runs commands, and stores results.

Think of it like renting office space for your employee. The "virtual filesystem" is their desk -- drawers they can organize however they want. The "backends" determine whether that desk is a cardboard box that disappears when they go home (in-memory), a real filing cabinet (local disk), or a cloud drive shared across offices (PostgreSQL store). The "sandbox" is a sealed clean room: the agent can run experiments there without risking the main building's plumbing.

The key design insight is **separation of concerns**: the agent's file operations (read, write, edit, grep) are identical regardless of where files physically live. You swap backends -- in-memory for tests, local disk for CLI tools, cloud store for multi-tenant SaaS -- without changing a single line of agent code.

## Why It Matters

Execution environment decisions are where Director/VP candidates win or lose system design interviews. The wrong backend choice leaks customer data across tenants. The wrong sandbox tier lets a prompt injection achieve host-level remote code execution. The wrong credential handling pattern puts API keys inside a container that an attacker can exfiltrate. These are not theoretical risks -- Microsoft disclosed CVEs in May 2026 showing exactly this attack chain.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Deep Agent Execution Layer                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────── VIRTUAL FILESYSTEM TOOLS ─────────────────────┐  │
│  │                                                               │  │
│  │  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────┐  │  │
│  │  │   ls   │ │read_file │ │write_file│ │edit_file│ │ delete │  │  │
│  │  └────┬───┘ └────┬─────┘ └────┬─────┘ └────┬───┘ └───┬────┘  │  │
│  │       │          │            │             │          │       │  │
│  │  ┌────────┐ ┌────────┐  ┌─────────┐                           │  │
│  │  │  glob  │ │  grep  │  │ execute │ (sandbox only)            │  │
│  │  └────┬───┘ └────┬───┘  └────┬────┘                           │  │
│  │       │          │           │                                 │  │
│  └───────┼──────────┼───────────┼────────────────────────────────┘  │
│          │          │           │                                    │
│          v          v           v                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              PERMISSION LAYER (first-match-wins)             │   │
│  │    allow -> deny -> interrupt -> default (permissive)        │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                             │                                       │
│                             v                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    BACKEND ROUTER                            │   │
│  │              (BackendProtocol dispatch)                      │   │
│  ├──────────┬───────────┬───────────┬───────────┬──────────────┤   │
│  │  State   │Filesystem │   Store   │ Context   │  Sandbox     │   │
│  │ Backend  │  Backend  │  Backend  │Hub Backend│  Backend     │   │
│  │(default) │(local disk│(cross-thd)│(LangSmith)│(E2B/Modal/  │   │
│  │          │virtual_md)│(Postgres) │(Hub cmts) │ LangSmith)   │   │
│  └────┬─────┴─────┬─────┴─────┬─────┴─────┬─────┴──────┬──────┘   │
│       │           │           │           │            │            │
│       v           v           v           v            v            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ ┌──────────────┐  │
│  │Checkpt  │ │  Disk   │ │Postgres │ │Context │ │ Firecracker/ │  │
│  │ State   │ │  I/O    │ │  Store  │ │  Hub   │ │ gVisor VM    │  │
│  └─────────┘ └─────────┘ └─────────┘ └────────┘ └──────────────┘  │
│                                                                     │
│  ┌──────────────── CODE INTERPRETER ─────────────────────────────┐  │
│  │  QuickJS (embedded, capability-scoped)                        │  │
│  │  PTC: tools.* namespace for batched tool calls                │  │
│  │  Modes: thread | turn | call                                  │  │
│  │  Limits: 64MB heap, 5s timeout, 4000 char result, 256 PTC    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────── MCP INTEGRATION ──────────────────────────────┐  │
│  │  MultiServerMCPClient (stdio, HTTP, OAuth)                    │  │
│  │  Stateful sessions, tool filtering, auto-discovery            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────── TELEMETRY PLANE ──────────────────────────────┐  │
│  │  LangSmith traces      │  Token/cost metrics per trace        │  │
│  │  Execution logs         │  Tool call audit trail (who/what/   │  │
│  │  (stdout/stderr/exit)   │  when/path/operation/result)        │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### CompositeBackend Routing

```
┌─────────────────────────────────────────────────────────┐
│                   CompositeBackend                       │
│                                                         │
│  Incoming path: "/workspace/src/main.py"                │
│                                                         │
│  Route table (longest-prefix match):                    │
│    /workspace/  ──> FilesystemBackend(virtual_mode=True) │
│    /memories/   ──> StoreBackend(ns=user_id)            │
│    /skills/     ──> ContextHubBackend()                  │
│    (default)    ──> StateBackend()                       │
│                                                         │
│  Match: /workspace/ (14 chars) wins                     │
│  Dispatch: FilesystemBackend.read("src/main.py")        │
│                                                         │
│  Internal data (/large_tool_results/,                   │
│  /conversation_history/) hits default StateBackend      │
│  -- never mixes with user data                          │
└─────────────────────────────────────────────────────────┘
```

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

---

## Part 2: Core Mechanics & Algorithms

### Backend Protocol

Every backend implements these methods:

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

### Six Backend Types -- Lifecycle & Behavior

| Backend | Storage | Persistence | Key Caveat |
|---------|---------|-------------|------------|
| **StateBackend** (default) | Graph state, keyed by `thread_id` | Thread-scoped via checkpointer | Ephemeral with MemorySaver |
| **FilesystemBackend** | Local disk, configurable `root_dir` | Permanent | `virtual_mode=True` required -- without it, no security even with `root_dir` |
| **LocalShellBackend** | Extends Filesystem + `execute` | Permanent | `subprocess.run(shell=True)`, no sandbox, HITL mandatory. Dev only. |
| **StoreBackend** | LangGraph `BaseStore` (Postgres prod) | Cross-thread | Without namespace factory, all users share storage |
| **ContextHubBackend** | LangSmith Hub (version-controlled) | Hub commits | UTF-8 only; binary files silently rejected |
| **CompositeBackend** | Routes paths to other backends | Mixed | Always use to separate internal data from user workspace |

### Namespace Isolation in StoreBackend

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

Namespace validation: alphanumeric, hyphens, underscores, dots, `@`, `+`, colons, tildes. Wildcards are rejected to prevent glob injection.

### QuickJS Code Interpreter

Embedded JavaScript interpreter that keeps intermediate results out of model context. The agent writes JavaScript, executes locally, and only the final result enters the conversation.

**Programmatic Tool Calling (PTC)**: Selected agent tools are exposed inside QuickJS under `tools.*` namespace. The agent can batch multiple tool calls into a single model turn:

```javascript
// Three tool calls in one model turn instead of three sequential turns
const topics = ["retrieval", "memory", "evaluation"];
const results = await Promise.all(
    topics.map(t => tools.webSearch({ query: `${t} best practices 2025` }))
);
results.join("\n\n");
```

Each saved turn avoids re-processing the full context window -- significant cost savings at scale.

**Persistence modes**:

| Mode | Behavior | Use Case |
|------|----------|----------|
| `"thread"` (default) | State persists across eval calls and agent turns via snapshots | Multi-turn computation |
| `"turn"` | Persists within one turn, resets on next | Single-task computation |
| `"call"` | Fresh REPL each eval, no carry-over | Stateless transforms |

Thread mode lifecycle: turn starts -> restore latest snapshot -> agent calls eval -> turn finishes -> write snapshot to state -> next turn resumes.

**Serialization caveat**: Snapshots preserve serializable data only. Functions, classes, and unserializable objects silently become inaccessible after restore.

### MCP Tool Integration

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

MCP provides standardized protocol for connecting agents to databases, APIs, file systems, and browsers. Supports stdio servers, OAuth authentication, tool filtering, and stateful sessions.

### Tool Call Interception

Cross-cutting concerns (logging, rate limiting, PII filtering) via `wrap_tool_call`:

```python
@wrap_tool_call
def audit_all_tools(request, handler):
    log.info("Tool: %s, Args: %s", request["name"], request["args"])
    result = handler(request)
    log.info("Result size: %d bytes", len(str(result)))
    return result
```

---

## Part 3: Token Economics & NFR Analysis

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

### Resource Limits

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `memory_limit` | 64 MB | Cap QuickJS heap memory per thread |
| `timeout` | 5.0 seconds | Per eval call execution time |
| `max_result_chars` | 4000 | Truncate result text returned to model |
| `max_ptc_calls` | 256 | Maximum tool calls per eval |
| `max_snapshot_bytes` | `memory_limit` | Drop snapshots larger than this |

### Backend Performance Characteristics

| Backend | Latency Profile | Persistence | Scalability |
|---------|----------------|-------------|-------------|
| StateBackend | In-process, fastest | Thread-scoped via checkpointer | Single thread |
| FilesystemBackend | Local disk I/O | Permanent | Single machine |
| StoreBackend | Depends on store impl | Cross-thread | Scales with store backend |
| ContextHubBackend | Network + cache | Hub commits | LangSmith-managed |
| Sandbox backends | Network roundtrip | Session-scoped | Provider-dependent |
| CompositeBackend | Route overhead + backend | Mixed | Route-dependent |

### Sandbox Cold Start Costs

| Provider | Isolation Tier | Cold Start | Max Concurrent |
|----------|---------------|------------|----------------|
| E2B | Firecracker microVM | Sub-second | API-managed |
| Modal | gVisor user-space kernel | Fast | 50K+ sessions |
| Daytona | Docker containers | 27-90ms | Provider-managed |
| LangSmith | Managed | Warm pools eliminate cold start | Managed |
| Vercel | Firecracker | Sub-second | Managed |

### Latency SLA Targets for Execution Operations

| Operation | p50 | p95 | p99 | Notes |
|-----------|-----|-----|-----|-------|
| Backend read (StateBackend) | <1ms | 2ms | 5ms | In-process memory access |
| Backend read (FilesystemBackend) | 1-5ms | 10ms | 25ms | Local disk I/O |
| Backend read (StoreBackend/Postgres) | 5-15ms | 30ms | 60ms | Network + query |
| Sandbox execute (warm) | 100-500ms | 1.5s | 3s | Network + execution |
| Sandbox execute (cold start) | 500ms-2s | 5s | 10s | Provision + execution |
| QuickJS eval (simple) | <5ms | 10ms | 20ms | In-process, no I/O |
| QuickJS eval with PTC | 50-500ms | 2s | 5s | Depends on tool latency |

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

### Availability, RPO & RTO Targets (Execution Layer)

| Target | Filesystem Ops | Sandbox Ops | Notes |
|--------|---------------|-------------|-------|
| **Availability** | 99.9% | 99.5% | Sandbox lower due to cold start variance and provider-dependent uptime |
| **RPO** | Backend-dependent | Session-scoped | In-memory = 0 durability; LocalDisk = OS-level; LangGraph Store = checkpoint-level |
| **RTO** | <30s | 2-5 min | Filesystem: reconnect/remount; Sandbox: full rebuild from template |

**Compliance mapping**:
- **HIPAA** (healthcare backends): Encrypt data at rest in FilesystemBackend and StoreBackend. DLP pre-processing before LLM ingestion. Audit every file read/write with user identity.
- **SOC 2**: Complete audit trail of all tool calls, file operations, and sandbox executions. PostgresSaver provides SQL-queryable evidence for auditors.
- **OWASP Agentic Top 10**: Tool Misuse (ASI02) mitigated by permission layer; Unexpected Code Execution (ASI05) mitigated by sandbox isolation tiers; Supply Chain (ASI04) mitigated by MCP tool filtering and PTC allowlists.

---

## Part 4: Distributed Resilience & Security

### Three-Tier Sandbox Isolation

This is the most interview-critical security concept in this module.

```
┌─────────────────────────────────────────────────────────────┐
│  TIER 3: MicroVMs (Firecracker) -- GOLD STANDARD           │
│  Each workload gets its own kernel on hardware              │
│  virtualization (KVM). Kernel exploit inside one VM         │
│  cannot reach host or other VMs.                            │
│  Boots in ~125ms, ~5MB memory overhead.                     │
│  Powers: AWS Lambda, E2B, Vercel Sandbox.                   │
├─────────────────────────────────────────────────────────────┤
│  TIER 2: User-Space Kernels (gVisor) -- MID-TIER           │
│  Intercepts and re-implements syscalls in user space.       │
│  Agent never talks to real kernel. Less overhead than VM.   │
│  Tradeoff: not all syscalls perfectly emulated.             │
│  Used by: Google Agent Sandbox (GKE), Modal.                │
├─────────────────────────────────────────────────────────────┤
│  TIER 1: Containers (Docker/runc) -- WEAKEST               │
│  Shared kernel. Kernel vulnerabilities allow escape.        │
│  Microsoft May 2026 CVE: prompt injection in Semantic       │
│  Kernel achieved host-level RCE via container escape.       │
│  Consensus: INSUFFICIENT for untrusted AI agent code.       │
└─────────────────────────────────────────────────────────────┘
```

### What Sandboxes Protect vs Do Not Protect

**Protects**: Host filesystem isolation, process isolation, resource boundaries (CPU, memory, disk).

**Does NOT protect against**:
- **Context injection**: Attacker controlling part of agent input can instruct it to run arbitrary commands *inside* the sandbox.
- **Network exfiltration**: Unless network is blocked, an injected agent can send data out via HTTP/DNS. Some providers support blocking: `modal.Sandbox.create(block_network=True)`.

### Credential Handling -- The Cardinal Rule

**Never put secrets inside a sandbox.** API keys, tokens, and database credentials can be read and exfiltrated by a context-injected agent.

**Safe approaches**:

```
Pattern 1: Tools Outside Sandbox (RECOMMENDED)
┌────────────────────┐     tool call     ┌──────────────────┐
│  Agent in Sandbox  │ ───────────────> │  Tool on Host    │
│  (no credentials)  │ <─────────────── │  (has API keys)  │
│                    │     result        │  (handles auth)  │
└────────────────────┘                   └──────────────────┘

Pattern 2: Auth Proxy with Credential Injection
┌────────────────────┐     HTTP request  ┌──────────────────┐     authed request
│  Agent in Sandbox  │ ───────────────> │  Auth Proxy      │ ───────────────>
│  (no credentials)  │ <─────────────── │  (injects creds) │ <───────────────
│                    │     response      │  (on host/edge)  │     response
└────────────────────┘                   └──────────────────┘
```

### Filesystem Permissions -- Deep Dive

**Declaration order, first-match-wins.** Three modes:

| Mode | Behavior | Requires |
|------|----------|----------|
| `allow` | Operation proceeds | Nothing |
| `deny` | Operation blocked, error returned | Nothing |
| `interrupt` | Execution pauses for human approval | Checkpointer configured |

**Default when no rule matches**: operations allowed (permissive). An empty permissions list allows everything.

**Critical scope limitation**: Permissions only cover the 8 built-in filesystem tools. They do NOT cover:
- Custom tools (your own functions)
- MCP tools (external servers)
- Sandbox `execute` (arbitrary shell commands bypass file permissions entirely)

**Directory deletion**: Checks write permission on target AND every descendant path. All-or-nothing -- refuses the entire operation if any descendant is denied.

**Sub-agent permissions**: Inherit parent by default. Explicit `permissions` in sub-agent spec *replaces* parent rules entirely -- this is a security design decision, not a bug.

### Authority Amplification -- The Confused Deputy Problem

LLM agents act as deputies that spend privileges on the user's behalf. Minor instruction-level deviations can trigger high-impact actions. OWASP Top 10 for Agentic Applications 2026 codifies this: Tool Misuse (ASI02), Supply Chain Vulnerabilities (ASI04), Unexpected Code Execution (ASI05).

**Enterprise mitigation stack** (see Module 08 for full detail): least-privilege tool whitelisting, separation of duties (Orchestrator/Reader/Actuator roles), data classification at infrastructure level, semantic firewalls (secondary model evaluating outputs), DLP/data masking before LLM, unified audit logging, prompt isolation (system prompts as locked vault).

### Zero-Trust Execution

Every execution-layer operation passes through the permission layer -- no implicit trust:
- **Tool call verification**: Each filesystem or sandbox operation is checked against permission rules before dispatch to the backend router. No tool call reaches a backend without explicit authorization.
- **Sandbox network isolation**: No egress by default. Allowlist specific internal endpoints only when required (e.g., internal git, package registry). DNS exfiltration is a real vector -- block it.
- **Credentials via auth proxy**: API keys and tokens are injected at the network boundary by an auth proxy running outside the sandbox. Never in agent context, never in sandbox environment variables.
- **Backend encryption at rest**: FilesystemBackend with `virtual_mode=True` stores content in checkpoint state -- encrypted if the checkpointer backend supports it (Postgres with TDE, DynamoDB with AWS KMS).

### Execution-Layer RBAC

Map filesystem permissions to organizational roles for the execution environment:

| Role | Tools Available | Path Access | Sandbox |
|------|----------------|-------------|---------|
| **Analyst** | `read_file`, `glob`, `grep` | Read-only `/workspace/**`, `/shared/**` | No |
| **Engineer** | `read_file`, `write_file`, `edit_file`, `glob`, `grep` | Read/write `/workspace/**` | No |
| **Admin** | All tools including `execute` | Full access | Yes, with network restrictions |
| **Auditor** | `grep`, `glob` | Read-only, all paths | No -- compliance checks only |

Enforce by constructing different `permissions` lists and `HarnessProfile.excluded_tools` sets per role at agent creation time.

### PII Pipeline for File Operations

Three-stage pipeline applied at the execution layer:

1. **Pre-write scanning**: Before `write_file` or `edit_file` commits content to any backend, scan the content for PII (regex for structured patterns + NER for unstructured). Block or redact before write completes.
2. **Post-read filtering**: After `read_file` returns content from a backend, scan for PII before injecting into the agent's context window. Redact with typed placeholders (`[PII-SSN-1]`, `[PII-EMAIL-2]`) to prevent PII from entering model memory or checkpoint state.
3. **Audit log**: Every file operation logged with: user identity, file path, operation type (read/write/edit/delete), PII detection result (detected/clean), entity types found, action taken (passed/redacted/blocked), timestamp. Queryable via LangSmith traces or direct SQL on the checkpoint database.

### QuickJS Security Boundary

Strict default isolation -- no access to host filesystem, network, shell, package manager, or wall clock.

| Capability | Default | How to Expose |
|------------|---------|---------------|
| JavaScript execution | Yes | Add interpreter middleware |
| Top-level await | Yes | Use promises |
| Console capture | Yes | Disable with `capture_console=False` |
| Agent tools | No | Add PTC allowlist |
| Filesystem | No | Add built-in filesystem tools via PTC |
| Network | No | Expose specific network tool via PTC |
| Shell/packages/OS | No | Use sandbox backend instead |

**Security note**: QuickJS runs in embedded context via `quickjs-rs`, not a separate VM or process. For untrusted code, run agents in isolated worker processes with narrow PTC allowlists.

**PTC interrupt gap**: PTC calls inside QuickJS do not enforce `interrupt_on` approval workflows per tool call. Tool calls from the interpreter bypass HITL. Mitigation: careful PTC allowlist curation -- never expose high-risk tools via PTC.

### Execution-Layer Failure Taxonomy

| Category | Example | Detection | Response |
|----------|---------|-----------|----------|
| **Transient** | Sandbox timeout, filesystem lock contention | Timeout/retry-status codes | Retry with exponential backoff |
| **Permanent** | Backend corruption, permission denied, invalid path | Error result with diagnostic | Fail immediately, surface error to agent for reasoning |
| **Adversarial** | Path traversal attempt (`../../etc/passwd`), code injection via interpreter | Pattern matching in permission layer, audit log anomaly detection | Block operation + alert security team + log full trace |

### Circuit Breaker for Sandbox Operations

When sandbox operations fail repeatedly, continuing to retry wastes tokens and latency:

```
State machine:
  CLOSED (normal) ──3 consecutive execute failures──> OPEN (reject all)
  OPEN ──wait 60s──> HALF-OPEN (probe)
  HALF-OPEN ──probe succeeds──> CLOSED
  HALF-OPEN ──probe fails──> OPEN (reset timer)

Probe command: lightweight diagnostic (e.g., `echo ok` or `python -c "print('healthy')"`)
```

Implement as custom middleware wrapping `execute` tool calls. Track failure count in graph state (not `self` -- concurrent sub-agents would race).

### Retry with Exponential Backoff

For transient backend failures (filesystem lock, store timeout, sandbox cold start):

- **Max retries**: 3
- **Backoff**: Exponential with jitter -- `delay = min(base * 2^attempt + random(0, base), max_delay)`
- **Base delay**: 500ms, **max delay**: 10s
- **Dead-letter**: After exhaustion, log the failed operation with full context (trace_id, tool_call, args, error) and return a structured error to the agent. Do not silently swallow.
- **Idempotency**: `write_file` and `edit_file` are naturally idempotent (same content = same result). `execute` is NOT -- retrying a shell command may duplicate side effects. Use the circuit breaker pattern for `execute` instead of blind retry.

---

## Part 5: Production Enterprise Code

### Complete Execution Environment Setup

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

# ---------------------------------------------------------------------------
# 1. Sandbox backend -- Firecracker microVM via E2B
# ---------------------------------------------------------------------------
sandbox_backend = E2BSandboxBackend(
    api_key="e2b_api_key_from_vault",  # from secrets manager, never hardcoded
    template="python-3.12",
    idle_ttl_seconds=3600,             # reclaim after 1 hour idle
    timeout=120,                       # 2-minute command timeout
    max_output_bytes=100_000,          # truncate large outputs
)

# ---------------------------------------------------------------------------
# 2. CompositeBackend -- route user workspace to sandbox, memories to store
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 3. Permissions -- defense in depth
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 4. Interpreter -- QuickJS with PTC for batched operations
# ---------------------------------------------------------------------------
interpreter_mw = InterpreterMiddleware(
    mode="thread",              # persist state across turns
    memory_limit=64 * 1024 * 1024,  # 64 MB heap
    timeout=5.0,                # 5 seconds per eval
    max_result_chars=4000,      # truncate results
    max_ptc_calls=256,          # cap tool calls per eval
    ptc_allowlist=[             # only expose safe tools
        "read_file",
        "grep",
        "glob",
        "search_docs",         # custom read-only tool
    ],
    # NOTE: do NOT add write_file, delete, or execute to ptc_allowlist
    # PTC calls bypass HITL interrupt_on checks
)

# ---------------------------------------------------------------------------
# 5. Custom tools (run on host, NOT inside sandbox)
# ---------------------------------------------------------------------------
def search_docs(query: str, collection: str = "default") -> dict[str, Any]:
    """Search documentation collection. Runs on host with auth."""
    return {
        "results": [
            {
                "title": f"Result for '{query}'",
                "path": f"/shared/docs/{collection}/result.md",
                "score": 0.89,
                "snippet": "Relevant documentation excerpt...",
            }
        ],
        "total": 1,
    }


def deploy_service(
    service_name: str, version: str, environment: str
) -> dict[str, str]:
    """Deploy a service to the specified environment. Requires human approval."""
    if environment not in ("staging", "production"):
        return {"error": f"Unknown environment: {environment}"}
    return {
        "status": "deployed",
        "service": service_name,
        "version": version,
        "environment": environment,
        "url": f"https://{service_name}.{environment}.internal",
    }


# ---------------------------------------------------------------------------
# 6. Assemble the agent
# ---------------------------------------------------------------------------
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_docs, deploy_service],
    system_prompt=(
        "You are a senior DevOps engineer assistant. You can search documentation, "
        "write and run code in a sandboxed environment, and deploy services. "
        "Always test code in the sandbox before recommending deployment. "
        "Never store credentials in files."
    ),
    middleware=[
        SummarizationMiddleware(trigger=("tokens", 80_000), retention=("messages", 15)),
        ToolCallLimitMiddleware(max_calls=150),
        interpreter_mw,
    ],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["deploy_service"]},  # HITL for deployments
    checkpointer=checkpointer,
    store=store,
)


# ---------------------------------------------------------------------------
# 7. File seeding -- upload files before agent starts
# ---------------------------------------------------------------------------
def seed_workspace(thread_id: str, files: dict[str, bytes]) -> None:
    """Seed the sandbox with initial files using provider's native API."""
    sandbox_backend.upload_files(
        thread_id=thread_id,
        files=files,
    )


def retrieve_results(thread_id: str, paths: list[str]) -> dict[str, bytes]:
    """Retrieve result files after agent completes."""
    return sandbox_backend.download_files(
        thread_id=thread_id,
        paths=paths,
    )


# ---------------------------------------------------------------------------
# 8. Invocation with lifecycle management
# ---------------------------------------------------------------------------
def run_code_review(
    user_id: str,
    thread_id: str,
    pr_diff: str,
    repo_docs: dict[str, bytes],
) -> str:
    """Run an AI code review with sandbox execution."""
    # Seed workspace with PR files
    seed_workspace(thread_id, repo_docs)

    config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": user_id,
        }
    }

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Review this PR diff. Run the test suite in /workspace/ "
                        f"to check for regressions. Provide a structured review.\n\n"
                        f"```diff\n{pr_diff}\n```"
                    ),
                }
            ]
        },
        config=config,
    )

    return result["messages"][-1].content


if __name__ == "__main__":
    review = run_code_review(
        user_id="dev-42",
        thread_id="pr-review-789",
        pr_diff="- old_function()\n+ new_function()",
        repo_docs={
            "test_main.py": b"import pytest\ndef test_basic(): assert True\n",
            "main.py": b"def new_function(): return 42\n",
        },
    )
    print(review)
```

### Custom Backend Implementation

```python
"""
Custom backend wrapping S3. Shows the BackendProtocol contract.
Key rule: return structured dicts with 'error' field -- never raise exceptions.
"""

from typing import Any
import boto3


class S3Backend:
    """BackendProtocol implementation backed by AWS S3."""

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
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        return self.write(file_path, updated)

    # ls, glob, grep, delete follow the same pattern:
    # return {"entries"|"matches": [...], "error": ""} on success,
    # return {"error": "description"} on failure.
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Multi-Tenant Data Analysis Platform

**Problem Statement**: A fintech company wants to offer AI-assisted data analysis to 200 enterprise clients. Each client uploads CSV/Parquet datasets (up to 500MB). The agent must load data, run Python analysis, generate visualizations, and return results. Client data must be strictly isolated. Analysts may upload proprietary models. The system must handle 500 concurrent analysis sessions.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    API Gateway (JWT + tenant ID)                │
└───────────────────────────┬─────────────────────────────────────┘
                            v
┌─────────────────────────────────────────────────────────────────┐
│                    LangGraph Cloud (K8s)                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Supervisor Agent (claude-sonnet-4-6)                     │  │
│  │  Plans analysis strategy, delegates to workers            │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Worker: data_analyst (claude-haiku-4)                    │  │
│  │    Sandbox: Modal (gVisor) -- Python + pandas + plotly    │  │
│  │    GPU: available for ML model inference                  │  │
│  │    Network: blocked except internal APIs                  │  │
│  │                                                           │  │
│  │  Worker: report_writer (claude-sonnet-4-6)                │  │
│  │    No sandbox, read-only filesystem access                │  │
│  │    Synthesizes findings into structured report            │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  CompositeBackend                                         │  │
│  │    /data/     -> S3Backend(bucket=tenant_bucket)           │  │
│  │    /results/  -> StoreBackend(ns=(tenant_id, thread_id))  │  │
│  │    /shared/   -> FilesystemBackend(templates, virtual)     │  │
│  │    (default)  -> StateBackend()                            │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Interpreter: QuickJS with PTC                            │  │
│  │    Allowlist: [read_file, glob, grep]                     │  │
│  │    Batches data exploration before heavy sandbox compute  │  │
│  └───────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  PostgresSaver (checkpoints)  │  Warm sandbox pool (Modal)     │
│  DynamoDBSaver (AWS option)   │  50K+ concurrent sessions      │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Sandbox provider | E2B (Firecracker) | Modal (gVisor) | Modal | GPU support needed for client ML models; gVisor isolation is mid-tier but sufficient since code is semi-trusted (uploaded by paying clients, not adversaries) |
| Data storage | FilesystemBackend with mounted volumes | Custom S3Backend via CompositeBackend | S3Backend | 500MB datasets on local disk do not scale to 500 concurrent sessions; S3 provides tenant isolation via bucket policies |
| Credential handling | Inject S3 credentials into sandbox | Auth proxy pattern | Auth proxy | Client datasets may contain proprietary models; credentials inside sandbox + prompt injection = data exfiltration |
| Interpreter usage | Skip (everything in sandbox) | QuickJS for exploration, sandbox for compute | B | Exploring a dataset (column names, shapes, null counts) should not require sandbox cold start; interpreter handles exploration, sandbox handles heavy compute |
| Checkpoint backend | PostgresSaver | DynamoDBSaver | Depends on cloud | AWS-native shops use DynamoDB (auto-scaling, no ops); others use Postgres (SQL queryability for compliance) |

**Decision Rationale**: Modal is chosen over E2B because GPU support is a hard requirement for client ML model inference -- E2B does not support GPUs. The gVisor isolation tier is acceptable because the code being executed comes from paying enterprise clients (semi-trusted), not arbitrary public input. The CompositeBackend with a custom S3Backend handles the scale requirement: 500MB datasets cannot live on local disk across 500 concurrent sessions. QuickJS with PTC handles the "exploration phase" (what columns exist, what does the distribution look like) without sandbox overhead, then the sandbox handles the "compute phase" (run the analysis, generate plots). This two-phase pattern reduces sandbox usage by an estimated 40-60%, directly reducing compute cost.

---

### Scenario 2: Regulated Document Processing Pipeline (Healthcare)

**Problem Statement**: A healthcare organization needs AI agents to process clinical trial documents: extract data points, cross-reference with regulatory databases, generate compliance reports, and flag anomalies. Documents contain PHI (Protected Health Information) subject to HIPAA. The system must provide a complete audit trail. Processing must complete within 10 minutes per document batch. No data may leave the organization's VPC.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│              Internal Load Balancer (VPC-only)                  │
└───────────────────────────┬─────────────────────────────────────┘
                            v
┌─────────────────────────────────────────────────────────────────┐
│               Self-Hosted LangGraph (EKS, VPC)                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Orchestrator Agent (claude-sonnet-4-6, self-hosted)      │  │
│  │  System prompt: locked vault, no user override            │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Phase 1: Extraction                                      │  │
│  │    Worker: doc_parser (claude-haiku-4)                     │  │
│  │    Backend: FilesystemBackend(virtual_mode=True)           │  │
│  │    Input: de-identified documents (DLP pre-processing)    │  │
│  │    Output: structured data to /extracted/                  │  │
│  │                                                           │  │
│  │  Phase 2: Cross-Reference                                 │  │
│  │    Worker: reg_checker (claude-haiku-4)                    │  │
│  │    MCP server: internal regulatory database               │  │
│  │    No filesystem write access                             │  │
│  │                                                           │  │
│  │  Phase 3: Report Generation                               │  │
│  │    Worker: report_gen (claude-sonnet-4-6)                  │  │
│  │    HITL: interrupt before writing final compliance report  │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Semantic Firewall                                        │  │
│  │    Secondary model (haiku) evaluating all outputs         │  │
│  │    Checks: PHI leakage, hallucinated citations,           │  │
│  │    regulatory claim accuracy                              │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Permissions                                              │  │
│  │    /raw_docs/**     -> read-only (deny write)             │  │
│  │    /extracted/**    -> allow (phase 1 output)             │  │
│  │    /reports/**      -> interrupt (human approval)         │  │
│  │    /**              -> deny (catch-all)                    │  │
│  └───────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  DLP Pre-Processor     │  PostgresSaver    │  Audit Logger     │
│  (de-identify PHI      │  (checkpoints     │  (every tool call │
│   before LLM sees it)  │   for recovery)   │   + model call)   │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Deployment | LangGraph Cloud (managed) | Self-hosted on EKS in VPC | B | HIPAA: PHI cannot leave the org's VPC. Managed service would route data externally |
| PHI handling | Let LLM see raw PHI | DLP pre-processing to de-identify before LLM | B | Even with self-hosted LLM, de-identification is defense-in-depth. Reduces breach scope if model outputs are logged |
| Sandbox | E2B/Modal (external) | No sandbox (FilesystemBackend, virtual_mode=True) | B | No external sandbox providers -- data cannot leave VPC. Local filesystem with virtual mode is sufficient since code execution is not needed for document processing |
| Output validation | Trust model outputs | Semantic firewall (secondary model) | B | Healthcare compliance requires verifiable outputs. Secondary haiku model checks for PHI leakage, hallucinated citations, and regulatory claim accuracy |
| Checkpoint granularity | Every node | Phase boundaries only | Phase boundaries | 10-minute SLA with 3 phases: checkpoint after each phase. Full-node checkpointing adds ~15% overhead for no recovery benefit within a phase |

**Decision Rationale**: The architecture is driven by two non-negotiable constraints: HIPAA compliance (no data leaves VPC) and auditability (complete trail of every model and tool interaction). Self-hosting eliminates external data flow. DLP pre-processing removes PHI before any text reaches the LLM -- this is defense-in-depth, not the sole control. The semantic firewall (a secondary constrained model evaluating all outputs) catches PHI leakage, hallucinated citations, and inaccurate regulatory claims before they reach human reviewers. Phase-boundary checkpointing (after extraction, after cross-reference, after report generation) provides recovery without the overhead of per-node checkpointing. The 10-minute SLA is met by parallelizing document parsing across sub-agents (multiple doc_parser workers processing different documents simultaneously), then sequential cross-referencing and report generation.

---

## Backend Selection Decision Tree

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

## Production Readiness Checklist (Execution-Specific)

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

---

**Sources**: LangChain Deep Agents docs (tools, backends, sandboxes, interpreters, permissions), E2B/Modal/Daytona sandbox docs, OWASP Top 10 Agentic 2026, NSA/CISA agentic AI adoption guidance, Microsoft Semantic Kernel CVE disclosures, Zylos Research (durable execution), AWS DynamoDB agent blog.
