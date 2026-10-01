# 05. LLM Concepts -- Deep Dive for Principal AI Architects

**Sub-areas covered**: Transformer architecture internals (self-attention, MHA, causal masking, RoPE, FFN-as-memory), tokenization mechanics and production edge cases (BPE, SentencePiece, tiktoken), embedding spaces and retrieval models, inference serving physics (prefill vs decode, KV cache sizing, PagedAttention, prefix caching, KV quantization), continuous batching and chunked prefill trade-offs, model serving engines (vLLM, TensorRT-LLM, SGLang) with throughput benchmarks, speculative decoding variants (EAGLE-3, P-EAGLE, Medusa, DFlash) and acceptance-rate analysis, quantization ladder (FP8 -> INT8 -> AWQ INT4) with regression data, token pricing across provider tiers with cost-reduction levers (caching, batch API, volume), latency SLO design (TTFT/TPOT/TPS at p50/p95/p99), throughput capacity planning (disaggregated serving, prompt caching), parallelism strategies (TP, PP, EP, DP) and super-linear scaling, inference fault tolerance and failover taxonomy, KV cache management paradigms (PagedAttention, static sparsification, dynamic selection, LMCache), prompt injection attack taxonomy (direct, indirect, invisible, MCP tool poisoning, VPI) with defense layers (guardrails, LLM-as-Critic, SecAlign, CaMeL, least-privilege), API key management and audit logging, training data extraction risks and membership inference, hallucination taxonomy (entity fabrication, fact misattribution, ceremonialization, sycophancy, context rot, tool argument spoofing) with detection cascade, context window overflow strategies, degraded quality under load, scaling laws (Chinchilla and the overtraining paradigm), pre-training/SFT/RLHF pipeline, LoRA/QLoRA fine-tuning, RAG architecture, evaluation benchmarks (MMLU-Pro, HumanEval, SWE-bench, Chatbot Arena, LLM-as-Judge), production Python code with retries/circuit-breakers/fallback-chains/structured-logging, and two enterprise system-design scenarios (multi-model routing gateway and self-hosted inference platform) with trade-off matrices

---

## 1. System Topology & Data Flow

A production LLM inference platform spans five cooperating layers: a **request plane** handling client connections, authentication, and routing; a **serving plane** where GPU-resident models execute prefill and decode; a **memory plane** managing KV caches, model weights, and page tables; a **resilience plane** providing failover, circuit breaking, and fallback model chains; and an **observability plane** emitting per-request telemetry for cost, latency, and quality tracking.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              REQUEST PLANE                                       │
│                                                                                  │
│  ┌──────────────────┐   ┌──────────────────────┐   ┌─────────────────────────┐  │
│  │ Client App        │   │ LLM Gateway           │   │ Auth / Rate Limiter     │  │
│  │ (chat UI, agent   │──▶│ (Bifrost, LiteLLM,    │──▶│ (per-key token-aware    │  │
│  │  loop, RAG pipe)  │   │  Portkey; unified     │   │  rate limits; budget    │  │
│  │                   │   │  OpenAI-compat API)   │   │  caps per team/project) │  │
│  └──────────────────┘   └──────────┬───────────┘   └───────────┬─────────────┘  │
│                                     │ route decision            │ spend check    │
│                          ┌──────────▼───────────┐              │                │
│                          │ Model Router           │◀─────────────┘                │
│                          │ (rule / semantic /     │                               │
│                          │  learned classifier;   │                               │
│                          │  cascade: small->big)  │                               │
│                          └──────────┬───────────┘                               │
└─────────────────────────────────────┼───────────────────────────────────────────┘
                                      │ selected model + priority
┌─────────────────────────────────────▼───────────────────────────────────────────┐
│                             SERVING PLANE                                        │
│                                                                                  │
│  ┌───────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐  │
│  │ Continuous Batch   │  │ Prefill Engine         │  │ Decode Engine            │  │
│  │ Scheduler          │  │ (compute-bound;        │  │ (memory-bandwidth-bound; │  │
│  │ (iteration-level;  │──▶│ chunked prefill for   │──▶│ speculative decoding     │  │
│  │  admits/releases   │  │ fairness; process      │  │ via EAGLE-3/P-EAGLE;     │  │
│  │  per decode step)  │  │ prompt, build KV       │  │ autoregressive token     │  │
│  │                    │  │ cache, emit first tok) │  │ generation from cache)   │  │
│  └───────────────────┘  └───────────┬───────────┘  └──────────┬──────────────┘  │
│                                      │ KV write                │ KV read         │
│            ┌─────────────────────────┼─────────────────────────┘                │
│            │ Disaggregated option:   │ separate prefill/decode pools             │
│            │ on different GPU SKUs   │ (compute-heavy vs bandwidth-optimized)    │
└────────────┼─────────────────────────┼──────────────────────────────────────────┘
             │                         │
┌────────────▼─────────────────────────▼──────────────────────────────────────────┐
│                             MEMORY PLANE                                         │
│                                                                                  │
│  ┌──────────────────┐  ┌──────────────────────┐  ┌───────────────────────────┐  │
│  │ Model Weights      │  │ KV Cache Manager      │  │ Prefix Cache              │  │
│  │ (FP8/INT4 on HBM;  │  │ (PagedAttention:      │  │ (shared system prompts,   │  │
│  │  TP shards across   │  │  virtual memory       │  │  few-shot examples;       │  │
│  │  intra-node GPUs;   │  │  pages; eviction:     │  │  5-10x TTFT reduction     │  │
│  │  PP across nodes)   │  │  LRU / attn-score;    │  │  for cached prefixes;     │  │
│  │                     │  │  quantize to INT8/4)  │  │  RadixAttention in SGLang)│  │
│  └──────────────────┘  └──────────────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────────────┐
│                           RESILIENCE PLANE                                       │
│                                                                                  │
│  ┌──────────────────┐  ┌──────────────────────┐  ┌───────────────────────────┐  │
│  │ Circuit Breaker    │  │ Fallback Model Chain  │  │ Retry + Backoff           │  │
│  │ (per-provider;     │  │ (Opus -> Sonnet ->    │  │ (429: obey Retry-After;   │  │
│  │  CLOSED -> OPEN    │  │  Haiku; graceful      │  │  5xx: exp backoff + jitter│  │
│  │  -> HALF-OPEN;     │  │  degradation, not     │  │  mid-stream drop: resume  │  │
│  │  trips on 5xx      │  │  hard failure)        │  │  or retry full request)   │  │
│  │  rate or latency)  │  │                       │  │                           │  │
│  └──────────────────┘  └──────────────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────────────┐
│                        OBSERVABILITY PLANE                                       │
│                                                                                  │
│  Per-request: model, tokens_in, tokens_out, TTFT, TPOT, cost_usd, prompt_hash  │
│  Per-provider: p50/p95/p99 TTFT+TPOT, circuit-breaker state, error rate         │
│  Per-quality: hallucination rate by mode, faithfulness score, format compliance  │
│  Budget: cumulative spend per key/team/project, burn-rate alerts                │
│  Compliance: PII-redacted prompt/completion audit log, data residency routing   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A client application sends a prompt to the LLM gateway over a unified OpenAI-compatible API. (2) The gateway authenticates the API key, checks token-aware rate limits, and verifies the team's spend cap has not been exceeded. (3) The model router selects a target model: rule-based routing checks user tier and request type; semantic routing embeds the prompt and classifies complexity; the cascade pattern tries a small model first and escalates only if confidence is low. (4) The continuous batch scheduler admits the request at the next decode iteration boundary, rather than waiting for a static batch to complete -- sequences that finish early release their slots immediately. (5) The prefill engine processes the full input prompt in one compute-bound forward pass, writing Key/Value tensors into the PagedAttention-managed KV cache. If the prompt shares a prefix with cached system prompts or few-shot examples, the prefix cache provides pre-computed KV entries, reducing TTFT by 5-10x. For long prompts, chunked prefill breaks processing into 512-token chunks interleaved with decode steps, preventing starvation of concurrent requests. (6) The decode engine generates tokens autoregressively, each step reading the full KV cache from HBM (memory-bandwidth-bound). Speculative decoding (EAGLE-3 or P-EAGLE) accelerates this by proposing K candidate tokens verified in a single forward pass. (7) If the provider returns a 5xx or exceeds latency thresholds, the circuit breaker opens and the fallback chain routes to the next model tier (Opus -> Sonnet -> Haiku). Rate-limited 429s respect the Retry-After header. Mid-stream drops retry or resume the request. (8) Every request emits telemetry: model used, tokens consumed, TTFT, TPOT, cost in USD, and a prompt hash for caching and deduplication. Quality monitors track hallucination rates by failure mode. Compliance logging records PII-redacted prompt/completion pairs.

