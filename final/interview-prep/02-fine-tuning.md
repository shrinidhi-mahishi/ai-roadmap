# Module 02: Fine-Tuning LLMs (SFT / PEFT / Preference / RLVR)

Consolidated from GPT, Opus, and Grok research. All prices, ranks, learning rates, GPU-hours, and eval deltas are sourced from vendor docs, papers, and named blogs as of 2026-09-02. Inferred per-query costs are marked **[inferred]**.

---

## What Is This?

Fine-tuning is like coaching an athlete who already knows the sport. A base LLM (GPT, Llama, Qwen) already understands language -- fine-tuning teaches it your specific style, format, or domain expertise. You show it hundreds of examples of "here is the input, here is the output I want," and it adjusts its internal weights to produce outputs that match your pattern.

Another way to think about it: a base LLM is a generalist chef who has read every cookbook on earth. Fine-tuning does not hand that chef a new recipe at dinner (that is prompting / RAG). It retrains a slice of the weights so the chef's default muscle memory **is** your house style: the JSON schema, the IRAC memo, the "never invent a citation" habit, the department's jargon.

Three analogies for different audiences:

| Audience | Analogy |
|---|---|
| Non-technical | Prompting = telling the model what to do each time. RAG = giving it a reference book. Fine-tuning = training it so the behavior becomes second nature. |
| Engineering | Fine-tuning shifts the model's default output distribution. A tuned model should behave correctly before the prompt gets elaborate. |
| Architecture | Fine-tuning is a **parametric** distribution shift, not a document store. It changes weights, not retrieval context. |

The 2022-2026 post-training stack is modular:

1. **(Optional) Continued pretraining / DAPT** -- next-token prediction on unlabeled domain text so the tokenizer/model speaks the domain.
2. **SFT / instruction tuning** -- supervised `(prompt, completion)` or chat-messages JSONL.
3. **Preference optimization** -- DPO / ORPO / KTO / SimPO on chosen/rejected (or binary) labels, or classical RLHF (reward model + PPO).
4. **RL with verifiable rewards (RLVR)** -- GRPO / GSPO / hosted RFT against a programmatic grader (unit test, boxed math, schema).

**PEFT (LoRA and friends)** is the production default: freeze the base W0, train a tiny adapter BA so you store megabytes per tenant instead of a full copy. **Full fine-tune** moves every weight -- highest capacity, worst forgetting, worst storage.

Think of a restaurant chain. **Training** is the test kitchen (write / control plane): ingest recipes, redact allergens, run a job, taste against a holdout, promote or dump the batch. **Serving** is the dining room (read / data plane): load the house sauce (adapter) onto the same stove (base weights), route table to sauce, plate, log. If you couple those jobs, a hung GPU blocks dinner service, and a bad promote ships unreviewed food.

---

## Why It Matters

Fine-tuning matters when prompting, retrieval, and workflow design have already done most of the work, but you still need a **stable behavior shift**. Good examples are format discipline, tone consistency, tool-call style, and narrow-domain skill improvement where the model repeatedly makes the same kind of mistake.

In enterprise AI, fine-tuning reduces inference costs (shorter prompts by removing few-shot examples), enforces consistent output formats, and enables domain-specific tone that prompting cannot reliably achieve.

**The critical skill for a Director/VP AI role is knowing when NOT to fine-tune**: 73% of underperforming enterprise fine-tuning projects trace root cause to data quality issues, not model or hyperparameter choices.

**The clean decision boundary (an interview ladder):**

1. **Prompt better** -- use prompting when the behavior change is light and easy to specify.
2. **Add retrieval or tool use** -- use RAG when the problem is private or fast-changing knowledge.
3. **Add deterministic validation or workflow structure** -- use workflow and validators when the problem is orchestration or business rules.
4. **Fine-tune only after the above start showing diminishing returns** -- use fine-tuning when you want the model itself to internalize a repeatable behavior.

This framing avoids the common anti-pattern of using fine-tuning to memorize data that should live in retrieval.

**Vendor landscape context**: OpenAI is winding down self-serve FT (no new jobs for remaining customers on **2027-01-06**; `ft-o4-mini-2025-04-16` inference dies **2026-10-23**). Anthropic has **no** first-party FT API. Newer bases + prompt cache + RAG absorb most "we should fine-tune" tickets.

Within fine-tuning itself, think of three families:

| Family | What It Teaches | Data Shape |
|---|---|---|
| **SFT** | "Produce this kind of answer" | Clean prompt-response pairs |
| **DPO / Preference** | "Prefer this answer over that one" | Chosen/rejected pairs (or binary) |
| **RFT / GRPO** | "Optimize against a grader or reward signal" | Tasks + reliable grader |

The further you go from SFT toward RL, the higher the data and evaluation burden.

**What fine-tuning does NOT do:**
- Does not solve prompt injection (injection is an application security problem).
- Does not provide tenant-specific ACLs.
- Does not act as a freshness layer.
- Does not give you citation, ACL filters, or instant unpublish.

---

## Architecture / System Design

### High-Level Pipeline Flow

```text
problem definition -> baseline with prompt/RAG/workflow
-> choose objective (SFT, DPO, or RFT)
-> curate and version data
-> train adapters or full weights
-> evaluate against holdouts and task slices
-> package model/adapters
-> deploy with rollback path
```

A production fine-tuning system is **two independently scaled planes sharing artifacts** (base checkpoint + adapter or merged weights + dataset version + eval report), not a single "train then chat" function.

### Full Architecture Diagram

```
                         TELEMETRY / OBSERVABILITY SINKS
         +--------------------------------------------------------------+
         |  W&B / MLflow: loss, LR, eval  |  job meters (tok*epochs, GPU-h) |
         |  holdout + forgetting + safety |  serve: TTFT, OTPS, adapter hit |
         |  WORM audit: who/hash/promote  |  lineage: dataset->job->adapter |
         +--------^--------------------^-----------------^--------------+
                  | spans              | metrics          | audit events
                  |                    |                  |
+-----------------+--------------------+------------------+-----------------+
| CONTROL PLANE  (write -- jobs, gates, pointers; not token decode)        |
|                                                                          |
|  +----------+ +-------------+ +--------------+ +----------+ +--------+  |
|  | IdP/IAM  | | Dataset     | | Job API      | | Eval gate| |Registry|  |
|  | actor +  | | versioning  | | SFT/DPO/RFT  | | block    | |promote |  |
|  | tenant   | | hash/PII    | | rank/a/LR/ep | | promote  | |rollback|  |
|  +----+-----+ +------+------+ +------+-------+ +-----+----+ +---+----+  |
|       |              |               |               |           |       |
+-------+--------------+---------------+---------------+-----------+-------+
        |              |               |               |           |
        v              v               v               v           v
+----------------------------------------------------------------------+
| DATA PLANE  (train write vs serve read -- independently scaled)      |
|                                                                      |
|  TRAIN (write): JSONL/parquet -> PII DLP -> split -> distributed     |
|                 runtime (FSDP2 / ZeRO / QLoRA) -> checkpoints ->     |
|                 eval artifact                                        |
|                                                                      |
|  SERVE (read):  load base W + LoRA_t (or merged) -> route            |
|                 tenant->adapter -> batch/quantize -> log             |
|                 {tenant, adapter_sha, base_rev}                      |
|                                                                      |
|  +------------ TOOL PROXIES (MCP / vendor APIs -- least priv) ----+  |
|  | submit_sft_job | submit_dpo_job | submit_rft_job (grader)      |  |
|  | load_lora      | generate_ft    | generate_base | retrieve_kb  |  |
|  | Identity from verified token / RunContext -- NEVER from model   |  |
|  +----------------------------------------------------------------+  |
+--------+----------------+------------------+-----------------+--------+
         |                |                  |                 |
         v                v                  v                 v
+----------------------------------------------------------------------+
| PERSISTENCE LAYER  (artifacts the two planes share)                  |
|                                                                      |
|  +----------+ +----------+ +----------+ +----------+ +---------+    |
|  | Dataset  | |Checkpoints| | Adapters | | Merged   | | Eval    |    |
|  | hash +   | | every N  | | LoRA     | | GGUF/AWQ | | reports |    |
|  | PII rpt  | | steps/   | | (tens of | | (rollback| | task +  |    |
|  | train/   | | epoch    | | MB) +    | | = full   | | forget  |    |
|  | val/test | | obj store| | config   | | ckpt)    | | safety  |    |
|  +----------+ +----------+ +----------+ +----------+ +---------+    |
|  Registry pointer: adapter_id | merged_id | vendor model id          |
|  Lineage: base@rev + tokenizer/chat_template + seed + code SHA      |
+----------------------------------------------------------------------+
```

### Detailed Layer Diagram (Control + Compute + Serving + Persistence)

```
+--------------------------------------------------------------------------+
|                         CONTROL PLANE                                     |
|  +-------------+  +-----------------+  +------------+  +------------+    |
|  |  Experiment  |  |  Model Registry |  |  Eval      |  |  Canary    |    |
|  |  Tracker     |  |  (MLflow/W&B)   |  |  Pipeline  |  |  Deploy    |    |
|  |  (W&B/       |  |  Versions +     |  |  4-Layer   |  |  Controller|    |
|  |   MLflow)    |  |  Signatures     |  |            |  |            |    |
|  +------+------+  +--------+--------+  +-----+------+  +-----+------+    |
|         |                  |                  |               |            |
+---------+------------------+------------------+---------------+-----------+
|         |           DATA PLANE                |               |           |
|         v                  |                  |               |           |
|  +------------------------------------------------------------------+    |
|  |                    TRAINING PIPELINE                               |    |
|  |  +----------+  +-----------+  +----------+  +---------------+    |    |
|  |  | Data Prep |  | SFT       |  | Pref.    |  | RL (Optional) |    |    |
|  |  | Dedup,    |--| (Format,  |--| Optimize |--| GRPO/DAPO     |    |    |
|  |  | Format,   |  |  Style)   |  | DPO/SimPO|  | (Verifiable   |    |    |
|  |  | Decontam. |  |           |  |          |  |  Rewards)     |    |    |
|  |  +----------+  +-----------+  +----------+  +---------------+    |    |
|  +------------------------------------------------------------------+    |
|                                                                          |
+--------------------------------------------------------------------------+
|                      COMPUTE LAYER                                       |
|  +--------------+  +---------------+  +--------------------------+       |
|  | GPU Cluster   |  | Checkpointing |  | Distributed Training     |       |
|  | (H100/A100)   |  | (Spot Resume) |  | (FSDP / DeepSpeed ZeRO) |       |
|  +--------------+  +---------------+  +--------------------------+       |
|                                                                          |
+--------------------------------------------------------------------------+
|                      SERVING LAYER                                       |
|  +--------------+  +---------------+  +--------------------------+       |
|  | Inference     |  | Adapter Store |  | Traffic Management       |       |
|  | Engine        |  | (Hot-swap     |  | (A/B, Canary, Blue-Green)|       |
|  | (vLLM/TGI/   |  |  LoRA deltas) |  |                          |       |
|  |  SGLang)      |  |               |  |                          |       |
|  +--------------+  +---------------+  +--------------------------+       |
|                                                                          |
+--------------------------------------------------------------------------+
|                      PERSISTENCE LAYER                                   |
|  +--------------+  +---------------+  +--------------+  +-------------+  |
|  | Training Data |  | Model         |  | Eval         |  | Audit Log   |  |
|  | (Encrypted    |  | Artifacts     |  | Results      |  | (Immutable  |  |
|  |  S3/GCS)      |  | (Signed)      |  | (Benchmark   |  |  + WORM)    |  |
|  |               |  |               |  |  + Prod)     |  |             |  |
|  +--------------+  +---------------+  +--------------+  +-------------+  |
+--------------------------------------------------------------------------+
```

### Planes (Do Not Couple)

| Plane | Owns | Typical Components | Failure If Coupled |
|---|---|---|---|
| **Training (write / control)** | Dataset ingest, PII redaction, split, job config (method, rank, LR, epochs), distributed runtime, checkpoints, eval gates, promote/rollback | JSONL/parquet pipelines, Axolotl / LlamaFactory / Unsloth / TRL jobs, FSDP2 / DeepSpeed, W&B / MLflow, model registry | A hung GPU job blocks serving; a bad promote ships an unreviewed checkpoint |
| **Serving (read / data)** | Load base + adapter(s) or merged weights, route tenant to adapter, batch, quantize, log | vLLM `--enable-lora`, Fireworks multi-LoRA (up to **100** addons), SageMaker / Bedrock custom endpoints, merged GGUF / AWQ | Training dtype/quant silently mismatches serve dtype; adapter cache miss dominates TTFT |

### Vendor Control Planes

- **OpenAI:** upload JSONL -> `/v1/fine_tuning/jobs` (SFT, vision SFT, DPO, or RFT) -> evaluate -> call the resulting model id. New orgs cannot create jobs since **2026-05-07**; orgs with no fine-tuned inference in 60 days lost job creation **2026-07-02**; **all remaining customers lose new-job creation on 2027-01-06**. Inference on already-tuned models continues until the **base snapshot is deprecated**. `ft-o4-mini-2025-04-16` (the only public RFT snapshot) shuts down **2026-10-23**.
- **Vertex / Gemini:** regional `tuningJobs.create` (`us-central1`, `europe-west4`). User data stored in the tuning-job region; compute may offload to other US/EU accelerator regions. **Supervised fine-tuning is NOT a Covered Service and is excluded from any SLA.** CMEK not supported for listed Flash models.
- **Bedrock:** `CreateModelCustomizationJob` with `customizationType` in {`FINE_TUNING`, `CONTINUED_PRE_TRAINING`, `DISTILLATION`, `REINFORCEMENT_FINE_TUNING`, `IMPORTED`}. Train JSONL on S3; optional `customModelKmsKeyId`, VPC `subnetIds` / `securityGroupIds`.

