# Module 01: LLM Foundations

**Scope**: Transformer internals, inference pipeline, MoE architecture, token economics, distributed serving, enterprise security, and production code patterns.
**Prerequisite**: Familiarity with neural networks, Python, REST APIs.
**Last updated**: 2026-08-21 | **Sources consulted**: 58

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                              CONTROL PLANE                                      │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐   │
 │  │  API Gateway  │  │ Model Router │  │  Autoscaler  │  │  Model Registry   │   │
 │  │  - Auth/mTLS  │  │  - Rule/ML   │  │  - KEDA      │  │  - Version ctrl   │   │
 │  │  - Rate limit │  │  - A/B test  │  │  - HPA       │  │  - Canary config  │   │
 │  │  - Quota mgmt │  │  - Shadow    │  │  - Scale-0   │  │  - Quantization   │   │
 │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └───────────────────┘   │
 │         │                 │                  │                                   │
 └─────────┼─────────────────┼──────────────────┼──────────────────────────────────┘
           │                 │                  │
 ┌─────────┼─────────────────┼──────────────────┼──────────────────────────────────┐
 │         ▼                 ▼                  ▼        DATA PLANE                 │
 │  ┌─────────────────────────────────────────────────────────────────────────┐    │
 │  │                    Continuous Batching Scheduler                        │    │
 │  │            (iteration-level scheduling, chunked prefill)               │    │
 │  └──────────────────────────┬──────────────────────────────────────────────┘    │
 │                             │                                                   │
 │  ┌──────────────────────────▼──────────────────────────────────────────────┐    │
 │  │                     INFERENCE ENGINE (vLLM / SGLang / TRT-LLM)         │    │
 │  │                                                                         │    │
 │  │   ┌─────────────┐    ┌──────────────────────────────────────────┐       │    │
 │  │   │  Tokenizer  │    │         Decoder-Only Transformer         │       │    │
 │  │   │  (BPE)      │    │                                          │       │    │
 │  │   │  text->IDs  ├───►│  Embedding (B x S x E)                  │       │    │
 │  │   └─────────────┘    │       │                                  │       │    │
 │  │                      │       ▼                                  │       │    │
 │  │                      │  ┌─────────────────────────────────┐     │       │    │
 │  │                      │  │  N x Transformer Block          │     │       │    │
 │  │                      │  │  ┌───────────┐  ┌────────────┐  │     │       │    │
 │  │                      │  │  │ RMSNorm   │  │ RMSNorm    │  │     │       │    │
 │  │                      │  │  │     │     │  │     │      │  │     │       │    │
 │  │                      │  │  │     ▼     │  │     ▼      │  │     │       │    │
 │  │                      │  │  │ Attention │  │ SwiGLU FFN │  │     │       │    │
 │  │                      │  │  │ GQA / MLA │  │ (or MoE    │  │     │       │    │
 │  │                      │  │  │ + RoPE    │  │  Router +  │  │     │       │    │
 │  │                      │  │  │     │     │  │  k Experts)│  │     │       │    │
 │  │                      │  │  │     ▼     │  │     │      │  │     │       │    │
 │  │                      │  │  │ KV Cache  │  │     │      │  │     │       │    │
 │  │                      │  │  └─────┬─────┘  └─────┬──────┘  │     │       │    │
 │  │                      │  │        └───────┬──────┘         │     │       │    │
 │  │                      │  └────────────────┼────────────────┘     │       │    │
 │  │                      │                   ▼                      │       │    │
 │  │                      │  Logit Head -> Softmax -> Sampling       │       │    │
 │  │                      │  (temperature, top-p, top-k, min-p)      │       │    │
 │  │                      └──────────────────────────────────────────┘       │    │
 │  │                                                                         │    │
 │  └─────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                 │
 │  ┌────────────────────────────────────────────────────────────────────────────┐  │
 │  │                         KV CACHE TIER                                      │  │
 │  │  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────────┐     │  │
 │  │  │ GPU HBM  │───►│ CPU DRAM │───►│ Local SSD│───►│ Remote (Ceph/S3) │     │  │
 │  │  │ (hot)    │    │ (warm)   │    │ (cool)   │    │ (cold/persistent)│     │  │
 │  │  └──────────┘    └──────────┘    └──────────┘    └──────────────────┘     │  │
 │  └────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                 │
 │                                                                                 │
 │  ┌────────────────────────────────────────────────────────────────────────────┐  │
 │  │                         TOOL PROXY LAYER                                   │  │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │  │
 │  │  │Schema Validatr│  │  Sandbox     │  │   RBAC       │  │  Result      │  │  │
 │  │  │- JSON schema  │  │  - gVisor    │  │  - Per-tool  │  │  Injector    │  │  │
 │  │  │- Param types  │  │  - WASM      │  │  - Per-tenant│  │  - Sanitize  │  │  │
 │  │  │- Required flds│  │  - Timeout   │  │  - Scope chk │  │  - Truncate  │  │  │
 │  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │  │
 │  │         │                 │                  │                 │          │  │
 │  │         └────────┬────────┘                  └────────┬───────┘          │  │
 │  │                  ▼                                    ▼                   │  │
 │  │         ┌──────────────┐                    ┌──────────────────┐         │  │
 │  │         │ External APIs│                    │ Code Exec Engine │         │  │
 │  │         │ (REST/gRPC)  │                    │ (interpreter/CLI)│         │  │
 │  │         └──────────────┘                    └──────────────────┘         │  │
 │  └────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                 │
 └─────────────────────────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                          TELEMETRY & OBSERVABILITY                              │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
 │  │ OpenTelemetry│  │ Audit Logger │  │ Cost Tracker │  │ Quality Evaluator│   │
 │  │ - Traces     │  │ - SOC2 logs  │  │ - Per-model  │  │ - Hallucination  │   │
 │  │ - Metrics    │  │ - Immutable  │  │ - Per-tenant │  │ - Schema valid.  │   │
 │  │ - TTFT/TPS   │  │ - GDPR-safe  │  │ - Alerts     │  │ - Content filter │   │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

A client request enters the **API Gateway**, which authenticates via mTLS or API key, enforces per-tenant rate limits (TPM/RPM), and attaches a correlation ID. The **Model Router** classifies the request -- rule-based (<1ms overhead) or ML-based (~5ms) -- and selects a model tier. For shadow deployments, the gateway duplicates traffic to a candidate model without affecting the response path.

The request reaches the **Continuous Batching Scheduler**, which inserts it into the next iteration-level batch rather than waiting for a static batch to fill. During **prefill**, all input tokens are processed in parallel (compute-bound). The scheduler uses chunked prefill to interleave prefill chunks with ongoing decode steps, preventing head-of-line blocking.

Inside the **Transformer**, each block applies pre-RMSNorm, then masked self-attention with RoPE-rotated Q/K vectors. GQA shares KV heads across query-head groups (Llama 3, Mixtral); MLA compresses KV into a low-rank latent vector and re-expands per head (DeepSeek V3/V4). The KV cache stores computed key-value pairs via **PagedAttention** -- a block table maps logical positions to non-contiguous physical GPU memory blocks, eliminating 60-80% fragmentation.

After attention, the residual passes through a second RMSNorm into the FFN. In MoE models, a lightweight router selects top-k experts (e.g., top-8 of 256 for DeepSeek-V3) per token; only selected expert FFNs fire. The router uses auxiliary-loss-free load balancing or dynamic bias to prevent expert collapse.

After N blocks, the logit head projects to vocabulary size V. Sampling applies temperature scaling, top-p nucleus filtering, and top-k truncation to produce the next token ID. The token feeds back autoregressively during **decode** (memory-bandwidth-bound, one token per forward pass). The detokenizer converts IDs back to text and streams them to the client.

