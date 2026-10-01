# Module 05 — LLM Concepts: A Ultimate Deep Dive

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 05 (foundational serving model after agents/MCP; underpins every completion call)  
**Grounded in**: `research/05-llm-concepts.md` (32 sources, 2026-09-30)

An LLM is an autoregressive next-token predictor: given tokens \(x_{<t}\), it samples \(P(x_t \mid x_{<t})\) until EOS or a max-output limit ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)). Text never enters the network as characters—a **tokenizer** maps strings → IDs, then an **embedding** layer maps IDs → dense vectors. Modern chat products are typically **decoder-only** stacks adapted (fine-tune / RLHF / preference optimization) from a base model into an instruct/aligned assistant ([System Design Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). This module treats the hosted/self-hosted **inference path** as a distributed system: control vs data plane, KV as hot state, token economics, and host-side resilience when the model sits inside an agent.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                     │
│  API gateway · auth / org-project keys · RPM·TPM·RPD quotas                  │
│  model routing · prompt_cache_key affinity · system/safety filters           │
│  deadline / max_tokens policy · tenant bulkheads                             │
│                                                                              │
│  ┌────────────┐  ┌─────────────────┐  ┌──────────────────────────────────┐   │
│  │ Auth+ACL   │  │ Rate limiter    │  │ Router / cache sticky key        │   │
│  │ + residency│  │ (token bucket)  │  │ (prefix affinity)                │   │
│  └─────┬──────┘  └────────┬────────┘  └───────────────┬──────────────────┘   │
└────────┼──────────────────┼───────────────────────────┼──────────────────────┘
         │                  │                           │
         ▼                  ▼                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                             DATA PLANE                                       │
│  tokenize → embed → PREFILL (K/V for prompt) → DECODE loop → SAMPLE → stream │
│  GPU/TPU workers · continuous / iteration-level batching · KV block manager  │
│  (PagedAttention: logical→physical blocks, CoW for shared prefixes)          │
└───┬──────────────────────────────┬──────────────────────────────┬────────────┘
    │                              │                              │
    ▼                              ▼                              ▼
┌───────────────────┐   ┌──────────────────────────┐   ┌──────────────────────┐
│   TOOL PROXIES    │   │      PERSISTENCE         │   │     TELEMETRY        │
├───────────────────┤   ├──────────────────────────┤   ├──────────────────────┤
│ Host-side only:   │   │ Conversation / audit log │   │ correlation_id       │
│ schema-validated  │   │ (durable, product layer) │   │ TTFT / TPS / tokens  │
│ tool executors    │   │ Prompt-cache prefix      │   │ cached_tokens hits   │
│ (MCP servers when │   │ (vendor; not your DB)    │   │ 429/503 taxonomy     │
│  agent embeds LLM)│   │ KV blocks (ephemeral on  │   │ circuit state        │
│ Raw Completions / │   │  worker; lost on crash)  │   │ PII redact events    │
│ Messages ≠ MCP    │   │ Compaction summaries     │   │ model version / SKU  │
└─────────┬─────────┘   └────────────┬─────────────┘   └──────────┬───────────┘
          │                          │                            │
          └──────────────────────────┴────────────────────────────┘
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **CONTROL PLANE** | Auth, quotas, routing, model selection, `prompt_cache_key` sticky affinity, system-prompt / safety filters | [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) |
| **DATA PLANE** | Tokenization → prefill → decode → sampling → stream; GPU workers; KV memory manager; continuous batching | [vLLM / PagedAttention](https://arxiv.org/abs/2309.06180); [vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts) |
| **PERSISTENCE** | Durable conversation/audit logs (product); vendor prompt-cache prefixes; **ephemeral** per-request KV; optional compaction | [Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [PagedAttention](https://arxiv.org/abs/2309.06180) |
| **TOOL PROXIES** | Not part of raw Completions/Messages. When an **agent host** wraps the model, tools execute via schema-validated proxies (often MCP servers)—outside the forward pass | [Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); module 04 (MCP) |
| **TELEMETRY** | Correlation IDs, TTFT/TPS, token/cache counters, rate-limit/overload codes, circuit state, PII redact events | [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) |

### End-to-end request-flow narrative (tokenize → prefill → decode → sample)

1. **Ingress (control plane)** — Client hits the API gateway with org/project credentials. Auth + residency policy apply; token-bucket / sliding-window RPM·TPM checks run. First limit hit wins ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
2. **Prompt assembly (control plane)** — System instructions + user content + optional tool schemas + conversation history share one **context window** (input tokens already present + tokens about to be generated) ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). Static prefixes first for cache affinity; optional `prompt_cache_key` / Anthropic `cache_control` breakpoints ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
3. **Tokenize** — Vendor tokenizer maps text → integer IDs. OpenAI: **tiktoken**. Anthropic: **do not use tiktoken** (~15–20% undercount; Claude 4.7+ / Mythos Preview ~30% more tokens vs earlier Claude tokenizers)—use `POST /v1/messages/count_tokens` with the same model ID ([OpenAI tiktoken cookbook](https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb); [Anthropic Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)).
4. **Prefill (data plane)** — Full prompt runs in parallel across prompt tokens; K and V tensors for every layer/position are written into the **KV cache**. This dominates **TTFT** (time-to-first-token ≈ scheduling + prefill) ([PagedAttention](https://arxiv.org/abs/2309.06180); [Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)). Attention over length \(n\) is \(O(n^2 d)\) for the attention matrix ([Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)).
5. **Decode loop (data plane)** — One new token per step. Prior K/V are reused so past tokens are not recomputed. Continuous/iteration-level batching admits new requests between decode steps ([PagedAttention](https://arxiv.org/abs/2309.06180)). PagedAttention pages KV like virtual memory (fixed blocks, block tables, CoW); naive contiguous allocation wastes **60–80%** of KV memory vs **&lt;4%** with paging; a LLaMA-13B sequence’s KV can reach ~**1.7 GB** ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [PagedAttention](https://arxiv.org/abs/2309.06180)).
6. **Sample** — Logits → temperature rescale → optional top-p (nucleus). Prefer altering **either** temperature **or** top_p, not both; even temperature 0.0 is not fully deterministic on some stacks ([OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create/); [Anthropic Completions](https://github.com/anthropics/anthropic-sdk-python/blob/49d639a6/src/anthropic/resources/completions.py)).
7. **Stream / complete** — Tokens stream to the client; telemetry records TTFT, inter-token latency, `cached_tokens`, stop reason. On Claude 4.5+, generation can stop with `stop_reason: "model_context_window_exceeded"` when input + generation hits the ceiling ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)).
8. **Tool proxies (host, if agent)** — If the completion emits tool calls, the **host** (not the raw Completions API) validates schemas and invokes executors / MCP servers, then appends tool results for the next tokenize→prefill→decode→sample cycle. Raw Completions/Messages is **not** MCP.

---

## Part 2 — Core Mechanics & Algorithms

### Transformer fundamentals

Vaswani et al. replace recurrence/convolution with stacked self-attention + position-wise FFNs. Original encoder–decoder: \(N=6\) layers/stack, \(d_{\text{model}}=512\), residual + LayerNorm, multi-head attention, sinusoidal positions. Decoder self-attention is **causally masked** so position \(i\) cannot attend to future positions—preserving autoregression ([Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)).

**Complexity**: self-attention over sequence length \(n\) and head dimension \(d\) is \(O(n^2 d)\) compute and memory for the attention matrix. That quadratic dependence is why context length is both a product feature and a serving cost driver ([Vaswani et al., 2017](https://arxiv.org/abs/1706.03762)).

### Prefill vs decode state machine

```
     ┌────────────┐
     │  ADMITTED  │  (passed control-plane quotas)
     └──────┬─────┘
            │ tokenize + schedule
            ▼
     ┌────────────┐
     │  PREFILL   │  cache hit on exact prefix → cheaper/faster prefill
     │  (TTFT)    │
     └──────┬─────┘
            │ KV written
            ▼
     ┌────────────┐
     │   DECODE   │◄── append sampled token / update KV (loop)
     │  (1 tok)   │───► STREAM tokens to client
     └──────┬─────┘
            │ EOS | max_tokens | window exceeded
            ▼
     ┌────────────┐
     │  RELEASE   │  (free KV blocks; retain conversation log if host persists)
     └────────────┘
```

### Sampling algorithm (nucleus)

1. Compute logits \(z \in \mathbb{R}^{|V|}\).
2. Temperature: \(z'_i = z_i / T\) (\(T \in [0,2]\) on OpenAI chat create).
3. Softmax → \(p_i\).
4. Top-p: keep smallest set \(S\) with \(\sum_{i \in S} p_i \ge p\); renormalize; sample.
5. Append token; if not stop, return to decode.

**Invariant**: causal mask + autoregressive append → position \(t\) never depends on future tokens. **Non-invariant**: temperature 0 ≠ bit-identical across runs/providers ([Anthropic Completions](https://github.com/anthropics/anthropic-sdk-python/blob/49d639a6/src/anthropic/resources/completions.py)).

### Prompting as control-plane steering

Zero-shot, few-shot, and chain-of-thought steer the same context window; they are not separate architectures ([Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Anthropic prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)).

### Context rot (quality invariant under length)

As token count grows, accuracy/recall degrade (**context rot**)—curation matters as much as window size ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)). Empirical anchors: even with perfect retrieval, performance can drop **13.9%–85%** as input length grows; long-horizon search shows **premature termination** before exhausting the window; distractors cause extreme-value attention interference ([EMNLP 2025 findings](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf); [arXiv:2606.29718](https://arxiv.org/abs/2606.29718); [arXiv:2609.22101](https://arxiv.org/abs/2609.22101)).

---

## Part 3 — Token Economics & NFR Analysis

### Verified prices (USD / MTok, Standard / Global Standard, researched 2026-09-30)

Re-check vendor pages before capacity planning—flagship SKUs change.

| Model | Input | Cached input | Output | Notes |
| --- | --- | --- | --- | --- |
| gpt-4o | $2.50 | $1.25 (50% off) | $10.00 | 128K context ([GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o); [Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)) |
| gpt-4.1 | $2.00 | $0.50 (75% off) | $8.00 | ~1.05M ([GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1)) |
| gpt-4.1-nano | $0.10 | $0.025 | $0.40 | ~1.05M ([GPT-4.1 nano](https://developers.openai.com/api/docs/models/gpt-4.1-nano)) |
| Claude Sonnet 5 / 5.5 | $2 | hit $0.20; 5m write $2.50; 1h write $4 | $10 | ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)) |
| Claude Haiku 4.5 | $1 | hit $0.10; 5m $1.25; 1h $2 | $5 | same source |
| Claude Opus 5 / 4.x listed | $5 | hit $0.50; 5m $6.25; 1h $10 | $25 | same source |

Anthropic cache multipliers: **1.25×** base input for 5-minute writes, **2×** for 1-hour writes, **0.1×** for hits (lower on some SKUs). Batch API: **50%** off input and output ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

### Cost formula → `$ per 1k runs`

Let \(I\) = input tokens/run, \(O\) = output tokens/run, \(C_i, C_o\) = $/token input/output, \(f_{\text{cache}}\) = fraction of input billed at cached rate, \(C_{\text{cache}}\) = cached input $/token.

\[
\text{Cost}_{\text{run}} = I\cdot\big((1-f_{\text{cache}})C_i + f_{\text{cache}}C_{\text{cache}}\big) + O\cdot C_o
\]

\[
\$/\text{1k runs} = 1000 \times \text{Cost}_{\text{run}}
\]

**Assumptions (worked examples)** — \(I=100{,}000\), \(O=10{,}000\); prices from tables above; OpenAI cache min prompt length **1,024** tokens; Anthropic hits after write amortized elsewhere ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

| Scenario | Per-run arithmetic | `$ / 1k runs` |
| --- | --- | --- |
| GPT-4.1, no cache | \(0.1\times\$2 + 0.01\times\$8 = \$0.20 + \$0.08 = \$0.28\) | **$280** |
| GPT-4.1, \(f_{\text{cache}}=0.75\) @ $0.50/MTok | input \(0.025\times\$2 + 0.075\times\$0.50 = \$0.0875\) + out $0.08 → **$0.1675** [inferred] | **~$167.50** |
| Claude Sonnet 5, no cache | \(0.1\times\$2 + 0.01\times\$10 = \$0.30\) | **$300** |
| Sonnet 5, 80K hits @ $0.20/MTok + 20K uncached | \(0.02\times\$2 + 0.08\times\$0.20 + 0.01\times\$10 = \$0.156\) | **$156** |

[inferred] Anthropic 5m write (1.25×) payback ≈ one full cache read of that prefix; 1h write (2×) ≈ two reads—matching documented break-even guidance ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Cached tokens still **consume context window** capacity and (OpenAI) still count toward **TPM** ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Latency SLAs — vendor gap + [inferred] budget

> ⚠️ Gap: Public **p50/p95/p99 TTFT** for standard chat APIs is not published as a universal SLA. OpenAI Fast/Priority mode documents streaming targets such as **99% of requests &gt; 80 tokens/sec** (model-dependent; premium pricing)—not TTFT percentiles ([OpenAI Fast mode](https://openai.com/api-fast-mode/)). Self-hosted vLLM publishes relative throughput, not absolute TTFT SLOs ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)).

**[inferred] latency budget** (planning arithmetic, not a vendor guarantee). Decompose:

\[
T_{\text{e2e}} \approx T_{\text{queue}} + T_{\text{TTFT}} + O \times t_{\text{decode}}
\]

where \(T_{\text{TTFT}}\) is prefill-bound (\(O(n)\)–\(O(n^2)\) attention over prompt length) and \(t_{\text{decode}}\) is ms/token after first token (KV/batch bound).

| Percentile | [inferred] budget (chat, \(I{\approx}4\)K, \(O{\approx}400\), streamed) | Arithmetic sketch | Mitigations |
| --- | --- | --- | --- |
| **p50** | **~800–1,500 ms** e2e | TTFT ~400–800 ms + \(400\times 1.5\)–\(2.5\) ms/tok | Static prefix cache; smaller model for FAQ; stream to mask TTFT |
| **p95** | **~2.5–5.0 s** e2e | TTFT ~1.2–2.5 s (long prefill / batch wait) + decode tail | Cap prompt; RAG over stuffing; separate `prompt_cache_key` pools; Priority/Fast where justified |
| **p99** | **~6–15 s** e2e | Queue + noisy-neighbor continuous batch + KV pressure | Deadlines + cancel; bulkheads; fallback to nano/Haiku; reject oversized prefills early |

**Published throughput anchor (not TTFT):** Fast mode **&gt;80 tok/s for 99%** of requests (premium) ([OpenAI Fast mode](https://openai.com/api-fast-mode/)). Self-host: up to **24×** vs HF Transformers / **~3.5×** vs TGI (blog); **2–4×** vs FasterTransformer/Orca at comparable latency (paper) ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [PagedAttention](https://arxiv.org/abs/2309.06180)).

### Throughput & back-pressure

OpenAI enforces org/project **RPM, TPM, RPD, TPD** (first limit hit wins). Example flagship tiers: Build 5k RPM / 1M TPM → Launch 10k / 4M → Grow 15k / 40M (Astra/Sol/Terra); Luna Grow 30k RPM / 180M TPM ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). After ~**1M input TPM**, increase traffic by ≤ **50% every 15 minutes** or risk `429` / `slow_down` even under nominal caps. Distinguish `429` ramp throttling from `503` `server_is_overloaded` ([OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

**Back-pressure design**: client token-bucket mirroring provider limits; `Retry-After` + exponential backoff with jitter; shed load to smaller models; preemption/swap under KV pressure on vLLM; [inferred] per-tenant projects / cache-key pools so one long prefill cannot starve another’s TPM ([PagedAttention](https://arxiv.org/abs/2309.06180); [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

### NFR trade-offs

| NFR | Target posture | LLM-specific notes |
| --- | --- | --- |
| **Availability** | Product SLOs typically 99.9%+ at the **gateway**; model plane has separate overload (`503`) and throttle (`429`) modes | Multi-model fallback chains; bulkheads; never market “unlimited” concurrency past TPM |
| **RPO** | Conversation/audit log: RPO ≈ sync write lag (seconds). **KV cache: RPO = entire in-flight decode** (not durable) | Persist messages + tool results outside the window; do not treat GPU KV as backup |
| **RTO** | Re-prefill from durable prompt (+ partial completion) restores a crashed decode; TTFT paid again; hosted APIs re-bill prompt tokens [inferred] | Checkpoint **conversation**, not KV, for product RTO |
| **Compliance** | Data-residency: Anthropic US-only **1.1×** on Claude 4.6+; OpenAI regional/FedRAMP **+10%** eligible models | PII redact **before** tokenize; retain request IDs / token counts / model versions |

**Explicit trade-off — context length vs cost/latency (and quality vs cache):**

- Longer context → quadratic prefill cost/latency + linear–superlinear token $, and **context rot** can destroy quality before the hard window limit ([EMNLP 2025 findings](https://aclanthology.org/anthology-files/pdf/findings/2025.findings-emnlp.1264.pdf); [Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows)).
- Prompt cache cuts **price** (and often TTFT on hits) but does **not** shrink window occupancy or remove rot; OpenAI cached tokens still burn TPM ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- Prefer RAG + short context when evidence is sparse; use long windows when the working set is dense and curated. Claude 1M models: same per-token rate as short (stated policy); OpenAI gpt-6-* families: separate higher long-context prices ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution note for inference

> ⚠️ Gap: Classic Temporal / Kafka / event-sourced orchestration is an **application** concern above the model API. Vendor docs do not expose distributed locks or leader election for KV placement. Patterns below are inference-native ([research note §3](../research/05-llm-concepts.md)).

| State | Checkpointed? | Semantics |
| --- | --- | --- |
| **KV cache (worker)** | **No** (product durability). Ephemeral per-request working set; ~30% of A100-40GB for 13B in paper memory breakdown vs ~65% weights | Crash → KV gone; recover by **re-prefill** (+ optional partial completion replay). Burns TTFT; re-bills prompt on hosted APIs [inferred] ([PagedAttention](https://arxiv.org/abs/2309.06180)) |
| **KV blocks (vLLM paging)** | Swap/evict under memory pressure; resume by remapping blocks—**serving** durability, not cross-datacenter RPO | CoW for shared prefixes / beams ([PagedAttention docs](https://docs.vllm.ai/en/stable/design/paged_attention/)) |
| **Vendor prompt cache** | Prefix activations / billed reuse; TTL (OpenAI default **30m** on GPT-5.6+ docs; Anthropic 5m/1h writes) | Hit ≠ conversation durability ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) |
| **Conversation log** | **Yes** — durable at product layer (DB/object store/workflow) | Source of truth for replay; compaction summaries are lossy ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |

**Rule**: durable agent workflows checkpoint **messages, tool results, and decisions**—never assume GPU KV survives worker death.

### Failure taxonomy

| Class | Examples | Client posture |
| --- | --- | --- |
| **Transient** | `429` rate/ramp, brief network blips, `503` overload | Retry with exponential backoff + jitter; honor `Retry-After`; circuit breaker |
| **Permanent / client** | `400` prompt too long; invalid model; schema errors | Do not retry same payload; shrink context or fix request |
| **Semantic / product** | Hallucination; context rot; hallucinated tool args; bias / cutoff | Grounding, schema validation, eval—not HTTP retries ([Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)) |
| **Poison pill** | Pathological max_tokens reservation OOMs a shard; adversarial mega-prompt | Admit limits; isolate tenant; DLQ after N fails |
| **Idempotency** | Retries without keys **double-bill** input tokens | Idempotency key at product layer; cancel streams past SLA [inferred] |

### Circuit breaker: closed → open → half-open

```
     ┌──────────┐  failure rate ≥ threshold     ┌────────────┐
     │  CLOSED  │──────────────────────────────►│    OPEN    │
     │  (pass)  │                               │ fail-fast  │
     └────┬─────┘◄── success in half-open ──────│ + fallback │
          │                                     └─────┬──────┘
          │ probe after cooldown                      │
          ▼                                           │
     ┌──────────┐  ◄──────────────────────────────────┘
     │HALF-OPEN │  limited probes to primary
     └──────────┘  failure → OPEN; success → CLOSED
```

Open state routes to **fallback model chain** (e.g. gpt-4.1 → gpt-4.1-nano → deterministic template) rather than hammering a hot SKU.

### Fallback model chains

1. **Primary** — quality SKU (gpt-4.1 / Sonnet 5).  
2. **Secondary** — cheaper/faster (gpt-4.1-nano / Haiku 4.5) with same schema contract.  
3. **Deterministic** — cached FAQ / rules / “degraded mode” response when all model planes fail.  

Preserve correlation IDs across hops; mark `degraded=true` in telemetry.

### Security boundaries (LLM API ≠ MCP)

**Raw Completions / Messages trust boundary**

1. Untrusted user text shares the attention pool with system policy → prompt injection is a topology failure mode ([Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).
2. System prompts are **soft** policy; RLHF is not cryptographic enforcement ([Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts)).
3. Guardrails / safety filters on input and output are the production boundary vs a raw base model.
4. Sampling temperature raises variance of off-policy completions—constrain regulated flows ([OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create/)).

Do **not** pretend the completion API is MCP. MCP is a host↔server tool/context protocol (module 04). The model only emits text/tool-call JSON; the host decides whether to speak MCP.

**When the model is embedded in an agent host — add Zero-Trust MCP + controls**

| Control | Mechanism |
| --- | --- |
| **Zero-Trust MCP** | Per-server clients; `initialize` capability negotiation; Origin validation / localhost bind; OAuth or equivalent for remote servers; never trust tool annotations from untrusted servers |
| **Tool RBAC** | Least-privilege allowlists per tenant/role; schema validate args before execute; sandbox side effects; deny-by-default |
| **PII pipeline** | **detect → redact → audit** **before** tokenization; never log raw PII in prompts; residency endpoints when required (1.1× / +10%) ([Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing)) |
| **Immutable logs** | Append-only audit: `correlation_id`, model version, token counts, tool name/args hash, redact events, decision outcomes—chain-of-custody for agent actions |

---

## Part 5 — Production Enterprise Code

Runnable, deterministic, **no API keys**, **no TODOs**. Tiny logits→sample decode loop plus retries+jitter, circuit breaker, fallback chain, correlation IDs, graceful degradation.

```python
#!/usr/bin/env python3
"""Deterministic local 'LLM' client: decode/sample + resilience primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Structured logging + correlation IDs
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s corr=%(correlation_id)s %(message)s",
)
logger = logging.getLogger("llm_resilient")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


logger.addFilter(CorrelationFilter())


def log(corr: str, msg: str, **extra: object) -> None:
    logger.info(msg + ((" | " + json.dumps(extra, sort_keys=True)) if extra else ""),
                extra={"correlation_id": corr})


# ---------------------------------------------------------------------------
# Tiny deterministic decode / sampling (no network, no keys)
# ---------------------------------------------------------------------------

VOCAB = ["<eos>", "yes", "no", "maybe", "error", "ok", "degraded"]


def _stable_logits(prompt: str, step: int, model_id: str) -> List[float]:
    """Hash-derived logits so the same (prompt, step, model) always matches."""
    out: List[float] = []
    for i, tok in enumerate(VOCAB):
        h = hashlib.sha256(f"{model_id}|{prompt}|{step}|{tok}".encode()).digest()
        # Map 4 bytes → pseudo logit in [-3, 3]
        raw = int.from_bytes(h[i % 28 : i % 28 + 4], "big") / 2**32
        out.append((raw * 6.0) - 3.0)
    return out


def softmax(xs: Sequence[float]) -> List[float]:
    m = max(xs)
    exps = [math.exp(x - m) for x in xs]
    s = sum(exps)
    return [e / s for e in exps]


def sample_top_p(
    logits: Sequence[float],
    temperature: float,
    top_p: float,
    rng: random.Random,
) -> int:
    if temperature <= 0:
        return max(range(len(logits)), key=lambda i: logits[i])
    scaled = [x / temperature for x in logits]
    probs = softmax(scaled)
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    cum = 0.0
    kept: List[int] = []
    for i in order:
        kept.append(i)
        cum += probs[i]
        if cum >= top_p:
            break
    mass = sum(probs[i] for i in kept)
    r = rng.random() * mass
    acc = 0.0
    for i in kept:
        acc += probs[i]
        if r <= acc:
            return i
    return kept[-1]


@dataclass
class DecodeResult:
    text: str
    tokens: List[str]
    model_id: str
    degraded: bool = False


def local_decode(
    prompt: str,
    model_id: str,
    *,
    max_tokens: int = 8,
    temperature: float = 0.0,
    top_p: float = 1.0,
    seed: int = 0,
    fail_rate: float = 0.0,
    fail_kind: Optional[str] = None,
) -> DecodeResult:
    """Autoregressive loop over a fixed vocab. fail_rate simulates provider faults."""
    rng = random.Random(seed ^ hashlib.sha256(model_id.encode()).digest()[0])
    if fail_rate > 0 and rng.random() < fail_rate:
        kind = fail_kind or rng.choice(["429", "503", "timeout"])
        raise TransientLLMError(kind, f"simulated {kind} from {model_id}")

    tokens: List[str] = []
    for step in range(max_tokens):
        logits = _stable_logits(prompt + " " + " ".join(tokens), step, model_id)
        idx = sample_top_p(logits, temperature, top_p, rng)
        tok = VOCAB[idx]
        if tok == "<eos>":
            break
        tokens.append(tok)
    text = " ".join(tokens) if tokens else "ok"
    return DecodeResult(text=text, tokens=tokens, model_id=model_id)


# ---------------------------------------------------------------------------
# Failure types, retries + jitter, circuit breaker, fallback chain
# ---------------------------------------------------------------------------

class TransientLLMError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermanentLLMError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_sec: float = 0.05  # short for demo; production: tens of seconds
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.cooldown_sec:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: single probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
            self.failures = 0


def sleep_backoff(attempt: int, base: float, cap: float, rng: random.Random) -> None:
    # Full jitter: U(0, min(cap, base * 2^attempt))
    delay = rng.uniform(0.0, min(cap, base * (2**attempt)))
    time.sleep(delay)


@dataclass
class ModelEndpoint:
    model_id: str
    call: Callable[..., DecodeResult]
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


def deterministic_fallback(prompt: str, corr: str) -> DecodeResult:
    log(corr, "graceful_degradation", prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:12])
    return DecodeResult(
        text="degraded",
        tokens=["degraded"],
        model_id="deterministic-fallback",
        degraded=True,
    )


def call_with_retries(
    endpoint: ModelEndpoint,
    prompt: str,
    corr: str,
    *,
    max_attempts: int = 3,
    rng: random.Random,
    **kwargs: object,
) -> DecodeResult:
    if not endpoint.breaker.allow():
        raise TransientLLMError("circuit_open", f"breaker open for {endpoint.model_id}")

    last: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            result = endpoint.call(prompt, endpoint.model_id, **kwargs)
            endpoint.breaker.record_success()
            log(corr, "model_ok", model=endpoint.model_id, attempt=attempt, state=endpoint.breaker.state.value)
            return result
        except TransientLLMError as e:
            last = e
            endpoint.breaker.record_failure()
            log(
                corr,
                "model_transient",
                model=endpoint.model_id,
                code=e.code,
                attempt=attempt,
                state=endpoint.breaker.state.value,
            )
            if attempt + 1 < max_attempts and endpoint.breaker.state != CircuitState.OPEN:
                sleep_backoff(attempt, base=0.01, cap=0.05, rng=rng)
            else:
                break
        except PermanentLLMError:
            raise
    assert last is not None
    raise last


def resilient_complete(
    prompt: str,
    chain: Sequence[ModelEndpoint],
    *,
    correlation_id: Optional[str] = None,
    seed: int = 7,
) -> DecodeResult:
    corr = correlation_id or str(uuid.uuid4())
    rng = random.Random(seed)
    log(corr, "request_start", models=[e.model_id for e in chain])

    errors: List[str] = []
    for endpoint in chain:
        try:
            return call_with_retries(endpoint, prompt, corr, rng=rng, seed=seed, temperature=0.0)
        except TransientLLMError as e:
            errors.append(f"{endpoint.model_id}:{e.code}")
            continue

    log(corr, "all_models_failed", errors=errors)
    return deterministic_fallback(prompt, corr)


# ---------------------------------------------------------------------------
# Demo main
# ---------------------------------------------------------------------------

def main() -> None:
    # Primary flakes hard → trips breaker; secondary mostly works; else degraded.
    primary = ModelEndpoint(
        "gpt-4.1-sim",
        lambda prompt, model_id, **kw: local_decode(
            prompt, model_id, fail_rate=1.0, fail_kind="503", **kw
        ),
        breaker=CircuitBreaker(failure_threshold=2, cooldown_sec=0.05),
    )
    secondary = ModelEndpoint(
        "gpt-4.1-nano-sim",
        lambda prompt, model_id, **kw: local_decode(prompt, model_id, fail_rate=0.0, **kw),
        breaker=CircuitBreaker(failure_threshold=3, cooldown_sec=0.05),
    )

    r1 = resilient_complete("classify: is this urgent?", [primary, secondary], correlation_id="demo-001")
    print(json.dumps({"text": r1.text, "model": r1.model_id, "degraded": r1.degraded}, sort_keys=True))

    # Deterministic decode sanity: same seed → same tokens
    a = local_decode("hello", "m0", seed=42, temperature=0.0)
    b = local_decode("hello", "m0", seed=42, temperature=0.0)
    assert a.tokens == b.tokens, (a.tokens, b.tokens)

    # Full chain down → graceful degradation
    dead = ModelEndpoint(
        "dead",
        lambda prompt, model_id, **kw: local_decode(
            prompt, model_id, fail_rate=1.0, fail_kind="429", **kw
        ),
        breaker=CircuitBreaker(failure_threshold=1, cooldown_sec=10.0),
    )
    r2 = resilient_complete("anything", [dead], correlation_id="demo-002", seed=1)
    assert r2.degraded and r2.text == "degraded"
    print(json.dumps({"text": r2.text, "model": r2.model_id, "degraded": r2.degraded}, sort_keys=True))
    print("ok")


if __name__ == "__main__":
    main()
```

Extract the code block to a `.py` file and run with `python3`. Expected: secondary-model answer for `demo-001`, then `degraded` for `demo-002`, then `ok`.

---

## Part 6 — Architectural System Design Scenarios

Exactly **two** scenarios. Interview prompts only after both.

### Scenario 1 — Multi-tenant hosted chat API (100k runs/day, cost-capped)

**Problem statement**  
Design a multi-tenant SaaS chat backend at ~**100k completions/day**, p95 interactive UX, org isolation, and a hard monthly model budget. Mix of FAQ classification and deep reasoning. Data may leave the VPC only via approved residency endpoints.

**Proposed architecture**  
Edge API → auth/quota (per-tenant RPM/TPM mirrors) → PII detect→redact→audit → prompt assembly (system + RAG ≤ budget, static prefix first) → router: Haiku 4.5 / gpt-4.1-nano for FAQ; Sonnet 5 / gpt-4.1 for reasoning → streaming Completions/Messages → telemetry (`correlation_id`, `cached_tokens`, breaker state). Separate vendor **projects** per env/tenant tier; optional residency SKUs (**1.1×** / **+10%**). Conversation log in your DB; never treat vendor KV/prompt-cache as RPO.

```
┌────────────┐   ┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ Edge API   │──►│ Auth + tenant   │──►│ PII detect →     │──►│ Prompt assembly │
│ (ingress)  │   │ RPM/TPM mirrors │   │ redact → audit   │   │ sys+RAG+prefix  │
└────────────┘   └────────┬────────┘   └──────────────────┘   └────────┬────────┘
                          │                                            │
                          ▼                                            ▼
                 ┌────────────────┐                          ┌──────────────────┐
                 │ Vendor project │                          │ Model router     │
                 ├────────────────┤                          │ FAQ→nano/Haiku   │
                 │ bulkheads/env  │                          │ Deep→4.1/Sonnet  │
                 └───────┬────────┘                          └────────┬─────────┘
                         │                                            │
         ┌───────────────┴───────────────┐                            │
         ▼                               ▼                            ▼
┌─────────────────┐            ┌─────────────────┐          ┌──────────────────────┐
│ Conversation DB │            │ Telemetry sink  │◄─────────│ Streaming Completions│
│ (RPO source)    │            │ corr/cache/brk  │          │ /Messages + residency│
└─────────────────┘            └─────────────────┘          └──────────────────────┘
```

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| A. Single frontier SKU for all traffic | High ($280/1k on GPT-4.1 @ 100K/10K shape) | Vendor TTFT; simple | Low | Shared infra; residency uplift | TPM/RPM tiers + ramp rule |
| B. Tiered router + prompt cache (recommended) | Medium (~$156–$167/1k on cached shapes) | Better TTFT on cache hits; nano/Haiku for FAQ | Low–med | Same API trust model + redact gate | Scales with tier headroom; cache sticky keys |
| C. Self-host all traffic on day one | CapEx/GPU; no $/MTok | Tunable but cold-start ops | High | VPC isolation | 2–4× serving efficiency possible; ops-bound |

**Decision rationale**  
**B** wins under cost cap + low ops headcount: verified cache economics cut $/1k runs sharply; small models absorb high-QPS FAQ; residency multipliers apply only where required. Self-host (C) is premature until GPU amortization beats token spend and the team can own KV/paging.

### Scenario 2 — Long-horizon agent (200K–1M windows) with tools

**Problem statement**  
Design an internal research agent that may touch **200K–1M** token working sets, call tools (search, tickets, code), and must survive worker crashes mid-run without double-charging every prefill blindly. Quality must resist context rot; security requires tool RBAC and immutable decision logs.

**Proposed architecture**  
Agent host (Temporal/LangGraph-style durable workflow) checkpoints **conversation + tool results**, not KV. Each model turn: assemble curated context (JIT IDs, recite-then-answer) → Completions/Messages → if tool calls, **Zero-Trust MCP** / schema-validated proxies with RBAC → observe → checkpoint. Compaction when approaching window limits (Claude Sonnet/Opus 5 family **1M**, Sonnet 4.5 **200K**; GPT-4.1 ~**1M**, GPT-4o **128K**) ([Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1)). Circuit breaker + fallback Haiku/nano for non-critical subcalls; primary frontier for final synthesis.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Durable agent host (Temporal / LangGraph)                                  │
│ checkpoint: conversation + tool results (NOT GPU KV)                       │
│   ┌──────────────┐   ┌─────────────────┐   ┌────────────────────────────┐  │
│   │ Context build│──►│ Completions /   │──►│ Circuit breaker + fallback │  │
│   │ JIT + compact│   │ Messages (prim.)│   │ Haiku/nano on subcalls     │  │
│   └──────┬───────┘   └────────┬────────┘   └─────────────┬──────────────┘  │
└──────────┼────────────────────┼──────────────────────────┼─────────────────┘
           │                    │ tool_calls               │
           ▼                    ▼                          ▼
┌──────────────────┐   ┌───────────────────┐    ┌────────────────────────────┐
│ Vector / memory  │   │ Zero-Trust MCP    │    │ Immutable decision log     │
│ + compaction art.│   ├───────────────────┤    │ corr_id · model · tool hash│
└──────────────────┘   │ + Tool RBAC gate  │    └────────────────────────────┘
                       └─────────┬─────────┘
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
     │ Search MCP     │ │ Tickets MCP    │ │ Code MCP       │
     └────────────────┘ └────────────────┘ └────────────────┘
```

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| A. Stuff full history every turn | Linear–superlinear $; Claude 1M no long surcharge (stated); OpenAI gpt-6-* long rates higher | Prefill-heavy TTFT | Med | Huge PII surface in-window | Hits rot / premature termination before max window |
| B. Durable workflow + RAG/compaction + MCP tool RBAC (recommended) | Retrieval + fewer tokens/turn; cache static tool schemas | Extra hops; lower prefill | Med | Redact + RBAC + immutable logs; MCP ≠ raw API | Scales with index + workflow workers, not \(n^2\) every turn |
| C. Self-host vLLM for the agent brain | GPU amortization; full KV control | Tunable; KV ~1.7 GB/seq class on 13B | High | VPC + your paging | Paper 2–4× throughput; LMSYS anecdote ~30K–60K req/day class ([vLLM blog](https://vllm.ai/blog/2023-06-20-vllm)) |

**Decision rationale**  
**B** wins for enterprise agents: durable execution lives in the **conversation log / workflow**, matching what survives crashes; MCP/RBAC attaches at the host; rot mitigations (compaction, JIT context, quote-then-answer) beat naive stuffing (A). Choose C when data residency forbids hosted weights **and** ops can own PagedAttention fleets.

---

### Interview prompts (after both scenarios)

1. Walk tokenize → prefill → decode → sample and mark where TTFT vs inter-token latency is determined.  
2. Compute `$/1k runs` for GPT-4.1 vs Sonnet 5 at 100K/10K with and without cache; state tokenizer assumptions.  
3. What is checkpointed on worker death—KV or conversation log—and how does that set RPO/RTO?  
4. Draw closed → open → half-open and place a three-tier fallback chain on the open path.  
5. Why is raw Completions not MCP, and where do Zero-Trust MCP + tool RBAC + PII detect→redact→audit attach?  
6. Given context rot evidence (13.9%–85% drops), when do you refuse a 1M-window design in favor of RAG?

---

## Sources (from research)

Primary: [Newsletter — LLM Concepts](https://newsletter.systemdesign.one/p/llm-concepts); [Vaswani et al., 2017](https://arxiv.org/abs/1706.03762); [PagedAttention / vLLM](https://arxiv.org/abs/2309.06180); [vLLM blog](https://vllm.ai/blog/2023-06-20-vllm); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [OpenAI Rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [OpenAI Pricing](https://developers.openai.com/api/docs/pricing); [Anthropic Pricing](https://platform.claude.com/docs/en/about-claude/pricing); [Anthropic Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting); [Anthropic Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows); [OpenAI Fast mode](https://openai.com/api-fast-mode/); context-rot papers cited in research note.
