# Module 04: LLM and Agent Evaluation

## What Is This?

Imagine you hire a new employee. You would not just trust them blindly -- you would review their work, test them on known problems, and have a senior colleague grade their answers before they go live with customers. LLM evaluation is exactly that, but for AI systems: you feed the system known inputs, score its outputs against criteria (using rules, another LLM as "judge," or humans), and decide whether it is good enough to ship. The twist is that unlike testing traditional software where 2 + 2 must equal 4, LLM outputs are probabilistic and open-ended, so the field has developed specialized paradigms -- pointwise scoring, pairwise comparison, reference-based checking -- and layered them into CI/CD pipelines that gate deployments on quality thresholds. Agent evaluation adds another dimension: you are no longer scoring a single response but an entire multi-step workflow -- did the agent pick the right tools, call them correctly, recover from errors, and ultimately achieve the user's goal?

Quality regressions hit 40% of LLM deployments within 90 days. Without systematic evaluation, you discover failures from customer complaints, not dashboards. Evaluation is the mechanism that transforms an AI demo into a production system.

---

## Part 1: System Topology and Data Flow

### Three-Plane Architecture

An evaluation system has three planes operating on two different clocks:

```
                          EVAL CLOCK (batch, async)                 USER CLOCK (real-time)
                ┌──────────────────────────────────┐      ┌──────────────────────────────────┐
                │         CONTROL PLANE             │      │          DATA PLANE               │
                │                                   │      │                                   │
                │  Dataset (as_of pin)              │      │  Production agent (same scaffold  │
                │  Runner  (k trials, env reset)    │      │    as CI, sim MCP only in eval)   │
                │  Scorer  (hard + soft oracles)    │      │  Tracer (OTLP spans on every req) │
                │  Gate    (pass^k, coverage%)      │      │  Sidecar judge (async, sampled)   │
                │  Stats   (paired vs baseline,     │      │                                   │
                │           bootstrap if n<200)     │      │  0 ms eval tax on user path       │
                └───────────────┬──────────────────┘      └──────────────┬───────────────────┘
                                │                                        │
                                └──────────────┬─────────────────────────┘
                                               v
                              ┌──────────────────────────────────┐
                              │        PERSISTENCE LAYER          │
                              │                                   │
                              │  Experiment store (run config,    │
                              │    model, prompt hash, per-row)   │
                              │  Dataset store (git-versioned,    │
                              │    content-addressable)           │
                              │  Audit log (immutable, tamper-    │
                              │    evident, exportable to SIEM)   │
                              └──────────────────────────────────┘
```

**Why three planes?** The control plane runs the eval harness on the eval clock (batch, nightly, or per-PR). The data plane serves real users on the user clock (real-time). A judge sidecar scores sampled production traces asynchronously -- it must never sit on the user request path. This separation is the single most important architectural decision: user-path eval tax must be **0 ms**.

### Evaluated System = Product Tuple

A score is meaningless without its full context. The evaluated system is not "the model." It is:

**Model x Scaffold x Tools x Environment x Judge x Sampling x Retries x Infra**

If any term changes, the score changes. Anthropic showed that infra alone (Docker image, RAM, network) accounts for **6 pp** on Terminal-Bench. SWE-agent's ACI shell interface vs raw bash on the same GPT-4 Turbo moved scores **+64% relative** (12.47% absolute, 286/2,294 instances). A `run_key` must therefore include `(suite_id, dataset_version, harness_commit, judge_model, trial_id)` -- a scaffold change is a new measurement.

### Request-Flow Narrative

1. **Dataset loads**: Golden set pinned by `as_of` content hash. Every discovered production bug auto-promotes into the regression suite after PII redaction.
2. **Runner dispatches**: k independent trials per task, each with a fresh environment snapshot (Docker/DB reset). Concurrency limits and exponential backoff prevent judge rate-limit cascading.
3. **Hard oracle fires first**: Deterministic checks -- DB goal-state assertions, policy-cap compliance, JSON schema validation, hidden unit tests. Milliseconds, zero ambiguity. If hard oracle fails, soft oracle is skipped (never averaged).
4. **Soft oracle fires second**: LLM-as-judge rubric scores tone, completeness, and helpfulness on outputs that survived hard checks. Runs asynchronously. In online mode, a tripped judge breaker skips the score rather than failing the user's request.
5. **Aggregator persists**: Full experiment config, per-row results, aggregate scores, `grader_status` (scored/skipped/error/timeout). Unscored rows are never counted as passed.
6. **Gate decides**: `pass^k` on hard oracle, soft threshold with coverage floor, paired comparison vs last shipped version. A 50-item golden set cannot support a 3 pp claim (Miller worked example: n ~ 969 for 3 pp MDE).
7. **Feedback loop closes**: Failing online traces flow to an annotation queue (redacted), get labeled, and join the regression dataset.

### Three Evaluation Paradigms

| Paradigm | Mechanism | Strength | Weakness | Best For |
|---|---|---|---|---|
| **Pointwise** | Absolute score (1-5) against rubric | Simple, threshold-ready | Susceptible to prompt variation, randomness | Quality gates, production monitoring |
| **Pairwise** | Judge picks better of two responses | More stable (relative easier than absolute) | 2x inference cost (both orderings required), no absolute threshold | A/B testing model versions, prompt variants |
| **Reference-Based** | Compare against gold-standard answer | Most objective | Requires curated reference datasets | Factual accuracy, structured extraction |

**Real-world example**: When comparing two prompt variants for a customer support bot, use pairwise (evaluate both orderings, count only consistent verdicts). When gating a PR merge, use pointwise against your rubric with a threshold (e.g., avg >= 0.85). When checking RAG faithfulness against retrieved documents, use reference-based (RAGAS entailment).

---

## Part 2: Core Mechanics and Algorithms

### pass@k -- Capability Envelope

**What it measures**: The probability that at least one of k independent samples passes. It answers "can the system do this at all?"

**Unbiased estimator** (Chen et al., HumanEval):

```
pass@k = 1 - C(n-c, k) / C(n, k)
```

Where n = total samples generated, c = number that passed, C = binomial coefficient.

**Why not the naive formula?** The naive `1 - (1 - c/n)^k` is biased upward for small n. The Chen product uses combinatorial exact counting. **Critical edge case**: When n < k, the estimator is undefined. The UK AI Safety Institute's Inspect framework correctly returns NaN rather than extrapolating -- this is the right behavior, not a bug.

**Real-world example**: You generate n=10 solutions for a coding problem and c=4 pass the unit tests. pass@1 = 1 - C(6,1)/C(10,1) = 0.4. pass@5 = 1 - C(6,5)/C(10,5) = 1 - 6/252 = 0.976. The system almost certainly *can* solve it if you sample enough -- but can it solve it reliably?

### pass^k -- Reliability Metric

**What it measures**: The probability that ALL k independent trials succeed. This is what users experience -- they get one try.

**Estimator** (Inspect, without replacement):

```
pass^k = C(c, k) / C(n, k)
```

**Why this matters more than pass@k for production**: Users live on pass^1. The gap between pass@k and pass^k can be enormous:

| Benchmark | pass@1 | pass^5 | Gap |
|---|---|---|---|
| tau-airline (GPT-4o class, baseline) | 0.332 | 0.100 | 23.2 pp |
| tau-airline (think+prompt) | 0.584 | 0.340 | 24.4 pp |
| tau-retail (Think) | 0.812 | 0.626 | 18.6 pp |
| Original tau-retail pass^8 | -- | <25% | -- |
| Randomness study max gap | -- | -- | **24.9 pp** |

**Key insight**: pass@k requires a verifier (unit tests, DB state check) to pick the correct sample. If you only have an LLM judge, you cannot reliably select which of k samples is correct -- the judge is not a verifier. Gate on pass^k and report pass@k as the capability envelope.

### Temperature 0 Does Not Mean Deterministic

The Randomness paper analyzed ~60,000 trajectories: standard deviation still exceeds **1.5 pp at T=0**; single-run pass@1 ranges **2.2-6.0 pp**; trajectories diverge in the first ~1% of tokens due to aleatoric noise plus engine/environment nondeterminism. Treat a 31% to 33% single-run "win" as noise, not a result. Estimate pass@1 from multiple independent runs and report confidence intervals.

### Dual-Oracle Pattern

The enterprise default for any agent that touches money, policy, or safety:

```
┌─────────────┐     ┌──────────────────────────────────────┐
│ Task Output  │────>│ Hard Oracle (deterministic)           │
│              │     │  - DB goal-state assertions           │
│              │     │  - Policy cap compliance              │
│              │     │  - JSON/AST schema validation         │
│              │     │  - Hidden unit tests                  │
│              │     │  - Citation ID in retrieved set       │
│              │     │                                       │
│              │     │  Verdict: PASS / FAIL / ERROR         │
│              │     └──────────────┬───────────────────────┘
│              │                    │
│              │          hard_fail ──> skip soft, never average
│              │          hard_pass ──> proceed to soft
│              │                    │
│              │     ┌──────────────v───────────────────────┐
│              │────>│ Soft Oracle (LLM judge, async)        │
│              │     │  - Rubric: tone, completeness, PII    │
│              │     │  - Position-swapped, CoT rationale    │
│              │     │  - Breaker: skip online, error in CI  │
│              │     │  - Score: 0.0-1.0 continuous           │
│              │     └──────────────┬───────────────────────┘
│              │                    │
│              │     ┌──────────────v───────────────────────┐
│              │     │ Gate Decision                         │
│              │     │  hard.passed AND soft.score >= 0.85   │
│              │     │  AND coverage >= 0.95                 │
│              │     │  Result: SHIP / HOLD / INSPECT        │
│              │     └──────────────────────────────────────┘
└─────────────┘
```

**Why dual-oracle?** Hard-only ships "correct but hostile" and misses PII-in-logs. Soft-only ships "pretty wrong" (ARE whole-trace judge precision is only **0.53**). Safety is never in the mean -- a 0.70 safety score hidden inside "quality 0.93" is a lawsuit.

**Real-world example**: A refund agent correctly processes a refund (hard oracle passes) but tells the customer "I processed your stupid refund, stop bothering us" (soft oracle catches the hostile tone). Hard-only would ship this. Conversely, an agent says "I've processed your refund!" warmly but actually booked the wrong fare class (soft oracle passes, hard oracle catches the DB state mismatch). Soft-only would ship this.

