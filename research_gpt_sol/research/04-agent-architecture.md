# Research: Agent Architecture - ReAct, Loops, Planning, State, Workflows

**Date researched**: 2026-08-21
**Sources consulted**: 30

## Scope and evidence labels

This brief covers all five roadmap subtopics: ReAct, agent loops, planning, state, and workflows. Plain factual statements are backed by current official documentation or primary papers. `[inferred]` marks an architecture recommendation or a derived model rather than a provider guarantee. Prices, SDK behavior, and platform limits are point-in-time facts as of the research date. Older benchmark results are retained to teach evaluation design, not to rank current models.

## 1. System Topology & Mechanics

### Architecture is control policy plus runtime

Anthropic distinguishes **workflows**, where code selects predefined paths, from **agents**, where an LLM dynamically chooses its process and tool use. Its production guidance recommends starting with the simplest adequate design because agentic flexibility normally trades additional latency and cost for task performance. OpenAI similarly describes a run as a loop that continues until an exit condition such as final output, an error, or a maximum turn count. [[2]](https://www.anthropic.com/engineering/building-effective-agents) [[3]](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

`[inferred]` Treat an agent architecture as two coupled systems:

1. A **probabilistic control policy** proposes plans, actions, replans, and completion.
2. A **deterministic runtime** validates transitions, enforces permissions and budgets, executes tools, persists state, recovers work, and decides whether claimed completion is admissible.

```text
CONTROL PLANE
prompt/model/tool/workflow registry | policy | budgets | eval gates | rollout config
                                      |
DATA PLANE                            v
request -> admission -> run coordinator -> planner? -> bounded executor loop
                            |                         /      |       \
                            |                    model   tool gateway  verifier
                            |                         \      |       /
                            +<-- checkpoint/event ledger <-- observation
                                           |
                             pause / approve / retry / compensate / finish
```

The model may decide *what it wants to do*; only the coordinator can commit a state transition. Tool execution, authorization, approvals, budget accounting, and success verification therefore remain outside the prompt.

### ReAct: interleave decisions with environmental evidence

ReAct introduced an interleaved pattern in which language-model reasoning updates a plan, an action changes or queries the environment, and an observation grounds the next decision. The paper reported that this combination improved performance and interpretability over reasoning-only or acting-only baselines on its tested language and interactive tasks. [[1]](https://arxiv.org/abs/2210.03629)

```text
Goal -> decide next action -> execute -> observe -> update working state
          ^                                      |
          +------ replan / continue / finish ----+
```

`[inferred]` A production ReAct record should expose an inspectable **decision summary**, requested action, validated arguments, observation provenance, state delta, and completion evidence. It need not depend on storing private or raw chain-of-thought. The auditable contract is “why this action is relevant and what evidence changed,” not an unrestricted reasoning transcript.

ReAct is appropriate when later steps genuinely depend on fresh environmental results. It is wasteful when the path is already known: code should route a fixed approval, payment, or document pipeline rather than asking the model to rediscover it every turn. Anthropic's workflow guidance explicitly positions prompt chaining for fixed decompositions and agents for open-ended paths whose step count cannot be hard-coded. [[2]](https://www.anthropic.com/engineering/building-effective-agents)

### The bounded agent loop

Client-executed tool loops generally follow the same protocol: call the model, execute requested tools, append results, and call the model again until it stops requesting tools. Anthropic documents this as a loop over `stop_reason == "tool_use"`; OpenAI's Agents SDK runner similarly repeats for tool calls or handoffs and raises `MaxTurnsExceeded` when a configured limit is crossed. OpenAI permits `max_turns=None`, but that removes this particular bound. [[4]](https://openai.github.io/openai-agents-python/running_agents/) [[5]](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)

Provider termination values are transport signals, not business success. For example, Anthropic distinguishes natural end, tool use, token/context truncation, refusal, stop sequence, and `pause_turn`; the last means a server-tool loop reached its iteration limit and can be continued, while truncation means output is incomplete. [[6]](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)

`[inferred]` A safe run has independent, monotonic budgets and typed terminal states:

| Control | Example terminal state | Why it is separate |
|---|---|---|
| Postcondition validator passes | `SUCCEEDED` | Proves the requested outcome, not merely fluent final text |
| Model stops without a valid proof | `INCOMPLETE` | Natural language completion is only a claim |
| Turn/tool/token/cost budget exhausted | `BUDGET_EXHAUSTED` | Prevents runaway spend and loops |
| Wall-clock deadline or cancellation | `TIMED_OUT` / `CANCELLED` | Bounds user and queue latency |
| Repeated state/action hash or no progress | `STALLED` | Detects oscillation before the hard turn cap |
| Policy or approval denial | `BLOCKED_POLICY` | Must not be retried as a transient failure |
| Dependency failure after retry budget | `FAILED_DEPENDENCY` | Supports explicit degradation or recovery |
| Human information required | `WAITING_INPUT` | Durable pause, not worker occupancy |

The loop should count model turns, tool attempts, parallel fan-out, tokens, estimated currency cost, elapsed time, and consecutive no-progress transitions. A single `max_iterations` counter cannot express all resource or risk limits.

### Planning patterns

Planning is useful only when it changes execution quality enough to pay for its calls and latency. Plan-and-Solve separates plan generation from subtask execution and was designed to reduce missing-step errors found in zero-shot chain-of-thought on its ten evaluated datasets. Tree of Thoughts searches and self-evaluates multiple candidate reasoning paths; on the paper's Game of 24 setup, GPT-4 with chain-of-thought solved 4% while its Tree-of-Thought configuration solved 74%. These are task-specific research results, not general production uplift estimates. [[7]](https://aclanthology.org/2023.acl-long.147/) [[8]](https://proceedings.neurips.cc/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract.html)

| Pattern | Mechanics | Best fit | Principal risk |
|---|---|---|---|
| Inline ReAct | choose one next action from current evidence | short, uncertain paths | myopia and oscillation |
| Plan-then-execute | create ordered steps, then execute | decomposable work with stable dependencies | stale plan after environment change |
| Receding-horizon plan | plan a few steps, execute one, replan on material delta | volatile tools or partial information | repeated planning cost |
| DAG planner | explicit dependencies; run ready nodes in parallel | independent research/build subtasks | merge conflict and fan-out explosion |
| Search / Tree of Thoughts | generate, score, prune, backtrack | high-value tasks with objective scoring | call count grows with breadth and depth |
| Evaluator-optimizer | generate, critique against criteria, revise | objectively judgeable artifacts | evaluator bias or endless polishing |
| Reflection memory | turn feedback into a lesson for another attempt | repeated tasks with reliable feedback | confident but false self-diagnosis |

Reflexion stores verbal feedback in episodic memory rather than changing model weights and reported 91% pass@1 on its HumanEval configuration versus 80% for the cited GPT-4 baseline. That result demonstrates the possible value of feedback loops, but reflection should not be treated as independent verification because it can reuse the same mistaken assumptions. [[9]](https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)

`[inferred]` Plans should be typed, versioned hypotheses rather than prose promises. A plan step needs `step_id`, dependencies, expected preconditions, allowed tool class, success predicate, rollback/compensation metadata, risk level, and status. Replanning creates `plan_version + 1`, records why the old plan became invalid, and carries forward only completed steps whose postconditions still hold.

### State: authoritative facts versus model context

LangGraph separates thread-scoped checkpoints from a cross-thread store. Its checkpointer persists graph state for continuity, human review, time travel, and fault recovery; its store holds application-defined data such as preferences or facts across threads. OpenAI Agents SDK sessions manage conversation history, but sessions cannot be combined in the same run with its run-level continuation options. These products illustrate that conversation history, workflow state, and long-term memory are distinct concerns. [[10]](https://docs.langchain.com/oss/python/langgraph/persistence) [[13]](https://openai.github.io/openai-agents-python/sessions/)

`[inferred]` Split state by consistency and retention needs:

| State plane | Examples | Authority and retention |
|---|---|---|
| Run identity | tenant, actor, goal, policy/version IDs | immutable, durable |
| Workflow snapshot | node, plan version, budgets, pending work | strongly consistent per run |
| Effect ledger | proposed call, idempotency key, attempt, result digest | append-only audit/recovery record |
| Observation/artifact store | documents, screenshots, tool outputs | immutable blobs with provenance and TTL |
| Context projection | selected messages, summaries, current plan | disposable derivation; never system of record |
| Long-term memory | approved facts/preferences/lessons | separately governed, scoped, and correctable |
| Completion evidence | postconditions, test results, approvals | immutable evidence linked to final state |

The LLM receives a size-bounded projection of authoritative state. It must not be allowed to rewrite the ledger, approval record, tenant identity, or already committed business facts through a generated summary.

### Workflows and graphs

Anthropic documents five composable workflow patterns: prompt chaining with gates, routing, parallelization by sectioning or voting, orchestrator-workers for dynamically discovered subtasks, and evaluator-optimizer loops. It recommends the last only when evaluation criteria are clear and iterative feedback measurably improves the output. [[2]](https://www.anthropic.com/engineering/building-effective-agents)

LangGraph's graph API makes the corresponding runtime concepts explicit: nodes perform work, edges define routing, state schemas define shared data, and reducers determine how updates are combined. This is useful for model-directed graphs because a reducer supplies deterministic merge semantics where parallel model branches would otherwise overwrite shared state. [[12]](https://docs.langchain.com/oss/python/langgraph/use-graph-api)

`[inferred]` Use a deterministic outer graph and put model choice only in nodes where uncertainty is real:

```python
def advance(run_id: str) -> None:
    state = store.load_for_update(run_id)
    policy.check_run(state)

    if verifier.postconditions_hold(state):
        store.transition(state, "SUCCEEDED", evidence=verifier.evidence(state))
        return
    if budgets.exhausted(state):
        store.transition(state, "BUDGET_EXHAUSTED")
        return
    if state.pending_approval:
        store.transition(state, "WAITING_INPUT")
        return

    decision = model.propose(context.project(state))
    command = policy.validate_and_bind(decision, state)  # actor, resource, plan version

    if command.requires_approval:
        store.create_approval(state, command.digest())
        return

    # Intent and idempotency key become durable before the external effect.
    attempt = store.record_intent(state, command)
    result = tools.execute(command, idempotency_key=attempt.key)
    store.record_result_and_transition(
        state,
        attempt,
        sanitized=result.for_model(),
        authoritative=result.receipt,
    )
```

This pattern is illustrative. The storage transaction and tool protocol determine whether `record_intent`, execution, and result recovery are actually safe.

## 2. Token Economics & NFR Metrics

### Cost and latency grow by trajectory, not request

For a run with model calls `i = 1..n`, a practical cost model is:

```text
model_cost = Σ[(uncached_input_i × input_rate)
             + (cached_input_i × cached_rate)
             + (output_i × output_rate)]
run_cost   = model_cost + tool fees + sandbox/runtime + storage + observability
```

On the researched date, GPT-5.4 standard text pricing is $2.50 per million input tokens, $0.25 per million cached input tokens, and $15 per million output tokens. A workload totaling 12M uncached input, 20M cached input, and 3M output tokens across 1,000 runs would therefore cost `(12 × 2.50) + (20 × 0.25) + (3 × 15) = $80`, before tool and infrastructure charges. This is a worked illustration, not a forecast. [[25]](https://developers.openai.com/api/docs/models/gpt-5.4)

`[inferred]` Report **cost per successful run**, not only mean cost per invocation:

```text
cost_per_success = total_cost / successful_runs
wasted_cost_rate = cost_of_failed_cancelled_duplicate_work / total_cost
```

A cheaper model that needs more turns, retries, or human repair can have a higher cost per success. Conversely, a planner call is justified only if it reduces downstream failure or execution cost enough to offset itself.

### Critical-path latency

```text
serial_run_latency ≈ Σ(model_i + queue_i + tool_i + persistence_i)
parallel_stage      ≈ dispatch + max(branch_latency_j) + join
end_to_end          = admission + Σ(critical_path_stages) + human_wait
```

Parallel work reduces critical-path time only for independent branches; it increases call volume, contention, merge work, and tail exposure. A simplified breadth-`b`, depth-`d` search can examine on the order of `b^d` candidates without pruning. `[inferred]` Put explicit limits on frontier width, retained candidates, depth, evaluator calls, and total tokens rather than relying on a natural stop.

> ⚠️ Limited public data available for this dimension. Hosted providers do not publish stable, comparable end-to-end p50/p95/p99 figures segmented by architecture (single call, ReAct, planner-executor, graph), model, region, queueing, tool latency, checkpoint backend, context length, and customer tier. Benchmark the complete production-shaped trajectory; model-only latency is not the run SLA.

### Reliability compounds over steps

In a deliberately simplified independent-step model, if every one of `n` required steps succeeds with probability `p`, full-path success is `p^n`; at `p = 0.98`, twenty required steps yield about `0.98^20 = 66.8%`. `[inferred]` Real steps are neither independent nor identically distributed, but the calculation explains why long trajectories need postconditions, retries for truly transient failures, and fewer unnecessary steps.

The original tau-bench compared final database state with an annotated goal and introduced `pass^k`, the probability-like requirement that repeated trials all succeed. Its evaluated state-of-the-art function-calling agents achieved under 50% task success, and retail `pass^8` was under 25%. AgentBench evaluated 29 models over eight environments and identified long-term reasoning, decision-making, and instruction following as major failure sources. These historical results support multi-run, state-based evaluation rather than current vendor ranking. [[17]](https://arxiv.org/abs/2406.12045) [[18]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/e9df36b21ff4ee211a8b71ee8b7e9f57-Abstract-Conference.html)

### NFR scorecard

`[inferred]` Segment every measure by intent, risk class, model/prompt/tool/workflow version, tenant tier, and trajectory length:

| Dimension | Production measures |
|---|---|
| Outcome | executable task success, postcondition pass, human acceptance, false-success rate |
| Reliability | `pass^k`, recovery success, duplicate-effect rate, compensation success, checkpoint resume success |
| Control quality | invalid transitions, policy denials, approval bypasses, unauthorized attempts, unsafe action rate |
| Trajectory | turns, tool calls, replans, branch fan-out, repeated-action ratio, no-progress terminations |
| Performance | time to first useful event, p50/p95/p99 run latency, tool and model spans, queue wait, human wait excluded/included |
| Economics | tokens and currency per run/success, cached-token share, failed-work cost, planner/evaluator overhead |
| State | checkpoint bytes, context projection tokens, artifact growth, stale-read conflicts, state-reducer conflicts |
| Operations | queue age, saturation, retry volume, circuit-open time, stuck-run age, cancellation latency |

GAIA originally contained 466 questions requiring reasoning, browsing, multimodality, and tool use; its paper reported 92% for human respondents versus 15% for GPT-4 with plugins. Use such benchmarks for broad capability signals, then require private, versioned tasks with business-state assertions and adversarial variants. [[19]](https://arxiv.org/abs/2311.12983)

### Capacity model

```text
model_calls_per_second ≈ admitted_runs_per_second × mean_model_turns
tool_calls_per_second  ≈ admitted_runs_per_second × mean_tool_calls
active_runs            ≈ admitted_runs_per_second × mean_run_duration_seconds
```

`[inferred]` Size queues and quotas by weighted work units, not request count: a run with 30 turns and 10 parallel branches is not equivalent to a two-turn lookup. Enforce tenant concurrency, global model-call rate, branch fan-out, tool-specific quotas, and maximum queue age. Shed or downgrade low-priority planning/evaluation work before transactional tool workers.

## 3. Distributed Resilience & State

### Durable execution is replay, not magic exactly-once effects

LangGraph checkpoints graph state at step boundaries and persists completed task writes within a super-step so successful parallel nodes need not rerun when another node fails. Its replay documentation also states that nodes after a selected checkpoint re-execute, including model calls, API requests, and interrupts. [[10]](https://docs.langchain.com/oss/python/langgraph/persistence)

LangGraph interrupts persist state and wait for external input, but a resumed interrupt restarts its node from the beginning. Its documentation therefore requires side effects before an interrupt to be idempotent. [[11]](https://docs.langchain.com/oss/python/langgraph/interrupts)

AWS Step Functions documents different orchestration semantics: Standard workflows persist state and use an exactly-once workflow-execution model unless retries are configured, asynchronous Express is at-least-once, and synchronous Express is at-most-once. Standard runs can last up to one year; Express runs up to five minutes. These are workflow-service semantics and do not by themselves make every external API effect exactly once. [[14]](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html)

`[inferred]` For each mutation:

1. Persist canonical command, actor, authorization decision, plan version, deadline, and idempotency key.
2. Dispatch through an outbox or durable task queue.
3. Make the destination deduplicate by that key where possible.
4. Persist the authoritative receipt and observation digest.
5. On crash ambiguity, reconcile by querying the destination before reissuing.

HTTP defines PUT, DELETE, and safe methods as idempotent by intended semantics, while warning clients not to retry a non-idempotent request automatically unless they know it is safe or unapplied. Business idempotency must still be designed around the actual downstream effect. [[29]](https://datatracker.ietf.org/doc/html/rfc9110)

### State consistency and concurrent branches

`[inferred]` Give every run an optimistic `state_version`; a worker commits only from the version it read. Parallel branches write namespaced results, not a shared prose scratchpad. A deterministic reducer joins branch outputs and rejects conflicting mutations. Use a lease plus fencing token for exclusive executors so a paused or partitioned worker cannot resume later and overwrite a newer owner.

Recommended invariants:

- A terminal run never returns to active state except through an explicit child/retry run.
- A plan step has one authoritative status transition history.
- An approval binds the exact command digest, actor, resource, amount, and expiry.
- A tool result is immutable; corrections append a new event.
- State projections are reproducible from versioned source events and artifact digests.
- Only the coordinator, never the model text, advances the workflow state machine.

### Retries, circuits, and compensation

Microsoft's retry guidance distinguishes cancel, immediate retry, and delayed retry; it warns that a request can succeed remotely while its response is lost, causing an unsafe duplicate on retry. It recommends finite retry policy, idempotency analysis, and circuit breaking for persistent faults. Its current transient-fault guidance also recommends an aggregate retry budget because per-request retries can collectively overload a dependency. [[15]](https://learn.microsoft.com/en-us/azure/architecture/patterns/retry)

`[inferred]` Retry transport timeouts, throttling, and selected 5xx failures with exponential backoff, jitter, server `Retry-After`, a deadline, and one owning retry layer. Do not retry schema errors, authorization denials, policy blocks, invalid plans, or insufficient funds. A model “trying again differently” is replanning and consumes an agent budget; it is not a network retry.

Compensating transactions record how to semantically undo completed work in an eventually consistent workflow. Microsoft notes that compensation is domain-specific, can itself fail, should be resumable and idempotent, and may require human intervention; irreversible steps should occur only after critical validation. [[16]](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)

`[inferred]` Every effectful plan step should be classified as:

- **read-only**: repeatable subject to freshness;
- **idempotent mutation**: safe under a stable key;
- **compensable mutation**: has a tested semantic inverse;
- **irreversible/high impact**: requires preconditions, fresh authorization, and point-of-action approval.

### Recovery and degradation

`[inferred]` Recovery should preserve evidence rather than asking the model to infer what probably happened:

```text
worker crash -> acquire fenced lease -> load last checkpoint
             -> reconcile any DISPATCHED-without-result effect
             -> reuse committed observations
             -> continue from next admissible transition
```

A controlled degradation order is: disable branching and evaluator passes; fall back to a smaller bounded workflow for recognized intents; change mutations to draft-only; allow read-only retrieval; enqueue for later; then fail closed with a resumable run ID. Never silently replace a policy-bound model/tool/workflow version during an in-flight high-risk run without recording a version transition.

## 4. Enterprise Security & Governance

### Treat model, harness, tools, and environment as separate trust surfaces

Anthropic's 2026 trust framework describes an agent as model, harness, tools, and environment, noting that a capable model can still be exploited by a weak harness, over-permissive tool, or exposed environment. It defines the loop as plan, act, observe, adjust, and repeat until completion or human input. [[28]](https://www.anthropic.com/research/trustworthy-agents)

`[inferred]` Establish these boundaries:

- User instructions are untrusted intent.
- Retrieved pages, documents, emails, tool errors, and peer-agent messages are untrusted observations, not policy.
- Model plans and tool calls are proposals.
- The policy gateway and downstream service enforce authorization on the actual resource.
- Workflow state, approval receipts, and effect ledgers are authoritative and not model-editable.

OWASP classifies excessive agency as excessive functionality, permissions, or autonomy and recommends minimum downstream privileges, authorization in downstream systems, user approval for high-impact actions, complete mediation, logging, monitoring, and rate limiting. [[21]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

### Prompt injection and state poisoning

AgentDojo demonstrates the core architecture risk: external tool data can contain instructions that hijack subsequent actions. Its benchmark contains 97 realistic tasks and 629 security test cases across attacks and defenses. [[20]](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)

`[inferred]` Apply defense in depth:

1. Label observation provenance and keep external content in data-delimited fields.
2. Filter the available tool set to what the current workflow node requires.
3. Prevent observations from changing system policy, identity, credentials, budgets, or approval rules.
4. Apply deterministic argument and destination policies after model generation.
5. Require an exact-command approval for sensitive effects and revalidate it immediately before execution.
6. Red-team poisoned documents, webpages, tool descriptions, memory entries, summaries, and branch outputs.
7. Measure both attack success and benign task utility; a defense that blocks all work is not production-ready.

### Guardrail placement matters

OpenAI Agents SDK documents that input guardrails run only on the first agent, output guardrails only on the final-output agent, and tool guardrails on each custom function-tool call. It also warns that parallel input guardrails can finish after the agent has already consumed tokens or executed tools; blocking mode prevents execution until the check passes. [[24]](https://openai.github.io/openai-agents-python/guardrails/)

`[inferred]` Use blocking admission controls before any effectful loop, per-tool authorization/validation immediately before dispatch, observation sanitization after each result, and a final disclosure/quality gate. Do not assume a chain-level input/output guardrail mediates every internal tool or handoff.

### Identity, approval, and data governance

- Propagate tenant, end-user, purpose, and delegated scopes independently of model-provided arguments.
- Mint short-lived, audience-restricted credentials at execution time; never put bearer tokens in model context or durable plaintext traces.
- Separate read, draft, approve, execute, and administer roles. The agent that proposes a payment should not confer its own approval.
- Bind approval to canonical arguments and expire it after material state, price, resource, destination, or plan changes.
- Encrypt state and artifacts, apply tenant-scoped keys and row-level controls, and define deletion/retention schedules for checkpoints, memories, and traces.
- Version and sign prompts, policies, schemas, tools, reducers, models, and workflow graphs; store the version set on every run.

NIST AI 600-1 is a voluntary cross-sector Generative AI Profile for incorporating trustworthiness considerations throughout design, development, use, and evaluation. `[inferred]` Map each deployed workflow to an owner, intended use, impact assessment, eval evidence, incident process, rollback criteria, and periodic review rather than treating a model card as system-level assurance. [[22]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)

### Audit without creating a new data leak

OpenAI Agents SDK tracing records model generations, tool calls, handoffs, guardrails, and custom events, with trace/span parentage. Its documentation warns that generation and function spans may contain sensitive inputs and outputs; sensitive-data capture is configurable. [[23]](https://openai.github.io/openai-agents-python/tracing/)

OpenTelemetry's GenAI registry defines common attributes for workflows, agents, and agent-side tool execution. `[inferred]` Use the stable semantic fields your telemetry stack supports, but keep business authorization and effect receipts in the authoritative audit ledger rather than assuming a trace is a transaction record. [[26]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

`[inferred]` Record run/trace ID, tenant pseudonym, node, state and plan versions, model/prompt/tool versions, tool call ID, canonical argument hash, policy decision, approval ID, idempotency key, result/status digest, token/cost counters, and timing. Keep raw content in a separately authorized store with shorter retention; telemetry should default to metadata, classifications, and hashes.

Anthropic's 2026 autonomy study explicitly notes limited visibility: public API traffic could be analyzed at individual tool-call level but not reliably linked into full customer sessions, while complete Claude Code sessions represented one software-heavy product. The study argues for privacy-preserving post-deployment monitoring and warns against generalizing first-party product behavior to other domains. [[27]](https://www.anthropic.com/research/measuring-agent-autonomy)

## 5. Production Failure Modes

| Failure mode | Detection signal | Prevention / recovery |
|---|---|---|
| Runaway loop | turn/cost cap, repeated action-state hash | monotonic budgets, no-progress detector, typed terminal state |
| Premature “done” | final text but failed postcondition | external verifier; success requires evidence |
| Plan drift | action does not map to active plan version | bind command to plan/step; replan on material delta |
| Stale plan | precondition or observation version changed | receding-horizon execution; freshness checks |
| Missing step | unsatisfied dependency at commit | typed DAG dependencies and gate assertions |
| Overplanning | plan/evaluator spend exceeds execution value | start with ReAct/fixed flow; measure marginal uplift |
| Search explosion | growing frontier, calls, tokens | width/depth/frontier and cost limits; deterministic pruning |
| Oscillation | A/B actions or repeated tool errors | action hash, error classifier, escalate after bounded replans |
| False reflection | critique sounds plausible but tests still fail | environment/test oracle; do not accept self-evaluation alone |
| Evaluator-generator correlation | both accept the same defect | deterministic checks, diverse evidence, sampled human review |
| Context/state divergence | summary conflicts with ledger | rebuild projection from authoritative events |
| State growth | checkpoint/context bytes trend upward | artifact references, compaction, TTL, snapshot plus event archive |
| Parallel lost update | reducer conflict or state-version rejection | namespaced branch outputs, optimistic CAS, deterministic reducer |
| Split-brain executor | two lease owners emit commands | fenced leases; reject stale fencing tokens |
| Duplicate mutation | dispatched intent lacks result after crash | idempotency ledger, destination reconciliation, compensation |
| Retry storm | retries and dependency errors rise together | one retry owner, global retry budget, jitter, circuit breaker |
| Poisoned observation | untrusted content changes goal or destination | provenance, tool filtering, policy gateway, injection evals |
| Approval replay | approved digest differs from live command | exact-command binding, expiry, reauthorization |
| Unsafe parallel guardrail | side effect occurs before tripwire | blocking preflight plus per-tool guardrails |
| Workflow version skew | old run resumes under incompatible graph | pin version; migration adapter; replay/canary tests |
| Unrecoverable partial saga | compensation fails or effect irreversible | durable compensation state, alert, human recovery playbook |
| Queue starvation | old high-cost runs monopolize workers | weighted fair queues, tenant quotas, max queue age |
| Trace data leak | secrets/PII appear in spans | content capture off, redaction, access/retention controls |

### Test the state machine, not only answers

OpenAI's Agents SDK testing guide includes deterministic provider-neutral tests for tool loops, handoffs, guardrails, retries, streaming, and session behavior. `[inferred]` At minimum, test every legal and illegal state transition, stop reason, budget boundary, crash point around an effect, duplicate delivery, stale lease, approval mutation, reducer conflict, cancellation, model truncation, dependency timeout, compensation failure, and poisoned observation. [[30]](https://openai.github.io/openai-agents-python/testing/)

Evaluate complete trajectories with state assertions: correct final business state, no forbidden side effect, valid ordering, correct actor/resource scope, bounded calls/cost/latency, and adequate evidence. Re-run successful-looking cases multiple times and report `pass^k`, because a high average task score can hide an unreliable operation.

> ⚠️ Limited public data available for this dimension. No authoritative cross-vendor production dataset was found that quantifies loop-runaway incidence, checkpoint replay duplicates, planner-versus-ReAct p99 latency, approval interception, compensation failure, or workflow-version migration defects under a common taxonomy. Public benchmarks expose capability and attack classes, but enterprise incident rates and trajectory distributions are usually private.

## 6. Enterprise System Design Scenarios

### Scenario A: customer-service resolution with transactional actions

**Need:** answer policy questions, inspect orders, offer permitted resolutions, and issue a bounded refund.

`[inferred]` Use a deterministic outer workflow:

```text
authenticate -> classify -> gather authoritative facts -> propose resolution
             -> policy calculator -> approval if threshold/risk -> execute idempotently
             -> verify order/refund state -> communicate -> close
```

Use ReAct only inside fact gathering and exception diagnosis. The policy calculator, refund limit, approval, mutation, and final state check are code. Persist order versions so a plan based on an old shipment state cannot execute. On timeout after refund dispatch, query the payment/order system by idempotency key before retrying. The original tau-bench design validates this class of task by comparing final database state, a stronger oracle than response style. [[17]](https://arxiv.org/abs/2406.12045)

### Scenario B: due-diligence research with unknown decomposition

**Need:** investigate a company across filings, litigation, product, market, and security evidence, where relevant branches emerge during research.

`[inferred]` Use a planner-executor DAG with bounded fan-out. The planner emits research questions and dependencies; branch workers collect cited evidence; a deterministic join deduplicates sources and flags contradictions; an evaluator scores coverage against a fixed rubric. Replan only when new evidence creates or invalidates a material branch. The output must link claims to immutable source artifacts and identify unresolved conflicts. This is a good agent fit because the subtask topology is not known in advance, matching the orchestrator-worker rationale in Anthropic's workflow guidance. [[2]](https://www.anthropic.com/engineering/building-effective-agents)

Budget by total searches, fetched bytes, model calls, branch width, and wall time. Degrade by pruning low-value branches and returning a partial report labeled with missing coverage, never by inventing completion.

### Scenario C: finance close with long-running approvals and compensation

**Need:** collect evidence, reconcile accounts, draft journal entries, obtain segregation-of-duties approval, post entries, and handle partial failure.

`[inferred]` Use a durable workflow, not an unconstrained autonomous loop. Agent nodes may investigate discrepancies and propose entries, but deterministic nodes validate account codes, periods, tolerances, and supporting evidence. Approval binds the exact journal batch digest. Posting uses idempotency keys; each successful external step records a receipt. Failed multi-system updates enter a resumable compensation/manual-review workflow. This follows the documented compensating-transaction requirement to retain undo information, account for concurrent work, and support human intervention for high-impact ambiguity. [[16]](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)

### Architecture trade-off matrix

| Architecture | Predictability | Adaptability | Relative call/latency shape | Recovery surface | Choose when |
|---|---|---|---|---|---|
| Single model call | high path simplicity | low | one call | retry/reissue | bounded transformation or classification |
| Deterministic workflow with LLM nodes | high | medium within nodes | known serial/parallel stages | checkpoint per stage | business process and controls are known |
| Bounded ReAct | medium | high next-action flexibility | variable serial turns | per-turn ledger | fresh observations determine the path |
| Plan-execute-replan | medium | high with global structure | planner plus execution turns | plan and step versions | dependencies matter and change occasionally |
| Tree/search planner | low-to-medium | very high exploration | potentially exponential without pruning | frontier/checkpoint complexity | rare, high-value, objectively scored search |
| Evaluator-optimizer | medium | iterative | repeated generation/evaluation | versioned candidates | quality rubric is clear and refinement is measurable |
| Durable approval workflow | highest control | bounded | includes human wait | resume/compensate/manual repair | regulated, irreversible, or long-running effects |

### Principal-architect decision rules

1. Put deterministic business rules and known control flow in code; spend model autonomy only on genuine ambiguity.
2. A provider stop is not success. Require externally checkable postconditions and evidence.
3. Bound turns, tools, fan-out, tokens, currency, wall time, retries, and no-progress separately.
4. Persist intent before effects and reconcile uncertain outcomes; never assume exactly-once external execution.
5. Keep workflow state authoritative, context disposable, observations provenance-labeled, and long-term memory separately governed.
6. Bind permissions and approvals to the live actor, resource, command digest, and plan version at the point of action.
7. Compare architectures by repeated task success, policy compliance, p95/p99 latency, and cost per success, not diagram sophistication.

## Sources

- [1] https://arxiv.org/abs/2210.03629 - ReAct reasoning/action loop and evaluated results.
- [2] https://www.anthropic.com/engineering/building-effective-agents - Workflow/agent distinction and composable architecture patterns.
- [3] https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/ - Agent runs, loops, orchestration, guardrails, and human intervention.
- [4] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI Agents SDK run loop, limits, timeouts, and exceptions.
- [5] https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works - Client/server tool loops and control flow.
- [6] https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons - Typed stop reasons, truncation, pause, and continuation behavior.
- [7] https://aclanthology.org/2023.acl-long.147/ - Plan-and-Solve prompting paper.
- [8] https://proceedings.neurips.cc/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract.html - Tree of Thoughts paper and task-specific results.
- [9] https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html - Reflexion verbal-feedback architecture and results.
- [10] https://docs.langchain.com/oss/python/langgraph/persistence - LangGraph checkpoints, stores, pending writes, and replay.
- [11] https://docs.langchain.com/oss/python/langgraph/interrupts - Durable interrupts, resumption, and idempotent-side-effect rules.
- [12] https://docs.langchain.com/oss/python/langgraph/use-graph-api - Graph state, reducers, nodes, edges, and persistence mechanics.
- [13] https://openai.github.io/openai-agents-python/sessions/ - OpenAI Agents SDK session state and continuation constraints.
- [14] https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html - Durable workflow duration and execution semantics.
- [15] https://learn.microsoft.com/en-us/azure/architecture/patterns/retry - Retry, idempotency, and circuit-breaker guidance.
- [16] https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction - Compensation, resumability, idempotency, and human recovery.
- [17] https://arxiv.org/abs/2406.12045 - tau-bench task-state and repeated-reliability evaluation.
- [18] https://proceedings.iclr.cc/paper_files/paper/2024/hash/e9df36b21ff4ee211a8b71ee8b7e9f57-Abstract-Conference.html - AgentBench environments and failure analysis.
- [19] https://arxiv.org/abs/2311.12983 - GAIA general-assistant benchmark and original results.
- [20] https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html - AgentDojo prompt-injection benchmark.
- [21] https://genai.owasp.org/llmrisk/llm062025-excessive-agency/ - OWASP excessive-agency causes and mitigations.
- [22] https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence - NIST AI 600-1 Generative AI Profile.
- [23] https://openai.github.io/openai-agents-python/tracing/ - Agent trajectory spans and sensitive-data controls.
- [24] https://openai.github.io/openai-agents-python/guardrails/ - Guardrail scope, execution timing, and tool guardrails.
- [25] https://developers.openai.com/api/docs/models/gpt-5.4 - Current GPT-5.4 token prices and context pricing condition.
- [26] https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ - GenAI workflow, agent, and tool telemetry attributes.
- [27] https://www.anthropic.com/research/measuring-agent-autonomy - 2026 deployment evidence and measurement limitations.
- [28] https://www.anthropic.com/research/trustworthy-agents - Model, harness, tools, environment, oversight, and governance.
- [29] https://datatracker.ietf.org/doc/html/rfc9110 - HTTP idempotency and retry semantics.
- [30] https://openai.github.io/openai-agents-python/testing/ - Deterministic testing patterns for agent workflows.
