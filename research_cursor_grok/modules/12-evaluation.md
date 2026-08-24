# Module 12 — Evaluation

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/12-evaluation.md` (researched 2026-08-21, 68 sources). Prices and benchmark numbers are from named vendor docs or papers as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 SLOs are invented. `$ per 1k eval runs` figures are **[inferred]** from published SKUs × a stated reference loop — not a vendor “per eval” SKU.
**Mandatory topics**: Task success · Trajectory · Tool accuracy · Quality · Cost · Latency.

The unit of production is not “the model scored 91%.” It is a **control plane** that versions datasets, attaches evaluators, enforces spend caps, and ships on **dual oracles**, wrapping a **data plane** of traces, sandbox I/O, and judge sidecars. LangSmith’s split is the published topology: **offline** runs on *datasets/examples* and produces an *experiment*; **online** runs on *runs/threads* from a tracing project. Braintrust is isomorphic (playground → immutable experiment → `bt eval` → online scoring on logs). Phoenix is the OTel twin (OTLP + OpenInference). Interview answers that collapse `(model × scaffold × tools × oracle × sampling)` into a naked percentage fail when the follow-up is “named split, named scaffold, date, contamination status — and is the judge on the user path?”

**Invariant:** the harness is not the product. Outcome oracles (hidden tests, DB goal-state, exact-match) and process oracles (judge-on-trace, tool F1) answer different questions. Mixing them in one number is how teams Goodhart themselves. Online scoring **must not** sit on the user critical path (Braintrust: async, UI hides scorer duration unless toggled; LangSmith online evaluators attach to ingested traces). If “eval” is a synchronous second LLM call in the request handler, you built a latency tax, not an eval system **[inferred]**.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, evaluator attachments (sampling, filters, weekly USD cap — **per attachment**, not per evaluator definition), dataset version/tag, experiment identity, judge circuit (LangSmith spend cap; tracing 429s), fail-open vs fail-closed policy, and Temporal workflow id / Kafka outbox for the **eval job**. Data plane owns two clocks: the **harness clock** (batch: dataset → target fn → scorers → experiment) and the **user SLO clock** (live nested span tree; TTFT / e2e). Persistence is not one store: versioned datasets (indefinite even when traces expire), immutable experiments, traces at 14-day base vs 400-day extended, feedback objects, WORM audit of who/what scored. Tool proxies for eval are **simulators and ephemeral Docker**, not corp Git or production CRM writes; MCP is allowlisted (Gaia2/ARE: untrusted MCP is RCE-adjacent). Telemetry is the only authoritative place for coverage % (traces with a judge score), tokens/task, cache hit %, sandbox minutes, time-to-score, and breaker state. **Eval datasets are as sensitive as production logs** because they *are* production logs that someone promoted. If the data plane holds PII, the judge model is a **subprocessor** on every online-eval call.

Two planes, two clocks, two oracles (research table):

| Plane | Clock | Store | Oracle |
| --- | --- | --- | --- |
| Eval harness (control) | Job wall-clock; retries; `num_repetitions` | Versioned dataset + immutable experiment | Reference outputs, hidden tests, DB goal-state, rubric |
| Production tracing (data) | User SLO (TTFT / e2e) | Trace store (14d vs 400d, or self-hosted OTLP) | Reference-free: safety, format, sampled LLM-as-judge |
| Judge / scorer (sidecar) | Async; **must not** sit on the user path | Feedback attached to run/span | Score + comment; audit of who/what scored |

Braintrust hybrid: UI/auth/metadata stay in the vendor control plane; experiment logs, traces, datasets, prompts, completions stay in the customer data plane (VPC / on-prem). LangSmith Enterprise: Cloud / Hybrid / Self-Hosted (Hybrid = SaaS control + self-hosted data). Phoenix self-hosts the whole stack.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (CI `bt eval` / GitHub Action / Playground / live user / annotators)   │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant principal (never sandbox IAM)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (LangSmith SaaS / Braintrust UI+auth / your CI orchestrator)     │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Eval router  │─▶│ Orchestrator          │  │
│  │ auth,quota │  │ PII detect→  │  │ OFFLINE: pin │  │ Temporal wf =         │  │
│  │ 500k evt/h │  │ redact→audit │  │ dataset ver  │  │  dataset×agent×scorer │  │
│  │ 5 GB/h     │  │ tool RBAC    │  │ ONLINE: sample│ │ Kafka outbox: intent  │  │
│  │ 25k runs/  │  │ MCP allowlist│  │ 1–5% + spend │  │  before sandbox/judge │  │
│  │  trace cap │  │ no prod writes│ │  cap / attach │  │ fail-closed CI /      │  │
│  │ breaker    │  │ CI ≠ prod IdP│  │ never on SLO │  │ fail-open online      │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
└─────────────────────────┼─────────────────┼─────────────────────┼───────────────┘
                          │                 │                     │
          ┌───────────────┘                 │                     └───────────────┐
          ▼                                 ▼                                     ▼
┌─────────────────────────────┐  ┌─────────────────────────────┐  ┌───────────────┐
│ EVAL HARNESS (job clock)    │  │ PRODUCTION TRACES (SLO clk) │  │ JUDGE SIDECAR │
│ DATA PLANE of the batch     │  │ DATA PLANE of live traffic  │  │ async only    │
│                             │  │                             │  │               │
│  dataset examples ────────┐ │  │  root span (user request)   │  │  code scorer  │
│  target fn / agent loop   │ │  │    ├─ LLM / AGENT           │  │  LLM-as-judge │
│  num_repetitions          │ │  │    ├─ TOOL / RETRIEVER      │  │  pairwise A/B │
│  max_concurrency          │ │  │    └─ EVALUATOR (after)     │  │  human queue  │
│  disk cache (scorers!)    │ │  │  OpenInference kinds        │  │  T=0, schema  │
│  experiment (immutable)   │ │  │  14d base / 400d extended   │  │  spend cap =  │
│  promote fail → dataset   │ │  │  online eval auto-upgrade   │  │  judge breaker│
│                             │  │  retention unless opt-out   │  │  unscored≠pass│
└──────────────┬──────────────┘  └──────────────┬──────────────┘  └───────┬───────┘
               │                                 │                         │
               │         ┌───────────────────────┴─────────────────────────┘
               │         │  scores attach to run/span AFTER root ends
               ▼         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ TOOL PROXIES (eval = simulators; prod = real IAM — never share tickets)         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ τ-style CRM  │  │ SWE Docker   │  │ BFCL-like    │  │ Gaia2/ARE MCP      │   │
│  │ simulator    │  │ ephemeral @  │  │ schema/AST   │  │ allowlist URLs     │   │
│  │ no prod CRM  │  │ base_commit  │  │ no BLEU-JSON │  │ OAuth aud per srv  │   │
│  │ user-sim LLM │  │ hidden tests │  │ irrelevance  │  │ PKCE S256; no pass │   │
│  │ cache ≠ agent│  │ new run_id   │  │  track       │  │ through; STDIO env │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────────┘   │
└────────────────────────────────────────────┬────────────────────────────────────┘
                                             │
┌────────────────────────────────────────────┴────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐ │
│  │ Datasets           │  │ Experiments        │  │ App / sandbox checkpoints  │ │
│  │ auto-version+tag   │  │ Braintrust: snap-  │  │ SWE logs/run_evaluation    │ │
│  │ indefinite retain  │  │  shot; not playgrd │  │ Gaia2 --output_dir; split  │ │
│  │ promote-from-trace │  │ pin baseline id    │  │  run vs `are-benchmark     │ │
│  │ = longer legal rec.│  │ LANGSMITH_TEST_    │  │  judge` (replay judge)     │ │
│  │                    │  │  CACHE = scorers   │  │ (run_id, instance_id) cache│ │
│  └────────────────────┘  └────────────────────┘  └────────────────────────────┘ │
└────────────────────────────────────────────┬────────────────────────────────────┘
                                             │
┌────────────────────────────────────────────┴────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ Audit WORM  │  │ Metrics      │  │ Trace spans │  │ Usage (authoritative) │  │
│  │ example_id, │  │ coverage %,  │  │ OTLP /      │  │ prompt/out/reasoning  │  │
│  │ eval_id+ver,│  │ pass@k,      │  │ OpenInf.    │  │ cache_read/write      │  │
│  │ prompt_hash,│  │ pass^k, F1,  │  │ LLM/AGENT/  │  │ Docker minutes        │  │
│  │ score,      │  │ $/task, TTFT │  │ TOOL/RETR/  │  │ judge tokens          │  │
│  │ rationale,  │  │ e2e, time-to-│  │ EVALUATOR   │  │ LCU / Engine ticks    │  │
│  │ dataset_ver │  │ score; p50/  │  │             │  │ weekly judge USD      │  │
│  │             │  │ 95/99 labeled│  │             │  │                       │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

Two request shapes share policy and telemetry and **must not share a clock**.

**A. Offline experiment (harness clock).**

1. **Ingress.** CI or Playground starts an experiment: `dataset_version` (LangSmith auto-versions; tag for CI), `agent_version`, `scorer_version`, `num_repetitions`, `max_concurrency`. Correlation id = `tenant:experiment_id`. Default `max_concurrency=0` (langsmith 0.2.0 changed this from unlimited) — unset concurrency looks “resilient” because it is accidentally serial.
2. **Policy.** Redact PII **before** the example is sent to the agent or the judge. Masking all I/O makes offline eval impossible; tokenize/mask PII but keep task structure; keyed mapping lives in *your* vault if replay needs the real email. Judge prompts receive **already-redacted** text or you exported PII to a second model vendor. Tool RBAC: eval MCP allowlist only; no production write APIs; separate IdP clients for CI vs prod (CI bots must not inherit user refresh tokens).
3. **Route.** Offline path. Pin user-sim model+prompt for τ-class work (never silently upgrade). Snapshot or mock live web in CI (GAIA / BFCL search); live only in nightly. SWE: ephemeral Docker at `base_commit`; gold `test_patch` stays hidden.
4. **Execute (data plane of the job).** Target fn / agent loop. Optional disk cache (`LANGSMITH_TEST_CACHE` / VCR) is for **scorer iteration**; poisonous if you think you re-measured the agent. SWE-bench caches by `(run_id, instance_id)` — same `run_id` + different diff **will not re-run**. New `run_id` when the patch changed.
5. **Score (sidecar, still on the job clock, not the user clock).** Code oracle first (tests, DB state, AST, schema). Then LLM-as-judge / pairwise / human queue. Gaia2 splits **run vs `are-benchmark judge`** so you can replay judging without re-running agents. Braintrust scorer scope: span vs trace vs group.
6. **Persist.** Immutable experiment. Compare against a **named baseline experiment**, not “last week’s vibe.” Failing traces promote back into the dataset (copy into a longer-lived legal record — datasets outlive 14d traces).
7. **Gate.** Dual oracle: binary hard gate AND soft rubric. Coverage % of scored examples is a first-class NFR. Unscored ≠ passed. `error_handling: log | ignore`; infra failures are a **bucket**, not silent unresolved.

**B. Online live request (user SLO clock).**

1. **Ingress.** User request. Gateway stamps correlation id, RPM/TPM, tracing project.
2. **Serve.** Agent loop on the product path. Spans: OpenInference `LLM` / `AGENT` / `TOOL` / `RETRIEVER`. LangSmith: one *trace* = one application execution; child *runs* = LLM/tool/retriever; *threads* = multi-turn. Hard reject after **25,000 runs per trace**. Flattened attributes: `llm.model_name`, `llm.token_count.prompt`, `tool.name`, `retrieval.documents`, `input.value` / `output.value`.
3. **Return to user.** TTFT / e2e SLO is **this path only**. Do not await a judge.
4. **Sample and score asynchronously.** Attachment sampling_rate (Braintrust API example: `1` = 100%; production default should be 1–5% plus spend cap). Score spans record **after** the root span. Time-to-score answers “will scores be five minutes late?”, not “is chat fast?”
5. **Retention event.** LangSmith online eval / rules **auto-upgrade** matching traces to extended (0.50¢, 400d) unless you opt out. That upgrade is an auditable retention event and a bill.
6. **Closed loop.** Topics (Braintrust, daily cluster after ≥100 facet summaries) + annotation queues feed the next dataset version.

**Interview talking point:** “Eval is a distributed system with a control plane (harness, CI, spend caps), a data plane (traces, datasets, PII), and an untrusted sidecar (judges, MCP). Dual oracles, versioned datasets, coverage SLOs — never a naked percentage.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Task success — pass@k, pass^k, binary oracles, partial credit

**pass@k** (Chen et al., Codex / HumanEval, arXiv:2107.03374). Functional correctness, not BLEU. Generate \(n \ge k\) samples per task, count \(c\) that pass unit tests, report the unbiased estimator

\[
\operatorname{pass@}k = \mathbb{E}\!\left[1 - \frac{\binom{n-c}{k}}{\binom{n}{k}}\right]
\]

Naive \(1-(1-\hat p)^k\) is **biased**. If \(n-c < k\), the binomial is 0 and pass@k = 1. Original paper: \(n=200\), \(k \le 100\), 164 hand-written Python problems, mean 7.7 tests/problem. pass@1 ≈ “one sample works”; pass@k ≈ “something in a candidate set works *if you can filter*.” Agents inherit this: best-of-N with a unit-test oracle is pass@k; best-of-N with an LLM judge is **not** the same estimator.

**Complexity.** Per task, given \((n,c,k)\): \(\Theta(k)\) product in ratio form \(\prod_{i=0}^{k-1}(n-c-i)/(n-i)\), or \(\Theta(1)\) with log-gamma. Sampling \(n\) trajectories is the real cost: \(\Theta(n)\) agent loops, not the estimator.

**pass^k** (Yao et al., τ-bench, arXiv:2406.12045). Probability that **all** \(k\) independent trials succeed, averaged over tasks. Reliability, not “at-least-one.” Anthropic’s think-tool post contrasts them: pass@k is optimistic, pass^k is pessimistic. Original τ-bench: even GPT-4o-class function-calling agents succeed on **<50%** of tasks; retail **pass^8 < 25%**; airline pass@1 **35.2%** for GPT-4o. Anthropic (think-tool, 2025) on τ-airline with Claude 3.7-era configs: baseline pass^1 **0.332** → “Think”+prompt **0.584**; pass^5 **0.100** → **0.340**. Retail “Think” (no extra prompt): pass^1 **0.812**, pass^5 **0.626**. The gap between pass^1 and pass^5 *is* the product risk.

**On Randomness in Agentic Evals** (arXiv:2602.07150): 60,000 trajectories, 25.58B tokens, 1.88M tool calls, three models × two scaffolds × two temperatures. Trajectories diverge in the first few percent of tokens; gaps up to **24.9 percentage points** between pass@k (best-case envelope) and pass^k (worst-case). A 31%→33% single-run “win” is often sampling noise. Vestige (ASSERT-KTH) analyzes pass@k / pass^k, temperature-0 non-determinism, nano-agent / r2e-gym / Claude Code formats. T=0 still diverges: pin seed **and** treat residual as aleatoric; do not claim determinism.

**SWE-bench** (Jimenez et al., ICLR 2024, arXiv:2310.06770). 2,294 GitHub issues from 12 Python repos. Oracle is **execution**: apply `model_patch` in Docker at `base_commit`; **FAIL_TO_PASS** must all pass; **PASS_TO_PASS** must not regress. Gold `test_patch` is hidden. Docker harness since 2024-06-27. Splits: Lite 300; **Verified** 500 (OpenAI+authors, 2024-08); Multimodal; Multilingual 300 / 9 languages / 42 repos.

**Verified is no longer a frontier metric.** OpenAI (2026): all tested frontier models could reproduce the **human gold patch** verbatim; they stopped reporting it. Independent work (arXiv:2512.10218): Claude models localize files **3×** better on Verified than on BeetleBox / SWE-rebench given issue text only — contamination signal. **SWE-bench Pro** (Scale; arXiv:2509.16941): 1,865 problems, 41 repos, public 731 / held-out 12 repos / commercial 18; mean gold patch **107.4 LOC across 4.1 files**. Public-split frontier pass@1 moved **23.3% → 80.3% in eight months**. July 2026 audit: automated pipeline flagged **200/731 (27.4%)** broken; human campaign **249 (34.1%)**; OpenAI estimates **~30%** broken (over-strict tests, underspecified prompts, low coverage) and **retracts** the Pro recommendation. Quote **named split + named scaffold + date + contamination status**. Do not treat aggregator “96% Verified” pages as an SLO.

**GAIA** (Mialon et al., arXiv:2311.12983). 466 questions; 166 with answers (dev); 300 held for the leaderboard. Quasi-exact match after type-tied normalization. Human **92%** vs GPT-4+plugins **15%**. By 2025 HF/Meta judged L1/L2 near-saturated — hence Gaia2.

**Gaia2 + ARE** (Froger et al., arXiv:2509.17158; HF blog 2025-09-22). Read-and-write, **asynchronous** environment (time flows while the agent thinks — unlike paused τ-bench / SWE-bench). 800 unique human-annotated scenarios × 10 universes × **101 tools**; Gaia2-mini **160**; Agent2Agent + Noise add 320 → **1,120** total. Seven equally weighted capabilities. Judge mix: **Llama 3.3 Instruct 70B** (soft args) + exact-match (hard args); write-actions compared to oracle write-actions. Uniform ReAct, T=0.5, 16k gen cap. Validation is the public leaderboard; **test set is private**. Paper plots **score vs $ and vs time**; budget-scaling curves **plateau**. `--scenario_timeout 300` is a **harness NFR**, not a product SLO.

**τ-bench family (Sierra).** τ (Jun 2024): success = **final DB state == annotated goal** (no LLM judge on the pass/fail bit). τ² (Jun 2025, arXiv:2506.07982): **dual control** — user also has tools. τ³ (2026): banking knowledge (~700 docs), **voice full-duplex**, 75+ task fixes; original GitHub repo marked outdated. Anthropic Opus 4.6 system card: **τ² Retail 91.9%**, **Telecom 99.3%**. ⚠️ Aggregator sites mix user-simulators and judges — do not compare pass^1 across rows without the harness footnote.

**Binary vs partial credit.** A 90%-right patch that fails one FAIL_TO_PASS is a **0**. Partial credit belongs in **rubrics** (HealthBench: weighted criteria, score = points met / max) and **process metrics** (tool F1, faithfulness fraction, step-level PRM). Rubric-only ship gates ship “pretty wrong.” Binary-only gates on open-ended chat ship “correct but hostile.” Dual-oracle is the enterprise default: **hard gate + soft score**.

### 2.2 Trajectory — process vs outcome, traces, offline replay

**Outcome eval** asks “did the world end in the goal state?” **Process eval** asks “were the steps legal, efficient, and policy-faithful?” τ-bench and SWE-bench are outcome-first. Gaia2 scores **write actions and their arguments** against an oracle trace — process-shaped outcome. BFCL multi-turn is state-transition. LLM-as-judge on the full trace is process. Appen / Poolside (2026): publish **full trajectories**, not just aggregates; TRACE-style taxonomies test whether judges detect reward exploits. Chain-of-thought as evidence is weak: models omit the hint they actually used.

**Trace data model (OpenInference).** Required `openinference.span.kind`. Evaluator spans are first-class. This is the portable **data plane** if you do not want LangSmith-proprietary run trees as the source of truth. Phoenix: pull spans to pandas → `run_evals` / `llm_classify` → write labels back. Replay of *judging* = re-score a DataFrame, not re-call the agent.

**Process metrics that belong on the trace, not the leaderboard:** step count, unique tools, retry rate, policy-violation spans, tokens-to-success, wall-clock-to-success, cache hit rate. Gaia2 is explicit: a correct answer after thousands of tokens / hours is **dominated** on the cost-normalized Pareto.

**Offline DAG (all three vendors):** (1) Curate dataset (manual 10–20, then production failures, then synthetic). (2) Pin a dataset version. (3) Run target with `num_repetitions`, `max_concurrency`, optional disk cache. (4) Score: code | LLM-as-judge | pairwise | human. (5) Compare experiments; promote failing traces.

### 2.3 Tool accuracy — BFCL AST, parameters, selection F1, hallucinations

**BFCL** (Patil et al., ICML 2025). AST matching + state-transition — **not** an LLM judge on the classic tracks. Numbers are deterministic. V1: simple / parallel / multiple. V2: live enterprise/OSS functions. V3: multi-turn / multi-step, missing params, long context. **V4** (2025, last leaderboard note 2026-04-12): Overall = **Agentic 40% + Multi-Turn 30% + Live 10% + Non-Live 10% + Hallucination 10%**. Reproduce with `bfcl-eval==2025.12.17` (commit f7cf735). Subcategories: unweighted average inside a bucket (cannot farm a huge bucket); Live is **weighted** by case count.

**Hallucinated tools = irrelevance track.** V4: Non-Live Irrelevance **240** + Live Irrelevance **882** (unweighted avg, 10% of overall). Relevance (18 live) is listed; format-sensitivity (26 configs × 200 = 5,200) is **non-scoring**. Abstention is first-class: calling a tool that was never on the menu is a fail, not partial credit.

**Parameter correctness.** AST checks name, types, and structure; executable tracks check runtime. Multi-turn “missing parameter / missing function” splits punish invented args and skipped clarification. **[inferred] enterprise F1:** gold tool sequence as a set (or ordered list); **precision** = fraction of emitted calls that are allowed+correct; **recall** = fraction of required calls emitted; **F1** of that set. Do not use BLEU on JSON. Do not LLM-judge parameter equality when a JSON schema exists. Complexity: \(\Theta(|P|+|G|)\) set hash of `(name, canonical_json(args))`.

**V4 Web Search** (blog 2025-07-17): 100 human multi-hop questions, DuckDuckGo + fetch, snippet vs no-snippet (200 scored entries). Disable search → accuracy collapses — models are not secretly answering from params.

**Tool selection vs SWE/τ/GAIA.** BFCL asks “right function, right args.” τ asks “right DB mutation under policy.” SWE asks “tests green.” A model can ace BFCL Live AST and fail τ policy. Ship gates need **both** a function-calling unit (BFCL-style AST on *your* schema) and a **stateful** scenario pack (τ-style).

### 2.4 Quality — judges, pairwise, rubrics, humans, faithfulness

**LLM-as-judge** (Zheng et al., MT-Bench / Chatbot Arena, arXiv:2306.05685). GPT-4 judge vs humans: **>80%** agreement, matching human–human. Biases: **position**, **verbosity**, **self-enhancement**, weak math. Mitigations: swap order and treat flips as ties; length-normalize; cross-family judges. TrustJudge (arXiv:2509.21117) still uses double-order pairwise as the position-bias baseline. Documented swings: position **~10–15 pt**; verbosity **15–30 pt** (Wang et al. 2023); self-preference **10–25%**. If that judge is the **reward**, RL will farm it.

**G-Eval** (Liu et al., EMNLP 2023, arXiv:2303.16634). Auto-generated CoT evaluation steps + form-fill; GPT-4 Spearman **0.514** on summarization vs humans, beating BLEU/ROUGE/BERTScore. Authors flag **bias toward LLM-generated text**. Token-probability-weighted scores (original method) are more stable than greedy integer scores — most vendor UIs skip this and just ask for an int.

**Rubric eval (HealthBench, OpenAI, arXiv:2505.08775).** 5,000 multi-turn conversations; **262** physicians / **60** countries; **48,562** unique criteria; median **11** criteria/example (range 2–48). Axes: accuracy, completeness, context awareness, communication, instruction-following. Grader: **GPT-4.1**, physician-validated (PMC viewpoint: macro F1 **0.71** on themes). Score = weighted points met / max. o3 **~60%**; GPT-4o **32%**; GPT-3.5 Turbo **16%**; GPT-4.1 nano beats GPT-4o at **25×** lower cost. HealthBench Hard: then-top **32%**. Template for enterprise rubrics: **itemized, weighted, conversation-specific**, not a single 1–5 vibe. Judge cost is not rounding error: median 11 × 5,000 = **55k grader calls** per model.

**Pairwise.** Use when absolute scores are unstable but A vs B is easy. Always run both orders.

**Human raters.** Annotation queues set **thresholds and calibration**, not 100% of volume. Hamel Husain (linked from LangSmith concepts): *look at traces first*.

**Faithfulness (RAGAS, Es et al., EACL 2024, arXiv:2309.15217).** Not “true in the world” — **entailed by retrieved context**. Extract atomic statements → NLI vs context → fraction supported. WikiEval: RAGAS faithfulness **~95%** agreement with humans vs direct GPT scoring **72%**. ⚠️ A faithful answer can still be the wrong answer if retrieval missed the doc. Pair with answer relevance + context precision/recall.

**SimpleQA (Wei et al., OpenAI).** Grade **correct / incorrect / not attempted**. F-score balances attempted-correct vs hallucinations. Grader is itself an LLM (A/B/C regex; default C = not attempted). Claude-class models attempt less → similar F at lower accuracy.

### 2.5 State machines, complexity, invariants

**Eval-job state machine (control plane).**

```
  ADMIT ──▶ RUN_AGENT ──▶ RECORD_TRACE ──▶ SCORE_CODE ──▶ SCORE_JUDGE ──▶ AGGREGATE ──▶ PERSIST
    │            │              │                │              │              │
    │            │              │                │              ▼              │
    │            │              │                │         SKIP_UNSCORED       │
    │            │              │                │         (breaker / 429)     │
    │            ▼              │                ▼                             │
    └──────── HARNESS_ERROR ────┴──────── INFRA_BUCKET (≠ unresolved fail) ────┘
