# Research: How Fine Tuning Works
**Date researched**: 2026-09-29
**Sources consulted**: 27

---

## 1. System Topology & Mechanics

### 1.1 The Fine-Tuning Pipeline

Fine-tuning runs the same next-token prediction training process as pretraining but on a much smaller, targeted dataset. The system comprises four components:

1. **Base model** -- the pretrained LLM (Llama, Qwen, Mistral, GPT-4.1, etc.)
2. **Dataset** -- instruction-response pairs encoding target behavior
3. **Fine-tuning pipeline** -- shifts base model behavior toward the target domain
4. **Serving endpoint** -- serves the resulting model, optionally paired with RAG

**Core principle**: "Fine-tune for behavior, retrieve for knowledge." A reasoning pattern repeated across thousands of examples persists; a fact appearing once or twice will not.

### 1.2 Three Training Regimes

| Regime | Data Type | Dataset Size | When to Use |
|--------|-----------|-------------|-------------|
| **Continued Pretraining** | Raw unlabeled domain text | 100M--1B+ words | Base model barely understands your domain (e.g., BloombergGPT, Med-PaLM) |
| **Supervised Fine-Tuning (SFT)** | Instruction-response pairs | 1,000--50,000 pairs | The right starting point for almost every project. Addresses style, format, reasoning patterns |
| **Preference Tuning (RLHF/DPO)** | Comparison pairs (chosen vs rejected) | 500--5,000 pairs | Calibrated refusals, consistent tone, output formats that SFT cannot reliably teach |

**Practical order**: Start with SFT, ship, identify remaining issues, then add DPO.

### 1.3 Parameter-Efficient Fine-Tuning (PEFT) Methods

#### Full Fine-Tuning
- Updates all model parameters.
- 7B model: parameters = 14 GB (fp16), gradients ~14 GB, optimizer states ~32 GB. Total GPU memory: **60--80 GB**.
- Strongest on hard tasks (math, code), but highest cost and highest forgetting risk.

#### LoRA (Low-Rank Adaptation)
- Freezes base weights W entirely. Injects trainable low-rank matrices A and B such that W' = W + AB^T.
- Rank r typically 8 or 16. Trains **<1% of parameters** full fine-tuning would touch.
- Recovers **90--95% of full fine-tuning quality** on most tasks.
- GPU memory drops by an order of magnitude.
- **Target modules matter more than rank**: targeting all linear layers (q_proj, k_proj, v_proj, o_proj, gate_proj, down_proj, up_proj, lm_head) is the biggest quality lever. Rank beyond r=8 shows diminishing returns (Databricks study on OpenLLaMA-3b: r=8 vs r=16 on attention blocks showed no quality improvement).
- Adapter size: typically a few MB vs. multi-GB base model.
- Merged LoRA: zero inference latency overhead. Unmerged: slight latency increase but enables adapter swapping on shared backbone.

#### QLoRA (Quantized LoRA)
- Quantizes base weights W to **4-bit NF4** (Normal Float, information-theoretically optimal). LoRA matrices A, B remain in bf16/fp16.
- Three innovations: (1) 4-bit NF4 quantization, (2) Double Quantization (quantize the quantization constants, saving ~0.5 bits/param), (3) Paged Optimizers (optimizer states page to CPU during memory spikes).
- **65B model**: previously required 8x A100 GPUs; QLoRA fits on a **single 48GB A6000**.
- **7B model**: runs on a free Google Colab T4 (16 GB).
- Trade-off: **33% memory savings at cost of 39% runtime increase** vs standard LoRA.
- Quality: negligible degradation vs LoRA/full fine-tuning. 92% of full fine-tuning performance with 70% less VRAM in low-resource scenarios.

#### Adapter Tuning
- Injects small bottleneck modules (down-project, nonlinearity, up-project) inside each transformer block.
- Trains ~0.48% of parameters for 7B model (~33.55M params).
- Slight accuracy gap to full fine-tuning but robust across domains.
- Adds non-negligible inference latency (cannot be merged like LoRA).

