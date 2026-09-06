# Module 14: Deep Agents -- Production & Ecosystem

## What Is This?

Going to production with Deep Agents is a hosting and durability problem, not a second orchestrator. `create_deep_agent` returns a LangGraph `CompiledStateGraph` -- the same object you tested locally. Production ships **Agent Server** (API replicas + queue workers + Postgres checkpointer/store + Redis pub/sub) **around that graph**. API replicas accept HTTP, persist a pending run, and stream SSE. They do NOT execute the graph. Queue workers acquire a lease, run LangGraph super-steps, write checkpoints, and publish stream events. Redis is signaling only (wake-up sentinel, cancel/stream pub/sub, attempt counter) -- no user or run payloads flow through it. The ecosystem surfaces -- Code (`dcode` CLI), ACP (editor protocol), A2A (agent-to-agent), MCP ingress (agent-as-tool) -- are I/O adapters around the same compiled graph. None of them introduces a new runtime.

---

## 1. System Topology & Data Flow

### 1.1 Agent Server Architecture

```
                         TELEMETRY / OBSERVABILITY
         ┌──────────────────────────────────────────────────────────────┐
         │  Cloud: traces -> project named after deployment (automatic) │
         │  Local: LANGSMITH_TRACING + LANGSMITH_API_KEY               │
         │  Filter: metadata.lc_agent_name ; ls_integration=deepagents │
         │  PII: detect->redact->audit BEFORE traces/checkpoints       │
         └────────────▲─────────────────────▲──────────────────────────┘
                      |                     |
┌─────────────────────┴─────────────────────┴──────────────────────────┐
│ CONTROL PLANE (LLM-free: deploy + run-config + identity)             │
│  langgraph.json: dependencies, graphs (id->"./file.py:export"), env │
│  Revision: git SHA / archive; env snapshot for rollback              │
│  Workspace secrets; @auth module; deployment type/size (IMMUTABLE)   │
│  Workspace RBAC (Admin/Editor/Viewer -- Enterprise)                  │
│  Run submit: thread_id, assistant_id, context, durability,           │
│    on_disconnect, multitask_strategy, recursion_limit                │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────┐
│ DATA PLANE (untrusted model+tools on WORKERS, not on API replicas)   │
│  ┌──────────────┐   persist pending run (no graph exec in split mode)│
│  │ Agent Server  │───────────────────────────────────────┐           │
│  │ API replicas  │  /threads /runs /stream SSE /cancel   │           │
│  │ (stateless;   │  store CRUD; MCP+A2A INGRESS          │           │
│  │ no stickiness)│<── Redis PubSub (stream/cancel)───────┤           │
│  └──────┬────────┘                                       │           │
│         │ Redis wake-up sentinel (no run payload)        │           │
│         v                                                │           │
│  ┌──────────────────────────────────────────────────────┐│           │
│  │ QUEUE WORKERS  (N_JOBS_PER_WORKER default 10)        ││           │
│  │  claim lease -> load graph -> super-steps ->          ││           │
│  │    checkpoint at durability cadence -> publish events ││           │
│  │  AT MOST ONE RUN PER thread_id                       ││           │
│  │  heartbeat timestamp in Redis                        ││           │
│  │  HITL interrupt(): worker RELEASES slot              ││           │
│  └──────────────┬───────────────────────────────────────┘│           │
│                 │                                        │           │
│  ┌──────────────▼────────────────────────────────────────┐│          │
│  │ TOOL PROXIES (least privilege)                        ││          │
│  │  Sandbox (BaseSandbox / LangSmithSandbox / Daytona)   ││          │
│  │  AUTH PROXY injects keys -- never in sandbox env      ││          │
│  │  MCP EGRESS: gateway PEP still required               ││          │
│  │  MCP/A2A INGRESS: free with deploy; same @auth        ││          │
│  │  NEVER LocalShellBackend / host FilesystemBackend     ││          │
│  └───────────────────────────────────────────────────────┘│          │
└──────────┬────────────────┬────────────────┬──────────────┘          │
           v                v                v                         │
┌──────────────────────────────────────────────────────────────────────┤
│ PERSISTENCE LAYER                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │ Postgres (default)│  │ Redis            │  │ Optional Mongo     │  │
│  │ assistants,threads│  │ wake sentinel    │  │ checkpoints ONLY   │  │
│  │ runs, crons ALWAYS│  │ cancel pub/sub   │  │ Postgres STILL req │  │
│  │ checkpoints dflt  │  │ stream pub/sub   │  │ for threads/runs   │  │
│  │ store dflt        │  │ attempt counter  │  │                    │  │
│  │ thread_id < 255   │  │ NO user/run bytes│  │ InMemorySaver =    │  │
│  │ EncryptedSerializer│ │ prolonged outage │  │ prototype only     │  │
│  │ if AES_KEY present│  │ = Server down    │  │                    │  │
│  └──────────────────┘  └──────────────────┘  └────────────────────┘  │
│  Do NOT pass checkpointer=/store= in graph code on Agent Server      │
│  -- the server injects (and REPLACES) whatever the app configured    │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request Flow Narrative

1. **Admit.** Client hits an API replica: create thread (if needed) and `runs.create` / `runs.stream` with `thread_id` + `context` + assistant_id. Payload cap **25 MB** -> HTTP 413.
2. **Durable queue.** API persists a pending run in Postgres. Redis sentinel wakes a worker. Redis does NOT carry the run payload. Creating a run is a fast write; if all job slots are busy, runs queue (back-pressure, not HTTP 429).
3. **Lease.** A worker claims the lease (Postgres MVCC; exactly-once attempt semantics). At most one run per `thread_id` at a time. Worker loads the compiled graph (already in memory) or calls the async factory.
4. **Super-steps.** LangGraph runs. Checkpoints write at the deployment's durability cadence (`async` is Agent Server default). Successful nodes in a super-step are saved and NOT re-run on resume; the interrupted node restarts from the top of its function.
5. **Stream.** Any API replica with an open `/stream` subscribes via Redis PubSub and forwards SSE. Protocol v2: POST-only, resume via body field `since` (NOT `Last-Event-ID`). Bounded per-run buffer; earliest events may be evicted.
6. **Disconnect.** Default: run keeps going. `useStream.disconnect()` is client-only leave. Opt-in `on_disconnect="cancel"`. Rejoin: same `thread_id` + `since`.
7. **Stop.** HITL `interrupt()` is NOT cancel: worker releases slot, sleep unbounded, resume via `Command(resume=...)`. Cancel interrupt keeps checkpoints; cancel rollback deletes them.

### 1.3 Four Retry Layers

Deep Agents does NOT install retry middleware by default -- you append it. These four layers are commonly confused in interviews:

| Layer | Default | Retries What | Does NOT Retry |
|-------|---------|-------------|----------------|
| **1. Chat model HTTP** | `max_retries=6` | 429 / 5xx / network | 401 / 404 |
| **2. LangGraph node RetryPolicy** | `max_attempts=3` (includes first); jitter=True | Exceptions matching `retry_on` (excludes ValueError, TypeError, etc.) | Non-retryable types; non-retryable node -> attempt fails |
| **3. Agent Server run attempt** | **3** attempts for transient Postgres errors | PG blips during the run | Model 429s (this is NOT a model retry) |
| **4. Middleware** | `max_retries=2` (3 total) if installed | Model: retryable + unclassified; Tool: optional allowlist | Tools you did not allowlist |

**ModelFallbackMiddleware:** Provider outage path (e.g., fallback to gpt-5.5), not per-request 429. Separate from retry.

### 1.4 Hosting Paths

| Path | What | Auth | Best For |
|------|------|------|----------|
| **Managed Deep Agents (MDA)** | CLI-first hosted runtime (`mda deploy`) | LangSmith key or Supabase-class identity | Agent IS the product; US-cloud; CLI-first |
| **LangSmith Deployment (direct)** | `langgraph.json` + graph export; Agent Server hosts everything | Custom `@auth.authenticate` + workspace RBAC | Custom auth, Dedicated HA, webhooks, MCP/A2A ingress |
| **Self-hosted** | Open-source harness on your LangGraph/Agent Server image | You own everything | Data residency, air-gap, platform-standard k8s |

**MDA is US-cloud beta with waitlist/docs lag (as of 2026-09-02).** Deployments cannot migrate regions. Type (Serverless/Dedicated) is immutable after create.

### 1.5 Ecosystem Surfaces (I/O Adapters)

| Surface | Package / Endpoint | What It Is |
|---------|-------------------|------------|
| **SDK** | `deepagents` `create_deep_agent` | Construction + in-process invoke/stream |
| **Code** | `deepagents-code` / `dcode` | TUI, headless `-n`, or `--acp` with optional remote sandbox |
| **ACP** | `deepagents-acp` `AgentServerACP` | Editor <-> coding agent; JSON-RPC over stdio; protocol v1 |
| **A2A** | Agent Server `POST /a2a/{assistant_id}` | Agent <-> agent; JSON-RPC tasks between deployments |
| **MCP ingress** | Agent Server `POST /mcp` | Expose agent as a tool (stateless per request) |
| **OpenWiki** | `openwiki` npm CLI | Out-of-band authoring of wiki Markdown |

**Key invariant:** All surfaces consume the same `CompiledStateGraph` from `create_deep_agent`. None introduces a new runtime.

---

## 2. Core Mechanics & Algorithms

### 2.1 Production Invariants

**I1.** Deep Agents introduces NO production runtime. `create_deep_agent` returns a LangGraph `CompiledStateGraph`. Production = Agent Server around that graph.

**I2.** API replicas ROUTE. Queue workers EXECUTE. Coupling them produces "the HTTP timeout is my agent SLO" -- false for a 20-minute research run.

**I3.** One run per `thread_id` is the concurrency boundary. No distributed lock API in Deep Agents.

**I4.** `thread_id` and `context` are independent. Missing `thread_id` means every `useStream` mount is a new conversation; disconnect cannot rejoin; HITL resume targets the wrong cursor.

**I5.** Do NOT pass `checkpointer=` / `store=` in graph code on Agent Server -- the server injects and replaces whatever the app configured.

**I6.** `permissions=` covers built-in FS tools only. Production still needs the MCP gateway.

### 2.2 Streaming: `on_disconnect`, Protocol v2, Double-Texting

| Event | Worker | Client |
|-------|--------|--------|
| TCP/SSE drop, default | Continues to completion | Misses live tokens until rejoin |
| `on_disconnect="cancel"` | Cancel requested | Avoids zombie spend |
| `stream.disconnect()` | Continues | Intentional background |
| Rejoin | Unchanged | `thread_id` + seq `since`; SDK auto |

**Protocol v2 dualism (interview trap):** The 2026 runtime blog still describes resume via `Last-Event-ID` header. Protocol v2 docs are explicit: POST-only SSE, NO `Last-Event-ID`, client sends `since` in the JSON body. Browser `EventSource` does NOT apply. Verify with a disconnect test; do not cite Last-Event-ID as the v2 contract.

**Double-texting (new input while a run is running):**

| Strategy | Effect |
|----------|--------|
| **enqueue** (default) | Queue the new run; no state corruption |
| **reject** | 409 refuse until current run ends |
| **interrupt** | Halt, keep checkpoints, start from that state |
| **rollback** | Halt and DELETE the in-flight run's checkpoints |

### 2.3 Worker Sweeper and Durability

Workers write heartbeat timestamps to Redis. Sweeper interval: **2 minutes**. On hard crash, sweeper re-enqueues; another instance resumes from last checkpoint. Instances are stateless; no session stickiness.

| Durability Mode | When It Writes | Failure Implication |
|----------------|---------------|---------------------|
| **async** (Agent Server default) | After each step, async | Small crash window: last step may be lost |
| **sync** | Before next step | Highest durability, extra latency |
| **exit** | Only on graph exit | Fast; NO mid-run crash recovery |

### 2.4 A2A Protocol

A2A is Google's (now Linux Foundation) protocol. Every LangSmith Deployment auto-exposes MCP + A2A. Agent Server implements JSON-RPC only (NOT gRPC or HTTP+JSON).

**Identity mapping (critical):**

| A2A | LangGraph |
|-----|-----------|
| `contextId` | **`thread_id`** -- MUST be a UUID; `session-42` returns -32602 |
| `taskId` | One run inside the thread; new user turn = new task |
| Client `metadata.thread_id` | **Ignored** |

**History replay trap:** Default `historyScope=context` replays the whole context (including tool results) on `SendMessage`/`GetTask`/`ListTasks`. Set `historyScope=task` to scope. `historyLength` max **10** (-32602 if larger). Streaming silently ignores both options. Mis-cased `historyscope` is silently ignored.

### 2.5 ACP Protocol (Agent Client Protocol)

ACP standardizes communication between coding agents and code editors. Protocol version **1** (integer in `initialize`). v2 is a consolidation draft.

| Aspect | Detail |
|--------|--------|
| Transport | Stdio (JSON-RPC); remote HTTP/WebSocket is WIP |
| Clients | Zed, JetBrains, VS Code, Neovim |
| Demo checkpointer | MemorySaver (dies with subprocess -- NOT production) |
| Known bugs | #4254 (missing selectors), #5084 (process-wide cancel flag) |
| Modes | `ask_before_edits`, `accept_edits`, `accept_everything` |

**ACP is NOT a PEP.** The editor is the TCB (trusted computing base), not a policy enforcement point.

### 2.6 Claude Agent SDK vs Deep Agents Code

| Axis | Deep Agents Code | Claude Agent SDK |
|------|-----------------|-----------------|
| **Where the loop runs** | Outside sandbox (laptop/container); sandbox is a tool | **Inside** sandbox only |
| **Execution backend** | Pluggable: local, VFS, remote sandbox | Local filesystem of that sandbox |
| **Model support** | Any LangChain tool-calling provider (100+) | Claude only (Anthropic, Bedrock, Vertex) |
| **Deployment** | MDA / LangSmith Deployment / self-host | Self-host the HTTP/auth/streaming layer |
| **Multi-tenancy** | Scoped threads, per-user sandboxes, RBAC | Build it yourself (cwd + CLAUDE_CONFIG_DIR) |
| **Credentials** | Auth proxy injects outside guest | Typically in the guest |

**Comparison drafted 2026-04-16.** Anthropic Managed Agents is a SEPARATE product from the SDK -- SDK code does not deploy onto it. LangSmith MDA is also a separate deployment SKU.

### 2.7 RAG on Deep Agents

Deep Agents does NOT ship an index. RAG is: your `@tool` searches -> `upload_files` writes paths to `/retrieved/{batch_id}/chunk_i.md` -> parent sees paths -> up to 3 `chunk-analyst` `task()` calls -> parent synthesizes.

Tutorial numbers: 14 pages, 589,579 chars, 782 chunks, k=4, 20s/page fetch, <300 word summaries. Skipping `upload_files` reintroduces ~150k tokens into the parent context.

---

## 3. Token Economics & NFR Analysis

### 3.1 Trace SKUs (Observability Bill)

| Metric | Price |
|--------|-------|
| Base LangSmith trace (14-day retention) | **$0.50 / 1k traces** (0.05c per trace) |
| Extended trace (400-day retention) | **$5.00 / 1k traces** (0.50c per trace) |
| Developer free tier | 5,000 traces/month |
| Plus tier | 10,000/month then pay-as-you-go |

**Cost trap:** Online evaluators and automation rules default to extended retention. Auto-upgrade can turn $0.50/1k into $5.00/1k.

### 3.2 Agent Server Compute SKUs

Normalized units: 1 LCU = $1.50, 1 LSU = $1.00.

**Dedicated Small (3 vCPU, 6 GiB, DB 1 vCPU / 4 GiB) [inferred monthly floor]:**

| Line | USD / 720h |
|------|-----------|
| Runtime CPU | $145.80 |
| Runtime memory | $38.88 |
| DB CPU | $127.44 |
| DB memory | $72.00 |
| **Infra subtotal** | **~$384/mo** |

Plus seats ($39/seat Plus), traces, sandboxes, Engine, model APIs -- NOT in that floor.

### 3.3 All-In Cost Per 1k Runs [inferred]

| Component | Per 1k | Notes |
|-----------|--------|-------|
| Model (Sonnet 4.6, 10-call cached) | **$223** | 2k prefix + 30k uncached + 8k out |
| LangSmith traces (base 14d) | **$0.50** | 0.05c per trace |
| LangSmith traces (extended 400d) | **$5.00** | Auto-upgrade trap |
| Dedicated Small infra (at 10k runs/week) | **~$10** | $384/mo / 40k runs/mo |
| **All-in (base traces, GTM volume)** | **~$234** | |
| **All-in (extended traces)** | **~$238** | |

At low volume the $384/mo infra floor dominates traces. Tracing is noise next to model spend at this shape.

**Ecosystem adapter overhead:**

| Session Type | Per 1k | Delta vs Chat |
|-------------|--------|---------------|
| Chat harness (baseline) | $223 | -- |
| +MCP schemas on prefix (~+3k tokens) | $242 | +$19 |
| +Editor @file buffers (+2k uncached/turn) | $302 | +$79 |
| A2A 3-round (6 full runs, cached) | $1,337/1k conversations | 6x baseline |
| `/cost` docs example (1 Sonnet 4.5 thread) | $1.03/thread | Published example, not a SKU |

### 3.4 Latency SLA Targets

| Path | p50 | p95 | p99 | Notes |
|------|-----|-----|-----|-------|
| **Run-create API persist + Redis wake** | **20 ms** | **80 ms** | **250 ms** | [inferred] Fast write |
| **Dedicated warm SSE / TTFT** | **800 ms** | **3,200 ms** | **6,400 ms** | [inferred] Always-on |
| **Serverless scale-from-zero** | **5,000 ms** | **20,000 ms** | **60,000 ms** | [inferred] Unpublished; chat goes on Dedicated |
| **Queue wait (slots busy)** | **0 ms** | **5,000 ms** | **30,000 ms** | [inferred] Back-pressure |
| **One ReAct cycle on worker** | **2,000 ms** | **8,000 ms** | **20,000 ms** | [inferred] |
| **10-call research run** | **20,000 ms** | **80,000 ms** | **200,000 ms** | [inferred] Do not put on chat HTTP timeout |
| **Checkpointer async extra/step** | **5 ms** | **30 ms** | **100 ms** | [inferred] Small crash window |
| **Checkpointer sync extra/step** | **10 ms** | **50 ms** | **200 ms** | [inferred] Highest durability |
| **Worker crash reclaim (sweeper)** | **60,000 ms** | **120,000 ms** | **180,000 ms** | From documented 2-min sweeper |
| **HITL clock** | **30,000 ms** | **180,000 ms** | **600,000 ms** | [inferred] Worker frees slot |
| **ACP stdio extra hop** | **80 ms** | **320 ms** | **1,280 ms** | [inferred] Local JSON-RPC |
| **A2A one round (2 deploys)** | **40,000 ms** | **160,000 ms** | **400,000 ms** | [inferred] 2x 10-call shape |

All figures are [inferred policy targets], not vendor SLOs. GTM ~10k req/week, 150+ users, 26/74 user/ambient is the ONLY named capacity anecdote.

### 3.5 Availability & Recovery

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Availability** | Prolonged Redis OR Postgres outage = Agent Server unavailable | Both planes required |
| **RPO (sync)** | Last super-step before next | Highest durability |
| **RPO (async)** | Small window -- last step may be lost | Agent Server default |
| **RPO (exit)** | Empty if crash mid-run | No mid-run recovery |
| **RTO (happy path)** | Resume `thread_id` in seconds | Any replica |
| **RTO (crash)** | ~2 minutes (sweeper interval) | Heartbeat window |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution (Agent Server = Temporal-Equivalent)

Deep Agents does NOT wrap Temporal. The Temporal-shaped story:

| Temporal Concept | Agent Server Equivalent |
|-----------------|------------------------|
| Workflow ID | `thread_id` (< 255 chars) |
| Event history | Postgres checkpoints (async/sync/exit) |
| Activity worker | Queue worker; N_JOBS_PER_WORKER=10 |
| Task queue | Postgres pending runs + Redis sentinel |
| Sticky execution | None -- stateless replicas |
| Signal / query | Redis cancel/stream pub/sub; Command(resume=...) |
| Retry policy | Four layers (not one Temporal retry) |
| Continue-as-new | Thread TTL / new thread (stateless cron) |

**You do not buy a second workflow engine to "make Deep Agents production."**

### 4.2 Failure Taxonomy

| Class | Examples | Handling |
|-------|----------|---------|
| **Transient** | Model 429/5xx, network, PG blip, NodeTimeoutError | Layer 1 HTTP (6); Layer 2 node (3); Layer 3 PG (3); Layer 4 middleware if installed |
| **Permanent** | ValueError, 401/404, GraphRecursionError, @auth 403 | Fail closed. Do not bump recursion_limit to 10000 |
| **Poison-pill threads** | Non-retryable crash loop (sweeper burns 3 PG attempts); HITL never resumed; recursion ceiling; rollback deleting checkpoints | Shrink state; cancel/resume from any replica; product cap |
| **Idempotency** | Resume restarts node from line 1; duplicate Slack/CRM write | Upserts; idempotency keys; HITL after draft |
| **Zombie spend** | SSE drop while worker continues; cron never deleted; Serverless idle-before-scale-down | `on_disconnect="cancel"` for chats; cron lifecycle in destroy |
| **Denial of wallet** | 9,999-step loop; no call-limit middleware; online eval auto-upgrade; dual-instrument traces | Caps; opt out retention; emit once |

### 4.3 Circuit Breaker (Yours, Not the Product)

Independent breakers for: **model**, **sandbox**, **checkpointer**, **MCP gateway**.

**Fallback chain (required interview answer):** Hosted Agent Server -> self-host the same compiled graph (invoke with your Postgres checkpointer) -> deterministic refuse.

**Never:** circuit open -> LocalShellBackend. **Never:** HITL timeout -> auto-approve. **Never:** model 429 -> unsandboxed execute. **Never:** hosted outage -> skip @auth.

### 4.4 Zero-Trust MCP in Production

`permissions=` covers built-in FS tools only. Production egress MCP requires:

| Control | What |
|---------|------|
| **Transport** | OAuth 2.1 + PKCE; RFC 8707 audience = canonical MCP server URI |
| **Token** | No passthrough (RFC 8693 exchange); servers accept only their own audience |
| **Hash-pin** | Canonical JSON of name + description + schemas; re-verify every tools/call; CVE-2025-54136 CVSS 8.8 |
| **Identity** | From verified `runtime.server_info.user.identity`, NOT from model JSON |

Agent Server exposing your agent as MCP/A2A is INGRESS. Still need gateway on EGRESS. Ingress without @auth = anyone with the URL invokes the graph.

### 4.5 Authentication Architecture

| Layer | Purpose | Mechanism |
|-------|---------|-----------|
| End-user auth | Establish identity | Custom `@auth.authenticate` on Cloud; default-off on self-host |
| Agent-acting-as-user | Per-user credentials for external APIs | Agent Auth OAuth; auto-refresh |
| Operator RBAC | Control deployment/monitoring | Workspace Admin/Editor/Viewer (Enterprise) |

**Secret management:** Auth proxy intercepts outbound requests and injects credentials. API keys NEVER appear in sandbox code, environment variables, or logs. Sandbox `secrets=` is forbidden -- agent can read them.

### 4.6 PII Pipeline

**Three steps, all required:**

1. **Detect:** Regex (email, PAN, SSN, phones) + optional ML NER. Scan: user input, model output, tool args/results, VFS writes, memory candidates, traces, webhooks, HITL UI, ACP logs, A2A payloads. If ML down: fail closed to mask on chat; block on external sinks.

2. **Redact:** `redact`/`mask`/`hash` to stable tokens (`[EMAIL_<hash12>]`); `block` where field must not exist (secrets, MCP args, sandbox env). Strip from VFS AND message channel.

3. **Audit (WORM):** Log decisions, not values: content_sha256 pre/post, entity types + counts, action, detector, correlation_id, tenant, thread_id, tool arg digest. A tool call without an audit row is a control-plane bug.

**`PIIMiddleware` is NOT in the default Deep Agents stack.** You append it. `>=0.7.9` omits middleware trace inputs (volume/PII shrink, NOT DLP).

---

## 5. Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| LocalShellBackend in prod | Agent Server user = host user; execute = subprocess.run(shell=True) | Incident/review | StateBackend/StoreBackend/BaseSandbox only |
| No checkpointer / thread_id | No resume; HITL broken; useStream mounts new conversation each time | Lost threads | Agent Server injects Postgres; self-host: PostgresSaver |
| Tracing PII | Prompts, tool args in LangSmith 14-400 days | DLP on traces | HIDE_INPUTS/OUTPUTS; PIIMiddleware; detect->redact->audit |
| recursion_limit 25 on children | Nested task hits bare default (#1698) | GraphRecursionError at 25 | Pin deepagents; parent bind 9,999; verify propagation |
| Stream without thread_id | Every mount = new thread; can't rejoin; bounded buffer eviction | "UI skipped step" | Persist threadId; onThreadId callback |
| Dual-instrument OTel + LangSmith | Duplicate trees, doubled token counts | Two roots; doubled $ | Emit once; Collector fan-out |
| excluded_tools on <0.7.9 | Hidden but still executable | Schema gone; tool still runs | Pin >=0.7.9 |
| durability="exit" + worker kill | No mid-run checkpoint | Lost run after crash | Keep async for long runs |
| StateBackend large files | Checkpointed every step | PG bloat; OOM crash loop + sweeper burn | Store/sandbox; prune TTL |
| MCP without gateway | permissions= gives false confidence | Writes via MCP not in PDP | Gateway PEP (RFC 8707, hash-pin) |
| Cron never deleted | Runs and bills forever | LCU / surprise $ | Lifecycle in deploy/destroy |
| Protocol Last-Event-ID on v2 | Blog vs spec dualism | Failed resume | POST body `since`; verify disconnect test |
| A2A without @auth | Open /a2a/{uuid}; public card | Anonymous runs | @auth on both deploys; disable_a2a for air-gap |
| ACP MemorySaver in prod | Demo saver; dies with subprocess | Reload wipes state | Postgres or dcode .state/ |
| ACP cancel bleed (#5084) | Process-wide cancel flag | Cancelling session A cancels B | One server per editor window until fix |
| Bad A2A contextId | `session-42` instead of UUID | -32602 error | Server-minted UUID; echo forever |
| Dual harness | Claude SDK query() AND Deep Agents execute on same repo | Duplicate diffs; two /cost | One inner harness; other as MCP/A2A peer |
| Webhook SSRF | Outbound POST to unallowlisted HTTP | Leaked run payload | HTTPS + domain allowlist |

---

## 6. Architectural System Design Scenarios

### Scenario A -- LangSmith-Hosted vs Self-Host Agent Server

**Problem.** A regulated SaaS wants Deep Agents in production for an internal research copilot plus an external assistant. Requirements: EU data residency (or documented US exception), custom JWT @auth, no host shell, per-user memory, audit trail of who deployed which graph. Traffic may resemble GTM (~10k req/week, 74% ambient).

**Architecture (recommended: A1 Cloud Dedicated Deployment when region acceptable):**

```
  ┌─────────┐   ┌──────────────────────────────────────────────────────┐
  │ IdP/PEP │-->│ CONTROL: langgraph.json + @auth.authenticate         │
  │ JWT ->  │   │   @auth.on.threads/store owner filters               │
  │ identity│   │   workspace secrets + sandbox AUTH PROXY              │
  │         │   │   revisions (SHA + env snapshot) + audit logs SIEM    │
  │         │   │   pin deepagents>=0.7.9  recursion bind 9,999        │
  └─────────┘   └──────────────────────────┬───────────────────────────┘
                                           v
  ┌────────────────────────────────────────────────────────────────────┐
  │ DATA: Cloud Dedicated M (always-on; autoscale 75%/10-pending/30m) │
  │   API replicas (no graph exec); queue workers N_JOBS=10            │
  │   Postgres checkpointer+store; Redis signaling only               │
  │   ns=(assistant_id, user.identity)                                │
  │   MCP EGRESS via gateway PEP; MCP/A2A INGRESS behind @auth       │
  │   PII detect->redact->audit; PIIMiddleware appended               │
  │   NEVER LocalShell; excluded_tools={execute} unless sandbox       │
  └────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | A1: Cloud Dedicated (recommended if region OK) | A2: MDA (CLI-first beta) | A3: Self-host split API+queue |
