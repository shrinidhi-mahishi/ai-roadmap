# Module 02: Fine-Tuning LLMs

## What Is This?

Fine-tuning is changing a model's default behavior by updating its weights on task-specific data -- after the original pretraining is done. Think of it as specialized coaching for an athlete who already knows how to run: you are not teaching them to run, you are teaching them your team's plays. The clean decision boundary: use prompting when the behavior change is light, RAG when the problem is private or fast-changing knowledge, workflow/validators when the problem is orchestration or business rules, and fine-tuning when you want the model itself to internalize a repeatable behavior that persists even before the prompt gets elaborate.

**Why not just prompt?** LIMA (Zhou et al., NeurIPS 2023) showed that 1,000 carefully curated SFT examples can rival 50k+ noisy ones. InstructGPT (Ouyang et al., NeurIPS 2022) proved ~13k SFT + 33k RLHF examples beat a 100x-larger unaligned model. Fine-tuning locks in formatting, tone, tool-call style, domain vocabulary, and safety refusals more reliably than any system prompt, especially when you need that behavior across millions of requests.

---

## Part 1: System Topology & Data Flow

### The Post-Training Stack

```
Pre-trained Base (next-token predictor, no instruction following)
        |
        v
+------------------+
|  SFT (Stage 1)   |  -- "produce this kind of answer"
|  gold (input,    |     1k-100k curated examples
|  output) pairs   |     InstructGPT: ~13k demonstrations
+--------+---------+
         |
         v
+------------------+
| Preference        |  -- "prefer this answer over that one"
| Alignment (2)     |     DPO/ORPO/KTO/SimPO
|  chosen/rejected  |     InstructGPT: ~33k comparisons
|  or thumbs up/dn  |     (or RLHF with reward model)
+--------+---------+
         |
         v
+------------------+
| RL / Verifier (3) |  -- "optimize against a grader"
|  GRPO/GSPO/RFT   |     Requires reliable reward signal
|  group sampling   |     DeepSeek-R1: GRPO on math/code
+--------+---------+
         |
         v
   Aligned Model
```

Each stage is optional but ordered: SFT before preference (DPO on an unaligned base is unstable); preference before RL (policy needs a reasonable starting point). The further right you go, the higher the data and evaluation burden.

### Control Plane + Serve Plane

```
+-----------------------------------------------------------------------+
| CONTROL PLANE                                                          |
|                                                                        |
|  +-------------------+  +-------------------+  +-------------------+   |
|  | Data Pipeline      |  | Train Orchestrator|  | Eval Gate         |   |
|  | collect, dedup,    |  | HF Trainer / API  |  | task + forgetting |   |
|  | normalize, PII     |  | job, checkpoint   |  | + safety + dtype  |   |
|  | redact, version    |  | versioned artifact|  | pass -> promote   |   |
|  +--------+----------+  +--------+----------+  +--------+----------+   |
|           |                      |                      |              |
+-----------+----------------------+----------------------+--------------+
            |                      |                      |
            v                      v                      v
+-----------------------------------------------------------------------+
| SERVE PLANE                                                            |
|                                                                        |
|  +-------------------+  +-------------------+  +-------------------+   |
|  | Adapter Registry   |  | Canary Controller |  | Inference Runtime |   |
|  | (adapter_id,       |  | 5% -> 25% -> 100%|  | vLLM / TGI /     |   |
|  |  base_model_id,    |  | rollback on SLO   |  | vendor API        |   |
|  |  version, tenant,  |  | violation         |  | hot-swap adapters |   |
|  |  hash, eval_score) |  |                   |  | per request       |   |
|  +-------------------+  +-------------------+  +-------------------+   |
+-----------------------------------------------------------------------+
```

### Full Fine-Tuning vs PEFT Decision

| Axis | Full Fine-Tune | PEFT (LoRA / QLoRA) |
|---|---|---|
| **What trains** | All parameters | 0.01-1% of parameters |
| **GPU requirement** | 16x-32x model size in memory | 1x-2x model size |
| **Storage per variant** | Full checkpoint (GB-TB) | Adapter file (10-100 MB) |
| **Multi-tenant** | One model per tenant (infeasible) | One base + N adapters |
| **Catastrophic forgetting** | Higher risk (all weights shift) | Lower (base frozen) |
| **Max capability shift** | Highest (can reshape deep features) | Limited by rank |
| **When** | Own infra, open weights, deep distribution shift | Default for everything else |

**In practice PEFT wins** unless you own open weights, have strong infra, and truly need deep distribution shift. The original LoRA claim: ~10,000x fewer trainable parameters and ~3x lower GPU memory than full FT on GPT-3-scale models.

---

## Part 2: Core Mechanics & Algorithms

### LoRA (Low-Rank Adaptation)

**Core insight:** The weight updates during fine-tuning live in a low-rank subspace. Instead of updating W directly, decompose the update into two small matrices.

```
W' = W + (alpha/r) * B @ A

Where:
  W   = frozen pretrained weight matrix (d_in x d_out)
  A   = trainable matrix (d_in x r), initialized from N(0, sigma^2)
  B   = trainable matrix (r x d_out), initialized to zeros
  r   = rank (typically 8-64; 4-256 range)
  alpha = scaling factor (commonly 2*r; ratio alpha/r controls update magnitude)
```

**Trainable parameters per linear layer:** `r * (d_in + d_out)`. For a square d x d layer: `2dr`. Example: Llama-3-8B with r=16 on all attention projections (q, k, v, o) across 32 layers: `2 * 16 * 4096 * 4 * 32 = 16.8M` trainable params vs 8B total = **0.21%**.

