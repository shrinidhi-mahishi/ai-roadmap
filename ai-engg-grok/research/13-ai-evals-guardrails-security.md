# Research: AI Evals, Guardrails & Security

**Date researched**: 2026-09-30
**Sources consulted**: 24
**Scope note**: Covers **offline/online evaluation** (golden sets, grader hierarchy, LLM-as-judge, pass@k / pass^k, CI gates) **and** runtime **guardrails / security** (OWASP LLM Top 10, prompt-injection defenses, NeMo-style rail orchestration as a cited pattern, PDP/PEP authorization) as one trust layer. Deep RAG retrieval design, fine-tuning mechanics, and MCP transport internals are out of scope except where they appear as eval metrics or attack surfaces.

> **Paywall note**: The primary System Design Newsletter piece ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)) is partially gated. Evaluation architecture, grader hierarchy, metric stack, pass@k / pass^k framing, and the four-component trust layer are drawn from the publicly available portion; guardrails depth below is cross-checked against OWASP, Anthropic/OpenAI primary docs, and published papers rather than fabricated from the gated remainder.

## 1. System Topology & Mechanics

### Trust-layer control plane vs. request data plane

The newsletter frames production LLM systems as having a **trust layer** with four components that answer different lifecycle questions ([System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)):

| Component | When it runs | Question answered |
| --- | --- | --- |
| **Evaluation** | Pre-deploy / CI | Is this version good enough to ship? |
| **Guardrails** | Every request / response | Is this I/O allowed *now*? |
| **Observability** | Always-on | What happened, and which failures become new tests? |
| **Security** | Cross-cutting | Can an external attacker steer tools, data, or policy? |

