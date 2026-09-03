# Module 04: Evals (Harness, Dual-Oracle, pass@k / pass^k)

**Study + interview prep.** Grounded in research dated 2026-09-02 (109 sources). Prices, bench numbers, and platform meters are vendor docs / papers as of that date. `$ per 1k eval tasks` figures that multiply published rates by a stated reference loop are **[inferred]**, not a vendor SKU. Public pages do **not** publish production p50/p95/p99 of *your* online judge — missing percentiles are marked. Fine-tuning promote-gates and RAG retrieval mechanics are out of scope except at the eval intersection.

Invariant: **the harness is part of the system under test**. Task success is a property of `(model × scaffold × tools × environment × oracle × sampling × retries × infra)`. Collapsing that product into “the model scored 91%” is the dominant interview failure.

---

## What Is This?

An LLM demo is not a measurement. **Eval** is the **measurement system** around the agent: a versioned dataset, a runner that invokes the *complete* production scaffold, an environment that can be reset, one or more oracles that score the result, retries that are *declared* rather than hidden, and infrastructure whose RAM/Docker/timeouts move the number. A leaderboard screenshot is a press release. A measurement names the construct, the unit, the grader, and the uncertainty.

Think of manufacturing QC, not a report card:

| Plane | Analogy | Clock |
| --- | --- | --- |
| **Eval harness (control)** | The test lab. Batch: dataset → target → scorers → immutable experiment. | Job wall-time; `num_repetitions`; Docker minutes |
| **Production tracing (data)** | Factory-floor sensors. Every live request as a nested span tree. | User SLO (TTFT / e2e) |
| **Judge / scorer (sidecar)** | The inspector. Code, LLM-as-judge, or a human queue. | Async. **Must not** sit on the user critical path |

Two oracles, not one vibe score. **Hard** (DB goal-state, hidden tests, schema/AST, safety classifier) refuses partial credit — a 90%-right refund that violates policy is a **0**. **Soft** (HealthBench-shaped rubric, RAGAS faithfulness, tone) is partial credit among trials that already passed the hard gate. Using only the rubric is how teams ship “pretty wrong.” Using only the binary gate on open-ended chat is how teams ship “correct but hostile.”

Two estimators, opposite questions:

- **pass@k** (Chen et al., Codex / HumanEval): probability that **at least one** of \(k\) samples succeeds, given a **verifier** that can pick the winner. Optimistic capability envelope.
- **pass^k** (Yao et al., τ-bench): probability that **all** \(k\) independent trials succeed. Reliability. Users typically get one try — they live on pass^1, but the gap to pass^5 *is* the product risk.

## Why It Matters

Every enterprise agent that can refund, book, cite, or patch is an eval product with a chat UI. Interviews test whether you put the judge **off** the user p99 path, gate on **pass^k + a hard oracle** rather than a single-run pass@1, treat promoted traces as a **legal record**, and refuse to LLM-judge JSON that a schema can check. A Principal answer names the three planes, the two clocks, the two oracles, coverage% as an NFR, and the miller-unit (task, not span).

---

### 1. System Topology & Data Flow

An eval stack is **three independently scaled planes**. Coupling them — putting an LLM judge on the user p99 path, or scoring production traces with a CI golden-set grader that expects a reference output — is how teams either blow latency SLOs or invent fake correctness.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  coverage% (scored / eligible)   pass@k AND pass^k   paired SE   │
         │  OpenInference: LLM | AGENT | TOOL | RETRIEVER | EVALUATOR       │
         │  ingest 429s / spend-cap remaining / idle-timeout lag            │
         │  WORM: example_id, evaluator_version, actor, dataset as_of       │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ metrics           │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴────────────┐
│ CONTROL PLANE  (suite, versions, gates, spend — not token math)            │
│                                                                            │
│  ┌──────────────┐ ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐  │
│  │ Suite        │ │ Experiment      │ │ Release gate │ │ Judge breaker │  │
│  │ registry     │ │ controller      │ │ fail-closed  │ │ spend cap /   │  │
│  │ hypothesis,  │ │ model×scaffold  │ │ CI exit ≠ 0  │ │ sampling_rate │  │
│  │ slices, k    │ │ matrix, budget  │ │ dual-oracle  │ │ online only   │  │
│  └──────┬───────┘ └────────┬────────┘ └──────┬───────┘ └───────┬───────┘  │
│         │ pin              │ trials          │ signed          │ pause    │
└─────────┼──────────────────┼─────────────────┼─────────────────┼──────────┘
          │                  │                 │                 │
          ▼                  ▼                 │                 │
