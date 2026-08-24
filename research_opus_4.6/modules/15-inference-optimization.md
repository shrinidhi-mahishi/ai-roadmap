# Module 15: Inference & Optimization — Serving Infrastructure, Quantization, Caching, Hardware, and Cost Optimization

**Scope**: Model serving engines (vLLM, SGLang, TensorRT-LLM), quantization (FP8, NVFP4, AWQ, GPTQ, GGUF), KV cache optimization (PagedAttention, prefix caching), speculative decoding, prompt caching, batching strategies (continuous, chunked prefill), hardware landscape (NVIDIA B200, AMD MI355X, Groq LPU, Cerebras WSE-3, AWS Trainium3), cost optimization (routing, cascade, distillation), and edge/on-device inference (llama.cpp, MLX, ExecuTorch).
**Prerequisite**: Module 1 (LLM Foundations).
**Last updated**: 2026-08-21 | **Sources consulted**: 80

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Model Router    │  │  Autoscaler      │  │  Budget          │  │  SLO             │  │
 │  │  - Complexity    │  │  - GPU pool      │  │  Controller      │  │  Enforcer        │  │
 │  │    classifier    │  │  - Queue depth   │  │  - $/user cap    │  │  - TTFT target   │  │
 │  │  - Cascade logic │  │  - TTFT-driven   │  │  - $/feature     │  │  - TPOT target   │  │
 │  │  - RouteLLM /    │  │  - Prefill vs    │  │  - Monthly       │  │  - Goodput       │  │
 │  │    FrugalGPT     │  │    decode pools  │  │    budget cap    │  │    tracking      │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                          DATA PLANE: INFERENCE PIPELINE                             │    │
 │  │                                                                                    │    │
 │  │  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────────────┐   │    │
 │  │  │ Request     │   │ Prefix Cache │   │ Continuous   │   │ Speculative       │   │    │
 │  │  │ Admission   │──▶│ Lookup       │──▶│ Batcher      │──▶│ Decode Engine     │   │    │
 │  │  │ - Rate limit│   │ - Exact match│   │ - In-flight  │   │ - Draft model     │   │    │
 │  │  │ - Priority  │   │ - Radix tree │   │ - Chunked    │   │ - EAGLE-3 heads   │   │    │
 │  │  │   queue     │   │ - Semantic   │   │   prefill    │   │ - Verify + accept │   │    │
 │  │  │ - Budget    │   │   cache (opt)│   │ - PagedAttn  │   │ - 2-3× speedup    │   │    │
 │  │  │   check     │   │ - 85-95%     │   │ - BucketServe│   │                   │   │    │
 │  │  │             │   │   cost save  │   │   (opt)      │   │                   │   │    │
 │  │  └─────────────┘   └──────────────┘   └──────────────┘   └───────────────────┘   │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  DISAGGREGATED SERVING (models >13B at batch >256)                      │      │    │
 │  │  │  ┌──────────────────┐        ┌──────────────────┐                       │      │    │
 │  │  │  │ PREFILL POOL     │──KV──▶│ DECODE POOL      │                       │      │    │
 │  │  │  │ (compute-bound)  │ xfer  │ (memory-bound)   │                       │      │    │
 │  │  │  │ - H100/B200      │       │ - H200/B200      │                       │      │    │
 │  │  │  │ - High batch     │       │ - High bandwidth │                       │      │    │
 │  │  │  │ - 80%+ GPU util  │       │ - Long-running   │                       │      │    │
 │  │  │  └──────────────────┘       └──────────────────┘                       │      │    │
 │  │  │  DistServe: 7.4× more req/GPU, 12.6× tighter SLO compliance           │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  QUANTIZATION ENGINE                                                    │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ FP8 (default)│  │ NVFP4        │  │ AWQ/GPTQ     │  │ GGUF       │  │      │    │
 │  │  │  │ H100/B200    │  │ Blackwell    │  │ INT4 GPU     │  │ CPU/Apple  │  │      │    │
 │  │  │  │ ~99.5% qual  │  │ ~99% quality │  │ ~93-95% qual │  │ ~92% qual  │  │      │    │
 │  │  │  │ 1.4-1.7× thr │  │ ~2× over FP8│  │ Marlin 2.6×  │  │ Edge focus │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ AI Gateway    │  │ Model Registry│  │ Tokenizer     │  │ Health        │       │    │
 │  │  │ (LiteLLM/     │  │ (weights,     │  │ Service       │  │ Monitor       │       │    │
 │  │  │  Portkey)     │  │  quant config,│  │ - Token count │  │ - GPU util    │       │    │
 │  │  │ - Cost track  │  │  draft models)│  │ - Cost est.   │  │ - KV cache    │       │    │
 │  │  │ - Failover    │  │ - Version     │  │ - Cache key   │  │ - Queue depth │       │    │
 │  │  │ - Rate limit  │  │   pinning     │  │   generation  │  │ - TTFT/TPOT   │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ KV Cache Store    │  │ Model Weights     │  │ Prefix Cache      │  │ Cost Ledger    │  │
 │  │ (GPU HBM)         │  │ (NVMe / HBM)      │  │ (GPU + CPU DRAM)  │  │ - Per-request  │  │
 │  │ - PagedAttention  │  │ - Quantized wts   │  │ - Radix tree      │  │ - Per-model    │  │
 │  │ - <4% waste       │  │ - Draft model wts │  │ - LRU eviction    │  │ - Per-user     │  │
 │  │ - FP8 KV quant    │  │ - LoRA adapters   │  │ - Semantic index  │  │ - Budget state │  │
 │  │ - Copy-on-write   │  │                   │  │                   │  │                │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Latency           │  │ Throughput        │  │ Hardware          │  │ Cost            │  │
 │  │ - TTFT histogram  │  │ - Tokens/sec      │  │ - GPU utilization │  │ - $/request     │  │
 │  │ - TPOT histogram  │  │ - Requests/sec    │  │ - KV cache usage  │  │ - $/user        │  │
 │  │ - ITL jitter      │  │ - Goodput         │  │ - Memory pressure │  │ - Model tier    │  │
 │  │ - E2E latency     │  │ - Batch occupancy │  │ - Power draw      │  │   distribution  │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Admission & Routing**: The **Model Router** classifies query complexity (static rules → cascade → classifier-based). Simple queries route to budget models (Haiku, Flash, $0.05–0.10/MTok); complex queries route to frontier models (Opus, Sol, $5–25/MTok). The **Budget Controller** checks per-user daily caps before admission.

