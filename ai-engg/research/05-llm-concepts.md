# Research: LLM Concepts - A Ultimate Deep Dive
**Date researched**: 2026-09-29
**Sources consulted**: 42+

---

## 1. System Topology & Mechanics

### 1.1 Transformer Architecture Internals

The Transformer (Vaswani et al., 2017) replaced recurrence with self-attention, enabling parallel sequence processing. All modern LLMs (GPT, Claude, Gemini, Llama) are decoder-only variants of this architecture.

**Core Components (per layer):**

| Component | Function | Dimensionality (original) |
|---|---|---|
| Multi-Head Self-Attention | Captures token-to-token dependencies across all positions | d_model=512, 8 heads, d_k=64 |
| Position-wise FFN | Non-linear transformation per position; recent research suggests FFN layers function as key-value memories storing factual knowledge | d_ff=2048 (4x d_model) |
| Residual Connections | Gradient highway preventing vanishing gradients in deep stacks | Identity mapping around each sub-layer |
| Layer Normalization | Stabilizes activations; Pre-LN (before sub-layer) is now standard over Post-LN | Per-position normalization |

**Self-Attention Mechanics:**
1. Input embeddings are projected into Query (Q), Key (K), Value (V) matrices via learned weight matrices W_Q, W_K, W_V
2. Attention scores: `Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V`
3. The `sqrt(d_k)` scaling prevents softmax saturation as dimensionality grows
4. Multi-head attention runs h parallel attention functions, each learning different relationship patterns (syntactic, semantic, positional). Outputs are concatenated and projected: `MultiHead = Concat(head_1,...,head_h) W_O`

**Decoder-only LLMs use causal (masked) attention:** each token can only attend to itself and prior tokens, enforced by masking future positions to negative infinity before softmax. This is what enables autoregressive generation.

**Positional Encoding:**
- Original: sinusoidal fixed encoding (sin/cos at different frequencies), enabling generalization to unseen sequence lengths
- Modern: Rotary Position Embedding (RoPE) -- used by Llama, Qwen, Mistral -- encodes relative position directly into attention computation. Supports context extension via NTK-aware scaling or YaRN
- Learned embeddings (GPT-2 style) are largely abandoned for RoPE in current models

**Scale of Modern Models:**
- GPT-4: rumored ~1.8T parameters (MoE, 8 experts) [unconfirmed]
- Llama 3.1 405B: 405B dense parameters, 126 layers, 128 attention heads, d_model=16384
- Claude Opus 4: architecture undisclosed
- DeepSeek-V3: 671B total params, 37B active (MoE with 256 experts, top-8 routing)

### 1.2 Tokenization

Tokenizers convert text to integer sequences the model processes. Common algorithms:

| Tokenizer | Used By | Vocab Size | Approach |
|---|---|---|---|
| BPE (Byte-Pair Encoding) | GPT-4, Claude, Llama 3 | 100K-200K | Iteratively merges most frequent byte pairs |
| SentencePiece (Unigram) | T5, mT5 | 32K-256K | Probabilistic subword selection |
| tiktoken | OpenAI models | 100K+ | Optimized BPE implementation |

Token != word. "running" might be 1 token; "counterintuitive" might be 3. A rough heuristic: 1 token ~ 4 characters in English, ~0.75 words. Non-English and code can have much worse token efficiency.

**Tokenizer edge cases that cause production failures:**
- Trailing whitespace changes tokenization and can alter model behavior
- Numbers tokenize inconsistently: "123456" may split as ["123", "456"], breaking arithmetic
- Unicode homoglyphs can bypass content filters while appearing identical to humans

### 1.3 Embeddings & Latent Space

Each token ID maps to a learned embedding vector (e.g., d=4096 for Llama 3 8B). Semantically similar words cluster in embedding space -- the classic example: `vec("king") - vec("man") + vec("woman") ~ vec("queen")`.

Embedding models (distinct from generative LLMs) are purpose-built for encoding semantic similarity:
- OpenAI text-embedding-3-large: 3072 dimensions
- Cohere embed-v4: optimized for retrieval
- Open-source: BGE, GTE, E5

These power RAG retrieval, semantic search, and classification pipelines.

### 1.4 Inference Serving: Prefill vs. Decode

LLM inference splits into two phases with fundamentally different hardware bottlenecks:

| Phase | What happens | Bottleneck | Key metric |
|---|---|---|---|
| **Prefill** | Process entire input prompt in one forward pass; compute KV cache for all input tokens; produce first output token | **Compute-bound** -- saturates GPU tensor cores | TTFT (Time to First Token) |
| **Decode** | Generate tokens one at a time; each step reads full KV cache + model weights from memory | **Memory-bandwidth-bound** -- GPU compute units idle waiting for HBM reads | TPOT (Time Per Output Token) / TPS (Tokens Per Second) |

A 4096-token prompt on H100 takes ~50ms for prefill. Each decode step is ~10-20ms for a 70B model.

**KV Cache:**
The decode phase caches Key and Value tensors from all prior tokens to avoid recomputation.

KV cache size per token = `2 x num_layers x (num_heads x dim_head) x precision_bytes`

For Llama 3.1 70B at FP16: ~1.3 MB per token per sequence. At 128K context with batch size 32, KV cache alone requires ~5.3 TB -- this is why KV cache management is the central challenge of long-context serving.

