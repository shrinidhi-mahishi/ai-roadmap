# Module 03 — Tool Use

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/03-tool-use.md` (researched 2026-08-21, 80 sources).
**Mandatory topics**: APIs · Function calling · Browser · Code execution.

The unit of production is not “the model called a function.” It is a **control plane** that compiles schemas, packs `tool_choice`, budgets the loop, and checkpoints around a **data plane** that mutates the world: REST/gRPC/GraphQL adapters, MCP servers, Playwright/CDP sessions, and Firecracker/gVisor/WASM sandboxes. Across OpenAI, Anthropic, Gemini, and Bedrock the invariant is the same: **the model does not execute client tools**. It emits a structured call; your dispatcher (or a hosted server tool) executes; a result is injected under a correlating ID. Interview answers that skip this split fail when the follow-up is “who holds IAM, and why did the parallel batch mix a server tool with a client tool?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, schema compilation, `tool_choice` / `FunctionCallingConfig`, parallel-call packing, stop-reason parsing, turn/loop budget, timeout envelopes, and (for hosted tools) the provider’s internal dispatcher. Data plane owns everything that actually mutates the world. Persistence is two stores: **durable intent + results** (Temporal history, Kafka outbox, object-store artifacts) versus **soft caches** (prompt-cache of the tools JSON, sandbox snapshots, browser cookies that die with the `BrowserContext`). Tool proxies never share IAM with the planner. Telemetry is the only place `tool_exec_ms`, token usage, and policy decisions are authoritative.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE / sync HTTP / Batch / inbound webhooks)                           │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant + Idempotency-Key (writes)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Schema compiler│─▶│ Orchestrator        │  │
│  │ auth, quota│  │ tool RBAC    │  │ OpenAPI/proto/ │  │ Temporal workflow / │  │
│  │ RPM/TPM    │  │ PII detect   │  │ GraphQL → JSON │  │ LangGraph ToolNode  │  │
│  │ breaker    │  │ SSRF URL     │  │ Schema / strict│  │ max_turns, wall clk │  │
│  │ Retry-After│  │ HITL gate    │  │ pin tool order │  │ stop_reason parse   │  │
│  └────────────┘  └──────┬───────┘  └───────┬────────┘  └──────────┬──────────┘  │
│                         │                  │                      │             │
│                         │                  ▼                      │             │
│                         │           ┌──────────────────────────┐  │             │
│                         │           │ Function-call packer     │  │             │
│                         │           │ tool_choice · parallel   │  │             │
│                         │           │ mixed-batch guard        │◀─┘             │
│                         │           │ (no built-in+custom mix  │                │
│                         │           │  on OpenAI; Anthropic    │                │
│                         │           │  mixed → finish client)  │                │
│                         │           └────────────┬─────────────┘                │
└─────────────────────────┼────────────────────────┼──────────────────────────────┘
                          │                        │
                          │ hosted complete()      │ tool_calls[] / tool_use
                          ▼                        ▼
┌─────────────────────────┼───────────────────────────────────────────────────────┐
│ DATA PLANE              │  LLM tokenizer→prefill→decode→parser (hosted or self) │
│                         │  Hosted/server tools INVERT this plane: OpenAI        │
│  ┌───────────┐  ┌───────┴────────┐  ┌──────────┐  ┌──────────────────────────┐  │
│  │ Tokenizer │─▶│ Prefill + cache│─▶│ Decode   │─▶│ Parser                   │  │
│  │ + tools   │  │ tools JSON 1st │  │ sampler  │  │ function_call / tool_use │  │
│  │ prefix    │  │ TTFT KPI       │  │          │  │ / functionCall + id      │  │
│  └───────────┘  └────────────────┘  └──────────┘  └────────────┬─────────────┘  │
│   Server tools: web_search, file_search, code_interpreter,     │                │
│   hosted MCP, Anthropic web_fetch, Gemini Search — execute     │                │
│   inside the provider; conversation still owned by control.    │                │
└────────────────────────────────────────────────────────────────┼────────────────┘
                                                                 │
              ┌──────────────────────────────────────────────────┤
              │ if stop_reason = tool_use                        │ if final
              ▼                                                  ▼
┌─────────────────────────────────────────────┐    ┌─────────────────────────────┐
│ TOOL PROXIES (untrusted planner; no IAM)    │    │ PERSISTENCE                 │
│                                             │    │                             │
│  ┌───────────────────────────────────────┐  │    │  ┌───────────────────────┐  │
│  │ FUNCTION-CALL DISPATCHER              │  │    │  │ Durable app state     │  │
│  │ parse → validate JSON Schema → RBAC   │  │    │  │ Temporal Activities   │  │
│  │ → timeout child → execute → map error │  │    │  │ LangGraph checkpointer│  │
│  │ NEVER execute partial streaming JSON  │  │    │  │ thread_id / workflow  │  │
│  └───────────────┬───────────────────────┘  │    │  └───────────────────────┘  │
│                  │                          │    │  ┌───────────────────────┐  │
│     ┌────────────┼────────────┬─────────────┤    │  │ Artifacts (RPO≠TTL)   │  │
│     ▼            ▼            ▼             │    │  │ S3/GCS: stdout, HAR,  │  │
│  ┌────────┐  ┌──────────┐  ┌─────────────┐  │    │  │ screenshots, files    │  │
│  │ API    │  │ BROWSER  │  │ CODE-EXEC   │  │    │  │ before sandbox expire │  │
│  │ ADAPTER│  │ SANDBOX  │  │ SANDBOX     │  │    │  └───────────────────────┘  │
│  │ REST   │  │ Playwright│  │ Firecracker│  │    │  ┌───────────────────────┐  │
│  │ gRPC   │  │ MCP ctx  │  │ gVisor     │──┼────┼─▶│ Soft caches           │  │
│  │ GraphQL│  │ snapshot │  │ WASM/WASI  │  │    │  │ prompt cache (tools)  │  │
│  │ MCP    │  │ or pixels│  │ E2B / CI   │  │    │  │ sandbox snapshot      │  │
│  │ idem + │  │ CDP      │  │ Lambda µVM │  │    │  │ BrowserContext cookies│  │
│  │ cursor │  │ allowlist│  │ egress deny│  │    │  └───────────────────────┘  │
│  └────────┘  └──────────┘  └─────────────┘  │    └──────────────┬──────────────┘
└─────────────────────────────────────────────┘                   │
                                                                  │
┌─────────────────────────────────────────────────────────────────┴───────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐ │
│  │ Audit (WORM)│  │ Metrics      │  │ Trace spans │  │ Usage                  │ │
│  │ call_id,    │  │ tool_exec_ms │  │ gateway →   │  │ tokens + tool fees     │ │
│  │ hashed args,│  │ p50/p95/p99  │  │ adapter →   │  │ search $/1k, container │ │
│  │ policy dec.,│  │ breaker, RPM │  │ sandbox     │  │ hours, E2B vCPU-s      │ │
│  │ sandbox_id  │  │ pool util    │  │ browser ctx │  │ response.completed     │ │
│  └─────────────┘  └──────────────┘  └─────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (interactive agent) or sync HTTP (single tool hop) or a webhook (job completed). Gateway stamps `correlation_id`, authenticates the principal, checks org/project RPM/TPM **and** a separate downstream token bucket so the agent cannot stampede Stripe/GitHub. A closed circuit on a dependency is already a routing input.
2. **Policy.** Control plane maps `(principal, tenant, tool, args_shape)` → allow / deny / HITL **before** the model sees the catalog. High-impact names (`charge_card`, `send_email`, `puppeteer_evaluate`, shell, computer click) stay off the schema unless this turn is authorized. PII in the user text is detected and redacted **before tokenize**; secrets never appear as schema enums.
3. **Compile schemas.** OpenAPI 3.x operations, GraphQL SDL, or `.proto` unary RPCs compile to JSON Schema, then to the provider shape: OpenAI `{type:"function"}` + `strict`, Anthropic `input_schema`, Gemini `functionDeclarations` (strip `$schema` / `additionalProperties` / `$defs` — Gemini rejects JSON-Schema-only keys). Pin **tool order and text** for prompt-cache hits. Catalogs that bust the cache use `tool_search` / MCP `deferLoading`.
4. **Pack the model turn.** Set `tool_choice` (`auto` | `required` | named | OpenAI `allowed_tools` without mutating the cached list). Decide parallel: OpenAI `parallel_tool_calls`; Anthropic `disable_parallel_tool_use` **inside** `tool_choice`. Guard mixed batches: OpenAI GPT-5+ custom functions may parallelize, but **built-ins cannot share that batch**; Anthropic mixed server+client may return `stop_reason: "tool_use"` and you must finish client results before the server path continues; Gemini 3 circulates mixed built-in+custom via `previous_interaction_id`.
5. **Model data plane.** Tokenizer + tools prefix → prefill (cacheable) → decode → parser emits `function_call` / `tool_use` / `functionCall` with a unique id. Streaming: OpenAI `response.function_call_arguments.delta`, Gemini `step.delta` / `stream_function_call_arguments`. **Do not execute on partial JSON.**
6. **Dispatcher (sync contract).** Parse `tool_calls[]` → validate against JSON Schema (strict path) → authorize → execute with timeout child + idempotency key → map errors to `tool_result` / `is_error: true` / function response → re-inject **all** IDs in one user/tool turn. Anthropic: all `tool_result` blocks in **one** user message, results **before** any text, skipped calls still need `is_error: true`. Gemini: every `functionResponse` must echo `id`.
7. **API adapter path.** Auth is injected **outside** the model (OBO OAuth with RFC 8707 resource indicators, workload identity, or vault keys keyed by `tenant_id`). Pagination, retries, and idempotency live here — the model will re-call `list_orders` with `page=1` forever if you let it. Offset pagination is banned; cursor/`pageToken`/`starting_after` only, with `limit` capped in the adapter.
8. **Browser sandbox path.** Dedicated VM/container; per-task Playwright `BrowserContext` (incognito cookies/localStorage/cache). Snapshot-first (a11y tree + refs) unless the UI has no tree (canvas) — then Computer Use pixels. Sticky proxy per context if geo matters. HITL for checkout/ToS/cookies. Origin allowlists **are not a security boundary and do not affect redirects**.
9. **Code-exec sandbox path.** Create/resume a Firecracker microVM, gVisor `runsc`, WASM module, E2B session, OpenAI container, Anthropic `code_execution` container, Gemini 30 s Python sandbox, or Lambda MicroVM. Persist artifacts to object storage **before** TTL. Idempotent `create` with a client-generated sandbox key so retries do not leak VMs.
10. **Async / webhook resume.** Prefer start-work → `{job_id, pending}` → Temporal wait / Kafka signal over polling `get_job` every model turn. Stripe: HMAC `Stripe-Signature`, 5-minute skew, at-least-once up to 3 days, dedupe `event.id`. E2B: `sandbox.lifecycle.killed|paused`, verify `e2b-signature`, dedupe `e2b-delivery-id`.
11. **Persist and emit.** Orchestrator snapshots application state. Sandbox disks and `/mnt/data` are **not** durable. Audit sink gets `tool_name`, `call_id`, principal, hashed args, policy decision, downstream status, latency, sandbox id. Terminal usage event is the token+fee bill.

**Interview talking point:** “The model is an untrusted planner. IAM, egress, SSRF checks, and tool execution live on the tool host. Hosted tools move the data plane into the provider; mixed parallel groups are a topology hazard, not a convenience flag.”

---

## 2. Core Mechanics & Algorithms

### 2.1 APIs — OpenAPI / REST / gRPC / GraphQL adapters

Native tools are JSON Schema objects (`type: object`, `properties`, `required`). OpenAI **strict** additionally requires `additionalProperties: false` on every object and **all** `properties` keys in `required` (optional via `type: ["string","null"]`). Anthropic uses `input_schema`. Gemini uses an OpenAPI subset under `functionDeclarations[].parameters`. MCP `inputSchema` is JSON Schema 2020-12. Production adapters therefore: OpenAPI 3.0/3.1 or GraphQL SDL / `.proto` → JSON Schema → per-provider shape.

**REST.** Map each OpenAPI operation to **one** tool. One operation ≠ dump of 200 endpoints — schema tokens dominate the prefix (Anthropic tool-use system prompt already 286–804 tokens before your catalog). Bind `Authorization` / mTLS / signed cookies in the adapter. Pagination, retries, and idempotency belong in the adapter, not the LLM.

**gRPC.** Unary RPCs become tools. Client/server/bidi streams are unsupported or **long-running tools** — do not ask the model to consume a stream token-by-token. JSON↔protobuf is lossy for `oneof`, maps, and default-zero fields; validate with `protojson` and return field-level errors as `tool_result`. Map `UNAVAILABLE` / `DEADLINE_EXCEEDED` / `RESOURCE_EXHAUSTED` to retry vs fail-closed.

**GraphQL.** Introspection → one tool per **safe** query/mutation, or one `graphql_query` tool with a **persisted-query allowlist**. Unrestricted GraphQL is an RCE-shaped confused-deputy. Pagination is Relay: `edges { node, cursor }`, `pageInfo.hasNextPage/endCursor`, `first`/`after`. Cap query depth and node count **before** the model can pass `first: 10000`. Cost-based rate limits (GitHub-style points) replace simple RPM.

**Auth that survives audit.** (1) On-behalf-of OAuth with RFC 8707 so the downstream token is audience-bound to the API, not a passthrough of the MCP/client token. (2) Workload identity (AWS IAM / GCP ADC) for first-party APIs. (3) Per-tenant keys in a secret store, referenced by a `tenant_id` the model is allowed to pass. MCP remote HTTP: OAuth 2.1 + PKCE, RFC 9728 PRM, RFC 8414 / OIDC discovery. MUST NOT token-passthrough.

**Idempotency.** RFC 9110: GET/PUT/DELETE are idempotent; POST/PATCH are not. Stripe-class: client `Idempotency-Key` (UUIDv4, ≤255 chars); server stores status+body ≥24 h and replays it, including 500s; keys not saved if validation fails before execution. IETF draft-07 (expired 2026-04-18): `409` in-flight, `422` fingerprint mismatch. **Agent rule:** derive the key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` **or** from the runtime’s `call_id`/`tool_use_id` if exactly-once delivery of that ID is guaranteed. **Do not let the model invent the key** (hallucinated keys = duplicate charges).

