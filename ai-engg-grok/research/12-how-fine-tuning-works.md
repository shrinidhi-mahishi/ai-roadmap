# Research: How Fine Tuning Works

**Date researched**: 2026-09-30
**Sources consulted**: 18
**Scope note**: Mechanics of SFT / PEFT (LoRA, QLoRA), preference alignment (RLHF, DPO), hosted fine-tuning surfaces (OpenAI, Anthropic-via-Bedrock), and the decision boundary **when not to fine-tune** vs prompt vs RAG. Deep RAG retrieval design and prompt-engineering craft are out of scope (covered elsewhere); only the FT-vs-prompt-vs-RAG lever choice is included.

## 1. System Topology & Mechanics

### What fine-tuning changes

Foundation models are next-token predictors pretrained on internet-scale text. Fine-tuning reuses the same loss on a much smaller, task-shaped dataset so the model’s **defaults** (style, format, reasoning patterns, refusal calibration) shift toward the target domain — it is not a substitute for installing fresh facts ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)). Operating rule from that piece: **fine-tune for behavior, retrieve for knowledge**.

Three training regimes ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)):

| Regime | Data shape | Typical scale | What it changes |
| --- | --- | --- | --- |
| **Continued pretraining** | Raw domain text (unlabeled) | Hundreds of millions → billions of tokens | Domain vocabulary / next-token priors (e.g. BloombergGPT, Med-PaLM lineage) |
| **Supervised fine-tuning (SFT)** | Instruction → desired response pairs | ~1k–50k pairs common in practice writeups | Style, format, reasoning imitation |
| **Preference tuning (RLHF / DPO)** | Prompt + preferred vs rejected completion | ~500–5k preference pairs in practice writeups | Tone, calibrated refusals, ranking-sensitive behaviors SFT misses |

Practical order: SFT first → ship → DPO/RLHF for residual preference failures; continued pretraining only if the base fails a domain-vocabulary probe ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### Full fine-tune memory topology

For a **7B** model: parameters alone ~**14 GB** at FP16; training roughly doubles that for gradients (~**28 GB**); Adam-style optimizer states push total toward **~60–80 GB** — beyond consumer 16–24 GB GPUs ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)). This memory wall is why PEFT dominates production FT.

### LoRA (Hu et al., 2021/2022)