### Six Evaluation Dimensions

Every agent eval should measure across these six axes. Collapsing them into a single number hides failures:

| Dimension | What It Measures | Hard or Soft | Example Check |
|---|---|---|---|
| **Task Success** | Did the agent achieve the user's goal? | Hard (DB/test) | Refund row exists in DB with correct amount |
| **Trajectory Quality** | Was the path efficient and correct? | Soft (LLM judge) | Geometric mean of per-step scores >= 3.5/5 |
| **Tool Accuracy** | Did the agent call the right tools correctly? | Hard (AST/schema) | Selection acc >= 95%, arg correct >= 90% |
| **Output Quality** | Is the response helpful, coherent, safe? | Soft (rubric) | Faithfulness, tone, no PII leakage |
| **Cost** | What did this task cost? | Hard (metering) | Per-task $ within 2x of baseline |
| **Latency** | How long did it take? | Hard (tracing) | p95 < 30s end-to-end |

### RAGAS Metrics for RAG Evaluation

RAGAS provides claim-level evaluation for RAG systems. Understanding the specific constructs prevents common confusion:

| Metric | Construct | WikiEval Agreement | Ship-Gate? |
|---|---|---|---|
| **Faithfulness** | Entailment: each claim in the answer is supported by retrieved context | ~95% | Yes (hard) |
| **Context Recall** | What fraction of reference answer claims appear in retrieved context | -- | Yes (complementary to faithfulness) |
| **Context Precision** | Are retrieved docs relevant to the question? (ID-based) | -- | Monitor |
| **Answer Relevancy** | Is the answer relevant to the question? (cosine) | ~78% | No -- too noisy for gating |
| **Context Relevance** | -- | ~70% | Monitor only |

**Critical footgun**: Faithfulness can be 1.0 on the **wrong documents**. If your retriever pulls irrelevant chunks, the generator can be perfectly faithful to them while being factually wrong. Always pair faithfulness with context recall. Additionally, RAGAS faithfulness is entailment vs **retrieved context**, not world truth -- it will not catch answers that are faithful to retrieved docs but wrong about reality. That is a different construct (SimpleQA measures world-fact accuracy).

**DeepEval trap**: DeepEval's default `FaithfulnessMetric` treats "I don't know" as supported and empty verdicts as 1.0 unless you set `penalize_ambiguous_claims=True`. This silently inflates scores. RAGAS entailment does not have this default.

**Citation validation**: Citations are a **schema/constraint** problem, not an LLM-judge problem. Check that citation IDs are a subset of the retrieved document set deterministically. RAGAS will not catch a bare invented `[doc 17]`.

**RAGAS Faithfulness walkthrough** (Einstein example): Answer claim "Einstein was born in Germany" gets a faithfulness score of 0.5 if only one of two claims is entailed by context. The metric decomposes the answer into atomic claims, checks each against context via NLI, and averages.

### Agent Evaluation: Three-Layer Stack

**Layer 1 -- End-to-End (Task Completion)**

Binary or graded assessment of goal achievement. Success Rate (SR) is the primary metric. Critical distinction: execution completion is not task success. An agent that runs all steps but produces wrong output has 100% execution completion and 0% task success. Keep these metrics separate.

**Layer 2 -- Trajectory Scoring**

Scores the sequence of (state, action) pairs across the agent's execution. The 2026 standard uses an LLM judge scoring each pair on a 1-5 scale; the trajectory score is the **geometric mean** (not arithmetic).

**Why geometric mean?** It punishes any single bad step severely:
- **Arithmetic mean**: One catastrophic step (score=1) among 19 fine steps (score=5) gives (19 x 5 + 1) / 20 = **4.8** -- the bad step nearly vanishes.
- **Geometric mean**: (5^19 x 1)^(1/20) = **4.17** -- the single failure drags the score down meaningfully.

In financial contexts, one incorrect refund step among 19 correct steps must not vanish in an average. Geometric mean ensures this.

Trajectory evaluation modes:
- **Exact matching**: Fixed workflows where step order is prescribed
- **Set-based matching**: Order-flexible -- correct steps in any sequence
- **Partial-credit**: Fractional credit for partially correct actions
- **LLM judge**: When multiple valid paths exist (most production scenarios)

**Lucky pass detection**: AgentLens found that **10.7%** (range 0.5-23.2%) of agent passes are "lucky" -- the agent reached the right answer through an incorrect process. Process overlay (forbidden-action counts, trajectory scoring) catches these.

**Layer 3 -- Component-Level (Tool Call Accuracy)**

Decomposes into four sub-metrics:

| Sub-Metric | Target | What It Catches |
|---|---|---|
| **Selection accuracy** | >= 95% | Agent picked wrong tool |
| **Argument correctness** | >= 90% | Args syntactically/semantically invalid |
| **Repetition rate** | < 5% | Duplicate calls wasting tokens/money |
| **Error recovery** | Qualitative | Did agent recover from failed calls? |

**BFCL (Berkeley Function Calling Leaderboard)** V4 bucket weights: 40/30/10/10/10. Tool-call evaluation uses AST matching (not string comparison) because argument order and formatting can vary without changing semantics.

### LLM-as-Judge Bias Taxonomy

Understanding judge biases is essential because an uncalibrated judge can show perfect dashboards while diverging from expert review. Each bias has a known mitigation:

| Bias | Severity | Detection | Mitigation |
|---|---|---|---|
| **Position bias** | High | GPT-4 flipped preference on ~1/3 of MT-Bench pairwise cases when order was swapped | Evaluate both orderings; only count consistent verdicts. Use "Response A/B" not "1/2". **Cost: 2x inference, not optional.** |
| **Length/verbosity bias** | Medium-High | Correlation analysis between response length and judge scores; HealthBench found significant length correlation | Explicit rubric: "concise responses score equal to or better than verbose at equivalent correctness." Length-controlled win rate for pairwise. |
| **Self-preference bias** | Medium | Model rates own-style outputs higher by **+10%** (GPT-4) to **+25%** (Claude-v1) vs human ratings | Use different model family for judging than generation. Cross-model evaluation panels. |
| **Rubric position bias** | Medium (2026 finding) | Reordering criteria within rubrics shifts scores | Randomize rubric option ordering across eval runs. |
| **Compounding (FairJudge, Feb 2026)** | Critical | Position, length, formatting, and model provenance all shape verdicts. Frontier models exceeded **50% error rates** on bias tests. | FairJudge approach: SFT for base judge behavior, DPO targeting non-semantic biases, GRPO enforcing consistency across scoring modes. |

**Few-shot helps consistency, not human agreement**: Zheng showed few-shot raised GPT-4 swap consistency from 65% to 77.5% at 4x cost -- but did not lift agreement with human judges. More expensive judging is not automatically better judging.

**Chain-of-thought improves agreement**: Asking the judge to write a one-paragraph rationale before emitting the grade lifts inter-judge kappa from ~0.55 to ~0.75 on retrieval relevance tasks.

**When NOT to use LLM-as-judge**: If a deterministic oracle exists (math correctness, code unit tests, JSON schema, DB state, citation ID membership), use that. ARE showed write-oracle agreement **0.98** vs whole-trace LLM judge **0.72**; precision **0.99** vs **0.53**. LLM judges are for the residual where no deterministic check is possible.

### Human Evaluation: Inter-Annotator Agreement

| Task Type | Metric | Target kappa |
|---|---|---|
| Objective (classification, entity extraction) | Cohen's/Fleiss' kappa | >= 0.90 |
| Moderately subjective (relevance, coherence) | Cohen's/Fleiss' kappa | 0.70 - 0.85 |
| Inherently subjective (creativity, style) | Krippendorff's alpha | 0.60 - 0.75 |

**Kappa prevalence paradox**: Severe class imbalance produces surprisingly low kappa despite high raw agreement. If 90% of outputs are "pass," a judge that always says "pass" hits 90% raw agreement but kappa near zero. Always report prevalence alongside kappa.

**Calibration protocol**:
1. Written guidelines with 10-20 worked examples
2. Calibration sessions: annotators independently rate gold examples, discuss disagreements
3. Agreement gate: kappa > 0.6 on calibration set before production annotation begins
4. Ongoing monitoring: 5-15% overlap, rolling IAA, quarterly recalibration

**HealthBench anchor**: Physician-physician (MD-MD) agreement is only **55-75%** on medical quality rubrics. The benchmark has median **11** criteria per conversation, **48,562** unique criteria, and **5,000** conversations. Top model scores: o3 ~60%, GPT-4o ~32%, GPT-3.5-Turbo ~16%. HealthBench kept GPT-4.1 over o3 as grader because meta-eval F1 was higher (**0.709**) and cheaper -- reasoning models are not automatically better judges.

### Benchmark Families and Contamination

| Benchmark | Tasks | What It Measures | Key Numbers |
|---|---|---|---|
| **HumanEval** | 164 | Code generation (pass@k) | n=200 samples, 7.7 tests avg; HumanEval+ added 80x tests, dropped pass@k by **19.3-28.9%** |
| **SWE-bench** | 2,294 (Lite 300, Verified 500) | Issue resolution against tests | Pro climbed 23.3% to 80.3% in 8 months; ~30% of tests broken; 19.78% semantically incorrect |
| **tau-bench** | Retail + airline + telecom | Goal-state correctness in task envs | GPT-4.1: retail 74%, airline 56%, telecom 34% pass^1 |
| **GAIA** | 466 questions | Broad tool-using reasoning | Humans 92%, GPT-4+plugins 15% |
| **GAIA2** | 800 scenarios x 10 universes x 101 tools | Multi-tool agent eval | 160 mini set; attaches MCP |
| **BFCL** | Function calling correctness | Tool/function call accuracy | V4 bucket weights: 40/30/10/10/10 |
| **HealthBench** | 5,000 conversations | Medical rubric-heavy | 48,562 criteria; 55k grader calls/model |
| **SimpleQA** | 4,326 items | World-fact accuracy | Informal grader disagreements: 2/300 |
| **ARE** | Agent reasoning evaluation | Verifier vs whole-trace | Write-oracle 0.98 vs whole-trace 0.72 |

