# Research: Agent Frameworks

**Date researched**: 2026-08-21
**Sources consulted**: 80

Scope: LangGraph (StateGraph, checkpointers, Store, Send, interrupts, LangSmith Deployment / formerly LangGraph Platform), OpenAI Agents SDK (agents, handoffs, guardrails, tracing, sessions, hosted tools), Google ADK (agents, tools, sessions, A2A, Vertex / Gemini Enterprise Agent Platform / Agent Engine), CrewAI (Crews, Flows, agents, tasks, memory, AMP). Prices and limits below are from vendor docs, GitHub licenses, or named protocol specs as of 2026-08-21. ⚠️ No unpublished p50/p95/p99 agent-loop SLOs are invented; missing percentiles are marked. `$ per 1k executions` figures are **[inferred]** from published token rates × a stated reference loop, plus published platform SKUs where they exist — not vendor “per execution” SKUs (none of the four OSS runtimes sell a single SKU of that form).

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane (all four)

Invariant: **the model never executes tools, handoffs, or graph edges**. It emits structured actions; the runtime dispatches; observations are injected; the loop continues.

| Layer | Owns | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
| --- | --- | --- | --- | --- | --- |
| **Control** | Loop budget, routing, checkpoint/session key, RBAC, stream mux | `StateGraph` compiler + Agent Server APIs | `Runner` loop (`max_turns` default **10**, `None` disables) | `Runner` + workflow/graph agents | Flow event graph + Crew `Process` |
| **Data** | Tool HTTP, MCP `tools/call`, A2A tasks, sandboxes | nodes / `ToolNode` / MCP adapters | function tools, hosted tools, MCP, sandbox | ADK tools, `McpToolset`, Vertex tools | `@tool`, MCP DSL, A2A client/server |
| **Persistence control** | Resume identity | `thread_id` (+ `checkpoint_id`) | `session_id` / `RunState` / `conversation_id` | `session_id` + `user_id` + `app_name` | Flow `state.id` / checkpoint id |
| **Managed platform** | Hosted control + data | LangSmith control plane + Agent Server data plane | None as a product; you host the SDK; OpenAI hosts Responses/MCP/sandbox | Agent Runtime + Sessions + Memory Bank | CrewAI AMP (SaaS) / Factory (self-hosted) |

LangSmith Deployment (renamed from LangGraph Platform, Oct 2025): the **control plane never connects to the data plane**. A listener polls control-plane APIs; Agent Servers + PostgreSQL + Redis + a task queue form the data plane. Cloud: LangChain hosts both on AWS/GCP. Hybrid: SaaS control, your VPC data. Self-hosted: both in your cluster (Enterprise). Standalone Agent Server: Docker/K8s, no control plane.

Gemini Enterprise Agent Platform: **Agent Runtime** (serverless deploy of ADK/LangGraph/LangChain/LlamaIndex/A2A/AG2 agents), **Sessions**, **Memory Bank**, **Code Execution**, **Example Store**, **Evaluation**, **Agent Gateway**. ADK itself is OSS and can run on Cloud Run / GKE / laptop.

CrewAI AMP: GitHub- or Studio-deployed crews/flows behind REST; Factory is the self-hosted twin. OSS CrewAI is a library you embed.

OpenAI Agents SDK: **library + OpenAI platform**. Hosted tools (`WebSearchTool`, `FileSearchTool`, `CodeInterpreterTool`, `HostedMCPTool`, `ImageGenerationTool`) execute on OpenAI’s Responses path. Local tools execute in *your* process. There is no LangSmith-equivalent “Agent Server” SKU from OpenAI for the SDK itself.

### 1.2 LangGraph — graph orchestration, typed state, Send, interrupts

**Unit of composition:** `StateGraph[State]` (or the Functional API `entrypoint`). `State` is a user TypedDict / dataclass / Pydantic model. Channels use **reducers** (`Annotated[list, operator.add]`) so parallel writes merge instead of last-write-wins.

**Compile:** `.compile(checkpointer=..., store=..., interrupt_before=..., interrupt_after=...)`. Compile checks orphaned nodes. Checkpoints are written at **super-step** boundaries, **not** mid-function. If a node is retried or resumed after `interrupt()`, **the whole node function restarts**. Side effects before the pause re-run unless you wrap them in Functional API `task`s (results restored from the checkpointer). Changing `task` / `interrupt` order before the resume point can mismatch cached values.

**Send:** conditional edges may return `list[Send(node_name, arg_state)]` for dynamic fan-out (map-reduce). Worker count is data-dependent. Fan-in uses reducers on shared channels. `Command` combines a state update with a hop (`goto`) so a node can both write and redirect.

**Streaming:** two APIs. Stream-mode (`updates`, `values`, `messages`, `custom`, `checkpoints`, `tasks`, `debug`; combinable). Event streaming v3 (LangGraph ≥1.2): typed projections (`messages`, `values`, subgraphs, output) as independent iterators. `interrupt()` payloads appear on `stream.interrupts` (`stream_events(..., version="v3")`) or `__interrupt__` on `invoke()`. Token streaming is `stream_mode="messages"` → `(chunk, metadata)`.

**HITL:** `interrupt(value)` raises a resumable `GraphInterrupt`; client resumes with `Command(resume=...)`. Requires a checkpointer + `thread_id`. Static breakpoints: `interrupt_before` / `interrupt_after` at compile. Graph waits **indefinitely** until resume (process-held if you self-host the invoke; durable wait if Agent Server / Temporal).

**Tools:** nodes call tools; `langgraph.prebuilt` ReAct (`create_react_agent`) + `ToolNode` is the stock agent. MCP via `langchain-mcp-adapters` (`MultiServerMCPClient`, Streamable HTTP / SSE / stdio). Agent Server exposes graphs as MCP tools at `/mcp` (Streamable HTTP, `langgraph-api>=0.2.3`). `/mcp` is **stateless per request** — conversational memory must live in the graph’s checkpointer/store, not the MCP session.

**A2A:** not native to the OSS graph runtime. Expose via Agent Server MCP, or wrap an executor in the Linux Foundation A2A protocol (AgentCard + AgentExecutor). Vertex Agent Runtime can host a LangGraph agent *and* an A2A agent as separate deploy types.

**Licensing:** `langgraph` MIT (PyPI/GitHub). LangSmith / Deployment is commercial (Developer $0, Plus $39/seat, Enterprise custom).

### 1.3 OpenAI Agents SDK — role loop, handoffs, guardrails

**Unit of composition:** `Agent(name, instructions, tools, handoffs, input_guardrails, output_guardrails, output_type)`. `Runner.run` / `run_sync` / `run_streamed` owns the ReAct-like loop until final output, tripwire, interruption, or `MaxTurnsExceeded`.

