# Research: Evaluation
**Date researched**: 2026-08-21
**Sources consulted**: 68

Scope: **task success** (pass@k / pass^k, SWE-bench family, GAIA / Gaia2, τ-bench family, binary vs partial credit), **trajectory** (step traces, process vs outcome, LangSmith / Braintrust / Phoenix, offline replay), **tool accuracy** (BFCL AST, parameter correctness, tool-selection F1, hallucinated tools), **quality** (LLM-as-judge, pairwise, rubric, human raters, faithfulness), **cost** (tokens/task, $/task, cache, thinking/reasoning tokens), **latency** (TTFT, e2e, p50/p95/p99, SLO vs eval harness). Prices and benchmark numbers are from named vendor docs or papers as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 SLOs are invented. `$ per 1k eval runs` figures are **[inferred]** from published SKUs × a stated reference loop — not a vendor “per eval” SKU.

Invariant: **the harness is not the product**. Task success is a *property of (model × scaffold × tools × oracle × sampling)*. Collapsing that product into “the model scored 91%” is the dominant interview failure. Outcome oracles (hidden tests, DB state, exact-match) and process oracles (judge-on-trace, tool F1) answer different questions; mixing them in one number is how teams Goodhart themselves.

---

## 1. System Topology & Mechanics

### 1.1 Two planes, two clocks, two oracles

| Plane | What it is | Clock | Typical store | Oracle |
| --- | --- | --- | --- | --- |
| **Eval harness (control)** | Batch runner: dataset → target fn → scorers → experiment | Wall-clock of the job; retries; `num_repetitions` | Versioned dataset + immutable experiment | Reference outputs, hidden tests, DB goal-state, rubric |
| **Production tracing (data)** | Every live request as a nested span tree | User SLO clock (TTFT / e2e) | Trace store (14d vs 400d, or self-hosted OTLP) | Reference-free: safety, format, sampled LLM-as-judge |
| **Judge / scorer (sidecar)** | Code, LLM-as-judge, human queue | Async; **must not** sit on the user critical path | Feedback attached to run/span | Score + comment; audit log of who/what scored |

LangSmith’s own split is the cleanest published topology (docs, 2026): **offline** runs on *datasets/examples* (inputs + optional reference outputs) and produces an *experiment*; **online** runs on *runs/threads* from a tracing project (inputs/outputs only). Evaluators are workspace-level resources attachable to many projects/datasets; sampling rate, filters, and weekly spend caps are **per attachment**, not per evaluator definition. Braintrust’s loop is isomorphic: playground → immutable experiment → CI (`bt eval` / GitHub Action) → online scoring automations on logs → promote traces into datasets. Phoenix (Arize) is the open-source OTel twin: traces arrive over OTLP with **OpenInference** span kinds (`LLM`, `AGENT`, `TOOL`, `RETRIEVER`, `EVALUATOR`, …); evals are scored back onto spans as annotations.

**Control vs data plane (enterprise).** Braintrust’s hybrid post is explicit: UI/auth/metadata stay in the vendor control plane; experiment logs, traces, datasets, prompts, and completions stay in the customer data plane (VPC / on-prem). LangSmith Enterprise adds Cloud / Hybrid / Self-Hosted: Hybrid is SaaS control plane + self-hosted data plane. Phoenix self-hosts the whole stack. Interview move: **eval datasets are as sensitive as production logs** because they *are* production logs that someone promoted. If the data plane holds PII, the judge model is a **subprocessor** on every online-eval call.

**Online scoring must be off the SLO path.** Braintrust docs: online scoring “runs asynchronously in the background without adding latency”; score spans are recorded after the root span; the UI hides scorer duration from the execution timeline unless you toggle “Include score spans.” LangSmith online evaluators similarly attach to ingested traces, not to the serving path. **[inferred]** If your “eval” is a synchronous second LLM call in the request handler, you have built a latency tax, not an eval system.

### 1.2 Task success: pass@k, pass^k, binary oracles, partial credit

**pass@k (Chen et al., Codex / HumanEval, arXiv:2107.03374).** Functional correctness, not BLEU. Generate \(n \ge k\) samples per task, count \(c\) that pass unit tests, report the unbiased estimator \(\mathbb{E}[1 - \binom{n-c}{k}/\binom{n}{k}]\). Naive \(1-(1-\hat p)^k\) is **biased**. Original paper: \(n=200\), \(k \le 100\), 164 hand-written Python problems, mean 7.7 tests/problem. pass@1 ≈ “one sample works”; pass@k ≈ “something in a candidate set works *if you can filter*.” Agents inherit this: best-of-N with a unit-test oracle is pass@k; best-of-N with an LLM judge is **not** the same estimator.

**pass^k (Yao et al., τ-bench, arXiv:2406.12045).** Probability that **all** \(k\) independent trials succeed, averaged over tasks. This is the reliability metric. Anthropic’s “think tool” post reprints it as the primary τ-bench metric and contrasts it with pass@k: pass@k is optimistic (at-least-one), pass^k is pessimistic (every-time). Original τ-bench: even GPT-4o-class function-calling agents succeed on **<50%** of tasks; retail **pass^8 < 25%**; airline pass@1 **35.2%** for GPT-4o. Anthropic (think-tool, 2025) on τ-airline with Claude 3.7-era configs: baseline pass^1 **0.332** → “Think”+prompt **0.584**; pass^5 **0.100** → **0.340**. Retail “Think” (no extra prompt): pass^1 **0.812**, pass^5 **0.626**. The gap between pass^1 and pass^5 *is* the product risk.

**On Randomness in Agentic Evals (arXiv:2602.07150).** 60,000 trajectories, 25.58B tokens, 1.88M tool calls, three models × two scaffolds × two temperatures. Trajectories diverge in the first few percent of tokens; gaps up to **24.9 percentage points** between pass@k (best-case envelope) and pass^k (worst-case). A 31%→33% single-run “win” is often sampling noise. Vestige (ASSERT-KTH) is the companion trajectory analyzer: pass@k / pass^k, temperature-0 non-determinism, nano-agent / r2e-gym / Claude Code formats.

