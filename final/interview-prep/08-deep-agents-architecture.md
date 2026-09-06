# Module 08: Deep Agents Architecture & Harness

## What Is This?

Deep Agents is LangChain's opinionated **harness** -- not a new runtime, not a new framework -- that assembles a production-ready agent in a single function call: `create_deep_agent()`. Think of it like this: LangGraph is the stove (durable execution, timers, interrupts). `create_agent` is a burner and a pan -- you bring every ingredient. Deep Agents is the *mise en place*: cutting board (virtual filesystem), prep cooks (sub-agents), a rule that leftover stock goes in the walk-in (offload/summarize). The output is a standard `CompiledStateGraph` -- not a proprietary black box. You can inspect it, extend it, or drop it into a larger LangGraph as a node.

The project was directly inspired by Claude Code: an attempt to understand what makes it effective and make those patterns model-agnostic. The 2026 industry consensus is that **the model is commodity; the harness is moat**. Two teams using the identical model can see a 40-point difference in task completion rates based purely on harness design.

**Package**: `deepagents==0.7.12` (PyPI 2026-09-01; Beta; Python `>=3.11,<4.0`; MIT). PyPI last-month downloads: **5,646,660**.

---

## Part 1: System Topology & Data Flow

### Three-Layer Hierarchy

The architecture is a strict three-layer stack. Each layer adds capability, but they are complementary -- not competing products:

```
Layer 3  create_deep_agent()  -- Full harness: VFS, sub-agents, skills,
         (Harness)               context management, planning, memory
         Most context-engineering defaults. Least control of loop shape.

Layer 2  create_agent()       -- Minimal harness: agent loop + tool
         (Framework)             interface + middleware hooks
         Non-deterministic loop + middleware hooks.

Layer 1  LangGraph            -- Graph runtime: nodes, edges, state,
         (Runtime)               checkpointing, streaming, interrupts
         Most control. Least opinion. Durable execution lives here.
```

**LangChain's published rule of thumb**: Start with Deep Agents; drop to `create_agent` or LangGraph when you need to own the harness or the graph shape.

### Architecture Diagram

```
+---------------------------------------------------------------------------+
|                         create_deep_agent()                               |
|                          (Factory Function)                               |
+---------------------------------------------------------------------------+
|  CONTROL PLANE  (construction -- LLM-free)                                |
|                                                                           |
|  +---------------+ +---------------+ +------------------+ +-----------+   |
|  | create_deep_  | | HarnessProfile| | permissions      | | interrupt |   |
|  | agent kwargs  | | ProviderProfile| | first-match     | | _on +     |   |
|  | model/tools   | | overlays      | | fail-open        | | checkpt   |   |
|  +-------+-------+ +-------+-------+ +--------+---------+ +-----+-----+  |
|          |                  |                   |                 |        |
|          v                  v                   v                 v        |
|  +--------------------------------------------------------------------+   |
|  |  graph.py: resolve model+profile -> backend -> middleware DAG ->    |   |
|  |    GP subagent -> USER|BASE|SUFFIX prompt -> create_agent(...) ->   |   |
|  |    .with_config({recursion_limit: 9999, ls_integration:deepagents}) |   |
|  +--------------------------------------------------------------------+   |
+---------------------------------------------------------------------------+
|  DATA PLANE  (untrusted token stream)                                     |
|                                                                           |
|  messages + assembled system + tool schemas -> model -> final | tool_calls|
|  Middleware adds/removes tools, injects prompt sections, compacts history,|
|  writes typed state, enforces FS perms BEFORE a built-in FS tool runs.   |
|                                                                           |
|  +-- TOOL PROXIES (least privilege -- not an omnibus shell) -----------+  |
|  | FS: ls read_file write_file edit_file glob grep delete              |  |
|  | execute (sandbox protocol only; else error string)  eval (QuickJS)  |  |
|  | task (GP + declarative SubAgent)   MCP/custom on tools= (additive)  |  |
|  | permissions= covers built-in FS only -- NOT MCP, NOT execute        |  |
|  +---------------------------------------------------------------------+  |
+---------------------------------------------------------------------------+
|  PERSISTENCE LAYER (two LangGraph systems + VFS lifetimes)                |
|                                                                           |
|  +-------------+ +-------------+ +--------------+ +-------------------+   |
|  | Checkpointer| | Store       | | VFS backends | | Sandbox / Hub     |   |
|  | thread_id   | | cross-thread| | State (dflt) | | remote container  |   |
|  | messages,   | | required for| | StoreBackend | | idle_ttl e.g.     |   |
|  | interrupts, | | StoreBackend| | Filesystem*  | | 3600s (docs ex.)  |   |
|  | time-travel | |             | | Composite    | | ContextHubBackend |   |
|  +-------------+ +-------------+ +--------------+ +-------------------+   |
|  *FilesystemBackend / LocalShellBackend: forbidden in deployed agents.    |
+---------------------------------------------------------------------------+
|  TELEMETRY                                                                |
|  LangSmith traces | metadata.ls_integration=deepagents                    |
|  stream.subagents (nested message/tool/task handles)                      |
|  summarization spans | metadata.lc_source=summarization                   |
+---------------------------------------------------------------------------+
```