**Two multi-agent patterns (official):**

| Pattern | Ownership of the user-facing reply | Mechanism |
| --- | --- | --- |
| **Handoffs** | Specialist takes over | `handoffs=[billing, handoff(refund)]`; control moves; `AgentUpdatedStreamEvent` |
| **Agents as tools** | Manager keeps the reply | `specialist.as_tool(...)`; nested run, bounded |

Handoffs can filter history / attach metadata (language-specific APIs). Official guidance: split only when instructions, tools, or **policy** actually change — extra agents multiply prompts, traces, and approval surfaces.

**Typed state:** not a graph state object. Structured **outputs** via `output_type` (Pydantic). App context is `RunContextWrapper[TContext]`. Resumable control state is `RunState` (`result.to_state()`), serializable for HITL. Sessions store **conversation items**, not a user-defined reducer graph.

**Tools (five categories):** (1) Hosted OpenAI tools on Responses; (2) local `ComputerTool` / `ApplyPatchTool` / `ShellTool`; (3) `@function_tool` / `@tool` with schema + Pydantic; (4) agents-as-tools; (5) experimental Codex tool. **Hosted tool search** (`ToolSearchTool` + `defer_loading=True` / `tool_namespace`) loads a subset of a large tool surface at runtime (Responses models; `openai>=2.25.0`). Official rule of thumb: **<10 functions per namespace**. **Programmatic Tool Calling** runs model-generated JS in a hosted V8 with **no** Node/fs/net — only allowlisted tools. **Hosted MCP:** `HostedMCPTool` — OpenAI’s Responses API lists and calls a public MCP server; your Python process is not in the round-trip. Local MCP: `MCPServerStdio` / `Sse` / `StreamableHttp`.

**Guardrails:** input (first agent only), output (last agent only), tool (every `@function_tool` invocation). Input `run_in_parallel=True` (default) = better latency, possible wasted tokens if tripwire fires late; `False` = block until check completes. Tool guardrails **do not** wrap hosted tools, handoffs, `Agent.as_tool()`, Shell/Computer. Tripwires: `InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered` / tool variants. Output tripwire **sanitizes** rejected final/tool payloads in session (`"Output withheld by an output guardrail."`).

**HITL:** `needs_approval` on function tools, `Agent.as_tool`, Shell, ApplyPatch; MCP `require_approval`; HostedMCP `tool_config={"require_approval": "always"|"never"}` + optional `on_approval_request`. Pause → `result.interruptions` → `state.approve()` / `reject()` → `Runner.run(agent, state, session=same)`. Hosted MCP sticky approve key = `(server_label, tool_name)`.

**Streaming:** `Runner.run_streamed` → `stream_events()` until the iterator **ends** (session persist / compaction may finish after last token). Events: raw `ResponsesStreamEvent`, `RunItemStreamEvent` (`tool_called`, `handoff_occured` [sic], `mcp_approval_requested`, …), `AgentUpdatedStreamEvent`. Cancel: immediate or `cancel("after_turn")`.

**Tracing:** on by default; spans: `trace` → `task_span` → `turn_span` → `agent_span` / `generation_span` / `function_span` / `guardrail_span` / `handoff`. `BatchTraceProcessor` → OpenAI backend. Long-lived workers: traces buffer until flush — call `flush_traces` on Celery/RQ shutdown. `trace_include_sensitive_data` gates I/O in traces. Custom processors via `add_trace_processor` / `set_trace_processors`.

**Licensing:** MIT (`openai/openai-agents-python`). Model, tracing, hosted-tool, and Conversations API usage billed by OpenAI.

### 1.4 Google ADK — LlmAgent + workflow templates + graphs (2.0)

**Unit of composition:** `LlmAgent` / `Agent` (model + instruction + tools). Multi-agent: parent/sub-agent trees, **template workflow agents**, and (ADK 2.0, Python/Go) **graph workflows**. Languages: Python, TypeScript, Go, Java, Kotlin.

**Workflow agents (deterministic control, not LLM-routed):**

| Class | Semantics | Stop condition |
| --- | --- | --- |
| `SequentialAgent` | Sub-agents in list order; shared `InvocationContext` / session state | End of list |
| `ParallelAgent` | Concurrent independent sub-agents | All complete |
| `LoopAgent` | Repeat sub-agents | **You must** set `max_iterations` and/or a sub-agent `exit_loop` / STOP signal — the LoopAgent does **not** infer “good enough” |

ADK 2.0 docs: template workflows are **superseded** for new work by graph-based workflows (explicit edges, HITL nodes). Templates remain supported.

**State:** session `state` dict with key templating (`{my_key?}`). Tools write state. Output keys pass SequentialAgent stages. Context assembly is a first-class ADK claim: filter events, summarize old turns, lazy-load artifacts, track tokens — not “concatenate until overflow.”

**Sessions:** `InMemorySessionService` locally; `VertexAiSessionService(project, location, agent_engine_id)` against Agent Platform. Create/get/list; optional `ttl` **or** `expire_time` (not both). Docs: default TTL **365 days** if unspecified. Events (user, model, function call/response) are the billed/stored unit on the older per-event SKU; current Agent Platform meters **storage GiB** + read/write ops (see §2).

**HITL (two mechanisms):** (1) Graph `RequestInput` / Go `ResumeOrRequestInput` → `ErrNodeInterrupted`, persist, resume; `RerunOnResume: true` re-runs the node and returns the human payload. (2) Tool confirmation: `RequireConfirmation: true` or `ctx.RequestConfirmation` → `adk_request_confirmation`. Dec 2025 Agent Builder blog: pause **anywhere** including complex workflows; **rewind** invalidates later turns (polluted-context recovery).

**Tools:** function tools, Google Search, Vertex/Google ecosystem, OpenAPI, `McpToolset` (stdio / SSE; `tool_filter`; `getstate`/`setstate` for Cloud Run/GKE — **active MCP sockets are not restored**, reconnect on demand). Cloud **API Registry** + ADK `ApiRegistry` for org-curated MCP/Apigee tools (Dec 2025). Plugins + callbacks: `before_tool_callback` / `after_tool_callback` / `on_tool_error_callback` / model/agent analogues. Plugins on the Runner apply **across** agents and run **before** object-level callbacks.

**A2A (native gravity well):** AgentCard (capabilities, skills, URL, `/.well-known/agent-card.json`), AgentExecutor (`execute` / `cancel` + EventQueue), `RemoteA2aAgent(agent_card=URL)`, `to_a2a(root_agent)`, `adk api_server --a2a`. Agent Runtime deploys A2A agents; IAM / GoogleCloudAuth on `httpx`. A2A is **agent-to-agent**; MCP is **agent-to-tool**. Linux Foundation, Apache-2.0. Complementary A2UI (early): LLM-generated UI widgets over A2A without shipping executable UI code.

