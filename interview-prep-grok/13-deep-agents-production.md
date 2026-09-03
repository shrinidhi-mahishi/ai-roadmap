# Module 13: Deep Agents Production (Agent Server around the same graph)

**Study + interview prep.** Grounded in research dated 2026-09-02 (53 sources). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01). Production-relevant gates: `rt.server_info` / `rt.execution_info` namespace factories `>=0.5.0`; `excluded_tools` also **blocks execution** and middleware tracing inputs omitted **`>=0.7.9`**; event streaming `version="v3"` since Deep Agents 0.6; `ToolErrorMiddleware` needs `langchain>=1.3.14`; model-retry `is_retryable` skip needs `langchain>=1.3.16`. Harness assembly, `ls_integration=deepagents`, recursion bind **9,999**, and the GTM **~10k req/week** anecdote live in [08-deep-agents-harness](08-deep-agents-harness.md) — cited, not recopied. OTel vs LangSmith meters live in [05-observability](05-observability.md); this file only covers the dual-instrument failure mode. Sandbox vs `LocalShellBackend` catalog lives in [09-deep-agents-execution](09-deep-agents-execution.md); here only the production ban and secret-proxy pattern. HITL (12), subagents (11), and VFS catalog (09) appear only as production implications (checkpointer required to resume; one-run-per-thread; MCP gateway still required because `permissions=` does not cover MCP).

**Thesis:** Deep Agents does **not** ship a new production runtime. `create_deep_agent` returns a LangGraph `CompiledStateGraph`. Production ships **LangSmith Deployment’s Agent Server** (API replicas + queue workers + Postgres checkpointer/store + Redis pub/sub) **around that graph**. Going to production is a hosting and durability problem, not a second orchestrator.

`$ per 1k runs` includes published LangSmith trace SKUs plus **[inferred]** Dedicated Small infra and the [08] medium-research model bill. LangChain publishes **no** p50/p95/p99 of hosted `invoke` / `runs.stream` — missing percentiles are architecture-derived **[inferred] policy targets** and are marked. Do not cite them as vendor SLOs.

| Pin | Why |
| --- | --- |
| `deepagents>=0.7.9` | Harness-profile `excluded_tools` is an **execution** filter, not just a schema hide. Pre-0.7.9 a hidden tool could still dispatch |
| Compiled graph `recursion_limit: 9_999` | Bound in `create_deep_agent` / `graph.py`. Frontend copy still says `recursionLimit: 10000`. Binding **10000** has historically been a no-op in LangGraph `merge_configs` (sentinel). Cite [08]; do not re-derive |
| Never `LocalShellBackend` / host `FilesystemBackend` on a deployed Agent Server | Official production page: “Don't use them in deployed agents” |
| Always pass `thread_id` + `context` on prod invokes | Independent knobs: conversation cursor vs per-run identity/flags |

---

## What Is This?

**The same compiled graph, a different place it runs.** You do not rewrite Deep Agents for production. You export `agent = create_deep_agent(...)` (or a cheap async factory), put it in `langgraph.json`, and let **Agent Server** provision threads, runs, a store, and a checkpointer so the application does not wire Postgres itself. API replicas accept HTTP, persist a pending run, and stream SSE. **They do not execute the graph** in split/cloud mode. Queue workers acquire a lease, run LangGraph super-steps, write checkpoints, and publish stream events. Redis is **signaling only** (wake-up sentinel, cancel/stream pub/sub, attempt counter) — **no user/run payloads**. Postgres holds assistants, threads, runs, crons (always) and, by default, checkpoints and the store.

Two hosted paths, same runtime underneath:

| Path | What it is | Auth / tenancy | Extra platform |
| --- | --- | --- | --- |
| **Managed Deep Agents (MDA)** | CLI-first hosted runtime (`mda dev` / `mda deploy`) that compiles a Deep Agents project, syncs Context Hub, uploads a build, and creates a LangSmith Deployment | Docs: LangSmith key or Supabase-class identity; **not** the place for custom HTTP routes or arbitrary `@auth` | Persistence, memory mounts, skill loading, sandbox lifecycle owned by LangSmith |
| **LangSmith Deployment (direct)** | You ship `langgraph.json` + graph export; Agent Server hosts assistants / threads / runs / crons | Custom `@auth.authenticate` + `@auth.on.*` resource filters; Agent Auth OAuth; workspace RBAC for operators | Auth, webhooks, cron, observability; can expose the agent as **MCP or A2A** |

**Doc-status conflict (do not collapse):** going-to-production (fetched 2026-09-02) still says MDA is **private preview** (join waitlist). LangChain’s product blog says MDA is **public beta**, LangSmith Cloud **US region only**, CLI-first while the API finalizes. Treat MDA as **US-cloud beta with waitlist/docs lag**; do not claim EU/hybrid MDA.

Self-host remains: open-source harness on your LangGraph/Agent Server image. You then own Postgres, Redis, secrets, upgrades, and tracing destination.

Think of a restaurant. **The graph is the recipe** (already written in [08]). **Agent Server is the kitchen line**: ticket printer (API), cooks (queue workers), walk-in (Postgres), pager (Redis). You do not invent a second stove. You do not let the waiter (`LocalShellBackend`) cook on the pass.

## Why It Matters

Almost every “how do you productionize Deep Agents?” interview now forks here: is production a new runtime, or Agent Server around the same `CompiledStateGraph`? Trap answers: “Deep Agents ships Temporal,” “disconnect cancels the run,” “Last-Event-ID resumes protocol v2,” “`permissions=` is the MCP gateway,” “bind `recursionLimit: 10000`,” “use LocalShell with `virtual_mode`,” “skip `thread_id` if you have `context`.”

The only named production anecdote is LangChain’s GTM agent: ~**10k requests/week**, **>150** active users, **26%** user-initiated / **74%** ambient (cron/event) — a **traffic shape**, not a p99. Interviews test whether you can split **control plane vs data plane**, name the **four retry layers**, pin `>=0.7.9`, refuse LocalShell, always pass `thread_id`, and put a Zero-Trust MCP **gateway** on egress even after Agent Server gives you MCP **ingress** for free.

---

### 1. System Topology & Data Flow

LangSmith splits **control plane** (org, deployments, revisions, billing, listener desired-state) from **data plane** (Agent Servers + Postgres + Redis + secrets + autoscalers). The listener in the data plane polls control-plane APIs and reconciles create/update/delete of deployments. Cloud: both planes hosted by LangChain (US/EU implied by org region; deployments **cannot migrate regions**). Hybrid: SaaS control plane, customer data plane. Self-hosted: both in VPC; air-gapped license option.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  Cloud: traces → project named after the deployment (automatic)  │
         │  Local: LANGSMITH_TRACING + LANGSMITH_API_KEY (+ PROJECT)        │
         │  Filter: metadata.lc_agent_name ; ls_integration=deepagents [08] │
         │  Engine (opt.): every 6 h, meters in LCUs; Polly → online evals  │
         │  Audit logs: create/update/delete_deployment (actor, ts, OCSF    │
         │    class 6003). Revisions = graph version (env snapshot + SHA)   │
         │  PII: detect→redact→audit BEFORE traces/checkpoints (not 0.7.9)  │
         │  Dual-SDK OTel+LangSmith = duplicate trees / double-bill [05]    │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ Engine / evals    │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (LLM-free: deploy + run-config; identity is NOT the model) │
│                                                                           │
│  LangSmith UI / langgraph deploy / mda deploy                             │
│  langgraph.json: dependencies, graphs (id→"./file.py:export"), env,       │
│    optional auth.path="./auth.py:auth"                                    │
│  Revision: git SHA / uploaded archive / image; env snapshot for rollback  │
│  Workspace secrets; @auth module; deployment type/size (type IMMUTABLE)   │
│  Workspace RBAC (Admin/Editor/Viewer — Enterprise; else everyone Admin)   │
│  Run submit knobs: thread_id, assistant_id, context, durability,          │
│    on_disconnect, multitask_strategy, recursion_limit                     │
│  Listener: polls CP, reconciles desired-state of data-plane deployments   │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ desired-state / revision
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted model+tools live on WORKERS, not on API replicas)  │
│                                                                           │
│  ┌──────────────┐  persist pending run (no graph exec in split/cloud)     │
│  │ Agent Server │──────────────────────────────────────────────┐          │
│  │ API replicas │  /threads /runs /stream SSE /join /cancel    │          │
│  │ (stateless;  │  store CRUD; MCP+A2A INGRESS; webhooks       │          │
│  │  no session  │                                              │          │
│  │  stickiness) │◀── Redis PubSub (stream/cancel; no payloads)─┤          │
│  └──────┬───────┘                                              │          │
│         │ Redis wake-up sentinel (list; no run payload)        │          │
│         ▼                                                      │          │
│  ┌──────────────────────────────────────────────────────────┐  │          │
│  │ QUEUE WORKERS  (N_JOBS_PER_WORKER default 10 RUN slots)  │  │          │
│  │  claim lease → load graph (compiled once at container    │  │          │
│  │    start, or async factory EVERY run) → super-steps →    │  │          │
│  │    checkpoint at durability cadence → publish events     │  │          │
│  │  AT MOST ONE RUN PER thread_id                           │  │          │
│  │  heartbeat timestamp in Redis; SIGINT grace then requeue │  │          │
│  │  HITL interrupt(): worker RELEASES slot; sleep unbounded │  │          │
│  └────────┬───────────────┬─────────────────┬───────────────┘  │          │
│           │               │                 │                  │          │
│  ┌────────┴──────── TOOL PROXIES (least privilege — not host)─┐│          │
│  │ Sandbox BaseSandbox / LangSmithSandbox / Daytona + AUTH    ││          │
│  │   PROXY (${OPENAI_API_KEY}) — never sandbox env/files      ││          │
│  │ MCP EGRESS: gateway PEP still required (permissions= ≠     ││          │
│  │   MCP). MDA: HTTP/SSE only; stdio unsupported              ││          │
│  │ MCP/A2A INGRESS: free with deploy; same @auth as /runs     ││          │
│  │ Cron: stateful (append thread) vs stateless (new thread)   ││          │
│  │ Webhook: HTTPS + domain allowlist (SSRF otherwise)         ││          │
│  │ NEVER LocalShellBackend / host FilesystemBackend           ││          │
│  └────────────────────────────────────────────────────────────┘│          │
└─────────┬───────────────┬─────────────────┬────────────────────┘          │
          │               │                 │                               │
          ▼               ▼                 ▼                               │
┌───────────────────────────────────────────────────────────────────────────┤
│ PERSISTENCE LAYER                                                         │
│                                                                           │
│  ┌────────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │
│  │ Postgres (default) │  │ Redis              │  │ Optional Mongo      │  │
│  │ assistants,threads,│  │ wake sentinel list │  │ checkpoints ONLY    │  │
│  │ runs, crons ALWAYS │  │ cancel pub/sub     │  │ Postgres STILL req. │  │
│  │ checkpoints dflt   │  │ stream pub/sub     │  │ for threads/runs/   │  │
│  │ store dflt         │  │ attempt counter    │  │ assistants          │  │
│  │ thread_id < 255    │  │ NO user/run bytes  │  │                     │  │
│  │ EncryptedSerializer│  │ prolonged outage = │  │ InMemorySaver =     │  │
│  │ if LANGGRAPH_AES_  │  │ Agent Server down  │  │ prototype only      │  │
│  │ KEY present        │  │                    │  │                     │  │
│  └────────────────────┘  └────────────────────┘  └─────────────────────┘  │
│  Do NOT pass checkpointer=/store= in graph code on Agent Server — the     │
│  server injects (and REPLACES) whatever the app configured. Local scripts │
│  still pass them; that is the prototype/prod fork.                        │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? |
| --- | --- | --- |
| **Control (deploy)** | UI / `langgraph deploy` / `mda deploy`; `langgraph.json`; revision; workspace secrets; `@auth` path; type/size; RBAC who may create/update/delete deployments | Yes |
| **Control (run config)** | `thread_id`, `assistant_id`, `context` / `context_schema`, `durability`, `on_disconnect`, `multitask_strategy`, `recursion_limit` on submit | Yes |
| **Data (Agent Server API)** | HTTP: create thread/run, `/stream` SSE, `/join`, cancel, store CRUD. **Does not execute the graph** in split/cloud mode | Yes for routing |
| **Data (queue workers)** | Load graph, acquire lease, run super-steps, write checkpoints, publish stream events | No — untrusted model+tools |
| **Data (Postgres)** | Assistants, threads, runs, crons (**always**); checkpoints (default); store (default) | Persistence of untrusted content |
| **Data (Redis)** | Wake-up sentinel, cancel pub/sub, stream pub/sub, attempt counter. **No user/run payloads** | Ephemeral signaling |