**Contamination is real**:
- **GSM1k vs GSM8k**: **8 pp** contamination gap -- models score higher on the public set they have seen.
- **SWE-bench Verified**: OpenAI stopped reporting because models regurgitate gold patches.
- **UC Berkeley RDI (April 2026)**: Automated scanning agent broke all eight major agent benchmarks (SWE-bench, WebArena, OSWorld, GAIA, Terminal-Bench, FieldWorkArena, CAR-bench) by reward hacking -- achieving near-perfect scores without solving a single task.
- **SWE-bench inflation**: 5-15 points from training-data leakage. OpenAI audit: 59.4% of hardest Verified tasks have tests that would not catch the intended bug. A 90% headline score is closer to 75-80% real capability.

**Takeaway**: Build internal private evals (50-200 representative tasks from your actual product). Weight independent evaluations heavily and lab marketing lightly.

### Statistical Hygiene

- **Unit = task**, not step. n=50 SWE instances is not n=1,000 steps. Pseudo-replication inflates statistical power.
- **n < few hundred means no CLT**: Bootstrap confidence intervals instead.
- **Miller worked example**: n ~ 969 needed for a 3 pp MDE (minimum detectable effect). Going from K=1 to K=10 retries on n=198 only reduces MDE from 13.2% to 7.5%.
- **Paired comparison**: Compare candidate and baseline on the **same** tasks. Report paired SE, not independent SE.
- **Report intervals, not points**: "83% +/- 2.1 pp (95% CI)" is a result. "83%" alone is not.

---

## Part 3: Token Economics and NFR Analysis

### Cost of Evaluation

**Judge cost guardrail**: Keep judge cost under 10-15% of production LLM cost. Act (reduce sampling or downgrade judge model) if approaching 25%.

**1,000 judge calls** (2k input / 200 output per call):

| Judge Model | Input Rate | Output Rate | Cost per 1k Calls | Notes |
|---|---|---|---|---|
| Claude Sonnet | $3/MTok | $15/MTok | ~$9 | Standard eval judge |
| Claude Sonnet (cached prefix) | $0.30/MTok (cached) | $15/MTok | ~$1.10 | 90% input savings with stable rubric |
| Claude Opus 5 | $5/MTok | $25/MTok | ~$15 | Highest accuracy, calibration runs |

**Agent eval cost multipliers** (tau-style agent, 8k input / 1.5k output, 70% cache read on 6k prefix):

| Component | Cost per 1k Tasks | Notes |
|---|---|---|
| Agent execution | ~$26 | Model calls + tool sim |
| + LangSmith extended traces | ~$5 | $0.50 per 1k base, $5 extended |
| + Judge (Sonnet) | ~$9 | 2k/200 rubric calls |
| **Total per 1k tasks** | **~$40** | Without pass^k |
| pass^5 multiplier | ~$48 agent alone | Agent+sim cost x ~4 (some env reuse) |
| Nightly 200-task envelope | ~$12 uncached | Smoke test budget |

**Benchmark suite costs**:

| Suite | Tasks | Cost per Model per Run |
|---|---|---|
| SWE-bench Verified | 500 | $50 - $200 (depends on agent loop length) |
| GAIA | 450+ | $30 - $100 |
| HealthBench | 5,000 | 55k grader calls -- judge line is not rounding error |
| Custom private eval | 200 | $5 - $20 (Sonnet-class judge) |
| HAL tau-airline | varies | o4-mini High $11.36 vs Opus 4.1 $180.49 (snapshot) |

**Online eval cost** (1M requests/month, 1% sampling):

| Component | Monthly Cost |
|---|---|
| Judge calls (10k sampled x Sonnet) | ~$90 |
| LangSmith upgrade (extended traces) | ~$50 |
| **Total** | **~$140/month** |

### Cost Optimization Strategies

| Strategy | Savings | Tradeoff |
|---|---|---|
| Layered scoring (deterministic first) | 60-80% fewer LLM judge calls | Requires building deterministic checks |
| Cached judge rubric (prompt caching) | 90% input cost reduction | Requires stable rubric prefix |
| Distilled judge model | 10x cheaper per judgment | Slight accuracy loss on edge cases |
| Batch API for offline evals | 50% cost reduction | Higher latency (async) |
| Sample-based online scoring (5%) | 95% fewer production judge calls | Statistical sampling error |

### Platform Pricing Comparison

| Platform | Base/Trace Cost | Extended Cost | Seat Cost | Notes |
|---|---|---|---|---|
| **LangSmith** | $0.05 per 1k traces (14d) | $0.50 per 1k traces (400d) | $39/seat (Plus) | Max 25k runs/trace; evaluator spend-cap resets Monday 00:00 UTC |
| **Braintrust** | Free tier | $1.50 per 1k on-demand scores | $249/seat (Pro) | 30s idle default; 10k function executions/10s; 20 MB/span |
| **Datadog LLM Obs** | $0.35 per 1k LLM spans | -- | $160/100k LLM spans | Standard Datadog pricing model |
| **DeepEval** | Free (OSS) | Confident AI hosted: $19.99/user/month | -- | pytest-style, CI-native |
| **RAGAS** | Free (fully OSS) | -- | -- | RAG-specific metrics |
| **Promptfoo** | Free (OSS) | -- | -- | Matrix comparison, config-driven |
| **Arize Phoenix** | Free (self-hosted OSS) | -- | -- | `llm_classify` default max_retries=10 |

**Death dates (plan migrations accordingly)**:
- Promptfoo acquisition agreement: **2026-03-09**
- OpenAI Hosted Evals read-only: **2026-10-31**
- OpenAI Hosted Evals shutdown: **2026-11-30**

### Latency SLA Targets

| Tier | p50 | p95 | p99 | Notes |
|---|---|---|---|---|
| **User-path eval tax** | **0 ms** | **0 ms** | **0 ms** | Non-negotiable -- judge is async sidecar |
| Deterministic oracle sidecar | 20 ms | 80 ms | 250 ms | DB check, schema validation |
| LLM judge compute (2k/200) | 1,200 ms | 4,000 ms | 12,000 ms | Single rubric call |
| Time-to-score (with 30s idle) | 31,200 ms | 34,000 ms | 42,000 ms | Braintrust idle + judge |
| Braintrust inline scorer | -- | -- | 240,000 ms | 4-minute timeout |
| Gaia2 scenario timeout | -- | -- | 300,000 ms | 5-minute timeout |
| Single pairwise comparison | 4s | 10s | 25s | Parallel execution recommended |
| Full suite (500 rows) | 5 min | 12 min | 20 min | 10-20 concurrent workers |
| CI gate (PR-level) | 3 min | 8 min | 15 min | Subset sampling for PR, full suite nightly |

**Critical anti-pattern**: If someone inlines the judge on the user path, they buy **+12s on user p99** and judge 429s become user 500s. This is not a theoretical risk -- it is the most common eval architecture mistake.

### Throughput and Back-Pressure

Eval pipeline throughput depends on LLM provider rate limits (typical: 60-500 RPM). Design for back-pressure:
- Use a queue (SQS/Redis/Kafka) between eval runner and scorer
- Implement concurrency control (semaphore limiting parallel judge calls)
- Monitor queue depth -- alert if >1,000 pending evals (indicates eval backlog)

**Capacity planning** (500-row suite, pairwise): 1,000 LLM calls x avg 800 tokens = 800K tokens. At $3/MTok (Sonnet): $2.40/run. At 100 RPM rate limit: ~10 min wall clock. Scale: 10 PRs/day x $2.40 = $24/day eval cost.

**LangSmith ingest limits**: Developer (no card) 50k traces / 500 MB per UTC hour. Plus: 500k traces / 5 GB per UTC hour. At 10 spans/task, Plus headroom is ~50k tasks/hour.

### NFR Targets

| Dimension | Target | Notes |
|---|---|---|
| CI gate latency | < 15 min for 500-row suite | Layered scoring reduces wall time |
| Judge availability | 99.5% (fallback judge configured) | Primary: Opus/Sonnet; Fallback: Haiku |
| Test-retest reliability | kappa >= 0.80 same judge, same inputs | Score same outputs twice, measure agreement |
| Online eval sampling | 5-10% of production traffic | Reservoir sampling for representative coverage |
| Alert SLA | < 5 min from sustained score drop to alert | Individual outliers are noise, sustained drops are signal |
| Coverage% | >= 95% of eligible tasks scored | `score IS NOT NULL` alert; unscored != passed |
| Spend-cap backfill | No silent skip on cap hit | LangSmith resets Monday 00:00 UTC; missed runs are not backfilled |

---

## Part 4: Distributed Resilience and Security

### Judge Availability and Fallback Chain

The judge model is a single point of failure for the entire eval pipeline. Design a fallback chain:

```
Primary Judge (Opus/Sonnet)
    │
    ├── Circuit breaker: 5 failures in 60s → open
    │
    ▼ (breaker open)
Fallback Judge (Haiku / cheaper model)
    │
    ├── If also failing...
    │
    ▼ (both failing)
Online: SKIP score (never block user)
CI:     ERROR status (fail the build, do not pass silently)
```

**Key invariant**: A tripped judge breaker skips the score in online mode -- it does not fail the user's refund. In CI mode, a tripped breaker errors the build -- it does not pass silently. This asymmetry (fail-open online, fail-closed CI) is the correct default.

### Flaky CI Builds from LLM Non-Determinism

Temperature=0 does not guarantee determinism across API calls (SD > 1.5 pp). Mitigations:
- Use **tolerance bands** instead of exact thresholds (e.g., pass if score >= 0.83 rather than >= 0.85)
- Pin the judge model version explicitly
- Sample a stable golden test set
- Cache judge responses for identical inputs
- Report `grader_status` separately: Inspect NaN when n < k, bucket infra errors separately from task failures

### Dataset Versioning

Eval datasets must be versioned alongside code. A score change can come from dataset drift, not model regression. Use content-addressable storage or git-tracked fixtures. Every score record includes `dataset_version` so that a historical score is reproducible.

### Checkpointing for Long-Running Suites

For 500+ item suites, implement checkpointing: persist partial results so a crash at item 400 does not lose items 1-399. Braintrust and LangSmith both support incremental result uploads. The checkpoint file is a JSONL of per-row results.

### Durable Execution (Temporal Sketch)

For production eval infrastructure at scale:
- Workflow ID = `run_key(suite, dataset_version, harness_commit, judge_model, trial_id)`
- Activity per task with sandbox lease
- On `TransientError`: retry with jitter
- On poison (deadline exceeded): DLQ with `grader_status=timeout`
- Compensation writes `SKIPPED`, never `passed=true`
- Replay-safe: re-running a workflow with the same ID is idempotent

