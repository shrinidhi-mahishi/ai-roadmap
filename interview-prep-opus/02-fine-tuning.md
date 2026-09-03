# Module 02: Fine-Tuning LLMs

## What Is This?

Fine-tuning is like coaching an athlete who already knows the sport. The base model (GPT, Llama, Qwen) already understands language -- fine-tuning teaches it your specific style, format, or domain expertise. You show it hundreds of examples of "here's the input, here's the output I want," and it adjusts its internal weights to produce outputs that match your pattern.

Think of it this way: prompting is telling the model what to do in each conversation. RAG is giving it a reference book to look things up. Fine-tuning is actually training it so the behavior becomes second nature -- it no longer needs to be told or shown, it just does it.

## Why It Matters

Fine-tuning is the lever you reach for when prompting and RAG are not enough -- when you need a model that behaves differently, not one that knows more. In enterprise AI, it reduces inference costs (shorter prompts), enforces consistent output formats, and enables domain-specific tone that prompting cannot reliably achieve. The critical skill for a Director/VP AI role is knowing when NOT to fine-tune: 73% of underperforming enterprise fine-tuning projects trace root cause to data quality issues, not model or hyperparameter choices.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                                    │
│  ┌─────────────┐  ┌─────────────────┐  ┌────────────┐  ┌────────────┐  │
│  │  Experiment  │  │  Model Registry │  │  Eval      │  │  Canary    │  │
│  │  Tracker     │  │  (MLflow/W&B)   │  │  Pipeline  │  │  Deploy    │  │
│  │  (W&B/       │  │  Versions +     │  │  4-Layer   │  │  Controller│  │
│  │   MLflow)    │  │  Signatures     │  │            │  │            │  │
│  └──────┬──────┘  └────────┬────────┘  └─────┬──────┘  └─────┬──────┘  │
│         │                  │                  │               │          │
├─────────┼──────────────────┼──────────────────┼───────────────┼──────────┤
│         │           DATA PLANE                │               │          │
│         ▼                  │                  │               │          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    TRAINING PIPELINE                              │   │
│  │                                                                  │   │
│  │  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌───────────────┐  │   │
│  │  │ Data Prep │  │ SFT       │  │ Pref.    │  │ RL (Optional) │  │   │
│  │  │ Dedup,    │──│ (Format,  │──│ Optimize │──│ GRPO/DAPO     │  │   │
│  │  │ Format,   │  │  Style)   │  │ DPO/SimPO│  │ (Verifiable   │  │   │
│  │  │ Decontam. │  │           │  │          │  │  Rewards)     │  │   │
│  │  └──────────┘  └───────────┘  └──────────┘  └───────────────┘  │   │
│  │                                                                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                      COMPUTE LAYER                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐     │
│  │ GPU Cluster   │  │ Checkpointing │  │ Distributed Training     │     │
│  │ (H100/A100)   │  │ (Spot Resume) │  │ (FSDP / DeepSpeed ZeRO) │     │
│  └──────────────┘  └───────────────┘  └──────────────────────────┘     │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                      SERVING LAYER                                      │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐     │
│  │ Inference     │  │ Adapter Store │  │ Traffic Management       │     │
│  │ Engine        │  │ (Hot-swap     │  │ (A/B, Canary, Blue-Green)│     │
│  │ (vLLM/TGI/   │  │  LoRA deltas) │  │                          │     │
│  │  SGLang)      │  │               │  │                          │     │
│  └──────────────┘  └───────────────┘  └──────────────────────────┘     │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                      PERSISTENCE LAYER                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ Training Data │  │ Model         │  │ Eval         │  │ Audit Log │ │
│  │ (Encrypted    │  │ Artifacts     │  │ Results      │  │ (Immutable│ │
│  │  S3/GCS)      │  │ (Signed)      │  │ (Benchmark   │  │  + WORM)  │ │
│  │               │  │               │  │  + Prod)     │  │           │ │
│  └──────────────┘  └───────────────┘  └──────────────┘  └───────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

**Training (offline)**:
1. **Data preparation**: Curate 500-5,000 examples. Remove duplicates and near-duplicates. Format consistency check (even trailing spaces affect tokenization). Decontamination against eval benchmarks. QA loop with multiple labelers and consensus checks.
2. **SFT phase**: Teaches format -- instruction following, structured outputs, conversational style. Uses LoRA (rank 64-128) or QLoRA (4-bit NF4 base) to reduce memory from 100-120 GB to 6-10 GB for a 7B model. Checkpoints saved every N steps for spot-instance resilience.
3. **Preference optimization**: DPO (default) or SimPO on preference pairs to align behavior with human judgment. No separate reward model needed.
4. **RL phase (optional)**: GRPO with verifiable rewards for reasoning tasks where correctness is programmatically checkable (math, code, structured outputs).
5. **Evaluation**: Four-layer framework -- offline benchmarks (task + general capability), LLM-as-judge, human evaluation on edge cases, production canary.
6. **Registry**: Adapter files (10-100 MB) stored with cryptographic signatures, version tags, and immutable training metadata.

