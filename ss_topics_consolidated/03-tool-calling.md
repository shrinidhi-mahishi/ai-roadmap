# Topic 3: Tool Calling
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Tool calling is how LLMs **interact with the outside world** — reading databases, calling APIs, executing code, browsing the web. This topic covers JSON Schema tool definitions, the tool dispatch lifecycle (model generates call → your code validates → executes → injects result), Pydantic integration for schema generation, parallel vs sequential execution, dynamic tool discovery via MCP, retry and self-correction patterns, and error recovery. Tool calling transforms a language model from a text generator into an agent that can act.

### Why Study This

- **Foundation for agents**: Every agent architecture (Topics 4-9) depends on reliable tool calling. If your tool dispatch is fragile, your agent is fragile.
- **Interview staple**: Expect questions on hallucinated tool parameters (3-15% failure rate), schema validation strategies, infinite tool-calling loops (68 confirmed in 47 projects per IAL-Scan study), and the token cost of tool definitions in prompts.
- **Reliability multiplier**: A 5-agent chain where each tool call has 95% success rate yields only 77% end-to-end reliability. Understanding retry logic, circuit breakers, and self-correction (40% failure reduction) is the difference between a demo and production.
- **Cost awareness**: Tool definitions consume 40-200+ tokens each. With 20+ tools, that's 800-1,600 tokens of overhead per request — and most of it is wasted on irrelevant tools. Dynamic discovery and progressive disclosure cut this by 89%.

### What Details Are Included

- Anthropic vs OpenAI tool definition schemas with exact JSON examples and field-level differences
- Tool dispatch lifecycle for both providers with streaming mechanics
- BFCL benchmark scores (with the 48% defect rate caveat from Epoch AI)
- Dynamic tool discovery via MCP 2026-07-28 stateless spec
- Self-correction patterns reducing failures by ~40%
- Circuit breaker, retry, and fallback chain implementations
- Sandboxed execution (Docker, WASM, subprocess) with trade-offs
- OWASP LLM01 (tool injection) defenses
- Two enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2 hours)**: Sections 1-4. Focus on the tool dispatch lifecycle diagram and the Anthropic vs OpenAI schema comparison. Understand the difference between `tool_choice: auto`, `required`, and `any`.
>
> **Second pass (2-3 hours)**: Sections 5-7. Run the Pydantic schema generation code. Study the failure modes table — practice explaining each failure and its mitigation.
>
> **Interview prep (1 hour)**: Section 10 (Interview Quick Reference). Memorize the token overhead numbers and the compound failure math (0.95^n).
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

## 1. Concept Overview

**Tool calling** is the production architecture that lets an LLM emit a structured request -- a function name plus JSON arguments -- which **your** code (not the model) validates, authorizes, and executes. The model is an **untrusted planner**: it never holds your Stripe secret key, never runs your SQL, and never touches your IAM. A schema compiler converts Pydantic models into the provider-strict subset, a per-turn allowlist controls which tools are callable without mutating the cached prefix, and a dispatcher validates arguments again at execute-time, enforces RBAC from the session principal, mints idempotency keys derived from the session (never the model), and echoes every call ID back.

**Why it matters at enterprise scale.** A 20-tool Sonnet 5 copilot pays 354 (auto/none) or 474 (any/tool) hidden system tokens **plus** every JSON schema, every turn. Cache-warm that prefix and a two-call tool-using question costs ~$19/1k; miss cache and it is ~$33/1k. A 20% schema-invalid rate adds +$0.96/1k -- a full extra model call, not a 50ms HTTP retry. Anthropic documents a GitHub+Slack+Sentry+Notion+Splunk catalog at ~55k definition tokens; tool search typically cuts that >85%. Selection accuracy degrades past 30-50 inlined tools. Multi-step tool chains compound failure rates: a 5-step chain with 5% per-step failure yields 23% end-to-end failure, making saga patterns and circuit breakers non-negotiable.

