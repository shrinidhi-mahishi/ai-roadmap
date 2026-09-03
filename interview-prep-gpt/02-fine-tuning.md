# Fine-tuning

## Why It Matters
Fine-tuning matters when prompting, retrieval, and workflow design have already done most of the work, but you still need a stable behavior shift. Good interview examples are format discipline, tone consistency, tool-call style, and narrow-domain skill improvement where the model repeatedly makes the same kind of mistake.

The clean decision boundary is this:

- Use prompting when the behavior change is light and easy to specify.
- Use RAG when the problem is private or fast-changing knowledge.
- Use workflow and validators when the problem is orchestration or business rules.
- Use fine-tuning when you want the model itself to internalize a repeatable behavior.

That framing is strong because it avoids the common anti-pattern of using fine-tuning to memorize data that should live in retrieval.

## Mental Model
Treat fine-tuning as changing the model's default distribution, not attaching a knowledge base. A tuned model should behave correctly before the prompt gets elaborate.

Another useful interview frame is a ladder:

1. Prompt better.
2. Add retrieval or tool use.
3. Add deterministic validation or workflow structure.
4. Fine-tune only after the above start showing diminishing returns.

Within fine-tuning itself, think of three families:

- SFT teaches "produce this kind of answer."
- DPO teaches "prefer this answer over that one."
- RFT teaches "optimize against a grader or reward signal."

The further right you go, the higher the data and evaluation burden.

## Architecture / Flow
```text
problem definition -> baseline with prompt/RAG/workflow
-> choose objective (SFT, DPO, or RFT)
-> curate and version data
-> train adapters or full weights
-> evaluate against holdouts and task slices
-> package model/adapters
-> deploy with rollback path
```

A practical system usually has four moving parts:

1. Data pipeline
   - collect examples
   - deduplicate
   - normalize format
   - split by train/validation/test

2. Training strategy
   - full fine-tune for rare cases
   - PEFT for most real deployments

3. Evaluation
   - task success
   - output format compliance
   - regression slices
   - safety and prompt-injection boundaries

4. Serving strategy
   - merged weights
   - hot-swappable adapters
   - per-customer adapter catalogs

## Key Concepts
- Fine-tuning vs RAG:
  - Fine-tuning is bad for rapidly changing knowledge.
  - RAG is bad at making a model consistently adopt a new style or schema when the base model keeps drifting.
  - The best answer is often both: RAG for facts, tuning for behavior.

- Full fine-tuning vs PEFT:
  - Full fine-tuning updates all model weights and is expensive to train, store, and serve.
  - PEFT keeps the base model frozen and trains a small task-specific delta.
  - In practice PEFT wins unless you own open weights, have strong infra, and truly need deep distribution shift.

- LoRA:
  - Low-rank adapter matrices are inserted into target linear layers while base weights stay frozen.
  - This is usually the first PEFT method interviewers expect you to explain.

- QLoRA:
  - Freeze the base model in 4-bit.
  - Train LoRA adapters on top.
  - Use NF4, double quantization, and paged optimizers to fit larger models on modest hardware.

- Other PEFT families:
  - IA3 scales intermediate activations and can be lighter than LoRA.
  - Prefix tuning and prompt tuning add trainable prompt-like vectors instead of weight deltas.
  - These are useful when memory is extremely constrained or when you want minimal serving changes.

- Data shapes:
  - SFT wants clean prompt-response pairs.
  - DPO wants chosen/rejected pairs.
  - RFT wants tasks plus a reliable grader or reward signal.

- Dataset quality beats raw size:
  - contradictory labels, duplicates, or unclear formatting can teach the wrong behavior very efficiently.
  - narrow data can overfit tone and damage general capability.

- Serving choices:
  - Merged weights simplify deployment but create model sprawl.
  - Adapter catalogs are operationally cleaner for multi-tenant or multi-feature systems.
  - Hot-loading adapters needs strict versioning and authz.

- Important boundary:
  - Fine-tuning does not solve prompt injection.
  - Fine-tuning does not provide tenant-specific ACLs.
  - Fine-tuning is not a freshness layer.

## Metrics and Formulas to Memorize
- LoRA trainable parameters per linear layer:
  - `r * (d_in + d_out)`
  - for a square `d x d` layer, approximately `2dr`
