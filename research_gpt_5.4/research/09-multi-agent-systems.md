# Research: Multi-Agent Systems - Supervisor, Worker, Collaboration, Delegation

**Date researched**: 2026-08-21
**Sources consulted**: 18

---

## 1. System Topology & Mechanics

The cleanest current distinction is between **manager-style supervision** and **delegated ownership**. In the `OpenAI Agents SDK`, `agents as tools` keeps a manager agent in control of the final answer, while `handoffs` transfer the active branch of the conversation to a specialist agent that owns the rest of the turn ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). That maps closely to the architectural difference between a classic **supervisor-worker** tree and a **router-to-specialist** topology.

`LangChain` now documents the **subagents** pattern as its primary centralized multi-agent design: a main agent, often explicitly called a **supervisor**, coordinates subagents by calling them as tools, decides which worker to invoke, what input to pass, and how to combine results ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)). The same docs state that subagents are typically **stateless**, with conversation memory maintained by the main agent, which makes context isolation a first-class reason to split work across workers rather than giving one agent every tool ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)).

The older `langgraph-supervisor` package is now explicitly deprecated in favor of this tool-wrapped subagent pattern. The migration guidance says to flatten independent workers under one supervisor when possible, and only keep nested hierarchies when an intermediate layer provides real coordination value ([LangGraph supervisor migration](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)). That is a practical signal that current production guidance is shifting away from bespoke supervisor abstractions toward general graph/tool composition.

`Google ADK` supports both deterministic workflow composition and LLM-driven collaboration. In **collaborative workflows**, a coordinator agent delegates to `sub_agents`, and ADK automatically generates one delegation tool per subagent, named after the subagent itself ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/)). ADK also exposes three collaboration modes:

- `chat`: subagent can interact with the user and must transfer control back explicitly.
- `task`: subagent can interact across turns, then returns automatically after `finish_task`.
- `single_turn`: subagent completes one bounded task and returns automatically.

