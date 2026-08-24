# Research: Agent Architecture

**Date researched**: 2026-08-21
**Sources consulted**: 51

---

## 1. System Topology & Mechanics

The canonical **ReAct** topology is an interleaved `reason -> act -> observe -> reason` loop: the model emits reasoning traces plus task actions, observes tool or environment feedback, and updates the plan on the next turn. In the original paper, this improved HotpotQA and FEVER factual tasks by grounding through a Wikipedia API, and improved ALFWorld and WebShop success rates by absolute **34%** and **10%** over imitation/RL baselines, respectively ([ReAct paper](https://arxiv.org/abs/2210.03629), [Google Research summary](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

In production frameworks, ReAct is usually wrapped in an explicit **control loop** rather than left as prompt-only behavior. The OpenAI Agents SDK runner loops until one of four stopping conditions: final output, tool calls completed and loop continued, handoff to another agent, or `max_turns` exceeded; the run is explicitly defined as one application-level turn ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/)). LangGraph expresses the same pattern as a `StateGraph` with node transitions and super-steps, which makes loops, conditional routing, and checkpoint boundaries explicit ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

The main orchestration topologies now fall into four buckets:

1. **ReAct loop**: flexible but serial; every tool result normally requires another model turn ([ReAct paper](https://arxiv.org/abs/2210.03629), [OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)).
2. **Planner/executor**: a planner creates a multi-step plan, then executors run steps and a replanner updates the remaining plan; LangChain positions this as a cost/latency improvement over plain ReAct because the expensive planner model is not called for every tool invocation ([LangChain planning agents](https://www.langchain.com/blog/planning-agents)).
3. **Deterministic workflow agents**: Google ADK template workflows provide `SequentialAgent`, `ParallelAgent`, and `LoopAgent`, where orchestration logic is deterministic and not delegated to an LLM ([ADK workflow agents](https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/index.md), [ADK sequential agent](https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/sequential-agents.md)).
4. **Parallel DAG planning**: LLMCompiler decomposes work into dependency-aware tasks and reports up to **3.7x** latency speedup, **6.7x** cost savings, and about **9%** accuracy improvement versus ReAct on its benchmark suite ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)).

For **state orchestration**, LangGraph persists graph state as checkpoints at each **super-step** and binds runs into `thread_id` namespaces; it also persists per-node writes inside a super-step so successful siblings need not re-run after a failure ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)). OpenAI exposes four continuation models: client-managed `result.to_input_list()`, client-managed `session`, OpenAI-managed `conversationId`, and lightweight `previousResponseId` chaining ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)). ADK separates **Session** (turn/thread container), **State** (session-scoped scratchpad), and **Memory** (cross-session searchable store) ([ADK sessions overview](https://github.com/google/adk-docs/blob/main/docs/sessions/index.md), [Google Cloud ADK memory blog](https://cloud.google.com/blog/topics/developers-practitioners/remember-this-agent-state-and-memory-with-adk)).

For **agent-to-agent communication**, OpenAI distinguishes **handoffs** from **agents-as-tools**: handoffs transfer ownership of the branch to a specialist, while `agent.asTool()` keeps a manager in control of the final answer ([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)). ADK workflow agents pass a shared invocation context and session state between sub-agents in deterministic pipelines ([ADK sequential agent](https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/sequential-agents.md)). CrewAI Flows use event-driven decorators like `@start`, `@listen`, and `@router` to move structured state through a workflow, and can call agents or crews as nodes in that flow ([CrewAI flows](https://docs.crewai.com/edge/en/concepts/flows), [CrewAI production architecture](https://docs.crewai.com/v1.15.6/en/concepts/production-architecture)).

At the protocol layer, **MCP** standardizes tool, prompt, and resource exchange over **JSON-RPC 2.0**. The 2025-11-25 spec is stateful with capability negotiation during initialization; the 2026-07-28 spec moves to **stateless, self-contained requests** with per-request metadata and optional async task extensions ([MCP 2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25), [MCP 2026-07-28 spec](https://modelcontextprotocol.io/specification/2026-07-28), [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). That matters architecturally because the tool protocol can be load-balanced independently of model runtime state [inferred].

**Control plane / data plane split**: in most deployed agent systems, routing, loop control, checkpointing, approvals, tracing, and rate-limit handling behave like a **control plane**, while model inference, tool execution, and external API I/O behave like a **data plane**; this split is explicit in OpenAI's runner/session abstractions, LangGraph's graph/checkpointer abstractions, Temporal's workflow/activity split, and MCP's host/client/server layering ([OpenAI Agents overview](https://developers.openai.com/api/docs/guides/agents), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Temporal workflows](https://docs.temporal.io/workflows), [MCP 2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25)) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable **p50/p95/p99 end-to-end latency** by framework. The major vendors publish pricing, caching, and rate-limit behavior, but not repeatable user-facing SLA distributions for complete multi-step agent runs.

The most important latency fact is structural: **serial ReAct latency grows roughly with the number of loop iterations**, because each tool result normally triggers another model round-trip; planner/executor and DAG schedulers reduce that by shortening the critical path rather than only making any single model call faster ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) [inferred]. LLMCompiler's published benchmark is the clearest quantified datapoint here: up to **3.7x** lower latency and **6.7x** lower cost than ReAct when tasks can run in parallel ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)).

For **per-run cost**, the generic formula is:

```text
run_cost =
  (uncached_input_tokens / 1_000_000) * input_price +
  (cached_read_tokens / 1_000_000) * cached_input_price +
  (cache_write_tokens / 1_000_000) * cache_write_price +
  (output_tokens / 1_000_000) * output_price +
  tool_surcharges
```

That formula matches OpenAI's explicit cached-input and cache-write billing, Anthropic's prompt-caching billing, and Gemini's cached-input plus storage billing ([OpenAI pricing](https://developers.openai.com/api/docs/pricing), [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Gemini context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching)).

For **OpenAI**, current flagship short-context pricing is: `gpt-5.6-sol` input **$5.00/M**, cached input **$0.50/M**, cache writes **$6.25/M**, output **$30.00/M**; `gpt-5.6-terra` is **$2.00/M**, **$0.20/M**, **$2.50/M**, **$12.00/M**; `gpt-5.6-luna` is **$0.20/M**, **$0.02/M**, **$0.25/M**, **$1.20/M** ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)). On GPT-5.6 and later, cache writes cost **1.25x** uncached input and cache reads cost **0.1x** uncached input; cached tokens still count toward TPM limits ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

For **Anthropic**, Sonnet 5 pricing is base input **$2/M**, 5-minute cache writes **$2.50/M**, 1-hour cache writes **$4/M**, cache hits **$0.20/M**, output **$10/M**; Sonnet 4.6 is **$3/M**, **$3.75/M**, **$6/M**, **$0.30/M**, **$15/M** ([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Anthropic caching is more explicit than OpenAI's: you can place up to **4** breakpoints, the default TTL is **5 minutes**, an optional **1 hour** TTL exists, and the cache lookback window is **20 blocks** from a breakpoint ([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

For **Gemini**, implicit caching is automatic on Gemini 2.5+; explicit caching adds guaranteed cached-input discounts plus a storage charge. On `gemini-3.1-pro-preview`, standard paid pricing is input **$2.00/M** for prompts up to 200k tokens, cached input **$0.20/M**, output **$12.00/M**, and explicit cache storage **$4.50 per 1M tokens per hour** ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Gemini context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching)).

**Cache break-even math**:

- OpenAI GPT-5.6+ and Anthropic 5-minute caches both use **1.25x write** and **0.1x read**, so a cached prefix becomes cheaper on the **first reuse** (the second total use) ([OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred].
- Anthropic 1-hour cache writes cost **2x** base input, so they become cheaper on the **second reuse** (the third total use) ([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred].

**Worked example for 1,000 executions**: assume an 8-turn ReAct loop with a stable **3,000-token** system/tool prefix, **500 fresh input tokens** per turn, and **300 output tokens** per turn. Using official pricing, the total comes out to about **$48.50** on `gpt-5.6-terra` with caching versus **$84.80** without caching, and about **$43.70** on Claude Sonnet 5 with 5-minute caching versus **$80.00** without caching ([OpenAI pricing](https://developers.openai.com/api/docs/pricing), [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred].

**Routing implications**: the cheapest pattern is usually **strong planner, cheap executor**. A high-capability model can plan once, then a cheaper model executes many bounded tool or synthesis steps; this is exactly the efficiency claim behind plan-and-execute architectures and parallel planners ([LangChain planning agents](https://www.langchain.com/blog/planning-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) [inferred]. None of OpenAI Agents SDK, ADK, LangGraph, or CrewAI currently document a first-party automatic "complexity router"; routing policy is generally application logic [inferred].

For **throughput and back-pressure**, official docs are clearer than for latency:

- **OpenAI** rate limits are expressed via RPM/TPM/RPD/TPD and exposed in `x-ratelimit-*` headers; the docs avoid publishing one static per-model table because limits are account- and tier-specific ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
- **Anthropic** publishes tier tables and uses a **token bucket** algorithm; for many current models, `cache_read_input_tokens` do **not** count toward ITPM, which materially increases effective throughput ([Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)).
- **Gemini** limits are project-level RPM/TPM/RPD plus spend-based throttles, and cached tokens still count toward standard token limits ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [Gemini context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching)).

Anthropic's docs provide the strongest published numeric throughput example: with a **2,000,000 ITPM** limit and **80% cache hit rate**, an application can effectively process about **10,000,000 total input tokens/minute** because cached reads do not count toward ITPM on most models ([Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)).

## 3. Distributed Resilience & State

The most mature **durable execution** model in this source set is Temporal. A Temporal workflow has exclusive access to its local state, writes an append-only **Event History**, and resumes progress by **replay** rather than by restoring an in-memory snapshot. Workflow code re-executes deterministically; external side effects should live in **Activities**, whose recorded results are reused during replay instead of being re-run ([Temporal workflow execution](https://docs.temporal.io/workflow-execution), [Temporal workflows](https://docs.temporal.io/workflows)). Temporal also exposes **Signals** (async writes), **Queries** (read-only state access), and **Updates** (synchronous tracked writes) as first-class control channels ([Temporal message passing](https://docs.temporal.io/encyclopedia/workflow-message-passing)).

LangGraph implements a lighter but still explicit durability model. A checkpointer stores a full `StateSnapshot` at each **super-step** and also stores **pending writes** from individual nodes within a super-step, so a failure in one sibling does not force all siblings to re-run ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). The `thread_id` is the primary key for retrieval and resume, and durability mode can be tuned:

- `sync`: persist before the next step; strongest durability, more overhead.
- `async`: default; persist while the next step runs; better latency, some crash risk.
- `exit`: persist on exit only; fastest, weakest mid-run recovery.

([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers))

OpenAI's durability story is **session- and state-centric** rather than workflow-history-centric. The docs recommend `session` as the default when you want durable memory, resumable approval flows, or storage your application controls; server-managed `conversationId` or `previousResponseId` can reduce client bookkeeping but move more state responsibility to OpenAI ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)). Human approval pauses return a serializable **RunState**, which can be stored and resumed later as the same run ([OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

Google ADK provides the clearest published **concurrency control** among the agent frameworks here. `DatabaseSessionService` persists sessions to PostgreSQL/MySQL/MariaDB/SQLite and uses:

- **in-process locking** to serialize `append_event` updates inside one process
- **row-level locking** via `SELECT ... FOR UPDATE` for PostgreSQL/MySQL/MariaDB across processes

([ADK session docs](https://github.com/google/adk-docs/blob/main/docs/sessions/session/index.md), [ADK session page](https://adk.dev/sessions/session/))

ADK also migrated its session schema from pickle-based `v0` to JSON-based `v1` in Python **v1.22.0+**, which improves portability and auditability ([ADK session migration](https://github.com/google/adk-docs/blob/main/docs/sessions/session/migrate.md)).

CrewAI Flows add workflow persistence through `@persist`, saving state after workflow steps and allowing either **resume** on the same lineage or **fork** from a prior state ID into a new lineage. The docs explicitly recommend PostgreSQL rather than SQLite for multi-instance production deployment ([CrewAI flows](https://docs.crewai.com/edge/en/concepts/flows), [CrewAI production architecture](https://docs.crewai.com/v1.15.6/en/concepts/production-architecture)).

**Distributed locking**:

- Strongly documented: ADK `DatabaseSessionService` row-level locks; Temporal workflow-execution ownership ([ADK session page](https://adk.dev/sessions/session/), [Temporal workflow execution](https://docs.temporal.io/workflow-execution)).
- Weakly documented: LangGraph checkpointers and OpenAI sessions describe persisted state and replay, but do not publicly specify a first-party distributed lock manager for multi-writer updates [inferred].

**Circuit breakers and graceful degradation**:

> ⚠️ Limited public data available for framework-native circuit-breaker implementations. The frameworks document retries, resume, and approvals far better than threshold tuning, half-open probes, or bulkhead implementations.

The closest official back-pressure primitives are provider rate-limit headers plus resumable workflows: OpenAI returns `Retry-After` and `x-ratelimit-*` headers; Anthropic uses a token bucket and `retry-after`; Gemini surfaces `RESOURCE_EXHAUSTED` and recommends rate reduction/retry ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits), [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits), [Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)).

## 4. Enterprise Security & Governance

For **Zero-Trust MCP**, the strongest primary source is the MCP authorization spec. For HTTP transports, MCP adopts **OAuth 2.1**, requires **Protected Resource Metadata** discovery, requires clients to use **Resource Indicators** (`resource` parameter), requires **PKCE** with `S256` when technically capable, and requires HTTPS on authorization server endpoints; `stdio` transports should instead source credentials from the environment ([MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization), [MCP authorization security considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). This is more explicit than most framework docs about server authentication and least-privilege scoping.

For **capability negotiation**, the 2025 MCP spec negotiates client/server features such as tools, resources, prompts, sampling, roots, and elicitation during initialization, while the 2026-07-28 spec moves capability metadata inline on each request and deprecates roots/sampling/logging under a formal lifecycle policy ([MCP 2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25), [MCP 2026-07-28 spec](https://modelcontextprotocol.io/specification/2026-07-28), [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

For **tool-level authorization**, Anthropic exposes the most concrete public knob in this source set: tool definitions support `allowed_callers`, `defer_loading`, and `strict`, and strict tool use guarantees that tool names and inputs match the declared JSON Schema via grammar-constrained sampling ([Anthropic tool definition docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools), [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use), [Anthropic SDK tool schema source](https://github.com/anthropics/anthropic-sdk-python/blob/04b468da/src/anthropic/types/tool_param.py)). OpenAI's primary public governance control is different: **human review pauses** before sensitive tool calls, and that pause can happen even inside handoffs or nested agent-as-tool executions ([OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

For **schema enforcement against tool misuse**, OpenAI recommends `strict: true` on function tools and requires `additionalProperties: false` plus all fields in `required`; otherwise function calling may fall back to best-effort behavior depending on API surface ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)). Anthropic's strict tool use gives a stronger public guarantee: valid tool name plus schema-valid tool inputs ([Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)).

For **auditability**, OpenAI Agents SDK includes built-in tracing by default for model calls, tool calls, handoffs, guardrails, and custom spans, and those traces are inspectable in the Traces dashboard ([OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/), [OpenAI observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)). ADK stores event history inside sessions ([ADK sessions overview](https://github.com/google/adk-docs/blob/main/docs/sessions/index.md)). Temporal keeps a complete event history for replay and debugging ([Temporal workflows](https://docs.temporal.io/workflows)). MCP 2026 deprecates protocol-level logging in favor of `stderr` for `stdio` and **OpenTelemetry** for structured cloud observability ([MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

For **PII redaction and sandbox isolation**:

> ⚠️ Limited public data available for first-party PII redaction pipelines, built-in classifier stacks, or hard isolation details such as container-vs-WASM-vs-process guarantees across these frameworks.

What is public is mainly the governance shell around sensitive actions: schema constraints, approvals, session persistence, and tracing ([OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use), [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)).

## 5. Production Failure Modes

### Context-window degradation

Long-running agent loops accumulate tool schemas, conversation history, and prior results. OpenAI explicitly exposes transcript compaction options such as `nest_handoff_history`, while Anthropic exposes prompt caching and mid-conversation system-message strategies that preserve cacheable prefixes instead of rewriting top-level system text ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/), [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). Neither source set publishes a universal automatic "context decay detector", so **summary insertion, cache-stable prefixes, and planner/executor splitting** remain architectural mitigations rather than guaranteed platform features [inferred].

### Infinite execution loops

OpenAI's runner has an explicit `max_turns` guard and throws `MaxTurnsExceeded` when exceeded ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/)). LangGraph enforces `recursion_limit` by super-step and raises `GraphRecursionError`; current docs say the default is **1000** steps and recommend proactive handling with `RemainingSteps` rather than only catching the exception afterward ([LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph recursion-limit error guide](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)). ADK's `LoopAgent` requires a termination condition such as `max_iterations` or `exit_loop` ([ADK workflow agents](https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/index.md), [Google ADK multi-agent codelab](https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/3-developing-agents/build-a-multi-agent-system-with-adk)).

### State drift and replay divergence

LangGraph replay re-executes nodes **after** the selected checkpoint; those nodes may re-trigger LLM calls, API requests, or interrupts, so node logic must be idempotent ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Temporal solves the same problem by replaying workflow code against event history while **not** re-executing Activities ([Temporal workflows](https://docs.temporal.io/workflows)). OpenAI documents one subtle edge: if a retry policy approves unsafe replay after a request may already have reached the provider, provider-side work can repeat even though the SDK preserves a single durable input item in its local state model ([OpenAI results](https://openai.github.io/openai-agents-python/results/)).

### Cascading timeouts and retries

> ⚠️ Limited public data available for vendor-published timeout-budget recipes or agent-specific bulkhead patterns.

The official primitives are still useful: Temporal isolates failure-prone external work into Activities with retry policies ([Temporal workflow execution](https://docs.temporal.io/workflow-execution)); OpenAI, Anthropic, and Gemini all document `Retry-After`/backoff behavior for rate or transient failures ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits), [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits), [Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)). Architecturally, that implies **deadline propagation and bounded retries per tool** should live outside the model loop [inferred].

### Hallucinated tool parameters

OpenAI's strongest mitigation is **strict function calling** with JSON Schema ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)). Anthropic's strongest mitigation is **strict tool use**, which guarantees valid tool names and schema-valid inputs ([Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)). MCP also standardizes structured tool schemas, which reduces ambiguity at the protocol boundary ([MCP 2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25)). If strict validation is unavailable, the fallback pattern is validate -> surface model-visible error -> retry with corrected arguments [inferred].

### Incident and post-mortem data

> ⚠️ Limited public data available for detailed agent-framework incident post-mortems. The public material is overwhelmingly design docs and usage guides, not RCA-quality production writeups.

## 6. Enterprise System Design Scenarios

### 6.1 Architecture selection matrix

The following matrix synthesizes the documented behavior of ReAct, planner/executor, and DAG-style parallel planners ([ReAct paper](https://arxiv.org/abs/2210.03629), [LangChain planning agents](https://www.langchain.com/blog/planning-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) [inferred]:

| Pattern | Best fit | Latency profile | Cost profile | Ops complexity | Security/governance fit |
| --- | --- | --- | --- | --- | --- |
| ReAct loop | Open-ended support/copilot tasks | Worst when many serial tool calls | High if powerful model used every turn | Low to medium | Good only with turn caps, approvals, and strict schemas |
| Planner + executor | Multi-step tasks with moderate branching | Better than ReAct when planner is amortized | Lower if executor uses cheaper model | Medium | Good because approval and logging can sit at executor boundaries |
| Parallel DAG planner | Research, ETL, multi-source retrieval, independent subtasks | Best when tasks parallelize; LLMCompiler reports up to 3.7x speedup | Best when repeated planner calls are avoided; LLMCompiler reports up to 6.7x savings | High | Strong if each task edge carries explicit schema, timeouts, and audit IDs |

### 6.2 Reference deployment patterns

**Pattern A: User-facing SaaS copilot**

- Runtime: OpenAI Agents SDK or ADK `SequentialAgent`/graph workflow for predictable turn handling ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [ADK workflow agents](https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/index.md)).
- State: client-managed session or ADK `DatabaseSessionService` for persistence ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [ADK session page](https://adk.dev/sessions/session/)).
- Governance: strict tool schemas plus approvals for side-effecting actions ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)).

**Pattern B: Long-running back-office automation**

- Runtime: Temporal for durable workflow orchestration, with LangGraph/ADK/CrewAI embedded as task-level logic ([Temporal workflows](https://docs.temporal.io/workflows), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [CrewAI production architecture](https://docs.crewai.com/v1.15.6/en/concepts/production-architecture)).
- Why: event history, replay, Signals/Queries/Updates, and explicit retry policies are stronger primitives than an in-memory agent loop for jobs that can run for hours or require humans in the middle ([Temporal workflow execution](https://docs.temporal.io/workflow-execution), [Temporal message passing](https://docs.temporal.io/encyclopedia/workflow-message-passing)).

**Pattern C: Parallel research or analysis engine**

- Runtime: DAG planner with explicit dependency graph and parallel execution ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)).
- Why: when subtasks are independent, serial ReAct wastes both latency and expensive planner-model invocations [inferred].

### 6.3 Capacity-planning formulas

Useful first-order planning formulas:

```text
max_runs_per_minute ~= min(
  provider_rpm / avg_model_turns_per_run,
  provider_tpm / avg_total_tokens_per_run
)
```

For Anthropic-style cache-aware limits, a better approximation is:

```text
effective_runs_per_minute ~= min(
  rpm / turns_per_run,
  itpm / (uncached_input_tokens_per_run + cache_write_tokens_per_run)
)
```

because `cache_read_input_tokens` do not count toward ITPM on most current Claude models ([Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)) [inferred].

### 6.4 Practical conclusions

1. If your workflow is mostly **serial tool use**, ReAct is easy but becomes the worst option fastest on both latency and cost.
2. If you can separate **planning from execution**, that is usually the first production optimization with the highest ROI.
3. If you can expose **independent subtasks**, move to DAG scheduling; that is where the only strong published parallel-agent benchmark in this source set shows material wins.
4. If state must survive crashes, approvals, or multi-hour pauses, pair the agent framework with a true durable state layer rather than relying on transcript replay alone.

## Sources

- [1] https://arxiv.org/abs/2210.03629 - ReAct: primary paper on interleaved reasoning and acting.
- [2] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ - Google Research summary of ReAct results and design.
- [3] https://developers.openai.com/api/docs/guides/agents/running-agents - OpenAI runtime loop, continuation strategies, and sessions.
- [4] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI Agents SDK loop details including `max_turns`.
- [5] https://developers.openai.com/api/docs/guides/agents/orchestration - Handoffs vs agents-as-tools.
- [6] https://developers.openai.com/api/docs/guides/agents - OpenAI Agents overview and runtime responsibilities.
- [7] https://developers.openai.com/api/docs/guides/prompt-caching - OpenAI prompt caching semantics, pricing multipliers, and rate-limit behavior.
- [8] https://developers.openai.com/api/docs/pricing - OpenAI model and tool pricing.
- [9] https://developers.openai.com/api/docs/guides/rate-limits - OpenAI rate-limit dimensions and response headers.
- [10] https://developers.openai.com/api/docs/guides/function-calling - OpenAI function tool schemas and strict mode.
- [11] https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat - OpenAI structured outputs and schema guarantees.
- [12] https://developers.openai.com/api/docs/guides/agents/guardrails-approvals - OpenAI approvals and run-pause lifecycle.
- [13] https://openai.github.io/openai-agents-python/human_in_the_loop/ - OpenAI HITL resume semantics.
- [14] https://openai.github.io/openai-agents-python/results/ - OpenAI run state, resumability, and replay notes.
- [15] https://openai.github.io/openai-agents-python/tracing/ - OpenAI built-in tracing.
- [16] https://developers.openai.com/api/docs/guides/agents/integrations-observability - OpenAI tracing and observability guidance.
- [17] https://docs.langchain.com/oss/python/langgraph/checkpointers - LangGraph checkpoints, pending writes, and durability modes.
- [18] https://docs.langchain.com/oss/python/langgraph/persistence - LangGraph short-term vs long-term memory.
- [19] https://docs.langchain.com/oss/python/langgraph/graph-api - LangGraph super-steps and recursion limit handling.
- [20] https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT - LangGraph troubleshooting for runaway loops.
- [21] https://www.langchain.com/blog/planning-agents - Plan-and-execute and planner/executor design.
- [22] https://doi.org/10.48550/arxiv.2312.04511 - LLMCompiler paper with latency/cost/accuracy benchmarks.
- [23] https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/index.md - ADK workflow agent topologies.
- [24] https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/sequential-agents.md - ADK sequential workflow semantics and shared invocation context.
- [25] https://github.com/google/adk-docs/blob/main/docs/sessions/index.md - ADK Session, State, and Memory model.
- [26] https://github.com/google/adk-docs/blob/main/docs/sessions/session/index.md - ADK persistent session storage and locking semantics.
- [27] https://adk.dev/sessions/session/ - ADK session-service deployment guidance.
- [28] https://github.com/google/adk-docs/blob/main/docs/sessions/session/migrate.md - ADK JSON session schema migration.
- [29] https://cloud.google.com/blog/topics/developers-practitioners/remember-this-agent-state-and-memory-with-adk - ADK memory and persistence trade-offs.
- [30] https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/3-developing-agents/build-a-multi-agent-system-with-adk - ADK loop and parallel workflow examples.
- [31] https://ai.google.dev/gemini-api/docs/generate-content/caching - Gemini implicit/explicit caching behavior.
- [32] https://ai.google.dev/gemini-api/docs/interactions/caching - Gemini implicit caching thresholds.
- [33] https://ai.google.dev/gemini-api/docs/pricing - Gemini model and context-cache pricing.
- [34] https://ai.google.dev/gemini-api/docs/rate-limits - Gemini RPM/TPM/RPD and spend-based limits.
- [35] https://ai.google.dev/gemini-api/docs/troubleshooting - Gemini 429 handling and retries.
- [36] https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching - Google Cloud explanation of cache discount/storage billing.
- [37] https://platform.claude.com/docs/en/build-with-claude/prompt-caching - Anthropic prompt caching semantics, pricing, thresholds, and lookback window.
- [38] https://platform.claude.com/docs/en/api/rate-limits - Anthropic RPM/ITPM/OTPM limits and token bucket behavior.
- [39] https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools - Anthropic tool definition schema.
- [40] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use - Anthropic strict tool use guarantees.
- [41] https://github.com/anthropics/anthropic-sdk-python/blob/04b468da/src/anthropic/types/tool_param.py - Anthropic SDK source for tool parameters such as `strict` and `allowed_callers`.
- [42] https://docs.crewai.com/edge/en/concepts/flows - CrewAI flow execution and persistence concepts.
- [43] https://docs.crewai.com/v1.15.6/en/concepts/production-architecture - CrewAI production guidance and persistence recommendations.
- [44] https://modelcontextprotocol.io/specification/2025-11-25 - MCP stateful spec, roles, and capability negotiation.
- [45] https://modelcontextprotocol.io/specification/2026-07-28 - MCP stateless request model.
- [46] https://modelcontextprotocol.io/specification/2026-07-28/changelog - MCP deprecations, tasks extension, and observability changes.
- [47] https://modelcontextprotocol.io/specification/draft/basic/authorization - MCP OAuth 2.1 authorization profile.
- [48] https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations - MCP transport/auth security requirements.
- [49] https://docs.temporal.io/workflow-execution - Temporal durable workflow execution and replay.
- [50] https://docs.temporal.io/workflows - Temporal event history and replay semantics.
- [51] https://docs.temporal.io/encyclopedia/workflow-message-passing - Temporal Signals, Queries, and Updates.
