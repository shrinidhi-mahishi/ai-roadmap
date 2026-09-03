# 12. Evaluation: Task Success, Trajectory, Tool Accuracy, Quality, Cost & Latency

## What Is This?

**Evaluation** answers two deceptively simple questions: (1) Does my agent work? (2) How do I know it still works after I change something?

This is much harder for agents than for traditional software because:
- **Non-deterministic**: Run the same task twice and you might get different results. The model might choose different tools, take different paths, or generate different text.
- **Multi-step**: An agent might take 15 steps to complete a task. The final answer might be correct even though step 7 was wrong (it self-corrected). Or the final answer might be wrong because of a subtle error on step 3.
- **No single "right answer"**: For tasks like "write a market analysis report," there's no simple pass/fail -- quality is subjective and multi-dimensional.

The basic metrics are:
- **Task success rate**: Did the agent accomplish what it was asked to do? (e.g., "did the code compile and pass tests?")
- **Trajectory quality**: Did the agent take a reasonable path? (e.g., "did it make 3 tool calls or 47?")
- **Cost**: How much did it cost in tokens/dollars?
- **Latency**: How long did it take?

**LLM-as-judge** is a common pattern: you use a separate LLM to evaluate the agent's output (like having a teacher grade a student's work). This is cheaper than human evaluation but introduces its own biases -- judges tend to prefer longer answers, favor their own writing style, and are influenced by the order in which options are presented.

## Why It Matters

Without evaluation, you're flying blind. You can't improve what you can't measure, and you can't ship what you can't test. Evaluation is what separates "demo that works sometimes" from "product that works reliably."

---

## 2. Core Concepts

### The Harness IS Part of the Model

When someone quotes a benchmark score, you must ask: what model? What scaffold? What prompts? What tools? What environment image? What resources? What retry count? What grader? Without those, the number is meaningless. Anthropic found infrastructure config alone moved scores by 6 percentage points. SWE-agent showed that just changing from raw shell to their ACI gave +64% relative improvement with the same model.

### Two Planes, Two Clocks, Two Oracles

Think of evaluation like quality control in manufacturing. You have:

- **The eval harness (control plane)**: The testing lab. Runs batches of tasks, produces experiments. Operates on its own clock (wall-time of the job).
- **Production tracing (data plane)**: The factory floor sensors. Every live request as a nested span tree. Operates on the user's SLO clock (TTFT/e2e).
- **The judge/scorer (sidecar)**: The quality inspector. Runs asynchronously -- must NEVER sit on the user's critical path.

| Plane | What It Is | Clock | Typical Store | Oracle |
|---|---|---|---|---|
| **Eval harness (control)** | Batch runner: dataset -> target fn -> scorers -> experiment | Wall-clock of the job; retries; `num_repetitions` | Versioned dataset + immutable experiment | Reference outputs, hidden tests, DB goal-state, rubric |
| **Production tracing (data)** | Every live request as nested span tree | User SLO clock (TTFT/e2e) | Trace store (14d vs 400d, or self-hosted OTLP) | Reference-free: safety, format, sampled LLM-as-judge |
| **Judge/scorer (sidecar)** | Code, LLM-as-judge, human queue | Async; must NOT sit on user critical path | Feedback attached to run/span | Score + comment; audit log |

### pass@k vs pass^k -- The Two Questions

These answer opposite questions:

- **pass@k** (Chen et al., Codex/HumanEval): Probability at least one of k samples succeeds. Optimistic. Useful only when you have a verifier to pick the winner. Best-of-N with a unit-test oracle is pass@k; best-of-N with an LLM judge is NOT the same estimator.
- **pass^k** (Yao et al., tau-bench): Probability ALL k independent trials succeed, averaged over tasks. Pessimistic. This is the reliability metric.

The gap between them IS the product risk. Original tau-bench retail: pass^8 < 25%. Anthropic think-tool on tau-airline: pass^1 0.332 -> 0.584 with "Think"+prompt, but pass^5 only 0.100 -> 0.340. In production, your users get one try -- they experience pass^1, but if you only measured that, you'd miss that 1 in 3 times you'd fail.

### Dual-Oracle is the Enterprise Default

Binary oracles (SWE-bench resolved, tau DB match, GAIA exact match) refuse partial credit by design -- a 90%-right patch that fails one FAIL_TO_PASS is a zero. Partial credit belongs in rubrics (HealthBench: weighted criteria). Using rubric partial credit as a ship gate without a binary safety gate is how teams ship "pretty wrong." Using only binary gates on open-ended chat is how teams ship "correct but hostile." The answer: **hard gate + soft score**.

### Six Named Measurement Dimensions

Every agent eval should address:
1. **Task Success** -- Did the user's intended, policy-compliant outcome happen?
2. **Trajectory** -- Were the steps legal, efficient, and policy-faithful?
3. **Tool Accuracy** -- Right tool, right args, right order, right side effects?
4. **Quality** -- Correctness, completeness, tone, safety?
5. **Cost** -- Total $/task including all components?
6. **Latency** -- TTFT, e2e, percentiles, SLO vs harness time?

Safety gates must not be averaged away into a composite score.

### Task Success Hierarchy

1. **Deterministic outcome**: DB row, test suite, file/hash, transaction/receipt, exact constraint
2. **Partial outcome**: Weighted milestones where work has objectively progressed
3. **Semantic outcome**: Rubric against evidence when no deterministic state exists
4. **Human adjudication**: Consequential ambiguity or evaluator disagreement

Do NOT grade success from the final claim alone. A flight agent saying "booked" is distinct from a reservation existing in the database.

## 3. How It Works

### 3.1 Evaluation System Architecture

```
+-------------------+     +------------------+     +----------------+
| EVAL HARNESS      |     | PROD TRACING     |     | JUDGE SIDECAR  |
| (Control Plane)   |     | (Data Plane)     |     |                |
|                   |     |                  |     |                |
| Dataset + Version |     | Every live req   |     | Code scorer    |
| -> Target fn      |     | as span tree     |     | LLM-as-judge   |
| -> Scorers        |     |                  |     | Human queue    |
| -> Experiment     |     | 14d-400d traces  |     |                |
|                   |     |                  |     | MUST be async  |
| Versioned dataset |     | OTLP store       |     | Off SLO path   |
| + immutable exp   |     |                  |     |                |
+-------------------+     +------------------+     +----------------+
```

**Full evaluation platform components:**

| Component | Responsibility | Required Versioned Artifacts |
|---|---|---|
| **Objective/Suite Registry** | Defines construct, target population, risk, tasks, slices, pass gates | Suite ID/version, owner, hypothesis, intended use |
| **Dataset Service** | Serves immutable inputs, references, policies, environment seeds | Item ID/hash, source/license, split, creation date, sensitivity |
| **Experiment Controller** | Expands model/scaffold/config matrix into trials | Run manifest, random seeds, trial count, deadlines, budget |
| **Runner** | Invokes complete agent under test | Model snapshot, prompts, tools, harness commit, container image |
| **Environment** | Supplies stateful APIs, browser, repo, simulator, sandbox | Initial-state snapshot, reset/replay, dependency versions |
| **Trace Collector** | Captures model, tool, state, token, timing events | Append-only event log with run/task/trial/span IDs |
| **Grader Service** | Applies deterministic, model, and human graders | Grader code/prompt/model/version, rubric, reference |
| **Statistics Service** | Aggregates by task/trial/slice, estimates uncertainty | Estimator definition, confidence interval, exclusions |
| **Release Gate** | Compares candidate with baseline and requirements | Signed decision, thresholds, exceptions, approver, expiry |

