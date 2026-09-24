# Topic 1: Python & LLM Foundations
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep
> Pricing data vintage: September 2026

---

## Introduction

### What This Topic Covers

This is the **foundational layer** of the entire AI engineering stack. Python & LLM Foundations covers how to correctly call hosted language models and embedding APIs from production Python — async patterns, SDK architecture (Anthropic vs OpenAI), tokenization internals, embedding models, structured outputs, rate limiting, and connection management. Every agent, RAG pipeline, and orchestration framework you will study in later topics is built on these primitives. If you get this wrong, everything downstream breaks.

### Why Study This

- **Interview signal**: Interviewers use this topic to separate builders from tutorial-followers. Knowing that prompt cache stores KV tensors (not token strings), that tiktoken on Claude undercounts by 15-20%, or that a 600s SDK timeout times `max_retries+1` creates a 30-minute hang — these details signal production experience.
- **Cost impact**: A single misconfigured client (wrong model tier, no caching, no batching) can 10x your inference bill. Understanding token economics here saves real money.
- **Prerequisite**: Topics 2-11 all assume fluency with async Python, SDK patterns, tokenization, and embedding mechanics.

### What Details Are Included

- Full pricing tables for Anthropic (Claude Opus/Sonnet/Haiku) and OpenAI (GPT-4o/o3/Sol) as of September 2026
- BPE tokenization algorithm with complexity analysis
- Async patterns with connection pooling, semaphores, and backpressure
- Circuit breaker and fallback chain implementations (production Python)
- Embedding model comparison with MTEB scores and Matryoshka dimensionality
- Rate limit tables by tier, latency benchmarks (TTFT, tokens/sec)
- Two enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2-3 hours)**: Read sections 1-4 to build mental models. Focus on the architecture diagram and request flow narrative — try to redraw them from memory.
>
> **Second pass (2-3 hours)**: Deep-dive sections 5-6. Run the code examples locally. Modify the circuit breaker thresholds and observe behavior.
>
> **Interview prep (1 hour)**: Jump to section 10 (Interview Quick Reference). Memorize the key numbers table. Practice explaining one system design scenario end-to-end in 10 minutes.
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

**Python & LLM foundations** is the client-side architecture of calling hosted (or self-hosted) language and embedding models from production Python. It covers asyncio vs threads, HTTP/SSE transport, OpenAI Responses vs Chat Completions, Anthropic Messages + tool_use + thinking, tokenizer selection, embedding serving, and constrained decoding. Every downstream agent, RAG pipeline, and eval stack is built on these primitives.

The unit of production is not `client.chat.completions.create()`. It is a **Python control plane**: one shared async client per vendor, `TaskGroup` fan-out, semaphore back-pressure, schema compilation, tokenizer matched to the model you will call, and a tool host that treats the model as an untrusted planner. Hosted APIs own tokenizer, prefill/decode, sampler, and parser; they **never execute customer tools** -- they emit structured requests your process must run.

**Why it matters for interviews**: Interviewers probe whether you know that prompt cache stores KV tensors not tokens, that `ThreadPoolExecutor.submit` never blocks, that tiktoken on Claude undercounts 15-20%, that embeddings are invertible (Vec2Text recovered 92% of 32-token inputs), and that a 600s SDK timeout multiplied by `max_retries+1` is a 30-minute hang. Getting these wrong signals that you have not operated LLM APIs at production scale.

---

## 2. Core Concepts

### 2.1 Tokenization -- Byte Pair Encoding (BPE)

BPE underpins tokenization across GPT, Claude, Gemini, and Llama model families. It converts raw text into integer tokens that the model processes.

**Algorithm** (Philip Gage 1994, adapted by Sennrich et al. 2016 for NMT):

```
Input:  Raw byte sequence of training corpus
Output: Vocabulary V of size |V| = target_vocab_size

1. Initialize V = {byte_0, byte_1, ..., byte_255}   -- 256 base tokens
2. REPEAT:
   a. Count frequency of every adjacent token pair (t_i, t_{i+1}) in corpus
   b. Select pair (a, b) with maximum frequency
   c. Create new token t_new = merge(a, b)
   d. Add t_new to V
   e. Replace all occurrences of (a, b) in corpus with t_new
3. UNTIL |V| == target_vocab_size
```

**Complexity**: O(N * |V|) where N = corpus size in bytes. Each merge pass is O(N) and there are |V| - 256 merge passes. Encoding at inference time (tiktoken) is **Theta(n)** in input bytes via hash-map merges; decoding is **Theta(m)** for m tokens.

**Key invariants**:
- No OOV tokens -- any byte sequence is representable via the 256 base tokens
- High-frequency substrings get single tokens; rare text decomposes to byte-level pieces
- Token density: ~1.3 tokens/English word, ~1.5-3x for code, ~3-6x for Japanese/Arabic

**Production tokenizer map (September 2026)**:

| Encoding | Models | Vocab Size | Library |
|----------|--------|------------|---------|
| `o200k_base` | `gpt-4o*`, `gpt-4.1*`, `gpt-5*`, `o1`/`o3`/`o4-mini` | 199,998 + 2 special | tiktoken |
| `o200k_harmony` | `gpt-oss-*` | ~200k | tiktoken |
| `cl100k_base` | `gpt-4` (non-4o), `gpt-3.5-turbo`, **`text-embedding-3-small/large`**, `ada-002` | 100,256 | tiktoken |
| `p50k_base`/`r50k_base` | legacy davinci / GPT-3 | ~50k | tiktoken |
| Anthropic (Claude <= 4.6) | Proprietary | ~100k | `client.messages.count_tokens()` |
| Anthropic (Claude >= 4.7) | Newer tokenizer (~30% more tokens for same text) | ~100k | Same API |
| Meta (Llama) | SentencePiece BPE | 128,000 | sentencepiece |

**Cost lever**: Same UTF-8 can be shorter under o200k than cl100k (cookbook Japanese example: 9 vs 8 tokens). Switching from cl100k_base to o200k_base cuts per-character token cost for Portuguese and Indonesian by ~35%. The tokenizer alone moves the cost needle.

**Critical rule -- do NOT use tiktoken for Claude**: tiktoken undercounts Claude ~15-20% on typical text, more on code/non-English. Use `POST /v1/messages/count_tokens` with the **same model ID** you will call. Claude 4.7+ / Fable 5 / Mythos 5 share a newer tokenizer producing ~30% more tokens for the same text vs pre-Opus-4.7. The old SDK `count_tokens(text)` is not accurate for Claude 3+. The token-counting API has a separate rate pool (third-party notes: 100 RPM tier1 up to 8,000 tier4).

**Emerging alternatives**:
- **Byte Latent Transformer (BLT)** -- Meta: Groups bytes into variable-length patches via an entropy model at training time. 8B-parameter BLT matches Llama-3 8B BPE on benchmarks with better typo/code robustness.
- **T-FREE** (Deiseroth et al., 2024): Tokenizer-free LLM using sparse hashed character-trigram embeddings. Embedding table compresses by ~85% vs BPE vocab.