#### Prefix Tuning
- Learns continuous embeddings prepended to key and value vectors in each attention layer.
- Trains ~0.075% of parameters for 7B model (~5.24M params).
- Effective on generation tasks (summarization, translation). Less effective on classification.
- No base model modification needed.

#### Prompt Tuning
- Learns soft prompt embeddings prepended to input sequence.
- Trains ~0.006% of parameters (~0.41M for 7B model).
- Lightest PEFT method. Best when modifications to base model are undesirable.

#### DoRA (Weight-Decomposed Low-Rank Adaptation, 2024)
- Decomposes weight matrix into magnitude and direction components.
- Applies LoRA only to the direction component; magnitude is learned independently.
- Closes the gap with full fine-tuning on tasks requiring simultaneous adjustment of both weight magnitude and direction.

#### Comparative Summary

| Method | Trainable Params (7B) | Memory (7B) | Inference Overhead | Quality vs Full FT |
|--------|----------------------|-------------|-------------------|-------------------|
| Full Fine-Tuning | 7B (100%) | 60--80 GB | None | Baseline |
| LoRA (r=16) | ~16.8M (0.24%) | 14--16 GB | None (merged) | 90--95% |
| QLoRA (r=16) | ~16.8M (0.24%) | ~10 GB | None (merged) | ~90--95% |
| Adapters | ~33.6M (0.48%) | 14--16 GB | +5--10% latency | ~90% |
| Prefix Tuning | ~5.2M (0.075%) | 14--16 GB | Minimal | ~85--90% |
| Prompt Tuning | ~0.4M (0.006%) | 14--16 GB | Minimal | ~80--85% |

### 1.4 RLHF (Reinforcement Learning from Human Feedback)

**Pipeline**: SFT on demonstrations --> Train reward model from human preference rankings --> RL (PPO) to optimize policy against reward model.

**Infrastructure**: Requires **four concurrent model instances** at training time: reward model, critic model, actor (policy) model, and frozen reference LLM. This makes RLHF the most resource-intensive alignment method.

**Strengths**: Gold standard for production alignment (ChatGPT, Claude, GPT-4). Best instruction-following quality. Proven at scale on 10B+ models.

**Weaknesses**:
- Training instability (PPO hyperparameter sensitivity -- small changes to KL divergence coefficient produce dramatic performance swings).
- Reward hacking (model over-optimizes the reward model rather than genuine quality).
- Extremely sensitive to hyperparameters.
- 10--50x more expensive than DPO.

### 1.5 DPO (Direct Preference Optimization)

**Mechanism**: Reframes preference learning as a binary classification problem. Directly optimizes model to prefer chosen output over rejected output using a loss function derived from the Bradley-Terry model.

**Infrastructure**: Requires only **two model instances**: the policy model and a frozen reference SFT model. Eliminates reward model and RL loop entirely.

**Strengths**:
- **10--50x cheaper** than RLHF.
- Simpler loss function = less oscillation and instability.
- Matches or exceeds RLHF on summarization, helpfulness, factuality benchmarks.
- Ideal for 7B--70B models on limited GPU budgets.

**Weaknesses**:
- Susceptible to **overfitting on limited preference datasets**, reducing generalization.
- **Offline nature**: requires upfront human annotation before training begins.
- RL-based training remains indispensable for generalization on complex tasks.

### 1.6 Emerging Alignment Methods (2025--2026)

| Method | Key Idea | Quality | Cost |
|--------|----------|---------|------|
| **GRPO** (DeepSeek-R1) | Group Relative Policy Optimization; pure RL for reasoning | Excellent for reasoning tasks | Moderate |
| **KTO** | Knowledge-based optimization; requires only thumbs-up/thumbs-down | Adequate for 1B--13B | Cheapest |
| **Align-Pro** | Achieves 92% of RLHF win-rate with 8x less compute, 40% lower GPU memory | Near-RLHF | Low |
| **Hybrid DPO+PPO** (Baidu, 2024 patent) | Unified framework combining DPO and PPO | Next-gen alignment | Moderate |
| **Tied Preference Optimization** (DeepMind, 2025) | Handles tied preferences (neither response preferred) | Extends DPO | Similar to DPO |