The runner must reproduce the production **agent**, not bypass routing, retrieval, memory, policy, or tool wrappers. Offline replay can rescore stored outputs cheaply, but it cannot estimate how a changed policy, prompt, model, or tool result would alter later actions. Use on-policy execution for causal end-to-end comparison; use replay for grader development and cost-efficient rescoring.

### 3.2 Task Success Metrics in Detail

#### pass@k Formula (Chen et al., Codex/HumanEval, arXiv:2107.03374)

Functional correctness, not BLEU. Generate n >= k samples per task, count c that pass unit tests, report the unbiased estimator:

```
E[1 - C(n-c, k) / C(n, k)]
```

Naive `1-(1-p)^k` is BIASED. Original paper: n=200, k <= 100, 164 Python problems, mean 7.7 tests/problem.

#### pass^k Formula (Yao et al., tau-bench, arXiv:2406.12045)

Probability ALL k independent trials succeed, averaged over tasks. Key numbers:
- Original tau-bench: even GPT-4o-class agents succeed on <50% of tasks; retail pass^8 < 25%; airline pass@1 35.2%
- Anthropic think-tool on tau-airline: baseline pass^1 0.332 -> "Think"+prompt 0.584; pass^5 0.100 -> 0.340
- Retail "Think" (no extra prompt): pass^1 0.812, pass^5 0.626

#### On Randomness in Agentic Evals (arXiv:2602.07150)

60,000 trajectories, 25.58B tokens, 1.88M tool calls across three models x two scaffolds x two temperatures. Key findings:
- Trajectories diverge in the first few percent of tokens
- Gaps up to **24.9 percentage points** between pass@k and pass^k
- A 31%->33% single-run "win" is often sampling noise
- Vestige (ASSERT-KTH) is the companion trajectory analyzer

#### SWE-bench Family

| Split | Size | Key Facts |
|---|---|---|
| **Original** | 2,294 issues from 12 Python repos | Oracle is execution, not patch similarity. FAIL_TO_PASS + PASS_TO_PASS |
| **Lite** | 300 | Smaller, faster evaluation |
| **Verified** | 500 | Engineer-confirmed solvable. BUT: OpenAI (2026) declared it contaminated/saturated. All frontier models could reproduce the gold patch verbatim |
| **Pro** | 731 public + held-out | Mean gold patch 107.4 LOC across 4.1 files. BUT: ~30% broken (over-strict tests, underspecified prompts). OpenAI retracted Pro recommendation |
| **Multimodal** | varies | Visual reasoning tasks |
| **Multilingual** | 300, 9 languages, 42 repos | Cross-language eval |

Harness caches by `(run_id, instance_id)` -- same `run_id` + different diff will NOT re-run. Independent work (arXiv:2512.10218): Claude models localize files 3x better on Verified than on BeetleBox/SWE-rebench -- contamination signal.

Interview takeaway: quote **named split + named scaffold + date + contamination status**. Never treat aggregator "96% Verified" pages as an SLO.

#### GAIA and Gaia2

**GAIA** (Mialon et al., arXiv:2311.12983): 466 questions; quasi-exact match. Human 92% vs GPT-4+plugins 15%. L1/L2 near-saturated by 2025.

**Gaia2 + ARE** (Froger et al., arXiv:2509.17158): Read-and-write, **asynchronous** environment (time flows while the agent thinks -- unlike paused tau-bench/SWE-bench). 800 scenarios x 10 universes x 101 tools; 1,120 total with augmentations. Seven capabilities: execution, search, adaptability, time, ambiguity, agent2agent, noise. Judge mix: Llama 3.3 70B (soft args) + exact-match (hard args). Budget-scaling curves **plateau**. Private test set. As of Sep 2025: GPT-5 high-reasoning led; Kimi K2 best open.

#### tau-bench Family (Sierra)

| Version | Key Innovation | Key Numbers |
|---|---|---|
| **tau** (Jun 2024) | User-LLM x agent x domain APIs x policy; success = final DB state == goal | GPT-4o airline pass@1 35.2%; retail pass^8 < 25% |
| **tau2** (Jun 2025) | Dual control -- user also has tools; agent coaches user-only steps | Opus 4.6: Retail 91.9%, Telecom 99.3% |
| **tau3** (2026) | Banking knowledge (~700 docs), voice full-duplex, 75+ task fixes | Original repo marked outdated |

Warning: aggregator sites (taubench.com, Steel) mix user-simulators and judges -- do not compare pass^1 across rows without the harness footnote.

#### Core Task Success Estimands

```text
task_success_rate = successful_trials / valid_trials
macro_success = mean(success_rate_per_task_or_slice)
weighted_success = sum(business_weight_i * success_i) / sum(business_weight_i)
policy_compliant_success = trials(success AND no_hard_policy_violation) / valid_trials
```

Report invalid infrastructure trials separately. Treating timeouts as agent failures may be appropriate for a production SLO but is misleading for a capability claim; publish both views.

### 3.3 Trajectory Evaluation

**Outcome eval** asks "did the world end in the goal state?" **Process eval** asks "were the steps legal, efficient, and policy-faithful?" Both are needed but answer different questions.

- tau-bench and SWE-bench are outcome-first
- Gaia2 scores write actions and their arguments against an oracle trace -- process-shaped outcome
- BFCL multi-turn is state-transition
- Chain-of-thought as evidence is weak: models omit the hint they actually used

**Trajectory Metrics:**
- Required-state coverage and forbidden-state/action count
- Milestone progress, backtracking, repeated-state rate, no-progress steps
- Tool-call count, model-call count, retries, maximum depth
- Path efficiency: `reference_steps / actual_successful_steps` (capped at 1)
- Grounded-action rate: actions supported by current observations
- Recovery rate after injected failures
- Termination correctness: successful stop, justified escalation, premature stop, or loop/budget exhaustion

**AgentBoard** adds a progress-rate metric so partially completed multi-turn tasks are visible when final success is zero. **PaperBench** demonstrates hierarchical rubrics: 20 research-replication tasks decomposed into 8,316 individually gradable subtasks with author-informed rubrics and a separately evaluated judge (JudgeEval).

**Process metrics that belong on the trace, not the leaderboard:** step count, unique tools, retry rate, policy-violation spans, tokens-to-success, wall-clock-to-success, cache hit rate. Gaia2 is explicit: a correct answer after thousands of tokens/hours is dominated on the cost-normalized Pareto.

#### Trace Data Model (OpenInference)

Required `openinference.span.kind` types: LLM, AGENT, TOOL, RETRIEVER, EVALUATOR. Flattened attributes: `llm.model_name`, `llm.token_count.prompt`, `tool.name`, `input.value`/`output.value`. This is the portable data plane if you don't want LangSmith-proprietary run trees.

