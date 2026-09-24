# Module 01 — Python & LLM Foundations

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `ss_topics_grok/research/01-python-llm-foundations.md` (researched 2026-09-23, 96 sources). Vendor list prices, model IDs, and published limits are as of that date. OpenAI publishes two concurrent flagship families (`gpt-5.4` / `gpt-5.5` on openai.com/api/pricing; `gpt-6-*` on developers.openai.com). Anthropic’s models overview lists **Claude Opus 5.5** at $4/$20 while the pricing page still tabulates **Claude Opus 5** at $5/$25 — quote the page you bill against.
**Mandatory topics**: Async orchestration · API design (REST/SSE) · Anthropic/OpenAI SDKs · tokens · embeddings · structured outputs.

The unit of production is not `client.chat.completions.create()`. It is a **Python control plane**: one shared async client per vendor, `TaskGroup` fan-out, semaphore back-pressure, schema compilation, tokenizer matched to the model you will call, and a tool host that treats the model as an untrusted planner. Hosted APIs own tokenizer → prefill/decode → sampler → parser; they **never execute customer tools** — they emit structured requests your process must run.

---

## What Is This?

**Python & LLM foundations** is the client-side architecture of calling hosted (or self-hosted) language and embedding models from production Python: asyncio vs threads, HTTP/SSE, OpenAI **Responses** vs **Chat Completions**, Anthropic **Messages** + `tool_use` + thinking, `tiktoken` vs `POST /v1/messages/count_tokens`, embedding serving as a **different topology** from generation, and constrained decoding (`json_schema` / strict tools / vLLM xgrammar) versus post-hoc Pydantic retry.

## Why It Matters

Every downstream agent, RAG, and eval stack is built on these primitives. Interviews probe whether you know that prompt cache stores **KV tensors not tokens**, that `ThreadPoolExecutor.submit` **never blocks**, that tiktoken on Claude **undercounts 15–20%**, that embeddings are **invertible** (Vec2Text), and that a 600 s SDK timeout × (`max_retries+1`) is a **30-minute** hang.

## Interview traps (fail these, fail the round)

- `AsyncOpenAI()` **per request** → connection-pool explosion.
- `encoding_for_model("gpt-4")` / `cl100k_base` on GPT-5-class prompts → wrong context **and** cost (`o200k_base` is the encoding).
- tiktoken for Claude; old SDK `count_tokens(text)` for Claude 3+.
- Nested Chat Completions tool shape `{type:"function", function:{...}}` on Responses → `invalid_request_error` (Responses tools are **flat**).
- JSON mode (`json_object`) treated as schema adherence; missing substring `json` → 400; Responses `instructions` alone does **not** satisfy that check.
- Swallowing `CancelledError` inside `TaskGroup` / `asyncio.timeout`.
- Retrying an SSE after the first delta (duplicate billed tokens).
- One global circuit breaker that also kills the failover path; opening the breaker on 429 (throttle, not outage) except spend-cap with no `Retry-After`.
- Mixing embedding bursts onto the streaming-chat keepalive pool.
- Treating the vector index as ciphertext. Morris et al. recovered **92%** of 32-token inputs exactly.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns HTTP pools, retries, cancellation, schema compilation, idempotency, tenant auth, the agent loop, and **which tools exist this turn**. Data plane (vendor or vLLM) owns tokenizer → embedding **xor** transformer prefill+decode → sampler (optional grammar bitmask) → detokenizer / function-call parser. Persistence is two stores: **application checkpoints** (messages, `batch_id`, partial SSE buffers) versus **soft caches** (prompt-cache KV, not a ledger). Tool proxies / MCP servers execute side effects; telemetry is the only place streaming usage is authoritative (`response.completed` / final `message_delta`).

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS                                                                         │
│  SSE (chat tokens)  │  REST JSON (CRUD / extract)  │  poll batch_id (no webhook)│
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ TLS + Idempotency-Key + correlation-id + anthropic-version pin
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (your process — asyncio, not the GPU)                            │
│                                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ Edge gateway │─▶│ Policy       │─▶│ Tokenizer    │─▶│ Schema compiler     │  │
│  │ auth, RPM    │  │ PII redact   │  │ planner      │  │ Pydantic → JSON     │  │
│  │ circuit brk  │  │ tool RBAC    │  │ o200k vs     │  │ Schema STRICT       │  │
│  │ per (vendor, │  │ MCP ticket   │  │ count_tokens │  │ additionalProperties│  │
│  │  model)      │  │ verify       │  │ NEVER mix    │  │ false + all required│  │
│  └──────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────┘  │
│                           │                 │                     │             │
│                           ▼                 ▼                     ▼             │
│                    ┌─────────────────────────────────────────────────────────┐  │
│                    │ Orchestrator  asyncio.TaskGroup + Semaphore(vendor)     │  │
│                    │ stop_reason loop │ stream watchdog (ping / idle)        │  │
│                    │ bounded Queue(embed ingest)  ≠  chat socket pool        │  │
│                    └──────────────────────────┬──────────────────────────────┘  │
└───────────────────────────────────────────────┼─────────────────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────────┐
                     │ chat / extract SSE, REST │                          │
                     ▼                          ▼                          │
┌────────────────────────────────────────────┐  ┌──────────────────────────┼──────┐
│ DATA PLANE  GENERATION                     │  │ DATA PLANE  EMBEDDING    │      │
│ (provider-owned on hosted APIs)            │  │ single-shot encoder      │      │
│                                            │  │ NO decode, NO KV growth, │      │
│  ┌──────────┐  ┌───────────┐  ┌─────────┐  │  │ NO sampler               │      │
│  │Tokenizer │─▶│ Prefill   │─▶│ Decode  │  │  │                          │      │
│  │+template │  │ compute-  │  │ memory- │  │  │  ┌──────────┐ ┌────────┐ │      │
│  │          │  │ bound KV  │  │ bound   │  │  │  │Tokenizer │▶│Encoder │ │      │
│  │          │  │ write     │  │ 1 tok/  │  │  │  │cl100k for│ │MRL trim│ │      │
│  │          │  │ TTFT KPI  │  │ step    │  │  │  │3-small/  │ │dims= + │ │      │
│  └──────────┘  └───────────┘  │ TPOT KPI│  │  │  │3-large   │ │L2-norm │ │      │
│                               └────┬────┘  │  │  └──────────┘ └───┬────┘ │      │
│  Prompt cache = KV TENSOR reuse    │       │  └──────────────────┼───────┘      │
│  (exact prefix; not "similar text")│       │                     │              │
│                                    ▼       │                     │              │
│  ┌─────────┐  ┌─────────────────────────┐  │                     │              │
│  │ Sampler │─▶│ Parser                  │  │                     │              │
│  │ +grammar│  │ text | tool_use         │  │                     │              │
│  │ bitmask │  │ thinking | json_schema  │  │                     │              │
│  └─────────┘  └────────────┬────────────┘  │                     │              │
└────────────────────────────┼───────────────┘                     │              │
                             │                                     │              │
           ┌─────────────────┴─────────────┐                       │              │
           │ stop_reason = tool_use        │  final / extract      │              │
           ▼                               ▼                       ▼              │
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┤
│ TOOL PROXIES  (MCP / workers)   │  │ PERSISTENCE LAYER                          │
│ Untrusted planner never holds   │  │                                            │
│ IAM. Identity from ticket,      │  │  ┌──────────────────┐  ┌─────────────────┐ │
│ not from model JSON.            │  │  │ App state        │  │ Soft caches     │ │
│  ┌──────────┐  ┌─────────────┐  │  │  │ Postgres:        │  │ prompt-cache KV │ │
│  │ STS /    │─▶│ Sandbox     │  │  │  │  thread, batch_id│  │ (TTL 5m/30m/1h) │ │
│  │ signed   │  │ HTTP, code  │──┼──│  │  custom_id BEFORE│  │ Redis: partial  │ │
│  │ scope    │  │ JSON-encode │  │  │  │  user ack        │  │  SSE buffer     │ │
│  └──────────┘  └─────────────┘  │  │  └──────────────────┘  └─────────────────┘ │
│  computer-use tools sequential  │  │  ┌──────────────────┐                      │
└─────────────────────────────────┘  │  │ Vector index     │◀── embeddings        │
                                     │  │ pin model+dim+   │    (same DLP as src) │
                                     │  │ metric; ACL      │                      │
                                     │  └──────────────────┘                      │
                                     └──────────────────────┬─────────────────────┘
                                                            │