**KV Cache Management Strategies:**
- **PagedAttention** (vLLM): Treats KV cache like virtual memory pages, eliminating fragmentation. Achieves 14-24x higher throughput than naive HF Transformers
- **Prefix caching**: Reuses KV cache across requests sharing common prefixes (system prompts, few-shot examples). Reduces TTFT for repeated prefixes by 5-10x
- **KV cache quantization**: INT8 KV is near-lossless in practice; INT4 KV has measurable but small quality loss, enables 2x more concurrent sequences
- **NVFP4**: 4-bit KV cache allowing ~2x more context on-device vs FP8; delivers up to 3x better TTFT latency through reduced evictions

### 1.5 Continuous Batching & Chunked Prefill

**Continuous batching** (a.k.a. iteration-level scheduling): admits new requests and releases finished ones at every decode step, rather than waiting for a fixed batch to complete. Key insight: sequences finish at different times; static batching wastes GPU cycles on padding.

**Chunked prefill**: Breaks long prefill into chunks (e.g., 512 tokens) interleaved with decode steps. Prevents long prompts from starving concurrent decode. The effect: P99 TTFT becomes bounded by chunk size; P99 decode latency doesn't spike when long prompts arrive.

**Tradeoff:** Smaller chunks protect streaming smoothness but reduce throughput; larger chunks protect throughput and TTFT but add latency variance.

### 1.6 Model Serving Infrastructure

**As of late 2026, the landscape has consolidated around three engines:**

| Engine | Key Innovation | Throughput | TTFT | Hardware | Status |
|---|---|---|---|---|---|
| **vLLM** | PagedAttention; broadest hardware support (CUDA + ROCm) | High (3,245 tok/s on Llama-2-70B, 4 GPU TP) | Good | NVIDIA + AMD | Active, community default |
| **TensorRT-LLM** | NVIDIA kernel optimization; compiled engine approach | Highest on NVIDIA (10,000+ output tok/s on H100 FP8, 64 concurrent) | Best (sub-10ms batch-1 on H100) | NVIDIA only | Active |
| **SGLang** | RadixAttention for KV reuse; tree-based speculative decoding | Competitive with vLLM, excels on prefix-heavy workloads | Good | NVIDIA + AMD | Active, rising |

**HuggingFace TGI**: Frozen December 2025, archived March 2026. Migrate away.

**Decision framework:**
- Shipping this week, general purpose: vLLM
- NVIDIA-committed, willing to invest in tuning: TensorRT-LLM
- Agentic/RAG with heavy prefix reuse: SGLang
- Also notable: LMDeploy (1.8x higher throughput than vLLM in some benchmarks)

### 1.7 Speculative Decoding

Core idea: A small, fast **draft model** proposes K candidate tokens; the large **target model** verifies all K in a single forward pass. Accepted tokens are free; rejected tokens fall back to standard sampling. Output quality is mathematically identical to standard decoding.

**Why it works:** Transformers compute attention over all positions in parallel during verification. The target model checks K draft tokens in one pass instead of K sequential passes.

**Acceptance rate (alpha) determines speedup:**
- alpha = 0.75-0.85 (code completion, structured data): 3-5x speedup
- alpha = 0.60-0.80 (general queries): 2-3x speedup
- alpha < 0.50 (creative, domain-specific): can *hurt* performance -- more cycles wasted on rejected tokens

**Production-ready variants (2025-2026):**

| Variant | Mechanism | Speedup | Notes |
|---|---|---|---|
| External draft model | Separate smaller model (e.g., Llama-3.1-8B drafting for 70B) | 2-3x | Adds 2-6 GB VRAM |
| EAGLE-3 | Learned prediction head on target model | 3.5-5x on code | ~4.8x on HumanEval for Llama 3.3 70B |
| P-EAGLE | Parallel drafting (all K tokens in one pass) | 4-5x | 20-30% over EAGLE-3; contributed to vLLM by AWS (early 2026) |
| Medusa | Multiple prediction heads on base model | 2-3x | No separate draft model needed |
| DFlash | Block diffusion speculative decoding | 6x claimed | Next-generation, emerging |

All three major engines (vLLM, TensorRT-LLM, SGLang) include native speculative decoding support. NVIDIA demonstrated 3.6x throughput improvements on H200 GPUs.

### 1.8 Quantization

Reducing model precision to shrink memory footprint and increase throughput. The 2026 production landscape:

| Format | Quality Regression (vs FP16, avg across 70B models) | VRAM Savings | Throughput Lift | Production Readiness |
|---|---|---|---|---|
| FP8 (E4M3) | ~0.4 pts MMLU-Pro | ~50% | 1.4-1.7x | **Default for H100+**; near-lossless |
| INT8 (SmoothQuant) | ~0.7 pts | ~50% | ~1.5x | Mature |
| AWQ INT4 | ~1.6 pts | ~75% | 2.6-3.1x | Best-practice INT4; activation-aware |
| GPTQ INT4 | ~1.5-2.4 pts | ~75% | 2.6-3.1x | Widely supported; larger variance |

**Decision ladder** (NVIDIA-recommended): FP8 first -> INT8 SmoothQuant -> AWQ/GPTQ INT4.

**The sub-4-bit cliff:** AWQ-4 loses ~1.6 pts on average; INT3 loses ~6 pts. Sub-4-bit is research-grade for most production workloads.