- Original LoRA claim: about `10,000x` fewer trainable parameters and about `3x` lower GPU memory than full fine-tuning on GPT-3-scale models
- QLoRA headline:
  - `65B` fine-tuned on a single `48 GB` GPU
  - full 16-bit fine-tuning at that scale was more than `780 GB`
- QLoRA double quantization saved about `0.373 bits/parameter`, roughly `3 GB` on a `65B` model
- Guanaco / QLoRA benchmark references:
  - top model reported `99.3%` of ChatGPT on Vicuna
  - `33B` model reported `97.8%`
- Azure practical data guidance:
  - hard minimum `10` examples for jobs
  - practical start around `50`
  - serious runs usually need hundreds or thousands
- Azure SFT knob worth remembering:
  - `batch_size = -1` means about `0.2%` of training examples
  - maximum `256`
- Azure learning-rate multiplier range:
  - roughly `0.02` to `0.2`
- OpenAI DPO `beta` range on their API surface:
  - `0` to `2`
- RFT only tends to help when:
  - the base model has non-zero competence
  - the eval is neither floor nor ceiling
  - the grader is reliable

Public cost comparisons change quickly and differ by provider, so interview answers are stronger when they focus on parameter efficiency, hardware fit, and eval quality rather than memorizing transient job pricing.

## Trade-offs and Failure Modes
- Using fine-tuning for mutable knowledge:
  this creates stale answers and retraining churn where retrieval would have been cleaner.

- Bad data:
  contradictory or low-quality labels can overpower a good base model.

- Overfitting:
  the tuned model becomes excellent on narrow examples but drifts in tone, verbosity, or edge cases.

- Wrong target modules:
  teams sometimes blame LoRA rank when the real issue is poor module selection or bad data.

- Weak DPO foundation:
  if the baseline model cannot already produce decent candidates, preference tuning becomes unstable.

- Reward hacking in RFT:
  if the grader is easy to exploit, the model optimizes the grader instead of the task.

- Operational sprawl:
  many full tuned checkpoints are painful to store, route, and roll back. Adapters are usually cleaner.

- Vendor lock-in:
  closed-model fine-tuning support changes over time. Snapshot deprecations and gated feature matrices are real operational risks.

## Interview Q&A
**Q: When should I fine-tune instead of using RAG?**  
A: Fine-tune when you need stable behavior or style changes. Use RAG for fresh or private knowledge. They solve different problems.

**Q: Explain LoRA in one sentence.**  
A: LoRA keeps base weights frozen and learns a low-rank delta inserted into selected layers.

**Q: Why did QLoRA matter?**  
A: It made large-model fine-tuning practical on far less hardware by combining 4-bit frozen weights with trainable LoRA adapters.

**Q: SFT vs DPO vs RFT?**  
A: SFT learns from gold outputs, DPO learns from ranked preferences, and RFT learns against a reward or grader.

**Q: What is the biggest data lesson in fine-tuning?**  
A: Quality and consistency matter more than raw dataset size once you have enough coverage.

**Q: Does fine-tuning solve prompt injection?**  
A: No. Injection is an application security problem. Fine-tuning may improve robustness a bit, but it is not a security boundary.

**Q: Should I merge adapters into the base model?**  
A: Only if deployment simplicity matters more than modularity. Adapter catalogs are often better for multi-tenant systems and rollback.

**Q: When is RFT worth it?**  
A: When you have a reliable grader, the task is measurable, and the base model is already good enough that extra search or reward optimization can compound.

## Sources
- Local anchors:
  - `ai-roadmap/final/15-inference-optimization.md`
  - `ai-roadmap/final/13-security-guardrails.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [OpenAI Model Optimization Guide](https://developers.openai.com/api/docs/guides/model-optimization)
  - [OpenAI DPO Guide](https://developers.openai.com/api/docs/guides/direct-preference-optimization)
  - [OpenAI RFT Guide](https://developers.openai.com/api/docs/guides/reinforcement-fine-tuning)
  - [Microsoft Foundry Fine-tuning Docs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/fine-tuning)
  - [LoRA Paper](https://arxiv.org/abs/2106.09685)
  - [QLoRA Paper](https://arxiv.org/abs/2305.14314)
  - [Hugging Face PEFT LoRA Docs](https://huggingface.co/docs/peft/main/package_reference/lora)
  - [TRL DPO Trainer Docs](https://huggingface.co/docs/trl/en/dpo_trainer)