**`langgraph.json` (canonical fields on the production page):** `dependencies` (packages; `["."]` reads `requirements.txt` / `pyproject.toml` / `package.json`); `graphs` (graph ID → `"./file.py:export"`, used as the API assistant id; examples use `"agent"`); `env` (`.env` path; vars set at **build** and available at runtime); optional `auth.path`. Full options (Docker steps, store indexing, auth handlers) live in application-structure docs, not the production page.

**How the graph is hosted (registration modes):**

1. **Compiled graph (recommended).** Export `agent = create_deep_agent(...)`. Server loads **once at container start**. No per-request compile.
2. **Async factory.** Export `async def agent(config: RunnableConfig)`. Server calls it **every run**. Required when the sandbox/backend must key off `config["configurable"]["thread_id"]` or `assistant_id`. Factories do **not** receive full `Runtime` (`server_info` / `execution_info` unavailable in the factory). Keep factories cheap.

Invocation contract: `config = {"configurable": {"thread_id": str(uuid7())}}` then `agent.invoke(input, config=config, context=Context(user_id="user-123"))`. SDK: `client.threads.create()` then `client.runs.stream(thread_id, "agent", input=..., context={...})`. `thread_id` scopes checkpoints and message history. `context` is per-run data tools/middleware read. Changing one does not change the other. Frontend: `useStream` local default `http://localhost:2024`; production `apiUrl` = LangSmith Deployment URL + `assistantId`. Persist `threadId` (`sessionStorage` / cookie) so remount rejoins the same thread and (if thread-scoped) the same sandbox.

**Runtime modes:**

| Mode | Who runs the queue | Typical |
| --- | --- | --- |
| **Single host** | API process manages the queue; no separate workers. Default for self-hosted | Dev / low traffic |
| **Split API + queue** | `queue.enabled: true`. Dedicated workers. API scales on request volume; workers on pending runs | Production self-host |
| **Distributed runtime** | Separate orchestration process vs execution process | “Large-scale / high concurrency” — **no public QPS number** |

Cloud Dedicated autoscales replicas for you; you do **not** set Helm replica counts. Self-host Helm examples **do**.

**Request-flow narrative (API replica → queue worker → graph → checkpoint → stream):**

1. **Admit.** Client hits an API replica: create thread (if needed) and `runs.create` / `runs.stream` with `thread_id` + `context` + assistant id (the `graphs` key, usually `"agent"`). Payload cap **25 MB** → HTTP **413**. Cloud default auth is the **developer** LangSmith API key in `x-api-key` until you add `@auth.authenticate`.
2. **Durable queue.** API persists a **pending** run in Postgres. Redis sentinel **wakes** a worker. Redis does **not** carry the run payload. Creating a run is a **fast write**; if all job slots are busy, runs **queue** (back-pressure, not an HTTP 429 from the worker).
3. **Lease.** A worker claims the lease (Postgres MVCC; no long-lived DB locks; exactly-once **attempt** semantics). **At most one run per `thread_id` at a time.** Worker loads the compiled graph (already in memory) or calls the async factory. Heartbeat timestamp written to Redis while owned.
4. **Super-steps.** LangGraph runs. Checkpoints write at the deployment’s durability cadence (`"async"` is Agent Server default). Successful nodes in a super-step are saved and **not re-run** on resume; the **interrupted node restarts from the top of its function**. Worker publishes events on Redis PubSub. Events are **not stored** in Redis.
5. **Stream.** Any API replica with an open `/stream` (or `joinStream`) subscribes via Redis and forwards **SSE**. Protocol v2 is **POST** `/threads/{id}/stream/events`; resume cursor is body field **`since`** (seq) — **not** `Last-Event-ID`. Bounded per-run buffer; earliest events of a long run **may be evicted**. SDK dedupes by durable `event_id`. Consume **`stream.subagents` / `thread.subagents`**, not raw subgraph namespaces.
6. **Disconnect.** Default: **run keeps going**. `useStream.disconnect()` is client-only leave. Opt-in `on_disconnect="cancel"` on wait/stream/join. Rejoin: same `thread_id` + `since`.
7. **Stop / release.** Completion status, slot release. HITL `interrupt()` is **not** cancel: worker **releases the slot**, sleep unbounded, resume via `Command(resume=...)`. Cancel **interrupt** (default): status `interrupted`; checkpoints kept. Cancel **rollback**: delete run + its checkpoints; thread restored to pre-run. At least **one queue worker must listen** or runs orphan.

**Cloud SKUs (published sizes, not QPS):**

| Type | Scaling | Database | Intended |
| --- | --- | --- | --- |
| **Serverless** S/M/L | Scale-to-zero after inactivity (beta on new usage pricing); wake on next request | Shared multi-tenant | Background, latency-tolerant, preview |
| **Dedicated** S/M/L | Always-on; autoscale replicas | Dedicated Postgres, backups, HA | Customer-facing critical path |

| Resource | Srv S | Srv M | Srv L | Ded S | Ded M | Ded L |
| --- | --- | --- | --- | --- | --- | --- |
| Runtime vCPU | 1 | 2 | 4 | 3 | 5 | 10 |
| Runtime GiB | 2 | 5 | 9 | 6 | 12 | 24 |
| DB vCPU | — | — | — | 1 | 2 | 4 |
| DB GiB | — | — | — | 4 | 8 | 16 |
| Storage | Shared | Shared | Shared | Auto-scale disk | Auto-scale | Auto-scale |

Immutable: **deployment type cannot change** after create; **size can** (new revision, no downtime claimed). Legacy Dev/Prod types remain until **2026-10-01** for orgs on previous pricing. Static NAT IPs per region published for allowlisting egress from Cloud deployments created after **2026-01-06**. Plus plan **minimum** for Cloud agent deploys.

**Hosting vs tracing destination:**

| Option | Tracing | Telemetry for billing | License check |
| --- | --- | --- | --- |
| Cloud | Required → LangSmith SaaS | SaaS | API key vs SaaS |
| Hybrid | Optional: off or SaaS | SaaS | API key vs SaaS |
| Self-hosted | Off, SaaS, or self-hosted LangSmith | Self-reported usage if air-gapped; else SaaS | Air-gapped key or platform key vs SaaS |

**Memory scoping (production implication only — VFS catalog in [09]):** Memory is **files on a backend**. Thread scratch (`StateBackend`) is **not** cross-thread memory. Cross-thread requires `StoreBackend` (or Hub) on a route such as `/memories/`. User namespace `(assistant_id, user.identity)` or `(user.identity,)` — only that user can poison it. Assistant `(assistant_id,)` is a **prompt-injection channel** (any user of that assistant). Org `(org_id,)` must be app-owned **read-only** via `permissions` deny-write. `rt.server_info` factories need `deepagents>=0.5.0`.

**Platform surfaces that ship with every LangSmith Deployment (not the harness):** MCP **ingress** (Claude Desktop / IDEs / other agents call *your* agent) — egress to remote MCP still needs a gateway. **A2A** across deployments. **Webhooks** (outbound POST of the run payload on completion; pass `webhook` URL at run create; HTTPS, headers, domain allowlists). **Cron** scheduled `runs.create` — same durability, tracing, auth; **delete crons or they keep billing**. Stateful cron (`create_for_thread`): each tick **appends** the same conversation. Stateless cron (`create`): new thread per tick; `on_run_completed`: `"delete"` (default) vs `"keep"` then find via `client.runs.search(metadata={"cron_id": ...})`. GTM’s Monday account-intel job and Salesforce-triggered inbound drafts are this ambient plane. `deepagents deploy` / `deepagents.toml` is the packaging path the runtime blog describes; MDA’s `mda deploy` is the current CLI name on product pages — **same Agent Server underneath**.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (production, not a new scheduler)

**I1.** Deep Agents introduces **no** production runtime. Durable execution, streaming, interrupts, checkpoints, stores, crons, MCP/A2A ingress = **Agent Server / LangGraph**. The object you deploy is the same `CompiledStateGraph` [08] assembled.

**I2.** API replicas **route**. Queue workers **execute**. Coupling them in your head produces “the HTTP timeout is my agent SLO” — false for a 20-minute research run.

**I3.** **One run per `thread_id`** is the concurrency boundary. There is **no** distributed lock API in Deep Agents. Two local `invoke`s on one `thread_id` without Agent Server: checkpointer CAS / undefined — **[inferred]**; do not promise.

**I4.** `thread_id` and `context` are independent. Missing `thread_id` means every `useStream` mount is a new conversation; disconnect cannot rejoin; HITL resume targets the wrong cursor; stateful cron cannot continue.

**I5.** Do not pass `checkpointer=` / `store=` in graph code on Agent Server — injected and any app-configured checkpointer **replaced**. Local scripts still pass them.

**I6.** `permissions=` covers **built-in FS tools only**. Production still needs the MCP gateway from [07]/[09]. Agent Server MCP/A2A is **ingress**.

#### 2.2 Four retry layers (easy to confuse)

Deep Agents **does not** install retry middleware by default — you append it. Fault-tolerance page examples use `ModelRetryMiddleware(max_retries=3, ...)` as a **call-site override**, not a new default. `InMemoryRateLimiter` on the chat model is **process-local**, not a cluster token bucket.

| Layer | Default | Retries what | Does **not** retry |
| --- | --- | --- | --- |
| **1. Chat model HTTP** | `init_chat_model(..., max_retries=6)` | 429 / 5xx / network | 401 / 404. Raising to 10–15 is documented for flaky networks **and** you still want a checkpointer |
| **2. LangGraph node `RetryPolicy`** | `max_attempts=3` (includes first); `initial_interval=0.5s`; `backoff_factor=2.0`; `max_interval=128s`; `jitter=True` | Exceptions matching `retry_on`. `default_retry_on`: retry **except** `ValueError`, `TypeError`, `ArithmeticError`, `ImportError`, `LookupError`, `NameError`, `SyntaxError`, `RuntimeError`, `ReferenceError`, `StopIteration`, `StopAsyncIteration`, `OSError` (+ subclasses). HTTP libs: **5xx only**. `NodeTimeoutError` **is** retryable | Those excluded types. Non-retryable node exception → attempt fails; sweeper will **not** help |
| **3. Agent Server run attempt** | **3** attempts for **transient Postgres errors** during the run; attempt count in Redis, short TTL | PG blips during the run | Model 429s. This is **not** a model retry |
| **4. Middleware** `ModelRetryMiddleware` / `ToolRetryMiddleware` | `max_retries=2` ⇒ **3 total attempts**; `backoff_factor=2.0`; `initial_delay=1.0s`; `max_delay=60s`; jitter ±25% | Model: retryable + unclassified; skip `is_retryable=False` (`langchain>=1.3.16`). Tool: optional `tools=[...]` allowlist; `on_failure='continue'` default (error `ToolMessage`) | Tools you did not allowlist. Retrying `read_file` is wasted work — scope to network tools |

`ModelFallbackMiddleware("gpt-5.5")`: provider **outage** path, not per-request 429. Fireworks cache headers stripped on cross-provider fallback (cite [08]).

Call caps (runaway loops) are a **fifth** concern, not a retry layer: `ModelCallLimitMiddleware.run_limit` (this invoke, no checkpointer) vs `.thread_limit` (**needs checkpointer**); `ToolCallLimitMiddleware.run_limit`. `recursion_limit` counts LangGraph **super-steps**, not model calls. A confused agent can burn budget **inside** 9,999 super-steps. Caps are application middleware, **not** installed by `create_deep_agent`. Guardrail middleware (retry/fallback/PII/call-limit) is **open source LangChain**, not “free with deploy.” A Cloud deploy without caller `middleware=` still has durable execution and traces, **not** PII redaction or spend caps. `OpenAIModerationMiddleware` is named on the runtime blog; it is **not** a Deep Agents default.

Official error-class → who-fixes-it (fault-tolerance page): transient (network, 429) → system, exponential backoff (layers 1/4); LLM-recoverable (bad tool output, parse) → model, error `ToolMessage` (`ToolErrorMiddleware`, `langchain>=1.3.14`); user-fixable → human, `interrupt_on` (checkpointer required); provider outage → `ModelFallbackMiddleware`; runaway loops → call-limit middleware; unexpected → bubble (`on_error` returns `None`). Compose **retry outside, error-convert inside**: retries exhaust first, then ToolError, else bubble — same order as LangGraph node retry-then-handler. `NodeTimeoutError` (langgraph≥1.2): per-attempt timeout; timer **resets each retry**; orthogonal to Deep Agents tool `execute` timeout (catalog in [09], cap 3600 s).

#### 2.3 `recursion_limit`: 9,999 vs 10000 sentinel