### Zero-Trust MCP for Eval Harness

GAIA2 and ARE attach MCP tools to the eval harness. An untrusted MCP server is RCE-adjacent. The failure mode is an eval bot with the user's refresh token calling corporate Git or live Stripe.

| Principle | Implementation |
|---|---|
| Audience-restricted tokens | Per-MCP-server token with narrow scope |
| URL allowlist | Only sim_* endpoints, not production APIs |
| Ephemeral sandbox | Docker containers per trial, destroyed after |
| Identity from RunContext | Never from model output or user prompt |
| Tool RBAC | `dataset.write` never on a tool the model can call |
| Online judge breaker | Skip score on failure, never block user request |

### RBAC for Evaluation Systems (Four-Role Model)

| Role | Permissions | Cannot Do |
|---|---|---|
| **Operator** | Run evals, view aggregate scores | Access PII, modify datasets |
| **Auditor** | PII unmasking (dual approval), inspect individual traces | Modify datasets, change thresholds |
| **Compliance Owner** | Retention policies, legal hold | Modify eval logic |
| **Security** | Audit-of-audit log access | Modify anything else |

### PII Pipeline: Detect, Redact, Audit

PII handling in evaluation is a legal record problem, not just a privacy concern:

```
Production Trace
    │
    ▼
DETECT (NER + regex + Presidio)
    │
    ▼
REDACT (tokenize, keep structure: "ada@example.com" → "<EMAIL_1>")
    │
    ▼
AUDIT (log data category + hash, NOT raw spans)
    │
    ▼
INGEST into dataset / judge input
```

**Key rules**:
- Detect, redact, audit **before ingest** -- a trace promoted to a golden dataset with PII becomes immortal (14-day debug TTL becomes forever)
- LangSmith anonymizer / hide flags; OpenInference HIDE_*; Braintrust mask
- Hide-all makes offline eval impossible -- tokenize and keep structure instead
- The judge sees already-redacted text, or you have a second subprocessor agreement
- PCI cardholder data does not go to LangSmith at all
- Who changed goldens: dataset versions + git of the suite; log `evaluator_version` on every score

### EU AI Act Context (Effective August 2026)

High-risk AI system obligations require:
- Risk management systems
- Data governance
- Record-keeping / logging (immutable audit trails)
- Transparency
- Human oversight

Eval audit trails (who ran what, which model, which dataset, what scores, what gating decisions) are directly relevant to compliance. The persistence layer must be tamper-evident and exportable to SIEM.

### Governance Platforms (2026)

| Platform | Strength |
|---|---|
| **Braintrust** | Eval scoring + production traces + human review + CI release gates |
| **Galileo** | Runtime protection blocking unsafe outputs with audit trails + policy versioning |
| **Credo AI** | Portfolio-level governance across many AI systems with registries + risk assessments |
| **Lakera** | AI-native runtime security: prompt injection defense, PII protection |
| **Bifrost** | Open-source AI gateway: governance, budgets, access control, audit logs |

---

## Part 5: Production Enterprise Code

