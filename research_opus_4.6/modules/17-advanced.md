# Module 17: Advanced — Frontier Topics in AI Architecture

**Scope**: Reasoning models (o3, DeepSeek-R1, Claude extended thinking), long-context architectures (1M+ tokens, context utilization), multimodal architectures (VLMs, audio, computer use), model merging and composition (MoE, task arithmetic, SLERP/TIES/DARE), synthetic data generation (distillation, model collapse), RAG advances (contextual retrieval, late chunking, GraphRAG), agent frameworks evolution (LangGraph, CrewAI, Claude Agent SDK, MCP), and frontier model capabilities and limitations (benchmark saturation, emergent abilities, jagged frontier).
**Prerequisite**: Modules 1–16.
**Last updated**: 2026-08-21 | **Sources consulted**: 80

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Reasoning       │  │  Context         │  │  Model           │  │  Eval            │  │
 │  │  Controller      │  │  Manager         │  │  Composer        │  │  Orchestrator    │  │
 │  │  - Effort level  │  │  - Window size   │  │  - MoE routing   │  │  - Benchmark     │  │
 │  │    (low→xhigh)  │  │  - Hybrid RAG vs │  │  - Merge config  │  │    selection     │  │
 │  │  - Budget tokens │  │    long-context  │  │  - Expert select │  │  - Saturation    │  │
 │  │  - Adaptive vs   │  │  - Prefix cache  │  │  - Distillation  │  │    detection     │  │
 │  │    manual think  │  │  - Context rot   │  │    pipeline      │  │  - Multi-axis    │  │
 │  │                  │  │    mitigation    │  │                  │  │    scoring       │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                          DATA PLANE: FRONTIER AI PIPELINE                          │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  REASONING LAYER                                                        │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │      │    │
 │  │  │  │ Chain-of-    │  │ Test-Time    │  │ Reasoning    │                  │      │    │
 │  │  │  │ Thought      │  │ Compute      │  │ Verification │                  │      │    │
 │  │  │  │ - Internal   │  │ Scaling      │  │ - Self-check │                  │      │    │
 │  │  │  │   (o3) or    │  │ - 1B can beat│  │ - Backtrack  │                  │      │    │
 │  │  │  │   visible    │  │   405B with  │  │ - Decompose  │                  │      │    │
 │  │  │  │   (R1)       │  │   right      │  │ - Reflect    │                  │      │    │
 │  │  │  │ - Adaptive   │  │   strategy   │  │              │                  │      │    │
 │  │  │  │   (Claude)   │  │ - 256× more  │  │              │                  │      │    │
 │  │  │  │              │  │   efficient  │  │              │                  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  MULTIMODAL + LONG-CONTEXT LAYER                                        │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Vision       │  │ Audio        │  │ Long-Context │  │ Computer   │  │      │    │
 │  │  │  │ Encoder      │  │ Encoder      │  │ Engine       │  │ Use Agent  │  │      │    │
 │  │  │  │ - ViT/SigLIP │  │ - Whisper /  │  │ - 1M-2M      │  │ - Screen   │  │      │    │
 │  │  │  │ - Dynamic    │  │   native     │  │ - FlashAttn-3│  │   grounding│  │      │    │
 │  │  │  │   resolution │  │ - Cross-modal│  │ - RoPE ext.  │  │ - 78% OS-  │  │      │    │
 │  │  │  │ - Early      │  │   alignment  │  │ - Ring Attn  │  │   World    │  │      │    │
 │  │  │  │   fusion     │  │              │  │ - Hybrid RAG │  │   Verified │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  RETRIEVAL + COMPOSITION LAYER                                          │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Advanced RAG │  │ GraphRAG     │  │ MoE Router   │  │ Synthetic  │  │      │    │
 │  │  │  │ - Contextual │  │ - Knowledge  │  │ - Top-k      │  │ Data       │  │      │    │
 │  │  │  │   retrieval  │  │   graph      │  │   routing    │  │ Generator  │  │      │    │
 │  │  │  │ - Late chunk │  │ - Community  │  │ - Fine-grain │  │ - Distill  │  │      │    │
 │  │  │  │ - Hybrid     │  │   summaries  │  │ - 256 experts│  │ - Self-    │  │      │    │
 │  │  │  │   BM25+dense │  │ - LazyGraph  │  │ - Aux-loss-  │  │   instruct │  │      │    │
 │  │  │  │ - Reranking  │  │   RAG (0.1%) │  │   free       │  │ - Collapse │  │      │    │
 │  │  │  │ - 67% ↓ fail │  │              │  │   balancing  │  │   control  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Agent Frame-  │  │ MCP Gateway   │  │ MergeKit      │  │ Eval Bench    │       │    │
 │  │  │ work Runtime  │  │ (97M monthly  │  │ Pipeline      │  │ Runner        │       │    │
 │  │  │ - LangGraph   │  │  downloads)   │  │ - SLERP/TIES/ │  │ - HLE, SWE-   │       │    │
 │  │  │ - CrewAI      │  │ - JSON-RPC 2.0│  │   DARE merge  │  │   bench Pro,  │       │    │
 │  │  │ - Claude Agent│  │ - OAuth 2.1   │  │ - Task vectors│  │   ARC-AGI 2   │       │    │
 │  │  │   SDK         │  │ - Streamable  │  │ - Model soups │  │ - LiveBench   │       │    │
 │  │  │ - Pydantic AI │  │   HTTP        │  │              │  │ - Arena Elo   │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Knowledge Graph   │  │ Vector Index      │  │ Model Registry    │  │ Eval Results   │  │
 │  │ (Neo4j/NetworkX)  │  │ (pgvector/Qdrant) │  │ - Base weights    │  │ - Benchmark    │  │
 │  │ - Entity triples  │  │ - Contextual      │  │ - Merged weights  │  │   scores       │  │
 │  │ - Community       │  │   embeddings      │  │ - Expert weights  │  │ - Arena Elo    │  │
 │  │   summaries       │  │ - BM25 index      │  │ - Adapter weights │  │ - Saturation   │  │
 │  │ - Leiden clusters │  │ - Embedding vers.  │  │ - Draft models   │  │   tracking     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Reasoning         │  │ Context           │  │ RAG Quality       │  │ Frontier       │  │
 │  │ - Think tokens    │  │ - Effective vs    │  │ - Retrieval       │  │ Tracking       │  │
 │  │ - Acceptance rate │  │   advertised      │  │   precision       │  │ - Benchmark    │  │
 │  │ - Self-verify     │  │ - Context rot     │  │ - Faithfulness    │  │   shelf life   │  │
 │  │   success         │  │   magnitude       │  │ - GraphRAG        │  │ - Capability   │  │
 │  │ - Cost/reasoning  │  │ - Cache hit rate  │  │   coverage        │  │   coupling     │  │
 │  │   task            │  │                   │  │                   │  │ - Jagged gaps  │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Reasoning Decision**: The **Reasoning Controller** decides reasoning depth. For complex math/coding: engage extended thinking (Claude adaptive thinking, o3 reasoning, or R1 visible CoT). For simple queries: standard generation. Test-time compute scaling means a 1B model with optimal strategy can outperform a 405B model — reasoning budget allocation matters more than raw model size.