**Eval / observability:** ADK evaluation tooling + Agent Platform Evaluation / Example Store / Feedback service. Production: Cloud Trace, Cloud Logging, built-in + custom metrics. Bidirectional streaming on Agent Runtime.

**Licensing:** Apache-2.0 (`google/adk-python`). Agent Platform is Google Cloud billed.

### 1.5 CrewAI — role crews inside event Flows

**Official production shape:** **start with a Flow**; invoke a **Crew** only when a step needs autonomous multi-role work.

| Primitive | Job | State |
| --- | --- | --- |
| **Flow** | Event-driven backbone: `@start`, listeners, conditionals, loops, `@persist`, `@human_feedback` | Typed Flow state; `usage_metrics` aggregates every LLM call in the run |
| **Crew** | Team of Agents + Tasks | `Process.sequential` (default) or `Process.hierarchical` (`manager_llm` required) |
| **Agent** | `role`, `goal`, `backstory`, tools, LLM, optional private memory | — |
| **Task** | Description, agent, context deps, `output_pydantic` / `output_json`, human input flag | — |

Hierarchical process: a manager (auto or explicit) delegates and validates. Planning flag optional.

**Memory (unified, replaces old short/long/entity/external split):** one `Memory` class. LLM infers scope/categories/importance on save; recall blends semantic + recency + importance. `memory=True` on Crew → default `Memory()`, default embedder **OpenAI `text-embedding-3-large`** unless overridden. After each task: extract facts; before each task: inject recall. Flows: `self.remember` / `recall` / `extract_memories`. Persistence: LanceDB on disk for default Flow memory (survives process restarts on the same volume).

**MCP:** agent `mcps=[url, "catalog#tool", MCPServerStdio/HTTP/SSE]` or `MCPServerAdapter`. Transports: stdio, Streamable HTTP, SSE. Tool filters + `cache_tools_list`. AMP: export workflow **as MCP server**.

**A2A:** first-class (`pip install 'crewai[a2a]'`). `A2AClientConfig` (remote) / `A2AServerConfig` (expose). Auth: Bearer, OAuth2, API key, HTTP. `A2AConfig` deprecated → removed in v2.0.0.

**HITL:** Flow `@human_feedback` (local/console, 1.8.0+); webhook HITL on crew kickoff (`human_input` task + Bearer webhook); AMP: in-platform review, assignment, escalation, SLA.

**Streaming / API:** AMP streams execution events; OSS `kickoff_async` for long runs. Checkpoint TUI: `crewai checkpoint`.

**Licensing:** OSS MIT. AMP Basic **Free** (50 workflow executions/month). Enterprise **custom** (SSO, RBAC, PII redaction, VPC, listed FedRAMP High / SAM on marketing comparison — treat certifications as **vendor claims**, verify ATO).

### 1.6 Cross-cutting: MCP vs A2A vs framework-native multi-agent

| Mechanism | Problem it solves | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- | --- |
| Native multi-agent | In-process orchestration | Graph nodes / subgraphs / Send | Handoffs + as_tool | Sub-agents, Sequential/Loop/Parallel, graphs | Crew processes + Flow steps |
| **MCP** | Agent → tools/resources | Consume via adapters; **serve** graphs at Agent Server `/mcp` | HostedMCP + local MCP servers | `McpToolset`; can wrap ADK tools as MCP server | `mcps` DSL; AMP export as MCP |
| **A2A** | Agent → opaque remote agent | Wrap yourself / Vertex A2A runtime | Not a first-class SDK primitive (use HTTP/MCP) | First-class RemoteA2aAgent + Runtime | First-class client/server |

A2A explicitly **is not** an ADK/LangGraph/Crew replacement and **is not** MCP. Opaque: no shared memory/tools/weights.

### 1.7 Pick-at-a-glance (mechanics only; economics in §2, scenarios in §6)

- **Typed cyclic graphs, time-travel, map-reduce, durable HITL:** LangGraph.
- **Minimal primitives, OpenAI-hosted tools/MCP/sandbox, traces→evals in one vendor:** Agents SDK.
- **GCP IAM/VPC-SC/CMEK, Memory Bank, A2A mesh, multi-language ADK 2.0 graphs:** ADK + Agent Platform.
- **Role-play teams + deterministic outer Flow, AMP for business+eng, Fortune-500 GTM:** CrewAI.

---

## 2. Token Economics & NFR Metrics

### 2.1 Latency percentiles

⚠️ **None of the four frameworks publish p50/p95/p99 for `invoke` / `Runner.run` / crew kickoff.** Agent Platform documents **quotas** (QPM), not latency SLOs. LangSmith Deployment exposes Agent Server API latency **as a metric you monitor**, not a contractual p99. Temporal + LangGraph: HITL waits are **durable and cheap** (signals/timers), which improves *cost while blocked*, not model p95.

Treat end-to-end latency as: model TTFT + tool RTT × turns + queue wait (Agent Server dedicated workers vs in-process) + sandbox cold start. ⚠️ Do not cite a framework “overhead ms” without your own trace.

### 2.2 Published model prices used below (standard, short context; 2026-08-21)

| Model | Input / 1M | Output / 1M | Source |
| --- | --- | --- | --- |
| OpenAI **gpt-4.1** | $2.00 | $8.00 | OpenAI GPT-4.1 launch (cached input $0.50) |
| OpenAI **gpt-5.6-terra** | $2.00 | $12.00 | OpenAI API pricing table |
| OpenAI **gpt-5.6-luna** | $0.20 | $1.20 | same |
| OpenAI **gpt-5.6-sol** | $5.00 | $30.00 | same (long-context row is higher) |
| Gemini **2.5 Flash** | $0.30 (text) | $2.50 (incl. thinking) | Gemini Developer API pricing |
| Gemini **3.6 Flash** | $1.50 | $7.50 (incl. thinking) | same |
| Gemini **2.5 Pro** | $1.25 (≤200k prompt) | $10.00 (≤200k) | same family table; **$2.50 / $15.00** if prompt >200k |

Regional OpenAI processing: **+10%** uplift for eligible models released on/after 2026-03-05. Batch typically **50%** of standard where offered. Thinking/reasoning tokens bill as **output** on Gemini.

Embeddings (CrewAI default memory): OpenAI `text-embedding-3-large` is commonly listed ~$0.13/1M tokens — **confirm live** on OpenAI pricing; used only as **[inferred]** memory tax below.