### Four Capability Buckets

| Bucket | What Ships | Default On? |
|--------|-----------|-------------|
| **Execution Environment** | Custom tools + MCP (additive); VFS tools; `delete` (`>=0.7`); `execute` (sandbox only); optional QuickJS `eval` | FS yes; `execute` only if sandbox protocol; interpreter opt-in |
| **Context Management** | Skills (progressive disclosure), memory (`AGENTS.md` always loaded), summarization, large-result offload (20,000 tokens), prompt caching | Summarization + offload always; skills/memory if kwargs set; Anthropic cache middleware always registered |
| **Delegation** | `task` + auto GP subagent; opt-in `TodoListMiddleware` / `write_todos` | GP **on** unless profile `enabled=False`. Todos **off** since 0.7 |
| **Steering** | `interrupt_on` -> `HumanInTheLoopMiddleware`; filesystem `permissions` | Off unless configured. Permission `mode="interrupt"` auto-installs HITL (`>=0.6.8`) |

### Request Flow Narrative

1. **Control / construction (LLM-free).** Application calls `create_deep_agent`. `graph.py` in order: (1) resolve chat model + `ProviderProfile`/`HarnessProfile`; (2) resolve backend (`StateBackend()` if omitted); (3) assemble middleware stack; (4) build the default `general-purpose` subagent and any caller `subagents`; (5) compose the authored system prompt `USER -> BASE -> SUFFIX`; (6) call `langchain.agents.create_agent(...)`; (7) bind `.with_config({recursion_limit: 9_999, metadata: {ls_integration: "deepagents"}})`.
2. **Invoke contract.** Production always passes `thread_id` in `config["configurable"]` **and** a `context_schema` instance (`user_id`, flags). They are independent. No checkpointer = no resume and no HITL.
3. **Data plane loop.** LangGraph drives turns. Each turn the model sees message history + assembled system + the current tool surface. It returns a final message or tool calls. Tool results append to state. Middleware may compact, offload, patch dangling `tool_calls`, or interrupt **before** the next model call.
4. **Tool proxy.** Built-in FS tools hit the backend; `permissions` first-match inside `FilesystemMiddleware`. `task` fans out to a nested graph (isolated conversation). `execute` is real only if the backend implements `SandboxBackendProtocol`; otherwise an error string.
5. **Persistence.** Each super-step checkpoints. `DeepAgentState.messages` uses a `DeltaChannel` reducer (`langgraph>=1.2`) so growth stays linear.
6. **Stream / observe.** Typed projections for messages, tools, values, output; Deep Agents adds `stream.subagents`. Summarization LLM calls appear with `metadata.lc_source == "summarization"`.
7. **Stop.** Model stops calling tools, or `GraphRecursionError` at 9,999 super-steps (hard error), or HITL interrupt (requires checkpointer).

---

## Part 2: Core Mechanics & Algorithms

### Key Invariants

**I1. Deep Agents introduces no new runtime.** Durable execution, streaming, interrupts, checkpoints, stores = LangGraph. The loop shape is `create_agent`'s ReAct-style graph.

**I2. `tools=` is additive.** It never removes a built-in. Hide tools with `HarnessProfile.excluded_tools` or `FilesystemMiddleware(tools=[...])` (must keep `read_file`).

