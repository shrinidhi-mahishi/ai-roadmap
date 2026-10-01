# 12. Fine-Tuning LLMs

**Sub-areas covered**: The fine-tuning pipeline (base model, dataset, training, serving), three training regimes (continued pretraining, SFT, preference tuning), parameter-efficient fine-tuning taxonomy (LoRA, QLoRA, Adapters, Prefix Tuning, Prompt Tuning, DoRA) with trainable-parameter counts and memory profiles for 7B models, full fine-tuning memory breakdown (params + gradients + optimizer states = 60-80 GB), LoRA rank/target-module analysis (r=8 on all linear layers as optimal default), QLoRA innovations (4-bit NF4, double quantization, paged optimizers) fitting 65B on a single 48 GB GPU, RLHF pipeline (four concurrent model instances, PPO instability, reward hacking) vs DPO (two instances, 10-50x cheaper, Bradley-Terry loss), emerging alignment methods (GRPO, KTO, Align-Pro, Tied Preference Optimization), GPU-hour cost tables from free Colab to 8xH100 clusters, API fine-tuning economics (OpenAI winding down May 2026, Together AI/Vertex/Fireworks alternatives), inference cost crossover at 200-500K queries/month, distributed training framework decision tree (FSDP2 for 7-30B, DeepSpeed ZeRO-3 for 30-70B with CPU/NVMe offload, Megatron-LM for >100B), checkpoint management and model versioning (MLflow 3.0, Unity Catalog), catastrophic forgetting (LoRA does NOT prevent it, ~200 example threshold, FIP/AWD/SDFT mitigations), data poisoning as concrete 2025 threat (~250 documents sufficient for backdoor, survives RLHF), LoRA adapters as attack vector (JFrog finding ~100 malicious models on HuggingFace), OWASP LLM04:2025 mapping, AIBOM and model provenance, compliance (HIPAA/GDPR/SOC2 via VPC-hosted training), RAG vs fine-tuning decision framework with industry data (70%+ enterprises use RAG, hybrid fastest-growing), ship gates (< 5% MMLU regression, >= 50% cost reduction, TTFT < 300 ms), production Python code with LoRA training pipeline using retry/backoff/circuit-breaker/structured-logging, and two enterprise system-design scenarios (domain-specific customer support, multi-tenant compliance platform) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production fine-tuning system spans five cooperating layers: a **control plane** managing experiment configuration, dataset versioning, and access control before any training job launches; a **training plane** where the actual gradient computation happens across one or more GPUs using FSDP2/DeepSpeed sharding; a **evaluation plane** gating every checkpoint against regression benchmarks and domain-specific metrics before promotion; a **model registry and persistence layer** storing immutable dataset snapshots, LoRA adapters, merged weights, and full provenance metadata; and a **serving and telemetry layer** hosting the fine-tuned model behind inference infrastructure while tracking drift, latency, and cost.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Experiment Config  │   │ Dataset Version   │   │ Access Control /      │  │
│  │ Manager            │   │ Manager           │   │ Governance Engine     │  │
│  │                    │   │                    │   │                        │  │
│  │ Hyperparams:       │   │ DVC / MLflow:      │   │ RBAC per stage:        │  │
│  │  base model ID     │   │  immutable SHA-256 │   │  dev: train + eval     │  │
│  │  LoRA rank, alpha  │   │  snapshots         │   │  staging: promote      │  │
│  │  target modules    │   │  rollback to any   │   │  prod: deploy (human   │  │
│  │  lr, batch size    │   │  prior version     │   │    gate required)      │  │
│  │  epochs (default 1)│   │  poison-scan on    │   │  archive: read-only    │  │
│  │  chat template     │   │  ingest            │   │                        │  │
│  └────────┬──────────┘   └────────┬──────────┘   └───────────┬────────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │ locked config          │ dataset ref              │ scoped perms
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                           TRAINING PLANE                                     │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Data Preprocessor │  │ Training Engine   │  │ Checkpoint Manager      │   │
│  │                   │  │                   │  │                          │   │
│  │ Tokenize, apply   │  │ HF TRL / PEFT:    │  │ SHARDED_STATE_DICT      │   │
│  │ chat template,    │  │  SFT / DPO / GRPO │  │ (FSDP2) or              │   │
│  │ validate format,  │──▶│                   │──▶│ zero_to_fp32.py         │   │
│  │ split train/val   │  │ Distributed:       │  │ (DeepSpeed)             │   │
│  │                   │  │  FSDP2 (<=30B)     │  │                          │   │
│  │ Chat template     │  │  ZeRO-3 (<=70B)   │  │ Async offload for       │   │
│  │ mismatch = top    │  │  Megatron (>100B)  │  │ spot instance safety    │   │
│  │ failure cause     │  │                   │  │                          │   │
│  └──────────────────┘  └────────┬──────────┘  └──────────┬───────────────┘   │
│                                 │ loss curve              │ checkpoints       │
│  ┌──────────────────────────────▼──────────────────────────▼───────────────┐  │
│  │           Experiment Tracker (W&B / MLflow 3.0)                         │  │
│  │  loss per step | grad norm | lr schedule | GPU util | token throughput  │  │
│  │  connects: code version, dataset version, config, eval results          │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ candidate checkpoint
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                          EVALUATION PLANE                                     │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ General Capability │  │ Domain-Specific  │  │ Safety / Red Team       │   │
│  │ Regression Gate    │  │ Eval Suite       │  │ Battery                 │   │
│  │                    │  │                   │  │                          │   │
│  │ MMLU-Pro,          │  │ Task-specific    │  │ Sensitive topics,        │   │
│  │ MT-Bench:          │  │ held-out test    │  │ unusual prompts,         │   │
│  │ regression < 5%    │  │ set: precision,  │  │ OOD inputs,              │   │
│  │ of base model      │  │ recall, format   │  │ capability regression    │   │
│  │                    │  │ compliance       │  │ on UNTRAINED tasks       │   │
│  └────────┬──────────┘  └────────┬─────────┘  └──────────┬───────────────┘   │
│           │ pass/fail            │ scores                 │ pass/fail          │
│  ┌────────▼──────────────────────▼───────────────────────▼───────────────┐   │
│  │                       Ship Gate Evaluator                              │   │
│  │  ALL three must pass:                                                  │   │
│  │   1. General regression < 5% on MMLU-Pro / MT-Bench                   │   │
│  │   2. Inference cost >= 50% below foundation-model baseline             │   │
│  │   3. TTFT < 300 ms for 7B on single GPU                               │   │
│  └────────┬──────────────────────────────────────────────────────────────┘   │
└───────────┼──────────────────────────────────────────────────────────────────┘
            │ promoted model + eval report
┌───────────▼──────────────────────────────────────────────────────────────────┐
│                   MODEL REGISTRY & PERSISTENCE LAYER                          │
│                                                                              │
│  ┌──────────────────┐  ┌───────────────────┐  ┌────────────────────────┐    │
│  │ Model Registry    │  │ Artifact Store    │  │ Provenance / AIBOM     │    │
│  │ (MLflow 3.0 /     │  │                   │  │                        │    │
│  │  Unity Catalog)   │  │ LoRA adapters     │  │ Base model ID          │    │
│  │                   │  │ Merged weights    │  │ Dataset SHA + version  │    │
│  │ Lifecycle stages: │  │ Tokenizer config  │  │ Training config        │    │
│  │  dev --> staging   │  │ Chat template     │  │ Eval results           │    │
│  │  --> prod -->      │  │ Eval reports      │  │ Approver + timestamp   │    │
│  │  archived         │  │                   │  │ Code commit hash       │    │
│  └──────────┬────────┘  └───────────────────┘  └────────────────────────┘    │
└─────────────┼────────────────────────────────────────────────────────────────┘
              │ serving config
┌─────────────▼────────────────────────────────────────────────────────────────┐
│                    SERVING & TELEMETRY LAYER                                  │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Inference Server  │  │ A/B Router       │  │ Monitoring               │   │
│  │ (vLLM / TGI)     │  │                   │  │                          │   │
│  │                   │  │ Shadow traffic:   │  │ Output quality drift     │   │
│  │ Merged LoRA:      │  │ 5% to new model, │  │ Latency p50/p95/p99      │   │
│  │  zero overhead    │  │ 95% to incumbent  │  │ Cost per query           │   │
│  │ Unmerged LoRA:    │  │ until eval parity │  │ Token throughput         │   │
│  │  hot-swap adapters│  │ confirmed         │  │ Forgetting regression    │   │
│  │  on shared base   │  │                   │  │  on non-target tasks     │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) An engineer defines an experiment in the control plane: base model ID, LoRA rank, target modules, learning rate, and dataset version. The dataset version manager pulls an immutable snapshot (SHA-256 hashed), runs a poison-scan on ingest, and locks the corpus. The governance engine assigns RBAC: training and evaluation access in dev, promotion rights in staging, deployment (with mandatory human gate) in prod. (2) The training plane preprocesses data: tokenization, chat template application (a mismatch between training and inference templates is the single most common cause of degraded post-fine-tuning performance), and train/validation split. The training engine (HF TRL/PEFT) runs SFT, DPO, or GRPO using the distributed framework matched to model size -- FSDP2 for 7-30B, DeepSpeed ZeRO-3 with CPU offload for 30-70B, Megatron-LM for >100B. The checkpoint manager writes SHARDED_STATE_DICT periodically; on spot instances, async offload prevents loss of training progress on preemption. The experiment tracker (W&B/MLflow) logs loss curves, gradient norms, GPU utilization, and token throughput, linking every run to the exact code version, dataset snapshot, and config. (3) Each candidate checkpoint enters the evaluation plane, which enforces three gates in sequence: general capability regression (< 5% drop on MMLU-Pro/MT-Bench vs. base model), domain-specific accuracy on a held-out test set, and safety/red-team validation including capability regression on tasks NOT in the training set (the primary forgetting detector). All three must pass before the ship gate evaluator promotes the model. (4) Promoted models enter the persistence layer: the model registry (MLflow 3.0 / Unity Catalog) tracks lifecycle stage (dev/staging/prod/archived), while the artifact store holds LoRA adapters, merged weights, tokenizer config, and chat templates. The AIBOM (AI Bill of Materials) captures full provenance: base model, dataset hash, training config, eval results, approver identity, and code commit hash. (5) The serving layer deploys via vLLM or TGI. Merged LoRA adapters incur zero inference overhead; unmerged adapters enable hot-swapping on a shared backbone for multi-tenant scenarios. An A/B router sends 5% shadow traffic to the new model until eval parity is confirmed. (6) The telemetry layer continuously monitors output quality drift, latency, cost per query, and -- critically -- forgetting regression on non-target tasks, the failure mode unique to fine-tuned models.

