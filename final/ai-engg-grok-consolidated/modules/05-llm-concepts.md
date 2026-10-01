# Module 05: LLM Concepts

### What Is This?

A Large Language Model is a next-word prediction machine: you feed it text, it guesses the most probable continuation one token at a time, like a supremely well-read autocomplete engine that has digested trillions of words. Under the hood, it is a deep neural network called a Transformer that converts your text into numbers, runs those numbers through dozens of attention layers (where every word "looks at" every other word to understand context), and then samples from a probability distribution to pick the next token. From an infrastructure perspective, serving an LLM is a distributed systems problem: you must manage GPU memory (the KV cache holding intermediate computations can reach terabytes), handle rate limits and failures across providers, control costs that scale with every token processed, and guard against hallucinations and prompt injection attacks. This module treats the LLM as a production system -- covering everything from the math of self-attention through token pricing tables to circuit breaker patterns and fallback chains.

---

## 1. System Topology & Data Flow

A production LLM inference platform spans five cooperating planes. The **Control Plane** handles authentication, rate limiting, model routing, and prompt-cache affinity. The **Serving/Data Plane** runs tokenization, prefill, decode, and sampling on GPU workers with continuous batching and KV cache management. The **Memory Plane** manages model weights, KV caches (via PagedAttention), and prefix caches. The **Resilience Plane** provides circuit breakers, fallback model chains, and retry logic. The **Observability Plane** emits per-request telemetry for cost, latency, quality, and compliance tracking.

```
+---------------------------------------------------------------------------------+
|                              CONTROL PLANE                                       |
|  API gateway - auth / org-project keys - RPM/TPM/RPD quotas                     |
|  Model router (rule/semantic/learned cascade) - prompt_cache_key affinity        |
|  System/safety filters - deadline/max_tokens policy - tenant bulkheads           |
+--------+-------------------+----------------------------+-----------------------+
         |                   |                            |
         v                   v                            v
+---------------------------------------------------------------------------------+
|                         SERVING / DATA PLANE                                     |
|  tokenize -> embed -> PREFILL (K/V for prompt) -> DECODE loop -> SAMPLE -> stream|
|  Continuous batch scheduler (iteration-level; admits/releases per decode step)    |
|  Chunked prefill (512-tok chunks interleaved with decode for fairness)           |
|  Speculative decoding: draft proposes K tokens, target verifies in one pass      |
+--------+-------------------+----------------------------+-----------------------+
         |                   |                            |
         v                   v                            v
+---------------------------------------------------------------------------------+
|                           MEMORY PLANE                                           |
|  Model Weights (FP8/INT4 on HBM; TP shards intra-node; PP across nodes)         |
|  KV Cache Manager (PagedAttention: virtual-memory pages, <4% waste vs 60-80%)   |
|  Prefix Cache (shared system prompts; 5-10x TTFT reduction; RadixAttention)     |
+--------+-------------------+----------------------------+-----------------------+
         |                   |                            |
         v                   v                            v
+---------------------------------------------------------------------------------+
|                         RESILIENCE PLANE                                         |
|  Circuit Breaker (per-provider: CLOSED -> OPEN -> HALF-OPEN)                    |
|  Fallback Chain (Opus -> Sonnet -> Haiku; graceful degradation, not hard fail)  |
|  Retry + Backoff (429: Retry-After; 5xx: exp backoff + jitter; mid-stream drop) |
+--------+-------------------+----------------------------+-----------------------+
         |                   |                            |
         v                   v                            v
+---------------------------------------------------------------------------------+
|                       OBSERVABILITY PLANE                                        |
|  Per-request: model, tokens_in/out, TTFT, TPOT, cost_usd, cached_tokens        |
|  Per-provider: p50/p95/p99 TTFT+TPOT, circuit-breaker state, error rate         |
|  Per-quality: hallucination rate by mode, faithfulness score, format compliance  |
|  Budget: cumulative spend per key/team/project, burn-rate alerts                |
|  Compliance: PII-redacted prompt/completion audit log, data residency routing   |
+---------------------------------------------------------------------------------+
```

**End-to-end request flow:**

1. **Ingress (Control Plane)** -- Client hits the API gateway with org/project credentials. Auth + residency policy apply; token-bucket/sliding-window RPM/TPM checks run. First limit hit wins.
2. **Prompt Assembly (Control Plane)** -- System instructions + user content + optional tool schemas + conversation history share one context window. Static prefixes placed first for cache affinity; optional `prompt_cache_key` or Anthropic `cache_control` breakpoints.
3. **Tokenize** -- Vendor tokenizer maps text to integer IDs. OpenAI uses **tiktoken**. Anthropic: do NOT use tiktoken (~15-20% undercount; Claude 4.7+/Mythos ~30% more tokens vs earlier Claude). Use `POST /v1/messages/count_tokens` with the same model ID.
4. **Prefill (Data Plane)** -- Full prompt processed in parallel; K and V tensors written to the KV cache. This dominates **TTFT** (time-to-first-token). Attention is O(n^2 d). Typical: ~50ms for 4K tokens on H100.
5. **Decode Loop (Data Plane)** -- One new token per step; prior K/V reused. Continuous batching admits new requests between decode steps. PagedAttention manages KV like virtual memory. Typical: 10-20ms/token for 70B model.
6. **Sample** -- Logits -> temperature rescale -> optional top-p (nucleus). Prefer altering either temperature or top_p, not both. Even temperature 0.0 is not fully deterministic.
7. **Stream/Complete** -- Tokens stream to client; telemetry records TTFT, inter-token latency, cached_tokens, stop reason. Claude 4.5+: can stop with `stop_reason: "model_context_window_exceeded"`.
8. **Tool Proxies (Host, if agent)** -- If completion emits tool calls, the host validates schemas and invokes executors/MCP servers, then appends results for the next cycle. Raw Completions/Messages is NOT MCP.

---

## 2. Core Mechanics & Algorithms

### 2.1 Transformer Architecture

All modern LLMs (GPT, Claude, Gemini, Llama) are decoder-only Transformers (Vaswani et al., 2017). Recurrence is replaced with self-attention, enabling fully parallel sequence processing.

**Per-layer computation:**