**Step 2 — Context Assembly**: The **Context Manager** decides between long-context window (1M+ tokens) and hybrid RAG (retrieve 50K–200K relevant tokens, then reason). Pure long-context rots beyond 200K for most models; pure RAG misses single-document reasoning. The 2026 default is hybrid.

**Step 3 — Advanced Retrieval**: For knowledge-intensive queries, **Contextual Retrieval** prepends document-level context before embedding (67% failure reduction). **GraphRAG** handles global/thematic questions via community summaries. **Late Chunking** preserves cross-chunk dependencies. Hybrid BM25+dense with reranking is the production standard.

**Step 4 — Multimodal Processing**: Vision (ViT/SigLIP), audio, and text enter a single early-fused token stream (Era 3a architecture). Computer use agents achieve 78% on OSWorld-Verified. Dynamic resolution ViT handles images at native resolution.

**Step 5 — Model Composition**: **MoE routing** activates only k experts per token (e.g., DeepSeek V4: 49B active out of 1.6T total — 96.9% sparsity). **Model merging** (SLERP, TIES, DARE) composes capabilities without retraining. **Synthetic data** pipelines distill frontier models into compact students (5–30× cost reduction).

**Step 6 — Frontier Evaluation**: The **Eval Orchestrator** selects benchmarks based on task type, avoiding saturated ones (MMLU >92%, HellaSwag >95%). The 2026 eval stack: GPQA Diamond, SWE-bench Pro, AIME 2025, ARC-AGI 2, HLE, BFCL v4, Arena Elo. When top models are within noise on benchmarks, differentiation shifts to cost, reliability, and domain fit.

---

## 2. Core Mechanics & Algorithms

### 2.1 Reasoning Models

| Model | Architecture | AIME 2024 | SWE-bench Verified | GPQA Diamond | Cost (in/out $/MTok) | CoT Visibility |
|-------|-------------|:---------:|:------------------:|:------------:|:--------------------:|:--------------:|
| **o3** | Hidden CoT, RL-trained | 91.6% | 69.1% | 87.7% | $2/$8 (post GPT-5) | Hidden |
| **o4-mini** | Hidden CoT, budget model | 93.4% | 68.1% | — | $1.10/$4.40 | Hidden |
| **DeepSeek-R1** | 671B MoE (37B active), GRPO RL | 79.8% | 49% | 71.5% | $0.55/$2.19 | Visible (`<think>` tags) |
| **Claude Opus 4.7** | Adaptive thinking, auto depth | — | 87.6% (SWE-bench Pro: 64.3%) | 94.2% | $5/$25 | Visible (thinking blocks) |
| **Gemini Deep Think** | Native multimodal + reasoning | — | — | — | Varies | Partial |

**Key architectural differences**:
- **GRPO** (DeepSeek): Avoids training a separate 671B value model by normalizing rewards across group of completions. Memory savings critical at scale.
- **Adaptive thinking** (Claude 4.6+): Model dynamically decides reasoning depth per request. Manual `budget_tokens` deprecated for Opus 4.7+.
- **R1-Zero experiment**: Pure RL without any supervised fine-tuning spontaneously produced self-verification, backtracking, and problem decomposition — emergent reasoning behaviors.

**Test-time compute scaling**: With optimal strategy, a 1B model outperforms 405B on MATH-500; a 7B model exceeds both o1 and R1 on AIME 2024. Compute-optimal strategies achieve 256× better efficiency than majority voting.

### 2.2 Long-Context Architectures

| Model | Advertised Context | Effective Context (Multi-Needle) | NIAH-2 Single-Needle (1M) |
|-------|:-----------------:|:-------------------------------:|:------------------------:|
| **Gemini 3.5 Pro** | 2M | ~1.5M (best) | 99% (Deep Think) |
| **Claude Opus 4.7** | 1M | ~200–400K (complex tasks) | 89% |
| **GPT-5.5** | 1M | ~200–400K (complex tasks) | 96% |
| **DeepSeek V4 Pro** | 1M | ~200K | 78% |
| **Llama 4 Scout** | 10M (claimed) | Not independently verified | — |

**The context reliability problem**: NVIDIA's RULER benchmark puts usable context at 50–65% of advertised. Adobe's NoLiMa found most models drop below half their short-context score by 32K tokens. Marketing claims hide a 30–60 point retrieval drop between 200K and 1M for every frontier model except Gemini Deep Think.

**Root causes**: U-shaped attention curve (tokens in the middle get deprioritized). RoPE decay (attention diminishes proportional to distance squared). Context rot confirmed across 18 frontier models.

**Extension techniques**: Position Interpolation, NTK-Aware Scaling, YaRN (attention temperature), LongRoPE (extrapolation beyond training). FlashAttention-3 at 1.3 PFLOPs/s on H100. Ring Attention for distributed context across devices. TTT-E2E delivers 35× speedup for 2M context.

**2026 production default**: Hybrid — retrieve 50K–200K relevant tokens, then long-context-reason over them. The context-window arms race is over; the context-reliability race is the real story.

### 2.3 Multimodal Architectures

**Four architectural eras**:

| Era | Period | Architecture | Examples |
|-----|--------|-------------|---------|
| 1. Frozen towers | 2021–2022 | Separate frozen encoders + contrastive alignment | CLIP, ALIGN |
| 2. Bridge/connector | 2022–2024 | Frozen vision + learnable bridge (Q-Former) into frozen LLM | BLIP-2, Flamingo |
| 3. LLM-as-trunk | 2023–2025 | Pretrained LLM trunk + vision adapter | LLaVA, GPT-4V, Qwen2.5-VL |
| 3a. Native early fusion | 2025–2026 | Single early-fused token stream across modalities | Gemini 3, GPT-5.4, Claude Opus 4.6 |

**Computer use agents (OSWorld-Verified 2026)**: Claude Opus 4.7: 78.0%, GPT-5.5: 78.7%, Gemini 3.5 Flash: 78.4%. The three-way spread is 0.7 points — less than measurement error. But OSWorld 2.0 (long multi-step, avg 318 tool calls): even Opus 4.8 completes only 20.6%.