---

## 2. Core Mechanics & Algorithms

### 2.1 The Core Principle

"Fine-tune for behavior, retrieve for knowledge." A reasoning pattern repeated across thousands of examples persists in weights; a fact appearing once or twice will not. This single heuristic determines when fine-tuning is the right tool vs. RAG.

### 2.2 Three Training Regimes

```
┌────────────────────┬──────────────────────┬───────────────────┬──────────────────────────┐
│ Regime             │ Data Type            │ Dataset Size      │ When to Use              │
├────────────────────┼──────────────────────┼───────────────────┼──────────────────────────┤
│ Continued          │ Raw unlabeled        │ 100M-1B+ words    │ Base model barely        │
│ Pretraining        │ domain text          │                   │ understands domain       │
│                    │                      │                   │ (BloombergGPT, Med-PaLM) │
├────────────────────┼──────────────────────┼───────────────────┼──────────────────────────┤
│ Supervised         │ Instruction-response │ 1,000-50,000      │ Right starting point     │
│ Fine-Tuning (SFT)  │ pairs                │ pairs             │ for almost every project │
│                    │                      │                   │ Style, format, reasoning │
├────────────────────┼──────────────────────┼───────────────────┼──────────────────────────┤
│ Preference Tuning  │ Comparison pairs     │ 500-5,000 pairs   │ Calibrated refusals,     │
│ (RLHF / DPO)      │ (chosen vs rejected) │                   │ consistent tone, formats │
│                    │                      │                   │ SFT cannot reliably teach│
└────────────────────┴──────────────────────┴───────────────────┴──────────────────────────┘
```

**Practical order**: Start with SFT, ship, identify remaining issues, then add DPO. Preference tuning fixes the long tail of behaviors that demonstrations alone cannot encode.

### 2.3 Parameter-Efficient Fine-Tuning (PEFT) Methods

#### Full Fine-Tuning

Updates all model parameters. For a 7B model: parameters = 14 GB (fp16), gradients = 14 GB, optimizer states (Adam: first moment + second moment + master weights) = 32 GB. **Total GPU memory: 60-80 GB.** Strongest on hard tasks (math, code) but highest cost and highest catastrophic forgetting risk.

#### LoRA (Low-Rank Adaptation)

Freezes base weights W entirely. Injects trainable low-rank matrices A (d x r) and B (r x k) such that the effective weight becomes W' = W + AB^T.

```
┌───────────────────────────────────────────────────────────┐
│                    LoRA Forward Pass                       │
│                                                           │
│   Input x ──┬──────────────────────────────────┐          │
│             │                                  │          │
│             ▼                                  ▼          │
│   ┌───────────────┐                 ┌───────────────┐    │
│   │ W (frozen)     │                 │ A (d x r)     │    │
│   │ d x k          │                 │ trainable     │    │
│   │ full-precision │                 └───────┬───────┘    │
│   └───────┬───────┘                         │            │
│           │                                  ▼            │
│           │                         ┌───────────────┐    │
│           │                         │ B (r x k)     │    │
│           │                         │ trainable     │    │
│           │                         └───────┬───────┘    │
│           │                                  │            │
│           ▼              +                   ▼            │
│   ┌───────────────────────────────────────────────┐      │
│   │          Output = Wx + ABx                     │      │
│   └───────────────────────────────────────────────┘      │
│                                                           │
│   Trainable params: 2 x d x r  (r << d)                  │
│   Rank r=8 or 16: trains < 1% of full parameters         │
│   Merged at deploy: W_deploy = W + AB^T, zero overhead   │
└───────────────────────────────────────────────────────────┘
```

**Critical insight on target modules**: Targeting all linear layers (q_proj, k_proj, v_proj, o_proj, gate_proj, down_proj, up_proj, lm_head) matters more than increasing rank. Databricks study on OpenLLaMA-3b showed r=8 vs r=16 on attention blocks alone produced no quality improvement. The biggest quality lever is breadth of layers targeted, not rank depth.

**Inference**: Merged LoRA has zero overhead (W + AB^T fused once). Unmerged enables hot-swapping adapters on a shared backbone, at slight latency cost.

#### QLoRA (Quantized LoRA)

Quantizes the frozen base weights W to 4-bit NF4 (Normal Float, information-theoretically optimal for normally distributed weights). LoRA matrices A, B remain in bf16/fp16. Three innovations:

1. **4-bit NF4 quantization**: Maps each weight to one of 16 levels optimized for the Gaussian distribution of pretrained weights.
2. **Double Quantization**: Quantizes the quantization constants themselves, saving ~0.5 bits/param.
3. **Paged Optimizers**: Optimizer states page to CPU RAM during GPU memory spikes, preventing OOM.

**Impact**: A 65B model that previously required 8x A100 GPUs fits on a **single 48 GB A6000**. A 7B model runs on a free Google Colab T4 (16 GB). Trade-off: 33% memory savings at cost of 39% runtime increase vs. standard LoRA. Quality: negligible degradation -- 92% of full fine-tuning performance with 70% less VRAM.

#### DoRA (Weight-Decomposed Low-Rank Adaptation)

Decomposes weight matrix into magnitude ||W|| and direction W/||W|| components. Applies LoRA only to the direction component; magnitude is learned independently as a scalar. This closes the quality gap with full fine-tuning on tasks requiring simultaneous adjustment of both weight magnitude and direction -- a limitation of standard LoRA.

#### PEFT Complexity Analysis

```
┌────────────────┬──────────────────┬────────────┬──────────────┬────────────────┐
│ Method         │ Trainable Params │ Memory     │ Inference    │ Quality vs     │
│                │ (7B model)       │ (7B)       │ Overhead     │ Full FT        │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ Full FT        │ 7B (100%)        │ 60-80 GB   │ None         │ Baseline       │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ LoRA r=16      │ ~16.8M (0.24%)   │ 14-16 GB   │ None (merged)│ 90-95%         │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ QLoRA r=16     │ ~16.8M (0.24%)   │ ~10 GB     │ None (merged)│ ~90-95%        │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ Adapters       │ ~33.6M (0.48%)   │ 14-16 GB   │ +5-10% lat   │ ~90%           │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ Prefix Tuning  │ ~5.2M (0.075%)   │ 14-16 GB   │ Minimal      │ ~85-90%        │
├────────────────┼──────────────────┼────────────┼──────────────┼────────────────┤
│ Prompt Tuning  │ ~0.4M (0.006%)   │ 14-16 GB   │ Minimal      │ ~80-85%        │
└────────────────┴──────────────────┴────────────┴──────────────┴────────────────┘
```

**Key invariant**: Memory for PEFT methods (except QLoRA) is dominated by the base model in fp16 (14 GB for 7B), not the trainable parameters. QLoRA's 4-bit quantization reduces the base model footprint to ~3.5 GB.

### 2.4 RLHF: Reinforcement Learning from Human Feedback

**Pipeline**: SFT on demonstrations --> Train reward model from human preference rankings --> RL (PPO) to optimize policy against reward model.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        RLHF Training Infrastructure                          │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Actor (Policy)    │  │ Critic Model     │  │ Reward Model             │   │
│  │ Model             │  │                   │  │                          │   │
│  │ The model being   │  │ Estimates value   │  │ Trained on human         │   │
│  │ trained. Generates│  │ function V(s)     │  │ preference rankings.     │   │
│  │ responses, receives│ │ for PPO advantage │  │ Scores (prompt,response) │   │
│  │ PPO gradient      │  │ computation       │  │ pairs. Frozen during RL. │   │
│  │ updates.          │  │                   │  │                          │   │
│  └────────┬──────────┘  └────────┬─────────┘  └──────────┬───────────────┘   │
│           │                      │                        │                   │
│  ┌────────▼──────────────────────▼────────────────────────▼───────────────┐   │
│  │                         PPO Training Loop                               │   │
│  │                                                                         │   │
│  │  1. Actor generates response to prompt                                  │   │
│  │  2. Reward model scores the response                                    │   │
│  │  3. Critic estimates baseline value                                     │   │
│  │  4. Advantage = reward - baseline                                       │   │
│  │  5. PPO clips gradient to prevent destructive updates                   │   │
│  │  6. KL penalty vs reference model prevents reward hacking               │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Reference Model (frozen copy of initial SFT model)                   │    │
│  │ Anchors KL divergence: prevents actor from drifting into degenerate  │    │
│  │ high-reward but low-quality outputs (reward hacking)                 │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Total GPU instances required: 4 (actor + critic + reward + reference)       │
│  Cost: 10-50x more expensive than DPO                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Failure modes**: (1) PPO hyperparameter sensitivity -- small changes to the KL divergence coefficient produce dramatic performance swings. (2) Reward hacking -- the actor learns to exploit the reward model's blind spots rather than genuinely improving. (3) Training instability -- four concurrent models with interdependent gradients create oscillation.