```
Input: x (sequence of token embeddings, shape [seq_len, d_model])

  x_norm = LayerNorm(x)                  <-- Pre-LN (modern default)

  Multi-Head Causal Self-Attention:
    Q = x_norm @ W_Q    K = x_norm @ W_K    V = x_norm @ W_V
    scores = (Q @ K^T) / sqrt(d_k)
    scores[i,j] = -inf  where j > i      <-- causal mask (hard constraint)
    attn = softmax(scores) @ V
    h heads in parallel, each d_k = d_model / h
    output = Concat(head_1, ..., head_h) @ W_O

  x = x + attn_output                    <-- residual connection

  x_norm2 = LayerNorm(x)

  Position-wise FFN (acts as key-value memory):
    FFN(x) = GELU(x @ W_1 + b_1) @ W_2 + b_2
    d_ff = 4 * d_model (original); SwiGLU in modern models

  x = x + ffn_output                     <-- residual connection
```

**Complexity analysis.** Self-attention is **O(n^2 * d)** for sequence length n and model dimension d: the QK^T product is O(n^2 * d_k) per head, repeated across h heads. The FFN is O(n * d * d_ff), linear in sequence length. For long contexts (n >> d), attention dominates. This quadratic dependence is why context length is both a product feature and a serving cost driver.

**Causal masking** enables autoregressive generation. By setting scores[i,j] = -inf for j > i before softmax, each token attends only to itself and all preceding tokens. This is not approximate -- it is an exact hard constraint.

**Positional encoding evolution:**

| Generation | Method | Properties |
|---|---|---|
| Original (2017) | Sinusoidal sin/cos | Fixed; generalizes to unseen lengths; absolute position |
| GPT-2 era | Learned embeddings | Tied to max trained length; largely abandoned |
| Modern (2024+) | Rotary Position Embedding (RoPE) | Encodes relative position in attention dot product; supports context extension via NTK-aware scaling or YaRN without retraining |

**Key invariant:** Residual connections create gradient highways. Without them, gradients vanish across 80+ layers (Llama 3.1 has 126 layers). Pre-LN (normalizing before each sub-layer) is now standard because it stabilizes training at depth without learning-rate warmup tricks.

### 2.2 Tokenization

Tokenizers convert text to integer sequences. Token != word. "counterintuitive" might be 3 tokens; "running" might be 1. Heuristic: **1 token ~ 4 characters ~ 0.75 words** in English. Non-English and code have worse efficiency.

| Algorithm | Used By | Vocab Size | Mechanism |
|---|---|---|---|
| BPE (Byte-Pair Encoding) | GPT-4, Claude, Llama | 100K-200K | Iteratively merges most frequent byte pairs |
| SentencePiece (Unigram) | T5, mT5 | 32K-256K | Probabilistic subword selection |
| tiktoken | OpenAI models | 100K+ | Optimized BPE implementation |

**Production-breaking edge cases:**
- Trailing whitespace changes tokenization, silently altering model behavior
- Numbers split inconsistently: "123456" may become ["123", "456"], breaking arithmetic
- Unicode homoglyphs bypass content filters while appearing identical to humans
- Right-to-left Unicode markers reverse displayed text while model processing is unchanged
- Emoji and CJK character tokenization is highly variable across tokenizers

### 2.3 Inference: Prefill vs Decode

LLM inference has two phases with fundamentally different hardware bottlenecks:

| Property | Prefill Phase | Decode Phase |
|---|---|---|
| **Input** | Entire prompt (N tokens) | Last generated token + full KV cache |
| **Operation** | One forward pass, all N tokens in parallel | One token per forward pass, sequential |
| **Bottleneck** | **Compute-bound** (saturates GPU tensor cores) | **Memory-bandwidth-bound** (GPU idles waiting for HBM reads) |
| **Key metric** | TTFT (Time to First Token) | TPOT (Time Per Output Token) / TPS |
| **Typical** | ~50ms for 4K tokens on H100 | 10-20ms/token for 70B model on H100 |
| **Optimization** | Chunked prefill (512-tok chunks interleaved with decode) | Speculative decoding (draft proposes K tokens, verified in one pass) |

**KV cache sizing formula:**

```
KV_cache_per_token = 2 * num_layers * (num_heads * dim_head) * precision_bytes

Llama 3.1 70B @ FP16:
  = 2 * 80 * (64 * 128) * 2 bytes = ~1.3 MB per token per sequence

At 128K context, batch size 32:
  = 1.3 MB * 128K * 32 = ~5.3 TB  <-- why KV management is the central challenge
```

**KV cache management strategies:**

| Strategy | Mechanism | Impact |
|---|---|---|
| **PagedAttention (vLLM)** | Virtual-memory-style paging; eliminates fragmentation; CoW for shared prefixes | **14-24x** throughput vs naive HF; waste **<4%** vs **60-80%** |
| **Prefix caching** | Reuse KV across requests with shared prefixes (system prompt, few-shot) | **5-10x** TTFT reduction for cached prefixes |
| **KV quantization** | INT8: near-lossless; INT4: small quality loss | 2x more concurrent sequences |
| **NVFP4** | 4-bit KV cache on-device | 3x TTFT via reduced evictions |

**Continuous batching** admits new requests and releases finished ones at every decode step, rather than waiting for a fixed batch. Key insight: sequences finish at different times; static batching wastes GPU cycles on padding.

**Chunked prefill** breaks long prefill into 512-token chunks interleaved with decode steps. Prevents long prompts from starving concurrent requests. Trade-off: smaller chunks protect streaming smoothness but reduce throughput; larger chunks protect throughput but add latency variance.

### 2.4 Speculative Decoding

A small, fast draft model proposes K candidate tokens. The large target model verifies all K in a single forward pass. Accepted tokens are free; rejected tokens fall back to standard sampling. **Output quality is mathematically identical to standard decoding -- this is not an approximation.**

**Acceptance rate (alpha) determines real-world speedup:**
- alpha 0.75-0.85 (code, structured data): 3-5x speedup
- alpha 0.60-0.80 (general queries): 2-3x speedup
- alpha < 0.50 (creative, domain-specific): can hurt performance -- wasted cycles on rejected tokens