|------|----------------------------------------------|--------------------------|-------------------------------|
| **Cost** | ~$384/mo Dedicated S + $223/1k models + $0.50/1k traces | Same Agent Server; waitlist/docs lag | Your k8s + PG HA; Marketplace $150k+$150k |
| **Ops** | langgraph deploy / GitHub revisions | Lowest (mda deploy) | Highest: queue, Redis, backups, auth default-off |
| **Security** | Custom auth + proxy; audit logs; MCP ingress needs @auth | Limited identity | You own network; default NO auth |
| **Latency** | Warm SSE 800/3,200/6,400ms [inferred] | Unpublished; US only | You tune N_JOBS; same 2-min crash sweeper |

### Scenario B -- Multi-Agent Fleet: Subagents vs A2A

**Problem.** An enterprise must compose research, billing, and HR specialists. Research subtasks are same trust domain (parallel chunk analysis). Billing lives in another compliance zone (PCI). HR must NOT be a peer. Some partners speak CrewAI/ADK A2A.

**Architecture (recommended: hybrid -- subagents inside + A2A across zones):**

```
  ┌───────────────────────────────────────────────────────────────────┐
  │ Orchestrator Deep Agent  (one trust domain)                       │
  │   subagents/task for private decomposition (chunk-analysts, k=4) │
  │   A2A ONLY to billing assistant in zone B                         │
  │   contextId UUID = thread_id; @auth.on.threads owner filter      │
  │   historyScope=task; A2A_ALLOWED_TOOL_CALL_RESULTS=ui_tool       │
  │   disable_a2a on HR deploy                                        │
  └──────────────────────────┬────────────────────────────────────────┘
                             |
       ┌─────────────────────┼─────────────────────┐
       v                     v                     v
  ┌──────────┐        ┌──────────┐          ┌──────────┐
  │ task()   │        │ A2A JSON-│          │ HR LSD   │
  │ chunk-   │        │ RPC zone │          │ disable_ │
  │ analysts │        │ B billing│          │ a2a      │
  │ in-proc  │        │ PCI      │          │ parent-  │
  └──────────┘        └──────────┘          │ only     │
                                             └──────────┘
```

