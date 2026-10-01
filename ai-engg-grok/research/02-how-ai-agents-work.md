# Research: How AI Agents Work

**Date researched**: 2026-09-29
**Sources consulted**: 32
**Sibling topic (do not duplicate)**: Spec Driven Development — see `01-spec-driven-development-for-ai-agents.md` (SDD treats specs/artifacts as the control plane for coding agents; this note covers the runtime agent loop, tool dispatch, and orchestration patterns).

## 1. System Topology & Mechanics

### Definition: chatbot vs. agent vs. workflow

A chatbot returns text; an agent produces **side effects**—API calls, browser actions, purchases, emails—mediated by tools with defined inputs/outputs ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Anthropic splits **agentic systems** into: **workflows** (LLMs + tools on predefined code paths) vs. **agents** (the LLM dynamically directs its own process and tool usage) ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Agents are “typically just LLMs using tools based on environmental feedback in a loop” ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

Classical PEAS framing (Performance, Environment, Actuators, Sensors) maps cleanly: success criteria, bounds of operation, tools (actuators), and observation channels (API responses, page content, email confirmations) ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Control plane vs. data plane

| Plane | Role in agent runtimes |
| --- | --- |
| **Control plane** | Orchestrator / runner / graph compiler: turn limits, routing (handoffs), approval gates, checkpoint persistence, which tools exist |
| **Data plane** | Model completions + tool executions + observation messages appended into context |