**Step 2 — Prefix Cache Lookup**: The request prefix is hashed against the **Prefix Cache**. On a cache hit (system prompt, tool definitions, reference documents), pre-computed KV tensors are reused — saving 85–95% of input cost and reducing TTFT by 13–31%. SGLang uses RadixAttention (radix tree with LRU eviction); vLLM uses hash-based block matching.

**Step 3 — Batching & Scheduling**: The **Continuous Batcher** adds the request to an in-flight batch. PagedAttention allocates KV cache pages dynamically (<4% waste vs. 60–80% with static allocation). Chunked prefill splits long prompts into smaller ranges, preventing prefill from blocking ongoing decode operations.

**Step 4 — Inference Execution**: For models >13B at high concurrency, the **Disaggregated Serving** architecture separates compute-bound prefill (GPU pool optimized for batch throughput) from memory-bound decode (GPU pool optimized for bandwidth). KV cache transfers use FlowKV (96% reduction in transfer latency). For standard serving, monolithic engines (vLLM, SGLang) handle both phases on the same GPU.

**Step 5 — Speculative Decode**: The **Speculative Decode Engine** uses a lightweight draft model (or EAGLE-3 prediction head) to propose multiple candidate tokens. The target model verifies all candidates in a single forward pass — 2–3× decode speedup with mathematically identical output quality. Acceptance rates: 60–80% for standard chat, 75–85% for code.

**Step 6 — Quantized Execution**: Weights execute at the optimal precision for the hardware: FP8 on H100/H200 (1.4–1.7× throughput, 99.5% quality), NVFP4 on Blackwell (2× over FP8, 99% quality), or INT4 AWQ/GPTQ with Marlin kernels (2.6–10.9× speedup). KV cache is independently quantized to FP8, halving cache memory.

---

## 2. Core Mechanics & Algorithms

### 2.1 Model Serving Engine Comparison

| Criterion | vLLM | SGLang | TensorRT-LLM |
|-----------|------|--------|---------------|
| **Best for** | Multi-model, rapid iteration | Prefix-heavy, multi-turn, RAG | Max throughput on NVIDIA |
| **Hardware** | NVIDIA, AMD, Intel, TPU, Trainium | NVIDIA, AMD | NVIDIA only |
| **Throughput (8B, H100)** | ~12,500 tok/s | ~16,200 tok/s (+29%) | ~13,500 tok/s (+8%) |
| **Throughput (70B, H100)** | Baseline | +3–5% | +30–50% (high concurrency) |
| **TTFT (70B, dual H100)** | ~210ms p50 | ~190ms p50 | ~180ms p50 |
| **Setup time** | Minutes | Minutes | Hours to days (compilation) |
| **Model flexibility** | Highest | High | Low (recompile on change) |
| **Speculative decode** | EAGLE-3 | EAGLE, MTP | Native |
| **Prefix caching** | Hash-based block | RadixAttention (radix tree) | Supported |
| **GPU utilization (100+ users)** | 85–92% | 85–92% | 88–95% |
| **Production users** | Default for most teams | xAI, NVIDIA, LinkedIn (400K+ GPUs) | Perplexity, cloud providers |
| **TGI (deprecated)** | — | — | — |

**TGI**: Entered maintenance mode December 2025. Hugging Face Inference Endpoints now default to vLLM. Not recommended for new deployments.

### 2.2 Quantization Techniques

| Method | Bit Width | Quality Retention | Throughput Gain | Memory Reduction | Hardware Req | Best For |
|--------|:---------:|:-----------------:|:---------------:|:----------------:|:------------:|---------|
| **FP8** | 8-bit | ~99.5% (0.3–0.6pt loss) | 1.4–1.7× | 2× vs FP16 | H100+ | 2026 production default |
| **NVFP4** | 4-bit | ~99% (<1pt loss) | ~2× over FP8 | 3.5× vs FP16 | Blackwell only | B200 VRAM-constrained |
| **AWQ** | 4-bit | ~95–96% (0.5–1.5pt > GPTQ) | 2.6–3.1× (Marlin) | ~60% | Any GPU | Reasoning, agents |
| **GPTQ** | 4-bit | ~93–95% | 2.6× (Marlin) | ~60% | Any GPU | Multi-LoRA deployments |
| **GGUF Q4_K_M** | 4-bit | ~92% | N/A (CPU focus) | ~75% | CPU/Apple Silicon | Ollama, edge |
| **INT8** | 8-bit | ~99% | ~1.5× | 2× vs FP16 | Cross-platform | Fallback option |
| **BitNet b1.58** | 1-bit | Outperforms 1B peers | CPU-native (29ms decode) | 0.4GB for 2B params | CPU | Ultra-edge, research |

**Key insight**: Marlin kernels provide 2.6–10.9× speedup — the kernel choice matters as much as the quantization algorithm. AWQ + Marlin achieves 741 tok/s on Qwen2.5-32B; base AWQ achieves 68 tok/s (10.9× gap).

### 2.3 KV Cache Optimization

| Technique | Mechanism | Impact |
|-----------|-----------|--------|
| **PagedAttention** | OS-style virtual memory paging for KV cache | <4% waste (vs. 60–80% static); universal default |
| **Prefix caching** | Share KV pages for identical prefixes | 85–95% cost savings on hits |
| **KV cache quantization** | FP8 for KV tensors (independent of weight quant) | 2× cache memory reduction; negligible quality loss |
| **Copy-on-write** | Duplicate only modified blocks, not entire cache | Efficient beam search and parallel sampling |
| **RadixAttention** (SGLang) | Radix tree with LRU for automatic KV reuse | 20–40% TTFT reduction for prefix-heavy workloads |
| **PagedEviction** | Block-wise eviction of low-importance blocks | Extends effective context window |
| **Cache-aware routing** | Route prefix-sharing requests to same replica | Maximizes hit rates in distributed deployments |
| **Oneiros** | Multi-tenant KV remapping | Up to 86.7% higher throughput than vLLM |