**Which modules to target?** Default: all attention projections (q, k, v, o). Adding MLP layers (gate, up, down) increases capacity but also forgetting risk. `target_modules="all-linear"` in PEFT is convenient but rarely optimal -- profile forgetting on a holdout.

**Initialization matters:** B=0 means W'=W at step 0 (no initial perturbation). A from Kaiming uniform. If both were random, training would start from a corrupted model.

**Merge vs serve separately:**
- **Merge** (`W_merged = W + (alpha/r) * B @ A`): zero inference overhead, but you lose composability and rollback requires swapping the full model.
- **Serve adapters live**: vLLM `--enable-lora --max-loras 16 --max-lora-rank 64` (defaults); per-request `lora_request` header. Punica SGMV kernel: adapter overhead ~**2ms/token** at batch 16. Hot-swap without restart.

### QLoRA (Quantized LoRA)

Three innovations that made 65B fine-tuning possible on a single 48GB GPU (vs >780GB for full 16-bit FT):

1. **NF4 (4-bit NormalFloat):** Quantization data type optimized for normally distributed weights. Information-theoretically optimal for N(0,1) at 4 bits. Matches FP16 training quality.

2. **Double quantization:** Quantize the quantization constants themselves. Saves ~0.373 bits/parameter = **~3 GB** on a 65B model. First quantization: 64-element blocks -> FP32 scales. Second: quantize those scales to FP8 with 256-element blocks.

3. **Paged optimizers:** Unified memory (CPU+GPU) for optimizer states; pages evict on OOM spikes during long sequences, page back for the backward pass. Prevents training crashes without manual memory tuning.

**Memory math:** NF4 base: ~0.5 bytes/param. LoRA adapters in BF16: ~2 bytes/param but only on 0.2-1% of params. Optimizer states (AdamW): 2 states x BF16 per LoRA param. Total for 65B QLoRA: ~33 GB base + ~2 GB adapters + ~4 GB optimizer = fits in 48 GB A6000.

**Guanaco/QLoRA benchmark:** Top model reported **99.3%** of ChatGPT on Vicuna; 33B model at **97.8%**. Training: 24 hours on single GPU.

### DoRA (Weight-Decomposed Low-Rank Adaptation)

Decomposes W into magnitude (m) and direction (V), then applies LoRA only to the direction:
```
W' = m * (V + (alpha/r) * B @ A) / ||V + (alpha/r) * B @ A||
```

Consistently outperforms LoRA by **0.5-1.5 points** on commonsense reasoning benchmarks with the same rank. Extra cost: one norm computation per forward pass (negligible).

### AdaLoRA (Adaptive Rank Allocation)

SVD-parameterized adaptation that dynamically prunes rank per layer based on importance scores. Layers with more to learn keep higher rank; easy layers drop to r=1-2. Saves 10-30% parameters vs uniform-rank LoRA at similar quality.

### Composable LoRA

Stack multiple adapters: `W' = W + B1@A1 + B2@A2`. Use case: base style adapter + domain adapter + customer tone adapter. Interference risk: adapters trained independently may conflict. Mitigation: train with the base adapters frozen, or use orthogonal regularization.

### Preference Alignment Methods

| Method | Input Shape | Key Hyperparameter | Mechanism | When to Use |
|---|---|---|---|---|
| **DPO** (Rafailov et al., NeurIPS 2023) | (prompt, chosen, rejected) pairs | beta=0.1 (KL penalty) | Implicit reward from preference pairs; no reward model needed | Default preference method; need chosen/rejected pairs |
| **ORPO** (Hong et al., 2024) | (prompt, chosen, rejected) | lambda=0.1 (odds ratio weight) | Single-stage: SFT + odds-ratio penalty in one loss | Skip SFT stage; smaller datasets |
| **KTO** (Ethayarajh et al., 2024) | (prompt, output, thumbs_up/down) | lambda_D/lambda_U (desirable/undesirable loss weights) | Binary feedback only; no paired comparisons needed | Production logs with like/dislike; cannot get paired data |
| **SimPO** (Meng et al., 2024) | (prompt, chosen, rejected) | gamma (reward margin) | Length-normalized log-prob as implicit reward; no reference model | Reduce length bias in DPO; simpler pipeline |
| **GRPO** (Shao et al., DeepSeek-R1, 2024) | (prompt, G=64 sampled completions, reward scores) | G=64 (group size), clip range | Group-relative advantage: normalize rewards within the group; no critic model | Math/code with verifiable rewards; reasoning chains |
| **GSPO** (MoE-targeted, 2025) | (prompt, chosen, rejected) + router regularization | router entropy weight | DPO-like but adds MoE router balance loss | Fine-tuning Mixture-of-Experts models |

**DPO beta tuning:** beta too high -> model barely moves from base (underfitting). beta too low -> policy collapses to always producing "chosen" regardless of prompt (overfitting). Start at 0.1, sweep [0.05, 0.3]. OpenAI API surface: beta range 0 to 2.

**GRPO workflow:** For each prompt, sample G=64 completions. Score each with a verifiable reward (unit test, math checker). Compute group-relative advantage: `A_i = (r_i - mean(r)) / std(r)`. Update policy with clipped surrogate objective. No critic model needed (unlike PPO). DeepSeek-R1 used GRPO to bootstrap reasoning without any SFT data.

### Catastrophic Forgetting

The central risk of fine-tuning: the model gets better at your task but worse at everything else.

**Biderman et al. (2024) "Loser" principle:** After fine-tuning on N tasks sequentially, the **average performance across all N tasks** is what matters, not just the last one. New task performance of X% means nothing if three prior capabilities each dropped Y%.

