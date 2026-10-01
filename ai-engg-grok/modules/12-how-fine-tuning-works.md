# Module 12 — How Fine Tuning Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 12 (model customization after retrieval/index layers in 06/11 and agent runtime topics)  
**Grounded in**: `research/12-how-fine-tuning-works.md` (18 sources, 2026-09-30)

Fine-tuning shifts a foundation model’s **defaults** (style, format, reasoning patterns, refusal calibration) toward a task-shaped dataset. It is not a substitute for installing fresh facts — operating rule: **fine-tune for behavior, retrieve for knowledge** ([System Design Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [OpenAI Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)). This module covers SFT / PEFT (LoRA, QLoRA), preference alignment (RLHF, DPO), hosted surfaces (OpenAI, Bedrock Claude), and the FT-vs-prompt-vs-RAG lever choice.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  method select (SFT / DPO / RFT) · job submit / cancel   │
                         │  dataset version lock · eval gates · grader policy       │
                         │  adapter promote / rollback · model-card approval        │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ FT Job     │  │ Eval /     │  │ Policy / RBAC      │  │
                         │  │ Orchestr.  │  │ Holdout    │  │ adapter ACL        │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  train loop: forward · loss · adapter update (LoRA/QLoRA)│
                         │  serve path: base W₀ + ΔW (or merged) → tokens           │
                         │  async job workers · inference replicas · hot-swap       │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  MCP complete_ft     │  │  JSONL datasets     │  │  job status      │
              │  train-job client    │  │  adapter ckpts A,B  │  │  loss / eval     │
              │  gateway tenant inj. │  │  model registry     │  │  p50/p95/p99     │
              │  PII scrub sidecar   │  │  lineage / model   │  │  breaker · corr. │
              │                      │  │    cards            │  │  MU / TPM        │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | Role in fine-tuning | Concrete components |
| --- | --- | --- |
| **CONTROL PLANE** | Method select, job lifecycle, dataset lock, eval/promote gates, adapter ACL | OpenAI FT dashboard/API; Bedrock `CreateModelCustomizationJob`; internal job orchestrator + model card ([OpenAI Model optimization](https://developers.openai.com/api/docs/guides/model-optimization); [Bedrock submit job](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html)) |
| **DATA PLANE** | Training steps (frozen \(W_0\), trainable adapters) and inference with selected adapter/merged weights | GPU trainers (HF PEFT / Axolotl); OpenAI-managed training; Bedrock private base copy; vLLM/TGI serve ([LoRA](https://arxiv.org/abs/2106.09685); [QLoRA](https://arxiv.org/abs/2305.14314)) |
| **PERSISTENCE** | Versioned JSONL, adapter checkpoints \((A,B)\), registry IDs, immutable lineage | S3 train/val; LoRA ~35 MB adapters; `ft:…` model IDs; model cards ([LoRA paper](https://arxiv.org/abs/2106.09685); [AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)) |
| **TOOL PROXIES** | Agent/app-facing invoke of FT models; train-job clients; PII scrub before upload | MCP `complete_ft` tool; Bedrock/OpenAI SDKs; DLP sidecar ([inferred] enterprise pattern from research §4) |
| **TELEMETRY** | Job status, loss/eval, inference latency, breaker state, correlation IDs, MU/TPM | `GetModelCustomizationJob`; app spans; provisioned-throughput meters ([Bedrock submit job](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html)) |

### End-to-end request-flow narrative

**Training path (dataset → train job → adapter registry → inference)**

1. **Dataset ingress** — Curate instruction pairs (SFT) or preference triples (DPO/RLHF). Lock version, seed, and holdout. Run **PII detect → redact → audit** in the tool-proxy sidecar *before* any trainer sees bytes ([inferred] enterprise practice; research flags no standardized in-trainer PII pipeline).
2. **CONTROL PLANE submit** — Choose method (SFT / DPO / RFT). OpenAI: JSONL upload + job create. Bedrock: S3 URIs + `CreateModelCustomizationJob` (async). Self-host: enqueue Temporal/K8s job with dataset digest + hyperparams ([OpenAI Model optimization](https://developers.openai.com/api/docs/guides/model-optimization); [Bedrock submit job](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html)).
3. **DATA PLANE train** — Freeze \(W_0\); optimize LoRA \(\Delta W = BA\) (or QLoRA 4-bit base + BF16 adapters). Checkpoint adapters to PERSISTENCE on interval; optional early stop on validation loss ([LoRA](https://arxiv.org/abs/2106.09685); [QLoRA](https://arxiv.org/abs/2305.14314); [AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).
4. **Adapter registry** — On success, register artifact: OpenAI `ft:…` ID; Bedrock custom model ARN; self-host adapter ID → \((A,B)\) object key + base digest. CONTROL PLANE runs eval gate (e.g. general capability regresses **&lt;5%** on MMLU-Pro / MT-Bench before promote) ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).
5. **Inference promote** — Serve plane loads base + adapter (or merged \(W = W_0 + BA\)). Bedrock custom Claude requires **Provisioned Throughput** before invoke ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).
6. **Online path** — Client / MCP tool proxy calls FT model with correlation ID; TELEMETRY records TTFT/tokens/breaker; on FT outage fall back **FT → base → prompt template** (Part 4).

---

## Part 2 — Core Mechanics & Algorithms

### Three training regimes

| Regime | Data shape | Typical scale | What it changes |
| --- | --- | --- | --- |
| **Continued pretraining** | Raw domain text | Hundreds of millions → billions of tokens | Domain vocabulary / next-token priors |
| **Supervised fine-tuning (SFT)** | Instruction → desired response | ~1k–50k pairs common in practice writeups | Style, format, reasoning imitation |
| **Preference tuning (RLHF / DPO)** | Prompt + preferred vs rejected | ~500–5k preference pairs in practice writeups | Tone, calibrated refusals, ranking-sensitive behaviors |

Practical order: **SFT first → ship → DPO/RLHF** for residual preference failures; continued pretraining only if the base fails a domain-vocabulary probe ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### Memory wall (why PEFT dominates)

For a **7B** model at FP16: parameters ~**14 GB**; gradients roughly double (~**28 GB**); Adam-style optimizer states push total toward **~60–80 GB** — beyond consumer 16–24 GB GPUs ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### LoRA (Hu et al., arXiv:2106.09685)

Freeze pretrained \(W_0 \in \mathbb{R}^{d \times k}\). Learn \(\Delta W = BA\) with \(B \in \mathbb{R}^{d \times r}\), \(A \in \mathbb{R}^{r \times k}\), \(r \ll \min(d,k)\).

**Forward** (scaled by \(\alpha/r\)):

\[
h = W_0 x + \frac{\alpha}{r} B A x
\]

Init: Gaussian \(A\), zero \(B\) so \(\Delta W = 0\) at start. Trainable count when adapting \(\hat{L}\) matrices: \(|\Theta| = 2 \times \hat{L} \times d_{\text{model}} \times r\) ([LoRA §4](https://arxiv.org/abs/2106.09685)).

| Metric (GPT-3 175B) | Full FT (Adam) | LoRA |
| --- | --- | --- |
| Trainable params | Full 175B | Up to **~10,000×** fewer; as low as **~0.01%** of \(\lvert\Phi_0\rvert\) |
| Training VRAM | **1.2 TB** | **~350 GB** (~**3×** reduction) |
| Adapter ckpt (\(r=4\), \(W_q+W_v\)) | Full ~**350 GB** | **~35 MB** (~**10,000×** smaller) |
| 100-task storage | ~**35 TB** full copies | ~**350 GB + 100×35 MB ≈ 354 GB** |

Merge \(W = W_0 + BA\) → **zero added inference latency** vs full FT; task switch by swapping adapters. Newsletter framing: rank typically **8 or 16**; trains **&lt;1%** of params; recovers **~90–95%** of full-FT quality on many tasks; hard domain shifts can hit rank capacity ([LoRA](https://arxiv.org/abs/2106.09685); [Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### QLoRA (Dettmers et al., arXiv:2305.14314)

Freeze a **4-bit** quantized base; backprop into **BF16 LoRA** adapters. NF4 + double quantization (~**0.373 bits/param** saved; ~**3 GB** on 65B) + paged optimizers for OOM spikes.

| Claim | Number |
| --- | --- |
| Full 16-bit FT of LLaMA **65B** | **&gt;780 GB** GPU memory |
| QLoRA FT of 65B | **&lt;48 GB** (single GPU) |
| Guanaco 65B train time | **~24 h** on one professional GPU → **99.3%** of ChatGPT on Vicuna (GPT-4 judged) |
| Deployed footprint | 65B: **41 GB**; 33B: **21 GB**; 13B: **10 GB**; 7B: **6 GB** |

QLoRA places adapters on **all** transformer layers to match 16-bit FT quality ([QLoRA](https://arxiv.org/abs/2305.14314)).

### Alignment: RLHF vs DPO

**RLHF / InstructGPT** ([Ouyang et al., arXiv:2203.02155](https://arxiv.org/abs/2203.02155)):

1. SFT on demonstrations → supervised policy.  
2. Reward model under Bradley-Terry: \(p(y_w \succ y_l \mid x) = \sigma(r(x,y_w)-r(x,y_l))\).  
3. PPO maximizes \(r_\phi\) with **per-token KL** to the SFT reference.

Headline: **1.3B** InstructGPT preferred over **175B** GPT-3 on their prompt distribution ([InstructGPT](https://arxiv.org/abs/2203.02155)).

**DPO** ([Rafailov et al., arXiv:2305.18290](https://arxiv.org/abs/2305.18290)): same KL-constrained objective, closed-form policy → **binary classification** on \((y_w, y_l)\) with temperature \(\beta\); **no separate RM, no on-policy sampling loop during FT**.

### Job state machine

```
                    ┌──────────────┐
                    │  dataset     │──PII scrub──► locked JSONL + digest
                    │  staged      │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
           ┌───────►│  queued      │◄──── rate-limit / quota
           │        └──────┬───────┘
           │               ▼
           │        ┌──────────────┐
           │        │  running     │──checkpoint adapters──► registry (draft)
           │        └──────┬───────┘
           │         ┌─────┴─────┐
           │         ▼           ▼
           │  ┌──────────┐ ┌──────────┐
           │  │ failed   │ │ succeeded│──eval gate──► promoted | rejected
           │  └────┬─────┘ └────┬─────┘
           │       │            ▼
           │       │     ┌──────────┐
           └───────┴─────│ serving  │──FT → base → prompt fallback
                         └──────────┘
```

**Invariants**: (1) train never mutates the frozen base blob in place without a new lineage ID; (2) promote requires holdout + regression gate; (3) inference selection is a registry pointer, not a side-effect of the last train job.

---

## Part 3 — Token Economics & NFR Analysis

### Inference cost: `$ per 1k runs` (fine-tuned vs base)

Define **1 run** = one online completion (no tool loop). Use the **only** OpenAI fine-tuning inference rates published on the Pricing page at research time: `o4-mini-2025-04-16` **RFT** ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing), verified 2026-09-30).

**Labeled token / price assumptions**

| Parameter | Value | Label |
| --- | --- | --- |
| FT model SKU | `o4-mini-2025-04-16` RFT (no data sharing) | **[verified]** Pricing page |
| FT input / cached / output | **$4.00 / $1.00 / $16.00** per **1M** tokens | **[verified]** |
| Same + data sharing | **$2.00 / $0.50 / $8.00** per 1M | **[verified]** |
| Input tokens / run | \(T_{\text{in}} = 800\) | **[assumed]** short task prompt after FT (few-shots removed) |
| Output tokens / run | \(T_{\text{out}} = 200\) | **[assumed]** |
| Cache hit rate on input | \(h = 0\) | **[assumed]** worst case; set \(h>0\) to credit cached tier |
| Base comparator | Same token shape billed at **half** the no-share FT rates (\(T_{\text{in}}, T_{\text{out}}\) identical) | **[illustrative]** — live base `o4-mini` list prices move; purpose is FT markup vs a same-shape baseline, not a frozen base SKU quote |

\[
\begin{aligned}
C_{\text{run}} &= \frac{T_{\text{in}}}{10^6}\bigl((1-h)\,P_{\text{in}} + h\,P_{\text{cache}}\bigr)
  + \frac{T_{\text{out}}}{10^6}\,P_{\text{out}} \\
C_{\$/1k} &= 1000 \times C_{\text{run}}
\end{aligned}
\]

**Arithmetic — FT (no data sharing), \(h=0\)**:

\[
\begin{aligned}
C_{\text{run}}^{\text{FT}} &= \frac{800}{10^6}\cdot 4.00 + \frac{200}{10^6}\cdot 16.00
  = 0.0032 + 0.0032 = \$0.0064 \\
C_{\$/1k}^{\text{FT}} &= 1000 \times 0.0064 = \$6.40
\end{aligned}
\]

**Arithmetic — illustrative base at half rates** (\(P_{\text{in}}=\$2\), \(P_{\text{out}}=\$8\)):

\[
\begin{aligned}
C_{\text{run}}^{\text{base}} &= \frac{800}{10^6}\cdot 2.00 + \frac{200}{10^6}\cdot 8.00
  = 0.0016 + 0.0016 = \$0.0032 \\
C_{\$/1k}^{\text{base}} &= 1000 \times 0.0032 = \$3.20
\end{aligned}
\]

| Path | \$ / run | **\$ / 1k runs** | Notes |
| --- | --- | --- | --- |
| FT RFT (`o4-mini`, no share) | $0.0064 | **$6.40** | [verified] prices × [assumed] tokens |
| Illustrative base (½ rates) | $0.0032 | **$3.20** | Same \(T_{\text{in}}, T_{\text{out}}\) |
| FT + data sharing | $0.0032 | **$3.20** | \(800/10^6·2 + 200/10^6·8\) |

FT value props that change the formula in production: **shorter prompts** (drop few-shots) and distill into smaller models — both cut \(T_{\text{in}}\) and often model tier ([OpenAI Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).

### Training cost (separate from inference)

| Item | Rate | Status |
| --- | --- | --- |
| OpenAI **RFT** core training loop | **$100.00 / hour** wall-clock | **[verified]** Pricing page + RFT billing help ([Pricing](https://developers.openai.com/api/docs/pricing); [RFT billing](https://help.openai.com/en/articles/11323177)) |
| RFT grader tokens | Graded at grader model’s normal inference rates | **[verified]** |
| Self-host PEFT anecdote | ~**$10/run** for 7B-class LoRA on commodity GPU | **[ops anecdote]** Newsletter #162 — not a published SLA |

> ⚠️ **Gap — live OpenAI SFT/DPO training \$/1M tokens not published**: As of 2026-09-30 the Pricing fine-tuning table lists **only** RFT hourly (\$100/h), not per-1M-token SFT/DPO training rates. Third-party aggregators disagree on historical figures. **Not fabricated here** — treat SFT training \$/1M as account-dashboard / quote-dependent ([OpenAI Pricing](https://developers.openai.com/api/docs/pricing)).

> ⚠️ **Gap — Bedrock Claude Haiku FT \$/1M train**: Bedrock Pricing publishes Llama customization \$/1M (e.g. 13B **\$1.49**/1M; 70B **\$7.99**/1M) but **not** a clear public Claude Haiku FT \$/1M row ([Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)).

**Training-job duration note**: QLoRA Guanaco **65B** ≈ **~24 h / professional GPU** ([QLoRA](https://arxiv.org/abs/2305.14314)). Budget RFT as \(C_{\text{train}} \approx \$100 \times t_{\text{hours}}\) plus grader tokens — independent of the inference \$/1k table above.

### Latency SLA targets (inference)

> ⚠️ **Gap**: Research cites no universal vendor contractual **p50 / p95 / p99** for hosted fine-tuned completion e2e. Newsletter cites fine-tuned **7B** TTFT **&lt;300 ms** on a single GPU for autocomplete-class products ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)). Merged LoRA adds **zero** adapter depth vs base ([LoRA](https://arxiv.org/abs/2106.09685)).

**[inferred] inference latency budget (ms)** — hosted FT completion, warm replica, short prompt after FT:

| Stage | p50 | p95 | p99 | Mitigation |
| --- | --- | --- | --- | --- |
| Gateway + auth | 5 | 15 | 40 | Edge terminate TLS; connection reuse |
| Queue / admission | 2 | 20 | 80 | Shed load; provisioned capacity (Bedrock MU) |
| Prefill (TTFT) | 120 | 280 | 450 | Shorter prompts post-FT; small specialized model; streaming |
| Decode (200 tok @ ~40 tok/s) | 200 | 350 | 600 | Cap `max_tokens`; speculative decode where available |
| **E2E sum** | **327** | **665** | **1170** | Additive stages |

\[
\begin{aligned}
T_{\text{e2e}}^{\text{p50}} &\approx 5 + 2 + 120 + 200 = 327\text{ ms} \\
T_{\text{e2e}}^{\text{p95}} &\approx 15 + 20 + 280 + 350 = 665\text{ ms} \\
T_{\text{e2e}}^{\text{p99}} &\approx 40 + 80 + 450 + 600 = 1170\text{ ms}
\end{aligned}
\]

Align product SLO to newsletter **&lt;300 ms TTFT** for self-hosted 7B autocomplete by shrinking prefill (local GPU, tiny context) rather than assuming hosted p50 equals that figure.

### Throughput & back-pressure

| Surface | Capacity model | Back-pressure |
| --- | --- | --- |
| **Bedrock custom Claude** | **Provisioned Throughput** (hourly MU); model unusable on-demand without PT | MU exhaustion → throttle/reject; scale MU commitment before launch ([AWS GA](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/); [AWS ML blog](https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/)) |
| **OpenAI FT inference** | Account TPM/RPM on `ft:` models | 429 → retry+jitter; circuit breaker → base fallback |
| **Self-host adapters** | GPU batch concurrency; many **~35 MB** adapters on one base | Queue depth + admission control; merge hot adapters to cut swap cost ([LoRA](https://arxiv.org/abs/2106.09685)) |

Training concurrency/quotas are account-specific — treat train as best-effort batch with status polling ([inferred] from async job APIs; research ⚠️ limited public Temporal/Kafka SLAs).

### NFR trade-offs

| NFR | Target / posture | FT-specific note |
| --- | --- | --- |
| **Availability** | Inference: design for FT outage via fallback chain; Train: async jobs are best-effort | Prefer multi-adapter + base path over single `ft:` ID |
| **RPO (adapters)** | Adapter checkpoint interval → RPO ≈ last successful ckpt (minutes–hours of train progress) | LoRA state is small \((A,B)\); reload base + last ckpt ([LoRA](https://arxiv.org/abs/2106.09685)) |
| **RTO (adapters)** | Registry rollback to previous promoted adapter: minutes; full retrain: hours–days (QLoRA 65B ~24 h/GPU) | Keep N−1 promoted adapter immutable |
| **Compliance** | HIPAA/GDPR/SOC2 often force VPC train+serve (open-weight) or Bedrock private model copy + KMS/VPC | OpenAI FT processes data on vendor platform; wind-down reduces greenfield viability ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [AWS GA](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)) |

### Explicit lever trade-off: fine-tune vs prompt vs RAG

OpenAI accuracy guide axes ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)):

| Axis | Symptom | Lever |
| --- | --- | --- |
| **Context** | Missing / stale / proprietary facts | **RAG** (or tools) — not FT |
| **Behavior** | Inconsistent format, tone, instruction-following | Prompt → few-shot → **FT** |

| Approach | Cost to start | Latency | Ops | Security | Scalability of variants |
| --- | --- | --- | --- | --- | --- |
| Prompt / few-shot | Lowest | Higher token latency | Low | Data in prompts | Poor (prompt bloat) |
| RAG (+ prompt) | Medium (index) | Retrieval + gen | Medium | Corpus ACLs | High for facts |
| LoRA/QLoRA self-host | GPU + dataset | Best for small models | High | Full VPC control | Excellent (swap adapters) |
| Hosted FT | Train \$ + inference markup | API TTFT | Medium | Vendor boundary | Medium (OpenAI wind-down; Bedrock MU) |

**When NOT to fine-tune**: prompting already hits the eval bar; failures are knowledge/context; &lt;50 representative demos / no holdout; “FT because prompting felt inconvenient”; greenfield OpenAI FT unavailable ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models); [Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning); [Pricing](https://developers.openai.com/api/docs/pricing)).

**Stacking**: FT + RAG when you need baked-in behavior **and** fresh facts — fine-tune on examples that **include retrieved context** ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).

---

## Part 4 — Distributed Resilience & Security

### Training job durability (checkpoint adapters)

- Persist adapter tensors \((A,B)\) (+ optimizer pages if QLoRA paged optimizers spill) on a fixed interval; job resume = reload base digest + latest adapter ckpt ([LoRA](https://arxiv.org/abs/2106.09685); [QLoRA](https://arxiv.org/abs/2305.14314)).
- Bedrock: async job + S3 outputs; poll `GetModelCustomizationJob`; optional early stopping ([Bedrock submit job](https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html)).
- Hosted OpenAI: job → `ft:…` ID; inference may continue until **base deprecation** even as FT platform winds down ([Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)).
- Production gates (newsletter product criteria, not universal SLAs): locked dataset/seeds/model card; ship only if general regression **&lt;5%** and inference cost **≥50%** below foundation baseline ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

### Failure taxonomy

| Failure | Class | Mechanism | Mitigation |
| --- | --- | --- | --- |
| **Poison / non-representative data** | Permanent (data) | Train formatting ≠ prod; injected hostile demos | PII+toxicity screens; holdout; version pin; canary adapter |
| **Catastrophic forgetting** | Permanent (model) | Full FT erases general skill; domain shift | Prefer LoRA (frozen base); &lt;5% regression gate ([LoRA](https://arxiv.org/abs/2106.09685); [Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)) |
| **FT as knowledge dump** | Permanent (design) | Facts once/twice never override pretraining mass | RAG for facts; FT for repeated behavior |
| **LoRA rank ceiling** | Permanent (capacity) | Fixed \(r\) cannot encode large shifts | Raise \(r\), all-layer QLoRA, or escalate FT |
| **Train API / GPU transient** | Transient | 429, preempt, OOM spike | Retry+jitter; QLoRA paged optimizers; breaker on train client |
| **Reward hacking (RLHF)** | Permanent (align) | PPO overfits RM | Per-token KL; or DPO classification objective |
| **Bedrock serve misconfig** | Permanent (ops) | Custom model without Provisioned Throughput | Budget MU hours pre-launch |
| **Platform sunset** | Permanent (vendor) | OpenAI FT closed to new orgs | Migration path to open-weight / Bedrock |

**Idempotency**: submit train jobs with a client `Idempotency-Key` / job name bound to `dataset_digest + hparams_hash` so retries do not spawn duplicate billable runs ([inferred] from async job patterns).

### Circuit breaker on the train API

Train clients use closed → open → half-open around create/status endpoints: after \(N\) consecutive transient failures, open the breaker, fail fast to “queue locally / alert”, probe after cooldown. Do **not** hammer create-job under 429 (amplifies quota burn). Inference breakers are separate and shorter-cooldown (Part 5 code).

### Fallback chain (inference)

```
FT model (primary adapter / ft: ID)
        │  timeout · 5xx · breaker open · quality canary fail
        ▼
Base model (same family, no adapter)
        │  still failing / budget exceed
        ▼
Deterministic prompt template (no LLM) or cached last-good structured fill
```

Merged LoRA shares the base serving path (no Houlsby-style adapter latency) ([LoRA Table 1](https://arxiv.org/abs/2106.09685)).

### Enterprise security

**1. Dataset PII: detect → redact → audit BEFORE training**

```
raw demos ──► detect (regex/NER/DLP) ──► redact/tokenizeize ──► audit log
                      │                         │                    │
                      └──── reject if residual high-risk PII ────────┘
                                         ▼
                                   locked JSONL (digest) → trainer
```

Papers/vendor guides do not standardize in-trainer PII — scrub before JSONL upload or keep train inside VPC with DLP ([research ⚠️](../research/12-how-fine-tuning-works.md); [inferred] enterprise practice).

**2. Adapter access RBAC**

Least privilege: `ft:train`, `ft:promote`, `ft:invoke` as separate roles. Multi-tenant: one adapter per tenant/task; gateway injects allowed `adapter_id` — clients never pass arbitrary registry keys ([LoRA multi-task isolation](https://arxiv.org/abs/2106.09685); [inferred] RBAC mapping).

**3. Zero-Trust when the FT model is called via MCP tool**

- MCP tool `complete_ft` authenticates caller (mTLS / signed token), authorizes tenant→adapter binding, and never embeds long-lived vendor keys in the agent prompt.  
- Tool schema exposes only `prompt`, `adapter_alias`, `max_tokens` — not raw weight paths.  
- Egress from tool runtime allowlists the inference endpoint; dataset buckets are **not** reachable from the invoke path (train plane ≠ serve plane).

**4. Immutable training lineage**

Append-only record: `dataset_digest`, `base_model_id`, `method`, `hparams`, `adapter_digest`, `eval_report_id`, `promoted_by`, `ts`. Model card + eval harness required before ship ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)). Bedrock private model copy + optional KMS/VPC for residency ([AWS GA](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)).

---

## Part 5 — Production Enterprise Code

Runnable, deterministic demo: tiny LoRA-shaped forward (low-rank delta) plus a train-job / inference client with retries+jitter, circuit breaker, FT→base→prompt fallback, and correlation IDs. **No API keys, no network, no real training.**

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


# ---------------------------------------------------------------------------
# Tiny LoRA-shaped forward: h = W0 x + (alpha/r) B A x
# ---------------------------------------------------------------------------

def matvec(matrix: Sequence[Sequence[float]], vec: Sequence[float]) -> list[float]:
    return [sum(row[j] * vec[j] for j in range(len(vec))) for row in matrix]


@dataclass
class LoRALinear:
    """Frozen W0 plus low-rank adapters A (r×k), B (d×r)."""

    w0: list[list[float]]
    a: list[list[float]]
    b: list[list[float]]
    alpha: float = 16.0

    @property
    def rank(self) -> int:
        return len(self.a)

    def forward(self, x: Sequence[float]) -> list[float]:
        base = matvec(self.w0, x)
        # A: r×k, B: d×r
        ax = matvec(self.a, x)
        bax = matvec(self.b, ax)
        scale = self.alpha / self.rank
        return [base[i] + scale * bax[i] for i in range(len(base))]


def make_demo_lora(seed: int = 0) -> LoRALinear:
    rng = random.Random(seed)
    d, k, r = 4, 4, 2
    w0 = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(d)]
    a = [[rng.uniform(-0.1, 0.1) for _ in range(k)] for _ in range(r)]
    # Zero-B init ⇒ ΔW=0 at start (LoRA paper); we set a small trained B for demo.
    b = [[rng.uniform(-0.05, 0.05) for _ in range(r)] for _ in range(d)]
    return LoRALinear(w0=w0, a=a, b=b, alpha=16.0)


# ---------------------------------------------------------------------------
# Failures, retries with full jitter, circuit breaker
# ---------------------------------------------------------------------------

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
            delay = delay * (0.5 + rng.random())  # full jitter in [0.5, 1.5)×
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
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.time()


# ---------------------------------------------------------------------------
# Fake train API + inference backends (deterministic, injectable faults)
# ---------------------------------------------------------------------------

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
    path: str
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
        # Map prompt → unit vector in R^4, run LoRA, emit deterministic token-ish text.
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


# ---------------------------------------------------------------------------
# Client: train submit (breaker) + inference fallback chain
# ---------------------------------------------------------------------------

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
                    text=text,
                    path="ft",
                    correlation_id=cid,
                    breaker=self.infer_breaker.state.value,
                    degraded=False,
                )
            except FTError:
                self.infer_breaker.record_failure()
                LOG.warning("ft_failed_fallback_base", extra={"correlation_id": cid})

        # Secondary: base model
        try:
            text = self.inference.complete_base(prompt)
            return CompleteResult(
                text=text,
                path="base",
                correlation_id=cid,
                breaker=self.infer_breaker.state.value,
                degraded=True,
            )
        except FTError:
            LOG.warning("base_failed_fallback_template", extra={"correlation_id": cid})

        # Tertiary: deterministic prompt template
        return CompleteResult(
            text=prompt_template_fallback(prompt),
            path="prompt_template",
            correlation_id=cid,
            breaker=self.infer_breaker.state.value,
            degraded=True,
        )


def main() -> None:
    lora = make_demo_lora(seed=42)
    x = [0.5, 0.0, 0.0, 0.0]
    h0 = matvec(lora.w0, x)
    h1 = lora.forward(x)
    assert len(h1) == 4
    # With non-zero B, LoRA output differs from pure W0 (low-rank delta applied).
    assert h1 != h0

    api = FakeTrainAPI()
    api.inject_create_failures(2)  # two transient 429s then success
    client = FTClient(train_api=api, inference=FakeInference(lora), rng_seed=1)
    job = client.submit_train(
        ["user: hi\nassistant: hello", "user: bye\nassistant: goodbye"],
        correlation_id="corr-train-demo",
    )
    assert job.status == "succeeded" and job.adapter_id
    assert len(client.lineage) == 1

    client.inference.inject_ft_failures(5)  # exhaust retries → fallback
    # Lower threshold so breaker opens quickly during demo.
    client.infer_breaker.failure_threshold = 1
    r1 = client.complete("summarize claim", correlation_id="corr-infer-1")
    assert r1.path in {"base", "prompt_template"} and r1.degraded

    r2 = client.complete("summarize claim", correlation_id="corr-infer-2")
    # Breaker may still be open → base/template without calling FT.
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

Run: `python3 modules/12-how-fine-tuning-works.md` is not valid — copy the code block to a `.py` file or extract with your usual snippet runner. Expected: prints `lora_out`, `job`, two `complete_*` lines, `lineage_adapter`, and `ok`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Clinical note style specialization (HIPAA VPC)

**Problem**: A health-system AI assistant must emit SOAP-formatted clinical notes with house style. Prompt+few-shot burns tokens and still drifts. PHI charts **cannot** leave the VPC. Target: specialize a 7B–8B open-weight model; TTFT for autocomplete-class drafting **&lt;300 ms** on a single GPU; holdout regression on general ability **&lt;5%** before promote ([Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models)).

**Proposed architecture (ASCII component diagram)**

```
┌──────────── CONTROL PLANE ────────────┐
│  dataset lock · eval gate · promote   │
│  model card + RBAC (train/promote)    │
└───────┬───────────────┬───────────────┘
        │               │
        ▼               ▼
┌──────────────┐  ┌─────────────────────┐
│ PII DLP      │  │ QLoRA/LoRA trainer  │
│ detect→redact│─►│ (VPC GPU)           │
│ →audit       │  └──────────┬──────────┘
└──────────────┘             │ adapter ckpt
                             ▼
                    ┌─────────────────┐
                    │ Adapter registry│
                    │ + lineage log   │
                    └────────┬────────┘
                             ▼
┌──────── TOOL PROXY (MCP) ──┴──────────────────────────────┐
│  complete_ft(tenant→adapter) · mTLS · no raw weight paths │
└────────┬───────────────────────────────┬──────────────────┘
         ▼                               ▼
┌─────────────────┐              ┌─────────────────┐
│ Serve: merged   │              │ Fallback: base  │
│ 7B + LoRA       │──fail───────►│ → SOAP template │
└─────────────────┘              └─────────────────┘
         │
         ▼
   TELEMETRY: TTFT · corr_id · breaker
```

**Trade-off matrix**

| Alternative | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. VPC LoRA/QLoRA (recommended)** | GPU + dataset labor; ~$10/run order PEFT anecdote | Best path to &lt;300 ms TTFT on 7B | High (train+serve+eval) | PHI stays in VPC; adapter RBAC | Many ~26–35 MB adapters / base |
| **A2. Prompt + few-shot only** | Lowest start | Worse (long prompts) | Low | PHI in every prompt window | Poor — prompt bloat |
| **A3. Hosted OpenAI FT** | Train \$ + inference markup | API TTFT | Medium | Data leaves VPC; platform wind-down | Medium — access risk for greenfield |

**Decision rationale**: HIPAA boundary eliminates A3 for greenfield. A2 fails the style-consistency + token budget. A1 matches “fine-tune for behavior,” keeps train/serve inside the auth/audit boundary, and uses LoRA’s frozen base to limit catastrophic forgetting under the &lt;5% gate. Pair with RAG later if note generation needs chart facts — fine-tune on RAG-shaped examples ([Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)).

---

### Scenario B — Distill Sonnet-quality behavior into Bedrock Haiku + Provisioned Throughput

**Problem**: A SaaS support copilot uses Claude Sonnet via Bedrock for high-quality replies but unit economics break above a few hundred thousand queries/month. Product wants Haiku-class latency/cost with Sonnet-distilled behavior, AWS residency, and reserved capacity — not on-demand gamble ([AWS GA blog](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/); [Newsletter #162](https://newsletter.systemdesign.one/p/fine-tuning-ai-models) break-even heuristic).

**Proposed architecture (ASCII component diagram)**

```
┌──────────────────── CONTROL PLANE ────────────────────┐
│  CreateModelCustomizationJob · early stop · approve   │
└──────────────┬────────────────────┬───────────────────┘
               │                    │
               ▼                    ▼
┌──────────────────────┐   ┌────────────────────────────┐
│ S3 train/val JSONL   │   │ Distill set: Sonnet labels │
│ (32–10k lines,       │◄──│ on prod-like prompts       │
│  <32k tok/entry)     │   └────────────────────────────┘
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Bedrock customization│──private copy of Claude 3 Haiku──┐
│ job (async)          │                                  │
└──────────┬───────────┘                                  │
           ▼                                              ▼
┌──────────────────────┐                        ┌─────────────────────┐
│ Custom model ARN     │──bind Provisioned──────►│ PT (MU hours)       │
│ in registry+lineage  │       Throughput       │ capacity reserved   │
└──────────┬───────────┘                        └──────────┬──────────┘
           ▼                                               │
┌──────────────────────┐                                   │
│ App / MCP invoke     │◄──── back-pressure on MU exhaust──┘
│ FT Haiku → Sonnet    │
│ → template fallback  │
└──────────────────────┘
```

**Trade-off matrix**

| Alternative | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Bedrock Haiku FT + PT (recommended)** | Train (quote) + **hourly MU**; lower \$/query than Sonnet at volume | Provisioned, stable | Medium–High (job+PT+evals) | Private model copy; VPC/KMS options | Scale via MU commitment |
| **B2. Stay on Sonnet on-demand** | High \$/query at volume | Good quality, higher token cost | Low | Standard Bedrock tenancy | Scales with account limits; unit economics fail |
| **B3. Self-host open-weight LoRA** | GPU fleet | Potentially best TTFT | Highest | Max control | Adapter swap excellent; ops burden |

**Decision rationale**: Anthropic first-party API has **no** self-serve FT — Bedrock is the Claude customization path ([AWS GA](https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/)). Distilling Sonnet labels into Haiku FT hits the behavior axis while PT removes on-demand starve. B2 loses on cost at the stated volume. B3 wins only if the team already runs GPU serve and accepts open-weight license fit; here AWS residency + Claude quality favor B1. Budget MU **before** go-live — trained custom models are not usable without Provisioned Throughput.

---

### Interview prompts (after both scenarios)

1. Walk dataset → train job → adapter registry → inference on the Part 1 diagram; name what each plane owns.  
2. Derive \$/1k inference for FT vs base given \(T_{\text{in}}, T_{\text{out}}\) and the verified RFT \$/1M table; state the SFT \$/1M **Gap**.  
3. Contrast LoRA forward \(W_0x + BAx\) with QLoRA’s 4-bit base; why is adapter ckpt ~35 MB meaningful for multi-tenant RPO/RTO?  
4. When do you refuse FT in favor of RAG? Give the context-vs-behavior axes.  
5. Design the FT → base → prompt-template fallback and where the train-API circuit breaker sits.  
6. Specify PII detect→redact→audit before JSONL upload and Zero-Trust rules for an MCP `complete_ft` tool.  
7. Scenario A vs B: which constraint (HIPAA VPC vs Claude-on-Bedrock) flips the recommendation, and why?

---

## Sources (from research)

- [1] https://newsletter.systemdesign.one/p/fine-tuning-ai-models — Newsletter #162  
- [2] https://arxiv.org/abs/2106.09685 — LoRA  
- [3] https://arxiv.org/abs/2305.14314 — QLoRA  
- [4] https://arxiv.org/abs/2305.18290 — DPO  
- [5] https://arxiv.org/abs/2203.02155 — InstructGPT / RLHF  
- [6] https://developers.openai.com/api/docs/guides/model-optimization  
- [7] https://developers.openai.com/api/docs/guides/supervised-fine-tuning  
- [8] https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy  
- [9] https://developers.openai.com/api/docs/pricing — RFT $100/h; SFT \$/1M gap  
- [10] https://help.openai.com/en/articles/11323177 — RFT billing  
- [11] https://aws.amazon.com/blogs/aws/fine-tuning-for-anthropics-claude-3-haiku-model-in-amazon-bedrock-is-now-generally-available/  
- [12] https://aws.amazon.com/blogs/machine-learning/fine-tune-anthropics-claude-3-haiku-in-amazon-bedrock-to-boost-model-accuracy-and-quality/  
- [13] https://docs.aws.amazon.com/bedrock/latest/userguide/model-customization-submit.html  
- [14] https://aws.amazon.com/bedrock/pricing/  