---

## 2. Token Economics & NFR Metrics

### 2.1 GPU-Hours and Training Costs by Model Size

| Model Size | Method | Hardware | Training Time | Approx. Cost |
|-----------|--------|----------|--------------|--------------|
| 7B | QLoRA (r=8) | 1x T4 16GB (Colab free) | ~2--4 hours | ~$0--10 |
| 7B | LoRA (r=8, all linear) | 1x A100 80GB | ~15 min (5K examples) | ~$5--15 |
| 7B | Full fine-tuning | 1x A100 80GB | ~1--2 hours (5K examples) | ~$30--50 |
| 13B | QLoRA | 1x A6000 48GB | ~4--8 hours | ~$20--50 |
| 70B | QLoRA | 1x A6000 48GB | ~24--48 hours | ~$100--300 |
| 70B | LoRA + FSDP | 8x H100 SXM5 | ~3 hours (50K examples, 2K tok each) | ~$500--1,000 |
| 70B | Full fine-tuning | 8x H100 (640GB total) | Days | ~$5,000--35,000+ |

**Production throughput benchmark**: 8x H100 SXM5 with FSDP2 and flash attention: ~8,500--9,400 tokens/sec aggregate throughput. A 100M token dataset finishes in ~3 hours.

### 2.2 API Fine-Tuning Costs (as of Sept 2026)

#### OpenAI (Winding Down -- May 2026)
OpenAI is winding down its fine-tuning platform. No longer open to new users. Existing users can still fine-tune GPT-4.1, GPT-4.1-mini (SFT/DPO), and o4-mini (RFT). GPT-5.x and GPT-6 families are NOT available for fine-tuning.

| Model | Training ($/1M tokens) | Inference Input ($/1M) | Inference Output ($/1M) |
|-------|----------------------|----------------------|------------------------|
| GPT-4.1 | ~$3.00 | ~$3.00 | ~$12.00 |
| GPT-4.1 Mini | ~$0.80 | ~$0.80 | ~$3.20 |
| GPT-4o | ~$25.00 | ~$30.00 | ~$60.00 |
| GPT-4o mini | ~$3.00 | -- | -- |

OpenAI charges ~$0.50/hour additional training compute. Fine-tuned inference costs 50--100% premium over base model rates.

#### Anthropic (Claude)
As of September 2026, **Anthropic does not offer a public self-serve fine-tuning API**. Custom fine-tuning is available through enterprise sales (sales@anthropic.com). Anthropic's approach emphasizes prompt engineering, prompt caching, and tools over fine-tuning.

Inference pricing (for reference):

| Model | Input ($/1M) | Output ($/1M) |
|-------|-------------|---------------|
| Claude Haiku 4.5 | $1.00 | $5.00 |
| Claude Sonnet 5 | $2.00 | $10.00 |
| Claude Opus 5.5 | $4.00 | $20.00 |
| Claude Fable 5.1 | $10.00 | $50.00 |

#### Alternatives for Fine-Tuning (Post-OpenAI Wind-Down)

| Provider | Training Cost ($/1M tokens) | Notes |
|----------|-----------------------------|-------|
| Together AI | ~$0.48 (LoRA, 7B open-source) | Best budget option. Supports Llama, Mistral. Serverless inference included. |
| Google Vertex AI | Competitive (Gemini 2.0 Flash) | No inference cost increase for tuned models. |
| Fireworks | ~2x SFT price for DPO | Strong DPO support. Competitive on open-source. |

### 2.3 Inference Cost Impact

