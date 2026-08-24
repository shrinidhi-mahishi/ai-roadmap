# Research: Planning & Reasoning - Decomposition, reflection, verification, replanning

**Date researched**: 2026-08-21
**Sources consulted**: 38

---

## 1. System Topology & Mechanics

The baseline planning topology in current agent systems is still the serial `ReAct` loop: `reason -> act -> observe -> reason`. The original ReAct paper frames reasoning traces as a way to maintain a working plan while tool/environment feedback updates the next step, and reports gains on both knowledge tasks and interactive environments ([ReAct paper](https://arxiv.org/abs/2210.03629), [Google Research summary](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

Modern orchestration stacks make that loop explicit. `OpenAI Agents SDK` runs a turn loop that keeps going until final output, tool execution plus continuation, agent handoff, or `max_turns` exhaustion; `LangGraph` expresses loops, conditional routing, and checkpoints as graph structure rather than hidden prompt behavior ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/), [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

The main decomposition upgrade over plain ReAct is `planner/executor`. LangChain's planning-agents design describes a planner that emits a multi-step plan, executors that carry out individual steps, and a replanner that updates the remaining plan after new evidence arrives ([LangChain planning agents](https://www.langchain.com/blog/planning-agents)). `LLMCompiler` pushes the same idea further by compiling tasks into a dependency-aware DAG so independent steps can run in parallel instead of waiting for a full serial thought-action cycle ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)).

`Reflection` and `verification` are present in current public systems more as explicit control nodes than as one universal first-party API. In the `LangGraph` agentic RAG tutorial, a grading node checks whether retrieved documents are relevant, decides whether the question should be rewritten, and only then routes to answer generation; that is a concrete verifier-plus-replanner loop even though it is retrieval-specific ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)). Azure AI Search agentic retrieval similarly lets an LLM decompose a query into subqueries, execute them in parallel, and return an activity log plus references, making the retrieval plan inspectable instead of opaque ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

`OpenAI` and `Anthropic` reasoning/tool stacks add another layer: reasoning can happen within one model turn before or between tool calls, but the surrounding application still owns execution, validation, and continuation. OpenAI states that reasoning models use internal reasoning tokens and can think between tool calls; Anthropic's thinking mode emits `thinking` blocks that must be preserved across the tool loop ([OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning), [Anthropic Extended Thinking with Tool Use](https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use)).

The practical topology split is therefore:

- `ReAct`: reasoning and execution tightly interleaved each turn.
- `Planner/executor`: expensive planning amortized across multiple bounded actions.
- `Verifier/replanner`: a checker or grading node decides whether to continue, rewrite, or stop.
- `Parallel DAG planning`: decomposition plus dependency scheduling for shorter critical paths.

That taxonomy is a synthesis across the cited runtime and retrieval systems rather than one vendor's formal classification ([LangChain planning agents](https://www.langchain.com/blog/planning-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511), [LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable `p50/p95/p99` latency of full planning-and-replanning workloads across vendors. The public material is much stronger on structural trade-offs, pricing units, and benchmark deltas than on production SLA percentiles.

The primary economic fact is structural: serial ReAct pays for another model turn after nearly every tool result, while planner/executor and DAG schedulers try to reduce both `number_of_planner_calls` and the wall-clock critical path ([OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents), [LangChain planning agents](https://www.langchain.com/blog/planning-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) [inferred].

`LLMCompiler` provides the clearest public benchmark in this local source set for decomposition economics: up to `3.7x` lower latency, `6.7x` lower cost, and about `9%` higher accuracy than ReAct on its benchmark suite when tasks can be parallelized ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)). That is the strongest evidence here that decomposition is not just a quality technique; it is a first-order NFR optimization.