### 2.4 Speculative Decoding

**Core guarantee**: Accepted tokens follow the exact same probability distribution as standard autoregressive decoding. Output quality is mathematically identical.

| Variant | Mechanism | Acceptance Rate | Speedup |
|---------|-----------|:--------------:|:-------:|
| **EAGLE-3** | Prediction head on target model's internal layers | 60–80% | 2–3× |
| **P-EAGLE** | Current SOTA (2026) | Higher than EAGLE-3 | 2–3× |
| **N-gram** | Pattern matching in recent context | Variable (high for code) | 1.5–2× |
| **Draft model** | Separate smaller model proposes tokens | 50–75% | 1.5–2.5× |

**When speculative decoding helps vs. hurts**:

| Workload | Acceptance Rate | Recommendation |
|----------|:--------------:|---------------|
| Code with patterns | 75–85% | Always use |
| Structured data / formal writing | 75–85% | Always use |
| Standard chat | 60–80% | Use (net positive) |
| Creative writing | 40–60% | Test carefully |
| Open-ended reasoning | 40–60% | May not help |

### 2.5 Prompt Caching

| Provider | Pricing Discount | Mechanism | Min Prefix |
|----------|:----------------:|-----------|:----------:|
| **Anthropic** | 90% on cached reads | Automatic + explicit `cache_control` | 1,024 tokens |
| **OpenAI** | Up to 90% (newer models) | Automatic on prompts >1,024 tokens | 1,024 tokens |
| **Google** | Varies | Explicit + implicit (Gemini 2.5) | Varies |

**Production results**: ProjectDiscovery cut LLM spend 59–70% by raising cache hit rate from 7% to 84%. Agentic tasks see 41–80% API cost reduction. Customer support app with 10K-token system+RAG context at 95% hit rate: $10K/month → $2.4K/month (76% reduction).

**Multi-tier caching architecture**:
```
Request → Semantic Cache (100% savings, 20-45% hit rate)
        → Prefix Cache   (50-90% savings, exact match)
        → Full Inference
```

### 2.6 Batching Strategies

| Strategy | Throughput Impact | Status (2026) |
|----------|:-----------------:|:-----:|
| **Static batching** | Baseline (wastes 50–70% compute) | Legacy; never for LLMs |
| **Dynamic batching** | 2–5× over static | Superseded |
| **Continuous batching** | Up to 36.9× over static; 23× with vLLM | Universal default |
| **Chunked prefill** | Prevents prefill interference with decode | Standard complement to continuous |
| **BucketServe** | 3.58× throughput over baseline; 2× load capacity | Emerging (2026) |
| **Dynamic prefix bucketing** | 50.7% speedup | Emerging (2026) |

### 2.7 Disaggregated Inference

Separates compute-bound prefill from memory-bound decode onto separate GPU pools.

| Metric | Monolithic | Disaggregated | Improvement |
|--------|:----------:|:-------------:|:-----------:|
| Requests/GPU | Baseline | 7.4× (DistServe) | 7.4× |
| SLO compliance | Baseline | 12.6× tighter | 12.6× |
| GPU utilization | ~40% | 80%+ | 2× |
| TTFT | Baseline | 88% faster | — |
| Cost/request | Baseline | 30–50% lower | — |

**When to use**: Models >13B at batch sizes >256. Below 7B, network transfer overhead outweighs benefits.

**Production frameworks**: NVIDIA Dynamo, llm-d (Red Hat + IBM + Google, CNCF), Mooncake (100B+ tokens/day at Kimi).

### 2.8 Hardware Landscape

| Accelerator | Compute | Memory | Bandwidth | Strength | $/hr (cloud) |
|-------------|:-------:|:------:|:---------:|----------|:-----------:|
| **NVIDIA H100** | 1,979 TFLOPS FP8 | 80GB HBM3 | 3.35 TB/s | Ecosystem maturity | $2.85–3.50 |
| **NVIDIA H200** | Same as H100 | 141GB HBM3e | 4.8 TB/s | Memory for large models | $3.50–4.50 |
| **NVIDIA B200** | 9,000 TFLOPS FP4 | 192GB HBM3e | 8.0 TB/s | 7× cost reduction vs H100 | $5.91–16.11 |
| **AMD MI300X** | 1,300 BF16 TFLOPS | 192GB HBM3 | — | Better $/TFLOP | ~$2.20 |
| **AMD MI355X** | 10.1 PFLOPS MXFP4 | 288GB HBM3E | — | 4× MI300X performance | Not yet |
| **Groq LPU** | Custom | On-chip SRAM | Deterministic | <100ms TTFT, 840 tok/s (8B) | API pricing |
| **Cerebras WSE-3** | 125 PFLOPS | 44GB SRAM | 7,000× H100 | 969 tok/s on 405B | API pricing |
| **AWS Trainium3** | 2.52 PFLOPS FP8 | 144GB HBM3e | 4.9 TB/s | 50% cost reduction claim | AWS pricing |
| **Google TPU v7** | 4,614 FP8 TFLOPS | 192GB HBM3E | 7.37 TB/s | GCP-native | GCP pricing |

**B200 concrete benchmarks**: Llama 3.3 70B at 10,000+ TPS per GPU — 4× H200 per-GPU throughput. Cost per million tokens dropped from $0.11 to $0.02 in two months (software optimization). $5M in GB200 NVL72 generates $75M in documented token revenue (15× ROI).

### 2.9 Cost Optimization Strategies

| Strategy | Cost Reduction | Quality Impact | Implementation Complexity |
|----------|:-------------:|:--------------:|:------------------------:|
| **Prompt caching** | 50–90% (input) | 0% | Low (API parameter) |
| **Model routing** | 85% (RouteLLM) | <5% (95% of GPT-4) | Medium (classifier) |
| **Cascade** | 50–98% (FrugalGPT) | Variable (requires monitoring) | Medium |
| **Batch API** | 50% (most providers) | 0% | Low (non-urgent only) |
| **Distillation** | 75–95% | 3–5% | High (training pipeline) |
| **Self-hosting** | 50–80% (>50M tok/day) | 0% | High (infra + ops) |

