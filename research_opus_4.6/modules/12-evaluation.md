# Module 12: Evaluation — Benchmarks, Frameworks, Statistical Rigor, and Production Monitoring

**Scope**: Offline/online/pre-merge evaluation, benchmarks (MMLU, SWE-bench, GAIA, Chatbot Arena, LiveBench), LLM-as-judge (biases and calibration), evaluation frameworks (DeepEval, Promptfoo, Braintrust, Langfuse, Inspect AI), agentic evaluation (trajectory, tool use, cost-normalized scoring), statistical methods (bootstrap CIs, Bradley-Terry, multiple comparisons), eval-driven development, RAG evaluation, anti-patterns (contamination, Goodhart's Law), production monitoring (drift, shadow mode, A/B, canary), and cost of evaluation.
**Prerequisite**: Module 04 (Agent Architecture), Module 07 (Memory — for RAG eval context).
**Last updated**: 2026-08-21 | **Sources consulted**: 102

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Eval Scheduler  │  │  Judge Model     │  │  Regression Gate │  │  Alert Manager   │  │
 │  │  - Offline: per  │  │  Selector        │  │  - Threshold:    │  │  - Drift alerts  │  │
 │  │    release       │  │  - Opus for cal. │  │    3-5% drop     │  │  - Quality drop  │  │
 │  │  - CI: per PR    │  │  - Flash for CI  │  │    blocks merge  │  │  - Budget exceed │  │
 │  │  - Online: cont. │  │  - Ensemble for  │  │  - Score summary │  │  - Contamination │  │
 │  │    1-5% sample   │  │    high-stakes   │  │    on PR comment │  │    detection     │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         DATA PLANE: EVALUATION ENGINE                              │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐                 │    │
 │  │  │  OFFLINE EVAL    │  │  CI / PRE-MERGE  │  │  ONLINE EVAL     │                 │    │
 │  │  │                  │  │                  │  │                  │                 │    │
 │  │  │  Golden Dataset  │  │  Fast Tier (<30s)│  │  Traffic Sampler │                 │    │
 │  │  │  (100-500 examp.)│  │  - Deterministic │  │  - 1-5% of reqs │                 │    │
 │  │  │                  │  │  - JSON/regex    │  │                  │                 │    │
 │  │  │  LLM-as-Judge   │  │  - Schema valid  │  │  LLM-as-Judge   │                 │    │
 │  │  │  - Pointwise    │  │                  │  │  on live samples │                 │    │
 │  │  │  - Pairwise     │  │  Slow Tier (PRs) │  │                  │                 │    │
 │  │  │  - Multi-rubric │  │  - LLM judges    │  │  Drift Detector  │                 │    │
 │  │  │                  │  │  - Agent trace   │  │  - Input distrib │                 │    │
 │  │  │  Human Review   │  │    eval          │  │  - Model behav.  │                 │    │
 │  │  │  - Calibration  │  │                  │  │  - Embedding     │                 │    │
 │  │  │  - Compliance   │  │                  │  │  - Retrieval     │                 │    │
 │  │  └──────────────────┘  └──────────────────┘  └──────────────────┘                 │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐                 │    │
 │  │  │  BENCHMARK EVAL  │  │  AGENTIC EVAL    │  │  RAG EVAL        │                 │    │
 │  │  │                  │  │                  │  │                  │                 │    │
 │  │  │  SWE-bench, GAIA │  │  Layer 1: Outcome│  │  Retrieval Stage │                 │    │
 │  │  │  LiveBench, Arena│  │  Layer 2: Traject│  │  - Precision@k   │                 │    │
 │  │  │  TAU-bench, BFCL │  │  Layer 3: Compon.│  │  - Recall@k      │                 │    │
 │  │  │  HLE, FrontierM. │  │                  │  │  - MRR, NDCG     │                 │    │
 │  │  │                  │  │  Tool correctness│  │                  │                 │    │
 │  │  │  Pass@1, Pass^k  │  │  Step efficiency │  │  Generation Stage│                 │    │
 │  │  │  Cost-normalized │  │  Cost-normalized │  │  - Faithfulness  │                 │    │
 │  │  │                  │  │                  │  │  - Relevance     │                 │    │
 │  │  └──────────────────┘  └──────────────────┘  └──────────────────┘                 │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Judge Model   │  │ Sandbox       │  │ Human Annot.  │  │ CI/CD         │       │    │
 │  │  │ Gateway       │  │ Execution     │  │ Queue         │  │ Integration   │       │    │
 │  │  │ - Rate limit  │  │ - Docker/K8s  │  │ - Label mgmt  │  │ - GitHub Act. │       │    │
 │  │  │ - Cost cap    │  │ - Agent isol. │  │ - Calibration │  │ - PR comments │       │    │
 │  │  │ - Model route │  │ - Timeout     │  │ - Agreement   │  │ - Merge block │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Golden Dataset    │  │ Eval Results &    │  │ Calibration Sets  │  │ Drift Baselines│  │
 │  │ Store             │  │ Experiment Log    │  │ - Human labels    │  │ - Embedding    │  │
 │  │ - Versioned JSON  │  │ - Per-run scores  │  │ - 100-500 examples│  │   centroids    │  │
 │  │ - Test/train split│  │ - Judge traces    │  │ - Agreement stats │  │ - Probe set    │  │
 │  │ - Production-     │  │ - Cost tracking   │  │ - Quarterly       │  │   fingerprints │  │
 │  │   failure imports │  │ - Confidence int. │  │   recalibration   │  │ - Retrieval    │  │
 │  │                   │  │ - Model versions  │  │                   │  │   overlap      │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Quality Metrics   │  │ Statistical       │  │ Cost & Token      │  │ Benchmark      │  │
 │  │ - Accuracy, F1    │  │ Health            │  │ Accounting        │  │ Tracking       │  │
 │  │ - Faithfulness    │  │ - CI widths       │  │ - $/eval run      │  │ - Score over   │  │
 │  │ - Judge agreement │  │ - Power achieved  │  │ - $/correct ans   │  │   time         │  │
 │  │ - Drift magnitude │  │ - Effect sizes    │  │ - Judge model $   │  │ - Contamination│  │
 │  │ - Regression %    │  │ - Sample adequacy │  │ - Infra overhead  │  │   indicators   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Eval Trigger**: The **Eval Scheduler** fires based on context: a PR triggers CI-tier evals (fast deterministic + slow LLM judge), a release triggers the full offline suite against the golden dataset, and continuously in production, a sampler selects 1–5% of live traffic for online quality scoring.

**Step 2 — Judge Selection**: The **Judge Model Selector** routes scoring to the appropriate judge. Flash-class models ($0.10/M tokens) handle CI scoring where 85%+ agreement with calibration suffices. Frontier models ($15/M tokens) are reserved for calibration validation and high-stakes compliance reviews. Ensemble judging (3 different model families) is used for deployment decisions.

**Step 3 — Evaluation Execution**: The **Evaluation Engine** runs the appropriate eval type: deterministic checks (JSON parse, regex, schema validation) execute in <30s for CI gates; LLM-as-judge scoring runs pointwise or pairwise comparisons; agentic eval traces tool calls, trajectory efficiency, and cost; RAG eval separately scores retrieval (precision@k, recall@k) and generation (faithfulness, relevance).

**Step 4 — Statistical Analysis**: Raw scores go through statistical processing: bootstrap confidence intervals for uncertainty quantification, effect size calculation for model comparisons, Holm-Bonferroni correction when comparing multiple models, and Bradley-Terry estimation for preference data from pairwise comparisons.

**Step 5 — Gating Decision**: The **Regression Gate** applies thresholds. A 3–5% drop on any core metric blocks the merge, with the CI pipeline posting a PR comment showing which eval cases improved and regressed. For production, drift alerts fire when input distribution embeddings move >2 standard deviations from the 30-day baseline.

**Step 6 — Feedback Loop**: Production failures are triaged, root-caused, and added to the golden dataset. Online eval results feed back into the offline suite. Calibration sets are refreshed quarterly with updated human labels, and judge-human agreement (Cohen's kappa) is re-measured.

---

## 2. Core Mechanics & Algorithms

### 2.1 Evaluation Taxonomy

| Dimension | Categories |
|-----------|-----------|
| **Lifecycle stage** | Offline (pre-release) → CI/Pre-merge (per PR) → Online (production) |
| **Scoring method** | Deterministic (exact match, ROUGE, F1) → LLM-as-judge (80–90% human agreement) → Human (gold standard) |
| **Scope** | Model eval (base model on benchmarks) → System eval (full app + prompts + retrieval + tools) |
| **Granularity** | Unit (single LLM call) → Integration (retriever + generator) → System (full multi-step agent) |
| **Quality dimensions** | Correctness, Faithfulness, Relevance, Safety — each needs separate metrics |

### 2.2 Benchmark Landscape (August 2026)

| Benchmark | Domain | Status | SOTA | Key Property |
|-----------|--------|:------:|:----:|-------------|
| **MMLU** | General (57 subjects) | Saturated (>88%) | ~90%+ | Contaminated; historical baseline only |
| **MMLU-Pro** | General (10-choice) | Near-saturated | ~90% | 2% prompt sensitivity vs 4–5% for MMLU |
| **HumanEval** | Python coding | Saturated (>90%) | ~95%+ | Likely contaminated |
| **SWE-bench Verified** | Real-world software eng | Near-saturated (7 models >95%) | 97% (Opus 5) | Scaffolding-dependent; 15+ point variance |
| **SWE-bench Pro** | Complex software eng | Active (61–80%) | ~80% | Contamination-resistant; reveals scaffold inflation |
| **GPQA Diamond** | Graduate science | Near-saturated (24 models >90%) | 95.5% | Expert humans: 65–74% |
| **GAIA** | General AI assistant | Active (~52%) | 52.3% | Human: ~92%; private test; exact match |
| **HLE** | Expert multi-domain | Active (~55-65%) | 64.7% (Opus 5) | ~30% of chem/bio answers may be incorrect |
| **FrontierMath** | Advanced math | Active (~40%) | ~40%+ | Under 2% at launch (Nov 2024) |
| **ARC-AGI-2** | Abstract reasoning | Active (~85%) | 97.9% | ARC-AGI-3: <1% AI vs 100% human |
| **Chatbot Arena** | User preference | Active | Elo ~1418 | 5M+ votes; Bradley-Terry; gold standard |
| **LiveBench** | Multi-domain (refreshed) | Active (<70%) | ~70% | Monthly refresh; contamination-resistant |
| **TAU-bench** | Customer service agents | Active | Domain-dependent | Text + voice full-duplex |
| **IFEval** | Instruction following | Active | Varies | 25 verifiable instruction types |
| **MixEval** | Web-query matched | Active | ~80%+ | 0.96 Chatbot Arena correlation; $0.60/run |
| **BFCL v4** | Function/tool calling | Active | ~78% | Single-turn strong; multi-turn challenging |

**Saturation rule**: If a vendor leads with MMLU or HumanEval in 2026, treat it like BLEU in 2021 — useful for continuity, not for choosing a model.

### 2.3 LLM-as-Judge Patterns and Biases

**Adoption**: 53.3% of teams with deployed AI agents use LLM-as-judge. Achieves 80–90% agreement with humans at 500–5,000× lower cost.

| Pattern | Mechanism | Best For |
|---------|-----------|---------|
| **Pointwise scoring** | Judge rates single output on rubric (1–5) | Absolute quality tracking |
| **Pairwise comparison** | Judge picks winner between two outputs | Model comparison (A/B) |
| **Reference-based** | Judge compares output against gold answer | Known-answer regression |
| **Multi-criteria rubric** | Separate scores for correctness, faithfulness, safety | Granular quality tracking |

| Bias | Magnitude | Mitigation |
|------|:---------:|-----------|
| **Position bias** | 10–30% of verdicts flip on order swap | Randomize order; evaluate both permutations; average |
| **Verbosity bias** | Long nonsense scores above correct short answers | Penalize verbosity in rubric; "value conciseness" |
| **Self-preference** | 10% higher win rate for own model's outputs | Use different model family as judge |
| **Authority bias** | Variable | Strip authority markers from judged text |
| **Fallacy oversight** | Variable | Add "check logical validity" to rubric |

### 2.4 Three-Layer Agentic Evaluation

| Layer | What It Scores | Metrics | Why Outcome-Only Fails |
|-------|---------------|---------|----------------------|
| **Layer 1: Outcome** | Final black-box output | Task completion rate, answer correctness | A correct answer via a policy-violating trajectory is a false positive |
| **Layer 2: Trajectory** | Full ordered trace (tool calls, args, sequence) | Trajectory accuracy, step efficiency, plan adherence | 20 steps with 2 policy violations → failing trajectory even with correct answer |
| **Layer 3: Component** | Specific span (one LLM call, one retriever) | Invocation accuracy, selection accuracy, argument correctness | Pinpoints which component causes failures |

**Error compounding**: A 5% per-step error rate across a 5-step trajectory gives a 23% chance of overall failure.

### 2.5 Statistical Methods

| Method | Purpose | When to Use |
|--------|---------|-------------|
| **Bootstrap CI (percentile)** | Uncertainty quantification for eval scores | All eval reporting; never report bare means |
| **BCa bootstrap** | Bias-corrected CI | Historically recommended; underperforms percentile on pairwise LLM comparisons |
| **Bayesian beta-posterior** | Small-sample CI | Binary outcomes with N < 300 |
| **Paired bootstrap** | Compare two models on shared test set | Model A vs. B decisions |
| **Holm-Bonferroni** | Multi-model comparison correction | Evaluating N models, reporting best one |
| **Bradley-Terry MLE** | Preference rating from pairwise votes | Chatbot Arena-style human preference |
| **Cohen's kappa** | Judge-human agreement | Quarterly calibration of LLM judges |
| **Effect size (Cohen's d)** | Practical significance beyond statistical | Alongside p-values for all comparisons |

**Sample size rule**: For binary outcomes, ~400 examples/model for SE ~2.5%. For detecting a 5-point difference with 80% power: 600–1,000 examples. **tinyBenchmarks**: IRT can compress MMLU from 14,000 to 100 items at ~2% error.

### 2.6 RAG Evaluation: Two-Stage Metrics

| Stage | Metric | What It Measures |
|-------|--------|-----------------|
| **Retrieval** | Context Precision@k | Of chunks retrieved, how many are relevant? |
| **Retrieval** | Context Recall@k | Of relevant chunks, how many were retrieved? |
| **Retrieval** | MRR | Position of first relevant result |
| **Retrieval** | NDCG | Ranking quality when order matters |
| **Generation** | Faithfulness | Claims supported by retrieved documents? |
| **Generation** | Answer Relevancy | Does the response address the query? |
| **Generation** | Answer Correctness | Is the answer factually right? |

Tracking only generation hides retrieval regressions. Tracking only retrieval misses fabrication. Always track at least one metric from each stage.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Evaluation Economics

| Eval Type | Examples | Judge Model | Cost/Run | Notes |
|-----------|:-------:|-------------|:--------:|-------|
| CI smoke test (deterministic) | 50 | None (regex/JSON) | ~$0 | <30s; every commit |
| CI LLM-judge suite | 200 | Gemini Flash ($0.10/$0.40) | ~$0.50 | Per PR |
| Offline golden dataset | 500 | Flash + calibration sample on Opus | ~$5 | Per release |
| Pairwise model comparison | 500 × 2 | Opus ($15/$75) | ~$50 | Per model evaluation |
| MixEval full run | Rolling | Mixed | ~$0.60 | 6% of MMLU cost/time |
| SWE-bench Verified (500 tasks) | 500 | Agent execution | $500–5,000 | Scaffold-dependent |
| WebArena (full suite) | 812 | Browser agent + judge | $1,577 | Browser-Use + Sonnet 4 |
| RE-Bench (METR) | 7 envs | 1–6 H100s × 8h each | 56–336 H100-hours | Research engineering |
| Human annotation (calibration) | 100–500 | Expert human | $50–200/hr | Medical/legal: higher |

**Cost/1K eval runs**:

| Eval Configuration | Cost/1K Runs | Notes |
|-------------------|:-----------:|-------|
| Deterministic CI (50 cases) | ~$0 | Regex, JSON parse, schema |
| LLM-judge CI (200 cases, Flash) | ~$500 | Cheapest LLM judge |
| Full offline (500 cases, Opus) | ~$50,000 | Calibration-grade frontier |
| Agent eval (SWE-bench, 500 tasks) | $500K–$5M | Scaffold + execution cost |

**Judge model cost spread** (100× range on input pricing alone):

| Judge Model | Input $/M tokens | Output $/M tokens |
|------------|:----------------:|:-----------------:|
| Claude Opus 4.1 | $15.00 | $75.00 |
| GPT-4o | $2.50 | $10.00 |
| Gemini 2.0 Flash | $0.10 | $0.40 |
| GPT-4.1 Nano | $0.10 | $0.40 |

**Cost optimization**: Use cheapest judge maintaining >85% agreement with calibration set. Tiered evaluation (deterministic every commit, LLM judge per PR, full suite per release) reduces compute 60–70%. tinyBenchmarks via IRT compress eval sets 99% at ~2% error.

### 3.2 Latency SLA Targets

| Eval Type | p50 | p95 | p99 | Mitigation |
|-----------|-----|-----|-----|------------|
| Deterministic CI checks | 2s | 10s | 20s | Parallel execution; cache parsed schemas |
| LLM-judge (pointwise, Flash) | 5s/case | 15s/case | 30s/case | Batch requests; parallel judging |
| LLM-judge (pairwise, Opus) | 15s/case | 45s/case | 90s/case | Limit to calibration-grade use only |
| Agent eval (single task) | 60s | 300s | 600s | Per-step timeout; kill at wall-clock limit |
| Full CI pipeline (fast tier) | 30s | 60s | 120s | Split fast/slow tier; fast blocks merge |
| Full CI pipeline (slow tier) | 5min | 15min | 30min | Run on PR only; post results as comment |
| Drift detection cycle | 60s | 180s | 300s | Rolling window; pre-computed baselines |

**p50 mitigation**: Parallel judge calls across eval cases. Deterministic checks first (zero LLM cost).
**p95 mitigation**: Per-case timeout with skip + flag. Split CI into fast-tier (merge-blocking) and slow-tier (advisory).
**p99 mitigation**: Hard wall-clock limit per eval run. Return partial results with coverage gap notification.

### 3.3 Throughput & Back-Pressure

**Eval throughput at scale**: A 500-case golden dataset with LLM-judge scoring at 5s/case takes 42 minutes sequentially. With 10-way parallelism: ~4 minutes. With 50-way parallelism: <1 minute. Rate limits on judge model APIs are the primary bottleneck.

**Back-pressure mechanisms**:
- **CI queue depth**: If eval queue exceeds 10 pending PRs, downgrade slow-tier to sampling (100 cases instead of 500).
- **Judge API rate limit**: Fan out across multiple judge model providers. Flash for bulk, Opus for calibration subset.
- **Production sampling**: Under load, reduce online eval sampling from 5% to 1%. Never drop below 1%.
- **Cost cap**: Hard per-run budget. If eval cost exceeds $X, terminate and flag for review.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Golden dataset** | 0 (version-controlled) | <1s (git checkout) | JSON/CSV in repo; versioned with tests |
| **Eval results log** | Per-run (append-only) | <5s (reload from DB) | Timestamped results DB; experiment ID |
| **Calibration set** | 0 (version-controlled) | <1s (git checkout) | Versioned with golden dataset |
| **Drift baselines** | Per-cycle (rolling 7-day) | <30s (recompute from raw) | Pre-computed centroids; raw embeddings retained |
| **Judge traces** | Per-judgment | <5s (replay from log) | Full input/output logging for auditability |
| **Benchmark scores** | Per-eval-run | <1s (historical DB) | Append-only score DB with model version |

### 3.5 Benchmark Score vs. Production Reality

| Gap Type | Magnitude | Source |
|----------|:---------:|-------|
| Lab vs. production accuracy | 37% gap | Enterprise data across multiple deployments |
| Same model, different scaffold | 15+ point variance | SWE-bench: 30% with one scaffold, 55% with another |
| Pass@1 vs. pass^4 reliability | 15–25 point drop | Consistent finding: pass^k << pass@1 |
| Cost variation at similar accuracy | 50× | Different agent configurations |
| Public benchmark vs. domain eval | Highly variable | Teams routinely find production accuracy well below benchmark |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Evaluation Systems

#### 4.1.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Halt eval│    │ Run 3 probe │
         │ eval    │    │ pipeline;│    │ eval cases  │
         │ scoring │    │ fall back│    │ from calib. │
         │         │    │ to determ│    │ set; check  │
         │         │    │ only     │    │ agreement   │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 45s
              │     recovery timeout
              │     (45s → 90s → 180s exponential)
              │
              └──────────────────────────────┘
                    3/3 probes agree with
                    calibration labels
```

**Thresholds**:
- **Closed → Open**: 5 judge failures (timeout, malformed response, agreement with calibration <0.5) within 90s window.
- **Open duration**: 45s initial recovery timeout with exponential backoff (45s → 90s → 180s).
- **Half-Open probes**: 3 eval cases from calibration set with known human labels.
- **Half-Open → Closed**: All 3 probes produce scores agreeing with calibration labels (within 1 point on 5-point scale).
- **Fallback during Open**: Deterministic-only evaluation (JSON parse, regex, schema validation). No LLM-judge scoring. CI gate uses last-known-good score baseline.

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Judge model timeout | **Transient** | Response timeout >30s | Retry with backoff; fallback to cheaper judge |
| Judge hallucination (fabricated reasoning) | **Transient** | Calibration agreement drop <0.7 | Re-prompt with stricter rubric; switch judge model |
| Judge position bias | **Transient** | Score changes when response order swapped | Evaluate both permutations; average scores |
| Benchmark contamination detected | **Permanent** (dataset) | Canary string match; verbatim reproduction test | Retire contaminated items; use private holdout |
| Golden dataset label error | **Permanent** (data) | Disagreement pattern analysis; expert review | Quarantine disputed items; re-label |
| Eval infrastructure outage | **Transient** | Health check; CI pipeline failure | Queue evals; fall back to deterministic only |
| Model provider silent update | **Transient** | Behavioral fingerprint drift on probe set | Re-run benchmarks; update baselines |
| Eval set overfitting (Goodhart) | **Permanent** (process) | Score plateau with production quality decline | Rotate eval sets; evaluate on own data |
| Cost explosion (agent eval runaway) | **Transient** | Per-run budget monitor | Hard token/dollar cap per eval run |
| Calibration drift (judge disagrees with humans) | **Transient** | Quarterly kappa recalculation | Re-tune judge prompt; replace judge model |

### 4.3 Idempotency in Evaluation

Eval runs must be reproducible and idempotent — rerunning the same eval with the same model version and dataset must produce statistically equivalent results (within expected LLM non-determinism bounds).

```
Eval run request:
  │
  ┌─────────────────────────────────┐
  │ Idempotency Key:                │
  │ hash(eval_suite_version         │
  │   + model_id + model_version    │
  │   + dataset_version             │
  │   + judge_model_id              │
  │   + scoring_prompt_hash)        │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │ IF key in eval_results_cache    │
  │   AND cache_age < 24h:          │
  │   RETURN cached result          │
  │ ELSE:                           │
  │   run eval; store result + key  │
  └─────────────────────────────────┘
```

**Cache invalidation triggers**: Model version change, dataset update, judge prompt edit, scoring rubric change. Any of these invalidates the cache for all affected eval runs.

**Non-determinism handling**: LLM judge scores vary across runs even with temperature=0. Store mean ± CI from multiple judge passes. Flag results where CI width exceeds acceptable threshold (e.g., >1 point on 5-point scale).

### 4.3.1 Poison-Pill Detection in Evaluation

A poison pill in evaluation is an adversarial input designed to inflate or deflate eval scores without being detected — benchmark contamination, format-engineered prompts, or adversarial calibration examples.

**Detection heuristics**:
- **Contamination canary**: Embed unique canary strings in eval datasets. If model reproduces verbatim, flag contamination. BIG-bench pioneered this; demonstrated that canary was swallowed into training data.
- **Format sensitivity test**: Change answer format (e.g., (A) → [A]). Score swing >5% indicates format overfitting, not genuine capability.
- **Verbatim reproduction check**: If model generates eval question/answer pairs verbatim, those items are contaminated. Remove from scoring.
- **GSM1K control**: Compare performance on original dataset vs. reformulated control set. Gap exceeding noise threshold indicates memorization (Spearman's r² = 0.36 between verbatim reproduction rate and performance gap).

**Quarantine flow**: Flagged eval items → quarantine queue → expert review → confirm contamination → remove from active eval set → replace with fresh items from reserve pool → re-score without contaminated items.

### 4.4 Zero-Trust Evaluation Security

1. **Eval dataset isolation**: Golden datasets stored in version-controlled repos with access control. Eval questions never included in training data pipelines. Separate access paths for eval vs. training teams.

2. **Judge model separation**: Never use the same model as both the system-under-test and the judge — self-preference bias is 10%+ on own outputs. Use a different model family for judging.

3. **Immutable eval results**: All eval scores, judge traces, and statistical analyses stored in append-only storage. No retroactive editing. Enables audit trail for regulatory compliance (EU AI Act).

4. **Calibration set integrity**: Human-labeled calibration examples stored separately from automated eval pipeline. Access restricted. Labels verified by multiple annotators with inter-rater agreement requirements.

5. **Benchmark result provenance**: Every reported score must include: model ID, model version, scaffold version, eval harness version, prompt template hash, number of runs, confidence interval. Bare scores without provenance are inadmissible.

---

## 5. Production Enterprise Code

### 5.1 Evaluation Pipeline with Statistical Rigor

```python
import json
import random
from dataclasses import dataclass, field
from enum import Enum


class EvalTier(Enum):
    CI_FAST = "ci_fast"
    CI_SLOW = "ci_slow"
    OFFLINE = "offline"
    ONLINE = "online"


@dataclass
class EvalCase:
    input_text: str
    expected_output: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class EvalScore:
    case_id: str
    score: float
    judge_model: str
    reasoning: str = ""
    latency_ms: float = 0.0
    tokens_used: int = 0


@dataclass
class EvalResult:
    suite_name: str
    model_id: str
    mean_score: float
    ci_lower: float
    ci_upper: float
    n_cases: int
    scores: list[EvalScore]
    cost_usd: float


class EvalPipeline:
    def __init__(self, llm_client, judge_model: str, golden_dataset: list[EvalCase],
                 calibration_set: list[EvalCase] = None):
        self.llm = llm_client
        self.judge_model = judge_model
        self.golden_dataset = golden_dataset
        self.calibration_set = calibration_set or []

    async def run_suite(self, model_id: str, tier: EvalTier) -> EvalResult:
        cases = self._select_cases(tier)
        scores = []
        total_cost = 0.0

        for i, case in enumerate(cases):
            response = await self._get_model_response(model_id, case)
            score = await self._judge_response(case, response, tier)
            scores.append(score)
            total_cost += self._estimate_cost(score)

        values = [s.score for s in scores]
        mean_score = sum(values) / len(values)
        ci_lower, ci_upper = self._bootstrap_ci(values, n_bootstrap=1000)

        return EvalResult(
            suite_name=f"{tier.value}_{model_id}",
            model_id=model_id,
            mean_score=round(mean_score, 3),
            ci_lower=round(ci_lower, 3),
            ci_upper=round(ci_upper, 3),
            n_cases=len(cases),
            scores=scores,
            cost_usd=round(total_cost, 4),
        )

    def compare_models(self, result_a: EvalResult, result_b: EvalResult) -> dict:
        scores_a = [s.score for s in result_a.scores]
        scores_b = [s.score for s in result_b.scores]

        if len(scores_a) != len(scores_b):
            raise ValueError("Results must be from the same eval set for paired comparison")

        diffs = [a - b for a, b in zip(scores_a, scores_b)]
        mean_diff = sum(diffs) / len(diffs)
        ci_lower, ci_upper = self._bootstrap_ci(diffs, n_bootstrap=2000)

        std_diff = (sum((d - mean_diff) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5
        cohens_d = mean_diff / std_diff if std_diff > 0 else 0.0

        return {
            "model_a": result_a.model_id,
            "model_b": result_b.model_id,
            "mean_diff": round(mean_diff, 3),
            "ci_95": (round(ci_lower, 3), round(ci_upper, 3)),
            "significant": ci_lower > 0 or ci_upper < 0,
            "cohens_d": round(cohens_d, 3),
            "effect_size": (
                "negligible" if abs(cohens_d) < 0.2 else
                "small" if abs(cohens_d) < 0.5 else
                "medium" if abs(cohens_d) < 0.8 else "large"
            ),
            "favors": result_a.model_id if mean_diff > 0 else result_b.model_id,
        }

    def _select_cases(self, tier: EvalTier) -> list[EvalCase]:
        if tier == EvalTier.CI_FAST:
            return self.golden_dataset[:50]
        elif tier == EvalTier.CI_SLOW:
            return self.golden_dataset[:200]
        return self.golden_dataset

    async def _get_model_response(self, model_id: str, case: EvalCase) -> str:
        response = self.llm.messages.create(
            model=model_id, max_tokens=2048,
            messages=[{"role": "user", "content": case.input_text}],
        )
        return response.content[0].text

    async def _judge_response(self, case: EvalCase, response: str,
                               tier: EvalTier) -> EvalScore:
        judge_prompt = (
            f"Score this response on a 1-5 scale for correctness.\n"
            f"Question: {case.input_text}\n"
            f"Expected: {case.expected_output}\n"
            f"Response: {response}\n"
            f"Output JSON: {{\"score\": <1-5>, \"reasoning\": \"<brief>\"}}"
        )

        import time
        start = time.time()
        judge_response = self.llm.messages.create(
            model=self.judge_model, max_tokens=200,
            messages=[{"role": "user", "content": judge_prompt}],
        )
        elapsed_ms = (time.time() - start) * 1000

        parsed = json.loads(judge_response.content[0].text)
        return EvalScore(
            case_id=case.metadata.get("id", ""),
            score=parsed["score"],
            judge_model=self.judge_model,
            reasoning=parsed.get("reasoning", ""),
            latency_ms=elapsed_ms,
            tokens_used=judge_response.usage.input_tokens + judge_response.usage.output_tokens,
        )

    def _bootstrap_ci(self, values: list[float], n_bootstrap: int = 1000,
                       alpha: float = 0.05) -> tuple[float, float]:
        n = len(values)
        boot_means = []
        for _ in range(n_bootstrap):
            sample = [values[random.randint(0, n - 1)] for _ in range(n)]
            boot_means.append(sum(sample) / n)
        boot_means.sort()
        lower_idx = int(n_bootstrap * (alpha / 2))
        upper_idx = int(n_bootstrap * (1 - alpha / 2))
        return boot_means[lower_idx], boot_means[upper_idx]

    def _estimate_cost(self, score: EvalScore) -> float:
        price_per_token = 0.10 / 1_000_000  # Flash-class default
        return score.tokens_used * price_per_token
```

### 5.2 Drift Detector for Production Monitoring

```python
import math
from dataclasses import dataclass


@dataclass
class DriftAlert:
    drift_type: str
    magnitude: float
    threshold: float
    triggered: bool
    details: str


class DriftDetector:
    def __init__(self, baseline_centroid: list[float], baseline_std: float,
                 probe_fingerprints: dict[str, str], alert_std_devs: float = 2.0):
        self.baseline_centroid = baseline_centroid
        self.baseline_std = baseline_std
        self.probe_fingerprints = probe_fingerprints
        self.alert_std_devs = alert_std_devs

    def check_input_drift(self, recent_centroid: list[float]) -> DriftAlert:
        distance = self._cosine_distance(self.baseline_centroid, recent_centroid)
        threshold = self.baseline_std * self.alert_std_devs
        return DriftAlert(
            drift_type="input_distribution",
            magnitude=round(distance, 4),
            threshold=round(threshold, 4),
            triggered=distance > threshold,
            details=(
                f"Cosine distance {distance:.4f} between 30-day baseline "
                f"and 7-day rolling centroid (threshold: {threshold:.4f})"
            ),
        )

    def check_model_drift(self, current_fingerprints: dict[str, str]) -> DriftAlert:
        changed = [
            k for k in self.probe_fingerprints
            if self.probe_fingerprints[k] != current_fingerprints.get(k, "")
        ]
        magnitude = len(changed) / len(self.probe_fingerprints) if self.probe_fingerprints else 0
        return DriftAlert(
            drift_type="model_behavioral",
            magnitude=round(magnitude, 4),
            threshold=0.2,
            triggered=magnitude > 0.2,
            details=(
                f"{len(changed)}/{len(self.probe_fingerprints)} probe responses "
                f"changed since baseline"
            ),
        )

    def check_retrieval_drift(self, baseline_overlap: float,
                               current_overlap: float) -> DriftAlert:
        drop = baseline_overlap - current_overlap
        return DriftAlert(
            drift_type="retrieval_corpus",
            magnitude=round(drop, 4),
            threshold=0.15,
            triggered=drop > 0.15,
            details=(
                f"Retrieval overlap dropped from {baseline_overlap:.2%} "
                f"to {current_overlap:.2%}"
            ),
        )

    def _cosine_distance(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return 1.0 - (dot / (norm_a * norm_b))
```

### 5.3 CI/CD Quality Gate

```python
from dataclasses import dataclass


@dataclass
class GateDecision:
    passed: bool
    regressions: list[dict]
    improvements: list[dict]
    summary: str


class CICDQualityGate:
    def __init__(self, regression_threshold: float = 0.05,
                 min_cases_for_gate: int = 20):
        self.regression_threshold = regression_threshold
        self.min_cases = min_cases_for_gate

    def evaluate(self, current: dict, baseline: dict) -> GateDecision:
        regressions = []
        improvements = []

        for metric_name, current_score in current.items():
            baseline_score = baseline.get(metric_name)
            if baseline_score is None:
                continue

            diff = current_score - baseline_score
            entry = {
                "metric": metric_name,
                "baseline": round(baseline_score, 3),
                "current": round(current_score, 3),
                "diff": round(diff, 3),
                "diff_pct": round(diff / baseline_score * 100, 1) if baseline_score else 0,
            }

            if diff < -self.regression_threshold * baseline_score:
                regressions.append(entry)
            elif diff > self.regression_threshold * baseline_score:
                improvements.append(entry)

        passed = len(regressions) == 0
        summary = self._format_summary(passed, regressions, improvements)

        return GateDecision(
            passed=passed,
            regressions=regressions,
            improvements=improvements,
            summary=summary,
        )

    def _format_summary(self, passed: bool, regressions: list,
                         improvements: list) -> str:
        status = "PASS" if passed else "BLOCKED"
        lines = [f"## Eval Gate: {status}\n"]

        if regressions:
            lines.append("### Regressions (blocking)")
            for r in regressions:
                lines.append(
                    f"- **{r['metric']}**: {r['baseline']:.3f} → {r['current']:.3f} "
                    f"({r['diff_pct']:+.1f}%)"
                )

        if improvements:
            lines.append("\n### Improvements")
            for i in improvements:
                lines.append(
                    f"- **{i['metric']}**: {i['baseline']:.3f} → {i['current']:.3f} "
                    f"({i['diff_pct']:+.1f}%)"
                )

        if not regressions and not improvements:
            lines.append("No significant changes detected.")

        return "\n".join(lines)
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Eval-Driven Development Platform for a 50-Person AI Team

**Business context**: An AI company with 50 engineers ships 3 LLM-powered products (chatbot, code assistant, RAG search). Each product has 5–10 prompts iterated weekly. Current process: engineers eyeball 5–10 examples after prompt changes, leading to silent regressions caught by customers. Requirements: catch regressions before merge, track quality over time across all products, $5K/month eval infrastructure budget, integrate with GitHub Actions CI, and support both automated and human-in-the-loop evaluation.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     EVAL-DRIVEN DEVELOPMENT PLATFORM                     │
 │                                                                          │
 │  Engineer ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌───────────┐ │
 │  (PR push)    │ GitHub Action│     │ Eval Runner  │     │ Gate      │ │
 │               │              │     │              │     │ Decision  │ │
 │               │ Trigger eval │     │ Fast tier:   │     │           │ │
 │               │ workflow     │     │ deterministic│     │ Block if  │ │
 │               │              │     │              │     │ >5% drop  │ │
 │               │              │     │ Slow tier:   │     │ on any    │ │
 │               │              │     │ LLM-judge    │     │ core      │ │
 │               │              │     │ + bootstrap  │     │ metric    │ │
 │               └──────────────┘     │ CI           │     │           │ │
 │                                    └──────────────┘     └─────┬─────┘ │
 │                                                               │       │
 │                                                    ┌──────────▼─────┐ │
 │                                                    │ PR Comment     │ │
 │                                                    │ - Score diff   │ │
 │                                                    │ - Regressions  │ │
 │                                                    │ - CI widths    │ │
 │                                                    └────────────────┘ │
 │                                                                       │
 │  ┌─────────────────────────────────────────────────────────────────┐  │
 │  │  MONITORING LAYER                                               │  │
 │  │  - Production traffic sampling (1-5%)                           │  │
 │  │  - Drift detection (input, model, retrieval)                    │  │
 │  │  - Quality score dashboard over time                            │  │
 │  │  - Failure triage → golden dataset import                       │  │
 │  └─────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Commercial Platform (Braintrust) | B: Two-Tool OSS + Platform (Recommended) | C: Fully Custom Build |
|-----------|-------------------------------------|------------------------------------------|----------------------|
| **Regression catch rate** | ⬛⬛⬛ — Built-in merge blocking, score summaries | ⬛⬛⬛ — DeepEval for CI gates + Langfuse for monitoring | ⬛⬛⬜ — Depends on build quality |
| **Setup time** | ⬛⬛⬛ — Days (managed platform) | ⬛⬛⬜ — 1–2 weeks (OSS integration) | ⬛⬜⬜ — 2–3 months |
| **Cost at scale (50 eng, 3 products)** | ⬛⬛⬜ — $249/mo platform + judge tokens (~$2K/mo total) | ⬛⬛⬛ — $0 platform + judge tokens (~$500–1K/mo) + hosting (~$500/mo) | ⬛⬛⬜ — $0 tools + eng time (~$30K/mo in FTE) |
| **Flexibility / customization** | ⬛⬛⬜ — Platform constraints; custom scorers possible | ⬛⬛⬛ — Full control over eval logic, metrics, dashboards | ⬛⬛⬛ — Unlimited |
| **Production monitoring** | ⬛⬛⬛ — Built-in tracing + scoring | ⬛⬛⬛ — Langfuse for traces + drift detection | ⬛⬛⬜ — Must build drift/alerting |
| **Vendor risk** | ⬛⬛⬜ — Single vendor dependency | ⬛⬛⬛ — OSS core; platform optional | ⬛⬛⬛ — No vendor dependency |

**Recommended approach**: **B (Two-Tool OSS + Platform)**.

**Decision rationale**: The two-tool pattern is the empirically validated convergence point for experienced teams. DeepEval provides tests-as-code with pytest integration for CI gates — engineers write eval assertions alongside their prompt changes, and GitHub Actions blocks merges when regressions exceed 5%. Langfuse (self-hosted via Docker Compose, ~$500/month hosting) provides nested trace observability, LLM-as-judge evaluators configured in UI, and production quality tracking across all 3 products. Total cost: ~$1.5K/month (hosting + judge tokens), well within the $5K budget. Option A (Braintrust) is faster to set up but creates vendor lock-in at $249/month platform fees plus per-seat costs that grow with the team. Option C is the most flexible but the 2–3 month build time and ongoing FTE cost (~$30K/month) far exceeds the budget — the team should be shipping products, not building eval infrastructure.

### 6.2 Scenario: Regulatory-Compliant Evaluation System for Healthcare AI

**Business context**: A healthcare AI company deploys clinical decision support agents across 200 hospitals. Regulatory requirements: FDA's AI/ML-Based Software as a Medical Device framework, EU AI Act high-risk classification, HIPAA compliance. Must demonstrate model safety and efficacy through rigorous evaluation. Requirements: maintain 99%+ accuracy on clinical guidelines, detect model drift within 24 hours, immutable audit trail for regulatory inspection, support for quarterly re-evaluation with expert clinical reviewers, and budget of $200K/year for evaluation infrastructure.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                HEALTHCARE AI EVALUATION SYSTEM                           │
 │                                                                          │
 │  ┌────────────────────────────────────────────────────────────────────┐  │
 │  │  SAFETY EVALUATION PIPELINE                                        │  │
 │  │                                                                    │  │
 │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
 │  │  │ Clinical │  │ LLM Judge│  │ Clinician │  │ Regulatory       │  │  │
 │  │  │ Dataset  │  │ Scoring  │  │ Review    │  │ Report Generator │  │  │
 │  │  │ (MedHELM │  │ (3-model │  │ (quarterly│  │ - FDA 510(k)     │  │  │
 │  │  │  + custom│  │  ensemble)│  │  panel)   │  │ - EU AI Act      │  │  │
 │  │  │  121     │  │          │  │          │  │ - Audit trail    │  │  │
 │  │  │  tasks)  │  │          │  │          │  │                  │  │  │
 │  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │  │
 │  └────────────────────────────────────────────────────────────────────┘  │
 │                                                                          │
 │  ┌────────────────────────────────────────────────────────────────────┐  │
 │  │  CONTINUOUS MONITORING                                             │  │
 │  │  - 5% of clinical queries evaluated by LLM judge                  │  │
 │  │  - Drift detection: input distribution + model behavioral + RAG   │  │
 │  │  - Alert within 24h of quality degradation                        │  │
 │  │  - All outputs logged to WORM storage (HIPAA compliant)           │  │
 │  └────────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: General Eval Framework + Custom | B: Healthcare-Specialized Eval (Recommended) | C: Fully Manual Clinical Review |
|-----------|-----------------------------------|----------------------------------------------|-------------------------------|
| **Regulatory compliance** | ⬛⬛⬜ — Must add custom compliance; audit trail DIY | ⬛⬛⬛ — Built-in MedHELM integration; WORM audit; FDA-ready reports | ⬛⬛⬛ — Gold standard but doesn't scale |
| **Accuracy on clinical guidelines** | ⬛⬛⬜ — General judge may miss clinical nuances | ⬛⬛⬛ — 3-model ensemble with clinical calibration set (500 examples, expert-labeled) | ⬛⬛⬛ — 100% clinical expert judgment |
| **Drift detection speed** | ⬛⬛⬜ — General drift tools; <48h | ⬛⬛⬛ — Clinical-specific probes; <24h | ⬛⬜⬜ — Monthly at best |
| **Cost at 200 hospitals** | ⬛⬛⬛ — ~$100K/year (OSS + judge tokens) | ⬛⬛⬛ — ~$180K/year (specialized tooling + expert reviews) | ⬛⬜⬜ — ~$1M/year (clinical FTEs) |
| **Scalability** | ⬛⬛⬛ — Automated; scales linearly | ⬛⬛⬛ — Automated + quarterly expert review | ⬛⬜⬜ — 200 hospitals × manual = infeasible |
| **Explainability for regulators** | ⬛⬛⬜ — General reasoning; not clinical-aware | ⬛⬛⬛ — Clinical-specific rubrics; expert-calibrated scores; provenance chain | ⬛⬛⬛ — Full clinical reasoning |

**Recommended approach**: **B (Healthcare-Specialized Eval)**.

**Decision rationale**: Regulatory requirements (FDA, EU AI Act, HIPAA) make Option A insufficient — general eval frameworks lack clinical calibration and audit trail depth. Option C is the accuracy gold standard but at 200 hospitals, manual review costs ~$1M/year and can't provide continuous monitoring. Option B combines automated LLM-judge scoring (3-model ensemble, calibrated quarterly against 500 expert-labeled clinical cases, Cohen's kappa >0.85 required) with continuous drift detection (clinical-specific probe sets run every 4 hours, alert within 24 hours). MedHELM's 121 clinical tasks provide the standardized benchmark component. All outputs and evaluations log to HIPAA-compliant WORM storage for FDA inspection. Quarterly expert panels (4 clinicians × 2 days × 4 quarters = 32 clinician-days/year at ~$2K/day = ~$64K/year for expert reviews). Total: ~$180K/year within the $200K budget. The key architectural decision: the continuous automated evaluation runs at 5% sampling rate with <24h drift alerting, while quarterly clinical panels recalibrate the automated judges and validate that the system still meets clinical standards — the automated system watches daily, the experts verify quarterly.

---

*Module 12 complete. Covers eval taxonomy, benchmark landscape (17 benchmarks), LLM-as-judge (biases, calibration, patterns), 10 evaluation frameworks, three-layer agentic evaluation, statistical methods (bootstrap, Bradley-Terry, multiple comparisons), eval-driven development, RAG evaluation, anti-patterns (Goodhart's Law, benchmaxxing, contamination), production monitoring (drift, shadow, A/B, canary), and cost analysis.*