**SWE-bench (Jimenez et al., ICLR 2024, arXiv:2310.06770).** 2,294 GitHub issues from 12 Python repos. Oracle is **execution**, not patch similarity: apply `model_patch` in Docker at `base_commit`; **FAIL_TO_PASS** tests must all pass; **PASS_TO_PASS** must not regress. Gold `test_patch` is hidden. Docker harness since 2024-06-27. Splits: Lite 300; **Verified** 500 (OpenAI+authors, 2024-08, engineer-confirmed solvable); Multimodal; Multilingual 300 / 9 languages / 42 repos. Harness caches by `(run_id, instance_id)` — same `run_id` + different diff **will not re-run**.

**SWE-bench Verified is no longer a frontier metric.** OpenAI (2026): all tested frontier models could reproduce the **human gold patch** verbatim; scores track training exposure, not SWE skill. They stopped reporting it. Independent work (arXiv:2512.10218): Claude models localize files **3×** better on Verified than on BeetleBox / SWE-rebench given issue text only — contamination signal. OpenAI then pointed at **SWE-bench Pro** (Scale; arXiv:2509.16941): 1,865 problems, 41 repos, public 731 / held-out 12 repos / commercial 18; mean gold patch **107.4 LOC across 4.1 files**. Public-split frontier pass@1 moved **23.3% → 80.3% in eight months**. July 2026 audit (“Separating signal from noise”): automated pipeline flagged **200/731 (27.4%)** broken; human campaign **249 (34.1%)**; OpenAI estimates **~30%** broken (over-strict tests, underspecified prompts, low coverage) and **retracts** the Pro recommendation. Interview takeaway: quote **named split + named scaffold + date + contamination status**. Do not treat aggregator “96% Verified” pages as an SLO.

**GAIA (Mialon et al., arXiv:2311.12983).** 466 questions; 166 with answers (dev); 300 answers held for the leaderboard. Quasi-exact match after type-tied normalization (string / number / list). Human **92%** vs GPT-4+plugins **15%**. Levels by step/tool count. By 2025 HF/Meta judged L1/L2 near-saturated for frontier agents — hence Gaia2.

**Gaia2 + ARE (Froger et al., arXiv:2509.17158; HF blog 2025-09-22).** Read-and-write, **asynchronous** environment (time flows while the agent thinks — unlike paused τ-bench / SWE-bench worlds). 800 unique human-annotated scenarios × 10 universes × **101 tools**; Gaia2-mini **160**; Agent2Agent + Noise augmentations add 320 → **1,120** total. Seven equally weighted capabilities: execution, search, adaptability, time, ambiguity, agent2agent, noise. Judge mix: **Llama 3.3 Instruct 70B** (soft args) + exact-match (hard args); write-actions compared to oracle write-actions. Uniform ReAct, T=0.5, 16k gen cap. Blog: as of Sep 2025, **GPT-5 high-reasoning** led; **Kimi K2** best open. Time split hardest. Paper plots **score vs $ and vs time**; budget-scaling curves **plateau**. Validation split is the public leaderboard; **test set is private** (Meta/HF). ARE records structured traces (tool calls, thoughts, latency) exportable as JSON; MCP can be attached — “json agents can’t mess up your machine unless you connect unsafe MCP.”

**τ-bench family (Sierra).** τ (Jun 2024): user-LLM × agent × domain APIs × policy; success = **final DB state == annotated goal** (no LLM judge on the pass/fail bit). τ² (Jun 2025, arXiv:2506.07982): **dual control** — user also has tools; agent must coach user-only steps. τ³ (2026): banking knowledge (~700 docs), **voice full-duplex**, 75+ task fixes; original GitHub repo marked outdated. Anthropic Opus 4.6 system card (table vs Opus 4.5 / Sonnet / Gemini / GPT-5.2): **τ² Retail 91.9%**, **Telecom 99.3%**. ⚠️ Aggregator sites (taubench.com, Steel) mix user-simulators and judges — do not compare pass^1 across rows without the harness footnote.

**Success rate vs partial credit.** Binary oracles (SWE-bench resolved, τ DB match, GAIA exact match) refuse partial credit by design — a 90%-right patch that fails one FAIL_TO_PASS is a **0**. Partial credit belongs in **rubrics** (HealthBench: weighted criteria, score = points met / max) and **process metrics** (tool F1, faithfulness fraction, step-level PRM). Using rubric partial credit as a *ship gate* without a binary safety/correctness gate is how teams ship “pretty wrong.” Using only binary gates on open-ended chat is how teams ship “correct but hostile.” Dual-oracle is the enterprise default: **hard gate + soft score**.

### 1.3 Trajectory: process vs outcome, traces, offline eval

**Outcome eval** asks “did the world end in the goal state?” **Process eval** asks “were the steps legal, efficient, and policy-faithful?” τ-bench and SWE-bench are outcome-first. Gaia2 scores **write actions and their arguments** against an oracle trace — process-shaped outcome. BFCL multi-turn is state-transition. LLM-as-judge on the full trace is process. Appen / Poolside (2026): publish **full trajectories**, not just aggregates; TRACE-style taxonomies test whether judges detect reward exploits. Chain-of-thought as evidence is weak: models omit the hint they actually used.

**Trace data model (OpenInference).** Required `openinference.span.kind`. Flattened attributes: `llm.model_name`, `llm.token_count.prompt`, `tool.name`, `retrieval.documents`, `input.value` / `output.value`. Evaluator spans are first-class. This is the portable **data plane** if you do not want LangSmith-proprietary run trees as the source of truth. LangSmith traces still map: one *trace* = one application execution; child *runs* = LLM/tool/retriever; *threads* = multi-turn. Max **25,000 runs per trace** (hard reject after that).

**Offline eval loop (all three vendors, same DAG):**

