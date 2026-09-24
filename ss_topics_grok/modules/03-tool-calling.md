# Module 03 — Tool Calling

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `ss_topics_grok/research/03-tool-calling.md` (researched 2026-09-23, 97 sources). Vendor list prices, tokenizer tables, and SDK HTTP retry constants live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Prefix-stability, cache breakpoints, and assembler-level schema tax live in [`02-context-engineering.md`](02-context-engineering.md) — **do not recopy those tables**. This module is the **tool-calling control plane**: JSON Schema compilation, provider `strict` / `VALIDATED` decoding, Pydantic as the last-mile validator, semantic vs HTTP retry, MCP `tools/list` vs inlined schemas, and recovery from hallucinated names, timeouts, and partial side effects.
**Mandatory topics**: JSON schemas · Pydantic validation · retry logic · dynamic tool discovery · error recovery.

The model **does not execute your tools**. It emits a structured call; **your** dispatcher (or a hosted MCP / server-tool runtime) executes; a correlating ID carries the result back. Mixing the HTTP retry loop with the semantic reask loop is how you double-charge Stripe.

---

## What Is This?

**Tool calling** is the production architecture that **compiles** Pydantic / JSON Schema into a provider-strict subset, **allowlists** which names are callable this turn without mutating the cached `tools` prefix, **discovers** remote catalogs (MCP pagination, `defer_loading`, tool search), **validates** arguments again at execute-time (`extra="forbid"`), **executes** under timeout + an idempotency key derived from the **session** (never the model), and **echoes every call ID** as `tool_result` / `function_call_output` / `functionResponse`. Constrained decode (`strict` / `VALIDATED` / vLLM structural tags) is syntax. Authorization, identity, and money are the dispatcher.

## Why It Matters

A 20-tool Sonnet 5 copilot pays hidden **354** (`auto`/`none`) or **474** (`any`/`tool`) system tokens **plus** every JSON schema, every turn. Cache-warm that prefix and a two-call tool-using question is **[inferred] ~$19 / 1k**; miss cache and it is **~$33 / 1k**. A 20% schema-invalid rate adds **+$0.96 / 1k** — that is a **full extra model call**, not a 50 ms HTTP retry. Anthropic documents a GitHub+Slack+Sentry+Notion+Splunk catalog at **~55k** definition tokens; tool search typically cuts that **>85%**. Selection accuracy degrades past **30–50** inlined tools. Cursor’s published A/B: **−46.9%** tokens on runs that **called** an MCP tool. BFCL V4 weights hallucination/irrelevance **10%** of overall (**1,122** samples). The interview is whether you know the **two retry loops**, Stripe keys that store **500s for ≥24 h**, and that `user_id` in tool JSON is **untrusted**.

## Interview traps (fail these, fail the round)

- Sending Pydantic `model_json_schema()` verbatim into OpenAI `strict: true` (`$defs`, open objects, `Optional` omitted from `required`) → **400**.
- Nested Chat Completions tool shape on **Responses** (GPT-6 Astra **requires** Responses) → `invalid_request_error`.
- Executing on `input_json_delta.partial_json` / `function_call_arguments.delta` / Anthropic `input: {}` at `content_block_start`.
- Missing one parallel `tool_result` (Anthropic 400: `tool_use ids were not found`).
- `disable_parallel_tool_use` as a **top-level** Anthropic field — it lives **inside** `tool_choice`.
- `defer_loading: true` **and** `cache_control` on the same Anthropic tool → **400** every request.
- MCP `tools/list` **page 1 only** (AgentCore paginates at **30**/page) → tools 31+ look “hallucinated.”
- Idempotency UUID **invented by the model** (or a new key after Stripe **500**) → duplicate PaymentIntent.
- Exponential backoff on `ValidationError` (same args fail identically); semantic-retry of a **429** without sleep.
- Identity / `Authorization` / `tenant_id` taken from tool **arguments** instead of the session principal.
- `extra="ignore"` at the dispatcher: hallucinated `bcc:` on `send_email` silently dropped — or accepted.
- Token-passthrough from MCP gateway to Stripe (confused deputy; RFC 8707 audience required).
- Mixing client tools and Anthropic **server** tools in one parallel batch (`stop_reason: "tool_use"` until client results land).
- Gemini 3: omitting `thought_signature` on the first `functionCall` of a step → **400**.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns the **schema compiler**, **per-turn allowlist**, **RBAC**, MCP catalog client (all pages), correlating IDs, idempotency keys, loop budget (`max_turns`, Agents SDK default **10**), and **which tools exist this turn**. It does **not** own transformer weights, KV cache, or grammar bitmasks. Data plane (model) owns constrained decode → emit `function_call` / `tool_use` / `functionCall`. Data plane (executor) owns validate → authorize → downstream HTTP / `tools/call` → map errors. Persistence is the **idempotency store** (Stripe-style, **≥24 h**, including **500s**) plus saga/checkpoint state — **not** the prompt-cache KV. Tool proxies never take IAM from model JSON. Telemetry is the only place schema-fail rate, tool RTT, and hashed args are authoritative.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS                                                                         │
│  SSE copilot  │  REST extract  │  HITL (refund / amount)  │  Stripe webhooks    │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ TLS + session JWT + Idempotency-Key (YOUR POST) + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (your process — dispatcher, not the GPU)                         │
│                                                                                 │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Edge       │─▶│ Policy     │─▶│ Schema     │─▶│ Allowlist  │─▶│ MCP       │  │
│  │ auth, RPM  │  │ PII redact │  │ compiler   │  │ per-turn   │  │ discovery │  │
│  │ breaker per│  │ tool RBAC  │  │ Pydantic → │  │ OpenAI     │  │ tools/list│  │
│  │ (vendor,   │  │ principal  │  │ STRICT     │  │ allowed_   │  │ ALL pages │  │
│  │  model,    │  │ ≠ model    │  │ additional │  │ tools /    │  │ pin hash  │  │
│  │  tool-class│  │ JSON       │  │ Properties │  │ Gemini     │  │ list_     │  │
│  │  )         │  │            │  │ false +    │  │ allowed_   │  │ changed   │  │
│  │            │  │            │  │ all required│ │ function_  │  │ re-list   │  │
│  └────────────┘  └─────┬──────┘  └─────┬──────┘  │ names      │  └─────┬─────┘  │
│                        │               │         └─────┬──────┘        │        │
│                        ▼               ▼               ▼               ▼        │
│                 ┌──────────────────────────────────────────────────────────┐    │
│                 │ ORCHESTRATOR  Temporal Workflow = tenant:thread          │    │
│                 │  ├─ Activity: LLM (strict, max_retries=0 in SDK)         │    │
│                 │  ├─ Activity: ToolDispatcher per call_id                 │    │
│                 │  ├─ loop: tool_use → validate → execute → tool_result    │    │
│                 │  ├─ echo ALL IDs; disable parallel around money          │    │
│                 │  └─ semantic reask ≤2 (no sleep) ≠ HTTP jitter           │    │
│                 └──────────────────────────┬───────────────────────────────┤    │
│  ┌────────────┐  ┌────────────┐            │            ┌──────────────────┐    │
│  │ Idempotency│  │ Circuit    │◀───────────┘───────────▶│ Fallback compile │    │
│  │ store ≥24h │  │ breaker    │                         │ Anthropic msgs   │    │
│  │ incl. 500s │  │ per class  │                         │ vs Responses     │    │
│  │ key=hash(  │  │ payments / │                         │ vs Gemini FC     │    │
│  │ tenant,    │  │ CRM / MCP) │                         └────────┬─────────┘    │
│  │ intent_id, │  └────────────┘                                  │              │
│  │ tool, args)│                                                  │              │
│  └────────────┘                                                  │              │
└──────────────────────────────────────────────────────────────────┼──────────────┘
                                                                   │
          ┌────────────────────────────────┬───────────────────────┘
          │ chat / agent SSE, REST         │
          ▼                                ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ DATA PLANE  GENERATION          │  │ DATA PLANE  EXECUTOR (your workers)        │
│ (provider-owned on hosted APIs) │  │ model NEVER holds IAM or Stripe sk         │
│                                 │  │                                            │
│  Tokenizer → Prefill → Decode   │  │  complete JSON only (no partial_json exec) │
│  + grammar bitmask (strict /    │  │  Pydantic extra=forbid → RBAC → timeout    │
│    VALIDATED / vLLM FSM)        │  │  bulkhead Semaphore per downstream class   │
│  Parser: function_call |        │  │                                            │
│    tool_use | functionCall      │  │  Hosted/server tools invert this box:      │
│  stop_reason=tool_use /         │  │  OpenAI built-ins, Anthropic web_search,   │
│    output items with call_id    │  │  Gemini Search run INSIDE the provider     │
└────────────┬────────────────────┘  └─────────────────────┬──────────────────────┘
             │                                             │
             │  structured call (untrusted planner)        │ side effects
             ▼                                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ TOOL PROXIES  (MCP / adapters)  │  │ PERSISTENCE LAYER                          │