**Key decisions:**
- **Subagents** for private decomposition inside one trust domain (no extra HTTP, same process)
- **A2A** only for the billing agent in another compliance zone (network boundary, separate @auth)
- `historyScope=task` prevents cross-task history replay (disclosure control)
- `disable_a2a` on HR deployment to prevent it from being a peer
- Fallback if billing A2A is down: parent-only (degraded quote, no card charge), NOT LocalShell

---

## Interview Q&A

**Q1. What is "going to production" for Deep Agents?**
I do not ship a new runtime. `create_deep_agent` already returned a LangGraph CompiledStateGraph. Production is Agent Server around that graph: API replicas persist runs and stream SSE; queue workers execute; Postgres is the checkpointer and store; Redis is signaling only with no payloads. MDA is an opinionated CLI on the same runtime. Custom auth or routes mean a direct LangSmith Deployment.

**Q2. Walk a request from API replica to SSE.**
Client sends thread_id + context to an API replica. API writes a pending run to Postgres and Redis wakes a worker -- no payload in Redis. Worker takes a lease (at most one run per thread), runs super-steps, checkpoints at async cadence by default, publishes events on PubSub. Any replica with /stream open forwards SSE. Default disconnect leaves the run running. I rejoin with the same thread_id and protocol v2 `since`. HITL interrupt releases the worker slot.