LangSmith traces: one trace = one application execution; child runs = LLM/tool/retriever; threads = multi-turn. Max **25,000 runs per trace** (hard reject).

#### Offline Eval Loop (Same DAG: LangSmith/Braintrust/Phoenix)

1. Curate dataset (manual 10-20, then production failures, then synthetic)
2. Pin a **dataset version** (LangSmith auto-versions; tag for CI)
3. Run target with `num_repetitions`, `max_concurrency`, optional disk cache (`LANGSMITH_TEST_CACHE` / VCR)
4. Score: code | LLM-as-judge | pairwise | human queue
5. Compare experiments; promote failing traces back to the dataset

Braintrust scorers have span vs trace vs group scope. Online automation `sampling_rate`. Phoenix: pull spans to pandas -> `run_evals`/`llm_classify` -> write labels back.

### 3.4 Tool Accuracy (BFCL and Beyond)

#### BFCL V4 (Patil et al., ICML 2025)

AST matching + state-transition -- NOT an LLM judge. That's why numbers are deterministic.

**V4 Overall Weights:**
- Agentic: 40%
- Multi-Turn: 30%
- Live: 10%
- Non-Live: 10%
- Hallucination: 10%

Reproduce with `bfcl-eval==2025.12.17` (commit f7cf735). Subcategories: unweighted average inside a bucket; Live is weighted by case count.

**Hallucinated tools = irrelevance track:** Non-Live Irrelevance 240 + Live Irrelevance 882. Abstention is a first-class skill: calling a tool that was never on the menu is a fail, not partial credit.

**V4 Web Search:** 100 human multi-hop questions. Ablation: disable search -> accuracy collapses. Models are not secretly answering from parameters.

#### Tool Lifecycle Decomposition

| Stage | Question | Metric |
|---|---|---|
| **Need/Abstain** | Should any tool be called? | Tool-use decision precision/recall |
| **Selection** | Correct authorized tool chosen? | Top-1 accuracy; unsafe selection rate |
| **Arguments** | Names, types, values correct? | Schema-valid rate; field exact/F1 |
| **Ordering** | Prerequisites satisfied? | Dependency violation count |
| **Execution** | Did tool actually succeed? | Success/error/timeout by tool |
| **Result Use** | Agent correctly interpreted state? | Grounded response; omission rate |
| **Side Effect** | Intended state change correct? | State delta match; duplicate write rate |

**ToolSandbox** adds stateful execution, implicit dependencies, dynamic milestone evaluation -- exposing failures absent from stateless single-turn calls.

**Enterprise Tool F1:** Treat gold tool sequence as a set (or ordered list); precision = fraction of emitted calls that are allowed + correct; recall = fraction of required calls emitted; F1 of that set. Do NOT use BLEU on JSON. Do NOT LLM-judge parameter equality when a JSON schema exists.

**BFCL vs tau vs SWE:** BFCL asks "right function, right args." tau asks "right DB mutation under policy." SWE asks "tests green." A model can ace BFCL and fail tau. Ship gates should include BOTH a function-calling unit (BFCL-style AST) AND a stateful scenario pack (tau-style).

### 3.5 Quality: Judges, Rubrics, Humans, Faithfulness

#### LLM-as-Judge (Zheng et al., MT-Bench/Chatbot Arena, arXiv:2306.05685)

GPT-4 judge vs humans: >80% agreement, matching human-human. Documented biases:
- **Position bias**: ~10-15 pt pairwise swing
- **Verbosity bias**: 15-30 pt (Wang et al. 2023)
- **Self-enhancement**: 10-25%
- **Weak math reasoning**

Mitigations: swap order and treat flips as ties; length-normalize; cross-family judges.

#### G-Eval (Liu et al., EMNLP 2023)

Auto-generated CoT evaluation steps + form-fill. GPT-4 Spearman **0.514** on summarization vs humans, beating BLEU/ROUGE/BERTScore. Authors flag bias toward LLM-generated text. Token-probability-weighted scores are more stable than greedy integer scores -- most vendor UIs skip this.

#### HealthBench (OpenAI, arXiv:2505.08775) -- The Rubric Template

5,000 multi-turn conversations; 262 physicians / 60 countries; 48,562 unique criteria; median 11 criteria/example (range 2-48). Axes: accuracy, completeness, context awareness, communication, instruction-following. Score = weighted points met / max.

| Model | Score |
|---|---|
| o3 | ~60% |
| GPT-4o | 32% |
| GPT-3.5 Turbo | 16% |
| GPT-4.1 nano | Beats GPT-4o at 25x lower cost |

This is the template for enterprise rubrics: **itemized, weighted, conversation-specific**, not a single 1-5 vibe.

#### RAGAS Faithfulness (Es et al., EACL 2024)

Not "true in the world" -- **entailed by retrieved context**. Pipeline: extract atomic statements -> NLI vs context -> fraction supported. WikiEval: ~95% agreement with humans vs direct GPT scoring 72%. A faithful answer can still be wrong if retrieval missed the doc -- pair with answer relevance + context precision/recall.

#### SimpleQA (Wei et al., OpenAI)

Short-form factuality; single indisputable answer; grade correct/incorrect/not attempted. F-score balances attempted-correct vs hallucinations.

#### Grader Hierarchy

1. **Code/state graders**: Exact, schema, unit test, DB/state, policy assertions. Fast, reproducible, but gameable.
2. **Reference metrics**: ROUGE/BLEU/embedding similarity. Useful for narrow equivalence, weak for diverse valid outputs.
3. **Model graders**: Pointwise rubric, pairwise preference, reference-based, claim-level judgment. Scalable but biased and stochastic.
4. **Human graders**: Highest construct relevance when trained and calibrated, but slow, costly, and variable.

HELM argues for multi-metric, scenario-based evaluation across accuracy, calibration, robustness, fairness, bias, toxicity, and efficiency. Prometheus 2 provides open evaluator models but its agreement is benchmark-dependent.

#### Judge Validation Protocol

Create a hidden calibration set labeled by at least two qualified humans. For each judge version, report: confusion matrix, precision/recall/F1 for hard gates, rank correlation for continuous rubrics, calibration/error by slice, abstention rate, human agreement baseline.

Mitigations:
- Explicit criterion-by-criterion rubric with positive/negative anchors
- Blind model/provider identity; remove irrelevant formatting
- Randomize pair order; require position-swap consistency
- Control verbosity when length is not a criterion
- Separate factual verification from style preference
- Require evidence spans; expose abstain/insufficient-evidence label
- Use different model family only if calibration shows improvement
- Periodically relabel random production sample after prompt/model changes

### 3.6 Observability Platforms: LangSmith, Braintrust, Phoenix

**LangSmith** (LangChain):
- **Offline**: Runs on datasets/examples (inputs + optional reference outputs) -> produces experiment
- **Online**: Runs on runs/threads from tracing project (inputs/outputs only)
- Evaluators are workspace-level, attachable to many projects/datasets
- Sampling rate, filters, and weekly spend caps are per-attachment
- Online scoring runs asynchronously -- "without adding latency"
- Auto-upgrade matching traces to extended retention unless you opt out