**Paper**: *LoRA: Low-Rank Adaptation of Large Language Models* ([arXiv:2106.09685](https://arxiv.org/abs/2106.09685)).

Mechanics:

- Freeze pretrained \(W_0 \in \mathbb{R}^{d \times k}\). Learn update \(\Delta W = BA\) with \(B \in \mathbb{R}^{d \times r}\), \(A \in \mathbb{R}^{r \times k}\), rank \(r \ll \min(d,k)\).
- Forward: \(h = W_0 x + BAx\) (scaled by \(\alpha/r\)). Init: Gaussian \(A\), zero \(B\) so \(\Delta W=0\) at start.
- Original study adapts attention projections (\(W_q, W_v\) primarily); MLP frozen in most GPT-3 experiments.
- Trainable count when adapting \(\hat{L}_{\text{LoRA}}\) matrices: \(|\Theta| = 2 \times \hat{L}_{\text{LoRA}} \times d_{\text{model}} \times r\) ([LoRA paper §4](https://arxiv.org/abs/2106.09685)).

Quantified GPT-3 **175B** results ([LoRA paper](https://arxiv.org/abs/2106.09685)):

| Metric | Full FT (Adam) | LoRA |
| --- | --- | --- |
| Trainable params | Full 175B | Up to **~10,000×** fewer; as low as **~0.01%** of \(|\Phi_0|\) |
| Training VRAM | **1.2 TB** | **~350 GB** (~**3×** reduction) |
| Adapter checkpoint (\(r=4\), \(W_q+W_v\)) | Full ~**350 GB** | **~35 MB** (~**10,000×** smaller) |
| Multi-tenant storage (100 tasks) | ~**35 TB** full copies | ~**350 GB + 100×35 MB ≈ 354 GB** |
| Parameter budget example | — | **18M** trainable (~**35 MB** FP16) = \(r=8\) on one attention type **or** \(r=4\) on two types across **96** layers |

Deployment: merge \(W = W_0 + BA\) → **zero added inference latency** vs full FT; task switch by swapping adapters ([LoRA paper](https://arxiv.org/abs/2106.09685)). Newsletter framing: rank typically **8 or 16**; trains **&lt;1%** of params and recovers **~90–95%** of full-FT quality on many tasks; hard domain shifts can hit LoRA’s rank capacity ceiling ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### QLoRA (Dettmers et al., 2023)

**Paper**: *QLoRA: Efficient Finetuning of Quantized LLMs* ([arXiv:2305.14314](https://arxiv.org/abs/2305.14314)).

Mechanics: freeze a **4-bit** quantized base; backprop into **BF16 LoRA** adapters. Innovations:

1. **NF4** (4-bit NormalFloat) — information-theoretically optimal for normally distributed weights.
2. **Double Quantization** — quantize the quantization constants → saves **~0.373 bits/parameter** (~**3 GB** on a 65B model).
3. **Paged Optimizers** — NVIDIA unified memory pages optimizer states to CPU on OOM spikes.

Quantified ([QLoRA paper](https://arxiv.org/abs/2305.14314)):

| Claim | Number |
| --- | --- |
| Full 16-bit FT of LLaMA **65B** | **&gt;780 GB** GPU memory |
| QLoRA FT of 65B | **&lt;48 GB** (single GPU) |
| Guanaco 65B train time | **~24 h** on one professional GPU → **99.3%** of ChatGPT on Vicuna (GPT-4 judged) |
| Deployed footprint (Table 1) | Guanaco **65B: 41 GB**; **33B: 21 GB**; **13B: 10 GB**; **7B: 6 GB** |
| 7B LoRA weight size (example) | **~26 MB** LoRA params vs **~5,048 MB** 4-bit base; LoRA input grads **567 MB** (batch 1) before checkpointing → **~18 MB**/seq with checkpointing |

QLoRA places adapters on **all** transformer layers to match 16-bit FT quality (unlike original LoRA’s attention-only default) ([QLoRA paper](https://arxiv.org/abs/2305.14314)).

### Alignment stack: RLHF vs DPO (mechanism level)

**RLHF / InstructGPT** ([Ouyang et al., arXiv:2203.02155](https://arxiv.org/abs/2203.02155)):

1. **SFT** on human demonstrations → supervised policy.
2. **Reward model (RM)** on ranked completions under Bradley-Terry: \(p(y_w \succ y_l \mid x) = \sigma(r(x,y_w)-r(x,y_l))\).
3. **PPO** maximizes \(r_\phi\) subject to a **per-token KL penalty** to the SFT reference to limit reward hacking / mode collapse.

Headline InstructGPT result: **1.3B** InstructGPT preferred over **175B** GPT-3 on their prompt distribution despite **100×** fewer parameters ([InstructGPT abstract](https://arxiv.org/abs/2203.02155)).

**DPO** ([Rafailov et al., arXiv:2305.18290](https://arxiv.org/abs/2305.18290)): same KL-constrained reward objective, but reparameterizes the optimal policy in closed form so preference learning collapses to a **binary classification loss** on \((y_w, y_l)\) pairs — **no separate RM, no on-policy sampling loop during FT**. Loss (paper Eq. 7) uses temperature/KL strength \(\beta\); gradient upweights pairs where the implicit reward ranks the loser higher. Pipeline: build offline preference set → optimize \(\mathcal{L}_{\text{DPO}}\) vs frozen \(\pi_{\text{ref}}\) (usually SFT).

OpenAI hosted methods (for remaining FT customers) map to the same stack: **SFT**, **Vision SFT**, **DPO**, **RFT** (reasoning models only; grader-scored) ([OpenAI Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

### Hosted control vs data planes

| Vendor path | Control plane | Data / training plane | Serve plane |
| --- | --- | --- | --- |
| **OpenAI FT API** | Dashboard/API jobs; JSONL upload; method select (SFT/DPO/RFT) | OpenAI-managed training | Fine-tuned model IDs until base deprecation |
| **Anthropic Claude FT** | **Not** on Anthropic first-party API; **Amazon Bedrock** `CreateModelCustomizationJob` | S3 training/validation JSONL; private copy of base; VPC/KMS options | **Provisioned Throughput** required before invoke ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/); [AWS ML blog](https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/)) |
| **Open-weight LoRA/QLoRA** | Your job orchestrator (HF PEFT, Axolotl, etc.) | Your GPUs; adapters as artifacts | vLLM / TGI / merged weights ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)) |

Bedrock Claude 3 Haiku dataset caps (GA announcement): train **32–10,000** lines (≤**10 GB**); validation **32–1,000** lines (≤**1 GB**); train+val ≤**10,000** lines; **&lt;32k tokens**/entry ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).

---

## 2. Token Economics & NFR Metrics

### OpenAI fine-tuning pricing (verified 2026-09-30)

**Official Pricing page** ([developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing)) states the fine-tuning platform is **winding down** (closed to new users; existing users can still create jobs for a limited window). The **only fine-tuning rates listed on that page** at research time:

| Model | Training | Inference input / cached / output (per **1M** tokens) |
| --- | --- | --- |
| `o4-mini-2025-04-16` RFT | **$100.00 / hour** wall-clock of core training loop | **$4.00 / $1.00 / $16.00** |
| same + data sharing | **$100.00 / hour** | **$2.00 / $0.50 / $8.00** |

RFT grader tokens billed at the grader model’s normal inference rates ([OpenAI RFT billing](https://help.openai.com/en/articles/11323177); [Pricing](https://developers.openai.com/api/docs/pricing)).

> ⚠️ **Limited public data — OpenAI SFT training \$/1M tokens**: As of 2026-09-30 the live OpenAI Pricing page’s fine-tuning table **does not publish** per-1M-token **SFT/DPO training** rates (only RFT hourly). Third-party aggregators disagree on historical SFT training figures (e.g. \$25 vs other numbers for GPT-4.1-class). **Not fabricated here** — treat SFT training \$/1M as **account-dashboard / quote-dependent** until re-published officially.

OpenAI SFT guidance still documents supported bases `gpt-4.1` / `mini` / `nano` (2025-04-14 snapshots), **minimum 10** examples, recommend start at **50** well-crafted demos, improvements often seen at **50–100** ([Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)). Example context limits for FT examples: **65,536** tokens on gpt-4.1 family ([Fine-tuning best practices](https://developers.openai.com/api/docs/guides/fine-tuning-best-practices)).

### Anthropic / Bedrock economics

Anthropic first-party API: **no** self-serve fine-tuning product; customization of Claude is via **Bedrock** ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).

> ⚠️ **Limited public data — Claude Haiku FT training \$/1M tokens**: Bedrock Pricing documents customization \$/1M for some Meta Llama SKUs (e.g. Llama 2 13B train **\$1.49**/1M tokens; 70B **\$7.99**/1M; custom storage **\$1.95**/model/month) ([Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)), but **does not publish a clear public \$/1M-token training row for Anthropic Claude Haiku fine-tuning** in the fetched pricing page. Serving a customized Claude model requires **Provisioned Throughput** (hourly MU pricing; commitment discounts); AWS points customers to Bedrock Pricing / account team for Haiku custom rates ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/); [AWS ML blog](https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/)).

### Self-hosted PEFT cost anchors (not API list prices)

- Newsletter: PEFT LoRA run of a 7B-class model can land around **~$10/run** on commodity cloud GPU vs tens of thousands for naive full FT clusters ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)) — treat as order-of-magnitude ops anecdote, not a published SLA.
- Break-even heuristic from same source: above a few **hundred thousand queries/month**, self-hosted FT often beats flagship API+RAG; below that, dataset+train labor can exceed a year of API spend ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
- Latency NFR motivating FT of small models: fine-tuned **7B** TTFT **&lt;300 ms** on a single GPU cited for autocomplete-class products ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### Prompt-length economics (why FT pays at scale)

Hosted FT value props from OpenAI docs: more examples than fit in one context window; **shorter prompts** (fewer few-shots) → lower \$/request and latency; train proprietary patterns once instead of shipping them every call; distill large-model behavior into cheaper small models ([Model optimization](https://developers.openai.com/api/docs/guides/model-optimization); [Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).

---

## 3. Distributed Resilience & State

Fine-tuning is a **batch training system**, not a multi-agent runtime; resilience patterns differ.

### Job durability & reproducibility

- Newsletter production gates: locked dataset, locked seeds, versioned **model card**; ship only if general capability regresses **&lt;5%** on MMLU-Pro / MT-Bench and inference cost is **≥50%** below foundation baseline (gates described as product criteria, not universal industry SLAs) ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
- Bedrock jobs are **asynchronous**; monitor via `GetModelCustomizationJob`; optional **early stopping** on validation loss; outputs land in S3 ([Bedrock submit job](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html); [AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).
- QLoRA **Paged Optimizers** are an explicit resilience mechanism against activation/optimizer **memory spikes** that abort long-sequence steps ([QLoRA paper](https://arxiv.org/abs/2305.14314)).

### Checkpointing & adapter state

- LoRA state is the small \(A,B\) tensors (+ optional merge into \(W\)). Failure recovery = reload base + last adapter checkpoint; multi-task isolation = one adapter per task without cloning the full base ([LoRA paper](https://arxiv.org/abs/2106.09685)).
- Hosted OpenAI: job → resulting `ft:…` model ID; inference continues until **base model deprecation** even after FT platform wind-down ([OpenAI Pricing / Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

### Concurrency / rate limits during training

> ⚠️ **Limited public data available for this dimension.** Vendor FT APIs do not publish Temporal/Kafka-style orchestration SLAs for training jobs. Concurrent job quotas, TPM during training, and retry/backoff are account-specific; treat training as best-effort batch with status polling `[inferred from API job patterns]`.

### Inference resilience after FT

- Merged LoRA matches base serving path (no adapter depth latency — unlike Houlsby adapters, which LoRA paper measured adding **ms** on short GPT-2 online passes) ([LoRA paper Table 1](https://arxiv.org/abs/2106.09685)).
- Bedrock custom Claude: capacity reserved via **Provisioned Throughput** MUs — failure mode is under-provisioned MU exhaustion rather than on-demand soft throttles ([AWS ML blog](https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/)).

---

## 4. Enterprise Security & Governance

### Data residency & tenancy

- Newsletter drivers for open-weight FT: HIPAA charts, attorney-client material, GDPR/SOC2 customer records that **cannot** leave a VPC — keep train + serve inside customer auth/audit boundary ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
- Bedrock: separate **private copy** of the foundation model accessible only to the customer; optional **VPC config** and **KMS** on custom model; training data via customer-controlled S3 ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/); [CreateModelCustomizationJob](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateModelCustomizationJob.html)).
- OpenAI FT: proprietary data can be trained without embedding it in every prompt, but data is processed on OpenAI’s FT platform under their data/retention terms — wind-down reduces long-term viability of this path for greenfield programs ([Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

### License / ToS governance

Model selection must check **license fit**: Llama community license vs Apache-2.0 open weights vs OpenAI ToS hosting constraints ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### Audit & eval gates

- Versioned dataset + model card + eval harness before ship ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
- OpenAI: build **evals first**; holdout from same distribution as training; only then invest in FT ([Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning); [Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).
- OpenAI RFT: requires expert graders that **agree** on ideal outputs — governance of grader quality becomes part of the control plane ([Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

### PII / sandbox

> ⚠️ **Limited public data available for this dimension.** Papers and vendor FT guides do not standardize PII redaction pipelines inside the trainer. Enterprise practice is to scrub/tokenizeize before JSONL upload or keep training inside a VPC with DLP `[inferred]`. LoRA/QLoRA themselves add no sandbox isolation — isolation is an infra concern (containers, GPU tenants).

---

## 5. Production Failure Modes

| Failure | Mechanism | Mitigation |
| --- | --- | --- |
| **FT used as a knowledge dump** | Facts appearing once/twice do not override pretraining mass; model “forgets” or never learns the fact | Prefer RAG for freshness/facts; FT for repeated behavioral patterns ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)) |
| **Catastrophic forgetting / capability regression** | Full FT updates all weights; domain shift erases general skill | LoRA freezes base (low regression risk); eval gate **&lt;5%** regression on general benches before ship ([LoRA paper](https://arxiv.org/abs/2106.09685); [System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)) |
| **LoRA rank ceiling** | Fixed \(r\) cannot encode large domain shifts; more data stops helping | Raise \(r\), adapt more modules (QLoRA all-layers), or escalate to full FT ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [QLoRA paper](https://arxiv.org/abs/2305.14314)) |
| **Non-representative train set** | Train formatting ≠ production (esp. RAG context missing in FT examples) | Fine-tune **with RAG-shaped examples** if prod uses retrieval ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)) |
| **Reward hacking / KL collapse (RLHF)** | PPO over-optimizes RM; diversity collapses | Per-token KL to SFT ref ([InstructGPT](https://arxiv.org/abs/2203.02155)); or switch to DPO’s classification objective ([DPO](https://arxiv.org/abs/2305.18290)) |
| **DPO distribution shift** | Preference data from a different policy than \(\pi_{\text{ref}}\) | Later studies (e.g. Xu et al. 2024) show DPO more shift-sensitive than well-tuned PPO — keep preference sampling on-policy to the reference `[inferred ops rule from literature debate]` |
| **Base too weak** | Newsletter probe: if base answers **&lt;~50%** of a 20-question domain quiz, FT will not install missing knowledge | Change base model or use continued pretraining / RAG ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)) |
| **Hosted platform sunset** | OpenAI FT closed to new orgs; job creation ends for remaining customers on published wind-down path | Plan open-weight or Bedrock alternatives; keep inference migration path ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)) |
| **Bedrock serve misconfig** | Custom model trained but not usable on-demand without Provisioned Throughput | Budget MU hours before go-live ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)) |
| **Overfit / style collapse** | Small noisy SFT sets | Start **50** high-quality demos; holdout eval; early stopping (Bedrock) ([Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning); [AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)) |

QLoRA empirical note: **data quality ≫ dataset size** — **9k** OASST1 beat **450k** FLAN-v2 subsample on chatbot Elo in their study ([QLoRA paper](https://arxiv.org/abs/2305.14314)).

---

## 6. Enterprise System Design Scenarios

### Decision matrix: Prompt vs RAG vs Fine-tune

OpenAI accuracy guide frames two axes ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)):

| Axis | Symptom | Lever |
| --- | --- | --- |
| **Context** | Missing / stale / proprietary facts | RAG (or tools) — **not** FT |
| **Behavior** | Inconsistent format, tone, instruction-following | Prompt → few-shot → **FT** |

**When NOT to fine-tune** (synthesis of OpenAI + newsletter; RAG/prompt details intentionally shallow):

1. Prompting (+ few-shot) already hits the eval bar ([Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).
2. Failures are **knowledge/context** failures → retrieval / tools first ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).
3. You lack a holdout eval and ≥**50** representative demos ([Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning)).
4. You are “reaching for FT because prompting felt inconvenient” ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
5. Greenfield OpenAI FT access is unavailable due to platform wind-down ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)).

**When FT is warranted**: durable house style (clinical SOAP, legal memo); latency/cost via small specialized model (Copilot-class TTFT); privacy (weights stay in VPC); behaviors that need more examples than a context window ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

**Stacking**: FT + RAG is valid when you need both baked-in behavior **and** fresh facts — fine-tune on examples that **include retrieved context** so the model learns to use it ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).

### Architecture patterns

**A. Open-weight LoRA/QLoRA product**

1. Domain probe on 7B–8B base → reject if &lt;~50% ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
2. SFT LoRA (\(r=8\)–\(16\)) or QLoRA if VRAM ≤48 GB for 33B/65B ([LoRA](https://arxiv.org/abs/2106.09685); [QLoRA](https://arxiv.org/abs/2305.14314)).
3. Eval harness + optional DPO on residual prefs ([DPO](https://arxiv.org/abs/2305.18290)).
4. Serve merged weights or hot-swap adapters (multi-tenant: many **35 MB**-class adapters on one base) ([LoRA paper](https://arxiv.org/abs/2106.09685)).

**B. Hosted OpenAI (legacy / existing customers)**

SFT or DPO on gpt-4.1 family; RFT on `o4-mini` at **\$100/h** + grader tokens; plan migration before Jan 2027 job-creation cutoff referenced in deprecation communications ([Pricing](https://developers.openai.com/api/docs/pricing); [Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

**C. Anthropic Claude via Bedrock**

Fine-tune Claude 3 Haiku (JSONL messaging format) → Provisioned Throughput → invoke. Distill from larger Claude (Sonnet/Opus) into Haiku training data as a cost pattern ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).

### Capacity / adapter sizing cheat sheet

| Artifact | Size / count | Source |
| --- | --- | --- |
| GPT-3 175B LoRA (\(r=4\), \(W_q+W_v\)) | **~35 MB** adapter vs **~350 GB** full | [LoRA](https://arxiv.org/abs/2106.09685) |
| GPT-3 175B LoRA param budget | **18M** @ \(r=8\) one matrix type | [LoRA](https://arxiv.org/abs/2106.09685) |
| LLaMA-7B LoRA weights (QLoRA study) | **~26 MB** | [QLoRA](https://arxiv.org/abs/2305.14314) |
| QLoRA 65B train | **&lt;48 GB** vs **&gt;780 GB** full | [QLoRA](https://arxiv.org/abs/2305.14314) |
| OpenAI RFT train | **\$100 / hour** | [OpenAI Pricing](https://developers.openai.com/api/docs/pricing) |

### Trade-off matrix

| Approach | Cost to start | Latency | Ops complexity | Security | Scalability of variants |
| --- | --- | --- | --- | --- | --- |
| Prompt / few-shot | Lowest | Higher token latency | Low | Data in prompts | Poor (prompt bloat) |
| RAG (+ prompt) | Medium (index) | Retrieval + gen | Medium | Corpus ACLs | High for facts |
| LoRA/QLoRA self-host | GPU + dataset | Best for small models | High | Full VPC control | Excellent (swap adapters) |
| OpenAI FT | Training \$ + markup inference | API TTFT | Medium | Vendor boundary | Medium (platform wind-down) |
| Bedrock Claude FT | Train + **PT** hourly | Provisioned | Medium–High | AWS tenancy + private model copy | Medium (MU capacity) |

---

## Sources

- [1] https://newsletter.systemdesign.one/p/fine-tuning-ai-models — System Design Newsletter #162 (Bouchard & Kim): FT regimes, LoRA/QLoRA framing, prompt/RAG/FT decision, memory heuristics
- [2] https://arxiv.org/abs/2106.09685 — Hu et al., LoRA (trainable param formula, 10,000× / 35 MB / 1.2TB→350GB)
- [3] https://arxiv.org/pdf/2106.09685 — LoRA PDF (same paper, tables)
- [4] https://arxiv.org/abs/2305.14314 — Dettmers et al., QLoRA (NF4, DQ, paged optimizers, &lt;48 GB / &gt;780 GB)
- [5] https://arxiv.org/pdf/2305.14314 — QLoRA PDF
- [6] https://arxiv.org/abs/2305.18290 — Rafailov et al., DPO (Bradley-Terry → classification loss, β KL)
- [7] https://arxiv.org/abs/2203.02155 — Ouyang et al., InstructGPT / RLHF (SFT → RM → PPO+KL)
- [8] https://developers.openai.com/api/docs/guides/model-optimization — OpenAI FT methods (SFT/DPO/RFT), wind-down notice
- [9] https://developers.openai.com/api/docs/guides/supervised-fine-tuning — SFT data minima (10 / 50–100), distillation pattern
- [10] https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy — Prompt vs RAG vs FT decision matrix
- [11] https://developers.openai.com/api/docs/pricing — Verified FT pricing (o4-mini RFT \$100/h); platform wind-down
- [12] https://help.openai.com/en/articles/11323177 — RFT billing (\$100/h + grader tokens)
- [13] https://developers.openai.com/api/docs/guides/fine-tuning-best-practices — Example context length caps
- [14] https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/ — Claude 3 Haiku FT GA, dataset caps, Provisioned Throughput
- [15] https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/ — Bedrock FT workflow, PT pricing factors
- [16] https://docs.aws.amazon.com/bedrock/latest/APIReference/API_CreateModelCustomizationJob.html — Bedrock customization API
- [17] https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html — Job submit / async monitoring
- [18] https://aws.amazon.com/bedrock/pricing/ — Bedrock customization pricing (Llama \$/1M train published; Claude Haiku FT \$/1M not clearly listed)