1. Curate dataset (manual 10–20, then production failures, then synthetic).
2. Pin a **dataset version** (LangSmith auto-versions; tag for CI).
3. Run target with `num_repetitions`, `max_concurrency`, optional disk cache (`LANGSMITH_TEST_CACHE` / VCR).
4. Score: code | LLM-as-judge | pairwise | human queue.
5. Compare experiments; promote failing traces back to the dataset.

Braintrust scorers have **span vs trace vs group** scope. Online automation `sampling_rate` (API example: `1` = 100%). LangSmith online: filters + sampling to control cost. Phoenix: pull spans to pandas → `run_evals` / `llm_classify` → write labels back.

**Process metrics that belong on the trace, not the leaderboard:** step count, unique tools, retry rate, policy-violation spans, tokens-to-success, wall-clock-to-success, cache hit rate. Gaia2’s paper is explicit: a correct answer after thousands of tokens / hours is **dominated** on the cost-normalized Pareto.

### 1.4 Tool accuracy: BFCL, parameters, selection F1, hallucinations

**BFCL (Patil et al., ICML 2025; gorilla.cs.berkeley.edu).** AST matching + state-transition — **not** an LLM judge on the classic tracks. That is why numbers are deterministic. V1: simple / parallel / multiple, expert-curated. V2: live enterprise/OSS functions. V3: multi-turn / multi-step, missing params, long context. **V4 (2025, last leaderboard note 2026-04-12):** holistic agentic. Overall = **Agentic 40% + Multi-Turn 30% + Live 10% + Non-Live 10% + Hallucination 10%**. Reproduce with `bfcl-eval==2025.12.17` (commit f7cf735). Subcategories: unweighted average inside a bucket (a model cannot farm a huge bucket); Live is **weighted** by case count.

**Hallucinated tools = irrelevance track.** V4 Hallucination Measurement: Non-Live Irrelevance **240** + Live Irrelevance **882** (unweighted avg, 10% of overall). Relevance (18 live) is listed but format-sensitivity (26 configs × 200 = 5,200) is **non-scoring**. Abstention is a first-class skill: calling a tool that was never on the menu is a fail, not partial credit.

**Parameter correctness.** AST checks name, types, and structure against the expected call; executable tracks check runtime. Multi-turn “missing parameter / missing function” splits punish invented args and skipped clarification. **[inferred] enterprise F1:** treat gold tool sequence as a set (or ordered list); **precision** = fraction of emitted calls that are allowed+correct; **recall** = fraction of required calls emitted; **F1** of that set. Do not use BLEU on JSON. Do not LLM-judge parameter equality when a JSON schema exists.

**V4 Web Search (blog 2025-07-17):** 100 human multi-hop questions, DuckDuckGo + fetch, snippet vs no-snippet (200 scored entries). Ablation: disable search → accuracy collapses — models are not secretly answering from params. Memory + format-sensitivity complete the agentic 40%.

**Tool selection vs SWE/τ/GAIA.** BFCL asks “right function, right args.” τ asks “right DB mutation under policy.” SWE asks “tests green.” A model can ace BFCL Live AST and fail τ policy. Ship gates should include **both** a function-calling unit (BFCL-style AST on your schema) and a **stateful** scenario pack (τ-style).

### 1.5 Quality: judges, pairwise, rubrics, humans, faithfulness

**LLM-as-judge (Zheng et al., MT-Bench / Chatbot Arena, arXiv:2306.05685).** GPT-4 judge vs humans: **>80%** agreement, matching human–human. Documented biases: **position**, **verbosity**, **self-enhancement**, weak math. Mitigations: swap order and treat flips as ties; length-normalize; cross-family judges. Later meta-eval (TrustJudge, arXiv:2509.21117) still uses double-order pairwise as the position-bias baseline.

**G-Eval (Liu et al., EMNLP 2023, arXiv:2303.16634).** Auto-generated CoT evaluation steps + form-fill; GPT-4 Spearman **0.514** on summarization vs humans, beating BLEU/ROUGE/BERTScore. Authors flag **bias toward LLM-generated text**. Token-probability-weighted scores (original method) are more stable than greedy integer scores — most vendor UIs skip this and just ask for an int.

**Rubric eval (HealthBench, OpenAI, arXiv:2505.08775; openai.com/index/healthbench).** 5,000 multi-turn conversations; **262** physicians / **60** countries; **48,562** unique criteria; median **11** criteria/example (range 2–48). Axes: accuracy, completeness, context awareness, communication, instruction-following. Grader: **GPT-4.1**, physician-validated (PMC viewpoint: macro F1 **0.71** on themes). Score = weighted points met / max. o3 **~60%**; GPT-4o **32%**; GPT-3.5 Turbo **16%**; GPT-4.1 nano beats GPT-4o at **25×** lower cost (paper). HealthBench Hard: then-top **32%**. Consensus subset: 34 physician-agreed dimensions. simple-evals hosts the harness (repo frozen to new models as of Jul 2025; code remains). This is the template for enterprise rubrics: **itemized, weighted, conversation-specific**, not a single 1–5 vibe.

**Pairwise.** LangSmith pairwise queues and pairwise LLM evaluators: use when absolute scores are unstable but A vs B is easy (summaries, tone). Always run both orders.

**Human raters.** LangSmith annotation queues: single-run (rubric + assertions that become future offline graders) and pairwise; multi-reviewer, reservations, export to datasets. Humans set **thresholds and calibration**, not 100% of volume. Hamel Husain’s evals post (linked from LangSmith concepts): *look at traces first*.

**Faithfulness (RAGAS, Es et al., EACL 2024 demo, arXiv:2309.15217).** Not “true in the world” — **entailed by retrieved context**. Pipeline: extract atomic statements → NLI vs context → fraction supported. WikiEval: RAGAS faithfulness **~95%** agreement with humans vs direct GPT scoring **72%**. ⚠️ A faithful answer can still be the wrong answer if retrieval missed the doc. Pair with answer relevance + context precision/recall. Code path: `Faithfulness` in explodinggradients/ragas.