**Braintrust**:
- Isomorphic loop: playground -> immutable experiment -> CI (`bt eval`/GitHub Action) -> online scoring automations -> promote traces into datasets
- Hybrid: UI/auth/metadata in vendor control plane; logs, traces, datasets in customer data plane (VPC/on-prem)
- Topics: daily cluster after >= 100 facet summaries; `bt topics rewind` for replay
- Scorers have span vs trace vs group scope

**Phoenix (Arize)**:
- Open-source OTel twin: traces arrive over OTLP with OpenInference span kinds
- Evals scored back onto spans as annotations
- Self-hosts the whole stack
- Pull spans to pandas -> `run_evals`/`llm_classify` -> write labels back

**Enterprise Data Plane**: Eval datasets are as sensitive as production logs because they ARE production logs that someone promoted. If the data plane holds PII, the judge model is a subprocessor on every online-eval call.

### 3.7 Evaluation Portfolio

| Layer | Purpose | Cadence | Examples |
|---|---|---|---|
| **Tool/Unit** | Validate schemas, permissions, transforms | Every commit | Argument validator, idempotency, policy checks |
| **Component** | Isolate retrieval, router, planner, grader | Every commit/nightly | Retrieval recall, tool selection, judge calibration |
| **Scenario** | Complete agent in stateful environment | Nightly/release | tau-bench conversation, browser workflow, repo issue |
| **Capability** | Difficult frontier tasks | Periodic | AgentBoard, PaperBench, private challenge set |
| **Regression** | Protect known production behavior | Every candidate | Near-100%-expected stable cases |
| **Safety/Red-team** | Probe misuse, injection, overreach | Continuous/release | AgentDojo-style tool-data injection (97 tasks, 629 security cases) |
| **Shadow/Canary** | Validate production distribution/SLOs | Staged rollout | Sampled real traffic, no writes or guarded writes |
| **Online Experiment** | Measure user/business effect | After offline gates | Randomized A/B with guardrails and rollback |

## 4. Key Patterns & Best Practices

### The Dual Oracle Pattern

Every ship gate should have:
1. A **hard gate** (binary: tests pass, DB state correct, policy not violated)
2. A **soft score** (rubric: quality, tone, completeness, user satisfaction)

The hard gate catches "wrong." The soft score catches "right but hostile/unhelpful."

### Statistical Rigor

- Write the estimand BEFORE running: "policy-compliant success probability for production-like support tasks under version X" -- NOT "benchmark score"
- NIST distinguishes **benchmark accuracy** (conditioned on fixed items) from **generalized accuracy** (over wider population)
- Use paired design: candidate and baseline run same task/environment seed
- Task-clustered bootstrap or hierarchical mixed model (tasks as experimental unit, not steps -- avoid pseudo-replication)
- Report confidence intervals for absolute AND candidate-baseline delta
- Effect size and operational materiality, not p-value alone
- For low-frequency harms, "zero observed" is not proof of zero risk

### Online Scoring Must Be Off the SLO Path

If your "eval" is a synchronous second LLM call in the request handler, you have built a latency tax, not an eval system. All three platforms (LangSmith, Braintrust, Phoenix) run online scoring asynchronously.

### NFR Release Gates

```text
eligible = policy_compliant_success >= target
       AND unsafe_side_effect_rate <= ceiling
       AND critical_slice_lower_bound >= floor
       AND p95_latency <= SLO
       AND cost_per_success <= budget
       AND judge_calibration >= minimum
```

Then compare eligible candidates on a Pareto frontier rather than hiding trade-offs in one score.

**Release table:**

| Metric | Estimator | Release Use |
|---|---|---|
| Policy-compliant task success | Task-weighted rate + interval | Primary benefit gate |
| Critical safety/policy violation | Per trial and per 1,000 actions + interval | Non-compensable gate |
| Tool side-effect correctness | Correct writes / attempted writes | Authorization/transaction gate |
| Quality rubric | Per-dimension mean/distribution + judge validation | User-value gate |
| Cost per compliant success | Total cost / compliant successes | Unit economics |
| End-to-end p95/p99 | Admitted trials with timeout treatment | Capacity/UX gate |
| Human escalation | Escalations / valid trials, by reason | Automation/staffing |
| Reliability | pass^k or repeated-trial distribution | Consistency gate |

### Cache Hygiene

- LangSmith `LANGSMITH_TEST_CACHE` replays identical API calls from disk -- good for scorer iteration, poisonous if you think you re-measured the agent
- SWE-bench result cache: same `run_id` + different diff will NOT re-run
- Report cache hit rate in both eval and prod; bust cache in a "cold" slice
- Separate `prompt_cache_key` for judges vs agents
- Changing thinking/effort INVALIDATES Anthropic caches -- a sweep over `budget_tokens` is a cache-busting cost multiplier

### Dataset and Experiment Lifecycle

- **Dataset versions**: LangSmith auto-versions + tags for CI. Datasets have indefinite retention even when traces expire -- promoting a trace is a copy into a longer-lived legal record
- **Experiment immutability**: Braintrust: playground is mutable; experiment is the snapshot
- **Trace -> dataset**: All three vendors. This is the closed loop

## 5. System Design Considerations

### 5.1 Token Economics -- The Eval Bill Is a Second Product

```text
eval_cost = (agent tokens + tool I/O + sandbox time)
          x dataset_size x repetitions
          x (1 + judge_tokens x criteria)
          + platform_traces + human_annotation
```

Judge cost is NOT rounding error: HealthBench median 11 criteria x 5,000 examples = **55k grader calls** per model. pass^4 multiplies agent+sim by ~4.

#### LangSmith Platform SKUs (2026)

| Meter | Cost |
|---|---|
| Developer | $0/seat, 5k base traces/mo, 1 seat |
| Plus | $39/seat/mo, 10k base traces/mo, then PAYG |
| Base trace | 0.05 cents ($0.0005); 14-day retention |
| Extended trace | 0.50 cents ($0.005) all-in; 400-day retention |
| Experiments | Extended retention by default |
| Online eval/rules | Auto-upgrade to extended unless opt out |
| LCU | $1.50; Engine run ~5-30 LCU -> ~$7.50-$45/Engine tick |
| Plus ingest caps | 500k events/hour, 5.0 GB/hour, 25k runs/trace max |
| Evaluator spend cap | Weekly USD, resets Monday 00:00 UTC |

Platform costs per 1k eval runs: Extended traces 1,000 x $0.005 = **$5**. Base online (eval opt-out) = **$0.50**.

#### Provider Cache Economics

**Anthropic**: 1.25x (5 min write), 2x (1 h write), 0.10x read. Opus 5: $5/$25; cached read $0.50. Sonnet 5: $2/$10; cached read $0.20.

**OpenAI (GPT-5.6+)**: read 0.1x, write 1.25x. Reasoning tokens billed as output, often invisible. Reserve >= 25,000 tokens for reasoning+output.

#### Inferred Cost per 1k Eval Runs (All-In)

1k tau-like tasks, 1 trial, Sonnet 4.6 agent (~8k in/1.5k out, 70% cache read on 6k prefix): agent ~$26 + user-sim similar + $5 LangSmith + $9 judge. **pass^4 multiplies agent+sim by ~4.** Total for reliable eval: ~$120-200 per 1k tasks.