### 2.3 Reference loop for `$ / 1k executions` **[inferred]**

**Definition of one execution:** 1 user task, **4 model calls** (route + 2 tool-using turns + synthesize), **no** vision/audio, **no** web-search grounding surcharge. Tokens **per call:** 3,000 input + 800 output (includes growing history; conservative for a 4-turn support agent, light for RAG).

**Per execution token cost** = `4 × (3000 × P_in + 800 × P_out) / 1e6`.

| Model | $ / execution **[inferred]** | $ / 1k executions **[inferred]** |
| --- | --- | --- |
| gpt-4.1 | $0.0496 | **$49.60** |
| gpt-5.6-terra | $0.0624 | **$62.40** |
| gpt-5.6-luna | $0.00624 | **$6.24** |
| Gemini 2.5 Flash | $0.0116 | **$11.60** |
| Gemini 3.6 Flash | $0.0420 | **$42.00** |
| Gemini 2.5 Pro (≤200k) | $0.0470 | **$47.00** |

Framework **token** overhead on the **same** 4-call skeleton:

| Overhead | Extra calls (typical) | Notes |
| --- | --- | --- |
| Agents SDK input guardrail (blocking, cheap model) | +1 | If parallel, you still pay if tripwire is late |
| CrewAI `memory=True` | +1 extract LLM/task + embed write/read | Default embedder is **not free**; 3 tasks → 3 extracts |
| LangGraph | 0 extra LLM | Checkpointer I/O is infra, not tokens |
| ADK context summarization | ⚠️ unmetered in docs | May add hidden model calls; budget as extra Flash calls in traces |
| Hierarchical Crew manager | +N manager LLM | Can **double** calls vs sequential |
| Handoff / as_tool nested agent | + nested max_turns | Worst-case 10×10 if both at default |

**[inferred] CrewAI memory tax** on the reference loop with 2 tasks: ~2 × (500-token extract at luna/Flash-Lite) + ~2k embedding tokens. Order of **$0.001–$0.01 / execution** at Flash-Lite/luna rates — small vs gpt-4.1, material vs luna-only agents. Measure `usage_metrics`.

### 2.4 Platform SKUs (published; not “per execution”)

**LangSmith (langchain.com/pricing, 2026-08-21):**

| Item | Published |
| --- | --- |
| Developer | $0/seat, **5k** base traces/mo, 1 seat |
| Plus | **$39**/seat/mo, **10k** base traces/mo, Deployment + Engine |
| Enterprise | Custom; hybrid/self-host; custom SSO, ABAC, RBAC |
| LCU | **$1.50** (compute/work) |
| LSU | **$1.00** (storage/traces) |
| Base vs extended traces | 14-day vs **400-day** retention; upgrade fee for extended |
| Plus Deployment | **1 free Serverless Small**; more billed on resources |
| Runtime compute | **0.045 LCU / vCPU-hr** → **$0.0675 / vCPU-hr [inferred]** |
| Runtime memory | **0.006 LCU / GiB-hr** → **$0.009 / GiB-hr [inferred]** |
| Database compute / memory | **0.177 LSU / vCPU-hr**, **0.025 LSU / GiB-hr** |
| Sandbox | **0.0384 LCU / vCPU-hr**, **0.0123 LCU / GiB-hr**, **0.000123 LSU / GiB-hr**; per-second; Cloud only |
| Fleet included | Dev **5 LCU**/org/mo; Plus **25 LCU** |
| Engine | ~**5–30 LCU per run** (vendor estimate range, not an SLO); scheduled **every 6 hours** |
| Startup | Up to **$10,000** credits (program terms) |

Older ZenML writeups still quote **$0.001/node** + standby **$0.0007 / $0.0036 per minute**. That is **superseded** by LCU/LSU resource metering on the current pricing page. Do not mix eras in a budget.

**LangSmith Deployment $ / 1k executions [inferred]:** assume 2 vCPU-s compute per execution (⚠️ not published): \(1000 × 2/3600 × 0.045 × 1.50 ≈ \$0.038\). **Tokens dominate.** Database uptime for a Dedicated prod instance is a **fixed** LSU burn while the deployment exists (docs: prod stays live across revisions). Serverless scales to zero — use for internal/background agents.

**Gemini Enterprise Agent Platform (cloud.google.com pricing + Dec 2025 blog):**

Two SKU generations exist. **Cite the date.**

| Era | Runtime vCPU | RAM | Sessions | Memory Bank |
| --- | --- | --- | --- | --- |
| Blog 2025-12-18 (billing start **2026-01-28**) | $0.0864/vCPU-h (was $0.0994 from 2025-12-16) | $0.0090/GiB-h | **$0.25 / 1k stored events** | **$0.25 / 1k stored / mo** + **$0.50 / 1k retrieved** (1k retrievals/mo free on blog table) |
| Agent Platform unified SKUs (fetched 2026-08-21) | First **50 vCPU-h/mo free**, then **$0.085 / vCPU-h** | First **100 GiB-h free**, then **$0.009 / GiB-h** | Storage **$0.30 / GiB-month** (1 GiB-mo free) + reads **1 vCPU-h / 3M ops**, writes **1 vCPU-h / 1M ops** | Same storage/ops meters; revisions count toward storage |

Idle Agent Runtime: **not billed** (blog/docs: bill to nearest second of usage). Code Execution / Computer Use: **same** compute/memory rates (no longer free after 2026-01-28). Agent Gateway: **1 vCPU-h per 15,000** API/auth requests **[published conversion]**.

**Agent Engine $ / 1k executions [inferred]:** 1 vCPU × 2 s × 1000 / 3600 × $0.085 ≈ **$0.047** runtime. Session events: if **3 events/request × 1 request/execution** (Google’s own example shape): 3k events / 1k exec × $0.25/1k events = **$0.75 / 1k [inferred, old SKU]**. On GiB metering, 3k small JSON events are **≪ 1 GiB** — storage $ ≈ 0; ops also ≈ 0 at 1k scale. **Memory Bank retrieval** at $0.50/1k memories × 1 retrieve/exec = **$0.50 / 1k [inferred, old SKU]** — can exceed runtime. Always pull the live SKU; do not freeze 2025-12 blog rates into 2027 budgets without checking.

**Quotas (Agent Platform, docs):** e.g. Query/StreamQuery **90/min** (one table), session writes **100/min**, Memory Bank write **100/min** / read **300/min**; other tables show tighter **10/min** for some memory ops — **environment-specific**. Scale tickets, don’t guess.

**CrewAI AMP:** Basic **50 executions/mo**. Enterprise included/max/additional executions: **checkmark only, no public $**. ⚠️ Cannot publish `$ / 1k` for AMP without a quote. OSS = $0 framework + your model bill.