**The two concepts most candidates get wrong.** (1) Conflating the HTTP retry loop (jitter for wires, no new tokens) with the semantic reask loop (ValidationError as tool_result, no sleep) -- mixing them is how you double-charge Stripe. (2) Taking identity from tool arguments (`user_id`, `tenant_id` in the model's JSON) instead of from the session principal -- that is a confused-deputy vulnerability the model can be prompt-injected into exploiting.

---

## 2. Core Concepts

### 2.1 JSON Schema: Drafts vs the Strict Subset

JSON Schema **Draft-07** and **2020-12** both treat `additionalProperties` as an applicator over names not matched by sibling `properties`/`patternProperties`. The default is **open** (any extra keys accepted). `additionalProperties: false` forbids extras; it does not make declared keys present -- that is `required`. Draft 2020-12 adds `unevaluatedProperties` (sees into `allOf`/`$ref`); `items`/`additionalItems` become `prefixItems`/`items`.

**MCP `inputSchema`** defaults to JSON Schema 2020-12 when `$schema` is omitted. It MUST be a JSON Schema object (not `null`). Empty-arg tools use `{ "type": "object", "additionalProperties": false }` for a closed empty object.

**The strict subset (OpenAI / Anthropic `strict: true`).** Both providers require:

| Rule | Detail |
|---|---|
| All objects closed | Every object must have `additionalProperties: false` |
| All keys required | Every key in `properties` must be listed in `required` |
| Optionality via union | Use `type: ["string", "null"]` (or union with `null`), not omitting from `required` |
| No unsupported composition | `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`/`then`/`else` all rejected with 400 |
| OpenAI limits (2026) | Up to 5,000 object properties total, 10 nesting levels, 1,000 enum values |
| Fine-tune restrictions | Additionally drops `minLength`/`maxLength`/`pattern`/`format`, numeric bounds, `patternProperties`, `minItems`/`maxItems` |

**Gemini** `functionDeclarations[].parameters` is an **OpenAPI-subset**. Production MCP adapters must strip `$schema`, `additionalProperties`, `$defs` or the request 400s.

**Key invariant (S1).** Grammar is a subset of syntax. `{"amount": -1}` can be schema-valid. Business constraints (`gt=0`, `EmailStr`, tenant-scoped IDs) live in **Pydantic at execute-time**, not in the grammar.

### 2.2 Tool Definition Schema Design

**Anthropic format** -- flat, no wrapper:

```json
{
  "name": "get_weather",
  "description": "Get the current weather in a given location",
  "input_schema": {
    "type": "object",
    "properties": {
      "location": { "type": "string", "description": "City and state" }
    },
    "required": ["location"]
  }
}
```

Required fields: `name` (regex `^[a-zA-Z0-9_-]{1,128}$`), `description` (plaintext), `input_schema` (JSON Schema object). Optional: `cache_control`, `strict` (boolean), `defer_loading` (boolean), `allowed_callers`, `input_examples` (~20-50 tokens for simple examples, ~100-200 for complex nested objects).

**OpenAI format** -- wrapped in `type: "function"`:

```json
{
  "type": "function",
  "name": "get_weather",
  "description": "Retrieves current weather for the given location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": { "type": "string" },
      "units": { "type": "string", "enum": ["celsius", "fahrenheit"] }
    },
    "required": ["location", "units"],
    "additionalProperties": false
  },
  "strict": true
}
```

**Critical incompatibility.** Copying an OpenAI definition to Anthropic (or vice versa) will silently fail. The `type: "function"` wrapper and `parameters` vs `input_schema` naming differ. Any cross-provider proxy must translate at this boundary.

**`tool_choice` parameter comparison:**

| Behavior | Anthropic | OpenAI | Gemini |
|---|---|---|---|
| Model decides | `{"type": "auto"}` (default) | `"auto"` (default) | `AUTO` |
| Must use a tool | `{"type": "any"}` | `"required"` | `ANY` |
| Specific tool | `{"type": "tool", "name": "X"}` | `{"type": "function", "name": "X"}` | `ANY` + `allowed_function_names` |
| No tools | `{"type": "none"}` | `"none"` | `NONE` |
| Subset restriction | N/A (use tool search + defer_loading) | `{"type": "allowed_tools", "tools": [...]}` | `ANY` + optional `allowed_function_names` |
| Schema validation mode | `strict: true` | `strict: true` | `VALIDATED` (schema + required params) |
| Disable parallel | `tool_choice.disable_parallel_tool_use: true` (inside tool_choice, NOT top-level) | `parallel_tool_calls: false` (top-level) | Constrain with sequential steps |

**Anthropic caveat**: Claude Opus 5.5, Fable 5.1, and Mythos 5.1 do not support forced tool use (`any` or `tool` returns 400). Use `auto` with `strict: true` instead.

### 2.3 Pydantic v2 as Last-Mile Validator

Pydantic serves as the canonical schema layer with a three-step pipeline:

1. **Compile:** `Args.model_json_schema()` or `TypeAdapter(T).json_schema(mode="validation")` produces a JSON Schema that a provider-subset compiler transforms into the strict subset (add `additionalProperties: false`, make all keys `required`, convert `Optional` to union with `null`, strip unsupported keywords).
2. **Execute-gate:** `Args.model_validate_json(raw)` validates the model's response **before any side effect**.
3. **Closed objects:** `model_config = ConfigDict(extra="forbid")` produces error type `extra_forbidden` on hallucinated keys. Default `extra="ignore"` **silently drops** them -- dangerous because a hallucinated `bcc:` on `send_email` vanishes from audit.
4. **Retry context:** `ValidationError.errors()` returns `{type, loc, msg, input, ctx?, url}`; `e.json()` is the compact payload to feed back to the model as a tool_result.

`TypeAdapter` covers dataclasses, typed dicts, unions without a `BaseModel`. `defer_build=True` (v2.10+) postpones core-schema compile until first validate -- useful for large catalogs. OpenAI Python `type_to_text_format_param` always sets `"strict": True` when converting a Pydantic type.

### 2.4 Tool Dispatch Lifecycle

**Anthropic lifecycle:**

1. Client sends `POST /v1/messages` with `tools` array and messages.
2. Model responds with `stop_reason: "tool_use"` and one or more `tool_use` content blocks: `{type: "tool_use", id: "toolu_...", name: "...", input: {...}}`.
3. Client executes tool(s) locally.
4. Client appends the assistant response to messages, then appends a `user` message containing **all** `tool_result` blocks **first** (matching `tool_use_id`), then optional text. The assistant message must be echoed **verbatim** (including thinking signatures).
5. Loop until `stop_reason == "end_turn"` or iteration cap reached.

**OpenAI lifecycle (Responses API -- required for GPT-6 Astra):**

1. Client sends request with `tools` array.
2. Model returns `function_call` items with `name`, `arguments` (JSON string), `call_id`.
3. Client executes, returns `function_call_output` items referencing each `call_id`.
4. For reasoning models (GPT-5, o4-mini), reasoning items from the model's response must also be passed back with tool call outputs.
5. Loop until no more tool calls.

**OpenAI Chat Completions (legacy):** Uses `choices[0].message.tool_calls` array; results sent as `role: "tool"` messages with matching `tool_call_id`. Do NOT nest the Chat Completions tool shape on the Responses API -- you will get `invalid_request_error`.

**Gemini Interactions:** `function_result.call_id` must match `steps[].id`. Gemini `generateContent`: `functionCall.id` is often `None` -- match by name; if `id` is populated, echo it. Gemini 3: `thought_signature` on the first `functionCall` of each step (parallel: first part only). Omit the signature and you get a 400.

### 2.5 Parallel vs Sequential Tool Execution

| Provider | Parallel default | Disable | Completeness rule |
|---|---|---|---|
| OpenAI | On; `parallel_tool_calls: false` forces exactly 0 or 1 call | Top-level `parallel_tool_calls` | Match every `call_id`; GPT-5+ custom and built-ins NOT in one batch |
| Anthropic | Parallel `tool_use` blocks | `tool_choice.disable_parallel_tool_use: true` (**inside** `tool_choice`, not top-level) | All `tool_result` blocks first; skip remainder of computer/browser member batches on first failure |
| Gemini | Parallel `functionCall` parts | Constrain with `ANY` + names / sequential steps | Interactions: echo `call_id`; thought signature on first part |

**Important gotchas:**
- Fine-tuned OpenAI: parallel calls **disable strict for that turn**.
- Snapshot `gpt-4.1-nano-2025-04-14` can emit duplicate same-tool calls if parallel is on. Two parallel `charge` calls with two keys = two charges.
- `defer_loading` reveal spliced between a sibling `tool_use` and its `tool_result` is a documented 400.
- OpenAI `strict: true` schema guarantees are NOT honored across parallel calls.

**Performance data:** LLMCompiler paper (ICML 2024) showed parallel tool calls reduce end-to-end latency by up to 3.7x.

**Dependency rule:** Never execute tool calls with side effects (writes, sends, deletes) in parallel with reads that inform them. Enforce sequential execution at the orchestration layer.

### 2.6 Streaming with Tool Calls

**Anthropic:** Stream emits typed SSE events. Text deltas, `input_json_delta` fragments, and thinking blocks arrive interleaved on separate content block indices. Reconstruct by grouping on index, not arrival order. Fine-Grained Tool Streaming (GA February 5, 2026) removes buffering and streams tool parameters immediately. **Critical: do NOT execute on `input_json_delta.partial_json` or the `input: {}` at `content_block_start`.** Wait for `content_block_stop` with complete JSON.

**OpenAI (Responses API):** Key events: `response.output_item.added` (begins a function call, includes name and call_id), `response.function_call_arguments.delta` (incremental JSON chunks), `response.function_call_arguments.done` (complete arguments). Accumulate deltas by `output_index`. Do NOT execute on `function_call_arguments.delta`.

**Cross-provider proxy warning:** Proxying Claude through OpenAI-compatible gateways (LiteLLM, llm-gateway) during streaming can produce invalid tool-use events, empty `tool_use` blocks, or truncated JSON. Use native SDKs where possible.

### 2.7 The Two Retry Loops (Never Conflate)

This is the single most important concept for interviews. There are two fundamentally different retry loops, and conflating them causes double-charges and wasted spend.

| Loop | Trigger | What you send the model | Backoff | Cap |
|---|---|---|---|---|
| **HTTP / transport** | 408/429/5xx, connect reset, Anthropic 529 | Nothing (no new tokens billed if it fails before streaming) | Exponential + **full jitter**; OpenAI SDK `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2` | Honor `Retry-After` iff 0 < t <= 60; Anthropic 529 = failover, do not spin |
| **Schema-invalid args (semantic)** | Pydantic `ValidationError` / JSON-RPC -32602 | `tool_result`/Instructor reask with `e.json()` | **No sleep** (same args fail identically; sleeping wastes time) | 2-3 reasks then fail closed |
| **Tool runtime error** | Timeout, CRM 5xx, `isError: true` | Error payload as tool result (`is_error: true`) | HTTP backoff **inside** the adapter iff idempotent | Adapter retries are independent of model retries |

**Rules:**
- Never exponential-backoff a `ValidationError` (same args fail identically every time).
- Never semantic-retry a 429 without sleeping (you will hammer the API).
- Disable nested SDK retries inside Temporal Activities (`max_retries=0`) so one owner retries.
- Self-correction (returning structured validation errors to the model) reduces failure rates by ~40% compared to naive retries.
- AWS research shows full jitter reduces retry storms by 60-80% compared to fixed delays.

**Framework-specific behavior:**
- Instructor `client.create(..., max_retries=2)` = 1 extraction + 2 validation retries (3 SDK calls), separate from transport `OpenAI(max_retries=2)`. Exhaustion raises `InstructorRetryException`. Mode.TOOLS reasks via a tool message.
- LangGraph `ToolNode.handle_tool_errors` default catches invocation/validation errors but **re-raises ordinary execution failures**.
- OpenAI Agents: `@function_tool` `failure_error_function` turns exceptions into model-visible output; `tool_not_found_behavior="return_error_to_model"` vs default `ModelBehaviorError`; `DEFAULT_MAX_TURNS=10`.

### 2.8 Dynamic Tool Discovery

| Strategy | What the model sees every turn | Who expands | Token impact |
|---|---|---|---|
| Inline all schemas | Full JSON Schema x N + hidden tool-use prompt (354/474) | You | ~55k for a typical SaaS catalog |
| Names + disk (Cursor) | Tool names; schemas as files the agent reads | Agent | -46.9% on MCP-calling runs (Cursor A/B) |
| Anthropic `defer_loading` + tool search | Search tool + 3-5 hot tools; rest via `tool_reference` | Anthropic API | 77k to 8.7k tokens (89% reduction) |
| OpenAI `tool_search` + namespaces (gpt-5.4+) | Namespace blurb; schemas at end of window on hit | OpenAI or your `tool_search_output` | Keep <10 functions per namespace |
| Per-turn `allowed_tools` | Full schema list (cached) but only a subset callable | OpenAI | Cache-preserving; no prefix mutation |

**MCP JSON-RPC 2.0:** `tools/list` returns `ListToolsResult.tools[]` + optional `nextCursor`; `tools/call` takes `{name, arguments}` and returns `content[]`, optional `structuredContent`, optional `isError`. The 2025-06-18 error split: unknown tool / invalid args = -32602; execution failure = `isError: true`.

**MCP 2026-07-28 spec is stateless.** No protocol-level sessions. Tool and method names travel in `Mcp-Method` and `Mcp-Name` HTTP headers for gateway routing without body parsing. Dynamic Client Registration (DCR) is deprecated in favor of Client ID Metadata Documents (CIMD). Progressive discovery is planned (servers expose a small entry point and reveal more catalog as the conversation narrows). MCP Server Cards (`.well-known` URLs) are under development.

**Anthropic tool search mechanics:** You still send every definition every request; `defer_loading` controls the system-prompt prefix. At least one tool (the search tool) stays non-deferred. Never set `cache_control` on a deferred tool (causes 400 every request). Grammar for strict mode builds from the full toolset -- deferral does not recompile grammars. Tool references persist across turns.

**Pagination invariant (D1).** Pagination bugs present as hallucinated names. AgentCore paginates at 30/page; Claude Code historically listed page 1 only. On `tools/call` unknown: re-list all pages once, then fail with allowlist -- do not inline 55k tokens.

**Mid-conversation tool changes (beta, 2026-07-01).** Claude Opus 5 supports adding/removing tools mid-conversation through `tool_addition` and `tool_removal` content blocks on `role: "system"` messages, avoiding re-sending the full top-level tools array.

### 2.9 Tool Result Formatting

Return semantic, stable identifiers (slugs, UUIDs) rather than opaque internal references. Include only fields the model needs for its next reasoning step. For void functions (e.g., `send_email`), return a success/failure indicator string. Large results must be truncated or summarized before injection -- bloated responses waste context and degrade reasoning quality. Results >20k should spill to object storage + 10-line preview. Cap `limit` in the adapter regardless of schema (model will pass `limit: 10000`).

**Terminal-state clarity.** Every tool response must unambiguously signal completion or continuation. Ambiguous responses ("Found 2 flights, more may be available") cause infinite loops. Clear terminal states ("SUCCESS: Booking HT79265 confirmed") reduced tool calls from 14 to 2 in one demo -- a 7x improvement.

### 2.10 Hosted / Server Tools

Hosted tools invert the executor: OpenAI built-ins (web search, file search, code interpreter, hosted MCP), Anthropic `web_search`/`web_fetch`/`code_execution`/`tool_search`, and Gemini Search/Code Execution run **inside** the provider.

**Mixed parallel groups are a topology hazard:** Anthropic may return `stop_reason: "tool_use"` if a server tool sits in the same batch as a client tool -- complete client `tool_result`s before the server path continues. OpenAI GPT-5+: custom functions may parallelize, but built-ins cannot share a parallel function-call batch. Gemini 3 mixes built-in + custom via `previous_interaction_id`.

`pause_turn` treated as `end_turn` drops server-tool results.

---

## 3. Architecture & System Design

### 3.1 System Topology

The architecture separates into five planes that **must not be coupled**. Coupling them is how you get double-charges, confused-deputy attacks, and retry storms.

```
+---------------------------------------------------------------------------------+
| CLIENTS                                                                         |
|  SSE copilot  |  REST extract  |  HITL (refund / amount)  |  Stripe webhooks   |
+------------+--------------------------------------------------------------------+
             | TLS + session JWT + Idempotency-Key (YOUR POST) + correlation-id
             v
+---------------------------------------------------------------------------------+
| CONTROL PLANE  (your process -- dispatcher, not the GPU)                        |
|                                                                                 |
|  +------------+  +------------+  +------------+  +------------+  +-----------+  |
|  | Edge       |->| Policy     |->| Schema     |->| Allowlist  |->| MCP       |  |
|  | auth, RPM  |  | PII redact |  | compiler   |  | per-turn   |  | discovery |  |
|  | breaker per|  | tool RBAC  |  | Pydantic ->|  | OpenAI     |  | tools/list|  |
|  | (vendor,   |  | principal  |  | STRICT     |  | allowed_   |  | ALL pages |  |
|  |  model,    |  | != model   |  | additional |  | tools /    |  | pin hash  |  |
|  |  tool-class|  | JSON       |  | Properties |  | Gemini     |  | list_     |  |
|  |  )         |  |            |  | false +    |  | allowed_   |  | changed   |  |
|  |            |  |            |  | all required| | function_  |  | re-list   |  |
|  +------------+  +------+-----+  +------+-----+  | names      |  +-----+----+  |
|                        |               |         +------+-----+        |        |
|                        v               v               v               v        |
|                 +--------------------------------------------------------------+|
|                 | ORCHESTRATOR  Temporal Workflow = tenant:thread               ||
|                 |  +-- Activity: LLM (strict, max_retries=0 in SDK)            ||
|                 |  +-- Activity: ToolDispatcher per call_id                    ||
|                 |  +-- loop: tool_use -> validate -> execute -> tool_result    ||
|                 |  +-- echo ALL IDs; disable parallel around money            ||
|                 |  +-- semantic reask <=2 (no sleep) != HTTP jitter            ||
|                 +-----------------------------+--------------------------------+|
|  +------------+  +------------+               |          +-------------------+  |
|  | Idempotency|  | Circuit    |<--------------+--------->| Fallback compile  |  |
|  | store >=24h|  | breaker    |                          | Anthropic msgs    |  |
|  | incl. 500s |  | per class  |                          | vs Responses      |  |
|  | key=hash(  |  | payments / |                          | vs Gemini FC      |  |
|  | tenant,    |  | CRM / MCP) |                          +--------+----------+  |
|  | intent_id, |  +------------+                                    |             |
|  | tool, args)|                                                    |             |
|  +------------+                                                    |             |
+--------------------------------------------------------------------+-------------+
                                                                     |
          +--------------------------------+-------------------------+
          | chat / agent SSE, REST         |
          v                                v
+---------------------------------+  +--------------------------------------------+
| DATA PLANE  GENERATION          |  | DATA PLANE  EXECUTOR (your workers)        |
| (provider-owned on hosted APIs) |  | model NEVER holds IAM or Stripe sk         |
|                                 |  |                                            |
|  Tokenizer -> Prefill -> Decode |  |  complete JSON only (no partial_json exec) |
|  + grammar bitmask (strict /    |  |  Pydantic extra=forbid -> RBAC -> timeout  |
|    VALIDATED / vLLM FSM)        |  |  bulkhead Semaphore per downstream class   |
|  Parser: function_call |        |  |                                            |
|    tool_use | functionCall      |  |  Hosted/server tools invert this box:      |
|  stop_reason=tool_use /         |  |  OpenAI built-ins, Anthropic web_search,   |
|    output items with call_id    |  |  Gemini Search run INSIDE the provider     |
+-------------+-------------------+  +----------------------+---------------------+
              |                                             |
              |  structured call (untrusted planner)        | side effects
              v                                             v
+---------------------------------+  +--------------------------------------------+
| TOOL PROXIES  (MCP / adapters)  |  | PERSISTENCE LAYER                          |
| Zero-Trust: RFC 8707 resource   |  |                                            |
| indicator; NO token passthrough |  |  +------------------+  +-----------------+ |
| identity = session JWT / Temporal|  |  | App state        |  | Idempotency     | |
| info -- never model JSON        |  |  | Postgres:        |  | store (Redis or | |
|  +----------+  +-------------+  |  |  |  saga_state,     |  |  Stripe's)      | |
|  | Stripe   |  | CRM / MCP   |--+--+  |  thread, HITL    |  |  status+body of | |
|  | adapter  |  | tools/call  |  |  |  |  Temporal hist.  |  |  FIRST request  | |
|  | same key |  | cap limit   |  |  |  |  outbox events   |  |  including 500s | |
|  | after 500|  | SSRF filter |  |  |  +------------------+  +-----------------+ |
|  +----------+  +-------------+  |  |  +------------------+  +-----------------+ |
|  mutating tools sequential      |  |  | MCP catalog      |  | Soft caches     | |
+---------------------------------+  |  | Redis: name,     |  | prompt-cache KV | |
                                     |  | hash(desc+schema)|  | grammar <=24h   | |
                                     |  | server, scopes   |  | (Anthropic      | |
                                     |  +------------------+  |  strict tools)  | |
                                     |                        +-----------------+ |
                                     +-----------------------+--------------------+
                                                             |
+------------------------------------------------------------+--------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                  |
|  +-------------+ +-------------+ +-------------+ +----------------------------+ |
|  | Audit (WORM)| | Metrics     | | Traces      | | Usage (authoritative       | |
|  | cid, tenant | | tool RTT    | | gateway->LLM| | on terminal event)         | |
|  | SHA-256 args| | p50/p95/p99 | | ->validate->| | input, cache_read,         | |
|  | call_id,    | | schema-fail | | execute->   | | cache_write, output,       | |
|  | policy,     | | %, poison   | | Stripe/CRM  | | thinking_tokens            | |
|  | idempotency | | fingerprint,| |             | |                            | |
|  | key, MCP    | | breaker,    | |             | |                            | |
|  | server hash | | sem wait    | |             | |                            | |
|  +-------------+ +-------------+ +-------------+ +----------------------------+ |
+---------------------------------------------------------------------------------+
```

**Plane responsibilities (do not couple):**

| Plane | Owns | Failure if coupled |
|---|---|---|
| **Control** | Compiler, allowlist, RBAC, Temporal loop, idempotency key minting, MCP pagination | Provider 529 becomes a second Stripe POST with a new key |
| **Generation data** | Prefill + constrained decode + parser | Executor runs on `partial_json` |
| **Executor data** | Validate, ACL, HTTP, `tools/call` | Model JSON as IAM = confused deputy |
| **Tool proxies** | Stripe/CRM/MCP with audience-bound tokens | Token passthrough to upstream |
| **Persistence** | Idempotency store + saga + catalog hashes | Restart = duplicate charge; `list_changed` unseen = rug pull |
| **Telemetry** | Hashed args, `call_id`, usage | Finance dashboards that ignore schema-retry tokens |

### 3.2 End-to-End Request Flow

1. **Ingress.** SSE (interactive agent) or REST (single-shot extract). Gateway stamps `correlation_id`, checks tenant quota, consults the per-(vendor, model) breaker AND the per-tool-class breaker (payments != CRM). Stripe/CRM RPM is a second token bucket -- OpenAI TPM left does not mean Stripe will accept 400 parallel `PaymentIntent`s.

2. **Policy + identity.** Detect and redact PII before args are logged or cached. Bind principal from session JWT / Temporal memo / MCP access token. Tool RBAC maps `(principal, tenant, tool, args_shape)` to allow / deny / HITL. Per-turn allowlists are capability reduction, not authorization -- a prompt-injected model can still call every tool you left callable.

3. **Schema compile (or cache hit).** `Args.model_json_schema()` or `TypeAdapter.json_schema()` goes through the provider-subset compiler: every object gets `additionalProperties: false`, every key goes in `required`, `Optional[T]` becomes `["T","null"]`, unsupported keywords are stripped. Hash the compiled blob; Anthropic strict grammars cache for 24h since last use. Illegal schema = 400, not best-effort JSON.

4. **Allowlist without prefix mutation.** OpenAI `tool_choice.type: "allowed_tools"` subsets names without rewriting the cached `tools` array. Do not reorder or hot-reload the full `tools[]` every request -- that is a prefix cache miss.

5. **MCP discovery (if remote).** `tools/list` with cursor pagination until `nextCursor` is absent. Persist `{name, hash(description+schema), server, scopes}` in Redis. On `notifications/tools/list_changed`, re-list all pages. Last-good catalog with TTL beats inlining 55k tokens.

6. **Dispatch model.** One process-wide async client per vendor; `max_retries=0` if Temporal owns HTTP retry. `strict: true` on OpenAI Chat (default off; Responses omits it, tries strict). Anthropic `strict: true` guarantees name is in provided tools; computer/browser toolset entries reject `strict` (400). Stream: concatenate until `content_block_stop` / complete `function_call_arguments` -- do not execute early.

7. **Parse + ID contract.** Provider-specific ID matching rules:
   - **OpenAI:** Inject `function_call_output` matching `call_id` (Responses) or Chat `role: "tool"` + `tool_call_id`.
   - **Anthropic:** One user message with all `tool_result` blocks first (`tool_use_id` match), then optional text; echo the assistant message verbatim (thinking signatures).
   - **Gemini Interactions:** `function_result.call_id` must match `steps[].id`. `generateContent`: match by name if `id` is None. Gemini 3: `thought_signature` on the first `functionCall` of each step.

8. **Execute-gate.** For each completed call: unknown name becomes a model-visible allowlist error (host maps JSON-RPC -32602 so the model can recover). `Args.model_validate_json(raw)` with `extra="forbid"` before any side effect. RBAC re-checks resource IDs the model named. Mint idempotency key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` or `workflowRunId + activityId` -- never from model-emitted UUID.

9. **Downstream + completeness.** `asyncio.gather` with per-call try/except for independent reads; always return N results (skipped siblings get `is_error: true`). Mutating tools: `parallel_tool_calls: false` / `disable_parallel_tool_use: true` inside Anthropic `tool_choice`. Nested timeout ordering: LLM request timeout > tool Activity `StartToClose` > HTTP client > downstream SLA. Invert this and you retry-amplify.

10. **Semantic vs HTTP recovery.** `ValidationError` goes as `tool_result` containing `e.json()`; no sleep; cap 2-3 reasks then fail closed. Transient Stripe/CRM 5xx retries with HTTP backoff only if the tool is retryable and the same idempotency key is reused. After Stripe 500, do NOT mint a new key.

11. **Saga / webhook.** Charge succeeds, CRM 503s: return both `tool_result`s; persist `saga_state`; a separate Workflow (or human) compensates -- do not auto-refund from the model loop. Stripe webhooks signal the Workflow; never a Kafka client inside Workflow code.

12. **Emit + audit.** Terminal usage is the invoice (schema-retry tokens included). WORM log: `tool_name`, `call_id`/`tool_use_id`, principal, hashed args, policy decision, downstream status, latency, idempotency key, MCP server hash.

---

## 4. Key Algorithms & Mechanics

### 4.1 State Machines

**HTTP retry (control plane -- wires, not tokens):**

```
                    Retry-After in (0, 60s]              attempts exhausted
  +----------+  HTTP 408/409/429/5xx/529   +---------+  -----------------> FAIL
  |  SEND    | --------------------------> |  WAIT   |
  +----+-----+  400 schema / 401 / 403     | jitter  |
       |        spend-cap 429 (no RA)      | cap 8s  |
       |        -----------------------> FAIL        |
       | success                           +----+----+
       v                                        |
     DONE <-------------------------------------+  retry SEND
```

**Tool-use loop (generation -> executor -> generation):**

```
  MODEL --> end_turn / stop_sequence --> DONE
         --> max_tokens mid-tool_use --> FAIL CLOSED (do NOT exec partial JSON)
         --> refusal --> FAIL CLOSED
         --> pause_turn --> resend assistant content (server-tool cap)
         --> tool_use / function_call[]
                |
                v
         +--------------+  unknown name     +-----------------------------+
         | PARSE complete| ----------------> | tool_result is_error +      |
         | JSON (not     |                   | allowlist (model-visible)   |
         | partial_json) |                   +-------------+--------------+
         +------+--------+                                 |
                | known                                    |
                v                                          |
         +--------------+  ValidationError   +-------------+--------------+
         | PYDANTIC     | -----------------> | SEMANTIC REASK (no sleep)  |
         | extra=forbid |                    | e.json() as tool_result    |
         +------+-------+                    | N>=3 identical fingerprint |
                | valid                      | --> poison -> HITL / DLQ   |
                v                            +-------------+--------------+
         +--------------+  deny                            |
         | RBAC + HITL  | --> is_error / pause             |
         +------+-------+                                  |
                | allow                                    |
                v                                          |
         +--------------+  timeout / 5xx (idempotent)      |
         | EXECUTE      | -- HTTP jitter, SAME key --+     |
         | key=hash(    |                            |     |
         |  session,    |  mutating + parallel -- SEQ|     |
         |  intent,     |                            |     |
         |  tool, args) |                            |     |
         +------+-------+                            |     |
                | all IDs                            |     |
                v                                    v     v
         NEXT MODEL TURN  (tool_result / function_call_output / functionResponse)
         echo thinking / thought_signature verbatim
```

**MCP catalog lifecycle:**

```
  CONNECT --> tools/list page --> nextCursor? --yes--> next page
                    |                    no
                    v
              PIN hashes in Redis --> notifications/tools/list_changed --> re-list ALL
                    |
                    v
              tools/call -- unknown --> re-list once --> allowlist is_error
```

### 4.2 Schema Compile / Cache Complexity

Walk the JSON Schema tree (`properties`, `items`/`prefixItems`, `$defs`): **Theta(N)** in nodes. Canonical `json.dumps(..., sort_keys=True, separators=(",",":"))` then SHA-256 is **Theta(B)** in serialized bytes. Provider grammar compile (Anthropic strict, OpenAI strict, vLLM named/required structured outputs): first named-function FSM is documented as "several seconds" then cached; Anthropic tool schemas cached 24h since last use. Subsequent turns with a frozen `tools[]` prefix are **O(1)** cache-key lookup plus KV prefix reuse.

vLLM: `auto` constrains only if at least one tool has `strict: true` AND `VLLM_ENFORCE_STRICT_TOOL_CALLING=true` (default); else raw-text extract. Always Pydantic-validate after the parser. Hermes historically drops a call if a literal `</tool_call>` appears inside a JSON string.

Dispatcher per turn: **O(T)** tools. Parallel independent reads: wall-clock = **max(tool RTT)**, not the sum. Sequential computer-use: N x action RTT. Catalog list: O(P x page_size); you must exhaust the cursor.

### 4.3 Compound Failure Math

Multi-step tool chains compound failure rates. This is a critical interview number:

```
P(at least one failure in N-step chain) = 1 - (1 - p)^N

5-step chain, 5% per-step failure rate:
P(failure) = 1 - (0.95)^5 = 22.6%
```

**With saga + per-step retry (3 attempts):**

```
P(step failure after retries) = (0.05)^3 = 0.000125
P(workflow failure) = 1 - (1 - 0.000125)^4 = 0.05%
```

Analysis of LLM API traffic (February 2026): 5% of all LLM call spans reported errors; 60% caused by rate limits. Five agents at 95% individual accuracy deliver ~77% overall success rate. Multi-agent systems show failure rates between 41% and 86.7% in production.

### 4.4 Core Invariants (Interview Checklist)

1. Hosted APIs never execute customer tools (server tools are the exception -- still not your Stripe key).
2. Do not execute on partial JSON / placeholder `input: {}`.
3. Echo every ID in one turn; Anthropic results first.
4. Compile Pydantic to the strict subset; validate again at execute-time with `extra="forbid"`.
5. Semantic retry <=2 with `ValidationError.json()`; HTTP jitter is a different loop.
6. Idempotency key not from the model; Temporal Activity per LLM call and per tool.
7. Parallel: return every ID; disable parallel around money.
8. Discover via MCP all pages + tool search; never inline 55k tokens as the happy path.
9. Identity from the session principal; model may pass a resource ID the ACL re-checks.
10. Poison-pill schemas (always-invalid) need a loop detector on `(tool, error_type)`.
11. Schema-execution parity: the schema registered must exactly match validation logic at execution.
12. Context monotonicity: tool results only grow context; budget enforcement happens before injection.
13. Terminal-state clarity: every response unambiguously signals completion or continuation.

---

## 5. Token Economics & Cost Analysis

### 5.1 Token Cost per Tool Definition

Tool schemas are serialized into the model's input context on every call. There is no "free metadata."

| Tool Complexity | Tokens per Tool (Claude) | Tokens per Tool (OpenAI) |
|---|---|---|
| Minimal (1 param, brief desc) | ~12 tokens | ~15 tokens |
| Production (multi-param, detailed desc) | ~30-65 tokens | ~40-80 tokens |
| Complex (nested objects, enums, examples) | ~80-160 tokens | ~100-200+ tokens |

Claude tokenizes at ~0.8x the rate of GPT-4 (cl100k_base).

**Scaling formula:** `Per-request tool overhead = N_tools x avg_tokens_per_tool`

Example: 20 tools x 60 tokens avg = 1,200 tokens overhead per request.

**Real-world ceiling.** A setup with GitHub, Slack, Sentry, Grafana MCP servers: ~55,000 tokens in tool definitions before the model sees a single user instruction. Systems with 50+ tools often hit 50-60% overhead.

**Hidden system tokens (Anthropic).** Every Anthropic request with `tools` injects a hidden tool-use system prompt:

| Tool choice mode | Hidden tokens |
|---|---|
| `auto` or `none` | **354** tokens |
| `any` or `tool` (specific) | **474** tokens |
| `computer_toolset_20260801` members | ~4,590 tokens |
| `browser_toolset_20260801` members | ~6,670 tokens |

These are on top of your schema tokens, even with one empty tool.

**Input examples cost-benefit.** ~20-50 tokens per example. First example improves accuracy from ~80% to ~92%; fifth example barely moves the needle.

### 5.2 Cost per 1k Tool-Using Questions

Define a tool-using user question as: 1 planning model-call (emits tools) + 1 execution of 2 parallel REST tools + 1 synthesis model-call. No thinking. Sonnet 5 with 5-minute cache on the stable prefix.

| Shape | Model-calls | $ / 1k questions |
|---|---|---|
| Happy path, cache warm | 2 | **$19.14** |
| + 20% one-shot schema retry | 2.2 | **$21.06** |
| Uncached (no prefix cache hit) | 2 | **$33.42** |
| Computer-use prefix uncached | 2 | **$35.78** |
| Computer-use prefix cached | 2 | **$17.98** |

**Schema-invalid retry increment:** Cached 20-tool prefix at $0.00957/turn, plus 400 uncached error tokens + 400 extra output = ~$0.0048 per retry (~50% of a warm turn). If 20% of questions hit one schema retry: **+$0.96/1k questions**. A poison-pill schema at Instructor `max_retries=2` is 3x model calls per question with zero successful tool I/O.

**Cost formula:**

```
C = n * (T_miss * P_miss + T_hit * P_hit + T_write * P_write + T_out * P_out) / 10^6
```

Where T = token counts, P = price per million tokens, n = number of calls.

### 5.3 Cost Optimization Strategies

| Strategy | Token Savings | Implementation Cost |
|---|---|---|
| Dynamic tool gating (20 to 1-2 per request) | ~760 tokens/request (40-50% reduction) | Medium -- requires classifier or embedding-based selector |
| Tool Search / defer_loading (50+ tools) | 77K to 8.7K tokens (89% reduction) | Low -- native API feature |
| Prompt caching (stable tool definitions first) | Cache hit discount (Anthropic: 90% off cached input) | Low -- ordering discipline |
| Remove credentials from schemas | 400-600 tokens across 5 tools | Low -- vault injection at runtime |
| `allowed_tools` subsetting | Avoids prefix cache invalidation | Low -- no schema mutation |
| Cache-friendly ordering (no timestamps in system) | Prevents cache invalidation | Low -- configuration discipline |

**Cache invalidation subtlety.** Changing `tool_choice` invalidates cached message blocks. Tool definitions and system prompts remain cached only if their serialized content is byte-identical. Mid-session ToolSearch unlock that mutates the tools block can still rewrite the prefix.

### 5.4 Latency SLA Targets

| Metric | Single Tool Call | 3-5 Step Chain | Target |
|---|---|---|---|
| p50 | 1-2s | 4-8s | <3s single, <10s chain |
| p95 | 3-5s | 8-15s | <8s single, <20s chain |
| p99 | 5-10s | 15-30s | <15s single, <45s chain |

| Stage | Published / Documented | Notes |
|---|---|---|
| OpenAI SDK timeout | 600s request, 5s connect | Ceiling, not SLO (footgun: 600 x 3 retries = 30 min) |
| vLLM first named-function FSM | "several seconds" then cached | Cold schema penalty |
| Anthropic grammar cache | 24h since last use | Cold vs warm strict mode |
| Temporal Activity `StartToClose` | 30s for LLM (cookbook) | Your SLO to configure |

**Parallel wins:** Independent tools make wall-clock = slowest tool + model, not the sum. LLMCompiler: up to 3.7x latency reduction.

### 5.5 Throughput and Back-Pressure

LLM RPM/TPM is one bucket. Tool backends are a second:

| Backend | Public Figure | Implication |
|---|---|---|
| Stripe | Test and live request limits are dashboard-specific; keys expire >=24h | Agent fan-out of `create PaymentIntent` will 429 Stripe while OpenAI still has TPM left |
| MCP `tools/list` | Paginated; AgentCore example 30 tools/page | Missing page 2 looks like "hallucinated tool" |
| OpenAI prompt cache routing | >~15 req/min per `prompt_cache_key` overflows routing | Partition by tenant |
| vLLM | `--max-num-seqs` (guides use 4 on 70B) | Tool-call JSON decode shares same seq slots |

**Bulkhead sizing:** 100 concurrent agents x 4 parallel tools = 400 in-flight HTTP. Size pools to that, not to LLM RPM. One `Semaphore` per downstream class (payments, CRM read, MCP, sandbox).

**Back-pressure design:**
1. Admit iff LLM breaker is closed/half-open AND tool-class breaker AND both semaphores have room.
2. 429 + `Retry-After` on Stripe sleeps that bulkhead; do not steal the CRM pool.
3. Shed: disable parallel writes first; then HITL the mutating tool; then deterministic schema-valid decline JSON.
4. Agent fleets: budget N_rounds x (TTFT + T_out/TPOT + T_tool). Cap rounds (`DEFAULT_MAX_TURNS=10`). Loop detector on `(tool, args_hash)`.

### 5.6 BFCL Benchmark

**Berkeley Function Calling Leaderboard (BFCL) v4** (April 2026) evaluation: Agentic (40%), Multi-Turn (30%), Live (10%), Non-Live (10%), Hallucination (10%, 1,122 samples).

**BFCL v3 top scores** (June 2026):

| Model | Score |
|---|---|
| GLM 4.5 | 76.7% |
| Claude Opus 4.7 | 76.6% |
| Gemini 3.1 Flash Lite Preview | 76.5% |

**Quality caveat.** Epoch AI audit found defects in 48% of a random sample of 50 tasks (from 5,088 scored items). Frontier closed-API vs top open-weight gap: 3-4 percentage points. Do not cite BFCL scores as proof of production readiness.

---

## 6. Production Patterns & Code

### 6.1 Pydantic Schema Compiler and Tool Registry

```python
"""Schema compiler + tool registry. Compiles Pydantic BaseModel into
provider-strict JSON Schema (additionalProperties: false, all keys required).
Generates definitions for both Anthropic and OpenAI formats.

Python 3.11+.
"""
from __future__ import annotations

import json
import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Walk the JSON Schema tree and enforce the strict subset:
    - Every object: additionalProperties = false
    - Every property: listed in required
    - Recurse into nested objects, arrays, $defs
    """
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


def build_anthropic_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    *,
    defer_loading: bool = False,
    cache_control: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build Anthropic-format tool definition.
    NEVER set both defer_loading and cache_control (causes 400).
    """
    if defer_loading and cache_control:
        raise ValueError("defer_loading + cache_control = 400 every request")
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": input_model.model_json_schema(),
    }
    if defer_loading:
        tool["defer_loading"] = True
    if cache_control:
        tool["cache_control"] = cache_control
    return tool