### 2.5 DPO: Direct Preference Optimization

Reframes preference learning as binary classification. The loss function is derived from the Bradley-Terry preference model:

```
L_DPO = -log sigma(beta * (log pi(y_w|x)/pi_ref(y_w|x) - log pi(y_l|x)/pi_ref(y_l|x)))

where:
  y_w = chosen (preferred) response
  y_l = rejected response
  pi  = policy model being trained
  pi_ref = frozen reference SFT model
  beta = temperature controlling deviation from reference
```

**Infrastructure**: Two model instances (policy + reference), not four. Eliminates reward model and RL loop entirely. Matches or exceeds RLHF on summarization, helpfulness, and factuality benchmarks. 10-50x cheaper.

**Weakness**: Susceptible to overfitting on limited preference datasets, reducing generalization. Offline nature requires upfront human annotation. For complex tasks requiring broad generalization, RL-based training remains superior.

### 2.6 Alignment Method Decision Matrix

```
┌──────────────────┬───────┬────────┬────────────┬────────┬────────┐
│ Factor           │ SFT   │ DPO    │ RLHF (PPO) │ GRPO   │ KTO    │
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ Compute cost     │ $     │ $$     │ $$$$$      │ $$$    │ $      │
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ GPU instances    │ 1     │ 2      │ 4          │ 2-3    │ 1      │
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ Data requirement │ Demos │ Pref   │ Pref pairs │ Task   │ Thumbs │
│                  │       │ pairs  │ + RM data  │ compl. │ up/down│
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ Stability        │ High  │ High   │ Low        │ Medium │ High   │
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ Quality ceiling  │ Good  │ V.Good │ Excellent  │ Excl.  │ Adeq.  │
│                  │       │        │            │(reason)│        │
├──────────────────┼───────┼────────┼────────────┼────────┼────────┤
│ Best model range │ Any   │ 1B-70B │ 10B+       │ Reason.│ 1B-13B │
└──────────────────┴───────┴────────┴────────────┴────────┴────────┘
```

**GRPO (Group Relative Policy Optimization)**: Used in DeepSeek-R1. Pure RL for reasoning -- groups completions and uses relative ranking within each group as the reward signal, eliminating the separate reward model. Excellent for math and code reasoning.

**KTO**: Requires only thumbs-up/thumbs-down signals (no paired comparisons). Cheapest alignment method. Adequate quality for 1B-13B models.

### 2.7 Catastrophic Forgetting

Fine-tuning on a new task degrades performance on tasks the model previously handled. One of the most disorienting production failures because it manifests as regression in workflows nobody touched.

**Key findings (2025-2026)**:

- LoRA does NOT prevent forgetting. Despite minimal parameter changes, catastrophic forgetting occurs in continual learning. The issue is which network paths are altered, not how many parameters change.
- Below ~200 training examples, catastrophic forgetting is near-certain.
- Larger models generally forget less (overcapacity), but no model is immune.

**Mitigation landscape**:

```
┌──────────────────────────┬───────────────────────────────────────┬────────────────┐
│ Technique                │ Mechanism                             │ Status         │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Functionally Invariant   │ Considers geometry of loss landscape  │ Research, 2025 │
│ Paths (FIP)              │ rather than parameter magnitude       │ (Caltech)      │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Anchored Weight Decay    │ Constrains drift in ES update rule    │ Prod-ready,    │
│ (AWD)                    │                                       │ 2025 (Cogniz.) │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Self-Distillation FT     │ Model distills own knowledge into     │ Research, 2025 │
│ (SDFT)                   │ new learning signal                   │ (MIT/ETH)      │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Selective Token Masking  │ Masks high-perplexity tokens (core    │ Research, 2025 │
│ (STM)                    │ general knowledge) during fine-tuning │                │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Modular LoRA adapters    │ Separate adapter per domain; update   │ Production     │
│                          │ one without touching others           │ standard       │
├──────────────────────────┼───────────────────────────────────────┼────────────────┤
│ Context-window continual │ Put knowledge in context window       │ Production     │
│ learning                 │ instead of weight updates             │ alternative    │
└──────────────────────────┴───────────────────────────────────────┴────────────────┘
```

### 2.8 Hyperparameter Recommendations

- **LoRA rank**: Start r=8, target all linear layers. Increase rank only if quality plateaus with more data.
- **Optimizer**: AdamW standard; optimizer choice has minimal impact (Raschka 2025).
- **Epochs**: Single epoch preferred for static datasets. Multi-epoch often degrades results due to overfitting.
- **Chat template**: Canonicalize system/role names, BOS/EOS tokens. Mismatch between training and inference is a top failure cause.
- **Dataset size**: 1,000-50,000 for SFT. 500-5,000 for preference tuning. Below 200, catastrophic forgetting near-certain.
- **Learning rate**: 1e-5 to 5e-5 for larger models. Standard transformer fine-tuning schedule.

---

## 3. Token Economics & NFR Analysis

### 3.1 GPU-Hours and Training Costs by Model Size

```
┌───────────┬───────────────────┬──────────────────┬───────────────┬──────────────┐
│ Model     │ Method            │ Hardware         │ Training Time │ Approx. Cost │
│ Size      │                   │                  │               │              │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 7B        │ QLoRA r=8         │ 1x T4 16GB       │ 2-4 hours     │ $0-10        │
│           │                   │ (Colab free)     │               │              │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 7B        │ LoRA r=8          │ 1x A100 80GB     │ ~15 min       │ $5-15        │
│           │ all linear layers │                  │ (5K examples) │              │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 7B        │ Full fine-tuning  │ 1x A100 80GB     │ 1-2 hours     │ $30-50       │
│           │                   │                  │ (5K examples) │              │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 13B       │ QLoRA             │ 1x A6000 48GB    │ 4-8 hours     │ $20-50       │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 70B       │ QLoRA             │ 1x A6000 48GB    │ 24-48 hours   │ $100-300     │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 70B       │ LoRA + FSDP       │ 8x H100 SXM5    │ ~3 hours      │ $500-1,000   │
│           │                   │                  │ (50K examples)│              │
├───────────┼───────────────────┼──────────────────┼───────────────┼──────────────┤
│ 70B       │ Full fine-tuning  │ 8x H100          │ Days          │ $5,000-      │
│           │                   │ (640GB total)    │               │ $35,000+     │
└───────────┴───────────────────┴──────────────────┴───────────────┴──────────────┘
```

**Throughput benchmark**: 8x H100 SXM5 with FSDP2 and flash attention delivers ~8,500-9,400 tokens/sec aggregate. A 100M token dataset finishes in ~3 hours.

### 3.2 API Fine-Tuning Costs (as of Sept 2026)

**OpenAI** is winding down its fine-tuning platform (May 2026). No new users accepted. Existing users can fine-tune GPT-4.1, GPT-4.1-mini (SFT/DPO), and o4-mini (RFT). GPT-5.x and GPT-6 are NOT available for fine-tuning. Additional $0.50/hour training compute charge. Fine-tuned inference costs 50-100% premium over base rates.

```
┌──────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Model        │ Training         │ Inference Input  │ Inference Output │
│              │ ($/1M tokens)    │ ($/1M tokens)    │ ($/1M tokens)    │
├──────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4.1      │ ~$3.00           │ ~$3.00           │ ~$12.00          │
├──────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4.1 Mini │ ~$0.80           │ ~$0.80           │ ~$3.20           │
├──────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4o       │ ~$25.00          │ ~$30.00          │ ~$60.00          │
└──────────────┴──────────────────┴──────────────────┴──────────────────┘
```

**Anthropic** does not offer public self-serve fine-tuning as of September 2026. Custom fine-tuning available through enterprise sales. Anthropic's approach emphasizes prompt engineering, prompt caching, and tools over fine-tuning.

**Post-OpenAI alternatives**:

```
┌──────────────┬──────────────────────────┬────────────────────────────────────┐
│ Provider     │ Training Cost            │ Notes                              │
│              │ ($/1M tokens)            │                                    │
├──────────────┼──────────────────────────┼────────────────────────────────────┤
│ Together AI  │ ~$0.48 (LoRA, 7B OSS)    │ Best budget. Llama, Mistral.       │
│              │                          │ Serverless inference included.     │
├──────────────┼──────────────────────────┼────────────────────────────────────┤
│ Google       │ Competitive (Gemini 2.0  │ No inference cost increase for     │
│ Vertex AI    │ Flash)                   │ tuned models.                      │
├──────────────┼──────────────────────────┼────────────────────────────────────┤
│ Fireworks    │ ~2x SFT price for DPO    │ Strong DPO support. Competitive    │
│              │                          │ on open-source models.             │
└──────────────┴──────────────────────────┴────────────────────────────────────┘
```

