# Module 02 — How AI Agents Work

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 02 (runtime foundations after Spec-Driven Development)  
**Grounded in**: `research/02-how-ai-agents-work.md` (32 sources, 2026-09-29)

A chatbot returns text; an agent produces **side effects**—API calls, browser actions, purchases, emails—mediated by tools with defined inputs/outputs ([System Design Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Anthropic splits **agentic systems** into **workflows** (LLMs + tools on predefined code paths) vs **agents** (the LLM dynamically directs its own process and tool usage); agents are “typically just LLMs using tools based on environmental feedback in a loop” ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Classical PEAS (Performance, Environment, Actuators, Sensors) maps to success criteria, operating bounds, tools, and observation channels ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Sibling module 01 covers specs as the *build-time* control plane; this module covers the *runtime* loop, tool dispatch, and orchestration patterns.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  Runner / StateGraph compiler / ADK workflow outer loop  │
                         │  turn caps · routing/handoffs · approval gates · routing │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ OpenAI     │  │ LangGraph  │  │ Google ADK         │  │
                         │  │ Runner     │  │ StateGraph │  │ Loop/Seq/Parallel  │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  model completions · tool executions · observations      │
                         │  Thought/Action/Obs trajectory appended into context     │
                         │  (perceive → plan/reason → act → observe)                │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  schema validate     │  │  LangGraph ckpt     │  │  turn/token $    │
              │  local fn tools      │  │  PostgresSaver      │  │  rate-limit hdrs │
              │  MCP HTTPS+OAuth     │  │  thread_id / RunSt. │  │  Retry-After     │
              │  concurrency caps    │  │  ADK Session/State  │  │  correlation IDs │
              │  allow/deny lists    │  │  Temporal workflow  │  │  HITL audit      │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **Control plane** | Orchestrator / runner / graph compiler: turn limits, routing (handoffs), approval gates, checkpoint policy, which tools exist | OpenAI `Runner` loop; LangGraph compiled `StateGraph` + checkpointer; ADK `SequentialAgent` / `ParallelAgent` / `LoopAgent` outer loop ([OpenAI Agents SDK — Running agents](https://openai.github.io/openai-agents-python/running_agents/); [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/)) |
| **Data plane** | Model completions + tool executions + observation messages appended into context | Anthropic `tool_use` / `tool_result` round trip; OpenAI classify → final / handoff / tools ([Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| **Persistence** | Per-super-step state snapshots; session prefixes; durable workflow history | LangGraph `thread_id` (+ optional `checkpoint_id`); ADK `Session` + `user:`/`app:`/`temp:` state; Temporal Workflow history ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [ADK context](https://adk.dev/context/index.md); [Temporal LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)) |
| **Tool proxies** | Schema registration → host validation → execution; MCP remote connector | JSON Schema tools; `tool_execution.max_function_tool_concurrency`; Anthropic MCP allowlist/denylist + OAuth Bearer ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)) |
| **Telemetry** | Token/cost meters, rate-limit headers, HITL decision trails, correlation IDs | OpenAI `Retry-After` / `x-ratelimit-*`; Temporal event history; LangGraph time-travel checkpoints ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems)) |

### End-to-end request-flow narrative

1. **Ingress** — User (or upstream workflow) submits a goal plus optional session/thread id. Control plane loads prior checkpoint or empty trajectory ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
2. **Perceive** — Context window = working RAM: system instructions + trajectory + prior tool outputs. Long-term policy/preferences may be retrieved via RAG into a bounded slice ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
3. **Reason / plan (data plane model call)** — Control plane invokes the LLM for the current agent. Model either emits final text, a handoff, or one or more structured tool calls—not free-form “browsing” ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
4. **Classify (control plane)** — OpenAI `Runner`: (a) final output of desired type with **no** tool calls → return; (b) handoff → switch agent, re-run; (c) tool calls → execute, append results, re-run; (d) `max_turns` exceeded → `MaxTurnsExceeded` (default `DEFAULT_MAX_TURNS = 10`; `max_turns=None` disables) ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [run_config.py](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py)).
5. **Act via tool proxies** — Host validates JSON against schema (reject invented params), then executes. Anthropic path: `stop_reason: "tool_use"` → host runs tools → next request sends `tool_result`; server tools (e.g. `web_search`) run on Anthropic infra ([Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). OpenAI can start all local function tools in a turn, or cap with `tool_execution.max_function_tool_concurrency` (≥ 1)—independent of provider `parallel_tool_calls` ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)).
6. **MCP branch (optional)** — Messages API `mcp_servers` + `mcp_toolset` connect remote HTTPS MCP; allowlist/denylist; OAuth `authorization_token`; tool calls only (no local STDIO via connector) ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).
7. **Observe** — Tool results (or errors) append as observations. Flight-booking mental model: search → filter → book → email until goal or guardrail ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
8. **Approval interrupt (optional)** — Tools with `needs_approval` pause → serialize `RunState` → approve/reject → `Runner.run(agent, state)`. LangGraph `interrupt()` + Temporal Workflow `wait_condition` / signal for durable HITL ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems)).
9. **Checkpoint** — LangGraph snapshots channel state each **super-step** under `thread_id`; pending writes from successful sibling nodes persist if one node fails mid-step ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
10. **Terminate or loop** — Final answer, escalate flag (ADK `EventActions.escalate=True`), or turn/iteration cap. Telemetry records tokens, rate-limit headers, and audit decisions ([ADK Loop agents](https://adk.dev/agents/workflow-agents/loop-agents/); [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

Message style: **observe–reason–act cycle** under a code-owned outer loop. Parallelism is at **tool concurrency within a turn** or **DAG ready-set** (LLMCompiler), not an unconstrained peer mesh ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [LLMCompiler](https://arxiv.org/html/2312.04511v3)).

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

ReAct (Yao et al., ICLR 2023) interleaves verbal **Thought**, environment **Action**, and **Observation**. Thoughts do not affect the environment; they update context for subsequent actions. On ALFWorld / WebShop, few-shot ReAct beat imitation/RL baselines by **+34%** and **+10%** absolute success with 1–2 in-context examples ([ReAct arXiv:2210.03629](https://arxiv.org/abs/2210.03629)). HotpotQA/FEVER setups used dense thought–action–observation steps with Wikipedia `search` / `lookup` / `finish` ([ReAct HTML](https://arxiv.org/html/2210.03629v3)).

Production caveat: early mistakes compound—mitigate with checkpoints, structured plans for known phases, and verification before irreversible actions ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). CoT alone suffers fact hallucination and error propagation; tool grounding reduces but does not eliminate early-observation poisoning ([ReAct](https://arxiv.org/abs/2210.03629); [Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### ReAct state machine (tool-loop agent)

```
                    ┌──────────────┐
                    │   START      │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
           ┌───────►│  CALL_MODEL  │◄──────────────────────────┐
           │        └──────┬───────┘                           │
           │               │                                   │
           │        ┌──────▼───────┐                           │
           │        │  CLASSIFY    │                           │
           │        └──────┬───────┘                           │
           │     ┌─────────┼─────────┬────────────┐            │
           │     ▼         ▼         ▼            ▼            │
           │  FINAL    HANDOFF   TOOL_CALLS   MAX_TURNS        │
           │     │         │         │            │            │
           │     ▼         ▼         ▼            ▼            │
           │  RETURN   switch     VALIDATE     MaxTurnsExc    │
           │           agent      + EXECUTE    / error_handler │
           │              │         │                          │
           │              │         ▼                          │
           │              │   ┌──────────┐                     │
           │              │   │ OBSERVE  │──append──►context───┘
           │              │   └──────────┘
           │              └──────────► CALL_MODEL (new agent)
           │
           └── (optional) approval pause → serialize RunState → resume
```

**Invariants**

| Invariant | Binding |
| --- | --- |
| Turn budget | OpenAI default `max_turns=10`; ReAct paper used step caps (e.g. 7 HotpotQA / 5 FEVER when backing off to CoT-SC); ADK `max_iterations` and/or escalate ([OpenAI run_config](https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py); [ReAct HTML](https://arxiv.org/html/2210.03629v3); [ADK Loop](https://adk.dev/agents/workflow-agents/loop-agents/)) |
| Termination | Final typed output with no tool calls; escalate; max turns; human reject |
| Message pairing | Handoffs must keep AIMessage↔ToolMessage pairs valid ([LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)) |
| Schema gate | Invented tool parameters rejected before side effects ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)) |

**Complexity [inferred]**: for \(N\) tool steps with growing context size \(C_i \approx C_0 + i\cdot\Delta\), model calls = \(N+1\) (or \(N\) if last action is `finish`). Token volume \(\approx \sum_{i=0}^{N} C_i = \Theta(N^2 \Delta)\) in the naive replay case—matches ReWOO/LangChain motivation that ReAct cost scales with steps × cumulative context ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents); [ReWOO](https://arxiv.org/html/2305.18323v1)).

### Plan-and-Execute state machine

**Plan-and-Solve** (Wang et al., ACL 2023): devise a plan that divides the task into subtasks, then carry out the plan—zero-shot CoT variant that reduces missing-step errors ([Plan-and-Solve arXiv:2305.04091](https://arxiv.org/pdf/2305.04091)).

**LangChain/LangGraph Plan-and-Execute**: (1) planner LLM emits multi-step plan, (2) executor(s) run steps with tools, (3) re-plan or finish. Benefits vs ReAct: fewer frontier planner calls per tool step; smaller models for sub-tasks; forces explicit whole-task reasoning ([LangChain — Plan-and-Execute Agents](https://www.langchain.com/blog/planning-agents)). Rigidity: without replan, unexpected observations cannot course-correct mid-plan ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

```
     ┌─────────┐     ┌──────────┐     ┌──────────┐     ┌─────────┐
     │  PLAN   │────►│ EXECUTE  │────►│ OBSERVE  │────►│ REPLAN? │──yes──► PLAN
     │ (large) │     │ (tools / │     │  step i  │     │ or DONE │
     └─────────┘     │  small)  │     └──────────┘     └────┬────┘
                     └──────────┘                           │ no
                                                            ▼
                                                         FINISH
```

**Hybrid (common in production)**: outer plan with phase checkpoints; ReAct (or tool loop) inside each phase ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### ReWOO / LLMCompiler (plan → execute DAG)

**ReWOO**: Planner emits plan with variable placeholders (`#E1`…); Worker executes tools; Solver answers. Reported **~5× token efficiency** and **+4%** HotpotQA accuracy vs ReAct ([ReWOO arXiv:2305.18323](https://arxiv.org/html/2305.18323v1)). Failure mode: no mid-flight adapt if evidence contradicts the fixed plan ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [ReWOO](https://arxiv.org/html/2305.18323v1)).

**LLMCompiler**: Planner streams a **DAG** of tasks with dependencies; Task Fetching Unit schedules ready tasks in parallel; Joiner decides replan vs finish. Reported vs ReAct: HotpotQA **1.80×** latency / **3.37×** cost; Movie Recommendation **3.74×** / **6.73×**; ParallelQA **4.65×** cost; sample tokens HotpotQA ReAct ~2900 in / 120 out vs LLMCompiler ~1300 / 80 ([LLMCompiler arXiv:2312.04511](https://arxiv.org/html/2312.04511v3)).

**Scheduling complexity [inferred]**: topological ready-set scheduling over a DAG of \(V\) tool tasks is \(O(V+E)\) per joiner cycle; parallelism bounded by independent ready nodes + tool concurrency caps.

### Supervisor / orchestrator-workers state machine

Anthropic **orchestrator-workers**: central LLM dynamically decomposes, delegates to workers, synthesizes—suited when subtasks are unpredictable (e.g. multi-file code edits). Related composable patterns: prompt chaining, routing, parallelization (sectioning/voting), evaluator-optimizer ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

LangChain **subagents**: main agent (supervisor) invokes workers as **tools**; subagents typically **stateless** per call (context isolation); multiple subagents in one turn; distinct from a one-shot **router**. Deprecated `langgraph-supervisor` → migrate to tool-wrapped `create_agent` subagents ([LangChain multi-agent subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents); [LangGraph supervisor migration](https://docs.langchain.com/oss/python/migrate/langgraph-supervisor)).

**Handoffs**: tool-driven transfer of control (OpenAI term); LangGraph `Command(goto=..., update=...)` / `Command.PARENT` ([LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)). Google ADK **AgentTool** wraps a child as a function declaration for the parent `LlmAgent` ([ADK about](https://github.com/google/adk-docs/blob/main/docs/get-started/about.md)).

```
                    ┌────────────────┐
                    │  SUPERVISOR    │◄──── synthesize / next delegate
                    └───────┬────────┘
                            │ invoke workers-as-tools
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         ┌────────┐   ┌────────┐   ┌────────┐
         │Worker A│   │Worker B│   │Worker C│  (stateless per call)
         └────────┘   └────────┘   └────────┘
```

**Invariant**: supervisor misroute is a first-class failure mode—workers need clear tool docs (ACI) and the supervisor needs max_turns on the outer loop ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)).

### Convergence properties

| Pattern | Converges when | Diverges when |
| --- | --- | --- |
| ReAct | `finish` / final text / step cap | Looping failed tools; LLMCompiler notes ReAct looping and early stopping on HotpotQA / Movie Rec ([LLMCompiler](https://arxiv.org/html/2312.04511v3)) |
| Plan-and-Execute | Plan empty / replan says done | Stale plan without replan ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)) |
| Supervisor | Synthesized answer / handoff complete | Endless re-delegation without turn budget |
| ADK LoopAgent | `max_iterations` or escalate | Missing escalate + unbounded iterations ([ADK Loop](https://adk.dev/agents/workflow-agents/loop-agents/)) |

---

## Part 3 — Token Economics & NFR Analysis

### Why agent loops are expensive

ReAct-style loops call a (often frontier) LLM **once per tool step**, replaying growing history—token cost scales roughly with \(\Omega(\text{steps} \times \text{cumulative context})\) ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents); [ReWOO](https://arxiv.org/html/2305.18323v1)). Anthropic: agentic systems trade **latency and cost** for task performance; start simple ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

### Published architecture-level efficiency

| Design | vs ReAct (published) | Source |
| --- | --- | --- |
| ReWOO | ~**5×** token efficiency; **+4%** HotpotQA accuracy | [ReWOO](https://arxiv.org/html/2305.18323v1) |
| LLMCompiler | HotpotQA **1.80×** faster, **3.37×** cheaper; Movie Rec **3.74×** / **6.73×**; ParallelQA **4.65×** cost | [LLMCompiler](https://arxiv.org/html/2312.04511v3) |
| LLMCompiler token sample (HotpotQA) | ReAct ~2900 in / 120 out vs LLMCompiler ~1300 / 80 | [LLMCompiler](https://arxiv.org/html/2312.04511v3) |

[inferred] For an \(N\)-step ReAct booking agent with growing history, planner-once + worker tools can cut frontier-model calls from ~\(N\) to ~2–few (plan + final), matching ReWOO’s “two LLM calls” framing ([ReWOO](https://arxiv.org/html/2305.18323v1)).

### Cost formulas — $ per 1k runs

**Assumptions (state explicitly; substitute your contract rates)**

| Symbol | Assumed value | Role |
| --- | --- | --- |
| \(P_{\text{in}}\) | **$3.00 / 1M** input | Frontier list-class placeholder—not a vendor SLA |
| \(P_{\text{out}}\) | **$15.00 / 1M** output | Same |
| ReAct trajectory | \(N=8\) model turns after tools; avg **3,000** input + **150** output tokens/turn | Aligns with research capacity note (~3k tokens/turn) ([research §6](../research/02-how-ai-agents-work.md)) |
| Cache | GPT-5.6+ cache **read = 0.1×**, cache **write = 1.25×** uncached input; min prefix **1,024** tokens | [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) |

#### A. Naive ReAct (no cache)

\[
\text{Cost}_{1\text{run}}^{\text{ReAct}} = N\cdot\Bigl(\frac{3000}{10^6}P_{\text{in}} + \frac{150}{10^6}P_{\text{out}}\Bigr)
= 8\cdot(0.009 + 0.00225) = \$0.090
\]

\[
\text{Cost}_{1\text{k runs}}^{\text{ReAct}} \approx \$90
\]

#### B. ReWOO-class (~5× token efficiency on HotpotQA)

Using published **~5×** token efficiency vs ReAct ([ReWOO](https://arxiv.org/html/2305.18323v1)):

\[
\text{Cost}_{1\text{k}}^{\text{ReWOO}} \approx \frac{\$90}{5} = \$18
\]

#### C. LLMCompiler-class cost reduction (HotpotQA **3.37×**)

\[
\text{Cost}_{1\text{k}}^{\text{LLMCompiler}} \approx \frac{\$90}{3.37} \approx \$26.7
\]

(Use Movie Rec **6.73×** only when the workload matches that benchmark’s parallelism—do not universalize.)

#### D. Prompt-cache impact (stable tool schemas)

GPT-5.6+ multipliers: write **1.25×**, read **0.1×** ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). For a stable system+tools prefix of \(S=2{,}000\) tokens reused across 8 turns with 1 write + 7 reads of the prefix:

**[inferred]**:

\[
\text{PrefixCost}_{8} = P_{\text{in}}\cdot\frac{S}{10^6}\bigl(1.25 + 7\cdot 0.1\bigr)
= 3\cdot\frac{2000}{10^6}\cdot 1.95 = \$0.0117
\]

vs 8× full uncached prefix \(= 3\cdot 8\cdot 2000/10^6 = \$0.048\). Research note: 1 write + 9 full reads ≈ **2.15×** vs **10×** uncached for that prefix ([research §6](../research/02-how-ai-agents-work.md); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

**Cache breakers**: tool name/description/schema/order changes, `parallel_tool_calls`, structured output format, reasoning effort, verbosity, compaction ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Agents API uses same caching as Responses; session reuse helps but **does not guarantee** hits. TTL: GPT-5.6+ only `30m`; older models typically 5–10 min inactivity. Traffic above ~**15 RPM** per cache key can overflow machines and hurt hits ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

#### E. Dynamic model routing

Anthropic: easy → cheaper/faster (Haiku-class); hard → larger. Plan-and-execute: large model for plan/replan; smaller for step execution ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [LangChain planning-agents](https://www.langchain.com/blog/planning-agents)). **[inferred]** If 6 of 8 turns use a model at **0.2×** input/output of frontier and 2 use full frontier:

\[
\text{Cost}_{1\text{k}}^{\text{routed}} \approx 1000\cdot\bigl(6\cdot 0.2\cdot c_{\text{turn}} + 2\cdot c_{\text{turn}}\bigr)
= 1000\cdot 3.2\cdot c_{\text{turn}}
\]

with \(c_{\text{turn}}=\$0.01125\) from §A → ≈ **$36 / 1k** (illustrative mix only).

### Latency SLA targets

> ⚠️ **Gap**: Limited public data. Vendor-published p50/p95/p99 for full multi-turn agent loops (perceive→act→observe×N) are not standardized; latency is dominated by per-turn model TTFT + tool RTTs. Research papers report relative speedups (LLMCompiler **1.8–3.7×** vs ReAct on named benchmarks) rather than absolute SLA percentiles ([LLMCompiler](https://arxiv.org/html/2312.04511v3); research §2). The millisecond budgets below are **[inferred]** engineering SLAs assembled from stated component assumptions—not vendor-published agent-loop percentiles.

#### Component assumptions [inferred]

| Component | Symbol | p50 | p95 | p99 | Notes (assumption, not a citation) |
| --- | --- | --- | --- | --- | --- |
| Model time-to-first-token | \(T_{\text{TTFT}}\) | **400 ms** | **1,000 ms** | **2,500 ms** | Frontier API cold-ish path |
| Output decode | \(t_{\text{tok}}\) | **25 ms/tok** | **25 ms/tok** | **33 ms/tok** | ≈40 tok/s p50/p95; slower p99 decode |
| Output tokens / short turn | \(O\) | 150 | 150 | 150 | Aligns with Part 3 ~150 out/turn |
| Tool round-trip | \(T_{\text{tool}}\) | **200 ms** | **800 ms** | **3,000 ms** | Schema-validated HTTPS tool |
| Checkpoint write | \(T_{\text{ckpt}}\) | **50 ms** | **150 ms** | **400 ms** | LangGraph Postgres-class super-step |
| Human approval wait (HITL only) | \(T_{\text{human}}\) | **120,000 ms** | **600,000 ms** | **3,600,000 ms** | 2 min / 10 min / 60 min ops SLA |
| ReAct steps | \(N\) | 8 | 8 | 8 | Same \(N\) as Part 3 cost model |

Output generation wall time: \(T_{\text{out}} = O \cdot t_{\text{tok}}\).

\[
T_{\text{out}}^{\text{p50/p95}} = 150\times 25 = 3{,}750\text{ ms},\quad
T_{\text{out}}^{\text{p99}} = 150\times 33 = 4{,}950\text{ ms}
\]

#### (a) Short tool-using turn [inferred]

One model call + one tool + one checkpoint:

\[
L_{\text{short}} = T_{\text{TTFT}} + T_{\text{out}} + T_{\text{tool}} + T_{\text{ckpt}}
\]

| Tier | Arithmetic | Target |
| --- | --- | --- |
| **p50** | \(400 + 3{,}750 + 200 + 50\) | **4,400 ms** |
| **p95** | \(1{,}000 + 3{,}750 + 800 + 150\) | **5,700 ms** |
| **p99** | \(2{,}500 + 4{,}950 + 3{,}000 + 400\) | **10,850 ms** |

**Mitigations**: stream tokens after TTFT (perceived p50 ≪ wall clock); stable prompt cache to cut TTFT on reuse ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)); Haiku-class routing for easy turns ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).

#### (b) \(N\)-step ReAct trajectory [inferred]

Serial loop of \(N=8\) short turns (naive ReAct; no mid-turn tool parallelization):

\[
L_{\text{ReAct}} = N \cdot L_{\text{short}}
\]

| Tier | Arithmetic | Target |
| --- | --- | --- |
| **p50** | \(8 \times 4{,}400\) | **35,200 ms** |
| **p95** | \(8 \times 5{,}700\) | **45,600 ms** |
| **p99** | \(8 \times 10{,}850\) | **86,800 ms** |

**Relative lever (citeable, not absolute ms)**: LLMCompiler HotpotQA **1.80×** / Movie Rec **3.74×** vs ReAct ([LLMCompiler](https://arxiv.org/html/2312.04511v3)) ⇒ [inferred] budget compressions \(35{,}200/1.80 \approx 19{,}600\) ms (p50 HotpotQA-like) or \(35{,}200/3.74 \approx 9{,}400\) ms (p50 Movie-Rec-like) when the DAG fits.

**Mitigations**: Plan-and-Execute / ReWOO / LLMCompiler DAG to cut serial model waits; `tool_execution.max_function_tool_concurrency` for independent tools in one turn ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [LangChain planning-agents](https://www.langchain.com/blog/planning-agents)); propagate a hard deadline (e.g. cancel remaining turns when elapsed \(> 45{,}600\) ms p95 budget).

#### (c) HITL-paused run [inferred]

Machine path to approval interrupt ≈ **2** short turns (search + propose book), then human wait, then one resume turn (confirm/book):

\[
L_{\text{HITL}} = 2\cdot L_{\text{short}} + T_{\text{human}} + L_{\text{short}} = 3\cdot L_{\text{short}} + T_{\text{human}}
\]

| Tier | Arithmetic | Target |
| --- | --- | --- |
| **p50** | \(3\times 4{,}400 + 120{,}000 = 13{,}200 + 120{,}000\) | **133,200 ms** |
| **p95** | \(3\times 5{,}700 + 600{,}000 = 17{,}100 + 600{,}000\) | **617,100 ms** |
| **p99** | \(3\times 10{,}850 + 3{,}600{,}000 = 32{,}550 + 3{,}600{,}000\) | **3,632,550 ms** |

Time-to-interrupt only (exclude human): \(2\cdot L_{\text{short}}\) → **8,800 / 11,400 / 21,700 ms** (p50/p95/p99) [inferred].

**Mitigations**: durable `interrupt` / `RunState` so the API worker need not hold the websocket (OpenAI websocket ≤ **60 minutes**—prefer HTTP/SSE when reliability > latency) ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)); Temporal durable timer for escalation at the human p95/p99 budgets ([Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems)); deadline propagation so post-resume tools inherit remaining wall-clock budget.

#### Summary SLA table [inferred]

| Workload | p50 | p95 | p99 | Primary mitigation |
| --- | ---: | ---: | ---: | --- |
| (a) Short tool turn | **4,400 ms** | **5,700 ms** | **10,850 ms** | Streaming + prompt cache + model routing |
| (b) \(N{=}8\) ReAct | **35,200 ms** | **45,600 ms** | **86,800 ms** | DAG/plan patterns + parallel tools + deadline cancel |
| (c) HITL end-to-end | **133,200 ms** | **617,100 ms** | **3,632,550 ms** | Durable interrupt + escalation timer + deadline on resume |

### Throughput & back-pressure

| Anchor | Value | Source |
| --- | --- | --- |
| chatgpt-4o-latest Tier 1 | **500 RPM / 30k TPM** | [OpenAI limits dump](https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt) |
| chatgpt-4o-latest Tier 5 | **10k RPM / 30M TPM** | same |
| Usage tiers | Free → T1 ($5) → … → T5 ($1,000) cumulative spend | [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits) |
| Ramp | After ~1M input TPM, increase ≤**50% every 15 minutes** | [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits) |

**[inferred] capacity**: a 10-turn agent with ~3k tokens/turn average ≈ **30k tokens/request** → Tier 1 TPM exhausted at ~**1 concurrent full agent completion per minute** if saturated—drives caching, smaller step models, or plan-and-execute ([research §6](../research/02-how-ai-agents-work.md)).

**Back-pressure design**

1. Honor `Retry-After` and `x-ratelimit-*` at the **runner**, not inside the LLM prompt ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits); research [inferred] note).
2. When RPM-bound but TPM-available, batch multiple tasks into one request ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).
3. Queue/shed non-critical agents; keep `max_turns` and cost caps as hard shed valves ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)).
4. Cap local tool concurrency (`max_function_tool_concurrency`) to protect downstream APIs ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)).

### Availability, RPO/RTO, compliance, NFR trade-offs

| NFR | Target / posture | Trade-off |
| --- | --- | --- |
| **Availability** | Control plane (runner) HA + model/tool multi-region; Temporal Workflows outlive process death | Checkpointer alone persists **data**, not **execution**—still need outer orchestrator to resume ([Temporal LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)) |
| **RPO** | LangGraph Postgres checkpoints per super-step → RPO ≈ last successful super-step; pending writes retain sibling success ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) | `store=False` / ZDR websocket flows may lose `previous_response_id`—rebuild from local session ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| **RTO** | Temporal Activity retry + Workflow restart → minutes; HITL waits on durable timers for escalation ([Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems)) | Longer HITL windows ↑ RTO for “done” but protect irreversible spends |
| **Compliance** | Boundaries in **code** (allowlists, approvals, rate limits)—not prompts; sandbox agents before prod ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) | > ⚠️ **Gap**: no standardized public SOC2/HIPAA control mapping unique to agent loops; PII methods are application-layer ([research §4](../research/02-how-ai-agents-work.md)) |
| **Cost vs autonomy** | More steps / larger models ↑ $ and latency for task success ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)) | Prefer simplest pattern that works |
| **Cache vs correctness** | Compaction/summarization can **break** prompt-cache prefixes ([OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) | Context hygiene vs $ |

---

## Part 4 — Distributed Resilience & Security

### Durable execution: LangGraph checkpoints + Temporal

| Concern | LangGraph checkpointer | Temporal (+ LangGraph plugin) |
| --- | --- | --- |
| What persists | Channel state snapshot each **super-step**, keyed by `thread_id` (+ optional `checkpoint_id`) | Workflow history + Activity retries/timeouts |
| Enables | HITL, time-travel debug, fault-tolerant resume, conversational memory | Process-death recovery; durable timers; signal-based human resume |
| Failure nuance | **Pending writes**: successful sibling nodes in a super-step are not re-run on resume | Activity policies wrap model/tool calls |
| Savers | `InMemorySaver` (dev); `SqliteSaver` / `AsyncSqliteSaver`; `PostgresSaver` / `AsyncPostgresSaver` (prod) | Workflow is the durability boundary |

Sources: [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [checkpoint reference](https://reference.langchain.com/python/langgraph/checkpoints); [Temporal LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution); [Temporal LangGraph docs](https://docs.temporal.io/develop/python/integrations/langgraph).

**HITL patterns**: OpenAI `needs_approval` → interruptions → serialize `RunState` → approve/reject → resume. LangGraph `interrupt()` inside Activity; Workflow waits on signal/`wait_condition`; resume via `Command(resume=...)` as next observation ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems); [durable-hitl-agents](https://github.com/temporal-community/durable-hitl-agents)).

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | Model 429/5xx, tool timeout, MCP blip | Exponential backoff + jitter; honor `Retry-After`; Temporal Activity retries ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [Temporal LangGraph](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)) |
| **Permanent** | Schema-invalid after model correction loop exhausted; policy deny; user reject approval | Fail closed; do not retry side effects; return partial via `error_handlers["max_turns"]` if configured ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| **Poison pill** | Repeating same failed tool call; infinite ReAct loop | `max_turns` / `max_iterations`; escalate; DLQ the run thread ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [LLMCompiler](https://arxiv.org/html/2312.04511v3); [ADK Loop](https://adk.dev/agents/workflow-agents/loop-agents/)) |
| **Semantic** | Early misread observation poisons later steps; hallucinated params | Schema validation; compress tool dumps; restate constraints before irreversible acts ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)) |
| **Idempotency** | Re-drive after crash mid-tool | Idempotency keys on mutating tools `(run_id, tool_call_id)`; LangGraph pending-write semantics for parallel nodes ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)) |
| **State drift** | Websocket reconnect with `store=False`; unpaired handoff messages | Rebuild from local session; keep AIMessage↔ToolMessage pairs ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)) |

Non-determinism: same prompt → different phrasings; lower temperature reduces but does not eliminate variance—use verification gates for high-stakes acts ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### Circuit breaker: closed → open → half-open

> ⚠️ **Gap**: Framework docs do not publish concrete circuit-breaker thresholds, half-open probe intervals, or distributed-lock algorithms specific to agent runners; production systems typically wrap tool HTTP clients with standard bulkheads/timeouts (e.g. Temporal Activity retry policies) ([research §3](../research/02-how-ai-agents-work.md); [Temporal LangGraph](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)).

Apply **per dependency** (model endpoint, each tool host, each MCP server):

1. **Closed** — traffic flows; count failures in a sliding window.
2. **Open** — after threshold: short-circuit calls; queue or degrade (secondary model / deterministic answer / HITL).
3. **Half-open** — after cool-down, allow one probe; success → closed; failure → open.

Do not open the breaker on **permanent** schema/policy failures—those are not dependency health signals.

### Fallback model chains

```
primary (frontier) → secondary (Haiku-class / smaller executor) → deterministic fallback
```

Deterministic fallbacks for agents: (1) synthesize partial answer via `error_handlers["max_turns"]`; (2) escalate to human with serialized state; (3) safe no-op / ticket for irreversible tools ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Plan-and-execute naturally places large model on plan and small on steps ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents)).

### Zero-Trust MCP

- Remote Streamable HTTP or SSE only via Claude Messages MCP connector; client supplies `authorization_token` (OAuth completed by consumer before API call) ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).
- URL must be `https://`; enable all tools, **allowlist**, or **denylist**; per-tool configuration ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).
- Auth types include `oauth_dcr`, `oauth_cimd`, static headers, etc.; **tokens in URL query strings prohibited** (leak via logs/proxies; MCP auth spec forbids query-string access tokens) ([Claude authentication for connectors](https://claude.com/docs/connectors/building/authentication)).
- Treat MCP-retrieved content as **data**, not instructions; validate tool args server-side before side effects ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [Anthropic ACI](https://www.anthropic.com/engineering/building-effective-agents)).

### Tool-level RBAC (least privilege)

| Capability | Mechanism |
| --- | --- |
| Tool inventory | MCP allowlist/denylist; register only needed schemas ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)) |
| High-stakes | `needs_approval` / MCP `require_approval`; confirm flight/price/total before charge ([OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/); [Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)) |
| Guardrails | Input/output guardrails; `tool_not_found_behavior` (raise or model-visible error) ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| Auth helpers | ADK `ToolContext.request_credential` / `get_auth_response` ([ADK context](https://adk.dev/context/index.md)) |
| ACI hygiene | Poka-yoke args (e.g. absolute file paths after SWE-bench relative-path failures); invest in tool docs as much as prompts ([Anthropic Appendix 2](https://www.anthropic.com/engineering/building-effective-agents)) |

Enforce rate limits, allow lists, and verification gates **in code**—agents scale mistakes instantly (e.g. email blast on vague “follow up with leads”) ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

### PII pipeline: detection → redaction → audit

> ⚠️ **Gap**: No standardized public SOC2/HIPAA control mapping unique to agent loops; PII redaction methods (NER vs regex) are application-layer, not specified by OpenAI/Anthropic/LangGraph runners ([research §4](../research/02-how-ai-agents-work.md)).

1. **Detect** — scan prompts, tool args, and observations for PII/secrets before model/MCP egress.
2. **Redact** — replace with stable tokens in model context; keep mapping in a sealed vault; compress large tool dumps externally ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).
3. **Audit** — append-only event with correlation id, tool name, decision, redaction counts; Temporal/LangGraph history holds HITL decisions for chain-of-custody ([Temporal durable multi-agent](https://temporal.io/blog/durable-flexible-multi-agent-systems); [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

### Immutable logs & chain-of-custody

- Temporal Workflow event history = durable decision log for approvals and activity outcomes ([Temporal durable HITL](https://temporal.io/blog/durable-flexible-multi-agent-systems)).
- LangGraph checkpoints support inspection and time travel ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
- Hash-chain or WORM store for `{correlation_id, run_id, turn, tool, args_hash, observation_hash, decision}` when regulated—define schema yourself (⚠️ Gap above).

Sandbox isolation: Anthropic recommends sandboxed evaluation; computer-use/coding agents imply OS-level sandboxing; framework docs emphasize tool ACI more than WASM vs container trade-offs ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Prefer APIs over raw HTML for UI brittleness ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

---

## Part 5 — Production Enterprise Code

Runnable, self-contained Python: agent loop with a **deterministic fake model** (no API keys), retries with exponential backoff + full jitter, circuit breaker (closed → open → half-open), fallback model chain, structured logging with correlation IDs, and graceful degradation. No TODOs.

```python
#!/usr/bin/env python3
"""Production-shaped agent loop with resilience primitives (no API keys)."""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "run_id": getattr(record, "run_id", None),
            "turn": getattr(record, "turn", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "provider": getattr(record, "provider", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = build_logger("agent.runtime")


def log_extra(**kwargs: Any) -> dict[str, Any]:
    return kwargs


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class AgentError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    window_sec: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def _prune(self, now: float) -> None:
        self.failures = [t for t in self.failures if now - t <= self.window_sec]

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state is BreakerState.OPEN:
            assert self.opened_at is not None
            if now - self.opened_at >= self.recovery_timeout_sec:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self.failures.append(now)
        if self.state is BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.opened_at = now
            return
        if len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_sec: float = 0.5
    max_delay_sec: float = 20.0

    def delay(self, attempt: int) -> float:
        ceiling = min(self.max_delay_sec, self.base_delay_sec * (2**attempt))
        return random.uniform(0.0, ceiling)


# ---------------------------------------------------------------------------
# Tool registry (schema validation + execution)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class Observation:
    call_id: str
    name: str
    ok: bool
    payload: dict[str, Any]


class ToolRegistry:
    """Validates required fields then executes deterministic tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "search_flights": self._search_flights,
            "book_flight": self._book_flight,
        }
        self._required: dict[str, set[str]] = {
            "search_flights": {"origin", "destination", "date"},
            "book_flight": {"flight_id", "confirm"},
        }

    def execute(self, call: ToolCall) -> Observation:
        if call.name not in self._tools:
            return Observation(call.call_id, call.name, False, {"error": "tool_not_found"})
        missing = self._required[call.name] - set(call.arguments)
        if missing:
            return Observation(
                call.call_id,
                call.name,
                False,
                {"error": "schema_invalid", "missing": sorted(missing)},
            )
        try:
            payload = self._tools[call.name](call.arguments)
            return Observation(call.call_id, call.name, True, payload)
        except AgentError as exc:
            return Observation(
                call.call_id,
                call.name,
                False,
                {"error": str(exc), "kind": exc.kind.value},
            )

    @staticmethod
    def _search_flights(args: dict[str, Any]) -> dict[str, Any]:
        # Deterministic catalog — compress to top results (context hygiene)
        catalog = [
            {"flight_id": "AA100", "price": 420, "stops": 0},
            {"flight_id": "UA220", "price": 390, "stops": 1},
            {"flight_id": "DL310", "price": 455, "stops": 0},
        ]
        return {
            "origin": args["origin"],
            "destination": args["destination"],
            "date": args["date"],
            "top": catalog[:3],
        }

    @staticmethod
    def _book_flight(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("confirm") is not True:
            raise AgentError("booking requires confirm=true", FailureKind.PERMANENT)
        return {"status": "booked", "flight_id": args["flight_id"], "pnr": "PNR-DEMO-42"}


# ---------------------------------------------------------------------------
# Fake / deterministic model providers + fallback chain
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelOutput:
    kind: str  # "final" | "tool_calls"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider: str = ""


class ModelProvider(Protocol):
    name: str

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        ...


class PrimaryFakeModel:
    """Simulates a frontier model. Fails transiently a configurable number of times."""

    name = "primary"

    def __init__(self, fail_times: int = 0) -> None:
        self._remaining_failures = fail_times

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AgentError("model 503", FailureKind.TRANSIENT)

        has_search = any(
            m.get("role") == "tool" and m.get("name") == "search_flights" and m.get("ok")
            for m in messages
        )
        has_book = any(
            m.get("role") == "tool" and m.get("name") == "book_flight" and m.get("ok")
            for m in messages
        )

        if not has_search:
            return ModelOutput(
                kind="tool_calls",
                provider=self.name,
                tool_calls=(
                    ToolCall(
                        name="search_flights",
                        arguments={
                            "origin": "SFO",
                            "destination": "JFK",
                            "date": "2026-10-10",
                        },
                        call_id=f"call-search-{turn}",
                    ),
                ),
            )
        if not has_book:
            # Pick cheapest from last search observation
            last = next(
                m for m in reversed(messages)
                if m.get("role") == "tool" and m.get("name") == "search_flights"
            )
            flight_id = last["payload"]["top"][1]["flight_id"]  # UA220 $390
            return ModelOutput(
                kind="tool_calls",
                provider=self.name,
                tool_calls=(
                    ToolCall(
                        name="book_flight",
                        arguments={"flight_id": flight_id, "confirm": True},
                        call_id=f"call-book-{turn}",
                    ),
                ),
            )
        return ModelOutput(
            kind="final",
            provider=self.name,
            text="Booked UA220 SFO→JFK on 2026-10-10. PNR-DEMO-42. Total $390.",
        )


class SecondaryFakeModel:
    """Cheaper/faster path: answers from search alone without booking."""

    name = "secondary"

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        has_search = any(
            m.get("role") == "tool" and m.get("name") == "search_flights" and m.get("ok")
            for m in messages
        )
        if not has_search:
            return ModelOutput(
                kind="tool_calls",
                provider=self.name,
                tool_calls=(
                    ToolCall(
                        name="search_flights",
                        arguments={
                            "origin": "SFO",
                            "destination": "JFK",
                            "date": "2026-10-10",
                        },
                        call_id=f"call-search-sec-{turn}",
                    ),
                ),
            )
        last = next(
            m for m in reversed(messages)
            if m.get("role") == "tool" and m.get("name") == "search_flights"
        )
        top = last["payload"]["top"][0]
        return ModelOutput(
            kind="final",
            provider=self.name,
            text=(
                f"[secondary] Top option {top['flight_id']} at ${top['price']}. "
                "Booking deferred (degraded)."
            ),
        )


class DeterministicFallback:
    """Graceful degradation: no further model/tool side effects."""

    name = "deterministic"

    def complete(self, messages: list[dict[str, Any]], turn: int) -> ModelOutput:
        return ModelOutput(
            kind="final",
            provider=self.name,
            text=(
                "[deterministic] Agent capacity degraded. "
                "Escalated to human queue with prior trajectory preserved."
            ),
        )


# ---------------------------------------------------------------------------
# Runner: control-plane loop with max_turns, retries, breaker, fallbacks
# ---------------------------------------------------------------------------

@dataclass
class AgentRunner:
    providers: list[ModelProvider]
    tools: ToolRegistry
    breaker: CircuitBreaker
    retry: RetryPolicy
    max_turns: int = 10
    sleep_fn: Callable[[float], None] = time.sleep

    def run(self, goal: str, correlation_id: str | None = None) -> dict[str, Any]:
        cid = correlation_id or str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
        last_error: Exception | None = None
        degraded = False
        provider_used = ""

        for turn in range(self.max_turns):
            output: ModelOutput | None = None

            for provider in self.providers:
                if not self.breaker.allow():
                    LOG.warning(
                        "circuit_open_skip_provider",
                        extra=log_extra(
                            correlation_id=cid,
                            run_id=run_id,
                            turn=turn,
                            breaker_state=self.breaker.state.value,
                            provider=provider.name,
                        ),
                    )
                    continue

                output = self._call_with_retry(provider, messages, turn, cid, run_id)
                if output is not None:
                    provider_used = output.provider
                    if provider.name != self.providers[0].name:
                        degraded = True
                    break

            if output is None:
                # All providers exhausted this turn — deterministic last resort
                det = next((p for p in self.providers if p.name == "deterministic"), None)
                if det is None:
                    raise AgentError(f"all providers failed: {last_error!r}", FailureKind.POISON)
                output = det.complete(messages, turn)
                provider_used = output.provider
                degraded = True
                LOG.warning(
                    "graceful_degradation",
                    extra=log_extra(
                        correlation_id=cid,
                        run_id=run_id,
                        turn=turn,
                        breaker_state=self.breaker.state.value,
                        provider=provider_used,
                    ),
                )

            if output.kind == "final":
                LOG.info(
                    "run_complete",
                    extra=log_extra(
                        correlation_id=cid,
                        run_id=run_id,
                        turn=turn,
                        provider=provider_used,
                    ),
                )
                return {
                    "ok": True,
                    "final": output.text,
                    "turns": turn + 1,
                    "degraded": degraded,
                    "provider": provider_used,
                    "correlation_id": cid,
                    "run_id": run_id,
                }

            # Execute tools, append observations (data plane)
            for call in output.tool_calls:
                obs = self.tools.execute(call)
                messages.append(
                    {
                        "role": "tool",
                        "name": obs.name,
                        "call_id": obs.call_id,
                        "ok": obs.ok,
                        "payload": obs.payload,
                    }
                )
                LOG.info(
                    "tool_executed",
                    extra=log_extra(
                        correlation_id=cid,
                        run_id=run_id,
                        turn=turn,
                        provider=provider_used,
                    ),
                )

        # max_turns exceeded — graceful partial answer
        LOG.error(
            "max_turns_exceeded",
            extra=log_extra(correlation_id=cid, run_id=run_id, turn=self.max_turns),
        )
        return {
            "ok": False,
            "final": "Stopped at max_turns with partial trajectory; escalate to human.",
            "turns": self.max_turns,
            "degraded": True,
            "provider": provider_used or "none",
            "correlation_id": cid,
            "run_id": run_id,
        }

    def _call_with_retry(
        self,
        provider: ModelProvider,
        messages: list[dict[str, Any]],
        turn: int,
        cid: str,
        run_id: str,
    ) -> ModelOutput | None:
        for attempt in range(self.retry.max_attempts):
            LOG.info(
                "model_attempt",
                extra=log_extra(
                    correlation_id=cid,
                    run_id=run_id,
                    turn=turn,
                    attempt=attempt,
                    breaker_state=self.breaker.state.value,
                    provider=provider.name,
                ),
            )
            try:
                result = provider.complete(messages, turn)
                self.breaker.record_success()
                return result
            except AgentError as exc:
                if exc.kind is FailureKind.PERMANENT:
                    LOG.error(
                        "permanent_model_failure",
                        extra=log_extra(
                            correlation_id=cid,
                            run_id=run_id,
                            turn=turn,
                            attempt=attempt,
                            provider=provider.name,
                        ),
                    )
                    return None
                self.breaker.record_failure()
                if attempt + 1 >= self.retry.max_attempts:
                    return None
                delay = self.retry.delay(attempt)
                LOG.warning(
                    "transient_retry",
                    extra=log_extra(
                        correlation_id=cid,
                        run_id=run_id,
                        turn=turn,
                        attempt=attempt,
                        breaker_state=self.breaker.state.value,
                        provider=provider.name,
                    ),
                )
                self.sleep_fn(delay)
            except Exception:  # noqa: BLE001 — unknown faults treated as transient
                self.breaker.record_failure()
                if attempt + 1 >= self.retry.max_attempts:
                    return None
                self.sleep_fn(self.retry.delay(attempt))
        return None


def demo() -> None:
    runner = AgentRunner(
        providers=[
            PrimaryFakeModel(fail_times=2),
            SecondaryFakeModel(),
            DeterministicFallback(),
        ],
        tools=ToolRegistry(),
        breaker=CircuitBreaker(failure_threshold=5, recovery_timeout_sec=0.01),
        retry=RetryPolicy(max_attempts=4, base_delay_sec=0.01, max_delay_sec=0.05),
        max_turns=10,
        sleep_fn=lambda _: None,
    )
    result = runner.run(
        "Book the cheapest one-stop-or-direct flight SFO to JFK on 2026-10-10.",
        correlation_id="corr-agent-demo-001",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    demo()
```

Run: `python3 02-agent-runtime.py` (or paste into a file). Expected path: primary fails twice (transient 503), then succeeds through `search_flights` → `book_flight` → final PNR text. Raise `fail_times` or lower breaker threshold to exercise secondary (search-only degraded) or deterministic human-queue fallback. Logs emit JSON lines with `correlation_id`, `run_id`, `turn`, `attempt`, `breaker_state`, `provider`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Consumer flight-booking agent with purchase HITL

**Problem statement**  
A travel platform wants an agent that searches flights, filters by price/stops, emails itineraries, and books—Newsletter #111’s PEAS reference workload. Constraints: irreversible charges need human confirmation of flight/price/total; schema-validated tools only; context must not drop budget/airline prefs as tool dumps grow; target cost near ReWOO/plan-and-execute economics (~**$18–$27 / 1k** under Part 3 assumptions vs ~**$90 / 1k** naive ReAct); OpenAI-tier TPM must not collapse at ~30k tokens/full run on Tier 1 ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); Part 3; [OpenAI limits](https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt)).

**Proposed architecture**

```
┌──────────────┐  goal+prefs   ┌─────────────────┐  phase ckpt   ┌──────────────┐
│ API Gateway  │──────────────►│ Hybrid control  │──────────────►│ ReAct inner  │
│ + corr IDs   │               │ plan outer      │               │ per phase    │
└──────┬───────┘               └────────┬────────┘               └──────┬───────┘
       │                                │                               │
       │                       ┌────────▼────────┐                      │
       │                       │ Postgres ckpt / │                      │
       │                       │ Temporal HITL   │◄── approve purchase ─┤
       │                       └────────┬────────┘                      │
       │                                │                               │
       ▼                                ▼                               ▼
┌──────────────┐               ┌─────────────────┐               ┌──────────────┐
│ Telemetry    │               │ Tool proxies    │               │ External mem │
│ $ · RPM/TPM  │               │ search·book·mail│               │ full results │
└──────────────┘               │ schema+RBAC     │               │ top-3 only   │
                               └─────────────────┘               └──────────────┘
```

Technology: hybrid plan outer + ReAct inner ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)); OpenAI `Runner` `max_turns` + `needs_approval` on `book_flight` ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/); [HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)); LangGraph Postgres checkpointer + optional Temporal for durable approval ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Temporal LangGraph](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)); compress 47 flights → top 3 in context ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)).

**Trade-off matrix**

| Dimension | Alt 1: Pure ReAct tool loop | Alt 2: Hybrid plan + ReAct (recommended) | Alt 3: ReWOO / LLMCompiler DAG |
| --- | --- | --- | --- |
| **Cost** | ~**$90 / 1k** under Part 3 assumptions; highest frontier calls/step | Balanced; fewer planner calls than pure ReAct ([LangChain planning-agents](https://www.langchain.com/blog/planning-agents)) | ReWOO ~**5×** tokens; LLMCompiler up to **3.37–6.73×** cost vs ReAct on papers ([ReWOO](https://arxiv.org/html/2305.18323v1); [LLMCompiler](https://arxiv.org/html/2312.04511v3)) |
| **Latency** | Serial model+tool each step; worst p95 | Phase parallelism limited; better than naive ReAct | Best when many independent tools (LLMCompiler **1.8–3.7×**) |
| **Ops complexity** | Lowest code | Medium (checkpoint/phase design) | Higher (DAG planner quality, joiner) |
| **Security** | Approvals bolt-on; easy to miss | HITL at purchase phase boundary | Same need for purchase gate; static plan may skip re-verify |
| **Scalability** | Burns Tier 1 TPM fast (~30k tok/run) | Caching + smaller executors help | Parallel tools need concurrency caps on downstream airlines |

**Decision rationale**  
Recommend **Alt 2**: production booking needs mid-flight adapt (inventory changes) that pure ReWOO lacks, while pure ReAct is too expensive and context-bloated. Keep purchase behind code-enforced approval; use Temporal if approval can outlive the API worker. Prefer Alt 3 only when tool calls are highly parallel and plans are stable (search facets), not for the final charge step ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained); [ReWOO](https://arxiv.org/html/2305.18323v1)).

---

### Scenario B — Multi-tenant customer-support / coding orchestrator at Tier-bound RPM

**Problem statement**  
An enterprise ships an orchestrator-workers agent: supervisor decomposes tickets (or multi-file code edits), delegates to specialized subagents-as-tools, synthesizes answers. Measurable success (resolution, tests) matches Anthropic’s successful domains ([Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Constraints: OpenAI chatgpt-4o-class **500 RPM / 30k TPM** at Tier 1 rising to **10k RPM / 30M TPM** at Tier 5 ([OpenAI limits dump](https://cdn.openai.com/API/docs/txt/llms-models-pricing.txt)); context isolation between tenants/subagents; MCP tools allowlisted per tenant; runaway loops must hit `max_turns` with graceful partial output; no query-string OAuth tokens ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector); [Claude auth](https://claude.com/docs/connectors/building/authentication)).

**Proposed architecture**

```
┌─────────────┐  tenant ACL   ┌──────────────────┐  workers-as-tools  ┌─────────────┐
│ Ingress +   │──────────────►│ Supervisor       │───────────────────►│ Subagents   │
│ rate bucket │               │ (orchestrator)   │  parallel fan-out  │ (stateless) │
└──────┬──────┘               └────────┬─────────┘                    └──────┬──────┘
       │                               │                                     │
       ▼                               ▼                                     ▼
┌─────────────┐               ┌──────────────────┐                    ┌─────────────┐
│ Prompt cache│               │ LangGraph+PG /   │                    │ MCP HTTPS   │
│ stable tools│               │ Temporal resume  │                    │ allowlist   │
└─────────────┘               └──────────────────┘                    │ OAuth Bearer│
                                                                      └─────────────┘
```

Technology: Anthropic orchestrator-workers / LangChain subagents-as-tools ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)); OpenAI `max_turns` + `error_handlers["max_turns"]` ([OpenAI Running agents](https://openai.github.io/openai-agents-python/running_agents/)); evaluator-optimizer when eval criteria are clear ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)); ADK `AgentTool` + `LoopAgent` escalate as alternative packaging ([ADK](https://adk.dev/agents/workflow-agents/loop-agents/)); Zero-Trust MCP per Part 4.

**Trade-off matrix**

| Dimension | Alt 1: Single ReAct agent (all tools) | Alt 2: Supervisor + subagents-as-tools (recommended) | Alt 3: ADK workflow agents (Sequential/Loop/Parallel) |
| --- | --- | --- | --- |
| **Cost** | Highest context pollution; all tools in one window | Extra orchestration turns; isolation can cut tokens/worker ([LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)) | Predictable; may still need LlmAgent leaves for open tasks ([ADK](https://github.com/google/adk-docs/blob/main/docs/get-started/about.md)) |
| **Latency** | Long serial trajectories; ReAct loop risk ([LLMCompiler](https://arxiv.org/html/2312.04511v3)) | Parallel subagent calls in one supervisor turn | ParallelAgent helps sectioning; LoopAgent needs iteration caps |
| **Ops complexity** | Lowest | Medium–high (routing quality, handoff pairs) | Low–medium code-controlled outer loop |
| **Security** | Broad tool surface to one model | Per-subagent tool RBAC + tenant allowlists | Strongest outer determinism; over-constrains open tickets |
| **Scalability** | Hits TPM/RPM first | Route easy→Haiku, hard→frontier; cache stable supervisor tools ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents); [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)) | Best when phases are known a priori |

**Decision rationale**  
Recommend **Alt 2** when subtasks are unpredictable (multi-file edits, heterogeneous tickets)—Anthropic’s stated fit for orchestrator-workers ([Anthropic](https://www.anthropic.com/engineering/building-effective-agents)). Use Alt 3 when the outer control must be deterministic (compliance playbooks). Reject Alt 1 for multi-tenant: one agent with the union of tools maximizes blast radius and context collapse ([Newsletter #111](https://newsletter.systemdesign.one/p/ai-agents-explained)). Cap concurrency, honor `Retry-After` at the runner, and keep MCP tokens out of URLs ([OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [Claude auth](https://claude.com/docs/connectors/building/authentication)).

### Interview prompts

1. Draw control vs data plane for OpenAI `Runner` vs LangGraph vs ADK workflow agents—who owns the outer loop?
2. When is ReAct wrong on cost, and which published multiplier (ReWOO ~5×, LLMCompiler 3.37× HotpotQA) would you cite—and when must you not?
3. Why is a LangGraph checkpointer insufficient alone for process death, and what does Temporal add ([Temporal LangGraph Plugin](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution))?
4. Design Zero-Trust MCP: HTTPS only, allowlists, Bearer OAuth, no query-string tokens ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector); [Claude auth](https://claude.com/docs/connectors/building/authentication)).
5. Given Tier 1 **30k TPM** and a ~30k-token agent run, how do you size concurrency and back-pressure ([research §6](../research/02-how-ai-agents-work.md))?