def build_openai_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Build OpenAI-format tool definition with strict mode.
    strict: true requires additionalProperties: false on every nested object
    and all fields in required.
    """
    schema = strict_json_schema(input_model) if strict else input_model.model_json_schema()
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": schema,
        "strict": strict,
    }


# --- Example tool arg models ---

class CreatePaymentArgs(BaseModel):
    """Payment tool: extra=forbid catches hallucinated keys like 'bcc'."""
    model_config = ConfigDict(extra="forbid")
    invoice_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)  # Business constraint: gt=0 lives in Pydantic, not grammar
    currency: str = Field(min_length=3, max_length=3)
    # Model may fill these but dispatcher overwrites from session principal:
    customer_id: str | None = None
    idempotency_key: str | None = None


class LookupAccountArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)  # Cap in adapter; model will pass 10000
```

### 6.2 Tool Dispatcher with Idempotency, RBAC, and Semantic Reask

```python
"""Full async tool dispatcher implementing:
- Session-derived idempotency keys (never model-invented)
- Pydantic extra=forbid validation before any side effect
- RBAC from session principal (never model JSON)
- Two separate retry loops (HTTP jitter vs semantic reask)
- Poison-pill loop detection
- Parallel write disabling around mutating tools
- Always returning every call_id (completeness rule)