```python
"""
Production eval system: pass@k / pass^k estimators, dual-oracle gate,
layered scorer pipeline, trajectory scoring, circuit breaker, PII pipeline,
online monitor. Merge of best patterns from all research sources.

Requires: structlog, tenacity. LLM API calls are stubbed for portability.
"""

import hashlib
import json
import logging
import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import comb, lgamma, exp, log
from pathlib import Path
from typing import Optional, NamedTuple

import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = structlog.get_logger()


# ───────────────────────────────────────────────────────────────────────
#  Structured logging helper
# ───────────────────────────────────────────────────────────────────────

def slog(level: int, event: str, **kw):
    """Structured log with correlation fields (cid, tenant, layer)."""
    logger.log(level, event, **kw)


# ───────────────────────────────────────────────────────────────────────
#  pass@k and pass^k estimators
# ───────────────────────────────────────────────────────────────────────

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al., HumanEval).

    Returns the probability that at least one of k samples passes.
    Uses log-space to avoid overflow on large n.
    Returns NaN when n < k (Inspect convention -- do not extrapolate).
    """
    if n < k:
        return float("nan")
    if c == 0:
        return 0.0
    if c == n:
        return 1.0
    # 1 - C(n-c, k) / C(n, k), computed in log-space
    log_numerator = lgamma(n - c + 1) - lgamma(n - c - k + 1) - lgamma(k + 1)
    log_denominator = lgamma(n + 1) - lgamma(n - k + 1) - lgamma(k + 1)
    return 1.0 - exp(log_numerator - log_denominator)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k reliability estimator (Inspect, without replacement).

    Returns the probability that ALL k trials succeed.
    NaN when n < k.
    """
    if n < k:
        return float("nan")
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


# ───────────────────────────────────────────────────────────────────────
#  Run key: immutable experiment identity
# ───────────────────────────────────────────────────────────────────────

def run_key(
    suite_id: str,
    dataset_version: str,
    harness_commit: str,
    judge_model: str,
    trial_id: str,
) -> str:
    """Deterministic key for an experiment run.

    Includes harness_commit so a scaffold change is a new measurement.
    SWE-bench caches on (run_id, instance_id) -- reusing run_id with
    a different patch is a silent no-op. Always mint a new key.
    """
    parts = f"{suite_id}|{dataset_version}|{harness_commit}|{judge_model}|{trial_id}"
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


# ───────────────────────────────────────────────────────────────────────
#  Dual-oracle gate
# ───────────────────────────────────────────────────────────────────────

class GraderStatus(Enum):
    SCORED = "scored"
    SKIPPED = "skipped"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class OracleResult:
    passed: Optional[bool]
    status: GraderStatus
    grader: str
    score: float = 0.0


class GateDecision(NamedTuple):
    ship: bool
    reason: str


def dual_oracle_gate(
    hard: OracleResult,
    soft: OracleResult,
    soft_threshold: float = 0.85,
    min_coverage: float = 0.95,
    scored: int = 1,
    eligible: int = 1,
) -> GateDecision:
    """Combine hard + soft oracle into a ship/hold decision.

    Rules:
    1. Hard fail => HOLD, regardless of soft score.
    2. Hard error => HOLD in CI (fail-closed).
    3. Soft score below threshold => HOLD.
    4. Coverage below floor => HOLD (unscored != passed).
    5. All checks pass => SHIP.
    """
    if hard.status == GraderStatus.ERROR:
        return GateDecision(False, "hard_oracle_error")
    if hard.passed is False:
        return GateDecision(False, "hard_fail")
    coverage = scored / eligible if eligible > 0 else 0.0
    if coverage < min_coverage:
        return GateDecision(False, f"coverage_{coverage:.2f}_below_{min_coverage}")
    if soft.status == GraderStatus.SKIPPED:
        return GateDecision(True, "soft_skipped_hard_passed")
    if soft.score < soft_threshold:
        return GateDecision(False, f"soft_{soft.score:.2f}_below_{soft_threshold}")
    return GateDecision(True, "all_checks_passed")


# ───────────────────────────────────────────────────────────────────────
#  Circuit breaker for judge model
# ───────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """Three-state circuit breaker (closed -> open -> half_open).

    After failure_threshold consecutive failures, opens for reset_timeout
    seconds. In half_open, allows one probe; success closes, failure reopens.
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half_open"
                return True
            return False
        return True  # half_open: allow one probe

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            slog(logging.WARNING, "circuit_breaker_opened",
                 failure_count=self.failure_count)


# ───────────────────────────────────────────────────────────────────────
#  Retry with jitter
# ───────────────────────────────────────────────────────────────────────

class TransientError(Exception):
    pass


def retry_with_jitter(fn, *, cid: str, tenant: str, op: str, max_retries: int = 3):
    """Retry with exponential backoff + jitter. Raises on exhaustion."""
    for attempt in range(max_retries):
        try:
            return fn()
        except TransientError as exc:
            wait = (2 ** attempt) + random.uniform(0, 1)
            slog(logging.WARNING, f"retry_{op}",
                 cid=cid, tenant=tenant, attempt=attempt, wait=round(wait, 2))
            time.sleep(wait)
    raise TransientError(f"{op}_exhausted_after_{max_retries}")


# ───────────────────────────────────────────────────────────────────────
#  PII detection, redaction, and audit
# ───────────────────────────────────────────────────────────────────────

@dataclass
class PIIAuditRecord:
    cid: str
    tenant: str
    categories: list  # ["email", "phone"] -- types detected, not raw spans
    sha256: str       # hash of original text for forensic linking
    redacted: str     # text with PII replaced by tokens


def pii_detect_redact_audit(text: str, *, cid: str, tenant: str) -> PIIAuditRecord:
    """Detect -> Redact -> Audit pipeline.

    In production: plug in Presidio / custom NER.
    Key: log data CATEGORY and HASH, never raw PII spans.
    Runs BEFORE ingest into dataset or judge input.
    """
    import re
    categories = []
    redacted = text

    # Email detection
    if re.search(r"[\w.-]+@[\w.-]+\.\w+", text):
        categories.append("email")
        redacted = re.sub(r"[\w.-]+@[\w.-]+\.\w+", "<EMAIL>", redacted)

    # Phone detection (simple pattern)
    if re.search(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", text):
        categories.append("phone")
        redacted = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "<PHONE>", redacted)

    record = PIIAuditRecord(
        cid=cid,
        tenant=tenant,
        categories=categories,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        redacted=redacted,
    )
    if categories:
        slog(logging.INFO, "pii_redacted",
             cid=cid, tenant=tenant, categories=categories,
             sha256=record.sha256[:12])
    return record


# ───────────────────────────────────────────────────────────────────────
#  Layered scorer (deterministic -> heuristic -> LLM judge)
# ───────────────────────────────────────────────────────────────────────

class ScorerTier(Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    LLM_JUDGE = "llm_judge"


@dataclass
class EvalRow:
    row_id: str
    input_text: str
    expected_output: str
    candidate_output: str = ""
    scores: dict = field(default_factory=dict)
    passed_tiers: list = field(default_factory=list)
    final_score: float = 0.0


def score_deterministic(row: EvalRow) -> Optional[float]:
    """Tier 1: Format and schema validation. Milliseconds, zero ambiguity.

    Returns 0.0 on failure (hard fail, skip remaining tiers).
    Returns None on pass (continue to next tier).
    """
    output = row.candidate_output.strip()
    if not output:
        row.scores["deterministic"] = 0.0
        return 0.0
    # JSON parsability check if expected is JSON
    if row.expected_output.strip().startswith("{"):
        try:
            json.loads(output)
        except json.JSONDecodeError:
            row.scores["deterministic"] = 0.0
            return 0.0
    row.scores["deterministic"] = 1.0
    row.passed_tiers.append(ScorerTier.DETERMINISTIC)
    return None


def score_heuristic(row: EvalRow) -> Optional[float]:
    """Tier 2: Length bounds and keyword checks.

    Catches structural issues before expensive LLM judge.
    """
    output = row.candidate_output.strip()
    if len(output) < 10:
        row.scores["heuristic"] = 0.2
        return 0.2
    if len(output) > 50_000:
        row.scores["heuristic"] = 0.3
        return 0.3
    row.scores["heuristic"] = 1.0
    row.passed_tiers.append(ScorerTier.HEURISTIC)
    return None


# ───────────────────────────────────────────────────────────────────────
#  Eval runtime: orchestrates hard + soft oracle with breaker + PII
# ───────────────────────────────────────────────────────────────────────

class EvalRuntime:
    """Full eval runtime with dual-oracle, circuit breaker, and PII.

    Online: skip score on breaker open (never block user).
    CI: error on breaker open (never pass silently).
    """

    def __init__(self):
        self.judge_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)

    def score_task(
        self,
        *,
        cid: str,
        tenant: str,
        key: str,
        db_goal_met: bool,
        output_redacted: str,
        online: bool = False,
    ) -> tuple[OracleResult, OracleResult]:
        """Score a single task through both oracles.

        Hard oracle: deterministic (DB goal state, policy assertions).
        Soft oracle: LLM judge rubric (tone, completeness).
        Hard fail => skip soft (never average safety into quality).
        """
        # --- Hard oracle ---
        hard = OracleResult(
            passed=db_goal_met,
            status=GraderStatus.SCORED,
            grader="db_goal",
            score=1.0 if db_goal_met else 0.0,
        )

        if not db_goal_met:
            slog(logging.WARNING, "hard_fail_skip_soft",
                 cid=cid, tenant=tenant, layer="hard")
            soft = OracleResult(None, GraderStatus.SKIPPED, "skipped_hard_fail")
            return hard, soft

        # --- Soft oracle (with breaker) ---
        judge_ok = random.random() > 0.1  # Simulate 10% judge failure rate
        soft = self._soft_or_skip(
            cid, tenant, output_redacted,
            online=online, judge_ok=judge_ok,
        )
        return hard, soft

    def _soft_or_skip(
        self,
        cid: str,
        tenant: str,
        text: str,
        *,
        online: bool,
        judge_ok: bool,
    ) -> OracleResult:
        """Soft oracle with circuit breaker fallback.

        Breaker open + online => SKIP (0 ms eval tax).
        Breaker open + CI => ERROR (fail the build).
        """
        if not self.judge_breaker.allow():
            slog(logging.WARNING,
                 "judge_open_skip" if online else "judge_open_ci",
                 cid=cid, tenant=tenant, layer="judge")
            if online:
                return OracleResult(None, GraderStatus.SKIPPED, "breaker_open")
            return OracleResult(None, GraderStatus.ERROR, "breaker_open_ci")

        try:
            def _judge() -> OracleResult:
                if not judge_ok:
                    raise TransientError("judge_429")
                # Rubric call: length is NOT a criterion
                # (HealthBench length correlation is a known bias).
                score = 0.0 if "hostile" in text.lower() else 0.91
                return OracleResult(True, GraderStatus.SCORED, "rubric", score)

            result = retry_with_jitter(_judge, cid=cid, tenant=tenant, op="judge")
            self.judge_breaker.record_success()
            return result
        except TransientError as exc:
            self.judge_breaker.record_failure()
            slog(logging.WARNING, "fallback_cheap_judge",
                 cid=cid, tenant=tenant, layer="judge", err=str(exc))
            if online:
                return OracleResult(None, GraderStatus.SKIPPED, "skip_online")
            # CI: cheap fallback, not a pass
            cheap = 0.5 if len(text) > 0 else 0.0
            return OracleResult(True, GraderStatus.SCORED, "cheap_fallback", cheap)


# ───────────────────────────────────────────────────────────────────────
#  Trajectory scorer (geometric mean)
# ───────────────────────────────────────────────────────────────────────

@dataclass
class TrajectoryStep:
    step_index: int
    action: str
    tool_name: Optional[str]
    tool_args: Optional[dict]
    result: str
    score: float = 0.0  # 1-5 scale, set by judge


def geometric_mean(scores: list[float]) -> float:
    """Geometric mean -- punishes any single bad step.

    One score=1 among 19 score=5 steps:
      Arithmetic: (19*5 + 1)/20 = 4.8 (bad step vanishes)
      Geometric:  (5^19 * 1)^(1/20) = 4.17 (failure drags score down)
    """
    if not scores:
        return 0.0
    product = 1.0
    for s in scores:
        if s <= 0:
            return 0.0
        product *= s
    return product ** (1.0 / len(scores))


def score_trajectory(steps: list[TrajectoryStep]) -> dict:
    """Score an agent trajectory.

    Returns geometric mean (production metric), arithmetic mean
    (for comparison), and per-step breakdown.
    """
    raw_scores = [s.score for s in steps]
    normalized = [s / 5.0 for s in raw_scores]  # Normalize to 0-1

    geo = geometric_mean(normalized)
    arith = sum(normalized) / len(normalized) if normalized else 0.0

    return {
        "geometric_mean": round(geo, 4),
        "arithmetic_mean": round(arith, 4),
        "step_count": len(steps),
        "min_step_score": min(raw_scores) if raw_scores else 0,
        "per_step": [
            {"step": s.step_index, "action": s.action, "score": s.score}
            for s in steps
        ],
    }


# ───────────────────────────────────────────────────────────────────────
#  Eval pipeline orchestrator with checkpointing
# ───────────────────────────────────────────────────────────────────────

@dataclass
class EvalRunResult:
    run_id: str
    dataset_version: str
    model_name: str
    prompt_hash: str
    total_rows: int
    passed_rows: int
    avg_score: float
    per_row_results: list
    duration_seconds: float
    judge_cost_usd: float
    gate_passed: bool


class EvalPipeline:
    """Orchestrates layered evaluation with checkpointing.

    Tier 1 (deterministic) filters gross failures in milliseconds.
    Tier 2 (heuristic) catches structural issues cheaply.
    Tier 3 (LLM judge) runs only on survivors -- keeps cost proportional.
    """

    def __init__(
        self,
        runtime: EvalRuntime,
        gate_threshold: float = 0.85,
        checkpoint_dir: Optional[str] = None,
    ):
        self.runtime = runtime
        self.gate_threshold = gate_threshold
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.completed_rows: list[dict] = []

    def _checkpoint(self, row_result: dict):
        """Persist partial results for crash recovery (JSONL)."""
        self.completed_rows.append(row_result)
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            cp_path = self.checkpoint_dir / "checkpoint.jsonl"
            with open(cp_path, "a") as f:
                f.write(json.dumps(row_result) + "\n")

    def _load_checkpoint(self) -> set:
        """Resume from checkpoint: load completed row IDs."""
        completed_ids = set()
        if self.checkpoint_dir:
            cp_path = self.checkpoint_dir / "checkpoint.jsonl"
            if cp_path.exists():
                with open(cp_path) as f:
                    for line in f:
                        data = json.loads(line.strip())
                        completed_ids.add(data["row_id"])
                        self.completed_rows.append(data)
        return completed_ids

    def run(
        self,
        dataset: list[EvalRow],
        dataset_version: str,
        model_name: str,
        prompt_template: str,
    ) -> EvalRunResult:
        start_time = time.time()
        prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:12]
        rk = run_key("suite-1", dataset_version, "harness@HEAD",
                      model_name, str(uuid.uuid4())[:8])

        completed_ids = self._load_checkpoint()
        slog(logging.INFO, "eval_run_started",
             run_id=rk, total_rows=len(dataset), resumed=len(completed_ids))

        for row in dataset:
            if row.row_id in completed_ids:
                continue

            # Tier 1: Deterministic (milliseconds)
            result = score_deterministic(row)
            if result is not None:
                row.final_score = result
                self._checkpoint({"row_id": row.row_id, "score": result,
                                   "tier": "deterministic"})
                continue

            # Tier 2: Heuristic (cheap)
            result = score_heuristic(row)
            if result is not None:
                row.final_score = result
                self._checkpoint({"row_id": row.row_id, "score": result,
                                   "tier": "heuristic"})
                continue

            # Tier 3: Dual-oracle (expensive, only on survivors)
            pii = pii_detect_redact_audit(
                row.candidate_output, cid=row.row_id, tenant="eval")
            hard, soft = self.runtime.score_task(
                cid=row.row_id, tenant="eval",
                key=rk, db_goal_met=True,
                output_redacted=pii.redacted, online=False,
            )
            gate = dual_oracle_gate(hard, soft, soft_threshold=self.gate_threshold)
            row.final_score = soft.score if soft.status == GraderStatus.SCORED else 0.5
            self._checkpoint({"row_id": row.row_id, "score": row.final_score,
                               "tier": "llm_judge", "ship": gate.ship})

        duration = time.time() - start_time
        scores = [r["score"] for r in self.completed_rows]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        passed = sum(1 for s in scores if s >= self.gate_threshold)

        result = EvalRunResult(
            run_id=rk, dataset_version=dataset_version,
            model_name=model_name, prompt_hash=prompt_hash,
            total_rows=len(dataset), passed_rows=passed,
            avg_score=round(avg_score, 4),
            per_row_results=self.completed_rows,
            duration_seconds=round(duration, 2),
            judge_cost_usd=0.0,  # Track via API response headers in prod
            gate_passed=avg_score >= self.gate_threshold,
        )
        slog(logging.INFO, "eval_run_completed",
             run_id=rk, avg_score=result.avg_score,
             gate_passed=result.gate_passed, duration_s=result.duration_seconds)
        return result


# ───────────────────────────────────────────────────────────────────────
#  Online eval monitor with drift detection
# ───────────────────────────────────────────────────────────────────────

class OnlineEvalMonitor:
    """Samples production traffic async, detects sustained quality drift.

    Key: 0 ms eval tax on user path. Scoring happens in sidecar.
    Alert on sustained drops (3+ consecutive windows below threshold),
    not individual outliers.
    """

    def __init__(
        self,
        sample_rate: float = 0.05,
        window_size: int = 1000,
        alert_threshold: float = 0.80,
        sustained_drop_count: int = 3,
    ):
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.sustained_drop_count = sustained_drop_count
        self.score_window: deque = deque(maxlen=window_size)
        self.rolling_averages: list[dict] = []
        self.consecutive_drops = 0

    def should_sample(self) -> bool:
        return random.random() < self.sample_rate

    def record_score(self, trace_id: str, score: float, metadata: dict):
        self.score_window.append({
            "trace_id": trace_id,
            "score": score,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata,
        })
        self._check_drift()

    def _check_drift(self):
        if len(self.score_window) < 50:
            return
        recent = list(self.score_window)[-100:]
        avg = sum(r["score"] for r in recent) / len(recent)
        self.rolling_averages.append({
            "timestamp": datetime.utcnow().isoformat(),
            "avg_score": round(avg, 4),
        })
        if avg < self.alert_threshold:
            self.consecutive_drops += 1
            if self.consecutive_drops >= self.sustained_drop_count:
                slog(logging.ERROR, "quality_drift_alert",
                     avg_score=round(avg, 4),
                     threshold=self.alert_threshold,
                     consecutive_drops=self.consecutive_drops)
        else:
            self.consecutive_drops = 0


# ───────────────────────────────────────────────────────────────────────
#  Async user-path integration (0 ms eval tax)
# ───────────────────────────────────────────────────────────────────────

def user_request_then_eval_async(
    runtime: EvalRuntime,
    *,
    cid: str,
    tenant: str,
    user_output: str,
    db_goal_met: bool,
) -> str:
    """User path returns immediately. Sidecar scores later (0 ms eval tax).

    In production: enqueue (cid, redacted, db_goal_met) to Kafka/Temporal.
    The judge runs out-of-band; a tripped breaker skips the score
    rather than failing the user's request.
    """
    pii = pii_detect_redact_audit(user_output, cid=cid, tenant=tenant)
    slog(logging.INFO, "user_complete", cid=cid, tenant=tenant, layer="serve")
    # Async: in production, this is enqueued, not called inline
    runtime.score_task(
        cid=cid, tenant=tenant,
        key=run_key("suite-1", "ds-v3", "harness@abc", "sonnet", cid),
        db_goal_met=db_goal_met,
        output_redacted=pii.redacted,
        online=True,
    )
    return user_output  # Returns immediately -- never waits on judge


# ───────────────────────────────────────────────────────────────────────
#  Demo
# ───────────────────────────────────────────────────────────────────────

def demo():
    random.seed(0)

    # --- Estimator examples ---
    trials = [True, True, False, True, True]  # 4/5 pass
    n, c = len(trials), sum(1 for t in trials if t)
    print(f"pass@1 = {pass_at_k(n, c, 1):.4f}")    # 0.8000
    print(f"pass@3 = {pass_at_k(n, c, 3):.4f}")    # 0.9000
    print(f"pass^1 = {pass_hat_k(n, c, 1):.4f}")   # 0.8000
    print(f"pass^3 = {pass_hat_k(n, c, 3):.4f}")   # 0.4000
    print(f"pass@k(n=2, c=2, k=5) = {pass_at_k(2, 2, 5)}")  # NaN

    # --- Dual-oracle gate ---
    rt = EvalRuntime()
    cid = str(uuid.uuid4())
    hard, soft = rt.score_task(
        cid=cid, tenant="acme",
        key=run_key("suite-1", "ds-v3", "harness@abc", "sonnet", "run-1"),
        db_goal_met=True,
        output_redacted="Refund issued per policy.",
        online=False,
    )
    gate = dual_oracle_gate(hard, soft, soft_threshold=0.85)
    print(f"Gate: ship={gate.ship}, reason={gate.reason}")

    # --- Trajectory scoring ---
    steps = [
        TrajectoryStep(0, "lookup_account", "db_query", {"id": 123}, "found", 5.0),
        TrajectoryStep(1, "check_policy", "policy_api", {"type": "refund"}, "ok", 4.0),
        TrajectoryStep(2, "process_refund", "payment_api", {"amt": 50}, "done", 5.0),
        TrajectoryStep(3, "confirm_user", None, None, "Your refund is processed", 5.0),
    ]
    traj = score_trajectory(steps)
    print(f"Trajectory: geo={traj['geometric_mean']}, arith={traj['arithmetic_mean']}")

    # --- Online async (0 ms eval tax) ---
    user_request_then_eval_async(
        rt,
        cid=str(uuid.uuid4()),
        tenant="acme",
        user_output="Done, emailed ada@example.com",
        db_goal_met=True,
    )


if __name__ == "__main__":
    demo()
```