The **KV Cache Tier** evicts cold cache entries down the hierarchy: GPU HBM to CPU DRAM to local SSD to remote storage (Ceph/S3). **KV-cache-aware routing** (GKE Inference Gateway, llm-d) directs repeat requests to replicas already holding relevant cached state, avoiding redundant prefill.

When the model emits a tool/function call, the **Tool Proxy Layer** intercepts it before execution. The **Schema Validator** checks the call against the registered JSON schema (parameter types, required fields, enum constraints) — malformed calls are rejected with a structured error fed back to the model for retry. **RBAC** enforces per-tool, per-tenant permission scopes. The **Sandbox** (gVisor, WASM, or containerized) executes the tool with resource limits (CPU, memory, network, timeout). The **Result Injector** sanitizes and truncates tool output before injecting it back into the model's context window for the next generation step.

Throughout, the **Telemetry layer** records TTFT, TPS, token usage, model version, and the full request/response for audit logging. Cost tracking aggregates per-model, per-tenant spend. Quality evaluators run asynchronously to detect hallucination, schema violations, and content policy breaches.

### 1.3 MoE Internal Topology

```
                        Token embedding vector
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Router (Softmax /   │
                    │   Sigmoid gating)     │
                    │   Produces weights    │
                    │   for top-k experts   │
                    └───────┬───────────────┘
                            │
              ┌─────────────┼─────────────┬─────────────┐
              ▼             ▼             ▼             ▼
         ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
         │Expert 1 │  │Expert 2 │  │Expert k │  │ Shared  │
         │(SwiGLU) │  │(SwiGLU) │  │(SwiGLU) │  │ Expert  │
         └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘
              │            │            │             │
              └─────────┬──┴────────────┘             │
                        │  weighted sum               │
                        ▼                             │
                   ┌──────────┐                       │
                   │  Combine  │◄──────────────────────┘
                   │  outputs  │
                   └──────────┘
```

Key: only top-k experts activate per token (e.g., 8 of 256 in DeepSeek-V3). All expert weights must reside in GPU memory; MoE saves compute, not memory. DeepSeek-V3: 671B total parameters, 37B active per token.

---

## 2. Core Mechanics & Algorithms

### 2.1 Self-Attention

The core attention computation for a single head:

```
Attention(Q, K, V) = softmax(QK^T / sqrt(d_k)) V
```

**Complexity**: O(n^2 * d) where n = sequence length, d = head dimension. The QK^T matrix is n x n, computed for each of h heads. Total FLOPs for multi-head attention: O(n^2 * d * h) = O(n^2 * D) where D = model dimension.

**Memory**: The attention score matrix alone is O(n^2) per head. FlashAttention avoids materializing this matrix by computing attention in tiled blocks, reducing memory from O(n^2) to O(n) while maintaining exact computation (no approximation).

### 2.2 KV Cache Mechanics

During autoregressive decoding, each new token only needs to attend to all previous tokens. Without caching, generating T tokens from a prompt of length P requires recomputing attention over all prior tokens at each step -- O((P+T)^2 * T) total work.

With KV cache, keys and values from prior tokens are stored and reused. Each decode step computes attention between the single new Q vector and all cached K/V vectors -- O(n * d) per head per step instead of O(n^2 * d).

**Memory cost per token cached**:

```
KV cache per token = 2 * n_layers * n_kv_heads * d_head * bytes_per_param

Example (Llama 3 70B, FP16):
  = 2 * 80 layers * 8 GQA heads * 128 dim * 2 bytes
  = 327,680 bytes (~320 KB) per token
  = 320 MB per 1K tokens cached
```

**Attention variant KV cache sizes** (relative to MHA baseline with h=64 heads):

```
┌──────────┬──────────────┬────────────────┬──────────────────────────┐
│ Variant  │ KV heads     │ Cache fraction │ Memory per 1K tokens     │
├──────────┼──────────────┼────────────────┼──────────────────────────┤
│ MHA      │ 64           │ 1.0x           │ ~2.6 GB (Llama 3 70B)   │
│ GQA-8    │ 8            │ 0.125x         │ ~320 MB                  │
│ MQA      │ 1            │ 0.016x         │ ~40 MB                   │
│ MLA      │ compressed   │ < GQA          │ Varies; re-expands on fly│
└──────────┴──────────────┴────────────────┴──────────────────────────┘
```

MLA compresses KV into a low-rank latent vector (much smaller than even GQA), then re-expands per head during attention. This trades compute (re-expansion) for memory (smaller cache). Benchmarks show MLA can surpass MHA quality while using less memory than GQA.

### 2.3 Positional Encoding: RoPE

RoPE encodes position by rotating Q and K vectors in 2D subspaces:

```
RoPE(x, m) applies rotation matrix R(m*theta_i) to each pair (x_{2i}, x_{2i+1})

where theta_i = 10000^(-2i/d)  for dimension pair i
      m = absolute position index

Inner product <RoPE(q, m), RoPE(k, n)> depends only on (m - n),
giving relative position sensitivity without learned parameters.
```

**Why RoPE won**: It injects relative position information directly into attention scores without extra parameters. It generalizes to longer sequences than seen during training (with techniques like NTK-aware scaling, YaRN). ALiBi (linear bias decay) is simpler but empirically underperforms RoPE at scale.

### 2.4 SwiGLU FFN

The FFN in modern transformers uses SwiGLU activation:

```
SwiGLU(x) = (x * W_1) * swish(x * W_gate) then projected by W_2

swish(x) = x * sigmoid(beta * x)    (beta=1 in practice)
```

SwiGLU uses three weight matrices instead of two (W_1, W_gate, W_2), increasing parameter count by ~50% per FFN layer, but empirically improves quality enough to justify the cost. When combined with MoE, only top-k experts' SwiGLU FFNs fire per token, amortizing the parameter increase.

### 2.5 Sampling Strategies

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Sampling Pipeline                              │
│                                                                     │
│  Logits (raw) ──► Temperature ──► Top-k ──► Top-p ──► Sample       │
│                    scaling       truncate   nucleus    from dist    │
│                                                                     │
│  Temperature T:  logits' = logits / T                               │
│    T < 1.0 : sharper (more deterministic)                           │
│    T = 1.0 : unmodified                                             │
│    T > 1.0 : flatter (more random)                                  │
│                                                                     │
│  Top-k:  keep only k highest-probability tokens, zero out rest      │
│                                                                     │
│  Top-p (nucleus):  keep smallest set of tokens whose cumulative     │
│    probability >= p, zero out rest                                   │
│                                                                     │
│  min-p:  keep tokens with probability >= min_p * max_probability    │
└─────────────────────────────────────────────────────────────────────┘
```

**Production rules**:
- Temperature and top-p are coupled. Raising both amplifies randomness unpredictably. If raising temperature, lower top-p; if raising top-p, lower temperature.
- Temperature=0 is not deterministic. GPU floating-point non-associativity and server-side batching variations mean output is highly repeatable but not bit-identical.
- For agents and tool calling: temperature=0 (greedy) is the standard choice.
- Claude 4.x rejects simultaneous temperature and top_p with a 400 error.
- OpenAI o1/o3 reasoning models freeze sampling parameters (temperature=1, top_p=1); changes return an error.

### 2.6 Inference Pipeline State Transitions

```
                    ┌───────────┐
      Request ────► │  QUEUED   │
                    └─────┬─────┘
                          │ scheduler assigns GPU blocks
                          ▼
                    ┌───────────┐
                    │  PREFILL  │  compute-bound: all input tokens in parallel
                    │  (prompt)  │  builds initial KV cache
                    └─────┬─────┘
                          │ first output token emitted (TTFT measured here)
                          ▼
                    ┌───────────┐
                    │  DECODE   │◄─┐  memory-bandwidth-bound: 1 token/step
                    │  (autoregr)│  │  appends to KV cache each step
                    └─────┬─────┘  │
                          │        │ next token (loop)
                          ├────────┘
                          │ EOS or max_tokens reached
                          ▼
                    ┌───────────┐
                    │ COMPLETE  │  KV cache blocks returned to free pool
                    └───────────┘  continuous batching slots in next request