**Emerging:** Format hybridization (FP8 attention + INT4 MLPs), dynamic per-token precision adjustment.

---

## 2. Token Economics & NFR Metrics

### 2.1 Token Pricing Across Providers (as of Sep 2026)

Prices dropped ~80% between early 2025 and early 2026. Output tokens cost 5-6x input tokens -- verbose responses, not long prompts, dominate most bills.

**Flagship Tier ($/1M tokens):**

| Model | Input | Output | Notes |
|---|---|---|---|
| OpenAI GPT-5.6 Sol | $5.00 | $30.00 | |
| Anthropic Claude Opus 5 | $5.00 | $25.00 | Cheaper output than GPT-5.6 Sol |
| OpenAI o3 (reasoning) | $10-15 | $40-60 | Reasoning tokens add significant cost |

**Production Tier:**

| Model | Input | Output |
|---|---|---|
| Anthropic Claude Sonnet 5 | $2.00 | $10.00 |
| Anthropic Claude Sonnet 4.6 | $3.00 | $15.00 |
| OpenAI GPT-4o | $2.50 | $10.00 |
| Google Gemini 3.1 Pro Preview | $2.00 | $12.00 |

**Budget Tier:**

| Model | Input | Output |
|---|---|---|
| OpenAI GPT-5.6 Luna | $0.20 | $1.20 |
| OpenAI GPT-4.1 Nano | $0.10 | $0.40 |
| Google Gemini 2.0 Flash | $0.10 | $0.40 |
| Anthropic Claude Haiku 3.5 | $0.80 | $4.00 |

**Cost reduction levers:**
- **Prompt caching**: Cached tokens cost 10-25% of normal input price (both OpenAI and Anthropic)
- **Batch API**: 50% discount across OpenAI and Anthropic
- **Volume commitments**: 20-50% discounts common above $5-10K/month spend

### 2.2 Latency Metrics: TTFT vs TPS

| Metric | Definition | What Drives It | Typical Range (70B model, single GPU) |
|---|---|---|---|
| **TTFT** | Time from request to first output token | Prefill computation; scales with input length | 50-500ms (short prompt) to 2-5s (100K+ context) |
| **TPOT** | Time per output token (inverse of TPS) | Decode; memory bandwidth bound | 20-50ms/token (~20-50 TPS) |
| **TPS** | Tokens per second (throughput per request) | Decode speed | 20-80 tok/s per request |
| **E2E Latency** | Total response time | TTFT + (output_tokens x TPOT) | Varies widely |

**Tail latency matters more than averages.** Production SLOs target p95/p99:
- Conversational chat: p95 TTFT < 500ms, p95 TPS > 30
- Code completion: p99 TTFT < 200ms (user is typing)
- Batch processing: optimize throughput, latency SLO relaxed

### 2.3 Throughput Optimization

**Batching** is the primary throughput lever. Continuous batching + larger batch sizes = higher aggregate TPS, at the cost of higher per-request latency. Production systems tune batch size to hit latency SLO at maximum throughput.

**Disaggregated serving**: Separate prefill and decode onto different hardware. Prefill on compute-heavy GPUs (A100/H100); decode on bandwidth-optimized or cheaper hardware. Reduces interference between the two phases.

**Speculative decoding** provides 2-5x latency reduction without quality loss (see Section 1.7).

**Prompt caching** eliminates redundant prefill for shared prefixes. Impact: 5-10x TTFT reduction for cached portions.

---

## 3. Distributed Resilience & State

### 3.1 Model Sharding: Parallelism Strategies

**Decision framework:** Memory-limited -> Pipeline Parallelism. Compute/latency-limited -> Tensor Parallelism. Throughput-limited -> Data Parallelism.

| Strategy | How It Works | Communication Cost | Best For |
|---|---|---|---|
| **Tensor Parallelism (TP)** | Shards weight matrices within each layer across GPUs; requires all-reduce/all-gather after each layer | High (needs NVLink/InfiniBand) | Reducing latency; intra-node scaling |
| **Pipeline Parallelism (PP)** | Assigns contiguous layer groups to different GPUs; activations passed between stages | Low (one transfer per stage boundary) | Cross-node scaling; memory relief |
| **Expert Parallelism (EP)** | Each GPU holds full weights of a subset of MoE experts | Moderate (token routing between GPUs) | MoE models (DeepSeek-V3, Mixtral) |
| **Data Parallelism (DP)** | Full model replicas handle independent requests | Minimal (no inter-replica communication) | Scaling throughput linearly |

**Hybrid parallelism is standard in production:**
- Within a node (8 GPUs, NVLink): TP=8
- Across nodes (InfiniBand): PP=N_nodes
- Multiple replicas: DP for throughput scaling

Example: 2 nodes x 8 GPUs -> TP=8, PP=2 for a model that needs 16 GPUs.

**Super-linear scaling effect:** With TP or PP, available KV cache memory increases super-linearly. vLLM observed: TP=1 to TP=2 increased KV cache blocks by 13.9x, yielding 3.9x throughput gain (vs expected 2x linear). Mechanism: freeing weight memory enables disproportionately larger batch sizes.