**OpenAI hosted extras (Agents SDK):** Web search / file search / code interpreter / container minutes are **separate** Responses SKUs (file search per call; containers billed per minute with a minimum). Do not fold them into the 4-call skeleton.

### 2.5 NFR: throughput knobs that *are* documented

| Knob | Value | Effect on $ and latency |
| --- | --- | --- |
| LangGraph `recursion_limit` | Default **1000** supersteps since **v1.0.6** (was lower historically; ignore “25” blogs) | Cap spend; `GraphRecursionError` |
| Agents SDK `max_turns` | Default **10** | Hard stop; `MaxTurnsExceeded` |
| ADK `LoopAgent.max_iterations` | You set; example docs use **5** | Prevents infinite critic loops |
| Crew sequential vs hierarchical | Hierarchical adds manager tokens | Quality vs $ |
| LangGraph Send fan-out | N parallel node invocations | Latency ↓, $ × N if each calls an LLM |
| Agent Server dedicated queue workers | API pods ≠ execute pods | Smooth p99 under load; more always-on $ |
| Prompt cache (gpt-4.1 cached $0.50/1M; Gemini cache rows) | Published discounts | Dominates multi-turn if history is stable |

---

## 3. Distributed Resilience & State

### 3.1 LangGraph

**Checkpointers (thread-scoped):** `put` / `put_writes` / `get_tuple` / list. `thread_id` is the primary key; `checkpoint_id` for time-travel / replay. Replay **re-executes** nodes after the checkpoint (LLM/tools/interrupts re-fire). `PostgresSaver`: `thread_id` **≤255 chars**. `InMemorySaver` / `MemorySaver`: **lost on process restart**. Production: Postgres (Agent Server default) or Sqlite for single-node.

**Stores (cross-thread):** namespace tuple + key + JSON value. `PostgresStore` / Mongo / Redis / Upstash / InMemory. Semantic search if configured with embeddings (`dims`, `fields`). Prefix search truncates at `limit` **silently**. Agent Server + Studio: store provided; enable index in `langgraph.json`.

**Retries / timeouts (`langgraph>=1.2`):** `RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2, max_interval=128, jitter=True)`. Default `retry_on` retries most exceptions **except** ValueError/TypeError/RuntimeError/OSError/…; HTTP retries **5xx only**. `NodeTimeoutError` is retryable. After exhaustion: optional `error_handler` → state update + `Command`. Order: attempt → retry → handler → bubble. **Node restart = non-idempotent tools double-charge** unless tools are idempotent or wrapped in `task`s.

**Temporal plugin (Python, Public Preview):** graph as Workflow; nodes as Activities (timeouts, retries, at-least-once) or in-Workflow (must be deterministic). Checkpoints every node. `interrupt()` → durable wait (**no** compute while waiting). `continue-as-new` for long histories. Streaming via `streaming_topic` + `WorkflowStream`; Activity nodes batch signals (`streaming_batch_interval` default **100ms**). Activity retry **re-runs the whole node**. LangSmith integration ships Python+TS.

**Agent Server:** persistence automatic; task queue; dedicated workers scale independently from API. Graceful shutdown at superstep (see fault-tolerance docs).

### 3.2 OpenAI Agents SDK

**Sessions:** retrieve-before / persist-after each `Runner.run`. Cannot mix a session with `conversation_id` / `previous_response_id` / `auto_previous_response_id` in the same run. Backends: `SQLiteSession`, `AsyncSQLiteSession`, `RedisSession` (optional extra, TTL), `SQLAlchemySession`, `MongoDBSession`, `DaprSession` (30+ stores), `OpenAIConversationsSession`, wrappers `EncryptedSession`, `OpenAIResponsesCompactionSession` (do **not** wrap ConversationsSession). `SessionSettings(limit=N)` truncates **read** window; writes still append. `session_input_callback` custom-merges history vs new input without rewriting old rows.

**HITL durability:** serialize `RunState`; resume with **same** session id/backend. Tracing API keys omitted from serialized state unless `includeTracingApiKey`.

**Retries:** model retries exist in the running-agents guide (unsafe-replay override for ambiguous accept). SDK is **not** Temporal. Process crash without session/RunState = lost in-flight turn. Hosted tools: OpenAI’s infra retries are **opaque** (⚠️).

**Sandbox agents:** isolated workspace, resumable sandbox sessions — durability of **files**, not of the Runner loop.

### 3.3 Google ADK + Agent Platform

**Local:** in-memory sessions vanish on restart. **Production:** Vertex/Agent Platform Sessions (GA per Dec 2025 blog) + Memory Bank (cross-session, topic-based memory; ACL 2025 method). IAM Conditions on sessions and Memory Bank. Default session TTL 365 days if unset.

**Failure recovery (ADK, 2025-12 blog):** restore conversation state after crash; HITL pause inside workflows; rewind/invalidate suffix.

**Retries:** callbacks `on_model_error_callback` / `on_tool_error_callback`; LoopAgent iteration caps. Agent Runtime: serverless, scale, Cloud Trace. ⚠️ No Temporal-equivalent first-party workflow engine; use Cloud Tasks / Workflows / your orchestrator around `Runner`.

**MCP statefulness:** persistent MCP sockets vs Cloud Run scale-out — ADK documents the tension; `McpToolset` manages lifecycle; reconnect after restore.

### 3.4 CrewAI

**Flow `@persist`:** DB-backed Flow state; `kickoff(inputs={"id": ...})` resumes same `flow_uuid`; `restore_from_state_id` **forks** (new `state.id`; cannot combine with `from_checkpoint`).

**Checkpointing (early release, APIs may change):** `CheckpointConfig` on Crew/Flow/Agent; events e.g. `task_completed`, `method_execution_finished`, `llm_call_completed`, `*`. `max_checkpoints` to bound disk. Providers: JSON files or SQLite. Writes are **best-effort** (log + continue). `["*"]` / per-LLM-call checkpoints can **hurt** performance.

**Memory LanceDB:** survives restarts on disk; not a multi-region consensus store. AMP: managed runtime + traces.

**Retries:** not a Temporal; wrap Flow steps / HTTP tools yourself. Hierarchical deadlock: manager waits on workers that wait on manager — see §5.

### 3.5 Comparison (durability)

| Capability | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- |
| Thread checkpoint + time travel | Yes | RunState snapshot, not full graph history | Session events; rewind | Flow persist + checkpoints (early) |
| Cross-thread memory | Store | DIY / Conversations / your DB | Memory Bank | Unified Memory + LanceDB |
| Durable HITL wait (days, $≈0) | Agent Server or Temporal | You park `RunState` | Session + RequestInput | AMP webhooks / persist |
| Distributed workers | Agent Server queue / Temporal | Redis/SQL session + your fleet | Agent Runtime | AMP / your FastAPI |
| At-least-once node semantics | Yes (restart node) | Tool retry policies you write | Tool callbacks | Checkpoint skip completed tasks |