([ADK collaborative workflows](https://adk.dev/workflows/collaboration/), [ADK LLM agents](https://adk.dev/agents/llm-agents/))

Those modes are useful because they make the boundary between **worker**, **delegate**, and **temporary specialist** explicit in the runtime itself.

`CrewAI` exposes two related collaboration layers. Inside one crew, `allow_delegation=True` gives agents built-in **Delegate Work** and **Ask Question** tools so teammates can assign work or query one another directly ([CrewAI collaboration](https://docs.crewai.com/edge/en/concepts/collaboration)). At the orchestration layer, `Process.hierarchical` introduces a manager agent or `manager_llm` that allocates tasks, validates outputs, and decides when tasks are complete ([CrewAI processes](https://docs.crewai.com/en/concepts/processes)). This is structurally closer to a human project-manager model than the flatter tool-routing style common in LangChain/OpenAI.

Anthropic's published guidance frames the same design space as **orchestrator-workers**: a central LLM dynamically decomposes a task, delegates subtasks to workers, and synthesizes results when the subtasks cannot be predicted in advance ([Anthropic effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Anthropic's multi-agent research further distinguishes systems where agents treat each other as **tool-like bounded components** from systems where they act as long-lived peers without a clear hierarchy; the latter remain much less mature operationally ([Anthropic multiagent research](https://www.anthropic.com/research/multiagent-systems)).

For cross-process or cross-vendor delegation, `CrewAI` documents **A2A** transport support with `JSONRPC` as default and `GRPC` or `HTTP+JSON` as alternatives, while the endpoint typically starts from an agent card at `/.well-known/agent-card.json` ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation), [CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)). For tool access rather than peer delegation, `MCP` remains the dominant host-to-tool pattern rather than agent-to-agent collaboration ([OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/), [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable **p50/p95/p99 end-to-end latency** of multi-agent systems across frameworks. Official docs describe patterns and controls far better than they publish comparable percentile benchmarks.

The main economic trade-off is straightforward: **multi-agent systems usually spend more tokens than single-agent systems, but they can reduce latency or improve quality when they isolate expertise or parallelize independent subtasks**. Anthropic says hierarchical systems consume "significantly more tokens than single interactions," but argues that the performance gains can justify that overhead for high-value tasks ([Anthropic effective agents](https://www.anthropic.com/engineering/building-effective-agents)). OpenAI's orchestration guidance makes the same point from the opposite direction: splitting too early creates more prompts, more traces, and more approval surfaces, so specialists should be added only when capability isolation or policy isolation materially improves the workflow ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)).

A useful first-order cost model is:

```text
multi_agent_run_cost
  ~= supervisor_turns
   + Σ(worker_turns)
   + synthesis_turns
   + duplicated_context_tokens
   + tool/protocol overhead
```

([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [Anthropic effective agents](https://www.anthropic.com/engineering/building-effective-agents)) [inferred]

The latency model differs from the cost model because worker turns can overlap:

```text
critical_path_latency
  ~= routing_or_planning
   + max(parallel_worker_branches)
   + synthesis
   + approval / network overhead
```

([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents), [ADK collaborative workflows](https://adk.dev/workflows/collaboration/)) [inferred]

There are a few concrete runtime constraints in public docs:

- `OpenAI Agents SDK`: the runner raises `MaxTurnsExceeded` if `max_turns` is hit, and the optional websocket helper still inherits the service limit of **one response at a time per websocket connection** with a **60-minute** connection lifetime ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).
- `LangChain` subagents: the main agent can invoke multiple subagents in a single turn, and the runtime can execute those calls in parallel ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)).
- `ADK`: `single_turn` workers are explicitly bounded, while collaborative workflows allow automatic return to the coordinator after completion, which reduces the risk of unnecessarily long worker conversations ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/), [ADK LLM agents](https://adk.dev/agents/llm-agents/)).
- `CrewAI A2A`: remote delegation exposes explicit `timeout`, `max turns`, transport preferences, and update modes such as streaming, polling, or push notifications, which are effectively NFR knobs for distributed collaboration ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation)).

For caching, public material is uneven. `LangChain`'s subagent guidance makes **context isolation** the main optimization lever: stateless workers receive only the bounded input they need, which prevents the supervisor's transcript from ballooning across every worker call ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)). `ADK` similarly uses collaboration modes and scoped subagent behavior to constrain how much context each worker carries ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/)) [inferred]. However, the sources consulted do **not** publish a shared, framework-level story for **semantic caching hit rates**, **TTL defaults**, or **cache invalidation** specifically for multi-agent delegation workflows.

Dynamic model routing is also mostly application-defined. `OpenAI`, `LangChain`, and `CrewAI` all support specialist agents with different instructions and models, but their docs stop short of publishing a built-in complexity router for supervisor-to-worker selection ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents), [CrewAI processes](https://docs.crewai.com/en/concepts/processes)) [inferred].

## 3. Distributed Resilience & State

The most important resilience question in multi-agent systems is **where shared state actually lives**. `LangChain`'s subagents pattern deliberately keeps memory in the main agent and treats workers as mostly stateless helpers ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)). That reduces cross-worker drift, but it also means the supervisor becomes the main consistency boundary [inferred].

When subagents are implemented as `LangGraph` subgraphs, persistence behavior depends on how they are wired. The `use-subgraphs` docs say **per-invocation persistence** is the recommended mode for most multi-agent systems where subagents handle independent requests; **per-thread persistence** is the better fit when a subagent needs multi-turn memory of its own ([LangGraph use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)). The same page also notes that if subagents are wrapped as tools, graph state cannot be statically discovered through normal subgraph inspection, which is an observability and debugging trade-off rather than just an implementation detail ([LangGraph use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)).

`OpenAI Agents SDK` takes a pause/resume approach to durable collaboration. Sensitive tool or agent-as-tool calls can pause a run for approval, surface interruptions, and serialize `RunState` for later resume ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)). This is strong for human-gated delegation, but it is not the same as a full distributed workflow engine with event-history replay [inferred].

`ADK` is the most explicit of the major frameworks here about **session-scoped collaboration semantics**. Collaborative workflows can branch work to subagents, and `task` / `single_turn` modes automatically return control to the parent after completion ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/)). The broader ADK agent model also exposes hierarchy via `sub_agents`, `parent_agent`, and `find_agent(name)`, which gives custom workflows a direct way to reason about delegation scope ([ADK custom agents](https://adk.dev/agents/custom-agents/)).

`CrewAI` provides two resilience layers:

- In-process team coordination via delegation tools and hierarchical crews ([CrewAI collaboration](https://docs.crewai.com/edge/en/concepts/collaboration), [CrewAI processes](https://docs.crewai.com/en/concepts/processes)).
- Cross-process or remote collaboration via A2A clients, which can stream, poll, or receive push notifications for task status and can continue with available agents when one remote agent is unavailable if fail-fast behavior is disabled ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation)).

That second point matters because it gives an explicit **graceful degradation** path for partial team failure rather than forcing the whole multi-agent run to fail closed.

At the protocol layer, A2A-style delegation naturally creates independent failure domains: remote agent endpoints, transport queues, webhook delivery, and coordinator state all become separate retry surfaces ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation), [CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)) [inferred]. In practice, that means cross-agent collaboration behaves less like nested function calls and more like a distributed system with queueing, timeout, and partial-failure semantics.