┌──────────────────────────────────────────────┼─────────────────┼──────────┐
│ DATA PLANE  (two clocks — do not share a thread pool)          │          │
│                                              │                 │          │
│  USER PATH (SLO clock)                       │   SIDECAR (async clock)    │
│  ┌─────────────────────────────────────┐     │   ┌─────────────────────┐  │
│  │ Agent under test = prod scaffold    │     │   │ Online scorer       │  │
│  │ retrieve / tools / policy / memory  │─────┼──▶│ idle timeout (30 s  │  │
│  │ Trace = 1 execution; runs = children│     │   │ Braintrust default) │  │
│  │ Hard limit: 25,000 runs / trace_id  │     │   │ + judge / schema    │  │
│  └──────────────┬──────────────────────┘     │   │ sampling_rate       │  │
│                 │                            │   │ FAIL-OPEN + coverage│  │
│  ┌──────────────▼──────────────────────┐     │   └──────────┬──────────┘  │
│  │ TOOL PROXIES (MCP — least privilege)│     │              │ score       │
│  │ sim_refund │ sim_book │ sandbox_exec│     │              │             │
│  │ schema_ast │ nli_claim│ (NO prod    │     │              │             │
│  │ Identity from RunContext, not JSON  │     │              │             │
│  │ Eval MCP ≠ corp Git / live payments │     │              │             │
│  └──────────────┬──────────────────────┘     │              │             │
└─────────────────┼────────────────────────────┼──────────────┼─────────────┘
                  │                            │              │
                  ▼                            ▼              ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER                                                         │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐  │
│  │ Golden     │ │ Immutable  │ │ Trace      │ │ Annotation │ │ Run-key │  │
│  │ datasets   │ │ experiments│ │ store      │ │ queues     │ │ ledger  │  │
│  │ auto-ver / │ │ extended   │ │ 14d vs     │ │ run vs PAQ │ │ suite+  │  │
│  │ as_of pin  │ │ by default │ │ 400d       │ │ → promote  │ │ dataset+│  │
│  │ ≠ 14d TTL  │ │ (LangSmith)│ │ OTLP       │ │ copy       │ │ harness │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └─────────┘  │
│  Inspect: transcripts persist under --no-score; inspect score is job 2    │
│  SWE: cache key = (run_id, instance_id) NOT the patch hash                │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Typical store | Oracle | Fail policy |
| --- | --- | --- | --- | --- |
| **Harness (control)** | Suite registry, dataset pin, experiment matrix, CI gate | Versioned dataset + immutable experiment | Reference outputs, hidden tests, DB goal-state, rubric | Fail-closed (non-zero exit) |
| **Tracing (data)** | Live nested spans; user SLO clock | Trace store (14d vs 400d, or self-hosted OTLP) | None on the request path | Serve the user |
| **Judge (sidecar)** | Code / LLM-as-judge / human | Feedback attached to run/span | Score + comment; who/what scored | Fail-open online (skip + coverage%); fail-closed in CI |

LangSmith’s published split is the cleanest vendor topology: **offline** evaluations run on *datasets/examples* (inputs + optional reference outputs) and produce an *experiment*; **online** evaluations run on *runs/threads* from a tracing project (inputs/outputs only — **no** reference). Evaluators are workspace-level resources; sampling rate, filters, and weekly spend caps are **per attachment**. Braintrust is isomorphic: playground → immutable experiment → CI (`bt eval`) → online scoring on logs → promote traces into datasets. Scorers have **span vs trace vs group** scope. A trace-scoped rule waits an **idle timeout (default 30 seconds)** after the last span; scorer-written spans do not restart the idle timer; new *application* spans after scoring **can** cause a rescore. Phoenix is the OpenTelemetry twin: traces over OTLP with OpenInference span kinds; evals are annotations via `run_evals` / `llm_classify`, typically under `suppress_tracing()` so judge calls are not themselves product traces.

**Control vs data plane (enterprise).** Braintrust: control plane (UI, auth, org metadata) is vendor SaaS; data plane holds experiments, traces, datasets, completions. SDKs send data **directly to the customer data plane**. Hybrid/BYOC/self-hosted data planes are Enterprise; Notion and Ramp are the public hybrid mentions. LangSmith Enterprise: Cloud / Hybrid / Self-Hosted. Phoenix self-hosts the whole stack. Interview move: **eval datasets are as sensitive as production logs** because they *are* production logs that someone promoted. If the data plane holds PII, the judge model is a **subprocessor** on every online-eval call.

**On-policy vs replay.** The runner must reproduce the production **agent**, not bypass routing, retrieval, memory, policy, or tool wrappers. Offline replay can rescore stored outputs cheaply; it cannot estimate how a changed policy, prompt, model, or tool result would alter later actions. Inspect: `inspect eval` runs solvers; `inspect score` / `--no-score` defers grading. Gaia2/ARE: `are-benchmark run` vs `are-benchmark judge`. Two-phase is how a judge outage does not lose agent rollouts.

#### Request-flow narrative — three clocks, one promotion

**A. CI offline eval (fail-closed, reference OK).**

1. Control pins `(suite_version, dataset_version / as_of, harness_commit, model_id, k)`. A golden-set edit cannot silently change a ship gate.
2. Experiment controller expands the model×scaffold matrix into trials. Idempotency key = that tuple + `run_id`.
3. Runner invokes the **complete** agent. Environment loads the snapshot (τ DB, SWE instance image at `base_commit`, Gaia2 universe). Trial 2 must not inherit trial 1’s booked flight.
4. Trace collector writes append-only spans (`trace_id` / `task_id` / `trial` / `span`). LangSmith hard-rejects past **25,000** runs on one `trace_id`.
5. Grader: deterministic oracle first (tests, DB state, AST). Soft rubric only if the hard bit passed — or as a *separate* metric, never averaged into the hard bit.
6. Statistics: unit = **task**, not step. Reduce epochs with `pass_at_k` / `pass_k_k` / mean. Infra errors in a **separate bucket** from unresolved.
7. Release gate: Promptfoo `jq '.results.stats.failures'` non-zero → exit 1; LangSmith official example accuracy **≥ 0.85** on `ExperimentResults`; Braintrust `Reporter.reportRun → false` (default `bt eval` exits non-zero on **exceptions**, not score regressions). Signed decision + threshold + expiry.

**B. Async online scoring (fail-open, no reference).**

1. User request completes on the **SLO clock**. Root span ends. **No judge in this thread.**
2. Sidecar: wait idle timeout (Braintrust **30 s** default) or LangSmith online evaluator on ingested traces. `sampling_rate` / attachment filters. Weekly evaluator USD cap **pauses the evaluator**, does **not** pause the agent, does **not** backfill skipped runs.
3. Reference-free only: schema, safety, sampled LLM-as-judge, RAGAS faithfulness vs *this* retrieve. A gold-NLI grader here silently no-ops or compares against empty gold.
4. Matching traces **auto-upgrade** to extended retention on LangSmith (14d → 400d) — a **billing** event, not a latency event.
5. Dashboard: score **and** coverage%. Unscored ≠ passed. Braintrust alerts must include `scores.your_scorer_name IS NOT NULL` or they fire on async lag.

**C. Promotion gate (trace → golden).**

1. Hamel protocol: error-analyze ≥100 traces; open-code failures; axial-code a taxonomy; stop when ~20 new traces add no category; **code assertions first**, LLM-as-judge only for residual subjective failures. Start datasets at **10–20** manually curated examples.
2. PII pipeline **before** ingest, not after promotion (detect → redact → audit — §4.4). Hide-all I/O makes offline eval **impossible**.
3. Annotation queue: single-run items can **Add to Dataset** (copy; reviewer may edit). Thread items add the whole conversation and **do not** support a default dataset. Pairwise annotation queues (PAQs) **cannot** add items to a dataset. Reservations prevent double-score.
4. Dataset auto-versions; tag the version CI uses. Promotion is a **retention-class change**: a 14-day debug email becomes a golden-set immortal.

**Vendor topology (interview traps):**

| Platform | Topology | Eval-specific fact |
| --- | --- | --- |
| **LangSmith** | SaaS / Hybrid / Self-hosted | Offline on datasets; online on traces; evaluator spend cap; experiments default **extended** |
| **Braintrust** | SaaS / BYOC / self-hosted data plane | Idle timeout 30 s; span/trace/group; `sampling_rate`; SDK never through Braintrust servers |
| **Phoenix** | Self-host OSS + Arize AX | OTLP + OpenInference; `llm_classify` `max_retries` default **10**; `UNPARSABLE` is a third class |
| **Promptfoo** | Local CLI + optional cloud | OpenAI agreed to acquire **2026-03-09**; official hosted-Evals migration; CI on `results.stats.failures` |
| **Inspect AI** | Local Python (UK AISI) | Epochs + `pass_at_{k}` / `pass_k_{k}`; clustered `stderr`; deferred score |
| **Datadog Agent Observability** | SaaS APM-adjacent | Bills **LLM spans only**; evals included; 15-day default retention |
| **OpenAI Evals (hosted)** | Being retired | Read-only **2026-10-31**; shutdown **2026-11-30** — a CI job still calling it is a **hard outage** |

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants

**I1.** The harness is in the product \(\mathcal{S} = (M, H, T, E, O, n, r, I)\). Quote \(M\) alone and you have not measured. Anthropic: Terminal-Bench 2.0 most- vs least-resourced setups **6 pp** (\(p < 0.01\)); SWE-bench RAM up to 5× baseline, 227 problems × 10 samples, **+1.54 pp** monotonically. Treat leaderboard gaps **below 3 pp** with skepticism until resource methodology is matched; naive binomial CIs already span 1–2 pp and infra noise **stacks on top**. SWE-agent: same GPT-4 Turbo, ACI vs raw shell → **64% relative** (12.47% = 286/2,294 vs Shell-only).

**I2.** Dual-oracle: hard gate ∧ soft score. Safety/PII/tool-side-effect **must not** be averaged into a composite. LangSmith: *testing* asserts correctness (“a system can only be deployed if it passes all tests”); *evaluation* measures, often relative/fuzzy. Convert a metric into a regression test that must beat a baseline — that is the ship gate.

**I3.** Statistical unit = **task** (Miller 2024). Scoring every tool span and averaging as if \(n =\) spans **overstates** precision. 50 SWE instances × 20 steps is **n ≈ 50 tasks**, not n = 1,000 steps. Cluster when tasks share a repo/user/template (`stderr(cluster=)` in Inspect). \(n\) below a few hundred: bootstrap/Bayes, not CLT 1.96·SE (2025 position paper).

**I4.** Unscored ≠ failed ≠ passed. OpenAI hosted `python` grader: any exception / non-float → **0** (aliases grader crash with agent fail). Inspect `pass_at` returns **NaN** when \(n < k\) (avoids the spurious **1.0** short-circuit). Log `grader_status ∈ {scored, error, timeout}`.

#### 2.2 pass@k — unbiased capability, needs a verifier

Chen et al. (arXiv:2107.03374). Functional correctness, not BLEU. Generate \(n \ge k\) samples per task, count \(c\) that pass unit tests:

\[
\operatorname{pass}@k \;=\; \mathbb{E}_{\text{problems}}\left[1 - \frac{\binom{n-c}{k}}{\binom{n}{k}}\right]
\]

Numerically stable product (paper Appendix A; naive \(1-(1-\hat p)^k\) is **biased**):

\[
1 - \prod_{i=n-c+1}^{n}\left(1 - \frac{k}{i}\right) \quad\text{when } n-c \ge k,\ \text{else } 1.0
\]

Original paper: \(n=200\), \(k \le 100\), **164** handwritten Python problems, mean **7.7** tests/problem. pass@1 ≈ “one sample works.” pass@k ≈ “something in a candidate set works **if you have a verifier to pick the winner**.” Best-of-N with a unit-test oracle **is** pass@k. Best-of-N with an LLM judge is **not** the same estimator.

Inspect `pass_at(k)` is this product form; **NaN** if fewer than \(k\) scored epochs remain after NaN-filter.

**HumanEval+ (EvalPlus, NeurIPS 2023):** same 164 problems; tests scaled **80×** (LLM-seeded + type-aware mutation). Across 26 models, pass@k dropped up to **19.3% (pass@1) / 24.9% (pass@10) / 28.9% (pass@100)**; greedy pass@1⋆ up to **23.1%**. Rankings flipped. Original gold had bugs (e.g. `#124` `validate_date`). A weak **oracle** inflates pass@k even when the estimator is unbiased.

#### 2.3 pass^k — reliability, what the user feels

Yao et al., τ-bench (arXiv:2406.12045). Probability that **all** \(k\) independent trials succeed, averaged over tasks. Closed form \(p^k\) under a per-task success rate \(p\). Inspect `pass_k(k)` is the draw-without-replacement dual \(\binom{c}{k}/\binom{n}{k}\) — also NaN when \(n<k\).

τ original: even GPT-4o-class function-calling agents succeed on **<50%** of tasks; retail **pass^8 < 25%**; airline pass@1 **35.2%** for GPT-4o. Success = **final DB state == annotated goal** — no LLM judge on the pass/fail bit.

Anthropic “think” tool (Claude 3.7-era, τ-airline):

| Configuration | pass^1 | pass^2 | pass^3 | pass^4 | pass^5 |
| --- | --- | --- | --- | --- | --- |
| Think + prompt | 0.584 | 0.444 | 0.384 | 0.356 | 0.340 |
| Think only | 0.404 | 0.254 | 0.186 | 0.140 | 0.100 |
| Extended thinking | 0.412 | 0.290 | 0.232 | 0.192 | 0.160 |
| Baseline | 0.332 | 0.206 | 0.148 | 0.116 | 0.100 |

Retail Think (no extra prompt): pass^1 **0.812**, pass^5 **0.626**. Max steps **30 → 100** (model completions); most trajectories still finished under 30; one exceeded 50.

**On Randomness in Agentic Evals (arXiv:2602.07150):** 60,000 trajectories, 25.58B tokens, 1.88M tool calls; ten independent runs per SWE-bench Verified configuration. Single-run pass@1 varies **2.2–6.0 pp**; SD **>1.5 pp even at temperature 0**. Trajectories diverge in the **first ~1% of tokens**. Gaps up to **24.9 pp** between pass@k (best-case envelope) and pass^k (worst-case). A 31%→33% single-run “win” is often sampling noise. Estimate pass@1 from multiple independent runs; report **both**.

Retry inflation: production agent retries a tool 3× **and** the harness retries failed examples → undeclared pass@k. Inspect epochs + reducers are the honest API. LangSmith `num_repetitions` re-runs **target and all evaluators**. Silent retries in the agent look like reliability and **double-refund** in prod unless the environment is idempotent.

#### 2.4 Dual-oracle and the six dimensions

Do not collapse these into one “quality”:

1. **Task success** — policy-compliant outcome happened? (DB row, tests green, exact constraint; rubric only if no state exists.)
2. **Trajectory** — steps legal, efficient, policy-faithful?
3. **Tool accuracy** — right tool, args, order, side effects?
4. **Quality** — correctness, completeness, tone, safety as **separate** scores.
5. **Cost** — $/task including agent, tools, sandbox, **and** judge.
6. **Latency** — TTFT/e2e on the **user** path; harness wall-time separately.

Task-success hierarchy (highest applicable): (1) deterministic outcome, (2) weighted milestones, (3) semantic rubric against evidence, (4) human adjudication. **Do not** grade success from the final NL claim. “Booked” ≠ a reservation row.

Hosted graders (OpenAI, retiring with Evals): `string_check`, `python` (exception → 0), `text_similarity`, `score_model` / `label_model`, `multi`. `string_check` on function args under-rewards `1` vs `1.0` — canonicalize JSON, do **not** jump to `score_model`. Inspect: `match`/`includes`/`pattern`/`choice` vs `model_graded_*` (regex `GRADE: C|P|I`; optional partial 0.5; optional majority vote). DeepEval: `ToolCorrectnessMetric` primarily **deterministic** vs `expected_tools`; if `available_tools` is set, a second LLM optimality score is taken and the metric returns the **min**; `DAGMetric` keeps stochasticity confined. Promptfoo YAML is a dual-oracle kit: deterministic `equals`/`is-json`/`regex`/`python`/`trajectory:tool-sequence` **alongside** `llm-rubric`, not instead of it.

HealthBench (rubric template): **5,000** multi-turn; **262** physicians / **60** countries / **49** languages / **26** specialties; **48,562** unique criteria; median **11**/example (range 2–48). Grader **GPT-4.1** (macro-F1 **0.709** vs physicians; o4-mini/o3 slightly worse). Score = weighted points met / max. o3 **~60%**; GPT-4o **32%**; GPT-3.5 Turbo **16%**. Physician–physician agreement on consensus criteria **55–75%** — disagreement is inherent. Scores **partially correlate with length**.

#### 2.5 LLM-as-judge vs deterministic oracles

**When a schema/test/DB exists, use it.** BFCL is AST + state-transition, **not** an LLM judge on classic tracks. ARE Verifier compares **write** actions to a minimal oracle write sequence (unlimited reads are free): consistency, causality (DAG), timing windows, completeness. On **450** hand-labeled trajectories vs an in-context whole-trace judge (same Llama 3.3 70B): agreement **0.98 vs 0.72**, precision **0.99 vs 0.53**, recall **0.95 vs 0.83** — the unconstrained judge **accepts too readily**. Default ARE judge: Llama 3.3 70B Instruct, **temperature 0**; `--judge_model` is **independent** of the agent.

Published calibration anchors (do **not** transplant as your SLO):

| Judge / verifier | Human agreement or meta-eval | Caveat |
| --- | --- | --- |
| Zheng GPT-4 vs humans (MT-Bench/Arena) | **>80%**, matching human–human on that set | Position/verbosity/self-enhancement remain |
| RAGAS faithfulness vs WikiEval | **~95%** pairwise vs **~72%** direct GPT | Construct = context entailment, **not** world truth |
| RAGAS answer / context relevance | **78% / 70%** | Too weak for a ship gate |
| G-Eval GPT-4 vs SummEval | Spearman **0.514** avg | Biased to LLM-generated text |
| HealthBench GPT-4.1 vs physicians | macro-F1 **0.709**; MD–MD **55–75%** | Contested construct |
| SimpleQA ChatGPT grader | **2 / 300** informal | Not a formal κ |
| ARE Verifier vs labeled trajectories | **0.98 / 0.99** vs in-context **0.72 / 0.53** | Write-oracle, not whole-trace vibe |
| Guanaco GPT-4-as-judge vs humans | Kendall τ **0.43**, Spearman **0.55**, Fleiss κ **0.25** | Rank-level, not binary gates |

Zheng residual biases that **do not go away**: most judges prefer **first** position (GPT-4 consistent **>60%**; few-shot **65.0% → 77.5%** at **4×** prompt cost **without** lifting human agreement); verbosity (“repetitive list” attack); self-enhancement (GPT-4 **+10%** win rate for itself vs humans; Claude-v1 **+25%**; GPT-3.5 did **not**); LLM-generated text preference (G-Eval authors). Bias is **worse when models are close**. G-Eval original score is a **probability-weighted** sum over score tokens (20 samples at T=1 when logprobs were unavailable); most UIs skip this — more flake than the paper. DeepEval: use `DAGMetric` when you need determinism.

Judge validation protocol: hidden calibration set, ≥2 qualified humans; confusion matrix; P/R/F1 for **hard** gates; rank correlation for rubrics; calibration **by slice**; abstention rate; human–human baseline. Promote a judge only if it beats the previous version on that set — same rule as promoting the agent.

Pointwise (HealthBench, RAGAS, G-Eval, SimpleQA, online sampling) vs pairwise (LangSmith PAQ, Arena, Zheng swap). Pairwise when two candidates share a prompt and you do not trust absolute scores. LangSmith PAQs pair two experiment sessions **in chronological order** — trailing unpaired runs if B is shorter.

#### 2.6 RAGAS-class metrics (eval intersection only)

Faithfulness (Es et al., EACL 2024). Extract atomic claims → NLI vs **retrieved context** → fraction supported:

\[
\text{Faithfulness} = \frac{|\{c \in \text{claims} : \text{context} \models c\}|}{|\text{claims}|}
\]

Official two-claim Einstein example → **0.5** when the date is wrong. **Not “true in the world.”** A faithful answer can still be the wrong answer if retrieval missed the doc — pair with answer relevancy + context precision/recall. Context recall typically needs a **reference**. Context precision is rank-weighted; **ID-based** when chunk IDs exist. Answer relevancy: \(n\) hypothetical questions from the answer, mean cosine vs the user question (`text-embedding-ada-002` in the paper); cosine \(\in [-1,1]\), so the metric is **not guaranteed** in \([0,1]\).

DeepEval `FaithfulnessMetric` is a **different construct**: a claim is truthful if it **does not contradict** retrieved facts — not “must be entailed.” Empty verdicts → **1.0**; `"idk"` **passes** unless `penalize_ambiguous_claims=True`. Default can be **1.0 on totally unsupported answers**. `HallucinationMetric` compares against provided ground-truth `context`, not retrieved chunks. Do not mix RAGAS entailment and DeepEval contradiction-only on one SLO.

Citation correctness is a **separate** construct (ALCE-style precision/recall on IDs ⊆ retrieved set). RAGAS faithfulness does **not** catch invented `[doc 17]` if no claim is extracted from the citation token. Constrain decode / tool-only citations.

Self-RAG reflection tokens (`Retrieve` / `ISREL` / `ISSUP` / `ISUSE`) are a **model-internal** critic, not a substitute for an external faithfulness oracle. Weights in the paper: \(w_{rel}=1.0\), \(w_{sup}=1.0\), \(w_{use}=0.5\).

#### 2.7 Trajectory vs stateless tool-call; complexity of \(k\)-sample evals

| Stage | Question | Metric |
| --- | --- | --- |
| Need / abstain | Should any tool be called? | Decision P/R; BFCL irrelevance (**240** non-live + **882** live) |
| Selection | Correct authorized tool? | Top-1; unsafe-selection rate |
| Arguments | Names, types, values? | Schema-valid rate; field exact/F1 |
| Ordering | Prerequisites? | Dependency-violation count |
| Execution | Did the tool succeed? | Success/error/timeout by tool |
| Result use | Did the agent read state? | Grounded response; omission rate |
| Side effect | Intended mutation? | State-delta match; duplicate-write rate |

BFCL simple/live AST is **stateless**. τ / τ² / Gaia2 / SWE are **stateful**: reset between trials is load-bearing. BFCL V4 overall = **Agentic 40% + Multi-Turn 30% + Live 10% + Non-Live 10% + Hallucination 10%**; unweighted averages **inside** a bucket (cannot farm a huge subcategory). A model can ace BFCL Live AST and fail τ policy. Ship gates need **both**.

AgentLens (2026): 2,614 OpenHands trajectories, 60 SWE-bench Verified tasks; **10.7%** “Lucky Passes” (correct outcome, weak process); lucky rates **0.5–23.2%**; process-quality rankings **disagree** with pass-rate rankings for every model in the PTA subset. Outcome-only gates hide this.

**Complexity (k-sample).** Per task: sample \(n \ge k\) independent rollouts. Agent+env tokens \(\Theta(n \cdot T_{\text{rollout}})\). Environment reset \(\Theta(n)\) snapshots (container from instance image; DB clone). Counting \(c\) is \(\Theta(n)\). Unbiased pass@k product is \(\Theta(n-c) \subseteq O(n)\). pass^k binomial ratio is \(O(k)\) after \(c\) is known. **Judge** can be a second job (Inspect `--no-score`): extra \(\Theta(n \cdot C \cdot T_{\text{judge}})\) for \(C\) rubric criteria (HealthBench median 11 → 11× grader calls). pass@k does **not** extra-sample beyond the \(n\) epochs already paid for. Space: store \(n\) transcripts per task; LangSmith 25k-run cap is the trace-shape ceiling, not the suite size. Gaia2: 3 scenario repeats at T=0.5 is **not** pass^k — report the repeat protocol.

Power (Miller): detecting a 3 pp difference at \(\alpha=0.05\), \(\beta=0.20\), \(\sigma^2\approx 1/9\) needs **n ≈ 969** independent questions. A 50-item golden set cannot support a 3 pp claim. Raising \(K\) (repeats per question) from 1 to 10 on n=198 dropped MDE **13.2% → 7.5%** in their worked nondeterministic example. Paired A vs B on the same task IDs: \(\mathrm{SE}_{A-B}=\sqrt{\mathrm{SE}_A^2+\mathrm{SE}_B^2-2\,\mathrm{SE}_A\mathrm{SE}_B\mathrm{Corr}}\). Corr=0.5 uniform scores → **1/3** less variance than unpaired. Do **not** bootstrap each model’s CI separately and check overlap.

#### 2.8 Contamination, freshness, private holdouts

Never treat aggregator “96% Verified” pages as an SLO. Quote **named split + named scaffold + date + contamination status**.

| Control | Published instance |
| --- | --- |
| **Private holdout** | Gaia2 test set (Meta/HF); SWE-bench Pro held-out 12 repos + commercial 18; GSM1k never released |
| **Copyleft wall** | SWE-bench Pro public: GPL repos to deter training scrape |
| **Time-segmented** | LiveCodeBench: problems after a cutoff; DS-Ins-33B drops after Aug 2023; GPT-4o after Nov 2023; Claude-3S after Apr 2023 |
| **Distribution-matched twin** | GSM1k: 1,000 human-written GSM8k-style items; drops up to **8 pp** |
| **Item identity** | Hash of prompt+tools+env seed; LangSmith auto-version; Braintrust snapshots (Pro+); Inspect `revision=<commit_sha>` |

SWE-bench Verified: OpenAI (2026) stopped reporting — frontier models reproduce the **human gold patch** verbatim. Independent: Claude localizes files **3×** better on Verified than on BeetleBox / SWE-rebench given issue text only. SWE-bench Pro: public-split frontier pass@1 **23.3% → 80.3% in eight months**; July 2026 audit **~30%** broken; OpenAI **retracts** the Pro recommendation. GAIA: 466 questions; human **92%** vs GPT-4+plugins **15%**; L1/L2 near-saturated by 2025 → Gaia2 (800 scenarios × 10 universes × 101 tools; test set **private**).

SimpleQA as a **refusal-aware hard-gate pattern** (not a RAG metric): **4,326** items; grade correct / incorrect / **not attempted**; F-score = harmonic mean of overall-correct and correct-given-attempted. Hedged wrong answers are **incorrect**. Do not substitute for RAGAS faithfulness (world-fact vs retrieved-context).

Inspect BEST_PRACTICES: stable sample IDs; pin HF `revision=`; pin GitHub URLs to SHAs; store reducible numbers in `Score.value` not `Score.metadata` when `epochs>1`.

---

### 3. Token Economics & NFR Analysis

Eval cost is a **second product**:

\[
\text{eval \$} = (\text{agent tokens} + \text{tool I/O} + \text{sandbox time}) \times |\mathcal{D}| \times \text{reps} \times (1 + \text{judge tokens} \times \text{criteria}) + \text{platform traces}
\]

HealthBench median 11 criteria × 5,000 examples = **55,000 grader calls per model**. pass^k multiplies agent+environment by \(k\). Online eval = traffic × sample rate × judge.

#### 3.1 `$ per 1k eval tasks` **[inferred]** (assumptions stated)

Public vendors do **not** sell a portable “eval task” SKU. Multiply published meters by a stated loop.

**Platform meters only (no model tokens):**

| Meter | Published | **[inferred] / 1k tasks** |
| --- | --- | --- |
| LangSmith experiment = extended traces | 0.50¢ = **$0.005**/trace | **$5.00** |
| LangSmith base traces, online eval **opt-out** | 0.05¢ = **$0.0005** | **$0.50** |
| Braintrust Pro on-demand scores (after included 50k/mo) | **$1.50 / 1k scores** | **$1.50** (score meter only) |
| Datadog LLM-span overage | **$3.50 / 10k** LLM spans | **$0.35 / 1k spans**; 8 LLM spans/task → **$0.28 / 1k tasks** if already past 100k included |
| Phoenix / Inspect / Promptfoo OSS | no per-eval SKU | **$0** platform; you pay GPU/CPU + provider tokens |

LangSmith Plus: **$39**/seat/mo + 10k base traces then PAYG. Developer: $0/seat, 5k traces. Experiments created at **extended** (400-day) by default. Online eval **auto-upgrades** matching traces. Third-party TCO posts quoting **$2.50 / 1k** base traces **do not match** official 0.05¢ = **$0.50 / 1k** — use the conceptual billing page.

Braintrust Pro **$249**/mo: 5 GB then **$3/GB**; 50k scores then **$1.50/1k**; 30-day retention then **$0.50/GB/mo**. Datadog Pro annual: **$160**/mo first **100k** LLM spans; tool/workflow/agent/embedding/retrieval spans **free**; evals **no separate product fee** — you still pay the **provider** for judge tokens.

**Judge-token reference loop.** Anthropic Sonnet-class list **$3 / $15** per MTok in/out (cache read **0.10×**). 1k judge-only calls, 2,000 input + 200 output, no cache:

\[
1000 \times (2000\times 3 + 200\times 15)/10^6 = \$9
\]

Stable rubric prefix cached after first write (0.1× on 1,800 prefix, 200 unique): ~**$1.1** input-side after warmup **[inferred]**.

**All-in worked examples [inferred], not a quote:**

| Loop (stated mix) | Arithmetic (from research) | **[inferred] $ / 1k** |
| --- | --- | --- |
| **Judge only** 2k/200 Sonnet, no cache | above | **$9** agent-side $0 |
| **τ-like agent** 8k in / 1.5k out, 70% cache read on 6k prefix, 1 trial | \(1000\times[(0.3\times2000+0.7\times6000\times0.1)\times3+1500\times15]/10^6\) | **≈ $26** agent |
| Same + LangSmith extended + one trace-judge | $26 + $5 + $9 | **≈ $40** |
| Same, **pass^4** | agent+sim × ~4 | **≈ $104** agent before judge |
| **Nightly 200-task** 8k/1.5k uncached + one 2k/200 judge + 200 extended traces | agent **$9.3** + judge **$1.8** + LS **$1** | **≈ $12** before Docker; pass^5 agent **~$48** |
| **Online** 1M req/mo, 1% sample, one Sonnet judge/request | 10k × $9/1k | **$90** judge + LS upgrade of those 10k ≈ **$50** |

HAL published τ-airline snapshot (scaffold+model, **not** a portable SLO): o4-mini High **$11.36** vs Claude Opus 4.1 **$180.49** for comparable ~54% accuracy.

HealthBench-scale: 55k GPT-4.1 grader calls at ~1.5k in / 80 out is a **five-figure** line item per checkpoint — **order-of-magnitude only**; do not quote without tokenizer counts.

> ⚠️ Gap: this research does **not** publish a human-annotator $/task (no MTurk/Scale SKU). The human term in “agent + judge + human” is `queue hours × your loaded cost`, not a list price. Do not invent one. Annotation queues are the **capacity** meter; dollars are yours.

**Cache advice (what to meter):** SWE = agent tokens + **Docker minutes**; new `run_id` per patch. τ = agent + **user-sim** LLM × trials; user-sim and agent must **not** share a cache key. Gaia2 = 101-tool system prompt (cache) + 70B judge on writes (not). Rubric = cache the template, bind example-specific criteria after the breakpoint. BFCL AST = **do not** LLM-judge schema equality. `LANGSMITH_TEST_CACHE` (VCR) is for **scorer iteration**; poisonous if you think you re-measured the agent. SWE result cache is worse: it can hide a new patch.

#### 3.2 Latency SLA — two clocks, numeric ms

> ⚠️ Gap: OpenAI, Anthropic, LangSmith, Braintrust, Phoenix, and Datadog do **not** publish production p50/p95/p99 of *your* online judge, nor a portable chat p99. Do not use eval-harness wall-time as an SLO. Numbers in the **sidecar** table are architecture-derived **[inferred]** from published timeouts + a 2k-in/200-out judge class. Measure time-to-score yourself.

**User-facing path (product SLO clock).** Online eval **MUST NOT** sit here. Every serious vendor (Braintrust, LangSmith, Datadog) places scoring **after** the root span. Eval’s contribution to user latency, by architecture:

| Metric | Eval tax on user path **[inferred policy]** | If you put a sync judge on the handler (anti-pattern) |
| --- | --- | --- |
| **p50** | **0 ms** | **+1,200 ms** (one 2k/200 judge) |
| **p95** | **0 ms** | **+4,000 ms** |
| **p99** | **0 ms** | **+12,000 ms** (then the judge 429 becomes a **user** 500) |

The product’s own TTFT/e2e p50/p95/p99 are **out of this module** — they belong to the agent serve design. The eval contract is: **add 0 ms**, plus a sidecar time-to-score SLO so scores are late, not the chat.

**Sidecar time-to-score (root span end → score attached).** Published primitives, not SLOs: Braintrust idle timeout **30,000 ms** default; inline scorer timeout **240,000 ms**; bundled code **30,000 ms**; query **30,000 ms**; Gaia2 `--scenario_timeout` default **300,000 ms**/scenario (harness floor on a hung tool, **not** a sidecar SLO); Phoenix `llm_classify` `max_retries` default **10**.

Split **idle wait** (conversation grouping) from **judge compute**. Idle is a product of “no conversation-ended signal,” not model latency.

| Path | **p50** | **p95** | **p99** | Mitigation |
| --- | --- | --- | --- | --- |
| **Deterministic oracle** (schema / AST / DB match, local) **[inferred]** | **20 ms** | **80 ms** | **250 ms** | Keep this on a thread pool; never block the user; timeout 500 ms **[policy]** then `grader_status=timeout` |
| **LLM-as-judge compute only** (2k/200, T≈0, no idle) **[inferred]** | **1,200 ms** | **4,000 ms** | **12,000 ms** | Circuit-break the judge API; majority-of-3 only on **CI** ship gates; structured output; temp 0 |
| **Time-to-score, Braintrust-class** (idle 30 s + judge) **[inferred]** | **31,200 ms** | **34,000 ms** | **42,000 ms** | Alert only where `score IS NOT NULL`; coverage% NFR; do not page on unscored during idle |
| **Time-to-score hard cap** (published timeouts) | — | — | **bundled 30,000 ms** / **inline 240,000 ms** | Hung judge = unscored row, **not** a slow user; `exit_on_error` fail-closes a **batch**, opposite of online |

CI / experiment wall-clock is **not** an SLO. With `max_concurrency=N` and i.i.d. example times, wall-clock ≈ \((n/N)\times\) mean + tail from retries. A single Gaia2 hung tool sets a **300,000 ms** floor on **that example**. LangSmith Plus ingest caps produce **429s**, not a published `evaluate()` p99.

**Mitigations mapped to percentiles:**

- **p50 (sidecar):** prefer code oracles; cache rubric prefixes (0.1×); `suppress_tracing` on judge calls so you do not trace the tracer.
- **p95:** sampling_rate is the load-shed; spend cap pauses the **evaluator**; two-phase `inspect score` when the judge fleet is saturated.
- **p99:** timeout the judge independently (Braintrust 240 s inline is the published ceiling, not a target); skip the score; never retry the **user** request because a judge 429’d.

#### 3.3 Throughput and back-pressure

Vendors do **not** publish “eval tasks / second” SLOs. Binding published ceilings:

| Ceiling | Number | Effect |
| --- | --- | --- |
| LangSmith Plus ingest | **500,000 events / hour**, **5.0 GB / hour**, UTC clock-hour | 1k-task agent × 8 LLM spans = 8k events — under Plus. A load test that traces every token **429s and drops traces**; coverage% lies. |
| LangSmith Developer (no card) | **50,000 events / hour**, **500 MB / hour** | Personal-tier CI 429s first. |
| LangSmith `/runs/multipart` | **6,000 req / 10 s** (support article) | SDK retries transient 429s; **sustained** limits drop traces. |
| Braintrust function executions | **10,000 / 10 s** | Scorer fan-out is the bottleneck, not the agent. |
| Braintrust per-span payload | **20 MB** | Hide embeddings (`OPENINFERENCE_HIDE_EMBEDDING_VECTORS`) is ingest-cost **and** this cap. |
| Gaia2 | 300 s timeout × `--max_concurrent_scenarios` | Throughput ≈ concurrent × (1/mean scenario time). |
| SWE-bench | Docker GB + `--cache_level` (default `env`) | Throughput is **containers**, not tokens. Full prebuilt images = **hundreds of GB**. |

**[inferred] Plus-plan ingest headroom:** 500k events/hour ÷ 10 spans/task ≈ **50k tasks/hour** before the event 429 — only if payloads stay under 5 GB/hour. 20 kB I/O per span × 500k = **10 GB** hits the **data** 429 first.

**Back-pressure design:** (1) admit online scoring with `sampling_rate` and attachment filters; (2) bulkhead **judge API** / **sandbox fleet** / **user serve** — a judge TPM storm must not stall chat; (3) LangSmith weekly USD cap is the published judge circuit breaker (in-flight may **overshoot**; skipped runs **not backfilled**); (4) degrade: skip online score rather than block the user; CI stays fail-closed; (5) two-phase persist transcripts, grade when the judge has capacity; (6) SWE: unique `run_id` per candidate so cache cannot swallow a new patch.

#### 3.4 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of the gate vs product** | Product serve is the SLO. Online eval is **best-effort** (fail-open + coverage%). CI gate is **fail-closed** and may go red while prod is green — that is correct. LangSmith spend cap pauses the evaluator, **not** the agent. Hosted OpenAI Evals after **2026-11-30** is a **hard outage** of the *gate*, not of chat. | Coverage% vs $; a tripped breaker that is not on the dashboard paints quality green |
| **RPO of golden sets** | Goldens are a **longer-lived legal record** than 14-day traces (LangSmith datasets persist independently of trace TTL). RPO = last tagged dataset version / Braintrust snapshot (Pro+) / git SHA of Promptfoo YAML + HF `revision=`. A reviewer edit in an annotation queue is a write. | Freshness (promote failing traces) vs stability (pin `as_of` in CI) |
| **RTO of golden sets** | Rollback = pin previous dataset tag / `as_of` (seconds). Recreating a contaminated public bench is **weeks**. After hosted Evals shutdown, RTO for *that* control plane is “migrate to Promptfoo/Inspect” **before** 2026-11-30. | Velocity of suite edits vs ship-gate reproducibility |
| **RPO/RTO of traces** | 14d vs 400d (LangSmith extended **10×** $); Datadog 15d default + 30/60/90-day add-ons at **$1.50 / $3 / $4** per 10k LLM spans. Online eval auto-upgrade is a retention **and** billing event. | Debug window vs $ |
| **Compliance** | LangSmith shared-responsibility: **no PCI DSS cardholder data** on the platform; mask PII at source. Judge = subprocessor (Engine lists OpenAI, Anthropic, Fireworks, Baseten; ZDR per analysis task — still a subprocessor). Health/finance gold → self-hosted Phoenix / Braintrust hybrid, not SaaS + cloud judge. GDPR erasure of a promoted email is **dataset version surgery**, not trace expiry. | Online-judge coverage vs residency |
| **Correctness vs flake** | Temp 0, structured output, order-swap pairwise, majority of 3 on **CI**; code oracles first. T=0 still has SD >1.5 pp (Randomness paper) — do not claim determinism. | $ (k trials, majority judges) vs ship confidence |
| **Eval as product vs research bench** | Internal private holdout + rolling production tickets after a cutoff. Public Verified/Pro are procurement **risk**. | Marketing scores vs construct validity |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_gold = last tagged `as_of` (minutes since last human edit if you do not tag). RTO_gold = retag (seconds) vs rebuild from traces (hours, and only if traces were not TTL’d). RPO_scores = last attached feedback; a spend-cap skip is **unrecoverable** (no backfill) — RPO for those traces is “never scored.”

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: eval jobs, idempotent run keys, environment reset

> ⚠️ Gap: vendors publish timeouts, concurrency, and 429 classes — **not** a Temporal workflow ID for “eval job.” The pattern below is **[inferred]** from those primitives plus generic durable-execution practice.

**[inferred] enterprise pattern:** submit the suite as a Temporal workflow (or Kafka consumer group) with:

- **Idempotency key** = `(suite_version, dataset_version, harness_commit, model_id, run_id)`.
- Per-task activities with a **sandbox lease**.
- Poison queue for infra errors vs agent fails (separate buckets).
- Compensation = mark `unscored`, **not** `passed`.
- Circuit breaker on the **judge** (LangSmith spend cap is the published instance): fail-open online, fail-closed CI.

What *is* published: LangSmith online backfill is a **background job**; Inspect `--no-score` + later `inspect score`; Gaia2 `--output_dir` then `are-benchmark judge`; Braintrust `eval-action@v2` runs `braintrust eval --jsonl` (`bt eval` macOS/Linux only); Datadog evals async relative to APM.

**Environment reset is load-bearing.**

| Harness | Cache / reset footgun |
| --- | --- |
| **SWE-bench** | Cache key = `(run_id, instance_id)` **not** the patch hash. Reusing `run_id` after editing predictions is a **silent no-op**. `--cache_level` ∈ {`none`,`base`,`env`,`instance`}; default `env`. Container `sweb.eval.{instance_id}.{run_id}`. Reset = new container from instance image at `base_commit`. Gold tests hidden. |
| **τ / Gaia2** | Success depends on **initial DB/universe snapshot**. Trial 2 inheriting trial 1’s booking garbage-collects pass^k. Gaia2: 10 universes so the same scenario instantiates in different worlds. User-sim cache must not collide with agent cache. |
| **LangSmith VCR** | `LANGSMITH_TEST_CACHE` keys on HTTP identity. Treat cassettes as **grader fixtures**. |
| **Promptfoo** | `PROMPTFOO_CACHE_PATH` can make CI green without re-calling the model. |
| **Phoenix classify** | `max_retries=10` is **judge** resilience, not agent pass@k. `exit_on_error=True` fail-closes a batch. |

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Judge 429/5xx, LangSmith ingest 429, Phoenix classify retry storm, Docker pull flake, Braintrust 10k/10s | Error rate; coverage% drop while agent QPS flat | Full-jitter retries on **idempotent** score calls; do **not** retry the user; skip online score |
| **Permanent** | 4xx auth, unsupported judge model once a spend limit exists (must be OpenAI/Anthropic/Gemini with a price row), hosted Evals after 2026-11-30, `n<k` for pass@k | Non-retryable; NaN reducer; API gone | Fail-closed CI; fail-open online; **do not** fold into pass@1 = 0 |
| **Poison pill (eval item)** | One example that hangs the sandbox / blows 25k runs / 20 MB span; live-web GAIA item in CI | Per-task deadline; run-count cap | Isolate to poison queue; snapshot/mock search in CI; live only nightly |
| **Poison pill (grader)** | Rubric that scores 1.0 on `idk`; python grader exception → 0; composite that hides a safety slice | Calibration set; `grader_status`; slice dashboards | Dual-oracle; `penalize_ambiguous_claims`; never average safety |
| **Flaky grader** | Position bias, temp>0, G-Eval integer-ask vs paper’s 20-sample weights | Swap-order disagreement; epoch SD | Temp 0; both orders; majority of 3 on ship gates; DAGMetric |
| **Idempotency / cache lie** | Reused SWE `run_id`; VCR on agent calls; undeclared agent retries | Zero new Docker; cassette hit; pass@1 looks like pass@k | New `run_id`; cap retries in the **target**; log retry spans as cost |
| **Construct mismatch** | Gold-NLI on production threads; DeepEval faithfulness mixed with RAGAS | Online metric undefined for 99% of traffic | Reference-free online; pin metric construct in the dashboard name |

#### 4.3 Circuit breaker (closed → open → half-open)

Independent breakers: **judge API**, **env sandbox fleet**, **trace ingest**. A judge TPM storm must not stall chat (**bulkhead**). A sandbox OOM must not fail-open the **CI** gate into “all skipped = green.”

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

**Thresholds [policy, not vendor SLO]:** trip judge on 5xx/429 sustained and on spend-cap pause; trip sandbox on infra-error rate (not on agent `unresolved`); trip ingest on 429 that would drop coverage. Cooldown tens of seconds. One probe in half-open.

**Fallback chain (cited policy):** **deterministic oracle → cheaper/self-hosted judge → human queue / skip.** Online: **skip the score rather than block the user**. CI: non-zero exit if coverage of the *pinned set* drops or the hard oracle fails. Never fail-open a safety hard gate into a rubric average. Hedging: majority vote is for **CI judges**, not for doubling user-path latency.

LangSmith spend cap: weekly USD, resets **Monday 00:00 UTC**; in-flight may **overshoot**; skipped runs **not backfilled**; agent traffic continues. That is a judge breaker with **no replay**.

#### 4.4 Zero-Trust MCP, tool RBAC, PII pipeline, immutable goldens

**Zero-Trust MCP (eval harnesses *are* MCP clients — Gaia2/ARE attach MCP; coding-agent evals call MCP browsers/tickets).** ARE: untrusted MCP = RCE-adjacent. τ-style eval should hit **simulators**; SWE should hit **ephemeral Docker**, not corp Git. CI eval bots should not inherit user refresh tokens.

**[inferred] policy, not a vendor eval SKU:** audience-bound tokens **per MCP server**; allowlist URLs in the harness; **no production write APIs** on eval MCP (`sim_refund` ≠ Stripe); SSRF controls on client-metadata fetch; identity from verified `RunContext`, never from model-filled `tenant_id`.

**Tool-level RBAC (least privilege):**

| Tool | Who | Must not |
| --- | --- | --- |
| `sim_*` domain APIs | Eval runner | Touch prod DB |
| `sandbox_exec` | SWE harness | Network to corp Git / secrets |
| `score_schema` / `score_nli` | Sidecar | Receive raw PII (already-redacted text only) |
| `enqueue_human` | Sidecar on disagreement | Auto-promote to golden |
| `dataset.write` / annotation-queue edit | Human + `organization:manage`-class role | Be an LLM tool |
| `release.sign` | Release bot after dual-oracle | Run from the judge prompt |

LangSmith: dataset write = production-data write; evaluator spend limits require `organization:manage`. Braintrust: Starter = owner-only groups; Pro = four built-ins; Enterprise = custom + audit logging. Phoenix: **your** SSO in front of the UI.

**PII pipeline — detect → redact → audit.** Applies **before ingest** (not after promotion). Promotion is a retention-class change. Hiding all I/O makes offline eval impossible — **tokenize/mask PII but keep task structure**; keyed mapping in *your* vault if replay needs the real email. Judge prompts receive **already-redacted** text or you have exported PII to a second model vendor.

1. **Detection (regex + NER/classifier, client-side where the vendor supports it).** LangSmith: SDK `create_anonymizer` (regex / Presidio / Comprehend); `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` (anonymizer is **skipped** when hide-all is on); Gateway PII/secrets redaction does **not** cover traces that bypass the gateway. OpenInference / Phoenix: `OPENINFERENCE_HIDE_INPUTS/OUTPUTS/MESSAGES/TEXT/IMAGES`, `HIDE_LLM_TOOLS`, `HIDE_EMBEDDING_VECTORS`; `TraceConfig` in code beats env vars; hiding inputs also hides tool defs. Braintrust: global masking on inputs/outputs/metadata/context; Topics summarization **reads trace text** — scrub first. Datadog: Sensitive Data Scanner **in Datadog’s backend** (contrast: LangSmith client-side). Dual-gate: regex is cheap/high-precision on PAN/email/phone; NER/classifier catches names in tickets regex misses. If the classifier is down, **fail closed on promotion and on judge egress** (still serve the user) — do not copy raw traces into datasets or to a cloud judge.

2. **Redaction.** Replace with stable tokens (`[EMAIL_<hash12>]`) so task structure (refund amount, tool names, retrieved chunk IDs) survives for offline eval. Do **not** send cardholder data to LangSmith at all (prohibited). Self-host the judge (Phoenix + vLLM, Inspect local, Braintrust hybrid) when gold contains support-ticket PII; SimpleQA’s 4,326 public facts are the wrong analog. Annotation-queue **edits** are a second promotion path — a reviewer pasting a real ticket is a PII incident.

3. **Audit trail (WORM).** Immutable log of detect/redact **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`tokenize`/`strip`/`block-from-judge`/`block-from-dataset`), detector (`regex`|`presidio`|`comprehend`|`ner`), actor. Score audit (research minimum **[inferred]**): `(example_id | trace_id, evaluator_id, evaluator_version, model+params, prompt_hash, score, rationale, timestamp, dataset_version, actor)`. Without `evaluator_version`, you cannot explain a metric jump after a rubric tweak. **Who changed goldens:** LangSmith automatic dataset versions (timestamp) + tags; Braintrust dataset snapshots (Pro+); git-backed Promptfoo/Inspect/DeepEval for **code review of the suite**. Neither SaaS versioning replaces git for the YAML/python that *is* the gate.

