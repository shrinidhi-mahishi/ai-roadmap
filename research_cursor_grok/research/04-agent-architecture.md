# Research: Agent Architecture

**Date researched**: 2026-08-21
**Sources consulted**: 81

Scope: ReAct (Thought/Action/Observation; variants; when it fails); loops (max-iter, tool loops, HITL, event loops, streaming loops); planning (plan-and-execute, hierarchical, dynamic replanning); state (checkpointing, reducers, thread/session, LangGraph typed graphs); workflows (DAG vs cyclic, Temporal/Inngest/Prefect vs LLM graphs, fan-out/fan-in). Prices and limits below are from vendor docs or named papers as of 2026-08-21. ⚠️ No unpublished p50/p95/p99 agent-loop SLOs are invented; missing percentiles are marked. `$ per 1k executions` figures are **[inferred]** from published token rates × stated loop depths, not vendor SKUs.

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

An agent system is two planes. The **control plane** owns the loop: which LLM to call, which tools/agents are legal this turn, when to stop, where state is written, and how events stream to the client. The **data plane** is everything with side effects: tool adapters, MCP servers, sandboxes, knowledge bases, and other agents.

Invariant across OpenAI Agents SDK, Anthropic, Google ADK, LangGraph, CrewAI, and Bedrock AgentCore: **the model does not execute tools or handoffs**. It emits a structured action; the runtime dispatches; an observation is injected; the loop continues ([OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/); [Anthropic building effective agents](https://www.anthropic.com/engineering/building-effective-agents); [ADK custom agents](https://adk.dev/agents/custom-agents/)).

Anthropic’s 2024 split still holds in 2026: **workflows** = LLMs and tools on predefined code paths; **agents** = the LLM dynamically directs process and tool use ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)). Production stacks mix both: a deterministic outer graph (control) wrapping ReAct inner loops (data-plane tool I/O).

| Plane | Owns | Failure if conflated |
| --- | --- | --- |
| Control | Loop budget, routing, checkpoint key (`thread_id` / session / workflow-id), RBAC, stream mux | Infinite spend; lost resume; cross-tenant state |
| Data | Tool HTTP, MCP `tools/call`, A2A tasks, sandbox FS | Side effects on hallucinated args; duplicate charges |

### 1.2 ReAct: Thought / Action / Observation

