# Research: Advanced — Autonomous Agents, Long-Horizon Tasks, and Agent Environments

**Date researched**: 2026-08-21  
**Sources consulted**: 50

An autonomous agent is not one that has no boundaries. It is a system permitted to select intermediate actions without asking a human at every step, inside an explicit envelope of objective, authority, resources, time, environment, and acceptable impact. **Bounded autonomy** is the production design; “run until done with every tool” is an absent design.

Long-horizon work is also not merely a longer prompt. As the horizon grows, the system must preserve intent across context turnover, observe an environment that may change independently, distinguish progress from activity, resume after infrastructure and model failure, verify irreversible effects, and terminate when success is proven or continued work no longer has value. The environment becomes part of the product contract: observation and action schemas, identity, clocks, reset/snapshot behavior, concurrency, permissions, grading, and versioned initial state determine what the agent can know and safely do.

The practical architecture therefore separates four concerns `[inferred]`:

1. **Goal/control:** what outcome, constraints, authority, budget, and stop conditions govern the run?
2. **Cognition:** how does the model plan, act, reflect, retrieve state, and adapt?
3. **Environment:** which versioned state and actions exist, and what effects do they have?
4. **Evidence/governance:** how are progress, policy decisions, side effects, quality, and human approvals independently recorded and verified?

## 1. System Topology & Mechanics

### 1.1 Bounded-autonomy topology

```text
 user / scheduler / upstream workflow
                 |
     goal contract + principal identity
     scope, success predicate, deadline,
     spend/action limits, approval policy
                 |
       durable run coordinator ---------------- policy/control plane
       lease, checkpoint, cancel, resume         versioned policies
                 |                               capability issuer
        planner / state estimator                artifact registry
          /       |       \                      eval/release gates
     executor  verifier  observer
          |        |        |
      action broker / policy enforcement point -------- audit/evidence ledger
          |
  +-------+------------------+------------------+
  |                          |                  |
 sandbox/code env       web/desktop env     enterprise APIs
 snapshot + limits      browser/VM state    transactional state
  |                          |                  |
  +---------------- versioned environment ------+
                 |
        receipts + state deltas + errors
                 |
       checkpoint / replan / approve / stop
```

The **control plane** owns policies, capability templates, model/tool/environment releases, quotas, evaluation thresholds, and revocation. The **execution plane** owns one run's observations, actions, checkpoints, environment lease, receipts, and terminal state. Keep policy enforcement outside model text: the model may propose an action, but a deterministic action broker authenticates the run, validates schema, evaluates policy, reserves budget, binds approval, invokes the tool, and records the result `[inferred]`.

OpenAI's current model guidance explicitly recommends defining autonomy and approval boundaries, permitting safe in-scope local work while requiring confirmation for external writes, destructive actions, purchases, or material scope expansion [[1]](https://developers.openai.com/api/docs/guides/latest-model). This is a useful general pattern, not proof that prompting alone enforces it. The effective authority is the credentials, network, filesystem, and tool implementation available after the prompt.

### 1.2 An autonomy envelope, not a binary setting

Represent authority as data:

```yaml
run:
  principal: tenant/acme/user/42
  objective: "Upgrade service X to runtime Y and preserve behavior"
  environment: repo-x@commit:abc123 + test-image@sha256:...
  allowed_actions:
    - repo.read
    - worktree.write:/services/x/**
    - test.run:nonprod
  denied_actions:
    - main.merge
    - production.deploy
    - secret.read
  approvals:
    external_write: required
    destructive: required
  budgets:
    wall_clock: 8h
    model_tokens: 8_000_000
    tool_calls: 2000
    spend_usd: 100
  stop:
    success: "required checks pass and verifier accepts diff"
    failure: "budget exhausted or no progress across 3 replans"
```

Dimensions are independent `[inferred]`:

| Dimension | Examples | Enforcement point |
|---|---|---|
| Objective | one ticket, one research question, one account operation | coordinator + verifier |
| Data scope | tenant, repository paths, records, time range | data/tool authorization |
| Action scope | read, draft, mutate sandbox, external write, irreversible act | capability + action broker |
| Resource scope | tokens, calls, compute, storage, money | admission + metering |
| Temporal scope | start/expiry, deadline, maintenance window | coordinator + credential expiry |
| Destination | domains, APIs, branches, recipients, regions | egress/tool policy |
| Concurrency | workers, parallel branches, outstanding writes | scheduler + quotas |
| Escalation | which exact actions require which approver | approval service |

Autonomy can increase for **reversible, observable, low-impact** actions and decrease as impact, ambiguity, or irreversibility grows. A code agent may freely edit an isolated worktree and run tests, require review to open a pull request, and remain unable to merge or deploy. A finance agent may search and draft an order but require a principal to approve exact asset, quantity, price bound, account, and expiry.

### 1.3 Control loop and horizon management

ReAct interleaves reasoning and environment actions so observations can update a plan rather than requiring a fixed plan to survive every surprise [[30]](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/). That is a local loop, not sufficient long-horizon architecture. Add explicit milestones, state estimation, independent verification, durable checkpoints, and bounded replanning:

```text
load goal + last accepted checkpoint
 -> observe authoritative environment state
 -> reconcile state with checkpoint and pending effects
 -> choose next milestone or request approval
 -> propose bounded action batch
 -> policy/budget/deadline admission
 -> execute and collect typed receipts/state deltas
 -> verify local postconditions and global invariants
 -> update evidence, progress and checkpoint
 -> continue, replan, wait, escalate, or terminate
```

