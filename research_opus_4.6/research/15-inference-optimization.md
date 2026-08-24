# Module 15: Inference & Optimization

> Research compiled August 2026. Covers production-grade LLM inference infrastructure, quantization, caching, hardware, and cost optimization strategies for Principal AI Architect interview preparation.

---

## Table of Contents

1. [Model Serving Infrastructure](#1-model-serving-infrastructure)
2. [Quantization Techniques](#2-quantization-techniques)
3. [KV Cache Optimization](#3-kv-cache-optimization)
4. [Speculative Decoding and Draft Models](#4-speculative-decoding-and-draft-models)
5. [Prompt Caching and Context Reuse](#5-prompt-caching-and-context-reuse)
6. [Batching Strategies](#6-batching-strategies)
7. [Hardware Landscape](#7-hardware-landscape)
8. [Cost Optimization Strategies](#8-cost-optimization-strategies)
9. [Edge and On-Device Inference](#9-edge-and-on-device-inference)
10. [Sources](#sources)

---

## 1. Model Serving Infrastructure

### 1.1 The 2026 Landscape

The production LLM serving landscape in 2026 has consolidated around three primary engines: **vLLM** (broadest hardware and model support), **SGLang** (best for prefix-heavy and multi-turn workloads), and **TensorRT-LLM** (highest raw throughput on NVIDIA hardware). Text Generation Inference (TGI) from Hugging Face entered maintenance mode in December 2025 and is no longer recommended for new deployments [1][2][3].

### 1.2 vLLM

**Overview:** vLLM pioneered PagedAttention, which treats the KV cache like virtual memory with paging. It prevents memory fragmentation and enables highly efficient continuous batching. Under heavy load (100+ concurrent users), vLLM keeps GPUs 85-92% utilized vs. TGI's 68-74% [1].

**Key metrics (2026):**
- Llama 3 8B at 8 concurrent users: ~187 tok/s (vs. Ollama's 82 tok/s) [4]
- At 128 concurrent requests on A100: ~793 output tok/s (vs. Ollama's ~41 tok/s) [4]
- Llama 4 Maverick on 8x B200 at 1,024 concurrent requests: 9,870 output tok/s [5]
- Single-request latency: Ollama beats vLLM by ~18%, but vLLM wins at any concurrency > 1 [4]

**Platform support:** NVIDIA GPUs, AMD GPUs (ROCm), Intel CPUs/GPUs, Google TPUs, AWS Trainium, IBM Spyre, Huawei Ascend [1].

**Recent features (v0.20-v0.21, May 2026):**
- Speculative decoding with thinking budget support for reasoning models
- KV offload + hybrid memory allocator (offloads KV cache pages to CPU RAM under VRAM pressure)
- Blackwell/Rubin GPU support (sm_107 target for NVIDIA Rubin)
- FlashInfer fused all-reduce tuned for GB300 world_size=16
- NVFP4 swizzled-scale zero-init for Blackwell decode throughput recovery
- Rust frontend with gRPC control plane
- New model support: Qwen3.5, Kimi K3, K-EXAONE-2.0-750B, DeepSeek V4 optimizations [5][6]

**When to use:** Default choice for most teams. Best for multi-model deployments, rapid iteration, and mixed-hardware environments.

### 1.3 SGLang

**Overview:** Developed by UC Berkeley/LMSYS, SGLang uses RadixAttention -- a radix tree data structure for automatic KV cache reuse across requests. Its cache-aware scheduler prioritizes requests with longer shared prefixes, maximizing cache hits [7][8].

**Key metrics (2026):**
- Llama 3 8B on H100: ~16,200 tok/s (vs. vLLM's ~12,500 tok/s, a 29% advantage) [8]
- DeepSeek V3: 3.1x faster inference than vLLM [8]
- Multi-turn chat at concurrency=50: 37% lower TTFT p50 and 41% lower p95 vs. vLLM [9]
- Dual H100 on Llama 3 70B: TTFT p50 ~190ms vs. vLLM's ~210ms [9]
- Powers 400,000+ GPUs in production at xAI, NVIDIA, AMD, and LinkedIn [9]

**RadixAttention advantages:**
- If two requests share a 200K-token prefix and differ only in the last 5K, RadixAttention computes the prefix once and reuses it for subsequent requests [7]
- At 70B+ scale, the throughput gap vs. vLLM narrows to 3-5%; at 8B scale, the gap is significant [8]
- For prefix-heavy workloads: 20-40% lower TTFT vs. vLLM; for unique-prompt workloads: within 5% [9]

**When to use:** Multi-turn chatbots, RAG pipelines, few-shot classification, agent orchestration, and any workload where prefix overlap exceeds 60%.

### 1.4 TensorRT-LLM

**Overview:** NVIDIA's proprietary framework that compiles model weights and architecture into optimized CUDA kernel graphs. Uses aggressive kernel fusion to combine multiple operations (LayerNorm + MatMul + activation) into single CUDA kernels [10][11].

**Key metrics (2026):**
- Llama 3.1 8B: 11,077 tok/s with TPOT of 7.32ms [11]
- H100 FP8: 10,000+ output tok/s with sub-100ms TTFT [11]
- vs. vLLM: 1.34x advantage for short sequences, up to 2.72x for long sequences [11]
- AOT engine building yields 2.3x tokens/s of vLLM when FP8 is enabled [1]
- At 50 concurrent requests: 13% faster than vLLM; at 1 request: 8% faster [5]
- p95 TTFT at 100 concurrent: 1,280ms vs. vLLM's 1,450ms [5]

**Quantization support:**
- FP8 (Hopper): 1.3-1.5x throughput over FP16
- FP4/NVFP4 (Blackwell): 70B model fits in ~35GB; 405B fits in ~200GB on 4-GPU B200
- INT4 AWQ, INT8 SmoothQuant [11]

**Trade-offs:**
- Engine compilation: 10-30 minutes for large models, up to 28 minutes [1][11]
- Locks you into NVIDIA hardware
- 1-2 weeks of setup time for production deployments [1]
- Model configuration changes require recompilation

**When to use:** Stable production models with high throughput requirements on NVIDIA hardware where operational complexity is acceptable. Used by Perplexity, major cloud providers [1].

**Emerging competitor:** TokenSpeed claims 9% latency and 11% throughput edge over TensorRT-LLM on B200 for agentic coding workloads, without a compilation step [11].

### 1.5 TGI (Text Generation Inference)

As of December 2025, TGI only accepts bug fixes with no new features. Hugging Face's own Inference Endpoints now default to vLLM, with SGLang as an alternative. TGI trails both vLLM and SGLang on GPU utilization (68-74% vs. 85-92%) [1][2]. Not recommended for new deployments.

### 1.6 Disaggregated Inference (Prefill/Decode Separation)

A major architectural shift in 2025-2026 is **disaggregated inference**, which runs prefill and decode on separate GPU pools [12][13].

**Why it matters:** Prefill is compute-bound; decode is memory-bandwidth-bound. Cramming both onto the same GPU wastes resources. Separating them lets each phase use hardware optimized for its needs.

**Key results:**
- DistServe: 7.4x more requests per GPU and 12.6x tighter SLO compliance vs. monolithic serving [12]
- 70% higher throughput, 88% faster TTFT, GPU utilization jumps from 40% to 80%+ [12]
- Net cost per request drops 30-50% [12]
- SGLang on DeepSeek-R1 with 96 H100s (3 prefill + 9 decode nodes): 52.3K input tok/s, 22.3K output tok/s per node [12]

**Production frameworks:**
- NVIDIA Dynamo (GTC 2025): prefill/decode workers as first-class citizens
- llm-d (Red Hat + IBM + Google, CNCF, KubeCon Europe 2026): Kubernetes-native disaggregated inference on vLLM, 25% performance boost with zero tuning
- Mooncake (Moonshot AI, FAST 2025 Best Paper): processing 100B+ tokens/day at Kimi [12]

**KV cache transfer challenge:** For DeepSeek-R1, KV cache per 4K prompt is ~1.34 GB. FlowKV (2025) reduced kernel invocations from 23,469 to 1 per request, achieving 96% reduction in transfer latency. NVIDIA built NIXL for this purpose [12].

**When to use:** Models above 13B parameters at batch sizes above 256. Below 7B, the network transfer overhead outweighs benefits [12].

### 1.7 Decision Framework

| Criterion | vLLM | SGLang | TensorRT-LLM |
|---|---|---|---|
| **Best for** | Multi-model, rapid iteration | Prefix-heavy, multi-turn, RAG | Maximum throughput on NVIDIA |
| **Hardware** | NVIDIA, AMD, Intel, TPU, Trainium | NVIDIA, AMD | NVIDIA only |
| **Setup time** | Minutes | Minutes | Hours to days |
| **Throughput** | Baseline | +29% (small models) | +30-50% (high concurrency) |
| **Model flexibility** | Highest | High | Low (recompilation required) |
| **Speculative decoding** | Yes (Eagle3) | Yes (Eagle, MTP) | Yes (native) |

---

## 2. Quantization Techniques

### 2.1 Overview

Quantization compresses model weights from 16-bit floats (FP16/BF16) to lower precision (8-bit, 4-bit, or lower), drastically reducing memory usage while preserving most accuracy. A 70B-parameter model in FP16 requires 140GB of VRAM; quantized to INT4, it drops to 35-40GB, fitting on a single RTX 4090 or A100 [14][15].

### 2.2 FP8 (8-bit Floating Point)

**The 2026 production default** on Hopper (H100/H200) and Blackwell (B200/B300) hardware.

**Quality:**
- Qwen3-32B: 69.64% MMLU-Pro vs. 70.24% BF16 (0.6-point difference across 12,000 questions) [16]
- HumanEval: identical scores at FP8 and BF16 (39.02%) [16]
- Six 70B-class models: FP8 lands within 0.4 points of FP16 on MMLU-Pro and HumanEval+ [16]
- Calibrated FP8: 0.5-2% degradation on standard benchmarks [16]

**Throughput:**
- 1.4-1.7x over FP16 [16]
- Mistral 7B on Baseten: 8.5% decrease in TTFT, 33% improvement in output tok/s, 31% increase in throughput [17]
- Llama-v2-7B on H100: 2.3x inference speedup vs. FP16 (TTFT < 500ms, batch size 16) [16]

**Hardware requirement:** H100 or newer with native FP8 Tensor Cores [16].

### 2.3 NVFP4 / FP4 (4-bit Floating Point)

**Blackwell-exclusive format** (B200, B300, RTX 5090, RTX PRO 6000).

**Format:** FP4 E2M1 (1 sign, 2 exponent, 1 mantissa) with two-level scaling: FP8 E4M3 scale per 16-value micro-block + per-tensor FP32 scale [18].

**Quality:**
- DeepSeek-R1 MMLU: 90.7% (vs. 90.8% FP8), a 0.1% drop [18]
- DeepSeek-R1-0528: 1% or less accuracy loss on MMLU-Pro, GPQA Diamond, LiveCodeBench [18]
- Across nine NVIDIA benchmarks: NVFP4 scores within 0.5 points of FP8 on average; NVFP4 actually scored higher than FP8 in four benchmarks [18]

**Memory savings:**
- ~3.5x reduction vs. FP16; ~1.8x vs. FP8 [18]
- Qwen3.6-27B compressed from 55.6GB to 19.7GB [18]

**Throughput:**
- B200: 9,000 TFLOPS at FP4 vs. 4,500 at FP8 vs. 2,250 at BF16 [18]

**Best practice:** Run NVFP4 weights with FP8 or BF16 attention for quality-sensitive tasks [18].

### 2.4 GPTQ (GPU-Optimized Post-Training Quantization)

One-shot weight quantization method minimizing mean squared error per weight. Supports 8/4/3/2-bit quantization [14][15].

**Key characteristics:**
- Optimized for pure GPU inference with maximum throughput
- With Marlin kernels: 712 tok/s output throughput on Qwen2.5-32B (vs. 741 for Marlin-AWQ) [19]
- Marlin kernels provide 2.6x speedup over base GPTQ [19]
- Code generation: ~46% Pass@1, about 10% below BF16 baseline [19]
- Only 4-bit format with LoRA adapter support in vLLM [19]

**When to use:** GPU production inference, multi-LoRA deployments [14].

### 2.5 AWQ (Activation-Aware Weight Quantization)

Protects salient weights identified by analyzing activation distributions [14][15].

**Key characteristics:**
- Slightly better quality than GPTQ at 4-bit for reasoning tasks
- Consistently outperforms GPTQ by 0.5-1.5% on MMLU at same bit width [11]
- With Marlin kernels: 741 tok/s (fastest overall), with a 10.9x speedup from base AWQ [19]
- Code generation: 51.83% Pass@1, only 4% below BF16 baseline [19]
- GPU memory reduction: up to 40% [14]

**When to use:** Reasoning-heavy and agent workloads, instruction-tuned models [14].

### 2.6 GGUF (llama.cpp Format)

The de facto standard for CPU and Apple Silicon inference via llama.cpp and Ollama [14][15].

**Key characteristics:**
- Supports range of quantization levels: Q2_K through Q8_0
- CPU+GPU hybrid inference: model loads partially into VRAM and partially into system RAM
- Q5_K_M and Q6_K offer near-BF16 quality; Q2_K shows noticeable degradation [14]
- Q4_K_M keeps ~92% quality (best balance for Ollama) [14]
- Mixed-precision integer quantization with multiple sub-formats

**Limitation:** Uses integer tensor cores, leaving FP4/FP8 cores unused on newer GPUs [15].

**When to use:** CPU-focused inference, Apple Silicon, consumer hardware with limited VRAM [14].

### 2.7 Quantization Comparison Table

| Method | Bit Width | Quality Retention | Throughput Gain | Best For |
|---|---|---|---|---|
| **FP8** | 8-bit | ~99.5% | 1.4-1.7x | H100/B200 production default |
| **NVFP4** | 4-bit | ~99% | ~2x over FP8 | Blackwell VRAM-constrained |
| **AWQ** | 4-bit | ~95% | 2.6-3.1x (Marlin) | Reasoning, agents |
| **GPTQ** | 4-bit | ~93-95% | 2.6x (Marlin) | GPU inference, multi-LoRA |
| **GGUF Q4_K_M** | 4-bit | ~92% | N/A (CPU focus) | Ollama, Apple Silicon |
| **INT8** | 8-bit | ~99% | ~1.5x | Cross-platform fallback |

### 2.8 Emerging Trends

- **Hybrid precision:** Combining FP8 for critical layers and INT4 for others [14]
- **BitNet b1.58:** Native 1-bit LLM at 2B parameters, 0.4 GB weights, 29ms decoding latency on CPU, 6x less energy than Gemma 3 1B [20]
- **bitnet.cpp:** ACL 2025 paper, runs ternary models efficiently on CPUs without GPUs [14]
- **Kernels matter more than algorithms:** Marlin kernels provide massive speedups (2.6-10.9x), making the kernel choice as important as the quantization method [19]

---

## 3. KV Cache Optimization

### 3.1 The Core Problem

KV cache memory consumption often exceeds model weights in production deployments. A 70B model processing 8K context with batch size 32 needs ~640GB of KV cache alone. Traditional allocation wastes 60-80% of memory through fragmentation and over-allocation [21][22].

### 3.2 PagedAttention

Borrowed from OS virtual memory paging, PagedAttention divides the KV cache into fixed-size pages (blocks) allocated dynamically as sequences grow. A block table maps logical pages to physical memory [21][22].

**Key results:**
- Reduced KV cache waste to under 4% [21]
- Enabled 2-4x throughput improvements [21]
- Copy-on-write mechanism for shared blocks: duplicates only the affected block, not the entire cache [22]
- Now the universal default: vLLM, SGLang, and TensorRT-LLM all ship it by default [22]

### 3.3 Prefix Caching

When multiple requests share identical prefixes (system prompts, few-shot examples, long documents), physical pages are shared rather than duplicated [21][22].

**Example:** Two requests sharing a 200K-token prefix and differing only in the final 5K of user content: the cache stores the 200K KV state once and reuses it for every subsequent request [21].

**Results:**
- 85-95% cost savings on cache hits [21]
- The highest-leverage application optimization for LLM serving

**Implementations differ:**
- vLLM: hash-based prefix matching at the block level [21]
- SGLang: RadixAttention tree with LRU cache of KV blocks in radix tree structure [7]

### 3.4 KV Cache Quantization

FP8 halves KV cache memory vs. BF16 with negligible quality loss for most production workloads [22]. This is independent of weight quantization and is applied specifically to the key-value tensors stored during inference.

### 3.5 Advanced Techniques (2025-2026)

| Technique | Description | Impact |
|---|---|---|
| **PagedEviction** | Block-wise eviction of low-importance blocks without modifying CUDA kernels | Extends effective context window |
| **Entropy-guided caching** | Allocates cache budget intelligently across layers | Better quality/memory tradeoff |
| **Cache-aware routing** | Directs requests with shared prefixes to same replicas | Maximizes hit rates in distributed deployments |
| **InfiniGen** | Stores KV cache in CPU memory, predictively prefetches critical KV pairs to GPU | Extends context beyond VRAM limits |
| **Oneiros** | Multi-tenant KV remapping | Up to 86.7% higher throughput than vLLM [22] |

### 3.6 Hybrid Architectures

Models like Gemma 3, Jamba, and Llama 4 use hybrid attention architectures. SGLang's CUDA virtual memory approach and Jenga's LCM allocator are designed for these mixed architectures [22].

### 3.7 State Space Models

Mamba-3 and Liquid AI's LFM family eliminate the KV cache entirely with constant-memory approaches, at some accuracy tradeoff. These represent a fundamental architectural alternative to transformer-based KV caching [22].

---

## 4. Speculative Decoding and Draft Models

### 4.1 Core Mechanism

Speculative decoding uses a smaller, faster draft model to propose multiple candidate tokens, which a larger target model verifies in a single forward pass. This exploits the fact that LLM inference is memory-bound, not compute-bound -- GPUs have unused compute capacity while waiting on memory [23][24].

**Key guarantee:** Accepted tokens follow the exact same probability distribution as standard autoregressive decoding. Output quality is mathematically identical to running the target model alone [23].

### 4.2 Speedup and Acceptance Rates

- **Production speedup:** 2-3x for well-matched draft models [23][24]
- **NVIDIA demonstration:** 3.6x throughput improvements on H200 GPUs [24]
- **Acceptance rates:** EAGLE-3 achieves 60-80% on in-distribution workloads; code generation with high repetition can exceed 85% [24]
- **When it hurts:** Below 50% acceptance rate, speculative decoding wastes cycles and can actually slow things down [24]

### 4.3 Key Variants (2025-2026)

**EAGLE-3:**
- Lightweight autoregressive prediction head attached to the target model's internal layers
- Eliminates need for a separate draft model
- Uses Bayesian optimization for optimal exit layer selection
- Multi-token prediction from each head
- Training on target model's own generation data [24]

**P-EAGLE:**
- Current state-of-the-art for EAGLE-based speculative decoding in 2026 [24]

**N-gram Speculative Decoding:**
- Scans recent context for repeated n-gram patterns
- Zero extra memory, trivially fast, no model training required
- Works well for repetitive content (code, structured data) [24]

### 4.4 Where It Works Best and Worst

| Workload Type | Acceptance Rate | Notes |
|---|---|---|
| Code with clear patterns | 75-85% | High repetition aids prediction |
| Formal writing, structured data | 75-85% | Predictable completions |
| Standard chat/instruction | 60-80% | Typical production workload |
| Creative writing, poetry | 40-60% | Unpredictable turns |
| Open-ended reasoning | 40-60% | Novel content is hard to draft |

### 4.5 Production Ecosystem

All major inference engines support speculative decoding natively:
- vLLM: EAGLE-3 support, speculative decoding with thinking budget for reasoning models [5]
- TensorRT-LLM: native support, 3.6x throughput on H200 [24]
- SGLang: Multi-Token Prediction via EAGLE, 1.8x decode speedup at batch size 1, 1.5x at batch size 32 on H200 [8]

### 4.6 Research Frontiers

- UC Berkeley PhD dissertation (December 2025) quantifies theoretical upper bounds and shows existing methods fall far short, reframing speculative decoding as a verification efficiency problem [24]
- OSD (Online Speculative Decoding): improves token acceptance rates on the fly via knowledge distillation, without increasing draft model size [24]
- SpecForge: maturing domain-specific speculation tools [24]

---

## 5. Prompt Caching and Context Reuse

### 5.1 What It Is

Prompt caching reuses pre-computed KV tensors for repeated prompt prefixes, so the static portion of every request (system prompt, tool definitions, reference documents) is processed once and reused. The model produces byte-identical output [25][26].

### 5.2 Provider Landscape (2026)

| Provider | Launch | Pricing | Mechanism |
|---|---|---|---|
| **Anthropic** | Aug 2024 beta, Dec 2024 GA | Cache reads at 0.1x base input cost (90% discount) | Automatic + explicit `cache_control` markers |
| **OpenAI** | Oct 2024 | Up to 90% discount on newer models | Automatic on prompts > 1,024 tokens; optional `prompt_cache_retention` (24hr) |
| **Google** | May 2024 (I/O), May 2025 (implicit for Gemini 2.5) | Varies | Explicit context caching + implicit caching on Gemini 2.5 models |

### 5.3 Cost Impact

- **ProjectDiscovery production case:** Cache hit rate from 7% to 84%, cutting total LLM spend by 59-70% with a single architectural change [26]
- **Agentic tasks evaluation:** 41-80% API cost reduction, 13-31% TTFT improvement across three major providers [27]
- **Customer support application (Agentbrisk, April 2026):** 4,000-token system prompt + 6,000-token RAG context, 95% cache hit rate, 76% total cost reduction ($10,000/month to $2,361/month) [26]

### 5.4 Why It Matters More Over Time

Average prompt token count grew ~4x between early 2024 and late 2025 (from ~1,500 to ~6,000 tokens per request). Longer prompts make caching more valuable [26].

### 5.5 Prompt Structure Best Practices

1. **Place stable content first:** System prompts, tool definitions, reference documents at the beginning
2. **Keep shared content exactly consistent:** Even a date injected into the system prompt prevents caching
3. **Dynamic content last:** Timestamps, user IDs, session identifiers, per-request metadata at the end
4. **Minimum prefix lengths:** Typically 1,024 tokens (provider-dependent)
5. **Cache eviction:** Time-based windows of 5 minutes to 1 hour (provider-dependent) [26]

### 5.6 Multi-Tier Caching Architecture

Production systems implement multiple caching layers:

```
Request --> Semantic Cache (100% savings, 20-45% hit rate)
        --> Prefix Cache (50-90% savings, per-provider)
        --> Full Inference
```

- **Semantic caching:** Bypasses LLM calls entirely on cache hits. Returns cached responses for semantically similar prompts. Production hit rates: 20-45% of total traffic (per Technion benchmarks, 2026) [26]
- **Prefix caching:** Only reduces input-side costs. Works on exact prefix matches. 50-90% savings on matching requests [26]
- **vCache:** Returns cached responses for semantically similar prompts under user-defined error-rate guarantees [26]

### 5.7 Strategic Cache Boundary Control

For agentic tasks, caching only system prompts while excluding dynamic tool results provides more consistent benefits than naive full-context caching. Strategic boundary control is critical when tool outputs change between turns [27].

---

## 6. Batching Strategies

### 6.1 Static Batching

Collects N requests, runs them all through the model simultaneously, and returns results only after every request finishes. Research from the Orca paper (Microsoft) showed static batching wastes 50-70% of GPU compute on padding and idle cycles [28][29].

### 6.2 Dynamic Batching

Adds a timeout before starting a batch, but a batch still cannot return early once it begins. Better than static for reducing initial wait times, but still forces all requests to complete together [29].

### 6.3 Continuous Batching (In-Flight Batching)

The standard approach in 2026. Each sequence in a batch finishes independently and is immediately replaced with a new request [28][29][30].

**Key techniques combined:**
- KV caching to avoid recomputing past token representations
- Chunked prefill to handle variable-length prompts within memory constraints
- Ragged batching with dynamic scheduling to eliminate padding waste [29]

**Performance:**
- Orca paper: up to 36.9x higher throughput vs. static batching [30]
- vLLM with continuous batching: 23x throughput improvement with reduced p50 latency [28]
- Supported by: vLLM, SGLang, TensorRT-LLM (in-flight batching), LMDeploy (persistent batching), MAX [30]

### 6.4 The Prefill-Decode Challenge

Every LLM request has two phases with completely different hardware profiles:
- **Prefill:** Processes entire input prompt at once (compute-bound, high GPU utilization)
- **Decode:** Generates output tokens one at a time (memory-bound, lower GPU utilization)

**Chunked prefill** splits the prompt into smaller token ranges and schedules them across multiple iterations, preventing long prefills from blocking ongoing decode operations [29].

### 6.5 Advanced Batching (2025-2026)

**BucketServe (July 2026):** Groups requests into size-homogeneous buckets based on sequence length, minimizing padding overhead. Results: up to 3.58x higher throughput and nearly 2x greater system load capacity while maintaining SLO attainment [30].

**Dynamic Prefix Bucketing:** Employs prefix bucketing locally and prefix-aware routing globally to avoid GPU idle time. Combined with continuous batching: 50.7% total speedup over baseline [30].

### 6.6 When to Use Which

| Strategy | Best For | Trade-off |
|---|---|---|
| **Continuous batching** | Most LLM deployments, high concurrency | Standard default, no real downside |
| **Dynamic batching** | Non-LLM models, low concurrency | Faster TTFT under light load |
| **Static batching** | Legacy systems, very simple setups | Never recommended for LLMs in 2026 |
| **Chunked prefill** | Mixed long/short prompts | Prevents prefill interference with decode |
| **BucketServe** | Heterogeneous request lengths | Reduces padding waste significantly |

---

## 7. Hardware Landscape

### 7.1 NVIDIA

#### H100 (Hopper)

The workhorse of 2024-2025 that remains widely deployed:
- 80GB HBM3, 3.35 TB/s bandwidth
- ~1,979 TFLOPS FP8
- Cloud pricing: $2.85-3.50/hr (stabilized early 2026) [31]
- Regional providers: $2.20-2.60/hr with reduced SLAs [32]

#### H200 (Hopper Enhanced)

- 141GB HBM3e, 4.8 TB/s bandwidth
- Same compute as H100, more memory and bandwidth
- Llama 4 Maverick on 8x H200: 6,694 output tok/s at 1,024 concurrent requests [5]

#### B200 (Blackwell)

The 2026 flagship:
- 192GB HBM3e, 8.0 TB/s bandwidth (2.4x H100) [33]
- ~2,250 TFLOPS FP16, ~4,500 TFLOPS FP8, ~9,000 TFLOPS FP4 [33]
- 208 billion transistors across two dies connected by 10 TB/s inter-die interconnect [33]
- 1000W TDP (43% over H100/H200's 700W) [33]

**Key benchmarks:**
- 60,000 tok/s per GPU, 1,000 tok/s per user on GPT-OSS [33]
- Llama 3.3 70B: 10,000+ TPS per GPU at 50 TPS per user -- 4x H200 per-GPU throughput [33]
- Cost per million tokens dropped from $0.11 at launch to $0.02 within two months (software optimization alone) [33]
- 7x inference cost reduction vs. H100 ($0.02/M tokens vs. $0.14/M tokens) [33]
- 15x cost reduction vs. previous generation [33]

**Pricing:**
- DGX B200 8-GPU: $280,000-$320,000 (~$35,000-$40,000 per GPU at system level) [33]
- Cloud: $5.91-$16.11 per GPU-hour across 11 providers (August 2026) [33]
- Individual GPUs: $45K-$55K range [33]

**B200 ROI:** $5M investment in GB200 NVL72 generates $75M in documented token revenue (15x ROI) [33].

#### B300 (Blackwell Ultra)

- At $3.29/hr vs. B200's $2.68/hr (23% more), with effectively the same FP8/FP16 throughput at typical batch sizes [33]
- B200 is the clear choice for 7B-70B models at FP8/FP4 [33]

#### Rubin (Next Gen)

- vLLM already has sm_107 target support [5]
- CPX variant designed specifically for prefill throughput in disaggregated inference [12]

### 7.2 AMD

#### MI300X

- 192GB HBM3, ~1,300 BF16 TFLOPS [34]
- Pricing: ~$15,000 vs. NVIDIA's ~$25,000-30,000 (better $/TFLOP for memory-bound workloads) [34]
- Latency 37-75% higher than H200 across tested configurations (software stack maturity gap) [34]
- ROCm improving: SemiAnalysis revised its assessment from "0% chance of breaking CUDA moat" (Dec 2024) to "a great chance of success" (July 2026) [34]

#### MI350X / MI355X (CDNA4, 3nm)

- Launched June 2025 [35]
- MI355X: 288GB HBM3E, 10.1 PFLOPS MXFP4 [34]
- Up to 4x peak performance over MI300X [35]
- MLPerf Inference v6.0 (April 2026): 9 partners submitted results, partner results within 4% of AMD's submission [35]

#### MI400 Series

- Planned for 2026 on CDNA5 architecture [35]

### 7.3 Groq LPU

**Architecture:** Custom Language Processing Unit keeps entire model weights in fast on-chip SRAM rather than external HBM. Deterministic scheduling eliminates memory-wait stalls [36].

**Inference speed (2026):**
- Llama 3.1 8B: 840 TPS [36]
- Llama 3 70B: ~2,100 tok/s (vs. 280-450 on comparable GPU) [36]
- Llama 4 Scout: 460+ tok/s (vs. 100-150 on H100) [36]
- GPT OSS 120B: 500 TPS [36]
- GPT OSS 20B: 1,000 TPS [36]
- TTFT: typically < 100ms (vs. 200-500ms on GPU) [36]

**Pricing (June/July 2026):**
- Llama 3.1 8B: $0.05/$0.08 per 1M tokens [36]
- Llama 3.3 70B: $0.59/$0.79 per 1M tokens [36]
- DeepSeek R1 Distill 70B: $0.75/$0.99 per 1M tokens [36]
- Batch API: 50% discount; prompt caching: additional 50% [36]

**Limitations:** Inference-only (no training), runs only open-weight models [36].

**Company status:** NVIDIA acquired Groq's LPU architecture for ~$20B (deal under antitrust scrutiny, April 2026). Founded by former Google TPU engineers, 2M+ developers, $750M raised at $6.9B valuation [36].

### 7.4 Cerebras

**WSE-3 (Wafer-Scale Engine 3):**
- 46,225 mm², 4 trillion transistors, 900,000 AI cores [37]
- 125 PFLOPS peak compute [37]
- 44GB on-chip SRAM with 7,000x more memory bandwidth than H100 [37]

**Key benchmarks:**
- Llama 3.1-405B: 969 tok/s, TTFT 240ms (up to 75x faster than GPU clouds) [37]
- Llama 4 Maverick: 2,500 tok/s per user (2x+ DGX B200 Blackwell) [37]
- Kimi K2.6: 981 tok/s, 5.6s end-to-end for 10K prompt + 500 reply [37]
- GPT-5.6 Sol Ultrafast Mode (August 2026): 750 output tok/s, 14x faster than Standard mode, 11x faster than Fable 5, 5x faster than Opus 4.8 Fast [37]

**CS-4 System (2026):**
- Up to 2x faster tok/s than CS-3; 30x faster than GPU solutions on frontier models [37]
- 6x more memory bandwidth, compute, fabric bandwidth vs. CS-3 [37]
- Three WSE-3 Turbo chips per rack, each with 43 PB/s memory bandwidth [37]

**Roadmap:** Plan to double raw performance annually through 2027, aiming for 20x throughput by 2027. CS-5 planned for 2027 [37].

### 7.5 AWS Trainium

#### Trainium2

- 30-40% better price-performance than GPU-based EC2 P5e/P5en instances [38]
- ~2.6x cheaper per hour but 4x less raw BF16 throughput [38]
- 2TB aggregate HBM across 16 chips on trn2.48xlarge (vs. 640GB on p5en) [38]
- For memory-bandwidth-bound workloads: 40-60% cheaper per training run [38]
- Itau Unibanco: 7x throughput improvement vs. GPUs for batch and online inference [38]

#### Trainium3

- 3nm chip, 2.52 PFLOPS FP8 per chip, 144GB HBM3e, 4.9 TB/s bandwidth [38]
- 4.4x more compute, 4x greater energy efficiency vs. Trainium2 [38]
- 50% reduction in training and inference costs (claimed) [38]
- Single Trn3 UltraServer: 144 chips, 362 FP8 PFLOPS total [38]

**Key caveat:** Neuron SDK ecosystem is 3-4 years behind CUDA. Migration cost analysis: porting a production vLLM stack takes ~3 weeks ($36K), requiring ~109B tokens served before break-even vs. H200 spot pricing. At 1B tokens/day, break-even is 109 days; at 10M tokens/day, it is 30 years [38].

#### Trainium4

In development; promised 6x FP4 throughput, 3x FP8 performance, 4x memory bandwidth vs. Trainium3. Will support NVIDIA NVLink Fusion for hybrid deployments [38].

### 7.6 Google TPU

**TPU v7 Ironwood (2026):**
- 192GB HBM3E, 7.37 TB/s bandwidth
- 4,614 FP8 TFLOPS
- 9.6 Tb/s inter-chip interconnect
- Analysts describe it as "on par with Blackwell" [34]

### 7.7 Hardware Decision Matrix

| Accelerator | Strength | Weakness | Best For |
|---|---|---|---|
| **NVIDIA B200** | Ecosystem, raw throughput, software maturity | Price, 1000W TDP | General-purpose production |
| **AMD MI355X** | Price/TFLOP, 288GB HBM3E | ROCm maturity | Cost-sensitive GPU workloads |
| **Groq LPU** | Latency (< 100ms TTFT), 500+ tok/s | Inference-only, limited models | Latency-critical applications |
| **Cerebras WSE-3** | 30x GPU speed on frontier models | Specialized infrastructure | Ultra-fast inference |
| **AWS Trainium3** | Price at scale within AWS | Vendor lock-in, Neuron SDK | AWS-native high-volume |
| **Google TPU v7** | On par with Blackwell | GCP-only | GCP-native workloads |

### 7.8 Market Trends

- NVIDIA maintains ~80% market share [34]
- AI server shipments on custom ASICs projected to grow 44.6% in 2026 vs. 16.1% for GPU-based servers [34]
- The software stack (PyTorch, Triton, FlashAttention, vLLM, TensorRT-LLM) is years ahead on NVIDIA [34]

---

## 8. Cost Optimization Strategies

### 8.1 Model Routing

Route queries to the optimal model based on complexity, sending simple queries to cheap models and only complex queries to expensive frontier models [39][40].

**Key frameworks:**
- **RouteLLM (UC Berkeley, ICLR 2025):** 85%+ cost reduction on MT Bench while maintaining 95% of GPT-4 performance, sending only 14% of queries to the strong model [40]
- **FrugalGPT (Stanford):** Cascade tries cheaper models in sequence, stops when confidence threshold is met. Results: 50-98% cost savings vs. always using the best model [40]
- **RouteNLP (arXiv 2026):** Closed-loop routing with conformal prediction cascading. Production: 58% cost savings within 7% of simulation predictions [40]
- **Not Diamond:** Meta-model predicting which downstream LLM performs best per query, plus automatic prompt adaptation (5-60% accuracy improvements) [40]

**Enterprise finding:** Only 25-35% of queries require frontier models. One financial services enterprise cut $200K+/month LLM costs [40].

### 8.2 Cascade Patterns

```
Request --> Cheapest Model (e.g., Haiku, $0.05/MTok)
        --> If confidence < threshold: Mid-tier (e.g., Sonnet, $3/MTok)
        --> If still < threshold: Frontier (e.g., Opus, $15/MTok)
```

**Critical failure mode:** Silent quality degradation. If the confidence threshold is too loose, the cheap model returns plausible-sounding responses with errors. No alerts fire, latency looks good, but quality has quietly degraded. Requires downstream quality monitoring [40].

**Latency overhead:** Escalated queries pay for both the cheap-model call and the flagship-model call (e.g., 800ms + 1.5s = 2.3s end-to-end) [40].

### 8.3 Knowledge Distillation

Transfer knowledge from large teacher models to compact student models, achieving 5-30x cost reduction with 95-97% of original performance [41].

**Production results:**
- 75% inference cost reduction reported without sacrificing response quality [41]
- DeepSeek R1-Distill-Qwen-32B: ~85% of R1's reasoning at 1/20th inference cost [41]
- Microsoft: Llama 3.1 405B teacher to 8B student: 21% better accuracy vs. directly prompting the 8B model [41]
- Microsoft Phi-3 Mini (3.8B): ~31% improvement under distillation [41]
- Apple Intelligence: On-device models distilled from larger server-side models [41]

**Optimal compression pipeline (2025 study):**
Pruning -> Distillation -> Quantization (P-KD-Q) performs best [41].

### 8.4 API Pricing Landscape (2026)

#### Frontier Tier
| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| Claude Fable 5 | $10.00 | $50.00 |
| GPT-5.6 Sol | $5.00 | $30.00 |
| Claude Opus 5 | $5.00 | $25.00 |
| Claude Opus 4.6 | $5.00 | $25.00 |

#### Mid Tier (Production Workhorses)
| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| Claude Sonnet 4 | $3.00 | $15.00 |
| GPT-5.2 | $1.75 | $14.00 |
| Gemini 3.1 Pro | $2.00 | $12.00 |
| GPT-5.6 Terra | $2.00 | $12.00 |

#### Budget Tier
| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| Claude Haiku 4.5 | $1.00 | $5.00 |
| DeepSeek V4 Flash | $0.14 | $0.28 |
| Gemini 2.5 Flash | $0.075 | $0.30 |
| GPT-4.1 Nano | $0.10 | $0.40 |

#### Open-Weight Hosted (Groq, Together, Fireworks)
| Model | Input $/MTok | Output $/MTok |
|---|---|---|
| Llama 4 Scout | $0.18 | $0.59 |
| Llama 3.1 8B (Groq) | $0.05 | $0.08 |

**Key patterns:**
- Output tokens priced 4-8x higher than input across all providers [32]
- LLM API pricing dropped ~80% between early 2025 and early 2026 [32]
- Historical: GPT-3 cost $60/MTok in Nov 2021; multiple models now < $0.06/MTok [32]
- Llama 4 Scout is 16x cheaper on input and 25x cheaper on output vs. Claude Sonnet 4.6 [32]

### 8.5 Self-Hosting Economics

- Llama 4 70B on H200 at $2.49/hr: ~$0.13/MTok [32]
- Break-even vs. API: typically 10-50M tokens/day [32]
- Above 50M tokens/day: self-hosting is 50-80% cheaper than API [32]
- Below 10M tokens/day: APIs are almost always cheaper [32]

### 8.6 Prompt Optimization

- Switch to smaller open-source models where feasible
- Implement prompt caching for repeated contexts (90% input cost reduction)
- Batch non-urgent inference (providers offer 50% batch discounts)
- These three tactics often cut costs 50-80% [32]
- Compare cost per completed task, not cost per token [32]

### 8.7 Production Routing Tooling (2026)

| Tool | Description |
|---|---|
| **Vercel AI Gateway** (GA Aug 2025) | Zero-markup, per-request routing across 40+ providers with automatic failover |
| **Not Diamond** | Meta-model predicting best LLM per query with prompt adaptation |
| **Amazon Bedrock IPR** (GA April 2025) | Serverless routing within model families |
| **vLLM Semantic Router** | ModernBERT-based classifier for query complexity routing |

### 8.8 Practical Routing Implementation Order

1. **Start with static rules** if workload has obvious routing signals (input length, user tier, endpoint)
2. **Add cascade** once obvious rules are exhausted
3. **Move to classifier-based routing** only after both static and cascade are in place and volume justifies it
4. Skipping straight to classifier-based is a classic over-engineering trap [40]

---

## 9. Edge and On-Device Inference

### 9.1 The State of On-Device LLMs (2026)

Running capable language models on phones and laptops without internet has moved from research project to product decision. Apple Neural Engine, Qualcomm Hexagon NPU, MediaTek APU, and consumer GPUs have collectively made on-device AI not just feasible, but often preferable for interactive features [42][43].

**Latency advantage:** A local 7B model responds in 200-400ms on a modern laptop, while a cloud API call to equivalent quality is 600-1200ms just for the first token [42].

### 9.2 Key Inference Runtimes

#### llama.cpp

The de facto community standard with ~91K GitHub stars and multiple weekly releases [42].
- CPU-first philosophy with GGUF single-file format
- CPU+GPU hybrid inference (partial VRAM, partial system RAM offloading)
- Most portable option across platforms
- Community has extended support far beyond LLaMA family

#### MLX (Apple)

Apple's framework designed specifically for Apple Silicon's unified memory architecture [42][43].

**Performance advantage:**
- 30-50% faster than llama.cpp on Apple Silicon for token generation [43]
- Mac mini M4 Pro (64GB) on Qwen3-Coder-30B-A3B MoE: ~130 tok/s (vs. Ollama's 43 tok/s, a 3x difference) [43]
- MLX-Swift: 1.4x-1.8x over llama.cpp on decode [43]
- M4 Max (128GB) on Qwen3.5-35B-A3B: 130 tok/s (MLX) vs. 43.5 tok/s (Ollama) [44]
- MetalRT engine: 658 tok/s on M4 Max with small optimized models [44]

**Key development:** Ollama announced switch to MLX as its inference engine on Apple Silicon in March 2026 (currently in preview). Apple optimized M5 hardware at chip design stage for MLX [43].

#### ExecuTorch (Meta)

Production standard for mobile:
- Reached 1.0 GA (October 2025), v1.1.0 (January 2026) [42]
- 50KB base runtime (smallest of any framework) [42]
- Supports 12+ hardware backends: Apple Core ML, Qualcomm QNN/Hexagon NPU, Arm XNNPACK with KleidiAI, MediaTek, Samsung Exynos, Vulkan, NXP [42]
- Powers: Instagram, WhatsApp, Messenger, Quest 3, Ray-Ban Smart Glasses (billions of users) [42]

#### LiteRT-LM (Google)

Google's open-source edge LLM framework:
- Powers Chrome, Chromebook Plus, Pixel Watch [42]
- Supports Gemma 4, Llama, Phi-4, Qwen across Android, iOS, Web, Desktop, Raspberry Pi [42]
- "Near-zero latency" offline inference in collaboration with Pixel, Qualcomm, MediaTek [42]

### 9.3 Apple Silicon Performance

| Chip | Memory | Bandwidth | 7B Q4 Performance | Sweet Spot |
|---|---|---|---|---|
| **M4 Pro (24GB)** | 24-48GB | ~273 GB/s | 20-30 tok/s | 8B-22B models |
| **M4 Max** | 64-128GB | 546 GB/s | 75-90 tok/s | Up to 70B at Q4 |
| **M5 Pro** | - | Higher | 4.06x faster TTFT vs. M4 | Next-gen baseline |

The M4 Max with 128GB can run 70B models quantized to 4-bit comfortably -- the gold standard for CPU-based LLM inference [44]. For 96GB+ Macs running 70B+ models, MLX-LM gives 10-30% better throughput than llama.cpp Metal [44].

### 9.4 Mobile Hardware

#### Qualcomm Snapdragon 8 Elite
- 45 TOPS NPU, 12GB LPDDR5X [42]
- Runs 7B models at 20+ tok/s [42]
- QMX (Qualcomm Matrix Extension) for fast on-device AI [42]

#### MediaTek Dimensity 9500
- First mobile chipset with native BitNet 1.58-bit processing support [42]
- Claimed 33% lower power consumption [42]
- Pursuing compute-in-memory architecture for memory bandwidth bottleneck [42]

### 9.5 Key Edge Techniques

**INT4 for NPU targets:** Native framework INT4 quantization tuned for specific NPU hardware generally outperforms llama.cpp Q4_0 on phones, sometimes matching Q4_K_M. Always prefer the native framework's quantization path over GGUF when targeting phone NPUs [42].

**BitNet b1.58 2B4T (April 2025):**
- First open-source native 1-bit LLM at 2B parameters [42]
- 0.4 GB non-embedding weights [42]
- 29ms decoding latency on CPU [42]
- 6x less energy per inference than Gemma 3 1B [42]
- Outperforms Llama 3.2 1B, Gemma 3 1B, Qwen 2.5 1.5B on standard benchmarks [42]

**Speculative decoding on-device:** Intel and Weizmann Institute (ICML 2025) showed any small draft model can accelerate any LLM regardless of vocabulary differences, delivering up to 2.8x faster inference [42].

**MoE on edge:** Over 60% of 2025 frontier model releases adopted MoE designs. For edge: large-model capability with small-model compute. Challenge is fitting all experts in memory (quantization and offloading techniques are essential) [42].

### 9.6 Practical Deployment Path

1. **Prototype** in Ollama
2. **Port** to platform-native runtime:
   - Apple: MLX / MLX-Swift
   - Mobile (Android/iOS): ExecuTorch / LiteRT-LM
   - NVIDIA Jetson: TensorRT Edge-LLM
   - General: llama.cpp for broadest compatibility
3. **Ship** with platform-specific quantization (not generic GGUF) [42]

---

## Sources

### Model Serving Infrastructure

[1] Spheron Network, "vLLM vs TensorRT-LLM vs SGLang: Which Is Fastest? (H100 Benchmarks, 2026)," https://www.spheron.network/blog/vllm-vs-tensorrt-llm-vs-sglang-benchmarks/

[2] Yotta Labs, "Best LLM Inference Engines (2026): vLLM, SGLang & TensorRT-LLM," https://www.yottalabs.ai/post/best-llm-inference-engines-in-2026-vllm-tensorrt-llm-tgi-and-sglang-compared

[3] n1n.ai, "A Comprehensive Comparison of LLM Inference Engines: vLLM, TGI, TensorRT-LLM, SGLang, llama.cpp, and Ollama," https://explore.n1n.ai/blog/llm-inference-engine-comparison-vllm-tgi-tensorrt-sglang-2026-03-13

[4] Tech Insider, "vLLM vs Ollama 2026: 9x Throughput Gap, 172K Stars [Tested]," https://tech-insider.org/vllm-vs-ollama-2026/

[5] vLLM GitHub Releases, https://github.com/vllm-project/vllm/releases

[6] AI FOSS, "vLLM Review 2026: Production LLM Inference at Scale," https://aifoss.dev/blog/vllm-review-2026/

[7] SGLang Paper, "SGLang: Efficient Execution of Structured Language Model Programs," https://arxiv.org/pdf/2312.07104

[8] Particula Tech, "SGLang vs vLLM in 2026: Benchmarks, Architecture, and When to Use Each," https://particula.tech/blog/sglang-vs-vllm-inference-engine-comparison

[9] n4n AI, "Benchmarking SGLang's RadixAttention for multi-turn chat," https://n4n.ai/blog/benchmarking-sglangs-radixattention-for-multi-turn-chat/

[10] NVIDIA, "TensorRT-LLM Production Deployment on GPU Cloud: Engine Build, Multi-GPU Serving, and In-Flight Batching (2026)," https://www.spheron.network/blog/tensorrt-llm-production-deployment-guide/

[11] Lyceum Technology, "vLLM vs TensorRT-LLM: 2026 Production Benchmarks," https://lyceum.technology/magazine/vllm-vs-tensorrt-llm-production-benchmark

[12] Hao AI Lab @ UCSD, "Disaggregated Inference: 18 Months Later," https://haoailab.com/blogs/distserve-retro/

[13] BentoML, "Prefill-decode disaggregation," https://bentoml.com/llm/inference-optimization/prefill-decode-disaggregation

### Quantization Techniques

[14] VRLA Tech, "LLM Quantization Explained: INT4, INT8, FP8, AWQ, and GPTQ in 2026," https://vrlatech.com/llm-quantization-explained-int4-int8-fp8-awq-and-gptq-in-2026/

[15] Local AI Master, "GGUF vs GPTQ vs AWQ 2026: Which Quantization Should You Use?," https://localaimaster.com/blog/quantization-explained

[16] Digital Applied, "Quantization Tradeoffs: 4-bit vs 8-bit vs FP8 Data," https://www.digitalapplied.com/blog/quantization-tradeoffs-4bit-8bit-fp8-performance-data

[17] Baseten, "33% faster LLM inference with FP8 quantization," https://www.baseten.co/blog/33-faster-llm-inference-with-fp8-quantization/

[18] NVIDIA Technical Blog, "Introducing NVFP4 for Efficient and Accurate Low-Precision Inference," https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/

[19] Jarvis Labs, "The Complete Guide to LLM Quantization with vLLM: Benchmarks & Best Practices," https://jarvislabs.ai/blog/vllm-quantization-complete-guide-benchmarks

[20] Local AI Master, "Knowledge Distillation: Compress 671B Models to 7B (2026)," https://localaimaster.com/blog/knowledge-distillation-guide

### KV Cache Optimization

[21] Introl Blog, "KV Cache Optimization: Memory Efficiency for Production LLMs," https://introl.com/blog/kv-cache-optimization-memory-efficiency-production-llms-guide

[22] Spheron Network, "KV Cache Optimization: Serve 10x More Users per GPU (2026)," https://www.spheron.network/blog/kv-cache-optimization-guide/

### Speculative Decoding

[23] Introl Blog, "Speculative Decoding: Achieving 2-3x LLM Inference Speedup," https://introl.com/blog/speculative-decoding-llm-inference-speedup-guide-2025

[24] BentoML, "Get 3x Faster LLM Inference with Speculative Decoding Using the Right Draft Model," https://www.bentoml.com/blog/3x-faster-llm-inference-with-speculative-decoding

### Prompt Caching

[25] Digital Applied, "Prompt Caching in 2026: Cut LLM Costs, Keep Quality," https://www.digitalapplied.com/blog/prompt-caching-2026-cut-llm-costs-engineering-guide

[26] NeuralTrust, "LLM Caching Strategies: Prompt Caching, Semantic Caching, and When to Use Each," https://neuraltrust.ai/blog/llm-caching-strategies

[27] arXiv, "An Evaluation of Prompt Caching for Long-Horizon Agentic Tasks," https://arxiv.org/html/2601.06007v2

### Batching Strategies

[28] Anyscale, "Achieve 23x LLM Inference Throughput & Reduce p50 Latency," https://www.anyscale.com/blog/continuous-batching-llm-inference

[29] BentoML, "Static, dynamic and continuous batching," https://bentoml.com/llm/inference-optimization/static-dynamic-continuous-batching

[30] arXiv, "BucketServe: Bucket-Based Dynamic Batching for Smart and Efficient LLM Inference Serving," https://arxiv.org/html/2507.17120v1

### Hardware Landscape

[31] NVIDIA Blog, "Blackwell Raises Bar in New InferenceMAX Benchmarks," https://blogs.nvidia.com/blog/blackwell-inferencemax-benchmark-results/

[32] Featherless, "LLM API Pricing Comparison 2026: The Complete Guide to Inference Costs," https://featherless.ai/blog/llm-api-pricing-comparison-2026-complete-guide-inference-costs

[33] Inworld, "NVIDIA B200 GPU: Specs, Pricing, and Cloud Availability (2026)," https://inworld.ai/resources/nvidia-b200-gpu-cloud

[34] AI Multiple, "Top 30+ AI Chip Makers: NVIDIA & Its Competitors," https://aimultiple.com/ai-chip-makers

[35] AMD Blog, "AMD Delivers Breakthrough MLPerf Inference 6.0 Results," https://www.amd.com/en/blogs/2026/amd-delivers-breakthrough-mlperf-inference-6-0-results.html

[36] eesel AI, "Groq pricing in 2026: every model, free tier, and hidden discounts explained," https://www.eesel.ai/blog/groq-pricing

[37] Cerebras, "Cerebras Powers Ultrafast Mode for OpenAI's GPT-5.6 Sol," https://www.cerebras.ai/blog/accelerating-gpt-5-6-sol-ultrafast-with-openai

[38] Spheron Network, "AWS Trainium 3 vs NVIDIA H200 and B200 for LLM Training and Inference," https://www.spheron.network/blog/aws-trainium-3-vs-nvidia-h200-b200-llm-training-inference-2026/

### Cost Optimization

[39] Digital Applied, "LLM Model Routing in 2026: Cost-Quality Optimization," https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide

[40] TianPan.co, "LLM Routing and Model Cascades: How to Cut AI Costs Without Sacrificing Quality," https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades

[41] Redis, "Model Distillation for LLMs: Cut Costs & Boost Speed in 2026," https://redis.io/blog/model-distillation-llm-guide/

### Edge and On-Device Inference

[42] On-Device LLMs: State of the Union 2026, https://v-chandra.github.io/on-device-llms/

[43] Yage AI, "MLX vs llama.cpp on Apple Silicon: Benchmarks, M5 Neural Accelerators, and Why Ollama Switched," https://yage.ai/share/mlx-apple-silicon-en-20260331.html

[44] Local AI Master, "Best Mac for Local AI 2026: Every Apple Silicon Chip Ranked (M1-M5)," https://localaimaster.com/blog/apple-silicon-ai-buying-guide

### Additional Sources

[45] PremAI Blog, "KV Cache Optimization: PagedAttention, Prefix Caching & Memory Management," https://blog.premai.io/kv-cache-optimization-pagedattention-prefix-caching-memory-management/

[46] PremAI Blog, "Speculative Decoding: 2-3x Faster LLM Inference (2026)," https://blog.premai.io/speculative-decoding-2-3x-faster-llm-inference-2026/

[47] NVIDIA Technical Blog, "An Introduction to Speculative Decoding for Reducing Latency in AI Inference," https://developer.nvidia.com/blog/an-introduction-to-speculative-decoding-for-reducing-latency-in-ai-inference/

[48] PyTorch Blog, "FlashAttention-3: Fast and Accurate Attention with Asynchrony and Low-precision," https://pytorch.org/blog/flashattention-3/

[49] arXiv, "FlashAttention-3: Fast and Accurate Attention with Asynchrony and Low-precision," https://arxiv.org/abs/2407.08608

[50] Introl Blog, "AI Accelerators Beyond GPUs: TPU, Trainium, Gaudi, Cerebras," https://introl.com/blog/ai-accelerators-beyond-gpus-tpu-trainium-gaudi-cerebras

[51] Cerebras, "Cerebras Delivers Record-Breaking Performance with Meta's Llama 3.1-405B Model," https://www.cerebras.ai/press-release/cerebras-inference-llama-405b

[52] SemiAnalysis, "Groq Inference Tokenomics: Speed, But At What Cost?," https://newsletter.semianalysis.com/p/groq-inference-tokenomics-speed-but

[53] ngrok Blog, "Prompt caching: 10x cheaper LLM tokens, but how?," https://ngrok.com/blog/prompt-caching

[54] Introl Blog, "Prompt Caching Infrastructure," https://introl.com/blog/prompt-caching-infrastructure-llm-cost-latency-reduction-guide-2025

[55] DigitalOcean, "Prefill/Decode Disaggregation: Why Production LLM Inference Is Splitting Onto Separate Hardware," https://www.digitalocean.com/community/tutorials/prefill-decode-disaggregation

[56] JarvisLabs, "Disaggregated Prefill-Decode: The Architecture Behind Meta's LLM Serving," https://jarvislabs.ai/blog/llm-optimization-disaggregated-prefill-decode

[57] arXiv, "KV Cache Optimization Strategies for Scalable and Efficient LLM Inference," https://arxiv.org/html/2603.20397v1

[58] Sesame Disk, "Quantization Techniques for AI Inference in 2026: GGUF, AWQ, GPTQ, and FP8," https://sesamedisk.com/quantization-techniques-ai-inference-2026/

[59] ai.rs, "Quantization Methods Compared: GGUF, AWQ, GPTQ, EXL2, NVFP4," https://ai.rs/ai-developer/quantization-methods-compared

[60] Cast AI, "Demystifying Quantizations: LLMs," https://cast.ai/blog/demystifying-quantizations-llms/

[61] Meta Intelligence, "Model Quantization Guide: Run 70B LLMs in 4 Bits," https://www.meta-intelligence.tech/en/insight-quantization

[62] StackPulsar, "LLM Inference Engine Comparison 2026: vLLM vs TGI vs TensorRT-LLM," https://stackpulsar.com/blog/llm-inference-engine-comparison/

[63] Inference Engineering, "AI Inference Hardware Guide," https://inferenceengineering.tech/learn/ai-inference-hardware/

[64] Spheron Network, "Hyperscaler Custom AI Chips in 2026: Trainium 3, Google TPU, Maia 200, and Meta MTIA vs NVIDIA GPU," https://www.spheron.network/blog/hyperscaler-custom-ai-chips-2026-trainium-tpu-maia-mtia-vs-nvidia-gpu/

[65] arXiv, "RouteNLP: Closed-Loop LLM Routing with Conformal Cascading and Distillation Co-Optimization," https://arxiv.org/html/2604.23577v1

[66] arXiv, "Dynamic Model Routing and Cascading for Efficient LLM Inference: A Survey," https://arxiv.org/html/2603.04445v2

[67] Microsoft Community Hub, "Distillation: Turning Smaller Models into High-Performance Solutions," https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/distillation-turning-smaller-models-into-high-performance-cost-effective-solutio/4355029

[68] Medium, "How We Reduced LLM Inference Costs by 75% Using Model Distillation," https://vashishth04.medium.com/how-we-reduced-llm-inference-costs-by-75-using-model-distillation-without-sacrificing-response-df12cfe797e0

[69] Qualcomm Developer Blog, "Accelerating LLAMA inference on mobile CPUs using Qualcomm Matrix Extensions," https://www.qualcomm.com/developer/blog/2026/04/llama-models-acceleration-on-cpu-qmx

[70] Edge AI Stack, "The Edge LLM Runtime Stack 2026: llama.cpp vs Ollama vs TensorRT Edge-LLM vs ExecuTorch vs vLLM vs MLX," https://edgeaistack.ai/blog/edge-llm-runtime-stack-2026/

[71] RunAnywhere, "We Built the Fastest LLM Decode Engine for Apple Silicon. Here Are the Numbers.," https://www.runanywhere.ai/blog/metalrt-fastest-llm-decode-engine-apple-silicon

[72] Packet.ai, "LLM Inference Cost 2026: Cost per Million Tokens," https://packet.ai/blog/llm-inference-cost

[73] Inference.net, "LLM API Pricing Comparison 2026: 30+ Models, Every Provider," https://inference.net/content/llm-api-pricing-comparison/

[74] TensorWave, "Benchmark Breakdown: How AMD's MI300X, MI325X, and MI355X Are Redefining AI Inference Economics," https://tensorwave.com/blog/benchmark-breakdown-how-amds-mi300x-mi325x-and-mi355x-are-redefining-ai-inference-economics

[75] NVIDIA TensorRT-LLM Documentation, "Speed up inference with SOTA quantization techniques in TRT-LLM," https://nvidia.github.io/TensorRT-LLM/blogs/quantization-in-TRT-LLM.html

[76] Fabricio Narcizo, "Edge AI in Action: Mastering On-Device Inference (CVPR 2026)," https://www.fabricionarcizo.com/cvpr2026-edge-ai-in-action/

[77] Sara Zan, "Making sense of KV Cache optimizations, Ep. 1: An overview," https://www.zansara.dev/posts/2025-10-26-kv-caching-optimizations-intro/

[78] Spheron Network, "FP4 Quantization on Blackwell GPUs: Throughput, Cost, and When It's Worth It," https://www.spheron.network/blog/fp4-quantization-blackwell-gpu-cost/

[79] Glukhov.org, "Speculative Decoding: 20-50% Faster LLM Inference," https://www.glukhov.org/llm-performance/optimization/speculative-decoding/

[80] Daft AI, "Cutting LLM Batch Inference Time in Half: Dynamic Prefix Bucketing at Scale," https://www.daft.ai/blog/cutting-llm-batch-inference-time-in-half-dynamic-prefix-bucketing-at-scale