PCI: do not promote payment traces into eval datasets on that SaaS. Engine ZDR per analysis task is **not** a reason to skip client-side masking.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional HTTP/Temporal wiring is commented. Run: `python eval_runtime.py`.

Wired: retries + full jitter, circuit breaker (closed → open → half-open) on **judge** and **sandbox**, fallback **deterministic oracle → cheaper judge → human-queue / skip**, dual-oracle gate (hard fail-closed; soft never overrides hard), pass@k (Chen product) + pass^k (Inspect \(\binom{c}{k}/\binom{n}{k}\)), coverage% (unscored ≠ passed), PII detect→redact→audit, idempotent `run_key`, structured logs with correlation IDs. Online path **never** blocks a fake user response.

```python
#!/usr/bin/env python3
"""Eval-plane resilience: dual-oracle, pass@k / pass^k, judge breaker, skip-not-block.

Stdlib only. Swap Fake* ports for vendor HTTP / Temporal / Docker.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

# Optional deps (not required to run this file):
#   import httpx          # LangSmith / Braintrust / judge HTTP
#   from temporalio import activity, workflow  # durable eval jobs


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        record.eval_layer = getattr(record, "eval_layer", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("evals")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"layer":"%(eval_layer)s","msg":"%(message)s"}'
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
    layer: str = "-",
    **fields: object,
) -> None:
    extra = {
        "correlation_id": cid,
        "tenant_id": tenant,
        "eval_layer": layer,
    }
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, timeout, circuit open — safe to retry idempotent score calls."""


class PermanentError(Exception):
    """4xx auth, schema mismatch, unsupported judge model — do not retry."""


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    cid: str,
    tenant: str,
    op: str,
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 1.0,
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
            sleep_s = min(cap_s, base_s * (2**i))
            sleep_s = random.random() * sleep_s  # full jitter
            slog(
                logging.WARNING,
                "retry",
                cid=cid,
                tenant=tenant,
                layer="retry",
                op=op,
                attempt=i + 1,
                sleep_s=round(sleep_s, 4),
                err=str(exc),
            )
            time.sleep(sleep_s)
    assert last is not None
    raise last


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Independent breaker per dependency (judge API, sandbox)."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 5.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if (
                self._state is CircuitState.OPEN
                and (time.monotonic() - self._opened_at) >= self.cooldown_s
            ):
                self._state = CircuitState.HALF_OPEN
            return self._state

    def allow(self) -> bool:
        st = self.state
        if st is CircuitState.CLOSED:
            return True
        if st is CircuitState.HALF_OPEN:
            return True  # single probe; caller serializes
        return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
            elif self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


class GraderStatus(str, Enum):
    SCORED = "scored"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # breaker / spend-cap / sampling


@dataclass
class OracleResult:
    passed: bool | None  # None = unscored
    status: GraderStatus
    detail: str
    score: float | None = None  # soft [0,1] if any


@dataclass
class GateDecision:
    ship: bool
    hard_passed: bool | None
    soft_score: float | None
    reason: str
    coverage_ok: bool


def pass_at_k(n: int, c: int, k: int) -> float:
    """Chen unbiased estimator. NaN if n < k (Inspect)."""
    if n < k or k < 1:
        return float("nan")
    if c < 0 or c > n:
        raise ValueError("c must be in [0, n]")
    if n - c < k:
        return 1.0
    prod = 1.0
    for i in range(n - c + 1, n + 1):
        prod *= 1.0 - k / i
    return 1.0 - prod


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Inspect pass_k: C(c,k)/C(n,k). NaN if n < k; 0 if c < k."""
    if n < k or k < 1:
        return float("nan")
    if c < k:
        return 0.0
    # product form avoids huge factorials: Π_{i=0}^{k-1} (c-i)/(n-i)
    acc = 1.0
    for i in range(k):
        acc *= (c - i) / (n - i)
    return acc


_EMAIL = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PAN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


@dataclass
class PiiDecision:
    redacted: str
    types: dict[str, int]
    action: str
    pre_sha: str
    post_sha: str


def pii_detect_redact_audit(text: str, *, cid: str, tenant: str) -> PiiDecision:
    """Detect → redact → audit. Decisions, not raw spans, go to the log."""
    pre = hashlib.sha256(text.encode()).hexdigest()
    types: dict[str, int] = {}
    out = text
    emails = _EMAIL.findall(out)
    if emails:
        types["EMAIL"] = len(emails)
        for e in emails:
            token = hashlib.sha256(e.encode()).hexdigest()[:12]
            out = out.replace(e, f"[EMAIL_{token}]")
    pans = _PAN.findall(out)
    if pans:
        types["PAN"] = len(pans)
        out = _PAN.sub("[PAN_REDACTED]", out)
    action = "tokenize" if types else "none"
    post = hashlib.sha256(out.encode()).hexdigest()
    slog(
        logging.INFO,
        "pii_audit",
        cid=cid,
        tenant=tenant,
        layer="pii",
        action=action,
        types=types,
        pre_sha=pre[:16],
        post_sha=post[:16],
    )
    return PiiDecision(out, types, action, pre, post)


def run_key(
    suite_version: str,
    dataset_version: str,
    harness_commit: str,
    model_id: str,
    run_id: str,
) -> str:
    raw = "|".join(
        [suite_version, dataset_version, harness_commit, model_id, run_id]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def dual_oracle_gate(
    hard: OracleResult,
    soft: OracleResult,
    *,
    soft_threshold: float,
    min_coverage: float,
    scored: int,
    eligible: int,
) -> GateDecision:
    coverage = scored / eligible if eligible else 0.0
    coverage_ok = coverage >= min_coverage
    if hard.status is not GraderStatus.SCORED or hard.passed is None:
        return GateDecision(
            False, None, soft.score, f"hard_{hard.status.value}", coverage_ok
        )
    if not hard.passed:
        return GateDecision(
            False, False, soft.score, "hard_fail", coverage_ok
        )
    if soft.status is not GraderStatus.SCORED or soft.score is None:
        # Hard passed; soft skipped — CI may still fail on coverage.
        return GateDecision(
            coverage_ok, True, None, f"soft_{soft.status.value}", coverage_ok
        )
    if soft.score < soft_threshold:
        return GateDecision(False, True, soft.score, "soft_fail", coverage_ok)
    return GateDecision(True, True, soft.score, "pass", coverage_ok)


class EvalRuntime:
    def __init__(self) -> None:
        self.judge_breaker = CircuitBreaker("judge")
        self.sandbox_breaker = CircuitBreaker("sandbox")
        self._done_keys: set[str] = set()
        self._lock = threading.Lock()

    def score_task(
        self,
        *,
        cid: str,
        tenant: str,
        key: str,
        db_goal_met: bool,
        output_redacted: str,
        online: bool,
        judge_ok: bool = True,
    ) -> tuple[OracleResult, OracleResult]:
        with self._lock:
            if key in self._done_keys:
                slog(
                    logging.INFO,
                    "idempotent_skip",
                    cid=cid,
                    tenant=tenant,
                    layer="harness",
                    key=key[:16],
                )
                return (
                    OracleResult(None, GraderStatus.SKIPPED, "idempotent"),
                    OracleResult(None, GraderStatus.SKIPPED, "idempotent"),
                )
            self._done_keys.add(key)

        hard = self._hard_oracle(cid, tenant, db_goal_met)
        if not hard.passed:
            return hard, OracleResult(None, GraderStatus.SKIPPED, "hard_failed")
        soft = self._soft_or_skip(
            cid, tenant, output_redacted, online=online, judge_ok=judge_ok
        )
        return hard, soft

    def _hard_oracle(self, cid: str, tenant: str, db_goal_met: bool) -> OracleResult:
        if not self.sandbox_breaker.allow():
            slog(
                logging.ERROR,
                "sandbox_open",
                cid=cid,
                tenant=tenant,
                layer="sandbox",
            )
            return OracleResult(None, GraderStatus.SKIPPED, "sandbox_open")
        try:

            def _run() -> OracleResult:
                # Fake env: a real SWE/τ runner would lease a container/DB here.
                if not self.sandbox_breaker.allow():
                    raise TransientError("sandbox_open")
                return OracleResult(
                    db_goal_met, GraderStatus.SCORED, "db_goal", 1.0 if db_goal_met else 0.0
                )

            result = retry_with_jitter(
                _run, cid=cid, tenant=tenant, op="hard_oracle"
            )
            self.sandbox_breaker.record_success()
            return result  # type: ignore[return-value]
        except (TransientError, PermanentError) as exc:
            self.sandbox_breaker.record_failure()
            return OracleResult(None, GraderStatus.ERROR, str(exc))

    def _soft_or_skip(
        self,
        cid: str,
        tenant: str,
        text: str,
        *,
        online: bool,
        judge_ok: bool,
    ) -> OracleResult:
        # Fallback: cheaper heuristic judge, then human/skip.
        if not self.judge_breaker.allow():
            slog(
                logging.WARNING,
                "judge_open_skip" if online else "judge_open_ci",
                cid=cid,
                tenant=tenant,
                layer="judge",
            )
            if online:
                return OracleResult(None, GraderStatus.SKIPPED, "breaker_open")
            return OracleResult(None, GraderStatus.ERROR, "breaker_open_ci")
        try:

            def _judge() -> OracleResult:
                if not judge_ok:
                    raise TransientError("judge_429")
                # Stand-in for a 2k/200 rubric call. Length is NOT a criterion
                # (HealthBench length correlation is a known bias).
                score = 0.0 if "hostile" in text.lower() else 0.91
                return OracleResult(True, GraderStatus.SCORED, "rubric", score)

            result = retry_with_jitter(_judge, cid=cid, tenant=tenant, op="judge")
            self.judge_breaker.record_success()
            return result  # type: ignore[return-value]
        except TransientError as exc:
            self.judge_breaker.record_failure()
            cheap = 0.5 if len(text) > 0 else 0.0
            slog(
                logging.WARNING,
                "fallback_cheap_judge",
                cid=cid,
                tenant=tenant,
                layer="judge",
                err=str(exc),
            )
            if online:
                return OracleResult(None, GraderStatus.SKIPPED, "skip_online")
            return OracleResult(True, GraderStatus.SCORED, "cheap_fallback", cheap)


def user_request_then_eval_async(
    runtime: EvalRuntime,
    *,
    cid: str,
    tenant: str,
    user_output: str,
    db_goal_met: bool,
) -> str:
    """User path returns immediately. Sidecar scores later (0 ms eval tax)."""
    pii = pii_detect_redact_audit(user_output, cid=cid, tenant=tenant)
    slog(logging.INFO, "user_complete", cid=cid, tenant=tenant, layer="serve")
    # In production: enqueue (cid, pii.redacted, db_goal_met) to Kafka/Temporal.
    runtime.score_task(
        cid=cid,
        tenant=tenant,
        key=run_key("suite-1", "ds-v3", "harness@abc", "sonnet", cid),
        db_goal_met=db_goal_met,
        output_redacted=pii.redacted,
        online=True,
    )
    return user_output  # never waits on the judge


def demo() -> None:
    random.seed(0)
    rt = EvalRuntime()
    cid = str(uuid.uuid4())
    tenant = "acme"
    trials = [True, True, False, True, True]  # 4/5 hard-pass
    c = sum(1 for t in trials if t)
    n = len(trials)
    slog(
        logging.INFO,
        "estimators",
        cid=cid,
        tenant=tenant,
        layer="stats",
        n=n,
        c=c,
        pass_at_1=round(pass_at_k(n, c, 1), 4),
        pass_at_3=round(pass_at_k(n, c, 3), 4),
        pass_hat_1=round(pass_hat_k(n, c, 1), 4),
        pass_hat_3=round(pass_hat_k(n, c, 3), 4),
        pass_at_k_nan_if_short=str(pass_at_k(2, 2, 5)),
    )
    hard, soft = rt.score_task(
        cid=cid,
        tenant=tenant,
        key=run_key("suite-1", "ds-v3", "harness@abc", "sonnet", "run-1"),
        db_goal_met=True,
        output_redacted="Refund issued per policy.",
        online=False,
    )
    gate = dual_oracle_gate(
        hard, soft, soft_threshold=0.85, min_coverage=0.95, scored=1, eligible=1
    )
    slog(
        logging.INFO,
        "gate",
        cid=cid,
        tenant=tenant,
        layer="gate",
        ship=gate.ship,
        reason=gate.reason,
        hard=hard.passed,
        soft=soft.score,
    )
    # Online: skip rather than block.
    user_request_then_eval_async(
        rt,
        cid=str(uuid.uuid4()),
        tenant=tenant,
        user_output="Done, emailed ada@example.com",
        db_goal_met=True,
    )


if __name__ == "__main__":
    demo()
```