```

Judge sidecar is a nested machine: `CLOSED` (score) → `OPEN` (skip or fail job) → `HALF_OPEN` (probe). LangSmith evaluator spend cap **pauses the evaluator**; **agent traffic continues**; skipped runs **not backfilled**; in-flight overshoot allowed. That is a published **judge circuit breaker**. Coverage monitors must fire or a tripped breaker paints quality green.

**Dual-oracle ship gate (algorithm).** Let \(T\) = mean task success (binary oracle or pass^k on a canary), \(F\) = mean tool F1, \(C\) = mean $/example, \(L\) = latency percentile on **prod spans** (not harness wall), \(\gamma\) = judge coverage. Ship iff \(T \ge T_{\min} \land F \ge F_{\min} \land C \le C_{\max} \land L \le L_{\max} \land \gamma \ge \gamma_{\min}\). Rubric \(Q\) is informational unless the product is open-ended *and* a binary safety gate already passed. \(Q\) must not override \(T=0\).

**Complexity (harness).** \(n\) examples, \(r\) repetitions, concurrency \(N\), judge criteria \(K\): agent calls \(\Theta(n r)\); rubric judges \(\Theta(n r K)\) (HealthBench-shaped); wall-clock **[inferred]** \(\approx (n r / N)\cdot \mathbb{E}[t] +\) retry/timeout tail. Gaia2 default timeout **300 s/scenario** puts a **5 min** floor on that example’s e2e regardless of TTFT. Ingest: Plus **500k events/hour**, **5.0 GB/hour**; 25k runs/trace is a hard reject.

**Invariants worth stating in an interview.**

1. Harness ≠ product. Named (split, scaffold, date, contamination).
2. Unscored ≠ passed. Coverage % is an NFR.
3. Outcome oracle ≠ process oracle. Dual-oracle default.
4. Judge is a subprocessor and a sidecar — never on the SLO path.
5. Pin `dataset_version`, `evaluator_version`, user-sim, `run_id`. Without `evaluator_version` you cannot explain a metric jump after a rubric tweak.
6. Result caches are checkpoints **and** footguns (SWE `(run_id, instance_id)`; `LANGSMITH_TEST_CACHE` for agent calls).
7. Abstention: hallucinated tools are fails (BFCL irrelevance), not partial credit.
8. pass@k ≠ pass^k; 24.9 pp envelopes exist; 31%→33% is often noise.

---

## 3. Token Economics & NFR Analysis

Eval cost = **(agent tokens + tool I/O + sandbox time) × dataset × repetitions × (1 + judge tokens × criteria) + platform traces**. The eval bill is a **second product**. Agent-under-test tokens usually dominate judges; HealthBench and Gaia2’s 70B write-judge are the counterexamples.

Formula (USD list, 2026-08-21 SKUs):

\[
C = n \cdot \frac{T_{\mathrm{in,miss}} P_{\mathrm{miss}} + T_{\mathrm{in,hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}}{10^{6}}
\]

\(T_{\mathrm{out}}\) **includes thinking / reasoning tokens** (OpenAI: billed as output, occupy context, often **invisible**; `usage.output_tokens_details.reasoning_tokens`). OpenAI recommends reserving **≥25,000** tokens for reasoning+output when first using reasoning models. `max_output_tokens` can expire **before any visible token**. Changing Anthropic thinking/effort **invalidates** message (and often tool/system) caches — a sweep over `budget_tokens` / effort is a **cache-busting** cost multiplier. Thinking blocks cannot take `cache_control` directly; they can be cached as prior-turn content and then count as cache-read input.

### 3.1 `$ per 1k` — eval cost and product cost (all **[inferred]** unless SKU)

**LangSmith platform SKUs** (langchain.com/pricing + usage-and-billing, 2026-08-21):

| Meter | Published number |
| --- | --- |
| Developer | $0/seat, **5k** base traces/mo included |
| Plus | **$39**/seat/mo, **10k** base traces/mo, then PAYG |
| Base trace | **0.05¢** ($0.0005); **14-day** retention |
| Extended trace | **10×** base → **0.50¢** ($0.005); upgrade **0.45¢**; **400-day** |
| Experiments | runs created at **extended** retention by default |
| Online eval / rules | **auto-upgrade** matching traces to extended unless opt-out |
| LCU | **$1.50**; Engine run **~5–30 LCU [vendor FAQ estimate]** → **~$7.50–$45**/tick; Engine every **6h** |
| LSU | **$1.00** |
| Tuned Evaluators | **0.01 LCU** per successful Perceived Error eval; skipped/failed not billed |
| Plus ingest | **500k events/hour**, **5.0 GB/hour**; 25k runs/trace |
| Evaluator spend cap | weekly USD, resets **Monday 00:00 UTC**; in-flight may **overshoot** |

LangSmith spend ≠ provider invoice. Gateway Credits and Tuned Evaluators are extra meters.

**[inferred] platform-only, 1k experiment runs (Plus, extended):** \(1000 \times \$0.005 = \$5\). Same 1k as **base** online traces with eval opt-out: \(1000 \times \$0.0005 = \$0.50\). At 100% online judge without opt-out you pay the upgrade **and** the judge API.

**Published model SKUs used below.** Claude Sonnet 4.6: **$3 / $15** per MTok in/out; 5-min write **$3.75**, 1h write **$6**, read **$0.30**. Opus 4.6/5 line: **$5 / $25**; 5-min write **$6.25**, 1h **$10**, read **$0.50**. OpenAI GPT-5.6+: cache **read 0.1×**, **write 1.25×**; prompts ≥ **1,024** tokens; `prompt_cache_options.ttl` **30m**. Published short-context example: **gpt-5.6-sol** input **$5**, cached **$0.50**, writes **$6.25**, output **$30** per 1M.

**[inferred] $ per 1k judge-only calls** on Sonnet 4.6, reference loop 2,000 input + 200 output, no cache:

\[
1000 \times (2000\times 3 + 200\times 15)/10^6 = \$9
\]

Stable rubric prefix cached after first write (0.1× on 1,800 prefix tokens, 200 unique): ~**$1.1** input-side after warmup **[inferred]**.

**[inferred] $ per 1k eval runs, all-in τ-like (not a quote):** 1k tasks, 1 trial, Sonnet 4.6 agent ~8k in / 1.5k out with 70% cache read on 6k prefix:

\[
1000\times[(0.3\times2000+0.7\times6000\times0.1)\times 3 + 1500\times 15]/10^6 \approx \$26
\]

plus user-sim of similar order + **$5** LangSmith extended + **$9** if you add a trace-judge. **pass^4 multiplies agent+sim by ~4.** Reliability eval is a **budget line**, not a unit test: **[inferred] ~$208** agent+sim for 1k tasks × 4 trials, before platform and judges.

**Product cost (serving) is a different meter.** The same τ-shaped support agent in production pays agent+tools on **every live ticket**, plus 1–5% sampled judges, plus traces (base 0.05¢ or extended 0.50¢ if online eval upgrades). **[inferred]** 1k live tickets at the same 8k/1.5k Sonnet 4.6 cache mix ≈ **$26** LLM + **$0.50** base traces (opt-out) or **$5** extended, + \(0.05\times \$9 \approx \$0.45\) judges at 5% sample. Product p95 is the user path; the $9/1k judge SKU is the sidecar. Do not quote harness Docker-pull time as COGS.

| Workload | What to meter | Cache advice |
| --- | --- | --- |
| SWE-bench-class | Agent tokens + **Docker minutes** + optional patch-rerank | Stable system+tools prefix; per-instance issue at the tail |
| τ-bench-class | Agent + **user-sim** LLM + tools; × trials for pass^k | User-sim and agent **must not** share a cache key |
| Gaia2 | Agent + **101-tool system prompt** + 70B judge on writes | System+tools are the cache; scenario body is not |
| Rubric (HealthBench-style) | 1 completion + **K** grader calls | Cache the rubric template; bind example-specific criteria after the breakpoint |
| Online 1% sample | Live tokens already paid; add judge × 0.01 | Separate `prompt_cache_key` for judges vs agents |

### 3.2 Latency — TTFT, e2e, p50/p95/p99 (label **[inferred]**)

⚠️ Vendors do not publish *your* p99. Artificial third-party TTFT (toolbrain.net, May 2026) reported Claude cache-hit TTFT **2.89s → 1.36s (53%)** on one Opus 4.7 setup; anecdotal, not an SLO. No public “p99 of LangSmith evaluate()” — only ingest 429s (Plus 500k events/hour).

| Metric | Definition | Eval harness | Production SLO |
| --- | --- | --- | --- |
| **TTFT** | Time to first visible token | Often unused; batch APIs hide it | Chat UX; cache hits cut TTFT |
| **e2e** | Request start → final answer / tool-loop done | Job time / dataset; dominated by slowest example unless bounded | User-facing; agent loops are **multiples** of single-call e2e |
| **p50/p95/p99** | Latency distribution | Report **per example** and **per experiment**; p99 of a 50-item set is noise | Need volume |
| **Time-to-score** | Root span end → judge span end | Braintrust timeline (optional) | Must not gate the response |

**Do not use eval-harness wall time as an SLO.** Harness e2e includes dataset load, Docker pull, judge queues, retries, and `max_concurrency`. Production p95 is the user path only. LangSmith and Phoenix attach latency/token/cost on **spans** — that is the SLO telemetry.

Working envelopes (architecture mapping; **[inferred]** — sequential-sum / documented caps, not a vendor SLO):

| Percentile | Interactive chat (prod spans) | Agent tool-loop (prod) | Offline experiment wall | Online time-to-score |
| --- | --- | --- | --- | --- |
| p50 | **[inferred]** cache-hit first chunk; anecdotal ~1.4 s on one Opus cache-hit setup | **[inferred]** N × single-call e2e | **[inferred]** \((n/N)\times\) mean example | **[inferred]** seconds–low minutes if judge queue healthy |
| p95 | **[inferred]** 1.5–3× p50 on mixed prefill + thinking preamble (same heuristic as hosted chat; not a LangSmith SLO) | **[inferred]** retry + slow tool; cap rounds | **[inferred]** timeout tail; Gaia2 **300 s** floor per hung scenario | **[inferred]** spend-cap / 429 delay; scores late, users unaffected |
| p99 | **[inferred]** raise client timeout if reasoning `max`; do not block UX on judge | **[inferred]** kill-switch / escalate | Plus ingest **429** (500k evt/h) or 25k-run reject | Unscored backlog; coverage SLO fires |

| Tier | Mitigations |
| --- | --- |
| p50 | Prefix cache on system+tools; streaming on the **product** path; batch/Flex APIs for harness, not Playground |
| p95 | `max_concurrency` set on purpose; scenario timeouts (Gaia2 300 s is a cap, not a goal); sample online judges 1–5%; opt out of extended-retention upgrade |
| p99 | Disagg product serving from eval jobs (eval Docker/judge must not share the interactive pool); judge breaker fail-open online; fail-closed CI; never put time-to-score on the user SLO |

**[inferred] eval p95 wall:** with `max_concurrency=N` and i.i.d. example times, experiment wall-clock ≈ \((n/N)\times\) mean + tail from retries/timeouts.

### 3.3 Throughput and back-pressure

Harness throughput is \(\min(N_{\mathrm{concurrency}}, \mathrm{provider\ TPM}, \mathrm{Docker\ slots}, \mathrm{judge\ TPM}, \mathrm{ingest\ 500k\ evt/h})\). LangSmith 429 classes: 1-min ALB (e.g. POST `/runs*` **5000/min**), hourly events/bytes, monthly unique traces on unpaid Developer (5k). SDK batches up to 100 runs. Retry with jitter; **saturation ≠ retryable**.

Back-pressure design:

1. Admit an experiment only if judge breaker is closed/half-open **or** the job is allowed to run unscored (online). CI ship gates admit only if coverage SLO remains achievable.
2. Honor `Retry-After` on provider 429; do not retry SWE Docker on a poisoned `run_id` cache.
3. Online scoring load-sheds by **sampling_rate**, not by blocking the user. Braintrust `Reporter.reportRun → bool` is a quality gate (non-zero CI exit), not a latency breaker.
4. pass^k multiplies QPS by \(k\) (agent+sim). Budget reliability eval as a nightly wave, not a per-PR 8-trial τ pack on the full set.
5. Plus 5.0 GB/hour: traces with tool payloads and screenshots will hit **bytes** before event count. Strip or redact before ingest.

Worked admission **[inferred]**: 1k τ-like examples, `num_repetitions=4`, `max_concurrency=20` → 4k agent loops + 4k user-sim loops. At ~8k+1.5k tokens, raw TPM ≈ \(8000\times(8000+1500)/60\) if the job finishes in one minute — it will not; at 20 concurrent loops the in-flight TPM is \(20 \times 9500\) ≈ **190k tok/s** peak if they overlap, which no interactive tier should share with production. **Isolate eval TPM from serving TPM.**

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

⚠️ Research publishes no numeric RPO/RTO for LangSmith/Braintrust/Phoenix. Architecture mapping:

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | Control plane 99.9% for CI kickoff; **judge availability is not product availability**. Coverage SLO (e.g. ≥95% of sampled traces scored) is the judge NFR | Silent judge outage looks like quality stability |
| RPO | Datasets: **0** (pin version; indefinite retention). Traces: 14d vs 400d by SKU. Experiments: immutable snapshot. Prompt/KV cache: **not** an eval RPO | Treating 14d SaaS traces as the only gold set loses the legal record; promote failures into datasets |
| RTO | Replay judge without re-running agents (Gaia2 split; Phoenix DataFrame). Agent re-run: new `run_id`, never reuse SWE cache for a new patch. Online: skip score, serve user | Fast CI vs valid measurement |
| Consistency | Code oracles: deterministic given env. Judges: T=0 + structured output still aleatoric; majority-of-3 on ship gates. User-sim must be frozen | Cannot have bit-identical LLM retry at T>0 |
| Compliance | PCI: LangSmith **prohibits cardholder data**. Health/finance eval sets may need **self-hosted Phoenix or Braintrust hybrid**, not SaaS traces. Judge = subprocessor. AES-256 at rest, TLS 1.2+ (LangSmith). Engine / Topics: ZDR per analysis task (docs reviewed 2026-06-25) — still a subprocessor; mask at SDK first | SaaS convenience vs residency |
| Cost vs latency | Extended traces 10× base; 100% online judge; pass^4 × 2 LLMs; reasoning without cap | Cheap pass@1 CI that flakes |
| Consistency vs availability | Sticky cache in eval vs cold prod; fail-open online vs fail-closed CI | Warm-cache eval, cold-cache prod is leakage |

**Explicit trade-offs (research §6.5).**

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| Task success | pass@1, n=1 | pass@1 with 3 reps + CI delta | pass^k canary + nightly pass@k |
| Oracle | LLM-as-judge 1–5 | Code + judge | Hidden tests / DB state + human audit |
| Traces | SaaS, 14d, 1% sample | SaaS extended on failures only | Hybrid/self-host, mask at SDK |
| Tools eval | “Did it call a tool?” | Schema validate args | BFCL-style AST + irrelevance tests |
| Judge | Same family as agent | Cross-family, swap pairwise | Rubric + expert calibration set |
| MCP | STDIO secrets in CI | OAuth, audience per server | No prod MCP in eval; simulators |
| Cost control | Unlimited online judge | Sample + weekly spend cap | Fail-closed CI; fail-open online with coverage SLO |
| Latency KPI | Harness wall time | Span p95 on prod traces | Separate TTFT / e2e / time-to-score |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Kafka

Eval jobs are **stateful workflows**: dataset pin, per-example activities, judge second pass, promotion into datasets. Playground is mutable; **experiment is the snapshot** (Braintrust).

**Temporal.** Workflow id = `tenant:eval:dataset_version:agent_version:experiment_id`. Activities = (redact example), (run agent / user-sim), (sandbox / Docker), (code score), (judge), (human queue), (promote trace). Replay reconstructs control state; activities must be **idempotent** and return a recorded `Trace` — never re-sample the agent inside a replay-unsafe closure. Non-determinism (temperature, live web, Docker flakes) lives **inside** the activity. Continue-As-New at history bounds (agent traces are large; LangSmith 25k runs/trace is a cousin cap). Compensating action for a bad experiment = mark superseded, do not delete the snapshot. SWE compensating action = new `run_id`, not “same cache, new diff.” Gaia2 compensating action = re-run `are-benchmark judge` only.

**Kafka.** Topics per tenant-shard: `eval.intents`, `eval.traces`, `eval.scores`, `eval.dlq`. Produce **intent** (`example_id` + idempotency key) **before** the agent/sandbox side effect (outbox). Score workers consume traces, write feedback. Compaction on `experiment_id`. Poison (unparseable payload, identical hash crashing N times, 25k-run overflow) → DLQ; do not block the partition. Online path: product traces land on `prod.traces`; a sampler copies into `eval.scores` — **no** synchronous request/reply on the user span.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB SWE traces or Kafka lag SLOs for OpenInference buses. Treat Temporal/Kafka here as the enterprise mapping of experiment immutability + OTLP ingest.

**Resume keys.** Dataset version + example id + repetition index. SWE: `(run_id, instance_id)` is a checkpoint **and** a footgun. Phoenix: span id in your collector. Promotion: trace → dataset is a **copy**, not a pointer that dies at 14d.

### 4.2 Failure taxonomy

| Class | Eval symptom | Handler |
| --- | --- | --- |
| Transient | Agent 429/5xx; LangSmith 1-min ALB 429; judge 429; Docker pull flap; T=0 residual jitter | Full-jitter retry **idempotent** reads; honor `Retry-After`; `num_repetitions`; do not retry irreversible sandbox writes without idempotency key |
| Permanent | Illegal tool schema; unsupported judge model once a LangSmith spend limit exists (must be OpenAI/Anthropic/Gemini with a price row); Anthropic cache-bust by design when effort changes | Fail the example into a labeled bucket; fix config; do not count as task-fail |
| Poison pill | Same `(run_id, instance_id)` hiding a new patch; committed `LANGSMITH_TEST_CACHE` for agent calls; live MCP prod writes; 25k-run traces; gold `test_patch` leaked into prompt | New `run_id`; never auto-replay; DLQ; simulators only |
| Semantic | Reward hacking (verbosity, fake CoT, judge-steering, environment tampering / edit tests); position/length/family bias; contamination (Verified gold-patch regurgitation; Pro 23%→80% then ~30% broken); user-sim quoting the policy; warm cache in eval / cold in prod; rubric style without facts | Hidden tests + code oracles; cross-family judges; swap pairwise; canary strings; report cache hit rate in **both** planes; item-level facts on rubrics; human spot-check of high-score traces |

**Why evals flake (research §3.1).** Agent sampling → pass@1 jitter / 24.9 pp envelopes. User simulator → τ pass^k collapses if unpinned. Docker/network → SWE “instances with errors” — distinguish harness crash vs unresolved. Live web → snapshot in CI. Judge stochasticity → T=0, structured output, double-order pairwise, majority of 3 on ship gates. Dataset drift → pin version/tag. Result caches → false stability.

**Operational modes that look like quality.** Silent judge outage → dashboards freeze at last value. Extended-retention surprise bill → online eval default-on. Engine / Topics reading PII → mask at SDK. CI eval using live MCP prod → destructive writes. pass@1 CI gate on agents → flaky red/green (use repetitions + pass^k on a **small** canary; full pass@k nightly).

**Reward-hacking hierarchy** (surveys 2026): verbosity/sycophancy → fake CoT → **judge-steering** (format, injection) → **environment tampering** (edit tests, mock APIs, exfiltrate gold). Shi et al. 2024: optimization-based injection of judges. Tong et al. 2025: poisoned judge-training data → backdoors. Defenses in production write-ups: trajectory publication (Poolside); mix **code oracles + judges**; cross-family judges; adversarial judge prompts; human spot-check; forbid known shortcuts in the rubric **and** in the environment (the instruction is not a security boundary). Hidden tests exist *because* models will pattern-match visible tests. Gaia2 private test set exists for the same reason.

### 4.3 Circuit breaker and fallbacks

Per downstream (judge provider, trace ingest, Docker pool, live search):

- **Closed:** traffic flows; consecutive failures or error-rate window trip to open.
- **Open:** fail fast; start recovery timer (e.g. 30 s). **Online:** skip score, flag unscored, user path continues (fail-open). **CI:** fail the job (fail-closed). Flex/batch eval can wait.
- **Half-open:** one probe (or `half_open_max`). Success → closed; fail → open.

Published, not folklore:

1. LangSmith **evaluator spend cap** — pauses evaluator on that project/dataset; agent continues; skipped not backfilled; in-flight overshoot.
2. LangSmith **tracing usage limits** — 429 on monthly all-traces or extended-traces; extended cap also blocks retention-upgrading evals/rules.
3. LangSmith **429 classes** — ALB 5000/min `/runs*`, hourly events/bytes, monthly unique traces.
4. Braintrust `sampling_rate` + CI reporter — quality gate, not a latency breaker.
5. Provider 429/5xx — **[inferred]** fail-open online; fail-closed CI.
6. Unsupported judge models once a spend limit exists — config-level breaker.

**Fallback chain:** primary judge (cross-family from the agent) → secondary cheaper grader (HealthBench-style: GPT-4.1 nano **25×** cheaper than GPT-4o on that paper’s result) → **deterministic degrade** (code oracle only; rubric = `unscored`). Deterministic degrade must still emit a structured `ScoreRecord` so aggregators do not crash. Do not fall back from fail-closed CI to “skip and pass.” Do not fall back from simulator MCP to production write APIs. Do not fall back from hidden tests to LLM-as-judge for merge.

⚠️ No vendor publishes “circuit breaker trips/hour” as an SLO. Design for **unscored ≠ passed**.

### 4.4 Zero-Trust MCP, tool RBAC, PII detect→redact→audit, immutable logs

**Zero-Trust MCP for eval tools.** Harnesses increasingly **are** MCP clients (Gaia2/ARE; LangSmith Deployment exposing agents as MCP servers; coding-agent evals calling browsers/tickets). MCP authorization (spec 2025-06-18 onward; tutorial dated 2026-07-28): **OAuth 2.1**, **PKCE (S256)**, Protected Resource Metadata **RFC 9728**, AS metadata **RFC 8414**, resource indicators **RFC 8707** (token audience = that MCP server). **Token passthrough is forbidden.** STDIO transports are **out of** the OAuth spec (env credentials instead). Implicit/ROPC gone; bearer tokens in query strings forbidden. SSRF controls on CIMD URL fetch: AS fetching client metadata must be SSRF-safe.

| Control | Why eval is special |
| --- | --- |
| Audience-bound tokens per MCP server | A “search MCP” used in BFCL-like eval must not accept a token minted for “admin MCP” |
| Separate IdP clients for CI vs prod | CI eval bots should not inherit user refresh tokens |
| Allowlist MCP URLs in the harness | ARE: untrusted MCP = RCE-adjacent |
| No production write APIs on eval MCP | τ-style → **simulators**; SWE → **ephemeral Docker**, not corp Git |
| SSRF-safe metadata fetch | Spec requirement on AS |

**Tool RBAC (least privilege per experiment).** Attach only the tools this dataset is authorized to call. BFCL irrelevance: calling a tool that was never on the menu is a fail. HITL / never-merge: LLM-as-judge on PR description quality; **tests merge**. Do not attach prod refund APIs to a support eval — attach a simulator with a policy cap the **code scorer** checks (`refund ≤ policy cap`; do not LLM-judge arithmetic).

**PII pipeline:** detect → redact **before tokenize / before ingest** → audit placeholder map (hash, never raw).

| Product | Control |
| --- | --- |
| **LangSmith** | Shared-responsibility: **you** filter PII before ingest. SDK anonymizer (regex / Presidio / Comprehend); `LANGSMITH_HIDE_INPUTS` / `HIDE_OUTPUTS`; Gateway PII/secrets redaction (**beta**) — redacts provider **and** trace, but **not** traces that bypass the gateway. Retention 14d/400d is a **privacy control**, not just cost |
| **OpenInference / Phoenix** | `OPENINFERENCE_HIDE_INPUTS/OUTPUTS/MESSAGES/TEXT/IMAGES`, `HIDE_LLM_TOOLS`, `HIDE_EMBEDDING_VECTORS`. Code `TraceConfig` beats env. Hiding inputs also hides tool defs |
| **Braintrust** | Global masking; hybrid so AI data never lands on vendor disk; Topics summarization **reads trace text** — scrub first. Self-hosted Topics: ZDR to the same endpoints |

Pattern: mask PII, keep task structure. Judge sees redacted text.

**RBAC on datasets.** LangSmith: org roles User/Admin on Developer/Plus; Enterprise custom SSO, ABAC, RBAC. Evaluator spend limits require `organization:manage`. Dataset write = production-data write. Phoenix: your SSO in front of the UI; restrict who can `log_evals`.

**Immutable logs.** Minimum audit record **[inferred]:** `(example_id | trace_id, evaluator_id, evaluator_version, model+params, prompt_hash, score, rationale, timestamp, dataset_version)`. Hash-chain WORM; feedback objects `{key, score|value, comment}` — **the comment is the audit trail**. Online eval auto-upgrade is an auditable retention event. Reconstruct: policy snapshot + agent id + sampled trace + code oracle + judge version + human interrupt.

---

## 5. Production Enterprise Code

Stdlib-only eval harness: full-jitter retries, circuit breaker (closed → open → half-open), primary → secondary → deterministic `unscored` / degraded trace, correlation-id JSON logs, PII detect→redact→audit, hash-chained WORM, MCP allowlist, BFCL-style tool F1, pass@k / pass^k, dual-oracle **score aggregator** (task success + tool F1 + cost + latency gate + coverage). Fail-open online / fail-closed CI. Run: `python eval_harness.py`.

```python
#!/usr/bin/env python3
"""Eval harness primitives (stdlib only). Run: python eval_harness.py"""
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
from typing import Any, Callable
from urllib.parse import urlparse