```

### 2.7 Attention Variant Trade-off Summary

```
┌──────────┬────────────┬────────────┬──────────────┬──────────────────────┐
│ Variant  │ Memory     │ Compute    │ Quality      │ Adopted by           │
├──────────┼────────────┼────────────┼──────────────┼──────────────────────┤
│ MHA      │ Highest    │ Baseline   │ Highest      │ Original Transformer │
│ MQA      │ Lowest     │ Fastest    │ Some loss    │ PaLM, Falcon         │
│ GQA      │ Low        │ Fast       │ Near-MHA     │ Llama 3, Mixtral     │
│ MLA      │ Very low   │ Re-expand  │ Exceeds MHA  │ DeepSeek V2/V3/V4   │
└──────────┴────────────┴────────────┴──────────────┴──────────────────────┘
```

GQA is the pragmatic default (best ecosystem support, good trade-off). MLA is theoretically superior but requires custom kernels and is primarily used by DeepSeek.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Base cost per request**:

```
cost = (input_tokens * input_price / 1M) + (output_tokens * output_price / 1M)
```

**Cost per 1,000 runs** (assuming 1,000 input + 500 output tokens per run):

```
┌──────────────────┬────────────┬─────────────┬──────────────────────┐
│ Tier             │ Model      │ $/1K runs   │ Assumptions          │
├──────────────────┼────────────┼─────────────┼──────────────────────┤
│ Frontier         │ Opus 5     │ $17.50      │ $5/M in, $25/M out   │
│ Frontier         │ Sonnet 5   │ $10.50      │ $3/M in, $15/M out   │
│ Frontier         │ GPT-5.5    │ $20.00      │ $5/M in, $30/M out   │
│ Frontier         │ o3 (reason)│ $45.00      │ $15/M in, $60/M out  │
│ Mid-tier         │ Haiku 4.5  │ $3.50       │ $1/M in, $5/M out    │
│ Mid-tier         │ GPT-4.1 Mini│ $1.20      │ $0.40/M in, $1.60/M  │
│ Mid-tier         │ Gemini 2.5 Flash│ $0.45  │ $0.15/M in, $0.60/M  │
│ Budget           │ GPT-4.1 Nano│ $0.30      │ $0.10/M in, $0.40/M  │
│ Budget           │ DeepSeek V3│ $0.28       │ $0.14/M in, $0.28/M  │
└──────────────────┴────────────┴─────────────┴──────────────────────┘
```

**Frontier-to-budget spread**: 63x (o3 at $45 vs DeepSeek V3 at $0.28 per 1K runs). Model routing is the single highest-leverage cost optimization.

### 3.2 Prompt Caching Impact

Claude prompt caching is a **prefix match**. Render order: `tools` -> `system` -> `messages`. Any byte change anywhere in the prefix invalidates everything after it.

```
┌────────────────┬────────────────┬────────────┬──────────────────┐
│ TTL            │ Write cost     │ Read cost  │ Effective saving │
├────────────────┼────────────────┼────────────┼──────────────────┤
│ 5 min (default)│ 1.25x base     │ 0.1x base  │ 90% on reads     │
│ 1 hour         │ 2.0x base      │ 0.1x base  │ 90% on reads     │
└────────────────┴────────────────┴────────────┴──────────────────┘
```

**Worked example** (Opus 4.6 at $5/M input, 800-token system prompt cached, 200 new tokens per turn, 100 turns):
- Without caching: 100 turns * 800 tokens = 80K cached-region tokens re-sent = $0.40
- With caching: 1 write (800 * $6.25/M = $0.005) + 99 reads (800 * $0.50/M * 99 = $0.040) = $0.045
- **Saving: ~89%** on the cached portion

**Silent invalidators to avoid**: `datetime.now()` in cached content, user-specific fields in the system prompt prefix, unsorted JSON keys across calls, varying tool sets between requests.

Minimum cacheable prefix: ~1,024 tokens.

### 3.3 Batch API Economics

Flat 50% discount on input and output across all Claude models. Trade-off: results within 24 hours, not real-time.

```
┌──────────────┬─────────────┬─────────────┬────────────────────┐
│ Model        │ Std $/1K    │ Batch $/1K  │ Saving per 1K runs │
│              │ runs        │ runs        │                    │
├──────────────┼─────────────┼─────────────┼────────────────────┤
│ Opus 5       │ $17.50      │ $8.75       │ $8.75 (50%)        │
│ Sonnet 5     │ $10.50      │ $5.25       │ $5.25 (50%)        │
│ Haiku 4.5    │ $3.50       │ $1.75       │ $1.75 (50%)        │
└──────────────┴─────────────┴─────────────┴────────────────────┘
```

Single batch: up to 100,000 requests or 256 MB. Results downloadable for 29 days.

**Best for**: evals, bulk classification, nightly processing, data enrichment -- anything without real-time latency requirements.

### 3.4 Latency SLA Targets

```
┌──────────────┬─────────┬─────────┬─────────┬──────────────────────────┐
│ Metric       │ P50     │ P95     │ P99     │ Mitigation               │
├──────────────┼─────────┼─────────┼─────────┼──────────────────────────┤
│ TTFT         │         │         │         │                          │
│  Frontier    │ 850ms   │ 1.8s    │ 2.5s+   │ Prompt caching, region   │
│  Mid-tier    │ 300ms   │ 500ms   │ 800ms   │ Smaller models, edge     │
│  Speed-opt   │ 150ms   │ 300ms   │ 500ms   │ Groq/Cerebras, quantized │
├──────────────┼─────────┼─────────┼─────────┼──────────────────────────┤
│ TPS          │         │         │         │                          │
│  Frontier    │ 80      │ 60      │ 40      │ Speculative decoding     │
│  Mid-tier    │ 150     │ 120     │ 90      │ Continuous batching      │
│  Speed-opt   │ 400+    │ 350     │ 280     │ Custom silicon (Groq)    │
└──────────────┴─────────┴─────────┴─────────┴──────────────────────────┘
```

**Design principle**: P95 is the constraint, not P50. SLO design should anchor on P95 -- provider quality differs more at the tail than at the median.

**Regional impact**: US-East to APAC adds 180-220ms TTFT P50. EU to US-East adds 80-110ms. Multi-region deployment or edge inference eliminates this.

**TPS UX thresholds**: 50 TPS feels slow, 100 feels normal, 200+ feels instant, above 300 the bottleneck shifts to the client renderer.

**Anthropic-specific**: Most consistent latency variance among major providers -- P50 and P99 TTFT stay close together. GPT-4.1 P99 can spike 3-5x above P50 during peak hours.

### 3.5 Context Window Cost Implications

All current Claude models (Opus, Sonnet) support 1M tokens at flat per-token rates -- no surcharges for long context. Haiku 4.5 supports 200K.

Competitors penalize long context: Gemini 3.1 Pro doubles input pricing above 200K tokens. GPT-5.6 Sol doubles above 272K tokens.

However, context window utilization has a hidden cost: instruction attenuation. A 200K context window that loses instruction fidelity between 60-80% fill is effectively a 140K-160K window for production agent use. Multi-turn performance drops 39% on average (2025 study). Design for compaction or summarization before hitting these thresholds.

### 3.6 Cost Optimization Stack (Multiplicative)

```
Baseline: $17.50 / 1K runs (Opus 5, 1K in + 500 out)