For provider-side reasoning cost, OpenAI is the clearest source: reasoning tokens are billable output tokens, occupy context-window space, and reasoning effort is an explicit knob from lower-effort to higher-effort modes depending model family ([OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning)). `OpenAI Agents SDK` surfaces `reasoning_tokens` in per-request usage entries, and `CrewAI` exposes aggregated flow metrics including `reasoning_tokens`, `cached_prompt_tokens`, and `cache_creation_tokens` ([OpenAI usage](https://openai.github.io/openai-agents-python/usage/), [OpenAI usage reference](https://openai.github.io/openai-agents-python/ref/usage/), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)).

For managed decomposition in retrieval, Azure publishes a concrete cost example: `2,000` agentic retrievals with `3` subqueries, `50` reranked chunks per subquery, and `500` tokens per chunk consume `150M` reranking tokens, with the example totaling `$3.30` for reranking plus `$1.02` for query planning, or `$4.32` combined under the hypothetical rates shown ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

Useful first-order formulas:

```text
planning_run_cost
  ~= planner_tokens
   + Σ(executor_tokens)
   + Σ(verifier_or_grader_tokens)
   + replanning_tokens
   + tool_or_retrieval_surcharges
```

([OpenAI usage](https://openai.github.io/openai-agents-python/usage/), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred]

```text
critical_path_latency
  ~= planning_latency
   + max(parallel_step_durations)
   + verification_latency
   + answer_synthesis_latency
```

([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview), [LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)) [inferred]

The cheapest routing pattern is usually `strong planner, cheaper bounded executors`, because the expensive reasoning model is called fewer times than in a pure ReAct loop ([LangChain planning agents](https://www.langchain.com/blog/planning-agents), [LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) [inferred]. Public docs in this local source set do not show a first-party universal complexity router across LangGraph, OpenAI Agents SDK, ADK, and CrewAI; routing remains application logic or framework composition ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/), [ADK multi-agent workflows](https://adk.dev/agents/multi-agents/), [CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture)) [inferred].

## 3. Distributed Resilience & State

Planning systems are only reliable if they persist enough state to resume with the same remaining plan, verifier outputs, and tool results. `LangGraph` is the clearest public checkpoint model in this local source set: it saves graph state at each super-step, keys runs by `thread_id`, and persists pending writes from successful sibling nodes within the same super-step ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

That checkpoint model matters directly for replanning. If a verifier node decides "rewrite query," "retry tool," or "change branch," the graph can continue from persisted state rather than rebuilding the whole run from scratch [inferred]. The same architectural idea appears in the `LangGraph` agentic RAG tutorial, where retrieval, grading, rewrite, and generation are separate stages in a guarded loop ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

`OpenAI Agents SDK` persists state differently: session history is automatically retrieved before each run and stored after each run, while approval pauses return a serializable `RunState` that can be resumed later as the same run ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)). That is enough for multi-step planning loops with human verification, but the docs explicitly point users toward external durable orchestrators such as `Dapr`, `Temporal`, `Restate`, and `DBOS` for longer-lived or failure-tolerant execution ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`Google ADK` makes planning-state separation explicit: `Session` is the thread container, `State` is session-scoped scratch data, and `Memory` is a distinct cross-session store. For concurrency, `DatabaseSessionService` uses in-process locking and row-level `SELECT ... FOR UPDATE` on PostgreSQL/MySQL/MariaDB ([ADK sessions overview](https://adk.dev/sessions/), [ADK session service](https://adk.dev/sessions/session/), [ADK memory](https://adk.dev/sessions/memory/)). That is the strongest documented multi-writer control plane among the current framework sources.

Provider-native reasoning loops also have a minimum replay contract. OpenAI requires reasoning items returned alongside tool calls to be passed back with tool outputs for reasoning models, and Anthropic requires prior `thinking` or `redacted_thinking` blocks to be preserved when thinking mode is enabled ([OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling), [Anthropic Extended Thinking with Tool Use](https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use)). In practice, this means the natural checkpoint boundary for a planning agent is `before and after each tool/verifier step`, not only at user-visible turns [inferred].

> ⚠️ Limited public data available for provider-internal checkpoint engines, exactly-once semantics, or replay journals behind hosted reasoning loops. The public sources document resumability and state artifacts, but not deep workflow-engine internals.

## 4. Enterprise Security & Governance

Planning agents have a distinct governance problem: `verification` of task correctness is not the same thing as `authorization` for a side effect. A verifier can decide that a plan step is internally coherent while the organization still needs schema validation, approval, RBAC, and audit before execution [inferred].

The strongest documented execution gate in this local source set is `OpenAI Agents SDK` approvals. `needs_approval` can pause function tools, `Agent.as_tool()`, `ShellTool`, and `ApplyPatchTool`; MCP integrations can also require approval, and sticky decisions can apply for the rest of a run ([OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [OpenAI MCP](https://openai.github.io/openai-agents-python/mcp/), [OpenAI MCP server reference](https://openai.github.io/openai-agents-python/ref/mcp/server/)). That gives a clean governance pattern for "plan freely, execute guarded."

`CrewAI` exposes a complementary pattern: task guardrails validate outputs, structured outputs make inter-step handoffs machine-checkable, and `@human_feedback` supports approval or revision loops in workflows ([CrewAI production architecture](https://docs.crewai.com/en/concepts/production-architecture), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows), [CrewAI human feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows)). This is closer to explicit verifier nodes than to one monolithic agent loop [inferred].

For tool-connected planning systems, schema rigor matters because a plan step can be syntactically valid but semantically dangerous. OpenAI recommends `strict: true` for function tools and structured outputs, while Anthropic's `strict` tool use guarantees schema-valid tool names and inputs ([OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI Structured Outputs Guide](https://developers.openai.com/api/docs/guides/structured-outputs), [Anthropic Strict Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)). This narrows the failure class from "invalid JSON" to "valid but wrong business action" [inferred].

Prompt-injection guidance is especially relevant to reflection and verification loops because verifier nodes often read tool outputs, retrieved passages, or browser content. OpenAI advises against concatenating untrusted data into developer instructions, and Anthropic recommends passing third-party content only in `tool_result` blocks and optionally screening it with a smaller model first ([OpenAI Safety in Building Agents](https://developers.openai.com/api/docs/guides/agent-builder-safety), [Anthropic Mitigate Jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)).

For cross-system planning via tools, `MCP` provides the clearest Zero-Trust baseline: OAuth `2.1`, Protected Resource Metadata, Resource Indicators, and PKCE with `S256` are explicit requirements when authorization is in scope ([MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization), [MCP auth security considerations](https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations)). Azure agentic retrieval adds a lighter governance datapoint by returning references and an activity log, which improves auditability of the retrieval plan ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

> ⚠️ Limited public data available for first-party PII redaction of intermediate reasoning traces, immutable audit schemas for replanning decisions, or built-in RBAC hierarchies over planner/verifier nodes.

## 5. Production Failure Modes

`Infinite execution loops` are the most obvious planning failure. `OpenAI Agents SDK` enforces `max_turns`, `LangGraph` enforces `recursion_limit` and raises `GraphRecursionError`, and `Google ADK` loop agents rely on explicit termination conditions such as `maxIterations` / `max_iterations` ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/), [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [LangGraph recursion-limit error guide](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT), [ADK LoopAgent](https://adk.dev/api-reference/typescript/interfaces/LoopAgentConfig.html), [ADK LoopAgent Java](https://adk.dev/api-reference/java/com/google/adk/agents/LoopAgent.html)).

`Replanning storms` are a subtler variant: a verifier repeatedly decides to rewrite, retry, or decompose further without materially improving the answer. The `LangGraph` retrieval-grading pattern is powerful precisely because it makes those branches explicit, but it also means teams need branch caps, retry budgets, or stop rules around rewrite loops [inferred] ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag), [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

`Silent token burn` is the canonical reasoning-model failure mode. OpenAI warns that reasoning models can return `status="incomplete"` if reasoning plus visible output exhausts the context window or `max_output_tokens`, and recommends reserving at least `25,000` tokens for initial workload characterization ([OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning)).

`Schema-valid but wrong actions` remain a planning hazard. Strict tool/JSON modes prevent malformed arguments, but they do not guarantee that the chosen values are correct, authorized, or aligned with user intent ([OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI Structured Outputs Guide](https://developers.openai.com/api/docs/guides/structured-outputs), [Anthropic Strict Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)). This is why verification layers usually need business-rule checks or human review, not just parser success [inferred].

`State drift` is another multi-step failure mode. OpenAI requires reasoning items to be replayed with tool results, and Anthropic can reject the next request if prior thinking context is altered; dropping or mutating these artifacts during retries can change the agent's effective plan or cause hard failure ([OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling), [Anthropic Extended Thinking with Tool Use](https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use)).

`Over-decomposition` is the retrieval/planning analogue of tool thrash. Azure states directly that agentic retrieval adds latency and extra planning/reranking work compared with single-query retrieval, so decomposing into too many subqueries can increase cost and noise rather than improve grounding ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred].

`Long-horizon brittleness` is supported by benchmark evidence beyond one-step tool calling. The BFCL paper concludes that memory, dynamic decision-making, and long-horizon reasoning remain open challenges even as single-turn function calling improves ([BFCL Paper](https://proceedings.mlr.press/v267/patil25a.html)). `Lost in the Middle` and `RULER` add that long context alone does not solve multi-step reasoning quality, especially when key evidence is buried in the middle or context length keeps growing ([Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/), [RULER](https://arxiv.org/abs/2404.06654)).

> ⚠️ Limited public data available for detailed production post-mortems focused specifically on planner/verifier agents, reflection collapse, or replanning-loop outages.

## 6. Enterprise System Design Scenarios

### 6.1 Pattern matrix

| Pattern | Best fit | Strongest documented strengths | Main trade-offs |
| --- | --- | --- | --- |
| `ReAct loop` | Open-ended support/copilot tasks with moderate tool use | Flexible interleaving of reasoning and action; simple runtime model ([ReAct paper](https://arxiv.org/abs/2210.03629), [OpenAI running agents](https://developers.openai.com/api/docs/guides/agents/running-agents)) | Serial latency and repeated planner-cost overhead |
| `Planner + executor` | Multi-step tasks where high-quality planning can be amortized | Fewer planner calls and cleaner decomposition ([LangChain planning agents](https://www.langchain.com/blog/planning-agents)) | Requires explicit state, verifier logic, and replanning criteria |
| `Verifier/rewrite loop` | Retrieval QA, citation-heavy research, grounded workflows | Relevance grading, query rewrite, and explicit stop/continue logic ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) | Can degenerate into rewrite thrash without budgets |
| `Parallel DAG planner` | Research, ETL, multi-source analysis, dependency-aware subtasks | Best public latency/cost evidence in this source set; parallelizable critical path ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)) | Highest orchestration complexity and strongest need for checkpoint discipline |

### 6.2 Recommended deployment patterns

**Pattern A: user-facing SaaS copilot with bounded workflows**

Use `planner + executor` only when tasks regularly require more than one or two tool calls; otherwise the overhead of explicit planning can outweigh the gain. Put hard `max_turns` or recursion guards around the loop and reserve approvals for side-effecting tools ([OpenAI Agents SDK running agents](https://openai.github.io/openai-agents-python/running_agents/), [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

**Pattern B: research or analytics engine over many independent subtasks**

Prefer dependency-aware decomposition. The strongest evidence in the local source set is `LLMCompiler`, which shows that when work can be parallelized, DAG planning materially improves cost and latency over ReAct ([LLMCompiler paper](https://doi.org/10.48550/arxiv.2312.04511)).

**Pattern C: retrieval-heavy enterprise assistant**

Use `verifier/rewrite` loops rather than one-shot retrieval when the question is multi-part or grounding quality is more important than raw speed. Azure agentic retrieval is the managed version; `LangGraph` agentic RAG is the framework-owned version with explicit relevance grading and query rewrite ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview), [LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)).

**Pattern D: regulated operations agent**

Separate planning from execution approval. Let the model plan and propose structured steps, but require strict schemas, authorization, and human review before external writes. This is the most defensible pattern in the current public docs because it combines reasoning with explicit governance gates ([OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [MCP authorization spec](https://modelcontextprotocol.io/specification/draft/basic/authorization)).

### 6.3 Capacity-planning heuristics

Useful first-order formulas:

```text
max_completed_runs_per_minute
  ~= min(
       provider_rpm / avg_model_turns_per_run,
       provider_tpm / avg_total_tokens_per_run
     )
```

([OpenAI Rate Limits](https://developers.openai.com/api/docs/guides/rate-limits), [Anthropic Rate Limits](https://platform.claude.com/docs/en/api/rate-limits)) [inferred]

```text
effective_total_tokens_per_run
  ~= planner_tokens
   + executor_tokens
   + verifier_tokens
   + replayed_history
   + tool_outputs
   + hidden_reasoning_tokens
```

([OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning), [OpenAI usage](https://openai.github.io/openai-agents-python/usage/), [CrewAI Flows](https://docs.crewai.com/en/concepts/flows)) [inferred]

```text
replan_budget
  ~= max_attempts_per_step * avg(step_cost + verifier_cost)
```

([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred]

### 6.4 Strongest practical conclusions

1. `Decomposition` is the most consistently supported planning improvement in the local source set; it has stronger public evidence than generic "self-reflection" claims.
2. `Verification` is most production-ready when implemented as explicit grading, guardrails, strict schemas, or human approval checkpoints rather than as an unbounded freeform critique loop.
3. `Replanning` needs persistent state plus hard stop conditions; otherwise the same mechanisms that improve quality can create cost blow-ups or infinite loops.
4. The most robust architecture today is usually `strong planner + bounded executors + explicit verifier + approval/audit gates` rather than a single unconstrained reasoning loop ([LangChain planning agents](https://www.langchain.com/blog/planning-agents), [OpenAI human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [Anthropic Strict Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)) [inferred].

## Sources

- [1] https://arxiv.org/abs/2210.03629 - ReAct paper on interleaved reasoning and acting.
- [2] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ - Google Research summary of ReAct results.
- [3] https://developers.openai.com/api/docs/guides/agents/running-agents - OpenAI agent runtime loop and continuation models.
- [4] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI Agents SDK loop semantics, `max_turns`, and durable-execution integrations.
- [5] https://docs.langchain.com/oss/python/langgraph/graph-api - LangGraph super-step execution, routing, and recursion limits.
- [6] https://www.langchain.com/blog/planning-agents - LangChain planner/executor and replanner design.
- [7] https://doi.org/10.48550/arxiv.2312.04511 - LLMCompiler benchmark and DAG planning approach.
- [8] https://docs.langchain.com/oss/python/langgraph/agentic-rag - LangGraph retrieval-decision, grading, rewrite, and generation loop.
- [9] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Azure agentic retrieval decomposition, reasoning effort, cost example, and activity log.
- [10] https://developers.openai.com/api/docs/guides/reasoning - OpenAI reasoning tokens, effort controls, and incomplete-response behavior.
- [11] https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use - Anthropic thinking blocks and tool-loop state requirements.
- [12] https://openai.github.io/openai-agents-python/usage/ - OpenAI usage accounting including reasoning token fields.
- [13] https://openai.github.io/openai-agents-python/ref/usage/ - OpenAI request-usage schema details.
- [14] https://docs.crewai.com/en/concepts/flows - CrewAI flows, usage metrics, persistence, and control primitives.
- [15] https://docs.langchain.com/oss/python/langgraph/checkpointers - LangGraph checkpoints, pending writes, and replay semantics.
- [16] https://docs.langchain.com/oss/python/langgraph/persistence - LangGraph persistence model.
- [17] https://openai.github.io/openai-agents-python/sessions/ - OpenAI session persistence and history shaping.
- [18] https://openai.github.io/openai-agents-python/human_in_the_loop/ - OpenAI approval pause/resume semantics.
- [19] https://adk.dev/sessions/ - ADK Session, State, and Memory model.
- [20] https://adk.dev/sessions/session/ - ADK session-service locking and persistence.
- [21] https://adk.dev/sessions/memory/ - ADK long-term memory service model.
- [22] https://developers.openai.com/api/docs/guides/function-calling - OpenAI function-calling state and strict-schema guidance.
- [23] https://developers.openai.com/api/docs/guides/structured-outputs - OpenAI structured outputs and refusal/schema behavior.
- [24] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use - Anthropic strict schema-valid tool use.
- [25] https://developers.openai.com/api/docs/guides/agent-builder-safety - OpenAI prompt-injection and agent safety guidance.
- [26] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks - Anthropic isolation and screening guidance for untrusted content.
- [27] https://openai.github.io/openai-agents-python/mcp/ - OpenAI MCP integration and approval support.
- [28] https://openai.github.io/openai-agents-python/ref/mcp/server/ - OpenAI MCP server approval configuration.
- [29] https://modelcontextprotocol.io/specification/draft/basic/authorization - MCP OAuth-based authorization profile.
- [30] https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations - MCP PKCE, issuer validation, and auth security requirements.
- [31] https://docs.crewai.com/en/concepts/production-architecture - CrewAI production guidance and task guardrails.
- [32] https://docs.crewai.com/en/learn/human-feedback-in-flows - CrewAI human-feedback revision/approval loop.
- [33] https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT - LangGraph runaway-loop troubleshooting.
- [34] https://adk.dev/api-reference/typescript/interfaces/LoopAgentConfig.html - ADK loop-agent max-iteration semantics.
- [35] https://adk.dev/api-reference/java/com/google/adk/agents/LoopAgent.html - ADK loop-agent stop conditions.
- [36] https://proceedings.mlr.press/v267/patil25a.html - BFCL benchmark showing long-horizon decision-making remains an open challenge.
- [37] https://aclanthology.org/2024.tacl-1.9/ - "Lost in the Middle" long-context degradation benchmark.
- [38] https://arxiv.org/abs/2404.06654 - RULER long-context benchmark.