**Pagination.** Offset (`page`/`offset`) drifts under concurrent writes — ban for agents. Prefer Stripe `starting_after`/`ending_before` + `has_more`; Google opaque `pageToken`; GitHub `Link: rel="next"` (RFC 8288); GraphQL Relay `after`. Cap `limit` in the adapter (e.g. max 100) regardless of schema.

**Retries.** Only **safe** failures: HTTP 408/429/5xx, gRPC `UNAVAILABLE`/`DEADLINE_EXCEEDED`, network reset. Honor `Retry-After`. Exponential backoff + full jitter. **Never retry POST without an idempotency key.** Convert 4xx (except 429) to `is_error` so the model can correct args instead of looping.

**Webhook vs poll.** Poll burns RPM and tokens. Pattern: start → pending `job_id` → Temporal/Kafka wait → webhook/signal → next model turn. Poll only when the vendor has no callback, and then with a **server-side** scheduler, not an LLM loop.

**Complexity.** Compiling \(O\) operations of average schema size \(s\) is \(\Theta(O s)\) once, then a prefix of \(\Theta(\sum s)\) tokens every uncached turn. Adapter HTTP is \(\Theta(1)\) per call plus pagination rounds \(p\); the invariant is \(p\) is **adapter-owned** (cursor loop with a cap), never model-owned.

### 2.2 Native function calling and parallel dispatch

**OpenAI (Responses + Chat Completions).** Tools: `type: function` + JSON Schema + `strict`. `tool_choice`: `"auto"` | `"required"` | `"none"` | `{type:"function", name}` | `allowed_tools`. `parallel_tool_calls: false` ⇒ 0 or 1 call. GPT-5+ may parallelize functions alongside built-ins, but built-ins stay out of the parallel function batch. `tool_search` + deferred tools on `gpt-5.4`+. Strict = Structured Outputs; incompatible schemas **reject** if `strict: true`. Responses omit `strict`: try to normalize, else fall back (`strict: false`). Chat Completions default non-strict. Fine-tunes: parallel **disables strict** for that turn. Injection: `function_call_output` / Chat Completions `role: tool` + `tool_call_id`.

**Anthropic.** `stop_reason: "tool_use"`, one or more `tool_use` blocks. Return **all** `tool_result` blocks in **one** user message; `tool_result` before any text; match `tool_use_id`. `tool_choice`: `{type:"auto"|"any"|"tool"|"none"}`; `disable_parallel_tool_use` lives **inside** `tool_choice`. Server tools run on Anthropic unless mixed with client tools. Computer/browser are **client toolsets** (`computer_toolset_20260801`, `browser_toolset_20260801`): one `type` entry expands to many members; execute members **in order** for a batch action. Programmatic tool calling (`code_execution_20260120`+): server container pauses and emits client `tool_use` with `caller` pointing at the code-execution run; continuation must echo the same `container` and tool list.

**Gemini.** `FunctionCallingConfig.mode`: `AUTO` | `ANY` | `NONE` | `VALIDATED` (preview: schema adherence; Gemini 3+ also enforces required params). Every `functionCall` has a unique `id` that **must** be echoed. Parallel supported. Remote MCP: `name` + `url` in tools config. Mixed bash + custom: `gemini-3.1-pro-preview-customtools`. Built-in + custom in one turn: tool context circulation via `previous_interaction_id`.

**Bedrock Converse.** `toolConfig.tools[].toolSpec.inputSchema.json` + `toolChoice` `{auto|{tool:{name}}}`; `stopReason: tool_use`; echo `toolUseId` on `toolResult`. Three modes include server-side Lambda / AgentCore Gateway.

**Hallucinated params.** Non-strict decoding invents keys, drops required fields, or emits invalid JSON. BFCL V4 weights hallucination/irrelevance **10%** of overall (1,122 samples). Mitigations: OpenAI `strict: true`, Gemini `VALIDATED`/`ANY`, **server-side JSON Schema validation before side effects**, enum/allowlists in the adapter, canonical ID lookup (never trust model-supplied PKs), BFCL-style irrelevance (abstain when no tool fits). One schema-error retry as `tool_result`, then fail.

**Parallel algorithm.** Providers emit \(N\) calls. Execute with `gather` + per-call try/catch; **always return \(N\) results**. One 500 must not drop sibling `tool_use_id`s. Complexity: wall time \(\approx \max_i t_i\) plus a global timeout envelope; work \(\Theta(N)\) with a bulkhead pool per tool class (REST ≠ browser ≠ sandbox). Duplicate-same-tool (documented on some nano SKUs): disable parallel for that model.

**Dispatcher state machine.**