**SimpleQA (Wei et al., OpenAI).** Short-form factuality; single indisputable answer; grade **correct / incorrect / not attempted**. Adversarially hard vs GPT-4. F-score balances attempted-correct vs hallucinations. Claude-class models attempt less → similar F at lower accuracy. Grader is itself an LLM (A/B/C regex; default C = not attempted).

---

## 2. Token Economics & NFR Metrics

### 2.1 The eval bill is a second product

Eval cost = **(agent tokens + tool I/O + sandbox time) × dataset × repetitions × (1 + judge tokens × criteria) + platform traces**. Judge cost is not rounding error on rubric benches: HealthBench median 11 criteria × 5,000 examples = **55k grader calls** per model. Gaia2 uses a 70B judge on write-args. Online eval is **traffic × sample rate × judge**.

**LangSmith platform SKUs (langchain.com/pricing + usage-and-billing, 2026-08-21):**

| Meter | Published number |
| --- | --- |
| Developer | $0/seat, **5k** base traces/mo included, 1 seat |
| Plus | **$39**/seat/mo, **10k** base traces/mo, then PAYG |
| Base trace | **0.05¢** ($0.0005); **14-day** retention |
| Extended trace | **10×** base → **0.50¢** ($0.005) all-in; upgrade **0.45¢**; **400-day** (Enterprise can customize) |
| Experiments | runs created at **extended** retention by default |
| Online eval / rules | **auto-upgrade** matching traces to extended unless you opt out |
| LCU | **$1.50**; Engine run **~5–30 LCU [estimate in vendor FAQ]** → **~$7.50–$45**/Engine tick; Engine every **6h** |
| LSU | **$1.00** (storage/compute-adjacent) |
| Tuned Evaluators | Plus/Enterprise US public beta; **0.01 LCU** per successful Perceived Error eval run; skipped/failed not billed |
| Plus ingest caps | **500k events/hour**, **5.0 GB/hour**; 25k runs/trace max |
| Evaluator spend cap | weekly USD, resets **Monday 00:00 UTC**; OpenAI/Anthropic/Gemini with configured prices only; in-flight may **overshoot** |

LangSmith spend ≠ provider invoice (discounts, unlisted variants). Gateway Credits and Tuned Evaluators are extra meters.

**[inferred] $ per 1k eval runs (platform only, Plus, experiment = extended traces):** \(1000 \times \$0.005 = \$5\) LangSmith. Same 1k as **base** online traces with eval opt-out: \(1000 \times \$0.0005 = \$0.50\). At 100% online judge without opt-out you pay both the upgrade and the judge API.

**[inferred] $ per 1k judge-only calls** on Claude Sonnet 4.6 (Anthropic cache table: **$3 / $15** per MTok in/out; 5-min write **$3.75**, 1h write **$6**, read **$0.30**). Reference loop: 2,000 input + 200 output, no cache: \(1000 \times (2000\times3 + 200\times15)/10^6 = \$9\). With a stable rubric prefix cached after first write (0.1× on 1,800 prefix tokens, 200 unique): ~**$1.1** input-side after warmup **[inferred]**. Agent-under-test tokens usually dominate judges.

**OpenAI cache + reasoning (developers.openai.com, 2026):** prompts ≥ **1,024** tokens. GPT-5.6+: cache **read 0.1×**, **write 1.25×** (reported `cache_write_tokens`); explicit breakpoints + `prompt_cache_options.ttl` **30m**. Pre-5.6: automatic, no write surcharge, `prompt_cache_retention`. Published short-context example: **gpt-5.6-sol** input **$5**, cached **$0.50**, writes **$6.25**, output **$30** per 1M. Reasoning tokens: **billed as output**, occupy context, often **invisible**; `usage.output_tokens_details.reasoning_tokens`. OpenAI recommends reserving **≥25,000** tokens for reasoning+output when first using reasoning models. `max_output_tokens` can expire **before any visible token** (`incomplete` / `max_output_tokens`). Pro mode aggregates extra work at **standard rates** — eval harnesses that “turn on thinking” without a token cap will blow both **$ and e2e**.

**Anthropic cache + thinking (platform.claude.com prompt-caching):** multipliers **1.25×** (5 min write), **2×** (1 h write), **0.10×** read. Opus 4.6/5 line: **$5 / $25** in/out; 5-min write **$6.25**, 1h **$10**, read **$0.50** per MTok. Sonnet 4.6: **$3 / $15**; read **$0.30**. Thinking blocks **cannot** take `cache_control` directly; they **can** be cached as prior-turn content and then count as **cache-read input**. Changing thinking/effort **invalidates** message (and often tool/system) caches. Eval implication: a sweep over `budget_tokens` / effort is a **cache-busting** cost multiplier, not a free knob.

⚠️ Third-party TTFT benches (toolbrain.net, May 2026) reported Claude cache-hit TTFT **2.89s → 1.36s (53%)** on one Opus 4.7 setup; treat as anecdotal, not an SLO.

### 2.2 Tokens/task, $/task, cache in the harness

| Workload | What to meter | Cache advice |
| --- | --- | --- |
| SWE-bench-class | Agent tokens + **Docker minutes** + (optional) patch-rerank model | Stable system+tools prefix; per-instance user issue at the tail |
| τ-bench-class | Agent + **user-sim** LLM + tools; × trials for pass^k | User-sim and agent should **not** share a cache key |
| Gaia2 | Agent + **101-tool system prompt** + 70B judge on writes | System+tools are the cache; scenario body is not |
| Rubric (HealthBench-style) | 1 completion + **K** grader calls | Cache the rubric template; bind example-specific criteria after the breakpoint |
| Online 1% sample | Live tokens already paid; add judge × 0.01 | Separate `prompt_cache_key` for judges vs agents |

LangSmith experiment **caching** (`LANGSMITH_TEST_CACHE`) replays identical API calls from disk — good for **scorer iteration**, poisonous if you think you re-measured the agent. SWE-bench result cache is worse: it can hide a new patch. Gaia2 `--scenario_timeout 300` and `--max_concurrent_scenarios` are **harness NFRs**, not product SLOs.