OpenAI Agents SDK `Runner` is the control plane: call model → classify output → final / handoff / tools → loop ([OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/)). LangGraph’s compiled `StateGraph` + checkpointer is the control plane; node functions and tool invocations are the data plane ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Google ADK separates **LlmAgent** (model-directed) from deterministic **workflow agents** (`SequentialAgent`, `ParallelAgent`, `LoopAgent`) whose outer loop is code-controlled, not model-controlled ([ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/); [ADK about](https://github.com/google/adk-docs/blob/main/docs/get-started/about.md)).

### The agent loop: perceive → plan/reason → act → observe

Newsletter #111’s flight-booking walkthrough is the production mental model: reason (“search first”) → act (`search_flights` JSON) → observe (API results) → reason again → filter / book / email until goal or guardrail stop ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

**OpenAI Agents SDK loop** (documented literally):

1. Call the LLM for the current agent with current input.
2. If final output (text of desired type, **no** tool calls) → return.
3. If handoff → switch agent, re-run.
4. If tool calls → execute tools, append results, re-run.
5. If `max_turns` exceeded → `MaxTurnsExceeded` (default turn limit exists; `max_turns=None` disables) ([OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/); [Runner ref — `DEFAULT_MAX_TURNS = 10`](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py)).

**Anthropic tool-use round trip**: client tools → model returns `stop_reason: "tool_use"` + `tool_use` block(s) → host executes → next request sends `tool_result`; server tools (e.g. `web_search`) execute on Anthropic infrastructure ([Anthropic tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)).

### ReAct (reason + act)

ReAct (Yao et al., ICLR 2023) interleaves verbal **Thought**, environment **Action**, and **Observation**. Thoughts do not affect the environment; they update context for subsequent actions. On ALFWorld / WebShop, few-shot ReAct beat imitation/RL methods by **+34%** and **+10%** absolute success with 1–2 in-context examples ([ReAct paper arXiv:2210.03629](https://arxiv.org/abs/2210.03629)). HotpotQA/FEVER setups used dense thought–action–observation steps with Wikipedia `search` / `lookup` / `finish` actions ([ReAct paper](https://arxiv.org/html/2210.03629v3)).

Production caveat from #111: early mistakes compound; mitigate with checkpoints, structured plans for known phases, and verification before irreversible actions ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Plan-and-Execute / Plan-and-Solve

**Plan-and-Solve prompting** (Wang et al., ACL 2023): devise a plan that divides the task into subtasks, then carry out the plan—zero-shot CoT variant that reduces missing-step errors ([Plan-and-Solve arXiv:2305.04091](https://arxiv.org/pdf/2305.04091)).

**LangChain/LangGraph Plan-and-Execute**: (1) planner LLM emits multi-step plan, (2) executor(s) run steps with tools, (3) re-plan or finish. Claimed benefits vs. ReAct: fewer calls to the large planner per tool step; can use smaller models for sub-tasks; forces explicit whole-task reasoning ([LangChain — Plan-and-Execute Agents](https://www.langchain.com/blog/planning-agents)). Rigidity trade-off: without replan, unexpected observations cannot course-correct mid-plan ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

**Hybrid (common in practice)**: outer plan with phase checkpoints; ReAct (or tool loop) inside each phase ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### ReWOO and LLMCompiler (plan → execute DAG)

**ReWOO** (Xu et al.): detaches planning from interleaved observations—Planner emits plan with variable placeholders (`#E1`…), Worker executes tools, Solver answers. Reported **~5× token efficiency** and **+4% accuracy** on HotpotQA vs. ReAct ([ReWOO arXiv:2305.18323](https://arxiv.org/html/2305.18323v1); [LangChain planning-agents](https://www.langchain.com/blog/planning-agents)).

**LLMCompiler** (Kim et al.): Planner streams a **DAG** of tasks with dependencies; Task Fetching Unit schedules ready tasks in parallel; Joiner decides replan vs. finish. Reported vs. ReAct: HotpotQA **1.80×** latency / **3.37×** cost reduction; Movie Recommendation **3.74×** latency / **6.73×** cost; ParallelQA **4.65×** cost reduction ([LLMCompiler arXiv:2312.04511](https://arxiv.org/html/2312.04511v3)).

### Supervisor / orchestrator-workers / subagents

Anthropic **orchestrator-workers**: central LLM dynamically decomposes, delegates to workers, synthesizes—suited when subtasks are unpredictable (e.g. multi-file code edits) ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Related patterns: prompt chaining, routing, parallelization (sectioning/voting), evaluator-optimizer ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

LangChain **subagents**: main agent (supervisor) invokes workers as **tools**; subagents are typically **stateless** per call (context isolation); main agent can invoke multiple subagents in one turn; distinct from a one-shot **router** ([LangChain multi-agent subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)). Deprecated `langgraph-supervisor` → migrate to tool-wrapped `create_agent` subagents ([LangGraph supervisor migration](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)).

**Handoffs**: tool-driven transfer of control (OpenAI coined the term); LangGraph `Command(goto=..., update=...)` / `Command.PARENT` for graph navigation; must keep AIMessage↔ToolMessage pairs valid ([LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs); [OpenAI Agents SDK loop](https://openai.github.io/openai-agents-python/running_agents/)).

Google ADK **AgentTool**: wrap a child agent as a function declaration for the parent `LlmAgent`; also LLM-driven transfer and workflow agents ([ADK about](https://github.com/google/adk-docs/blob/main/docs/get-started/about.md); [ADK custom agents](https://github.com/google/adk-docs/blob/main/docs/agents/custom-agents.md)).

### How frameworks dispatch tools

1. **Schema registration**: tools declared with name, description, JSON Schema parameters in the API request ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)).
2. **Model emission**: structured function/tool call (not free-form “browsing”) ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
3. **Host validation + execution**: verify required fields/types; call real API; return observation; reject invented parameters ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
4. **Concurrency**: OpenAI SDK can start all local function tools emitted in a turn, or cap with `tool_execution.max_function_tool_concurrency` (integer ≥ 1); this is independent of provider `parallel_tool_calls` ([OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/)).
5. **MCP path**: Anthropic Messages API `mcp_servers` + `mcp_toolset` connects remote HTTPS MCP servers; allowlist/denylist tools; OAuth `authorization_token`; only **tool calls** supported (not full MCP feature set); no local STDIO via connector ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).

### State & memory topology

- **Context window = working RAM**: instructions + trajectory + tool outputs; attention degrades as context grows (“lost in the middle”); mitigate by summarizing, externalizing full results, restating constraints ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
- **External / long-term memory**: RAG over vector stores for policies/preferences; retrieve only relevant chunks ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
- **LangGraph**: channel state snapshotted per super-step under `thread_id` ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
- **ADK**: `Session` + `State`; tools mutate via `ToolContext.state`; prefixes `user:` / `app:` / session-scoped / `temp:`; `output_key` auto-saves agent text response ([ADK agent context](https://adk.dev/context/index.md); [ADK tools](https://github.com/google/adk-docs/blob/498c2f6f/docs/tools-custom/index.md)).

## 2. Token Economics & NFR Metrics

### Why agent loops are expensive

ReAct-style loops call a (often frontier) LLM **once per tool step**, replaying growing history—token cost scales roughly with Ω(steps × cumulative context) ([LangChain — Plan-and-Execute Agents](https://www.langchain.com/blog/planning-agents); [ReWOO](https://arxiv.org/html/2305.18323v1)). Anthropic: agentic systems trade **latency and cost** for task performance; start simple ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

### Published architecture-level efficiency (research benchmarks)

| Design | vs. ReAct (published) | Source |
| --- | --- | --- |
| ReWOO | ~**5×** token efficiency; **+4%** HotpotQA accuracy | [ReWOO](https://arxiv.org/html/2305.18323v1) |
| LLMCompiler | HotpotQA **1.80×** faster, **3.37×** cheaper; Movie Rec **3.74×** / **6.73×**; ParallelQA **4.65×** cost | [LLMCompiler](https://arxiv.org/html/2312.04511v3) |
| LLMCompiler token sample (HotpotQA) | ReAct ~2900 in / 120 out vs. LLMCompiler ~1300 / 80 | [LLMCompiler](https://arxiv.org/html/2312.04511v3) |

[inferred] For an N-step ReAct booking agent with ~K tokens of growing history per turn, planner-once + worker tools can cut frontier-model calls from ~N to ~2–few (plan + final), matching ReWOO’s “two LLM calls” framing ([ReWOO](https://arxiv.org/html/2305.18323v1)).

### Prompt caching (OpenAI, agent-relevant)

- Cached input discounted **up to 90%**; GPT-5.6+: cache **read = 0.1×**, cache **write = 1.25×** uncached input; min cacheable prefix **1,024** tokens (visible; hidden system excluded from minimum) ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- Agents API uses same caching as Responses API; session reuse helps prefix stability but **does not guarantee** hits ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- Prefix broken by changes to tools (names/descriptions/schema/order), `parallel_tool_calls`, structured output format, reasoning effort, verbosity, compaction ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- TTL: GPT-5.6+ `prompt_cache_options.ttl` only supported value **`30m`** (default); older models `in_memory` typically **5–10 min** inactivity ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- Routing: traffic above ~**15 RPM** per cache key can overflow machines and hurt hits ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Throughput / rate limits (OpenAI API)

Usage tiers by cumulative spend: Free → Tier 1 ($5) → Tier 2 ($50) → Tier 3 ($100) → Tier 4 ($250) → Tier 5 ($1,000); monthly usage caps rise with tier ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Model-specific RPM/TPM from OpenAI’s published limits snapshot for **chatgpt-4o-latest**: Tier 1 **500 RPM / 30k TPM**; Tier 5 **10k RPM / 30M TPM** ([OpenAI models pricing/limits dump](https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt)). Ramp guidance: after ~1M input TPM, increase by ≤**50% every 15 minutes** ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

### Dynamic model routing

Anthropic routing pattern: easy queries → cheaper/faster model (e.g. Haiku-class); hard → larger model ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Plan-and-execute: large model for plan/replan; smaller models for step execution ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents)).

### Latency

> ⚠️ Limited public data available for this dimension. Vendor-published p50/p95/p99 for full multi-turn agent loops (perceive→act→observe×N) are not standardized; latency is dominated by per-turn model TTFT + tool RTTs. Research papers report relative speedups (LLMCompiler 1.8–3.7× vs ReAct on named benchmarks) rather than absolute SLA percentiles ([LLMCompiler](https://arxiv.org/html/2312.04511v3)).

OpenAI Agents SDK websocket transport: connection limited to **60 minutes**; tune `ping_timeout` for long reasoning; prefer HTTP/SSE when reliability > websocket latency ([OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/)).

## 3. Distributed Resilience & State

### Checkpointing (LangGraph)

- Checkpoint = snapshot of graph state at each **super-step** (all nodes scheduled that tick, possibly parallel); keyed by `thread_id` (+ optional `checkpoint_id`) ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
- Enables HITL, time-travel debug, fault-tolerant resume, conversational memory ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
- **Pending writes**: if one node fails mid-super-step, successful siblings’ writes persist and are not re-run on resume ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
- Savers: `InMemorySaver` (dev), `SqliteSaver` / `AsyncSqliteSaver`, `PostgresSaver` / `AsyncPostgresSaver` (production) ([LangGraph checkpoint reference](https://reference.langchain.com/python/langgraph/checkpoints)).

Temporal’s production note: a LangGraph checkpointer persists **data**, not necessarily **execution**—process death still requires an outer orchestrator to restart/resume; Temporal Workflow + Activity nodes provide durable execution and retries ([Temporal — LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution); [Temporal LangGraph docs](https://docs.temporal.io/develop/python/integrations/langgraph)).

### Durable execution & HITL

- OpenAI Agents SDK: tools with `needs_approval` pause run → `interruptions` → serialize `RunState` → `approve`/`reject` → resume `Runner.run(agent, state)` ([OpenAI Agents SDK — Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- LangGraph `interrupt()` + Temporal: Activity calls `interrupt`; Workflow waits on signal/`wait_condition`; resume via `Command(resume=...)` as next observation; durable timers for escalation timeouts ([Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems); [temporal-community/durable-hitl-agents](https://github.com/temporal-community/durable-hitl-agents)).
- ADK `LoopAgent`: terminate via `max_iterations` and/or sub-agent/tool `EventActions.escalate=True` ([ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/)).

### Loop / cost caps as resilience controls

- OpenAI: `max_turns` (default **10**); `MaxTurnsExceeded`; optional `error_handlers["max_turns"]` for graceful final output ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/); [run_config.py](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py)).
- Anthropic: stopping conditions such as max iterations are common for control ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
- ReAct paper used step caps (e.g. 7 HotpotQA / 5 FEVER) when backing off to CoT-SC ([ReAct paper](https://arxiv.org/html/2210.03629v3)).

### Concurrency, locking, circuit patterns

OpenAI SDK concurrent local tools: default start-all-emitted; configurable concurrency cap ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/)). LangGraph parallel nodes within a super-step with pending-write recovery ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

> ⚠️ Limited public data available for this dimension. Framework docs do not publish concrete circuit-breaker thresholds, half-open probe intervals, or distributed-lock algorithms specific to agent runners; production systems typically wrap tool HTTP clients with standard bulkheads/timeouts (e.g. Temporal Activity retry policies) ([Temporal LangGraph integration](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)).

### Rate-limit fallbacks

OpenAI surfaces `Retry-After` and `x-ratelimit-*` headers; batching multiple tasks into one request helps when RPM-bound but TPM-available ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). [inferred] Agent orchestrators should queue/backoff at the runner, not inside the LLM prompt.

## 4. Enterprise Security & Governance

### Boundaries must be code, not prompts

Agents scale mistakes instantly (e.g. email blast on vague “follow up with leads”); enforce rate limits, allow lists, verification gates in code ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Anthropic: extensive testing in **sandboxed** environments + guardrails; higher cost and compounding errors with autonomy ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

### Tool-level RBAC / capability scoping

- Anthropic MCP connector: enable all tools, **allowlist**, or **denylist**; per-tool configuration; OAuth Bearer for remote servers; URL must be `https://` ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).
- OpenAI Agents SDK: `needs_approval` / MCP `require_approval`; input/output **guardrails**; `tool_not_found_behavior` (default raise `ModelBehaviorError`, or return model-visible error) ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/); [HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- ADK tools: `ToolContext` auth helpers `request_credential` / `get_auth_response` ([ADK agent context](https://adk.dev/context/index.md)).

### Zero-Trust MCP transport / auth

- Claude Messages MCP connector: remote Streamable HTTP or SSE only; client supplies `authorization_token` (OAuth handled by consumer before API call) ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).
- Connector auth types documented for Claude connectors include `oauth_dcr`, `oauth_cimd`, static headers, etc.; **tokens in URL query strings prohibited** (leak via logs/proxies; MCP auth spec forbids query-string access tokens) ([Claude authentication for connectors](https://claude.com/docs/connectors/building/authentication)).

### Schema validation vs. hallucinated parameters

Validate tool JSON against schema before execution; return errors so the model can correct rather than retry the same bad call ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Anthropic ACI guidance: poka-yoke tool args (e.g. require **absolute** file paths after SWE-bench relative-path failures); invest in tool docs as much as prompts ([Anthropic — Building effective agents, Appendix 2](https://www.anthropic.com/engineering/building-effective-agents)).

### Audit / HITL as governance

High-stakes actions (payments) need confirmation of flight/price/total before charge ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Temporal/LangGraph HITL leaves decisions in durable event history for audit ([Temporal durable multi-agent](https://temporal.io/blog/durable-flexible-multi-agent-systems)). LangGraph checkpoints support inspection and time travel ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

> ⚠️ Limited public data available for this dimension. No standardized public SOC2/HIPAA control mapping unique to “agent loops”; PII redaction methods (NER vs regex) are application-layer, not specified by OpenAI/Anthropic/LangGraph agent runners themselves.

### Sandbox isolation

Anthropic recommends sandboxed evaluation for agents ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Computer-use / coding agents imply OS-level sandboxing in practice; framework docs emphasize tool ACI more than WASM vs container trade-offs ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

## 5. Production Failure Modes

### Context window degradation / “context collapse”

Symptoms: earlier constraints (budget, airline prefs) dropped or ignored as tool dumps fill the window; attention fails on middle content ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Mitigations: compress results (e.g. 47 flights → top 3), store full payloads externally, restate rules before irreversible steps ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Compaction/summarization can also **break prompt-cache prefixes** ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Infinite / runaway loops

Causes: repeating failed tool calls; missing stop conditions ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/)). Guards: `max_turns` / `max_iterations`; escalate flags; error handlers that synthesize partial answers ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/); [ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/)). LLMCompiler paper notes ReAct frequently exhibits **looping and early stopping** on HotpotQA / Movie Recommendation ([LLMCompiler](https://arxiv.org/html/2312.04511v3)).

### Error propagation & hallucination

ReAct motivation: CoT alone suffers fact hallucination and error propagation; grounding via tools reduces this ([ReAct paper](https://arxiv.org/abs/2210.03629)). Still: misread observations early poison later steps ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Hallucinated tool parameters caught by schema validation ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Non-determinism

Same prompt → different phrasings/conclusions; lower temperature reduces but does not eliminate variance—use verification gates for high-stakes acts ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Cascading timeouts / partial failure

LangGraph pending writes avoid re-executing successful parallel nodes after a sibling failure ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Temporal Activities provide timeout + retry policies for model/tool calls ([Temporal LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)). OpenAI: tool approval interruptions and nested `agent.as_tool` approval propagation ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).

### State drift / resume hazards

Websocket Responses: after reconnect, `store=False` / ZDR flows cannot recover uncached `previous_response_id`—rebuild from local session state ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/)). Handoffs without paired tool messages confuse the next agent ([LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)). ReWOO without mid-run replan cannot adapt if evidence contradicts the fixed plan ([ReWOO](https://arxiv.org/html/2305.18323v1); [System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### UI / environment brittleness

Unlike RPA scripts, agents adapt semantically but still break on messy pages, pop-ups, layout shifts without fallbacks ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Prefer APIs over raw HTML where possible ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Real-world pattern notes (not full post-mortems)

Anthropic customer-support and coding agents succeed where success is measurable (resolution, tests) and tools provide ground truth each step ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). SWE-bench work: more time optimizing tools than prompts; absolute paths fixed relative-path failures ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

## 6. Enterprise System Design Scenarios

### Pattern selection matrix

| Approach | Best when | Cost / latency | Ops complexity | Failure mode |
| --- | --- | --- | --- | --- |
| Single tool-loop agent (ReAct-shaped) | Open-ended, unpredictable steps | Highest tokens/latency per step | Lowest code | Loops, context bloat ([ReAct](https://arxiv.org/abs/2210.03629); [LangChain planning-agents](https://www.langchain.com/blog/planning-agents)) |
| Plan-and-Execute + replan | Known phase structure; reviewable plan | Lower frontier calls | Medium | Stale plan if replan weak ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents)) |
| ReWOO | Tool results substitutable via variables; static plan OK | ~5× tokens vs ReAct (HotpotQA) | Medium | No mid-flight adapt ([ReWOO](https://arxiv.org/html/2305.18323v1)) |
| LLMCompiler DAG | Many independent tool calls | Up to ~3.7× latency / ~6.7× cost vs ReAct (paper) | Higher | Planner DAG quality ([LLMCompiler](https://arxiv.org/html/2312.04511v3)) |
| Supervisor + subagents-as-tools | Specialized skills; context isolation | Extra orchestration turns | Medium–high | Supervisor misroute ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)) |
| Workflow agents (ADK Sequential/Loop/Parallel) | Deterministic outer control | Predictable | Low–medium | Over-constrains open tasks ([ADK Loop](https://adk.dev/agents/workflow-agents/loop-agents/)) |
| Hybrid plan outer + ReAct inner | Production booking/support | Balanced | Medium | Checkpoint design burden ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)) |

### Capacity planning (published anchors)

- OpenAI chatgpt-4o-class Tier 1: **500 RPM / 30k TPM**; Tier 5: **10k RPM / 30M TPM** ([OpenAI limits dump](https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt)).
- [inferred] A 10-turn agent with ~3k tokens/turn average ≈ 30k tokens/request → Tier 1 TPM exhausted at ~1 concurrent full agent completion per minute if saturated—drives need for caching, smaller step models, or plan-and-execute.
- Prompt-cache write-once / read-many: 1 write + 9 full reads ≈ **2.15×** vs **10×** uncached for that prefix (GPT-5.6+ multipliers) ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
- Keep tool schemas stable across turns to preserve cache prefixes in multi-turn agents ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Reference architectures to cite in interviews

1. **Flight / booking agent (#111)**: PEAS + ReAct/hybrid + schema-validated tools + external memory for policy + human checkpoint before purchase ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
2. **OpenAI Agents SDK production runner**: loop + handoffs + approvals + guardrails + max_turns ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/)).
3. **LangGraph + Postgres checkpointer (+ optional Temporal)**: durable state, HITL interrupt, pending-write recovery ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Temporal LangGraph](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)).
4. **Anthropic orchestrator-workers / evaluator-optimizer**: coding multi-file edits; iterative refinement with clear eval criteria ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
5. **Google ADK**: LlmAgent + AgentTool hierarchy + LoopAgent max_iterations/escalate + session state prefixes ([ADK](https://adk.dev/agents/workflow-agents/loop-agents/); [ADK context](https://adk.dev/context/index.md)).

### Design principles (cross-source)

1. LLM is a **processor**, not a knowledge base—facts from tools/memory ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
2. Prefer simplest pattern that works; add agent loops only when needed ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
3. Treat **ACI** (agent-computer interface) with HCI-level rigor ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).
4. Enforce autonomy limits in **code** (allowlists, approvals, turn/cost caps) ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/running_agents/)).

## Sources

- [1] https://newsletter.systemdesign.one/p/ai-agents-explained — System Design Newsletter #111 primary article (agent loop, tools, memory, ReAct vs plan-and-execute)
- [2] https://www.anthropic.com/engineering/building-effective-agents — Anthropic: workflows vs agents, composable patterns, ACI
- [3] https://openai.github.io/openai-agents-python/running_agents/ — OpenAI Agents SDK runner loop, tools, max_turns
- [4] https://openai.github.io/openai-agents-python/human_in_the_loop/ — Tool approval / RunState resume
- [5] https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py — DEFAULT_MAX_TURNS=10, tool concurrency config
- [6] https://arxiv.org/abs/2210.03629 — ReAct paper (Yao et al.)
- [7] https://arxiv.org/html/2210.03629v3 — ReAct HTML full text / benchmark details
- [8] https://arxiv.org/pdf/2305.04091 — Plan-and-Solve prompting (Wang et al.)
- [9] https://www.langchain.com/blog/planning-agents — Plan-and-Execute, ReWOO, LLMCompiler overview
- [10] https://arxiv.org/html/2305.18323v1 — ReWOO paper (5× tokens, +4% HotpotQA)
- [11] https://arxiv.org/html/2312.04511v3 — LLMCompiler paper (latency/cost vs ReAct)
- [12] https://docs.langchain.com/oss/python/langgraph/checkpointers — LangGraph checkpointing / super-steps
- [13] https://reference.langchain.com/python/langgraph/checkpoints — Checkpoint saver implementations
- [14] https://docs.langchain.com/oss/python/langchain/multi-agent/subagents — Supervisor/subagents-as-tools
- [15] https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs — Handoff / Command routing
- [16] https://docs.langchain.com/oss/python/migrate/langgraph-supervisor — Supervisor package migration
- [17] https://docs.langchain.com/oss/python/langgraph/graph-api — Command API / tool returns
- [18] https://adk.dev/agents/workflow-agents/loop-agents/ — Google ADK LoopAgent
- [19] https://adk.dev/context/index.md — ADK ToolContext / state
- [20] https://github.com/google/adk-docs/blob/main/docs/get-started/about.md — ADK concepts (LlmAgent, workflow agents, AgentTool)
- [21] https://github.com/google/adk-docs/blob/498c2f6f/docs/tools-custom/index.md — ADK custom tools & state prefixes
- [22] https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview — Anthropic tool_use / tool_result round trip
- [23] https://platform.claude.com/docs/en/agents-and-tools/mcp-connector — MCP connector, allowlists, OAuth token
- [24] https://claude.com/docs/connectors/building/authentication — Connector auth types; no tokens in URLs
- [25] https://developers.openai.com/api/docs/guides/prompt-caching — Cache multipliers, TTL, agent notes
- [26] https://developers.openai.com/api/docs/guides/rate-limits — Usage tiers, Retry-After, ramp guidance
- [27] https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt — Published RPM/TPM by model/tier
- [28] https://temporal.io/blog/temporal-langgraph-plugin-durable-execution — Durable execution vs checkpointer-only
- [29] https://temporal.io/blog/durable-flexible-multi-agent-systems — HITL signals / wait_condition
- [30] https://docs.temporal.io/develop/python/integrations/langgraph — Temporal↔LangGraph integration
- [31] https://github.com/temporal-community/durable-hitl-agents — ask_human → interrupt → signal pattern
- [32] https://www.langchain.com/blog/plan-and-execute-agents — Early Plan-and-Execute vs Action agents