| Variant | Mechanism | Speedup | Trade-off |
|---|---|---|---|
| External draft | Separate smaller model (e.g., 8B for 70B) | 2-3x | +2-6 GB VRAM |
| **EAGLE-3** | Learned prediction head on target model | **3.5-5x** (code) | ~4.8x on HumanEval |
| P-EAGLE | Parallel drafting (all K tokens in one pass) | 4-5x | 20-30% over EAGLE-3 |
| Medusa | Multiple prediction heads on base model | 2-3x | No separate draft model |
| DFlash | Block diffusion speculative decoding | 6x claimed | Emerging, not proven |

### 2.5 Quantization

Reducing precision to shrink memory footprint and increase throughput. **Decision ladder** (NVIDIA-recommended): FP8 first -> INT8 SmoothQuant -> AWQ/GPTQ INT4.

| Format | Quality Loss (vs FP16, 70B) | VRAM Savings | Throughput Lift | When to Use |
|---|---|---|---|---|
| **FP8 (E4M3)** | ~0.4 pts MMLU | ~50% | 1.4-1.7x | Default for H100+; near-lossless |
| INT8 SmoothQuant | ~0.7 pts | ~50% | ~1.5x | Mature fallback |
| AWQ INT4 | ~1.6 pts | ~75% | 2.6-3.1x | Must fit 70B on single GPU |
| GPTQ INT4 | ~1.5-2.4 pts | ~75% | 2.6-3.1x | Wider variance; use AWQ instead |

**The sub-4-bit cliff:** INT3 loses ~6 pts on average. Sub-4-bit is research-grade for production.

### 2.6 Scaling Laws and Training Pipeline

**The Chinchilla correction and overtraining paradigm.** Chinchilla (DeepMind, 2022) established compute-optimal training at ~20 tokens per parameter. But compute-optimal minimizes training FLOP, not deployment cost. Inference dominates lifetime cost, so modern practice trains smaller models on far more data ("overtraining").

| Model | Params | Tokens | Tok/Param | vs Chinchilla 20:1 |
|---|---|---|---|---|
| GPT-3 | 175B | 300B | 1.7 | 12x undertrained |
| Llama-2 70B | 70B | ~2T | ~28 | ~Chinchilla optimal |
| Llama-3 8B | 8B | 15T | 1,875 | 94x overtrained |
| Qwen3-0.6B | 0.6B | 36T | 60,000 | 3,000x overtrained |
| Liquid LFM2.5-350M | 350M | 28T | 80,000 | 4,000x overtrained (record) |

**Takeaway:** The right model is smaller and trained longer than Chinchilla suggests. Overtrain for inference economics.

**Training pipeline:**
1. **Pre-training** -- Next-token prediction on trillions of tokens. Produces base model that predicts text but is not a useful assistant.
2. **Instruction Fine-tuning (SFT)** -- Curated instruction-response pairs. Model learns to follow structured instructions.
3. **RLHF/RLAIF Alignment** -- Humans/AI rank responses -> reward model -> policy optimization. Teaches helpfulness, safety, clarity. Reasoning models (o3, Opus 4) have chain-of-thought built in via training, not prompting.

**Parameter-efficient fine-tuning (PEFT):**
- **LoRA**: Adds small rank-decomposition matrices to attention layers. Trains <1% of parameters. Standard for domain adaptation.
- **QLoRA**: LoRA on 4-bit quantized base model. Enables fine-tuning 70B models on a single GPU.

### 2.7 Sampling Algorithm (Nucleus)

```
1. Compute logits z in R^|V|
2. Temperature: z'_i = z_i / T  (T in [0,2] on OpenAI chat create)
3. Softmax -> p_i
4. Top-p: keep smallest set S with sum(p_i for i in S) >= p; renormalize; sample
5. Append token; if not stop, return to decode
```

**Invariant:** causal mask + autoregressive append means position t never depends on future tokens. **Non-invariant:** temperature 0 does NOT equal bit-identical across runs/providers.

### 2.8 Serving Engines (2026)

| Engine | Key Innovation | Throughput | TTFT | Hardware | Status |
|---|---|---|---|---|---|
| **vLLM** | PagedAttention; broadest hardware | High (3,245 tok/s Llama-2-70B, 4 GPU TP) | Good | NVIDIA + AMD | Default |
| **TensorRT-LLM** | NVIDIA kernel optimization | Highest on NVIDIA (10K+ tok/s H100 FP8) | Best (sub-10ms batch-1) | NVIDIA only | Active |
| **SGLang** | RadixAttention for KV reuse | Competitive; excels on prefix-heavy | Good | NVIDIA + AMD | Rising |

**HuggingFace TGI**: Frozen Dec 2025, archived Mar 2026. Migrate away.

**Decision:** Shipping this week, general purpose: vLLM. NVIDIA-committed: TensorRT-LLM. Agentic/RAG with heavy prefix reuse: SGLang.

### 2.9 Context Rot

As token count grows, accuracy/recall degrade -- curation matters as much as window size.
- Even with perfect retrieval, performance can drop **13.9%-85%** as input length grows
- Long-horizon search shows **premature termination** before exhausting the window
- Distractors cause extreme-value attention interference; maintaining accuracy may require evidence margins scaling like Omega(sqrt(log N))
- Mitigations: retrieve-then-recite, short-context re-prompting (~4% gain on RULER for GPT-4o), compaction, tool-result clearing, JIT context, quote-then-answer

---

## 3. Token Economics & NFR Analysis

### 3.1 Provider Pricing (September 2026)

Prices dropped ~80% between early 2025 and early 2026. **Output tokens cost 5-6x input tokens** -- verbose responses, not long prompts, dominate most bills.

**Flagship Tier ($/1M tokens):**

| Model | Input | Output | Notes |
|---|---|---|---|
| OpenAI GPT-5.6 Sol | $5.00 | $30.00 | |
| Anthropic Claude Opus 5 | $5.00 | $25.00 | Cheaper output |
| OpenAI o3 (reasoning) | $10-15 | $40-60 | Reasoning tokens add significant cost |

**Production Tier:**

| Model | Input | Cached Input | Output | Context |
|---|---|---|---|---|
| Claude Sonnet 5/5.5 | $2 | hit $0.20; 5m write $2.50; 1h write $4 | $10 | 1M |
| GPT-4o | $2.50 | $1.25 (50% off) | $10.00 | 128K |
| GPT-4.1 | $2.00 | $0.50 (75% off) | $8.00 | ~1.05M |
| Gemini 3.1 Pro | $2.00 | -- | $12.00 | -- |