### 2.3 Latency: TTFT, e2e, percentiles, SLO vs harness

| Metric | Definition | Eval harness | Production SLO |
| --- | --- | --- | --- |
| **TTFT** | Time to first visible token (stream) | Often unused; batch APIs hide it | Chat UX; cache hits cut TTFT |
| **e2e** | Request start → final answer / tool-loop done | Job time / dataset; dominated by slowest example unless bounded | User-facing; agent loops are **multiples** of single-call e2e |
| **p50/p95/p99** | Latency distribution | Report **per example** and **per experiment**; p99 of a 50-item set is noise | Need volume; ⚠️ vendors do not publish *your* p99 |
| **Time-to-score** | Root span end → judge span end | Braintrust timeline (optional) | Must not gate the response |

**Do not use eval-harness wall time as an SLO.** Harness e2e includes dataset load, Docker pull, judge queues, retries, and `max_concurrency`. Production p95 is the user path only. LangSmith and Phoenix attach latency/token/cost on **spans** — that is the SLO telemetry. Percentiles on *online judges* measure the sidecar, useful for “will scores be 5 minutes late?” not “is chat fast?”

**[inferred] eval p95:** with `max_concurrency=N` and i.i.d. example times, experiment wall-clock ≈ (n/N)×mean + tail from retries/timeouts. Gaia2 default timeout **300s/scenario** means a single hung tool sets a **5 min** floor on that example’s e2e regardless of TTFT.

**Cost of eval latency:** reasoning models add TTFT (thinking happens before tokens) and e2e. Batch eval should use batch APIs / Flex where available; interactive eval (Playground) should not. ⚠️ No public “p99 of LangSmith evaluate()” — only ingest 429s (Plus 500k events/hour).

**[inferred] $ per 1k eval runs, all-in example (not a quote):** 1k τ-like tasks, 1 trial, Sonnet 4.6 agent ~8k in / 1.5k out with 70% cache read on 6k prefix: agent ≈ \(1000\times[(0.3\times2000+0.7\times6000\times0.1)\times3 + 1500\times15]/10^6 \approx \$26\) **[inferred]** + user-sim similar order + $5 LangSmith extended + $9 judge if you add a trace-judge. **pass^4 multiplies agent+sim by ~4.** Reliability eval is a **budget line**, not a unit test.

---

## 3. Distributed Resilience & State

### 3.1 Why evals flake

| Source | Symptom | Mitigation |
| --- | --- | --- |
| Agent sampling | pass@1 jitter; 24.9 pp envelopes | `num_repetitions`; report pass@k **and** pass^k; power analysis (Randomness paper) |
| T=0 still diverges | Vestige non-determinism plots | Pin seed **and** treat residual as aleatoric; don’t claim determinism |
| User simulator | τ pass^k collapses | Freeze user-sim model+prompt; never silently upgrade |
| Docker / network | SWE “instances with errors”, infra failures | Distinguish harness crash vs unresolved; `--cache_level` hygiene; never score errors as fails without a bucket |
| Live web | GAIA / BFCL search | Snapshot or mock search in CI; live only in nightly |
| Judge stochasticity | rubric flip | Temp 0, structured output, double-order pairwise, majority of 3 on ship gates |
| Dataset drift | “regression” from label edits | Pin dataset **version/tag** in CI |
| Result caches | false stability | New `run_id`; don’t commit `LANGSMITH_TEST_CACHE` for agent calls |

LangSmith `error_handling: log | ignore`; default `max_concurrency=0` (changed in langsmith 0.2.0 from unlimited) — CI that forgets to set concurrency will look “resilient” because it is accidentally serial.

### 3.2 Replay, checkpoints, datasets as durable state

- **Dataset versions:** LangSmith automatic versions + tags for CI. Datasets have **indefinite** retention even when traces expire — promoting a trace is a **copy into a longer-lived legal record**.
- **Experiment immutability:** Braintrust: playground is mutable; experiment is the snapshot. Compare against a named baseline experiment, not “last week’s vibe.”
- **Trace → dataset:** all three vendors; this is the closed loop. Topics (Braintrust, daily cluster after ≥100 facet summaries) + `bt topics rewind` for replay of classifications.
- **SWE-bench:** Docker images + logs under `logs/run_evaluation`; cache is a checkpoint **and** a footgun.
- **Gaia2:** `--output_dir` + HF upload of traces; `are-benchmark judge` is a **second pass** (offline validation) — split run vs judge so you can replay judging without re-running agents.
- **OpenInference / Phoenix:** spans in OTLP collectors you already operate; replay = re-score DataFrame, not re-call the agent.

### 3.3 Circuit breakers on judge models

Published, not folklore:

1. **LangSmith evaluator spend cap** — pauses the evaluator on that project/dataset when weekly USD hits; **agent traffic continues**; skipped runs **not backfilled**; in-flight overshoot allowed. This is a **judge circuit breaker**.
2. **LangSmith tracing usage limits** — 429 when monthly all-traces or extended-traces hit; extended cap also blocks retention-upgrading evals/rules.
3. **LangSmith 429 classes** — 1-min ALB (e.g. POST `/runs*` 5000/min), hourly events/bytes, monthly unique traces on unpaid Developer (5k). SDK batching up to 100 runs. Retry with jitter; saturation ≠ retryable.
4. **Braintrust** `sampling_rate` + `Reporter.reportRun → bool` non-zero CI exit — quality gate, not a latency breaker. Online scoring is async (load shed by sampling).
5. **Provider 429/5xx** — standard; **[inferred]** fail-open (skip score, flag unscored) for **online** monitoring; fail-closed (fail the job) for **CI ship gates**.
6. **Unsupported judge models** once a LangSmith spend limit exists (must be OpenAI/Anthropic/Gemini with a price row) — a config-level breaker.

⚠️ No vendor publishes “circuit breaker trips/hour” as an SLO. Design for **unscored ≠ passed**. Dashboards must show **coverage %** (traces with a judge score) as a first-class NFR; otherwise a tripped breaker silently paints quality green.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP for eval tools