```
IDLE ──complete()──▶ AWAIT_MODEL ──tool_use──▶ VALIDATE ──ok──▶ AUTHORIZE
   ▲                      │                      │ fail            │ deny
   │                      │ text/final           ▼                 ▼
   │                      │                   ERROR_RESULT     HITL / DENY
   │                      ▼                      │                 │
   │                   TERMINAL                  └────────┬────────┘
   │                                                      ▼
   │                                                 EXECUTE (budget)
   │                                                      │
   │                         ┌──────────┬─────────────────┼──────────────┐
   │                         ▼          ▼                 ▼              ▼
   │                       REST      BROWSER           CODE_EXEC     HOSTED
   │                         │          │                 │              │
   │                         └──────────┴────────inject ALL ids──────────┘
   └──────────── next turn (loop++) ; if loop≥Nmax or (tool,args_hash) repeat → STOP
```

**Invariants.** (I1) No side effect before schema+RBAC+policy. (I2) No execute on partial stream JSON. (I3) All ids echoed in one turn. (I4) Idempotency key is runtime-derived, never model-supplied. (I5) Mixed parallel groups are split or serialized per provider rules. (I6) `tool_choice: none` on the closing turn.

### 2.3 Browser — DOM/a11y snapshot vs screenshot / Computer Use

Three observation/action channels:

| Channel | Observation | Action | Typical stack |
| --- | --- | --- | --- |
| A11y / DOM snapshot | Accessibility tree + refs (`e5`) | Click/type by ref | Playwright MCP default |
| Screenshot / Computer Use | PNG/JPEG pixels; model returns coordinates | Click/type/scroll in VM | Anthropic computer/browser toolsets; OpenAI `computer`; Gemini `computer_use` |
| Hybrid | Snapshot + optional screenshot | LLM chooses; Playwright executes | browser-use on CDP |

**Playwright MCP (Microsoft).** 40+ tools; **snapshot-first**. Official comparison ~**200–400 tokens**/snapshot vs ~**3,000–5,000** for screenshots (Mintlify copy cites a wider 500–5,000 vs 10k–50k band — same direction). Actions must use snapshot refs, not screenshot pixels. Isolation: `BrowserContext` = incognito profile. Timeouts: action **5 s**, navigation **60 s**, expect **5 s** defaults. `--allowed-origins` / `--blocked-origins`: **not a security boundary and does not affect redirects**. `--isolated` vs addressable `sessionId`: one browser process, N contexts.

**browser-use.** Python loop: `get_browser_state_summary(include_screenshot=True)` → LLM `AgentOutput` → `tools.act`. Shares Chrome with Playwright via CDP. Domain allowlists in `Browser`/`BrowserSession` config are library-level, not a kernel boundary.

**Anthropic Computer Use (GA `computer_toolset_20260801`, no beta header on Claude API).** 17 members (`screenshot`, `left_click`, `type`, `zoom`, …); client-hosted VM/container; batch actions **in order**; not in Claude Managed Agents. Prefer `browser_toolset_20260801` for webpage-only work (DOM/a11y members, no full desktop). Default prefix ~**4,500** tokens (computer) / ~**6,600** (browser) **before** the first screenshot. Screenshot classifiers can force user confirmation on suspected injection.

**OpenAI Computer Use.** Responses `computer` tool; `computer_call.actions[]` run in order; return `computer_call_output` with `computer_screenshot`; prefer `detail: "original"`. GPT-5.6 does **not** resize original images — large screenshots consume unbounded input tokens. Observed working desktop sizes 1440×900 and 1600×900. `computer-use-preview` remains a specialized Responses-only model.

**Gemini Computer Use.** Client loop; screenshots in, `function_call` UI actions out; `ENVIRONMENT_BROWSER` (default) / `MOBILE` / `DESKTOP`. Playwright or Browserbase in the reference impl. Gemini 3.x may attach `intent` + `safety_decision` (`require_confirmation` / blocked).

**Required isolation (all three).** Dedicated VM/container; per-task context; sticky proxy if geo matters; no raw credentials in the model context; HITL for checkout/ToS/cookies. SPA never-idle, file-download dialogs, auth walls, CAPTCHA, and `page.waitFor` forever are the hang class — fix with a **task wall clock** (inferred ~5 min envelope from common Playwright agent practice; Anthropic does not publish one), hybrid snapshot+screenshot, abort `evaluate`/JS on untrusted pages, and never share one context across users (cookie bleed).

**Complexity.** A \(k\)-step snapshot loop costs \(\Theta(k \cdot 300)\) extra input tokens; screenshot loop \(\Theta(k \cdot 4000)\) plus vision billing. Coordinate click after CSS zoom is \(O(1)\) per action but fails closed without a fresh screenshot — treat zoom as a state transition that invalidates the last coordinate frame.

### 2.4 Code execution — sandbox isolation models

| Runtime | Isolation | Kernel | Typical cold path | Notes |
| --- | --- | --- | --- | --- |
| Firecracker | Hardware VM (KVM) | Guest Linux | Spec **≤125 ms** InstanceStart → guest `/sbin/init`; ≤5 MiB VMM; >95% bare-metal compute [pending test] | AWS Lambda, E2B, Lambda MicroVMs (2026-06). NumaVM lab (2026-03-10, **not** AWS SLA): SSH-ready **1,133 ms**; snapshot restore **176 ms** (load 25 ms) |
| gVisor `runsc` | Userspace Sentry + Gofer | Host kernel, ~50 syscalls | Process-start, no guest boot | GKE Sandbox, Cloud Run; nvproxy CUDA path |
| WASM/WASI | Capability runtime (Wasmtime / WasmEdge) | None | Sub-ms module start **[inferred from runtime design; ⚠️ no vendor p99 in this brief]** | WASI 0.2; not a full Linux ABI |
| E2B | Firecracker microVM | Guest Linux | Vendor marketing ~150 ms; pause/resume; **no GPU** | Internet **from sandbox by default** — egress risk. Default 2 vCPU + 512 MiB |
| OpenAI Code Interpreter / Hosted Shell | Provider VM | Debian 12 (shell docs; may change) | Auto or `/v1/containers`; idle **20 min**; memory **1g/4g/16g/64g** | **No egress by default**; org allowlist **and** `network_policy`. Expired auto container + `previous_response_id` **fails** rather than recreate |
| Anthropic `code_execution` | Anthropic container | Unspecified public ABI | Server tool; **5 min billing minimum** when charged | **$0** if `web_search_20260209+` or `web_fetch_20260209+` in the same request |
| Gemini code execution | Google sandbox | Python only | **30 s** max; up to **5** regenerate-on-error; no arbitrary file I/O | No extra tool fee |
| AWS Lambda MicroVMs (2026-06) | Firecracker | Guest | Snapshot resume; suspend/resume up to **8 h**; ARM64; up to 16 vCPU / 32 GB RAM / 32 GB disk in launch coverage | HTTPS URL (HTTP/2, gRPC, WebSockets); Anthropic self-hosted sandbox backend. GPU ⚠️ not claimed |

**Lifecycle state machine.** `image/template → create → running → (pause/snapshot) → resume → kill/expire`. E2B bills **running only**; pause does not bill; Hobby **1 h** / Pro **24 h** hard caps; webhook on killed/paused. OpenAI: auto reuse vs explicit `container_id`. Lambda MicroVMs: Dockerfile → snapshot image → URL; idle suspend with memory+disk. Anthropic Managed Agents: **$0.08/h** only while `running`. Always persist artifacts to object storage before TTL; never treat `/mnt/data` as durable. Idempotent `create` with a client-generated sandbox key; cap concurrent sandboxes to vendor quotas or Temporal retries will 429 the control plane.

**Network egress.** OpenAI hosted containers: deny-by-default; dashboard org allowlist **and** request `network_policy`. E2B: sandboxes can reach the internet. Gemini CI: assume locked (no extra network product surface in public docs). Pin DNS between allowlist check and connect (TOCTOU / rebinding). Enabling OpenAI network **explicitly** expands prompt-injection via fetched content.

**Isolation ranking for untrusted model-generated code.** Prefer Firecracker / Lambda MicroVMs. gVisor: Sentry exploit + remaining ~50 host syscalls. WASM: strongest capability model, weakest Linux compatibility. True escape is guest kernel + VMM (Firecracker) or Sentry + host syscall (gVisor). **More common lookalikes:** egress to IMDS, shared Docker socket, `puppeteer_evaluate` as in-browser RCE, over-granted WASM host functions. Lambda MicroVMs exist because container isolation was judged insufficient for AI-generated code.

**Complexity.** Cold start: Firecracker spec \(O(125\,\mathrm{ms})\) to `init`, but product UX is SSH-ready ~1.1 s unless you snapshot-restore (~176 ms). Exec bounded by product caps (Gemini 30 s, OpenAI 20 min idle, E2B session length). Loop detector must treat Gemini’s 5 regenerate-on-error as **internal** retries that still consume the outer turn budget.

---

## 3. Token Economics & NFR Analysis

Prices are **USD list, 2026-08-21**, from vendor docs cited in the research file. Vendor dashboards are source of truth for org-specific RPM/TPM. ⚠️ No vendor publishes p50/p95/p99 for a “tool dispatcher” as a product SLO — latency percentiles below are **engineering targets** labeled **[inferred]** unless a published figure is cited.

### 3.1 Cost per 1k runs

