# Module 12: Deep Agents Steering (`interrupt_on`, HITL, permission `mode="interrupt"`)

**Study + interview prep.** Grounded in research dated 2026-09-02 (48 sources). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01). This file is the **steering bucket** of the harness (alongside execution, context, and delegation): human control at **runtime** via `interrupt_on` → `HumanInTheLoopMiddleware`, LangGraph `interrupt()` / `Command(resume=...)`, and filesystem `permissions=` `mode="interrupt"`. Backend catalogs live in [09-deep-agents-execution](09-deep-agents-execution.md). Zero-Trust MCP (OAuth 2.1 / RFC 8707 / hash-pins / gateway PEP) lives in [07-guardrails](07-guardrails.md) / [09](09-deep-agents-execution.md) — cited here only where HITL sits **next to** that PEP, not recopied. `interrupt_on` as a review queue is sketched in [08-deep-agents-harness](08-deep-agents-harness.md) §4.1; HITL TOCTOU/fatigue taxonomy in [07-guardrails](07-guardrails.md) §3.4; child inheritance in [11-deep-agents-delegation](11-deep-agents-delegation.md) §4.2. `$ per 1k runs` is **[inferred]** from Anthropic list prices already used in [08](08-deep-agents-harness.md) §2.3 × a stated run shape, not a SKU. LangChain / Deep Agents / LangSmith publish **no** p50/p95/p99 of time-to-approve — missing percentiles are architecture-derived **[inferred] policy** and are marked.

**Thesis:** HITL is a **pause**, not a policy decision point. `HumanInTheLoopMiddleware` batches proposed tool calls, persists graph state, and waits for a human `HITLResponse`. It does not authenticate the approver, does not evaluate `(principal, action, resource)`, does not bind a hash of executed args, does not time out, and does not cover MCP / `execute` / custom tools unless you **name** them in `interrupt_on`. Filesystem `permissions=` is a **first-match, fail-open path PDP for built-in FS tools only**. Neither is Zero-Trust. The model still proposes; code (middleware + checkpointer + **your** resume handler) disposes.

| Gate | Version |
| --- | --- |
| `permissions=` | `deepagents>=0.5.2` |
| Inherit parent `interrupt_on` on declarative specs (not only GP) | PR #2334 |
| `mode="interrupt"` | `>=0.6.8` |
| `when` on `InterruptOnConfig` | `langchain>=1.3.3` (`when` is **Python-only** on the JS HITL page) |
| Exact-match file `delete` | `>=0.7.3` |
| `delete` tool at all | `>=0.7` |
| `when` required by `create_deep_agent` for permission-generated interrupts | pin `langchain` accordingly |

---

## What Is This?

**Steering is two planes on one Pregel runtime.** Deep Agents does not add a scheduler. It wires LangChain `HumanInTheLoopMiddleware` onto LangGraph interrupts. `interrupt_on` is a dict: tools you **name** pause; tools you **omit** auto-approve (fail-open). `True` means interrupt with all four decisions; `False` is explicit auto-approve (useful when inheriting a parent map). `InterruptOnConfig` customizes `allowed_decisions`, `description`, `args_schema`, and a `when` predicate. Filesystem `permissions=` `mode="interrupt"` synthesizes those configs for built-in FS tools whose paths match — still fail-open for unmatched paths, still not MCP/`execute`.

**Checkpointer is required.** Without one, `interrupt()` has nowhere to write. Examples use `MemorySaver` / `InMemorySaver` (RAM: process restart drops every paused HITL). Production: `AsyncPostgresSaver` / `PostgresSaver` / `MongoDBSaver`. Agent Server / Managed Deep Agents provision checkpointer + store for you. `thread_id` is the persistent cursor (Postgres column **< 255** chars; Deep Agents examples use `uuid7()`). LangGraph **waits indefinitely**. There is **no** product TTL, no expire-deny, no expire-approve, no Slack notification. OWASP AISVS C9.2: checkpoint/interrupt is not an approval workflow. **Expire-deny is your code** — an external timer that resumes `Command(resume={"decisions": [{"type": "reject", "message": "expired"}]})`. Never expire-approve.

Think of a loading dock, not a courthouse. HITL is the **hold** on a pallet the model already labeled. The warehouse still needs a badge reader (IdP), a shipping policy (PDP / gateway PEP), and a sealed crate (sandbox). A human clicking “Approve” on a LangGraph card does not mint an audience-bound token and does not replace RFC 8707.

## Why It Matters

Almost every “human-in-the-loop agent platform” interview now forks here: is HITL authorization, or a durable pause with a review UI? Trap answers: “unnamed tools are denied,” “`permissions=` covers MCP,” “LangGraph times out the card,” “compiled children inherit `interrupt_on`,” “`interrupt_on={"task": True}` catches interpreter `task()`,” “`respond` is how I reject a send,” “HITL timeout auto-approves so the user isn’t blocked.”

Anthropic’s coding-agent analog (not a Deep Agents SKU): users approve **~93%** of permission prompts; sandboxing cut prompts **84%**; auto-mode classifier on real overeager n=52: **17% FNR**; traffic n=10,000: **0.4% FPR** after two stages. That is the cost of **not** using sandbox + policy: either human FTE (fatigue) or a second model (residual miss). Deep Agents has **no** built-in auto-mode. `deepagents_code.AutoModeHITLMiddleware` exists in the Code package — not in `create_deep_agent` defaults. Do not quote Code-package timeouts as harness SLOs.

---

### 1. System Topology & Data Flow

Steering sits on the same `CompiledStateGraph` as [08](08-deep-agents-harness.md). Construction / `interrupt_on` / `permissions` / checkpointer / resume authz are **control** (LLM-free for pause/resume routing; `when` predicates are **your** code). Proposed `tool_call` name/args/id, `HITLRequest.action_requests`, edited args, and `respond` bodies are **data** (untrusted model-authored tokens plus whatever the human types). Persistence **is** the HITL database: the checkpointer row keyed by `thread_id`. Tool proxies are `FilesystemMiddleware.wrap_tool_call` (deny still binds after edit), MCP adapters (HITL can name them; `permissions=` cannot see them), and `execute` (outside the FS PDP). Telemetry is LangSmith traces plus a WORM decision log **you** write — the runtime does not mint signed receipts or approver ids.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  LangSmith traces (interrupt payloads if you log them)           │
         │  GraphOutput.interrupts / stream.interrupted  (version v2 / v3)  │
         │  get_state_history / checkpoint_id  (replay re-triggers HITL)    │
         │  WORM you build: (cid, thread_id, interrupt_id, tool,            │
         │    args_digest, decision, actor_id, ts, policy_version)          │
         │  Queue metrics: depth, time-to-approve, expire-deny count        │
         │  PII audit: detect→redact→audit on HITL UI + checkpoint args     │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ queue            │ decision log
                      │                     │                  │
┌─────────────────────┴─────────────────────┴──────────────────┴────────────┐
│ CONTROL PLANE  (LLM-free routing; identity is NOT here unless you add it) │
│                                                                           │
│  interrupt_on: dict[str, bool | InterruptOnConfig]   (unnamed = approve)  │
│  permissions= FilesystemPermission(ops, paths, mode=allow|deny|interrupt) │
│  _merge_fs_interrupt_on(fs, user)  — user wins PER TOOL NAME              │
│  HumanInTheLoopMiddleware  (tail slot 14; auto-install if mode=interrupt) │
│  when: Callable[[ToolCallRequest], bool]   allowed_decisions non-empty    │
│  checkpointer + thread_id (<255)   Command(resume={"decisions": [...]})   │
│  YOUR expire-deny timer / CAS ticket / resume RBAC  (not in the library)  │
│  PatchToolCallsMiddleware ALWAYS before HITL (dangling tool_calls repair) │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ interrupt() raises; graph waits forever
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted — model proposed the call; human/code dispose)     │
│                                                                           │
│  AIMessage.tool_calls → after_model filter → HITLRequest                  │
│    action_requests[] = {name, args copy, description}  (display snapshot) │
│    review_configs[]  = {action_name, allowed_decisions, args_schema?}     │
│  Resume: approve|edit keep ToolCall; reject|respond inject ToolMessage    │
│  Execution uses in-memory ToolCall (or edited replacement), NOT a         │
│    re-parse of the interrupt payload. Tokens for that model turn are sunk.│
│                                                                           │
│  ┌────────────── TOOL PROXIES (HITL is optional extra, not the PEP) ────┐ │
│  │ Built-in FS: ls read_file write_file edit_file glob grep delete      │ │
│  │   wrap_tool_call: deny still binds AFTER human edit                  │ │
│  │ execute / sandbox: permissions= CANNOT constrain; name in interrupt_ │ │
│  │   on or omit HITL if sandbox is the PDP                              │ │
│  │ MCP / custom on tools=: name in interrupt_on to pause; gateway PEP   │ │
│  │   still required (OAuth 2.1 + RFC 8707). permissions= does NOT apply │ │
│  │ Interpreter task() / PTC tools.*: skip parent interrupt_on — gate    │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (the HITL database — no product TTL)                   │
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ Checkpointer │ │ thread_id    │ │ Durability  │ │ Approval ticket  │  │
│  │ Postgres /   │ │ uuid7()      │ │ exit|async| │ │ (YOUR CAS; not   │  │
│  │ Mongo /      │ │ < 255 chars  │ │ sync        │ │  in LangGraph)   │  │
│  │ MemorySaver  │ │ reuse=resume │ │ interrupt = │ │ TTL job sends    │  │
│  │ = RAM, gone  │ │ new id=empty │ │ an "exit"   │ │ reject Command   │  │
│  └──────────────┘ └──────────────┘ └─────────────┘ └──────────────────┘  │
│  Nested declarative children inherit parent checkpointer. Async children  │
│  = second interrupt domain on the remote thread. Retention cron MUST NOT  │
│  delete threads with next waiting on interrupt.                           │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `interrupt_on`, `permissions` (incl. `mode="interrupt"`), HITL middleware assembly, checkpointer / `thread_id`, `Command(resume=...)`, `when`, `allowed_decisions`, your TTL + resume RBAC | **Yes** for pause/resume routing. Predicates are your code. Approver identity is **not** in this plane unless you add it | Treating HITL as the PDP; putting allow/deny only in the system prompt |
| **Data** | Proposed `tool_call` name/args/id, `HITLRequest.action_requests` (args copied into the interrupt payload), edited `args` on resume, rejection `message`, `respond` synthetic `ToolMessage` body | No — untrusted model-authored tokens, plus whatever the human types | Showing raw PII on the card; executing the display snapshot instead of the in-memory `ToolCall` without a hash bind |

`create_deep_agent` merges filesystem-generated interrupt configs with caller `interrupt_on` (`_merge_fs_interrupt_on`: user entries **win per tool name**). If the merge is non-empty, it appends `HumanInTheLoopMiddleware` as the last tail slot (after Memory / caching — slot **14** in [08](08-deep-agents-harness.md)). Permission `mode="interrupt"` auto-installs the same middleware even when `interrupt_on=None`.

**Request-flow narrative (model `tool_calls` → HITL batch → `Command` resume → execute):**