`create_deep_agent` binds `recursion_limit: 9_999`. Frontend examples still pass **10000**. LangGraph `merge_configs` has historically **dropped** `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10000) — binding 10000 is a **no-op** and nested graphs fall back (bare LangGraph historically **25**; graph-API docs also mention default **1000** since 1.0.6 — **verify on pinned `langgraph`**). Cite [08] for the dodge; do not re-derive.

Hitting the limit is `GraphRecursionError` / `GRAPH_RECURSION_LIMIT` — a **hard error**, not graceful degrade. Historical subagent bug: parent did not forward config → children ran at 25, surfaced as `CancelledError` (issue #1698; cite [08]). Graph API offers a `RemainingSteps` managed-value channel so the graph can **route to an end node before** the ceiling. Deep Agents’ 9,999 bind makes RemainingSteps less urgent for shallow bots and **more** necessary if you need a **product** hop cap below 9,999. `recursion_limit` is super-steps, not “max subagents.” Still set `ModelCallLimitMiddleware` / product max-`task`.

#### 2.4 Streaming: `on_disconnect`, protocol v2 `since`, dualism trap

Two APIs coexist: LangGraph `stream` / `stream_mode` with `subgraphs=True` (v2 chunks), and event streaming (`stream_events` / `version="v3"`) with typed projections (`messages`, `tool_calls`, `values`, `output`) plus Deep Agents’ **`stream.subagents`** (one handle per `task` delegation, `name` = `subagent_type`). UI should prefer `stream.subagents`. Agent Server aliases `thread.subagents`.

| Event | Worker | Client |
| --- | --- | --- |
| TCP/SSE drop, **default** | Continues to completion / interrupt | Misses live tokens until rejoin |
| `on_disconnect="cancel"` | Cancel requested (interrupt vs rollback per cancel API) | Avoids zombie spend |
| `stream.disconnect()` | Continues | Intentional background |
| Rejoin | Unchanged | `thread_id` + seq `since`; SDK auto |

**Protocol dualism (interview trap).** The 2026 runtime blog still describes **thread** streaming resume via the **`Last-Event-ID` header** and claims gapless replay. Protocol v2 docs are explicit: POST-only SSE, **no** `Last-Event-ID`, client sends **`since`** in the JSON body. Browser `EventSource` / `Last-Event-ID` **does not apply**. `client.runs.stream()` is **run-scoped**; `client.threads.joinStream()` / `useStream` remount is **thread-scoped** (follow-ups, background runs, HITL resumes on one connection). Bounded buffer: late join of a long run can look like “the UI skipped the plan step.” Until the blog is updated, implement **whatever the SDK version you pin actually sends**; verify with a disconnect test, do not cite Last-Event-ID as the v2 contract. No published stream-buffer size. Redis PubSub does not persist the event stream; a slow SSE client plus bounded replay ⇒ **dropped early tokens**, not HTTP 429 from the worker.

**Double-texting** (new input while a run is `running`):

| Strategy | Effect |
| --- | --- |
| **`enqueue` (default)** | Queue the new run; no state corruption; user waits |
| **`reject`** | 409-class refuse until current run ends |
| **`interrupt`** | Halt, **keep** checkpoints, start new input from that state. Partial in-flight tool calls may need `PatchToolCallsMiddleware` ([08]) |
| **`rollback`** | Halt and **delete** the in-flight run’s checkpoints; treat the new message as a fresh run |

Chat UIs that feel snappy often want `interrupt`; GTM Slack drafts should stay `enqueue`/`reject` so a second webhook does not wipe research **[inferred from runtime blog + GTM blog]**. Rollback as a footgun: user correction deletes the first run’s checkpoints.

**Time travel:** every super-step checkpoint is a fork point. Studio/API: pick `checkpoint_id`, optionally patch state, resume as a **branch**; original history intact. LLM/tool/interrupt all **re-trigger** (not a stub replay). Debugging “wrong tool at step 5 of 20” without re-paying steps 1–4 is the pitch. Replay of a nondeterministic agent is **not** audit truth — cite [05].

#### 2.5 Worker sweeper (2 min) and durability state machine

While a worker owns a run it writes a **heartbeat timestamp in Redis**.

**Graceful SIGINT:** stop new HTTP; allow in-progress runs a grace period then re-queue; stop picking new jobs.

**Hard crash:** sweeper looks for in-progress runs past heartbeat window; **sweeper interval 2 minutes**; re-enqueue; another instance resumes from last checkpoint. Instances are **stateless**; **no session stickiness**; any API replica can stream/cancel any worker via Redis PubSub.

LangGraph checkpoints at **super-step** boundaries, not mid-function.

| Mode | When it writes | Failure implication |
| --- | --- | --- |
| `"async"` **(Agent Server default)** | After each step, async vs next step | Small crash window: last step may be lost |
| `"sync"` | Before next step | Highest durability, extra latency |
| `"exit"` | Only on graph exit (success, error, interrupt) | Fast; **no mid-run crash recovery** |

Keep `"async"` for long Deep Agents. `"exit"` + worker kill = lost mid-run work. `"sync"` on a token-heavy Deep Agent adds a Postgres RTT every super-step. Postgres is default checkpointer (LangSmith). Optional MongoDB for checkpoints only; **Postgres still required** for threads/runs/assistants. `InMemorySaver` / `MemorySaver`: lost on process restart — prototype only. `StateBackend` files are checkpointed **every step** — do not write large blobs; prune TTL on threads.

On resume/retry the affected **node function runs from line 1**. Side effects before `interrupt()` or before a crash **happen again**. Mitigations: upserts, idempotency keys, move effects **after** interrupt, or wrap in Functional API **tasks** so completed task results restore from the checkpointer. Production implication: `execute`, MCP `tools/call`, CRM writes, Slack posts must be **idempotent or gated by HITL after the effect is prepared** (GTM: “nothing is sent without explicit approve”). `PatchToolCallsMiddleware` repairs dangling tool_calls after cancel (harness default — [08]); it does not make Slack idempotent. Graph **structure** may change across revisions; resume uses **saved state + newly compiled graph**. Changing `interrupt`/task **order inside a node** can mismatch cached values.

**Async is a throughput control, not a style preference.** LLM apps are I/O-bound. Production page: implement **async tools** (sync tools run in a thread but still cost a worker slot’s event loop if you block); async middleware hooks (`abefore_agent`); async sandbox/MCP lifecycle — which is why graph **factories are async**. Prefer `asyncio.sleep` over `time.sleep`. `N_JOBS_PER_WORKER` default **10** concurrent **runs per worker container** — bounds **run** concurrency, not HTTP request concurrency. Formula: `available_jobs = workers × N_JOBS_PER_WORKER`; `throughput_per_s ≈ available_jobs / avg_run_s`.

**Complexity [architecture, not a paper]:** admit path is \(O(1)\) Postgres write + Redis wake. Run duration is the graph (model RTT × turns), unbounded except product caps and 9,999 super-steps. Crash RTO is dominated by the **2 min** sweeper, not by API p99. Stream fan-out is PubSub subscribe per open SSE — events not retained in Redis, so replay is a **bounded buffer** problem, \(O(\text{buffer})\) not \(O(\text{run})\).

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: LangChain does **not** publish Agent Server latency SLOs, TTFT for Deep Agents, tokens/sec of a worker, or stream-buffer size. Treat any percentile below as **[inferred] policy** to put on *your* graph + model, or as documented intervals (sweeper **2 min**) converted to ms. Traffic-shape anecdote is not an SLO. Do not say “Deep Agents handles 500 rps” — that Helm table is generic, 1 s “standard assistant” runs.

#### 3.1 Trace SKUs (observability bill) — published

A trace is **one application execution** (agent run, evaluator, playground), not one LLM call. Hard cap: **25,000 runs per trace**; further runs rejected.

| Metric | What | Price |
| --- | --- | --- |
| **LangSmith Traces (Base Charge)** | Every ingested **trace** (root run + children = one trace), any retention tier | **0.05¢ per trace** = **$0.50 / 1k traces** |
| **Extended Data Retention Upgrades** | Upgrade to 400-day (Enterprise-customizable) | Upgrade **0.45¢**; extended all-in **0.50¢ / trace** = **$5.00 / 1k** |

Retention: base **14 days**; extended **400 days**. After expiry, I/O deleted within a day; some metadata kept for analytics/billing. **Do not use third-party $2.50/1k** figures unless they cite a current pricing page. Official usage-and-billing (fetched 2026-09-02) is **0.05¢ / 0.50¢**. Pricing page defers unit list to the calculator and LCU/LSU FAQ.

Included allotments: Developer **5k** base traces/mo (1 seat); Plus **10k**/mo then pay-as-you-go; Enterprise custom. Developer with **no card**: monthly unique traces hard-stop at 5,000 (HTTP 429).

Hourly ingest 429s: Developer no card **50,000** events / **500 MB**; Developer card **250,000** / **2.5 GB**; Plus **500,000** / **5.0 GB**; Enterprise custom. ALB: `POST/PATCH /runs*` **5,000 / min**; catch-all **2,000 / min**; SDK batches up to **100** runs per session.

Auto-upgrade to extended (cost trap): online evaluators and automation rules **default on** for new configs; UI feedback/notes do **not** upgrade; experiments default extended. GTM-style “attach Slack send/edit/cancel to the trace” is feedback — only extends if `extend_trace_retention=true`.

#### 3.2 Agent Server / Deployment compute SKUs — published + Dedicated Small **[inferred]**

Normalized units: **1 LCU = $1.50**, **1 LSU = $1.00**.

| Meter | Rate |
| --- | --- |
| Runtime compute | **0.045 LCU / vCPU-hr** |
| Runtime memory | **0.006 LCU / GiB-hr** |
| Database compute | **0.177 LSU / vCPU-hr** |
| Database memory | **0.025 LSU / GiB-hr** |

Plus: **1 free Serverless (Small)** included. Additional Serverless/Dedicated billed on resource time. **Uptime** = database live from create until delete; prod stays up across revisions. Serverless still bills compute while provisioned **including idle-before-scale-down**.

Dedicated always-on **inferred** monthly floor from published size × rates (using **720 hr** = 24×30; 730 hr/mo ≈ 24×30.4 is **[inferred 730]** if you annualize):

**Dedicated Small** (3 vCPU, 6 GiB, DB 1 vCPU / 4 GiB) **[inferred]**:

| Line | Math | USD / 720 h |
| --- | --- | --- |
| Runtime CPU | 3 × 0.045 LCU × 720 × $1.50 | **$145.80** |
| Runtime mem | 6 × 0.006 LCU × 720 × $1.50 | **$38.88** |
| DB CPU | 1 × 0.177 LSU × 720 × $1.00 | **$127.44** |
| DB mem | 4 × 0.025 LSU × 720 × $1.00 | **$72.00** |
| **Infra subtotal** | | **~$384 / mo** |

Plus seats ($39/seat Plus), traces, sandboxes, Engine, model APIs — **not** in that floor. AWS Marketplace self-hosted listing quotes **$150k** platform license + **$150k** usage commitment (enterprise packaging; not a per-trace public SKU). Do not treat Marketplace $0.01/trace as the Cloud self-serve rate.

Sandbox meters (Plus/Developer include **5 LCU + 1 LSU / mo**; Developer cap **10 sandboxes**): compute **0.0384 LCU / vCPU-hr**, memory **0.0123 LCU / GiB-hr**, storage **0.000123 LSU / GiB-hr**, billed **per second**. Catalog of sandbox sizes/TTLs: [09], not recopied.

Engine: **~5–30 LCU per Engine run** (estimate), schedule **once / 6 h**, model usage included in LCU. Disable in Settings to stop the meter.

#### 3.3 `$ cost per 1k` executions including tracing **[inferred]**

Assumptions (not a SKU). 1k Deep Agent **runs** = 1k LangSmith **traces** (one root per run). **Model line** uses the same **[inferred]** mix as [08] §3.3 (numbers inlined so this section stands alone):

- Model: `anthropic:claude-sonnet-4-6` at list prices (input **$3 / MTok**, output **$15 / MTok**).
- Medium research run: **10** model calls, all inside one **5-minute** window (cache stays warm). GP subagent **off**.
- **Tokens per call:** **2,000** cached prefix (v0.7 tools + empty authored prompt) / **3,000** uncached input / **800** output. Dynamic 3k never cached.
- **Prompt cache:** Anthropic TTL **5m**; **1× 5m write** of the 2k prefix + **9× reads** of the same 2k. Multipliers (current Claude except Fable/Mythos 5.1): **5m write = 1.25×** base input (**$3.75 / MTok**); **read = 0.1×** (**$0.30 / MTok**).

| Model path | / run | **Model $ / 1k** |
| --- | --- | --- |
| Cached (1 write + 9 reads of 2k + 30k uncached + 8k out) | $0.2229 | **$223** |
| Uncached (10 × 5,000 in + same 8k out) | $0.270 | **$270** |

Caching saves ~**$47 / 1k** at a 2k prefix. Production rollups below use the **cached** model line (**$223 / 1k**). Tracing:

| Trace tier | Trace $ / 1k runs | Model $ / 1k | **Sum / 1k** |
| --- | --- | --- | --- |
| Base 14d | $0.50 | $223 | **~$224** |
| Extended 400d | $5.00 | $223 | **~$228** |

Tracing is **noise** next to model spend at this shape. It becomes material if you emit a new root per subagent or per eval replica, or if online evals auto-upgrade **every** GTM run, or if you add a second LangChain callback in graph code on Cloud Agent Server (**double-bill traces [inferred from 05 + usage-and-billing]**).

Deployment infra amortized: Dedicated Small ~$384/mo / 10k runs/week ≈ 40k runs/mo → **~$10 / 1k runs** infra **[inferred from GTM volume × Dedicated S]**. All-in at that volume: **~$234 / 1k** (base traces) or **~$238 / 1k** (extended) **[inferred]**. At low volume the infra line dominates traces (idle Dedicated S is still ~$384/mo). Chat-model HTTP retries (layer 1) are extra model spend, not a separate SKU.

#### 3.4 Latency SLA — p50 / p95 / p99 numeric ms

> ⚠️ Gap: **No published p50/p95/p99 of hosted `invoke` / `runs.stream`, no TTFT SLO for first SSE after Serverless scale-from-zero** (docs only: first request after scale-down is slower), no worker tokens/sec. Helm examples assume average run **1 s** for a “standard assistant” — **not** measured Deep Agents p99. GTM ~10k req/week is not a latency SLA.

Clock-split: (a) LLM-free admit (API persist + Redis wake); (b) queue wait (unpublished; back-pressure = queue depth); (c) first SSE event / parent TTFT; (d) Serverless wake; (e) one ReAct cycle on a worker; (f) full research run; (g) checkpointer tax; (h) worker-crash reclaim (sweeper **2 min** documented); (i) HITL — a **different clock**, worker frees the slot.

**[inferred] policy targets — numeric ms.** Anchors: inner-chat TTFT histogram buckets used in [08]/[05] (640 / 2,560 / 5,120 ms) plus one API+Redis hop; ReAct cycle class from [08] (2 s / 8 s / 20 s); 10-call research shape from [08]; checkpointer class from [08]; sweeper interval **120,000 ms** (documented). These are **not** Agent Server measurements.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Run-create API persist + Redis wake (no model)** **[inferred policy]** | **20 ms** | **80 ms** | **250 ms** | “Creating a run is a fast write.” Postgres insert + sentinel. Timeout this independently of the graph. 25 MB payload → 413, not a slow write |
| **Dedicated warm SSE first-event / parent TTFT** **[inferred policy]** | **800 ms** | **3,200 ms** | **6,400 ms** | [08] 640 / 2,560 / 5,120 plus API hop ~160 / 640 / 1,280. Stream; always-on Dedicated; persist `threadId`; do not wait on LangSmith export |
| **Serverless scale-from-zero first SSE** **[inferred policy]** | **5,000 ms** | **20,000 ms** | **60,000 ms** | **Unpublished.** Docs: first request after scale-down is slower. Policy: chat UX on Dedicated; Serverless for ambient/cron. Scale-down delayed **30 minutes** (1,800,000 ms) on Dedicated autoscale — different knob |
| **Queue wait when all `N_JOBS` slots busy** **[inferred policy]** | **0 ms** | **5,000 ms** | **30,000 ms** | Unpublished buffer. Back-pressure is **queue**, not worker 429. Scale workers on **10 pending runs / container**; API on CPU/RAM so a submit spike does not stall thread reads |
| **One ReAct cycle on worker (model + local FS)** **[inferred]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Same class as [08]. Worker slot occupied the whole time unless HITL |
| **10-call research run, GP off, no summarize** **[inferred]** | **20,000 ms** | **80,000 ms** | **200,000 ms** | [08] cost-section shape. Do not put it on a chat HTTP timeout. Use SSE + rejoin |
| **Checkpointer `"async"` extra per super-step** **[inferred policy]** | **5 ms** | **30 ms** | **100 ms** | Agent Server default. Small crash window |
| **Checkpointer `"sync"` extra per super-step** **[inferred policy]** | **10 ms** | **50 ms** | **200 ms** | [08] fsync-class tax. Use when compliance demands; not the default |
| **Checkpointer `"exit"` extra on the hot path** **[inferred policy]** | **0 ms** | **0 ms** | **0 ms** | No mid-run recovery. Forbidden as the long-horizon default |
| **Worker hard-crash reclaim (sweeper)** **[inferred from documented 2 min]** | **60,000 ms** | **120,000 ms** | **180,000 ms** | Sweeper interval **120,000 ms**. Heartbeat window unpublished — p99 = interval + slack. Resume from last checkpoint, not from zero |
| **HITL interrupt clock** **[inferred policy]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Worker **releases slot**. Sleep unbounded in-product; p99 expire-deny is **your** timer ([12]). Not a Chat Completions SLO |
| **`GraphRecursionError`** | — | — | **hard error** | 9,999 is a fuse. Product cap must fire **earlier** |

**Mitigations mapped to percentiles:**

- **p50 (user):** Dedicated (no scale-to-zero); stream; compiled graph not a heavy factory; async tools (`asyncio.sleep`); `"async"` durability; persist `thread_id`.
- **p95:** scale queue workers on pending runs; `N_JOBS_PER_WORKER` lower for RAM-heavy Deep Agents (greedy fetch otherwise); do not block the worker event loop with `time.sleep`; bounded SSE buffer → persist `threadId` and accept late-join eviction of early tokens.
- **p99:** HITL off the request thread (slot release already helps capacity); Serverless wake **is** the tail if you put chat there; sweeper 2 min **is** the crash tail — design UX for rejoin, not for 200 ms failover; never wait on Engine / LangSmith export; product hop cap ≪ 9,999.

#### 3.5 Throughput / back-pressure (queue, LCU/LSU)

**Published traffic shape (still valid 2026-09-02):** LangChain internal GTM agent on `deepagents` + LangSmith Deployments: almost **10k requests/week**, **>150** active users, **26%** user-initiated / **74%** ambient. Converted **[inferred]**: 10,000 / (7×86,400) ≈ **0.0165 QPS average**. That is **not** a latency SLO and **not** peak QPS. GTM outcome metrics (separate blog, 2026-03-09; not harness SLOs): inbound conversion **+250%** Dec 2025–Mar 2026; **3×** pipeline dollars; **40 hours/rep/month** reclaimed; **50%** daily / **86%** weekly active usage. Treat as LangChain-reported product metrics, not transferable SLAs.

**Self-host sizing examples** (average run **1 s**, “standard assistant”) — **request rates the Helm examples target**, not measured Deep Agents p99:

| Pattern | Write rps | Read rps | API replicas (1 CPU, 2 Gi) | Queue workers | `N_JOBS_PER_WORKER` |
| --- | --- | --- | --- | --- | --- |
| Low/low | 5 | 5 | 1 | 1 | 10 |
| Low/high write | 5 | 500 | 6 | 10 | 50 |
| High/low write | 500 | 5 | 10 | 1 | 10 |
| Med/med | 50 | 50 | 3 | 5 | 10 |
| High/high | 500 | 500 | 15 | 10 | 50 |

Autoscaling Cloud Dedicated: target **75%** CPU, **75%** memory, **10 pending runs / container**; scale-down delayed **30 minutes**. Queue workers scale on pending runs; API servers on CPU/RAM.

A 20-minute research run with `N_JOBS_PER_WORKER=10` and 1 worker is **0.008 completed runs/s** if fully utilized **[inferred]** — ambient GTM batch is this regime, not the 500 rps table. HITL pause **frees the slot**; a 48-hour Slack approval does not hold a worker.

**Back-pressure design:** (1) admit is a fast Postgres write — capacity-plan **queue depth** and **worker slots**, not API RPS; (2) bulkhead API (reads/submit) vs workers (runs) vs sandbox vs checkpointer vs model TPM; (3) Redis PubSub + bounded SSE buffer ⇒ dropped early tokens, not 429; (4) `N_JOBS_PER_WORKER` too high → greedy fetch, uneven memory; (5) `StateBackend` large files checkpointed every step — plan **checkpoint storage**; (6) payload > 25 MB → 413, offload blobs (GTM offloaded Gong/CRM instead of a custom truncator); (7) 25k runs/trace cap — don’t explode a single root; (8) LCU/LSU are the **money** back-pressure: Dedicated S ~$384/mo floor; Engine 5–30 LCU / 6 h; Serverless idle-before-scale-down still bills; (9) cron you forget **keeps billing**.

#### 3.6 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability** | Prolonged Redis **or** Postgres outage ⇒ Agent Server **unavailable**. Momentary blips: client retries on retryable errors. Dedicated Cloud Postgres: backups + standby failover. At least one queue worker must listen or runs orphan. Chat SLO ≠ worker crash SLO (sweeper 2 min) | Always-on Dedicated $ vs Serverless wake vs self-host HA you staff |
| **RPO of Postgres checkpoints** | `"sync"`: last super-step before next. `"async"` (default): small window — last step may be lost. `"exit"`: **empty if crash mid-run**. `InMemorySaver`: **empty on restart**. Encrypted blobs if `LANGGRAPH_AES_KEY` present (LangSmith auto-on); this encrypts checkpoint blobs, **not** LangSmith trace I/O | Crash-consistency vs p50 (`sync` extra **10 / 50 / 200 ms [inferred]**) |
| **RTO of Postgres checkpoints** | Resume `thread_id` from last checkpoint after sweeper re-enqueue (**60,000 / 120,000 / 180,000 ms [inferred]** crash path). Any replica can resume (state in PG, not worker RAM). Time-travel `checkpoint_id` is a **branch**, re-triggers LLM/tools — debugger, not audit. Oversize `thread_id` → DB error (keep **< 255**) | Time-to-resume vs forensic truth ([05]) |
| **RPO of Redis** | Signaling only — **no** user/run payloads to lose. Prolonged outage = cancel/stream/heartbeats die = **unavailability**, not silent data loss | Stream UX vs durability (events not in Redis) |
| **RPO of store / memories** | Last Store put. Namespace `(assistant_id, user.identity)` recommended. Org namespace **read-only** | Lifelong memory vs prompt-injection |
| **RPO of traces** | Base 14 d / extended 400 d; hourly 429s. Sampled/hidden I/O is lossy by policy. 0.7.9 omitted middleware tracing **inputs** — you already lost that tape | Debug vs PII vs $5/1k extended |
| **Compliance** | **Not provided by `deepagents`.** Retention + hide-I/O + region (US/EU, **no cross-region migrate**) are the published knobs. LangSmith ToS: they do **not** train on your traces. SOC2/HIPAA/GDPR: traces, checkpoints, VFS bytes are subprocessors if they hold prompts. MDA is **US Cloud only**. GDPR erasure of a thread is checkpointer+store+sandbox+trace purge, not `thread_id` TTL | Time-to-debug (content-on) vs residency |
| **Correctness vs $** | Models dominate `$/1k` (~$223 vs $0.50 traces vs ~$10 infra at GTM volume). Online eval auto-upgrade and dual-SDK traces are the cost traps. Recursion 9,999 is not a budget | Agency vs wallet |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_checkpoint = last durable super-step (`sync` / `async` window / `exit` empty). RTO_checkpoint_happy = resume `thread_id` (seconds, API path). RTO_checkpoint_crash = sweeper interval class (**~2 min**). RPO_sandbox = last snapshot / TTL (docs example `idle_ttl_seconds=3600` — [09]). RTO_sandbox = new container; sandbox OOM ≠ worker death (bulkhead). A `GraphRecursionError` is a **completed refuse**, not an RPO hole.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: Agent Server = LangGraph runtime with PG + Redis (Temporal-equivalent)

Deep Agents does **not** wrap Temporal. The Temporal-shaped story you tell in an interview is: **workflow identity = `thread_id`**, **history = Postgres checkpoints at super-step boundaries**, **task queue = Postgres durable run queue + Redis wake**, **workers = queue replicas with leases and heartbeats**, **signals = Redis pub/sub (cancel/stream)**, **timers = cron + HITL unbounded sleep with slot release**, **replay = resume from checkpoint (node restarts at line 1; completed pending writes skipped)**. That **is** LangGraph + Agent Server. You do not buy a second workflow engine to “make Deep Agents production.”

| Temporal analog | Agent Server / LangGraph |
| --- | --- |
| Workflow id | `thread_id` (< 255 chars) |
| Event history | Checkpoints (`"async"` / `"sync"` / `"exit"`) |
| Activity worker | Queue worker; `N_JOBS_PER_WORKER=10` |
| Task queue | Postgres pending runs + Redis sentinel |
| Sticky execution | **None** — stateless replicas |
| Signal / query | Redis cancel/stream pub/sub; `/join`; `Command(resume=...)` |
| Retry policy | Four layers in §2.2 — **not** one Temporal retry |
| Continue-as-new | Thread TTL / new thread (stateless cron) |
| Search attributes | Run metadata; `cron_id`; LangSmith trace |

Exactly-once **attempt** semantics via Postgres MVCC; no long-lived DB locks. Sandbox OOM kills the **sandbox**, not the worker (process isolation, **not** a circuit breaker).

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Model 429/5xx, network, PG blip during run, Redis momentary, NodeTimeoutError | Error rate; retryable flags | Layer 1 HTTP (6); layer 2 node (3 attempts, jitter); layer 3 PG (3); layer 4 middleware if installed. Client retries on retryable Agent Server errors |
| **Permanent** | `ValueError` et al. excluded from `default_retry_on`; 401/404; `GraphRecursionError`; construction errors; `@auth` 403 | Non-retryable; sweeper will **not** help | Fail closed. Do not bump `recursion_limit` to 10000 as a “fix.” Do not retry 401 |
| **Poison-pill threads** | (1) Non-retryable node exception → thread left failed/interrupted. (2) Retryable crash loop: worker dies every time the same node runs (OOM from a huge `StateBackend` file) — sweeper re-queues every ~2 min × remaining PG attempts (3) → burn then fail. (3) HITL never resumed: correct durable sleep; **occupies thread** (one run per thread) until resume/cancel. (4) Recursion ceiling. (5) Double-text `rollback` deleting checkpoints | Failed/interrupted status; sweeper churn; thread stuck in interrupt; `GRAPH_RECURSION_LIMIT` | Shrink state; sandbox bulkhead; cancel/resume from any replica; product cap; avoid `rollback` for GTM-like drafts. `"durability=exit"` does **not** help mid-node |
| **Idempotency of invoke** | Resume restarts the node from line 1; duplicate Slack / `execute` / CRM write; two webhooks on one thread (`enqueue` vs `rollback`) | Duplicate side effects | Upserts; idempotency keys; HITL **after** draft; Functional API tasks. Agent Server **one run per thread** is the invoke idempotency boundary for hosted — local dual `invoke` is undefined **[inferred]** |
| **Zombie spend** | SSE drop while worker continues (default); cron never deleted; Serverless idle-before-scale-down; Engine every 6 h | Trace $; LCU; orphan crons | `on_disconnect="cancel"` for abandoned chats; cron lifecycle in destroy; disable Engine; Dedicated vs Serverless chosen on purpose |
| **Denial of wallet** | 9,999-step loop; no call-limit middleware; online eval auto-upgrade; dual-instrument traces; 25k runs/trace | Token ledger; $5/1k extended; 429 ingest | Caps; opt out retention extension; emit **once**; filter middleware traces |

#### 4.3 Circuit breaker closed → open → half-open (yours, not the product)

> ⚠️ Gap: **Limited public data inside Deep Agents** for circuit breakers, leader election, or poison-pill quarantine APIs. Resilience is heartbeat + sweeper + retries + HITL slot release. Put breakers in the **client / worker wrapper** around model, sandbox allocate, and checkpointer writes.

Independent breakers: **model**, **sandbox**, **checkpointer** (and optionally store, MCP gateway). A model 429 must not stall thread **reads** on the API replica (**bulkhead**) **and** must not fail open to `LocalShellBackend`.

```
        model 429/5xx | sandbox 503 | checkpointer timeout | error-rate window
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  invoke  │  success resets consecutive count                     │ FAIL FAST│
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