Python 3.11+.  Run offline: python tool_dispatcher.py
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
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

# --- Constants matching SDK defaults ---
INITIAL_RETRY_DELAY = 0.5   # OpenAI SDK
MAX_RETRY_DELAY = 8.0       # OpenAI SDK
SDK_DEFAULT_MAX_RETRIES = 2  # OpenAI SDK
SEMANTIC_REASK_CAP = 2       # No sleep; cap then fail closed
MCP_PAGE_SIZE = 30           # AgentCore example
IDEMPOTENCY_TTL_S = 24 * 3600  # Stripe: prune >=24h; reuse after prune = NEW request


# --- Structured logging ---

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


# --- Error classes ---

class TransientError(Exception):
    """Retryable via HTTP jitter (408, 429, 5xx, 529)."""
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status

class PermanentError(Exception):
    """Do NOT failover (400 schema, 401, 403)."""
    pass

class CircuitOpenError(TransientError):
    """Circuit breaker is open; fail-fast."""
    pass


# --- Circuit breaker (per provider+model OR per tool-class) ---

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Do not trip on 429-with-Retry-After (that is throttling, not an outage).
    Trip on 5xx/529/timeout rate exceeding threshold.
    Half-open probe should be a cheap read, not create_payment_intent.
    """
    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
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
        if self._state is BreakerState.OPEN and \
           (time.monotonic() - self._opened_at) >= self.recovery_seconds:
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
            if self._state is BreakerState.HALF_OPEN or \
               self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


# --- HTTP retry loop (transport only; never wrap ValidationError) ---

T = TypeVar("T")

async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """Full jitter backoff. Honor Retry-After iff 0 < t <= 60.
    PermanentError propagates immediately (never retried).
    """
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
            sleep_s = ra if ra is not None and 0 < ra <= 60 \
                else random.random() * min(cap, base * (2 ** i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


# --- Idempotency ---

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def session_idempotency_key(tenant: str, intent_id: str, tool: str,
                            args: dict[str, Any]) -> str:
    """Stripe-style <=255 chars, no PII. NEVER a model-emitted UUID."""
    material = f"{tenant}|{intent_id}|{tool}|{canonical_json(args)}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    return f"{tenant[:8]}-{intent_id[:8]}-{digest}"[:255]


class IdempotencyStore:
    """Stores status+body of FIRST execution, including 500s.
    Replay >=24h then prune. After prune, reuse = NEW request.
    """
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


# --- Session principal (identity from session, never model JSON) ---

class SessionPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant: str
    user_id: str
    roles: list[str]
    intent_id: str


# --- Tool result ---

class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    name: str
    is_error: bool
    content: dict[str, Any]


# --- Tool registry ---

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
        """Generate OpenAI-format strict tool definitions."""
        return [
            {
                "type": "function",
                "name": spec.name,
                "description": spec.description,
                "strict": True,
                "parameters": strict_json_schema(spec.args_model),
            }
            for spec in self._tools.values()
        ]


# --- MCP catalog with cursor pagination ---

@dataclass
class McpTool:
    name: str
    description: str
    schema_hash: str
    server: str
    scopes: tuple[str, ...]


class McpCatalog:
    """MCP-like tools/list with cursor pagination.
    Re-list ALL pages on list_changed. Page-1-only is the AgentCore bug.
    """
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


# --- Dispatcher ---

@dataclass
class ModelCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


class Dispatcher:
    """Core dispatch engine implementing:
    1. Unknown name -> model-visible allowlist (not a 500)
    2. Pydantic extra=forbid validation before side effects
    3. RBAC check against session principal
    4. Session-derived idempotency keys
    5. Parallel write disabling
    6. Poison-pill loop detection
    7. Always returning every call_id
    """
    def __init__(
        self,
        registry: ToolRegistry,
        catalog: McpCatalog,
        payments_breaker: CircuitBreaker,
        disable_parallel_writes: bool = True,
    ) -> None:
        self.registry = registry
        self.catalog = catalog
        self.payments_breaker = payments_breaker
        self.disable_parallel_writes = disable_parallel_writes
        self._poison: dict[str, int] = {}  # (tool|error_type) -> count
        self.sem_pay = asyncio.Semaphore(32)
        self.sem_crm = asyncio.Semaphore(64)

    def _unknown(self, call: ModelCall) -> ToolResult:
        return ToolResult(
            call_id=call.call_id, name=call.name, is_error=True,
            content={"error": "unknown_tool", "allowlist": self.registry.allowlist()},
        )

    def _rbac(self, principal: SessionPrincipal, spec: ToolSpec) -> None:
        if spec.mutating and "payments.write" not in principal.roles \
           and spec.name.startswith("create_"):
            raise PermanentError("rbac_deny")

    async def _exec_one(self, principal: SessionPrincipal, call: ModelCall,
                        log: CorrelationAdapter) -> ToolResult:
        spec = self.registry.get(call.name)
        if spec is None:
            # Re-list MCP pages once before declaring unknown
            await self.catalog.list_all_pages()
            spec = self.registry.get(call.name)
            if spec is None:
                log.warning("unknown_tool name=%s", call.name)
                return self._unknown(call)
        # Validate with extra=forbid BEFORE any side effect
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            fp = f"{call.name}|validation|{exc.errors()[0]['type']}"
            self._poison[fp] = self._poison.get(fp, 0) + 1
            return ToolResult(
                call_id=call.call_id, name=call.name, is_error=True,
                content={"error": "validation",
                         "details": json.loads(exc.json()),
                         "poison_count": self._poison[fp]},
            )
        # Strip model-supplied identity fields; use session principal
        dumped = args.model_dump()
        dumped.pop("idempotency_key", None)
        dumped.pop("customer_id", None)
        # RBAC check
        try:
            self._rbac(principal, spec)
        except PermanentError as exc:
            return ToolResult(call_id=call.call_id, name=call.name,
                              is_error=True, content={"error": str(exc)})
        # Mint idempotency key from session, never from model
        key = session_idempotency_key(
            principal.tenant, principal.intent_id, spec.name, dumped)
        try:
            result = await spec.handler(principal, args, key)
            return ToolResult(call_id=call.call_id, name=call.name,
                              is_error=False, content=result)
        except TransientError as exc:
            return ToolResult(
                call_id=call.call_id, name=call.name, is_error=True,
                content={"error": "transient", "status": exc.status, "msg": str(exc)},
            )

    async def dispatch(self, principal: SessionPrincipal, calls: list[ModelCall],
                       log: CorrelationAdapter) -> list[ToolResult]:
        """Dispatch tool calls. Sequential if mutating tools present and
        disable_parallel_writes is True. Always returns one result per call_id.
        """
        mutating = [c for c in calls
                    if (s := self.registry.get(c.name)) is not None and s.mutating]
        sequential = self.disable_parallel_writes and bool(mutating) and len(calls) > 1
        if sequential:
            log.info("parallel_writes_disabled count=%s", len(calls))
            return [await self._exec_one(principal, c, log) for c in calls]
        # Parallel: always catch and return every ID
        async def _one(c: ModelCall) -> ToolResult:
            try:
                return await self._exec_one(principal, c, log)
            except Exception as exc:
                log.error("exec_fail call_id=%s err=%s", c.call_id, exc)
                return ToolResult(call_id=c.call_id, name=c.name,
                                  is_error=True, content={"error": "internal"})
        return list(await asyncio.gather(*[_one(c) for c in calls]))

    async def semantic_loop(
        self,
        principal: SessionPrincipal,
        proposed: list[ModelCall],
        log: CorrelationAdapter,
        repair: Callable[[list[ToolResult]], list[ModelCall]] | None = None,
    ) -> list[ToolResult]:
        """Semantic reask loop: NO sleep. Cap at SEMANTIC_REASK_CAP.
        HTTP jitter lives in handlers, not here.
        Poison detection: stop after 3 identical (tool, error_type) fingerprints.
        """
        current = proposed
        last: list[ToolResult] = []
        for attempt in range(SEMANTIC_REASK_CAP + 1):
            last = await self.dispatch(principal, current, log)
            if not any(r.is_error and r.content.get("error") == "validation"
                       for r in last):
                return last
            if any((r.content.get("poison_count") or 0) >= 3 for r in last):
                log.error("poison_pill_stop")
                return last
            if repair is None or attempt == SEMANTIC_REASK_CAP:
                return last
            log.info("semantic_reask attempt=%s", attempt + 1)
            current = repair(last)
        return last


# --- Fallback chain: primary -> secondary -> deterministic decline ---

def deterministic_decline(calls: list[ModelCall]) -> list[ToolResult]:
    """Schema-valid, no-charge fallback when both LLM providers are down."""
    return [
        ToolResult(call_id=c.call_id, name=c.name, is_error=True,
                   content={"status": "degraded", "error": "fallback"})
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
            # PermanentError does NOT failover (400 schema fails everywhere)
            await self.breaker.record_failure(trip=False)
            log.error("llm_permanent_no_failover err=%s", exc)
            raise
        try:
            return await retry_with_jitter(self.secondary, log=log)
        except (TransientError, PermanentError) as exc:
            log.error("degraded_deterministic err=%s", exc)
            raise PermanentError("degraded") from exc
```

### 6.3 Dynamic Tool Registry with Progressive Disclosure

```python
"""Dynamic tool registry supporting defer_loading and progressive disclosure.