**Q3. Name the four retry layers.**
Chat-model HTTP max_retries=6 (429/5xx/network, not 401/404). LangGraph node RetryPolicy max_attempts=3 with jitter, skipping ValueError and friends. Agent Server 3 attempts for transient Postgres errors, not model 429s. Middleware ModelRetryMiddleware/ToolRetryMiddleware max_retries=2 (3 total) -- not installed by default. I do not collapse these into "the platform retries three times."

**Q4. Why 9,999 not 10,000?**
LangGraph merge_configs historically dropped recursion_limit when it equaled the 10,000 sentinel, so frontend recursionLimit: 10000 can be a no-op and children fall back toward 25 (bug #1698). Hitting the ceiling is GraphRecursionError -- a hard error. I still set product call caps and ToolCallLimitMiddleware.

**Q5. Give me the all-in cost per 1k including traces and Dedicated Small.**
Models: $223/1k (10-call Sonnet 4.6 cached). LangSmith traces: $0.50/1k base or $5/1k extended. Dedicated Small: ~$384/mo; at 10k runs/week (~40k/mo) that is ~$10/1k infra. All-in: ~$234/1k (base traces) or ~$238/1k (extended). At low volume the $384 floor dominates. I never quote $2.50/1k traces.

**Q6. What p50/p95/p99 do you put on Agent Server?**
Nobody publishes hosted invoke percentiles. I contract Dedicated warm SSE/TTFT at 800/3,200/6,400ms (inferred). Admit path 20/80/250ms. Serverless wake 5,000/20,000/60,000ms -- unpublished, so chat goes on Dedicated. A 10-call run 20,000/80,000/200,000ms. Worker crash reclaim 60,000/120,000/180,000ms from the documented 2-min sweeper. HITL 30,000/180,000/600,000ms, expire-deny. I measure on my graph; I do not claim a vendor SLO.

**Q7. Disconnect, Last-Event-ID, and HITL -- how do they differ?**
Disconnect does not cancel unless I set on_disconnect="cancel". Protocol v2 is POST SSE; resume is `since` in the JSON body; Last-Event-ID is the old blog story. HITL is not cancel: the worker frees the slot and sleeps unbounded; resume is Command(resume=...). Cancel interrupt keeps checkpoints; cancel rollback deletes them.

**Q8. Durable execution vs Temporal -- what do you tell the interviewer?**
Agent Server IS the Temporal equivalent: thread_id is workflow id, Postgres checkpoints are history, durable queue plus Redis wake is the task queue, workers heartbeat and a 2-min sweeper re-leases on crash, PubSub is signals. Replay restarts the interrupted node from line 1, so tools must be idempotent or HITL-gated. I do not add Temporal to make LangGraph durable.

**Q9. Circuit breaker and fallback.**
The product does not ship a breaker. I wrap model, sandbox, and checkpointer: closed -> open -> half-open with one probe. Fallback is hosted Agent Server -> self-host the same graph invoke -> deterministic refuse. I never fail open to LocalShell, never skip the MCP gateway, never auto-approve HITL on timeout.

**Q10. Zero-Trust MCP in production -- isn't Agent Server enough?**
No. Deploying gives me MCP/A2A ingress for free. permissions= still only covers built-in FS tools. Egress tools/call needs a gateway PEP: allowlists, hash-pinned tool JSON, OAuth 2.1, RFC 8707 audience, no client-token passthrough. MDA is HTTP/SSE only, no stdio. Ingress without @auth is an open URL.

**Q11. When do I pick A2A vs task subagents?**
Subagents for private decomposition in one process -- RAG chunk-analysts, coding subtasks. A2A when the peer is another team, another framework, or another compliance zone. Hybrid is the enterprise default: subagents inside, A2A only across the zone boundary, historyScope=task, tool-result allowlist, disable_a2a on sensitive deployments. A2A contextId must be a UUID; it IS thread_id.

**Q12. ACP vs A2A vs MCP -- which is which?**
ACP is agent-to-editor: JSON-RPC over stdio, protocol v1, for Zed/JetBrains/VS Code. A2A is agent-to-agent: JSON-RPC tasks between deployments, contextId=thread_id UUID. MCP is agent-to-tool: egress for calling external tools (needs gateway PEP), ingress for exposing agent as a tool (stateless). They stack: Zed ACP session -> Deep Agent -> MCP tools; a fleet agent calls the same graph over A2A. MCP ingress is stateless; A2A is threaded. Do not use MCP when you need contextId continuity.

---

## Key Numbers to Memorize

### Package / Pins
| Number | What |
|--------|------|
| **0.7.12** | SDK research pin (PyPI 2026-09-01) |
| **>=0.7.9** | excluded_tools blocks execution; middleware trace inputs off |
| **9,999 / 10,000** | Bound recursion_limit / sentinel (dropped by merge_configs) |
| **255** | Postgres thread_id max chars |
| **2 min / 120,000 ms** | Worker sweeper interval |
| **10** | N_JOBS_PER_WORKER default concurrent runs per worker |

### Retry Layers
| Number | What |
|--------|------|
| **6** | Chat-model HTTP max_retries |
| **3** | Node RetryPolicy max_attempts (includes first) |
| **3** | Agent Server PG run attempts |
| **2** | Middleware max_retries default (3 total) if installed |

### Cloud SKUs
| Number | What |
|--------|------|
| **3/6/1/4** | Dedicated Small vCPU/GiB/DB vCPU/DB GiB |
| **25 MB** | Cloud request payload cap -> 413 |
| **75%/75%/10/30 min** | Autoscale CPU/mem/pending/scale-down delay |
| **~10k/week, 150+ users, 26/74** | GTM traffic shape (not a QPS SLO) |
| **[inferred] ~$384/mo** | Dedicated Small 720h floor |

### Cost [inferred]
| Number | What |
|--------|------|
| **$0.50 / $5.00 per 1k** | Base / extended trace pricing |
| **$223 / 1k** | Model bill (10-call Sonnet 4.6 cached) |
| **~$234 / 1k** | All-in at GTM volume (base traces + Dedicated S) |
| **$242 / 1k** | +MCP schemas on prefix |
| **$1.03** | /cost docs example (one Sonnet 4.5 thread) |
| **$1.34 / $1.62** | A2A 3-round conversation cached/uncached |

### Latency [inferred policy]
| Number | What |
|--------|------|
| **20 / 80 / 250 ms** | Run-create persist + Redis wake |
| **800 / 3,200 / 6,400 ms** | Dedicated warm SSE / TTFT |
| **5,000 / 20,000 / 60,000 ms** | Serverless scale-from-zero |
| **60,000 / 120,000 / 180,000 ms** | Worker crash reclaim (sweeper) |

### Protocols
| Number | What |
|--------|------|
| **ACP protocol 1** | Wire protocolVersion; v2 is draft |
| **A2A v1.0 + v0.3** | JSON-RPC only (NOT gRPC/HTTP+JSON on Agent Server) |
| **contextId = thread_id** | UUID required; session-42 -> -32602 |
| **historyLength max 10** | -32602 if larger; streaming ignores |
| **`since`** | Protocol v2 SSE resume (POST body, NOT Last-Event-ID) |
| **enqueue** | Default double-text strategy |
| **continue** | Default SSE disconnect behavior |

---

## Quick Reference

```
PRODUCTION = AGENT SERVER AROUND THE SAME COMPILED GRAPH
  API replicas ROUTE (no graph exec)
  Queue workers EXECUTE (N_JOBS=10 per worker)
  Postgres = checkpoints + threads + runs + store
  Redis = signaling only (wake, cancel, stream) -- NO payloads
  One run per thread_id at a time

FOUR RETRY LAYERS
  1. Chat HTTP: max_retries=6 (429/5xx)
  2. Node RetryPolicy: max_attempts=3 (jitter, skip ValueError)
  3. Agent Server PG: 3 attempts (transient PG only)
  4. Middleware: max_retries=2 if installed (not default)

FALLBACK CHAIN
  Hosted Agent Server -> self-host graph invoke -> deterministic refuse
  NEVER: LocalShell / skip @auth / auto-approve HITL / skip gateway

ECOSYSTEM SURFACES (all use same CompiledStateGraph)
  SDK    = construction + in-process invoke
  Code   = dcode TUI/CLI/ACP; sandbox-as-tool
  ACP    = editor protocol; stdio JSON-RPC; protocol v1
  A2A    = agent-to-agent; contextId = thread_id UUID
  MCP    = egress (tools, needs gateway) / ingress (agent-as-tool)

PROTOCOL v2 STREAMING
  POST-only SSE; resume via body field "since" (NOT Last-Event-ID)
  Disconnect != cancel (default: run keeps going)
  HITL != cancel (worker releases slot, sleeps unbounded)
  Cancel rollback DELETES checkpoints

DURABILITY
  async (default): small crash window
  sync: highest durability, extra latency
  exit: no mid-run recovery -- forbidden for long runs
  Sweeper: 2 minutes; re-enqueue after heartbeat timeout
```
