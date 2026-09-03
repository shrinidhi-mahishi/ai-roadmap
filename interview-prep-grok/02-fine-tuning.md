# Module 02: Fine-Tuning (SFT / PEFT / Preference / RLVR)

**Study + interview prep.** Grounded in research dated 2026-09-02 (105 sources). Prices, ranks, learning rates, GPU-hours, and eval deltas are vendor docs / papers / named blogs as of that date. `$ per 1k queries` and `$ per 1k runs` figures that multiply published rates by a stated reference query are **[inferred]**, not a vendor SKU. Public pages do **not** publish a global p50/p95/p99 SLO for fine-tuned serving — missing percentiles are marked.

---

## What Is This?

A base LLM is a generalist chef who has read every cookbook on earth. **Fine-tuning** does not hand that chef a new recipe at dinner (that is prompting / RAG). It **retrains a slice of the weights** so the chef’s default muscle memory *is* your house style: the JSON schema, the IRAC memo, the “never invent a citation” habit, the department’s jargon.

The 2022–2026 post-training stack is modular:

1. **(Optional) continued pretraining / DAPT** — next-token prediction on unlabeled domain text so the tokenizer/model *speaks* the domain.
2. **SFT / instruction tuning** — supervised `(prompt, completion)` or chat-messages JSONL.
3. **Preference optimization** — DPO / ORPO / KTO / SimPO on chosen/rejected (or binary) labels, **or** classical RLHF (reward model + PPO).
4. **RL with verifiable rewards (RLVR)** — GRPO / GSPO / hosted RFT against a programmatic grader (unit test, boxed math, schema).

**PEFT (LoRA and friends)** is the production default: freeze the base \(W_0\), train a tiny adapter \(BA\) so you store megabytes per tenant instead of a full copy. **Full fine-tune** moves every weight — highest capacity, worst forgetting, worst storage.

Think of a restaurant chain. **Training** is the test kitchen (write / control plane): ingest recipes, redact allergens, run a job, taste against a holdout, promote or dump the batch. **Serving** is the dining room (read / data plane): load the house sauce (adapter) onto the same stove (base weights), route table → sauce, plate, log. If you couple those jobs, a hung GPU blocks dinner service, and a bad promote ships unreviewed food.

## Why It Matters

Interviews test whether you know **when not to fine-tune**. OpenAI is winding down self-serve FT (no new jobs for remaining customers on **2027-01-06**; `ft-o4-mini-2025-04-16` inference dies **2026-10-23**). Anthropic has **no** first-party FT API. Newer bases + prompt cache + RAG absorb most “we should fine-tune” tickets.

When FT *is* the right lever — stable schema, persona, tool-call JSON, domain language the base does not speak — you must split **control vs data plane**, gate promote on **task + forgetting + safety + serve-dtype**, serve **unmerged LoRA** for hot-swap or **merge** for decode throughput, and budget **hosting minutes**, not the $9 training job. A Principal answer names rank/α, DPO vs GRPO, adapter routing, and the fallback **adapter → base → prompt/RAG**.

---

### 1. System Topology & Data Flow

A production fine-tuning system is **two independently scaled planes sharing artifacts** (base checkpoint + adapter or merged weights + dataset version + eval report), not a single “train then chat” function.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  W&B / MLflow: loss, LR, eval  │  job meters (tok×epochs, GPU-h) │
         │  holdout + forgetting + safety │  serve: TTFT, OTPS, adapter hit │
         │  WORM audit: who/hash/promote  │  lineage: dataset→job→adapter   │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ metrics           │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (write — jobs, gates, pointers; not token decode)          │
│                                                                           │
│  ┌──────────┐ ┌─────────────┐ ┌──────────────┐ ┌────────────┐ ┌────────┐ │
│  │ IdP/IAM  │ │ Dataset     │ │ Job API      │ │ Eval gate  │ │ Registry│ │
│  │ actor +  │ │ versioning  │ │ SFT/DPO/RFT  │ │ block      │ │ promote │ │
│  │ tenant   │ │ hash/PII    │ │ rank/α/LR/ep │ │ promote    │ │ rollback│ │
│  └────┬─────┘ └──────┬──────┘ └──────┬───────┘ └─────┬──────┘ └───┬────┘ │
│       │              │               │               │            │      │
└───────┼──────────────┼───────────────┼───────────────┼────────────┼──────┘
        │              │               │               │            │
        ▼              ▼               ▼               ▼            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (train write vs serve read — independently scaled)            │
│                                                                           │
│  TRAIN (write): JSONL/parquet → PII DLP → split → distributed runtime     │
│                 (FSDP2 / ZeRO / QLoRA) → checkpoints → eval artifact      │
│                                                                           │
│  SERVE (read):  load base W + LoRA_t (or merged) → route tenant→adapter   │
│                 → batch/quantize → log {tenant, adapter_sha, base_rev}    │
│                                                                           │
│  ┌────────────── TOOL PROXIES (MCP / vendor APIs — least privilege) ───┐  │
│  │ submit_sft_job │ submit_dpo_job │ submit_rft_job (grader)           │  │
│  │ load_lora      │ generate_ft    │ generate_base │ retrieve_kb (RAG) │  │
│  │ Identity from verified token / RunContext — NEVER from model JSON   │  │
│  │ NO omnibus train(dataset_uri, tenant_id) the model can fill         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (artifacts the two planes share; query pins a rev)     │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐  │
│  │ Dataset    │ │ Checkpoints│ │ Adapters   │ │ Merged     │ │ Eval    │  │
│  │ hash +     │ │ every N    │ │ LoRA files │ │ GGUF/AWQ   │ │ reports │  │
│  │ PII report │ │ steps/epoch│ │ (tens of   │ │ (rollback= │ │ task +  │  │
│  │ train/val/ │ │ object     │ │ MB) + PEFT │ │ full ckpt) │ │ forget  │  │
│  │ test split │ │ store      │ │ config JSON│ │            │ │ safety  │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └─────────┘  │
│  Registry pointer: adapter_id | merged_id | vendor model id               │
│  Lineage: base@rev + tokenizer/chat_template + seed + code SHA            │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Training (write / control)** | Dataset ingest, PII redaction, split, job config (method, rank, LR, epochs), distributed runtime, checkpoints, eval gates, promote/rollback | JSONL/parquet pipelines, Axolotl / LlamaFactory / Unsloth / TRL jobs, FSDP2 / DeepSpeed, W&B / MLflow, model registry | A hung GPU job blocks serving; a bad promote ships an unreviewed checkpoint |
| **Serving (read / data)** | Load base + adapter(s) or merged weights, route tenant → adapter, batch, quantize, log | vLLM `--enable-lora`, Fireworks multi-LoRA (up to **100** addons), SageMaker / Bedrock custom endpoints, merged GGUF / AWQ | Training dtype/quant silently mismatches serve dtype; adapter cache miss dominates TTFT |

**Vendor control planes encode the same split:**

- **OpenAI:** upload JSONL → `/v1/fine_tuning/jobs` (SFT, vision SFT, DPO, or RFT) → evaluate → call the resulting model id. New orgs cannot create jobs since **2026-05-07**; orgs with no fine-tuned inference in 60 days lost job creation **2026-07-02**; **all remaining customers lose new-job creation on 2027-01-06**. Inference on already-tuned models continues until the **base snapshot is deprecated**. `ft-o4-mini-2025-04-16` (the only public RFT snapshot) shuts down **2026-10-23** — a harder deadline than SFT job creation.
- **Vertex / Gemini Enterprise Agent Platform:** regional `tuningJobs.create` (`us-central1`, `europe-west4` for current Gemini 3.x Flash/Pro). User data stored in the **tuning-job region**; compute may offload to other US/EU accelerator regions. Tuned serving: `us` / `eu` multi-region only for Gemini 3.5 Flash / 3.1 Flash-Lite. **Supervised fine-tuning is not a Covered Service and is excluded from any SLA.** CMEK **not supported** for listed Flash models.
- **Bedrock:** `CreateModelCustomizationJob` with `customizationType` ∈ {`FINE_TUNING`, `CONTINUED_PRE_TRAINING`, `DISTILLATION`, and API-surface `REINFORCEMENT_FINE_TUNING` / `IMPORTED`}. Train JSONL (or invocation-log harvest) on S3; metrics to `outputDataConfig.s3Uri`. Optional `customModelKmsKeyId`, VPC `subnetIds` / `securityGroupIds`, `jobTags` + `customModelTags`.

**Request-flow narrative (training job → eval gate → adapter serve):**

1. **Ingest / control.** Actor (IAM / Azure AD) uploads JSONL. Pipeline hashes the file, runs PII detection → redaction → audit, then **splits train/val/test before upload** (OpenAI best practice; InstructGPT used **user-ID disjoint** splits). Record `dataset_hash + tokenizer/chat_template + base_model_id@revision + peft_config + seed + code SHA`.
2. **Job submit (control + tool proxy).** Idempotency key = that lineage tuple. Config: method (SFT / DPO / ORPO / GRPO / RFT), rank \(r\), \(\alpha\), LR, epochs. Vertex: adapter size **1, 2, 4, 8, 16** (Gemini 2.5 Pro max **8**); thinking budget **0** (≤2.5) or **MINIMAL** (≥3) during SFT — tuned models omit thinking in the data, so extra thinking at serve is wasted spend. OpenAI/Azure: **no charge** for queue, failed jobs, cancel-before-train, or safety checks. Together: failed jobs **fully refunded**; cancel bills **completed steps**.
3. **Train (data plane, write).** Axolotl/TRL/Unsloth/LlamaFactory or vendor job API. Checkpoints every \(N\) steps/epoch to object store (OpenAI: **epoch checkpoints** so an overfit last epoch is not the only artifact). FSDP2 or ZeRO-3 for full FT 70B+; QLoRA + paged Adam to fit 65B in **<48 GB**.
4. **Eval gate (control) — block promote until all four pass.** (1) Task holdout the job never saw. (2) **Forgetting slice** (Biderman: mean of HellaSwag, WinoGrande, ARC-Challenge — or a frozen production golden set of *pre-tune* behaviors). (3) Safety / refusal / jailbreak, especially after DPO/RL. (4) **Serving-parity**: same dtype/quant as prod (Unsloth). Plus contamination audit (DICE; GSM1k vs GSM8K). Vertex Gen AI evaluation during tuning is Preview, charged as **batch prediction**. OpenAI **Evals platform** is deprecated (announced 2026-06-03, read-only 2026-10-31, shutdown **2026-11-30**) — gates must live **outside** that dashboard.
5. **Registry promote.** Pointer flip: `adapter_id` (unmerged) or `merged_id` / vendor model id. Canary **1–5%** of tenants (or sticky `canary=true`) to the new adapter **on the same vLLM replica** (`max_loras ≥ 2`) — not a second GPU cluster. Rollback = previous pointer (adapters are tens of MB; Hu et al. **35 MB** for GPT-3 \(r=4\) on \(W_q,W_v\)).
6. **Serve (data plane, read).** Gateway: `tenant_id → adapter_id` from verified token. vLLM: request `model` field selects LoRA; mixed LoRA + base traffic in one scheduler. Fireworks: up to **100** addons on one base deployment; trained LoRAs need a **dedicated** deployment (not serverless custom-LoRA on the shared pool). Merged path: one weight file, best decode, rollback = load previous full checkpoint. Telemetry: `{tenant, adapter_sha, base_rev, job_id}` — not the training JSONL in shared traces.