### 2.10 Edge / On-Device Inference

| Runtime | Platform | Strength | 7B Q4 Performance |
|---------|----------|----------|:------------------:|
| **llama.cpp** | All (CPU/GPU/Metal) | Most portable; 91K GitHub stars | 20–30 tok/s (laptop CPU) |
| **MLX** | Apple Silicon only | 30–50% faster than llama.cpp on Mac | 75–90 tok/s (M4 Max) |
| **ExecuTorch** | Mobile (iOS/Android) | 50KB runtime; 12+ backends; billions of users | Device-dependent |
| **LiteRT-LM** | All (Android/iOS/Web) | Powers Chrome, Pixel, Chromebook | Device-dependent |

**Apple Silicon sweet spots**: M4 Pro (24–48GB) handles 8B–22B models. M4 Max (128GB) runs 70B at Q4 comfortably. Ollama switching to MLX engine on Apple Silicon (March 2026 preview).

**Mobile AI silicon**:

| Chipset | NPU Performance | LLM Capability | Key Feature |
|---------|:--------------:|----------------|-------------|
| **Qualcomm Snapdragon 8 Elite** | 45 TOPS | 7B at 20+ tok/s | QMX (Qualcomm Matrix Extension); Hexagon NPU; ExecuTorch QNN backend |
| **MediaTek Dimensity 9500** | ~40 TOPS | 7B with BitNet 1.58-bit | First mobile SoC with native 1-bit processing; 33% lower power; compute-in-memory architecture |

**Qualcomm** dominates Android on-device LLM inference via the Hexagon NPU and QMX instruction set. ExecuTorch's QNN backend targets Hexagon directly, and native INT4 quantization tuned for Hexagon generally outperforms generic GGUF Q4_0 on phones. **MediaTek** is pursuing a differentiated path with native BitNet 1.58-bit support at the silicon level, enabling sub-1-bit models to run without software emulation — a potential inflection point if ternary models mature.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Inference $/Million Tokens

| Tier | Model Example | Input $/MTok | Output $/MTok | Effective $/1K Requests (500 in / 500 out) |
|------|---------------|:------------:|:-------------:|:------------------------------------------:|
| **Frontier** | Claude Opus 5, GPT-5.6 Sol | $5.00–10.00 | $25.00–50.00 | $15.00–30.00 |
| **Mid** | Claude Sonnet 4, Gemini 3.1 Pro | $1.75–3.00 | $12.00–15.00 | $6.88–9.00 |
| **Budget** | Claude Haiku 4.5 | $1.00 | $5.00 | $3.00 |
| **Ultra-budget** | Gemini 2.5 Flash, DeepSeek V4 Flash | $0.075–0.14 | $0.28–0.40 | $0.18–0.27 |
| **Open-weight hosted** | Llama 4 Scout (Groq) | $0.18 | $0.59 | $0.39 |
| **Self-hosted** | Llama 4 70B (H200 @ $2.49/hr) | ~$0.13 | ~$0.13 | ~$0.13 |

**Historical trend**: GPT-3 cost $60/MTok in Nov 2021. Multiple models now <$0.06/MTok. LLM API pricing dropped ~80% between early 2025 and early 2026.

**Self-hosting break-even**: <10M tokens/day → APIs cheaper. 10–50M tokens/day → depends on ops capability. >50M tokens/day → self-hosting 50–80% cheaper.

### 3.2 Latency SLA Targets

| Component | p50 | p95 | p99 | Mitigation |
|-----------|:---:|:---:|:---:|------------|
| **TTFT (interactive)** | <200ms | <500ms | <1s | Prefix caching; disaggregated prefill; queue management |
| **TTFT (batch)** | <1s | <3s | <5s | Lower priority; larger batch sizes |
| **TPOT** | <20ms | <30ms | <50ms | Speculative decoding; FP8/NVFP4; Marlin kernels |
| **ITL (streaming jitter)** | <15ms | <25ms | <40ms | Continuous batching; chunked prefill |
| **E2E (interactive)** | <1s | <5s | <10s | Model routing; prefix cache; spec decode |
| **E2E (agent, multi-step)** | <10s | <30s | <60s | Parallel tool calls; cache warm-up |
| **Model load (cold start)** | <30s | <60s | <120s | Pre-warmed GPU pool; model weight caching |
| **Quantization compile** | 10min | 20min | 30min | Pre-compiled engines; CI/CD pipeline |

**Critical rule**: TTFT p95 <500ms (MLCommons standard). Never use averages — a 200ms average can hide a 3,000ms p99. TPOT >250ms makes streaming feel broken.

### 3.3 Throughput & Back-Pressure

**Throughput benchmarks (per GPU)**:

| Configuration | Output Tokens/sec |
|---------------|:-----------------:|
| vLLM, Llama 3 8B, A100, 128 concurrent | 793 |
| SGLang, Llama 3 8B, H100 | 16,200 |
| vLLM, Llama 4 Maverick, 8× B200, 1024 concurrent | 9,870 |
| TensorRT-LLM, Llama 3.1 8B, FP8 | 11,077 |
| Cerebras WSE-3, Llama 3.1 405B | 969/user |
| Groq LPU, Llama 3.1 8B | 840 |

