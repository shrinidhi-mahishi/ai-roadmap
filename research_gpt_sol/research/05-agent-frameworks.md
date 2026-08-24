# Research: Agent Frameworks - LangGraph, OpenAI Agents SDK, Google ADK, CrewAI

**Date researched**: 2026-08-21
**Sources consulted**: 47

## Scope and evidence labels

This brief compares the current documented architecture of LangGraph, OpenAI Agents SDK, Google Agent Development Kit (ADK), and CrewAI. It covers their open-source libraries separately from optional hosted control planes. Plain factual statements are sourced from first-party documentation, specifications, repositories, or a primary paper. `[inferred]` marks a design recommendation or conclusion derived from those sources. Features and versions move quickly; verify the pinned release and deployment tier before procurement or implementation.

## 1. System Topology & Mechanics

### Compare control models, not feature checklists

All four frameworks can call models and tools, but their primary abstractions allocate control differently:

| Framework | Primary abstraction | Default control model | State center | Managed production path |
|---|---|---|---|---|
| LangGraph | nodes, edges, shared state, reducers; graph and functional APIs | application-defined graph with optional model routing | checkpointed thread state plus cross-thread store | LangSmith Deployment or self-managed Agent Server |
| OpenAI Agents SDK | `Agent`, `Runner`, tools, handoffs, guardrails | SDK-owned model/tool/handoff turn loop; code can orchestrate outside it | run items / resumable `RunState` and optional session history | no required deployment platform; external durable integrations documented |
| Google ADK | `Agent`/node, `Workflow`, `Runner`, `Event`, session/memory/artifact services | ADK 2.0 graph or dynamic workflows plus collaborative agents | append-only session events and state deltas via services | Agent Runtime, Cloud Run, GKE, or another container host |
| CrewAI | role-based Agent/Task/Crew and event-driven Flow | Crew for autonomous collaboration; Flow for deterministic routing | Flow state and optional persistence/checkpoints | CrewAI AMP or self-hosted Python deployment |