---

## 2. Core Mechanics & Algorithms

### 2.1 Transformer Architecture

All modern LLMs (GPT, Claude, Gemini, Llama) are decoder-only Transformers (Vaswani et al., 2017). Recurrence is replaced with self-attention, enabling fully parallel sequence processing.

**Per-layer computation stack:**

```
┌─────────────────────────────────────────────────────────────────┐
│ Input: x (sequence of token embeddings, shape [seq_len, d])     │
│                                                                 │
│  x_norm = LayerNorm(x)              ◀── Pre-LN (modern default)│
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Multi-Head Causal Self-Attention                          │  │
│  │                                                           │  │
│  │  Q = x_norm @ W_Q    K = x_norm @ W_K    V = x_norm @ W_V│  │
│  │                                                           │  │
│  │  scores = (Q @ K^T) / sqrt(d_k)                          │  │
│  │  scores[i,j] = -inf  where j > i   ◀── causal mask       │  │
│  │  attn = softmax(scores) @ V                               │  │
│  │                                                           │  │
│  │  h heads in parallel, each d_k = d_model / h             │  │
│  │  output = Concat(head_1, ..., head_h) @ W_O              │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  x = x + attn_output                   ◀── residual connection │
│                                                                 │
│  x_norm2 = LayerNorm(x)                                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Position-wise FFN (acts as key-value memory)              │  │
│  │  FFN(x) = GELU(x @ W_1 + b_1) @ W_2 + b_2               │  │
│  │  d_ff = 4 * d_model (original); SwiGLU in modern models   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  x = x + ffn_output                    ◀── residual connection │
│                                                                 │
│ Output: x (passed to next layer or final projection)            │
└─────────────────────────────────────────────────────────────────┘
```

**Complexity analysis.** Self-attention is O(n^2 * d) for sequence length n and model dimension d: the QK^T product is O(n^2 * d_k) per head, repeated across h heads. The FFN is O(n * d * d_ff), linear in sequence length. For long contexts (n >> d), attention dominates.

**Causal masking** is the mechanism enabling autoregressive generation. By setting scores[i,j] = -inf for j > i before softmax, each token attends only to itself and all preceding tokens. After softmax, masked positions contribute zero weight. This is not approximate -- it is an exact hard constraint.

**Positional encoding evolution.** The original sinusoidal encoding (sin/cos at geometrically spaced frequencies) enables generalization to unseen lengths but encodes absolute position. Modern models use Rotary Position Embedding (RoPE), which encodes relative position directly into the attention dot product by rotating Q and K vectors. RoPE supports context extension via NTK-aware scaling or YaRN without retraining. Learned positional embeddings (GPT-2 style) are abandoned in current architectures.

**Key invariant:** Residual connections create gradient highways. Without them, gradients vanish across 80+ layers (Llama 3.1 has 126 layers). Pre-LN (normalizing before each sub-layer) is now standard over Post-LN because it stabilizes training at depth without learning-rate warmup tricks.

### 2.2 Tokenization

Tokenizers convert text to integer sequences. Token != word.

```
┌─────────────────────────────────────────────────────────────────┐
│ Algorithm       │ Used By              │ Vocab Size │ Mechanism  │
├─────────────────┼──────────────────────┼────────────┼────────────┤
│ BPE             │ GPT-4, Claude, Llama │ 100K-200K  │ Merge most │
│                 │                      │            │ frequent   │
│                 │                      │            │ byte pairs │
├─────────────────┼──────────────────────┼────────────┼────────────┤
│ SentencePiece   │ T5, mT5              │ 32K-256K   │ Probabilis-│
│ (Unigram)       │                      │            │ tic subword│
├─────────────────┼──────────────────────┼────────────┼────────────┤
│ tiktoken        │ OpenAI models        │ 100K+      │ Optimized  │
│                 │                      │            │ BPE impl   │
└─────────────────────────────────────────────────────────────────┘
```

Heuristic: 1 token ~ 4 characters ~ 0.75 words in English. Non-English and code have worse token efficiency.

**Production-breaking edge cases:**
- Trailing whitespace changes tokenization, silently altering model behavior
- Numbers split inconsistently: "123456" may become ["123", "456"], breaking arithmetic
- Unicode homoglyphs bypass content filters while appearing identical to humans
- Right-to-left Unicode markers reverse displayed text while model processing is unchanged

### 2.3 Inference: Prefill vs Decode

LLM inference has two phases with fundamentally different hardware bottlenecks:

```
┌──────────────────────────────────────────────────────────────────────┐
│                         PREFILL PHASE                                │
│                                                                      │
│  Input: entire prompt (N tokens)                                     │
│  Operation: one forward pass, all N tokens in parallel               │
│  Bottleneck: COMPUTE-BOUND (saturates GPU tensor cores)              │
│  Output: KV cache for all N tokens + first output token              │
│  Metric: TTFT (Time to First Token)                                  │
│  Typical: ~50ms for 4K tokens on H100                                │
│                                                                      │
│  Optimization: chunked prefill (512-tok chunks interleaved with      │
│  decode) prevents long-prompt starvation of concurrent requests      │
├──────────────────────────────────────────────────────────────────────┤
│                         DECODE PHASE                                 │
│                                                                      │
│  Input: last generated token + full KV cache from all prior tokens   │
│  Operation: one token per forward pass, sequential                   │
│  Bottleneck: MEMORY-BANDWIDTH-BOUND (GPU compute idles waiting       │
│              for HBM reads of KV cache + model weights)              │
│  Output: next token                                                  │
│  Metric: TPOT (Time Per Output Token) / TPS (Tokens Per Second)      │
│  Typical: 10-20ms/token for 70B model on H100                        │
│                                                                      │
│  Optimization: speculative decoding (draft model proposes K tokens,  │
│  target verifies all K in one pass -- mathematically lossless)       │
└──────────────────────────────────────────────────────────────────────┘
```

**KV cache sizing formula:**

```
KV_cache_per_token = 2 * num_layers * (num_heads * dim_head) * precision_bytes

Llama 3.1 70B @ FP16:
  = 2 * 80 * (64 * 128) * 2 bytes
  = ~1.3 MB per token per sequence

At 128K context, batch size 32:
  = 1.3 MB * 128K * 32 = ~5.3 TB
```

This is why KV cache management is the central scaling challenge for long-context serving.

**KV cache management strategies:**

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Strategy             │ Mechanism                       │ Impact            │
├──────────────────────┼─────────────────────────────────┼───────────────────┤
│ PagedAttention       │ Virtual-memory-style paging,    │ 14-24x throughput │
│ (vLLM)               │ eliminates fragmentation        │ vs naive HF       │
├──────────────────────┼─────────────────────────────────┼───────────────────┤
│ Prefix caching       │ Reuse KV across requests with   │ 5-10x TTFT for   │
│                      │ shared prefixes (system prompt)  │ cached prefixes   │
├──────────────────────┼─────────────────────────────────┼───────────────────┤
│ KV quantization      │ INT8: near-lossless; INT4:      │ 2x more concurrent│
│                      │ small quality loss               │ sequences         │
├──────────────────────┼─────────────────────────────────┼───────────────────┤
│ NVFP4                │ 4-bit KV cache on-device         │ 3x TTFT via      │
│                      │                                  │ reduced evictions │
└────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Speculative Decoding

A small, fast draft model proposes K candidate tokens. The large target model verifies all K in a single forward pass. Accepted tokens are free; rejected tokens fall back to standard sampling. Output quality is mathematically identical to standard decoding -- this is not an approximation.

**Acceptance rate (alpha) determines real-world speedup:**
- alpha 0.75-0.85 (code, structured data): 3-5x speedup
- alpha 0.60-0.80 (general queries): 2-3x speedup
- alpha < 0.50 (creative, domain-specific): can hurt performance -- wasted cycles on rejected tokens