**I3. Required middleware cannot be excluded.** `_REQUIRED_MIDDLEWARE = (FilesystemMiddleware, SubAgentMiddleware)`. Attempting `excluded_middleware` on either raises **`ValueError`**. The filesystem "backs every built-in file tool and enforces `permissions`"; SubAgent "backs the `task` tool handler."

**I4. Permissions are fail-open and FS-only.** `permissions=` is a path PDP for built-in FS tools only, first-match-wins, **no match -> allow**. Does not cover MCP, custom tools, `execute`/shell, or direct `backend.*`.

**I5. Subagent `permissions` replace the parent list.** Declarative `SubAgent.system_prompt` is required and does not inherit. `CompiledSubAgent` / `AsyncSubAgent` do not inherit `interrupt_on`.

### Middleware Stack -- Deterministic Assembly

The middleware stack is the core extension mechanism. It replaces subclassing. Each middleware can hook into six lifecycle points:

```
before_agent --> before_model --> wrap_model_call --> after_model --> after_agent
                                       |
                                  wrap_tool_call
                                  (per tool invocation)
```

**Full stack, first to last:**

| # | Slot | When Present |
|---|------|-------------|
| 1 | `SkillsMiddleware` | `skills=` set. Before filesystem so skill metadata exists before file tools |
| 2 | `FilesystemMiddleware` | **Always**. Permissions live here |
| 3 | `SubAgentMiddleware` | >=1 synchronous subagent (including auto GP) |
| 4 | `SummarizationMiddleware` | **Always** (`create_summarization_middleware`) |
| 5 | `PatchToolCallsMiddleware` | **Always**. Repairs dangling tool calls after interrupt/cancel/malformed args |
| 6 | `AsyncSubAgentMiddleware` | Async `subagents` present |
| 7 | **Caller `middleware=`** | After Patch. `.name` match **replaces in place** (`>=0.7`); else appends |
| 8 | Profile `extra_middleware` | Resolved `HarnessProfile` |
| 9 | `_ToolExclusionMiddleware` | Profile `excluded_tools` |
| 10 | `AnthropicPromptCachingMiddleware` | **Unconditional**; `unsupported_model_behavior="ignore"` |
| 11 | `BedrockPromptCachingMiddleware` | If `langchain-aws` installed; no-op off-Bedrock |
| 12 | `FireworksPromptCachingMiddleware` | If `langchain-fireworks` installed |
| 13 | `MemoryMiddleware` | `memory=` set. **After** caching so AGENTS.md updates are less likely to bust the prefix |
| 14 | `HumanInTheLoopMiddleware` | `interrupt_on=` set, or any permission `mode="interrupt"` |

**Critical concurrency rule**: Never mutate `self` attributes inside hooks. Concurrent operations (sub-agents, parallel tool calls) will race. Use graph state for shared mutable data.

### Harness Profiles -- Declarative Tuning

**HarnessProfile** (agent-level tuning):

| Field | Effect |
|-------|--------|
| `base_system_prompt` | Replaces BASE in `USER -> BASE -> SUFFIX` |
| `system_prompt_suffix` | Always last; applied to main, declarative subagents, and auto GP |
| `tool_description_overrides` | Per-tool description map |
| `excluded_tools` | Post-injection name filter; can drop user tools and harness tools. As of **0.7.9** also blocks **execution** |
| `excluded_middleware` | Classes (exact type) or strings or `module:Class` |
| `extra_middleware` | Appended at slot 8 |
| `general_purpose_subagent` | `enabled` / rename / re-prompt the default sub-agent |

**ProviderProfile** (model construction): `init_kwargs`, `pre_init` hooks, `runtime_kwargs_factory` for `init_chat_model`. Applies only to `provider:model` strings, not prebuilt `BaseChatModel`.

**Resolution order**: Registration keys work at provider level (`"openai"`) or model level (`"openai:gpt-5.5"`). Model-level overrides win. Load order: built-ins, then entry-point plugins, then direct `register()` calls.

### `excluded_tools` vs `excluded_middleware` -- Interview Fork

