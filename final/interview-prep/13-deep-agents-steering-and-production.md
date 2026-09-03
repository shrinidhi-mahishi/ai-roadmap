# Deep Agents Steering, Human-in-the-Loop & Production

Consolidated from three independent research sources (GPT, Grok, Opus). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01). Research frozen **2026-09-02**.

---

## What Is This?

Human-in-the-loop (HITL) is like having a manager approve expense reports above a certain threshold -- the agent works autonomously on routine tasks but pauses for human review on high-risk actions. A sales agent can look up CRM records freely (Tier 1, read-only), but before it sends a pricing proposal to a customer (Tier 4, irreversible), it stops, shows the human what it wants to do, and waits for approval, edits, or rejection.

**Steering is two planes on one Pregel runtime.** Deep Agents does not add a scheduler. It wires LangChain `HumanInTheLoopMiddleware` onto LangGraph interrupts. `interrupt_on` is a dict: tools you **name** pause; tools you **omit** auto-approve (fail-open). `True` means interrupt with all four decisions; `False` is explicit auto-approve (useful when inheriting a parent map). `InterruptOnConfig` customizes `allowed_decisions`, `description`, `args_schema`, and a `when` predicate. Filesystem `permissions=` `mode="interrupt"` synthesizes those configs for built-in FS tools whose paths match -- still fail-open for unmatched paths, still not MCP/`execute`.

In production, the agent's state is checkpointed to a database. The agent can wait minutes, hours, or days for a human decision. When the human responds, the agent resumes exactly where it left off -- same state, same context, same thread. The mechanism is a "resumable exception": execution raises a resumable exception, state serializes, and a `Command(resume=...)` re-enters the graph with the human's decision.

**Production does not require a new runtime.** `create_deep_agent` returns a LangGraph `CompiledStateGraph`. Production ships **LangSmith Deployment's Agent Server** (API replicas + queue workers + Postgres checkpointer/store + Redis pub/sub) **around that graph**. Going to production is a hosting and durability problem, not a second orchestrator.

Think of a loading dock, not a courthouse. HITL is the **hold** on a pallet the model already labeled. The warehouse still needs a badge reader (IdP), a shipping policy (PDP / gateway PEP), and a sealed crate (sandbox). A human clicking "Approve" on a LangGraph card does not mint an audience-bound token and does not replace RFC 8707.

Think of a restaurant. **The graph is the recipe** (already written). **Agent Server is the kitchen line**: ticket printer (API), cooks (queue workers), walk-in (Postgres), pager (Redis). You do not invent a second stove. You do not let the waiter (`LocalShellBackend`) cook on the pass.

**Thesis:** HITL is a **pause**, not a policy decision point. `HumanInTheLoopMiddleware` batches proposed tool calls, persists graph state, and waits for a human `HITLResponse`. It does not authenticate the approver, does not evaluate `(principal, action, resource)`, does not bind a hash of executed args, does not time out, and does not cover MCP / `execute` / custom tools unless you **name** them in `interrupt_on`. Filesystem `permissions=` is a **first-match, fail-open path PDP for built-in FS tools only**. Neither is Zero-Trust. The model still proposes; code (middleware + checkpointer + **your** resume handler) disposes.

| Gate | Version |
| --- | --- |
| `permissions=` | `deepagents>=0.5.2` |
| Inherit parent `interrupt_on` on declarative specs (not only GP) | PR #2334 |
| `mode="interrupt"` | `>=0.6.8` |
| `when` on `InterruptOnConfig` | `langchain>=1.3.3` (`when` is **Python-only** on the JS HITL page) |
| Exact-match file `delete` | `>=0.7.3` |
| `delete` tool at all | `>=0.7` |
| `excluded_tools` blocks **execution** (not just schema hide) | `deepagents>=0.7.9` |
| `rt.server_info` / `rt.execution_info` namespace factories | `>=0.5.0` |
| `ToolErrorMiddleware` | `langchain>=1.3.14` |
| Model-retry `is_retryable` skip | `langchain>=1.3.16` |

---

## Why It Matters

61% of large enterprises now run at least one production AI agent system (Gartner 2026), up from 18% in 2024. Multi-agent reliability sits at 56.6% task success across 4.5M production runs -- meaning retry, recovery, and human oversight design determine whether an agent system is usable. Director/VP roles require you to design the guardrails, not just the model.

Almost every "human-in-the-loop agent platform" interview now forks here: is HITL authorization, or a durable pause with a review UI? Trap answers: "unnamed tools are denied," "`permissions=` covers MCP," "LangGraph times out the card," "compiled children inherit `interrupt_on`," "`interrupt_on={"task": True}` catches interpreter `task()`," "`respond` is how I reject a send," "HITL timeout auto-approves so the user isn't blocked."

Almost every "how do you productionize Deep Agents?" interview forks here: is production a new runtime, or Agent Server around the same `CompiledStateGraph`? Trap answers: "Deep Agents ships Temporal," "disconnect cancels the run," "Last-Event-ID resumes protocol v2," "`permissions=` is the MCP gateway," "bind `recursionLimit: 10000`," "use LocalShell with `virtual_mode`," "skip `thread_id` if you have `context`."

Anthropic's coding-agent analog (not a Deep Agents SKU): users approve **~93%** of permission prompts; sandboxing cut prompts **84%**; auto-mode classifier on real overeager n=52: **17% FNR**; traffic n=10,000: **0.4% FPR** after two stages. That is the cost of **not** using sandbox + policy: either human FTE (fatigue) or a second model (residual miss). Deep Agents has **no** built-in auto-mode.

---

## Architecture / System Design

### Steering System Topology & Data Flow

Steering sits on the same `CompiledStateGraph` as the harness. Construction / `interrupt_on` / `permissions` / checkpointer / resume authz are **control** (LLM-free for pause/resume routing). Proposed `tool_call` name/args/id, `HITLRequest.action_requests`, edited args, and `respond` bodies are **data** (untrusted model-authored tokens plus whatever the human types).

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  LangSmith traces (interrupt payloads if you log them)                |
         |  GraphOutput.interrupts / stream.interrupted  (version v2 / v3)      |
         |  get_state_history / checkpoint_id  (replay re-triggers HITL)         |
         |  WORM you build: (cid, thread_id, interrupt_id, tool,                |
         |    args_digest, decision, actor_id, ts, policy_version)              |
         |  Queue metrics: depth, time-to-approve, expire-deny count            |
         |  PII audit: detect->redact->audit on HITL UI + checkpoint args       |
         +------^------------------^---------------------^---------------------+
                | spans            | queue               | decision log
                |                  |                     |
+---------------+------------------+---------------------+---------------------+
| CONTROL PLANE  (LLM-free routing; identity is NOT here unless you add it)    |
|                                                                              |
|  interrupt_on: dict[str, bool | InterruptOnConfig]   (unnamed = approve)     |
|  permissions= FilesystemPermission(ops, paths, mode=allow|deny|interrupt)    |
|  _merge_fs_interrupt_on(fs, user)  -- user wins PER TOOL NAME               |
|  HumanInTheLoopMiddleware  (tail slot 14; auto-install if mode=interrupt)    |
|  when: Callable[[ToolCallRequest], bool]   allowed_decisions non-empty       |
|  checkpointer + thread_id (<255)   Command(resume={"decisions": [...]})      |
|  YOUR expire-deny timer / CAS ticket / resume RBAC  (not in the library)    |
|  PatchToolCallsMiddleware ALWAYS before HITL (dangling tool_calls repair)    |
+-------------------------------+----------------------------------------------+
                                | interrupt() raises; graph waits forever
                                v
+------------------------------------------------------------------------------+
| DATA PLANE  (untrusted -- model proposed the call; human/code dispose)       |
|                                                                              |
|  AIMessage.tool_calls -> after_model filter -> HITLRequest                   |
|    action_requests[] = {name, args copy, description}  (display snapshot)    |
|    review_configs[]  = {action_name, allowed_decisions, args_schema?}        |
|  Resume: approve|edit keep ToolCall; reject|respond inject ToolMessage       |
|  Execution uses in-memory ToolCall (or edited replacement), NOT a            |
|    re-parse of the interrupt payload. Tokens for that model turn are sunk.   |
|                                                                              |
|  +------------- TOOL PROXIES (HITL is optional extra, not the PEP) --------+ |
|  | Built-in FS: ls read_file write_file edit_file glob grep delete         | |
|  |   wrap_tool_call: deny still binds AFTER human edit                     | |
|  | execute / sandbox: permissions= CANNOT constrain; name in interrupt_    | |
|  |   on or omit HITL if sandbox is the PDP                                | |
|  | MCP / custom on tools=: name in interrupt_on to pause; gateway PEP     | |
|  |   still required (OAuth 2.1 + RFC 8707). permissions= does NOT apply   | |
|  | Interpreter task() / PTC tools.*: skip parent interrupt_on -- gate      | |
|  +------------------------------------------------------------------------+ |
+--------+----------------+-----------------+-----------------+----------------+
         |                |                 |                 |
         v                v                 v                 v
+------------------------------------------------------------------------------+
| PERSISTENCE LAYER  (the HITL database -- no product TTL)                     |
|                                                                              |
|  +--------------+ +--------------+ +-------------+ +------------------+      |
|  | Checkpointer | | thread_id    | | Durability  | | Approval ticket  |      |
|  | Postgres /   | | uuid7()      | | exit|async| | | (YOUR CAS; not   |      |
|  | Mongo /      | | < 255 chars  | | sync        | |  in LangGraph)   |      |
|  | MemorySaver  | | reuse=resume | | interrupt = | | TTL job sends    |      |
|  | = RAM, gone  | | new id=empty | | an "exit"   | | reject Command   |      |
|  +--------------+ +--------------+ +-------------+ +------------------+      |
|  Nested declarative children inherit parent checkpointer. Async children     |
|  = second interrupt domain on the remote thread. Retention cron MUST NOT     |
|  delete threads with next waiting on interrupt.                              |
+------------------------------------------------------------------------------+
```

### Production Architecture Diagram

```
 +----------------------------------------------------------------------+
 |                        CONTROL PLANE                                 |
 |  +---------------+  +--------------+  +----------------------------+ |
 |  |  LangSmith    |  |  Helm/K8s    |  |  Auth Provider (OAuth2)    | |
 |  |  Observability|  |  Orchestrator|  |  RBAC + Token Refresh      | |
 |  +------+--------+  +------+-------+  +------------+---------------+ |
 |         |                  |                        |                 |
 +---------+------------------+------------------------+-----------------+
           |                  |                        |
 +---------+------------------+------------------------+-----------------+
 |         |           DATA PLANE                      |                 |
 |         v                  v                        v                 |
 |  +-------------------------------------------------------------------+|
 |  |                   Agent Server (API + Workers)                    ||
 |  |  +-----------+  +----------------+  +-------------------+         ||
 |  |  | Deep Agent|  | HITL Middleware |  | Permission Engine |         ||
 |  |  |  (Graph)  |--|  interrupt_on  |--|  First-Match-Wins |         ||
 |  |  +-----+-----+  +-------+--------+  +-------------------+        ||
 |  |        |                |                                         ||
 |  |        |    +-----------v-----------+                             ||
 |  |        |    |  Checkpoint Manager   |                             ||
 |  |        |    |  (serialize/resume)   |                             ||
 |  |        |    +-----------+-----------+                             ||
 |  |        |                |                                         ||
 |  +--------+----------------+----------------------------------------+||
 |           |                |                                          |
 |  +--------v--------+  +---v--------------+  +------------------+     |
 |  |  Redis (pub-sub) |  |  PostgreSQL      |  |  Object Store    |    |
 |  |  Stream broker   |  |  Checkpoints     |  |  (S3/GCS)        |    |
 |  |  (no payloads)   |  |  Threads + Runs  |  |  Large payloads  |    |
 |  |  wake + cancel   |  |  Task queue      |  |  Documents       |    |
 |  |                  |  |  Long-term memory |  |                  |    |
 |  +------------------+  +------------------+  +------------------+    |
 +----------------------------------------------------------------------+
           |                |                       |
 +---------v----------------v-----------------------v------------------+
 |                       TELEMETRY PLANE                               |
 |  +--------------+  +--------------+  +--------------------------+   |
 |  |  Trace Export |  |  Metrics     |  |  Audit Log               |   |
 |  |  (async, zero |  |  P50/P99    |  |  Every interrupt/resume  |   |
 |  |   app latency)|  |  cost/token |  |  + decision + identity   |   |
 |  +--------------+  +--------------+  +--------------------------+   |
 +---------------------------------------------------------------------+