┌───────────────────────────────────────────────────────────┴─────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics      │  │ Traces       │  │ Usage (authoritative │ │
│  │ cid, tenant  │  │ TTFT p50/95  │  │ gateway→HTTP │  │ on terminal event)   │ │
│  │ SHA-256 of   │  │ TPOT, ping   │  │ →prefill     │  │ input, cache_read,   │ │
│  │ redacted     │  │ gaps, breaker│  │ →decode→tool │  │ cache_write, output, │ │
│  │ prompt, tool │  │ state, sem   │  │              │  │ thinking_tokens      │ │
│  │ names, stop_ │  │ wait, queue  │  │              │  │                      │ │
│  │ reason,      │  │ depth        │  │              │  │                      │ │
│  │ request_id   │  │              │  │              │  │                      │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control** | Auth, PII, RBAC, schema, TaskGroup, semaphores, breakers, idempotency | Provider 529 becomes *your* 500 with no fallback |
| **Generation data** | Prefill (TTFT) then decode (TPOT); prompt-cache KV | Embedding ingest starves SSE sockets |
| **Embedding data** | Single-shot encoder; 8192/300k/2048 caps (OpenAI) | Chat retries 429 the embed pool |
| **Tool proxies** | Side effects, MCP identity, sandbox | Model JSON as IAM → Excessive Agency |
| **Persistence** | Checkpoints + `batch_id`; vector index with pinned dim | Restart re-creates batch = duplicate spend |
| **Telemetry** | Usage on terminal frames; hashed prompts | Finance dashboards that bill from tiktoken |

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (interactive), REST (extract), or you accept a job and persist a `batch_id` (offline). Gateway stamps `correlation_id`, checks tenant quota, consults the **per-(vendor, model)** breaker. OpenAI `x-ratelimit-*` / Anthropic token-bucket are inputs to admission, not afterthoughts.
2. **Policy.** Detect → redact PII **before tokenize**. Secrets must not sit **left** of a cache breakpoint: Anthropic matches `tools` → `system` → `messages` in that order; OpenAI cache is exact-prefix KV. Tool RBAC attaches only this turn’s tools. MCP: verify a **signed ticket** (audience, tenant, tool name, expiry) — never take `tenant_id` from model-emitted JSON.
3. **Count.** OpenAI: `tiktoken` encoding **matched to the model** (`o200k_base` for `gpt-4o*` / `gpt-5*` / `o1`/`o3`/`o4-mini`; `cl100k_base` for `text-embedding-3-*`). Claude: `POST /v1/messages/count_tokens` with the **same model ID**. Reserve `max_output + thinking + tool-schema overhead` (Sonnet 5 `auto` tools add **354** system tokens; `any`/`tool` add **474**).
4. **Compile.** Pydantic → JSON Schema subset: every object `additionalProperties: false`, **every** property in `required`, optionality via `["string","null"]`. OpenAI SDK `parse()` **forces** `"strict": true`. Illegal schema → **400**, not best-effort JSON. Anthropic strict tools compile `input_schema` to a grammar cached **up to 24 h**.
5. **Dispatch (control → data).** One process-wide `AsyncOpenAI` and one `AsyncAnthropic`. Anthropic 1.x HTTP is **`httpx2`**: passing `httpx.AsyncClient` as `http_client=` raises `TypeError`. High-concurrency: `DefaultAioHttpClient()` (`anthropic[aiohttp]`); OpenAI maintainers documented default httpx **>10× slower** than aiohttp under concurrent GETs. Chat sockets and embed workers use **separate** limits / semaphores.
6. **Prefill or encode.** Generation: compute-bound prefill writes KV; KPI = TTFT. Cache hit skips recomputing that prefix (OpenAI: KV tensors; Anthropic: `cache_control` breakpoints). Embedding: one forward, no sampler. OpenAI `dimensions=` is Matryoshka truncation **and the API L2-normalizes**; a client-side slice **must** re-normalize.
7. **Decode + constrain.** Memory-bound, one token/step, KPI = TPOT. After logits, before sample, a grammar bitmask (vLLM xgrammar / guidance / outlines; provider `strict`) sets illegal tokens to −∞. Instructor is **not** this: it is post-hoc validate + retry (extra tokens, still can fail).
8. **Stream parse.** OpenAI Responses named events: `response.created` → `response.output_text.delta` / `response.function_call_arguments.delta` → `response.completed` / `error`. Chat Completions: anonymous `chat.completion.chunk`; `choices` may be **empty** on a usage-only final chunk — request `stream_options={"include_usage": true}`. Anthropic: `message_start` → (`content_block_start` / `delta` / `stop`)* → `message_delta` (stop_reason + usage, including `thinking_tokens`) → `message_stop`. **`ping` may appear anywhere**; SDK iterators have historically dropped ping — install an idle watchdog. Persist `response_id` / `message.id` on the start event; append deltas to Redis every N tokens / 200 ms.
9. **Tool proxy (only if `stop_reason=tool_use`).** Validate args against schema, check ticket + RBAC, execute, JSON-encode results. Anthropic: next user message has **all** `tool_result` blocks **first** (`tool_use_id` match), then optional text; echo the assistant message **verbatim** including `thinking` / `redacted_thinking` + signatures. Omit one parallel result → 400; use `is_error: true` for skipped calls. `max_tokens` mid-`tool_use` yields invalid JSON — **do not execute**. Computer/browser-use batches are sequential; other tools may fan-out in a nested `TaskGroup`.
10. **Cancel / hang.** Cancel the Task that owns the stream context manager; both SDKs close the HTTP body on exit. Persist partial text; `finish_reason=client_cancelled`. **Do not replay** after deltas (OpenAI: 429/503 before stream start are retryable HTTP; after start, errors are stream events). Anthropic `ping` is liveness; missing `message_stop` → half-closed TCP — `asyncio.timeout` **and** inter-event watchdog.
11. **Persist / batch.** Sync chat is ephemeral unless you store it. OpenAI Batch / Anthropic Message Batches: **no GA completion webhook** — persist id **before** ack, poll. Anthropic retrieve-batch is documented **idempotent**. Recreate-on-restart = duplicate spend. ZDR **excludes** batches/files — do not put PHI there.
12. **Emit + audit.** Terminal SSE frame is the invoice (`usage`). Log tenant (your id), model, `request_id`, token breakdown, cache hit, `stop_reason`, tool names, SHA-256 of **redacted** prompt — never raw PII.

**Interview talking point:** “The model is an untrusted planner. IAM lives on the tool host. Mixing embed ingest onto the chat connection pool is a topology error, not a tuning issue.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Tokenization

OpenAI `tiktoken` is a **byte-level BPE**. Merge rules are applied left-to-right over UTF-8 bytes against a fixed vocabulary. Complexity: **Θ(n)** in input bytes for encode (hash-map merges, n = bytes), **Θ(m)** decode for m tokens. Vocabulary (o200k): **199,998** regular + **2** special.

| Encoding | Models (research map) |
| --- | --- |
| `o200k_base` | `gpt-4o*`, `gpt-4.1*`, `gpt-5*`, `o1` / `o3` / `o4-mini` |
| `o200k_harmony` | `gpt-oss-*` |
| `cl100k_base` | `gpt-4` (non-4o), `gpt-3.5-turbo`, **`text-embedding-3-small/large`**, `ada-002` |
| `p50k_base` / `r50k_base` | legacy davinci / GPT-3 |

Same UTF-8 can be **shorter** under o200k than cl100k (cookbook Japanese example **9 vs 8** tokens). Using `cl100k_base` on GPT-5-class prompts **mis-estimates** context and cost.

**Do not use tiktoken for Claude.** Official skill: tiktoken undercounts Claude **~15–20%** on typical text, more on code/non-English. Use `POST /v1/messages/count_tokens` with the **same model ID**. Claude 4.7+ / Fable 5 / Mythos 5 share a **newer tokenizer ≈ 30% more tokens** for the same text vs pre-Opus-4.7; 1M context ≈ **555k words** new vs **~750k words** old. Counts “may differ by a small amount” from billed `usage` and can include system-added tokens you are **not** billed for. Old SDK `count_tokens(text)` is **not** accurate for Claude 3+. Token-counting API has a **separate** rate pool (third-party notes: 100 RPM tier1 up to 8,000 tier4 — verify in Console).

**Invariant T1.** Pre-flight counts are **capacity planning**. Invoices come from response `usage` (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `thinking_tokens` / OpenAI `cached_tokens` / `cache_write_tokens`).

**Invariant T2.** Thinking tokens are **output-billed**. Anthropic reports them on final `message_delta` as `usage.output_tokens_details.thinking_tokens`. They count against `max_tokens` on older `budget_tokens` models (`budget_tokens ≥ 1024`; interleaved thinking can **exceed** `max_tokens`). Opus 4.6+ / Sonnet 4.6+ / Fable 5 / Opus 5: **adaptive thinking**; sending `budget_tokens` is **400**.

### 2.2 Embeddings geometry

An embedding is a map \(f: \text{text} \to \mathbb{R}^{d}\). Serving is a **single-shot encoder**: no decode loop, no KV growth, no sampler. Mixing this traffic with streaming decode sockets on one `httpx.Limits` pool lets an ingest burst steal keepalives from TTFT-sensitive chat.

**OpenAI `POST /v1/embeddings`:** per-input max **8191–8192** tokens, **300,000** tokens summed/request, ≤ **2048** strings; overflow is **400**, not truncation. Chunk with `cl100k_base` at **8191**. Default dims: `text-embedding-3-small` **1536**, `text-embedding-3-large` **3072**. `dimensions=` is MRL (Kusupati et al., NeurIPS 2022) truncation **and the API L2-normalizes**. Manual slice **must** re-normalize:

\[
\hat{v} = \frac{v_{1:k}}{\lVert v_{1:k}\rVert_2}
\]

OpenAI vectors are unit-length ⇒ **cosine = inner product** and cosine rankings = Euclidean rankings. Complexity: encode API-bound; cosine for one query vs N docs is **Θ(N d)** (or ANN sublinear). MRL: nested prefixes remain independently useful; vendor: 256-d `3-large` beat unshortened ada-002 1536-d on MTEB.

**Voyage 4:** context **32,000**; dims 256/512/**1024**/2048; dtypes `float|int8|uint8|binary|ubinary`. **All Voyage 4 models share one space** (asymmetric: index `voyage-4-large`, query `voyage-4-lite`). `input_type` `query`/`document` prepends a prompt. Batch ≤ **1,000** texts. `truncation=True` (default) vs error. Binary packing: returned int list length = **`output_dimension / 8`**. Hamming/IP on packed bits, **not** float cosine.