**What to recode from memory in an interview:** (1) Chen product vs naive \(1-(1-p)^k\); (2) Inspect NaN when \(n<k\); (3) hard fail ⇒ skip soft, never average; (4) judge breaker open ⇒ skip online / error in CI; (5) PII audit logs hashes and types, not spans; (6) `run_key` includes harness commit so a scaffold change is a new measurement.

Optional Temporal sketch (not executed): workflow id = `run_key(...)`; activity per task with sandbox lease; on `TransientError` retry with jitter; on poison (deadline exceeded) DLQ with `grader_status=timeout`; compensation writes `SKIPPED`, never `passed=true`.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Dual-oracle release gate for a policy-bound support agent

**Problem.** A τ-style support agent (refunds, bookings, plan changes) must not ship a prompt/model that is “nicer” but books the wrong fare class or refunds above cap. Users get **one** try. The team today quotes a single-run pass@1 on a 50-item golden set and an LLM judge on the utterance (“looks booked”). Dual-control telecom-class work exists in the product (user must toggle a setting). Requirements: fail-closed CI, fail-open online, PII-safe promotion, no judge on user p99.

**Proposed architecture:**

```
  ┌─────────────┐   ┌─────────────────────────────────────────────────┐
  │ IdP / PEP   │──▶│ CONTROL: pin as_of + harness_commit + k=5       │
  │ JWT→tenant  │   │   hard: τ² sim + DB goal + policy assertions    │
  │             │   │   tool unit: BFCL-style AST / Promptfoo is-json │
  │             │   │   CI: pass^5 + coverage of pinned set           │
  │             │   │   stats: paired vs last ship; bootstrap if n<200│
  └─────────────┘   └──────────────────┬──────────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────────┐
                    │ DATA: prod agent (same scaffold as CI)           │
                    │   sim_* MCP only in eval; prod tools in serve    │
                    │   freeze user-sim model+prompt                   │
                    │ SIDECAR: sample 0.1 after 30 s idle              │
                    │   HealthBench-shaped rubric (tone/completeness)  │
                    │   spend cap; coverage% on the board              │
                    └──────────────────┬───────────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────────┐
                    │ Promote: redacted failing traces → annotation    │
                    │ queue (runs, not threads) → tagged dataset       │
                    │ Human on hard-vs-soft disagreement               │
                    └──────────────────────────────────────────────────┘
```