**Production variants (2025-2026):**

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Variant          │ Mechanism                     │ Speedup │ Trade-off    │
├──────────────────┼───────────────────────────────┼─────────┼──────────────┤
│ External draft   │ Separate smaller model        │ 2-3x    │ +2-6 GB VRAM│
│ (e.g. 8B for 70B)│ (Llama-3.1-8B for 70B)       │         │              │
├──────────────────┼───────────────────────────────┼─────────┼──────────────┤
│ EAGLE-3          │ Learned prediction head on    │ 3.5-5x  │ ~4.8x on     │
│                  │ target model                   │ (code)  │ HumanEval    │
├──────────────────┼───────────────────────────────┼─────────┼──────────────┤
│ P-EAGLE          │ Parallel drafting (all K      │ 4-5x    │ 20-30% over  │
│                  │ tokens in one pass)            │         │ EAGLE-3      │
├──────────────────┼───────────────────────────────┼─────────┼──────────────┤
│ Medusa           │ Multiple prediction heads     │ 2-3x    │ No separate  │
│                  │ on base model                  │         │ draft model  │
├──────────────────┼───────────────────────────────┼─────────┼──────────────┤
│ DFlash           │ Block diffusion speculative   │ 6x      │ Emerging,    │
│                  │ decoding                       │ claimed │ not proven   │
└───────────────────────────────────────────────────────────────────────────┘
```

### 2.5 Quantization

Reducing precision to shrink memory footprint and increase throughput.

**Decision ladder** (NVIDIA-recommended): FP8 first -> INT8 SmoothQuant -> AWQ/GPTQ INT4.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Format          │ Quality Loss    │ VRAM     │ Throughput │ When to Use     │
│                 │ (vs FP16, 70B)  │ Savings  │ Lift       │                 │
├─────────────────┼─────────────────┼──────────┼────────────┼─────────────────┤
│ FP8 (E4M3)     │ ~0.4 pts MMLU   │ ~50%     │ 1.4-1.7x   │ Default for     │
│                 │                 │          │            │ H100+; lossless │
├─────────────────┼─────────────────┼──────────┼────────────┼─────────────────┤
│ INT8 SmoothQuant│ ~0.7 pts        │ ~50%     │ ~1.5x      │ Mature fallback │
├─────────────────┼─────────────────┼──────────┼────────────┼─────────────────┤
│ AWQ INT4        │ ~1.6 pts        │ ~75%     │ 2.6-3.1x   │ Must fit 70B on │
│                 │                 │          │            │ single GPU      │
├─────────────────┼─────────────────┼──────────┼────────────┼─────────────────┤
│ GPTQ INT4       │ ~1.5-2.4 pts    │ ~75%     │ 2.6-3.1x   │ Wider variance; │
│                 │                 │          │            │ use AWQ instead │
└─────────────────────────────────────────────────────────────────────────────┘
```

**The sub-4-bit cliff:** INT3 loses ~6 pts on average. Sub-4-bit is research-grade for production.

### 2.6 Scaling Laws and Training Pipeline

**The Chinchilla correction and the overtraining paradigm.** Chinchilla (DeepMind, 2022) established compute-optimal training at ~20 tokens per parameter. But compute-optimal minimizes training FLOP, not deployment cost. Inference dominates lifetime cost, so modern practice trains smaller models on far more data ("overtraining").

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Model               │ Params │ Tokens  │ Tok/Param │ vs Chinchilla 20:1 │
├─────────────────────┼────────┼─────────┼───────────┼────────────────────┤
│ GPT-3               │ 175B   │ 300B    │ 1.7       │ 12x undertrained   │
│ Llama-2 70B         │ 70B    │ ~2T     │ ~28       │ ~Chinchilla optimal│
│ Llama-3 8B          │ 8B     │ 15T     │ 1,875     │ 94x overtrained    │
│ Qwen3-0.6B          │ 0.6B   │ 36T     │ 60,000    │ 3,000x overtrained │
│ Liquid LFM2.5-350M  │ 350M   │ 28T     │ 80,000    │ 4,000x overtrained │
└──────────────────────────────────────────────────────────────────────────┘
```

**Takeaway:** The right model is smaller and trained longer than Chinchilla suggests. Overtrain for inference economics.

**Training pipeline state machine:**

```
┌──────────────┐     ┌───────────────────┐     ┌───────────────────────┐
│ Pre-training  │────▶│ Instruction       │────▶│ RLHF / RLAIF         │
│               │     │ Fine-tuning (SFT) │     │ Alignment             │
│ Next-token    │     │                   │     │                       │
│ prediction on │     │ Curated           │     │ Human/AI ranks        │
│ trillions of  │     │ instruction-      │     │ responses -> reward   │
│ tokens.       │     │ response pairs.   │     │ model -> policy       │
│               │     │ Model learns to   │     │ optimization.         │
│ Produces base │     │ follow structured │     │ Teaches helpfulness,  │
│ model that    │     │ instructions.     │     │ safety, clarity.      │
│ predicts text │     │                   │     │                       │
│ but is not a  │     │                   │     │ Reasoning models add  │
│ useful        │     │                   │     │ chain-of-thought via  │
│ assistant.    │     │                   │     │ training, not prompt. │
└──────────────┘     └───────────────────┘     └───────────────────────┘
```

**Parameter-efficient fine-tuning (PEFT):**
- **LoRA**: Adds small rank-decomposition matrices to attention layers. Trains <1% of parameters. Standard for domain adaptation.
- **QLoRA**: LoRA on a 4-bit quantized base model. Enables fine-tuning 70B models on a single GPU.

### 2.7 RAG (Retrieval-Augmented Generation)

Three-step architecture: Retrieve (search documents via embedding similarity) -> Augment (inject retrieved chunks into prompt) -> Generate (LLM answers from evidence).

**RAG vs fine-tuning decision:** RAG when knowledge changes frequently, source attribution is required, or you cannot afford retraining. Fine-tuning when you need to change the model's behavior/style or teach domain-specific reasoning patterns.

**RAG failure surface:** The model can ignore, misread, or extrapolate beyond retrieved chunks. Faithfulness evaluation (NLI-based scoring) is mandatory in production.

### 2.8 Hallucination Taxonomy

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Mode                    │ Description                │ Detection          │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Entity fabrication      │ Invents people, papers,    │ KB cross-reference;│
│                         │ URLs, ArXiv IDs            │ URL validation     │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Fact misattribution     │ Assigns real facts to      │ Entity-aware NLI   │
│                         │ wrong entities             │                    │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Unfaithful summary      │ Summary contradicts or     │ NLI faithfulness   │
│                         │ extrapolates beyond source │ vs retrieved chunks│
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Self-contradiction      │ Response contradicts       │ Intra-response     │
│                         │ itself across paragraphs   │ consistency check  │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Ceremonialization       │ Says "verified" without    │ Action verification│
│ (instruction attenuation│ verifying; says "tests     │ in agentic loops   │
│                         │ passed" without running    │                    │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Sycophancy              │ Agrees with user's stated  │ Contra-factual     │
│                         │ position even when wrong   │ probing            │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Context rot             │ Quality degrades as context│ Position-aware     │
│ ("lost in the middle")  │ fills; middle content lost │ eval; place crit.  │
│                         │                            │ info at start/end  │
├─────────────────────────┼────────────────────────────┼────────────────────┤
│ Tool argument spoofing  │ Invents args, IDs, or      │ Schema validation; │
│                         │ params in tool calls       │ idempotency checks │
└───────────────────────────────────────────────────────────────────────────┘
```

Base hallucination rates: 3-27% on factual tasks even with frontier models (2025-2026 studies).

**Production detection cascade (recommended):**
1. Cheap heuristic (regex, URL validation, format compliance) -- filter obvious failures
2. Classifier-backed (NLI model, embedding similarity) -- catch semantic errors
3. LLM-as-judge on borderline cases only -- expensive but highest precision

**Semantic entropy** (published in Nature): generate multiple responses, cluster semantically, measure entropy. High entropy = high hallucination probability.

---

## 3. Token Economics & NFR Analysis