**Cohere `embed-v4.0`:** context **128k**; dims 256/512/1024/**1536**; multimodal; max **96** inputs/call.

**Invariant E1.** Pin `(model_id, dimensions, similarity, output_dtype)` in the index schema. Indexing 3072-d and querying 1536-d (or `dimensions=256` vs full) is a silent ANN disaster. Mixing `voyage-3` and `voyage-4` is unsupported; mixing **Voyage 4 sizes** is supported.

**Invariant E2.** Cosine on unnormalized inner-product models changes rankings. MTEB v2.0.0 **ignored** `similarity_fn_name` and forced cosine (issue #1731) — do not treat leaderboard metric as your index metric.

**Invariant E3.** MTEB **62.3% / 64.6% / ada 61.0%** is an aggregate, not your corpus nDCG.

**Invariant E4.** Embeddings are **not encryption**. Vec2Text (Morris et al., EMNLP 2023): **92%** of 32-token inputs recovered exactly (BLEU 97.3), **89%** of full names from embedded MIMIC notes. Treat the vector index as **equivalent to raw text** for DLP, ACL, and retention.

### 2.3 Constrained decoding vs post-hoc parse

Pipeline (vLLM / provider strict): compile schema → CFG/PDA/FSM → at each decode step mask illegal logits → sample. vLLM applies the bitmask **after logits, before sampling**. Backends: `xgrammar`, `guidance` (llguidance), `outlines`; `--structured-outputs-config.backend` default **`auto`** (try xgrammar, fall back). Per-request backend switching is **rejected on V1** — pin at engine start. XGrammar: byte-level PDA, up to **100×** grammar speedup, up to **80×** e2e structured serving (Llama 3.1 / H100). Outlines: FSM vocabulary index, **O(1)** average token mask. Mask work per token is **O(|V|)** worst-case to apply, not to compute if the FSM is indexed.

| Layer | Guarantee | Cost | When |
| --- | --- | --- | --- |
| Prompt “return JSON” | none | 1× | never in prod |
| JSON mode `json_object` | parseable only; needs substring `json` | 1× | legacy models |
| Instructor + Pydantic | validate/retry | 1–N× | APIs without strict |
| OpenAI/Anthropic `strict` | constrained decode on a schema **subset** | 1× + compile | default |
| vLLM xgrammar | constrained, self-host | GPU + compile | high-QPS JSON |

**Strict schema subset (OpenAI function calling + `json_schema`):** every object `additionalProperties: false`; **every** property in `required`; optionality = union with `null`, not omitted keys. Unsupported → **400**, not silent strip. JSONSchemaBench (10k real schemas): coverage gaps on `multipleOf`, `uniqueItems`, `contains`, `patternProperties`, `format` — constrained decoding is **not uniformly solved**.

**OpenAI Chat Completions vs Responses (interview table):**

| Concern | Chat Completions | Responses |
| --- | --- | --- |
| Input | `messages=[...]` | `input=` ; `instructions=` top-level |
| Token cap | `max_tokens` / `max_completion_tokens` | `max_output_tokens` |
| JSON schema | `response_format={type:"json_schema",...}` | `text={format:{type:"json_schema", name, strict, schema}}` |
| Function tools | nested `{type:"function", function:{name, parameters}}` | **flat** `{type:"function", name, parameters}` |
| Strict default | `strict` **off** | omitting `strict` **attempts** strict; falls back if incompatible |
| Parse helper | `client.chat.completions.parse` | `client.responses.parse` → `output_parsed` |
| Tools on GPT-6 Astra | — | **required** for tool calling |

**Anthropic structured:** `output_config.format = {type:"json_schema", schema}` + `client.messages.parse()`. **Strict tool use:** `"strict": true` compiles `input_schema` into a grammar (same pipeline); schemas cached ≤ **24 h** since last use; prompts not retained beyond the API response. Pre-native: `tool_choice: {type:"tool", name:"extract"}` and read `tool_use.input`.

**Failure even with a mask:** `stop_reason=refusal`; incomplete JSON at `max_tokens`; truncated `partial_json` if `eager_input_streaming` skipped server validation; **semantic** bypass (`{"amount": -1}` is schema-valid). Grammar ⊆ syntax. Authorization ⊆ tool host.

### 2.4 asyncio scheduling

`asyncio` is the production default: each in-flight HTTP stream is a **coroutine**, not a thread. Python 3.11 `TaskGroup`: tasks from `tg.create_task()` are awaited on context exit; first non-`CancelledError` failure **cancels siblings** and raises `ExceptionGroup`. Swallowing `CancelledError` breaks `TaskGroup` and `asyncio.timeout()` (both implemented with cancellation). Intentional suppression: `Task.uncancel()`. Nested TaskGroup + parent cancel: CPython issue 116720 — child group with errors **and** external cancel re-cancels the parent so `CancelledError` is not lost inside an `ExceptionGroup`.

| Primitive | Blocks producer? | Caps in-flight? | LLM use |
| --- | --- | --- | --- |
| `asyncio.Semaphore(N)` | Yes, at `async with sem` | Yes | Cap provider calls to RPM/TPM |
| `asyncio.Queue(maxsize=M)` | Yes, on `await put()` | Buffer + workers | Ingest → embed |
| `ThreadPoolExecutor.submit` | **No** (unbounded queue) | Only running threads | Sync SDK — wrap with a semaphore |

`concurrent.futures.ThreadPoolExecutor` uses an **unbounded** work queue; `submit()` never blocks. Production: `threading.Semaphore(max_workers + queue_slots)` around `submit`, release in a done-callback. Use threads for **blocking** SDKs; `asyncio.to_thread()` to hop off the loop. Do **not** `ProcessPoolExecutor` HTTP clients (pickle is wrong; LLM calls are I/O-bound). `asyncio.Queue` is **not thread-safe**. `put_nowait()` raises `QueueFull`. Queue stuck at `maxsize` ⇒ consumers are the bottleneck; empty ⇒ producer is.

**Scheduling complexity:** task switch is O(1) w.r.t. ready-queue operations; the scarce resources are **file descriptors / keepalive slots** (each SSE holds a connection for the full decode) and **vendor OTPM**, not the event loop. Research [inferred] pool size: concurrent streams S, keepalive ≈ **2S** (not the SDK default `max_connections=1000`, `max_keepalive=100` unless you actually have 1000 fds). 200 concurrent GPT-5.4 streams at 50 tok/s ≈ **10,000 tok/s** aggregate decode; Anthropic Start OTPM 400k/min ≈ **6,667 tok/s** — the **OTPM budget binds first**.

### 2.5 HTTP / SSE mechanics

OpenAI Python defaults: `DEFAULT_TIMEOUT = httpx.Timeout(timeout=600, connect=5.0)` (**10 min**), `DEFAULT_MAX_RETRIES = 2` (3 attempts), `Limits(max_connections=1000, max_keepalive_connections=100)`, `INITIAL_RETRY_DELAY = 0.5`, `MAX_RETRY_DELAY = 8.0`. Both official SDKs retry connection errors, 408, 409, 429, ≥500; honor `retry-after-ms` then `Retry-After` if **0 < value ≤ 60 s**, else exponential backoff + jitter, cap **8 s**. Nested application retries **multiply** traffic — set `max_retries=0` if Tenacity/your wrapper owns retry.

SSE (WHATWG): MIME `text/event-stream`, UTF-8, events separated by `\n\n`. Fields: `event`, `data`, `id`, `retry`. Consecutive `data:` lines concatenate with LF. Colon-first line = comment. EOF **without** a trailing blank line **discards** the last event. **Not** “one network chunk = one event.” OpenAPI 3.1 has **no** first-class SSE schema; 3.2 adds `itemSchema`. `info.version` is **your** API; `openapi` is the spec version.

**Your product API:** REST JSON for non-chat CRUD; SSE (or Responses-style named events) for token streams; webhooks for durable jobs; poll as backup. Stripe pattern: `Idempotency-Key`, pin version (`anthropic-version: 2023-06-01`), webhooks + poll `/events`. Anthropic webhooks exist for **Managed Agents / vaults** (HMAC, ~5 min freshness) — **not** Message Batches.

aiohttp large SSE lines: raise `read_bufsize`; `StreamReader.readline()` has **no** `max_line_length` kwarg.

### 2.6 State machines

**SDK retry (control plane).** Unsuccessful retries **still consume RPM**. Continuously resending a 429 will not drain the bucket faster. Spend-cap 429 has **no** `Retry-After` until next month 00:00 UTC — **do not** retry.

```
                    Retry-After ∈ (0, 60s]              attempts exhausted
  ┌──────────┐  HTTP 408/409/429/5xx/529   ┌─────────┐  ─────────────────▶ FAIL
  │  SEND    │ ──────────────────────────▶ │  WAIT   │
  └────┬─────┘  400/401/403/404/413        │ jitter  │
       │        spend-cap 429 (no RA)      │ cap 8s  │
       │        ─────────────────────────▶ FAIL      │
       │ success                           └────┬────┘
       ▼                                        │
     DONE ◀─────────────────────────────────────┘  retry SEND
```

**Stream lifecycle:**

```
  OPEN HTTP ──▶ START_EVENT (persist response_id / message.id)
       │
       ├── delta*  ── persist buffer every N toks / 200 ms
       ├── ping    ── reset idle watchdog (Anthropic; SDK may drop)
       ├── error / response.failed  ── TERMINAL; do not retry this stream
       ├── client cancel  ── close body; expose partial; NO replay
       └── COMPLETED / message_stop  ── usage authoritative; close
```

**Structured-output parse:**

```
  COMPILE schema ──▶ 400 if unsupported strict subset
       │
       ▼
  CONSTRAINED DECODE ──▶ tokens legal w.r.t. PDA
       │
       ├── stop=refusal / max_tokens truncation ──▶ FAIL CLOSED (no tool exec)
       ├── native parse() ──▶ output_parsed
       └── Instructor path: validate ──▶ retry (pays tokens) ──▶ still fail
```

**Anthropic tool loop (stop_reason):**

```
  message ──▶ end_turn / stop_sequence ──▶ DONE
           ──▶ max_tokens ──▶ maybe repair JSON; do NOT exec partial tool
           ──▶ refusal ──▶ FAIL CLOSED
           ──▶ pause_turn ──▶ resend assistant content (server-tool cap)
           ──▶ tool_use ──▶ VALIDATE ──▶ TICKET/RBAC ──▶ EXECUTE
                              │                │
                              │ schema/deny    │ all tool_result FIRST
                              ▼                ▼
                            400/403         next Messages call
                                            (echo thinking verbatim)
```

`pause_turn` treated as `end_turn` **drops server-tool results**.

### 2.7 Invariants worth stating in an interview

1. Hosted APIs never execute customer tools.
2. Prompt cache = **KV reuse**, exact prefix; `prompt_cache_key` is a namespace hint, **not** a confidentiality boundary (OpenAI caches are **org-scoped**).
3. KV / prompt cache is **not** RPO=0.
4. `CancelledError` is control flow; swallowing it breaks structured concurrency.
5. Do not retry after consuming streamed output.
6. Tokenizer must match the billed model; finance uses `usage`, not tiktoken.
7. Embeddings ≡ plaintext for DLP (Vec2Text).
8. Shape ≠ safety: grammar does not authorize `DROP TABLE`.
9. One shared async client per process per vendor.
10. Cache stable prefixes (tools, system, corpus) at the **left**; tenant data at the **right**. Longer Anthropic TTL blocks must appear **before** shorter ones. Concurrent cache: entry available only **after the first response begins**.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1k runs

Prices **USD / million tokens**, captured 2026-09-23. Batch = **50%** of sync at OpenAI and Anthropic unless noted. Voyage Batch = **33% off**, 12-hour window, **free credits do not apply**. Formula:

\[
C = n \cdot \frac{T_{\mathrm{miss}} P_{\mathrm{miss}} + T_{\mathrm{hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}}{10^{6}}
\]

\(T_{\mathrm{out}}\) **includes thinking**. Cached Anthropic reads **do not** count toward ITPM except Haiku 3.5; writes **do**. OTPM counts **actual** generated tokens, not `max_tokens`.

**Reference workload W** (research §2.4): **2,000 input + 800 output**; **80% cache-hit** on the 2,000 input (1,600 cache-read, 400 uncached); cache already warm (**no write**); **1,000 executions**. All **[inferred]** from list prices.

| Model | $/exec | **$/1k runs** | Notes |
| --- | --- | --- | --- |
| Claude Sonnet 5 | $0.00912 | **$9.12** | 400×$2 + 1600×$0.20 + 800×$10 / MTok |
| Claude Sonnet 5, no cache | $0.01200 | **$12.00** | 2000×$2 + 800×$10 |
| Claude Sonnet 5, Batch 50%, no cache | $0.00600 | **$6.00** | |
| Claude Haiku 4.5 | $0.00456 | **$4.56** | |
| Claude Opus 5 | $0.02280 | **$22.80** | $5 / $0.50 / $25 |
| Claude Opus 5.5 | $0.01792 | **$17.92** | $4 / $0.20 [inferred 5% of input] / $20 |
| GPT-5.4 | $0.01340 | **$13.40** | $2.50 / $0.25 / $15 |
| GPT-4.1 | $0.00800 | **$8.00** | cached **$0.50** not $0.20 |
| GPT-6 Sol | $0.00880 | **$8.80** | $2 / $0.20 / $10 |
| Embed 3-small (2k in only) | $0.00004 | **$0.04** | $0.02/1M |
| Embed 3-large (2k) | $0.00026 | **$0.26** | $0.13/1M |
| voyage-4-lite (2k) | $0.00004 | **$0.04** | after free tier |

**[inferred] chat:embed ratio** for W’s 2k input: Sonnet 5 uncached $0.012 vs 3-small $0.00004 ≈ **300×**. Indexing 1M chunks × 512 tokens = 512M tokens → 3-small **$10.24** standard / **$5.12** batch.

5-minute Anthropic write on the 1,600-token prefix: **1.25×** so first miss costs 1600×$2.50/MTok = **$0.004** vs uncached **$0.0032**. Break-even: **one** subsequent hit at read price (Anthropic’s 1-read / 2-read rule: 5m write breaks even after one read; 1h after two). Hits **refresh** TTL at the read price.

**List prices you must be able to write on a whiteboard:**

Anthropic (pricing page unless noted): Fable 5.1 **$10 / $0.25 / $50** (cache read **0.025×**); Opus 5 **$5 / $0.50 / $25**; Opus 5.5 overview **$4 / ~$0.20 / $20**; Sonnet 5 **$2 / $0.20 / $10** (Sep 1 2026 increase **cancelled**); Sonnet 4.6/4.5 **$3 / $0.30 / $15**; Haiku 4.5 **$1 / $0.10 / $5**. Cache write 5m **1.25×**, 1h **2×**. Fast mode Opus 5/4.8 **$10 / $50**, first-party only, **not** with Batch. `inference_geo: "us"` on 4.6+: **1.1×**. 1M context on 4.6+ is **standard price**.

OpenAI: GPT-5.4 **$2.50 / $0.25 / $15**, context **1,050,000**, max out **128,000**; prompts **>272K** input: **2× input and 1.5× output for the full session** (standard, batch, flex). GPT-5.5 **$5 / $0.50 / $30**; GPT-5.4 mini **$0.75 / $0.075 / $4.50**. GPT-4.1 **$2 / $0.50 / $8**, max out **32,768**. GPT-6 Astra **$10 / $1 / $12.50 write / $50**; Sol **$2 / $0.20 / $2.50 / $10**; Luna **$0.10 / $0.01 / $0.125 / $0.50**. Long context on GPT-6 table: **2× input / 1.5× output**. Models on/after **2026-03-05**: regional **+10%**. GPT-5.6+ cache write **1.25×**; pre-5.6 **no write fee**. Fast/Priority cached input up to **90%** off. Web search tool: **$10 / 1k calls** + content tokens.

Embeddings: 3-small **$0.02/1M**, 3-large **$0.13/1M**, Batch 50%. Vendor: ~**62,500 / 9,615** pages per dollar at ~800 tokens/page. Voyage-4-lite **$0.02**, voyage-4 **$0.06**, voyage-4-large **$0.12** / 1M; 200M free (not on Batch). Files storage **$0.05/GB-month**, 30-day retention. Cohere embed-v4: **no public first-party per-token table** on 2026-09-23; AWS Marketplace Bedrock **$0.12 / 1M** — treat as Bedrock on-demand, not a first-party quote.

> ⚠️ Limited public data available for **Cohere first-party per-token embed-v4 list price** (official page is instance/vault; $0.12/MTok is AWS Marketplace Bedrock).

Minimum cacheable prefix (silent skip if shorter — both cache usage fields stay 0): **512** Fable/Mythos 5.x / Opus 5; **1,024** Opus 4.8, Sonnet 5/4.6/4.5, Opus 4.1/4, Sonnet 4; **2,048** Mythos Preview, Opus 4.7, Haiku 3.5; **4,096** Opus 4.6/4.5, Haiku 4.5. OpenAI GPT-5.6+: **1,024** visible tokens; TTL option **`30m`** only. Earlier OpenAI: typically **5–10 min** idle, always within **1 hour**. Cookbook: up to **80%** latency reduction for prompts **>10,000** tokens; caching described as ZDR-eligible (KV ≠ abuse-log “stored content”) — still confirm on contract.

### 3.2 Latency SLA targets

> ⚠️ Limited public data available for **Standard-tier TTFT/TPOT percentiles**. OpenAI Fast mode (Enterprise) publishes **p50 tokens/sec over 5-minute windows** plus **99.9% uptime**, not TTFT. Anthropic publishes comparative adjectives (Haiku fastest … Fable slower), not milliseconds.
> ⚠️ Limited public data available for **p99 streaming hang rates** and inter-token timeout recommendations (operational, not vendor-published).

OpenAI Fast examples: GPT-5.4 Fast **99% of 5-min windows > 50 tok/s**; GPT-4.1 Fast **> 80 tok/s**; GPT-5.6 Luna **> 100 tok/s**. Fast **shares RPM/TPM** with Standard. Ramp: ≥ **1M** input TPM and **>50% TPM increase in <15 min** may **downgrade Fast → Standard** (billed standard, `service_tier="Default"`). Some contracts still use p50 per **minute**.

**[inferred] policy targets** for *your* SLO doc (not vendor guarantees):

| Metric | Target | Mitigation |
| --- | --- | --- |
| **p50 TTFT** | **< 800 ms** streaming chat UX | Stream by default; cache prefix ≥10k toks (cookbook up to 80% latency cut); separate embed pool |
| **p95 TTFT** | **< 2 s** | Sticky warm cache; don’t insert embed bursts; aiohttp transport under concurrency; Haiku/4.1 for extract |
| **p99 TTFT / hang** | Fail closed on idle gap; no 10-min non-stream | Inter-event watchdog (Anthropic `ping`); `asyncio.timeout` on stream; never non-stream `max_tokens=128k` with default 600 s × 3 ≈ **30 min** wall clock |
| **p50 TPOT** | Match Fast tok/s **if you pay for it** (50/80/100) | Fast mode Enterprise; otherwise size UX for slower Standard **[inferred ~25 tok/s in research capacity example]** |
| **p95 time-to-final** | 800 out / 50 tok/s ≈ **16 s**; / 25 tok/s ≈ **32 s** | Cap `max_output_tokens`; adaptive thinking off for extract; SSE so perceived latency = first token |

Context windows (API): Claude Fable/Opus/Sonnet 5 = **1M in / 128k out**; Haiku 4.5 = **200k / 64k**; GPT-5.4 = **1.05M / 128k**; GPT-4.1 = **1.047M / 32,768**. Routing a 1M RAG bundle to Haiku is a **400**, not a slow call.

### 3.3 Throughput and back-pressure

**OpenAI GPT-5.4 published per-tier (typical, not a contract):**

| Tier | RPM | TPM | Batch queue |
| --- | --- | --- | --- |
| 1 | 500 | 500,000 | 1,500,000 |
| 2 | 5,000 | 1,000,000 | 3,000,000 |
| 3 | 5,000 | 2,000,000 | 100,000,000 |
| 4 | 10,000 | 4,000,000 | 200,000,000 |
| 5 | 15,000 | 40,000,000 | 15,000,000,000 |

Embeddings 3-small: Tier1 **3,000 RPM / 1M TPM**; Tier5 **10,000 RPM / 10M TPM**. Batch: **50,000** requests/batch, **200 MB**, **2,000** batches/hour, 24h window, **does not** consume sync TPM. Org **and** project scoped; first limit hit wins. `slow_down` is **429** (ramp, even under RPM/TPM); `server_is_overloaded` is **503**. Ramp heuristic: once ≥ **1M input TPM**, increase **≤50% every 15 minutes**. Spend: Tier5 $1,000 paid / **$200,000/mo** cap.

> ⚠️ Limited public data available for **your** org’s exact OpenAI RPM/TPM (tier tables are typical, not a contract).

**Anthropic Start / Build / Scale** (Sonnet 5, Opus 5, Haiku 4.5 — same numbers):

| Tier | RPM | ITPM | OTPM |
| --- | --- | --- | --- |
| Start | 1,000 | 2,000,000 | 400,000 |
| Build | 5,000 | 5,000,000 | 1,000,000 |
| Scale | 10,000 | 10,000,000 | 2,000,000 |

Fable 5.x tighter (Start 1,000 RPM / **500k ITPM** / 100k OTPM). **[inferred]** 80% cache hit on Start Sonnet 5: effective input ≈ 2M uncached + 8M cached = **10M total input tokens/min** (Anthropic’s own example). Message Batches shared: Start **1,000 RPM / 200k queued / 100k per batch**; Scale **4,000 / 500k / 100k**. Spend caps: Start **$500/mo**, Build **$1,000**, Scale **$200,000**; cap → 429 `enforced_spend_limit_reached` **without** `retry-after`.

**Worked interactive example (research §6.4) [inferred]:** 50 concurrent streams, W’s 2k/800, 80% Anthropic cache, Sonnet 5, Start-tier. At 25 tok/s: duration **32 s**; output **1,250 tok/s = 75,000 OTPM** ≪ 400,000; uncached input ≈ **37,500 ITPM** ≪ 2M; RPM ≈ **94** ≪ 1,000. Binding constraint is **UX latency and 50 connections**, not Start quotas. Semaphore **60**, not 1000. Embed 10 qps × 512 tok = 5,120 TPM ≪ embed Tier1 1M TPM.

**50k streaming req/min (scenario 1 scale-up) [inferred]:** 50,000 RPM. Output 50k × 800 = **40M OTPM**. Anthropic Scale OTPM is **2M** — **20× short**. OpenAI T5 RPM **15k** — **3.3× short**; if all 2.8k tokens count toward TPM, 50k × 2,800 = **140M TPM** vs T5 **40M** — **3.5× short**. This product is **multi-project / provisioned / self-host**, not one Start-tier key. Concurrent sockets at 25 tok/s: 50,000/60 × 32 ≈ **26,667** SSE holds — SDK keepalive **100** is not a plan.

**Back-pressure design:**

1. Admit iff breaker ∈ {closed, half-open} **and** local `Semaphore` **and** token bucket (RPM/ITPM/OTPM) has room.
2. 429 + `Retry-After` → sleep on **that** provider; do not steal the other vendor’s pool (bulkhead).
3. Bounded `Queue` for embed ingest; drop-oldest or 429 the producer — never `ThreadPoolExecutor.submit` without a cap.
4. Shed: Batch/offline first; then degrade model; then deterministic JSON. Do not infinite-retry 429.
5. Agent fleets: budget \(N_{\mathrm{rounds}} \times (\mathrm{TTFT} + T_{\mathrm{out}}/\mathrm{TPOT})\). Cap rounds. Parallel **read** tools cut rounds; they multiply downstream QPS.

### 3.4 Non-functional requirements

| NFR | Working target | Tension |
| --- | --- | --- |
| **Availability** | 99.9% **gateway** (control plane). Fast mode vendor uptime **99.9%** is Enterprise, not your SLO. Multi-vendor fallback for 503/529 | Output-distribution drift; schema mapping cost |
| **RPO** | App state / `batch_id` / partial SSE buffer: **0** for irreversible tools (checkpoint before execute). Prompt-cache KV: **minutes** (5m / 30m / 1h), best-effort | Treating KV as RPO=0 over-provisions nothing you control |
| **RTO** | Interactive: fail over **< 1 s** to secondary model (breaker already open). Batch: resume poll from durable id, **do not** re-create | Fast failover vs identical tokens (temperature > 0) |
| **Consistency** | Tool side effects: **exactly-once via idempotency keys**. Model text: at-least-once retry **changes tokens** | Cannot have bit-identical retry on T>0 |
| **Compliance** | Regional +10% / Anthropic 1.1× US geo; ZDR **excludes** batches, files, managed agents, some UIs; Fable/Mythos 5 may require **30-day** retention in some ZDR tables — verify before enabling | Residency vs latency vs price |
| **Cost vs latency** | W: Haiku **$4.56/1k** vs Opus 5 **$22.80/1k** vs Sonnet cached **$9.12/1k**; Fast mode Opus **$10/$50**; Batch 50% but 24h | Paying Fast + Opus for extract |
| **Cache vs tenancy** | Left-prefix tools/system; `prompt_cache_key` isolates **namespaces**, not tenants. Do not put tenant docs in a shared prefix | Hit rate vs leak |

OpenAI ZDR: abuse logs default **30 days**; `retention_type` via Admin API. Stateful endpoints (**Assistants threads, vector stores, files, fine-tuning, evals, batches**) remain **ZDR-ineligible**. Embeddings, chat completions, audio listed ZDR-eligible. Fast mode ZDR/BAA compatible. Anthropic ZDR: no prompts/responses at rest after response except law/safety; flagged content up to **2 years**; per-org via sales; Messages + Token Counting in-scope; **excludes** `/v1/files`, Message Batches, code execution, managed agents, Teams/Enterprise UIs (Claude Code on commercial keys is in-scope).

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution (Temporal / Kafka equivalent)

Application state ≠ KV cache. Messages, tool results, `batch_id`, and partial streams must survive process death. Prompt cache is a performance cache.

> ⚠️ Gap: the research file has no Temporal worker-versioning runbooks, measured replay cost for multi-MB tool traces, or Kafka lag SLOs for token-delta buses. Map the **equivalent** pattern onto the documented failure modes (batch poll, stream cancel, unbounded `submit`).

**Temporal (workflow = conversation / invoice job):**

- **Replay:** workflow history is the source of truth. Each model call and each tool execution is an **activity**. Activities must be **idempotent**. The LLM activity returns a structured `ModelTurn` (already-sampled tokens + tool calls) recorded in history — **never** “call the model again” inside a replay-unsafe closure. Non-determinism (temperature, `time.time`, `uuid4`) belongs **inside** the activity.
- **Distributed locking:** `workflow-id = tenant:thread_id` so two gateways cannot run the same agent loop. Tool activities take a lock keyed by `idempotency_key`. Redis `SET NX PX` is the cheap equivalent if you are not on Temporal yet.
- **Checkpointing:** persist `response_id` / `message.id` on START_EVENT; flush SSE buffer every 200 ms; persist `custom_id` + batch id **before** HTTP 200 to the user. Prefer workflow history over ad-hoc Redis “save messages” for the control loop; Redis is appropriate for the **hot** partial-token buffer.
- **Dead-letter:** after `max_attempts` on transient activity failure, or immediately on poison (identical payload hash crashes N times; truncated tool JSON), route to a DLQ workflow. Do not infinite-retry irreversible tools. Anthropic/OpenAI Batch: if the poll worker dies, the **batch keeps running** — DLQ the *poller*, not the batch, as long as the id is durable.

**Kafka (log = chain of custody + ingest back-pressure):**

- Topics: `llm.turns` (intent + idempotency key **before** side effect — outbox), `llm.stream.deltas` (optional; hot path often Redis), `llm.embed.ingest` (bounded consumer parallelism), `llm.dlq`.
- Embed pipeline: `await queue.put()` with `maxsize` **is** back-pressure; a Kafka consumer pause on embed 429 is the distributed form of `Semaphore`.
- Compaction on `thread_id` keeps a snapshot; the full log is audit chain-of-custody.
- Poison messages: skip + alert after N handler crashes; do not block the partition.

**Batch vs sync (durability table):**

| | Sync chat | OpenAI Batch | Anthropic Batches |
| --- | --- | --- | --- |
| Discount | 0 | 50% | 50% (incl. cache r/w) |
| SLA | seconds | 24h (often 1–6h) | 24h (docs: most <1h) |
| Resume | none unless you stored deltas | poll batch id | poll batch id (retrieve idempotent) |
| Webhook | n/a | **none** at GA | **none** (agents webhooks ≠ batches) |
| Tool loops | yes | single-shot | server tools **yes**; client tools single-shot per request |
| ZDR | chat often eligible | **ineligible** | **ineligible** |

### 4.2 Failure taxonomy

| HTTP | OpenAI | Anthropic | Retry? |
| --- | --- | --- | --- |
| 400 | invalid schema, missing `json` in JSON-mode, strict-schema fail | `invalid_request_error` (thinking config, consecutive same-role, mutated thinking, spend **self-limit**) | **No** |
| 401/403 | bad/missing key | `authentication_error` / `permission_error` | **No** (rotate key) |
| 404 | unknown model | `not_found_error` | **No** |
| 408 | timeout | timeout | **Yes** (SDK) |
| 409 | conflict / lock | conflict | **Yes** (SDK) |
| 413 | — | `request_too_large` | **No** |
| 429 | RPM/TPM **or** `slow_down` | `rate_limit_error`; spend-cap 429 **no** Retry-After | **Yes** if Retry-After; **No** for monthly cap |
| 500 | `api_error` | `api_error` | **Yes** |
| 503 | `server_is_overloaded` | — | **Yes** |
| 529 | — | `overloaded_error` | **Yes** |

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408, 409, 429 with Retry-After, 500, 503, 529, TLS reset | Full jitter backoff; cap 8 s unless Retry-After; retry **idempotent** reads only |
| **Permanent** | 400 schema, 401/403, 404, 413, spend-cap 429, `refusal`, `budget_tokens` on adaptive models | Fail the turn; do not failover schema errors (will fail everywhere) |
| **Poison pill** | Same payload crashes parser every time; truncated `partial_json` executed as a tool; recursive tool storm | Hash + N crashes or `N ≥ Nmax` rounds → DLQ; never auto-replay |
| **Semantic** | Schema-valid unauthorized action; injection in `tool_result` | RBAC + classifier; not a retry |
| **Partial stream** | Cancel, mid-stream `error`, missing `message_stop` | Persist partial; **do not** failover mid-utterance |

**Idempotency keys.** LLM generation is **not** naturally idempotent (T>0, streaming partials). Apply Stripe-style keys to **your** side-effecting POSTs (charge, ticket, batch submit), not to token streams. Stripe: `Idempotency-Key` ≤255 chars; stores status+body of first execution **including 500s**; retain **≥24 h**; parameter mismatch → error; GET/DELETE ignore the header. Tool key: `sha256(tenant|thread_id|tool_name|canonical_json(args)|turn_index)`. Batch: persist `custom_id` + batch id **before** user ack. Stainless `_idempotency_header` does **not** make the model return the same tokens.

**Failover map:** 429 with Retry-After → sleep, stay primary. Spend cap → **do not** retry; page finance; failover only if the other vendor is in the UX contract. 529/503 → breaker open; failover Sonnet 5 ↔ GPT-5.4 / GPT-4.1. 400 schema → **never** failover. Partial stream → finish or abort, do not switch vendors mid-utterance. Keep a **provider-agnostic IR** (Pydantic) and compile: OpenAI flat tools ↔ Anthropic `input_schema`; Responses `text.format` ↔ Anthropic `output_config.format`.

### 4.3 Circuit breaker and fallback chain

Netflix **Hystrix is maintenance-mode**; use the Resilience4j pattern. **One breaker per (provider, model)**, never one global breaker that kills failover. Open on high **5xx/529/timeout** rate. **Do not** open solely on 429 unless 429s persist with **no** Retry-After (spend cap). Half-open: probe with cheap **Haiku / GPT-4.1-nano**. Bulkhead: `Semaphore` per provider so Anthropic overload cannot exhaust the OpenAI pool.

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
                                                 │ probe    │
                                                 └──────────┘
```

**Fallback:** primary (Sonnet 5 / GPT-5.4) → secondary (other vendor or Haiku / GPT-4.1) → **deterministic** fallback that still emits **schema-valid JSON** so parsers do not crash. Do not fall back from `strict` JSON to free-form text on an extract path.

### 4.4 Enterprise security

**Keys.** OpenAI: user/project keys for inference; **Admin API keys cannot call model endpoints**. Service account secret returned **once**; later retrieve redacted (`sk-abc...def`). Terraform must **not** store the secret in state. **Workload Identity Federation** exchanges OIDC/SPIFFE/mTLS for short-lived tokens — no long-lived `sk-` in the runtime. Project model allow/deny lists exist. OAuth in OpenAI docs is for **GPT Actions**, not org API keys. Anthropic: GitHub secret scanning **auto-deactivates** leaked keys. ZDR orgs: **CORS disabled** — browser apps must proxy. Never put tenant identity, emails, or row IDs in model arguments or `prompt_cache_key` if that key is logged. `user=` / `prompt_cache_key` are **routing/abuse** fields, not authz.

**Zero-Trust MCP (even at foundations layer).** MCP servers are **tool proxies**, not “the model’s plugins.” The Python client must:

1. Treat the model as an **untrusted planner**. It may emit `tools/call` JSON. That JSON is a **request**, not a credential.
2. Issue **short-lived, audience-bound tickets** (tenant, tool name, resource ids, expiry, signature). The MCP server verifies the ticket **before** I/O. The LLM never sees the raw secret, PAT, or cloud metadata token.
3. Bind identity from the **verified gateway token / RunContext**, never from model-filled `tenant_id` arguments.
4. Network: private egress; MCP tools must not reach instance metadata (`169.254.169.254`). Allowlists by **method + resource**, not hostname (allowlisted API hosts can still be exfil channels).
5. Session memory lives in **your** checkpointer, not the MCP session.

**Tool-level RBAC (least privilege per turn).** Do not attach `send_email` / `create_ticket` unless the user asked. OpenAI `tool_choice` allowed-tools subset. Anthropic: omit the tool from `tools[]` — extra tools also cost **354–474** system tokens on Sonnet 5. Parallel writes: `disable_parallel_tool_use` / `parallel_tool_calls=false`. Irreversible tools: HITL. Computer-use: sequential.

**PII pipeline: detect → redact → audit.**

1. **Detect** at the control-plane edge (regex + DLP) **before tokenize** and **before embed**.
2. **Redact** to stable placeholders (`<email:sha256[:12]>`) so cache prefixes stay stable **without** storing secrets in KV. Do not place raw PII left of `cache_control`.
3. **Audit** the redaction map (placeholder → hash, not plaintext) on a WORM log. Second gate after retrieve / before prompt if RAG is in the path.
4. Vectors are **derived personal data**. Vec2Text recovered **92%** exact 32-token inputs and **89%** of full names from MIMIC embeddings. ACL + retention on the index **as if it were the source documents**.
5. Do not send another tenant’s documents in the same prompt/cache prefix.

**Auditability / chain of custody.** Immutable log: timestamp, `correlation_id`, `tenant_id` (yours, not sent to the model), route, model, `request_id` / `message._request_id`, usage breakdown (cache hit/write, thinking), `stop_reason`, tool names, SHA-256 of **redacted** prompt, policy decision (allowed tools, ticket id), breaker state. Include `request-id` when filing vendor tickets. OpenAI Admin audit APIs cover **org events** (keys, invites), not per-completion content. Reconstruct an agent decision as: policy snapshot + model id + sampled turn + tool results + human interrupt. Kafka / WORM object store; workflow history is a second copy.

---

## 5. Production Enterprise Code

Assumptions in comments match research: Sonnet 5 **$2/$10**, GPT-5.4 **$2.50/$15**, embed 3-small **$0.02/1M**, SDK `max_retries=2`, backoff cap **8 s**, default timeout **600 s** is a footgun. Run offline self-test: `python python_llm_gateway.py`. Live paths need `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.

```python
#!/usr/bin/env python3
"""Python LLM control-plane primitives. Python 3.11+.

  python python_llm_gateway.py          # offline assertions
  OPENAI_API_KEY=... ANTHROPIC_API_KEY=... python python_llm_gateway.py --live
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Optional live deps — offline self-test does not import them.
# Anthropic 1.x HTTP layer is httpx2. Passing httpx.AsyncClient as http_client=
# raises TypeError. Use DefaultAsyncHttpxClient / DefaultAioHttpClient, or
# `import httpx2 as httpx`. Tracing must call httpx2.alias_httpx() BEFORE
# any `import httpx` or OpenTelemetry HTTPXClientInstrumentor will miss SDK traffic.

INITIAL_RETRY_DELAY = 0.5   # openai/_constants.py
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2  # 3 attempts; disable if this wrapper owns retry
CONNECT_TIMEOUT_S = 5.0
STREAM_IDLE_TIMEOUT_S = 45.0  # not 600s; 600*(2+1)≈30 min non-stream footgun

# W workload prices (USD / 1M toks) — 2026-09-23 research.
SONNET5_IN, SONNET5_CACHE, SONNET5_OUT = 2.00, 0.20, 10.00
GPT54_IN, GPT54_CACHE, GPT54_OUT = 2.50, 0.25, 15.00
EMBED_3_SMALL_PER_M = 0.02


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "model": getattr(record, "model", None),
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


def build_logger(correlation_id: str, tenant: str, model: str | None = None) -> CorrelationAdapter:
    base = logging.getLogger("llm.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if model:
        extra["model"] = model
    return CorrelationAdapter(base, extra)


_PII = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("acct", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    """Detect → redact before tokenize/embed. Audit placeholders, not plaintext."""
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per (provider, model). Do not trip on 429-with-Retry-After (throttle)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
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
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
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
    """Full jitter. Honor Retry-After only if 0 < value ≤ 60 s (SDK rule)."""
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
            if ra is not None and 0 < ra <= 60:
                sleep_s = ra
            else:
                sleep_s = random.random() * min(cap, base * (2**i))
            log.warning("retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    amount_cents: int
    injection_suspected: bool = False


class InvoiceExtract(BaseModel):
    """Strict-mode compatible: extra=forbid ⇒ additionalProperties: false.
    Every field required (optionality would be T | None, still in required)."""

    model_config = ConfigDict(extra="forbid")
    vendor: str
    invoice_id: str
    currency: str = Field(min_length=3, max_length=3)
    total_cents: int
    lines: list[InvoiceLine]
    injection_suspected: bool


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


def deterministic_invoice(text: str) -> InvoiceExtract:
    cents = [int(x.replace(",", "")) for x in re.findall(r"\$([0-9,]+)", text)]
    return InvoiceExtract(
        vendor="UNKNOWN",
        invoice_id="DEGRADED",
        currency="USD",
        total_cents=sum(cents) * 100 if cents else 0,
        lines=[],
        injection_suspected=False,
    )


def count_openai_tokens(text: str, model: str) -> int:
    """tiktoken for OpenAI models ONLY.
    gpt-5.4 / gpt-4o* / gpt-4.1* → o200k_base (199,998 + 2 special).
    text-embedding-3-* → cl100k_base. cl100k on GPT-5-class mis-estimates.
    WARNING: do NOT use tiktoken for Claude — undercounts ~15–20% typical,
    more on code/non-English. Claude 4.7+ tokenizer is ≈ +30% vs pre-Opus-4.7.
    Use POST /v1/messages/count_tokens with the same model ID you will call.
    Pre-flight ≠ invoice; bill from response.usage.
    """
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding(
            "cl100k_base" if "embedding" in model else "o200k_base"
        )
    return len(enc.encode(text))


async def count_anthropic_tokens(text: str, model: str) -> int:
    from anthropic import AsyncAnthropic
    from anthropic import DefaultAsyncHttpxClient

    client = AsyncAnthropic(
        http_client=DefaultAsyncHttpxClient(),
        max_retries=0,
        timeout=CONNECT_TIMEOUT_S,
    )
    try:
        result = await client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text}],
        )
        return int(result.input_tokens)
    finally:
        await client.close()


def l2_normalize(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0.0:
        raise PermanentError("zero embedding")
    return [x / n for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """OpenAI unit-length ⇒ cosine == inner product. Re-normalize after slice."""
    if len(a) != len(b):
        raise PermanentError(f"dim mismatch {len(a)} vs {len(b)}")
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


@dataclass
class StreamBuffer:
    response_id: str | None = None
    text: str = ""
    cancelled: bool = False
    usage: dict[str, int] = field(default_factory=dict)


def _idle_or_raise(last: float, idle: float) -> None:
    if time.monotonic() - last > idle:
        raise TransientError("stream_idle_timeout")


async def openai_responses_stream(
    prompt: str,
    *,
    model: str = "gpt-5.4",
    max_output_tokens: int = 800,
    idle: float = STREAM_IDLE_TIMEOUT_S,
) -> StreamBuffer:
    from openai import AsyncOpenAI

    buf = StreamBuffer()
    client = AsyncOpenAI(max_retries=0, timeout=idle + CONNECT_TIMEOUT_S)
    last = time.monotonic()
    try:
        async with client.responses.stream(
            model=model,
            input=prompt,
            max_output_tokens=max_output_tokens,
        ) as stream:
            async for event in stream:
                _idle_or_raise(last, idle)
                last = time.monotonic()
                et = getattr(event, "type", "")
                if et == "response.created":
                    resp = getattr(event, "response", None)
                    buf.response_id = getattr(resp, "id", None)
                elif et == "response.output_text.delta":
                    buf.text += getattr(event, "delta", "") or ""
                elif et in {"error", "response.failed"}:
                    raise TransientError(et)
                elif et == "response.completed":
                    usage = getattr(getattr(event, "response", None), "usage", None)
                    if usage is not None:
                        buf.usage = {
                            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                        }
    except asyncio.CancelledError:
        buf.cancelled = True
        # Do not swallow: TaskGroup / asyncio.timeout use cancellation internally.
        # Partial text in buf is the only recovery artifact. Do NOT replay.
        raise
    finally:
        await client.close()
    return buf


async def anthropic_messages_stream(
    prompt: str,
    *,
    model: str = "claude-sonnet-5",
    max_tokens: int = 800,
    idle: float = STREAM_IDLE_TIMEOUT_S,
) -> StreamBuffer:
    from anthropic import AsyncAnthropic
    from anthropic import DefaultAsyncHttpxClient

    buf = StreamBuffer()
    client = AsyncAnthropic(
        http_client=DefaultAsyncHttpxClient(),
        max_retries=0,
        timeout=idle + CONNECT_TIMEOUT_S,
    )
    last = time.monotonic()
    try:
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for event in stream:
                _idle_or_raise(last, idle)
                last = time.monotonic()
                et = getattr(event, "type", "")
                if et == "message_start":
                    msg = getattr(event, "message", None)
                    buf.response_id = getattr(msg, "id", None)
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    buf.text += getattr(delta, "text", "") or ""
                elif et == "ping":
                    continue  # liveness; some SDK iterators historically drop this
                elif et == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        buf.usage["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
    except asyncio.CancelledError:
        buf.cancelled = True
        raise
    finally:
        await client.close()
    return buf


async def openai_responses_parse(prompt: str, *, model: str = "gpt-5.4") -> InvoiceExtract:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0)
    try:
        # SDK type_to_text_format_param sets strict: True for Pydantic types.
        resp = await client.responses.parse(
            model=model,
            input=prompt,
            text_format=InvoiceExtract,
            max_output_tokens=2048,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise PermanentError("empty output_parsed")
        return parsed
    finally:
        await client.close()


async def anthropic_messages_parse(prompt: str, *, model: str = "claude-sonnet-5") -> InvoiceExtract:
    from anthropic import AsyncAnthropic
    from anthropic import DefaultAsyncHttpxClient

    client = AsyncAnthropic(http_client=DefaultAsyncHttpxClient(), max_retries=0)
    try:
        resp = await client.messages.parse(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            output_format=InvoiceExtract,
        )
        parsed = resp.parsed_output if hasattr(resp, "parsed_output") else resp.output_parsed
        if parsed is None:
            raise PermanentError("empty anthropic parsed output")
        return parsed
    finally:
        await client.close()


async def embed_batch(
    texts: list[str],
    *,
    model: str = "text-embedding-3-small",
    dimensions: int = 1536,
) -> list[list[float]]:
    """OpenAI: ≤2048 strings, ≤300k toks summed, ≤8192/input (400 on overflow).
    Pin dimensions in the index schema. API L2-normalizes Matryoshka trim;
    if you slice client-side you MUST re-normalize before cosine.
    Chunk long inputs with tiktoken cl100k_base at 8191, not o200k.
    """
    from openai import AsyncOpenAI

    if len(texts) > 2048:
        raise PermanentError("batch > 2048")
    client = AsyncOpenAI(max_retries=0)
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), 2048):
            resp = await client.embeddings.create(
                model=model,
                input=texts[i : i + 2048],
                dimensions=dimensions,
            )
            by_idx = sorted(resp.data, key=lambda d: d.index)
            for row in by_idx:
                vec = list(row.embedding)
                if len(vec) != dimensions:
                    raise PermanentError(f"expected dim {dimensions} got {len(vec)}")
                out.append(l2_normalize(vec))
    finally:
        await client.close()
    return out


@dataclass
class McpTicket:
    tenant: str
    tool: str
    exp: float
    sig: str

    def verify(self, tenant: str, tool: str, secret: str) -> None:
        if time.time() > self.exp:
            raise PermanentError("mcp_ticket_expired")
        if self.tenant != tenant or self.tool != tool:
            raise PermanentError("mcp_ticket_audience")
        expect = hashlib.sha256(f"{self.tenant}|{self.tool}|{self.exp}|{secret}".encode()).hexdigest()
        if expect != self.sig:
            raise PermanentError("mcp_ticket_bad_sig")


def issue_ticket(tenant: str, tool: str, secret: str, ttl: float = 30.0) -> McpTicket:
    exp = time.time() + ttl
    sig = hashlib.sha256(f"{tenant}|{tool}|{exp}|{secret}".encode()).hexdigest()
    return McpTicket(tenant, tool, exp, sig)


class FallbackChain:
    def __init__(
        self,
        primary: Callable[[str], Awaitable[InvoiceExtract]],
        secondary: Callable[[str], Awaitable[InvoiceExtract]],
        breaker: CircuitBreaker,
        primary_name: str,
        secondary_name: str,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.primary_name = primary_name
        self.secondary_name = secondary_name

    async def extract(self, prompt: str, log: CorrelationAdapter) -> InvoiceExtract:
        async def _call(fn: Callable[[str], Awaitable[InvoiceExtract]]) -> InvoiceExtract:
            return await fn(prompt)

        try:
            await self.breaker.allow()
            result = await retry_with_jitter(lambda: _call(self.primary), log=log)
            await self.breaker.record_success()
            log.info("primary_ok model=%s", self.primary_name)
            return result
        except CircuitOpenError as exc:
            log.warning("breaker_open err=%s", exc)
        except TransientError as exc:
            await self.breaker.record_failure(trip=True)
            log.warning("primary_transient err=%s", exc)
        except PermanentError as exc:
            await self.breaker.record_failure(trip=False)
            log.error("primary_permanent_no_failover err=%s", exc)
            raise
        try:
            result = await retry_with_jitter(lambda: _call(self.secondary), log=log)
            log.info("secondary_ok model=%s", self.secondary_name)
            return result
        except (TransientError, PermanentError) as exc:
            log.error("degraded_deterministic err=%s", exc)
            return deterministic_invoice(prompt)


class ProviderGateway:
    """TaskGroup fan-out + per-vendor semaphore bulkhead + stream cancel."""

    def __init__(self, openai_limit: int = 60, anthropic_limit: int = 60) -> None:
        self.sem_oa = asyncio.Semaphore(openai_limit)
        self.sem_an = asyncio.Semaphore(anthropic_limit)
        self.breaker_oa = CircuitBreaker("openai:gpt-5.4")
        self.breaker_an = CircuitBreaker("anthropic:claude-sonnet-5")
        self.embed_q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)

    async def enqueue_embed(self, text: str) -> None:
        redacted, _ = redact_pii(text)
        await self.embed_q.put(redacted)  # blocks at maxsize — ingest back-pressure

    async def embed_worker(self, *, dimensions: int = 1536) -> None:
        while True:
            text = await self.embed_q.get()
            try:
                async with self.sem_oa:
                    await embed_batch([text], dimensions=dimensions)
            except (TransientError, PermanentError) as exc:
                logging.getLogger("llm.gateway").error("embed_worker_fail err=%s", exc)
            finally:
                self.embed_q.task_done()

    async def fanout_extract(self, prompts: list[str], tenant: str) -> list[InvoiceExtract]:
        cid = str(uuid.uuid4())
        log = build_logger(cid, tenant)
        chain = FallbackChain(
            openai_responses_parse,
            anthropic_messages_parse,
            self.breaker_oa,
            "gpt-5.4",
            "claude-sonnet-5",
        )

        async def _one(p: str) -> InvoiceExtract:
            redacted, audit = redact_pii(p)
            log.info("pii_redactions count=%s sha256=%s", len(audit),
                     hashlib.sha256(redacted.encode()).hexdigest()[:16])
            async with self.sem_oa:
                return await chain.extract(redacted, log)

        async def _isolated(p: str) -> InvoiceExtract:
            # Independent invoices: do not let one PermanentError cancel siblings
            # (TaskGroup otherwise cancels the whole group — CPython 116720).
            try:
                return await _one(p)
            except (PermanentError, TransientError):
                return deterministic_invoice(p)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_isolated(p)) for p in prompts]
        return [t.result() for t in tasks]

    async def stream_chat(self, prompt: str, tenant: str, cancel_after: float | None = None) -> StreamBuffer:
        cid = str(uuid.uuid4())
        log = build_logger(cid, tenant, "claude-sonnet-5")
        redacted, _ = redact_pii(prompt)

        async def _run() -> StreamBuffer:
            async with self.sem_an:
                await self.breaker_an.allow()
                try:
                    buf = await retry_with_jitter(
                        lambda: anthropic_messages_stream(redacted),
                        log=log,
                    )
                    await self.breaker_an.record_success()
                    return buf
                except TransientError:
                    await self.breaker_an.record_failure(trip=True)
                    raise

        task = asyncio.create_task(_run())
        if cancel_after is None:
            return await task
        try:
            async with asyncio.timeout(cancel_after):
                return await task
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                log.warning("stream_cancelled_partial")
                raise


async def _offline() -> None:
    schema = strict_json_schema(InvoiceExtract)
    assert schema["additionalProperties"] is False
    assert "vendor" in schema["required"]
    text = "Acme invoice INV-9 total $12 for user@example.com ssn 123-45-6789"
    redacted, audit = redact_pii(text)
    assert "<email:" in redacted and "<ssn:" in redacted
    assert any(a["type"] == "email" for a in audit)
    det = deterministic_invoice("pay $12")
    assert det.invoice_id == "DEGRADED" and det.total_cents == 1200
    InvoiceExtract.model_validate(det.model_dump())

    a = l2_normalize([3.0, 4.0, 0.0])
    b = l2_normalize([3.0, 4.0, 0.0])
    assert abs(cosine(a, b) - 1.0) < 1e-9
    sliced = l2_normalize([3.0, 4.0])  # client-side MRL slice MUST re-normalize

    br = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)
    log = build_logger("cid", "t1")

    async def boom(_prompt: str) -> InvoiceExtract:
        raise TransientError("529", retry_after=None)

    chain = FallbackChain(boom, boom, br, "primary", "secondary")
    out = await chain.extract("pay $5", log)
    assert out.invoice_id == "DEGRADED"
    assert br.state is BreakerState.OPEN
    await asyncio.sleep(0.06)
    try:
        await br.allow()
    except CircuitOpenError:
        raise AssertionError("should be half-open after recovery") from None
    await br.record_success()
    assert br.state is BreakerState.CLOSED

    slept: list[float] = []

    async def _sleep(s: float) -> None:
        slept.append(s)

    real_sleep = asyncio.sleep
    asyncio.sleep = _sleep  # type: ignore[method-assign]
    try:
        async def once() -> int:
            raise TransientError("429", retry_after=0.4)

        try:
            await retry_with_jitter(once, log=log, attempts=2, base=0.5, cap=8.0)
        except TransientError:
            pass
        assert slept and abs(slept[0] - 0.4) < 1e-9
    finally:
        asyncio.sleep = real_sleep  # type: ignore[method-assign]

    secret = "mcp-secret"
    ticket = issue_ticket("acme", "lookup_invoice", secret)
    ticket.verify("acme", "lookup_invoice", secret)
    try:
        ticket.verify("other", "lookup_invoice", secret)
        raise AssertionError("cross-tenant ticket must fail")
    except PermanentError:
        pass

    q: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
    await q.put(1)
    try:
        q.put_nowait(2)
        raise AssertionError("QueueFull expected")
    except asyncio.QueueFull:
        pass

    print(json.dumps({
        "ok": True,
        "schema_required": schema["required"],
        "pii": [a["type"] for a in audit],
        "cosine_unit": 1.0,
        "mrl_sliced_dim": len(sliced),
        "breaker": br.state.value,
        "degraded": det.model_dump(),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live:
        if not (os.environ.get("OPENAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")):
            raise SystemExit("need OPENAI_API_KEY and ANTHROPIC_API_KEY for --live")
        asyncio.run(ProviderGateway().fanout_extract(["Invoice ACME INV-1 total $10"], "t1"))
        return
    asyncio.run(_offline())


if __name__ == "__main__":
    main()
```

**Behavior encoded (maps to §§1–4):**

- Full-jitter retries; `Retry-After` honored iff \(0 < t \leq 60\); spend-cap 429 (`retry_after is None`) is **permanent**.
- Breaker closed → open → half-open; 429-with-RA does not trip; probe path is `allow()` after recovery timer.
- Fallback primary → secondary → schema-valid `invoice_id=DEGRADED`. **PermanentError on primary does not failover** (schema 400 would fail everywhere).
- JSON logs carry `correlation_id` + tenant; PII redacted before it would be tokenized; audit is placeholders.
- `TaskGroup` + per-vendor `Semaphore`; stream cancel via `asyncio.timeout` / `CancelledError` **re-raised**; partial buffer is the recovery artifact.
- OpenAI Responses `parse` / `stream` and Anthropic Messages `parse` / `stream` use real SDK surfaces; Anthropic client is `DefaultAsyncHttpxClient` (httpx2).
- tiktoken warning in `count_openai_tokens`; Claude must use `count_tokens` API.
- Pydantic `extra="forbid"` + `strict_json_schema` walks `$defs` to force `additionalProperties: false` and `required = all properties`.
- Embed batch pins `dimensions`, re-L2-normalizes, rejects dim mismatch; cosine is dot product on unit vectors.
- MCP ticket: audience + expiry + HMAC-equivalent digest; identity is not model JSON.

**Interview talking point:** retries with jitter handle 529; they do not make `create_ticket` safe. Idempotency + round cap + schema-valid deterministic fallback are three different classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers from the research file; scale-up arithmetic marked **[inferred]**.

### Scenario 1 — Multi-provider Python gateway (50k streaming chat req/min + embedding ingest)

**Problem statement.** B2B copilot: **50,000 streaming chat requests/minute** peak (~833 rps) with workload **W** (2k in / 800 out, 80% cache hit on input), plus nightly embedding ingest of **10M chunks × 512 tokens = 5.12B tokens**. p95 TTFT **< 2 s** **[inferred policy]**. Finance wants cache-aware routing (Anthropic `cache_control` vs OpenAI automatic / `prompt_cache_key`). Tokenizer discipline: `o200k_base` for GPT-5.4 chat, `cl100k_base` for `text-embedding-3-*`, Anthropic `count_tokens` for Claude — tiktoken-on-Claude is a billing incident. Multi-tenant; ZDR org cannot put PHI on Batch/Files.

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  (N gateway replicas)                               │
                    │ auth, tenant TPM, Idempotency-Key on YOUR POST           │
                    │ correlation-id, PII redact, MCP ticket mint              │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  Python 3.11+  (one AsyncOpenAI + AsyncAnthropic │
                    │          per process; httpx2/aiohttp; max_retries=0)     │
                    │  Router: cache-warm prefix → stay vendor; 529 → failover │
                    │          extract → Haiku/4.1; chat → Sonnet5/GPT-5.4     │
                    │  Sem_oa / Sem_an bulkhead    Breaker per (vendor,model)  │
                    │  count: tiktoken(o200k) vs messages.count_tokens         │
                    │  TaskGroup tools (cancel-on-fail); sequential computer   │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │ SSE chat                      │ embed Queue+workers
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  Generation │            │ DATA  Embedding         │
                    │ Sonnet 5 /       │            │ 3-small dims=1536 pin   │
                    │ GPT-5.4 Fast*    │            │ Batch API nightly 50%   │
                    │ KV prompt cache  │            │ Voyage-4 family optional│
                    └────────┬─────────┘            └──────────┬──────────────┘
                             │                                 │
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ TOOL PROXIES MCP │            │ PERSIST  pg batch_id    │
                    │ ticket verify    │            │ Redis SSE partials      │
                    │ least-priv tools │            │ ANN pin model+dim+metric│
                    └──────────────────┘            │ TELEMETRY usage+SHA-256 │
                                                    └─────────────────────────┘
* Fast mode Enterprise only; shares RPM/TPM; ramp >50%/15min can demote to Standard.
```

**Technology choices.** Primary chat: **Claude Sonnet 5** for cache read **0.1×** ($0.20) and explicit breakpoints; failover **GPT-5.4** ($0.25 cached). Extract/classify: Haiku 4.5 ($4.56/1k W) or GPT-4.1 ($8.00/1k W). Nightly index: OpenAI Batch `text-embedding-3-small` **$0.01/1M** → **$51.20** for 5.12B tokens (does not touch daytime RPM). Voyage-4-lite after 200M free: 4.92B × $0.02/M = **$98.40** (or Batch 33% off). HTTP: `DefaultAioHttpClient` on Anthropic; consider `AiohttpTransport` on OpenAI. Timeouts: connect **5 s**, stream idle **30–60 s**, never 600 s on interactive. Connection pools sized to **per-process** concurrency (keepalive ≈ 2S **[inferred]**), horizontally sharded — not one Python process holding 26k SSE sockets.

**Capacity math [inferred from research limits]:** 50k RPM × 800 out = **40M OTPM**. Anthropic Scale OTPM **2M** is **20×** too small; OpenAI T5 **15k RPM / 40M TPM** is **3.3×** short on RPM and **3.5×** short on TPM if 50k×2,800 tokens count. Concurrent SSE at 25 tok/s: **~26,667**. Cost at W Sonnet 5 cached **$9.12/1k** × 50 = **$456/min** peak (**$27,360/hour** if sustained). GPT-5.4 W **$13.40/1k** → **$670/min**. Binding constraints are **OTPM, TPM, fds, and $**, not asyncio.

**Trade-off evaluation matrix.**

| Dimension | A. Single-vendor Sonnet 5, one process, SDK defaults (1000/100 pool, max_retries=2) | B. Recommended: multi-provider gateway, bulkheads, cache-aware route, sharded processes, Batch embed | C. All interactive on Opus 5 Fast + sync embed 3-large |
| --- | --- | --- | --- |
| **Cost / 1k chat (W)** | **$9.12** cached; silent retry amplification on 429 | **$9.12** primary; Haiku **$4.56** extract; embed nightly **$51.20** | Opus 5 W **$22.80**; Fast **$10/$50** list; 3-large 5.12B × $0.13/M = **$665.60** sync |
| **Latency** | Embed bursts steal chat keepalives; 600 s timeout footgun | Separate pools; p50 TTFT policy <800 ms; Fast only if Enterprise SLO needed | Fast 50 tok/s helps TPOT; TTFT still thinking-bound; embed 3-large slower/costlier |
| **Ops complexity** | Low until 26k sockets / OTPM 429 storm | Medium (two SDKs, httpx2, shard math, poll Batch) | Low until invoice; spend-cap 429 has **no** Retry-After |
| **Security posture** | Shared prefix risk if tenant docs cached together | `prompt_cache_key` + left-stable tools; PII before tokenize; MCP tickets; Batch **not** for PHI | Same Fast ZDR story; 3-large index still Vec2Text-invertable |
| **Scalability ceiling** | Scale OTPM 2M vs need 40M; keepalive 100 vs 26k | Multi-org/provisioned/self-host; Batch queue T5 15B tokens; embed Tier5 10k RPM / 10M TPM | Fast shares RPM/TPM; ramp demotes Fast→Standard |

**Decision rationale.** **B** is the only option that treats 50k RPM as a **distributed systems** problem (shard, bulkhead, Batch for ingest) and a **tokenizer/cost** problem (o200k vs `count_tokens`, cache on the left). A collides embed and chat on one pool and will 429 OTPM while the event loop still looks healthy. C blows W by **2.5×** (Opus vs Sonnet cached) plus Fast premiums, and sync 3-large ingest is **13×** the 3-small Batch bill with no quality SLA that MTEB 64.6% vs 62.3% will save the product. Quote the billing page (Opus 5.5 $4/$20 vs Opus 5 $5/$25) in the design review.

### Scenario 2 — Structured-extraction service (invoices / PII)

**Problem statement.** Accounts-payable copilot: **200 invoices/min** (~3.3 rps), each 4–8 pages. Extract vendor, totals, line items into **strict JSON**. Documents contain account numbers, emails, sometimes SSNs. Compliance: ZDR org; **no PHI/PII on Batch or Files APIs**; immutable audit of who extracted what; Vec2Text means you do **not** dump raw invoice text into a shared vector index “for semantic search.” Target: schema-valid JSON **≥99%** of completed jobs; p95 extract **< 8 s**; never execute a tool on truncated `partial_json`. Fallback must not emit free-form text (downstream ERP parser).

**Proposed architecture.**

```
  ┌─────────────┐    ┌─────────────────────────────────────────────────────────┐
  │ ERP / email │───▶│ CONTROL  Temporal workflow invoice_id = idempotency key │
  │ ingest      │    │  1. DLP detect→redact→audit map                         │
  │             │    │  2. count_tokens (Claude) / tiktoken o200k (GPT)        │
  │             │    │  3. Pydantic InvoiceExtract → strict JSON Schema        │
  │             │    │  4. messages.parse / responses.parse  (NOT json_object) │
  └─────────────┘    │  5. validate locally; 400 schema = permanent (no FO)    │
                     │  6. HITL if injection_suspected or total mismatch       │
                     └───────────┬───────────────────────────┬─────────────────┘
                                 │                           │
                                 ▼                           ▼
                     ┌─────────────────────┐     ┌─────────────────────────────┐
                     │ DATA  constrained   │     │ TOOL PROXIES                │
                     │ Sonnet 5 strict     │     │ lookup_vendor (MCP ticket)  │
                     │ → GPT-5.4 parse     │     │ never create_payment here   │
                     │ → deterministic $   │     │ JSON-encode all results     │
                     │ vLLM xgrammar only  │     └─────────────────────────────┘
                     │ if air-gapped GPU   │
                     └──────────┬──────────┘
                                ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │ PERSIST  Postgres checkpoint + WORM audit               │
                     │ (cid, sha256 redacted, model, usage, stop_reason,      │
                     │  ticket_id, allowed_tools, human decision)              │
                     │ Vectors: redacted text only, ACL=tenant, dim pinned     │
                     └─────────────────────────────────────────────────────────┘
```

**Technology choices.** Default: Anthropic `client.messages.parse()` + `output_config.format=json_schema` (or strict tool `extract`). SDK `parse()` path. OpenAI `responses.parse` + Pydantic (`strict: True` forced). **Not** Instructor as the primary (pays extra tokens; still fails). **Not** JSON mode (no schema; needs substring `json`). Thinking **off** / adaptive default watched — thinking is output-billed and blows `max_tokens`. Do **not** put invoices on Message Batches despite 50% ($6.00/1k W no-cache Sonnet) — ZDR excludes batches. Sync Messages/Responses only. Optional self-host vLLM xgrammar if the schema catalog is **~20** invoice types (pin backend at engine start; unique-per-doc schemas miss the compile cache). Embeddings for “find similar invoices”: redact first; pin 3-small **1536**; cosine on unit vectors; tenant namespace.

**Cost [inferred]:** if each invoice ≈ W (2k/800) on Sonnet 5 **no cache** (unique docs) = **$12.00/1k** = **$0.012/invoice**. 200/min × 60 × 24 = 288k/day → **~$3,456/day** uncached. A 1,600-token **form schema + tool** prefix with 5m cache: W cached **$9.12/1k** → **~$2,626/day** (**25%** off) after the first write ($0.004 on the 1,600-token prefix). Haiku 4.5 cached **$4.56/1k** if eval quality allows. Instructor retries at 2× would **double** the uncached bill with still-no grammar guarantee.

**Trade-off evaluation matrix.**

| Dimension | A. Prompted JSON + Instructor retries + Batch 50% | B. Recommended: native strict parse, sync only, Temporal, PII-before-tokenize, deterministic last mile | C. vLLM xgrammar on-prem, unique schema per invoice |
| --- | --- | --- | --- |
| **Cost / 1k** | Instructor 1–N×; Batch **$6.00** W no-cache Sonnet but **ZDR-ineligible** | Sonnet cached **$9.12** / uncached **$12.00**; Haiku **$4.56** if quality holds | GPU capex; no per-token vendor bill; compile miss if schema unique |
| **Latency** | Retry loops on ValidationError dominate p95; Batch 24h | p95 < 8 s on 3.3 rps is easy vs Start 1k RPM; stream optional for UX | First-request compile; catalog hits are fast (XGrammar up to 80× e2e on cited bench) |
| **Ops complexity** | Low until silent wrong JSON hits ERP | Medium (Temporal + two parse SDKs + DLP) | High (GPU, pin V1 backend, grammar cache) |
| **Security posture** | Batch retains application state; raw text in traces; vectors invertible | Redaction + WORM SHA-256; no Batch/Files; MCP ticket on lookup; HITL on `injection_suspected` | Air-gap wins data-residency; still need DLP — GPU does not encrypt embeddings |
| **Scalability ceiling** | Batch 100k req / 24h; quality unbounded | Start-tier 1k RPM ≫ 200 RPM; OTPM 400k ≫ 200×800/60 ≈ 2.7k tok/s wait — 200×800 = 160k tokens/min ≪ 400k OTPM | QPS = GPU decode; unique schemas neutralize PDA cache (llguidance alternative) |

**Decision rationale.** **B** wins because the constraint set is **schema validity + PII + ZDR**, not raw $/token. A’s 50% Batch discount is unavailable without breaking ZDR, and Instructor is the wrong layer (post-hoc, not logit mask). C wins only with a **small, repeated** schema catalog and an air-gap mandate — unique per-invoice schemas throw away XGrammar’s compile cache, which is the reason to self-host. Deterministic fallback must still be `InvoiceExtract`-valid so ERP never sees a markdown fence. Permanent 400s **must not** failover (scenario-1 rule applies). Audit reconstructs: redacted hash + model + `output_parsed` + ticket + human decision.

---

## Key numbers to memorize

| Number | What |
| --- | --- |
| **$9.12 / $12.00 / $6.00** | Sonnet 5 W $/1k cached / uncached / Batch uncached **[inferred]** |
| **$13.40 / $8.00 / $8.80 / $4.56 / $22.80 / $17.92** | GPT-5.4 / GPT-4.1 / GPT-6 Sol / Haiku 4.5 / Opus 5 / Opus 5.5 W $/1k **[inferred]** |
| **$0.04 / $0.26** | Embed 3-small / 3-large W (2k) $/1k **[inferred]** |
| **300×** | Sonnet 5 uncached chat vs 3-small embed on 2k input **[inferred]** |
| **$10.24 / $5.12** | 1M chunks × 512 tok 3-small sync / Batch |
| **$51.20** | Nightly 10M × 512 tok 3-small Batch |
| **1.25× / 2× / 0.1×** | Anthropic 5m write / 1h write / cache read (Fable 5.1 read **0.025×**) |
| **272K → 2× in / 1.5× out** | GPT-5.4 long-context cliff (full session) |
| **+10% / 1.1×** | OpenAI regional (models ≥2026-03-05); Anthropic US geo 4.6+ |
| **15–20% / +30%** | tiktoken undercount on Claude; Claude 4.7+ tokenizer vs old |
| **354 / 474** | Sonnet 5 tool-use system tokens `auto` / `any`\|`tool` |
| **512–4096** | Anthropic min cache prefix by model class |
| **8192 / 2048 / 300k** | OpenAI embed per-input / batch size / summed tokens |
| **1536 / 3072** | 3-small / 3-large default dims; API L2-norms `dimensions=` |
| **92% / 89%** | Vec2Text exact 32-tok recovery / full names in MIMIC |
| **600 s × 3 ≈ 30 min** | Default timeout × (max_retries+1) non-stream footgun |
| **0.5 s / 8 s / retries=2** | SDK initial delay / cap / max_retries |
| **1000 / 100** | SDK default max_connections / max_keepalive |
| **50 / 80 / 100 tok/s** | Fast mode GPT-5.4 / GPT-4.1 / Luna (Enterprise 5-min windows) |
| **15k RPM / 40M TPM** | OpenAI GPT-5.4 Tier 5 typical |
| **10k / 10M / 2M** | Anthropic Scale RPM / ITPM / OTPM |
| **cache reads ∉ ITPM** | Anthropic except Haiku 3.5 |
| **50% / 33%** | OpenAI+Anthropic Batch / Voyage Batch (no free-tier on Voyage Batch) |
| **100k / 50k** | Anthropic batch max requests / OpenAI batch max requests |
| **24 h** | Batch expiry; Anthropic strict-tool grammar cache since last use |
| **99.9%** | OpenAI Fast mode uptime SLO (Enterprise) |
| **o200k = 199,998+2** | Regular + special tokens |

**Interview closer:** “I bill from `usage`, plan with the provider tokenizer, isolate embed sockets from chat SSE, constrain decode with strict schema, and treat MCP as a ticket-checked proxy — not as the model’s IAM.”