**Model token rates ($ / 1M tokens).** GPT-5.6 Sol **$5 / $0.50 cached / $30 out**; Terra **$2 / $0.20 / $12**; Luna **$0.20 / $0.02 / $1.20**. Batch 50%. GPT-5.6+ cache writes **1.25×**, 30 min TTL, min prefix 1,024; cached tokens **still count toward TPM**. Sonnet 5 **$2 / $10** (Sep 2026 rise to $3/$15 **will not occur**); Opus 5 / 4.8 **$5 / $25**; Haiku 4.5 **$1 / $5**. Anthropic cache: 5 min write 1.25×, 1 h write 2×, hit 0.1×; US `inference_geo` on 4.6+ **1.1×**; Fast Opus **$10 / $50**. Claude 4.7+ tokenizer ≈ **+30%** tokens vs prior for the same text. Anthropic cache hits **do not count against ITPM** (opposite of OpenAI). Gemini 3.6 Flash **$1.50 / $7.50**; 3.5 Flash **$1.50 / $9.00**; 3.1 Pro Preview **$2 / $12** (≤200k) and **$4 / $18** (>200k).

**Tool-definition overhead (Anthropic, billed as input):** tool-use system prompt **286–675** (`auto`) vs **406–804** (`any`/`tool`); computer toolset ~**4,500**; browser toolset ~**6,600**; bash extra 244–325; `text_editor_20250429` +700.

**Built-in tool fees (not token-only) → $ / 1k executions (fee line only unless noted):**

| Workload | Assumptions | **$ / 1k** |
| --- | --- | --- |
| OpenAI / Anthropic web search | Fee only | **$10.00** |
| OpenAI file search | Fee only | **$2.50** |
| Gemini 3 Search query (paid, after 5k prompts/mo free) | Fee only; a prompt may emit **multiple** queries | **$14.00** |
| Gemini 2.5 Pro grounded prompt (paid, after 1,500 RPD free) | Fee only | **$35.00** |
| OpenAI CI 1 GB session | 1k × $0.03 / 20 min | **$30.00** |
| OpenAI CI 4 / 16 / 64 GB | 1k × $0.12 / $0.48 / $1.92 | **$120 / $480 / $1,920** |
| Anthropic code exec (paid path) | 1k × 5 min min × $0.05/h | **$4.17** (+ tokens) |
| Anthropic Managed Agents | 1k × 1 min running × $0.08/h | **$1.33** |
| E2B default 2 vCPU + 512 MiB, **5 s** | $0.109/h × 5/3600 × 1k | **~$0.15** |
| E2B default **60 s** / **1 h** | ×12 / ×720 | **~$1.82 / ~$109** |
| Pure REST GET | Your API cost; LLM still pays schema+result tokens | **~$0.00** tool fee |

E2B compute: **$0.000014 / vCPU-s** = $0.0504 / vCPU-h; **$0.0000045 / GiB-s** = $0.0162 / GiB-h; paused/killed **$0**. Hobby: $0 + $100 credits, 1 h max, 20 concurrent, 1 create/s. Pro: $150/mo + usage, 24 h, 100–1,100 concurrent, 5 create/s.

**Worked LLM+tool turn [inferred composition from list prices].** Sonnet 5, 8k cached-hit input ($0.20/MTok) + 4k uncached ($2) + 800 out ($10) + 1 Anthropic web search ($0.01): \(0.0016 + 0.008 + 0.008 + 0.01 \approx\) **$0.028 / turn** → **$28 / 1k turns**. Playwright-MCP snapshot +300 input tokens: **$0.0006**; 4k-token screenshot: **$0.008** input — **~13×** the snapshot increment. ⚠️ Anthropic vision $/megapixel is on the Vision page — do not guess; count `usage` per turn. A 20-step screenshot loop is dominated by **image input**, not the 4.5k computer-toolset prefix. Cache the toolset: 100 Anthropic computer-use threads × ~4.5k × Sonnet $2/MTok ≈ **$0.90 / turn** uncached prefix, **$0.09 / turn** on 0.1× hit.

**Route cheap models** (Luna / Haiku / 3.6 Flash) for schema-valid CRUD; reserve Opus/Sol/3.1 Pro for computer-use and ambiguous tool choice.

### 3.2 Latency SLA targets and mitigations

Published figures (bounds, not dispatcher SLAs):

| Stage | Published figure | Kind |
| --- | --- | --- |
| Firecracker InstanceStart → init | **≤125 ms** | Spec bound |
| Firecracker host create rate | **up to 150 microVMs/s/host** | Spec |
| Firecracker cold SSH-ready | **1,133 ms** | NumaVM lab (not AWS SLA) |
| Firecracker snapshot restore SSH | **176 ms** | same |
| Playwright MCP action / nav | **5 s / 60 s** | Timeouts, not latency |
| Gemini code sandbox | **30 s** hard cap | Deadline |
| OpenAI container idle TTL | **20 min** | Lifecycle |
| OpenAI strict schema first compile | ⚠️ “1–2 s extra” | Third-party (Cadence); not official |
| Stripe webhook signature skew | **5 min** | Security window |

**p50 / p95 / p99 [inferred ops targets, not vendor SLOs].** Instrument `tool_exec_ms` / `llm_ms` / `sandbox_ms` yourselves.

| Class | p50 target | p95 target | p99 target | Mitigations |
| --- | --- | --- | --- | --- |
| REST / gRPC unary (incl. auth) | **[inferred]** 150–300 ms | **<800 ms** (research engineering bound) | **[inferred]** <2 s | Connection pool, mTLS session reuse, fail-closed on 4xx, Retry-After only on 429/5xx |
| GraphQL persisted query | **[inferred]** 200–400 ms | **[inferred]** <1.2 s | **[inferred]** <3 s | Depth/node cap, cost budget, no introspection in prod |
| Browser **snapshot** step | **[inferred]** 1–3 s (LLM + 5 s action budget) | **<8 s** (5 s action + LLM; research bound) | **[inferred]** <20 s (one 60 s nav in tail) | Snapshot-first, wall-clock task envelope, abort evaluate on untrusted pages |
| Browser **screenshot** / CU step | **[inferred]** 3–6 s | **[inferred]** 10–15 s | **[inferred]** >30 s on SPA/CAPTCHA | HITL on `safety_decision` / classifiers; do not offer CU on sync HTTP |
| Sandbox exec | Snapshot restore **176 ms** (lab) | **< product cap** (30 s Gemini; E2B session) | At the cap, then kill | Warm snapshot pool; persist artifacts before TTL |
| End-to-end agent turn (1 REST tool) | **[inferred]** 1–2 s + model TTFT | **[inferred]** 3–5 s | **[inferred]** 8–12 s | Cache tools JSON; Luna/Haiku for CRUD |

Nested timeout invariant: LLM request timeout > tool Activity `StartToCloseTimeout` > HTTP client timeout > downstream SLA. Computer-use loops need a wall-clock ceiling. OpenAI 20 min is **idle TTL**, not an exec timeout.

### 3.3 Throughput and back-pressure

**Hosted model quotas.** OpenAI: org + project RPM, TPM, RPD, TPD; tiers by spend (Free / T1 $5 … T5 $1,000 paid; usage caps $100 → $200k/month at T5). Dashboard + `x-ratelimit-*` are source of truth. Secondary compilations (verify in dashboard): GPT-4o-class T1 often cited **500 RPM / 30k TPM**, T5 **10k RPM / 30M TPM**. Anthropic: RPM + **ITPM** + **OTPM**; `429` = your limit (`retry-after`); **`529 overloaded_error` = provider saturation — failover provider, do not treat as 429**. Gemini: `RESOURCE_EXHAUSTED`; per-minute and per-day; Free / T1 $250 cap / T2 $2k / T3 $20k–$100k. E2B: create-rate **1/s** Hobby, **5/s** Pro; concurrency 20 / 100–1,100.

**Downstream REST** has its own RPM. The adapter needs a **separate** token bucket.

**Worked capacity [inferred from published limits, not a vendor sizing guide].** 100 concurrent agents × 4 parallel tools × 2 s avg REST = **200 in-flight HTTP** — size the pool and the SaaS RPM. Same 100 agents on E2B default ⇒ **100 concurrent sandboxes** = Pro floor (do not fake it with 5 Hobby accounts). 100 OpenAI 1 GB CI sessions = **$3.00 / 20 min** = **$9/h** container line **before tokens**.

**Back-pressure.**

1. Gateway admits only if breaker = closed/half-open **and** model TPM bucket has room **and** the **tool-class bulkhead** (REST / browser / sandbox) has a free slot.
2. 429 + `Retry-After` → wait. 529 → **switch provider**, do not backoff-as-quota.
3. One retry owner (Temporal Activity policy). Nested LLM timeout 60 s → tool HTTP 55 s → sandbox 30 s → downstream 25 s with independent retries is **retry amplification**.
4. Cap orchestrator turns (e.g. 8). Loop-detect `(tool, args_hash)`. `tool_choice: none` on the closing turn. Circuit-break identical GET pagination.
5. Shed browser and sandbox before CRUD REST: they hold scarce VM/context inventory.

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **control plane** (gateway + dispatcher). Model provider and SaaS APIs are **dependencies** | Multi-vendor fallback raises cost and tool-schema drift |
| RPO | Irreversible tools: **0** (outbox intent before POST). Conversation: Temporal/LangGraph checkpoint. Sandbox disk / `/mnt/data`: **TTL** (20 min OpenAI idle, Hobby 1 h, Pro 24 h) unless copied to object storage | Treating sandbox FS as RPO=0 loses data on expire |
| RTO | Interactive REST path: fail over <1 s to secondary adapter or cached read. Browser/CU: resume context or restart task. Code exec: snapshot restore ~176 ms **or** new container + tell the model files were lost — **never** `previous_response_id` against a dead OpenAI container | Fast failover vs identical sandbox state |
| Consistency | Tool side effects: **exactly-once via idempotency keys**. Model text: at-least-once retry may change tokens | Cannot bit-identical-retry temperature>0 |
| Compliance | OBO OAuth + RFC 8707; no token passthrough; regional inference (+10% / Anthropic `inference_geo` 1.1×); BAA before raw `tool_result` in third-party traces; screenshots are PII-dense (verify Anthropic ZDR table at request time) | Snapshot vs screenshot: tokens vs biometric/PII surface |
| Cost vs latency | Snapshot 200–400 tok vs screenshot 3k–50k; E2B 5 s **$0.15/1k** vs 1 h **$109/1k**; OpenAI 1 GB CI **$30/1k** sessions; web search **$10/1k** | Paying vision tokens to click a labeled button |
| Isolation vs ABI | Firecracker/Lambda µVM vs gVisor vs WASM vs OpenAI no-egress CI vs E2B internet-on | Strongest isolation (WASM/Firecracker) vs “pip install pandas” |
| Cache vs TPM | Anthropic cache hits exempt from ITPM; OpenAI cached tokens **still burn TPM** | Caching is a cost lever on OpenAI, a quota lever on Anthropic |

