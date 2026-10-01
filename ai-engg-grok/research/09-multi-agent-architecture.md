# Research: Multi-Agent Architecture

**Date researched**: 2026-09-30
**Sources consulted**: 22
**Scope note**: Platform-level multi-agent *topology* (who coordinates, how control/state move, fan-out economics). Control-flow *patterns* (ReAct, chaining, etc.) are covered in `08-agentic-patterns.md`. **A2A (Agent-to-Agent) protocol** is a later roadmap topic — noted here only as an interoperability boundary (cross-vendor agent messaging), not specified.

## 1. System Topology & Mechanics

### When to leave a single agent

A single agent has one context window, one tool set, and one run loop. Multi-agent systems become justified when the task hits **context overflow**, needs **true parallelism** across independent threads, or requires **role/tool/permission specialization** that one agent cannot safely hold ([System Design Newsletter — Multi-Agent Architectures](https://newsletter.systemdesign.one/p/multi-agent-system); [Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)). Single-agent counterexample cited in the newsletter teaser: Cognition’s Devin processed **5M lines of COBOL** across **500GB** of repos and raised PR merge rate **34% → 67%** without multi-agent fan-out ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

Anthropic’s production framing: multi-agent systems excel at **breadth-first**, heavily parallelizable work, information that **exceeds a single context window**, and interfacing with **many complex tools**; they are a poor fit when agents must share the same context or have many inter-agent dependencies (e.g., most coding tasks today) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

**Control plane vs. data plane** [inferred from framework docs]:

| Plane | Role in multi-agent systems |
| --- | --- |
| **Control** | Supervisor/lead routing, handoff tools (`transfer_to_*`, `transfer_to_agent`), graph edges, workflow agents (`Sequential`/`Parallel`/`Loop`), recursion/`max_turns` caps |
| **Data** | Per-agent context windows, shared `messages` / `session.state`, artifact stores / filesystem refs, citation passes |

**Protocol boundary (not deep-dived here):** MCP-style tool servers connect agents to tools/data; **A2A** (later topic) is the emerging agent-to-agent interoperability layer across vendors — do not treat MCP as peer agent messaging ([System Design Newsletter teaser on MCP vs A2A](https://newsletter.systemdesign.one/p/multi-agent-system)).

### Canonical topologies (newsletter taxonomy + framework mapping)

The System Design Newsletter enumerates six shapes ranging from central control to no coordinator: **orchestrator-worker, pipeline, hierarchical, swarm, mesh, handoffs** ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Free/teaser content fully documents the first three; swarm/mesh details beyond the blackboard sketch are paywalled.

| Topology | Coordination | Framework analogues |
| --- | --- | --- |
| **Orchestrator-worker** | Lead decomposes; workers run in parallel; no worker↔worker chat; results return to lead | Anthropic Research; OpenAI **agents-as-tools**; ADK `AgentTool` / coordinator+`ParallelAgent`; LangGraph supervisor with `parallel_tool_calls=True` |
| **Pipeline / DAG** | Fixed stage order; stage contracts (“rails”) | ADK `SequentialAgent`; Stripe-style agent DAG ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)); CrewAI `Process.sequential` |
| **Hierarchical** | Tree of supervisors → workers; summaries climb | LangGraph nested supervisors; ADK multi-level `sub_agents`; IBM watsonx Orchestrate (**80+** domain agents) cited ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)); CrewAI `Process.hierarchical` |
| **Swarm / peer handoff** | Agents hand off control; last-active agent resumes | LangGraph Swarm (`active_agent` + checkpointer); OpenAI **handoffs** |
| **Mesh / blackboard** | Shared store, no direct peer messages (newsletter teaser) | Redis/DB/vector blackboard pattern [described; limited public primary-source depth] |

### Anthropic Research: production orchestrator-worker

Architecture: **LeadResearcher** (Claude Opus 4) plans, persists plan to Memory (because context can exceed **200,000** tokens and truncate), spawns **Subagents** (Claude Sonnet 4) with isolated context windows, synthesizes, optionally iterates, then a separate **CitationAgent** attributes claims ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

Fan-out guidance encoded in prompts ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)):

| Query class | Subagent fan-out | Tool-call budget (per agent) |
| --- | --- | --- |
| Simple fact-finding | **1** agent | **3–10** tool calls |
| Direct comparisons | **2–4** subagents | **10–15** calls each |
| Complex research | **>10** subagents with clear division of labor | Clearly divided responsibilities |