**What to recode from memory in an interview**: (1) Chen product vs naive 1-(1-p)^k; (2) Inspect NaN when n < k; (3) hard fail => skip soft, never average; (4) judge breaker open => skip online / error in CI; (5) PII audit logs hashes and types, not spans; (6) `run_key` includes harness commit so a scaffold change is a new measurement; (7) geometric mean for trajectory scoring.

---

## Part 6: Architectural System Design Scenarios

### Scenario A -- Dual-Oracle Release Gate for a Policy-Bound Support Agent

**Problem.** A tau-style support agent (refunds, bookings, plan changes) must not ship a prompt/model that is "nicer" but books the wrong fare class or refunds above cap. Users get one try. The team today quotes a single-run pass@1 on a 50-item golden set and an LLM judge on the utterance ("looks booked"). Requirements: fail-closed CI, fail-open online, PII-safe promotion, no judge on user p99.

**Architecture:**

```
  ┌─────────────┐   ┌──────────────────────────────────────────────────┐
  │ IdP / PEP   │──>│ CONTROL: pin as_of + harness_commit + k=5        │
  │ JWT->tenant │   │   hard: tau sim + DB goal + policy assertions    │
  │             │   │   tool unit: BFCL-style AST / Promptfoo is-json  │
  │             │   │   CI: pass^5 + coverage of pinned set            │
  │             │   │   stats: paired vs last ship; bootstrap if n<200 │
  └─────────────┘   └──────────────────┬───────────────────────────────┘
                                       v
                    ┌──────────────────────────────────────────────────┐
                    │ DATA: prod agent (same scaffold as CI)           │
                    │   sim_* MCP only in eval; prod tools in serve    │
                    │   freeze user-sim model+prompt                   │
                    │ SIDECAR: sample 0.1 after 30s idle               │
                    │   HealthBench-shaped rubric (tone/completeness)  │
                    │   spend cap; coverage% on the board              │
                    └──────────────────┬───────────────────────────────┘
                                       v
                    ┌──────────────────────────────────────────────────┐
                    │ Promote: redacted failing traces -> annotation   │
                    │ queue (runs, not threads) -> tagged dataset      │
                    │ Human on hard-vs-soft disagreement               │
                    └──────────────────────────────────────────────────┘
```

**Technology choices:**
- Hard oracle = final DB state (tau) + policy caps, **not** the NL claim. Freeze the user-sim (tau pass^k collapses when the sim upgrades).
- `k=5` trials; gate on **pass^5** (or pass^3 if budget-constrained).
- Tool unit on every commit (BFCL / DeepEval `ToolCorrectnessMetric` / Promptfoo `trajectory:tool-sequence` + irrelevance).
- Online: Braintrust/LangSmith sampling 0.1, rubric **not** 1-5 vibe. CI exit: Promptfoo failures / LangSmith >= 0.85 / Braintrust `Reporter.reportRun`.
- Miller: a 50-item set **cannot** support a 3 pp claim (n ~ 969). tau-retail-only golden set is wrong if the product is dual-control (tau GPT-4.1: retail 74%, airline 56%, telecom 34%).

**Trade-off matrix:**

| Axis | A1: Dual-oracle DB/policy hard + async rubric (recommended) | A2: Hard-only (DB match) | A3: Soft-only (LLM judge on utterance) |
|---|---|---|---|
| **Cost** | Agent x k + sampled judge; nightly 200-task ~$12 uncached / ~$48 at pass^5 | Agent x k; no judge line | Agent + judge x sample; cheapest and wrong |
| **Latency** | User eval tax **0 ms**; sidecar time-to-score ~31.2s p50 with 30s idle | CI only; 0 ms user | 0 ms if async; **+12s p99** if inlined |
| **Ops complexity** | Freeze sim; two dashboards (hard vs soft); coverage% | Env reset discipline only | Judge calibration + position swap |
| **Security** | PII redact before promote/judge; sim MCP not Stripe; safety not averaged | Blind to tone/PII-in-utterance | Judge is a subprocessor on every sampled trace |
| **Scalability** | `sampling_rate` + spend cap; n ~ 969 for 3 pp claim | Docker/DB snapshots x k | Judge TPM; spend cap with no backfill |

**Decision.** A1 wins. Hard-only ships "correct but hostile" and misses PII-in-logs. Soft-only ships "pretty wrong" (ARE whole-trace judge precision 0.53). Dual-oracle costs more and that is the point: reliability eval is not a unit test.

### Scenario B -- RAG Faithfulness CI + Citation Constraint

**Problem.** A retrieval-grounded financial assistant must not hallucinate against retrieved context, and citations must be real IDs from the retrieved set. WikiEval's ~95% RAGAS faithfulness agreement is being proposed as the production SLO. A second team wants DeepEval `FaithfulnessMetric` defaults on the same board. Fine-tunes will land later and must not drop this suite.

**Architecture:**

```
  ┌──────────────┐    ┌─────────────────────────────────────────────┐
  │ Pinned       │───>│ CONTROL: dataset as_of + chunker/embed pin  │
  │ RAG gold     │    │   retriever: context recall vs reference    │
  │ (offline)    │    │              ID-based context precision     │
  └──────────────┘    │   generator hard: RAGAS Faithfulness NLI    │
                      │   citation: IDs in retrieved set (constrained│
                      │             decode / tool-only cite)        │
                      │   CI: DeepEval assert_test / Promptfoo      │
                      │       fail merge on paired drop vs baseline │
                      └──────────────────┬──────────────────────────┘
                                         v
                      ┌──────────────────────────────────────────────┐
                      │ ONLINE 1-10%: Phoenix rails                  │
                      │   {grounded, hallucinated} under             │
                      │   suppress_tracing; never block the user     │
                      │ Construct != world-fact (not SimpleQA)       │
                      └──────────────────────────────────────────────┘
```