1. **Model turn (data, tokens already spent).** The model returns an `AIMessage` with `tool_calls`. HITL fires in `after_model`, **after** that completion. The tool has **not** run. Human think-time burns **$0 of tokens** and **100% of wall-clock SLO**.
2. **Filter (control).** `HumanInTheLoopMiddleware.after_model` inspects each call. Tools **absent from the map are auto-approved**. Tools present as `False` are auto-approved. Tools present as `True` / `InterruptOnConfig` are candidates; a `when` predicate returning `False` **auto-approves and excludes the call from the batch**. There is no HITL audit event for skipped calls — they never appear in `action_requests`.
3. **Batch interrupt.** Remaining candidates become one `HITLRequest` (`action_requests` + `review_configs`). `interrupt(hitl_request)` raises into the runtime. The checkpointer writes the thread (messages including the `AIMessage` with pending `tool_calls`). The graph **waits indefinitely**. API returns `GraphOutput.interrupts` when `invoke(..., version="v2")` (default `invoke` surfaces `result["__interrupt__"]`; streaming uses `stream_events(..., version="v3")` → `stream.interrupted` / `stream.interrupts`). Deep Agents samples **highlight `version="v2"`** — forgetting it is a UI bug.
4. **Human / queue (your plane).** UI or Slack shows cards. Args in `action_requests` are a **copy for display**. `review_configs` is keyed by `action_name` (tool name), not by tool-call id — two parallel `write_file` calls share one review config; decisions are still **positional** on `action_requests`. Do not zip by name. Default description embeds **full tool args** (`Args: {tool_args}`).
5. **Resume (control).** Same `thread_id`. **Must** be `Command(resume={"decisions": [...]})`. `Command(update=...)` / `goto` are for returning from **nodes**, not for driving `invoke` on a paused HITL. Passing a new input dict is a **new invocation**, not a resume. Order **must** match `action_requests`; length mismatch → `ValueError`.
6. **Apply decisions.** `approve` keeps the original `ToolCall` (identity preserved). `edit` substitutes **new** name+args with the **same** `tool_call["id"]`. `reject` does **not** run the tool; synthetic `ToolMessage` with status `"error"`. `respond` does **not** run the tool; synthetic body with status `"success"` — the model treats it as a successful result. **Do not** use `respond` to deny.
7. **Execute.** `ToolNode` runs remaining calls. `FilesystemMiddleware.wrap_tool_call` still runs — deny-mode permissions **re-check** edited args on the way into the FS tool. MCP/custom tools have **no** such backstop unless your tool or gateway re-validates. `when` does **not** re-run on edited args inside `_process_decision`.

The **paused object** is the tool call the model already emitted. Significant `edit`s can make the model re-evaluate and **call the tool again**.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (pause, not PDP)

**I1.** HITL is a **review queue** over **named** tools. Unnamed tools auto-approve. `permissions=` is a path PDP for **built-in FS tools only**, first-match, **no match ⇒ allow**. Neither authenticates a principal. Neither is Zero-Trust.

**I2.** Checkpointer + `thread_id` are the HITL substrate. `MemorySaver` = RAM. LangGraph waits **forever** unless **you** expire-deny.

**I3.** `after_model` is **batch**. One human round-trip per model turn that has ≥1 gated call. PR #37579 discussed `interrupt_mode` batch vs per-call; shipped `__init__` takes only `interrupt_on` + `description_prefix`. The `when` docstring mentions `"batch"` / `"per_call"`; Deep Agents steering uses the batch path.

**I4.** User `interrupt_on` **wins per tool name** over permission-generated configs. `{"write_file": False}` disables `/secrets/**` interrupts. `{"write_file": True}` **drops the `when` predicate** — every write pauses.

