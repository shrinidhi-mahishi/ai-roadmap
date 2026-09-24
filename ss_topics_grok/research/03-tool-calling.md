# Research: Tool Calling

**Date researched**: 2026-09-23
**Sources consulted**: 97

Vendor list prices, tokenizer tables, SDK HTTP retry constants, and RPM/ITPM/OTPM live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Prefix-stability, cache breakpoints, and the **tool-schema tax** (Sonnet 5 hidden **354** / **474**; `computer_toolset_20260801` ≈ **4,590**) live in [`02-context-engineering.md`](02-context-engineering.md). This file does **not** recopy those tables. It covers the **tool-calling control plane**: JSON Schema compilation, provider strict/validated decoding, Pydantic as the last-mile validator, semantic vs HTTP retry, MCP `tools/list` vs inlined schemas, and recovery from hallucinated names, timeouts, and partial side effects.

Invariant across OpenAI, Anthropic, Gemini, Bedrock, and vLLM: **the model does not execute your tools**. It emits a structured call; **your** dispatcher (or a hosted MCP / server-tool runtime) executes; a correlating ID carries the result back ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling); [Anthropic overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling); [Bedrock tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)).

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

| Plane | Owns | Does not own |
| --- | --- | --- |
| **Control plane** | Tool **allowlist** (per tenant / per turn), **schema compiler** (Pydantic / JSON Schema → provider subset), **RBAC** (principal × tool × arg shape), `tool_choice` / `allowed_tools` / `FunctionCallingConfig`, loop budget (`max_turns`), correlating IDs, idempotency keys, timeout nesting | Transformer weights, KV cache, grammar bitmask |
| **Data plane (model)** | Constrained decode (`strict` / `VALIDATED` / vLLM structural tags) → emit `function_call` / `tool_use` / `functionCall` | HTTP to Stripe, SQL, MCP `tools/call` |
| **Data plane (executor)** | Validate args, authorize, call downstream, map errors to `tool_result` / `function_call_output` / `functionResponse` | Sampling |

Hosted / **server tools** invert the executor: OpenAI built-ins (web search, file search, code interpreter, hosted MCP), Anthropic `web_search` / `web_fetch` / `code_execution` / `tool_search`, Gemini Search / Code Execution run **inside** the provider. Mixed parallel groups are a topology hazard: Anthropic may return `stop_reason: "tool_use"` if a server tool sits in the same batch as a client tool — you must complete client `tool_result`s before the server path continues ([Anthropic overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)). OpenAI GPT-5+: custom functions may parallelize, but **built-ins cannot share a parallel function-call batch** ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)). Gemini 3 mixes built-in + custom via `previous_interaction_id` tool-context circulation ([Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)).