**Budget Tier:**

| Model | Input | Cached Input | Output |
|---|---|---|---|
| GPT-4.1-nano | $0.10 | $0.025 | $0.40 |
| GPT-5.6 Luna | $0.20 | $0.01 | $1.20 |
| Haiku 4.5 | $1 | hit $0.10 | $5 |
| Gemini 2.0 Flash | $0.10 | -- | $0.40 |

**Anthropic cache multipliers:** 1.25x base input for 5-minute writes, 2x for 1-hour writes, 0.1x for hits. Batch API: 50% off input and output. **OpenAI GPT-5.6+:** cache writes 1.25x uncached, reads 0.1x, default TTL 30m.

### 3.2 Cost Formula: $ Per 1K Runs

```
Cost_run = I * ((1 - f_cache) * C_i + f_cache * C_cache) + O * C_o
$/1k runs = 1000 * Cost_run
```

Where I = input tokens/run, O = output tokens/run, C_i/C_o = $/token, f_cache = cached fraction, C_cache = cached $/token.

**Worked examples (I=100K, O=10K):**

| Scenario | Per-run | $/1k runs |
|---|---|---|
| GPT-4.1, no cache | 0.1M * $2 + 0.01M * $8 = $0.28 | **$280** |
| GPT-4.1, 75% cache | $0.0875 + $0.08 = $0.1675 | **~$168** |
| Sonnet 5, no cache | 0.1M * $2 + 0.01M * $10 = $0.30 | **$300** |
| Sonnet 5, 80K cached | $0.04 + $0.016 + $0.10 = $0.156 | **$156** |

**Customer support chatbot example (2K input, 500 output, Sonnet 5):**

| Configuration | Per conversation | Monthly (1K/day) |
|---|---|---|
| No optimization | $0.009 | ~$270 |
| + Prompt caching (1500 tok cached) | $0.0063 | ~$189 (30% savings) |
| + Cascade routing (70% Haiku) | $0.0052 | ~$156 (42% savings) |
| + Both combined | ~$0.0036 | ~$109 (60% savings) |

### 3.3 Cost Reduction Levers

| Lever | Savings | Mechanism |
|---|---|---|
| Prompt caching | 25-40% | Cached tokens at 10-25% of input price |
| Batch API | 50% | Async processing, both providers |
| Model routing | 40-85% | ICLR 2025: 85% cost cut, 95% quality, sending 14% to strong model |
| Volume commits | 20-50% | Common above $5-10K/month |
| Output optimization | 10-30% | Shorter outputs via prompts, structured formats |

### 3.4 Latency SLO Targets

| Workload | p50 TTFT | p95 TTFT | p99 TTFT | p95 TPS |
|---|---|---|---|---|
| Conversational chat | <200ms | <500ms | <1s | >30 tok/s |
| Code completion | <80ms | <150ms | <200ms | >50 tok/s |
| Document analysis | <1s | <3s | <5s | >20 tok/s |
| Batch processing | relaxed | relaxed | relaxed | maximize |
| Agentic (per LLM call) | <300ms | <800ms | <1.5s | >40 tok/s |

**Tail latency matters more than averages.** A p50 of 100ms with a p99 of 5s means 1 in 100 users waits 50x longer. Monitor and SLO on p95/p99.

**Latency budget decomposition:**
```
T_e2e = T_queue + T_TTFT + O * t_decode
```
Where T_TTFT is prefill-bound (O(n)-O(n^2) over prompt length) and t_decode is ms/token (KV/batch bound).

### 3.5 Throughput & Rate Limits

OpenAI enforces org/project **RPM, TPM, RPD, TPD** (first limit hit wins):

| Tier | Models | RPM | TPM |
|---|---|---|---|
| Build | Astra/Sol/Terra | 5,000 | 1,000,000 |
| Launch | Astra/Sol/Terra | 10,000 | 4,000,000 |
| Grow | Astra/Sol/Terra | 15,000 | 40,000,000 |
| Grow | Luna | 30,000 | 180,000,000 |

**Ramp rule:** After ~1M input TPM, increase traffic by <=50% every 15 minutes or risk 429/slow_down. Distinguish 429 ramp throttling from 503 server_is_overloaded.

### 3.6 NFR Requirements

| NFR | Target | Implementation |
|---|---|---|
| **Availability** | 99.9% (8.7h/yr) | Multi-provider failover; circuit breakers; health checks on VRAM/queue/latency |
| **RPO** | 0 (stateless inference) | Prompts are idempotent; conversation state in app DB. KV cache is ephemeral -- RPO = entire in-flight decode |
| **RTO** | <30s failover | Pre-warmed standby models; health probe <10s. Re-prefill from durable prompt costs TTFT again |
| **Compliance** | SOC 2, GDPR, EU AI Act (Aug 2026) | PII redaction before model; audit logs; data residency routing; adversarial testing |
| **Scalability** | 10x burst | Autoscale on queue depth + TPOT, not request count |

**Critical trade-off -- context length vs cost/latency vs quality:**
- Longer context -> quadratic prefill cost/latency + context rot destroying quality before the hard window limit
- Prompt cache cuts price and often TTFT but does NOT shrink window occupancy or remove rot; cached tokens still burn TPM
- Prefer RAG + short context when evidence is sparse; use long windows when the working set is dense and curated

---

## 4. Distributed Resilience & Security

### 4.1 Model Parallelism Strategies

| Strategy | Mechanism | Comms Cost | Best For |
|---|---|---|---|
| **Tensor Parallel (TP)** | Shards weight matrices within each layer; all-reduce per layer | High (needs NVLink) | Intra-node; latency |
| **Pipeline Parallel (PP)** | Assigns contiguous layer groups to different GPUs | Low (one transfer per stage) | Cross-node; memory relief |
| **Expert Parallel (EP)** | Each GPU holds subset of MoE experts; tokens routed | Moderate (token routing) | MoE models (DeepSeek-V3, Mixtral) |
| **Data Parallel (DP)** | Full model replicas handle independent requests | Minimal | Throughput scaling |

**Hybrid parallelism is standard:** Within a node (8 GPUs, NVLink): TP=8. Across nodes (InfiniBand): PP=N_nodes. Multiple replicas: DP.