- **Cost crossover point**: Above ~200K--500K queries/month, self-hosted fine-tuned model becomes cheaper than API calls.
- Fine-tuned 7B model on single GPU: **TTFT < 300 ms**.
- Fine-tuned smaller model can replace larger base model: 50%+ inference cost reduction is a realistic target.
- Batch API (OpenAI, Anthropic): 50% discount with 24-hour processing window.
- Prompt caching (Anthropic): cache hits cost 0.1x standard input rate.

---

## 3. Distributed Resilience & State

### 3.1 Framework Decision Tree

| Framework | Sweet Spot | Key Feature |
|-----------|-----------|-------------|
| **FSDP2** (PyTorch native) | 7B--30B fine-tuning | Modern default. Per-parameter sharding via DTensor. torch.compile compatible. |
| **DeepSpeed ZeRO-3** | 30B--70B on tight memory | CPU and NVMe offload (unique feature). Halves throughput but fits models that FSDP cannot. |
| **Megatron-LM** (NVIDIA) | >100B pretraining | 3D parallelism. Used by Llama 3, Mistral, DeepSeek, Nemotron. Below 70B you don't need it. |

### 3.2 FSDP2: The Modern Default

- Rewrote internals on DTensor abstraction (2024). Parameters sharded per-parameter (not flattened/concatenated).
- Mix-and-match dtype per layer. Freeze individual parameters for LoRA without rewriting sharding logic.
- End-to-end torch.compile support.
- **Benchmark**: 70B Llama LoRA fine-tune fits on 8x H100s.
- Communication overlap: prefetches next parameter shard during compute via NCCL all-gathers overlapping with forward pass.
- Checkpointing: `SHARDED_STATE_DICT` for fast save/restore.

### 3.3 DeepSpeed ZeRO: When Offload Is Necessary

A 70B model in fp16 needs: 140 GB (params) + 140 GB (gradients) + 560 GB (Adam states) = **840 GB** static state. On 8x H100s (640 GB total), it does not fit without offload.

- ZeRO-3 + CPU offload: optimizer states live in CPU RAM. Cost: ~halves training speed.
- NVMe offload for even larger models.
- Hierarchical sharding: model parameter sharding within a node, replication across nodes (avoids expensive inter-node communication).

### 3.4 Checkpoint Management and Model Versioning

- **FSDP2**: `fsdp_state_dict_type: SHARDED_STATE_DICT` for fast checkpoint save/restore.
- **DeepSpeed**: `zero_to_fp32.py` to post-convert sharded checkpoints.
- **MLflow 3.0**: Extended model registry for GenAI -- connects models to exact code versions, prompt configs, eval runs, deployment metadata.
- **What must be versioned**: base model ID, fine-tuned weights/LoRA adapters, prompt templates, retrieval configurations, training hyperparameters, dataset version.
- **Tools**: MLflow, Weights & Biases, DVC, Databricks Unity Catalog.

### 3.5 Fault Tolerance and Recovery

- Both FSDP2 and DeepSpeed support periodic checkpoint saves.
- For spot instances (preemption likely): async checkpoint offload + automated restart.
- **Reproducibility requirements**: locked dataset, locked random seeds, versioned model card.
- Lifecycle stages: development --> staging --> production --> archived, with governance controls at each transition.

---

## 4. Enterprise Security & Governance

### 4.1 Training Data Privacy and Poisoning

**Data poisoning** is the insertion of malicious data into training/fine-tuning inputs to alter model behavior at inference time. This became a concrete (not theoretical) threat in 2025:

- **Jan 2025**: Hidden prompts in GitHub code comments poisoned a fine-tuned model. DeepSeek's DeepThink-R1, trained on contaminated repos, learned a persistent backdoor.
- **Grok 4 incident**: Typing `!Pliny` stripped all guardrails -- likely caused by training data saturated with jailbreak prompts from X.
- **Scale**: ~250 poisoned documents sufficient to implant a backdoor regardless of model size (13B poisoned by same number as 600M).
- **Persistence**: Backdoors survive SFT, RLHF, and adversarial training. Larger models show increased persistence.