| Trip condition | Closed → open | Half-open probe | Fallback (**not** LocalShell) |
| --- | --- | --- | --- |
| Model 429/5xx | consecutive ≥ **5** or error-rate window | One tiny run, GP off | **Hosted Agent Server → self-host graph invoke → deterministic refuse** |
| Sandbox pool empty / 503 | allocate failures ≥ **3** | One allocate | Queue or 503 — **never** `LocalShellBackend` / host `FilesystemBackend` |
| Checkpointer timeout | consecutive ≥ **3** | One checkpoint write | Fail closed for HITL / “must resume”; refuse. Do not silently switch to `InMemorySaver` |
| Redis / PG prolonged outage | plane down | Health check | Agent Server unavailable — **refuse**, do not run unsandboxed on the API box |
| MCP gateway 5xx | error-rate | One `tools/list` | Fail closed on egress tools; do not bypass the PEP with raw headers |

**Fallback chain (required interview answer):** **LangSmith-hosted Agent Server → self-host the same compiled graph (`invoke` with your Postgres checkpointer) → deterministic refuse.** Never: circuit open → `LocalShellBackend`. Never: HITL timeout → auto-approve. Never: model 429 → unsandboxed `execute`. Never: hosted outage → skip `@auth`.

#### 4.4 Zero-Trust MCP (still required in prod) and tool-level RBAC