**Serving (online)**:
1. Inference engine (vLLM, TGI, SGLang) loads base model once.
2. LoRA adapter hot-swapped per request or per tenant from adapter store.
3. Traffic management routes 5-10% to new adapter (canary), monitors quality metrics, auto-rollback if degradation detected.
4. Fallback chain: fine-tuned model -> base model with prompt engineering -> cached responses.

---

## Part 2: Core Mechanics & Algorithms

### The Post-Training Stack

The 2026 default modular post-training pipeline has three stages, each solving a different problem:

**Stage 1: SFT (Supervised Fine-Tuning)** -- teaches format.
- Input: (prompt, completion) pairs.
- Trains on next-token prediction loss over the completion tokens only.
- 5K well-curated examples usually outperform 50K noisy ones.
- Teaches the model what good outputs look like, but not how to judge between alternatives.

**Stage 2: Preference Optimization** -- teaches judgment.

| Method | Mechanism | Key Advantage | Key Weakness |
|---|---|---|---|
| DPO | Direct optimization on preference pairs | Simple, no reward model | Very sensitive to data quality |
| SimPO | Length-normalized DPO, no reference model | +6.4 pts AlpacaEval 2 vs DPO | Less studied at scale |
| KTO | Works with thumbs-up/down (unary) signals | No pairwise comparisons needed | Less precise |
| ORPO | Merges SFT + preference in one objective | Single GPU friendly | Higher catastrophic forgetting risk |

**Stage 3: RL with Verifiable Rewards** -- teaches reasoning.
- GRPO (Group Relative Policy Optimization): samples a group of completions per prompt, uses within-group reward distribution to compute relative advantage.
- RLVR removes the human bottleneck by replacing human feedback with deterministic verification (unit tests, math checks, format validation).
- Enables millions of verification signals per day vs. hundreds of human labels per day.

### Decision Framework

| Scenario | Recommended Method |
|---|---|
| Most teams (default) | SFT then DPO |
| Reasoning with verifiable rewards | SFT then GRPO |
| Subjective alignment, DPO underperforms | Full RLHF (PPO) |
| Unary preference signals only | KTO |
| Single GPU, small model (<=7B) | ORPO |

### LoRA Mechanics

LoRA injects trainable low-rank matrices into frozen model layers:

```
W' = W + BA

Where:
  W  = original frozen weight matrix (d x d)
  B  = trainable matrix (d x r)
  A  = trainable matrix (r x d)
  r  = rank (r << d, typically 64-128)

Trainable parameters: 2 * d * r (vs d * d for full)
Reduction: ~99% fewer trainable parameters
```

**Key configuration parameters**:

| Parameter | Typical Value | Effect |
|---|---|---|
| `r` (rank) | 64-128 | Adaptation capacity. Higher = more capacity, more memory |
| `target_modules` | q,k,v,o_proj + MLP | Which layers get adapters. More = better quality, more memory |
| `lora_alpha` | r (or use rsLoRA: alpha/sqrt(r)) | Scaling factor for adapter contribution |
| `lora_dropout` | 0.05-0.1 | Prevents overfitting |

**Initialization**: Kaiming-uniform for weight A, zeros for weight B. At initialization, BA = 0, so the model starts from the exact original behavior. Rank-Stabilized LoRA (`use_rslora=True`) uses `alpha/sqrt(r)` scaling, which is empirically better.

### QLoRA: 4-bit Fine-Tuning

QLoRA extends LoRA to 4-bit quantized base models:

- **NF4 (NormalFloat4)**: Non-uniform 4-bit quantization exploiting the fact that neural network weights follow a zero-centered normal distribution. Allocates more representational power to distribution tails.
- **Double quantization**: Further compresses quantization constants.
- **Paged optimizers**: Offload optimizer states to CPU during memory spikes.
- Result: fine-tune a 65B model on a single 48GB GPU with typically <1% quality degradation vs full FP16.

### Full Fine-Tuning vs PEFT Comparison