**Meta's innovations:**
- Direct Data Access (DDA) algorithms for TP all-reduce: reduces communication overhead by up to 30% by allowing ranks to directly load memory from other ranks
- Wide Expert Parallelism: dynamic expert replication and placement based on workload patterns for MoE models

### 3.2 Inference Fault Tolerance and Failover

Production LLM serving must handle multiple failure classes:

| Failure Type | Response Pattern |
|---|---|
| Hard 5xx from provider | Immediate fallback to backup model/provider |
| 429 rate limit | Back off per Retry-After header, queue requests |
| Rising latency (no error) | Shift new requests to faster provider proactively |
| Partial streaming failure (mid-response drop) | Resume or retry the request, not hard fail |
| GPU OOM during inference | Reduce batch size, evict KV cache entries, or redirect |

**Self-hosted resilience patterns:**
- Health check endpoints monitoring VRAM utilization, queue depth, and response latency
- Graceful degradation: shed load by reducing max concurrent requests before OOM
- KV cache eviction policies: LRU, attention-score-based eviction, or offload to CPU/SSD
- Checkpoint-based recovery for long-running generation tasks

### 3.3 KV Cache Management at Scale

The KV cache is the dominant memory consumer for long-context serving and the primary scaling bottleneck.

**Three management paradigms:**
1. **Memory management frameworks** (e.g., PagedAttention): Optimize allocation/scheduling without modifying attention computation. Reduce fragmentation, retain complete cache on GPU.
2. **Static sparsification**: Permanently evict low-attention-score tokens. Aggressive memory reduction at potential accuracy cost. Works well for repetitive/structured content.
3. **Dynamic selection**: Retain full KV cache in CPU memory; selectively transfer entries to GPU per decode step. Preserves accuracy, adds CPU-GPU transfer overhead.

**LMCache** provides batched KV cache operations with compute/IO pipelining. Combined with vLLM: up to 15x throughput improvement on multi-round QA and document analysis workloads.

---

## 4. Enterprise Security & Governance

### 4.1 Prompt Injection Defenses

**The fundamental problem:** LLMs cannot architecturally distinguish instructions from data -- both arrive as natural language in a single stream. OWASP ranked prompt injection as LLM01 in its 2025 Top 10.

**Attack taxonomy (2025-2026):**

| Attack Class | Mechanism | Example |
|---|---|---|
| Direct injection | User crafts input to override system prompt | "Ignore previous instructions and..." |
| Indirect injection | Malicious instructions embedded in retrieved documents, emails, web pages | RAG poisoning: 5 crafted docs manipulate responses 90% of the time |
| Invisible injection | Hidden instructions in white-on-white text, invisible Unicode, hidden HTML | Document appears clean to human reviewer; model processes hidden text |
| MCP tool poisoning | Malicious tool descriptions/schemas in Model Context Protocol | Tool description contains hidden instructions that override agent behavior |
| Virtual prompt injection (VPI) | 52 poisoned examples in training data create undetectable backdoor | No runtime defense can detect; only training data curation helps |

**Real-world impact (2025-2026):**
- CVE-2025-53773: GitHub Copilot RCE, CVSS 9.6
- CVE-2026-24307 ("Reprompt"): Single-click data exfiltration from Microsoft 365 Copilot
- Prompt injection CVEs filed against Claude Code, Cursor IDE, AWS Kiro, Google Jules, Amazon Q Developer (August 2025)
- Anthropic dropped direct injection metrics entirely (Feb 2026), focusing on indirect injection as the primary enterprise threat

**Defense layers (no single defense is sufficient):**

| Layer | Approach | Effectiveness | Limitation |
|---|---|---|---|
| Input/output guardrail models | Separate classifier (Llama Guard, ShieldGemma, Prompt Guard, NVIDIA NeMo) screens inputs/outputs | 60-80% detection for known patterns | Guardrail model itself susceptible to injection |
| LLM-as-Critic output validation | Second LLM validates primary's output against policy | +21% detection precision over input-only filtering | Adds latency and cost |
| Fine-tuning for resistance | Train model to resist injection (SecAlign, ReasAlign) | ReasAlign: 3.6% attack success rate | Buckles under optimization-based attacks |
| Architectural (CaMeL) | Google DeepMind's provably secure architecture | 77% task completion vs 84% undefended | 7-point capability trade-off |
| Least-privilege tool scopes | LLMs only get minimum required permissions | Limits blast radius | Doesn't prevent the injection itself |
| Automated red-teaming | RL-trained adversarial testing (OpenAI approach) | Discovers novel strategies before real attackers | Ongoing effort required |

**The sobering reality:** The International AI Safety Report (2026) found sophisticated attackers bypass safeguards ~50% of the time with 10 attempts on best-defended models. Current defenses are speed bumps, not walls.

**Regulatory implications:** EU AI Act (full enforcement August 2026) and NIST AI 600-1 both require demonstrable adversarial robustness for high-risk systems.

### 4.2 Model Access Control & API Key Management

Production patterns:
- **API key rotation** with short-lived tokens; never embed keys in client-side code
- **Per-user/per-team rate limiting** at the gateway layer (token-aware, not just request-count)
- **Budget enforcement**: Hard spend caps per API key/team/project at the gateway
- **Audit logging**: Every prompt and completion logged (with PII redaction) for compliance. SOC 2 and GDPR increasingly require this for LLM-powered features
- **Model access tiers**: Different users/teams authorized for different model tiers (e.g., interns get Haiku, not Opus)