**Back-pressure mechanisms**:
- **Request queue depth**: When queue exceeds 2× batch capacity, reject new requests with 429. Never silently queue — unbounded queues cause cascading TTFT failures.
- **KV cache pressure**: When KV cache utilization exceeds 90%, evict lowest-priority pages (LRU or importance-scored). vLLM's KV offload moves pages to CPU RAM under VRAM pressure.
- **GPU memory saturation**: Dynamic batch size reduction. Switch from continuous to rate-limited admission.
- **Cost budget**: Hard per-user daily cap enforced at gateway. At 80% threshold, alert; at 100%, throttle or cascade to cheaper model.
- **Prefill interference**: Chunked prefill limits prefill token range per iteration, preventing long prompts from starving decode slots.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **KV cache (GPU HBM)** | 0 (ephemeral per request) | N/A (reconstructed on miss) | Cache miss triggers re-prefill; no persistence needed |
| **Prefix cache** | 0 (ephemeral, LRU evicted) | <5s (warm-up from next matching request) | LRU rebuild; no persistence |
| **Model weights** | 0 (immutable, versioned) | <30s (reload from NVMe/S3) | Weight files versioned in model registry; fast NVMe load |
| **Cost ledger** | Per-request (transactional) | <2s (gateway restart) | Gateway-level atomic writes; periodic checkpoint |
| **Request queue** | Per-batch (~100ms) | <5s (restart; in-flight requests lost) | Retry from client; idempotent request IDs |
| **Quantized engine** | 0 (deterministic rebuild) | 10–30min (recompile) | Pre-compiled engines stored in registry; CI/CD pipeline |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Inference Systems

#### 4.1.1 State Machine

```
                  requests flowing
             ┌───────────────┐
             │               │
             ▼               │
        ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
        │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
        │         │    │          │    │             │
        │ Normal  │    │ Cascade  │    │ Route 3    │
        │ serving │    │ to backup│    │ test reqs   │
        │ on      │    │ model or │    │ through     │
        │ primary │    │ reject   │    │ primary     │
        │ engine  │    │ with 503 │    │ engine      │
        └─────────┘    └──────────┘    └─────────────┘
             ▲          │       ▲            │
             │          │       │            │
             │          │       └────────────┘
             │          │      any test req fails
             │     after 45s
             │     recovery timeout
             │     (45s → 90s → 180s exponential)
             │
             └──────────────────────────────┘
                   3/3 test requests return
                   TTFT <2× baseline AND
                   output matches expected
```

**Thresholds**:
- **Closed → Open**: 5 inference failures (OOM, CUDA error, timeout >30s, model crash) within 120s window. OR: TTFT p95 exceeds 5× baseline for 60s (quality degradation without explicit errors).
- **Open duration**: 45s initial recovery timeout with exponential backoff (45s → 90s → 180s).
- **Open behavior**: Cascade all traffic to backup model (e.g., primary = Sonnet, backup = Haiku). If no backup: return 503 with estimated recovery time. Do not queue — unbounded queues cascade.
- **Half-Open probes**: Route 3 test requests through primary engine.
- **Half-Open → Closed**: All 3 test requests return with TTFT <2× baseline AND output matches expected format (not garbled/truncated).
- **Escalation**: If circuit stays open >10 minutes, alert on-call with GPU health diagnostics and model version.

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| GPU OOM during inference | **Transient** | CUDA error code; process crash | Reduce batch size; restart engine; evict KV cache |
| CUDA kernel error / driver crash | **Transient** | Process monitor; health check failure | Auto-restart engine; failover to replica |
| Model weight corruption | **Permanent** | Checksum mismatch on load | Reload from registry; verify SHA-256 |
| Quantization quality regression | **Permanent** (version-specific) | Eval score drop after quant change | Revert to previous quantization config |
| KV cache fragmentation under load | **Transient** | Throughput degradation; TTFT spike | PagedAttention defragmentation; restart |
| Speculative decode acceptance collapse | **Transient** | Acceptance rate <30% for >5min | Disable speculative decoding; fall back to standard decode |
| Disaggregated KV transfer failure | **Transient** | Transfer timeout; FlowKV error | Retry transfer; fall back to monolithic serving |
| Provider API outage | **Transient** | 5xx responses; timeout | Failover to alternate provider via gateway |
| Model deprecated by provider | **Permanent** | API deprecation notice; 404 | Migrate to replacement model; update routing config |
| Draft model incompatible with target | **Permanent** (version mismatch) | Acceptance rate near 0% | Update draft model; retrain prediction head |

### 4.3 Idempotency in Inference

Inference requests are inherently non-idempotent (temperature >0 produces different outputs). Design for idempotency at the system level:

- **Request deduplication**: Gateway assigns unique `request_id`. If same `request_id` arrives within TTL (5s), return cached response. Prevents duplicate billing on client retry.
- **Cost recording**: At-most-once cost event per `request_id`. Gateway logs cost before forwarding response. Retry reads from cost cache.
- **Cache invalidation**: Prefix cache entries keyed by `hash(model_version + prompt_prefix)`. Model version change automatically invalidates stale entries.
- **Quantization reproducibility**: FP8/NVFP4 quantization with fixed calibration dataset produces deterministic weights. Pin calibration set version alongside model version.

### 4.3.1 Poison-Pill Detection in Inference

A poison pill in inference is a request that crashes the engine, corrupts shared state, or produces pathological resource consumption.

**Detection heuristics**:
- **Adversarial input length**: Request claiming 4K tokens but actually containing 400K after tokenization. Validate token count at gateway before forwarding. Quarantine sender.
- **KV cache bomb**: Prompt engineered to maximize KV cache allocation (extremely long context with no prefix sharing). Detect via per-request KV allocation exceeding 3× median. Rate-limit sender.
- **Infinite generation**: Output exceeding `max_tokens` due to engine bug or malformed stop sequence. Hard-kill generation at 2× `max_tokens`.
- **Quantization probe**: Crafted input that exploits low-precision rounding to produce garbled output. Detect via output quality monitor (perplexity check on sampled responses).
- **Draft model poisoning**: If speculative decoding uses external draft model, verify draft model provenance (signed weights, trusted registry). Quarantine unverified draft models.

**Quarantine flow**: Flagged requests logged with full metadata. Sender rate-limited (not blocked — could be legitimate edge case). Requests excluded from prefix cache population (prevent cache pollution). Alert for manual review if quarantine rate exceeds 0.1% of traffic.

### 4.4 Zero-Trust Boundaries

1. **Model weight integrity**: Every weight file verified against SHA-256 checksum from signed model registry. Quantized weights re-verified after quantization pipeline. Prevents supply chain attacks via poisoned weights.

2. **Prompt isolation**: Multi-tenant serving must isolate prompts between customers. No cross-request KV cache sharing between tenants (prefix caching only within same tenant/project). vLLM supports tenant-aware cache partitioning.