## 4. Enterprise Security & Governance

For multi-agent systems, the control problem is not only "can the model call a tool?" but also "which agent may delegate to which other agent, over what transport, with what approval and audit surface?" The strongest public spec here is still `MCP` authorization. The draft authorization spec requires MCP servers to implement **OAuth 2.0 Protected Resource Metadata** for authorization-server discovery, while the 2026 security considerations require clients to implement **PKCE**, use the `S256` code challenge when possible, and bind tokens to the intended server with the OAuth `resource` parameter ([MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization), [MCP authorization security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)).

`OpenAI Agents SDK` exposes the most concrete **approval plane** for delegation-heavy systems. `needs_approval` works on function tools and `Agent.as_tool()`, and local or hosted MCP integrations can require approval before a tool call proceeds ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/)). This is important in supervisor-worker designs because it lets a central orchestrator delegate bounded work while still pausing high-impact actions before execution.

`ADK` relies more on **clear delegation contracts** than on a published centralized policy plane. Its docs emphasize that subagents need distinct `description` fields so the parent can route correctly, and that transfer scope across parent, child, or sibling agents can be configured in custom workflows ([ADK LLM agents](https://adk.dev/agents/llm-agents/), [ADK custom agents](https://adk.dev/agents/custom-agents/)). That improves routing hygiene, but the consulted sources do not publish equally detailed first-party RBAC or approval matrices for agent-to-agent delegation.

`CrewAI` has the strongest published **remote-agent auth surface** among the framework docs consulted. Open-source A2A clients support Bearer tokens, OAuth2, API keys, and HTTP authentication for outbound delegation ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation)). CrewAI AMP extends inbound auth to OIDC, OAuth2 server auth with scopes, API-key auth, and mutual TLS, and states that these schemes work across both HTTP and gRPC transports ([CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)).

The `A2A` model also pushes governance into the **agent card**. CrewAI documents aggregate and per-agent cards at `/.well-known/agent-card.json`, while A2A clients use those cards to discover capabilities, transports, and security requirements before delegation ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation), [CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)). That is operationally similar to service discovery plus auth metadata in a microservice mesh [inferred].

Anthropic's research adds an important governance caveat: **groups of individually aligned agents can behave less ethically than a single agent even when they are more effective** ([Anthropic multiagent research](https://www.anthropic.com/research/multiagent-systems)). For enterprise deployments, that means single-agent safety evals do **not** transfer automatically to supervisor-worker or collaborative swarms [inferred].

> ⚠️ Limited public data available for built-in **PII redaction pipelines**, **sandbox isolation guarantees** (process vs container vs VM vs WASM), and **immutable audit-log schemas** across these multi-agent frameworks. Public documentation is much stronger on authorization, approvals, and delegation mechanics than on compliance-grade redaction internals.

## 5. Production Failure Modes

### Context-window degradation