**Key decisions:**
- Faithfulness = RAGAS claim-level **entailment** vs retrieved context, **or** DeepEval with `penalize_ambiguous_claims=True` -- pick one construct and name it on the dashboard.
- Threshold from a **human-labeled** calibration set, not WikiEval 0.95 transplanted.
- Citation check is **deterministic** (ID in retrieved set); RAGAS will not catch a bare invented `[doc 17]`.
- Context recall is the complementary hard signal (faithfulness can be 1.0 on the wrong documents).
- Answer relevancy (78% WikiEval) is **soft** -- do not ship-gate on it.
- Online: reference-free only. A new adapter must pass this suite **and** a frozen general holdout; hosted OpenAI Evals cannot be that gate after **2026-11-30**.

**Trade-off matrix:**

| Axis | B1: RAGAS entailment + ID citation + context recall (recommended) | B2: DeepEval default Faithfulness | B3: Answer-relevancy / G-Eval vibe |
|---|---|---|---|
| **Cost** | NLI calls per claim; ID check is free; nightly envelope | Similar LLM cost; **false 1.0** on unsupported answers | Cheap; G-Eval flake (20 samples + token weights) |
| **Ops** | Two metrics + citation unit; pin retriever version | One metric, wrong default | One number, contested construct |
| **Security** | Judge sees redacted claims+chunks; no gold world-facts required online | Same egress | Verbosity bias (HealthBench length correlation) |
| **Scalability** | Claim fan-out; sample online | Silent quality lie scales perfectly | Cannot catch retrieval miss or fake IDs |

**Decision.** B1 wins. B2 is a known footgun (empty/idk gives 1.0). Faithfulness without context recall ships fluent lies about the wrong docs. Citations are a constraint problem, not an LLM-judge problem.

### Scenario C -- Agent Deployment Quality Gate (Four-Dimensional)

**Problem.** A SaaS company builds an AI agent handling customer billing (checking balances, applying credits, processing refunds). Before deploying a new version, they need a quality gate that blocks unsafe releases. The agent has access to financial tools where errors have direct monetary impact.

**Architecture:**