**Detection:**
- Run a **general capability holdout** (MMLU subset, HumanEval, safety suite) before and after.
- Track per-task deltas, not just aggregate.
- Set a **forgetting budget**: if any capability drops more than Z%, the adapter fails the eval gate.

**Mitigation:**
- LoRA (frozen base = primary defense)
- Low rank (r=8-16, not r=256)
- alpha = 2r (standard; keep update magnitude conservative)
- Replay mixing: add 5-10% of general-capability data to the training mix
- Early stopping on validation loss (not just training loss)
- Elastic Weight Consolidation (EWC): penalize moving weights important for prior tasks

---

## Part 3: Token Economics & NFR Analysis

### Vendor Fine-Tuning Pricing

| Provider | Model | Training | Input (tuned) | Output (tuned) | Hosting | Death Date |
|---|---|---|---|---|---|---|
| **OpenAI** | gpt-4.1-mini | $1.80/1M train | $1.60/1M | $6.40/1M | Free while in use | Models expire; new FT jobs disabled 2027-01-06 for older |
| **OpenAI** | gpt-4.1 | $12.00/1M train | $8.00/1M | $32.00/1M | Free while in use | Same policy |
| **OpenAI** | gpt-4o-2024-08-06 | $25.00/1M | $3.75/1M | $15.00/1M | Free while in use | ft-* deprecated snapshot-by-snapshot |
| **OpenAI** | o4-mini | $11.00/1M train | varies | varies | Free while in use | **2026-10-23** (ft jobs disabled) |
| **Anthropic** | Not offered | -- | -- | -- | -- | -- |
| **Google** | Gemini 2.0 Flash | $0/1M (free) | $0.15/1M | $0.60/1M | $7/hr (SFT tuned endpoint) | -- |
| **Google** | Gemini 2.5 Flash | TBD | TBD | TBD | TBD | -- |
| **Fireworks** | Any supported | $0.60/1M train | varies by base | varies | $0/hr (serverless) | -- |
| **Together** | Llama/Qwen/etc | From $0.80/1M | varies | varies | $0/hr (serverless) | -- |

**Key dates to know:**
- OpenAI: Fine-tuning jobs on `gpt-4o-2024-08-06` -> no new jobs after **2027-01-06**
- OpenAI: `ft-o4-mini` -> disabled **2026-10-23**
- OpenAI: Evaluations API deprecated **2026-11-30**
- OpenAI: Storage for unused fine-tuned models may be reclaimed after inactivity

**Self-hosted GPU pricing (cloud spot):**

| GPU | VRAM | Spot $/hr | Max Model (QLoRA) | Max Model (Full FT, BF16) |
|---|---|---|---|---|
| A100 80GB | 80 GB | ~$1.50 | ~70B | ~10B |
| H100 80GB | 80 GB | ~$2.50 | ~70B | ~10B |
| 4x A100 80GB | 320 GB | ~$6.00 | ~180B (sharded) | ~40B |
| 8x H100 80GB | 640 GB | ~$20.00 | 400B+ | ~80B |

### Cost Per Training Run (Estimates)

| Scenario | Method | GPU Hours | Estimated Cost |
|---|---|---|---|
| 7B SFT, 10k examples, 3 epochs | QLoRA r=16 | ~2-4 hrs A100 | **$3-6** (spot) |
| 7B DPO, 5k pairs, 1 epoch | QLoRA r=16 | ~1-2 hrs A100 | **$1.50-3** |
| 70B SFT, 50k examples, 3 epochs | QLoRA r=32 | ~24-48 hrs 4xA100 | **$144-288** |
| 70B SFT, same | Full FT (8xH100) | ~48-96 hrs | **$960-1920** |
| gpt-4.1-mini, 10k examples, 3 epochs (API) | Vendor SFT | N/A | ~**$54** ($1.80/1M x ~10M tokens x 3) |
| gpt-4.1, 10k examples, 3 epochs (API) | Vendor SFT | N/A | ~**$360** ($12/1M x ~10M x 3) |

### Budget Tiers

| Tier | Budget | What You Get |
|---|---|---|
| **Prototype** | $0-50 | QLoRA 7-8B on free Colab/Kaggle; or 1 OpenAI mini run |
| **Serious experiment** | $50-500 | Multiple QLoRA runs with hyperparameter sweeps on spot A100s |
| **Production** | $500-5,000 | 70B QLoRA or multiple 7B full FT runs; vendor API fine-tuning |
| **Enterprise** | $5,000+ | Multi-GPU full FT; continuous retraining pipeline |

### Eval Gate (Four Checks Before Promotion)

| Gate | Metric | Threshold Example | Why |
|---|---|---|---|
| **Task quality** | Domain-specific accuracy/F1 | >= baseline + 2% | The reason you fine-tuned |
| **Forgetting** | General capability delta (MMLU, HumanEval) | < 1% drop | Catastrophic forgetting check |
| **Safety** | Refusal on harmful prompts; injection resistance | 0 regressions | Cannot ship unsafe model |
| **Serve-dtype parity** | FP16 vs BF16 vs INT8 quality delta | < 0.5% delta | Quantized serving must not silently degrade |

### Adapter Serving Economics

**vLLM multi-LoRA defaults:** `--max-loras 16`, `--max-lora-rank 64`. Punica SGMV kernel adds ~**2ms/token** at batch 16. Hot-swap per-request via `lora_request` header. No restart needed.

**Multi-tenant cost model:** One base model serving N tenants with different adapters vs N separate model deployments. At 100 tenants, adapter serving is **~50-100x cheaper** in GPU cost than separate deployments. The trade-off: shared base means all tenants upgrade/downgrade together.

