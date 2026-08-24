# Research: Planning & Reasoning - Decomposition, Reflection, Verification, Replanning

**Date researched**: 2026-08-21
**Sources consulted**: 38

## Scope and evidence labels

This brief treats planning and reasoning as an observable control process around a model, not as a claim that a model's hidden chain of thought is a reliable execution plan. It covers the four requested operations: decomposition, reflection, verification, and replanning. Plain factual claims are supported by first-party documentation or primary papers. `[inferred]` marks a production recommendation derived from those sources. Historical benchmark numbers are reported with the model, dataset, and experimental scope in which they were measured; they are not forecasts for a current production system.

## 1. System Topology & Mechanics

### Planning is a control artifact, reasoning is a capability

A useful distinction is:

- **Reasoning** transforms evidence and constraints into candidate conclusions or actions. It may occur inside one model call or across an external search process.
- **Planning** creates and maintains an executable representation of how a goal might be reached: ordered or partially ordered steps, dependencies, preconditions, expected effects, checks, budgets, and stopping conditions.
- **Execution** changes an environment through tools or people and returns observations.
- **Control** decides whether to continue, verify, retry, repair the plan, escalate, or stop.

Chain-of-thought prompting established that eliciting intermediate reasoning could improve historical arithmetic, commonsense, and symbolic results; for example, the paper reported state-of-the-art GSM8K accuracy from a 540B-parameter model with eight demonstrations. [[1]](https://arxiv.org/abs/2201.11903) That result does not make free-form reasoning text an execution contract. A production controller needs a smaller, explicit artifact that can be validated and resumed.

`[inferred]` A plan record should contain public operational fields rather than depend on private model deliberation:

```json
{
  "plan_id": "plan_01J...",
  "generation": 3,
  "goal": "restore checkout while preserving paid orders",
  "constraints": ["no destructive database operation", "change requires approval"],
  "assumptions": [{"claim": "replica is current", "evidence_ref": "obs_92"}],
  "steps": [
    {
      "id": "s4",
      "depends_on": ["s3"],
      "action": "shift 10 percent traffic to release-51",
      "preconditions": ["release-51 health checks pass"],
      "expected_effects": ["error rate remains below policy threshold"],
      "verifier": "deployment_canary_policy_v6",
      "compensation": "shift traffic to release-50",
      "status": "ready"
    }
  ],
  "budgets": {"model_calls": 20, "tool_calls": 30, "wall_seconds": 900},
  "stop_conditions": ["goal verified", "budget exhausted", "human rejects"],
  "policy_version": "prod-change-12"
}
```

This representation lets a system explain what it intends to do without exposing or storing unrestricted chain-of-thought. It also creates stable boundaries for authorization, distributed execution, verification, and audit.

### Decomposition

Decomposition turns a goal into units that can be solved, verified, scheduled, retried, or delegated. The major patterns are different tools, not a maturity ladder:

| Pattern | Mechanics | Good fit | Main risk |
|---|---|---|---|
| Prompt-local steps | model lists and solves steps in one response | bounded analysis with no external side effects | missing or invented dependencies are hard to detect |
| Least-to-most | decompose into simpler subproblems and solve sequentially using earlier answers | compositional problems where later answers depend on earlier ones | early errors propagate |
| Plan then execute | create the whole task plan, then run steps | workflows with known constraints and review points | plan becomes stale after observations |
| DAG / orchestrator-workers | create dependency-aware tasks, run independent work in parallel, synthesize | research, coding, document processing | duplicate work, conflicting writes, weak synthesis |
| Tree/search | branch into candidate thoughts or plans, score, prune, and backtrack | ambiguous tasks with cheap candidate evaluation | exponential cost and evaluator bias |
| Formal planner / solver | translate goal and domain into a formal model, solve, then translate back | scheduling, routing, constraint satisfaction | incorrect translation or incomplete domain model |

Least-to-Most Prompting explicitly separates decomposition from sequential subproblem solving. On the historical SCAN compositional-generalization setup, code-davinci-002 with 14 exemplars achieved at least 99% across splits versus 16% for the paper's chain-of-thought baseline. [[2]](https://arxiv.org/abs/2205.10625) Plan-and-Solve likewise first devises subtasks and then executes them; its authors motivated the method by missing-step errors in zero-shot chain of thought and added PS+ instructions for calculation and reasoning quality. [[3]](https://arxiv.org/abs/2305.04091)

The orchestrator-workers pattern uses a central model to create subtasks dynamically, delegates them, and synthesizes the results. Anthropic recommends it for tasks whose required subtasks cannot be predicted in advance, such as multi-file code changes or open-ended search. [[4]](https://www.anthropic.com/engineering/building-effective-agents) A dependency graph is preferable to a flat list when work can run concurrently or when one step consumes another's artifact.

Tree of Thoughts generalizes linear reasoning into search: generate intermediate candidates, evaluate them, and explore or backtrack. In its historical Game of 24 experiment, GPT-4 with chain-of-thought solved 4% while the paper's Tree of Thoughts configuration solved 74%. [[5]](https://arxiv.org/abs/2305.10601) The gain is task- and search-policy-specific. If branching factor is `b` and search depth is `d`, naive enumeration can require `O(b^d)` candidate evaluations.

Some tasks need a formal feasibility guarantee rather than better prose. LLM+P translates a natural-language problem into Planning Domain Definition Language, uses an external planner, and translates the resulting plan back. [[6]](https://arxiv.org/abs/2304.11477) LLM-Modulo proposes a broader loop in which an LLM supplies approximate knowledge while sound external verifiers reject invalid candidates and return feedback. [[7]](https://arxiv.org/abs/2402.01817) The LLM is valuable for interpretation and heuristics; the solver/verifier owns the claim it can actually prove.

Feasibility may also come from learned environment-specific affordances. SayCan combines a language model's estimate that a skill is useful for the instruction with value functions estimating whether the robot can execute that skill in its current environment. [[37]](https://arxiv.org/abs/2204.01691) This separates a semantically plausible next step from an embodied, currently achievable one.

`[inferred]` Good decomposition satisfies four testable properties:

1. **Coverage**: every acceptance criterion maps to at least one step and final check.
2. **Dependency correctness**: inputs exist before consumption; no circular dependency exists.
3. **Executability**: every leaf is assigned to a known tool, service, person, or solver.
4. **Boundedness**: the graph has call, time, cost, and depth limits plus terminal states.

Avoid decomposition when one deterministic API call or one model response already solves the task. Every extra planning call adds tokens, latency, failure surfaces, and state to reconcile.

### Reflection

Reflection is feedback-driven revision, not merely asking a model to "think again." Separate three forms:

- **Candidate critique**: inspect a draft plan or answer against explicit criteria before execution.
- **Execution reflection**: interpret a failed check or environment observation and propose a repair.
- **Episodic reflection**: after a completed attempt, preserve a concise lesson for a later attempt.

Self-Refine uses one model iteratively as generator, feedback provider, and refiner. Its paper reported about 20 percentage points average absolute improvement across seven tasks, under its particular automatic and human evaluations. [[8]](https://arxiv.org/abs/2303.17651) Reflexion stores linguistic feedback from prior attempts in episodic memory rather than updating model weights; it reported 91% HumanEval pass@1 versus an 80% GPT-4 reference in that historical setup. [[9]](https://arxiv.org/abs/2303.11366)

Those results do not establish intrinsic self-critique as an oracle. A separate study found that models prompted to self-correct reasoning without external feedback sometimes degraded their initial answers and argued that apparent gains can depend on answer extraction, oracle labels, or other feedback. [[10]](https://arxiv.org/abs/2310.01798) CRITIC instead grounds critique in tool interaction such as search or code execution, emphasizing that external evidence is important to correction. [[11]](https://arxiv.org/abs/2305.11738)

Anthropic's evaluator-optimizer workflow similarly assumes a response can be measurably improved against clear criteria and loops generator output through evaluator feedback. [[4]](https://www.anthropic.com/engineering/building-effective-agents) It is not appropriate when correctness is subjective but the evaluator is treated as definitive, or when iterations have no measurable stopping rule.

`[inferred]` A bounded reflection contract should be structured:

```text
input: candidate artifact + requirements + trusted observations
output:
  failed_criteria[]
  evidence_refs[]
  proposed_changes[]
  confidence / unresolved_questions
controller:
  max_iterations
  minimum measurable improvement
  stop on repeated critique fingerprint
  escalate when no admissible repair exists
```

Do not let a generated postmortem overwrite raw tool outputs. A reflection is a hypothesis about why something happened; verified events remain the evidence.

### Verification

Verification answers a narrower question than reflection: does an artifact or state meet an explicit condition? The strongest available verifier depends on the domain.

| Layer | What to verify | Preferred oracle |
|---|---|---|
| Request | goal, identity, constraints, ambiguity | authenticated input, policy, human clarification |
| Plan | coverage, dependency graph, preconditions, feasibility | schema, graph checks, constraint solver, policy engine |
| Step | tool arguments and authorization before action | typed validation, allowlist, current state, approval |
| Result | expected state transition occurred | API/database readback, unit test, sensor, signed response |
| Final | original goal and all constraints satisfied | end-state evaluator independent of model wording |

Training Verifiers to Solve Math Word Problems generated many candidate solutions and trained a verifier to rank them; the paper found verifier scaling more effective than equivalent generator fine-tuning in its GSM8K experiments. [[12]](https://arxiv.org/abs/2110.14168) Self-consistency samples diverse reasoning paths and selects the most consistent answer, producing historical gains across arithmetic and commonsense tasks. [[13]](https://arxiv.org/abs/2203.11171) Agreement, however, is not proof: correlated candidates can share the same false premise.

Process supervision can localize an error earlier than final-outcome scoring. Let's Verify Step by Step reported 78% on a representative subset of MATH for its process-supervised model and released PRM800K with 800,000 step-level human labels. [[14]](https://arxiv.org/abs/2305.20050) This is evidence from one mathematical setting, not a universal claim that a learned process reward model proves correctness.

Tool-backed verification is usually stronger where an executable oracle exists. Program-Aided Language Models has the model generate a program but delegates arithmetic and symbolic execution to an interpreter; its Codex configuration exceeded the cited PaLM-540B chain-of-thought baseline on GSM8K by 15 absolute percentage points. [[15]](https://arxiv.org/abs/2211.10435) Chain-of-Verification creates a draft, plans verification questions, answers those questions independently, and produces a revised answer; the paper reported reduced hallucination on its selected list-QA and long-form generation tasks. [[16]](https://arxiv.org/abs/2309.11495)

Robust LLM-Modulo pairs a generator with a complete set of sound verifiers and re-prompts on rejection; in the four scheduling domains evaluated by the paper, accepted outputs inherit the verifiers' correctness guarantees. [[17]](https://arxiv.org/abs/2411.14484) The guarantee is only as complete as the formalized constraints and only applies to properties those verifiers check.

`[inferred]` Verification should be independent along at least one axis: different mechanism, evidence, model, prompt, or owner. Repeating the same prompt to the same model at temperature zero has little epistemic diversity. For high-impact actions, deterministic policy checks and authoritative state reads should precede any model-judge signal.

### Replanning

Replanning updates an executable plan when the environment, goal, constraints, or evidence no longer matches its assumptions. ReAct interleaves reasoning traces with actions and observations, allowing a model to update its course and handle exceptions rather than commit to a static plan. In its historical experiments, ReAct improved absolute success over cited baselines by 34 points on ALFWorld and 10 on WebShop with one or two in-context examples. [[18]](https://arxiv.org/abs/2210.03629)

Embodied-agent research makes the feedback loop explicit. Inner Monologue feeds success detection, scene descriptions, and human feedback into language-model planning and reported improved instruction completion across simulated and real robotic domains. [[19]](https://arxiv.org/abs/2207.05608) DEPS describes execution, explains failures, replans, and selects among parallel subgoals; the paper reported accomplishing more than 70 Minecraft tasks and broader gains in its evaluated environments. [[20]](https://arxiv.org/abs/2302.01560) A 2026 embodied preprint, RePlan-Bot, separates high-level subgoal auditing, object search, and low-level action correction, illustrating that replanning may occur at multiple control levels. [[21]](https://arxiv.org/abs/2605.25851)

Voyager combines an automatically generated curriculum, an executable skill library, and iterative prompting that incorporates environment feedback, execution errors, and self-verification. Its Minecraft experiments reported 3.3 times more unique items, 2.3 times greater travel distance, and milestones unlocked up to 15.3 times faster than the paper's prior-state-of-the-art baselines. [[38]](https://arxiv.org/abs/2305.16291) These are environment-specific results, but the architecture demonstrates how planning horizon can be extended by retrieving verified reusable skills rather than regenerating every low-level action.

`[inferred]` Replan on an event, not on every token or every successful step. Useful triggers are:

- a precondition is false or required input is unavailable;
- observed effects differ materially from expected effects;
- a tool returns a non-transient business error;
- a new authenticated constraint or goal arrives;
- a dependency changes or a plan assumption expires;
- the verifier rejects the current plan or output;
- the cost, time, risk, or depth budget crosses a threshold.

Choose the smallest repair that restores validity:

```text
observation
 -> normalize and authenticate source
 -> update world/task state with version check
 -> classify: transient | plan defect | changed goal | policy block | unknown
 -> transient: retry within policy
 -> plan defect: repair invalid suffix or affected subgraph
 -> changed goal: create new plan generation
 -> policy block / irreversible conflict: pause and escalate
 -> verify repaired plan before resuming
```

Preserve completed, still-valid work. Full replanning can discard useful artifacts, repeat side effects, and oscillate between equally plausible strategies. A repaired plan should identify which old steps remain valid, which are invalidated, and why.

### End-to-end controller pattern

```python
def run_goal(goal, principal, budget):
    state = load_or_create_state(goal, principal, budget)
    while not state.terminal:
        enforce_budget(state)

        if state.plan is None or state.replan_reason:
            candidate = planner.propose(state.public_context())
            check_plan_schema(candidate)
            verify_coverage_dependencies_and_policy(candidate, state)
            state.install_new_generation(candidate)
            checkpoint(state)

        step = state.next_ready_step()
        check_current_preconditions(step, state)
        authorize(principal, step.tool, step.args, state.policy_version)
        maybe_request_approval(step, state)

        result = execute_idempotently(step, key=(state.run_id, step.id))
        state.record_result(result)
        verification = verify_expected_effects(step, result, authoritative_readback())

        if verification.passed:
            state.complete(step)
        else:
            reflection = critic.diagnose(step, verification.evidence)
            state.request_replan(reflection, verification)
        checkpoint(state)

    return verify_final_goal(state)
```

`[inferred]` The model may propose the transition, but ordinary code should own budgets, permissions, state versions, terminal conditions, and irreversible side effects.

## 2. Token Economics & NFR Metrics

### Cost model

Planning methods move work from one generation into a compound inference-and-execution system:

```text
model_tokens = decomposition
             + sum(step_generation)
             + candidate_search
             + reflection_iterations
             + verification_model_calls
             + replanning_generations

external_cost = tool/API execution
              + solver/search/interpreter compute
              + durable state, traces, and human review

cost_per_verified_success = total end-to-end cost
                          / tasks whose end state passes an independent oracle
```

For `n` self-consistency samples, generation cost is approximately linear in `n` before caching and batching. For a tree with branching factor `b` and depth `d`, the unpruned number of nodes is `(b^(d+1)-1)/(b-1)`. A planner-worker graph may reduce wall time by parallelizing independent steps, but total tokens and rate-limit pressure can rise.

`[inferred]` Budget search according to the value of verification. Use one direct attempt for cheap, reversible work; add candidates, tools, or human review as impact and uncertainty rise. Do not apply a fixed reflection count to every request.

### Latency and reliability

```text
T_success = T_plan
          + max(T_parallel_ready_steps) across critical path
          + T_verification
          + sum(T_retry_backoff)
          + sum(T_reflection_and_replan)
          + T_approvals
```

Track at least:

- end-to-end p50/p95/p99 latency and time to first useful artifact;
- model calls, input/output tokens, tool calls, and human minutes per verified success;
- queue time, rate-limit time, solver time, and verifier time separately;
- plan depth, width, critical-path length, and replan count;
- fraction of work reused after a replan;
- deadline and budget exhaustion rate.

### Quality metrics by operation

| Operation | Outcome metrics | Diagnostic metrics |
|---|---|---|
| Decomposition | final task success, acceptance-criterion coverage | omitted/duplicate steps, invalid dependencies, executable-leaf rate, graph depth |
| Reflection | improvement from attempt `k` to `k+1` on independent oracle | critique precision/recall, repeated critique rate, regression rate |
| Verification | escaped-defect rate, unsafe-action block rate | false accept/reject, oracle coverage, evidence freshness, verifier disagreement |
| Replanning | recovery success after perturbation, goal completion | trigger precision, time to repair, invalidated/reused work, oscillation and loop rate |

PlanBench introduced natural-language planning and reasoning tasks based on Blocksworld and Logistics, including obfuscated domains and replanning variants, with about 26,250 prompt instances in the original benchmark. [[22]](https://arxiv.org/abs/2206.10498) A 2026 PlanningBench preprint broadens controllable instance generation and verification across more than 30 task types, reporting continued difficulty as interacting constraints increase. [[23]](https://arxiv.org/abs/2605.20873) These controlled benchmarks test formal validity well, but not all ambiguity, policy, and tool failures in enterprise work.

TravelPlanner contains 1,225 intents and a sandbox with nearly four million records; its original evaluation reported only 0.6% success for GPT-4 and identified failures in tool use, constraint tracking, and staying on task. [[24]](https://arxiv.org/abs/2402.01622) Tau-bench evaluates tool agents by comparing final database state with the goal state and introduced `pass^k` to measure repeated-trial reliability; in its original experiments, leading function-calling agents achieved under 50% task success and retail `pass^8` below 25%. [[25]](https://arxiv.org/abs/2406.12045) These historical figures are useful warnings, not current leaderboards.

For coding, the original SWE-bench included 2,294 real GitHub issues across 12 Python repositories with executable tests. [[26]](https://arxiv.org/abs/2310.06770) USACO added 307 olympiad problems with tests and analyses; its historical GPT-4 result increased from 8.7% zero-shot chain-of-thought pass@1 to 20.2% using reflection plus episodic retrieval, while targeted human hints solved 13 of 15 previously unsolved cases in a small study. [[27]](https://arxiv.org/abs/2404.10952) This illustrates both the value of feedback and the remaining gap.

GAIA's 466 real-world questions require reasoning, browsing, multimodality, and tools; its original paper reported 92% for human respondents and 15% for GPT-4 with plugins. [[28]](https://arxiv.org/abs/2311.12983) Use benchmark families, not a single score: formal planning, interactive state change, coding, browsing/research, policy following, and domain-specific outcomes exercise different failure modes.

> ⚠️ Limited public data available for this dimension. There is no current, vendor-neutral benchmark that jointly normalizes task success, constraint satisfaction, reflection quality, verifier false accepts, replan recovery, token/tool cost, latency, and human-review time across major models and orchestration systems. Production SLOs must be measured on representative tasks and failures.

### Evaluation design

`[inferred]` Build a test matrix with:

1. normal tasks at several decomposition depths;
2. missing, contradictory, and changing constraints;
3. tool timeouts, stale reads, partial writes, and rate limits;
4. adversarial retrieved instructions and poisoned feedback;
5. verifier false-positive/false-negative cases;
6. irreversible steps and denied approvals;
7. crash/restart at every step boundary;
8. repeated runs to measure stochastic reliability, not only pass@1.

Anthropic's agent-evaluation guidance distinguishes final outcomes, trajectories, and transcript evidence, and stresses that multi-turn agents require evaluations matching system complexity. [[29]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) Judge the end state with executable or authoritative checks where possible. Trajectory grading is valuable for diagnosis, but an unconventional valid plan should not fail merely for differing from one reference path.

## 3. Distributed Resilience & State

### Durable execution model

Long-running plans are distributed workflows. Workers restart, queues redeliver, APIs time out after committing, approvals arrive hours later, and plan assumptions expire. The durable source of truth should be a versioned state machine, not the model transcript.

```text
run(run_id, tenant, goal, status, plan_generation, policy_version, budget)
plan(plan_id, generation, parent_generation, reason, created_at)
step(step_id, plan_id, dependency_ids, status, attempt, lease, idempotency_key)
event(event_id, run_id, sequence, type, payload_ref, origin, timestamp)
effect(effect_id, step_id, external_system, operation_id, observed_state)
approval(approval_id, step_id, principal, decision, expires_at)
artifact(artifact_id, content_hash, schema_version, provenance)
```

`[inferred]` Use append-only events for what happened and materialized state for current scheduling. Install a new plan with compare-and-swap on `plan_generation`; otherwise two workers can publish competing replans from stale state. A worker executes only a leased, ready step whose dependencies and current preconditions still pass.

LangGraph's persistence model checkpoints graph state, retains successful pending writes when another task in the same super-step fails, and can replay or fork from prior checkpoints. Its documentation warns that nodes after a checkpoint re-execute, including model calls and API requests. [[30]](https://docs.langchain.com/oss/python/langgraph/persistence) This is why side effects require idempotency keys and readback.

### Exactly-once is an application property

A timeout means "outcome unknown," not necessarily "failed." Retrying `charge_card` or `send_email` can duplicate the effect. A robust step contract has:

- stable idempotency key derived from run and logical step, not attempt number;
- request recorded before dispatch where feasible;
- provider operation ID and response recorded;
- authoritative read-after-timeout before retry;
- bounded retry classification: transient, permanent, policy, or uncertain;
- deduplicated events and monotonic status transitions.

LangGraph interrupts persist state and resume with external input, but resume restarts the node from its beginning; its documentation explicitly requires side effects before an interrupt to be idempotent. [[31]](https://docs.langchain.com/oss/python/langgraph/interrupts) Split irreversible effects into their own node after approval so replay does not repeat preparatory work ambiguously.

### Compensation and points of no return

Not every completed action can be rolled back. The Saga pattern divides work into local transactions and uses compensating actions after failure; it distinguishes compensable steps, a pivot or point of no return, and subsequent retryable idempotent steps. [[32]](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga)

`[inferred]` Annotate every side-effecting step as one of:

- **read-only**: retry freely within rate and freshness limits;
- **idempotent write**: retry with the same key;
- **compensable write**: record and test the compensation before continuing;
- **irreversible/pivot**: require stronger verification and approval before execution;
- **manual recovery**: stop with complete evidence and an owner.

Compensation is another workflow that can fail. Persist its progress and preserve the data needed to execute it. Do not let a replan pretend an irreversible step did not happen.

### Replanning under concurrency

`[inferred]` A safe plan repair protocol is:

1. stop leasing affected steps and issue cancellation where supported;
2. wait for or classify in-flight outcomes; never assume cancellation undid an effect;
3. take a consistent snapshot of completed effects and current external state;
4. generate a candidate repair against that snapshot;
5. validate dependencies, policies, budget, and compensation;
6. atomically publish generation `g+1` if generation `g` is still current;
7. reuse immutable artifacts by content hash and resume only ready steps.

Late results from generation `g` remain events but cannot silently mutate the scheduler for generation `g+1`. The controller must reconcile them as accepted evidence, duplicate effect, or exception.

### Recovery drills

`[inferred]` Test crash recovery after plan creation, before/after each tool call, during approval, while publishing a replan, and during compensation. Verify that the system resumes without duplicate external effects, preserves the original goal and policy version, and reaches a terminal state. Establish run TTLs, dead-letter handling, operator dashboards, and a manual termination path.

> ⚠️ Limited public data available for this dimension. Papers on reasoning and planning rarely publish recovery-point objectives, recovery-time objectives, duplicate-effect rates, checkpoint storage growth, or multi-region consistency behavior. These properties belong to the chosen workflow runtime and application protocol and require failure injection.

## 4. Enterprise Security & Governance

### A plan is not permission

An LLM-generated step is untrusted output. Authorization belongs to a deterministic enforcement point using the authenticated principal, tenant, tool, arguments, resource, risk class, and current policy. OWASP describes excessive agency as excessive functionality, permissions, or autonomy and recommends minimizing tools and privileges and independently approving high-impact actions. [[33]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

`[inferred]` Enforce immutable constraints outside the planner:

```text
proposal -> schema validation -> policy decision -> optional approval
         -> argument binding to authorized resources -> execution sandbox
         -> authoritative result verification -> audit event
```

The planner cannot grant itself a new tool, increase a budget, change tenant scope, waive an approval, or rewrite policy. Replanning must pass the same checks as the original plan.

### Goal and plan hijacking

Indirect prompt injection places adversarial instructions in retrieved web pages, documents, or tool results, exploiting the ambiguity between data and instructions. The primary attack paper demonstrated manipulation of application behavior and API use. [[34]](https://arxiv.org/abs/2302.12173) Planning magnifies this risk because an injected instruction can alter many later steps.

The OWASP Agentic Top 10 explicitly includes agent goal hijacking, tool misuse, identity and privilege abuse, supply-chain risks, unexpected code execution, memory/context poisoning, cascading failures, human-agent trust exploitation, and rogue behavior. [[35]](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)

`[inferred]` Defenses include:

- label every observation by origin and trust; quote untrusted content as data;
- keep authenticated goal and constraints in server-owned fields;
- diff each replan against the prior goal, privileges, destinations, and risk;
- require fresh authorization for changed arguments or resources;
- isolate web/browser, code, and data tools by capability and network policy;
- prevent reflection text and retrieved memories from becoming policy;
- scan generated code and run it in an ephemeral sandbox with bounded egress;
- alert on goal reversal, unusual plan depth, repeated verification bypass, and tool escalation.

### Verification governance

A verifier can be attacked, incomplete, biased, or correlated with the generator. Model judges may prefer fluent rationales and miss domain-specific harm. Tests may be weak or deliberately overfit. A solver may prove the wrong formalization.

`[inferred]` Maintain a verifier registry with owner, version, checked properties, known blind spots, evidence sources, calibration data, and false-accept/false-reject thresholds. High-impact decisions should combine independent mechanisms: deterministic policy, authoritative business rules, execution checks, and accountable human review. A verifier should not automatically possess the execution permissions of the agent it evaluates.

### Data and audit controls

The public plan, step decisions, evidence references, approvals, policy versions, tool arguments, and effects are auditable. Unrestricted hidden reasoning is neither necessary nor a dependable explanation. Store concise decision records and structured critique, redact secrets, and apply purpose-based retention.

NIST AI RMF 1.0 organizes risk work into Govern, Map, Measure, and Manage and calls for risk management across the lifecycle. [[36]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) `[inferred]` Map each production use case to an accountable owner, affected users, risk tolerance, allowed autonomy, evidence requirements, incident path, and retirement criteria. Model or prompt changes should trigger regression evaluation for task success, security, and stochastic reliability.

### Human oversight

Human review is a control only if the reviewer has time, authority, context, and meaningful choices. `[inferred]` An approval screen should show the exact action, target, expected effect, source evidence, uncertainty, alternatives, compensation, and what changed since the prior plan. Expire approvals when arguments, external state, policy, or plan generation changes. Never ask a human to approve an opaque bundle of many unrelated irreversible operations.

> ⚠️ Limited public data available for this dimension. Public planning papers rarely report enterprise access-control tests, prompt-injection resistance, reviewer error, audit completeness, data residency, or regulatory outcomes. Security claims require a threat model, red-team corpus, permission tests, and organization-specific legal review.

## 5. Production Failure Modes

### Decomposition failures

| Failure | Symptom | Detection / mitigation |
|---|---|---|
| Omitted requirement | plausible plan cannot satisfy full request | requirement-to-step coverage matrix and final oracle |
| Circular or false dependency | deadlock or wrong ordering | graph cycle, dataflow, and precondition checks |
| Oversplitting | token/latency explosion, coordination overhead | minimum useful leaf size; collapse deterministic work |
| Undersplitting | one opaque step has many effects | split at verification, approval, or recovery boundaries |
| Invented capability | leaf names a nonexistent tool or permission | tool registry and executable schema validation |
| Premature commitment | early interpretation locks out alternatives | clarify ambiguity; branch only where evaluation is affordable |

PlanBench's controlled experiments found substantial deficits in plan generation, optimal planning, and plan verification for the evaluated models. [[22]](https://arxiv.org/abs/2206.10498) Treat fluent plans as candidates until their dependencies and semantics are checked.

### Reflection failures

- **Rationalization**: critique explains the failure without locating a repairable cause.
- **Self-correction degradation**: a correct initial result is changed to an incorrect one.
- **Correlated critic**: generator and critic share the same blind spot.
- **Criteria drift**: each iteration changes the definition of success.
- **Looping**: revisions alternate or repeat without measurable improvement.
- **Poisoned feedback**: malicious tool content is stored as a lesson.

`[inferred]` Preserve the best independently scored candidate, require evidence-linked critiques, cap iterations, fingerprint repeated feedback, and stop or escalate when the oracle does not improve. Do not reward longer explanations as a proxy for correctness.

### Verification failures

- **Weak oracle**: tests cover syntax but not business invariants.
- **Wrong-world proof**: the formal solver proves an inaccurately translated problem.
- **Stale evidence**: a precondition passed before concurrent state changed.
- **Test gaming**: a candidate overfits visible tests or reward-model preferences.
- **False rejection**: a valid unconventional plan differs from a reference trajectory.
- **Unsafe verifier**: verification executes untrusted code or queries production broadly.
- **Majority error**: sampled candidates agree on the same misconception.

`[inferred]` Separate validation of the problem formalization from validation of the solution. Bind checks to current state versions, keep hidden/adversarial tests where appropriate, sandbox executable verification, and measure false accepts because they become production escapes.

### Replanning failures

- **Thrashing**: small observations trigger repeated full replans.
- **Stale repair**: a new plan is based on an old snapshot.
- **Goal drift**: local failure recovery silently changes the user's outcome.
- **Duplicate effects**: completed steps run again after repair or replay.
- **Orphan work**: old-generation workers continue after the scheduler moves on.
- **Irreversible conflict**: a replan assumes an external action can be undone.
- **Budget evasion**: each new plan resets counters.
- **Permanent failure retried as transient**: cost grows without new evidence.

`[inferred]` Use hysteresis and materiality thresholds, repair the affected suffix, retain global budgets across generations, fence stale workers, reconcile in-flight operations, and expose an explicit `blocked` terminal state.

### System and evaluation failures

- checkpoint contains prompt text but not authoritative external effects;
- approval resumes against changed tool arguments;
- plan schema or tool version changes during a long run;
- evaluator score rises while customer outcome, cost, or latency worsens;
- benchmark contamination or judge-model bias creates false confidence;
- traces omit failed branches and make the trajectory look cleaner than it was;
- PII, credentials, or private reasoning leak into plan logs.

`[inferred]` Version plans, policies, tools, prompts, models, and verifiers; record all attempts and terminal reasons; canary releases; and maintain rollback for orchestration changes. Evaluate both outcome and process, but keep the authoritative end state primary.

> ⚠️ Limited public data available for this dimension. There is no normalized public incident rate for decomposition omissions, reflection regressions, verifier escapes, replan loops, or duplicate side effects in deployed agent systems. Organizations should build a failure taxonomy into telemetry from the first release.

## 6. Enterprise System Design Scenarios

### Scenario A: Regulated claims or lending workflow

**Goal:** collect a case, evaluate eligibility, request missing evidence, and prepare a recommendation.

`[inferred]` Use a deterministic workflow for required documents, identity, deadlines, calculation, and policy. Let the model decompose document review and explain missing evidence, but do not let it alter eligibility rules. Verification reads authoritative records and runs versioned rules. Replanning occurs when authenticated evidence arrives or a rule rejects an assumption. A human owns adverse or otherwise regulated decisions; the plan stores evidence references and rule versions, not hidden reasoning.

**Key metrics:** complete-evidence rate, rule disagreement, false approval/denial, rework, human review time, plan changes after new evidence, and policy-version traceability.

### Scenario B: Coding and incident remediation agent

**Goal:** diagnose a production regression, propose a patch, test it, and stage deployment.

`[inferred]` Decompose by evidence acquisition, suspected components, patch, tests, and rollout. Run read-only diagnostics in parallel. Reflection consumes test failures and telemetry, not merely the previous explanation. Verification uses type checks, unit/integration/regression tests, security scans, diff policy, and a canary health oracle. A failed test repairs the code subgraph; a changed incident symptom triggers wider replanning. Production writes require approval and idempotent deployment operations with rollback.

**Key metrics:** verified resolution, escaped regression, tests added, time to diagnosis, replan count, repeated tool calls, canary rollback, and cost per resolved incident.

### Scenario C: Research and data-analysis agent

**Goal:** answer a market or scientific question with reproducible evidence.

`[inferred]` Build a question DAG: definitions, source collection, extraction, calculations, contradiction search, and synthesis. Parallelize independent source work but require provenance and content hashes. Reflect by identifying unsupported claims and conflicting evidence. Verify numeric claims with code, citations against source text, and dataset schema/range checks. Replan when a source is inaccessible, a premise is contradicted, or analysis reveals a confounder. Keep untrusted documents from redefining the goal or tool permissions.

**Key metrics:** claim entailment, citation correctness, source diversity/authority, reproducibility, calculation error, unsupported-claim rate, and evidence freshness.

### Scenario D: Travel, logistics, or field operations

**Goal:** create and execute a constraint-satisfying itinerary or dispatch plan under changing availability.

`[inferred]` Represent hard constraints separately from preferences. Use a constraint solver for feasibility and optimization; use the model for interpreting requests, proposing alternatives, and explaining tradeoffs. Verify inventory and price immediately before a commitment. Replan only the affected suffix after a cancellation, preserving valid bookings and accounting for fees. Treat purchase as a pivot requiring exact user approval, idempotency, and recorded provider confirmation.

**Key metrics:** hard-constraint satisfaction, preference score, total cost, optimality gap where computable, stale-availability failures, repair time, retained bookings, duplicate purchase rate, and manual recovery.

### Architecture choice matrix

| Need | Start with | Add only when evidence justifies it |
|---|---|---|
| simple reversible answer | one generation + output validation | reflection if an external score improves |
| known business process | deterministic workflow with bounded model nodes | dynamic decomposition for genuinely variable subtasks |
| compositional analysis | sequential least-to-most or DAG | search/tree when multiple plausible branches matter |
| hard scheduling/constraint problem | model-to-formal representation + solver | model repair loop for translation failures |
| interactive environment | observe-act-verify loop | hierarchical replanning for long horizons |
| high-impact action | policy, deterministic verifier, human approval | no autonomy increase without measured safety and value |

### Interview-ready design checklist

1. What is the authoritative goal, and which constraints cannot be changed by the planner?
2. What representation makes the plan executable, versioned, and independently verifiable?
3. Which work benefits from decomposition, and which should remain deterministic?
4. What external evidence makes reflection more than self-critique?
5. What oracle verifies the request, plan, step effects, and final state?
6. Which triggers justify retry, local repair, full replan, escalation, or termination?
7. How are tokens, tools, time, depth, retries, and plan generations bounded?
8. How are checkpoints, idempotency, stale workers, compensation, and irreversible actions handled?
9. Where are authorization and approvals enforced outside the model?
10. How will repeated-run reliability, cost per verified success, and failure recovery be evaluated?

### Recommended study progression

`[inferred]`

1. Implement a typed plan with dependencies and deterministic validation.
2. Add an observe-act-verify loop with explicit terminal and budget states.
3. Add external-feedback reflection and show it improves an independent metric.
4. Add suffix repair after injected environment changes.
5. Persist state and prove restart/idempotency behavior with failure injection.
6. Add formal solvers or tree search only for tasks where their extra cost improves verified success.
7. Add security tests for goal hijacking, privilege escalation, poisoned feedback, and approval replay.

The central interview answer is not that one prompting method "reasons best." It is that robust systems expose a minimal plan, ground revision in observations, verify with the strongest available oracle, and keep permissions, state, budgets, and stopping conditions outside probabilistic model output.

## Sources

1. Wei et al., [Chain-of-Thought Prompting Elicits Reasoning in Large Language Models](https://arxiv.org/abs/2201.11903).
2. Zhou et al., [Least-to-Most Prompting Enables Complex Reasoning in Large Language Models](https://arxiv.org/abs/2205.10625).
3. Wang et al., [Plan-and-Solve Prompting](https://arxiv.org/abs/2305.04091).
4. Anthropic, [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents).
5. Yao et al., [Tree of Thoughts: Deliberate Problem Solving with Large Language Models](https://arxiv.org/abs/2305.10601).
6. Liu et al., [LLM+P: Empowering Large Language Models with Optimal Planning Proficiency](https://arxiv.org/abs/2304.11477).
7. Kambhampati et al., [LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks](https://arxiv.org/abs/2402.01817).
8. Madaan et al., [Self-Refine: Iterative Refinement with Self-Feedback](https://arxiv.org/abs/2303.17651).
9. Shinn et al., [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366).
10. Huang et al., [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798).
11. Gou et al., [CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing](https://arxiv.org/abs/2305.11738).
12. Cobbe et al., [Training Verifiers to Solve Math Word Problems](https://arxiv.org/abs/2110.14168).
13. Wang et al., [Self-Consistency Improves Chain of Thought Reasoning in Language Models](https://arxiv.org/abs/2203.11171).
14. Lightman et al., [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050).
15. Gao et al., [PAL: Program-Aided Language Models](https://arxiv.org/abs/2211.10435).
16. Dhuliawala et al., [Chain-of-Verification Reduces Hallucination in Large Language Models](https://arxiv.org/abs/2309.11495).
17. Gundawar et al., [Robust Planning with Compound LLM Architectures: An LLM-Modulo Approach](https://arxiv.org/abs/2411.14484).
18. Yao et al., [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629).
19. Huang et al., [Inner Monologue: Embodied Reasoning through Planning with Language Models](https://arxiv.org/abs/2207.05608).
20. Wang et al., [Describe, Explain, Plan and Select: Interactive Planning with Large Language Models](https://arxiv.org/abs/2302.01560).
21. Gong et al., [RePlan-Bot: Multi-Level Replanning for Embodied Instruction Following](https://arxiv.org/abs/2605.25851).
22. Valmeekam et al., [PlanBench: An Extensible Benchmark for Evaluating Large Language Models on Planning and Reasoning about Change](https://arxiv.org/abs/2206.10498).
23. Tang et al., [PlanningBench: A Configurable and Expandable Benchmark for Evaluating Planning and Reasoning in LLMs](https://arxiv.org/abs/2605.20873).
24. Xie et al., [TravelPlanner: A Benchmark for Real-World Planning with Language Agents](https://arxiv.org/abs/2402.01622).
25. Yao et al., [Tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/abs/2406.12045).
26. Jimenez et al., [SWE-bench: Can Language Models Resolve Real-World GitHub Issues?](https://arxiv.org/abs/2310.06770).
27. Shi et al., [Can Language Models Solve Olympiad Programming?](https://arxiv.org/abs/2404.10952).
28. Mialon et al., [GAIA: A Benchmark for General AI Assistants](https://arxiv.org/abs/2311.12983).
29. Anthropic, [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
30. LangChain, [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
31. LangChain, [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
32. Microsoft Azure Architecture Center, [Saga Design Pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga).
33. OWASP GenAI Security Project, [LLM06:2025 Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/).
34. Greshake et al., [Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection](https://arxiv.org/abs/2302.12173).
35. OWASP GenAI Security Project, [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/).
36. NIST, [Artificial Intelligence Risk Management Framework (AI RMF 1.0)](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10).
37. Ahn et al., [Do As I Can, Not As I Say: Grounding Language in Robotic Affordances](https://arxiv.org/abs/2204.01691).
38. Google DeepMind, [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://arxiv.org/abs/2305.16291).