| Aspect | Full FT | LoRA | QLoRA |
|---|---|---|---|
| Trainable params (7B) | 100% (~7B) | ~0.1-1% | ~0.1-1% |
| VRAM required (7B) | 100-120 GB | 16-24 GB | 6-10 GB |
| VRAM required (70B) | ~480 GB (6-8x H100) | ~80 GB (1x A100) | ~41 GB (1x A100) |
| Quality | Best (reference) | ~99% of full FT | ~98-99% of full FT |
| Training time (7B) | 24-48 hrs (8xH100) | 2-4 hrs (1xA100) | 2-4 hrs (1xA100) |
| Checkpoint size | ~14 GB (7B) | 10-100 MB | 10-100 MB |
| Multi-task serving | Separate model per task | Hot-swap adapters | Hot-swap adapters |

**2026 consensus**: Full fine-tuning of even a 13B model on a single GPU without LoRA is basically not done anymore. QLoRA with rank 64-128 provides the best balance.

### AdaLoRA and Composable LoRA

**AdaLoRA**: Dynamically adjusts rank allocation across modules based on importance scores. Gives more capacity to layers that need it, less to layers that don't. Reduces total parameter count while matching or exceeding fixed-rank LoRA quality.

**Composable LoRA (2025-2026 trend)**: Multiple LoRA deltas stack logically for feature reuse across tasks. MLOps platforms include parameter-delta registries, adapter stores, automated merge-and-sign pipelines, and delta-aware CI.

### State Machine: Fine-Tuning Pipeline

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  DATA     │────▶│  SFT     │────▶│  PREF    │────▶│  EVAL    │
│  PREP     │     │  TRAIN   │     │  OPT     │     │  GATE    │
└──────────┘     └──────────┘     └──────────┘     └─────┬────┘
                       │                                  │
                       │ (checkpoint                      │
                       │  on spot                    pass │ fail
                       │  interruption)                   │
                       ▼                                  ▼
                 ┌──────────┐                       ┌──────────┐
                 │  RESUME  │                       │  REJECT  │
                 │  FROM    │                       │  (Root    │
                 │  CKPT    │                       │   Cause)  │
                 └──────────┘                       └──────────┘
                                                          │
                                                          ▼
                                                    ┌──────────┐
                                                    │  RETRAIN │
                                                    │  (Fix    │
                                                    │   Data)  │
                                                    └──────────┘
```

**Key invariant**: Task loss improving while general capability (MMLU, HellaSwag) craters is the signature of catastrophic forgetting. Always track both.

---

## Part 3: Token Economics & NFR Analysis

### GPU Cloud Pricing (2026)

| GPU | On-Demand $/hr | Spot $/hr | Throughput vs A100 |
|---|---|---|---|
| A100 80GB | $1.49-$3.43 | <$1.00 | 1x (baseline) |
| H100 80GB | $2.00-$2.50 | ~$2.50 | ~3x |
| H100 8x (p5.48xlarge) | $55.04/hr ($6.88/GPU) | -- | Best for full FT 70B+ |

H100 delivers ~3x training throughput, making cost-per-token-trained roughly equivalent to A100 despite the higher hourly rate. Practical advantage is speed: 24 hours instead of 72.

### Cost Per Training Run

| Model Size | Method | Hardware | Duration | Cost/Run |
|---|---|---|---|---|
| 7B | QLoRA | 1x A100 80GB | 2-4 hrs | $3-$14 |
| 7B | Full FT | 8x A100 | 24-48 hrs | $327-$1,638 |
| 13B | QLoRA | 1x A100 80GB | 4-8 hrs | $6-$27 |
| 70B | QLoRA | 1x A100/H100 | 24-36 hrs | $20-$90 |
| 70B | Full FT | 8x H100 | 24-48 hrs | $250-$510 |

**Cost formula**:
```
Training cost = GPU_hourly_rate * num_GPUs * training_hours
Spot discount = 60-70% off on-demand
Monthly inference = $1,000-$2,000 (self-hosted, single A100)
                  or $0.20-$2.00/MTok (managed endpoint)

Total annual cost = (training_cost * iterations/year)
                  + (inference_cost * 12)
                  + data_prep_labor
