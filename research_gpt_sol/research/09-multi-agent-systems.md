# Research: Multi-Agent Systems - Supervisor, Worker, Collaboration, Delegation

**Date researched**: 2026-08-21
**Sources consulted**: 40

## Scope and evidence labels

This brief treats a multi-agent system (MAS) as a compound application in which separately configured agent instances have distinct task state, context, tools, or authority and coordinate toward an outcome. Merely sampling the same prompt several times is an ensemble; chaining several prompts is a workflow. Both can be useful, but neither gains the operational properties of a supervisor-worker or peer collaboration merely by being called "multi-agent." The requested supervisor, worker, collaboration, and delegation concepts are covered across all six dimensions. `[inferred]` marks production guidance derived from the cited evidence. Paper and vendor benchmark numbers retain their original scope and are not assumed to transfer to other models or workloads.

## 1. System Topology & Mechanics

### When multiple agents are justified

Multi-agent architecture creates value through one or more of four mechanisms:

1. **Parallel capacity**: independent workstreams run in separate context windows and reduce critical-path time.
2. **Specialization**: workers have smaller prompts, domain knowledge, tools, models, or permissions.
3. **Context isolation**: verbose exploration stays out of the coordinator's context; workers return bounded artifacts.
4. **Independent perspectives**: candidates, critics, or verifiers reduce dependence on one trajectory when their errors are genuinely diverse.

LangChain's current multi-agent guide recommends these patterns when one agent has too many tools, specialized knowledge requires large focused contexts, work can run in parallel, or sequential constraints must be enforced; it identifies context engineering as the central design problem. [[5]](https://docs.langchain.com/oss/python/langchain/multi-agent/index) Anthropic reports that its production research system benefits most on valuable breadth-first tasks with many independent directions, while tightly dependent tasks and much coding work are weaker fits. [[1]](https://www.anthropic.com/engineering/multi-agent-research-system)

`[inferred]` Start with one agent plus deterministic tools. Introduce another agent only when an evaluation shows that specialization, isolation, parallelism, or independent review improves **verified success per unit cost**, not because organizational role names make a demo appear sophisticated.

### Core topology choices

| Topology | Control owner | Communication | Best fit | Principal weakness |
|---|---|---|---|---|
| Router -> specialist | code or one classification call | one-way transfer | mutually exclusive domains | routing error; little recovery |
| Supervisor -> workers | central supervisor | task/result star | dynamic decomposition and synthesis | supervisor bottleneck and single point of semantic failure |
| Fan-out / gather | deterministic controller or supervisor | one request to many, then reduce | independent research, candidate generation | duplicate work; costly synthesis |
| Sequential pipeline | workflow state machine | typed artifact between stages | known SOP, review/approval chains | accumulated error and latency |
| Handoff / swarm | current agent | peer transfer over shared or filtered context | conversational ownership changes | cycles, goal drift, unclear termination |
| Group chat / blackboard | selector or peer protocol | broadcast/shared state | iterative design or negotiation | context growth, contention, false consensus |
| Debate / ensemble | moderator or voting rule | candidate-critique rounds | ambiguous reasoning with checkable answer | correlated errors and token multiplication |
| Hierarchical tree | supervisors at several levels | parent-child tasks | large decomposable programs | deep delegation, lost constraints, hard cancellation |
| Event choreography | no central semantic supervisor | asynchronous events | independent services with stable contracts | no component has complete global progress view |