`permissions=` covers **built-in FS tools only**. MCP `tools/call`, custom tools, `execute`, and `backend.*` are **out of that PDP**. Production still needs the MCP gateway/PEP from [07]/[09]: server allowlist, tool allowlist, **hash-pin** descriptions (canonical JSON of name + description + schemas; re-verify every `tools/call`; CVE-2025-54136 MCPoison), **OAuth 2.1** + PKCE, **RFC 8707** audience = canonical MCP server URI on authorize *and* token, servers accept only tokens whose audience is themselves, **no token passthrough** (obtain a new token, typically RFC 8693 exchange) to upstream APIs, interceptor rate limits. Static `Authorization: Bearer` examples are **not** OAuth 2.1. Stdio MCP is unsupported on MDA; **HTTP/SSE only**.

Agent Server **exposing** your agent as MCP/A2A is an **ingress** surface — still put a gateway in front of **egress** MCP. Ingress without `@auth` = anyone with the URL invokes the graph. Put the **same** auth handlers on `/runs` and on MCP/A2A ingress.

**Tool-level RBAC via deployment authz (two layers the production page separates):**

**A. Operator RBAC** (who deploys / views traces) — LangSmith workspace roles. Workspace Admin: full, including settings and members. Workspace Editor: create/modify; cannot delete runs or manage members (wording varies slightly vs user-management). Workspace Viewer: read-only. RBAC is an **Enterprise** feature; other plans default everyone to Admin. Custom roles + ABAC (tag policies; deny overrides RBAC) also Enterprise. Org-level Admin/User/Viewer distinct from workspace roles.

**B. End-user AuthN/Z on Agent Server:**

- Cloud default: LangSmith API key in `x-api-key` (the **developer**, not the end user) until you add `@auth.authenticate`.
- Custom auth: all Cloud plans. Handler returns at least `identity`; extra fields (tokens, `org_id`) land on `config["configurable"]["langgraph_auth_user"]`.
- `@auth.on.threads` / `.assistants` / `.store`: tag `owner`, return **filters**, or HTTP **403**. Most-specific handler wins.
- Self-hosted default: **no auth** — you must add it.
- Studio: `is_studio_user()` bypass pattern so developers are not locked out of their own graph.

`context.user_id` is **not** authentication. Bind tenant from **verified** `runtime.server_info.user.identity` (or MDA `runtime.identity`). Store namespaces: `(assistant_id, user.identity)` recommended default. Agent Auth: managed OAuth; first use **interrupts** with consent URL; tokens stored/refreshed. Sandbox **auth proxy** injects headers from workspace secrets so guest code never sees keys. MDA: tools/middleware get a frozen `runtime.identity` envelope when the project declares identity — prefer that over client-supplied `configurable` keys. Fetch user credentials from a **secret store**, not graph state. Returning OAuth tokens from `@auth.authenticate` into `langgraph_auth_user` puts them on the run config — they can leak into traces if not hidden **[inferred leak path]**.

| Location | OK? |
| --- | --- |
| LangSmith **workspace secrets** | For Agent Server env (model keys) |
| Auth proxy `${OPENAI_API_KEY}` rules | Recommended for sandbox egress |
| Sandbox env / files / `secrets=` | **Forbidden** — agent can read them |
| Graph state / checkpoints | **Not recommended** |
| MDA `.env` non-reserved keys | Forwarded as hosted secrets; reserved `LANGSMITH_*` **not** uploaded as user secrets |

| Backend | Deployed Agent Server |
| --- | --- |
| `StateBackend` / `StoreBackend` / `ContextHubBackend` | Yes — files only |
| `BaseSandbox` / `LangSmithSandbox` / Daytona / … | Yes — isolated `execute` |
| `FilesystemBackend` / **`LocalShellBackend`** | **No** — “Don't use them in deployed agents” |

`LocalShellBackend.virtual_mode` does **not** jail `execute()` — cite [09]. Thread-scoped sandbox `idle_ttl_seconds=3600` example; assistant-scoped shared disk **unbounded growth** — TTL/snapshots/cleanup required. Skill scripts that must **run** in-guest: `upload_files` in `abefore_agent`. Path-traversal filter in the sample `_safe_filename`.

#### 4.5 PII pipeline — detect → redact → audit

Default Cloud/local tracing **logs inputs and outputs** when tracing is on. `PIIMiddleware` redacts/masks/hashes/blocks **before the model**; it is **not** in the default Deep Agents stack. `>=0.7.9` omit middleware trace **inputs** shrinks wrapper-span PII/volume — **not** DLP. LLM Gateway PII policies are fail-close scanners; they do not cover traces that bypass the gateway — cite [05]/[08]. `PIIMiddleware` does **not** scan sandbox files the model never re-reads as messages ([09] §4.5). Checkpoints still hold VFS bytes.

| Control | Effect |
| --- | --- |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` | Strip I/O for SDK + LangChain |
| `Client(hide_inputs=..., anonymizer=...)` | Per-client; anonymizer skipped if HIDE_* true |
| `tracing_context` replica `updates` | Per-request empty I/O, keep structure; set `project_name` or updates may drop |
| `PIIMiddleware` | Before the model; you append it |
| `>=0.7.9` omit middleware trace inputs | Volume/PII shrink, not a DLP program |

**Pipeline (explicit — three steps, all required):**

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner (Bedrock/Presidio/gateway). Scan: user input, model output, tool args/results, VFS writes, memory-write candidates, log/trace payloads, webhook bodies, HITL UI. If ML is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on tool args to external MCP / sandbox env / webhook — do not send raw PAN to a third-party server or into a checkpoint.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the task can continue; `block` when the field must not exist (secrets paths, MCP args, webhook). Strip the value from VFS **and** from the message channel. Do **not** persist raw PAN in traces (sampled APM is not this step).
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`redact` / `mask` / `hash` / `block`), detector (`regex` | `pii-middleware` | `gateway`), `correlation_id`, `tenant`, `thread_id`, permission decision, tool **arg digest**. A tool call without an audit row is a control-plane bug. Retention: security evidence *and* a sensitive-data asset — GDPR erasure vs legal hold is digest-level. Chain-of-custody for agent decisions: checkpointer `checkpoint_id` + arg digest + deployment **revision** — **not** “LangSmith has the prompt so we are SOX-ready.”

#### 4.6 Immutable deploy / audit logs

**Revisions** are the graph-version object: first revision at deploy; later `langgraph deploy` or UI “New Revision.” A revision records **env vars it was created with** so rollback restores code **and** config; GitHub revisions keep **repo ref + commit SHA**. Rollback/redeploy without code change **reuses the built image**.

**Audit logs** (org settings, membership, credentials, workspaces, **deployments**): operations include `create_deployment`, `update_deployment`, `delete_deployment`. Each event: timestamp, **actor** (user UUID or API key), operation, resources, success. API: `GET /api/v1/audit-logs` with `start_time`/`end_time`, filter `operations`, `actor_ls_user_ids`, `actor_api_key_ids`, `resource_ids`; OCSF class 6003 for SIEM. Operator role required.

This is **who changed the deployment**, not an immutable WORM log of every `execute`. Syscall audit does not exist in-tree ([09] §4.6). Combine: revision SHA + audit `update_deployment` + your PII/decision WORM + checkpointer `checkpoint_id`. Identity in tools: without `@auth.authenticate`, LangGraph sees only the **API-key owner** — requests are **not** scoped to end users.

---

### 5. Production Enterprise Code

Self-contained. Optional `langgraph` / `langsmith` / `deepagents` imports. Stdlib path runs the same control flow: retries + full jitter, circuit breakers (model / sandbox / checkpointer), fallback **hosted Agent Server → self-host graph invoke → refuse**, PII detect→redact→audit, structured logs with `correlation_id` / `thread_id`, graceful degradation. Never LocalShell. Always requires `thread_id`. Run: `python deep_agents_production.py`.

```python
#!/usr/bin/env python3
"""Production client around a compiled Deep Agents graph.

Fallback: hosted Agent Server → self-host invoke → deterministic refuse.
Never LocalShellBackend. thread_id is mandatory.
Run: python deep_agents_production.py
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
#   from langgraph.checkpoint.postgres import PostgresSaver
#   from langsmith import Client as LangSmithClient
#   from deepagents import create_deep_agent


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("thread_id", "-"),
            ("plane", "-"),
            ("breaker", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("da_prod")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"thread":"%(thread_id)s","plane":"%(plane)s",'
            '"breaker":"%(breaker)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, **extra: Any) -> None:
    LOG.log(level, msg, extra=extra)


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.5,
    cap_s: float = 8.0,
    retryable: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> Any:
    """Full-jitter backoff. Mirrors node RetryPolicy spirit (jitter=True), not a SKU."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = min(cap_s, base_s * (2**i))
            sleep_s = random.random() * sleep_s
            slog(logging.WARNING, f"retry_backoff attempt={i + 1} sleep_s={sleep_s:.3f}", plane="client")
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
                "cid": correlation_id, "tenant": tenant_id, "thread_id": thread_id,
                "sink": sink, "kinds": kinds, "action": action, "pre": pre,
                "post": post, "detector": "regex",
            }
        )

    if "pan" in kinds and block_on_pan and sink in {"mcp_args", "sandbox_env", "webhook"}:
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
    plane: str
    degraded: bool