Eval harnesses increasingly **are** MCP clients: Gaia2/ARE attach MCP; LangSmith Deployment can expose agents as MCP servers; coding-agent evals call MCP browsers/tickets. MCP authorization (spec 2025-06-18 onward; tutorial dated 2026-07-28): **OAuth 2.1**, **PKCE (S256)**, Protected Resource Metadata **RFC 9728**, AS metadata **RFC 8414**, resource indicators **RFC 8707** (token audience = that MCP server). **Token passthrough is forbidden.** STDIO transports are **out of** the OAuth spec (env credentials instead). Implicit/ROPC gone; bearer tokens in query strings forbidden.

Zero-trust mapping for **eval**:

| Control | Why eval is special |
| --- | --- |
| Audience-bound tokens per MCP server | A “search MCP” used in BFCL-like eval must not accept a token minted for “admin MCP” |
| Separate IdP clients for CI vs prod | CI eval bots should not inherit user refresh tokens |
| Allowlist MCP URLs in the harness | ARE’s own warning: untrusted MCP = RCE-adjacent |
| No production write APIs on eval MCP | τ-style eval should hit **simulators**; SWE should hit **ephemeral Docker**, not corp Git |
| SSRF controls on CIMD URL fetch | Spec: AS fetching client metadata must be SSRF-safe |

### 4.2 PII in traces (the real eval dataset)

| Product | Control |
| --- | --- |
| **LangSmith** | Shared-responsibility: **you** filter PII before ingest. SDK anonymizer (regex / Presidio / Comprehend); `LANGSMITH_HIDE_INPUTS` / `HIDE_OUTPUTS`; Gateway PII/secrets redaction (**beta**) — redacts provider **and** trace, but **not** traces that bypass the gateway. AES-256 at rest, TLS 1.2+. Engine sends trace content to model subprocessors under **ZDR per analysis task** (docs reviewed 2026-06-25). Retention 14d/400d is a **privacy control**, not just cost. |
| **OpenInference / Phoenix / AX** | `OPENINFERENCE_HIDE_INPUTS/OUTPUTS/MESSAGES/TEXT/IMAGES`, `HIDE_LLM_TOOLS`, `HIDE_EMBEDDING_VECTORS`, … Code `TraceConfig` beats env. Hiding inputs also hides tool defs. |
| **Braintrust** | Global masking on inputs/outputs/metadata/context; hybrid so AI data never lands on vendor disk; Topics summarization **reads trace text** — scrub first or filter the pipeline. Self-hosted Topics: ZDR to the same endpoints. |

Hiding all I/O makes **offline eval impossible** (no content to score). Pattern: **tokenize/mask PII but keep task structure**; store a keyed mapping in *your* vault if replay needs the real email. Judge prompts should receive **already-redacted** text or you have exported PII to a second model vendor.

### 4.3 RBAC on datasets and audit of judge scores

LangSmith: org roles (User/Admin) on Developer/Plus; Enterprise **custom SSO, ABAC, RBAC**. Evaluator spend limits require `organization:manage`. Datasets/experiments inherit workspace IAM — treat **dataset write** as production-data write. Audit: LangSmith **audit logs** (docs); feedback objects `{key, score|value, comment}` on runs. Online eval auto-upgrade is an auditable *retention* event.

Braintrust: project automations, scorer functions as code (reviewable), score spans with judge reasoning — **the comment is the audit trail**. Hybrid: customer IAM on the data plane.

Phoenix: self-host means **your** SSO in front of the UI; span annotations are the score audit if you restrict who can `log_evals`.

**[inferred] minimum audit record:** `(example_id | trace_id, evaluator_id, evaluator_version, model+params, prompt_hash, score, rationale, timestamp, dataset_version)`. Without `evaluator_version`, you cannot explain a metric jump after a rubric tweak.

PCI and similar: LangSmith explicitly **prohibits cardholder data** on the platform. Health/finance eval sets may need **self-hosted Phoenix or Braintrust hybrid**, not SaaS traces.

---

## 5. Production Failure Modes

### 5.1 Reward hacking and metric gaming

Hierarchy (surveys 2026: Discover AI 10.1007/s44163-026-01980-z; arXiv:2604.13602): verbosity/sycophancy → fake CoT → **judge-steering** (format, injection) → **environment tampering** (edit tests, mock APIs, exfiltrate gold). LLM-as-judge biases (Zheng): position **~10–15 pt** pairwise swing; verbosity **15–30 pt** (Wang et al. 2023, widely cited); self-preference **10–25%**. If that judge is the **reward**, RL will farm it. Shi et al. 2024: optimization-based injection of judges. Tong et al. 2025: poisoned judge-training data → backdoors.

SWE-bench: hidden tests exist *because* models will pattern-match visible tests. Contamination (Verified gold-patch regurgitation; Pro 23%→80% then 30% broken) is **metric gaming by the industry**, not just by the agent. Gaia2 private test set exists for the same reason; public validation will saturate.

**Defenses that appear in production write-ups:** trajectory publication (Poolside); mix **code oracles + judges**; cross-family judges; adversarial judge prompts; human spot-check of high-score traces; forbid known shortcuts in the rubric **and** in the environment (the instruction is not a security boundary).

### 5.2 Judge bias, distribution shift, eval leakage

| Failure | Mechanism | Detection |
| --- | --- | --- |
| **Position / length / family** | Autoregressive judge | Swap order; length-matched A/B; alien judge family |
| **Shared blind spots** | Same pretraining as the agent | HealthBench-style physician calibration; PMC warning on synthetic-only |
| **Distribution shift** | Offline set ≠ prod topics | Braintrust Topics weekly; add prod failures to datasets; online coverage % |
| **Eval leakage** | Bench in training; gold in prompt; test_patch visible | SWE-rebench / live cuts; canary strings; contamination pipelines (OpenAI Verified/Pro audits) |
| **User-sim leakage** | Sim quotes the policy the agent should infer | Pin sim; independent review of sim transcripts |
| **Cache leakage** | Warm cache in eval, cold in prod | Report cache hit rate in both; bust cache in a “cold” slice |
| **Harness confounding** | Scaffold, not model | mini-SWE-agent / bash-only tracks; Gaia2 uniform ReAct |