3. **Gateway-to-engine authentication**: Internal gRPC between gateway and inference engine uses mTLS. Prevents unauthorized model invocation from within the network.

4. **Cost boundary enforcement**: Per-user and per-feature cost caps enforced at gateway, not at engine. Engine cannot be tricked into unbounded generation by bypassing the gateway.

5. **Telemetry redaction**: Token counts and latency are safe to export. Prompt/completion content requires opt-in and PII scrubbing before export to observability backends (see Module 14).

---

## 5. Production Enterprise Code

### 5.1 Inference Router with Cascade and Cost Tracking

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class ModelTier(Enum):
    BUDGET = "budget"
    MID = "mid"
    FRONTIER = "frontier"


@dataclass
class ModelConfig:
    name: str
    tier: ModelTier
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    ttft_p95_ms: float
    max_context: int


@dataclass
class RoutingDecision:
    model: ModelConfig
    reason: str
    estimated_cost_usd: float
    cascade_depth: int


MODELS = {
    ModelTier.BUDGET: ModelConfig(
        "claude-haiku-4-5", ModelTier.BUDGET, 1.0, 5.0, 300, 200_000),
    ModelTier.MID: ModelConfig(
        "claude-sonnet-4", ModelTier.MID, 3.0, 15.0, 500, 200_000),
    ModelTier.FRONTIER: ModelConfig(
        "claude-opus-5", ModelTier.FRONTIER, 5.0, 25.0, 800, 200_000),
}


class InferenceRouter:
    def __init__(self, complexity_threshold_mid: float = 0.4,
                 complexity_threshold_frontier: float = 0.7,
                 daily_budget_per_user: float = 50.0):
        self.threshold_mid = complexity_threshold_mid
        self.threshold_frontier = complexity_threshold_frontier
        self.daily_budget = daily_budget_per_user
        self._user_spend: dict[str, float] = {}
        self._seen_requests: set[str] = set()

    def route(self, request_id: str, user_id: str,
              complexity_score: float, input_tokens: int,
              estimated_output_tokens: int) -> RoutingDecision:
        if self._user_spend.get(user_id, 0) > self.daily_budget:
            model = MODELS[ModelTier.BUDGET]
            return RoutingDecision(
                model=model,
                reason="budget_cap_exceeded",
                estimated_cost_usd=self._estimate_cost(
                    model, input_tokens, estimated_output_tokens),
                cascade_depth=0,
            )

        if complexity_score >= self.threshold_frontier:
            tier = ModelTier.FRONTIER
        elif complexity_score >= self.threshold_mid:
            tier = ModelTier.MID
        else:
            tier = ModelTier.BUDGET

        model = MODELS[tier]
        cost = self._estimate_cost(model, input_tokens, estimated_output_tokens)
        return RoutingDecision(
            model=model,
            reason=f"complexity_{complexity_score:.2f}",
            estimated_cost_usd=cost,
            cascade_depth=0,
        )

    def record_cost(self, request_id: str, user_id: str,
                     actual_cost: float) -> None:
        if request_id in self._seen_requests:
            return
        self._seen_requests.add(request_id)
        self._user_spend[user_id] = (
            self._user_spend.get(user_id, 0) + actual_cost
        )

    def _estimate_cost(self, model: ModelConfig, input_tokens: int,
                        output_tokens: int) -> float:
        return (
            (input_tokens / 1_000_000) * model.input_cost_per_mtok
            + (output_tokens / 1_000_000) * model.output_cost_per_mtok
        )
```

### 5.2 KV Cache Manager with PagedAttention and Prefix Matching

```python
from dataclasses import dataclass, field
from collections import OrderedDict
from typing import Optional
import hashlib


@dataclass
class CachePage:
    page_id: int
    tokens: tuple[int, ...]
    ref_count: int = 1
    tenant_id: str = ""


@dataclass
class CacheStats:
    total_pages: int
    used_pages: int
    hit_rate: float
    prefix_matches: int
    evictions: int


class PagedKVCacheManager:
    def __init__(self, max_pages: int, page_size: int = 16):
        self.max_pages = max_pages
        self.page_size = page_size
        self._pages: dict[int, CachePage] = {}
        self._prefix_index: OrderedDict[str, list[int]] = OrderedDict()
        self._next_page_id = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def lookup_prefix(self, token_ids: list[int],
                       tenant_id: str) -> tuple[list[int], int]:
        matched_page_ids = []
        matched_tokens = 0

        for start in range(0, len(token_ids), self.page_size):
            chunk = tuple(token_ids[start:start + self.page_size])
            if len(chunk) < self.page_size:
                break
            key = self._page_key(chunk, tenant_id)
            if key in self._prefix_index:
                page_ids = self._prefix_index[key]
                self._prefix_index.move_to_end(key)
                matched_page_ids.extend(page_ids)
                matched_tokens += len(chunk)
                self._hits += 1
            else:
                self._misses += 1
                break

        return matched_page_ids, matched_tokens

    def allocate(self, token_ids: list[int], tenant_id: str,
                  start_offset: int = 0) -> list[int]:
        new_page_ids = []
        for start in range(start_offset, len(token_ids), self.page_size):
            chunk = tuple(token_ids[start:start + self.page_size])
            if len(chunk) < self.page_size:
                break
            if len(self._pages) >= self.max_pages:
                self._evict_lru()

            page = CachePage(
                page_id=self._next_page_id,
                tokens=chunk,
                tenant_id=tenant_id,
            )
            self._pages[page.page_id] = page
            self._next_page_id += 1

            key = self._page_key(chunk, tenant_id)
            self._prefix_index[key] = self._prefix_index.get(key, []) + [
                page.page_id
            ]
            new_page_ids.append(page.page_id)

        return new_page_ids

    def release(self, page_ids: list[int]) -> None:
        for pid in page_ids:
            if pid in self._pages:
                self._pages[pid].ref_count -= 1
                if self._pages[pid].ref_count <= 0:
                    page = self._pages.pop(pid)
                    key = self._page_key(page.tokens, page.tenant_id)
                    if key in self._prefix_index:
                        del self._prefix_index[key]

    def stats(self) -> CacheStats:
        total = self._hits + self._misses
        return CacheStats(
            total_pages=self.max_pages,
            used_pages=len(self._pages),
            hit_rate=self._hits / total if total > 0 else 0.0,
            prefix_matches=self._hits,
            evictions=self._evictions,
        )

    def _evict_lru(self) -> None:
        if not self._prefix_index:
            return
        oldest_key, page_ids = self._prefix_index.popitem(last=False)
        for pid in page_ids:
            self._pages.pop(pid, None)
        self._evictions += 1

    def _page_key(self, tokens: tuple[int, ...], tenant_id: str) -> str:
        raw = f"{tenant_id}:{tokens}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