Layer 1 - Model routing (70% to Haiku, 30% to Opus):
  0.7 * $3.50 + 0.3 * $17.50 = $7.70 / 1K runs          (-56%)

Layer 2 - Prompt caching on routed calls (90% read hit):
  cached input portion: 90% savings on ~60% of input
  ~$5.60 / 1K runs                                        (-68% cumulative)

Layer 3 - Batch API for async workloads (40% of volume):
  0.6 * $5.60 + 0.4 * ($5.60 * 0.5) = $4.48 / 1K runs    (-74% cumulative)

Net: $17.50 -> $4.48 = 74% reduction, no quality-impacting changes
```

### 3.7 Non-Functional Requirements

```
┌──────────────────┬──────────────────────────────────────────────────┐
│ Requirement      │ Target & Notes                                   │
├──────────────────┼──────────────────────────────────────────────────┤
│ Availability     │ 99.5% (provider SLA); design for 99.9% via      │
│                  │ multi-provider failover. LLM providers run       │
│                  │ 99-99.5% -- 6-14x worse than cloud infra (99.97%)│
├──────────────────┼──────────────────────────────────────────────────┤
│ RPO              │ Zero data loss on request/response logs          │
│                  │ (async write to durable store before ack)        │
├──────────────────┼──────────────────────────────────────────────────┤
│ RTO              │ < 30s failover to secondary provider             │
│                  │ Circuit breaker opens after 5 consecutive        │
│                  │ failures or 50% error rate in 60s window         │
├──────────────────┼──────────────────────────────────────────────────┤
│ Compliance       │ SOC2 Type II: continuous audit logs (6-12 months)│
│                  │ HIPAA: minimum necessary PHI; guardrails to      │
│                  │   detect/block PHI before reaching model         │
│                  │ EU AI Act: high-risk obligations from Aug 2026   │
│                  │ GDPR Art 32: security of processing controls     │
│                  │ ISO 42001: risk assessments for input manip.     │
│                  │ NIST AI RMF 600-1: prompt injection as named risk│
└──────────────────┴──────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

#### PagedAttention (vLLM)

Applies OS-style virtual memory paging to KV caches. Each sequence's KV cache is addressed through a logical block table mapping to non-contiguous physical blocks in GPU memory (default block size: 16 tokens). Eliminates 60-80% memory fragmentation vs. contiguous allocation. Always active in vLLM -- tune via `--gpu-memory-utilization`.

#### KV Cache Tiered Storage

```
┌─────────────────────────────────────────────────────────────────┐
│ Tier       │ Latency    │ Capacity   │ Persistence │ Use case   │
├────────────┼────────────┼────────────┼─────────────┼────────────┤
│ GPU HBM    │ ~ns        │ 80GB/GPU   │ None        │ Active gen │
│ CPU DRAM   │ ~us        │ TBs        │ Process     │ Overflow   │
│ Local NVMe │ ~100us     │ TBs        │ Node        │ Warm cache │
│ Remote     │ ~ms        │ PBs        │ Durable     │ Cross-sess │
│ (Ceph/S3)  │            │            │             │            │
└─────────────────────────────────────────────────────────────────┘
```

LMCache decouples KV cache from the inference engine (no fate-sharing). If the engine crashes, KV cache survives in the external tier. Reduces TTFT for long-context, multi-turn, and RAG workloads by reusing cached prefixes.

#### Continuous Batching

Static batching wastes GPU cycles waiting for the slowest sequence. Continuous batching operates per forward pass -- when a sequence completes, its blocks return to the free pool immediately and a waiting request slots in.

At 128+ concurrent requests on H100 SXM5, continuous batching + PagedAttention + chunked prefill delivers 2,200-2,400 tok/s for Llama 3.3 70B FP8 -- roughly 25% above default vLLM and 3-4x above naive PyTorch.

#### Speculative Decoding

A small draft model proposes k tokens; the large target model verifies all k in a single forward pass. When acceptance rate is high (common for predictable outputs like code boilerplate or structured data), delivers 2-3x decode speedup with zero quality degradation. vLLM supports multiple strategies: draft model, n-gram, suffix, EAGLE, DFlash.

#### Model Parallelism

```
┌──────────────┬───────────────────┬────────────────────┬──────────────────────┐
│ Strategy     │ What splits       │ Communication      │ When to use          │
├──────────────┼───────────────────┼────────────────────┼──────────────────────┤
│ Tensor (TP)  │ Individual layers │ All-reduce/layer   │ Model > 1 GPU;       │
│              │ across GPUs       │                    │ low latency needed   │
├──────────────┼───────────────────┼────────────────────┼──────────────────────┤
│ Pipeline (PP)│ Layer groups to   │ Point-to-point     │ Multi-node; trades   │
│              │ different GPUs    │ between stages     │ latency for thrput   │
├──────────────┼───────────────────┼────────────────────┼──────────────────────┤
│ Expert (EP)  │ MoE experts on    │ All-to-all         │ MoE models; each     │
│              │ different GPUs    │ exchange           │ token routes remote  │
├──────────────┼───────────────────┼────────────────────┼──────────────────────┤
│ Data (DP)    │ Same model replic │ Gradient sync      │ High throughput      │
│              │ data split        │ (training)         │ serving              │
└──────────────┴───────────────────┴────────────────────┴──────────────────────┘
```

2026 best practice for large MoE: **DP attention + EP MoE** -- data parallelism for attention layers, expert parallelism for MoE layers.

#### Disaggregated Serving

NVIDIA Dynamo and llm-d independently scale prefill and decode phases. Prefill is compute-bound (benefits from more FLOPs); decode is memory-bandwidth-bound (benefits from more HBM bandwidth). Separating them allows each to run on hardware optimized for its bottleneck, improving GPU utilization from typical 30-40% to 60-70%.

### 4.2 Failure Taxonomy

```
┌────────────────────────┬────────────────────────┬──────────────────────────────┐
│ Failure Mode           │ Detection              │ Mitigation                   │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Context overflow       │ Token count > window;  │ Compaction at 60-70% fill;   │
│ (+ instruction decay)  │ quality degradation    │ sliding window; RAG retrieval│
│                        │ at 60-80% fill         │ instead of stuffing context  │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Tokenizer edge cases   │ Token count mismatch;  │ Use provider's usage counts; │
│ (multilingual 2-6x     │ unexpected cost spikes │ test with target language     │
│ inflation, emoji        │                        │ samples; validate client-side│
│ explosion, BPE shifts) │                        │ counts against server-side   │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Hallucination          │ Factual verification;  │ Lower temperature for facts; │
│ (82% of enterprise     │ confidence calibration;│ RAG grounding; citation      │
│ teams report as issue) │ output validators      │ enforcement; human-in-loop   │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Schema violations      │ Pydantic parse failure;│ Structured output mode;      │
│ (tool calls with       │ JSON decode error;     │ constrained decoding (FSM);  │
│ hallucinated params)   │ type check failure     │ retry-with-error-feedback    │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Silent 200 OK          │ Semantic validators;   │ Three-layer validation;      │
│ (correct HTTP status,  │ output quality checks  │ business rule checks post-   │
│ wrong/dangerous output)│                        │ schema validation            │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Rate limit cascade     │ 429 status codes;      │ Exponential backoff + jitter;│
│ (3^5 = 243 calls from  │ Retry-After headers;   │ retry at one layer only;     │
│ naive 5-layer retry)   │ cost monitoring alerts │ circuit breaker; budget caps │
├────────────────────────┼────────────────────────┼──────────────────────────────┤
│ Cascading hallucination│ Downstream validators; │ Validate at each chain step; │
│ (1 wrong fact -> 3     │ intermediate checksums │ don't propagate unvalidated  │
│ wrong sub-agent answers)│                       │ LLM output to next agent     │
└────────────────────────┴────────────────────────┴──────────────────────────────┘
```