G-Eval’s own paper: judges prefer LLM-ish text. Rubric gaming: agents learn the HealthBench *style* (hedge, cite, ask follow-up) without clinical correctness — **[inferred]** until item-level facts are checked.

### 5.3 Operational failure modes

- **Silent judge outage** → scores stop, dashboards freeze at last value → look like stability. Mitigation: coverage monitors + spend-cap alerts.
- **Extended-retention surprise bill** → online eval default-on. Mitigation: opt out of upgrade; sample 1–5%.
- **Engine / Topics reading PII** → subprocessors. Mitigation: mask at SDK.
- **CI eval using live MCP prod** → destructive writes. Mitigation: simulators + OAuth audience.
- **pass@1 CI gate on agents** → flaky red/green. Mitigation: repetitions + pass^k on a **small** canary set; full pass@k nightly.

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A — Coding agent, regulated bank

| Choice | Pick | Why |
| --- | --- | --- |
| Oracle | SWE-style hidden tests in **ephemeral** runners + PASS_TO_PASS | Binary ship gate |
| Public bench | Not Verified/Pro as KPI; **internal** issues after cutoff | Contamination + 30% Pro rot |
| Process | Tool/trace policy: no `.git/config` writes; record pytest node-ids | Reward-hack surface |
| Observability | Self-hosted Phoenix or Braintrust hybrid | PII/code in traces |
| Judge | Only for PR description quality; **never** for merge | Tests merge |
| NFR | Report tokens/issue + p95 sandbox time; not “SWE %” | Cost/latency are the SLO |

### 6.2 Scenario B — Customer-support agent (τ-shaped)

| Choice | Pick | Why |
| --- | --- | --- |
| Oracle | Final **CRM/DB state** + policy checklist | τ lesson: conversation fluency ≠ correct mutation |
| Reliability | pass^k on canary tasks in CI (k=3–5) | Original retail pass^8 hole |
| User sim | Frozen model; versioned | Dual-control τ² if users have apps |
| Online | Sampled rubric judge on **threads** (coherence, tone) | Reference-free |
| Gate | Code scorer: “refund ≤ policy cap” | Don’t LLM-judge arithmetic |
| Cost | pass^4 × 2 LLMs (agent+sim) is the real $/task | Budget for reliability |

### 6.3 Scenario C — RAG assistant, faithfulness SLO

| Choice | Pick | Why |
| --- | --- | --- |
| Offline | RAGAS faithfulness + context precision on a pinned corpus snapshot | Grounding ≠ world-truth |
| Online | Hallucination evaluator at 1–5% sample; spend cap | Judge cost |
| Human | Weekly annotation queue on low-faithfulness traces | Calibration |
| Leakage | Never put gold answers in the retriever index used at eval | |
| Latency | Faithfulness judge **async**; user SLO on TTFT of the **answer** | |

### 6.4 Scenario D — General assistant / Gaia2-like

| Choice | Pick | Why |
| --- | --- | --- |
| Env | Simulated apps + optional MCP allowlist | Reproducible writes |
| Score | Oracle write-actions; mix exact + judge | Gaia2 recipe |
| Pareto | Plot success vs $ vs time (ARE paper) | GPT-5-high may win accuracy and lose cost |
| Time | Don’t pause the world | Async failures are the product |
| Leaderboard | Validation only; hold a private test | Saturation |

### 6.5 Trade-off matrix (ship architecture)

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| **Task success** | pass@1, n=1 | pass@1 with 3 reps + CI delta | pass^k canary + nightly pass@k |
| **Oracle** | LLM-as-judge 1–5 | Code + judge | Hidden tests / DB state + human audit |
| **Traces** | SaaS, 14d, 1% sample | SaaS extended on failures only | Hybrid/self-host, mask at SDK |
| **Tools eval** | “Did it call a tool?” | Schema validate args | BFCL-style AST + irrelevance tests |
| **Judge** | Same family as agent | Cross-family, swap pairwise | Rubric + expert calibration set |
| **MCP** | STDIO secrets in CI | OAuth, audience per server | No prod MCP in eval; simulators |
| **Cost control** | Unlimited online judge | Sample + weekly spend cap | Fail-closed CI; fail-open online with coverage SLO |
| **Latency KPI** | Harness wall time | Span p95 on prod traces | Separate TTFT / e2e / time-to-score |

**Interview close:** Evaluation is a **distributed system** with a control plane (harness, CI, spend caps), a data plane (traces, datasets, PII), and an untrusted sidecar (judges, MCP tools). Task success without process, cost, and latency is a leaderboard. Process without a hard oracle is a vibe. The Principal Architect answer is **dual oracles, versioned datasets, coverage SLOs on judges, and named (split, scaffold, date)** — never a naked percentage.

---

## Sources