**Technology choices:** Hard oracle = final DB state (τ) + policy caps, **not** the NL claim. Freeze the user-sim (τ pass^k collapses when the sim upgrades). `k=5` trials; gate on **pass^5** (or pass^3 if budget-constrained). Tool unit on every commit (BFCL / DeepEval `ToolCorrectnessMetric` / Promptfoo `trajectory:tool-sequence` + irrelevance). Online: Braintrust/LangSmith sampling 0.1, rubric **not** 1–5 vibe. CI exit: Promptfoo failures / LangSmith ≥0.85 example / Braintrust `Reporter.reportRun`. Miller: a 50-item set **cannot** support a 3 pp claim (n≈969). τ²: GPT-4.1 pass^1 retail **74%** → telecom **34%** — a retail-only golden set is the wrong construct if the product is dual-control.

**Trade-off matrix:**

| Axis | **A1 Dual-oracle: DB/policy hard + async rubric (recommended)** | **A2 Hard-only (DB match)** | **A3 Soft-only (LLM judge on the utterance)** |
| --- | --- | --- | --- |
| **Cost** | Agent × k + sampled judge; nightly 200-task envelope **[inferred] ~$12** uncached / **~$48** agent at pass^5 | Agent × k; no judge line | Agent + judge × sample; cheapest *and* wrong |
| **Latency** | User p50/p95/p99 eval tax **0 ms**; sidecar time-to-score **[inferred] 31,200 / 34,000 / 42,000 ms** with 30 s idle | CI only; 0 ms user | 0 ms **if** async; **+12,000 ms p99** if someone “just adds a judge call” |
| **Ops complexity** | Freeze sim; two dashboards (hard vs soft); coverage% | Env reset discipline only | Judge calibration + position swap |
| **Security posture** | PII redact before promote/judge; sim MCP not Stripe; safety not averaged | Blind to tone/PII-in-utterance | Judge is a subprocessor on every sampled trace |
| **Scalability ceiling** | `sampling_rate` + spend cap; n≈969 to claim 3 pp | Docker/DB snapshots × k | Judge TPM; spend cap with **no backfill** |