### 3.1 Provider Pricing (September 2026)

Prices dropped ~80% between early 2025 and early 2026. Output tokens cost 5-6x input tokens -- verbose responses, not long prompts, dominate most bills.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                          FLAGSHIP TIER ($/1M tokens)                       │
├──────────────────────────┬────────────┬─────────────┬──────────────────────┤
│ Model                    │ Input      │ Output      │ Notes                │
├──────────────────────────┼────────────┼─────────────┼──────────────────────┤
│ OpenAI GPT-5.6 Sol       │ $5.00      │ $30.00      │                      │
│ Anthropic Claude Opus 5  │ $5.00      │ $25.00      │ Cheaper output       │
│ OpenAI o3 (reasoning)    │ $10-15     │ $40-60      │ Reasoning tokens add │
│                          │            │             │ significant cost     │
├──────────────────────────┴────────────┴─────────────┴──────────────────────┤
│                         PRODUCTION TIER ($/1M tokens)                      │
├──────────────────────────┬────────────┬─────────────┬──────────────────────┤
│ Anthropic Sonnet 5       │ $2.00      │ $10.00      │                      │
│ Anthropic Sonnet 4.6     │ $3.00      │ $15.00      │                      │
│ OpenAI GPT-4o            │ $2.50      │ $10.00      │                      │
│ Google Gemini 3.1 Pro    │ $2.00      │ $12.00      │                      │
├──────────────────────────┴────────────┴─────────────┴──────────────────────┤
│                           BUDGET TIER ($/1M tokens)                        │
├──────────────────────────┬────────────┬─────────────┬──────────────────────┤
│ OpenAI GPT-5.6 Luna      │ $0.20      │ $1.20       │                      │
│ OpenAI GPT-4.1 Nano      │ $0.10      │ $0.40       │                      │
│ Google Gemini 2.0 Flash  │ $0.10      │ $0.40       │                      │
│ Anthropic Haiku 3.5      │ $0.80      │ $4.00       │                      │
└──────────────────────────┴────────────┴─────────────┴──────────────────────┘
```

### 3.2 Cost Formula: $ Per 1K Runs

For a workload with avg_input tokens in and avg_output tokens out per request:

```
cost_per_1k_runs = 1000 * (
    (avg_input / 1_000_000) * input_price_per_M +
    (avg_output / 1_000_000) * output_price_per_M
)
```

**Worked example -- customer support chatbot, 1K conversations/day:**

```
Avg input: 2,000 tokens (system prompt + history + user query)
Avg output: 500 tokens
Model: Claude Sonnet 5 ($2/M in, $10/M out)

Per conversation: (2000/1M * $2) + (500/1M * $10) = $0.004 + $0.005 = $0.009
Per 1K runs:  $9.00
Per day:      $9.00
Per month:    ~$270

With prompt caching (system prompt = 1500 tokens cached at 10% price):
  Cached:   1500/1M * $0.20 = $0.0003
  Uncached:  500/1M * $2.00 = $0.001
  Output:    500/1M * $10   = $0.005
  Per conv:  $0.0063
  Monthly:   ~$189  (30% savings)

With cascade routing (70% handled by Haiku 3.5, 30% escalated to Sonnet 5):
  Haiku:   0.70 * [(2000/1M * $0.80) + (500/1M * $4.00)] = $0.70 * $0.0036 = $0.0025
  Sonnet:  0.30 * $0.009                                   = $0.0027
  Per conv: $0.0052
  Monthly:  ~$156  (42% savings)

Combined (caching + routing): ~$109/month  (60% savings)
```

### 3.3 Cost Reduction Levers

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Lever               │ Savings    │ Mechanism                              │
├─────────────────────┼────────────┼────────────────────────────────────────┤
│ Prompt caching      │ 25-40%     │ Cached tokens at 10-25% of input      │
│                     │            │ price (Anthropic and OpenAI)           │
├─────────────────────┼────────────┼────────────────────────────────────────┤
│ Batch API           │ 50%        │ Async processing, both providers       │
├─────────────────────┼────────────┼────────────────────────────────────────┤
│ Model routing       │ 40-85%     │ ICLR 2025: 85% cost cut, 95% quality  │
│                     │            │ retention, sending 14% to strong model │
├─────────────────────┼────────────┼────────────────────────────────────────┤
│ Volume commits      │ 20-50%     │ Common above $5-10K/month spend        │
├─────────────────────┼────────────┼────────────────────────────────────────┤
│ Output optimization │ 10-30%     │ Shorter outputs via system prompts,    │
│                     │            │ structured output formats              │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Latency SLO Targets

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Workload             │ p50 TTFT  │ p95 TTFT  │ p99 TTFT  │ p95 TPS      │
├──────────────────────┼───────────┼───────────┼───────────┼──────────────┤
│ Conversational chat  │ <200ms    │ <500ms    │ <1s       │ >30 tok/s    │
│ Code completion      │ <80ms     │ <150ms    │ <200ms    │ >50 tok/s    │
│ Document analysis    │ <1s       │ <3s       │ <5s       │ >20 tok/s    │
│ Batch processing     │ relaxed   │ relaxed   │ relaxed   │ maximize     │
│                      │           │           │           │ throughput   │
│ Agentic (multi-call) │ <300ms    │ <800ms    │ <1.5s     │ >40 tok/s    │
│ (per LLM call)       │           │           │           │              │
└────────────────────────────────────────────────────────────────────────────┘
```

**Tail latency matters more than averages.** A p50 of 100ms with a p99 of 5s means 1 in 100 users waits 50x longer. Monitor and SLO on p95/p99.

### 3.5 Throughput Capacity Planning

**Primary levers:**

1. **Continuous batching**: admits/releases requests per decode step. Larger batch size = higher aggregate TPS, but higher per-request latency. Tune batch size to hit latency SLO at maximum throughput.

2. **Disaggregated serving**: separate prefill and decode onto different hardware. Prefill on compute-heavy GPUs (H100); decode on bandwidth-optimized hardware. Eliminates phase interference.

3. **Speculative decoding**: 2-5x per-request latency reduction without quality loss.

4. **Prompt caching**: eliminates redundant prefill for shared prefixes. 5-10x TTFT reduction.

**Capacity estimation example:**

```
Target: 100 concurrent users, Sonnet-class (70B) model, self-hosted

Per-request: ~300ms TTFT + 500 output tokens * 25ms TPOT = ~12.8s total
Single H100 (TP=1): ~40 TPS decode throughput with continuous batching

Sustained throughput needed: 100 users * 500 tokens / 12.8s = ~3,900 tok/s
GPUs required (decode-limited): 3,900 / 40 = ~98 GPUs -> 13 nodes (8 GPUs each)

With TP=4 (4 GPUs per model instance, 2 instances per node):
  Per-instance TPS: ~80 tok/s (super-linear scaling from KV cache relief)
  Total: 13 nodes * 2 instances * 80 = ~2,080 tok/s -- still short

Revised: add speculative decoding (2.5x), prefix caching (30% hit rate):
  Effective: 2,080 * 2.5 * 1.3 = ~6,760 tok/s -- sufficient with margin
```

### 3.6 NFR Requirements