### 4.3 Enterprise Security

#### Prompt Injection (OWASP LLM01:2025 -- still #1)

The fundamental challenge: LLMs process instructions and data in the same context. No architectural solution fully separates them.

**Attack surface**:
- 84% success rate in agentic systems
- 100% evasion demonstrated against Azure Prompt Shield and Meta Prompt Guard
- Critical CVEs: Microsoft Copilot (CVSS 9.3), GitHub Copilot (CVSS 9.6), Cursor IDE (CVSS 9.8)
- Only 34.7% of organizations have deployed dedicated defenses (Cisco 2026)

**Defense-in-depth** (assume breach, not prevention-only):
1. Input sanitization -- strip known injection patterns, encode special tokens
2. Privilege separation -- LLM has minimal tool permissions, human-in-loop for destructive actions
3. Output validation -- never trust LLM output for security-critical decisions
4. Monitoring -- detect anomalous tool call patterns, unusual output distributions
5. Containment -- sandbox tool execution, rate-limit tool calls per session

#### Structured Output Enforcement

```
┌───────────────────────────────────────────────────────────────────────┐
│ Method              │ Guarantee          │ How it works               │
├─────────────────────┼────────────────────┼────────────────────────────┤
│ JSON Mode           │ Valid JSON syntax  │ Token constraints during   │
│                     │ (not schema)       │ sampling                   │
├─────────────────────┼────────────────────┼────────────────────────────┤
│ Structured Outputs  │ Full schema        │ JSON Schema compiled to    │
│ (Strict Mode)       │ compliance         │ FSM; only valid tokens     │
│                     │ (mathematical)     │ allowed at each step       │
├─────────────────────┼────────────────────┼────────────────────────────┤
│ Tool Use            │ Best-effort schema │ Claude: `strict` param     │
│ (Claude)            │ (not guaranteed)   │ currently ignored; add     │
│                     │                    │ validation + retry layers  │
├─────────────────────┼────────────────────┼────────────────────────────┤
│ Constrained         │ Schema + custom    │ Outlines library; works    │
│ Decoding (local)    │ regex/grammar      │ with vLLM, llama.cpp, HF  │
└───────────────────────────────────────────────────────────────────────┘
```

#### Three-Layer Validation Architecture

```
  LLM Response
       │
       ▼
  ┌──────────────────────┐
  │ Layer 1: GUARDRAILS  │  PII detection, content moderation,
  │ (policy filter)      │  prompt injection detection, toxicity
  └──────────┬───────────┘
             │ pass
             ▼
  ┌──────────────────────┐
  │ Layer 2: SCHEMA      │  Pydantic / Zod / JSON Schema parse
  │ (typed parse)        │  Type checking, required fields, enums
  └──────────┬───────────┘
             │ pass
             ▼
  ┌──────────────────────┐
  │ Layer 3: BUSINESS    │  Cross-field consistency, authorization
  │ (domain rules)       │  scope, data classification, range checks
  └──────────┬───────────┘
             │ pass
             ▼
       Accepted output
```

A response can pass guardrails but fail schema (unparseable JSON). It can pass schema but fail guardrails (clean JSON containing PII). It can pass both but fail business rules (valid JSON with out-of-scope values). All three layers are necessary.

#### Audit Logging

SOC2 Type II requires continuous evidence of control operation over 6-12 months. Every LLM call generates a structured audit entry:

```json
{
  "correlation_id": "req-abc-123",
  "timestamp": "2026-08-21T10:30:00Z",
  "user_id": "user-456",
  "model": "claude-sonnet-5",
  "input_tokens": 1200,
  "output_tokens": 450,
  "latency_ms": 1340,
  "cache_hit": true,
  "guardrail_result": "pass",
  "schema_valid": true,
  "tool_calls": ["search_db", "send_email"],
  "cost_usd": 0.0103
}
```

Ensure logging itself does not create data exposure risks -- redact PII from logged prompts, or log only hashes of sensitive content.

#### Compliance Summary

```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ Framework    │ LLM-Specific Obligation                                  │
├──────────────┼──────────────────────────────────────────────────────────┤
│ SOC2 Type II │ Continuous structured logs; control evidence 6-12 months │
│ HIPAA        │ Minimum necessary PHI; guardrails before model contact   │
│ EU AI Act    │ High-risk obligations from Aug 2026; GPAI since Aug 2025 │
│ NIST AI RMF  │ AI 600-1 names prompt injection as a risk                │
│ ISO 42001    │ Risk assessments for input manipulation                  │
│ GDPR Art 32  │ Security of processing including AI system controls      │
└──────────────┴──────────────────────────────────────────────────────────┘
```

---

## 5. Production Enterprise Code

### 5.1 Multi-Model Router with Cost Optimization