```

### Agent Server Data Plane Detail

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  Cloud: traces -> project named after the deployment (automatic)      |
         |  Local: LANGSMITH_TRACING + LANGSMITH_API_KEY (+ PROJECT)             |
         |  Filter: metadata.lc_agent_name ; ls_integration=deepagents           |
         |  Engine (opt.): every 6 h, meters in LCUs; Polly -> online evals     |
         |  Audit logs: create/update/delete_deployment (actor, ts, OCSF 6003)  |
         |  PII: detect->redact->audit BEFORE traces/checkpoints                |
         |  Dual-SDK OTel+LangSmith = duplicate trees / double-bill             |
         +------^------------------^---------------------^---------------------+
                | spans            | Engine / evals      | audit events
                |                  |                     |
+---------------+------------------+---------------------+---------------------+
| CONTROL PLANE  (LLM-free: deploy + run-config; identity is NOT the model)    |
|                                                                              |
|  LangSmith UI / langgraph deploy / mda deploy                                |
|  langgraph.json: dependencies, graphs (id->"./file.py:export"), env,         |
|    optional auth.path="./auth.py:auth"                                       |
|  Revision: git SHA / uploaded archive / image; env snapshot for rollback     |
|  Workspace secrets; @auth module; deployment type/size (type IMMUTABLE)      |
|  Workspace RBAC (Admin/Editor/Viewer -- Enterprise; else everyone Admin)     |
|  Run submit knobs: thread_id, assistant_id, context, durability,             |
|    on_disconnect, multitask_strategy, recursion_limit                        |
+-------------------------------+----------------------------------------------+
                                | desired-state / revision
                                v
+------------------------------------------------------------------------------+
| DATA PLANE  (untrusted model+tools live on WORKERS, not on API replicas)     |
|                                                                              |
|  +--------------+  persist pending run (no graph exec in split/cloud)        |
|  | Agent Server |----------------------------------------------------+      |
|  | API replicas |  /threads /runs /stream SSE /join /cancel           |      |
|  | (stateless;  |  store CRUD; MCP+A2A INGRESS; webhooks             |      |
|  | no session   |                                                    |      |
|  | stickiness)  |<-- Redis PubSub (stream/cancel; no payloads)-------+      |
|  +------+-------+                                                    |      |
|         | Redis wake-up sentinel (list; no run payload)               |      |
|         v                                                            |      |
|  +--------------------------------------------------------------+   |      |
|  | QUEUE WORKERS  (N_JOBS_PER_WORKER default 10 RUN slots)       |   |      |
|  |  claim lease -> load graph (compiled once at container        |   |      |
|  |    start, or async factory EVERY run) -> super-steps ->       |   |      |
|  |    checkpoint at durability cadence -> publish events          |   |      |
|  |  AT MOST ONE RUN PER thread_id                                |   |      |
|  |  heartbeat timestamp in Redis; SIGINT grace then requeue      |   |      |
|  |  HITL interrupt(): worker RELEASES slot; sleep unbounded      |   |      |
|  +--------+--------------+------------------+--------------------+   |      |
|           |              |                  |                        |      |
|  +--------+----- TOOL PROXIES (least privilege -- not host) -------+ |      |
|  | Sandbox BaseSandbox / LangSmithSandbox / Daytona + AUTH PROXY   | |      |
|  | MCP EGRESS: gateway PEP still required (permissions= != MCP)    | |      |
|  | MCP/A2A INGRESS: free with deploy; same @auth as /runs          | |      |
|  | Cron: stateful (append thread) vs stateless (new thread)        | |      |
|  | NEVER LocalShellBackend / host FilesystemBackend                | |      |
|  +----------------------------------------------------------------+ |      |
+---------+----------------+-----------------+-----------------------+ |      |
          |                |                 |                         |
          v                v                 v                         |
+-------------------------------------------------------------------------+   |
| PERSISTENCE LAYER                                                        |   |
|  +--------------------+  +--------------------+  +---------------------+ |   |
|  | Postgres (default) |  | Redis              |  | Optional Mongo      | |   |
|  | assistants,threads,|  | wake sentinel list |  | checkpoints ONLY    | |   |
|  | runs, crons ALWAYS |  | cancel pub/sub     |  | Postgres STILL req. | |   |
|  | checkpoints dflt   |  | stream pub/sub     |  | for threads/runs/   | |   |
|  | store dflt         |  | attempt counter    |  | assistants          | |   |
|  | thread_id < 255    |  | NO user/run bytes  |  |                     | |   |
|  | EncryptedSerializer|  | prolonged outage = |  | InMemorySaver =     | |   |
|  +--------------------+  | Agent Server down  |  | prototype only      | |   |
|                          +--------------------+  +---------------------+ |   |
|  Do NOT pass checkpointer=/store= in graph code on Agent Server -- the   |   |
|  server injects (and REPLACES) whatever the app configured.              |   |
+--------------------------------------------------------------------------+   |
```

### Control vs Data Plane Summary

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control (steering)** | `interrupt_on`, `permissions` (incl. `mode="interrupt"`), HITL middleware assembly, checkpointer / `thread_id`, `Command(resume=...)`, `when`, `allowed_decisions`, your TTL + resume RBAC | **Yes** for pause/resume routing. Predicates are your code. Approver identity is **not** in this plane unless you add it | Treating HITL as the PDP; putting allow/deny only in the system prompt |
| **Data (steering)** | Proposed `tool_call` name/args/id, `HITLRequest.action_requests` (args copied into the interrupt payload), edited `args` on resume, rejection `message`, `respond` synthetic `ToolMessage` body | No -- untrusted model-authored tokens, plus whatever the human types | Showing raw PII on the card; executing the display snapshot instead of the in-memory `ToolCall` without a hash bind |
| **Control (deploy)** | UI / `langgraph deploy` / `mda deploy`; `langgraph.json`; revision; workspace secrets; `@auth` path; type/size; RBAC who may create/update/delete deployments | Yes | |
| **Control (run config)** | `thread_id`, `assistant_id`, `context` / `context_schema`, `durability`, `on_disconnect`, `multitask_strategy`, `recursion_limit` on submit | Yes | |
| **Data (Agent Server API)** | HTTP: create thread/run, `/stream` SSE, `/join`, cancel, store CRUD. **Does not execute the graph** in split/cloud mode | Yes for routing | |
| **Data (queue workers)** | Load graph, acquire lease, run super-steps, write checkpoints, publish stream events | No -- untrusted model+tools | |

### Request Flow Narrative: HITL Path

1. **Client submits** a message with `thread_id` and `context` (user identity, feature flags).
2. **LangGraph Server** loads or creates the thread, hydrates state from the last checkpoint.
3. **Model turn (data, tokens already spent).** The model returns an `AIMessage` with `tool_calls`. HITL fires in `after_model`, **after** that completion. The tool has **not** run. Human think-time burns **$0 of tokens** and **100% of wall-clock SLO**.
4. **Filter (control).** `HumanInTheLoopMiddleware.after_model` inspects each call. Tools **absent from the map are auto-approved**. Tools present as `False` are auto-approved. Tools present as `True` / `InterruptOnConfig` are candidates; a `when` predicate returning `False` **auto-approves and excludes the call from the batch**.
5. **Batch interrupt.** Remaining candidates become one `HITLRequest` (`action_requests` + `review_configs`). `interrupt(hitl_request)` raises into the runtime. The checkpointer writes the thread. The graph **waits indefinitely**. API returns `GraphOutput.interrupts` when `invoke(..., version="v2")`.
6. **Human / queue (your plane).** UI or Slack shows cards. Args in `action_requests` are a **copy for display**. `review_configs` is keyed by `action_name` (tool name), not by tool-call id. Decisions are **positional** on `action_requests`. Do not zip by name. Default description embeds **full tool args**.
7. **Resume (control).** Same `thread_id`. **Must** be `Command(resume={"decisions": [...]})`. `Command(update=...)` / `goto` are for returning from **nodes**, not for driving `invoke` on a paused HITL. Passing a new input dict is a **new invocation**, not a resume. Order **must** match `action_requests`; length mismatch raises `ValueError`.
8. **Apply decisions.** `approve` keeps the original `ToolCall` (identity preserved). `edit` substitutes **new** name+args with the **same** `tool_call["id"]`. `reject` does **not** run the tool; synthetic `ToolMessage` with status `"error"`. `respond` does **not** run the tool; synthetic body with status `"success"` -- the model treats it as a successful result. **Do not** use `respond` to deny.
9. **Execute.** `ToolNode` runs remaining calls. `FilesystemMiddleware.wrap_tool_call` still runs -- deny-mode permissions **re-check** edited args. MCP/custom tools have **no** such backstop.
10. **Stream events** flow through Redis pub-sub to the frontend in real time.
11. **Traces export** asynchronously to LangSmith with zero application latency impact.

### Request Flow Narrative: Production Path (API -> Queue -> Graph)

1. **Admit.** Client hits an API replica: create thread (if needed) and `runs.create` / `runs.stream` with `thread_id` + `context` + assistant id. Payload cap **25 MB** -> HTTP **413**.
2. **Durable queue.** API persists a **pending** run in Postgres. Redis sentinel **wakes** a worker. Redis does **not** carry the run payload.
3. **Lease.** A worker claims the lease (Postgres MVCC; exactly-once **attempt** semantics). **At most one run per `thread_id` at a time.** Worker loads the compiled graph (already in memory) or calls the async factory.
4. **Super-steps.** LangGraph runs. Checkpoints write at the deployment's durability cadence (`"async"` is Agent Server default). Worker publishes events on Redis PubSub.
5. **Stream.** Any API replica with an open `/stream` subscribes via Redis and forwards **SSE**. Protocol v2 is **POST**; resume cursor is body field **`since`** -- **not** `Last-Event-ID`.
6. **Disconnect.** Default: **run keeps going**. Opt-in `on_disconnect="cancel"` on wait/stream/join. Rejoin: same `thread_id` + `since`.
7. **Stop / release.** HITL `interrupt()` is **not** cancel: worker **releases the slot**, sleep unbounded, resume via `Command(resume=...)`. Cancel **interrupt** (default): status `interrupted`; checkpoints kept. Cancel **rollback**: delete run + its checkpoints.

---

## Core Concepts & Algorithms

### HITL Invariants

**I1.** HITL is a **review queue** over **named** tools. Unnamed tools auto-approve. `permissions=` is a path PDP for **built-in FS tools only**, first-match, **no match -> allow**. Neither authenticates a principal. Neither is Zero-Trust.

**I2.** Checkpointer + `thread_id` are the HITL substrate. `MemorySaver` = RAM. LangGraph waits **forever** unless **you** expire-deny.

**I3.** `after_model` is **batch**. One human round-trip per model turn that has >=1 gated call.

**I4.** User `interrupt_on` **wins per tool name** over permission-generated configs. `{"write_file": False}` disables `/secrets/**` interrupts. `{"write_file": True}` **drops the `when` predicate** -- every write pauses.