**LoRA adapters as attack vector**: Small adapter files (~tens of MB) are perceived as low-risk but have full access to modify behavior. In Feb 2024, JFrog found ~100 malicious models on Hugging Face that executed arbitrary code on load.

**OWASP LLM Top 10 (2025)**: Data and Model Poisoning is listed as **LLM04:2025**.

### 4.2 Model Provenance and Lineage Tracking

- **AI Bill of Materials (AIBOM)**: Tracks model origin, training data used, and modification history.
- **Tools**: OWASP CycloneDX, ML-BOM for data origin and transformation tracking.
- **Dataset governance**: Restrict write access, enforce approval for corpus changes, verify lineage before training, store immutable dataset versions.
- **Model registry**: Databricks Unity Catalog + MLflow for centralized governance with fine-grained access control.

### 4.3 Compliance for Fine-Tuned Models

- **Data residency**: Fine-tuning addresses HIPAA, GDPR, SOC 2, attorney-client privilege by keeping model, data, and inference within your VPC.
- **Audit trail**: Authentication + logging at every stage.
- **Regulated industries** (finance, healthcare, pharma): GDPR, HIPAA, SOX require demonstrable control over data flows through AI systems.
- **Model lifecycle governance**: RBAC, audit logs, lineage tracking at each stage transition (dev --> staging --> prod --> archive).

### 4.4 Defense Recommendations

1. **Data provenance**: Verify origin and transformation history of all training data.
2. **Dataset versioning with rollback**: Immutable snapshots; restore known-good slices without reintroducing poisoned samples.
3. **Red teaming**: Test with sensitive topics, unusual prompts, OOD inputs.
4. **Runtime guardrails**: Defense-in-depth (provenance + red teaming + runtime monitoring).
5. **Behavioral testing**: Test fine-tuned model on capabilities that were NOT in the training set to detect regressions.

---

## 5. Production Failure Modes

### 5.1 Catastrophic Forgetting

Fine-tuning on a new task degrades performance on tasks the model previously handled. One of the most disorienting production failures because it manifests as regression in untouched workflows.

**Real-world example**: Fine-tune for invoice classification --> legal clause extraction (previously working) starts producing garbage. Nobody touched that workflow.

**Key findings (2025--2026)**:
- **LoRA does NOT prevent forgetting**. Despite minimal parameter changes, catastrophic forgetting is observed in continual learning scenarios. The issue is which paths through the network are altered, not how many.
- **Dataset size threshold**: Below ~200 examples, catastrophic forgetting is almost guaranteed.
- **Model size matters**: Larger models generally forget less (overcapacity), but no model is immune.
- **Model-specific**: Phi-3.5-mini exhibits minimal forgetting. Orca-2-7b and Qwen2.5-7B show strong post-fine-tuning retention.

**Langchain 2025 analysis** of production agent deployments identified three recurring failure modes: (1) agents giving outdated answers after policy updates, (2) workflow bots missing newly introduced rules, (3) assistants forgetting user preferences after capability expansions.

**Emerging mitigations**:

| Technique | Mechanism | Status |
|-----------|-----------|--------|
| **Functionally Invariant Paths (FIP)** | Considers geometry of loss landscape rather than parameter magnitude (Caltech) | Research, 2025 |
| **Anchored Weight Decay (AWD)** | Constrains drift in ES update rule (Cognizant) | Production-ready, 2025 |
| **Self-Distillation Fine-Tuning (SDFT)** | Model distills own knowledge into new learning signal (MIT/ETH Zurich) | Research, 2025 |
| **Selective Token Masking (STM)** | Masks high-perplexity tokens (core general knowledge) during fine-tuning | Research, 2025 |
| **Modular architectures** | Separate LoRA adapters per domain; update one without touching others | Production standard |
| **Context-window continual learning** | Curate knowledge in context window instead of weight updates | Production alternative |

### 5.2 Overfitting on Small Datasets