POLICY_VERSION = "eval-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
MAX_TOOL_ROUNDS = 8
COVERAGE_MIN = 0.95


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "experiment_id": getattr(record, "experiment_id", None),
            "dataset_version": getattr(record, "dataset_version", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
            "coverage": getattr(record, "coverage", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(
    correlation_id: str, tenant: str, experiment_id: str, dataset_version: str
) -> CorrelationAdapter:
    base = logging.getLogger("eval.harness")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base,
        {
            "correlation_id": correlation_id,
            "tenant": tenant,
            "experiment_id": experiment_id,
            "dataset_version": dataset_version,
        },
    )


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class PolicyDenied(PermanentError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = BREAKER_FAILURES,
        recovery_seconds: float = BREAKER_RECOVERY_S,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.01,
    max_seconds: float = 0.08,
    retry_after: float | None = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last


class WormLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._head = "genesis"
        self.rows: list[dict[str, Any]] = []

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            body = json.dumps(payload, sort_keys=True, default=str)
            digest = hashlib.sha256(f"{self._head}|{body}".encode()).hexdigest()
            row = {**payload, "prev": self._head, "worm_hash": digest}
            self.rows.append(row)
            self._head = digest
            return row


def pass_at_k(n: int, c: int, k: int) -> float:
    if k < 1 or n < k or c < 0 or c > n:
        raise PermanentError(f"pass@k undefined for n={n} c={c} k={k}")
    if n - c < k:
        return 1.0
    prod = 1.0
    for i in range(k):
        prod *= (n - c - i) / (n - i)
    return 1.0 - prod


def pass_hat_k(trials: list[bool]) -> float:
    if not trials:
        raise PermanentError("pass^k needs ≥1 trial")
    return 1.0 if all(trials) else 0.0


def mean_pass_hat_k(per_task: list[list[bool]]) -> float:
    if not per_task:
        return 0.0
    return sum(pass_hat_k(t) for t in per_task) / len(per_task)


def tool_f1(
    predicted: list[tuple[str, str]], gold: list[tuple[str, str]]
) -> tuple[float, float, float]:
    p_set, g_set = set(predicted), set(gold)
    if not p_set and not g_set:
        return 1.0, 1.0, 1.0
    inter = len(p_set & g_set)
    precision = inter / len(p_set) if p_set else 0.0
    recall = inter / len(g_set) if g_set else 0.0
    if precision + recall == 0:
        return precision, recall, 0.0
    return precision, recall, 2 * precision * recall / (precision + recall)


def canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class Trace:
    example_id: str
    repetition: int
    tool_calls: list[ToolCall]
    final_state: dict[str, Any]
    cost_usd: float
    latency_ms: float
    degraded: bool = False
    infra_error: str | None = None


@dataclass
class Example:
    example_id: str
    prompt: str
    gold_calls: list[ToolCall]
    goal_state: dict[str, Any]
    allowed_tools: frozenset[str]
    catalog: frozenset[str]


@dataclass(frozen=True)
class Gates:
    min_task_success: float = 0.5
    min_tool_f1: float = 0.8
    max_cost_usd: float = 0.05
    max_latency_ms: float = 8000.0
    min_coverage: float = COVERAGE_MIN


@dataclass
class ExampleScore:
    example_id: str
    task_success: float
    tool_precision: float
    tool_recall: float
    tool_f1: float
    cost_usd: float
    latency_ms: float
    quality: float | None
    judged: bool
    hallucinated: bool
    infra_error: str | None
    rationale: str


@dataclass
class Aggregate:
    n: int
    task_success: float
    pass_at_1: float
    pass_hat_k: float
    tool_f1: float
    cost_usd: float
    latency_ms: float
    coverage: float
    ship: bool
    failed_gates: list[str]


class Mode(Enum):
    CI = "ci"
    ONLINE = "online"


class ScoreAggregator:
    """Dual-oracle ship gate: hard T/F/C/L + coverage; rubric cannot override T=0."""

    def __init__(self, gates: Gates, worm: WormLog, evaluator_version: str) -> None:
        self.gates = gates
        self.worm = worm
        self.evaluator_version = evaluator_version

    def score_example(self, example: Example, traces: list[Trace], quality: float | None, judged: bool) -> ExampleScore:
        usable = [t for t in traces if t.infra_error is None]
        successes = [self._task_ok(example, t) for t in usable]
        task = sum(successes) / len(successes) if successes else 0.0
        f1s: list[float] = []
        precs: list[float] = []
        recs: list[float] = []
        halluc = False
        for t in usable:
            keys = [(c.name, canonical_args(c.arguments)) for c in t.tool_calls]
            gold = [(c.name, canonical_args(c.arguments)) for c in example.gold_calls]
            local_halluc = any(c.name not in example.catalog for c in t.tool_calls)
            halluc = halluc or local_halluc
            p, r, f1 = tool_f1(keys, gold)
            precs.append(p)
            recs.append(r)
            f1s.append(0.0 if local_halluc else f1)
        mean_f1 = sum(f1s) / len(f1s) if f1s else 0.0
        cost = sum(t.cost_usd for t in traces) / len(traces) if traces else math.inf
        lat = max((t.latency_ms for t in traces), default=math.inf)
        infra = next((t.infra_error for t in traces if t.infra_error), None)
        rationale = (
            f"T={task:.3f} F1={mean_f1:.3f} C={cost:.4f} L={lat:.0f}ms "
            f"judged={judged} halluc={halluc} q={quality}"
        )
        rec = ExampleScore(
            example.example_id,
            task,
            sum(precs) / len(precs) if precs else 0.0,
            sum(recs) / len(recs) if recs else 0.0,
            mean_f1,
            cost,
            lat,
            quality,
            judged,
            halluc,
            infra,
            rationale,
        )
        self.worm.append(
            {
                "event": "score",
                "policy": POLICY_VERSION,
                "evaluator_version": self.evaluator_version,
                "example_id": rec.example_id,
                "task_success": rec.task_success,
                "tool_f1": rec.tool_f1,
                "cost_usd": rec.cost_usd,
                "latency_ms": rec.latency_ms,
                "judged": rec.judged,
                "prompt_hash": hashlib.sha256(example.prompt.encode()).hexdigest()[:16],
            }
        )
        return rec

    def _task_ok(self, example: Example, trace: Trace) -> bool:
        if trace.degraded or trace.infra_error:
            return False
        return trace.final_state == example.goal_state

    def aggregate(self, scores: list[ExampleScore], per_task_trials: list[list[bool]]) -> Aggregate:
        n = len(scores)
        if n == 0:
            return Aggregate(0, 0.0, 0.0, 0.0, 0.0, math.inf, math.inf, 0.0, False, ["empty"])
        judged_ok = [s for s in scores if s.infra_error is None]
        coverage = sum(1 for s in judged_ok if s.judged) / n
        task = sum(s.task_success for s in scores) / n
        f1 = sum(s.tool_f1 for s in scores) / n
        cost = sum(s.cost_usd for s in scores) / n
        lat = sorted(s.latency_ms for s in scores)[min(n - 1, max(0, int(math.ceil(0.95 * n) - 1)))]
        p1 = task
        phk = mean_pass_hat_k(per_task_trials)
        failed: list[str] = []
        if task < self.gates.min_task_success:
            failed.append("task_success")
        if f1 < self.gates.min_tool_f1:
            failed.append("tool_f1")
        if cost > self.gates.max_cost_usd:
            failed.append("cost")
        if lat > self.gates.max_latency_ms:
            failed.append("latency")
        if coverage < self.gates.min_coverage:
            failed.append("coverage")
        return Aggregate(n, task, p1, phk, f1, cost, lat, coverage, not failed, failed)


class McpAllowlist:
    def __init__(self, hosts: frozenset[str]) -> None:
        self.hosts = hosts

    def check(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self.hosts:
            raise PolicyDenied(f"mcp host not allowlisted: {host}")


class JudgeClient:
    def __init__(self, name: str, score: float | None, fail: type[Exception] | None = None) -> None:
        self.name = name
        self._score = score
        self._fail = fail

    def grade(self, redacted_trace: str) -> float:
        if self._fail is not None:
            raise self._fail(f"{self.name} down")
        if self._score is None:
            raise PermanentError(f"{self.name} unscored")
        if "ignore previous" in redacted_trace.lower():
            raise PermanentError("judge_steering_suspected")
        return self._score


class JudgeChain:
    def __init__(
        self,
        primary: JudgeClient,
        secondary: JudgeClient,
        breaker: CircuitBreaker,
        mode: Mode,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.mode = mode

    def grade(self, redacted_trace: str, log: CorrelationAdapter) -> tuple[float | None, bool]:
        def _try(client: JudgeClient) -> float:
            return client.grade(redacted_trace)

        try:
            self.breaker.allow()
            score = retry_call(lambda: _try(self.primary))
            self.breaker.record_success()
            log.info("judge_primary_ok model=%s", self.primary.name)
            return score, True
        except (CircuitOpenError, TransientError, PermanentError) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.record_failure()
            log.warning("judge_primary_fail err=%s", exc, extra={"breaker": self.breaker.state.value})
            try:
                score = retry_call(lambda: _try(self.secondary))
                log.info("judge_secondary_ok model=%s", self.secondary.name)
                return score, True
            except (TransientError, PermanentError, CircuitOpenError) as sec:
                log.error("judge_degraded err=%s", sec, extra={"degraded": True})
                if self.mode is Mode.CI:
                    raise PermanentError("fail-closed CI: judge unavailable") from sec
                return None, False


class AgentFn:
    def __init__(self, emit: Callable[[Example], Trace], fail: type[Exception] | None = None) -> None:
        self._emit = emit
        self._fail = fail

    def run(self, example: Example) -> Trace:
        if self._fail is not None:
            raise self._fail("agent down")
        return self._emit(example)


class AgentChain:
    def __init__(self, primary: AgentFn, secondary: AgentFn, breaker: CircuitBreaker) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    def run(self, example: Example, log: CorrelationAdapter) -> Trace:
        def _try(agent: AgentFn) -> Trace:
            return agent.run(example)

        try:
            self.breaker.allow()
            trace = retry_call(lambda: _try(self.primary))
            self.breaker.record_success()
            log.info("agent_primary_ok example=%s", example.example_id)
            return trace
        except (CircuitOpenError, TransientError, PermanentError) as exc:
            if not isinstance(exc, CircuitOpenError):
                self.breaker.record_failure()
            log.warning("agent_primary_fail err=%s", exc)
            try:
                trace = retry_call(lambda: _try(self.secondary))
                log.info("agent_secondary_ok example=%s", example.example_id)
                return trace
            except (TransientError, PermanentError) as sec:
                log.error("agent_degraded err=%s", sec, extra={"degraded": True})
                return Trace(
                    example.example_id,
                    0,
                    [],
                    {},
                    0.0,
                    0.0,
                    degraded=True,
                    infra_error=None,
                )


class EvalHarness:
    def __init__(
        self,
        agent: AgentChain,
        judges: JudgeChain,
        aggregator: ScoreAggregator,
        mcp: McpAllowlist,
        log: CorrelationAdapter,
        repetitions: int = 1,
    ) -> None:
        self.agent = agent
        self.judges = judges
        self.aggregator = aggregator
        self.mcp = mcp
        self.log = log
        self.repetitions = repetitions

    def run(self, examples: list[Example]) -> Aggregate:
        scores: list[ExampleScore] = []
        trials: list[list[bool]] = []
        for ex in examples:
            redacted, audit = redact_pii(ex.prompt)
            self.aggregator.worm.append(
                {"event": "pii_redact", "example_id": ex.example_id, "audit": audit}
            )
            traces: list[Trace] = []
            for rep in range(self.repetitions):
                t = self.agent.run(ex, self.log)
                t.repetition = rep
                traces.append(t)
            blob = redacted + json.dumps(
                [canonical_args(c.arguments) for tr in traces for c in tr.tool_calls]
            )
            quality, judged = self.judges.grade(blob, self.log)
            sc = self.aggregator.score_example(ex, traces, quality, judged)
            scores.append(sc)
            usable = [t for t in traces if t.infra_error is None]
            trials.append([t.final_state == ex.goal_state and not t.degraded for t in usable])
        agg = self.aggregator.aggregate(scores, trials)
        self.log.info(
            "experiment_done ship=%s failed=%s",
            agg.ship,
            ",".join(agg.failed_gates) or "-",
            extra={"coverage": round(agg.coverage, 4)},
        )
        return agg


def _ok_trace(ex: Example) -> Trace:
    return Trace(
        ex.example_id,
        0,
        list(ex.gold_calls),
        dict(ex.goal_state),
        cost_usd=0.026,
        latency_ms=1200.0,
    )


def main() -> None:
    random.seed(0)
    cid = str(uuid.uuid4())
    log = build_logger(cid, "bank-a", "exp-canary", "ds-v3")
    worm = WormLog()
    example = Example(
        "refund-001",
        "Refund user ada@example.com SSN 111-22-3333 up to policy cap.",
        [ToolCall("apply_refund", {"amount": 20, "order_id": "o1"})],
        {"refunded": 20, "order_id": "o1"},
        frozenset({"apply_refund"}),
        frozenset({"apply_refund", "lookup_order"}),
    )
    mcp = McpAllowlist(frozenset({"eval-crm.internal"}))
    mcp.check("https://eval-crm.internal/mcp")
    agent = AgentChain(AgentFn(_ok_trace), AgentFn(_ok_trace), CircuitBreaker())
    judges = JudgeChain(
        JudgeClient("gpt-4.1", 0.71),
        JudgeClient("gpt-4.1-nano", 0.65),
        CircuitBreaker(),
        Mode.ONLINE,
    )
    harness = EvalHarness(
        agent, judges, ScoreAggregator(Gates(), worm, "rubric-v4"), mcp, log, repetitions=3
    )
    agg = harness.run([example])
    assert agg.ship, agg.failed_gates
    assert any("<email:" in json.dumps(r) or "pii_redact" == r.get("event") for r in worm.rows)
    n, c, k = 200, 40, 10
    est = pass_at_k(n, c, k)
    assert 0.0 < est < 1.0

    fail_judges = JudgeChain(
        JudgeClient("primary", None, TransientError),
        JudgeClient("secondary", None, TransientError),
        CircuitBreaker(failure_threshold=1),
        Mode.ONLINE,
    )
    degraded = EvalHarness(
        agent, fail_judges, ScoreAggregator(Gates(min_coverage=0.99), worm, "rubric-v4"), mcp, log
    )
    online = degraded.run([example])
    assert not online.ship and "coverage" in online.failed_gates

    ci_judges = JudgeChain(
        JudgeClient("primary", None, TransientError),
        JudgeClient("secondary", None, TransientError),
        CircuitBreaker(failure_threshold=1),
        Mode.CI,
    )
    try:
        EvalHarness(
            agent, ci_judges, ScoreAggregator(Gates(), worm, "rubric-v4"), mcp, log
        ).run([example])
        raise SystemExit("CI should fail-closed")
    except PermanentError as exc:
        log.info("ci_fail_closed err=%s", exc)

    print(json.dumps({
        "ship": agg.ship,
        "task_success": agg.task_success,
        "pass_hat_k": agg.pass_hat_k,
        "tool_f1": agg.tool_f1,
        "cost_usd": agg.cost_usd,
        "latency_ms": agg.latency_ms,
        "coverage": agg.coverage,
        "pass_at_k_demo": est,
        "online_unscored_fails_coverage": online.failed_gates,
        "worm_rows": len(worm.rows),
    }, indent=2))


if __name__ == "__main__":
    main()
```

**What the demo asserts.** Three repetitions of a τ-shaped refund example succeed on DB goal-state and tool-set F1; cost **$0.026**/trial matches the research τ-like **[inferred] ~$26/1k**; p95-style latency gate uses the 95th order statistic on the tiny set (noise — do not treat n=1 p99 as an SLO). PII is redacted before the judge. Online judge outage **fail-opens** (unscored) and the aggregator **fails coverage**. CI judge outage **fail-closes**. Hallucinated tools zero that trace’s F1. Ship uses T/F1/C/L/coverage only — rubric \(Q\) is recorded, never a hard gate.

**Interview talking point:** jittered retries handle judge 429s; they do not make a skipped score a pass. Coverage + dual oracles + WORM `evaluator_version` are three different failure classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rule: **dual oracles, versioned datasets, coverage SLOs, named (split, scaffold, date)**. Do not merge on an LLM judge. Do not use SWE-bench Verified/Pro as a KPI. Do not put the judge on the user path.

### Scenario 1 — Coding agent at a regulated bank

**Problem statement.** Internal coding agent for a bank: ephemeral runners, PCI/code-in-traces, merge gated by tests. Leadership wants “SWE-bench 96%” on a vendor slide. Threats: Verified gold-patch regurgitation (OpenAI 2026 stopped reporting it); Pro public split 23.3%→80.3% in eight months then **~30% broken** (OpenAI retracts the recommendation); reward hacking via editing tests or `.git/config`; eval MCP pointed at corp Git; SaaS traces holding source + PII; LLM-as-judge as the merge gate; harness Docker wall time quoted as the developer SLO. NFR: report tokens/issue + p95 sandbox time, not “SWE %.” Cost: SWE-class eval is agent tokens + Docker minutes; platform 1k experiment runs **[inferred] $5** extended LangSmith on top of provider tokens.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Jira/Git   │ CI  │ CONTROL PLANE                                             │
│ internal   │────▶│ Gateway: SSO, correlation-id, CI IdP ≠ prod refresh       │
│ issues     │     │ Policy: PII detect→redact→audit; issue body untrusted     │
└────────────┘     │ Router: OFFLINE experiment only for merge; no Verified KPI│
                   │ Budget: new run_id/patch; spend cap on description-judge  │
                   │ Orchestrator: Temporal wf=tenant:eval:ds:agent; Kafka     │
                   │  outbox before Docker; fail-closed CI; fail-open unused   │
                   │ HITL: humans merge; judge never merges                    │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ dataset vN + hidden tests    │ MCP git simulator only
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ EVAL DATA PLANE  │        │ TOOL PROXIES                 │
                   │ ephemeral Docker │        │ pytest FAIL_TO_PASS /        │
                   │ @ base_commit    │        │ PASS_TO_PASS; no corp Git;   │
                   │ Phoenix / hybrid │        │ no `.git/config` writes;     │
                   │  traces (not SaaS│        │ audience-bound MCP; PKCE     │
                   │  PCI card data)  │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Dataset indefinite; experiment snapshot; WORM: patch hash,│
                   │  node-ids, evaluator_ver; RPO=dataset pin; RTO=replay     │
                   │  tests not LLM; p95 = sandbox span, not harness wall      │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Public SWE Verified/Pro as KPI + SaaS traces + LLM merge judge | B. Recommended: internal post-cutoff issues + hidden tests in ephemeral runners + hybrid/Phoenix + judge only on PR text | C. pass@1 n=1 on Playground + live corp Git MCP |
| --- | --- | --- | --- |
| Cost | Leaderboard chasing; Pro rot wastes eval $; extended traces 10× base | Tokens/issue + Docker minutes; 1k exp **[inferred] $5** platform; description-judge sampled | Cheap until a prod write; then incident |
| Latency | Harness wall (pull/cache) confused with SLO | p95 sandbox time on spans; judge async | Live Git latency + irrecoverable side effects |
| Ops | Contamination + 27–34% Pro broken labels as “wins” | Pin dataset; new `run_id`; infra-error bucket ≠ fail | `(run_id, instance_id)` cache hides new diffs |
| Security | Source+PII to SaaS; PCI forbid card data; judge subprocessor on code | Self-hosted/hybrid; mask at SDK; no prod MCP; tests merge | Token passthrough / corp Git = RCE-adjacent |
| Scalability | Aggregator 96% is not capacity | Horizontal runner pool; nightly pass@k; canary pass^k | Serial Playground; max_concurrency=0 accident |

**Decision rationale.** **B** is research scenario A: SWE-style hidden tests in **ephemeral** runners + PASS_TO_PASS as the binary ship gate; not Verified/Pro as KPI; process policy on traces; Phoenix or Braintrust hybrid because PII/code is in traces; judge only for PR description; never for merge; report tokens/issue + p95 sandbox time. A treats a contaminated/broken public bench as an SLO and puts an LLM on merge. C is the poison-pill row (live MCP prod, pass@1 flake). Interview close: “Oracle is execution. The harness is not the product. Named internal split, dated, uncontaminated.”

### Scenario 2 — Customer-support agent (τ-shaped)

**Problem statement.** Support agent mutates CRM (refunds, tickets) under policy. Demo transcripts look fluent. Original τ-bench: GPT-4o-class **<50%** task success; retail **pass^8 < 25%**; airline pass@1 **35.2%**. Think-tool τ-airline pass^1 **0.332→0.584** still leaves pass^5 **0.340** — the pass^1/pass^5 gap is product risk. Temptations: LLM-judge the refund amount; share cache keys between agent and user-sim; 100% online rubric on the user path; pass@1 CI; comparing aggregator τ² rows (Opus 4.6 **Retail 91.9% / Telecom 99.3%**) without the harness footnote. Cost: 1k τ-like tasks **[inferred] ~$26** agent + similar user-sim + **$5** extended traces + **$9** full trace-judge; **pass^4 × ~4** on agent+sim.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Customer   │ SSE │ CONTROL PLANE (product + eval attachments)                │
│ chat /     │────▶│ Gateway: auth, tenant TPM, breaker; judge NOT on path     │
│ voice τ³   │     │ Policy: PII redact before trace ingest and before judge   │
└────────────┘     │ Router: ONLINE sample 1–5% rubric on threads; OFFLINE     │
                   │  canary pass^k (k=3–5) in CI; nightly full pack           │
                   │ Budget: weekly judge spend cap; opt-out extended upgrade  │
                   │ Orchestrator: Temporal ticket wf; Kafka intent before CRM │
                   │ Code gate: refund ≤ policy cap (not an LLM)               │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ live CRM (prod IAM)          │ eval CRM simulator
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ PROD DATA PLANE  │        │ EVAL TOOL PROXIES            │
                   │ OpenInference    │        │ Frozen user-sim (pinned);    │
                   │ AGENT/TOOL spans │        │ cache key ≠ agent; τ DB      │
                   │ TTFT/e2e SLO     │        │ goal-state oracle; BFCL AST  │
                   │ 14d unless fail  │        │  on schema + irrelevance;    │
                   │  promoted        │        │ dual-control τ² if user apps │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Traces + experiment snapshots; coverage %; pass^k canary; │
                   │ WORM evaluator_ver; RPO=dataset+CRM ledger; RTO=replay    │
                   │ sim, not prod refunds; time-to-score off the UX SLO       │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Fluency LLM-judge on the request path + pass@1 CI + shared agent/sim cache | B. Recommended: CRM/DB goal-state + policy code scorer + pass^k canary + sampled async rubric + AST/F1 | C. 100% online judge, extended retention, unpinned user-sim, live prod MCP in CI |
| --- | --- | --- | --- |
| Cost | Judge on every ticket = latency tax + **[inferred] $9/1k** at 100% plus upgrade to **$5/1k** traces | 1–5% judges **[inferred] ~$0.45/1k**; canary pass^3–5 not full pass^8; nightly is the reliability bill (**[inferred] ~$208** agent+sim /1k at pass^4) | Surprise extended bill; sim upgrades silently invalidate history |
| Latency | Synchronous second LLM; TTFT includes judge | User SLO = answer TTFT/e2e; time-to-score sidecar | CI live MCP + 300 s hung tools; p99 is a refund loop |
| Ops | Flaky pass@1 red/green; 24.9 pp envelopes | Pin sim; dataset versions; coverage monitor; Topics for shift | Aggregator τ² compared without harness footnote |
| Security | PII to judge on every turn; arithmetic judged by LLM | Redact then sample; code scorer on refund cap; simulators in eval | Prod writes from CI; token passthrough |
| Scalability | Judge TPM scales with traffic | Sampling + spend cap; isolate eval TPM from serving | 100% judge + pass^8 full set will not fit PR latency |

**Decision rationale.** **B** is research scenario B: final **CRM/DB state** + policy checklist (conversation fluency ≠ correct mutation); pass^k on canary tasks in CI (k=3–5) because of the original retail pass^8 hole; frozen user-sim; sampled rubric on **threads** (coherence, tone) reference-free; code scorer for refund caps; budget pass^4 × 2 LLMs as the real $/task. A puts the sidecar on the SLO path and ships optimistic pass@1. C is the spend-cap + leakage + destructive-eval row. Interview close: “pass^1 is demos. pass^5 is the pager. The DB mutation is the oracle. The judge is a sampled comment, not the refund.”

---

*End of module. Six sections. Six mandatory topics (task success, trajectory, tool accuracy, quality, cost, latency). Token `$ / 1k` tables are **[inferred]** from published SKUs × stated reference loops dated 2026-08-21. No unpublished product e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented caps (Gaia2 300 s, Plus 500k evt/h, 25k runs/trace, 14d/400d retention).*