```

**Spot instances are the single biggest lever**: A $1,638 full fine-tune becomes ~$490 on spot.

### Budget Tiers

| Tier | Budget | Typical Approach |
|---|---|---|
| Hobby | $50-$200 | Free tier, single A100 for hours |
| Startup MVP | $2,000-$8,000 | LoRA on 7B/13B, spot, 1-2 iterations |
| Production | $15,000-$65,000 | + $2K-$15K/mo inference |
| Enterprise | $100,000-$500,000 | Full FT, custom infra, dedicated MLOps |

**The 2023 to 2026 cost collapse**: What required $100K+ compute budgets in 2023 now runs on consumer hardware in hours. A single engineer with QLoRA on Llama 3.3 can have a production-quality adapter in an afternoon.

### Approach Cost Comparison (Mid-Volume Workload)

| Approach | Upfront | Annual Total | Accuracy |
|---|---|---|---|
| Prompt engineering only | $0 | ~$14,400 | 75-85% |
| RAG | $5,000 | ~$16,100 | 85-92% |
| Fine-tuning | $2,000 | ~$9,200 | 88-93% |
| Hybrid (RAG + FT) | $7,000 | ~$13,600 | 93-97% |

### Buy vs Rent

Break-even for GPU purchase ($30K-$40K per H100) at ~10,000-16,000 usage hours. Organizations planning multiple training runs often find ownership more economical.

### Latency & Availability

| Metric | Target | Notes |
|---|---|---|
| Training p50 latency | N/A (batch) | But iteration velocity matters: 4 hrs vs 48 hrs per experiment |
| Inference p50 | <200ms TTFT | vLLM/SGLang with prompt caching |
| Inference p99 | <1s TTFT | Pre-warmed replicas, load shedding |
| Availability | 99.9% | Multi-provider fallback chain |
| RPO (model artifacts) | 0 | Replicated object storage (S3/GCS) |
| RTO (model serving) | <5 min | Blue-green deployment, instant rollback |

---

## Part 4: Distributed Resilience & Security

### Checkpointing & Spot Resilience

Standard practice: save model checkpoints every N steps. With spot instances (60-70% cost savings), checkpointing is mandatory -- spot interruptions are the norm, not the exception.

**Gradient checkpointing** (distinct from model checkpointing): trades compute for memory by recomputing activations during backward pass instead of storing them. Enables QLoRA with 70B on ~41 GB VRAM.

### Distributed Training Patterns

| Pattern | Mechanism | Use Case |
|---|---|---|
| FSDP | Shards params, gradients, optimizer states across GPUs | PyTorch native, 70B+ full FT |
| DeepSpeed ZeRO (1-3) | Progressive sharding. Stage 3 shards all three | Large-scale training with CPU/NVMe offload |
| Tensor Parallelism | Splits individual layers across GPUs | Models too large for single device even with sharding |

Both FSDP and DeepSpeed support elastic training where nodes can join/leave mid-run -- critical for spot-instance clusters.

### Failure Taxonomy

| Failure Mode | Detection | Mitigation |
|---|---|---|
| **Catastrophic forgetting** | Track MMLU/HellaSwag during training, not just task loss | Data mixing, EWC, FIP, LoRA (helps but not a silver bullet) |
| **Mode collapse** | Output diversity metrics, manual inspection | Diverse training examples, temperature > 0 during eval |
| **Reward hacking** | Human audit of top-scored outputs, adversarial probes | PAR (Preference As Reward), reward caps, RLVR |
| **Data contamination** | LLM-based decontamination, n-gram overlap detection | Strict decontamination pipeline before training |
| **Semantic drift** | Production quality metrics over time | Canary deployment, automated rollback |
| **Stale model** | A/B test against base model periodically | Scheduled re-evaluation cadence |

**Catastrophic forgetting -- the central technical risk**: Teaching a model something new erodes what it already knew. A clinical NLP team fine-tunes on radiology reports; model collapses on cardiology notes. LoRA is not a silver bullet: 2025-2026 research proves that in many continual learning scenarios, LoRA fails to prevent significant knowledge loss. A model 10% better on your task but 25% worse on everything else is rarely the right trade.

### Model Weight Security

Fine-tuned model weights are intellectual property. Key controls:
- AES-256 encryption at rest for model artifacts and training data
- TLS in transit for all model transfers
- RBAC on model registries: only authorized personnel can promote to production
- Immutable audit logs for all training data access, fine-tuning jobs, model versions
- Cryptographic signatures on adapter files to prevent tampering

### SOC 2 for AI/ML Fine-Tuning

Auditors in 2026 test evidence beyond traditional SaaS:

| Control | What Auditors Test |
|---|---|
| CC6.3 | RBAC ensuring only authorized personnel access training data |
| CC9.2 | Risk mitigation covering model drift, retraining, third-party providers |
| Immutable logs | Training data, fine-tuning jobs, model versions |
| Drift monitoring | Model bias and behavior checks in pipeline |
| Vendor risk | Annual reassessment of third-party AI sub-processors |

IBM 2025: 97% of organizations experiencing ML system incidents lacked proper access controls; 63% lacked governance policies. Average breach cost: $4.4M.

### Training Data Governance

- **GDPR**: Unlike RAG, deleting data from a fine-tuned model requires retraining without that data. No selective deletion possible.
- **HIPAA**: PHI in training data requires BAA with cloud providers, encrypted storage, access logging.
- **EU AI Act**: High-risk obligations apply from December 2, 2027.

### Zero-Trust Fine-Tuning Architecture

Assume-breach posture applied to the entire fine-tuning pipeline. Every access is verified, every component runs with least privilege, and no implicit trust exists between stages.

**Core principles:**

| Principle | Implementation |
|---|---|
| **Mutual TLS between training nodes** | All inter-node communication (gradient sync, checkpoint transfer) uses mTLS with short-lived certificates rotated every 24 hours. No plaintext training data or gradient traffic on the wire. |
| **Signed model artifacts** | Every adapter and checkpoint is cryptographically signed (cosign or Sigstore) at creation time. The model registry rejects unsigned or tampered artifacts. Signature verification is enforced at serving load time -- an unsigned adapter never reaches production. |
| **Isolated training environments** | Training VMs/containers run with no internet egress. Dependencies are pre-baked into container images from an audited internal registry. Training data is mounted read-only from encrypted storage. No SSH access during training runs. |
| **Capability-scoped API tokens** | Model registry access uses short-lived, narrowly scoped tokens: `registry:read` for inference services, `registry:write` for the training pipeline, `registry:promote` for authorized reviewers only. No long-lived credentials. Tokens are bound to specific service identities via workload identity federation. |
| **Microsegmentation** | Network policies restrict training pods to communicate only with: the checkpoint store, the metrics collector, and peer training nodes. All other egress is denied by default. |

**Verification chain**: Data store (encrypted at rest, access-logged) -> Training environment (isolated, no egress) -> Checkpoint store (signed, integrity-verified) -> Model registry (RBAC-gated, immutable versions) -> Serving layer (signature-verified load, mTLS to registry).

### PII Filtering Pipeline for Training Data

A structured detection -> redaction -> audit trail pipeline that prevents PII from entering fine-tuning datasets. Runs as a mandatory pre-processing gate before any training job.

**Stage 1: Detection (multi-layer)**

| Detection Method | Targets | Precision | Recall |
|---|---|---|---|
| **NER models** (spaCy, Presidio) | Names, addresses, organizations, dates of birth | High | Medium-high |
| **Regex patterns** | SSN (`\d{3}-\d{2}-\d{4}`), email, phone, credit card (Luhn-validated), IP addresses | Very high | High (for formatted PII) |
| **Context-dependent classifier** | Medical record numbers, internal employee IDs, account numbers that lack standard formatting | Medium | Medium |

All three layers run in parallel. Union of detections is passed to redaction. False positive rate is tuned per deployment -- prefer false positives (over-redact) over false negatives (leak PII).

**Stage 2: Redaction**

- Replace detected PII with typed, indexed placeholders: `[EMAIL_1]`, `[SSN_2]`, `[PHONE_3]`, `[NAME_4]`.
- Placeholders preserve semantic structure so the model learns the pattern (e.g., "Contact [NAME_1] at [EMAIL_1]") without memorizing real PII.
- Reversible mapping stored in a separate, access-controlled vault for authorized audit. The mapping is never stored alongside the training data.
- Consistency: the same entity maps to the same placeholder within a document (all occurrences of "john@example.com" become `[EMAIL_1]`).

**Stage 3: Audit trail**

Every redaction is logged to an immutable store (append-only, WORM-compliant):
- Original content hash (SHA-256, not the content itself)
- Redaction type (EMAIL, SSN, PHONE, NAME, etc.)
- Detection method that flagged it (NER, regex, classifier)
- Timestamp (ISO 8601, UTC)
- Dataset version and source file identifier
- Redaction action taken (replaced, removed, flagged-for-review)

**Stage 4: Training gate**

The pipeline computes a PII detection rate (flagged tokens / total tokens). If the rate exceeds the configured threshold (default: >0.1% of tokens), the training job is blocked and an alert is raised. This catches upstream data quality issues (e.g., a new data source with unmasked customer records) before they contaminate the model.

### Tool-Level RBAC for Fine-Tuning Operations

Fine-grained role-based access control mapped to least-privilege permissions across the fine-tuning lifecycle.

| Role | Permissions | Denied |
|---|---|---|
| **data-engineer** | Prepare datasets, upload to data store, run PII filtering pipeline, view dataset metadata | Launch training, access model weights, promote models |
| **ml-engineer** | Launch training jobs, view training metrics, download evaluation results, read model registry | Modify datasets, promote models to production, modify RBAC |
| **reviewer** | Approve or reject model promotion, view eval results, view audit logs, trigger canary deployment | Launch training, modify datasets, modify RBAC |
| **admin** | Modify RBAC policies, manage service accounts, configure pipeline thresholds, emergency rollback | No restrictions (but all actions are audit-logged) |

**Enforcement**: Roles are mapped to identity provider groups (Okta, Azure AD). Pipeline tooling (MLflow, W&B, custom CLI) enforces role checks at every API call. No shared credentials. All role assignments and changes are logged to the immutable audit trail.

---

## Part 5: Production Enterprise Code

```python
"""
Production fine-tuning pipeline with resilience patterns.
Demonstrates: training with checkpointing and spot resilience,
evaluation gating, adapter versioning, canary deployment with
rollback, circuit breakers, and structured logging.
"""

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