### Latency SLA Targets

| Operation | Target | Notes |
|---|---|---|
| Training job start | < 5 min | Queue + GPU allocation |
| Adapter hot-swap | < 100ms | vLLM live reload |
| Canary rollback | < 30s | Traffic shift back to previous adapter |
| Inference overhead (adapter vs base) | < 5% | Punica kernel; negligible at high batch |
| Eval gate pipeline | < 30 min | All 4 gates automated |

---

## Part 4: Distributed Resilience & Security

### Zero-Trust Fine-Tuning Architecture

**Threat model:** Training data contains PII/trade secrets. Adapters encode proprietary behavior. Serving infrastructure is multi-tenant.

```
+-------------------------------------------------------------------+
| SECURITY BOUNDARY                                                  |
|                                                                    |
|  Training Environment (isolated)                                   |
|  +------------------+  +------------------+  +------------------+  |
|  | Data Pipeline     |  | Training Cluster |  | Artifact Store   |  |
|  | PII detect+redact |  | mTLS between     |  | signed adapters  |  |
|  | format normalize  |  | workers          |  | SHA256 manifest  |  |
|  | audit log (WORM)  |  | no egress except |  | versioned, ACL'd |  |  
|  |                   |  | artifact store   |  |                  |  |
|  +------------------+  +------------------+  +------------------+  |
|                                                                    |
|  Serving Environment (separate)                                    |
|  +------------------+  +------------------+  +------------------+  |
|  | Adapter Registry  |  | Canary Controller|  | Inference Nodes  |  |
|  | hash verification |  | per-tenant       |  | tenant-scoped    |  |
|  | provenance chain  |  | traffic splitting|  | adapter loading  |  |
|  +------------------+  +------------------+  +------------------+  |
+-------------------------------------------------------------------+
```

**Key principles:**
1. **mTLS** between all training workers and artifact stores.
2. **Signed artifacts**: Every adapter checkpoint has a SHA256 manifest. Serving nodes verify before loading.
3. **Isolated training environments**: Training cluster has no egress except to the artifact store. Prevents exfiltration of training data through model weights.
4. **Scoped tokens**: Each tenant's adapter is loaded only for requests with verified tenant identity. No adapter catalog browsing.
5. **Microsegmentation**: Training, eval, and serving are separate network segments.

### PII Filtering Pipeline

```
Raw Training Data
       |
       v
+------------------+
| Detection        |  Presidio / custom NER: names, SSNs, emails,
| (identify PII)   |  phone numbers, addresses, medical record numbers
+--------+---------+
         |
         v
+------------------+
| Redaction         |  Replace with type tokens: [NAME], [SSN], [EMAIL]
| (remove/mask)     |  Preserve sentence structure for training quality
+--------+---------+
         |
         v
+------------------+
| Audit             |  Log: what was redacted, original hash, redaction
| (immutable log)   |  method, timestamp. WORM storage for compliance.
+--------+---------+
         |
         v
+------------------+
| Training Gate     |  PII scan on redacted data: if any PII detected,
| (final check)     |  block training job. Zero-tolerance before GPU.
+------------------+
```

**Why before training, not after:** Once PII is in the training data, it can be memorized into weights. Extraction attacks (Carlini et al.) can recover training data from model outputs. Redaction after training is insufficient.

### Tool-Level RBAC for Adapter Operations

| Role | Permissions |
|---|---|
| **Data Engineer** | Upload training data, trigger PII pipeline, view data stats |
| **ML Engineer** | Launch training jobs, view training metrics, promote to staging |
| **ML Ops** | Promote staging -> production, configure canary, rollback |
| **Tenant Admin** | View own adapter metadata, request retraining, view eval scores |
| **Security** | Audit all operations, view PII redaction logs, revoke adapters |

### Canary Deployment State Machine

```
                 eval gate passes
   TRAINING -----------------------> STAGED
       |                                |
       | eval fails                     | canary_start (5% traffic)
       v                                v
   REJECTED                         CANARY_5%
                                       |
                           SLO met     | SLO violated
                           (24h)       v
                              |     ROLLBACK --> previous adapter
                              v
                         CANARY_25%
                              |
                   SLO met    | SLO violated
                   (24h)      v
                      |    ROLLBACK
                      v
                   CANARY_100% (promoted)
```

**SLO checks during canary:**
- Task accuracy on live traffic (sampled)
- Latency p99 within 10% of baseline
- Safety violation rate = 0
- Error rate within 2x of baseline

### Failure Taxonomy

| Failure | Cause | Detection | Mitigation |
|---|---|---|---|
| **Catastrophic forgetting** | Overfitting to narrow task; high rank; no replay mixing | General capability holdout drops > 1% | Freeze base (LoRA); low rank; replay 5-10% general data |
| **Data poisoning** | Malicious or mislabeled training examples | Validation loss anomalies; safety eval regression | PII pipeline; data provenance; human review sample |
| **Reward hacking (RFT)** | Grader is gameable; model exploits shortcuts | Reward goes up but task quality (human-judged) stays flat | Diverse reward signals; human spot-checks |
| **DPO collapse** | beta too low; weak base model; imbalanced pairs | Policy always outputs "chosen" regardless of prompt | beta sweep [0.05, 0.3]; ensure base can generate reasonable outputs first |
| **Adapter interference** | Composing independently-trained adapters | Quality drop when stacking | Train with other adapters frozen; orthogonal regularization |
| **Vendor deprecation** | Model snapshot deprecated; FT jobs disabled | Death date monitoring | Export weights if possible; maintain OSS fallback |
| **Serve-dtype mismatch** | Trained in BF16, served in INT8 without validation | Quality delta > 0.5% on eval gate | Always validate serve-dtype in eval pipeline |
| **Stale adapter** | Domain shift in production data vs training data | Drift detection on live traffic scores | Scheduled retraining; continuous eval |
| **Training data leakage** | PII/secrets in training data memorized into weights | Extraction attacks; compliance audit failure | PII pipeline; differential privacy; audit |
| **Wrong target modules** | LoRA applied to suboptimal layers | Poor task quality despite sufficient data | Profile per-layer gradient norms; ablation study |
| **Overfitting** | Too many epochs; too much rank; too little data | Val loss diverges from train loss | Early stopping; rank reduction; more data |
| **Checkpoint corruption** | Infra failure during save; bit rot | SHA256 mismatch on load | Checksummed saves; redundant storage |