---

## 4. Distributed Resilience & Security

### 4.1 Durable tool execution (Temporal vs Kafka)

**Temporal rule:** Workflows are deterministic replay. **Every LLM call and every tool I/O is an Activity** (Temporal AI reference architecture). OpenAI Agents SDK + Temporal: orchestration in the workflow; model/tools/sandbox lifecycle as activities; `activity_as_tool`; sandbox session serialized so worker crash ≠ lost VM handle. Workflow-id = `tenant:thread_id` so two gateways cannot run the same conversation.

**Kafka role:** ingest webhooks and high-volume “job completed” events; **do not** put Kafka clients inside Workflow code. Pattern: webhook → Kafka → consumer `Signal`/`Update` Temporal. At-least-once ⇒ idempotency keys on the Activity. Topics: `agent.tool_intents` (outbox **before** POST), `agent.tool_results`, `agent.dlq`. Compaction on `thread_id` keeps a snapshot; the full log is chain-of-custody.

**LangGraph:** node-level checkpointer is durable *conversation* state, not a distributed lock around Stripe POST — still wrap side-effect tools in an external workflow or outbox.

**Sandbox in the log.** Persist `sandbox_id` + snapshot handle in activity results. On replay, resume; do not `create` again without the client-generated sandbox key. Copy artifacts to object storage as an Activity **before** the TTL Activity.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | HTTP 408/429/5xx, gRPC `UNAVAILABLE`/`DEADLINE_EXCEEDED`, TLS reset, Anthropic **529**, OpenAI RPM, E2B create-rate | Full-jitter backoff; honor `Retry-After`; **failover provider on 529**; retry POST **only** with idempotency key |
| Permanent | HTTP 4xx (not 429) illegal args, schema reject under `strict: true`, Gemini `VALIDATED` miss, unsupported gRPC stream as a tool | `is_error` once so the model can correct; then fail the turn. Do not loop |
| Poison pill | Same payload crashes the worker; recursive tool storm; truncated JSON executed as a tool; `(tool, args_hash)` repeats ≥ \(N_{\max}\); Gemini CI 5 regenerates exhausted | Hash + crash count or round cap → DLQ; never auto-replay irreversible tools |
| Semantic / injection | Schema-valid but unauthorized (`charge_card` to attacker); IPI in page text / `tool_result`; MCP tool poisoning (benign `tools/list`, malicious runtime) | RBAC + dual-LLM quarantine + action-screening vs **original user intent**; not a retry |
| Idempotency miss | Model-invented `Idempotency-Key`; retry POST without key; OpenAI auto-container expire mid-thread | Runtime-derived key; on container expiry **start a new container** and tell the model files were lost |

**Partial parallel failure.** Always return N results. Map each exception to `is_error: true` with the sibling `tool_use_id` intact.

**Cascading timeouts.** One retry owner. Bulkhead thread pools per tool class. Parallel tools must not share one global HTTP pool.

### 4.3 Circuit breaker and fallback chain

Per **downstream API** and per **sandbox pool**, not per model:

- **Closed:** traffic flows; consecutive 5xx/timeouts or error-rate window trips to open.
- **Open:** fail fast; cooldown (e.g. 30 s). Feed `is_error` to the model **once**, then short-circuit further calls to that dependency for the cooldown (prevents the model from retry-amplifying).
- **Half-open:** one probe (or small %). Success → closed; fail → open.

Combine with RPM token buckets. OpenAI 429 / Anthropic 429 = backoff; Anthropic 529 = **failover provider**.

**Fallback chain (tool path):** primary adapter (live REST / warm sandbox / snapshot browser) → secondary (read replica, cached GET, snapshot-restore sandbox, Playwright snapshot if CU pixels fail) → **degraded structured result** (`is_error: true`, `code: "dependency_unavailable"`, no free-form apology). Deterministic fallback must still be valid `tool_result` JSON so the next model turn can stop or HITL. Do not fall back from a write to a silent no-op.

> ⚠️ Gap: no vendor publishes breaker trip curves or p99 dispatcher histograms. Measure them.

### 4.4 Enterprise security

**Zero-Trust MCP.** MCP 2025-11-25 / 2026-07-28: remote servers are **OAuth 2.1 resource servers**. MUST: RFC 9728 PRM, audience-bound tokens (RFC 8707 `resource`), PKCE, no implicit grant, exact redirect URIs. MUST NOT: **token passthrough** to upstream APIs — exchange for a new scoped token. Proxies MUST **per-client consent** (confused deputy: static client_id + DCR + consent cookie). June 2026 Enterprise-Managed Authorization (ID-JAG, optional) for org SSO without per-server consent screens. Local STDIO: env credentials, not OAuth. HTTP localhost: bind `127.0.0.1`, validate `Origin`/`Host` (DNS rebinding). Hosted MCP on Responses lists and invokes remote tools without a callback into your Python process — treat that as a **provider-side proxy** still bound by audience and consent.

**Tool RBAC.** Belongs in the **dispatcher**, not the system prompt. Map `(principal, tenant, tool, args_shape)` → allow/deny/HITL. ADK `LongRunningFunctionTool` and Agents SDK approval hooks are HITL productization. Least privilege **per turn**: do not attach `send_email` unless the user asked to send mail.

**PII: detect → redact → audit.** Tool results re-enter the context window — redact or summarize **before injection**. Do not log raw `tool_result` to third-party traces without a BAA. Computer-use screenshots are PII-dense. Persist placeholders and hashes in the audit map, never the raw match, in WORM/SIEM. Temporal history is a natural Activity audit; OpenAI Agents SDK tracing and Bedrock `requestMetadata` are **not** a substitute for an immutable SIEM trail. Audit fields: `tool_name`, `call_id`, principal, hashed args, policy decision, downstream status, latency, sandbox id.

**SSRF.** Browser `navigate` and `web_fetch` / custom HTTP tools must deny link-local **169.254.169.254**, RFC1918, localhost, and metadata IPv6. Playwright MCP origin lists **do not stop redirects** — re-check the **resolved** IP after every hop; pin DNS (TOCTOU / rebinding). MCP `server-puppeteer` advisory: navigate + screenshot/evaluate = SSRF + XSS-in-agent.

**Sandbox escape.** Prefer Firecracker/Lambda MicroVMs for untrusted generated code. Block IMDS and Docker socket. OpenAI: keep network off unless dual control (admin allowlist + `network_policy`). E2B internet-on-by-default is a **policy** choice, not a CVE — treat it as egress risk. Abort `evaluate`/JS tools on untrusted pages.

**Prompt injection via tool results (LLM01, ASI01/02/05; MCP tool poisoning).** Benign `tools/list` descriptions, malicious **runtime responses**. Dual-LLM: quarantined model reads untrusted pages/tool JSON; privileged model only sees structured summaries and holds the tools. Action-screening: compare proposed tool call to **original user intent**, not to the poisoned observation. Anthropic CU screenshot classifiers + confirmation; OpenAI: treat all page text/screenshots as untrusted. JSON-encode third-party strings (delimiter breakout). Untrusted content **only** in `tool_result`, never system. Empirical: InjecAgent / 2026 MCP-client study — prompt-only defenses fail. Prompt-only is not a control.

---

## 5. Production Enterprise Code

Stdlib-only dispatcher: full-jitter retries, circuit breaker (closed → open → half-open), primary → secondary → degraded `tool_result`, correlation-id JSON logs, JSON Schema validation of tool args (incl. OpenAI-style `["string","null"]` unions), nested timeout budget, sandbox/SSRF policy, idempotent execute, parallel gather that always returns N ids. Run: `python tool_runtime.py`.