- Manifests as high training accuracy but poor generalization to real-world queries.
- Multi-epoch training on static datasets often **degrades** results (Raschka, 2025).
- Goodhart's Law: optimizing for narrow eval metrics makes model appear successful in testing while failing on diverse production queries.

**Mitigations**: Early stopping, validation set monitoring, single-epoch training on small datasets, data augmentation.

### 5.3 Distribution Shift Post-Fine-Tuning

- Fine-tuned model performs well on data resembling training distribution but degrades on OOD inputs.
- **Chat template mismatch**: If training template differs from inference template (system/role names, BOS/EOS tokens), model may not recognize conversation structure. This is a **top cause of degraded post-training performance**.

**Diagnostic**: Test on queries that are out-of-domain for training data. If performance drops dramatically relative to base model, you have a problem.

### 5.4 Ship Gates

Production deployment should gate on:
- General capability regression **< 5%** on MMLU-Pro or MT-Bench.
- Inference cost **>= 50% below** foundation-model baseline.
- TTFT **< 300 ms** for fine-tuned 7B on single GPU.

---

## 6. Enterprise System Design Scenarios

### 6.1 Enterprise Fine-Tuning Pipeline Architecture

```
Data Collection --> Data Curation --> Dataset Versioning (DVC/MLflow)
       |                                       |
       v                                       v
  Quality Filtering              Immutable Snapshot Storage
       |                                       |
       v                                       v
  Format Standardization -----> Training Pipeline (HF TRL/PEFT)
                                       |
                                       v
                               Experiment Tracking (W&B/MLflow)
                                       |
                                       v
                               Evaluation Suite (MMLU, MT-Bench, domain-specific)
                                       |
                                       v
                               Model Registry (MLflow 3.0 / Unity Catalog)
                                       |
                                       v
                               Staged Deployment (dev --> staging --> prod)
                                       |
                                       v
                               Inference Serving (vLLM, TGI)
                                       |
                                       v
                               Monitoring & Drift Detection (Arize, Evidently)
```

**Key tooling stack**:

| Category | Tools |
|----------|-------|
| Experiment Tracking | MLflow 3.0, Weights & Biases, Neptune.ai |
| Pipeline Orchestration | Kubeflow, Apache Airflow, SageMaker Pipelines |
| Cloud MLOps | AWS SageMaker, Azure ML, Google Vertex AI |
| Unified Platforms | Databricks (Unity Catalog), TrueFoundry |
| Data Versioning | DVC, DagsHub |
| Monitoring | Arize AI, Evidently AI, Fiddler AI |
| Serving | vLLM, TGI (Text Generation Inference) |

### 6.2 RAG vs Fine-Tuning Decision Framework

**Use RAG when**:
- Data changes weekly or more frequently.
- Source attribution and compliance traceability are required.
- Query-time access controls are needed (different users see different data).
- Speed to value matters: deploy in 1--2 weeks vs. 2--6 months for fine-tuning.
- Knowledge base scales to millions of documents.

**Use Fine-Tuning when**:
- Output must follow a specific structure/format consistently (and prompting alone is insufficient).
- Domain reasoning patterns are needed (drug interactions, circuit design, actuarial modeling).
- Style and behavior consistency is required.
- Latency-sensitive: fine-tuned model eliminates retrieval step (saves 500ms--2s per query).
- High-volume cost optimization: millions of API calls/day on narrow tasks.

**Use Both (Hybrid) when**:
- RAG retrieves current knowledge; fine-tuning ensures consistent response format/behavior.
- Start with RAG for immediate value, layer fine-tuning onto high-volume workflows.

**Industry data (2025)**:
- 70%+ of enterprise AI teams use RAG as primary knowledge grounding (Gartner 2025).
- <25% use fine-tuning as standalone approach.
- Hybrid implementations are the fastest-growing segment.
- RAG market: $1.94B (2025) --> projected $9.86B (2030), 38.4% CAGR.
- 50%+ of enterprise GenAI models will be domain-specific by 2027, up from ~1% in 2024 (Gartner).

### 6.3 Trade-Off Matrices