**Audio-Visual LLMs**: Era 3a models (Gemini 3, GPT-5.4) process audio natively — joint encoding aligns auditory, visual, and linguistic modalities in a single token stream. This enables cross-modal reasoning (e.g., matching spoken descriptions to on-screen content). Earlier approaches used Whisper as a separate encoder with bridge modules; native fusion eliminates the pipeline latency and error propagation of cascaded transcription.

**Open-source VLMs closing the gap**: Qwen2.5-VL-72B within 5–10% of proprietary models on major benchmarks. InternVL3-78B near parity.

### 2.4 Model Merging and Composition

**Merging methods** (no additional training required):

| Method | Mechanism | Best For |
|--------|-----------|---------|
| **SLERP** | Spherical interpolation along unit hypersphere | Blending exactly 2 models |
| **TIES** | Trim top-k% significant changes + elect dominant sign + merge | Reducing interference between 3+ models |
| **DARE** | Randomly zero delta parameters at rate p, rescale by 1/(1−p) | Reducing redundancy |
| **Task Arithmetic** | τ = θ_finetuned − θ_base; add/subtract/scale task vectors | Composing/removing capabilities |
| **Model Soups** | Aggregate multiple fine-tuned checkpoints from different HP runs | Combining hyperparameter sweeps |

**MergeKit** (Apache 2.0, Arcee AI): De facto toolkit. Memory-efficient streaming (merges run on machines with less RAM than model size).

**MoE architecture — the 2026 default**:

| Model | Total Params | Active Params | Sparsity | Routing |
|-------|:----------:|:------------:|:--------:|---------|
| **Mixtral 8x22B** | 141B | 39B | 72% | Top-2, 8 experts |
| **DeepSeek-V3** | 671B | 37B | 94.5% | Top-8, 256 fine-grained experts |
| **DeepSeek-V4 Pro** | 1.6T | 49B | 96.9% | Fine-grained + shared experts |
| **DeepSeek-V4 Flash** | 284B | 13B | 95.4% | Fine-grained |
| **Llama 4 Maverick** | — | — | — | 1 shared + 1 routed, 128 pool |

**Activation ratio trend**: From 28% active (Mixtral 8x22B, 2024) to 3.1% active (DeepSeek V4 Pro, 2026) — equivalently, sparsity rose from 72% to 96.9%. DeepSeek's auxiliary-loss-free balancing applies dynamic bias terms adjusted in real time — trained at ~1/10 the cost of a comparable dense model.

**Critical caveat — merging composes; it does not teach**: Merging combines existing capabilities from source models but cannot introduce skills absent from all sources. Safety evaluations are mandatory post-merge: a model safe on its own may exhibit unsafe behavior after merging due to interference patterns. The Linear Mode Connectivity (LMC) hypothesis — that fine-tuned models lie in the same loss basin — becomes brittle for MoE architectures. Replacing the original expert router with a merged router degrades performance; restoring the source router recovers capability. This means MoE merging requires router-preserving strategies, not naive weight averaging.

### 2.5 Synthetic Data Generation

**Three production techniques**:

| Technique | Mechanism | Example |
|-----------|-----------|---------|
| **Self-Instruct / Distillation** | Teacher LLM generates training data for student | DeepSeek R1-Distill 32B: performs on par with OpenAI o1-mini on math/code benchmarks |
| **Self-Bootstrapping** | Models generate data that trains/improves other models | Automated program repair: 30K paired examples across 12 languages |
| **Classical generative** | GANs, VAEs, diffusion models for structured/tabular data | NVIDIA Omniverse (252 enterprise deployments) |

**Model collapse risk**: 74.2% of new web pages contain AI-generated content (April 2025). ICML 2025 finding: collapse is contained as long as models train on a mixture of real + synthetic data. Verified synthetic data (filtered by quality verifier) can improve retraining through bias-variance trade-off.

**Quality control pipeline**: Generate → filter (faithfulness, diversity, bias) → measure distribution drift against real anchor → run regression simulation.

### 2.6 RAG Advances

| Technique | Mechanism | Impact |
|-----------|-----------|--------|
| **Contextual Retrieval** | Prepend document-level context before embedding | 67% total retrieval failure reduction (with reranking) |
| **Late Chunking** | Embed full document first, then segment | Preserves cross-chunk dependencies; lower relevance |
| **GraphRAG** | Knowledge graph + community summaries via Leiden clustering | 80% accuracy vs. 50% traditional RAG; $20–500 indexing |
| **LazyGraphRAG** | 0.1% of GraphRAG indexing cost | Quality maintained; practical for production |
| **GraLC-RAG** | Graph-Aware Late Chunking | Bridges structure-rich + context-rich approaches |
| **Meta-Chunking** | Perplexity-based chunking + margin sampling for boundary detection | Adapts chunk size to content semantics |
| **Mix-of-Granularity** | Query-conditioned routing across granularity levels (sentence/paragraph/section) | Matches retrieval granularity to query type |
| **Hybrid BM25 + dense** | Reciprocal rank fusion (4:1 favoring dense) | Production standard for 2026 |

**80% of RAG retrieval failures trace to chunking strategy or embedding model, not the database.**

### 2.7 Agent Frameworks

| Framework | Paradigm | Stars | Strength | Production Users |
|-----------|----------|:-----:|----------|-----------------|
| **LangGraph** (1.0 GA) | Graph-based | 30K+ | Checkpointing, HITL, time travel, conditional routing | Klarna, Replit, Elastic |
| **CrewAI** | Role-based | — | Lowest learning curve; fast prototyping; **3× token footprint** vs other frameworks | — |
| **Claude Agent SDK** | Autonomous loop | — | Deepest MCP, hierarchical spawning, best coding model | Surpassed AutoGen Feb-Apr 2026 |
| **OpenAI Agents SDK** | Handoff-based | — | Handoff pattern, tool orchestration | — |
| **Microsoft Agent Framework** | Graph-based | 54K+ (AutoGen legacy) | .NET/Microsoft stack, responsible AI guardrails | Enterprise |
| **Pydantic AI** | Type-safe | 17K | FastAPI-style, typed I/O, deterministic testing | — |

**MCP**: 97M monthly SDK downloads, 81K+ GitHub stars. JSON-RPC 2.0, OAuth 2.1 auth. Donated to Linux Foundation (AAIF). Supported by every major vendor.

### 2.8 Frontier Capabilities and Limitations

**Benchmark saturation**:

| Benchmark | Introduced | Current Top | Status |
|-----------|:----------:|:-----------:|--------|
| **MMLU** | 2020 | >92% (all frontier) | Saturated; label errors cap at ~95% |
| **HellaSwag** | 2019 | >95% | Saturated |
| **GPQA Diamond** | 2023 | ~94% | Approaching ceiling |
| **HLE** | 2025 | 53.3% (Fable 5) | From unsolvable to half-solved in <1 year |
| **SWE-bench Verified** | 2024 | 97.0% (Opus 5) | Contamination concerns |
| **SWE-bench Pro** | 2025 | 64.3% (Opus 4.7) | Still discriminates |
| **ARC-AGI 2** | 2025 | 85.0% (GPT-5.5) | ARC-AGI 1 effectively solved |

**The jagged frontier**: Models earn IMO gold medals but fail ClockBench (50.1% on telling time). WebArena: 15% → 74.3% in 3 years. Swiss-Bench (legal compliance): even the best model achieves only 38.2%.

**Multiple frontiers**: The single "frontier model" concept has fractured into distinct frontiers — regulatory (EU AI Act 10^26 FLOPs threshold defines "systemic risk"), efficiency (1B models with optimal test-time compute rivaling 405B), cost (open-weight R1 at 27× cheaper than proprietary), and multimodal (OSWorld 2.0 at 20.6% shows computer use is far from solved). Architects must position their model strategy across all four, not just benchmark scores.

**Persistent limitations**: AI safety incidents rose from 233 (2024) to 362 (2025). Coding agent reliability remains below 65% for high-skill workflows, with failure modes breaking down as: 60% execution errors, 20% coherence loss across long sessions, 20% verification gaps. The 1-in-3 production failure rate across enterprise AI deployments means architects must design for failure as the common case, not the exception.

**Emergent abilities debate**: NeurIPS Outstanding Paper argued emergence is a "mirage" caused by metric choice. Counter: "all or nothing" metrics may be more practically meaningful. 2026 paper "Growing Pains" found below a critical scale, reasoning and truthfulness anticorrelate — above it, they cooperate.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Frontier AI Capabilities

| Capability | Cost Range | Key Driver |
|-----------|:---------:|------------|
| **Reasoning (o3-class)** | $2–10 / MTok input, $8–50 / MTok output | Thinking tokens billed as output but hidden |
| **Reasoning (R1-class open)** | $0.55 / MTok input, $2.19 / MTok output | 27–34× cheaper than Claude Opus with thinking |
| **Long-context (1M tokens)** | $2.50–10 / MTok input | Some providers 2× past 200K |
| **Multimodal vision** | ~$5–15 per 1K images (varies by resolution) | Pixel-to-token conversion; dynamic resolution |
| **GraphRAG indexing** | $20–500 per corpus | LLM calls for entity extraction + community summarization |
| **LazyGraphRAG indexing** | 0.1% of GraphRAG (~$0.02–0.50) | Skip precomputed summaries |
| **Contextual retrieval prep** | ~$1.02 / M doc tokens (with caching) | LLM context generation per chunk |
| **Model merging** | $0 (compute only, no training) | MergeKit runs on CPU; memory-efficient streaming |
| **Synthetic data distillation** | $500–5K per dataset | Teacher model API calls + quality filtering |
| **Agent framework runtime** | Variable (LangGraph: free self-hosted to managed) | Checkpointing overhead; MCP tool calls |

### 3.2 Latency SLA Targets

| Component | p50 | p95 | p99 | Mitigation |
|-----------|:---:|:---:|:---:|------------|
| **TTFT (standard)** | <200ms | <500ms | <1s | Prefix caching; queue management |
| **TTFT (reasoning model)** | 5s | 15s | 32s | Expected for thinking; stream partial results |
| **Long-context prefill (1M)** | 10s | 30s | 60s | FlashAttention-3; Ring Attention; disaggregated prefill |
| **RAG retrieval (hybrid)** | 50ms | 100ms | 200ms | Vector index + BM25 in parallel; rerank async |
| **GraphRAG query (local)** | 200ms | 500ms | 1s | Pre-indexed; entity cache |
| **GraphRAG query (global)** | 2s | 5s | 10s | Community summary map-reduce; async |
| **Multimodal (image encode)** | 100ms | 200ms | 500ms | Dynamic resolution; batch images |
| **MoE expert routing** | <1ms | <2ms | <5ms | On-device; no network hop |
| **Agent step (tool call)** | 500ms | 2s | 5s | Parallel tool calls; timeout + fallback |
| **MergeKit merge (70B)** | 15min | 30min | 60min | Memory streaming; CI/CD pipeline |

### 3.3 Throughput & Back-Pressure

**Reasoning model throughput**: o4-mini generates at 154.8 tok/s but TTFT is ~32s (thinking). R1 with 671B MoE activates only 37B per token — 18× less compute than a dense equivalent.

**Long-context throughput**: TTT-E2E delivers 35× speedup for 2M context. FlashAttention-3 at 1.3 PFLOPs/s on H100. Prompt caching saves 90% on repeated prefixes.

**Back-pressure mechanisms**:
- **Reasoning budget**: Cap thinking tokens per request. Claude adaptive thinking auto-calibrates; o3/o4-mini accept reasoning effort parameter (low/medium/high).
- **Context window overflow**: If input exceeds effective context (not advertised), truncate or chunk. Monitor context rot indicators.
- **GraphRAG indexing**: Full index costs $20–500. Use LazyGraphRAG (0.1% cost) for large corpora. Rate-limit entity extraction to control LLM API costs.
- **MoE expert load**: Auxiliary-loss-free balancing (DeepSeek) adjusts routing bias in real time. Expert-choice routing drops tokens under overload — monitor drop rate.
- **Agent loop bound**: Hard limit on agent steps (e.g., 50 max). Kill runaway loops. Monitor step count and cost per run.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Knowledge graph** | Per-triple (append) | <60s (graph DB restart) | Neo4j: WAL + snapshot; WAL-based recovery |
| **Vector index** | Per-write (WAL) | <30s (index reload) | Qdrant/pgvector: WAL + snapshot |
| **Merged model weights** | 0 (deterministic rebuild) | 15–60min (re-merge from sources) | MergeKit config + source weights in registry |
| **MoE expert weights** | 0 (immutable, versioned) | <30s (reload from NVMe) | Weight files versioned in model registry |
| **Community summaries** | Per-build (batch) | Hours (rebuild from graph) | Store summaries + Leiden clustering config |
| **Eval results / Arena Elo** | Per-eval (append-only) | <5s (DB reconnect) | Append-only log; score history preserved |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Frontier AI Systems

#### 4.1.1 State Machine