| Intent | Correct Knob | Wrong Knob |
|--------|-------------|-----------|
| Hide FS tools from the model but keep scaffolding (offload, permissions, skills/memory still need VFS) | `excluded_tools={"ls","read_file",...}` or `FilesystemMiddleware(tools=["read_file","ls",...])` | `excluded_middleware={"FilesystemMiddleware"}` -> **ValueError** |
| Run with no `task` tool | Profile `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` + `subagents=` empty | `excluded_middleware={"SubAgentMiddleware"}` -> **ValueError** |
| Drop summarization | `excluded_middleware={"SummarizationMiddleware"}` | -- |
| Stop offering `execute` | `excluded_tools={"execute"}` (and/or non-sandbox backend) | Removing FS middleware |

### Prompt Assembly After v0.7

v0.7 cut the hidden base system prompt and trimmed built-in tool descriptions **43%**. Combined with opt-in todos, **base input tokens** dropped **65% (~6k -> ~2k)**.

Assembly: caller `system_prompt` first; profile BASE only if set; suffix last. Empty caller + empty profile = empty authored system prompt; the model still receives tool schemas and middleware-injected sections.

### `recursion_limit`: 9,999 is a Sentinel Dodge

`create_deep_agent` binds `recursion_limit: 9_999`. Bare LangGraph default is **25** super-steps. LangGraph `merge_configs` has historically **dropped** `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10,000), so binding 10,000 was a no-op and nested graphs fell back to 25. Hence 9,999.

Issue #1698 (0.4.4, closed 2026-03): subagents invoked without parent config ran at 25, hit `GraphRecursionError`, surfaced upstream as `CancelledError`, cancelled sibling `asyncio.gather` tasks. Product hop caps belong in application state / `ModelCallLimitMiddleware`. Hitting 9,999 is a **hard error**, not a degrade.

### Sub-Agent Architecture

The `task` tool spawns ephemeral sub-agents with:
- **Fresh context**: No conversation history from parent. Prevents context pollution.
- **Autonomous execution**: Runs to completion without parent interaction.
- **Single handoff**: Returns only the final result (~200 tokens).
- **Context isolation**: Heavy outputs stored in virtual filesystem; parent sees summaries.
- **Permission replacement**: Sub-agents inherit parent permissions by default. Explicit `permissions` in the sub-agent spec **replaces** parent rules entirely.

Custom `CompiledStateGraph` instances can be passed as sub-agents, so raw LangGraph orchestration plugs in alongside the harness's defaults.

### Double-Texting Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `enqueue` (default) | Queue new input, process after current run | Chat UIs, sequential workflows |
| `reject` | Refuse new input until current run completes | Critical operations |
| `interrupt` | Halt current run, preserve progress, process new input | Interactive editing |
| `rollback` | Halt current run, revert all progress, process from scratch | Fresh-start preference |

### Multi-Provider Model Interface

```
model="anthropic:claude-sonnet-4-6"     # Anthropic direct
model="openai:gpt-5.5"                  # OpenAI
model="google_genai:gemini-3.6-flash"   # Google
model="ollama:north-mini-code-1.0"      # Local via Ollama
model="azure_openai:gpt-5.5"            # Azure
model="fireworks:accounts/.../glm-5p2"  # Fireworks
```

Any model supporting tool calling works. This is the key differentiator versus single-vendor SDKs (Claude Agent SDK = Claude only, OpenAI Agents SDK = OpenAI only).

### Offload / Summarization State Machine

| Knob | Default |
|------|---------|
| Tool-result / large-arg offload | **20,000** tokens; substitution = path + preview of first **10 lines** |
| Summarization trigger (profile with `max_input_tokens`) | `("fraction", 0.85)` |
| Keep after summarize | `("fraction", 0.10)` |
| Fallback if no profile | trigger `("tokens", 170000)`, keep `("messages", 6)` |
| `grep` match cap | **1,000** matches; `truncated` flag |

Write/edit input offload is delayed until the session crosses 85% of window; results over 20k offload immediately. v0.7 `write_file` **overwrites** instead of erroring. `grep`/`glob` return partial results with `truncated`; agents that treat "no more matches" as "complete search" under-recall on large trees.

---

## Part 3: Token Economics & NFR Analysis

### Harness Token Tax (v0.7 Measured)

- Base input tokens: **~6k -> ~2k** (-65%) on a default-agent turn.
- Reward CIs span zero for every model (no statistically significant quality drop).
- Luna: **-34% tokens, -15% cost, +4% reward** (statistically clear reductions).
- Unused built-in tools still send full JSON schemas every turn. `excluded_tools` shrinks baseline prompt size.

### Prompt-Caching (Published Unit Prices)

Deep Agents **auto-applies** prompt caching to static system sections for Anthropic and Bedrock. "No configuration is required." Default Anthropic TTL: **5m**. Replace in place with `AnthropicPromptCachingMiddleware(ttl="1h")` for long gaps.

| Model | Input | 5m Write | Cache Read | Output |
|-------|-------|----------|------------|--------|
| Claude Sonnet 4.6 | $3 | $3.75 | $0.30 | $15 |
| Claude Sonnet 5 | $2 | $2.50 | $0.20 | $10 |
| Claude Opus 4.6-5 | $5 | $6.25 | $0.50 | $25 |
| Claude Haiku 4.5 | $1 | $1.25 | $0.10 | $5 |

USD per million tokens. Multipliers: 5m write = 1.25x base input; 1h write = 2x; read = 0.1x.

### Cost Per 1,000 Runs (Inferred)

Assumptions: Claude Sonnet 4.6, 10 model calls in a 5-minute window, GP off, 2k cached prefix (v0.7), 3k uncached/call, 800 output/call.

| Component | USD/run | USD/1k runs |
|-----------|---------|-------------|
| Cache write (2k x $3.75/MTok) + cache reads (9x2k x $0.30/MTok) + uncached (10x3k x $3/MTok) + output (10x800 x $15/MTok) | **$0.2229** | **$223** |
| Same run without caching | **$0.270** | **$270** |
| With 20k prefix (memory+skills, cached) | **$0.339** | **$339** |
| Same 20k prefix uncached | **$0.810** | **$810** |

**Default GP subagent on**: roughly **+0.8-1.0x** the main-agent bill per run (isolation trades main-context tokens for a second full tool-schema prefix). Todos cost more in LangChain's own evals -- leave them off.

### Latency SLA Targets (Inferred Policy -- No Vendor SLO Published)

| Path | p50 | p95 | p99 |
|------|-----|-----|-----|
| Streaming TTFT, parent, no summarizer | 640 ms | 2,560 ms | 5,120 ms |
| One ReAct cycle (model + StateBackend FS tool) | 2,000 ms | 8,000 ms | 20,000 ms |
| Summarization extra LLM hop | 2,000 ms | 6,000 ms | 15,000 ms |
| GP subagent isolated 8-call, parent waits | 16,000 ms | 64,000 ms | 160,000 ms |
| 10-call research run, GP off | 20,000 ms | 80,000 ms | 200,000 ms |
| Checkpointer `sync` extra per super-step | 10 ms | 50 ms | 200 ms |
| HITL interrupt clock (expire -> deny) | 30,000 ms | 180,000 ms | 600,000 ms |

### Model Eval Scores (Published, Not a Production SLA)

| Model | Overall | File Ops | Retrieval | Tool Use |
|-------|---------|----------|-----------|----------|
| `google_genai:gemini-3.6-flash` | **82%** | 100% | 100% | 90% |
| `openai:gpt-5.5` | **80%** | 92% | 100% | 84% |
| `openai:gpt-5.4` | **18%** | 100% | 100% | 18% |
| `anthropic:claude-opus-4-7` | **80%** | 100% | 100% | 82% |
| `openrouter:z-ai/glm-5.1` | **89%** | 92% | 100% | 89% |

**Interview trap**: `gpt-5.4` at 18% overall with 100% file ops -- a model can ace FS and still fail tool-use.

### Availability, RPO & RTO

| Target | Value | Notes |
|--------|-------|-------|
| Availability | 99.9% (3-nines) | Production agent harness uptime target |
| RPO | 0 (with PostgresSaver) | MemorySaver = total loss on crash |
| RTO | <5 min | Restart container + replay from last checkpoint |

| Checkpointer | Durability | Cost | Use Case |
|--------------|-----------|------|----------|
| MemorySaver | Zero -- total loss on crash | $0 | Dev/test only |
| PostgresSaver | ACID, point-in-time recovery | ~$50/mo | Production default |
| DynamoDBSaver | Auto-scaling, multi-region | Higher | AWS-native, global |

---

## Part 4: Distributed Resilience & Security

### Checkpointing Architecture

Each super-step writes a checkpoint keyed by `thread_id`. Additionally, per-task `checkpoint_writes` ensure succeeded siblings in the same super-step are not re-run.

| Mode | Persist When | Crash Mid-Run |
|------|-------------|---------------|
| `exit` | Only on graph exit / interrupt | Intermediate state **lost** |
| `async` | Async while next step runs | Small window of loss |
| `sync` | Before next step | Highest durability, extra latency |

**Checkpointing vs true durable execution**: LangGraph checkpointing saves state. Developer is responsible for detecting the need to restore. True durable execution (Temporal, AWS Step Functions) guarantees exactly-once semantics and side-effect deduplication. Production architectures in 2026 combine both: durable execution for side-effect guarantees + LangGraph for conversation state.

### Zero-Trust Agent Architecture

Deep Agents' own security model is **"trust the LLM."** Boundaries belong at tool/sandbox/permission/gateway code, not in the prompt.

| Control | What It Is | What It Is Not |
|---------|-----------|----------------|
| `permissions=` | Path glob PDP, `read`/`write`, `allow`/`deny`/`interrupt`, first-match, **fail-open** | Per-principal RBAC; shell/execute; MCP; `backend.*` |
| `excluded_tools` | Blunt name allowlist (+ execution block >=0.7.9) | Per-user roles |
| `interrupt_on` | Review queue. `when` predicate skips the batch | An authorization PDP |
| Gateway / Cedar / OPA | **The** MCP PEP (audience-bound tokens, hash-pin) | Not in `deepagents` |

**Correct permission ordering**: deny `.env` **before** allow `/workspace/**`. Zero-Trust MCP requires a **gateway PEP in front of MCP**: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, no token passthrough, hash-pinned tool JSON.

### Circuit Breaker Pattern

Deep Agents does **not** ship circuit breakers. Those are application concerns. Independent breakers needed for: parent model, summarizer model, checkpointer, store, sandbox pool.

**Fallback chain (required interview answer)**: **Deep Agents (full harness) -> `create_agent` (thin harness, your tools only) -> deterministic refuse.**

Never: model 429 -> unsandboxed `execute`. Never: HITL timeout -> auto-approve. Never: circuit open -> `excluded_middleware` the filesystem.

### PII Pipeline -- Detect, Redact, Audit

Three sinks, three controls:

| Sink | Default | Control |
|------|---------|---------|
| LangSmith traces | Logged when `LANGSMITH_TRACING=true` | `LANGSMITH_HIDE_INPUTS/OUTPUTS`; `Client(hide_inputs=..., anonymizer=...)` |
| Checkpoints / StateBackend files | Full messages + file bytes | Don't put secrets in VFS; deny `/workspace/.env` |
| Model context | Unredacted unless middleware | `PIIMiddleware("email", strategy="redact", apply_to_input=True)` |

`PIIMiddleware` is **not** in the default stack -- you append it.

---

## Part 5: Production Enterprise Code

```python
"""
Production Deep Agent: durable checkpointing, permissions, HITL,
model tiering, cost guardrails, and observability.

pip install deepagents langgraph-checkpoint-postgres langchain-anthropic
"""
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware
from deepagents.permissions import FilesystemPermission
from deepagents.profiles import HarnessProfile, register_harness_profile
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"
checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()
store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# -- Backend: thread-scoped scratch + durable cross-thread memory
backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (rt.server_info.user.identity, "memories"),
        ),
    },
)

# -- Permissions: deny secrets BEFORE allow workspace (first-match-wins)
permissions = [
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/credentials*", "/**/*.key"],
        mode="deny",
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/memories/**"],
        mode="interrupt",  # human approval for memory writes
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**"],
        mode="deny",  # deny-all catch-all
    ),
]

# -- Assemble the agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[],  # add your custom tools here
    system_prompt="You are a senior research assistant. Always cite sources.",
    middleware=[
        SummarizationMiddleware(trigger=("tokens", 80_000), retention=("messages", 15)),
        ToolCallLimitMiddleware(max_calls=200),
    ],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["deploy_service"]},
    checkpointer=checkpointer,
    store=store,
)

# -- Invoke with thread tracking
def handle_request(user_id: str, thread_id: str, message: str) -> str:
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    result = agent.invoke(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
    )
    return result["messages"][-1].content
```

---

## Part 6: System Design Scenarios

### Scenario: Multi-Tenant Customer Support Platform

**Problem**: B2B SaaS, 500 enterprise customers, AI agents searching private knowledge bases, creating tickets, escalating. Strict tenant isolation. 10,000 concurrent conversations. SOC 2.

**Architecture**: `create_deep_agent` with CompositeBackend (`/workspace/` -> StoreBackend per tenant, `/shared/` -> StoreBackend global read-only). PostgresSaver for checkpoints. Model tiering (Sonnet supervisor + Haiku workers, ~60% cost reduction). HITL on `create_ticket` and `escalate_to_human` only.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Model tiering | Frontier supervisor + Haiku workers | 60% cost reduction; ~4-point accuracy loss acceptable for KB lookups |
| Tenant isolation | Shared agent pool with namespace isolation | 500 tenants x dedicated instances is operationally expensive |
| Checkpointer | PostgresSaver | SOC 2 requires durable audit trail; SQL queries for compliance |
| HITL scope | Only ticket creation + escalation | Approving every search destroys UX |

### Scenario: Autonomous Code Review Pipeline

**Problem**: 200 developers, 50 repos. AI-assisted code review. Clone PR branch, run static analysis, read docs, produce structured review. 3-minute SLA. Code never leaves infrastructure.

**Architecture**: Self-hosted LangGraph on internal K8s. E2B Firecracker sandboxes for code execution. Supervisor (Sonnet) + 3 specialized workers (Haiku). Auth proxy for GitHub tokens (never inside sandbox). `code_analyzer` and `doc_reader` run in parallel, then `review_writer` synthesizes.

---

## Common Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| `ValueError` at construction | `excluded_middleware` names `FilesystemMiddleware`/`SubAgentMiddleware`; `read_file` omitted from allowlist | Hide tools or disable GP; never strip scaffolding |
| Tool hidden but still executable | `excluded_tools` on <0.7.9 | Pin `>=0.7.9` |
| Accidental GP spend / loops | Default GP auto-added; `task` on bare stack | `GeneralPurposeSubagentProfile(enabled=False)` |
| Subagent 25-step `CancelledError` | Child invoked without parent config (#1698) | Pin current version; verify gather semantics |
| Context overflow | Summarization excluded and offload path gone | Keep factory summarizer; `ContextOverflowError` -> summarize + retry |
| Artifact clobber | v0.7 `write_file` overwrites silently | Permissions deny; HITL on writes |
| Fail-open FS leak | No matching `permissions` rule -> allow | Deny secrets before allow workspace |
| MCP/custom FS bypass | `permissions=` is FS-tools-only | Gateway PEP in front of MCP |
| Prompt-cache miss after memory write | Memory in the cached segment | Keep memory small; MemoryMiddleware is after cache on purpose |
| Host shell in prod | `LocalShellBackend` / `FilesystemBackend` | Sandbox backend; 503 if pool empty |

---

## Interview Q&A

**Q1: What is Deep Agents, in one sentence?**
I treat it as LangChain's opinionated agent harness on top of LangChain and LangGraph that bundles filesystem access, context management, delegation, streaming, and human approval into one constructor -- `create_deep_agent`.

**Q2: How is Deep Agents different from LangChain vs LangGraph?**
LangChain is the framework with building blocks. LangGraph is the runtime for durable execution, streaming, threads, and interrupts. Deep Agents is a higher-level harness that prepackages those blocks with VFS, subagents, summarization, profiles, optional skills/memory/HITL. Same `CompiledStateGraph`. I can drop it into a larger graph as a node.

**Q3: Walk `create_deep_agent` to invoke.**
Control plane: resolve model and HarnessProfile, resolve backend (StateBackend default), assemble middleware, add GP unless disabled, compose USER->BASE->SUFFIX, `create_agent`, `with_config`. Data plane: LangGraph loop; middleware shapes tools and prompt before the model; `tools=` callables run only after the model chooses them. I always pass `thread_id` and a `context_schema` instance.

**Q4: `excluded_tools` vs `excluded_middleware`.**
I hide FS tools with `excluded_tools` or an allowlist that still includes `read_file`, because offload, permissions, skills, and memory need the VFS. If I `excluded_middleware` Filesystem or SubAgent, I get `ValueError` at construction. Pre-0.7.9 exclusion was visibility-only; I pin `>=0.7.9` so it also blocks execution.

**Q5: Give me $ per 1k for a research run.**
Inferred, not a SKU: Claude Sonnet 4.6, 10 calls in a 5-minute window, GP off, 2k cached prefix after v0.7, 3k uncached in, 800 out. One 5m write + nine reads + uncached + output = roughly $0.223/run -> **$223/1k**. Uncached = $270/1k. A 20k prefix with memory/skills = $339/1k cached vs $810 uncached. GP on is roughly +0.8-1.0x the main bill.

**Q6: Why is recursion_limit 9,999?**
LangGraph `merge_configs` historically dropped `recursion_limit` when it equaled the default 10,000, so nested graphs fell back to 25. Binding 9,999 dodges that sentinel. It is a super-step fuse, not max `task` calls. I still set a product hop cap well below it.

**Q7: Are permissions Zero Trust for MCP and shell?**
No. `permissions=` is a fail-open path PDP for built-in FS tools. MCP, custom tools, execute, and `backend.*` are uncovered. Zero-Trust MCP is a gateway PEP: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, no client-token passthrough. Identity from the verified token / RunContext -- never from model JSON.

**Q8: Circuit breaker and fallback.**
The library does not ship a breaker. I wrap invoke: closed -> open -> half-open with one probe. Independent breakers for parent, summarizer, checkpointer, sandbox. Fallback chain: Deep Agents -> `create_agent` -> deterministic refuse. I strip Fireworks cache headers on cross-provider fallback. I never fail open to LocalShellBackend. HITL timeout does not auto-approve.

**Q9: Deep Agents vs create_agent vs custom LangGraph vs Claude Agent SDK.**
Research copilot, artifacts, subagents, summarization: Deep Agents. Short RAG Q&A, 1-2 tools, latency-sensitive: `create_agent`. Claims workflow with code scoring and policy in edges: custom LangGraph. Coding assistant: Deep Agents + remote sandbox if I need model portability; Claude Agent SDK if already Claude-in-sandbox and will own HTTP/auth/tenancy.

**Q10: What did v0.7 actually change?**
Todos opt-in, hidden base prompt removed, tool descriptions -43%, base tokens ~6k->~2k, `.name` middleware override, `delete` tool. Reward CIs spanned zero; Luna cost -15%. Footguns that remain: default GP on, permissions fail-open, `write_file` overwrites, `grep` 1,000-match truncate, 9,999 is not a product cap, beta + fast-moving.

---

## Key Numbers to Memorize

| Number | What |
|--------|------|
| **0.7.12** | Current research pin (Beta; MIT) |
| **5,646,660** | PyPI last-month downloads |
| **~6k -> ~2k / -65%** | v0.7 base input tokens |
| **-43%** | Built-in tool description trim |
| **20,000 / 10 lines** | Offload threshold / preview |
| **0.85 / 0.10** | Summarize trigger / keep fractions |
| **1,000** | `grep` match cap |
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **25** | Bare LangGraph default; subagent footgun |
| **255** | PostgresSaver `thread_id` max chars |
| **5m** | Default cache TTL |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$223 / 1k** | 10-call cached 2k prefix, GP off |
| **fail-open** | `permissions=` default when no rule matches |

## Quick Reference

| When to use | Choose |
|-------------|--------|
| Long-horizon artifact work | Deep Agents |
| Short latency-sensitive loop | `create_agent` |
| Policy in edges | Custom LangGraph |
| Claude-in-sandbox + own server | Claude Agent SDK |
| Maximum control, custom workflows | Raw LangGraph |
| Fast multi-agent prototyping | CrewAI |