### 4.3 Data Privacy: Training Data Extraction Risks

**The threat:** LLMs memorize training data. Adversaries can extract it by generating large text volumes, then using Membership Inference Attacks (MIAs) to verify whether specific data was in the training set.

**Key findings (2025-2026):**
- MIAs achieve AUC ~0.9 against fine-tuned LLMs using self-prompted calibration (Fu et al., 2024)
- Healthcare LLMs are particularly vulnerable -- rare clinical sequences (rare diseases, unique patient trajectories) are most extractable
- Privacy auditing via "canaries" achieves non-trivial lower bounds: epsilon ~ 1 for models trained to epsilon = 4 differential privacy (ICLR 2025)
- Loss landscape poisoning (2026): adversaries can poison training data to enable targeted extraction of *unseen* data

**Defenses:**
- **Training data deduplication**: Removing duplicate copies greatly mitigates extraction risk -- the single most impactful measure
- **Differential privacy**: Adding calibrated noise during training; trade-off with model quality
- **Ensemble Privacy Defense (EPD)**: Aggregates outputs of knowledge-injected LLM, base LLM, and judge model to resist MIAs
- **Access controls**: Rate limiting generation length and temperature to reduce extraction surface

---

## 5. Production Failure Modes

### 5.1 Hallucination Patterns and Detection

**Base hallucination rates:** 3-27% on factual tasks even with frontier models (2025-2026 studies).

**Six concrete failure modes (2026 taxonomy):**

| Mode | Description | Detection Approach |
|---|---|---|
| **Entity fabrication** | Invents people, places, papers, URLs, ArXiv IDs | Cross-reference against knowledge base; URL validation |
| **Fact misattribution** | Assigns real facts to wrong entities | Entity-aware NLI checking |
| **Unfaithful summarization** | Summary contradicts or extrapolates beyond source document | NLI-based faithfulness scoring against retrieved chunks |
| **Self-contradiction** | Response contradicts itself across paragraphs | Intra-response consistency checking |
| **Off-topic drift** | Loses thread of the question, answers something adjacent | Relevance scoring against original query |
| **Confident false refusal** | Claims not to know something that is in-scope and retrievable | Coverage analysis against knowledge base |

**Additional failure modes:**
- **Ceremonialization (instruction attenuation)**: Model says "verified" but didn't actually verify; says "tests passed" but didn't run them. The shell of the instruction remains; its substance is empty. Particularly dangerous in agentic workflows.
- **Sycophancy**: Agrees with user's stated position even when factually wrong. Amplified by RLHF optimizing for user approval.
- **Context rot**: Quality degrades as context window fills, especially for instructions in the middle of long contexts ("lost in the middle" effect).
- **Reasoning hallucination**: Longer chain-of-thought traces create more points where logical chains can break. Reasoning models make this *more* visible, not less.
- **Tool argument spoofing**: Model invents arguments, IDs, or parameters when calling tools, causing silent corruption in agent loops.

**Production detection cascade (recommended):**
1. Cheap heuristic check (regex, URL validation, format compliance)
2. Classifier-backed check (NLI model, embedding similarity)
3. LLM-as-judge fallback on borderline cases only

**Semantic entropy** (published in Nature): Generate multiple responses, cluster semantically, measure entropy. High entropy = high hallucination probability. Useful for uncertainty quantification.

**Production monitoring loop:** Generate -> Trace -> Evaluate -> Cluster failure modes -> Optimize prompts/retrieval -> Route. Per-mode reporting replaces single "hallucination score." Teams report: fabrication rate, faithfulness score, format compliance rate, refusal rate distribution, tool success rate.

### 5.2 Context Window Overflow & Tokenizer Edge Cases

**Context window limits (as of 2026):**
- Gemini 1.5/2.0 Pro: 1M-2M tokens
- Claude Opus 4/Sonnet 4.6: 200K tokens
- GPT-4o: 128K tokens
- Llama 3.1 405B: 128K tokens

**Overflow strategies:**
- Truncation (oldest messages first) -- simplest, loses early context
- Summarization of earlier conversation turns
- RAG: move long-term memory to vector store, retrieve relevant chunks
- Sliding window with summary checkpoints

**"Lost in the middle" effect:** Models recall information at the beginning and end of context windows far better than the middle. Empirically validated across GPT-4, Claude, and Gemini. Implications: place critical instructions at the start or end of prompts, not in the middle of long retrieved contexts.

**Tokenizer edge cases causing production failures:**
- Trailing whitespace alters tokenization, changing model behavior unpredictably
- Number splitting breaks arithmetic: "123456" -> ["123", "456"]
- Unicode homoglyphs bypass content filters while appearing identical to humans
- Right-to-left Unicode markers can reverse displayed text while leaving model processing unchanged
- Emoji and CJK character tokenization is highly variable across tokenizers

### 5.3 Degraded Quality Under Load

**Mechanisms:**
- Under high concurrency, continuous batching increases per-request latency (TPOT rises)
- KV cache pressure forces eviction, degrading quality on long-context requests
- Chunked prefill under load increases TTFT variance
- Provider-side throttling (429s) causes retries and cascading latency
- Quantization applied dynamically under memory pressure can reduce quality