Tools marked as deferred are excluded from the rendered tools array sent to
the LLM, preserving prompt cache. They load on demand when discovered via
tool search. Tool selection accuracy degrades with 30+ tools loaded at once.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


class ToolRegistryEntry(BaseModel):
    name: str
    description: str
    input_model_schema: dict[str, Any]
    category: str = "general"
    tags: list[str] = []
    deferred: bool = False
    schema_version: str = "1.0.0"


class DynamicToolRegistry:
    def __init__(self, max_active_tools: int = 10) -> None:
        self._entries: dict[str, ToolRegistryEntry] = {}
        self._handlers: dict[str, Any] = {}
        self._active_tools: set[str] = set()
        self.max_active_tools = max_active_tools

    def register(
        self, name: str, description: str, input_model: type[BaseModel],
        handler: Any, *, category: str = "general",
        tags: list[str] | None = None, deferred: bool = False,
    ) -> None:
        self._entries[name] = ToolRegistryEntry(
            name=name, description=description,
            input_model_schema=input_model.model_json_schema(),
            category=category, tags=tags or [], deferred=deferred,
        )
        self._handlers[name] = handler
        if not deferred:
            self._active_tools.add(name)

    def get_active_tool_definitions(self, provider: str = "anthropic") -> list[dict[str, Any]]:
        """Non-deferred tools only. Deferred tools excluded to preserve cache."""
        definitions = []
        for name in self._active_tools:
            entry = self._entries[name]
            if provider == "anthropic":
                definitions.append({
                    "name": entry.name, "description": entry.description,
                    "input_schema": entry.input_model_schema,
                })
            elif provider == "openai":
                schema = dict(entry.input_model_schema)
                schema["additionalProperties"] = False
                definitions.append({
                    "type": "function", "name": entry.name,
                    "description": entry.description,
                    "parameters": schema, "strict": True,
                })
        return definitions

    def search_tools(self, query: str, max_results: int = 5) -> list[ToolRegistryEntry]:
        """Search across ALL tools (including deferred) by name, description, tags.
        Production: replace with BM25 or embedding-based search.
        """
        query_lower = query.lower()
        query_terms = re.split(r"\s+", query_lower)
        scored: list[tuple[float, ToolRegistryEntry]] = []
        for entry in self._entries.values():
            searchable = f"{entry.name} {entry.description} {' '.join(entry.tags)}".lower()
            score = sum(1.0 for term in query_terms if term in searchable)
            if query_lower in entry.name.lower():
                score += 3.0  # Boost exact name match
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def activate_tool(self, name: str) -> bool:
        """Load a deferred tool into the active set (progressive disclosure)."""
        if name not in self._entries or len(self._active_tools) >= self.max_active_tools:
            return False
        self._active_tools.add(name)
        return True

    def deactivate_tool(self, name: str) -> None:
        self._active_tools.discard(name)