**Decision.** **A1 wins.** Hard-only ships “correct but hostile” and misses PII-in-logs. Soft-only ships “pretty wrong” (ARE: whole-trace judge precision **0.53**). Dual-oracle costs more (pass^k is a budget line) and that is the point: reliability eval is not a unit test. If the product includes user-only steps, the golden set must be τ² dual-control, not retail.

#### Scenario B — RAG faithfulness CI + citation-ID constraint

**Problem.** A retrieval-grounded assistant must not hallucinate against retrieved context, and citations must be real IDs from the retrieved set. WikiEval’s **~95%** RAGAS faithfulness agreement is being proposed as the production SLO. A second team wants DeepEval `FaithfulnessMetric` defaults on the same board. Fine-tunes will land later and must not drop this suite (intersection with module 02: adapter promote-gate).

**Proposed architecture:**

```
  ┌──────────────┐    ┌─────────────────────────────────────────────┐
  │ Pinned       │───▶│ CONTROL: dataset as_of + chunker/embed pin  │
  │ RAG gold     │    │   retriever: context recall vs reference    │
  │ (offline)    │    │              ID-based context precision     │
  └──────────────┘    │   generator hard: RAGAS Faithfulness NLI    │
                      │   citation: IDs ⊆ retrieved set (constrained│
                      │             decode / tool-only cite)        │
                      │   CI: DeepEval assert_test / Promptfoo      │
                      │       fail merge on paired drop vs baseline │
                      └──────────────────┬──────────────────────────┘
                                         ▼
                      ┌──────────────────────────────────────────────┐
                      │ ONLINE 1–10%: Phoenix rails                  │
                      │   {grounded, hallucinated} under             │
                      │   suppress_tracing; never block the user     │
                      │ Construct ≠ world-fact (not SimpleQA)        │
                      └──────────────────────────────────────────────┘
```