**Mitigation:**
- Autoscaling based on queue depth and TPOT, not just request count
- Admission control: reject or queue low-priority requests before quality degrades
- Separate pools for latency-sensitive vs. batch workloads
- Monitor quality metrics (not just throughput/latency) under load

---

## 6. Enterprise System Design Scenarios

### 6.1 Multi-Model Routing Architectures

**Why route:** A 2025 a16z survey found 37% of enterprise CIOs run 5+ models in production. Routing every request to a single frontier model overspends 40-80% on straightforward queries.

**Cost impact:** ICLR 2025 work cut cost 85% on MT Bench while retaining 95% of GPT-4 Turbo quality, sending only 14% of queries to the strong model. Routing classification from Opus to Sonnet saves 40%; to Haiku saves 80%.

**Three-tier routing progression:**

| Tier | Mechanism | When to Use |
|---|---|---|
| **Rule-based** | Deterministic conditions: user tier, request type, regex patterns, fixed weights | Starting point for every team; low overhead |
| **Semantic** | Embedding/classifier evaluates prompt complexity, routes to appropriate model tier | When meaning matters more than keywords |
| **Predictive/Learned** | Trained classifier predicts if smaller model can match frontier quality (RouteLLM, ICLR 2025) | Only when you have data + stable distribution to justify engineering cost |

**The Cascade Pattern (most commonly shipped):**
Answer with small model first; escalate to frontier only if confidence/verification check fails. Spends frontier tokens only on requests that provably need them. Can outperform a single frontier model on both cost *and* quality.

### 6.2 LLM Gateway Design Patterns

An LLM gateway sits between application code and model providers -- analogous to API gateways (Kong, Envoy) but purpose-built for LLM traffic patterns: streaming responses, per-token billing, provider-specific errors (Anthropic 529 overloaded), 30+ second request durations.

**Core capabilities:**

| Capability | Function |
|---|---|
| Unified API | Single OpenAI-compatible interface to 1000+ models across providers |
| Automatic failover | Retry on backup provider after 5xx; separate handling for 429s, latency degradation, mid-stream drops |
| Token-aware rate limiting | Limit by tokens consumed, not just request count |
| Budget enforcement | Hard spend caps per API key/team/project |
| Semantic caching | Match incoming prompts against cached responses via embedding similarity; up to 73% cost reduction (Redis LangCache) |
| Observability | Per-request logging: model, tokens, latency, cost, prompt hash |
| Compliance routing | Route requests to specific providers/regions based on data residency requirements |

**Leading gateways (2026):**

| Gateway | Differentiator | Overhead |
|---|---|---|
| Bifrost (Maxim AI) | CEL expression routing, model aliasing | 11 microseconds |
| LiteLLM | Self-hosted default; cost tracking, region-aware | 10-25ms (Python) |
| Portkey | Open-source (Apache-2.0); full governance suite | Low |
| Azure AI Foundry Model Router | Trained LLM analyzes prompts, routes across 27+ models; three modes (Balanced/Cost/Quality) | Higher (LLM classification pass) |

**Performance overhead reality:** Compiled native gateways: ~11 microseconds. Python proxies: 10-25ms. Routers with LLM classification: 50-200ms before upstream generation begins. For latency-sensitive workloads, the gateway itself must be fast.

### 6.3 Trade-off Matrices

**Model Selection: Cost vs. Quality vs. Latency**

| Scenario | Recommended Model Tier | Rationale |
|---|---|---|
| Customer-facing chat | Mid-tier (Sonnet 5, GPT-4o) | Balance of quality and cost; <500ms TTFT SLO |
| Document classification | Budget (Haiku 3.5, Gemini Flash) | Simple task; 80% cost savings vs. frontier |
| Complex reasoning/coding | Flagship (Opus 5, o3) | Quality-critical; cost justified by task value |
| Internal summarization | Budget with cascade to mid-tier | Most summaries are straightforward; escalate edge cases |
| Agentic workflows | Mid-tier with tool-use optimization | Multiple LLM calls per task; cost compounds fast |

**Self-hosted vs. API: Decision Matrix**

| Factor | API | Self-hosted |
|---|---|---|
| Time to production | Hours | Weeks-months |
| Cost at low volume | Lower (pay-per-token) | Higher (GPU idle time) |
| Cost at high volume | Higher (markup on compute) | Lower (amortized GPU cost) |
| Data privacy | Data leaves your network | Data stays on-premises |
| Model customization | Limited (fine-tuning APIs) | Full control |
| Operational burden | Zero | Significant (GPU ops, model updates, monitoring) |
| Latency control | Limited by provider | Full control |

**Quantization Decision Matrix**

| Constraint | Recommended Format | Trade-off |
|---|---|---|
| Quality-critical, H100+ available | FP8 | ~0.4 pt regression; 1.5x throughput |
| Must fit 70B on single GPU | AWQ INT4 | ~1.6 pt regression; 75% VRAM savings |
| Edge deployment / mobile | GGUF INT4 (llama.cpp) | Higher regression; runs on CPU |
| Long context (128K+) | FP8 weights + INT8 KV cache | Maximizes concurrent sequences |

---

## 7. Training Pipeline & Alignment (Supplementary)

### 7.1 Scaling Laws