class GraphPort:
    name: str

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str: ...


def require_thread_id(config: dict[str, Any]) -> str:
    thread_id = (config.get("configurable") or {}).get("thread_id")
    if not thread_id:
        raise InvokeError("permanent", "thread_id_required")
    if len(str(thread_id)) >= 255:
        raise InvokeError("permanent", "thread_id_too_long")
    return str(thread_id)


@dataclass
class ScriptedPort(GraphPort):
    """Stdlib stand-in for hosted Agent Server or a local compiled graph."""

    name: str
    fail_kind: str | None = None

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        require_thread_id(config)
        if self.name == "local_shell":
            raise InvokeError("permanent", "local_shell_forbidden")
        if self.fail_kind == "transient":
            raise InvokeError("transient", f"{self.name}_429")
        if self.fail_kind == "permanent":
            raise InvokeError("permanent", f"{self.name}_down")
        user = payload.get("user") or ""
        return f"ok:{self.name}:{user[:80]}"


def try_hosted_client() -> GraphPort | None:
    """Illustrative Agent Server runs.stream. Not used by build_runtime() (no keys)."""
    try:
        from langsmith import Client  # type: ignore
    except Exception:
        return None

    class _H(GraphPort):
        name = "agent_server"

        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
            thread_id = require_thread_id(config)
            # Production: client.runs.stream(thread_id, "agent", input=...,
            #   context=...). Disconnect ≠ cancel. Resume via body field since.
            try:
                client = Client()
                chunks = [str(ev) for ev in client.runs.stream(thread_id, "agent", input=payload)]
            except Exception as exc:
                raise InvokeError("transient", type(exc).__name__) from exc
            return "".join(chunks) or "ok:agent_server"

    return _H()


def try_self_host_graph() -> GraphPort | None:
    """Compiled graph + checkpointer. Agent Server injects these in prod —
    pass them only on the self-host fallback / local scripts. Never LocalShell."""
    try:
        from deepagents import create_deep_agent  # type: ignore
        from langgraph.checkpoint.memory import InMemorySaver  # type: ignore
    except Exception:
        return None
    graph = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=[],
        checkpointer=InMemorySaver(),  # prod self-host: PostgresSaver.setup()
        excluded_tools={"execute"},
        name="prod-fallback",
    )

    class _G(GraphPort):
        name = "self_host_graph"

        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
            require_thread_id(config)
            result = graph.invoke(payload, config=config)
            messages = result.get("messages") or []
            last = messages[-1] if messages else ""
            return getattr(last, "content", str(last))

    return _G()


def deterministic_refuse(reason: str) -> str:
    return json.dumps({"status": "refused", "reason": reason})


@dataclass
class ProductionRuntime:
    hosted: GraphPort
    self_host: GraphPort
    model_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("model"))
    sandbox_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("sandbox"))
    checkpointer_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("checkpointer", failure_threshold=3)
    )
    self_host_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("self_host"))
    audit: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        user_text: str,
        *,
        tenant_id: str,
        thread_id: str,
        correlation_id: str | None = None,
        on_disconnect: str = "continue",
    ) -> RunResult:
        cid = correlation_id or str(uuid.uuid4())
        extra = {
            "correlation_id": cid,
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "plane": "client",
        }
        if not thread_id:
            raise InvokeError("permanent", "thread_id_required")
        safe = pii_detect_redact_audit(
            user_text,
            audit=self.audit,
            correlation_id=cid,
            tenant_id=tenant_id,
            thread_id=thread_id,
            sink="model_input",
            block_on_pan=False,
        )
        payload = {"messages": [{"role": "user", "content": safe}], "user": safe}
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"cid": cid, "tenant_id": tenant_id},
            "context": {"user_id": tenant_id},
            "on_disconnect": on_disconnect,
        }

        def _guarded(port: GraphPort, *, plane: str, runtime_breaker: CircuitBreaker) -> str:
            extra["plane"] = plane
            slog(logging.INFO, "invoke_start", **extra)

            def _once() -> str:
                extra["breaker"] = "checkpointer"
                self.checkpointer_breaker.allow()
                extra["breaker"] = "sandbox"
                self.sandbox_breaker.allow()
                extra["breaker"] = runtime_breaker.name
                runtime_breaker.allow()
                return port.invoke(payload, config)

            try:
                text = retry_call(_once)
                runtime_breaker.record_success()
                self.sandbox_breaker.record_success()
                self.checkpointer_breaker.record_success()
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
                slog(logging.ERROR, "circuit_open", **extra)
                raise
            except InvokeError as exc:
                if exc.kind == "transient":
                    runtime_breaker.record_failure()
                slog(logging.ERROR, f"invoke_fail:{exc.kind}", **extra)
                raise
            except PermissionError:
                slog(logging.ERROR, "pii_block", **extra)
                raise
            except Exception as exc:
                runtime_breaker.record_failure()
                slog(logging.ERROR, "invoke_fail:unexpected", **extra)
                raise InvokeError("transient", type(exc).__name__) from exc

        try:
            return RunResult(
                _guarded(self.hosted, plane="agent_server", runtime_breaker=self.model_breaker),
                "agent_server",
                False,
            )
        except (CircuitOpenError, InvokeError, TimeoutError, ConnectionError) as exc:
            if isinstance(exc, InvokeError) and exc.kind == "permanent" and "thread_id" in str(exc):
                return RunResult(deterministic_refuse(str(exc)), "refuse", True)
            slog(logging.WARNING, "fallback_self_host", **{**extra, "plane": "self_host_graph"})
            try:
                text = _guarded(
                    self.self_host, plane="self_host_graph", runtime_breaker=self.self_host_breaker
                )
                return RunResult(text, "self_host_graph", True)
            except (CircuitOpenError, InvokeError, TimeoutError, ConnectionError):
                slog(logging.ERROR, "fallback_refuse", **{**extra, "plane": "refuse"})
                return RunResult(deterministic_refuse(type(exc).__name__), "refuse", True)


def build_runtime() -> ProductionRuntime:
    """Stdlib ports so this file runs without keys. Swap in try_* when live."""
    return ProductionRuntime(
        hosted=ScriptedPort(name="agent_server"),
        self_host=ScriptedPort(name="self_host_graph"),
    )


if __name__ == "__main__":
    rt = build_runtime()
    r1 = rt.run(
        "Summarize ticket 55 for ada@example.com",
        tenant_id="acme",
        thread_id="t-1",
        correlation_id="cid-1",
    )
    print(r1)
    assert "[EMAIL_" in r1.text
    assert any(row["action"] in {"redact", "allow"} for row in rt.audit)
    assert r1.plane == "agent_server"

    rt.hosted = ScriptedPort(name="agent_server", fail_kind="transient")
    rt.model_breaker = CircuitBreaker("model", failure_threshold=1, cooldown_s=60)
    r2 = rt.run("hello", tenant_id="acme", thread_id="t-2", correlation_id="cid-2")
    print(r2)
    assert r2.degraded is True
    assert r2.plane == "self_host_graph"

    rt.self_host = ScriptedPort(name="self_host_graph", fail_kind="permanent")
    r3 = rt.run("hello", tenant_id="acme", thread_id="t-3", correlation_id="cid-3")
    print(r3)
    assert r3.plane == "refuse"

    try:
        rt.run("hello", tenant_id="acme", thread_id="")
        raise SystemExit("expected thread_id_required")
    except InvokeError as exc:
        assert exc.kind == "permanent"

    blocked = False
    try:
        pii_detect_redact_audit(
            "4111 1111 1111 1111",
            audit=rt.audit,
            correlation_id="cid-4",
            tenant_id="acme",
            thread_id="t-4",
            sink="mcp_args",
        )
    except PermissionError:
        blocked = True
    assert blocked
    print("ok", len(rt.audit), "audit rows")
```

**Wiring notes (not in the script):** on Agent Server **delete** `checkpointer=` / `store=` from the deploy branch — the server injects them. Pin `deepagents>=0.7.9`. Durability `"async"` for long runs. `on_disconnect` default continue; `"cancel"` only for abandoned interactive chats. Frontend persist `threadId`; protocol v2 resume is `since` in POST body. MCP egress through a gateway (RFC 8707, no passthrough). `excluded_tools={"execute"}` unless a sandbox + auth proxy is bound. `PIIMiddleware` in `middleware=` — not default. Disable Engine / online-eval auto-upgrade if you do not want extended retention. Cron delete on teardown. Never `LocalShellBackend`.

---

### 6. Architectural System Design Scenarios

#### Scenario A — LangSmith-hosted vs self-host Agent Server

**Problem.** A regulated SaaS wants Deep Agents in production for an internal research copilot plus a smaller external-facing assistant. Security wants EU data-plane residency **or** a documented US-only exception, custom JWT `@auth`, no host shell, per-user memory, and an audit trail of **who deployed which graph**. Platform is split three ways: “just `mda deploy`,” “LangSmith Cloud Dedicated,” or “Helm Agent Server in our VPC.” Traffic may later look GTM-shaped (~10k req/week, mostly ambient). They already have the same `CompiledStateGraph` from [08]. They must not invent a Temporal rewrite.

**Proposed architecture (recommended when custom auth + Dedicated HA matter and US/EU org region is acceptable):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: langgraph.json + @auth.authenticate            │
  │ JWT →   │   │   @auth.on.threads/store owner filters                  │
  │ identity│   │   workspace secrets + sandbox AUTH PROXY                │
  │         │   │   revisions (SHA + env snapshot) + audit logs SIEM      │
  │         │   │   pin deepagents>=0.7.9  recursion bind 9999            │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ DATA: Cloud Dedicated M (always-on; autoscale 75%/10-pending/30m)     │
  │   API replicas (no graph exec)  queue workers  N_JOBS=10              │
  │   Postgres checkpointer+store  Redis signaling only                   │
  │   compiled graph (factory only if thread-scoped sandbox)              │
  │   StateBackend scratch + /memories/ StoreBackend                      │
  │     ns=(assistant_id, user.identity)                                  │
  │   MCP EGRESS via gateway PEP (RFC 8707, hash-pin, no passthrough)     │
  │   MCP/A2A INGRESS behind the same @auth                               │
  │   PII detect→redact→audit; HIDE_* ; PIIMiddleware appended            │
  │   NEVER LocalShell ; excluded_tools={execute} unless sandbox          │
  │   useStream + persisted threadId ; on_disconnect default continue     │
  └───────────────────────────────────────────────────────────────────────┘
```

**Technology choices:** Direct **LangSmith Deployment** (not MDA) because custom `@auth` / extra HTTP routes / webhooks / MCP/A2A ingress are first-class on Deployment and limited on MDA. Dedicated not Serverless because chat TTFT after scale-to-zero is unpublished. Hybrid/self-host if the org region cannot hold the data (MDA is **US-only**; Cloud deployments **cannot migrate regions**). Plus plan minimum for Cloud agent deploys.

**Trade-off matrix:**

| Axis | **A1 Cloud Dedicated Deployment (recommended if region OK)** | **A2 MDA (CLI-first beta)** | **A3 Hybrid / self-host split API+queue** |
| --- | --- | --- | --- |
| **Cost** | **[inferred] ~$384/mo Dedicated S** (M is larger: 5 vCPU / 12 GiB / DB 2 / 8 — do not fake a monthly $ without multiplying published meters). + traces $0.50–$5/1k + models **~$223/1k [08]** + seats | Same Agent Server under the hood; likely Serverless idle + seats. Waitlist/docs lag | Your k8s + PG HA. Marketplace self-hosted listing **$150k + $150k** usage commitment is packaging, not Cloud SKU |
| **Latency** | Always-on; autoscale 75% / 10 pending / 30 m cool-down. Warm SSE **800 / 3,200 / 6,400 ms [inferred policy]**. No published p99 | Unpublished; US Cloud only; CLI while API finalizes | You tune `N_JOBS_PER_WORKER`. Same sweeper **2 min** crash class |
| **Ops complexity** | `langgraph deploy` / GitHub revisions; they run workers | Lowest (`mda deploy`) | Highest: queue, Redis, backups, upgrades, tracing destination, **auth default-off** |
| **Security posture** | Custom auth + Agent Auth + proxy; workspace secrets; audit `update_deployment`; MCP ingress still needs `@auth`; egress gateway still required | Limited identity; not the place for arbitrary `@auth` / custom routes | You own network. Self-hosted default **no auth** is a finding until you add it. `LANGGRAPH_AES_KEY` or checkpoints plaintext |
| **Scalability** | Size S–L; extra resources via support. Type immutable after create | “Same Agent Server.” Cannot place EU data plane via MDA | Linear workers × 10 jobs. Helm 500 rps table is **1 s assistants**, not Deep Agents |