**Technology choices:** Faithfulness = RAGAS claim-level **entailment** vs retrieved context, **or** DeepEval with `penalize_ambiguous_claims=True` — pick one construct and name it on the dashboard. Threshold from a **human-labeled** calibration set, not WikiEval 0.95 transplanted. Citation check is **deterministic** (ID ∈ retrieved); RAGAS will not catch a bare invented `[doc 17]`. Context recall is the complementary hard signal (faithfulness can be 1.0 on the **wrong** documents). Answer relevancy (78% WikiEval) is **soft** — do not ship-gate on it. Online: reference-free only (this request’s retrieve). A new adapter must pass this suite **and** a frozen general holdout; hosted OpenAI Evals cannot be that gate after **2026-11-30**.

**Trade-off matrix:**

| Axis | **B1 RAGAS entailment + ID citation + context recall (recommended)** | **B2 DeepEval default Faithfulness (idk counts as yes)** | **B3 Answer-relevancy cosine / G-Eval vibe as the gate** |
| --- | --- | --- | --- |
| **Cost** | NLI calls per claim; ID check is free; nightly envelope like §3.1 | Similar LLM cost; **false 1.0** on unsupported answers | Cheap; G-Eval flake (paper used 20 samples + token weights) |
| **Latency** | CI wall-time; online sample **0 ms** user (Phoenix sidecar) | Same if async | Same if async |
| **Ops complexity** | Two metrics + citation unit; pin retriever version | One metric, wrong default | One number, contested construct |
| **Security posture** | Judge sees **redacted** claims+chunks; no gold world-facts required online | Same egress | Same egress; verbosity bias (HealthBench length correlation) |
| **Scalability ceiling** | Claim fan-out (HealthBench-like 11× is a different product); sample online | Silent quality lie scales perfectly | Cannot catch retrieval miss or fake IDs |

**Decision.** **B1 wins.** B2 is a known footgun (empty/`idk` → 1.0). B3’s 78% / 0.514 correlations are not ship gates; G-Eval is biased to LLM-generated text. Faithfulness without context recall ships fluent lies about the wrong docs. Citations are a **schema/constraint** problem, not an LLM-judge problem.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **“The model scored 91%”** | Collapsed \(M\times H\times T\times E\times O\times n\times r\times I\) | Cannot name scaffold/image/RAM/grader version | Pin the product tuple; <3 pp without it is not a result |
| **pass@1 quoted as SLO** | Demo’d best-of-N / hidden retries | pass^k gap (up to 24.9 pp); T=0 SD >1.5 pp | Report both; cap retries in the **target**; Inspect epochs |
| **Sync judge on user p99** | Second LLM call in the handler | User p99 ≈ judge p99 (**+12,000 ms [inferred]**); judge 429 → user 500 | Async sidecar; 0 ms eval tax |
| **Gold-NLI on prod traces** | Offline construct online | Metric undefined / empty gold | Reference-free online; gold only in CI |
| **Composite hides safety** | Average faithfulness+tone+PII | Slice 0.70 safety with “quality 0.93” | Dual-oracle; safety not in the mean |
| **Grader crash = agent fail** | OpenAI python exception → 0 | Infra errors in pass@1 | `grader_status`; Inspect NaN when \(n<k\) |
| **DeepEval faithfulness 1.0** | `"idk"` / empty verdicts pass | Calibration vs RAGAS entailment | `penalize_ambiguous_claims=True` or RAGAS |
| **Reused SWE `run_id`** | Cache key ignores patch | Zero new measurement | New `run_id`; don’t VCR agent calls |
| **Trial-2 inherits booking** | No env reset | Inflated pass^k | Snapshot per trial; separate cache keys |
| **Spend cap paints green** | Skipped runs not backfilled | Coverage% missing on dashboard | Coverage NFR; `score IS NOT NULL` alerts |
| **Promoted PII immortal** | Add to Dataset before redact | GDPR erasure vs 14d TTL | Detect→redact→audit **before** ingest |
| **Public Verified as hiring bar** | Contamination / gold regurgitation | OpenAI stopped reporting | Private holdout; rolling post-cutoff tickets |
| **3 pp claim on 50 items** | CLT on tiny n | Miller n≈969 for 3 pp MDE | Bootstrap; power analysis; paired SE |
| **Lucky pass ships** | Outcome-only gate | AgentLens 10.7% lucky | Process overlay; forbidden-action counts |
| **Hosted Evals CI after cutoff** | Deprecated control plane | Hard outage 2026-11-30 | Migrate to Promptfoo/Inspect before read-only 2026-10-31 |

---

## Key Takeaways

- Eval is a **measurement system** (harness, env, tools, judge, retries, infra), not a leaderboard screenshot. The harness is in the SUT.
- **Three planes, two clocks, two oracles.** Judge sidecar off the user path (eval tax **0 ms**). Hard gate + soft rubric; never average safety into “quality.”
- **pass@k ≠ pass^k.** Chen needs a verifier; τ pass^k is reliability. Users live on pass^1; the gap (up to **24.9 pp**) is product risk. Honest \(k\) = Inspect epochs, not silent SDK retries.
- **Unit = task.** n=50 SWE instances is not n=1,000 steps. 50 gold items cannot support a 3 pp claim (**n≈969**). Coverage% is an NFR; unscored ≠ passed.
- **Do not LLM-judge a schema.** BFCL/AST/DB/tests first. ARE write-oracle 0.98 vs whole-trace 0.72. RAGAS entailment ≠ DeepEval contradiction-only ≠ world-fact (SimpleQA).
- Eval is a **second product**: 1k judge calls **[inferred] $9** Sonnet 2k/200; LangSmith extended **$5/1k**; pass^k multiplies agent cost. Human $ is your loaded cost (no SKU in this research).
- Promotion is a **legal record**. PII: detect → redact → audit **before** ingest. Judge = subprocessor. Pin `as_of`. Hosted OpenAI Evals dies **2026-11-30**.

---

## Interview Q&A

**Q1. What is an eval system, in one minute?**  
I treat eval as a measurement system, not a screenshot. Three planes: a control-plane harness that runs a pinned dataset into an immutable experiment, a data-plane tracer on the user SLO clock, and an async judge sidecar that must not sit on user p99. Dual-oracle: a hard correctness/safety bit plus a soft rubric. I refuse to quote “the model scored 91%” without scaffold, tools, env image, retries, and grader version — Anthropic showed infra alone is 6 pp on Terminal-Bench.