**I5.** Declarative children **inherit** parent `interrupt_on` / `permissions` (spec **replaces** entirely if set; PR #2334 fixed GP-only inheritance). `CompiledSubAgent` / `AsyncSubAgent` **do not inherit**. Interpreter `task()` inside `eval` does **not** enforce parent `interrupt_on` per dispatch -- gate `eval`.

**I6.** Side effects **before** `interrupt()` in a node re-run on resume (the entire graph node restarts). Bare `except Exception` swallowing the interrupt exception -> **no pause, execution continues**.

### Production Invariants

**I7.** Deep Agents introduces **no** production runtime. Durable execution, streaming, interrupts, checkpoints, stores, crons, MCP/A2A ingress = **Agent Server / LangGraph**.

**I8.** API replicas **route**. Queue workers **execute**. Coupling them in your head produces "the HTTP timeout is my agent SLO" -- false for a 20-minute research run.

**I9.** **One run per `thread_id`** is the concurrency boundary.

**I10.** `thread_id` and `context` are independent. Missing `thread_id` means every `useStream` mount is a new conversation.

**I11.** Do not pass `checkpointer=` / `store=` in graph code on Agent Server -- injected and any app-configured checkpointer **replaced**.

**I12.** `permissions=` covers **built-in FS tools only**. Production still needs the MCP gateway.

### `interrupt_on` Mapping

Type: `dict[str, bool | InterruptOnConfig]`.

| Value | Meaning |
| --- | --- |
| **Omitted key** | Auto-approve. HITL is **fail-open** for unnamed tools |
| `True` | Interrupt. Default `allowed_decisions = ["approve", "edit", "reject", "respond"]` |
| `False` | Explicit auto-approve (disable one tool when inheriting a parent map) |
| `InterruptOnConfig` | Custom. **Required:** non-empty `allowed_decisions`. Optional: `description` (str or factory), `args_schema`, `when` |

Official Deep Agents example:

```python
interrupt_on={
    "remove_file": True,  # all four decisions
    "fetch_file": False,  # never pause
    "notify_email": {"allowed_decisions": ["approve", "reject"]},  # no edit, no respond
}
```

Risk-tiered pattern:

| Risk | Pattern |
| --- | --- |
| High (delete, send_email) | `["approve", "edit", "reject"]` |
| Medium (write_file) | `["approve", "reject"]` -- no silent arg rewrite |
| Must-run (rare) | `["approve"]` only -- human cannot reject **in-band**. TTL job must send an **allowed** type |
| Low (read_file, ls) | `False` |

### Four Decision Types

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
| `approve` | Yes, original args | None from HITL; real tool result later | -- | Send as drafted |
| `edit` | Yes, **new** name+args; **same** `tool_call["id"]` | None from HITL | -- | Change recipient; keep id so the pending call is satisfied |
| `reject` | **No** | Synthetic; default text says do not retry unless user asks | `"error"` | Deny side effects. For destructive tools, pass a domain-specific `message` |
| `respond` | **No** | Synthetic with human `message` | `"success"` | Human **is** the tool (`ask_user`). **Do not** use to deny -- the model treats it as success |

**Rejection overhead**: Vague rejections ("no, do something else") trigger 2-3 additional LLM calls as the model explores alternatives. Well-crafted rejections with domain-specific guidance resolve in a single retry.

`edited_action.name` may differ from the original -- `edit` is **tool-renaming power**. Restrict `edit` on irreversible tools; validate `edited_action.name == action.name` in your resume handler -- middleware does not.

### `when` Predicate

Requires `langchain>=1.3.3`. Signature: `Callable[[ToolCallRequest], bool]`. `True` -> interrupt; `False` -> **auto-approve, never enter the batch**.

`ToolCallRequest` at `after_model` (batch): `tool_call` dict, `state`, `runtime`; **`tool=None`** (no `BaseTool` instance at this hook). Do not write predicates that need `request.tool`.

```python
def writes_outside_workspace(request: ToolCallRequest) -> bool:
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")

interrupt_on = {
    "write_file": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "when": writes_outside_workspace,
    },
}
```

**Security of `when`:** a buggy predicate that returns `False` on a destructive path is a **silent auto-approve**. Exception inside `when` fails the node, **not** fail-closed to interrupt. Wrap predicates. FS write tools use **`file_path`**, not `path` -- a copy-paste `path` silently auto-approves every write.

### Permission Model (First-Match-Wins)

`FilesystemPermission(operations, paths, mode)`:

| Field | Values |
| --- | --- |
| `operations` | `"read"` -> `ls`, `read_file`, `glob`, `grep`. `"write"` -> `write_file`, `edit_file`, `delete` |
| `paths` | Globs, `**`, `{a,b}` alternation |
| `mode` | `"allow"` (default), `"deny"`, `"interrupt"` (`>=0.6.8`) |

**Evaluation:** declaration order, **first match wins**. **No match -> allow** (fail-open) -- the opposite of IAM default-deny. Coverage: built-in FS tools only. **Not** custom tools, **not** MCP tools, **not** sandbox `execute`.

**Critical ordering requirement** -- specific denies must precede general allows:

```python
permissions = [
    # Rule 1: Block secrets (matches first)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/.env"], mode="deny"),
    # Rule 2: Allow workspace (matches second)
    FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
    # Rule 3: Block everything else
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
]
```

A missing catch-all `deny` on `/**` is a production hole. Subagent `permissions` **replaces** parent -- an auditor spec that omits the catch-all is **more** privileged than a parent that had it.

| Config | Actual behavior |
| --- | --- |
| `mode="interrupt"` on `/secrets/**` only | `when` gates; other writes auto-run (fail-open) |
| Same + `interrupt_on={"write_file": True}` | **Every** write pauses (`when` replaced) |
| Same + `interrupt_on={"write_file": False}` | **No** write pauses, including `/secrets/**` |
| Deny `/secrets/**` **before** interrupt rule | Interrupt never fires; deny error instead -- often what you want |

### Four-Tier Action Risk Classification

| Tier | Category | Examples | Approval Policy |
| --- | --- | --- | --- |
| 1 | Read-only | Queries, retrievals, analysis | Fully autonomous |
| 2 | Reversible writes | Draft creation, internal state | Autonomous with audit logging |
| 3 | External side effects | Third-party API calls, emails | Staging queue or confidence-based review |
| 4 | High-risk/Irreversible | Production deploys, payments, data deletion | Mandatory human approval, no exceptions |

Enforcement must happen at the **workflow execution layer**, not negotiated by the AI at runtime. The agent should never decide its own oversight level.

### Inheritance, Interpreter Skip, Two Resume Dialects

| Spec | `interrupt_on` | `permissions` |
| --- | --- | --- |
| Main agent | Always applied | First-match list |
| Declarative `SubAgent` / auto GP | **Inherits** parent; spec **overrides** entirely if set | Inherits; spec **replaces** entirely |
| `CompiledSubAgent` | **Does not inherit.** Wire `interrupt()` inside the runnable | Owned by the runnable |
| `AsyncSubAgent` (remote) | **Does not inherit.** Configure on the remote agent | Remote |
| Interpreter `task()` inside `eval` | Parent `interrupt_on` **not enforced per dispatch**. Gate `eval` | Child still compiled with its spec |

Two payload dialects on one parent:

1. **HITL middleware:** one `Interrupt` whose `.value` is `HITLRequest`; resume `{decisions: [..]}` positional.
2. **Raw `interrupt()` in a compiled tool:** one `Interrupt` per call; resume is the raw object. Branch on payload keys (`action_requests` vs `type`/`action`).

### Four Retry Layers (Production)

Deep Agents **does not** install retry middleware by default -- you append it.

| Layer | Default | Retries what | Does **not** retry |
| --- | --- | --- | --- |
| **1. Chat model HTTP** | `init_chat_model(..., max_retries=6)` | 429 / 5xx / network | 401 / 404 |
| **2. LangGraph node `RetryPolicy`** | `max_attempts=3` (includes first); `initial_interval=0.5s`; `backoff_factor=2.0`; `max_interval=128s`; `jitter=True` | Exceptions matching `retry_on`. HTTP **5xx only**. `NodeTimeoutError` **is** retryable | `ValueError`, `TypeError`, `ArithmeticError`, `ImportError`, `LookupError`, `NameError`, `SyntaxError`, `RuntimeError`, `ReferenceError`, `StopIteration`, `OSError` (+ subclasses) |
| **3. Agent Server run attempt** | **3** attempts for **transient Postgres errors** during the run | PG blips during the run | Model 429s. This is **not** a model retry |
| **4. Middleware** `ModelRetryMiddleware` / `ToolRetryMiddleware` | `max_retries=2` => **3 total attempts**; jitter +/-25% | Model: retryable + unclassified. Tool: optional `tools=[...]` allowlist | Tools you did not allowlist |

Call caps (runaway loops) are a **fifth** concern, not a retry layer: `ModelCallLimitMiddleware.run_limit` vs `.thread_limit` (needs checkpointer); `ToolCallLimitMiddleware.run_limit`. `recursion_limit` counts LangGraph **super-steps**, not model calls.

### `recursion_limit`: 9,999 vs 10000 Sentinel

`create_deep_agent` binds `recursion_limit: 9_999`. Frontend examples pass **10000**. LangGraph `merge_configs` has historically **dropped** `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10000) -- binding 10000 is a **no-op**. Hitting the limit is `GraphRecursionError` -- a **hard error**, not graceful degrade.

### Streaming: `on_disconnect`, Protocol v2, Dualism Trap

| Event | Worker | Client |
| --- | --- | --- |
| TCP/SSE drop, **default** | Continues to completion / interrupt | Misses live tokens until rejoin |
| `on_disconnect="cancel"` | Cancel requested | Avoids zombie spend |
| `stream.disconnect()` | Continues | Intentional background |
| Rejoin | Unchanged | `thread_id` + seq `since`; SDK auto |

**Protocol dualism (interview trap).** Blog describes **`Last-Event-ID`** header. Protocol v2 docs: POST-only SSE, **no** `Last-Event-ID`, client sends **`since`** in JSON body. Implement whatever the SDK version you pin actually sends; verify with a disconnect test.

**Double-texting** (new input while a run is `running`):

| Strategy | Effect |
| --- | --- |
| **`enqueue` (default)** | Queue the new run; no state corruption |
| **`reject`** | 409-class refuse until current run ends |
| **`interrupt`** | Halt, **keep** checkpoints, start new input from that state |
| **`rollback`** | Halt and **delete** the in-flight run's checkpoints |

### Durable Execution: Agent Server = LangGraph + Postgres + Redis

The Temporal-shaped story for interviews:

| Temporal analog | Agent Server / LangGraph |
| --- | --- |
| Workflow id | `thread_id` (< 255 chars) |
| Event history | Checkpoints (`"async"` / `"sync"` / `"exit"`) |
| Activity worker | Queue worker; `N_JOBS_PER_WORKER=10` |
| Task queue | Postgres pending runs + Redis sentinel |
| Sticky execution | **None** -- stateless replicas |
| Signal / query | Redis cancel/stream pub/sub; `/join`; `Command(resume=...)` |
| Retry policy | Four layers -- **not** one Temporal retry |
| Continue-as-new | Thread TTL / new thread (stateless cron) |

### Durability Modes

| Mode | When it writes | Failure implication |
| --- | --- | --- |
| `"async"` **(Agent Server default)** | After each step, async vs next step | Small crash window: last step may be lost |
| `"sync"` | Before next step | Highest durability, extra latency |
| `"exit"` | Only on graph exit (success, error, interrupt) | Fast; **no mid-run crash recovery** |

Worker-crash reclaim: sweeper interval **2 minutes**; re-enqueue; another instance resumes from last checkpoint. Instances are **stateless**; **no session stickiness**.

### What Checkpointing Does NOT Provide (the Durable Execution Gap)

| Gap | Consequence | Mitigation |
| --- | --- | --- |
| No failure detection | Process crashes silently -- no supervisor, no heartbeat | External health checks, K8s liveness probes |
| No duplicate prevention | Two processes can resume same `thread_id` simultaneously | External distributed locking (Redis/Postgres advisory locks) |
| Single-process architecture | No task queue, no worker pool, no placement logic | External job scheduler or Agent Server |
| Manual recovery | Developers must detect failures and trigger resumption | Dead-letter queue with monitoring |
| Replay non-determinism | `datetime.now()` or live API reads differ on replay | Idempotency keys, deterministic node design |

**The structural insight** (Diagrid): "The gap is between saving state and guaranteeing completion. Adding a better checkpointer doesn't close the gap."

### Static Breakpoints vs `interrupt()` vs Functional `@task`

| | Static `interrupt_before` / `interrupt_after` | `interrupt()` / HITL middleware |
| --- | --- | --- |
| Where | Before/after named nodes | Inside `after_model` or a tool |
| Payload | None (empty pause) | `HITLRequest` or any JSON |
| Resume | `invoke(None, config)` | `Command(resume=...)` |
| Conditional | All invocations of that node | `when` / application `if` |
| Parallel tools | Pauses the whole tools node | Batches only gated calls |

Static breakpoints are **not recommended for HITL**. Mixing `interrupt_before=["tools"]` with HITL middleware double-pauses.

### Cloud SKUs

| Type | Scaling | Database | Intended |
| --- | --- | --- | --- |
| **Serverless** S/M/L | Scale-to-zero after inactivity; wake on next request | Shared multi-tenant | Background, latency-tolerant, preview |
| **Dedicated** S/M/L | Always-on; autoscale replicas | Dedicated Postgres, backups, HA | Customer-facing critical path |

| Resource | Srv S | Srv M | Srv L | Ded S | Ded M | Ded L |
| --- | --- | --- | --- | --- | --- | --- |
| Runtime vCPU | 1 | 2 | 4 | 3 | 5 | 10 |
| Runtime GiB | 2 | 5 | 9 | 6 | 12 | 24 |
| DB vCPU | -- | -- | -- | 1 | 2 | 4 |
| DB GiB | -- | -- | -- | 4 | 8 | 16 |

**Immutable:** deployment type cannot change after create; **size can** (new revision, no downtime). Deployment type (Serverless/Dedicated) is permanent. MDA is **US Cloud only**.

Two hosted paths, same runtime underneath:

| Path | What it is | Auth / tenancy |
| --- | --- | --- |
| **Managed Deep Agents (MDA)** | CLI-first hosted runtime | LangSmith key or Supabase-class identity; limited custom routes |
| **LangSmith Deployment (direct)** | `langgraph.json` + graph export | Custom `@auth.authenticate` + resource filters; Agent Auth OAuth; workspace RBAC |

### Hosting vs Tracing Destination

| Option | Tracing | License check |
| --- | --- | --- |
| Cloud | Required -> LangSmith SaaS | API key vs SaaS |
| Hybrid | Optional: off or SaaS | API key vs SaaS |
| Self-hosted | Off, SaaS, or self-hosted LangSmith | Air-gapped key or platform key |

---

## Code Examples

### Complete HITL Agent with Interrupt Configuration

```python
"""
Production HITL agent with tiered interrupt configuration,
conditional gating, and permission-based filesystem access.
"""

from uuid import uuid4
from dataclasses import dataclass
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt, Command
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def read_file(file_path: str) -> str:
    """Read a file from the workspace."""
    with open(file_path, "r") as f:
        return f.read()

@tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    with open(file_path, "w") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {file_path}"

@tool
def delete_file(file_path: str) -> str:
    """Delete a file from the workspace. Irreversible."""
    import os
    os.remove(file_path)
    return f"Deleted {file_path}"

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    return f"Email sent to {to}: {subject}"

@tool
def request_human_input(question: str) -> str:
    """Ask the human operator a question and wait for their response."""
    response = interrupt({
        "type": "question",
        "question": question,
    })
    return response.get("answer", "No answer provided")


# ---------------------------------------------------------------------------
# Conditional interrupt predicate
# ---------------------------------------------------------------------------

def writes_outside_workspace(request) -> bool:
    """Only interrupt file writes that target paths outside /workspace/."""
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    user_id: str
    org_id: str
    environment: str  # "staging" or "production"


DB_URI = "postgresql://user:pass@localhost:5432/langgraph_prod"

with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    from langchain_deepagents import create_deep_agent, FilesystemPermission

    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=[read_file, write_file, delete_file, send_email, request_human_input],

        # Tiered interrupt configuration
        interrupt_on={
            "read_file": False,                           # Tier 1: autonomous
            "write_file": {                               # Tier 2/3: conditional
                "allowed_decisions": ["approve", "edit", "reject"],
                "when": writes_outside_workspace,
            },
            "delete_file": True,                          # Tier 4: always interrupt
            "send_email": {                               # Tier 3: external side effect
                "allowed_decisions": ["approve", "edit", "reject"],
            },
        },

        # Filesystem permissions (first-match-wins)
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/.env", "/workspace/.secrets/**"],
                mode="deny",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=["/config/**"],
                mode="interrupt",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],

        checkpointer=checkpointer,
        context_schema=AgentContext,
    )
```

### Approval Handler with Escalation and Stale-Execution Guard

```python
"""
Production approval handler: processes interrupts, applies tiered
escalation, enforces SLAs, prevents stale execution.
"""

import hashlib
import json
import time
from datetime import datetime, timedelta


def compute_action_hash(action_requests: list[dict]) -> str:
    """Deterministic hash of proposed actions for stale-execution detection."""
    canonical = json.dumps(action_requests, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def classify_escalation_tier(action_requests: list[dict]) -> int:
    """Map action requests to escalation tier based on tool risk."""
    tier_map = {"delete_file": 3, "send_email": 2, "write_file": 1, "read_file": 0}
    return max(tier_map.get(ar["tool_name"], 1) for ar in action_requests)


ESCALATION_SLAS = {
    1: timedelta(hours=4),      # Moderate-confidence actions
    2: timedelta(hours=1),      # Low-confidence / high-blast-radius
    3: timedelta(minutes=15),   # Compliance-sensitive
}


def handle_interrupt(result, config: dict, agent, stored_hash: str | None = None):
    """
    Process an interrupt result. Returns the resumed agent result.
    In production, the human decision step is an async queue (Slack, email, web UI).
    """
    if not result.interrupts:
        return result

    action_requests = result.interrupts[0].value["action_requests"]
    current_hash = compute_action_hash(action_requests)

    # Stale execution guard
    if stored_hash and current_hash != stored_hash:
        decisions = [{
            "type": "reject",
            "message": "Action context has changed since original proposal. Re-evaluate.",
        }]
    else:
        tier = classify_escalation_tier(action_requests)
        if tier <= 1:
            decisions = [{"type": "approve"} for _ in action_requests]
        elif tier >= 3:
            decisions = [{
                "type": "reject",
                "message": "Tier 3 action requires explicit human review. "
                           "Queued for on-call approval.",
            } for _ in action_requests]
        else:
            decisions = [{"type": "approve"} for _ in action_requests]

    return agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        version="v2",
    )
```

### Steering Runtime with Circuit Breaker, PII, and CAS

```python
#!/usr/bin/env python3
"""HITL steering: pause is not a PDP. Expire-deny, never expire-approve.
Fallback: HITL (human Command) -> deny (reject / TTL) -> refuse (no tool run).
"""
from __future__ import annotations

import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger("steering")

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
    raise last


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
    text: str, *, audit: list[dict[str, Any]], correlation_id: str,
    tenant_id: str, sink: str, block_on_pan: bool = True,
) -> str:
    kinds = [n for n, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"hitl_ui", "mcp_args", "email_body"}:
        audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink,
                      "kinds": kinds, "action": "block", "pre": pre, "post": _sha(""),
                      "detector": "regex"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink,
                  "kinds": kinds, "action": "redact" if redacted != text else "allow",
                  "pre": pre, "post": _sha(redacted), "detector": "regex"})
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
    ttl_s: float = 600.0  # 10 min expire-deny

@dataclass
class ResumeResult:
    status: str  # approved | denied | refused
    decisions: list[dict[str, Any]]
    degraded: bool


class SteeringRuntime:
    def __init__(self, *, ttl_s: float = 600.0, now: Callable[[], float] | None = None):
        self.ttl_s = ttl_s
        self._now = now or time.monotonic
        self.tickets: dict[str, Ticket] = {}
        self.breaker = CircuitBreaker("hitl_queue", failure_threshold=3, cooldown_s=30.0)
        self.audit: list[dict[str, Any]] = []
        self.decision_log: list[dict[str, Any]] = []

    def enqueue(self, actions: list[ActionRequest], *, thread_id: str,
                correlation_id: str, tenant_id: str) -> Ticket:
        cards, digests = [], []
        for a in actions:
            raw = json.dumps(a.args, sort_keys=True, default=str)
            cards.append(pii_detect_redact_audit(
                raw, audit=self.audit, correlation_id=correlation_id,
                tenant_id=tenant_id, sink="hitl_ui"))
            digests.append(args_digest(a.args))
        t = Ticket(thread_id=thread_id, interrupt_id=str(uuid.uuid4()),
                   actions=actions, display_digests=digests,
                   ttl_s=self.ttl_s, created_at=self._now())
        self.tickets[t.interrupt_id] = t
        return t

    def _cas(self, ticket: Ticket, to: str) -> bool:
        if ticket.status != "pending":
            return False
        ticket.status = to
        return True

    def deny_decisions(self, n: int, message: str) -> list[dict[str, Any]]:
        return [{"type": "reject", "message": message} for _ in range(n)]

    def resume(self, interrupt_id: str, decisions: list[dict[str, Any]] | None, *,
               approver_id: str, correlation_id: str, tenant_id: str,
               role_can_approve: bool = True) -> ResumeResult:
        ticket = self.tickets.get(interrupt_id)
        if ticket is None:
            return ResumeResult("refused", [], True)
        n = len(ticket.actions)

        def _deny(reason: str, msg: str) -> ResumeResult:
            dec = self.deny_decisions(n, msg)
            if not self._cas(ticket, "expired" if "timeout" in reason else "consumed"):
                return ResumeResult("refused", [], True)
            self.decision_log.append({"reason": reason, "approver_id": approver_id,
                                       "thread_id": ticket.thread_id, "ts": time.time()})
            return ResumeResult("denied", dec, True)

        try:
            self.breaker.allow()
        except CircuitOpenError:
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
                return _deny("illegal_type", f"Decision {dtype!r} not allowed.")
            if dtype == "respond" and action.name in SIDE_EFFECT:
                return _deny("respond_forbidden", "respond is not a deny on side-effecting tools.")
            if dtype == "edit" and (d.get("edited_action") or {}).get("name") != action.name:
                return _deny("rename_forbidden", "edited_action.name must match original.")
            if dtype == "approve" and args_digest(action.args) != ticket.display_digests[i]:
                return _deny("toctou_hash", "Args changed since display. Do not execute.")

        if any(d.get("type") == "approve" for d in decisions) \
                and self.breaker._state is CircuitState.HALF_OPEN:
            self.breaker.record_failure()
            return _deny("half_open_no_approve", "Circuit half-open: deny-only probe.")

        if not self._cas(ticket, "consumed"):
            return ResumeResult("refused", [], True)
        self.breaker.record_success()
        self.decision_log.append({"reason": "human", "approver_id": approver_id,
                                   "thread_id": ticket.thread_id, "ts": time.time()})
        if all(d.get("type") in {"reject", "respond"} for d in decisions):
            return ResumeResult("denied", decisions, False)
        return ResumeResult("approved", decisions, False)
```

### Production Docker Compose

```yaml
# docker-compose.prod.yml
# LangGraph production deployment with PostgreSQL + Redis

version: "3.9"

services:
  langgraph-server:
    image: langgraph-app:0.4.2       # Pin version, never use 'latest'
    build:
      context: .
      dockerfile: Dockerfile          # Use Debian-slim, NOT Alpine
    ports:
      - "8123:8000"
    environment:
      - DATABASE_URI=postgresql://lguser:lgpass@postgres:5432/langgraph
      - REDIS_URI=redis://redis:6379
      - LANGSMITH_API_KEY=${LANGSMITH_API_KEY}
      - LANGSMITH_PROJECT=prod-agents
    command: >
      uvicorn langgraph_app.server:app
      --host 0.0.0.0
      --port 8000
      --timeout-keep-alive 65
      --workers 4
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "2.0"

  postgres:
    image: postgres:16-bookworm
    environment:
      - POSTGRES_USER=lguser
      - POSTGRES_PASSWORD=lgpass
      - POSTGRES_DB=langgraph
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U lguser -d langgraph"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-bookworm
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### Checkpoint Cleanup Job

```python
"""Background job to purge terminal-state checkpoints on a retention schedule."""

import psycopg2
from datetime import datetime, timedelta

DB_URI = "postgresql://lguser:lgpass@localhost:5432/langgraph"
RETENTION_DAYS = 30
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

def cleanup_stale_checkpoints():
    """Delete checkpoints for threads that reached terminal state
    more than RETENTION_DAYS ago. MUST NOT delete threads with pending interrupts."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    conn = psycopg2.connect(DB_URI)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM checkpoints
                WHERE thread_id IN (
                    SELECT thread_id FROM threads
                    WHERE status IN %s
                      AND updated_at < %s
                )
            """, (TERMINAL_STATUSES, cutoff))
            deleted = cur.rowcount
            conn.commit()
            print(f"Purged {deleted} checkpoints older than {cutoff.isoformat()}")
    finally:
        conn.close()
```

---

## Token Economics & Cost Analysis

### Sunk Tokens at Pause

HITL fires in `after_model`, **after** the completion. Already incurred: uncached input + output of that turn (and all prior turns); prompt-cache **write** of the static prefix if this was the first turn in the 5-minute window. On **reject**, those tokens are sunk; the model spends **another** completion to replan. On **approve**, ToolNode runs; subsequent model turns continue.

Anthropic 5-minute ephemeral cache is the Deep Agents default TTL. HITL p50 that exceeds **5 minutes** converts remaining turns from cache-read ($0.30/MTok) to full input ($3/MTok).

**Extra input cost after a cache miss**, 2k cached prefix, 7 remaining calls:
`7 * 2,000 * ($3 - $0.30) / 1e6 = $0.0378 / interrupted run`.

At 10% interrupt rate and all waits >5m: **+$3.78 / 1k runs** on prefix alone.

### Per-Run Cost Formula with HITL

```
C_run = C_llm + C_checkpoint + C_rejection_overhead

Where:
  C_llm         = (input_tokens * price_per_input_token) + (output_tokens * price_per_output_token)
  C_checkpoint  = num_supersteps * checkpoint_write_cost  (~1-5ms DB write, negligible $)
  C_rejection   = num_rejections * avg_retry_llm_calls * C_llm_per_call
```

**State size drives checkpoint cost**: A research agent storing a 40-page PDF in state creates megabyte-scale checkpoints. Storing S3 references instead keeps checkpoints at kilobyte-scale -- a 100-1000x difference.

### `$ cost per 1k runs` -- 0% vs 10% Interrupt

Assumptions: `anthropic:claude-sonnet-4-6` at list prices (input **$3/MTok**, output **$15/MTok**, 5m cache write **$3.75**, cache read **$0.30**). 10 model calls, 2k cached prefix, 3k uncached in / 800 out per call, 1x 5m write + 9x reads. Baseline: **$0.2229 / run -> $223 / 1k**.

| Scenario | LLM $ / run | LLM $ / 1k runs |
| --- | --- | --- |
| 0% interrupt (baseline) | $0.2229 | **$223** |
| 10% interrupt, all Approve, wait <5m | ~$0.2229 | **$223** (tokens unchanged) |
| 10% interrupt, 93% Approve / 7% Reject, wait <5m | +$0.00021 | **~$223** |
| 10% interrupt, all waits **>5m** (cache miss) | +$0.00378 per interrupted | **$227** |

**Interview takeaway:** at 10% interrupt rate, **LLM $ is dominated by the unattended path**. The NFR that moves is **p99 latency and reviewer FTE**, not tokens.

### Framework Comparison at 1,000 Daily Runs (3-step task)

| Framework | Monthly Cost | Primary Cost Driver |
| --- | --- | --- |
| LangGraph | ~$63 | Explicit node structure eliminates redundant LLM calls |
| CrewAI | $78-102 | Moderate overhead from crew delegation |
| AutoGen | $84-171 | Unbounded conversation loops can consume 5-10x expected tokens |

### Trace SKUs (Observability Bill)

| Metric | Price |
| --- | --- |
| **LangSmith Traces (Base 14d)** | **$0.50 / 1k traces** |
| **Extended 400-day** | **$5.00 / 1k traces** |

Included allotments: Developer **5k** base traces/mo; Plus **10k**/mo then pay-as-you-go; Enterprise custom.

### Deployment Compute SKUs

Normalized units: **1 LCU = $1.50**, **1 LSU = $1.00**.

| Meter | Rate |
| --- | --- |
| Runtime compute | **0.045 LCU / vCPU-hr** |
| Runtime memory | **0.006 LCU / GiB-hr** |
| Database compute | **0.177 LSU / vCPU-hr** |
| Database memory | **0.025 LSU / GiB-hr** |

**Dedicated Small** (3 vCPU, 6 GiB, DB 1 vCPU / 4 GiB) **[inferred monthly floor]**:

| Line | USD / 720 h |
| --- | --- |
| Runtime CPU | **$145.80** |
| Runtime mem | **$38.88** |
| DB CPU | **$127.44** |
| DB mem | **$72.00** |
| **Infra subtotal** | **~$384 / mo** |

### All-In Cost per 1k Executions [Inferred]

| Line | $ / 1k runs |
| --- | --- |
| Model (cached, 10-call Sonnet 4.6) | **$223** |
| Traces (base 14d) | **$0.50** |
| Traces (extended 400d) | **$5.00** |
| Infra (Dedicated S at GTM ~40k runs/mo) | **~$10** |
| **All-in (base traces)** | **~$234** |
| **All-in (extended traces)** | **~$238** |

At low volume the infra line dominates traces (idle Dedicated S is still ~$384/mo).

### Reviewer FTE Staffing [Inferred]

If 1k runs/day, 10% interrupt, 1 card/run:

| Think time | Reviewer load |
| --- | --- |
| p50=30s | **0.8 h/day** |
| p95=3 min | **5 h/day** |
| Every-write HITL (8 cards/run) | **~40 h/day** -- a team |

Budget FTE **before** LLM $.

---

## Trade-offs & Failure Modes

### HITL Circuit Breaker: Closed -> Open -> Half-Open

HITL does not ship a circuit breaker. Put the breaker around the **HITL service** (queue, reviewer API, TTL worker).

```
        queue depth | reviewer error-rate | checkpointer timeout | TTL worker down
  +----------+  -------------------------------------------------------->  +----------+
  |  CLOSED  |                                                               |   OPEN   |
  |  pause + |  success (human or expire-deny) resets count                  | FAIL FAST|
  |  wait    |                                                               | reject / |
  +----+-----+                                                               | refuse   |
       ^                                                                     | NEVER    |
       | probe = expire-deny OR queue-health                                 | approve  |
       | probe OK                                                            +----+-----+
       |                                                                          | cooldown
       |                                                                    +-----v------+
       +------------ probe allow -------------------------------------------| HALF-OPEN  |
                    probe fail -> stay OPEN                                  | 1 reject   |
                                                                            | Command or |
                                                                            | health GET |
                                                                            +------------+
```

**Fallback chain (required interview answer):**
**HITL (pause + human `Command`) -> deny (`reject` / expire-deny / empty-decisions-as-deny) -> refuse (do not run the tool; return error to caller).** Never: HITL timeout -> auto-approve. Never: circuit open -> `{"type": "approve"}`.

| Policy | Resume | When to use |
| --- | --- | --- |
| **Expire-deny** | `{"type": "reject", "message": "Approval timed out. Do not retry."}` | Default for email-send, delete, refund, MCP mutating tools |
| **Expire-approve** | `{"type": "approve"}` | Almost never |
| **Expire-escalate** | Keep interrupt; notify backup; extend TTL | p99 path. Still deny at hard deadline |
| **Expire-cancel thread** | `adelete_thread` / cancel run | Headless CI |

### Production Circuit Breaker

Independent breakers: **model**, **sandbox**, **checkpointer** (and optionally store, MCP gateway).

**Fallback chain:** **Hosted Agent Server -> self-host the same compiled graph (`invoke` with your Postgres checkpointer) -> deterministic refuse.** Never: circuit open -> `LocalShellBackend`. Never: HITL timeout -> auto-approve. Never: model 429 -> unsandboxed `execute`.

### HITL Failure Taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Checkpointer blip, resume HTTP 429, reviewer UI timeout, SSE drop | Error rate; retryable status | Full-jitter retries on **resume transport** and idempotent `get_state`. Do **not** retry Approve without a ticket CAS |
| **Permanent** | No checkpointer / no `thread_id`; `allowed_decisions` empty at construct; `when`-less langchain + `mode="interrupt"`; decision type typo (`"approved"`); `respond` used as reject | Construction `ValueError`; resume `ValueError` | Fail closed. Catch at the BFF; map errors to expire-deny |
| **Poison-pill approve-all** | Batched mixed-risk cards + one "Approve all"; `True` on every `write_file`; headless `useStream` auto-resume on `send_email` | 93%-class rubber-stamp; queue noise | Sandbox + deny PDP; `when` on dest/amount; no `edit`/`respond` on irreversible tools |
| **Poison-pill `when` skip** | `path` vs `file_path`; stale allowlist; uncaught exception in `when` | Destructive write never in `action_requests` | Wrap predicates; golden tests on skip |
| **Idempotent resume** | Double-click / retried HTTP two `Command(resume=)` on the same interrupt | Duplicate `send_email` / `delete` | CAS ticket; idempotent tools; no nonce in-tree |

### Production Failure Taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Model 429/5xx, network, PG blip during run, Redis momentary | Error rate; retryable flags | Layer 1 HTTP (6); layer 2 node (3 attempts); layer 3 PG (3); layer 4 middleware if installed |
| **Permanent** | `ValueError` et al.; 401/404; `GraphRecursionError`; `@auth` 403 | Non-retryable; sweeper will **not** help | Fail closed. Do not bump `recursion_limit` to 10000 |
| **Poison-pill threads** | Non-retryable node exception; OOM crash loop with sweeper re-queue; HITL never resumed; recursion ceiling | Failed/interrupted status; sweeper churn | Shrink state; sandbox bulkhead; cancel/resume from any replica |
| **Zombie spend** | SSE drop while worker continues; cron never deleted; Serverless idle-before-scale-down; Engine every 6 h | Trace $; LCU; orphan crons | `on_disconnect="cancel"` for abandoned chats; cron lifecycle in destroy |
| **Denial of wallet** | 9,999-step loop; no call-limit middleware; online eval auto-upgrade; dual-instrument traces | Token ledger | Caps; opt out retention extension; emit once |

### Silent Failures (Most Dangerous)

- `MemorySaver` state corruption under concurrent access -- no error raised, just wrong results
- Confidence miscalibration: model claims 90% confidence, actual accuracy ~75% (15pp gap)
- Compound chain failure: three agents at 90% confidence each yield ~42% actual reliability

**The distributed systems insight**: "Teams blame models for failures that are actually architectural: the agent 'hallucinated' because it was missing state after a restart; the agent 'looped' because retries weren't bounded. These aren't AI problems -- they're distributed systems problems wearing AI clothes."

### Common Failure Mode Catalog

| Failure | Cause | Mitigation |
| --- | --- | --- |
| HITL silently broken | `checkpointer=None` or no `thread_id` | Always pass Postgres-class checkpointer + `thread_id` |
| Approval fatigue -> auto-approve | `True` on high-frequency tools; Approve-all on mixed batch | Sandbox + deny PDP; `when` on dest/amount |
| `when` skip | `path` vs `file_path`; exception in predicate | Wrap predicates; golden skip tests |
| Interpreter / PTC bypass | `task()` inside `eval`; PTC `tools.*` | Gate `eval`; `interrupt_on={"task": True}` does **not** catch JS `task()` |
| `respond` used as reject | Status `"success"` synthetic | Forbid `respond` on side-effecting tools |
| `edit` to another tool | `edited_action.name` unrestricted | Validate name in resume handler |
| `try/except` swallows interrupt | Bare `except Exception` | Never wrap `interrupt()` that way |
| Permission vs `interrupt_on` mismatch | User map wins per tool name | Do not set `write_file` True/False beside interrupt-mode `when` |
| Expire-approve | TTL -> `{"type": "approve"}` | **Never.** Expire-deny |
| Double-execute | Two resume Commands; time-travel fork | CAS ticket; idempotent tools |
| Checkpoint GC of paused threads | Retention deletes pending interrupt | Filter pending interrupt from GC |
| MCP without gateway | `interrupt_on` on tool only | Gateway PEP + hash-pin |
| PII on the card | Default `description` embeds full args | detect->redact->audit; custom description factory |
| LocalShell in prod | Agent Server user = host user; `.env`, SSH keys exposed | `BaseSandbox` only; HITL on `execute` if sandbox is on |
| No checkpointer | No resume; HITL cannot pause; `MemorySaver` dies on rolling deploy | Agent Server injected Postgres |
| `recursion_limit` 25 on children | Nested `task` hits `GraphRecursionError` at 25 | Parent bind 9,999; confirm merge |
| Stream without `thread_id` | Every mount is a new thread | Persist `threadId` |
| Dual-instrument OTel + LangSmith | Duplicate trees, doubled token counts | Emit once |
| `durability="exit"` + worker kill | No mid-run checkpoint | Keep `"async"` for long runs |
| Checkpointer in graph code | Replaced/ignored by server | Delete it in the deploy branch |
| Cron never deleted | Runs (and bills) forever | Lifecycle in deploy/destroy |

### NFRs and Explicit Trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability** | Prolonged Redis **or** Postgres outage -> Agent Server **unavailable**. At least one queue worker must listen or runs orphan | Always-on Dedicated $ vs Serverless wake vs self-host HA |
| **RPO of checkpoints** | `"sync"`: last super-step before next. `"async"` (default): small window. `"exit"`: **empty if crash mid-run**. `InMemorySaver`: **empty on restart** | Crash-consistency vs p50 |
| **RTO of checkpoints** | Resume `thread_id` from last checkpoint after sweeper re-enqueue (~2 min crash path). Any replica can resume | Time-to-resume vs forensic truth |
| **RPO of Redis** | Signaling only -- **no** user/run payloads to lose. Prolonged outage = unavailability | Stream UX vs durability |
| **Compliance** | **Not provided by `deepagents`.** HITL card is a **new processing surface** (GDPR/HIPAA). Default description embeds full tool args. Checkpointer retention = PII lifetime | Reviewer usefulness vs data minimization |
| **Correctness vs fatigue** | Every-write HITL looks strict; analog is **93% approve**. Sandbox + deny PDP so HITL volume is escapes and irreversible sends | Apparent control vs actual mediation |

### Stale Execution Risk

If an agent waits days for approval, its context may be invalid. OAuth tokens expire (HubSpot ~30min, Google ~1hr, Salesforce ~2hr), pagination cursors go stale. Verify action hash on resume to prevent executing against changed state.

### Availability & Recovery Targets

| Metric | Target | Rationale |
| --- | --- | --- |
| Availability | 99.9% (3-nines) | Standard for internal enterprise tooling |
| RPO | 0 (zero data loss) | Every superstep checkpointed to PostgreSQL |
| RTO | < 5 minutes | Restart container, reconnect, resume from last checkpoint |

---

## Production Patterns & Best Practices

### Deployment Patterns

**Docker**: Use Debian-based slim images, not Alpine. musl libc causes C-extension compilation failures for Python AI workloads. Pin LangGraph version explicitly -- `latest` tag introduces breaking changes. Set `--timeout-keep-alive 65` in uvicorn to survive 30-60s LLM calls that would otherwise hit AWS API Gateway's 29s timeout.

**Kubernetes**: Official Helm chart (v0.2.6+). Use PostgreSQL for checkpoints to share context across replicas. Readiness probes intercept traffic until the container is healthy.

**Scaling**: Single container bottlenecks on Python GIL at 1,000+ concurrent requests. Scale horizontally with multiple containers behind a reverse proxy.

**Self-host sizing examples** (average run 1 s):

| Pattern | Write rps | Read rps | API replicas | Queue workers | `N_JOBS` |
| --- | --- | --- | --- | --- | --- |
| Low/low | 5 | 5 | 1 | 1 | 10 |
| Med/med | 50 | 50 | 3 | 5 | 10 |
| High/high | 500 | 500 | 15 | 10 | 50 |

Autoscaling Cloud Dedicated: target **75%** CPU, **75%** memory, **10 pending runs / container**; scale-down delayed **30 minutes**.

### Graph Hosting Modes

1. **Compiled graph (recommended).** Export `agent = create_deep_agent(...)`. Server loads **once at container start**.
2. **Async factory.** Export `async def agent(config: RunnableConfig)`. Server calls it **every run**. Required when the sandbox/backend must key off per-run config.

### Runtime Modes

| Mode | Who runs the queue | Typical |
| --- | --- | --- |
| **Single host** | API process manages the queue | Dev / low traffic |
| **Split API + queue** | Dedicated workers. API on request volume; workers on pending runs | Production self-host |
| **Distributed runtime** | Separate orchestration vs execution | Large-scale / high concurrency |

### Tiered Recovery Strategy

```
1. RETRY    -- Bounded, with idempotency keys. Never unbounded.
     |
     v (exhausted)
2. FALLBACK -- Switch to simpler model or cached response.
     |
     v (unavailable)
3. RESUME   -- Reload from checkpoint with state verification.
     |
     v (state invalid)
4. COMPENSATE -- Saga-style backward walk. Hard-to-reverse actions at end
     |           of saga so early failures only undo cheap operations.
     v (compensation fails)
5. DEAD-LETTER -- Human review queue. This is the final safety net.
```

### Zero-Trust MCP (Still Required in Prod)

`permissions=` covers **built-in FS tools only**. MCP `tools/call`, custom tools, `execute` are **out of that PDP**.

| Layer | Question it answers | Deep Agents primitive |
| --- | --- | --- |
| **IdP / agent principal** | Who is speaking? | Not in HITL. `HITLResponse` has `decisions` only -- **no `actor_id`** |
| **PDP** | Is `(principal, action, resource, ctx)` allowed? | `permissions=` for **FS paths only**, fail-open |
| **HITL** | Does a human accept **this proposed call**? | `interrupt_on` / permission interrupt |
| **Sandbox** | What can happen even if someone clicks Approve? | `BaseSandbox` / OS seatbelt |
| **MCP PEP** | Is this `tools/call` allowed for this token audience? | Gateway -- `permissions=` does not apply |

Zero-Trust: authenticate every hop, no passthrough tokens, audience-bound credentials. Clients **MUST** send RFC **8707** `resource` = canonical MCP server URI. Servers **MUST** accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token; obtain a new token (typically RFC **8693** exchange). Hash-pin `toolSurfaceHash` over canonical JSON of name + description + schemas; re-verify on every `tools/call`.

### PII Pipeline -- Detect -> Redact -> Audit

Default description embeds **full tool args** into the interrupt payload, reviewer UI, and traces. `PIIMiddleware` on the agent does **not** automatically redact interrupt payloads.

| Step | Details |
| --- | --- |
| **1. Detection** | Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if available. Scan: user input, model output, tool args/results, VFS writes, memory-write candidates, log/trace payloads, webhook bodies, HITL UI. If ML is down: **fail closed to mask** on chat; **fail closed (block)** on tool args to external MCP |
| **2. Redaction** | `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`); `block` when the field must not exist. Strip from VFS **and** message channel |
| **3. Audit trail** | WORM logs of: `content_sha256` pre/post, entity types + counts, action, detector, `correlation_id`, `tenant`, `thread_id`, permission decision, tool **arg digest**. A tool call without an audit row is a control-plane bug |