**Decision.** **A1 wins** when you need custom auth, Dedicated HA, webhooks/MCP/A2A, and the org region already matches (US or EU). **A2 wins** when the agent **is** the product and you will live with US + CLI + LangSmith identity. **A3 wins** when data residency, air-gap, or platform-standard k8s is the constraint — you re-implement scale math in §3.5. None of these is a new runtime. Do not pick A3 to “get Temporal.”

#### Scenario B — High-HITL interactive copilot vs 74% ambient batch

**Problem.** Same Agent Server, two products. (1) A Slack/web copilot: humans in the loop on sends, Dedicated TTFT, rejoin after laptop sleep, `interrupt_on` that may sleep for hours. (2) A GTM-like ambient plane: Salesforce trigger + Monday cron, **74%** of LangChain’s own traffic, 48h SLA auto-send as **application policy**, Slack buttons, one compiled subagent per account. Platform wants one graph, two NFR profiles, no LocalShell, no `on_disconnect=cancel` on batch (there is no client). The only public capacity anecdote is **~10k req/week, 150+ users, 26/74**.

**Proposed architecture (recommended: one Dedicated-or-split deployment, two admit paths):**

```
  ┌──────────────┐     ┌──────────────────────────────────────────────────┐
  │ Interactive  │     │ CONTROL: same langgraph.json / same assistant id │
  │ useStream    │────▶│   thread_id persisted; context per-run flags     │
  │ Slack HITL   │     │   durability=async; one run per thread           │
  └──────────────┘     │   Interactive: Dedicated; on_disconnect=continue │
                       │     double-text interrupt|enqueue; HITL on send  │
  ┌──────────────┐     │   Ambient: cron + webhook runs.create; no SSE    │
  │ Ambient 74%  │────▶│     double-text enqueue|reject; stateful cron    │
  │ Salesforce   │     │     for “Monday intel”; stateless for each lead  │
  │ Monday cron  │     │   Worker frees slot on interrupt()               │
  └──────────────┘     └──────────────────────┬───────────────────────────┘
                                              ▼
                       ┌──────────────────────────────────────────────────┐
                       │ DATA: queue workers; Postgres checkpoints; Redis │
                       │   signaling. Subagent-per-account + schema       │
                       │   contract (GTM). Offload Gong/CRM blobs.        │
                       │   Feedback send/edit/cancel → LangSmith trace    │
                       │   Watch evaluator retention auto-upgrade         │
                       │   MCP gateway still on egress. Never LocalShell  │
                       └──────────────────────────────────────────────────┘
```

**Technology choices:** Same compiled graph. SKU: Dedicated for the copilot (always-on, no scale-to-zero TTFT); Serverless or Dedicated Small for a **batch-only** service. Disconnect default continue + rejoin `thread_id` for interactive; no SSE for ambient. Idempotency: draft-then-approve (email send after resume) vs structured subagent schemas. 48-hour auto-send is **not** an Agent Server feature. OSS `examples/deploy-gtm-agent` is packaging (`deepagents.toml`, `AGENTS.md`, `mcp.json`) — **not** LangChain’s internal 10k-req/week graph.

**Trade-off matrix:**

| Axis | **B1 Split NFR on one Agent Server (recommended)** | **B2 Two deployments (chat Dedicated + batch Serverless)** | **B3 Interactive-only; fake batch with `on_disconnect=cancel` chat clients** |
| --- | --- | --- | --- |
| **Cost** | One Dedicated floor **[inferred] ~$384/mo S** + models + traces. Ambient slots free during HITL sleep | Two SKUs; Serverless saves idle **except** idle-before-scale-down still bills; plus two trace projects | Wasted Dedicated on a cron job; cancel kills work the user never watched |
| **Latency** | Chat: Dedicated warm **800 / 3,200 / 6,400 ms [inferred]**. Batch: queue + 20-minute run class **20,000 / 80,000 / 200,000 ms [inferred]** — webhook/Slack, not TTFT | Batch cold-start **5,000 / 20,000 / 60,000 ms [inferred policy]** on first tick after scale-to-zero; chat isolated | Cancel-on-disconnect makes laptop-sleep a **production incident** |
| **Ops complexity** | One revision, two admit paths (`multitask_strategy`, cron vs `useStream`) | Two revisions / secrets / auth surfaces; listener reconciles both | Looks simple; fights the 74% ambient shape |
| **Security** | Same `@auth` + gateway; namespace per identity; PII on traces **and** webhooks | Blast radius split; twice the audit surface | Cancel/rollback can **delete checkpoints** (rollback) — wrong for research drafts |
| **Scalability** | Workers scale on pending runs; HITL frees slots so 48h approvals do not hold `N_JOBS`. GTM **0.0165 QPS avg [inferred]** is not your peak | Batch can scale-to-zero; chat cannot. Type immutable — you cannot flip Serverless→Dedicated later | Concurrency = humans with laptops, not cron |

