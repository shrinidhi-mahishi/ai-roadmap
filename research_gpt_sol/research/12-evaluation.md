# Research: Evaluation — Task Success, Trajectory, Tool Accuracy, Quality, Cost, and Latency

**Date researched**: 2026-08-21  
**Sources consulted**: 46

Agent evaluation is a measurement system for a **model + agent harness + tools + environment + grader**, not a property of the model alone. A task is a test case, a trial is one stochastic attempt, a trajectory is the recorded sequence of interactions, and the outcome is the final environment state. Anthropic’s current evaluation guidance makes this distinction explicit and recommends multiple trials because agent outputs vary [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). NIST’s 2026 draft benchmark practices similarly require an evaluation objective, documented conditions, variation/uncertainty analysis, and limitations rather than an unexplained leaderboard score [[11]](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf).

## 1. System Topology & Mechanics

### 1.1 Evaluation control plane and execution plane

A production evaluation platform has these components:

| Component | Responsibility | Required versioned artifacts |
|---|---|---|
| Objective and suite registry | defines construct, target population, risk, tasks, slices, pass gates | suite ID/version, owner, hypothesis, intended use, exclusions |
| Dataset service | serves immutable inputs, references, policies, environment seeds | item ID/hash, source/license, split, creation date, sensitivity |
| Experiment controller | expands model/scaffold/config matrix into trials | run manifest, random seeds, trial count, deadlines, budget |
| Runner | invokes the complete agent under test | model snapshot, prompts, tools, harness commit, container image |
| Environment | supplies stateful APIs, browser, repository, simulator, sandbox | initial-state snapshot, reset/replay procedure, dependency versions |
| Trace collector | captures model, tool, state, token, timing, error, and approval events | append-only event log with run/task/trial/span IDs |
| Grader service | applies deterministic, model, and human graders | grader code/prompt/model/version, rubric, reference, raw judgment |
| Statistics service | aggregates by task/trial/slice and estimates uncertainty | estimator definition, confidence interval, paired comparison, exclusions |
| Release gate | compares candidate with baseline and absolute requirements | signed decision, thresholds, exceptions, approver, expiry |