---

## 4. Enterprise Security & Governance

### 4.1 Identity, SSO, RBAC

| | LangGraph OSS | LangSmith | Agents SDK | ADK OSS | Agent Platform | CrewAI OSS | CrewAI AMP |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SSO | DIY | Plus: Google/GitHub; **Enterprise: custom SSO** | Your app | DIY | Google Cloud IAM, OAuth, API keys, agent identity | DIY | **SAML 2.0 + OIDC**; Entra / Okta / Auth0; CLI device grant |
| RBAC | DIY | Org User/Admin (Plus); **custom RBAC + ABAC** (Ent.) | Your app | DIY | IAM + IAM Conditions on Sessions/Memory Bank | DIY | Feature RBAC (Manage/Read/None) + **entity** RBAC on automations, env vars, LLM connections, git repos |
| Agent acting as user | `langgraph_auth_user` after `@auth.authenticate` | Same on Deployment | Pass tokens in context; don’t store secrets in state | ADC / IAM agent identity | IAM agent identity; Agent Gateway | — | Workload identity (Enterprise list) |

LangSmith custom auth: populate `config["configurable"]["langgraph_auth_user"]`; **do not** put secrets in graph state. MCP `/mcp` uses the **same** Agent Server auth. Studio users: `is_studio_user()` special case.

CrewAI Factory (self-host): Entra app roles `factory-admin` vs `member` via JWT.

### 4.2 Network, data, PII, sandbox

**LangSmith:** Cloud US or EU; Hybrid/Self-Hosted keep data plane in VPC. LLM Gateway: PII/secrets redaction, cost/rate limits, fallbacks (Plus+). Sandboxes: ephemeral, TTL, snapshot/fork, auth proxy; **not** on self-hosted. LangSmith **does not train** on your traces (ToS statement on pricing page).

**Agent Platform enterprise matrix (docs):** VPC-SC, CMEK, DRZ at rest, HIPAA: **Yes** for Runtime, evaluation, Sessions, Memory Bank, Code Execution. Example Store: HIPAA yes; **no** VPC-SC/CMEK/DRZ. Access Transparency / Access Approval: Runtime, Sessions, Memory Bank — **not** evaluation/Example Store/Code Execution. Private Service Connect to VPC. Bidirectional streaming documented.

**Tool governance (GCP, 2025-12):** Cloud API Registry in Agent Builder Console; Apigee → MCP; MCP for BigQuery/Maps; ADK `ApiRegistry`.

**OpenAI:** hosted MCP/tools run **outside** your VPC. `trace_include_sensitive_data`, EncryptedSession, output-guardrail redaction. Sandbox / Code Interpreter / Shell: OpenAI or local isolation — data residency is an **OpenAI account/region** problem, not an SDK knob.

**CrewAI AMP Enterprise list:** PII redaction, policies, dedicated VPC, NAT, SAM, FedRAMP High (verify). Guardrails + HITL in both Free and Enterprise checkboxes; SSO/RBAC Enterprise-only on the public comparison.

**MCP Zero-Trust [inferred pattern, not a product name]:** (1) no unauthenticated Streamable HTTP; (2) per-user tokens via auth middleware, not a shared PAT in the graph; (3) `tool_filter` / namespace allowlists; (4) HITL on mutating tools; (5) hosted MCP = trust OpenAI’s egress to that URL; (6) A2A mTLS/OIDC between agent identities, not MCP shared secrets. LangGraph user-scoped MCP: custom auth → headers/`httpx.Auth` on `MultiServerMCPClient`. CrewAI HTTP MCP: `headers={"Authorization": "Bearer ..."}`. ADK: reconnect + filter. Agents SDK: HostedMCP `require_approval` + server_label isolation.

### 4.3 Audit

LangSmith traces (base 14d / extended 400d) + Agent Server access logs. OpenAI Traces dashboard + your processors (OTel). GCP Cloud Audit + Cloud Trace + Feedback service (qualitative next to traces). CrewAI AMP tracing + OpenTelemetry (listed). ⚠️ Map traces to **identity** (LangSmith auth user, GCP IAM, AMP RBAC actor) or they are useless for SOX.

### 4.4 License / procurement

- Build in VPC with no SaaS: LangGraph or ADK or CrewAI **OSS** (MIT / Apache-2.0 / MIT) + your K8s.
- Need vendor control plane: LangSmith Ent. vs Agent Platform vs CrewAI AMP Ent. vs “SDK + you.”
- A2A spec: Apache-2.0 (Linux Foundation).

---

## 5. Production Failure Modes

### 5.1 Graph recursion / cyclic ReAct (LangGraph)

**Symptom:** `GraphRecursionError` when supersteps exceed `recursion_limit` (default **1000** since 1.0.6). Parallel nodes in one superstep count as **one** step; a 30-node linear chain is 30.

**Causes:** conditional edge never returns `END`; tool-loop; Send explosion writing into a cycle.

**Mitigations:** terminate edges; `RemainingSteps` managed value to bail with headroom; do **not** “just set 10,000”; idempotent tools because **node replay** duplicates side effects; Functional API `task` for partial determinism.

**Version skew:** graph code changes while old checkpoints exist — interrupt/`task` order mismatch; subgraph checkpoint namespaces hiding parent updates (use Store for cross-graph facts). Agent Server **revisions** vs in-flight threads: drain or reject old schemas.

### 5.2 Handoff / as_tool loops (Agents SDK)

**Symptom:** ping-pong specialists; `MaxTurnsExceeded` (default 10); nested `as_tool` each with 10 turns → **up to 100** model calls **[inferred worst case]**.

**Causes:** overlapping `handoffDescription`; triage that always transfers; guardrails only on first/last agent so mid-chain specialists unconstrained.

**Mitigations:** narrow descriptions; prefer as_tool when manager must own the answer; tool guardrails on mutating functions; `max_turns` per run; session `limit`; compaction wrapper; disable tracing of secrets.

**Hosted MCP:** approval sticky per `(server_label, tool)` — wrong server with same tool name is **not** pre-approved (good). Hosted tools **bypass** tool guardrail pipeline — policy holes.

**Streaming:** not consuming `stream_events()` to completion → session/approval bookkeeping incomplete.

### 5.3 Workflow / LoopAgent / A2A (ADK)

**Symptom:** LoopAgent infinite if no max_iterations and no `exit_loop`; SequentialAgent quality collapse from bloated shared state; ParallelAgent races on the same state key.