│ Zero-Trust: RFC 8707 resource   │  │                                            │
│ indicator; NO token passthrough │  │  ┌──────────────────┐  ┌─────────────────┐ │
│ identity = session JWT / Temporal│ │  │ App state        │  │ Idempotency     │ │
│ info — never model JSON         │  │  │ Postgres:        │  │ store (Redis or │ │
│  ┌──────────┐  ┌─────────────┐  │  │  │  saga_state,     │  │  Stripe's)      │ │
│  │ Stripe   │  │ CRM / MCP   │  │  │  │  thread, HITL    │  │  status+body of │ │
│  │ adapter  │  │ tools/call  │──┼──│  │  Temporal hist.  │  │  FIRST request  │ │
│  │ same key │  │ cap limit   │  │  │  │  outbox events   │  │  including 500s │ │
│  │ after 500│  │ SSRF filter │  │  │  └──────────────────┘  └─────────────────┘ │
│  └──────────┘  └─────────────┘  │  │  ┌──────────────────┐  ┌─────────────────┐ │
│  mutating tools sequential      │  │  │ MCP catalog      │  │ Soft caches     │ │
└─────────────────────────────────┘  │  │ Redis: name,     │  │ prompt-cache KV │ │
                                     │  │ hash(desc+schema)│  │ grammar ≤24h    │ │
                                     │  │ server, scopes   │  │ (Anthropic      │ │
                                     │  └──────────────────┘  │  strict tools)  │ │
                                     │                        └─────────────────┘ │
                                     └──────────────────────┬─────────────────────┘
                                                            │
┌───────────────────────────────────────────────────────────┴─────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics      │  │ Traces       │  │ Usage (authoritative │ │
│  │ cid, tenant  │  │ tool RTT     │  │ gateway→LLM  │  │ on terminal event)   │ │
│  │ SHA-256 args │  │ p50/p95/p99  │  │ →validate→   │  │ input, cache_read,   │ │
│  │ (not PAN),   │  │ schema-fail  │  │ execute→     │  │ cache_write, output, │ │
│  │ call_id,     │  │ %, poison    │  │ Stripe/CRM   │  │ thinking_tokens      │ │
│  │ policy,      │  │ fingerprint, │  │              │  │                      │ │
│  │ idempotency  │  │ breaker,     │  │              │  │                      │ │
│  │ key, MCP     │  │ sem wait,    │  │              │  │                      │ │
│  │ server hash  │  │ pages listed │  │              │  │                      │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control** | Compiler, allowlist, RBAC, Temporal loop, idempotency **key minting**, MCP pagination | Provider 529 becomes a second Stripe POST with a new key |
| **Generation data** | Prefill + constrained decode + parser | Executor runs on `partial_json` |
| **Executor data** | Validate, ACL, HTTP, `tools/call` | Model JSON as IAM → confused deputy |
| **Tool proxies** | Stripe/CRM/MCP with audience-bound tokens | Token passthrough to upstream |
| **Persistence** | Idempotency store + saga + catalog hashes | Restart = duplicate charge; `list_changed` unseen = rug pull |
| **Telemetry** | Hashed args, `call_id`, usage | Finance dashboards that ignore schema-retry tokens |

Hosted / **server tools** invert the executor: OpenAI built-ins (web search, file search, code interpreter, hosted MCP), Anthropic `web_search` / `web_fetch` / `code_execution` / `tool_search`, Gemini Search / Code Execution run **inside** the provider. Mixed parallel groups are a topology hazard: Anthropic may return `stop_reason: "tool_use"` if a server tool sits in the same batch as a client tool — complete client `tool_result`s before the server path continues. OpenAI GPT-5+: custom functions may parallelize, but **built-ins cannot share a parallel function-call batch**. Gemini 3 mixes built-in + custom via `previous_interaction_id`.

### 1.2 End-to-end request flow

1. **Ingress.** SSE (interactive agent) or REST (single-shot extract). Gateway stamps `correlation_id`, checks tenant quota, consults the **per-(vendor, model)** breaker **and** the **per-tool-class** breaker (payments ≠ CRM). Stripe/CRM RPM is a **second** token bucket — OpenAI TPM left does not mean Stripe will accept 400 parallel `PaymentIntent`s.
2. **Policy + identity.** Detect → redact PII **before** args are logged or cached. Bind principal from **session JWT / Temporal memo / MCP access token**. Tool RBAC maps `(principal, tenant, tool, args_shape)` → allow / deny / HITL. Per-turn **allowlists** (`tool_choice.allowed_tools`, Gemini `allowed_function_names`, Anthropic non-deferred set) are **capability reduction**, not authz — a prompt-injected model can still call every tool you left callable.
3. **Schema compile (or cache hit).** `Args.model_json_schema()` / `TypeAdapter.json_schema()` → provider-subset compiler: every object `additionalProperties: false`, every key in `required`, `Optional[T]` as `["T","null"]`, strip `allOf` / `not` / `if`/`then`/`else`. Gemini adapters also strip `$schema`, `additionalProperties`, `$defs` (OpenAPI-subset). Hash the compiled blob; Anthropic strict grammars cache **≤24 h since last use**. Illegal schema → **400**, not best-effort JSON.
4. **Allowlist without prefix mutation.** OpenAI `tool_choice.type: "allowed_tools"` + `mode: "auto"` subsets **names** without rewriting the cached `tools` array (the cache-preserving control). Do **not** reorder or hot-reload the full `tools[]` every request — that is a 02 prefix miss.
5. **MCP discovery (if remote).** After initialize: `tools/list` with **cursor pagination until `nextCursor` is absent**. Persist `{name, hash(description+schema), server, scopes}`. On `notifications/tools/list_changed`, **re-list all pages**. Last-good catalog with TTL beats “inline 55k tokens as fallback.” Capability `tools.listChanged: true` ⇒ server SHOULD notify; clients that fetch **page 1 only** (Claude Code + AgentCore **30**/page) report `No such tool available` for tools 31+.
6. **Dispatch model (control → generation data).** One process-wide async client per vendor; `max_retries=0` if Temporal owns HTTP. `strict: true` (OpenAI Chat default **off**; Responses omit → **try** strict). Anthropic `strict: true` guarantees **name ∈ provided tools**; computer/browser toolset entries **reject** `strict` (400). Gemini `FunctionCallingConfig.mode`: `AUTO` / `VALIDATED` (schema + Gemini 3 required params) / `ANY` + optional `allowed_function_names` / `NONE`. Stream: concatenate until `content_block_stop` / complete `function_call_arguments` — **do not execute early**.
7. **Parse + ID contract.** OpenAI: inject `function_call_output` matching `call_id` (Responses) or Chat `role: "tool"` + `tool_call_id`. Anthropic: **one** user message with **all** `tool_result` blocks **first** (`tool_use_id` match), then optional text; echo the assistant message **verbatim** (thinking signatures). Gemini **Interactions**: `function_result.call_id` **must** match `steps[].id`. Gemini **generateContent**: `functionCall.id` often `None` — match by **name**; if `id` is populated, echo it. Gemini 3: `thought_signature` on the **first** `functionCall` of each step (parallel: first part only). Omit → **400**.
8. **Execute-gate (executor data).** For each completed call: unknown name → **model-visible** allowlist error (host maps JSON-RPC **−32602** / SEP-2140 `isError` so the **model** can recover). `Args.model_validate_json(raw)` with `extra="forbid"` **before any side effect**. RBAC re-checks resource ids the model named. Mint idempotency key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` or Temporal `workflowRunId + activityId` — **never** from model-emitted UUID.
9. **Downstream + completeness.** `asyncio.gather` with **per-call** try/except for independent **reads**; always return **N** results (skipped siblings get `is_error: true`). Mutating tools: `parallel_tool_calls: false` / `disable_parallel_tool_use: true` **inside** Anthropic `tool_choice`, or sequential computer-use members (stop at first failure, skip rest with documented skip text). Nested timeout: **LLM request timeout > tool Activity `StartToClose` > HTTP client > downstream SLA**. Invert this and you retry-amplify.
10. **Semantic vs HTTP recovery.** `ValidationError` → `tool_result` containing `e.json()`; **no** sleep; cap **2–3** reasks then fail closed. Transient Stripe/CRM 5xx → adapter HTTP backoff **only if** the tool is retryable **and** the **same** idempotency key is reused. After Stripe 500, **do not** mint a new key (the original may have had side effects; replay flagged `Idempotent-Replayed: true`).
11. **Saga / webhook.** Charge succeeds, CRM 503s: return **both** `tool_result`s; persist `saga_state`; a **separate** Workflow (or human) compensates — do **not** auto-refund from the model loop. Kafka: Stripe webhooks **in** → **Signal** the Workflow; never a Kafka client inside Workflow code. Outbox: intent + key **before** side effect.
12. **Emit + audit.** Terminal usage is the invoice (schema-retry tokens included). WORM: `tool_name`, `call_id`/`tool_use_id`, principal, **hashed** args, policy decision, downstream status, latency, idempotency key, MCP server hash. Provider traces are not a SIEM.

**Interview talking point:** “The model is an untrusted planner. The schema compiler and the allowlist live on the control plane; Stripe sees a key I derived from the session. Two retry loops: jitter for wires, reask for ValidationError.”

---

## 2. Core Mechanics & Algorithms

### 2.1 JSON Schema: Drafts vs the strict subset

JSON Schema **Draft-07** and **2020-12** both treat `additionalProperties` as an applicator over names **not** matched by sibling `properties` / `patternProperties`. Default is **open**. `additionalProperties: false` forbids extras; it does **not** make declared keys present — that is `required`. Draft 2020-12 adds `unevaluatedProperties` (sees into `allOf` / `$ref`); `items`/`additionalItems` become `prefixItems`/`items`.

**MCP `inputSchema`** defaults to JSON Schema **2020-12** when `$schema` is omitted; it **MUST** be a JSON Schema object (not `null`). Empty-arg tools: `{ "type": "object", "additionalProperties": false }` is the closed empty object; `{ "type": "object" }` accepts any object.

**OpenAI / Anthropic `strict: true` is a subset, not Draft-07.** Both require: (1) every object `additionalProperties: false`; (2) every key in `properties` listed in `required`; (3) optionality via `type: ["string","null"]` (or union with `null`), **not** by omitting from `required`. Unsupported keywords → **400**. OpenAI documented limits (2026): up to **5,000** object properties total, **10** nesting levels (raised from 100 / 5). Unsupported composition includes `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`/`then`/`else`. Fine-tunes additionally drop `minLength`/`maxLength`/`pattern`/`format`, numeric bounds, `patternProperties`, `minItems`/`maxItems`.

> ⚠️ Limited public data available for **which Azure Foundry copy you ship against** — some pages still cite **100 properties / 5 nesting**. Quote the page.

**Gemini** `functionDeclarations[].parameters` is an **OpenAPI-subset**. Production MCP adapters strip `$schema`, `additionalProperties`, `$defs` or the request 400s.

**Invariant S1.** Grammar ⊆ syntax. `{"amount": -1}` can be schema-valid. Business constraints (`gt=0`, `EmailStr`, tenant-scoped IDs) live in **Pydantic at execute-time**.

### 2.2 Pydantic v2 as last-mile validator

Pipeline:

1. **Compile:** `Args.model_json_schema()` or `TypeAdapter(T).json_schema(mode="validation")` → provider-subset compiler → `tools[]`.
2. **Execute-gate:** `Args.model_validate_json(raw)` **before** any side effect.
3. **Closed objects:** `model_config = ConfigDict(extra="forbid")` → error type `extra_forbidden`. Default `extra="ignore"` **silently drops** hallucinated keys — dangerous for audit.
4. **Retry context:** `ValidationError.errors()` → `{type, loc, msg, input, ctx?, url}`; `e.json()` is the compact payload to the model. Malformed JSON → `json_invalid`.

`TypeAdapter` covers dataclasses, typed dicts, unions without a `BaseModel`. `defer_build=True` (v2.10+) postpones core-schema compile until first validate — useful for large catalogs. Callable/`@validate_call` JSON validation is historically limited (`ArgsKwargs` from Python, not `validate_json` on a flat object) — **wrap tool args in a BaseModel**. OpenAI Python `type_to_text_format_param` always sets `"strict": True` when converting a Pydantic type (see 01).

### 2.3 Parallel vs sequential; ID echo

| Provider | Parallel default | Disable | Completeness rule |
| --- | --- | --- | --- |
| OpenAI | on; `parallel_tool_calls: false` ⇒ **exactly 0 or 1** call | top-level `parallel_tool_calls` | match every `call_id`; GPT-5+ custom // built-ins **not** in one batch |
| Anthropic | parallel `tool_use` blocks | `tool_choice.disable_parallel_tool_use: true` | all `tool_result` **first**; skip remainder of computer/browser member batches on first failure |
| Gemini | parallel `functionCall` parts | constrain with `ANY` + names / sequential steps | Interactions: echo `call_id`; generateContent: name and optional `id`; thought signature on **first** part |

Fine-tuned OpenAI: **parallel calls disable strict for that turn**. Snapshot `gpt-4.1-nano-2025-04-14` can emit **duplicate same-tool** calls if parallel is on. Two parallel `charge` calls with **two** keys = two charges — keys are **per intent**, not per sample.

`defer_loading` reveal spliced **between** a sibling `tool_use` and its `tool_result` is a documented 400 (pydantic-ai #7878). Computer/browser-use: **in order**.

### 2.4 Two retry loops (never one)

| Loop | Trigger | What you send the model | Backoff | Cap |
| --- | --- | --- | --- | --- |
| **HTTP / transport** | 408/429/5xx, connect reset | Nothing (no new tokens) | Exponential + **full jitter**; OpenAI SDK `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2` (01) | Honor `Retry-After` iff \(0 < t \leq 60\); Anthropic **529** = failover, don’t spin |
| **Schema-invalid args** | Pydantic `ValidationError` / JSON-RPC `-32602` invalid args | **Semantic retry:** `tool_result` / Instructor reask with `e.json()` | **No** sleep | **2–3** reasks then fail closed |
| **Tool runtime** | Timeout, CRM 5xx, `isError: true` | Error payload as tool result (`is_error: true`) | HTTP backoff **inside** the adapter iff idempotent | Adapter retries ≠ model retries |

Instructor `client.create(..., max_retries=2)` = **1 extraction + 2 validation retries** (3 SDK calls), separate from `OpenAI(max_retries=2)` transport. Exhaustion → `InstructorRetryException`. Mode.TOOLS reasks via a **tool** message. LangGraph `ToolNode.handle_tool_errors` default catches **invocation/validation** errors but **re-raises ordinary execution failures**. OpenAI Agents: `@function_tool` `failure_error_function` turns exceptions into model-visible output; `tool_not_found_behavior="return_error_to_model"` vs default raise `ModelBehaviorError`; `DEFAULT_MAX_TURNS=10`.

**Rule:** never exponential-backoff a `ValidationError`. Never semantic-retry a 429 without sleeping. Disable nested SDK retries inside Temporal Activities (`max_retries=0`) so **one** owner retries.

### 2.5 Dynamic discovery: MCP pagination and `defer_loading`

| Strategy | What the model sees every turn | Who expands |
| --- | --- | --- |
| Inline all schemas | Full JSON Schema × N + hidden tool-use prompt (354/474) | You |
| Names + disk (Cursor) | Tool **names**; schemas as files the agent `read`s | Agent |
| Anthropic `defer_loading` + tool search | Search tool + **3–5** hot tools; rest via `tool_reference` | Anthropic API |
| OpenAI `tool_search` + namespaces (`gpt-5.4`+) | Namespace blurb; schemas at **end of window** on hit (`defer_loading: true`) | OpenAI or your `tool_search_output` |
| Per-turn `allowed_tools` | Full schema list (**cached**) but only a subset **callable** | OpenAI |

MCP JSON-RPC 2.0: `tools/list` → `ListToolsResult.tools[]` + optional `nextCursor`; `tools/call` `{name, arguments}` → `content[]`, optional `structuredContent`, optional `isError`. Error split: 2025-06-18 unknown tool / invalid args = **−32602**; execution failure = `isError: true`. SEP-2140 wants unknown tools as `isError` so the **model** can recover. Interview answer: **map protocol −32602 unknown-name to a model-visible “no such tool; here is the allowlist” in the host**, regardless of wire encoding.

Anthropic tool search: you still **send every definition** every request; `defer_loading` controls the **system-prompt prefix**. At least one tool (the search tool) stays non-deferred. **Never** `cache_control` on a deferred tool. Grammar for strict mode builds from the **full** toolset — deferral does **not** recompile grammars. OpenAI namespaces: keep **<10** functions per namespace; prefer namespaces/MCP over per-function deferral. Loaded tools append at the **end of the window** so the prefix cache survives.

**Invariant D1.** Pagination bugs present as hallucinated names. On `tools/call` unknown: **re-list all pages once**, then fail with allowlist — do not inline 55k tokens.

### 2.6 State machines

**HTTP retry (control plane — wires, not tokens):**

```
                    Retry-After ∈ (0, 60s]              attempts exhausted
  ┌──────────┐  HTTP 408/409/429/5xx/529   ┌─────────┐  ─────────────────▶ FAIL
  │  SEND    │ ──────────────────────────▶ │  WAIT   │
  └────┬─────┘  400 schema / 401 / 403     │ jitter  │
       │        spend-cap 429 (no RA)      │ cap 8s  │
       │        ─────────────────────────▶ FAIL      │
       │ success                           └────┬────┘
       ▼                                        │
     DONE ◀─────────────────────────────────────┘  retry SEND
```

**Tool-use loop (generation → executor → generation):**

```
  MODEL ──▶ end_turn / stop_sequence ──▶ DONE
         ──▶ max_tokens mid-tool_use ──▶ FAIL CLOSED (do NOT exec partial JSON)
         ──▶ refusal ──▶ FAIL CLOSED
         ──▶ pause_turn ──▶ resend assistant content (server-tool cap)
         ──▶ tool_use / function_call[]
                │
                ▼
         ┌──────────────┐  unknown name     ┌─────────────────────────────┐
         │ PARSE complete│ ───────────────▶ │ tool_result is_error +      │
         │ JSON (not     │                  │ allowlist (model-visible)   │
         │ partial_json) │                  └──────────────┬──────────────┘
         └──────┬───────┘                                 │
                │ known                                   │
                ▼                                         │
         ┌──────────────┐  ValidationError   ┌────────────┴─────────────┐
         │ PYDANTIC     │ ─────────────────▶ │ SEMANTIC REASK (no sleep)│
         │ extra=forbid │                    │ e.json() as tool_result  │
         └──────┬───────┘                    │ N≥3 identical fingerprint│
                │ valid                      │ ──▶ poison → HITL / DLQ  │
                ▼                            └────────────┬─────────────┘
         ┌──────────────┐  deny                          │
         │ RBAC + HITL  │ ──▶ is_error / pause           │
         └──────┬───────┘                                │
                │ allow                                  │
                ▼                                        │
         ┌──────────────┐  timeout / 5xx (idempotent)    │
         │ EXECUTE      │ ── HTTP jitter, SAME key ──┐   │
         │ key=hash(    │                            │   │
         │  session,    │  mutating + parallel ── SEQ│   │
         │  intent,     │                            │   │
         │  tool, args) │                            │   │
         └──────┬───────┘                            │   │
                │ all IDs                            │   │
                ▼                                    ▼   ▼
         NEXT MODEL TURN  (tool_result / function_call_output / functionResponse)
         echo thinking / thought_signature verbatim
```

**MCP catalog:**

```
  CONNECT ──▶ tools/list page ──▶ nextCursor? ──yes──▶ next page
                    │                    no
                    ▼
              PIN hashes in Redis ──▶ notifications/tools/list_changed ──▶ re-list ALL
                    │
                    ▼
              tools/call ── unknown ──▶ re-list once ──▶ allowlist is_error
```

`pause_turn` treated as `end_turn` **drops server-tool results**.

### 2.7 Schema compile / cache complexity

Walk the JSON Schema tree (`properties`, `items`/`prefixItems`, `$defs`): **Θ(N)** in nodes. Canonical `json.dumps(..., sort_keys=True, separators=(",",":"))` then SHA-256 is **Θ(B)** in serialized bytes. Provider grammar compile (Anthropic strict, OpenAI strict, vLLM named/`required` structured outputs): **first** named-function FSM is documented as **“several seconds”** then cached; Anthropic tool schemas cached **24 h since last use**. Subsequent turns with a frozen `tools[]` prefix are **O(1)** cache-key lookup on the control plane plus KV prefix reuse on the data plane (02). `auto` on vLLM constrains only if **at least one** tool has `strict: true` **and** `VLLM_ENFORCE_STRICT_TOOL_CALLING=true` (default); else raw-text extract — **always Pydantic-validate after the parser**. Hermes historically **drops** a call if a literal `</tool_call>` appears inside a JSON string.

Dispatcher per turn: **O(T)** tools. Parallel independent reads: wall-clock ≈ **max(tool RTT)**, not the sum. Sequential computer-use: **N × action RTT**. Catalog list: **O(P × page_size)**; you must exhaust the cursor.

### 2.8 Invariants worth stating in an interview

1. Hosted APIs never execute customer tools (server tools are the exception — still not **your** Stripe key).
2. Do not execute on partial JSON / placeholder `input: {}`.
3. Echo **every** ID in one turn; Anthropic results **first**.
4. Compile Pydantic → subset; validate **again** at execute-time with `extra="forbid"`.
5. Semantic retry ≤2 with `ValidationError.json()`; HTTP jitter is a different loop.
6. Idempotency key **not** from the model; Temporal Activity per LLM call **and** per tool.
7. Parallel: return every ID; disable parallel around money.
8. Discover via MCP **all pages** + tool search; never inline 55k tokens as the happy path.
9. Identity from the session principal; model may pass a **resource id** the ACL re-checks.
10. Poison-pill schemas (always-invalid) need a **loop detector** on `(tool, error_type)` — Instructor will not stop for you.

---

## 3. Token Economics & NFR Analysis

List prices and cache multipliers: **see 01**. Assembler-level schema tax: **see 02**. Below is **tool-loop** spend. Formula (same as 01; \(T_{\mathrm{out}}\) includes thinking):

\[
C = n \cdot \frac{T_{\mathrm{miss}} P_{\mathrm{miss}} + T_{\mathrm{hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}}{10^{6}}
\]

### 3.1 Cost per 1k **tool-using** questions

Every Anthropic request with `tools` injects a hidden tool-use system prompt **plus** your JSON schemas. Sonnet 5: **354** tokens (`auto`/`none`) or **474** (`any`/`tool`) **on top of** names/descriptions/schemas, even with one empty tool. `computer_toolset_20260801` default members ≈ **4,590** input tokens; `browser_toolset_20260801` ≈ **6,670** (02). OpenAI: callable function definitions count against the context limit and are billed as input.

02 worked stand-in (do not re-derive prices): Sonnet 5, 20-tool agent, ~2,000 schema + 354 hidden + 1,500 system + 500 user, 800 output, no cache → **$0.01671 / model-turn** → **$16.71 / 1k model-turns**; 5m cache warm → **$0.00957 / model-turn** → **$9.57 / 1k**.

Define a **tool-using user question** as: **1 planning model-call** (emits tools) + **1 execution of 2 parallel REST tools** + **1 synthesis model-call**. **No thinking.** Sonnet 5 5m cache on the stable prefix (tools+system). Stripe/CRM **tool fee** is $0 at the LLM meter; you still pay **their** RPM and **your** result tokens every later turn (02 tool-result clearing at 100k, keep last 3).

| Shape | Model-calls / question | **[inferred] $ / 1k questions** |
| --- | --- | --- |
| Happy path, cache warm | 2 | 2 × $0.00957 × 1000 ≈ **$19.14** |
| + 20% one-shot schema retry | 2.2 | **$21.06** |
| Uncached (02 $0.01671 / model-turn) | 2 | **$33.42** |
| Computer-use prefix 4,590 + 354 hidden, uncached, 800 out, ignore screenshots | 2 | input 4,944 × $2 / 1e6 + 800 × $10 / 1e6 = $0.01789 / call → **$35.78** |
| Same computer-use, cache hit @ 0.1× input | 2 | 4,944 × $0.20 / 1e6 + $0.008 out = $0.00899 / call → **$17.98** |
| Cursor −46.9% on the **schema portion** of an MCP-heavy run | n/a | apply −46.9% to **agent tokens on MCP-calling traces**, not to list-price $/1k questions |

**[inferred] schema-invalid retry increment** (cached 20-tool prefix $0.00957, +400 uncached error tokens + 400 extra output): extra input 400 × $2 / 1e6 = **$0.00080**; extra output 400 × $10 / 1e6 = **$0.00400**; increment ≈ **$0.0048** (~**50%** of a warm turn). If **20%** of tool-using questions hit one schema retry: **+$0.96 / 1k questions**. A poison-pill schema at Instructor `max_retries=2` is **3×** model calls per question with **zero** successful tool I/O.

HTTP retries (`DEFAULT_MAX_RETRIES=2`) do **not** add LLM tokens if they fail before the stream; they **do** duplicate billed tokens if you replay after streaming started (01).

Anthropic tool search: a typical **~55k** definition catalog typically cuts **>85%**, loading 3–5 tools. That is a **prefix** win, not a license to skip Pydantic.

### 3.2 Latency SLA targets

> ⚠️ Limited public data available for **vendor p50/p95 of “tool dispatcher”** — no provider publishes that SLO. Bounds below are documented ceilings plus **[inferred] policy** for *your* SLO doc.

| Stage | Published / documented | Kind |
| --- | --- | --- |
| OpenAI SDK timeout | **600 s** request, **5 s** connect (01) | Ceiling, not SLO (footgun: 600×3 ≈ **30 min**) |
| Anthropic `input_json_delta` | concatenate until `content_block_stop` | Do not execute early |
| vLLM first named-function FSM | “several seconds” then cached | Cold schema |
| Anthropic grammar cache | **24 h** since last use | Cold vs warm strict |
| Temporal Activity `StartToClose` | cookbook examples **30 s** for LLM | Your SLO |
| Independent TTFT (02) | miss ~1.0–1.7 s at 1.5k–5k prefix | RTT-dominated |

**[inferred] policy targets** (not vendor guarantees):

| Metric | Target | Mitigation |
| --- | --- | --- |
| **p50** wall (tool-using turn) | **< 3 s** | Stream the planning call; `asyncio.gather` independent **reads**; REST tool p95 **<800 ms** including auth; cache the tool prefix |
| **p95** wall | **< 8 s** | Model-call p95 **1–8 s** (thinking / Fast-mode tok/s in 01); sequential computer-use is **N × RTT** — do not promise 8 s for N=20 UI actions |
| **p99** hang | Fail closed on idle gap / Activity timeout; no 10-min non-stream | Nested timeouts correctly ordered; inter-event watchdog; **one** retry owner; circuit-break identical GET pagination; `tool_choice: "none"` on the closing turn |

Parallel independent tools make wall-clock ≈ **slowest tool + model**, not the sum. Destructive + read in one batch: disable parallel or partition with `allowed_tools` so `refund` cannot share a turn with `charge`.

### 3.3 Throughput and back-pressure

LLM RPM/ITPM/OTPM: **see 01**. Tool backends are a **second** bucket:

| Backend | Public figure | Implication |
| --- | --- | --- |
| Stripe | Test-mode and live **request** limits are **dashboard-specific**; keys expire **≥24 h** | Agent fan-out of `create PaymentIntent` will 429 **Stripe** while OpenAI still has TPM left |
| MCP `tools/list` | Paginated; AgentCore example **30 tools/page** | Missing page 2 looks like “hallucinated tool” |
| OpenAI prompt cache routing | **>~15 req/min per `prompt_cache_key`** overflows routing (02) | Partition by tenant |
| vLLM | `--max-num-seqs` (example guides use **4** on a 70B) | Tool-call JSON decode shares the same seq slots |

> ⚠️ Limited public data available for **your** Stripe live-mode request ceiling (dashboard-specific, not a global published RPM).

**[inferred] bulkhead:** 100 concurrent agents × 4 parallel tools = **400** in-flight HTTP. Size pools to **that**, not to LLM RPM. One `Semaphore` per **downstream class** (payments, CRM read, MCP, sandbox). A 100-agent × 2 model-calls × Sonnet 5 Start **1,000 RPM** is usually fine; **100 × Stripe POSTs** is the real ceiling.

**Back-pressure design:**

1. Admit iff LLM breaker ∈ {closed, half-open} **and** tool-class breaker **and** both semaphores have room.
2. 429 + `Retry-After` on **Stripe** → sleep that bulkhead; do not steal the CRM pool.
3. Shed: disable parallel writes first; then HITL the mutating tool; then deterministic “cannot complete payment” JSON. Do not infinite-retry `get_job` every turn (poll-instead-of-webhook).
4. Agent fleets: budget \(N_{\mathrm{rounds}} \times (\mathrm{TTFT} + T_{\mathrm{out}}/\mathrm{TPOT} + T_{\mathrm{tool}})\). Cap rounds (`DEFAULT_MAX_TURNS=10`). Loop detector on `(tool, args_hash)`.

### 3.4 Non-functional requirements

| NFR | Working target | Tension |
| --- | --- | --- |
| **Availability** | 99.9% **gateway** (control plane). Tool-class bulkheads so Stripe 429 does not 500 the copilot **reads**. Multi-vendor LLM fallback for 503/529 | Output-distribution drift; schema mapping cost; **never** failover a 400 schema |
| **RPO** | Idempotency store + saga + `batch_id`: **0** for irreversible tools (checkpoint **before** execute). Prompt-cache KV / grammar cache: **minutes–24 h**, best-effort | Treating KV as RPO=0 over-provisions nothing you control |
| **RTO** | Interactive: fail over LLM **< 1 s** to secondary model (breaker already open). In-flight PaymentIntent: retry **same** key, not a new workflow | Fast failover vs identical tokens (T>0); Stripe replay vs “create again” |
| **Consistency** | Tool side effects: **exactly-once via downstream idempotency**. Temporal Activities are **at-least-once**. Model text: at-least-once retry **changes tokens** | Cannot have bit-identical retry on T>0; exactly-once is a **lie** without the store |
| **Compliance** | Regional +10% / Anthropic 1.1× US geo (01); ZDR **excludes** batches/files; tool args/results re-enter the window **and** the prompt cache — redact before inject; Stripe: **do not** use emails as idempotency keys | Residency vs latency vs DLP on cached tool results |
| **Cost vs latency** | Tool-using Sonnet 5 **[inferred] $19.14/1k** cached vs **$33.42** uncached vs **+$0.96** schema-fail tax; computer-use uncached **$35.78/1k** | Paying computer-use prefix for a CRM lookup |
| **Cache vs tenancy** | Left-prefix tools/system; `allowed_tools` to subset without mutation. `list_changed` / reconnect can bust the tools prefix | Hit rate vs rug-pull freshness |

OpenAI compact items are encrypted (better ZDR, worse DLP on tool dumps). Schema **enums** leak internal IDs and unreleased flags (02); deferral reduces both tokens and leakage.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution (Temporal / Kafka)

Application state ≠ KV cache. Every **LLM call** and every **tool I/O** is a Temporal **Activity**. The Workflow is the deterministic loop (stop_reason / output items). Workflows that call HTTP directly break replay. Disable the OpenAI SDK’s nested retries (`max_retries=0`) so **one** owner retries.

- **Replay:** Activity returns a structured `ModelTurn` / `ToolTurn` already sampled — never “call the model again” inside a replay-unsafe closure. Non-determinism (`uuid4`, `time.time`, temperature) belongs **inside** the Activity.
- **At-least-once vs at-most-once:** Activities are **at-least-once** by default (unlimited retries). `maximumAttempts=1` on a non-local Activity is **at-most-once** (zero times possible). **Exactly-once** requires the **downstream** idempotency store, not Temporal. `workflowRunId + activityId` is stable across Activity retries — that is a legal key material, unlike a model-invented UUID.
- **Distributed locking:** `workflow-id = tenant:thread_id` so two gateways cannot run the same agent loop. Tool activities lock on `idempotency_key`.
- **Checkpointing:** persist `call_id` / `tool_use_id` + saga_state **before** ack to the user on mutating tools. Workflow history is the audit log of the loop; Redis is the **hot** partial-token buffer (01).
- **Dead-letter:** after `max_attempts` on transient failure, or immediately on poison (identical `(tool, error_type)` N times; truncated tool JSON), route to a DLQ / HITL. Do not infinite-retry irreversible tools.

**Kafka / outbox:**

- Topics: `tool.intents` (intent + idempotency key **before** side effect — outbox), `tool.results`, `stripe.webhooks` → **Signal** the Workflow, `tool.dlq`.
- Compaction on `intent_id` keeps a snapshot; the full log is chain-of-custody.
- Poison messages: skip + alert after N handler crashes; do not block the partition.
- LangGraph checkpointers persist **messages**, not a lock around POST.

> ⚠️ Gap: the research file has no Temporal worker-versioning runbooks or measured replay cost for multi-MB tool traces. Map the **equivalent** pattern onto documented failure modes (Stripe 500 replay, MCP page-2 drop, nested retry amplification).

### 4.2 Idempotency keys (exactly-once is a lie)

RFC 9110: GET/PUT/DELETE are idempotent; POST/PATCH are not. Stripe: client sends `Idempotency-Key` (V4 UUID, **≤255** chars, **no PII**); server stores **status + body of the first request, including 500s**; subsequent same key **replays** that result; keys pruned after **≥24 hours** then reuse = **new** request; parameter mismatch → error; replay flagged `Idempotent-Replayed: true`. After a 500, Stripe advises **against** a new key — the original may have had side effects.

IETF `Idempotency-Key` header draft-07 (expired **2026-04-18**): **409** while in-flight, **422** fingerprint mismatch.

**Agent rule:** derive the key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` **or** from the provider `call_id` / `tool_use_id` **only if** the runtime guarantees **at-most-once delivery of that ID to the adapter**. **Do not let the model invent the key.** Timeout mid-POST: the PaymentIntent **might** exist — retry **same** key; if Stripe returns the object, treat as success.

**Compensating action (saga):** parallel `charge` + `create_crm_note`. Charge succeeds, note 503s. Return both results. Do **not** auto-refund from the model loop. Persist `saga_state`. OpenAI Agents `ProgrammaticToolCallingTool`: SDK **disables provider-managed retries** because replay may not be safe.

### 4.3 Failure taxonomy, poison pills, circuit breaker, fallbacks

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408, 409, 429 with Retry-After, 500, 503, 529, TLS reset, MCP disconnect | Full jitter; same idempotency key; cache last-good MCP catalog |
| **Permanent** | 400 schema (unsupported keywords, `defer_loading`+`cache_control`), 401/403, 404, 413, spend-cap 429, `refusal` | Fail the turn; **do not** failover schema 400s (will fail everywhere) |
| **Poison pill** | Always-invalid schema (OpenAI fine-tune dropped `format: date` but Pydantic `date` remains; `enum` of 1,001 values above the **1,000** cap → 400 on the **request**; MCP `inputSchema: null`; vLLM Hermes cut on `</tool_call>`); identical `(tool, error_type)` | After **N** identical failures, stop reasking; HITL / DLQ; Instructor/LangGraph will not do this unless you add a loop detector |
| **Semantic** | Schema-valid unauthorized refund; injection in `tool_result`; hallucinated `order_id: "12345"` | RBAC + canonical ID lookup; not a retry |
| **Partial side effect** | Timeout after Stripe accepted POST; one of N parallel IDs missing | Same key; always return N `tool_result`s |
| **Hallucinated name** | `create_charge` vs `create_payment_intent` | Model-visible allowlist; Anthropic `strict: true` guarantees name ∈ tools; Gemini `ANY` + `allowed_function_names` |

**Circuit breaker** (Resilience4j pattern; Hystrix is maintenance-mode). **One breaker per (provider, model)** for LLM **and** **one per tool-class** (payments / CRM / MCP). Never one global breaker that kills failover. Open on high **5xx/529/timeout** rate. **Do not** open solely on 429 unless 429s persist with **no** Retry-After (spend cap). Half-open: probe with a **cheap read** (Haiku / `lookup_account`), not `create_payment_intent`.

```
           5xx/529/timeout rate ≥ threshold           probe success
  ┌────────┐  ──────────────────────────────────▶  ┌──────┐  ──────▶ CLOSED
  │ CLOSED │                                       │ OPEN │
  └───┬────┘  429 with Retry-After = throttle      └──┬───┘
      │       (stay CLOSED; sleep)                    │ timer (e.g. 30 s)
      │ success resets window                         ▼
      │                                          ┌──────────┐
      └──────────────────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                                 │ 1 cheap  │
                                                 │ read     │
                                                 └──────────┘
```

**Fallback chain:** primary model (Sonnet 5 / GPT-5.4 strict tools) → secondary vendor (same compiled IR) → **deterministic** schema-valid decline (`status: "degraded"`, no charge). Tool-class open: **do not** invent a second processor; queue / HITL. PermanentError on schema **does not** failover.

**Retry amplification:** LLM timeout 60 s → tool HTTP 55 s → both retry → storm. Fix: one retry owner; turn cap; loop detector; nested timeouts strictly decreasing.

### 4.4 Zero-Trust MCP (RFC 8707, no token passthrough)

MCP 2025-11-25 remote servers are **OAuth 2.1 resource servers**. MUST: RFC 9728 Protected Resource Metadata, **RFC 8707** `resource` indicator (absolute URI, no fragment) on **authorization and token** requests, PKCE, no implicit grant, exact redirect URIs. MUST **validate audience**; MUST NOT **token-passthrough** to upstream APIs (confused deputy). Proxies MUST implement **per-client consent**. Local STDIO: env credentials, not OAuth. HTTP localhost: bind `127.0.0.1`, validate `Origin`/`Host`.

DNS rebinding: TypeScript SDK **CVE-2025-66414** and Python SDK **CVE-2025-66416** — protection **off by default** for unauthenticated localhost HTTP; enable `enableDnsRebindingProtection` / `TransportSecuritySettings`; patched defaults in TS **1.24.0** / Python **1.23.0** when binding localhost. Pin DNS between allowlist check and connect (TOCTOU).

Gateway pattern: **terminate OAuth** at the gateway (audience-bound token for the **gateway**). **Token exchange** (RFC 8693) to upstream; **never passthrough**. Pin MCP manifests (hash descriptions + schemas) against **rug pulls** / tool poisoning (OWASP MCP).

### 4.5 Tool RBAC and identity

**Never take identity from model JSON.** `user_id`, `tenant_id`, `role`, `Authorization`, `account_id` in tool **arguments** are **untrusted**. Bind identity from the **control-plane principal** (session JWT, MCP access token, Temporal `info` memo) **before** the adapter. The model may pass a **resource id** the principal is allowed to name; the adapter **re-checks ACL**. OWASP: confused deputy is the MCP server executing with **its** privileges, not the user’s. Agents SDK `RunContextWrapper.context` is **not** model-visible unless you inject it — that is the correct slot for `tenant_id`.

RBAC belongs in the **dispatcher**, not the system prompt (prompts are LLM01-injectable; 02). Map `(principal, tenant, tool, args_shape)` → allow / deny / HITL. High-impact: payments, email send, `tools/call` to shell/browser, CRM deletes → `needs_approval` / long-running HITL.

JSON Schema `strict` / Pydantic `extra="forbid"` stops **extra keys**, not **evil values**. HTTP tools: deny `169.254.169.254`, RFC1918, localhost, metadata IPv6; pin DNS. GraphQL: persisted-query allowlist. SQL tools: never `execute_sql`; parameterized ops only. MCP **descriptions** are prompt-injection surface (tool poisoning). Dual-LLM: quarantined model reads untrusted `tool_result`; privileged model holds tools.

### 4.6 PII in args/results and WORM audit

Tool args (SSN, PAN, email) and results (CRM dumps) **re-enter the window** and the **prompt cache**. Redact before inject; 02: tool-result clear default trigger **100k**, keep last **3**. Cap `limit` in the **adapter** regardless of schema (model will pass `limit: 10000`). Deep Agents: results **>20k** → file + **10-line** preview. Do not log raw `tool_result` to third-party traces without a BAA.

**Detect → redact → audit:** (1) DLP at the executor **before** persist/log; (2) stable placeholders so cache prefixes stay stable; (3) WORM log of placeholder → **hash**, not plaintext.

**Immutable audit:** `tool_name`, `call_id`/`tool_use_id`, principal, **hashed** args, policy decision, downstream status, latency, idempotency key, MCP server hash, breaker state, `correlation_id`. Temporal Event History is a natural second copy. Reconstruct a charge as: policy snapshot + model id + sampled tool_use + hashed args + Stripe status (including `Idempotent-Replayed`) + human interrupt.

---

## 5. Production Enterprise Code

Assumptions match research: SDK `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2`; Stripe-style store keeps **500s**; semantic reask **≤2** with **no** sleep; idempotency from **session** not model; MCP pages of **30**. Run offline: `python tool_dispatcher.py`.

```python
#!/usr/bin/env python3
"""Tool-calling control plane. Python 3.11+.

  python tool_dispatcher.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2
SEMANTIC_REASK_CAP = 2
MCP_PAGE_SIZE = 30
IDEMPOTENCY_TTL_S = 24 * 3600  # Stripe: prune ≥24h; reuse after prune = NEW request


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "intent_id": getattr(record, "intent_id", None),
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


def build_logger(correlation_id: str, tenant: str, intent_id: str | None = None) -> CorrelationAdapter:
    base = logging.getLogger("tool.dispatcher")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if intent_id:
        extra["intent_id"] = intent_id
    return CorrelationAdapter(base, extra)


class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per (provider, model) OR per tool-class. Do not trip on 429-with-Retry-After."""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


T = TypeVar("T")


async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """HTTP/transport loop ONLY. Full jitter. Do not wrap ValidationError."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = ra if ra is not None and 0 < ra <= 60 else random.random() * min(cap, base * (2**i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def session_idempotency_key(tenant: str, intent_id: str, tool: str, args: dict[str, Any]) -> str:
    """Stripe-style ≤255 chars, no PII. NEVER a model-emitted UUID."""
    material = f"{tenant}|{intent_id}|{tool}|{canonical_json(args)}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    return f"{tenant[:8]}-{intent_id[:8]}-{digest}"[:255]


class IdempotencyStore:
    """Stores status+body of FIRST execution, including 500s. Replay ≥24h then prune."""

    def __init__(self, ttl_s: float = IDEMPOTENCY_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._rows: dict[str, tuple[float, int, Any]] = {}

    def get(self, key: str) -> tuple[int, Any] | None:
        row = self._rows.get(key)
        if row is None:
            return None
        ts, status, body = row
        if time.monotonic() - ts > self.ttl_s:
            del self._rows[key]
            return None
        return status, body

    def put(self, key: str, status: int, body: Any) -> None:
        if key in self._rows:
            return  # first request wins, including 500s
        self._rows[key] = (time.monotonic(), status, body)


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()

    def _walk(node: dict[str, Any]) -> None:
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            props = node.get("properties", {})
            node["required"] = list(props)
            for child in props.values():
                if isinstance(child, dict):
                    _walk(child)
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            _walk(node["items"])
        for key in ("$defs", "definitions"):
            for child in node.get(key, {}).values():
                if isinstance(child, dict):
                    _walk(child)

    _walk(schema)
    return schema


class SessionPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str
    user_id: str
    roles: list[str]
    intent_id: str


class CreatePaymentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    # Untrusted if the model fills these — dispatcher overwrites from session.
    customer_id: str | None = None
    idempotency_key: str | None = None


class LookupAccountArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    name: str
    is_error: bool
    content: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    mutating: bool
    handler: Callable[..., Awaitable[dict[str, Any]]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def allowlist(self) -> list[str]:
        return sorted(self._tools)

    def provider_tools(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in self._tools.values():
            out.append({
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "strict": True,
                "parameters": strict_json_schema(spec.args_model),
            })
        return out


@dataclass
class McpTool:
    name: str
    description: str
    schema_hash: str
    server: str
    scopes: tuple[str, ...]


class McpCatalog:
    """MCP-like tools/list with cursor pagination. Re-list ALL pages on list_changed."""

    def __init__(self, tools: list[McpTool], page_size: int = MCP_PAGE_SIZE) -> None:
        self._all = tools
        self.page_size = page_size
        self._pinned: dict[str, McpTool] = {}

    async def list_all_pages(self) -> list[McpTool]:
        collected: list[McpTool] = []
        cursor = 0
        while True:
            page = self._all[cursor : cursor + self.page_size]
            collected.extend(page)
            cursor += self.page_size
            if cursor >= len(self._all):
                break
        self._pinned = {t.name: t for t in collected}
        return collected

    def pin_hash(self, name: str) -> str | None:
        t = self._pinned.get(name)
        return None if t is None else t.schema_hash


class FakeStripe:
    def __init__(self, store: IdempotencyStore) -> None:
        self.store = store
        self.calls = 0
        self.fail_next = False

    async def create_payment_intent(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        cached = self.store.get(key)
        if cached is not None:
            status, body = cached
            replayed = dict(body)
            replayed["idempotent_replayed"] = True
            if status >= 500:
                raise TransientError("stripe_replayed_500", status=status)
            return replayed
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            self.store.put(key, 500, {"error": "internal"})
            raise TransientError("stripe_500", status=500)
        body = {"id": f"pi_{hashlib.sha256(key.encode()).hexdigest()[:10]}", "status": "requires_capture", **payload}
        self.store.put(key, 200, body)
        return body


@dataclass
class ModelCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


class Dispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        catalog: McpCatalog,
        stripe: FakeStripe,
        payments_breaker: CircuitBreaker,
        disable_parallel_writes: bool = True,
    ) -> None:
        self.registry = registry
        self.catalog = catalog
        self.stripe = stripe
        self.payments_breaker = payments_breaker
        self.disable_parallel_writes = disable_parallel_writes
        self._poison: dict[str, int] = {}
        self.sem_pay = asyncio.Semaphore(32)
        self.sem_crm = asyncio.Semaphore(64)

    def _unknown(self, call: ModelCall) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            is_error=True,
            content={"error": "unknown_tool", "allowlist": self.registry.allowlist()},
        )

    def _rbac(self, principal: SessionPrincipal, spec: ToolSpec) -> None:
        if spec.mutating and "payments.write" not in principal.roles and spec.name.startswith("create_"):
            raise PermanentError("rbac_deny")

    async def _exec_one(self, principal: SessionPrincipal, call: ModelCall, log: CorrelationAdapter) -> ToolResult:
        spec = self.registry.get(call.name)
        if spec is None:
            await self.catalog.list_all_pages()
            spec = self.registry.get(call.name)
            if spec is None:
                log.warning("unknown_tool name=%s", call.name)
                return self._unknown(call)
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            fp = f"{call.name}|validation|{exc.errors()[0]['type']}"
            self._poison[fp] = self._poison.get(fp, 0) + 1
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                is_error=True,
                content={"error": "validation", "details": json.loads(exc.json()), "poison_count": self._poison[fp]},
            )
        dumped = args.model_dump()
        dumped.pop("idempotency_key", None)
        dumped.pop("customer_id", None)
        try:
            self._rbac(principal, spec)
        except PermanentError as exc:
            return ToolResult(call_id=call.call_id, name=call.name, is_error=True, content={"error": str(exc)})
        key = session_idempotency_key(principal.tenant, principal.intent_id, spec.name, dumped)
        try:
            result = await spec.handler(principal, args, key)
            return ToolResult(call_id=call.call_id, name=call.name, is_error=False, content=result)
        except TransientError as exc:
            return ToolResult(
                call_id=call.call_id,
                name=call.name,
                is_error=True,
                content={"error": "transient", "status": exc.status, "msg": str(exc)},
            )

    async def dispatch(self, principal: SessionPrincipal, calls: list[ModelCall], log: CorrelationAdapter) -> list[ToolResult]:
        mutating = [c for c in calls if (s := self.registry.get(c.name)) is not None and s.mutating]
        sequential = self.disable_parallel_writes and bool(mutating) and len(calls) > 1
        if sequential:
            log.info("parallel_writes_disabled count=%s", len(calls))
            return [await self._exec_one(principal, c, log) for c in calls]
        async def _one(c: ModelCall) -> ToolResult:
            try:
                return await self._exec_one(principal, c, log)
            except Exception as exc:  # always echo the ID
                log.error("exec_fail call_id=%s err=%s", c.call_id, exc)
                return ToolResult(call_id=c.call_id, name=c.name, is_error=True, content={"error": "internal"})
        return list(await asyncio.gather(*[_one(c) for c in calls]))

    async def semantic_loop(
        self,
        principal: SessionPrincipal,
        proposed: list[ModelCall],
        log: CorrelationAdapter,
        repair: Callable[[list[ToolResult]], list[ModelCall]] | None = None,
    ) -> list[ToolResult]:
        """Semantic reask: NO sleep. Cap SEMANTIC_REASK_CAP. HTTP jitter lives in handlers."""
        current = proposed
        last: list[ToolResult] = []
        for attempt in range(SEMANTIC_REASK_CAP + 1):
            last = await self.dispatch(principal, current, log)
            if not any(r.is_error and r.content.get("error") == "validation" for r in last):
                return last
            if any((r.content.get("poison_count") or 0) >= 3 for r in last):
                log.error("poison_pill_stop")
                return last
            if repair is None or attempt == SEMANTIC_REASK_CAP:
                return last
            log.info("semantic_reask attempt=%s", attempt + 1)
            current = repair(last)
        return last


def deterministic_decline(calls: list[ModelCall]) -> list[ToolResult]:
    return [
        ToolResult(call_id=c.call_id, name=c.name, is_error=True, content={"status": "degraded", "error": "fallback"})
        for c in calls
    ]


class FallbackChain:
    def __init__(
        self,
        primary: Callable[[], Awaitable[list[ModelCall]]],
        secondary: Callable[[], Awaitable[list[ModelCall]]],
        breaker: CircuitBreaker,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    async def plan(self, log: CorrelationAdapter) -> list[ModelCall]:
        try:
            await self.breaker.allow()
            result = await retry_with_jitter(self.primary, log=log)
            await self.breaker.record_success()
            return result
        except CircuitOpenError as exc:
            log.warning("llm_breaker_open err=%s", exc)
        except TransientError as exc:
            await self.breaker.record_failure(trip=True)
            log.warning("llm_primary_transient err=%s", exc)
        except PermanentError as exc:
            await self.breaker.record_failure(trip=False)
            log.error("llm_permanent_no_failover err=%s", exc)
            raise
        try:
            return await retry_with_jitter(self.secondary, log=log)
        except (TransientError, PermanentError) as exc:
            log.error("degraded_deterministic err=%s", exc)
            raise PermanentError("degraded") from exc


async def _offline() -> None:
    store = IdempotencyStore(ttl_s=60)
    stripe = FakeStripe(store)
    pay_br = CircuitBreaker("payments", failure_threshold=5, recovery_seconds=0.05)
    llm_br = CircuitBreaker("llm:sonnet5", failure_threshold=1, recovery_seconds=0.05)
    sem_pay = asyncio.Semaphore(32)
    registry = ToolRegistry()
    catalog = McpCatalog(
        [McpTool(f"t{i}", "d", hashlib.sha256(str(i).encode()).hexdigest()[:12], "mcp://s", ("crm.read",)) for i in range(35)]
    )

    async def create_payment(principal: SessionPrincipal, args: CreatePaymentArgs, key: str) -> dict[str, Any]:
        await pay_br.allow()
        async def _post() -> dict[str, Any]:
            async with sem_pay:
                return await stripe.create_payment_intent(
                    key,
                    {"invoice_id": args.invoice_id, "amount_cents": args.amount_cents, "customer_id": principal.user_id},
                )
        try:
            body = await retry_with_jitter(_post, log=build_logger("cid", principal.tenant, principal.intent_id))
            await pay_br.record_success()
            return body
        except TransientError:
            await pay_br.record_failure(trip=True)
            raise

    async def lookup_account(principal: SessionPrincipal, args: LookupAccountArgs, key: str) -> dict[str, Any]:
        _ = key
        return {"account_id": args.account_id, "tenant": principal.tenant, "limit": min(args.limit, 100)}

    registry.register(ToolSpec("create_payment_intent", "charge", CreatePaymentArgs, True, create_payment))
    registry.register(ToolSpec("lookup_account", "crm read", LookupAccountArgs, False, lookup_account))

    schema = strict_json_schema(CreatePaymentArgs)
    assert schema["additionalProperties"] is False
    assert "invoice_id" in schema["required"]
    tools = registry.provider_tools()
    assert tools[0]["strict"] is True

    pages = await catalog.list_all_pages()
    assert len(pages) == 35  # would be 30 if page-1-only

    principal = SessionPrincipal(tenant="acme", user_id="usr_1", roles=["payments.write"], intent_id="intent-99")
    cid = str(uuid.uuid4())
    log = build_logger(cid, principal.tenant, principal.intent_id)
    disp = Dispatcher(registry, catalog, stripe, pay_br)

    # Identity from session: model-supplied customer_id / idempotency_key stripped.
    charge = ModelCall("call_a", "create_payment_intent", {
        "invoice_id": "inv_1", "amount_cents": 500, "currency": "USD",
        "customer_id": "attacker", "idempotency_key": str(uuid.uuid4()),
    })
    r1 = await disp.dispatch(principal, [charge], log)
    assert r1[0].is_error is False
    assert r1[0].content["customer_id"] == "usr_1"
    first_id = r1[0].content["id"]
    r2 = await disp.dispatch(principal, [charge], log)
    assert r2[0].content["id"] == first_id
    assert r2[0].content.get("idempotent_replayed") is True
    assert stripe.calls == 1

    # Same key after 500: do NOT mint a new key.
    stripe.fail_next = True
    calls_before_500 = stripe.calls
    p2 = principal.model_copy(update={"intent_id": "intent-500"})
    c500 = ModelCall("call_b", "create_payment_intent", {"invoice_id": "inv_2", "amount_cents": 100, "currency": "USD"})
    fail = await disp.dispatch(p2, [c500], log)
    assert fail[0].is_error is True
    assert stripe.calls == calls_before_500 + 1  # one POST stored as 500
    again = await disp.dispatch(p2, [c500], log)
    assert again[0].is_error is True  # replayed 500, not a second charge
    assert stripe.calls == calls_before_500 + 1  # replay must not mint a new intent
    await pay_br.record_success()  # replayed 500 is not a class outage for later tests

    # Unknown tool → model-visible allowlist (not a 500).
    unk = await disp.dispatch(principal, [ModelCall("call_z", "create_charge", {"x": 1})], log)
    assert unk[0].is_error and "lookup_account" in unk[0].content["allowlist"]

    # extra=forbid validation → semantic reask payload, no sleep.
    bad = ModelCall("call_c", "create_payment_intent", {"invoice_id": "inv_3", "amount_cents": -1, "currency": "USD"})
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _nosleep(_: float) -> None:
        slept.append(1.0)

    asyncio.sleep = _nosleep  # type: ignore[method-assign]
    try:
        repaired = await disp.semantic_loop(
            principal,
            [bad],
            log,
            repair=lambda _rs: [ModelCall("call_c2", "create_payment_intent",
                                          {"invoice_id": "inv_3", "amount_cents": 100, "currency": "USD"})],
        )
        assert slept == []  # semantic path must not sleep
        assert any(not x.is_error for x in repaired)
    finally:
        asyncio.sleep = real_sleep  # type: ignore[method-assign]

    # Parallel writes disabled: mutating + read in one batch runs sequential, all IDs returned.
    mixed = await disp.dispatch(principal, [
        ModelCall("c1", "create_payment_intent", {"invoice_id": "inv_4", "amount_cents": 1, "currency": "USD"}),
        ModelCall("c2", "lookup_account", {"account_id": "acct_1", "limit": 5}),
    ], log)
    assert {m.call_id for m in mixed} == {"c1", "c2"}

    # HTTP jitter honors Retry-After ∈ (0, 60].
    slept2: list[float] = []

    async def _sleep(s: float) -> None:
        slept2.append(s)

    asyncio.sleep = _sleep  # type: ignore[method-assign]
    try:
        async def once() -> int:
            raise TransientError("429", retry_after=0.4)

        try:
            await retry_with_jitter(once, log=log, attempts=2)
        except TransientError:
            pass
        assert slept2 and abs(slept2[0] - 0.4) < 1e-9
    finally:
        asyncio.sleep = real_sleep  # type: ignore[method-assign]

    # Breaker closed → open → half-open; fallback degrades.
    async def boom() -> list[ModelCall]:
        raise TransientError("529")

    chain = FallbackChain(boom, boom, llm_br)
    try:
        await chain.plan(log)
        raise AssertionError("expected degraded")
    except PermanentError:
        pass
    assert llm_br.state is BreakerState.OPEN
    await asyncio.sleep(0.06)
    try:
        await llm_br.allow()
    except CircuitOpenError:
        raise AssertionError("should be half-open") from None
    await llm_br.record_success()
    assert llm_br.state is BreakerState.CLOSED
    declined = deterministic_decline([charge])
    assert declined[0].content["status"] == "degraded"

    print(json.dumps({
        "ok": True,
        "schema_required": schema["required"],
        "mcp_pages_total": len(pages),
        "stripe_calls": stripe.calls,
        "allowlist": registry.allowlist(),
        "breaker": llm_br.state.value,
        "cid": cid,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(_offline())
```

**Behavior encoded (maps to §§1–4):**

- Full-jitter **HTTP** retries; `Retry-After` honored iff \(0 < t \leq 60\); **semantic** reask does **not** sleep; cap **2**.
- Breaker closed → open → half-open; 429-with-RA does not trip; probe is `allow()` after recovery timer.
- Fallback primary → secondary → schema-valid `status: "degraded"` (no charge). **PermanentError does not failover**.
- JSON logs carry `correlation_id` + tenant + `intent_id`.
- Pydantic registry + `strict_json_schema` (`additionalProperties: false`, all keys `required`).
- MCP-like `tools/list` **all pages** (35 tools, page size 30 — page-1-only would drop 5).
- Idempotency key from `(tenant, intent_id, tool, canonical args)`; model `idempotency_key` / `customer_id` **stripped**; first 500 **replayed**.
- Unknown name → model-visible **allowlist**, not a 500; always return every `call_id`.
- Mutating tools in a batch: **sequential** (`disable_parallel_writes`).
- Poison counter on `(tool, error_type)`; identity from `SessionPrincipal`, not tool JSON.

**Interview talking point:** retries with jitter handle 529; they do not make `create_payment_intent` safe. Session-derived keys + round cap + schema-valid deterministic decline are three different classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers from the research file; scale-up arithmetic marked **[inferred]**.

### Scenario 1 — Payments + CRM tools with Stripe-style idempotency

**Problem statement.** Multi-tenant copilot: **10k tenants**, peak **100 concurrent agents**, each tool-using question = 1 planning call + 2 parallel REST tools (`create_payment_intent`, `lookup_account`) + 1 synthesis call. Constraint: **zero duplicate charges** on timeout/500/Activity retry; no cross-tenant CRM reads; p95 wall **< 8 s** **[inferred policy]**; Sonnet 5 Start **1,000 RPM** is plenty for 100 × 2 model-calls, but **100 Stripe POSTs** is the real ceiling. Cost envelope: cache-warm 20-tool prefix **[inferred] $19.14 / 1k questions**; uncached **$33.42 / 1k**; 20% schema-fail adds **+$0.96 / 1k**. HITL on refund and amount-over-threshold. ZDR org: no PAN in logs or prompt cache.

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  auth, tenant TPM, correlation-id, PII redact       │
                    │ Idempotency-Key on YOUR public POST (not the model's)    │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  Temporal workflow = tenant:thread               │
                    │  Activity LLM: strict tools; allowed_tools = f(intent)   │
                    │            SDK max_retries=0; parallel_tool_calls=false  │
                    │            whenever a mutating payment tool is attached  │
                    │  Activity ToolDispatcher:                                │
                    │    Pydantic extra=forbid → RBAC(principal) →             │
                    │    key=hash(tenant, intent_id, tool, args) → Stripe/CRM  │
                    │  CircuitBreaker(payments) ≠ CircuitBreaker(crm)          │
                    │  semantic reask ≤2; poison fingerprint → HITL            │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │                               │
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  Generation │            │ DATA  Executor          │
                    │ Sonnet 5 /       │            │ Fake-equivalent: Stripe │
                    │ GPT-5.4 failover │            │ adapter SAME key after  │
                    │ prefix cached    │            │ 500; CRM cap limit≤100  │
                    └────────┬─────────┘            └──────────┬──────────────┘
                             │                                 │
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ TOOL PROXIES     │            │ PERSIST  saga_state     │
                    │ identity=session │            │ idempotency ≥24h        │
                    │ never model JSON │            │ Kafka: Stripe webhooks  │
                    │ HITL refund      │            │   → Signal Workflow     │
                    └──────────────────┘            │ WORM hashed args        │
                                                    └─────────────────────────┘
```

**Technology choices.** Schema: OpenAI/Anthropic `strict: true`; Gemini `VALIDATED`. Discovery: **static OpenAPI** for first-party payments (small, cache-stable); MCP only for third-party SaaS with OAuth. Retry: Temporal owns HTTP; Pydantic semantic retry **≤2**; SDK `max_retries=0` inside Activities. Identity: `customer` from session; model may pass `invoice_id` which ACL re-checks. Failure drill: timeout after Stripe accepted POST → retry **same** key → `Idempotent-Replayed: true` → treat as success, then CRM note. If CRM fails, saga compensates; do **not** `create_payment_intent` again with a new key.

**Trade-off evaluation matrix.**

| Dimension | A. Model-invented UUID keys, parallel_tool_calls on, SDK retries nested in Temporal, extra=ignore | B. Recommended: session-hash keys, disable parallel writes, one retry owner, extra=forbid, Temporal Activity per tool | C. Every payment via hosted MCP with token passthrough |
| --- | --- | --- | --- |
| **Cost / 1k questions** | Uncached **$33.42** + duplicate charges (finance, not LLM meter) + schema-retry if extras accepted then Stripe 400 | Cached **$19.14**; **+$0.96** only if 20% still fail closed schema; cache schemas even on mutating turns | Same LLM $ plus MCP prefix tax; rug-pull / reconnect can **bust** tools cache (02) |
| **Latency** | Duplicate nano-style calls; nested 60 s × 55 s timeouts → p99 storms | Wall ≈ model + **one** Stripe RTT; REST p95 **<800 ms**; sequential writes only when mutating present | Extra OAuth/MCP hop; `tools/list` on the hot path if catalog uncached |
| **Ops complexity** | Looks simple until the first double charge | Medium (Temporal + two breakers + saga + webhook Signal) | Looks simple until confused-deputy incident |
| **Security posture** | Model JSON as customer_id; extra `bcc`-class keys ignored; keys may contain email (Stripe forbids PII in keys) | Principal from session; RFC-9110 POST protected by ≥24 h store including 500s; WORM hashed args | **Token passthrough** = confused deputy; RFC 8707 audience skipped |
| **Scalability ceiling** | 100 agents × 4 parallel = 400 Stripe POSTs; dashboard 429 while OpenAI TPM is idle | Bulkhead 32 payments / 64 CRM; Start 1k RPM ≫ 200 model RPM **[inferred]** | MCP pagination + disconnect eviction of `tools[]` = prefix miss storm |

**Decision rationale.** **B** is the only option that treats Stripe as an **at-least-once Activity against a ≥24 h store**, not as “another function the model called.” A fails the money exam (new key after 500; parallel duplicate charges; nested retries). C fails Zero-Trust (passthrough) and cache (MCP churn). Payments catalogs are small — **inline + freeze order + `allowed_tools`**, do not pay the 55k-token discovery tax for two first-party tools. Quote: uncached **$33.42** vs cached **$19.14** per 1k questions is a **prefix** problem (02), not a Stripe problem.

### Scenario 2 — MCP gateway with deferred schemas + catalog search

**Problem statement.** Platform team fronts **50 MCP servers**, **thousands** of tools, per-tenant OAuth. Anthropic documents a typical SaaS catalog at **~55k** definition tokens and selection-accuracy degradation past **30–50** inlined tools; tool search typically cuts **>85%**, loading **3–5** tools. Cursor A/B: **−46.9%** tokens on MCP-**calling** runs. Claude Code historically listed **page 1 only** (AgentCore **30**/page) → tools 31+ `No such tool`. Constraint: no 55k-token prefix, no page-2 dropouts, no token passthrough, p95 planner TTFT still cache-hit on a **frozen** search-tool + hot-tool prefix. Hallucinated names must become a **model-visible allowlist**, not a 500. Results >20k spill to object storage + 10-line preview.

**Proposed architecture.**

```
  ┌─────────────┐    ┌─────────────────────────────────────────────────────────┐
  │ Copilot /   │───▶│ CONTROL  MCP Gateway (resource server, RFC 8707 aud)    │
  │ IDE agents  │    │  1. Terminate OAuth; RFC 8693 exchange to upstream      │
  │             │    │     NEVER passthrough                                   │
  │             │    │  2. tools/list ALL pages on connect + list_changed      │
  │             │    │     Redis {name, hash(desc+schema), server, scopes}     │
  │             │    │  3. Per-turn allowlist = scopes ∩ tenant policy ∩      │
  │             │    │     allowed_tools                                       │
  │             │    │  4. Expose to model:                                    │
  │             │    │     Anthropic: tool search + defer_loading; 3–5 hot;    │
  │             │    │     NO cache_control on deferred tools                  │
  │             │    │     OpenAI gpt-5.4+: namespaces (<10 fns) + tool_search │
  │             │    │     Cursor-style: names in prompt, schemas on disk      │
  │             │    │  5. tools/call: pin hash (rug-pull), SSRF filter,       │
  │             │    │     timeout, host maps −32602 → allowlist is_error      │
  └─────────────┘    └───────────┬───────────────────────────┬─────────────────┘
                                 │                           │
                                 ▼                           ▼
                     ┌─────────────────────┐     ┌─────────────────────────────┐
                     │ DATA  Generation    │     │ TOOL PROXIES  50 MCP        │
                     │ schemas at END of   │     │ servers; last-good catalog  │
                     │ window on search hit│     │ TTL; Origin/Host on         │
                     │ (prefix cache lives)│     │ localhost; DNS pin TOCTOU   │
                     └──────────┬──────────┘     └──────────────┬──────────────┘
                                ▼                               ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │ PERSIST  Redis catalog + WORM (cid, hash, server hash,  │
                     │          policy, call_id)  OBJECT STORE oversized I/O   │
                     └─────────────────────────────────────────────────────────┘
```

**Technology choices.** Keep the **callable** set in the 30–50 band even if the catalog is thousands. On unknown `tools/call`: re-list **all pages once**, then allowlist error. Cap bytes in the adapter (`limit` the model passes is untrusted). Dual-LLM optional for untrusted `tool_result`. Measure `cache_creation_input_tokens`: mid-session ToolSearch unlock that **mutates the tools block** can still rewrite the prefix (Claude Code #53132). DNS rebinding: pin TS SDK **≥1.24.0** / Python **≥1.23.0**; enable rebinding protection explicitly on localhost.

**Trade-off evaluation matrix.**

| Dimension | A. Inline all schemas + strict, fetch MCP page 1 only | B. Recommended: gateway OAuth terminate, Redis catalog all pages, defer_loading + tool search, host-mapped allowlist | C. Names-on-disk only (Cursor-style) with no host allowlist map and no hash pin |
| --- | --- | --- | --- |
| **Cost / 1k** | 55k def tokens every turn; uncached 20-tool stand-in already **$33.42/1k questions** at 4k packed — 55k is **far** worse **[inferred]** | Search **>85%** cut (Anthropic); Cursor **−46.9%** on MCP-calling traces; hot 3–5 tools stay in the cached prefix | Token win similar to Cursor A/B **if** the agent actually `read`s files; eager IDEs still classify as inline |
| **Latency** | Prefill-bound TTFT; selection degrades **>30–50** tools | Prefix = search + hot tools; schemas append at **end of window**; first vLLM FSM still seconds if self-hosting | Extra `read` round-trips for schemas; variance with MCP count (Cursor: high) |
| **Ops complexity** | Low until tools 31+ “hallucinate” (page-2) | Medium (pagination, list_changed, hash pin, OAuth exchange) | Low until rug-pull changes a description the model still trusts |
| **Security posture** | Huge schema **enum leak**; token passthrough tempting at the IDE | RFC 8707 audience; no passthrough; pin hashes; −32602 → model-visible; SSRF filter | No pin = tool poisoning; unknown name may 500 the host (model never recovers) |
| **Scalability ceiling** | Attention + $ + 400 on `defer_loading`+`cache_control` if someone “fixes cache” | Callable set held in 30–50; catalog thousands in Redis; reconnect must not drop page 2 | Agent fleets that never `read` the schema file call the wrong arity forever |

**Decision rationale.** **B** is the only design that treats discovery as a **control-plane catalog** (all pages, pinned hashes, audience-bound tokens) and the window as a **working set** (search + 3–5 hot tools). A is the 55k-token interview fail and the AgentCore page-1 incident. C wins tokens when the product **is** Cursor’s A/B, but without host mapping a hallucinated name never becomes an allowlist `tool_result`, and without hashes a rug-pull is invisible. Fail closed if `defer_loading` and `cache_control` are both set. Keep **3–5** hottest tools non-deferred so the first turn does not always pay a search round.

---

## Key numbers to memorize

| Number | What |
| --- | --- |
| **$19.14 / $33.42 / $21.06** | Sonnet 5 tool-using question $/1k cached / uncached / +20% schema retry **[inferred]** |
| **+$0.96 / 1k** | 20% one-shot ValidationError reask on cached 20-tool prefix **[inferred]** |
| **$0.0048** | One schema reask increment (400 in + 400 out) **[inferred]** |
| **354 / 474** | Sonnet 5 hidden tool-use tokens `auto`/`none` vs `any`/`tool` |
| **~4,590 / ~6,670** | computer / browser toolset definition tokens on Sonnet 5 |
| **$17.98 / $35.78** | Computer-use 2-call question $/1k cached / uncached **[inferred]** |
| **~55k → >85%** | Typical SaaS catalog tokens vs tool-search cut; load **3–5** tools |
| **−46.9%** | Cursor deferred MCP (token cut on MCP-**calling** runs) |
| **30–50** | Inlined-tool selection-accuracy cliff (Anthropic) |
| **30 / page** | AgentCore `tools/list` example; page-1-only drops tool 31+ |
| **5,000 / 10 / 1,000** | OpenAI strict properties / nesting / enum cap (2026); Azure copy may still say **100 / 5** |
| **24 h** | Anthropic strict-tool grammar cache since last use; Stripe key retention **≥24 h** |
| **0.5 s / 8 s / retries=2** | SDK HTTP initial delay / cap / max_retries (not semantic reask) |
| **2–3** | Semantic reask cap then fail closed |
| **10** | OpenAI Agents `DEFAULT_MAX_TURNS` |
| **<10** | Functions per OpenAI tool-search namespace |
| **100k keep 3 / 20k** | Anthropic tool-result clear / Deep Agents offload preview |
| **10% / 1,122** | BFCL V4 hallucination/irrelevance weight / samples |
| **CVE-2025-66414 / 66416** | MCP DNS rebinding; patch TS **1.24.0** / Python **1.23.0** |
| **−32602 vs isError** | MCP unknown/invalid args vs execution failure (host must map to the model) |

**Interview closer:** “I compile Pydantic to the strict subset, allowlist without mutating the cached prefix, validate again with `extra=forbid`, reask ValidationError at most twice with no sleep, and mint Stripe keys from the session — never from the model. MCP is a paginated, audience-bound catalog, not 55k tokens in the system prompt.”