#### Tokens/Task and Cache Advice

| Workload | What to Meter | Cache Advice |
|---|---|---|
| SWE-bench-class | Agent tokens + Docker minutes + (optional) patch-rerank | Stable system+tools prefix; per-instance issue at tail |
| tau-bench-class | Agent + user-sim LLM + tools; x trials for pass^k | User-sim and agent should NOT share cache key |
| Gaia2 | Agent + 101-tool system prompt + 70B judge on writes | System+tools are the cache; scenario body is not |
| Rubric (HealthBench-style) | 1 completion + K grader calls | Cache rubric template; bind example criteria after breakpoint |
| Online 1% sample | Live tokens already paid; add judge x 0.01 | Separate prompt_cache_key for judges vs agents |

#### Full Cost Model

```text
agent_trial_cost = model_input + cache_write + cache_read + model_output
                 + tool/search/browser + sandbox_compute + storage/egress
                 + data_scan + human_approval

eval_cost = sum(agent_trial_cost)
          + deterministic_grader_compute + judge_model_cost
          + human_annotation + environment_reset

cost_per_success = sum(agent_trial_cost) / count(policy_compliant_success)
```

The denominator matters: cheaper attempts can be more expensive per successful outcome if retries or review rise.

### 5.2 Latency

| Metric | Eval Harness | Production SLO |
|---|---|---|
| **TTFT** | Often unused; batch APIs hide it | Chat UX; cache hits cut TTFT |
| **e2e** | Job time; dominated by slowest example | User-facing; agent loops are multiples of single-call |
| **p50/p95/p99** | Per example AND per experiment; p99 of 50-item set is noise | Need volume; vendors do not publish your p99 |
| **Time-to-score** | Braintrust timeline (optional) | Must not gate the response |

**Do NOT use eval-harness wall time as an SLO.** Harness e2e includes dataset load, Docker pull, judge queues, retries, and max_concurrency.

Do not report only the mean: long agent tails, retries, rate limits, and human waits are operationally decisive. Failed and censored trials must remain visible.

### 5.3 Why Evals Flake (and How to Fix It)

| Source | Symptom | Mitigation |
|---|---|---|
| **Agent sampling** | pass@1 jitter; 24.9 pp envelopes | `num_repetitions`; report pass@k AND pass^k |
| **T=0 still diverges** | Vestige non-determinism | Pin seed AND treat residual as aleatoric |
| **User simulator** | tau pass^k collapses | Freeze user-sim model+prompt; never silently upgrade |
| **Docker/network** | SWE "instances with errors" | Distinguish harness crash vs unresolved; never score errors as fails |
| **Live web** | GAIA/BFCL search results change | Snapshot/mock in CI; live only in nightly |
| **Judge stochasticity** | Rubric flip-flop | Temp 0, structured output, double-order pairwise, majority of 3 |
| **Dataset drift** | "Regression" from label edits | Pin dataset version/tag in CI |
| **Result caches** | False stability | New `run_id`; don't commit cache for agent calls |
| **Infrastructure** | Score changes with CPU/RAM/timeouts | Pin resources; environment health gate; 6pp shift documented |

### 5.4 Circuit Breakers on Judge Models

1. **LangSmith evaluator spend cap**: Pauses evaluator when weekly USD hits; agent traffic continues; skipped runs NOT backfilled. This is a judge circuit breaker.
2. **LangSmith tracing usage limits**: 429 when monthly traces hit cap.
3. **LangSmith 429 classes**: 1-min ALB (5000/min), hourly events/bytes, monthly unique traces on Developer (5k).
4. **Braintrust**: `sampling_rate` + `Reporter.reportRun -> bool` non-zero CI exit. Online scoring is async.
5. **Provider 429/5xx**: Fail-open (skip score, flag unscored) for online monitoring; fail-closed for CI ship gates.

**Critical**: Design for **unscored != passed**. Dashboards must show **coverage %** (traces with a judge score) as a first-class NFR; otherwise a tripped breaker silently paints quality green.

### 5.5 Reproducibility Manifest

Every run needs:

```text
run/suite/dataset versions and item hashes
candidate and baseline model snapshots
agent harness commit; prompts; tool schemas; policy versions
container/VM/browser/environment image and initial-state hash
generation, simulator, and environment seeds
provider region; resource limits; timeout/retry/concurrency
grader code/prompt/model/rubric/reference versions
trial inclusion/exclusion rule and statistical analysis plan
```

Model APIs remain nondeterministic despite a seed -- reproduction means statistically comparable conditions, not byte-identical output.

### 5.6 Isolation, Retries, and Failure Attribution

Start each trial from a clean, versioned environment. Anthropic reports an internal case where an agent observed git history from prior trials -- shared state correlates failures.

Classify failure before retry:
- **Agent**: Bad reasoning/action, policy breach, malformed call
- **Model provider**: Rate limit, transient error, truncated response
- **Tool/environment**: API outage, stale site, broken image
- **Harness**: Parser, state reset, orchestration bug
- **Grader**: Broken reference, nondeterminism, crash
- **Task**: Ambiguous, impossible, leaked, contaminated

Do NOT silently retry agent failures until success and call the result pass@1.

### 5.7 Capacity Planning

Let N = tasks, R = trials/task, M = candidate configs, G = judge calls/trial.
Execution count = N x R x M. With arrival rate lambda and mean runner duration W, Little's Law gives mean concurrency L = lambda x W. Size separate pools for model connections, sandboxes, browser instances, and graders.

### 5.8 Security & Governance

#### PII in Traces

| Product | Controls |
|---|---|
| **LangSmith** | SDK anonymizer (regex/Presidio/Comprehend); `LANGSMITH_HIDE_INPUTS`/`HIDE_OUTPUTS`; Gateway PII redaction (beta). AES-256 at rest, TLS 1.2+. Prohibits cardholder data. |
| **OpenInference/Phoenix** | `OPENINFERENCE_HIDE_INPUTS/OUTPUTS/MESSAGES/TEXT/IMAGES`; TraceConfig beats env. Self-hosted = your SSO. |
| **Braintrust** | Global masking; hybrid (AI data never on vendor disk); Topics reads trace text -- scrub first. |

Pattern: Tokenize/mask PII but keep task structure. Judge prompts should receive already-redacted text.

#### Zero-Trust MCP for Eval Tools

| Control | Why Eval Is Special |
|---|---|
| Audience-bound tokens per MCP server | Search MCP must not accept tokens for admin MCP |
| Separate IdP clients for CI vs prod | CI bots should not inherit user refresh tokens |
| Allowlist MCP URLs in harness | ARE's own warning: untrusted MCP = RCE-adjacent |
| No production write APIs on eval MCP | tau-style eval hits simulators; SWE hits ephemeral Docker |

#### Grader and Benchmark Attacks

- **Prompt injection**: Candidate output tells judge to score high. Delimit untrusted content; deterministic hard gates.
- **Reward hacking**: Optimization finds rubric defects. Compare model-grader with expert evaluations.
- **Reference exfiltration**: Agent retrieves hidden tests. Network isolation, canaries, temporal sets.
- **Task contamination**: No tested strategy simultaneously solved fidelity and contamination resistance.
- **Benchmark gaming**: Gate on private, safety, regression, and shadow suites with independent ownership.