**Dispatcher contract (sync):** parse `tool_calls[]` / `tool_use` / `functionCall` → **do not execute on partial JSON** (OpenAI `response.function_call_arguments.delta`; Anthropic `input_json_delta.partial_json` with `input: {}` placeholder at `content_block_start`; Gemini `stream_function_call_arguments`) ([Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses); [Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming); [Gemini FC](https://ai.google.dev/gemini-api/docs/function-calling)) → JSON Schema / Pydantic validate → RBAC → execute with timeout + idempotency key → inject **all** IDs in one turn.

**Dispatcher contract (async):** Temporal Activity per LLM call **and** per tool; Workflow is the deterministic loop ([Temporal AI reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture)). LangGraph `ToolNode` is in-process parallelism, not a distributed lock around Stripe POST ([ToolNode](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode)).

### 1.2 JSON Schema drafts vs provider subsets

JSON Schema **Draft-07** (`http://json-schema.org/draft-07/schema#`) and **2020-12** both treat `additionalProperties` as an applicator over names **not** matched by sibling `properties` / `patternProperties`. Default is **open** (extra keys allowed). `additionalProperties: false` forbids extras; it does **not** make declared keys present — that is `required` ([Draft-07 validation §6.5.6](https://json-schema.org/draft-07/json-schema-validation); [Understanding JSON Schema — object](https://json-schema.org/understanding-json-schema/reference/object)). Draft 2020-12 adds `unevaluatedProperties` (sees into `allOf` / `$ref`); `items`/`additionalItems` become `prefixItems`/`items` ([2020-12 release notes](https://json-schema.org/draft/2020-12/release-notes)).

**MCP `inputSchema`** defaults to JSON Schema **2020-12** when `$schema` is omitted; it **MUST** be a JSON Schema object (not `null`). Empty-arg tools: `{ "type": "object", "additionalProperties": false }` is the recommended closed empty object; `{ "type": "object" }` accepts any object ([MCP tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).

**OpenAI / Anthropic strict** is a **subset**, not Draft-07/2020-12. Both require:

1. Every object: `additionalProperties: false`.
2. Every key in `properties` listed in `required`.
3. Optionality via `type: ["string", "null"]` (or union with `null`), **not** by omitting from `required`.

Unsupported keywords with `strict: true` → **400**, not best-effort JSON ([OpenAI function calling — strict](https://developers.openai.com/api/docs/guides/function-calling); [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs); [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)). OpenAI documented limits (2026 docs): up to **5,000** object properties total, **10** nesting levels (raised from the older 100 / 5); unsupported composition includes `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`/`then`/`else`. Fine-tunes additionally drop `minLength`/`maxLength`/`pattern`/`format`, numeric bounds, `patternProperties`, `minItems`/`maxItems` ([Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs); [limits raised](https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593)). Azure Foundry copy still cites **100 / 5** — quote the page you ship against ([Azure structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)).

**Gemini** `functionDeclarations[].parameters` is an **OpenAPI-subset** schema, not full JSON Schema. Production MCP adapters strip `$schema`, `additionalProperties`, `$defs` before Gemini or the request 400s (adapter rule documented in agent SDKs; confirm per SDK version) ([Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling); [Enterprise FC](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/function-calling)).

Pydantic v2 `model_json_schema()` / `TypeAdapter.json_schema()` emit Draft 2020-12-ish dicts with `$defs`. **Do not** send that dict verbatim into OpenAI `strict: true` without a compiler that: sets `additionalProperties: false` on every object, puts all keys in `required`, rewrites `Optional[T]` as `anyOf`/`["T","null"]`, and strips unsupported keywords ([Pydantic JSON Schema](https://pydantic.dev/docs/validation/2.12/concepts/json_schema/); [Pydantic LLM intro](https://pydantic.dev/articles/llm-intro)). OpenAI Python `type_to_text_format_param` always sets `"strict": True` when converting a Pydantic type (see 01).

### 1.3 OpenAI function / tools

**GPT-6 Astra requires the Responses API** for tool calling; Chat Completions examples use GPT-5.6 for compatibility ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)). Shape difference (01): Chat Completions nests `{type:"function", function:{name, parameters, strict}}`; Responses is **flat** `{type:"function", name, parameters, strict}` — nested shape is `invalid_request_error`.

| Control | Values | Effect |
| --- | --- | --- |
| `tool_choice` | `"auto"` / `"required"` / `"none"` / `{type:"function", name}` | 0..N / ≥1 / 0 / exactly that function |
| `tool_choice.type: "allowed_tools"` | `mode: "auto"` + subset of names | **Per-turn allowlist without mutating the cached `tools` array** — the cache-preserving control ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)) |
| `parallel_tool_calls` | default on; `false` | **exactly 0 or 1** tool call ([OpenAI staff, Jun 2024](https://community.openai.com/t/new-api-feature-disable-parallel-function-calling-via-parallel-tool-calls-false/805405)) |
| `strict` | Chat: default **off**; Responses: omit → **try** strict, fall back to `strict: false` on the returned tool | `strict: true` + nonconforming schema → **400** |

Fine-tuned models: **parallel calls disable strict for that turn**. Snapshot `gpt-4.1-nano-2025-04-14` can emit **duplicate same-tool** calls if parallel is on — disable it ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)). Injection: `function_call_output` (Responses, match `call_id`) or Chat `role: "tool"` + `tool_call_id`. Result format is **your** string (JSON, error codes, text) ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)).

**Tool search (`gpt-5.4`+ only):** `{type:"tool_search"}` + `defer_loading: true` on functions / MCP servers / namespace members. Hosted search returns `tool_search_call` + `tool_search_output` in the same response; client-executed search (`execution: "client"`) emits `tool_search_call` and you return `tool_search_output`. Loaded tools are appended at the **end of the window** so the prefix cache survives. Namespaces: model sees namespace name+description first; keep **<10** functions per namespace. Individual deferred functions still show **name + description** (schema deferred). Prefer namespaces/MCP over per-function deferral ([Tool search](https://developers.openai.com/api/docs/guides/tools-tool-search); [Using tools](https://developers.openai.com/api/docs/guides/tools)).

### 1.4 Anthropic tools

Client loop: `stop_reason == "tool_use"` → one or more `tool_use` blocks (`id`, `name`, `input`) → execute → **one** user message with **all** `tool_result` blocks **first** (`tool_use_id` match), then optional text. Echo the assistant message **verbatim** (including thinking signatures). Missing a result → 400 ([Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls); [Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).

`tool_choice`: `{type:"auto"|"any"|"tool"|"none"}`. **`disable_parallel_tool_use: true` lives inside `tool_choice`**, not top-level. Effect depends on type (`auto` + disable ⇒ at most one call). Fable 5.1 / Mythos 5.1 **reject** `any` and `type:"tool"` (400); use `auto` + `strict: true` ([tool-use concepts](https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md)).

**`strict: true`** on a tool: grammar-constrained sampling of `input`; **name is always valid**. Computer/browser toolset entries (`computer_toolset_20260801`, `browser_toolset_20260801`) **reject** `strict: true` (400). Grammars compile through the same pipeline as `output_config.format` structured outputs; tool schemas cached up to **24 hours since last use**; prompts/responses not retained beyond the API response ([Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use); [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).

**Errors:** `"is_error": true` on `tool_result` with explanatory `content`. Skipped parallel siblings **must** still get `is_error: true`. Computer/browser member batches: execute **in order**, stop at first failure, skip remainder with the documented skip text ([Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)). Server-tool errors are handled by Anthropic; you do not synthesize `is_error` for them ([Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)).

**Tool search:** `tool_search_tool_regex_20251119` or `tool_search_tool_bm25_20251119`. You still **send every definition** every request; `defer_loading: true` controls what enters the **system-prompt prefix**. At least one tool (the search tool) must stay non-deferred. Keep **3–5** hottest tools non-deferred. Search returns up to **5** `tool_reference` blocks by default (Claude can set `limit`). **Never** set `cache_control` on a deferred tool → 400 `"cannot have both defer_loading=true and cache_control set"`. Grammar for strict mode builds from the **full** toolset, so deferral does not recompile grammars ([Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool); [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching); [Claude Code #30920](https://github.com/anthropics/claude-code/issues/30920)). Anthropic: a typical GitHub+Slack+Sentry+Grafana+Splunk catalog is **~55k** definition tokens; tool search typically cuts that **>85%**, loading 3–5 tools. Selection accuracy degrades past **30–50** inlined tools ([Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).

### 1.5 Gemini functionCall

Two APIs, two ID contracts:

| API | Call identity | Echo |
| --- | --- | --- |
| **Interactions** | `steps[].id` on `function_call` | `function_result.call_id` **must** match ([Gemini FC](https://ai.google.dev/gemini-api/docs/function-calling)) |
| **generateContent** | `functionCall.id` optional; many payloads have `id=None` | Match by **name** on `FunctionResponse`; if `id` is populated, echo it ([FunctionCall JS](https://googleapis.github.io/js-genai/release_docs/interfaces/types.FunctionCall.html); [Enterprise FC reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/function-calling)) |

**Thought signatures (Gemini 3):** omitting `thought_signature` on the first `functionCall` of any step in the current turn → **400**, including `minimal` thinking. Parallel: signature on the **first** `functionCall` part only; sequential multi-step: **each** step’s first call. Do not concatenate or merge signed parts ([Thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures)). Official SDKs replay parts automatically; REST clients must copy raw parts.

**`FunctionCallingConfig.mode`:**

| Mode | Behavior |
| --- | --- |
| `AUTO` | Default when only function declarations; model may answer in text |
| `VALIDATED` | Schema adherence; Gemini 3+ also enforces **required params**; default when mixing built-ins / structured outputs |
| `ANY` | Always emit ≥1 function call; optional `allowed_function_names` allowlist |
| `NONE` | No calls (tools still in prompt unless you drop them) |

Sources: [generateContent FC](https://ai.google.dev/gemini-api/docs/generate-content/function-calling); [Enterprise intro](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/function-calling). `stream_function_call_arguments: true` streams partial args — **do not execute until complete**.

### 1.6 Pydantic v2 as the last-mile validator

Even with provider `strict` / `VALIDATED`, **business constraints** (`gt=0`, `EmailStr`, tenant-scoped IDs, extra-forbid) live in Pydantic. Pipeline:

1. **Compile:** `Args.model_json_schema()` or `TypeAdapter(T).json_schema(mode="validation")` → provider-subset compiler → `tools[]`.
2. **Execute-gate:** `Args.model_validate_json(raw)` or `TypeAdapter(T).validate_json(raw)` **before** any side effect ([TypeAdapter](https://pydantic.dev/docs/validation/2.12/concepts/type_adapter); [Pydantic LLM](https://pydantic.dev/articles/llm-intro)).
3. **Closed objects:** `model_config = ConfigDict(extra="forbid")` → error type `extra_forbidden` (JSON Schema `additionalProperties: false`) ([Validation errors](https://pydantic.dev/docs/validation/2.10/errors/validation_errors); [Config extra](https://pydantic.dev/docs/validation/2.7/api/pydantic/config/)). Default `extra="ignore"` **silently drops** hallucinated keys — dangerous for audit; use `forbid` at the dispatcher.
4. **Retry context:** `ValidationError.errors()` → list of `{type, loc, msg, input, ctx?, url}`; `e.json()` for a compact payload to the model ([Error handling](https://pydantic.dev/docs/validation/dev/errors/errors/)). Malformed JSON → `json_invalid`.

`TypeAdapter` covers dataclasses, typed dicts, and unions without a `BaseModel`. `defer_build=True` (v2.10+) postpones core-schema compile until first validate — useful for large catalogs ([TypeAdapter](https://pydantic.dev/docs/validation/2.12/concepts/type_adapter)). Callable/`@validate_call` JSON validation is historically limited (`ArgsKwargs` from Python, not `validate_json` on a flat object) ([pydantic#11240](https://github.com/pydantic/pydantic/issues/11240)) — **wrap tool args in a BaseModel**, do not validate the Python signature from LLM JSON.

### 1.7 Retry logic: schema-invalid vs runtime vs HTTP

Three **different** loops. Mixing them is how you double-charge Stripe.

| Loop | Trigger | What you send the model | Backoff | Cap |
| --- | --- | --- | --- | --- |
| **HTTP / transport** | 408/429/5xx, connect reset | Nothing (no new tokens) | Exponential + jitter; OpenAI SDK `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2` (01) | Honor `Retry-After`; Anthropic **529** is provider overload, not your quota — failover, don’t spin (01) |
| **Schema-invalid args** | Pydantic `ValidationError` / JSON-RPC `-32602` invalid args | **Semantic retry:** `tool_result` / `function_call_output` / Instructor reask with `e.json()` | **No** sleep — the model must rewrite args | **2–3** model re-asks then fail closed |
| **Tool runtime error** | Timeout, 5xx from CRM, `isError: true` | Error payload as tool result (`is_error: true` / Agents SDK `failure_error_function`) | HTTP backoff **inside** the adapter if the **tool** is retryable **and** idempotent | Adapter retries ≠ model retries |

**Instructor:** `client.create(..., max_retries=2)` means **1 extraction + 2 validation retries** (3 SDK calls). Separate from `OpenAI(max_retries=2)` transport retries. Custom `tenacity.Retrying(wait_exponential(...), retry=retry_if_exception_type((ValidationError, RateLimitError)))`. Exhaustion → `InstructorRetryException`. Mode.TOOLS reasks via a **tool** message; no `tool_calls` on the retry completion historically `TypeError`d (fixed in `reask_tools`) ([Instructor retrying](https://python.useinstructor.com/concepts/retrying/index.md); [PR #2448](https://github.com/567-labs/instructor/pull/2448); [reask validation](https://python.useinstructor.com/concepts/reask_validation/)). Instructor is **post-hoc validate + reask**, not logit masking (01).

**OpenAI Agents SDK:** `@function_tool` default `failure_error_function` turns exceptions into **model-visible** output (semantic retry). Pass `None` to raise (`ModelBehaviorError` for invalid JSON, `UserError` for your crash). `timeout_behavior="error_as_result"` vs `"raise_exception"` (`ToolTimeoutError`). `tool_not_found_behavior`: default **raise** `ModelBehaviorError`; `"return_error_to_model"` appends `function_call_output` and continues. `DEFAULT_MAX_TURNS = 10`; `max_turns=None` disables ([tools.md](https://openai.github.io/openai-agents-python/tools/); [running agents](https://openai.github.io/openai-agents-python/running_agents/); [run_config.py](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py)).

**LangGraph `ToolNode`:** `handle_tool_errors` default `_default_handle_tool_errors` **catches invocation/validation errors** (bad args from the model) and returns a `ToolMessage`, but **re-raises ordinary tool execution failures**. Set `True` to catch all; `False` to propagate; callable / exception-type filters supported ([ToolNode](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode)). `BaseTool.handle_tool_error` is a **separate** hook for `ToolException` (default `False`).

**Rule:** exponential backoff is for **transient infrastructure**. Semantic retry is for **the model’s next sample**. Never exponential-backoff a `ValidationError` (it will fail identically). Never semantic-retry a 429 without sleeping.

### 1.8 Dynamic tool discovery: MCP vs static registry vs catalog vs inline

**Static registry:** compile OpenAPI → JSON Schema at deploy; pin order for cache (02). Change = prefix miss.

**MCP (JSON-RPC 2.0):** after initialize, `tools/list` (cursor pagination) → `ListToolsResult.tools[]` + optional `nextCursor`. Invoke `tools/call` `{name, arguments}`. Result: `content[]`, optional `structuredContent`, optional `isError`. Capability `tools.listChanged: true` ⇒ server **SHOULD** send `notifications/tools/list_changed`; client re-lists **all pages** ([MCP tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)). Claude Code historically fetched **page 1 only** (AgentCore gateways paginate at **30**/page) → tools 31+ `No such tool available` ([#39586](https://github.com/anthropics/claude-code/issues/39586); [#59538](https://github.com/anthropics/claude-code/issues/59538)).

**Error split (spec vs SDK vs SEP):** 2025-06-18: **unknown tool / invalid args** = JSON-RPC **`-32602`**; **execution** failure = result `isError: true` ([MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)). TypeScript SDK #1389 aligned unknown tools to protocol errors. **SEP-2140** proposes reporting unknown tools as `isError: true` so the **model** can recover. TS SDK v2 docs: tool-handler throws become `isError: true` (model-visible); protocol errors are **never** seen by the model ([SDK errors](https://ts.sdk.modelcontextprotocol.io/v2/servers/errors); [SEP-2140](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2140)). Interview answer: **map protocol −32602 unknown-name to a model-visible “no such tool; here is the allowlist”** in the **host**, regardless of wire encoding.

**Catalog vs inlined schemas:**

| Strategy | What the model sees every turn | Who expands |
| --- | --- | --- |
| Inline all schemas | Full JSON Schema × N tools + hidden tool-use prompt | You |
| Names + disk/folder (Cursor) | Tool **names**; schemas as files the agent `read`s | Agent |
| Anthropic `defer_loading` + tool search | Search tool + 3–5 hot tools; rest via `tool_reference` | Anthropic API |
| OpenAI `tool_search` + namespaces | Namespace blurb; schemas at end of window on hit | OpenAI or your `tool_search_output` |
| Per-turn `allowed_tools` | Full schema list (cached) but only a subset **callable** | OpenAI |

Cursor A/B (runs that **called** an MCP tool): **−46.9%** total agent tokens; high variance with MCP count. Dynamic discovery syncs descriptions to a folder; static prompt holds names ([Cursor dynamic context discovery](https://cursor.com/blog/dynamic-context-discovery); [forum correction](https://forum.cursor.com/t/what-do-your-attached-mcp-servers-actually-cost-you-in-tokens-per-request-i-measured-it/166405)). Third-party IDE surveys (2026) sometimes still classify Cursor as eager — treat **46.9% as Cursor’s published A/B**, and measure your build ([Mornati](https://blog.mornati.net/your-mcp-input-context-which-ides-lazy-load-and-how-leanproxy-keeps-it-flat/)).

### 1.9 Self-hosted parsers (vLLM)

`vllm serve ... --enable-auto-tool-choice --tool-call-parser <name>`. Parsers include `hermes`, `mistral`, `llama3_json`, `llama4_pythonic`, `granite` / `granite4` / `granite-20b-fc`, `internlm`, `jamba`, `xlam`, `deepseek_v3` / `deepseek_v31`, `openai`, `kimi_k2`, `hunyuan_a13b`, `cohere_command3`, `longcat`, `glm45` / `glm47`, `functiongemma`, `qwen3_xml`, `olmo3`, `gigachat3`, `apertus`, `pythonic` ([vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/)).

Constrained decode: `tool_choice` named/`required` always uses structured outputs (first named call: **several seconds** FSM compile, then cached). `auto` constrains only if **at least one** tool has `strict: true` **and** `VLLM_ENFORCE_STRICT_TOOL_CALLING=true` (default). Else raw-text extract — args may be malformed ([vLLM](https://docs.vllm.ai/en/stable/features/tool_calling/)). Hermes parser historically **drops** a call if a literal `</tool_call>` appears inside a JSON string ([#45167](https://github.com/vllm-project/vllm/issues/45167)); streaming has returned raw XML instead of `tool_calls` ([#31871](https://github.com/vllm-project/vllm/issues/31871)). Mistral Transformers template requires **9-digit** `tool_call_id`s — vLLM provides truncated templates. **Always Pydantic-validate after the parser.**

---

## 2. Token Economics & NFR Metrics

List prices and cache multipliers: **see 01**. Assembler-level schema tax: **see 02**. Below is **tool-loop** spend.

### 2.1 Schema tokens every turn (cite 02)

Every Anthropic request with `tools` injects a hidden tool-use system prompt **plus** your JSON schemas. Sonnet 5: **354** tokens (`auto`/`none`) or **474** (`any`/`tool`) **on top of** names/descriptions/schemas, even with one empty tool ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing); 02). `computer_toolset_20260801` default members ≈ **4,590** input tokens on Sonnet 5; `browser_toolset_20260801` ≈ **6,670** (02). OpenAI: “callable function definitions count against the context limit and are billed as input tokens” ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)).

Cursor deferred MCP: **−46.9%** tokens on MCP-calling runs ([Cursor](https://cursor.com/blog/dynamic-context-discovery)). Anthropic tool search: **~55k → typically >85% reduction**, 3–5 tools loaded ([Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).

02 worked example (do not re-derive prices): Sonnet 5, 20-tool agent, ~2,000 schema + 354 hidden + 1,500 system + 500 user, 800 output, no cache → **$0.01671 / model-turn** → **$16.71 / 1k model-turns**; 5m cache warm → **$9.57 / 1k**.

### 2.2 Cost of retry loops [inferred]

A **schema-invalid** retry is a **full extra model call** (not a 50 ms HTTP retry). Shape: same cached prefix + error `tool_result` (~200–800 tokens) + new output.

**[inferred] Sonnet 5, cached 20-tool prefix (02’s $0.00957 / model-turn after warm), one ValidationError retry that adds 400 uncached error tokens and 400 extra output:**

- Base cached turn: $0.00957
- Extra input: 400 × $2 / 1e6 = $0.00080
- Extra output: 400 × $10 / 1e6 = $0.00400
- Retry increment ≈ **$0.0048** (~**50%** of a warm turn)

If **20%** of tool-using user questions hit one schema retry: **+ $0.96 / 1k user questions** on top of the loop below. A **poison-pill schema** (always-invalid; §3.4) at Instructor `max_retries=2` is **3×** model calls per user question with **zero** successful tool I/O.

HTTP retries (`DEFAULT_MAX_RETRIES=2`) do **not** add LLM tokens if they fail before the stream; they **do** duplicate billed tokens if you replay after streaming started (01).

### 2.3 $ per 1k **tool-using** turns (stated shape)

Define a **tool-using user question** as: 1 planning model-call (emits tools) + 1 execution of 2 parallel REST tools + 1 synthesis model-call. **No thinking.** Sonnet 5 5m cache on the stable prefix (tools+system). Numbers compose 02’s cached **$0.00957** warm model-turn as a stand-in for “4,354 packed in / 800 out” — your `usage` will differ.

| Shape | Model-calls / question | **[inferred] $ / 1k questions** |
| --- | --- | --- |
| Happy path, cache warm | 2 | 2 × $0.00957 × 1000 ≈ **$19.14** |
| + 20% one-shot schema retry | 2.2 | **$21.06** |
| Uncached (02 $0.01671 / model-turn) | 2 | **$33.42** |
| Computer-use prefix 4,590 + 354 hidden, uncached, 800 out, ignore screenshots | 2 | input 4,944 × $2 / 1e6 + 800 × $10 / 1e6 = $0.01789 / call → **$35.78** |
| Same computer-use, cache hit @ 0.1× input | 2 | 4,944 × $0.20 / 1e6 + $0.008 out = $0.00899 / call → **$17.98** |
| Cursor −46.9% on the **schema portion** of an MCP-heavy run | n/a | 02’s uncached $16.71/1k **model-turns** of schema-taxed input is the wrong unit; apply −46.9% to **agent tokens on MCP-calling traces**, not to list price |

Stripe/CRM **tool fee** is $0 at the LLM meter; you still pay **their** RPM (below) and **your** result tokens every later turn (02 tool-result clearing at 100k).

### 2.4 Latency: tool RTT vs model

No vendor publishes p50/p95 for “tool dispatcher.” Bounds:

| Stage | Published / documented | Kind |
| --- | --- | --- |
| OpenAI SDK timeout | **600 s** request, **5 s** connect (01) | Ceiling, not SLO |
| Anthropic `input_json_delta` | concatenate until `content_block_stop` | Do not execute early |
| vLLM first named-function FSM | “several seconds” then cached ([vLLM](https://docs.vllm.ai/en/stable/features/tool_calling/)) | Cold schema |
| Anthropic grammar cache | **24 h** since last use ([strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)) | Cold vs warm strict |
| Temporal Activity `StartToClose` | you set (cookbook examples **30 s** for LLM) ([Temporal cookbook](https://docs.temporal.io/ai/cookbook/tool-call-openai-python)) | Your SLO |
| Independent TTFT (02) | miss ~1.0–1.7 s at 1.5k–5k prefix on public internet | RTT-dominated |

**[inferred] budget:** REST tool p95 **<800 ms** including auth; model call p95 **1–8 s** (thinking / Fast-mode tok/s in 01). Parallel `asyncio.gather` of independent tools makes wall-clock ≈ **slowest tool + model**, not the sum ([Temporal parallel tool dispatch](https://go.temporal.io/platform-hub/ai-engineering/ai-patterns)). Sequential computer-use members add **N × action RTT**.

Nested timeout: **LLM request timeout > tool Activity StartToClose > HTTP client > downstream SLA**. Invert this and you retry-amplify (§5.7).

### 2.5 RPM of tool backends

LLM RPM (OpenAI tiers, Anthropic Start 1,000 RPM / 2M ITPM / 400k OTPM for Sonnet 5): **see 01**. Tool backends are a **second** token bucket:

| Backend | Public figure | Implication |
| --- | --- | --- |
| Stripe | Test-mode and live **request** limits are **dashboard-specific**; keys expire **≥24 h** ([Idempotent requests](https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia)) | Agent fan-out of `create PaymentIntent` will 429 **Stripe** while OpenAI still has TPM left |
| MCP `tools/list` | Paginated; AgentCore example **30 tools/page** ([#39586](https://github.com/anthropics/claude-code/issues/39586)) | Missing page 2 looks like “hallucinated tool” |
| OpenAI prompt cache routing | **>~15 req/min per `prompt_cache_key`** overflows routing (02) | Partition by tenant |
| vLLM | `--max-num-seqs` (example guides use **4** on a 70B) | Tool-call JSON decode shares the same seq slots |

**Bulkhead:** one semaphore per **downstream class** (payments, CRM read, MCP, sandbox). A 100-agent × 4-parallel-tools burst is **400** in-flight HTTP; size pools to **that**, not to LLM RPM.

---

## 3. Distributed Resilience & State

### 3.1 Idempotency keys (exactly-once is a lie)

RFC 9110: GET/PUT/DELETE are idempotent; POST/PATCH are not. Stripe: client sends `Idempotency-Key` (V4 UUID, **≤255** chars, no PII); server stores **status + body of the first request, including 500s**; subsequent same key **replays** that result; keys pruned after **≥24 hours** then reuse = **new** request; parameter mismatch → error; replay flagged `Idempotent-Replayed: true` ([Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia); [error handling](https://docs.stripe.com/error-low-level)). After a 500, Stripe advises **against** a new key — the original may have had side effects.

IETF `Idempotency-Key` header draft-07 (expired **2026-04-18**): **409** while in-flight, **422** fingerprint mismatch ([draft-ietf-httpapi-idempotency-key-header-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header)).

**Agent rule:** derive the key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` **or** from the provider `call_id` / `tool_use_id` if the runtime guarantees **at-most-once delivery of that ID to the adapter**. **Do not let the model invent the key** (hallucinated UUID = duplicate charge). Temporal: `workflowRunId + activityId` is stable across Activity retries ([Activity definition](https://docs.temporal.io/activity-definition); [Temporal idempotency](https://temporal.io/blog/idempotency-and-durable-execution)).

### 3.2 At-least-once vs at-most-once vs exactly-once

Temporal Activities are **at-least-once** by default (unlimited retries). `maximumAttempts=1` on a non-local Activity is **at-most-once** (zero times possible). **Exactly-once** requires the **downstream** idempotency store, not Temporal ([Temporal idempotency](https://temporal.io/blog/idempotency-and-durable-execution)). Rule: **every LLM call and every tool I/O is an Activity**; Workflows that call HTTP directly break replay ([AI reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture); [cookbook](https://docs.temporal.io/ai/cookbook/tool-call-openai-python)). Disable the OpenAI SDK’s nested retries (`max_retries=0`) so **one** owner retries ([AI patterns](https://go.temporal.io/platform-hub/ai-engineering/ai-patterns)).

LangGraph checkpointers persist **messages**, not a lock around POST. Kafka is for webhooks in; **Signal** the Workflow — do not put a Kafka client in Workflow code ([quality-bar Temporal notes](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture)).

### 3.3 Timeouts and partial side effects

Timeout mid-POST: the PaymentIntent **might** exist. Recovery: retry **same** idempotency key; if Stripe returns the object, treat as success; if 500 replayed, **do not** mint a new key ([Stripe](https://docs.stripe.com/error-low-level)).

**Compensating action (saga):** parallel tools `charge` + `create_crm_note`. Charge succeeds, note 503s. Return **both** `tool_result`s (Anthropic requires every `tool_use_id`). Do **not** auto-refund from the model loop. Persist `saga_state`; a **separate** Workflow (or human) compensates. Computer-use: stop at first failure and `is_error` the rest ([Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).

OpenAI Agents `ProgrammaticToolCallingTool`: SDK **disables provider-managed retries** and WebSocket pre-event retries because replay may not be safe ([tools.md](https://openai.github.io/openai-agents-python/tools/)).

### 3.4 Poison pills (always-invalid schema)

A schema the **grammar cannot satisfy** or Pydantic **always** rejects:

- OpenAI `strict: true` dropped `format: "date"` on fine-tunes, but Pydantic `date` still requires ISO dates → infinite semantic retry.
- `enum` of 1,001 values above OpenAI’s **1,000** enum cap (post-raise limits) → 400 on the **request**, not a tool result ([structured outputs limits](https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593)).
- MCP tool whose `inputSchema` is `null` (illegal) or open `{type:object}` while your dispatcher `extra="forbid"`s an empty properties set.
- vLLM Hermes cut on `</tool_call>` inside a string → parser returns text, Instructor retries until `max_retries`.

**Circuit:** after **N** identical `(tool, error_type)` failures, stop reasking; return to the user / HITL. Instructor/LangGraph will not do this unless you add a loop detector on `(tool, args_hash)` or error fingerprint.

### 3.5 Parallel completeness

Anthropic/OpenAI/Gemini can emit **N** parallel calls. `asyncio.gather` with **per-call** try/except; always return **N** results. One 500 must not drop sibling IDs (Anthropic 400: `tool_use ids were not found in tool_result blocks`) ([pydantic-ai #653](https://github.com/pydantic/pydantic-ai/pull/653); [Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)). `defer_loading` reveal spliced **between** a sibling `tool_use` and its `tool_result` is a documented 400 ([pydantic-ai #7878](https://github.com/pydantic/pydantic-ai/issues/7878)).

Destructive + read in one batch: **disable parallel** (`parallel_tool_calls: false` / `disable_parallel_tool_use: true`) or partition with `allowed_tools` so `refund` cannot share a turn with `charge`.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP

MCP 2025-11-25 remote servers are **OAuth 2.1 resource servers**. MUST: RFC 9728 Protected Resource Metadata, RFC 8707 `resource` indicator (absolute URI, no fragment) on **authorization and token** requests, PKCE, no implicit grant, exact redirect URIs. MUST **validate audience**; MUST NOT **token-passthrough** to upstream APIs (confused deputy) ([MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization); [security best practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)). Proxies MUST implement **per-client consent**. Local STDIO: env credentials, not OAuth. HTTP localhost: bind `127.0.0.1`, validate `Origin`/`Host`.

DNS rebinding: TypeScript SDK **CVE-2025-66414** and Python SDK **CVE-2025-66416** — DNS rebinding protection **off by default** for unauthenticated localhost HTTP; enable `enableDnsRebindingProtection` / `TransportSecuritySettings`; patched defaults in TS **1.24.0** / Python **1.23.0** when binding localhost ([GHSA-W48Q-CV73-MX4W](https://github.com/advisories/GHSA-W48Q-CV73-MX4W); [GHSA-9h52-p55h-vw2f](https://github.com/advisories/GHSA-9h52-p55h-vw2f)). Pin DNS between allowlist check and connect (TOCTOU) ([MCP security best practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)).

### 4.2 Never take identity from model JSON

`user_id`, `tenant_id`, `role`, `Authorization`, `account_id` in tool **arguments** are **untrusted**. Bind identity from the **control-plane principal** (session JWT, MCP access token, Temporal `info` memo) **before** the adapter. The model may pass a **resource id** the principal is allowed to name; the adapter **re-checks ACL**. OWASP MCP cheat sheet: confused deputy is the MCP server executing with **its** privileges, not the user’s ([OWASP MCP](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)).

Agents SDK `RunContextWrapper.context` is **not** model-visible unless you inject it (02) — that is the correct slot for `tenant_id`.

### 4.3 Tool RBAC

RBAC belongs in the **dispatcher**, not the system prompt (prompts are LLM01-injectable; 02). Map `(principal, tenant, tool, args_shape)` → allow / deny / HITL. Per-turn **allowlists** (`allowed_tools`, Gemini `allowed_function_names`, Anthropic non-deferred set) are **capability reduction**, not authz — a prompt-injected model can still call every tool you left callable.

High-impact: payments, email send, `tools/call` to shell/browser, CRM deletes. Agents SDK `needs_approval` / ADK `LongRunningFunctionTool` are the HITL productization ([Agents tools](https://openai.github.io/openai-agents-python/tools/)).

Pin MCP manifests (hash descriptions + schemas) against **rug pulls** / tool poisoning ([OWASP MCP](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html); [OWASP third-party MCP guide](https://genai.owasp.org/download/51928/)).

### 4.4 Argument sanitization and SSRF

JSON Schema `strict` / Pydantic `extra="forbid"` stops **extra keys**, not **evil values**. HTTP tools: deny `169.254.169.254`, RFC1918, localhost, metadata IPv6; pin DNS; do not trust hostname alone ([MCP security](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices)). GraphQL: persisted-query allowlist — unrestricted GraphQL is RCE-shaped. SQL tools: never `execute_sql`; parameterized ops only.

MCP tool **descriptions** are prompt-injection surface (tool poisoning) ([OWASP](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)). Dual-LLM: quarantined model reads untrusted `tool_result`; privileged model holds tools.

### 4.5 PII in tool args and results

Tool args (SSN, PAN, email) and results (CRM dumps) **re-enter the window** and the **prompt cache**. Redact before inject; 02: tool-result clear default trigger **100k**, keep last **3**. Do not log raw `tool_result` to third-party traces without a BAA. OpenAI compact items are encrypted (better ZDR, worse DLP). Stripe: **do not** use emails as idempotency keys ([Stripe](https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia)).

Schema **enums** leak internal IDs and unreleased flags (02 tool-schema leakage). Deferral reduces both tokens and leakage.

### 4.6 Audit

Persist: `tool_name`, `call_id`/`tool_use_id`, principal, **hashed** args, policy decision, downstream status, latency, idempotency key, MCP server hash. Temporal Event History is a natural audit log. Provider traces are not a SIEM.

---

## 5. Production Failure Modes

### 5.1 Hallucinated names and parameters

**Names:** model emits `create_charge` when the tool is `create_payment_intent`. OpenAI Agents default **raises** `ModelBehaviorError`; set `tool_not_found_behavior="return_error_to_model"` to recover ([running agents](https://openai.github.io/openai-agents-python/running_agents/)). MCP unknown tool: JSON-RPC `-32602` **or** `isError` depending on SDK/SEP — host must map to a model-visible allowlist (§1.8). Anthropic `strict: true` guarantees **name ∈ provided tools**. Gemini `ANY` + `allowed_function_names` constrains the set.

**Params:** extra keys, dropped required, `"2"` vs `2`, fabricated PKs (`order_id: "12345"`). Mitigations: OpenAI `strict: true`, Anthropic `strict: true`, Gemini `VALIDATED`/`ANY`, Pydantic `extra="forbid"` **before** side effects, canonical ID lookup (never trust model PKs). BFCL V4 weights hallucination/irrelevance **10%** of overall (**1,122** samples in the published breakdown) ([BFCL V4](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html); [leaderboard](https://gorilla.cs.berkeley.edu/leaderboard)).

Non-strict + `extra="ignore"` is the silent failure: extra `bcc:` on `send_email` dropped, or worse, **accepted** if you didn’t forbid.

### 5.2 Parallel destructive tools

`refund` + `capture` in one sample; `delete_user` + `export_user`. GPT-5+ parallel functions; `gpt-4.1-nano-2025-04-14` duplicate-same-tool. **Fix:** `parallel_tool_calls: false` / Anthropic `disable_parallel_tool_use`; split mutating tools behind HITL; idempotency keys **per intent**, not per sample (two parallel identical `charge` calls with two keys = two charges).

### 5.3 Schema drift

MCP `notifications/tools/list_changed` while a conversation still has old `tool_use` names. Adding/removing/reordering tools **busts Anthropic tools cache** (02). Hot-reload every request → never hit cache. Pydantic model v2 vs still-inlined v1 schema → poison pill. **Fix:** version `tool_schema_hash` on the assembler; on mismatch, compact or start a new thread; pin MCP hashes.

Claude Code + MCP: `defer_loading=true` **and** `cache_control` on the same tool → **every request 400** until `ENABLE_TOOL_SEARCH=false` ([#30920](https://github.com/anthropics/claude-code/issues/30920)).

### 5.4 MCP timeout, disconnect, pagination

Transient MCP disconnect **evicts** tools from the front `tools` array → full prefix invalidation; reconnect = second miss ([Claude Code #53132](https://github.com/anthropics/claude-code/issues/53132) related reports). `tools/list` timeout: stale catalog → hallucinated names. Pagination bugs: page 2 dropped → “No such tool” for real tools ([#39586](https://github.com/anthropics/claude-code/issues/39586)).

**Fix:** cache last-good catalog with TTL; on `tools/call` unknown, **re-list all pages** once, then fail; do not inline 55k tokens as the fallback (use search).

### 5.5 Oversized tool results blowing context

CRM `list_orders` returning 50k tokens. Deep Agents: results **>20k** → file + **10-line** preview (02). Anthropic `clear_tool_uses_20250919` trigger **100k**, keep **3** (02). Cursor: write long MCP output to a file instead of truncating ([Cursor](https://cursor.com/blog/dynamic-context-discovery)). Cap `limit` in the **adapter** regardless of schema (model will pass `limit: 10000`).

Overflow: OpenAI compact cannot run if the dump already exceeds the window (02). Hard 400 `model_context_window_exceeded`.

### 5.6 Parser / grammar failures

vLLM streaming Hermes → raw `<tool_call>` text, `finish_reason: stop` ([#31871](https://github.com/vllm-project/vllm/issues/31871)). Embedded `</tool_call>` drops the call ([#45167](https://github.com/vllm-project/vllm/issues/45167)). OpenAI `strict: true` 400 on `propertyNames` / free-form maps ([tanstack/ai #976](https://github.com/tanstack/ai/issues/976)). Anthropic `input: {}` at stream start is a **placeholder**, not empty args ([fine-grained streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)).

### 5.7 Retry amplification and infinite loops

LLM timeout 60 s → tool HTTP 55 s → both retry → **retry storm**. Poll-instead-of-webhook (`get_job` every turn). Error results that say “try again” without a budget. Instructor `max_retries` nested under SDK `max_retries` under Temporal Activity retries. Gemini CI regenerates up to **5** times on sandbox errors (quality-bar 03; out of scope here except as a pattern).

**Fix:** one retry owner; turn cap (`DEFAULT_MAX_TURNS=10`); loop detector on `(tool, args_hash)`; `tool_choice: "none"` on the closing turn; circuit-break identical GET pagination.

---

## 6. Enterprise System Design Scenarios

### 6.1 Payments + CRM tools at scale

**Goal:** copilot can `create_payment_intent` and `lookup_account` for 10k tenants without double charges or cross-tenant CRM reads.

**Control plane**

```
API Gateway ─► Temporal Workflow (agent loop)
                 ├─ Activity: LLM (strict tools, allowed_tools = f(intent))
                 ├─ Activity: ToolDispatcher
                 │    ├─ Pydantic validate (extra=forbid)
                 │    ├─ RBAC (principal, not model JSON)
                 │    ├─ Idempotency-Key = hash(tenant, intent_id, tool, args)
                 │    ├─ Stripe adapter (never retry POST without key)
                 │    └─ CRM adapter (cursor pagination; cap limit)
                 └─ Kafka: Stripe webhooks → Signal Workflow
```

**Choices**

| Decision | Pick | Why |
| --- | --- | --- |
| Schema | OpenAI/Anthropic `strict: true`; Gemini `VALIDATED` | Kill hallucinated extra keys before Stripe |
| Parallel | `parallel_tool_calls: false` on any turn that includes a mutating payment tool | Duplicate nano-style calls |
| Discovery | Static OpenAPI for first-party payments; MCP only for third-party SaaS with OAuth | Payments catalog is small and must be cache-stable |
| Retry | Temporal owns HTTP; Instructor/Pydantic semantic retry **≤2**; SDK `max_retries=0` inside Activities | One owner |
| Identity | `customer` from session; model may pass `invoice_id` which ACL re-checks | Confused deputy |
| HITL | Amount > threshold or `refund` | LLM06 excessive agency |

**Capacity [inferred]:** 100 concurrent agents × 2 model-calls × Sonnet 5 Start 1,000 RPM is fine; **100 × Stripe POSTs** is the real ceiling. Cache the tool prefix (02): uncached 20-tool copilot **$16.71 / 1k model-turns** vs **$9.57** cached. Payments questions that always mutate should still **cache schemas**; only args and results vary.

**Failure drill:** timeout after Stripe accepted the POST → retry **same** key → `Idempotent-Replayed: true` → treat as success, then CRM note. If CRM fails, saga compensates; do not `create_payment_intent` again with a new key.

### 6.2 MCP gateway with dynamic discovery

**Goal:** 50 MCP servers, thousands of tools, per-tenant OAuth, no 55k-token prefix, no page-2 dropouts.

**Gateway**

1. **Terminate OAuth** at the gateway (audience-bound token for the gateway). **Token exchange** (RFC 8693) to upstream; **never passthrough**.
2. `tools/list` **all pages** on connect and on `notifications/tools/list_changed`; store catalog `{name, hash(description+schema), server, scopes}` in Redis.
3. **Do not** inline all schemas. Expose to the model:
   - Anthropic: tool search + `defer_loading: true`; **3–5** hot tools live; **no** `cache_control` on deferred tools.
   - OpenAI `gpt-5.4+`: namespaces (`crm`, `jira`, …) + `{type:"tool_search"}`; `<10` functions per namespace.
   - Cursor-style: names in static prompt, schemas on disk — **−46.9%** tokens on MCP-calling runs ([Cursor](https://cursor.com/blog/dynamic-context-discovery)).
4. Per-turn allowlist = intersection of (user scopes, tenant policy, `allowed_tools`). Hallucinated name → model-visible error + **top-k catalog names**, not a 500.
5. `tools/call`: validate args against **pinned** schema hash (rug-pull detect); SSRF URL filter; timeout; `isError` vs −32602 mapped in the host.
6. Results: cap bytes; spill to object storage + 10-line preview (Deep Agents 20k pattern, 02).

**Selection accuracy:** Anthropic documents degradation past **30–50** inlined tools ([Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)). The gateway’s job is to keep the **callable** set in that band even if the catalog is thousands.

**ZDR / cache:** discovered tools as `tool_reference` / end-of-window inject preserve prefix cache ([Anthropic caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching); [OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)). Mid-session ToolSearch unlock that **mutates the tools block** can still rewrite the prefix — measure `cache_creation_input_tokens` ([#53132](https://github.com/anthropics/claude-code/issues/53132)).

### 6.3 Trade-off matrix (architect review)

| Axis | Inline schemas + strict | Deferred / tool search | MCP gateway catalog | vLLM auto parser |
| --- | --- | --- | --- | --- |
| Hallucinated **names** | Low if small N; degrades >30–50 | Search miss → wrong tool | Host maps −32602 | Parser-dependent |
| Hallucinated **params** | Lowest (`strict`/`VALIDATED`) | Same once loaded | Same + pin hash | `auto` without `strict` is extract-from-text |
| Token $ | Highest every turn (02 354/474 + schemas) | −46.9% / >85% documented cuts | Catalog tokens ≪ schemas | Schemas still in prompt unless excluded |
| Cache | Best if **frozen** order | Designed to preserve prefix | `list_changed` can bust | Local KV |
| Authz | Your RBAC | Same | OAuth audience + RBAC | Same |
| Ops risk | Schema 400s on unsupported keywords | `defer_loading`+`cache_control` 400 | Pagination / disconnect | Hermes `</tool_call>` bugs |

### 6.4 Interview checklist

1. Control plane allowlists **and** RBAC; model JSON is not identity.
2. Compile Pydantic → provider subset; `additionalProperties: false` + all keys `required`.
3. Validate **again** at execute-time (`extra="forbid"`).
4. Semantic retry ≤2 with `ValidationError.json()`; HTTP backoff is a different loop.
5. Idempotency key **not** from the model; Temporal Activity per tool.
6. Parallel: return every ID; disable parallel around money.
7. Discover via MCP pages + tool search; never inline 55k tokens.
8. Cap tool results; pin MCP hashes; Origin/Host on localhost MCP.

---

## Sources

1. https://developers.openai.com/api/docs/guides/function-calling
2. https://developers.openai.com/api/docs/guides/structured-outputs
3. https://developers.openai.com/api/docs/guides/tools-tool-search
4. https://developers.openai.com/api/docs/guides/tools
5. https://developers.openai.com/api/docs/guides/streaming-responses
6. https://openai.com/index/introducing-structured-outputs-in-the-api/
7. https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api
8. https://community.openai.com/t/new-api-feature-disable-parallel-function-calling-via-parallel-tool-calls-false/805405
9. https://community.openai.com/t/schema-additionalproperties-must-be-false-when-strict-is-true/929996
10. https://community.openai.com/t/structured-outputs-limits-are-raised-to-support-larger-schemas/1313593
11. https://community.openai.com/t/responses-api-allowed-tools-tool-choice-parameter-now-failing-for-gpt-5/1357177
12. https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs
13. https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/tool-search
14. https://openai.github.io/openai-agents-python/tools/
15. https://openai.github.io/openai-agents-python/running_agents/
16. https://openai.github.io/openai-agents-python/ref/run/
17. https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py
18. https://github.com/openai/openai-agents-python/blob/cdde4d65/docs/tools.md
19. https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
20. https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
21. https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use
22. https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use
23. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
24. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching
25. https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming
26. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
27. https://platform.claude.com/docs/en/about-claude/pricing
28. https://github.com/anthropics/skills/blob/HEAD/skills/claude-api/shared/tool-use-concepts.md
29. https://github.com/anthropics/claude-code/issues/30920
30. https://github.com/anthropics/claude-code/issues/39586
31. https://github.com/anthropics/claude-code/issues/53132
32. https://github.com/anthropics/claude-code/issues/59538
33. https://ai.google.dev/gemini-api/docs/function-calling
34. https://ai.google.dev/gemini-api/docs/generate-content/function-calling
35. https://ai.google.dev/gemini-api/docs/thought-signatures
36. https://ai.google.dev/gemini-api/docs/generate-content/thinking
37. https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/function-calling
38. https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/function-calling
39. https://googleapis.github.io/js-genai/release_docs/interfaces/types.FunctionCall.html
40. https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html
41. https://json-schema.org/draft-07/json-schema-validation
42. https://json-schema.org/understanding-json-schema/reference/object
43. https://json-schema.org/draft/2020-12/release-notes
44. https://pydantic.dev/articles/llm-intro
45. https://pydantic.dev/docs/validation/2.12/concepts/json_schema/
46. https://pydantic.dev/docs/validation/2.12/concepts/type_adapter
47. https://pydantic.dev/docs/validation/latest/api/pydantic/type_adapter/
48. https://pydantic.dev/docs/validation/2.10/errors/validation_errors
49. https://pydantic.dev/docs/validation/2.7/api/pydantic/config/
50. https://pydantic.dev/docs/validation/dev/errors/errors/
51. https://github.com/pydantic/pydantic/issues/11240
52. https://python.useinstructor.com/concepts/retrying/index.md
53. https://python.useinstructor.com/concepts/reask_validation/
54. https://python.useinstructor.com/concepts/validation/
55. https://github.com/567-labs/instructor/issues/716
56. https://github.com/567-labs/instructor/pull/2448
57. https://modelcontextprotocol.io/specification/2025-11-25/server/tools
58. https://modelcontextprotocol.io/specification/2025-06-18/server/tools
59. https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
60. https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices
61. https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-11-25/schema.ts
62. https://github.com/modelcontextprotocol/modelcontextprotocol/issues/2140
63. https://github.com/modelcontextprotocol/typescript-sdk/issues/1510
64. https://github.com/modelcontextprotocol/typescript-sdk/pull/1389
65. https://ts.sdk.modelcontextprotocol.io/v2/servers/errors
66. https://cursor.com/blog/dynamic-context-discovery
67. https://forum.cursor.com/t/what-do-your-attached-mcp-servers-actually-cost-you-in-tokens-per-request-i-measured-it/166405
68. https://blog.mornati.net/your-mcp-input-context-which-ides-lazy-load-and-how-leanproxy-keeps-it-flat/
69. https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode
70. https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py
71. https://reference.langchain.com/python/langchain-core/tools/base/BaseTool/handle_tool_error
72. https://docs.vllm.ai/en/stable/features/tool_calling/
73. https://github.com/vllm-project/vllm/issues/31871
74. https://github.com/vllm-project/vllm/issues/45167
75. https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia
76. https://docs.stripe.com/error-low-level
77. https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header
78. https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture
79. https://go.temporal.io/platform-hub/ai-engineering/ai-patterns
80. https://docs.temporal.io/ai/cookbook/tool-call-openai-python
81. https://docs.temporal.io/activity-definition
82. https://temporal.io/blog/idempotency-and-durable-execution
83. https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html
84. https://genai.owasp.org/download/51928/
85. https://github.com/advisories/GHSA-W48Q-CV73-MX4W
86. https://github.com/advisories/GHSA-9h52-p55h-vw2f
87. https://gorilla.cs.berkeley.edu/leaderboard
88. https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html
89. https://github.com/pydantic/pydantic-ai/pull/653
90. https://github.com/pydantic/pydantic-ai/issues/7878
91. https://github.com/tanstack/ai/issues/976
92. https://www.rfc-editor.org/info/rfc8707/
93. https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thought-signatures
94. https://github.com/openai/openai-agents-python/pull/763
95. https://platform.claude.com/docs/en/build-with-claude/prompt-caching
96. https://developers.openai.com/api/docs/guides/prompt-caching
97. https://github.com/fugue-labs/gollem/commit/a55e5f719ddd228f28aa4c397156fcc9e3df54cf