**I5.** Declarative children **inherit** parent `interrupt_on` / `permissions` (spec **replaces** entirely if set; PR #2334 fixed GP-only inheritance). `CompiledSubAgent` / `AsyncSubAgent` **do not inherit**. Interpreter `task()` inside `eval` does **not** enforce parent `interrupt_on` per dispatch — gate `eval`.

**I6.** Side effects **before** `interrupt()` in a node re-run on resume (the entire graph node restarts). HITL middleware keeps the interrupt as the first effect. Bare `except Exception` swallowing the interrupt exception → **no pause, execution continues**.

#### 2.2 `interrupt_on` mapping

Type: `dict[str, bool | InterruptOnConfig]`.

| Value | Meaning |
| --- | --- |
| **Omitted key** | Auto-approve. HITL is **fail-open** for unnamed tools |
| `True` | Interrupt. Default `allowed_decisions = ["approve", "edit", "reject", "respond"]` |
| `False` | Explicit auto-approve (disable one tool when inheriting a parent map) |
| `InterruptOnConfig` | Custom. **Required:** non-empty `allowed_decisions`. Optional: `description` (str or factory), `args_schema`, `when` |

Empty / missing `allowed_decisions` on a dict config raises `ValueError` at construction — the middleware refuses to silently drop the gate. `description_prefix` default on the middleware is `"Tool execution requires approval"`; Deep Agents docs’ LangChain sibling uses `"Tool execution pending approval"`. Unused if the tool’s config supplies `description`. Factories run **before** `interrupt()`, producing a string — functions in a `when` closure are **not** in the payload.

Official Deep Agents example:

```python
interrupt_on={
    "remove_file": True,  # all four decisions
    "fetch_file": False,  # never pause
    "notify_email": {"allowed_decisions": ["approve", "reject"]},  # no edit, no respond
}
```

Risk-tiered pattern from the same page:

| Risk | Pattern |
| --- | --- |
| High (delete, send_email) | `["approve", "edit", "reject"]` |
| Medium (write_file) | `["approve", "reject"]` — no silent arg rewrite |
| Must-run (rare) | `["approve"]` only — human cannot reject **in-band**. TTL job must send an **allowed** type or you must allow `reject` for timeouts (sending `reject` when it is not allowed → `ValueError`; thread sits forever) |
| Low (read_file, ls) | `False` |

#### 2.3 Four decisions (approve / edit / reject / respond)

`DecisionType = Literal["approve", "edit", "reject", "respond"]`. Resume payload:

```python
Command(resume={"decisions": [
    {"type": "approve"},
    {"type": "edit", "edited_action": {"name": "...", "args": {...}}},
    {"type": "reject", "message": "..."},   # message optional
    {"type": "respond", "message": "..."},  # message required
]})
```

| Decision | Tool runs? | `ToolMessage` | Status | Use |
| --- | --- | --- | --- | --- |
| `approve` | Yes, original args | None from HITL; real tool result later | — | Send as drafted |
| `edit` | Yes, **new** name+args; **same** `tool_call["id"]` | None from HITL | — | Change recipient; keep id so the pending call is satisfied |
| `reject` | **No** | Synthetic; default text says do not retry unless user asks | `"error"` | Deny side effects. For destructive tools, pass a domain-specific `message` |
| `respond` | **No** | Synthetic with human `message` | `"success"` | Human **is** the tool (`ask_user`). **Do not** use to deny — the model treats it as success |

`edited_action.name` may differ from the original — `edit` is **tool-renaming power**. Restrict `edit` on irreversible tools; validate `edited_action.name == action.name` in your resume handler **[inferred]** — middleware does not. Schema enforcement is the tool’s JSON schema, not HITL; a human can Approve a hallucinated recipient. `args_schema` on `InterruptOnConfig` exists for UI editors — populate it from the tool schema; middleware comment says “eventually can get tool information.”

#### 2.4 `when` predicate

Requires `langchain>=1.3.3`. Signature: `Callable[[ToolCallRequest], bool]`. `True` → interrupt; `False` → **auto-approve, never enter the batch**.

`ToolCallRequest` at `after_model` (batch): `tool_call` dict, `state`, `runtime` as a constructed `ToolRuntime` with `tool_call_id`; **`tool=None`** (no `BaseTool` instance at this hook). Do not write predicates that need `request.tool`.

Official example: pause `write_file` only when `file_path` does not start with `/workspace/`. LangChain sibling: pause SQL unless `query` is `SELECT`. FS write tools use **`file_path`**, not `path` — a copy-paste `path` silently auto-approves every write.

**Security of `when`:** a buggy predicate that returns `False` on a destructive path is a **silent auto-approve**. Treat `when` as part of the PDP you own, not as “the human will see everything.” Exception inside `when`: `_should_interrupt` calls `when(req)` without a documented try/except — exception fails the node, **not** fail-closed to interrupt. **[inferred]** wrap predicates. `create_deep_agent` **requires** a `when`-capable langchain when generating permission interrupts; old langchain + `mode="interrupt"` is a construction/runtime failure, not a silent deny.

#### 2.5 Permission `mode="interrupt"` vs `interrupt_on`

`FilesystemPermission(operations, paths, mode)`:

| Field | Values |
| --- | --- |
| `operations` | `"read"` → `ls`, `read_file`, `glob`, `grep`. `"write"` → `write_file`, `edit_file`, `delete` |
| `paths` | Globs, `**`, `{a,b}` alternation |
| `mode` | `"allow"` (default), `"deny"`, `"interrupt"` (`>=0.6.8`) |

**Evaluation:** declaration order, **first match wins**. **No match ⇒ allow** (fail-open) — the opposite of IAM default-deny. Coverage (official): built-in FS tools only. **Not** custom tools, **not** MCP tools, **not** sandbox `execute`, **not** direct `backend.*`. Composite + sandbox default: permission paths must sit under a **known route**; otherwise `NotImplementedError` at construction — path rules cannot constrain shell.

`FilesystemMiddleware` enforces deny (pre-check + post-filter on `ls`/`glob`/`grep` artifacts). It does **not** know about HITL. Glue is `_build_interrupt_on_from_permissions` in `_fs_interrupt.py`. `mode="interrupt"` synthesizes `InterruptOnConfig` with `allowed_decisions=["approve","edit","reject","respond"]` plus a **`when`** that fires only on matching paths.

| Tool | Scope | Interrupt fires when |
| --- | --- | --- |
| `read_file`, `write_file`, `edit_file` | **exact** | Path matches an interrupt-mode rule under first-match. A **preceding deny wins** — HITL does not fire; tool returns permission-denied |
| `ls`, `glob`, `grep`, `delete` | **bulk** | Search subtree **could overlap** an interrupt-mode anchor. Pathless `grep` / missing path: **unconditional** fire if any interrupt-mode rule exists for that op. `path="."` normalized to `/` so it cannot bypass. `glob`’s `pattern` can redirect the root — absolute patterns gated independently; relative `..` treated as fire |

Docs: “Anchor interrupt patterns with a literal leading segment (`/secrets/**`). Fully unanchored `/**/secrets` **conservatively over-fires** on bulk tools.” Do not assume reads never interrupt — `_FS_TOOL_PATH_ARGS` also wires `ls`, `read_file`, `delete`, `glob`, `grep`. Directory `delete` checks `write` on the target **and every descendant**; file `delete` is exact-match first-match (`>=0.7.3`). Interrupt-mode `delete` is **bulk**. Interview trap: “we interrupt deletes” ≠ “we deny secrets.” Rule order is the PDP.

| Config | Actual behavior |
| --- | --- |
| `mode="interrupt"` on `/secrets/**` only | `when` gates; other writes auto-run (fail-open) |
| Same + `interrupt_on={"write_file": True}` | **Every** write pauses (`when` replaced) |
| Same + `interrupt_on={"write_file": False}` | **No** write pauses, including `/secrets/**` |
| `interrupt_on={"write_file": True}` without permissions | Every write pauses; no path PDP |
| Deny `/secrets/**` **before** interrupt rule | Interrupt never fires; deny error instead — often what you want |

Edited calls **re-enter the tool** and hit the pre-execution deny check; `respond` skips execution.

#### 2.6 Inheritance, interpreter skip, two resume dialects

| Spec | `interrupt_on` | `permissions` |
| --- | --- | --- |
| Main agent | Always applied | First-match list |
| Declarative `SubAgent` / auto GP | **Inherits** parent; spec **overrides** entirely if set | Inherits; spec **replaces** entirely |
| `CompiledSubAgent` | **Does not inherit.** Wire `interrupt()` inside the runnable or HITL middleware on that graph | Owned by the runnable |
| `AsyncSubAgent` (remote) | **Does not inherit.** Configure on the remote agent | Remote |
| Interpreter `task()` inside `eval` | Parent `interrupt_on` **not enforced per dispatch**. Gate `eval`. `interrupt_on={"task": True}` does **not** catch JS `task()` | Child still compiled with its spec; the **dispatch** is the hole |

PTC (`tools.*` from JS): allowlist-off by default; if enabled, PTC also bypasses HITL. Dynamic subagents default **on** if both interpreter and subagents exist. Child interrupt surfaces on the **parent** result (`result.interrupts` → `Command(resume=...)`). Compiled-subagent tools may call `interrupt({type, action, message})` directly; resume value is whatever you put in `Command(resume=...)` (not necessarily `{decisions: [...]}`). Two payload dialects on one parent:

1. **HITL middleware:** one `Interrupt` whose `.value` is `HITLRequest`; resume `{decisions: [..]}` positional.
2. **Raw `interrupt()` in a compiled tool:** one `Interrupt` per call; resume is the raw object (`{"approved": true}` in the Deep Agents sample). `Command(resume={id: value, ...})` pairs parallel graph branches by interrupt id.

A UI that always sends `{decisions: [{type: "approve"}]}` will mis-resume dialect 2. Branch on payload keys (`action_requests` vs `type`/`action`). Sync children inherit the parent checkpointer via nested graph invocation. Async children are a **second interrupt domain**. Nested subgraphs default to inherited checkpointer: you cannot time-travel between two child HITLs unless the subgraph compiles `checkpointer=True`.

`PatchToolCallsMiddleware` (always in the Deep Agents stack, **before** HITL) repairs dangling `tool_calls` after cancel / interrupt / HITL reject so providers like Gemini do not 400 on mismatched function-call counts.

JS `useStream`: interrupt on `stream.interrupt`; resume with `stream.submit(null, { command: { resume } })` or `stream.respond(hitlResponse)`. Headless tool handlers can **auto-resume** matching interrupts without showing a card — that is a **HITL bypass** if registered on a gated tool. Treat headless handlers as **PDP code**, same review as `when`.

#### 2.7 Static breakpoints vs `interrupt()` vs functional `@task`

| | Static `interrupt_before` / `interrupt_after` | `interrupt()` / HITL middleware |
| --- | --- | --- |
| Where | Before/after named nodes | Inside `after_model` or a tool |
| Payload | None (empty pause) | `HITLRequest` or any JSON |
| Resume | `invoke(None, config)` | `Command(resume=...)` |
| Conditional | All invocations of that node | `when` / application `if` |
| Parallel tools | Pauses the whole tools node | Batches only gated calls |

Docs: static breakpoints are **not recommended for HITL**. Mixing `interrupt_before=["tools"]` with HITL middleware double-pauses **[inferred]** — avoid.

Functional API: `interrupt()` inside `@task` replays from the **entrypoint**, restoring **completed task results** so finished `@task`s are not recomputed; a task that **started but did not finish** may run again. Middleware HITL lives in `after_model` of a **graph node**: the **entire node** restarts. CompiledSubAgent tools that call `interrupt()` are the functional-API-like case inside a node — wrap side effects in idempotent `@task` or put them **after** resume.

**Complexity [architecture, not a paper]:** filter is \(O(k)\) dict lookups + optional `when` per tool call in the turn. One `interrupt()` per batched turn, not per call. Resume validate is \(O(k)\) positional zip. Human clock dominates; middleware is microseconds.

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: LangChain / Deep Agents / LangSmith do **not** publish p50/p95/p99 of time-to-approve, queue depth, expire-deny, concurrent paused threads per replica, or memory of a paused `DeepAgentState`. LangGraph documents **indefinite wait**. [08](08-deep-agents-harness.md) likewise has no 30s/3m/10m HITL clocks. Percentiles below are **human-clock [inferred] policy**, not vendor SLOs. `$ per 1k` uses Anthropic list prices already cited in [08](08-deep-agents-harness.md) §2.3 (Sonnet 4.6: input **$3 / MTok**, output **$15 / MTok**, 5m cache write **$3.75**, cache read **$0.30**) × stated assumptions — not a SKU.

#### 3.1 Sunk tokens at pause

HITL fires in `after_model`, **after** the completion that proposed the tool calls. Already incurred: uncached input + output of that turn (and all prior turns); prompt-cache **write** of the static prefix if this was the first turn in the 5-minute window ([08](08-deep-agents-harness.md) §2.3). Occupied resources: checkpointer row, Agent Server worker (if the run is held), SSE slot, UI card. Agent Server: disconnecting SSE does **not** cancel the worker unless the client calls cancel; rejoin needs `thread_id`.

On **reject**, those tokens are sunk; the model spends **another** completion to replan. On **approve**, ToolNode runs; subsequent model turns continue. On **respond**, one cheap synthetic `ToolMessage` then another model turn.

Anthropic 5-minute ephemeral cache is the Deep Agents default TTL. HITL p50 that exceeds **5 minutes** converts remaining turns from cache-read ($0.30/MTok) to full input ($3/MTok) until a new write.

**[inferred]** extra input cost after a cache miss, 2k cached prefix, 7 remaining calls:

`7 × 2,000 × ($3 − $0.30) / 1e6 = $0.0378 / interrupted run`.

At 10% interrupt rate and all waits >5m: **+$3.78 / 1k runs** on prefix alone — small vs human labor, large vs a well-cached unattended fleet. 1-hour cache TTL (2× write price) is the documented long-gap option ([08](08-deep-agents-harness.md)). Use it if HITL p95 is expected in tens of minutes.

#### 3.2 `$ cost per 1k runs` — 0% vs 10% interrupt **[inferred]**

Assumptions (same skeleton as [08](08-deep-agents-harness.md) §2.4, plus HITL):

- Model: `anthropic:claude-sonnet-4-6` at list prices above.
- Uninterrupted run: 10 model calls, 2k cached prefix, 3k uncached in / 800 out per call, 1× 5m write + 9× reads. **$0.2229 / run → $223 / 1k** ([08](08-deep-agents-harness.md)).
- Interrupt rate **10%** of runs (one batched HITL after call 3). Anthropic telemetry: users **approve ~93%** of permission prompts → **[inferred]** 93% of HITL cards are Approve, 7% Reject (ignore Edit/Respond for the table).
- Approve path: wait p50 **3 min** → 5m cache still warm; remaining 7 calls as originally priced. No extra model call.
- Reject path: table uses “replan + 7 remaining” (same length as approve).
- Human labor **not** included in `$`.

| Scenario | LLM $ / run | LLM $ / 1k runs |
| --- | --- | --- |
| 0% interrupt (08 baseline) | $0.2229 | **$223** |
| 10% interrupt, all Approve, wait <5m | ≈ $0.2229 | **$223** (tokens unchanged) |
| 10% interrupt, 93% Approve / 7% Reject, wait <5m | +0.7% × (one extra 3k in + 800 out) on the 10% slice ≈ +$0.00021 | **~$223** |
| 10% interrupt, all waits **>5m** (cache miss on 7 remaining calls) | +0.10 × $0.0378 = +$0.00378 | **$227** |
| 10% interrupt, wait >5m, **and** 7% reject extra call | ≈ $227 + negligible reject tax | **~$227** |

**Interview takeaway:** at 10% interrupt rate, **LLM $ is dominated by the unattended path**. The NFR that moves is **p99 latency and reviewer FTE**, not tokens. Tokens become material when (a) interrupt rate is high (every-tool HITL), (b) waits exceed cache TTL, (c) reject-and-retry loops, or (d) you add a **second** classifier model (Claude Code auto-mode analog — 17% FNR on real overeager; do not replace HITL on payments/prod-delete with a classifier).

#### 3.3 Latency SLA — human clock AND resume path (numeric ms)

> ⚠️ Gap: **no published HITL product SLOs.** Middleware overhead itself is **microseconds** (dict lookup + optional `when` + one `interrupt()`). p99 agent latency in production is **HITL + cold sandbox + classifier cascade**, not the PDP ([07](07-guardrails.md) §2). Anthropic `API_TIMEOUT_MS=600000` is an **HTTP** deadline, not HITL. Classifier timeout in Claude Code auto mode is fail-closed (`automode-unavailable`) — adjacent product, cite only as analog.

These **30s / 3m / 10m** numbers are **not** in [08](08-deep-agents-harness.md) and **not** a LangChain SLO. They are an interview-ready default policy consistent with fail-closed HITL ([07](07-guardrails.md)) and AISVS “approvals expire within a defined TTL.” Implement with an external timer (Temporal, Agent Server cron, your queue).

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Human clock (time-to-approve)** **[inferred policy]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | 30 s interactive card (user is the approver). 3 min first-line / Slack DM — still inside Anthropic 5m cache. 10 min then **expire-deny** (not expire-approve). Abandoned: escalate then deny; persist audit. LangGraph will **not** do this |
| **HITL middleware apply (CPU, no I/O)** **[inferred]** | **1 ms** | **5 ms** | **20 ms** | Unpublished. Research: microseconds; rounded up so the table is numeric ms. Not the SLO |
| **Resume path: checkpoint load + write (Postgres `sync`)** **[inferred policy]** | **10 ms** | **50 ms** | **200 ms** | Same durability-tax class as [08](08-deep-agents-harness.md) §3.4. Prefer `sync` when HITL/crash-consistency is the product |
| **Resume path: `Command` → decisions applied, ToolNode start (no human, local FS)** **[inferred]** | **15 ms** | **80 ms** | **400 ms** | CPU row + checkpointer row + invoke overhead. Tool execution after that is the tool’s clock (email RTT, sandbox cold-start — unpublished here; see [09](09-deep-agents-execution.md)) |
| **Checkpointer `exit` extra on the hot path before interrupt returns** **[inferred]** | **0 ms** | **0 ms** | **0 ms** | Persist on graph exit **or interrupt**. Crash **before** the interrupt returns → pause may be lost |

**Mitigations mapped to percentiles:**

- **p50 (human):** in-product card, not a ticket queue; `when` so volume is irreversible actions only; sandbox to cut prompts (Anthropic analog **−84%**).
- **p95:** Slack DM still reasonable; `AnthropicPromptCachingMiddleware(ttl="1h")` if reviewers are slow; notify **after** you have the interrupt payload (idempotent), never Slack **before** `interrupt()` inside a custom node.
- **p99:** expire-deny at **600,000 ms**; escalate then deny; never expire-approve. Circuit-open on the HITL service → reject Command / refuse — **not** Approve.

#### 3.4 Throughput, back-pressure, reviewer FTE

No Deep Agents HITL RPM. Constraints that **are** documented:

| Ceiling | Number / fact | Effect |
| --- | --- | --- |
| Batch per model turn | one interrupt if ≥1 gated call | Parallel tools share one human round-trip — good for latency, bad for “approve all” fatigue |
| `recursion_limit` | **9,999** ([08](08-deep-agents-harness.md)) | HITL does not consume recursion the way a tool hop does; reject-retry loops do |
| Postgres `thread_id` | **< 255** chars | DB error, no checkpoint |
| Checkpointer growth | unbounded without retention | Prune **must not** drop pending interrupts |
| Agent Server worker vs pause | unpublished | Design as if the worker is free after the interrupt returns to the API (documented SSE disconnect behavior, [09](09-deep-agents-execution.md)) |
| Anthropic analog approve rate | **~93%** | Fatigue becomes a bypass (CaMeL / NCSC) |
| Sandbox analog prompt cut | **84%** | Volume control that HITL alone cannot provide |
| Classifier analog FNR / FPR | **17%** / **0.4%** | Residual miss; 3 consecutive / 20 total denials escalate — not a wall-clock TTL |

**[inferred]** staffing, not a vendor SKU. If 1k runs/day, 10% interrupt, 1 card/run, p50=30s think-time: `1000 × 0.10 × 30s = 3,000 s ≈ 0.8 reviewer-hours/day` loaded. At p95=3 min: `1000 × 0.10 × 180s = 5 reviewer-hours/day`. At every-write HITL with 8 cards/run: ~40 hours/day — a team, which is when 93% rubber-stamp appears. Budget FTE **before** LLM $.

**Back-pressure design:** (1) admit only 2–3 irreversible tool names into `interrupt_on`; (2) fail-closed FS catch-all deny so HITL is rare; (3) queue depth + reviewer FTE as the real RPM cap; (4) expire-deny, never expire-approve; (5) CAS the approval ticket so two API replicas cannot double-execute; (6) circuit-open → deny/refuse; (7) copy Anthropic’s denial budget (3 consecutive / 20 total) in application middleware if reject-retry storms — `ToolCallLimitMiddleware` / `ModelCallLimitMiddleware` halt loops but do **not** expire HITL.

#### 3.5 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of paused threads** | Graph waits forever; availability of **chat** ≠ availability of **approvers**. Paused HITL occupies a checkpointer row (and possibly a worker — unpublished). SSE drop ≠ cancel | Interactive p99 vs irreversible-action safety |
| **RPO of paused checkpoints** | Last durable interrupt write. `sync`: before next step. `async`: small loss window (crash during async write after interrupt → **[inferred]** pause missing; client saw SSE interrupts). `exit`: crash **before** interrupt returns → pause never persisted; crash **after** → resume possible. `InMemorySaver` RPO = **empty on restart** | Crash-consistency vs p50 (`sync` extra **10 / 50 / 200 ms [inferred]**) |
| **RTO of paused checkpoints** | Resume same `thread_id` with `Command(resume=...)`. New `thread_id` orphans the pause. Time travel **always forks**; original timeline remains; email/FS deletes are **not** rolled back. Replay **re-triggers** `interrupt()` — a stored “already approved” flag in app DB that is not in graph state will not skip the new interrupt | Time-to-resume vs forensic undo |
| **RPO of decision evidence** | Runtime gives checkpoint history + interrupt payloads + LangSmith traces. It does **not** give signed receipts, approver id, policy-bundle hash. Log at resume independently of the checkpointer so pruning does not destroy evidence | Storage $ vs audit |
| **Compliance** | HITL card is a **new processing surface** (GDPR/HIPAA). Default description embeds full tool args into interrupt payload, reviewer UI, traces. `PIIMiddleware` on the agent does **not** automatically redact interrupt payloads. Checkpointer retention = PII lifetime | Reviewer usefulness (raw args) vs minimization |
| **Correctness vs fatigue** | Every-write HITL looks strict; analog is **93% approve**. Sandbox + deny PDP so HITL volume is escapes and irreversible sends | Apparent control vs actual mediation |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_pause = last interrupt checkpoint (`sync`/`exit`-on-interrupt durable; `async` small hole; InMemory empty). RTO_pause = `Command` on stored `thread_id` (seconds) vs “we used InMemory” (**cannot restore**). RPO_audit = last WORM append you wrote. A TTL reject is a **completed deny**, not an RPO hole — log it. Retention cron that deletes pending-interrupt threads is accidental expire-deny **without** a `reject` ToolMessage.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: checkpointer + `thread_id` (no product TTL)

Happy path: `after_model` calls `interrupt(HITLRequest)` → runtime checkpoints thread state → API returns interrupts; worker may park → approver later `invoke(Command(resume=...), same thread_id)` → node **restarts from the beginning**; `interrupt()` returns the resume value; decisions applied; ToolNode runs.

**Idempotency rule:** any side effect **before** `interrupt()` in that node re-runs. Tools themselves run **after** resume — once per approve/edit unless time-travel replays.

| Durability | When it writes | HITL implication |
| --- | --- | --- |
| `"exit"` | Only on graph exit (success, error, **or interrupt**) | Fast. Crash **before** the interrupt returns → pause may be lost. Crash **after** interrupt is written → resume possible. Interrupt is an exit, so `"exit"` still writes on pause |
| `"async"` | Checkpoint while next step runs | Small window of lost writes on crash |
| `"sync"` | Checkpoint before next step | Highest durability; extra latency (**10 / 50 / 200 ms [inferred]**) |

Pending **task writes** inside a super-step: if another parallel node fails, completed nodes’ writes are durable and not re-run. HITL `after_model` is typically one node; the interrupt itself is the exit that `"exit"` mode persists.

**Lost resume:**

| Failure | Effect | Mitigation |
| --- | --- | --- |
| Resume with **new** `thread_id` | Empty graph; original pause orphaned | Idempotent client: store `thread_id` in the approval ticket |
| Resume with **input dict** instead of `Command` | Treated as new invocation | Only `Command(resume=)` |
| `InMemorySaver` + process restart | All pauses gone | Postgres/SQLite; Agent Server |
| `durability="exit"` crash **before** interrupt returns | Pause never persisted | `"sync"` for HITL-critical graphs |
| Postgres `thread_id` too long | DB error, no checkpoint | UUID ≤255 |
| Decision count ≠ action count | `ValueError` on resume | UI must not drop cards; regenerate `decisions` from current `get_state` interrupts |
| Approver never comes | Thread sits **forever** | Application TTL → reject Command |
| Self-hosted without Postgres | Worker failover = lost HITL | Real checkpointer |

**Worker failover mid-HITL:** Agent Server run enqueued; worker executes; Redis pub/sub to `/stream`; checkpoints to Postgres. After interrupt, the durable fact is the **checkpoint**, not the worker. A new worker can `Command(resume=)` with the same `thread_id`. Nested declarative children: child HITL visible on the parent interrupt channel; internal child checkpoints may not be independently addressable.

LangGraph checkpointers do **not** document a lease on a paused thread. Two API replicas can both `get_state` and both show the same interrupt. **[inferred]** application must CAS the approval ticket (`UPDATE ... WHERE status='pending'`), ignore the second Approve (idempotent tools + ticket state), or use Agent Server as the single writer for `runs` on a thread. Postgres `put` / `put_writes` are durability, not an approval lock. Optimistic concurrency: last `Command` wins if both resume — **double-execute** if the tool is not idempotent.

AISVS: “Checkpoints Are Not Durable Execution” (Diagrid, Mar 2026, cited therein) — LangGraph captures state but lacks durable timers, retries, compensations. Pair HITL with Temporal/Diagrid **or** custom TTL glue.

**Code-package analog:** `ServerHooksMiddleware._ask_permission_via_hitl` builds the same `HITLRequest` shape with `["approve","reject"]`. Empty / missing `decisions` is treated as **deny**. That is expire-deny at the hook layer — **not** default `create_deep_agent` HITL, which will `ValueError` on count mismatch rather than default-deny. If you wrap HITL, prefer empty-decision = deny for production queues.

LangSmith Deployment time-travel (`client.threads.get_history`, `update_state`, `runs.wait(..., input=None, checkpoint_id=...)`): resuming past execution **always forks**. Use this for “approver wants to undo Approve” — fork from before the HITL node; do **not** expect the original tool call to un-send.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Checkpointer blip, resume HTTP 429, reviewer UI timeout, SSE drop | Error rate; retryable status | Full-jitter retries on **resume transport** and idempotent `get_state`. Do **not** retry Approve without a ticket CAS. SSE drop ≠ cancel — rejoin `thread_id` |
| **Permanent** | No checkpointer / no `thread_id`; `allowed_decisions` empty at construct; `when`-less langchain + `mode="interrupt"`; decision type typo (`"approved"`); `respond` used as reject (logical, not HTTP) | Construction `ValueError`; resume `ValueError`; model “notifies user it is done” after a fake send | Fail closed. Catch at the BFF; map errors to expire-deny; do not retry Approve |
| **Poison-pill approve-all** | Batched mixed-risk cards + one “Approve all”; `True` on `read_file`/`ls`/every `write_file`; unanchored `/**/secrets` bulk over-fire; headless `useStream` auto-resume on `send_email`; `when` too coarse | 93%-class rubber-stamp; queue noise | Sandbox + deny PDP; `when` on dest/amount; no `edit`/`respond` on irreversible tools; treat headless handlers as PDP |
| **Poison-pill `when` skip** | `path` vs `file_path`; stale allowlist; uncaught exception in `when` | Destructive write never in `action_requests` | Wrap predicates; golden tests on skip; fail-closed to interrupt on predicate error **[inferred]** |
| **Idempotent resume** | Double-click / retried HTTP two `Command(resume=)` on the same interrupt; time-travel replay re-triggers interrupt then Approve again | Duplicate `send_email` / `delete` | LangGraph does **not** mint a single-use nonce (AISVS 9.2.3 requires binding + nonce + TTL — **not implemented**). Treat resume as at-least-once: `upsert`, `Message-Id`, compare-and-swap. Pending writes recovery does **not** make `send_email` idempotent across two timelines |
| **Inheritance holes** | Compiled/async no inherit; declarative override **replace** not merge; interpreter `task()` skip; PTC bypass | Child writes with no card | Wire HITL inside compiled runnable; restate parent gates on child specs; gate `eval` |
| **Config mismatch** | `interrupt_on={"write_file": False}` “to reduce noise” | `/secrets` never pauses | Do not set `write_file` True/False when using permission `when` |

#### 4.3 Circuit breaker closed → open → half-open (MUST NOT expire-approve)

> ⚠️ Gap: **HITL does not ship a circuit breaker.** Related knobs: `ToolCallLimitMiddleware` / `ModelCallLimitMiddleware` halt loops; permission `deny` is a hard tool error, not a pause. Put the breaker around the **HITL service** (queue, reviewer API, TTL worker), independent of the parent-model breaker in [08](08-deep-agents-harness.md).

```
        queue depth | reviewer error-rate | checkpointer timeout | TTL worker down
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  pause + │  success (human or expire-deny) resets count          │ FAIL FAST│
  │  wait    │                                                       │ reject / │
  └────┬─────┘                                                       │ refuse   │
       ▲                                                             │ NEVER    │
       │ probe = expire-deny OR queue-health                         │ approve  │
       │ probe OK                                                    └────┬─────┘
       │                                                                  │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 reject   │
                                                                    │ Command or │
                                                                    │ health GET │
                                                                    │ stay OPEN  │
                                                                    │ if fail    │
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip condition | Closed → open | Half-open probe | Fallback (**never** Approve) |
| --- | --- | --- | --- |
| HITL queue / reviewer 5xx | consecutive ≥ **5** or error-rate window | One **reject** Command on a canary thread, or a health GET | **HITL → deny (reject) → refuse**. Not Approve |
| Checkpointer timeout | consecutive ≥ **3** | One checkpoint write | Fail closed for HITL; ephemeral refuse for “must resume” products |
| TTL worker down | missed heartbeats | One expire-deny job | Keep interrupt **and** page; still deny at hard deadline — do not flip to approve because the timer is sick |
| Provider 429 on the **next** model turn after resume | parent-model breaker ([08](08-deep-agents-harness.md)) | Tiny invoke, GP off | Unrelated to HITL decisions |

**Fallback chain (required interview answer):** **HITL (pause + human `Command`) → deny (`reject` / expire-deny / empty-decisions-as-deny) → refuse (do not run the tool; return error to caller).** Never: HITL timeout → auto-approve. Never: circuit open → `{"type": "approve"}`. Never: classifier auto-HITL on payments/prod-delete (17% FNR analog). Expire-approve turns TTL into an attacker-controlled delay (wait out the human); AISVS flags timeout-based auto-approval as risk.

| Policy | Resume | When to use |
| --- | --- | --- |
| **Expire-deny** | `{"type": "reject", "message": "Approval timed out. Do not retry."}` | Default for email-send, delete, refund, MCP mutating tools |
| **Expire-approve** | `{"type": "approve"}` | Almost never |
| **Expire-escalate** | Keep interrupt; notify backup; extend TTL | p99 path. Still deny at hard deadline |
| **Expire-cancel thread** | `adelete_thread` / cancel run | Headless CI analog to Claude Code `-p` terminate-on-escalation |

#### 4.4 Zero-Trust MCP still required (HITL is not the PEP)

NIST/OWASP complete mediation: every downstream request is validated by a tool, an independent PEP, or the downstream system — **not** by the LLM (LLM03 #7). HITL is **optional extra** for high-impact actions (LLM03 #6), not that PEP.

| Layer | Question it answers | Deep Agents primitive |
| --- | --- | --- |
| **IdP / agent principal** | Who is speaking? | Not in HITL. `rt.server_info.user.identity` exists for Store namespaces ([08](08-deep-agents-harness.md)); HITL resume does **not** check it. `HITLResponse` has `decisions` only — **no `actor_id`**. Anyone who can `invoke` that `thread_id` with a `Command` is the approver |
| **PDP** | Is `(principal, action, resource, ctx)` allowed? | `permissions=` for **FS paths only**, fail-open. Cedar/OPA/AgentCore Policy at **MCP gateway** for tools |
| **HITL** | Does a human accept **this proposed call**? | `interrupt_on` / permission interrupt |
| **Sandbox** | What can happen even if someone clicks Approve? | `BaseSandbox` / OS seatbelt. Codex: **sandbox ⊥ approval_policy** (`on-request` / `never` / granular vs `read-only` / `workspace-write` / `danger-full-access`). `untrusted` policy **retired** in CLI v0.149.0 (2026-08-20) |
| **MCP PEP** | Is this `tools/call` allowed for this token audience? | Gateway. `permissions=` **does not apply**. `interrupt_on` **can** name the MCP tool but is not OAuth |

Zero-Trust: authenticate every hop, no passthrough tokens, audience-bound credentials. Clients **MUST** send RFC **8707** `resource` = canonical MCP server URI on authorize *and* token. Servers **MUST** accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token; obtain a new token (typically RFC **8693** exchange). Hash-pin `toolSurfaceHash` over canonical JSON of name + description + schemas; re-verify on every `tools/call`. A human clicking Approve on a LangGraph card does **not** issue an audience-bound token and does **not** replace RFC 8707.

`tools=` accepts MCP tools from `langchain-mcp-adapters`. You **may** list those names in `interrupt_on` — HITL will pause before the adapter sends `tools/call`. That is still a **review queue**, still fail-open for any MCP tool you forgot to name, still no hash-pin of the tool JSON, still no audience check. A model that has both `write_file` (gated) and `mcp_fs_write` (ungated) bypasses the path PDP.

Three human pauses — do not conflate:

| Pause | Who initiates | What it authorizes | Deep Agents overlap |
| --- | --- | --- | --- |
| **HITL middleware** | Graph, after model proposes a tool | “Run this tool call in this thread” — **not** a token | `interrupt_on` / permission interrupt |
| **MCP elicitation** (`elicitation/create`, incl. URL mode 2025-11-25) | MCP **server** | Collect input / send user out-of-band. Spec: **not** for authorizing the MCP client to the MCP server | Independent. URL mode exists so secrets never enter model context. Putting a refresh token in a HITL `respond` is the anti-pattern elicitation was designed to prevent |
| **MCP OAuth / RFC 8707** | Client→AS | Client may call **this** MCP server as this user | Gateway PEP. HITL click does not mint this |

Official Deep Agents `LocalShellBackend` warning: unrestricted host shell; **STRONGLY RECOMMENDED** HITL on **all** operations; dedicated dev hosts; never untrusted users. `permissions=` cannot constrain `execute`. Architecture rule: sandbox bounds **blast radius without a human**; HITL bounds **when to ask**. Using only HITL on `LocalShellBackend` is the fatigue-failure mode Anthropic measured. Using only sandbox without HITL on network-enable / escape is the Codex `danger-full-access` out-of-scope cell. Issue #2894 asked for `ExecutePermission` / `TaskPermission`; maintainers declined — custom middleware. `interrupt_on={"execute": True}` is the supported gate on the **tool path**; PTC `tools.execute` would not be.

**TOCTOU (CWE-367):** (1) Human reviews `action_requests[].args` (snapshot); `approve` executes the **in-memory** `ToolCall` — Deep Agents does **not** re-hash args at execute. (2) Permission/`when` checks a path string; later `open()` follows a swapped symlink — `validate_path` is string policy, not `openat2(RESOLVE_NO_SYMLINKS)`. (3) Human `edit`s args; those new args are **not** shown in a second card unless you add one; `when` does not re-run. Mitigations in-tree: permission deny after edit; display raw args in `description`. Mitigations you must add: hash(`canonical_args`) at display and at execute; refuse mismatch; strip invisible Unicode in the HITL UI. OpenClaw CVE-2026-29607 (wrapper-level Allow-Always) is bait-and-switch: approval bound to a wrapper, not the inner command. PraisonAI CVE-2026-44338 (unauthenticated agent API) is AISVS’s example that an approval workflow **assumes you can identify the requester** — LangGraph HITL on a public `thread_id` is the same class.

**Tool-level RBAC of who can approve [inferred from AISVS 9.2.1 / 9.2.3, not a Deep Agents feature]:** authenticate the resume caller (IdP session, not the end-user’s chat token if a security reviewer must approve); authorize `approve` vs `reject` vs `edit` per role (SOC cannot Approve prod delete); bind `thread_id` + interrupt id + decision to that identity in the audit log; separation of duties (EU AI Act Art. 14 shape — requirement, not Deep Agents compliance). `allowed_decisions=["approve"]` only means the human **cannot** reject in-band — closing the UI leaves the thread paused unless TTL reject is allowed.

#### 4.5 PII pipeline — detect → redact → audit (HITL UI payloads)

Default description embeds **full tool args**. Email `body`, `to`, file contents in `write_file`, SQL, customer IDs all land in: interrupt payload (returned to the client, stored in checkpoint); reviewer UI (browser, Slack if you forward it); LangSmith traces if you log interrupts. `PIIMiddleware` on the agent does not automatically redact interrupt payloads — different hook.

**Pipeline (explicit):**

1. **Detection (control plane, before the card is rendered or the checkpoint is shown).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner. Scan: `action_requests[].args`, `description`, human `edit`/`respond` messages, Slack-forwarded cards, traces of interrupts. If ML is down: **fail closed to mask** on reviewer UI; **fail closed (block)** on tool args that leave for MCP / email-send — do not put raw PAN on a Slack HITL card.
2. **Redaction.** Custom `description` factory shows a redacted view; keep raw args **server-side** for the hash-bind. `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the reviewer can still reason about dest vs body; `block` when the field must not exist on the card (secrets paths). “Classified: bounce to DPO” for residual. Do **not** persist raw PAN in traces.
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: approver **identity**, `thread_id`, interrupt id, tool name, **arg digest** (canonical JSON SHA-256) at display **and** at execute, decision type, `correlation_id`, policy version, detector, pre/post content hashes of the **redacted** card. A resume without an audit row is a control-plane bug. Store independently of the checkpointer so pruning does not destroy evidence. GDPR erasure vs legal hold is digest-level.

#### 4.6 `permissions=` fail-open is not least privilege

A missing catch-all `deny` on `/**` is a production hole. Correct pattern from the docs: specific deny, then workspace allow, then `/**` deny. Subagent `permissions` **replaces** parent — an auditor spec that omits the catch-all is **more** privileged than a parent that had it. `mode="interrupt"` is still fail-open for unmatched paths: they never pause and never deny.

---

### 5. Production Enterprise Code

Self-contained. Optional `deepagents` / `langchain` / `langgraph` imports. Stdlib path runs the same control flow: retries + full jitter, circuit breaker that **never** expire-approves, expire-deny timer, fallback **HITL → deny → refuse**, PII detect→redact→audit, structured logs with `correlation_id` and `approver_id`, CAS ticket against double-approve, arg-digest bind. Run: `python deep_agents_steering.py`.

```python
#!/usr/bin/env python3
"""HITL steering: pause is not a PDP. Expire-deny, never expire-approve.

Fallback: HITL (human Command) → deny (reject / TTL) → refuse (no tool run).
Run: python deep_agents_steering.py
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
#   from langgraph.types import Command
#   from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger("steering")


def slog(level: int, msg: str, **extra: Any) -> None:
    payload = {"msg": msg, **extra}
    _log.log(level, json.dumps(payload, default=str))


def full_jitter_sleep(attempt: int, base_s: float = 0.05, cap_s: float = 1.0) -> None:
    time.sleep(random.uniform(0, min(cap_s, base_s * (2**attempt))))


def retry_call(fn: Callable[[], Any], *, attempts: int = 3) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except (TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            if i == attempts - 1:
                raise
            full_jitter_sleep(i)
    raise last  # pragma: no cover


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    """HITL-service breaker. Half-open probe MUST be deny/health, never approve."""

    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self) -> None:
        if self._state is CircuitState.CLOSED:
            return
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                return
            raise CircuitOpenError(self.name)
        # HALF_OPEN: allow exactly one probe (caller must deny/health, never approve)

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
ALLOWED = frozenset({"approve", "edit", "reject", "respond"})
SIDE_EFFECT = frozenset({"notify_email", "delete", "refund", "execute"})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def args_digest(args: dict[str, Any]) -> str:
    return _sha(json.dumps(args, sort_keys=True, default=str))


def pii_detect_redact_audit(
    text: str,
    *,
    audit: list[dict[str, Any]],
    correlation_id: str,
    tenant_id: str,
    sink: str,
    block_on_pan: bool = True,
) -> str:
    kinds = [n for n, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"hitl_ui", "mcp_args", "email_body"}:
        audit.append(
            {"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds,
             "action": "block", "pre": pre, "post": _sha(""), "detector": "regex"}
        )
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append(
        {"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds,
         "action": "redact" if redacted != text else "allow",
         "pre": pre, "post": _sha(redacted), "detector": "regex"}
    )
    return redacted


@dataclass
class ActionRequest:
    name: str
    args: dict[str, Any]
    allowed: tuple[str, ...] = ("approve", "edit", "reject", "respond")


@dataclass
class Ticket:
    thread_id: str
    interrupt_id: str
    actions: list[ActionRequest]
    display_digests: list[str]
    status: str = "pending"  # pending | consumed | expired
    created_at: float = field(default_factory=time.monotonic)
    ttl_s: float = 600.0  # 10 min expire-deny [inferred policy]


@dataclass
class ResumeResult:
    status: str  # approved | denied | refused
    decisions: list[dict[str, Any]]
    degraded: bool


class SteeringRuntime:
    def __init__(self, *, ttl_s: float = 600.0, now: Callable[[], float] | None = None) -> None:
        self.ttl_s = ttl_s
        self._now = now or time.monotonic
        self.tickets: dict[str, Ticket] = {}
        self.breaker = CircuitBreaker("hitl_queue", failure_threshold=3, cooldown_s=30.0)
        self.audit: list[dict[str, Any]] = []
        self.decision_log: list[dict[str, Any]] = []

    def enqueue(
        self,
        actions: list[ActionRequest],
        *,
        thread_id: str,
        correlation_id: str,
        tenant_id: str,
    ) -> Ticket:
        extra = {"correlation_id": correlation_id, "tenant_id": tenant_id,
                 "thread_id": thread_id, "approver_id": "-"}
        cards, digests = [], []
        for a in actions:
            raw = json.dumps(a.args, sort_keys=True, default=str)
            cards.append(pii_detect_redact_audit(
                raw, audit=self.audit, correlation_id=correlation_id,
                tenant_id=tenant_id, sink="hitl_ui",
            ))
            digests.append(args_digest(a.args))
        t = Ticket(
            thread_id=thread_id, interrupt_id=str(uuid.uuid4()),
            actions=actions, display_digests=digests,
            ttl_s=self.ttl_s, created_at=self._now(),
        )
        self.tickets[t.interrupt_id] = t

        def _notify_after_interrupt() -> str:
            return t.interrupt_id  # Slack/queue publish AFTER payload exists

        retry_call(_notify_after_interrupt)
        slog(logging.INFO, "hitl_enqueued", n=len(actions), cards=cards, **extra)
        return t

    def _cas(self, ticket: Ticket, to: str) -> bool:
        if ticket.status != "pending":
            return False
        ticket.status = to
        return True

    def _log_decision(
        self, ticket: Ticket, decisions: list[dict[str, Any]], *,
        approver_id: str, correlation_id: str, tenant_id: str, reason: str,
    ) -> None:
        self.decision_log.append({
            "cid": correlation_id, "tenant": tenant_id, "thread_id": ticket.thread_id,
            "interrupt_id": ticket.interrupt_id, "approver_id": approver_id,
            "reason": reason, "tools": [a.name for a in ticket.actions],
            "arg_digests": ticket.display_digests,
            "decisions": [d.get("type") for d in decisions], "ts": time.time(),
        })
        slog(logging.INFO, "hitl_decision", reason=reason,
             types=[d.get("type") for d in decisions],
             correlation_id=correlation_id, tenant_id=tenant_id,
             thread_id=ticket.thread_id, approver_id=approver_id)

    def deny_decisions(self, n: int, message: str) -> list[dict[str, Any]]:
        return [{"type": "reject", "message": message} for _ in range(n)]

    def resume(
        self,
        interrupt_id: str,
        decisions: list[dict[str, Any]] | None,
        *,
        approver_id: str,
        correlation_id: str,
        tenant_id: str,
        role_can_approve: bool = True,
    ) -> ResumeResult:
        extra = {"correlation_id": correlation_id, "tenant_id": tenant_id,
                 "thread_id": "-", "approver_id": approver_id}
        ticket = self.tickets.get(interrupt_id)
        if ticket is None:
            slog(logging.ERROR, "hitl_unknown_interrupt", **extra)
            return ResumeResult("refused", [], True)
        extra["thread_id"] = ticket.thread_id
        n = len(ticket.actions)

        def _deny(reason: str, msg: str) -> ResumeResult:
            dec = self.deny_decisions(n, msg)
            if not self._cas(ticket, "expired" if "timeout" in reason else "consumed"):
                slog(logging.WARNING, "hitl_double_resume_ignored", **extra)
                return ResumeResult("refused", [], True)
            self._log_decision(
                ticket, dec, approver_id=approver_id, correlation_id=correlation_id,
                tenant_id=tenant_id, reason=reason,
            )
            return ResumeResult("denied", dec, True)

        try:
            self.breaker.allow()
        except CircuitOpenError:
            slog(logging.ERROR, "hitl_circuit_open_deny", **extra)
            return _deny("circuit_open", "HITL service unavailable. Do not retry.")

        if self._now() - ticket.created_at >= ticket.ttl_s:
            self.breaker.record_success()
            return _deny("ttl_timeout", "Approval timed out. Do not retry.")
        if not role_can_approve:
            self.breaker.record_success()
            return _deny("rbac_deny", "Approver not authorized for this tool.")
        if not decisions or len(decisions) != n:
            self.breaker.record_success()
            return _deny("bad_decisions", "Decision count mismatch. Do not retry.")

        for i, (d, action) in enumerate(zip(decisions, ticket.actions)):
            dtype = d.get("type")
            if dtype not in ALLOWED or dtype not in action.allowed:
                self.breaker.record_success()
                return _deny("illegal_type", f"Decision {dtype!r} not allowed.")
            if dtype == "respond" and action.name in SIDE_EFFECT:
                self.breaker.record_success()
                return _deny("respond_forbidden", "respond is not a deny on side-effecting tools.")
            if dtype == "edit" and (d.get("edited_action") or {}).get("name") != action.name:
                self.breaker.record_success()
                return _deny("rename_forbidden", "edited_action.name must match original.")
            if dtype == "approve" and args_digest(action.args) != ticket.display_digests[i]:
                self.breaker.record_success()
                return _deny("toctou_hash", "Args changed since display. Do not execute.")

        if any(d.get("type") == "approve" for d in decisions) and self.breaker._state is CircuitState.HALF_OPEN:
            self.breaker.record_failure()
            return _deny("half_open_no_approve", "Circuit half-open: deny-only probe.")

        if not self._cas(ticket, "consumed"):
            slog(logging.WARNING, "hitl_double_resume_ignored", **extra)
            return ResumeResult("refused", [], True)

        self.breaker.record_success()
        self._log_decision(
            ticket, decisions, approver_id=approver_id, correlation_id=correlation_id,
            tenant_id=tenant_id, reason="human",
        )
        if all(d.get("type") in {"reject", "respond"} for d in decisions):
            return ResumeResult("denied", decisions, False)
        return ResumeResult("approved", decisions, False)


def try_build_deep_agent() -> Any:
    """Illustrative wiring when the lib is present. Stdlib path does not call this."""
    try:
        from deepagents import create_deep_agent  # type: ignore
        from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
    except Exception:
        return None
    return create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        checkpointer=InMemorySaver(),  # prod: AsyncPostgresSaver
        interrupt_on={"notify_email": {"allowed_decisions": ["approve", "edit", "reject"]}},
        name="steering-copilot",
    )


if __name__ == "__main__":
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    rt = SteeringRuntime(ttl_s=600.0, now=now)
    t1 = rt.enqueue(
        [ActionRequest("notify_email", {"to": "ada@example.com", "subject": "hi"})],
        thread_id="t-1", correlation_id="cid-1", tenant_id="acme",
    )
    r1 = rt.resume(
        t1.interrupt_id, [{"type": "approve"}],
        approver_id="reviewer-7", correlation_id="cid-1", tenant_id="acme",
    )
    print(r1)
    assert r1.status == "approved"
    assert any(row["action"] == "redact" for row in rt.audit)
    assert rt.decision_log[-1]["approver_id"] == "reviewer-7"

    t2 = rt.enqueue(
        [ActionRequest("notify_email", {"to": "bob@example.com"})],
        thread_id="t-2", correlation_id="cid-2", tenant_id="acme",
    )
    clock["t"] = 601.0
    r2 = rt.resume(
        t2.interrupt_id, [{"type": "approve"}],
        approver_id="reviewer-7", correlation_id="cid-2", tenant_id="acme",
    )
    print(r2)
    assert r2.status == "denied" and r2.decisions[0]["type"] == "reject"

    t3 = rt.enqueue(
        [ActionRequest("notify_email", {"to": "c@example.com"})],
        thread_id="t-3", correlation_id="cid-3", tenant_id="acme",
    )
    r3a = rt.resume(
        t3.interrupt_id, [{"type": "approve"}],
        approver_id="reviewer-7", correlation_id="cid-3", tenant_id="acme",
    )
    r3b = rt.resume(
        t3.interrupt_id, [{"type": "approve"}],
        approver_id="reviewer-7", correlation_id="cid-3", tenant_id="acme",
    )
    assert r3a.status == "approved" and r3b.status == "refused"

    rt.breaker._state = CircuitState.OPEN
    rt.breaker._opened_at = time.monotonic()
    t4 = rt.enqueue(
        [ActionRequest("notify_email", {"to": "d@example.com"})],
        thread_id="t-4", correlation_id="cid-4", tenant_id="acme",
    )
    r4 = rt.resume(
        t4.interrupt_id, [{"type": "approve"}],
        approver_id="reviewer-7", correlation_id="cid-4", tenant_id="acme",
    )
    assert r4.status == "denied"  # OPEN → deny, never approve

    try:
        rt.enqueue(
            [ActionRequest("notify_email", {"to": "x", "body": "4111 1111 1111 1111"})],
            thread_id="t-5", correlation_id="cid-5", tenant_id="acme",
        )
        raise SystemExit("expected pan block")
    except PermissionError:
        pass

    print("ok", len(rt.audit), "pii rows", len(rt.decision_log), "decisions")
```

**Wiring notes (not in the script):** production `create_deep_agent` should pass `AsyncPostgresSaver` (not `InMemorySaver`), `thread_id` **< 255**, `durability="sync"` when HITL is the product, `AnthropicPromptCachingMiddleware(ttl="1h")` if reviewers are slow. Do **not** set `interrupt_on={"write_file": True/False}` when permission `mode="interrupt"` supplies `when`. Name MCP mutating tools in `interrupt_on` **and** keep the gateway PEP. Omit `respond` on side-effecting tools. Gate `eval`. Pin `deepagents==0.7.12` and `langchain>=1.3.3` if you use `when` / permission interrupts. Resume **must** be `Command(resume=...)` with `version="v2"`. Slack notify **after** the interrupt payload exists, with an idempotency key.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Destructive FS + email-send HITL

**Problem.** A coding/research agent may write workspace files freely. Writing `/secrets` or `/memories` and sending email require a human. MCP mail still cannot skip the gateway. Reviewers rubber-stamp if every `write_file` pauses (Anthropic analog **93%** approve). Platform debate: (A1) permission `mode="interrupt"` on secrets + named `notify_email` HITL + catch-all FS deny + MCP gateway; (A2) `interrupt_on={"write_file": True}` on every write, no path PDP; (A3) no HITL, fail-closed `permissions=` + sandbox, MCP mail blocked at gateway only.

**Proposed architecture (recommended: A1):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent                              │
  │ JWT →   │   │   permissions: interrupt /secrets/** /memories/**       │
  │ reviewer│   │                allow /workspace/**                      │
  │ role ≠  │   │                deny /**          (fail-closed FS)       │
  │ chat    │   │   interrupt_on: notify_email + mcp mail.send            │
  │ user    │   │     allowed=["approve","edit","reject"]  (no respond)   │
  │         │   │   DO NOT set write_file True/False (would clobber when) │
  │         │   │   PostgresSaver sync  thread_id uuid7  TTL 600s deny    │
  │         │   │   CAS ticket + arg digest + actor_id WORM               │
  │         │   │   PII detect→redact→audit on HITL cards                 │
  │         │   │   gateway Cedar/OPA + DLP + dest allowlist (RFC 8707)   │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: model proposes write_file / notify_email       │
                    │   after_model batches gated calls → HITLRequest      │
                    │   Slack notify AFTER interrupt payload (idempotent)  │
                    │   resume Command → wrap_tool_call deny still binds   │
                    │   cache ttl=1h if reviewers are slow                 │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** maps LLM03 #6 (HITL on high-impact) onto named tools without turning HITL into the PEP. `respond` omitted so reviewers cannot fake a send. Resume handler verifies `len(decisions)==len(action_requests)`, records actor, hashes args, **re-checks dest allowlist** (TOCTOU). Failure to avoid: `interrupt_on={"write_file": False}` “to reduce noise” — disables `/secrets` interrupt.

**Trade-off matrix:**

| Axis | **A1 interrupt-mode secrets + named email + gateway (recommended)** | **A2 HITL on every `write_file`** | **A3 no HITL, fail-closed FS + gateway only** |
| --- | --- | --- | --- |
| **Cost** | **[inferred] ~$223 / 1k** if waits <5m (tokens ≈ unattended); FTE **0.8 h/day** at 1k runs × 10% × 30s. Slow waits **$227 / 1k** cache-miss tax | Same LLM $ until fatigue; 8 cards/run → **~40 reviewer-hours/day [inferred]** | Lowest LLM $ (**$223 / 1k**); no FTE; no human for policy exceptions |
| **Latency** | Human clock **30,000 / 180,000 / 600,000 ms [inferred]** on rare cards; resume path **15 / 80 / 400 ms [inferred]** | Fatigue-dominated p99; cache miss if >5m | Model+tool only (ReAct **2,000 / 8,000 / 20,000 ms [inferred]** from [08](08-deep-agents-harness.md)); no HITL clock |
| **Ops complexity** | Medium: checkpointer, UI, TTL, CAS, PII factory, Slack after interrupt | Medium UI, worse queue noise (unanchored bulk over-fire) | Low |
| **Security** | Best in-tree FS story + LLM03 #6 on email; MCP still needs gateway. HITL is **not** Zero-Trust | Looks strict; **93% approve** analog; TOCTOU unmitigated without hash-bind | Strong for FS; **zero** human for irreversible send if gateway miss |
| **Scalability** | Reviewer staffing on rare cards | Reviewer collapse | Horizontal PEP |

**Decision.** **A1 wins** for this problem statement. A2 is the fatigue-failure mode. A3 wins only if email/MCP mutating tools are **impossible** (not on `tools=`) and FS catch-all deny is tested. Enterprise default in the research trade-off matrix: **gateway PEP + HITL on remaining ambiguous calls**.

#### Scenario B — Coding `execute` HITL vs sandbox-only no HITL

**Problem.** Ship a coding assistant: repo checkout, tests, patches, optional `execute`. Mixing poorly is how you get **93%** rubber-stamps **and** host RCE. Debate: (B1) `BaseSandbox`, network off, HITL omitted or only on network/host-mount escapes; (B2) `LocalShellBackend` + HITL on **all** operations; (B3) hybrid Codex-shaped: sandbox `workspace-write` analog + HITL on escape via `when`.

**Proposed architecture (recommended: B1 for untrusted/multi-tenant; B3 for internal copilot):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL B1 (prod): BaseSandbox network OFF              │
  │ JWT     │   │   interrupt_on only for execute that needs net/mounts   │
  │ tenant  │   │     OR omit HITL if sandbox is the PDP                  │
  │         │   │   permissions on Composite /memories/ routes; CANNOT    │
  │         │   │     permission the sandbox default                      │
  │         │   │ CONTROL B3 (internal): workspace-write analog + when    │
  │         │   │   on path outside workspace / network enable            │
  │         │   │ NEVER: LocalShell + permissions deny /** + no execute   │
  │         │   │   HITL — shell ignores the FS PDP                       │
  │         │   │ Destructive-action PROXY in front of cloud MCP /        │
  │         │   │   volume.delete (AISVS): out-of-band tokens model       │
  │         │   │   never sees. HITL is the UI to that proxy, not the     │
  │         │   │   only control. interrupt_on={"execute": True} is the   │
  │         │   │   supported tool-path gate; PTC tools.execute is not    │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: execute in guest; auth proxy for keys          │
                    │   PII detect→redact→audit; gateway on MCP            │
                    │   LocalShell: laptop CLI only + HITL ALL ops [docs]  │
                    └──────────────────────────────────────────────────────┘
```

**Do not:** `LocalShellBackend` + `permissions=` deny `/**` + no HITL on `execute`. Shell ignores the PDP. **Do not:** sandbox **with** network + credentials in the guest + no HITL. Isolation ≠ authorization.

**Trade-off matrix:**

| Axis | **B1 Sandbox-only / rare execute HITL (recommended prod)** | **B2 LocalShell + HITL all ops** | **B3 Hybrid Codex-shaped (internal copilot)** |
| --- | --- | --- | --- |
| **Cost** | Lowest LLM $; sandbox compute unpublished here ([09](09-deep-agents-execution.md)) | FTE on every command | Low extra LLM $; FTE only on escapes |
| **Latency** | Sandbox cold-start unpublished — treat as p99 of `execute`, not TTFT. No human clock if HITL omitted | Human clock **30,000 / 180,000 / 600,000 ms [inferred]** on **every** command | Human clock only on escape; resume **15 / 80 / 400 ms [inferred]** |
| **Ops complexity** | Medium (sandbox provider) | High (humans); docs’ last resort | Medium: `when` + fail-closed VFS deny |
| **Security** | Blast radius bounded; no human for policy exceptions; still need HITL or deny on network-enable | Docs: HITL on ALL ops; fatigue; **no isolation** | Sandbox ⊥ approval; HITL remaining questions stay rare |
| **Scalability** | High (untrusted tickets, multi-tenant, CI) | Does not scale | High for internal copilot |

**Decision.** **B1 wins** for untrusted / multi-tenant / CI. **B3 wins** for an internal copilot that must sometimes enable network. **B2 never wins** in production — laptop CLI only. Classifier auto-HITL (Claude auto-mode analog) is for unattended coding **inside** sandbox, not payments; residual **17% FNR**.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| HITL silently broken | `checkpointer=None` (default) or no `thread_id` | Tools run without pause, or graph crashes mid-turn | Always pass Postgres-class checkpointer + `thread_id`. Tests use `MemorySaver()` |
| Approval fatigue → auto-approve | `True` on high-frequency tools; Approve-all on mixed batch; coarse `when`; unanchored bulk interrupt | 93%-class rubber-stamp | Sandbox + deny PDP; `when` on dest/amount; no `edit`/`respond` on irreversible tools |
| `when` skip | `path` vs `file_path`; exception in predicate; stale allowlist | Destructive call absent from `action_requests` | Wrap predicates; golden skip tests; fail-closed to interrupt **[inferred]** |
| Interpreter / PTC bypass | `task()` inside `eval`; PTC `tools.*` | Child/MCP ran with no card | Gate `eval`; `interrupt_on={"task": True}` does **not** catch JS `task()` |
| Compiled / async hole | No inherit; declarative override **replace** | Child `write_file` ungoverned | Wire HITL inside runnable; restate parent gates |
| Permission vs `interrupt_on` mismatch | User map wins per tool name | `/secrets` never pauses, or every write pauses | Do not set `write_file` True/False beside interrupt-mode `when` |
| `respond` used as reject | Status `"success"` synthetic | Model notifies “email sent” | Forbid `respond` on side-effecting tools |
| `edit` to another tool | `edited_action.name` unrestricted | `notify_email` → `delete_customer` | Validate name in resume handler **[inferred]** |
| `try/except` swallows interrupt | Bare `except Exception` | No pause; tool runs | Never wrap `interrupt()` that way |
| Headless auto-resume | JS `useStream` tool handlers | Silent Approve-equivalent | Treat as PDP |
| Wrong invoke version | Check only `.interrupts` on v1 or miss `stream.interrupted` | UI thinks done while paused | `version="v2"` / stream `v3`; assert both shapes **[inferred]** |
| Empty / typo decisions | `"approved"`; length mismatch | `ValueError` instead of deny | BFF maps to expire-deny; Code-package empty = deny |
| `allowed_decisions=["approve"]` + TTL reject | TTL sends disallowed `reject` | `ValueError`; thread forever | Allow `reject` for timeouts or send an allowed type |
| Expire-approve | TTL → `{"type": "approve"}` | Attacker waits out the human | **Never.** Expire-deny |
| Double-execute | Two resume Commands; time-travel fork | Duplicate side effects | CAS ticket; idempotent tools; no nonce in-tree (AISVS gap) |
| Checkpoint GC of paused threads | Retention deletes `next` waiting on interrupt | Accidental expire without ToolMessage | Filter pending interrupt |
| MCP without gateway | `interrupt_on` on `mail.send` only | Unnamed MCP auto-runs; no audience | Gateway PEP + hash-pin; `permissions=` does not see MCP |
| PII on the card | Default `description` embeds full args | GDPR/HIPAA incident | detect→redact→audit; custom description factory |
| LocalShell + FS deny, no execute HITL | Shell ignores `permissions=` | Host RCE | Sandbox; or HITL on **all** ops (docs), laptop only |

No public Deep Agents HITL post-mortem corpus beyond the issues/PRs cited in research (#2334, #37579, #2894). Do not invent incidents. Adjacent CVEs in AISVS (OpenClaw wrapper Allow-Always, PraisonAI unauthenticated API) are pattern analogs, not Deep Agents CVEs.

---

## Key Takeaways

- Steering is `interrupt_on` + permission `mode="interrupt"` on LangGraph `interrupt()`. The checkpointer **is** the HITL database. The graph waits **forever** unless you add expire-deny.
- `True` means four decisions; unnamed tools auto-approve — HITL is fail-open. `permissions=` is also fail-open (FS-only, first-match). Neither is a PDP you can point a Zero-Trust review at.
- HITL is a **pause**, not a PEP. MCP still needs the gateway (OAuth 2.1, RFC 8707 audience, no passthrough, hash-pin). A human click does not mint a token.
- Declarative children inherit HITL; compiled and async do not; interpreter `task()` skips parent `interrupt_on` — gate `eval`. User `interrupt_on` wins per tool name and can clobber permission `when`.
- Approve executes original in-memory args; there is no hash-bind, nonce, or TTL in-tree (AISVS 9.2.3 gap). CWE-367 + edit-to-other-tool are on you. `respond` is success, not deny.
- Sandbox reduces questions; HITL remaining questions must stay rare or humans will Approve **93%** of them. Circuit-open / timeout → **deny → refuse**, never approve.
- At 10% interrupt, LLM $ stays **~$223 / 1k** if waits <5m (**$227** if cache misses). The bill that moves is reviewer FTE and p99. Human clock **30,000 / 180,000 / 600,000 ms [inferred]**; resume path **15 / 80 / 400 ms [inferred]**.
- PII on HITL cards is detect → redact → audit. Log approver identity + arg digest off the checkpointer. `PIIMiddleware` does not redact interrupt payloads for you.

---

## Interview Q&A

**Q1. What is Deep Agents steering, in one minute?**  
I treat HITL as a durable pause, not a PDP. `interrupt_on` names tools that `HumanInTheLoopMiddleware` batches after the model proposes `tool_calls`. Unnamed tools auto-approve. A checkpointer is required; LangGraph waits forever; expire-deny is my timer sending a `reject` Command. `permissions=` `mode="interrupt"` synthesizes the same middleware for FS paths, still fail-open, still not MCP or `execute`. The model proposes; middleware plus my resume handler dispose.

**Q2. Walk model `tool_calls` → HITL → resume → execute.**  
After the completion, `after_model` filters by `interrupt_on` and `when`. Remaining calls become one `HITLRequest`. `interrupt()` checkpoints the thread and the API returns `result.interrupts` on `version="v2"`. I show cards from `action_requests` (display copies). I resume the same `thread_id` with `Command(resume={"decisions": [...]})` positional. Approve/edit keep a `ToolCall`; reject/respond inject a `ToolMessage`. `ToolNode` runs; FS deny still binds after edit. I never pass a new input dict or `Command(update=)` to continue a pause.

**Q3. `True` vs `InterruptOnConfig` vs `when` vs permission interrupt.**  
`True` is all four decisions. `InterruptOnConfig` requires non-empty `allowed_decisions`; optional `description`, `args_schema`, `when`. `when` False auto-approves out of the batch — a buggy predicate is a silent hole, and `tool=None` at this hook. `mode="interrupt"` adds `when` on matching FS paths. If I also set `interrupt_on={"write_file": True}` I drop that `when` and pause every write; `False` disables secrets interrupts. User map wins per tool name.

**Q4. Give me `$ per 1k` at 0% vs 10% interrupt.**  
Inferred, same 10-call Sonnet 4.6 shape as module 08: **$223 / 1k** at 0%. At 10% interrupt, tokens stay **$223** if waits stay inside the 5m cache, including a 93/7 approve/reject mix (~+$0.00021). If every wait exceeds 5m, prefix cache-miss tax is **$227 / 1k**. LLM $ is not the story — reviewer FTE and p99 are. I do not include human labor in the token line.

**Q5. What p50/p95/p99 do you put on HITL?**  
Nobody publishes them. Human clock I contract as **30,000 / 180,000 / 600,000 ms** — 30s interactive, 3 min Slack, 10 min expire-deny. Resume path after the Command, no human: middleware **1 / 5 / 20 ms**, Postgres sync **10 / 50 / 200 ms**, end-to-end to ToolNode start **15 / 80 / 400 ms**. I never expire-approve. I measure queue depth and time-to-approve myself.

**Q6. Is HITL Zero-Trust? What about MCP?**  
No. HITL does not authenticate, does not evaluate `(principal, action, resource)`, does not bind args, does not cover unnamed tools. `permissions=` is a fail-open FS path PDP. MCP tools on `tools=` can be named in `interrupt_on` — that is still a review queue. Zero-Trust is a gateway PEP: OAuth 2.1, RFC 8707 audience = canonical server URI, no token passthrough (RFC 8693 exchange), hash-pin tool JSON on every `tools/call`. Elicitation is a different human pause; it does not authorize the client to the server. A HITL click does not mint that token.

**Q7. Inheritance and the interpreter hole.**  
Declarative specs and auto GP inherit parent `interrupt_on` / `permissions`; a spec replaces entirely if set (PR #2334). Compiled and async do not inherit. Interpreter `task()` from `eval` skips parent `interrupt_on` per dispatch — I gate `eval`. `interrupt_on={"task": True}` does not catch JS `task()`. PTC, if enabled, bypasses HITL too. Two resume dialects: HITLRequest `decisions` vs raw `interrupt()` values — the UI must branch.

**Q8. PII — detect → redact → audit on the HITL card.**  
Default description embeds full tool args into the checkpoint, the UI, and traces. I detect with regex plus optional ML before render; redact/mask/hash in a custom `description` factory; keep raw args server-side for the digest bind; block PAN onto Slack/MCP. I audit WORM of approver id, arg digest, decision, cid, thread — not raw PAN. `PIIMiddleware` on the agent does not redact interrupt payloads. If ML is down I still regex-mask the card and I block PAN.

**Q9. Circuit breaker and fallback. What happens on timeout?**  
The library waits forever. My HITL-service breaker is closed → open → half-open. Half-open probe is a reject Command or a health GET — never Approve. Fallback is HITL → deny → refuse. Expire-deny at 10 minutes. Circuit open is deny, not approve. Expire-approve turns TTL into an attacker-controlled delay. I CAS the ticket so double-click cannot double-send.

**Q10. `respond` vs `reject`, `edit`, and TOCTOU.**  
`reject` is status error, tool does not run. `respond` is status success — the model believes the send happened. I do not use `respond` to deny. `edit` can rename the tool; I pin `edited_action.name`. Approve executes in-memory args, not a re-parse of the card; I hash-bind display vs execute (CWE-367). FS deny re-checks after edit; MCP does not unless the gateway does.

**Q11. Coding agent: HITL on `execute` or sandbox only?**  
Sandbox bounds blast radius without a human; HITL bounds when to ask. Prod untrusted: `BaseSandbox`, network off, HITL omitted or only on escapes. Internal copilot: Codex-shaped hybrid — `when` on path/network escape. `LocalShellBackend` is laptop CLI with HITL on **all** ops; `permissions=` cannot constrain shell. I do not replace payments HITL with a classifier (17% FNR analog). For prod infra I put a destructive-action proxy in front of `execute` / cloud MCP; HITL is the UI to that proxy, not the only control.

**Q12. Checkpointer durability and lost resume.**  
`sync` before next step; `async` small hole; `exit` still writes on interrupt but a crash before the interrupt returns can lose the pause. InMemory dies on restart. Resume needs the same `thread_id` and a `Command`, not a new dict. Time travel always re-triggers interrupts and forks — it will not un-send email. Retention must not GC pending interrupts. AISVS is right that checkpoints are not durable execution; I add Temporal or a queue TTL.

---

## Key Numbers to Memorize

### Package / gates / versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **`>=0.5.2`** | `permissions=` |
| **`>=0.6.8`** | Permission `mode="interrupt"` |
| **`langchain>=1.3.3`** | `when` on `InterruptOnConfig` (Python-only vs JS HITL page) |
| **`>=0.7` / `>=0.7.3`** | `delete` tool / exact-match file `delete` |
| **PR #2334** | Inherit parent `interrupt_on` on declarative specs, not only GP |
| **Slot 14** | `HumanInTheLoopMiddleware` tail ([08](08-deep-agents-harness.md)) |
| **255** | Postgres `thread_id` max chars |

### Decisions / fail-open
| Number | What |
| --- | --- |
| **4** | Default `allowed_decisions` when value is `True`: approve, edit, reject, respond |
| **fail-open** | Unnamed `interrupt_on` keys **and** unmatched `permissions=` paths |
| **first-match** | `permissions=` evaluation order; deny-before-interrupt wins |
| **positional** | `decisions` must match `action_requests` order and length |
| **`"error"` / `"success"`** | `reject` vs `respond` synthetic ToolMessage status |

### $ / cache **[inferred]** where marked
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok ([08](08-deep-agents-harness.md)) |
| **$3.75 / $0.30** | 5m cache write / cache read per MTok |
| **5m / 1h** | Default vs long-gap Anthropic cache TTL |
| **[inferred] $223 / 1k** | 0% interrupt, 10-call cached 2k prefix |
| **[inferred] $223 / 1k** | 10% interrupt, waits <5m (tokens unchanged) |
| **[inferred] $227 / 1k** | 10% interrupt, all waits >5m (prefix miss) |
| **[inferred] $0.0378** | Extra input $ per interrupted run after 5m miss (7 × 2k × $2.70/MTok) |
| **[inferred] +$3.78 / 1k** | That miss tax at 10% interrupt rate |
| **[inferred] +$0.00021 / run** | 7% reject extra-call tax on the 10% slice |

### Human / classifier analogs (Anthropic; not a Deep Agents SKU)
| Number | What |
| --- | --- |
| **~93%** | Users approve permission prompts |
| **84%** | Sandbox cut in prompts |
| **17% / 0.4%** | Auto-mode classifier FNR (n=52 overeager) / FPR (n=10,000, two stages) |
| **3 / 20** | Consecutive / total classifier denials before escalate to human |
| **[inferred] 0.8 h / 5 h / ~40 h per day** | Reviewer load at 1k runs: 10%×30s / 10%×3min / every-write 8 cards |

### Latency (numeric ms)
| Number | What |
| --- | --- |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** human clock p50/p95/p99; p99 = expire-deny 10 min |
| **1 / 5 / 20 ms** | **[inferred]** HITL middleware CPU (research: microseconds, rounded) |
| **10 / 50 / 200 ms** | **[inferred policy]** resume checkpoint load/write (`sync`) |
| **15 / 80 / 400 ms** | **[inferred]** `Command` → ToolNode start, no human |
| **600,000 ms** | Anthropic HTTP `API_TIMEOUT_MS` default — **not** HITL |

### Recursion / durability / security
| Number | What |
| --- | --- |
| **9,999** | Compiled `recursion_limit` ([08](08-deep-agents-harness.md)); HITL is not a hop |
| **detect → redact → audit** | PII on HITL UI, checkpoints, traces **before** persist |
| **RFC 8707 / 8693** | MCP resource indicator / token exchange — HITL click does not mint these |
| **CWE-367** | TOCTOU on approved args / FS paths / edits |
| **AISVS C9.2 / 9.2.3** | Interrupt ≠ approval workflow (no TTL/notify); binding + nonce + TTL **not** in Deep Agents |
| **LLM03 #6 / #7** | HITL optional extra / complete mediation (PDP not LLM) |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs. Expire-deny clocks are **your** policy.