**Invariant T1**: Pre-flight counts are capacity planning. Invoices come from response `usage` (`input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `thinking_tokens` / OpenAI `cached_tokens` / `cache_write_tokens`).

**Invariant T2**: Thinking tokens are output-billed. Anthropic reports them on final `message_delta` as `usage.output_tokens_details.thinking_tokens`. They count against `max_tokens` on older `budget_tokens` models (`budget_tokens >= 1024`; interleaved thinking can exceed `max_tokens`). Opus 4.6+ / Sonnet 4.6+ / Fable 5 / Opus 5: adaptive thinking; sending `budget_tokens` is 400.

### 2.2 Embeddings Geometry

An embedding is a map f: text -> R^d. Serving is a **single-shot encoder**: no decode loop, no KV growth, no sampler. This is a fundamentally different topology from generation -- mixing embedding traffic with streaming decode sockets on one `httpx.Limits` pool lets an ingest burst steal keepalives from TTFT-sensitive chat.

**OpenAI `POST /v1/embeddings`**: Per-input max **8,191-8,192** tokens, **300,000** tokens summed/request, max **2,048** strings; overflow is **400**, not truncation. Chunk with `cl100k_base` at 8,191. Default dims: `text-embedding-3-small` **1,536**, `text-embedding-3-large` **3,072**.

**Matryoshka Representation Learning (MRL)**: The `dimensions=` parameter is MRL (Kusupati et al., NeurIPS 2022) truncation, and the API L2-normalizes the result. Manual client-side slice **must** re-normalize:

```
v_hat = v[1:k] / ||v[1:k]||_2
```

OpenAI vectors are unit-length, so **cosine = inner product** and cosine rankings = Euclidean rankings. A 256-dim text-embedding-3-large embedding often outperforms a 1,536-dim ada-002 embedding. This cuts vector storage by 6x and accelerates similarity search proportionally.

**Production rule**: The embedding model has a bigger impact on retrieval quality than the vector database choice. A great embedding model with a basic store outperforms a mediocre model with the fanciest database.

**Voyage 4**: Context **32,000**; dims 256/512/**1,024**/2,048; dtypes `float|int8|uint8|binary|ubinary`. All Voyage 4 models share one space (asymmetric: index `voyage-4-large`, query `voyage-4-lite`). `input_type` `query`/`document` prepends a prompt. Batch max **1,000** texts. Binary packing: returned int list length = `output_dimension / 8`. Hamming/IP on packed bits, not float cosine.

**Cohere `embed-v4.0`**: Context **128k**; dims 256/512/1024/**1,536**; multimodal; max **96** inputs/call.

**Embedding pricing ($/MTok)**:

| Model | Price/MTok |
|-------|-----------|
| text-embedding-3-small | $0.02 |
| text-embedding-3-large | $0.13 |
| Google text-embedding-005 | $0.00625 |
| text-embedding-ada-002 (legacy) | $0.10 |
| voyage-4-lite | $0.02 |
| voyage-4 | $0.06 |
| voyage-4-large | $0.12 |

**Invariant E1**: Pin `(model_id, dimensions, similarity, output_dtype)` in the index schema. Indexing 3,072-d and querying 1,536-d (or `dimensions=256` vs full) is a silent ANN disaster. Mixing `voyage-3` and `voyage-4` is unsupported; mixing Voyage 4 sizes is supported.

**Invariant E2**: Cosine on unnormalized inner-product models changes rankings. MTEB v2.0.0 ignored `similarity_fn_name` and forced cosine (issue #1731) -- do not treat leaderboard metric as your index metric.

**Invariant E3**: MTEB 62.3% / 64.6% / ada 61.0% is an aggregate, not your corpus nDCG.

**Invariant E4 (Security)**: Embeddings are **not encryption**. Vec2Text (Morris et al., EMNLP 2023): **92%** of 32-token inputs recovered exactly (BLEU 97.3), **89%** of full names from embedded MIMIC notes. Treat the vector index as equivalent to raw text for DLP, ACL, and retention.

### 2.3 HNSW -- Approximate Nearest Neighbor Search

**Hierarchical Navigable Small World graphs** power sub-50ms similarity search over 10M+ vectors in production vector stores (Pinecone, Qdrant, Weaviate, pgvector).

**Algorithm** (Malkov & Yashunin 2018):

```
Structure: L layers of navigable small-world graphs
           Layer 0: all vectors
           Layer l: random subset of Layer l-1 (exponential decay)

Insert(v):
  1. Assign v to layers 0..l where l ~ -ln(uniform()) * m_L
  2. From top layer, greedily descend to layer l+1 finding nearest neighbor
  3. At each layer l..0, connect v to M nearest neighbors
  4. Maintain degree constraint: prune edges if any node exceeds M_max

Search(q, k):
  1. Enter at top layer with a single entry point
  2. Greedy search at each layer: move to the neighbor closest to q
  3. At layer 0: beam search with ef_search candidates, return top-k
```

**Complexity**: O(log N) search time (N = number of vectors). O(N * M * log N) build time.

**Trade-offs**: Higher `ef_search` improves recall but increases latency. Higher `M` (edges per node) improves recall but increases memory and build time. Typical production settings: 95-99% recall at < 5ms for 10M vectors.

**Key invariant**: Cosine similarity and dot product are equivalent for L2-normalized embeddings (OpenAI embeddings are pre-normalized). Always verify normalization before selecting a distance metric.

### 2.4 Constrained Decoding vs Post-Hoc Parse

Both Anthropic and OpenAI guarantee schema compliance via constrained decoding. At each decode step, the JSON schema (compiled into a CFG/PDA/FSM) constrains valid next tokens. Invalid tokens receive -infinity logit bias.

| Layer | Guarantee | Cost | When to use |
|-------|-----------|------|-------------|
| Prompt "return JSON" | None | 1x | Never in prod |
| JSON mode `json_object` | Parseable only; needs substring `json` | 1x | Legacy models |
| Instructor + Pydantic | Validate/retry | 1-Nx | APIs without strict |
| OpenAI/Anthropic `strict` | Constrained decode on a schema subset | 1x + compile | Default |
| vLLM xgrammar | Constrained, self-host | GPU + compile | High-QPS JSON |

**Constrained decoding backends**: vLLM applies the bitmask after logits, before sampling. XGrammar: byte-level PDA, up to 100x grammar speedup, up to 80x e2e structured serving (Llama 3.1 / H100). Outlines: FSM vocabulary index, O(1) average token mask. Mask work per token is O(|V|) worst-case.

**Schema compliance rates**: OpenAI: 100% schema compliance in evals on GPT-4o. Anthropic: ~99.8%+ compliance (< 0.2% failure across 300k calls on Sonnet 4.6). First request with a new schema incurs compilation latency (Anthropic caches compiled grammars for 24 hours).

**Strict schema subset**: Every object `additionalProperties: false`; every property in `required`; optionality = union with `null`, not omitted keys. Unsupported features (recursion, external $ref, numeric bounds, string-length constraints) return **400**, not silent strip. JSONSchemaBench (10k real schemas): coverage gaps on `multipleOf`, `uniqueItems`, `contains`, `patternProperties`, `format`.

**Failure even with a mask**: `stop_reason=refusal`; incomplete JSON at `max_tokens`; truncated `partial_json` if `eager_input_streaming` skipped server validation; **semantic** bypass (`{"amount": -1}` is schema-valid). Grammar is a subset of syntax. Authorization is a subset of the tool host.

**OpenAI Chat Completions vs Responses (interview table)**:

| Concern | Chat Completions | Responses |
|---------|-----------------|-----------|
| Input | `messages=[...]` | `input=`; `instructions=` top-level |
| Token cap | `max_tokens` / `max_completion_tokens` | `max_output_tokens` |
| JSON schema | `response_format={type:"json_schema",...}` | `text={format:{type:"json_schema", name, strict, schema}}` |
| Function tools | Nested `{type:"function", function:{name, parameters}}` | **Flat** `{type:"function", name, parameters}` |
| Strict default | `strict` off | Omitting `strict` attempts strict; falls back if incompatible |
| Parse helper | `client.chat.completions.parse` | `client.responses.parse` -> `output_parsed` |
| Tools on GPT-6 Astra | -- | Required for tool calling |

**Anthropic structured**: `output_config.format = {type:"json_schema", schema}` + `client.messages.parse()`. Strict tool use: `"strict": true` compiles `input_schema` into a grammar (same pipeline); schemas cached up to 24h since last use. Pre-native: `tool_choice: {type:"tool", name:"extract"}` and read `tool_use.input`.

### 2.5 asyncio Scheduling

`asyncio` is the production default: each in-flight HTTP stream is a coroutine, not a thread. Python 3.11 `TaskGroup`: tasks from `tg.create_task()` are awaited on context exit; first non-`CancelledError` failure cancels siblings and raises `ExceptionGroup`. Swallowing `CancelledError` breaks `TaskGroup` and `asyncio.timeout()`.

| Primitive | Blocks producer? | Caps in-flight? | LLM use |
|-----------|-----------------|-----------------|---------|
| `asyncio.Semaphore(N)` | Yes, at `async with sem` | Yes | Cap provider calls to RPM/TPM |
| `asyncio.Queue(maxsize=M)` | Yes, on `await put()` | Buffer + workers | Ingest -> embed |
| `ThreadPoolExecutor.submit` | **No** (unbounded queue) | Only running threads | Sync SDK -- wrap with a semaphore |

**Critical details**:
- `ThreadPoolExecutor` uses an unbounded work queue; `submit()` never blocks. Production: `threading.Semaphore(max_workers + queue_slots)` around `submit`.
- `asyncio.Queue` is not thread-safe. `put_nowait()` raises `QueueFull`.
- Do not use `ProcessPoolExecutor` for HTTP clients (pickle is wrong; LLM calls are I/O-bound).
- `uvloop` provides 2-4x throughput over stock asyncio for I/O workloads.
- Nested `TaskGroup` + parent cancel: CPython issue 116720 -- child group with errors and external cancel re-cancels the parent so `CancelledError` is not lost inside an `ExceptionGroup`.

**Scheduling complexity**: Task switch is O(1). The scarce resources are file descriptors / keepalive slots (each SSE holds a connection for the full decode) and vendor OTPM, not the event loop. Pool size: concurrent streams S, keepalive ~ 2S (not the SDK default `max_connections=1000`).

### 2.6 Token Bucket Rate Limiting

Anthropic's server-side rate limiter uses the token bucket algorithm. Understanding it is essential for client-side mirroring.

```
State: {tokens: float, last_refill: timestamp, capacity: int, refill_rate: float}

On request(cost):
  1. refill = (now - last_refill) * refill_rate
  2. tokens = min(capacity, tokens + refill)
  3. last_refill = now
  4. IF tokens >= cost:
       tokens -= cost
       ALLOW
     ELSE:
       DENY (429 Too Many Requests)
       retry_after = (cost - tokens) / refill_rate
```

**Properties**: Permits burst up to `capacity`; sustains `refill_rate` tokens/second over time. Unlike fixed-window counters, no "burst at window boundary" problem.

**Why it matters for LLM APIs**: Rate limits are measured separately as RPM, ITPM, and OTPM. Each is its own bucket. A single request can be blocked by any of the three. With prompt caching, only uncached input tokens + cache creation tokens count toward ITPM. An 80% cache hit rate on a 2M ITPM limit effectively supports ~10M real input tokens/minute.

### 2.7 HTTP / SSE Mechanics

**SDK defaults (OpenAI Python)**: `DEFAULT_TIMEOUT = httpx.Timeout(timeout=600, connect=5.0)` (10 min), `DEFAULT_MAX_RETRIES = 2` (3 attempts), `Limits(max_connections=1000, max_keepalive_connections=100)`, `INITIAL_RETRY_DELAY = 0.5`, `MAX_RETRY_DELAY = 8.0`. Both SDKs retry connection errors, 408, 409, 429, >=500; honor `retry-after-ms` then `Retry-After` if 0 < value <= 60s, else exponential backoff + jitter, cap 8s.

**SSE (WHATWG)**: MIME `text/event-stream`, UTF-8, events separated by `\n\n`. Fields: `event`, `data`, `id`, `retry`. **Not** "one network chunk = one event." EOF without a trailing blank line discards the last event.

**Stream event patterns**:
- **OpenAI Responses**: `response.created` -> `response.output_text.delta` / `response.function_call_arguments.delta` -> `response.completed` / `error`
- **OpenAI Chat Completions**: Anonymous `chat.completion.chunk`; `choices` may be empty on a usage-only final chunk (request `stream_options={"include_usage": true}`)
- **Anthropic**: `message_start` -> (`content_block_start` / `delta` / `stop`)* -> `message_delta` (stop_reason + usage, including `thinking_tokens`) -> `message_stop`. `ping` may appear anywhere; SDK iterators have historically dropped ping -- install an idle watchdog

**Anthropic httpx2 gotcha**: Anthropic 1.x HTTP layer is `httpx2`. Passing `httpx.AsyncClient` as `http_client=` raises `TypeError`. Use `DefaultAsyncHttpxClient` / `DefaultAioHttpClient`, or `import httpx2 as httpx`.

---

## 3. Architecture & System Design

### 3.1 System Topology

The architecture separates into planes that must not be coupled. The control plane (your process) owns auth, retries, cancellation, schema compilation, and tool decisions. The data plane (vendor or vLLM) owns tokenizer through sampler. Persistence stores checkpoints and vectors. Telemetry is the only place streaming usage is authoritative.

```
+----------------------------------------------------------------------------------+
| CLIENTS                                                                          |
|  SSE (chat tokens)  |  REST JSON (CRUD / extract)  |  poll batch_id (no webhook)|
+----------+---------------------------------------------------------------------------+
           | TLS + Idempotency-Key + correlation-id + anthropic-version pin
           v
+----------------------------------------------------------------------------------+
| CONTROL PLANE  (your process -- asyncio, not the GPU)                            |
|                                                                                  |
|  +--------------+  +--------------+  +--------------+  +---------------------+   |
|  | Edge gateway |->| Policy       |->| Tokenizer    |->| Schema compiler     |   |
|  | auth, RPM    |  | PII redact   |  | planner      |  | Pydantic -> JSON    |   |
|  | circuit brk  |  | tool RBAC    |  | o200k vs     |  | Schema STRICT       |   |
|  | per (vendor, |  | MCP ticket   |  | count_tokens |  | additionalProperties|   |
|  |  model)      |  | verify       |  | NEVER mix    |  | false + all required|   |
|  +--------------+  +------+-------+  +------+-------+  +----------+----------+   |
|                           |                 |                     |               |
|                           v                 v                     v               |
|                    +-------------------------------------------------------------+|
|                    | Orchestrator  asyncio.TaskGroup + Semaphore(vendor)          ||
|                    | stop_reason loop | stream watchdog (ping / idle)            ||
|                    | bounded Queue(embed ingest)  !=  chat socket pool           ||
|                    +-----------------------------+-------------------------------+|
+---------------------------------------------|------------------------------------+
                                               |
                     +-------------------------+----------------------------+
                     | chat / extract SSE, REST|                            |
                     v                         v                            |
+--------------------------------------------+ +---------------------------+------+
| DATA PLANE  GENERATION                     | | DATA PLANE  EMBEDDING            |
| (provider-owned on hosted APIs)            | | single-shot encoder              |
|                                            | | NO decode, NO KV growth,         |
|  +----------+  +-----------+  +---------+  | | NO sampler                       |
|  |Tokenizer |->| Prefill   |->| Decode  |  | |                                  |
|  |+template |  | compute-  |  | memory- |  | |  +----------+ +--------+         |
|  |          |  | bound KV  |  | bound   |  | |  |Tokenizer |>|Encoder |         |
|  |          |  | write     |  | 1 tok/  |  | |  |cl100k for| |MRL trim|         |
|  |          |  | TTFT KPI  |  | step    |  | |  |3-small/  | |dims= + |         |
|  +----------+  +-----------+  | TPOT KPI|  | |  |3-large   | |L2-norm |         |
|                               +----+----+  | |  +----------+ +---+----+         |
|  Prompt cache = KV TENSOR reuse    |       | +-------------------+---------------+
|  (exact prefix; not "similar text")|       |                     |
|                                    v       |                     |
|  +---------+  +-------------------------+  |                     |
|  | Sampler |->| Parser                  |  |                     |
|  | +grammar|  | text | tool_use         |  |                     |
|  | bitmask |  | thinking | json_schema  |  |                     |
|  +---------+  +------------+------------+  |                     |
+------------------------+------------------+                     |
                         |                                         |
       +-----------------+-----------------+                       |
       | stop_reason = tool_use           |  final / extract       |
       v                                  v                        v
+----------------------------------+  +------------------------------------+
| TOOL PROXIES  (MCP / workers)    |  | PERSISTENCE LAYER                  |
| Untrusted planner never holds    |  |                                    |
| IAM. Identity from ticket,      |  |  +------------------+ +---------+  |
| not from model JSON.            |  |  | App state        | | Caches  |  |
|  +----------+  +-------------+  |  |  | Postgres:        | | KV TTL  |  |
|  | STS /    |->| Sandbox     |  |  |  |  thread, batch_id| | Redis:  |  |
|  | signed   |  | HTTP, code  |--+--+  |  custom_id BEFORE| | partial |  |
|  | scope    |  | JSON-encode |  |  |  |  user ack        | | SSE buf |  |
|  +----------+  +-------------+  |  |  +------------------+ +---------+  |
|  computer-use tools sequential  |  |  +------------------+              |
+----------------------------------+  |  | Vector index     |<-- embeddings|
                                      |  | pin model+dim+   |              |
                                      |  | metric; ACL      |              |
                                      |  +------------------+              |
                                      +------------------+-----------------+
                                                         |
+--------------------------------------------------------+---------------------+
| TELEMETRY / OBSERVABILITY SINKS                                               |
|  +--------------+  +--------------+  +--------------+  +-------------------+  |
|  | Audit (WORM) |  | Metrics      |  | Traces       |  | Usage (authority) |  |
|  | cid, tenant  |  | TTFT p50/95  |  | gateway->HTTP|  | on terminal event |  |
|  | SHA-256 of   |  | TPOT, ping   |  | ->prefill    |  | input, cache_read |  |
|  | redacted     |  | gaps, breaker|  | ->decode->   |  | cache_write, out  |  |
|  | prompt, tool |  | state, sem   |  | tool         |  | thinking_tokens   |  |
|  | names, stop_ |  | wait, queue  |  |              |  |                   |  |
|  | reason,      |  | depth        |  |              |  |                   |  |
|  | request_id   |  |              |  |              |  |                   |  |
|  +--------------+  +--------------+  +--------------+  +-------------------+  |
+-------------------------------------------------------------------------------+
```

**Planes (do not couple)**:

| Plane | Owns | Failure if coupled |
|-------|------|--------------------|
| **Control** | Auth, PII, RBAC, schema, TaskGroup, semaphores, breakers, idempotency | Provider 529 becomes your 500 with no fallback |
| **Generation data** | Prefill (TTFT) then decode (TPOT); prompt-cache KV | Embedding ingest starves SSE sockets |
| **Embedding data** | Single-shot encoder; 8192/300k/2048 caps (OpenAI) | Chat retries 429 the embed pool |
| **Tool proxies** | Side effects, MCP identity, sandbox | Model JSON as IAM -> Excessive Agency |
| **Persistence** | Checkpoints + `batch_id`; vector index with pinned dim | Restart re-creates batch = duplicate spend |
| **Telemetry** | Usage on terminal frames; hashed prompts | Finance dashboards that bill from tiktoken |

### 3.2 End-to-End Request Flow

1. **Ingress**: Client opens SSE (interactive), REST (extract), or you accept a job and persist a `batch_id` (offline). Gateway stamps `correlation_id`, checks tenant quota, consults the per-(vendor, model) breaker. OpenAI `x-ratelimit-*` / Anthropic token-bucket headers are inputs to admission.

2. **Policy**: Detect and redact PII **before tokenize**. Secrets must not sit left of a cache breakpoint. Tool RBAC attaches only this turn's tools. MCP: verify a signed ticket (audience, tenant, tool name, expiry) -- never take `tenant_id` from model-emitted JSON.

3. **Count**: OpenAI: tiktoken encoding matched to the model (`o200k_base` for gpt-5*, `cl100k_base` for embedding-3-*). Claude: `POST /v1/messages/count_tokens` with the same model ID. Reserve `max_output + thinking + tool-schema overhead` (Sonnet 5 `auto` tools add 354 system tokens; `any`/`tool` add 474).

4. **Compile**: Pydantic -> JSON Schema subset: every object `additionalProperties: false`, every property in `required`, optionality via `["string","null"]`. Illegal schema -> **400**, not best-effort JSON. Anthropic strict tools compile `input_schema` to a grammar cached up to 24h.

5. **Dispatch**: One process-wide `AsyncOpenAI` and one `AsyncAnthropic`. Chat sockets and embed workers use separate limits / semaphores.

6. **Prefill or encode**: Generation: compute-bound prefill writes KV; KPI = TTFT. Cache hit skips recomputing that prefix. Embedding: one forward, no sampler.

7. **Decode + constrain**: Memory-bound, one token/step, KPI = TPOT. Grammar bitmask sets illegal tokens to -infinity before sample.

8. **Stream parse**: Persist `response_id` / `message.id` on the start event; append deltas to Redis every N tokens / 200ms.

9. **Tool proxy** (only if `stop_reason=tool_use`): Validate args against schema, check ticket + RBAC, execute, JSON-encode results. `max_tokens` mid-`tool_use` yields invalid JSON -- do not execute.

10. **Cancel / hang**: Cancel the Task that owns the stream context manager. Persist partial text. Do not replay after deltas. `asyncio.timeout` and inter-event watchdog for liveness.

11. **Persist / batch**: OpenAI Batch / Anthropic Message Batches: no GA completion webhook. Persist id before ack, poll. Recreate-on-restart = duplicate spend.

12. **Emit + audit**: Terminal SSE frame is the invoice (`usage`). Log tenant, model, `request_id`, token breakdown, cache hit, `stop_reason`, tool names, SHA-256 of redacted prompt -- never raw PII.

### 3.3 State Machines

**SDK retry**:
```
                    Retry-After in (0, 60s]              attempts exhausted
  +----------+  HTTP 408/409/429/5xx/529   +---------+  -----------------> FAIL
  |  SEND    | --------------------------> |  WAIT   |
  +----+-----+  400/401/403/404/413        | jitter  |
       |        spend-cap 429 (no RA)      | cap 8s  |
       |        -------------------------> FAIL      |
       | success                           +----+----+
       v                                        |
     DONE <-------------------------------------+  retry SEND
```

**Stream lifecycle**:
```
  OPEN HTTP --> START_EVENT (persist response_id / message.id)
       |
       +-- delta*  -- persist buffer every N toks / 200 ms
       +-- ping    -- reset idle watchdog (Anthropic; SDK may drop)
       +-- error / response.failed  -- TERMINAL; do not retry this stream
       +-- client cancel  -- close body; expose partial; NO replay
       +-- COMPLETED / message_stop  -- usage authoritative; close
```

**Anthropic tool loop (stop_reason)**:
```
  message --> end_turn / stop_sequence --> DONE
           --> max_tokens --> maybe repair JSON; do NOT exec partial tool
           --> refusal --> FAIL CLOSED
           --> pause_turn --> resend assistant content (server-tool cap)
           --> tool_use --> VALIDATE --> TICKET/RBAC --> EXECUTE
                              |                |
                              | schema/deny    | all tool_result FIRST
                              v                v
                            400/403         next Messages call
                                            (echo thinking verbatim)
```

---

## 4. Key Algorithms & Mechanics

### 4.1 Circuit Breaker State Machine

```
                      +------------------------------------+
                      |                                    |
                      v                                    |
                 +---------+     failure_rate >        +--------+
          ------>| CLOSED  |---- threshold ---------->|  OPEN  |
                 | (normal)|     (e.g., 50% in 60s)    |(reject)|
                 +---------+                           +---+----+
                      ^                                    |
                      |                              cooldown expires
                 probe succeeds                            |
                      |                                    v
                 +----+------+                      +----------+
                 |           |<---------------------|HALF-OPEN |
                 |           |                      | (probe)  |
                 +-----------+    probe fails ------>+----------+
                                 (back to OPEN)
```

**States**:
- **CLOSED**: Track success/failure over a sliding window. All requests pass through.
- **OPEN**: All requests fail fast (no external call) or route to fallback. Timer starts.
- **HALF-OPEN**: After cooldown, send one probe request. Success -> CLOSED; failure -> OPEN.

**Critical rules for LLM APIs**:
- One breaker per (provider, model), never one global breaker that kills failover
- Do not open solely on 429 unless 429s persist with no Retry-After (spend cap)
- Half-open: probe with cheap Haiku / GPT-4.1-nano
- Bulkhead: `Semaphore` per provider so Anthropic overload cannot exhaust the OpenAI pool

**Why this matters**: Without circuit breakers, retry logic on 429s converts rate limits into cascading retry storms. At 200 concurrent users, a single high-traffic hour can generate exponential retry amplification.

### 4.2 Prompt Cache Mechanics

Prompt cache stores **KV tensors**, not tokens. An exact prefix match lets the provider skip recomputing the prefill for that portion.

**Anthropic cache**: Explicit `cache_control` breakpoints in `tools` -> `system` -> `messages` (matched in that order). TTLs: 5-minute write at 1.25x input price, 1-hour write at 2x input price. Read at 0.1x input (Fable 5.1 read: 0.025x). Hits refresh TTL at the read price. Minimum cacheable prefix varies by model class (512-4,096 tokens). Cached reads do not count toward ITPM (except Haiku 3.5); writes do. Longer TTL blocks must appear before shorter ones.

**OpenAI cache**: Automatic exact-prefix KV, org-scoped. GPT-5.6+: 1,024 visible tokens minimum, TTL option `30m` only. Earlier: typically 5-10 min idle, always within 1 hour. Up to 80% latency reduction for prompts > 10,000 tokens.

**Cache optimization pattern**: Cache stable prefixes (tools, system, corpus) at the **left**; tenant data at the **right**. `prompt_cache_key` isolates namespaces, not tenants. Do not put tenant docs in a shared prefix.

**Break-even**: 5-min Anthropic write breaks even after 1 subsequent read; 1-hour after 2 reads.

### 4.3 Back-Pressure Design

1. Admit iff breaker is closed/half-open **and** local `Semaphore` has room **and** token bucket (RPM/ITPM/OTPM) has room.
2. 429 + `Retry-After` -> sleep on that provider; do not steal the other vendor's pool (bulkhead).
3. Bounded `Queue` for embed ingest; drop-oldest or 429 the producer -- never `ThreadPoolExecutor.submit` without a cap.
4. Shed: Batch/offline first; then degrade model; then deterministic JSON. Do not infinite-retry 429.
5. Agent fleets: budget N_rounds * (TTFT + T_out / TPOT). Cap rounds. Parallel read tools cut rounds; they multiply downstream QPS.

**Sizing formula**: To sustain R requests/minute with average response time T seconds:
```
concurrent_connections = R * T / 60
httpx.Limits(max_connections = concurrent_connections * 1.2)  # 20% headroom
asyncio.Semaphore(concurrent_connections)
```

**Worked example**: 30,000 RPM with 5s average response time:
```
concurrent = 30000 * 5 / 60 = 2,500
```
Default httpx pool of 100 connections is 25x too small. Set `max_connections=3000`.

---

## 5. Token Economics & Cost Analysis

### 5.1 Cost Formulas

**Base cost per API call**:
```
cost = (input_tokens / 1M) * input_price + (output_tokens / 1M) * output_price
```

**With prompt caching (Anthropic)**:
```
cost = (uncached_tokens / 1M) * input_price * cache_write_multiplier
     + (cached_tokens   / 1M) * input_price * cache_read_multiplier
     + (output_tokens   / 1M) * output_price

Where:
  5-min TTL:  cache_write_multiplier = 1.25,  cache_read_multiplier = 0.10
  1-hour TTL: cache_write_multiplier = 2.00,  cache_read_multiplier = 0.10
  (Fable 5.1 cache read: 0.025x; Opus 5.5 cache read: 0.05x)
```

**Full formula with all token types**:
```
C = n * (T_miss * P_miss + T_hit * P_hit + T_write * P_write + T_out * P_out) / 10^6
```
Where T_out **includes thinking tokens**. Cached Anthropic reads do not count toward ITPM (except Haiku 3.5); writes do.

**Reasoning token trap (OpenAI o3)**: o3 generates 8,000-20,000 internal reasoning tokens per query, all billed at the output rate ($8/MTok). A single o3 query can cost 10-15x more than GPT-4o.

**Tokenizer cost trap (Anthropic)**: Claude 4.7+ uses a newer tokenizer producing ~30% more tokens for the same text. Upgrading from 4.6 to 4.7+ increases effective cost by ~30% at identical per-token pricing.

### 5.2 Full Pricing Reference (September 2026)

**Anthropic Claude**:

| Model | Input/MTok | Cache Read | Output/MTok | Context | Max Output |
|-------|-----------|------------|-------------|---------|------------|
| Claude Fable 5.1 | $10.00 | 0.025x ($0.25) | $50.00 | 1M | 128k |
| Claude Opus 5.5 | $4.00 | ~0.05x ($0.20) | $20.00 | 1M | 128k |
| Claude Opus 5 / 4.8 | $5.00 | 0.10x ($0.50) | $25.00 | 1M | 128k |
| Claude Sonnet 5 | $2.00 | 0.10x ($0.20) | $10.00 | 1M | 128k |
| Claude Sonnet 4.6 / 4.5 | $3.00 | 0.10x ($0.30) | $15.00 | 1M | 128k |
| Claude Haiku 4.5 | $1.00 | 0.10x ($0.10) | $5.00 | 200k | 64k |

Cache write 5m: 1.25x. Cache write 1h: 2x. Fast mode Opus 5/4.8: $10/$50 (first-party only, not with Batch). `inference_geo: "us"` on 4.6+: 1.1x. 1M context on 4.6+ is standard price.

**OpenAI**:

| Model | Input/MTok | Cached/MTok | Output/MTok | Context | Max Output |
|-------|-----------|-------------|-------------|---------|------------|
| GPT-6 Astra | $10.00 | $1.00 | $50.00 | -- | -- |
| GPT-6 Sol | $2.00 | $0.20 | $10.00 | -- | -- |
| GPT-6 Luna | $0.10 | $0.01 | $0.50 | -- | -- |
| GPT-5.5 | $5.00 | $0.50 | $30.00 | -- | -- |
| GPT-5.4 | $2.50 | $0.25 | $15.00 | 1.05M | 128k |
| GPT-5.4 mini | $0.75 | $0.075 | $4.50 | -- | -- |
| GPT-4.1 | $2.00 | $0.50 | $8.00 | 1.047M | 32,768 |
| GPT-4o | $2.50 | $0.25 | $10.00 | 128k | 16k |
| GPT-4o mini | $0.15 | -- | $0.60 | 128k | 16k |
| o3 | $2.00 | -- | $8.00 | -- | -- |
| o3-pro | $20.00 | -- | $80.00 | -- | -- |

GPT-5.4 prompts > 272K: 2x input and 1.5x output for the full session. Models on/after 2026-03-05: regional +10%. GPT-5.6+ cache write: 1.25x. Web search tool: $10/1k calls + content tokens.

### 5.3 Cost per 1K Runs -- Reference Workload

**Reference workload W**: 2,000 input + 800 output tokens; 80% cache-hit on the 2,000 input (1,600 cache-read, 400 uncached); cache already warm (no write); 1,000 executions.

| Model | $/exec | $/1k runs | Notes |
|-------|--------|-----------|-------|
| Claude Sonnet 5 (cached) | $0.00912 | **$9.12** | 400x$2 + 1600x$0.20 + 800x$10 / MTok |
| Claude Sonnet 5 (no cache) | $0.01200 | **$12.00** | 2000x$2 + 800x$10 |
| Claude Sonnet 5 Batch (no cache) | $0.00600 | **$6.00** | 50% discount |
| Claude Haiku 4.5 | $0.00456 | **$4.56** | |
| Claude Opus 5 | $0.02280 | **$22.80** | $5 / $0.50 / $25 |
| Claude Opus 5.5 | $0.01792 | **$17.92** | $4 / $0.20 / $20 |
| GPT-5.4 | $0.01340 | **$13.40** | $2.50 / $0.25 / $15 |
| GPT-4.1 | $0.00800 | **$8.00** | cached $0.50 not $0.20 |
| GPT-6 Sol | $0.00880 | **$8.80** | $2 / $0.20 / $10 |
| GPT-4o mini | $0.00045 | **$0.45** | (1k in / 500 out chatbot scenario) |
| Embed 3-small (2k in only) | $0.00004 | **$0.04** | $0.02/1M |
| Embed 3-large (2k) | $0.00026 | **$0.26** | $0.13/1M |

**Chat:embed cost ratio**: Sonnet 5 uncached $0.012 vs 3-small $0.00004 ~ **300x**. Indexing 1M chunks x 512 tokens = 512M tokens -> 3-small **$10.24** standard / **$5.12** batch.

**Monthly projection example**: 100K RAG calls/month (4k input, 1k output) on Sonnet 5 = $1.80/month without caching. With 80% prompt cache hit rate on 3K of the 4K input tokens: ~$0.72/month. Caching delivers ~60% cost reduction.

### 5.4 Latency SLA Targets

**Measured benchmarks (September 2026)**:

| Model | TTFT p50 | Tokens/sec | Notes |
|-------|----------|------------|-------|
| Groq (any model) | <200ms | 1,200-2,000+ | Hardware-optimized inference |
| Gemini 2.5 Flash | ~350ms | ~213 | Fastest major-provider model |
| Claude Haiku 4.5 | <600ms | ~180 | Best Anthropic latency |
| Claude Opus 4.7 | ~850ms | ~78 | |
| GPT-5.5 standard | ~1,100ms | ~92 | |
| GPT-5.4 Fast mode | -- | >50 tok/s | 99% of 5-min windows (Enterprise) |
| GPT-4.1 Fast mode | -- | >80 tok/s | Enterprise |
| GPT-5.6 Luna | -- | >100 tok/s | Enterprise |

**Reasoning mode latency** (not suitable for interactive UX):

| Config | TTFT p50 |
|--------|----------|
| Claude Opus 4.7 extended thinking | ~28s |
| Gemini 3 Pro Deep Think | ~52s |
| GPT-5.5 Pro (high reasoning) | ~67s |

**UX thresholds** (Jakob Nielsen's guidelines):

| Threshold | Perception | Target Tier |
|-----------|-----------|-------------|
| <200ms | Instant | Autocomplete, inline suggestions |
| <500ms | Responsive | Chat TTFT for premium UX |
| <1,000ms | Acceptable | Standard chat, tool calls |
| >1,000ms | Flow-breaking | Requires streaming + progress indicator |

**Recommended SLO targets** (your policy, not vendor guarantees):

| Metric | Target | Mitigation |
|--------|--------|------------|
| **p50 TTFT** | < 800ms | Stream by default; cache prefix >= 10k toks (up to 80% latency cut); separate embed pool |
| **p95 TTFT** | < 2s | Sticky warm cache; aiohttp transport under concurrency; Haiku/4.1 for extract |
| **p99 TTFT** | Fail closed on idle gap | Inter-event watchdog; `asyncio.timeout` on stream; never non-stream 600s x 3 = 30 min wall clock |
| **p50 TPOT** | Match Fast tok/s if paying (50/80/100) | Fast mode Enterprise; size UX for Standard ~25 tok/s |
| **p95 time-to-final** | 800 out / 50 tok/s = 16s | Cap `max_output_tokens`; adaptive thinking off for extract |

**Agentic pipeline compounding**: A 4-step agent pipeline with 600ms TTFT/step adds 2.4s in first-token latency alone. At 2,400ms/step, 9.6s before any useful output. TTFT is the primary model selection criterion for agent workloads.

### 5.5 Throughput Limits and Rate Limits

**OpenAI GPT-5.4 per-tier**:

| Tier | RPM | TPM | Batch Queue |
|------|-----|-----|-------------|
| 1 | 500 | 500,000 | 1,500,000 |
| 2 | 5,000 | 1,000,000 | 3,000,000 |
| 3 | 5,000 | 2,000,000 | 100,000,000 |
| 4 | 10,000 | 4,000,000 | 200,000,000 |
| 5 | 15,000 | 40,000,000 | 15,000,000,000 |

Embeddings 3-small: Tier1 3,000 RPM / 1M TPM; Tier5 10,000 RPM / 10M TPM. Batch: 50,000 requests/batch, 200 MB, 2,000 batches/hour, 24h window, does not consume sync TPM. Spend: Tier5 $1,000 paid / $200,000/mo cap.

**Anthropic (same for Sonnet 5, Opus 5, Haiku 4.5)**:

| Tier | RPM | ITPM | OTPM | Spend Cap |
|------|-----|------|------|-----------|
| Start | 1,000 | 2,000,000 | 400,000 | $500/mo |
| Build | 5,000 | 5,000,000 | 1,000,000 | $1,000/mo |
| Scale | 10,000 | 10,000,000 | 2,000,000 | $200,000/mo |

Fable 5.x tighter (Start 1,000 RPM / 500k ITPM / 100k OTPM). Message Batches: Start 1,000 RPM / 200k queued / 100k per batch; Scale 4,000 / 500k / 100k. Spend cap -> 429 `enforced_spend_limit_reached` without `retry-after`.

**Observed availability (2026)**: Anthropic had 114 incidents in a 90-day window in early 2026. OpenAI's 99.76% uptime = ~16 hours downtime/year. On September 4, 2026, OpenAI, Anthropic, Google, and xAI all degraded simultaneously due to shared cloud infrastructure. Real multi-provider availability: ~99.6-99.8% effective uptime.

### 5.6 Non-Functional Requirements

| NFR | Working Target | Tension |
|-----|---------------|---------|
| **Availability** | 99.9% gateway (control plane). Multi-vendor fallback for 503/529 | Output-distribution drift; schema mapping cost |
| **RPO** | App state / `batch_id` / partial SSE buffer: 0 for irreversible tools. Prompt-cache KV: minutes (5m/30m/1h), best-effort | Treating KV as RPO=0 over-provisions nothing you control |
| **RTO** | Interactive: fail over < 1s to secondary model (breaker already open). Batch: resume poll from durable id | Fast failover vs identical tokens (temperature > 0) |
| **Consistency** | Tool side effects: exactly-once via idempotency keys. Model text: at-least-once retry changes tokens | Cannot have bit-identical retry on T>0 |
| **Compliance** | Regional +10% / Anthropic 1.1x US geo; ZDR excludes batches, files, managed agents | Residency vs latency vs price |
| **Cost vs latency** | Haiku $4.56/1k vs Opus 5 $22.80/1k vs Sonnet cached $9.12/1k | Paying Fast + Opus for extract |
| **Cache vs tenancy** | Left-prefix tools/system; `prompt_cache_key` isolates namespaces, not tenants | Hit rate vs leak |

**ZDR details**: OpenAI: abuse logs default 30 days. Stateful endpoints (Assistants threads, vector stores, files, fine-tuning, evals, batches) remain ZDR-ineligible. Anthropic: no prompts/responses at rest after response except law/safety; flagged content up to 2 years; Messages + Token Counting in-scope; excludes `/v1/files`, Message Batches, code execution, managed agents.

---

## 6. Production Patterns & Code

### 6.1 Complete LLM Gateway with Circuit Breaker, Retry, PII, MCP, and Fallback

This is the comprehensive production reference implementation. It covers retry with jitter, circuit breaker per (provider, model), PII redaction, MCP ticket verification, strict schema compilation, streaming with idle watchdog, fallback chain to deterministic JSON, and per-vendor semaphore bulkheads.

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

# Anthropic 1.x HTTP layer is httpx2. Passing httpx.AsyncClient as http_client=
# raises TypeError. Use DefaultAsyncHttpxClient / DefaultAioHttpClient.

INITIAL_RETRY_DELAY = 0.5   # openai/_constants.py
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2  # 3 attempts; disable if this wrapper owns retry
CONNECT_TIMEOUT_S = 5.0
STREAM_IDLE_TIMEOUT_S = 45.0  # not 600s; 600*(2+1)=30 min non-stream footgun

# W workload prices (USD / 1M toks) -- 2026-09-23.
SONNET5_IN, SONNET5_CACHE, SONNET5_OUT = 2.00, 0.20, 10.00
GPT54_IN, GPT54_CACHE, GPT54_OUT = 2.50, 0.25, 15.00
EMBED_3_SMALL_PER_M = 0.02


# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# PII Redaction (detect -> redact -> audit, before tokenize/embed)
# ---------------------------------------------------------------------------

_PII = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("acct", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    """Detect -> redact before tokenize/embed. Audit placeholders, not plaintext."""
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


# ---------------------------------------------------------------------------
# Error Hierarchy
# ---------------------------------------------------------------------------

class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


# ---------------------------------------------------------------------------
# Circuit Breaker (per provider+model, not global)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Retry with Jitter
# ---------------------------------------------------------------------------

T = TypeVar("T")


async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """Full jitter. Honor Retry-After only if 0 < value <= 60s (SDK rule)."""
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


# ---------------------------------------------------------------------------
# Strict JSON Schema (Pydantic -> OpenAI/Anthropic strict subset)
# ---------------------------------------------------------------------------

class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    amount_cents: int
    injection_suspected: bool = False


class InvoiceExtract(BaseModel):
    """Strict-mode compatible: extra=forbid => additionalProperties: false.
    Every field required (optionality would be T | None, still in required)."""
    model_config = ConfigDict(extra="forbid")
    vendor: str
    invoice_id: str
    currency: str = Field(min_length=3, max_length=3)
    total_cents: int
    lines: list[InvoiceLine]
    injection_suspected: bool


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Walk schema to enforce additionalProperties: false + all properties required."""
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
    """Schema-valid deterministic fallback when all providers fail."""
    cents = [int(x.replace(",", "")) for x in re.findall(r"\$([0-9,]+)", text)]
    return InvoiceExtract(
        vendor="UNKNOWN",
        invoice_id="DEGRADED",
        currency="USD",
        total_cents=sum(cents) * 100 if cents else 0,
        lines=[],
        injection_suspected=False,
    )


# ---------------------------------------------------------------------------
# Token Counting
# ---------------------------------------------------------------------------

def count_openai_tokens(text: str, model: str) -> int:
    """tiktoken for OpenAI models ONLY.
    gpt-5.4 / gpt-4o* / gpt-4.1* -> o200k_base (199,998 + 2 special).
    text-embedding-3-* -> cl100k_base. cl100k on GPT-5-class mis-estimates.
    WARNING: do NOT use tiktoken for Claude -- undercounts ~15-20% typical,
    more on code/non-English. Claude 4.7+ tokenizer is ~+30% vs pre-Opus-4.7.
    Use POST /v1/messages/count_tokens with the same model ID you will call.
    Pre-flight != invoice; bill from response.usage.
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
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

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


# ---------------------------------------------------------------------------
# Embedding Utilities
# ---------------------------------------------------------------------------

def l2_normalize(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0.0:
        raise PermanentError("zero embedding")
    return [x / n for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """OpenAI unit-length => cosine == inner product. Re-normalize after slice."""
    if len(a) != len(b):
        raise PermanentError(f"dim mismatch {len(a)} vs {len(b)}")
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


async def embed_batch(
    texts: list[str],
    *,
    model: str = "text-embedding-3-small",
    dimensions: int = 1536,
) -> list[list[float]]:
    """OpenAI: <=2048 strings, <=300k toks summed, <=8192/input (400 on overflow).
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


# ---------------------------------------------------------------------------
# Streaming (OpenAI Responses + Anthropic Messages)
# ---------------------------------------------------------------------------

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
            model=model, input=prompt, max_output_tokens=max_output_tokens,
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
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

    buf = StreamBuffer()
    client = AsyncAnthropic(
        http_client=DefaultAsyncHttpxClient(), max_retries=0,
        timeout=idle + CONNECT_TIMEOUT_S,
    )
    last = time.monotonic()
    try:
        async with client.messages.stream(
            model=model, max_tokens=max_tokens,
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


# ---------------------------------------------------------------------------
# Structured Output Parse (OpenAI + Anthropic)
# ---------------------------------------------------------------------------

async def openai_responses_parse(prompt: str, *, model: str = "gpt-5.4") -> InvoiceExtract:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0)
    try:
        resp = await client.responses.parse(
            model=model, input=prompt,
            text_format=InvoiceExtract, max_output_tokens=2048,
        )
        parsed = resp.output_parsed
        if parsed is None:
            raise PermanentError("empty output_parsed")
        return parsed
    finally:
        await client.close()


async def anthropic_messages_parse(prompt: str, *, model: str = "claude-sonnet-5") -> InvoiceExtract:
    from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

    client = AsyncAnthropic(http_client=DefaultAsyncHttpxClient(), max_retries=0)
    try:
        resp = await client.messages.parse(
            model=model, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
            output_format=InvoiceExtract,
        )
        parsed = resp.parsed_output if hasattr(resp, "parsed_output") else resp.output_parsed
        if parsed is None:
            raise PermanentError("empty anthropic parsed output")
        return parsed
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# MCP Ticket (audience + expiry + HMAC-equivalent digest)
# ---------------------------------------------------------------------------

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
        expect = hashlib.sha256(
            f"{self.tenant}|{self.tool}|{self.exp}|{secret}".encode()
        ).hexdigest()
        if expect != self.sig:
            raise PermanentError("mcp_ticket_bad_sig")


def issue_ticket(tenant: str, tool: str, secret: str, ttl: float = 30.0) -> McpTicket:
    exp = time.time() + ttl
    sig = hashlib.sha256(f"{tenant}|{tool}|{exp}|{secret}".encode()).hexdigest()
    return McpTicket(tenant, tool, exp, sig)


# ---------------------------------------------------------------------------
# Fallback Chain (primary -> secondary -> deterministic schema-valid JSON)
# ---------------------------------------------------------------------------

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
            # Schema 400 would fail everywhere -- do NOT failover
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


# ---------------------------------------------------------------------------
# Provider Gateway (TaskGroup fan-out + per-vendor semaphore bulkhead)
# ---------------------------------------------------------------------------

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
        await self.embed_q.put(redacted)  # blocks at maxsize -- ingest back-pressure

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
            openai_responses_parse, anthropic_messages_parse,
            self.breaker_oa, "gpt-5.4", "claude-sonnet-5",
        )

        async def _one(p: str) -> InvoiceExtract:
            redacted, audit = redact_pii(p)
            log.info("pii_redactions count=%s sha256=%s", len(audit),
                     hashlib.sha256(redacted.encode()).hexdigest()[:16])
            async with self.sem_oa:
                return await chain.extract(redacted, log)

        async def _isolated(p: str) -> InvoiceExtract:
            # Independent invoices: do not let one PermanentError cancel siblings
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
                        lambda: anthropic_messages_stream(redacted), log=log,
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
```

### 6.2 High-Throughput Batch Processor with Backpressure

For high-volume workloads (evaluation, data extraction, embedding) that need bounded concurrency and worker-pool semantics.

```python
"""
Async batch processor for high-throughput LLM workloads.
Uses worker pool pattern with bounded queue for backpressure.

Requirements: pip install anthropic structlog uvloop
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog
import anthropic

logger = structlog.get_logger()


@dataclass
class WorkItem:
    id: str
    messages: list[dict[str, str]]
    system: str = ""


@dataclass
class WorkResult:
    id: str
    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    error: str | None = None


class BatchLLMProcessor:
    """Process large batches with bounded concurrency and backpressure."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20250514",
        max_workers: int = 50,
        max_queue_size: int = 200,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.max_workers = max_workers
        self.max_tokens = max_tokens
        self._queue: asyncio.Queue[WorkItem | None] = asyncio.Queue(maxsize=max_queue_size)
        self._results: list[WorkResult] = []
        self._semaphore = asyncio.Semaphore(max_workers)
        self._client = anthropic.AsyncAnthropic(
            max_retries=3, timeout=anthropic.DEFAULT_TIMEOUT,
        )

    async def close(self) -> None:
        await self._client.close()

    async def process_batch(self, items: list[WorkItem]) -> list[WorkResult]:
        self._results = []
        start = time.monotonic()

        workers = [asyncio.create_task(self._worker(i)) for i in range(self.max_workers)]

        # Enqueue items (backpressure: blocks when queue is full)
        for item in items:
            await self._queue.put(item)
        # Poison pills to stop workers
        for _ in range(self.max_workers):
            await self._queue.put(None)

        await asyncio.gather(*workers)

        elapsed = time.monotonic() - start
        errors = sum(1 for r in self._results if r.error)
        logger.info(
            "batch_complete", items=len(items), errors=errors,
            elapsed_s=round(elapsed, 2),
            throughput_rps=round(len(items) / elapsed, 1),
        )
        return self._results

    async def _worker(self, worker_id: int) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            async with self._semaphore:
                result = await self._process_item(item, worker_id)
                self._results.append(result)

    async def _process_item(self, item: WorkItem, worker_id: int) -> WorkResult:
        start = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=item.system or "You are a helpful assistant.",
                messages=item.messages,
            )
            latency = round((time.monotonic() - start) * 1000, 1)
            return WorkResult(
                id=item.id, content=response.content[0].text,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.monotonic() - start) * 1000, 1)
            return WorkResult(
                id=item.id, content="", tokens_in=0, tokens_out=0,
                latency_ms=latency, error=str(e),
            )
```

### 6.3 Structured Output with Self-Healing Parser

For extracting structured data from models that lack native strict mode, or as a fallback layer.

```python
"""Self-healing JSON parser for LLM output recovery."""
import json
import re
from typing import TypeVar
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


def heal_json(raw: str) -> str:
    """Fix common JSON malformations from LLM output."""
    text = raw.strip()

    # Extract JSON from markdown code blocks
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()

    # Python-style booleans and None
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Single quotes to double quotes (naive but effective for simple cases)
    if text.startswith("{") and '"' not in text and "'" in text:
        text = text.replace("'", '"')

    # Close unclosed braces/brackets (LIFO)
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text += "]" * max(0, open_brackets)
    text += "}" * max(0, open_braces)

    # Trailing comma before closing brace/bracket
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


async def extract_structured(
    client,  # AsyncAnthropic or AsyncOpenAI
    model: str,
    messages: list[dict[str, str]],
    output_schema: type[T],
    system: str = "",
    max_retries: int = 2,
) -> T:
    """Multi-layer recovery: native parse -> pydantic validate -> heal -> retry."""
    json_schema = output_schema.model_json_schema()

    for attempt in range(max_retries + 1):
        retry_system = system
        if attempt > 0:
            retry_system += (
                f"\n\nIMPORTANT: Return ONLY valid JSON matching this exact schema. "
                f"No markdown, no explanation.\nSchema: {json.dumps(json_schema)}"
            )

        response = await client.messages.create(
            model=model, max_tokens=4096,
            system=retry_system or "Extract the requested information as JSON.",
            messages=messages,
        )
        raw_text = response.content[0].text

        # Attempt 1: Direct parse
        try:
            data = json.loads(raw_text)
            return output_schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

        # Attempt 2: Self-healing parse
        try:
            healed = heal_json(raw_text)
            data = json.loads(healed)
            return output_schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            pass

    raise ValueError(f"Failed to extract {output_schema.__name__} after {max_retries + 1} attempts")
```

---

## 7. Failure Modes & Mitigations

### 7.1 HTTP Error Taxonomy

| HTTP | OpenAI | Anthropic | Retry? |
|------|--------|-----------|--------|
| 400 | Invalid schema, missing `json` in JSON-mode, strict-schema fail | `invalid_request_error` (thinking config, consecutive same-role, mutated thinking, spend self-limit) | **No** |
| 401/403 | Bad/missing key | `authentication_error` / `permission_error` | **No** (rotate key) |
| 404 | Unknown model | `not_found_error` | **No** |
| 408 | Timeout | Timeout | **Yes** (SDK) |
| 409 | Conflict / lock | Conflict | **Yes** (SDK) |
| 413 | -- | `request_too_large` | **No** |
| 429 | RPM/TPM or `slow_down` | `rate_limit_error`; spend-cap 429 has no Retry-After | **Yes** if Retry-After; **No** for monthly cap |
| 500 | `api_error` | `api_error` | **Yes** |
| 503 | `server_is_overloaded` | -- | **Yes** |
| 529 | -- | `overloaded_error` | **Yes** |

### 7.2 Failure Classification

| Category | Examples | Strategy |
|----------|----------|----------|
| **Transient** | 408, 409, 429 with Retry-After, 500, 503, 529, TLS reset | Full jitter backoff; cap 8s unless Retry-After; retry idempotent reads only |
| **Permanent** | 400 schema, 401/403, 404, 413, spend-cap 429, `refusal`, `budget_tokens` on adaptive models | Fail the turn; do not failover schema errors (will fail everywhere) |
| **Poison pill** | Same payload crashes parser every time; truncated `partial_json` executed as a tool; recursive tool storm; infinite loop output | Hash + N crashes -> DLQ; never auto-replay; monitor `finish_reason=="length"` rate and circuit-break at >5% |
| **Semantic** | Schema-valid unauthorized action; injection in `tool_result` | RBAC + classifier; not a retry |
| **Partial stream** | Cancel, mid-stream `error`, missing `message_stop` | Persist partial; do not failover mid-utterance |
| **Silent** | Context truncation, embedding drift, cache TTL regression | Pre-flight token count; canary queries with MRR tracking (alert on 7% week-over-week drop); monitor cache hit rate |

### 7.3 Idempotency Keys

LLM generation is not naturally idempotent (T>0, streaming partials). Apply Stripe-style keys to your side-effecting POSTs (charge, ticket, batch submit), not to token streams.

- **Format**: `sha256(tenant|thread_id|tool_name|canonical_json(args)|turn_index)`
- **Retention**: Store status+body of first execution including 500s; retain >= 24h
- **Batch**: Persist `custom_id` + batch id **before** user ack. Stainless `_idempotency_header` does not make the model return the same tokens.
- **Redis shortcut**: Hash of `(input_content, model_id, temperature, seed, tool_definitions)`. Store completed request hashes with TTL matching your deduplication window.

### 7.4 Failover Map

| Condition | Action |
|-----------|--------|
| 429 with Retry-After | Sleep, stay primary |
| Spend cap 429 (no Retry-After) | Do not retry; page finance; failover only if other vendor is in the UX contract |
| 529/503 | Breaker open; failover Sonnet 5 <-> GPT-5.4 / GPT-4.1 |
| 400 schema | Never failover (will fail everywhere) |
| Partial stream | Finish or abort, do not switch vendors mid-utterance |

Keep a provider-agnostic IR (Pydantic) and compile: OpenAI flat tools <-> Anthropic `input_schema`; Responses `text.format` <-> Anthropic `output_config.format`.

---

## 8. Security & Governance

### 8.1 Zero-Trust MCP Architecture

MCP servers are tool proxies, not "the model's plugins." The Python client must:

1. **Treat the model as an untrusted planner**. It may emit `tools/call` JSON. That JSON is a request, not a credential.
2. **Issue short-lived, audience-bound tickets** (tenant, tool name, resource ids, expiry, signature). The MCP server verifies the ticket before I/O. The LLM never sees the raw secret.
3. **Bind identity from the verified gateway token / RunContext**, never from model-filled `tenant_id` arguments.
4. **Network**: Private egress; MCP tools must not reach instance metadata (`169.254.169.254`). Allowlists by method + resource, not hostname.
5. **Session memory**: Lives in your checkpointer, not the MCP session.

```
+---------------------------------------------------------------------+
|                         MCP Security Boundary                       |
|                                                                     |
|  +------------+     +---------------+     +----------------------+  |
|  | MCP Client |---->| Auth Gateway  |---->| Tool Registry        |  |
|  | (Agent)    |     | - mTLS        |     | - Schema validation  |  |
|  |            |     | - JWT verify  |     | - Input sanitization |  |
|  |            |     | - Rate limit  |     | - Scope enforcement  |  |
|  +------------+     +-------+-------+     +----------+-----------+  |
|                             |                        |              |
|                    +--------v--------+      +--------v----------+   |
|                    | Policy Engine   |      | Audit Logger      |   |
|                    | - OPA/Cedar     |      | - Every tool call |   |
|                    | - Per-tool RBAC |      | - Input/output    |   |
|                    | - Context-aware |      | - Decision reason |   |
|                    +-----------------+      +-------------------+   |
+---------------------------------------------------------------------+
```

### 8.2 Tool-Level RBAC (Least Privilege Per Turn)

Do not attach `send_email` / `create_ticket` unless the user asked. Extra tools also cost 354-474 system tokens on Sonnet 5.

```
Role: "financial_analyst_agent"
Permissions:
  - tool: "query_database"
    actions: ["SELECT"]
    tables: ["transactions", "accounts"]
    row_filter: "org_id = {caller.org_id}"
    max_rows: 10000
  - tool: "generate_report"
    actions: ["create"]
    output_formats: ["pdf", "csv"]
  DENY:
  - tool: "execute_sql"          # No raw SQL
  - tool: "file_system_write"    # No disk writes
  - tool: "send_email"           # No external comms
```

Parallel writes: `disable_parallel_tool_use` / `parallel_tool_calls=false`. Irreversible tools: HITL. Computer-use: sequential.

### 8.3 PII Pipeline: Detect -> Redact -> Audit

```
+-----------+    +-----------------+    +--------------+    +----------+
| Raw Input |--->| Stage 1: Regex  |--->| Stage 2: NER |--->| Stage 3: |
|           |    | - SSN patterns  |    | - Presidio   |    | Synthetic|
|           |    | - CC numbers    |    | - spaCy NER  |    | Token    |
|           |    | - API keys      |    | - Names,     |    | Replace  |
|           |    +---------+-------+    | addresses    |    +----+-----+
|           |              |            +------+-------+         |
|           |              v                   v                 v
|           |    +------------------------------------------------------+
|           |    | Redaction Map: {token_id: original_value, ...}       |
|           |    | Stored in encrypted KV store, TTL = request lifetime |
|           |    +------------------------------------------------------+
|           |                                                    |
|           |    +-----------------+    +--------------+         |
|           |    | LLM Response    |--->| Stage 4:     |<--------+
|           |    | (with synthetic |    | Restore      |
|           |    |  tokens)        |    | originals    |
|           |    +-----------------+    +------+-------+
|           |                                  |
|           |                          +-------v----------+
|           |                          | Stage 5: Output  |
|           |                          | PII Scan         |
|           |                          | (catch leakage)  |
+-----------+                          +------------------+
```

**Key rules**:
- Detect at the control-plane edge before tokenize and before embed
- Redact to stable placeholders (`<email:sha256[:12]>`) so cache prefixes stay stable without storing secrets in KV
- Vectors are derived personal data -- Vec2Text recovered 92% of 32-token inputs; ACL + retention on the index as if it were source documents
- Do not send another tenant's documents in the same prompt/cache prefix

### 8.4 API Key Management

- **Scoping**: One key per service/application, scoped by function and environment
- **Rotation**: Automated 90-day rotation (SOC 2, ISO 27001, FedRAMP). Encrypted in transit (TLS 1.3) and at rest (AES-256 via KMS)
- **Virtual keys**: AI gateway holds real provider keys. Services authenticate with virtual keys carrying RBAC, budget limits, and residency policy
- **OpenAI specifics**: Admin API keys cannot call model endpoints. Service account secret returned once; later retrieve redacted. Workload Identity Federation exchanges OIDC/SPIFFE/mTLS for short-lived tokens
- **Anthropic**: GitHub secret scanning auto-deactivates leaked keys. ZDR orgs: CORS disabled -- browser apps must proxy
- **Storage**: HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager. Never in code, env vars on dev machines, logs, or error messages

### 8.5 Audit Trail Requirements by Compliance Framework

| Framework | Key Requirements |
|-----------|-----------------|
| SOC 2 Type II | Immutable audit trails; key rotation evidence; access controls on logs |
| HIPAA | Cryptographic integrity verification; prompt lineage tracking |
| GDPR | Right-to-erasure for logged PII; data minimization |
| ISO 27001 | Key lifecycle documentation; incident response evidence |
| OWASP LLM Top 10 | Prompt injection detection logging; output validation records |

**Immutable audit log fields**: timestamp, `correlation_id`, `tenant_id` (yours, not sent to the model), route, model, `request_id` / `message._request_id`, usage breakdown (cache hit/write, thinking), `stop_reason`, tool names, SHA-256 of redacted prompt, policy decision (allowed tools, ticket id), breaker state.

---

## 9. System Design Scenarios

### 9.1 Scenario: Multi-Provider Streaming Gateway (50K req/min + Embedding Ingest)

**Problem statement**: B2B copilot: 50,000 streaming chat requests/minute peak (~833 rps) with workload W (2k in / 800 out, 80% cache hit), plus nightly embedding ingest of 10M chunks x 512 tokens = 5.12B tokens. p95 TTFT < 2s. Multi-tenant; ZDR org cannot put PHI on Batch/Files.

**Proposed architecture**:

```
                    +----------------------------------------------------------+
                    | EDGE  (N gateway replicas)                               |
                    | auth, tenant TPM, Idempotency-Key on YOUR POST           |
                    | correlation-id, PII redact, MCP ticket mint              |
                    +----------------------------+-----------------------------+
                                                 |
                    +----------------------------v-----------------------------+
                    | CONTROL  Python 3.11+  (one AsyncOpenAI + AsyncAnthropic |
                    |          per process; httpx2/aiohttp; max_retries=0)     |
                    |  Router: cache-warm prefix -> stay vendor; 529 -> FO     |
                    |          extract -> Haiku/4.1; chat -> Sonnet5/GPT-5.4   |
                    |  Sem_oa / Sem_an bulkhead    Breaker per (vendor,model)  |
                    |  count: tiktoken(o200k) vs messages.count_tokens         |
                    |  TaskGroup tools (cancel-on-fail); sequential computer   |
                    +-----+-------------------------------+--------------------+
                          | SSE chat                      | embed Queue+workers
                          v                               v
                    +------------------+            +-------------------------+
                    | DATA  Generation |            | DATA  Embedding         |
                    | Sonnet 5 /       |            | 3-small dims=1536 pin   |
                    | GPT-5.4 Fast*    |            | Batch API nightly 50%   |
                    | KV prompt cache  |            | Voyage-4 family optional|
                    +--------+---------+            +------------+------------+
                             |                                   |
                    +--------v---------+            +------------v------------+
                    | TOOL PROXIES MCP |            | PERSIST  pg batch_id    |
                    | ticket verify    |            | Redis SSE partials      |
                    | least-priv tools |            | ANN pin model+dim+metric|
                    +------------------+            | TELEMETRY usage+SHA-256 |
                                                    +-------------------------+

* Fast mode Enterprise only; shares RPM/TPM; ramp >50%/15min can demote.
```

**Technology choices**: Primary chat: Claude Sonnet 5 for cache read 0.1x ($0.20) and explicit breakpoints; failover GPT-5.4 ($0.25 cached). Extract: Haiku 4.5 ($4.56/1k W). Nightly index: OpenAI Batch text-embedding-3-small $0.01/1M -> $51.20 for 5.12B tokens. HTTP: `DefaultAioHttpClient` on Anthropic. Timeouts: connect 5s, stream idle 30-60s, never 600s on interactive. Pools sized to per-process concurrency, horizontally sharded.

**Capacity math**: 50k RPM x 800 out = 40M OTPM. Anthropic Scale OTPM 2M is 20x too small. OpenAI T5 15k RPM is 3.3x short. Concurrent SSE at 25 tok/s: ~26,667. This is multi-project/provisioned/self-host, not one Start-tier key.

**Cost**: Sonnet 5 cached $9.12/1k x 50 = $456/min peak. GPT-5.4 W $13.40/1k -> $670/min.

**Trade-off matrix**:

| Dimension | A. Single-vendor, SDK defaults | B. Multi-provider gateway (recommended) | C. All Opus 5 Fast + sync embed |
|-----------|-------------------------------|----------------------------------------|--------------------------------|
| **Cost/1k chat** | $9.12 cached; retry amplification | $9.12 primary; Haiku $4.56 extract; embed $51.20 nightly | Opus 5 $22.80; embed 3-large $665.60 sync |
| **Latency** | Embed bursts steal chat keepalives | Separate pools; p50 TTFT <800ms | Fast 50 tok/s helps TPOT; TTFT thinking-bound |
| **Ops complexity** | Low until 26k sockets | Medium (two SDKs, shard math, Batch poll) | Low until invoice |
| **Scalability** | Scale OTPM 2M vs need 40M | Multi-org/provisioned; Batch queue T5 15B | Fast shares RPM/TPM; ramp demotes |

**Decision**: B is the only option that treats 50k RPM as a distributed systems problem (shard, bulkhead, Batch for ingest) and a tokenizer/cost problem (o200k vs count_tokens, cache on the left).

### 9.2 Scenario: High-Throughput Document Embedding Pipeline (100M Documents)

**Problem statement**: A financial services firm needs to embed 100M regulatory documents into a vector store for semantic search. Must support incremental updates (10K new docs/day), handle embedding model version migrations without downtime, and meet a 4-hour SLA for full re-indexing.

**Proposed architecture**:

```
+------------------------------------------------------------------------+
|                        Document Ingestion                              |
|  +--------------+    +--------------+    +--------------------------+  |
|  | S3 / GCS     |--->| Change Data  |--->| Content Hasher           |  |
|  | Document     |    | Capture      |    | (skip unchanged docs)    |  |
|  | Store        |    | (event-      |    | SHA-256 of normalized    |  |
|  |              |    |  driven)     |    | content                  |  |
|  +--------------+    +--------------+    +------------+-------------+  |
+-------------------------------------------------------+----------------+
                                                        |
+-------------------------------------------------------v----------------+
|                        Chunking & Embedding                            |
|  +--------------+    +--------------+    +--------------------------+  |
|  | Semantic     |--->| asyncio.Queue|--->| 200 Worker Coroutines    |  |
|  | Chunker      |    | (maxsize=    |    | asyncio.Semaphore(200)   |  |
|  | (512 tok,    |    |  500)        |    | httpx.AsyncClient        |  |
|  |  50 overlap) |    |              |    | (max_connections=250)    |  |
|  +--------------+    +--------------+    +------------+-------------+  |
|                                                       |                |
|                                          +------------v-------------+  |
|                                          | OpenAI text-embedding-   |  |
|                                          | 3-small (batch API)      |  |
|                                          | 256 dimensions (MRL)     |  |
|                                          +------------+-------------+  |
+-------------------------------------------------------+----------------+
                                                        |
+-------------------------------------------------------v----------------+
|                        Vector Store                                    |
|  +------------------------------------------------------------------+  |
|  | Qdrant (HNSW index)                                              |  |
|  | Metadata: model_name, model_version, embedding_dim,              |  |
|  |   content_hash, chunk_index, doc_id, created_at                  |  |
|  |                                                                  |  |
|  | Two collections during migration:                                |  |
|  |   [v1_embeddings] --read--> active queries                       |  |
|  |   [v2_embeddings] --write-> new + re-indexed docs                |  |
|  |   Dual-read with model-tag filter during transition              |  |
|  +------------------------------------------------------------------+  |
|                                                                        |
|  +--------------+    +--------------+    +--------------------------+  |
|  | DLQ (Redis)  |    | Progress     |    | Canary Query Monitor    |  |
|  | Failed chunks|    | Tracker      |    | (50 queries, weekly MRR)|  |
|  +--------------+    +--------------+    +--------------------------+  |
+------------------------------------------------------------------------+
```

**Cost estimation**:
- 100M docs x 500 tokens avg = 50B tokens
- text-embedding-3-small at $0.02/MTok: **$1,000 standard, $500 batch**
- Google text-embedding-005 alternative: **$312 standard** (3x cheaper)
- Storage: 100M vectors x 256 dims x 4 bytes = ~100 GB (vs 600 GB at 1,536 dims)
- Daily 10K docs incremental: $0.10/day (OpenAI 3-small)

**Trade-off matrix**:

| Dimension | A: OpenAI 3-small (256d, batch) | B: Google text-embedding-005 | C: BGE-M3 Self-hosted |
|-----------|-------------------------------|------------------------------|----------------------|
| **Cost (full index)** | $500 (batch) | $312 | ~$200 (GPU compute only) |
| **Quality (MTEB)** | ~62% | ~60% | ~65% |
| **Ops complexity** | Low (managed API) | Low (managed API) | High (GPU infra) |
| **Data residency** | US (OpenAI default) | Configurable (GCP) | Full control |

**Decision**: Option A for most cases. The 6x storage reduction from 256 dims makes cluster sizing manageable. Self-host (C) only if regulatory requirements mandate on-premise.

**Drift mitigation**: 50 canary queries with known-good answers, run weekly. Track MRR. Alert on 7% week-over-week drop. Quarterly full re-index on parallel collection. Zero-downtime switchover after recall validation passes.

### 9.3 Scenario: Structured-Extraction Service (Invoices / PII)

**Problem statement**: Accounts-payable copilot: 200 invoices/min (~3.3 rps), each 4-8 pages. Extract vendor, totals, line items into strict JSON. Documents contain account numbers, emails, sometimes SSNs. Compliance: ZDR org; no PHI/PII on Batch or Files APIs; immutable audit trail. Target: schema-valid JSON >= 99% of completed jobs; p95 extract < 8s; never execute a tool on truncated `partial_json`.

**Proposed architecture**:

```
  +-------------+    +-----------------------------------------------------+
  | ERP / email |--->| CONTROL  Temporal workflow invoice_id = idem key    |
  | ingest      |    |  1. DLP detect -> redact -> audit map               |
  |             |    |  2. count_tokens (Claude) / tiktoken o200k (GPT)    |
  |             |    |  3. Pydantic InvoiceExtract -> strict JSON Schema   |
  +-------------+    |  4. messages.parse / responses.parse (NOT json_obj) |
                     |  5. validate locally; 400 schema = permanent (no FO)|
                     |  6. HITL if injection_suspected or total mismatch   |
                     +-----------+---------------------------+-------------+
                                 |                           |
                                 v                           v
                     +---------------------+     +-------------------------+
                     | DATA  constrained   |     | TOOL PROXIES            |
                     | Sonnet 5 strict     |     | lookup_vendor (MCP tkt) |
                     | -> GPT-5.4 parse    |     | never create_payment    |
                     | -> deterministic $  |     | JSON-encode all results |
                     +----------+----------+     +-------------------------+
                                v
                     +-----------------------------------------------------+
                     | PERSIST  Postgres checkpoint + WORM audit            |
                     | (cid, sha256 redacted, model, usage, stop_reason,   |
                     |  ticket_id, allowed_tools, human decision)           |
                     | Vectors: redacted text only, ACL=tenant, dim pinned |
                     +-----------------------------------------------------+
```

**Technology choices**: Default: Anthropic `client.messages.parse()` + `output_config.format=json_schema`. Not Instructor as primary (pays extra tokens; still fails). Not JSON mode (no schema; needs substring `json`). Do not put invoices on Message Batches -- ZDR excludes batches.

**Cost**: Each invoice ~ W (2k/800) on Sonnet 5 no cache (unique docs) = $12.00/1k = $0.012/invoice. 200/min x 60 x 24 = 288k/day -> ~$3,456/day uncached. With 1,600-token form schema prefix cached at 5m: $9.12/1k -> ~$2,626/day (25% off).

**Trade-off matrix**:

| Dimension | A. Instructor + Batch 50% | B. Native strict parse (recommended) | C. vLLM xgrammar on-prem |
|-----------|--------------------------|--------------------------------------|--------------------------|
| **Cost/1k** | Instructor 1-Nx; Batch $6.00 but ZDR-ineligible | Sonnet cached $9.12 / uncached $12.00 | GPU capex; no per-token vendor bill |
| **Latency** | Retry loops dominate p95; Batch 24h | p95 < 8s at 3.3 rps | First-request compile; catalog hits fast |
| **Security** | Batch retains app state; raw text in traces | Redaction + WORM; no Batch/Files; MCP tickets | Air-gap wins; still need DLP |

**Decision**: B wins because the constraint set is schema validity + PII + ZDR, not raw $/token. A's Batch discount is unavailable without breaking ZDR. C wins only with a small, repeated schema catalog and an air-gap mandate. Deterministic fallback must still be `InvoiceExtract`-valid so ERP never sees markdown.

---

## 10. Interview Quick Reference

### 10.1 Invariants to State Confidently

1. Hosted APIs **never execute customer tools** -- they emit structured requests your process must run.
2. Prompt cache = **KV reuse**, exact prefix; `prompt_cache_key` is a namespace hint, not a confidentiality boundary (OpenAI caches are org-scoped).
3. KV / prompt cache is **not** RPO=0.
4. `CancelledError` is control flow; swallowing it breaks structured concurrency.
5. Do not retry after consuming streamed output.
6. Tokenizer must match the billed model; finance uses `usage`, not tiktoken.
7. Embeddings are plaintext for DLP (Vec2Text: 92% exact recovery at 32 tokens).
8. Shape (grammar) does not equal safety: grammar does not authorize `DROP TABLE`.
9. One shared async client per process per vendor.
10. Cache stable prefixes (tools, system) at the left; tenant data at the right.

### 10.2 Key Numbers to Memorize

| Number | What |
|--------|------|
| **$9.12 / $12.00 / $6.00** | Sonnet 5 W $/1k: cached / uncached / Batch uncached |
| **$13.40 / $8.00 / $8.80** | GPT-5.4 / GPT-4.1 / GPT-6 Sol W $/1k |
| **$4.56 / $22.80 / $17.92** | Haiku 4.5 / Opus 5 / Opus 5.5 W $/1k |
| **$0.04 / $0.26** | Embed 3-small / 3-large W (2k) $/1k |
| **300x** | Sonnet 5 uncached chat vs 3-small embed on 2k input |
| **$10.24 / $5.12** | 1M chunks x 512 tok 3-small sync / Batch |
| **1.25x / 2x / 0.1x** | Anthropic 5m write / 1h write / cache read |
| **272K -> 2x in / 1.5x out** | GPT-5.4 long-context price cliff |
| **+10% / 1.1x** | OpenAI regional; Anthropic US geo 4.6+ |
| **15-20% / +30%** | tiktoken undercount on Claude; Claude 4.7+ tokenizer vs old |
| **354 / 474** | Sonnet 5 tool-use system tokens auto / any+tool |
| **512-4,096** | Anthropic min cache prefix by model class |
| **8,192 / 2,048 / 300k** | OpenAI embed per-input / batch size / summed tokens |
| **1,536 / 3,072** | 3-small / 3-large default dims; API L2-normalizes `dimensions=` |
| **92% / 89%** | Vec2Text exact 32-tok recovery / full names in MIMIC |
| **600s x 3 = 30 min** | Default timeout x (max_retries+1) non-stream footgun |
| **0.5s / 8s / retries=2** | SDK initial delay / cap / max_retries |
| **1,000 / 100** | SDK default max_connections / max_keepalive |
| **50 / 80 / 100 tok/s** | Fast mode GPT-5.4 / GPT-4.1 / Luna (Enterprise) |
| **15k RPM / 40M TPM** | OpenAI GPT-5.4 Tier 5 |
| **10k / 10M / 2M** | Anthropic Scale RPM / ITPM / OTPM |
| **50% / 33%** | OpenAI+Anthropic Batch / Voyage Batch discount |
| **24h** | Batch expiry; Anthropic strict-tool grammar cache TTL |
| **99.9%** | OpenAI Fast mode uptime SLO (Enterprise) |
| **o200k = 199,998 + 2** | Regular + special tokens |

### 10.3 Interview Traps (Fail These, Fail the Round)

- `AsyncOpenAI()` per request -> connection-pool explosion. Use one per process.
- `encoding_for_model("gpt-4")` / `cl100k_base` on GPT-5-class prompts -> wrong context and cost (`o200k_base` is the encoding).
- tiktoken for Claude; old SDK `count_tokens(text)` for Claude 3+ -> wrong counts.
- Nested Chat Completions tool shape on Responses -> `invalid_request_error` (Responses tools are flat).
- JSON mode (`json_object`) treated as schema adherence; missing substring `json` -> 400.
- Swallowing `CancelledError` inside `TaskGroup` / `asyncio.timeout`.
- Retrying an SSE after the first delta (duplicate billed tokens).
- One global circuit breaker that also kills the failover path.
- Opening the breaker on 429 (throttle, not outage) except spend-cap with no `Retry-After`.
- Mixing embedding bursts onto the streaming-chat keepalive pool.
- Treating the vector index as ciphertext (Vec2Text: 92% exact recovery).

### 10.4 Decision Frameworks

**Model selection**: Cost optimization comes from routing, not from using one model for everything. 60% of enterprise queries are simple (Haiku), 30% standard (Sonnet), 10% complex (Opus). Routing alone saves 60-70%.

**Cache vs compute**: 5-min cache pays off after 1 read. 1-hour cache after 2 reads. Cache prefix must be left-stable: tools, system, corpus on the left; per-user content on the right.

**Batch vs sync**: Use batch (50% off) for non-time-sensitive workloads. But batch is ZDR-ineligible and has no completion webhook -- poll with durable id.

**Self-host vs API**: Self-host wins only with a small repeated schema catalog + air-gap mandate. API wins on ops simplicity for everything else.

### 10.5 Interview Closer

"I bill from `usage`, plan with the provider tokenizer, isolate embed sockets from chat SSE, constrain decode with strict schema, and treat MCP as a ticket-checked proxy -- not as the model's IAM."