**Chinchilla (DeepMind, 2022):** Compute-optimal training requires ~20 tokens per parameter. Prior practice (Kaplan/OpenAI, 2020) favored scaling model size over data. Chinchilla 70B outperformed Gopher 280B at the same compute budget.

**The Chinchilla Trap (2023-2026):** Compute-optimal is a training-FLOP minimization, not a deployment recipe. Inference dominates lifetime cost. Modern practice: train smaller models on far more data ("overtraining").

| Model | Params | Training Tokens | Tokens/Param | vs. Chinchilla 20:1 |
|---|---|---|---|---|
| GPT-3 | 175B | 300B | 1.7 | 12x undertrained |
| Llama-2 70B | 70B | ~2T | ~28 | ~Chinchilla-optimal |
| Llama-3 8B | 8B | 15T | 1,875 | 94x overtrained |
| Qwen3-0.6B | 0.6B | 36T | 60,000 | 3,000x overtrained |
| Liquid LFM2.5-350M | 350M | 28T | 80,000 | 4,000x overtrained (record) |

**Takeaway:** The right model is smaller and trained longer than Chinchilla suggests. "Overtrain" for inference economics.

### 7.2 Pre-training -> Instruction Tuning -> RLHF Pipeline

1. **Pre-training**: Next-token prediction on internet-scale data (trillions of tokens). Produces base model that predicts text but isn't a useful assistant.
2. **Instruction fine-tuning (SFT)**: Train on curated instruction-response pairs. Model learns to follow instructions, give structured answers.
3. **RLHF/RLAIF alignment**:
   - Humans (or AI judges) rank multiple model responses
   - Rankings train a reward model predicting human preferences
   - Policy optimization nudges the LLM toward higher-scoring responses
   - Teaches helpfulness, safety, clarity without explicitly coding these behaviors

**Reasoning models** (o3, Claude Opus 4, Gemini 2.5 Pro): Have chain-of-thought reasoning built in via training, not prompting. Trade-off: longer responses, higher cost, higher latency vs. better accuracy on complex tasks.

### 7.3 Fine-Tuning for Specialization

Fine-tuning adapts a pre-trained model on a smaller, domain-specific dataset. Examples: GitHub Copilot (code), Bloomberg GPT (finance).

**Parameter-efficient methods (PEFT):**
- **LoRA**: Adds small rank-decomposition matrices to attention layers; trains <1% of parameters. Standard for domain adaptation.
- **QLoRA**: LoRA on quantized (4-bit) base model; enables fine-tuning 70B models on a single GPU.

### 7.4 RAG (Retrieval-Augmented Generation)

Three-step architecture: Retrieve (search documents) -> Augment (inject into prompt) -> Generate (LLM answers from evidence).

**Why RAG over fine-tuning:**
- No retraining needed; update knowledge by updating document store
- Source attribution possible (cite retrieved chunks)
- Cheaper and faster iteration
- Knowledge is verifiable and auditable

**RAG introduces new failure surfaces:** Model can ignore, misread, or extrapolate beyond retrieved chunks. Faithfulness evaluation is critical.

---

## 8. Evaluation & Benchmarks (Supplementary)

**Key benchmarks:**

| Benchmark | Measures | Notes |
|---|---|---|
| MMLU / MMLU-Pro | General knowledge across 57 subjects | Standard but increasingly saturated |
| HumanEval / HumanEval+ | Code generation (Python) | Pass@1 metric; EAGLE-3 reports 4.8x speedup here |
| GSM8K | Grade-school math reasoning | Largely solved by frontier models |
| BBH (BIG-Bench Hard) | Logical reasoning | More discriminative than GSM8K |
| Chatbot Arena (LMSYS) | Crowdsourced head-to-head human preference | Most trusted for overall quality ranking |
| SWE-bench | Real-world software engineering tasks | Key for coding agent evaluation |

**LLM-as-Judge:** Using a powerful LLM (GPT-5, Claude Opus) to evaluate another model's output at scale. Receives: original prompt, candidate response, evaluation rubric. Returns: score + explanation. Standard practice for automated evaluation.

**RAG-specific metrics:**
- **Faithfulness**: Does the answer stick to retrieved documents? (hallucination control)
- **Answer relevance**: Does it address the question? (retrieval quality)
- Tools: RAGAS, DeepEval, custom NLI pipelines

---

## Sources