### 3.3 Inference Cost Impact

- **Cost crossover point**: Above 200K-500K queries/month, self-hosted fine-tuned model becomes cheaper than API calls.
- Fine-tuned 7B on single GPU: TTFT < 300 ms.
- Fine-tuned smaller model replacing larger base model: 50%+ inference cost reduction is a realistic target.
- Batch API (OpenAI, Anthropic): 50% discount with 24-hour processing window.
- Prompt caching (Anthropic): cache hits cost 0.1x standard input rate.

### 3.4 Inference Cost Formula: Base vs Fine-Tuned

The consolidated cost-per-1K-inference-runs formula makes the economic case for fine-tuning concrete and auditable:

```
Cost_per_1K_inference_runs = 1000 * (input_tokens + output_tokens) * price_per_token
```

**Worked example** (customer support task, ~500 input tokens + ~300 output tokens per run):

```
┌──────────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Model                │ $/1M input tok   │ $/1M output tok  │ Cost per 1K runs │
├──────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4.1 (API)        │ $2.00            │ $8.00            │ $3.40            │
│                      │                  │                  │ (1K * (500*$2 +  │
│                      │                  │                  │  300*$8) / 1M)   │
├──────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4.1 fine-tuned   │ $3.00            │ $12.00           │ $5.10            │
│ (API, 50-100%        │                  │                  │ Higher per-query  │
│  inference premium)  │                  │                  │ but shorter output│
├──────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ GPT-4.1 Mini (API)   │ $0.40            │ $1.60            │ $0.68            │
├──────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Self-hosted FT 7B    │ ~$0.05*          │ ~$0.05*          │ $0.04            │
│ (1x A100 lease)      │                  │                  │ 85x cheaper than │
│                      │                  │                  │ GPT-4.1 API      │
├──────────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Together AI FT 7B    │ ~$0.20           │ ~$0.20           │ $0.16            │
│ (serverless)         │                  │                  │                  │
└──────────────────────┴──────────────────┴──────────────────┴──────────────────┘

* Self-hosted amortized: $2/hr A100 lease, ~3,500 requests/hr at 800 tok/req
  = ~$0.0006/request = $0.57/1K runs (GPU amortization only, no per-token charge)
  Effective per-token rate shown for formula consistency.
```

**Key insight**: The fine-tuned smaller model wins on cost not just through cheaper per-token rates but through shorter outputs. A fine-tuned 7B trained via DPO to produce concise responses generates 30-50% fewer output tokens than a prompted large model, compounding the savings. At 500K queries/month, the self-hosted fine-tuned 7B costs ~$285/month vs ~$1,700/month for GPT-4.1 API.

### 3.5 NFR Targets

```
┌──────────────────────┬──────────────────────────────────────────────────┐
│ NFR                  │ Target                                           │
├──────────────────────┼──────────────────────────────────────────────────┤
│ TTFT (7B, 1 GPU)     │ < 300 ms                                        │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Inference latency    │ Fine-tuned 7B on single A100 (merged LoRA):     │
│ (end-to-end,         │   p50: < 180 ms                                 │
│  fine-tuned model)   │   p95: < 350 ms                                 │
│                      │   p99: < 500 ms                                 │
│                      │ Fine-tuned 70B on 4x H100 (merged LoRA):        │
│                      │   p50: < 600 ms                                 │
│                      │   p95: < 1,200 ms                               │
│                      │   p99: < 1,800 ms                               │
│                      │ Measured: prompt encoding + first-token + full   │
│                      │ generation for typical 300-token output. Tail    │
│                      │ latency (p99) driven by KV cache pressure under  │
│                      │ concurrent load and long-context inputs.         │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Training throughput   │ 8,500-9,400 tok/sec (8x H100 FSDP2)            │
│ (70B LoRA cluster)   │                                                  │
├──────────────────────┼──────────────────────────────────────────────────┤
│ General capability   │ < 5% regression on MMLU-Pro / MT-Bench vs base  │
│ regression           │                                                  │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Inference cost vs    │ >= 50% reduction over foundation-model baseline  │
│ base model           │                                                  │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Checkpoint recovery  │ Resume from last checkpoint within minutes       │
│ (spot preemption)    │ (async offload mandatory)                        │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Model promotion      │ All three eval gates pass (general, domain,      │
│ criteria             │ safety) before any stage transition               │
├──────────────────────┼──────────────────────────────────────────────────┤
│ Data poisoning scan  │ Automated scan on ingest; immutable dataset      │
│ latency              │ snapshots with rollback capability                │
└──────────────────────┴──────────────────────────────────────────────────┘
```

### 3.6 RAG vs Fine-Tuning Decision Framework

```
┌──────────────────────┬───────────┬───────────┬─────────────┬──────────┐
│ Factor               │ Prompting │ RAG       │ Fine-Tuning │ Hybrid   │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Time to deploy       │ Hours     │ 1-2 weeks │ 2-6 months  │ 3-6 mo   │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Data freshness       │ N/A       │ Real-time │ Frozen at   │ Mixed    │
│                      │           │           │ training    │          │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Source attribution   │ No        │ Yes       │ No          │ Partial  │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Behavior consistency │ Low       │ Low       │ High        │ High     │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Latency              │ Lowest    │ +500ms-2s │ Low         │ Medium   │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Cost at scale        │ High      │ Medium    │ Low (small  │ Medium   │
│ (>500K queries/mo)   │ (large)   │           │ tuned model)│          │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Data privacy         │ API-dep.  │ Control.  │ Full (self- │ Mixed    │
│                      │           │           │ hosted)     │          │
├──────────────────────┼───────────┼───────────┼─────────────┼──────────┤
│ Maintenance burden   │ Low       │ Medium    │ High        │ Highest  │
└──────────────────────┴───────────┴───────────┴─────────────┴──────────┘
```

**Industry data (2025)**: 70%+ of enterprise AI teams use RAG as primary knowledge grounding (Gartner). <25% use fine-tuning standalone. Hybrid implementations are the fastest-growing segment. 50%+ of enterprise GenAI models will be domain-specific by 2027, up from ~1% in 2024.

---

## 4. Distributed Resilience & Security