#### RBAC and Audit

Minimum audit record: `(example_id | trace_id, evaluator_id, evaluator_version, model+params, prompt_hash, score, rationale, timestamp, dataset_version)`. Without `evaluator_version`, you cannot explain a metric jump after a rubric tweak.

Treat dataset write as production-data write. LangSmith prohibits cardholder data on platform. Health/finance may need self-hosted Phoenix or Braintrust hybrid.

## 6. Code Examples

### pass@k and pass^k Calculation

```python
import math

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021).
    n = total samples, c = correct samples, k = selection size.
    Naive 1-(1-c/n)^k is BIASED -- use this instead."""
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)

def pass_power_k(results: dict[str, list[bool]], k: int) -> float:
    """pass^k: probability ALL k trials succeed, averaged over tasks.
    This is the RELIABILITY metric (tau-bench).
    The gap between pass@k and pass^k IS the product risk."""
    task_probs = []
    for task_id, outcomes in results.items():
        n = len(outcomes)
        c = sum(outcomes)
        if n < k:
            continue
        prob_all = math.comb(c, k) / math.comb(n, k) if c >= k else 0.0
        task_probs.append(prob_all)
    return sum(task_probs) / len(task_probs) if task_probs else 0.0

# Example
results = {
    "task_1": [True, True, True, False, True],   # 4/5
    "task_2": [True, True, True, True, True],     # 5/5
    "task_3": [False, True, False, True, False],  # 2/5
}
print(f"pass@1 (task_1): {pass_at_k(5, 4, 1):.3f}")
print(f"pass^3 (reliability): {pass_power_k(results, 3):.3f}")
```

### Dual Oracle Gate

```python
def dual_oracle_gate(
    hard_pass: bool,   # binary: tests green, DB state correct, policy met
    soft_score: float, # rubric: 0.0 to 1.0 from judge
    soft_threshold: float = 0.6
) -> bool:
    """Ship only if BOTH the hard gate passes AND soft score exceeds threshold.
    Using only one is insufficient:
    - Hard-only ships "correct but hostile"
    - Soft-only ships "pretty wrong"
    """
    return hard_pass and soft_score >= soft_threshold
```

### BFCL-Style AST Tool Validation

```python
def validate_tool_call(predicted: dict, expected: dict, schema: dict) -> dict:
    """BFCL-style AST matching for tool calls.
    Do NOT use BLEU on JSON. Do NOT LLM-judge parameters
    when a JSON schema exists."""
    result = {
        "function_name_match": predicted["name"] == expected["name"],
        "schema_valid": validate_against_schema(predicted["args"], schema),
        "arg_exact_match": {},
        "hallucinated_args": [],
        "missing_args": [],
    }

    expected_args = set(expected["args"].keys())
    predicted_args = set(predicted["args"].keys())

    # Hallucinated args (calling something never on the menu = fail)
    result["hallucinated_args"] = list(predicted_args - expected_args)

    # Missing required args
    required = {k for k in schema.get("required", [])}
    result["missing_args"] = list(required - predicted_args)

    # Per-arg exact match
    for arg in expected_args & predicted_args:
        result["arg_exact_match"][arg] = (
            predicted["args"][arg] == expected["args"][arg]
        )

    result["overall"] = (
        result["function_name_match"]
        and result["schema_valid"]
        and not result["hallucinated_args"]
        and not result["missing_args"]
        and all(result["arg_exact_match"].values())
    )
    return result
```

### NFR Release Gate

```python
def release_eligible(metrics: dict) -> bool:
    """Multi-dimensional release gate. Hard gates first, then Pareto."""
    return (
        metrics["policy_compliant_success"] >= metrics["target"]
        and metrics["unsafe_side_effect_rate"] <= metrics["ceiling"]
        and metrics["critical_slice_lower_bound"] >= metrics["floor"]
        and metrics["p95_latency"] <= metrics["slo"]
        and metrics["cost_per_success"] <= metrics["budget"]
        and metrics["judge_calibration"] >= metrics["minimum"]
    )
```

### Faithfulness Evaluation (RAGAS Pattern)

```python
def evaluate_faithfulness(answer: str, context: str) -> dict:
    """RAGAS faithfulness: entailed by retrieved context,
    NOT 'true in the world.'
    A faithful answer can still be WRONG if retrieval missed the doc."""
    claims = extract_atomic_claims(answer)
    supported = 0
    for claim in claims:
        if check_entailment(claim, context):
            supported += 1
    return {
        "faithfulness": supported / len(claims) if claims else 0.0,
        "claims_total": len(claims),
        "claims_supported": supported,
    }
```

## 7. Common Pitfalls & Failure Modes

### Reward Hacking Hierarchy (Increasing Severity)

1. **Verbosity/sycophancy**: Agent learns longer = higher score
2. **Fake CoT**: Fabricated reasoning that looks good to judge
3. **Judge-steering**: Format injection, telling the judge to score high
4. **Environment tampering**: Edit tests, mock APIs, exfiltrate gold answers

SWE-bench hidden tests exist BECAUSE models pattern-match visible tests. Verified gold-patch regurgitation and Pro 23%->80% then 30% broken is metric gaming by the industry.

Defenses: trajectory publication, mix code oracles + judges, cross-family judges, adversarial judge prompts, human spot-check of high-score traces.

### Judge Bias and Detection

| Failure | Mechanism | Detection |
|---|---|---|
| Position/length/family | Autoregressive judge | Swap order; length-matched A/B; alien judge |
| Shared blind spots | Same pretraining as agent | Expert calibration (HealthBench-style) |
| Distribution shift | Offline set != prod topics | Braintrust Topics weekly; add prod failures |
| Eval leakage | Bench in training; gold in prompt | SWE-rebench/live cuts; canary strings |
| User-sim leakage | Sim quotes policy agent should infer | Pin sim; independent review |
| Cache leakage | Warm in eval, cold in prod | Report cache hit rate; bust in "cold" slice |
| Harness confounding | Scaffold wins, not model | Gaia2 uniform ReAct; mini-SWE-agent track |

### Full Failure Mode Catalog

| Failure Mode | Symptom | Mitigation |
|---|---|---|
| **Construct mismatch** | Score rises but user/business outcome does not | Rewrite objective from production decision |
| **Unrepresentative dataset** | Production slices absent | Sample production taxonomy; stratify |
| **Benchmark contamination** | Score-production gap; memorized artifacts | Private temporal holdouts; retire saturated items |
| **Broken/ambiguous task** | Capable agents fail on contradictory spec | Automated QA + human review; exclude with reason |
| **Environment leakage** | Later trial benefits from earlier state | Clean reset, isolated identity, canary artifacts |
| **Resource-induced score** | Different CPU/RAM/timeout changes score | Pin and record resources; environment health gate |
| **Silent retry inflation** | Failed attempts retried until pass | Retain all attempts; report pass@1 AND pass^k |
| **Pseudo-replication** | Narrow CI from treating steps as independent | Cluster/hierarchical analysis at task level |
| **Tool schema pass, semantic fail** | Valid JSON targets wrong account | Execute in stateful environment; compare state delta |
| **Side-effect duplication** | Retry repeats purchase/ticket | Idempotency and post-state reconciliation |
| **Judge prompt injection** | Evaluated text instructs judge | Quote as untrusted; no tools/secrets; injection suite |
| **Composite-score hiding** | Safety decline offset by style gain | Non-compensable gates; per-dimension reporting |
| **Silent judge outage** | Scores stop; dashboards freeze at last value | Coverage monitors + spend-cap alerts |
| **Extended-retention surprise bill** | Online eval default-on auto-upgrades | Opt out; sample 1-5% |
| **CI eval using live MCP prod** | Destructive writes in production | Simulators + OAuth audience |
| **pass@1 CI gate on agents** | Flaky red/green | Repetitions + pass^k on canary; full pass@k nightly |