OpenAI defines trace grading as assigning structured labels or scores to the end-to-end log of decisions, tool calls, and reasoning steps, while trace evals apply those graders across examples to find regressions [[4]](https://developers.openai.com/api/docs/guides/trace-grading). Google ADK separates trajectory/tool-use evaluation from final-response evaluation and supports exact-trajectory and rubric/model-based criteria [[6]](https://adk.dev/evaluate/). Inspect models an evaluation as a dataset, solver/agent, and scorer and supports tools, sandboxes, logs, and many model providers [[7]](https://inspect.aisi.org.uk/).

OpenAI's current Evals API guide expresses the basic lifecycle as describing the task, running it on test inputs, and analyzing results to iterate [[3]](https://developers.openai.com/api/docs/guides/evals). The enterprise topology above extends that lifecycle with stateful environments, trace lineage, statistical analysis, and governed release decisions `[inferred]`.

The runner must reproduce the production **agent**, not bypass routing, retrieval, memory, policy, or tool wrappers. Offline replay can rescore stored outputs cheaply, but it cannot estimate how a changed policy, prompt, model, user simulator, or tool result would alter later actions `[inferred]`. Use on-policy execution for causal end-to-end comparison; use replay for grader development, trace analysis, and cost-efficient rescoring.

### 1.2 The six named measurement dimensions

#### A. Task success

Task success asks whether the user’s intended, policy-compliant outcome exists in the environment. The preferred hierarchy is:

1. **Deterministic outcome:** database row, test suite, file/hash, transaction/receipt, or exact constraint.
2. **Partial outcome:** weighted milestones where work has objectively progressed.
3. **Semantic outcome:** rubric against evidence when no deterministic state exists.
4. **Human adjudication:** consequential ambiguity or evaluator disagreement.

Do not grade success from the final claim alone. A flight agent saying “booked” is distinct from a reservation existing in the database [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). τ-bench evaluates interactive customer-service agents against final database state and communication requirements in airline and retail environments [[15]](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html). The current τ2-bench evaluation specification documents database and communication reward bases and warns that annotated actions are not necessarily a mandatory trajectory [[16]](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md).

Core estimands `[inferred]`:

```text
task_success_rate = successful_trials / valid_trials
macro_success = mean(success_rate_per_task_or_slice)
weighted_success = sum(business_weight_i * success_i) / sum(business_weight_i)
policy_compliant_success = trials(success AND no_hard_policy_violation) / valid_trials
```

Report invalid infrastructure trials separately. Treating timeouts as agent failures may be appropriate for a production SLO but is misleading for a capability claim; publish both views `[inferred]`.

Two often-confused reliability metrics answer opposite questions:

- **`pass@k`**: probability at least one of `k` samples succeeds; useful only when a verifier can select the successful candidate. The HumanEval/Codex paper provides the unbiased estimator from `n` samples and `c` correct samples [[37]](https://arxiv.org/abs/2107.03374).
- **`pass^k`**: probability all `k` repeated trials succeed; τ-bench introduced it to measure consistency and reported historical retail `pass^8` below 25% even when single-trial success was materially higher [[15]](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html).

#### B. Trajectory

Trajectory evaluation asks how the agent reached the outcome. Measure it for diagnosis, safety, and efficiency without enforcing one arbitrary “gold path.” Exact step matching is appropriate only when order is a policy or protocol requirement. Anthropic notes that agents may find valid unanticipated solutions and recommends grading outputs/outcomes rather than rigid paths where possible [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). AgentBoard adds a progress-rate metric so partially completed multi-turn tasks are visible when final success is zero [[19]](https://proceedings.neurips.cc/paper_files/paper/2024/hash/877b40688e330a0e2a3fc24084208dfa-Abstract-Datasets_and_Benchmarks_Track.html).

Trajectory metrics `[inferred]`:

- required-state coverage and forbidden-state/action count;
- milestone progress, backtracking, repeated-state/action rate, and no-progress steps;
- tool-call count, model-call count, handoffs, approvals, retries, and maximum depth;
- path efficiency: `reference_or_lower_bound_steps / actual_successful_steps`, capped at 1 only if the lower bound is valid;
- grounded-action rate: actions supported by current observations and task state;
- recovery rate after injected tool, user, or environment failures;
- termination correctness: successful stop, justified escalation, premature stop, or loop/budget exhaustion.

AgentBench spans eight interactive environments and demonstrates the breadth of state/action interfaces, but cross-environment averages hide domain-specific failure modes [[20]](https://openreview.net/forum?id=zAdUB0aCTQ). Trace inspection remains necessary to determine whether a low score reflects agent behavior, bad task design, or a grader defect [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

#### C. Tool accuracy

Tool accuracy is not one exact-match rate. Decompose the tool lifecycle:

| Stage | Question | Metric example `[inferred]` |
|---|---|---|
| Need/abstain | should any tool be called? | tool-use decision precision/recall |
| Selection | was the correct authorized tool chosen? | top-1 selection accuracy; unsafe selection rate |
| Arguments | are names, types, values, units, and resource IDs correct? | schema-valid rate; field exact/F1; semantic argument accuracy |
| Ordering/dependency | were prerequisites satisfied? | dependency violation; valid partial-order completion |
| Execution | did the tool actually succeed? | success/error/timeout by tool and status code |
| Result use | did the agent correctly interpret returned state? | grounded response; contradiction/omission rate |
| Side effect | was the intended state change correct and idempotent? | state delta match; duplicate/unauthorized write rate |

BFCL evaluates function selection and calling across serial, parallel, relevance, and multi-turn categories and emphasizes executable rather than text-only checking [[18]](https://proceedings.mlr.press/v267/patil25a.html). ToolSandbox adds stateful execution, implicit dependencies, an on-policy user simulator, and dynamic milestone evaluation, exposing failures absent from stateless single-turn calls [[17]](https://aclanthology.org/2025.findings-naacl.65/). Use both component tests and stateful scenarios; neither alone establishes production tool reliability `[inferred]`.

#### D. Quality

Quality is a multidimensional construct, not cosine similarity. Define observable rubric criteria for correctness/factuality, completeness, relevance, coherence, instruction adherence, evidence/citation, uncertainty, tone, accessibility, and domain-specific safety `[inferred]`. Make hard constraints binary and report rubric dimensions separately before any weighted composite. HELM argues for multi-metric, scenario-based evaluation across accuracy, calibration, robustness, fairness, bias, toxicity, and efficiency rather than one accuracy number [[38]](https://arxiv.org/abs/2211.09110).

Grader hierarchy:

- **Code/state graders:** exact, schema, unit test, database/state, policy assertions. Fast and reproducible but can be incomplete or gamed.
- **Reference metrics:** ROUGE/BLEU/embedding similarity. Useful for narrow equivalence, weak for diverse valid outputs.
- **Model graders:** pointwise rubric, pairwise preference, reference-based, or claim-level judgment. Scalable but biased and stochastic.
- **Human graders:** domain experts or target users. Highest construct relevance when trained and calibrated, but slow, costly, and also variable.

OpenAI’s grader interface supports string, similarity, score-model, and Python graders and explicitly warns about grader/reward hacking [[5]](https://developers.openai.com/api/docs/guides/graders). G-Eval reported a historical Spearman correlation of 0.514 with humans for summarization under its GPT-4-based setup, while also identifying bias toward LLM-generated text [[28]](https://aclanthology.org/2023.emnlp-main.153/). MT-Bench/Chatbot Arena reported over 80% judge-human preference agreement in its studied setting but documented position, verbosity, self-enhancement, and reasoning biases [[29]](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html). These results do not transfer automatically to a new rubric, model, language, or domain.

#### E. Cost

Measure cost for the complete trial and for the evaluation itself:

```text
agent_trial_cost = model_input + cache_write + cache_read + model_output
                 + tool/search/browser + sandbox_compute + storage/egress
                 + data_scan + human_approval

eval_cost = sum(agent_trial_cost)
          + deterministic_grader_compute
          + judge_model_cost
          + human_annotation_and_adjudication
          + environment_reset_and_storage

cost_per_success = sum(agent_trial_cost) / count(policy_compliant_success)
```

Use actual provider usage records and invoices, not token-count estimates alone `[inferred]`. Report input, cached input, reasoning/output, tool, compute, and human components separately. A candidate can reduce cost per attempt while increasing cost per accepted outcome through retries or review. Anthropic reports its own multi-agent research system uses roughly 15 times chat tokens and about four times single-agent tokens; this is a vendor workload observation, not a general multi-agent multiplier [[45]](https://www.anthropic.com/engineering/multi-agent-research-system).

#### F. Latency

Capture spans for queue/admission, context/retrieval, each model call, tool wait, sandbox execution, approval wait, retry/backoff, grading, and end-to-end task time. OpenTelemetry traces model operations as nested spans, and its GenAI conventions cover model, token, agent/workflow, tool-call, and tool-result attributes, though GenAI conventions remain under active development [[34]](https://opentelemetry.io/blog/2026/genai-observability/) [[36]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).

Metrics `[inferred]`:

- time to first token/meaningful event, time to first tool, and time to first correct partial result;
- p50/p90/p95/p99 end-to-end latency and per-stage critical path;
- successful-task latency, all-admitted latency, timeout rate, abandonment, and approval-excluded/approval-included time;
- model/tool/sandbox service time versus queue time;
- tokens/second, tasks/hour, active trials, queue depth, and utilization;
- latency per successful outcome and cost-latency-quality Pareto frontier.

Do not report only the mean: long agent tails, retries, rate limits, and human waits are operationally decisive. Failed and censored trials must remain visible rather than disappearing from “successful latency” `[inferred]`.

### 1.3 Evaluation portfolio

| Layer | Purpose | Typical cadence | Examples |
|---|---|---|---|
| Tool/unit | validate schemas, permissions, deterministic transforms | every commit | argument validator, idempotency, policy checks |
| Component | isolate retrieval, router, planner, grader | every commit/nightly | retrieval recall, tool selection, judge calibration |
| Scenario | execute complete agent in stateful environment | nightly/release | τ-bench-like conversation, browser workflow, repository issue |
| Capability | measure difficult frontier tasks | periodic | AgentBoard, PaperBench, private challenge set |
| Regression | protect known production behavior | every candidate | near-100%-expected stable cases [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Safety/red-team | probe misuse, injection, overreach, privacy | continuous/release | AgentDojo-style tool-data injection [[41]](https://arxiv.org/abs/2406.13352) |
| Shadow/canary | validate production distribution and SLOs | staged rollout | sampled real traffic, no writes or guarded writes |
| Online experiment | measure user/business effect | after offline gates | randomized A/B with guardrails and rollback |

PaperBench demonstrates hierarchical rubrics for long-horizon outcomes: 20 research-replication tasks are decomposed into 8,316 individually gradable subtasks, with author-informed rubrics and a separately evaluated judge [[21]](https://openai.com/index/paperbench/). This improves diagnostic resolution but does not remove rubric or judge error.

## 2. Token Economics & NFR Metrics

### 2.1 Statistical estimand and experimental unit

Write the estimand before running the evaluation: “policy-compliant success probability for production-like support tasks under version X, over the next release population,” not “benchmark score” `[inferred]`. NIST distinguishes **benchmark accuracy**, conditioned on the fixed items, from **generalized accuracy** over a wider population of similar potential items. Its 2026 statistical report shows that generalized linear mixed models can account for model/item structure and quantify generalized uncertainty more correctly than some common approaches [[12]](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models).

The experimental unit is usually a task, with repeated trials nested within task and slices nested within user/domain. Treating every step, grader assertion, or repeated attempt as independent creates pseudo-replication and narrow confidence intervals `[inferred]`. Use:

- a paired design: candidate and baseline run the same task/environment seed where possible;
- multiple stochastic trials on a representative subset, not only one trial over more items;
- task-clustered bootstrap or a hierarchical/binomial mixed model for population inference;
- confidence intervals for absolute performance and candidate-baseline delta;
- predeclared primary metric, slices, thresholds, and multiplicity treatment;
- effect size and operational materiality, not p-value alone;
- human inter-rater reliability and judge-human agreement by rubric and slice.

For binary outcomes, report numerator/denominator and an interval; for low-frequency harms, “zero observed” is not proof of zero risk `[inferred]`. For ordinal rubrics, report category distribution and weighted agreement, not only a mean. The 2026 agreement-metrics analysis explains relationships among accuracy, F1, Cohen’s kappa, Fleiss’ kappa, Krippendorff’s alpha, and rank correlation; metric choice must match label scale and rater design [[32]](https://arxiv.org/abs/2606.00093).

### 2.2 Judge validation

Create a hidden calibration set labeled independently by at least two qualified humans, adjudicate disagreements, and stratify across quality, task, language, length, model family, and adversarial output `[inferred]`. For each judge version, report confusion matrix, precision/recall/F1 for hard gates, rank/linear correlation for continuous rubrics where appropriate, calibration/error by slice, abstention, and human agreement. Do not validate the judge only on polished outputs.

Mitigations `[inferred]`:

- explicit criterion-by-criterion rubric with positive/negative anchors;
- blind model/provider identity and remove irrelevant formatting;
- randomize pair order and require position-swap consistency;
- control or normalize verbosity when length is not a criterion;
- separate factual verification from style preference;
- require evidence spans for claims and expose an abstain/insufficient-evidence label;
- use a different model family or multiple judges only if calibration shows improvement;
- periodically relabel a random production sample and revalidate after prompt/model changes.

A systematic position-bias study finds judge behavior depends on response ordering and judge/task setup [[30]](https://arxiv.org/abs/2406.07791). Prometheus 2 provides open evaluator models for direct and pairwise assessment, but its reported agreement remains benchmark- and rubric-dependent [[31]](https://arxiv.org/abs/2405.01535). A panel does not guarantee independent errors when judges share training data, prompts, or blind spots `[inferred]`.

### 2.3 NFR gates and multi-objective decisions

Define hard gates before a weighted score:

```text
eligible = policy_compliant_success >= target
       AND unsafe_side_effect_rate <= ceiling
       AND critical_slice_lower_bound >= floor
       AND p95_latency <= SLO
       AND cost_per_success <= budget
       AND judge_calibration >= minimum
```

Then compare eligible candidates on a Pareto frontier rather than hiding trade-offs in one score `[inferred]`. Suggested release table:

| Metric | Estimator | Release use |
|---|---|---|
| Policy-compliant task success | task-weighted rate + interval | primary benefit gate |
| Critical safety/policy violation | per trial and per 1,000 actions + interval | non-compensable gate |
| Tool side-effect correctness | correct writes / attempted writes | authorization/transaction gate |
| Quality rubric | per-dimension mean/distribution + judge validation | user-value gate |
| Cost per compliant success | total cost / compliant successes | unit economics |
| End-to-end p95/p99 | admitted trials with timeout treatment | capacity/user-experience gate |
| Human escalation | escalations / valid trials, by reason | automation and staffing |
| Reliability | pass^k or repeated-trial distribution | consistency gate |

Model routing must be evaluated as a policy: measure router confusion, downstream success, cost, latency, and failures by route. A lower-cost route that receives easier tasks will look better without paired or difficulty-adjusted analysis `[inferred]`. Caches must be keyed by all semantically material inputs and separately reported; caching can reduce cost/latency while also leaking prior trial state or invalidating independence `[inferred]`.

### 2.4 Capacity planning

Let `N` be tasks, `R` trials/task, `M` candidate configurations, and `G` judge calls/trial. The execution count is `N*R*M`; model calls depend on agent turns plus `G`. With arrival rate `lambda` trials/second and mean occupied runner duration `W`, Little’s Law gives mean concurrency `L=lambda*W`; size separate pools for model connections, sandboxes, browser instances, and graders from their own service-time and tail distributions `[inferred]`.

Inspect executes samples asynchronously while enforcing model-connection, sandbox, subprocess, and sample limits. Its documentation notes a recovery/throughput trade-off: high in-flight sample counts improve utilization but leave fewer completed samples persisted after interruption [[8]](https://inspect.aisi.org.uk/parallelism.html). Use named concurrency limits and back-pressure per dependency rather than one global worker count.

> ⚠️ Limited public data available for this dimension. Comparable production p50/p95/p99 latency, judge cost, annotation cost, cache hit rate, and cost-per-1,000-policy-compliant-success numbers are generally proprietary and depend on task distribution, model contract, tool runtime, and human workflow.

## 3. Distributed Resilience & State

### 3.1 Reproducibility manifest and event state

Every run needs an immutable manifest:

```text
run/suite/dataset versions and item hashes
candidate and baseline model snapshots
agent harness commit; prompts; tool schemas; policy and router versions
container/VM/browser/environment image and initial-state hash
generation, simulator, and environment seeds where supported
provider region; resource limits; timeout/retry/concurrency settings
grader code/prompt/model/rubric/reference versions
trial inclusion/exclusion rule and statistical analysis plan
```

Model APIs may remain nondeterministic despite a seed, so reproduction means statistically comparable conditions, not guaranteed byte-identical output `[inferred]`. Model Cards and Datasheets for Datasets establish documentation patterns for intended use, evaluation conditions, composition, collection, and limitations [[39]](https://arxiv.org/abs/1810.03993) [[40]](https://arxiv.org/abs/1803.09010).

Use append-only trial events: `scheduled`, `leased`, `environment_ready`, `model_call`, `tool_call`, `state_delta`, `approval`, `checkpoint`, `graded`, `excluded`, `complete`. OpenTelemetry spans contain trace/span identity, parentage, timestamps, attributes, events, status, and links, providing a vendor-neutral distributed correlation model [[35]](https://opentelemetry.io/docs/specs/otel/trace/api/). Keep low-cardinality metrics separate from high-cardinality traces and artifacts `[inferred]`.

### 3.2 Isolation, retries, and failure attribution

Start each trial from a clean, versioned environment. Anthropic reports that shared state can correlate failures or inflate results, including an internal case where an agent observed git history from prior trials [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). SWE-bench requires repository containers and tests; WebArena provides self-hosted sites; OSWorld provides real computer environments, illustrating that the environment is part of the benchmark contract [[22]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html) [[24]](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf) [[25]](https://arxiv.org/abs/2404.07972).

Classify failure before retry:

- **agent:** bad reasoning/action, policy breach, malformed call after valid schema;
- **model provider:** rate limit, transient server error, truncated response;
- **tool/environment:** API outage, stale site, broken image, resource exhaustion;
- **harness:** parser, state reset, logging, orchestration bug;
- **grader:** broken reference, nondeterminism, crash, judge error;
- **task:** ambiguous, impossible, leaked, contaminated, or missing success state.

`[inferred]` Retry transient infrastructure failures under a predeclared policy and retain every attempt. Do not silently retry agent failures until success and call the result pass@1. If a write may have succeeded before timeout, query state or operation ID before replay. Store both `raw_outcome` and `analysis_disposition` with reviewer and reason.

Anthropic’s Terminal-Bench 2.0 infrastructure study found a six-percentage-point score shift across resource configurations in its experiment, with pod errors near 6% in some settings and roughly three times baseline resources needed for stabilization [[27]](https://www.anthropic.com/engineering/infrastructure-noise). The lesson is scoped but important: CPU, RAM, timeout, image, and retry settings are benchmark variables.

### 3.3 Durable orchestration

Shard by `(suite_version, item_id, candidate_id, trial_index)` and make that tuple the idempotency key `[inferred]`. A controller writes work to a durable queue; workers lease, heartbeat, checkpoint, and atomically publish artifacts before completion. Grading runs separately from generation so grader changes can rescore immutable traces without rerunning expensive agents. Aggregation reads only terminal, policy-valid trial records.

Use these resilience controls `[inferred]`:

- token-bucket quotas and per-provider/model/tool concurrency;
- circuit breakers per dependency with half-open probes;
- deadlines propagated to model, tool, environment, and grader calls;
- checkpoint after each irreversible state transition or long phase;
- content-addressed artifact storage with checksums and retention class;
- dead-letter queue for repeated infrastructure failures;
- canary subset before launching a large matrix;
- deterministic environment-health tests before agent admission;
- comparison abort if missingness or infrastructure errors differ materially by candidate.

Inspect logs support drilling into messages, scoring, and metadata and expose programmatic log APIs [[9]](https://inspect.aisi.org.uk/log-viewer.html). `[inferred]` The production store additionally needs access control, retention, lineage, and immutable run/grader links.

### 3.4 Drift and benchmark lifecycle

Maintain separate **capability**, **regression**, **fresh temporal holdout**, **adversarial**, and **production shadow** sets. Promote solved capability cases into regression; replace saturated challenge items; retain historical results under their original version. BrowseComp’s launch results include a disclosure that Deep Research had been trained on similar tasks, showing why benchmark exposure must accompany scores [[26]](https://openai.com/index/browsecomp/). A 2025 controlled contamination study across 10 models, five benchmarks, 20 mitigation strategies, and two contamination scenarios found no tested update strategy simultaneously solved fidelity and contamination resistance across benchmarks [[33]](https://proceedings.mlr.press/v267/sun25t.html).

OpenAI’s 2026 review concluded SWE-bench Verified was no longer useful for frontier coding evaluation because of contamination and flawed/ambiguous tests, despite its earlier value [[23]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/). Benchmark retirement is a quality-control action, not loss of historical data `[inferred]`.

> ⚠️ Limited public data available for this dimension. Benchmark papers rarely publish recovery-point objectives, queue/lease designs, multi-region disaster recovery, or reproducibility success rates for distributed evaluation farms.

## 4. Enterprise Security & Governance

### 4.1 Threat model

Evaluation assets are targets: hidden test inputs, expected outputs, policy documents, judge rubrics, model credentials, production traces, and human labels can leak or be manipulated. The system under test may execute adversarial code or use tools; evaluated output can prompt-inject a model grader; a developer may overfit to a release gate; an evaluator may infer protected user data `[inferred]`.

Controls by boundary:

| Boundary | Controls `[inferred]` |
|---|---|
| Suite/dataset | classification, owner, purpose/license, immutable version, row-level RBAC, encryption, access audit, blind holdout |
| Runner | ephemeral non-root sandbox/VM, no host socket, resource/PID/disk limits, default-deny egress, scoped synthetic credentials |
| Tool environment | seeded synthetic data, least privilege, idempotency, no production writes, destination allowlist |
| Grader | treat candidate output as quoted untrusted data, structured rubric, no secrets/tools, separate model, injection tests |
| Trace/artifact | redact/tokenize PII and secrets, tenant isolation, retention/deletion, integrity hash, controlled raw-content access |
| Human labeling | need-to-know access, training, conflicts/blinding, secure workspace, adjudication and quality audit |
| Release decision | signed gate, separation of duties, documented exception, rollback trigger, expiry |

Inspect supports Docker, Kubernetes, and other sandbox providers for untrusted model code and tool execution [[7]](https://inspect.aisi.org.uk/) [[10]](https://inspect.aisi.org.uk/sandboxing.html). Sandboxing reduces execution authority but does not replace network, data, secret, and tool policy `[inferred]`.

### 4.2 Grader and benchmark attacks

- **Prompt injection:** candidate output tells the judge to assign a high score or disclose rubric. Delimit untrusted content, prohibit instruction following from it, test attack strings, and use deterministic checks for hard gates `[inferred]`.
- **Reward/grader hacking:** optimization finds rubric defects. OpenAI recommends comparing model-grader results with expert evaluations to detect high automated/low expert performance [[5]](https://developers.openai.com/api/docs/guides/graders).
- **Reference exfiltration:** the agent retrieves hidden tests or expected state. Use network isolation, filesystem boundaries, canaries, temporal/private sets, and audit queries `[inferred]`.
- **Task contamination:** test items enter training or prompt libraries. Maintain provenance, exposure disclosures, private holdouts, and fresh items; do not assume paraphrasing removes contamination [[33]](https://proceedings.mlr.press/v267/sun25t.html).
- **Benchmark gaming:** teams tune only the public score while production slices regress. Gate on private, safety, regression, and shadow suites with independent ownership `[inferred]`.
- **Human-label manipulation:** unblinded annotators see candidate identity or business pressure. Randomize/blind identity and order, measure agreement, adjudicate, and retain raw labels `[inferred]`.

AgentDojo’s 97 tasks and 629 security test cases evaluate prompt injection through untrusted tool data, demonstrating that ordinary task success must be paired with security success [[41]](https://arxiv.org/abs/2406.13352). An agent that achieves the task by violating policy is not a successful trial.

### 4.3 Privacy, audit, and compliance evidence

Trace content can include prompts, personal data, API arguments/results, source documents, credentials, and reasoning-like text. OpenTelemetry explicitly warns that tool-call arguments/results and workflow names can contain sensitive information [[36]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Default to metadata-only telemetry; opt in to raw content by environment and classification, with field-level redaction, encryption, access logging, and bounded retention `[inferred]`.

Minimum immutable audit event `[inferred]`: actor/workload identity, tenant, run/suite/item/trial/span IDs, model/harness/tool/environment/grader versions, authorization decision, timestamps, normalized action or protected hash, state outcome, tokens/cost/latency, score/uncertainty, exclusion reason, human annotator/adjudicator pseudonym, release decision, and artifact hashes.

NIST’s AI RMF organizes voluntary risk management across Govern, Map, Measure, and Manage; the Generative AI Profile adds generative-AI risks and actions [[14]](https://www.nist.gov/itl/ai-risk-management-framework) [[46]](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf). These are governance frameworks, not certifications or metric specifications. NIST’s TEVV program explicitly frames testing, evaluation, validation, and verification as broader than a single automated benchmark [[13]](https://www.nist.gov/ai-test-evaluation-validation-and-verification-tevv).

### 4.4 Governance lifecycle

`[inferred]` Assign independent owners for product objective, dataset, harness, graders/statistics, security, and release. Each suite needs an evaluation card: construct, population, task sampling, environment, metrics, known failure modes, judge calibration, uncertainty, sensitivity, intended/non-intended use, contamination exposure, and retirement trigger. Changes to a reference, grader, task, environment, or exclusion rule create a new version and require backfill or explicit non-comparability.

OpenAI’s current evaluation best-practices page states that its legacy Evals platform is scheduled to become read-only on October 31, 2026 and shut down on November 30, 2026 [[2]](https://developers.openai.com/api/docs/guides/evaluation-best-practices). This is a concrete reminder that evaluation evidence must remain portable rather than depending on one hosted dashboard.

## 5. Production Failure Modes

| Failure mode | Symptom / detection | Mitigation / disposition |
|---|---|---|
| Construct mismatch | score rises but user/business outcome does not | rewrite objective from production decision; validate with users/SMEs `[inferred]` |
| Unrepresentative dataset | production slices absent; class/base-rate skew | sample production taxonomy; stratify and weight transparently `[inferred]` |
| Benchmark contamination | public score-production gap; memorized artifacts | private temporal holdouts, exposure audit, retire saturated items [[33]](https://proceedings.mlr.press/v267/sun25t.html) |
| Broken/ambiguous task | capable agents fail for contradictory spec/test | automated QA plus human review; exclude with versioned reason [[23]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/) |
| Environment leakage | later trial benefits from earlier files/cache/state | clean reset, isolated identity/storage, canary artifacts [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Environment drift | website/API/data changes mid-comparison | snapshot or pair by time/seed; record version; separate live/frozen results `[inferred]` |
| Resource-induced score | candidate receives different CPU/RAM/timeout | pin and record resources; environment health gate [[27]](https://www.anthropic.com/engineering/infrastructure-noise) |
| Silent retry inflation | failed agent attempts retried until pass | retain all attempts; predeclare infra retry; report pass@1 and pass^k `[inferred]` |
| Wrong metric denominator | exclusions or invalid trials disappear | publish flow counts and reasons; sensitivity analysis `[inferred]` |
| Macro/micro masking | frequent easy tasks dominate or tiny slices dominate | report macro, micro, business-weighted, and slice denominators `[inferred]` |
| Pseudo-replication | narrow CI from treating steps/trials as independent | cluster/hierarchical analysis at task/user level [[12]](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models) |
| Multiple-comparison fishing | one of many slices appears “significant” | predeclare primary tests; adjust or label exploratory `[inferred]` |
| Capability/regression confusion | saturated eval cannot rank improvements | graduate solved items to regression; refresh capability set [[1]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| Exact-path brittleness | valid alternative trajectory fails | grade outcome/invariants; exact order only for policy dependencies `[inferred]` |
| Success-only latency | tails/timeouts vanish from report | include admitted/censored/failed distribution and timeout rate `[inferred]` |
| Token-only cost | compute, tools, scans, and humans omitted | metered full cost and cost per compliant success `[inferred]` |
| Tool schema pass, semantic fail | valid JSON targets wrong account/resource | execute in stateful environment; compare intended state delta [[17]](https://aclanthology.org/2025.findings-naacl.65/) |
| Side-effect duplication | retry repeats purchase/ticket/refund | idempotency and post-state reconciliation; count unsafe writes `[inferred]` |
| Model-judge position bias | winner flips when answer order swaps | randomized swap test; calibrate by slice [[30]](https://arxiv.org/abs/2406.07791) |
| Verbosity/self preference | long or same-family output wins unfairly | criterion anchors, blind identity, length controls, human calibration [[29]](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html) |
| Judge drift | same trace receives changed scores after model update | pin judge snapshot; version; frozen calibration; parallel old/new judge `[inferred]` |
| Judge prompt injection | evaluated text instructs judge | quote as untrusted, no tools/secrets, injection suite, deterministic hard gate `[inferred]` |
| Reward hacking | automated score high, expert quality low | holdout expert audit and adversarial judge eval [[5]](https://developers.openai.com/api/docs/guides/graders) |
| Human rater drift/fatigue | agreement drops over batch/time | gold checks, overlap, breaks, retraining, adjudication `[inferred]` |
| Simulator bias | agent optimizes quirks of synthetic user | multiple simulators, human conversations, outcome-based grading `[inferred]` |
| Trace sampling bias | only errors or only successes retained | stratified retention plus random sample; publish inclusion policy `[inferred]` |
| PII/secret leakage | raw prompts/tools appear in logs or judge input | classification, redaction, restricted raw store, retention/deletion [[36]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/) |
| Flaky grader | score varies on deterministic artifact | repeat/calibrate, deterministic assertions, judge variance metric `[inferred]` |
| Composite-score hiding | safety decline offset by style gain | non-compensable gates and per-dimension reporting `[inferred]` |
| Goodhart/release overfit | candidate tuned narrowly to gate | independent holdout owner, periodic refresh, shadow outcomes `[inferred]` |

Public benchmarks provide useful patterns but different constructs. Original SWE-bench tests repository issue resolution [[22]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html); WebArena tests executable web tasks [[24]](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf); GAIA combines reasoning, multimodality, browsing, and tools across 466 questions [[42]](https://arxiv.org/abs/2311.12983); frozen Deep Research Bench evaluates research over a stable corpus with trajectory checks [[43]](https://arxiv.org/abs/2506.06287); ScienceAgentBench evaluates executable analysis tasks from scientific papers [[44]](https://arxiv.org/abs/2410.05080). Scores across them are not directly comparable.

> ⚠️ Limited public data available for this dimension. Detailed production postmortems linking evaluator defects to release incidents, unsafe actions, or financial loss are rare; most evidence is benchmark analysis, controlled research, or vendor engineering reports.

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A: policy-constrained support agent

**Goal:** release a model/harness update for an agent that reads accounts, changes bookings, and issues refunds under policy.

**Design `[inferred]`:** build a stateful simulator with a versioned database, API tools, policy documents, diverse multi-turn users, and exact final-state/communication checks. Generate a paired matrix over task, user persona, environment seed, candidate, and at least several repeated trials for a reliability subset. Grade policy-compliant outcome first, then tool lifecycle, progress/recovery, final-response rubric, cost, and latency. Add injection, ambiguous-request, outage, timeout-after-write, and escalation tasks. Use `pass^k` for consistency, not `pass@k`.

**Release gate `[inferred]`:** no regression in policy-compliant success lower bound, hard ceiling for unauthorized/duplicate writes, critical policy slices above floor, judge-human calibration passed, p95 task latency and cost/success within budget. Shadow with write tools disabled; then canary a small eligible population with idempotency and instant rollback. τ-bench supplies a benchmark pattern, but company policy and tools require private tasks [[15]](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html).

### 6.2 Scenario B: coding-agent model migration

**Goal:** replace the coding model across many languages/repos without increasing regressions or CI cost.

**Design `[inferred]`:** create temporal issue tasks from repositories the candidate could not train on; pin base commits, dependency images, CPU/RAM, and tests; run one worktree/container per trial. Deterministic graders cover fail-to-pass, pass-to-pass, build/type/lint/security, and file policy. Human reviewers grade architectural fit and maintainability on a blinded stratified sample. Record turns, repeated actions, tests, diff size, sandbox minutes, tokens, total cost, time-to-green, and accepted-without-major-rework.

**Statistics `[inferred]`:** pair baseline/candidate on each task, use task-clustered intervals, and separate invalid infrastructure trials. Report success at fixed budget, cost/success, p95 duration, and critical security/repository slices. Do not gate on current SWE-bench Verified rank alone because OpenAI’s 2026 analysis identifies contamination and task/test defects at the frontier [[23]](https://openai.com/index/separating-signal-from-noise-coding-evaluations/).

### 6.3 Scenario C: enterprise research/RAG agent

**Goal:** answer internal questions with citations across confidential and public corpora.

**Design `[inferred]`:** separate retrieval component evals (recall at fixed candidate pool, access-policy correctness, freshness), claim-level evidence evals (entailment, citation precision/coverage), report rubric (correctness, completeness, usefulness, uncertainty), and end-to-end task success. Use a frozen corpus for reproducible regression plus fresh temporal cases for current information. Store document snapshot/hash, retrieval set/rank, claim-to-source mapping, code calculations, judge version, tokens, search fees, latency, and reviewer corrections.

**Security `[inferred]`:** confidential documents never enter public graders; candidate output and sources are untrusted to the judge; PII is redacted before ordinary traces; raw evaluation artifacts have restricted retention. Hard-fail cross-tenant retrieval even if the answer is high quality. Deep Research Bench’s frozen corpus addresses reproducibility, while live research remains necessary for freshness [[43]](https://arxiv.org/abs/2506.06287).

### 6.4 Scenario D: shared enterprise evaluation platform

**Scale assumptions `[inferred]`:** 20 teams, 50 suites, 10,000 tasks/day, multiple providers, browsers and code sandboxes, confidential traces, release-gate SLA.

**Architecture `[inferred]`:** Git/registry stores suite definitions and signed manifests; object storage holds versioned datasets/traces/artifacts; scheduler expands run matrix into a durable queue; separate Kubernetes worker pools handle API-only, browser, and sandbox workloads; secret broker issues task-scoped credentials; OpenTelemetry collector receives metadata spans; grader workers are isolated from candidate runners; statistics service creates immutable comparison reports; policy engine enforces dataset/tool/model region and retention; release API writes signed decisions and exception records.

**Capacity `[inferred]`:** admission computes predicted token, judge, sandbox, and wall-clock budget. Per-provider token buckets and per-pool concurrency prevent one suite starving others. A canary shard validates environment health and cost before full fan-out. Intermediate artifacts checkpoint after each task; scoring can replay without generation. The platform exports OpenTelemetry-compatible metadata but stores raw sensitive content in a separate access-controlled artifact system [[34]](https://opentelemetry.io/blog/2026/genai-observability/) [[36]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).

### 6.5 Trade-off matrix

| Choice | Strength | Risk | Use when `[inferred]` |
|---|---|---|---|
| Deterministic outcome grader | fast, reproducible, auditable | incomplete tests; gaming | state and policy can be encoded |
| Model rubric grader | scalable semantic judgment | bias, injection, drift, cost | calibrated against humans and not sole hard gate |
| Human expert | construct validity and adjudication | cost, latency, rater variability | consequential ambiguity and judge calibration |
| Exact trajectory | catches protocol violations | rejects valid alternate paths | order/actions are mandatory policy |
| Outcome + invariant trace checks | allows creativity, catches critical behavior | harder grader design | most open-ended agents |
| Replay/off-policy | cheap rescoring and debugging | cannot capture behavioral change | grader iteration on immutable traces |
| Live/on-policy | realistic interaction | expensive, variable, risky | end-to-end release evidence |
| Frozen benchmark | reproducible comparisons | staleness/contamination | regression and longitudinal evidence |
| Fresh/private temporal set | lower exposure, current | smaller, expensive to maintain | capability and release gating |
| One trial/task | broad coverage at fixed budget | hides stochastic unreliability | deterministic components only |
| Repeated trials | reliability and variance | multiplicative cost | agentic/stochastic high-impact tasks |

### 6.6 Principal-architect interview synthesis

1. **Name the construct and outcome state.** “Helpful” is not a metric; “policy-compliant refund exists in the ledger and the customer received correct terms” is testable.
2. **Evaluate the system, not the model label.** Harness, tools, simulator, resources, retries, and grader are part of the result.
3. **Keep six dimensions visible.** Task success, trajectory, tool accuracy, quality, cost, and latency answer different questions; safety gates must not be averaged away.
4. **Use the strongest available oracle.** Prefer executable state and policy assertions, then calibrated model rubrics, then targeted human adjudication.
5. **Treat stochasticity statistically.** Repeated trials are nested within tasks; pair candidates, report intervals/deltas, and distinguish fixed-benchmark from generalized claims [[12]](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models).
6. **Distinguish capability from reliability.** `pass@k` rewards finding one correct sample; `pass^k` demands all repeated attempts succeed.
7. **Version and secure the evidence.** Dataset, model, scaffold, environment, grader, statistics, exclusions, and release decision need lineage and access control.
8. **Retire broken or saturated tests.** Historical comparability is preserved by versioning, not by pretending a benchmark remains valid forever.

## Sources

- [1] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents — Current agent-evaluation definitions, grader types, harness design, and lifecycle.
- [2] https://developers.openai.com/api/docs/guides/evaluation-best-practices — OpenAI evaluation design guidance and current legacy-platform lifecycle notice.
- [3] https://developers.openai.com/api/docs/guides/evals — OpenAI Evals API workflow.
- [4] https://developers.openai.com/api/docs/guides/trace-grading — OpenAI trace grading and trace evaluations.
- [5] https://developers.openai.com/api/docs/guides/graders — OpenAI deterministic, similarity, model, and Python graders; grader hacking.
- [6] https://adk.dev/evaluate/ — Google ADK trajectory, tool-use, response, and multi-turn evaluation criteria.
- [7] https://inspect.aisi.org.uk/ — UK AI Security Institute Inspect evaluation framework.
- [8] https://inspect.aisi.org.uk/parallelism.html — Inspect concurrency, resource limits, and recovery trade-offs.
- [9] https://inspect.aisi.org.uk/log-viewer.html — Inspect evaluation-log and trace inspection.
- [10] https://inspect.aisi.org.uk/sandboxing.html — Inspect sandbox providers and execution isolation.
- [11] https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf — NIST draft practices for automated benchmark evaluation.
- [12] https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models — NIST AI 800-3 on benchmark/generalized accuracy and statistical models.
- [13] https://www.nist.gov/ai-test-evaluation-validation-and-verification-tevv — NIST AI TEVV program.
- [14] https://www.nist.gov/itl/ai-risk-management-framework — NIST AI Risk Management Framework.
- [15] https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html — τ-bench and pass^k reliability.
- [16] https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md — τ2-bench evaluation specification.
- [17] https://aclanthology.org/2025.findings-naacl.65/ — ToolSandbox stateful, interactive tool-use evaluation.
- [18] https://proceedings.mlr.press/v267/patil25a.html — Berkeley Function Calling Leaderboard paper.
- [19] https://proceedings.neurips.cc/paper_files/paper/2024/hash/877b40688e330a0e2a3fc24084208dfa-Abstract-Datasets_and_Benchmarks_Track.html — AgentBoard and progress-rate evaluation.
- [20] https://openreview.net/forum?id=zAdUB0aCTQ — AgentBench across eight interactive environments.
- [21] https://openai.com/index/paperbench/ — PaperBench hierarchical rubrics and JudgeEval.
- [22] https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html — Original SWE-bench environment and evaluation.
- [23] https://openai.com/index/separating-signal-from-noise-coding-evaluations/ — 2026 SWE-bench Verified validity review.
- [24] https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf — WebArena executable web evaluation.
- [25] https://arxiv.org/abs/2404.07972 — OSWorld computer-use benchmark.
- [26] https://openai.com/index/browsecomp/ — BrowseComp and similar-task training disclosure.
- [27] https://www.anthropic.com/engineering/infrastructure-noise — Infrastructure effects on agent-evaluation scores.
- [28] https://aclanthology.org/2023.emnlp-main.153/ — G-Eval and scoped human correlation.
- [29] https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html — MT-Bench, Chatbot Arena, and LLM-judge biases.
- [30] https://arxiv.org/abs/2406.07791 — Systematic study of LLM-judge position bias.
- [31] https://arxiv.org/abs/2405.01535 — Prometheus 2 evaluator models.
- [32] https://arxiv.org/abs/2606.00093 — Agreement metrics for LLM-as-judge validation.
- [33] https://proceedings.mlr.press/v267/sun25t.html — Controlled study of benchmark-contamination mitigations.
- [34] https://opentelemetry.io/blog/2026/genai-observability/ — GenAI agent/tool trace and metrics example.
- [35] https://opentelemetry.io/docs/specs/otel/trace/api/ — OpenTelemetry trace and span model.
- [36] https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ — GenAI semantic attributes and sensitive-content warnings.
- [37] https://arxiv.org/abs/2107.03374 — HumanEval/Codex paper and pass@k estimator.
- [38] https://arxiv.org/abs/2211.09110 — HELM holistic evaluation framework.
- [39] https://arxiv.org/abs/1810.03993 — Model Cards for Model Reporting.
- [40] https://arxiv.org/abs/1803.09010 — Datasheets for Datasets.
- [41] https://arxiv.org/abs/2406.13352 — AgentDojo prompt-injection security evaluation.
- [42] https://arxiv.org/abs/2311.12983 — GAIA general AI assistant benchmark.
- [43] https://arxiv.org/abs/2506.06287 — Frozen-corpus Deep Research Bench and trajectory evaluation.
- [44] https://arxiv.org/abs/2410.05080 — ScienceAgentBench executable scientific analysis evaluation.
- [45] https://www.anthropic.com/engineering/multi-agent-research-system — Multi-agent research evaluation and scoped token economics.
- [46] https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf — NIST Generative AI Profile.