### 4.1 Distributed Training Framework Decision Tree

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Framework Selection                                │
│                                                                      │
│   Model size?                                                        │
│       │                                                              │
│       ├── <= 30B ──── FSDP2 (PyTorch native)                        │
│       │                 Per-parameter sharding via DTensor            │
│       │                 torch.compile compatible                     │
│       │                 Mix-and-match dtype per layer                │
│       │                 Freeze individual params for LoRA            │
│       │                 SHARDED_STATE_DICT for checkpoints           │
│       │                 Communication: prefetch next shard during    │
│       │                   compute (NCCL all-gather overlap)          │
│       │                                                              │
│       ├── 30B-70B ── DeepSpeed ZeRO-3                               │
│       │                 CPU offload (unique feature):                │
│       │                   optimizer states live in CPU RAM            │
│       │                 NVMe offload for even larger models          │
│       │                 Cost: ~halves training speed                  │
│       │                 Hierarchical sharding: intra-node shard,     │
│       │                   inter-node replicate                       │
│       │                                                              │
│       └── > 100B ─── Megatron-LM (NVIDIA)                          │
│                         3D parallelism (tensor + pipeline + data)    │
│                         Used by Llama 3, Mistral, DeepSeek           │
│                         Below 70B you do not need it                 │
└──────────────────────────────────────────────────────────────────────┘
```

**Memory arithmetic for 70B in fp16**: Parameters = 140 GB, gradients = 140 GB, Adam states (2x params for moments + master weights) = 560 GB. **Total: 840 GB static state.** On 8x H100s (640 GB total GPU memory), this does not fit without ZeRO-3 CPU offload or 3D parallelism.

### 4.2 Checkpoint Management

- **FSDP2**: `SHARDED_STATE_DICT` for fast save/restore. Each rank writes its own shard; no costly all-gather for checkpoint.
- **DeepSpeed**: Post-convert sharded checkpoints with `zero_to_fp32.py`.
- **Spot instance safety**: Async checkpoint offload + automated restart on preemption. Without this, a 48-hour QLoRA run on a spot A6000 is a gamble.
- **What must be versioned**: base model ID, LoRA adapters / merged weights, prompt templates, retrieval configurations, training hyperparameters, dataset version (SHA), eval results.
- **Tools**: MLflow 3.0 (extended model registry for GenAI), Weights & Biases, DVC, Databricks Unity Catalog.

### 4.3 Data Poisoning: A Concrete 2025 Threat

Data poisoning -- the insertion of malicious data into training inputs to alter model behavior at inference time -- became a concrete (not theoretical) threat in 2025.

**Incidents**:
- **Jan 2025**: Hidden prompts in GitHub code comments poisoned a fine-tuned model. DeepSeek's DeepThink-R1, trained on contaminated repos, learned a persistent backdoor.
- **Grok 4 incident**: Typing `!Pliny` stripped all guardrails -- likely caused by training data saturated with jailbreak prompts from X.
- **Scale**: ~250 poisoned documents sufficient to implant a backdoor regardless of model size (13B poisoned by same count as 600M).
- **Persistence**: Backdoors survive SFT, RLHF, and adversarial training. Larger models show increased persistence.

**LoRA adapters as attack vector**: Small adapter files (~tens of MB) are perceived as low-risk but have full access to modify behavior. In Feb 2024, JFrog found ~100 malicious models on Hugging Face that executed arbitrary code on load. OWASP LLM Top 10 (2025) lists data and model poisoning as **LLM04:2025**.

### 4.4 Model Provenance and Compliance

- **AIBOM (AI Bill of Materials)**: Tracks model origin, training data used, modification history. Tools: OWASP CycloneDX, ML-BOM.
- **Dataset governance**: Restrict write access, enforce approval for corpus changes, verify lineage before training, store immutable dataset versions.
- **Compliance**: Fine-tuning addresses HIPAA, GDPR, SOC 2, attorney-client privilege by keeping model, data, and inference within your VPC. RBAC + audit logs + lineage tracking at each stage transition (dev --> staging --> prod --> archive).

### 4.5 Defense Recommendations

```
┌─────┬──────────────────────────┬───────────────────────────────────────────┐
│  #  │ Defense                  │ Implementation                            │
├─────┼──────────────────────────┼───────────────────────────────────────────┤
│  1  │ Data provenance          │ Verify origin and transformation history  │
│     │                          │ of ALL training data before ingestion     │
├─────┼──────────────────────────┼───────────────────────────────────────────┤
│  2  │ Dataset versioning       │ Immutable snapshots; rollback to known-   │
│     │ with rollback            │ good slices without reintroducing poison  │
├─────┼──────────────────────────┼───────────────────────────────────────────┤
│  3  │ Red teaming              │ Test with sensitive topics, unusual       │
│     │                          │ prompts, OOD inputs after every FT run   │
├─────┼──────────────────────────┼───────────────────────────────────────────┤
│  4  │ Runtime guardrails       │ Defense-in-depth: provenance + red team   │
│     │                          │ + runtime monitoring (not one layer)      │
├─────┼──────────────────────────┼───────────────────────────────────────────┤
│  5  │ Behavioral testing on    │ Test on capabilities NOT in training set  │
│     │ untrained capabilities   │ to detect forgetting and regressions     │
└─────┴──────────────────────────┴───────────────────────────────────────────┘
```

### 4.6 Fault Tolerance and Reproducibility

- Both FSDP2 and DeepSpeed support periodic checkpoint saves.
- **Reproducibility requirements**: Locked dataset (immutable snapshot), locked random seeds, versioned model card.
- **Lifecycle stages**: development --> staging --> production --> archived, with governance controls and audit logs at each transition.

### 4.7 Zero-Trust MCP for Fine-Tuning Workflows

When MCP (Model Context Protocol) tools orchestrate fine-tuning operations -- submitting training jobs, monitoring runs, canceling jobs, deploying adapters, accessing training data -- each tool invocation must be treated as an untrusted boundary crossing. Fine-tuning workflows are high-privilege (they can alter model behavior permanently), making zero-trust architecture mandatory rather than optional.

**Per-invocation authentication and capability scoping**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                   Zero-Trust MCP for Fine-Tuning Ops                         │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ MCP Tool: training_job.submit                                          │  │
│  │   Auth: per-invocation JWT with short TTL (5 min)                      │  │
│  │   Scoped capabilities:                                                 │  │
│  │     - model: [specific base model IDs only]                            │  │
│  │     - max_gpu_hours: 48 (prevents runaway jobs)                        │  │
│  │     - dataset: [approved dataset versions only, read-only access]      │  │
│  │     - output_bucket: [team-scoped storage path]                        │  │
│  │   Denied by default: delete dataset, modify base model, access         │  │
│  │     other teams' adapters, override eval gates                         │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ MCP Tool: training_job.monitor                                         │  │
│  │   Auth: read-only token scoped to job_id owned by caller               │  │
│  │   Returns: loss curve, GPU utilization, ETA -- no access to training   │  │
│  │     data contents or model weights during training                     │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ MCP Tool: training_job.cancel                                          │  │
│  │   Auth: elevated token requiring MFA or team-lead approval             │  │
│  │   Scoped: can only cancel jobs submitted by same user/team             │  │
│  │   Audit: cancellation logged with reason, caller identity, timestamp   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ MCP Tool: adapter.deploy                                               │  │
│  │   Auth: requires both ML engineer token AND deployment approver token  │  │
│  │   Pre-conditions enforced:                                             │  │
│  │     - All three eval gates passed (general, domain, safety)            │  │
│  │     - AIBOM generated and attached                                     │  │
│  │     - Human gate approval recorded in audit log                        │  │
│  │   Scoped: deploy only to staging first; prod requires separate call    │  │
│  │     with prod-scoped credentials                                       │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ MCP Resource: training_data                                            │  │
│  │   Access: read-only (write requires dataset version manager API,       │  │
│  │     not MCP)                                                           │  │
│  │   Scoping: per-dataset-version, per-team ACL                           │  │
│  │   No bulk export: streaming access only, no download-all capability    │  │
│  │   Audit: every read logged with caller, timestamp, rows accessed       │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  Principle: every MCP tool call is stateless and re-authenticated.           │
│  No ambient authority. A valid submit token cannot monitor or cancel.         │
│  Capability leaks (e.g., submit token reused for deploy) are rejected.       │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Why fine-tuning MCP tools require stricter scoping than inference MCP tools**: A compromised inference tool can produce bad outputs for one session. A compromised training tool can permanently alter model behavior across all users, inject backdoors that survive deployment, or exfiltrate training data containing proprietary examples. The blast radius is categorically larger, so the auth boundary must be correspondingly tighter.

### 4.8 PII Filtering in Training Data

Training data for fine-tuning frequently contains PII -- customer names, emails, phone numbers, addresses, account numbers, and health identifiers embedded in support tickets, compliance documents, or internal communications used as SFT examples. PII in training data creates two distinct risks: (1) the model memorizes and regurgitates PII at inference time, and (2) GDPR/CCPA right-to-erasure requests cannot be honored because PII is baked into model weights (the "unlearning problem").

**Detection pipeline (applied before training data ingestion)**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    PII Filtering Pipeline                                     │
│                                                                              │
│  Raw Training Data (SFT pairs, preference pairs, pretraining corpus)         │
│       │                                                                      │
│       ▼                                                                      │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Stage 1: Regex-Based Detection (fast, high recall, lower precision)    │  │
│  │   - Email addresses, phone numbers, SSNs, credit card numbers          │  │
│  │   - IP addresses, dates of birth, postal codes                         │  │
│  │   - Known ID formats (passport, driver's license patterns by country)  │  │
│  │   Throughput: ~50,000 examples/min on single CPU                       │  │
│  └──────────────────────────┬─────────────────────────────────────────────┘  │
│                             │ flagged + unflagged                             │
│                             ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Stage 2: NER Model Detection (higher precision on names, addresses)    │  │
│  │   - Transformer-based NER (e.g., spaCy, Presidio, Flair)              │  │
│  │   - Entity types: PERSON, ORG (when linked to individuals),            │  │
│  │     GPE (geo), DATE, MEDICAL_ID, FINANCIAL_ID                          │  │
│  │   - Catches PII that regex misses: names, free-text addresses,         │  │
│  │     medical conditions in narrative text                               │  │
│  │   Throughput: ~5,000 examples/min on single GPU                        │  │
│  └──────────────────────────┬─────────────────────────────────────────────┘  │
│                             │ all detections merged                           │
│                             ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Stage 3: Classifier Confirmation (reduces false positives)             │  │
│  │   - Lightweight classifier trained on domain-specific PII examples     │  │
│  │   - Confirms whether NER-flagged entities are truly PII in context     │  │
│  │     (e.g., "Apple" as company vs person name)                          │  │
│  │   - Outputs confidence score per detection                             │  │
│  └──────────────────────────┬─────────────────────────────────────────────┘  │
│                             │ confirmed PII detections                        │
│                             ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Stage 4: Action Strategy (configurable per entity type)                │  │
│  │                                                                        │  │
│  │   Strategy A - Redact: Replace with type tag                           │  │
│  │     "John Smith called about..." --> "[PERSON] called about..."        │  │
│  │     Best for: names, addresses. Preserves sentence structure.          │  │
│  │                                                                        │  │
│  │   Strategy B - Mask: Replace with realistic synthetic data             │  │
│  │     "john.smith@acme.com" --> "user_7f3a@example.com"                  │  │
│  │     Best for: emails, phone numbers. Preserves format for the model    │  │
│  │     to learn patterns (e.g., "email the customer at <email>").         │  │
│  │                                                                        │  │
│  │   Strategy C - Exclude: Drop entire example from training set          │  │
│  │     Best for: examples with dense PII (>5 entities) where              │  │
│  │     redaction would destroy training signal quality.                    │  │
│  │                                                                        │  │
│  │   Default policy: Redact names/addresses, Mask structured IDs,         │  │
│  │   Exclude examples with >5 PII entities or medical/financial IDs       │  │
│  │   that cannot be safely anonymized.                                    │  │
│  └──────────────────────────┬─────────────────────────────────────────────┘  │
│                             │ cleaned training data                           │
│                             ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ Audit Trail                                                            │  │
│  │   - Per-example log: original hash, PII entities detected, action      │  │
│  │     taken (redact/mask/exclude), confidence score, pipeline version    │  │
│  │   - Aggregated report: total examples processed, PII hit rate,         │  │
│  │     examples excluded, entity type distribution                        │  │
│  │   - Immutable audit log stored alongside dataset version in DVC/MLflow │  │
│  │   - Enables GDPR Art. 17 response: "PII from subject X was detected   │  │
│  │     in N examples, redacted/excluded before training, never entered    │  │
│  │     model weights"                                                     │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**GDPR implications for model memorization**: Under GDPR Article 17 (right to erasure), if PII enters model weights through training data, erasure requires either full model retraining without that data or approximate unlearning techniques (which remain unreliable as of 2026). Prevention at the pipeline level -- ensuring PII never enters training data -- is categorically simpler and more defensible than post-hoc unlearning. The audit trail described above provides the documented evidence that PII was handled before ingestion, which is the standard regulators currently accept.

**Integration with the training pipeline**: The PII filtering pipeline runs as a mandatory stage in the Data Preprocessor (Training Plane, Section 1). No training job can proceed without a signed PII-scan completion certificate attached to the dataset version. This is enforced by the same governance engine that enforces poison-scan on ingest -- the dataset version manager rejects any corpus that lacks both a poison-scan and PII-scan attestation.

---

## 5. Production Enterprise Code

### 5.1 LoRA Fine-Tuning Pipeline with Resilient Training

```python
"""
Production LoRA fine-tuning pipeline with structured logging, retry logic,
circuit breaker for API-dependent data loading, and checkpoint recovery.
"""

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
)
from trl import SFTTrainer