## 8. Interview Questions & Answers

**Q1: What is the dual oracle and why do you need it?**

Dual oracle means combining a hard gate (binary pass/fail from code, hidden tests, DB state, or policy assertions) with a soft score (rubric-based quality from LLM judge or human). You need both because using only binary gates on open-ended chat ships "correct but hostile," while using only rubric partial credit ships "pretty wrong." A 90%-right patch that fails one hidden test is a zero on the hard gate. Meanwhile, a rude but technically correct response scores well on DB-state checks but fails on quality. The enterprise default is: hard gate for safety/correctness, soft score for user experience.

**Q2: What is the difference between pass@k and pass^k, and why does it matter?**

pass@k asks "can the system EVER succeed?" -- probability at least one of k samples works. pass^k asks "does the system ALWAYS succeed?" -- probability ALL k trials succeed. The gap IS the product risk. Original tau-bench: retail pass^8 < 25% even when pass@1 looked okay. Anthropic: tau-airline pass^1 0.584 dropped to pass^5 of only 0.340. The "On Randomness" paper showed 24.9 percentage point envelopes across 60k trajectories. In production, users get one try. If you only measure pass@1, you hide that 1 in 3 times you fail catastrophically. For CI, use pass^k (k=3-5) on canary tasks; for nightly, full pass@k with enough repetitions.

**Q3: Why is "the model scored 91%" a bad statement?**

Task success is a property of (model x scaffold x tools x oracle x sampling). You must ask: what benchmark split? What scaffold (SWE-agent vs raw shell = +64% difference)? What grader? How many repetitions? Anthropic found infra config alone moved scores by 6 percentage points. OpenAI declared Verified contaminated -- all frontier models reproduce the gold patch verbatim. Without full context, the number is noise. Correct form: "System X (Claude Opus 5 + SWE-agent scaffold) resolved 91% of SWE-bench Verified (500 tasks) as of [date], with [contamination status]."

**Q4: How would you evaluate a coding agent at a regulated bank?**

Five design choices: (1) Oracle: SWE-style hidden tests in ephemeral Docker with FAIL_TO_PASS and PASS_TO_PASS. Binary ship gate -- tests merge, not judges. (2) Benchmarks: NOT Verified/Pro as KPIs (contaminated/broken). Build internal issues from post-cutoff repos. (3) Process: Record tool/trace policy, no `.git/config` writes, log pytest node-ids. (4) Observability: Self-hosted Phoenix or Braintrust hybrid -- code in traces. (5) Judge: Only for PR description quality, never for merge. Report tokens/issue + p95 sandbox time as SLO.

**Q5: How do you handle LLM-as-judge biases?**

Published magnitudes: position ~10-15pt, verbosity 15-30pt, self-preference 10-25%. Mitigations: (1) Swap order; treat flips as ties. (2) Length-normalize or use criterion anchors. (3) Cross-family judge. (4) Hidden calibration set labeled by 2+ humans, stratified across quality levels and adversarial outputs. Report confusion matrix, P/R/F1 by slice. (5) Structured rubrics with positive/negative anchors per criterion -- not 1-5 vibe. (6) Periodic relabeling after judge changes. Key insight from G-Eval: judges prefer LLM-ish text. If judge IS the reward signal, RL will farm it.

**Q6: What makes HealthBench the template for enterprise rubric design?**

48,562 unique criteria across 5,000 conversations, median 11 criteria per example (range 2-48). It is itemized (each criterion is specific and checkable), weighted (criteria have different importance), and conversation-specific (not generic). Score = weighted points met / max. GPT-4.1 nano beats GPT-4o at 25x lower cost on this rubric -- proving that the rubric, not the model size, drives the measurement. The enterprise lesson: per-criterion anchors, separate hard safety gates from soft quality scores, and never use a single 1-5 vibe.

**Q7: What are the six dimensions of agent evaluation?**

Task success (did the world end in the goal state?), trajectory (how did the agent reach it -- steps, efficiency, policy compliance), tool accuracy (right tool, right args, right side effects), quality (correctness, tone, completeness via rubric), cost (tokens + compute + judge + platform per task), and latency (TTFT, e2e, per-stage, time-to-score). Safety gates are non-compensable -- they must never be averaged away. A model that scores 95% on task success but costs 10x more per success or violates policy in 2% of traces is not necessarily better.

**Q8: How do you prevent a tripped judge circuit breaker from painting quality green?**

The failure: judge spend cap trips, scores stop, dashboard freezes at last value -- looks like stable quality. Prevention: (1) Track coverage % (traces with a judge score) as a first-class NFR. (2) Alert on coverage drops. (3) Design for "unscored != passed." (4) LangSmith spend cap pauses the evaluator but agent traffic continues and skipped runs are NOT backfilled. (5) For CI: fail-closed. For online: fail-open but flag unscored.

**Q9: What is RAGAS faithfulness, and what does it NOT measure?**

Faithfulness measures whether the answer is entailed by the retrieved context -- NOT whether it is true in the world. Pipeline: extract atomic statements, NLI against context, compute fraction supported. ~95% agreement with humans vs 72% for direct GPT scoring. But a faithful answer can be completely wrong if retrieval missed the right document. Pair with answer relevance and context precision/recall.

**Q10: Design an enterprise evaluation platform for 20 teams.**

Nine components: objective/suite registry, dataset service, experiment controller, runner, environment, trace collector, grader service, statistics service, release gate. Key design: (1) Separate generation from grading (rescore without rerunning). (2) Shard by (suite_version, item_id, candidate_id, trial_index). (3) Size pools with Little's Law. (4) Dual oracle (hard + soft). (5) Separate K8s pools for API-only, browser, sandbox. (6) Canary shard before full fan-out. Security: ephemeral sandboxes, scoped synthetic creds, audience-bound MCP tokens, PII redaction at SDK level, self-hosted data plane for regulated workloads.

**Q11: How do you handle benchmark contamination?**

SWE-bench Verified: all frontier models reproduce gold patches verbatim. Pro: 23.3%->80.3% in 8 months, then ~30% broken. No tested strategy simultaneously solved fidelity and contamination resistance. Pattern: maintain separate capability, regression, fresh temporal holdout, adversarial, and production shadow sets. Promote solved items to regression. Replace saturated items. Use private holdouts with independent ownership. Retirement is a quality-control action that preserves historical data under its original version.