**A2A:** discovery via stale AgentCard URL; auth mismatch (`GoogleCloudAuth` vs open card); executor `cancel` not implemented → zombie tasks on Runtime.

**MCP:** stateful session affinity vs serverless; restored agent without live MCP socket.

**Sessions billing:** verbose events (every function call/response) inflate the old $0.25/1k-event SKU; rewind helps **context** but historical events may still be stored.

**Quota:** 90 QPM Query/StreamQuery — burst fan-out fails closed.

### 5.4 Crew deadlock and memory blow-up

**Hierarchical deadlock [inferred from process design]:** manager delegates to agent A; A waits for human/webhook; manager blocks; no timeout → AMP execution hung. Sequential crews: task N context includes all prior outputs → token cliff.

**Flow persist fork bugs:** combining `restore_from_state_id` + `from_checkpoint` raises `ValueError`.

**Checkpoint `*`:** disk fill; resume from a mid-LLM checkpoint with **new** prompt = version skew.

**Memory:** default `text-embedding-3-large` + LLM extract on **every** task — cost and prompt injection of stale “facts.” Scope memory per user.

**MCP string URLs with API keys** in `mcps=[...]` → secrets in source.

### 5.5 Shared modes

| Mode | All frameworks |
| --- | --- |
| Non-idempotent tools + retry/resume | Double refund / double email |
| Unbounded context | Quadratic $ (Gemini Live/session window re-bills accumulated tokens per turn — Vertex LiveAPI docs) |
| Eval/trace PII | Training-data and GDPR incidents |
| Multi-framework A2A | Schema/version skew of AgentCard / protocol versions (CrewAI notes protocol version on server) |

---

## 6. Enterprise System Design Scenarios

### 6.1 Capability matrix (architect view)

| Dimension | LangGraph + LangSmith | OpenAI Agents SDK | ADK + Agent Platform | CrewAI + AMP |
| --- | --- | --- | --- | --- |
| Orchestration metaphor | **Typed graph** (cyclic DAG + Send) | **Role loop** + handoff/as_tool | **Agent tree + templates + graphs** | **Roles (Crew) inside events (Flow)** |
| Typed state | Best (reducers, channels) | Outputs typed; graph state DIY | Session dict + graph payloads | Flow state + pydantic tasks |
| Durability | Best OSS checkpoint + Temporal option | Session + RunState; you operate | Managed Sessions/Memory Bank | Flow persist + early checkpoints |
| HITL | interrupt/Command; durable on platform | Approval interruptions | RequestInput + tool confirm | Decorator + webhooks + AMP inbox |
| Streaming | Multi-mode + event v3 | First-class stream events | Runtime bidirectional + ADK events | AMP events; OSS weaker |
| Eval/tracing | LangSmith datasets, online/offline, Engine | OpenAI traces → eval/fine-tune | Example Store, Eval service, Cloud Trace | AMP traces, OTel, “hallucination scores” (vendor) |
| Multi-agent | Subgraphs, multi-actor, Send | Handoff vs manager | Native + A2A | Crew hierarchical/sequential |
| MCP | Consume + **serve** `/mcp` | Hosted + local; richest hosted tools | Consume + serve; API Registry | Consume + AMP export MCP |
| A2A | Indirect | Indirect | **Native** | **Native** |
| Enterprise SSO/RBAC | LangSmith Ent. | Your IdP in front | GCP IAM | AMP Ent. |
| License | MIT + paid platform | MIT + API | Apache-2.0 + GCP | MIT + paid AMP |
| Lock-in | Checkpoints/LangSmith | Responses hosted tools | GCP services | AMP control plane |
| Languages | Python (primary), JS LangGraph | Python + JS/TS | Py/TS/Go/Java/Kotlin | Python |

### 6.2 Scenario A — Regulated HITL claims (days-long wait, audit)

**Need:** pause for adjuster, resume weeks later, no GPU burn, replayable state, SSO.

| Option | Fit |
| --- | --- |
| LangGraph + Temporal or LangSmith Dedicated | **Best mechanics.** Temporal: durable interrupt $≈0. LangSmith Ent. SSO/RBAC. Postgres checkpoints. |
| ADK + Sessions | Strong if already GCP; RequestInput; CMEK/VPC-SC. Confirm TTL vs 7-year retention (365d default is **wrong** for claims — set `expire_time`). |
| Agents SDK | Park `RunState` in your DB; you build the wait fabric. Fine if OpenAI-only tools. |
| CrewAI AMP | Webhook HITL + Enterprise inbox; ⚠️ execution $ unpublished; good if business ops own the queue. |

**Pick:** LangGraph+Temporal **or** ADK on GCP if CMEK is the constraint.

### 6.3 Scenario B — OpenAI-native assistant (web search, vector store, hosted MCP, traces)

**Need:** ship in a week, hosted tools, guardrails, ChatKit later.

**Pick:** Agents SDK. Don’t introduce LangGraph until you need cycles, Send map-reduce, or time-travel. Put Redis/SQLAlchemy sessions in front of multiple workers. Set `max_turns` and blocking input guardrails on the public entry agent. Budget hosted search/file-search SKUs **separately** from §2.3.

### 6.4 Scenario C — Multi-vendor agent mesh (Crew research + ADK policy + LangGraph workflow)

**Need:** opaque specialists, IAM between services.

**Pick:** **A2A** as the contract; MCP only for tools. Host ADK `RemoteA2aAgent` or Crew `A2AClientConfig` against LangGraph wrapped in AgentExecutor (or Vertex A2A runtime). Do not share checkpointers across frameworks. Card URLs + OIDC/mTLS. Version AgentCards.

### 6.5 Scenario D — High-volume inner-loop (classification, 1–2 tools, Flash/luna)

**Need:** minimize $ / 1k.

From §2.3: luna **~$6 / 1k** vs gpt-4.1 **~$50 / 1k** **[inferred]**. Platform adders ~**$0.04–$0.75 / 1k** except Memory Bank retrieval and verbose session-event SKUs.

**Pick:** thinnest runtime that still gives a loop cap: Agents SDK **or** a 3-node LangGraph. Avoid Crew hierarchical and `memory=True` until quality requires it. ADK LoopAgent only with `max_iterations=1..3`.

### 6.6 Scenario E — Enterprise “digital workforce” on GCP

**Need:** API Registry, Memory Bank personalization, Code Execution sandbox, HIPAA, Agent Gateway.

**Pick:** ADK 2.0 graphs + Agent Runtime. LangGraph **can** deploy *on* Agent Runtime (supported deploy type) if you want LangGraph semantics with GCP ops — hybrid that is real, not theoretical.