```python
#!/usr/bin/env python3
"""Tool dispatcher primitives (stdlib only). Run: python tool_runtime.py"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import random
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "call_id": getattr(record, "call_id", None),
            "tool": getattr(record, "tool", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str) -> CorrelationAdapter:
    base = logging.getLogger("tool.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(base, {"correlation_id": correlation_id, "tenant": tenant})


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text

    def _sub_factory(label: str) -> Callable[[re.Match[str]], str]:
        def _sub(m: re.Match[str]) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{label}:{digest}>"
            audit.append({"type": label, "placeholder": token})
            return token
        return _sub

    for label, pat in _PII_PATTERNS:
        out = pat.sub(_sub_factory(label), out)
    return out, audit


class SchemaError(ValueError):
    pass


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class PoisonPillError(PermanentError):
    pass


class CircuitOpenError(Exception):
    pass


class TimeoutBudgetError(TransientError):
    pass


class PolicyDeniedError(PermanentError):
    pass


def _types(schema: dict[str, Any]) -> list[str]:
    raw = schema.get("type")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    types = _types(schema)
    if instance is None:
        if "null" in types:
            return
        raise SchemaError(f"{path} unexpected null")
    effective = [t for t in types if t != "null"] or types
    if "object" in effective:
        if not isinstance(instance, dict):
            raise SchemaError(f"{path} expected object")
        _validate_object(instance, schema, path)
        return
    if "array" in effective:
        if not isinstance(instance, list):
            raise SchemaError(f"{path} expected array")
        item_schema = schema.get("items", {})
        for i, item in enumerate(instance):
            validate_schema(item, item_schema, f"{path}[{i}]")
        return
    checkers: dict[str, Callable[[Any], bool]] = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: type(v) is int,
        "boolean": lambda v: isinstance(v, bool),
    }
    if effective and not any(checkers.get(t, lambda _v: False)(instance) for t in effective):
        raise SchemaError(f"{path} expected {'|'.join(effective)}")
    enum = schema.get("enum")
    if enum is not None and instance not in enum:
        raise SchemaError(f"{path} not in enum")


def _validate_object(instance: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    props: dict[str, Any] = schema.get("properties", {})
    required = schema.get("required", list(props))
    additional = schema.get("additionalProperties", False)
    for key in required:
        if key not in instance:
            raise SchemaError(f"{path}.{key} required")
    for key, value in instance.items():
        if key not in props and additional is False:
            raise SchemaError(f"{path}.{key} additionalProperties=false")
        if key in props:
            validate_schema(value, props[key], f"{path}.{key}")


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    retry_after: float | None = None,
    budget: "TimeoutBudget | None" = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        if budget is not None:
            budget.check()
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            if retry_after is not None:
                cap = max(cap, retry_after)
            if budget is not None:
                cap = min(cap, budget.remaining())
            time.sleep(random.random() * max(cap, 0.0))  # full jitter
    assert last is not None
    raise last


@dataclass
class TimeoutBudget:
    deadline_mono: float

    @classmethod
    def from_seconds(cls, seconds: float) -> TimeoutBudget:
        if seconds <= 0:
            raise TimeoutBudgetError("budget must be positive")
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, self.deadline_mono - time.monotonic())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise TimeoutBudgetError("timeout budget exhausted")

    def child(self, cap_seconds: float) -> TimeoutBudget:
        self.check()
        child_deadline = min(self.deadline_mono, time.monotonic() + cap_seconds)
        if child_deadline <= time.monotonic():
            raise TimeoutBudgetError("child budget empty")
        return TimeoutBudget(child_deadline)


_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.google.internal.",
    }
)
_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
_LINK_LOCAL_V6 = ipaddress.ip_network("fe80::/10")
_METADATA_V6 = ipaddress.ip_network("fd00:ec2::/48")


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip in _LINK_LOCAL
        or ip in _LINK_LOCAL_V6
        or ip in _METADATA_V6
        or str(ip) == "169.254.169.254"
    )


def _host_blocked(host: str) -> bool:
    h = host.strip("[]").lower().rstrip(".")
    if h in _BLOCKED_HOSTS or h.endswith(".localhost"):
        return True
    literals: list[str] = [h]
    try:
        ipaddress.ip_address(h)
    except ValueError:
        try:
            literals = [info[4][0] for info in socket.getaddrinfo(h, None)]
        except socket.gaierror:
            return False
    for lit in literals:
        try:
            if _ip_blocked(ipaddress.ip_address(lit)):
                return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class SandboxPolicy:
    allowed_runtimes: frozenset[str]
    allow_egress: bool
    max_mem_mb: int
    max_vcpu: int
    browser_origins: frozenset[str]
    redirect_recheck: bool = True


def check_sandbox_policy(
    *,
    kind: str,
    runtime: str,
    url: str | None,
    mem_mb: int,
    vcpu: int,
    policy: SandboxPolicy,
) -> None:
    if kind in {"code", "browser"} and runtime not in policy.allowed_runtimes:
        raise PolicyDeniedError(f"runtime {runtime} not in allowlist")
    if mem_mb > policy.max_mem_mb or vcpu > policy.max_vcpu:
        raise PolicyDeniedError("resource limit exceeded")
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise PolicyDeniedError(f"bad scheme {parsed.scheme}")
    host = parsed.hostname or ""
    if _host_blocked(host):
        raise PolicyDeniedError(f"SSRF deny host {host}")
    if kind == "browser" and policy.browser_origins:
        origin = f"{parsed.scheme}://{host}"
        if origin not in policy.browser_origins:
            raise PolicyDeniedError(f"origin {origin} not allowlisted")
    if kind in {"code", "http"} and not policy.allow_egress and host:
        raise PolicyDeniedError("egress denied by policy")


def idempotency_key(tenant: str, tool: str, args: dict[str, Any], intent_id: str) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    raw = f"{tenant}|{tool}|{canonical}|{intent_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # http | browser | code
    parameters: dict[str, Any]
    runtime: str = "rest"
    irreversible: bool = False
    http_timeout_s: float = 8.0


@dataclass(frozen=True)
class FunctionCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    payload: str
    is_error: bool
    idempotency_key: str
    degraded: bool = False


class ToolExecutor(Protocol):
    def run(self, spec: ToolSpec, args: dict[str, Any], budget: TimeoutBudget) -> dict[str, Any]:
        ...


class IdempotencyStore:
    def __init__(self) -> None:
        self._done: dict[str, ToolResult] = {}
        self._inflight: set[str] = set()
        self._crash_n: dict[str, int] = {}
        self._lock = threading.Lock()

    def begin(self, key: str) -> ToolResult | None:
        with self._lock:
            hit = self._done.get(key)
            if hit is not None:
                return hit
            if key in self._inflight:
                raise TransientError("idempotency 409 in-flight")
            n = self._crash_n.get(key, 0)
            if n >= 3:
                raise PoisonPillError(f"poison pill {key[:12]}")
            self._inflight.add(key)
            return None

    def commit(self, key: str, result: ToolResult) -> None:
        with self._lock:
            self._inflight.discard(key)
            self._done[key] = result
            self._crash_n.pop(key, None)

    def abort_crash(self, key: str) -> None:
        with self._lock:
            self._inflight.discard(key)
            self._crash_n[key] = self._crash_n.get(key, 0) + 1


class RestPrimary:
    def __init__(self, live: dict[str, Any] | Exception) -> None:
        self._live = live

    def run(self, spec: ToolSpec, args: dict[str, Any], budget: TimeoutBudget) -> dict[str, Any]:
        budget.child(spec.http_timeout_s).check()
        if isinstance(self._live, Exception):
            raise self._live
        return {"ok": True, "source": "primary", "echo": args, "data": self._live}


class RestSecondary:
    def __init__(self, cache: dict[str, Any]) -> None:
        self._cache = cache

    def run(self, spec: ToolSpec, args: dict[str, Any], budget: TimeoutBudget) -> dict[str, Any]:
        budget.check()
        return {"ok": True, "source": "secondary_cache", "echo": args, "data": self._cache}


class BrowserExecutor:
    def run(self, spec: ToolSpec, args: dict[str, Any], budget: TimeoutBudget) -> dict[str, Any]:
        budget.child(min(spec.http_timeout_s, 5.0)).check()
        return {
            "ok": True,
            "channel": "snapshot",
            "ref": args.get("ref", "e5"),
            "tokens_est": 300,
        }


class CodeExecExecutor:
    def run(self, spec: ToolSpec, args: dict[str, Any], budget: TimeoutBudget) -> dict[str, Any]:
        budget.child(min(spec.http_timeout_s, 30.0)).check()
        return {
            "ok": True,
            "runtime": spec.runtime,
            "stdout": str(args.get("code", "print(2+2)"))[:2000],
            "artifact_uri": "s3://tools/artifacts/demo.json",
        }


def degraded_result(call: FunctionCall, key: str, reason: str) -> ToolResult:
    body = {
        "ok": False,
        "code": "dependency_unavailable",
        "reason": reason,
        "degraded": True,
    }
    redacted, _audit = redact_pii(json.dumps(body))
    return ToolResult(call.id, call.name, redacted, True, key, True)


class ToolDispatcher:
    def __init__(
        self,
        specs: dict[str, ToolSpec],
        allowed: dict[str, set[str]],
        policy: SandboxPolicy,
        executors: dict[str, ToolExecutor],
        fallbacks: dict[str, ToolExecutor],
        breakers: dict[str, CircuitBreaker],
        store: IdempotencyStore,
        *,
        seen_hashes: dict[str, int] | None = None,
        loop_limit: int = 3,
    ) -> None:
        self.specs = specs
        self.allowed = allowed
        self.policy = policy
        self.executors = executors
        self.fallbacks = fallbacks
        self.breakers = breakers
        self.fallback_breakers = {k: CircuitBreaker() for k in breakers}
        self.store = store
        self.seen_hashes = seen_hashes if seen_hashes is not None else {}
        self.loop_limit = loop_limit

    def _authorize(self, principal: str, spec: ToolSpec) -> None:
        granted = self.allowed.get(principal, set())
        if spec.name not in granted:
            raise PolicyDeniedError(f"rbac deny {spec.name}")

    def _policy_args(self, spec: ToolSpec, args: dict[str, Any]) -> None:
        url = args.get("url") if isinstance(args.get("url"), str) else None
        check_sandbox_policy(
            kind=spec.kind,
            runtime=spec.runtime,
            url=url,
            mem_mb=int(args.get("mem_mb", 512)),
            vcpu=int(args.get("vcpu", 2)),
            policy=self.policy,
        )

    def execute_one(
        self,
        call: FunctionCall,
        *,
        principal: str,
        tenant: str,
        intent_id: str,
        budget: TimeoutBudget,
        log: CorrelationAdapter,
    ) -> ToolResult:
        spec = self.specs.get(call.name)
        if spec is None:
            raise PermanentError(f"unknown tool {call.name}")
        validate_schema(call.arguments, spec.parameters)
        self._authorize(principal, spec)
        self._policy_args(spec, call.arguments)
        key = idempotency_key(tenant, spec.name, call.arguments, intent_id)
        cached = self.store.begin(key)
        if cached is not None:
            log.info("idempotent replay", extra={"call_id": call.id, "tool": spec.name})
            return cached
        loop_fp = f"{spec.name}|{key}"
        self.seen_hashes[loop_fp] = self.seen_hashes.get(loop_fp, 0) + 1
        if self.seen_hashes[loop_fp] > self.loop_limit:
            self.store.abort_crash(key)
            raise PoisonPillError("infinite tool loop")
        primary_breaker = self.breakers[spec.kind]
        fallback_breaker = self.fallback_breakers[spec.kind]
        redacted_args, pii_audit = redact_pii(json.dumps(call.arguments, sort_keys=True))
        log.info(
            "tool.execute",
            extra={
                "call_id": call.id,
                "tool": spec.name,
                "pii_redactions": len(pii_audit),
                "args_redacted": redacted_args[:500],
            },
        )

        def _invoke(execu: ToolExecutor, br: CircuitBreaker) -> dict[str, Any]:
            br.allow()
            try:
                out = execu.run(spec, call.arguments, budget)
                br.record_success()
                return out
            except TransientError:
                br.record_failure()
                raise

        try:
            primary = self.executors[spec.kind]
            raw = retry_call(lambda: _invoke(primary, primary_breaker), budget=budget)
            payload, _a = redact_pii(json.dumps(raw, default=str))
            result = ToolResult(call.id, spec.name, payload, False, key, False)
            self.store.commit(key, result)
            return result
        except (CircuitOpenError, TransientError, TimeoutBudgetError) as exc:
            log.warning("primary failed; fallback", extra={"call_id": call.id, "tool": spec.name})
            secondary = self.fallbacks.get(spec.kind)
            if secondary is not None and not spec.irreversible:
                try:
                    raw = retry_call(
                        lambda: _invoke(secondary, fallback_breaker),
                        attempts=2,
                        budget=budget,
                    )
                    payload, _a = redact_pii(json.dumps(raw, default=str))
                    result = ToolResult(call.id, spec.name, payload, False, key, True)
                    self.store.commit(key, result)
                    return result
                except (CircuitOpenError, TransientError, TimeoutBudgetError):
                    pass
            result = degraded_result(call, key, type(exc).__name__)
            self.store.commit(key, result)
            return result
        except PermanentError:
            self.store.abort_crash(key)
            raise
        except Exception:
            self.store.abort_crash(key)
            raise

    def execute_parallel(
        self,
        calls: list[FunctionCall],
        *,
        principal: str,
        tenant: str,
        intent_id: str,
        budget: TimeoutBudget,
        log: CorrelationAdapter,
    ) -> list[ToolResult]:
        if not calls:
            return []
        results: dict[str, ToolResult] = {}

        def _one(c: FunctionCall) -> tuple[str, ToolResult]:
            try:
                return c.id, self.execute_one(
                    c,
                    principal=principal,
                    tenant=tenant,
                    intent_id=intent_id,
                    budget=budget,
                    log=log,
                )
            except Exception as exc:
                key = idempotency_key(tenant, c.name, c.arguments, intent_id)
                body = {"ok": False, "code": "is_error", "reason": type(exc).__name__, "detail": str(exc)}
                payload, _a = redact_pii(json.dumps(body))
                return c.id, ToolResult(c.id, c.name, payload, True, key, True)

        with ThreadPoolExecutor(max_workers=min(8, len(calls))) as pool:
            futs = [pool.submit(_one, c) for c in calls]
            for fut in as_completed(futs):
                cid, res = fut.result()
                results[cid] = res
        return [results[c.id] for c in calls]


LIST_ORDERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cursor": {"type": ["string", "null"]},
        "limit": {"type": "integer"},
    },
    "required": ["cursor", "limit"],
    "additionalProperties": False,
}

NAVIGATE: dict[str, Any] = {
    "type": "object",
    "properties": {"url": {"type": "string"}, "ref": {"type": ["string", "null"]}},
    "required": ["url", "ref"],
    "additionalProperties": False,
}

RUN_CODE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "mem_mb": {"type": "integer"},
        "vcpu": {"type": "integer"},
    },
    "required": ["code", "mem_mb", "vcpu"],
    "additionalProperties": False,
}


def _demo() -> None:
    cid = str(uuid.uuid4())
    log = build_logger(cid, "acme")
    policy = SandboxPolicy(
        allowed_runtimes=frozenset({"rest", "playwright", "firecracker"}),
        allow_egress=False,
        max_mem_mb=4096,
        max_vcpu=4,
        browser_origins=frozenset({"https://docs.example.com"}),
    )
    specs = {
        "list_orders": ToolSpec("list_orders", "http", LIST_ORDERS, "rest", False, 2.0),
        "browser_navigate": ToolSpec("browser_navigate", "browser", NAVIGATE, "playwright", False, 5.0),
        "run_python": ToolSpec("run_python", "code", RUN_CODE, "firecracker", False, 10.0),
    }
    allowed = {"analyst": {"list_orders", "browser_navigate", "run_python"}}
    http_breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=0.05)
    dispatch = ToolDispatcher(
        specs,
        allowed,
        policy,
        executors={
            "http": RestPrimary(TransientError("429")),
            "browser": BrowserExecutor(),
            "code": CodeExecExecutor(),
        },
        fallbacks={"http": RestSecondary({"orders": [{"id": "ord_1"}]})},
        breakers={"http": http_breaker, "browser": CircuitBreaker(), "code": CircuitBreaker()},
        store=IdempotencyStore(),
    )
    budget = TimeoutBudget.from_seconds(5.0)
    calls = [
        FunctionCall("call_rest", "list_orders", {"cursor": None, "limit": 20}),
        FunctionCall("call_browser", "browser_navigate", {"url": "https://docs.example.com/q", "ref": "e5"}),
        FunctionCall("call_code", "run_python", {"code": "print(2+2)", "mem_mb": 512, "vcpu": 2}),
    ]
    results = dispatch.execute_parallel(
        calls, principal="analyst", tenant="acme", intent_id="intent-1", budget=budget, log=log
    )
    assert len(results) == 3
    by_id = {r.call_id: r for r in results}
    assert by_id["call_rest"].degraded is True
    assert "secondary_cache" in by_id["call_rest"].payload
    assert by_id["call_browser"].is_error is False
    assert by_id["call_code"].is_error is False
    replay = dispatch.execute_one(
        calls[0],
        principal="analyst",
        tenant="acme",
        intent_id="intent-1",
        budget=TimeoutBudget.from_seconds(2.0),
        log=log,
    )
    assert replay.idempotency_key == by_id["call_rest"].idempotency_key
    try:
        validate_schema({"limit": 20}, LIST_ORDERS)
        raise SystemExit("schema should require cursor")
    except SchemaError:
        pass
    try:
        check_sandbox_policy(
            kind="http",
            runtime="rest",
            url="http://169.254.169.254/latest/meta-data/",
            mem_mb=128,
            vcpu=1,
            policy=policy,
        )
        raise SystemExit("SSRF should deny IMDS")
    except PolicyDeniedError:
        pass
    open_breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    open_breaker.record_failure()
    assert open_breaker.state is BreakerState.OPEN
    try:
        open_breaker.allow()
        raise SystemExit("open circuit must fail closed")
    except CircuitOpenError:
        pass
    print(json.dumps({"ok": True, "n": len(results), "correlation_id": cid}))


if __name__ == "__main__":
    _demo()
```