Anthropic distinguishes orchestrator-workers, where a central model dynamically creates tasks and synthesizes results, from predefined parallelization and evaluator-optimizer loops. [[2]](https://www.anthropic.com/engineering/building-effective-agents) LangChain similarly distinguishes a stateful supervisor that can call several subagents over multiple turns from a stateless router that classifies once. [[6]](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)

Current framework implementations expose similar semantics with different vocabulary:

- OpenAI's official API quickstart demonstrates a triage agent that hands the current conversation to a language specialist. [[4]](https://platform.openai.com/docs/quickstart/make-your-first-api-request) Current GPT-5.6 guidance also documents a hosted multi-agent beta in which one instance coordinates parallel subagents and synthesizes results; OpenAI recommends explicit concurrency, retry, evidence, and stop limits. [[3]](https://developers.openai.com/api/docs/guides/latest-model)
- Google ADK distinguishes `AgentTool`, where the parent receives the specialist result and retains control, from a sub-agent transfer, where the specialist becomes responsible for responding to the user. [[8]](https://adk.dev/tools-custom/function-tools/) It also supplies deterministic `SequentialAgent`, `ParallelAgent`, and `LoopAgent` primitives and documents hierarchical decomposition and evaluator-refiner patterns. [[9]](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
- AutoGen supplies round-robin, model-selected group chat, Magentic-One, and handoff-based Swarm teams, but explicitly recommends optimizing a single agent before adopting the extra scaffolding of a team. [[10]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html)
- CrewAI's hierarchical process uses a manager to allocate tasks and validate results; delegation is disabled by default, and maximum requests per minute and maximum iterations are configurable. [[13]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.0/en/learn/hierarchical-process.mdx)
- Amazon Bedrock Agents Classic implements a synchronous hierarchical supervisor with collaborators. AWS now marks this service as unavailable to new customers after July 30, 2026, so the topology remains instructive but it is not a greenfield recommendation. [[14]](https://docs.aws.amazon.com/en_us/bedrock/latest/userguide/agents-multi-agent-collaboration.html)

LangChain's handoff pattern makes the active agent or stage a persisted state variable updated by tools. Its documentation also requires the model's handoff tool call to be paired with a corresponding tool-result message so downstream history remains valid, and recommends passing filtered context rather than every internal message. [[7]](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)

### Supervisor and worker responsibilities

The supervisor is a **control-plane role**, not the agent with every tool. `[inferred]` It should own:

- authenticated goal and immutable constraints;
- decomposition and dependency graph;
- worker selection against an allowlisted capability registry;
- global token, tool, time, depth, and concurrency budgets;
- task leases, cancellation, progress, and terminal-state accounting;
- conflict detection, aggregation, verification, and user-facing result;
- escalation when no authorized worker or valid synthesis exists.

A worker is a **bounded data-plane role**. `[inferred]` It should accept a typed assignment, operate only within delegated capability and resource scope, return evidence-bearing artifacts or a structured failure, and never silently redefine the parent goal. Workers may create children only when the delegation contract explicitly grants a remaining depth, budget, and capability subset.

Magentic-One is a representative centralized design: an Orchestrator plans, tracks progress, delegates to specialized agents, and replans after errors. [[36]](https://arxiv.org/abs/2411.04468) LangChain's subagent pattern keeps all user interaction and conversation memory in the supervisor while stateless subagents run in isolated contexts and return results. [[6]](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)

`[inferred]` Separate the supervisor's semantic duties from the durable runtime. An LLM can propose tasks and synthesize findings; ordinary code should validate the DAG, enforce authorization and budgets, schedule workers, fence stale attempts, and determine whether all acceptance criteria have been verified.

### Delegation is a typed contract

A natural-language message such as "research this and report back" omits the conditions required for reliable distributed work. A delegation envelope should be immutable and versioned:

```json
{
  "task_id": "task_01J...",
  "run_id": "run_42",
  "parent_task_id": "task_root",
  "plan_generation": 4,
  "assigned_agent": "vendor_risk_researcher:v3",
  "objective": "Assess vendor X's documented data retention",
  "acceptance_criteria": ["primary sources", "effective dates", "unknowns listed"],
  "input_artifacts": [{"id": "brief_7", "sha256": "..."}],
  "constraints": ["no personal data", "read-only web access"],
  "output_schema": "VendorRiskFinding/v2",
  "evidence_policy": "source URL plus retrieved timestamp",
  "capability_grant": ["web.read:allowlisted_domains"],
  "budget": {"input_tokens": 50000, "output_tokens": 6000, "tool_calls": 20},
  "deadline": "2026-08-21T12:00:00Z",
  "delegation_depth_remaining": 0,
  "idempotency_key": "run_42:task_01J...",
  "reply_to": "artifact://run_42/task_root"
}
```

The worker returns one terminal status: `succeeded`, `failed`, `blocked`, `cancelled`, or `expired`, with artifact references, evidence, actual usage, and an error class. "Done" in prose is not a task-state transition.

A2A's current specification demonstrates useful interoperable primitives: Agent Cards advertise identity, skills, interfaces, and authentication requirements; Tasks have IDs and lifecycle state; artifacts and status updates can stream or arrive by webhook. It warns that webhook delivery is at least once and receivers should process duplicates idempotently. [[39]](https://a2a-protocol.org/latest/specification/) Topic 10 covers A2A and MCP in depth; here the relevant lesson is that delegation needs protocol state beyond chat text.

### Collaboration mechanics

**Shared conversation.** Every participant reads prior messages. This makes peer reasoning simple but creates `O(rounds x participants x history)` prompt replay unless compaction or selective views are used. AutoGen's Swarm broadcasts a common context and chooses the next speaker from the latest handoff message; its docs warn that parallel tool calling can generate multiple simultaneous handoffs and recommend disabling it for this pattern. [[12]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html)

**Private context plus artifacts.** Each worker receives only its brief and returns a typed report. This maximizes isolation and parallelism but can withhold context needed to resolve cross-task dependencies. The supervisor must supply explicit shared facts and update briefs when assumptions change.

**Blackboard.** Agents append claims, tasks, artifacts, and reviews to a shared store. `[inferred]` Prefer append-only, namespaced records and deterministic reducers. Do not let several agents overwrite one mutable `answer` field. Claims need author, provenance, confidence meaning, version, and verification state.

**Pipeline.** Roles consume typed outputs in a fixed SOP. MetaGPT encodes software-development SOPs into prompt sequences and assigns roles along an assembly line, explicitly seeking to reduce cascading hallucinations from naive chat chains. [[17]](https://arxiv.org/abs/2308.00352) ChatDev likewise structures design, coding, and testing through a prescribed chat chain. [[18]](https://arxiv.org/abs/2307.07924)

**Dynamic group.** AgentVerse recruits agents, coordinates discussion and execution, and can adjust group composition. [[19]](https://arxiv.org/abs/2308.10848) This flexibility raises additional capability-discovery, identity, budget, and termination requirements.

**Role-playing conversation.** CAMEL's early research uses role prompts to let two communicative agents cooperate without continuous human steering. [[16]](https://arxiv.org/abs/2303.17760) It is useful as a study of conversational cooperation, but role consistency in a benchmark does not provide durable task state, authority boundaries, or recovery semantics.

**Debate and consensus.** Multiagent Debate iterates proposals and critiques before selecting a result and reported gains on its mathematical, strategic, and factuality tasks. [[20]](https://arxiv.org/abs/2305.14325) Mixture-of-Agents instead layers several model outputs into subsequent aggregators; its historical open-model configuration scored 65.1% on AlpacaEval 2.0 versus 57.5% for its cited GPT-4 Omni baseline. [[21]](https://arxiv.org/abs/2406.04692) These are inference ensembles, not evidence that social agreement proves truth.

### Conflict resolution

Conflicts occur at four levels:

1. **Fact conflict**: workers report incompatible claims.
2. **Artifact conflict**: parallel workers edit the same file or record.
3. **Plan conflict**: agents propose mutually exclusive actions or dependencies.
4. **Authority conflict**: an agent attempts an action outside its grant or contradicts policy.

`[inferred]` Resolve them with different mechanisms:

- fact conflict -> retrieve primary evidence, preserve both claims, use an independent verifier;
- artifact conflict -> optimistic version check, three-way merge, owner partitioning, or serial critical section;
- plan conflict -> supervisor evaluates against explicit objective, constraints, cost, and risk;
- authority conflict -> deterministic deny; neither voting nor supervisor prose can override policy.

Voting is useful only when candidate errors are sufficiently independent and the answer is aggregable. Agent Forest found performance scaling from repeated sampling and voting across its tested LLM benchmarks. [[22]](https://arxiv.org/abs/2402.05120) If all agents share a model, prompt, evidence, and first answer, nominally different voters can be strongly correlated. Assigning a "critic" role does not create independence by itself.

### Termination

Termination has two separate conditions:

- **computational termination**: no live, queued, or delegatable work remains, or a hard limit/cancellation stops it;
- **semantic completion**: an independent final verifier confirms the original acceptance criteria.

AutoGen exposes termination on maximum messages, token usage, timeout, handoff, source, function result, external signal, and custom predicates; conditions can be combined with AND/OR and are evaluated after agent responses. [[11]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html) A model emitting `TERMINATE` is a semantic proposal. Pair it with a hard maximum and, for important tasks, an end-state verifier.

`[inferred]` A centralized supervisor can terminate when:

```text
all acceptance criteria verified
AND outstanding_tasks = 0
AND running_leases = 0
AND no required approval is pending
```

It must also end in explicit non-success states when global budget/deadline is exhausted, an unresolvable conflict exists, authorization is denied, or a human cancels. In peer or recursive delegation, use a finite task graph or a runtime accounting protocol: each delegated child increments durable outstanding work, and exactly one accepted terminal result decrements it. Cap delegation depth, total descendants, repeated task fingerprints, rounds without verified progress, and total cost. Never reset a global budget in a child.

## 2. Token Economics & NFR Metrics

### Cost model

Multi-agent cost is additive even when latency is parallel:

```text
C_run = C_supervisor_plan
      + sum_i(C_worker_i)
      + C_inter_agent_messages
      + C_conflict_resolution
      + C_synthesis
      + C_verification
      + C_retries_and_replans
      + C_tools + C_state + C_human_review

C_model_i = (input_tokens_i * input_rate_model_i
           + cached_input_tokens_i * cached_rate_model_i
           + output_tokens_i * output_rate_model_i) / 1,000,000

C_per_1000_verified_runs = 1000 * sum(C_run) / verified_successful_runs
```

Use the current contracted rates for each model tier; public prices and cache discounts change. A strong supervisor with cheaper bounded workers is a common routing hypothesis, but it must be compared with a strong single agent under the **same total token and tool budget**.

Anthropic's production research report provides unusually concrete economics. Its agents used about four times as many tokens as ordinary chat interactions, and its multi-agent system about 15 times as many. On an internal research evaluation, an Opus 4 lead with Sonnet 4 subagents outperformed single-agent Opus 4 by 90.2%. In its BrowseComp analysis, token usage alone explained 80% of performance variance, while token usage, tool calls, and model choice together explained 95%. [[1]](https://www.anthropic.com/engineering/multi-agent-research-system) These vendor-internal findings concern breadth-first research and should not be generalized to transactional workflows.

### Coordination tax and parallel speedup

```text
T_run = T_plan
      + sum(T_sequential_stages)
      + max(T_parallel_worker_critical_paths)
      + T_queue_and_rate_limit
      + T_gather_synthesis_verify
      + T_retry_replan_approval

parallel_efficiency = single_agent_equivalent_work_time
                    / (worker_count * parallel_wall_time)

coordination_overhead_ratio = (supervision + messaging + synthesis tokens)
                           / total tokens
```

The 2025 paper *Towards a Science of Scaling Agent Systems* controlled tools and token budgets over 180 configurations, five topologies, three model families, and four benchmarks. It reported centralized coordination improving parallelizable financial reasoning by 80.9%, decentralized coordination improving dynamic web navigation by 9.2% versus 0.2% for centralized, and all tested MAS variants degrading sequential-reasoning performance by 39% to 70%. It also measured 17.2-fold error amplification for independent agents versus 4.4-fold for centralized coordination, and diminishing or negative coordination returns after the single-agent baseline exceeded roughly 45% in its model. [[23]](https://arxiv.org/abs/2512.08296) These are controlled research findings, not universal constants, but they make task structure a first-class routing signal.

### Capacity and backpressure

If arrival rate is `lambda` runs/second and each run fans out to mean `f` workers, the worker arrival rate is approximately `lambda * f`, before retries. Tail fan-out matters more than the mean: one pathological supervisor can consume the shared quota.

`[inferred]` Enforce hierarchical quotas:

- tenant concurrent runs;
- per-run live workers and descendants;
- provider/model requests and tokens per minute;
- per-tool concurrency and rate;
- worker queue depth and age;
- maximum result bytes and shared-state writes;
- retry and replan budget inherited from the parent.

Use bounded queues and admission control. When capacity is exhausted, reduce fan-out, route to a cheaper/smaller topology, delay background work, or return a structured overload result. Do not allow every worker to independently retry a provider-wide outage.

### Metrics that reveal coordination quality

| Layer | Metrics |
|---|---|
| Outcome | independently verified task success, constraint satisfaction, user correction, harmful-action rate |
| Supervisor | decomposition coverage, assignment precision, invalid/duplicate task rate, synthesis defect rate |
| Worker | task success by capability, evidence completeness, blocked/timeout/retry rate, budget variance |
| Collaboration | messages per useful artifact, conflict rate, ignored-input rate, duplicated work, information-loss rate |
| Termination | premature completion, false completion, loop rate, orphan task count, time after last verified progress |
| Economics | tokens/tools/human minutes per verified success, coordination overhead, cache reuse, wasted cancelled work |
| Reliability | p50/p95/p99 latency, fan-out width, queue age, partial failure, replay duplicate-effect rate |

MultiAgentBench evaluates collaboration and competition with milestone-based KPIs across star, chain, tree, and graph protocols; its paper found topology effects varied by scenario and reported cognitive planning improving milestone achievement by 3% in its setup. [[25]](https://arxiv.org/abs/2503.01935) MAST instead diagnoses failures from execution traces, so pair outcome scores with trajectory-level failure labels. [[24]](https://arxiv.org/abs/2503.13657)

Tau-bench compares final database state with an annotated goal and uses `pass^k` to measure repeated-run consistency; its original retail experiment reported `pass^8` below 25% for leading function-calling agents. [[38]](https://arxiv.org/abs/2406.12045) MAS evaluation should likewise repeat runs because adding routing and speaker selection adds stochastic branch points.

Anthropic's 2026 evaluation guidance recommends combining final-outcome graders, transcript/trajectory analysis, and multiple trials for agents whose actions modify state over many turns. [[26]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) `[inferred]` Compare at least: single strong agent, single agent with the same tools, deterministic workflow, and each proposed MAS topology under equal token/call budgets.

> ⚠️ Limited public data available for this dimension. No vendor-neutral current dataset normalizes end-to-end task success, p95/p99 latency, full token and tool cost, queue behavior, human review, and security outcomes across production supervisor, handoff, swarm, and group-chat systems. Published gains often spend different token budgets and use historical models.

## 3. Distributed Resilience & State

### Control state, message state, and domain state

Keep three stores separate:

1. **Control state**: run/task DAG, assignments, leases, attempts, budgets, deadlines, approvals, terminal status.
2. **Message/artifact state**: immutable delegation envelopes, status events, reports, evidence, versions, provenance.
3. **Domain state**: orders, incidents, code, documents, or other systems of record changed through tools.

The conversation transcript is not the authoritative task ledger. `[inferred]` A minimum schema includes `run_id`, `task_id`, `parent_task_id`, `plan_generation`, `agent_id/version`, `status`, `attempt`, `lease_owner/expiry`, `input/output artifact IDs`, `idempotency_key`, actual usage, and the external operation IDs for side effects.

LangGraph checkpoints graph state at super-step boundaries and persists completed node writes during a partially failed parallel super-step, so successful branches need not be recomputed on resume. Nodes after a checkpoint can re-execute, including model and API calls. [[28]](https://docs.langchain.com/oss/python/langgraph/persistence) Its subgraph docs distinguish per-invocation isolated state, per-thread accumulated state, and stateless execution; parallel calls to the same per-thread subgraph can conflict in one checkpoint namespace. [[27]](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)

`[inferred]` Default workers to per-assignment state. Give a worker long-lived memory only when the business case requires it, because shared or accumulated context couples tasks, complicates concurrent invocation, expands attack persistence, and makes replay less reproducible.

### Durable supervisor-worker execution

A robust runtime uses a durable orchestrator or equivalent event-sourced state machine:

```text
supervisor publishes task T with idempotency key
 -> queue delivers at least once
 -> worker claims lease for (T, attempt)
 -> worker checkpoints internal progress and emits heartbeat
 -> external side effect uses stable operation key
 -> worker commits terminal result + artifact in one durable protocol
 -> supervisor accepts only current generation/attempt
 -> synthesis begins after required join condition
```

Temporal persists workflow history and resumes executions after process or infrastructure failure. It recommends placing failure-prone, nondeterministic API and LLM calls in Activities while keeping Workflow code deterministic. [[29]](https://docs.temporal.io/) Temporal Activities retry by default; the documented default starts at one second, uses a 2.0 backoff coefficient, caps intervals at 100 seconds, and has unlimited attempts unless bounded. [[30]](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx) `[inferred]` Agent work should override unlimited defaults with deadlines, maximum elapsed retry time, non-retryable semantic errors, and stable idempotency.

### Partial failure and late results

Fan-out creates a vector of outcomes, not one success flag. Define the join policy before execution:

- **all**: every required worker must succeed;
- **quorum**: a specified number of independent valid results is enough;
- **best effort**: synthesize available evidence and disclose missing branches;
- **first valid**: cancel remaining workers after an independent verifier passes;
- **deadline**: accept completed valid branches at cutoff and mark the rest expired.

`[inferred]` A timeout is an unknown outcome. Cancellation does not imply a worker stopped or an external action rolled back. Fence late results with `plan_generation` and attempt version; record them for audit but do not let stale work mutate current synthesis. Propagate a parent deadline downward so children cannot outlive the useful result window.

### Shared-state concurrency

Parallel workers should own disjoint artifact paths or append immutable events. When they must update one aggregate:

- use compare-and-swap/version checks for exclusive fields;
- use associative, commutative reducers for unordered parallel contributions;
- sort by stable keys before deterministic synthesis;
- designate one merge owner;
- keep evidence and dissent instead of last-write-wins.

LangGraph warns that non-associative reducers can reconstruct different state depending on write batching, and recommends explicit ordering metadata when deterministic order matters. [[28]](https://docs.langchain.com/oss/python/langgraph/persistence) `[inferred]` Natural-language summaries are not conflict-free replicated data types.

### Retry, circuit breaking, and compensation

Classify failures as transient infrastructure, rate limit, invalid task/input, authorization, worker capability, policy, verifier rejection, conflict, or unknown effect. Retry only transient classes and budget retries globally. Apply circuit breakers and bulkheads per provider, tool, agent type, and tenant so one failing dependency does not consume all workers.

When agents change multiple services, use saga semantics: record each completed local transaction and its compensation; distinguish compensable actions, a pivot/point of no return, and idempotent retryable steps. [[40]](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga) A compensating action can also fail and must be durable. Approval must occur before an irreversible pivot, not after a worker reports completion.

### Termination and recovery invariants

`[inferred]` Assert these invariants continuously:

```text
accepted_terminal_results(task_id) <= 1
sum(child budgets) <= remaining parent budget
child capabilities subset_of parent delegation grant
terminal parent implies no unaccounted live child leases
verified completion implies every required acceptance criterion has evidence
cancelled/expired work cannot publish current-generation domain changes
```

Recovery tests should crash the supervisor before and after task publication, crash workers before and after external effects, duplicate and reorder messages, expire leases, lose a worker result, publish two competing replans, cancel during fan-out, and restore from checkpoints. Verify no duplicated effect, lost task, orphan descendant, budget reset, or false success.

> ⚠️ Limited public data available for this dimension. Multi-agent papers rarely publish recovery-time objectives, recovery-point objectives, duplicate-effect rates, checkpoint growth, task-queue saturation, split-brain supervisor behavior, or multi-region consistency. Those guarantees come from the selected workflow and messaging infrastructure and require fault injection.

## 4. Enterprise Security & Governance

### Delegation attenuates authority

An agent identity, task identity, user identity, and service identity are distinct. `[inferred]` Every worker receives a short-lived capability restricted to the task's tenant, resources, operations, and deadline. It must not inherit the supervisor's broad credential or obtain new permissions by delegating again.

```text
authenticated user intent
 -> policy evaluates supervisor proposal
 -> task-scoped grant issued to named worker/version
 -> worker action re-authorized at tool boundary
 -> high-impact action pauses for exact approval
 -> result/effect written to immutable audit trail
```

A2A requires encrypted production transport, server certificate validation, authentication of every request, and server-side authorization. It explicitly states that entering `TASK_STATE_AUTH_REQUIRED` is not itself authorization and leaves scope, validity, and revocation to the implementation or credential issuer. [[39]](https://a2a-protocol.org/latest/specification/) This is a critical interview distinction: a protocol can convey an authorization request without defining your enterprise authorization policy.

### Inter-agent messages are untrusted inputs

An authenticated agent can still be compromised, hallucinating, overprivileged, or operating on poisoned context. Validate message schema, size, task/run binding, sender identity, nonce/timestamp, plan generation, artifact hashes, content type, and capability scope. Separate natural-language claims from control fields; a message body cannot change routing policy, approval status, or budget.

OWASP's 2026 Agentic Top 10 names insecure inter-agent communication and cascading failures alongside goal hijacking, tool misuse, identity abuse, supply-chain compromise, memory poisoning, and rogue agents. [[31]](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/) AgentDojo contains 97 realistic tasks and 629 security test cases for indirect prompt injection through tool data, demonstrating that even a single agent is difficult to make both useful and secure. [[32]](https://arxiv.org/abs/2406.13352) In MAS, a poisoned source can be summarized by one worker, trusted by the supervisor, copied to the blackboard, and amplified by peers.

`[inferred]` Attach origin and trust labels to every claim and artifact. Preserve source evidence through synthesis. Treat worker prose as untrusted data; use deterministic reference monitors at tool boundaries. A supervisor's agreement does not launder untrusted content into policy.

### Agent registry and supply chain

The registry should bind agent name to owner, version, model/provider, approved prompt and tools, input/output schemas, data classifications, capability ceiling, network policy, deployment digest, evaluation status, and retirement date. Discovering a new Agent Card or tool at runtime does not authorize it.

`[inferred]` Require allowlisted registries, signed artifacts/manifests, pinned versions, dependency scanning, and change approval. Re-evaluate delegation accuracy and security after changing worker descriptions because descriptions directly influence model-based selection in several frameworks. Retire stale capabilities and fail closed when a requested specialist is unavailable.

### Isolation and data minimization

Give each worker the minimum context required. Context isolation reduces both token cost and blast radius. Redact or tokenize PII before delegation where possible, enforce tenant-scoped retrieval, and prevent worker-to-worker raw transcript broadcast unless necessary. Use separate sandboxes and egress policies for code/browser agents; partition file workspaces to avoid covert or accidental cross-task modification.

`[inferred]` Do not use the same shared memory for task artifacts, agent instructions, credentials, and policy. Procedural instructions should be developer-controlled; worker-generated lessons should enter quarantine or review before reuse.

### Collusion, consensus, and human trust

Multiple agreeing agents do not create independent assurance when they share training, prompt, context, or incentives. A malicious or simply persuasive worker can anchor later agents. A moderator can selectively omit dissent. Require evidence-based resolution and independent deterministic checks for consequential decisions.

A 2026 preprint models collaboration as a dependency graph and reports that one injected atomic error can form false consensus; its proposed genealogy-based message layer raised defense success from 0.32 to above 0.89 across its experimental frameworks. [[34]](https://arxiv.org/abs/2603.04474) Treat this as emerging evidence, not a production guarantee. `[inferred]` Preserve message genealogy and let reviewers inspect which claims descend from the same evidence seed.

Human approval is meaningful only when the person sees the exact action, resource, evidence, dissent, risk, and compensation. Never ask for approval of an opaque multi-agent transcript. Invalidate approval if the task arguments, worker identity, plan generation, or external state changes.

### Governance and audit

NIST AI RMF organizes lifecycle risk work into Govern, Map, Measure, and Manage. [[33]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) `[inferred]` For each MAS use case, document why multiple agents are necessary, autonomy level, responsible owner, permitted topologies, maximum delegation depth, data flows, model/vendor inventory, risk tier, verifier, incident response, and kill switch.

Audit events should include run/task/parent IDs, authenticated principal, agent and prompt/model/tool versions, delegation grant, input and output artifact hashes, evidence links, tool arguments/effects, approvals, policy decisions, token/tool usage, retries, handoffs, conflicts, cancellations, and terminal reason. Use append-only tamper-evident storage appropriate to the risk and retention policy; keep secrets and unnecessary private reasoning out of logs.

> ⚠️ Limited public data available for this dimension. Public MAS benchmarks rarely measure cross-agent prompt-injection propagation, identity spoofing, privilege attenuation, insider/compromised worker behavior, collusion, tenant isolation, reviewer error, or compliance audit completeness. Threat modeling and adversarial multi-agent tests are required for each deployment.

## 5. Production Failure Modes

### Empirically observed taxonomy

The MAST study examined five popular MAS frameworks across more than 150 tasks with expert annotation and identified 14 modes in three groups: specification/system design, inter-agent misalignment, and task verification/termination; three-annotator agreement reached Cohen's kappa 0.88. [[24]](https://arxiv.org/abs/2503.13657) Its reported sample included disobeyed task/role specifications, repeated steps, lost history, unrecognized termination, conversation resets, missing clarification, derailment, withheld or ignored information, reasoning-action mismatch, premature termination, and incomplete or incorrect verification. Better role prompts alone did not solve the structural failures.

### Supervisor failures

| Failure | Operational symptom | Containment |
|---|---|---|
| bad decomposition | missing acceptance criterion or invalid dependency | coverage/DAG validation before dispatch |
| wrong worker | task is repeatedly transferred or weakly answered | typed capabilities, routing eval, deterministic fallback |
| supervisor bottleneck | workers idle while plan/synthesis waits | deterministic scheduling, smaller supervisor context, staged reducers |
| synthesis loss | correct worker evidence omitted or distorted | structured artifacts, evidence-preserving merge, final verifier |
| recursive explosion | descendants grow faster than completion | global descendant/depth/concurrency budget |
| premature success | supervisor interprets fluent partial report as done | authoritative task ledger and end-state oracle |
| single semantic point of failure | one wrong supervisor premise affects all workers | validate assumptions, independent verifier, allow dissent |

### Worker and delegation failures

- ambiguous objective or missing inputs;
- worker accepts a task outside its capability;
- constraints disappear at the second delegation hop;
- input summary loses evidence or changes meaning;
- worker returns prose that does not match the output schema;
- duplicate workers repeat identical searches;
- hidden worker failure is summarized as success;
- child retries or delegates after the parent deadline;
- worker changes shared state beyond its owned partition;
- worker withholds uncertainty or inconvenient evidence.

ReDel, a recursive MAS toolkit, reports delegation loops among its common failures, illustrating why recursion needs explicit depth and task budgets. [[35]](https://aclanthology.org/2024.emnlp-demo.17.pdf) `[inferred]` Require workers to reject invalid assignments explicitly and return `blocked` with missing prerequisites rather than improvise authority or facts.

### Collaboration and conflict failures

- **echo chamber/groupthink**: later agents imitate the first answer;
- **false consensus**: repeated text is mistaken for independent evidence;
- **broadcast flooding**: shared history consumes context and attention;
- **blackboard poisoning**: one unverified write contaminates all workers;
- **ignored dissent**: reducer optimizes agreement instead of correctness;
- **edit collision**: parallel workers overwrite or create inconsistent artifacts;
- **speaker starvation**: selector repeatedly chooses one agent;
- **handoff ping-pong**: peers transfer control without progress;
- **role drift**: workers solve adjacent tasks and leave their own incomplete;
- **coordination over reasoning**: tokens are spent discussing rather than using tools.

The controlled scaling study found topology-dependent error amplification and negative returns on sequential tasks. [[23]](https://arxiv.org/abs/2512.08296) `[inferred]` Detect lack of progress using task/artifact state changes and verifier improvement, not message count. Hash semantically equivalent assignments/tool arguments to catch repeated work.

### Distributed-system failures

- supervisor publishes a task but crashes before recording it, or records before publish and loses it;
- queue redelivery executes a side effect twice;
- lease expires while a slow worker is still active;
- cancelled old-generation result wins a last-write race;
- provider outage triggers retry storms from every worker;
- join waits forever for an orphan task;
- partial fan-out is incorrectly marked fully successful;
- concurrent shared-state reducers are non-deterministic;
- child workflow outlives parent and consumes budget;
- compensation fails after several agents already committed effects.

`[inferred]` Use transactional outbox/inbox or equivalent durable publication, idempotent task/effect keys, fencing tokens, bounded retry with jitter, circuit breakers, deadline propagation, dead-letter queues, and reconciliation jobs. A semantic agent layer does not remove ordinary distributed-systems obligations.

### Context and economics failures

Supervisor context grows with every worker report; group chat replays irrelevant turns; workers receive too little context to solve the task; raw reports overflow synthesis; costs continue after the useful deadline; a stronger single agent would have been cheaper; parallel calls saturate quota and worsen p99.

`[inferred]` Pass task-specific briefs and immutable artifact references, summarize only with provenance, cap report schemas, progressively disclose agent capabilities, and expose cost/latency to topology routing. Abort remaining candidates after a verified first-valid result where safe.

### Termination failures

- infinite delegation or handoff cycle;
- evaluator and worker alternate without measurable improvement;
- task counter leaks because a worker fails before terminal event;
- supervisor stops because one agent said `DONE`;
- hard maximum stops work but API reports success;
- all workers finish, but original acceptance criteria were never covered;
- blocked approval is mistaken for completed work.

AutoGen warns that a team without a termination condition or turn maximum can run indefinitely. [[11]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html) `[inferred]` Distinguish `completed`, `failed`, `blocked`, `cancelled`, `expired`, and `budget_exhausted` at every task and run level. Always couple a semantic success check with hard ceilings.

### Domain-specific evidence

MASAI assigned software-engineering subagents distinct objectives and strategies and reported a 28.33% resolution rate on the historical 300-issue SWE-bench Lite. [[37]](https://arxiv.org/abs/2406.11638) The result supports modular specialization in that setup, not unrestricted autonomous software changes. AutoGen's original paper demonstrated flexible conversational patterns across coding, math, QA, operations research, and other examples, but did not establish one topology as universally reliable. [[15]](https://arxiv.org/abs/2308.08155)

> ⚠️ Limited public data available for this dimension. Normalized production incident frequencies for lost delegation, false consensus, orphan workers, duplicated side effects, privilege propagation, and runaway cost are not publicly available. Instrument the failure taxonomy directly rather than inferring health from HTTP/model-call success.

## 6. Enterprise System Design Scenarios

### Scenario A: Enterprise research and due diligence

**Goal:** assess a company, regulation, or technical market from many independent primary sources.

`[inferred]` Use a central supervisor with a deterministic fan-out limit. Partition by non-overlapping evidence dimension, geography, entity, or source class. Each worker returns typed claims, dates, quotations within copyright limits, URLs, and gaps. A separate verifier checks citation entailment and contradictions; the synthesizer cannot discard unresolved dissent. Use one worker for a narrow lookup, two to four for comparisons, and larger fan-out only after measured marginal value. This matches the breadth-first conditions in Anthropic's case study but must be gated by the roughly 15-times-chat token economics it reports. [[1]](https://www.anthropic.com/engineering/multi-agent-research-system)

**Key metrics:** claim/citation correctness, coverage, unique useful sources per worker, duplicated search, contradiction resolution, token/tool cost per verified claim, and time to first/complete report.

### Scenario B: Customer support triage and transaction specialists

**Goal:** route a conversation among identity, billing, technical support, and retention while safely executing account actions.

`[inferred]` Use deterministic intent/policy routing where categories are stable; use a handoff only when a specialist needs to own several user turns. Persist `active_agent` and stage outside model text. Pass authenticated account facts through application context, not summaries. Each specialist has disjoint tools and least privilege. Refund, cancellation, or account changes require current policy, exact parameters, and approval where appropriate. Terminate on verified database state or a clear user-facing blocked/escalated state.

**Key metrics:** routing precision, transfer count, repeated questions, context-loss rate, policy violations, resolution/containment, action reversal, p95 latency, and cost per resolved case.

### Scenario C: Software change and incident response

**Goal:** diagnose an issue, modify code/configuration, verify it, and stage a rollout.

`[inferred]` Use an orchestrator with repository mapper, investigator, implementer, test/security reviewer, and deployment verifier only when work can be partitioned. Give parallel workers separate branches/worktrees or read-only access; one merge owner resolves changes. Tests, static analysis, policy, and canary telemetry are authoritative. A failed verifier creates a bounded repair task, not an unrestricted group chat. Production credentials stay outside coding agents; the rollout is an irreversible/pivot step with human approval and compensation.

**Key metrics:** verified issue resolution, escaped regression, edit conflicts, duplicated exploration, test coverage added, time to diagnosis, repair rounds, canary rollback, and cost per merged fix.

### Scenario D: Financial reconciliation or regulated case workflow

**Goal:** combine ledger, document, policy, and exception analysis into an auditable recommendation.

`[inferred]` Prefer a deterministic workflow with specialist model nodes rather than a free-form swarm. Partition records by stable key, use typed monetary values, and reconcile totals with code. A policy agent can retrieve and cite rules but cannot authorize exceptions. A supervisor gathers evidence; a deterministic rules service and accountable human own material decisions. Preserve every worker artifact and version. New documents create a new plan generation and invalidate affected approvals.

**Key metrics:** reconciliation exactness, unexplained variance, policy citation accuracy, false escalation, human review time, evidence completeness, duplicate transaction rate, and decision reproducibility.

### Scenario E: Cross-organization agent delegation

**Goal:** a procurement agent requests compliance evidence from vendor-operated remote agents.

`[inferred]` Use A2A-style discovery and task lifecycle, but allowlist vendors and pin card/version metadata. Authenticate both sides with enterprise identity, authorize every requested skill, minimize disclosed data, and issue task-scoped credentials. Store messages and artifacts with signatures/hashes and correlation IDs. Treat remote claims as evidence submissions, never verified truth. Use deadlines, at-least-once deduplication, revocation, contractual SLAs, and a human path for authorization or dispute.

**Key metrics:** authenticated-task success, schema/version mismatch, duplicate delivery, authorization challenges, evidence rejection, cross-boundary latency, vendor error budget, and audit completeness.

### Topology decision matrix

| Task property | Preferred starting design | Avoid |
|---|---|---|
| one clear tool path | single agent or deterministic code | multi-agent role play |
| mutually exclusive request domains | router or stateful handoff | all-agent broadcast |
| many independent evidence branches | bounded supervisor fan-out/gather | sequential group chat |
| high dependency density | deterministic DAG/pipeline | independent swarm |
| shared mutable artifact | one owner plus reviewers | parallel uncoordinated writes |
| checkable candidate answer | diverse ensemble + independent verifier | consensus as proof |
| changing conversational owner | persisted handoff state | manager re-summarizing every turn |
| cross-enterprise delegation | authenticated task protocol + capability policy | raw chat/webhook trust |
| irreversible/high-impact action | deterministic policy + human approval | peer vote or self-authorization |

### Capacity-planning checklist

`[inferred]`

1. Forecast runs/second by tenant and task class.
2. Measure fan-out and descendant distributions, not only averages.
3. Convert each topology into model RPM/TPM, tool concurrency, queue, storage, and egress demand.
4. Reserve supervisor/synthesis capacity so workers cannot starve the control plane.
5. Bound worker result bytes and checkpoint/artifact retention.
6. Load-test provider rate limits, partial fan-out, cancellation, and retry storms.
7. Size human approval/escalation queues; automation can move the bottleneck to reviewers.
8. Route overload to smaller topologies with honest partial/queued status.

### Interview-ready design questions

1. What measured limitation of a single agent requires multiple agents?
2. Is the task parallel, sequential, interactive, or conflict-heavy?
3. Who owns the final answer and the authoritative task ledger?
4. What exact contract crosses each delegation boundary?
5. Which context, tools, data, and permissions does each worker receive?
6. How are worker outputs verified and conflicting claims resolved?
7. What are semantic and hard termination conditions?
8. How are global cost, depth, concurrency, and retries inherited by children?
9. How does the system recover from duplicate delivery, stale workers, and supervisor failure?
10. How are inter-agent identity, message integrity, least privilege, and audit enforced?
11. What single-agent and deterministic baselines prove the coordination tax is worthwhile?
12. Which outcome, trajectory, security, cost, and tail-latency metrics gate production?

The principal-architect answer is conditional: multi-agent systems are valuable when task structure exposes parallel or specialized work and the runtime can enforce delegation, evidence, state, authority, budgets, and termination. Adding agents without those controls multiplies stochastic decisions, context, cost, and propagation paths faster than it multiplies truth.

## Sources

1. Anthropic, [How We Built Our Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system).
2. Anthropic, [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents).
3. OpenAI, [Model Guidance - Multi-Agent Beta and Tool Orchestration](https://developers.openai.com/api/docs/guides/latest-model).
4. OpenAI, [Developer Quickstart - Build Agents and Handoffs](https://platform.openai.com/docs/quickstart/make-your-first-api-request).
5. LangChain, [Multi-Agent Patterns](https://docs.langchain.com/oss/python/langchain/multi-agent/index).
6. LangChain, [Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents).
7. LangChain, [Handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs).
8. Google ADK, [Agent as a Tool](https://adk.dev/tools-custom/function-tools/).
9. Google Developers, [Developer's Guide to Multi-Agent Patterns in ADK](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/).
10. Microsoft AutoGen, [Teams](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html).
11. Microsoft AutoGen, [Termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html).
12. Microsoft AutoGen, [Swarm](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html).
13. CrewAI, [Hierarchical Process](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.0/en/learn/hierarchical-process.mdx).
14. AWS, [Use Multi-Agent Collaboration with Amazon Bedrock Agents](https://docs.aws.amazon.com/en_us/bedrock/latest/userguide/agents-multi-agent-collaboration.html).
15. Wu et al., [AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation](https://arxiv.org/abs/2308.08155).
16. Li et al., [CAMEL: Communicative Agents for Mind Exploration of Large Scale Model Society](https://arxiv.org/abs/2303.17760).
17. Hong et al., [MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework](https://arxiv.org/abs/2308.00352).
18. Qian et al., [ChatDev: Communicative Agents for Software Development](https://arxiv.org/abs/2307.07924).
19. Chen et al., [AgentVerse: Facilitating Multi-Agent Collaboration and Exploring Emergent Behaviors](https://arxiv.org/abs/2308.10848).
20. Du et al., [Improving Factuality and Reasoning in Language Models through Multiagent Debate](https://arxiv.org/abs/2305.14325).
21. Wang et al., [Mixture-of-Agents Enhances Large Language Model Capabilities](https://arxiv.org/abs/2406.04692).
22. Li et al., [More Agents Is All You Need](https://arxiv.org/abs/2402.05120).
23. Kim et al., [Towards a Science of Scaling Agent Systems](https://arxiv.org/abs/2512.08296).
24. Cemri et al., [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657).
25. Zhu et al., [MultiAgentBench: Evaluating the Collaboration and Competition of LLM Agents](https://arxiv.org/abs/2503.01935).
26. Anthropic, [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
27. LangChain, [LangGraph Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs).
28. LangChain, [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
29. Temporal, [Temporal Platform Documentation](https://docs.temporal.io/).
30. Temporal, [Retry Policies](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx).
31. OWASP GenAI Security Project, [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/).
32. Debenedetti et al., [AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents](https://arxiv.org/abs/2406.13352).
33. NIST, [Artificial Intelligence Risk Management Framework (AI RMF 1.0)](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10).
34. Xie et al., [From Spark to Fire: Modeling and Mitigating Error Cascades in LLM-Based Multi-Agent Collaboration](https://arxiv.org/abs/2603.04474).
35. Menz et al., [ReDel: A Toolkit for LLM-Powered Recursive Multi-Agent Systems](https://aclanthology.org/2024.emnlp-demo.17/).
36. Fourney et al., [Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks](https://arxiv.org/abs/2411.04468).
37. Arora et al., [MASAI: Modular Architecture for Software-Engineering AI Agents](https://arxiv.org/abs/2406.11638).
38. Yao et al., [Tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/abs/2406.12045).
39. Linux Foundation A2A Project, [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/).
40. Microsoft Azure Architecture Center, [Saga Distributed Transactions Pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga).