### Open-Weight Control-Plane Software

| Stack | Role | Key Facts |
|---|---|---|
| **HF PEFT** | Adapter injection | LoRA / DoRA / rsLoRA / LoftQ; `merge_and_unload()` is **not in-place** -- must assign return value |
| **HF TRL** | Post-training trainers | `SFTTrainer`, `DPOTrainer`, `GRPOTrainer` (stable); `ORPOTrainer` / `PPOTrainer` experimental. TRL v1 announced **2026-03-27** |
| **Unsloth** | Kernel-optimized LoRA/QLoRA/FFT/RL | Up to **2x** faster, **70-80%** less VRAM; currently requires `lora_dropout=0`, `bias="none"` for fused kernels |
| **Axolotl** | YAML over Transformers/PEFT/TRL/Accelerate | **FSDP2** recommended, FSDP1 deprecated; FSDP+QLoRA: **70B on two 24 GB GPUs**; DeviceMesh for dp_shard, TP, CP, EP |
| **LlamaFactory** | CLI/UI, 100+ models | Stages: `pt` / `sft` / `rm` / `ppo` / `dpo` / `kto` / `orpo` / `simpo`; default `lora_rank: 8`, `lora_target: all`; **do not merge a quantized base** |

MarkTechPost 2026-07-22: Unsloth wins single-GPU; Axolotl wins multi-GPU N-D; LlamaFactory wins UI/breadth.

### Request-Flow Narrative

**Training (offline):**
1. **Ingest / control.** Actor (IAM/Azure AD) uploads JSONL. Pipeline hashes the file, runs PII detection -> redaction -> audit, then splits train/val/test **before upload** (InstructGPT used user-ID disjoint splits). Record `dataset_hash + tokenizer/chat_template + base_model_id@revision + peft_config + seed + code SHA`.
2. **Job submit.** Idempotency key = that lineage tuple. Config: method (SFT / DPO / ORPO / GRPO / RFT), rank r, alpha, LR, epochs. OpenAI/Azure: no charge for queue, failed jobs, cancel-before-train, or safety checks. Together: failed jobs fully refunded; cancel bills completed steps.
3. **Train.** LoRA (rank 64-128) or QLoRA (4-bit NF4 base) reduces memory from 100-120 GB to 6-10 GB for a 7B model. Checkpoints saved every N steps for spot-instance resilience. FSDP2 or ZeRO-3 for full FT 70B+.
4. **Eval gate -- block promote until all four pass.** (1) Task holdout the job never saw. (2) **Forgetting slice** (Biderman: mean of HellaSwag, WinoGrande, ARC-Challenge -- or a frozen production golden set). (3) Safety / refusal / jailbreak, especially after DPO/RL. (4) **Serving-parity**: same dtype/quant as prod (Unsloth). Plus contamination audit (DICE; GSM1k vs GSM8K).
5. **Registry promote.** Pointer flip: `adapter_id` (unmerged) or `merged_id` / vendor model id. Canary 1-5% to the new adapter on the **same vLLM replica** (`max_loras >= 2`) -- not a second GPU cluster. Rollback = previous pointer (adapters are tens of MB; Hu et al. 35 MB for GPT-3 r=4 on Wq,Wv).

**Serving (online):**
1. Inference engine (vLLM, TGI, SGLang) loads base model once.
2. LoRA adapter hot-swapped per request or per tenant from adapter store.
3. Traffic management routes 5-10% to new adapter (canary), monitors quality metrics, auto-rollback if degradation detected.
4. Fallback chain: **fine-tuned model -> base model with prompt engineering -> cached/deterministic responses**.

### State Machine: Fine-Tuning Pipeline

```
+----------+     +----------+     +----------+     +----------+
|  DATA     |---->|  SFT     |---->|  PREF    |---->|  EVAL    |
|  PREP     |     |  TRAIN   |     |  OPT     |     |  GATE    |
+----------+     +----------+     +----------+     +-----+----+
                       |                                  |
                       | (checkpoint                      |
                       |  on spot                    pass | fail
                       |  interruption)                   |
                       v                                  v
                 +----------+                       +----------+
                 |  RESUME  |                       |  REJECT  |
                 |  FROM    |                       |  (Root    |
                 |  CKPT    |                       |   Cause)  |
                 +----------+                       +----------+
                                                          |
                                                          v
                                                    +----------+
                                                    |  RETRAIN |
                                                    |  (Fix    |
                                                    |   Data)  |
                                                    +----------+
```

**Key invariant**: Task loss improving while general capability (MMLU, HellaSwag) craters is the signature of catastrophic forgetting. Always track both.

---

## Core Concepts & Algorithms

### Invariants

**Invariant I1 -- FT is a distribution shift, not a document store.** Fine-tuning changes the parametric policy. It does not give you citation, ACL filters, or instant unpublish. Private / large / time-varying knowledge stays in RAG. LIMA/InstructGPT-style alignment teaches format.