#### Method Selection Matrix

| Factor | Prompting | RAG | Fine-Tuning | Hybrid |
|--------|-----------|-----|-------------|--------|
| Time to deploy | Hours | 1--2 weeks | 2--6 months | 3--6 months |
| Data freshness | N/A | Real-time | Frozen at training | Mixed |
| Source attribution | No | Yes | No | Partial |
| Behavior consistency | Low | Low | High | High |
| Latency | Lowest | +500ms--2s | Low | Medium |
| Cost at scale (>500K queries/mo) | High (large model) | Medium | Low (small tuned model) | Medium |
| Data privacy | API-dependent | Controllable | Full control (self-hosted) | Mixed |
| Maintenance burden | Low | Medium (index updates) | High (retraining) | Highest |

#### PEFT Method Selection Matrix

| Factor | Full FT | LoRA | QLoRA | Adapters | Prefix Tuning |
|--------|---------|------|-------|----------|---------------|
| Best quality | Yes | Near | Near | Good | Fair |
| Min hardware | 80GB+ | 16--24GB | 16GB | 16--24GB | 16--24GB |
| Multi-task swap | No | Yes (adapters) | Yes | Yes | Yes |
| Inference overhead | None | None (merged) | None (merged) | +5--10% | Minimal |
| Forgetting risk | Highest | Moderate | Moderate | Low | Low |
| Domain shift handling | Best | Good (ceiling at high shift) | Good | Good | Fair |

#### Alignment Method Selection Matrix

| Factor | SFT | DPO | RLHF (PPO) | GRPO | KTO |
|--------|-----|-----|-----------|------|-----|
| Compute cost | $ | $$ | $$$$$ | $$$ | $ |
| GPU instances needed | 1 | 2 | 4 | 2--3 | 1 |
| Data requirement | Demonstrations | Preference pairs | Preference pairs + RM | Task completions | Thumbs up/down |
| Stability | High | High | Low (hyperparameter sensitive) | Medium | High |
| Quality ceiling | Good | Very good | Excellent | Excellent (reasoning) | Adequate |
| Best model range | Any | 1B--70B | 10B+ | Reasoning models | 1B--13B |

### 6.4 Model Selection Criteria for Enterprise Fine-Tuning

1. **20-question domain probe**: If base model can't answer ~50% correctly, fine-tuning will not recover it.
2. **Recommended starting range**: 7B--8B parameters. Scale up only if quality is insufficient.
3. **License fit**: Llama community license, Apache 2.0 (Qwen, Mistral), or OpenAI ToS for hosted.
4. **Fine-tuning support**: Confirm model has established fine-tuning tooling (Llama, Qwen, Mistral for open-weight).

### 6.5 Practical Hyperparameter Recommendations

- **Rank (LoRA)**: Start with r=8 targeting all linear layers. Only increase if quality plateaus with more data.
- **Optimizer**: AdamW is standard; choice of optimizer has minimal impact (Raschka 2025). SGD alone is suboptimal; SGD with scheduler is competitive.
- **Epochs**: For static datasets, **single epoch preferred**. Multi-epoch often degrades results due to overfitting.
- **Chat template**: Canonicalize system/role names, BOS/EOS tokens. Mismatch between training and inference templates is a top failure cause.
- **Dataset size**: 1,000--50,000 examples for SFT. 500--5,000 for preference tuning. Below 200 examples, catastrophic forgetting is near-certain.
- **Batch size and learning rate**: Standard transformer fine-tuning practices apply; lower learning rates (1e-5 to 5e-5) for larger models.

---

## Sources