---

## Part 5: Production Enterprise Code

```python
"""Fine-tuning control plane: adapter registry, canary deployment, 
   fallback inference chain. Production patterns with full error handling.
"""
from __future__ import annotations

import hashlib, json, logging, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("ft")

# ── Adapter Registry ──────────────────────────────────────────────

@dataclass(frozen=True)
class AdapterRecord:
    adapter_id: str
    base_model_id: str
    version: int
    tenant_id: str
    sha256: str                     # integrity check on load
    task_score: float               # eval gate: domain metric
    forgetting_delta: float         # eval gate: general capability drop
    safety_pass: bool               # eval gate: zero regressions
    serve_dtype_delta: float        # eval gate: quantized parity
    created_at: float = field(default_factory=time.time)

    def passes_eval_gate(self, min_task=0.80, max_forget=0.01,
                         max_dtype_delta=0.005) -> bool:
        """Four-gate promotion check."""
        return (self.task_score >= min_task
                and self.forgetting_delta <= max_forget
                and self.safety_pass
                and self.serve_dtype_delta <= max_dtype_delta)


class AdapterRegistry:
    """Versioned adapter store with integrity verification."""

    def __init__(self):
        self._store: dict[str, list[AdapterRecord]] = {}  # tenant -> versions

    def register(self, rec: AdapterRecord) -> bool:
        if not rec.passes_eval_gate():
            log.warning("adapter_rejected tenant=%s score=%.3f forget=%.4f",
                        rec.tenant_id, rec.task_score, rec.forgetting_delta)
            return False
        self._store.setdefault(rec.tenant_id, []).append(rec)
        log.info("adapter_registered tenant=%s v=%d id=%s",
                 rec.tenant_id, rec.version, rec.adapter_id)
        return True

    def latest(self, tenant_id: str) -> Optional[AdapterRecord]:
        versions = self._store.get(tenant_id, [])
        return max(versions, key=lambda r: r.version) if versions else None

    def rollback(self, tenant_id: str) -> Optional[AdapterRecord]:
        versions = self._store.get(tenant_id, [])
        if len(versions) < 2:
            return None
        versions.pop()   # remove latest
        prev = versions[-1]
        log.warning("adapter_rollback tenant=%s to_v=%d", tenant_id, prev.version)
        return prev

    def verify_integrity(self, rec: AdapterRecord, 
                         adapter_bytes: bytes) -> bool:
        """Check SHA256 before loading adapter into GPU memory."""
        actual = hashlib.sha256(adapter_bytes).hexdigest()
        if actual != rec.sha256:
            log.error("integrity_fail tenant=%s expected=%s actual=%s",
                      rec.tenant_id, rec.sha256[:16], actual[:16])
            return False
        return True


# ── Canary Controller ─────────────────────────────────────────────

class CanaryStage(str, Enum):
    STAGED = "staged"
    CANARY_5 = "canary_5"
    CANARY_25 = "canary_25"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"

@dataclass
class CanaryController:
    """Progressive traffic ramp with automatic rollback on SLO violation."""
    tenant_id: str
    new_adapter: AdapterRecord
    old_adapter: Optional[AdapterRecord]
    stage: CanaryStage = CanaryStage.STAGED
    stage_start: float = field(default_factory=time.time)
    slo_window_s: float = 86400.0        # 24h per stage
    error_budget: float = 0.02           # 2x baseline error rate
    _error_count: int = 0
    _request_count: int = 0

    @property
    def new_traffic_pct(self) -> float:
        return {
            CanaryStage.STAGED: 0.0,
            CanaryStage.CANARY_5: 0.05,
            CanaryStage.CANARY_25: 0.25,
            CanaryStage.PROMOTED: 1.0,
            CanaryStage.ROLLED_BACK: 0.0,
        }[self.stage]

    def record_outcome(self, success: bool):
        self._request_count += 1
        if not success:
            self._error_count += 1
        # Check error budget
        if self._request_count >= 100:  # min sample
            error_rate = self._error_count / self._request_count
            if error_rate > self.error_budget:
                self.stage = CanaryStage.ROLLED_BACK
                log.error("canary_rollback tenant=%s error_rate=%.3f",
                          self.tenant_id, error_rate)

    def maybe_advance(self):
        if self.stage == CanaryStage.ROLLED_BACK:
            return
        elapsed = time.time() - self.stage_start
        if elapsed < self.slo_window_s:
            return
        transitions = {
            CanaryStage.STAGED: CanaryStage.CANARY_5,
            CanaryStage.CANARY_5: CanaryStage.CANARY_25,
            CanaryStage.CANARY_25: CanaryStage.PROMOTED,
        }
        next_stage = transitions.get(self.stage)
        if next_stage:
            self.stage = next_stage
            self.stage_start = time.time()
            self._error_count = 0
            self._request_count = 0
            log.info("canary_advance tenant=%s stage=%s",
                     self.tenant_id, self.stage.value)

    def should_use_new(self) -> bool:
        """Route decision for a single request."""
        import random
        return random.random() < self.new_traffic_pct


# ── Fallback Inference Chain ──────────────────────────────────────

class InferenceError(Exception): pass

@dataclass
class InferenceNode:
    """One model/adapter endpoint in the fallback chain."""
    name: str
    adapter: Optional[AdapterRecord]
    _call_fn: object  # Callable[[str], str] -- actual inference

    def generate(self, prompt: str) -> str:
        try:
            return self._call_fn(prompt)
        except Exception as exc:
            log.warning("inference_fail node=%s error=%s", self.name, exc)
            raise InferenceError(f"{self.name}: {exc}") from exc


class FallbackInferenceChain:
    """Try tuned adapter -> base model -> deterministic fallback.
    
    Production pattern: never leave the user with no response.
    """
    def __init__(self, nodes: list[InferenceNode]):
        self.nodes = nodes

    def generate(self, prompt: str, *, tenant_id: str) -> tuple[str, str]:
        """Returns (response, node_name) for observability."""
        for node in self.nodes:
            try:
                result = node.generate(prompt)
                log.info("inference_ok tenant=%s node=%s", tenant_id, node.name)
                return result, node.name
            except InferenceError:
                continue
        # All nodes failed -- deterministic refusal
        log.error("inference_exhausted tenant=%s", tenant_id)
        return ("Service temporarily unavailable. Please retry.", 
                "deterministic_fallback")


# ── Training Data Quality Checks ──────────────────────────────────

@dataclass
class DataQualityReport:
    total_examples: int
    duplicates_removed: int
    pii_redacted: int
    format_errors: int
    quality_score: float   # 0-1

    @property
    def ready_for_training(self) -> bool:
        return (self.format_errors == 0 
                and self.quality_score >= 0.8
                and self.total_examples >= 50)  # Azure practical minimum


def validate_sft_dataset(examples: list[dict]) -> DataQualityReport:
    """Basic SFT dataset validation. Real pipeline adds PII detection."""
    seen_hashes = set()
    dupes = 0
    format_errors = 0
    
    for ex in examples:
        # Check required fields
        if not isinstance(ex.get("messages"), list):
            format_errors += 1
            continue
        roles = [m.get("role") for m in ex["messages"]]
        if "system" not in roles and "user" not in roles:
            format_errors += 1
            continue
        if "assistant" not in roles:
            format_errors += 1
            continue
        # Dedup by content hash
        h = hashlib.sha256(json.dumps(ex, sort_keys=True).encode()).hexdigest()
        if h in seen_hashes:
            dupes += 1
        seen_hashes.add(h)
    
    valid = len(examples) - format_errors - dupes
    quality = valid / max(len(examples), 1)
    
    return DataQualityReport(
        total_examples=len(examples),
        duplicates_removed=dupes,
        pii_redacted=0,     # real pipeline fills this
        format_errors=format_errors,
        quality_score=round(quality, 3),
    )
```