**Q2. pass@k vs pass^k — which is the SLO?**  
pass@k (Chen) is the probability at least one of k samples works, and only if I have a verifier to pick it — HumanEval unit tests, not an LLM judge. pass^k (τ-bench) is the probability all k trials work; that is reliability. Original retail pass^8 was under 25%. Anthropic’s think-tool moved τ-airline pass^1 from 0.332 to 0.584 but pass^5 only from 0.100 to 0.340. Users get one try, so I gate on pass^k and I still report pass@k as a capability envelope. Mixing them is how a demo becomes an SLO.

**Q3. Why not put GPT-4-as-judge on every request?**  
Because that is a latency tax and a subprocessor egress, not an eval system. Braintrust/LangSmith/Datadog all score after the root span. My user-path eval tax is 0 ms. Sidecar time-to-score with a 30 s idle plus a 2k/200 judge is about 31.2 s p50 inferred — late scores, not a slow chat. Zheng still has position, verbosity, and self-enhancement bias; few-shot raised GPT-4 swap consistency 65→77.5% at 4× cost without lifting human agreement. If a JSON schema or DB state exists, I use that.

**Q4. Give me the cost model for 1,000 eval tasks.**  
I state the mix. Judge-only 2k in / 200 out at Sonnet $3/$15 is $9/1k inferred. LangSmith extended experiments are $5/1k platform. Braintrust Pro on-demand scores are $1.50/1k platform. A τ-like 8k/1.5k agent with 70% cache read on a 6k prefix is about $26/1k agent inferred; add judge and extended traces and I am near $40. pass^4 multiplies agent+sim by about four. Human annotation has no SKU in this research — I will not invent one. HealthBench is 55k grader calls per model; that judge line is not rounding error.

**Q5. What p99 do you put in the contract?**  
I do not quote a vendor online-judge p99 — nobody publishes mine. I contract 0 ms eval tax on the user path, and I SLO the sidecar separately: inferred 20/80/250 ms for a local deterministic oracle, 1,200/4,000/12,000 ms for judge compute, 31,200/34,000/42,000 ms time-to-score including Braintrust’s 30 s idle. p99 of a 50-example experiment is noise. If someone inlines the judge, they have bought +12 s on user p99 inferred and judge 429s as user 500s.

**Q6. Dual-oracle for a refund agent — walk the gate.**  
CI fail-closed: τ-style simulator, frozen user-sim, DB goal-state plus policy caps, k=5, gate on pass^5, infra errors in a separate bucket. Every commit: schema/AST tool unit including abstention. Soft rubric (tone, completeness) is async sampled and never overrides a hard fail. Online fail-open with coverage%. I will not average PII/safety into a 0.93 quality score. A 50-item set cannot detect a 3 pp move — Miller’s worked example is n≈969.

**Q7. RAG faithfulness looked like 1.0 and users still complained.**  
Two footguns. RAGAS faithfulness is entailment vs **retrieved context**, not world truth — you can be perfectly faithful to the wrong docs, so I pair it with context recall and I do not ship-gate on answer relevancy (78% WikiEval). DeepEval’s default Faithfulness treats `"idk"` as yes and empty verdicts as 1.0 unless `penalize_ambiguous_claims`. Citations are a third construct: I constrain IDs to the retrieved set because RAGAS will not catch a bare invented `[doc 17]`. I calibrate the threshold on our humans, not WikiEval’s 0.95.

**Q8. Our SWE eval did not move after a patch change. What happened?**  
SWE-bench caches on `(run_id, instance_id)`, not the patch hash. Reusing `run_id` is a silent no-op. I mint a new `run_id`, keep `--cache_level=env`, and I do not commit LangSmith VCR cassettes for agent calls if I intend to re-measure. I also bucket harness crashes separately from unresolved. If we were quoting public Verified, I would refuse — OpenAI stopped reporting it because models regurgitate gold patches; Pro then climbed 23.3→80.3% in eight months and ~30% of tests were broken.

**Q9. Temperature 0, still ±2 pp. Are we sloppy?**  
The Randomness paper: SD still >1.5 pp at T=0; single-run pass@1 ranges 2.2–6.0 pp; trajectories diverge in the first ~1% of tokens. That is aleatoric plus engine/env nondeterminism. I estimate pass@1 from multiple independent runs, report pass@k and pass^k, and I treat a 31→33% single-run “win” as noise. Silent agent retries on top of that are an undeclared pass@k and a double-refund risk in prod.

**Q10. Trace → golden set. Where do people get sued?**  
Promotion is a retention-class change: a 14-day debug email becomes immortal. I run detect→redact→audit **before ingest** — LangSmith anonymizer / hide flags, OpenInference HIDE_*, Braintrust mask. Hide-all makes offline eval impossible, so I tokenize and keep structure. The judge sees already-redacted text or I have a second subprocessor. Annotation-queue edits are the same RBAC as dataset write. PCI cardholder data does not go to LangSmith at all. Who changed goldens: dataset versions + git of the suite; I log evaluator_version on every score.

**Q11. How do you keep the judge from becoming the reward?**  
Code assertions first (Hamel: ≥100 traces, taxonomy, stop when 20 add no category). Itemized weighted criteria, not a 1–5 vibe. Position swap and treat flips as ties. Different model family only if calibration improves. HealthBench kept GPT-4.1 over o3 as grader because meta-eval F1 was higher and cheaper — reasoning models are not automatically better judges. I never use a judge where math/code/JSON can be checked. If that judge is also the RL reward, the policy will farm length and sycophancy.

**Q12. Zero-Trust MCP for the eval harness — failure mode?**  
Gaia2/ARE attach MCP; untrusted MCP is RCE-adjacent. The failure mode is an eval bot with the user’s refresh token calling corp Git or live Stripe. I bind audience-restricted tokens per MCP server, allowlist URLs, expose only sim_* and ephemeral Docker, take identity from RunContext, and I never put `dataset.write` on a tool the model can call. Online, a tripped judge breaker skips the score; it does not fail the user’s refund.

---

## Key Numbers to Memorize

### Estimators / statistics
| Number | What |
| --- | --- |
| **Chen product; naive \(1-(1-p)^k\) biased** | pass@k; NaN if \(n<k\) (Inspect) |
| **\(\binom{c}{k}/\binom{n}{k}\)** | Inspect pass^k (without replacement) |
| **164 / n=200 / 7.7 tests** | HumanEval original |
| **80× / 19.3–28.9% drop** | HumanEval+ tests / pass@k drop |
| **<50% / pass^8 <25% / 35.2%** | τ GPT-4o-class; retail pass^8; airline pass@1 |
| **0.332→0.584 / 0.100→0.340** | τ-airline think+prompt pass^1 / pass^5 |
| **0.812 / 0.626** | τ-retail Think pass^1 / pass^5 |
| **2.2–6.0 pp / >1.5 pp @ T=0 / 24.9 pp** | Single-run range; T=0 SD; pass@k vs pass^k envelope |
| **n≈969 / 13.2%→7.5% MDE** | Miller 3 pp example; K=1→10 on n=198 |
| **unit = task; n< few hundred → no CLT** | Miller; 2025 position paper |
| **25,000** | LangSmith max runs / trace |

### Oracles / benches / judges
| Number | What |
| --- | --- |
| **6 pp / +1.54 pp / <3 pp skepticism** | Terminal-Bench infra; SWE RAM; Anthropic rule |
| **+64% relative; 12.47% (286/2,294)** | SWE-agent ACI vs shell, same GPT-4 Turbo |
| **10.7% (0.5–23.2%)** | AgentLens lucky passes |
| **0.98 vs 0.72 / 0.99 vs 0.53** | ARE Verifier vs whole-trace judge agreement/precision |
| **>80% / 65→77.5% @ 4×** | Zheng GPT-4 vs humans; few-shot consistency, no human-agreement lift |
| **~95% / 78% / 70%** | RAGAS WikiEval faithfulness / answer / context relevance |
| **0.514** | G-Eval GPT-4 Spearman avg (SummEval) |
| **0.709 / 55–75% / median 11 / 48,562 / 5,000** | HealthBench grader F1; MD–MD; criteria; unique; conversations |
| **~60% / 32% / 16%** | HealthBench o3 / GPT-4o / GPT-3.5 Turbo |
| **+10% / +25%** | GPT-4 / Claude-v1 self-enhancement vs humans |
| **2,294 / Lite 300 / Verified 500** | SWE-bench family |
| **23.3%→80.3% / ~30% broken** | SWE-Pro public split then audit; retract |
| **92% vs 15%** | GAIA human vs GPT-4+plugins |
| **800 × 10 × 101 / 160 mini** | Gaia2 scenarios / universes / tools |
| **74% / 56% / 34%** | τ² GPT-4.1 retail / airline / telecom pass^1 |
| **40/30/10/10/10** | BFCL V4 bucket weights |
| **4,326 / 2/300** | SimpleQA items; informal grader disagreements |
| **8 pp** | GSM1k vs GSM8k contamination gap |
| **Einstein example 0.5** | RAGAS faithfulness walkthrough |

### $ / platforms / dates
| Number | What |
| --- | --- |
| **0.05¢ / 0.50¢; 14d / 400d** | LangSmith base / extended trace |
| **[inferred] $5 / $0.50 / $1.50 / $0.35** | LS extended / LS base / BT Pro scores / DD 1k LLM spans |
| **$3/$15; [inferred] $9 / ~$1.1** | Sonnet in/out per MTok; 1k judge 2k/200; cached prefix input |
| **[inferred] ≈$26 / ≈$40 / ~$12 / ~$48** | τ-like 1k agent; +LS+judge; nightly 200; pass^5 agent |
| **[inferred] $90 + $50** | 1M req/mo × 1% sample judge + LS upgrade |
| **$39 / $249 / $160** | LS Plus seat; BT Pro; DD 100k LLM spans |
| **55,000** | HealthBench grader calls / model |
| **$11.36 vs $180.49** | HAL τ-airline o4-mini High vs Opus 4.1 (snapshot) |
| **2026-03-09 / 2026-10-31 / 2026-11-30** | Promptfoo acquire agreement; Evals read-only; Evals shutdown |
| **Monday 00:00 UTC** | LangSmith evaluator spend-cap reset; no backfill |

### Latency / throughput / security
| Number | What |
| --- | --- |
| **0 / 0 / 0 ms** | **[inferred policy]** user-path eval tax if async |
| **20 / 80 / 250 ms** | **[inferred]** deterministic-oracle sidecar p50/p95/p99 |
| **1,200 / 4,000 / 12,000 ms** | **[inferred]** LLM-judge compute p50/p95/p99 (2k/200) |
| **31,200 / 34,000 / 42,000 ms** | **[inferred]** time-to-score with 30 s idle + judge |
| **30,000 / 240,000 / 300,000 ms** | Braintrust idle default / inline scorer / Gaia2 scenario timeout |
| **10** | Phoenix `llm_classify` default max_retries |
| **50k/500 MB; 500k/5 GB per UTC hour** | LS Developer (no card) / Plus ingest |
| **10,000 / 10 s; 20 MB** | Braintrust function executions; per-span payload |
| **[inferred] ~50k tasks/hour** | Plus event headroom at 10 spans/task |
| **detect → redact → audit before ingest** | PII; hide-all ⇒ cannot eval offline |
| **no PCI on LangSmith** | Shared-responsibility; judge = subprocessor |

---

*End of module. Practice the Q&A out loud; recode Chen pass@k, pass^k, dual-oracle, and judge-breaker skip-not-block from memory; recompute the $9 judge loop and the $26 τ-like agent mix on a whiteboard with the assumptions listed.*