```
┌────────────────────────────────────────────────────────────────────────────┐
│ NFR                │ Target                 │ Implementation               │
├────────────────────┼────────────────────────┼──────────────────────────────┤
│ Availability       │ 99.9% (8.7h/yr down)   │ Multi-provider failover;     │
│                    │                        │ circuit breakers; health     │
│                    │                        │ checks on VRAM/queue/latency │
├────────────────────┼────────────────────────┼──────────────────────────────┤
│ RPO                │ 0 (stateless inference)│ Prompts are idempotent;      │
│                    │                        │ conversation state in app DB │
├────────────────────┼────────────────────────┼──────────────────────────────┤
│ RTO                │ <30s (failover to      │ Pre-warmed standby models;   │
│                    │ backup model/provider) │ health probe interval <10s   │
├────────────────────┼────────────────────────┼──────────────────────────────┤
│ Compliance         │ SOC 2, GDPR, EU AI Act │ PII redaction before model;  │
│ (by Aug 2026)      │                        │ audit logs; data residency   │
│                    │                        │ routing; adversarial testing │
├────────────────────┼────────────────────────┼──────────────────────────────┤
│ Scalability        │ 10x burst capacity     │ Autoscale on queue depth +   │
│                    │                        │ TPOT, not request count      │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Model Parallelism Strategies

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Strategy   │ Mechanism                        │ Comms Cost │ Best For      │
├────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Tensor     │ Shards weight matrices within    │ High       │ Intra-node    │
│ Parallel   │ each layer across GPUs;          │ (needs     │ (NVLink);     │
│ (TP)       │ all-reduce/all-gather per layer  │ NVLink)    │ latency       │
├────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Pipeline   │ Assigns contiguous layer groups  │ Low (one   │ Cross-node    │
│ Parallel   │ to different GPUs; activations   │ transfer   │ (InfiniBand); │
│ (PP)       │ passed between stages            │ per stage) │ memory relief │
├────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Expert     │ Each GPU holds subset of MoE     │ Moderate   │ MoE models    │
│ Parallel   │ experts; tokens routed between   │ (token     │ (DeepSeek-V3, │
│ (EP)       │ GPUs based on expert assignment  │ routing)   │ Mixtral)      │
├────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Data       │ Full model replicas handle       │ Minimal    │ Throughput    │
│ Parallel   │ independent requests             │ (none      │ scaling       │
│ (DP)       │                                  │ inter-rep) │               │
└────────────────────────────────────────────────────────────────────────────┘
```

**Hybrid parallelism is standard in production:**
- Within a node (8 GPUs, NVLink): TP=8
- Across nodes (InfiniBand): PP=N_nodes
- Multiple replicas: DP for throughput scaling
- Example: 2 nodes x 8 GPUs -> TP=8, PP=2

**Super-linear scaling effect:** With TP=1 to TP=2, vLLM observed 13.9x increase in KV cache blocks, yielding 3.9x throughput gain (vs expected 2x). Mechanism: freeing weight memory from GPU HBM enables disproportionately larger batch sizes.

### 4.2 Inference Fault Tolerance

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Failure Class               │ Response Pattern                            │
├─────────────────────────────┼─────────────────────────────────────────────┤
│ Hard 5xx from provider      │ Immediate fallback to backup model/provider │
├─────────────────────────────┼─────────────────────────────────────────────┤
│ 429 rate limit              │ Back off per Retry-After header, queue      │
├─────────────────────────────┼─────────────────────────────────────────────┤
│ Rising latency (no error)   │ Proactively shift new requests to faster    │
│                             │ provider                                    │
├─────────────────────────────┼─────────────────────────────────────────────┤
│ Partial streaming failure   │ Resume from last token or retry full request│
│ (mid-response drop)         │ -- never hard fail                          │
├─────────────────────────────┼─────────────────────────────────────────────┤
│ GPU OOM during inference    │ Reduce batch size, evict KV cache entries,  │
│                             │ or redirect to another instance             │
└────────────────────────────────────────────────────────────────────────────┘
```

**Self-hosted resilience patterns:**
- Health checks monitoring VRAM utilization, queue depth, and response latency
- Graceful degradation: shed load by reducing max concurrent requests before OOM
- KV cache eviction policies: LRU, attention-score-based, or offload to CPU/SSD
- Autoscaling on queue depth + TPOT, not just request count

### 4.3 Prompt Injection Attack Taxonomy

OWASP ranked prompt injection as LLM01 in its 2025 Top 10. The fundamental problem: LLMs cannot architecturally distinguish instructions from data.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Attack Class         │ Mechanism                    │ Real-World Impact    │
├──────────────────────┼──────────────────────────────┼──────────────────────┤
│ Direct injection     │ User crafts input to         │ CVEs against Copilot,│
│                      │ override system prompt       │ Cursor, Claude Code  │
├──────────────────────┼──────────────────────────────┼──────────────────────┤
│ Indirect injection   │ Malicious instructions in    │ RAG poisoning: 5     │
│                      │ retrieved docs, emails, web  │ crafted docs         │
│                      │                              │ manipulate 90% of    │
│                      │                              │ responses            │
├──────────────────────┼──────────────────────────────┼──────────────────────┤
│ Invisible injection  │ White-on-white text,         │ CVE-2026-24307:      │
│                      │ invisible Unicode,           │ single-click data    │
│                      │ hidden HTML                  │ exfiltration from    │
│                      │                              │ M365 Copilot         │
├──────────────────────┼──────────────────────────────┼──────────────────────┤
│ MCP tool poisoning   │ Hidden instructions in tool  │ Overrides agent      │
│                      │ descriptions/schemas         │ behavior silently    │
├──────────────────────┼──────────────────────────────┼──────────────────────┤
│ Virtual prompt       │ 52 poisoned training examples│ Undetectable at      │
│ injection (VPI)      │ create backdoor              │ runtime; only        │
│                      │                              │ training data        │
│                      │                              │ curation helps       │
└────────────────────────────────────────────────────────────────────────────┘
```

**The sobering reality:** The International AI Safety Report (2026) found sophisticated attackers bypass safeguards ~50% of the time with 10 attempts on best-defended models. Current defenses are speed bumps, not walls.

### 4.4 Defense-in-Depth

No single defense is sufficient. Layer them:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Layer                     │ Effectiveness      │ Limitation              │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ Input/output guardrails   │ 60-80% detection   │ Guardrail model itself  │
│ (Llama Guard, NeMo,       │ for known patterns │ susceptible to injection│
│  Prompt Guard)            │                    │                         │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ LLM-as-Critic output      │ +21% detection     │ Adds latency and cost   │
│ validation                │ precision over     │                         │
│                           │ input-only         │                         │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ Fine-tuning for resistance│ ReasAlign: 3.6%    │ Buckles under           │
│ (SecAlign, ReasAlign)     │ attack success rate│ optimization attacks    │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ Architectural (CaMeL,     │ 77% task completion│ 7-point capability      │
│ Google DeepMind)          │ vs 84% undefended  │ trade-off               │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ Least-privilege tool      │ Limits blast radius│ Does not prevent the    │
│ scopes                    │                    │ injection itself        │
├───────────────────────────┼────────────────────┼─────────────────────────┤
│ Automated red-teaming     │ Discovers novel    │ Ongoing effort required │
│ (RL-trained adversarial)  │ attack strategies  │                         │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.5 API Key Management and Audit

Production patterns for enterprise LLM access control:

- **API key rotation** with short-lived tokens; never embed in client-side code
- **Per-user/per-team rate limiting** at the gateway -- token-aware, not just request-count
- **Budget enforcement**: hard spend caps per API key/team/project
- **Audit logging**: every prompt and completion logged with PII redaction for SOC 2 / GDPR compliance
- **Model access tiers**: different users authorized for different model tiers (interns get Haiku, not Opus)
- **Data residency routing**: requests routed to specific providers/regions based on compliance requirements

### 4.6 Training Data Extraction and Privacy

LLMs memorize training data. Adversaries extract it via large-volume generation + Membership Inference Attacks (MIAs).

**Key findings (2025-2026):**
- MIAs achieve AUC ~0.9 against fine-tuned LLMs using self-prompted calibration
- Healthcare LLMs are particularly vulnerable -- rare clinical sequences are most extractable
- Loss landscape poisoning (2026): adversaries can poison training data to enable targeted extraction of unseen data

**Defenses:**
- **Training data deduplication**: single most impactful measure for reducing extraction risk
- **Differential privacy**: calibrated noise during training; trade-off with model quality
- **Ensemble Privacy Defense (EPD)**: aggregates outputs of knowledge-injected LLM, base LLM, and judge model
- **Access controls**: rate-limit generation length and temperature to reduce extraction surface

**Regulatory context:** EU AI Act (full enforcement August 2026) and NIST AI 600-1 both require demonstrable adversarial robustness for high-risk systems.

---

## 5. Production Enterprise Code

### 5.1 Resilient LLM Client with Retry, Circuit Breaker, and Fallback Chain