def correlation_id() -> str:
    return str(uuid.uuid4())[:12]


# ─── Circuit Breaker ───────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 2
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                return True
            return False
        return self.half_open_calls < self.half_open_max_calls

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN


# ─── Retry with Backoff + Jitter ──────────────────────────────────────

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple = (ConnectionError, TimeoutError, OSError),
    cid: str = "",
):
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                logger.info("retry.succeeded", attempt=attempt, cid=cid)
            return result
        except retryable as e:
            if attempt == max_retries:
                logger.error("retry.exhausted", attempts=max_retries + 1, error=str(e), cid=cid)
                raise
            delay = random.uniform(0, min(base_delay * (2 ** attempt), max_delay))
            logger.warning("retry.backoff", attempt=attempt + 1, delay_s=round(delay, 2), cid=cid)
            time.sleep(delay)


# ─── Evaluation Gate ──────────────────────────────────────────────────

@dataclass
class EvalThresholds:
    """Minimum scores to pass the evaluation gate."""
    task_accuracy: float = 0.85
    mmlu_floor: float = 0.60       # general capability floor
    hellaswag_floor: float = 0.70  # commonsense floor
    safety_pass_rate: float = 0.95  # red-team resistance


@dataclass
class EvalResult:
    task_accuracy: float
    mmlu_score: float
    hellaswag_score: float
    safety_pass_rate: float
    forgetting_delta_mmlu: float   # change from base model score
    forgetting_delta_hellaswag: float

    def passes(self, thresholds: EvalThresholds) -> bool:
        return (
            self.task_accuracy >= thresholds.task_accuracy
            and self.mmlu_score >= thresholds.mmlu_floor
            and self.hellaswag_score >= thresholds.hellaswag_floor
            and self.safety_pass_rate >= thresholds.safety_pass_rate
        )

    @property
    def forgetting_alert(self) -> bool:
        """Alert if general capability dropped significantly."""
        return (
            self.forgetting_delta_mmlu < -0.05
            or self.forgetting_delta_hellaswag < -0.05
        )