Multi-agent systems often solve one context problem by creating another. `LangChain`'s answer is to keep subagents stateless and context-isolated so each worker gets only the relevant slice of work instead of the supervisor's whole transcript ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)). Anthropic also recommends the orchestrator-workers pattern specifically when the decomposition must be dynamic, which implies the coordinator should send bounded worker prompts rather than let all workers share one massive context ([Anthropic effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

### Infinite delegation loops

`OpenAI Agents SDK` gives an explicit `max_turns` circuit breaker for agent loops ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). `ADK` narrows loop risk by making `single_turn` and `task` collaboration modes explicit, and by distinguishing automatic return from `chat` mode's explicit transfer behavior ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/), [ADK LLM agents](https://adk.dev/agents/llm-agents/)). `CrewAI` hierarchical crews can still create manager-specialist ping-pong if delegation instructions are vague, so the manager's role definition and delegation policy become the practical loop guard ([CrewAI collaboration](https://docs.crewai.com/edge/en/concepts/collaboration), [CrewAI processes](https://docs.crewai.com/en/concepts/processes)) [inferred].

### Wrong-agent routing and delegation drift

OpenAI explicitly advises keeping each specialist narrow and each `handoffDescription` short and concrete so routing stays legible ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)). ADK similarly says the `description` field should be specific enough for other agents to decide whether they should route to that agent ([ADK LLM agents](https://adk.dev/agents/llm-agents/)). In practice, ambiguous agent descriptions are one of the fastest ways to create degraded collaboration quality because the orchestrator starts treating routing as a fuzzy semantic task instead of a constrained policy choice [inferred].

### Hidden or fragmented state

If a `LangGraph` subagent is wrapped as a tool, parent-level graph inspection cannot statically discover that nested state; if teams need to inspect or persist nested graph state, the docs recommend calling the subgraph from a node function instead ([LangGraph use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)). This is a concrete example of a production failure mode where the system still works functionally, but debugging and recovery become much harder.

### Cascading timeouts and partial remote failure

Remote collaboration adds transport-level failure modes that local supervisor-worker systems largely avoid. `CrewAI` A2A clients expose timeout controls, multiple transport options, and alternative update mechanisms such as polling or push notifications ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation)). `OpenAI`'s websocket transport similarly documents connection lifetime and reuse constraints, including the 60-minute limit ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). The architectural implication is to treat each remote worker as a bulkhead with its own deadline and fallback, not as a perfectly reliable nested call [inferred].

### Hallucinated tool or agent parameters

`OpenAI` mitigates this through approval gates and structured tool execution surfaces ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/)). `CrewAI` reduces risk by constraining agent collaboration through delegation tools and managed processes rather than arbitrary peer messaging ([CrewAI collaboration](https://docs.crewai.com/edge/en/concepts/collaboration), [CrewAI processes](https://docs.crewai.com/en/concepts/processes)) [inferred]. But none of the consulted sources claims that simply adding more agents eliminates bad parameter generation; it mainly relocates validation to more boundaries.

### Incident and post-mortem coverage

> ⚠️ Limited public data available for detailed RCA-style incident reports specific to production multi-agent systems. Most public sources are framework docs, design guidance, or research writeups rather than operational post-mortems.

## 6. Enterprise System Design Scenarios

### 6.1 Pattern selection matrix

| Pattern | Best fit | Main strengths | Main trade-offs |
| --- | --- | --- | --- |
| Supervisor with workers-as-tools | Internal copilots, bounded research, structured back-office flows | Centralized policy and final-answer ownership; easier approvals and context isolation ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)) | More duplicated prompts and coordination turns |
| Handoff / delegated ownership | Triage-to-specialist support flows | Specialist owns instructions, tools, and response for that branch ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)) | Harder to preserve one central policy surface |
| Hierarchical manager + specialists | Task management, content pipelines, multi-role automation | Explicit chain of command, validation, delegation ([CrewAI processes](https://docs.crewai.com/en/concepts/processes)) | Manager quality becomes a system bottleneck |
| Collaborative coordinator + task/single-turn agents | Mixed deterministic + agentic workflows | Automatic return semantics and explicit collaboration modes ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/)) | Public benchmark and governance data are thinner than the control primitives |
| Remote A2A agent mesh | Cross-team or cross-vendor automation | Transport flexibility, agent cards, auth negotiation, partial independence ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation), [CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)) | Distributed failure modes, auth complexity, harder observability |

### 6.2 Reference deployment patterns

**Pattern A: SaaS copilot with sensitive side effects**

- Use a **supervisor with workers-as-tools** so the manager owns the final answer and all privileged actions pass through one approval surface ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- Add `MCP` for enterprise tool integration when the tools need standardized auth and discovery ([OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/), [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)).

**Pattern B: Multi-step internal operations workflow**