# ── Structured Logging ──────────────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def log_with_fields(logger: logging.Logger, level: int, msg: str, **fields):
    record = logger.makeRecord(
        logger.name, level, "(file)", 0, msg, (), None
    )
    record.extra_fields = fields
    logger.handle(record)

logger = get_logger("fine_tuning_pipeline")

# ── Retry with Exponential Backoff + Jitter ──────────────────────────────────

def retry_with_backoff(
    fn,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError, OSError),
):
    """
    Retries fn() with exponential backoff and full jitter.
    Jitter prevents thundering herd when multiple workers retry simultaneously.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            if attempt == max_retries:
                log_with_fields(
                    logger, logging.ERROR,
                    "All retries exhausted",
                    function=fn.__name__,
                    attempts=max_retries + 1,
                    error=str(e),
                )
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay)  # full jitter
            log_with_fields(
                logger, logging.WARNING,
                "Retrying after transient failure",
                function=fn.__name__,
                attempt=attempt + 1,
                delay_sec=round(jitter, 2),
                error=str(e),
            )
            time.sleep(jitter)

# ── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    """
    Protects external calls (model downloads, dataset fetches) from cascading
    failure. Opens after failure_threshold consecutive failures, resets after
    recovery_timeout seconds.
    """
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0

    def call(self, fn, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                log_with_fields(
                    logger, logging.INFO,
                    "Circuit half-open, attempting probe",
                    circuit=self.name,
                )
                self.state = CircuitState.HALF_OPEN
            else:
                raise RuntimeError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Recovery in {self.recovery_timeout - (time.time() - self.last_failure_time):.0f}s"
                )

        try:
            result = fn(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                log_with_fields(
                    logger, logging.INFO,
                    "Circuit recovered, closing",
                    circuit=self.name,
                )
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            return result
        except Exception as e:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                log_with_fields(
                    logger, logging.ERROR,
                    "Circuit opened after repeated failures",
                    circuit=self.name,
                    failures=self.failure_count,
                    error=str(e),
                )
            raise

# ── Fallback Chain ───────────────────────────────────────────────────────────

def fallback_chain(primary_fn, *fallbacks, context: str = ""):
    """
    Tries primary_fn first, then each fallback in order.
    Returns the first successful result.
    """
    last_error = None
    for i, fn in enumerate([primary_fn, *fallbacks]):
        try:
            result = fn()
            if i > 0:
                log_with_fields(
                    logger, logging.WARNING,
                    "Fallback succeeded",
                    context=context,
                    fallback_index=i,
                    function=fn.__name__,
                )
            return result
        except Exception as e:
            last_error = e
            log_with_fields(
                logger, logging.WARNING,
                "Fallback attempt failed",
                context=context,
                fallback_index=i,
                function=fn.__name__,
                error=str(e),
            )
    raise RuntimeError(
        f"All fallbacks exhausted for '{context}': {last_error}"
    )

# ── Fine-Tuning Configuration ────────────────────────────────────────────────

@dataclass
class FTConfig:
    """Single source of truth for fine-tuning hyperparameters."""
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct"
    dataset_name: str = "tatsu-lab/alpaca"
    output_dir: str = "./ft-output"
    # LoRA config -- r=8, all linear layers is the empirically optimal default
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lora_target_modules: list = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "down_proj", "up_proj",
    ])
    # Training config -- single epoch prevents overfitting on static datasets
    epochs: int = 1
    per_device_batch_size: int = 4
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-5
    max_seq_length: int = 2048
    # Checkpoint
    save_steps: int = 100
    resume_from_checkpoint: Optional[str] = None

# ── Pipeline ─────────────────────────────────────────────────────────────────

model_download_breaker = CircuitBreaker(name="model_download", failure_threshold=3)
dataset_download_breaker = CircuitBreaker(name="dataset_download", failure_threshold=3)


def load_base_model(config: FTConfig):
    """Load base model with circuit breaker and retry protection."""
    def _load():
        log_with_fields(
            logger, logging.INFO,
            "Loading base model",
            model=config.base_model,
        )
        model = AutoModelForCausalLM.from_pretrained(
            config.base_model,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=False,  # security: never trust remote code
        )
        tokenizer = AutoTokenizer.from_pretrained(config.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        return model, tokenizer

    return model_download_breaker.call(
        lambda: retry_with_backoff(_load, max_retries=3)
    )


def load_training_dataset(config: FTConfig):
    """Load dataset with fallback chain: HF Hub -> local cache -> error."""
    def _from_hub():
        return load_dataset(config.dataset_name, split="train")

    def _from_cache():
        log_with_fields(
            logger, logging.INFO,
            "Attempting local cache fallback",
            dataset=config.dataset_name,
        )
        cache_dir = Path.home() / ".cache" / "huggingface" / "datasets"
        return load_dataset(config.dataset_name, split="train", cache_dir=str(cache_dir))

    return fallback_chain(
        lambda: dataset_download_breaker.call(
            lambda: retry_with_backoff(_from_hub, max_retries=3)
        ),
        _from_cache,
        context="dataset_load",
    )


def apply_lora(model, config: FTConfig):
    """Apply LoRA adapter targeting all linear layers."""
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=config.lora_rank,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.lora_target_modules,
        bias="none",
    )
    peft_model = get_peft_model(model, lora_config)
    trainable, total = peft_model.get_nb_trainable_parameters()
    log_with_fields(
        logger, logging.INFO,
        "LoRA applied",
        trainable_params=trainable,
        total_params=total,
        trainable_pct=round(100 * trainable / total, 4),
        rank=config.lora_rank,
        target_modules=config.lora_target_modules,
    )
    return peft_model


def find_latest_checkpoint(output_dir: str) -> Optional[str]:
    """Find the latest checkpoint directory for resume-from-checkpoint."""
    output_path = Path(output_dir)
    if not output_path.exists():
        return None
    checkpoints = sorted(
        [d for d in output_path.iterdir() if d.name.startswith("checkpoint-")],
        key=lambda d: int(d.name.split("-")[-1]),
    )
    if checkpoints:
        latest = str(checkpoints[-1])
        log_with_fields(
            logger, logging.INFO,
            "Found checkpoint for resume",
            checkpoint=latest,
        )
        return latest
    return None


def run_training(config: FTConfig):
    """
    Full fine-tuning pipeline:
      1. Load model + tokenizer (with circuit breaker + retry)
      2. Load dataset (with fallback chain)
      3. Apply LoRA
      4. Train with checkpoint resume
      5. Save final adapter
    """
    start_time = time.time()
    log_with_fields(
        logger, logging.INFO,
        "Pipeline started",
        base_model=config.base_model,
        dataset=config.dataset_name,
        lora_rank=config.lora_rank,
        epochs=config.epochs,
    )

    # Step 1: Load model
    model, tokenizer = load_base_model(config)

    # Step 2: Load dataset
    dataset = load_training_dataset(config)
    log_with_fields(
        logger, logging.INFO,
        "Dataset loaded",
        num_examples=len(dataset),
    )

    # Step 3: Apply LoRA
    model = apply_lora(model, config)

    # Step 4: Configure training with checkpoint resume
    resume_checkpoint = config.resume_from_checkpoint or find_latest_checkpoint(
        config.output_dir
    )

    training_args = TrainingArguments(
        output_dir=config.output_dir,
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.per_device_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        bf16=torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False,
        logging_steps=10,
        save_steps=config.save_steps,
        save_total_limit=3,  # keep last 3 checkpoints to manage disk
        report_to="wandb",
        optim="adamw_torch",
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        max_seq_length=config.max_seq_length,
    )

    log_with_fields(
        logger, logging.INFO,
        "Training started",
        resume_from=resume_checkpoint,
    )
    trainer.train(resume_from_checkpoint=resume_checkpoint)

    # Step 5: Save adapter (not merged -- merge at deploy time)
    adapter_path = os.path.join(config.output_dir, "final_adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)

    elapsed = time.time() - start_time
    log_with_fields(
        logger, logging.INFO,
        "Pipeline completed",
        adapter_path=adapter_path,
        elapsed_sec=round(elapsed, 1),
        elapsed_min=round(elapsed / 60, 1),
    )
    return adapter_path


if __name__ == "__main__":
    config = FTConfig(
        base_model="meta-llama/Llama-3.1-8B-Instruct",
        dataset_name="tatsu-lab/alpaca",
        output_dir="./ft-output",
        lora_rank=8,
        epochs=1,
    )
    run_training(config)
```

### 5.2 Post-Training Evaluation Gate

```python
"""
Ship gate evaluator: blocks deployment if any of three criteria fail.
  1. General capability regression < 5% on reference benchmark
  2. Domain-specific accuracy above threshold
  3. Forgetting regression check on untrained capabilities
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("eval_gate")