**Super-linear scaling:** TP=1 to TP=2 increased KV cache blocks by 13.9x, yielding 3.9x throughput (vs expected 2x). Mechanism: freeing weight memory enables disproportionately larger batch sizes.

### 4.2 KV Cache as Hot State

| State | Checkpointed? | Semantics |
|---|---|---|
| **KV cache (worker)** | No (ephemeral) | Crash -> KV gone; recover by re-prefill. Burns TTFT; re-bills prompt on hosted APIs |
| **KV blocks (vLLM)** | Swap/evict under pressure | Serving durability only; not cross-datacenter RPO |
| **Vendor prompt cache** | TTL-based (OpenAI 30m; Anthropic 5m/1h) | Hit != conversation durability |
| **Conversation log** | Yes -- durable at product layer | Source of truth for replay; compaction is lossy |

**Rule:** Durable agent workflows checkpoint messages, tool results, and decisions -- never assume GPU KV survives worker death.

### 4.3 Failure Taxonomy

| Class | Examples | Client Posture |
|---|---|---|
| **Transient** | 429 rate/ramp, network blips, 503 overload | Retry with exp backoff + jitter; honor Retry-After; circuit breaker |
| **Permanent** | 400 prompt too long, invalid model, schema errors | Do not retry same payload |
| **Semantic** | Hallucination, context rot, hallucinated tool args, bias/cutoff | Grounding, schema validation, eval -- not HTTP retries |
| **Poison pill** | Max_tokens OOMs a shard, adversarial mega-prompt | Isolate tenant; DLQ after N fails |
| **Idempotency** | Retries without keys double-bill input tokens | Idempotency key at product layer; cancel streams past SLA |

### 4.4 Circuit Breaker Pattern

```
     +----------+  failure rate >= threshold     +------------+
     |  CLOSED  |------------------------------->|    OPEN    |
     |  (pass)  |                                | fail-fast  |
     +----+-----+<-- success in half-open -------|+ fallback  |
          |                                      +-----+------+
          | probe after cooldown                       |
          v                                            |
     +----------+  <----------------------------------+
     |HALF-OPEN |  limited probes to primary
     +----------+  failure -> OPEN; success -> CLOSED
```

Open state routes to **fallback model chain**: (1) Primary (gpt-4.1 / Sonnet 5) -> (2) Secondary (gpt-4.1-nano / Haiku 4.5) -> (3) Deterministic (cached FAQ / rules / degraded mode).

### 4.5 Prompt Injection Attack Taxonomy

OWASP ranked prompt injection as **LLM01** in 2025 Top 10. The fundamental problem: LLMs cannot architecturally distinguish instructions from data.

| Attack Class | Mechanism | Real-World Impact |
|---|---|---|
| Direct injection | User overrides system prompt | CVEs against Copilot, Cursor, Claude Code |
| Indirect injection | Malicious instructions in retrieved docs/emails/web | 5 crafted docs manipulate 90% of responses |
| Invisible injection | White-on-white text, invisible Unicode, hidden HTML | CVE-2026-24307: single-click exfiltration from M365 Copilot |
| MCP tool poisoning | Hidden instructions in tool descriptions/schemas | Overrides agent behavior silently |
| Virtual prompt injection (VPI) | 52 poisoned training examples create backdoor | Undetectable at runtime |

**The sobering reality:** International AI Safety Report (2026): sophisticated attackers bypass safeguards **~50% of the time** with 10 attempts on best-defended models.

### 4.6 Defense-in-Depth

| Layer | Effectiveness | Limitation |
|---|---|---|
| Input/output guardrails (Llama Guard, NeMo, Prompt Guard) | 60-80% detection for known patterns | Guardrail model itself susceptible |
| LLM-as-Critic output validation | +21% detection precision | Adds latency and cost |
| Fine-tuning resistance (SecAlign, ReasAlign) | ReasAlign: 3.6% attack success | Buckles under optimization attacks |
| Architectural (CaMeL, Google DeepMind) | 77% task completion vs 84% undefended | 7-point capability trade-off |
| Least-privilege tool scopes | Limits blast radius | Doesn't prevent injection |
| Automated red-teaming | Discovers novel strategies | Ongoing effort required |

### 4.7 Training Data Extraction Risks

LLMs memorize training data. Adversaries extract it via large-volume generation + Membership Inference Attacks (MIAs).
- MIAs achieve **AUC ~0.9** against fine-tuned LLMs
- Healthcare LLMs particularly vulnerable (rare clinical sequences most extractable)
- Loss landscape poisoning (2026): adversaries poison training data to enable targeted extraction

**Defenses:** Training data deduplication (single most impactful), differential privacy, Ensemble Privacy Defense (EPD), access controls (rate-limit generation length/temperature).

### 4.8 Security Controls Summary

| Control | Mechanism |
|---|---|
| **Zero-Trust MCP** | Per-server clients; capability negotiation; origin validation; OAuth for remote servers |
| **Tool RBAC** | Least-privilege allowlists per tenant/role; schema validate args; deny-by-default |
| **PII pipeline** | detect -> redact -> audit BEFORE tokenization; never log raw PII |
| **Immutable logs** | Append-only audit: correlation_id, model version, token counts, tool name/args hash |
| **Data residency** | Anthropic US-only 1.1x; OpenAI regional/FedRAMP +10% |

---

## 5. Failure Modes

### 5.1 Hallucination Taxonomy

Base hallucination rates: **3-27%** on factual tasks even with frontier models (2025-2026 studies).

| Mode | Description | Detection |
|---|---|---|
| Entity fabrication | Invents people, papers, URLs, ArXiv IDs | KB cross-reference; URL validation |
| Fact misattribution | Assigns real facts to wrong entities | Entity-aware NLI |
| Unfaithful summary | Summary contradicts or extrapolates beyond source | NLI faithfulness vs retrieved chunks |
| Self-contradiction | Response contradicts itself across paragraphs | Intra-response consistency check |
| Ceremonialization | Says "verified" without verifying; says "tests passed" without running | Action verification in agentic loops |
| Sycophancy | Agrees with user's stated position even when wrong | Contra-factual probing |
| Context rot ("lost in the middle") | Quality degrades as context fills; middle content lost | Position-aware eval; place critical info at start/end |
| Tool argument spoofing | Invents args, IDs, or params in tool calls | Schema validation; idempotency checks |