```
┌────────────────────────────────────────────────────────────────────┐
│                    FOUR-DIMENSIONAL QUALITY GATE                   │
│                                                                    │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐          │
│  │  Dim 1:      │   │  Dim 2:      │   │  Dim 3:      │          │
│  │  OUTCOME     │   │  TRAJECTORY  │   │  TOOL USE    │          │
│  │              │   │              │   │              │          │
│  │  Task SR     │   │  Geo-mean    │   │  Selection   │          │
│  │  >= 85%      │   │  >= 3.5/5    │   │  acc >= 95%  │          │
│  │  on 200-task │   │  (LLM judge) │   │  Arg correct │          │
│  │  internal    │   │              │   │  >= 90%      │          │
│  │  eval        │   │              │   │  Repeat < 5% │          │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘          │
│         v                  v                   v                  │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │                   GATE LOGIC                             │      │
│  │  Block deploy if ANY dimension regresses > 5%           │      │
│  │  from baseline (absorbs LLM non-determinism)            │      │
│  └────────────────────────┬────────────────────────────────┘      │
│                           v                                       │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  Dim 4: COST + LATENCY                                     │   │
│  │  Per-task $ within 2x of baseline; p95 latency < 30s      │   │
│  └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

**Key decisions:**
- Internal 200-task suite (not public benchmarks -- reward hacking risk).
- Geometric mean for trajectory (one bad refund step must not vanish in arithmetic mean).
- 5% regression tolerance absorbs LLM non-determinism while catching real regressions.
- Four dimensions ensure no single metric masks a failure: outcome alone misses unsafe trajectories, trajectory alone misses tool misuse, tool accuracy alone misses goal failure, cost prevents runaway loops.
- Real-time tool verification during eval (not post-hoc log analysis) because financial tools require immediate detection.

---

## Common Failure Modes

| # | Failure | Cause | Detection | Mitigation |
|---|---|---|---|---|
| 1 | **"The model scored 91%"** | Collapsed M x H x T x E x O x n x r x I into one number | Cannot name scaffold/image/RAM/grader version | Pin the product tuple; < 3 pp without it is not a result |
| 2 | **pass@1 quoted as SLO** | Demo used best-of-N / hidden retries | pass^k gap (up to 24.9 pp) | Report both; cap retries in target; Inspect epochs |
| 3 | **Sync judge on user p99** | Second LLM call in the request handler | User p99 = judge p99 (+12s); judge 429 becomes user 500 | Async sidecar; 0 ms eval tax |
| 4 | **Gold-NLI applied to prod traces** | Offline construct used online | Metric undefined (no gold reference) | Reference-free online; gold only in CI |
| 5 | **Composite hides safety** | Average faithfulness+tone+PII | Safety 0.70 buried in "quality 0.93" | Dual-oracle; safety not in the mean |
| 6 | **Grader crash = agent fail** | OpenAI exception sets score to 0 | Infra errors in pass@1 count | `grader_status`; Inspect NaN when n < k |
| 7 | **DeepEval faithfulness 1.0** | "idk" / empty verdicts pass by default | Calibration vs RAGAS entailment | `penalize_ambiguous_claims=True` or use RAGAS |
| 8 | **Reused SWE run_id** | Cache key ignores patch hash | Zero new measurement | New `run_id`; don't VCR agent calls |
| 9 | **Trial-2 inherits booking** | No environment reset between trials | Inflated pass^k | Snapshot per trial; separate cache keys |
| 10 | **Spend cap paints green** | Skipped runs not backfilled | Coverage% missing on dashboard | Coverage NFR; `score IS NOT NULL` alerts |
| 11 | **Promoted PII becomes immortal** | Trace added to dataset before redacting | GDPR erasure vs 14d TTL conflict | Detect -> redact -> audit **before** ingest |
| 12 | **Public Verified as hiring bar** | Contamination / gold regurgitation | OpenAI stopped reporting scores | Private holdout; rolling post-cutoff tickets |
| 13 | **3 pp claim on 50 items** | CLT on tiny n | Miller: n ~ 969 for 3 pp MDE | Bootstrap; power analysis; paired SE |
| 14 | **Lucky pass ships** | Outcome-only gate | AgentLens 10.7% lucky passes | Process overlay; forbidden-action counts |
| 15 | **Hosted Evals CI after cutoff** | Deprecated control plane | Hard outage 2026-11-30 | Migrate to Promptfoo/Inspect before read-only 2026-10-31 |
| 16 | **Uncalibrated judge drift** | Judge not recalibrated quarterly | Perfect dashboards diverging from expert review | Monthly kappa checks; recalibrate when kappa < 0.60 |
| 17 | **Benchmark reward hacking** | Agent exploits eval harness, not tasks | UC Berkeley broke 8 benchmarks (April 2026) | Internal private evals from actual product |

---

## Interview Q&A

**Q1. What is an eval system, in one minute?**
I treat eval as a measurement system, not a screenshot. Three planes: a control-plane harness that runs a pinned dataset into an immutable experiment, a data-plane tracer on the user SLO clock, and an async judge sidecar that must not sit on user p99. Dual-oracle: a hard correctness/safety bit plus a soft rubric. I refuse to quote "the model scored 91%" without naming the scaffold, tools, environment image, retries, and grader version -- Anthropic showed infra alone is 6 pp on Terminal-Bench.

**Q2. pass@k vs pass^k -- which is the SLO?**
pass@k (Chen) is the probability at least one of k samples works, and only if I have a verifier to pick it -- HumanEval unit tests, not an LLM judge. pass^k (tau-bench) is the probability all k trials work; that is reliability. Original retail pass^8 was under 25%. Anthropic's think-tool moved tau-airline pass^1 from 0.332 to 0.584 but pass^5 only from 0.100 to 0.340. Users get one try, so I gate on pass^k and report pass@k as a capability envelope. Mixing them up is how a demo becomes an SLO.

**Q3. Why not put GPT-4-as-judge on every request?**
Because that is a latency tax and a subprocessor egress, not an eval system. Braintrust, LangSmith, and Datadog all score after the root span. My user-path eval tax is 0 ms. Sidecar time-to-score with a 30s idle plus a 2k/200 judge is about 31.2s p50 -- late scores, not a slow chat. Zheng still has position, verbosity, and self-enhancement bias; few-shot raised GPT-4 swap consistency 65 to 77.5% at 4x cost without lifting human agreement. If a JSON schema or DB state exists, I use that first.

**Q4. Give me the cost model for 1,000 eval tasks.**
I state the mix. Judge-only 2k in / 200 out at Sonnet $3/$15 is about $9 per 1k. LangSmith extended experiments are $5 per 1k platform. Braintrust Pro on-demand scores are $1.50 per 1k. A tau-like 8k/1.5k agent with 70% cache read on a 6k prefix is about $26 per 1k agent; add judge and extended traces and I am near $40. pass^4 multiplies agent+sim by about four. Human annotation has no fixed SKU -- I will not invent one. HealthBench is 55k grader calls per model; that judge line is not rounding error.

**Q5. What p99 do you put in the contract?**
I do not quote a vendor online-judge p99 -- nobody publishes theirs. I contract 0 ms eval tax on the user path, and I SLO the sidecar separately: about 20/80/250 ms for a local deterministic oracle, 1,200/4,000/12,000 ms for judge compute, 31,200/34,000/42,000 ms time-to-score including Braintrust's 30s idle. p99 of a 50-example experiment is noise. If someone inlines the judge, they buy +12s on user p99 and judge 429s become user 500s.

**Q6. Dual-oracle for a refund agent -- walk the gate.**
CI fail-closed: tau-style simulator, frozen user-sim, DB goal-state plus policy caps, k=5, gate on pass^5, infra errors in a separate bucket. Every commit: schema/AST tool unit including abstention. Soft rubric (tone, completeness) is async sampled and never overrides a hard fail. Online fail-open with coverage%. I will not average PII/safety into a 0.93 quality score. A 50-item set cannot detect a 3 pp move -- Miller's worked example is n ~ 969.

**Q7. RAG faithfulness looked like 1.0 and users still complained.**
Two footguns. RAGAS faithfulness is entailment vs retrieved context, not world truth -- you can be perfectly faithful to the wrong docs, so I pair it with context recall and I do not ship-gate on answer relevancy (78% WikiEval). DeepEval's default Faithfulness treats "idk" as yes and empty verdicts as 1.0 unless I set `penalize_ambiguous_claims`. Citations are a third construct: I constrain IDs to the retrieved set because RAGAS will not catch a bare invented `[doc 17]`. I calibrate the threshold on our humans, not WikiEval's 0.95.

**Q8. Our SWE eval did not move after a patch change. What happened?**
SWE-bench caches on (run_id, instance_id), not the patch hash. Reusing run_id is a silent no-op. I mint a new run_id, keep --cache_level=env, and I do not commit LangSmith VCR cassettes for agent calls if I intend to re-measure. I also bucket harness crashes separately from unresolved. If we were quoting public Verified, I would refuse -- OpenAI stopped reporting it because models regurgitate gold patches; Pro then climbed 23.3% to 80.3% in eight months and roughly 30% of tests were broken.

**Q9. Temperature 0, still plus/minus 2 pp. Are we sloppy?**
The Randomness paper: SD still exceeds 1.5 pp at T=0; single-run pass@1 ranges 2.2-6.0 pp; trajectories diverge in the first roughly 1% of tokens. That is aleatoric plus engine/env nondeterminism. I estimate pass@1 from multiple independent runs, report pass@k and pass^k, and I treat a 31% to 33% single-run "win" as noise. Silent agent retries on top of that are an undeclared pass@k and a double-refund risk in prod.

**Q10. Trace to golden set. Where do people get sued?**
Promotion is a retention-class change: a 14-day debug email becomes immortal. I run detect, redact, audit before ingest -- LangSmith anonymizer/hide flags, OpenInference HIDE_*, Braintrust mask. Hide-all makes offline eval impossible, so I tokenize and keep structure. The judge sees already-redacted text or I have a second subprocessor agreement. Annotation-queue edits are the same RBAC as dataset write. PCI cardholder data does not go to LangSmith at all. Who changed goldens: dataset versions + git of the suite; I log evaluator_version on every score.

**Q11. How do you keep the judge from becoming the reward?**
Code assertions first (Hamel's rule: at least 100 traces, build a taxonomy, stop when 20 add no category). Itemized weighted criteria, not a 1-5 vibe. Position swap and treat flips as ties. Different model family only if calibration improves. HealthBench kept GPT-4.1 over o3 as grader because meta-eval F1 was higher and cheaper -- reasoning models are not automatically better judges. I never use a judge where math/code/JSON can be checked deterministically. If that judge is also the RL reward, the policy will farm length and sycophancy.

**Q12. LLM-as-judge biases -- name them and fix them.**
Five documented biases. Position bias: GPT-4 flipped preference on a third of pairwise cases, fix with both-order evaluation and neutral labels. Length bias: judges rate longer answers higher regardless of correctness, fix with explicit rubric instruction that concise equals verbose at equivalent quality. Self-preference: models rate own-style output higher by 10-25%, fix with cross-family judging. Rubric position bias (2026): reordering criteria options shifts scores, fix with randomized ordering. Compounding (FairJudge, Feb 2026): frontier models exceeded 50% error rates on bias tests, fix with SFT+DPO+GRPO debiasing pipeline.

**Q13. How do you build an internal private eval suite?**
I start with 50-200 representative tasks from actual product usage, not public benchmarks. I version the dataset in git alongside code, use content-addressable storage so score changes are attributable to code changes not dataset drift. I include slices for each product vertical (tau showed retail 74% but telecom only 34% on the same model). I protect the holdout -- never expose test cases to the training pipeline. I refresh quarterly with post-cutoff production failures. And I run power analysis before claiming improvements: n ~ 969 for a 3 pp MDE, so I do not pretend a 50-item set can detect small regressions.

---

## Key Numbers to Memorize

### Estimators and Statistics

| Number | What |
|---|---|
| **Chen product; naive 1-(1-p)^k biased** | pass@k unbiased estimator |
| **C(c,k) / C(n,k)** | Inspect pass^k (without replacement) |
| **NaN when n < k** | Correct behavior (Inspect), not a bug |
| **164 / n=200 / 7.7 tests** | HumanEval original |
| **80x tests / 19.3-28.9% drop** | HumanEval+ additions / pass@k impact |
| **2.2-6.0 pp / >1.5 pp at T=0** | Single-run pass@1 range; T=0 standard deviation |
| **24.9 pp** | Maximum pass@k vs pass^k gap observed |
| **n ~ 969** | Miller 3 pp MDE sample size |
| **13.2% to 7.5% MDE** | K=1 to K=10 retries on n=198 |
| **unit = task, not step** | CLT fails below few hundred tasks |

### Benchmarks and Reliability

| Number | What |
|---|---|
| **0.332 to 0.584 / 0.100 to 0.340** | tau-airline think+prompt pass^1 / pass^5 |
| **0.812 / 0.626** | tau-retail Think pass^1 / pass^5 |
| **< 50% / pass^8 < 25%** | tau GPT-4o-class pass^1 / retail pass^8 |
| **74% / 56% / 34%** | tau GPT-4.1 retail / airline / telecom pass^1 |
| **6 pp** | Terminal-Bench infra-only score difference |
| **+64% relative (12.47%)** | SWE-agent ACI vs shell, same GPT-4 Turbo |
| **10.7% (0.5-23.2%)** | AgentLens lucky passes |
| **92% vs 15%** | GAIA human vs GPT-4+plugins |
| **8 pp** | GSM1k vs GSM8k contamination gap |
| **23.3% to 80.3% / ~30% broken** | SWE-Pro public split; retracted audit |

### Oracles, Judges, and Metrics

| Number | What |
|---|---|
| **0.98 vs 0.72 / 0.99 vs 0.53** | ARE verifier vs whole-trace judge agreement/precision |
| **> 80% / 65 to 77.5% at 4x** | Zheng GPT-4 vs humans; few-shot consistency |
| **~95% / 78% / 70%** | RAGAS WikiEval faithfulness / answer / context relevance |
| **0.514** | G-Eval GPT-4 Spearman avg (SummEval) |
| **0.709 / 55-75% / median 11** | HealthBench grader F1 / MD-MD agreement / criteria per example |
| **48,562 / 5,000** | HealthBench unique criteria / conversations |
| **~60% / 32% / 16%** | HealthBench o3 / GPT-4o / GPT-3.5 Turbo |
| **+10% / +25%** | GPT-4 / Claude-v1 self-enhancement bias |
| **> 50% error rate** | FairJudge: frontier models on bias tests (Feb 2026) |
| **0.5** | RAGAS faithfulness walkthrough (Einstein example) |

### Cost and Platforms

| Number | What |
|---|---|
| **$9 per 1k judge calls** | Sonnet 2k/200 (uncached) |
| **~$1.10 per 1k** | Sonnet with cached prefix input |
| **~$26 per 1k tasks** | tau-like agent execution |
| **~$40 per 1k tasks** | Agent + judge + LangSmith extended |
| **~$48 per 1k tasks** | pass^5 agent multiplier |
| **~$12 nightly** | 200-task uncached envelope |
| **$5 / $0.50 per 1k** | LangSmith extended / base traces |
| **$1.50 per 1k** | Braintrust Pro on-demand scores |
| **$39 / $249 / $160** | LS Plus seat / BT Pro seat / DD 100k LLM spans |
| **55,000** | HealthBench grader calls per model |
| **$11.36 vs $180.49** | HAL tau-airline o4-mini High vs Opus 4.1 |

### Latency, Throughput, and Dates

| Number | What |
|---|---|
| **0 / 0 / 0 ms** | User-path eval tax (non-negotiable) |
| **20 / 80 / 250 ms** | Deterministic oracle sidecar p50/p95/p99 |
| **1,200 / 4,000 / 12,000 ms** | LLM judge compute p50/p95/p99 |
| **31,200 / 34,000 / 42,000 ms** | Time-to-score with 30s idle + judge |
| **25,000** | LangSmith max runs per trace |
| **50k/500 MB; 500k/5 GB per hour** | LS Developer / Plus ingest limits |
| **10,000 executions / 10s** | Braintrust function execution limit |
| **2026-10-31 / 2026-11-30** | OpenAI Evals read-only / shutdown |
| **Monday 00:00 UTC** | LangSmith evaluator spend-cap reset |

---

## Quick Reference

| Concept | One-Line Summary |
|---|---|
| **Eval = measurement system** | Harness + env + tools + judge + retries + infra, not a leaderboard |
| **Three planes** | Control (batch harness), Data (user traffic), Judge (async sidecar) |
| **Two clocks** | Eval clock (async, batch) vs user clock (real-time, 0 ms tax) |
| **Dual oracle** | Hard (deterministic) + soft (rubric); hard fail skips soft |
| **pass@k** | At-least-one-of-k; Chen product; needs verifier; capability envelope |
| **pass^k** | All-k-succeed; reliability; what users experience; gate on this |
| **NaN when n < k** | Correct Inspect behavior; do not extrapolate |
| **Geometric mean** | Trajectory scoring; punishes single bad step unlike arithmetic |
| **Layered scoring** | Deterministic (ms) -> heuristic (cheap) -> LLM judge (expensive) |
| **Position swap** | Evaluate both orderings in pairwise; only count consistent verdicts |
| **RAGAS faithfulness** | Entailment vs retrieved context, NOT world truth |
| **Context recall** | Complement to faithfulness -- catches faithful-to-wrong-docs |
| **Citation = constraint** | Deterministic ID check, not LLM judge problem |
| **Coverage%** | Unscored != passed; alert on `score IS NULL` |
| **PII pipeline** | Detect -> redact -> audit BEFORE ingest into dataset/judge |
| **Fail-open online** | Breaker open => skip score, never block user |
| **Fail-closed CI** | Breaker open => error, never pass silently |
| **n ~ 969** | Minimum for 3 pp MDE; 50-item sets cannot detect small regressions |
| **run_key** | Includes harness commit; scaffold change = new measurement |
| **Hosted Evals death** | OpenAI Evals shutdown 2026-11-30; migrate before 2026-10-31 |