---

## Part 6: Architectural System Design Scenarios

### Scenario A -- Multi-Tenant LoRA SaaS Platform

**Problem.** A platform serving 200 enterprise customers, each wanting their own fine-tuned model behavior (tone, format, domain terminology) on a shared Llama-3.1-70B base. Requirements: per-tenant adapter isolation, hot-swap without downtime, canary rollout per tenant, cost must be < $50/tenant/month for inference.

| Axis | A1 Per-Tenant LoRA Adapters + vLLM (recommended) | A2 Per-Tenant Full Fine-Tuned Models | A3 Prompt Engineering Only |
|---|---|---|---|
| **GPU cost** | 1 base model (2x H100) + adapters in RAM; ~$2/tenant/mo | 200 separate deployments = 400 H100s | 1 base model; cheapest | 
| **Quality** | LoRA captures tone/format well at r=16-32 | Maximum capability shift | Limited by prompt length/complexity |
| **Isolation** | Adapter per request via `lora_request`; tenant from verified token | Full isolation but infeasible cost | Tenant separation via prompt only (weakest) |
| **Rollback** | Adapter registry version swap; < 100ms | Full model swap; minutes | Prompt version swap; instant |
| **Scalability** | vLLM `--max-loras 16` hot slots; LRU eviction for 200 tenants | Does not scale | Scales trivially |

**Decision.** A1 wins. 200 tenants on shared base with per-request adapter routing. Canary controller per tenant. Eval gate blocks promotion of adapters that fail forgetting checks. Cost: 2x H100 at $5/hr = ~$7,200/mo / 200 tenants = **$36/tenant/mo**.

**Architecture details:**
- Training: Each tenant submits data through PII pipeline -> QLoRA r=16 on shared training cluster (spot A100s) -> eval gate -> adapter registry
- Serving: vLLM with 16 hot adapter slots, LRU eviction for cold tenants, ~2ms overhead per token
- Canary: Per-tenant 5% -> 25% -> 100% over 72 hours with automatic rollback
- Monitoring: Per-tenant task accuracy on sampled live traffic; shared base model general capability check weekly

### Scenario B -- E-Commerce Email Classifier (Domain-Specific)

**Problem.** Classify incoming customer emails into 15 categories (return, refund, shipping, complaint, etc.) with >95% accuracy. Current system uses GPT-4.1 with few-shot prompting at $8/1M input, processing 50k emails/day (~500 tokens each). Monthly cost: 50k x 30 x 500 x $8/1M = **$6,000/mo** just for classification.

