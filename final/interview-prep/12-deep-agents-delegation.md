# Module 12: Deep Agents -- Delegation, Planning & Subagents

## What Is This?

Delegation is how Deep Agents keeps one model from carrying every subproblem in a single context window. The biggest win is not parallelism -- it is context quarantine: a child agent processes heavy work in a fresh window and returns one compact result to the coordinator, keeping the parent's context clean and focused. Deep Agents provides five subagent forms (general-purpose, declarative SubAgent, CompiledSubAgent, AsyncSubAgent, and dynamic via QuickJS interpreter), a built-in `task` tool for synchronous delegation, optional TodoListMiddleware for structured planning, and an event streaming architecture that gives frontends real-time visibility into every delegation hop. The coordination model follows a data-plane isolation contract: each child gets its own context, and only a single ToolMessage crosses the boundary back to the parent.

---

## 1. System Topology & Data Flow

### 1.1 Delegation Architecture

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  PARENT AGENT (coordinator)                                         │
  │  ┌───────────────────────────────────────────────────────────────┐  │
  │  │  Model proposes tool_calls                                    │  │
  │  │    ├── task(name="researcher", task="Find X")  [sync]        │  │
  │  │    ├── start_async_task(spec="bg-worker")      [async]       │  │
  │  │    └── write_todos(items=[...])                [planning]    │  │
  │  └───────────────────────────┬───────────────────────────────────┘  │
  │                              │                                      │
  │                    ┌─────────▼──────────┐                          │
  │                    │  DELEGATION ROUTER │                          │
  │                    │  (ToolNode)         │                          │
  │                    └─────────┬──────────┘                          │
  │                              │                                      │
  │           ┌──────────────────┼───────────────────┐                 │
  │           ▼                  ▼                   ▼                  │
  │  ┌────────────┐    ┌────────────┐      ┌─────────────┐            │
  │  │ GP Child   │    │ Declarative│      │ Async Child  │            │
  │  │ (default)  │    │ SubAgent   │      │ (own thread) │            │
  │  │ fresh ctx  │    │ own prompt │      │ background   │            │
  │  │ inherits   │    │ own tools  │      │ 5 lifecycle  │            │
  │  │ tools/model│    │ replaces   │      │ tools on     │            │
  │  │            │    │ on match   │      │ supervisor   │            │
  │  └─────┬──────┘    └─────┬──────┘      └──────┬──────┘            │
  │        │                 │                     │                    │
  │        ▼                 ▼                     ▼                    │
  │  ┌──────────────────────────────────────────────────────────────┐  │
  │  │  Single ToolMessage returned to parent                       │  │
  │  │  (child messages, tool calls, reasoning stay in child)       │  │
  │  └──────────────────────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────────┘

  STREAMING (real-time visibility into child work)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  stream.subagents → one handle per task() delegation                │
  │    name = subagent_type (e.g. "researcher", "coder")               │
  │  thread.subagents → Agent Server alias                              │
  │  event streaming v3 (deepagents >= 0.6)                             │
  │  Do NOT consume raw subgraph namespaces -- use stream.subagents     │
  └──────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data-Plane Isolation Contract

The fundamental invariant of delegation: **child messages never pollute the parent window.** Each `task()` call creates a fresh child context. The child executes its full ReAct loop independently, and only a single ToolMessage (the final report) crosses back to the parent. This is what makes delegation a context management tool, not just a parallelism feature.

**Real-world example:** A research agent needs to analyze a 50-page PDF. Without delegation, the PDF content (approximately 75k tokens) enters the parent context and may trigger summarization. With delegation, a "document-analyst" subagent processes the PDF in its own 200k window and returns a 500-token summary to the parent. The parent's context budget is consumed by only 500 tokens instead of 75k.

**Context quarantine ROI:**

| Scenario | Without Delegation | With Delegation | Savings |
|----------|-------------------|-----------------|---------|
| 50-page PDF analysis | 75k tokens in parent window | 500-token summary in parent | 99.3% context savings |
| 4 parallel web searches | 4 x 10k = 40k tokens | 4 x 200-token summaries | 98% context savings |
| Database query + analysis | 30k result + 5k reasoning | 300-token conclusion | 99.1% context savings |

---

## 2. Core Mechanics & Algorithms

### 2.1 Five Subagent Forms