Required wiring for the four to compose: a **golden test set**, repeatable offline grading, runtime checks with **explicit latency budgets**, and end-to-end traces that include **guardrail decisions** ([System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

Five properties make “assert equality” insufficient without this layer: non-determinism; fluent wrong answers; silent regressions across prompt/model/retrieval changes; untrusted instructions inside retrieved/tool content; and legal exposure that depends on deployment context, not model brand ([System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

### Grader hierarchy (eval control plane)

Match grader cost to failure type ([System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [Anthropic — Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)):

1. **Deterministic / code graders** — schema parse, tool-arg validation, unit tests, exact/normalized match, static analysis. Prefer these when a ground truth exists.
2. **LLM-as-judge / rubric graders** — subjective quality, tone, groundedness, open-ended synthesis.
3. **Human graders** — calibration gold standard; high-stakes or ambiguous cases.

Anthropic’s agent vocabulary: **task** → **trial** → **grader(s)** over **transcript** and/or **outcome**; an **evaluation harness** runs tasks and aggregates; the **agent harness** (scaffold + model) is what you are scoring ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). Outcome-first grading (e.g. refund row exists in DB) beats path-rigid tool-sequence checks that punish valid alternative trajectories ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

### Golden-set topology

Start **20–50** cases from real failures; grow toward **~50–100**; split **in-scope / out-of-scope (refuse) / adversarial**; keep a **holdout** set so prompt tuning does not overfit the regression suite ([System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). Two suites:

- **Regression**: near-**100%** pass — detects backsliding.
- **Capability**: deliberately **&lt;100%** — hill to climb; Anthropic calls near-ceiling **eval saturation** ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [System Design Newsletter #179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

OpenAI’s process loop: define objective → collect dataset (production, expert, synthetic, edge/adversarial) → define metrics → run/compare → continuous evaluation on every change ([OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)). Architecture-specific nondeterminism entry points: single-turn → workflows → single-agent (tool choice) → multi-agent ([OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).

### Metric stack by component

| Layer | Metrics (examples) | Source |
| --- | --- | --- |
| Base completion | Correctness, format compliance | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| RAG | Hit@K, MRR, context precision/recall, faithfulness, response relevance | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); OpenAI Q&A example targets context recall ≥**0.85**, precision &gt;**0.7** ([OpenAI eval best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)) |
| Tools | Correct tool + args (deterministic) | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Agents | Outcome state; diagnostics: step efficiency, loop detection, tokens/task (not release gates alone) | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Media / voice | Prompt adherence, unsafe rates; first-audio latency, interruption success | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |

### Runtime rail topology (NeMo Guardrails as cited pattern)

NVIDIA **NeMo Guardrails** orchestrates layered rails around the application LLM ([NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails); cited as orchestration pattern in [OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)):

1. **Input rails** — reject/alter user input before the main model.
2. **Dialog rails** — Colang flows steering whether to call the LLM, run an action, or return a canned response.
3. **Retrieval rails** — inspect/transform RAG chunks before they enter the prompt.
4. **Execution / tool rails** — gate custom actions and tool calls/results.
5. **Output rails** — inspect/transform the response before return.

Engines (pattern detail): full **LLMRails** (Colang dialog + retrieval + actions) vs. low-latency **IORails** focused on input/output/tool validation with optional parallel rails and speculative generation ([NeMo engine feature support via NVIDIA docs summary in public GitHub discussion](https://github.com/NVIDIA-NeMo/Guardrails/issues/1674); [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

### PDP / PEP authorization topology

Classical **Policy Decision Point (PDP)** vs **Policy Enforcement Point (PEP)** maps cleanly onto agent tool boundaries:

- **PDP**: evaluates “may this exact action with these arguments proceed *now*?” (policy, budgets, approvals, kill switch).
- **PEP**: sits at the tool wrapper / gateway / workflow step; **must not** execute a governed action without a permit; **fail closed** on uncertainty ([IETF draft AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/); [OpenID AuthZEN COAZ-MCP binding](https://openid.github.io/authzen/authzen-coaz-mcp-binding-1_0.html); [Enforcement-Point profile draft](https://www.ietf.org/archive/id/draft-schrock-ep-enforcement-point-00.html)).

AADP defines a two-phase wire contract (authorize → report outcome), atomic budget reservation, and the invariant that an **irreversible action is never executed autonomously** ([AADP draft](https://datatracker.ietf.org/doc/draft-saha-aadp/)). AuthZEN **COAZ-MCP** maps MCP JSON-RPC methods into AuthZEN PDP requests so an MCP gateway/server acting as PEP calls the PDP before the message takes effect ([COAZ-MCP](https://openid.github.io/authzen/authzen-coaz-mcp-binding-1_0.html)).

[inferred] In an enterprise agent stack, LLM safety classifiers are **advisory signals** into the PDP; the PEP remains the sole authoritative write-path gate — aligning with CapiscIO-style “LLM never authorizes” designs ([CapiscIO AGCP RFC-001](https://docs.capisc.io/rfcs/001-agcp/)).

## 2. Token Economics & NFR Metrics

### Eval-side cost structure

| Cost driver | Quantified / documented guidance | Source |
| --- | --- | --- |
| Human labels for judge calibration | Newsletter: calibrate on **30–50** human-labeled examples before trusting a judge; enough to start, not enough as permanent ground truth | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Judge model choice | Prefer cheapest model that passes calibration; **consistency &gt; raw capability**; judge inference is cheap vs. human labels | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Offline vs online judge | Related newsletter framing: offline can use the strongest judge; online samples use **small/cheap** judges + heuristics ([LLM Evals — System Design](https://newsletter.systemdesign.one/p/llm-evals)) | [LLM Evals](https://newsletter.systemdesign.one/p/llm-evals) |
| Repeated trials | Run each suite **3–5×** per change to separate sampling noise from real regressions | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Few-shot judge prompts | MT-Bench: few-shot judge prompts make API calls **~4×** more expensive vs zero-shot | [Zheng et al.](https://arxiv.org/abs/2306.05685) |
| pass@k sampling | Codex/HumanEval convention: generate **n=200**, report **k ∈ {1,10,100}** from the same samples (amortizes inference) | [Chen et al., arXiv:2107.03374](https://arxiv.org/abs/2107.03374) |

OpenAI example metric targets (illustrative product-specific, not universal SLAs): summarization ROUGE-L ≥**0.40** and G-Eval coherence ≥**80%** on 1000 held-out pairs; Q&A context recall ≥**0.85**, precision &gt;**0.7**, ≥**70%** positive ratings ([OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).

### pass@k vs pass^k (reliability economics)

**pass@k** (Chen et al. / Codex): probability that **≥1 of k** samples is correct; unbiased estimator \(\widehat{\mathrm{pass@}k}=1-\binom{n-c}{k}/\binom{n}{k}\) with \(n\ge k\) samples, \(c\) correct ([Chen et al., 2021](https://arxiv.org/abs/2107.03374)). Headline HumanEval: Codex-12B **pass@1 = 28.8%**, rising to **70.2%** with **100** samples (selection by unit tests) ([Chen et al.](https://arxiv.org/abs/2107.03374)).

**pass^k** (τ-bench / Sierra): probability that **all k** i.i.d. trials succeed — the reliability metric for unsupervised agents ([Yao et al., τ-bench, arXiv:2406.12045](https://arxiv.org/abs/2406.12045); [Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)). Published τ-bench numbers: gpt-4o function-calling ~**61%** pass^1 on retail / ~**35%** on airline; **pass^8 &lt; ~25%** on retail ([τ-bench](https://arxiv.org/abs/2406.12045)). Anthropic illustration: if per-trial success is **75%**, \(0.75^3 \approx 42%\) chance of three consecutive successes ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)). Newsletter illustration: **10%** unsupervised failure → ~**57%** chance of ≥1 failure across eight tasks ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

Use **pass@k** when a human reviews and retries are OK; **pass^k** when the agent acts without supervision ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

### Guardrail / safety-model latency (published)

| System / model | Published latency / throughput | Source |
| --- | --- | --- |
| Llama Guard 3-1B (A30 GPU bench) | **~165 ms/test** avg; **76%** detection on 100 adversarial prompts in one OWASP-oriented study | [arXiv:2601.19970](https://arxiv.org/html/2601.19970v1) |
| Llama Guard 3-1B-INT4 (mobile) | ≥**30 tok/s**, TTFT ≤**2.5 s** on commodity Android CPU; ~**440 MB** | [arXiv:2411.17713](https://arxiv.org/pdf/2411.17713) |
| Meta Code Shield | Avg latency **~200 ms** (7 languages) | [Meta Llama Protections](https://dev.meta.ai/llama/llama-protections) |
| NeMo IORails vs LLMRails (internal mock-LLM bench, concurrency 32) | Internal guardrails overhead p50 **1063 → 38 ms** (**~28×**); p99 **1257 → 78 ms** (**~16×**); mock content-safety fixed **500 ms** ×2 + app LLM **4 s** → **5 s** lower-bound e2e | [NeMo Guardrails #1674](https://github.com/NVIDIA-NeMo/Guardrails/issues/1674) |
| PromptGuard (academic layered defense) | Latency increase **&lt;8%** with up to **~67%** injection-success reduction | [PMC12789616](https://pmc.ncbi.nlm.nih.gov/articles/PMC12789616/) |

NVIDIA’s own FAQ states there is **no deployment-independent p50/p95** for a Guardrails config — results depend on rails enabled, engine, models, network, lengths, streaming, concurrency ([NeMo Runtime Security FAQ](https://docs.nvidia.com/nemo/guardrails/resources/runtime-security-faq) — cited as vendor guidance; treat as pattern-level, not a universal SLA).

> ⚠️ Limited public data available for this dimension. Cross-vendor production **p95 end-to-end guardrail budgets** (classifier + PDP + main LLM) are rarely published as comparable SLAs; numbers above are model-specific or microbenchmarks under stated hardware/mock assumptions. Do not treat them as portable enterprise SLOs without re-benchmarking.

[inferred] A practical budget pattern: hard-cap total guardrail overhead as a fraction of TTFT (e.g. cheap regex/schema on 100% of traffic; LLM safety models on input + output for tool-bearing paths only) so safety spend stays sub-linear with RPM.

## 3. Distributed Resilience & State

### Eval harness resilience

- **Isolate trials**: clean environment per trial; shared filesystem/git history can leak answers across runs (Anthropic observed Claude reading prior-trial git history) ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).
- **CI as quality circuit breaker**: change → run suites → scorecard → **block merge** if critical regression thresholds fail ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [OpenAI CE guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).
- **Holdout + production feedback loop**: online signals (thumbs, regeneration, citation clicks) feed new golden cases; offline prevents shipping known regressions ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).
- **Swiss-cheese layering**: automated evals + production monitoring + A/B + user feedback + transcript review + periodic human studies — no single layer catches all failures ([Anthropic — Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).

### Runtime resilience patterns for attacks / abuse

- **Best-of-N jailbreak scaling**: Hughes et al. report **89%** success on GPT-4o and **78%** on Claude 3.5 Sonnet with enough attempts; rate limits, content filters, and circuit breakers **slow but do not eliminate** attacks under power-law scaling ([OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).
- **Fail-closed PEP**: rejecting / uncertain PDP decisions must take effect **before** any approval-bearing state mutation ([EP Enforcement-Point draft](https://www.ietf.org/archive/id/draft-schrock-ep-enforcement-point-00.html); [AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/)).
- **Budget reservation**: AADP’s authorize-then-report pattern treats a permit without an outcome report as an unresolved intent and does not silently release budget ([AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/)).
- **Parallel rails short-circuit**: independent input/output rails can run concurrently; a blocking rail fails the request early ([NeMo parallel-rails pattern](https://github.com/NVIDIA-NeMo/Guardrails); [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).
- **Speculative generation** (IORails pattern): run input rails concurrent with generation; discard generation if input rail blocks — hides latency on the common safe path ([NeMo #1674 discussion](https://github.com/NVIDIA-NeMo/Guardrails/issues/1674)).

> ⚠️ Limited public data available for this dimension. Multi-tenant distributed eval orchestration (Kafka/Temporal-backed golden-set runners, cross-region judge fleets, distributed locking for suite ownership) is not documented with portable public benchmarks; resilience claims here stay at harness/CI and request-path patterns.

## 4. Enterprise Security & Governance

### OWASP Top 10 for LLM Applications 2025

Canonical risk catalog ([OWASP Top 10 for LLMs 2025 PDF](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf); [LLM01 page](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)):

| ID | Risk |
| --- | --- |
| LLM01 | Prompt Injection (direct + indirect) |
| LLM02 | Sensitive Information Disclosure |
| LLM03 | Supply Chain |
| LLM04 | Data and Model Poisoning |
| LLM05 | Improper Output Handling |
| LLM06 | Excessive Agency |
| LLM07 | System Prompt Leakage |
| LLM08 | Vector and Embedding Weaknesses |
| LLM09 | Misinformation |
| LLM10 | Unbounded Consumption |

2025 shifts vs 2023: **Unbounded Consumption** expands DoS to cost/resource abuse; **Vector/Embeddings** covers RAG; **System Prompt Leakage** added from real exploits; **Excessive Agency** expanded for agent/plugin autonomy ([OWASP 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

### Prompt injection — mechanics and mitigations

OWASP states there is **no known fool-proof prevention** given stochastic instruction/data mixing; mitigations reduce impact ([OWASP LLM01](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)):

1. Constrain model role/capabilities in system prompt.
2. Define/validate expected output formats with deterministic validators.
3. Input/output filtering (semantic + string rules; RAG triad for relevance/groundedness).
4. Least-privilege tool/API tokens handled in code, not handed wholesale to the model.
5. Human-in-the-loop for high-risk actions.
6. Segregate/label untrusted external content.
7. Continuous adversarial testing treating the model as untrusted.

Attack classes catalogued in the OWASP cheat sheet include direct/indirect injection, encoding/obfuscation, typoglycemia, Best-of-N jailbreaks, HTML/Markdown exfil, multimodal, RAG poisoning, and agent thought/tool manipulation ([OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

**Dual-LLM / privilege separation pattern**: privileged LLM holds tools but never reads untrusted content directly; quarantined LLM reads untrusted content but cannot act; privileged model receives only structured summaries/labels (Willison dual-LLM; DeepMind **CaMeL** extends with strict data tracking) ([OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

**Model-based guardrails** (alongside deterministic controls): Llama Guard, ShieldGemma, Granite Guardian, Prompt Guard; NeMo Guardrails as orchestration ([OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html); [Meta Llama Protections](https://dev.meta.ai/llama/llama-protections)).

### Published attack-success figures (do not over-generalize)

| Study / claim | Attack success | Notes |
| --- | --- | --- |
| Hughes et al. (via OWASP cheat sheet) | **89%** GPT-4o, **78%** Claude 3.5 Sonnet (BoN) | Defenses slow, not eliminate |
| Layered agent/RAG defense benchmark (arXiv:2511.15759) | Baseline **73.2%** → full defense **8.7%**; task performance retained **94.3%** | 847 adversarial cases; independent academic result |
| PromptGuard layers | Up to **~67%** ISR reduction; detection F1 **0.91**; **&lt;8%** latency | Retraining-free modular pipeline |

### PDP/PEP governance for tools

- PEP at MCP gateway/server constructs AuthZEN evaluation from method + tool params before execution ([COAZ-MCP](https://openid.github.io/authzen/authzen-coaz-mcp-binding-1_0.html)).
- Decision vocabulary examples: `allow` / `allow_with_signoff` / `deny` (+ observe mode); signoff required before mutation ([EP draft](https://www.ietf.org/archive/id/draft-schrock-ep-enforcement-point-00.html)).
- Maps to OWASP **LLM06 Excessive Agency**: constrain permissions, require HITL for privileged ops ([OWASP 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

### Audit / compliance [inferred + thin public detail]

Traces should record model version, prompts (with PII policy), tool args/results, **guardrail allow/deny reasons**, and PDP verdict IDs for SOC2/HIPAA-style reconstruction ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) observability framing; [AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/) evidence requirements).

> ⚠️ Limited public data available for this dimension. Standardized, audited schemas for “guardrail decision + PDP receipt” in regulated industries are still emerging (AADP/AuthZEN drafts); treat as architecture direction, not settled compliance standards.

## 5. Production Failure Modes

### Eval / quality failures

| Failure | Symptoms | Mitigation | Source |
| --- | --- | --- | --- |
| Silent regression | Prompt/model fix for one case breaks others; users feel “worse” with no metric move | Regression suite near 100%; CE on every change | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Eval overfitting | Scores climb while holdout/production falls | Holdout set; refresh from production failures | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Eval saturation | Capability suite → ~100%; score deltas meaningless | Add harder tasks; graduate old capability → regression | [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); SWE-Bench Verified cited **~30% → &gt;80%** in ~1 year |
| Grader bugs / ambiguous tasks | Agent “fails” correctly (Opus 4.5 CORE-Bench **42% → 95%** after fixing rigid grading/specs) | Reference solutions; transcript review; partial credit | [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Path-brittle tool checks | Punishes valid alternate trajectories | Grade outcomes, not exact tool sequences | [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Single-run noise | 2-point drop treated as release blocker | **3–5** repeated runs; require persistent drop | [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| Unsupervised inconsistency | High pass@1, low pass^k | Gate unsupervised deploy on pass^k | [τ-bench](https://arxiv.org/abs/2406.12045); [#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |

### LLM-as-judge failure modes (Zheng et al.)

| Bias / limit | Quantified finding | Mitigation |
| --- | --- | --- |
| **Position bias** | On near-indistinguishable pairs, only GPT-4 consistent in **&gt;60%** of swaps; most judges favor first position | Swap order; declare win only if preferred both ways; few-shot can lift GPT-4 consistency **65% → 77.5%** |
| **Verbosity bias** | “Repetitive list” attack fools weaker judges; GPT-4 resists better | Length-matched pairs in calibration |
| **Self-enhancement** | GPT-4 +**~10%** win rate for itself vs humans; Claude-v1 +**~25%** (inconclusive causality) | Use **different model family** as judge than system under test ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)) |
| Agreement | GPT-4 vs humans **&gt;80%** overall; S2 (no ties) **85%** vs human-human **81%** on MT-Bench; humans rate GPT-4 judgments reasonable **75%**, change mind **34%** | Calibrate; pairwise for subjective; critique-before-verdict ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)) |

Sources: [Zheng et al., MT-Bench / Chatbot Arena, arXiv:2306.05685](https://arxiv.org/abs/2306.05685); newsletter bias list aligns ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

### Security / guardrail failures

| Failure | Mechanism | Mitigation |
| --- | --- | --- |
| Indirect prompt injection | Instructions in web/docs/tools/RAG chunks | Dual-LLM; retrieval rails; treat all external data untrusted ([OWASP](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf); [Greshake et al. lineage via OWASP refs](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)) |
| Tool privilege escalation | Injection → unauthorized tool/API use | PEP/PDP least privilege; HITL for irreversible actions ([OWASP LLM06](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf); [AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/)) |
| Improper output handling | Model output → XSS/SQLi/cmd in downstream sink | Deterministic output encoding/validation (LLM05) |
| Unbounded consumption | BoN / looped agents burn tokens | Rate limits, cost caps, max iterations (LLM10; BoN note) |
| Guardrail bypass | Classifier false negatives; filter-evading typos/encoding | Defense-in-depth; fuzzy/typo detectors; layered ASRs still leave residual risk ([OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)) |

Real incident class examples listed by OWASP (email-assistant CVE-2024-5184, resume payload splitting, multimodal image instructions) demonstrate production exploit paths beyond lab jailbreaks ([OWASP 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

## 6. Enterprise System Design Scenarios

### Scenario A — Internal meeting-notes vs hiring-screen (risk-scaled trust layer)

Newsletter rule: depth of the trust layer tracks **decision impact**, not user count. Meeting notes → small golden set + basic tracing. Hiring screen → stronger refusal/adversarial cases, human review gates, stricter audit ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

### Scenario B — Eval-driven CI for agents (Anthropic / OpenAI pattern)

1. Encode success as tasks with reference solutions before feature work (“eval-driven development”).
2. Combine code graders (state/tests) + calibrated LLM rubrics + occasional human.
3. Run capability + regression suites on every prompt/model/tool change; track latency, tokens, cost per task as free side metrics ([Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); [OpenAI](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).
4. Ship only if regression holds and capability deltas are real across **3–5** seeds ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails)).

Customer anecdotes: Descript dual suites (quality + regression) with LLM graders + human calibration; Bolt built an eval system in ~**3 months** post-scale; Claude Code evolved from dogfooding to concision/file-edit/over-engineering evals ([Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).

### Scenario C — Customer-service agent reliability gate (τ-bench style)

Do not ship unsupervised tool-using support on pass@1 alone. Require pass^k trend (e.g. pass^4 / pass^8) because gpt-4o-class agents can show ~**60%** single-trial retail success collapsing to **&lt;25%** at pass^8 ([τ-bench](https://arxiv.org/abs/2406.12045)). Pair with outcome DB checks + policy rubrics ([Anthropic conversational-agent pattern](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)).

### Scenario D — Defense-in-depth request path

```
Client → Input filters (deterministic) → Safety classifier (optional)
      → Application LLM
      → Output filters / schema validation
      → PEP → PDP (tool args, budgets, approvals) → Tool / MCP
      → Trace (incl. rail + PDP decisions) → Golden-set mining
```

Trade-off matrix:

| Approach | Latency | Ops complexity | Residual injection risk | Best fit |
| --- | --- | --- | --- | --- |
| System-prompt-only constraints | Lowest | Low | High | Prototypes |
| Deterministic I/O filters + schema | Low ms | Medium | Medium | Structured APIs |
| + Llama Guard–class models (~**100–200 ms**/check class) | Medium | Medium | Lower | Consumer chat / content |
| + NeMo-style rail orchestration | Medium–high (IORails can cut internal overhead to tens of ms in mocks) | Higher | Lower | Multi-rail apps |
| + Dual-LLM + PEP/PDP HITL | Highest on risky paths | Highest | Lowest blast radius | Agents with write tools |

Sources: [OWASP](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf); [OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html); [Llama Guard latency studies](https://arxiv.org/html/2601.19970v1); [NeMo #1674](https://github.com/NVIDIA-NeMo/Guardrails/issues/1674); [AADP](https://datatracker.ietf.org/doc/draft-saha-aadp/); [COAZ-MCP](https://openid.github.io/authzen/authzen-coaz-mcp-binding-1_0.html).

### Scenario E — Capacity / cost planning [inferred from published pieces]

- Offline: \(N_{\text{cases}} \times N_{\text{trials (3–5)}} \times (C_{\text{SUT}} + C_{\text{judge}})\) per release candidate; amortize with smaller judges after calibration ([#179](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails); [LLM Evals](https://newsletter.systemdesign.one/p/llm-evals)).
- Online: sample rate for LLM judges; **100%** deterministic rails; escalate to heavy classifiers only on tool/RAG/untrusted-content paths ([OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).
- Unbounded consumption (LLM10): couple agent max-steps with token/$ budgets and BoN-aware rate limits ([OWASP 2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)).

OpenAI platform note: hosted **Evals API/platform** is being deprecated (read-only **2026-10-31**, shutdown **2026-11-30**) — design harness ownership independent of a single vendor UI ([OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)).

## Sources

- [1] https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails — System Design Newsletter #179: LLM Evaluation and Guardrails (primary article; partial public text)
- [2] https://newsletter.systemdesign.one/p/llm-evals — System Design Newsletter: LLM Evals (offline/online judge tiering)
- [3] https://developers.openai.com/api/docs/guides/evaluation-best-practices — OpenAI evaluation best practices + CE + architecture nondeterminism map
- [4] https://developers.openai.com/api/docs/guides/evals — OpenAI Working with evals / Evals API guide
- [5] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents — Anthropic: Demystifying evals for AI agents (Jan 2026)
- [6] https://platform.claude.com/docs/en/test-and-evaluate/develop-tests — Anthropic: Define success criteria and build evaluations
- [7] https://arxiv.org/abs/2306.05685 — Zheng et al.: Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena
- [8] https://arxiv.org/abs/2107.03374 — Chen et al.: Evaluating Large Language Models Trained on Code (pass@k, HumanEval)
- [9] https://arxiv.org/abs/2406.12045 — Yao et al.: τ-bench (pass^k for tool-agent-user interaction)
- [10] https://github.com/sierra-research/tau-bench — τ-bench leaderboard / pass^k tables
- [11] https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf — OWASP Top 10 for LLM Applications 2025
- [12] https://genai.owasp.org/llmrisk/llm01-prompt-injection/ — OWASP LLM01:2025 Prompt Injection
- [13] https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html — OWASP LLM Prompt Injection Prevention Cheat Sheet
- [14] https://github.com/NVIDIA-NeMo/Guardrails — NVIDIA NeMo Guardrails (rail types / Colang pattern)
- [15] https://github.com/NVIDIA-NeMo/Guardrails/issues/1674 — NeMo IORails vs LLMRails internal latency microbenchmark
- [16] https://docs.nvidia.com/nemo/guardrails/resources/runtime-security-faq — NeMo Guardrails runtime/latency FAQ (no universal p50)
- [17] https://dev.meta.ai/llama/llama-protections — Meta Llama Guard / Prompt Guard / Code Shield (~200 ms)
- [18] https://arxiv.org/pdf/2411.17713 — Llama Guard 3-1B-INT4 efficiency paper (tok/s, TTFT, size)
- [19] https://arxiv.org/html/2601.19970v1 — Llama Guard OWASP-oriented latency/detection benchmark (~165 ms, 76%)
- [20] https://datatracker.ietf.org/doc/draft-saha-aadp/ — Agent Action Decision Protocol (PDP/PEP for agents)
- [21] https://openid.github.io/authzen/authzen-coaz-mcp-binding-1_0.html — AuthZEN COAZ-MCP binding (PEP→PDP for MCP)
- [22] https://www.ietf.org/archive/id/draft-schrock-ep-enforcement-point-00.html — Enforcement-Point profile (fail-closed PEP)
- [23] https://arxiv.org/html/2511.15759v1 — Layered prompt-injection defense benchmark (73.2% → 8.7% ASR)
- [24] https://pmc.ncbi.nlm.nih.gov/articles/PMC12789616/ — PromptGuard layered injection defense (&lt;8% latency, ISR reductions)
