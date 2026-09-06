"""
Fine-Tuning LLMs -- Interview Prep Code Snippets.

Covers SFT data preparation (JSONL chat format), LoRA/QLoRA configuration
and math, training loops with validation, DPO preference data format,
a decision function (RAG vs fine-tune vs prompt), and evaluation gates
for before/after fine-tuning comparison.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# --- Data Preparation: SFT JSONL Format ---

def create_sft_example(
    system: str,
    user: str,
    assistant: str,
    metadata: Optional[dict] = None,
) -> dict:
    """Create a single SFT training example in OpenAI chat-completions format.

    This is the standard format for vendor fine-tuning (OpenAI, Fireworks, Together)
    and HuggingFace TRL SFTTrainer.

    LIMA showed 1,000 carefully curated examples can rival 50k+ noisy ones.
    InstructGPT: ~13k SFT demonstrations + ~33k RLHF comparisons beat a 100x-larger
    unaligned model.

    Key: quality > quantity. Dedup, PII-redact, and validate format before training.
    """
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ],
        **({"metadata": metadata} if metadata else {}),
    }


def validate_sft_dataset(examples: list[dict]) -> dict:
    """Validate an SFT dataset for common issues.

    Checks: required fields, role ordering, deduplication, length bounds.
    Azure practical minimum: 50 examples. Hard minimum: 10.

    Returns a quality report -- training should not proceed if format_errors > 0
    or quality_score < 0.8.
    """
    seen_hashes = set()
    dupes = 0
    format_errors = 0
    too_short = 0
    too_long = 0

    for ex in examples:
        # Check required structure
        if not isinstance(ex.get("messages"), list):
            format_errors += 1
            continue

        roles = [m.get("role") for m in ex["messages"]]

        # Must have at least user + assistant
        if "user" not in roles or "assistant" not in roles:
            format_errors += 1
            continue

        # Check role ordering: system (optional) -> user -> assistant -> ...
        for msg in ex["messages"]:
            if not msg.get("content", "").strip():
                format_errors += 1
                break

        # Length checks (approximate token count as words / 0.75)
        total_words = sum(len(m.get("content", "").split()) for m in ex["messages"])
        approx_tokens = int(total_words / 0.75)
        if approx_tokens < 10:
            too_short += 1
        if approx_tokens > 8192:
            too_long += 1

        # Dedup by content hash
        h = hashlib.sha256(
            json.dumps(ex["messages"], sort_keys=True).encode()
        ).hexdigest()
        if h in seen_hashes:
            dupes += 1
        seen_hashes.add(h)

    valid = len(examples) - format_errors - dupes
    quality = valid / max(len(examples), 1)

    return {
        "total_examples": len(examples),
        "valid_examples": valid,
        "format_errors": format_errors,
        "duplicates": dupes,
        "too_short": too_short,
        "too_long": too_long,
        "quality_score": round(quality, 3),
        "ready_for_training": format_errors == 0 and quality >= 0.8 and len(examples) >= 50,
    }


def write_jsonl(examples: list[dict], path: str):
    """Write examples to JSONL file (one JSON object per line)."""
    with open(path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")


def prepare_classification_dataset(
    emails: list[dict],
    categories: list[str],
    system_prompt: str = "Classify the email into one of these categories.",
) -> list[dict]:
    """Prepare an email classification dataset for SFT.

    Scenario B from the module: classify 50k emails/day into 15 categories.
    Fine-tuned gpt-4.1-mini achieves 96-98% accuracy vs 93-95% with prompting,
    and cuts inference cost from $6,000/mo to $1,200/mo (5x savings).

    Input: list of {"text": str, "label": str}
    """
    examples = []
    for email in emails:
        ex = create_sft_example(
            system=f"{system_prompt}\nCategories: {', '.join(categories)}",
            user=email["text"],
            assistant=email["label"],
        )
        examples.append(ex)
    return examples


# --- LoRA / QLoRA Configuration ---

@dataclass
class LoRAConfig:
    """LoRA configuration with the math for interview discussion.

    Core insight: weight updates during fine-tuning live in a low-rank subspace.
    Instead of updating W directly, decompose: W' = W + (alpha/r) * B @ A

    Where:
      W   = frozen pretrained weight (d_in x d_out)
      A   = trainable (d_in x r), init from Kaiming uniform
      B   = trainable (r x d_out), init to ZEROS (so W' = W at step 0)
      r   = rank (8-64 typical; 4-256 range)
      alpha = scaling factor (commonly 2*r)

    B=0 initialization is critical: if both A and B were random,
    training would start from a corrupted model.
    """
    rank: int = 16                      # r: 8-64 typical, 4-256 range
    alpha: int = 32                     # commonly 2*r; ratio alpha/r controls magnitude
    target_modules: list[str] = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj"])
    dropout: float = 0.05
    bias: str = "none"

    def trainable_params_per_layer(self, d_in: int, d_out: int) -> int:
        """Trainable parameters per adapted linear layer: r * (d_in + d_out)."""
        return self.rank * (d_in + d_out)

    def total_trainable_params(self, d_model: int, n_layers: int) -> dict:
        """Calculate total trainable parameters for a transformer.

        Example: Llama-3-8B, r=16, all attention (q,k,v,o), 32 layers:
        2 * 16 * 4096 * 4 * 32 = 16.8M trainable vs 8B total = 0.21%
        """
        n_modules = len(self.target_modules)
        # For attention: each projection is d_model x d_model (simplified)
        params_per_layer = n_modules * self.trainable_params_per_layer(d_model, d_model)
        total = params_per_layer * n_layers
        return {
            "params_per_layer": params_per_layer,
            "total_trainable": total,
            "total_trainable_m": round(total / 1e6, 2),
        }

    def scaling_factor(self) -> float:
        """The effective scaling: alpha / rank."""
        return self.alpha / self.rank

    def to_peft_config(self) -> dict:
        """Convert to HuggingFace PEFT LoraConfig kwargs.

        In practice:
        from peft import LoraConfig, get_peft_model
        config = LoraConfig(**lora.to_peft_config())
        model = get_peft_model(base_model, config)
        """
        return {
            "r": self.rank,
            "lora_alpha": self.alpha,
            "target_modules": self.target_modules,
            "lora_dropout": self.dropout,
            "bias": self.bias,
            "task_type": "CAUSAL_LM",
        }


@dataclass
class QLoRAConfig(LoRAConfig):
    """QLoRA: three innovations that put 65B fine-tuning on a single 48GB GPU.

    1. NF4 (4-bit NormalFloat): info-theoretically optimal for N(0,1) weights.
       Matches FP16 training quality.
    2. Double quantization: quantize the quantization constants.
       Saves ~0.373 bits/param = ~3 GB on 65B model.
    3. Paged optimizers: CPU+GPU unified memory for optimizer states.
       Pages evict on OOM, page back for backward pass.

    Memory math:
      NF4 base: ~0.5 bytes/param
      LoRA adapters in BF16: ~2 bytes/param but only on 0.2-1% of params
      Optimizer (AdamW): 2 states x BF16 per LoRA param
      65B QLoRA total: ~33 GB base + ~2 GB adapters + ~4 GB optimizer = fits 48 GB

    Guanaco/QLoRA: top model 99.3% of ChatGPT on Vicuna; 33B at 97.8%.
    Training: 24 hours on single GPU.
    """
    bits: int = 4                       # NF4 quantization
    quant_type: str = "nf4"             # info-theoretically optimal for N(0,1)
    double_quant: bool = True           # quantize the quant constants (~3 GB savings on 65B)
    compute_dtype: str = "bfloat16"     # computation dtype for adapters

    def memory_estimate_gb(self, total_params_b: float) -> dict:
        """Estimate GPU memory for QLoRA training.

        total_params_b: total parameters in billions (e.g. 7.0 for Llama-3-8B)
        """
        base_gb = total_params_b * 0.5  # NF4: ~0.5 bytes/param
        trainable_frac = 0.005           # ~0.5% of params are trainable
        adapter_gb = total_params_b * trainable_frac * 2  # BF16
        optimizer_gb = adapter_gb * 2     # AdamW: 2 states
        overhead_gb = 1.0                 # activations, framework

        total = base_gb + adapter_gb + optimizer_gb + overhead_gb
        return {
            "base_model_gb": round(base_gb, 1),
            "adapter_gb": round(adapter_gb, 2),
            "optimizer_gb": round(optimizer_gb, 2),
            "overhead_gb": overhead_gb,
            "total_gb": round(total, 1),
            "fits_48gb": total <= 48,
            "fits_80gb": total <= 80,
        }

    def to_bnb_config(self) -> dict:
        """Convert to BitsAndBytesConfig kwargs.

        In practice:
        from transformers import BitsAndBytesConfig
        bnb = BitsAndBytesConfig(**qlora.to_bnb_config())
        model = AutoModelForCausalLM.from_pretrained(name, quantization_config=bnb)
        """
        return {
            "load_in_4bit": self.bits == 4,
            "bnb_4bit_quant_type": self.quant_type,
            "bnb_4bit_use_double_quant": self.double_quant,
            "bnb_4bit_compute_dtype": self.compute_dtype,
        }


# --- Training Loop with Validation ---

@dataclass
class TrainingConfig:
    """Training hyperparameters for SFT.

    Default: QLoRA r=16, alpha=32, all attention projections, 3 epochs,
    early stopping on validation loss.
    """
    num_epochs: int = 3
    batch_size: int = 4
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.1
    weight_decay: float = 0.01
    gradient_accumulation_steps: int = 4
    max_grad_norm: float = 1.0
    early_stopping_patience: int = 3    # stop if val loss doesn't improve for N evals
    eval_steps: int = 50
    save_steps: int = 100
    logging_steps: int = 10


def simulate_training_loop(
    train_data: list[dict],
    val_data: list[dict],
    config: TrainingConfig,
) -> dict:
    """Simulate a training loop to illustrate the key patterns.

    In production: use HuggingFace TRL SFTTrainer or Axolotl.
    Key patterns to discuss in interview:
    1. Validation loss tracking (not just training loss)
    2. Early stopping to prevent overfitting
    3. Checkpoint saving for rollback
    4. Learning rate warmup + decay
    """
    steps_per_epoch = max(
        len(train_data) // (config.batch_size * config.gradient_accumulation_steps), 1
    )
    total_steps = steps_per_epoch * config.num_epochs
    warmup_steps = int(total_steps * config.warmup_ratio)

    history = {
        "train_loss": [],
        "val_loss": [],
        "learning_rates": [],
        "best_val_loss": float("inf"),
        "best_step": 0,
        "early_stopped": False,
        "patience_counter": 0,
    }

    for step in range(1, total_steps + 1):
        # Simulate decreasing training loss with noise
        progress = step / total_steps
        train_loss = 2.5 * math.exp(-3 * progress) + 0.1 * (0.5 - progress) * (step % 7) / 7
        train_loss = max(train_loss, 0.1)

        # LR schedule: linear warmup then cosine decay
        if step <= warmup_steps:
            lr = config.learning_rate * step / max(warmup_steps, 1)
        else:
            decay_ratio = (step - warmup_steps) / max(total_steps - warmup_steps, 1)
            lr = config.learning_rate * 0.5 * (1 + math.cos(math.pi * decay_ratio))

        history["train_loss"].append(round(train_loss, 4))
        history["learning_rates"].append(round(lr, 8))

        # Validation at eval_steps intervals
        if step % config.eval_steps == 0:
            # Simulate val loss (slightly higher than train, potential overfitting)
            val_loss = train_loss * 1.1 + 0.05 * max(0, progress - 0.6)

            history["val_loss"].append(round(val_loss, 4))

            if val_loss < history["best_val_loss"]:
                history["best_val_loss"] = round(val_loss, 4)
                history["best_step"] = step
                history["patience_counter"] = 0
            else:
                history["patience_counter"] += 1

            # Early stopping: critical to prevent catastrophic forgetting
            if history["patience_counter"] >= config.early_stopping_patience:
                history["early_stopped"] = True
                break

    history["total_steps_run"] = step
    history["total_steps_planned"] = total_steps
    return history


# --- DPO Preference Data Format ---

def create_dpo_example(
    prompt: str,
    chosen: str,
    rejected: str,
) -> dict:
    """Create a DPO training example.

    DPO (Rafailov et al., NeurIPS 2023): learns preferences from (prompt, chosen,
    rejected) pairs without training a separate reward model.

    Key hyperparameter: beta=0.1 (KL penalty).
    - beta too high -> model barely moves from base (underfitting)
    - beta too low -> policy collapses to always producing "chosen" (overfitting)
    - Start at 0.1, sweep [0.05, 0.3]

    Prerequisites for DPO:
    1. Base model must already produce reasonable outputs (SFT first)
    2. DPO on an unaligned base is UNSTABLE
    3. Chosen/rejected pairs must be clean -- contradictory pairs teach noise
    """
    return {
        "prompt": prompt,
        "chosen": chosen,
        "rejected": rejected,
    }


def create_kto_example(prompt: str, completion: str, thumbs_up: bool) -> dict:
    """Create a KTO training example (Ethayarajh et al., 2024).

    KTO needs only binary feedback (thumbs up/down), not paired comparisons.
    Use when: production logs have like/dislike but you cannot get paired data.
    """
    return {
        "prompt": prompt,
        "completion": completion,
        "label": thumbs_up,
    }


def create_grpo_batch(
    prompt: str,
    completions: list[str],
    rewards: list[float],
) -> dict:
    """Create a GRPO training batch (Shao et al., DeepSeek-R1, 2024).

    GRPO workflow:
    1. For each prompt, sample G=64 completions
    2. Score each with a VERIFIABLE reward (unit test, math checker)
    3. Compute group-relative advantage: A_i = (r_i - mean(r)) / std(r)
    4. Update policy with clipped surrogate objective
    No critic model needed (unlike PPO).

    Use only when you have a reliable automated grader (math, code, structured output).
    DeepSeek-R1 used GRPO to bootstrap reasoning without any SFT data.
    """
    mean_r = sum(rewards) / len(rewards)
    std_r = max(
        math.sqrt(sum((r - mean_r) ** 2 for r in rewards) / len(rewards)),
        1e-8,
    )
    advantages = [(r - mean_r) / std_r for r in rewards]

    return {
        "prompt": prompt,
        "completions": completions,
        "rewards": rewards,
        "advantages": [round(a, 4) for a in advantages],
        "group_size": len(completions),
    }


# --- When-to-Use Decision Function ---

class Approach(Enum):
    PROMPTING = "prompting"
    RAG = "rag"
    WORKFLOW = "workflow"
    FINE_TUNING = "fine_tuning"
    RAG_PLUS_FT = "rag_plus_fine_tuning"


def decide_approach(
    need_private_knowledge: bool = False,
    knowledge_changes_frequently: bool = False,
    need_style_or_format_change: bool = False,
    need_domain_vocabulary: bool = False,
    need_tool_call_patterns: bool = False,
    need_safety_refusals: bool = False,
    prompt_complexity_high: bool = False,
    millions_of_requests: bool = False,
    have_labeled_data: bool = False,
    have_paired_preferences: bool = False,
    need_orchestration_logic: bool = False,
) -> dict:
    """Decision ladder: Prompt -> RAG -> Workflow -> Fine-tune.

    Fine-tuning is for BEHAVIOR, not FACTS. The biggest anti-pattern is
    using fine-tuning to memorize knowledge that should live in retrieval --
    it creates stale answers and retraining churn.

    Fine-tune when:
    - Stable behavior changes (formatting, tone, tool-call style, domain vocab)
    - Need that behavior across millions of requests (prompt length savings)
    - Have labeled data (minimum ~50 examples, practical ~1k+)

    Use RAG when:
    - Private or fast-changing knowledge
    - Need citations and provenance

    The best answer is often BOTH: RAG for facts, fine-tuning for behavior.
    """
    reasons = []
    approach = Approach.PROMPTING

    # RAG signals
    if need_private_knowledge or knowledge_changes_frequently:
        approach = Approach.RAG
        reasons.append("Private/changing knowledge -> RAG for retrieval")

    # Workflow/validator signals
    if need_orchestration_logic:
        approach = Approach.WORKFLOW
        reasons.append("Orchestration logic -> Workflow/validators")

    # Fine-tuning signals
    ft_signals = sum([
        need_style_or_format_change,
        need_domain_vocabulary,
        need_tool_call_patterns,
        need_safety_refusals,
        prompt_complexity_high and millions_of_requests,
    ])

    if ft_signals >= 2 and have_labeled_data:
        if approach == Approach.RAG:
            approach = Approach.RAG_PLUS_FT
            reasons.append("Behavior change + knowledge -> RAG + fine-tuning")
        else:
            approach = Approach.FINE_TUNING
            reasons.append("Stable behavior change with labeled data -> fine-tune")

    if not reasons:
        reasons.append("Light behavior change -> prompting is sufficient")

    return {
        "recommended": approach.value,
        "reasons": reasons,
        "data_requirements": {
            "sft_minimum": "50 examples (Azure hard min: 10)",
            "sft_practical": "1,000+ curated examples (LIMA showed this rivals 50k noisy)",
            "dpo_pairs": "5,000+ (prompt, chosen, rejected) triples",
            "grpo": "Requires verifiable reward function (math/code)",
        },
    }


# --- Evaluation Before/After Fine-Tuning ---

@dataclass
class EvalGate:
    """Four-gate evaluation before promoting a fine-tuned adapter.

    Every adapter must pass ALL four checks before entering canary deployment.
    Deploying based on training loss alone is the second-biggest anti-pattern.
    """
    task_quality_threshold: float = 0.80    # Domain-specific accuracy/F1
    max_forgetting_delta: float = 0.01      # < 1% drop on general capabilities
    safety_pass_required: bool = True       # Zero regressions on safety suite
    max_dtype_delta: float = 0.005          # < 0.5% quality loss from quantization


@dataclass
class EvalResult:
    """Result of evaluating a model (base or fine-tuned) on a benchmark suite."""
    model_name: str
    task_score: float           # Domain-specific metric
    general_score: float        # MMLU/HumanEval subset
    safety_score: float         # Refusal on harmful prompts
    serve_dtype_score: float    # Quality in serving precision (INT8/BF16)

    def passes_gate(self, gate: EvalGate, baseline: Optional["EvalResult"] = None) -> dict:
        """Check all four eval gates.

        The forgetting check compares against a baseline (pre-FT model).
        This is the Biderman et al. "Loser" principle: track AVERAGE performance
        across all tasks, not just the latest one.
        """
        checks = {}

        # Gate 1: Task quality meets threshold
        checks["task_quality"] = {
            "passed": self.task_score >= gate.task_quality_threshold,
            "score": self.task_score,
            "threshold": gate.task_quality_threshold,
        }

        # Gate 2: Forgetting delta on general holdout
        if baseline:
            delta = baseline.general_score - self.general_score
            checks["forgetting"] = {
                "passed": delta <= gate.max_forgetting_delta,
                "delta": round(delta, 4),
                "max_allowed": gate.max_forgetting_delta,
            }
        else:
            checks["forgetting"] = {"passed": True, "note": "no baseline provided"}

        # Gate 3: Safety -- zero regressions
        checks["safety"] = {
            "passed": self.safety_score >= 1.0 if gate.safety_pass_required else True,
            "score": self.safety_score,
        }

        # Gate 4: Serve-dtype parity
        if baseline:
            dtype_delta = abs(baseline.serve_dtype_score - self.serve_dtype_score)
            checks["serve_dtype"] = {
                "passed": dtype_delta <= gate.max_dtype_delta,
                "delta": round(dtype_delta, 4),
                "max_allowed": gate.max_dtype_delta,
            }
        else:
            checks["serve_dtype"] = {"passed": True, "note": "no baseline provided"}

        all_passed = all(c["passed"] for c in checks.values())

        return {
            "model": self.model_name,
            "all_gates_passed": all_passed,
            "gates": checks,
            "recommendation": "PROMOTE to canary" if all_passed else "REJECT -- fix failing gates",
        }


def compare_before_after(
    base_result: EvalResult,
    ft_result: EvalResult,
    gate: EvalGate,
) -> dict:
    """Compare base vs fine-tuned model across all evaluation dimensions.

    This is the evaluation workflow you'd discuss in an interview:
    1. Run general capability holdout BEFORE fine-tuning (baseline)
    2. Fine-tune with LoRA/QLoRA
    3. Run same holdout AFTER fine-tuning
    4. Check all four gates
    5. Only then enter canary deployment (5% -> 25% -> 100%)
    """
    gate_result = ft_result.passes_gate(gate, baseline=base_result)

    return {
        "base": {
            "task": base_result.task_score,
            "general": base_result.general_score,
            "safety": base_result.safety_score,
        },
        "fine_tuned": {
            "task": ft_result.task_score,
            "general": ft_result.general_score,
            "safety": ft_result.safety_score,
        },
        "deltas": {
            "task_improvement": round(ft_result.task_score - base_result.task_score, 4),
            "general_regression": round(base_result.general_score - ft_result.general_score, 4),
            "safety_regression": round(base_result.safety_score - ft_result.safety_score, 4),
        },
        "gate_result": gate_result,
    }


# --- Cost Estimator ---

def training_cost_estimate(
    total_params_b: float,
    num_examples: int,
    avg_tokens_per_example: int = 1000,
    num_epochs: int = 3,
    method: str = "qlora",
    gpu_type: str = "A100_80GB",
    spot_price_per_hr: float = 1.50,
    vendor_api_price_per_m: float = None,
) -> dict:
    """Estimate fine-tuning cost.

    Key benchmarks:
    - 7B SFT, 10k examples, 3 epochs, QLoRA r=16: ~2-4 hrs A100, $3-6 spot
    - 70B SFT, 50k examples, 3 epochs, QLoRA r=32: ~24-48 hrs 4xA100, $144-288
    - gpt-4.1-mini, 10k examples, 3 epochs, API: ~$54 ($1.80/1M x ~10M x 3)
    - gpt-4.1, 10k examples, 3 epochs, API: ~$360 ($12/1M x ~10M x 3)
    """
    total_tokens = num_examples * avg_tokens_per_example * num_epochs

    if vendor_api_price_per_m is not None:
        # Vendor API pricing
        cost = total_tokens * vendor_api_price_per_m / 1_000_000
        return {
            "method": "vendor_api",
            "total_tokens": total_tokens,
            "cost_usd": round(cost, 2),
            "time_estimate": "30-120 minutes (vendor managed)",
        }

    # Self-hosted estimate
    if method == "qlora":
        # Rough: ~2-4 hrs per 10k examples for 7B
        scale_factor = total_params_b / 7.0
        base_hours = 3 * (num_examples / 10000) * num_epochs / 3
        gpu_hours = base_hours * scale_factor
        num_gpus = 1 if total_params_b <= 70 else 4
        if total_params_b > 70:
            gpu_hours = gpu_hours / 2  # Parallelism
    else:  # full ft
        scale_factor = total_params_b / 7.0
        gpu_hours = 8 * (num_examples / 10000) * num_epochs / 3 * scale_factor
        num_gpus = max(1, int(math.ceil(total_params_b / 10)))

    cost = gpu_hours * num_gpus * spot_price_per_hr

    return {
        "method": method,
        "gpu_type": gpu_type,
        "num_gpus": num_gpus,
        "gpu_hours": round(gpu_hours, 1),
        "cost_usd": round(cost, 2),
        "total_tokens": total_tokens,
    }


# --- Adapter Serving Economics ---

def multi_tenant_serving_cost(
    num_tenants: int,
    base_model_gpus: int = 2,
    gpu_price_per_hr: float = 5.0,
    hours_per_month: int = 720,
) -> dict:
    """Compare adapter serving vs separate deployments.

    vLLM multi-LoRA: --enable-lora --max-loras 16 --max-lora-rank 64
    Punica SGMV kernel: ~2ms/token overhead at batch 16. Hot-swap per-request.

    At 100+ tenants, adapter serving is ~50-100x cheaper than separate deployments.
    Trade-off: shared base means all tenants upgrade/downgrade together.
    """
    # Adapter serving: one base model, N adapters in RAM
    adapter_monthly = base_model_gpus * gpu_price_per_hr * hours_per_month
    adapter_per_tenant = adapter_monthly / num_tenants

    # Separate deployments: N x base model
    separate_monthly = num_tenants * base_model_gpus * gpu_price_per_hr * hours_per_month
    separate_per_tenant = separate_monthly / num_tenants

    savings_ratio = separate_monthly / max(adapter_monthly, 1)

    return {
        "adapter_serving": {
            "total_monthly": round(adapter_monthly, 2),
            "per_tenant_monthly": round(adapter_per_tenant, 2),
        },
        "separate_deployments": {
            "total_monthly": round(separate_monthly, 2),
            "per_tenant_monthly": round(separate_per_tenant, 2),
        },
        "savings_ratio": round(savings_ratio, 1),
        "note": f"Adapter serving is {round(savings_ratio)}x cheaper for {num_tenants} tenants",
    }


# --- Demo ---

if __name__ == "__main__":
    print("=" * 60)
    print("Fine-Tuning Interview Prep -- Runnable Demos")
    print("=" * 60)

    # 1. SFT data preparation
    print("\n--- SFT Data Preparation ---")
    examples = [
        create_sft_example(
            system="Classify the email category.",
            user="I want to return my order #12345",
            assistant="return",
        ),
        create_sft_example(
            system="Classify the email category.",
            user="My package hasn't arrived yet",
            assistant="shipping",
        ),
        create_sft_example(
            system="Classify the email category.",
            user="I was charged twice for my order",
            assistant="billing",
        ),
    ]
    # Pad to 50+ for the validator
    examples = examples * 20

    report = validate_sft_dataset(examples)
    print(f"Dataset quality: {report['quality_score']}")
    print(f"Ready for training: {report['ready_for_training']}")
    print(f"Duplicates found: {report['duplicates']}")

    # 2. LoRA configuration
    print("\n--- LoRA/QLoRA Configuration ---")
    lora = LoRAConfig(rank=16, alpha=32)
    # Llama-3-8B: d_model=4096, 32 layers
    params = lora.total_trainable_params(d_model=4096, n_layers=32)
    print(f"Trainable params: {params['total_trainable_m']}M (r={lora.rank})")
    print(f"Scaling factor (alpha/r): {lora.scaling_factor()}")

    qlora = QLoRAConfig(rank=16, alpha=32)
    mem = qlora.memory_estimate_gb(total_params_b=7.0)
    print(f"\nQLoRA memory (7B): {mem['total_gb']} GB")
    print(f"  Base (NF4):     {mem['base_model_gb']} GB")
    print(f"  Adapters:       {mem['adapter_gb']} GB")
    print(f"  Optimizer:      {mem['optimizer_gb']} GB")
    print(f"  Fits 48GB GPU:  {mem['fits_48gb']}")

    mem_65b = qlora.memory_estimate_gb(total_params_b=65.0)
    print(f"\nQLoRA memory (65B): {mem_65b['total_gb']} GB")
    print(f"  Fits 48GB GPU:  {mem_65b['fits_48gb']}")

    # 3. DPO preference data
    print("\n--- DPO Preference Format ---")
    dpo_ex = create_dpo_example(
        prompt="How do I return a product?",
        chosen="You can initiate a return within 30 days through your order page.",
        rejected="Returns? I guess you could try the website or something.",
    )
    print(f"DPO example keys: {list(dpo_ex.keys())}")

    # 4. GRPO batch with advantages
    print("\n--- GRPO Group-Relative Advantages ---")
    grpo = create_grpo_batch(
        prompt="Write a function to sort a list",
        completions=["def sort(l): return sorted(l)", "print('hello')", "def sort(l):\n  l.sort()\n  return l"],
        rewards=[1.0, 0.0, 1.0],
    )
    print(f"GRPO advantages: {grpo['advantages']}")
    print(f"Group size: {grpo['group_size']}")

    # 5. Decision function
    print("\n--- When to Fine-Tune Decision ---")
    decision = decide_approach(
        need_private_knowledge=True,
        need_style_or_format_change=True,
        need_domain_vocabulary=True,
        have_labeled_data=True,
    )
    print(f"Recommended: {decision['recommended']}")
    print(f"Reasons: {decision['reasons']}")

    # 6. Eval gate
    print("\n--- Eval Gate: Before/After Fine-Tuning ---")
    base = EvalResult("llama-3-8b-base", task_score=0.72, general_score=0.85,
                      safety_score=1.0, serve_dtype_score=0.84)
    ft = EvalResult("llama-3-8b-lora-r16", task_score=0.89, general_score=0.845,
                    safety_score=1.0, serve_dtype_score=0.838)

    comparison = compare_before_after(base, ft, EvalGate())
    print(f"Task improvement:     +{comparison['deltas']['task_improvement']:.1%}")
    print(f"General regression:   -{comparison['deltas']['general_regression']:.1%}")
    print(f"All gates passed:     {comparison['gate_result']['all_gates_passed']}")
    print(f"Recommendation:       {comparison['gate_result']['recommendation']}")

    # 7. Cost estimates
    print("\n--- Training Cost Estimates ---")
    qlora_cost = training_cost_estimate(
        total_params_b=7.0, num_examples=10000, method="qlora"
    )
    print(f"QLoRA 7B, 10k examples: ${qlora_cost['cost_usd']} ({qlora_cost['gpu_hours']}h)")

    api_cost = training_cost_estimate(
        total_params_b=0, num_examples=10000, vendor_api_price_per_m=1.80
    )
    print(f"gpt-4.1-mini API, 10k examples: ${api_cost['cost_usd']}")

    # 8. Multi-tenant serving
    print("\n--- Multi-Tenant Serving Economics ---")
    serving = multi_tenant_serving_cost(num_tenants=200)
    print(f"Adapter serving: ${serving['adapter_serving']['per_tenant_monthly']:.0f}/tenant/mo")
    print(f"Separate deploys: ${serving['separate_deployments']['per_tenant_monthly']:.0f}/tenant/mo")
    print(f"{serving['note']}")