```python
"""
Production LLM client with:
  - Exponential backoff + jitter on retries
  - Per-provider circuit breaker (CLOSED -> OPEN -> HALF-OPEN)
  - Fallback model chain (Opus -> Sonnet -> Haiku)
  - Structured JSON logging with cost tracking
  - Graceful degradation (never hard-fail to user)
"""

import time
import random
import logging
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import anthropic


# ── Structured Logging ────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        return json.dumps(log_entry, default=str)


logger = logging.getLogger("llm_client")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)


def log_with_extra(level: str, msg: str, **kwargs):
    record = logger.makeRecord(
        logger.name, getattr(logging, level.upper()), "", 0, msg, (), None
    )
    record.extra = kwargs
    logger.handle(record)


# ── Circuit Breaker ───────────────────────────────────────────────────────

class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Per-provider circuit breaker.
    Trips OPEN after `failure_threshold` consecutive failures.
    Transitions to HALF_OPEN after `recovery_timeout` seconds.
    Resets to CLOSED on first success in HALF_OPEN.
    """
    provider: str
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    state: CBState = CBState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def record_success(self):
        self.failure_count = 0
        if self.state == CBState.HALF_OPEN:
            log_with_extra("info", "circuit_breaker_reset",
                           provider=self.provider, new_state="closed")
        self.state = CBState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CBState.OPEN
            log_with_extra("warning", "circuit_breaker_tripped",
                           provider=self.provider, failures=self.failure_count)

    def allow_request(self) -> bool:
        if self.state == CBState.CLOSED:
            return True
        if self.state == CBState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CBState.HALF_OPEN
                log_with_extra("info", "circuit_breaker_half_open",
                               provider=self.provider, elapsed_s=round(elapsed, 1))
                return True
            return False
        # HALF_OPEN: allow one probe request
        return True


# ── Retry with Exponential Backoff + Jitter ───────────────────────────────

RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 529}  # 529 = Anthropic overloaded


def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_range: float = 0.5,
):
    """
    Retry with exponential backoff + random jitter.
    Respects Retry-After header on 429s.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except anthropic.RateLimitError as e:
            last_exception = e
            retry_after = getattr(e.response, "headers", {}).get("retry-after")
            if retry_after:
                delay = float(retry_after)
            else:
                delay = min(base_delay * (2 ** attempt), max_delay)
            delay += random.uniform(0, jitter_range)
            log_with_extra("warning", "rate_limited_retry",
                           attempt=attempt + 1, delay_s=round(delay, 2),
                           retry_after=retry_after)
            time.sleep(delay)
        except anthropic.APIStatusError as e:
            last_exception = e
            if e.status_code not in RETRIABLE_STATUS_CODES:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            delay += random.uniform(0, jitter_range)
            log_with_extra("warning", "retriable_error",
                           attempt=attempt + 1, status=e.status_code,
                           delay_s=round(delay, 2))
            time.sleep(delay)
        except anthropic.APIConnectionError as e:
            last_exception = e
            delay = min(base_delay * (2 ** attempt), max_delay)
            delay += random.uniform(0, jitter_range)
            log_with_extra("warning", "connection_error_retry",
                           attempt=attempt + 1, delay_s=round(delay, 2))
            time.sleep(delay)
    raise last_exception


# ── Fallback Model Chain ──────────────────────────────────────────────────

@dataclass
class ModelTier:
    model: str
    input_price_per_m: float   # $/1M input tokens
    output_price_per_m: float  # $/1M output tokens
    max_retries: int = 3


# Ordered from strongest to weakest -- degrade gracefully
FALLBACK_CHAIN = [
    ModelTier("claude-sonnet-4-5-20250514", 3.0, 15.0),
    ModelTier("claude-haiku-4-5-20250514", 0.80, 4.0, max_retries=2),
]


@dataclass
class LLMResponse:
    content: str
    model_used: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    ttft_ms: float
    total_ms: float
    fallback_used: bool


@dataclass
class ResilientLLMClient:
    """
    Production client that tries each model in the fallback chain,
    respecting per-provider circuit breakers.
    Never hard-fails -- degrades through the chain.
    """
    client: anthropic.Anthropic = field(default_factory=anthropic.Anthropic)
    circuit_breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def _get_cb(self, model: str) -> CircuitBreaker:
        if model not in self.circuit_breakers:
            self.circuit_breakers[model] = CircuitBreaker(provider=model)
        return self.circuit_breakers[model]

    def complete(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """
        Attempt completion through the fallback chain.
        Returns the first successful response.
        Raises only if ALL models in the chain fail.
        """
        last_error = None
        for i, tier in enumerate(FALLBACK_CHAIN):
            cb = self._get_cb(tier.model)
            if not cb.allow_request():
                log_with_extra("info", "circuit_open_skip", model=tier.model)
                continue

            try:
                t_start = time.monotonic()

                def _call():
                    kwargs = {
                        "model": tier.model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    if system:
                        kwargs["system"] = system
                    return self.client.messages.create(**kwargs)

                response = retry_with_backoff(_call, max_retries=tier.max_retries)
                t_end = time.monotonic()
                total_ms = (t_end - t_start) * 1000

                cb.record_success()

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cost_usd = (
                    (input_tokens / 1_000_000) * tier.input_price_per_m
                    + (output_tokens / 1_000_000) * tier.output_price_per_m
                )

                content = response.content[0].text if response.content else ""
                result = LLMResponse(
                    content=content,
                    model_used=tier.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    ttft_ms=0.0,  # non-streaming; use stream for real TTFT
                    total_ms=round(total_ms, 1),
                    fallback_used=(i > 0),
                )

                log_with_extra("info", "llm_completion",
                               model=tier.model,
                               input_tokens=input_tokens,
                               output_tokens=output_tokens,
                               cost_usd=round(cost_usd, 6),
                               total_ms=round(total_ms, 1),
                               fallback_used=(i > 0))
                return result

            except Exception as e:
                cb.record_failure()
                last_error = e
                log_with_extra("error", "model_failed_fallback",
                               model=tier.model,
                               error=str(e),
                               next_model=FALLBACK_CHAIN[i + 1].model
                               if i + 1 < len(FALLBACK_CHAIN) else "none")

        # All models exhausted -- this is the only hard failure path
        log_with_extra("error", "all_models_exhausted",
                       chain=[t.model for t in FALLBACK_CHAIN])
        raise RuntimeError(
            f"All models in fallback chain exhausted. Last error: {last_error}"
        )


# ── Usage ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client = ResilientLLMClient()
    resp = client.complete(
        messages=[{"role": "user", "content": "Explain KV cache in 3 sentences."}],
        system="You are a concise ML systems expert.",
        max_tokens=256,
    )
    print(f"Model: {resp.model_used}")
    print(f"Cost: ${resp.cost_usd:.6f}")
    print(f"Latency: {resp.total_ms:.0f}ms")
    print(f"Fallback: {resp.fallback_used}")
    print(f"\n{resp.content}")
```

### 5.2 Streaming Client with Real TTFT Measurement

```python
"""
Streaming LLM client that measures actual TTFT (time to first token).
Production use: when you need sub-second perceived latency.
"""

import time
import anthropic


def stream_with_ttft(
    client: anthropic.Anthropic,
    model: str,
    messages: list[dict],
    system: str = "",
    max_tokens: int = 1024,
) -> dict:
    """
    Stream a completion, measuring real TTFT and collecting full response.
    Returns dict with content, ttft_ms, total_ms, tokens.
    """
    t_start = time.monotonic()
    ttft_recorded = False
    ttft_ms = 0.0
    chunks = []
    input_tokens = 0
    output_tokens = 0

    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if system:
        kwargs["system"] = system

    with client.messages.stream(**{k: v for k, v in kwargs.items()
                                   if k != "stream"}) as stream:
        for event in stream:
            if hasattr(event, "type"):
                if event.type == "content_block_delta" and not ttft_recorded:
                    ttft_ms = (time.monotonic() - t_start) * 1000
                    ttft_recorded = True
                if event.type == "content_block_delta":
                    chunks.append(event.delta.text)

        final_message = stream.get_final_message()
        input_tokens = final_message.usage.input_tokens
        output_tokens = final_message.usage.output_tokens

    total_ms = (time.monotonic() - t_start) * 1000
    tpot_ms = (total_ms - ttft_ms) / max(output_tokens, 1)

    return {
        "content": "".join(chunks),
        "ttft_ms": round(ttft_ms, 1),
        "tpot_ms": round(tpot_ms, 1),
        "tps": round(1000 / tpot_ms, 1) if tpot_ms > 0 else 0,
        "total_ms": round(total_ms, 1),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


if __name__ == "__main__":
    client = anthropic.Anthropic()
    result = stream_with_ttft(
        client,
        model="claude-sonnet-4-5-20250514",
        messages=[{"role": "user", "content": "What is PagedAttention?"}],
        system="Answer in exactly 3 sentences.",
        max_tokens=200,
    )
    print(f"TTFT:  {result['ttft_ms']:.0f}ms")
    print(f"TPOT:  {result['tpot_ms']:.1f}ms")
    print(f"TPS:   {result['tps']:.0f} tok/s")
    print(f"Total: {result['total_ms']:.0f}ms")
    print(f"\n{result['content']}")
```