Graceful degradation contract: when the primary REST adapter raises transient/429/open-circuit, the dispatcher tries the secondary cache **only for reversible reads**, then returns a structured `is_error` payload the model can end on. Parallel gather always emits one result per `call_id`. Poison-pill detection is three crashes on the same idempotency key or `(tool, args_hash)` above `loop_limit`. Nested `TimeoutBudget.child` enforces LLM envelope > activity > HTTP/sandbox cap.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Internal SaaS copilot (REST + GraphQL, native function calling)

**Problem statement.** Multi-tenant internal copilot over Jira-class GraphQL and Stripe-class REST: **~100 concurrent sessions**, typically 1–4 **parallel read** tools per turn, irreversible writes (`create_invoice`, `transition_issue`). SLO: REST-tool p95 **<800 ms** including auth **[research engineering bound]**; end-to-end interactive turn p95 **[inferred] ≤5 s** on Haiku/Luna/Flash. Polling `get_job` is forbidden. Compliance: OBO OAuth (RFC 8707), no token passthrough, BAA on traces. Cost envelope: schema+JSON tools, not computer-use; web search is not on the hot path.

**Proposed architecture.**

```
┌────────────┐   ┌─────────────────────────────────────────────────────────────┐
│ Slack/IDE  │   │ CONTROL PLANE (VPC)                                         │
│ 100 sess.  │──▶│ Gateway: tenant TPM + SaaS RPM bucket + correlation-id      │
└────────────┘   │ Policy: RBAC/turn, HITL on POST, PII redact pre-tokenize    │
                 │ Schema compiler: OpenAPI + persisted GraphQL → strict JSON  │
                 │ Packer: parallel reads ON; parallel_tool_calls=false writes │
                 │ Orchestrator: Temporal workflow; max_turns=8; no poll loops │
                 └───────┬───────────────────────────────┬─────────────────────┘
                         │ complete()                    │ Activities
                         ▼                               ▼
                 ┌───────────────────┐         ┌───────────────────────────────┐
                 │ DATA PLANE        │         │ TOOL PROXIES                  │
                 │ Haiku/Luna/Flash  │         │ REST adapter: Idempotency-Key │
                 │ tools JSON cached │         │   cursor page, Retry-After    │
                 │ strict / VALIDATED│         │ GraphQL: persisted queries,   │
                 │                   │         │   depth/node cap              │
                 │                   │         │ MCP gateway: OAuth 2.1, aud,  │
                 │                   │         │   no passthrough              │
                 └───────────────────┘         └──────────────┬────────────────┘
                                                              │
                 ┌────────────────────────────────────────────┴────────────────┐
                 │ PERSISTENCE / TELEMETRY                                     │
                 │ Kafka: Stripe/Jira webhooks → Temporal Signal (dedupe id)   │
                 │ Postgres checkpoints │ WORM audit (hashed args, call_id)    │
                 └─────────────────────────────────────────────────────────────┘
```