| Axis | B1 Fine-Tuned gpt-4.1-mini (recommended) | B2 Fine-Tuned Open-Source 7B | B3 Keep Prompting GPT-4.1 |
|---|---|---|---|
| **Training cost** | ~$54 (10k examples, 3 epochs, $1.80/1M) | ~$5 (QLoRA on spot A100) | $0 |
| **Inference cost** | $1.60/1M in -> **$1,200/mo** | ~$0.10/1M self-hosted -> **$75/mo** | **$6,000/mo** |
| **Accuracy** | 96-98% (structured classification is FT sweet spot) | 94-97% (depends on base model) | 93-95% (prompt brittleness) |
| **Maintenance** | Vendor handles serving; retrain monthly | Self-host GPU infra | Zero |
| **Vendor risk** | Model deprecation (have migration plan) | Full control | Prompt changes can regress |

**Decision.** B1 for quick win (5x cost reduction, better accuracy). Migrate to B2 for maximum savings if volume justifies self-hosting. Key: build the eval pipeline first, then the FT is a drop-in replacement validated by the same pipeline.

**Implementation:**
1. Collect 10k labeled emails from production (stratified across 15 categories)
2. PII redaction pipeline (customer names, order IDs, addresses)
3. Format as SFT: system prompt + email -> category label
4. Train gpt-4.1-mini via OpenAI API (~$54, ~30 min)
5. Eval gate: accuracy on 2k holdout, confusion matrix per category, safety check
6. Canary: 5% traffic for 48h, compare accuracy + latency vs baseline
7. Promote if all gates pass; schedule monthly retraining with latest labeled data

### Scenario C -- Multi-Domain Legal Drafting with Composable Adapters

**Problem.** Law firm with 5 practice areas (M&A, IP, litigation, employment, real estate). Each area needs domain-specific drafting style, citation format, and jurisdiction awareness. Shared base: Llama-3.1-70B. Some matters span multiple practice areas.

**Architecture:**
- **Base adapter** (style): Trained on 50k general legal writing examples. Applied to all requests.
- **Domain adapters** (5x): Each trained on 10k domain-specific examples. Applied per practice area.
- **Composition**: For cross-domain matters, stack base + primary domain + secondary domain adapters.

**Risks and mitigations:**
- **Adapter interference**: Test all 10 pairwise domain combinations. If quality drops > 2%, retrain secondary adapter with primary frozen.
- **Forgetting**: Each domain adapter validated against general legal holdout + all other domains.
- **Confidentiality**: Matter-level data never crosses practice area boundaries during training. Adapters do not encode client data -- they encode *style*. Facts come from RAG.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---|---|---|---|
| **Catastrophic forgetting** | Narrow task overfitting | General capability holdout drops | LoRA, low rank, replay mixing, early stopping |
| **Data poisoning** | Malicious/mislabeled examples | Validation loss anomalies; safety regression | PII pipeline; provenance; human review |
| **Reward hacking** | Gameable grader in RFT/GRPO | Reward up, human quality flat | Diverse rewards; human spot-checks |
| **DPO collapse** | beta too low; weak base | Always outputs "chosen" | beta sweep; verify base quality first |
| **Vendor deprecation** | Model snapshot deprecated | Death date monitoring | Export weights; OSS fallback |
| **Serve-dtype mismatch** | Train BF16, serve INT8 | Quality delta on eval | Validate serve-dtype in pipeline |
| **Adapter interference** | Composing independent adapters | Quality drop on stack | Train with others frozen; orthogonal reg |
| **Stale adapter** | Domain drift | Live traffic score decline | Scheduled retraining; continuous eval |
| **PII memorization** | Training data contains PII | Extraction attacks | PII pipeline before training; differential privacy |
| **Wrong modules** | LoRA on suboptimal layers | Poor quality despite data | Gradient norm profiling; ablation |

---

## Interview Q&A

**Q1. When should I fine-tune instead of using RAG?**
I fine-tune when I need stable behavior or style changes -- formatting, tone, tool-call patterns, domain vocabulary. RAG is for fresh or private knowledge retrieval. They solve different problems and the best answer is often both: RAG for facts, tuning for behavior.

**Q2. Explain LoRA to me. Why does it work?**
LoRA keeps base weights frozen and learns a low-rank delta: W' = W + (alpha/r) * B @ A, where B is initialized to zeros so training starts from the pretrained model. It works because weight updates during fine-tuning empirically live in a low-rank subspace -- you do not need to update all parameters. A rank-16 LoRA on Llama-3-8B trains only 0.21% of parameters while matching full fine-tuning quality on most tasks.

**Q3. Walk me through QLoRA. What are the three innovations?**
First, NF4 quantization: information-theoretically optimal 4-bit format for normally distributed weights. Second, double quantization: quantize the quantization constants themselves, saving ~0.373 bits/param (~3 GB on 65B). Third, paged optimizers: unified CPU+GPU memory that pages optimizer states on OOM spikes. Together they put 65B fine-tuning on a single 48GB GPU where full FT needed >780GB.

**Q4. SFT vs DPO vs GRPO -- when do I use each?**
SFT is stage 1: teach the model to produce the right kind of output from curated (input, output) pairs. DPO is stage 2: teach preferences from (chosen, rejected) pairs without training a reward model. GRPO is stage 3: group-sample G=64 completions, score with a verifiable reward, update with group-relative advantages. Each stage needs the prior one as a foundation. I use GRPO only when I have a reliable automated grader -- math proofs, code tests, structured outputs.

