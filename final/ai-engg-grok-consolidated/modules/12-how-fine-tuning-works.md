# Module 12: How Fine-Tuning Works

### What Is This?

Fine-tuning shifts a foundation model's **defaults** -- style, format, reasoning patterns, refusal calibration -- toward a task-shaped dataset. It rewrites behavior into model weights rather than specifying it through prompts at every call. The operating rule that governs every decision in this module: **fine-tune for behavior, retrieve for knowledge**. A reasoning pattern repeated across thousands of examples persists in weights; a fact appearing once or twice will not. This single heuristic determines when fine-tuning is the right tool versus RAG or prompt engineering.

**Concrete example**: A customer support platform uses GPT-4.1 with RAG over product docs. Responses are factually grounded (RAG works) but inconsistently formatted -- sometimes bullet lists, sometimes paragraphs, sometimes overly verbose. Prompt engineering achieves ~70% format compliance. Fine-tuning on 5,000 examples of correctly-formatted responses pushes compliance to 95%+ because the behavior becomes the model's default rather than an instruction it sometimes ignores.

This module covers three training regimes (continued pretraining, SFT, preference alignment), the PEFT taxonomy (LoRA, QLoRA, DoRA, Adapters, Prefix Tuning, Prompt Tuning), alignment methods (RLHF, DPO, GRPO, KTO), hosted surfaces (OpenAI wind-down, Bedrock Claude, Together AI, Vertex), distributed training (FSDP2, ZeRO-3, Megatron-LM), catastrophic forgetting, data poisoning threats, and the FT-vs-prompt-vs-RAG lever choice.

---

## 1. System Topology & Data Flow

A production fine-tuning system spans five cooperating layers: a **control plane** managing experiment configuration, dataset versioning, and access control; a **training plane** where gradient computation happens across GPUs using FSDP2/DeepSpeed sharding; an **evaluation plane** gating every checkpoint against regression benchmarks; a **model registry and persistence layer** storing immutable dataset snapshots, LoRA adapters, and full provenance; and a **serving and telemetry layer** hosting the fine-tuned model while tracking drift, latency, and cost.

```
+----------------------------------------------------------------------------------+
|                              CONTROL PLANE                                        |
|                                                                                   |
|  +-------------------+   +------------------+   +------------------------+       |
|  | Experiment Config  |   | Dataset Version  |   | Access Control /       |       |
|  | Manager            |   | Manager          |   | Governance Engine      |       |
|  |                    |   |                  |   |                        |       |
|  | Hyperparams:       |   | DVC / MLflow:    |   | RBAC per stage:        |       |
|  |  base model ID     |   |  immutable SHA   |   |  dev: train + eval     |       |
|  |  LoRA rank, alpha  |   |  snapshots       |   |  staging: promote      |       |
|  |  target modules    |   |  rollback to any |   |  prod: deploy (human   |       |
|  |  lr, batch, epochs |   |  prior version   |   |    gate required)      |       |
|  |  chat template     |   |  poison-scan +   |   |  archive: read-only    |       |
|  |                    |   |  PII-scan on     |   |                        |       |
|  |  method: SFT/DPO/  |   |  ingest          |   | FT job submit/cancel   |       |
|  |  RFT/GRPO          |   |                  |   | adapter ACL            |       |
|  +--------+-----------+   +--------+---------+   +----------+------------+       |
+-----------+------------------------+----------------------------+----------------+
            | locked config          | dataset ref                | scoped perms
+-----------v------------------------v----------------------------v----------------+
|                           TRAINING PLANE                                          |
|                                                                                   |
|  +------------------+  +------------------+  +--------------------------+        |
|  | Data Preprocessor |  | Training Engine  |  | Checkpoint Manager       |        |
|  |                   |  |                  |  |                          |        |
|  | Tokenize, apply   |  | HF TRL / PEFT:  |  | SHARDED_STATE_DICT       |        |
|  | chat template,    |  |  SFT / DPO /    |  | (FSDP2) or               |        |
|  | validate format,  +->|  GRPO / RFT     +->| zero_to_fp32.py          |        |
|  | PII filter,       |  |                  |  | (DeepSpeed)              |        |
|  | split train/val   |  | Distributed:     |  |                          |        |
|  |                   |  |  FSDP2 (<=30B)   |  | Async offload for        |        |
|  | Chat template     |  |  ZeRO-3 (<=70B)  |  | spot instance safety     |        |
|  | mismatch = #1     |  |  Megatron (>100B)|  |                          |        |
|  | failure cause     |  |                  |  |                          |        |
|  +------------------+  +--------+---------+  +------------+-------------+        |
|                                  | loss curve               | checkpoints         |
|  +-------------------------------v---------------------------v-----------------+  |
|  |           Experiment Tracker (W&B / MLflow 3.0)                              |  |
|  |  loss per step | grad norm | lr schedule | GPU util | token throughput       |  |
|  |  connects: code version, dataset version, config, eval results               |  |
|  +------------------------------------------------------------------------------+  |
+--------------------------------------+-------------------------------------------+
                                       | candidate checkpoint
+--------------------------------------v-------------------------------------------+
|                          EVALUATION PLANE                                          |
|                                                                                   |
|  +------------------+  +------------------+  +--------------------------+        |
|  | General Capability|  | Domain-Specific  |  | Safety / Red Team        |        |
|  | Regression Gate   |  | Eval Suite       |  | Battery                  |        |
|  |                   |  |                  |  |                          |        |
|  | MMLU-Pro,         |  | Task-specific    |  | Sensitive topics,        |        |
|  | MT-Bench:         |  | held-out test    |  | unusual prompts,         |        |
|  | regression < 5%   |  | set: precision,  |  | OOD inputs,              |        |
|  | of base model     |  | recall, format   |  | capability regression    |        |
|  |                   |  | compliance       |  | on UNTRAINED tasks       |        |
|  +--------+---------+  +--------+---------+  +------------+-------------+        |
|           | pass/fail            | scores                  | pass/fail             |
|  +--------v----------------------v--------------------------v-----------------+   |
|  |                       Ship Gate Evaluator                                   |   |
|  |  ALL three must pass:                                                       |   |
|  |   1. General regression < 5% on MMLU-Pro / MT-Bench                        |   |
|  |   2. Inference cost >= 50% below foundation-model baseline                  |   |
|  |   3. TTFT < 300 ms for 7B on single GPU                                    |   |
|  +----------+--------------------------------------------------------------+   |   |
+--------------+-------------------------------------------------------------------+
               | promoted model + eval report
+--------------v-------------------------------------------------------------------+
|                   MODEL REGISTRY & PERSISTENCE LAYER                              |
|                                                                                   |
|  +------------------+  +-------------------+  +------------------------+         |
|  | Model Registry   |  | Artifact Store    |  | Provenance / AIBOM     |         |
|  | (MLflow 3.0 /    |  |                   |  |                        |         |
|  |  Unity Catalog)  |  | LoRA adapters     |  | Base model ID          |         |
|  |                  |  | (~20-35 MB each)  |  | Dataset SHA + version  |         |
|  | Lifecycle stages:|  | Merged weights    |  | Training config        |         |
|  |  dev -> staging  |  | Tokenizer config  |  | Eval results           |         |
|  |  -> prod ->      |  | Chat template     |  | Approver + timestamp   |         |
|  |  archived        |  | Eval reports      |  | Code commit hash       |         |
|  +--------+---------+  +-------------------+  +------------------------+         |
+---------------+------------------------------------------------------------------+
                | serving config
+---------------v------------------------------------------------------------------+
|                    SERVING & TELEMETRY LAYER                                      |
|                                                                                   |
|  +------------------+  +------------------+  +--------------------------+        |
|  | Inference Server |  | A/B Router       |  | Monitoring               |        |
|  | (vLLM / TGI)     |  |                  |  |                          |        |
|  |                  |  | Shadow traffic:  |  | Output quality drift     |        |
|  | Merged LoRA:     |  | 5% to new model, |  | Latency p50/p95/p99     |        |
|  |  zero overhead   |  | 95% to incumbent |  | Cost per query           |        |
|  | Unmerged LoRA:   |  | until eval parity|  | Token throughput         |        |
|  |  hot-swap adapts |  | confirmed        |  | Forgetting regression    |        |
|  |  on shared base  |  |                  |  |  on non-target tasks     |        |
|  +------------------+  +------------------+  +--------------------------+        |
+-----------------------------------------------------------------------------------+
```

### End-to-End Request-Flow Narrative

**Training path (dataset to adapter registry to inference)**:

1. **Dataset ingress** -- Curate instruction pairs (SFT) or preference triples (DPO/RLHF). Lock version, seed, and holdout. Run **PII detect -> redact -> audit** plus **poison scan** in the data preprocessor *before* any trainer sees bytes. Both scans produce signed attestations required for the dataset version manager to release the corpus.

2. **Control plane submit** -- Choose method (SFT / DPO / RFT / GRPO). OpenAI: JSONL upload + job create (winding down May 2026). Bedrock: S3 URIs + `CreateModelCustomizationJob` (async). Self-host: enqueue Temporal/K8s job with dataset digest + hyperparams. Submit with idempotency key `dataset_digest:hparams_hash` to prevent duplicate billable runs.

3. **Training plane train** -- Freeze W_0; optimize LoRA delta_W = BA (or QLoRA with 4-bit base + bf16 adapters). Checkpoint adapters to the persistence layer on interval; optional early stop on validation loss. Chat template mismatch between training and inference is the single most common cause of degraded post-fine-tuning performance.

4. **Evaluation plane gates** -- Each candidate checkpoint faces three gates in sequence: general capability regression (< 5% drop vs base), domain-specific accuracy on held-out test set, and safety/red-team including capability regression on tasks NOT in the training set (the primary forgetting detector). All three must pass.

5. **Adapter registry** -- On success, register artifact: OpenAI `ft:...` ID, Bedrock custom model ARN, or self-host adapter ID mapping to (A,B) object key + base digest. AIBOM (AI Bill of Materials) captures full provenance: base model, dataset hash, training config, eval results, approver identity, code commit hash.

6. **Inference promote** -- Serving layer loads base + adapter (unmerged for hot-swap) or merged (W = W_0 + BA, zero overhead). Bedrock custom Claude requires **Provisioned Throughput** (MU hours) before invoke -- the model is unusable on-demand without PT. A/B router sends 5% shadow traffic to new model until eval parity confirmed.

7. **Online path** -- Client / MCP tool proxy calls FT model with correlation ID. Telemetry records TTFT/tokens/breaker state. On FT outage, fallback chain: **FT model -> base model -> deterministic prompt template**.

### Job State Machine

```
                +---------------+
                |  dataset      |-- PII scrub + poison scan --> locked JSONL + digest
                |  staged       |
                +------+--------+
                       v
                +---------------+
       +------->|  queued       |<---- rate-limit / quota
       |        +------+--------+
       |               v
       |        +---------------+
       |        |  running      |-- checkpoint adapters --> registry (draft)
       |        +------+--------+
       |         +-----+------+
       |         v            v
       |  +-----------+ +-----------+
       |  | failed    | | succeeded |-- eval gate --> promoted | rejected
       |  +-----+-----+ +-----+----+
       |        |              v
       |        |       +-----------+
       +--------+-------| serving   |-- FT -> base -> prompt fallback
                        +-----------+
```

**Invariants**: (1) Train never mutates the frozen base blob in place without a new lineage ID. (2) Promote requires holdout + regression gate. (3) Inference selection is a registry pointer, not a side-effect of the last train job.

---

## 2. Core Mechanics & Algorithms

### 2.1 Three Training Regimes

```
+--------------------+----------------------+-------------------+--------------------------+
| Regime             | Data Type            | Dataset Size      | When to Use              |
+--------------------+----------------------+-------------------+--------------------------+
| Continued          | Raw unlabeled        | 100M - 1B+ words  | Base model barely        |
| Pretraining (CPT)  | domain text          |                   | understands domain       |
|                    |                      |                   | (BloombergGPT, Med-PaLM) |
+--------------------+----------------------+-------------------+--------------------------+
| Supervised         | Instruction-response | 1,000 - 50,000    | Right starting point     |
| Fine-Tuning (SFT)  | pairs                | pairs             | for almost every project |
|                    |                      |                   | Style, format, reasoning |
+--------------------+----------------------+-------------------+--------------------------+
| Preference Tuning  | Comparison pairs     | 500 - 5,000 pairs | Calibrated refusals,     |
| (RLHF / DPO)      | (chosen vs rejected) |                   | consistent tone, formats |
|                    |                      |                   | SFT cannot reliably teach|
+--------------------+----------------------+-------------------+--------------------------+
```