**Open-weight control-plane software (what actually runs the job):**

| Stack | Role | Documented facts |
| --- | --- | --- |
| **HF PEFT** | Adapter injection | LoRA / DoRA / rsLoRA / LoftQ; `merge_and_unload()` is **not in-place** |
| **HF TRL** | Post-training trainers | `SFTTrainer`, `DPOTrainer`, `GRPOTrainer` (stable, vLLM rollouts); `ORPOTrainer` / `PPOTrainer` experimental. TRL v1 announced **2026-03-27** |
| **Unsloth** | Kernel-optimized LoRA/QLoRA/FFT/RL | Vendor: up to **2×** faster, **70–80%** less VRAM vs vanilla HF; currently requires `lora_dropout=0`, `bias="none"` for fused kernels |
| **Axolotl** | YAML over Transformers/PEFT/TRL/Accelerate | **FSDP2** recommended, FSDP1 deprecated; FSDP+QLoRA: **70B on two 24 GB GPUs**; DeviceMesh: `dp_shard_size`, `tensor_parallel_size`, `context_parallel_size`, `expert_parallel_size` |
| **LlamaFactory** | CLI/UI, 100+ models | `stage`: `pt` / `sft` / `rm` / `ppo` / `dpo` / `kto` / `orpo` / `simpo`; default `lora_rank: 8`, `lora_target: all`; **do not merge a quantized base** |

MarkTechPost 2026-07-22: Unsloth wins single-GPU; Axolotl wins multi-GPU N-D; LlamaFactory wins UI/breadth.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariant: FT is a distribution shift, not a document store

**Invariant I1.** Fine-tuning changes the **parametric** policy. It does not give you citation, ACL filters, or instant unpublish. Private / large / time-varying knowledge stays in **RAG**. LIMA/InstructGPT-style alignment teaches **format**; Knowledge-Instruct (2025) exists because **CPT on ~1M-token** corpora is data-inefficient.