### 5.3 Speculative Decode Coordinator

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class DraftOutput:
    token_ids: list[int]
    log_probs: list[float]


@dataclass
class VerifyResult:
    accepted_count: int
    accepted_tokens: list[int]
    correction_token: Optional[int]


@dataclass
class SpecDecodeMetrics:
    total_draft_tokens: int
    total_accepted: int
    total_rounds: int

    @property
    def acceptance_rate(self) -> float:
        if self.total_draft_tokens == 0:
            return 0.0
        return self.total_accepted / self.total_draft_tokens

    @property
    def speedup_estimate(self) -> float:
        if self.acceptance_rate < 0.3:
            return 1.0
        return 1.0 / (1.0 - self.acceptance_rate + 1.0 / self.draft_tokens_per_round)

    @property
    def draft_tokens_per_round(self) -> float:
        if self.total_rounds == 0:
            return 0.0
        return self.total_draft_tokens / self.total_rounds


class SpeculativeDecodeCoordinator:
    def __init__(self, draft_tokens_per_step: int = 5,
                 min_acceptance_rate: float = 0.3):
        self.draft_k = draft_tokens_per_step
        self.min_acceptance = min_acceptance_rate
        self._enabled = True
        self.metrics = SpecDecodeMetrics(0, 0, 0)
        self._recent_acceptance: list[float] = []

    def should_speculate(self) -> bool:
        if not self._enabled:
            return False
        if len(self._recent_acceptance) >= 10:
            recent_avg = sum(self._recent_acceptance[-10:]) / 10
            if recent_avg < self.min_acceptance:
                self._enabled = False
                return False
        return True

    def verify_draft(self, draft: DraftOutput,
                      target_log_probs: list[float]) -> VerifyResult:
        accepted = []
        for i, (draft_lp, target_lp) in enumerate(
            zip(draft.log_probs, target_log_probs)
        ):
            import math
            acceptance_prob = min(1.0, math.exp(target_lp - draft_lp))
            if acceptance_prob >= 0.5:
                accepted.append(draft.token_ids[i])
            else:
                correction = draft.token_ids[i]
                self._record_round(len(draft.token_ids), len(accepted))
                return VerifyResult(
                    accepted_count=len(accepted),
                    accepted_tokens=accepted,
                    correction_token=correction,
                )

        self._record_round(len(draft.token_ids), len(accepted))
        return VerifyResult(
            accepted_count=len(accepted),
            accepted_tokens=accepted,
            correction_token=None,
        )

    def _record_round(self, drafted: int, accepted: int) -> None:
        self.metrics.total_draft_tokens += drafted
        self.metrics.total_accepted += accepted
        self.metrics.total_rounds += 1
        rate = accepted / drafted if drafted > 0 else 0.0
        self._recent_acceptance.append(rate)
        if len(self._recent_acceptance) > 50:
            self._recent_acceptance = self._recent_acceptance[-50:]
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Cost-Optimized Inference Platform for a B2B SaaS Company

**Business context**: A B2B SaaS company serves 5,000 business customers through an AI-powered document analysis product. Monthly LLM spend is $180K and growing 20% month-over-month. The CEO mandates cutting inference costs by 60% without degrading quality below 95% of current levels. Current architecture: all requests go to Claude Sonnet 4 via direct API. Monthly volume: 50M requests averaging 2K input + 500 output tokens.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   COST-OPTIMIZED INFERENCE PLATFORM                      │
 │                                                                          │
 │  Request ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐ │
 │              │ Complexity   │     │ Prompt Cache │     │ Model Tier │ │
 │              │ Classifier   │     │ Layer        │     │            │ │
 │              │ (ModernBERT) │     │ - 90% input  │     │ Budget:65% │ │
 │              │              │     │   discount   │     │ Mid:  25%  │ │
 │              │ Score 0→1    │     │ - 84% hit    │     │ Front:10%  │ │
 │              │              │     │   rate target│     │            │ │
 │              └──────────────┘     └──────────────┘     └──────┬─────┘ │
 │                                                               │       │
 │  ┌─────────────────────────────────────────────────────────────▼────┐  │
 │  │  Cost Tracker: Per-customer attribution + monthly budget gate    │  │
 │  │  Quality Monitor: 5% sampled traffic scored by LLM judge        │  │
 │  │  Alert: If quality score drops >5% from baseline, escalate tier │  │
 │  └─────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Model Routing Only | B: Routing + Prompt Caching (Recommended) | C: Self-Host Open Weight |