### 6.7 Scenario F — Business-user Studio + governed deploy

**Need:** visual editor, GitHub, 50 execs to demo, then SSO.

**Pick:** CrewAI AMP Basic → Enterprise. Engineers still wrap production paths in **Flows**. Don’t run unbounded hierarchical Crews as the HTTP handler.

### 6.8 Anti-patterns

1. LangGraph **without** a durable checkpointer in prod + HITL (lost interrupts).
2. Agents SDK handoffs **and** as_tool **and** a third graph framework for one product surface.
3. ADK LoopAgent as “until quality is good” with no max_iterations.
4. Crew `Process.hierarchical` as the only control plane (use Flow).
5. Shared MCP PAT in graph state / crew YAML.
6. Mixing LangSmith **node-era** prices with **LCU** prices in the same model.
7. Assuming Agent Platform **idle** is free **and** Dedicated LangSmith DB uptime is free — opposite shapes.

### 6.9 Decision rule (one paragraph)

Choose **LangGraph** when the product *is* a state machine (cycles, fan-out, time-travel, multi-week HITL). Choose **OpenAI Agents SDK** when the product *is* a tool-using assistant on OpenAI’s hosted surface and you want traces/evals without a graph compiler. Choose **ADK** when the control plane must be Google Cloud (IAM, CMEK, A2A mesh, Memory Bank, registry). Choose **CrewAI** when the unit of work is a **role team** and you want Flow as the outer app plus AMP for ops; still add caps (process, max iterations, persist) before production traffic.

---

## Sources

1. https://docs.langchain.com/oss/python/langgraph/graph-api
2. https://docs.langchain.com/oss/python/langgraph/persistence
3. https://docs.langchain.com/oss/python/langgraph/checkpointers
4. https://docs.langchain.com/oss/python/langgraph/stores
5. https://docs.langchain.com/oss/python/langgraph/interrupts
6. https://docs.langchain.com/oss/python/langgraph/streaming
7. https://docs.langchain.com/oss/python/langgraph/use-graph-api
8. https://docs.langchain.com/oss/python/langgraph/fault-tolerance
9. https://docs.langchain.com/oss/python/langgraph/durable-execution
10. https://reference.langchain.com/python/langgraph/types/interrupt
11. https://docs.langchain.com/oss/python/langchain/mcp
12. https://docs.langchain.com/langsmith/deploy-to-cloud-overview
13. https://docs.langchain.com/langsmith/deployment
14. https://docs.langchain.com/langsmith/control-plane
15. https://docs.langchain.com/langsmith/data-plane
16. https://docs.langchain.com/langsmith/agent-server
17. https://docs.langchain.com/langsmith/components
18. https://docs.langchain.com/langsmith/server-mcp
19. https://docs.langchain.com/langsmith/custom-auth
20. https://docs.langchain.com/langsmith/pricing-faq
21. https://www.langchain.com/pricing
22. https://www.langchain.com/blog/langgraph-platform-ga
23. https://github.com/langchain-ai/langgraph
24. https://pypi.org/project/langgraph/
25. https://docs.temporal.io/develop/python/integrations/langgraph
26. https://temporal.io/blog/temporal-langgraph-plugin-durable-execution
27. https://openai.github.io/openai-agents-python/
28. https://openai.github.io/openai-agents-python/running_agents/
29. https://openai.github.io/openai-agents-python/streaming/
30. https://openai.github.io/openai-agents-python/results/
31. https://openai.github.io/openai-agents-python/sessions/
32. https://openai.github.io/openai-agents-python/tools/
33. https://openai.github.io/openai-agents-python/guardrails/
34. https://openai.github.io/openai-agents-python/human_in_the_loop/
35. https://openai.github.io/openai-agents-python/tracing/
36. https://openai.github.io/openai-agents-python/ref/stream_events/
37. https://openai.github.io/openai-agents-js/guides/human-in-the-loop/
38. https://developers.openai.com/api/docs/guides/agents/orchestration
39. https://developers.openai.com/api/docs/pricing
40. https://openai.com/api/pricing/
41. https://openai.com/index/gpt-4-1/
42. https://github.com/openai/openai-agents-python
43. https://github.com/openai/openai-agents-python/blob/main/docs/mcp.md
44. https://github.com/openai/openai-agents-python/blob/3a11cf52/src/agents/run_config.py
45. https://adk.dev/
46. https://google.github.io/adk-docs/agents/
47. https://adk.dev/tools/mcp-tools/
48. https://adk.dev/graphs/human-input/
49. https://adk.dev/safety/
50. https://adk.dev/callbacks/types-of-callbacks/
51. https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/loop-agents.md
52. https://github.com/google/adk-docs/blob/main/docs/agents/workflow-agents/sequential-agents.md
53. https://github.com/google/adk-python
54. https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/adk
55. https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sessions/manage-with-adk
56. https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview
57. https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/create-an-a2a-agent
58. https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/agent-quotas
59. https://cloud.google.com/products/gemini-enterprise-agent-platform/pricing
60. https://cloud.google.com/blog/products/ai-machine-learning/new-enhanced-tool-governance-in-vertex-ai-agent-builder
61. https://cloud.google.com/blog/products/ai-machine-learning/build-and-manage-multi-system-agents-with-vertex-ai
62. https://ai.google.dev/gemini-api/docs/pricing
63. https://a2a-protocol.org/latest/
64. https://github.com/a2aproject/A2A
65. https://codelabs.developers.google.com/adk-a2a-agent-runtime
66. https://docs.crewai.com/en/introduction
67. https://docs.crewai.com/en/concepts/memory
68. https://docs.crewai.com/en/concepts/production-architecture
69. https://docs.crewai.com/en/learn/hierarchical-process
70. https://docs.crewai.com/en/learn/human-in-the-loop
71. https://docs.crewai.com/en/mcp/overview
72. https://docs.crewai.com/v1.15.1/en/concepts/flows
73. https://docs.crewai.com/v1.14.4/en/concepts/checkpointing
74. https://docs.crewai.com/v1.15.16/en/learn/a2a-agent-delegation
75. https://docs-platform.crewai.com/platform/en/introduction
76. https://docs-platform.crewai.com/platform/en/features/sso
77. https://docs-platform.crewai.com/platform/en/features/rbac
78. https://crewai.com/pricing
79. https://github.com/crewaiinc/crewai
80. https://codelabs.developers.google.com/codelabs/production-ready-ai-with-gc/3-developing-agents/build-a-multi-agent-system-with-adk

---

*End of research. No unpublished latency SLOs. Token `$ / 1k` tables are **[inferred]** from the stated 4×(3k/800) loop and list prices dated 2026-08-21.*