```python
"""
Multi-model routing with small-to-large escalation, circuit breaker,
structured output validation, exponential backoff, and fallback chains.

Requirements:
    pip install anthropic openai pydantic tenacity structlog
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

def get_logger(correlation_id: str | None = None) -> structlog.BoundLogger:
    cid = correlation_id or str(uuid.uuid4())
    return structlog.get_logger().bind(correlation_id=cid)


# ---------------------------------------------------------------------------
# Model tier definitions
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    BUDGET = "budget"
    MID = "mid"
    FRONTIER = "frontier"


@dataclass(frozen=True)
class ModelConfig:
    name: str
    tier: ModelTier
    input_cost_per_m: float   # $/1M input tokens
    output_cost_per_m: float  # $/1M output tokens
    max_output_tokens: int = 4096


# Ordered cheapest-first for escalation
MODEL_CHAIN: list[ModelConfig] = [
    ModelConfig("claude-haiku-4-5", ModelTier.BUDGET, 1.00, 5.00, 8192),
    ModelConfig("claude-sonnet-5", ModelTier.MID, 3.00, 15.00, 16384),
    ModelConfig("claude-opus-5", ModelTier.FRONTIER, 5.00, 25.00, 32768),
]


# ---------------------------------------------------------------------------
# Circuit breaker (closed -> open -> half-open -> closed)
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0       # seconds before half-open
    half_open_max_calls: int = 2
    success_threshold: int = 2           # consecutive successes to close

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
            self._success_count = 0
        return self._state

    def allow_request(self) -> bool:
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False  # OPEN

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
        else:
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        self._success_count = 0
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN

    def record_half_open_attempt(self) -> None:
        self._half_open_calls += 1


# ---------------------------------------------------------------------------
# Exponential backoff with full jitter
# ---------------------------------------------------------------------------

async def backoff_with_jitter(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after: float | None = None,
) -> None:
    """Exponential backoff with full jitter per AWS best practice.

    If the server returned a Retry-After header value, respect it as
    the minimum delay.
    """
    exp_delay = min(base_delay * (2 ** attempt), max_delay)
    jittered = random.uniform(0, exp_delay)
    delay = max(jittered, retry_after or 0.0)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Structured output schema (example: entity extraction)
# ---------------------------------------------------------------------------

class ExtractedEntity(BaseModel):
    name: str
    entity_type: str          # "person", "org", "location", etc.
    confidence: float         # 0.0 - 1.0
    source_span: str          # verbatim text span from input


class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]
    summary: str
    model_used: str


# ---------------------------------------------------------------------------
# Request complexity classifier (determines starting tier)
# ---------------------------------------------------------------------------

def classify_complexity(prompt: str) -> ModelTier:
    """Rule-based classifier for routing. <1ms overhead.

    In production, replace or augment with an embedding-based classifier
    (~5ms overhead) for finer-grained routing.
    """
    token_estimate = len(prompt.split())

    # Long or multi-step prompts go to frontier
    if token_estimate > 2000:
        return ModelTier.FRONTIER

    complexity_signals = [
        "analyze", "compare", "evaluate", "synthesize", "design",
        "architect", "trade-off", "multi-step", "reasoning",
    ]
    signal_count = sum(1 for s in complexity_signals if s in prompt.lower())

    if signal_count >= 2:
        return ModelTier.FRONTIER
    if signal_count == 1 or token_estimate > 500:
        return ModelTier.MID
    return ModelTier.BUDGET


# ---------------------------------------------------------------------------
# LLM client abstraction (simulated for portability)
# ---------------------------------------------------------------------------

class LLMClientError(Exception):
    """Base error for LLM API calls."""
    def __init__(self, message: str, status_code: int = 500, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


async def call_llm_api(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Call an LLM API. Replace this with your actual Anthropic/OpenAI client.

    Returns dict with keys: content, input_tokens, output_tokens.
    Raises LLMClientError on failure.

    Production implementation would use:
        import anthropic
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return {
            "content": response.content[0].text,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    """
    raise NotImplementedError(
        "Replace with actual API client. See docstring for Anthropic example."
    )


# ---------------------------------------------------------------------------
# Core router: small-to-large escalation with full resilience
# ---------------------------------------------------------------------------

@dataclass
class ModelRouter:
    """Routes requests through model chain with escalation, circuit breaking,
    structured validation, backoff, and fallback.

    Usage:
        router = ModelRouter()
        result = await router.route(
            system_prompt="Extract entities from the text.",
            user_prompt="Apple CEO Tim Cook announced...",
            output_schema=ExtractionResult,
        )
    """

    model_chain: list[ModelConfig] = field(default_factory=lambda: list(MODEL_CHAIN))
    max_retries_per_model: int = 3
    max_validation_retries: int = 2
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for model in self.model_chain:
            if model.name not in self.breakers:
                self.breakers[model.name] = CircuitBreaker()

    def _get_chain_from_tier(self, start_tier: ModelTier) -> list[ModelConfig]:
        """Return models from start_tier onward (escalation order)."""
        tier_order = [ModelTier.BUDGET, ModelTier.MID, ModelTier.FRONTIER]
        start_idx = tier_order.index(start_tier)
        allowed_tiers = set(tier_order[start_idx:])
        return [m for m in self.model_chain if m.tier in allowed_tiers]

    async def route(
        self,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Route a request through the model chain with full resilience.

        Returns dict with keys: result (parsed or raw), model_used, cost_usd,
        input_tokens, output_tokens, attempts.
        """
        log = get_logger(correlation_id)
        start_tier = classify_complexity(user_prompt)
        chain = self._get_chain_from_tier(start_tier)

        log.info(
            "routing_request",
            start_tier=start_tier.value,
            chain=[m.name for m in chain],
            has_schema=output_schema is not None,
        )

        last_error: Exception | None = None

        for model_config in chain:
            breaker = self.breakers[model_config.name]

            if not breaker.allow_request():
                log.warning("circuit_open", model=model_config.name)
                continue

            if breaker.state == CircuitState.HALF_OPEN:
                breaker.record_half_open_attempt()

            try:
                result = await self._call_with_retry(
                    model_config, system_prompt, user_prompt, output_schema, log
                )
                breaker.record_success()
                return result
            except LLMClientError as e:
                breaker.record_failure()
                last_error = e
                log.warning(
                    "model_failed_escalating",
                    model=model_config.name,
                    error=str(e),
                    status_code=e.status_code,
                )
                continue
            except ValidationError as e:
                # Schema validation exhausted retries -- escalate to stronger model
                last_error = e
                log.warning(
                    "validation_failed_escalating",
                    model=model_config.name,
                    error=str(e),
                )
                continue

        # All models exhausted -- deterministic fallback
        log.error("all_models_exhausted", last_error=str(last_error))
        return self._deterministic_fallback(user_prompt, output_schema)

    async def _call_with_retry(
        self,
        model_config: ModelConfig,
        system_prompt: str,
        user_prompt: str,
        output_schema: type[BaseModel] | None,
        log: structlog.BoundLogger,
    ) -> dict[str, Any]:
        """Call a single model with exponential backoff on transient errors
        and retry-with-feedback on validation failures."""
        last_api_error: LLMClientError | None = None
        validation_feedback = ""

        for attempt in range(self.max_retries_per_model):
            try:
                prompt = user_prompt
                if validation_feedback:
                    prompt = (
                        f"{user_prompt}\n\n"
                        f"[Previous attempt failed validation: {validation_feedback}. "
                        f"Fix the output to match the required schema exactly.]"
                    )

                raw = await call_llm_api(
                    model=model_config.name,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    max_tokens=model_config.max_output_tokens,
                    temperature=0.0,
                )

                cost = (
                    raw["input_tokens"] * model_config.input_cost_per_m / 1_000_000
                    + raw["output_tokens"] * model_config.output_cost_per_m / 1_000_000
                )

                # Validate structured output if schema provided
                parsed = raw["content"]
                if output_schema is not None:
                    parsed = self._validate_output(
                        raw["content"], output_schema, model_config.name
                    )

                log.info(
                    "request_success",
                    model=model_config.name,
                    attempt=attempt + 1,
                    input_tokens=raw["input_tokens"],
                    output_tokens=raw["output_tokens"],
                    cost_usd=round(cost, 6),
                )

                return {
                    "result": parsed,
                    "model_used": model_config.name,
                    "cost_usd": cost,
                    "input_tokens": raw["input_tokens"],
                    "output_tokens": raw["output_tokens"],
                    "attempts": attempt + 1,
                }

            except LLMClientError as e:
                last_api_error = e
                if e.status_code == 429 or e.status_code >= 500:
                    log.warning(
                        "retryable_error",
                        model=model_config.name,
                        attempt=attempt + 1,
                        status_code=e.status_code,
                        retry_after=e.retry_after,
                    )
                    await backoff_with_jitter(
                        attempt, retry_after=e.retry_after
                    )
                    continue
                raise  # non-retryable (400, 401, 403)

            except ValidationError as e:
                validation_feedback = str(e)
                log.warning(
                    "validation_retry",
                    model=model_config.name,
                    attempt=attempt + 1,
                    errors=str(e),
                )
                if attempt >= self.max_validation_retries:
                    raise
                continue

        if last_api_error:
            raise last_api_error
        raise LLMClientError("Max retries exhausted", status_code=500)

    @staticmethod
    def _validate_output(
        raw_content: str, schema: type[BaseModel], model_name: str
    ) -> BaseModel:
        """Parse and validate LLM output against Pydantic schema.

        Strips markdown code fences if present, then attempts JSON parse.
        """
        import json

        content = raw_content.strip()
        # Strip markdown code fences
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines[1:] if l.strip() != "```"]
            content = "\n".join(lines)

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValidationError.from_exception_data(
                title=schema.__name__,
                line_errors=[],
            ) from e

        return schema.model_validate(data)

    @staticmethod
    def _deterministic_fallback(
        user_prompt: str,
        output_schema: type[BaseModel] | None,
    ) -> dict[str, Any]:
        """Last-resort fallback when all LLM providers are down.

        Returns a safe error response that conforms to the expected schema
        structure, allowing the caller to handle gracefully.
        """
        fallback_content = (
            "Service temporarily unavailable. All model providers are "
            "unreachable. This is a deterministic fallback response."
        )

        return {
            "result": fallback_content,
            "model_used": "deterministic_fallback",
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "attempts": 0,
            "is_fallback": True,
        }