```
                  requests flowing
             ┌───────────────┐
             │               │
             ▼               │
        ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
        │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
        │         │    │          │    │             │
        │ Normal  │    │ Degrade: │    │ Route 3    │
        │ frontier│    │ fallback │    │ test reqs   │
        │ model   │    │ to simpler│   │ through     │
        │ serving │    │ model or │    │ primary     │
        │         │    │ cached   │    │ model       │
        │         │    │ response │    │             │
        └─────────┘    └──────────┘    └─────────────┘
             ▲          │       ▲            │
             │          │       │            │
             │          │       └────────────┘
             │          │      test req quality
             │     after 60s   below threshold
             │     recovery timeout
             │     (60s → 120s → 240s exponential)
             │
             └──────────────────────────────┘
                   3/3 test requests succeed:
                   quality score ≥ 0.8 AND
                   TTFT < 3× baseline AND
                   no hallucination flag
```

**Thresholds**:
- **Closed → Open**: 5 failures within 120s window. Failures include: reasoning timeout (>60s for standard, >120s for reasoning models), API error (5xx), quality score <0.5 on consecutive requests, context rot detection (retrieval accuracy drops >20% vs baseline).
- **Open duration**: 60s initial recovery timeout with exponential backoff (60s → 120s → 240s). Cap at 15 minutes.
- **Open behavior**: Cascade to simpler model tier (frontier → mid-tier → budget → cached response). For reasoning tasks: disable extended thinking, use standard generation. For RAG: fall back to vector-only retrieval (skip GraphRAG).
- **Half-Open probes**: Route 3 test requests through primary model.
- **Half-Open → Closed**: All 3 requests succeed with quality score ≥0.8 AND TTFT <3× baseline AND no hallucination flag on any response.
- **Escalation**: If circuit stays open >10 minutes, page on-call. If reasoning model consistently fails, evaluate whether provider has pushed a silent update (OpenAI April 2025 pattern).

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Reasoning model timeout (extended thinking) | **Transient** | TTFT >60s (standard) or >120s (reasoning) | Cap thinking budget; reduce effort level |
| Context rot (retrieval degradation at depth) | **Permanent** (architectural) | Multi-needle accuracy drop >20% at target depth | Reduce context window; switch to hybrid RAG |
| GraphRAG entity drift (duplicate entities) | **Permanent** (data quality) | Entity deduplication audit; triple count anomaly | Re-index with improved entity resolution |
| MoE expert collapse (all tokens routed to few experts) | **Permanent** (training bug) | Expert utilization distribution monitoring | Retrain with auxiliary balancing; fallback to dense |
| Model merge quality regression | **Permanent** (composition error) | Eval score drop post-merge vs. source models | Revert to pre-merge; adjust merge coefficients |
| Synthetic data contamination | **Permanent** (data poisoning) | Membership inference attack; canary detection | Quarantine synthetic batch; retrain from clean data |
| Multimodal hallucination (vision) | **Transient** | Object detection discrepancy; user reports | Cross-validate with OCR/object detector; flag response |
| Agent framework infinite loop | **Transient** | Step count >50; token budget exceeded | Hard kill; reduce max steps; add loop detection |
| MCP tool poisoning | **Permanent** (security) | Tool output schema violation; anomalous behavior | Revoke MCP server; audit OAuth 2.1 scopes |
| Benchmark contamination in eval | **Permanent** (process gap) | Train-test overlap detection; canary question | Use LiveBench (monthly refresh); hold out canaries |

### 4.3 Idempotency in Frontier Systems

- **Reasoning model calls**: Non-idempotent (different thinking paths produce different answers). Mitigate with request dedup at gateway (same `request_id` returns cached result). For critical decisions, use majority voting across N independent reasoning runs.
- **GraphRAG indexing**: Entity extraction is non-deterministic. Make idempotent via content-hash-based dedup: same document chunk + same LLM version produces same entity set. Re-indexing unchanged documents is a no-op.
- **Model merging**: Deterministic given same source weights + same merge config (YAML). Pin source model versions. MergeKit produces identical outputs for identical inputs.
- **Synthetic data generation**: Non-deterministic by design. Make pipeline idempotent via hash-based dedup on generated outputs. Re-running with same seed + same prompt + same temperature produces cache hit.
- **Eval runs**: Key by `hash(model_version + benchmark_version + eval_config)`. Re-running identical config returns cached scores.

### 4.3.1 Poison-Pill Detection in Frontier Systems

**Detection heuristics**:
- **Reasoning model sycophancy**: Model agrees with user's incorrect premise during extended thinking. Detect by comparing answers with and without user-stated preference on control set. Quarantine responses where agreement rate exceeds 90% on controversial control questions.
- **Synthetic data collapse**: Training on model-generated data producing progressively lower-quality outputs. Detect by monitoring entropy of training data distribution across generations. Alert when entropy drops >15% from initial generation.
- **GraphRAG community poisoning**: Adversarial documents inserted to create false entity relationships. Detect by monitoring graph topology changes: new communities appearing with suspiciously high centrality. Quarantine newly added documents pending provenance verification.
- **MCP tool injection**: Malicious MCP server returning crafted outputs to manipulate agent behavior. Detect via output schema validation, anomaly detection on tool response distribution, and OAuth 2.1 scope enforcement. Quarantine unverified MCP servers.
- **Benchmark contamination**: Model trained on eval data, inflating benchmark scores. Detect via canary questions (unique, never-published items). If model scores >2σ above expected on canaries, flag training data overlap.

**Quarantine flow**: Flagged artifact (data, model, tool server) isolated from production pipeline. Excluded from training data mixtures, model registries, and eval baselines. Alert for investigation. Promoted only after root cause addressed and clean re-evaluation passes.

### 4.4 Zero-Trust Boundaries

1. **Reasoning trace isolation**: Extended thinking traces may contain sensitive reasoning about user data. Traces stored separately from standard logs. Access requires elevated permissions. o3-style hidden CoT is never stored; R1-style visible CoT requires PII scrubbing before retention.

2. **MCP server authentication**: OAuth 2.1 as authentication standard (June 2025 spec). MCP servers classified as OAuth Resource Servers. Every tool call carries a scoped token. Unauthenticated MCP servers rejected by default.

3. **Model weight provenance**: Merged models inherit risk from all source models. Track full provenance chain (source weights → merge config → output weights). Verify checksums. Never deploy merged models without eval against safety benchmarks.

4. **Synthetic data lineage**: Every synthetic datum tagged with generator model version, prompt used, and quality filter results. Data lineage from synthetic generation → filtering → training → production model.

5. **Long-context content isolation**: 1M-token contexts may contain multiple customers' data in multi-tenant scenarios. Enforce tenant isolation at context assembly — never mix tenants within a single context window.

---

## 5. Production Enterprise Code