**Q5. How do you detect catastrophic forgetting?**
I run a general capability holdout -- a frozen slice of MMLU, HumanEval, and safety prompts -- before and after fine-tuning. I set a forgetting budget: if any capability drops more than 1%, the adapter fails the eval gate. The key insight from Biderman et al. is to track average performance across all tasks, not just the latest one.

**Q6. How do you serve 200 tenants with different adapters?**
One base model on vLLM with `--enable-lora --max-loras 16`. Each request carries a verified tenant identity that maps to an adapter in the registry. Punica SGMV kernel adds ~2ms/token overhead. Hot tenants stay in GPU memory; cold ones LRU-evict and reload in <100ms. Cost: ~$36/tenant/month on 2x H100 vs $3,600/tenant for separate deployments.

**Q7. What is your eval gate before promoting an adapter?**
Four checks: (1) task quality meets domain threshold, (2) forgetting delta on general holdout < 1%, (3) safety suite shows zero regressions, (4) serve-dtype parity -- the quantized serving format must not degrade quality > 0.5% vs training precision. All four must pass before the adapter enters canary.

**Q8. How do you handle vendor model deprecation?**
I track death dates -- OpenAI disables new FT jobs on older models (e.g., 2027-01-06 for gpt-4o). I maintain an OSS fallback path (equivalent QLoRA on Llama/Qwen) so deprecation is a migration, not an emergency. Training data and eval pipelines are vendor-agnostic by design.

**Q9. What is the biggest anti-pattern in fine-tuning?**
Using fine-tuning to memorize knowledge that should live in retrieval. It creates stale answers and retraining churn. Fine-tuning is for behavior, not facts. The second biggest: skipping the eval gate and deploying based on training loss alone.

**Q10. DPO is not working. What do you check?**
First, is beta too low? If so, the policy collapses to always outputting "chosen." Second, can the base model already produce reasonable outputs? DPO on an unaligned base is unstable. Third, are the chosen/rejected pairs clean? Contradictory or ambiguous pairs teach noise. I sweep beta in [0.05, 0.3] and verify the base with manual inspection before DPO.

**Q11. How do you handle PII in training data?**
I run a four-stage pipeline: detect (Presidio + custom NER), redact (replace with type tokens preserving structure), audit (immutable log of what was redacted), and gate (re-scan redacted data; any remaining PII blocks the training job). This happens before any GPU touches the data because once PII is in weights, extraction attacks can recover it.

**Q12. Full fine-tune vs LoRA -- give me a concrete decision framework.**
Full FT only if: (a) I own the weights (open-source), (b) I have the GPU budget (8x H100 for 70B), (c) I need deep distribution shift that low-rank cannot capture, and (d) I will not need multi-tenant adapter serving. For everything else -- and that is 90%+ of production use cases -- LoRA/QLoRA wins on cost, forgetting risk, serving flexibility, and rollback speed.

---

## Key Numbers to Memorize

### LoRA / QLoRA
| Number | What |
|---|---|
| **r = 8-64** | Typical LoRA rank range; 4-256 full range |
| **alpha = 2r** | Standard scaling; ratio alpha/r controls update magnitude |
| **0.21%** | Trainable params: Llama-3-8B, r=16, all attention |
| **~10,000x / ~3x** | LoRA: fewer trainable params / lower GPU memory vs full FT |
| **65B on 48GB** | QLoRA headline; full FT needed >780 GB |
| **0.373 bits/param** | Double quantization savings; ~3 GB on 65B |
| **99.3% / 97.8%** | Guanaco QLoRA: ChatGPT parity on Vicuna |
| **~2ms/token** | Punica SGMV adapter overhead at batch 16 |
| **16 / 64** | vLLM defaults: max_loras / max_lora_rank |

### Training Data
| Number | What |
|---|---|
| **1,000** | LIMA: curated examples rivaling 50k+ noisy |
| **~13k / ~33k** | InstructGPT: SFT demonstrations / RLHF comparisons |
| **10 / 50** | Azure: hard minimum / practical start examples |
| **G = 64** | GRPO: group sample size |
| **beta = 0.1** | DPO: default KL penalty |

### Cost
| Number | What |
|---|---|
| **$1.80 / $12.00** | OpenAI training: gpt-4.1-mini / gpt-4.1 per 1M tokens |
| **$3-6** | QLoRA 7B SFT, 10k examples, spot A100 |
| **$144-288** | QLoRA 70B SFT, 50k examples, 4xA100 spot |
| **$36/tenant/mo** | 200-tenant LoRA serving on 2x H100 |

### Deadlines
| Date | Event |
|---|---|
| **2026-10-23** | OpenAI: ft-o4-mini jobs disabled |
| **2026-11-30** | OpenAI: Evaluations API deprecated |
| **2027-01-06** | OpenAI: no new FT jobs on gpt-4o-2024-08-06 |

---

## Quick Reference

- **Decision ladder:** Prompt -> RAG -> Workflow -> Fine-tune (only after diminishing returns)
- **Default method:** QLoRA r=16, alpha=32, all attention projections, 3 epochs, early stopping
- **Preference:** DPO beta=0.1 as default; GRPO only with verifiable rewards
- **Eval gate:** 4 checks -- task quality, forgetting < 1%, safety = 0 regressions, serve-dtype < 0.5% delta
- **Serving:** vLLM multi-LoRA with per-request adapter routing; Punica kernel ~2ms overhead
- **Canary:** 5% -> 25% -> 100% over 72h; automatic rollback on SLO violation
- **PII:** Detect -> Redact -> Audit -> Gate, all before GPU
- **Anti-patterns:** FT for knowledge (use RAG), deploying on training loss alone, DPO on unaligned base