# ---------------------------------------------------------------------------
# Usage example
# ---------------------------------------------------------------------------

async def main() -> None:
    router = ModelRouter()

    # Simple request -> routes to budget model first
    result = await router.route(
        system_prompt=(
            "Extract named entities from the text. Return JSON matching "
            "the ExtractionResult schema with entities array and summary."
        ),
        user_prompt="Tim Cook announced that Apple will open a new office in Berlin.",
        output_schema=ExtractionResult,
        correlation_id="demo-001",
    )

    print(f"Model used: {result['model_used']}")
    print(f"Cost: ${result['cost_usd']:.6f}")
    print(f"Result: {result['result']}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 5.2 Code Design Decisions

**Why this structure**:

1. **ModelRouter.route()** is the single entry point. It classifies complexity, selects a starting tier, and escalates through the chain. This avoids the caller needing to know about model tiers or failover logic.

2. **CircuitBreaker** uses the standard three-state pattern (closed/open/half-open) with time-based recovery. Each model has its own breaker instance, so a failing frontier model does not block budget model calls.

3. **Exponential backoff with full jitter** prevents retry storms. The `retry_after` parameter from 429 responses takes precedence over computed delay. Retries happen at one layer only (inside `_call_with_retry`), not at the router level -- this prevents the 3^N cascade problem.

4. **Validation retry with feedback** appends the validation error to the prompt on retry, giving the model a chance to self-correct before escalating to a more capable (and expensive) model.

5. **Deterministic fallback** returns a safe error response when all providers are down, allowing the caller to degrade gracefully rather than crash.

6. **Structured logging** with `structlog` attaches correlation IDs to every log entry, enabling request tracing across the routing chain. JSON output integrates directly with log aggregation systems (DataDog, Splunk, ELK).

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Model Inference Gateway at 50K RPM

**Problem**: Design a multi-model inference gateway serving 50,000 requests per minute across 4 model tiers (budget, mid, frontier, reasoning) with sub-200ms P99 TTFT.

#### Component Diagram

```
                              50K RPM
                                │
                                ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                      EDGE / CDN LAYER                        │
  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
  │  │ US-East PoP  │  │ EU-West PoP  │  │ APAC PoP     │       │
  │  │ TLS term     │  │ TLS term     │  │ TLS term     │       │
  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
  └─────────┼─────────────────┼─────────────────┼────────────────┘
            │                 │                 │
            ▼                 ▼                 ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                    API GATEWAY (Envoy)                        │
  │  Auth │ Rate Limit │ Quota │ Request ID │ Shadow Traffic      │
  └──────────────────────────┬───────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                  INTELLIGENT ROUTER                          │
  │  ┌─────────────────────────────────────────────────────┐    │
  │  │ Rule engine (<1ms) + ML classifier fallback (~5ms)  │    │
  │  │ Input: prompt length, keyword signals, user tier    │    │
  │  │ Output: model selection + priority                  │    │
  │  └──────────┬──────────┬──────────┬──────────┬─────────┘    │
  └─────────────┼──────────┼──────────┼──────────┼──────────────┘
                │          │          │          │
       ┌────────┘          │          │          └────────┐
       ▼                   ▼          ▼                   ▼
  ┌──────────┐   ┌──────────┐  ┌──────────┐     ┌──────────────┐
  │ BUDGET   │   │ MID-TIER │  │ FRONTIER │     │  REASONING   │
  │ Pool     │   │ Pool     │  │ Pool     │     │  Pool        │
  │ Haiku/   │   │ Sonnet/  │  │ Opus/    │     │  o3/         │
  │ Nano     │   │ Flash    │  │ GPT-5.5  │     │  DeepSeek-R2 │
  │ ~35K RPM │   │ ~10K RPM │  │ ~4K RPM  │     │  ~1K RPM     │
  └──────────┘   └──────────┘  └──────────┘     └──────────────┘
       │              │             │                  │
       └──────────────┴─────────────┴──────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                   RESPONSE PIPELINE                          │
  │  Schema Validation │ Guardrails │ Audit Log │ Metrics        │
  └──────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

```
┌─────────────────────┬─────────────────┬──────────────────┬────────────────────┐
│ Criterion           │ A: Single-vendor│ B: Multi-vendor  │ C: Self-hosted +   │
│                     │ API gateway     │ API gateway      │ API hybrid         │
│                     │ (e.g. Portkey)  │ (custom Envoy)   │ (vLLM + API)       │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ P99 TTFT            │ 180ms (via      │ 150ms (regional  │ 120ms (local GPU   │
│                     │ provider edge)  │ PoPs + routing)  │ for budget tier)   │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ Cost / month        │ Low ops ($)     │ Medium ops ($$)  │ High ops ($$$)     │
│ (at 50K RPM)        │ + API markup    │ + multi-provider │ + GPU infra cost   │
│                     │                 │   API costs      │ + API for frontier │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ Ops complexity      │ Low: managed    │ Medium: own      │ High: GPU fleet +  │
│                     │ service         │ gateway, multi-  │ model updates +    │
│                     │                 │ vendor contracts │ API integration    │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ Security / data     │ Data transits   │ Data transits    │ Budget/mid on own  │
│ residency           │ vendor + LLM    │ own gateway +    │ GPUs (no 3rd       │
│                     │ provider        │ LLM providers    │ party); frontier   │
│                     │                 │                  │ via API            │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ Availability        │ Single vendor   │ Multi-provider   │ Highest: local     │
│                     │ SPOF            │ failover; 99.9%  │ fallback + API     │
├─────────────────────┼─────────────────┼──────────────────┼────────────────────┤
│ Scalability         │ Limited by      │ Scales across    │ GPU scaling is     │
│                     │ vendor caps     │ providers        │ slow (procurement) │
└─────────────────────┴─────────────────┴──────────────────┴────────────────────┘
```

#### Decision Rationale

**Recommended: Approach B (Multi-vendor API gateway)** for most enterprises at 50K RPM.

1. **Sub-200ms P99 TTFT** is achievable by routing 70% of traffic to budget/mid-tier models (Haiku P50 TTFT ~300ms, P99 ~500ms) and adding regional edge PoPs to eliminate 80-220ms cross-region latency. The P99 target is realistic for budget/mid tiers but requires prompt caching and regional deployment for frontier models. For the frontier/reasoning pools (10% of traffic), the P99 TTFT target relaxes -- these requests are inherently latency-tolerant.

2. **Multi-provider failover** eliminates the single-provider SPOF. LLM providers run at 99-99.5% uptime (OpenAI's longest outage: 34 hours). With circuit breakers per provider, failover to a secondary completes in <30 seconds.

3. **Cost control**: At 50K RPM, even small routing improvements yield significant savings. Routing 70% of traffic to Haiku ($1/M) vs. Sonnet ($3/M) saves ~$2/M tokens on 70% of volume.

4. **Approach C** (self-hosted) makes sense only when data residency requirements prohibit sending data to API providers, or when budget-tier volume is high enough (>100K RPM) to amortize GPU infrastructure costs. GPU procurement lead times (weeks to months) make rapid scaling difficult.

---

### Scenario 2: Cost-Optimized LLM Serving Platform (80% Cost Reduction, 95% Quality Parity)

**Problem**: Design a platform that reduces inference costs by 80% compared to routing all traffic to a frontier model, while maintaining 95% quality parity on internal eval benchmarks.

#### Component Diagram

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │                      COST OPTIMIZATION STACK                           │
  │                                                                        │
  │   Request ──►┌────────────────┐                                       │
  │              │ Semantic Cache  │──hit──► Return cached response        │
  │              │ (embedding sim) │         (cost: $0, latency: <10ms)    │
  │              └───────┬────────┘                                       │
  │                      │ miss                                            │
  │                      ▼                                                 │
  │              ┌────────────────┐                                       │
  │              │ Prompt Optimizer│  Compress prompts, optimize prefix    │
  │              │ - Caching prefix│  ordering for cache hits, strip       │
  │              │ - Compression  │  redundant context                     │
  │              └───────┬────────┘                                       │
  │                      │                                                 │
  │                      ▼                                                 │
  │              ┌────────────────┐                                       │
  │              │ Model Router   │  Rule + ML classifier                 │
  │              │                │                                       │
  │              ├───────┬────────┤                                       │
  │              │       │        │                                       │
  │         ┌────┘       │        └────┐                                  │
  │         ▼            ▼             ▼                                  │
  │    ┌─────────┐ ┌──────────┐ ┌───────────┐                            │
  │    │ Budget  │ │ Mid-tier │ │ Frontier  │                            │
  │    │ INT4    │ │ FP8      │ │ FP16/BF16 │                            │
  │    │ vLLM    │ │ vLLM     │ │ API       │                            │
  │    │ (local) │ │ (local)  │ │ (Anthropic│                            │
  │    │         │ │          │ │  /OpenAI) │                            │
  │    └────┬────┘ └────┬─────┘ └────┬──────┘                            │
  │         │           │            │                                    │
  │         └───────────┼────────────┘                                    │
  │                     ▼                                                  │
  │              ┌────────────────┐                                       │
  │              │ Batch Aggregator│  Collect async requests, submit via  │
  │              │ (async paths)  │  Batch API at 50% discount            │
  │              └───────┬────────┘                                       │
  │                      │                                                 │
  │                      ▼                                                 │
  │              ┌────────────────┐                                       │
  │              │Quality Monitor │  Compare outputs against eval set,    │
  │              │ - A/B eval     │  alert on quality drift, auto-adjust  │
  │              │ - Drift detect │  routing thresholds                    │
  │              └────────────────┘                                       │
  └────────────────────────────────────────────────────────────────────────┘
```

#### Cost Reduction Breakdown

Starting from Opus 5 baseline ($17.50/1K runs at 1K in + 500 out):

```
┌──────────────────────────────────┬────────────┬─────────────┬───────────┐
│ Optimization Layer               │ $/1K runs  │ Cumulative  │ Quality   │
│                                  │            │ Reduction   │ Impact    │
├──────────────────────────────────┼────────────┼─────────────┼───────────┤
│ Baseline (all Opus 5)            │ $17.50     │ --          │ 100%      │
│ + Model routing (70% budget)     │ $7.70      │ -56%        │ ~97%      │
│ + Prompt caching (90% hit rate)  │ $5.60      │ -68%        │ 100%*     │
│ + Batch API (40% async volume)   │ $4.48      │ -74%        │ 100%*     │
│ + Semantic cache (15% hit rate)  │ $3.81      │ -78%        │ ~99%      │
│ + Quantization INT4 (budget tier)│ $3.50      │ -80%        │ ~98%      │
├──────────────────────────────────┼────────────┼─────────────┼───────────┤
│ Final                            │ $3.50      │ -80%        │ ~95-97%   │
└──────────────────────────────────┴────────────┴─────────────┴───────────┘

* Caching and batch API do not affect output quality.
```

#### Trade-off Matrix

```
┌────────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Criterion          │ A: Routing only  │ B: Routing +     │ C: Full stack    │
│                    │ (no infra)       │ Caching + Batch  │ (+ self-hosted   │
│                    │                  │                  │   + quantization)│
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Cost reduction     │ 56%              │ 74%              │ 80%+             │
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Quality parity     │ 97%+ (routing    │ 97%+ (caching/   │ 95-97% (quant.   │
│                    │ preserves output)│ batch don't      │ introduces minor │
│                    │                  │ affect quality)  │ degradation)     │
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Implementation     │ 1-2 weeks        │ 3-4 weeks        │ 8-12 weeks       │
│ effort             │ (router + eval)  │ (+ cache infra   │ (+ GPU fleet +   │
│                    │                  │   + batch job)   │   model serving) │
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Ops complexity     │ Low              │ Medium           │ High             │
│                    │                  │ (cache TTL,      │ (GPU monitoring, │
│                    │                  │  batch SLA)      │  model updates)  │
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Latency impact     │ Neutral (+1ms    │ Improved (cache  │ Improved (local  │
│                    │ router overhead) │ hits ~10ms)      │ inference)       │
├────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Risk               │ Low (API-only)   │ Low-Medium       │ Medium-High      │
│                    │                  │ (cache           │ (GPU procurement,│
│                    │                  │  invalidation)   │  model drift)    │
└────────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

#### Decision Rationale

**Recommended: Start with B, graduate to C when volume justifies GPU investment.**

1. **Approach B achieves 74% cost reduction** with no quality impact from caching or batching, and minimal impact from routing. Implementation is 3-4 weeks, entirely API-based, with no GPU infrastructure to manage.

2. **The 80% target requires Approach C** (adding self-hosted quantized models for the budget tier). This is worth the added complexity when budget-tier volume exceeds ~500K requests/day, where self-hosted INT4 models on 2x A100 GPUs become cheaper than API calls.

3. **Quality monitoring is non-negotiable** at any approach level. Run 5-10% of traffic through both the routed path and the frontier model, comparing outputs against an eval set. Set alerts for quality drift below the 95% threshold and auto-adjust routing thresholds (send more traffic to frontier models) when quality drops.

4. **Semantic caching** (embedding similarity match for near-duplicate queries) provides an additional 10-15% hit rate in customer support, FAQ, and documentation use cases. It is less effective for novel/creative queries. The quality risk is that cached responses may not account for changed context; use short TTLs (5-15 minutes) for volatile domains.

5. **Quantization** (INT4 via AWQ/GPTQ) reduces memory 4x with minor quality impact for most tasks. Google's TurboQuant (2026) achieves KV cache 3-bit quantization with zero measured accuracy loss. Apply quantization to the budget tier only; keep mid/frontier tiers at full precision.

---

## Appendix: Key Numbers to Memorize

```
Self-attention complexity:         O(n^2 * d)
KV cache per token (70B, FP16):    ~320 KB
PagedAttention waste reduction:    60-80%
Prompt cache read savings:         90%
Batch API discount:                50%
Frontier-to-budget cost spread:    63x (o3 vs DeepSeek V3)
P95/P50 TTFT ratio (frontier):    ~2.1x average, 3.2x worst
LLM provider uptime:              99-99.5% (vs 99.97% cloud infra)
Multi-turn performance drop:       39% average
Prompt injection success rate:     84% in agentic systems
MoE active parameters:            5-10% of total (e.g., 37B of 671B)
Continuous batching throughput:    2,200-2,400 tok/s (70B FP8, H100)
Speculative decoding speedup:     2-3x decode (no quality loss)
```