# ─── Adapter Version Management ──────────────────────────────────────

@dataclass
class AdapterVersion:
    version_id: str
    base_model: str
    adapter_path: str
    training_config: dict
    eval_result: EvalResult
    signature: str   # SHA-256 of adapter weights
    created_at: float
    promoted: bool = False


class AdapterRegistry:
    """Immutable adapter registry with promotion and rollback."""

    def __init__(self):
        self._versions: dict[str, AdapterVersion] = {}
        self._production_version: Optional[str] = None
        self._previous_production: Optional[str] = None

    def register(self, version: AdapterVersion, cid: str = "") -> None:
        if version.version_id in self._versions:
            raise ValueError(f"Version {version.version_id} already exists (immutable)")
        self._versions[version.version_id] = version
        logger.info(
            "adapter.registered",
            version=version.version_id,
            task_acc=version.eval_result.task_accuracy,
            mmlu=version.eval_result.mmlu_score,
            cid=cid,
        )

    def promote(
        self,
        version_id: str,
        thresholds: EvalThresholds,
        cid: str = "",
    ) -> bool:
        version = self._versions.get(version_id)
        if not version:
            raise KeyError(f"Version {version_id} not found")

        if not version.eval_result.passes(thresholds):
            logger.warning(
                "adapter.promotion_rejected",
                version=version_id,
                reason="eval_below_threshold",
                cid=cid,
            )
            return False

        if version.eval_result.forgetting_alert:
            logger.warning(
                "adapter.forgetting_detected",
                version=version_id,
                mmlu_delta=version.eval_result.forgetting_delta_mmlu,
                hellaswag_delta=version.eval_result.forgetting_delta_hellaswag,
                cid=cid,
            )

        self._previous_production = self._production_version
        self._production_version = version_id
        version.promoted = True
        logger.info("adapter.promoted", version=version_id, cid=cid)
        return True

    def rollback(self, cid: str = "") -> Optional[str]:
        if not self._previous_production:
            logger.error("adapter.rollback_failed", reason="no_previous_version", cid=cid)
            return None
        rolled_back_from = self._production_version
        self._production_version = self._previous_production
        self._previous_production = None
        logger.info(
            "adapter.rolled_back",
            from_version=rolled_back_from,
            to_version=self._production_version,
            cid=cid,
        )
        return self._production_version

    @property
    def production_version(self) -> Optional[str]:
        return self._production_version