**Production detection cascade:**
1. **Cheap heuristic** (regex, URL validation, format compliance) -- filter obvious failures
2. **Classifier-backed** (NLI model, embedding similarity) -- catch semantic errors
3. **LLM-as-judge** on borderline cases only -- expensive but highest precision

**Semantic entropy** (published in Nature): generate multiple responses, cluster semantically, measure entropy. High entropy = high hallucination probability.

### 5.2 Common Failure Modes Table

| Failure Mode | Severity | Detection Signal | Mitigation |
|---|---|---|---|
| Context rot beyond ~80K tokens | High | Accuracy drops 13.9-85% | RAG + short context; recite-then-answer |
| KV cache exhaustion (OOM) | Critical | 503 / worker crash | PagedAttention; eviction; reduce batch size |
| Tokenizer mismatch (tiktoken for Claude) | Medium | 15-20% budget undercount | Use vendor's count_tokens API |
| Retry double-billing | Medium | Cost spikes without traffic increase | Idempotency keys; cancel past SLA |
| Cascading timeout from long prefill | High | TTFT >> SLO | Deadline propagation; cacheable static prefixes |
| KV fragmentation (pre-PagedAttention) | High | Only 20-38% of reserved KV holding real tokens | PagedAttention paging |
| Noisy-neighbor continuous batching | Medium | p99 latency spikes | Bulkheads; per-tenant projects/cache pools |
| Degraded quality under load | High | Faithfulness score drops | Autoscale on queue depth + TPOT; separate batch pools |
| Prompt injection (direct/indirect) | Critical | Output deviates from policy | Layered defenses; least-privilege tools |
| Embedding model version mismatch | Critical | Silent retrieval quality collapse | Pin versions; never mix in one space |

---

## 6. System Design Scenarios

### Scenario 1: Multi-Model Routing Gateway for Enterprise AI Platform

**Problem statement.** A mid-size enterprise (5K employees) runs 12 LLM-powered applications. Current state: each team calls providers directly, spending $45K/month with no visibility, no cost controls, no failover. Three outage incidents in 6 months. CISO requires PII redaction and audit logging before SOC 2 audit.

**Proposed architecture:**

```
+--------------------------------------------------------------------+
|                    APPLICATION LAYER (12 apps)                       |
|  Support Chat | Doc Search | Code Review | Contract Analysis | ... |
+-------------------------------+------------------------------------+
                                | unified OpenAI-compat API
+-------------------------------v------------------------------------+
|                   LLM GATEWAY (Portkey / LiteLLM)                   |
|  Auth + API Key Mgr (per-team, RBAC model tiers)                   |
|  Token-Aware Rate Limiter (team budgets; hard caps)                |
|  PII Detect + Redact (Presidio; before logging and provider)       |
|  Request Logger (prompt hash, model, tokens, cost; SOC 2)          |
|  MODEL ROUTER:                                                      |
|    Rule tier:  support_chat -> Sonnet 5                              |
|                doc_search -> Haiku 3.5                                |
|                contract_analysis -> Opus 5                           |
|    Cascade:    Try Haiku first -> escalate if confidence < 0.7      |
|    Fallback:   Sonnet fails -> GPT-4o (cross-provider resilience)   |
+-------+-------------------+-------------------+--------------------+
        |                   |                   |
+-------v------+  +---------v--------+  +------v---------+
| Anthropic API |  | OpenAI API       |  | Google Gemini  |
| Circuit: 3    |  | Circuit: 3       |  | Circuit: 3     |
| fails -> OPEN |  | fails -> OPEN    |  | fails -> OPEN  |
| 30s recovery  |  | 30s recovery     |  | 30s recovery   |
+---------------+  +------------------+  +----------------+
```

**Trade-off matrix:**

| Dimension | Current (direct) | Gateway (proposed) |
|---|---|---|
| **Cost** | $45K/mo, no control | $18-25K/mo with routing + caching (40-55% savings) |
| **Latency** | Direct (~200ms TTFT) | +10-25ms proxy overhead (<15% increase) |
| **Ops** | Zero | Gateway infra (justified: 1 team for 12 apps) |
| **Security** | API keys in 12 repos; no PII controls | Centralized vault; PII redaction; per-team audit (SOC 2 unblocked) |
| **Availability** | Single-provider SPOF per app | Multi-provider failover; 99.9% achievable |

**Decision:** Gateway wins. 10-25ms overhead is negligible. Cost savings pay for ops in month 1. Routing cascade (70% to Haiku) saves $15K/month alone. Start with Portkey (open-source, governance) or LiteLLM (self-hosted, cost tracking).

### Scenario 2: Self-Hosted Inference for Regulated Financial Services

**Problem statement.** Financial services firm processes 50K confidential document analysis requests/day at $126K/month on Claude Sonnet API. Regulatory mandate: no customer financial data may leave VPC. Legal blocks all external API usage for production starting Q2.

**Proposed architecture:**

```
+------------------------------------------------------------------------+
|                    ON-PREMISES / VPC BOUNDARY                            |
|                                                                          |
|  Internal LB -> mTLS + RBAC -> Request Classifier (<1ms overhead)       |
|    simple -> Pool A: Llama 3.1 70B (FP8)                                |
|    complex -> Pool B: Llama 3.1 405B (FP8)                              |
|                                                                          |
|  Pool A: 2 nodes x 8 H100 (TP=4, PP=1, 4 replicas via DP)             |
|    Handles 70% of requests (classification, extraction, summarization)  |
|    KV: PagedAttention + INT8 quant | Spec decoding: EAGLE-3 (3.5x)     |
|    Throughput: ~320 tok/s agg | p95 TTFT: <400ms                        |
|                                                                          |
|  Pool B: 8 nodes x 8 H100 (TP=8, PP=8)                                 |
|    Handles 30% of requests (complex reasoning, multi-doc analysis)      |
|    Spec decoding: external draft (Llama 8B, 2.5x)                       |
|    Throughput: ~200 tok/s agg | p95 TTFT: <1.5s                         |
|                                                                          |
|  Shared: Prometheus+Grafana | Autoscaler (queue_depth>50 or TPOT>40ms) |
|  Model registry | Audit logger (all prompts/completions, PII-redacted) |
|  No data leaves VPC. Zero external API calls for inference.             |
+------------------------------------------------------------------------+
```