**Invariant I2.** Pin `base_model_id@revision + tokenizer/chat_template + peft_config + adapter_sha + serve_dtype`. Changing any is a new eval, often a new job. Train-in-QLoRA-4-bit then merge into 4-bit without dequant-merge-requant **collapses quality** (transformers#31293). Unsloth: **train and serve in the same precision**.

**Invariant I3.** Promote is a **pointer**, gated on holdout + forgetting + safety + serve-dtype. Loss going down is not a ship signal (LIMA picked checkpoints on a **50**-example dev set because perplexity did not track generation quality; InstructGPT SFT **overfits val NLL after 1 epoch** but more epochs still improved RM score — they pick by **RM score**, not val NLL).

#### 2.2 When to FT vs RAG vs prompt

| Need | First lever | Why |
| --- | --- | --- |
| Behavior specifiable in text; examples fit the window; base already follows instructions | **Prompt / few-shot / cache** | OpenAI’s wind-down rationale: newer bases reduce the need for self-serve FT. Anthropic’s public path is prompt + cache + (gated) Bedrock Haiku FT, not a Claude FT API |
| Knowledge private, large, or time-varying | **RAG** | FT is a bad document store; no citation / ACL / unpublish. Hybrid: FT for schema/persona, RAG for facts — two bills |
| Stable distribution shift: schema, tool-call JSON, persona, department language, “never do X” that few-shot still misses | **SFT** | Vertex examples: classification to a **single class token**, PII-stripped summary format, extractive span QA, shopkeeper persona. OpenAI: classification, format, instruction-following failures |
| Tokenizer/model does not speak the domain (jargon, code dialect, legal French) | **CPT then SFT** | NVIDIA Llama 3.1 70B: DAPT on **17M** papers **then** **250,000** synthetic instructions. Biderman: **do not** expect LoRA CPT to match full-FT CPT. Nova Forge mid-train = conservative-LR domain absorption |
| Pairwise or binary preferences; no PPO stack | **DPO / ORPO / SimPO / KTO** | DPO if clean pairs + ref in memory; SimPO if reference-free + length-norm (watch LR); ORPO if **one** SFT+preference stage; KTO if thumbs / logs rather than pairs |
| Machine checker exists (unit tests, MATH boxed answers, schema validators) | **GRPO / RFT**; **GSPO** on MoE / long rollouts | DeepSeekMath \(G=64\); OpenAI RFT time-based **$100/h** core loop; Bedrock RFT = prompts + Lambda reward |

**Hybrid (most enterprises):** RAG for facts + SFT/LoRA for form + prompt for policy that must change this week. Fine-tune does **not** remove retrieval ACLs.

#### 2.3 SFT (InstructGPT step 1; LIMA)

Ouyang et al. (InstructGPT, 2022): SFT on **~13k** demonstration prompts → RM on **~33k** ranked comparison prompts → PPO on **~31k** unlabeled API prompts. Labelers: **~40** contractors. Prompts: Playground traffic (not production API), PII-filtered, user-ID disjoint, max **200** prompts/user, context **2k** (drop prompts **>1k**, cap responses **1k**). SFT: **16 epochs**, residual dropout **0.2**, cosine LR to **10%** of peak, **no warmup**. Peak LR / batch: 1.3B and 6B **9.65e-6 / 32**; 175B **5.03e-6 / 8**. The **1.3B** InstructGPT (PPO-ptx) was **preferred to 175B GPT-3**; 175B InstructGPT preferred to 175B GPT-3 **85 ± 3%** of the time and to few-shot 175B GPT-3 **71 ± 4%**.

Zhou et al. (LIMA, NeurIPS 2023): LLaMA-65B SFT on **exactly 1,000** curated pairs totaling **~750,000 tokens**. Mix: SE STEM **200**, SE other **200**, wikiHow **200**, r/WritingPrompts **150**, Natural Instructions **50**, author-written **200**. AdamW \(\beta_1=0.9\), \(\beta_2=0.95\), WD **0.1**, LR **1e-5 → 1e-6** linear, **15 epochs**, batch **32**, trim **2048**. Vs GPT-4: win **18%** / tie **25%** / lose **57%** (equivalent-or-better **43%**). Absolute: **88%** met requirements; **50%** excellent. **30** hand-written dialogue chains raised “excellent” multi-turn **45.2% → 76.1%** and cut failures **15/42 → 1/46** turns. Superficial Alignment Hypothesis: almost all knowledge is in pretraining; limited instruction data teaches **format**. Scaling **quantity** without diversity has diminishing returns.

NEFTune (Jain et al., 2023): uniform noise on token embeddings scaled by \(\alpha / \sqrt{L \cdot d}\). LLaMA-2-7B + Alpaca: AlpacaEval **29.79% → 64.69%**. TRL: `neftune_noise_alpha`.

**Quality > volume (bounded):** LIMA 1,000 curated vs folklore that you need 50k+ noisy pairs for style. QLoRA Guanaco on **9,209** OASST1 top-replies: Vicuna GPT-4-as-judge **99.3%** of ChatGPT — and the same paper warns chatbot benches are **not trustworthy** (Kendall \(\tau=0.43\), Spearman \(r=0.55\) vs humans).

OpenAI epoch heuristic: under-follows → **+1–2 epochs**; loses diversity → **−1–2 epochs**; fails to converge → raise LR multiplier. Unsloth: many tasks look healthy around loss **0.5–1.0**; loss **→ 0** is overfitting; if loss is flat, change LR/data, not “more FFT.”

#### 2.4 LoRA / QLoRA / DoRA / rsLoRA

**LoRA (Hu et al., 2021).** Frozen \(W_0 \in \mathbb{R}^{d \times k}\); trainable \(B \in \mathbb{R}^{d \times r}\), \(A \in \mathbb{R}^{r \times k}\), \(r \ll \min(d,k)\):

\[
h = W_0 x + \frac{\alpha}{r} B A x
\]

Default init: Kaiming-uniform \(A\), \(B=0\) so \(\Delta W=0\) at step 0 (identity). **rsLoRA** (Kalajdzievski 2023): scale \(\alpha/\sqrt{r}\) instead of \(\alpha/r\) (`use_rslora=True`). PEFT defaults: `r=8`, `lora_alpha=8`, `lora_dropout=0.0`, `bias='none'`.

Trainable count per linear: \(r(d+k)\) vs \(dk\). Biderman’s example: \(r=16\) on a \(4096\times4096\) matrix trains **<1%** of that matrix. Original LoRA targeted \(W_q,W_v\) only; current practice (QLoRA, Databricks, Biderman, Unsloth) targets **all** of \(\{W_q,W_k,W_v,W_o,W_{\mathrm{gate}},W_{\mathrm{up}},W_{\mathrm{down}}\}\).

GPT-3 175B numbers they state: trainable params **10,000×** down; VRAM **1.2 TB → 350 GB**; \(r=4\) on \(W_q,W_v\): checkpoint **350 GB → 35 MB**. Storing 100 adapted models: **~354 GB** vs **~35 TB** of full copies. Training: **96 V100s** full FT vs **24 V100s** LoRA; ~**25%** training speedup from skipping frozen grads. **Merged LoRA: no extra inference latency.** Unmerged: swap megabyte adapters. Limitation: batching **different** adapters in one forward is not straightforward once \(BA\) is absorbed into \(W\).

**Rank / α (the interview knob).**

| Claim | Bound |
| --- | --- |
| LoRA ≈ full FT “always” | **False** on Biderman code/math Llama-2-7B: low-rank LoRA **underperforms** full FT; CPT gap **not closed even at high rank**; IFT high ranks **can match** full FT |
| Folklore \(r=8\) | LlamaFactory default; often underfits code. Databricks: **rank 32** necessary for most customers to avoid quality drop (then pressures inference kernels). Biderman IFT: **\(r=256\)** all modules; 16–64 often **fail on code** |
| \(\alpha = r\) (PEFT default 8/8) | “Illusion of Equivalence”: \(\alpha=8\) produces **intruder dimensions** and worse forgetting than \(\alpha=2r\) |
| Biderman recipe | LoRA for **IFT not CPT**; all transformer modules at **\(r=256\)** if memory allows; **\(\alpha = 2r\)** (at \(r=256\), \(\alpha=512\) beat 256 and 32 across LRs); sweep LR **\([1\mathrm{e}{-5}, 5\mathrm{e}{-4}]\)** |
| Together | LoRA default; trains **0.1%–1%** of full-FT params; some MoE / long-context / VLM models are **LoRA-only** |

**Complexity.** Forward extra cost is two thin matmuls \(O(r \cdot (d+k))\) vs \(O(dk)\). Memory: optimizer states only on \(A,B\) (plus 4-bit base for QLoRA). Serving unmerged: vLLM pre-allocates `max_loras × max_lora_rank × hidden` at start — **not** per-adapter actual rank. Rank > `max_lora_rank` is a **hard reject** (restart). Oversize `max_lora_rank` wastes VRAM and can hurt kernels. Biderman \(r=256\) **will not load** on vLLM’s default `max_lora_rank=16`.

**QLoRA (Dettmers et al., 2023).** Frozen base in **NF4** + **double quantization** of constants (~0.5 → ~0.2 bits/param metadata) + **paged optimizers**. Adapters stay 16-bit; backprop through 4-bit base. Claim: 65B on a **single 48 GB GPU**; they reduce >**780 GB** (their 16-bit full-FT figure) to **<48 GB**. Guanaco-65B: **24 hours** on one professional GPU; 4-bit footprint **41 GB**; OASST1 top-replies **9,209** examples. HPs (Table 9): 7B/13B LR **2e-4**, 33B/65B LR **1e-4**; **1875** steps, batch **16**, target length **512**. MMLU NF4+DQ mean **53.1** vs bf16 **53.0** vs FP4 **52.2**. They **did not** claim 16-bit full-FT parity at 33B/65B.

QLoRA 5-shot MMLU (LLaMA): FLAN v2 **raises** MMLU (similar distribution); OASST1/Alpaca **chat** datasets can **drop** 65B MMLU vs base (**62.2 / 62.5 vs 63.4**) — eval-set mismatch, not “QLoRA destroys MMLU.”

**DoRA (Liu et al., ICML 2024).** \(W = m \cdot (V / \|V\|_c)\); LoRA updates **direction**; magnitude \(m\) is a learned vector (**\(d\)** extra params/layer). No extra inference cost after merge. Commonsense vs LoRA: LLaMA-7B/13B **+3.7 / +1.0**, LLaMA2-7B **+2.9**, LLaMA3-8B **+4.4**.

**PiSSA / LoftQ.** PiSSA: init \(A,B\) from top singular components of \(W\) (not a no-op). LoftQ: alternate quantization and low-rank approx so \(Q+BA \approx W\) **before** QLoRA; PEFT can **roll back** a layer if error did not drop. LoftQ paper also reports cases that **do not** beat QLoRA — treat as an init, not a guarantee.

**Merge.** `merge_and_unload()` must be assigned. `safe_merge=True` checks NaNs. **Do not** merge a quantized base. Path: merge into **bf16/fp16**, then re-quantize AWQ/GGUF; eval the **exported** artifact. PEFT on MoE **experts**: unmerged LoRA materializes every expert’s adapter even when few fire under KV-cache decode — **merge for MoE serving**.

#### 2.5 Preference and RL methods

**PPO-RLHF (InstructGPT steps 2–3).** SFT policy → RM on pairwise rankings → PPO with **per-token KL against the SFT model**. Labelers ranked \(K=4\)–\(9\) completions; all \(\binom{K}{2}\) pairs from one prompt are **one batch element**. RM: **one 6B** model for PPO of **all** policy sizes (1.3B/6B/175B). Bradley–Terry:

\[
\mathcal{L}(\theta)=-\mathbb{E}\big[\log\sigma(r_\theta(x,y_w)-r_\theta(x,y_l))\big]
\]

Then **bias-shift** so demonstrations have mean score **0**. Held-out labeler-group accuracy **69.6 ± 0.9%** vs in-group **72.4 ± 0.4%**. PPO: **256k** episodes, ~**31k** unique prompts, batch **512** / minibatch **64**, KL \(\beta=0.02\), clip **0.2**. PPO-ptx mixes **8×** pretraining examples and scales those grads by \(\gamma=27.8\) (\(\gamma\geq 20\) recovered SQuADv2/DROP on 1.3B). Raising \(\beta\) to **2.0** with \(\gamma=0\) **did not** fix those regressions and crushed validation reward. Gao et al.: as KL from SFT grows, proxy RM keeps rising while **gold reward peaks then falls** (reward hacking).

**DPO (Rafailov et al., NeurIPS 2023).** Implicit reward \(r(x,y)=\beta\log(\pi_\theta/\pi_{\mathrm{ref}})\). Pairwise loss; **no RM, no sampling loop, no value function**. LlamaFactory default `pref_beta` **0.1**. Together DPO LoRA is a **~12–10%** surcharge on the LoRA meter vs SFT; Fireworks DPO LoRA is **exactly 2×** SFT LoRA. OpenAI DPO documented for `gpt-4.1-2025-04-14` / mini / nano while grandfathered orgs can still create jobs.

**ORPO (Hong, Lee, Thorne, EMNLP 2024).** Monolithic SFT + odds-ratio penalty; **no reference model**, no SFT warm-up. \(\mathcal{L}_{\mathrm{ORPO}}=\mathbb{E}[\mathcal{L}_{\mathrm{SFT}}+\lambda\mathcal{L}_{\mathrm{OR}}]\). Published \(\lambda\): Phi-2 **0.25**, Llama-2-7B **0.2**, Mistral-ORPO-α **0.1**. TRL names that weight `beta` (docs: paper \(\lambda\); code historically `alpha`), default **0.1**. Mistral-ORPO-β 7B: AlpacaEval 2.0 **12.20%**, IFEval instr. loose **66.19%**, MT-Bench **7.32**. Mistral-ORPO-α exceeds Zephyr-α (SFT 200k + DPO UltraFeedback) on AlpacaEval 2.0 (**11.33 vs 8.35**). Monitor general eval **more tightly** — no KL anchor by design.

**KTO (Ethayarajh et al., ICML 2024).** Binary desirable/undesirable per \((x,y)\), not pairs. Zephyr-β-SFT + UltraFeedback, 1 epoch: GSM8K **40.0 (DPO) → 53.5 (KTO, \(\beta=0.1\))**; BBH **44.1 → 52.6**. Dropping **90%** of desirable data still beat DPO on Llama-7B. Risk-neutral \(v(\cdot)=\cdot\): BBH **collapses to 6.1**. If the pretrained model is strong enough, **KTO can skip SFT**; DPO still wants SFT.

**SimPO (Meng, Xia, Chen, NeurIPS 2024).** Reference-free; implicit reward = **length-normalized** average log-prob + margin \(\gamma\). \(\beta\) typically **2–10** (vs DPO 0.1). Llama3-Instruct SimPO **44.7** LC / **33.8** Arena-Hard vs DPO **40.3 / 32.6** vs SFT **26.0 / 22.3**. Without length-norm, Mistral-Base LC **21.5 → 11.9** (worse than DPO’s 15.1). GitHub: LR **1e-5** can produce incoherent/repetitive text; math-heavy prefer **~5e-7**. Grid **\(3\mathrm{e}{-7}\)–\(1\mathrm{e}{-6}\)**.

**GRPO (Shao et al., DeepSeekMath 2024; DeepSeek-R1 2025).** Deletes PPO’s critic. Sample group \(\{o_i\}_{i=1}^{G}\); advantage \(\hat{A}_i=(R_i-\mathrm{mean}(R))/\mathrm{std}(R)\). DeepSeekMath-RL 7B: policy LR **1e-6**, KL **0.04**, **\(G=64\)**, max length **1024**, batch **1024**. vs Instruct: GSM8K **82.9% → 88.2%**, MATH **46.8% → 51.7%**. R1-Zero skips SFT and uses **outcome** verification only. Community note: ~**1 day on 8×A100** for a small Qwen GRPO run — **[third-party / config-specific]**, not a TRL SLO. Tag-only rewards can move already-correct answers into the tag without gaining skill (`strict_tag_acc` vs `last_number` — small-n, illustrative).

**GSPO (Zheng et al., Qwen 2025).** GRPO’s **token-level** importance ratio is high-variance on long rollouts and unstable on MoE (expert routing changes between \(\pi_{\theta_{\mathrm{old}}}\) and \(\pi_\theta\)). GSPO clips a **sequence-level** geometric-mean ratio. They report GSPO clips **two orders of magnitude more tokens** than GRPO yet is **more** sample-efficient on AIME’24 / LiveCodeBench / CodeForces from Qwen3-30B-A3B-Base. GRPO on that MoE needed **Routing Replay**; GSPO **drops** it. Sequence-level likelihood is more tolerant of train/infer engine numeric mismatch (disaggregated RL).

**Method card — what you must host in VRAM during the job:**

| Method | Ref policy | Reward model | On-policy samples | Pair labels | Typical extra vs SFT |
| --- | --- | --- | --- | --- | --- |
| SFT | no | no | no | no | 1× model |
| ORPO | no | no | no | yes (SFT+penalty) | 1×; \(\lambda\) default 0.1 |
| SimPO | no | no | no | yes | 1×; \(\beta\sim 2{-}10\) |
| DPO | yes (frozen) | no (implicit) | no | yes | ~2× logits |
| KTO | yes (KL ref point) | no | no | binary ok | ~2×; unpaired OK |
| PPO-RLHF | yes (KL) | yes + value | yes | rankings → RM | 4× class (policy, ref, RM, value); InstructGPT used 6B RM for all sizes |
| GRPO | yes | optional / verifier | **G** completions/prompt | verifier or RM | no critic; \(G=64\) in DeepSeekMath |
| GSPO | yes | verifier/RM | **G** sequences | verifier | sequence IS; MoE-stable |
| OpenAI RFT | hosted | grader model | yes | grader | **$100/h** core loop |

**Forgetting (Biderman; “Illusion of Equivalence”).** Task FT moves weights along directions that also implemented prior skills. IFT forgets **more** than CPT; programming forgets **more** than math; forgetting **increases with data volume**; LoRA forgets **less** than full FT (even at **equal** task accuracy) and less than dropout/weight-decay. Mitigations: mix replay/general data (InstructGPT PPO-ptx \(\gamma=27.8\)); LoRA instead of full FT for IFT; \(\alpha=2r\); orthogonal/projected LoRA (OPLoRA / O-LoRA) for continual FT; CPT mix-back at conservative LR (Nova Forge). Detection: frozen general eval; task loss down + that slice crash = signature.

**Embedding FT.** OpenAI does **not** offer fine-tuning of `text-embedding-3-*` (Matryoshka `dimensions` truncates at inference). Production path: open-weight + sentence-transformers `MultipleNegativesRankingLoss` (batch **64** ⇒ **63** in-batch negatives) wrapped in `MatryoshkaLoss`. After FT, pin **model id + dim + metric** in the vector index — changing any is a full re-embed (same rule as module 01).

---

### 3. Token Economics & NFR Analysis

#### 3.1 Serving — `$ cost per 1k runs` **[inferred]**

Public vendors do **not** sell a “fine-tuned query” SKU. Figures multiply published **inference** rates by a stated mix.

**Assumptions (research reference query):** 800 input + 400 output tokens; 1k requests; no retries; no hosting amortization unless stated.

| Path | Meter (per 1M tok) | Arithmetic | **[inferred] $ / 1k runs** |
| --- | --- | --- | --- |
| OpenAI `o4-mini-2025-04-16` FT | in **$4.00** / cached **$1.00** / out **$16.00** | \(0.8\times4 + 0.4\times16 = 3.2+6.4\) | **$9.60** uncached |
| Same + data sharing | **$2 / $0.50 / $8** | \(0.8\times2 + 0.4\times8\) | **$4.80** |
| Gemini 2.0 Flash (older tuned = **base** rates) | in **$0.15** / out **$0.60** | \(0.8\times0.15 + 0.4\times0.60\) | **$0.36** |
| Gemini 3+ tuned | **1.5×** base prediction | \(1.5 \times\) whatever the live base SKU is | **1.5× the base 1k figure** — do not invent a Gemini 3.5 Flash inference row; it is not in this research file |
| Fireworks dedicated LoRA | “same price as base models” on that deployment’s token meter | training SKU is rarely the annual cost | underwrite **dedicated minutes + base token meter** |
| Bedrock custom **Nova** | on-demand = **same $/token as base Nova** | no standing PTU | token-only; storage **$1.95 / model / month** in worked examples |
| Bedrock Llama 2 70B custom | PTU no-commit **$23.50 / model-unit / hour** | \(23.50\times24\times30 \approx \$16{,}920/\mathrm{mo}\) idle | **≈ $16.92 / 1k** hosting-only if you spread 1M req/month across that idle bill **[inferred]** |

**Hosting dominates low QPS (published, then amortized [inferred]):**

- Azure Standard / Global Standard FT deployment: **$1.70 / hour**. Idle: \(1.70\times24\times30=\$1{,}224/\mathrm{mo}\); **[inferred]** \(1.70\times24\times365=\$14{,}892/\mathrm{yr}\) if left up. At **1M req/month**, hosting alone is **$1.224 / 1k runs**. At **10k req/month**, **$122.40 / 1k**. Developer tier: **no hourly host**, auto-delete **24 h**, **no availability SLA** — exists so you do not pay $1.70/h during eval.
- Azure chatbot example (their o4-mini token rates **$1.10 / $4.40** per 1M, flagged “example purposes” for some arithmetic): 20M in + 40M out + hosting = **$1,422 / month**.
- Together/Fireworks: LoRA train is dollars to tens of dollars for 8B-class SFT; **dedicated endpoint minutes until deleted** dominate if you leave the endpoint up.
- Vertex old Gemini tuned endpoints stay at **base** rates; Gemini 3+ = **1.5×**.

**Break-even vs long prompt [inferred from published rates]:** if a fine-tune **removes 2,000 few-shot tokens/request**, you save 2B input tokens/month at 1M req. At Gemini 2.5 Flash input **$0.15 / 1M**, that is **$300 / month** saved. A 6M-token Flash-Lite tune costs **$9** once. RAG alternative: retrieval + rerank + extra context **every** request (see module 01). Azure **$1,224/month** host means low-QPS Azure FT is often more expensive than a long cached prompt.

#### 3.2 Training cost — worked examples (assumptions stated)

**Billable training tokens** (Google, Together, Fireworks, Bedrock custom-models, Azure SFT/DPO):

\[
\text{billable} = (\text{dataset tokens} \times \text{epochs}) + (\text{val tokens} \times \text{eval passes})
\]

Together: if packing is disabled, tokens = `dataset_length × max_seq_length`. Fireworks: reasoning traces unroll extra tokens; estimate × (conversation turns)/2 when tuning with intermediate thinking.

**Worked A — 5,000 examples × 400 tokens × 3 epochs = 6M training tokens [inferred from Vertex SKUs]:**

| Meter | Arithmetic | Train $ |
| --- | --- | --- |
| Together LoRA SFT ≤16B | \(6 \times 0.48 = \$2.88\) → **$4.00 min** | **$4.00** |
| Fireworks LoRA SFT ≤16B | \(6 \times 0.50\) | **$3.00** |
| Vertex Gemini 2.5 Flash-Lite SFT | \(6 \times \$1.50/1M\) | **$9** |
| Vertex Gemini 2.5 Flash SFT+pref | \(6 \times \$5/1M\) | **$30** |
| Vertex Gemini 3.5 Flash SFT+RL FT | \(6 \times \$10/1M\) | **$60** |
| Vertex Gemini 2.5 Pro SFT | \(6 \times \$25/1M\) | **$150** |
| Azure SFT example (1M tok × 2 ep × **$2/1M**) | different mix | **$4** train + **$1.70/h** host |
| Bedrock Llama 2 70B | \(6 \times 7.99\) | **$47.94** train + **$23.50/h** PTU if that path applies |
| Bedrock Nova Micro (blog, not a SKU) | 4,978 ex, 3 ep, ~1.75M tok, ~1.5 h | **$2.18** train + **$1.75/mo** storage; **[inferred]** \(2.18/1.75 \approx \$1.25/1M\) for **that job only** |

**Worked B — 10M tokens × 3 epochs = 30M billable [inferred from Together]:** Llama-8B-class LoRA SFT \(30\times0.48=\$14.40\); Llama-70B LoRA \(30\times2.90=\$87\).

**Worked C — time-based RL, not token meters:** OpenAI / Azure RFT on `o4-mini-2025-04-16`: **$100.00 / hour** of **core training loop** wall-clock (not queue, not safety checks), prorated to the second. Grader tokens bill at the grader’s rate **after** the job. Azure: **$5,000 per-job cap** (training + grading) pauses and writes a deployable checkpoint; resume then has **no further cap**. Bedrock gpt-oss-20b RFT: **$80/h** then on-demand token inference ($0.09 / $0.39 per 1M) + **$1.95/mo** storage.

**Self-host durations (not a cloud list price):** QLoRA Guanaco 65B = **24 h** on 1×48 GB professional GPU; second Guanaco **<12 h** on a consumer GPU. NVIDIA Llama 3.1 70B DAPT: **128×H100**, **144 h** bf16 → **[inferred] 18,432 H100-hours**. A **hypothetical** $2–$6 / H100-hour would be ~$37k–$110k — **not a quoted NVIDIA price**.

> ⚠️ Gap: There is **no** canonical 2026 on-demand A100/H100 list price that all clouds share. Amazon’s public Bedrock page does **not** publish a complete Nova SFT $/1M grid comparable to Llama 2’s $1.49/$7.99 rows. GPT-5.x is **not** on OpenAI’s live fine-tune pricing table. Snapshot blogs of gpt-4.1 per-token SFT rates are **not** live SKUs as of 2026-09-02. Qwen RFT $/hour on Bedrock was not extracted as a complete grid — confirm live SKU.

**Together / Fireworks training bands (per 1M tokens):**

| Size | Together SFT LoRA / DPO LoRA / SFT full / DPO full | Fireworks LoRA SFT / LoRA DPO / Full SFT / Full DPO |
| --- | --- | --- |
| ≤16B | **$0.48 / $0.54 / $1.20 / $1.35** | **$0.50 / $1.00 / $1.00 / $2.00** |
| mid | 17–69B: **$1.50 / $1.65 / $3.75 / $4.12** | 16.1–80B: **$3 / $6 / $6 / $12** |
| large | 70–100B: **$2.90 / $3.20 / $7.25 / $8.00** | 80–300B: **$6 / $12 / $12 / $24**; >300B: **$10 / $20 / $20 / $40** |

Together: **$4.00 minimum** per job (some models exempt). Specialized LoRA SFT examples: Llama 4 Scout **$3.00**, Maverick **$8.00**, gpt-oss-120B **$5.00**, DeepSeek-V3.1 **$10.00**, GLM-5.x **$40**.

Vertex Gemma 3 SFT per 1M: 1B / 4B / 12B / 27B = **$0.47 / $1.14 / $1.82 / $6.83**. Llama 3.1 8B / 3.3 70B / 4 Scout 17B-16E: **$0.67 / $6.72 / $5.77**. Qwen 3 4B / 8B / 14B / 32B: **$1.35 / $4.18 / $8.46 / $6.57**. Gemini 2.5 Flash-Lite SFT+pref **$1.50 / 1M**; 2.5 Flash **$5**; 2.5 Pro **$25**; 3.1 Flash-Lite **$3**; 3.5 Flash **$10**.

Bedrock Titan Image Generator FT official example: **$0.005 × 500 steps × 64 batch = $160** train + $1.95 storage + $21 eval-hour. Cohere Embed 3 on Bedrock is **PTU-priced ($7.12/h no-commit)**, not an embedding-FT product.

#### 3.3 Latency SLA

> ⚠️ Gap: Public pages do **not** publish a single global p50/p95/p99 for fine-tuned serving. Decompose. Architecture-derived targets below are **[inferred]**, not vendor SLOs. The only published serve datapoints in this research are kernel/paper figures.

| Published datapoint | What it is **not** |
| --- | --- |
| LoRA **merged**: Hu et al. — **zero** added inference latency vs base | Not a p99 SLO |
| LoRA **unmerged batched**: Punica **+2 ms/token** vs a single-model server, **12×** multi-tenant throughput (MLSys 2024) | 2024 research hardware |
| S-LoRA S1: ~**7.6 req/s** from 5 to **2,000** adapters; vLLM-packed OOM past ~5; PEFT **0.25 req/s** at n=100 | **2023** research vs then-vLLM packed — not a 2026 vLLM SLO |
| vLLM 0.15.0 + SageMaker/Bedrock: GPT-OSS 20B **454%** OTPS and **87%** lower TTFT vs 0.11.1rc3; Amazon-tuned **171 OTPS / 124 ms TTFT**; Qwen3-32B dense OTPS **+99%** | Blog benchmark, not a customer SLO |
| Databricks PEFT serving: **~1.5×** vs their “open” baselines; rank **32** is the quality/perf tension | Their prod, not yours |
| Vertex SFT: **excluded from SLA** | There is no Vertex tuned-endpoint availability number to quote |
| Azure Developer FT: **no availability SLA**; Standard **does** offer regional residency | Hosting tier *is* the NFR |

**For 400 output tokens, Punica’s +2 ms/token is +800 ms decode vs merged [inferred from the paper’s +2 ms/token].** Adapter cache miss (vLLM `max_loras` too small → swap) dominates TTFT.

**Architecture-derived targets [inferred] — set adapter-hot vs adapter-cold separately from generate:**

| Metric | Adapter-hot merged or GPU-resident LoRA **[inferred]** | Adapter-cold / swap / first load **[inferred]** | Mitigation |
| --- | --- | --- | --- |
| **p50** | TTFT ~100–250 ms (anchored to **124 ms** Amazon-tuned GPT-OSS 20B blog figure as an existence proof, not a contract) | + adapter load | Keep canary + prod both GPU-hot (`max_loras ≥ 2`); merge if single adapter |
| **p95** | 250–600 ms | 1–3 s if CPU LoRA page-in | Size `max_loras` to the hot tenant set; `max_cpu_loras` = warm set; do not oversize `max_lora_rank` |
| **p99** | 600 ms–1.5 s then **fail to base** | 3–8 s then trip breaker | Timeout the adapter path independently of the base; hedge; never wait on a hung training job |

**Mitigations mapped to percentiles:**

- **p50:** merge for single-tenant / firm-wide models (Hu: zero added latency); Punica/S-LoRA-class batching for multi-LoRA; vLLM `max_lora_rank` = max **among loaded adapters** (if ranks are 16/32/64, set **64 not 256**).
- **p95:** raise `max_loras` so a second tenant does not evict (default **1**); dedicated replica for noisy/regulated tenants; drop unmerged MoE LoRA — merge (PEFT decode-time expert sparsity).
- **p99:** circuit-break the **adapter** path independently of base generate; on miss, serve **base** with a longer prompt or RAG rather than stall; Vertex: drop constrained decoding on tuned Gemini if you did not train with matching structure (documented quality hit).

#### 3.4 Throughput and back-pressure

| Constraint | Documented limit |
| --- | --- |
| vLLM `max_loras` | GPU-resident adapters; **default 1** — second adapter **evicts** unless raised |
| vLLM `max_lora_rank` | Pre-allocated; **default 16**; rank above max = **hard reject** (restart) |
| vLLM `lora-extra-vocab-size` | Default **256** |
| Fireworks addons | **100** per dedicated deployment |
| S-LoRA S1 (research) | **2,000** concurrent adapters, ~**7.61 req/s** at n=2000 vs **7.99** at n=100 |
| Vertex concurrent tuning jobs | Default quota **≥1** global; request `Global concurrent tuning jobs` to raise |
| Fireworks serverless training | Default **8** concurrent runs; **cannot** serverless-serve the trained LoRA afterward |
| Azure Standard vs Global | Global: higher throughput, **no** data-residency guarantee |
| OpenAI FT inference | Dies when the **base** dies; `ft-*` snapshot shutdowns **2026-10-23** for listed ids |

**Back-pressure design:** (1) admission control on the inference gateway by `tenant_id` (token bucket) so one tenant’s LoRA cannot starve the scheduler; (2) bulkhead **training job API** from **serve**; a hung `CreateModelCustomizationJob` must not take serve threads; (3) if `max_loras` is the hot set, overflow tenants queue or route to a second replica — do not silently swap on the p99 path; (4) degrade: requested adapter → base model → prompt/RAG → **deterministic** schema/extractive fallback; (5) training-side: checkpoint-and-pause (Azure Developer preemptible; Azure RFT $5k cap) rather than a single 144-hour uncheckpointed DAPT.

**Capacity identity:** `hot_adapters ≤ max_loras`. Canary needs **+1** slot. 100 Fireworks addons on one deployment is a **hard cap**, not a soft scheduler hint.

#### 3.5 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability** | Vertex SFT **excluded from SLA**. Azure Developer: **no** availability SLA. Azure Standard: regional residency + hourly host. Circuit-break adapter independently of base. | $1.70/h host vs Developer 24 h eviction |
| **RPO** | Last successful **checkpoint** (epoch or `save_steps`, e.g. LlamaFactory 500). Dataset lineage is the other RPO: you cannot unlearn a row from a merged checkpoint without retrain. | Checkpoint frequency vs job throughput (I/O) |
| **RTO** | Adapter rollback = pointer flip (seconds, tens of MB). Merged / vendor FT = load previous full checkpoint or previous model id (minutes; Azure canary+prod = **$3.40/h**). OpenAI after **2027-01-06**: you **cannot create a replacement job**. | Keeping N adapter versions (cheap) vs N merged 70B copies |
| **Compliance** | Vertex: data in tuning region; compute may leave it inside US/EU; **no CMEK** on listed Flash. Azure Standard = regional residency; Global/Developer do **not**. OpenAI regional processing **+10%** uplift (eligible models on/after 2026-03-05). Bedrock: VPC + optional KMS. Fine-tunes **inherit** the base license (Llama **700M MAU** + “Built with Llama” + name prefix **Llama**). | Latency (residency path) and portability (OpenAI/Vertex: **no downloadable weights** in public docs) |
| **Privacy vs kernels** | LoRA-Leak AUC up to **0.775** using the public base as reference. Defenses that kept utility: **dropout** and excluding layers. Unsloth currently wants `lora_dropout=0` — **kernel constraint vs privacy constraint**. | Quality/speed vs MIA |
| **Quality vs forgetting** | Full FT highest capacity (Biderman); LoRA forgets less; \(\alpha=2r\); replay mix. | Plasticity (SLoRA: regularization that blocks forgetting can also block new-task learning) |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO = last object-store checkpoint + dataset hash (minutes of steps since `save_steps`, or one epoch on OpenAI). RTO = adapter pointer (seconds) vs merged reload (minutes) vs **cannot retrain** on OpenAI after 2027-01-06 (RTO becomes “migrate vendor”). GDPR erasure: deleting a row from S3 does **not** unlearn it — retrain from a redacted dataset version; no public vendor “unlearn this user” API was found. Prefer RAG for personal records; **do not put unique identifiers in SFT targets**.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: checkpoints, FSDP/DeepSpeed, vendor job APIs

**Model checkpoints (job resilience):** save every \(N\) steps/epoch. OpenAI: epoch-based checkpoints. Azure RFT: pause at **$5,000** writes a deployable checkpoint; cancel bills work **up to last checkpoint** if a later hour fails. Together: retrieve `steps_completed` on cancel. Fireworks serverless: resume optimizer state (`load_state_with_optimizer`) or fork from a fully qualified state URI. QLoRA **paged optimizers** survive optimizer-state **memory spikes**, not node preemption.

**Activation checkpointing (memory):** recompute activations in backward. Unsloth `use_gradient_checkpointing="unsloth"` (vendor: 2× vs default checkpointing). Required to fit QLoRA 65B in 41–48 GB together with paged Adam.

**DeepSpeed ZeRO:**

| Stage | What is partitioned | Typical use in FT |
| --- | --- | --- |
| 1 | Optimizer states (Adam: 32-bit weights + momentum + variance) | LoRA (few trainable params) often does not need this |
| 2 | Optimizer states + gradients | Mid-size full FT |
| 3 | Parameters + grads + optimizer (all-gather per layer) | Full FT 70B+ |

ZeRO-3 `state_dict` contains **placeholders** unless gathering is enabled — a restore footgun. Offload: optimizer/param to CPU or NVMe (ZeRO-Offload / ZeRO-Infinity).

**Axolotl:** prefer **FSDP2**; FSDP1 deprecated. Compose FSDP + TP + CP + EP via DeviceMesh. DDP+TP/CP is **explicitly unsupported** (use FSDP+TP/CP). FSDP+QLoRA: **70B on two 24 GB GPUs** (`adapter: qlora` + FSDP; Answer.AI path). FSDP2 swap fallback: `offload_params: true` + `cpu_offload_pin_memory: false` uses disk swap when CPU RAM is exhausted (PR #3167; tested 2×3090 + ~32 GB RAM). FSDP1 rejects the swap flag.

**Preemption / spot:** Azure **Developer** training uses preemptible capacity: jobs **pause and auto-resume**; **no charge while paused**; 50% off global. Global training: 10–30% off regional; **no data-residency guarantee**. Together/Fireworks hide the cluster (tokens or GPU-seconds). Self-host: checkpoint-to-object-storage is **mandatory** on spot; neither FSDP nor ZeRO makes a job automatically elastic unless Torch Elastic / Kubernetes is configured.

> ⚠️ Gap: Open-source FT stacks do not publish a standard “spot interruption resume time” SLO. Treat resume latency as checkpoint frequency × load time, not a trainer feature. The research file does not specify Temporal/Kafka product SKUs — map the **equivalent**: job id + checkpoint URI is the workflow handle; DLQ = failed eval / poison JSONL, not a silent promote.

**Canary / rollback:** unmerged adapters = router pointer (vLLM `model` / Fireworks addon). Merged / vendor = previous full id. Eval gate **before** raising canary fraction. After OpenAI 2027-01-06 you cannot mint a replacement; after **2026-10-23** `ft-o4-mini` inference is gone even if other FT jobs can still be created until January 2027.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Vendor 429 / 5xx, GPU preemption, vLLM adapter swap timeout, Together/Fireworks queue | Error rate, Retry-After | Exponential backoff + **full jitter**; retry **idempotent** job-status GETs and serve reads; Azure Developer pause/resume |
| **Permanent** | 4xx auth, rank > `max_lora_rank`, Vertex region mismatch (tune `us-central1`, serve outside `us`/`eu`), OpenAI job creation after org cutoff, merge-into-4-bit | Non-retryable code | Fail closed; do not re-submit the same poison config |
| **Poison pill (data)** | JSONL row that crashes the tokenizer; near-duplicate DPO pairs (signal collapse); PII that should never have been a completion target; contaminated exam items (DICE) | Repeat crash on same row hash; holdout vs train n-gram/embedding near-dup | Quarantine row; DLQ; **do not** block the partition forever — skip + alert; InstructGPT-style PII filter **before** train |
| **Poison pill (policy)** | Reward tag without skill (GRPO `strict_tag_acc`); SimPO/DPO LR **1e-5** → repetitive policy with falling train loss; GRPO on MoE without Routing Replay / GSPO → irreversible collapse | Gold/task metrics falling while proxy RM / train loss looks healthy | Stop on **gold** metrics; held-out verifier; GSPO or Routing Replay for MoE |
| **Idempotency** | Double-POST of `CreateModelCustomizationJob` / `/v1/fine_tuning/jobs` | Two jobs, two bills, two adapters | Idempotency key = `dataset_hash + base@rev + peft_config + seed + codeSHA`; Together tokenized-dataset inspection to see what the model **actually** trained on |
| **Stale serve** | Train NF4, serve GGUF Q4 of the **delta**; Vertex thinking-on after thinking-off SFT; constrained decoding on tuned Gemini | Serve-dtype eval vs training-loss | Eval the **exported** artifact; Unsloth same-precision rule |

**Job-submit idempotency:** treat create as “start if not exists.” OpenAI/Azure: no charge for failed jobs / cancel-before-train, but a **successful duplicate** still trains. Bedrock invocation-log harvest (`invocationLogsConfig`) is a lineage edge **prod traffic → weights** — filter metadata or you fine-tune on production PII.

#### 4.3 Circuit breaker (closed → open → half-open)

Independent breakers: **training job API**, **adapter serve**, **base serve**. A Together token-meter storm or Vertex concurrent-job quota must not starve chat (**bulkhead**).

```
        failures ≥ threshold or error-rate window
  ┌──────────┐  ─────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                       │   OPEN   │
  │ pass all │  success resets consecutive count     │ fail fast│
  └────┬─────┘                                       └────┬─────┘
       ▲                                                  │ cooldown elapsed
       │ trial success                                    ▼
       │                                            ┌──────────┐
       └──────────── trial OK ──────────────────────│ HALF-OPEN│
                    trial fail → OPEN               │ 1 probe  │
                                                    └──────────┘
```

**Thresholds [policy, not vendor SLO]:** trip training-API breaker on 5xx, quota, and repeated `job.failed` with the same config hash (that is **permanent** — do not half-open into the same poison). Trip adapter-serve on TTFT timeout / `max_lora_rank` reject / load failures. Cooldown tens of seconds for serve; minutes-to-hours for training APIs (jobs are not latency-sensitive). One probe in half-open.

**Fallback chain (cited policy):** requested **FT adapter** → **base model** (same tokenizer) → **prompt / RAG** (facts + schema in-context) → **deterministic** fallback (regex/schema extract, canned refusal, last-known-good template). Hedging: canary adapter on the same replica, not a cloned cluster, until promotion. Agent/tool path: on adapter failure, **do not** invent a fine-tune; surface `adapter_degraded`.

#### 4.4 Enterprise security

**Zero-Trust MCP.** `tools/call` on `submit_sft_job` or `generate_ft` is a **weight-and-data exfil API**.

1. **Server-side identity.** `tenant_id` / dataset URI / adapter id from verified token / `RunContext`, never from tool arguments the model filled. Predicate: this actor may train **this** dataset hash onto **this** base revision; this caller may invoke **this** adapter.
2. **Least privilege per tool.** `submit_sft_job` vs `submit_dpo_job` vs `submit_rft_job` (grader may see completions). `generate_ft` vs `generate_base` vs `retrieve_kb`. No omnibus `train(dataset, tenant_id)` / `complete(model_id)`.
3. **Stateless MCP + stateful jobs.** Job state in the registry/checkpointer, not the MCP session. Conversation memory stays out of training JSONL unless a reviewed harvest pipeline (Bedrock `invocationLogsConfig` with equals/notEquals filters) explicitly allows it.
4. **No raw train-row echo** to unauthorized traces. Log hashes + PII-report ids, not completions containing PHI.
5. **Hosted FT:** provider sees your JSONL. Contract residency (Azure Standard vs Global; Vertex region; OpenAI **+10%** regional). Together documents **downloadable checkpoints**; OpenAI/Azure FT = **hosted id only**; Vertex adapter size 1–16 is Google-side PEFT, **not** a downloadable LoRA file in the public docs.

**Isolation ladder (tenant adapters):**

| Pattern | Guarantee | Cost |
| --- | --- | --- |
| Shared replica, many LoRAs | Logical isolation of weights; Punica/S-LoRA batch different LoRAs in one GPU forward — latency **coupled** (noisy neighbor). LoRA-Leak if attacker can query **both** base and adapter | Cheapest; Fireworks cap **100** |
| Dedicated replica per tenant | Azure Standard / Together dedicated / Fireworks dedicated; pay GPU-time | $1.70/h Azure or dedicated minutes |
| Merged per tenant | Max decode; no hot-swap; rollback = full file | Storage; 70B copies |

**PII pipeline (detection → redaction → audit):**

1. **Detect** at ingest (deterministic + ML DLP) **before** the job. InstructGPT **filtered PII from the training split**. Vectors/weights are **derived personal data**.
2. **Redact** unique identifiers from **SFT targets** (not just prompts). Prefer RAG for personal records. Second gate: drop rows that still match PAN/MRN/SSN regex after redaction — poison-pill those, do not train.
3. **Audit** immutable lineage: who uploaded the JSONL, dataset hash, PII-report id, job actor, base revision, rank/α, eval report, promoter, canary %, prod timestamp. Bedrock `jobTags` + `customModelTags`. Vertex project + region + `tuningJobs` audit logs.
4. **Chain-of-custody for the decision to serve:** `adapter_sha` + eval-report hash + promoter identity in every serve span. A chat answer is not “the model said”; it is “adapter `sha` promoted by `actor` at `ts` after eval `id`.”

**LoRA leakage / MIA.** LoRA-Leak (2025): 15 attacks; calibrating against the **public base** **amplifies** leakage; conservative FT AUC **0.765 / 0.721 / 0.775** on Llama-2 / GPT-2 XL / Pythia-2.8B. Do not publish adapters trained on PHI/PII. PrivAuditor: adaptation **does** leak membership; method choice changes risk. Dropout > 0 even when Unsloth wants 0 — document the conflict.

**License inheritance.** A LoRA on Llama-3.3-70B is still Llama-licensed (700M MAU; redistribution “Built with Llama”; distributed model name starting with **“Llama”**). Qwen3 official cards often Apache 2.0 — **verify the checkpoint** (some Qwen2.5-72B / selected Qwen3.5 rows still show a **100 million MAU**-class custom license). Mistral 7B Instruct: Apache 2.0; API-only Large/Medium: commercial ToS. DeepSeek-R1 weights: MIT (inherited-license caveats for some distills).

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional HTTP/PEFT wiring is commented. Run: `python ft_runtime.py`.

```python
#!/usr/bin/env python3
"""Fine-tune control+serve resilience: retries, breakers, adapter→base→deterministic.

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
    """429, 5xx, preemption, adapter swap timeout — safe to retry idempotent ops."""


class PermanentError(Exception):
    """4xx auth, rank>max_lora_rank, cutoff org, poison config hash — do not retry."""


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
            sleep = random.uniform(0, sleep)  # full jitter (AWS-style)
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
    tenant_id: str
    actor: str
    # Server-side: which adapter this caller may hit. NEVER parsed from model JSON.
    allowed_adapter_id: str | None


@dataclass(frozen=True)
class Lineage:
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
    text: str
    adapter_degraded: bool
    generation_degraded: bool
    served: str  # adapter | base | deterministic


class FtServeRuntime:
    """Serve fallback: FT adapter → base → deterministic. Independent breakers."""

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

    def _call(self, gen: Generator, prompt: str, adapter_id: str | None, cid: str, tenant: str) -> str:
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
        return retry_with_jitter(_op, cid=cid, tenant=tenant, op=label)  # type: ignore[return-value]

    def complete(self, prompt: str, authz: Authz, schema_fallback: str) -> DegradedResult:
        cid = str(uuid.uuid4())
        slog(logging.INFO, "serve_start", cid=cid, tenant=authz.tenant_id, q=prompt[:200])
        aid = authz.allowed_adapter_id
        if aid:
            try:
                text = self._call(self.adapter_gen, prompt, aid, cid, authz.tenant_id)
                slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="adapter")
                return DegradedResult(text, False, False, "adapter")
            except (TransientError, PermanentError) as exc:
                slog(logging.ERROR, "adapter_failed", cid=cid, tenant=authz.tenant_id, err=str(exc))
        try:
            slog(logging.WARNING, "fallback_base", cid=cid, tenant=authz.tenant_id)
            # Longer prompt / RAG belongs here in production; identity still from Authz.
            text = self._call(self.base_gen, prompt, None, cid, authz.tenant_id)
            slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="base")
            return DegradedResult(text, True, False, "base")
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "base_failed", cid=cid, tenant=authz.tenant_id, err=str(exc))
        slog(logging.ERROR, "serve_deterministic", cid=cid, tenant=authz.tenant_id)
        return DegradedResult(
            f"Generation unavailable. Deterministic fallback: {schema_fallback}",
            True,
            True,
            "deterministic",
        )


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
    assert jid == jid2
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

**Wired here:** full-jitter retries; closed→open→half-open breakers on **train API** and **adapter/base serve**; idempotent job submit keyed by dataset+base+peft+seed+code SHA; eval gate hard-blocks promote; fallback **adapter → base → deterministic**; JSON logs with `cid`+`tenant`+`job`. Real vLLM clients must send `authz.allowed_adapter_id` as the `model` alias, never a model-emitted string. Real merge path: `merged = PeftModel.merge_and_unload()` — assign the return; merge into bf16 then re-quantize; eval the export.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Multi-tenant SaaS: per-tenant LoRA on one base (10k tenants, schema lock-in)

**Problem.** B2B copilot. Each tenant has a stable ticket JSON schema + tone, but **facts** (SKU tables, error codes) change weekly. Peak: thousands of QPS mixed across tenants. Requirements: SOC 2 isolation, hot-swap without cloning 8B weights per tenant, p95 chat in a few seconds, ability to rollback a bad tenant adapter without touching others. Do **not** fine-tune the SKU catalog.

**Proposed architecture:**

```
  ┌──────────────┐     ┌─────────────────────────────────────────────┐
  │ Tenant IdP   │     │ CONTROL: dataset_hash → SFT LoRA job        │
  │ JWT → PEP    │────▶│   r=32, α=64 (2r), all-linear               │
  └──────────────┘     │   eval: schema exact-match + forgetting     │
                       │   + safety + serve-dtype                    │
                       │   registry: tenant_id → adapter_id (env)    │
                       └──────────────────┬──────────────────────────┘
                                          ▼
                       ┌──────────────────────────────────────────────┐
                       │ SERVE: vLLM --enable-lora                    │
                       │   max_loras = GPU-hot set (≫ 1; canary +1)   │
                       │   max_lora_rank ≥ 32 (not default 16)        │
                       │   max_cpu_loras = warm set                   │
                       │ Fireworks alt: ≤100 addons / dedicated       │
                       │ RAG tool for SKU/error facts (ACL predicate) │
                       └──────────────────┬───────────────────────────┘
                                          ▼
                       ┌──────────────────────────────────────────────┐
                       │ Fallback: adapter → base → schema template   │
                       │ Canary 5% on same replica (max_loras ≥ 2)    │
                       │ Audit: {tenant, adapter_sha, base_rev}       │
                       └──────────────────────────────────────────────┘
```

**Technology choices:** Open-weight 8B LoRA (Together LoRA SFT **$0.48/1M**, Fireworks **$0.50/1M** ≤16B) or Vertex Gemini 2.5 Flash-Lite (**$1.50/1M** train; tuned serve at **base** rates for older Gemini). Databricks-style **rank 32** as the quality floor unless eval says otherwise; Biderman **\(r=256\)** only if code-IFT quality demands it **and** you restart vLLM with `max_lora_rank=256`. Facts via RAG (module 01), not CPT on the catalog. Isolation: shared replica for standard tenants; dedicated replica for regulated tenants (LoRA-Leak + noisy-neighbor).

**Economics [inferred]:** 6M-token 8B LoRA train is **$3–$9** once (Fireworks/Together min / Vertex Flash-Lite). Annual cost is **dedicated minutes + token meter**, not the train SKU. Azure Standard **$1,224/mo** host even at zero traffic — reject Azure FT for sparse tenants unless Developer 24 h eval then promote elsewhere. 1k runs at Gemini 2.0 Flash-class **[inferred] $0.36** vs OpenAI o4-mini FT **[inferred] $9.60** uncached — plus o4-mini FT inference **dies 2026-10-23**.

**Trade-off matrix:**

| Axis | **A1 Shared vLLM/Fireworks multi-LoRA + RAG facts (recommended)** | **A2 Merged 8B per tenant** | **A3 Prompt/cache only (no FT)** |
| --- | --- | --- | --- |
| **Cost** | Train $3–$9/tenant-version; one GPU + Punica/vLLM; Fireworks 100-addon cap may force a second dedicated | 8B copy × tenants; rollback = full files; Hu 100 adapters **~354 GB** vs **~35 TB** full (GPT-3-scale illustration) | Zero train; long few-shot every request; 2k tok saved × 1M req = **$300/mo** at Flash $0.15/1M if you *had* FT’d that away |
| **Latency** | Merged=0 extra; unmerged Punica **+2 ms/token**; default `max_loras=1` → TTFT spikes on swap | Best decode (merged) | Prompt-cache wins if examples fit; else lost-in-the-middle |
| **Ops complexity** | `max_lora_rank` restart class; canary on same replica | N merged artifacts; no hot-swap | No registry; policy changes this week stay in prompt (good) |
| **Security posture** | Logical LoRA isolation; LoRA-Leak if base+adapter both queryable; pin dedicated for PHI | Full weights contain train data | No tenant weights to leak; RAG ACL still required |
| **Scalability ceiling** | Fireworks **100** addons; S-LoRA research **2,000**/GPU (not a 2026 SLO); overflow = more replicas | Storage/ops wall | Window + cache TTL |

**Decision.** **A1 wins** for schema/persona lock-in with changing facts: LoRA is the form, RAG is the catalog, prompt is the weekly policy. A2 wins only for a single firm-wide merged model (Scenario B’s merge option). A3 wins while few-shot still hits exact-match JSON — OpenAI’s own wind-down assumes this is increasingly true; **measure** before you mint adapters. Do not put SKUs in SFT targets.

#### Scenario B — Legal / professional services: jurisdiction SFT + case-law RAG

**Problem.** AmLaw / Big Four copilot: answers must follow IRAC/memo structure and jurisdiction-specific captioning, cite **only** from the provided record, and never treat the model as a case-law database. Privilege + PII in matter files. Some practice groups need hot-swap tone (litigation vs advisory). Llama-family license if you distribute. Eval must hold out **real** matters never in train (DICE/GSM1k-class contamination of bar-exam public sets).

**Proposed architecture:**

```
  ┌─────────────┐   ┌─────────────────────────────────────────────┐
  │ Matter ACL  │──▶│ CONTROL: PII/privilege redact → SFT JSONL    │
  │ + DLP gate  │   │   Completions = structure, not case holdings │
  └─────────────┘   │   Optional CPT/Nova Forge if legal perplexity│
                    │   high (full / high-rank — not LoRA CPT)     │
                    │   then SFT LoRA r=32–256, α=2r, all-linear   │
                    │   DPO/ORPO only on tone pairs, not holdings  │
                    └──────────────────┬──────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────┐
                    │ SERVE: merge firm-wide model  OR             │
                    │   unmerged LoRA per practice group           │
                    │ RAG over the record (ACL + recency);         │
                    │ OpenAI RFT docs list case-law passages as a  │
                    │ **grader** problem — still grounded, not FT’d│
                    │ Citation ⊆ retrieved chunk_ids               │
                    └──────────────────┬───────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────┐
                    │ Eval: held-out matters + forgetting + safety │
                    │ LoRA-Leak: do not publish adapters           │
                    │ License: Llama 700M MAU + "Llama…" naming    │
                    │ Fallback: adapter → base → extractive quotes │
                    └──────────────────────────────────────────────┘
```

**Technology choices:** SFT for **behavior** (captioning, “answer only from provided record”). RAG for the corpus of opinions (module 01 ACL). CPT/DAPT (NVIDIA 70B: **128×H100**, **144 h**, 17M papers — class of spend) **only** if the base’s legal perplexity is high; Biderman: LoRA CPT will **not** close the full-FT CPT gap. Preference: DPO \(\beta\approx0.1\) or ORPO \(\lambda=0.1\) on memo tone — not on who should win the case. RFT/GRPO if a **schema/citation-format grader** exists; do not use a weak “sounds legal” RM (Gao overoptimization; ODIN length hack). Serving: merge for one firm-wide model; unmerged LoRAs per practice group if you need hot-swap without 70B copies. Vertex thinking **off** during SFT if you use Gemini. Together if you need **downloadable** checkpoints; OpenAI/Vertex if you accept hosted-id lock-in (OpenAI jobs end **2027-01-06**).

**Trade-off matrix:**

| Axis | **B1 SFT/LoRA for form + RAG for record (recommended)** | **B2 CPT full-FT on unlabeled opinions then SFT** | **B3 Hosted RFT ($100/h o4-mini) as the knowledge store** |
| --- | --- | --- | --- |
| **Cost** | 6M Flash-Lite **$9** or Together 8B LoRA **$4** min; RAG is the ongoing bill (module 01) | NVIDIA-class **18,432 H100-h [inferred]**; Nova Forge = subscription, not the public Bedrock token table | **$100/h** core loop + grader tokens; Azure **$5k** pause; snapshot **dies 2026-10-23** |
| **Latency** | Merged firm model: base-class decode; RAG retrieve+rerank dominates e2e | Same serve as any merged 70B | Hosted FT inference + RAG anyway if you stay grounded |
| **Ops complexity** | Two systems (FT registry + RAG index); eval four gates | FSDP2/ZeRO-3; checkpoint-to-object-store on spot; ZeRO-3 `state_dict` placeholders | Vendor job API; **cannot retrain** after OpenAI cutoff |
| **Security posture** | Redact before JSONL; no identifiers in targets; adapters unpublished (MIA 0.775); RAG ACL on the record | Full weights memorize opinions; erasure = retrain; Llama license if distributed | Provider sees JSONL; hosted id only; no CMEK on Vertex listed Flash |
| **Scalability ceiling** | Practice-group LoRAs on one vLLM; merge when rank/kernel pressure (Databricks 32) | 70B+ training cluster; Biderman: use full/high-rank for CPT | Single vendor snapshot; Evals dashboard gone **2026-11-30** |

**Decision.** **B1 wins** for this threat model: FT teaches *how to write on the record*; RAG *is* the record; RFT graders check format/grounding, they do not replace the corpus. B2 is justified only when the tokenizer does not speak the domain (legal French, novel captioning) and you can fund DAPT + a forgetting slice. B3 as a knowledge store fails on lock-in, the **2026-10-23** o4-mini FT death date, and OpenAI’s own framing of case-law passages as a **grader** problem. Hybrid still needs retrieval ACLs — fine-tune does not stamp privilege.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **Catastrophic forgetting** | Task FT moves directions that implemented prior skills; IFT > CPT; code > math; volume increases forgetting | Frozen golden / Biderman 3-task average crashes while task loss falls | LoRA for IFT; \(\alpha=2r\); PPO-ptx replay \(\gamma=27.8\); OPLoRA/O-LoRA; block promote |
| **Reward hacking** | Proxy RM ↑, gold ↓ as KL grows (Gao); length (ODIN); GRPO tag-only rewards | Gold/task vs proxy RM; mean length \(L\) Pareto | KL \(\beta\) (InstructGPT **0.02**, DeepSeekMath **0.04**); held-out verifier; stop on gold; GSPO / Routing Replay on MoE |
| **Eval contamination** | Paraphrased test items in FT data (DICE); GSM8K vs GSM1k gap | Private never-published eval; n-gram + embedding near-dup | Hold out real traffic; OOD exam beside public set |
| **Silent overfit** | Too many epochs; Unsloth loss → 0; OpenAI diversity collapse | Epoch checkpoints; diversity slice | −1–2 epochs; pick by RM/dev quality not val NLL (InstructGPT/LIMA) |
| **SimPO/DPO garbage policy** | LR **1e-5** (SFT-typical) on preference trainers | Repetitive/incoherent gens; train loss still falling | LR grid **3e-7–1e-6**; math ~**5e-7** |
| **GRPO MoE collapse** | Token-level IS + expert routing volatility | Non-convergence on long runs | GSPO or Routing Replay |
| **QLoRA merge collapse** | `merge_and_unload` into bitsandbytes 4-bit (transformers#31293) | Serve-dtype eval ≠ train loss | Merge bf16/fp16, then AWQ/GGUF; eval export |
| **Train/serve dtype skew** | QLoRA 16-bit adapters vs 4-bit base; GGUF Q4 of the delta | Same | Unsloth same-precision; Vertex: no extra thinking after thinking-off SFT |
| **Constrained decode on tuned Gemini** | Constraints not applied during tuning | Quality drop at serve | Drop constrained decoding or tune with matching structure |
| **Adapter swap TTFT** | vLLM `max_loras` default **1**; rank > `max_lora_rank` default **16** | TTFT spikes; hard reject | Size hot set; restart with max rank; canary slot +1 |
| **MoE unmerged LoRA** | Every expert’s LoRA materialized at decode | OTPS collapse | Merge for MoE serving (PEFT) |
| **Poison JSONL / PII in targets** | Harvested prod logs; identifiers in completions | DLP on dataset; LoRA-Leak-class MIA | InstructGPT-style filter; RAG for personal records; retrain to erase |
| **Duplicate job bills** | Non-idempotent CreateJob | Two adapter ids | Key = hash(dataset, base, peft, seed, code) |
| **ZeRO-3 restore empty** | `state_dict` placeholders | Load looks like random | Enable gather on checkpoint |
| **Vendor lock / death dates** | OpenAI no new jobs **2027-01-06**; `ft-o4-mini` **2026-10-23**; Evals **2026-11-30** | Calendar | Weights-out path (Together) or migrate before cutoff |
| **License surprise** | LoRA inherits Llama 700M MAU / naming | Legal review at distribute | Verify **checkpoint** license (Qwen Apache vs Qwen License) |
| **Chat bench ≠ humans** | Guanaco Vicuna 99.3%; Kendall \(\tau=0.43\) | Human / task holdout | Do not promote on chatbot leaderboards alone |
| **CMEK / SLA gap** | Vertex listed Flash: no CMEK; SFT not Covered Service | Contract | Azure Standard residency or Bedrock KMS/VPC; do not quote a Vertex tuned SLA |

---

## Key Takeaways

- Fine-tuning is **two planes sharing versioned artifacts**, not `train()` then `chat()`. A hung job must not block serve; a bad promote is a pointer you should be able to roll back.
- **Prompt / RAG first.** FT is for a **stable distribution shift** (schema, persona, domain language). It is a bad document store and does not stamp ACLs. Hybrid is the enterprise default.
- **LoRA is not “full FT but cheaper.”** Biderman: low-rank underperforms; CPT gap persists; IFT at **\(r=256\)**, all-linear, **\(\alpha=2r\)** can match. Databricks customers often need **rank 32**. LoRA **forgets less** even at matched task accuracy.
- **Method card:** SFT for format; DPO (~2× logits, Together **~10–12%** surcharge, Fireworks **2×**); ORPO one-stage no ref; KTO for thumbs; GRPO when a **machine checker** exists (\(G=64\) in DeepSeekMath); GSPO for MoE/long rollouts; PPO if you can afford 4× VRAM and still fight Gao overoptimization.
- **Budget hosting, not the $9 job.** Azure **$1.70/h = $1,224/mo** idle. Vertex Gemini 3+ tuned serve is **1.5×** base. Fireworks/Together dedicated minutes dominate. OpenAI o4-mini FT serve **[inferred] $9.60/1k** on 800/400 vs Gemini 2.0 Flash-class **[inferred] $0.36/1k**.
- **Eval four ways before promote:** task holdout, forgetting slice, safety, **serve-dtype**. OpenAI Evals dies **2026-11-30**; Vertex SFT has **no SLA**.
- **Serve path:** merge for one model (zero extra latency); unmerged for tenants (vLLM `max_loras` / `max_lora_rank`; Fireworks **100**). Fallback **adapter → base → prompt/RAG → deterministic**.
- **Weights are derived personal data.** PII filter before JSONL; LoRA-Leak AUC **0.775**; erasure = retrain; Llama license **inherits** onto the LoRA. OpenAI/Vertex: you may not get the files.

---

## Interview Q&A

**Q1. Explain production fine-tuning to someone who only knows ChatGPT.**  
I split a test kitchen from the dining room. Training ingests redacted JSONL, runs a job (SFT or LoRA or DPO), checkpoints, and only promotes after holdout, forgetting, safety, and serve-dtype evals. Serving loads the same base weights plus a megabyte adapter per tenant — or a merged file — and never waits on a hung GPU job.

**Q2. When do you refuse to fine-tune?**  
When the behavior fits in a cached prompt, or the knowledge moves weekly — that is RAG. OpenAI is winding down self-serve FT because newer bases already follow instructions; Anthropic has no first-party FT API. I still SFT for a schema or persona few-shot keeps missing, and I CPT only when the model does not speak the domain.

**Q3. LoRA vs full fine-tune — which do you pick?**  
LoRA for instruction FT: Biderman shows it forgets less and high-rank IFT can match full FT. I target all linear layers, start from Databricks’ rank **32**, and go to **\(r=256\), \(\alpha=2r\)** for code. I do **not** use LoRA for continued pretraining — that gap does not close. Full FT if memory allows and CPT quality is the product.

**Q4. What are rank and alpha, actually?**  
Rank \(r\) is the inner dimension of \(BA\); trainable params per matrix are \(r(d+k)\). Alpha scales the update: Hu uses \(\alpha/r\); rsLoRA uses \(\alpha/\sqrt{r}\). PEFT’s default \(\alpha=r=8\) is the intruder-dimension setting; Biderman and the Illusion of Equivalence paper want \(\alpha=2r\). vLLM’s default `max_lora_rank=16` will **hard-reject** a 256-rank adapter until restart.

**Q5. DPO vs PPO vs GRPO vs ORPO — pick in one minute.**  
PPO needs a reward model, a value head, and on-policy samples — InstructGPT 4×-class VRAM and still overoptimizes (Gao). DPO reparameterizes that KL-constrained optimum into a pairwise loss with a frozen reference — Together only surcharges the LoRA meter ~10–12%; Fireworks charges **2×**. ORPO folds preference into SFT with no reference; watch the forgetting slice because there is no KL anchor. GRPO drops the critic, samples a group, and needs a **verifier**; DeepSeekMath used \(G=64\), LR **1e-6**, KL **0.04**. GSPO if the policy is MoE or the rollouts are long.

**Q6. Give me a cost model for training and for 1,000 production calls.**  
Training: 5,000 × 400 tok × 3 epochs = 6M billable. Together 8B LoRA hits the **$4** minimum; Fireworks LoRA **$3**; Vertex Flash-Lite **$9**; 3.5 Flash **$60**; 2.5 Pro **$150**. Serving 800/400: o4-mini FT **[inferred] $9.60/1k** uncached; Gemini 2.0 Flash-class tuned-at-base **[inferred] $0.36/1k**; add Azure **$1.224/1k** host if you spread $1,224/mo over 1M requests — or **$122/1k** at 10k requests. The train job is not the annual bill.

**Q7. What SLO do you put in the contract for a fine-tuned endpoint?**  
I do **not** quote a vendor FT p99 — nobody publishes one, and Vertex SFT is excluded from SLA. I SLO adapter-hot TTFT separately from generate, treat **124 ms TTFT** on Amazon-tuned GPT-OSS 20B as a blog existence proof, set an adapter timeout as **policy**, merge when I can (Hu: zero extra latency), and fail to base rather than wait on LoRA swap. Punica’s **+2 ms/token** is ~**+800 ms** on a 400-token completion if I stay unmerged **[inferred]**.

**Q8. How do you stop a bad adapter from taking down prod?**  
Eval gate is a hard block. Canary 1–5% on the **same** replica with `max_loras ≥ 2`. Rollback is a registry pointer for unmerged LoRA (35 MB-class artifacts). Merged rollback is a full checkpoint; Azure canary+prod is **$3.40/h**. Independent circuit breakers for train API vs adapter vs base. Fallback adapter → base → RAG/prompt → deterministic schema.

**Q9. Catastrophic forgetting showed up on our general eval. Now what?**  
That is the signature: task loss down, HellaSwag/WinoGrande/ARC (or our frozen golden) down. I switch IFT to LoRA with \(\alpha=2r\), mix replay like PPO-ptx \(\gamma=27.8\), and I do not keep pouring data — Biderman saw forgetting increase with volume. For continual tasks I look at orthogonal LoRA (O-LoRA / OPLoRA). I never promote on Vicuna GPT-4-as-judge alone — QLoRA’s own Kendall \(\tau\) with humans was **0.43**.

**Q10. Multi-tenant LoRA serving — what bites in production?**  
vLLM defaults: `max_loras=1` (evict), `max_lora_rank=16` (reject Biderman ranks). Memory is `max_loras × max_lora_rank × hidden` at start. Fireworks caps **100** addons and will not serverless-serve your custom LoRA. Isolation is logical — Punica batches different LoRAs in one forward, so latency couples. An attacker who can hit base **and** adapter is the LoRA-Leak threat model (AUC **0.775**). Regulated tenants get a dedicated replica. MoE: merge.

**Q11. QLoRA on 65B — what do you actually need to remember?**  
NF4 + double quant + paged Adam + 16-bit adapters; 65B in **<48 GB**, Guanaco **24 h** on one 48 GB GPU, **41 GB** 4-bit footprint, LR **1e-4** at 33B/65B. MMLU NF4+DQ **53.1** vs bf16 **53.0** — but they did **not** claim 16-bit full-FT parity at 65B. Do not merge into 4-bit. Axolotl FSDP+QLoRA: **70B on two 24 GB GPUs**. Unsloth wants `lora_dropout=0`, which fights the MIA dropout defense.

**Q12. Zero-Trust MCP around training and adapters — failure mode?**  
An omnibus `train(dataset_uri, tenant_id)` or `complete(model_id)` filled by the model. That is a data-and-weight exfil API. I split submit_sft / submit_rft / generate_ft / generate_base / retrieve_kb, take identity from the verified token, key jobs by dataset hash + base rev + peft JSON + seed + code SHA, log hashes not PHI completions, and I do not harvest production logs into a Bedrock job without metadata filters. After 2027-01-06 I cannot mint an OpenAI replacement — portability is a security+continuity requirement, not a nice-to-have.

---

## Key Numbers to Memorize

### Methods / quality
| Number | What |
| --- | --- |
| **~13k / ~33k / ~31k** | InstructGPT SFT / RM / PPO prompt counts |
| **16 epochs; 1 epoch val-NLL overfit** | InstructGPT SFT; pick by RM score not val NLL |
| **85 ± 3% / 71 ± 4%** | 175B InstructGPT vs 175B GPT-3 / vs few-shot 175B |
| **1.3B preferred to 175B GPT-3** | InstructGPT PPO-ptx |
| **1,000 pairs; ~750k tok; 43% vs GPT-4** | LIMA; 88% met req; 50% excellent |
| **45.2% → 76.1%** | LIMA 30 dialogue chains, multi-turn “excellent” |
| **29.79% → 64.69%** | NEFTune AlpacaEval LLaMA-2-7B + Alpaca |
| **99.3%; \(\tau=0.43\)** | Guanaco-65B Vicuna-of-ChatGPT; human vs GPT-4 rank agreement |
| **9,209** | OASST1 top-replies (Guanaco SFT) |
| **\(\beta=0.02\) / \(\gamma=27.8\)** | InstructGPT PPO KL / PPO-ptx pretrain mix scale |
| **\(\beta=0.1\)** | LlamaFactory / TRL DPO (and ORPO `beta` ≡ paper \(\lambda\)) default |
| **12.20% / 66.19% / 7.32** | Mistral-ORPO-β AlpacaEval 2.0 / IFEval loose / MT-Bench |
| **11.33 vs 8.35** | Mistral-ORPO-α vs Zephyr-α AlpacaEval 2.0 |
| **GSM8K 40.0 → 53.5** | KTO vs DPO on Zephyr-β-SFT UltraFeedback |
| **44.7 / 40.3 / 26.0** | Llama3-Instruct SimPO / DPO / SFT AlpacaEval 2 LC |
| **\(G=64\); LR 1e-6; KL 0.04** | DeepSeekMath GRPO |
| **82.9% → 88.2%; 46.8% → 51.7%** | DeepSeekMath-RL GSM8K / MATH vs Instruct |
| **SimPO \(\beta=2{-}10\); LR 3e-7–1e-6** | Not DPO’s 0.1 / not SFT’s 1e-5 |

### LoRA / QLoRA / serve
| Number | What |
| --- | --- |
| **10,000×; 1.2 TB → 350 GB; 35 MB** | LoRA GPT-3 175B params / VRAM / \(r=4\) Q/V adapter |
| **96 vs 24 V100s; ~25% speedup** | Full FT vs LoRA training (Hu) |
| **\(r=8\), \(\alpha=8\)** | PEFT / LlamaFactory defaults — not Biderman’s recipe |
| **\(r=32\)** | Databricks: most customers need this to avoid quality drop |
| **\(r=256\), \(\alpha=2r=512\)** | Biderman IFT (code); 16–64 often fail on code |
| **LR \([1\mathrm{e}{-5}, 5\mathrm{e}{-4}]\)** | Biderman IFT LoRA sweep |
| **2e-4 / 1e-4** | QLoRA 7B/13B vs 33B/65B LR |
| **<48 GB; 41 GB; 24 h** | QLoRA 65B fit / Guanaco 4-bit footprint / wall clock |
| **53.1 vs 53.0 vs 52.2** | QLoRA MMLU mean NF4+DQ / bf16 / FP4 |
| **62.2 / 62.5 vs 63.4** | 65B MMLU Guanaco/Alpaca vs base (chat data can drop MMLU) |
| **+3.7 / +2.9 / +4.4** | DoRA commonsense vs LoRA LLaMA-7B / LLaMA2-7B / LLaMA3-8B |
| **0 extra ms merged; +2 ms/token Punica; 12×** | Serve overhead / multi-tenant throughput (papers) |
| **max_loras=1; max_lora_rank=16** | vLLM defaults; rank 256 **rejects** |
| **100 addons** | Fireworks dedicated cap |
| **7.61 req/s @ 2000 adapters** | S-LoRA S1 (2023 research, not 2026 SLO) |
| **171 OTPS / 124 ms TTFT** | vLLM 0.15 Amazon-tuned GPT-OSS 20B (blog) |
| **70B on 2×24 GB** | Axolotl FSDP+QLoRA |
| **AUC 0.775** | LoRA-Leak MIA (public base as reference) |

### $ / dates / NFR
| Number | What |
| --- | --- |
| **$100/h; $80/h** | OpenAI/Azure o4-mini RFT core loop; Bedrock gpt-oss-20b RFT |
| **$5,000** | Azure RFT per-job cap then uncapped resume |
| **$1.70/h; $1,224/mo; $14,892/yr [inferred]** | Azure Standard FT host; 24×30; 24×365 |
| **$4.00 / $1.00 / $16.00** | o4-mini FT in / cached / out per 1M (data-share half) |
| **[inferred] $9.60 / $4.80 / 1k** | 800/400 o4-mini FT uncached / data-share |
| **[inferred] $0.36 / 1k** | 800/400 Gemini 2.0 Flash base=older-tuned rates |
| **1.5×** | Gemini 3+ tuned inference vs base |
| **$0.48 / $0.50** | Together / Fireworks SFT LoRA ≤16B per 1M |
| **~10–12% vs 2×** | Together vs Fireworks DPO LoRA surcharge on SFT LoRA |
| **$4 min** | Together per job (some exempt) |
| **$9 / $60 / $150** | Vertex 6M tok Flash-Lite / 3.5 Flash / 2.5 Pro **[inferred]** |
| **$2.18 + $1.75/mo** | Bedrock Nova Micro blog (4,978 ex, ~1.75M tok) |
| **$1.49 / $7.99 per 1M** | Bedrock Llama 2 13B / 70B FT train |
| **$23.50/h ≈ $17k/mo** | Llama 2 70B no-commit PTU idle |
| **$1.95 / model / mo** | Bedrock custom storage in worked examples |
| **$300/mo saved [inferred]** | Drop 2k few-shot tok × 1M req at Flash $0.15/1M |
| **2026-05-07 / 2026-07-02 / 2027-01-06** | OpenAI FT: no new orgs / 60-day idle cutoff / all new-job creation ends |
| **2026-10-23** | `ft-o4-mini-2025-04-16` (+ listed ft-* snapshots) inference shutdown |
| **2026-11-30** | OpenAI Evals platform shutdown (read-only 2026-10-31) |
| **+10%** | OpenAI regional processing uplift (eligible models from 2026-03-05) |
| **131,072; 1 GB; 10M / 300k** | Vertex per-example tok; JSONL max; train text / multimodal max |
| **5,000 or 30%** | Vertex max validation examples |
| **Adapter 1–16 (Pro max 8)** | Vertex LoRA-rank analogue |
| **700M MAU; 100M MAU-class** | Llama Community License; some Qwen custom-license rows |
| **128×H100 × 144 h = 18,432 [inferred]** | NVIDIA 70B DAPT GPU-hours; $ **not** published |

---

*End of module. Practice the Q&A out loud; recode the breaker states and adapter→base→deterministic chain from memory; recompute the 6M-token train bill and the 800/400 serve mix on a whiteboard with the assumptions listed.*