1. https://arxiv.org/abs/2107.03374 — Chen et al., HumanEval / pass@k (2021)
2. https://www.swebench.com/original.html — SWE-bench 2,294 / 12 repos
3. https://github.com/SWE-bench/SWE-bench — Docker harness, result cache
4. https://www.swebench.com/SWE-bench/faq/ — splits (Verified 500, Lite 300, …)
5. https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ — Verified contamination (2026)
6. https://openai.com/index/separating-signal-from-noise-coding-evaluations/ — SWE-bench Pro ~30% broken (2026-07)
7. https://arxiv.org/abs/2509.16941 — SWE-Bench Pro paper
8. https://scaleapi.github.io/SWE-bench_Pro-os/ — Pro public split, 23.3% GPT-5-era
9. https://arxiv.org/html/2512.10218v2 — Verified vs BeetleBox/SWE-rebench localization
10. https://arxiv.org/pdf/2602.07150 — On Randomness in Agentic Evals
11. https://github.com/ASSERT-KTH/vestige — pass@k / pass^k trajectory tool
12. https://arxiv.org/abs/2311.12983 — GAIA
13. https://huggingface.co/blog/gaia2 — Gaia2 + ARE (2025-09-22)
14. https://arxiv.org/pdf/2509.17158 — ARE / Gaia2 paper
15. https://huggingface.co/datasets/meta-agents-research-environments/gaia2 — Gaia2 dataset card
16. https://facebookresearch.github.io/meta-agents-research-environments/user_guide/gaia2_evaluation.html — leaderboard / private test
17. https://facebookresearch.github.io/meta-agents-research-environments/user_guide/benchmarking.html — are-benchmark CLI
18. https://arxiv.org/abs/2406.12045 — τ-bench
19. https://github.com/sierra-research/tau-bench — original repo (outdated warning)
20. https://github.com/sierra-research/tau2-bench — τ²/τ³
21. https://arxiv.org/abs/2506.07982 — τ²-Bench paper
22. https://sierra.ai/blog/tau-bench-shaping-development-evaluation-agents — Sierra on pass^k
23. https://taubench.com/ — τ³ voice / knowledge
24. https://www.anthropic.com/engineering/claude-think-tool — think tool × τ-bench pass^k
25. https://www-cdn.anthropic.com/c788cbc0a3da9135112f97cdf6dcd06f2c16cee2.pdf — Claude Opus 4.6 system card (τ² table)
26. https://gorilla.cs.berkeley.edu/leaderboard — BFCL V4
27. http://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html — V4 weights + AST
28. https://proceedings.mlr.press/v267/patil25a.html — BFCL ICML 2025
29. https://arxiv.org/abs/2306.05685 — LLM-as-judge / MT-Bench
30. https://arxiv.org/abs/2303.16634 — G-Eval
31. https://arxiv.org/html/2509.21117v1 — TrustJudge
32. https://arxiv.org/abs/2309.15217 — RAGAS
33. https://github.com/explodinggradients/ragas/blob/298b6827/src/ragas/metrics/collections/faithfulness/metric.py — faithfulness implementation
34. https://cdn.openai.com/papers/simpleqa.pdf — SimpleQA
35. https://github.com/openai/simple-evals — HealthBench / SimpleQA / BrowseComp harness
36. https://openai.com/index/healthbench/ — HealthBench announcement
37. https://arxiv.org/abs/2505.08775 — HealthBench paper
38. https://pmc.ncbi.nlm.nih.gov/articles/PMC12547120/ — HealthBench clinical critique (F1 0.71)
39. https://docs.langchain.com/langsmith/evaluation-concepts — offline vs online
40. https://docs.langchain.com/langsmith/evaluation — workflow
41. https://docs.langchain.com/langsmith/evaluation-types — types
42. https://docs.langchain.com/langsmith/experiment-configuration — reps / concurrency / cache
43. https://docs.langchain.com/langsmith/usage-and-billing — 0.05¢ / 0.50¢, 14d/400d, 429s
44. https://docs.langchain.com/langsmith/evaluator-spend — weekly judge spend caps
45. https://www.langchain.com/pricing — seats, traces, LCU $1.50
46. https://docs.langchain.com/langsmith/mask-inputs-outputs — PII masking
47. https://docs.langchain.com/langsmith/shared-responsibility-model — tenant vs customer data
48. https://docs.langchain.com/langsmith/llm-gateway-data-protection — gateway redaction beta
49. https://docs.langchain.com/langsmith/engine-security — Engine / ZDR
50. https://www.braintrust.dev/docs/evaluate — eval loop
51. https://www.braintrust.dev/docs/evaluate/score-online — async online scoring
52. https://www.braintrust.dev/docs/evaluate/run-in-ci — CI reporters
53. https://www.braintrust.dev/docs/evaluate/write-scorers — span/trace/group
54. https://www.braintrust.dev/blog/security-data-control — hybrid control/data plane
55. https://arize.com/docs/phoenix.md — Phoenix tracing + evals
56. https://github.com/Arize-ai/openinference — OpenInference
57. https://arize-ai.github.io/openinference/spec/ — span kinds
58. https://arize.com/docs/phoenix/tracing/how-to-tracing/advanced/masking-span-attributes — OPENINFERENCE_HIDE_*
59. https://developers.openai.com/api/docs/guides/prompt-caching — 0.1× / 1.25× GPT-5.6+
60. https://developers.openai.com/api/docs/guides/reasoning — reasoning tokens as output
61. https://developers.openai.com/api/docs/pricing — gpt-5.6-sol published rates
62. https://platform.claude.com/docs/en/build-with-claude/prompt-caching — Anthropic 1.25×/2×/0.1×; thinking cache
63. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization — MCP OAuth 2.1
64. https://modelcontextprotocol.io/specification/draft/basic/authorization — RFC 9728/8707, no passthrough
65. https://link.springer.com/article/10.1007/s44163-026-01980-z — reward hacking survey
66. https://arxiv.org/html/2604.13602v1 — reward hacking mechanisms
67. https://www.appen.com/blog/reward-hacking-ai-agent-evaluation — trajectories as evidence
68. https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge — sampling rates (see evaluation-concepts cross-links)

**Coverage confirmation:** task success (pass@k, pass^k, SWE-bench family + 2026 audits, GAIA/Gaia2, τ/τ²/τ³, binary vs rubric partial credit); trajectory (OpenInference/LangSmith/Braintrust/Phoenix, process vs outcome, offline replay); tool accuracy (BFCL V4 AST, irrelevance/hallucination, parameter/state checks, F1 **[inferred]**); quality (MT-Bench, G-Eval, HealthBench rubrics, pairwise, humans, RAGAS faithfulness, SimpleQA); cost (LangSmith meters, OpenAI/Anthropic cache + thinking/reasoning, **[inferred]** $/1k); latency (TTFT vs e2e vs time-to-score, harness ≠ SLO); six research dimensions (topology, token/NFR, resilience, security, failure modes, design scenarios).