**Invariant I2 -- Pin the full lineage.** Pin `base_model_id@revision + tokenizer/chat_template + peft_config + adapter_sha + serve_dtype`. Changing any is a new eval, often a new job. Train-in-QLoRA-4-bit then merge into 4-bit without dequant-merge-requant **collapses quality** (transformers#31293). Unsloth: train and serve in the same precision.

**Invariant I3 -- Promote is a pointer, gated on holdout + forgetting + safety + serve-dtype.** Loss going down is not a ship signal. LIMA picked checkpoints on a 50-example dev set because perplexity did not track generation quality; InstructGPT SFT overfits val NLL after 1 epoch but more epochs still improved RM score.

### When to Fine-Tune vs RAG vs Prompt

| Need | First Lever | Why |
|---|---|---|
| Behavior specifiable in text; examples fit window; base follows instructions | **Prompt / few-shot / cache** | OpenAI's wind-down rationale: newer bases reduce the need for self-serve FT. Anthropic's path is prompt + cache |
| Knowledge private, large, or time-varying | **RAG** | FT is a bad document store; no citation / ACL / unpublish. Hybrid: FT for schema/persona, RAG for facts |
| Stable distribution shift: schema, tool-call JSON, persona, department language | **SFT** | Classification to a single class token, PII-stripped summary format, extractive span QA, persona |
| Tokenizer/model does not speak the domain (jargon, code dialect, legal French) | **CPT then SFT** | NVIDIA Llama 3.1 70B: DAPT on 17M papers then 250,000 synthetic instructions. Biderman: do not expect LoRA CPT to match full-FT CPT |
| Pairwise or binary preferences; no PPO stack | **DPO / ORPO / SimPO / KTO** | DPO if clean pairs + ref in memory; SimPO if reference-free; ORPO if one SFT+preference stage; KTO if thumbs/logs |
| Machine checker exists (unit tests, MATH boxed answers, schema validators) | **GRPO / RFT / GSPO** | DeepSeekMath G=64; OpenAI RFT $100/h; Bedrock RFT = prompts + Lambda reward |

**The best answer is often both: RAG for facts, tuning for behavior.**

### The Post-Training Stack

**Stage 1: SFT (Supervised Fine-Tuning)** -- teaches format.
- Input: (prompt, completion) pairs.
- Trains on next-token prediction loss over the completion tokens only.
- 5K well-curated examples usually outperform 50K noisy ones.
- Teaches the model what good outputs look like, but not how to judge between alternatives.

**Stage 2: Preference Optimization** -- teaches judgment.

| Method | Mechanism | Key Advantage | Key Weakness |
|---|---|---|---|
| **DPO** | Direct optimization on preference pairs, implicit reward r(x,y) = beta * log(pi_theta / pi_ref) | Simple, no reward model, no sampling loop, no value function | Very sensitive to data quality; needs frozen reference model (~2x logits) |
| **SimPO** | Length-normalized DPO, no reference model. Implicit reward = length-normalized avg log-prob + margin gamma | +6.4 pts AlpacaEval 2 vs DPO; reference-free | Less studied at scale; LR 1e-5 can produce incoherent text; need grid 3e-7 to 1e-6 |
| **KTO** | Works with thumbs-up/down (unary) signals; binary desirable/undesirable | No pairwise comparisons needed; can skip SFT if base strong; dropping 90% desirable still beat DPO | Less precise; risk-neutral v(.) collapses BBH to 6.1 |
| **ORPO** | Merges SFT + odds-ratio penalty in one objective; no reference model, no SFT warm-up | Single GPU friendly; one stage | Higher catastrophic forgetting risk (no KL anchor); monitor general eval tightly |

**Stage 3: RL with Verifiable Rewards** -- teaches reasoning.
- **GRPO (Group Relative Policy Optimization):** Deletes PPO's critic. Samples a group {o_i} of G completions per prompt; advantage A_i = (R_i - mean(R)) / std(R). Enables millions of verification signals per day vs hundreds of human labels per day.
- **GSPO:** GRPO's token-level importance ratio is high-variance on long rollouts and unstable on MoE (expert routing changes). GSPO clips a sequence-level geometric-mean ratio. Reports clipping two orders of magnitude more tokens than GRPO yet is more sample-efficient. MoE-stable; drops Routing Replay requirement.
- **RLVR** removes the human bottleneck by replacing human feedback with deterministic verification (unit tests, math checks, format validation).

### Decision Framework

| Scenario | Recommended Method |
|---|---|
| Most teams (default) | SFT then DPO |
| Reasoning with verifiable rewards | SFT then GRPO |
| Subjective alignment, DPO underperforms | Full RLHF (PPO) |
| Unary preference signals only | KTO |
| Single GPU, small model (<=7B) | ORPO |
| MoE or long rollouts | GSPO |

### Method Card -- What You Must Host in VRAM

| Method | Ref Policy | Reward Model | On-Policy Samples | Pair Labels | Typical Extra vs SFT |
|---|---|---|---|---|---|
| SFT | no | no | no | no | 1x model |
| ORPO | no | no | no | yes (SFT+penalty) | 1x; lambda default 0.1 |
| SimPO | no | no | no | yes | 1x; beta ~2-10 |
| DPO | yes (frozen) | no (implicit) | no | yes | ~2x logits |
| KTO | yes (KL ref point) | no | no | binary ok | ~2x; unpaired OK |
| PPO-RLHF | yes (KL) | yes + value | yes | rankings -> RM | 4x class (policy, ref, RM, value); InstructGPT used 6B RM for all sizes |
| GRPO | yes | optional / verifier | G completions/prompt | verifier or RM | no critic; G=64 in DeepSeekMath |
| GSPO | yes | verifier/RM | G sequences | verifier | sequence IS; MoE-stable |
| OpenAI RFT | hosted | grader model | yes | grader | $100/h core loop |

### LoRA Mechanics

LoRA injects trainable low-rank matrices into frozen model layers:

```
W' = W + (alpha/r) * BA

Where:
  W  = original frozen weight matrix (d x k)
  B  = trainable matrix (d x r)
  A  = trainable matrix (r x k)
  r  = rank (r << min(d,k), typically 64-128)

Trainable parameters per linear layer: r * (d + k)
For a square d x d layer: approximately 2dr
Reduction: ~99% fewer trainable parameters
```

**Initialization**: Kaiming-uniform for A, zeros for B. At initialization BA = 0, so the model starts from the exact original behavior.

**rsLoRA (Rank-Stabilized LoRA)**: uses `alpha/sqrt(r)` scaling instead of `alpha/r` (`use_rslora=True`), which is empirically better and avoids intruder dimensions.

**Key configuration parameters:**

| Parameter | Typical Value | Effect |
|---|---|---|
| `r` (rank) | 64-128 | Adaptation capacity. Higher = more capacity, more memory |
| `target_modules` | q,k,v,o_proj + MLP (all linear) | Which layers get adapters. More = better quality, more memory |
| `lora_alpha` | r (PEFT default) or 2r (Biderman recipe) | Scaling factor for adapter contribution |
| `lora_dropout` | 0.05-0.1 (0 for Unsloth) | Prevents overfitting |

**Rank / alpha -- the interview knob:**

| Claim | Reality |
|---|---|
| LoRA = full FT "always" | **False** on Biderman code/math Llama-2-7B: low-rank LoRA underperforms full FT; CPT gap not closed even at high rank; IFT high ranks can match |
| Folklore r=8 | LlamaFactory default; often underfits code. Databricks: **rank 32** necessary for most customers. Biderman IFT: r=256 all modules; 16-64 often fail on code |
| alpha = r (PEFT default 8/8) | "Illusion of Equivalence": alpha=8 produces intruder dimensions and worse forgetting than alpha=2r |
| **Biderman recipe** | LoRA for **IFT not CPT**; all transformer modules at r=256 if memory allows; alpha = 2r; sweep LR [1e-5, 5e-4] |

**Complexity.** Forward extra cost is two thin matmuls O(r*(d+k)) vs O(dk). Memory: optimizer states only on A,B (plus 4-bit base for QLoRA). Serving unmerged: vLLM pre-allocates `max_loras x max_lora_rank x hidden` at start -- not per-adapter actual rank. Rank > `max_lora_rank` is a **hard reject** (restart required). Biderman r=256 **will not load** on vLLM's default `max_lora_rank=16`.

**GPT-3 175B original LoRA numbers:**
- Trainable params: **10,000x** fewer
- VRAM: **1.2 TB -> 350 GB**
- r=4 on Wq,Wv: checkpoint **350 GB -> 35 MB**
- Storing 100 adapted models: ~354 GB vs ~35 TB of full copies
- Training: 96 V100s full FT vs 24 V100s LoRA; ~25% training speedup
- **Merged LoRA: zero extra inference latency**

### QLoRA: 4-bit Fine-Tuning

QLoRA extends LoRA to 4-bit quantized base models with three innovations:

- **NF4 (NormalFloat4)**: Non-uniform 4-bit quantization exploiting the fact that neural network weights follow a zero-centered normal distribution. Allocates more representational power to distribution tails.
- **Double quantization**: Further compresses quantization constants (~0.5 -> ~0.2 bits/param metadata, saving about 0.373 bits/parameter, roughly 3 GB on a 65B model).
- **Paged optimizers**: Offload optimizer states to CPU during memory spikes.

**Result: fine-tune a 65B model on a single 48GB GPU** with typically <1% quality degradation vs full FP16.

**Key numbers:**
- Full 16-bit fine-tuning at 65B scale: >780 GB
- QLoRA: <48 GB (41 GB 4-bit footprint)
- Guanaco-65B: 24 hours on one professional GPU
- MMLU NF4+DQ: 53.1 vs bf16 53.0 vs FP4 52.2
- HPs: 7B/13B LR 2e-4, 33B/65B LR 1e-4; 1875 steps, batch 16, target length 512
- Guanaco Vicuna GPT-4-as-judge: 99.3% of ChatGPT (but Kendall tau=0.43, Spearman r=0.55 vs humans -- chatbot benches are not trustworthy)

**Important**: QLoRA 5-shot MMLU on LLaMA shows that chat datasets (OASST1/Alpaca) can **drop** 65B MMLU vs base (62.2/62.5 vs 63.4) -- eval-set mismatch, not "QLoRA destroys MMLU."

### DoRA (Direction + Magnitude)

W = m * (V / ||V||_c); LoRA updates direction; magnitude m is a learned vector (d extra params/layer). No extra inference cost after merge.

Commonsense vs LoRA gains: LLaMA-7B **+3.7**, LLaMA-13B **+1.0**, LLaMA2-7B **+2.9**, LLaMA3-8B **+4.4**.

### PiSSA / LoftQ Init Variants

- **PiSSA**: Init A,B from top singular components of W (not a no-op at step 0).
- **LoftQ**: Alternate quantization and low-rank approximation so Q+BA approx W before QLoRA training. PEFT can roll back a layer if error did not drop. Cases exist that do not beat QLoRA -- treat as an init, not a guarantee.

### AdaLoRA and Composable LoRA

**AdaLoRA**: Dynamically adjusts rank allocation across modules based on importance scores. Gives more capacity to layers that need it, less to layers that don't. Reduces total parameter count while matching or exceeding fixed-rank LoRA quality.

**Composable LoRA (2025-2026 trend)**: Multiple LoRA deltas stack logically for feature reuse across tasks. MLOps platforms include parameter-delta registries, adapter stores, automated merge-and-sign pipelines, and delta-aware CI.

### Other PEFT Families

- **IA3**: Scales intermediate activations; can be lighter than LoRA.
- **Prefix tuning and prompt tuning**: Add trainable prompt-like vectors instead of weight deltas. Useful when memory is extremely constrained or when you want minimal serving changes.

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

### Merge Rules

- `merge_and_unload()` must be **assigned** (not in-place). `safe_merge=True` checks NaNs.
- **Do not merge a quantized base.** Path: merge into bf16/fp16, then re-quantize AWQ/GGUF; eval the exported artifact.
- PEFT on MoE experts: unmerged LoRA materializes every expert's adapter even when few fire under KV-cache decode -- **merge for MoE serving**.

### SFT Deep Dive: InstructGPT and LIMA

**InstructGPT (Ouyang et al., 2022):** SFT on ~13k demonstration prompts -> RM on ~33k ranked comparison prompts -> PPO on ~31k unlabeled API prompts. ~40 labeler contractors. Prompts from Playground traffic, PII-filtered, user-ID disjoint, max 200 prompts/user. SFT: 16 epochs, residual dropout 0.2, cosine LR to 10% of peak, no warmup. Peak LR/batch: 1.3B and 6B 9.65e-6/32; 175B 5.03e-6/8. The **1.3B InstructGPT (PPO-ptx) was preferred to 175B GPT-3**; 175B InstructGPT preferred to 175B GPT-3 **85 +/- 3%** and to few-shot 175B GPT-3 **71 +/- 4%**.

**LIMA (Zhou et al., NeurIPS 2023):** LLaMA-65B SFT on **exactly 1,000** curated pairs totaling ~750,000 tokens. Mix: SE STEM 200, SE other 200, wikiHow 200, r/WritingPrompts 150, Natural Instructions 50, author-written 200. AdamW beta1=0.9, beta2=0.95, WD 0.1, LR 1e-5 -> 1e-6 linear, 15 epochs, batch 32, trim 2048. Vs GPT-4: win 18% / tie 25% / lose 57% (equivalent-or-better 43%). 88% met requirements; 50% excellent. 30 hand-written dialogue chains raised multi-turn "excellent" 45.2% -> 76.1% and cut failures 15/42 -> 1/46 turns.

**Superficial Alignment Hypothesis**: Almost all knowledge is in pretraining; limited instruction data teaches **format**. Scaling quantity without diversity has diminishing returns.

**NEFTune (Jain et al., 2023):** Uniform noise on token embeddings scaled by alpha / sqrt(L * d). LLaMA-2-7B + Alpaca: AlpacaEval **29.79% -> 64.69%**. TRL: `neftune_noise_alpha`.

**Quality > volume:** LIMA 1,000 curated > folklore that you need 50k+ noisy pairs. QLoRA Guanaco on 9,209 OASST1 top-replies achieved Vicuna GPT-4-as-judge 99.3% of ChatGPT. But chatbot benches are not trustworthy (Kendall tau=0.43).

**OpenAI epoch heuristic:** under-follows -> +1-2 epochs; loses diversity -> -1-2 epochs; fails to converge -> raise LR multiplier. Unsloth: many tasks look healthy around loss 0.5-1.0; loss -> 0 is overfitting.

### PPO-RLHF Deep Dive (InstructGPT Steps 2-3)

SFT policy -> RM on pairwise rankings -> PPO with per-token KL against the SFT model. Labelers ranked K=4-9 completions; all C(K,2) pairs from one prompt are one batch element. RM: one 6B model for PPO of all policy sizes (1.3B/6B/175B).

**Bradley-Terry loss:**

```
L(theta) = -E[log sigma(r_theta(x, y_w) - r_theta(x, y_l))]
```

Then bias-shift so demonstrations have mean score 0. Held-out labeler-group accuracy 69.6 +/- 0.9% vs in-group 72.4 +/- 0.4%.

PPO: 256k episodes, ~31k unique prompts, batch 512 / minibatch 64, KL beta=0.02, clip 0.2. PPO-ptx mixes 8x pretraining examples and scales those grads by gamma=27.8 (gamma >= 20 recovered SQuADv2/DROP on 1.3B). Raising beta to 2.0 with gamma=0 did not fix those regressions and crushed validation reward.

**Gao et al.:** as KL from SFT grows, proxy RM keeps rising while **gold reward peaks then falls** (reward hacking).

### DPO Details

Implicit reward r(x,y) = beta * log(pi_theta / pi_ref). Pairwise loss; no RM, no sampling loop, no value function. LlamaFactory default `pref_beta` 0.1. Together DPO LoRA is a ~12-10% surcharge vs SFT; Fireworks DPO LoRA is exactly 2x SFT LoRA. OpenAI DPO beta range on API: 0 to 2.

### ORPO Details

Monolithic SFT + odds-ratio penalty; no reference model, no SFT warm-up. L_ORPO = E[L_SFT + lambda * L_OR]. Published lambda: Phi-2 0.25, Llama-2-7B 0.2, Mistral-ORPO-alpha 0.1. TRL names that weight `beta` (docs: paper lambda), default 0.1. Mistral-ORPO-beta 7B: AlpacaEval 2.0 12.20%, IFEval instr. loose 66.19%, MT-Bench 7.32. Mistral-ORPO-alpha exceeds Zephyr-alpha (SFT 200k + DPO UltraFeedback) on AlpacaEval 2.0 (11.33 vs 8.35). Monitor general eval tightly -- no KL anchor by design.

### KTO Details

Binary desirable/undesirable per (x,y), not pairs. Zephyr-beta-SFT + UltraFeedback, 1 epoch: GSM8K 40.0 (DPO) -> 53.5 (KTO, beta=0.1); BBH 44.1 -> 52.6. Dropping 90% of desirable data still beat DPO on Llama-7B. Risk-neutral v(.)=.: BBH collapses to 6.1. If pretrained model is strong enough, KTO can skip SFT; DPO still wants SFT.

### SimPO Details

Reference-free; implicit reward = length-normalized average log-prob + margin gamma. Beta typically 2-10 (vs DPO 0.1). Llama3-Instruct SimPO 44.7 LC / 33.8 Arena-Hard vs DPO 40.3 / 32.6 vs SFT 26.0 / 22.3. Without length-norm, Mistral-Base LC 21.5 -> 11.9 (worse than DPO's 15.1). LR 1e-5 can produce incoherent/repetitive text; grid 3e-7 to 1e-6.

### GRPO Details

Deletes PPO's critic. Sample group {o_i} of G completions; advantage A_i = (R_i - mean(R)) / std(R). DeepSeekMath-RL 7B: policy LR 1e-6, KL 0.04, G=64, max length 1024, batch 1024. vs Instruct: GSM8K 82.9% -> 88.2%, MATH 46.8% -> 51.7%. R1-Zero skips SFT and uses outcome verification only.

### GSPO Details

GRPO's token-level importance ratio is high-variance on long rollouts and unstable on MoE (expert routing changes). GSPO clips a sequence-level geometric-mean ratio. Reports clipping two orders of magnitude more tokens than GRPO yet is more sample-efficient on AIME'24 / LiveCodeBench / CodeForces from Qwen3-30B-A3B-Base. GRPO on that MoE needed Routing Replay; GSPO drops it.

### Catastrophic Forgetting (Central Technical Risk)

Teaching a model something new erodes what it already knew. A clinical NLP team fine-tunes on radiology reports; model collapses on cardiology notes. LoRA is not a silver bullet: 2025-2026 research proves that in many continual learning scenarios, LoRA fails to prevent significant knowledge loss.

**Biderman findings ("Illusion of Equivalence"):**
- IFT forgets **more** than CPT
- Programming forgets **more** than math
- Forgetting **increases with data volume**
- LoRA forgets **less** than full FT (even at equal task accuracy) and less than dropout/weight-decay
- alpha=2r helps

**Mitigations:**
- Mix replay/general data (InstructGPT PPO-ptx gamma=27.8)
- LoRA instead of full FT for IFT
- alpha=2r
- Orthogonal/projected LoRA (OPLoRA / O-LoRA) for continual FT
- CPT mix-back at conservative LR (Nova Forge)
- Detection: frozen general eval; task loss down + that slice crash = signature

**A model 10% better on your task but 25% worse on everything else is rarely the right trade.**

### Embedding Fine-Tuning

OpenAI does not offer fine-tuning of `text-embedding-3-*` (Matryoshka `dimensions` truncates at inference). Production path: open-weight + sentence-transformers `MultipleNegativesRankingLoss` (batch 64 -> 63 in-batch negatives) wrapped in `MatryoshkaLoss`. After FT, pin model id + dim + metric in the vector index -- changing any is a full re-embed.

---

## Token Economics & Cost Analysis

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

### Cost Formula

```
Training cost = GPU_hourly_rate * num_GPUs * training_hours
Spot discount = 60-70% off on-demand
Monthly inference = $1,000-$2,000 (self-hosted, single A100)
                  or $0.20-$2.00/MTok (managed endpoint)

Total annual cost = (training_cost * iterations/year)
                  + (inference_cost * 12)
                  + data_prep_labor

Billable training tokens (Google, Together, Fireworks, Bedrock, Azure):
  billable = (dataset_tokens * epochs) + (val_tokens * eval_passes)
```

**Spot instances are the single biggest lever**: A $1,638 full fine-tune becomes ~$490 on spot.

### Serving Cost per 1k Runs [inferred]

Assumptions: 800 input + 400 output tokens; 1k requests; no retries; no hosting amortization unless stated.

| Path | Meter (per 1M tok) | Arithmetic | [inferred] $/1k runs |
|---|---|---|---|
| OpenAI o4-mini FT | in $4.00 / cached $1.00 / out $16.00 | 0.8*4 + 0.4*16 | **$9.60** uncached |
| Same + data sharing | $2 / $0.50 / $8 | 0.8*2 + 0.4*8 | **$4.80** |
| Gemini 2.0 Flash (tuned = base rates) | in $0.15 / out $0.60 | 0.8*0.15 + 0.4*0.60 | **$0.36** |
| Gemini 3+ tuned | 1.5x base prediction | 1.5x base SKU | **1.5x the base 1k figure** |
| Fireworks dedicated LoRA | "same price as base models" | token meter | dedicated minutes + base token meter |
| Bedrock custom Nova | on-demand = same $/token as base Nova | token-only; storage $1.95/model/month | token-only |
| Bedrock Llama 2 70B custom | PTU no-commit $23.50/model-unit/hour | 23.50*24*30 ~ $16,920/mo idle | **~$16.92/1k** hosting-only at 1M req/month [inferred] |

**Hosting dominates low QPS:**
- Azure Standard FT deployment: **$1.70/hour**. Idle: $1,224/mo; $14,892/yr [inferred].
- At 1M req/month, hosting alone is $1.224/1k runs.
- At 10k req/month, **$122.40/1k** -- often more expensive than a long cached prompt.
- Azure Developer tier: no hourly host, auto-delete 24h, no availability SLA.

### Training Cost Worked Examples

**Worked A -- 5,000 examples * 400 tokens * 3 epochs = 6M training tokens:**

| Meter | Train $ |
|---|---|
| Together LoRA SFT <=16B | **$4.00** (min) |
| Fireworks LoRA SFT <=16B | **$3.00** |
| Vertex Gemini 2.5 Flash-Lite SFT | **$9** |
| Vertex Gemini 2.5 Flash SFT+pref | **$30** |
| Vertex Gemini 3.5 Flash SFT+RL FT | **$60** |
| Vertex Gemini 2.5 Pro SFT | **$150** |
| Azure SFT (1M tok * 2 ep * $2/1M) | **$4** train + $1.70/h host |
| Bedrock Llama 2 70B | **$47.94** train |
| Bedrock Nova Micro (blog) | **$2.18** train + $1.75/mo storage |

**Worked B -- 10M tokens * 3 epochs = 30M billable:**
- Llama-8B-class LoRA SFT: $14.40
- Llama-70B LoRA: $87

**Worked C -- Time-based RL:**
- OpenAI / Azure RFT on o4-mini: **$100.00/hour** core loop wall-clock (not queue/safety checks), prorated to the second
- Azure: **$5,000 per-job cap** then uncapped resume
- Bedrock gpt-oss-20b RFT: **$80/h** then on-demand token inference

**Self-host durations:** QLoRA Guanaco 65B = 24h on 1x48 GB; second Guanaco <12h on consumer GPU. NVIDIA Llama 3.1 70B DAPT: 128xH100, 144h bf16 -> [inferred] 18,432 H100-hours.

### Together / Fireworks Training Bands (per 1M tokens)

| Size | Together SFT LoRA / DPO LoRA / SFT full / DPO full | Fireworks LoRA SFT / LoRA DPO / Full SFT / Full DPO |
|---|---|---|
| <=16B | $0.48 / $0.54 / $1.20 / $1.35 | $0.50 / $1.00 / $1.00 / $2.00 |
| mid (17-69B / 16.1-80B) | $1.50 / $1.65 / $3.75 / $4.12 | $3 / $6 / $6 / $12 |
| large (70-100B / 80-300B) | $2.90 / $3.20 / $7.25 / $8.00 | $6 / $12 / $12 / $24 |

Together: $4.00 minimum per job. Fireworks >300B: $10 / $20 / $20 / $40.

### Vertex Model-Specific Training Pricing (per 1M tokens)

- **Gemma 3 SFT**: 1B $0.47 / 4B $1.14 / 12B $1.82 / 27B $6.83
- **Llama**: 3.1 8B $0.67 / 3.3 70B $6.72 / 4 Scout 17B-16E $5.77
- **Qwen 3**: 4B $1.35 / 8B $4.18 / 14B $8.46 / 32B $6.57
- **Gemini**: 2.5 Flash-Lite $1.50 / 2.5 Flash $5.00 / 2.5 Pro $25.00 / 3.1 Flash-Lite $3.00 / 3.5 Flash $10.00

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

**Break-even vs long prompt [inferred]:** if a fine-tune removes 2,000 few-shot tokens/request, you save 2B input tokens/month at 1M req. At Gemini 2.5 Flash input $0.15/1M, that is $300/month saved. A 6M-token Flash-Lite tune costs $9 once. But Azure $1,224/month host means low-QPS Azure FT is often more expensive than a long cached prompt.

**Buy vs Rent:** Break-even for GPU purchase ($30K-$40K per H100) at ~10,000-16,000 usage hours. Organizations planning multiple training runs often find ownership more economical.

### Latency & Availability

| Metric | Target | Notes |
|---|---|---|
| Training p50 latency | N/A (batch) | Iteration velocity matters: 4 hrs vs 48 hrs per experiment |
| Inference p50 | <200ms TTFT | vLLM/SGLang with prompt caching |
| Inference p99 | <1s TTFT | Pre-warmed replicas, load shedding |
| Availability | 99.9% | Multi-provider fallback chain |
| RPO (model artifacts) | 0 | Replicated object storage (S3/GCS) |
| RTO (model serving) | <5 min | Blue-green deployment, instant rollback |

**Published latency datapoints (not SLOs):**

| Datapoint | Context |
|---|---|
| LoRA merged: zero added inference latency (Hu et al.) | Not a p99 SLO |
| LoRA unmerged batched: Punica +2 ms/token vs single-model, 12x multi-tenant throughput | 2024 research hardware |
| S-LoRA S1: ~7.6 req/s from 5 to 2,000 adapters | 2023 research |
| vLLM 0.15 + SageMaker: Amazon-tuned GPT-OSS 20B 171 OTPS / 124 ms TTFT | Blog benchmark |
| Databricks PEFT serving: ~1.5x vs "open" baselines; rank 32 is quality/perf tension | Their prod |
| Vertex SFT: excluded from SLA | No availability number to quote |
| Azure Developer FT: no availability SLA; Standard does offer regional residency | Hosting tier is the NFR |

**Architecture-derived targets [inferred]:**

| Metric | Adapter-hot (merged or GPU-resident LoRA) | Adapter-cold (swap / first load) | Mitigation |
|---|---|---|---|
| p50 | TTFT ~100-250 ms | + adapter load time | Keep canary + prod both GPU-hot (max_loras >= 2); merge if single adapter |
| p95 | 250-600 ms | 1-3 s if CPU LoRA page-in | Size max_loras to hot tenant set; max_cpu_loras = warm set |
| p99 | 600 ms-1.5 s then fail to base | 3-8 s then trip breaker | Timeout adapter path independently; never wait on a hung training job |

**For 400 output tokens, Punica's +2 ms/token is +800 ms decode vs merged.**

### Throughput and Back-Pressure

| Constraint | Documented Limit |
|---|---|
| vLLM `max_loras` | GPU-resident adapters; **default 1** -- second adapter evicts unless raised |
| vLLM `max_lora_rank` | Pre-allocated; **default 16**; rank above max = hard reject (restart) |
| vLLM `lora-extra-vocab-size` | Default 256 |
| Fireworks addons | **100** per dedicated deployment |
| S-LoRA S1 (research) | 2,000 concurrent adapters, ~7.61 req/s at n=2000 |
| Vertex concurrent tuning jobs | Default quota >=1 global; request raise |
| Fireworks serverless training | Default 8 concurrent runs; cannot serverless-serve the trained LoRA |
| Azure Standard vs Global | Global: higher throughput, no data-residency guarantee |
| OpenAI FT inference | Dies when the base dies; ft-* snapshot shutdowns 2026-10-23 |

**Back-pressure design:** (1) admission control on inference gateway by tenant_id (token bucket); (2) bulkhead training job API from serve -- a hung `CreateModelCustomizationJob` must not take serve threads; (3) if max_loras is the hot set, overflow tenants queue or route to a second replica; (4) degrade: requested adapter -> base model -> prompt/RAG -> deterministic schema fallback; (5) training-side: checkpoint-and-pause rather than a single 144-hour uncheckpointed DAPT.

**Capacity identity:** `hot_adapters <= max_loras`. Canary needs +1 slot. 100 Fireworks addons is a hard cap.

### NFRs and Explicit Trade-Offs

| NFR | Production Stance | Competes With |
|---|---|---|
| **Availability** | Vertex SFT excluded from SLA. Azure Developer: no availability SLA. Azure Standard: regional residency + hourly host. Circuit-break adapter independently of base. | $1.70/h host vs Developer 24h eviction |
| **RPO** | Last successful checkpoint (epoch or save_steps). Dataset lineage is the other RPO: you cannot unlearn a row without retrain. | Checkpoint frequency vs job throughput (I/O) |
| **RTO** | Adapter rollback = pointer flip (seconds, tens of MB). Merged = load previous full checkpoint (minutes). After OpenAI 2027-01-06: cannot create replacement job. | Keeping N adapter versions (cheap) vs N merged 70B copies |
| **Compliance** | Vertex: data in tuning region; compute may leave inside US/EU; no CMEK on Flash. Azure Standard = regional residency. OpenAI regional +10% uplift. Bedrock: VPC + KMS. Fine-tunes inherit base license (Llama 700M MAU). | Latency (residency path) and portability (OpenAI/Vertex: no downloadable weights) |
| **Privacy vs kernels** | LoRA-Leak AUC up to 0.775 using public base as reference. Defenses that kept utility: dropout and excluding layers. Unsloth currently wants lora_dropout=0 -- kernel constraint vs privacy. | Quality/speed vs MIA |
| **Quality vs forgetting** | Full FT highest capacity (Biderman); LoRA forgets less; alpha=2r; replay mix. | Plasticity (regularization that blocks forgetting can also block new-task learning) |

---

## Trade-Offs & Failure Modes

### Comprehensive Failure Mode Table

| Failure | Cause | Detection | Mitigation |
|---|---|---|---|
| **Catastrophic forgetting** | Task FT moves directions that implemented prior skills; IFT > CPT; code > math; volume increases forgetting | Frozen golden / Biderman 3-task average crashes while task loss falls | LoRA for IFT; alpha=2r; PPO-ptx replay gamma=27.8; OPLoRA/O-LoRA; block promote |
| **Reward hacking** | Proxy RM up, gold down as KL grows (Gao); length exploit (ODIN); GRPO tag-only rewards | Gold/task vs proxy RM; mean length Pareto | KL beta (InstructGPT 0.02, DeepSeekMath 0.04); held-out verifier; stop on gold; GSPO for MoE |
| **Eval contamination** | Paraphrased test items in FT data (DICE); GSM8K vs GSM1k gap | Private never-published eval; n-gram + embedding near-dup | Hold out real traffic; OOD exam beside public set |
| **Silent overfit** | Too many epochs; Unsloth loss -> 0; OpenAI diversity collapse | Epoch checkpoints; diversity slice | -1-2 epochs; pick by RM/dev quality not val NLL (InstructGPT/LIMA) |
| **SimPO/DPO garbage policy** | LR 1e-5 (SFT-typical) on preference trainers | Repetitive/incoherent gens; train loss still falling | LR grid 3e-7 to 1e-6; math ~5e-7 |
| **GRPO MoE collapse** | Token-level IS + expert routing volatility | Non-convergence on long runs | GSPO or Routing Replay |
| **QLoRA merge collapse** | merge_and_unload into bitsandbytes 4-bit (transformers#31293) | Serve-dtype eval != train loss | Merge bf16/fp16, then AWQ/GGUF; eval export |
| **Train/serve dtype skew** | QLoRA 16-bit adapters vs 4-bit base; GGUF Q4 of the delta | Serve-dtype eval diverges | Unsloth same-precision; Vertex: no extra thinking after thinking-off SFT |
| **Mode collapse** | Insufficient diversity in training data | Output diversity metrics, manual inspection | Diverse training examples, temperature > 0 during eval |
| **Constrained decode on tuned Gemini** | Constraints not applied during tuning | Quality drop at serve | Drop constrained decoding or tune with matching structure |
| **Adapter swap TTFT** | vLLM max_loras default 1; rank > max_lora_rank default 16 | TTFT spikes; hard reject | Size hot set; restart with max rank; canary slot +1 |
| **MoE unmerged LoRA** | Every expert's LoRA materialized at decode | OTPS collapse | Merge for MoE serving (PEFT) |
| **Wrong target modules** | Teams blame LoRA rank when real issue is poor module selection or bad data | Poor task performance despite reasonable rank | Target all linear modules; audit data quality first |
| **Weak DPO foundation** | Baseline model cannot produce decent candidates | Preference tuning becomes unstable | Ensure SFT baseline is adequate before running DPO |
| **Poison JSONL / PII in targets** | Harvested prod logs; identifiers in completions | DLP on dataset; LoRA-Leak-class MIA | InstructGPT-style filter; RAG for personal records; retrain to erase |
| **Duplicate job bills** | Non-idempotent CreateJob | Two adapter ids, two bills | Key = hash(dataset, base, peft, seed, code) |
| **ZeRO-3 restore empty** | state_dict placeholders | Load looks like random init | Enable gather on checkpoint |
| **Vendor lock / death dates** | OpenAI no new jobs 2027-01-06; ft-o4-mini 2026-10-23; Evals 2026-11-30 | Calendar | Weights-out path (Together) or migrate before cutoff |
| **License surprise** | LoRA inherits Llama 700M MAU / naming | Legal review at distribute | Verify checkpoint license (Qwen Apache vs Qwen License) |
| **Chat bench != humans** | Guanaco Vicuna 99.3%; Kendall tau=0.43 vs humans | Human / task holdout | Do not promote on chatbot leaderboards alone |
| **CMEK / SLA gap** | Vertex listed Flash: no CMEK; SFT not Covered Service | Contract review | Azure Standard residency or Bedrock KMS/VPC |
| **Stale model** | Model not periodically re-evaluated against base | A/B test drift | Scheduled re-evaluation cadence |
| **Using FT for mutable knowledge** | Creates stale answers and retraining churn | Outdated answers in production | Use retrieval for fast-changing knowledge |
| **Semantic drift** | Production quality degrades over time | Production quality metrics | Canary deployment, automated rollback |
| **Operational sprawl** | Many full tuned checkpoints | Storage, routing, rollback pain | Use adapters instead of full checkpoints |
| **Data contamination** | Training data overlaps with eval benchmarks | LLM-based decontamination, n-gram overlap | Strict decontamination pipeline before training |

---

## Production Patterns & Best Practices

### Distributed Training Patterns

| Pattern | Mechanism | Use Case |
|---|---|---|
| FSDP / FSDP2 | Shards params, gradients, optimizer states across GPUs | PyTorch native, 70B+ full FT. FSDP2 recommended by Axolotl |
| DeepSpeed ZeRO (1-3) | Progressive sharding. Stage 3 shards all three | Large-scale training with CPU/NVMe offload |
| Tensor Parallelism | Splits individual layers across GPUs | Models too large for single device even with sharding |

Both FSDP and DeepSpeed support elastic training where nodes can join/leave mid-run -- critical for spot-instance clusters.

**DeepSpeed ZeRO Stages:**

| Stage | What Is Partitioned | Typical Use |
|---|---|---|
| 1 | Optimizer states (Adam: 32-bit weights + momentum + variance) | LoRA often does not need this |
| 2 | Optimizer states + gradients | Mid-size full FT |
| 3 | Parameters + grads + optimizer (all-gather per layer) | Full FT 70B+ |

**ZeRO-3 footgun**: `state_dict` contains placeholders unless gathering is enabled -- a restore trap.

**Axolotl specifics:** Prefer FSDP2; FSDP1 deprecated. Compose FSDP + TP + CP + EP via DeviceMesh. DDP+TP/CP is explicitly unsupported. FSDP+QLoRA: **70B on two 24 GB GPUs** (Answer.AI path). FSDP2 swap fallback uses disk swap when CPU RAM is exhausted.

### Checkpointing & Spot Resilience

Standard practice: save model checkpoints every N steps. With spot instances (60-70% cost savings), checkpointing is mandatory.

**Model checkpointing (job resilience):** OpenAI: epoch-based checkpoints. Azure RFT: pause at $5,000 writes a deployable checkpoint. Together: retrieve `steps_completed` on cancel. Fireworks: resume optimizer state or fork from a fully qualified state URI.

**Gradient checkpointing** (distinct from model checkpointing): trades compute for memory by recomputing activations during backward pass instead of storing them. Unsloth `use_gradient_checkpointing="unsloth"` (2x vs default). Enables QLoRA with 70B on ~41 GB VRAM.

**Preemption / spot:** Azure Developer training uses preemptible capacity: jobs pause and auto-resume; no charge while paused; 50% off global. Self-host: checkpoint-to-object-storage is mandatory on spot; neither FSDP nor ZeRO makes a job automatically elastic unless Torch Elastic / Kubernetes is configured.

### Model Weight Security

Fine-tuned model weights are intellectual property. Key controls:
- **AES-256** encryption at rest for model artifacts and training data
- **TLS** in transit for all model transfers
- **RBAC** on model registries: only authorized personnel can promote to production
- **Immutable audit logs** for all training data access, fine-tuning jobs, model versions
- **Cryptographic signatures** on adapter files to prevent tampering (cosign or Sigstore)

### SOC 2 for AI/ML Fine-Tuning

| Control | What Auditors Test |
|---|---|
| CC6.3 | RBAC ensuring only authorized personnel access training data |
| CC9.2 | Risk mitigation covering model drift, retraining, third-party providers |
| Immutable logs | Training data, fine-tuning jobs, model versions |
| Drift monitoring | Model bias and behavior checks in pipeline |
| Vendor risk | Annual reassessment of third-party AI sub-processors |

IBM 2025: 97% of organizations experiencing ML system incidents lacked proper access controls; 63% lacked governance policies. Average breach cost: $4.4M.

### Training Data Governance

- **GDPR**: Unlike RAG, deleting data from a fine-tuned model requires retraining without that data. No selective deletion possible. Prefer RAG for personal records.
- **HIPAA**: PHI in training data requires BAA with cloud providers, encrypted storage, access logging.
- **EU AI Act**: High-risk obligations apply from December 2, 2027.

### Zero-Trust Fine-Tuning Architecture

Assume-breach posture applied to the entire fine-tuning pipeline.

| Principle | Implementation |
|---|---|
| **Mutual TLS between training nodes** | All inter-node communication (gradient sync, checkpoint transfer) uses mTLS with short-lived certificates rotated every 24 hours. No plaintext training data or gradient traffic on the wire. |
| **Signed model artifacts** | Every adapter and checkpoint is cryptographically signed at creation time. The model registry rejects unsigned or tampered artifacts. Signature verification enforced at serving load time. |
| **Isolated training environments** | Training VMs/containers run with no internet egress. Dependencies pre-baked into container images from audited internal registry. Training data mounted read-only from encrypted storage. No SSH access during training runs. |
| **Capability-scoped API tokens** | Short-lived, narrowly scoped tokens: `registry:read` for inference, `registry:write` for training, `registry:promote` for reviewers only. Bound to specific service identities via workload identity federation. |
| **Microsegmentation** | Network policies restrict training pods to communicate only with: checkpoint store, metrics collector, and peer training nodes. All other egress denied by default. |

**Verification chain**: Data store (encrypted, access-logged) -> Training environment (isolated, no egress) -> Checkpoint store (signed, integrity-verified) -> Model registry (RBAC-gated, immutable versions) -> Serving layer (signature-verified load, mTLS to registry).

### Zero-Trust MCP for Training & Adapters

`tools/call` on `submit_sft_job` or `generate_ft` is a weight-and-data exfil API.

1. **Server-side identity.** tenant_id / dataset URI / adapter id from verified token / RunContext, never from tool arguments the model filled.
2. **Least privilege per tool.** `submit_sft_job` vs `submit_dpo_job` vs `submit_rft_job`. `generate_ft` vs `generate_base` vs `retrieve_kb`. No omnibus `train(dataset, tenant_id)`.
3. **Stateless MCP + stateful jobs.** Job state in registry/checkpointer, not MCP session. Conversation memory stays out of training JSONL unless a reviewed harvest pipeline allows it.
4. **No raw train-row echo** to unauthorized traces. Log hashes + PII-report ids, not completions containing PHI.
5. **Hosted FT:** provider sees your JSONL. Contract residency matters.

### PII Filtering Pipeline for Training Data

A 4-stage detection -> redaction -> audit trail pipeline that runs as a mandatory pre-processing gate before any training job.

**Stage 1: Detection (multi-layer)**

| Detection Method | Targets | Precision | Recall |
|---|---|---|---|
| NER models (spaCy, Presidio) | Names, addresses, organizations, dates of birth | High | Medium-high |
| Regex patterns | SSN, email, phone, credit card (Luhn-validated), IP addresses | Very high | High (for formatted PII) |
| Context-dependent classifier | Medical record numbers, internal employee IDs, account numbers | Medium | Medium |

All three layers run in parallel. Union of detections passed to redaction. Prefer false positives (over-redact) over false negatives (leak PII).

**Stage 2: Redaction**
- Replace detected PII with typed, indexed placeholders: `[EMAIL_1]`, `[SSN_2]`, `[PHONE_3]`, `[NAME_4]`.
- Placeholders preserve semantic structure so the model learns the pattern without memorizing real PII.
- Reversible mapping stored in a separate, access-controlled vault (never alongside training data).
- Consistency: the same entity maps to the same placeholder within a document.
- Second gate: drop rows that still match PAN/MRN/SSN regex after redaction -- poison-pill those, do not train.

**Stage 3: Audit trail**
Every redaction is logged to an immutable store (append-only, WORM-compliant):
- Original content hash (SHA-256, not the content itself)
- Redaction type (EMAIL, SSN, PHONE, NAME, etc.)
- Detection method that flagged it (NER, regex, classifier)
- Timestamp (ISO 8601, UTC)
- Dataset version and source file identifier
- Redaction action taken (replaced, removed, flagged-for-review)

**Stage 4: Training gate**
PII detection rate (flagged tokens / total tokens). If rate exceeds threshold (default: >0.1% of tokens), training job is blocked and alert raised.

**Chain-of-custody for the decision to serve:** adapter_sha + eval-report hash + promoter identity in every serve span.

### Tool-Level RBAC for Fine-Tuning Operations

| Role | Permissions | Denied |
|---|---|---|
| **data-engineer** | Prepare datasets, upload to data store, run PII filtering pipeline, view dataset metadata | Launch training, access model weights, promote models |
| **ml-engineer** | Launch training jobs, view training metrics, download evaluation results, read model registry | Modify datasets, promote models to production, modify RBAC |
| **reviewer** | Approve or reject model promotion, view eval results, view audit logs, trigger canary deployment | Launch training, modify datasets, modify RBAC |
| **admin** | Modify RBAC policies, manage service accounts, configure pipeline thresholds, emergency rollback | No restrictions (all actions audit-logged) |

Roles mapped to identity provider groups (Okta, Azure AD). All role assignments and changes logged to immutable audit trail.

### LoRA Leakage / MIA Risk

LoRA-Leak (2025): 15 attacks; calibrating against the public base amplifies leakage; conservative FT AUC **0.765 / 0.721 / 0.775** on Llama-2 / GPT-2 XL / Pythia-2.8B. Do not publish adapters trained on PHI/PII. Dropout > 0 helps even when Unsloth wants 0 -- document the conflict.

### License Inheritance

A LoRA on Llama-3.3-70B is still Llama-licensed (700M MAU; "Built with Llama"; name starting with "Llama"). Qwen3 official cards often Apache 2.0 -- verify the checkpoint (some Qwen2.5-72B / selected Qwen3.5 rows still show 100M MAU-class custom license). Mistral 7B Instruct: Apache 2.0; API-only Large/Medium: commercial ToS. DeepSeek-R1 weights: MIT (inherited-license caveats for some distills).

### Tenant Isolation Patterns

| Pattern | Guarantee | Cost |
|---|---|---|
| Shared replica, many LoRAs | Logical isolation; Punica/S-LoRA batch different LoRAs in one GPU forward -- latency coupled (noisy neighbor). LoRA-Leak if attacker can query both base and adapter | Cheapest; Fireworks cap 100 |
| Dedicated replica per tenant | Azure Standard / Together / Fireworks dedicated; pay GPU-time | $1.70/h Azure or dedicated minutes |
| Merged per tenant | Max decode; no hot-swap; rollback = full file | Storage; 70B copies |

### Circuit Breaker Pattern

Independent breakers for: **training job API**, **adapter serve**, **base serve**. A Together token-meter storm or Vertex concurrent-job quota must not starve chat (bulkhead).

```
        failures >= threshold or error-rate window
  +----------+  ---------------------------------->  +----------+
  |  CLOSED  |                                       |   OPEN   |
  | pass all |  success resets consecutive count     | fail fast|
  +----+-----+                                       +----+-----+
       ^                                                  | cooldown elapsed
       | trial success                                    v
       |                                            +----------+
       +------------ trial OK ----------------------| HALF-OPEN|
                    trial fail -> OPEN               | 1 probe  |
                                                    +----------+
```

**Thresholds:** Trip training-API breaker on 5xx, quota, and repeated job.failed with same config hash (that is permanent -- do not half-open into the same poison). Trip adapter-serve on TTFT timeout / max_lora_rank reject / load failures. Cooldown tens of seconds for serve; minutes-to-hours for training APIs.

**Fallback chain:** requested FT adapter -> base model (same tokenizer) -> prompt / RAG (facts + schema in-context) -> deterministic fallback (regex/schema extract, canned refusal, last-known-good template).

---

## Code Examples

### Production Code A: Adapter Registry, Canary Controller, and Fallback Chain (Opus)

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


# --- Circuit Breaker ---

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


# --- Retry with Backoff + Jitter ---

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


# --- Evaluation Gate ---

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


# --- Adapter Version Management ---

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


# --- Canary Deployment Controller ---

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


# --- Fallback Inference Chain ---

class FallbackInferenceChain:
    """
    Try fine-tuned model -> base model with prompt -> cached response.
    Each provider protected by a circuit breaker.
    """

    def __init__(self, providers: list[tuple[str, Any, CircuitBreaker]]):
        self.providers = providers  # (name, inference_fn, circuit_breaker)

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
                return {
                    "text": result,
                    "provider": name,
                    "fallback": name != self.providers[0][0],
                }
            except Exception as e:
                cb.record_failure()
                errors.append((name, str(e)))
                logger.warning("fallback.failed", provider=name, error=str(e), cid=cid)

        raise RuntimeError(f"All inference providers failed: {errors}")
```

### Production Code B: Control Plane + Serve Runtime with Full Jitter, Authz, Lineage (Grok)

```python
#!/usr/bin/env python3
"""Fine-tune control+serve resilience: retries, breakers, adapter->base->deterministic.

Stdlib only. Swap Fake* ports for vendor HTTP (OpenAI jobs, vLLM /v1/load_lora_adapter).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# Optional deps (not required to run this file):
#   import httpx  # vendor job + vLLM client
#   from peft import PeftModel  # local adapter load; merge_and_unload() MUST be assigned


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        record.job_id = getattr(record, "job_id", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("ft")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"job":"%(job_id)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(
    level: int,
    msg: str,
    *,
    cid: str,
    tenant: str,
    job: str = "-",
    **fields: object,
) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant, "job_id": job}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, preemption, adapter swap timeout -- safe to retry idempotent ops."""


class PermanentError(Exception):
    """4xx auth, rank>max_lora_rank, cutoff org, poison config hash -- do not retry."""


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    cid: str,
    tenant: str,
    op: str,
    job: str = "-",
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 2.0,
) -> object:
    """Retry with full jitter (AWS-style). Distinguishes transient vs permanent errors."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = min(cap_s, base_s * (2**i))
            sleep = random.uniform(0, sleep)  # full jitter
            slog(
                logging.WARNING, "retry",
                cid=cid, tenant=tenant, job=job, op=op,
                attempt=i + 1, sleep_s=round(sleep, 3), err=str(exc),
            )
            time.sleep(sleep)
    assert last is not None
    raise last


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(TransientError):
    pass


@dataclass
class CircuitBreaker:
    """Independent circuit breaker for train API, adapter serve, and base serve."""
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        now = time.monotonic()
        if self._state is CircuitState.OPEN:
            if now - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Authz:
    """Server-side authorization. adapter_id NEVER parsed from model JSON."""
    tenant_id: str
    actor: str
    allowed_adapter_id: str | None


@dataclass(frozen=True)
class Lineage:
    """Full lineage tuple for idempotent job submission."""
    dataset_hash: str
    base_rev: str
    peft_json: str
    seed: int
    code_sha: str

    def idempotency_key(self) -> str:
        raw = "|".join(
            [self.dataset_hash, self.base_rev, self.peft_json, str(self.seed), self.code_sha]
        )
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class EvalReport:
    """4-gate evaluation: task, forgetting, safety, serve-dtype."""
    task_ok: bool
    forgetting_ok: bool
    safety_ok: bool
    serve_dtype_ok: bool

    def promote_allowed(self) -> bool:
        return self.task_ok and self.forgetting_ok and self.safety_ok and self.serve_dtype_ok


class JobClient(Protocol):
    name: str
    def submit(self, lineage: Lineage, method: str) -> str: ...
    def status(self, job_id: str) -> str: ...


class Generator(Protocol):
    name: str
    def complete(self, prompt: str, adapter_id: str | None) -> str: ...


@dataclass
class JobRegistry:
    """Process-local stand-in; production: Postgres unique(idempotency_key)."""
    _jobs: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self._jobs.get(key)

    def put(self, key: str, job_id: str) -> None:
        self._jobs[key] = job_id


class FtControlPlane:
    """Training-API breaker + idempotent submit. Eval gate is a hard block."""

    def __init__(self, jobs: JobClient, registry: JobRegistry | None = None) -> None:
        self.jobs = jobs
        self.registry = registry or JobRegistry()
        self.breaker = CircuitBreaker("train_api", cooldown_s=60.0)

    def submit_idempotent(
        self, lineage: Lineage, method: str, cid: str, tenant: str
    ) -> str:
        key = lineage.idempotency_key()
        existing = self.registry.get(key)
        if existing:
            slog(logging.INFO, "job_dedup", cid=cid, tenant=tenant, job=existing, key=key[:12])
            return existing

        def _op() -> str:
            self.breaker.allow()
            try:
                jid = self.jobs.submit(lineage, method)
            except PermanentError:
                self.breaker.record_failure()
                raise
            except Exception as exc:
                self.breaker.record_failure()
                raise TransientError(str(exc)) from exc
            self.breaker.record_success()
            return jid

        jid = retry_with_jitter(
            _op, cid=cid, tenant=tenant, op="train_submit", attempts=3, base_s=0.2, cap_s=5.0
        )
        assert isinstance(jid, str)
        self.registry.put(key, jid)
        slog(logging.INFO, "job_submitted", cid=cid, tenant=tenant, job=jid, method=method)
        return jid

    def promote(self, adapter_id: str, report: EvalReport, cid: str, tenant: str) -> str:
        if not report.promote_allowed():
            slog(
                logging.ERROR, "promote_blocked", cid=cid, tenant=tenant, job=adapter_id,
                task=report.task_ok, forget=report.forgetting_ok,
                safety=report.safety_ok, dtype=report.serve_dtype_ok,
            )
            raise PermanentError("eval_gate_failed")
        slog(logging.INFO, "promote_ok", cid=cid, tenant=tenant, job=adapter_id)
        return adapter_id


@dataclass
class DegradedResult:
    """Result with degradation metadata for observability."""
    text: str
    adapter_degraded: bool
    generation_degraded: bool
    served: str  # adapter | base | deterministic


class FtServeRuntime:
    """Serve fallback: FT adapter -> base -> deterministic. Independent breakers."""

    def __init__(
        self,
        adapter_gen: Generator,
        base_gen: Generator,
        adapter_timeout_s: float = 2.0,
    ) -> None:
        self.adapter_gen = adapter_gen
        self.base_gen = base_gen
        self.adapter_timeout_s = adapter_timeout_s
        self.breakers = {
            "adapter": CircuitBreaker("adapter_serve"),
            "base": CircuitBreaker("base_serve"),
        }

    def _call(self, gen: Generator, prompt: str, adapter_id: str | None,
              cid: str, tenant: str) -> str:
        br = self.breakers["adapter" if adapter_id else "base"]

        def _op() -> str:
            br.allow()
            t0 = time.monotonic()
            try:
                text = gen.complete(prompt, adapter_id)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            if adapter_id and (time.monotonic() - t0) > self.adapter_timeout_s:
                br.record_failure()
                raise TransientError("adapter_ttft_timeout")
            br.record_success()
            return text

        label = f"generate:{gen.name}:{adapter_id or 'base'}"
        return retry_with_jitter(_op, cid=cid, tenant=tenant, op=label)

    def complete(self, prompt: str, authz: Authz, schema_fallback: str) -> DegradedResult:
        cid = str(uuid.uuid4())
        slog(logging.INFO, "serve_start", cid=cid, tenant=authz.tenant_id, q=prompt[:200])
        aid = authz.allowed_adapter_id

        # Level 1: Try fine-tuned adapter
        if aid:
            try:
                text = self._call(self.adapter_gen, prompt, aid, cid, authz.tenant_id)
                slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="adapter")
                return DegradedResult(text, False, False, "adapter")
            except (TransientError, PermanentError) as exc:
                slog(logging.ERROR, "adapter_failed", cid=cid,
                     tenant=authz.tenant_id, err=str(exc))

        # Level 2: Fall back to base model (longer prompt / RAG belongs here)
        try:
            slog(logging.WARNING, "fallback_base", cid=cid, tenant=authz.tenant_id)
            text = self._call(self.base_gen, prompt, None, cid, authz.tenant_id)
            slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="base")
            return DegradedResult(text, True, False, "base")
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "base_failed", cid=cid,
                 tenant=authz.tenant_id, err=str(exc))

        # Level 3: Deterministic fallback (regex/schema extract, canned response)
        slog(logging.ERROR, "serve_deterministic", cid=cid, tenant=authz.tenant_id)
        return DegradedResult(
            f"Generation unavailable. Deterministic fallback: {schema_fallback}",
            True,
            True,
            "deterministic",
        )


# --- Demo backends (swap for real vLLM / vendor HTTP clients) ---

class FakeJobClient:
    name = "train_api"

    def submit(self, lineage: Lineage, method: str) -> str:
        _ = method
        return f"job-{lineage.idempotency_key()[:8]}"

    def status(self, job_id: str) -> str:
        return f"succeeded:{job_id}"


class StaticGenerator:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def complete(self, prompt: str, adapter_id: str | None) -> str:
        if self.fail:
            raise TransientError("simulated_outage")
        tag = adapter_id or "base"
        return f"[{tag}] {prompt[:40]}"


if __name__ == "__main__":
    cid = str(uuid.uuid4())
    lineage = Lineage(
        dataset_hash="sha256:abc",
        base_rev="llama-3.1-8b@rev1",
        peft_json='{"r":32,"alpha":64,"target":"all-linear"}',
        seed=42,
        code_sha="deadbeef",
    )
    control = FtControlPlane(FakeJobClient())
    jid = control.submit_idempotent(lineage, "sft", cid, "acme")
    jid2 = control.submit_idempotent(lineage, "sft", cid, "acme")
    assert jid == jid2  # idempotent: same lineage -> same job
    control.promote(
        "adapter-v3",
        EvalReport(True, True, True, True),
        cid,
        "acme",
    )
    serve = FtServeRuntime(
        adapter_gen=StaticGenerator("adapter_gen", fail=True),
        base_gen=StaticGenerator("base_gen"),
    )
    authz = Authz(tenant_id="acme", actor="u1", allowed_adapter_id="adapter-v3")
    result = serve.complete("Emit the ticket JSON schema only.", authz, '{"status":"degraded"}')
    print(json.dumps({
        "job_id": jid,
        "dedup_ok": jid == jid2,
        "text": result.text,
        "served": result.served,
        "adapter_degraded": result.adapter_degraded,
        "generation_degraded": result.generation_degraded,
    }, indent=2))
```

**Wired in this code:** full-jitter retries; closed->open->half-open breakers on train API and adapter/base serve; idempotent job submit keyed by dataset+base+peft+seed+code SHA; 4-gate eval hard-blocks promote; fallback adapter -> base -> deterministic; JSON logs with cid+tenant+job. Real vLLM clients must send `authz.allowed_adapter_id` as the `model` alias, never a model-emitted string. Real merge path: `merged = PeftModel.merge_and_unload()` -- assign the return; merge into bf16 then re-quantize; eval the export.

---

## Interview Q&A

**Q1: When should I fine-tune instead of using RAG?**
Fine-tune when you need a stable behavior or style change -- schema compliance, persona, domain jargon, tool-call JSON format. Use RAG for fresh or private knowledge that changes. They solve different problems. The best answer is often both: RAG for facts, tuning for behavior. Fine-tuning is a bad document store: no citation, no ACL, no instant unpublish. OpenAI is winding down self-serve FT because newer bases already follow instructions; Anthropic has no first-party FT API. Measure whether few-shot prompting is enough before you mint adapters.

**Q2: Explain LoRA in one sentence.**
LoRA keeps base weights frozen and learns a low-rank delta (two thin matrices B and A with rank r << d) inserted into selected layers, so you train <1% of parameters and store megabyte-sized adapters instead of full model copies.

**Q3: Why did QLoRA matter?**
It made large-model fine-tuning practical on far less hardware by combining 4-bit frozen base weights (NF4 quantization + double quantization) with trainable 16-bit LoRA adapters and paged optimizers. Result: 65B model fine-tuned on a single 48 GB GPU with <1% quality degradation. Full 16-bit at that scale was >780 GB.

**Q4: What are rank and alpha, actually?**
Rank r is the inner dimension of BA; trainable params per matrix are r*(d+k). Alpha scales the update: Hu uses alpha/r; rsLoRA uses alpha/sqrt(r). PEFT's default alpha=r=8 is the intruder-dimension setting; Biderman and the "Illusion of Equivalence" paper want alpha=2r. Databricks: most customers need at least rank 32 to avoid quality drops. Biderman IFT: r=256, all modules, alpha=512 for code tasks. vLLM's default max_lora_rank=16 will hard-reject a 256-rank adapter until restart.

**Q5: SFT vs DPO vs RFT / GRPO?**
SFT learns from gold outputs -- teaches format and style. DPO reparameterizes the KL-constrained RLHF optimum into a pairwise loss with a frozen reference -- simple, no reward model, no sampling loop. GRPO drops the PPO critic, samples a group (G=64 in DeepSeekMath), and needs a verifier or reward signal. PPO is the full RLHF stack (4x VRAM) and still suffers reward hacking (Gao). ORPO folds SFT + preference into one stage with no reference model but has higher forgetting risk. KTO works with binary signals (thumbs up/down), not pairs.

**Q6: DPO vs PPO vs GRPO vs ORPO -- pick in one minute.**
PPO needs a reward model, value head, and on-policy samples -- InstructGPT 4x-class VRAM and still overoptimizes (Gao). DPO reparameterizes that into a pairwise loss with a frozen reference -- Together ~10-12% surcharge; Fireworks 2x. ORPO folds preference into SFT with no reference; watch forgetting because no KL anchor. GRPO drops the critic, samples G=64, needs a verifier; DeepSeekMath LR 1e-6, KL 0.04. GSPO if MoE or long rollouts.

**Q7: What is the biggest data lesson in fine-tuning?**
Quality and consistency matter more than raw dataset size once you have enough coverage. LIMA used 1,000 curated pairs and matched 43% of GPT-4 quality. Contradictory labels, duplicates, or unclear formatting teach the wrong behavior very efficiently. Narrow data can overfit tone and damage general capability.

**Q8: Does fine-tuning solve prompt injection?**
No. Injection is an application security problem. Fine-tuning may improve robustness slightly, but it is not a security boundary and does not provide tenant ACLs, freshness, or data governance.

**Q9: Should I merge adapters into the base model?**
Merge when you have a single model (zero extra inference latency). Use adapter catalogs for multi-tenant systems (hot-swap, independent rollback, each ~50 MB). For MoE models, merge is recommended because unmerged LoRA materializes every expert's adapter at decode and collapses throughput. Do not merge a quantized base -- merge into bf16/fp16, then re-quantize.

**Q10: When is RFT / GRPO worth it?**
When you have a reliable programmatic grader (unit tests, boxed math, schema validators), the task is measurable, and the base model has non-zero competence. RFT only helps when the eval is neither floor nor ceiling. DeepSeekMath GRPO: GSM8K 82.9% -> 88.2%, MATH 46.8% -> 51.7%. OpenAI RFT is $100/h core loop.

**Q11: Explain production fine-tuning to someone who only knows ChatGPT.**
Split a test kitchen from the dining room. Training ingests redacted JSONL, runs a job (SFT or LoRA or DPO), checkpoints, and only promotes after holdout, forgetting, safety, and serve-dtype evals pass. Serving loads the same base weights plus a megabyte adapter per tenant -- or a merged file -- and never waits on a hung GPU job.

**Q12: When do you refuse to fine-tune?**
When the behavior fits in a cached prompt, or the knowledge moves weekly (that is RAG). I still SFT for a schema or persona that few-shot keeps missing, and I CPT only when the model does not speak the domain. Newer bases reduce the need.

**Q13: LoRA vs full fine-tune -- which do you pick?**
LoRA for instruction FT: Biderman shows it forgets less and high-rank IFT can match full FT. Target all linear layers, start from Databricks' rank 32, go to r=256, alpha=2r for code. Do not use LoRA for continued pretraining -- that gap does not close. Full FT if memory allows and CPT quality is the product.

**Q14: Give me a cost model for training and for 1,000 production calls.**
Training: 5,000 * 400 tok * 3 epochs = 6M billable. Together 8B LoRA hits the $4 minimum; Fireworks LoRA $3; Vertex Flash-Lite $9; 3.5 Flash $60; 2.5 Pro $150. Serving 800/400: o4-mini FT [inferred] $9.60/1k uncached; Gemini 2.0 Flash-class tuned-at-base [inferred] $0.36/1k; add Azure $1.224/1k host at 1M requests/month -- or $122/1k at 10k requests. The train job is not the annual bill.

**Q15: What SLO do you put in the contract for a fine-tuned endpoint?**
Do not quote a vendor FT p99 -- nobody publishes one, and Vertex SFT is excluded from SLA. SLO adapter-hot TTFT separately from generate, treat 124 ms TTFT on Amazon-tuned GPT-OSS 20B as a blog existence proof (not a contract), set an adapter timeout as policy, merge when you can (Hu: zero extra latency), and fail to base rather than wait on LoRA swap. Punica's +2 ms/token is ~+800 ms on a 400-token completion if you stay unmerged.

**Q16: How do you stop a bad adapter from taking down prod?**
Eval gate is a hard block (4 gates: task, forgetting, safety, serve-dtype). Canary 1-5% on the same replica with max_loras >= 2. Rollback is a registry pointer for unmerged LoRA (35 MB-class artifacts). Merged rollback is a full checkpoint. Independent circuit breakers for train API vs adapter vs base. Fallback adapter -> base -> RAG/prompt -> deterministic schema.

**Q17: Catastrophic forgetting showed up on our general eval. Now what?**
That is the signature: task loss down, HellaSwag/WinoGrande/ARC down. Switch IFT to LoRA with alpha=2r, mix replay like PPO-ptx gamma=27.8, do not keep pouring data -- Biderman saw forgetting increase with volume. For continual tasks look at orthogonal LoRA (O-LoRA / OPLoRA). Never promote on Vicuna GPT-4-as-judge alone -- QLoRA's own Kendall tau with humans was 0.43.

**Q18: Multi-tenant LoRA serving -- what bites in production?**
vLLM defaults: max_loras=1 (evict), max_lora_rank=16 (reject Biderman ranks). Memory is max_loras * max_lora_rank * hidden at start. Fireworks caps 100 addons and will not serverless-serve your custom LoRA. Isolation is logical -- Punica batches different LoRAs in one forward, so latency couples. Attacker who can hit base and adapter is the LoRA-Leak threat model (AUC 0.775). Regulated tenants get a dedicated replica. MoE: merge.

**Q19: QLoRA on 65B -- what do you actually need to remember?**
NF4 + double quant + paged Adam + 16-bit adapters; 65B in <48 GB, Guanaco 24h on one 48 GB GPU, 41 GB 4-bit footprint, LR 1e-4 at 33B/65B. MMLU NF4+DQ 53.1 vs bf16 53.0 -- but they did not claim 16-bit full-FT parity at 65B. Do not merge into 4-bit. Axolotl FSDP+QLoRA: 70B on two 24 GB GPUs. Unsloth wants lora_dropout=0, which fights the MIA dropout defense.

**Q20: Zero-Trust MCP around training and adapters -- failure mode?**
An omnibus `train(dataset_uri, tenant_id)` or `complete(model_id)` filled by the model. That is a data-and-weight exfil API. Split submit_sft / submit_rft / generate_ft / generate_base / retrieve_kb, take identity from verified token, key jobs by dataset hash + base rev + peft JSON + seed + code SHA, log hashes not PHI completions, and do not harvest production logs into a training job without metadata filters. After 2027-01-06 you cannot mint an OpenAI replacement -- portability is a security+continuity requirement.

---

## System Design Scenarios

### Scenario 1: Domain-Specific Customer Email Classifier for E-Commerce

**Problem Statement**: 50K customer emails daily across 15 categories (refund request, shipping inquiry, product complaint, compliment, fraud report, etc.). Current rule-based system: 72% accuracy, 40% manual review rate. Target: >92% accuracy, <5% manual review, <200ms p95 inference, deployed within 8 weeks. Budget: $25K total.

**Proposed Architecture:**

```
+----------------------------------------------------------------+
|                   EMAIL CLASSIFICATION PIPELINE                  |
|                                                                  |
|  +----------+   +--------------+   +----------------------+     |
|  | Email     |-->| Preprocessor |-->| Fine-Tuned Llama 3.3 |     |
|  | Ingestion |   | (truncate,   |   | 8B + QLoRA Adapter   |     |
|  |           |   |  normalize)  |   | (classification head)|     |
|  +----------+   +--------------+   +----------+-----------+     |
|                                                |                 |
|                                     +----------+----------+      |
|                                     |  Confidence Router   |      |
|                                     |  >0.9: auto-route    |      |
|                                     |  0.7-0.9: LLM verify |      |
|                                     |  <0.7: human review  |      |
|                                     +---------------------+      |
+----------------------------------------------------------------+
```

**Trade-Off Matrix:**

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| A: Prompt engineering (Claude/GPT) | Zero training cost, deployed in days | $14,400/year inference (50K/day), 80-85% accuracy, no confidentiality | Rejected: accuracy gap and ongoing cost |
| **B: QLoRA on Llama 3.3 8B** | $6-14 training cost/run, ~93% accuracy, self-hosted ~$1K/mo inference, data stays on-prem | Requires 5K labeled examples, 2-4 hrs training/iteration | **Selected** |
| C: Full fine-tune of 70B | Potentially 95%+ accuracy | $250-510/run, $2K/mo inference, overkill for classification | Rejected: diminishing returns |

**Decision Rationale**: Classification is the ideal fine-tuning use case -- narrow task, high volume, consistent format. QLoRA on an 8B model achieves 93%+ accuracy at $3-14 per training run. The confidence router sends high-confidence predictions straight to routing, medium to cheaper LLM verification, low to human review -- targeting <5% manual review. Total cost: ~$5K setup + ~$1K/month inference = well within $25K budget. Self-hosting eliminates sending customer PII to third-party APIs. Adapter size (~50 MB) enables instant rollback.

---

### Scenario 2: Multi-Domain Legal Document Drafting Assistant

**Problem Statement**: Law firm wants AI for document drafting across 5 practice areas. 10,000 historical documents. Must maintain general legal reasoning while adopting firm-specific patterns. Confidentiality paramount (no data leaves firm infrastructure). Budget: $100K first year.

**Proposed Architecture:**

```
+-----------------------------------------------------------------+
|                    LEGAL DRAFTING SYSTEM                          |
|                                                                   |
|  +----------------------------------------------------------+   |
|  |              BASE MODEL: Llama 3.3 70B (Self-Hosted)       |   |
|  |              Serving: vLLM on 2x H100                      |   |
|  +----------------------------------------------------------+   |
|                            |                                      |
|              +-------------+-------------+                       |
|              v             v             v                        |
|  +---------------+ +------------+ +---------------+              |
|  | LoRA Adapter   | | LoRA       | | LoRA Adapter  |   ...       |
|  | Corporate Law  | | Litigation | | IP Law        |             |
|  | (50 MB)        | | (50 MB)    | | (50 MB)       |             |
|  +---------------+ +------------+ +---------------+              |
|                            |                                      |
|                            v                                      |
|  +----------------------------------------------------------+   |
|  |  COMPOSABLE ADAPTER ROUTER                                 |   |
|  |  Practice area detected -> load appropriate adapter        |   |
|  |  Hot-swap in <100ms                                        |   |
|  +----------------------------------------------------------+   |
|                            |                                      |
|                            v                                      |
|  +----------------------------------------------------------+   |
|  |  RAG LAYER (Hybrid Retrieval from Firm Knowledge Base)     |   |
|  |  Precedent cases, clause libraries, client-specific terms  |   |
|  +----------------------------------------------------------+   |
|                            |                                      |
|                            v                                      |
|  +----------------------------------------------------------+   |
|  |  EVAL: Senior attorney review on 10% sample                |   |
|  |  + LLM-as-judge on formatting compliance                   |   |
|  +----------------------------------------------------------+   |
+-----------------------------------------------------------------+
```

**Trade-Off Matrix:**

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| A: Single fine-tuned 70B | Simpler ops, one model | Catastrophic forgetting across 5 practice areas | Rejected: forgetting risk too high |
| **B: Composable LoRA (per practice area)** | Per-domain specialization, hot-swap, base preserves general reasoning, each adapter independent | More complex adapter routing, requires practice area detection | **Selected** |
| C: Five separate fine-tuned models | Maximum isolation | 5x inference cost ($10K/mo vs $2K/mo), 5x ops burden | Rejected: cost and operations |

**Decision Rationale**: Each practice area gets its own adapter (50 MB each), trained on 1,000-2,000 domain examples. The base 70B retains general legal reasoning because adapters modify <1% of weights. Hot-swapping <100ms. RAG grounds in actual firm precedents (FT teaches style, RAG provides facts). Self-hosted on 2x H100 ($5K/mo). First-year: ~$60K hardware + ~$20K data prep + ~$5K training + ~$15K engineering. Each adapter retrained independently without affecting other domains.

---

### Scenario 3: Multi-Tenant SaaS -- Per-Tenant LoRA on One Base (10k Tenants)

**Problem Statement**: B2B copilot. Each tenant has a stable ticket JSON schema + tone, but facts (SKU tables, error codes) change weekly. Peak: thousands QPS mixed across tenants. Requirements: SOC 2 isolation, hot-swap without cloning 8B weights per tenant, p95 chat in a few seconds, rollback per-tenant adapter without touching others. Do NOT fine-tune the SKU catalog.

**Proposed Architecture:**

```
  +--------------+     +---------------------------------------------+
  | Tenant IdP   |     | CONTROL: dataset_hash -> SFT LoRA job        |
  | JWT -> PEP   |---->|   r=32, alpha=64 (2r), all-linear            |
  +--------------+     |   eval: schema exact-match + forgetting       |
                       |   + safety + serve-dtype                      |
                       |   registry: tenant_id -> adapter_id (env)     |
                       +------------------+----------------------------+
                                          v
                       +----------------------------------------------+
                       | SERVE: vLLM --enable-lora                     |
                       |   max_loras = GPU-hot set (>> 1; canary +1)   |
                       |   max_lora_rank >= 32 (not default 16)        |
                       |   max_cpu_loras = warm set                    |
                       | Fireworks alt: <=100 addons / dedicated       |
                       | RAG tool for SKU/error facts (ACL predicate)  |
                       +------------------+---------------------------+
                                          v
                       +----------------------------------------------+
                       | Fallback: adapter -> base -> schema template   |
                       | Canary 5% on same replica (max_loras >= 2)    |
                       | Audit: {tenant, adapter_sha, base_rev}        |
                       +----------------------------------------------+
```

**Trade-Off Matrix:**

| Axis | A1: Shared vLLM multi-LoRA + RAG (recommended) | A2: Merged 8B per tenant | A3: Prompt/cache only (no FT) |
|---|---|---|---|
| **Cost** | Train $3-$9/tenant-version; one GPU; Fireworks 100-addon cap may force second dedicated | 8B copy per tenant; rollback = full files; Hu: 100 adapters ~354 GB vs ~35 TB full | Zero train; long few-shot every request |
| **Latency** | Merged=0 extra; unmerged Punica +2 ms/token; default max_loras=1 -> TTFT spikes | Best decode (merged) | Prompt-cache wins if examples fit |
| **Ops** | max_lora_rank restart class; canary on same replica | N merged artifacts; no hot-swap | No registry; policy changes in prompt |
| **Security** | Logical LoRA isolation; LoRA-Leak if base+adapter queryable; dedicated for PHI | Full weights contain train data | No tenant weights to leak; RAG ACL still needed |
| **Scalability** | Fireworks 100 addons; S-LoRA research 2,000/GPU; overflow = more replicas | Storage/ops wall | Window + cache TTL |

**Decision**: A1 wins for schema/persona lock-in with changing facts: LoRA is the form, RAG is the catalog, prompt is the weekly policy. A3 wins while few-shot still hits exact-match JSON -- measure before you mint adapters. Do not put SKUs in SFT targets.

---

### Scenario 4: Legal / Professional Services -- Jurisdiction SFT + Case-Law RAG

**Problem Statement**: AmLaw / Big Four copilot. Answers must follow IRAC/memo structure and jurisdiction-specific captioning, cite only from the provided record, never treat model as case-law database. Privilege + PII in matter files. Hot-swap tone (litigation vs advisory). Eval must hold out real matters (not public bar-exam sets).

**Proposed Architecture:**

```
  +-------------+   +---------------------------------------------+
  | Matter ACL  |-->| CONTROL: PII/privilege redact -> SFT JSONL    |
  | + DLP gate  |   |   Completions = structure, not case holdings   |
  +-------------+   |   Optional CPT/Nova Forge if legal perplexity  |
                    |   high (full / high-rank -- not LoRA CPT)      |
                    |   then SFT LoRA r=32-256, alpha=2r, all-linear |
                    |   DPO/ORPO only on tone pairs, not holdings    |
                    +------------------+----------------------------+
                                       v
                    +----------------------------------------------+
                    | SERVE: merge firm-wide model  OR              |
                    |   unmerged LoRA per practice group            |
                    | RAG over the record (ACL + recency)           |
                    | Citation subset of retrieved chunk_ids        |
                    +------------------+---------------------------+
                                       v
                    +----------------------------------------------+
                    | Eval: held-out matters + forgetting + safety   |
                    | LoRA-Leak: do not publish adapters             |
                    | License: Llama 700M MAU + "Llama..." naming   |
                    | Fallback: adapter -> base -> extractive quotes |
                    +----------------------------------------------+
```

**Trade-Off Matrix:**

| Axis | B1: SFT/LoRA for form + RAG for record (recommended) | B2: CPT full-FT on unlabeled opinions then SFT | B3: Hosted RFT ($100/h o4-mini) |
|---|---|---|---|
| **Cost** | 6M Flash-Lite $9 or Together 8B LoRA $4 min; RAG is the ongoing bill | NVIDIA-class 18,432 H100-h; Nova Forge = subscription | $100/h core loop; Azure $5k cap; snapshot dies 2026-10-23 |
| **Latency** | Merged firm model: base-class decode; RAG retrieve+rerank dominates e2e | Same serve as any merged 70B | Hosted FT inference + RAG |
| **Ops** | Two systems (FT registry + RAG index); eval four gates | FSDP2/ZeRO-3; checkpoint-to-object-store; ZeRO-3 state_dict placeholders | Vendor job API; cannot retrain after OpenAI cutoff |
| **Security** | Redact before JSONL; no identifiers in targets; adapters unpublished (MIA 0.775); RAG ACL | Full weights memorize opinions; erasure = retrain; Llama license | Provider sees JSONL; hosted id only |
| **Scalability** | Practice-group LoRAs on one vLLM; merge when rank/kernel pressure | 70B+ training cluster; Biderman: use full/high-rank for CPT | Single vendor snapshot; Evals dashboard gone 2026-11-30 |

**Decision**: B1 wins: FT teaches how to write on the record; RAG is the record; RFT graders check format/grounding. B2 justified only when tokenizer does not speak the domain and you can fund DAPT + forgetting slice. B3 fails on lock-in and 2026-10-23 o4-mini FT death date.

---

## Key Numbers to Memorize

### Methods / Quality

| Number | What |
|---|---|
| **~13k / ~33k / ~31k** | InstructGPT SFT / RM / PPO prompt counts |
| **16 epochs; 1 epoch val-NLL overfit** | InstructGPT SFT; pick by RM score not val NLL |
| **85 +/- 3% / 71 +/- 4%** | 175B InstructGPT vs 175B GPT-3 / vs few-shot 175B |
| **1.3B preferred to 175B GPT-3** | InstructGPT PPO-ptx |
| **1,000 pairs; ~750k tok; 43% vs GPT-4** | LIMA; 88% met req; 50% excellent |
| **45.2% -> 76.1%** | LIMA 30 dialogue chains, multi-turn "excellent" |
| **29.79% -> 64.69%** | NEFTune AlpacaEval LLaMA-2-7B + Alpaca |
| **99.3%; tau=0.43** | Guanaco-65B Vicuna-of-ChatGPT; human vs GPT-4 rank agreement |
| **9,209** | OASST1 top-replies (Guanaco SFT data) |
| **beta=0.02 / gamma=27.8** | InstructGPT PPO KL / PPO-ptx pretrain mix scale |
| **beta=0.1** | LlamaFactory / TRL DPO (and ORPO beta = paper lambda) default |
| **12.20% / 66.19% / 7.32** | Mistral-ORPO-beta AlpacaEval 2.0 / IFEval loose / MT-Bench |
| **11.33 vs 8.35** | Mistral-ORPO-alpha vs Zephyr-alpha AlpacaEval 2.0 |
| **GSM8K 40.0 -> 53.5** | KTO vs DPO on Zephyr-beta-SFT UltraFeedback |
| **44.7 / 40.3 / 26.0** | Llama3-Instruct SimPO / DPO / SFT AlpacaEval 2 LC |
| **G=64; LR 1e-6; KL 0.04** | DeepSeekMath GRPO |
| **82.9% -> 88.2%; 46.8% -> 51.7%** | DeepSeekMath-RL GSM8K / MATH vs Instruct |
| **SimPO beta=2-10; LR 3e-7 to 1e-6** | Not DPO's 0.1 / not SFT's 1e-5 |

### LoRA / QLoRA / Serve

| Number | What |
|---|---|
| **10,000x; 1.2 TB -> 350 GB; 35 MB** | LoRA GPT-3 175B params / VRAM / r=4 Q/V adapter |
| **96 vs 24 V100s; ~25% speedup** | Full FT vs LoRA training (Hu) |
| **r=8, alpha=8** | PEFT / LlamaFactory defaults -- not Biderman's recipe |
| **r=32** | Databricks: most customers need this to avoid quality drop |
| **r=256, alpha=2r=512** | Biderman IFT (code); 16-64 often fail on code |
| **LR [1e-5, 5e-4]** | Biderman IFT LoRA sweep |
| **2e-4 / 1e-4** | QLoRA 7B/13B vs 33B/65B LR |
| **<48 GB; 41 GB; 24 h** | QLoRA 65B fit / Guanaco 4-bit footprint / wall clock |
| **53.1 vs 53.0 vs 52.2** | QLoRA MMLU mean NF4+DQ / bf16 / FP4 |
| **62.2 / 62.5 vs 63.4** | 65B MMLU Guanaco/Alpaca vs base (chat data can drop MMLU) |
| **+3.7 / +2.9 / +4.4** | DoRA commonsense vs LoRA LLaMA-7B / LLaMA2-7B / LLaMA3-8B |
| **0 extra ms merged; +2 ms/token Punica; 12x** | Serve overhead / multi-tenant throughput (papers) |
| **max_loras=1; max_lora_rank=16** | vLLM defaults; rank 256 rejects |
| **100 addons** | Fireworks dedicated cap |
| **7.61 req/s @ 2000 adapters** | S-LoRA S1 (2023 research) |
| **171 OTPS / 124 ms TTFT** | vLLM 0.15 Amazon-tuned GPT-OSS 20B (blog) |
| **70B on 2x24 GB** | Axolotl FSDP+QLoRA |
| **AUC 0.775** | LoRA-Leak MIA (public base as reference) |
| **0.373 bits/param; ~3 GB on 65B** | QLoRA double quantization savings |

### Cost / Dates / NFR

| Number | What |
|---|---|
| **$100/h; $80/h** | OpenAI/Azure o4-mini RFT; Bedrock gpt-oss-20b RFT |
| **$5,000** | Azure RFT per-job cap then uncapped resume |
| **$1.70/h; $1,224/mo; $14,892/yr** | Azure Standard FT host |
| **$4.00 / $1.00 / $16.00** | o4-mini FT in / cached / out per 1M |
| **[inferred] $9.60 / $4.80 /1k** | 800/400 o4-mini FT uncached / data-share |
| **[inferred] $0.36 /1k** | 800/400 Gemini 2.0 Flash tuned-at-base rates |
| **1.5x** | Gemini 3+ tuned inference vs base |
| **$0.48 / $0.50** | Together / Fireworks SFT LoRA <=16B per 1M |
| **~10-12% vs 2x** | Together vs Fireworks DPO LoRA surcharge on SFT LoRA |
| **$4 min** | Together per job (some exempt) |
| **$9 / $60 / $150** | Vertex 6M tok Flash-Lite / 3.5 Flash / 2.5 Pro [inferred] |
| **$2.18 + $1.75/mo** | Bedrock Nova Micro blog example |
| **$1.49 / $7.99 per 1M** | Bedrock Llama 2 13B / 70B FT train |
| **$23.50/h ~ $17k/mo** | Llama 2 70B no-commit PTU idle |
| **$300/mo saved [inferred]** | Drop 2k few-shot tok x 1M req at Flash $0.15/1M |
| **2026-05-07 / 2026-07-02 / 2027-01-06** | OpenAI FT: no new orgs / 60-day idle cutoff / all new-job creation ends |
| **2026-10-23** | ft-o4-mini inference shutdown |
| **2026-11-30** | OpenAI Evals platform shutdown |
| **+10%** | OpenAI regional processing uplift |
| **131,072; 1 GB; 10M / 300k** | Vertex per-example tok; JSONL max; train text / multimodal max |
| **Adapter 1-16 (Pro max 8)** | Vertex LoRA-rank analogue |
| **700M MAU; 100M MAU-class** | Llama Community License; some Qwen custom rows |
| **128xH100 x 144 h = 18,432** | NVIDIA 70B DAPT GPU-hours [inferred] |
| **Azure data guidance** | Hard minimum 10 examples; practical start ~50; serious runs hundreds-thousands |
| **Azure batch_size = -1** | ~0.2% of training examples, max 256 |
| **Azure LR multiplier** | ~0.02 to 0.2 |
| **73%** | Enterprise FT projects failing due to data quality (not model/HP choices) |
| **$4.4M** | Average breach cost (IBM 2025) |
| **97%** | Organizations with ML incidents lacking proper access controls |

### Key Takeaways (One-Liner Reference)

- Fine-tuning is **two planes sharing versioned artifacts**, not `train()` then `chat()`.
- **Prompt / RAG first.** FT is for a stable distribution shift (schema, persona, domain language).
- **LoRA is not "full FT but cheaper."** Biderman: low-rank underperforms; CPT gap persists; IFT at r=256, alpha=2r can match. LoRA forgets less.
- **Budget hosting, not the $9 job.** Azure $1.70/h = $1,224/mo idle.
- **Eval four ways before promote:** task holdout, forgetting slice, safety, serve-dtype.
- **Serve path:** merge for one model (zero latency); unmerged for tenants. Fallback adapter -> base -> prompt/RAG -> deterministic.
- **Weights are derived personal data.** PII filter before JSONL; LoRA-Leak AUC 0.775; erasure = retrain; Llama license inherits onto the LoRA.

---

## Sources

- InstructGPT (Ouyang et al., 2022)
- LIMA (Zhou et al., NeurIPS 2023)
- LoRA (Hu et al., 2021)
- QLoRA (Dettmers et al., 2023)
- DoRA (Liu et al., ICML 2024)
- DPO (Rafailov et al., NeurIPS 2023)
- ORPO (Hong, Lee, Thorne, EMNLP 2024)
- KTO (Ethayarajh et al., ICML 2024)
- SimPO (Meng, Xia, Chen, NeurIPS 2024)
- GRPO (Shao et al., DeepSeekMath 2024; DeepSeek-R1 2025)
- GSPO (Zheng et al., Qwen 2025)
- NEFTune (Jain et al., 2023)
- Biderman "Illusion of Equivalence" (2025-2026)
- LoRA-Leak (2025)
- Punica (MLSys 2024)
- S-LoRA (2023)
- OpenAI Fine-Tuning / DPO / RFT Docs
- Microsoft Foundry / Azure Fine-Tuning Docs
- HF PEFT / TRL Docs
- Vertex / Gemini Tuning Docs
- AWS Bedrock Custom Models Docs
- Together / Fireworks Training Pricing