- Use `ADK` collaborative workflows or a LangGraph-style supervisor/subgraph composition when workers need clear lifecycle boundaries and predictable return semantics ([ADK collaborative workflows](https://adk.dev/workflows/collaboration/), [LangGraph use-subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)).
- Prefer `single_turn` or bounded `task` workers over unconstrained peer chat for operational determinism ([ADK LLM agents](https://adk.dev/agents/llm-agents/)) [inferred].

**Pattern C: Cross-organization agent collaboration**

- Use `A2A`-style endpoints when teams or vendors need interoperable remote agents rather than shared in-process runtime state ([CrewAI A2A delegation](https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation), [CrewAI enterprise A2A](https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a)).
- Treat agent cards, transport negotiation, and auth scopes as platform contracts, not as incidental configuration [inferred].

### 6.3 Published scale signals and planning heuristics

The best concrete public scale datapoint in the consulted source set is Anthropic's multi-agent vulnerability research setup: **45 agents**, each with its own virtual machine, coordinated through a shared forum and an arbiter agent, run across **15 open-source software projects** for **12 hours** ([Anthropic multiagent research](https://www.anthropic.com/research/multiagent-systems)). This is not a reusable benchmark, but it does show the current shape of large multi-agent experimentation: isolated worker environments, shared coordination substrate, and explicit review/arbitration.

For production planning, the most useful rough formulas are:

```text
max_parallelism
  ~= min(
       provider_concurrency_limit,
       remote_agent_capacity,
       human_approval_bandwidth,
       tool/backend concurrency ceilings
     )
```

```text
routing_overhead_ratio
  ~= supervisor_tokens / total_run_tokens
```

```text
effective_worker_efficiency
  improves when worker context << full transcript
  and worker branches can run concurrently
```

([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/), [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents), [Anthropic effective agents](https://www.anthropic.com/engineering/building-effective-agents)) [inferred]

### 6.4 Strongest practical conclusions

1. The most mature production pattern remains **centralized supervision with bounded workers**, not fully peer-to-peer agent swarms.
2. Multi-agent systems become worthwhile when they provide one of three concrete gains: **context isolation**, **parallelism**, or **policy isolation**.
3. Remote collaboration via `A2A` should be treated as distributed-systems engineering, not just prompt engineering.
4. Governance must be evaluated at the **organization level**, because a team of aligned agents can still produce collectively misaligned behavior ([Anthropic multiagent research](https://www.anthropic.com/research/multiagent-systems)).

## Sources

- [1] https://developers.openai.com/api/docs/guides/agents/orchestration - OpenAI manager-style orchestration, handoffs, and specialist design guidance.
- [2] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI runner loop, `max_turns`, websocket limits, and run continuation behavior.
- [3] https://openai.github.io/openai-agents-python/human_in_the_loop/ - OpenAI approval pauses, resumable run state, and approval coverage for tools and agent-as-tool calls.
- [4] https://openai.github.io/openai-agents-python/mcp/ - OpenAI MCP integration, approval policies, and tool filtering.
- [5] https://docs.langchain.com/oss/python/langchain/multi-agent/subagents - LangChain supervisor/subagent pattern, stateless workers, context isolation, and parallel subagent execution.
- [6] https://docs.langchain.com/oss/python/migrate/langgraph-supervisor - Deprecation of `langgraph-supervisor` and migration to tool-wrapped subagents.
- [7] https://docs.langchain.com/oss/python/langgraph/use-subgraphs - LangGraph subgraph persistence modes, nested-state visibility, and multi-agent subgraph guidance.
- [8] https://adk.dev/workflows/collaboration/ - ADK coordinator/subagent collaboration, modes, and automatic return semantics.
- [9] https://adk.dev/agents/llm-agents/ - ADK agent descriptions, delegation-related fields, and mode semantics.
- [10] https://adk.dev/agents/custom-agents/ - ADK hierarchy primitives, `sub_agents`, transfer scope, and custom orchestration patterns.
- [11] https://docs.crewai.com/edge/en/concepts/collaboration - CrewAI delegation tools and agent-to-agent collaboration within a crew.
- [12] https://docs.crewai.com/en/concepts/processes - CrewAI sequential vs hierarchical execution and manager-agent behavior.
- [13] https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation - CrewAI outbound A2A delegation, auth options, timeouts, update modes, and transport choices.
- [14] https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a - CrewAI enterprise A2A transport, agent-card, auth, TLS, and gRPC features.
- [15] https://www.anthropic.com/engineering/building-effective-agents - Anthropic orchestrator-workers pattern and trade-offs of multi-agent decomposition.
- [16] https://www.anthropic.com/research/multiagent-systems - Anthropic research on emerging multi-agent patterns, scaling experiments, and alignment risks.
- [17] https://modelcontextprotocol.io/specification/draft/basic/authorization - MCP authorization requirements, discovery, and OAuth-based protected resource model.
- [18] https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations - MCP security requirements including PKCE, HTTPS, and resource-bound tokens.