|-----------|----------------------|-------------------------------------------|--------------------------|
| **Cost reduction** | 50–60% (route 65% to budget) | 70–80% (routing + 84% cache hit) | 80–90% at volume |
| **Quality retention** | 95%+ (frontier for hard queries) | 95%+ (caching doesn't affect quality) | 90–95% (open-weight gap) |
| **Implementation time** | 2 weeks (classifier + gateway) | 3 weeks (add cache layer) | 3–4 months (infra + ops) |
| **Operational complexity** | Low (API-based) | Low (API + cache config) | High (GPU fleet, on-call) |
| **Vendor flexibility** | High (swap models easily) | High (cache is orthogonal) | Low (locked to chosen model) |
| **Risk** | Silent quality degradation on cheap tier | Same as A + cache invalidation | Model quality ceiling; ops burden |

**Recommended approach**: **B (Routing + Prompt Caching)**.

**Decision rationale**: Option A alone achieves 50–60% cost reduction by routing 65% of simple document classification requests to Haiku ($1/MTok input) and reserving Sonnet for complex analysis. But the 2K-token average input contains substantial repeated content (system prompt + tool definitions + document schema = ~1.2K tokens shared across all requests). Adding prompt caching at 84% hit rate saves 90% on cached input tokens — an additional 20% total cost reduction on top of routing. Combined: $180K × 0.25 = ~$45K/month (75% reduction). Option C achieves better unit economics above 50M tokens/day but requires 3–4 months of GPU infrastructure buildout, dedicated ML ops, and the current open-weight models (Llama 4) trail Claude Sonnet on document analysis quality by 5–10%. The 3-week implementation of Option B delivers 75% savings immediately, with Option C as a future phase once volume exceeds 100M tokens/day. Quality monitoring via 5% sampled LLM-judge scoring ensures any routing-induced degradation is caught within hours, not weeks.

### 6.2 Scenario: Low-Latency Inference Stack for Real-Time Coding Agent

**Business context**: A developer tools startup building a real-time coding agent (competing with Cursor/Copilot). Requirements: TTFT <200ms p95, TPOT <20ms, support for 10,000 concurrent developers, each generating 50–200 completions/day. Budget: $500K/month for inference infrastructure. The agent uses a 70B model for complex code generation and an 8B model for autocomplete. Latency is the primary competitive differentiator — every 100ms of added latency costs measurable user engagement.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   LOW-LATENCY CODING AGENT INFERENCE                     │
 │                                                                          │
 │  IDE ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────────┐ │
 │  Plugin  │ Edge Gateway │     │ Request Type │     │ Inference Pool │ │
 │          │ - WebSocket  │     │ Classifier   │     │                │ │
 │          │ - Geo-routed │     │              │     │ AUTOCOMPLETE:  │ │
 │          │ - Prefix     │     │ Autocomplete │     │ 8× B200, 8B   │ │
 │          │   cache warm │     │ → 8B pool    │     │ FP8, EAGLE-3  │ │
 │          │              │     │              │     │ SGLang (Radix) │ │
 │          │              │     │ Complex gen  │     │                │ │
 │          │              │     │ → 70B pool   │     │ GENERATION:    │ │
 │          │              │     │              │     │ 16× B200, 70B  │ │
 │          │              │     │              │     │ FP8, disagg.   │ │
 │          │              │     │              │     │ 4 prefill +    │ │
 │          │              │     │              │     │ 12 decode      │ │
 │          └──────────────┘     └──────────────┘     └────────────────┘ │
 │                                                                        │
 │  ┌────────────────────────────────────────────────────────────────────┐ │
 │  │  LATENCY OPTIMIZATION STACK                                        │ │
 │  │  - Prefix caching: file context + repo schema (85%+ hit rate)      │ │
 │  │  - Speculative decode: EAGLE-3 (75-85% acceptance on code)         │ │
 │  │  - KV cache: FP8 quantized, PagedAttention                        │ │
 │  │  - Disaggregated: 4 prefill + 12 decode GPUs for 70B              │ │
 │  │  - Connection: persistent WebSocket, no HTTP overhead              │ │
 │  └────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: API-Based (Anthropic/OpenAI) | B: Self-Hosted SGLang + B200 (Recommended) | C: Groq / Cerebras API |
|-----------|--------------------------------|---------------------------------------------|------------------------|
| **TTFT p95** | 300–500ms (network + queue) | <200ms (local, prefix cached) | <100ms (Groq), 240ms (Cerebras) |
| **TPOT** | 15–30ms | <15ms (EAGLE-3 + FP8) | <10ms (Groq) |
| **Cost at 10K devs** | ~$800K/mo (200M completions × $4/MTok avg) | ~$400K/mo (24 B200s × $12/hr + ops) | ~$600K/mo (fast but premium pricing) |
| **Model control** | None (provider models only) | Full (fine-tune, custom draft models) | None (open-weight only) |
| **Prefix cache control** | Provider-managed (5min–1hr TTL) | Full control (RadixAttention, custom TTL) | Limited |
| **Scaling flexibility** | Elastic (provider handles) | Manual (GPU procurement lead time) | Elastic (API) |
| **Competitive moat** | None (competitors use same APIs) | High (custom model + infra tuning) | Low (same API available to all) |

**Recommended approach**: **B (Self-Hosted SGLang + B200)**.

**Decision rationale**: The 200ms TTFT p95 requirement eliminates Option A — network latency to API providers alone consumes 50–150ms, leaving insufficient headroom for queuing and prefill. Option C (Groq) achieves excellent latency but only supports open-weight models — the coding agent needs a fine-tuned 70B model trained on proprietary code patterns, which Groq cannot serve. Option B deploys SGLang on 24 B200 GPUs: 8 for the autocomplete pool (8B model, FP8, EAGLE-3 speculative decode achieving 75–85% acceptance on code, RadixAttention for file-context prefix caching at 85%+ hit rate) and 16 for the generation pool (70B model, FP8, disaggregated 4 prefill + 12 decode). SGLang's RadixAttention is ideal for coding — developers repeatedly query with the same file context, and RadixAttention automatically reuses KV cache across those requests, delivering 20–40% lower TTFT than vLLM for this workload pattern. At $12/hr per B200 (mid-range cloud pricing), 24 GPUs cost ~$207K/month in compute plus ~$100K/month in ops/engineering — well within the $500K budget and 50% cheaper than API-based serving. The competitive moat comes from custom fine-tuning + infrastructure tuning that API-based competitors cannot replicate.

---

*Module 15 complete. Covers model serving engines (vLLM, SGLang, TensorRT-LLM with benchmarks), quantization (FP8, NVFP4, AWQ, GPTQ, GGUF, BitNet), KV cache optimization (PagedAttention, prefix caching, RadixAttention), speculative decoding (EAGLE-3, P-EAGLE, acceptance rates), prompt caching (provider comparison, multi-tier architecture), batching (continuous, chunked prefill, BucketServe), hardware (B200, MI355X, Groq LPU, Cerebras WSE-3, Trainium3, TPU v7), cost optimization (routing, cascade, distillation, self-hosting break-even), and edge inference (llama.cpp, MLX, ExecuTorch).*