| Form | How Created | When Used | Loop Location |
|------|------------|-----------|---------------|
| **General-Purpose (GP)** | Auto-added by default | Any `task()` call that doesn't match a named spec | Same process; fresh context |
| **Declarative SubAgent** | `SubAgent(name=..., instructions=..., tools=...)` | Named specialization with custom prompt/tools | Same process; spec overrides |
| **CompiledSubAgent** | Pre-compiled LangGraph graph as subagent | Maximum control; own graph topology | Same process; own graph |
| **AsyncSubAgent** | `AsyncSubAgent(name=..., url=...)` | Non-blocking background work | Own thread; optional remote |
| **Dynamic (QuickJS)** | Interpreter `eval()` creates at runtime | Model designs agents on-the-fly (RLM paper pattern) | QuickJS sandbox; parent process |

**Disabling GP:** The general-purpose subagent is auto-added by default. To disable it, you need both a HarnessProfile that opts out AND no synchronous subagent specs. If GP is the only subagent, `task()` won't exist as a tool.

### 2.2 Inheritance Matrix

This is one of the most interview-tested topics. What a child inherits from the parent depends on the subagent form:

| Property | GP | Declarative SubAgent | CompiledSubAgent | AsyncSubAgent |
|----------|----|--------------------|-----------------|---------------|
| **tools** | Inherits | Inherits (spec can add/replace) | **Own graph** | **Own deployment** |
| **model** | Inherits | Inherits (spec can override) | **Own graph** | **Own deployment** |
| **system_prompt** | Inherits | **Replaced** if spec sets it | **Own graph** | **Own deployment** |
| **middleware** | **Does NOT inherit** | **Does NOT inherit** | **Own graph** | **Own deployment** |
| **skills** | **Inherits** | **Does NOT inherit** (needs own `skills=`) | **Own graph** | **Own deployment** |
| **interrupt_on (HITL)** | Inherits | Inherits; spec **replaces entirely** if set (PR #2334) | **Does NOT inherit** | **Does NOT inherit** |
| **permissions** | Inherits | **Replaces entirely** if set | **Does NOT inherit** | **Own deployment** |

**Critical interview point:** Setting `interrupt_on` or `permissions` on a declarative spec does NOT merge with parent rules -- it **completely replaces** them. This enables principle-of-least-privilege per delegation level but also means you can accidentally drop parent safety gates.

### 2.3 The `task` Tool (Synchronous Delegation)

The built-in `task` tool is the primary delegation primitive. It is synchronous: the parent blocks until the child completes.

```python
# Model calls task() to delegate
# task(name="researcher", task="Find the latest pricing for competitor X")

# What happens under the hood:
# 1. Router matches "researcher" against subagent specs
# 2. If match: use that spec's config. If no match: use GP.
# 3. Child gets fresh context window with the task description
# 4. Child runs full ReAct loop (model calls, tool use, reasoning)
# 5. Child's final message becomes a ToolMessage to the parent
# 6. ALL child intermediate messages stay in the child context
```

**Multiple parallel `task()` calls:** When the model proposes multiple `task()` calls in a single turn, they execute in parallel (up to the model's tool-calling batch). This is the primary parallelism mechanism.

### 2.4 Async Subagents

Async delegation uses `AsyncSubAgent` specs and a different control model. The supervisor launches a background task, gets a task ID immediately, and can continue working or talking to the user.

**Five lifecycle tools added to the supervisor:**

| Tool | Purpose |
|------|---------|
| `start_async_task` | Launch background work; returns task ID immediately |
| `check_async_task` | Poll status and get partial/final results |
| `update_async_task` | Send additional context to a running task |
| `cancel_async_task` | Stop a running task |
| `list_async_tasks` | Show all active/completed tasks |

**Transport options:**

| Transport | When | URL |
|-----------|------|-----|
| **ASGI** | Co-deployed graphs (same process) | `url` omitted |
| **HTTP** | Remote Agent Protocol server | `url="https://worker.example.com"` |

**Worker-pool sizing rule:** `1 supervisor + N active async subagents = N + 1 worker slots`. Each async subagent occupies its own thread on the Agent Server.

**Common anti-pattern:** Launching `start_async_task` and immediately polling `check_async_task` in a loop. This turns async delegation back into blocking and wastes model calls. The docs explicitly call this out.

### 2.5 TodoListMiddleware (Task Planning)

TodoListMiddleware adds a structured planning surface via a `write_todos` tool. It was opt-in since v0.7 and is **not** the default.

| Aspect | Detail |
|--------|--------|
| **Version** | Opt-in since deepagents >= 0.7 |
| **Tool added** | `write_todos` |
| **Statuses** | pending, in_progress, completed |
| **Streaming** | `stream.values.todos` |
| **Accuracy impact** | **No measured improvement** in task accuracy |
| **Token impact** | **Higher token usage** (plan tokens + status updates) |
| **Orchestration** | Planning only -- `write_todos` does NOT execute delegation |

**Key insight:** TodoListMiddleware is a UX feature for visibility, not an accuracy feature. The model writes a plan, but executing the plan still requires `task()` calls. If you need accurate multi-step execution, invest in better prompts and subagent specs rather than adding a todo list.

### 2.6 `recursion_limit` and Call Caps

**`recursion_limit: 9,999`** is bound by `create_deep_agent`. This is NOT a max-subagent count -- it counts LangGraph super-steps. The reason for 9,999 (not 10,000) is a sentinel dodge: `merge_configs` historically dropped `recursion_limit` when it equaled `DEFAULT_RECURSION_LIMIT` (10,000), making 10,000 a no-op. Frontend examples still pass 10,000 -- that may be silently dropped.

**Historical subagent bug (#1698):** Parent did not forward config to children, causing children to run at the bare LangGraph default of 25 super-steps. This surfaced as `CancelledError`. Fixed by propagating the parent's bound config.

**Call caps (separate from recursion_limit):**

| Cap | Scope | Needs Checkpointer |
|-----|-------|-------------------|
| `ModelCallLimitMiddleware.run_limit` | This invoke only | No |
| `ModelCallLimitMiddleware.thread_limit` | Across all invokes on this thread | **Yes** |
| `ToolCallLimitMiddleware.run_limit` | Tool calls this invoke | No |

A confused agent can burn budget **inside** 9,999 super-steps. Always set product-level call caps in addition to the recursion limit.

### 2.7 Delegation Patterns

| Pattern | Description | When |
|---------|-------------|------|
| **Supervisor** | One coordinator dispatches to specialized children | Default Deep Agents pattern |
| **Fan-out / Gather** | Parallel `task()` calls; coordinator synthesizes | Research across multiple sources |
| **Pipeline** | Sequential delegation: output of child A becomes input to child B | Document processing stages |
| **Debate** | Two children argue opposing positions; coordinator judges | Complex decisions requiring multiple perspectives |
| **Swarm** | Dynamic peer-to-peer delegation | Emergent multi-agent (rarely used in production) |

### 2.8 Protocol Stack (MCP / A2A / AG-UI)

Three protocols serve different purposes -- they stack, they do not compete:

| Protocol | Direction | What It Connects | In Deep Agents |
|----------|-----------|-----------------|----------------|
| **MCP** | Agent <-> Tools/Data | Agent calls external tools | Egress: `tools=` from MCP servers. Ingress: `/mcp` exposes agent as tool |
| **A2A** | Agent <-> Agent | Cross-deployment communication | Agent Server `POST /a2a/{assistant_id}` |
| **AG-UI** | Agent <-> Frontend | User-facing streaming | Event streaming v3 + `stream.subagents` |

**A2A is NOT the `task` tool.** Subagents are in-process child graphs with context quarantine. A2A is a network protocol between separate deployments or vendors. Use subagents for private decomposition; A2A only across trust-domain boundaries.

---

## 3. Token Economics & NFR Analysis

### 3.1 Delegation Cost Model

**Per-child cost formula:**

```
C_child = C_prefix_child + C_dynamic_child + C_output_child + C_tool_calls_child

Where:
  C_prefix_child  = child's stable prefix (may be smaller if fewer tools)
  C_dynamic_child = task description + tool results in child context  
  C_output_child  = child's reasoning + final report
  C_tool_calls_child = tools invoked by the child
```

**Multi-agent cost benchmarks (Anthropic research):**

| Configuration | Task Success | Token Multiplier | When Worth It |
|---------------|-------------|-------------------|---------------|
| Single Opus 4 agent | Baseline | 1x | Simple tasks |
| Multi-agent (3-5 parallel subagents) | **90.2%** (vs single) | **4x** chat tokens, **15x** total | Complex multi-step tasks |
| Recommended parallel subagents | -- | **3-5** optimal | Diminishing returns beyond 5 |

**Cost with delegation (10-call parent + 1 GP child, Sonnet 4.6):**

| Configuration | Per Run | Per 1k Runs |
|---------------|---------|-------------|
| Parent only, 10 calls, cached | $0.2229 | **$223** |
| Parent + 1 GP child (8 calls) | $0.4032 | **$403** |
| Parent + 2 GP children | $0.5835 | **$584** |
| Parent + 3 GP children | $0.7638 | **$764** |
| Uncached parent + 1 GP child | $0.5400 | **$540** |

The GP child adds approximately $0.18/child/run (8-call child with separate cache). Context quarantine is not free -- but it is cheaper than context overflow, summarization loops, or wrong answers from a bloated parent context.

### 3.2 TodoListMiddleware Token Overhead

| Metric | Without Todos | With Todos |
|--------|--------------|------------|
| Accuracy | Baseline | **No measured improvement** |
| Token usage | Baseline | **+15-30%** (plan generation + status updates) |
| Turns | Baseline | **+1-2** (planning turn + status updates) |

**Recommendation:** Do not add TodoListMiddleware for accuracy. Add it only when the UX requires visible task progress (e.g., `stream.values.todos` in a UI).

### 3.3 Latency SLA Targets

| Path | p50 | p95 | p99 | Notes |
|------|-----|-----|-----|-------|
| **Single `task()` call (8-call child)** | **16,000 ms** | **64,000 ms** | **160,000 ms** | [inferred] Parent blocks for duration |
| **Parallel 3x `task()` calls** | **16,000 ms** | **64,000 ms** | **160,000 ms** | [inferred] Parallel = same wall-clock as single |
| **Async task start** | **2,000 ms** | **8,000 ms** | **20,000 ms** | [inferred] One model call to dispatch |
| **Full 10-call parent + 1 GP child** | **36,000 ms** | **144,000 ms** | **360,000 ms** | [inferred] Sequential parent + child |
| **HITL on delegated tool** | **30,000 ms** | **180,000 ms** | **600,000 ms** | [inferred policy] Human clock |

All latency figures are [inferred policy targets], not vendor SLOs.

### 3.4 Availability & Recovery

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Availability** | 99.9% | Standard for internal tooling |
| **RPO** | Last durable super-step | Child checkpoints independently if using Agent Server |
| **RTO** | <5 min (container restart + checkpoint resume) | Any replica can resume |

---

## 4. Distributed Resilience & Security

### 4.1 Subagent Failure Handling

When a child subagent fails, the failure propagates as an error string in the ToolMessage back to the parent. The parent model then decides what to do: retry, try a different approach, or report failure to the user.

| Failure | Impact | Handling |
|---------|--------|---------|
| Child hits recursion limit | `GraphRecursionError` as ToolMessage to parent | Parent can retry with simpler task or report |
| Child tool error | Error in ToolMessage | Parent can retry or use different tools |
| Child timeout | Timeout error in ToolMessage | Parent can decompose into smaller tasks |
| Async child dies | `check_async_task` shows failure | Parent can restart or cancel |

### 4.2 Security Architecture

**HITL inheritance gaps (the interpreter hole):**
- Declarative specs and auto GP inherit parent `interrupt_on` / `permissions` (PR #2334)
- Compiled and async subagents do **NOT** inherit -- you must wire HITL inside the child
- QuickJS interpreter `task()` from `eval` **skips** parent `interrupt_on` per dispatch
- `interrupt_on={"task": True}` does NOT catch JavaScript `task()`
- **Mitigation:** Gate `eval`; restate parent gates inside compiled/async children

**Zero-Trust MCP on children:** `permissions=` is a fail-open FS path PDP that covers only built-in filesystem tools. MCP tools, custom tools, `execute`, and `backend.upload_files` are outside that PDP. Children calling MCP tools still need the gateway PEP.

**RBAC per subagent form:**

| Control | GP | Declarative | Compiled | Async |
|---------|-----|------------|----------|-------|
| Parent HITL gates | Inherited | Inherited (replaced if set) | Must wire own | Must wire own |
| Parent FS permissions | Inherited | Replaced if set | Must wire own | Must wire own |
| MCP gateway | Still required | Still required | Still required | Still required |
| Tool allowlist | Inherits parent tools | Can restrict via spec tools | Own graph | Own deployment |

### 4.3 PII Pipeline for Delegation

Apply detect -> redact -> audit on:
- Task descriptions passed to children (may contain user data)
- Child ToolMessage results returned to parent (may contain sensitive data)
- Async task updates (may accumulate PII over long-running background work)
- Streaming events from `stream.subagents` (visible to frontend)
- Child traces in LangSmith (separate trace per async child)

### 4.4 Circuit Breaker for Delegation

The product does not ship a delegation circuit breaker. Put one in the worker wrapper:

| Trip Condition | Threshold | Fallback |
|---------------|-----------|----------|
| Child model 429/5xx | Consecutive >= 5 | Parent-only (no delegation) |
| Child recursion error | 3 in window | Simpler task decomposition |
| Async child unresponsive | Timeout + 3 check failures | Cancel + parent-only |
| MCP gateway 5xx on child | Error-rate window | Fail closed on egress tools |

**Fallback chain:** Delegated graph -> parent-only (degraded) -> deterministic refuse. Never: child fails -> skip MCP gateway. Never: child fails -> auto-approve HITL.

---

## 5. Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| Child runs at recursion_limit=25 | Parent config not forwarded (historical bug #1698) | `CancelledError` or `GraphRecursionError` at step 25 | Pin current `deepagents`; verify parent bind 9,999 propagates |
| Compiled/async child skips HITL | No inherit of `interrupt_on` from parent | Dangerous tool calls execute without pause | Wire HITL inside the child graph; restate parent gates |
| Interpreter `task()` bypasses parent gates | `eval` creates dynamic subagent outside `interrupt_on` dispatch | QuickJS `task()` runs ungoverned | Gate `eval`; `interrupt_on={"task": True}` doesn't catch JS `task()` |
| Declarative spec accidentally drops parent HITL | `interrupt_on` on spec **replaces** (not merges) parent rules | Child `write_file` runs without pause | Explicitly include parent gates when setting spec-level HITL |
| Async immediate-poll anti-pattern | `start_async_task` followed immediately by `check_async_task` loop | Wasted model calls; defeats async purpose | Prompt engineering; do not poll immediately |
| Over-delegation overhead | Every minor task becomes a subagent | Token cost increases 1.8x per unnecessary child | Delegate only when context isolation justifies the prefix overhead |
| Custom subagent missing skills | Assumption that all subagents inherit parent skills | Subagent lacks expected capabilities | Only GP inherits; custom needs own `skills=` |
| `recursion_limit: 10000` is a no-op | Frontend sets 10000 which `merge_configs` may drop (sentinel) | Children fall back to default 25 | Bind 9,999 (the SDK does this); never use 10,000 |
| TodoListMiddleware token waste | Added expecting accuracy improvement | ~15-30% more tokens, no accuracy gain | Use only for UX visibility, not accuracy |
| Middleware not inherited by children | Assumption that RetryMiddleware or PIIMiddleware propagates | Child has no retry or PII redaction | Middleware does NOT inherit; configure per-child if needed |
| GP subagent runs when not wanted | Auto-added by default | Unexpected `task()` tool availability | Both profile opt-out AND no sync specs required to disable |
| `ToolCallLimitMiddleware` on task not task-calls | Limit applied to `task` tool itself, not calls inside child | Parent can only delegate N times total | Set limit on child's model calls instead |

---

## 6. Architectural System Design Scenarios

### Scenario A -- Legal Contract Analysis with Parallel Chunk Analysts

**Problem.** A legal services firm processes 200 contracts daily, each 40-80 pages. Analysis requires extracting key clauses, identifying risks, and comparing against standard templates. Current process takes 2 hours per contract manually. Target: 15-minute automated analysis with human review of flagged items.

**Architecture (recommended: supervisor + parallel chunk analysts):**

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Orchestrator Agent                                              │
  │    tools: [read_file, search_templates]                         │
  │    subagents:                                                    │
  │      - SubAgent("clause-extractor", tools=[read_file])          │
  │      - SubAgent("risk-analyzer", tools=[read_file, search_db])  │
  │      - SubAgent("template-comparator", tools=[read_file])       │
  │    interrupt_on: {"send_report": True}  # HITL on final report  │
  │    permissions: deny /client-data/** except workspace           │
  └──────────────────────────────┬───────────────────────────────────┘
                                 │
        ┌────────────────────────┼──────────────────────┐
        ▼                        ▼                      ▼
  ┌─────────────┐       ┌─────────────┐       ┌─────────────────┐
  │ Clause       │       │ Risk         │       │ Template        │
  │ Extractor    │       │ Analyzer     │       │ Comparator      │
  │ (parallel    │       │ (parallel    │       │ (sequential     │
  │  per section)│       │  per section)│       │  after clauses) │
  └─────────────┘       └─────────────┘       └─────────────────┘
```

**Trade-off matrix:**

| Axis | Parallel subagents (recommended) | Single agent, sequential | No delegation |
|------|----------------------------------|-------------------------|---------------|
| **Cost** | 3 children x $0.18 = +$0.54/contract; $223 parent; total ~$0.77 | $0.22 but may hit summarization | $0.22 but 80-page = context overflow |
| **Latency** | ~16s parallel (wall-clock of slowest child) | ~48s sequential (3 x 16s) | Fails on large contracts |
| **Quality** | Each child gets full context window for its section | Summarization may lose clause details | Cannot process full contract |
| **Security** | Per-child permissions; HITL on final report | Same | Same |

### Scenario B -- Customer Support with Async Background Research

**Problem.** A SaaS support system handles 12,000 tickets daily. For complex tickets, the agent needs to search knowledge bases, check account history, and sometimes run diagnostic scripts -- work that takes 2-5 minutes. The agent should continue chatting with the user while background research completes.

**Architecture (recommended: sync for fast queries + async for research):**

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Support Agent (supervisor)                                      │
  │    Sync subagents:                                               │
  │      - SubAgent("quick-lookup", tools=[search_kb])  # <5s       │
  │    Async subagents:                                              │
  │      - AsyncSubAgent("deep-research", url=None)     # ASGI      │
  │        tools: [search_kb, check_account, run_diag]               │
  │    Worker pool: 1 supervisor + 3 async = 4 slots                │
  │    interrupt_on: {"modify_account": True}                        │
  └──────────────────────────────┬───────────────────────────────────┘
                                 │
        ┌────────────────────────┼─────────────┐
        ▼                        ▼             ▼
  ┌──────────┐          ┌─────────────┐  ┌──────────┐
  │ Quick    │          │ Deep        │  │ User     │
  │ Lookup   │          │ Research    │  │ Chat     │
  │ (sync)   │          │ (async,     │  │ continues│
  │ <5s      │          │  2-5 min)   │  │ while    │
  └──────────┘          └─────────────┘  │ research │
                                          │ runs     │
                                          └──────────┘
```

**Trade-off matrix:**

| Axis | Sync + async hybrid (recommended) | All sync | All async |
|------|----------------------------------|----------|-----------|
| **Cost** | Quick lookups: $0.04; deep research: $0.18; total ~$0.26/ticket | Same model cost but user waits | Higher -- every query goes through async overhead |
| **Latency** | User sees quick answer in <5s; deep results arrive in 2-5 min | User waits 2-5 min for everything | Minimum async overhead even for simple lookups |
| **UX** | Agent chats naturally while research happens in background | Agent goes silent during long searches | Good for complex; overhead for simple |
| **Worker slots** | 4 per supervisor (1 + 3 async) | 1 | N + 1 per ticket |

---

## Interview Q&A

**Q1. What is delegation in Deep Agents, and what is the biggest win?**
Delegation turns a monolithic agent into a coordinator-plus-workers system. The biggest win is not parallelism -- it is context quarantine. Each child agent gets a fresh context window, processes heavy work independently, and returns only a compact ToolMessage result to the parent. This keeps the parent's context clean and prevents summarization from destroying important information. A 50-page PDF analysis stays in the child's 200k window; the parent sees only a 500-token summary.

**Q2. Walk me through the five subagent forms.**
General-purpose (GP) is auto-added by default and handles any `task()` call that doesn't match a named spec -- it inherits parent tools, model, and skills. Declarative SubAgent is a named specialization with its own instructions, tools, and optional overrides. CompiledSubAgent is a pre-compiled LangGraph graph used as a subagent for maximum control. AsyncSubAgent launches non-blocking background work on its own thread with five lifecycle tools (start, check, update, cancel, list). Dynamic subagents are created at runtime by the QuickJS interpreter via `eval`. Each form has different inheritance rules, which is the interview trap.

**Q3. What does the inheritance matrix look like?**
Tools and model inherit for GP and declarative (unless overridden). System_prompt and middleware do NOT inherit. Skills only inherit for GP -- custom subagents need their own `skills=`. HITL and permissions inherit for GP and declarative, but if a declarative spec sets them, it **replaces entirely** -- no merge. Compiled and async subagents inherit nothing; you must wire everything explicitly. The interpreter hole: `eval` creating a `task()` bypasses parent `interrupt_on`.

**Q4. Why is 9,999 the recursion limit and not 10,000?**
`create_deep_agent` binds `recursion_limit: 9,999` because LangGraph `merge_configs` has historically dropped `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10,000). Frontend examples still pass 10,000 which can be a no-op, causing children to fall back to the bare LangGraph default of 25 super-steps. Historical bug #1698: parent config was not forwarded to children at all, causing `CancelledError` at step 25. The fix propagates the parent's 9,999 binding.

**Q5. Give me the cost math for delegation.**
Using the standard Sonnet 4.6 10-call cached mix: parent alone is $0.2229/run ($223/1k). Adding one GP child with 8 calls adds approximately $0.18 ($0.4032/run, $403/1k). Each additional child adds another ~$0.18. So parent + 3 children costs about $0.76/run ($764/1k). The context quarantine is not free, but it is cheaper than context overflow, extra summarization calls, or wrong answers from a bloated parent window. Anthropic research shows multi-agent achieves 90.2% success with 4x chat tokens. The sweet spot is 3-5 parallel subagents before diminishing returns.

**Q6. What does TodoListMiddleware actually do?**
It adds a `write_todos` tool that creates a structured plan with pending/in_progress/completed statuses, visible via `stream.values.todos`. It was made opt-in since v0.7. The critical thing: it has **no measured improvement** in task accuracy and adds **15-30% more tokens** for plan generation and status updates. I use it only when the UX requires visible task progress. It is a planning surface, not an execution mechanism -- the model still needs `task()` calls to actually delegate work.

**Q7. Explain the interpreter hole for HITL.**
When the QuickJS interpreter runs `eval()`, it can create a `task()` call inside JavaScript that bypasses the parent's `interrupt_on` dispatch. Setting `interrupt_on={"task": True}` on the parent does NOT catch JavaScript `task()` calls. The mitigation is to gate `eval` itself -- either exclude it from tools, require HITL approval for `eval`, or restrict interpreter capabilities. PTC (parallel tool calls) from the interpreter can also bypass HITL.

**Q8. When do you use async vs sync delegation?**
Sync (`task()`) for work where the parent needs the result before continuing -- research that feeds into the next step, analysis that the parent synthesizes, any dependency chain. Async (`AsyncSubAgent`) for work the user doesn't need to wait for -- background monitoring, long-running data processing, parallel independent investigations where the user should keep chatting. The anti-pattern is immediate polling: launching async and then looping `check_async_task` defeats the purpose. Worker-pool sizing: 1 supervisor + N async = N+1 slots.

**Q9. How do you handle subagent failures?**
Child failures propagate as error strings in the ToolMessage back to the parent. The parent model decides: retry with a simpler task, try a different approach, or report failure to the user. For async children, `check_async_task` shows the failure status. I put circuit breakers in the worker wrapper: if a child model returns 429/5xx consecutively >= 5 times, I fall back to parent-only mode (degraded, no delegation). I never fail open to skipping the MCP gateway or auto-approving HITL on failure.

**Q10. What is the difference between subagents and A2A?**
Subagents are in-process child graphs with context quarantine and a single ToolMessage handoff -- they are the `task` tool. A2A is Google's (now Linux Foundation) network protocol for cross-deployment agent communication. Use subagents for private decomposition within one trust domain (RAG chunk analysts, coding subtasks). Use A2A when the peer is another team, another framework, or another compliance zone. The enterprise default is hybrid: subagents inside, A2A only across zone boundaries.

**Q11. How does streaming work for delegated work?**
For sync subagents, use `stream.subagents` -- it provides one handle per `task()` delegation with `name = subagent_type`. Agent Server aliases this as `thread.subagents`. Do NOT consume raw subgraph namespaces. For async work, each child run appears as its own trace linked by thread ID in LangSmith. Event streaming v3 (since deepagents >= 0.6) with typed projections (messages, tool_calls, values, output) is the current API.

**Q12. Walk me through the delegation security model.**
Three layers. First, HITL inheritance: GP and declarative inherit parent `interrupt_on`, but compiled and async do NOT -- you must wire gates inside each child. Second, permissions: same pattern as HITL, with the added risk that declarative specs **replace** (not merge) parent permissions. Third, MCP gateway: `permissions=` covers only built-in FS tools. Children calling MCP tools still need the gateway PEP with OAuth 2.1, RFC 8707 audience, no token passthrough, and hash-pinned tool schemas. The interpreter hole (eval/PTC bypassing HITL) and the double-execute risk (resume restarts nodes from line 1, so tools must be idempotent) are the two gaps I always call out.

---

## Key Numbers to Memorize

### Subagent Forms & Inheritance
| Number | What |
|--------|------|
| **5** | Subagent forms: GP, declarative, compiled, async, dynamic |
| **GP** | Auto-added by default; inherits tools/model/skills/HITL/permissions |
| **Declarative** | Inherits tools/model; **replaces** system_prompt/HITL/permissions if set |
| **Compiled/Async** | Inherits **nothing** -- must wire everything |
| **PR #2334** | Declarative specs inherit parent `interrupt_on` (not only GP) |

### Recursion & Caps
| Number | What |
|--------|------|
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **10,000** | Frontend copy; historically dropped by `merge_configs` |
| **25** | Bare LangGraph historical default (children without propagated config) |
| **#1698** | Bug: parent config not forwarded to children |

### Async & Planning
| Number | What |
|--------|------|
| **5** | Async lifecycle tools: start/check/update/cancel/list |
| **N+1** | Worker slots: 1 supervisor + N async subagents |
| **>=0.5.0** | AsyncSubAgent preview |
| **>=0.7** | TodoListMiddleware opt-in (no accuracy improvement, higher tokens) |

### Cost [inferred]
| Number | What |
|--------|------|
| **$223 / 1k** | Parent only, 10-call cached |
| **$403 / 1k** | Parent + 1 GP child (8 calls) |
| **$584 / 1k** | Parent + 2 GP children |
| **~$0.18/child/run** | GP child marginal cost (8-call, cached) |
| **90.2%** | Multi-agent task success vs single Opus 4 (Anthropic) |
| **4x / 15x** | Chat tokens / total tokens for multi-agent vs single |
| **3-5** | Optimal parallel subagents (diminishing returns beyond) |

### Latency [inferred policy]
| Number | What |
|--------|------|
| **16,000 / 64,000 / 160,000 ms** | Single 8-call child p50/p95/p99 |
| **2,000 / 8,000 / 20,000 ms** | One ReAct cycle (model + tool) |
| **30,000 / 180,000 / 600,000 ms** | HITL clock p50/p95/p99 |

---

## Quick Reference

```
DELEGATION = CONTEXT QUARANTINE
  Child gets fresh window -> processes heavy work -> returns compact ToolMessage
  Parent context stays clean -> avoids summarization pressure

INHERITANCE RULES (memorize this table)
  GP:          inherits tools/model/skills/HITL/permissions
  Declarative: inherits tools/model; REPLACES prompt/HITL/permissions if set
  Compiled:    inherits NOTHING -- wire everything
  Async:       inherits NOTHING -- own deployment
  Dynamic:     interpreter hole -- eval() bypasses parent interrupt_on

RECURSION_LIMIT
  Bind 9,999 (not 10,000 -- sentinel dodge)
  Counts super-steps, NOT model calls or subagents
  Always add ModelCallLimitMiddleware / ToolCallLimitMiddleware

ASYNC SUBAGENTS
  5 tools: start / check / update / cancel / list
  Worker slots: 1 supervisor + N async = N+1
  Anti-pattern: immediate poll after start

TASK vs A2A
  task() = in-process child graph (context quarantine)
  A2A = network protocol between deployments
  Default: subagents inside trust domain, A2A across zones
```