@dataclass
class EvalResult:
    metric_name: str
    baseline_score: float
    candidate_score: float
    threshold: float  # max allowed regression (as fraction, e.g., 0.05 = 5%)
    passed: bool

    @property
    def regression_pct(self) -> float:
        if self.baseline_score == 0:
            return 0.0
        return (self.baseline_score - self.candidate_score) / self.baseline_score


def evaluate_general_capability(
    run_benchmark: Callable[[], float],
    baseline_score: float,
    max_regression: float = 0.05,
) -> EvalResult:
    """
    Gate 1: Run MMLU-Pro or MT-Bench against fine-tuned model.
    Regression must be < max_regression (default 5%) vs base model.
    """
    candidate_score = run_benchmark()
    regression = (baseline_score - candidate_score) / baseline_score if baseline_score > 0 else 0
    passed = regression < max_regression
    result = EvalResult(
        metric_name="general_capability",
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        threshold=max_regression,
        passed=passed,
    )
    logger.info(json.dumps({
        "gate": "general_capability",
        "baseline": baseline_score,
        "candidate": candidate_score,
        "regression_pct": round(regression * 100, 2),
        "threshold_pct": max_regression * 100,
        "passed": passed,
    }))
    return result


def evaluate_domain_accuracy(
    run_domain_eval: Callable[[], float],
    min_accuracy: float = 0.85,
) -> EvalResult:
    """Gate 2: Domain-specific held-out test set accuracy."""
    score = run_domain_eval()
    passed = score >= min_accuracy
    result = EvalResult(
        metric_name="domain_accuracy",
        baseline_score=min_accuracy,
        candidate_score=score,
        threshold=0.0,
        passed=passed,
    )
    logger.info(json.dumps({
        "gate": "domain_accuracy",
        "score": score,
        "min_required": min_accuracy,
        "passed": passed,
    }))
    return result


def evaluate_forgetting(
    run_forgetting_eval: Callable[[], float],
    baseline_score: float,
    max_regression: float = 0.05,
) -> EvalResult:
    """
    Gate 3: Test capabilities NOT in the training set.
    Detects catastrophic forgetting -- the silent production failure.
    """
    score = run_forgetting_eval()
    regression = (baseline_score - score) / baseline_score if baseline_score > 0 else 0
    passed = regression < max_regression
    result = EvalResult(
        metric_name="forgetting_regression",
        baseline_score=baseline_score,
        candidate_score=score,
        threshold=max_regression,
        passed=passed,
    )
    logger.info(json.dumps({
        "gate": "forgetting_regression",
        "baseline": baseline_score,
        "candidate": score,
        "regression_pct": round(regression * 100, 2),
        "threshold_pct": max_regression * 100,
        "passed": passed,
    }))
    return result


def ship_gate(results: list[EvalResult]) -> bool:
    """All gates must pass. Any single failure blocks deployment."""
    all_passed = all(r.passed for r in results)
    failures = [r.metric_name for r in results if not r.passed]
    logger.info(json.dumps({
        "ship_gate": "PASS" if all_passed else "FAIL",
        "failures": failures,
        "results": [
            {
                "metric": r.metric_name,
                "baseline": r.baseline_score,
                "candidate": r.candidate_score,
                "passed": r.passed,
            }
            for r in results
        ],
    }))
    return all_passed
```

### 5.3 LoRA Adapter Merge and Validation

```python
"""
Merge LoRA adapter into base model for zero-overhead inference deployment.
Validates merged model produces identical outputs to adapter-loaded model.
"""

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def merge_and_validate(
    base_model_id: str,
    adapter_path: str,
    output_path: str,
    validation_prompts: list[str] | None = None,
    atol: float = 1e-4,
) -> str:
    """
    1. Load base + adapter
    2. Generate reference outputs (pre-merge)
    3. Merge adapter into base weights
    4. Generate outputs from merged model
    5. Validate equivalence within tolerance
    6. Save merged model
    """
    tokenizer = AutoTokenizer.from_pretrained(base_model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load base + LoRA adapter (unmerged)
    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=False,
    )
    lora_model = PeftModel.from_pretrained(base_model, adapter_path)

    if validation_prompts is None:
        validation_prompts = [
            "Explain the concept of fine-tuning in one sentence.",
            "What is the capital of France?",
        ]

    # Generate reference outputs from unmerged model
    lora_model.eval()
    reference_logits = []
    with torch.no_grad():
        for prompt in validation_prompts:
            inputs = tokenizer(prompt, return_tensors="pt").to(lora_model.device)
            outputs = lora_model(**inputs)
            reference_logits.append(outputs.logits.cpu())

    # Merge LoRA into base weights: W_merged = W + AB^T
    merged_model = lora_model.merge_and_unload()

    # Validate merged outputs match unmerged
    merged_model.eval()
    with torch.no_grad():
        for i, prompt in enumerate(validation_prompts):
            inputs = tokenizer(prompt, return_tensors="pt").to(merged_model.device)
            outputs = merged_model(**inputs)
            merged_logits = outputs.logits.cpu()
            max_diff = (reference_logits[i] - merged_logits).abs().max().item()
            if max_diff > atol:
                raise ValueError(
                    f"Merge validation failed on prompt {i}: "
                    f"max logit diff {max_diff:.6f} > tolerance {atol}"
                )

    # Save merged model -- zero inference overhead from this point
    merged_model.save_pretrained(output_path)
    tokenizer.save_pretrained(output_path)
    return output_path
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Domain-Specific Customer Support Platform