Parallelism levers that cut research wall-clock by **up to 90%**: (1) lead spins **3–5** subagents in parallel (not serially); (2) each subagent uses **3+** tools in parallel ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Newsletter restates Anthropic’s worker band as **2–10** (sometimes more) Sonnet workers ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

Execution model today: lead runs subagents **synchronously** (waits for each set to finish). That simplifies coordination but blocks steering, cross-worker coordination, and progress while one slow worker runs; Anthropic flags **async** fan-out as future work with harder state/error semantics ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

Artifact pattern to avoid “telephone”: subagents write large outputs to a **filesystem / external store** and return lightweight references to the lead ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### LangGraph: supervisor vs swarm

**Supervisor** (`langgraph-supervisor`): central supervisor controls all communication and task delegation via **tool-based handoffs**; workers do not peer-chat. `create_supervisor(..., parallel_tool_calls=False)` by default — set `True` (OpenAI/Anthropic) to hand off to **multiple agents at once**. `output_mode`: `'last_message'` (default) vs `'full_history'` trades token load vs fidelity. Nested supervisors enable multi-level hierarchies. Compile with checkpointer/store for memory ([langgraph-supervisor README](https://github.com/langchain-ai/langgraph-supervisor-py); [create_supervisor API](https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor)). LangChain now also recommends implementing supervisor via **manual tool-calling** for context-engineering control ([langgraph-supervisor README note](https://github.com/langchain-ai/langgraph-supervisor-py)).

**Swarm** (`langgraph-swarm`): peers hand off via `create_handoff_tool`; system tracks **`active_agent`** so the next user turn resumes the last specialist. **Requires a checkpointer** for multi-turn use — without it, the swarm forgets which agent was active and loses history. Default shared `messages` key merges all agent transcripts (privacy/token implication); custom per-agent message keys need state wrappers ([langgraph-swarm README](https://github.com/langchain-ai/langgraph-swarm-py)).

Handoffs in both libraries are implemented as tools returning LangGraph `Command(goto=..., graph=Command.PARENT, update={...})` ([LangGraph JS multi-agent docs](https://github.com/langchain-ai/langgraphjs/blob/86389fa3/docs/docs/agents/multi-agent.md)).

### OpenAI Agents SDK: ownership fork

Two first-class patterns ([OpenAI — Orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration); [OpenAI Agents SDK — multi_agent](https://openai.github.io/openai-agents-python/multi_agent/)):

| Pattern | Control ownership | Mechanism |
| --- | --- | --- |
| **Handoffs** | Specialist becomes active agent for rest of turn | Tools named `transfer_to_<agent_name>`; optional `input_filter`, `input_type`, `is_enabled`, `on_handoff` ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)) |
| **Agents as tools** | Manager keeps final-answer ownership | `Agent.as_tool()` / `asTool()` — bounded specialist calls |

Guardrail asymmetry: **input guardrails apply only to the first agent** in a handoff chain; **output guardrails only to the agent that produces final output**; use tool guardrails for per-tool checks ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)). Nested handoff history compaction is opt-in beta and **does not redact** sensitive tool args/results ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)).

### Google ADK: hierarchy + workflow agents + three interaction modes

Composition primitives ([ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md); [ADK workflow patterns](https://adk.dev/workflows/patterns/)):

- **Hierarchy**: `sub_agents` tree; single-parent rule (`ValueError` on second parent).
- **Workflow agents**: `SequentialAgent` (same `InvocationContext`, `output_key` → state); `ParallelAgent` (distinct `InvocationContext.branch`, **shared** `session.state` — use distinct keys to avoid races); `LoopAgent` (`max_iterations` and/or `escalate=True` to stop; example uses `max_iterations=10`).
- **Interaction**: (a) shared `session.state`; (b) LLM-driven delegation via `transfer_to_agent(agent_name=...)`; (c) explicit `AgentTool` — parent keeps control, child runs as a tool.

`AgentTool` creates an **isolated child session** (does not inherit parent session ID); state deltas can forward back. Prefer `sub_agents` / inline single-turn mode when shared session context is required ([ADK discussion / AgentTool source behavior](https://github.com/google/adk-python/discussions/3456); [ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md)).

### CrewAI (contrast only)

Crews expose **`Process.sequential`** (default; tasks in list order) vs **`Process.hierarchical`** (manager via `manager_llm` or `manager_agent` plans, delegates, validates; tasks not pre-assigned). Separate **Flows** product for event-driven, precise control ([CrewAI Processes](https://docs.crewai.com/en/concepts/processes); [CrewAI GitHub](https://github.com/crewaiinc/crewAI)). Useful as a role/task-oriented abstraction; durability/checkpoint semantics are thinner in public docs than LangGraph.

## 2. Token Economics & NFR Metrics

### Published multipliers (Anthropic Research)

| Interaction class | Token usage vs. chat | Source |
| --- | --- | --- |
| Single agent (typical) | ~**4×** chat | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Multi-agent system | ~**15×** chat | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |

BrowseComp variance decomposition (browsing agents locating hard-to-find info) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)):

- **Token usage alone** explains **~80%** of performance variance.
- Token usage + tool-call count + model choice explain **~95%**.
- Upgrading Sonnet **3.7 → 4** beat **doubling the token budget** on 3.7 (model is an efficiency multiplier on tokens).

Internal research eval: Opus 4 lead + Sonnet 4 subagents beat **single-agent Opus 4 by 90.2%** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

Wall-clock: parallel subagents + parallel tools → **up to 90%** reduction in research time for complex queries ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Cost-tier routing (coordinator / big-plan–small-execute)

Claude Managed Agents cookbook (“coordinator pattern”): frontier coordinator plans/synthesizes; cheap workers do token-heavy web reading in parallel threads. On authors’ runs, **84–98%** of team **input** tokens billed at the **worker** rate while reading volume matched a rigor-matched solo-frontier control ([Claude Cookbook — coordinator pattern](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)). Coordinator has **no tools**; workers scoped to search/fetch — also a security blast-radius choice. Session budget cited as the **fan-out guardrail** in that cookbook’s framing ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

**$ per 1k executions**: not published as a stable multi-agent unit price. Cost is run-dependent (subagent count × per-agent tokens × model mix). [inferred] Order-of-magnitude: if chat ≈ 1× and multi-agent ≈ 15× tokens, multi-agent research queries are roughly mid-teens multiples of chat token spend before tool/vendor margins — use live `usage.list_cost` / provider metering rather than a fixed formula ([Cookbook metering](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Latency / throughput shapes

| Pattern | Latency shape | Notes |
| --- | --- | --- |
| Orchestrator-worker (parallel workers) | ≈ slowest worker + lead plan/synth | Anthropic sync lead still waits on the cohort ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Pipeline | **Sum** of stage latencies | Newsletter example: 5 stages × 2s → **10s** before output ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |
| Handoff / swarm | Serial specialist hops | Each handoff = another model turn |
| Supervisor serial handoffs | Bottleneck at supervisor | Newsletter: if each orchestrator↔worker call ≈ **3s** and **20** workers wait, ceiling ≈ **~7 tasks/s** through the center ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |

Stripe pipeline case (regulated KYC-style review): agent DAG cut average handling time **26%**; reviewers rated outputs **96%** helpful ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Spotify orchestrator-worker ad-planning case cited: **15 minutes → 5 seconds** ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

p50/p95/p99 for multi-agent frameworks: **not published** as vendor SLAs for these topologies.

### Caching, RPM/TPM, back-pressure

> ⚠️ Limited public data available for this dimension. Multi-agent posts emphasize token multipliers and fan-out heuristics, not prompt-cache hit rates, TTLs, or multi-tenant RPM/TPM for agent clusters. Apply provider-level prompt caching and org RPM/TPM caps outside the orchestration layer; CrewAI exposes crew-level `max_rpm` in schema docs but without published cluster benchmarks ([CrewAI crews schema mentions](https://docs.crewai.com/en/concepts/crews)).

LangGraph supervisor `output_mode='last_message'` and `create_forward_message_tool` are explicit **token-saving** controls (avoid re-feeding full worker histories / paraphrasing) ([langgraph-supervisor README](https://github.com/langchain-ai/langgraph-supervisor-py)).

## 3. Distributed Resilience & State

### Durable execution & checkpointing

**LangGraph**: checkpointer saves a state snapshot at each **super-step**; per-node **pending writes** survive sibling failures so resume skips recomputing successful nodes. Threads keyed by `thread_id`. Durability modes include `"exit"` (persist on exit only — faster, weaker mid-crash recovery) and `"sync"` (persist before next step — stronger, slower) ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Production savers: Postgres (sync/async); AWS blog documents DynamoDBSaver + S3 for large payloads (≥ **350 KB** offloaded) ([AWS — LangGraph + DynamoDB](https://aws.amazon.com/blogs/database/build-durable-ai-agents-with-langgraph-and-amazon-dynamodb/)). Swarm **must** compile with a checkpointer for multi-turn `active_agent` continuity ([langgraph-swarm README](https://github.com/langchain-ai/langgraph-swarm-py)).

**Anthropic Research**: resume-from-failure rather than full restart; combine model adaptability with **retry logic and regular checkpoints**; **rainbow deployments** so in-flight agents are not broken by prompt/tool/code updates ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Long-horizon: summarize completed phases to external memory; spawn fresh subagents with clean contexts while retrieving the stored plan ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

**ADK**: session state via `SessionService`; updates should flow through events/`state_delta` for persistence and thread-safety; `temp:` state for invocation-scoped data shared along a parent→sub-agent `InvocationContext` ([ADK State](https://adk.dev/sessions/state/)).

**OpenAI Agents SDK**: `RunState` pause/resume; sessions / `conversation_id` / `previous_response_id` for continuity; handoff input filters unsupported on server-managed conversations — use a separate run with explicit input ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/); [Running agents](https://openai.github.io/openai-agents-python/running_agents/)).

### Loop / recursion guards

| System | Guard | Default / example |
| --- | --- | --- |
| LangGraph | `recursion_limit` → `GraphRecursionError` | Default **1000** super-steps since v1.0.6; overridable per invoke; `RemainingSteps` for proactive degradation ([LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit)) |
| OpenAI Agents SDK | `max_turns` → `MaxTurnsExceeded` | `DEFAULT_MAX_TURNS = **10**`; `None` disables ([run_config.py](https://github.com/openai/openai-agents-python/blob/cdde4d65/src/agents/run_config.py); [Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| ADK `LoopAgent` | `max_iterations` and/or `escalate` | Example **10** ([ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md)) |
| Anthropic Research | Prompt effort budgets | Cap simple queries at 1 agent / 3–10 tools; early bug: **50** subagents on simple queries ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |

### Circuit breakers, distributed locking, Kafka/Temporal

> ⚠️ Limited public data available for this dimension. Primary multi-agent framework docs do not publish circuit-breaker thresholds, half-open probe strategies, or leader-election recipes specific to agent fan-out. Anthropic describes deterministic retries + checkpoints + rainbow deploys, not Temporal/Kafka topologies ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). [inferred] Enterprise deployments typically wrap the agent graph in an external workflow engine (Temporal/Step Functions) for saga-style compensation; that layer is outside these SDKs’ documented multi-agent APIs.

OpenAI local tool concurrency: `max_function_tool_concurrency` can cap parallel local function tools in a turn; default starts all emitted local tool calls (`None`) ([Running agents](https://openai.github.io/openai-agents-python/running_agents/)).

## 4. Enterprise Security & Governance

### Privilege isolation by topology

Multi-agent’s security win is **splitting tools and data access by role**: research agent gets web search; code agent gets a sandbox; customer-facing agent gets user data but not production DB credentials ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Coordinator-with-tool-less planner + tool-scoped workers (Claude cookbook) hardens the blast radius of untrusted web content ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

Risks called out in the newsletter teaser: prompt injection, **context contamination** across agents, and **privilege creep** when agents accumulate tools ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

### Framework-native controls

| Concern | Mechanism | Source |
| --- | --- | --- |
| Tool RBAC / policy | ADK: validate via deterministic `ToolContext` + `before_tool` callbacks / plugins; do not trust model-supplied args alone | [ADK Safety](https://adk.dev/safety/) |
| Code sandbox | Hermetic exec: no network, cleanup between users; Vertex / Agent Runtime sandboxes | [ADK Safety](https://adk.dev/safety/); [ADK code-exec runtime](https://github.com/google/adk-docs/blob/main/docs/integrations/code-exec-agent-runtime.md) |
| Handoff history leakage | OpenAI: nested history **does not redact**; use explicit `input_filter` / custom sanitizers; server-managed convos cannot use filters | [Handoffs](https://openai.github.io/openai-agents-python/handoffs/) |
| Guardrail placement | OpenAI: input guardrails on **first** agent only; output on **final** agent; tool guardrails for each function tool | [Handoffs](https://openai.github.io/openai-agents-python/handoffs/) |
| Swarm shared transcript | Default shared `messages` exposes all agents’ internals unless custom schemas | [langgraph-swarm README](https://github.com/langchain-ai/langgraph-swarm-py) |
| Observability vs privacy | Anthropic: production tracing of **decision patterns / interaction structures** without monitoring conversation contents | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |

### Zero-Trust MCP / PII / SOC2 schemas

> ⚠️ Limited public data available for this dimension. Multi-agent architecture docs rarely specify Zero-Trust MCP mutual auth, PII NER pipelines, or immutable audit-log schemas for agent-to-agent hops. Treat MCP server auth and PII redaction as **cross-cutting platform** concerns (see MCP research topic); for multi-agent, enforce **least-privilege tool allowlists per agent** and audit **which agent** invoked **which tool** with which args.

[inferred] Hierarchical/supervisor topologies are easier to govern (single policy chokepoint) than mesh/swarm peer handoffs where any agent may receive full history.

## 5. Production Failure Modes

| Failure mode | Symptoms / causes | Mitigations (documented) |
| --- | --- | --- |
| **Runaway fan-out** | Early Anthropic agents spawned **50** subagents on simple queries; endless search for nonexistent sources | Effort-scaling rules in lead prompt; explicit stop when sufficient ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Duplicated worker work** | Vague delegation → overlapping searches (e.g., three agents on same supply-chain slice) | Rich task briefs: objective, output format, tools/sources, boundaries ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |
| **Context window truncation** | Plan lost past **200k** tokens | Persist plan to Memory; retrieve on continue; spawn clean-context subagents ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Game of telephone** | Detail lost as results climb hierarchy / through lead paraphrase | Filesystem artifacts + references; LangGraph `forward_message`; `output_mode` choices ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py)) |
| **Sync orchestrator bottleneck** | Lead blocked on slowest worker; cannot steer mid-flight | Accept sync simplicity or invest in async (harder error/state) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Infinite / long loops** | Peer handoffs or ReAct loops never terminate | `max_turns=10` default (OpenAI); `recursion_limit` (LangGraph); `LoopAgent.max_iterations` (ADK) |
| **Bad tool descriptions** | MCP tools with uneven docs send agents down wrong paths | Tool-testing agent that rewrites descriptions → **40%** lower task completion time in Anthropic’s test ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **SEO / source bias** | Early agents preferred content farms over academic PDFs | Source-quality heuristics in prompts; human eval catch ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Deploy skew** | Updating prompts/tools mid-flight breaks stateful agents | Rainbow deployments ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Parallel state races** | ADK `ParallelAgent` children share `session.state` | Distinct output keys ([ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md)) |
| **Hallucinated handoff / tool args** | Model invents agent names or payloads | Schema validation (`input_type` / Pydantic on OpenAI handoffs); ADK `before_tool` checks; find_agent resolution failures should fail closed ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/); [ADK Safety](https://adk.dev/safety/)) |
| **Cascading timeouts** | Slow tool or worker stalls cohort | [inferred] Per-worker timeouts + overall deadline budget; Anthropic lets the model adapt when a tool fails rather than hard-failing the whole run ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |

Compound-error property: a minor step failure can divert the entire trajectory — prototype→production gap is larger than for ordinary software ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

Eval implications: paths are non-deterministic; prefer **outcome / end-state** judges (factuality, citations, completeness, source quality, tool efficiency) over step traces; start with ~**20** real queries; LLM-as-judge with 0.0–1.0 + pass/fail worked for Anthropic ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 6. Enterprise System Design Scenarios

### Decision matrix (cost × latency × ops × security × fit)

| Approach | Cost | Latency | Ops complexity | Security / governance | Best fit |
| --- | --- | --- | --- | --- | --- |
| **Stay single-agent** | Lowest (~1–4× chat) | Lowest serial | Lowest | One allowlist | Coding, shared-context tasks, most CRUD agents ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)) |
| **Pipeline / Sequential DAG** | Moderate (stage sum) | Additive | Medium; excellent auditability | Strong stage contracts | Regulated reviews (Stripe **−26%** AHT, **96%** helpful) ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |
| **Orchestrator-worker (parallel)** | High (~**15×** chat) | ≈ p95 worker + synth; up to **90%** faster vs serial research | High (prompts, tracing, deploys) | Role-scoped tools; lead is chokepoint | Breadth-first research / diligence ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Hierarchical supervisors** | High (summary tax each level) | Deep trees add hops | High | Natural policy layers; detail loss risk | Large domain catalogs (e.g., **80+** agents) ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system); [langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py)) |
| **Handoff / swarm** | Medium–high (full history by default) | Serial specialist turns | Medium; needs checkpointer | History leakage risk | Support triage, conversational ownership transfer ([OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration); [langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)) |
| **Agents-as-tools / AgentTool** | Medium–high | Nested runs under manager | Medium | Manager owns answer + guardrails | Manager synthesis, bounded specialists ([OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration); [ADK](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md)) |

### Capacity planning (what is known vs inferred)

**Confirmed knobs:**

- Fan-out bands: **1 / 2–4 / >10** subagents by complexity; parallel cohort **3–5** common in Anthropic Research ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Token multiplier planning: budget **~15×** chat for multi-agent research-class jobs ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Turn/step caps: OpenAI **10** turns default; LangGraph **1000** super-steps default ([OpenAI run_config](https://github.com/openai/openai-agents-python/blob/cdde4d65/src/agents/run_config.py); [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit)).
- Context plan persistence threshold cited at **200k** tokens ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

**Not published:** multi-tenant concurrent-agent cluster sizes, tokens/sec per topology, or memory footprints for N-worker Research clones.

[inferred] Capacity formula for orchestrator-worker research:

`cost ≈ C_lead(plan+synth) + Σ_i C_worker_i(context_i + tools_i)` with wall-clock ≈ `T_plan + max(T_worker_i) + T_synth` under parallel sync cohorts — multiply by iteration count if the lead re-spawns workers.

### Architecture selection checklist (enterprise)

1. Confirm at least one of: context overflow, parallel independent threads, or permission/tool split ([System Design Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).
2. Choose **ownership**: manager retains answer (**agents-as-tools** / AgentTool / supervisor) vs specialist speaks to user (**handoffs** / swarm) ([OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration)).
3. Prefer **deterministic workflow agents** (Sequential/Parallel/Loop) when stages are known; LLM delegation when routing is open-ended ([ADK patterns](https://adk.dev/workflows/patterns/)).
4. Price the **15×** token tax against task value; use cheap workers for read-heavy legs ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).
5. Ship observability of **interaction structure** before optimizing prompts; evaluate outcomes, not fixed paths ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
6. Defer **A2A** cross-vendor agent networking to the dedicated protocol topic; keep in-process/framework handoffs for v1.

## Sources

- [1] https://newsletter.systemdesign.one/p/multi-agent-system — Neo Kim, Multi-Agent Architectures (primary article; partial paywall)
- [2] https://www.anthropic.com/engineering/multi-agent-research-system — Anthropic engineering: Research multi-agent system
- [3] https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small — Claude Cookbook: coordinator / big-plan–small-execute economics
- [4] https://github.com/langchain-ai/langgraph-supervisor-py — LangGraph Supervisor library README
- [5] https://github.com/langchain-ai/langgraph-swarm-py — LangGraph Swarm library README
- [6] https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor — `create_supervisor` API (`parallel_tool_calls`, `output_mode`)
- [7] https://docs.langchain.com/oss/python/langgraph/checkpointers — LangGraph checkpointing / durability
- [8] https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit — Default recursion limit (1000) and handling
- [9] https://github.com/langchain-ai/langgraphjs/blob/86389fa3/docs/docs/agents/multi-agent.md — LangGraph multi-agent / handoff concepts
- [10] https://openai.github.io/openai-agents-python/handoffs/ — OpenAI Agents SDK handoffs
- [11] https://openai.github.io/openai-agents-python/multi_agent/ — OpenAI Agents SDK agent orchestration patterns
- [12] https://developers.openai.com/api/docs/guides/agents/orchestration — OpenAI orchestration & ownership guidance
- [13] https://openai.github.io/openai-agents-python/running_agents/ — Runner loop, `max_turns`, tool concurrency
- [14] https://github.com/openai/openai-agents-python/blob/cdde4d65/src/agents/run_config.py — `DEFAULT_MAX_TURNS = 10`
- [15] https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md — Google ADK multi-agent systems
- [16] https://adk.dev/workflows/patterns/ — ADK workflow patterns (coordinator, sequential, parallel, hierarchical)
- [17] https://adk.dev/safety/ — ADK safety, tool policy, sandbox guidance
- [18] https://adk.dev/sessions/state/ — ADK session state mechanics
- [19] https://github.com/google/adk-python/discussions/3456 — AgentTool isolated session vs shared `sub_agents` session
- [20] https://docs.crewai.com/en/concepts/processes — CrewAI sequential vs hierarchical processes (contrast)
- [21] https://github.com/crewaiinc/crewAI — CrewAI crews vs flows overview
- [22] https://aws.amazon.com/blogs/database/build-durable-ai-agents-with-langgraph-and-amazon-dynamodb/ — Production checkpoint sizing (DynamoDB/S3 350 KB split)