# ─── Canary Deployment Controller ─────────────────────────────────────

@dataclass
class CanaryConfig:
    initial_traffic_pct: float = 5.0
    ramp_step_pct: float = 10.0
    ramp_interval_seconds: float = 3600.0  # 1 hour between ramps
    quality_threshold: float = 0.85
    rollback_on_degradation: bool = True


class CanaryController:
    """
    Routes traffic between production and canary adapter.
    Monitors quality. Auto-rollback on degradation.
    """

    def __init__(
        self,
        registry: AdapterRegistry,
        config: CanaryConfig,
        quality_monitor,  # callable(version_id) -> float
    ):
        self.registry = registry
        self.config = config
        self.quality_monitor = quality_monitor
        self.canary_version: Optional[str] = None
        self.canary_traffic_pct: float = 0.0
        self.last_ramp_time: float = 0.0

    def start_canary(self, version_id: str, cid: str = "") -> None:
        self.canary_version = version_id
        self.canary_traffic_pct = self.config.initial_traffic_pct
        self.last_ramp_time = time.time()
        logger.info(
            "canary.started",
            version=version_id,
            traffic_pct=self.canary_traffic_pct,
            cid=cid,
        )

    def route_request(self) -> str:
        """Returns version_id to serve this request."""
        if self.canary_version and random.random() * 100 < self.canary_traffic_pct:
            return self.canary_version
        return self.registry.production_version or "base"

    def check_and_ramp(self, cid: str = "") -> None:
        if not self.canary_version:
            return

        quality = self.quality_monitor(self.canary_version)

        if quality < self.config.quality_threshold:
            logger.warning(
                "canary.quality_degraded",
                version=self.canary_version,
                quality=quality,
                threshold=self.config.quality_threshold,
                cid=cid,
            )
            if self.config.rollback_on_degradation:
                self.canary_version = None
                self.canary_traffic_pct = 0.0
                logger.info("canary.rolled_back", cid=cid)
            return

        elapsed = time.time() - self.last_ramp_time
        if elapsed >= self.config.ramp_interval_seconds:
            self.canary_traffic_pct = min(
                100.0, self.canary_traffic_pct + self.config.ramp_step_pct
            )
            self.last_ramp_time = time.time()
            logger.info(
                "canary.ramped",
                version=self.canary_version,
                traffic_pct=self.canary_traffic_pct,
                cid=cid,
            )

            if self.canary_traffic_pct >= 100.0:
                self.registry.promote(
                    self.canary_version, EvalThresholds(), cid=cid
                )
                self.canary_version = None
                logger.info("canary.completed_full_rollout", cid=cid)


# ─── Fallback Inference Chain ─────────────────────────────────────────

class FallbackInferenceChain:
    """
    Try fine-tuned model -> base model with prompt -> cached response.
    Each provider protected by a circuit breaker.
    """

    def __init__(self, providers: list[tuple[str, Any, CircuitBreaker]]):
        self.providers = providers  # list of (name, inference_fn, circuit_breaker)

    def generate(self, prompt: str, cid: str = "") -> dict:
        errors = []
        for name, inference_fn, cb in self.providers:
            if not cb.can_execute():
                errors.append((name, "circuit_open"))
                continue
            try:
                def _call():
                    return inference_fn(prompt)

                result = retry_with_backoff(_call, max_retries=2, cid=cid)
                cb.record_success()
                return {"text": result, "provider": name, "fallback": name != self.providers[0][0]}
            except Exception as e:
                cb.record_failure()
                errors.append((name, str(e)))
                logger.warning("fallback.failed", provider=name, error=str(e), cid=cid)

        raise RuntimeError(f"All inference providers failed: {errors}")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Domain-Specific Customer Email Classifier for E-Commerce

**Problem Statement**: An e-commerce company receives 50K customer emails daily across 15 categories (refund request, shipping inquiry, product complaint, compliment, fraud report, etc.). Current rule-based system achieves 72% accuracy with 40% manual review rate. Target: >92% accuracy, <5% manual review rate, <200ms p95 inference latency, deployed within 8 weeks. Budget: $25K total.

**Proposed Architecture**:

```
┌────────────────────────────────────────────────────────────────┐
│                   EMAIL CLASSIFICATION PIPELINE                 │
│                                                                 │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────┐   │
│  │ Email     │──▶│ Preprocessor │──▶│ Fine-Tuned Llama 3.3 │   │
│  │ Ingestion │   │ (truncate,   │   │ 8B + QLoRA Adapter   │   │
│  │           │   │  normalize)  │   │ (classification head)│   │
│  └──────────┘   └──────────────┘   └──────────┬───────────┘   │
│                                                │                │
│                                     ┌──────────┴──────────┐    │
│                                     │  Confidence Router   │    │
│                                     │  >0.9: auto-route    │    │
│                                     │  0.7-0.9: LLM verify │    │
│                                     │  <0.7: human review  │    │
│                                     └──────────────────────┘    │
└────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Prompt engineering (Claude/GPT)** | Zero training cost, deployed in days | $14,400/year inference (50K emails/day * $0.80/1K), 80-85% accuracy, no confidentiality | Rejected: accuracy gap and ongoing cost |
| **B: QLoRA on Llama 3.3 8B** | $6-14 training cost per run, ~93% accuracy achievable, self-hosted at ~$1K/mo inference, data stays on-prem | Requires 5K labeled examples, 2-4 hrs training per iteration | **Selected** |
| **C: Full fine-tune of 70B model** | Potentially 95%+ accuracy | $250-510/run, $2K/mo inference, overkill for classification | Rejected: diminishing returns on classification |

**Decision Rationale**: Classification is the ideal fine-tuning use case -- narrow task, high volume, consistent format. QLoRA on an 8B model achieves 93%+ accuracy at $3-14 per training run. The confidence router sends high-confidence predictions straight to routing, medium-confidence to a cheaper LLM verification step, and low-confidence to human review -- targeting <5% manual review. Total cost: ~$5K setup (data labeling + initial training iterations) + ~$1K/month inference = well within $25K annual budget. Self-hosting eliminates sending customer PII to third-party APIs. Adapter size (~50 MB) enables instant rollback via blue-green deployment.

---

### Scenario 2: Multi-Domain Legal Document Drafting Assistant

**Problem Statement**: A law firm wants an AI assistant that generates first drafts of legal documents (contracts, briefs, memos) in the firm's house style. The firm has 10,000 historical documents across 5 practice areas. Each practice area has distinct formatting, citation style, and terminology. The model must maintain general legal reasoning while adopting firm-specific patterns. Confidentiality is paramount (no data leaves firm infrastructure). Budget: $100K first year.

**Proposed Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    LEGAL DRAFTING SYSTEM                         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              BASE MODEL: Llama 3.3 70B (Self-Hosted)      │   │
│  │              Serving: vLLM on 2x H100                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│              ┌─────────────┼─────────────┐                      │
│              ▼             ▼             ▼                       │
│  ┌───────────────┐ ┌────────────┐ ┌───────────────┐            │
│  │ LoRA Adapter   │ │ LoRA       │ │ LoRA Adapter  │   ...      │
│  │ Corporate Law  │ │ Litigation │ │ IP Law        │            │
│  │ (50 MB)        │ │ (50 MB)    │ │ (50 MB)       │            │
│  └───────────────┘ └────────────┘ └───────────────┘            │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  COMPOSABLE ADAPTER ROUTER                                │   │
│  │  Practice area detected -> load appropriate adapter       │   │
│  │  Hot-swap in <100ms                                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  RAG LAYER (Hybrid Retrieval from Firm Knowledge Base)    │   │
│  │  Precedent cases, clause libraries, client-specific terms │   │
│  └──────────────────────────────────────────────────────────┘   │
│                            │                                     │
│                            ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  EVAL: Senior attorney review on 10% sample               │   │
│  │  + LLM-as-judge on formatting compliance                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Single fine-tuned 70B model** | Simpler ops, one model to manage | Catastrophic forgetting across 5 practice areas, no per-domain specialization | Rejected: forgetting risk too high for legal |
| **B: Composable LoRA (per practice area)** | Per-domain specialization, hot-swap, base model preserves general legal reasoning, each adapter trainable independently | More complex adapter routing, requires practice area detection | **Selected** |
| **C: Five separate fine-tuned models** | Maximum isolation between domains | 5x inference cost ($10K/mo vs $2K/mo), 5x the ops burden | Rejected: cost and operational overhead |

**Decision Rationale**: Composable LoRA is the key architectural insight. Each practice area gets its own adapter (50 MB each), trained on 1,000-2,000 domain examples. The base 70B model retains general legal reasoning because adapters modify <1% of weights. Hot-swapping adapters per request takes <100ms. Adding RAG grounds the model in actual firm precedents and clause libraries (fine-tuning teaches style, RAG provides facts -- the RAG+FT hybrid achieves 93-97% accuracy). Self-hosted on 2x H100 ($5K/mo) keeps all data on-prem. Total first-year cost: ~$60K hardware + ~$20K data preparation and labeling + ~$5K training iterations + ~$15K engineering time. Each adapter can be retrained independently when a practice area's style evolves, without affecting other domains.