Yao et al. (ICLR 2023) augment the action space to \(\hat{\mathcal{A}} = \mathcal{A} \cup \mathcal{L}\): language **thoughts** do not touch the environment; domain **actions** do. The trajectory is interleaved `Thought → Action → Observation` ([ReAct arXiv](https://arxiv.org/abs/2210.03629); [HTML v3](https://arxiv.org/html/2210.03629v3)).

Thoughts in the paper: decompose goals, extract from observations, inject commonsense, reformulate search, synthesize answers. For QA they used **dense** TAO steps; for ALFWorld/WebShop, thoughts are **sparse** (the LM decides when to think). Action space for HotpotQA/FEVER: `search[entity]`, `lookup[string]`, `finish[answer]` against a weak Wikipedia API (first 5 sentences / Ctrl+F simulation) — deliberately not a neural retriever.

**PaLM-540B prompting (paper Table 1):**

| Method | HotpotQA EM | FEVER Acc |
| --- | --- | --- |
| Standard | 28.7 | 57.1 |
| CoT | 29.4 | 56.3 |
| CoT-SC (21 samples, T=0.7) | 33.4 | 60.4 |
| Act-only | 25.7 | 58.9 |
| ReAct | 27.4 | 60.9 |
| CoT-SC → ReAct | 34.2 | 64.6 |
| ReAct → CoT-SC | **35.1** | 62.0 |
| Supervised SoTA (then) | 67.5 | 89.5 |

ALFWorld / WebShop: 1–2-shot ReAct beat IL/RL trained on \(10^3\)–\(10^5\) instances by **+34 pp** and **+10 pp** success. The authors capped ReAct at **7 steps (HotpotQA)** and **5 (FEVER)**; extra steps recovered only 0.84% / 1.33% of already-correct trajectories.

**When ReAct fails (human labels, 200 HotpotQA trajectories, Table 2):**

| Mode | ReAct | CoT |
| --- | --- | --- |
| Success: true positive | 94% | 86% |
| Success: false positive (hallucinated facts) | 6% | 14% |
| Failure: reasoning error (incl. **repetitive TAO loops**) | **47%** | 16% |
| Failure: empty/useless search | **23%** | n/a |
| Failure: hallucination | **0%** | **56%** |
| Failure: label ambiguity | 29% | 28% |

Grounding kills hallucination; the same interleaving **reduces reasoning flexibility** and creates a signature failure: greedy decode repeats the previous thought+action. Production implication: ReAct needs an **external** loop breaker; the model will not reliably stop itself.

### 1.3 Variants (same family, different control graphs)

| Variant | Control topology | What changes vs ReAct | Named numbers (paper, not production SLOs) |
| --- | --- | --- | --- |
| **Act-only** | Action→Obs | No thoughts | Worse than ReAct on HotpotQA (25.7 vs 27.4 EM) |
| **ReAct → CoT-SC / reverse** | Sequential backoff | Use external KB *or* internal majority vote | Best HotpotQA 35.1 EM |
| **Plan-and-Solve / PS+** | Plan then execute in one generation | Zero-shot “devise a plan… then carry out”; PS+ adds variable extraction + intermediate calc | PS+ vs Zero-shot-CoT: MultiArith **91.8**, GSM8K **59.3** (+2.9 pp), avg math **76.7**; CSQA 71.9 vs 65.2 ([ACL 2023](https://aclanthology.org/2023.acl-long.147)) |
| **Plan-and-Execute** (LangChain) | Planner LLM + executor ReAct | Plan is data; executor walks steps; replanner optional | Architectural, not a single bench ([planning agents](https://www.langchain.com/blog/planning-agents)) |
| **ReWOO** | Planner → Worker(s) → Solver | Thoughts **decoupled** from observations; blueprint then tool burst | **5×** token efficiency, **+4 pp** HotpotQA vs interleaved ALMs ([arXiv 2305.18323](https://arxiv.org/abs/2305.18323)) |
| **HuggingGPT** | Plan → select HF models → execute → summarize | LLM as controller over modality-specific models | NeurIPS 2023 four-stage pipeline ([arXiv 2303.17580](https://arxiv.org/abs/2303.17580)) |
| **LLMCompiler** | Streamed DAG + task-fetch + joiner/replan | Parallel function DAG; args can be `$1` from prior tasks | Up to **3.7×** latency, **6.7×** cost, **~9 pp** accuracy vs ReAct; **1.35×** vs OpenAI parallel FC ([arXiv 2312.04511](https://arxiv.org/abs/2312.04511)) |
| **Tree of Thoughts** | BFS/DFS over thought nodes | Lookahead + backtrack; not tool-centric | Game of 24: GPT-4 CoT **4%** vs ToT **74%** ([arXiv 2305.10601](https://arxiv.org/abs/2305.10601)) |
| **LATS** | MCTS over ReAct steps | LM as actor, value, reflection | HumanEval GPT-4 pass@1 **92.7%** (abstract); WebShop GPT-3.5 avg **75.9** ([arXiv 2310.04406](https://arxiv.org/abs/2310.04406)) |
| **Reflexion** | Actor + Evaluator + Self-Reflection + episodic buffer | Verbal RL across **trials**, not within one trajectory | HumanEval pass@1 **91%** vs GPT-4 **80%** ([NeurIPS 2023 PDF](https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf)) |

**Production mapping:** ReAct ≈ LangGraph `bind_tools` + `ToolNode` + `tools_condition`; OpenAI Agents SDK `Runner` loop; Anthropic “autonomous agent” (tools in a loop with max iterations). Plan-and-execute ≈ Anthropic **orchestrator-workers** (subtasks **not** known a priori) vs **parallelization** (fixed fan-out). Evaluator-optimizer ≈ ADK `LoopAgent` + escalate, or Reflexion without a second trial ID.

### 1.4 Loops: five distinct clocks

Do not collapse these. They have different meters, stop conditions, and cost functions.

**A. Max-iteration / recursion (control-plane fuse).**

- OpenAI Agents SDK: a **turn** = one model invocation including any tool calls that occur with it. Default `maxTurns` / `max_turns` = **10**; `MaxTurnsExceeded` / `MaxTurnsExceededError`; pass `None`/`null` to disable ([Python running agents](https://openai.github.io/openai-agents-python/running_agents/); [JS running agents](https://openai.github.io/openai-agents-js/guides/running-agents/)). ⚠️ “Turn” ≠ LangGraph super-step.
- LangGraph: `recursion_limit` default **25 supersteps**; `GraphRecursionError` / `GRAPH_RECURSION_LIMIT`. Raise only when the work is genuinely long: `graph.invoke(..., {"recursion_limit": 100})` ([error doc](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT)). One ReAct tool cycle is typically **2 supersteps** (model node + tool node) → default ≈ **12 tool rounds** before the fuse. Pair with a state `RemainingSteps` / `max_iterations` that routes to `END` *before* the hard error.
- ADK `LoopAgent`: sequential sub-agents until `max_iterations` **or** any sub-agent emits `escalate=True` ([workflow patterns](https://adk.dev/workflows/patterns/); [custom agents](https://adk.dev/agents/custom-agents/)). Example in docs: `max_iterations=5` for code refinement.
- CrewAI hierarchical: workers should set `allow_delegation=False` or manager↔worker ping-pong is unbounded ([hierarchical process](https://docs.crewai.com/edge/en/learn/hierarchical-process)).

**B. Tool loops (data-plane inner ReAct).** Same tool with the same args, or pagination-by-LLM (`page=1` forever). Adapter must: cap `limit`, return `is_error` on 4xx (except 429), refuse POST without idempotency key, and treat identical `(tool, canonical_args)` N times as a **circuit** (see §3/§5). ReAct paper: repetitive thoughts/actions are the dominant *reasoning* failure (47%).

**C. Human-in-the-loop (HITL).** Pause without burning a GPU/worker.

- LangGraph: `interrupt(value)` requires a checkpointer; resume with `Command(resume=...)`. Node **restarts from the top** on resume; multiple interrupts match resume values **by order**. Event streaming v3: `stream.interrupted` / `stream.interrupts` ([interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).
- OpenAI Agents SDK: first-class HITL plus `AbortSignal`; Temporal TS integration waits on Signal/Update then Continue-As-New ([Temporal openai-agents](https://docs.temporal.io/develop/typescript/integrations/openai-agents)).
- ADK Go 2.0: HITL is a built-in primitive; interrupt format shared with Python so a paused graph can resume across runtimes; run state lives in the session ([ADK Go 2.0](https://developers.googleblog.com/announcing-adk-go-20/)).
- Inngest AgentKit: HITL tool = `step.waitForEvent` (e.g. 4h timeout, `match: "data.ticketId"`) — **zero compute** while paused ([AgentKit HITL](https://agentkit.inngest.com/advanced-patterns/human-in-the-loop); [waitForEvent](https://www.inngest.com/docs/features/inngest-functions/steps-workflows/wait-for-event)). Losing `waitForEvent` inside `group.parallel()` is **not cancelled** until timeout — keep timeouts tight.
- CrewAI Flows: `@human_feedback` (v1.8.0+); `@persist` for crash/HITL resume ([production architecture](https://docs.crewai.com/en/concepts/production-architecture)).

**D. Event loops (runtime, not ReAct).** ADK Runner is an **ask–yield event loop**: user message + session id → internal events (model, tools) → streamed events to client ([The New Stack ADK tour](https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/)). Inngest functions are event-triggered; Temporal workflows are event-sourced (history replay). These loops outlive a single LLM call.

**E. Streaming loops.** Control plane must mux tokens **and** tool/interrupt events without executing on partial JSON.

- LangGraph stream modes: `values` (full state/step), `updates` (per-node deltas; parallel nodes emit separately), `messages` (token, metadata), `custom`, `checkpoints`, `tasks`, `debug`. v1.2+ **event streaming** gives independent iterators (`stream.messages`, `stream.values`, `stream.interrupts`) ([streaming](https://docs.langchain.com/oss/python/langgraph/streaming)).
- OpenAI: `Runner.run_streamed()` → `RunResultStreaming.stream_events()`. Handoff `input_filter` **does not stream**; items already streamed stay streamed. Server-managed conversations (`conversation_id` / `previous_response_id`) **do not support** handoff input filters ([handoffs](https://openai.github.io/openai-agents-python/handoffs/)).
- Temporal TS: `{ stream: true }` publishes model events onto a Workflow Stream; labeled **experimental** ([Temporal openai-agents](https://docs.temporal.io/develop/typescript/integrations/openai-agents)).
- ADK: native multimodal streaming (text/voice/video) from the same event loop ([ADK docs repo](https://github.com/google/adk-docs)).

### 1.5 ReAct vs DAG vs supervisor (topology choice)

Anthropic names five **workflow** patterns plus agents ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)):

1. Prompt chaining (fixed sequence + optional gates)
2. Routing (classifier → specialist)
3. Parallelization: **sectioning** (independent subtasks) or **voting** (same task N ways)
4. Orchestrator-workers (LLM decides subtasks **at runtime**)
5. Evaluator-optimizer (generate ↔ critique loop)

Google Cloud’s 2025-11-25 architecture center: pick pattern from task complexity, latency, cost, HITL need; single-agent first, multi-agent when one model’s context/tools degrade ([choose a design pattern](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system); [overview](https://docs.cloud.google.com/architecture/agentic-ai-overview)).

| Topology | Cycle? | Who picks next hop | Typical runtime |
| --- | --- | --- | --- |
| DAG (Prefect `.map`, Airflow, static LangGraph without back-edges) | No | Engineer | ETL, fixed RAG pipelines |
| Cyclic ReAct graph | Yes | Model + `tools_condition` | Tool-using assistants |
| Supervisor / hierarchical | Yes at manager; workers often DAGs or ReAct | Manager LLM | CrewAI `Process.hierarchical`, Bedrock Classic supervisor+collaborators, LangGraph supervisor node |
| Orchestrator-workers | Fan-out DAG per plan, then join | Orchestrator | Anthropic coding/search; ADK ParallelAgent + gather |
| Plan-and-execute | Outer DAG of steps; inner ReAct optional | Planner then executor | LangChain planning agents; HuggingGPT |

LangGraph’s reason to exist: **a ReAct loop is not a DAG**. Nodes compute; conditional edges (and `Send`) decide next; typed state carries memory across cycles ([LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview); [graph API](https://docs.langchain.com/oss/python/langgraph/graph-api.md)).

**OpenAI two multi-agent contracts** ([orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)):

- **Handoffs**: specialist **owns** the next user-facing reply; runner swaps current agent and re-enters the loop.
- **Agents-as-tools**: manager **keeps** the reply; specialist is a bounded capability.

`nest_handoff_history=True` is opt-in beta: compact history into ordered assistant summary segments; disabled by default. Individual `Handoff.input_filter` overrides nesting.

**CrewAI:** start production apps as a **Flow** (state, loops, conditionals), delegate islands of autonomy to Crews. Sequential process = DAG of tasks; hierarchical = manager LLM + workers ([production architecture](https://docs.crewai.com/en/concepts/production-architecture); [GitHub](https://github.com/crewaiinc/crewai/)).

**ADK workflow agents:** `SequentialAgent` (shared `InvocationContext`), `ParallelAgent` (branch names like `Parent.Child` for history isolation; **same** `session.state` — distinct keys required), `LoopAgent` ([workflow patterns](https://adk.dev/workflows/patterns/)). ADK Go 2.0 unifies `LlmAgent` and graph nodes on one runtime (`Chat` / `Task` / `SingleTurn` modes; helpers `finish_task`, `single_turn`, `task`).

**Bedrock:** Agents Classic (supervisor + collaborator agents; **closed to new customers**) vs **AgentCore** (framework-agnostic Runtime hosting LangGraph/CrewAI/ADK/OpenAI Agents/Strands; MCP + A2A) ([Classic MAC](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-multi-agent-collaboration.html); [what is AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)).

### 1.6 Sync / async / streaming execution

| Mode | Semantics | Use |
| --- | --- | --- |
| Sync `invoke` / `run_sync` | Block until final output or interrupt | Scripts, tests |
| Async `ainvoke` / `Runner.run` | Same loop, non-blocking | APIs, fan-out workers |
| Stream | Partial tokens + graph events | UX; HITL detection |
| Durable (Temporal Activity / Inngest `step.run` / Prefect task) | Await without holding a process | Hours–months HITL; retries |

LangGraph Pregel **super-step**: all scheduled nodes run (possibly in parallel); then reducers merge; then checkpoint. `Send(node, state)` from a conditional edge is **dynamic fan-out** with per-child state (map-reduce); fan-in is a reducer on a shared channel ([use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)). Concurrent writes to a key **without** a reducer → `InvalidUpdateError` (“Can receive only one value per step”).

### 1.7 Agent-to-agent vs tool dispatch

Two protocols, complementary ([A2A site](https://a2a-protocol.org/latest/); [Google announcement](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)):

- **MCP** = agent → tools/resources (JSON-RPC `tools/list`, `tools/call`).
- **A2A** = agent → agent: Agent Card discovery, **task lifecycle**, messages, artifacts, streaming, push notifications. Spec 1.0.0; proto is normative ([A2A spec](https://a2a-protocol.org/v1.0.0/specification/)). Linux Foundation; GitHub created 2025-03-25. A2A is **not** an agent framework and **not** a replacement for MCP.

In-process dispatch (LangGraph `ToolNode`, Agents SDK `FunctionTool`, ADK `FunctionTool`, CrewAI `BaseTool`) is cheaper and simpler than A2A; use A2A when the peer is a **different trust domain / vendor / language**. Google’s enterprise pattern: ADK orchestrator on Cloud Run, MCP servers as anti-corruption layers to backends, optional A2A for remote agents ([orchestrate disparate systems](https://docs.cloud.google.com/architecture/agenticai-orchestrate-access-disparate-systems); [single-agent ADK + Cloud Run](https://docs.cloud.google.com/architecture/single-agent-ai-system-adk-cloud-run)).

---

## 2. Token Economics & NFR Metrics

### 2.1 Published prices (API, 2026-08-21)

**OpenAI** per 1M tokens, short context ([Pricing](https://developers.openai.com/api/docs/pricing); [openai.com/api/pricing](https://openai.com/api/pricing/)):

| Model | Input | Cached in | Cache write | Output |
| --- | --- | --- | --- | --- |
| gpt-5.6-sol | $5.00 | $0.50 | $6.25 | $30.00 |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 |

Long context (>270k on that table): sol $10 / $1 / $12.50 / $45. Cache writes **1.25×** uncached input on GPT-5.6+; earlier families: writes free ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Cached input still counts toward **TPM**. Web search: **$10 / 1k calls** + content tokens. File search: **$2.50 / 1k calls** + $0.10/GB-day (1 GB free). Containers (shell / code interpreter): 1 GB $0.03 … 64 GB $1.92 **per 20-minute session**. Regional processing: **+10%** on models released on/after 2026-03-05. Fast mode (née priority): separate multiplier — ⚠️ not a loop SLO.

**Anthropic API** ([anthropic.com/pricing](https://www.anthropic.com/pricing)):

| Model | In / Out / 5-min cache write / cache read ($/MTok) |
| --- | --- |
| Fable 5 | 10 / 50 / 12.50 / 1 |
| Opus 5 / 4.8 / 4.7 / 4.6 / 4.5 | 5 / 25 / 6.25 / 0.50 |
| Sonnet 5 | 2 / 10 / 2.50 / 0.20 |
| Sonnet 4.6 / 4.5 | 3 / 15 / 3.75 / 0.30 |
| Haiku 4.5 | 1 / 5 / 1.25 / 0.10 |
| Opus 4.1 (legacy) | 15 / 75 / 18.75 / 1.50 |

Prompt caching: 5-min write **1.25×**, 1-hour write **2×**, read **0.10×**. Batch **−50%**. US-only inference **1.1×**. Opus 5 fast mode **2×** tokens for up to 2.5× speed. **Managed Agents: $0.08 per session-hour** active runtime + standard tokens. Web search **$10 / 1k**. Code execution: 50 free hours/org/day, then **$0.05/hour/container**. Enterprise Claude: **$20/seat/month + API rates**; Team standard **$20/seat/yr** ($25 monthly).

⚠️ Anthropic consumer Max/Pro usage windows are **5-hour rolling**, not RPM — do not use as agent SLO.

### 2.2 Cost of extra loop iterations **[inferred]**

ReAct’s bill is **quadratic-ish in turns** unless the stable prefix caches.

Assumptions for a support agent: 8k-token frozen prefix (system + tools), 400 output tokens/turn, 600 new input tokens/turn (user/tool obs), `gpt-5.6-terra`, cache hit on prefix from turn 2, short context.

| Turns | Uncached in | Cached in | Out | **$ / run** | **$ / 1k runs** |
| --- | --- | --- | --- | --- | --- |
| 1 (no tools) | 8.0k | 0 | 0.4k | 0.0208 | **21** |
| 3 (2 tools) | 8.0 + 2×0.6 = 9.2k | 16k | 1.2k | 0.0356 | **36** |
| 10 (SDK default cap) | 8.0 + 9×0.6 = 13.4k | 72k | 4.0k | 0.0872 | **87** |
| 25 (LangGraph default fuse ≈ 12 tool rounds if 2 nodes/round — here counted as 25 model calls) | 8.0 + 24×0.6 = 22.4k | 192k | 10.0k | 0.203 | **203** |

Formula used: `$ = (uncached_M × 2) + (cached_M × 0.20) + (out_M × 12)`. First turn writes cache at **$2.50/M** on GPT-5.6: add **8k × $2.50/M = $0.020** once per cold prefix (**+$20 / 1k** cold starts). ⚠️ If the prefix mutates (tool list shuffle, timestamp in system prompt), cache hit rate → 0 and the 10-turn row jumps to **[inferred] ~$0.22/run ($220/1k)** because all 8k×10 are billed at $2.

**Anthropic same shape, Sonnet 5, 5-min cache:** read $0.20/M, write $2.50/M, out $10/M. 10-turn cached: **[inferred] ~$0.074/run ($74/1k)** plus one write $0.020. Orchestrator-workers: 1 Sonnet plan + N Haiku workers + 1 Sonnet synthesize. For N=8, 2k in / 800 out each: **[inferred] 2×(2k×$2 + 0.8k×$10) + 8×(2k×$1 + 0.8k×$5) = $0.040 + $0.048 = $0.088/run** — cheaper than 10-turn ReAct **if** workers stay narrow; **cost multiplier vs one Sonnet call** is the reason Anthropic says start with workflows ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents); July 2026 tutorial same split).

**ReWOO / LLMCompiler vs ReAct:** papers claim **5× tokens** (ReWOO) and **6.7× cost** (LLMCompiler) by not re-sending the full trajectory to the planner every tool hop. Production: put the planner on a cached prefix; execute tools **outside** the LLM; join once.

**ToT / LATS:** Game-of-24 ToT is **many** LLM calls per puzzle (thought samples × tree depth × value). Treat as **research spend**, not a chat SKU. Reflexion is **N trials × ReAct cost** plus reflection tokens in the next context.

### 2.3 Caching, routing, RPM/TPM

**Cache hygiene for agents:** stable byte-prefix: tools → system → few-shot → **then** mutating messages. OpenAI: 1024-token minimum; `prompt_cache_key` when >~15 RPM against one prefix; GPT-5.6 explicit breakpoints + 30-minute TTL. Anthropic: you place `cache_control`; TTL refresh on hit. Gemini: implicit cache floors 1024 (Flash) / 2048–4096 (Pro-class) — ⚠️ confirm per model card.

**Routing (cost control plane):** Anthropic’s own example: Haiku for easy, Sonnet for hard. OpenAI: luna for high-volume inner workers, terra for supervisor, sol for the 5% hard tail. Each hop is a full request against **that model’s** RPM/TPM.

**OpenAI org tiers** ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits)): Free $100/mo; T1 $5 paid / $100; T2 $50 / $500; T3 $100 / $1k; T4 $250 / $5k; T5 $1k / **$200k/mo**. Limits are **org + project**, not user. ⚠️ Per-model RPM/TPM live in the developer console and change; a 2026 secondary compilation lists GPT-5.6 sol/terra T1 **500 RPM / 500k TPM**, T5 **15k RPM / 40M TPM**; luna T5 **30k / 180M** ([ScriptByAI tables](https://www.scriptbyai.com/rate-limits-openai-api/) — treat as **unofficial**; console wins). Cached tokens **count** toward TPM.

**NFR latency:** ⚠️ OpenAI/Anthropic/Google do **not** publish p50/p95/p99 for *agent loops*. What exists:

- LLMCompiler: up to **3.7×** wall-clock vs sequential ReAct on their benches (parallel DAG).
- Sequential ReAct **[inferred]**: `T_loop ≈ Σ_i (TTFT_i + T_decode_i + T_tool_i)`. p99 is dominated by **slowest tool** + **longest decode**, not average TTFT.
- Fan-out: p99 ≈ max(worker p99) + join LLM; voting ×N multiplies **cost** linearly, **latency** stays ~one call if parallel.
- HITL: p99 is the human SLA (Inngest example 4h), not the model.

Measure in LangSmith / Agents SDK traces / ADK traces: turn count, cached_tokens, cache_write_tokens, tool latency histogram. Do not put a made-up p95 in an architecture review.

### 2.4 `$ per 1k executions` capacity sketch **[inferred]**

Support bot, 1k conversations/day, mix 70% 1-turn luna, 25% 3-turn terra, 5% 10-turn terra, 80% prefix cache hit, ignore tools’ own SaaS fees:

`0.7×1000×$0.0025 [inferred luna 8k+400] + 0.25×1000×$0.036 + 0.05×1000×$0.087 ≈ $1.75 + $9 + $4.4 ≈ **$15/day model**`.

Add Anthropic Managed Agents runtime if used: 1k sessions × 2 min active = 33.3 session-hours × $0.08 = **$2.67/day**. Tools: 200 web searches × $10/1k = **$2**. Order-of-magnitude **~$20/day** before retries, evals, and 5% sol escalations. A runaway 25-turn terra fleet at 1k/day is **~$203/day** — 10× — which is why max-turns is a **financial** control, not just a correctness fuse.

---

## 3. Distributed Resilience & State

### 3.1 LLM graphs vs durable workflow engines

| System | Abstraction | Checkpoint grain | Pause/HITL | Replay | Best at |
| --- | --- | --- | --- | --- | --- |
| **LangGraph** | Cyclic typed `StateGraph` | Super-step snapshot + per-task writes | `interrupt` | Time travel / fork | Agent reasoning + mixed deterministic nodes |
| **Temporal** | Workflow + Activities | Event history | Signal / Update | Deterministic workflow replay; Activities **not** re-executed | Months-long agents; exactly-once side effects |
| **Inngest** | Functions + `step.run` | Per-step memo | `waitForEvent` / `sleep` | Replay function; completed steps cached | Serverless; HITL days; wrap LangGraph inside a step |
| **Prefect 3** | Flows + tasks | Task run state | UI retry / pause | Skip completed tasks on retry | Data + `PrefectAgent` wrapping pydantic-ai |
| **CrewAI Flow** | Event-driven methods | `@persist` / `flow_uuid` | `@human_feedback` | Resume or `restore_from_state_id` fork | Productized Crews with an outer Flow |
| **ADK SessionService** | Event log + `state` prefixes | Event + `state_delta` | LongRunning tools / Go 2.0 HITL | Reconstruct from session history (Go 2.0) | Google deploy (Cloud Run / Agent Runtime) |

Compose: **LangGraph (cognition) inside Temporal/Inngest/Prefect (durability)**. Temporal × OpenAI Agents SDK **GA 2026-03-23**: orchestration in Workflow, model calls as Activities so replay does not re-bill tokens ([Temporal blog](https://temporal.io/blog/announcing-openai-agents-sdk-integration); [TS docs](https://docs.temporal.io/develop/typescript/integrations/openai-agents)). `useLocalActivity: true` keeps history smaller. Prefect: `PrefectAgent` wraps LLM + tools as tasks; example retries LLM 3× backoff 1s/2s/4s, tools 2×, 60s timeout ([Prefect + pydantic-ai](https://docs.prefect.io/v3/examples/ai-data-analyst-with-pydantic-ai)).

### 3.2 LangGraph state: typed graphs, reducers, threads, durability

**State** = `TypedDict` / Pydantic schema. Channels default to **LastValue** (overwrite). `Annotated[list, operator.add]` (or a custom reducer) **merges** — required for messages and for parallel fan-in ([graph API](https://docs.langchain.com/oss/python/langgraph/graph-api.md); [use graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).

**Threads:** `configurable.thread_id` is the primary key. No `thread_id` ⇒ no save, no interrupt resume ([checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Production: `thread_id = f"{tenant}:{user}:{session}"` — a constant string shares history across users (documented failure mode in field blogs).

**Checkpoint grain:** full `StateSnapshot` at each **super-step**; **task-level writes** as nodes finish so a sibling failure does not re-run successful parallel nodes (**pending writes**). Time travel resumes at super-step boundaries, not mid-node. `update_state` creates a **new** checkpoint; reducers still apply.

**Durability modes** (`Durability = "sync"|"async"|"exit"`):

- `sync`: persist **before** next step (slowest, safest)
- `async` (**default**): persist while next step runs
- `exit`: persist only when the graph exits (less duplication; **lose** mid-run on pod kill)

([types.py](https://github.com/langchain-ai/langgraph/blob/2e5025ec1ac8d435840ed4a972097de87aaa2eab/libs/langgraph/langgraph/types.py); [ainvoke](https://reference.langchain.com/python/langgraph/pregel/main/Pregel/ainvoke)). LangChain support: PostgresSaver with a raw `Connection` holds the connection for the **entire run** → timeouts; use `ConnectionPool`; TTL + `exit` durability to control disk ([support article](https://support.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl)).

**Checkpointer vs Store** ([persistence](https://docs.langchain.com/oss/python/langgraph/persistence)): checkpointer = short-term **thread** memory (HITL, time travel, crash); Store = long-term **cross-thread** KV (prefs, facts). Subgraphs do not automatically share parent checkpoints.

**DeltaChannel (beta):** store a sentinel in checkpoint blobs; reconstruct by replaying ancestor writes through a **deterministic, batching-invariant** reducer. Snapshot when update count hits `snapshot_frequency` **or** supersteps since snapshot hit `DELTA_MAX_SUPERSTEPS_SINCE_SNAPSHOT` (**default 5000**) ([DeltaChannel](https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel)). Makes accumulating `messages` O(1) blob size per step instead of O(N). On-disk format **not stable**.

**Backends:** MemorySaver (dev; dies with process), SqliteSaver (single box; write lock), PostgresSaver (prod). Field reports: Postgres write **~5–15 ms**, **~3–8 ms** with asyncpg pool — ⚠️ not a vendor SLO ([AlterSquare](https://altersquare.io/langgraph-state-management-undocumented-issues-after-commit/)).

### 3.3 ADK / CrewAI / OpenAI session state

**ADK** `session.state` prefixes ([state](https://adk.dev/sessions/state/)):

| Prefix | Scope | Example |
| --- | --- | --- |
| (none) | this session | `current_intent` |
| `user:` | all sessions for `user_id` in `app_name` | `user:preferred_language` |
| `app:` | all users of the app | `app:api_endpoint` |
| `temp:` | this invocation (shared down the agent tree) | `temp:raw_api_response` |

Mutate via `CallbackContext`/`ToolContext`/`output_key`/`EventActions.state_delta` so `append_event` persists. Direct `Session.state[...] =` **bypasses** the event log → lost updates. `DatabaseSessionService`: **per-session lock**; `InMemorySessionService` **not** multi-thread safe. `{key?}` optional injection; missing required `{key}` throws.

**OpenAI Agents SDK Sessions:** default `Runner.run` is **stateless** across calls; attach a Session (incl. Redis in production guides) for chat memory. `trace_include_sensitive_data` / `OPENAI_AGENTS_DISABLE_TRACING_SENSITIVE_DATA` strip LLM I/O and tool args from traces.

**CrewAI `@persist`:** resume with `kickoff(inputs={"id": ...})`; `restore_from_state_id` forks a new `state.id` (cannot combine with some checkpoint flags — `ValueError`).

### 3.4 Temporal / Kafka-class limits

Temporal history: warn **10,240 events / 10 MB**; terminate **51,200 events / 50 MB**; also **2,000 Updates / 10,000 Signals** ([events](https://docs.temporal.io/workflow-execution/event); [long-running blog](https://temporal.io/blog/very-long-running-workflows)). **Continue-As-New** passes latest state into a new RunId with empty history ([continue-as-new](https://docs.temporal.io/workflow-execution/continue-as-new)). Agent implication: **do not** put full tool payloads / screenshots in Activity **return values** — a 500 KB result × 100 tools ≈ 50 MB. Use blob store + handle in history. LLM tokens belong in Activity results so **replay does not re-call the API**.

Kafka (or any log): commit the offset **after** the tool/LLM Activity succeeds; otherwise crash-retry duplicates. Combine with idempotency keys on tools. ⚠️ Kafka is not an agent runtime; it is the **data plane bus** Temporal/Inngest already replace for many teams.

### 3.5 Locking, circuit breakers, rate limits

**Locking:** ADK DB session lock; LangGraph Postgres row-level; Sqlite file lock under concurrency. ParallelAgent / LangGraph fan-out: **reducer or distinct keys**, else lost updates / `InvalidUpdateError`.

**429 vs breaker** ([TrueFoundry](https://www.truefoundry.com/blog/llm-failover-load-balancing-provider-outages); [BackendBytes](https://backendbytes.com/articles/llm-provider-outage-resilience/); [Ranjan Kumar](https://ranjankumar.in/fault-isolation-circuit-breaking-llm-agent-pipelines)):

- **429**: your quota. Honor `Retry-After` / `x-ratelimit-*` / `anthropic-ratelimit-*-reset`. **Do not** trip the provider circuit or fail over (you replicate the spike). Exception: billing 429 → halt spend.
- **5xx / 529 / timeout / mid-stream**: breaker (closed → open → half-open). Fail fast vs waiting full LLM timeout.
- Retry **exactly one layer** (SDK **or** gateway). Nested 3×3×3 = 27 upstream calls (SRE amplification).
- LangGraph `RetryPolicy` defaults: `initial_interval=0.5`, `backoff_factor=2`, `max_attempts=3`, `jitter=True`; does **not** parse `Retry-After`. On node failure with checkpointer: pending writes kept; checkpoint does not advance.

**Gateway pattern (TrueFoundry 3-layer):** (1) token bucket per `(user, repo, model)`; (2) pattern breaker (identical-prompt loop, cost velocity, consecutive 429s, >50% errors/60s); (3) fallback chain primary → cheaper model → semantic cache → 503.

Inngest/Temporal retries must **cap** LLM Activities: unbounded retry × $30/M output = bill shock.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP

MCP HTTP auth (spec **2025-11-25**): MCP server = OAuth 2.1 **resource server**; clients use RFC 9728 Protected Resource Metadata; AS metadata RFC 8414 or OIDC; **RFC 8707 resource indicators**; **PKCE mandatory**; no implicit/password grants ([MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)).

Hard rules from spec + 2026 enterprise write-ups:

- **No token passthrough** to downstream APIs (confused deputy). Token-exchange / separate client-credentials per hop ([Tian Pan, 2026-05-09](https://tianpan.co/blog/2026-05-09-oauth-mcp-threading-user-identity-tool-servers)).
- Audience-validate: token for `mcp.other.com` must fail even if signature is valid.
- Scopes at **tool** grain (`mcp:tool:{name}:{read|execute}`), not server-wide admin ([CSA agentic MCP](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/); OWASP Agentic **ASI01 Agent Goal Hijack** via poisoned tool descriptions).
- DPoP (RFC 9449) for write/admin-class tokens where SDKs allow.
- Separate **read MCP** from **write MCP** so retrieved tickets cannot instruct `send_email`.

AgentCore: platform-layer auth for MCP + Lambda + KB in one turn; policies checked with automated reasoning (IAM/S3 lineage) ([AgentCore](https://aws.amazon.com/bedrock/agentcore/)).

### 4.2 Tool RBAC and sandbox

| Control | Where enforced | Notes |
| --- | --- | --- |
| Tool allowlist per agent role | Control plane (graph compile / Agent.tools) | Supervisor must not inherit worker destructive tools |
| Argument schema + adapter caps | Data plane | Model cannot pass `limit=10e9` |
| Human approval | HITL interrupt / Inngest wait / Claude permission UX | Destructive tools only |
| Sandbox | E2B / Firecracker / Bedrock code interpreter / Anthropic $0.05/h container | CPU/mem/egress limits |
| Network egress allowlist | Sidecar / AgentCore | SSRF from tool URLs |

CrewAI / ADK: tools at **agent** and **task** level — prefer task-level for least privilege.

### 4.3 PII and audit of trajectories

A trajectory is a **regulated artifact**: prompts, thoughts, tool args, observations, plan text.

- LangSmith: `create_anonymizer` regex; `LANGSMITH_HIDE_INPUTS/OUTPUTS`; OTEL collector transform to strip `gen_ai.prompt` / `gen_ai.completion` **before** SaaS ([mask](https://docs.langchain.com/langsmith/mask-inputs-outputs); [redact secrets](https://docs.langchain.com/langsmith/redact-secrets); [OTEL redaction](https://docs.langchain.com/langsmith/otel-gateway-trace-redaction)).
- OpenAI Agents SDK: `trace_include_sensitive_data=False` keeps span topology, drops I/O.
- Audit tuple: `{tenant, user, agent, thread_id, turn, tool, schema-valid args hash, decision, model, cache_hit}` — store **hashes + redacted preview** in SIEM; full payload in a customer-managed bucket with TTL.
- Thoughts are **not** safe to log unredacted: they copy PII from observations. Treat like prompts (GDPR/HIPAA).

Anthropic Enterprise: audit logs, SCIM, Compliance API, HIPAA-ready offering, IP allowlist — **seat + usage** pricing, not a substitute for your trajectory store.

---

## 5. Production Failure Modes

### 5.1 Infinite loops

**Symptoms:** `GraphRecursionError` at 25; `MaxTurnsExceeded` at 10; identical `next` in consecutive checkpoints; 429 storm; linear cost.

**Causes:** router never returns `END`; mapping key mismatch (`"done"` vs `"end"`); ReAct repetitive TAO (paper 47% of failures); worker `allow_delegation=True` both ways; pagination-by-LLM; tool error returned as empty string so the model retries.

**Mitigations:** hard fuse **and** soft `max_iterations` in state; hash last K (thought, action, args) and break on repeat; adapter-level circuit on duplicate calls; `RemainingSteps`; never `max_turns=None` in prod.

### 5.2 State drift

**Symptoms:** user B sees user A history; parallel workers clobber `session.state['result']`; subgraph amnesia; ADK `temp:` expected but new invocation; reducer not associative → DeltaChannel replay ≠ live state.

**Causes:** shared `thread_id`; missing reducer; mutating `Session.state` off the event path; schema evolution without migration; Continue-As-New dropping fields.

**Mitigations:** typed state + tests that two parallel `Send`s merge; ADK distinct keys under ParallelAgent; version the state schema in checkpoint metadata; Store for cross-thread facts, not stuffed into the graph blob.

### 5.3 Lost checkpoints

**Symptoms:** HITL never resumes; pod restart restarts from turn 0 and **re-bills** tools; `exit` durability + OOM.

**Causes:** MemorySaver in prod; Sqlite under multi-worker; Postgres connection timeout (unpooled); durability `async` window vs kill -9; Temporal history overflow **terminates** the workflow.

**Mitigations:** PostgresSaver + pool; Temporal Activities for LLM/tools; Inngest `step.run` around LangGraph; Continue-As-New before 10k events; blob offload; `sync` durability for payment tools.

### 5.4 Plan hallucination

**Symptoms:** orchestrator emits 40 useless workers; plan-and-execute walks steps that contradict new observations; HuggingGPT selects wrong HF model; PS+ still has **27%** semantic-error share on GSM8K misses (plan does not fix understanding).

**Causes:** plan frozen while world changed; no joiner/replanner (LLMCompiler’s Joiner exists for this); XML/JSON parse fail (Anthropic cookbook failure mode); ReAct search-result error 23% derails later thoughts.

**Mitigations:** **dynamic replanning** every K steps or on tool error; structured plan schema with max N subtasks and a cost cap; evaluator-optimizer with a **grounded** stop (tests, not another LLM vibe); ReAct→CoT-SC backoff when search is empty; require citations / tool ids in the plan.

Reflexion helps **across trials** (coding with unit tests). It does **not** stop a single bad plan from spending N workers **this** request unless you gate on the evaluator before fan-out.

### 5.5 Cascading timeouts

**Symptoms:** gateway p99 explosion; Temporal `start_to_close` 60s vs 120s LLM; nested retries; streaming already flushed partial tokens then failover duplicates speech.

**Causes:** stacked retries; breaker tripping on 429; parallel `waitForEvent` losers holding runs; tool timeout < LLM timeout < HTTP gateway; fan-out without per-child deadline.

**Mitigations:** one retry layer; child deadline < parent; hedge only on **idempotent** GETs; streaming failover policy explicit (buffer vs commit); Inngest tight wait timeouts; LangGraph pending writes so a timed-out sibling does not redo the whole super-step.

### 5.6 Failure-mode × layer matrix

| Failure | ReAct loop | LangGraph | Temporal/Inngest | MCP/tools |
| --- | --- | --- | --- | --- |
| Infinite loop | Repeat TAO | `recursion_limit` | Workflow loop without Continue-As-New | Duplicate `tools/call` |
| State drift | Context overflow | Missing reducer | History vs blob split | Session vs token identity |
| Lost checkpoint | Process death | MemorySaver / `exit` | History 50 MB kill | MCP session hijack |
| Plan hallucination | Bad thought | Supervisor node | Activity input is the bad plan | Wrong tool selected |
| Timeout cascade | N sequential tools | Super-step wait | Activity retry × children | Downstream 504 |

---

## 6. Enterprise System Design Scenarios

### 6.1 Decision matrix (architect interview)

| Requirement | Prefer | Avoid |
| --- | --- | --- |
| Fixed 4-step pipeline, SLO < 3s | Prompt chain / ADK Sequential / Prefect | Open ReAct with 10 turns |
| Unknown subtasks (multi-file coding, research) | Orchestrator-workers + cap N + HITL on apply | Unbounded ReAct |
| Chat + tools, <10 hops | Agents SDK or LangGraph ReAct, `max_turns=8`, cache prefix | ToT/LATS in the hot path |
| Approval that may take days | Temporal Signal / Inngest `waitForEvent` | Holding a request worker |
| Multi-vendor agents | A2A tasks + MCP tools | Shared DB as “protocol” |
| Strict audit / HIPAA | Customer-managed checkpointer + redacted traces + no-passthrough MCP | Default LangSmith full payloads |
| 10k concurrent sessions | AgentCore Runtime / Cloud Run + Postgres checkpoints + token buckets | SqliteSaver, in-memory ADK sessions |

Google: single-agent ADK + Cloud Run + MCP first; multi-agent when specialization/policy isolation pays for extra hops ([multi-agent ref](https://docs.cloud.google.com/architecture/multiagent-ai-system); [components](https://docs.cloud.google.com/architecture/choose-agentic-ai-architecture-components)). Anthropic: add agents only when evals show workflows fail.

### 6.2 Reference topologies

**A. Customer support (Anthropic appendix + Inngest HITL).** Router (Haiku) → policy DAG (refund rules as code) → ReAct specialist (CRM MCP, max 6 turns) → interrupt if refund > $X → Inngest wait 24h. Success metric: resolution rate, not tokens. Usage-based pricing in Anthropic’s customer examples is **outcome** pricing — your infra still pays tokens.

**B. Coding agent (SWE-bench-class).** Anthropic: ACI (absolute paths, diffs the model can write) mattered more than the outer prompt. Inner loop: ReAct + tests (evaluator-optimizer). Outer: Temporal so a 40-minute job survives deploys. Plan-and-execute for file list; **replan** after test fail. Reflexion-style memory **across** attempts in Store, not in the 128k window forever.

**C. Enterprise swivel-chair (Google).** Orchestrator ADK on Cloud Run; one MCP server per backend (anti-corruption); Sessions in Gemini Enterprise or GCS; IAM on the agent API ([disparate systems](https://docs.cloud.google.com/architecture/agenticai-orchestrate-access-disparate-systems)).

**D. AWS 2026.** New work: **AgentCore Runtime** hosting **your** LangGraph/CrewAI graph, Gateway for MCP, Memory (incl. **branching** for parallel specialists), Identity. Classic Bedrock Agents MAC is maintenance/closed to new customers — don’t design greenfield on Classic.

### 6.3 Scale & capacity **[inferred]**

LangGraph public names (Klarna, Uber, J.P. Morgan) are **testimonials**, not published QPS ([LangGraph product](https://www.langchain.com/langgraph)). Size from first principles:

- **Checkpoint IOPS:** 1k concurrent ReAct agents × 2 supersteps/turn × 4 turns/min ≈ **8k writes/min**. PostgresSaver is fine; add TTL.
- **TPM:** 1k agents × 8k prefix/turn × 4 turns/min = **32M TPM** if uncached — **over T5 sol 40M** with no headroom. **Cache is a capacity feature**: 90% hit → ~3.2M uncached + 28.8M cached, still counts toward OpenAI TPM but **cost** drops ~10× on input.
- **Temporal:** 1k entity workflows idle on HITL ≈ cheap (no worker CPU); history growth is the constraint, not RAM.
- **Fan-out cap:** orchestrator `max_workers=8`; each worker 2k tokens; join 1 call. Hard-code; do not let the LLM pick 200.

### 6.4 Trade-off matrix (control vs cost vs durability)

| Pattern | Latency | $ / task | Durability | Debuggability |
| --- | --- | --- | --- | --- |
| Single LLM | Best | Best | None | Traces of one span |
| ReAct (10 turns) | Linear in tools | Linear–quadratic tokens | App-managed | Trajectory inspectable (ReAct’s original claim) |
| Plan-and-execute | Plan + exec | Fewer planner tokens if ReWOO-like | Same | Plan artifact auditable |
| Orchestrator-workers | ~max(workers) | 2× supervisor + N workers | Same | N traces to join |
| ToT / LATS | Worst | Worst | Research | Tree logs |
| LangGraph + Postgres | +5–15 ms/step ⚠️ field | Checkpoint $ negligible vs tokens | Super-step | Time travel |
| Temporal + Agents SDK | Activity overhead | **Saves** re-billed tokens on crash | Event-sourced | Temporal UI |
| Inngest wrap | Step overhead | Same save-on-retry | Step memo | Step traces |

### 6.5 Interview sound-bites (facts, not slogans)

1. ReAct trades **hallucination (0% vs CoT 56% on labeled HotpotQA failures)** for **reasoning errors and loops (47%)** — production must add a fuse the paper did not.
2. A ReAct loop is a **cyclic graph**; Prefect/Airflow DAGs cannot express retry-until without an outer scheduler.
3. `max_turns=10` and `recursion_limit=25` are **different units**; converting requires knowing nodes per tool cycle.
4. Checkpointer ≠ memory Store ≠ Temporal history ≠ MCP session.
5. Honor 429; break on 5xx; retry once.
6. MCP OAuth binds **audience**; A2A binds **tasks**; mixing them is how you get confused deputies.
7. Extra ReAct turns are the dominant cost knob; cache-break the prefix and a 10-turn terra job goes from **[inferred] ~$87/1k to ~$220/1k**.
8. Dynamic replanning is the difference between HuggingGPT/LLMCompiler and a stale plan-and-execute that walks off a cliff.

---

## Sources

1. https://arxiv.org/abs/2210.03629
2. https://arxiv.org/html/2210.03629v3
3. https://aclanthology.org/2023.acl-long.147
4. https://papers.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf
5. https://arxiv.org/abs/2303.11366
6. https://arxiv.org/abs/2305.10601
7. https://arxiv.org/abs/2305.18323
8. https://arxiv.org/abs/2310.04406
9. https://arxiv.org/abs/2312.04511
10. https://arxiv.org/abs/2303.17580
11. https://www.anthropic.com/engineering/building-effective-agents
12. https://www.anthropic.com/pricing
13. https://github.com/anthropics/claude-cookbooks/blob/001e5ca1e735563cdaf9ee5c06019a6f608fd403/patterns/agents/orchestrator_workers.ipynb
14. https://openai.github.io/openai-agents-python/running_agents/
15. https://openai.github.io/openai-agents-python/handoffs/
16. https://openai.github.io/openai-agents-python/ref/run/
17. https://openai.github.io/openai-agents-js/guides/running-agents/
18. https://developers.openai.com/api/docs/guides/agents/orchestration
19. https://developers.openai.com/api/docs/pricing
20. https://openai.com/api/pricing/
21. https://developers.openai.com/api/docs/guides/rate-limits
22. https://developers.openai.com/api/docs/guides/prompt-caching
23. https://docs.langchain.com/oss/python/langgraph/overview
24. https://docs.langchain.com/oss/python/langgraph/checkpointers
25. https://docs.langchain.com/oss/python/langgraph/persistence
26. https://docs.langchain.com/oss/python/langgraph/graph-api.md
27. https://docs.langchain.com/oss/python/langgraph/use-graph-api
28. https://docs.langchain.com/oss/python/langgraph/interrupts
29. https://docs.langchain.com/oss/python/langgraph/streaming
30. https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT
31. https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel
32. https://reference.langchain.com/python/langgraph/pregel/main/Pregel/ainvoke
33. https://github.com/langchain-ai/langgraph/blob/2e5025ec1ac8d435840ed4a972097de87aaa2eab/libs/langgraph/langgraph/types.py
34. https://www.langchain.com/langgraph
35. https://www.langchain.com/blog/planning-agents
36. https://docs.langchain.com/langsmith/mask-inputs-outputs
37. https://docs.langchain.com/langsmith/redact-secrets
38. https://docs.langchain.com/langsmith/otel-gateway-trace-redaction
39. https://support.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl
40. https://adk.dev/workflows/patterns/
41. https://adk.dev/sessions/state/
42. https://adk.dev/agents/custom-agents/
43. https://github.com/google/adk-docs
44. https://developers.googleblog.com/announcing-adk-go-20/
45. https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/
46. https://docs.cloud.google.com/architecture/agentic-ai-overview
47. https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system
48. https://docs.cloud.google.com/architecture/choose-agentic-ai-architecture-components
49. https://docs.cloud.google.com/architecture/multiagent-ai-system
50. https://docs.cloud.google.com/architecture/single-agent-ai-system-adk-cloud-run
51. https://docs.cloud.google.com/architecture/agenticai-orchestrate-access-disparate-systems
52. https://a2a-protocol.org/latest/
53. https://a2a-protocol.org/v1.0.0/specification/
54. https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/
55. https://github.com/a2aproject/A2A
56. https://docs.crewai.com/edge/en/learn/hierarchical-process
57. https://docs.crewai.com/en/concepts/production-architecture
58. https://github.com/crewaiinc/crewai/
59. https://temporal.io/blog/announcing-openai-agents-sdk-integration
60. https://docs.temporal.io/develop/typescript/integrations/openai-agents
61. https://docs.temporal.io/workflow-execution/continue-as-new
62. https://docs.temporal.io/workflow-execution/event
63. https://temporal.io/blog/very-long-running-workflows
64. https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents
65. https://www.inngest.com/docs/features/inngest-functions/steps-workflows/wait-for-event
66. https://agentkit.inngest.com/advanced-patterns/human-in-the-loop
67. https://docs.prefect.io/v3/how-to-guides/workflows/run-work-concurrently
68. https://docs.prefect.io/v3/examples/ai-data-analyst-with-pydantic-ai
69. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
70. https://aws.amazon.com/bedrock/agentcore/
71. https://docs.aws.amazon.com/bedrock/latest/userguide/agents-multi-agent-collaboration.html
72. https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
73. https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/
74. https://www.truefoundry.com/blog/llm-failover-load-balancing-provider-outages
75. https://backendbytes.com/articles/llm-provider-outage-resilience/
76. https://ranjankumar.in/fault-isolation-circuit-breaking-llm-agent-pipelines
77. https://www.truefoundry.com/blog/rate-limiting-ai-agents-preventing-llm-api-exhaustion
78. https://aicost.tools/blog/prompt-caching-llm-cost-math/
79. https://tianpan.co/blog/2026-05-09-oauth-mcp-threading-user-identity-tool-servers
80. https://www.scriptbyai.com/rate-limits-openai-api/
81. https://altersquare.io/langgraph-state-management-undocumented-issues-after-commit/