LangGraph describes itself as a low-level orchestration runtime focused on durable execution, streaming, human-in-the-loop, and persistence; it deliberately does not abstract prompts or agent architecture. Its graph API uses nodes for work, edges for routing, state schemas for shared data, and reducers for merging updates. [[1]](https://docs.langchain.com/oss/python/langgraph/overview) [[2]](https://docs.langchain.com/oss/python/langgraph/use-graph-api)

OpenAI Agents SDK defines an agent as a model configured with instructions, tools, handoffs, guardrails, and structured output. `Runner` owns the loop: call the active model, terminate on final output, change active agent on a handoff, or execute tool calls and loop. Code-based orchestration remains available outside that loop. [[10]](https://openai.github.io/openai-agents-python/agents/) [[11]](https://openai.github.io/openai-agents-python/running_agents/) [[12]](https://openai.github.io/openai-agents-python/multi_agent/)

Google ADK 2.0 changed the Python and Go architecture from a hierarchical agent executor to a graph-based Workflow Runtime in which agents, tools, and functions are nodes. Python 2.0 became generally available on May 19, 2026 and Go 2.0 on June 30, 2026; the migration adds event fields and changes extension points. [[19]](https://adk.dev/2.0/) ADK supports graph workflows for explicit routing, dynamic workflows for code-driven control, collaborative coordinator/subagent workflows, and older template sequential/parallel/loop workflows. [[20]](https://adk.dev/workflows/) [[21]](https://adk.dev/graphs/)

CrewAI divides **Crews**, autonomous role-based teams of agents and tasks, from **Flows**, event-driven application control using start/listen/router steps and state. CrewAI's own production guidance recommends starting with a Flow and using Crews for bounded pockets of complex autonomous work. [[29]](https://docs.crewai.com/core-concepts/Agents) [[32]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx)

`[inferred]` Do not ask which framework “has agents.” Ask who owns the next transition, what data is authoritative, what is checkpointed, and how an external side effect is recovered after a crash.

### Framework topology in one diagram

```text
                         DOMAIN CONTROL PLANE (owned by the application)
      identity | policy | tool registry | prompt/model versions | budgets | eval gates
                                            |
                                            v
LANGGRAPH              OPENAI AGENTS SDK       GOOGLE ADK             CREWAI
StateGraph/entrypoint   Agent + Runner loop     Workflow + Runner       Flow decorators
 nodes/edges/reducers   tools + handoffs        Nodes + Event stream    start/listen/router
        |                       |               agents/functions/tools       |
 checkpointer/store      Session / RunState     Session/Memory/Artifact   Flow persistence
        |                       |                    Services                 |
 LangSmith/AgentServer   app or durable adapter Agent Runtime/app host  AMP/app host
                                            |
                                            v
                 TOOL GATEWAY -> APIs / browser / code / queues / humans
                                            |
                              effect ledger and business systems
```

The bottom effect ledger is intentionally outside every framework. `[inferred]` Framework state is orchestration state; the database, payment provider, ticket system, or other domain service remains authoritative for business effects.

### LangGraph mechanics

- `StateGraph` makes shared state and transition topology explicit. Reducers control concurrent state updates, while conditional edges can route from deterministic or model output. [[2]](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- Checkpointers save state snapshots by thread and step. Stores hold application-defined, cross-thread data. Pending writes retain successful node outputs when another node in the same super-step fails. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence)
- Interrupts durably pause and surface a payload; resuming restarts the interrupted node from its beginning, which is why side effects before the interrupt must be idempotent. [[5]](https://docs.langchain.com/oss/python/langgraph/interrupts)
- The functional API persists task results while allowing ordinary control flow. Its documentation says API calls belong inside tasks and still need idempotency because a task that starts but does not complete can run again. [[4]](https://docs.langchain.com/oss/python/langgraph/functional-api)

```python
builder = StateGraph(OrderState)
builder.add_node("propose", propose_resolution)
builder.add_node("approve", approval_node)
builder.add_node("execute", idempotent_execute)
builder.add_conditional_edges("propose", route_by_risk)
graph = builder.compile(checkpointer=durable_checkpointer)
```

`[inferred]` LangGraph is the strongest fit of these four when a team wants to model and inspect exact transition topology, reducers, checkpoints, and branches. That control also means the team owns more graph design, state schema, side-effect discipline, and deployment decisions.

### OpenAI Agents SDK mechanics

- Two distinct multi-agent semantics matter. With `Agent.as_tool()`, a manager calls a specialist for a bounded result and retains the conversation. With a handoff, the specialist becomes the active agent and receives conversation history. [[12]](https://openai.github.io/openai-agents-python/multi_agent/) [[17]](https://openai.github.io/openai-agents-python/handoffs/)
- The SDK's loop has typed exceptions for maximum turns, model timeouts, tool timeouts, malformed model behavior, and guardrail tripwires. `max_turns=None` disables the turn limit, so production code should retain independent turn, time, token, and cost budgets. [[11]](https://openai.github.io/openai-agents-python/running_agents/)
- Sessions automatically maintain conversation history across runs. A session cannot be combined in the same run with `conversation_id`, `previous_response_id`, or automatic previous-response continuation; choose one state mechanism. [[13]](https://openai.github.io/openai-agents-python/sessions/)
- Tool approvals produce interruptions, and `RunState` can be serialized and resumed after approve/reject decisions. The interruption propagates to the outer run even for nested agent-as-tool calls. [[14]](https://openai.github.io/openai-agents-python/human_in_the_loop/)

```python
triage = Agent(name="Triage", handoffs=[billing, support])
result = await Runner.run(
    triage,
    user_input,
    max_turns=8,
    session=session,
)
```

The default path uses OpenAI Responses models, but the SDK exposes per-agent/per-run provider interfaces and best-effort beta third-party adapters. Its documentation warns that tool, structured-output, multimodal, and usage behavior varies by provider. [[16]](https://openai.github.io/openai-agents-python/models/)

`[inferred]` This SDK is the shortest path when the desired abstraction is an OpenAI-first tool/handoff loop with integrated approvals and traces. It is not, by itself, a general durable scheduler: the documentation points long-running recovery to Dapr, Temporal, Restate, or DBOS integrations. [[11]](https://openai.github.io/openai-agents-python/running_agents/)

### Google ADK mechanics

- ADK's `Runner` drives an asynchronous stream of `Event` objects. The runner appends the user event, consumes the agent/workflow generator, commits each event and its state changes through the session service, and yields it to the caller. Blocking synchronous work can still stall an execution thread or event loop. [[22]](https://adk.dev/runtime/event-loop/)
- ADK 2.0 `Workflow` graphs compose agent nodes, code functions, tools, human input, branches, joins, and loops. Typed node output can flow directly to the next node instead of using shared session state. Current documented limitations include no live streaming for graph workflows and incomplete compatibility with some third-party integrations. [[21]](https://adk.dev/graphs/)
- Collaborative task/single-turn agents isolate their branch context. Parallel peers do not see each other's in-progress events; the parent receives results after the branches complete. [[38]](https://adk.dev/workflows/collaboration/)
- Session contains chronological events and state for a conversation; Memory is searchable across sessions; ArtifactService manages named, versioned binary data separately from session state. [[23]](https://adk.dev/sessions/) [[39]](https://adk.dev/artifacts/)

```python
root_agent = Workflow(
    name="order_workflow",
    edges=[
        ("START", classify_agent, policy_function, approval_node, execute_agent)
    ],
)
```

`[inferred]` ADK is a strong fit for teams aligned with Google Cloud, Gemini, and an event/service-based runtime, while its open-source repository describes it as model- and deployment-agnostic. Teams must distinguish ADK 2.0 Python/Go graph behavior from 1.x or other-language examples because feature parity is not uniform. [[19]](https://adk.dev/2.0/) [[28]](https://github.com/google/adk-python)

### CrewAI mechanics

- CrewAI Agents have role, goal, backstory, tools, delegation, model, iteration, rate, execution-time, cache, and callback controls. Current docs list a default `max_iter` of 20 and recommend dedicated sandbox services rather than deprecated built-in code-execution switches. [[30]](https://github.com/crewaiinc/crewai/blob/main/docs/en/concepts/agents.mdx)
- Crews organize agents and tasks under processes; Flows use explicit event routing and can invoke a Crew inside a step. The official production architecture places the Flow around autonomous Crew work. [[29]](https://docs.crewai.com/core-concepts/Agents) [[32]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx)
- Flow state may be a flexible dictionary or a Pydantic model. `@persist` can save every method or selected methods; the documented default is SQLite persistence. Restore/fork behavior is keyed by state IDs, and a missing restore ID can fall back silently rather than raise. [[31]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx)
- `@human_feedback` can pause a Flow and optionally use an LLM to collapse free-form feedback into one of configured routing outcomes. For high-impact approval, `[inferred]` preserve the human's raw decision and bind it to the exact command rather than trusting an LLM-classified approval label. [[33]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.14.7/en/concepts/flows.mdx)

```python
@persist()
class OrderFlow(Flow[OrderState]):
    @start()
    def validate(self): ...

    @listen(validate)
    def run_resolution_crew(self, validated): ...

    @router(run_resolution_crew)
    def route_by_risk(self, proposal): ...
```

`[inferred]` CrewAI provides the highest-level role/team vocabulary of the four. It fits rapid multi-agent workflow development, but production work should wrap Crews in typed Flows, bound autonomy, and make state/effect semantics explicit rather than relying on role prose.

### Open-source versus hosted layers

The four core repositories use permissive licenses: LangGraph, OpenAI Agents SDK, and CrewAI are MIT-licensed; Google ADK Python is Apache-2.0. [[9]](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) [[18]](https://github.com/openai/openai-agents-python/blob/main/LICENSE) [[28]](https://github.com/google/adk-python) [[34]](https://github.com/crewaiinc/crewai)

Those licenses do not make hosted capabilities free or portable. LangSmith Deployment offers managed cloud, standalone Agent Server, and enterprise self-hosted modes; CrewAI AMP adds managed deployment, API access, monitoring, and tooling; Google documents Agent Runtime, Cloud Run, GKE, and generic containers; OpenAI Agents SDK can run in an application and documents external durable-runtime integrations. [[6]](https://docs.langchain.com/langsmith/deployment) [[26]](https://adk.dev/deploy/) [[35]](https://docs.crewai.com/enterprise/introduction) [[11]](https://openai.github.io/openai-agents-python/running_agents/)

`[inferred]` Evaluate library license, hosted control plane, telemetry backend, model/tool services, and operational support as separate products.

## 2. Token Economics & NFR Metrics

### The framework is rarely the main inference cost

The open-source libraries do not eliminate provider, tool, storage, tracing, or compute charges. A comparable run-cost equation is:

```text
run_cost = Σ model_call_cost
         + Σ tool/sandbox/search cost
         + checkpoint and artifact I/O
         + queue/worker/control-plane compute
         + trace ingestion and retention
         + expected human review and repair

cost_per_success = total production-shaped cost / verified successful runs
```

`[inferred]` Framework choice changes the multipliers: number of model calls, how much history each handoff/branch repeats, checkpoint frequency, trace volume, and how many retries or evaluator passes occur. A role-based five-agent Crew may be more expensive than one bounded OpenAI agent; a broad LangGraph or ADK fan-out may be more expensive still. The diagram, not the package name, determines the trajectory.

### Framework-specific cost levers

| Framework | Primary levers | Cost trap |
|---|---|---|
| LangGraph | node count, parallel super-steps, state projection, checkpoint frequency, delta storage, model routing | full growing state/checkpoints and accidental branch fan-out |
| OpenAI Agents SDK | turns, handoffs versus agents-as-tools, model per agent, hosted tool use, session history, trace content | nested specialist calls and full-history handoffs hidden behind a short API |
| Google ADK | workflow nodes, branch isolation/context, event volume, service backend, artifact loads, model per agent | large event histories and duplicate state/artifact/context representation |
| CrewAI | agents/tasks, delegation, planning/reasoning attempts, `max_iter`, Flow persistence, Crew/Flow trace events | autonomous collaboration and retries multiplying calls without a task-success gain |

LangGraph documents that full channel values are checkpointed at every super-step by default; its beta `DeltaChannel` reduces storage for append-heavy state at a storage-versus-read tradeoff. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence) OpenAI's provider adapters may fail to return complete usage unless explicitly configured and validated. [[16]](https://openai.github.io/openai-agents-python/models/) ADK plugins can collect token use, execution time, invocation counts, and caching behavior across a Runner. [[25]](https://adk.dev/plugins/) CrewAI exposes `max_iter`, execution time, rate limits, retry limits, and caching controls per Agent. [[30]](https://github.com/crewaiinc/crewai/blob/main/docs/en/concepts/agents.mdx)

### Latency model

```text
serial stage latency = Σ(model + tool + framework hook + persistence)
parallel stage latency = dispatch + max(branch latency) + join + merge
end-to-end latency = queue + critical path + approval wait + recovery work
```

`[inferred]` Measure time to first useful event and p50/p95/p99 complete-run latency separately. Streaming improves perceived responsiveness but does not reduce the time or cost required to verify the final business result. ADK's async-first runtime, OpenAI's streamed Runner, LangGraph streaming, and CrewAI streaming differ in event schemas and backpressure behavior, so test with slow consumers and disconnects. [[22]](https://adk.dev/runtime/event-loop/) [[11]](https://openai.github.io/openai-agents-python/running_agents/) [[1]](https://docs.langchain.com/oss/python/langgraph/overview)

> ⚠️ Limited public data available for this dimension. No framework owner publishes a stable, audited, apples-to-apples p50/p95/p99, throughput, memory, checkpoint-I/O, or cost-per-success benchmark for the same model, tools, state backend, workflow, deployment region, and version across all four frameworks. Vendor descriptions such as “fast,” “scalable,” or “production ready” are not capacity evidence.

### What the 2026 ADK Arena paper does and does not establish

ADK Arena is a June 2026 preprint that used an LLM developer to generate agents against 51 Python frameworks and four benchmarks. Across 408 generated agents, 232 (57%) passed its three validation levels; per-agent generation cost ranged from $0.60 to $3.40, a 5.6x spread. The paper explicitly treats generation effort as a proxy for API usability and fixes the execution backbone to isolate framework effects. [[36]](https://arxiv.org/abs/2606.05548)

`[inferred]` This is useful evidence that framework APIs and documentation affect automated implementation effort. It is not a production runtime benchmark for a hand-engineered workload, does not establish durability or security, and should not be used to claim that one of these four frameworks has universally lower runtime latency or cost.

### NFR scorecard for a bake-off

Build the same small but realistic workload in each candidate. Pin model/version, tools, datasets, deployment region, instance size, trace policy, and state backend. Report:

| NFR | Required measures |
|---|---|
| Outcome | executable task success, false-success rate, human acceptance, `pass^k` across repeated runs |
| Trajectory | model calls, tools, handoffs/delegations, node transitions, retries, no-progress exits |
| Performance | p50/p95/p99 run and node latency, queue wait, first event, stream completion, cold start |
| Economics | tokens and currency per run/success, checkpoint/artifact bytes, trace bytes, human minutes |
| Recovery | resume success at every crash point, duplicate effects, recovery time, compensation success |
| State | concurrent-update conflicts, checkpoint growth, schema-migration success, restore correctness |
| Security | unauthorized-action rate, injection attack success, approval binding, tenant-isolation tests |
| Operability | deploy/rollback time, stuck-run detection, alert coverage, trace completeness, on-call repair time |

`[inferred]` Run a framework upgrade as part of the bake-off. Migration cost and semantic drift are often more important than a few milliseconds of local dispatch overhead.

## 3. Distributed Resilience & State

### “Memory,” “session,” “checkpoint,” and “durability” are not synonyms

| Capability | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---|---|---|---|---|
| Conversation continuity | thread/messages | Session or server continuation | Session events/state | Crew memory or Flow state |
| Step state | graph checkpoint | in-run items / serializable `RunState` | Event and state deltas | Flow state / checkpoint |
| Long-term memory | Store | custom Session or application service | MemoryService | CrewAI memory/knowledge integrations |
| Large artifacts | external store referenced by state | files/application storage | versioned ArtifactService | application/tool storage |
| Crash-resumable orchestration | native checkpointer semantics | use documented durable-runtime integration for long waits/restarts | resumable events/workflow features | Flow persistence/checkpoint facilities |

`[inferred]` None of these rows alone guarantees exactly-once external mutation. The application still needs idempotency keys, a durable effect ledger, destination reconciliation, and compensation.

### LangGraph recovery boundary

LangGraph checkpoints state at super-step boundaries and persists successful per-task writes. Replay from a prior checkpoint re-executes later nodes, including LLM calls, API calls, and interrupts. The functional API retrieves committed task results but documents that an incomplete task can run again. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence) [[4]](https://docs.langchain.com/oss/python/langgraph/functional-api)

`[inferred]` Put each non-idempotent effect behind a task that first records a canonical intent and idempotency key in a domain ledger. On resume, query the destination before repeating a dispatched-but-unconfirmed operation. Keep large tool outputs in immutable artifact storage and checkpoint references/digests.

### OpenAI Agents SDK recovery boundary

Sessions persist conversation history, while HITL `RunState` serializes a paused run for approval/resume. The SDK documentation separately lists Dapr, Temporal, Restate, and DBOS integrations for durable, long-running execution. [[13]](https://openai.github.io/openai-agents-python/sessions/) [[14]](https://openai.github.io/openai-agents-python/human_in_the_loop/) [[11]](https://openai.github.io/openai-agents-python/running_agents/)

`[inferred]` A database-backed Session is not a job queue or workflow transaction log. For work that must survive worker loss between tool calls, place the Runner inside a durable workflow/activity boundary, serialize only supported run state, and make tools idempotent. Avoid two independent owners advancing the same session/run.

### Google ADK recovery boundary

ADK's session service owns events and state; persistent service choices determine whether state survives process restarts. In-memory implementations are explicitly for development and lose data on restart. [[23]](https://adk.dev/sessions/) The resume feature logs completed tasks and, after interruption, can skip completed sequential/parallel/loop work while restarting incomplete work. [[40]](https://adk.dev/runtime/resume/)

ADK Rewind restores session-level state and artifacts for a previous invocation while preserving a log of rewound requests. Its documentation states that app/user state is not restored, external dependencies are not restored, and state, artifact, and event updates are not one atomic transaction. [[24]](https://adk.dev/sessions/rewind/)

`[inferred]` Treat Rewind as a conversational/debugging operation, not a distributed rollback. Add application locking around active sessions, record external effect receipts, and use compensating commands for external systems.

### CrewAI recovery boundary

CrewAI Flow `@persist` can save after every method or only selected methods and restore state by ID. The current state guide distinguishes persistence restoration from checkpoint restoration and warns that a missing restore ID can fall back to new/default behavior. [[31]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx)

`[inferred]` Never allow a silent restore miss to start a duplicate business process. Validate the expected run ID in a domain run table before `kickoff`, fail closed if the framework state is absent, and use a production database persistence implementation rather than default local SQLite for replicated workers. Prefer method-level persistence after committed transitions when class-level snapshots can expose intermediate state.

### Common resilient topology

```text
API -> admission/idempotency -> durable run queue -> framework worker
          |                         |                 |
          |                         |                 +-> model/tool calls
          |                         |                 +-> checkpoint/session store
          |                         |                 +-> immutable artifacts
          v                         v
    domain run table <------ effect intent/result ledger ------> business systems
          |
    outbox/status events -> UI / webhook / monitoring / human approval
```

`[inferred]` Use optimistic state versions, one fenced executor per run, bounded retries owned by one layer, dead-letter/manual-repair states, and a terminal-state invariant. During deployment, drain or version-pin in-flight runs rather than resuming old serialized state under new code implicitly.

### Degradation strategy

1. Disable optional evaluator, planning, and parallel branches.
2. Route recognized intents to a deterministic read-only workflow.
3. Convert mutations to drafts requiring later review.
4. Queue resumable work with a status URL and deadline.
5. Fail closed with the run ID and preserved evidence.

`[inferred]` Do not silently swap framework, model provider, or state backend mid-run; create a child run with an explicit migration record.

## 4. Enterprise Security & Governance

### Framework hooks are not authorization systems

The policy sequence is invariant across frameworks:

```text
authenticate user -> derive tenant/purpose/scopes -> propose action
-> validate schema and business rule -> authorize concrete resource
-> approve exact high-risk command -> mint short-lived credential
-> execute -> verify postcondition -> audit
```

`[inferred]` Agent roles, backstories, instructions, graph node names, and Flow labels are not RBAC. A downstream gateway must enforce the actual actor/resource/action decision even if a model, callback, or framework is compromised.

### LangGraph / LangSmith controls

LangSmith Deployment supports custom authentication, encryption, and multi-tenant access control, but its current custom-auth documentation limits the built-in `Auth` integration to LangSmith SaaS or Enterprise self-hosted deployments. It warns that an exposed development server has no authentication by default. [[7]](https://docs.langchain.com/langsmith/set-up-custom-auth)

`[inferred]` Protect standalone Agent Server with an API gateway or service mesh, enforce tenant ownership on thread/run IDs, namespace stores, and never accept a client-supplied thread ID without authorization. Review checkpoint serializers carefully; avoid unsafe pickle fallback for untrusted state.

### OpenAI Agents SDK controls

OpenAI guardrails have specific scope: input guardrails apply to the first agent, output guardrails to the final-output agent, and tool guardrails to each custom function-tool call. Parallel input guardrails may complete after the agent has already consumed tokens or executed a tool; blocking mode prevents that. [[15]](https://openai.github.io/openai-agents-python/guardrails/)

`[inferred]` Use blocking admission checks before effectful work, per-tool authorization immediately before dispatch, and approval for a canonical argument digest. Handoffs must not broaden credentials merely because the active prompt changed. Validate non-OpenAI provider adapters because capability and usage semantics can differ. [[16]](https://openai.github.io/openai-agents-python/models/)

### Google ADK controls

ADK Plugins register global lifecycle hooks on a Runner and execute before local agent callbacks, making them the documented mechanism for cross-cutting policy, logging, metrics, and caching. [[25]](https://adk.dev/plugins/) ADK safety guidance recommends before-tool validation, network controls, sandboxed execution, and escaping model-generated UI content. [[41]](https://adk.dev/safety/)

ADK authentication documentation warns against storing access and especially refresh tokens directly in session state, recommends authentication/secret manager services for production, and supports API key, bearer, OAuth2, OIDC, and service-account schemes for tools. [[42]](https://adk.dev/tools-custom/authentication/)

`[inferred]` Put tenant and subject identity in trusted invocation metadata, not general state keys the model or tools can edit. Plugins can deny calls, but downstream IAM remains the final control.

### CrewAI controls

CrewAI production guidance recommends task guardrails, structured Pydantic/JSON outputs, LLM hooks, Flow persistence, and human gates. CrewAI AMP advertises managed infrastructure, API access, observability, team RBAC, and production automation management. [[32]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx) [[35]](https://docs.crewai.com/enterprise/introduction) [[45]](https://docs.crewai.com/)

`[inferred]` In open-source CrewAI, the application owns identity, authorization, secrets, tenancy, audit retention, and tool isolation. Treat Agent `allow_delegation` as orchestration permission, not a security entitlement. Do not let an LLM translate free-form human feedback into approval for an irreversible action without deterministic confirmation.

### Prompt injection and excessive agency

Every framework carries untrusted tool output back into a model context. OWASP identifies excessive functionality, permission, and autonomy as causes of damaging agent actions and recommends least privilege, downstream authorization, high-impact approvals, complete mediation, monitoring, and rate limits. [[37]](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

Apply common defenses:

- Label user, retrieved, tool, memory, peer-agent, and policy provenance.
- Filter tools per node/agent/task rather than registering the enterprise catalog everywhere.
- Keep authorization, identity, budgets, and approval records outside model-editable state.
- Sanitize tool output but assume detection is imperfect; validate the generated action again.
- Use short-lived, audience-bound credentials minted at dispatch.
- Sandbox browser/code tools and restrict network/filesystem access.
- Red-team poisoned pages, files, memory, handoff summaries, Crew task context, ADK events, and checkpoint restores.

### Supply chain, versions, and portability

`[inferred]` Pin framework plus adapter versions, generate an SBOM, scan dependencies and images, verify licenses, sign deployment artifacts, and test serialized-state migrations before rollout. ADK 2.0 is a concrete warning: it adds Event fields, changes custom execution extension points, and can cause strict schemas or rigid custom session tables to fail until migrated. [[19]](https://adk.dev/2.0/)

Own these contracts outside the framework:

- domain request/result schemas;
- tool gateway, auth, and idempotency protocol;
- canonical run and effect ledger;
- prompt/model/tool/policy version registry;
- golden and adversarial eval corpus;
- OpenTelemetry correlation IDs and redaction policy;
- exportable artifacts and business postconditions.

This does not make an agent graph automatically portable; it limits the cost of rebuilding orchestration semantics.

### Observability and privacy

LangSmith can trace LangGraph automatically and supports OpenTelemetry ingestion/export patterns. [[8]](https://docs.langchain.com/langsmith/trace-with-opentelemetry) OpenAI Agents SDK traces generations, tools, handoffs, guardrails, and custom spans and warns that generation/tool inputs and outputs may contain sensitive data. [[43]](https://openai.github.io/openai-agents-python/tracing/) ADK implements OpenTelemetry GenAI conventions for distributed traces and provides agent evaluation of both output and trajectory. [[27]](https://adk.dev/observability/traces/) [[44]](https://adk.dev/evaluate/) CrewAI AMP provides traces/logs, while CrewAI also documents event listeners and external observability integrations. [[35]](https://docs.crewai.com/enterprise/introduction) [[46]](https://docs.crewai.com/llms.txt)

`[inferred]` Trace metadata by default and store raw prompts, tool results, and artifacts only under separate access, retention, and residency controls. A debugging dashboard is not an immutable compliance ledger.

## 5. Production Failure Modes

### Cross-framework failures

| Failure | Symptom | Prevention / recovery |
|---|---|---|
| Framework chosen by demo familiarity | architecture fights state/risk requirements | score the same workload against explicit NFRs |
| Session mistaken for durable execution | history survives but job/effect is lost | durable queue/workflow plus effect ledger |
| Resume duplicates mutation | email/payment/API executes twice | stable idempotency key and destination reconciliation |
| Framework retry plus SDK retry | retry storm and long tail | one owning retry layer and aggregate retry budget |
| Model claims completion | fluent output with wrong business state | external postcondition verifier |
| Unbounded delegation/fan-out | calls, tokens, and queue grow rapidly | turns, depth, width, tools, cost, and wall-time budgets |
| State/context divergence | model summary conflicts with database | rebuild projection from authoritative state/events |
| State schema upgrade breaks old runs | deserialization or missing fields | versioned schema, migration test, pinned in-flight code |
| Trace leakage | prompts, credentials, or PII in SaaS telemetry | redaction, content capture off, retention and access controls |
| Role/prompt treated as permission | unauthorized resource access | downstream complete mediation |
| Provider portability illusion | structured output/tool/usage breaks | contract test every exact adapter/provider combination |
| Managed-platform assumption in OSS | missing auth, queue, scaling, backup | document deployment-layer responsibilities explicitly |

### LangGraph-specific failures

- A side effect outside a checkpointed task repeats on replay; an incomplete task can also repeat. Make effects idempotent and reconcile ambiguous outcomes. [[4]](https://docs.langchain.com/oss/python/langgraph/functional-api)
- A wrong or reused `thread_id` crosses conversations or tenant boundaries. Authorize thread ownership and generate server-side scoped IDs.
- Parallel branches overwrite state through an incorrect reducer. Property-test reducers for ordering, duplication, and conflicts.
- Full-state checkpoints grow without bound. Store artifact references, compact history, use retention, and evaluate delta storage. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence)
- A standalone server is deployed as scale-to-zero serverless. LangSmith's self-hosting documentation warns task loss and unreliable scale-up for that mode. [[47]](https://docs.langchain.com/langsmith/self-hosted)

### OpenAI Agents SDK-specific failures

- `max_turns=None` or a generous nested run creates runaway work. Enforce run-wide budgets including nested agents. [[11]](https://openai.github.io/openai-agents-python/running_agents/)
- Session history is combined with an incompatible server continuation mechanism. The SDK prohibits that combination; select one state owner. [[13]](https://openai.github.io/openai-agents-python/sessions/)
- A handoff sends excess conversation or changes active instructions unexpectedly. Use input filters, narrow specialists, and test the exact transcript passed.
- An input/output guardrail is assumed to cover internal tools. Use tool guardrails and downstream authorization. [[15]](https://openai.github.io/openai-agents-python/guardrails/)
- A third-party provider adapter omits usage or lacks structured/tool behavior. Validate the deployed backend, not the nominal model name. [[16]](https://openai.github.io/openai-agents-python/models/)
- A process dies after a tool effect because only session memory was durable. Wrap the Runner in a documented durable runtime for long work.

### Google ADK-specific failures

- A 1.x custom `BaseAgent` override is silently bypassed after 2.0 because the workflow engine now uses node lifecycle callbacks. Migrate extension logic and run lifecycle tests. [[19]](https://adk.dev/2.0/)
- New `node_info`/`output` Event fields break rigid session tables or strict clients. Migrate storage and readers before writers.
- Rewind is treated as atomic rollback even though external dependencies, app/user state, and atomic state/artifact/event updates are excluded. Lock and compensate. [[24]](https://adk.dev/sessions/rewind/)
- Synchronous CPU or I/O work blocks the async runtime. Offload CPU work and use real async clients. [[22]](https://adk.dev/runtime/event-loop/)
- ADK 2.0 graph workflow is selected for a live-streaming requirement it does not support. Validate the current limitations or use another workflow style. [[21]](https://adk.dev/graphs/)
- Shared `user:` or `app:` state is mutated without concurrency policy. Put shared authoritative data in a transactional domain store.

### CrewAI-specific failures

- A Crew is deployed directly for a controlled transaction and autonomous delegation obscures the path. Wrap it in a typed Flow and restrict the Crew to analysis/proposal.
- `max_iter` bounds one Agent but not total Crew/Flow work. Add global model/tool/cost/time budgets.
- A restore ID miss silently starts fresh state. Verify domain run existence and fail closed. [[31]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx)
- Class-level `@persist` records a mid-run snapshot that is later mistaken for a committed state. Persist at designed transition boundaries and include a domain status/version.
- Human feedback is LLM-collapsed to the wrong route. Keep raw feedback and require structured confirmation for high-impact decisions. [[33]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.14.7/en/concepts/flows.mdx)
- Deprecated code-execution flags or old tutorials create unsafe assumptions. Use a dedicated sandbox and pin current documentation. [[30]](https://github.com/crewaiinc/crewai/blob/main/docs/en/concepts/agents.mdx)

> ⚠️ Limited public data available for this dimension. The framework owners do not publish a shared taxonomy with production incident rates for lost runs, duplicate effects, checkpoint corruption, cross-tenant state, runaway delegation, migration failures, or trace leakage. Public repositories expose issues and fixes, but they are not normalized denominators or audited reliability statistics.

### Required verification suite

For every candidate implementation, inject a crash before dispatch, after remote commit but before local receipt, after checkpoint, during parallel join, while waiting for approval, and during cancellation. Repeat messages, reorder events, corrupt/omit state, expire credentials, throttle providers, return malformed tools, poison observations, and resume under the next release candidate. Assert both final state and forbidden side effects.

## 6. Enterprise System Design Scenarios

### Scenario A: regulated claims or payment workflow

**Shape:** known state machine, long waits, exact approval binding, compensations, and audit.

`[inferred]` Prefer LangGraph when the team needs explicit graph topology/reducers/checkpoints and will operate the persistence/runtime, or Google ADK 2.0 when Google Cloud deployment and its event/service model are strategic. In either case, keep the payment/claim ledger outside the framework, put agent work in proposal/investigation nodes, and make execution a deterministic authorized node. OpenAI Agents SDK can serve model/tool nodes inside a durable workflow, but the SDK Session alone is insufficient. CrewAI can fit only with Flow-first control and a separately verified effect ledger.

### Scenario B: OpenAI-first customer support with specialist routing

**Shape:** short interactive sessions, web/file retrieval, several specialists, tool approvals, and integrated trace review.

`[inferred]` OpenAI Agents SDK is the smallest natural surface: use a triage handoff when the specialist should own the conversation, `Agent.as_tool()` when a manager should synthesize, session history for continuity, blocking/tool guardrails, and serialized RunState for approval. Add an external durable runtime only for tasks that outlive the request/worker. [[12]](https://openai.github.io/openai-agents-python/multi_agent/) [[14]](https://openai.github.io/openai-agents-python/human_in_the_loop/)

### Scenario C: Google Cloud multimodal operations agent

**Shape:** Gemini-heavy stack, event streaming, files/images, session and long-term memory services, Cloud IAM, and a mixture of deterministic and collaborative paths.

`[inferred]` Google ADK aligns well: use Workflow nodes for deterministic routing, isolated collaborative task agents for open-ended branches, ArtifactService for versioned binary objects, persistent SessionService, global Plugins for policy/telemetry, and Agent Runtime/Cloud Run/GKE according to control needs. Avoid graph workflows if live streaming is mandatory until the documented limitation changes; test language-specific parity. [[21]](https://adk.dev/graphs/) [[39]](https://adk.dev/artifacts/) [[25]](https://adk.dev/plugins/) [[26]](https://adk.dev/deploy/)

### Scenario D: research/content automation with role-oriented teams

**Shape:** rapidly changing research, reviewer, writer, and editor roles; business users understand team/task vocabulary; moderate risk.

`[inferred]` CrewAI is a pragmatic fit: place the research/writing Crew inside a Flow, use Pydantic task outputs and state, add a deterministic source/quality gate, cap every agent plus the whole Flow, and persist only intentional boundaries. For a later transactional action, emit a proposal into another service rather than granting the content Crew direct production credentials. [[29]](https://docs.crewai.com/core-concepts/Agents) [[32]](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx)

### Decision matrix

Score with a proof-of-concept rather than accepting these qualitative defaults:

| Selection concern | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---|---|---|---|---|
| Explicit state graph/reducers | strongest native emphasis | code-owned outside loop | strong in ADK 2.0 Python/Go | Flow routes, less reducer-centric |
| Minimal agent-loop API | lower-level | strongest emphasis | Runner plus services | high-level Crew API |
| Role/team mental model | custom/subgraphs | handoffs/agents-as-tools | collaborative agents | strongest emphasis |
| Built-in state service taxonomy | checkpoint/store | Session/RunState | Session/State/Memory/Artifact | Flow state/memory/knowledge |
| Durable recovery in core design | strong with checkpointer | external durable integration for long work | event/resume facilities | Flow persistence/checkpoints |
| OpenAI hosted-tool alignment | via model integration | strongest | possible through custom/provider work | via configured models/tools |
| Google Cloud alignment | deployable, optional LangSmith | application-managed | strongest | application/AMP managed |
| Self-managed OSS freedom | MIT library | MIT library | Apache-2.0 framework | MIT library |
| Managed control plane choice | LangSmith | OpenAI trace UI plus chosen app runtime | Agent Runtime / Google Cloud | CrewAI AMP |

### Principal-architect recommendation

1. Choose the control model that matches the risk and state machine, not the most attractive demo.
2. Separate OSS library, hosted runtime, trace platform, model provider, and tool services in the architecture and budget.
3. Keep identity, authorization, effect ledger, business state, approvals, and eval corpus framework-independent.
4. Prove crash recovery and schema upgrades before feature breadth.
5. Pin exact versions and test language/provider parity; “supports” does not mean identical semantics.
6. Benchmark repeated verified success, p95/p99, recovery, and cost per success on the same workload.
7. Favor a deterministic outer workflow with bounded agent nodes for consequential systems, regardless of framework.

## Sources

- [1] https://docs.langchain.com/oss/python/langgraph/overview - LangGraph scope and core capabilities.
- [2] https://docs.langchain.com/oss/python/langgraph/use-graph-api - Nodes, edges, state, reducers, and graph execution.
- [3] https://docs.langchain.com/oss/python/langgraph/persistence - Checkpoints, stores, pending writes, replay, and storage growth.
- [4] https://docs.langchain.com/oss/python/langgraph/functional-api - Durable task replay and idempotent-side-effect guidance.
- [5] https://docs.langchain.com/oss/python/langgraph/interrupts - Pause/resume behavior and interrupt side-effect rules.
- [6] https://docs.langchain.com/langsmith/deployment - LangSmith cloud, standalone, and self-hosted deployment modes.
- [7] https://docs.langchain.com/langsmith/set-up-custom-auth - LangSmith authentication scope and server protection.
- [8] https://docs.langchain.com/langsmith/trace-with-opentelemetry - LangSmith OpenTelemetry tracing.
- [9] https://github.com/langchain-ai/langgraph/blob/main/LICENSE - LangGraph MIT license.
- [10] https://openai.github.io/openai-agents-python/agents/ - OpenAI Agent and Runner architecture.
- [11] https://openai.github.io/openai-agents-python/running_agents/ - Run loop, limits, errors, and durable integrations.
- [12] https://openai.github.io/openai-agents-python/multi_agent/ - Handoffs, agents-as-tools, and code orchestration.
- [13] https://openai.github.io/openai-agents-python/sessions/ - Session history and continuation constraints.
- [14] https://openai.github.io/openai-agents-python/human_in_the_loop/ - Approval interruption, serialized state, and resume.
- [15] https://openai.github.io/openai-agents-python/guardrails/ - Guardrail scope and blocking versus parallel execution.
- [16] https://openai.github.io/openai-agents-python/models/ - OpenAI and non-OpenAI provider integration limits.
- [17] https://openai.github.io/openai-agents-python/handoffs/ - Handoff history, filters, and callbacks.
- [18] https://github.com/openai/openai-agents-python/blob/main/LICENSE - OpenAI Agents SDK MIT license.
- [19] https://adk.dev/2.0/ - ADK 2.0 graph runtime and migration incompatibilities.
- [20] https://adk.dev/workflows/ - ADK graph, dynamic, collaborative, and template workflows.
- [21] https://adk.dev/graphs/ - ADK 2.0 workflow graph mechanics and limitations.
- [22] https://adk.dev/runtime/event-loop/ - ADK Runner, Events, async execution, and blocking risks.
- [23] https://adk.dev/sessions/ - ADK Session, State, Memory, and service boundaries.
- [24] https://adk.dev/sessions/rewind/ - ADK Rewind behavior, exclusions, and atomicity limitation.
- [25] https://adk.dev/plugins/ - ADK global lifecycle plugins, policy, metrics, and caching.
- [26] https://adk.dev/deploy/ - ADK deployment options.
- [27] https://adk.dev/observability/traces/ - ADK OpenTelemetry tracing and agent trajectory.
- [28] https://github.com/google/adk-python - Google ADK positioning and Apache-2.0 license.
- [29] https://docs.crewai.com/core-concepts/Agents - CrewAI Crews-versus-Flows architecture.
- [30] https://github.com/crewaiinc/crewai/blob/main/docs/en/concepts/agents.mdx - CrewAI Agent controls and code-execution guidance.
- [31] https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx - CrewAI Flow state and persistence semantics.
- [32] https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx - CrewAI Flow-first production guidance.
- [33] https://github.com/crewAIInc/crewAI/blob/main/docs/v1.14.7/en/concepts/flows.mdx - CrewAI Flow routing and human feedback.
- [34] https://github.com/crewaiinc/crewai - CrewAI open-source repository and MIT license.
- [35] https://docs.crewai.com/enterprise/introduction - CrewAI AMP deployment and observability capabilities.
- [36] https://arxiv.org/abs/2606.05548 - ADK Arena 2026 framework evaluation preprint.
- [37] https://genai.owasp.org/llmrisk/llm062025-excessive-agency/ - OWASP excessive-agency threat and mitigation guidance.
- [38] https://adk.dev/workflows/collaboration/ - ADK collaborative multi-agent workflow semantics.
- [39] https://adk.dev/artifacts/ - ADK versioned binary artifact service.
- [40] https://adk.dev/runtime/resume/ - ADK workflow resume behavior.
- [41] https://adk.dev/safety/ - ADK safety and tool-execution guidance.
- [42] https://adk.dev/tools-custom/authentication/ - ADK tool credential and authentication guidance.
- [43] https://openai.github.io/openai-agents-python/tracing/ - OpenAI Agents SDK tracing and sensitive-data controls.
- [44] https://adk.dev/evaluate/ - ADK output and trajectory evaluation.
- [45] https://docs.crewai.com/ - CrewAI documentation and enterprise RBAC summary.
- [46] https://docs.crewai.com/llms.txt - CrewAI current documentation index, including observability integrations.
- [47] https://docs.langchain.com/langsmith/self-hosted - LangSmith self-hosting constraints and scale-to-zero warning.