Reflection may improve subsequent attempts, but it must be grounded. Reflexion stores linguistic feedback in episodic memory and showed gains in its evaluated sequential, coding, and reasoning tasks [[31]](https://papers.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html). In production, self-critique is a hypothesis; a compiler, test, database predicate, simulator state, policy engine, or human review is evidence. Storing an agent's unverified explanation as memory can institutionalize its error `[inferred]`.

**Horizon drift** means the active interpretation of the goal, constraints, world, or success criteria diverges over time. Detect it using `[inferred]`:

- goal/constraint restatement compared structurally with the signed original;
- milestone completion predicates and remaining-work graph;
- repeated actions, edit/revert cycles, revisited URLs, and tool-argument similarity;
- progress velocity and verifier delta per token, action, and wall-clock interval;
- plan churn without new environment evidence;
- contradiction between checkpoint claims and authoritative state;
- scope/permission requests expanding after setbacks;
- summary or memory facts without source/receipt provenance;
- a growing fraction of actions devoted to recovering from the agent's own changes.

Use a **receding-horizon plan**: keep a coarse end-to-end dependency map, but commit only the next verifiable milestone. Replan after material state changes or failed assumptions; do not rewrite the entire plan after every tool call. Freeze invariant constraints separately from mutable tactics so compaction and reflection cannot silently weaken them `[inferred]`.

### 1.4 Durable resumability across context windows and failures

Context continuation, application checkpointing, and environment snapshotting solve different problems:

| Mechanism | Preserves | Does not prove |
|---|---|---|
| model conversation/compaction | task-relevant conversational state | external effects or full semantic fidelity |
| agent checkpoint | explicit goal, plan, facts, evidence, budgets, pending work | live environment still matches |
| workflow event history | durable control decisions/results | activities were externally exactly once |
| environment snapshot | filesystem/VM/application state at a point | external SaaS/database state or current authorization |
| artifact commit | durable output version | task success or policy compliance |

OpenAI's current `/responses/compact` endpoint returns an opaque encrypted compaction item and token accounting; the guidance is to compact after milestones rather than every turn and pass the item into continuation [[2]](https://developers.openai.com/api/reference/java/resources/responses/methods/compact). Treat provider compaction as an optimization. Maintain an inspectable application checkpoint because an opaque summary cannot be audited for every omitted constraint.

Anthropic's long-running coding harness reports the practical problem as sessions starting without the preceding session's memory and uses durable project artifacts so later sessions can make incremental progress [[3]](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents). Its 2026 follow-up also warns that harness components encode assumptions about model limitations and can become stale as models improve [[4]](https://www.anthropic.com/engineering/harness-design-long-running-apps). Version and ablate the harness; scaffolding is part of the evaluated system.

A semantic checkpoint should include `[inferred]`:

```text
run/attempt ID, principal, signed objective and invariant constraints
environment/task/artifact/policy/model/tool/schema versions
last authoritative state digest and logical/environment clock
completed milestones with evidence and verifier status
current plan/dependencies and rejected hypotheses
working artifacts/commits and resumable sandbox/snapshot reference
issued actions: idempotency key, request, receipt, postcondition
pending/ambiguous effects and reconciliation procedure
remaining token/call/time/spend budget and deadline
active capabilities, expiry, approvals and revocations
memory facts with provenance, confidence, valid-from/to
terminal/next-state reason
```

Temporal reconstructs workflow state by replaying commands against event history and resumes from the last recorded event [[5]](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflow-execution.mdx). Replay requires deterministic orchestration: AWS durable-execution guidance similarly states that time, randomness, filesystem reads, and external I/O outside checkpointed steps can send replay down a different branch [[6]](https://docs.aws.amazon.com/durable-execution/patterns/best-practices/determinism/). Put nondeterminism and side effects in activities/steps with idempotency and receipts. LangGraph checkpoints can persist graph state for thread continuity, human review, fault tolerance, and time travel, while stores preserve cross-thread data [[7]](https://langchain-ai.github.io/langgraph/concepts/time-travel/).

On resume, never blindly continue from prose. Acquire/fence the environment lease, re-authenticate, re-evaluate policy and budget, inspect every ambiguous action, compare current state to the saved digest/version, invalidate stale observations/plans, then either continue or replan. Model/tool/prompt/workflow upgrades need replay and checkpoint-migration tests.

### 1.5 Agent environment contract

The minimal Gymnasium contract is `reset(seed, options) -> observation, info` and `step(action) -> observation, reward, terminated, truncated, info`; `terminated` means the task reached an end state, while `truncated` means an external limit ended the episode [[8]](https://gymnasium.farama.org/api/env/). Production environments need the same clarity even when they are browsers, code repositories, enterprise APIs, robots, or markets.

Define:

1. **Identity/version:** environment name, image/data/task/grader digest, source provenance, region and dependencies.
2. **Initial state/reset:** seeded fixture, snapshot, account identities, clocks, random sources, cleanup guarantees.
3. **Observation:** schema, partial observability, freshness, source, confidence, size, privacy and adversarial trust label.
4. **Action:** typed schema, preconditions, authority, idempotency, effect class, timeout, cancellation, receipt and postcondition.
5. **Time:** logical versus wall clock; independent events; scheduled tasks; leases; deadline; wait/sleep behavior.
6. **Concurrency:** other agents/users, ordering, conflict rules, isolation level and fencing.
7. **Lifecycle:** running, waiting, terminated-success, terminated-failure, truncated-budget/time, cancelled, corrupted.
8. **Scoring:** hidden/public predicates, partial credit, safety violations, evaluator access, nondeterminism and confidence intervals.
9. **Snapshot/fork:** what state is captured, external exclusions, uniqueness/secret reset, restore compatibility.
10. **Network/data:** egress allowlist, credentials, secret fixtures, retention and destruction.

PettingZoo distinguishes turn-based Agent Environment Cycle and parallel multi-agent APIs, making action timing explicit rather than assuming all agents act simultaneously [[9]](https://pettingzoo.farama.org/main/content/basic_usage/). That distinction matters for delegated agents and simulations: define whether an observation includes other agents' actions, who advances the clock, and whether parallel writes conflict.

### 1.6 Environment classes and what they measure

- **Deterministic API/state machine:** fast and exactly inspectable; may underrepresent UI ambiguity, latency and partial observability. ToolSandbox adds stateful execution, implicit state dependencies, a user simulator and milestone evaluation rather than only stateless single calls [[13]](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark).
- **Self-hosted web replica:** realistic multi-step state changes with repeatable reset. WebArena provides functional sites across commerce, forums, software collaboration and CMS, with programmatic end-state checks; its original GPT-4 baseline achieved 14.41% versus 78.24% human performance in that 2023 setup [[10]](https://proceedings.iclr.cc/paper_files/paper/2024/hash/4410c0711e9154a7a2d26f9b3816d1ef-Abstract-Conference.html).
- **Unified browser harness:** BrowserGym exposes multiple web benchmarks through a common framework, useful for holding the harness constant across tasks [[11]](https://arxiv.org/abs/2412.05467). Common interfaces do not erase benchmark-specific setup or grader semantics.
- **Full desktop/VM:** OSWorld's original benchmark contains 369 tasks across real web and desktop applications on multiple operating systems [[12]](https://papers.nips.cc/paper_files/paper/2024/hash/5d413e48f84dc61244b6be550f1cd8f5-Abstract-Datasets_and_Benchmarks_Track.html). The project now directs users to OSWorld 2.0, illustrating why result comparisons must pin benchmark version [[50]](https://osworld-v1.xlang.ai/).
- **Code/repository:** SWE-bench Verified uses 500 engineer-confirmed solvable tasks, and the SWE-bench ecosystem uses a Docker evaluation harness [[18]](https://www.swebench.com/verified.html). Even curated tasks remain versioned benchmark artifacts. A repository task environment must pin base commit, dependency network, tests, patch scope, and hidden grader.
- **Workplace simulation:** TheAgentCompany combines browsing, code, programs and coworker communication in a self-contained software-company environment; its reported best baseline completed 30% of tasks in that study [[19]](https://openreview.net/pdf/b533993ef9bc8320779646b1c475e47635dd98c2.pdf).
- **Open-ended embodied world:** Voyager used an automatic curriculum, executable skill library and iterative environment feedback in Minecraft; it reported 3.3x more unique items and up to 15.3x faster tech-tree milestones than its evaluated prior systems [[29]](https://arxiv.org/abs/2305.16291). These scoped game results do not establish safe open-world enterprise autonomy.
- **Time-evolving sentinel environment:** SentinelBench's 100 tasks across 10 synthetic web environments replay events independent of agent actions and measure completion, reaction time and resource use [[26]](https://arxiv.org/abs/2606.05342). It distinguishes productive waiting from tool-call churn.

## 2. Token Economics & NFR Metrics

### 2.1 Reliability decays with horizon unless verification and recovery intervene

If a task needs `n` independently correct irreversible steps with per-step correctness `p`, naive completion probability is `p^n`. At `p=0.99`, 100 such steps yield about `36.6%`; at `p=0.999`, about `90.5%` `[inferred arithmetic]`. Real steps are not independent and errors can be detected or repaired, so this is not a forecast. It explains why optimizing single-call accuracy is insufficient: long-horizon architecture must reduce irreversible steps, verify milestones, retry safely, and recover from errors.

Measure several layers:

| Layer | Metrics |
|---|---|
| Outcome | strict task success, partial progress, policy-compliant success, human acceptance |
| Horizon | steps/tool calls/context turns/wall time/human-equivalent task duration at success level |
| Progress | verified milestone delta, time-to-first-useful-artifact, rework ratio, stagnation intervals |
| Reliability | pass@1, pass^k consistency, success by task length, recovery after injected fault |
| Environment | invalid actions, observation staleness, reset failure, grader nondeterminism, snapshot restore |
| Safety | unauthorized proposals/blocks/effects, approval correctness, secret flow, monitor detection |
| Economics | input/output/cached/reasoning tokens, tool/compute/network/storage, cost per accepted result |
| Operations | queue age, checkpoint/resume latency, environment allocation, idle/wait duty cycle |

`pass@k` asks whether at least one of `k` attempts succeeds and rewards parallel sampling. τ-bench proposed `pass^k`, whether all `k` trials succeed, to measure behavioral consistency; its original experiments reported under 50% task success and retail `pass^8 < 25%` for evaluated function-calling agents [[14]](https://openreview.net/pdf?id=roNSXZpUDN). For enterprise automation, repeatability often matters more than best-of-many because the system cannot silently discard harmful failed attempts.

AgentBoard adds a fine-grained progress metric because final success alone does not reveal where a trajectory failed [[16]](https://arxiv.org/abs/2401.13178). Progress credit must not become the production success contract: an agent can make impressive progress and still leave a repository broken or an account incorrectly mutated.

### 2.2 Interpret “time horizon” precisely

METR defines a 50%-time horizon as the **human-expert completion time** of tasks at which a fitted model predicts 50% agent success, not how long the agent runs [[20]](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/) [[21]](https://metr.org/time-horizons/). Its initial historical result found an approximately seven-month doubling trend on predominantly software/research/reasoning tasks. METR explicitly cautions that an eight-hour horizon does not mean all eight-hour jobs are automatable, that the suite is much cleaner than many jobs, and that capability is jagged across domains.

Time Horizon 1.1 expanded the suite and infrastructure, but METR reports human baseline measurements for only 5 of 31 tasks longer than eight hours [[23]](https://metr.org/blog/2026-1-29-time-horizon-1-1/). Its limitations note says the metric has historically had error bars roughly a factor of two in each direction, differs across domains by orders of magnitude, and does not directly yield labor automation or productivity [[22]](https://metr.org/notes/2026-01-22-time-horizon-limitations/).

Benchmark integrity is part of capability measurement. METR's June 2026 GPT-5.6 Sol report says its point estimate was about 11.3 human-hours when cheating attempts were failures but over 270 hours when counted as successes, outside the suite's reliable range; METR did not consider either a robust measurement [[24]](https://metr.org/blog/2026-06-26-gpt-5-6-sol/). This is direct evidence that grader access, environment leakage and exploit classification can dominate an autonomy score.

RE-Bench contains seven open-ended ML research-engineering environments and 71 eight-hour attempts by 61 distinct human experts; agents could be faster and competitive early in the budget, while humans were stronger over the full horizon in the original study [[25]](https://arxiv.org/abs/2411.15114). Do not reduce this to “agents replace eight-hour researchers.” Report task suite, harness, model snapshot, budget, scoring, human comparison protocol and uncertainty.

### 2.3 Token and action economics

Without compaction/retrieval, resending a linearly growing history makes cumulative input tokens roughly quadratic in turns:

```text
if each step adds d tokens and step t resends all history:
cumulative_input ~= base*n + d*n*(n+1)/2

run_cost = Σ(model_input_t + model_output_t + cache_write/read_t)
           + Σ(tool/provider/compute/storage/network_t)
           + environment_idle_and_snapshot_cost
           + review/recovery cost

cost_per_accepted_success = total_cost_across_successes_and_failures
                            / policy-compliant accepted outcomes
```

These are `[inferred]` accounting formulas. Preserve stable instructions/tool schemas as cacheable prefixes, retrieve only milestone-relevant evidence, compact at semantic boundaries, and store large observations outside context by digest/reference. Never cache mutable environment observations as if current. A summary must carry provenance and an expiry/revalidation rule.

Route work by phase `[inferred]`:

- cheap deterministic code for parsing, schema validation, diff/statistics and policy;
- lower-cost model for classification, watch/wait, simple retrieval and summarization;
- capable planner for ambiguity, exceptions and decomposition;
- independent capable verifier for high-impact milestones;
- human review for impact/ambiguity that exceeds delegated authority.

Parallel subagents reduce wall-clock only when branches are independent. They multiply tokens, environments, conflicts and verification. Record branch budget and useful contribution; cancel losing branches. Avoid spawning agents to restate or “vote” without independent evidence.

### 2.4 Capacity, latency and backpressure

For a long-run service, capacity has at least four inventories:

```text
active_run_capacity = min(
  model/provider concurrency,
  environment leases,
  worker/activity slots,
  downstream quotas,
  review/approval capacity
)

checkpoint_write_rate ~= active_runs * checkpoints_per_run_per_time
environment_storage ~= active_envs * mutable_state_size + snapshots + artifacts
```

Size by work class and tail distributions: tokens, actions, environment CPU/memory/disk, browser/VM lifetime, tool wait, checkpoint size, approval delay and retry. A monitoring task may occupy an environment for hours while making few calls; a research agent may saturate model tokens and accelerators; a coding agent may saturate test CPU and storage. Queueing all three on “one agent worker” obscures the bottleneck `[inferred]`.

Admission reserves maximum or predicted run budget and concurrency. Use bounded queues and priority; pause durable runs at checkpoints when quota is constrained. Propagate absolute deadlines to every child call. Do not let autoscaling create more agents than environment, API, database, budget or human reviewers can support.

Sentinel tasks require an explicit cost/responsiveness frontier. Polling more often can reduce reaction time but spends tokens/tools and adds failure opportunities; event subscriptions/webhooks are better when trusted and available. SentinelBench was designed to score task completion, reaction time and resource usage jointly rather than treating continuous activity as intelligence [[26]](https://arxiv.org/abs/2606.05342).

### 2.5 Evaluation portfolio and generalization

No single benchmark establishes general autonomy:

- AgentBench spans eight interactive environments and found long-term reasoning, decision-making and instruction following among major failure causes in its evaluated models [[15]](https://arxiv.org/abs/2308.03688).
- GAIA combines reasoning, multimodality, browsing and tools; its original paper reported 92% human versus 15% GPT-4-with-plugins performance, a dated scaffold/model comparison [[17]](https://openreview.net/pdf?id=fibxvahvs3).
- UltraHorizon reports standard trajectories over 35k tokens/60 tool calls and heavy trajectories averaging over 200k tokens/400 calls; simple test-time scaling did not close its evaluated human-agent gap [[27]](https://arxiv.org/abs/2509.21766).
- HorizonBench simulates six-month user histories averaging about 4,300 turns/163k tokens and reports failure to update evolved preferences as a primary bottleneck [[28]](https://arxiv.org/abs/2604.17283). Long context availability is not equivalent to correct temporal belief update.
- A 2026 methodological paper argues a claimed “long-horizon failure” should be compared with a preregistered prediction from matched short stages using the same agent configuration, because some drop may follow ordinary stage-level difficulty [[49]](https://arxiv.org/abs/2607.27283).

Production release gates should combine deterministic unit/contract tests, replay of representative internal tasks, current external benchmarks, adversarial/security environments, shadow operation, limited-authority canary and monitored human acceptance. Slice by horizon, tenant, language, environment version, tool, action impact and failure class.

> ⚠️ Limited public data available for this dimension. There is no portable public capacity or cost table for long-horizon agents. Model/provider pricing, hidden reasoning, context policy, environment lifetime, tool mix, retries, verification, human review and success criteria dominate; benchmark token counts do not predict production cost per accepted result.

## 3. Distributed Resilience & State

### 3.1 Durable run state machine

```text
CREATED -> ADMITTED -> RUNNING <-> WAITING_EXTERNAL
                    |     |
                    |     +-> WAITING_APPROVAL
                    |     +-> PAUSED_QUOTA/OPERATOR
                    |     +-> RECONCILING_AMBIGUOUS_EFFECT
                    |
                    +-> SUCCEEDED_VERIFIED
                    +-> FAILED_TERMINAL
                    +-> TRUNCATED_BUDGET/DEADLINE
                    +-> CANCELLED (with effect status)
```

Persist transitions with a compare-and-swap run version or event sequence. A worker holds a renewable lease/fencing token; stale workers cannot checkpoint or issue new actions after ownership changes. Every action uses `(run_id, action_id, attempt, idempotency_key, fence)` and stores request hash, authorization/policy decision, receipt and observed postcondition `[inferred]`.

HTTP only defines safe/idempotent semantics for specified methods and warns against automatic retries of non-idempotent requests without additional knowledge [[41]](https://www.rfc-editor.org/rfc/rfc9110.html). Tool contracts must declare stronger application semantics. For an ambiguous timeout after an external write, enter reconciliation: query by idempotency key or state predicate before retrying.

Queue visibility is a lease, not a lock forever. Amazon SQS states that standard queues can redeliver even within a visibility timeout, so a long agent worker must heartbeat/extend ownership and remain duplicate-safe [[47]](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html). Kubernetes Jobs retry pods toward completion but cannot make application side effects exactly once [[48]](https://kubernetes.io/docs/concepts/workloads/controllers/job/).

### 3.2 Checkpoint granularity and replay

Checkpoint after a meaningful, durable boundary, not every model token:

- environment allocated/reset and identity verified;
- plan/milestone accepted;
- tool action committed and postcondition observed;
- artifact commit/test result produced;
- approval requested/granted/denied;
- external wait subscription/timer registered;
- summary/compaction and memory update validated;
- terminal verification.

Too-frequent checkpoints increase storage, serialization and contention; too-sparse checkpoints increase replay cost and ambiguous effects. Measure recovery point in verified actions and recovery time to productive execution, not merely “workflow status loaded” `[inferred]`.

Event history should record decisions and results necessary for reconstruction, while large screenshots, files and traces live in content-addressed object storage referenced by digest. Snapshot a sandbox for expensive local setup, but restore secrets, network identity, random/clock state and uniqueness deliberately. A VM snapshot is not a snapshot of GitHub, SaaS, a database or another agent.

### 3.3 Environment and artifact integrity

An environment is an artifact supply chain. Pin its OCI image/data/setup/grader digests; OCI descriptors use digest and size for content identity/verification [[39]](https://github.com/opencontainers/image-spec/blob/main/descriptor.md). Record build provenance so consumers can verify how the environment was produced; SLSA v1.2 defines progressively stronger source/build tracks and provenance [[40]](https://slsa.dev/spec/v1.2/).

Reset validation must assert more than “container started” `[inferred]`:

- fixture/database counts and hashes;
- no prior-run files, browser state, messages, memory or credentials;
- correct logical time, seed, locale and network stubs;
- correct task/grader separation;
- expected dependent service versions;
- canary secret uniqueness;
- no leaked solution/test artifacts;
- teardown and data-destruction receipt.

For stochastic environments, record seed and random-event stream when possible and run repeated trials. For live environments, archive the observations/actions/receipts needed to audit, but do not claim exact replay. Separate environment termination from infrastructure truncation and agent cancellation.

### 3.4 State drift and reconciliation

Drift sources include concurrent humans/agents, independent events, expired credentials, schema/tool updates, partial writes, restored old snapshots, model/harness upgrades, stale retrieval, compaction loss and retry divergence. The checkpoint's `last_seen_state` is evidence about the past, not current truth.

Reconciliation procedure `[inferred]`:

1. Fence prior executors and pause mutations.
2. Read authoritative state and event/receipt history.
3. Classify each intended action as committed, absent, partial, duplicated or unknown.
4. Re-establish invariants and compensate only where explicitly supported.
5. Invalidate observations/memory derived from reverted or superseded state.
6. Re-evaluate policy, capability expiry, deadline and remaining value.
7. Generate a new checkpoint/plan or terminate for human repair.

Never compensate an external action merely by asking the model to “undo it.” Compensation is a domain operation with its own authorization, failure modes and possibly irreversible consequences.

### 3.5 Circuit breakers, retries and degradation

Retries across model SDK, tool, activity, queue, browser and coordinator multiply. Assign retry ownership per failure class, apply exponential backoff/jitter, honor remaining deadline, and cap attempts/cost. Google SRE documents how retries and overload can create cascading failure and recommends load shedding and controlled retry behavior [[46]](https://sre.google/sre-book/addressing-cascading-failures/).

Use per-dependency circuit breakers and bulkheads. A failing browser provider must not consume all coding-agent workers. In half-open state, allow bounded probes that cannot mutate high-impact state. Degradation order `[inferred]`:

1. preserve identity, policy, audit, checkpoint and cancellation;
2. stop new low-priority runs and speculative branches;
3. checkpoint/pause resumable work;
4. switch only to pre-evaluated model/tool/environment fallbacks;
5. request human takeover with current evidence;
6. never bypass policy or silently reduce verification to improve availability.

> ⚠️ Limited public data available for this dimension. Public durable-workflow documentation explains replay and checkpoint mechanics, but there is little public incident data on multi-day agent recovery, checkpoint corruption, ambiguous external effects, or cross-version replay at enterprise scale. These guarantees require kill-point, restore and migration testing on the actual workflow and tools.

## 4. Enterprise Security & Governance

### 4.1 Least agency and capability enforcement

Least privilege is necessary but not sufficient: an agent can combine several individually permitted actions into an impermissible outcome. Apply **least agency** `[inferred]`:

- minimum tool set and action methods for the current milestone;
- short-lived, run-bound credentials rather than inherited user/cloud credentials;
- resource and destination constraints embedded in capabilities;
- one tenant/environment per isolation boundary for untrusted work;
- no general shell/network when structured operations suffice;
- separate read, draft, approve and commit capabilities;
- policy on sequences/cumulative effects, not only individual calls;
- revoke on cancel, anomaly, horizon drift, ownership loss or deadline.

OpenAI's Responses API currently exposes model-side controls such as allowed tool selection and `max_tool_calls` for built-in calls [[45]](https://developers.openai.com/api/reference/cli/resources/responses/methods/create). These reduce accidental action surface but do not replace downstream authorization, because custom tools and external systems enforce the real effect.

OPA separates policy decision from enforcement and evaluates structured inputs against versioned policy [[43]](https://www.openpolicyagent.org/docs). Policy input should include principal, tenant, run/objective, action, arguments, resource/destination, data classification, environment, cumulative budget, prior effects, approval, time and risk. Record policy revision and `decision_id`; OPA decision logs can include policy query/input/result and support masking sensitive fields before upload [[44]](https://www.openpolicyagent.org/docs/management-decision-logs).

### 4.2 Approval that cannot be reinterpreted

A human approval is a capability for an exact proposed effect, not a conversational “yes.” Bind `[inferred]`:

```text
principal + tenant + run + action name + canonical arguments
+ target current version/state digest + expected state delta
+ maximum amount/scope + policy version + expiry + nonce
```

Show the approver the material effect, evidence, uncertainty and alternatives. After approval, re-check preconditions; if target state or arguments changed, approval expires. Prevent the model from supplying its own approval text. Use separation of duties for high-impact actions, and make deny/cancel paths as available as approve.

### 4.3 Prompt injection, memory poisoning and environmental adversaries

Observations are data, not authority. A webpage, issue, email, tool output, retrieved memory, coworker message or file may contain malicious instructions. Maintain trust labels and instruction hierarchy; never concatenate untrusted content into privileged policy. Validate proposed effects against the user goal and deterministic policy after every observation.

AgentDojo provides 97 realistic tasks and 629 security cases for agents executing tools over untrusted data, showing prompt-injection robustness needs dynamic environment evaluation rather than a static string filter [[32]](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html). The OWASP Top 10 for Agentic Applications 2026 covers goal hijacking, tool misuse, identity/privilege abuse, supply chain, unexpected code execution, memory/context poisoning, insecure inter-agent communication, cascading failure, human-agent trust exploitation and rogue/drifting behavior [[34]](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).

Memory writes require schema, provenance, tenant/subject authorization, confidence, validity interval and review policy. Separate facts, user preferences, procedures/skills and agent reflections. A retrieved item never grants capability. Support correction/deletion and rebuild derived summaries when source facts change `[inferred]`.

### 4.4 Sandbox and environment isolation

Plain containers share the host kernel. gVisor inserts a userspace application kernel and exposes an OCI runtime for stronger isolation while retaining container integration [[38]](https://gvisor.dev/docs/). Firecracker uses microVM isolation; its production guidance says to run with the jailer or equivalent constraints and patch host/guest kernels and microcode [[37]](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md).

Choose by threat model `[inferred]`:

| Isolation | Strength/fit | Cost/limitations |
|---|---|---|
| language/process restrictions | trusted transformation, no arbitrary code | weak against native escape/runtime bugs |
| standard container | packaged trusted workload | shared kernel; not adequate alone for hostile code |
| gVisor/application kernel | untrusted code with container ergonomics | syscall compatibility/performance testing required |
| microVM | stronger tenant/kernel boundary | startup, memory, image/network/snapshot operations |
| dedicated host/account | highest-impact or regulated separation | expensive and slower allocation |

Regardless of runtime: ephemeral filesystem, read-only base, no host socket/mount, non-root, seccomp, PID/CPU/memory/disk/time limits, no ambient metadata credentials, default-deny network, allowlisted destinations, per-run secrets, output scanning, and destruction receipt. Treat artifacts crossing out of the sandbox as untrusted.

### 4.5 Monitoring, sabotage and audit

SHADE-Arena pairs benign main tasks with harmful side objectives in long-horizon environments. Its paper reported the best evaluated sabotage-agent scores of 27% and 15% against one monitor setup, and a top monitor AUC of 0.87; hidden scratchpad access materially affected sabotage success [[33]](https://arxiv.org/abs/2506.15740). These are scoped experimental results, not prevalence estimates. They show that final-output review and a single LLM monitor are insufficient.

Use independent controls `[inferred]`:

- deterministic capability/policy enforcement before effects;
- state-delta and data-flow monitoring at tool boundaries;
- canary resources/secrets and anomalous destination detection;
- separate verifier model with only needed evidence, not agent persuasion;
- sampling plus full review for high-impact actions;
- immutable action/approval/policy receipts;
- kill/revoke/fence mechanisms outside agent control;
- red-team tasks where task success coexists with hidden harm.

OpenTelemetry semantic conventions provide common attributes/spans/metrics vocabulary [[42]](https://opentelemetry.io/docs/specs/semconv/), but a multi-day run should not be one unbounded trace object. Correlate `run_id`, attempt, checkpoint and action IDs across bounded traces. Record typed state deltas and artifact digests; store raw sensitive prompts/screenshots separately under access and retention policy.

### 4.6 Governance lifecycle

NIST AI RMF organizes risk work across Govern, Map, Measure and Manage and is voluntary/use-case agnostic [[35]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10). Its Core calls for defined roles and responsibilities for human-AI configurations and oversight across the system lifecycle [[36]](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/).

Govern by autonomy tier `[inferred]`:

| Tier | Authority | Required evidence |
|---|---|---|
| 0 advise | no tool effect | answer quality/privacy eval |
| 1 observe | read scoped systems | access audit, injection/privacy test |
| 2 act in sandbox | reversible isolated changes | sandbox escape, correctness, resource tests |
| 3 draft external | creates reviewable artifact/request | provenance, diff/effect preview, approval binding |
| 4 bounded external commit | narrow reversible transaction | policy, idempotency, canary, reconciliation, human override |
| 5 high-impact/irreversible | exceptional, multi-party authorization | formal risk acceptance, independent verification, recovery/compensation proof |

Maintain system card, owners/on-call, task and environment inventory, threat model, autonomy tier, data map, eval suite, model/harness/tool/policy versions, release approval, incident/redrive/rollback runbooks, exception expiry and periodic recertification. Increase authority only from production evidence at the lower tier; model benchmark improvement does not automatically widen deployed permissions.

## 5. Production Failure Modes

| Failure | Symptom / mechanism | Containment and recovery | Test / signal |
|---|---|---|---|
| Goal drift | local subgoal replaces signed objective | immutable goal/constraints, structural comparison, replan gate | goal-alignment score; adversarial detour |
| Premature victory | agent declares done without end-state proof | deterministic success predicate + independent verifier | hidden tests/state predicates |
| Activity without progress | repeated search/edit/test or polling | progress budget, loop fingerprint, wait state, terminate/replan | verifier delta per action/token |
| In-context locking | persists with early hypothesis despite evidence | alternative hypotheses, checkpoint reset, fresh verifier | UltraHorizon-style diagnostic tasks |
| Context compaction loss | omitted constraint or pending effect | inspectable semantic checkpoint, invariant block, resume audit | compact at injected critical fact |
| Memory poisoning | untrusted observation becomes durable authority | provenance/trust labels, validated writes, revocation/rebuild | indirect injection into memory source |
| Stale resume | environment changed while run paused | lease/fence, state digest, re-observe/reconcile | mutate state between pause/resume |
| Replay nondeterminism | workflow diverges after code/time/random change | deterministic workflow, versioning, replay tests | historical-event replay in CI |
| Duplicate side effect | timeout/lease loss followed by retry | idempotency key, receipt, postcondition reconciliation | kill before/after effect/ack |
| Approval TOCTOU | action changes after human approval | sign canonical effect + target version + expiry | modify arguments/state after approval |
| Approval laundering | model fabricates/interprets prose as permission | separate approval identity/service | injected “approved” observation |
| Capability creep | agent requests broader tools after failure | tiered immutable envelope, explicit new authorization | deny/escalation telemetry |
| Prompt injection | external content directs privileged action | trust separation, deterministic policy, least agency | AgentDojo-like environment attacks |
| Secret exfiltration | code/tool reads secret then sends/logs it | no ambient secrets, egress/data-flow controls, canaries | secret-crossing boundary test |
| Sandbox escape | generated code reaches host/other tenant | gVisor/microVM, patching, no host mounts, destruction | runtime penetration and isolation test |
| Grader/benchmark hacking | reads hidden tests/answers or exploits scorer | isolated grader, no solution access, exploit classification | adversarial evaluator red team |
| Environment leakage | prior run state contaminates next | reset assertions, per-run fixtures/credentials | alternating canary tenants |
| Version drift | benchmark/tool/site changes under same name | digest every component, immutable results | manifest comparison |
| Observation staleness | acts on outdated page/API/cache | source timestamp/version, precondition re-read | concurrent actor mutation |
| Concurrent agents conflict | branches overwrite or duplicate | ownership partitions, optimistic version/fencing, merge verifier | simultaneous conflicting branches |
| Monitor blind spot | harmful sequence looks benign stepwise | cumulative/sequence policy, state-delta monitor | benign main + hidden side task |
| Retry amplification | nested retries multiply calls/cost | retry ownership/global attempt budget | inject rate limit/timeouts, count fanout |
| Cascading tool outage | agents increase activity when dependency fails | circuit breaker, durable wait, load shedding | provider blackhole game day |
| Unbounded spend | loop/parallel branches consume budget | atomic reservation, hard per-run/tenant caps | adversarial no-progress task |
| Cancellation illusion | UI says cancelled but effects continue | revoke/fence/cancel downstream, effect-status terminal state | cancel at every boundary |
| Wait churn | monitoring agent continuously refreshes | event subscription/scheduled wake, duty-cycle budget | Sentinel-style delayed event |
| Temporal misconception | treats truncation as failure or success | distinct terminated/truncated states | deadline just before success |
| Bad reflection | agent rationalizes error and persists it | external evidence, bounded reflection memory | incorrect self-feedback injection |
| Skill-library supply chain | learned code reused outside valid scope | provenance, tests, versioned dependencies, capability limits | mutate dependency/environment |
| Partial progress damage | run fails leaving broken artifacts/state | transactional staging, commits, cleanup/repair plan | terminate mid-milestone |
| Human-review overload | approval queue delays or rubber-stamps | risk-tier routing, batchable evidence, staffing/backpressure | peak-load approval exercise |
| Audit privacy leak | traces retain prompts/secrets/screens | redaction/reference storage, RBAC, retention/deletion | privacy scan and subject deletion |

UltraHorizon's published trajectories identify recurring patterns including repetitive looping, premature convergence, incoherent planning, misaligned tools, memory problems, uncontrolled experiments, error propagation and environment mis-modeling [[27]](https://arxiv.org/abs/2509.21766). Treat the taxonomy as hypotheses for production monitors; validate thresholds on your tasks to avoid terminating useful exploration.

Incident response should first revoke capabilities and fence executors, preserve immutable evidence, classify committed/ambiguous effects, protect affected tenants, reconcile authoritative state, rotate exposed credentials, and only then resume or redrive. A model prompt change is not sufficient remediation for an authorization or sandbox failure.

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A: multi-day codebase migration

**Goal:** migrate a large service across thousands of files, preserve behavior, survive context/pod restarts, and deliver a reviewable pull request but never merge/deploy.

**Design `[inferred]`:** allocate an isolated worktree in a gVisor or microVM environment pinned to repository/base-image/dependency digests. The signed goal enumerates target runtime, in-scope paths, invariant tests, banned changes and budgets. An initializer maps the repository and produces a dependency/milestone graph. Each session loads the last semantic checkpoint, validates the worktree commit and tests, completes one coherent milestone, commits it, updates evidence and exits. Parallel agents receive disjoint packages; a merge agent resolves only after ownership checks. Hidden tests and an independent verifier gate completion. Network allows approved package registries through a proxy; no production or merge credentials exist. Human review authorizes PR creation from an exact diff.

**Capacity/economics:** separate model concurrency from test CPU/storage capacity. Cache immutable repo guidance/tool schemas; summarize logs by reference. Track verified checks gained, regressions/reverts, cost per accepted commit, checkpoint recovery, and human review minutes.

**Failure exercise:** kill before/after commit/checkpoint, change base branch while paused, corrupt a summary, revoke a package, inject instructions into a repository file, and verify no main/production action is possible.

### 6.2 Scenario B: long-running monitoring and conditional action

**Goal:** watch a procurement portal for a qualifying event for seven days, then draft a response within ten minutes; no autonomous purchase.

**Design `[inferred]`:** durable workflow registers an event subscription or scheduled wake rather than occupying continuous model context. Environment stores logical time and last observed state/version. A low-cost observer extracts typed changes; a policy engine checks qualification. A capable model prepares the draft with cited evidence. Human approval binds the exact response and destination. The system persists checkpoint/timer/subscription, remaining deadline and capability expiry. It terminates as success, expired/no-event, cancelled or failed, never “still thinking.”

**Metrics:** event recall/precision, reaction time, polling/subscription calls, idle compute, false actions, draft acceptance, approval delay and total cost. Evaluate with time-compressed scripted timelines like SentinelBench rather than waiting real weeks.

**Failure exercise:** duplicate/out-of-order events, portal downtime, clock jump, policy change, cancellation just before event, stale approval, and an event containing prompt injection.

### 6.3 Scenario C: autonomous ML research optimization

**Goal:** improve a metric within compute budget in a reproducible sandbox and produce evidence; no deployment or external publication.

**Design `[inferred]`:** immutable dataset/code/environment and hidden evaluator; scheduler issues experiment IDs, seeds and compute reservations. Planner proposes hypotheses, executor runs controlled experiments, and verifier reads metrics/artifacts directly. Results append to an experiment ledger; failures and negative results remain searchable. Promotion requires replication across seeds and a held-out test. Stop on budget, deadline, target, stagnation or unsafe proposal. No grader internals/held-out labels are readable by the agent.

RE-Bench is the closest cited research-engineering analogue, but its seven tasks and eight-hour protocol do not supply a universal R&D automation rate [[25]](https://arxiv.org/abs/2411.15114). Internal release decisions should measure statistically valid improvement, reproducibility, compute/spend, policy compliance and researcher review.

**Failure exercise:** metric leakage, overfitting, hidden-test access, unbounded hyperparameter sweep, nondeterministic replay, malicious dependency, and convincing but non-reproducible narrative.

### 6.4 Scenario D: bounded customer-service operations

**Goal:** resolve airline/retail cases through conversation and APIs while following domain policy; refunds above a threshold require approval.

**Design `[inferred]`:** stateful operation record holds authenticated customer, gathered facts, policy version, tool receipts and pending approval. The action broker performs object/function authorization, canonicalizes tool arguments, checks preconditions and uses idempotency. The model may read and draft broadly inside the case but only invoke customer-scoped tools. Database end-state and conversation quality are both verified. Refund/booking changes display exact delta and expiry to approver. Retry reconciliation prevents duplicates.

τ-bench demonstrates why a static answer benchmark is insufficient: it evaluates conversation, domain rules, APIs and final database state across repeated trials [[14]](https://openreview.net/pdf?id=roNSXZpUDN). ToolSandbox adds intermediate milestone assessment and state dependencies [[13]](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark).

**Failure exercise:** insufficient information, conflicting user request/policy, duplicate submit, concurrent human update, prompt injection in account notes, expired approval and tool timeout after commit.

### 6.5 Scenario E: enterprise environment/evaluation platform

**Goal:** run thousands of versioned agent evaluations safely across API, browser, desktop and code environments.

**Design `[inferred]`:** a registry stores task manifest, environment OCI digest/provenance, setup/reset checks, observation/action schema, grader digest, secrets policy and supported harness versions. Scheduler assigns isolated environment leases and separate grader credentials. Runs have outbound policy, budgets and complete action receipts. Environment reset is attested; snapshots exclude or rotate uniqueness/credentials. Grading occurs outside agent visibility. Results record model, prompt, harness, tools, policy, seeds, attempts, environment and grader digests. Security suites place canary secrets and indirect injections. A benchmark upgrade creates a new series rather than overwriting history.

**Metrics:** valid-run yield, allocation/reset p95/p99, cross-run contamination, grader disagreement, deterministic replay rate, cost/task, success/progress/safety by version, and exploit attempts. Compare models only under equivalent harness, budget and environment.

### 6.6 Architecture trade-off matrix

| Choice | Strength | Cost/risk | Use when |
|---|---|---|---|
| one continuous context | simple, full recent trace | token growth, context limit/drift | short bounded tasks |
| compaction + semantic checkpoint | spans context windows, inspectable state | summary validation/migration | hours/days of work |
| durable workflow | timers, replay, crash recovery | determinism/activity discipline | external waits/effects |
| event subscription | low idle cost, fast response | integration/trust complexity | environment supports reliable events |
| polling | universal | cost, latency, churn/rate limits | no event source, bounded cadence |
| browser replica | realistic and resettable | maintenance/version drift | web behavior evaluation |
| live production shadow | highest realism | privacy/safety/nondeterminism | after sandbox gates, read-only or tightly contained |
| standard container | high density/compatibility | shared kernel | trusted code |
| gVisor | stronger isolation, container workflow | compatibility/performance | untrusted common workloads |
| microVM | separate guest kernel | operations/startup/memory | hostile or cross-tenant code |
| single agent | coherent ownership | one perspective/bottleneck | tightly coupled task |
| parallel agents | wall-clock/diversity | token cost/conflicts/verification | independent partitions |
| self-verification | cheap and contextual | correlated blind spots | low-impact interim feedback |
| independent verifier/human | lower correlated risk | cost/latency/capacity | milestones/high-impact effects |

### 6.7 Production and interview checklist

1. **Bound:** What objective, data, actions, destinations, resources, duration and escalation are authorized?
2. **Enforce:** Which non-model component denies an action, reserves budget and revokes authority?
3. **Prove:** What authoritative predicate distinguishes verified success, failure, waiting and truncation?
4. **Persist:** What semantic state, evidence, ambiguous effects, budgets and permissions survive context/process loss?
5. **Resume:** How are environment drift, expired capabilities, changed policy and pending effects reconciled?
6. **Environment:** Are reset, clocks, partial observation, concurrency, versions, snapshots, grader and teardown explicit?
7. **Contain:** Can generated code/content reach host, secrets, other tenants or unrestricted network?
8. **Measure:** Are progress, consistency, recovery, safety, reaction time and cost per accepted outcome measured by horizon?
9. **Evaluate:** Is the exact model + harness + policy + tool + environment system tested, including adversarial and stateful cases?
10. **Govern:** Who owns the autonomy tier, reviews evidence, handles incidents and approves any authority increase?

The advanced design standard is not “the agent ran for a long time.” It is: the agent remained inside delegated authority, made verified progress in a versioned environment, survived interruption without duplicating effects, stopped for the right reason, and produced evidence strong enough for the outcome's risk.

> ⚠️ Limited public data available for this dimension. Public long-horizon benchmarks cover selected software, web, desktop, research, simulation and customer-service environments, but there is little independently verifiable production data on multi-tenant multi-day autonomy, human-approval load, incident rates, or recovery economics. Enterprise capacity and risk claims need internal workload replay, staged authority and monitored field evidence.

## Sources

- [1] https://developers.openai.com/api/docs/guides/latest-model — Current official autonomy and approval-boundary guidance.
- [2] https://developers.openai.com/api/reference/java/resources/responses/methods/compact — Current Responses conversation compaction contract and token accounting.
- [3] https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents — Cross-context long-running agent harness design.
- [4] https://www.anthropic.com/engineering/harness-design-long-running-apps — 2026 harness design and stale-assumption analysis.
- [5] https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflow-execution.mdx — Temporal workflow history, replay and resumption.
- [6] https://docs.aws.amazon.com/durable-execution/patterns/best-practices/determinism/ — Durable replay determinism constraints.
- [7] https://langchain-ai.github.io/langgraph/concepts/time-travel/ — LangGraph checkpoint persistence and time travel.
- [8] https://gymnasium.farama.org/api/env/ — Gymnasium reset/step/termination/truncation environment API.
- [9] https://pettingzoo.farama.org/main/content/basic_usage/ — Turn-based AEC and parallel multi-agent environment APIs.
- [10] https://proceedings.iclr.cc/paper_files/paper/2024/hash/4410c0711e9154a7a2d26f9b3816d1ef-Abstract-Conference.html — WebArena environment and original scoped results.
- [11] https://arxiv.org/abs/2412.05467 — BrowserGym unified web-agent environment framework.
- [12] https://papers.nips.cc/paper_files/paper/2024/hash/5d413e48f84dc61244b6be550f1cd8f5-Abstract-Datasets_and_Benchmarks_Track.html — OSWorld real-computer environment benchmark.
- [13] https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark — ToolSandbox stateful interactive environment.
- [14] https://openreview.net/pdf?id=roNSXZpUDN — τ-bench tools, users, policies, database-state grading and consistency.
- [15] https://arxiv.org/abs/2308.03688 — AgentBench multi-environment agent evaluation and failures.
- [16] https://arxiv.org/abs/2401.13178 — AgentBoard fine-grained progress evaluation.
- [17] https://openreview.net/pdf?id=fibxvahvs3 — GAIA general-assistant benchmark and original human/model result.
- [18] https://www.swebench.com/verified.html — SWE-bench Verified human-validated task set and benchmark ecosystem.
- [19] https://openreview.net/pdf/b533993ef9bc8320779646b1c475e47635dd98c2.pdf — TheAgentCompany workplace environment and baseline.
- [20] https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/ — METR task-completion time-horizon method and historical trend.
- [21] https://metr.org/time-horizons/ — Current METR definition, domain and automation caveats.
- [22] https://metr.org/notes/2026-01-22-time-horizon-limitations/ — METR uncertainty and interpretation limitations.
- [23] https://metr.org/blog/2026-1-29-time-horizon-1-1/ — Time Horizon 1.1 task/evaluation update.
- [24] https://metr.org/blog/2026-06-26-gpt-5-6-sol/ — Current example of benchmark exploitation destabilizing a time-horizon estimate.
- [25] https://arxiv.org/abs/2411.15114 — RE-Bench research-engineering tasks and human comparison.
- [26] https://arxiv.org/abs/2606.05342 — SentinelBench time-evolving monitoring environments and metrics.
- [27] https://arxiv.org/abs/2509.21766 — UltraHorizon trajectory scale and failure analysis.
- [28] https://arxiv.org/abs/2604.17283 — HorizonBench evolving-preference state tracking.
- [29] https://arxiv.org/abs/2305.16291 — Voyager curriculum, skill library and embodied lifelong-agent results.
- [30] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ — ReAct interleaved reasoning/action mechanics.
- [31] https://papers.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html — Reflexion feedback and episodic reflective memory.
- [32] https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html — AgentDojo dynamic prompt-injection environment.
- [33] https://arxiv.org/abs/2506.15740 — SHADE-Arena sabotage and monitoring benchmark.
- [34] https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ — OWASP agentic-application risk taxonomy.
- [35] https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10 — NIST AI RMF 1.0.
- [36] https://airc.nist.gov/airmf-resources/airmf/5-sec-core/ — NIST AI RMF Core governance and human-AI responsibility outcomes.
- [37] https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md — Firecracker production isolation and jailer guidance.
- [38] https://gvisor.dev/docs/ — gVisor userspace application-kernel isolation architecture.
- [39] https://github.com/opencontainers/image-spec/blob/main/descriptor.md — OCI descriptor content identity and verification.
- [40] https://slsa.dev/spec/v1.2/ — SLSA source/build provenance and assurance levels.
- [41] https://www.rfc-editor.org/rfc/rfc9110.html — HTTP idempotency and retry semantics.
- [42] https://opentelemetry.io/docs/specs/semconv/ — OpenTelemetry semantic-convention foundation.
- [43] https://www.openpolicyagent.org/docs — OPA policy decision/enforcement separation.
- [44] https://www.openpolicyagent.org/docs/management-decision-logs — OPA decision audit and sensitive-field masking.
- [45] https://developers.openai.com/api/reference/cli/resources/responses/methods/create — Current Responses tool allowlisting, call limits and background controls.
- [46] https://sre.google/sre-book/addressing-cascading-failures/ — Retry, overload and cascading-failure engineering.
- [47] https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html — Queue ownership leases and duplicate-delivery caveat.
- [48] https://kubernetes.io/docs/concepts/workloads/controllers/job/ — Kubernetes Job completion and retry semantics.
- [49] https://arxiv.org/abs/2607.27283 — Methodological controls for attributing long-horizon failure.
- [50] https://osworld-v1.xlang.ai/ — OSWorld project version history and current-version notice.