| PII Control | Effect |
| --- | --- |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` | Strip I/O for SDK + LangChain |
| `Client(hide_inputs=..., anonymizer=...)` | Per-client redaction |
| `PIIMiddleware` | Before the model; you append it |
| `>=0.7.9` omit middleware trace inputs | Volume/PII shrink, not a DLP program |

### Authentication Layers for Multi-Tenancy

| Layer | Purpose | Mechanism |
| --- | --- | --- |
| End-user auth | Establish identity | OAuth2 / OIDC |
| Agent-acting-as-user | Per-user credentials for external APIs | OAuth via Agent Auth, auto-refresh |
| Team RBAC | Control deployment/monitoring access | Role-based access control |

**Secret management**: Use an auth proxy that intercepts outbound requests and injects credentials. API keys never appear in sandbox code, environment variables, or logs.

| Location | OK? |
| --- | --- |
| LangSmith **workspace secrets** | For Agent Server env (model keys) |
| Auth proxy `${OPENAI_API_KEY}` rules | Recommended for sandbox egress |
| Sandbox env / files / `secrets=` | **Forbidden** -- agent can read them |
| Graph state / checkpoints | **Not recommended** |

### TOCTOU (CWE-367)

(1) Human reviews `action_requests[].args` (snapshot); `approve` executes the **in-memory** `ToolCall` -- Deep Agents does **not** re-hash args at execute. (2) Permission/`when` checks a path string; later `open()` follows a swapped symlink. (3) Human `edit`s args; those new args are **not** shown in a second card unless you add one. Mitigations: hash(`canonical_args`) at display and at execute; refuse mismatch; strip invisible Unicode in the HITL UI.

### Regulatory Compliance

| Regulation | Effective Date | Key Requirement |
| --- | --- | --- |
| EU AI Act Article 14 | August 2, 2026 | Mandates human ability to "intervene, stop, or override" high-risk AI |
| NIST AI Agent Standards | February 2026 | Moves from experimentation to infrastructure requirements |
| California SB-833 | July 1, 2026 | State-level agent oversight requirements |
| OWASP LLM Top 10 | Ongoing | "Excessive Agency" as dedicated risk class; prompt injection ranked #1 |

---

## Latency SLA Targets

### HITL Latency (Numeric ms)

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Human clock (time-to-approve)** [inferred policy] | **30,000 ms** | **180,000 ms** | **600,000 ms** | 30 s interactive. 3 min Slack. 10 min expire-deny |
| **HITL middleware apply (CPU)** [inferred] | **1 ms** | **5 ms** | **20 ms** | Microseconds rounded up |
| **Resume checkpoint load+write (Postgres sync)** [inferred] | **10 ms** | **50 ms** | **200 ms** | Same durability-tax class |
| **Command -> ToolNode start (no human)** [inferred] | **15 ms** | **80 ms** | **400 ms** | CPU + checkpointer + invoke overhead |

### Production Latency (Numeric ms)

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Run-create API persist + Redis wake** [inferred] | **20 ms** | **80 ms** | **250 ms** | "Creating a run is a fast write" |
| **Dedicated warm SSE / parent TTFT** [inferred] | **800 ms** | **3,200 ms** | **6,400 ms** | Stream; always-on Dedicated |
| **Serverless scale-from-zero first SSE** [inferred] | **5,000 ms** | **20,000 ms** | **60,000 ms** | Unpublished. Chat UX on Dedicated |
| **Queue wait when all slots busy** [inferred] | **0 ms** | **5,000 ms** | **30,000 ms** | Scale workers on 10 pending runs |
| **One ReAct cycle on worker** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | Model + local FS |
| **10-call research run, GP off** [inferred] | **20,000 ms** | **80,000 ms** | **200,000 ms** | Do not put on HTTP timeout. Use SSE + rejoin |
| **Checkpointer async extra** [inferred] | **5 ms** | **30 ms** | **100 ms** | Agent Server default |
| **Checkpointer sync extra** [inferred] | **10 ms** | **50 ms** | **200 ms** | Use when compliance demands |
| **Worker hard-crash reclaim (sweeper)** [from documented 2 min] | **60,000 ms** | **120,000 ms** | **180,000 ms** | Resume from last checkpoint |

### Autonomous vs HITL Latency Summary

| Metric | Target (autonomous) | Target (HITL path) | Notes |
| --- | --- | --- | --- |
| p50 | 2-5s | Human response time + 2-5s | Human is the bottleneck |
| p95 | 8-15s | 4 hours (Tier 1 SLA) | Async queue model |
| p99 | 25-30s | 24 hours | Complex approval chains |
| TTFT | 0.4s (streaming) | 0.4s after resume | First token to UI |

**Gateway timeout risk**: AWS API Gateway closes connections at 29s. LLM calls routinely take 30-60s. Without streaming or `--timeout-keep-alive 65`, requests drop silently at p95+.

### Tiered Escalation with SLAs

| Tier | Trigger | SLA | Routing |
| --- | --- | --- | --- |
| 1 | Moderate-confidence actions | 4 hours | Async queue |
| 2 | Low-confidence / high-blast | 1 hour | Priority queue + escalation |
| 3 | Compliance-sensitive | 15 minutes | Sync with on-call paging |

### Traffic Shape (Only Named Anecdote)

LangChain internal GTM agent: ~**10k requests/week**, **>150** active users, **26%** user-initiated / **74%** ambient (cron/event). Converted: ~**0.0165 QPS average**. GTM outcome metrics: inbound conversion **+250%**, **3x** pipeline dollars, **40 hours/rep/month** reclaimed, **50%** daily / **86%** weekly active usage.

---

## Interview Q&A

### Steering & HITL

**Q1. What is Deep Agents steering, in one minute?**
I treat HITL as a durable pause, not a PDP. `interrupt_on` names tools that `HumanInTheLoopMiddleware` batches after the model proposes `tool_calls`. Unnamed tools auto-approve. A checkpointer is required; LangGraph waits forever; expire-deny is my timer sending a `reject` Command. `permissions=` `mode="interrupt"` synthesizes the same middleware for FS paths, still fail-open, still not MCP or `execute`. The model proposes; middleware plus my resume handler dispose.

**Q2. Walk model `tool_calls` -> HITL -> resume -> execute.**
After the completion, `after_model` filters by `interrupt_on` and `when`. Remaining calls become one `HITLRequest`. `interrupt()` checkpoints the thread and the API returns `result.interrupts` on `version="v2"`. I show cards from `action_requests` (display copies). I resume the same `thread_id` with `Command(resume={"decisions": [...]})` positional. Approve/edit keep a `ToolCall`; reject/respond inject a `ToolMessage`. `ToolNode` runs; FS deny still binds after edit. I never pass a new input dict or `Command(update=)` to continue a pause.

**Q3. `True` vs `InterruptOnConfig` vs `when` vs permission interrupt.**
`True` is all four decisions. `InterruptOnConfig` requires non-empty `allowed_decisions`; optional `description`, `args_schema`, `when`. `when` False auto-approves out of the batch -- a buggy predicate is a silent hole, and `tool=None` at this hook. `mode="interrupt"` adds `when` on matching FS paths. If I also set `interrupt_on={"write_file": True}` I drop that `when` and pause every write; `False` disables secrets interrupts. User map wins per tool name.

**Q4. What is the difference between `reject` and `respond`?**
`reject` denies execution and feeds rejection feedback to the agent with status `"error"`. `respond` supplies a synthetic tool result with status `"success"` when the human is effectively acting as the tool. I do not use `respond` to deny a side-effecting tool because the model believes the send happened.

**Q5. Can path permissions trigger the same interrupt flow?**
Yes. `FilesystemPermission(mode="interrupt")` uses the same human-in-the-loop mechanism for built-in filesystem tools. The `when` predicate fires only on matching paths. A preceding deny rule wins -- HITL does not fire; tool returns permission-denied.

**Q6. Why is a checkpointer required?**
Because the paused agent state has to survive between the interrupt and the later resume call. Without one, `interrupt()` has nowhere to write. `MemorySaver` is RAM -- process restart drops every paused HITL.

**Q7. Give me `$ per 1k` at 0% vs 10% interrupt.**
Inferred, same 10-call Sonnet 4.6 shape: **$223 / 1k** at 0%. At 10% interrupt, tokens stay **$223** if waits stay inside the 5m cache. If every wait exceeds 5m, prefix cache-miss tax is **$227 / 1k**. LLM $ is not the story -- reviewer FTE and p99 are.

**Q8. What p50/p95/p99 do you put on HITL?**
Nobody publishes them. Human clock I contract as **30,000 / 180,000 / 600,000 ms** -- 30s interactive, 3 min Slack, 10 min expire-deny. Resume path: middleware **1 / 5 / 20 ms**, Postgres sync **10 / 50 / 200 ms**, end-to-end to ToolNode start **15 / 80 / 400 ms**. I never expire-approve.

**Q9. Is HITL Zero-Trust? What about MCP?**
No. HITL does not authenticate, does not evaluate `(principal, action, resource)`, does not cover unnamed tools. `permissions=` is a fail-open FS path PDP. Zero-Trust is a gateway PEP: OAuth 2.1, RFC 8707 audience = canonical server URI, no token passthrough (RFC 8693 exchange), hash-pin tool JSON on every `tools/call`. A HITL click does not mint that token.

**Q10. Inheritance and the interpreter hole.**
Declarative specs inherit parent `interrupt_on` / `permissions`; a spec replaces entirely if set (PR #2334). Compiled and async do not inherit. Interpreter `task()` from `eval` skips parent `interrupt_on` per dispatch -- I gate `eval`. PTC, if enabled, bypasses HITL too. Two resume dialects: HITLRequest `decisions` vs raw `interrupt()` values -- the UI must branch.

**Q11. PII on the HITL card.**
Default description embeds full tool args into the checkpoint, the UI, and traces. I detect with regex plus optional ML before render; redact/mask/hash in a custom `description` factory; keep raw args server-side for the digest bind; block PAN onto Slack/MCP. I audit WORM of approver id, arg digest, decision, cid, thread -- not raw PAN.

**Q12. Circuit breaker and fallback. What happens on timeout?**
The library waits forever. My HITL-service breaker is closed -> open -> half-open. Half-open probe is a reject Command or a health GET -- never Approve. Fallback is HITL -> deny -> refuse. Expire-deny at 10 minutes. Circuit open is deny, not approve. I CAS the ticket so double-click cannot double-send.

**Q13. Checkpointer durability and lost resume.**
`sync` before next step; `async` small hole; `exit` still writes on interrupt but a crash before the interrupt returns can lose the pause. InMemory dies on restart. Resume needs the same `thread_id` and a `Command`. Time travel always re-triggers interrupts and forks -- it will not un-send email. Retention must not GC pending interrupts.

### Production

**Q14. What is "going to production" for Deep Agents, in one minute?**
I do not ship a new runtime. `create_deep_agent` already returned a LangGraph `CompiledStateGraph`. Production is LangSmith Deployment's Agent Server around that graph: API replicas persist runs and stream SSE; queue workers execute; Postgres is the checkpointer and store; Redis is wake/cancel/stream signaling with no payloads. Custom auth or routes mean a normal LangSmith Deployment, not MDA.

**Q15. Walk a request from API replica to SSE.**
Client sends `thread_id` + `context` to an API replica. API writes a pending run to Postgres and Redis wakes a worker -- no payload in Redis. Worker takes a lease, at most one run per thread, runs super-steps, checkpoints at `"async"` by default, publishes events on PubSub. Any replica with `/stream` open forwards SSE. Default disconnect leaves the run running. I rejoin with the same `thread_id` and protocol v2 `since`. HITL `interrupt()` releases the worker slot.

**Q16. Name the four retry layers.**
Chat-model HTTP `max_retries=6` (429/5xx/network, not 401/404). LangGraph node `RetryPolicy` `max_attempts=3` with jitter, skipping `ValueError` and friends. Agent Server **3** attempts for **transient Postgres errors**, not model 429s. Middleware `ModelRetryMiddleware` / `ToolRetryMiddleware` default `max_retries=2` (3 attempts) -- **not** installed by Deep Agents unless I append them.

**Q17. Why 9,999 not 10000?**
The SDK binds 9,999 because LangGraph `merge_configs` has historically dropped `recursion_limit` when it equals the 10000 sentinel, so frontend `recursionLimit: 10000` can be a no-op and children fall back toward 25. Hitting the ceiling is `GraphRecursionError`, a hard error.

**Q18. Give me `$ per 1k` including traces and Dedicated Small.**
Inferred, not a SKU. Model: **$223 / 1k**. LangSmith traces **$0.50 / 1k** base or **$5 / 1k** extended. Sum **~$224 / ~$228**. Dedicated Small ~**$384/mo**; at GTM ~40k/mo that's **~$10 / 1k** infra. All-in ~**$234 / 1k** at that volume.

**Q19. Disconnect, Last-Event-ID, and HITL -- how do they differ?**
Disconnect does not cancel unless I set `on_disconnect="cancel"`. Protocol v2 is POST SSE; resume is `since` in the JSON body; Last-Event-ID is the old blog story. HITL is not cancel: the worker frees the slot and sleeps unbounded; resume is `Command(resume=...)`. Cancel interrupt keeps checkpoints; cancel rollback deletes them.

**Q20. Durable execution vs Temporal.**
I describe Agent Server as the Temporal-equivalent: `thread_id` is the workflow id, Postgres checkpoints are history, the durable queue plus Redis wake is the task queue, workers heartbeat and a 2 min sweeper re-leases on crash. Replay restarts the interrupted node from line 1, so tools must be idempotent or HITL-gated after the draft. I do not add Temporal "to make LangGraph durable."

**Q21. Zero-Trust MCP in production -- isn't Agent Server enough?**
No. Deploying gives me MCP/A2A **ingress** for free. `permissions=` still only covers built-in FS tools. Egress `tools/call` needs a gateway PEP: allowlists, hash-pinned tool JSON, OAuth 2.1, RFC 8707 audience, no client-token passthrough. Ingress without `@auth` is an open URL.

**Q22. How do you design human oversight for production agents?**
Lead with the four-tier risk classification, then the async approval architecture with SLA tiers. Mention that synchronous approval collides with gateway timeouts and token expiry. Show the stale-execution problem (action hash verification on resume). Close with the regulatory drivers -- EU AI Act Article 14 makes HITL legally mandatory for high-risk systems as of August 2026.

**Q23. What goes wrong with agents in production?**
Lead with the 56.6% task success rate. Then the distributed-systems framing: "Most agent failures are architectural, not model failures." Hit the top three: MemorySaver in production (silent state corruption), confidence miscalibration (90% claimed vs 75% actual), and the durable execution gap (checkpoints save state but don't guarantee completion).

**Q24. How do you scale agent systems?**
LangGraph bottlenecks on Python GIL at 1,000+ concurrent requests. Scale horizontally behind a reverse proxy. PostgreSQL for shared checkpoint state across replicas. Redis for real-time streaming pub-sub. The real scaling bottleneck is human review throughput, not compute -- design the escalation SLAs before the infrastructure.

---

## System Design Scenarios

### Scenario 1: Destructive FS + Email-Send HITL

**Problem.** A coding/research agent may write workspace files freely. Writing `/secrets` or `/memories` and sending email require a human. MCP mail still cannot skip the gateway. Reviewers rubber-stamp if every `write_file` pauses (93% approve analog).

**Proposed architecture (recommended):**

```
  +---------+   +-------------------------------------------------------------+
  | IdP/PEP |-->| CONTROL: create_deep_agent                                  |
  | JWT ->  |   |   permissions: interrupt /secrets/** /memories/**            |
  | reviewer|   |                allow /workspace/**                           |
  | role != |   |                deny /**          (fail-closed FS)            |
  | chat    |   |   interrupt_on: notify_email + mcp mail.send                 |
  | user    |   |     allowed=["approve","edit","reject"]  (no respond)        |
  |         |   |   DO NOT set write_file True/False (would clobber when)      |
  |         |   |   PostgresSaver sync  thread_id uuid7  TTL 600s deny         |
  |         |   |   CAS ticket + arg digest + actor_id WORM                    |
  |         |   |   PII detect->redact->audit on HITL cards                    |
  |         |   |   gateway Cedar/OPA + DLP + dest allowlist (RFC 8707)        |
  +---------+   +----------------------------+--------------------------------+
                                             v
                    +------------------------------------------------------+
                    | DATA: model proposes write_file / notify_email        |
                    |   after_model batches gated calls -> HITLRequest      |
                    |   Slack notify AFTER interrupt payload (idempotent)   |
                    |   resume Command -> wrap_tool_call deny still binds   |
                    |   cache ttl=1h if reviewers are slow                  |
                    +------------------------------------------------------+
```

**Trade-off matrix:**

| Axis | Interrupt-mode secrets + named email + gateway (recommended) | HITL on every `write_file` | No HITL, fail-closed FS + gateway only |
| --- | --- | --- | --- |
| **Cost** | ~$223/1k if waits <5m; FTE 0.8 h/day at 1k runs x 10% x 30s | Same LLM $ until fatigue; 8 cards/run -> ~40 reviewer-hours/day | Lowest LLM $ ($223/1k); no FTE |
| **Security** | Best in-tree FS + LLM03 #6 on email; MCP still needs gateway | Looks strict; 93% approve analog | Strong for FS; zero human for irreversible send |
| **Scalability** | Reviewer staffing on rare cards | Reviewer collapse | Horizontal PEP |

### Scenario 2: Coding `execute` HITL vs Sandbox-Only

**Problem.** Ship a coding assistant: repo checkout, tests, patches, optional `execute`. Options: (B1) `BaseSandbox`, network off, HITL omitted or only on escapes; (B2) `LocalShellBackend` + HITL on all operations; (B3) hybrid Codex-shaped: sandbox + HITL on escape via `when`.

**Trade-off matrix:**

| Axis | B1 Sandbox-only (prod) | B2 LocalShell + HITL all ops | B3 Hybrid Codex-shaped (internal copilot) |
| --- | --- | --- | --- |
| **Cost** | Lowest LLM $; sandbox compute | FTE on every command | Low extra; FTE only on escapes |
| **Security** | Blast radius bounded; no human for exceptions | HITL on ALL ops; fatigue; no isolation | Sandbox + approval; rare questions |
| **Scalability** | High (untrusted/multi-tenant/CI) | Does not scale | High for internal copilot |

**Decision.** **B1 wins** for untrusted / multi-tenant / CI. **B3 wins** for internal copilot. **B2 never wins** in production.

### Scenario 3: Enterprise Document Processing Pipeline with Tiered Human Review

**Problem Statement**: A financial services firm processes 5,000 loan applications daily. Each requires document extraction, data validation, credit risk scoring, and a final approval decision. Regulatory requirements (CFPB, ECOA) mandate human review for any denial and for applications above $500K. Target: 80% fully automated, 20% human review, 30-minute average processing time.

**Architecture:**

```
+-----------+     +--------------------------------------------------+
| Document  |     |            Agent Pipeline (LangGraph)             |
| Ingestion |---->|                                                   |
| (S3)      |     |  +--------+  +--------+  +--------------+        |
+-----------+     |  |Extract |->|Validate|->| Risk Score   |        |
                  |  | Agent  |  | Agent  |  | Agent        |        |
                  |  +--------+  +--------+  +------+-------+        |
                  |                                  |                |
                  |                    +-------------+                |
                  |                    v             v                |
                  |           +------------+  +----------+           |
                  |           |Auto-Approve|  |HITL Queue|           |
                  |           | (Tier 1-2) |  |(Tier 3-4)|           |
                  |           +------+-----+  +-----+----+           |
                  |                  |              |                 |
                  |                  v              v                 |
                  |           +---------------------------+          |
                  |           |   Decision + Audit Log    |          |
                  |           +---------------------------+          |
                  +--------------------------------------------------+
```

**Interrupt routing logic:**
- Score > 700 AND amount < $500K: auto-approve (Tier 1)
- Score 600-700 OR amount $500K-$2M: async review queue, 4-hour SLA (Tier 2)
- Score < 600 OR denial: mandatory senior review, 1-hour SLA (Tier 3)
- Any ECOA-flagged demographic correlation: compliance officer, 15-min SLA (Tier 3)

**Trade-off Matrix:**

| Dimension | Chosen Approach | Alternative | Rationale |
| --- | --- | --- | --- |
| Checkpoint backend | PostgreSQL | DynamoDB | ACID for financial audit trail |
| State storage | S3 references | Documents inline | 100x smaller checkpoints |
| Review queue | Async with SLA tiers | Synchronous blocking | 66% of reviews take 10+ min |
| Observability | LangSmith (managed) | Langfuse (self-hosted) | Compliance team requires vendor SLA |

**Decision rationale**: Async queue with checkpointed state lets agents release resources during the wait. The stale-execution guard (action hash verification) prevents approving a loan whose credit score changed during review.

### Scenario 4: Multi-Agent Customer Support System with Escalation Chain

**Problem Statement**: A SaaS company handles 12,000 support tickets daily. Goal: increase autonomous resolution from 45% to 75% while ensuring zero unauthorized account changes. Must comply with GDPR Article 22.

**Architecture:**

```
+----------+    +------------------------------------------------------------+
| Ticket   |    |              Orchestrator Agent                            |
| Intake   |--->|  (classifies intent, routes to specialist subagent)        |
| (API/UI) |    |                                                            |
+----------+    |  +-------------+ +------------+ +------------------+       |
                |  | Billing     | | Technical  | | Account Mgmt     |       |
                |  | Subagent    | | Subagent   | | Subagent         |       |
                |  | interrupt:  | | interrupt: | | interrupt:       |       |
                |  |  refund>$50 | |  none      | |  ALL mutations   |       |
                |  |  plan_change| |  (Tier 1)  | |  (Tier 4)        |       |
                |  +------+------+ +-----+------+ +--------+---------+       |
                |         |              |                 |                  |
                |         v              v                 v                  |
                |  +-----------------------------------------------------+   |
                |  |           Shared Approval Queue                      |   |
                |  | Tier 1: auto | Tier 2: async | Tier 3: paged        |   |
                |  +-----------------------------------------------------+   |
                +------------------------------------------------------------+
                         |
                         v
                +--------------------+
                |  Feedback Loop     |
                |  Rejected actions  |--> Retrain / adjust thresholds
                |  become labels     |
                +--------------------+
```

**Subagent permission isolation** (no merge -- complete replacement):
- Billing subagent: read CRM + billing API, write restricted to draft invoices
- Technical subagent: read knowledge base + logs, no write access to customer data
- Account subagent: all mutations require interrupt, no autonomous writes

**Decision rationale**: Complete permission replacement on subagents rather than inheritance. Inherited permissions create "privilege creep" where a newly added tool on the parent silently becomes available to all subagents. The feedback loop from reviewer decisions closes the automation-improvement cycle: every rejection is a labeled training example.

### Scenario 5: LangSmith-Hosted vs Self-Host Agent Server

**Problem.** Regulated SaaS wants Deep Agents for an internal research copilot plus a smaller external assistant. Security wants EU residency or US-only exception, custom JWT `@auth`, no host shell, per-user memory, and audit trail of who deployed which graph.

**Trade-off matrix:**

| Axis | Cloud Dedicated Deployment | MDA (CLI-first beta) | Hybrid / self-host |
| --- | --- | --- | --- |
| **Cost** | ~$384/mo Dedicated S + traces + models | Same runtime; likely Serverless idle + seats | Your k8s + PG HA |
| **Latency** | Always-on; warm SSE 800/3,200/6,400 ms | Unpublished; US Cloud only | You tune `N_JOBS_PER_WORKER` |
| **Auth** | Custom @auth + Agent Auth + proxy; audit logs | Limited identity; not for custom routes | Default-off -- you must add it |
| **Scalability** | Size S-L; type immutable after create | Same Agent Server | Linear workers x 10 jobs |

**Decision.** Cloud Dedicated when custom auth + HA needed and org region matches. MDA when agent is the product and US+CLI acceptable. Self-host for data residency/air-gap.

### Scenario 6: High-HITL Interactive Copilot vs 74% Ambient Batch

**Problem.** Same Agent Server, two products: (1) Slack/web copilot with HITL on sends, Dedicated TTFT, rejoin after laptop sleep; (2) GTM-like ambient plane with Salesforce triggers and Monday cron, 74% of traffic, 48h SLA auto-send.

**Trade-off matrix:**

| Axis | Split NFR on one Agent Server (recommended) | Two deployments (chat Dedicated + batch Serverless) |
| --- | --- | --- |
| **Cost** | One Dedicated floor + models + traces. Ambient slots free during HITL sleep | Two SKUs; Serverless saves idle except idle-before-scale-down still bills |
| **Latency** | Chat: Dedicated warm 800/3,200/6,400 ms. Batch: 20-minute run class | Batch cold-start 5,000/20,000/60,000 ms |
| **Ops** | One revision, two admit paths | Two revisions / secrets / auth surfaces |

---

## Key Numbers to Memorize

### Package / Gates / Versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **`>=0.7.9`** | `excluded_tools` blocks **execution**; middleware tracing inputs off |
| **`>=0.6.8`** | Permission `mode="interrupt"` |
| **`langchain>=1.3.3`** | `when` on `InterruptOnConfig` (Python-only) |
| **`>=0.5.0`** | `rt.server_info` / `rt.execution_info` namespace factories |
| **PR #2334** | Inherit parent `interrupt_on` on declarative specs |
| **Slot 14** | `HumanInTheLoopMiddleware` tail |

### Decisions / Fail-Open
| Number | What |
| --- | --- |
| **4** | Default `allowed_decisions` when value is `True`: approve, edit, reject, respond |
| **fail-open** | Unnamed `interrupt_on` keys **and** unmatched `permissions=` paths |
| **first-match** | `permissions=` evaluation order; deny-before-interrupt wins |
| **positional** | `decisions` must match `action_requests` order and length |
| **`"error"` / `"success"`** | `reject` vs `respond` synthetic ToolMessage status |

### Workers / Streaming / Recursion
| Number | What |
| --- | --- |
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **255** | Postgres `thread_id` max chars |
| **`since`** | Protocol v2 SSE resume cursor (POST body); **not** Last-Event-ID |
| **2 min / 120,000 ms** | Worker sweeper interval |
| **10** | `N_JOBS_PER_WORKER` default concurrent runs per worker |
| **3** | Agent Server PG run attempts; node `RetryPolicy` max_attempts |
| **6** | Chat-model HTTP `max_retries` |
| **enqueue** | Default double-text strategy |
| **continue** | Default SSE disconnect (run keeps going) |

### $ / Cache [Inferred where marked]
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$3.75 / $0.30** | 5m cache write / cache read per MTok |
| **$223 / 1k** | 0% interrupt, 10-call cached 2k prefix |
| **$227 / 1k** | 10% interrupt, all waits >5m (prefix miss) |
| **$0.50 / $5.00 per 1k** | Base / extended 400-day traces |
| **~$384 / mo** | Dedicated Small 720h floor [inferred] |
| **~$234 / 1k** | Model + base traces + Dedicated S infra at GTM volume [inferred] |

### Human / Classifier Analogs (Anthropic)
| Number | What |
| --- | --- |
| **~93%** | Users approve permission prompts |
| **84%** | Sandbox cut in prompts |
| **17% / 0.4%** | Auto-mode classifier FNR (n=52) / FPR (n=10,000) |
| **3 / 20** | Consecutive / total denials before escalate |

### Latency (Numeric ms)
| Number | What |
| --- | --- |
| **30,000 / 180,000 / 600,000** | Human clock p50/p95/p99 [inferred policy] |
| **1 / 5 / 20** | HITL middleware CPU [inferred] |
| **15 / 80 / 400** | Command -> ToolNode start [inferred] |
| **800 / 3,200 / 6,400** | Dedicated warm SSE / parent TTFT [inferred] |
| **5,000 / 20,000 / 60,000** | Serverless scale-from-zero [inferred] |
| **60,000 / 120,000 / 180,000** | Worker crash reclaim from 2 min sweeper |
| **20 / 80 / 250** | Run-create API persist + Redis wake [inferred] |

### Cloud SKUs / Traffic
| Number | What |
| --- | --- |
| **3 / 6 / 1 / 4** | Dedicated Small runtime vCPU / GiB / DB vCPU / DB GiB |
| **25 MB** | Cloud request payload cap -> 413 |
| **~10k / week, 150+ users, 26% / 74%** | LangChain GTM agent traffic shape |

### Security
| Number | What |
| --- | --- |
| **detect -> redact -> audit** | PII pipeline on traces, checkpoints, HITL UI |
| **RFC 8707 / RFC 8693** | MCP resource indicator / token exchange |
| **CWE-367** | TOCTOU on approved args / FS paths / edits |
| **AISVS C9.2** | Interrupt != approval workflow (no TTL/notify/nonce in Deep Agents) |
| **LLM03 #6 / #7** | HITL optional extra / complete mediation (PDP not LLM) |
| **closed -> open -> half-open** | Application breakers on model / sandbox / checkpointer |

**Dates:** research frozen **2026-09-02**. Do not treat inferred $ or ms as list prices or vendor SLOs.