**Decision.** **B1 wins** when one graph and one tenancy model should serve both planes (LangChain’s own GTM). **B2 wins** when finance wants Serverless for overnight papers and will accept unpublished cold TTFT on that SKU — **and** you remember type is immutable, so you create two deployments rather than hoping to resize type. **B3 never wins**: disconnect ≠ cancel; 74% ambient is cron/webhook, not chat QPS. Capacity you can **say**: 10k req/week, 150+ users, 26/74. Capacity you must **not** say: hosted invoke p99, “Deep Agents handles 500 rps.”

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| `LocalShellBackend` in prod | Agent Server user = host user; `execute` is `subprocess.run(shell=True)`; `.env`, SSH keys, IMDSv2, sibling tenants in reach. `virtual_mode` only affects FS tools | Incident / review | `StateBackend`/`StoreBackend`/`BaseSandbox` only; HITL on `execute` if sandbox is on |
| No checkpointer | No `thread_id` resume; HITL cannot pause; factory cannot name `thread-{id}` sandbox; `useStream` remount starts a **new** conversation. `MemorySaver` dies on rolling deploy | Lost threads after deploy | Agent Server injected Postgres, or self-host `PostgresSaver.setup()` |
| Tracing PII | Prompts, tool args, CRM rows in LangSmith 14–400 days; hourly 5 GB Plus cap; extended auto-upgrade from online evals. Middleware spans used to duplicate full model requests | DLP on traces | HIDE_* / anonymizer / PIIMiddleware / gateway; **detect→redact→audit**; do not rely on 0.7.9 alone |
| `recursion_limit` 25 on children / 10000 sentinel | Nested `task` hits `GraphRecursionError` at **25** if bound config is dropped (#1698); UI sets `recursionLimit: 10000` and LangGraph **drops** it | Error at 25; or 10000 is a no-op | Pin current `deepagents`; parent bind **9,999**; confirm merge on pinned `langgraph`; product cap still required |
| Stream without `thread_id` | Every mount is a new thread; disconnect cannot rejoin; HITL resume wrong cursor; ambient cron cannot continue; bounded buffer eviction looks like skipped plan | New thread ids; “UI skipped step” | Persist `threadId`; `onThreadId`; optional `on_disconnect="cancel"` for abandoned chats |
| Dual-instrument OTel + LangSmith SDK | Duplicate trees, doubled token counts, conflicting parents. Cloud already traces to the deployment project | Two roots; doubled $ | Emit **once**; Collector fan-out. `LANGSMITH_OTEL_ENABLED` / `tracing_mode="hybrid"` is **migration**, not the target ([05]) |
| `excluded_tools` on `<0.7.9` | Hidden but executable | Schema gone; tool still runs | Pin `>=0.7.9` |
| Sync `time.sleep` in a tool | Blocks worker event loop; timeouts | Worker stalls | Async tools; `asyncio.to_thread` |
| `durability="exit"` + worker kill | No mid-run checkpoint | Lost run after crash | Keep `"async"` for long Deep Agents |
| `StateBackend` large files | Checkpointed **every step** | PG bloat; OOM crash loop + sweeper burn | Store/sandbox; prune TTL on threads |
| Assistant-scoped sandbox | Unbounded guest disk | Disk growth | TTL / snapshot / don’t share across tenants |
| Shared `/memories/` writable | Cross-tenant prompt injection | Unexpected cross-user reads | Namespace + deny-write permissions |
| Payload > 25 MB | HTTP 413 | 413 | Offload; don’t put blobs in `invoke` input |
| Serverless scale-to-zero | Cold TTFT unpublished | First-request slowness | Dedicated for chat UX |
| MDA US-only / CLI-only | Cannot place EU data plane via MDA | Residency review fail | Direct Deployment / hybrid |
| Checkpointer in graph code | Replaced/ignored by server | Local works, prod ignores your saver | Delete it in the deploy branch |
| Factory heavy I/O every run | Extra latency vs compiled graph | TTFT regression | Cache sandbox client; compiled graph if no per-run backend |
| `N_JOBS_PER_WORKER` too high | Greedy fetch, uneven memory | OOM / noisy neighbor | Lower for CPU/RAM-heavy Deep Agents |
| Redis prolonged outage | Cancel/stream/heartbeats die | Agent Server unavailable | Multi-AZ Redis; accept unavailability |
| 25k runs/trace | Extra spans rejected | Partial traces | Don’t explode a single root; filter middleware traces |
| GraphRecursionError uncaught | Run fails; user sees 500/interrupted | Hard error | Catch + RemainingSteps routing |
| Non-idempotent `execute` on failover | Duplicate shell / duplicate Slack | Dup side effects | HITL after draft; upserts |
| MCP without gateway | FS `permissions=` give false confidence | Writes via MCP not in PDP | Gateway PEP (RFC 8707, hash-pin, no passthrough) |
| Cron never deleted | Runs (and bills) forever | LCU / surprise $ | Lifecycle in deploy/destroy (`mda delete` / cron API) |
| Webhook to unallowlisted HTTP | SSRF / leaked run payload | Outbound POST | HTTPS + domain allowlist |
| A2A/MCP ingress without `@auth` | Anyone with the URL invokes the graph | Anonymous runs | Same auth handlers as `/runs` |
| `LANGGRAPH_AES_KEY` missing on self-host | Checkpoints plaintext at rest | Encryption review fail | Set key; rotate with dual-read plan **[inferred]** |
| Online eval on every GTM run | Base traces auto-upgrade to **0.50¢** | $5/1k | Opt out retention extension on evaluators |
| `durability="sync"` on token-heavy Deep Agent | Extra Postgres RTT every super-step | p50 regression | Keep `"async"` unless compliance demands sync |
| Stateless cron + `"delete"` then need the thread | Thread gone | Missing history | `"keep"` + search by `cron_id` |
| Protocol Last-Event-ID on v2 | Blog vs spec dualism | Failed resume | POST body `since`; verify with a disconnect test |

No public Deep Agents production incident post-mortem corpus beyond GitHub issues (#1698, #5809, #3952). Do not invent outages.

---

## Key Takeaways

- Production is **Agent Server around the same compiled Deep Agents graph**: API replicas do not run the model; queue workers do; Postgres holds checkpoints; Redis is only signaling. Not a new runtime, not Temporal-as-a-product.
- Pin **`deepagents>=0.7.9`**. Bind **9,999**, not UI **10000** (sentinel). Never **`LocalShellBackend`**. Always pass **`thread_id` + `context`**.
- Four retry layers: chat HTTP **6**, node `RetryPolicy` **3 attempts**, Agent Server PG **3**, middleware **2 extra** if installed. Sweeper **2 min**. Disconnect **≠** cancel. HITL **≠** cancel. Rollback cancel **deletes** checkpoints.
- Protocol v2 resume is **`since` in a POST body**, not `Last-Event-ID`. Consume **`stream.subagents`**. One run per `thread_id`.
- `$ per 1k` **[inferred]**: models **~$223** + traces **$0.50** (or **$5** extended) + Dedicated S infra **~$10** at GTM volume. Tracing is noise until you dual-bill or auto-upgrade. Dedicated S floor **~$384/mo [inferred]**.
- No published p50/p95/p99 — use **[inferred] policy** ms (Dedicated TTFT **800 / 3,200 / 6,400**; crash reclaim **60,000 / 120,000 / 180,000** from the 2 min sweeper). GTM **~10k req/week, 150 users, 26/74** is the only named capacity anecdote — not a QPS SLO.
- Fallback: **hosted Agent Server → self-host graph invoke → refuse**. Circuit breakers are **yours** (model / sandbox / checkpointer). Never fail open to LocalShell.
- Zero-Trust MCP is still a **gateway PEP** in prod (OAuth 2.1, RFC 8707 audience, no passthrough, hash-pin). `permissions=` is not that gateway. Agent Server MCP/A2A is **ingress**. PII is **detect → redact → audit**. Who deployed which graph: **revisions + audit `update_deployment`**, not traces.

---

## Interview Q&A

**Q1. What is “going to production” for Deep Agents, in one minute?**  
I do not ship a new runtime. `create_deep_agent` already returned a LangGraph `CompiledStateGraph`. Production is LangSmith Deployment’s Agent Server around that graph: API replicas persist runs and stream SSE; queue workers execute; Postgres is the checkpointer and store; Redis is wake/cancel/stream signaling with no payloads. MDA is an opinionated CLI on the same runtime. Custom auth or routes still mean a normal LangSmith Deployment.

**Q2. Walk a request from API replica to SSE.**  
Client sends `thread_id` + `context` to an API replica. API writes a pending run to Postgres and Redis wakes a worker — no payload in Redis. Worker takes a lease, at most one run per thread, runs super-steps, checkpoints at `"async"` by default, publishes events on PubSub. Any replica with `/stream` open forwards SSE. Default disconnect leaves the run running. I rejoin with the same `thread_id` and protocol v2 `since`. HITL `interrupt()` releases the worker slot.

**Q3. Name the four retry layers.**  
Chat-model HTTP `max_retries=6` (429/5xx/network, not 401/404). LangGraph node `RetryPolicy` `max_attempts=3` with jitter, skipping `ValueError` and friends. Agent Server **3** attempts for **transient Postgres errors**, not model 429s. Middleware `ModelRetryMiddleware` / `ToolRetryMiddleware` default `max_retries=2` (3 attempts) — **not** installed by Deep Agents unless I append them. I do not collapse these into “the platform retries three times.”

**Q4. Why 9,999 not 10000?**  
The SDK binds 9,999 because LangGraph `merge_configs` has historically dropped `recursion_limit` when it equals the 10000 sentinel, so frontend `recursionLimit: 10000` can be a no-op and children fall back toward 25. Hitting the ceiling is `GraphRecursionError`, a hard error. I still set a product hop cap and call-limit middleware. Cite the harness module for the dodge; I do not re-derive it.

**Q5. Give me `$ per 1k` including traces and Dedicated Small.**  
Inferred, not a SKU. Same 10-call Sonnet 4.6 research shape as the harness module ≈ **$223 / 1k** model. LangSmith traces **$0.50 / 1k** base (0.05¢) or **$5 / 1k** extended 400-day. Sum **~$224 / ~$228**. Dedicated Small published size × LCU/LSU rates over 720 h ≈ **$384/mo**; at GTM 10k runs/week ≈ 40k/mo that’s **~$10 / 1k** infra. All-in ~**$234 / 1k** at that volume. At low volume the $384 floor dominates. I do not quote $2.50/1k traces.

**Q6. What p50/p95/p99 do you put on Agent Server?**  
Nobody publishes hosted invoke percentiles. I contract Dedicated warm SSE/TTFT at **800 / 3,200 / 6,400 ms** inferred (harness TTFT plus an API hop). Admit path **20 / 80 / 250 ms**. Serverless wake **5,000 / 20,000 / 60,000 ms** inferred policy — unpublished, so chat goes on Dedicated. A 10-call run **20,000 / 80,000 / 200,000 ms**. Worker crash reclaim **60,000 / 120,000 / 180,000 ms** from the documented 2 min sweeper. HITL **30,000 / 180,000 / 600,000 ms**, expire-deny in my timer. I measure on my graph; I do not claim a vendor SLO.

**Q7. Disconnect, Last-Event-ID, and HITL — how do they differ?**  
Disconnect does not cancel unless I set `on_disconnect="cancel"`. `useStream.disconnect()` is leave, not kill. Protocol v2 is POST SSE; resume is `since` in the JSON body; Last-Event-ID is the old blog story. HITL is not cancel: the worker frees the slot and sleeps unbounded; resume is `Command(resume=...)`. Cancel interrupt keeps checkpoints; cancel rollback deletes them. I persist `thread_id` so remount rejoins.

**Q8. Durable execution vs Temporal.**  
I describe Agent Server as the Temporal-equivalent: `thread_id` is the workflow id, Postgres checkpoints are history, the durable queue plus Redis wake is the task queue, workers heartbeat and a 2 min sweeper re-leases on crash, PubSub is signals. Replay restarts the interrupted node from line 1, so tools must be idempotent or HITL-gated after the draft. I do not add Temporal “to make LangGraph durable.”

**Q9. Circuit breaker and fallback.**  
The product does not ship a breaker. I wrap model, sandbox, and checkpointer: closed → open → half-open with one probe. Fallback is **hosted Agent Server → self-host the same graph invoke → deterministic refuse**. I never fail open to LocalShell, never skip the MCP gateway, never auto-approve HITL on timeout. Prolonged Redis or Postgres outage means the server is unavailable — I refuse.

**Q10. Zero-Trust MCP in production — isn’t Agent Server enough?**  
No. Deploying gives me MCP/A2A **ingress** for free. `permissions=` still only covers built-in FS tools. Egress `tools/call` needs a gateway PEP: allowlists, hash-pinned tool JSON, OAuth 2.1, RFC 8707 audience = canonical server URI, no client-token passthrough. MDA is HTTP/SSE only, no stdio. Ingress without `@auth` is an open URL. Identity comes from verified `runtime.server_info.user.identity`, not from `context.user_id` the model invented.

**Q11. PII — detect → redact → audit.**  
Detection on every sink (model I/O, tools, VFS, webhooks, traces) with regex plus optional ML; fail closed to mask on chat and **block** PAN into MCP args, sandbox env, and webhooks if ML is down. Redaction via `PIIMiddleware` (not default), HIDE_INPUTS/OUTPUTS, anonymizer, tracing_context replicas. Audit WORM of decisions — pre/post hashes, entity types, action, detector, cid, tenant, `thread_id` — not raw PAN. 0.7.9 omitting middleware inputs is not DLP. Checkpoints still hold VFS bytes.

**Q12. Hosted vs self-host, and 74% ambient — what do you actually choose?**  
If I need custom `@auth`, Dedicated HA, and the org region matches: Cloud Deployment. MDA if the agent is the product and US+CLI is acceptable. Hybrid/self-host for residency/air-gap; I then own Postgres, Redis, and default-off auth. Interactive copilot on Dedicated with continue-on-disconnect and HITL slot release. Ambient 74% is cron/webhook `runs.create`, `enqueue`/`reject`, no SSE, delete crons on teardown. I cite ~10k req/week, 150 users, 26/74 as a traffic shape. I do not claim 500 rps or a hosted p99.

---

## Key Numbers to Memorize

### Package / pins / versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **`>=0.7.9`** | `excluded_tools` blocks **execution**; middleware tracing inputs off |
| **`>=0.5.0`** | `rt.server_info` / `rt.execution_info` namespace factories |
| **0.6** | Event streaming `version="v3"` |
| **1.3.14 / 1.3.16** | `ToolErrorMiddleware` / `is_retryable` skip |
| **2026-10-01** | Legacy Dev/Prod deployment types remain until this date |
| **2026-01-06** | Cloud static NAT IPs for egress allowlists (created after) |
| **2026-08-06** | GTM ~10k req/week blog (still cited 2026-09-02) |
| **2026-03-09** | GTM product-metrics blog (+250% conversion, 26/74 in later stack blog) |

### Recursion / streaming / workers
| Number | What |
| --- | --- |
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **10,000** | Frontend `recursionLimit` copy; historically dropped by `merge_configs` |
| **25 / 1000** | Bare LangGraph historical default / docs-mentioned 1.0.6 default — verify pin |
| **255** | Postgres `thread_id` max chars |
| **`since`** | Protocol v2 SSE resume cursor (POST body); **not** Last-Event-ID |
| **2 min / 120,000 ms** | Worker sweeper interval |
| **10** | `N_JOBS_PER_WORKER` default concurrent **runs** per worker container |
| **3** | Agent Server PG run attempts; node `RetryPolicy` `max_attempts` (includes first) |
| **6** | Chat-model HTTP `max_retries` |
| **2** | Middleware `max_retries` default (3 total attempts) if installed |
| **enqueue** | Default double-text strategy |
| **continue** | Default SSE disconnect (run keeps going) |

### Cloud SKUs / traffic
| Number | What |
| --- | --- |
| **3 / 6 / 1 / 4** | Dedicated Small runtime vCPU / GiB / DB vCPU / DB GiB |
| **25 MB** | Cloud request payload cap → 413 |
| **75% / 75% / 10 / 30 min** | Dedicated autoscale CPU / mem / pending runs per container / scale-down delay |
| **~10k / week, 150+ users, 26% / 74%** | LangChain GTM agent on Deep Agents + Deployments |
| **[inferred] 0.0165 QPS** | 10,000 / (7×86,400) average — not peak, not an SLO |
| **[inferred] 0.008 runs/s** | 20 min run × 10 jobs × 1 worker fully utilized |
| **localhost:2024** | Local `useStream` target |
| **US only** | MDA Cloud region (blog); deployments cannot migrate regions |

### $ / traces / LCU **[inferred]** where marked
| Number | What |
| --- | --- |
| **0.05¢ / $0.50 per 1k** | Base LangSmith trace |
| **0.50¢ / $5.00 per 1k** | Extended 400-day all-in (upgrade 0.45¢) |
| **14 d / 400 d** | Base / extended retention |
| **25,000** | Max runs per trace |
| **5k / 10k** | Developer / Plus included base traces per month |
| **50k / 250k / 500k** | Hourly events: Dev no card / Dev card / Plus |
| **5,000 / 2,000 / min** | ALB `POST/PATCH /runs*` / catch-all |
| **1 LCU = $1.50 / 1 LSU = $1.00** | Normalized compute units |
| **0.045 / 0.006 LCU** | Runtime vCPU-hr / GiB-hr |
| **0.177 / 0.025 LSU** | DB vCPU-hr / GiB-hr |
| **[inferred] ~$384 / mo** | Dedicated Small 720 h floor |
| **[inferred] ~$10 / 1k** | That floor amortized at 10k runs/week |
| **[inferred] $223 / 1k** | Model bill, [08] 10-call Sonnet 4.6, GP off |
| **[inferred] ~$224 / ~$228** | Model + base / extended traces |
| **[inferred] ~$234 / 1k** | Model + base traces + Dedicated S infra at GTM volume |
| **~5–30 LCU / 6 h** | Engine run estimate / schedule |
| **$150k + $150k** | Marketplace self-hosted license + usage commitment (packaging) |
| **$39/seat** | Plus seat (not in Dedicated floor) |

### Latency / security (numeric ms)
| Number | What |
| --- | --- |
| **20 / 80 / 250 ms** | **[inferred policy]** run-create persist + Redis wake p50/p95/p99 |
| **800 / 3,200 / 6,400 ms** | **[inferred policy]** Dedicated warm SSE / parent TTFT |
| **5,000 / 20,000 / 60,000 ms** | **[inferred policy]** Serverless scale-from-zero first SSE (unpublished) |
| **0 / 5,000 / 30,000 ms** | **[inferred policy]** queue wait when slots busy |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** one ReAct cycle on a worker |
| **20,000 / 80,000 / 200,000 ms** | **[inferred]** 10-call research run, GP off |
| **5 / 30 / 100 ms** | **[inferred policy]** `"async"` checkpointer extra per super-step |
| **10 / 50 / 200 ms** | **[inferred policy]** `"sync"` checkpointer extra per super-step |
| **0 / 0 / 0 ms** | **[inferred policy]** `"exit"` hot-path tax (no mid-run recovery) |
| **60,000 / 120,000 / 180,000 ms** | **[inferred from 2 min sweeper]** worker crash reclaim |
| **1,800,000 ms** | Dedicated autoscale scale-down delay (30 min) |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** HITL clock; p99 expire-deny (your timer) |
| **3,600 s** | Docs example sandbox `idle_ttl_seconds` |
| **detect → redact → audit** | PII on traces, checkpoints/VFS, model I/O, webhooks **before** persist |
| **RFC 8707 / RFC 8693** | MCP audience on authorize+token / no client-token passthrough (exchange) |
| **OCSF 6003** | Audit-log class for SIEM (`create/update/delete_deployment`) |
| **closed → open → half-open** | Application breakers on model / sandbox / checkpointer |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs.