### Primary Source
- [LLM Concepts - System Design Newsletter](https://newsletter.systemdesign.one/p/llm-concepts)

### Architecture & Inference
- [D2L.ai - Transformer Architecture](https://d2l.ai/chapter_attention-mechanisms-and-transformers/transformer.html)
- [NVIDIA - Mastering LLM Inference Optimization](https://developer.nvidia.com/blog/mastering-llm-techniques-inference-optimization/)
- [NVIDIA - NVFP4 KV Cache Optimization](https://developer.nvidia.com/blog/optimizing-inference-for-long-context-and-large-batch-sizes-with-nvfp4-kv-cache/)
- [GPU Inference Performance: Prefill, Decode & Batching - IntuitionLabs](https://intuitionlabs.ai/articles/gpu-inference-performance-prefill-decode-batching)
- [Benchmarking KV Cache, Continuous Batching, and Chunked Prefill](https://czhou578.github.io/blog/2026/05/29/benchmarks-one.html)
- [LMCache Technical Report](https://lmcache.ai/tech_report.pdf)

### Model Serving
- [vLLM vs TensorRT-LLM vs TGI vs LMDeploy - MarkTechPost](https://www.marktechpost.com/2025/11/19/vllm-vs-tensorrt-llm-vs-hf-tgi-vs-lmdeploy-a-deep-technical-comparison-for-production-llm-inference/)
- [vLLM vs TensorRT-LLM vs SGLang H100 Benchmarks - Spheron](https://www.spheron.network/blog/vllm-vs-tensorrt-llm-vs-sglang-benchmarks/)
- [Comparative Analysis of LLM Inference Serving (arXiv)](https://arxiv.org/html/2511.17593v1)

### Speculative Decoding
- [NVIDIA - Speculative Decoding for Faster Inference](https://developer.nvidia.com/blog/an-introduction-to-speculative-decoding-for-reducing-latency-in-ai-inference/)
- [BentoML - 3x Faster Inference with Speculative Decoding](https://www.bentoml.com/blog/3x-faster-llm-inference-with-speculative-decoding)
- [Speculative Decoding Production Guide - Spheron](https://www.spheron.network/blog/speculative-decoding-production-guide/)

### Parallelism & Distribution
- [Meta Engineering - Scaling LLM Inference: TP, CP, EP](https://engineering.fb.com/2025/10/17/ai-research/scaling-llm-inference-innovations-tensor-parallelism-context-parallelism-expert-parallelism/)
- [BentoML - Data, Tensor, Pipeline, Expert Parallelism](https://bentoml.com/llm/inference-optimization/data-tensor-pipeline-expert-hybrid-parallelism)
- [vLLM - Parallelism and Scaling](https://docs.vllm.ai/en/stable/serving/parallelism_scaling/)
- [TensorRT-LLM - Parallel Strategy](https://nvidia.github.io/TensorRT-LLM/features/parallel-strategy.html)

### Pricing
- [OpenAI vs Anthropic API Pricing Comparison - Finout](https://www.finout.io/blog/openai-vs-anthropic-api-pricing-comparison)
- [LLM API Pricing 2026 - IntuitionLabs](https://intuitionlabs.ai/articles/llm-api-pricing-comparison-2025)
- [LLM API Cost Comparison - ML Journey](https://mljourney.com/llm-api-cost-comparison-2026-openai-vs-anthropic-vs-google-vs-open-source/)

### Security
- [OWASP - LLM Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [Sysdig - Comprehensive Guide to Prompt Injection Attacks 2026](https://www.sysdig.com/learn-cloud-native/prompt-injection)
- [Prompt Injection Attacks: Comprehensive Review (MDPI)](https://www.mdpi.com/2078-2489/17/1/54)
- [FutureAGI - LLM Prompt Injection 2026](https://futureagi.com/blog/llm-prompt-injection-2025/)

### Hallucination & Failure Modes
- [FutureAGI - LLM Hallucination Deep Dive 2026](https://futureagi.com/blog/llm-hallucination-deep-dive-2026/)
- [Composo - An Ontology of LLM Failure Modes](https://www.composo.ai/post/llm-failure-modes/)
- [LLM Foundational Failure Modes](https://ceaksan.com/en/llm-foundational-failure-modes)
- [Maxim AI - LLM Hallucination Detection and Mitigation](https://www.getmaxim.ai/articles/llm-hallucination-detection-and-mitigation-best-techniques/)

### Routing & Gateways
- [LLM Gateway Architecture - Collin Wilkins](https://collinwilkins.com/articles/llm-gateway-architecture)
- [Redis - LLM Router Architecture Best Practices 2026](https://redis.io/blog/llm-router-architecture-best-practices/)
- [Multi-Model Routing - AI Gateway Pattern](https://akshayghalme.com/blogs/multi-model-routing-ai-gateway-pattern/)
- [Maxim AI - Best AI Gateway for Multi-Model Routing](https://www.getmaxim.ai/articles/best-ai-gateway-for-multi-model-routing-in-2026/)

### Scaling Laws
- [Chinchilla Scaling Laws (arXiv:2203.15556)](https://arxiv.org/abs/2203.15556)
- [Beyond Chinchilla-Optimal: Accounting for Inference](https://arxiv.org/html/2401.00448v3)
- [A Brief History of LLM Scaling Laws - Jon Vet](https://www.jonvet.com/blog/llm-scaling-in-2025)

### Quantization
- [NVIDIA TRT-LLM - SOTA Quantization Techniques](https://nvidia.github.io/TensorRT-LLM/blogs/quantization-in-TRT-LLM.html)
- [Quantization Tradeoffs: 4-bit vs 8-bit vs FP8](https://www.digitalapplied.com/blog/quantization-tradeoffs-4bit-8bit-fp8-performance-data)
- [LLM Quantization for Production Inference 2026 - AppScale](https://appscale.blog/en/blog/llm-quantization-production-inference-int8-fp8-awq-gguf-2026)

### Privacy
- [Membership Inference in Data Extraction from LLMs (arXiv:2512.13352)](https://arxiv.org/abs/2512.13352)
- [Privacy Attacks of LLMs for Healthcare - Frontiers in AI](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2026.1816692/full)
- [Training Data Extraction Attacks - Antispoofing Wiki](https://antispoofing.org/training-data-extraction-attacks-and-countermeasures/)