**Practical order**: SFT first -> ship -> DPO/RLHF for residual preference failures. Continued pretraining only if the base fails a domain-vocabulary probe. Preference tuning fixes the long tail of behaviors that demonstrations alone cannot encode.

### 2.2 The Memory Wall (Why PEFT Dominates)

For a **7B** model at FP16: parameters = ~14 GB, gradients = ~14 GB, Adam optimizer states (first moment + second moment + master weights) = ~32 GB. **Total GPU memory: 60-80 GB** -- beyond consumer 16-24 GB GPUs. This is why parameter-efficient methods dominate practical fine-tuning.

For a **70B** model at FP16: parameters = 140 GB, gradients = 140 GB, Adam states = 560 GB. **Total: 840 GB static state.** On 8x H100s (640 GB total GPU memory), this does not fit without ZeRO-3 CPU offload or 3D parallelism.

### 2.3 LoRA (Low-Rank Adaptation)

Freezes base weights W_0 entirely. Injects trainable low-rank matrices B (d x r) and A (r x k) such that the effective weight update is delta_W = BA, with r much smaller than min(d,k).

**Forward pass** (scaled by alpha/r):

```
h = W_0 * x + (alpha / r) * B * A * x
```

**Initialization**: Gaussian initialization for A, zero initialization for B, ensuring delta_W = 0 at start (training begins from the base model's behavior exactly).

**Trainable parameter count** when adapting L_hat matrices:

```
|Theta| = 2 * L_hat * d_model * r
```

```
+-----------------------------------------------------------+
|                    LoRA Forward Pass                        |
|                                                            |
|   Input x --+-----------------------------------+         |
|             |                                   |         |
|             v                                   v         |
|   +-----------------+                 +-----------------+ |
|   | W_0 (frozen)    |                 | A (d x r)       | |
|   | d x k           |                 | trainable       | |
|   | full-precision  |                 +---------+-------+ |
|   +--------+--------+                           |         |
|            |                                    v         |
|            |                          +-----------------+ |
|            |                          | B (r x k)       | |
|            |                          | trainable       | |
|            |                          +---------+-------+ |
|            |                                    |         |
|            v               +                    v         |
|   +---------------------------------------------------+  |
|   |          Output = W_0*x + (alpha/r)*B*A*x          |  |
|   +---------------------------------------------------+  |
|                                                            |
|   Trainable params: 2 * d * r  (r << d)                   |
|   Rank r=8 or 16: trains < 1% of full parameters          |
|   Merged at deploy: W_deploy = W_0 + BA, zero overhead    |
+-----------------------------------------------------------+
```

**Benchmark numbers (GPT-3 175B)**:

| Metric | Full FT (Adam) | LoRA |
|---|---|---|
| Trainable params | Full 175B | ~10,000x fewer; as low as ~0.01% |
| Training VRAM | ~1.2 TB | ~350 GB (~3x reduction) |
| Adapter checkpoint (r=4, W_q+W_v) | Full ~350 GB | ~35 MB (~10,000x smaller) |
| 100-task storage | ~35 TB full copies | ~350 GB + 100x35 MB = 354 GB |

**Critical insight on target modules**: Targeting **all linear layers** (q_proj, k_proj, v_proj, o_proj, gate_proj, down_proj, up_proj, lm_head) matters more than increasing rank. Databricks study on OpenLLaMA-3b showed r=8 vs r=16 on attention blocks alone produced no quality improvement. The biggest quality lever is **breadth of layers targeted**, not rank depth.

**Inference**: Merged LoRA (W + BA fused once) has **zero overhead** compared to the base model. Unmerged enables hot-swapping adapters on a shared backbone for multi-tenant scenarios, at slight latency cost. Task switch by swapping adapters. Rank typically 8 or 16; recovers ~90-95% of full-FT quality on many tasks; hard domain shifts can hit rank capacity.

### 2.4 QLoRA (Quantized LoRA)

Freezes a **4-bit** quantized base (NF4 -- Normal Float, information-theoretically optimal for normally distributed pretrained weights); backprops into **bf16 LoRA** adapters. Three innovations:

1. **4-bit NF4 quantization**: Maps each weight to one of 16 levels optimized for the Gaussian distribution of pretrained weights. Not uniform quantization -- uses the empirical distribution.
2. **Double Quantization**: Quantizes the quantization constants themselves, saving ~0.373 bits/param (~3 GB on 65B).
3. **Paged Optimizers**: Optimizer states page to CPU RAM during GPU memory spikes, preventing OOM crashes.

| Claim | Number |
|---|---|
| Full 16-bit FT of LLaMA 65B | >780 GB GPU memory |
| QLoRA FT of 65B | <48 GB (single GPU) |
| Guanaco 65B train time | ~24 h on one professional GPU |
| Guanaco 65B quality | 99.3% of ChatGPT on Vicuna (GPT-4 judged) |
| Deployed footprint | 65B: 41 GB; 33B: 21 GB; 13B: 10 GB; 7B: 6 GB |

**Impact for 7B**: A 7B model runs on a free Google Colab T4 (16 GB). Trade-off: 33% memory savings at cost of 39% runtime increase vs. standard LoRA. Quality: negligible degradation -- 92% of full fine-tuning performance with 70% less VRAM.

QLoRA places adapters on **all** transformer layers to match 16-bit FT quality.

### 2.5 DoRA (Weight-Decomposed Low-Rank Adaptation)

Decomposes the weight matrix into magnitude ||W|| and direction W/||W|| components. Applies LoRA only to the direction component; magnitude is learned independently as a scalar. This closes the quality gap with full fine-tuning on tasks requiring simultaneous adjustment of both weight magnitude and direction -- a limitation of standard LoRA where magnitude and direction are coupled in the low-rank update.

### 2.6 PEFT Complexity Analysis

```
+----------------+------------------+------------+--------------+----------------+
| Method         | Trainable Params | Memory     | Inference    | Quality vs     |
|                | (7B model)       | (7B)       | Overhead     | Full FT        |
+----------------+------------------+------------+--------------+----------------+
| Full FT        | 7B (100%)        | 60-80 GB   | None         | Baseline       |
+----------------+------------------+------------+--------------+----------------+
| LoRA r=16      | ~16.8M (0.24%)   | 14-16 GB   | None (merged)| 90-95%         |
+----------------+------------------+------------+--------------+----------------+
| QLoRA r=16     | ~16.8M (0.24%)   | ~10 GB     | None (merged)| ~90-95%        |
+----------------+------------------+------------+--------------+----------------+
| DoRA r=16      | ~17M (~0.24%)    | 14-16 GB   | None (merged)| ~95-98%        |
+----------------+------------------+------------+--------------+----------------+
| Adapters       | ~33.6M (0.48%)   | 14-16 GB   | +5-10% lat   | ~90%           |
+----------------+------------------+------------+--------------+----------------+
| Prefix Tuning  | ~5.2M (0.075%)   | 14-16 GB   | Minimal      | ~85-90%        |
+----------------+------------------+------------+--------------+----------------+
| Prompt Tuning  | ~0.4M (0.006%)   | 14-16 GB   | Minimal      | ~80-85%        |
+----------------+------------------+------------+--------------+----------------+
```

**Key invariant**: Memory for PEFT methods (except QLoRA) is dominated by the base model in fp16 (14 GB for 7B), not the trainable parameters. QLoRA's 4-bit quantization reduces the base model footprint to ~3.5 GB.

### 2.7 RLHF: Reinforcement Learning from Human Feedback

**Pipeline**: SFT on demonstrations -> Train reward model from human preference rankings -> RL (PPO) to optimize policy against reward model.

```
+----------------------------------------------------------------------------------+
|                        RLHF Training Infrastructure                               |
|                                                                                   |
|  +------------------+  +------------------+  +--------------------------+        |
|  | Actor (Policy)   |  | Critic Model     |  | Reward Model             |        |
|  | Model            |  |                  |  |                          |        |
|  | The model being  |  | Estimates value  |  | Trained on human         |        |
|  | trained. Generates| | function V(s)    |  | preference rankings.     |        |
|  | responses,       |  | for PPO advantage|  | Scores (prompt,response) |        |
|  | receives PPO     |  | computation      |  | pairs. Frozen during RL. |        |
|  | gradient updates |  |                  |  |                          |        |
|  +--------+---------+  +--------+---------+  +------------+-------------+        |
|           |                      |                         |                      |
|  +--------v----------------------v-------------------------v-----------------+    |
|  |                         PPO Training Loop                                  |    |
|  |                                                                            |    |
|  |  1. Actor generates response to prompt                                     |    |
|  |  2. Reward model scores the response                                       |    |
|  |  3. Critic estimates baseline value                                        |    |
|  |  4. Advantage = reward - baseline                                          |    |
|  |  5. PPO clips gradient to prevent destructive updates                      |    |
|  |  6. KL penalty vs reference model prevents reward hacking                  |    |
|  +----------------------------------------------------------------------------+    |
|                                                                                   |
|  +----------------------------------------------------------------------------+   |
|  | Reference Model (frozen copy of initial SFT model)                          |   |
|  | Anchors KL divergence: prevents actor from drifting into degenerate          |   |
|  | high-reward but low-quality outputs (reward hacking)                         |   |
|  +----------------------------------------------------------------------------+   |
|                                                                                   |
|  Total GPU instances required: 4 (actor + critic + reward + reference)            |
|  Cost: 10-50x more expensive than DPO                                             |
+----------------------------------------------------------------------------------+
```

**Bradley-Terry preference model**:

```
p(y_w > y_l | x) = sigma(r(x, y_w) - r(x, y_l))
```

**Headline result**: 1.3B InstructGPT preferred over 175B GPT-3 on their prompt distribution.

**Failure modes**: (1) PPO hyperparameter sensitivity -- small changes to the KL divergence coefficient produce dramatic performance swings. (2) Reward hacking -- the actor learns to exploit the reward model's blind spots rather than genuinely improving. (3) Training instability -- four concurrent models with interdependent gradients create oscillation.

### 2.8 DPO: Direct Preference Optimization

Reframes preference learning as binary classification. Derives the closed-form solution from the same KL-constrained objective as RLHF:

```
L_DPO = -log sigma(beta * (log pi(y_w|x)/pi_ref(y_w|x) - log pi(y_l|x)/pi_ref(y_l|x)))

where:
  y_w  = chosen (preferred) response
  y_l  = rejected response
  pi   = policy model being trained
  pi_ref = frozen reference SFT model
  beta = temperature controlling deviation from reference
```

**Infrastructure**: Two model instances (policy + reference), not four. Eliminates reward model and RL loop entirely. Matches or exceeds RLHF on summarization, helpfulness, and factuality benchmarks. **10-50x cheaper.**

**Weakness**: Susceptible to overfitting on limited preference datasets, reducing generalization. Offline nature requires upfront human annotation. For complex tasks requiring broad generalization, RL-based training remains superior.

### 2.9 Alignment Method Decision Matrix

```
+------------------+-------+--------+------------+--------+--------+
| Factor           | SFT   | DPO    | RLHF (PPO) | GRPO   | KTO    |
+------------------+-------+--------+------------+--------+--------+
| Compute cost     | $     | $$     | $$$$$      | $$$    | $      |
+------------------+-------+--------+------------+--------+--------+
| GPU instances    | 1     | 2      | 4          | 2-3    | 1      |
+------------------+-------+--------+------------+--------+--------+
| Data requirement | Demos | Pref   | Pref pairs | Task   | Thumbs |
|                  |       | pairs  | + RM data  | compl. | up/down|
+------------------+-------+--------+------------+--------+--------+
| Stability        | High  | High   | Low        | Medium | High   |
+------------------+-------+--------+------------+--------+--------+
| Quality ceiling  | Good  | V.Good | Excellent  | Excl.  | Adeq.  |
|                  |       |        |            |(reason)|        |
+------------------+-------+--------+------------+--------+--------+
| Best model range | Any   | 1B-70B | 10B+       | Reason.| 1B-13B |
+------------------+-------+--------+------------+--------+--------+
```

**GRPO (Group Relative Policy Optimization)**: Used in DeepSeek-R1. Pure RL for reasoning -- groups completions and uses relative ranking within each group as the reward signal, eliminating the separate reward model. Excellent for math and code reasoning.

**KTO**: Requires only thumbs-up/thumbs-down signals (no paired comparisons). Cheapest alignment method. Adequate quality for 1B-13B models.

**Align-Pro / Tied Preference Optimization**: Emerging 2026 methods in early research. Not yet production-proven but worth tracking for multi-objective alignment.

### 2.10 Catastrophic Forgetting

Fine-tuning on a new task degrades performance on tasks the model previously handled. One of the most disorienting production failures because it manifests as regression in workflows nobody touched.

**Key findings (2025-2026)**:

- **LoRA does NOT prevent forgetting.** Despite minimal parameter changes, catastrophic forgetting occurs in continual learning. The issue is *which network paths are altered*, not how many parameters change.
- Below ~200 training examples, catastrophic forgetting is near-certain.
- Larger models generally forget less (overcapacity), but no model is immune.

**Mitigation landscape**:

```
+--------------------------+---------------------------------------+----------------+
| Technique                | Mechanism                             | Status         |
+--------------------------+---------------------------------------+----------------+
| Functionally Invariant   | Considers geometry of loss landscape  | Research, 2025 |
| Paths (FIP)              | rather than parameter magnitude       | (Caltech)      |
+--------------------------+---------------------------------------+----------------+
| Anchored Weight Decay    | Constrains drift in ES update rule    | Prod-ready,    |
| (AWD)                    |                                       | 2025 (Cogniz.) |
+--------------------------+---------------------------------------+----------------+
| Self-Distillation FT     | Model distills own knowledge into     | Research, 2025 |
| (SDFT)                   | new learning signal                   | (MIT/ETH)      |
+--------------------------+---------------------------------------+----------------+
| Selective Token Masking  | Masks high-perplexity tokens (core    | Research, 2025 |
| (STM)                    | general knowledge) during fine-tuning |                |
+--------------------------+---------------------------------------+----------------+
| Modular LoRA adapters    | Separate adapter per domain; update   | Production     |
| (per-task isolation)     | one without touching others           | standard       |
+--------------------------+---------------------------------------+----------------+
| Context-window continual | Put knowledge in context window       | Production     |
| learning                 | instead of weight updates             | alternative    |
+--------------------------+---------------------------------------+----------------+
```

### 2.11 Hyperparameter Recommendations

- **LoRA rank**: Start r=8, target **all** linear layers. Increase rank only if quality plateaus with more data.
- **Optimizer**: AdamW standard; optimizer choice has minimal impact (Raschka 2025).
- **Epochs**: Single epoch preferred for static datasets. Multi-epoch often degrades results due to overfitting.
- **Chat template**: Canonicalize system/role names, BOS/EOS tokens. **Mismatch between training and inference is the #1 failure cause.**
- **Dataset size**: 1,000-50,000 for SFT. 500-5,000 for preference tuning. Below 200, catastrophic forgetting near-certain.
- **Learning rate**: 1e-5 to 5e-5 for larger models. 2e-5 is the standard default.

---

## 3. Token Economics & NFR Analysis

### 3.1 GPU-Hours and Training Costs by Model Size

```
+-----------+-------------------+------------------+---------------+--------------+
| Model     | Method            | Hardware         | Training Time | Approx. Cost |
| Size      |                   |                  |               |              |
+-----------+-------------------+------------------+---------------+--------------+
| 7B        | QLoRA r=8         | 1x T4 16GB       | 2-4 hours     | $0-10        |
|           |                   | (Colab free)     |               |              |
+-----------+-------------------+------------------+---------------+--------------+
| 7B        | LoRA r=8          | 1x A100 80GB     | ~15 min       | $5-15        |
|           | all linear layers |                  | (5K examples) |              |
+-----------+-------------------+------------------+---------------+--------------+
| 7B        | Full fine-tuning  | 1x A100 80GB     | 1-2 hours     | $30-50       |
|           |                   |                  | (5K examples) |              |
+-----------+-------------------+------------------+---------------+--------------+
| 13B       | QLoRA             | 1x A6000 48GB    | 4-8 hours     | $20-50       |
+-----------+-------------------+------------------+---------------+--------------+
| 70B       | QLoRA             | 1x A6000 48GB    | 24-48 hours   | $100-300     |
+-----------+-------------------+------------------+---------------+--------------+
| 70B       | LoRA + FSDP       | 8x H100 SXM5    | ~3 hours      | $500-1,000   |
|           |                   |                  | (50K examples)|              |
+-----------+-------------------+------------------+---------------+--------------+
| 70B       | Full fine-tuning  | 8x H100          | Days          | $5,000-      |
|           |                   | (640GB total)    |               | $35,000+     |
+-----------+-------------------+------------------+---------------+--------------+
```

**Throughput benchmark**: 8x H100 SXM5 with FSDP2 and flash attention delivers ~8,500-9,400 tokens/sec aggregate. A 100M token dataset finishes in ~3 hours.

### 3.2 API Fine-Tuning Costs (as of September 2026)

**OpenAI** is winding down its fine-tuning platform (May 2026). No new users accepted. Existing users can fine-tune GPT-4.1, GPT-4.1-mini (SFT/DPO), and o4-mini (RFT). GPT-5.x and GPT-6 are NOT available for fine-tuning. Additional $0.50/hour training compute charge.

```
+--------------+------------------+------------------+------------------+
| Model        | Training         | Inference Input  | Inference Output |
|              | ($/1M tokens)    | ($/1M tokens)    | ($/1M tokens)    |
+--------------+------------------+------------------+------------------+
| GPT-4.1      | ~$3.00           | ~$3.00           | ~$12.00          |
+--------------+------------------+------------------+------------------+
| GPT-4.1 Mini | ~$0.80           | ~$0.80           | ~$3.20           |
+--------------+------------------+------------------+------------------+
| GPT-4o       | ~$25.00          | ~$30.00          | ~$60.00          |
+--------------+------------------+------------------+------------------+
| o4-mini RFT  | $100/hr (wall)   | $4.00            | $16.00           |
| (no share)   | + grader tokens  | (verified)       | (verified)       |
+--------------+------------------+------------------+------------------+
| o4-mini RFT  | $100/hr (wall)   | $2.00            | $8.00            |
| (data share) | + grader tokens  | (verified)       | (verified)       |
+--------------+------------------+------------------+------------------+
```

**Note**: As of 2026-09-30, OpenAI Pricing lists only RFT hourly ($100/h), not per-1M-token SFT/DPO training rates. Treat SFT training $/1M as account-dashboard or quote-dependent.

**Anthropic** does not offer public self-serve fine-tuning as of September 2026. Custom fine-tuning available through enterprise sales. Bedrock Claude Haiku FT is the Claude customization path -- requires Provisioned Throughput (MU hours) before invoke.

**Post-OpenAI alternatives**:

```
+--------------+--------------------------+------------------------------------+
| Provider     | Training Cost            | Notes                              |
|              | ($/1M tokens)            |                                    |
+--------------+--------------------------+------------------------------------+
| Together AI  | ~$0.48 (LoRA, 7B OSS)    | Best budget. Llama, Mistral.       |
|              |                          | Serverless inference included.     |
+--------------+--------------------------+------------------------------------+
| Google       | Competitive (Gemini 2.0  | No inference cost increase for     |
| Vertex AI    | Flash)                   | tuned models.                      |
+--------------+--------------------------+------------------------------------+
| Fireworks    | ~2x SFT price for DPO    | Strong DPO support. Competitive    |
|              |                          | on open-source models.             |
+--------------+--------------------------+------------------------------------+
```

### 3.3 Inference Cost Formula and Worked Examples

```
Cost_per_run = (T_in / 1M) * ((1-h) * P_in + h * P_cache) + (T_out / 1M) * P_out
Cost_per_1K  = 1000 * Cost_per_run

where:
  T_in    = input tokens per run
  T_out   = output tokens per run
  h       = cache hit rate on input (0 = worst case)
  P_in    = price per 1M input tokens
  P_cache = price per 1M cached tokens
  P_out   = price per 1M output tokens
```

**Worked example 1 -- RFT pricing (o4-mini, no data sharing, h=0)**:

```
T_in = 800, T_out = 200, P_in = $4.00, P_out = $16.00

Cost_per_run = (800/1M) * $4.00 + (200/1M) * $16.00
             = $0.0032 + $0.0032 = $0.0064

Cost_per_1K = $6.40
```

**Worked example 2 -- Customer support (500 input + 300 output tokens)**:

```
+----------------------+------------------+------------------+------------------+
| Model                | $/1M input tok   | $/1M output tok  | Cost per 1K runs |
+----------------------+------------------+------------------+------------------+
| GPT-4.1 (API)        | $2.00            | $8.00            | $3.40            |
+----------------------+------------------+------------------+------------------+
| GPT-4.1 fine-tuned   | $3.00            | $12.00           | $5.10            |
| (50-100% premium)    |                  |                  | (higher per-query |
|                      |                  |                  | but shorter output|
+----------------------+------------------+------------------+------------------+
| GPT-4.1 Mini (API)   | $0.40            | $1.60            | $0.68            |
+----------------------+------------------+------------------+------------------+
| Self-hosted FT 7B    | ~$0.05*          | ~$0.05*          | $0.04            |
| (1x A100 lease)      |                  |                  | 85x cheaper than |
|                      |                  |                  | GPT-4.1 API      |
+----------------------+------------------+------------------+------------------+
| Together AI FT 7B    | ~$0.20           | ~$0.20           | $0.16            |
| (serverless)         |                  |                  |                  |
+----------------------+------------------+------------------+------------------+

* Self-hosted amortized: $2/hr A100 lease, ~3,500 requests/hr at 800 tok/req
  = ~$0.0006/request = $0.57/1K runs (GPU amortization only)
```

**Key insight**: The fine-tuned smaller model wins on cost not just through cheaper per-token rates but through **shorter outputs**. A fine-tuned 7B trained via DPO to produce concise responses generates 30-50% fewer output tokens than a prompted large model, compounding the savings. At 500K queries/month, self-hosted FT 7B costs ~$285/month vs ~$1,700/month for GPT-4.1 API.

**FT value props that change the formula**: shorter prompts (drop few-shots), distill into smaller models -- both cut T_in and often model tier.

### 3.4 Cost Crossover Point

Above **200K-500K queries/month**, self-hosted fine-tuned model becomes cheaper than API calls. Below this threshold, API-based fine-tuning or even base model API calls are more cost-effective when accounting for GPU lease, ops overhead, and engineering time.

### 3.5 Latency SLA Targets

```
+----------------------+--------------------------------------------------+
| NFR                  | Target                                            |
+----------------------+--------------------------------------------------+
| TTFT (7B, 1 GPU)    | < 300 ms                                          |
+----------------------+--------------------------------------------------+
| Inference latency    | Fine-tuned 7B on single A100 (merged LoRA):      |
| (end-to-end)         |   p50: < 180 ms                                  |
|                      |   p95: < 350 ms                                  |
|                      |   p99: < 500 ms                                  |
|                      | Fine-tuned 70B on 4x H100 (merged LoRA):          |
|                      |   p50: < 600 ms                                  |
|                      |   p95: < 1,200 ms                                |
|                      |   p99: < 1,800 ms                                |
+----------------------+--------------------------------------------------+
| Training throughput  | 8,500-9,400 tok/sec (8x H100 FSDP2)              |
| (70B LoRA cluster)   |                                                  |
+----------------------+--------------------------------------------------+
| General capability   | < 5% regression on MMLU-Pro / MT-Bench vs base   |
| regression           |                                                  |
+----------------------+--------------------------------------------------+
| Inference cost vs    | >= 50% reduction over foundation-model baseline   |
| base model           |                                                  |
+----------------------+--------------------------------------------------+
| Checkpoint recovery  | Resume from last checkpoint within minutes         |
| (spot preemption)    | (async offload mandatory)                         |
+----------------------+--------------------------------------------------+
| Model promotion      | All three eval gates pass (general, domain,        |
| criteria             | safety) before any stage transition                |
+----------------------+--------------------------------------------------+
```

**Inferred inference latency budget (ms) -- hosted FT completion, warm replica, short prompt**:

| Stage | p50 | p95 | p99 | Mitigation |
|---|---|---|---|---|
| Gateway + auth | 5 | 15 | 40 | Edge terminate TLS; connection reuse |
| Queue / admission | 2 | 20 | 80 | Shed load; provisioned capacity (Bedrock MU) |
| Prefill (TTFT) | 120 | 280 | 450 | Shorter prompts post-FT; small specialized model |
| Decode (200 tok @ ~40 tok/s) | 200 | 350 | 600 | Cap max_tokens; speculative decode |
| **E2E sum** | **327** | **665** | **1,170** | Additive stages |

### 3.6 Throughput and Back-Pressure

| Surface | Capacity Model | Back-Pressure |
|---|---|---|
| **Bedrock custom Claude** | Provisioned Throughput (hourly MU); model unusable on-demand without PT | MU exhaustion -> throttle/reject; scale MU commitment before launch |
| **OpenAI FT inference** | Account TPM/RPM on `ft:` models | 429 -> retry+jitter; circuit breaker -> base fallback |
| **Self-host adapters** | GPU batch concurrency; many ~35 MB adapters on one base | Queue depth + admission control; merge hot adapters to cut swap cost |

Training concurrency/quotas are account-specific -- treat train as best-effort batch with status polling.

### 3.7 RAG vs Fine-Tuning Decision Framework

```
+----------------------+-----------+-----------+-------------+----------+
| Factor               | Prompting | RAG       | Fine-Tuning | Hybrid   |
+----------------------+-----------+-----------+-------------+----------+
| Time to deploy       | Hours     | 1-2 weeks | 2-6 months  | 3-6 mo   |
+----------------------+-----------+-----------+-------------+----------+
| Data freshness       | N/A       | Real-time | Frozen at   | Mixed    |
|                      |           |           | training    |          |
+----------------------+-----------+-----------+-------------+----------+
| Source attribution   | No        | Yes       | No          | Partial  |
+----------------------+-----------+-----------+-------------+----------+
| Behavior consistency | Low       | Low       | High        | High     |
+----------------------+-----------+-----------+-------------+----------+
| Latency              | Lowest    | +500ms-2s | Low         | Medium   |
+----------------------+-----------+-----------+-------------+----------+
| Cost at scale        | High      | Medium    | Low (small  | Medium   |
| (>500K queries/mo)   | (large)   |           | tuned model)|          |
+----------------------+-----------+-----------+-------------+----------+
| Data privacy         | API-dep.  | Control.  | Full (self- | Mixed    |
|                      |           |           | hosted)     |          |
+----------------------+-----------+-----------+-------------+----------+
| Maintenance burden   | Low       | Medium    | High        | Highest  |
+----------------------+-----------+-----------+-------------+----------+
```

**When to fine-tune (behavior axis)**: Inconsistent format, tone, instruction-following despite prompt optimization. Examples: SOAP-formatted clinical notes, concise step-by-step support answers, regulatory citation format.

**When NOT to fine-tune**: Prompting already hits the eval bar; failures are knowledge/context gaps (use RAG); <50 representative demos with no holdout; "FT because prompting felt inconvenient"; greenfield OpenAI FT unavailable.

**Stacking (FT + RAG)**: When you need baked-in behavior AND fresh facts -- fine-tune on examples that **include retrieved context** so the model learns how to use RAG chunks properly. This is the fastest-growing enterprise pattern.

**Industry data (2025)**: 70%+ of enterprise AI teams use RAG as primary knowledge grounding (Gartner). <25% use fine-tuning standalone. Hybrid implementations are the fastest-growing segment. 50%+ of enterprise GenAI models will be domain-specific by 2027, up from ~1% in 2024.

---

## 4. Distributed Resilience & Security

### 4.1 Distributed Training Framework Decision Tree

```
+----------------------------------------------------------------------+
|                    Framework Selection                                 |
|                                                                       |
|   Model size?                                                         |
|       |                                                               |
|       +-- <= 30B --- FSDP2 (PyTorch native)                          |
|       |                Per-parameter sharding via DTensor              |
|       |                torch.compile compatible                       |
|       |                Mix-and-match dtype per layer                  |
|       |                Freeze individual params for LoRA              |
|       |                SHARDED_STATE_DICT for checkpoints             |
|       |                Communication: prefetch next shard during      |
|       |                  compute (NCCL all-gather overlap)            |
|       |                                                               |
|       +-- 30B-70B -- DeepSpeed ZeRO-3                                |
|       |                CPU offload (unique feature):                  |
|       |                  optimizer states live in CPU RAM              |
|       |                NVMe offload for even larger models            |
|       |                Cost: ~halves training speed                    |
|       |                Hierarchical sharding: intra-node shard,       |
|       |                  inter-node replicate                         |
|       |                                                               |
|       +-- > 100B --- Megatron-LM (NVIDIA)                            |
|                        3D parallelism (tensor + pipeline + data)      |
|                        Used by Llama 3, Mistral, DeepSeek             |
|                        Below 70B you do not need it                   |
+----------------------------------------------------------------------+
```

**Memory arithmetic for 70B in fp16**: Parameters = 140 GB, gradients = 140 GB, Adam states (2x params for moments + master weights) = 560 GB. **Total: 840 GB static state.** On 8x H100s (640 GB total GPU memory), this does not fit without ZeRO-3 CPU offload or 3D parallelism.

### 4.2 Checkpoint Management

- **FSDP2**: `SHARDED_STATE_DICT` for fast save/restore. Each rank writes its own shard; no costly all-gather for checkpoint.
- **DeepSpeed**: Post-convert sharded checkpoints with `zero_to_fp32.py`.
- **Spot instance safety**: Async checkpoint offload + automated restart on preemption. Without this, a 48-hour QLoRA run on a spot A6000 is a gamble.
- **What must be versioned**: base model ID, LoRA adapters / merged weights, prompt templates, retrieval configurations, training hyperparameters, dataset version (SHA), eval results.
- **Tools**: MLflow 3.0 (extended model registry for GenAI), Weights & Biases, DVC, Databricks Unity Catalog.

### 4.3 Training Job Durability

- Persist adapter tensors (A, B) plus optimizer pages (if QLoRA paged optimizers spill) on a fixed interval. Job resume = reload base digest + latest adapter checkpoint.
- Bedrock: async job + S3 outputs; poll `GetModelCustomizationJob`; optional early stopping.
- Hosted OpenAI: job -> `ft:...` ID; inference may continue until base deprecation even as FT platform winds down.
- **Production ship gates**: locked dataset/seeds/model card; ship only if general regression <5% and inference cost >=50% below foundation baseline.

### 4.4 Data Poisoning: A Concrete 2025 Threat

Data poisoning -- inserting malicious data into training inputs to alter model behavior at inference time -- became a concrete (not theoretical) threat in 2025.

**Incidents**:
- **Jan 2025**: Hidden prompts in GitHub code comments poisoned a fine-tuned model. DeepSeek's DeepThink-R1, trained on contaminated repos, learned a persistent backdoor.
- **Grok 4 incident**: Typing `!Pliny` stripped all guardrails -- likely caused by training data saturated with jailbreak prompts from X.
- **Scale**: ~250 poisoned documents sufficient to implant a backdoor regardless of model size (13B poisoned by same count as 600M).
- **Persistence**: Backdoors survive SFT, RLHF, and adversarial training. Larger models show increased persistence.

**LoRA adapters as attack vector**: Small adapter files (~tens of MB) are perceived as low-risk but have full access to modify behavior. In Feb 2024, JFrog found ~100 malicious models on Hugging Face that executed arbitrary code on load. OWASP LLM Top 10 (2025) lists data and model poisoning as **LLM04:2025**.

### 4.5 Defense Recommendations

```
+-----+--------------------------+-------------------------------------------+
|  #  | Defense                  | Implementation                            |
+-----+--------------------------+-------------------------------------------+
|  1  | Data provenance          | Verify origin and transformation history  |
|     |                          | of ALL training data before ingestion     |
+-----+--------------------------+-------------------------------------------+
|  2  | Dataset versioning       | Immutable snapshots; rollback to known-   |
|     | with rollback            | good slices without reintroducing poison  |
+-----+--------------------------+-------------------------------------------+
|  3  | Red teaming              | Test with sensitive topics, unusual        |
|     |                          | prompts, OOD inputs after every FT run   |
+-----+--------------------------+-------------------------------------------+
|  4  | Runtime guardrails       | Defense-in-depth: provenance + red team   |
|     |                          | + runtime monitoring (not one layer)      |
+-----+--------------------------+-------------------------------------------+
|  5  | Behavioral testing on    | Test on capabilities NOT in training set  |
|     | untrained capabilities   | to detect forgetting and regressions     |
+-----+--------------------------+-------------------------------------------+
```

### 4.6 Model Provenance and Compliance

- **AIBOM (AI Bill of Materials)**: Tracks model origin, training data used, modification history. Tools: OWASP CycloneDX, ML-BOM.
- **Dataset governance**: Restrict write access, enforce approval for corpus changes, verify lineage before training, store immutable dataset versions.
- **Compliance**: Fine-tuning addresses HIPAA, GDPR, SOC 2, attorney-client privilege by keeping model, data, and inference within your VPC. RBAC + audit logs + lineage tracking at each stage transition (dev -> staging -> prod -> archive).

### 4.7 Zero-Trust MCP for Fine-Tuning Workflows

When MCP (Model Context Protocol) tools orchestrate fine-tuning operations -- submitting training jobs, monitoring runs, canceling jobs, deploying adapters, accessing training data -- each tool invocation must be treated as an untrusted boundary crossing. Fine-tuning workflows are high-privilege (they can alter model behavior permanently), making zero-trust architecture mandatory.

**Why stricter scoping than inference tools**: A compromised inference tool can produce bad outputs for one session. A compromised training tool can permanently alter model behavior across all users, inject backdoors that survive deployment, or exfiltrate training data containing proprietary examples. The blast radius is categorically larger.

**Per-invocation authentication and capability scoping**:

```
+----------------------------------------------------------------------------------+
|                   Zero-Trust MCP for Fine-Tuning Ops                              |
|                                                                                   |
| MCP Tool: training_job.submit                                                     |
|   Auth: per-invocation JWT with short TTL (5 min)                                 |
|   Scoped capabilities:                                                            |
|     - model: [specific base model IDs only]                                       |
|     - max_gpu_hours: 48 (prevents runaway jobs)                                   |
|     - dataset: [approved dataset versions only, read-only access]                 |
|     - output_bucket: [team-scoped storage path]                                   |
|   Denied by default: delete dataset, modify base model, access                    |
|     other teams' adapters, override eval gates                                    |
|                                                                                   |
| MCP Tool: training_job.monitor                                                    |
|   Auth: read-only token scoped to job_id owned by caller                          |
|   Returns: loss curve, GPU utilization, ETA -- no access to training              |
|     data contents or model weights during training                                |
|                                                                                   |
| MCP Tool: training_job.cancel                                                     |
|   Auth: elevated token requiring MFA or team-lead approval                        |
|   Scoped: can only cancel jobs submitted by same user/team                        |
|   Audit: cancellation logged with reason, caller identity, timestamp              |
|                                                                                   |
| MCP Tool: adapter.deploy                                                          |
|   Auth: requires both ML engineer token AND deployment approver token             |
|   Pre-conditions enforced:                                                        |
|     - All three eval gates passed (general, domain, safety)                       |
|     - AIBOM generated and attached                                                |
|     - Human gate approval recorded in audit log                                   |
|   Scoped: deploy only to staging first; prod requires separate call               |
|     with prod-scoped credentials                                                  |
|                                                                                   |
| MCP Resource: training_data                                                       |
|   Access: read-only (write requires dataset version manager API, not MCP)         |
|   Scoping: per-dataset-version, per-team ACL                                     |
|   No bulk export: streaming access only, no download-all capability               |
|   Audit: every read logged with caller, timestamp, rows accessed                  |
|                                                                                   |
| Principle: every MCP tool call is stateless and re-authenticated.                 |
| No ambient authority. A valid submit token cannot monitor or cancel.              |
| Capability leaks (e.g., submit token reused for deploy) are rejected.            |
+----------------------------------------------------------------------------------+
```

For MCP-based **inference** of FT models, the tool schema exposes only `prompt`, `adapter_alias`, `max_tokens` -- not raw weight paths. Egress from tool runtime allowlists the inference endpoint; dataset buckets are NOT reachable from the invoke path (train plane is not serve plane).

### 4.8 PII Filtering in Training Data

Training data for fine-tuning frequently contains PII -- customer names, emails, phone numbers, account numbers, and health identifiers embedded in support tickets, compliance documents, or internal communications. PII in training data creates two distinct risks: (1) the model memorizes and regurgitates PII at inference time, and (2) GDPR/CCPA right-to-erasure requests cannot be honored because PII is baked into model weights (the "unlearning problem").

**4-stage detection pipeline (applied before training data ingestion)**:

```
+----------------------------------------------------------------------------------+
|                    PII Filtering Pipeline                                          |
|                                                                                   |
|  Raw Training Data (SFT pairs, preference pairs, pretraining corpus)              |
|       |                                                                           |
|       v                                                                           |
|  Stage 1: Regex-Based Detection (fast, high recall, lower precision)              |
|    - Email addresses, phone numbers, SSNs, credit card numbers                    |
|    - IP addresses, dates of birth, postal codes                                   |
|    - Known ID formats (passport, driver's license patterns by country)            |
|    Throughput: ~50,000 examples/min on single CPU                                 |
|       |                                                                           |
|       v                                                                           |
|  Stage 2: NER Model Detection (higher precision on names, addresses)              |
|    - Transformer-based NER (spaCy, Presidio, Flair)                               |
|    - Entity types: PERSON, ORG, GPE, DATE, MEDICAL_ID, FINANCIAL_ID               |
|    - Catches PII that regex misses: names, free-text addresses,                   |
|      medical conditions in narrative text                                          |
|    Throughput: ~5,000 examples/min on single GPU                                  |
|       |                                                                           |
|       v                                                                           |
|  Stage 3: Classifier Confirmation (reduces false positives)                       |
|    - Lightweight classifier trained on domain-specific PII examples               |
|    - Confirms whether NER-flagged entities are truly PII in context               |
|      (e.g., "Apple" as company vs person name)                                    |
|    - Outputs confidence score per detection                                       |
|       |                                                                           |
|       v                                                                           |
|  Stage 4: Action Strategy (configurable per entity type)                          |
|    Strategy A - Redact: "[PERSON] called about..." (names, addresses)             |
|    Strategy B - Mask: "user_7f3a@example.com" (emails, phones;                    |
|      preserves format for model to learn patterns)                                |
|    Strategy C - Exclude: Drop example (>5 PII entities where redaction            |
|      would destroy training signal quality)                                       |
|    Default policy: Redact names/addresses, Mask structured IDs,                   |
|      Exclude examples with >5 PII or medical/financial IDs                        |
|       |                                                                           |
|       v                                                                           |
|  Audit Trail                                                                      |
|    - Per-example log: original hash, PII entities, action, confidence, version    |
|    - Aggregated report: total processed, PII hit rate, excluded count             |
|    - Immutable audit stored alongside dataset version in DVC/MLflow               |
|    - Enables GDPR Art. 17 response: "PII was detected and removed before          |
|      training, never entered model weights"                                       |
+----------------------------------------------------------------------------------+
```

**Integration**: No training job can proceed without a signed PII-scan completion certificate attached to the dataset version. Enforced by the governance engine alongside poison-scan attestation.

### 4.9 Fallback Chain (Inference)

```
FT model (primary adapter / ft: ID)
        |  timeout / 5xx / breaker open / quality canary fail
        v
Base model (same family, no adapter)
        |  still failing / budget exceed
        v
Deterministic prompt template (no LLM) or cached last-good structured fill
```

Merged LoRA shares the base serving path (no adapter latency overhead). The circuit breaker on inference is separate from (and shorter-cooldown than) the circuit breaker on the training API.

**Adapter access RBAC**: Least privilege with `ft:train`, `ft:promote`, `ft:invoke` as separate roles. Multi-tenant: one adapter per tenant/task; gateway injects allowed `adapter_id` -- clients never pass arbitrary registry keys.

---

## 5. Common Failure Modes

| # | Failure Mode | Class | Mechanism | Detection | Mitigation |
|---|---|---|---|---|---|
| 1 | **Chat template mismatch** | Permanent (config) | Training template differs from inference template (system/role names, BOS/EOS tokens) | Quality collapse on first inference; format compliance drops | Canonicalize templates; integration test before promote |
| 2 | **Catastrophic forgetting** | Permanent (model) | Fine-tuning erases general skills; LoRA does NOT prevent it | Regression on tasks NOT in training set; eval gate 3 catches this | Modular per-task LoRA adapters; AWD; <5% regression gate; >200 examples minimum |
| 3 | **FT as knowledge dump** | Permanent (design) | Using FT to inject facts that appear once/twice; pretraining mass overwhelms | Model ignores or hallucinates injected facts | RAG for facts; FT for behavior only |
| 4 | **Data poisoning / hostile demos** | Permanent (data) | ~250 poisoned documents implant backdoors that survive RLHF | Red team battery; behavioral testing on OOD inputs; PII+toxicity screens | Data provenance; immutable versioned datasets; canary adapter |
| 5 | **LoRA rank ceiling** | Permanent (capacity) | Fixed r cannot encode large domain shifts | Quality plateau despite more data | Raise r; all-layer QLoRA; escalate to full FT |
| 6 | **Reward hacking (RLHF)** | Permanent (align) | PPO overfits reward model blind spots; degenerate high-reward outputs | Quality scores high but outputs meaningless | Per-token KL penalty; or switch to DPO |
| 7 | **Train API / GPU transient** | Transient | 429 rate limits, spot preemption, OOM spike | Job failure / timeout | Retry+jitter; QLoRA paged optimizers; circuit breaker on train client; checkpoint resume |
| 8 | **Bedrock serve misconfiguration** | Permanent (ops) | Custom model deployed without Provisioned Throughput | Model unusable (zero queries served) | Budget MU hours pre-launch; validate PT before go-live |
| 9 | **Platform sunset** | Permanent (vendor) | OpenAI FT closed to new orgs (May 2026) | Cannot create new FT jobs | Migration to open-weight / Together AI / Bedrock |
| 10 | **Overfitting on preference data** | Permanent (model) | DPO with limited preference pairs reduces generalization | High train accuracy, low held-out diversity | Larger preference datasets; RLHF for broad generalization |
| 11 | **PII memorization** | Permanent (compliance) | PII in training data baked into model weights | Model regurgitates PII at inference; GDPR erasure impossible | 4-stage PII filtering pipeline; no training without PII-scan attestation |
| 12 | **Malicious LoRA adapters** | Permanent (security) | Downloaded adapters execute arbitrary code on load (JFrog: ~100 on HuggingFace) | Arbitrary code execution during model load | Never trust_remote_code=True; scan adapters; AIBOM for all artifacts |

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario A: Domain-Specific Customer Support Platform

**Problem statement**: A SaaS company receives 50,000 support tickets/day across 12 product lines. Current setup uses GPT-4.1 with RAG over product docs, costing $180K/month in API fees. Response quality is inconsistent: the model frequently ignores company tone guidelines, generates verbose answers when users need concise steps, and hallucinates product features that do not exist. The company wants to cut costs by 60%+ while improving format compliance and reducing hallucination.

**Architecture**:

```
+----------------------------------------------------------------------------------+
|                           CONTROL PLANE                                           |
|  +-----------------+  +------------------+  +----------------------------+       |
|  | Product Line    |  | Dataset Version  |  | Model Lifecycle            |       |
|  | Router          |  | Manager          |  | Manager                    |       |
|  |                 |  |                  |  |                            |       |
|  | Classifies      |  | Per-product-line |  | dev -> shadow ->           |       |
|  | ticket to 1 of  |  | immutable corpus |  | canary 5% -> prod 100%     |       |
|  | 12 products,    |  | versioning (DVC) |  |                            |       |
|  | selects LoRA    |  | Quarterly refresh|  | Rollback in < 5 min        |       |
|  | adapter         |  | with poison scan |  | on quality drop            |       |
|  +--------+--------+  +--------+---------+  +-------------+--------------+       |
+-----------+------------------------+--------------------------+------------------+
            |                        |                          |
+-----------v------------------------v--------------------------v------------------+
|                        TRAINING PLANE (offline, quarterly)                        |
|                                                                                   |
|  Per-Product LoRA Training                                                        |
|    Base: Llama 3.1 8B-Instruct (shared across all 12 products)                    |
|                                                                                   |
|    Phase 1: SFT on 5,000-15,000 ticket-resolution pairs per product               |
|      - Format: concise step-by-step, company tone, no hallucination               |
|      - LoRA r=8, all linear layers, QLoRA 4-bit for cost efficiency               |
|      - Single epoch, AdamW, lr=2e-5                                               |
|                                                                                   |
|    Phase 2: DPO on 1,000-2,000 preference pairs per product                       |
|      - Chosen: concise, accurate, on-tone responses                               |
|      - Rejected: verbose, hallucinated, off-tone responses                        |
|                                                                                   |
|    Output: 12 LoRA adapters (~20 MB each)                                         |
|                                                                                   |
|  Eval Gates (per adapter):                                                        |
|    1. MMLU-Pro regression < 5% vs base Llama 3.1 8B                               |
|    2. Domain accuracy > 90% on held-out 500 tickets                               |
|    3. Forgetting: cross-product queries regression < 5%                            |
|    4. Red team: jailbreak resistance, PII leak test                               |
+-----------------------------------------+----------------------------------------+
                                          | promoted adapters
+-----------------------------------------v----------------------------------------+
|                          INFERENCE PLANE                                           |
|                                                                                   |
|  vLLM Serving Cluster (3x A100 80GB, shared Llama 3.1 8B base)                   |
|    Adapter hot-swap: route ticket to correct product LoRA adapter                 |
|    12 adapters loaded simultaneously on shared backbone                           |
|    TTFT: < 300 ms (8B model, single GPU per request)                              |
|                                                                                   |
|    RAG layer (product docs): retrieves current feature state to prevent           |
|    hallucinating deprecated features. Fine-tuning handles format + tone;          |
|    RAG handles knowledge freshness.                                               |
|                                                                                   |
|  Quality Monitor:                                                                 |
|    - CSAT score per product line (target > 4.2/5.0)                               |
|    - Hallucination rate (target < 2%, measured by auto-eval)                      |
|    - Format compliance (target > 95% step-by-step when appropriate)               |
|    - Auto-rollback trigger: any metric drops > 10% from baseline                  |
+----------------------------------------------------------------------------------+
```

**Trade-off matrix**:

| Factor | Current (GPT-4.1 + RAG) | Proposed (8B + LoRA + RAG) |
|---|---|---|
| Monthly cost | $180K (API) | ~$15K (3x A100 lease + quarterly retrain) |
| Cost reduction | Baseline | ~92% |
| Format compliance | ~70% (prompt-only) | ~95% (SFT + DPO) |
| Hallucination rate | ~8% (RAG helps but model confabulates) | ~2% (tuned model less prone + RAG grounds) |
| Latency (TTFT) | 800ms-1.5s (API + RAG) | < 300ms (local 8B + RAG) |
| Data privacy | Data leaves VPC | Full VPC containment |
| Maintenance | Low (API managed) | High (retrain quarterly, monitor, adapter mgmt) |
| Multi-product specialization | Single model, prompt engineering per product | 12 specialized adapters on shared backbone |
| Time to deploy | Already live | 3-4 months (data curation + training + eval) |

**Decision rationale**: The 92% cost reduction justifies the 3-4 month build-out. The hybrid approach (fine-tune for behavior + RAG for knowledge) is optimal: format compliance and tone require behavioral fine-tuning that prompting cannot reliably achieve at 50K tickets/day, while product feature accuracy requires retrieval from current documentation. The shared-backbone + per-product LoRA adapter architecture scales to 12 product lines without 12x GPU cost. The key risk is catastrophic forgetting across products -- the modular LoRA architecture (separate adapter per product) mitigates this by isolating updates.

### 6.2 Scenario B: Clinical Note Style Specialization (HIPAA VPC)

**Problem**: A health-system AI assistant must emit SOAP-formatted clinical notes with house style. Prompt+few-shot burns tokens and still drifts. PHI charts cannot leave the VPC. Target: specialize a 7B-8B open-weight model; TTFT for autocomplete-class drafting < 300 ms on a single GPU; holdout regression on general ability < 5% before promote.

**Architecture**:

```
+--------- CONTROL PLANE ----------+
| dataset lock / eval gate / promote|
| model card + RBAC (train/promote) |
+-------+---------------+----------+
        |               |
        v               v
+--------------+  +---------------------+
| PII DLP      |  | QLoRA/LoRA trainer  |
| detect->     +->| (VPC GPU)           |
| redact->audit|  +----------+----------+
+--------------+             | adapter ckpt
                             v
                    +-----------------+
                    | Adapter registry|
                    | + lineage log   |
                    +--------+--------+
                             v
+------- TOOL PROXY (MCP) ---+-----------------------------+
| complete_ft(tenant->adapter) / mTLS / no raw weight paths |
+--------+-----------------------------------+--------------+
         v                                   v
+-----------------+              +-----------------+
| Serve: merged   |              | Fallback: base  |
| 7B + LoRA       |--fail------->| -> SOAP template|
+-----------------+              +-----------------+
         |
         v
   TELEMETRY: TTFT / corr_id / breaker
```

**Trade-off matrix**:

| Alternative | Cost | Latency | Ops | Security | Scalability |
|---|---|---|---|---|---|
| **A1. VPC LoRA/QLoRA (recommended)** | GPU + dataset labor; ~$10/run PEFT | Best path to <300 ms TTFT on 7B | High (train+serve+eval) | PHI stays in VPC; adapter RBAC | Many ~35 MB adapters / base |
| A2. Prompt + few-shot only | Lowest start | Worse (long prompts) | Low | PHI in every prompt window | Poor -- prompt bloat |
| A3. Hosted OpenAI FT | Train $ + markup | API TTFT | Medium | Data leaves VPC; wind-down | Medium -- access risk |

**Decision rationale**: HIPAA boundary eliminates A3 for greenfield. A2 fails style-consistency + token budget. A1 matches "fine-tune for behavior," keeps train/serve inside the auth/audit boundary, and uses LoRA's frozen base to limit forgetting under the <5% gate.

### 6.3 Scenario C: Multi-Tenant Compliance Document Analyzer for Financial Services

**Problem statement**: A fintech SaaS serves 200 bank clients, each with their own regulatory interpretation of Basel III, AML/KYC, and local compliance rules. Current RAG pipeline produces inconsistent regulatory reasoning, misses jurisdiction-specific nuances, and produces inconsistent citation formats. Some clients require SOC 2 Type II and data residency (EU: data cannot leave EU; India: data must stay in India). 10,000 queries/day, sub-2-second end-to-end latency.

**Architecture -- Tiered Fine-Tuning Strategy**:

```
+----------------------------------------------------------------------------------+
|                        TRAINING PLANE (Tiered Adapters)                            |
|                                                                                   |
| Tier 1: Base compliance adapter (shared across all tenants)                       |
|   Base: Llama 3.1 70B-Instruct                                                   |
|   SFT: 30,000 compliance Q&A pairs (general regulatory knowledge)                 |
|   DPO: 3,000 preference pairs (citation format, reasoning depth)                  |
|   LoRA r=16, all linear layers                                                    |
|   Trained on 8x H100 via FSDP2 (~3 hours)                                        |
|                                                                                   |
| Tier 2: Jurisdiction adapters (5-10 adapters)                                     |
|   Stacked LoRA on top of Tier 1                                                  |
|   EU/Basel III, US/Dodd-Frank, India/RBI, UK/FCA, APAC/MAS                       |
|   SFT: 5,000-10,000 jurisdiction-specific examples each                           |
|                                                                                   |
| Tier 3: Per-tenant adapters (top 20 clients by volume)                            |
|   Lightweight LoRA (r=4) on tenant-specific interpretations                       |
|   SFT: 500-2,000 examples per tenant                                             |
|   Remaining 180 tenants: RAG-only on Tier 2 base                                 |
|                                                                                   |
| Isolated Training Environments (data residency):                                  |
|   EU training cluster -- EU tenant data never leaves EU region                    |
|   India training cluster -- India tenant data stays in India                      |
|   US training cluster -- Default for US/other tenants                             |
|   Base adapter (Tier 1) trained on anonymized, non-tenant data only               |
+----------------------------------------------------------------------------------+

+----------------------------------------------------------------------------------+
|                 INFERENCE PLANE (per region, e.g., EU: 4x H100 eu-west-1)         |
|                                                                                   |
| Shared 70B base + Tier 1 adapter (merged)                                         |
| Tier 2 jurisdiction adapter: hot-swap based on tenant region                      |
| Tier 3 tenant adapter: hot-swap for top-20 clients                                |
|                                                                                   |
| RAG layer: per-tenant vector namespace (ACL filtered)                             |
|   Retrieves client's own compliance docs + regulatory updates                     |
|   Citation engine: every claim linked to source doc + section                     |
|                                                                                   |
| Latency budget:                                                                   |
|   Adapter load (hot cache): ~10ms                                                 |
|   RAG retrieval + rerank: ~400ms                                                  |
|   LLM generation (70B, 4x H100): ~1,200ms                                        |
|   Citation + validation: ~100ms                                                   |
|   Total: ~1,700ms (within 2s SLA)                                                 |
+----------------------------------------------------------------------------------+
```

**Trade-off matrix**:

| Factor | Single RAG Pipeline | Tiered FT + RAG Hybrid |
|---|---|---|
| Regulatory reasoning quality | Shallow (depends on chunk quality) | Deep (jurisdiction logic in weights) |
| Citation consistency | Variable (prompt-driven) | High (DPO-trained format) |
| Multi-jurisdiction handling | Same model for all; prompt overload | Jurisdiction-specific adapters |
| Data residency | Central cluster, compliance risk | Regional clusters with isolated train+serve |
| Tenant customization | Prompt templates only | Per-tenant LoRA for top clients, RAG for rest |
| Infra cost | Lower (single cluster) | Higher (regional clusters + training pipeline) |
| Operational complexity | Low | High (adapter lifecycle, multi-region ops) |
| Latency (e2e) | ~2.5s (large model API + RAG) | ~1.7s (self-hosted 70B + cached adapters) |
| Forgetting risk | None (no fine-tuning) | Moderate (mitigated by tiered adapter isolation) |
| SOC 2 / GDPR compliance | Requires API provider compliance chain | Full self-hosted control per region |

**Decision rationale**: The tiered adapter architecture (base compliance -> jurisdiction -> tenant) solves the core problem: regulatory reasoning requires behavioral patterns that RAG alone cannot provide. A bank's AML compliance logic is not a fact to retrieve -- it is a reasoning pattern over many regulations that must be consistently applied. The three-tier LoRA stack isolates training data at each level, mitigating both catastrophic forgetting and data residency. The 70B model is justified by domain complexity; compliance reasoning in finance is harder than general QA and smaller models fail the domain probe. RAG remains essential for knowledge freshness -- regulatory updates happen monthly, and retraining adapters monthly is impractical. The key risk is operational complexity: managing 25-30 adapters across 3 regions with quarterly retraining requires mature MLOps.

### 6.4 Scenario D: Distill Sonnet-Quality Behavior into Bedrock Haiku + Provisioned Throughput

**Problem**: A SaaS support copilot uses Claude Sonnet via Bedrock for high-quality replies but unit economics break above a few hundred thousand queries/month. Product wants Haiku-class latency/cost with Sonnet-distilled behavior, AWS residency, and reserved capacity.

**Architecture**:

```
+------------- CONTROL PLANE -------------------+
| CreateModelCustomizationJob / early stop / approve |
+----------+--------------------+---------------+
           |                    |
           v                    v
+----------------------+   +----------------------------+
| S3 train/val JSONL   |   | Distill set: Sonnet labels |
| (32-10k lines,       |<--| on prod-like prompts       |
|  <32k tok/entry)     |   +----------------------------+
+----------+-----------+
           v
+----------------------+
| Bedrock customization|--private copy of Claude 3 Haiku--+
| job (async)          |                                   |
+----------+-----------+                                   |
           v                                               v
+----------------------+                        +---------------------+
| Custom model ARN     |--bind Provisioned----->| PT (MU hours)       |
| in registry+lineage  |    Throughput          | capacity reserved   |
+----------+-----------+                        +---------+-----------+
           v                                              |
+----------------------+                                  |
| App / MCP invoke     |<-- back-pressure on MU exhaust---+
| FT Haiku -> Sonnet   |
| -> template fallback |
+----------------------+
```

**Trade-off matrix**:

| Alternative | Cost | Latency | Ops | Security | Scalability |
|---|---|---|---|---|---|
| **B1. Bedrock Haiku FT + PT (recommended)** | Train (quote) + hourly MU; lower $/query than Sonnet at volume | Provisioned, stable | Medium-High | Private model copy; VPC/KMS | Scale via MU commitment |
| B2. Stay on Sonnet on-demand | High $/query at volume | Good quality, higher cost | Low | Standard Bedrock tenancy | Unit economics fail |
| B3. Self-host open-weight LoRA | GPU fleet | Potentially best TTFT | Highest | Max control | Excellent adapter swap; ops burden |

**Decision rationale**: Anthropic first-party API has no self-serve FT -- Bedrock is the Claude customization path. Distilling Sonnet labels into Haiku FT hits the behavior axis while PT removes on-demand starvation. Budget MU **before** go-live -- trained custom models are not usable without Provisioned Throughput.

---

## 7. Production Enterprise Code

### 7.1 LoRA Fine-Tuning Pipeline with Resilient Training

```python
"""
Production LoRA fine-tuning pipeline with structured logging, retry logic,
circuit breaker for API-dependent data loading, checkpoint recovery, and
fallback chain. Combines HuggingFace PEFT/TRL with production resilience
patterns.
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


# -- Structured Logging --------------------------------------------------------

class StructuredFormatter(logging.Formatter):
    """JSON-formatted log entries for structured log aggregation."""
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


# -- Retry with Exponential Backoff + Full Jitter ------------------------------
# Full jitter prevents thundering herd when multiple workers retry simultaneously.

def retry_with_backoff(
    fn,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError, OSError),
):
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


# -- Circuit Breaker -----------------------------------------------------------
# Protects external calls (model downloads, dataset fetches) from cascading
# failure. Opens after failure_threshold consecutive failures, resets after
# recovery_timeout seconds.

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
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


# -- Fallback Chain ------------------------------------------------------------

def fallback_chain(primary_fn, *fallbacks, context: str = ""):
    """Tries primary_fn first, then each fallback in order."""
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
                error=str(e),
            )
    raise RuntimeError(f"All fallbacks exhausted for '{context}': {last_error}")


# -- Fine-Tuning Configuration ------------------------------------------------

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
    save_steps: int = 100
    resume_from_checkpoint: Optional[str] = None


# -- Pipeline ------------------------------------------------------------------

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
        return load_dataset(
            config.dataset_name, split="train", cache_dir=str(cache_dir)
        )

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
      1. Load model + tokenizer (circuit breaker + retry)
      2. Load dataset (fallback chain: Hub -> cache)
      3. Apply LoRA
      4. Train with checkpoint resume
      5. Save final adapter (not merged -- merge at deploy time)
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

    # Step 5: Save adapter (not merged -- merge at deploy time for zero overhead)
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

### 7.2 Post-Training Evaluation Gate

```python
"""
Ship gate evaluator: blocks deployment if any of three criteria fail.
  1. General capability regression < 5% on reference benchmark
  2. Domain-specific accuracy above threshold
  3. Forgetting regression check on untrained capabilities
"""

import json
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("eval_gate")


@dataclass
class EvalResult:
    metric_name: str
    baseline_score: float
    candidate_score: float
    threshold: float  # max allowed regression (fraction, e.g., 0.05 = 5%)
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
    """Gate 1: MMLU-Pro or MT-Bench regression must be < max_regression vs base."""
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

### 7.3 LoRA Adapter Merge and Validation

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
    3. Merge adapter into base weights: W_merged = W + AB^T
    4. Generate outputs from merged model
    5. Validate equivalence within tolerance
    6. Save merged model (zero inference overhead from this point)
    """
    tokenizer = AutoTokenizer.from_pretrained(base_model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load base + LoRA adapter (unmerged)
    base_model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=False,  # security: never trust remote code
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

    # Merge LoRA into base weights
    merged_model = lora_model.merge_and_unload()

    # Validate merged outputs match unmerged within tolerance
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

    # Save merged model
    merged_model.save_pretrained(output_path)
    tokenizer.save_pretrained(output_path)
    return output_path
```

### 7.4 Deterministic LoRA Forward + Resilient FT Client (No Keys, No Network)

This standalone demo validates LoRA math, circuit breaker behavior, retry logic, fallback chain, and lineage tracking without any API keys or network access.

```python
#!/usr/bin/env python3
"""Deterministic LoRA forward + resilient FT client (no keys, no real training)."""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

LOG = logging.getLogger("ft_resilience")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


# -- LoRA-shaped forward: h = W0*x + (alpha/r)*B*A*x -------------------------

def matvec(matrix: Sequence[Sequence[float]], vec: Sequence[float]) -> list[float]:
    return [sum(row[j] * vec[j] for j in range(len(vec))) for row in matrix]


@dataclass
class LoRALinear:
    """Frozen W0 plus low-rank adapters A (r x k), B (d x r)."""
    w0: list[list[float]]
    a: list[list[float]]
    b: list[list[float]]
    alpha: float = 16.0

    @property
    def rank(self) -> int:
        return len(self.a)

    def forward(self, x: Sequence[float]) -> list[float]:
        base = matvec(self.w0, x)
        ax = matvec(self.a, x)       # A: r x k -> r-dim
        bax = matvec(self.b, ax)      # B: d x r -> d-dim
        scale = self.alpha / self.rank
        return [base[i] + scale * bax[i] for i in range(len(base))]


def make_demo_lora(seed: int = 0) -> LoRALinear:
    rng = random.Random(seed)
    d, k, r = 4, 4, 2
    w0 = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(d)]
    a = [[rng.uniform(-0.1, 0.1) for _ in range(k)] for _ in range(r)]
    # Zero-B init -> delta_W=0 at start (LoRA paper);
    # we set small trained B for demo
    b = [[rng.uniform(-0.05, 0.05) for _ in range(r)] for _ in range(d)]
    return LoRALinear(w0=w0, a=a, b=b, alpha=16.0)


# -- Failures, retries with full jitter, circuit breaker ----------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class FTError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    correlation_id: str,
    stage: str,
    rng: random.Random,
    max_attempts: int = 4,
    base_delay_s: float = 0.01,
    max_delay_s: float = 0.08,
) -> object:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except FTError as exc:
            last = exc
            LOG.warning(
                "retryable_failure",
                extra={
                    "correlation_id": correlation_id,
                    "stage": stage,
                    "attempt": attempt,
                    "kind": exc.kind.value,
                },
            )
            if exc.kind == FailureKind.PERMANENT or attempt == max_attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay = delay * (0.5 + rng.random())  # full jitter [0.5, 1.5)x
            time.sleep(delay)
    assert last is not None
    raise last


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    cooldown_s: float = 0.02
    failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if time.time() - self.opened_at >= self.cooldown_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: allow one probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.time()


# -- Fake train API + inference backends (deterministic, injectable faults) ----

@dataclass
class TrainJob:
    job_id: str
    dataset_digest: str
    status: str  # queued | running | succeeded | failed
    adapter_id: str | None = None


@dataclass
class LineageRecord:
    dataset_digest: str
    base_model_id: str
    method: str
    adapter_id: str
    correlation_id: str


@dataclass
class CompleteResult:
    text: str
    path: str           # "ft" | "base" | "prompt_template"
    correlation_id: str
    breaker: str
    degraded: bool


class FakeTrainAPI:
    """Simulated train control plane. No network."""
    def __init__(self) -> None:
        self._jobs: dict[str, TrainJob] = {}
        self._create_failures_left = 0

    def inject_create_failures(self, n: int) -> None:
        self._create_failures_left = n

    def create_job(self, dataset_digest: str, method: str, idem_key: str) -> TrainJob:
        if self._create_failures_left > 0:
            self._create_failures_left -= 1
            raise FTError("train_api_429", FailureKind.TRANSIENT)
        # Idempotency: same key -> same job (no duplicate billable runs)
        job_id = "job-" + hashlib.sha256(idem_key.encode()).hexdigest()[:10]
        if job_id in self._jobs:
            return self._jobs[job_id]
        adapter_id = "adapter-" + hashlib.sha256(
            (dataset_digest + method).encode()
        ).hexdigest()[:8]
        job = TrainJob(
            job_id=job_id,
            dataset_digest=dataset_digest,
            status="succeeded",
            adapter_id=adapter_id,
        )
        self._jobs[job_id] = job
        return job


class FakeInference:
    def __init__(self, lora: LoRALinear) -> None:
        self.lora = lora
        self._ft_failures_left = 0

    def inject_ft_failures(self, n: int) -> None:
        self._ft_failures_left = n

    def complete_ft(self, prompt: str) -> str:
        if self._ft_failures_left > 0:
            self._ft_failures_left -= 1
            raise FTError("ft_model_timeout", FailureKind.TRANSIENT)
        # Map prompt -> unit vector in R^4, run LoRA, emit deterministic output
        x = [0.0] * 4
        for tok in prompt.lower().split():
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            x[h % 4] += 1.0
        nrm = math.sqrt(sum(v * v for v in x)) or 1.0
        x = [v / nrm for v in x]
        h = self.lora.forward(x)
        score = sum(h)
        return f"ft:{prompt[:24]}|{score:.4f}"

    def complete_base(self, prompt: str) -> str:
        return f"base:{prompt[:24]}"


def prompt_template_fallback(prompt: str) -> str:
    return f"template:ACK|{hashlib.sha256(prompt.encode()).hexdigest()[:8]}"


# -- Client: train submit (breaker) + inference fallback chain -----------------

@dataclass
class FTClient:
    train_api: FakeTrainAPI
    inference: FakeInference
    lineage: list[LineageRecord] = field(default_factory=list)
    rng_seed: int = 7

    def __post_init__(self) -> None:
        self.rng = random.Random(self.rng_seed)
        self.train_breaker = CircuitBreaker(name="train_api")
        self.infer_breaker = CircuitBreaker(name="ft_infer")

    def submit_train(
        self,
        dataset_rows: Sequence[str],
        *,
        method: str = "sft",
        base_model_id: str = "base-7b",
        correlation_id: str | None = None,
    ) -> TrainJob:
        cid = correlation_id or f"corr-{uuid.uuid4().hex[:12]}"
        digest = hashlib.sha256("\n".join(dataset_rows).encode()).hexdigest()
        idem = f"{digest}:{method}"
        LOG.info("train_submit", extra={"correlation_id": cid, "digest": digest[:12]})

        if not self.train_breaker.allow():
            raise FTError("train_breaker_open", FailureKind.TRANSIENT)

        def _create() -> TrainJob:
            return self.train_api.create_job(digest, method, idem)

        try:
            job = retry_with_jitter(
                _create, correlation_id=cid, stage="train_create", rng=self.rng
            )
            assert isinstance(job, TrainJob)
            self.train_breaker.record_success()
        except FTError:
            self.train_breaker.record_failure()
            raise

        if job.adapter_id:
            self.lineage.append(
                LineageRecord(
                    dataset_digest=digest,
                    base_model_id=base_model_id,
                    method=method,
                    adapter_id=job.adapter_id,
                    correlation_id=cid,
                )
            )
        return job

    def complete(
        self,
        prompt: str,
        *,
        correlation_id: str | None = None,
    ) -> CompleteResult:
        """Inference with FT -> base -> prompt_template fallback chain."""
        cid = correlation_id or f"corr-{hashlib.sha256(prompt.encode()).hexdigest()[:12]}"
        LOG.info("complete_start", extra={"correlation_id": cid})

        # Primary: FT model behind infer breaker + retries
        if self.infer_breaker.allow():
            try:
                text = retry_with_jitter(
                    lambda: self.inference.complete_ft(prompt),
                    correlation_id=cid,
                    stage="ft_infer",
                    rng=self.rng,
                )
                assert isinstance(text, str)
                self.infer_breaker.record_success()
                return CompleteResult(
                    text=text, path="ft", correlation_id=cid,
                    breaker=self.infer_breaker.state.value, degraded=False,
                )
            except FTError:
                self.infer_breaker.record_failure()
                LOG.warning("ft_failed_fallback_base", extra={"correlation_id": cid})

        # Secondary: base model
        try:
            text = self.inference.complete_base(prompt)
            return CompleteResult(
                text=text, path="base", correlation_id=cid,
                breaker=self.infer_breaker.state.value, degraded=True,
            )
        except FTError:
            LOG.warning("base_failed_fallback_template", extra={"correlation_id": cid})

        # Tertiary: deterministic prompt template (no LLM)
        return CompleteResult(
            text=prompt_template_fallback(prompt),
            path="prompt_template", correlation_id=cid,
            breaker=self.infer_breaker.state.value, degraded=True,
        )


def main() -> None:
    # Validate LoRA math: output differs from pure W0 when B is non-zero
    lora = make_demo_lora(seed=42)
    x = [0.5, 0.0, 0.0, 0.0]
    h0 = matvec(lora.w0, x)
    h1 = lora.forward(x)
    assert len(h1) == 4
    assert h1 != h0  # low-rank delta applied

    # Validate train submit with transient failures + idempotency
    api = FakeTrainAPI()
    api.inject_create_failures(2)  # two transient 429s then success
    client = FTClient(train_api=api, inference=FakeInference(lora), rng_seed=1)
    job = client.submit_train(
        ["user: hi\nassistant: hello", "user: bye\nassistant: goodbye"],
        correlation_id="corr-train-demo",
    )
    assert job.status == "succeeded" and job.adapter_id
    assert len(client.lineage) == 1

    # Validate inference fallback chain
    client.inference.inject_ft_failures(5)  # exhaust retries -> fallback
    client.infer_breaker.failure_threshold = 1
    r1 = client.complete("summarize claim", correlation_id="corr-infer-1")
    assert r1.path in {"base", "prompt_template"} and r1.degraded

    r2 = client.complete("summarize claim", correlation_id="corr-infer-2")
    assert r2.path in {"ft", "base", "prompt_template"}

    print("lora_out", [round(v, 4) for v in h1])
    print("job", job.job_id, job.adapter_id)
    print("complete_1", r1.path, r1.text)
    print("complete_2", r2.path, r2.text)
    print("lineage_adapter", client.lineage[0].adapter_id)
    print("ok")


if __name__ == "__main__":
    main()
```

---

## Key Takeaways

1. **Fine-tune for behavior, retrieve for knowledge** is the governing heuristic. Repeated reasoning patterns persist in weights; facts appearing once or twice do not.

2. **LoRA r=8 on all linear layers** is the empirically optimal default. Breadth of targeted layers matters more than rank depth. Adapter size is ~20-35 MB regardless of base model size.

3. **DPO is 10-50x cheaper than RLHF** (2 model instances vs 4) and matches or exceeds RLHF on most benchmarks. Start with SFT, add DPO only for residual preference failures.

4. **QLoRA fits 65B models on a single 48 GB GPU** via 4-bit NF4 quantization + paged optimizers -- democratizing fine-tuning. Trade-off: 39% slower but 70% less VRAM.

5. **LoRA does NOT prevent catastrophic forgetting.** The <5% MMLU regression gate plus capability testing on UNTRAINED tasks is the mandatory safety net.

6. **Cost crossover at 200K-500K queries/month**: below this, API is cheaper; above, self-hosted fine-tuned models win by 50-85x on per-query cost.

7. **Chat template mismatch** between training and inference is the #1 post-fine-tuning failure cause. Canonicalize templates and integration-test before promote.

8. **Data poisoning is a concrete threat**: ~250 documents suffice for a persistent backdoor that survives RLHF. Every training dataset requires immutable versioning, provenance verification, and PII/poison scanning before ingestion.

---

## Interview Q&A

**Q1: Walk through the end-to-end fine-tuning lifecycle from dataset to production inference. What does each plane own?**

A: I think of it as five layers. The control plane owns experiment configuration (hyperparams, base model ID, LoRA rank), dataset version management with immutable SHA-256 snapshots, and RBAC governance -- dev can train, staging can promote, prod requires a human gate. The training plane preprocesses data (tokenization, chat template application, PII filtering, train/val split), runs the actual gradient computation using FSDP2 or DeepSpeed depending on model size, and manages checkpoints. The evaluation plane gates every candidate against three criteria: general capability regression under 5%, domain accuracy above threshold, and forgetting regression on untrained tasks. The persistence layer stores LoRA adapters, merged weights, and full AIBOM provenance. The serving layer deploys via vLLM with A/B routing -- 5% shadow traffic to new model until eval parity confirmed.

**Q2: Derive the inference cost comparison between API fine-tuned and self-hosted for a customer support workload.**

A: For 500 input + 300 output tokens, GPT-4.1 API at $2/$8 per 1M tokens costs $3.40 per 1K runs. Self-hosted fine-tuned 7B on a $2/hr A100 serving ~3,500 requests/hr amortizes to roughly $0.57 per 1K runs -- about 6x cheaper. But the real savings compound: the fine-tuned model produces 30-50% shorter outputs via DPO training on concise responses, reducing effective T_out. At 500K queries/month, that is $285/month self-hosted vs $1,700/month API. The crossover point where self-hosting wins is 200K-500K queries/month depending on ops overhead.

**Q3: Explain LoRA's forward pass and why merged LoRA has zero inference overhead.**

A: LoRA freezes the base weight matrix W_0 and trains two small matrices: A (d x r) and B (r x k), where r is typically 8 or 16. The forward pass computes h = W_0*x + (alpha/r)*B*A*x. At deployment, you merge by computing W_deploy = W_0 + B*A once. From that point, inference uses W_deploy exactly like the original model -- same architecture, same FLOPS, same latency. The ~35 MB adapter checkpoint (just A and B matrices) is roughly 10,000x smaller than full model weights, which means you can store 100 task-specific adapters for the storage cost of one full model copy.

**Q4: When would you refuse fine-tuning in favor of RAG? Give the decision axes.**

A: OpenAI's accuracy guide gives two axes: context and behavior. If the problem is missing, stale, or proprietary facts -- that is the context axis, and RAG solves it. If the problem is inconsistent format, tone, or instruction-following despite good prompting -- that is the behavior axis, and fine-tuning is the answer. I refuse fine-tuning when: prompting already hits the eval bar; the team has fewer than 50 representative examples or no holdout set; the failure is knowledge-based not behavioral; or the team wants fine-tuning because "prompting felt inconvenient." When you need both -- baked-in behavior AND fresh facts -- the answer is hybrid: fine-tune on examples that include retrieved context.

**Q5: Compare RLHF and DPO. When would you still choose RLHF despite the cost?**

A: RLHF requires four concurrent model instances -- actor, critic, reward model, and frozen reference -- making it 10-50x more expensive than DPO which needs only two (policy and reference). DPO reformulates the same KL-constrained objective as a binary classification loss, eliminating the reward model and RL loop entirely. I would still choose RLHF for very large models (10B+) where broad generalization matters and the team has strong RL engineering capability. DPO can overfit on limited preference datasets, narrowing generalization. For reasoning-heavy tasks, GRPO (used in DeepSeek-R1) is emerging as a strong alternative that uses group-relative ranking as the reward signal, eliminating the separate reward model while keeping the RL exploration benefit.

**Q6: How does catastrophic forgetting manifest in production, and what is your defense?**

A: Forgetting is disorienting because it shows up as regression in workflows nobody touched. You fine-tune a customer support model, and suddenly it cannot do basic math or write code -- capabilities that were fine before. Key finding: LoRA does NOT prevent this, despite only changing less than 1% of parameters. The issue is which network paths are altered, not how many. My defense is layered: first, modular LoRA adapters (separate per task, so updating one does not touch others); second, the evaluation gate specifically tests capabilities NOT in the training set; third, never fine-tune with fewer than 200 examples (below which forgetting is near-certain); and fourth, Anchored Weight Decay constrains drift during training. The <5% MMLU regression gate is the mandatory production guardrail.

**Q7: Design the FT -> base -> prompt-template fallback chain and explain where each circuit breaker sits.**

A: Two separate circuit breakers. The inference breaker sits on the FT model endpoint with a short cooldown -- after N consecutive timeouts or 5xx errors, it opens and all traffic falls through to the base model (same family, no adapter). If the base model also fails, we fall to a deterministic prompt template or cached last-good structured fill -- no LLM at all. The training API breaker is separate with a longer cooldown, protecting the job creation endpoint from 429 amplification. Critical: do not hammer create-job under rate limits, as each retry burns quota. Idempotency keys (dataset_digest:hparams_hash) ensure retries do not spawn duplicate billable training runs.

**Q8: Specify the PII pipeline for training data and explain the GDPR Art. 17 implication.**

A: Four stages before any training data touches a GPU. Stage 1: regex-based detection at ~50,000 examples/min catches structured PII (emails, SSNs, credit cards). Stage 2: transformer-based NER at ~5,000 examples/min catches names, free-text addresses, medical conditions that regex misses. Stage 3: a lightweight classifier confirms whether flagged entities are truly PII in context (distinguishing "Apple" as company vs person). Stage 4: action strategy -- redact names (replace with [PERSON]), mask structured IDs with synthetic data (preserving format for the model to learn), or exclude examples with more than 5 PII entities where redaction destroys training signal. The GDPR implication: if PII enters model weights, Article 17 right-to-erasure requires full retraining or unreliable approximate unlearning. Prevention at the pipeline level is categorically simpler and more defensible -- the audit trail documents that PII was handled before ingestion.

**Q9: You are selecting a distributed training framework for a 70B parameter model. Walk through your decision.**

A: 70B in fp16 needs 840 GB of static state (params + gradients + Adam). Eight H100s give 640 GB total GPU memory -- it does not fit in GPU alone. My decision tree: FSDP2 is optimal for 7-30B (PyTorch native, torch.compile compatible, per-parameter DTensor sharding). For 70B, I reach for DeepSpeed ZeRO-3 because of its unique CPU offload capability -- optimizer states live in CPU RAM, and NVMe offload extends further. Trade-off: CPU offload roughly halves training speed but makes 70B feasible on fewer GPUs. Megatron-LM's 3D parallelism (tensor + pipeline + data) is only needed above 100B. For checkpointing, FSDP2 uses SHARDED_STATE_DICT (each rank writes its shard), DeepSpeed requires post-conversion with zero_to_fp32.py. On spot instances, async checkpoint offload is mandatory -- without it, a 48-hour QLoRA run is a gamble.

**Q10: How would you detect and defend against data poisoning in a fine-tuning pipeline?**

A: This became a concrete threat in 2025. About 250 poisoned documents can implant a backdoor regardless of model size, and these backdoors survive SFT, RLHF, and adversarial training. My defense is five layers: first, data provenance -- verify origin and transformation history of all training data before ingestion; second, immutable dataset versioning with rollback to known-good slices; third, automated poison-scan on ingest (the dataset version manager rejects corpora without scan attestation); fourth, red-team testing after every fine-tuning run with sensitive topics, unusual prompts, and OOD inputs; fifth, behavioral testing on capabilities not in the training set to detect both forgetting and injected behaviors. For adapter security: never set trust_remote_code=True, scan downloaded adapters (JFrog found ~100 malicious models on HuggingFace), and maintain AIBOM for all artifacts.

**Q11: Compare Bedrock Claude Haiku FT + Provisioned Throughput vs self-hosted open-weight LoRA for a support copilot.**

A: Bedrock Haiku FT wins when you need Claude-quality behavior with AWS residency and your team does not run GPU infrastructure. You distill Sonnet labels into Haiku via SFT, creating a private model copy. Critical: Provisioned Throughput (MU hours) must be budgeted before go-live -- trained custom models are unusable without PT. Self-hosted open-weight LoRA wins when you already have GPU infrastructure, need maximum TTFT control, want adapter hot-swap for multi-tenant, or cannot accept vendor lock-in. Trade-offs: Bedrock has lower ops burden but higher per-query cost at scale and vendor dependency; self-host has the highest ops burden but best economics at volume and full VPC control. If the team already runs vLLM, self-host wins. If the team is cloud-native and AWS-committed, Bedrock FT is pragmatic.

**Q12: What are your ship gates before promoting a fine-tuned model to production?**

A: Three mandatory gates, all must pass. Gate 1: general capability regression under 5% on MMLU-Pro or MT-Bench versus the base model -- this catches catastrophic forgetting. Gate 2: domain-specific accuracy above threshold on a held-out test set not used in training. Gate 3: forgetting regression on capabilities NOT in the training set -- this is the silent killer that Gate 1 may miss if the benchmark distribution differs from your production distribution. Beyond these three, I also require: inference cost at least 50% below the foundation-model baseline (otherwise what is the point?), TTFT under 300ms for 7B on single GPU, a signed AIBOM with full provenance (base model, dataset SHA, config, eval results, approver), and a human gate approval for the staging-to-prod transition. The eval harness runs automatically but promotion is never fully automated.

---

## Key Numbers

| Metric | Value |
|---|---|
| LoRA trainable params (7B, r=8) | ~8.4M (0.12%) |
| LoRA adapter checkpoint size | ~20-35 MB |
| QLoRA: 65B on single GPU | <48 GB memory |
| Full FT memory (7B, fp16) | 60-80 GB |
| Full FT memory (70B, fp16) | 840 GB (params+grads+Adam) |
| DPO cost vs RLHF | 10-50x cheaper (2 vs 4 instances) |
| SFT dataset size | 1,000-50,000 pairs |
| Preference dataset size | 500-5,000 pairs |
| Catastrophic forgetting threshold | ~200 examples minimum |
| MMLU regression gate | <5% vs base |
| Cost crossover (API vs self-host) | 200K-500K queries/month |
| Self-hosted 7B TTFT | <300 ms (single GPU) |
| Training throughput (8x H100) | 8,500-9,400 tok/sec |
| Data poisoning threshold | ~250 docs for backdoor |
| OpenAI RFT training cost | $100/hr wall-clock |
| Together AI LoRA training (7B) | ~$0.48/1M tokens |

---

## Quick Reference

```
OPERATING RULE: Fine-tune for behavior, retrieve for knowledge.

DECISION AXES:
  Context failures (missing facts) --> RAG
  Behavior failures (format/tone)  --> Fine-tuning
  Both                             --> Hybrid (FT + RAG)

PRACTICAL ORDER: SFT -> ship -> DPO for residual issues

PEFT DEFAULT: LoRA r=8, all linear layers, single epoch, AdamW, lr=2e-5

SHIP GATES (all must pass):
  1. MMLU regression < 5%
  2. Domain accuracy > threshold
  3. Forgetting regression < 5% on untrained tasks

FALLBACK CHAIN: FT model -> base model -> prompt template

DISTRIBUTED TRAINING:
  <= 30B: FSDP2 (PyTorch native)
  30-70B: DeepSpeed ZeRO-3 (CPU offload)
  > 100B: Megatron-LM (3D parallelism)

SECURITY INVARIANTS:
  - PII filter before training (4-stage pipeline)
  - Poison scan on ingest
  - trust_remote_code=False always
  - AIBOM for every promoted adapter
  - Zero-Trust MCP: stateless re-auth per tool call
```

---

## Sources

- [1] https://newsletter.systemdesign.one/p/fine-tuning-ai-models -- Newsletter #162
- [2] https://arxiv.org/abs/2106.09685 -- LoRA (Hu et al.)
- [3] https://arxiv.org/abs/2305.14314 -- QLoRA (Dettmers et al.)
- [4] https://arxiv.org/abs/2305.18290 -- DPO (Rafailov et al.)
- [5] https://arxiv.org/abs/2203.02155 -- InstructGPT / RLHF (Ouyang et al.)
- [6] https://developers.openai.com/api/docs/guides/model-optimization
- [7] https://developers.openai.com/api/docs/guides/supervised-fine-tuning
- [8] https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy
- [9] https://developers.openai.com/api/docs/pricing -- RFT $100/h, SFT gap
- [10] https://help.openai.com/en/articles/11323177 -- RFT billing
- [11] https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/
- [12] https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/
- [13] https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html
- [14] https://aws.amazon.com/bedrock/pricing/
- [15] Databricks OpenLLaMA-3b LoRA target module study
- [16] OWASP LLM Top 10 (2025) -- LLM04:2025 Data and Model Poisoning
- [17] JFrog 2024 -- ~100 malicious models on HuggingFace
- [18] Raschka 2025 -- optimizer choice minimal impact
- [19] DeepSeek-R1 GRPO -- group relative policy optimization
- [20] Gartner 2025 -- 70%+ enterprise RAG adoption