**Trade-off matrix:**

| Dimension | API (current) | Self-hosted (proposed) |
|---|---|---|
| **Cost** | $126K/mo | ~$85K/mo (GPU lease $68K + ops $17K) -- 33% savings |
| **Latency** | p95 ~500ms | Pool A <400ms; Pool B <1.5s |
| **Ops** | Zero | 2-3 FTE MLOps (justified by regulatory mandate) |
| **Compliance** | Data leaves VPC; blocked by legal | Data on-prem; full audit control |
| **Quality** | Sonnet-class | 405B FP8: ~1-2 pts below Sonnet; 70B: 3-5 pts below |
| **Scalability** | Instant burst | 2-4 week lead time for new nodes |

**Decision:** Self-hosted is the only compliant path. Two-pool architecture mirrors cascade routing on self-hosted infra. FP8 is the right starting point (~0.4 pts regression). EAGLE-3 provides 2.5-3.5x latency improvement without quality loss.

---

## Common Failure Modes (Summary Table)

| # | Failure Mode | Root Cause | Detection | Mitigation |
|---|---|---|---|---|
| 1 | Hallucination (entity fabrication) | Next-token prediction != fact verification | KB cross-reference, URL validation | RAG, grounding, schema validation |
| 2 | Context rot | Attention degradation with length | Accuracy drops on position-aware eval | Short context + RAG; recite-then-answer |
| 3 | Prompt injection | Instructions and data share attention pool | Output deviation; red-team testing | Layered defenses; least-privilege tools |
| 4 | KV cache OOM | Long sequences exhaust GPU memory | 503 errors, worker crashes | PagedAttention; KV quantization; eviction |
| 5 | Tokenizer mismatch | Using tiktoken for Claude | 15-20% budget undercount | Vendor-specific count_tokens API |
| 6 | Retry double-billing | No idempotency keys | Cost spikes without traffic | Idempotency keys; stream cancellation |
| 7 | Cascading timeouts | Long prefills dominate TTFT | TTFT >> SLO | Deadline propagation; shorter prefixes |
| 8 | Training data extraction | Memorization in weights | MIA attacks (AUC ~0.9) | Deduplication; differential privacy |

## Key Takeaways for Interviews

1. **Prefill is compute-bound (TTFT); decode is memory-bandwidth-bound (TPOT/TPS)** -- these require fundamentally different optimizations and different hardware profiles in disaggregated serving.
2. **KV cache is the central scaling challenge**: 1.3 MB/token for Llama 70B at FP16; PagedAttention reduces waste from 60-80% to <4% and enables 14-24x throughput gains.
3. **Speculative decoding is mathematically lossless** -- the draft-verify cycle produces identical output distribution to standard decoding, just faster (3-5x for EAGLE-3 on code).
4. **Context rot is real and measured**: 13.9-85% accuracy drops as input grows. More tokens != better; RAG + short context often beats long-context stuffing.
5. **Prompt injection is architectural, not just an application bug**: system instructions and user text share the same attention pool. No single defense works; layer guardrails + LLM-as-Critic + least-privilege tools.
6. **Cost optimization is multiplicative**: caching (25-40%) + routing (40-85%) + batch API (50%) compound. A tiered gateway with cascade routing can cut bills 60%+.
7. **Checkpoint conversations, not KV cache**: GPU KV is ephemeral. Recovery means re-prefill from durable prompt (burns TTFT again, re-bills tokens on hosted APIs).
8. **The overtraining paradigm has replaced Chinchilla**: smaller models trained on far more data (Llama-3 8B at 1,875 tokens/param) optimize for inference economics, not training efficiency.

## Interview Q&A

**Q1: Walk me through what happens when I send a prompt to an LLM API.**

A1: "The request hits the control plane first -- auth, rate limit checks (RPM/TPM), and model routing. Then the prompt is tokenized using the vendor's tokenizer -- and I want to stress that using tiktoken for Claude would undercount by 15-20%. The tokens enter the prefill phase, which is compute-bound and processes the entire prompt in parallel, writing K and V tensors into the KV cache. This determines TTFT. Then the decode loop generates one token at a time, reusing the KV cache -- this phase is memory-bandwidth-bound. Each token goes through temperature scaling and top-p sampling before streaming to the client. If the system uses PagedAttention, KV is managed like virtual memory pages, cutting waste from 60-80% to under 4%."

**Q2: How would you optimize cost for a workload doing 100K completions per day?**

A2: "I would stack four levers. First, prompt caching -- placing static system prompts and tool schemas as a stable prefix saves 25-40% on input tokens at 10% of normal price. Second, cascade routing -- send 70% of simple queries to a budget model like Haiku or GPT-4.1-nano and only escalate to a frontier model when confidence is low. ICLR 2025 showed 85% cost cut with 95% quality retention. Third, Batch API for non-interactive workloads at 50% off. Fourth, output optimization through structured formats. Combined, these can cut a $45K/month bill to under $20K."

**Q3: What is PagedAttention and why does it matter?**

A3: "PagedAttention treats the KV cache like OS virtual memory. Instead of allocating one contiguous block per sequence -- which wastes 60-80% of GPU memory to fragmentation -- it uses fixed-size pages with a block table mapping logical to physical positions. It supports copy-on-write for shared prefixes across requests. The result is dramatic: vLLM achieves 14-24x throughput over naive HuggingFace Transformers. For a LLaMA-13B model, a single sequence's KV can reach 1.7 GB, so this efficiency is existential for serving at scale."

**Q4: Explain the difference between prefill and decode, and why it matters for infrastructure.**

A4: "Prefill processes the entire input prompt in one parallel forward pass -- it is compute-bound and saturates GPU tensor cores. Decode generates one token at a time, reading the full KV cache from HBM each step -- it is memory-bandwidth-bound, with GPU compute largely idle. This has direct infrastructure implications: disaggregated serving puts prefill on compute-heavy GPUs and decode on bandwidth-optimized hardware. Chunked prefill breaks long prompts into 512-token chunks interleaved with decode to prevent starvation."

