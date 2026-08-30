# Module 05 — Agent Frameworks

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/05-agent-frameworks.md` (researched 2026-08-21, 80 sources).
**Mandatory topics**: LangGraph · OpenAI Agents SDK · Google ADK · CrewAI.

The unit of production is not “an agent library.” It is a **control plane** that compiles the graph/crew/loop, stamps the resume key (`thread_id` / `session_id` / `user_id`+`app_name` / Flow `state.id`), enforces the fuse (`recursion_limit` / `max_turns` / `max_iterations` / process type), and parks HITL — wrapping a **data plane** that mutates the world (`ToolNode`, hosted Responses tools, `McpToolset`, Crew `@tool` / MCP / A2A). Across all four the invariant is identical: **the model never executes tools, handoffs, or graph edges**. It emits a structured action; the runtime dispatches; an observation is injected; the loop continues. Interview answers that skip this split fail when the follow-up is “who holds the checkpoint key, and why did a node restart double-charge the refund?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, policy, loop fuses, graph/crew compile, stream mux, and the resume identity. Data plane owns LLM I/O (untrusted planner), local tool HTTP, MCP `tools/call`, A2A tasks, and sandboxes (LangSmith Cloud sandbox; OpenAI Code Interpreter / local Shell; Agent Platform Code Execution; Crew tools you host). Persistence is **not** one store: thread/session checkpoints (HITL, crash resume, time-travel), cross-thread memory (LangGraph Store, Memory Bank, Crew unified Memory / LanceDB), and a durable wait fabric (Temporal / Agent Server queue / parked `RunState` / AMP webhook). Telemetry is the only place turn count, traces, and platform LCU/vCPU-h are authoritative.

LangSmith Deployment (renamed from LangGraph Platform, Oct 2025): the **control plane never connects to the data plane**. A listener polls control-plane APIs; Agent Servers + PostgreSQL + Redis + a task queue are the data plane. Cloud: LangChain hosts both. Hybrid: SaaS control, your VPC data. Self-hosted: both in-cluster (Enterprise). Standalone Agent Server: Docker/K8s, no control plane.

Gemini Enterprise Agent Platform: Agent Runtime (serverless ADK / LangGraph / LangChain / LlamaIndex / A2A / AG2), Sessions, Memory Bank, Code Execution, Example Store, Evaluation, Agent Gateway. ADK OSS also runs on Cloud Run / GKE / laptop.

OpenAI Agents SDK is **library + OpenAI platform** — no Agent-Server SKU. Hosted tools execute on Responses; local tools execute in *your* process. CrewAI AMP is SaaS REST in front of crews/flows; Factory is the self-hosted twin; OSS is a library you embed.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE / sync HTTP / Temporal Signal / AMP webhook / A2A task)           │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + tenant
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (compile, fuse, resume key, stream mux, RBAC)                    │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Loop fuse    │─▶│ Compiler / Runner     │  │
│  │ auth,quota │  │ PII redact   │  │ recursion=   │  │ StateGraph.compile    │  │
│  │ RPM/QPM    │  │ tool RBAC    │  │  1000        │  │ Runner.run max_turns  │  │
│  │ breaker    │  │ MCP allowlst │  │ max_turns=10 │  │  =10 (None=off)       │  │
│  │            │  │ HITL gate    │  │ LoopAgent N  │  │ ADK Runner + graphs   │  │
│  └────────────┘  └──────┬───────┘  │ Crew Process │  │ Flow event graph      │  │
│                         │          └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
│                         │                 ▼                     │               │
│                         │          ┌────────────────┐           │               │
│                         │          │ Orchestrator   │◀──────────┘               │
│                         │          │ superstep /    │  interrupt / Command      │
│                         │          │ turn / Flow    │  handoff / as_tool        │
│                         │          │ event          │  RequestInput / @human_fb │
│                         │          └───────┬────────┘                           │
└─────────────────────────┼──────────────────┼────────────────────────────────────┘
                          │                  │
                          │                  ▼
┌─────────────────────────┼───────────────────────────────────────────────────────┐
│ DATA PLANE              │  model = planner only; side effects live here         │
│                         │                                                       │
│  ┌────────────┐  ┌──────┴───────┐  ┌─────────────┐  ┌────────────┐  ┌────────┐  │
│  │ LLM actor  │─▶│ Action parse │─▶│ Tool proxy  │─▶│ MCP        │─▶│Sandbox │  │
│  │ (untrusted)│  │ schema+RBAC  │  │ idempotency │  │ adapters / │  │ LG Cloud│ │
│  │            │  │ hosted vs    │  │ dup circuit │  │ HostedMCP  │  │ CI/Shell│ │
│  └────────────┘  │  local tools │  └──────┬──────┘  │ McpToolset │  │ CodeEx.│ │
│                  └──────────────┘         │         │ Crew mcps  │  └────────┘  │
│  ┌────────────┐                           │         └────────────┘              │
│  │ A2A peer   │◀── AgentCard + execute ───┘  ADK + Crew first-class;            │
│  │ (opaque)   │     / cancel + EventQueue    LangGraph wrap; SDK = HTTP/MCP     │
│  └────────────┘                                                                 │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌─────────────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌─────────────┐ │
│  │ Thread / session    │ │ Cross-thread     │ │ Wait fabric   │ │ Blob / WORM │ │
│  │ LG: thread_id PK    │ │ LG Store         │ │ Temporal      │ │ tool bytes  │ │
│  │  checkpoint_id      │ │ ADK Memory Bank  │ │ Agent Server  │ │ not in hist.│ │
│  │  ≤255 Postgres      │ │ Crew Memory      │ │  queue        │ │             │ │
│  │ SDK: session items  │ │  + LanceDB       │ │ parked RunSt. │ │             │ │
│  │ ADK: session events │ │                  │ │ AMP webhook   │ │             │ │
│  │ Crew: Flow @persist │ │                  │ │               │ │             │ │
│  └─────────────────────┘ └──────────────────┘ └───────────────┘ └─────────────┘ │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐ │
│  │ Audit log   │  │ Metrics      │  │ Trace spans │  │ Usage (authoritative)  │ │
│  │ tenant,user,│  │ turns, fuse  │  │ LangSmith / │  │ LCU/LSU, vCPU-h,       │ │
│  │ agent,thread│  │ QPM, breaker │  │ OpenAI Trc  │  │ session GiB, Memory    │ │
│  │ hashed args,│  │ HITL wait s  │  │ Cloud Trace │  │ Bank retrieve, AMP     │ │
│  │ policy dec. │  │ worker queue │  │ AMP + OTel  │  │  exec count            │ │
│  └─────────────┘  └──────────────┘  └─────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Four runtimes (control vs data vs persist)

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ LangGraph        │ Agents SDK       │ Google ADK       │ CrewAI           │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ StateGraph[S] /  │ Agent + Runner   │ LlmAgent +       │ Flow (outer) +   │
│  Functional API  │  run/run_streamed│  Sequential /    │  Crew (inner)    │
│  entrypoint      │                  │  Parallel / Loop │                  │
│                  │                  │  + ADK 2.0 graph │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Control: compiler│ Control: Runner  │ Control: Runner  │ Control: Flow    │
│  + Agent Server  │  max_turns=10    │  + workflow/graph│  events + Process│
│ Data: nodes /    │ Data: function,  │ Data: ADK tools, │ Data: @tool,     │
│  ToolNode / MCP  │  hosted, MCP,    │  McpToolset,     │  MCP DSL, A2A    │
│  adapters        │  sandbox         │  Vertex tools    │  client/server   │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Resume:          │ Resume:          │ Resume:          │ Resume:          │
│  thread_id +     │  session_id /    │  session_id +    │  Flow state.id / │
│  checkpoint_id   │  RunState /      │  user_id +       │  checkpoint id   │
│                  │  conversation_id │  app_name        │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Platform:        │ Platform: none   │ Platform: Agent  │ Platform: AMP    │
│  LangSmith CP +  │  for the SDK;    │  Runtime + Sess  │  SaaS / Factory  │
│  Agent Server DP │  OpenAI hosts    │  + Memory Bank   │  self-host       │
│                  │  Responses/MCP   │                  │                  │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

### 1.3 End-to-end request flow

1. **Ingress.** Client opens SSE (interactive) or sync HTTP (kickoff) or a Temporal Signal / AMP webhook (HITL resume). Gateway stamps `correlation_id`, authenticates, checks RPM / Agent Platform QPM (Query/StreamQuery documented **90/min** on one table — environment-specific). A closed circuit breaker on the primary model is already a routing input.
2. **Policy.** Control plane redacts PII **before** the first model call. Tool RBAC attaches only the tools this turn may call. MCP gets a per-user token via auth middleware — not a shared PAT in graph state / crew YAML. Mutating tools require HITL (`interrupt()`, `needs_approval`, `RequestInput` / `RequireConfirmation`, `@human_feedback` / `human_input` webhook).
3. **Compile / bind.** LangGraph: `.compile(checkpointer=..., store=..., interrupt_before/after=...)` — compile rejects orphaned nodes. Agents SDK: bind `Agent(tools, handoffs, guardrails, output_type)`. ADK: bind `LlmAgent` plus Sequential/Parallel/Loop or a 2.0 graph. CrewAI: start a **Flow**; invoke a Crew only when a step needs multi-role work.
4. **Loop (control plane).** Until fuse, final output, tripwire, or interrupt: model emits actions → runtime dispatches on the data plane → observations merge into state. LangGraph writes checkpoints at **super-step** boundaries, **not** mid-function. Agents SDK persists session items retrieve-before / persist-after each `Runner.run`. ADK appends session events (user, model, function call/response). Crew Flow `@persist` writes typed state; Crew checkpoints are **best-effort**.
5. **Tool proxy (data plane).** Local tools run in your process (idempotency key required — node/activity **restart re-runs the whole function**). Hosted OpenAI tools (`WebSearchTool`, `FileSearchTool`, `CodeInterpreterTool`, `HostedMCPTool`) run on Responses — **your Python is not in the round-trip**; they **bypass** tool guardrails. ADK `McpToolset`: active MCP sockets are **not** restored after Cloud Run scale-out; reconnect on demand. Agent Server `/mcp` is **stateless per request** — conversational memory lives in the graph checkpointer/store, not the MCP session.
6. **HITL park.** LangGraph `interrupt(value)` raises resumable `GraphInterrupt`; client resumes `Command(resume=...)` on the same `thread_id` (checkpointer required). Graph waits **indefinitely** — process-held if you self-host `invoke`; durable if Agent Server or Temporal. SDK: pause → `result.interruptions` → `state.approve()`/`reject()` → `Runner.run(agent, state, session=same)`. Hosted MCP sticky approve key = `(server_label, tool_name)`. ADK: graph `RequestInput` / Go `ResumeOrRequestInput`; tool `RequireConfirmation` → `adk_request_confirmation`; rewind invalidates later turns. Crew: Flow `@human_feedback`; webhook HITL on `human_input` tasks; AMP inbox + SLA.
7. **Fan-out / handoff.** LangGraph conditional edges may return `list[Send(node, arg)]` (map-reduce; worker count is data-dependent; fan-in via reducers). SDK: **handoff** transfers the user-facing reply; **`as_tool`** keeps the manager’s reply (nested run, bounded). ADK ParallelAgent races if two sub-agents write the same state key. Crew hierarchical: manager delegates and validates (`manager_llm` required).
8. **Stream mux.** LangGraph: stream-mode (`updates`/`values`/`messages`/…) plus event streaming v3 (`langgraph>=1.2`) with `stream.interrupts`. SDK: consume `stream_events()` **to the end** or session/approval bookkeeping is incomplete; cancel immediate or `cancel("after_turn")`. ADK / Agent Runtime: bidirectional streaming. Crew AMP streams execution events; OSS `kickoff_async`.
9. **Persist and emit.** Superstep / session / Flow snapshot. Usage and hashed args land in LangSmith traces (base **14-day** vs extended **400-day**), OpenAI `BatchTraceProcessor` (call `flush_traces` on Celery/RQ shutdown), Cloud Trace / Cloud Audit, or AMP + OTel. Map traces to **identity** (LangSmith `langgraph_auth_user`, GCP IAM, AMP RBAC actor) or they are useless for SOX.

**Interview talking point:** “MCP is agent-to-tool. A2A is agent-to-opaque-agent (no shared memory/tools/weights). Neither replaces the in-process graph/crew/runner. Hosted MCP means you trust OpenAI’s egress to that URL.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Four orchestration metaphors

| Metaphor | Runtime | Unit of composition | Stop condition | Multi-agent |
| --- | --- | --- | --- | --- |
| **Typed cyclic graph** | LangGraph | `StateGraph[State]` (TypedDict / dataclass / Pydantic) + reducers (`Annotated[list, operator.add]`) so parallel writes merge | `END` edge, `GraphRecursionError` at `recursion_limit` (**1000** supersteps since v1.0.6), or `interrupt()` | Nodes, subgraphs, `Send` fan-out, `Command(goto=...)` |
| **Role loop** | OpenAI Agents SDK | `Agent(name, instructions, tools, handoffs, guardrails, output_type)` | Final output, tripwire, interruption, `MaxTurnsExceeded` (default **`max_turns=10`**, `None` disables) | Handoffs (specialist owns reply) vs `as_tool` (manager owns reply) |
| **Agent types + templates + graphs** | Google ADK | `LlmAgent` (model+instruction+tools). Deterministic: `SequentialAgent`, `ParallelAgent`, `LoopAgent`. ADK 2.0: graph workflows **supersede** templates for new work | Sequential: end of list. Parallel: all complete. Loop: **you must** set `max_iterations` and/or `exit_loop` — LoopAgent does **not** infer “good enough.” Docs examples use **5** | Parent/sub-agent trees; `RemoteA2aAgent`; `to_a2a(root_agent)` |
| **Roles inside events** | CrewAI | **Flow** (`@start`, listeners, conditionals, loops, `@persist`, `@human_feedback`) wrapping a **Crew** (Agents + Tasks) only when a step needs autonomous multi-role work | Flow listeners; Crew `Process.sequential` (default) or `Process.hierarchical` | Hierarchical manager; A2A client/server (`crewai[a2a]`) |

**State machines (what is actually snapshotted).**

- LangGraph: user-defined channels + reducers. Checkpoints at superstep. Replay **re-executes** nodes after the checkpoint (LLM/tools/interrupts re-fire). `InMemorySaver` dies on process restart. `PostgresSaver`: `thread_id` **≤255 chars**. Cross-thread: Store (namespace tuple + key + JSON; prefix search truncates at `limit` **silently**). Functional API `task`s restore results so side effects before `interrupt()` do not double-fire; changing `task`/`interrupt` **order** before the resume point mismatches cache.
- Agents SDK: **not** a reducer graph. Structured **outputs** via `output_type` (Pydantic). App context `RunContextWrapper[TContext]`. Resumable control = serializable `RunState`. Sessions store **conversation items**. Cannot mix a session with `conversation_id` / `previous_response_id` / `auto_previous_response_id` in the same run. `SessionSettings(limit=N)` truncates the **read** window; writes still append.
- ADK: session `state` dict with key templating (`{my_key?}`). Context assembly is first-class (filter, summarize, lazy artifacts, token track) — not “concatenate until overflow.” Sessions: `InMemorySessionService` locally; `VertexAiSessionService` in prod. Optional `ttl` **or** `expire_time` (not both). Default TTL **365 days** if unspecified.
- CrewAI: typed Flow state; `usage_metrics` aggregates every LLM call. Unified `Memory` (replaces old short/long/entity split): LLM infers scope/importance on save; recall blends semantic + recency + importance. `memory=True` on Crew → default `Memory()`, default embedder **OpenAI `text-embedding-3-large`**. After each task: extract facts; before each task: inject recall. `restore_from_state_id` **forks** (new `state.id`); cannot combine with `from_checkpoint` (`ValueError`).

**Complexity (one user task, sequential unless noted).**

| Mechanism | Model-call bound | Wall-clock | Failure mode |
| --- | --- | --- | --- |
| LangGraph linear chain of \(k\) nodes | \(\Theta(k)\) supersteps (parallel nodes in one superstep count as **one**) | \(\Theta(k)\) | Missing `END` → burn to 1000 |
| `Send` fan-out \(N\) | \(\Theta(N)\) LLM if each worker calls a model | \(\approx \max_i t_i\) | Cost \(\times N\); cycle + Send explosion |
| SDK loop | \(\le\) `max_turns` | \(\Theta(\mathrm{turns})\) | Nested `as_tool` each at 10 → **up to 100** model calls **[inferred worst case]** |
| ADK Sequential \(k\) | \(\Theta(k)\) | \(\Theta(k)\) | Shared-state bloat |
| ADK Parallel \(k\) | \(\Theta(k)\) tokens | \(\approx \max_i t_i\) | Same-key races |
| ADK Loop | \(\le\) `max_iterations` | \(\Theta(N)\) | Infinite if unset |
| Crew sequential \(T\) tasks | \(\Theta(T)\) + optional memory extract/task | \(\Theta(T)\) | Token cliff: task \(N\) sees all prior outputs |
| Crew hierarchical | **~2×** sequential **[inferred]** (manager LLM per delegate/validate) | Worse than sequential | Manager waits on worker waiting on manager (HITL deadlock) |

Guardrail placement (SDK, production-critical): input guardrails on the **first** agent only; output on the **last** only; tool guardrails on every `@function_tool` — **not** hosted tools, handoffs, `Agent.as_tool()`, Shell/Computer. Input `run_in_parallel=True` (default) = better latency, wasted tokens if tripwire fires late.

Official SDK split rule: extra agents only when instructions, tools, or **policy** actually change — they multiply prompts, traces, and approval surfaces.

Official Crew rule: **start with a Flow**; Crew is the inner autonomous team, not the HTTP handler.

### 2.2 Invariants (violate these in an interview and the design is wrong)

1. Model emits; runtime executes. Edges, handoffs, and `Process` are code, not tokens.
2. LangGraph node restart = **whole function** restarts. Non-idempotent tools double-charge unless wrapped in Functional API `task`s or the tool is idempotent.
3. Checkpoints are not mid-line. HITL inside a node still re-runs prefix side effects on resume unless `task`.
4. `LoopAgent` has no implicit quality stop. `max_turns=None` and “just set recursion_limit=10,000” are spend bugs.
5. Agent Server `/mcp` does not hold chat memory. Memory Bank / Store / checkpointer does.
6. A2A is not MCP and is not a framework replacement. No shared weights, tools, or checkpointers across vendors.
7. Hosted MCP approvals do not leak across `server_label`. Same tool name on another server is **not** pre-approved.
8. Version skew: graph code changes vs old checkpoints; interrupt/`task` order mismatch; Crew `["*"]` mid-LLM checkpoint + new prompt; Agent Server **revisions** vs in-flight threads — drain or reject old schemas.

### 2.3 Decision heuristics (mechanics only; `$` in §3, scenarios in §6)

| Pick | When the product *is* |
| --- | --- |
| **LangGraph** | A state machine: cycles, `Send` map-reduce, time-travel, multi-week HITL, typed reducers. Temporal plugin (Python, Public Preview) if waits must be durable and **$≈0 compute**. |
| **OpenAI Agents SDK** | A tool-using assistant on OpenAI’s hosted surface (web search, file search, code interpreter, hosted MCP, sandbox) with traces→evals in one vendor. Ship in a week; add Redis/SQLAlchemy sessions in front of workers. |
| **ADK + Agent Platform** | Control plane must be Google Cloud: IAM / VPC-SC / CMEK, Memory Bank, API Registry, A2A mesh, HIPAA matrix, Agent Gateway. Multi-language (Py/TS/Go/Java/Kotlin). LangGraph **can** deploy *on* Agent Runtime — hybrid that is real. |
| **CrewAI** | Unit of work is a **role team**; Flow is the outer app; AMP for business+eng Studio, GitHub deploy, Fortune-500 GTM. Still cap process, persist, and HITL before production traffic. |

Do not stack handoffs **and** `as_tool` **and** a third graph for one product surface. Do not run unbounded hierarchical Crews as the HTTP handler.

---

## 3. Token Economics & NFR Analysis

Prices below are from vendor docs dated **2026-08-21**. `$ / 1k executions` is **[inferred]** from published token rates × a stated reference loop, plus published platform SKUs. None of the four OSS runtimes sell a “per execution” SKU. ⚠️ No unpublished p50/p95/p99 agent-loop SLOs exist; missing percentiles are marked.

**Reference loop (one execution):** 1 user task, **4 model calls** (route + 2 tool-using turns + synthesize), no vision/audio, no web-search surcharge. Tokens per call: **3,000 input + 800 output**.

\[
C_{\mathrm{exec}} = 4 \times (3000\,P_{\mathrm{in}} + 800\,P_{\mathrm{out}}) / 10^{6}
\]

### 3.1 Cost per 1k runs (model tokens)

| Model | Input / 1M | Output / 1M | $ / exec **[inferred]** | **$ / 1k [inferred]** |
| --- | --- | --- | --- | --- |
| gpt-4.1 | $2.00 | $8.00 | $0.0496 | **$49.60** |
| gpt-5.6-terra | $2.00 | $12.00 | $0.0624 | **$62.40** |
| gpt-5.6-luna | $0.20 | $1.20 | $0.00624 | **$6.24** |
| Gemini 2.5 Flash | $0.30 | $2.50 (incl. thinking) | $0.0116 | **$11.60** |
| Gemini 3.6 Flash | $1.50 | $7.50 (incl. thinking) | $0.0420 | **$42.00** |
| Gemini 2.5 Pro (≤200k) | $1.25 | $10.00 | $0.0470 | **$47.00** |
| Gemini 2.5 Pro (>200k) | $2.50 | $15.00 | — | long-context row; do not use the ≤200k $47 |

gpt-4.1 cached input **$0.50 / 1M**. Regional OpenAI processing **+10%** for eligible models released on/after 2026-03-05. Batch typically **50%** where offered. Thinking/reasoning tokens bill as **output** on Gemini.

**Framework token overhead on the same 4-call skeleton:**

| Overhead | Extra calls | $ impact |
| --- | --- | --- |
| LangGraph | 0 extra LLM | Checkpointer I/O is infra (see LCU below) |
| SDK input guardrail (blocking, cheap model) | +1 | If parallel, you still pay if tripwire is late |
| CrewAI `memory=True` | +1 extract LLM/task + embed write/read | Default embedder is **not free**. **[inferred]** memory tax on 2-task loop: ~$0.001–$0.01/exec at Flash-Lite/luna — small vs gpt-4.1, material vs luna-only. Embeddings commonly ~$0.13/1M — confirm live. |
| Hierarchical Crew manager | +N manager LLM | Can **double** calls vs sequential |
| Handoff / `as_tool` nested | + nested `max_turns` | Worst-case 10×10 |
| ADK context summarization | ⚠️ unmetered in docs | Budget extra Flash calls from traces |

### 3.2 Platform SKUs (published; not “per execution”)

**LangSmith (langchain.com/pricing, 2026-08-21):**

| Item | Published |
| --- | --- |
| Developer | $0/seat, **5k** base traces/mo, 1 seat |
| Plus | **$39**/seat/mo, **10k** base traces/mo, Deployment + Engine |
| Enterprise | Custom; hybrid/self-host; custom SSO, ABAC, RBAC |
| LCU / LSU | **$1.50** compute/work; **$1.00** storage/traces |
| Trace retention | 14-day base vs **400-day** extended (upgrade fee) |
| Plus Deployment | **1 free Serverless Small**; more billed on resources |
| Runtime compute | **0.045 LCU / vCPU-hr** → **$0.0675 / vCPU-hr [inferred]** |
| Runtime memory | **0.006 LCU / GiB-hr** → **$0.009 / GiB-hr [inferred]** |
| DB compute / memory | **0.177 LSU / vCPU-hr**, **0.025 LSU / GiB-hr** |
| Sandbox | **0.0384 LCU / vCPU-hr**, **0.0123 LCU / GiB-hr**, **0.000123 LSU / GiB-hr**; per-second; **Cloud only** |
| Fleet included | Dev **5 LCU**/org/mo; Plus **25 LCU** |
| Engine | ~**5–30 LCU per run** (vendor estimate range, **not** an SLO); scheduled **every 6 hours** |

Do **not** mix superseded ZenML-era **$0.001/node** + standby $/minute with LCU/LSU.

**LangSmith Deployment $ / 1k [inferred]:** assume 2 vCPU-s compute per execution (⚠️ not published): \(1000 \times 2/3600 \times 0.045 \times 1.50 \approx \$0.038\). **Tokens dominate.** Dedicated prod DB is a **fixed** LSU burn while the deployment exists. Serverless scales to zero — internal/background agents.

**Gemini Enterprise Agent Platform (cite the date — two SKU generations):**

| Era | Runtime | RAM | Sessions | Memory Bank |
| --- | --- | --- | --- | --- |
| Blog 2025-12-18 (billing start **2026-01-28**) | $0.0864/vCPU-h (was $0.0994 from 2025-12-16) | $0.0090/GiB-h | **$0.25 / 1k stored events** | **$0.25 / 1k stored / mo** + **$0.50 / 1k retrieved** (1k retrievals/mo free on blog table) |
| Unified SKUs (fetched 2026-08-21) | First **50 vCPU-h/mo free**, then **$0.085 / vCPU-h** | First **100 GiB-h free**, then **$0.009 / GiB-h** | Storage **$0.30 / GiB-month** (1 GiB-mo free) + reads **1 vCPU-h / 3M ops**, writes **1 vCPU-h / 1M ops** | Same storage/ops; revisions count toward storage |

Idle Agent Runtime: **not billed** (nearest second). Code Execution / Computer Use: **same** compute/memory rates (no longer free after 2026-01-28). Agent Gateway: **1 vCPU-h per 15,000** API/auth requests **[published conversion]**.

**Agent Engine $ / 1k [inferred]:** 1 vCPU × 2 s × 1000 / 3600 × $0.085 ≈ **$0.047** runtime. Old event SKU: 3 events/request × 1k exec × $0.25/1k events = **$0.75 / 1k**. On GiB metering, 3k small JSON events ≪ 1 GiB — storage ≈ 0 at 1k scale. Memory Bank retrieval at $0.50/1k memories × 1 retrieve/exec = **$0.50 / 1k [inferred, old SKU]** — can **exceed** runtime. Do not freeze 2025-12 blog rates into 2027 budgets.

**CrewAI AMP:** Basic **Free, 50 workflow executions/month**. Enterprise included/max/additional: checkmark only, **no public $**. ⚠️ Cannot publish `$ / 1k` for AMP without a quote. OSS = $0 framework + your model bill.

**OpenAI hosted extras:** web search / file search / code interpreter / container minutes are **separate** Responses SKUs. Do not fold them into the 4-call skeleton.

### 3.3 Latency percentiles and mitigations

⚠️ **None of the four frameworks publish p50/p95/p99 for `invoke` / `Runner.run` / crew kickoff.** Agent Platform documents **quotas** (QPM), not latency SLOs. LangSmith Deployment exposes Agent Server API latency **as a metric you monitor**, not a contractual p99. Temporal + LangGraph: HITL waits are **durable and cheap** (signals/timers) — improves *cost while blocked*, not model p95.

Treat e2e as: model TTFT + decode + tool RTT × (turns−1) + queue wait (Agent Server dedicated workers vs in-process) + sandbox cold start. Do not cite a framework “overhead ms” without your own trace.

**Working composition SLA for the reference 4-call sequential loop** (interactive extract-class models; **[inferred]** — not a vendor SLO):

| Percentile | Composition assumption | Working target **[inferred]** | Mitigations |
| --- | --- | --- | --- |
| p50 | 4 × (TTFT≈0.5–0.9 s + short decode) + 2 × tool RTT≈0.1–0.3 s; no queue | **~4–8 s** time-to-final | Prefix cache; luna/Flash for route; stream first token; SDK `run_in_parallel` guardrails only if wasted tokens are acceptable |
| p95 | **[inferred]** 1.5–3× p50 from prefill queue + slow tool + worker queue | **~8–24 s** | Dedicated Agent Server execute pods ≠ API pods; cap `max_turns`; tool timeouts; pin MCP reconnect; avoid Crew `["*"]` per-LLM checkpoints |
| p99 | Hung tool, cold sandbox, QPM 429, breaker open, HITL mis-classified as model | **timeout envelope**, not a model number. HITL p99 = **human SLA (hours–weeks)** | Circuit-break to luna/Flash; fail closed on mutating tools; Temporal/Agent Server so wait ≠ gunicorn hold; shed burst at 90 QPM rather than retry-storm |

HITL that parks on Temporal / Agent Server / AMP webhook does **not** belong in the model p95 budget.

### 3.4 Throughput and back-pressure

| Knob | Documented value | Effect on $ and latency |
| --- | --- | --- |
| LangGraph `recursion_limit` | Default **1000** since v1.0.6 (ignore “25” blogs) | Cap spend; `GraphRecursionError` |
| Agents SDK `max_turns` | Default **10** | Hard stop; `MaxTurnsExceeded` |
| ADK `LoopAgent.max_iterations` | You set; examples **5** | Prevents infinite critic loops |
| Crew sequential vs hierarchical | Hierarchical adds manager tokens | Quality vs $ |
| LangGraph `Send` | N parallel node invocations | Latency ↓, $ × N if each calls an LLM |
| Agent Server dedicated workers | API pods ≠ execute pods | Smooth p99; more always-on $ |
| Agent Platform quotas | Query/StreamQuery **90/min** (one table); session writes **100/min**; Memory Bank write **100/min** / read **300/min**; other tables **10/min** | Burst fan-out **fails closed**. Scale tickets, don’t guess. |
| Prompt cache | gpt-4.1 cached $0.50/1M; Gemini cache rows | Dominates multi-turn if history is stable |
| AMP Basic | **50 exec/mo** | Demo cap, not a prod SLO |

**Back-pressure design:**

1. Gateway admits only if breaker is closed/half-open **and** QPM/TPM budget remains **and** execute-queue depth is under a watermark.
2. 429 + `Retry-After` → wait. Do not retry-storm Agent Platform 90 QPM.
3. Serverless Agent Runtime / LangSmith Serverless scale to zero (good for sparse agents). Dedicated LangSmith DB uptime is the **opposite** shape — always-on LSU.
4. Agent fleets: each user task is **N model calls**. Budget \(N \times (t_{\mathrm{model}} + t_{\mathrm{tool}})\). Cap N at the orchestrator. `Send` and ParallelAgent multiply downstream QPS.

### 3.5 Availability, RPO/RTO, compliance — explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **your** gateway; model + hosted tools are dependencies | Multi-vendor fallback vs output-distribution drift. Hosted MCP outage is an OpenAI dependency. |
| RPO | Checkpointer/session: **0** for irreversible tools (snapshot **before** execute). `InMemorySaver` / in-memory ADK sessions: RPO = process lifetime (**wrong** for prod HITL). Crew checkpoints: **best-effort** — not RPO=0 | Treating LanceDB-on-disk or MemorySaver as multi-region consensus |
| RTO | Interactive: fail over < 1 s to secondary model (luna/Flash). HITL: resume from `thread_id`/`RunState` in seconds of compute, **weeks** of wall clock | Fast failover vs identical tokens (temperature>0) |
| Consistency | Tool side effects: **exactly-once via idempotency keys**. At-least-once node/activity restart is the LangGraph/Temporal default | Replay re-bills if tools are not idempotent |
| Compliance | LangSmith Cloud US or EU; Hybrid/Self-Hosted keep data plane in VPC. Agent Platform: VPC-SC, CMEK, DRZ, HIPAA **Yes** for Runtime / Sessions / Memory Bank / Code Execution; Example Store: HIPAA yes, **no** VPC-SC/CMEK/DRZ. Access Transparency: Runtime/Sessions/Memory Bank — **not** eval/Example Store/Code Execution. CrewAI AMP Ent. lists FedRAMP High / SAM — **vendor claims, verify ATO**. OpenAI hosted tools = **account/region**, not an SDK knob | Residency vs hosted-tool convenience |
| Cost vs latency | luna **$6.24/1k** vs gpt-4.1 **$49.60/1k** vs terra **$62.40/1k** **[inferred]**; platform adders ~**$0.04–$0.75/1k** except Memory Bank retrieve and verbose event SKUs | Hierarchical Crew / nested `as_tool` / Memory Bank on the hot path |
| Idle cost | Agent Runtime idle **not billed**. Dedicated LangSmith prod DB **is billed** | Assuming both platforms have the same idle shape |

---

## 4. Distributed Resilience & Security

### 4.1 Checkpointers, Temporal, Kafka

**LangGraph checkpointers (thread-scoped):** `put` / `put_writes` / `get_tuple` / list. PK = `thread_id`; `checkpoint_id` for time-travel. Production: Postgres (Agent Server default) or Sqlite single-node. Stores (cross-thread): PostgresStore / Mongo / Redis / Upstash / InMemory; semantic search if embeddings configured (`dims`, `fields`).

**Retries / timeouts (`langgraph>=1.2`):** `RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2, max_interval=128, jitter=True)`. Default `retry_on` retries most exceptions **except** ValueError/TypeError/RuntimeError/OSError/…; HTTP retries **5xx only**. `NodeTimeoutError` is retryable. After exhaustion: optional `error_handler` → state update + `Command`. Order: attempt → retry → handler → bubble.

**Temporal plugin (Python, Public Preview):** graph as Workflow; nodes as Activities (timeouts, retries, at-least-once) or in-Workflow (**must be deterministic**). Checkpoints every node. `interrupt()` → durable wait (**no** compute while waiting). `continue-as-new` for long histories. Streaming via `streaming_topic` + `WorkflowStream`; Activity nodes batch signals (`streaming_batch_interval` default **100ms**). Activity retry **re-runs the whole node**. LangSmith integration ships Python+TS.

**Agent Server:** persistence automatic; task queue; dedicated workers scale independently from API. Graceful shutdown at superstep.

**Kafka (equivalent log — not a first-party SKU of any of the four):** topic-shard `agent.turns` / `agent.tool_intents` / `agent.dlq`. Produce the **intent** (`tool_call` + idempotency key) **before** the side effect (outbox). Tool workers consume, execute, produce `tool_result`. Compaction on `thread_id` is chain-of-custody; poison messages → DLQ after N. Agent Server’s task queue is the productized form of this split (API pods ≠ execute pods). ⚠️ No vendor publishes Kafka lag SLOs for agent buses.

**Agents SDK:** session backends `SQLiteSession`, `AsyncSQLiteSession`, `RedisSession` (TTL), `SQLAlchemySession`, `MongoDBSession`, `DaprSession` (30+ stores), `OpenAIConversationsSession`, wrappers `EncryptedSession`, `OpenAIResponsesCompactionSession` (do **not** wrap ConversationsSession). Process crash without session/`RunState` = lost in-flight turn. SDK is **not** Temporal. Hosted-tool retries are **opaque** (⚠️). Sandbox sessions durable for **files**, not the Runner loop.

**ADK:** local in-memory sessions vanish. Prod: Vertex/Agent Platform Sessions (GA per Dec 2025 blog) + Memory Bank (ACL 2025 method) + IAM Conditions. Failure recovery: restore conversation after crash; HITL pause inside workflows; rewind/invalidate suffix. Callbacks `on_model_error_callback` / `on_tool_error_callback`. ⚠️ No Temporal-equivalent first-party engine — wrap `Runner` with Cloud Tasks / Workflows / your orchestrator. MCP: `getstate`/`setstate` for Cloud Run/GKE; reconnect after restore.

**CrewAI:** Flow `@persist` DB-backed; `kickoff(inputs={"id": ...})` resumes same `flow_uuid`. Checkpointing (early; APIs may change): `CheckpointConfig`; events `task_completed`, `method_execution_finished`, `llm_call_completed`, `*`. `max_checkpoints` to bound disk. JSON files or SQLite. Writes **best-effort** (log + continue). `["*"]` hurts performance. LanceDB survives restarts on the **same volume** — not multi-region consensus. Wrap Flow steps / HTTP tools yourself; not Temporal.

| Capability | LangGraph | Agents SDK | ADK | CrewAI |
| --- | --- | --- | --- | --- |
| Thread checkpoint + time travel | Yes | `RunState` snapshot, not full graph history | Session events; rewind | Flow persist + early checkpoints |
| Cross-thread memory | Store | DIY / Conversations / your DB | Memory Bank | Unified Memory + LanceDB |
| Durable HITL wait (days, $≈0) | Agent Server or Temporal | You park `RunState` | Session + RequestInput | AMP webhooks / persist |
| Distributed workers | Agent Server queue / Temporal | Redis/SQL session + your fleet | Agent Runtime | AMP / your FastAPI |
| At-least-once node semantics | Yes (restart node) | Tool retry policies you write | Tool callbacks | Checkpoint skip completed tasks |

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | HTTP 429/5xx, TLS reset, Agent Platform QPM, NodeTimeoutError, MCP reconnect | Jittered backoff; honor `Retry-After`; retry **idempotent** model reads; HTTP 5xx only on LangGraph default policy |
| Permanent | HTTP 400, schema `ValueError`/`TypeError` (LangGraph default **does not** retry these), `MaxTurnsExceeded`, `GraphRecursionError`, output-guardrail tripwire | Fail the turn; fix graph/crew/prompt; do not bump limit to 10,000 |
| Poison pill | Conditional edge never `END`; Send+cycle; SDK ping-pong handoffs; LoopAgent without max; hierarchical deadlock (manager waits on HITL worker); identical TAO hash N times | Fuse; DLQ; never auto-replay irreversible tools |
| Semantic | Schema-valid unauthorized refund; injection in `tool_result`; hosted tools bypassing tool guardrails | RBAC + HITL + JSON-encode; not a retry |
| Version skew | Checkpoint vs new `interrupt`/`task` order; `restore_from_state_id` + `from_checkpoint`; AgentCard protocol version; Agent Server revision vs in-flight thread | Drain; reject; Store for cross-graph facts (subgraph namespaces hide parent updates) |

Shared modes across all four: non-idempotent tools + retry/resume = double refund; unbounded context = quadratic $ (Gemini Live/session window re-bills accumulated tokens per turn — Vertex LiveAPI docs); eval/trace PII; multi-framework A2A schema skew.

### 4.3 Circuit breaker (closed → open → half-open) and fallbacks

Per downstream (OpenAI, Gemini, MCP server, Agent Runtime):

- **Closed:** traffic flows; consecutive failures or error-rate window trips to **open**.
- **Open:** fail fast; start a timer. Interactive traffic routes to fallback (luna / Flash / deterministic JSON). Do not hold the user on a dead hosted-MCP path.
- **Half-open:** allow one probe (or a small percentage). Success → closed; fail → open.

**Fallback chain:** primary (terra / Pro / specialist Crew) → secondary (luna / Flash / sequential Crew without memory) → **deterministic degraded** (`{"status":"degraded","reason":...}`) that still satisfies the output schema. Do not fall back from structured `output_type` to free-form text on a parser path.

SDK-specific: consume streams to completion; `cancel("after_turn")` vs immediate. Long-lived workers: `flush_traces` on shutdown. `trace_include_sensitive_data` gates I/O in traces.

LangSmith LLM Gateway (Plus+): PII/secrets redaction, cost/rate limits, fallbacks — use it as the breaker’s sibling, not a substitute for your fuse.

### 4.4 Zero-Trust MCP, tool RBAC, PII, immutable logs

**Zero-Trust MCP [inferred pattern, not a product name]:**

1. No unauthenticated Streamable HTTP.
2. Per-user tokens via auth middleware — not a shared PAT in the graph / `mcps=[url_with_key]`.
3. `tool_filter` / namespace allowlists. SDK hosted tool search: official rule of thumb **<10 functions per namespace**; `ToolSearchTool` + `defer_loading=True`.
4. HITL on mutating tools. HostedMCP `require_approval` `"always"|"never"` + optional `on_approval_request`.
5. Hosted MCP = trust OpenAI’s egress to that URL.
6. A2A: mTLS/OIDC between **agent identities**, not MCP shared secrets. LangGraph user-scoped MCP: custom auth → headers/`httpx.Auth` on `MultiServerMCPClient`. CrewAI HTTP MCP: `headers={"Authorization": "Bearer ..."}`. ADK: reconnect + `tool_filter`. Agent Server `/mcp` uses the **same** Agent Server auth. Studio: `is_studio_user()` special case.

**Do not put secrets in graph state.** LangSmith: populate `config["configurable"]["langgraph_auth_user"]` after `@auth.authenticate`.

**Tool RBAC.** Attach tools per turn/role. SDK `isEnabled` / allowed subset. ADK Cloud **API Registry** + `ApiRegistry` for org-curated MCP/Apigee tools (Dec 2025). CrewAI AMP: feature RBAC (Manage/Read/None) + **entity** RBAC on automations, env vars, LLM connections, git repos. Factory: Entra `factory-admin` vs `member` via JWT.

**SSO surface:**

| | OSS libraries | Managed |
| --- | --- | --- |
| LangGraph / ADK / Crew / SDK | DIY in front of the process | LangSmith Ent. custom SSO; GCP IAM / OAuth / API keys / agent identity; AMP SAML 2.0 + OIDC (Entra/Okta/Auth0); Plus: Google/GitHub |

**PII pipeline:** detect → redact **before tokenize** → audit the redaction map (placeholder tokens), never log raw PII. LangSmith LLM Gateway redaction (Plus+). AMP Enterprise lists PII redaction. SDK: `EncryptedSession`, output-guardrail sanitizes rejected payloads to `"Output withheld by an output guardrail."`. Cached prefixes and traces (`trace_include_sensitive_data`) must not contain secrets. LangSmith **does not train** on your traces (ToS on pricing page).

**Immutable audit / chain of custody.** Persist `correlation_id`, tenant, agent, `thread_id`/`session_id`, hashed args, policy decision, fuse reason, HITL actor. Sinks: LangSmith traces + Agent Server access logs; OpenAI Traces + custom processors (OTel); GCP Cloud Audit + Cloud Trace + Feedback service; CrewAI AMP tracing + OTel. WORM object store or Kafka log is the second copy. Reconstruct: policy snapshot + model id + sampled turn + tool results + human interrupt.

**Sandboxes.** LangSmith: ephemeral, TTL, snapshot/fork, auth proxy; **not** on self-hosted. OpenAI: hosted Code Interpreter / local Shell / Computer — residency is account/region. Agent Platform Code Execution: billed at runtime rates; HIPAA yes; Access Transparency **no**. Programmatic Tool Calling (SDK): model-generated JS in hosted V8 with **no** Node/fs/net — only allowlisted tools.

---

## 5. Production Enterprise Code

Stdlib-only core: full-jitter retries matching LangGraph’s `RetryPolicy` shape (`max_attempts=3`, `initial_interval=0.5`, `backoff_factor=2`, `max_interval=128`, jitter), circuit breaker (closed → open → half-open), primary → secondary → deterministic degraded fallback, correlation-id JSON logs, PII detect→redact→audit, tool RBAC + idempotency (node restart safe), LangGraph-style reducer supersteps + `interrupt`/`resume`, SDK-style `max_turns` + handoff fuse. Run: `python agent_frameworks_runtime.py`.

LangGraph / SDK APIs below are **illustrative only** (not imported, not executed). The runnable core is the stdlib that follows.

```python
# ILLUSTRATIVE — requires `langgraph` / `openai-agents`. Not executed by this module.
# from langgraph.graph import StateGraph, START, END
# from langgraph.types import Command, interrupt
# from langgraph.checkpoint.postgres import PostgresSaver
# compiled = graph.compile(checkpointer=PostgresSaver.from_conn_string(dsn))
# compiled.invoke(intake, {"configurable": {"thread_id": tid}})          # may GraphInterrupt
# compiled.invoke(Command(resume="approve"), {"configurable": {"thread_id": tid}})
#
# from agents import Agent, Runner, SQLiteSession
# result = Runner.run(triage, user, session=session, max_turns=10)
# if result.interruptions:
#     state = result.to_state()
#     state.approve(result.interruptions[0])
#     Runner.run(triage, state, session=session)
```

```python
#!/usr/bin/env python3
"""Framework-agnostic runner + LangGraph-style state loop (stdlib only).

Run: python agent_frameworks_runtime.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- correlation-id JSON logs -------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "superstep": getattr(record, "superstep", None),
            "agent": getattr(record, "agent", None),
            "breaker": getattr(record, "breaker", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str, thread_id: str) -> CorrelationAdapter:
    base = logging.getLogger("agent.frameworks")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "thread_id": thread_id}
    )


# --- PII detect → redact → audit ---------------------------------------------

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token

        out = pat.sub(_sub, out)
    return out, audit


# --- errors, retries, circuit breaker ----------------------------------------


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class MaxTurnsExceeded(Exception):
    pass


class GraphRecursionError(Exception):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Closed → open → half-open. One probe in half-open."""

    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = BreakerState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state is BreakerState.CLOSED:
                return True
            if self.state is BreakerState.OPEN:
                if time.monotonic() - self.opened_at >= self.recovery_seconds:
                    self.state = BreakerState.HALF_OPEN
                    return True
                return False
            return True  # HALF_OPEN: single in-flight probe is the caller's duty

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
                self.state = BreakerState.OPEN
                self.opened_at = time.monotonic()