**Technology choices.** Native function calling + OpenAPI adapter; `strict: true` / Gemini `VALIDATED`; MCP only for third-party SaaS with OAuth. Jobs: webhook → Kafka → Temporal, never `get_job` in the LLM loop. Cache the OpenAPI-derived tool list (Anthropic ITPM relief; OpenAI cost relief). Bedrock Converse + AgentCore if the org is AWS-IAM-centric. CrewAI only if role crews and a separate `function_calling_llm` are an explicit product requirement.

**Trade-off evaluation matrix.**

| Dimension | A. Dump 200 OpenAPI endpoints + poll jobs in-band | B. Recommended: 1-op=1-tool catalog + strict FC + webhook/Temporal + cursor pagination | C. Unrestricted GraphQL tool + model-invented Idempotency-Key |
| --- | --- | --- | --- |
| Cost / 1k turns | Schema tokens dominate prefix; poll burns RPM every turn | Steady Sonnet-class cached agent **[worked]** ~$14–$28/1k with 0–1 search; CRUD on Haiku/Luna far lower | Same tokens plus duplicate POST charges |
| Latency | Offset `page=1` loops; p95 > SLO | REST p95 **<800 ms**; no poll turns | Query `first: 10000` tails; 409 in-flight storms |
| Ops complexity | Looks simple; hidden job loops | Medium (Temporal, Kafka, two RPM buckets) | Low until finance incident |
| Security posture | Broad tools = ASI02 / LLM03 | Dispatcher RBAC, OBO tokens, HITL writes | Confused-deputy GraphQL ≈ RCE-shaped |
| Scalability | 100 agents × poll QPS stampede SaaS RPM | 100 × 4 parallel × 2 s = 200 in-flight HTTP — poolable | Downstream cost-limit lockouts |

**Decision rationale.** **B** is the only option that keeps schema tokens cacheable, puts pagination/idempotency in the adapter, and meets the p95 REST bound without an LLM poll loop. A fails token economics and downstream RPM. C fails audit (model-invented keys, unrestricted GraphQL). Writes stay serial (`parallel_tool_calls=false` / Anthropic `disable_parallel_tool_use`) with runtime-derived idempotency keys.

### Scenario 2 — Research / analysis agent (browser sandbox + code-exec sandbox)

**Problem statement.** Knowledge-work agent: browse vendor docs and internal Confluence, extract tables, run pandas in a sandbox, return a cited memo. Peak **100 concurrent tasks**. CAPTCHA/ToS in-scope. Must not treat Playwright origin lists as a kernel boundary. Cost: prefer 200–400 tok snapshots over 3k–50k screenshots; code-exec fee line must stay near E2B **~$0.15/1k** (5 s) or OpenAI 1 GB CI **$30/1k sessions**, not E2B **~$109/1k** hour-long VMs. Isolation: untrusted model-generated Python. Fail closed on IMDS/RFC1918. HITL when Gemini `safety_decision=require_confirmation` or Anthropic screenshot classifiers fire.

**Proposed architecture.**

```
┌────────────┐   ┌─────────────────────────────────────────────────────────────┐
│ Analyst UI │──▶│ CONTROL PLANE                                               │
│ 100 tasks  │   │ Gateway admit if browser-pool AND sandbox-pool have slots   │
└────────────┘   │ Dual-LLM: quarantine model reads pages; privileged holds    │
                 │   tools; action-screen vs original user intent              │
                 │ Wall clock ~5 min [inferred]; max_turns; HITL on checkout   │
                 └───────┬────────────────────────────┬────────────────────────┘
                         │                            │
                         ▼                            ▼
                 ┌───────────────────┐      ┌──────────────────────────────────┐
                 │ BROWSER SANDBOX   │      │ CODE-EXEC SANDBOX                │
                 │ Dedicated VM      │      │ Firecracker / Lambda MicroVM /   │
                 │ Playwright MCP    │      │ OpenAI CI (egress OFF)           │
                 │ snapshot-first    │      │ snapshot restore ~176 ms warm    │
                 │ per-task Context  │      │ artifacts → S3 before TTL        │
                 │ redirect-aware    │      │ DNS pin; no IMDS; no Docker.sock │
                 │ fetch proxy       │      │ E2B only if egress is an explicit│
                 │ CU pixels iff no  │      │   product requirement            │
                 │ a11y tree         │      │                                  │
                 └─────────┬─────────┘      └──────────────┬───────────────────┘
                           │                               │
                 ┌─────────┴───────────────────────────────┴───────────────────┐
                 │ PERSISTENCE: HAR + screenshots + notebooks on object store  │
                 │ TELEMETRY: tool_exec_ms, pool util, injection_suspected     │
                 └─────────────────────────────────────────────────────────────┘
```

**Technology choices.** Playwright MCP **snapshot** path for forms and extraction; Computer Use only when the UI has no a11y tree (canvas). Isolate per-task context; allowlist **plus** redirect-aware fetch proxy (origin lists do not stop redirects). Gemini reference: Playwright local or Browserbase. Code path: Gemini CI if Python ≤30 s and token-only billing; OpenAI CI 1–4 GB if pandas+files and **no egress**; Firecracker/Lambda MicroVMs if untrusted Linux + snapshot resume (8 h suspend on Lambda); Anthropic CE **free** when paired with `web_search`/`web_fetch` ≥20260209. Persist outputs before 20 min / 1 h / 24 h TTLs.

**Trade-off evaluation matrix.**

| Dimension | A. Screenshot Computer Use + E2B internet-on, 1 h VMs | B. Recommended: snapshot Playwright + Firecracker/OpenAI-CI no-egress + dual-LLM + redirect proxy | C. WASM-only sandbox + origin-list-only browser on the app host |
| --- | --- | --- | --- |
| Cost / 1k | Vision + 4.5–6.6k prefix; E2B 1 h **~$109**; CU p95 many LLM rounds | Snapshot increment **$0.0006**/step vs **$0.008** screenshot (~13×); E2B 5 s **~$0.15** or CI 1 GB **$30**/1k sessions | Cheap tokens; hidden host-RCE cost |
| Latency | Action 5 s + nav 60 s tails; CU coordinate mismatch after zoom | Snapshot p95 **<8 s**/step; warm sandbox **176 ms** restore | Fast start; hangs on SPA still 60 s nav |
| Ops complexity | GPU-less but VM-hour sprawl | Two pools, HITL, artifact copy | Looks simple |
| Security posture | Page IPI + click; E2B egress default; IMDS risk | Dual-LLM, SSRF re-check after redirect, Firecracker, egress deny | WASM strong, poor pandas ABI; origin lists **do not** stop redirects; cookie bleed if shared context |
| Scalability | 100 CU threads × uncached 4.5k prefix ≈ **$0.90/turn** | 100 concurrent sandboxes = E2B Pro floor **or** 100 CI sessions **$9/h** container line | App-host browser does not scale to 100 tenants |

**Decision rationale.** **B** hits the token SLO (snapshot vs screenshot), the isolation SLO (Firecracker or OpenAI no-egress vs E2B default internet), and the SSRF SLO (redirect-aware proxy). A fails cost (hour-long VMs + vision) and treats Computer Use as the default instead of the canvas fallback. C fails Linux ABI for pandas and mistakes Playwright origin lists for a security boundary. CAPTCHA/ToS go to HITL (`safety_decision` / screenshot classifiers), not to an `evaluate` bypass. On OpenAI container expiry, start a **new** container and tell the model files were lost — never reuse `previous_response_id` against a dead container.

**Interview close.** Defend a two-plane tool host: schemas and `tool_choice` in the control plane; REST/gRPC/GraphQL, Playwright contexts, and Firecracker-class sandboxes in the data plane. Quote the invariants: no execute on partial JSON; all ids in one turn; runtime-derived idempotency keys; mixed parallel groups split per provider; origin lists are not a redirect firewall; sandbox disk is not RPO=0. When they ask for numbers, use the 2026-08-21 fee table ($10/1k search, $0.15/1k E2B-5 s, $30/1k OpenAI 1 GB CI) and label every p95 that is not a vendor SLO as **[inferred]**.