**Q5: How do you handle LLM provider failures in production?**

A5: "I use a three-layer approach. First, retry with exponential backoff and jitter, respecting Retry-After headers on 429s. Second, a per-provider circuit breaker -- after N consecutive failures, it trips open and fails fast for a cooldown period, then allows a single probe in half-open state. Third, a fallback model chain: primary frontier model, then a cheaper/faster alternative with the same schema contract, then a deterministic fallback for when all model planes are down. Every hop preserves correlation IDs and marks degraded=true in telemetry."

**Q6: What is context rot and how do you design around it?**

A6: "Context rot is the empirically measured degradation in accuracy and recall as the context window fills. Research shows 13.9-85% accuracy drops even with perfect retrieval, and models exhibit premature termination before exhausting the window. I design around it by preferring RAG with short, curated context over stuffing everything into a million-token window. When I must use long context, I place critical information at the beginning and end (avoiding the 'lost in the middle' effect), use recite-then-answer prompting, and implement compaction strategies."

**Q7: How do you think about the quantization ladder for self-hosted models?**

A7: "I follow NVIDIA's recommended ladder: FP8 first, which is near-lossless (only ~0.4 points MMLU regression) and the default on H100+. If I need more memory savings, INT8 SmoothQuant next (~0.7 pts). AWQ INT4 last, which gives 75% VRAM savings and 2.6-3.1x throughput but costs ~1.6 points. The critical insight is the sub-4-bit cliff: INT3 loses about 6 points on average -- it is research-grade only. For long-context serving, I combine FP8 weights with INT8 KV cache to maximize concurrent sequences."

**Q8: Walk me through the economics of prompt caching.**

A8: "Both OpenAI and Anthropic offer prompt caching. On Anthropic, a 5-minute cache write costs 1.25x the base input price, a 1-hour write costs 2x, but cache hits are only 0.1x -- so the break-even is roughly one full read for a 5-minute write, two reads for a 1-hour write. On OpenAI GPT-5.6+, writes are 1.25x and reads are 0.1x with a 30-minute default TTL. The key nuance: cached tokens still consume context window capacity and still count toward TPM on OpenAI. Caching changes price, not window occupancy."

**Q9: What is speculative decoding and when would you NOT use it?**

A9: "Speculative decoding uses a small draft model to propose K tokens, which the large target verifies in one forward pass. It is mathematically lossless -- same output distribution. I would not use it when the acceptance rate drops below 50%, which happens with creative or highly domain-specific content. Wasted verification cycles on rejected tokens can actually hurt throughput. EAGLE-3 achieves 3.5-5x speedup on code because code is highly predictable; for open-ended creative writing, I would skip it."

**Q10: How would you design an LLM gateway for a multi-team enterprise?**

A10: "I would build a centralized gateway with: (1) unified OpenAI-compatible API that all teams hit, abstracting provider differences, (2) per-team API keys with token-aware rate limits and hard monthly budget caps, (3) PII detection and redaction before any logging or provider call, (4) a model router with rule-based tier assignment (interns get Haiku, not Opus) plus cascade routing for cost optimization, (5) per-provider circuit breakers with cross-provider failover, and (6) structured audit logging with prompt hashes, costs, and model versions for SOC 2 compliance. The overhead is 10-25ms for Python proxies -- negligible against 200ms+ TTFT."

**Q11: What does it mean that KV cache is the 'hot state' in LLM inference?**

A11: "The KV cache holds the Key and Value tensors for every processed token across every layer -- it is the working memory of the decode phase. For Llama 70B at FP16, that is 1.3 MB per token. At 128K context with batch size 32, you need 5.3 TB just for KV. Critically, it is ephemeral: if a GPU worker crashes, the KV is gone. Recovery means re-prefilling from the original prompt, which costs TTFT again and re-bills prompt tokens on hosted APIs. This is why production systems checkpoint conversations and tool results at the application layer, never relying on KV durability."

**Q12: How do you defend against prompt injection in production?**

A12: "The fundamental challenge is that LLMs cannot architecturally distinguish instructions from data -- both enter the same attention pool. I layer defenses: input/output guardrails (Llama Guard, 60-80% detection), LLM-as-Critic validation (+21% precision), structured output enforcement to constrain the output space, least-privilege tool scopes to limit blast radius, and automated red-teaming to discover novel attacks. For agentic systems, I add Zero-Trust MCP with per-server capability negotiation and deny-by-default tool RBAC. The International AI Safety Report found that even best-defended models are bypassed ~50% of the time with 10 sophisticated attempts -- so defense-in-depth is mandatory."

## Key Numbers to Memorize

| Metric | Value |
|---|---|
| Self-attention complexity | O(n^2 * d) |
| KV cache per token (Llama 70B FP16) | ~1.3 MB |
| PagedAttention throughput gain | 14-24x vs naive HF |
| PagedAttention memory waste | <4% vs 60-80% naive |
| EAGLE-3 speedup (code) | 3.5-5x |
| FP8 quality loss | ~0.4 pts MMLU |
| Sub-4-bit cliff (INT3) | ~6 pts loss |
| Prompt injection bypass rate (10 attempts) | ~50% |
| Context rot accuracy drop | 13.9-85% |
| Overtraining ratio (Llama-3 8B) | 1,875 tok/param |
| Output tokens cost vs input | 5-6x more expensive |
| Cache hit price (Anthropic) | 0.1x base input |
| Price drop 2025-2026 | ~80% |

## Quick Reference

- **Tokenizer rule:** Use vendor's own tokenizer/API. Never tiktoken for Claude.
- **Cache placement:** Static prefix first, dynamic content last.
- **Sampling:** Alter temperature OR top_p, not both.
- **KV durability:** Checkpoint conversations, NOT KV cache.
- **Routing default:** Cascade small->big (Haiku first, escalate to Sonnet/Opus).
- **Quantization default:** FP8 on H100+; AWQ INT4 only if must fit on single GPU.
- **Context strategy:** RAG + short context beats long-context stuffing when evidence is sparse.
- **Rate limit ramp:** After 1M TPM, increase <=50% every 15 minutes.
- **Residency surcharge:** Anthropic US-only 1.1x; OpenAI FedRAMP +10%.
- **Fallback chain:** Primary -> secondary -> deterministic (never hard-fail to user).