### 5.1 Reasoning Router with Adaptive Budget

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ReasoningEffort(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ReasoningProvider(Enum):
    OPENAI_O3 = "o3"
    OPENAI_O4_MINI = "o4-mini"
    DEEPSEEK_R1 = "deepseek-r1"
    CLAUDE_ADAPTIVE = "claude-opus-4.7"


@dataclass
class ReasoningConfig:
    provider: ReasoningProvider
    effort: ReasoningEffort
    max_thinking_tokens: int
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    visible_cot: bool


CONFIGS = {
    ReasoningProvider.CLAUDE_ADAPTIVE: {
        ReasoningEffort.LOW: ReasoningConfig(
            ReasoningProvider.CLAUDE_ADAPTIVE, ReasoningEffort.LOW,
            1024, 5.0, 25.0, True),
        ReasoningEffort.HIGH: ReasoningConfig(
            ReasoningProvider.CLAUDE_ADAPTIVE, ReasoningEffort.HIGH,
            32768, 5.0, 25.0, True),
        ReasoningEffort.XHIGH: ReasoningConfig(
            ReasoningProvider.CLAUDE_ADAPTIVE, ReasoningEffort.XHIGH,
            131072, 5.0, 25.0, True),
    },
    ReasoningProvider.DEEPSEEK_R1: {
        ReasoningEffort.HIGH: ReasoningConfig(
            ReasoningProvider.DEEPSEEK_R1, ReasoningEffort.HIGH,
            65536, 0.55, 2.19, True),
    },
    ReasoningProvider.OPENAI_O4_MINI: {
        ReasoningEffort.MEDIUM: ReasoningConfig(
            ReasoningProvider.OPENAI_O4_MINI, ReasoningEffort.MEDIUM,
            16384, 1.10, 4.40, False),
        ReasoningEffort.HIGH: ReasoningConfig(
            ReasoningProvider.OPENAI_O4_MINI, ReasoningEffort.HIGH,
            65536, 1.10, 4.40, False),
    },
}


class ReasoningRouter:
    def __init__(self, cost_cap_per_request: float = 1.0,
                 require_visible_cot: bool = False):
        self.cost_cap = cost_cap_per_request
        self.require_visible = require_visible_cot

    def select(self, complexity_score: float,
               estimated_input_tokens: int,
               requires_coding: bool = False) -> ReasoningConfig:
        if requires_coding:
            effort = (ReasoningEffort.XHIGH if complexity_score > 0.8
                      else ReasoningEffort.HIGH)
            return CONFIGS[ReasoningProvider.CLAUDE_ADAPTIVE][effort]

        if complexity_score < 0.3:
            return CONFIGS[ReasoningProvider.OPENAI_O4_MINI][
                ReasoningEffort.MEDIUM]

        if self.require_visible:
            return CONFIGS[ReasoningProvider.DEEPSEEK_R1][
                ReasoningEffort.HIGH]

        effort = (ReasoningEffort.HIGH if complexity_score > 0.6
                  else ReasoningEffort.MEDIUM)
        if (ReasoningProvider.OPENAI_O4_MINI in CONFIGS
                and effort in CONFIGS[ReasoningProvider.OPENAI_O4_MINI]):
            config = CONFIGS[ReasoningProvider.OPENAI_O4_MINI][effort]
        else:
            config = CONFIGS[ReasoningProvider.CLAUDE_ADAPTIVE].get(
                effort, CONFIGS[ReasoningProvider.CLAUDE_ADAPTIVE][
                    ReasoningEffort.HIGH])

        est_cost = (
            (estimated_input_tokens / 1_000_000) * config.input_cost_per_mtok
            + (config.max_thinking_tokens / 1_000_000)
            * config.output_cost_per_mtok
        )
        if est_cost > self.cost_cap:
            return CONFIGS[ReasoningProvider.DEEPSEEK_R1][
                ReasoningEffort.HIGH]
        return config
```

### 5.2 Hybrid Context Assembler (RAG + Long-Context)

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RetrievedChunk:
    text: str
    source: str
    score: float
    context_prefix: str = ""


@dataclass
class ContextDecision:
    strategy: str
    chunks: list[RetrievedChunk]
    total_tokens: int
    estimated_context_utilization: float


class HybridContextAssembler:
    def __init__(self, max_effective_context: int = 200_000,
                 vector_retriever=None, graph_retriever=None,
                 tokenizer=None):
        self.max_effective = max_effective_context
        self.vector = vector_retriever
        self.graph = graph_retriever
        self.tokenizer = tokenizer

    def assemble(self, query: str, query_type: str = "local",
                  target_chunks: int = 20) -> ContextDecision:
        if query_type == "global" and self.graph:
            return self._graph_rag_path(query, target_chunks)
        if query_type == "local":
            return self._hybrid_rag_path(query, target_chunks)
        return self._vector_only_path(query, target_chunks)

    def _hybrid_rag_path(self, query: str,
                          target_chunks: int) -> ContextDecision:
        vector_chunks = self.vector.search(query, k=target_chunks)
        bm25_chunks = self.vector.bm25_search(query, k=target_chunks // 2)

        fused = self._reciprocal_rank_fusion(
            vector_chunks, bm25_chunks, dense_weight=4, sparse_weight=1)
        fused = fused[:target_chunks]

        total_tokens = sum(
            self.tokenizer.count(c.context_prefix + c.text) for c in fused)

        if total_tokens > self.max_effective:
            fused = self._trim_to_budget(fused, self.max_effective)
            total_tokens = sum(
                self.tokenizer.count(c.context_prefix + c.text)
                for c in fused)

        return ContextDecision(
            strategy="hybrid_rag",
            chunks=fused,
            total_tokens=total_tokens,
            estimated_context_utilization=min(
                1.0, total_tokens / self.max_effective),
        )

    def _graph_rag_path(self, query: str,
                         target_chunks: int) -> ContextDecision:
        community_summaries = self.graph.global_search(query)
        chunks = [
            RetrievedChunk(
                text=s.summary, source=f"community_{s.community_id}",
                score=s.relevance, context_prefix="")
            for s in community_summaries
        ]
        total_tokens = sum(self.tokenizer.count(c.text) for c in chunks)
        return ContextDecision(
            strategy="graph_rag_global",
            chunks=chunks,
            total_tokens=total_tokens,
            estimated_context_utilization=min(
                1.0, total_tokens / self.max_effective),
        )

    def _vector_only_path(self, query: str,
                           target_chunks: int) -> ContextDecision:
        chunks = self.vector.search(query, k=target_chunks)
        total_tokens = sum(
            self.tokenizer.count(c.context_prefix + c.text) for c in chunks)
        return ContextDecision(
            strategy="vector_only",
            chunks=chunks,
            total_tokens=total_tokens,
            estimated_context_utilization=min(
                1.0, total_tokens / self.max_effective),
        )

    def _reciprocal_rank_fusion(
        self, dense: list[RetrievedChunk],
        sparse: list[RetrievedChunk],
        dense_weight: int = 4, sparse_weight: int = 1,
        k: int = 60
    ) -> list[RetrievedChunk]:
        scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}
        for rank, chunk in enumerate(dense):
            key = chunk.source + ":" + chunk.text[:50]
            scores[key] = scores.get(key, 0) + dense_weight / (k + rank + 1)
            chunk_map[key] = chunk
        for rank, chunk in enumerate(sparse):
            key = chunk.source + ":" + chunk.text[:50]
            scores[key] = scores.get(key, 0) + sparse_weight / (k + rank + 1)
            chunk_map.setdefault(key, chunk)

        sorted_keys = sorted(scores, key=lambda x: scores[x], reverse=True)
        return [chunk_map[k] for k in sorted_keys]

    def _trim_to_budget(self, chunks: list[RetrievedChunk],
                         budget: int) -> list[RetrievedChunk]:
        result = []
        running = 0
        for chunk in chunks:
            tokens = self.tokenizer.count(chunk.context_prefix + chunk.text)
            if running + tokens > budget:
                break
            result.append(chunk)
            running += tokens
        return result
```

### 5.3 Benchmark Saturation Tracker

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BenchmarkScore:
    model: str
    score: float
    date: str


@dataclass
class BenchmarkStatus:
    name: str
    theoretical_ceiling: float
    scores: list[BenchmarkScore] = field(default_factory=list)

    @property
    def current_best(self) -> float:
        return max((s.score for s in self.scores), default=0.0)

    @property
    def headroom(self) -> float:
        return self.theoretical_ceiling - self.current_best

    @property
    def is_saturated(self) -> bool:
        return self.headroom < 5.0

    @property
    def model_spread(self) -> float:
        if len(self.scores) < 2:
            return 0.0
        vals = [s.score for s in self.scores]
        return max(vals) - min(vals)


class SaturationTracker:
    def __init__(self):
        self.benchmarks: dict[str, BenchmarkStatus] = {}

    def register(self, name: str, ceiling: float) -> None:
        self.benchmarks[name] = BenchmarkStatus(
            name=name, theoretical_ceiling=ceiling)

    def add_score(self, benchmark: str, model: str,
                   score: float, date: str) -> None:
        if benchmark not in self.benchmarks:
            return
        self.benchmarks[benchmark].scores.append(
            BenchmarkScore(model=model, score=score, date=date))

    def recommend_eval_stack(self, min_headroom: float = 5.0,
                              min_spread: float = 3.0
                              ) -> list[str]:
        valid = []
        for name, bench in self.benchmarks.items():
            if bench.headroom >= min_headroom and bench.model_spread >= min_spread:
                valid.append(name)
        return sorted(valid, key=lambda n: self.benchmarks[n].headroom,
                      reverse=True)

    def saturated_benchmarks(self) -> list[str]:
        return [name for name, b in self.benchmarks.items() if b.is_saturated]

    def report(self) -> dict:
        return {
            name: {
                "best": b.current_best,
                "ceiling": b.theoretical_ceiling,
                "headroom": b.headroom,
                "spread": b.model_spread,
                "saturated": b.is_saturated,
                "num_models": len(b.scores),
            }
            for name, b in self.benchmarks.items()
        }
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Knowledge Platform with Hybrid RAG + Reasoning

**Business context**: A management consulting firm with 5,000 consultants needs an internal knowledge platform that can answer complex analytical questions across 500K proprietary documents (strategy reports, financial analyses, client deliverables). Current keyword search yields <30% consultant satisfaction. Requirements: answer both specific point queries ("What was Client X's revenue in Q3?") and broad thematic questions ("What are the emerging trends in healthcare AI across our client base?"), cite sources with page numbers, handle multi-step reasoning, and operate under strict data residency (EU-hosted, no data leaves the tenant).

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   HYBRID KNOWLEDGE PLATFORM                              │
 │                                                                          │
 │  Query ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐   │
 │            │ Query Type   │     │ Retrieval    │     │ Reasoning  │   │
 │            │ Classifier   │     │ Engine       │     │ Engine     │   │
 │            │              │     │              │     │            │   │
 │            │ Point query  │     │ Vector: Hybrid│    │ Claude     │   │
 │            │ → vector RAG │     │ BM25+dense   │     │ Adaptive   │   │
 │            │              │     │ (contextual  │     │ Thinking   │   │
 │            │ Thematic     │     │  retrieval)  │     │ (EU-hosted │   │
 │            │ → GraphRAG   │     │              │     │  via       │   │
 │            │              │     │ Graph: Lazy  │     │  Langfuse  │   │
 │            │ Multi-step   │     │ GraphRAG     │     │  self-     │   │
 │            │ → reasoning  │     │ (0.1% cost)  │     │  hosted)   │   │
 │            └──────────────┘     └──────────────┘     └────────────┘   │
 │                                                                        │
 │  ┌────────────────────────────────────────────────────────────────────┐ │
 │  │  Data Pipeline: 500K docs → contextual chunking → pgvector        │ │
 │  │  + LazyGraphRAG index → weekly incremental refresh                 │ │
 │  └────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Pure Long-Context (Stuff 1M) | B: Hybrid RAG + Reasoning (Recommended) | C: Pure Vector RAG |
|-----------|-------------------------------|------------------------------------------|-------------------|
| **Point query accuracy** | ⬛⬛⬜ — Context rot at depth; 89% NIAH at 1M | ⬛⬛⬛ — Contextual retrieval (67% failure reduction) | ⬛⬛⬛ — Good for point lookups |
| **Thematic query accuracy** | ⬛⬛⬛ — Full document reasoning (if <200K) | ⬛⬛⬛ — GraphRAG community summaries (80% vs 50%) | ⬛⬛⬜ — Misses cross-document themes |
| **Multi-step reasoning** | ⬛⬛⬛ — Native in-context | ⬛⬛⬛ — Adaptive thinking over retrieved context | ⬛⬛⬜ — No native reasoning |
| **Cost at 500K docs** | ⬛⬜⬜ — $10/MTok × 1M tokens = $10/query | ⬛⬛⬛ — ~$0.10/query (retrieve 50K + reason) | ⬛⬛⬛ — ~$0.05/query |
| **Citation accuracy** | ⬛⬛⬜ — Hallucinated citations common at depth | ⬛⬛⬛ — Source chunks attached; page numbers preserved | ⬛⬛⬜ — Chunk-level only |
| **Data residency** | ⬛⬛⬛ — Self-hosted model | ⬛⬛⬛ — Self-hosted pgvector + EU API region | ⬛⬛⬛ — Self-hosted |

**Recommended approach**: **B (Hybrid RAG + Reasoning)**.

**Decision rationale**: Option A (pure long-context) is prohibitively expensive ($10/query × 5,000 consultants × 20 queries/day = $1M/day) and suffers context rot — at 500K documents, no single context window can hold the corpus, and even 200K-token windows show 30–60 point retrieval drops for buried information. Option C (pure vector RAG) handles point queries well but fails on thematic questions requiring cross-document synthesis — the firm's highest-value use case. Option B combines contextual retrieval (prepend document context before embedding, 67% failure reduction) with LazyGraphRAG (0.1% of GraphRAG indexing cost, ~$50 for 500K documents vs. $50K for full GraphRAG) for thematic queries, and Claude adaptive thinking for multi-step reasoning over retrieved context. The query classifier routes: point queries to vector RAG (fast, cheap), thematic queries to LazyGraphRAG (community summaries), and complex analytical queries to reasoning with retrieved context. pgvector self-hosted in EU satisfies data residency. Total cost: ~$0.10/query average, or ~$50K/month at scale — 20× cheaper than Option A.

### 6.2 Scenario: Building a Cost-Effective Reasoning Pipeline for a Coding Agent Platform

**Business context**: A developer tools company builds a coding agent platform serving 50,000 developers. The agent handles bug fixes, feature implementation, and code review. Current approach: send every request to Claude Opus 4.7 with adaptive thinking — excellent quality (87.6% SWE-bench Verified) but costs $0.50–3.00 per coding task. Monthly LLM spend: $800K. The CEO wants to cut inference costs by 70% while maintaining >80% task completion. Volume: 2M coding tasks/month.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   COST-OPTIMIZED REASONING PIPELINE                      │
 │                                                                          │
 │  Task ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐   │
 │           │ Complexity   │     │ Model        │     │ Verify &   │   │
 │           │ Classifier   │     │ Router       │     │ Escalate   │   │
 │           │              │     │              │     │            │   │
 │  Score:   │ 0.0-0.3:     │     │ Simple:      │     │ Run tests  │   │
 │  Simple   │  autocomplete│     │  o4-mini low │     │ If fail:   │   │
 │  Medium   │ 0.3-0.6:     │     │ Medium:      │     │  escalate  │   │
 │  Complex  │  bug fix     │     │  R1 (27x     │     │  to Opus   │   │
 │           │ 0.6-1.0:     │     │  cheaper)    │     │  4.7       │   │
 │           │  feature     │     │ Complex:     │     │            │   │
 │           │              │     │  Opus 4.7    │     │            │   │
 │           │              │     │  adaptive    │     │            │   │
 │           └──────────────┘     └──────────────┘     └────────────┘   │
 │                                                                        │
 │  ┌────────────────────────────────────────────────────────────────────┐ │
 │  │  Traffic Split: Simple 55% | Medium 30% | Complex 15%             │ │
 │  │  Escalation rate: ~10% of simple/medium tasks → Opus              │ │
 │  │  Effective cost: ~$0.15/task avg (vs $1.50 current)               │ │
 │  └────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: All Opus 4.7 (Current) | B: Tiered Reasoning with Verify + Escalate (Recommended) | C: All DeepSeek R1 |
|-----------|--------------------------|-----------------------------------------------------------|-------------------|
| **Task completion rate** | ⬛⬛⬛ — 87.6% SWE-bench | ⬛⬛⬛ — >80% (simple+medium handled by budget; complex by Opus) | ⬛⬛⬜ — 49% SWE-bench (insufficient) |
| **Monthly cost** | ⬛⬜⬜ — $800K ($1.50/task avg) | ⬛⬛⬛ — ~$300K ($0.15/task avg, 70% reduction) | ⬛⬛⬛ — ~$100K ($0.05/task avg) |
| **CoT visibility** | ⬛⬛⬛ — Visible thinking blocks | ⬛⬛⬜ — Mixed (R1 visible, o4-mini hidden) | ⬛⬛⬛ — Fully visible `<think>` tags |
| **Quality consistency** | ⬛⬛⬛ — Single model, consistent | ⬛⬛⬜ — Variable across tiers; verify+escalate mitigates | ⬛⬛⬜ — Lower baseline quality |
| **Implementation complexity** | ⬛⬛⬛ — Trivial (one endpoint) | ⬛⬛⬜ — Classifier + router + escalation (3-4 weeks) | ⬛⬛⬛ — Trivial (one endpoint) |
| **Open-weight option** | ⬛⬜⬜ — Proprietary | ⬛⬛⬜ — R1 tier is open-weight | ⬛⬛⬛ — MIT license, self-hostable |

**Recommended approach**: **B (Tiered Reasoning with Verify + Escalate)**.

**Decision rationale**: Option A works but $800K/month is unsustainable at current growth. Option C (all R1) achieves massive cost savings but 49% SWE-bench is below the 80% quality requirement — R1 excels at math/reasoning but lags on complex multi-file coding tasks. Option B routes by complexity: simple tasks (55% — autocomplete, single-file fixes) to o4-mini low effort ($1.10/$4.40, minimal thinking), medium tasks (30% — standard bug fixes, refactors) to DeepSeek R1 ($0.55/$2.19, 27× cheaper than Opus with visible CoT for debugging), and complex tasks (15% — multi-file features, architectural changes) to Claude Opus 4.7 adaptive thinking. The verify-and-escalate pattern runs tests after every completion; if tests fail on a simple/medium task, it automatically escalates to Opus 4.7 (~10% escalation rate observed in production). Effective cost: 55% × $0.03 + 30% × $0.08 + 15% × $0.50 + 10% × $0.50 (escalation) ≈ $0.15/task average. At 2M tasks/month: ~$300K vs. $800K (62% reduction). The 3–4 week implementation investment (complexity classifier trained on historical task data, routing logic, escalation pipeline) pays for itself in the first month.

---

*Module 17 complete. Covers reasoning models (o3, o4-mini, DeepSeek-R1 GRPO, Claude adaptive thinking, test-time compute scaling), long-context (1M–2M tokens, context rot, hybrid architecture as 2026 default), multimodal (4 architectural eras, computer use 78% OSWorld), model merging (SLERP/TIES/DARE/task arithmetic, MoE with DeepSeek V4 at 96.9% sparsity), synthetic data (distillation, model collapse contained by real+synthetic mixture), RAG advances (contextual retrieval 67% failure reduction, GraphRAG, LazyGraphRAG at 0.1% cost, late chunking), agent frameworks (LangGraph, CrewAI, Claude Agent SDK, MCP 97M downloads, Pydantic AI), and frontier limitations (benchmark saturation, jagged frontier, emergent abilities debate).*