```

### 6.4 Structured Observability for Tool Invocations

```python
"""Structured logging for tool invocation observability.

Each invocation emits a discrete structured log entry compatible with
OpenTelemetry span semantics. Sensitive fields (password, token, secret,
api_key, credential) are redacted before logging.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Generator

SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "credential"}


@dataclass
class ToolInvocationSpan:
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = ""
    tool_use_id: str = ""
    caller_identity: str = ""
    tenant_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    success: bool = False
    error_type: str | None = None
    error_message: str | None = None
    result_size_bytes: int = 0
    circuit_breaker_state: str = "closed"


class ToolObservabilityLogger:
    def __init__(self, service_name: str = "tool-gateway") -> None:
        self._logger = logging.getLogger(f"{service_name}.tool_invocation")
        self._service_name = service_name

    @contextmanager
    def span(
        self, trace_id: str, tool_name: str, tool_use_id: str,
        parameters: dict[str, Any], caller_identity: str = "",
        tenant_id: str = "",
    ) -> Generator[ToolInvocationSpan, None, None]:
        invocation = ToolInvocationSpan(
            trace_id=trace_id, tool_name=tool_name, tool_use_id=tool_use_id,
            caller_identity=caller_identity, tenant_id=tenant_id,
            parameters={k: "***REDACTED***" if k.lower() in SENSITIVE_KEYS else v
                        for k, v in parameters.items()},
            start_time=time.time(),
        )
        try:
            yield invocation
        except Exception as exc:
            invocation.success = False
            invocation.error_type = type(exc).__name__
            invocation.error_message = str(exc)[:500]
            raise
        finally:
            invocation.end_time = time.time()
            invocation.duration_ms = (invocation.end_time - invocation.start_time) * 1000
            log_data = {"event": "tool_invocation", "service": self._service_name,
                        **asdict(invocation)}
            level = logging.INFO if invocation.success else logging.WARNING
            self._logger.log(level, json.dumps(log_data, default=str))
```

---

## 7. Failure Modes & Mitigations

### 7.1 Failure Taxonomy

| Class | Examples | Handler |
|---|---|---|
| **Transient** | 408, 409, 429 with Retry-After, 500, 503, 529, TLS reset, MCP disconnect | Full jitter; same idempotency key; cache last-good MCP catalog |
| **Permanent** | 400 schema (unsupported keywords, `defer_loading`+`cache_control`), 401/403, 404, 413, spend-cap 429, `refusal` | Fail the turn; do NOT failover schema 400s (will fail everywhere) |
| **Poison pill** | Always-invalid schema (fine-tune dropped `format: date` but Pydantic `date` remains; enum >1,000 values; MCP `inputSchema: null`; vLLM Hermes `</tool_call>` inside JSON string) | After N identical failures, stop reasking; HITL / DLQ; loop detector on `(tool, error_type)` |
| **Semantic** | Schema-valid but unauthorized refund; injection in `tool_result`; hallucinated `order_id: "12345"` | RBAC + canonical ID lookup; not a retry problem |
| **Partial side effect** | Timeout after Stripe accepted POST; one of N parallel IDs missing | Same idempotency key; always return N `tool_result`s |
| **Hallucinated name** | `create_charge` vs `create_payment_intent` (3-15% of production calls) | Model-visible allowlist error; Anthropic `strict: true` guarantees name is in tools; Gemini `ANY` + `allowed_function_names` |

### 7.2 Infinite Loops

IAL-Scan examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects (91.9% precision). This is a systemic pattern being shipped to production. Causes:
- Ambiguous tool responses ("Found 2 flights, more may be available" -- no terminal signal)
- Vague goal definitions ("help the user")
- Model retrying the same failed tool call with identical parameters

**Prevention:** Iteration caps (10-25), per-tool debounce (block third identical call), clear terminal states in tool responses, budget guardrails, loop detector on `(tool, args_hash)`.

### 7.3 Cascading Failures

A hallucinated output in step 2 becomes a malformed input in step 3. By step 5, the agent operates on a premise that was never true. Five agents at 95% individual accuracy deliver ~77% overall success rate. Multi-agent systems show failure rates between 41% and 86.7%.

### 7.4 Context Degradation (Silent Failure)

Over long sessions, the agent's internal representation of the original task compresses, earlier constraints get deprioritized, and the agent reasons against a progressively incomplete picture. No exception fires. The system reports healthy. This is the hardest failure mode to detect.

### 7.5 Model Refusing Available Tools

The model answers from training data instead of calling a relevant tool. Causes: unclear tool descriptions, training biases, too many tools creating selection ambiguity. Mitigation: `tool_choice: "required"`, detailed descriptions (3-4+ sentences), explicit user-message instructions.

### 7.6 Circuit Breaker Pattern

One breaker per (provider, model) for LLM AND one per tool-class (payments / CRM / MCP). Never one global breaker that kills failover.

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ---------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                       | OPEN |
  +---+----+  429 with Retry-After = throttle      +--+---+
      |       (stay CLOSED; sleep)                    | timer (e.g. 30s)
      | success resets window                         v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | read     |
                                                 +----------+
```

**Fallback chain:** Primary model -> secondary vendor (same compiled IR) -> deterministic schema-valid decline (`status: "degraded"`, no charge). Tool-class open: do not invent a second processor; queue / HITL. **PermanentError on schema does not failover.**

**Retry amplification:** LLM timeout 60s -> tool HTTP 55s -> both retry -> storm. Fix: one retry owner; turn cap; loop detector; nested timeouts strictly decreasing.

---

## 8. Security & Governance

### 8.1 Zero-Trust MCP (RFC 8707)

MCP 2025-11-25 remote servers are OAuth 2.1 resource servers. Requirements:
- RFC 9728 Protected Resource Metadata
- **RFC 8707** resource indicator (absolute URI, no fragment) on authorization and token requests
- PKCE, no implicit grant, exact redirect URIs
- MUST validate audience
- MUST NOT token-passthrough to upstream APIs (confused deputy)
- Proxies MUST implement per-client consent
- Local STDIO: env credentials, not OAuth
- HTTP localhost: bind `127.0.0.1`, validate `Origin`/`Host`

**DNS rebinding CVEs:** TypeScript SDK CVE-2025-66414, Python SDK CVE-2025-66416. Protection off by default for unauthenticated localhost HTTP; enable `enableDnsRebindingProtection` / `TransportSecuritySettings`. Patched defaults in TS 1.24.0 / Python 1.23.0. Pin DNS between allowlist check and connect (TOCTOU).

**Gateway pattern:** Terminate OAuth at the gateway (audience-bound token for the gateway). Token exchange (RFC 8693) to upstream; never passthrough. Pin MCP manifests (hash descriptions + schemas) against rug pulls / tool poisoning.

### 8.2 Tool RBAC and Identity

**Never take identity from model JSON.** `user_id`, `tenant_id`, `role`, `Authorization`, `account_id` in tool arguments are untrusted. Bind identity from the control-plane principal (session JWT, MCP access token, Temporal `info` memo) before the adapter. The model may pass a resource ID the principal is allowed to name; the adapter re-checks ACL.

RBAC belongs in the dispatcher, not the system prompt (prompts are injectable). Map `(principal, tenant, tool, args_shape)` to allow / deny / HITL. High-impact tools: payments, email send, shell/browser access, CRM deletes -> `needs_approval` / HITL.

**Rate limiting per tool risk tier:**

| Tier | Examples | Suggested RPM | Gate |
|---|---|---|---|
| Read-only | `lookup_account`, `get_weather` | 100 RPM | None |
| Write | `create_note`, `update_record` | 10 RPM | None |
| Destructive | `delete_record`, `transfer_funds`, `send_email` | 2 RPM | Human approval |

### 8.3 Input and Output Validation

**Input validation (beyond schema):**
- Type checking (model sometimes returns `"2"` instead of `2`)
- Enum constraint enforcement (prevents hallucinated parameter values)
- Input normalization (strip zero-width characters, normalize whitespace)
- Range/length validation for numeric and string parameters
- HTTP tools: deny `169.254.169.254`, RFC1918, localhost, metadata IPv6; pin DNS (SSRF prevention)
- GraphQL: persisted-query allowlist
- SQL tools: never `execute_sql`; parameterized ops only

**Output validation (indirect prompt injection defense).** OWASP LLM Top 10 ranks prompt injection as LLM01 (2025). May 2026 survey: prompt injection present in 73% of production AI deployments.
- Content segregation: never concatenate raw tool output directly into system prompt without structural delimiter
- Dual-LLM pattern: quarantined model reads untrusted `tool_result`; privileged model holds tools
- Output scanning: LLM Guard (Protect AI), Lakera Guard, Azure AI Content Safety
- Typed tool calls via MCP reduce injection surface by structuring data flow

**Execution sandboxing options:** Docker (per-tool container isolation, resource limits, network policies), WASM (lightweight, portable, fast cold-start), Subprocess with cgroups (Linux-level resource isolation), Anthropic server tools (sandboxed on Anthropic infrastructure).

### 8.4 PII in Args/Results and WORM Audit

Tool args (SSN, PAN, email) and results (CRM dumps) re-enter the window and the prompt cache. Redact before inject; Anthropic tool-result clear default trigger at 100k tokens, keep last 3. Cap `limit` in the adapter regardless of schema. Results >20k spill to file + 10-line preview. Do not log raw `tool_result` to third-party traces without a BAA.

**Detect -> Redact -> Audit pipeline:**
1. DLP at the executor before persist/log
2. Stable placeholders so cache prefixes stay stable
3. WORM log of placeholder -> hash, not plaintext

**Immutable audit record:** `tool_name`, `call_id`/`tool_use_id`, principal, hashed args, policy decision, downstream status, latency, idempotency key, MCP server hash, breaker state, `correlation_id`. Temporal Event History is a natural second copy.

### 8.5 Durable Execution (Temporal / Kafka)

Application state is NOT KV cache. Every LLM call and every tool I/O is a Temporal Activity. The Workflow is the deterministic loop.

- **Replay safety:** Activity returns a structured `ModelTurn`/`ToolTurn` already sampled -- never "call the model again" inside a replay-unsafe closure. Non-determinism (`uuid4`, `time.time`, temperature) belongs inside the Activity.
- **At-least-once vs at-most-once:** Activities are at-least-once by default (unlimited retries). `maximumAttempts=1` on a non-local Activity is at-most-once (zero times possible). Exactly-once requires the downstream idempotency store, not Temporal. `workflowRunId + activityId` is stable across Activity retries -- that is legal key material.
- **Distributed locking:** `workflow-id = tenant:thread_id` so two gateways cannot run the same agent loop.
- **Dead-letter:** After `max_attempts` on transient failure, or immediately on poison, route to DLQ / HITL.

**Kafka / outbox pattern:**
- Topics: `tool.intents` (intent + key before side effect), `tool.results`, `stripe.webhooks` -> Signal the Workflow, `tool.dlq`
- Compaction on `intent_id` keeps a snapshot; the full log is chain-of-custody
- Poison messages: skip + alert after N handler crashes; do not block the partition
- Workflows call HTTP directly = breaks replay; Kafka client inside Workflow code = breaks replay

---

## 9. System Design Scenarios

### 9.1 Payments + CRM Copilot with Stripe-Style Idempotency

**Problem.** Multi-tenant copilot: 10k tenants, peak 100 concurrent agents, each tool-using question = 1 planning call + 2 parallel REST tools (`create_payment_intent`, `lookup_account`) + 1 synthesis call. Constraint: zero duplicate charges on timeout/500/Activity retry; no cross-tenant CRM reads; p95 wall <8s. Cost envelope: cache-warm $19.14/1k questions; uncached $33.42/1k; 20% schema-fail adds +$0.96/1k. HITL on refund and amount-over-threshold.

```
                    +------------------------------------------------------+
                    | EDGE  auth, tenant TPM, correlation-id, PII redact    |
                    | Idempotency-Key on YOUR public POST (not model's)     |
                    +----------------------------+-------------------------+
                                                 |
                    +----------------------------v-------------------------+
                    | CONTROL  Temporal workflow = tenant:thread            |
                    |  Activity LLM: strict tools; allowed_tools=f(intent) |
                    |            SDK max_retries=0; parallel_tool_calls=    |
                    |            false whenever mutating payment attached   |
                    |  Activity ToolDispatcher:                             |
                    |    Pydantic extra=forbid -> RBAC(principal) ->        |
                    |    key=hash(tenant, intent_id, tool, args) ->         |
                    |    Stripe/CRM                                        |
                    |  CircuitBreaker(payments) != CircuitBreaker(crm)      |
                    |  semantic reask <=2; poison fingerprint -> HITL       |
                    +-----+-------------------------------+----------------+
                          |                               |
                          v                               v
                    +------------------+            +-------------------------+
                    | DATA  Generation |            | DATA  Executor          |
                    | Sonnet 5 /       |            | Stripe adapter: SAME    |
                    | GPT-5.4 failover |            | key after 500; CRM cap  |
                    | prefix cached    |            | limit<=100              |
                    +--------+---------+            +----------+--------------+
                             |                                 |
                    +--------v---------+            +----------v--------------+
                    | TOOL PROXIES     |            | PERSIST  saga_state     |
                    | identity=session |            | idempotency >=24h       |
                    | never model JSON |            | Kafka: Stripe webhooks  |
                    | HITL refund      |            |   -> Signal Workflow    |
                    +------------------+            | WORM hashed args        |
                                                    +-------------------------+
```

**Trade-off evaluation:**

| Dimension | A: Model-invented UUID keys, parallel on, nested retries | B: Session-hash keys, disable parallel writes, one retry owner (Recommended) | C: Hosted MCP with token passthrough |
|---|---|---|---|
| **Cost/1k** | Uncached $33.42 + duplicate charges + schema-retry if extras accepted then Stripe 400 | Cached $19.14; +$0.96 only if 20% still fail closed | Same LLM $ plus MCP prefix tax; reconnect busts cache |
| **Latency** | Duplicate calls; nested 60s x 55s timeouts = p99 storms | Wall = model + one Stripe RTT; REST p95 <800ms | Extra OAuth/MCP hop; tools/list on hot path |
| **Security** | Model JSON as customer_id; extras ignored; keys may contain email | Principal from session; >=24h store including 500s; WORM hashed args | Token passthrough = confused deputy |
| **Scalability** | 100 agents x 4 parallel = 400 Stripe POSTs; dashboard 429 | Bulkhead 32 payments / 64 CRM; LLM RPM is not the ceiling | MCP pagination + disconnect = prefix miss storm |

**Decision.** B is the only option that treats Stripe as an at-least-once Activity against a >=24h store. A fails the money exam. C fails Zero-Trust. Payments catalogs are small -- inline + freeze order + `allowed_tools`.

### 9.2 MCP Gateway with Deferred Schemas + Catalog Search

**Problem.** Platform team fronts 50 MCP servers, thousands of tools, per-tenant OAuth. Constraint: no 55k-token prefix, no page-2 dropouts, no token passthrough, p95 TTFT still cache-hit on a frozen search-tool + hot-tool prefix. Hallucinated names must become model-visible allowlist, not a 500.

```
  +-------------+    +-----------------------------------------------------+
  | Copilot /   |--->| CONTROL  MCP Gateway (resource server, RFC 8707)     |
  | IDE agents  |    |  1. Terminate OAuth; RFC 8693 exchange to upstream   |
  |             |    |     NEVER passthrough                                |
  |             |    |  2. tools/list ALL pages on connect + list_changed   |
  |             |    |     Redis {name, hash(desc+schema), server, scopes}  |
  |             |    |  3. Per-turn allowlist = scopes ^ tenant policy ^    |
  |             |    |     allowed_tools                                    |
  |             |    |  4. Expose to model:                                 |
  |             |    |     Anthropic: tool search + defer_loading; 3-5 hot  |
  |             |    |     NO cache_control on deferred tools               |
  |             |    |     OpenAI: namespaces (<10 fns) + tool_search       |
  |             |    |  5. tools/call: pin hash (rug-pull), SSRF filter,    |
  |             |    |     timeout, host maps -32602 -> allowlist is_error  |
  +-------------+    +----------+-----------------------------+------------+
                                |                             |
                                v                             v
                    +---------------------+     +-----------------------------+
                    | DATA  Generation    |     | TOOL PROXIES  50 MCP        |
                    | schemas at END of   |     | servers; last-good catalog  |
                    | window on search hit|     | TTL; Origin/Host on         |
                    | (prefix cache lives)|     | localhost; DNS pin TOCTOU   |
                    +----------+----------+     +--------------+--------------+
                               v                               v
                    +-----------------------------------------------------+
                    | PERSIST  Redis catalog + WORM + OBJECT STORE for    |
                    |          oversized I/O (>20k -> 10-line preview)    |
                    +-----------------------------------------------------+
```

**Trade-off evaluation:**

| Dimension | A: Inline all schemas, page 1 only | B: Gateway OAuth + Redis catalog + defer_loading + tool search (Recommended) | C: Names-on-disk only (Cursor-style), no hash pin |
|---|---|---|---|
| **Cost/1k** | 55k tokens every turn; far worse than $33.42 | Search >85% cut; hot 3-5 tools stay in cached prefix | Token win similar to Cursor A/B if agent reads files |
| **Latency** | Prefill-bound TTFT; selection degrades >30-50 tools | Prefix = search + hot tools; schemas append at end of window | Extra read round-trips; variance with MCP count |
| **Security** | Huge schema enum leak; token passthrough tempting | RFC 8707 audience; no passthrough; pin hashes; SSRF filter | No pin = tool poisoning; unknown name may 500 host |
| **Scalability** | 400 on defer_loading+cache_control if someone "fixes cache" | Callable set held in 30-50; catalog thousands in Redis | Agent fleets that never read schema file call wrong arity |

**Decision.** B is the only design that treats discovery as a control-plane catalog (all pages, pinned hashes, audience-bound tokens) and the window as a working set (search + 3-5 hot tools). A is the 55k-token interview fail. C wins tokens when the product is Cursor, but without host mapping a hallucinated name never becomes an allowlist `tool_result`.

### 9.3 Enterprise Tool Gateway for Multi-Team AI Platform

**Problem.** Financial services company with 12 product teams deploying LLM agents. 200+ tools across teams. Requirements: centralized governance, per-team cost attribution, SOC 2 audit trail, tool-level access control, sub-3-second p95 latency.

```
+------------------------------------------------------------------------+
|                         API GATEWAY (Kong/Envoy)                        |
|  JWT validation | TLS termination | Global rate limit | Request routing |
+--------------------------------------+---------------------------------+
                                       |
                                       v
+------------------------------------------------------------------------+
|                        TOOL GATEWAY SERVICE                             |
|                                                                         |
|  +-------------+  +--------------+  +---------------+                  |
|  | RBAC Engine  |  | Tool Registry|  | Token Budget   |                  |
|  | (SCIM sync   |  | (200+ tools, |  | Controller     |                  |
|  |  from IdP)   |  |  versioned)  |  | (per-team cap) |                  |
|  +------+------+  +------+-------+  +-------+-------+                  |
|         |                |                   |                           |
|  +------+----------------+-------------------+---------+                |
|  |              Tool Selection Engine                   |                |
|  |  Filter: RBAC -> 200 tools to team's 15-30          |                |
|  |  Gate: embedding-based relevance -> 2-5 tools       |                |
|  |  Defer: remaining tools discoverable via search      |                |
|  +------------------------+----------------------------+                |
|                           |                                              |
|  +------------------------+----------------------------+                |
|  |           Dispatch + Execution Layer                 |                |
|  |  Circuit breakers | Sandboxed execution | Retries    |                |
|  |  Idempotency keys | Credential injection | Timeout   |                |
|  +------------------------+----------------------------+                |
|                           |                                              |
|  +------------------------+----------------------------+                |
|  |         Observability + Audit Pipeline               |                |
|  |  OpenTelemetry traces | Per-tool SLIs | Cost meter   |                |
|  |  SOC 2 audit log | Alerting (PagerDuty)              |                |
|  +-----------------------------------------------------+                |
+------------------------------------------------------------------------+
```

**Trade-off evaluation:**

| Dimension | A: Centralized Gateway (Recommended) | B: Per-Team Sidecar Proxies | C: SDK-Only (no gateway) |
|---|---|---|---|
| **Cost** | Medium -- one service to operate | High -- N sidecars, N configs | Low -- no infra |
| **Latency overhead** | +5-15ms per call | +2-5ms (co-located) | ~0ms (in-process) |
| **Security posture** | Strong -- single enforcement point, consistent audit | Medium -- each sidecar must be correctly configured | Weak -- no central policy |
| **Compliance (SOC 2)** | Strong -- centralized audit log, SCIM-driven RBAC | Achievable with log aggregation | Difficult -- no central audit trail |
| **Scalability** | High -- horizontal scaling | Medium -- bounded by pod resources | High -- scales with app |

**Decision.** Centralized gateway wins: SOC 2 requires a single auditable enforcement point; +5-15ms latency is irrelevant against 500-2000ms LLM inference; 200+ tools with per-team RBAC is intractable without centralized registry. Tool selection engine (RBAC filter -> embedding gate -> defer remainder) keeps per-request token overhead to 2-5 tools regardless of total count.

---

## 10. Interview Quick Reference

### Interview Traps (Fail These, Fail the Round)

- Sending Pydantic `model_json_schema()` verbatim into OpenAI `strict: true` (`$defs`, open objects, `Optional` omitted from `required`) -> 400
- Nested Chat Completions tool shape on Responses (GPT-6 Astra requires Responses) -> `invalid_request_error`
- Executing on `input_json_delta.partial_json` / `function_call_arguments.delta` / Anthropic `input: {}` at `content_block_start`
- Missing one parallel `tool_result` (Anthropic 400: `tool_use ids were not found`)
- `disable_parallel_tool_use` as a top-level Anthropic field -- it lives inside `tool_choice`
- `defer_loading: true` AND `cache_control` on the same Anthropic tool -> 400 every request
- MCP `tools/list` page 1 only (AgentCore paginates at 30/page) -> tools 31+ look "hallucinated"
- Idempotency UUID invented by the model (or a new key after Stripe 500) -> duplicate PaymentIntent
- Exponential backoff on `ValidationError` (same args fail identically); semantic-retry of a 429 without sleep
- Identity / `Authorization` / `tenant_id` taken from tool arguments instead of the session principal
- `extra="ignore"` at the dispatcher: hallucinated `bcc:` on `send_email` silently dropped or accepted
- Token-passthrough from MCP gateway to Stripe (confused deputy; RFC 8707 audience required)
- Gemini 3: omitting `thought_signature` on the first `functionCall` of a step -> 400

### Key Numbers to Memorize

| Number | What |
|---|---|
| **$19.14 / $33.42 / $21.06** | Sonnet 5 tool-using question $/1k cached / uncached / +20% schema retry |
| **+$0.96 / 1k** | 20% one-shot ValidationError reask on cached 20-tool prefix |
| **354 / 474** | Sonnet 5 hidden tool-use tokens auto/none vs any/tool |
| **~4,590 / ~6,670** | computer / browser toolset definition tokens |
| **~55k -> >85%** | Typical SaaS catalog tokens vs tool-search cut; load 3-5 tools |
| **-46.9%** | Cursor deferred MCP (token cut on MCP-calling runs) |
| **30-50** | Inlined-tool selection-accuracy cliff |
| **30 / page** | AgentCore tools/list page size; page-1-only drops tool 31+ |
| **5,000 / 10 / 1,000** | OpenAI strict properties / nesting / enum cap (2026) |
| **24h** | Anthropic strict-tool grammar cache; Stripe key retention >=24h |
| **0.5s / 8s / retries=2** | SDK HTTP initial delay / cap / max_retries |
| **2-3** | Semantic reask cap then fail closed |
| **10** | OpenAI Agents DEFAULT_MAX_TURNS |
| **<10** | Functions per OpenAI tool-search namespace |
| **100k keep 3 / 20k** | Anthropic tool-result clear / Deep Agents offload preview |
| **10% / 1,122** | BFCL V4 hallucination weight / samples |
| **CVE-2025-66414 / 66416** | MCP DNS rebinding; patch TS 1.24.0 / Python 1.23.0 |
| **-32602 vs isError** | MCP unknown/invalid args vs execution failure |
| **22.6%** | 5-step chain failure rate at 5% per step |
| **48%** | BFCL task defect rate found by Epoch AI audit |

### Decision Frameworks

1. **Schema design:** "Compile Pydantic to the strict subset, validate again with extra=forbid at execute-time. Grammar is syntax; Pydantic is semantics."

2. **Retry strategy:** "Two loops, never one. HTTP jitter for wires (sleep, same key). Semantic reask for ValidationError (no sleep, e.json() as tool_result, cap 2-3). Never backoff a ValidationError. Never semantic-retry a 429."

3. **Identity:** "Identity from the session principal, never from model JSON. The model may pass a resource ID that the ACL re-checks."

4. **Idempotency:** "Key from (tenant, intent_id, tool, canonical_args_hash). Never from the model. After Stripe 500, same key -- the original may have had side effects."

5. **Discovery:** "MCP is a paginated, audience-bound catalog, not 55k tokens in the system prompt. All pages, pin hashes, tool search for working set of 3-5."

6. **Parallel execution:** "Disable parallel around money. Return every ID. Wall-clock = max(tool RTT), not sum."

7. **Fallback:** "Primary -> secondary vendor (same compiled IR) -> deterministic schema-valid decline. PermanentError does not failover."

### Interview Closer

"I compile Pydantic to the strict subset, allowlist without mutating the cached prefix, validate again with extra=forbid, reask ValidationError at most twice with no sleep, and mint Stripe keys from the session -- never from the model. MCP is a paginated, audience-bound catalog, not 55k tokens in the system prompt. The model is an untrusted planner; the schema compiler and the allowlist live on the control plane; Stripe sees a key I derived from the session. Two retry loops: jitter for wires, reask for ValidationError."