**Problem statement**: A SaaS company receives 50,000 support tickets/day across 12 product lines. Current setup uses GPT-4.1 with RAG over product docs, costing $180K/month in API fees. Response quality is inconsistent: the model frequently ignores company tone guidelines, generates verbose answers when users need concise steps, and hallucinates product features that do not exist. The company wants to cut costs by 60%+ while improving format compliance and reducing hallucination.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Product Line      │  │ Dataset Version   │  │ Model Lifecycle          │   │
│  │ Router             │  │ Manager           │  │ Manager                  │   │
│  │                    │  │                    │  │                          │   │
│  │ Classifies ticket  │  │ Per-product-line  │  │ dev --> shadow -->       │   │
│  │ to 1 of 12 product │  │ immutable corpus  │  │ canary 5% --> prod 100% │   │
│  │ lines, selects     │  │ versioning (DVC)  │  │                          │   │
│  │ corresponding      │  │ Quarterly refresh │  │ Rollback in < 5 min     │   │
│  │ LoRA adapter       │  │ with poison scan  │  │ on quality drop          │   │
│  └────────┬──────────┘  └────────┬──────────┘  └──────────┬───────────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │                        │                          │
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                        TRAINING PLANE (offline, quarterly)                    │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Per-Product LoRA Training                                             │    │
│  │                                                                       │    │
│  │ Base: Llama 3.1 8B-Instruct (shared across all 12 products)          │    │
│  │                                                                       │    │
│  │ Phase 1: SFT on 5,000-15,000 ticket-resolution pairs per product     │    │
│  │   - Format: concise step-by-step, company tone, no hallucination     │    │
│  │   - LoRA r=8, all linear layers, QLoRA 4-bit for cost efficiency     │    │
│  │   - Single epoch, AdamW, lr=2e-5                                     │    │
│  │                                                                       │    │
│  │ Phase 2: DPO on 1,000-2,000 preference pairs per product             │    │
│  │   - Chosen: concise, accurate, on-tone responses                      │    │
│  │   - Rejected: verbose, hallucinated, off-tone responses               │    │
│  │                                                                       │    │
│  │ Output: 12 LoRA adapters (~20 MB each)                                │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Eval Gates (per adapter)                                              │    │
│  │  1. MMLU-Pro regression < 5% vs base Llama 3.1 8B                    │    │
│  │  2. Domain accuracy > 90% on held-out 500 tickets                     │    │
│  │  3. Forgetting: cross-product queries regression < 5%                 │    │
│  │  4. Red team: jailbreak resistance, PII leak test                     │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ promoted adapters
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                          INFERENCE PLANE                                      │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ vLLM Serving Cluster (3x A100 80GB, shared Llama 3.1 8B base)        │   │
│  │                                                                       │   │
│  │ Adapter hot-swap: route ticket to correct product LoRA adapter        │   │
│  │ 12 adapters loaded simultaneously on shared backbone                  │   │
│  │ TTFT: < 300 ms (8B model, single GPU per request)                    │   │
│  │                                                                       │   │
│  │ RAG layer (product docs): retrieves current feature state to          │   │
│  │ prevent hallucinating deprecated features. Fine-tuning handles        │   │
│  │ format + tone; RAG handles knowledge freshness.                       │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ Quality Monitor                                                       │   │
│  │  - CSAT score per product line (target > 4.2/5.0)                     │   │
│  │  - Hallucination rate (target < 2%, measured by auto-eval)            │   │
│  │  - Format compliance (target > 95% step-by-step when appropriate)    │   │
│  │  - Auto-rollback trigger: any metric drops > 10% from baseline       │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌──────────────────────┬──────────────────────────┬──────────────────────────┐
│ Factor               │ Current (GPT-4.1 + RAG)  │ Proposed (8B + LoRA+RAG) │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Monthly cost         │ $180K (API)              │ ~$15K (3x A100 lease +   │
│                      │                          │  quarterly retrain)      │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Cost reduction       │ Baseline                 │ ~92%                     │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Format compliance    │ ~70% (prompt-only)       │ ~95% (SFT + DPO)        │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Hallucination rate   │ ~8% (RAG helps, but      │ ~2% (tuned model less    │
│                      │  model still confabulates)│  prone + RAG grounds)   │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Latency (TTFT)       │ 800ms-1.5s (API + RAG)   │ < 300ms (local 8B +RAG) │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Data privacy         │ Data leaves VPC           │ Full VPC containment     │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Maintenance          │ Low (API managed)         │ High (retrain quarterly, │
│                      │                          │  monitor, adapter mgmt)  │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Multi-product        │ Single model, prompt      │ 12 specialized adapters  │
│ specialization       │ engineering per product   │ on shared backbone       │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Time to deploy       │ Already live              │ 3-4 months (data curation│
│                      │                          │  + training + eval)      │
└──────────────────────┴──────────────────────────┴──────────────────────────┘
```

**Decision rationale**: The 92% cost reduction justifies the 3-4 month build-out. The hybrid approach (fine-tune for behavior + RAG for knowledge) is the optimal pattern for this use case: format compliance and tone require behavioral fine-tuning that prompting cannot reliably achieve at 50K tickets/day, while product feature accuracy requires retrieval from current documentation. The shared-backbone + per-product LoRA adapter architecture scales to 12 product lines without 12x the GPU cost. The key risk is catastrophic forgetting across products -- the modular LoRA architecture (separate adapter per product) mitigates this by isolating updates.

---

### 6.2 Scenario: Multi-Tenant Compliance Document Analyzer for Financial Services

**Problem statement**: A fintech SaaS serves 200 bank clients, each with their own regulatory interpretation of Basel III, AML/KYC, and local compliance rules. Current solution uses a single RAG pipeline over each client's compliance docs, but output quality varies wildly: the model fails to apply client-specific regulatory logic, misses nuanced compliance distinctions between jurisdictions, and produces inconsistent citation formats. Some clients require SOC 2 Type II and data residency (EU clients: data cannot leave EU; India clients: data must stay in India). The system must handle 10,000 queries/day with sub-2-second end-to-end latency.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Tenant Router     │  │ Region Router     │  │ Compliance Governance    │   │
│  │                    │  │                    │  │ Engine                   │   │
│  │ JWT tenant_id -->  │  │ EU tenant -->     │  │                          │   │
│  │ select adapter +   │  │ EU cluster        │  │ RBAC per tenant          │   │
│  │ RAG namespace      │  │ India tenant -->  │  │ Audit log (immutable)    │   │
│  │                    │  │ India cluster     │  │ Data residency enforce   │   │
│  │ Per-tenant ACL     │  │ US tenant -->     │  │ SOC 2 Type II controls   │   │
│  │ filter on all      │  │ US cluster        │  │                          │   │
│  │ retrievals         │  │                    │  │ Model card + AIBOM per   │   │
│  │                    │  │                    │  │ adapter version          │   │
│  └────────┬──────────┘  └────────┬──────────┘  └──────────┬───────────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │                        │                          │
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                        TRAINING PLANE                                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Tiered Fine-Tuning Strategy                                           │    │
│  │                                                                       │    │
│  │ Tier 1: Base compliance adapter (shared across all tenants)           │    │
│  │   Base: Llama 3.1 70B-Instruct                                       │    │
│  │   SFT: 30,000 compliance Q&A pairs (general regulatory knowledge)    │    │
│  │   DPO: 3,000 preference pairs (citation format, reasoning depth)     │    │
│  │   LoRA r=16, all linear layers                                        │    │
│  │   Trained on 8x H100 via FSDP2 (~3 hours)                            │    │
│  │                                                                       │    │
│  │ Tier 2: Jurisdiction adapters (5-10 adapters)                         │    │
│  │   Stacked LoRA on top of Tier 1                                       │    │
│  │   EU/Basel III, US/Dodd-Frank, India/RBI, UK/FCA, APAC/MAS           │    │
│  │   SFT: 5,000-10,000 jurisdiction-specific examples each              │    │
│  │                                                                       │    │
│  │ Tier 3: Per-tenant adapters (top 20 clients by volume)                │    │
│  │   Lightweight LoRA (r=4) on tenant-specific interpretations           │    │
│  │   SFT: 500-2,000 examples per tenant                                 │    │
│  │   Remaining 180 tenants: RAG-only on Tier 2 base                     │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Isolated Training Environments (data residency)                       │    │
│  │                                                                       │    │
│  │  EU training cluster ──── EU tenant data never leaves EU region       │    │
│  │  India training cluster ── India tenant data stays in India           │    │
│  │  US training cluster ──── Default for US/other tenants                │    │
│  │                                                                       │    │
│  │  Base adapter (Tier 1) trained on anonymized, non-tenant data only    │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                          INFERENCE PLANE (per region)                         │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ Regional Serving Cluster (e.g., EU: 4x H100 in eu-west-1)            │   │
│  │                                                                       │   │
│  │ Shared 70B base + Tier 1 adapter (merged)                             │   │
│  │ Tier 2 jurisdiction adapter: hot-swap based on tenant region          │   │
│  │ Tier 3 tenant adapter: hot-swap for top-20 clients                   │   │
│  │                                                                       │   │
│  │ RAG layer: per-tenant vector namespace (ACL filtered)                 │   │
│  │   Retrieves client's own compliance docs + regulatory updates         │   │
│  │   Citation engine: every claim linked to source doc + section         │   │
│  │                                                                       │   │
│  │ Latency budget:                                                       │   │
│  │   Adapter load (hot cache): ~10ms                                     │   │
│  │   RAG retrieval + rerank: ~400ms                                      │   │
│  │   LLM generation (70B, 4x H100): ~1,200ms                            │   │
│  │   Citation + validation: ~100ms                                       │   │
│  │   Total: ~1,700ms (within 2s SLA)                                     │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │ Compliance Monitor                                                    │   │
│  │  - Citation accuracy (every response must cite source regulation)     │   │
│  │  - Jurisdiction correctness (responses apply correct local rules)     │   │
│  │  - Data residency audit (no cross-region data leak in logs/cache)     │   │
│  │  - Quarterly compliance review: human auditors validate sample        │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌──────────────────────┬──────────────────────────┬──────────────────────────┐
│ Factor               │ Single RAG Pipeline      │ Tiered FT + RAG Hybrid   │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Regulatory reasoning │ Shallow (depends on      │ Deep (jurisdiction logic  │
│ quality              │ retrieved chunk quality)  │ encoded in weights)      │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Citation consistency │ Variable (prompt-driven)  │ High (DPO-trained format)│
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Multi-jurisdiction   │ Same model for all;       │ Jurisdiction-specific    │
│ handling             │ prompt overload           │ adapters encode local    │
│                      │                          │ regulatory nuance        │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Data residency       │ Central cluster,          │ Regional clusters with   │
│                      │ compliance risk           │ isolated training + serve│
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Tenant customization │ Prompt templates only     │ Per-tenant LoRA for top  │
│                      │                          │ clients, RAG for rest    │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Infra cost           │ Lower (single cluster)    │ Higher (regional clusters│
│                      │                          │ + training pipeline)     │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Operational          │ Low                       │ High (adapter lifecycle, │
│ complexity           │                          │ multi-region ops)        │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Latency (e2e)        │ ~2.5s (large model API +  │ ~1.7s (self-hosted 70B  │
│                      │ RAG)                     │ + cached adapters + RAG) │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ Forgetting risk      │ None (no fine-tuning)     │ Moderate (mitigated by   │
│                      │                          │ tiered adapter isolation) │
├──────────────────────┼──────────────────────────┼──────────────────────────┤
│ SOC 2 / GDPR         │ Requires API provider     │ Full self-hosted control │
│ compliance           │ compliance chain          │ per region               │
└──────────────────────┴──────────────────────────┴──────────────────────────┘
```

**Decision rationale**: The tiered adapter architecture (base compliance --> jurisdiction --> tenant) solves the core problem: regulatory reasoning requires behavioral patterns that RAG alone cannot provide. A bank's AML compliance logic is not a fact to retrieve -- it is a reasoning pattern over many regulations that must be consistently applied. The three-tier LoRA stack isolates training data at each level, mitigating both catastrophic forgetting (each tier's adapter is independent) and data residency (Tier 3 tenant adapters train only in the tenant's region on tenant data). The 70B model is justified by the domain complexity; compliance reasoning in finance is harder than general QA and smaller models fail the 20-question domain probe threshold. RAG remains essential for knowledge freshness -- regulatory updates happen monthly, and retraining adapters monthly is impractical. The key risk is operational complexity: managing 25-30 adapters across 3 regions with quarterly retraining cycles requires mature MLOps. The cost of this complexity is justified by the SOC 2 / GDPR requirement -- API-based solutions cannot guarantee data residency at the training data level.