1. [System Design Newsletter: Fine-Tuning AI Models](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)
2. [Databricks: Efficient Fine-Tuning with LoRA Guide](https://www.databricks.com/blog/efficient-fine-tuning-lora-guide-llms)
3. [Sebastian Raschka: Practical Tips for Finetuning LLMs Using LoRA](https://magazine.sebastianraschka.com/p/practical-tips-for-finetuning-llms)
4. [Mercity: In-depth Guide to Fine-Tuning LLMs with LoRA and QLoRA](https://www.mercity.ai/blog-post/guide-to-fine-tuning-llms-with-lora-and-qlora/)
5. [Introl: Fine-Tuning Infrastructure -- LoRA, QLoRA, PEFT at Scale](https://introl.com/blog/fine-tuning-infrastructure-lora-qlora-peft-scale-guide-2025)
6. [OpenAI API Pricing](https://developers.openai.com/api/docs/pricing)
7. [CloudZero: OpenAI API Pricing in 2026](https://www.cloudzero.com/blog/openai-pricing/)
8. [PricePerToken: LLM Fine-Tuning Pricing 2026](https://pricepertoken.com/fine-tuning)
9. [ExplainX: OpenAI Winds Down Fine-Tuning API](https://explainx.ai/blog/openai-gpt-55-pricing-fine-tuning-api-wind-down-2026)
10. [Anthropic Claude Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
11. [Patsnap: RLHF vs DPO in LLM Fine-Tuning -- Architecture and Tradeoffs](https://www.patsnap.com/resources/blog/articles/rlhf-vs-dpo-in-llm-fine-tuning-60-patent-analysis-2/)
12. [Meta Intelligence: LLM Alignment -- RLHF to DPO & GRPO](https://www.meta-intelligence.tech/en/insight-rlhf-alignment)
13. [Phil Schmid: How to Align Open LLMs in 2025 with DPO](https://www.philschmid.de/rl-with-llms-in-2025-dpo)
14. [Datarekha: Distributed Training -- FSDP vs DeepSpeed vs Megatron](https://datarekha.com/blog/distributed-training-fsdp-vs-deepspeed/)
15. [Spheron: Distributed LLM Training -- FSDP, DeepSpeed, Megatron Multi-Node Guide 2026](https://www.spheron.network/blog/distributed-llm-training-fsdp-deepspeed-megatron-multi-node/)
16. [HuggingFace: FSDP vs DeepSpeed](https://huggingface.co/docs/accelerate/en/concept_guides/fsdp_and_deepspeed)
17. [Cognizant: How to Prevent Catastrophic Forgetting in LLM Fine-Tuning](https://www.cognizant.com/us/en/ai-lab/blog/overcoming-forgetting-in-llm-fine-tuning)
18. [AIXplore: Catastrophic Forgetting -- When Fine-Tuning Silently Breaks](https://ai.rundatarun.io/emerging-trends/the-hidden-crisis-in-llm-fine-tuning-catastrophic-forgetting)
19. [arXiv 2504.01241: Catastrophic Forgetting in LLMs -- A Comparative Analysis](https://arxiv.org/abs/2504.01241)
20. [EMNLP 2025: Mitigating Catastrophic Forgetting in LLMs](https://aclanthology.org/2025.emnlp-main.1108.pdf)
21. [Databricks: RAG vs Fine Tuning -- Enterprise Decisions](https://www.databricks.com/blog/rag-vs-fine-tuning)
22. [Matillion: RAG vs Fine-Tuning Enterprise Strategy Guide](https://www.matillion.com/blog/rag-vs-fine-tuning-enterprise-ai-strategy-guide)
23. [Contextual AI: RAG vs Fine-Tuning for Enterprise AI](https://contextual.ai/blog/rag-vs-fine-tuning-which-approach-is-right-for-enterprise-ai)
24. [OWASP LLM04:2025 -- Data and Model Poisoning](https://genai.owasp.org/llmrisk/llm042025-data-and-model-poisoning/)
25. [Lakera: Introduction to Data Poisoning](https://www.lakera.ai/blog/training-data-poisoning)
26. [Introl: Model Versioning Infrastructure](https://introl.com/blog/model-versioning-infrastructure-mlops-artifact-management-guide-2025)
27. [Stratagem Systems: PEFT Methods Compared](https://www.stratagem-systems.com/blog/parameter-efficient-fine-tuning-methods)