**Q12: How do you build a customer-support eval (tau-shaped)?**

Oracle: final CRM/DB state + policy checklist -- conversation fluency != correct mutation. Use pass^k (k=3-5) in CI. Freeze user-sim model+prompt. Use code scorers for arithmetic ("refund <= policy cap"). Add rubric judge on threads for tone (reference-free, online, sampled). Budget for reliability: pass^4 x 2 LLMs (agent+sim) is the real $/task. Add injection, ambiguous-request, outage, timeout-after-write, and escalation tasks.

**Q13: When should you use pass@k vs pass^k?**

pass@k rewards finding one correct sample (capability). pass^k demands all attempts succeed (reliability). tau-bench: retail pass@1 was reasonable but pass^8 < 25%. Users don't get best-of-N. A coding agent that can solve 80% of issues on its best day but only 40% reliably is 80% capability, 40% reliability. Report both. Use pass^k as the consistency gate for high-stakes deployments.

**Q14: What does "name the construct and outcome state" mean?**

"Helpful" is not a metric. "Policy-compliant refund exists in the ledger and the customer received correct terms" is testable. Before building any eval, define exactly what observable state constitutes success. Grade success from the world state, not the agent's final claim. If you cannot write a deterministic check, you need a rubric with explicit criteria -- not a vibe-based judge call.

**Q15: How do you distinguish eval-harness latency from production SLO latency?**

Eval-harness e2e includes dataset load, Docker pull, judge queues, retries, and max_concurrency. Production p95 is the user path only. Never equate them. Use span-level latency on production traces for SLO telemetry. Report TTFT (user-facing), e2e (request to answer), time-to-score (judge sidecar). For agents, e2e is multiples of single-call. p99 of a 50-item eval set is noise -- you need production volume.

## 9. Key Numbers to Memorize

### Benchmark Numbers

| Benchmark | Key Numbers |
|---|---|
| **SWE-bench** | 2,294 tasks; Verified 500 (contaminated); Pro 731 (~30% broken) |
| **tau-bench** | Retail pass^8 < 25%; airline pass@1 35.2% (GPT-4o) |
| **Anthropic think-tool** | tau-airline pass^1 0.332 -> 0.584; pass^5 0.100 -> 0.340 |
| **Opus 4.6** | tau2 Retail 91.9%, Telecom 99.3% |
| **GAIA** | 466 questions; humans 92%, GPT-4+plugins 15% |
| **Gaia2** | 1,120 scenarios; 101 tools; budget curves plateau |
| **BFCL V4** | Agentic 40% + Multi-Turn 30% + Live 10% + Non-Live 10% + Hallucination 10% |
| **HealthBench** | 48,562 criteria; o3 ~60%; GPT-4o 32%; nano beats 4o at 25x less |
| **Randomness study** | 60k trajectories; up to 24.9 pp gap between pass@k and pass^k |
| **PaperBench** | 20 tasks, 8,316 subtasks with hierarchical rubrics |

### Judge Bias Numbers

| Bias | Magnitude |
|---|---|
| Position | ~10-15 pt pairwise swing |
| Verbosity | 15-30 pt |
| Self-preference | 10-25% |
| LLM-as-judge vs human agreement | >80% (MT-Bench) |
| G-Eval Spearman vs humans | 0.514 (summarization) |
| RAGAS faithfulness vs humans | ~95% agreement |

### Platform Costs

| Item | Cost |
|---|---|
| LangSmith base trace | $0.0005 (14d) |
| LangSmith extended trace | $0.005 (400d) |
| LangSmith Plus seat | $39/month |
| LangSmith LCU | $1.50 |
| Anthropic cache read | 0.1x input |
| OpenAI cache read (5.6+) | 0.1x input |
| Per 1k extended eval runs | $5 LangSmith |
| Per 1k judge calls (Sonnet, no cache) | ~$9 |
| Per 1k judge calls (cached rubric) | ~$1.1 |

### Infrastructure Impact

| Finding | Number |
|---|---|
| Infra config score shift | 6 pp (Anthropic Terminal-Bench) |
| Pod errors in some settings | ~6% |
| Resources for stability | ~3x baseline |
| SWE-bench Pro broken tasks | ~30% |
| Verified contamination | All frontier models reproduce gold patch |
| SWE-bench Pro growth | 23.3% -> 80.3% in 8 months |

## 10. Quick Reference

```
EVAL = (model x scaffold x tools x oracle x sampling)
     Never collapse to "the model scored 91%"

DUAL ORACLE
  Hard gate:  tests, DB state, policy assertions  -> binary ship gate
  Soft score: rubric judge, quality dimensions     -> user experience

TWO METRICS
  pass@k = P(at least 1 of k succeeds)  -- capability, optimistic
  pass^k = P(all k succeed)             -- reliability, pessimistic
  Gap = product risk. Report both.

SIX DIMENSIONS
  Task success | Trajectory | Tool accuracy | Quality | Cost | Latency
  Safety gates are non-compensable. Never average away.

EVAL PORTFOLIO (commit -> release -> prod)
  Unit/tool -> Component -> Scenario -> Capability -> Regression
  -> Safety/red-team -> Shadow/canary -> Online experiment

KEY BENCHMARKS
  SWE-bench: 2,294/12, Verified 500 (contaminated), Pro 731 (~30% broken)
  GAIA: 466, human 92%, GPT-4+plugins 15%
  Gaia2: 800x10x101 tools, async, budget curves plateau
  tau: retail pass^8 <25%, airline pass@1 35.2%
  BFCL V4: Agentic 40% + Multi-Turn 30% + Live 10% + Non-Live 10% + Halluc 10%
  HealthBench: 5k convos, 262 physicians, 48,562 criteria, o3 ~60%

JUDGE BIASES
  Position: 10-15pt | Verbosity: 15-30pt | Self-pref: 10-25%
  Mitigate: swap order, length-normalize, cross-family, expert calibration

LANGSMITH COSTS
  Base: 0.05c/trace (14d) | Extended: 0.50c/trace (400d)
  LCU: $1.50 | Plus: $39/seat | 25k runs/trace max

CACHE
  Anthropic: 0.1x read | OpenAI: 0.1x read (GPT-5.6+)
  Effort/budget sweep = cache-busting cost multiplier

FLAKE SOURCES
  Agent sampling (24.9pp) | T=0 divergence | User-sim drift
  Docker/network | Live web | Judge stochasticity | Dataset drift | Caches

SHIP ARCHITECTURE (cheap -> balanced -> strict)
  Oracle: judge -> code+judge -> hidden tests+human audit
  Traces: SaaS 14d -> extended on failures -> hybrid/self-host
  CI gate: pass@1 n=1 -> 3 reps+delta -> pass^k canary+nightly pass@k

SECURITY
  MCP: OAuth 2.1, audience-bound, no passthrough, no prod writes in eval
  PII: mask at SDK, separate prompt_cache_key, judge = subprocessor
  RBAC: dataset write = production-data write

INTERVIEW CLOSE
  "Dual oracles, versioned datasets, coverage SLOs on judges,
   and named (split, scaffold, date) -- never a naked percentage."
```