def retry_call(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    initial_interval: float = 0.5,
    backoff_factor: float = 2.0,
    max_interval: float = 128.0,
    jitter: bool = True,
) -> Any:
    """LangGraph RetryPolicy shape. Full jitter when jitter=True."""
    last: Exception | None = None
    for i in range(max_attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == max_attempts - 1:
                break
            cap = min(max_interval, initial_interval * (backoff_factor**i))
            time.sleep(random.random() * cap if jitter else cap)
    assert last is not None
    raise last


# --- model + tools -----------------------------------------------------------


@dataclass
class FunctionCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[FunctionCall]
    finish: bool
    handoff_to: str | None = None
    interrupt: dict[str, Any] | None = None


class ScriptedClient:
    def __init__(self, name: str, script: list[ModelTurn], fail_first: int = 0) -> None:
        self.name = name
        self._script = list(script)
        self._fail_first = fail_first
        self._i = 0

    def complete(self, _messages: list[dict[str, Any]]) -> ModelTurn:
        if self._fail_first > 0:
            self._fail_first -= 1
            raise TransientError(f"{self.name} 503")
        if self._i >= len(self._script):
            return ModelTurn("done", [], True)
        turn = self._script[self._i]
        self._i += 1
        return turn


class FallbackChain:
    def __init__(
        self,
        primary: ScriptedClient,
        secondary: ScriptedClient,
        breaker: CircuitBreaker,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    def complete(self, messages: list[dict[str, Any]], log: CorrelationAdapter) -> ModelTurn:
        if self.breaker.allow():
            try:
                turn = retry_call(lambda: self.primary.complete(messages))
                self.breaker.record_success()
                log.info("model_ok", extra={"agent": self.primary.name, "breaker": self.breaker.state.value})
                return turn
            except TransientError:
                self.breaker.record_failure()
                log.info("primary_fail", extra={"agent": self.primary.name, "breaker": self.breaker.state.value})
        else:
            log.info("breaker_open_failfast", extra={"breaker": self.breaker.state.value})

        try:
            turn = retry_call(lambda: self.secondary.complete(messages), max_attempts=2)
            log.info("fallback_secondary", extra={"agent": self.secondary.name})
            return turn
        except (TransientError, PermanentError):
            log.info("graceful_degradation")
            return ModelTurn(
                json.dumps({"status": "degraded", "reason": "both_models_unavailable"}),
                [],
                True,
            )


class ToolProxy:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        self._executors = executors
        self._done: dict[str, Any] = {}
        self._lock = threading.Lock()

    def execute(
        self,
        call: FunctionCall,
        *,
        tenant: str,
        thread_id: str,
        turn_index: int,
        allowed: set[str],
    ) -> dict[str, Any]:
        if call.name not in allowed:
            raise PermanentError(f"rbac deny {call.name}")
        if call.name not in self._executors:
            raise PermanentError(f"unknown tool {call.name}")
        canonical = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(
            f"{tenant}|{thread_id}|{call.name}|{canonical}|{turn_index}".encode()
        ).hexdigest()
        with self._lock:
            hit = self._done.get(key)
            if hit is not None:
                return hit
        raw = self._executors[call.name](call.arguments)
        result = {"call_id": call.id, "name": call.name, "payload": raw, "idempotency_key": key}
        with self._lock:
            self._done[key] = result
        return result


# --- LangGraph-style reducers + checkpointer ---------------------------------


def merge_writes(state: dict[str, Any], writes: dict[str, Any]) -> dict[str, Any]:
    """Reducer graph: messages/pii_audit/tao_hashes append; scalars last-write-wins."""
    out = dict(state)
    for key, value in writes.items():
        if key in {"messages", "pii_audit", "tao_hashes"} and isinstance(value, list):
            out[key] = list(out.get(key) or []) + list(value)
        else:
            out[key] = value
    return out


@dataclass
class Checkpoint:
    thread_id: str
    checkpoint_id: str
    superstep: int
    state: dict[str, Any]


class Checkpointer:
    def __init__(self) -> None:
        self._rows: dict[str, list[Checkpoint]] = {}
        self._lock = threading.Lock()

    def put(self, cp: Checkpoint) -> None:
        if len(cp.thread_id) > 255:
            raise PermanentError("thread_id exceeds PostgresSaver 255-char limit")
        with self._lock:
            self._rows.setdefault(cp.thread_id, []).append(cp)

    def latest(self, thread_id: str) -> Checkpoint | None:
        with self._lock:
            rows = self._rows.get(thread_id) or []
            return rows[-1] if rows else None


def empty_state(*, remaining_steps: int, max_turns: int, agent: str) -> dict[str, Any]:
    return {
        "messages": [],
        "pii_audit": [],
        "tao_hashes": [],
        "status": "running",
        "remaining_steps": remaining_steps,
        "turns": 0,
        "max_turns": max_turns,
        "agent": agent,
        "interrupt_payload": None,
        "resume": None,
    }


# --- runtimes ----------------------------------------------------------------


class GraphRuntime:
    """Stdlib stand-in for StateGraph supersteps + interrupt()/Command(resume=)."""

    def __init__(
        self,
        llm: FallbackChain,
        tools: ToolProxy,
        checkpointer: Checkpointer,
        *,
        tenant: str,
        thread_id: str,
        log: CorrelationAdapter,
        allowed: set[str],
        recursion_limit: int = 1000,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.checkpointer = checkpointer
        self.tenant = tenant
        self.thread_id = thread_id
        self.log = log
        self.allowed = allowed
        self.recursion_limit = recursion_limit

    def _checkpoint(self, superstep: int, state: dict[str, Any]) -> None:
        self.checkpointer.put(
            Checkpoint(self.thread_id, f"cp-{superstep}", superstep, dict(state))
        )

    def invoke(self, user_text: str | None = None, resume: str | None = None) -> dict[str, Any]:
        prior = self.checkpointer.latest(self.thread_id)
        state = dict(prior.state) if prior else empty_state(
            remaining_steps=self.recursion_limit, max_turns=self.recursion_limit, agent="graph"
        )
        superstep = prior.superstep + 1 if prior else 0
        turn_index = int(state.get("turns") or 0)

        if resume is not None:
            state = merge_writes(
                state, {"status": "running", "resume": resume, "interrupt_payload": None}
            )
            self.log.info("hitl_resume", extra={"superstep": superstep})

        if user_text:
            redacted, audit = redact_pii(user_text)
            state = merge_writes(
                state, {"messages": [{"role": "user", "content": redacted}], "pii_audit": audit}
            )

        while state["status"] == "running":
            if superstep >= self.recursion_limit or state["remaining_steps"] <= 0:
                state = merge_writes(state, {"status": "fused"})
                self._checkpoint(superstep, state)
                raise GraphRecursionError("recursion_limit")

            self.log.info("model_node", extra={"superstep": superstep, "agent": state["agent"]})
            turn = self.llm.complete(state["messages"], self.log)

            pending: dict[str, Any] = {
                "remaining_steps": state["remaining_steps"] - 1,
                "turns": turn_index + 1,
                "messages": [{"role": "assistant", "content": turn.text or ""}],
            }

            if turn.interrupt:
                pending["status"] = "interrupted"
                pending["interrupt_payload"] = turn.interrupt
                state = merge_writes(state, pending)
                self._checkpoint(superstep, state)
                self.log.info("hitl_interrupt", extra={"superstep": superstep})
                return state

            if turn.finish or not turn.tool_calls:
                pending["status"] = "done"
                state = merge_writes(state, pending)
                self._checkpoint(superstep, state)
                return state

            state = merge_writes(state, pending)
            self._checkpoint(superstep, state)
            superstep += 1

            observations: list[dict[str, Any]] = []
            for call in turn.tool_calls:
                result = self.tools.execute(
                    call,
                    tenant=self.tenant,
                    thread_id=self.thread_id,
                    turn_index=turn_index,
                    allowed=self.allowed,
                )
                payload, pii = redact_pii(json.dumps(result["payload"], default=str))
                observations.append({"role": "tool", "name": call.name, "content": payload})
                state = merge_writes(state, {"pii_audit": pii})

            state = merge_writes(state, {"messages": observations})
            self._checkpoint(superstep, state)
            superstep += 1
            turn_index += 1

        return state


class RoleLoopRuntime:
    """Stdlib stand-in for Runner.run: max_turns + handoff (specialist takes the reply)."""

    def __init__(
        self,
        llm: FallbackChain,
        *,
        log: CorrelationAdapter,
        max_turns: int = 10,
    ) -> None:
        self.llm = llm
        self.log = log
        self.max_turns = max_turns

    def run(self, user_text: str, agent: str = "triage") -> dict[str, Any]:
        redacted, audit = redact_pii(user_text)
        messages: list[dict[str, Any]] = [{"role": "user", "content": redacted}]
        current = agent
        for turn_i in range(self.max_turns):
            self.log.info("role_turn", extra={"agent": current, "superstep": turn_i})
            result = self.llm.complete(messages, self.log)
            messages.append({"role": "assistant", "content": result.text or "", "agent": current})
            if result.handoff_to:
                current = result.handoff_to
                self.log.info("handoff", extra={"agent": current})
                continue
            if result.finish:
                return {
                    "status": "done",
                    "agent": current,
                    "messages": messages,
                    "pii_audit": audit,
                    "turns": turn_i + 1,
                }
        raise MaxTurnsExceeded(f"max_turns={self.max_turns}")


# --- demo --------------------------------------------------------------------


def _lookup(args: dict[str, Any]) -> dict[str, Any]:
    return {"claim": args.get("claim_id"), "note": "contact jane@example.com"}


def main() -> None:
    tenant = "acme"
    cid = str(uuid.uuid4())
    thread_id = f"{tenant}:claim:42"
    log = build_logger(cid, tenant, thread_id)
    tools = ToolProxy({"lookup": _lookup})
    saver = Checkpointer()

    # 1) Graph HITL: interrupt then Command(resume=) equivalent
    hitl_llm = FallbackChain(
        ScriptedClient(
            "terra",
            [
                ModelTurn(None, [], False, interrupt={"claim_id": "42", "ask": "adjuster"}),
                ModelTurn("approved", [], True),
            ],
        ),
        ScriptedClient("luna", [ModelTurn("degraded-luna", [], True)]),
        CircuitBreaker(failure_threshold=3, recovery_seconds=0.05),
    )
    graph = GraphRuntime(
        hitl_llm, tools, saver, tenant=tenant, thread_id=thread_id, log=log, allowed={"lookup"}
    )
    paused = graph.invoke("review claim 42 for jane@example.com")
    assert paused["status"] == "interrupted"
    assert any(a["type"] == "email" for a in paused["pii_audit"])
    resumed = graph.invoke(resume="approve")
    assert resumed["status"] == "done"
    assert saver.latest(thread_id) is not None

    # 2) Role-loop fuse (SDK max_turns) — ping-pong handoffs
    ping = FallbackChain(
        ScriptedClient(
            "triage",
            [
                ModelTurn("to billing", [], False, handoff_to="billing"),
                ModelTurn("to triage", [], False, handoff_to="triage"),
                ModelTurn("to billing", [], False, handoff_to="billing"),
            ],
        ),
        ScriptedClient("luna", [ModelTurn("unused", [], True)]),
        CircuitBreaker(),
    )
    loop = RoleLoopRuntime(ping, log=log, max_turns=2)
    try:
        loop.run("refund please")
        raise AssertionError("expected MaxTurnsExceeded")
    except MaxTurnsExceeded:
        pass

    # 3) Transient retry then success; 4) breaker open → secondary
    fresh = FallbackChain(
        ScriptedClient("terra", [ModelTurn("ok", [], True)], fail_first=1),
        ScriptedClient("luna", [ModelTurn("unused", [], True)]),
        CircuitBreaker(),
    )
    assert fresh.complete([], log).text == "ok"

    dead_primary = ScriptedClient("terra", [], fail_first=99)
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60.0)
    luna = ScriptedClient(
        "luna",
        [ModelTurn("luna-ok", [], True), ModelTurn("luna-ok", [], True)],
    )
    chain = FallbackChain(dead_primary, luna, breaker)
    assert chain.complete([], log).text == "luna-ok"
    assert breaker.state is BreakerState.OPEN
    assert chain.complete([], log).text == "luna-ok"  # fail-fast on primary

    # 5) Idempotent tool replay (node restart)
    call = FunctionCall("c1", "lookup", {"claim_id": "42"})
    a = tools.execute(call, tenant=tenant, thread_id=thread_id, turn_index=0, allowed={"lookup"})
    b = tools.execute(call, tenant=tenant, thread_id=thread_id, turn_index=0, allowed={"lookup"})
    assert a["idempotency_key"] == b["idempotency_key"]
    try:
        tools.execute(call, tenant=tenant, thread_id=thread_id, turn_index=0, allowed=set())
        raise AssertionError("expected rbac deny")
    except PermanentError:
        pass

    log.info("runtime_self_check_ok")
    print("OK")


if __name__ == "__main__":
    main()
```

**What this encodes that interviews probe:** (1) HITL is a checkpointed status, not a held HTTP worker. (2) `max_turns` is a fuse, not a quality heuristic. (3) Breaker fail-fast is cheaper than retrying a dead primary. (4) Tool replay must hit the idempotency map because LangGraph/Temporal **re-run the whole node**. (5) PII never enters the message list in the raw form.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Regulated HITL claims (days-long wait, replay, SSO)

**Problem statement.** Design a claims copilot: pause for an adjuster who may take **weeks**, resume on the same claim, **no GPU/vCPU burn while waiting**, replayable state for audit, SSO/RBAC, irreversible payout tools. Default ADK session TTL **365 days is wrong** for a 7-year retention clock — set `expire_time`. Success metric is lawful payout + reconstructable trajectory, not tokens. Volume: low thousands of in-flight paused claims, bursty model calls only when a human resumes.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ FNOL /     │────▶│ CONTROL PLANE                                             │
│ adjuster UI│     │ Gateway: SSO, tenant, correlation-id, breaker             │
└────────────┘     │   ▼                                                       │
                   │ LangGraph StateGraph[ClaimState]  (or ADK 2.0 graph)      │
                   │ reducers on events[]; recursion_limit as spend fuse       │
                   │   ▼  payout > $X                                          │
                   │ interrupt() → Temporal Signal / Agent Server durable wait │
                   │   (compute $≈0 while parked)                              │
                   │ Command(resume=) on same thread_id ≤255                   │
                   └────┬─────────────────────────────┬────────────────────────┘
                        │                             │
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ model Activities │        │ policy MCP (read, audience)  │
                   │ replay-safe      │        │ payout MCP (HITL + idempot.) │
                   │ (tokens stored)  │        │ no PAT in graph state        │
                   └──────────────────┘        └──────────────────────────────┘
                        │
                        ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ PostgresSaver + Store (cross-claim facts, not subgraph)   │
                   │ Temporal history = wait fabric; Kafka outbox for payouts  │
                   │ traces → identity (langgraph_auth_user / GCP IAM) 400d    │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** LangGraph + Temporal (Public Preview plugin) or LangSmith Dedicated: durable `interrupt()`, Postgres checkpoints, time-travel, Ent. SSO/RBAC. Idempotent payout tool (`task` or idempotency key) because activity/node retry re-runs the whole node. If CMEK/VPC-SC/HIPAA on GCP is the constraint, ADK `RequestInput` + Sessions with explicit `expire_time` + Memory Bank ACL + Agent Runtime idle-not-billed. Do **not** self-host `invoke` and hold a gunicorn worker for weeks.

**Trade-off evaluation matrix.**

| Dimension | A. LangGraph + Temporal or LangSmith Dedicated | B. ADK + Agent Platform Sessions | C. Agents SDK parked `RunState` **or** CrewAI AMP webhook inbox |
| --- | --- | --- | --- |
| Cost | Tokens still **[inferred] $6–$62/1k** on the 4-call skeleton when *running*; wait compute **$≈0**. Deployment adder **[inferred] ~$0.038/1k** + Dedicated **fixed LSU**. Plus **$39/seat** | Runtime **[inferred] ~$0.047/1k** + idle **$0**. Old Memory Bank retrieve **[inferred] $0.50/1k** can exceed runtime. Event-era sessions **[inferred] $0.75/1k** if verbose | SDK: you pay OpenAI tokens + your DB. AMP: **50 exec/mo** Free; Ent. **$ unpublished** — cannot budget `/1k` |
| Latency | Model p95 irrelevant while parked; resume = Signal + one superstep. p99 = **adjuster SLA** | Same: RequestInput park. Rewind helps polluted context, not human p99 | SDK: resume latency = your queue. AMP inbox + SLA is the productized human p99 |
| Ops | Temporal UI + `continue-as-new`; Agent Server API≠execute pods; drain revisions | GCP ops, 90 QPM, session TTL footgun (365d default) | SDK: you build the wait fabric. AMP: business ops own the queue; OSS checkpoints **best-effort** |
| Security | LangSmith Ent. SSO/RBAC/ABAC; Hybrid data plane in VPC; no secrets in state | CMEK, VPC-SC, HIPAA matrix (Runtime/Sessions/Memory Bank **Yes**; Example Store/eval gaps) | SDK hosted tools **outside** your VPC. AMP Ent. SSO/entity RBAC; FedRAMP High = **verify ATO** |
| Scalability | 1k idle HITL workflows ≈ cheap; history growth is the constraint | Serverless Runtime scales; Memory Bank/session QPM is the fuse | SDK workers × Redis sessions. AMP Ent. scale unpublished |

**Decision rationale.** **A** is the best *mechanics* fit (typed state, time-travel, durable interrupt $≈0). Pick **B** when CMEK/VPC-SC is non-negotiable — and fix TTL on day one. **C** is correct only if OpenAI-hosted tools are the product (SDK) or business ops already live in AMP; you still owe a durable wait store. Outcome: never MemorySaver + HITL in prod (anti-pattern 1 in the research).

### Scenario 2 — High-volume inner loop vs OpenAI-hosted assistant (same org, two SKUs)

**Problem statement.** Two workloads share an org but must **not** share a runtime: (W1) **classification + 1–2 tools**, 1k exec/min peak, minimize `$ / 1k`; (W2) a customer-facing assistant that **needs** web search, vector-store file search, hosted MCP, guardrails, and traces→evals, ship in a week. Do not put W1 on hierarchical Crews or Memory Bank retrieval. Do not introduce LangGraph cycles until W2 needs time-travel or `Send`. Compliance: W2 may leave VPC via hosted tools; W1 must not.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ W1 batch / │────▶│ CONTROL PLANE (split by SKU)                              │
│  API       │     │ Gateway: tenant TPM, QPM, breaker, correlation-id         │
└────────────┘     │   ▼                                                       │
                   │ Router: W1 vs W2                                          │
                   │   ├─ W1: Agents SDK max_turns=3  OR  3-node LangGraph     │
                   │   │     luna / Gemini 2.5 Flash; no memory=True           │
                   │   │     LoopAgent only if max_iterations=1..3             │
                   │   └─ W2: Agents SDK + hosted tools + blocking input       │
                   │         guardrails on the public entry agent              │
                   │         Redis/SQLAlchemy session; max_turns=10 hard cap   │
                   └────┬─────────────────────────────┬────────────────────────┘
                        │ W1 local tools              │ W2 Responses hosted
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE W1    │        │ DATA PLANE W2 (OpenAI)       │
                   │ luna/Flash       │        │ WebSearch / FileSearch /     │
                   │ local MCP (VPC)  │        │ HostedMCP / Code Interpreter │
                   │ no hosted search │        │ tool guardrails DO NOT apply │
                   └──────────────────┘        │ HITL require_approval=always │
                                               └──────────────────────────────┘
                        │
                        ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ W1: ephemeral or Sqlite; traces sampled                   │
                   │ W2: RedisSession TTL + EncryptedSession; flush_traces     │
                   │ Optional later: LangGraph on Agent Runtime for W1 cycles  │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** W1 thinnest fuse: SDK `max_turns=3` or a 3-node graph; luna **[inferred] $6.24/1k** or Flash **[inferred] $11.60/1k** vs gpt-4.1 **[inferred] $49.60/1k**. Avoid Crew hierarchical (manager doubles calls) and `memory=True` (embed + extract tax). W2: Agents SDK — official pick when the product *is* OpenAI’s hosted surface. Budget search/file-search/container minutes **separately**. Put `require_approval` on mutating hosted MCP; do not pretend tool guardrails cover them. If W2 later needs cycles/`Send`/time-travel, **then** wrap those paths in LangGraph — do not start there. If the same org also has a GCP policy specialist, speak **A2A** (opaque) and keep MCP for tools; do not share checkpointers.

**Trade-off evaluation matrix.**

| Dimension | A. One hierarchical Crew + `memory=True` for W1 and W2 | B. Recommended: split SKU — thin luna/Flash loop (W1) + Agents SDK hosted tools (W2) | C. Everything on ADK LoopAgent + Memory Bank, or LangGraph cycles on every classify |
| --- | --- | --- | --- |
| Cost | Hierarchical **~2×** tokens **[inferred]**; memory tax **$0.001–$0.01/exec**; AMP `/1k` unknown | W1 **[inferred] $6.24–$11.60/1k** + ~$0.04 platform; W2 tokens + **separate** hosted SKUs. Nested `as_tool` 10×10 is a fuse you set, not a default | Memory Bank retrieve **[inferred] $0.50/1k** (old SKU) can exceed runtime **$0.047/1k**. Cycles add 0 extra LLM *if* no extra nodes — until Send fans out |
| Latency | Sequential token cliff as task N concatenates priors; checkpoint `*` hurts | W1 p50 **[inferred] ~4–8 s** 4-call; W2 = TTFT + hosted search RTT. Consume `stream_events()` to end | LoopAgent without max = infinite. 90 QPM fail-closed under W1 1k/min (**16.7 rps** needs a quota ticket) |
| Ops | One Studio; Flow still required as outer app (official) | Two runtimes, one gateway; Redis sessions for W2 workers | GCP-only ops win if already there; else lock-in + API Registry onboarding |
| Security | MCP URL+key in `mcps=[...]` is a secret-in-source bug | W1 stays in VPC. W2 hosted MCP = OpenAI egress; sticky approve `(server_label, tool)` | IAM Conditions on Sessions/Memory Bank are the win — not needed for W1 classify |
| Scalability | AMP Basic 50/mo is not 1k/min | W1: in-process or Serverless scale-to-zero. W2: session store + `max_turns` | Agent Runtime scales; Memory Bank read **300/min** (one table) is a W1 anti-pattern |

**Decision rationale.** **B** is the only option that keeps W1 near **[inferred] ~$6–12/1k**, puts a real fuse on both SKUs, and uses hosted tools only where they are the product. **A** fails the official Crew production shape (Flow outer, Crew inner) and the cost fuse (manager + memory on classify). **C** is the right *GCP digital workforce* answer (research scenario E) — API Registry, Memory Bank, Code Execution, HIPAA — but it is the wrong default for a 1k/min classifier and it is not how you get OpenAI hosted search in a week. Interview close: “Pick the metaphor that matches the product. Graph when it *is* a state machine. SDK when it *is* a hosted-tool assistant. ADK when the control plane *is* GCP. Crew when the unit of work *is* a role team — still wrap it in a Flow.”

---

*End of module. Six sections. Four frameworks (LangGraph, OpenAI Agents SDK, Google ADK, CrewAI). Token `$ / 1k` tables are **[inferred]** from the stated 4×(3k/800) loop and list prices dated 2026-08-21. No unpublished latency SLOs.*
