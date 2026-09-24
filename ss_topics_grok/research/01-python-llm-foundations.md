# Research: Python & LLM Foundations

**Date researched**: 2026-09-23
**Sources consulted**: 96

Vendor list prices, model IDs, and SLAs below were captured from official pages on 2026-09-23. OpenAI currently publishes **two concurrent flagship families** on different pages (`gpt-5.4` / `gpt-5.5` on [openai.com/api/pricing](https://openai.com/api/pricing/) and `gpt-6-*` on [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing)); both are cited. Anthropic’s [models overview](https://platform.claude.com/docs/en/about-claude/models/overview) lists **Claude Opus 5.5** at $4/$20 while the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) still tabulates **Claude Opus 5** at $5/$25 — quote the page you bill against.

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane for an LLM API client

The **application control plane** owns: HTTP client pools, retries, cancellation, tool dispatch, schema compilation, idempotency, tenant auth, and the agent loop. The **provider data plane** owns: tokenizer → embedding → transformer forward (prefill + decode) → sampler → detokenizer / function-call parser. Hosted APIs never execute customer tools; they emit structured requests the client must run ([Anthropic: How tool use works](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works); [Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)).

OpenAI Prompt Caching stores **KV tensors, not tokens**; a later request with an exact prefix reuses those states instead of recomputing prefill ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Anthropic’s cache is similarly a prefix KV reuse keyed by `cache_control` breakpoints on `tools` → `system` → `messages` in that order ([Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

Embedding serving is a **single-shot encoder** (no decode loop, no KV growth, no sampler). Generation serving is **prefill (compute-bound, TTFT) then decode (memory-bound, TPOT)**. Mixing both on one connection pool is a topology error: embedding bursts should not starve streaming chat sockets.

### 1.2 Python async orchestration: asyncio vs concurrent.futures

**`asyncio` is the production default** for LLM I/O: each in-flight HTTP stream is a coroutine, not a thread. Python 3.11 added `asyncio.TaskGroup` as structured concurrency: tasks created with `tg.create_task()` are awaited on context exit; the first non-`CancelledError` failure cancels remaining siblings and raises `ExceptionGroup` ([Python 3.11 asyncio](https://docs.python.org/release/3.11.0/library/asyncio-task.html); [Python 3.14 asyncio](https://docs.python.org/3/library/asyncio-task.html)). Swallowing `CancelledError` breaks `TaskGroup` and `asyncio.timeout()` because both are implemented with cancellation internally; if suppression is intentional, call `Task.uncancel()` ([same docs](https://docs.python.org/3/library/asyncio-task.html)).

Nested `TaskGroup` plus parent cancellation has a documented edge: when a child group has both errors *and* an external cancel, Python re-cancels the parent so `CancelledError` is not lost inside an `ExceptionGroup` ([CPython issue 116720](https://github.com/python/cpython/issues/116720)).

**Backpressure primitives:**

| Primitive | Blocks producer? | Caps in-flight? | Typical LLM use |
| --- | --- | --- | --- |
| `asyncio.Semaphore(N)` | Yes, at `async with sem` | Yes | Cap concurrent provider calls to RPM/TPM |
| `asyncio.Queue(maxsize=M)` | Yes, on `await put()` | Buffer + workers | Ingest → embed pipeline |
| `ThreadPoolExecutor.submit` | **No** (unbounded queue) | Only running threads | Sync SDK inside async app — wrap with a semaphore |

Python’s `concurrent.futures.ThreadPoolExecutor` uses an **unbounded work queue**; `submit()` never blocks when all workers are busy ([CPython docs](https://docs.python.org/3/library/concurrent.futures.html)). Production wrappers acquire a `threading.Semaphore(max_workers + queue_slots)` around `submit` and release in a done-callback ([pattern](https://stackoverflow.com/questions/77874240/is-there-a-way-to-block-on-applying-tasks-in-python-threadpool-if-all-the-thread)). Use threads for **blocking** SDKs (`requests`, sync DB); use `asyncio.to_thread()` to hop off the loop. Do not use `ProcessPoolExecutor` for HTTP — pickling httpx clients is wrong, and LLM calls are I/O-bound.

`asyncio.Queue` is **not thread-safe**. Bounded `maxsize` with `await put()` is the stdlib backpressure; `put_nowait()` raises `QueueFull` ([asyncio queues](https://docs.python.org/3/library/asyncio-queue.html)). A queue stuck at `maxsize` means consumers are the bottleneck; empty means the producer is.

**Cancellation of in-flight token streams:** cancel the Task that owns the stream context manager. Both OpenAI and Anthropic SDKs close the HTTP body on context exit. Do **not** automatically replay a request after consuming streamed output — OpenAI documents that 429/503 before the stream starts are retryable HTTP errors, but errors after streaming begins arrive as stream events and replay would duplicate billed tokens ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Persist partial text on cancel; it is the only recovery artifact.

### 1.3 HTTP clients: httpx, httpx2, aiohttp

OpenAI Python SDK defaults: `DEFAULT_TIMEOUT = httpx.Timeout(timeout=600, connect=5.0)` (10 min), `DEFAULT_MAX_RETRIES = 2`, `DEFAULT_CONNECTION_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100)`, `INITIAL_RETRY_DELAY = 0.5`, `MAX_RETRY_DELAY = 8.0` ([openai/_constants.py](https://github.com/openai/openai-python/blob/main/src/openai/_constants.py); [README retries](https://github.com/openai/openai-python/blob/main/README.md)).

Anthropic Python SDK 1.x moved the HTTP layer from `httpx` to **`httpx2`** (Pydantic-maintained fork). Passing an `httpx.Client` / `httpx.AsyncClient` as `http_client=` raises `TypeError`. Use `DefaultHttpxClient` / `DefaultAsyncHttpxClient` / `DefaultAioHttpClient`, or `import httpx2 as httpx`. Tracing tools that patch `httpx` (OpenTelemetry `HTTPXClientInstrumentor`, Sentry, respx) do not see SDK traffic unless `httpx2.alias_httpx()` runs **before** any `import httpx` ([Anthropic MIGRATION.md](https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md); [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)).

High-concurrency path: `AsyncAnthropic(http_client=DefaultAioHttpClient())` after `pip install anthropic[aiohttp]` ([Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python)). OpenAI maintainers documented that default `httpx.AsyncClient` can be **>10× slower** than `aiohttp.ClientSession` under concurrent GETs; workaround is `httpx_aiohttp.AiohttpTransport` plugged into `DefaultAsyncHttpxClient` ([openai-python#1596](https://github.com/openai/openai-python/issues/1596); [httpx#3215 / httpx2#827](https://github.com/pydantic/httpx2/issues/827)).

HTTPX streaming: `async with client.stream(...) as r:` then `aiter_lines()` / `aiter_bytes()`; the stream context **closes the response on exit** ([HTTPX async](https://www.python-httpx.org/async/)). SSE is **not** “one network chunk = one event”: parse `text/event-stream` frames (blank-line delimited) per WHATWG ([HTML SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html)). aiohttp large SSE lines: raise `read_bufsize` on `ClientSession`; `StreamReader.readline()` has **no** `max_line_length` kwarg ([google-genai#2497](https://github.com/googleapis/python-genai/issues/2497)).

### 1.4 SSE vs REST for LLM apps (your API and the provider’s)

WHATWG SSE: MIME `text/event-stream`, UTF-8, events separated by `\n\n`. Fields: `event`, `data`, `id`, `retry`. Consecutive `data:` lines concatenate with LF. A colon-first line is a comment. EOF **without** a trailing blank line **discards** the last event ([HTML SSE parsing](https://html.spec.whatwg.org/multipage/server-sent-events.html); [MDN](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)).

OpenAPI 3.1 has **no first-class SSE schema**; document `content: { text/event-stream: { schema: { type: string } } }` plus prose. OpenAPI 3.2 adds `itemSchema` for per-event types ([OAS 3.1.2](https://spec.openapis.org/oas/v3.1.2); [Speakeasy SSE](https://github.com/speakeasy-api/developer-docs/blob/main/openapi/content/server-sent-events.mdx)). `info.version` is **your** API version; `openapi` is the spec version — they are distinct ([OAS 3.1](https://spec.openapis.org/oas/v3.1.0)).

**Provider streaming (data plane events):**

OpenAI **Responses API** (`POST /v1/responses`, `stream: true`) emits **named** SSE events: `response.created`, `response.output_text.delta`, `response.function_call_arguments.delta`, `response.completed`, `error` ([Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses); [streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events/)). OpenAI **Chat Completions** (`POST /v1/chat/completions`) emits anonymous `data:` chunks of `chat.completion.chunk`; append `chunk.choices[0].delta.content` when non-null; `choices` may be empty on a usage-only final chunk; request `stream_options={"include_usage": true}` for usage ([Streaming guide](https://developers.openai.com/api/docs/guides/streaming-responses)). GPT-6 Astra **requires Responses** for tool calling ([Function calling](https://developers.openai.com/api/docs/guides/function-calling)).

Anthropic Messages (`POST /v1/messages`, `stream: true`) sequence: `message_start` → (`content_block_start` / `content_block_delta` / `content_block_stop`)* → `message_delta` (carries `stop_reason` + usage, including `usage.output_tokens_details.thinking_tokens`) → `message_stop`. **`ping` may appear anywhere** and is a liveness signal; SDK iterators historically drop ping ([Streaming messages](https://platform.claude.com/docs/en/build-with-claude/streaming); [SDK issue #749](https://github.com/anthropics/anthropic-sdk-typescript/issues/749)). Tool args arrive as `input_json_delta.partial_json` fragments to concatenate; `content_block_start` for `tool_use` has `input: {}` as a placeholder ([Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)).

**Your product API:** REST JSON for non-chat CRUD; SSE (or Responses-style named events) for token streams; webhooks for **durable async jobs** (batch completion, agent session idle); poll as a backup. Stripe’s pattern: POST with `Idempotency-Key`, pin version (`Stripe-Version` analog: Anthropic `anthropic-version: 2023-06-01`), webhooks for events, poll `/events` for recovery ([Stripe idempotency](https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia); [Stripe versioning](https://docs.stripe.com/api/versioning?api-version=2026-03-25.dahlia); [Stripe webhooks](https://docs.stripe.com/webhooks)).

Neither OpenAI Batch nor Anthropic Message Batches ship a **completion webhook at GA**; persist the batch id and poll ([OpenAI Batch](https://developers.openai.com/api/docs/guides/batch); [Anthropic batches](https://platform.claude.com/docs/en/build-with-claude/batch-processing)). Anthropic webhooks exist for **Managed Agents / vaults** (HMAC, thin payloads, `client.beta.webhooks.unwrap()`, ~5 min freshness) — not for Message Batches ([Managed Agents webhooks](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/managed-agents-webhooks.md)).

### 1.5 OpenAI SDK: Chat Completions vs Responses vs tools vs structured outputs

| Concern | Chat Completions | Responses |
| --- | --- | --- |
| Input | `messages=[...]` | `input=` (string or item list); `instructions=` top-level |
| Token cap | `max_tokens` / `max_completion_tokens` | `max_output_tokens` |
| JSON schema | `response_format={type:"json_schema", json_schema:{...}}` | `text={format:{type:"json_schema", name, strict, schema}}` |
| Function tools | nested `{type:"function", function:{name, parameters}}` | **flat** `{type:"function", name, parameters}` — nested shape is `invalid_request_error` |
| Strict default | `strict` **off** | omitting `strict` **attempts** strict; falls back to `strict: false` if schema incompatible |
| Parse helper | `client.chat.completions.parse` | `client.responses.parse` → `output_parsed` |

Sources: [Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses); [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs); [Function calling](https://developers.openai.com/api/docs/guides/function-calling).

**Strict mode** (function calling and `json_schema`): constrained decoding against a JSON Schema subset. Every object needs `additionalProperties: false` and **every property in `required`**. Optionality is `["string","null"]`, not omitted required keys. Unsupported schemas → **400**, not best-effort JSON ([Function calling strict](https://developers.openai.com/api/docs/guides/function-calling); [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). JSON mode (`json_object`) guarantees parseable JSON **not** schema adherence; at least one message must contain the substring `json` (case-insensitive) or the API errors; Responses `instructions` alone does **not** satisfy that check ([Help Center](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)).

SDK parse path: `type_to_text_format_param` always sets `"strict": True` when converting a Pydantic type ([openai-python `_parsing/_responses.py`](https://github.com/openai/openai-python/blob/main/src/openai/lib/_parsing/_responses.py)).

### 1.6 Anthropic SDK: Messages, tool_use, thinking, batch

`Anthropic` / `AsyncAnthropic` wrap `POST /v1/messages`. Tool loop:

1. Response `stop_reason == "tool_use"` with one or more `tool_use` blocks (`id`, `name`, `input`).
2. Client executes tools (order is your choice except computer/browser-use batches, which are sequential).
3. Next user message: **all** `tool_result` blocks first (`tool_use_id` match), then optional text. Echo the assistant message **verbatim**, including `thinking` / `redacted_thinking` blocks and signatures. Missing a result or mutating thinking → 400 ([Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls); [Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use); [Thinking + tools](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows)).

`stop_reason` taxonomy: `end_turn`, `max_tokens`, `stop_sequence`, `tool_use`, `pause_turn` (server-tool loop hit iteration limit — resend assistant content), `refusal` ([Stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)).

**Extended / adaptive thinking:** older models use `thinking: {type:"enabled", budget_tokens:N}` with `budget_tokens ≥ 1024` counting toward `max_tokens` (interleaved thinking can exceed `max_tokens`). Opus 4.6+ / Sonnet 4.6+ / Fable 5 / Opus 5: **adaptive thinking**; `budget_tokens` on later models is a 400. Thinking tokens are billed as **output** and reported in `usage.output_tokens_details.thinking_tokens` on the final `message_delta` ([Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking); [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)). `display: "omitted"` (default on several 5.x models) sends no `thinking_delta`, only `signature_delta` ([Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)).

**Structured outputs (native):** `output_config.format = {type:"json_schema", schema:...}` plus SDK `client.messages.parse()`. **Strict tool use:** `"strict": true` on a tool compiles `input_schema` into a grammar (same pipeline as structured outputs); tool schemas cached up to **24 hours** since last use; prompts/responses are not retained beyond the API response ([Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs); [Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)). Pre-native pattern: force `tool_choice: {type:"tool", name:"extract"}` and read `tool_use.input` ([Cookbook](https://platform.claude.com/cookbook/tool-use-extracting-structured-json)).

**Batch:** `POST /v1/messages/batches` with up to **100,000** requests; `expires_at` is **24 hours** after create; poll `GET /v1/messages/batches/{id}` until `processing_status=ended`; results JSONL unordered — join on `custom_id`. Retrieve endpoint is documented as **idempotent** ([Batches retrieve](https://platform.claude.com/docs/en/api/messages/batches/retrieve); [Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)). Server tools **do** run inside batch (agentic loop on the worker) ([Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)).

### 1.7 Tokens: tiktoken vs Anthropic tokenizer vs billing units

OpenAI `tiktoken` encodings ([tiktoken/model.py](https://github.com/openai/tiktoken/blob/main/tiktoken/model.py); [cookbook](https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken)):

| Encoding | Models |
| --- | --- |
| `o200k_base` | `gpt-4o*`, `gpt-4.1*`, `gpt-5*`, `o1`/`o3`/`o4-mini` |
| `o200k_harmony` | `gpt-oss-*` |
| `cl100k_base` | `gpt-4` (non-4o), `gpt-3.5-turbo`, **`text-embedding-3-small/large`**, `text-embedding-ada-002` |
| `p50k_base` / `r50k_base` | legacy davinci / GPT-3 |

`o200k_base` vocabulary: **199,998** regular + 2 special ([tiktoken-rs](https://docs.rs/tiktoken/latest/tiktoken/encoding/index.html)). Same UTF-8 can tokenize **shorter** under o200k than cl100k (cookbook: Japanese example 9 vs 8 tokens). `encoding_for_model("gpt-4o")` → o200k; using cl100k on GPT-5-class prompts **mis-estimates** context and cost.

**Do not use tiktoken for Claude.** Anthropic: `POST /v1/messages/count_tokens` with the **same model ID** you will call. Official skill: tiktoken undercounts Claude by **~15–20%** on typical text, more on code/non-English ([token-counting.md](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/token-counting.md); [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)). Claude 4.7+ / Fable 5 / Mythos 5 share a **newer tokenizer ≈ 30% more tokens** for the same text vs pre-Opus-4.7; 1M context ≈ **555k words** on the new tokenizer vs **~750k words** on the old ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview); [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)). Counts “may differ by a small amount” from billed `usage` and can include system-added tokens you are not billed for ([Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)). Client-side `count_tokens(text)` in old SDKs is **not** accurate for Claude 3+ ([anthropic-sdk-python#375](https://github.com/anthropics/anthropic-sdk-python/issues/375)).

**Billing units (generation):** uncached **input**, **output** (includes thinking / reasoning tokens), **cache write**, **cache read**. Anthropic also bills extra **tool-use system prompt tokens** even with one empty tool (e.g. Sonnet 5 `auto`: **354** tokens; `any`/`tool`: **474**) ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). OpenAI Fast/Priority cached input is a separate line item, up to **90%** off ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Fast mode](https://openai.com/api-fast-mode/)).

### 1.8 Embeddings topology vs generation

OpenAI `POST /v1/embeddings`: per-input max **8191–8192 tokens**, **300,000** tokens summed per request, array length ≤ 2048 strings; overflow is **400**, not truncation ([Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create/); [cookbook](https://developers.openai.com/cookbook/examples/embedding_long_inputs)). Default dims: `text-embedding-3-small` **1536**, `text-embedding-3-large` **3072**; `dimensions=` is Matryoshka truncation **and the API L2-normalizes** the result. Manual slice **must** re-normalize ([Embeddings guide](https://developers.openai.com/api/docs/guides/embeddings); [Embeddings FAQ](https://help.openai.com/en/articles/6824809-embeddings-faq)). OpenAI vectors are unit-length ⇒ **cosine == inner product** and cosine rankings == Euclidean rankings ([FAQ](https://help.openai.com/en/articles/6824809-embeddings-faq)).

Voyage `POST https://api.voyageai.com/v1/embeddings`: Voyage 4 family context **32,000**; dims **256 / 512 / 1024 (default) / 2048**; `output_dtype`: `float` | `int8` | `uint8` | `binary` | `ubinary`. **All Voyage 4 models share one embedding space** (asymmetric: index with `voyage-4-large`, query with `voyage-4-lite`). `input_type` `query`/`document` prepends a prompt. Batch list max **1,000** texts; token caps 1M / 320K / 120K by model. `truncation=True` (default) vs error ([Voyage embeddings](https://docs.voyageai.com/docs/embeddings)). Binary packing: returned int list length is **`output_dimension / 8`**.

Cohere `embed-v4.0`: context **128k**; dims **256 / 512 / 1024 / 1536 (default)**; `embedding_types` `float|int8|uint8|binary|ubinary|base64`; similarity: cosine, dot, Euclidean; multimodal text+image; max **96** inputs/call ([Cohere Embed](https://docs.cohere.com/docs/cohere-embed); [Embed API](https://docs.cohere.com/reference/embed.mdx)).

MRL (Kusupati et al., NeurIPS 2022): nested prefixes of one vector remain independently useful; up to **14×** smaller embeddings at matched ImageNet accuracy ([arXiv:2205.13147](https://arxiv.org/abs/2205.13147)). OpenAI: 256-d `text-embedding-3-large` still beat unshortened ada-002 1536-d on MTEB ([OpenAI embeddings blog](https://openai.com/index/new-embedding-models-and-api-updates/)).

vLLM structured decoding applies a **grammar bitmask after logits, before sampling** ([vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/); [intro blog](https://vllm.ai/blog/2025-01-14-struct-decode-intro)). Backends: `xgrammar`, `guidance` (llguidance), `outlines`; serve flag `--structured-outputs-config.backend` default **`auto`** (try xgrammar, fall back). Per-request backend switching is rejected on V1 — pin at engine start ([PR #15724](https://github.com/vllm-project/vllm/pull/15724)). XGrammar: byte-level PDA, up to **100×** grammar speedup, up to **80×** e2e structured serving on Llama 3.1 / H100 ([arXiv:2411.15100](https://arxiv.org/abs/2411.15100)). Outlines: FSM vocabulary index, **O(1)** average token mask ([arXiv:2307.09702](https://arxiv.org/abs/2307.09702)). Instructor is **post-hoc Pydantic validate + retry**, not logit masking ([Instructor](https://python.useinstructor.com/concepts/patching/)).

---

## 2. Token Economics & NFR Metrics

Quoted 2026-09-23 from vendor pages. Batch is **50%** of synchronous token rates at both OpenAI and Anthropic unless noted. Voyage Batch is **33% off** with a 12-hour window ([Voyage pricing](https://docs.voyageai.com/docs/pricing)).

### 2.1 Anthropic list prices (USD / million tokens)

From [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing) unless noted.

| Model | Input | 5m cache write (1.25×) | 1h cache write (2×) | Cache read | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Fable 5.1 | $10 | $12.50 | $20 | **$0.25 (0.025×)** | $50 |
| Claude Opus 5 | $5 | $6.25 | $10 | $0.50 (0.1×) | $25 |
| Claude Opus 5.5 ([models overview](https://platform.claude.com/docs/en/about-claude/models/overview)) | **$4** | — | — | **5% of input ($0.20) [inferred from 5% note]** | **$20** |
| Claude Sonnet 5 | **$2** (now standard; Sep 1 2026 increase **cancelled**) | $2.50 | $4 | $0.20 | $10 |
| Claude Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |

Batch (50%): Sonnet 5 **$1 / $5**; Opus 5 **$2.50 / $12.50**; Haiku 4.5 **$0.50 / $2.50** ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Fast mode (Opus 5 / 4.8, first-party only): **$10 / $50**; stacks with cache and residency; **not** available with Batch ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). US-only `inference_geo: "us"` on Claude 4.6+: **1.1×** all token categories ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). 1M context on 4.6+ is **standard price** (no long-context multiplier) ([1M GA blog](https://claude.com/blog/1m-context-ga)).

Cache TTL: default **5 minutes** (`cache_control: {type:"ephemeral"}`); `"ttl":"1h"` at 2× write. Hits **refresh** TTL at the read price. 5m write breaks even after **one** subsequent read; 1h after **two** ([Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Longer TTL blocks must appear **before** shorter ones. Automatic caching: one top-level `cache_control`; or explicit per-block.

Minimum cacheable prefix (silent skip if shorter — both usage cache fields stay 0) ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)):

- **512**: Fable 5.1 / Mythos 5.1 / Opus 5 / Fable 5 / Mythos 5
- **1,024**: Opus 4.8, Sonnet 5 / 4.6 / 4.5, Opus 4.1 / 4, Sonnet 4
- **2,048**: Mythos Preview, Opus 4.7, Haiku 3.5
- **4,096**: Opus 4.6 / 4.5, Haiku 4.5

Concurrent cache: entry is available only **after the first response begins** ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

### 2.2 OpenAI list prices (USD / million tokens)

**GPT-5.4** (model card, 2026-09-23): input **$2.50**, cached **$0.25**, output **$15.00**; context **1,050,000**; max output **128,000**; snapshot `gpt-5.4-2026-03-05`. Prompts **>272K** input: **2× input and 1.5× output for the full session** (standard, batch, flex). Regional processing **+10%** ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)).

**GPT-5.5** (openai.com/api/pricing snippet): input **$5.00**, cached **$0.50**, output **$30.00**; GPT-5.4 mini **$0.75 / $0.075 / $4.50** ([openai.com/api/pricing](https://openai.com/api/pricing/)).

**GPT-4.1** (model card): input **$2.00**, cached **$0.50**, output **$8.00**; context **1,047,576**; max output **32,768** ([GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1)).

**GPT-6 family** (developers pricing, 2026-09-23), short-context standard:

| Model | Input | Cached | Cache write | Output |
| --- | --- | --- | --- | --- |
| gpt-6-astra | $10.00 | $1.00 | $12.50 | $50.00 |
| gpt-6-sol | $2.00 | $0.20 | $2.50 | $10.00 |
| gpt-6-luna | $0.10 | $0.01 | $0.125 | $0.50 |

Long context is **2× input / 1.5× output** vs short on this table. Batch is **50%** of those rates. GPT-5.6 Sol promotional pricing through **at least 2026-11-21**. Models released on/after **2026-03-05** pay **+10%** on regional/data-residency endpoints. Priority processing renamed **Fast mode** on **2026-07-30**; `service_tier: "priority"` or `"fast"` ([Pricing](https://developers.openai.com/api/docs/pricing); [Fast mode](https://openai.com/api-fast-mode/)).

GPT-5.6+ prompt cache writes: **1.25× uncached input**. Pre-GPT-5.6: **no write fee**. Min prefix: **1,024** visible tokens (GPT-5.6+); earlier models 1,024–2,048. GPT-5.6+ TTL `prompt_cache_options.ttl` only supported value **`30m`**. Earlier models: caches typically clear after **5–10 min** idle, always within **1 hour** of last use (2024 launch post) ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Launch post](https://openai.com/index/api-prompt-caching/)). Cache hits require **exact prefix** match; `prompt_cache_key` isolates customer namespaces; caches are **not shared across organizations**. Cookbook: up to **80% latency reduction** for prompts **>10,000** tokens; caching is ZDR-eligible ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)).

**Embeddings:** `text-embedding-3-small` **$0.02 / 1M**; `text-embedding-3-large` **$0.13 / 1M**; Batch **50%** ($0.01 / $0.065). No output tokens. MTEB (vendor table): 62.3% / 64.6% / ada-002 61.0%; ~62,500 / 9,615 pages per dollar at ~800 tokens/page ([Embeddings guide](https://developers.openai.com/api/docs/guides/embeddings); [model cards](https://developers.openai.com/api/docs/models/text-embedding-3-small)).

Web search tool: **$10 / 1k calls** + content tokens at model rates ([Pricing](https://developers.openai.com/api/docs/pricing)).

### 2.3 Voyage & Cohere embeddings

Voyage (after free tier) ([Voyage pricing](https://docs.voyageai.com/docs/pricing)):

| Model | $/1M tokens | Free tokens |
| --- | --- | --- |
| `voyage-4-lite` | $0.02 | 200M |
| `voyage-4` | $0.06 | 200M |
| `voyage-4-large` / `voyage-context-4` / `voyage-code-4` | $0.12 | 200M |
| Batch API | **33% off**; 12h window; **free credits do not apply** | — |

Files storage **$0.05/GB-month**, 30-day retention.

Cohere `embed-v4.0` dimensions/context as in §1.8. **cohere.com/pricing** on 2026-09-23 lists **Model Vault instance** rates (Embed 4 Small **$4/hour / $2,500/month**) not a public per-token table. AWS Marketplace Bedrock listing: **$0.12 per 1M input tokens** ([AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-j3fgisven2yrs)). Treat $0.12/MTok as the **Bedrock on-demand** figure, not a first-party quote.

### 2.4 Cost formulas — reference workload W

**W** = 2,000 input + 800 output tokens; **80% cache-hit on the 2,000 input** (1,600 cache-read, 400 uncached); cache already warm (no write); **1,000 executions**. All **[inferred]** from list prices above.

| Model | $/exec | $/1k exec | Notes |
| --- | --- | --- | --- |
| Claude Sonnet 5 | $0.00912 | **$9.12** | 400×$2 + 1600×$0.20 + 800×$10 per MTok |
| Claude Sonnet 5, no cache | $0.01200 | **$12.00** | 2000×$2 + 800×$10 |
| Claude Sonnet 5, Batch 50%, no cache | $0.00600 | **$6.00** | |
| Claude Haiku 4.5 | $0.00456 | **$4.56** | |
| Claude Opus 5 | $0.02280 | **$22.80** | |
| Claude Opus 5.5 | $0.01792 | **$17.92** | using $4/$0.20/$20 |
| GPT-5.4 | $0.01340 | **$13.40** | $2.50/$0.25/$15 |
| GPT-4.1 | $0.00800 | **$8.00** | cached $0.50 not $0.20 |
| GPT-6 Sol | $0.00880 | **$8.80** | $2/$0.20/$10 |
| Embed 3-small (2k in only) | $0.00004 | **$0.04** | |
| Embed 3-large (2k) | $0.00026 | **$0.26** | |
| voyage-4-lite (2k) | $0.00004 | **$0.04** | after free tier |

**[inferred] chat:embed ratio** for W’s 2k input: Sonnet 5 uncached full call $0.012 vs 3-small $0.00004 ≈ **300×**. Indexing 1M chunks × 512 tokens = 512M tokens → 3-small **$10.24** standard / **$5.12** batch ([same arithmetic](https://nicolalazzari.ai/articles/openai-api-pricing-explained-2026)).

5-minute Anthropic write on the 1,600-token prefix: **1.25×** so first miss costs 1600×$2.50/MTok = $0.004 extra vs uncached $0.0032. Break-even: one hit at $0.00032 read ([Anthropic’s own 1-read / 2-read rule](https://platform.claude.com/docs/en/about-claude/pricing)).

### 2.5 RPM / TPM / ITPM / OTPM

**OpenAI** metrics: RPM, RPD, TPM, TPD, IPM; org **and** project scoped; first limit hit wins. Headers: `x-ratelimit-limit-requests/tokens`, `remaining-*`, `reset-*`, `Retry-After`; project-token variants exist. `slow_down` is **429** (ramp, even under RPM/TPM); `server_is_overloaded` is **503**. Ramp heuristic: once ≥ **1M input TPM**, increase **≤50% every 15 minutes**. Batch queue is **separate** from sync RPM/TPM ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

Usage tiers: Free $100/mo → Tier1 $5 paid / $100 cap → … → Tier5 $1,000 paid / **$200,000/mo** ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

GPT-5.4 **published** per-tier (not a guarantee for every org) ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)):

| Tier | RPM | TPM | Batch queue |
| --- | --- | --- | --- |
| 1 | 500 | 500,000 | 1,500,000 |
| 2 | 5,000 | 1,000,000 | 3,000,000 |
| 3 | 5,000 | 2,000,000 | 100,000,000 |
| 4 | 10,000 | 4,000,000 | 200,000,000 |
| 5 | 15,000 | 40,000,000 | 15,000,000,000 |

Embeddings 3-small: Tier1 **3,000 RPM / 1M TPM**; Tier5 **10,000 RPM / 10M TPM** ([model card](https://developers.openai.com/api/docs/models/text-embedding-3-small)).

OpenAI Batch: **50,000 requests/batch**, **200 MB** file, **2,000 batches/hour**, embeddings also capped at 50,000 inputs/batch; 24h completion window; **50%** discount; does not consume sync TPM ([Batch API](https://developers.openai.com/api/docs/guides/batch)).

**Anthropic** Messages: RPM + **ITPM (uncached input)** + **OTPM**. Token-bucket, can enforce as 1 rps for a 60 RPM limit. **`cache_read_input_tokens` do not count toward ITPM** except Haiku 3.5. Cache writes **do** count. OTPM counts actual generated tokens, **not** `max_tokens`. Limits are **per model class**, concurrent across classes. Spend caps: Start **$500/mo**, Build **$1,000**, Scale **$200,000**; cap hit → 429 `enforced_spend_limit_reached` **without** `retry-after` until next month 00:00 UTC ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits)).

Start / Build / Scale for Sonnet 5, Opus 5, Haiku 4.5 (same numbers):

| Tier | RPM | ITPM | OTPM |
| --- | --- | --- | --- |
| Start | 1,000 | 2,000,000 | 400,000 |
| Build | 5,000 | 5,000,000 | 1,000,000 |
| Scale | 10,000 | 10,000,000 | 2,000,000 |

Fable 5.x is tighter (Start 1,000 RPM / **500k ITPM** / 100k OTPM). **[inferred]** 80% cache hit on Start Sonnet 5: effective input ≈ 2M uncached + 8M cached = **10M total input tokens/min** (Anthropic’s own example) ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits)).

Message Batches (shared across models): Start **1,000 RPM / 200k queued / 100k per batch**; Scale **4,000 / 500k / 100k**. Token counting API has a **separate** pool (third-party notes: 100 RPM tier1 up to 8,000 tier4 — verify in Console) ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits); [tokenizer change writeup](https://www.developersdigest.tech/blog/claude-tokenizer-change-cost-impact)).

### 2.6 Latency: TTFT vs TPOT; published SLAs

OpenAI **Fast mode (Enterprise only)** latency SLO is **p50 tokens/sec over 5-minute windows**, plus **99.9% uptime**. Examples: GPT-5.4 Fast **99% of 5-min windows > 50 tok/s**; GPT-4.1 Fast **> 80 tok/s**; GPT-5.6 Luna **> 100 tok/s**. Footnote: some existing contracts still use p50 per **minute**. Credits if SLO missed. Fast mode **shares RPM/TPM** with Standard; ramp: ≥1M TPM and **>50% TPM increase in <15 min** may **downgrade Fast → Standard** (billed standard, `service_tier="Default"`) ([Fast mode](https://openai.com/api-fast-mode/)).

> ⚠️ Limited public data available for this dimension. OpenAI does **not** publish p50/p95/p99 **TTFT** for Standard processing. Anthropic publishes only **comparative** latency (Haiku fastest … Fable slower), not milliseconds ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)).

**[inferred] policy targets** to put in your SLO doc (not vendor guarantees): TTFT p50 < 800 ms streaming / p95 < 2 s for chat UX; TPOT p50 matching Fast-mode tok/s if you pay for it; never set a 10-minute SDK timeout on a **non-streaming** `max_tokens=128k` call — worst-case wall clock is `timeout × (max_retries+1)` ≈ **30 minutes** with defaults ([Anthropic errors](https://platform.claude.com/docs/en/api/errors)). Stream by default for anything with thinking tokens.

Context windows (API, not ChatGPT UI): Claude Fable/Opus/Sonnet 5 = **1M in / 128k out**; Haiku 4.5 = **200k / 64k**; GPT-5.4 = **1.05M / 128k**; GPT-4.1 = **1.047M / 32,768** ([Models overview](https://platform.claude.com/docs/en/about-claude/models/overview); [GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4); [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1)).

---

## 3. Distributed Resilience & State

### 3.1 Retry taxonomy

| HTTP | OpenAI | Anthropic | Retry? |
| --- | --- | --- | --- |
| 400 | invalid schema, missing `json` in JSON-mode, strict-schema fail | `invalid_request_error` (thinking config, consecutive same-role, mutated thinking, spend **self-limit**) | **No** |
| 401/403 | bad/missing key | `authentication_error` / `permission_error` | **No** (rotate key) |
| 404 | unknown model | `not_found_error` | **No** |
| 408 | timeout | timeout | **Yes** (SDK) |
| 409 | conflict / lock | conflict | **Yes** (SDK) |
| 413 | — | `request_too_large` | **No** |
| 429 | RPM/TPM **or** `slow_down` ramp | `rate_limit_error`; spend-**cap** 429 has **no** Retry-After | **Yes** if Retry-After present; **No** for monthly cap |
| 500 | `api_error` | `api_error` | **Yes** |
| 503 | `server_is_overloaded` | — | **Yes** |
| 529 | — | `overloaded_error` | **Yes** |

Sources: [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [Anthropic errors](https://platform.claude.com/docs/en/api/errors); [error-codes.md](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/error-codes.md).

Both official Python SDKs: **max_retries=2** (3 attempts), retry connection errors, 408, 409, 429, ≥500; honor `retry-after-ms` then `Retry-After` if **0 < value ≤ 60 s**, else exponential backoff + jitter, cap **8 s** ([openai/_base_client.py](https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py); [anthropic/_base_client.py](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_base_client.py)). Nested application retries **multiply** traffic — disable SDK retries (`max_retries=0`) if Tenacity wraps the call ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

Unsuccessful retries **still consume RPM**. Continuously resending a 429 will not drain the bucket faster ([OpenAI](https://developers.openai.com/api/docs/guides/rate-limits)).

### 3.2 Idempotency

LLM **generation is not naturally idempotent** (temperature > 0, streaming partials). Apply Stripe-style keys to **your** side-effecting POSTs (charge, ticket create, batch submit), not to token streams.

Stripe v1: `Idempotency-Key` (≤255 chars, UUID recommended); stores status+body of first execution **including 500s**; keys retained **≥24 hours**; parameter mismatch → error; GET/DELETE ignore the header ([Stripe idempotency](https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia)). OpenAI Batch / Anthropic Batches: persist `custom_id` + batch id in your DB **before** acknowledging the user; create-batch on retry without that row duplicates work. Anthropic retrieve-batch is explicitly idempotent ([Retrieve batch](https://platform.claude.com/docs/en/api/messages/batches/retrieve)).

Stainless clients expose `_idempotency_header` on the base client ([openai/_base_client.py](https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py)) — still **do not** assume the model will return the same tokens.

### 3.3 Circuit breakers around provider SDKs

Netflix **Hystrix is maintenance-mode**; Netflix recommends **Resilience4j** for new work ([Hystrix README](https://github.com/Netflix/Hystrix)). Pattern for LLM gateways: **one breaker per (provider, model)**, never one global breaker that also kills the failover path ([reliability writeup](https://medium.com/learnwithnk/reliability-fault-tolerance-in-llm-systems-fallbacks-guardrails-031aaff465cf)). Open on high 5xx/529/timeout rate; **do not** open solely on 429 (that is throttle, not outage) — unless 429s persist with no Retry-After (spend cap). Half-open: probe with a cheap Haiku / 4.1-nano request. Bulkhead (`asyncio.Semaphore` per provider) so Anthropic overload cannot exhaust the OpenAI pool.

### 3.4 Streaming cancellation and partial recovery

- Store `response_id` / `message.id` as soon as `response.created` / `message_start` arrives.
- Append deltas to a durable buffer (Redis / DB) every N tokens or every 200 ms.
- On cancel: close the HTTP body (SDK context manager); **do not** retry the same stream; expose partial text to the user with `finish_reason=client_cancelled`.
- Mid-stream `error` / `response.failed`: treat as terminal for that attempt.
- Anthropic `max_tokens` mid-`tool_use`: `stop_reason=max_tokens` with invalid accumulated JSON — raise `max_tokens` or repair; do not execute the tool ([Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)).
- Idle hang vs thinking: Anthropic `ping` is the liveness signal; if the SDK swallows ping, install an **idle timeout** on `aiter` (community reports of watchdog false positives) ([SDK #749](https://github.com/anthropics/anthropic-sdk-typescript/issues/749)).

### 3.5 Batch durability vs sync chat

| | Sync chat | OpenAI Batch | Anthropic Batches |
| --- | --- | --- | --- |
| Discount | 0 | 50% | 50% (incl. cache r/w) |
| SLA | seconds | 24h (often 1–6h) | 24h (docs: most <1h) |
| State | ephemeral unless `store` | Files API JSONL, 30d abuse logs default | batch object until `archived_at` |
| Resume | none | poll batch id | poll batch id |
| Webhook | n/a | **none** | **none** (agents webhooks are a different product) |
| Tool loops | yes | single-shot | server tools **yes**; client tools still single-shot per request |

If the poll worker dies, the batch keeps running — as long as the id is in durable storage. Re-create on restart = duplicate spend.

---

## 4. Enterprise Security & Governance

### 4.1 Keys, OAuth, rotation

**OpenAI:** user/project API keys for inference; **Admin API keys cannot call model endpoints**. Service accounts: `POST /v1/organization/projects/{id}/service_accounts` returns the secret **once**; later retrieve is redacted (`sk-abc...def`). Rotation: create replacement SA + key, deploy, verify, delete old. Terraform must **not** store the secret in state ([Admin APIs](https://developers.openai.com/api/docs/guides/admin-apis); [Terraform SAs](https://developers.openai.com/api/docs/guides/terraform/service-accounts); [Retrieve project API key](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/api_keys/methods/retrieve)). **Workload Identity Federation** exchanges OIDC/SPIFFE/mTLS for short-lived OpenAI tokens — no long-lived `sk-` in the runtime ([WIF](https://developers.openai.com/api/docs/guides/workload-identity-federation)). Project model allow/deny lists exist on Admin APIs ([Admin APIs](https://developers.openai.com/api/docs/guides/admin-apis)).

OAuth in OpenAI docs is for **GPT Actions**, not a replacement for org API keys ([Actions auth](https://developers.openai.com/api/docs/actions/authentication)).

**Anthropic:** Console API keys; GitHub secret scanning **auto-deactivates** leaked keys ([API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)). ZDR orgs: **CORS disabled** — browser apps must proxy ([API + data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)). Workspace rate-limit overrides via Admin Rate Limits API ([workspace rate limits](https://platform.claude.com/docs/en/api/http/admin/workspaces/rate_limits.md)).

Never put tenant identity, emails, or row IDs in **model arguments** or `prompt_cache_key` if that key is logged. `user=` / `prompt_cache_key` are **routing/abuse** fields, not authz. Authz stays in your control plane.

### 4.2 Zero-trust and PII

Embeddings are **not encryption**. Morris et al. (EMNLP 2023) Vec2Text recovered **92% of 32-token inputs exactly** (BLEU 97.3) and **89% of full names** from embedded MIMIC notes ([ACL 2023.emnlp-main.765](https://aclanthology.org/2023.emnlp-main.765/); [arXiv:2310.06816](https://arxiv.org/abs/2310.06816)). Song & Raghunathan (2020) showed inversion and attribute inference on earlier embedders ([arXiv:2004.00053](https://arxiv.org/abs/2004.00053)). Treat vector indexes as **equivalent to raw text** for DLP, access control, and retention.

Do not send another tenant’s documents in the same prompt/cache prefix. OpenAI caches are org-scoped, not tenant-scoped — `prompt_cache_key` is an optimization hint, **not a confidentiality boundary** ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### 4.3 Audit logs

Log: timestamp, tenant_id (your id, not sent to the model), route, model, `request_id` / `message._request_id`, token usage breakdown, cache hit tokens, `stop_reason`, tool names, **SHA-256 of prompt** (or of redacted prompt), never raw PII. OpenAI Admin **audit log** APIs exist for org events (keys, invites) ([Admin APIs](https://developers.openai.com/api/docs/guides/admin-apis)). Include `request-id` header when filing vendor tickets ([Anthropic errors](https://platform.claude.com/docs/en/api/errors)).

### 4.4 Data retention / ZDR

**OpenAI:** abuse-monitoring logs default **30 days** on many endpoints. ZDR / Modified Abuse Monitoring is **approval-gated**. Configure `retention_type`: `organization_default | none | zero_data_retention | modified_abuse_monitoring | enhanced_*` via Admin `POST /organization/projects/{id}/data_retention` with an **admin** key ([Your data](https://developers.openai.com/api/docs/guides/your-data); [data_retention update](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/data_retention/methods/update)). Stateful endpoints (**Assistants threads, vector stores, files, fine-tuning, evals, batches**) remain **ZDR-ineligible** even when the org is ZDR — they retain application state. Embeddings, chat completions, audio transcriptions are listed ZDR-eligible in the endpoint table ([Your data](https://developers.openai.com/api/docs/guides/your-data)). Fast mode is ZDR/BAA compatible ([Fast mode](https://openai.com/api-fast-mode/)). Prompt caching is described as ZDR-eligible because KV is not “stored customer content” in the abuse-log sense ([Prompt Caching 101](https://developers.openai.com/cookbook/examples/prompt_caching101)) — still confirm on your ZDR contract.

**Anthropic:** ZDR = no prompts/responses at rest after the response, except law/safety; flagged content may be kept **up to 2 years**. Enabled **per organization** via sales. Applies to Messages + Token Counting; **excludes** `/v1/files`, Message Batches, code execution, managed agents, Teams/Enterprise **product UIs** (Claude Code on commercial keys is in-scope). Fable 5 / Mythos 5 called out as requiring **30-day retention** in some ZDR tables — verify current eligibility before enabling those models on a ZDR org ([API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention); [Privacy Center](https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to)). Strict-tool grammar cache is “schemas up to 24h, prompts not retained” ([Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)).

---

## 5. Production Failure Modes

### 5.1 Token overflow / context window

Symptoms: 400 `context_length_exceeded` / Anthropic `invalid_request_error` about max tokens. Causes: tiktoken vs Claude tokenizer mismatch; **thinking tokens counted as output** against `max_tokens`; tool schemas + extra system prompt (hundreds of tokens, §2.1); OpenAI hidden system tokens **not** counting toward cache minimum but **counting** toward context ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). GPT-5.4 long-context **price cliff at 272K** can surprise finance even when the call succeeds ([GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)). Embeddings: 8192 overflow is hard 400; Voyage can truncate if `truncation=True`. Haiku 4.5 200k vs Sonnet 5 1M — routing the same RAG bundle to Haiku overflows.

Mitigations: count with the **provider** tokenizer; reserve `max_output + thinking_budget + tool_overhead`; compact conversation before 967k on Claude Code’s default ([model-config](https://code.claude.com/docs/en/model-config.md)); chunk embeddings with tiktoken `cl100k_base` at 8191.

### 5.2 Hallucinated JSON vs schema-constrained decode

JSON mode ≠ schema. Instructor retries on Pydantic `ValidationError` — **pays extra tokens** and can still fail. OpenAI/Anthropic **strict** and vLLM **xgrammar/guidance** mask logits so invalid tokens have probability 0 ([Structured outputs OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs); [Anthropic strict tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use); [vLLM](https://docs.vllm.ai/en/latest/features/structured_outputs/)). JSONSchemaBench (10k real schemas) shows constrained decoding is **not uniformly solved** — coverage gaps on `multipleOf`, `uniqueItems`, `contains`, `patternProperties`, `format` ([arXiv:2501.10868](https://arxiv.org/abs/2501.10868)). Unsupported strict schemas fail **at request validate (400)**, which is better than silent wrong JSON.

### 5.3 Embedding dimension / metric mismatch

Indexing 3072-d and querying 1536-d (or `dimensions=256` vs full) is a silent ANN disaster. Mixing `voyage-3` and `voyage-4` spaces is unsupported; mixing **Voyage 4 sizes** is supported ([Voyage embeddings](https://docs.voyageai.com/docs/embeddings)). Using **cosine on unnormalized inner-product models** (e.g. Contriever / `dot` in MTEB metadata) changes rankings; MTEB v2.0.0 **ignored** `ModelMeta.similarity_fn_name` and forced cosine ([mteb#1731](https://github.com/embeddings-benchmark/mteb/issues/1731)). OpenAI: if you slice without L2-normalize, cosine ≠ intended similarity ([Embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)). Binary embeddings: Hamming/IP on packed bits, not float cosine. Quantized int8 indexes need the **same** `output_dtype` at query time.

MTEB scores are **not** a retrieval SLA: they average heterogeneous tasks; vendor “62.3%” is an aggregate, not your corpus nDCG ([OpenAI table](https://developers.openai.com/api/docs/guides/embeddings); [MTEB discussion](https://huggingface.co/spaces/mteb/leaderboard/discussions/44)).

### 5.4 Partial SSE hang, stop_reason, tool_use without tool_result

- Missing `message_stop` / `response.completed`: half-closed TCP; idle timeout; SDK waiting forever. Use `asyncio.timeout` around the stream **and** an inter-event watchdog (pings).
- `stop_reason=tool_use` but client returns a user **text** message: 400. `tool_result` must come first ([Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)).
- Parallel tools: omitting one `tool_result` (even for a skipped call) is invalid; use `is_error: true` ([Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).
- OpenAI Responses vs Chat Completions tool shape mix-up → 400.
- Anthropic thinking blocks stripped on the follow-up turn → 400.
- `pause_turn` treated as `end_turn` drops server-tool results ([Stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)).
- Fine-grained tool streaming: concatenated `partial_json` may **not** be valid JSON if `eager_input_streaming` skipped server validation ([Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)).

### 5.5 Tokenizer mismatch for billing estimates

Finance dashboards using `cl100k_base` on `gpt-5.4` (o200k) or tiktoken on Claude will **undercount**. Claude 4.7+ tokenizer ≈ **+30%** vs Opus 4.6-era counts — migrating Sonnet 4.6 → Fable 5 without recounting blows context and cost ([Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)). Always bill from response `usage` (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `thinking_tokens` / OpenAI `cached_tokens` / `cache_write_tokens`). Pre-flight counts are capacity planning, not invoices.

---

## 6. Enterprise System Design Scenarios

### 6.1 Reference production topology

```
[Edge API] --SSE--> [Orchestrator]
                      |-- asyncio.TaskGroup
                      |     |-- Semaphore_openai (cap = min(RPM, TPM/avg_tokens))
                      |     |-- Semaphore_anthropic
                      |     |-- CircuitBreaker per (vendor, model)
                      |-- Tool workers (TaskGroup, cancel-on-fail)
                      |-- Embed worker pool --> Vector index
                      '-- Batch submitter --> durable batch_id
HTTP: one AsyncOpenAI + one AsyncAnthropic (httpx2 or aiohttp backend)
      Limits: max_keepalive ≈ 2× expected concurrent streams
      Timeouts: connect 5s; read idle 30–60s streaming; 600s only if you mean it
```

Connection pool size **[inferred]**: concurrent streams S, keepalive ≈ `2S` (not the SDK default 1000/100 unless you actually have 1000 fds). Each SSE holds a connection for the full decode. 200 concurrent GPT-5.4 streams at 50 tok/s ≈ **10,000 tok/s** aggregate decode; Start-tier Anthropic OTPM 400k/min ≈ **6,667 tok/s** — the **OTPM budget**, not the pool, binds first ([Rate limits](https://platform.claude.com/docs/en/api/rate-limits); [Fast mode 50 tok/s](https://openai.com/api-fast-mode/)).

### 6.2 Multi-provider failover

| Failure | Action |
| --- | --- |
| 429 with Retry-After | sleep; stay on primary |
| 429 spend cap / no Retry-After | **do not** retry; page finance; failover only if the other vendor is in the UX contract |
| 529 / 503 overload | breaker open; failover Sonnet 5 ↔ GPT-5.4 / GPT-4.1 |
| 400 schema | **never** failover (will fail everywhere) |
| Partial stream | do not failover mid-utterance; finish or abort |

Map schemas: OpenAI flat tools ↔ Anthropic `input_schema`; Responses `text.format` ↔ Anthropic `output_config.format`. Keep a **provider-agnostic IR** (Pydantic model) and compile per SDK.

### 6.3 Trade-off matrices

**Streaming vs non-streaming**

| | Streaming SSE | Non-stream JSON |
| --- | --- | --- |
| TTFT | first token | full generation |
| Cancel | yes | no |
| Retry after start | **unsafe** | yes (idempotency on *your* POST) |
| Thinking UX | need ping/watchdog | simpler |
| SDK timeout | idle-based | 600s default is a footgun |

**Cache: OpenAI automatic vs Anthropic explicit**

| | OpenAI (pre-5.6) | OpenAI 5.6+ | Anthropic |
| --- | --- | --- | --- |
| Enable | automatic ≥1024 | implicit + optional breakpoints | `cache_control` required |
| Write fee | none | 1.25× | 1.25× (5m) / 2× (1h) |
| Read | ~0.1× (model-specific) | cached_input column | 0.1× (0.025× Fable 5.1; 5% Opus 5.5) |
| TTL | 5–10 min typical | 30m option | 5m or 1h |
| Tenancy | `prompt_cache_key` hint | same | prefix isolation by content |

**Structured output stack**

| Layer | Guarantee | Cost | When |
| --- | --- | --- | --- |
| Prompt “return JSON” | none | 1× | never in prod |
| JSON mode | parseable only | 1× | legacy models |
| Instructor + Pydantic | validate/retry | 1–N× | black-box APIs without strict |
| OpenAI/Anthropic strict | constrained decode | 1× + schema compile | default |
| vLLM xgrammar | constrained, self-host | GPU + compile | high QPS JSON |

**Embeddings vendor**

| | Dim / ctx | $/1M (std) | Quantization | Shared space |
| --- | --- | --- | --- | --- |
| 3-small | 1536 / 8k | $0.02 | client-side only | n/a |
| 3-large | 3072 / 8k | $0.13 | MRL dims | n/a |
| voyage-4-lite | 1024 / 32k | $0.02 | int8/binary native | Voyage 4 family |
| voyage-4-large | 1024 / 32k | $0.12 | same | same |
| embed-v4.0 | 1536 / 128k | $0.12 Bedrock | int8/binary | text+image |

### 6.4 Capacity planning worked example **[inferred]**

Product: 50 concurrent streaming chats, avg 2k in / 800 out, 80% Anthropic cache hit, Sonnet 5, Scale-tier not yet.

- Tokens/min output: 50 × 800 / (800/50 tok/s) wait — duration ≈ 800/50 = 16 s if Fast-like 50 tok/s; if Standard is slower, use 25 tok/s → 32 s/call.
- Concurrent 50 at 25 tok/s = **1,250 tok/s** = 75,000 OTPM ≪ Start 400,000 OTPM.
- Input uncached 50 × 400 / 32 s ≈ 625 tok/s = 37,500 ITPM ≪ 2M.
- RPM: 50 / 32 s × 60 ≈ **94 RPM** ≪ 1,000.
- Binding constraint is **UX latency and connection count (50)**, not Start-tier quotas.
- Embed path: 10 qps × 512 tokens = 5,120 TPM ≪ OpenAI embed Tier1 1M TPM.

Pool: `max_keepalive_connections=100` (SDK default) is enough for 50 streams + embed bursts. Raise `Semaphore` to 60 (headroom), not 1000.

Nightly index 10M chunks × 512 tokens = 5.12B tokens. 3-small batch $0.01/1M → **$51.20**, 24h window, **does not** touch daytime RPM ([Batch](https://developers.openai.com/api/docs/guides/batch)). Voyage-4-lite after free 200M: 4.92B × $0.02/M = **$98.40** (or Batch 33% off).

### 6.5 Interview-critical design rules

1. One shared async client per process per vendor; never `AsyncOpenAI()` per request (pool explosion).
2. `TaskGroup` for fan-out tools; `Semaphore` for vendor caps; bounded `Queue` for embed ingest.
3. Compile Pydantic → strict JSON schema; fail closed on 400.
4. Bill from `usage`, plan with count_tokens / tiktoken **matched to model**.
5. Cache stable prefixes (tools, system, corpus) at the **left**; tenant data at the **right**.
6. ZDR does not cover batches/files — don’t put PHI there.
7. Embeddings go in the same threat model as the source documents.

---

## Sources

- [1] https://docs.python.org/3/library/asyncio-task.html — asyncio TaskGroup, cancellation, uncancel
- [2] https://docs.python.org/release/3.11.0/library/asyncio-task.html — TaskGroup added in 3.11
- [3] https://docs.python.org/3/library/asyncio-queue.html — bounded Queue backpressure
- [4] https://docs.python.org/3/library/concurrent.futures.html — ThreadPoolExecutor API
- [5] https://github.com/python/cpython/issues/116720 — nested TaskGroup cancellation
- [6] https://www.python-httpx.org/async/ — HTTPX streaming API
- [7] https://github.com/openai/openai-python/blob/main/src/openai/_constants.py — 600s timeout, 2 retries, 1000/100 pool
- [8] https://github.com/openai/openai-python/blob/main/README.md — retry classes (408/409/429/5xx)
- [9] https://github.com/openai/openai-python/blob/main/src/openai/_base_client.py — Retry-After ≤60s, backoff cap 8s
- [10] https://github.com/openai/openai-python/issues/1596 — httpx vs aiohttp concurrency
- [11] https://github.com/pydantic/httpx2/issues/827 — httpx concurrent latency
- [12] https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md — httpx2 migration
- [13] https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python — AsyncAnthropic, DefaultAioHttpClient
- [14] https://html.spec.whatwg.org/multipage/server-sent-events.html — SSE framing
- [15] https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events — SSE fields
- [16] https://spec.openapis.org/oas/v3.1.2 — OpenAPI 3.1 versioning
- [17] https://spec.openapis.org/oas/v3.1.0 — openapi vs info.version
- [18] https://docs.stripe.com/api/idempotent_requests?api-version=2026-03-25.dahlia — Idempotency-Key 24h
- [19] https://docs.stripe.com/api/versioning?api-version=2026-03-25.dahlia — Stripe-Version pinning
- [20] https://docs.stripe.com/webhooks — webhook vs poll
- [21] https://developers.openai.com/api/docs/guides/streaming-responses — Responses vs Chat Completions SSE
- [22] https://developers.openai.com/api/reference/resources/responses/streaming-events/ — event types
- [23] https://developers.openai.com/api/docs/guides/migrate-to-responses — parameter mapping, strict defaults
- [24] https://developers.openai.com/api/docs/guides/structured-outputs — json_schema vs json_object
- [25] https://developers.openai.com/api/docs/guides/function-calling — flat tools, strict mode
- [26] https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api — JSON-mode `json` substring rule
- [27] https://github.com/openai/openai-python/blob/main/src/openai/lib/_parsing/_responses.py — parse() forces strict
- [28] https://platform.claude.com/docs/en/build-with-claude/streaming — message_start/delta/stop, ping
- [29] https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming — input_json_delta
- [30] https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls — tool_result contract
- [31] https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use — parallel + is_error
- [32] https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons — stop_reason enum
- [33] https://platform.claude.com/docs/en/build-with-claude/extended-thinking — budget_tokens, thinking_tokens
- [34] https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows — preserve thinking blocks
- [35] https://platform.claude.com/docs/en/build-with-claude/structured-outputs — output_config json_schema
- [36] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use — grammar-constrained tools
- [37] https://platform.claude.com/cookbook/tool-use-extracting-structured-json — tool-as-schema pattern
- [38] https://platform.claude.com/docs/en/build-with-claude/batch-processing — 50% off, poll, 100k req
- [39] https://platform.claude.com/docs/en/api/messages/batches/retrieve — idempotent poll
- [40] https://developers.openai.com/api/docs/guides/batch — 50% off, 50k req, 200MB, 24h
- [41] https://github.com/openai/tiktoken/blob/main/tiktoken/model.py — o200k vs cl100k map
- [42] https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken — encoding table
- [43] https://platform.claude.com/docs/en/build-with-claude/token-counting — count_tokens API, +30% tokenizer
- [44] https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/token-counting.md — do not use tiktoken
- [45] https://github.com/anthropics/anthropic-sdk-python/issues/375 — client tokenizer inaccurate for Claude 3+
- [46] https://platform.claude.com/docs/en/about-claude/models/overview — 1M/200k windows, Opus 5.5 $4/$20
- [47] https://platform.claude.com/docs/en/about-claude/pricing — Sonnet/Haiku/Opus list + cache multipliers
- [48] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — TTL 5m/1h, min tokens
- [49] https://claude.com/blog/1m-context-ga — 1M at standard price
- [50] https://developers.openai.com/api/docs/pricing — GPT-6 family, batch 50%, residency +10%
- [51] https://openai.com/api/pricing/ — GPT-5.5 / 5.4 consumer pricing page
- [52] https://developers.openai.com/api/docs/models/gpt-5.4 — $2.50/$0.25/$15, 1.05M, 272K cliff
- [53] https://developers.openai.com/api/docs/models/gpt-4.1 — $2/$0.50/$8, 1.047M, 32,768 out
- [54] https://developers.openai.com/api/docs/guides/prompt-caching — automatic cache, 1024 min, 30m TTL
- [55] https://openai.com/index/api-prompt-caching/ — original 50% cached-input, 5–10 min idle
- [56] https://developers.openai.com/cookbook/examples/prompt_caching101 — 80% latency, ZDR-eligible
- [57] https://openai.com/api-fast-mode/ — tok/s SLAs, ramp 50%/15 min, Fast rename 2026-07-30
- [58] https://developers.openai.com/api/docs/guides/rate-limits — RPM/TPM headers, slow_down 429, tiers
- [59] https://platform.claude.com/docs/en/api/rate-limits — ITPM/OTPM, cache-aware, spend caps
- [60] https://developers.openai.com/api/docs/guides/embeddings — dims, MTEB table, 8192, normalize
- [61] https://developers.openai.com/api/reference/resources/embeddings/methods/create/ — 300k batch tokens
- [62] https://developers.openai.com/cookbook/examples/embedding_long_inputs — 8191 cl100k_base
- [63] https://help.openai.com/en/articles/6824809-embeddings-faq — unit-norm, cosine == dot
- [64] https://openai.com/index/new-embedding-models-and-api-updates/ — MRL, 256-d > ada-002
- [65] https://arxiv.org/abs/2205.13147 — Matryoshka Representation Learning
- [66] https://docs.voyageai.com/docs/pricing — $0.02–$0.12/MTok, batch 33%
- [67] https://docs.voyageai.com/docs/embeddings — 32k ctx, dtypes, shared space
- [68] https://docs.cohere.com/docs/cohere-embed — v4 dims, 128k, metrics
- [69] https://docs.cohere.com/reference/embed.mdx — output_dimension, embedding_types
- [70] https://aws.amazon.com/marketplace/pp/prodview-j3fgisven2yrs — embed-v4 $0.12/1M Bedrock
- [71] https://cohere.com/pricing — Model Vault instance pricing (no public per-token table)
- [72] https://docs.vllm.ai/en/latest/features/structured_outputs/ — xgrammar/guidance/outlines
- [73] https://github.com/vllm-project/vllm/pull/15724 — backend auto default, pin at start
- [74] https://arxiv.org/abs/2411.15100 — XGrammar 100× / 80× H100
- [75] https://arxiv.org/abs/2307.09702 — Outlines FSM O(1) masks
- [76] https://arxiv.org/abs/2501.10868 — JSONSchemaBench
- [77] https://python.useinstructor.com/concepts/patching/ — Instructor validate+retry
- [78] https://python.useinstructor.com/concepts/models/ — Pydantic response_model
- [79] https://developers.openai.com/api/docs/guides/your-data — ZDR endpoint matrix
- [80] https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/data_retention/methods/update — retention_type enum
- [81] https://platform.claude.com/docs/en/manage-claude/api-and-data-retention — Anthropic ZDR scope
- [82] https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to — ZDR product list
- [83] https://developers.openai.com/api/docs/guides/admin-apis — admin vs project keys
- [84] https://developers.openai.com/api/docs/guides/workload-identity-federation — keyless OIDC/mTLS
- [85] https://developers.openai.com/api/docs/guides/terraform/service-accounts — key rotation
- [86] https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure — GitHub leak auto-revoke
- [87] https://aclanthology.org/2023.emnlp-main.765/ — Vec2Text 92% exact recovery
- [88] https://arxiv.org/abs/2310.06816 — embeddings ≈ plaintext
- [89] https://arxiv.org/abs/2004.00053 — embedding inversion 2020
- [90] https://github.com/embeddings-benchmark/mteb/issues/1731 — MTEB cosine vs dot bug
- [91] https://github.com/Netflix/Hystrix — Hystrix maintenance; use Resilience4j
- [92] https://github.com/resilience4j/resilience4j — circuit breaker / bulkhead
- [93] https://platform.claude.com/docs/en/api/errors — 429/529, SDK retries
- [94] https://github.com/anthropics/anthropic-sdk-typescript/issues/749 — ping dropped by SDK
- [95] https://github.com/googleapis/python-genai/issues/2497 — aiohttp SSE line length
- [96] https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/managed-agents-webhooks.md — webhooks ≠ batch completion

### Limited public data (explicit)

> ⚠️ Limited public data available for **Standard-tier TTFT/TPOT percentiles** (OpenAI Fast mode publishes tok/s SLAs for Enterprise only; Anthropic publishes comparative adjectives, not ms).
> ⚠️ Limited public data available for **Cohere first-party per-token embed-v4 list price** (official page is instance/vault; $0.12/MTok is AWS Marketplace Bedrock).
> ⚠️ Limited public data available for **your** org’s exact OpenAI RPM/TPM (tier tables are typical, not a contract).
> ⚠️ Limited public data available for **p99 streaming hang rates** and inter-token timeout recommendations (operational, not vendor-published).