### 5.3 Token-Aware Cost Tracker

```python
"""
Per-team/per-project cost tracking with budget enforcement.
Plugs into the gateway layer.
"""

import time
from dataclasses import dataclass, field
from threading import Lock


MODEL_PRICING = {
    # model_id: (input_$/M, output_$/M)
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-sonnet-4-5-20250514": (3.0, 15.0),
    "claude-haiku-4-5-20250514": (0.80, 4.0),
}


@dataclass
class BudgetEntry:
    team: str
    monthly_budget_usd: float
    spent_usd: float = 0.0
    request_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    window_start: float = field(default_factory=time.time)


class CostTracker:
    """
    Thread-safe cost tracker with budget enforcement.
    Call check_budget() before each LLM call.
    Call record_usage() after each successful call.
    """

    def __init__(self):
        self._budgets: dict[str, BudgetEntry] = {}
        self._lock = Lock()

    def set_budget(self, team: str, monthly_budget_usd: float):
        with self._lock:
            self._budgets[team] = BudgetEntry(
                team=team, monthly_budget_usd=monthly_budget_usd
            )

    def check_budget(self, team: str) -> bool:
        """Returns True if team is within budget. False = reject request."""
        with self._lock:
            entry = self._budgets.get(team)
            if entry is None:
                return True  # no budget configured = unlimited
            self._maybe_reset_window(entry)
            return entry.spent_usd < entry.monthly_budget_usd

    def record_usage(
        self, team: str, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Record token usage, return cost in USD."""
        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            return 0.0

        cost = (
            (input_tokens / 1_000_000) * pricing[0]
            + (output_tokens / 1_000_000) * pricing[1]
        )
        with self._lock:
            entry = self._budgets.get(team)
            if entry:
                self._maybe_reset_window(entry)
                entry.spent_usd += cost
                entry.request_count += 1
                entry.total_input_tokens += input_tokens
                entry.total_output_tokens += output_tokens
        return cost

    def get_summary(self, team: str) -> dict:
        with self._lock:
            entry = self._budgets.get(team)
            if not entry:
                return {"team": team, "status": "no_budget_configured"}
            self._maybe_reset_window(entry)
            return {
                "team": entry.team,
                "spent_usd": round(entry.spent_usd, 4),
                "budget_usd": entry.monthly_budget_usd,
                "utilization_pct": round(
                    entry.spent_usd / entry.monthly_budget_usd * 100, 1
                ),
                "requests": entry.request_count,
                "input_tokens": entry.total_input_tokens,
                "output_tokens": entry.total_output_tokens,
            }

    @staticmethod
    def _maybe_reset_window(entry: BudgetEntry):
        """Reset counters if calendar month has rolled over."""
        now = time.time()
        if now - entry.window_start > 30 * 86400:  # ~30 days
            entry.spent_usd = 0.0
            entry.request_count = 0
            entry.total_input_tokens = 0
            entry.total_output_tokens = 0
            entry.window_start = now


if __name__ == "__main__":
    tracker = CostTracker()
    tracker.set_budget("ml-platform", monthly_budget_usd=500.0)

    # Simulate usage
    cost = tracker.record_usage(
        "ml-platform", "claude-sonnet-4-5-20250514",
        input_tokens=50_000, output_tokens=10_000
    )
    print(f"Request cost: ${cost:.4f}")
    print(tracker.get_summary("ml-platform"))

    # Budget check before next request
    if not tracker.check_budget("ml-platform"):
        print("BLOCKED: monthly budget exceeded")
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Model Routing Gateway for Enterprise AI Platform

**Problem statement.** A mid-size enterprise (5K employees) runs 12 LLM-powered applications: customer support chat, internal document search, code review, contract analysis, and others. Current state: each team calls OpenAI or Anthropic directly, spending $45K/month with no visibility, no cost controls, and no failover. Three outage incidents in 6 months where a single provider going down took multiple apps offline. CISO requires PII redaction and audit logging before SOC 2 audit in Q1.

**Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           APPLICATION LAYER                                     │
│                                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Support   │  │ Doc      │  │ Code     │  │ Contract │  │ 8 other apps     │ │
│  │ Chat Bot  │  │ Search   │  │ Review   │  │ Analysis │  │                  │ │
│  └─────┬────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬──────────┘ │
└────────┼────────────┼─────────────┼─────────────┼─────────────────┼────────────┘
         │            │             │             │                 │
         └────────────┴──────┬──────┴─────────────┴─────────────────┘
                             │ unified OpenAI-compat API
┌────────────────────────────▼────────────────────────────────────────────────────┐
│                          LLM GATEWAY (Portkey / LiteLLM)                        │
│                                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │ Auth + API    │  │ Token-Aware   │  │ PII Detect   │  │ Request Logger     │ │
│  │ Key Manager   │  │ Rate Limiter  │  │ + Redact     │  │ (prompt hash,      │ │
│  │ (per-team     │  │ (team/project │  │ (Presidio;   │  │  model, tokens,    │ │
│  │  keys, RBAC   │  │  budgets;     │  │  before      │  │  cost, latency;    │ │
│  │  model tiers) │  │  hard caps)   │  │  logging and │  │  SOC 2 compliant)  │ │
│  │               │  │              │  │  provider)   │  │                    │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘ │
│         │                 │                 │                    │             │
│  ┌──────▼─────────────────▼─────────────────▼────────────────────▼───────────┐ │
│  │                         MODEL ROUTER                                      │ │
│  │                                                                           │ │
│  │  Rule tier:  support_chat -> Sonnet 5                                     │ │
│  │              doc_search -> Haiku 3.5                                       │ │
│  │              contract_analysis -> Opus 5                                   │ │
│  │                                                                           │ │
│  │  Cascade tier (for support_chat):                                         │ │
│  │    1. Try Haiku 3.5 (fast, cheap)                                         │ │
│  │    2. If confidence < 0.7 OR complex query detected -> escalate Sonnet 5  │ │
│  │    3. If Sonnet fails -> fall back to GPT-4o (cross-provider resilience)  │ │
│  └──────────────────────────────────┬────────────────────────────────────────┘ │
└─────────────────────────────────────┼───────────────────────────────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                           │                            │
┌────────▼──────────┐  ┌─────────────▼─────────────┐  ┌──────────▼────────────┐
│ Anthropic API      │  │ OpenAI API                 │  │ Google Gemini API     │
│ (Opus 5, Sonnet 5, │  │ (GPT-4o, GPT-5.6 Luna,   │  │ (Gemini 2.0 Flash)   │
│  Haiku 3.5)        │  │  o3)                       │  │                      │
│                    │  │                            │  │                      │
│ Circuit breaker:   │  │ Circuit breaker:           │  │ Circuit breaker:     │
│ 3 failures -> OPEN │  │ 3 failures -> OPEN         │  │ 3 failures -> OPEN   │
│ 30s recovery       │  │ 30s recovery               │  │ 30s recovery         │
└────────────────────┘  └────────────────────────────┘  └──────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dimension    │ Current (direct)    │ Gateway (proposed)   │ Decision        │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Cost         │ $45K/mo, no control │ $18-25K/mo with      │ Gateway wins:   │
│              │                     │ routing + caching    │ 40-55% savings  │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Latency      │ Direct to provider  │ +10-25ms (Python     │ Acceptable:     │
│              │ (~200ms TTFT)       │ proxy overhead)      │ <15% increase   │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Ops burden   │ Zero (API only)     │ Gateway infra:       │ Justified: 1    │
│              │                     │ deploy, monitor,     │ team manages    │
│              │                     │ upgrade              │ for 12 apps     │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Security     │ API keys scattered  │ Centralized key      │ Gateway wins:   │
│              │ across 12 repos;    │ vault; PII redaction │ SOC 2 audit     │
│              │ no PII controls     │ before provider;     │ blocker removed │
│              │                     │ per-team audit logs  │                 │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Availability │ Single-provider     │ Multi-provider       │ Gateway wins:   │
│              │ SPOF per app        │ failover; 99.9% SLA  │ eliminates 3/yr │
│              │                     │ achievable           │ outages         │
├──────────────┼─────────────────────┼──────────────────────┼─────────────────┤
│ Scalability  │ Each app manages    │ Centralized rate     │ Gateway wins:   │
│              │ own limits          │ limits + budget caps │ prevents runaway│
│              │                     │ prevent runaway      │ spend incidents │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Decision rationale:** The gateway is the correct choice. The 10-25ms latency overhead is negligible against 200ms+ TTFT. Cost savings (40-55%) pay for the ops burden within month 1. Multi-provider failover eliminates the single-provider SPOF. Centralized PII redaction and audit logging unblock the SOC 2 audit. The routing cascade alone -- sending 70% of support queries to Haiku instead of Sonnet -- saves $15K/month. Start with Portkey (open-source, full governance) or LiteLLM (self-hosted, cost tracking built in). Avoid Azure AI Foundry Router for latency-sensitive paths (50-200ms LLM classification overhead).

---

### Scenario 2: Self-Hosted Inference Platform for Regulated Financial Services

**Problem statement.** A financial services firm processes 50K confidential document analysis requests/day (earnings reports, SEC filings, loan applications). Current state: using Claude Sonnet API at $4,200/day ($126K/month). Regulatory requirement: no customer financial data may leave the firm's VPC. Legal has blocked all external API usage for production workloads starting Q2. The firm needs equivalent quality at lower cost with full data sovereignty.

**Architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        ON-PREMISES / VPC BOUNDARY                                │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │                        INGRESS + AUTH LAYER                              │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐   │    │
│  │  │ Internal LB   │  │ mTLS Termina-│  │ Request Classifier           │   │    │
│  │  │ (NGINX/Envoy) │──▶│ tion + RBAC  │──▶│ (rule-based; < 1ms overhead) │   │    │
│  │  │               │  │              │  │ simple -> Llama 70B          │   │    │
│  │  │               │  │              │  │ complex -> Llama 405B        │   │    │
│  │  └──────────────┘  └──────────────┘  └───────────┬──────────────────┘   │    │
│  └──────────────────────────────────────────────────┼───────────────────────┘    │
│                                                      │                           │
│  ┌──────────────────────────────────────────────────▼───────────────────────┐    │
│  │                        INFERENCE CLUSTER (vLLM)                          │    │
│  │                                                                          │    │
│  │  ┌─────────────────────────────────┐  ┌─────────────────────────────┐   │    │
│  │  │ Pool A: Llama 3.1 70B (FP8)     │  │ Pool B: Llama 3.1 405B(FP8)│   │    │
│  │  │                                 │  │                             │   │    │
│  │  │ 2 nodes x 8 H100 SXM (TP=4,    │  │ 8 nodes x 8 H100 SXM      │   │    │
│  │  │ PP=1, 4 replicas via DP)        │  │ (TP=8, PP=8)               │   │    │
│  │  │                                 │  │                             │   │    │
│  │  │ Handles: 70% of requests        │  │ Handles: 30% of requests   │   │    │
│  │  │ (classification, extraction,    │  │ (complex reasoning,        │   │    │
│  │  │  summarization)                 │  │  multi-doc analysis)       │   │    │
│  │  │                                 │  │                             │   │    │
│  │  │ KV cache: PagedAttention        │  │ KV cache: PagedAttention   │   │    │
│  │  │ + INT8 quantization             │  │ + prefix caching for       │   │    │
│  │  │ Spec decoding: EAGLE-3 (3.5x)  │  │   system prompts           │   │    │
│  │  │                                 │  │ Spec decoding: external    │   │    │
│  │  │ Throughput: ~320 tok/s agg      │  │   draft (Llama 8B, 2.5x)  │   │    │
│  │  │ p95 TTFT: <400ms               │  │                             │   │    │
│  │  │                                 │  │ Throughput: ~200 tok/s agg  │   │    │
│  │  │                                 │  │ p95 TTFT: <1.5s            │   │    │
│  │  └─────────────────────────────────┘  └─────────────────────────────┘   │    │
│  │                                                                          │    │
│  │  ┌─────────────────────────────────────────────────────────────────────┐ │    │
│  │  │ Shared Infrastructure                                               │ │    │
│  │  │  - Prometheus + Grafana: TTFT, TPOT, queue depth, VRAM, KV cache   │ │    │
│  │  │  - Autoscaler: scales replicas on queue_depth > 50 or TPOT > 40ms  │ │    │
│  │  │  - Model registry: versioned model weights + quantization configs   │ │    │
│  │  │  - Audit logger: all prompts/completions, PII-redacted, immutable  │ │    │
│  │  └─────────────────────────────────────────────────────────────────────┘ │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  No data leaves VPC. Zero external API calls for inference.                      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dimension    │ API (current)       │ Self-hosted (proposed)│ Decision       │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Cost         │ $126K/mo            │ ~$85K/mo (GPU lease:  │ Self-hosted    │
│              │ (50K req/day @      │ 10 nodes x 8 H100 =  │ wins: 33%     │
│              │  Sonnet pricing)    │ $68K + ops $17K)      │ savings at     │
│              │                     │                       │ this volume    │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Latency      │ p95 TTFT ~500ms     │ Pool A p95: <400ms    │ Self-hosted    │
│              │ (network + provider │ Pool B p95: <1.5s     │ wins (Pool A); │
│              │  queue)             │ (no network to cloud) │ comparable (B) │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Ops burden   │ Zero                │ Significant: GPU ops, │ API wins: but  │
│              │                     │ model updates, vLLM   │ regulatory     │
│              │                     │ upgrades, monitoring  │ mandate forces │
│              │                     │ (2-3 FTE MLOps)       │ self-hosted    │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Security /   │ Data leaves VPC;    │ Data stays on-prem;   │ Self-hosted    │
│ Compliance   │ blocked by legal    │ full audit control;   │ wins: only     │
│              │ for Q2              │ regulatory compliant  │ compliant path │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Quality      │ Sonnet-class (high) │ Llama 405B FP8:       │ Acceptable:    │
│              │                     │ ~1-2 pts below Sonnet │ 70B handles    │
│              │                     │ on MMLU-Pro; 70B:     │ simple tasks;  │
│              │                     │ 3-5 pts below         │ 405B for hard  │
├──────────────┼─────────────────────┼───────────────────────┼────────────────┤
│ Scalability  │ Instant (API burst) │ Bound by GPU count;   │ API wins for   │
│              │                     │ 2-4 week lead time    │ burst; self-   │
│              │                     │ for new nodes         │ hosted has     │
│              │                     │                       │ steady ceiling │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Decision rationale:** Self-hosted is the only compliant path given the regulatory mandate. At 50K requests/day, GPU amortization beats API pricing by 33%. The two-pool architecture (70B for simple tasks, 405B for complex) mirrors the cascade routing pattern from Scenario 1 but on self-hosted infrastructure. FP8 quantization is the right starting point on H100 hardware (~0.4 pts quality regression, near-lossless). Speculative decoding (EAGLE-3 on Pool A, external draft on Pool B) provides 2.5-3.5x latency improvement without quality loss. The operational cost (2-3 FTE MLOps) is the main drawback, justified by the regulatory mandate and monthly savings. Key risk: 2-4 week lead time for new GPU nodes limits burst capacity. Mitigation: keep a warm standby node and pre-negotiate cloud GPU spot capacity with a VPN tunnel for extreme bursts (data stays encrypted in transit, decrypted only on firm-controlled instances).

> **Gap**: This scenario does not cover model update/rollback procedures, A/B testing of model versions, or blue-green deployment patterns for the inference cluster. A production deployment would need all three.
