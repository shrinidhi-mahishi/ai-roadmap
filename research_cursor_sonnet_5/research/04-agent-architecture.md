# Research: Agent Architecture — ReAct, Loops, Planning, State, Workflows

**Date researched**: 2026-08-21
**Sources consulted**: 66

---

## 1. System Topology & Mechanics

### 1.1 The ReAct Pattern

**Origin.** ReAct ("Synergizing Reasoning and Acting in Language Models," Yao et al., 2022/2023) interleaves verbal reasoning traces with task-specific actions rather than treating reasoning (chain-of-thought) and acting (action-plan generation) as separate topics. The loop is Thought → Action → Observation, repeated until termination. Reasoning traces induce, track, and update action plans and handle exceptions ("reason to act"); actions interface with external sources such as Wikipedia to pull fresh information into reasoning ("act to reason"). On HotpotQA/Fever, ReAct overcomes hallucination and error-propagation issues common in pure chain-of-thought by grounding reasoning in real observations. On ALFWorld and WebShop, it beat imitation/RL baselines by 34 and 10 absolute points respectively, using only 1-2 in-context examples ([ReAct paper](https://react-lm.github.io/); [Google Research Blog](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)).

**Mechanics.** A ReAct prompt/loop is not just "think then act" — reasoning traces are sparse and appear only where most useful (exception handling, plan revision), not on every token. This keeps token overhead down relative to always-verbose CoT ([IBM ReAct](https://www.ibm.com/think/topics/react-agent)).

### 1.2 Agent Execution Loop — Reference Implementations

**OpenAI Agents SDK.** The `Runner` loop: (1) call the current agent's model with the prepared input; (2) if output is classified as final (matches `agent.output_type`, no tool calls), terminate; (3) if the model requests a handoff, swap the current agent and re-loop; (4) if the model emits tool calls, execute them, append results, and re-loop; (5) if `max_turns` is exceeded, raise `MaxTurnsExceeded` (disable via `max_turns=None`). One `Runner.run()` call is one logical conversation turn but may involve many LLM calls across handoffs/tools. Persistence options: `result.history` (client-managed), `session` (SDK + your storage), `conversationId` / `previousResponseId` (OpenAI-managed) — pick one per conversation to avoid duplicated context. Interrupted runs (e.g., pending tool approval) serialize to a `RunState` via `to_state()` for exact resumption ([OpenAI Agents SDK Running Agents](https://openai.github.io/openai-agents-python/running_agents/); [Runner reference](https://openai.github.io/openai-agents-python/ref/run/); [DeepWiki Runner Flow](https://deepwiki.com/openai/openai-agents-python/3.2-runner-and-execution-flow)).

**Google ADK (Agent Development Kit).** Built on an event-driven `Runner` ↔ "Execution Logic" loop using an ask-yield pattern. The Runner appends the user message, asks the agent to run, and each time the agent yields an `Event` (partial model output, tool call, tool result), the Runner persists it via `SessionService`/`ArtifactService`/`MemoryService`, applies `state_delta`/`artifact_delta`, and forwards it upstream. Because every event (with its delta) is appended to an append-only session log, durability is "free" — no separate checkpoint mechanism; a resumed run just replays the event log. ADK 2.0 (2026) added native `Workflow` graph/DAG support, separating deterministic execution routing from LLM-driven reasoning, so teams can compose deterministic tool/HITL steps with open-ended agent steps in one graph ([ADK Event Loop docs](https://github.com/google/adk-docs/blob/5331a07f/docs/runtime/event-loop.md); [New Stack ADK Tour](https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/); [ADK 2.0 announcement](https://developers.googleblog.com/why-we-built-adk-20/)).

**Claude Code (Anthropic).** The entire agent loop — streaming, tool execution, error recovery, context compaction — is one ~1,730-line asynchronous generator (`query()` in `query.ts`) implementing a ReAct-pattern `while(true)`: assemble context → call model → dispatch tools → check permissions → execute → repeat. The async-generator abstraction gives streaming without buffering, backpressure without manual flow control, and one implementation serving the CLI, SDK, and sub-agents simultaneously. Per the VILA-Lab reverse-engineering project, only ~1.6% of the codebase is AI decision logic; the other 98.4% is deterministic infrastructure (permission gates, context management, tool routing, recovery). A 9-step per-turn pipeline: settings resolution → state init → context assembly → 5 pre-model context-compaction shapers → model call → tool dispatch → permission gate → tool execution → stop-condition check ([Inside Claude Code](https://y-agent.github.io/inside-claude-code/02-agent-loop-query-engine.html); [Claude Code From Source](https://claude-code-from-source.com/ch05-agent-loop/); [Dive into Claude Code](https://github.com/VILA-Lab/Dive-into-Claude-Code)).

### 1.3 Graph-Based Orchestration (LangGraph)

**Three primitives.** *State* — a shared data structure (typically `TypedDict` or Pydantic model) that is the schema for every node/edge, updated via per-key *reducer* functions (default: overwrite; e.g., `add_messages` appends). *Nodes* — sync/async Python functions receiving the full state and returning a partial update. *Edges* — define control flow: normal edges (unconditional `A → B`), conditional edges (a routing function inspects state and returns the next node name from a mapping), and the `Send` API for map-reduce fan-out. A node with multiple outgoing edges executes all destinations **in parallel** as part of the next "superstep" (message-passing/Pregel-inspired model) ([LangGraph graph-api docs](https://docs.langchain.com/oss/python/langgraph/graph-api.md); [LangChain blog](https://www.langchain.com/blog/langgraph); [use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api)).

**Compilation.** `StateGraph(Schema)` → `add_node` → `add_edge`/`add_conditional_edges` → `.compile()` produces a runnable graph. Loops are first-class: a conditional edge can route back to an earlier node (e.g., `validator → analyst` on low quality), making cyclic graphs the natural representation of iterative agent refinement — something a plain DAG cannot express.

### 1.4 Workflows vs. Agents (Anthropic's Framing)

Anthropic draws a sharp architectural line: **workflows** are systems where LLMs and tools are orchestrated through *predefined code paths* (predictable, bounded cost/latency, reproducible); **agents** are systems where the LLM *dynamically directs its own process and tool use* (flexible, but "trades latency and cost for better task performance" and enables compounding errors). The recommended agent loop: (1) begin with user input; (2) the agent plans and executes autonomously; (3) it requests human feedback at checkpoints; (4) at each step it obtains "ground truth" from the environment (tool results, code execution) to assess progress; (5) it terminates on completion or a stop condition (e.g., max iterations). Anthropic explicitly recommends starting with the simplest workflow pattern (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer) and reaching for a full autonomous agent only when the step count/order is genuinely unknowable in advance — the canonical example being a coding agent ([Anthropic Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents); [Anthropic eBook](https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf)).

### 1.5 Planning Architectures

**Plan-and-Execute (P-t-E).** Decouples strategic planning from tactical execution via two components: a **Planner** (LLM) that decomposes a goal into an ordered multi-step plan, and **Executor(s)** that map each step to tool calls. After execution, the agent re-invokes with a re-planning prompt to decide whether to finish or generate a follow-up plan. Architectural advantage over ReAct: the (expensive) planner LLM is not re-queried for every tool invocation, improving cost-efficiency and predictability; disadvantage is rigidity if an early step's premise turns out false, requiring an explicit re-planning loop ([LangChain Plan-and-Execute](https://www.langchain.com/blog/planning-agents); [arXiv 2509.08646 Secure P-t-E](https://arxiv.org/pdf/2509.08646)).

**ReWOO (Reasoning WithOut Observation).** Decouples reasoning from observations: a Planner generates the full task plan upfront without waiting for intermediate results, a Worker executes tasks, and a Solver synthesizes the final answer — avoiding the token cost of re-reading every intermediate observation into the planner's context ([UTurn Data Solutions](https://www.uturndata.com/insights/choosing-the-right-agentic-ai-pattern-for-genai-implementation)).

**LLMCompiler.** Planner streams a **DAG of tasks** (each with tool, arguments, dependency list); tasks with satisfied dependencies execute in parallel rather than serially. Claims 3.6x speedup over sequential plan-and-execute/ReWOO by exploiting the fact that most tool calls (search, sub-LLM calls) are I/O-bound and independent ([LangChain Plan-and-Execute](https://www.langchain.com/blog/planning-agents)).

**Plan-and-Act (2025/2026, MLR/arXiv 2503.09572).** A trained (not just prompted) Planner + Executor pair: the Planner produces structured high-level plans; the Executor translates them into environment actions; a synthetic-data generation method (annotating ground-truth trajectories with feasible plans) trains the Planner without manual annotation. Achieves SOTA 57.58% on WebArena-Lite and 81.36% on WebVoyager (text-only), demonstrating that explicit, trainable planning outperforms single-model ReAct-style mapping for long-horizon web tasks ([Plan-and-Act paper](https://arxiv.org/html/2503.09572v2)).

**Security framing.** Plan-then-Execute's separation of planning from execution establishes *control-flow integrity*, making it inherently more resilient to indirect prompt injection than reactive (ReAct) loops, since a compromised tool-output cannot as easily redirect the overall plan mid-stream. Recommended defense-in-depth complements: least-privilege, task-scoped tool access, sandboxed code execution ([arXiv 2509.08646](https://arxiv.org/pdf/2509.08646)).

### 1.6 Loop Termination Conditions — the 2026 Consensus Pattern

Industry practice has moved decisively away from "the model decides when it's done" toward **composable, deterministic, pre-call termination contracts**:

- **Composable Termination Conditions**: independent primitives (`MaxMessages`, `TokenBudget`, `TextMention`, `FunctionCall`, `Handoff`, `Timeout`, `ExternalSignal`, `Cancellation`) evaluated by a single supervisor each step, combined via AND/OR, with the tripped condition logged for postmortem ([Agent Patterns Catalog](https://www.agentpatternscatalog.org/patterns/composable-termination-conditions/)).
- **Stop Hook**: a programmatic predicate run after every step returning `continue | stop-success | stop-failure`, based on target-reached, step-budget, error-class, or stagnation signals — explicitly forbidding any other loop-exit path ([Stop Hook pattern](https://www.agentpatternscatalog.org/patterns/stop-hook/)).
- **Bounded Agentic Loop**: wraps `send → check stop_reason → run tool → feed result back → repeat` in hard budgets (turns, tokens, cost) + a wall-clock timeout + progress detection + an out-of-band kill switch; the budget guard sits specifically on the "run another tool" edge, where autonomy compounds ([aiarch.dev Bounded Loop](https://aiarch.dev/patterns/bounded-agentic-loop)).
- **Stagnation/no-progress detection**: compare state across a window (e.g., last 4+ steps) and halt on identical/oscillating actions — this is the condition every pure budget check catches only *after* wasting the budget ([DEV Community Stopping Conditions](https://dev.to/multigrid/stopping-conditions-preventing-infinite-agent-loops-1377)).
- **Practical calibration**: run representative tasks with a generous budget, take the step count at the 95th percentile of *successful* runs, and set the hard cap slightly above it — a cap set by guesswork either kills good runs or fails to catch bad ones ([DEV Community](https://dev.to/multigrid/stopping-conditions-preventing-infinite-agent-loops-1377)).

### 1.7 Sync / Async / Streaming Execution & Control-Plane / Data-Plane Separation

Modern agent runtimes consistently separate a **control plane** (identity, policy/RBAC, budget enforcement, routing, audit logging — never executes work directly) from a **data/execution plane** (runs the agent loop, tool calls, streams events). AWS's own multi-tenant agentic-AI prescriptive guidance recommends this split explicitly, mirroring the long-standing SaaS control-plane/application-plane pattern ([AWS control-plane guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-multitenant/employing-control-planes-in-agentic-environments.html); [a5c-cell](https://github.com/j3brns/a5c-cell); [agynio platform](https://github.com/agynio/platform/blob/main/docs/operate/architecture.md)).

Execution modes: **synchronous** (caller blocks for the full result — short, interactive tasks); **asynchronous** (caller gets a job/run ID immediately, polls or is notified — long-running, tool-heavy, or HITL-gated tasks); **streaming** (agent yields discrete `Event`s — partial tokens, tool-call requests, tool results, state deltas — that a Runner persists and forwards incrementally, as in both the OpenAI Agents SDK and Google ADK). Streaming is not merely a UX nicety: in Claude Code and ADK it is architecturally load-bearing, since callbacks, state-delta application, and dev-tooling traces are all driven off the event stream rather than a single terminal return value ([ADK Runtime & Events](https://pratikdhanave.com/blog/posts/adk-10-runtime-and-events.html)).

### 1.8 Typed Agent State Schemas & State-Machine Design

Best-practice state design splits the schema into three layers: **(1) Input** — user-provided data, set once, never mutated; **(2) Pipeline state** — accumulated results (intermediate findings, tool outputs) written incrementally by nodes; **(3) Control/debug state** — iteration counters, routing decisions, error logs, trace metadata, used both for loop-safety (e.g., `analyze_attempts` counter preventing infinite loops) and observability. The schema — defined with Pydantic, Zod, or TypeBox — is treated as a versioned, immutable *data contract*; state transitions are named and validated rather than free-text status fields, and routing between states uses deterministic Python/TypeScript functions, not further LLM calls, so every transition is testable and reproducible ([Agents First: Typed State](https://agentsfirst.dev/principles/typed-state/); [Medium — control flow](https://medium.com/data-science-collective/your-agent-isnt-broken-your-control-flow-is-a10142d79e40); [Chanl: State Machines](https://www.channel.tel/blog/ai-agent-state-machines-deterministic-production)).

---

## 2. Token Economics & NFR Metrics

### 2.1 Cost Per Completed Task, Not Per Token

The relevant 2026 unit of agent economics is **cost per completed task** (including cleanup/retry cost for failed trajectories), not cost per token or even cost per API call. Gartner (March 2026) estimates agentic workflows consume **5-30x more tokens per task** than a single chatbot query, because a task triggers 10-20 model calls (reasoning, tool calls, verification, self-correction) instead of one ([Cockroach Labs](https://www.cockroachlabs.com/blog/agentic-ai-costs-at-scale/)). A preprint analyzing 8 frontier-LLM trajectories on SWE-bench Verified (arXiv:2604.22750, co-authored by Stanford's Erik Brynjolfsson and MIT's Alex Pentland) found agentic coding tasks consume **up to 1,000x more tokens** than simple code chat, with **up to 30x run-to-run variance**, and input tokens dominating the bill ([DoiT cost-per-task](https://www.doit.com/blog/cost-per-task-vs-cost-per-token)).

**Reliability-adjusted cost matters more than list price.** In the same study, per-token price and per-attempt cost favored OpenAI, but the *success-and-reliability-adjusted cost per correct task* favored Anthropic by nearly 2x, driven by the "cleanup" term for failed trajectories. On tau-bench, Opus 4.8 held 56% task success across 8 consecutive runs (retail) vs. GPT-5.5's 41%; 34% vs. 22% (airline) — consistency is what buys safe unattended operation. **Decision rule**: if cleanup cost exceeds ~5-10x a single attempt, pay the premium for the more reliable model; otherwise the token-efficient model wins ([DoiT](https://www.doit.com/blog/cost-per-task-vs-cost-per-token)).

### 2.2 Quadratic Token Growth in Naive Loops

Because LLM APIs bill the entire conversation history on every call, naive agent loops incur **O(N²)** token cost: turn *k* re-sends all *k-1* prior turns. A 20-step loop can consume >10x the tokens a naive linear per-step estimate would suggest. Mitigations: scope-limited context per step, state resets between phases, coordinator-specialist patterns where sub-agents get fresh, narrow context ([Augment Code](https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints)). Recommended monitoring metrics and alert thresholds:

| Metric | Alert Threshold |
|---|---|
| Token-per-task | >2x established baseline |
| Cost-per-completion | Daily spend exceeds historical baseline |
| Loop iterations per task | >2x baseline (signals retry loops/plan staleness) |
| Context utilization ratio | >85% of max context window |
| Per-subagent cost share | Orchestrator consuming >10-15% of total cost |

([Augment Code](https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints))

### 2.3 Prompt Caching Economics for Loops

Because an agent's system prompt, tool definitions, and few-shot examples are byte-identical every turn, marking that stable prefix cacheable is the single highest-leverage lever: cache reads cost ~0.1x fresh input-token price (a ~90% discount), though cache *writes* cost a ~1.25x premium and entries expire on a short TTL — caching wins precisely for high-frequency, same-prefix loops. Caching fails to help on: the first turn of every conversation, whenever any tool is added/removed/updated (invalidates the whole cached prefix), and short sessions (<~5 turns) that don't amortize the write premium ([dreaming.press unit-economics](https://dreaming.press/posts/what-an-ai-agent-costs-per-task-unit-economics-worksheet.html); [tool-use research file, §2.3](../research/03-tool-use.md)).

**Output-to-input tax.** Output tokens typically cost 3-5x input tokens. An agent that narrates its reasoning verbosely, echoes whole files, or returns unbounded tool results has a bill dominated by what it *writes*. Constraining output format (structured diffs over full-file echoes, capped list lengths) often saves more than switching models ([dreaming.press](https://dreaming.press/posts/what-an-ai-agent-costs-per-task-unit-economics-worksheet.html)).

**Full per-task cost formula**: for each turn, `uncached_input × input_rate × (1 − cache_hit_rate) + cached_input × input_rate × 0.1 + output_tokens × output_rate + thinking_tokens × output_rate`; sum across all turns in the run, divide by 1,000,000 for the per-task dollar figure. The provider's pricing-page number describes exactly one uncached, one-shot call — an agent loop is many turns over a growing transcript ([dreaming.press](https://dreaming.press/posts/what-an-ai-agent-costs-per-task-unit-economics-worksheet.html)).

### 2.4 Latency SLAs — Why P50 Lies for Multi-Step Loops

Task completion time for a multi-step agent is the **sum across every sequential call in the chain**, and tail latency compounds multiplicatively, not additively. Classic distributed-systems tail-amplification (P99/P50 ratio of 5-10x) is *worse* for LLM-backed steps, which routinely show **20-50x** P99/P50 ratios due to streaming/decode variance, provider-side queueing, and variable output length ([tianpan.co Latency Budgets](https://tianpan.co/blog/2026-05-01-latency-budgets-multi-step-agents)).

**Dean & Barroso tail-at-scale, applied to agents**: for a 5-hop chain (plan → retrieve → tool call → tool call → synthesize) where each hop independently has a 5% chance of hitting its slow threshold, the probability *all five* stay fast is `0.95^5 ≈ 0.77` — meaning **23% of requests eat at least one tail event**, even though each individual hop looks fine 95% of the time ([tianpan.co Latency Budget Nobody Allocated](https://tianpan.co/blog/2026-07-05-the-latency-budget-nobody-allocated-across-agent-hops)).

**Measured example** (single-call benchmarks, concurrency 1): Llama 4 Maverick — TTFT p50 300ms, total-completion p50 3,249ms, p99 7,458ms (p99:p50 ratio 2.30x). GPT-OSS 120B — TTFT p50 1,202ms, total-completion p50 1,933ms, p99 2,932ms (ratio 1.52x) — illustrating that a model winning on TTFT can still lose on a 10-call chained task by ~28% ([DigitalOcean p50 vs p99](https://www.digitalocean.com/community/tutorials/p50-vs-p99-latency-llm-inference)).

**SLO design**: (1) pick the end-to-end percentile the user actually feels — P95 for most interactive UX, P99 for stricter guarantees; (2) split time-to-first-token (usually target <2s for interactive surfaces) from end-to-end wall-clock (may reasonably sit at 45s P95 for ticket triage, 8 minutes for nightly reconciliation); (3) decompose the end-to-end budget into **per-hop budgets** worked backward from the target (e.g., an 8s P95 might break down as 300ms guardrails/routing, 1.5s retrieval, 4s generation, 1.5s tool execution, 700ms slack); (4) track per-hop latency histograms, not just end-to-end, because the end-to-end P99 tells you *that* you have a tail but not *where* it lives ([isimplifyme Agent SLOs](https://isimplifyme.com/blog/agent-slos); [tianpan.co](https://tianpan.co/blog/2026-07-05-the-latency-budget-nobody-allocated-across-agent-hops)). A hard timeout below the point where partial work stops being useful should route to **escalation**, not a bare error — a run that gives up at 90s with a partial draft and its retrieved context beats one that burns 4 minutes and returns nothing ([isimplifyme](https://isimplifyme.com/blog/agent-slos)).

### 2.5 Capacity Planning — Concurrency, Not QPS

Because agent-loop latency is measured in tens of seconds (not the ~200ms of a classic web request), the correct capacity unit is **concurrency via Little's Law** (`L = λ × W`), not requests-per-second. Example: 10 requests/sec × 90s average residency = **900 concurrent in-flight runs** — the number that must be provisioned against provider rate limits, vector DB connections, and internal API capacity ([tianpan.co Capacity Planning](https://tianpan.co/blog/2026-06-04-capacity-planning-for-agents-why-concurrency-not-qps-is-your-real-unit)).

**Fan-out multiplies the real load**: if each in-flight run holds ~3 concurrent tool calls on average, the 900 front-door concurrent runs become **2,700 simultaneous callers** at the downstream-tool tier — a multiplier invisible on any request-rate graph, and the reason capacity planning that stops at the front door systematically undercounts real load ([tianpan.co](https://tianpan.co/blog/2026-06-04-capacity-planning-for-agents-why-concurrency-not-qps-is-your-real-unit)).

**Binding-limit formula**: `Sustainable RPM = min(provider RPM limit, provider TPM limit ÷ avg tokens per request)`; `Required concurrency = ceil(sustainable throughput/sec × avg request duration)`. Apply 30-50% headroom for bursts/retries; monitor RPM/TPM utilization as a % of quota and alert at 70-80%, exactly as you would a database connection pool ([tianpan.co Rate Limit Product Decision](https://tianpan.co/blog/2026-05-17-rate-limit-became-product-decision); [Agent Native rate-limit calculator](https://www.agentnative.dev/tools/rate-limit-calculator)). Real-world constraint example: Salesforce's Agentforce sales-recommendation pipeline hit a **300 requests/minute** platform ceiling that made naive per-opportunity invocation infeasible at their required scale (hundreds of thousands of opportunities in a 9-hour nightly window, up to 27,000 input tokens per invocation), forcing a message-queue-driven architecture that decoupled orchestration from execution ([Enggist/Salesforce case study](https://enggist.vercel.app/post/f9408c82-7ceb-45a1-bf60-828de026bbd6)).

> ⚠️ No public, verifiable benchmark was found quantifying agent-loop throughput ceilings specifically for LangGraph or Temporal-hosted agent workflows at extreme scale (e.g., >1M concurrent runs); figures above are drawn from single-company case studies and may not generalize. `[inferred]` for cross-framework applicability.

---

## 3. Distributed Resilience & State

### 3.1 Durable Execution for Agent Loops

**Temporal Agent Harness (2026).** Every agent built on the harness is a Temporal Workflow: the agent loop is durable end-to-end — outer loops, tool calls, waiting for human approval, and even model-generated code execution in sandboxes are all durable. Temporal's event-sourced model durably records every Activity call/return in an **Event History**; on crash, the workflow deterministically replays that history to reconstruct in-memory state and resume exactly where it left off, without re-executing completed work or re-paying for already-made LLM calls (LLM calls are cached from first execution on replay) ([Temporal Agent Harness](https://temporal.io/blog/temporal-agent-harness-durable-agent-infrastructure); [niteagent Temporal guide](https://niteagent.com/blog/2026-06-29-durable-ai-agents-temporal-guide/)).

**Checkpoint boundary caveat.** Temporal checkpoints at the **Activity boundary**, not inside an inference call — an in-flight LLM call that was mid-token when the worker died cannot resume mid-token; that Activity retries from scratch and is paid for again. This makes **idempotent tool calls** (via idempotency keys / dedup guards) mandatory for any side-effecting Activity, since a retry that recharges a card or double-books a resource is a correctness bug, not a performance issue ([Temporal Multi-Agent](https://temporal.io/blog/durable-flexible-multi-agent-systems)).

**Human approval as durable wait.** `workflow.wait_condition()` blocks without consuming worker compute; the pending decision lives in Event History (not RAM), so thousands of approvals can wait independently in an open state with zero worker CPU cost, and every decision produces a free audit trail. A 24-hour timeout pattern ensures the workflow doesn't hang forever if a human never responds ([niteagent](https://niteagent.com/blog/2026-06-29-durable-ai-agents-temporal-guide/); [Temporal Multi-Agent](https://temporal.io/blog/durable-flexible-multi-agent-systems)).

**Continue-As-New.** For very long-running agent loops, event history grows unbounded; Temporal's Continue-As-New pattern atomically completes the current run and starts a fresh run under the same workflow ID, carrying forward only essential state — the workflow appears continuous externally while internally resetting its history log ([Zylos Checkpointing Research](https://zylos.ai/research/2026-03-04-ai-agent-workflow-checkpointing-resumability)).

**LangGraph vs. Temporal — scope distinction.** LangGraph's checkpointer (paired with a persistent backend such as PostgreSQL) provides durability *scoped to the graph* — `interrupt()` suspends the graph, and the checkpointer makes that pause durable *if* backed by Postgres/SQLite/Redis (an in-memory checkpointer does not survive a crash). Temporal is general-purpose durable execution that persists the *whole system* — agent loops, external service calls, human waits, and timers — across multiple frameworks simultaneously. The two are complementary, not competing, at different scopes ([Temporal Multi-Agent](https://temporal.io/blog/durable-flexible-multi-agent-systems)).

### 3.2 LangGraph Postgres Checkpointing (Production Pattern)

`PostgresSaver`/`AsyncPostgresSaver` persist checkpoints in three tables (`checkpoints`, `checkpoint_writes`/blobs, `checkpoint_migrations`), keyed by `(thread_id, checkpoint_ns, checkpoint_id)`. Beyond full super-step checkpoints, LangGraph persists **per-node (task) writes** as each node in a super-step finishes — this is what enables "pending writes" recovery: if one node in a super-step fails, the already-successful nodes' writes are durable and are not re-run on resume. `checkpointer.setup()` must be called once to create/migrate tables; connections require `autocommit=True` and `row_factory=dict_row`. The `"sync"` durability mode persists every checkpoint synchronously before the next step starts, trading some latency for maximum durability ([LangGraph checkpointers docs](https://docs.langchain.com/oss/python/langgraph/checkpointers); [LangGraph checkpoints reference](https://reference.langchain.com/python/langgraph/checkpoints)).

### 3.3 Distributed Locking & Race Conditions on Shared Agent State

Concurrent multi-agent systems that touch shared mutable state are full distributed systems and inherit all the classic failure modes: race conditions, ordering violations, split-brain, partial-failure recovery — the presence of an LLM in the loop does not change whether the state layer is correct ([tianpan.co Race Conditions](https://tianpan.co/blog/2026-04-12-race-conditions-in-concurrent-agent-systems)).

**Why classic DB locks fail for agents.** An agent's read-think-write cycle (read state → LLM reasons for 5-15 seconds → write result) is far longer than a typical database lock hold time; holding a pessimistic lock for the duration of an LLM call creates severe contention and connection-pool exhaustion. Distributed locks (Redis Redlock, ZooKeeper) are appropriate only for **short-lived critical sections under ~100ms**; for the 5-15s agent read-think-write cycle, **optimistic concurrency control** is almost always preferable ([ECOA AI Redis Conflicts](https://ecoaai.com/multi-agent-shared-state-problems-redis-orchestration/)).

**Optimistic locking pattern.** Every state record carries a version number; reads capture the version; writes assert `version == captured_version` as a precondition. The first writer to commit succeeds and bumps the version; every subsequent writer with a stale version fails immediately (no silent overwrite) and must re-read + recompute + retry. This is the industry-standard fix for the "two agents both believe they booked the same hotel room" class of bug, which one documented production incident described as a 47-second full outage during which no errors were logged — just two agents silently overwriting each other's writes in a shared Redis hash ([ECOA AI](https://ecoaai.com/multi-agent-shared-state-problems-redis-orchestration/); [tianpan.co Race Conditions](https://tianpan.co/blog/2026-04-12-race-conditions-in-concurrent-agent-systems)).

**Agentic Mutex (semantic lock).** For high-integrity or financial/compliance-sensitive actions, lock a *semantic token* representing the entity/workflow objective (e.g., `account:12345`) at the orchestration layer rather than a literal database row; add a lease TTL so a dead agent cannot hold the lock forever. For complex, hard-to-serialize work (e.g., software engineering agents), avoid shared-state mutexes entirely by isolating each agent in its own ephemeral git branch/container/DB clone, then resolving concurrent work deterministically at a final merge boundary via standard code review ([ninelayer.in Agentic Mutex](https://ninelayer.in/blog/agent-mutex)).

**Formal results (2026 research).** CoAgent (arXiv 2606.15376) benchmarks concurrency-control strategies for multi-agent LLM systems: uncoordinated execution passes only 13% of contended-workload trials; two-phase locking (2PL) deadlocks 0.81 times/trial with minimal speedup (1.04x); optimistic concurrency control (OCC) aborts 0.95 times/trial and is *slower* than serial execution (0.93x) at 1.83x token cost; CoAgent's selective-recovery approach passes all 10 contended workloads within 5% of serial correctness at a 1.4x speedup and near-serial (1.15x) token cost ([CoAgent paper](https://arxiv.org/html/2606.15376)).

### 3.4 Circuit Breakers for Loop-Internal Tool Calls

Standard three-state circuit breaker (CLOSED → OPEN → HALF-OPEN) applied at the tool-dispatch boundary: track per-dependency error rate over a rolling window; when it exceeds a threshold (e.g., 30% error rate over 60s, or 3 failures in 60s for external CRM/payment APIs), open the breaker and return a **structured error** (e.g., `CIRCUIT_OPEN`) to the agent instead of executing the call — critically, the agent's system prompt/tool contract must define what to do on `CIRCUIT_OPEN`, or the agent will attempt unpredictable workarounds that bypass the breaker via a different code path ([Chanl Circuit Breakers](https://www.channel.tel/blog/ai-agent-circuit-breakers-reliability-production); [Agent Patterns Catalog Circuit Breaker](https://www.agentpatternscatalog.org/patterns/circuit-breaker/)).

**Enhanced 2026 designs**: per-tool circuit isolation (independent state per tool/agent ID, not just hostname); confidence-aware tripping (trip when average response confidence drops below threshold, not just on hard errors); cost-aware tripping (trip on token-burn-rate/cost-budget breach, not just error count); gradual HALF-OPEN recovery via exponential ramp-up (1, 2, 4, 8... test calls, doubling backoff on repeated HALF-OPEN failure); a **DEGRADED** state between CLOSED and OPEN that disables risky tools / adds human review / switches to a conservative model rather than going fully silent ([reaatech circuit-breaker-agents](https://github.com/reaatech/circuit-breaker-agents); [Medium — Resilience Circuit Breakers](https://medium.com/@michael.hannecke/resilience-circuit-breakers-for-agentic-ai-cc7075101486)).

**Layered error-handling stack**: Layer 1 — retry with exponential backoff + jitter for transient errors (e.g., a single 429); Layer 2 — multi-provider fallback chain, triggered only after Layer-1 retries are exhausted on the primary; Layer 3 — circuit breaker to stop hammering a persistently failing dependency ([niteagent Error Handling Guide](https://niteagent.com/blog/2026-07-14-building-reliable-agent-error-handling-guide/)).

**Rate-limit handling is a distinct mechanism from circuit breaking**: rate-limit (429) errors should be handled by rate-aware retry with backoff honoring the `Retry-After` header, not by immediately tripping a failure-count-based breaker — the two mechanisms work in tandem, with sustained rate-limiting eventually exhausting retry budget and *then* counting as a breaker-relevant failure ([Chanl Circuit Breakers](https://www.channel.tel/blog/ai-agent-circuit-breakers-reliability-production)).

---

## 4. Enterprise Security & Governance

### 4.1 Zero Trust for Agent Loop Actions

NIST SP 800-207 Zero Trust principles applied to agents: no principal (human or machine) is implicitly trusted, and **every access decision is evaluated per-request**, not just at initial authentication — RBAC's coarse-grained roles alone cannot deliver this granularity ([SSOJet AI Agent IAM](https://ssojet.com/blog/ai-agent-identity-and-access-control-a-framework-for-agentic-b2b-applications)). The emerging standard architecture: (1) every agent has a **stable, unique cryptographic identity** (e.g., an Ed25519 keypair) rather than shared/inherited credentials; (2) every tool call passes through an external policy-decision gateway *before* execution — deterministic and testable, external to the agent's own (non-deterministic) reasoning; (3) trust does not transfer between agents — sub-agents must earn their own scoped permissions rather than inheriting a parent's ([runcycles.io Zero Trust](https://runcycles.io/blog/zero-trust-for-ai-agents-why-every-tool-call-needs-a-policy-decision); [Cubernet/agentzt](https://github.com/Cubernet/agentzt)).

**Enclave pattern.** An enclave is a network-reachability trust boundary (roughly mapped to a project) containing sandboxed agents and the assets/tools they're authorized to reach; isolation is enforced at the *network* layer (the agent has no visibility into or influence over what it cannot reach), not merely by policy the agent might reason around ([Zentera Zero Trust](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai)).

### 4.2 Per-Capability RBAC / Fine-Grained Authorization Progression

The recommended maturity ladder: **RBAC** (coarse roles, e.g. `agent:read-only`) for simple single-tenant agents → **ABAC** (attribute-based, incorporating user-context) for user-delegated agents acting on behalf of a specific person → **FGA** (Fine-Grained Authorization, Zanzibar-style relationship tuples, e.g. `document:budget-q1, viewer, agent:pipeline-456`) for multi-tenant enterprise agents needing per-resource, individually-revocable grants ([SSOJet](https://ssojet.com/blog/ai-agent-identity-and-access-control-a-framework-for-agentic-b2b-applications)).

**Per-tool scoping is the single highest-leverage, lowest-effort RBAC investment**: each tool gets the narrowest scope it can possibly need (read-only by default, write only where required, never admin unless the tool's entire purpose is administrative). Enforced in three layers: (1) the tool's own credential is provisioned with minimum scope (e.g., a CRM-read tool gets a read-only API key, never full-access); (2) the orchestration layer attaches an authorization policy per tool asserting the originating user may act on the referenced resource *before* the call leaves the boundary; (3) the tool itself performs a final per-call check using the carried identity. Most agent security incidents trace back to a tool granted broader scope than it ever actually used ([Digital Applied Agentic RBAC](https://www.digitalapplied.com/blog/agentic-ai-rbac-design-patterns-implementation-2026)).

**Just-in-Time (JIT) elevation.** High-blast-radius tools (e.g., `email.send`) are kept out of every role's standing scope; a role with JIT eligibility can request a single-resource, short-lived grant via an explicit elevation exchange that auto-expires — keeping dangerous capabilities off by default rather than gated only by prompt-level caution ([Cubernet/agentzt](https://github.com/Cubernet/agentzt)).

### 4.3 PII Redaction in Agent State

The dominant 2026 architecture is a **pass-through proxy with zero data retention**: redaction happens *before* persistence, in-memory, between the moment an upstream system returns data and the moment it's written into agent state/audit storage — raw prompts, raw model outputs, and PII/PHI values are never written to durable storage in the first place; only safe derivatives are (decision outcome, reason codes, redaction-applied flag, risk scores, normalized tool names) ([TealTiger audit-and-redaction](https://docs.tealtiger.ai/concepts/audit-and-redaction); [Truto PII redaction via MCP](https://truto.one/blog/how-to-implement-pii-redaction-when-passing-saas-data-to-llms-via-mcp/)).

**Session-scoped token vault.** For agents that need to reference the same PII across multiple tool calls within one session, use a short-lived, **in-memory-only** token vault keyed to the conversation/session that maps stable tokens to real values (never persisted to disk) — deterministic tokenization avoids re-identifying the entity while allowing consistent references across turns ([Truto](https://truto.one/blog/how-to-implement-pii-redaction-when-passing-saas-data-to-llms-via-mcp/)).

**Hash-anchored audit proof.** Rather than storing raw content, the audit trail stores a content hash (e.g., SHA-256) computed *before* redaction: this proves later that a specific invocation produced specific content (verifiable against an externally-recovered artifact, e.g., a leaked file) without ever storing the disclosed content itself — the regulator needs proof a disclosure occurred, not a copy of what was disclosed ([ARMO Minimum Viable Audit Trail](https://www.armosec.io/blog/minimum-viable-audit-trail/)).

### 4.4 Sandbox Isolation for Agent Execution

**MicroVMs are the 2026 gold standard** for untrusted/LLM-generated code execution: each workload gets its own dedicated guest kernel on a hardware-virtualization boundary (KVM), so a guest-kernel vulnerability cannot reach the host kernel — fundamentally stronger than shared-kernel containers (Docker/runc), which are "the floor, not the ceiling" and appropriate only for semi-trusted code with a genuinely small blast radius (no network, no secrets, read-only filesystem) ([noqta.tn Sandboxes 2026](https://noqta.tn/en/blog/ai-agent-sandbox-secure-code-execution-2026); [manveerc Sandboxing Guide](https://manveerc.substack.com/p/ai-agent-sandboxing-guide)).

| Isolation tier | Boundary | Cold start | Blast radius on escape |
|---|---|---|---|
| Containers (Docker/runc) | Shared kernel | ~100ms-seconds | Host kernel CVEs |
| gVisor | Syscall interception | Sub-1s | Host kernel + sandbox bugs |
| Firecracker microVM | Dedicated guest kernel (KVM) | <125ms boot, ~28ms w/ snapshot restore | Hypervisor CVEs (smaller surface) |
| OS-level (bubblewrap/Seatbelt) | Namespace/profile-based | Tens of ms | Host kernel + profile bugs |

Firecracker specifics: <125ms cold boot, <5MiB memory overhead per VM, <5% compute overhead vs. bare metal; with snapshot-and-restore, a pre-warmed memory image can be restored via copy-on-write in ~28ms ([noqta.tn](https://noqta.tn/en/blog/ai-agent-sandbox-secure-code-execution-2026)). This underpins AWS Lambda, E2B, Vercel Sandbox, and Bedrock AgentCore's per-session isolation. ⚠️ Even hypervisor isolation is "necessary but not sufficient" — CVE-2026-1386 (Firecracker jailer symlink host-file overwrite, ≤v1.13.1/v1.14.0) is a documented reminder to patch the VMM/jailer as aggressively as the guest kernel ([agentpatterns.ai runtime comparison](https://agentpatterns.ai/security/sandbox-runtime-comparison/)).

**Enterprise pattern**: one microVM per agent session, default-deny network egress, credentials brokered *outside* the sandbox (the sandbox never holds the real API key — a supervisor proxies requests and injects credentials at the boundary), and the entire VM (with memory) sanitized/terminated at session end. Amazon Bedrock AgentCore, Axonius, and AgentFlo all use this exact pattern for multi-tenant isolation, citing it as the deciding factor for handling sensitive customer data across tenants ([AWS AgentFlo case study](https://aws.amazon.com/blogs/architecture/how-agentflo-built-ai-sales-agents-with-amazon-bedrock-agentcore-part-1/); [AWS Axonius case study](https://aws.amazon.com/blogs/machine-learning/how-axonius-built-secure-multi-tenant-ai-agents-on-bedrock-agentcore/); [Chainguard microVM](https://www.chainguard.dev/unchained/this-shit-is-hard-how-chainguard-is-sandboxing-athena)).

### 4.5 Audit Logs of Agent Decision Trajectories

**OpenTelemetry GenAI semantic conventions** (formed under the GenAI SIG, April 2024; v1.41.x as of 2026) define standardized span shapes for model inference, tool execution, agent invocation, workflow invocation, and — newly — **planning**. Key operation types: `invoke_agent` (splits `CLIENT` for remote agents vs. `INTERNAL` for in-process), `execute_tool` (span name must include the tool name as of v1.41: `execute_tool {gen_ai.tool.name}`; `gen_ai.tool.call.arguments`/`result` are recorded only per privacy policy), and the new **`plan`** operation — an `INTERNAL` span wrapping an agent's explicit planning/task-decomposition phase, with the planning LLM call as its child and resulting tool/task spans as siblings under the parent `invoke_agent` span. A single `trace_id` links the entire decision trajectory end-to-end, cracking open what was previously a black-box reasoning process ([Greptime OTel GenAI](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions); [OTel plan-operation PR #97](https://github.com/open-telemetry/semantic-conventions-genai/pull/97); [hidekazu-konishi implementation guide](https://hidekazu-konishi.com/entry/opentelemetry_genai_semantic_conventions_guide.html)).

**Architectural rule**: tool spans are children of the *agent* span, not of the model-call span — the model span ends when the model emits a tool-call request, and tool execution happens afterward in application code; nesting the tool span inside the model span misrepresents the timeline and inflates apparent model latency ([hidekazu-konishi](https://hidekazu-konishi.com/entry/opentelemetry_genai_semantic_conventions_guide.html)).

**OpenInference vs. OTel GenAI**: OpenInference offers more mature, first-class support for retrieval/re-ranking spans (explicit document types), useful for RAG-heavy agents; OTel GenAI is the community-default trajectory for teams standardizing telemetry across a broad, non-LLM-specific stack, and the gap is narrowing ([Arthur.ai comparison](https://www.arthur.ai/column/openinference-vs-opentelemetry-genai-conventions-agent-tracing)).

**What to actually persist**: data shape (token/byte counts, structural schema), sensitivity classification resolved at access time (`PII:email`, `PCI:pan`, `secret:api_key`), semantic tags, and a content hash computed before redaction — never raw prompt/output content in the audit trail itself ([ARMO](https://www.armosec.io/blog/minimum-viable-audit-trail/)).

---

## 5. Production Failure Modes

### 5.1 Infinite Loops — The Canonical $47K Incident

**Post-mortem (published March 2026, incident occurred November 2025).** A four-agent LangChain pipeline (A2A protocol) doing market research entered an undetected feedback loop between an **Analyzer** and a **Verifier** agent: the Verifier repeatedly requested "further analysis" without bounded acceptance criteria, and the Analyzer complied each time. The loop ran continuously for **264 hours (11 days)**, accruing **~$47,000** in LLM API costs while producing zero usable output. It was surfaced only by a billing-dashboard threshold breach — not by any termination or progress mechanism inside the agent system. Status reports remained "technically correct" throughout ("Schema drift resolution in progress"), and all health checks passed the entire time ([vectara/awesome-agent-failures case study](https://github.com/vectara/awesome-agent-failures/blob/main/docs/case-studies/langchain-a2a-47k-infinite-loop.md); [Clyro.dev forensic analysis](https://clyro.dev/blog/the-47k-loop-a-complete-forensic-analysis/)).

**Root-cause lesson**: "we have observability" ≠ "we can stop a runaway agent" — dashboards *narrate* failures after the fact; they do not *enforce* limits. Four controls that would have stopped the incident at ~$10 on Day 1: 3-iteration loop detection, a $10 cost ceiling, a 100-step limit, and circuit breakers tripping at 3x baseline run-time deviation ([Clyro.dev](https://clyro.dev/blog/the-47k-loop-a-complete-forensic-analysis/)).

**Enforcement design principles distilled from this incident** ([dev.to prashar32](https://dev.to/prashar32/the-47k-agent-loop-why-logging-monitoring-and-maxtokens-all-failed-to-stop-it-19ch)):
1. **Deterministic** — no model in the enforcement decision path; a limit is `total_cost > ceiling` evaluated in compiled code, never "an LLM that decides when to stop the LLM."
2. **Pre-call** — the check runs and can refuse the *next* call before it leaves the process; post-hoc enforcement races the spend and the spend always wins.
3. **Per-run, not per-call** — the pathology (11 days, cumulative spend) lives at the run level; the budget must be evaluated at that same altitude, with an external kill switch.

**Cost-incident comparison table** ([RockB Cost Circuit Breaker Guide](https://baeseokjae.github.io/posts/agent-cost-circuit-breaker-pattern-guide-2026/)):

| Incident | Cost | Primary cause | Preventing control |
|---|---|---|---|
| Single-agent retry loop | $437 overnight | Identical tool calls repeating 8 hours | Runaway-loop detection + retry budget |
| 4-agent A2A ping-pong | $47,000 / 11 days | Cross-agent work passing without progress | Cost-velocity + session-level breaker |
| Image-gen runaway | $700 overnight | Flaky API + k8s restart replay loop | Consecutive-failure breaker + retry budget |
| GPT-4 847-call loop | $63 | Ambiguous tool response → identical retries | Hash-based loop detection |
| LangChain 10K iterations | Unknown (project destroyed) | 8,000 iterations in <10 minutes | Cost velocity + wall-clock timeout |

**Broader rollback statistics.** A 2026 survey of 2,527 decision-makers on AI customer-communications agents found a **74% production rollback rate**, climbing to **81%** at the most governance-mature firms — attributed to state loss, blast radius, and budget loops, not model quality; a more capable model does not reduce rollback risk ([Medium — 74% rollback post-mortem](https://medium.com/@wasowski.jarek/why-74-of-ai-agents-get-rolled-back-in-production-a-runtime-post-mortem-d43e089abd27)). "Runaway execution" accounts for **5.1% of 500+ documented agent failures** in one forensic dataset but is the single most expensive failure category per incident ([Clyro.dev](https://clyro.dev/blog/the-47k-loop-a-complete-forensic-analysis/)).

### 5.2 Planning Failures & Bad Task Decomposition

**MAST taxonomy** (Multi-Agent System failure Taxonomy) clusters 14 failure modes into three categories: **Specification & System Design** (~41.8% of failures — task misinterpretation, ambiguous role definitions, poor decomposition, missing termination conditions); **Inter-Agent Misalignment** (~36.9% — communication breakdowns, context loss during handoffs, conflicting outputs, format mismatches); **Task Verification & Termination** (~21.3% — premature ending 6.2%, incomplete verification 8.2%, incorrect verification 9.1%) ([futureagi.substack MAST](https://futureagi.substack.com/p/why-do-multi-agent-llm-systems-fail)).

**Decomposition Granularity Index (DGI).** An empirical study across 1,200 tasks and five agent frameworks found decomposition granularity has a phase diagram: **Phase I** (under-decomposition) — performance limited by subtask complexity; **Phase II** (optimal window) — performance peaks; **Phase III** (over-decomposition) — coordination overhead dominates. The optimal DGI *shifts with task complexity*: simple tasks peak at DGI = 1.0-1.2, moderate tasks at 1.8-2.4, complex tasks at 3.0-4.5, following the heuristic **optimal DGI ≈ 0.85√S** (S = minimum required reasoning steps). Critically, the optimal window *narrows* as complexity increases (width 0.4 for simple tasks vs. 1.7 for complex), which explains why agent performance on hard tasks is volatile — small decomposition-strategy changes can push a hard task from the optimal Phase II into failure-prone Phase I or III ([clawRxiv DGI paper](https://clawrxiv.io/abs/2604.00690)).

**Decomposition should scale inversely with verification-gate strength**: if the downstream consumer's acceptance gate is strong (automated contract tests, structural checks), coarse decomposition units are safe because the gate catches errors; if the gate is weak, finer decomposition is required so each unit remains independently judgeable. This reframes "how to decompose a task" as a function of your evaluation infrastructure, not a fixed prompting technique ([dev.to Contracts Not Tasks](https://dev.to/nickghost/contracts-not-tasks-22m7)).

**Long-horizon degradation.** Research shows every agent experiences measurable performance degradation after ~35 minutes of continuous task work, and **doubling task duration quadruples the failure rate** — a non-linear relationship that is a fundamental constraint on how long an autonomous loop can run before requiring human-in-the-loop intervention or a fresh sub-agent context ([Zylos Long-Running Agents](https://zylos.ai/research/2026-01-16-long-running-ai-agents/)).

### 5.3 State Corruption & Drift Across Iterations

**State tracking, not planning, is the dominant failure mode.** A large-scale analysis of 50,247 execution trajectories across 12 agentic benchmarks found **61.3% of failures** are attributable to state-tracking errors (lost intermediate results, stale state references, counter/index drift, environment-state desync) — planning errors account for only **23.1%**, directly contradicting the prevailing assumption that planning dominates agent failure. State-tracking failures correlate strongly with trajectory length (r=0.83) but only weakly with task complexity (r=0.21), meaning *longer loops fail more from losing track of state than from harder reasoning*. A runtime monitor (StateGuard) intercepting these errors before cascade improved end-to-end success by **14.2 percentage points** ([clawRxiv State Tracking study](https://clawrxiv.io/abs/2604.01216)).

**Binding drift vs. error propagation.** In multi-step tool-augmented workflows, an agent resolves an entity (e.g., a customer record) once and acts on it across later steps. Two distinct pathologies: **binding drift** (correct at step 1, silently switches to a different entity later — occurs on 18% of eligible workflows naturally, with per-step error rate rising across steps) and **error propagation** (wrong entity bound at step 1, carried forward into every later step). The intuitive fix — an "entity lock" that persists the first binding — **eliminates drift but amplifies propagation 3.0x on aggregate, up to 8.5x on Claude Opus 4.5**, because it faithfully carries a seeded wrong entity into every subsequent action. The effective fix is **independent re-verification**: a single cheap second-model call that re-reads the original instruction and re-derives the target entity before high-risk actions reduces wrong actions by **79%**, closing to within 1 percentage point of an oracle upper bound. Persistence and re-verification are not interchangeable — a defense that eliminates drift can *worsen* propagation ([arXiv 2607.18316 Binding Drift](https://arxiv.org/html/2607.18316v1)).

### 5.4 Cascading Failures in Multi-Step Workflows

Multi-agent systems fail **41-86%** of the time depending on task complexity; five agents each at 95% individual accuracy compose to only **~77% end-to-end success** (0.95^5). Memory and reflection errors are the most common cascade sources; once a cascade begins mid-chain it is extremely difficult to reverse, and traditional monitoring fails because the model always produces something fluent, well-formatted, and wrong ([carried from tool-use research, corroborated across sources]; see also [futureagi MAST](https://futureagi.substack.com/p/why-do-multi-agent-llm-systems-fail)).

### 5.5 Termination-Condition Bugs

The two symmetric failure modes when termination is left implicit ("the model says it's done"): on uncertain tasks the model never commits to "done" and the loop runs to budget; on stuck tasks the model keeps retrying variations of the same broken approach indefinitely. Both burn budget and produce poor results — the fix is an explicit, external stop-hook predicate, never a second LLM call deciding whether the first LLM call is finished (which is not a control, but "a second thing that can fail") ([Agent Patterns Catalog Stop Hook](https://www.agentpatternscatalog.org/patterns/stop-hook/); [dev.to $47K loop](https://dev.to/prashar32/the-47k-agent-loop-why-logging-monitoring-and-maxtokens-all-failed-to-stop-it-19ch)).

### 5.6 Mitigation Summary

| Failure Mode | Mitigation |
|---|---|
| Infinite loops / runaway cost | Deterministic pre-call per-run budget (cost, steps, tokens, wall-clock) + external kill switch |
| Bad task decomposition | Match DGI to task complexity (~0.85√S); scale granularity inversely with verification-gate strength |
| State tracking errors (61.3% of failures) | Runtime state monitor (StateGuard-style) intercepting drift before cascade |
| Binding drift vs. propagation | Independent LLM re-verification before high-risk actions (not naive entity-lock persistence) |
| Cascading multi-agent failure | Step-level scoring / early pruning of bad trajectories; do not compose >3-4 agents at <99% individual accuracy without a verification gate |
| Termination ambiguity | External, deterministic stop-hook predicate with tagged exit reason (success / budget / stagnation / error) |
| Shared-state races | Optimistic concurrency (version-checked writes) for high-throughput; semantic mutex with TTL for financial/compliance actions |

---

## 6. Enterprise System Design Scenarios

### 6.1 Devin (Cognition) — Long-Horizon Planning Architecture

Devin implements a continuous **plan → execute → observe → re-plan** loop, representing its task plan not as a flat numbered list but as a **directed acyclic graph (DAG)** of dependent steps — enabling explicit identification of parallelizable work, analogous to build systems like Make/Gradle or CI/CD DAGs (Airflow/Prefect) ([Medium — How Devin Thinks](https://medium.com/@nitinmatani22/how-devin-ai-actually-thinks-autonomous-planning-dag-execution-and-dynamic-re-planning-explained-997be175a475)).

**Hybrid execution model ("the brain and the devbox").** A stateless reasoning coordinator ("brain") runs in Cognition's cloud and handles high-level planning; sandboxed "devbox" execution environments (or self-hosted "Outposts" connecting via outbound-only dial to customer infrastructure) perform actual tool use, shell commands, and file edits — Devin's agent loop itself never runs on customer infrastructure even in the Outposts model, only tool execution does ([Fastio Devin Architecture](https://fast.io/resources/cognition-devin-ai-architecture/); [Devin Outposts](https://devin.ai/blog/introducing-devin-outposts/)).

**Devin Fusion.** A hybrid model-execution strategy pairing a frontier reasoning model (system design, planning) with smaller specialized helper models (linting, file reading, syntax checks) to minimize latency and inference cost — the same "capable model plans, cheap model executes" pattern research shows can achieve **90% cost reduction** ([Fastio](https://fast.io/resources/cognition-devin-ai-architecture/); [Zylos Long-Running Agents](https://zylos.ai/research/2026-01-16-long-running-ai-agents/)).

**Agentic MapReduce.** For whole-repo-context tasks, a deterministic 4-stage pipeline: (1) **Plan** — an agent studies the repo and authors *selectors* (concrete, deterministically-runnable relevance tests); (2) **Shard** — the selector runs deterministically (no model) over the entire repo, bucketing matches into bounded batches; (3) **Map** — one child Devin session per batch investigates candidates in parallel, emitting structured findings; (4) **Reduce** — an agent groups, dedupes, and synthesizes per-shard outputs. This moves the expensive "spend budget finding the work" phase into a one-time deterministic pass, so token cost scales with the amount of *relevant* code rather than the size of the whole codebase ([Devin Agentic MapReduce](https://devin.ai/blog/agentic-map-reduce)).

**Self-correction loop.** On a non-zero shell exit or failed test suite, Devin does not halt — it reads the traceback, matches the error to the relevant file, edits, and re-runs the test script, continuing until resolved, without requiring human guidance at every step ([Fastio](https://fast.io/resources/cognition-devin-ai-architecture/)).

### 6.2 ReAct vs. Plan-and-Execute vs. Graph Workflows — Trade-off Matrix

| Dimension | ReAct | Plan-and-Execute | Graph (LangGraph/DAG) |
|---|---|---|---|
| LLM calls per task | One per step, full context replayed each time | 1 planner call + N (often smaller-model) executor calls | Determined by graph topology; parallel branches fan out concurrently |
| Context growth | Grows with task length (re-sent every step) | Stays lean per executor step (planner context isolated from executor context) | Scoped per-node; explicit state schema controls what each node sees |
| Best fit | Exploratory/unpredictable tasks where the path can't be known in advance | Stable, decomposable, multi-step workflows with known dependencies | Complex workflows needing explicit conditional branching, audit-ready routing, cycles |
| Parallelism | None — each step waits on the prior observation | Possible if planner emits independent steps (LLMCompiler: DAG-based, 3.6x speedup) | Native — a node with multiple outgoing edges executes all destinations in the same superstep |
| Latency crossover | Faster for ≤3-4 step tasks (skips planner overhead) | Faster for ≥5-6 step tasks; planner tax amortizes | Fastest for high-parallelism workloads; highest upfront design cost |
| Failure mode | Context bloat, drift, no global plan to fall back on | Brittle to premature-plan invalidation without a re-planning loop | Rigid — requires the graph to have been designed for the actual branching that occurs |
| Auditability | Low — reasoning is implicit in the transcript | Medium — explicit plan object is inspectable | Highest — every node/edge transition is named, logged, and independently testable |

Synthesized from ([laxaar.com Tradeoffs](https://laxaar.com/blog/agent-planning-react-vs-plan-and-execute-1749470001700); [dasroot.net Agent Architectures](https://dasroot.net/posts/2026/04/agent-architectures-react-plan-execute-graph-agents/); [Atlan ReAct vs P-t-E](https://atlan.com/know/ai-agent/react-vs-plan-and-execute-agent-architecture/); [dreaming.press ReAct vs P-t-E vs Reflexion](https://dreaming.press/posts/react-vs-plan-and-execute-vs-reflexion.html)). Framework fit: Strands SDK covers Single Agent, Workflow, Loop, Coordinator, Swarm, Graph, and ReAct patterns; LangGraph is best suited for Parallel, Review & Critique, Reflection, Hierarchical, Human-in-the-Loop, and ReWOO patterns ([UTurn Data Solutions](https://www.uturndata.com/insights/choosing-the-right-agentic-ai-pattern-for-genai-implementation)).

> No production pattern is a pure binary choice — the framing that best matches 2026 practice is a single dial measuring "how much the agent commits before observing": every production plan-and-execute deployment eventually bolts on a re-plan step after each execution batch, moving it partway back toward ReAct's step-at-a-time re-decision ([dreaming.press](https://dreaming.press/posts/react-vs-plan-and-execute-vs-reflexion.html)).

### 6.3 Enterprise Scale Case Studies

**AgentFlo (AWS Bedrock AgentCore).** Multi-merchant AI sales agent handling 10-50x traffic spikes during flash sales. Architecture: fully serverless with independently-scaling layers (message ingestion, agent execution, tool execution, state, analytics, billing) so one merchant's surge cannot starve another's capacity. Each customer session runs in an isolated microVM with dedicated CPU/memory/filesystem, sanitized on termination; sessions are stateful for up to **8 hours**, preserving context across a customer's full day ([AWS AgentFlo case study](https://aws.amazon.com/blogs/architecture/how-agentflo-built-ai-sales-agents-with-amazon-bedrock-agentcore-part-1/)).

**Axonius (AWS Bedrock AgentCore, siloed multi-tenancy).** A dedicated agent per enterprise customer, each session in its own microVM (isolated CPU/memory/filesystem, memory sanitized on termination) — required because customer data includes sensitive cybersecurity asset inventories that must never cross tenant boundaries. Using the managed AgentCore runtime, Knowledge Bases for RAG, and built-in session memory reduced Axonius's development cycle from an estimated **8 weeks of custom infrastructure** to **10 days of production-ready deployment** — a 75% time-to-market reduction ([AWS Axonius case study](https://aws.amazon.com/blogs/machine-learning/how-axonius-built-secure-multi-tenant-ai-agents-on-bedrock-agentcore/)).

**Salesforce Agentforce Sales Agent.** Processes hundreds of thousands of opportunities nightly (1.04M monthly recommendations for 13,000 sellers) within a strict 9-hour window, each opportunity requiring up to 27,000 input tokens synthesized from calls/emails. A **300 requests/minute** platform ceiling made naive per-opportunity synchronous invocation infeasible, forcing a **message-queue-driven architecture** decoupling orchestration from execution; a fast-fail mechanism for missing video-call transcripts (falling back to voice transcripts) cut per-opportunity latency from 1.35s to ~600ms ([Enggist Salesforce case study](https://enggist.vercel.app/post/f9408c82-7ceb-45a1-bf60-828de026bbd6)).

**Scaling stages (100K-user trajectory), synthesized guidance**: Stage 1 (single-digit-to-low-hundreds users) — basic session handling; Stage 2 (100-1,000 users) — add session persistence, rate limiting, cost tracking, model routing for simple tasks; Stage 3 (1,000-10,000 users) — multi-region routing, tiered state management, containerized tool execution, comprehensive monitoring; Stage 4 (10,000-100,000 users) — full observability, sophisticated per-tenant cost attribution, automated scaling, agent-version A/B testing. Core KPIs to track throughout: concurrent session capacity, P99 inference latency, cost per successful task, tool-call failure rate, and session-abandonment rate (correlates strongly with latency) ([Vinayaka Jyothi Scaling Agents](https://vinayakajyothi.com/blog/2026-05-08-llm-agent-scaling-strategies/)).

### 6.4 Reference Architecture Checklist for Enterprise Agent-Loop Systems

Synthesizing across the case studies and patterns above, a production-grade agent-loop architecture combines:

1. **Control-plane / data-plane separation** — identity, policy, budget, and audit logging live outside the executing agent process and are enforced pre-call.
2. **Typed, versioned state schema** — input / pipeline / control layers, with named transitions and deterministic routing functions.
3. **Durable checkpointing** at the node/activity boundary (LangGraph Postgres checkpointer, or Temporal event-sourced workflow) so crashes resume without re-paying for completed LLM calls.
4. **Composable, deterministic termination contract** — step/token/time/cost budgets plus stagnation detection plus an external kill switch, evaluated pre-call, per-run.
5. **Per-tool circuit breakers** with structured error contracts the agent must consume, layered under provider-level fallback chains and rate-aware retry with backoff.
6. **MicroVM-isolated execution** per session/tenant with default-deny egress and externally-brokered credentials.
7. **Zero-trust, per-capability authorization** at the tool-dispatch gateway, with JIT elevation for high-blast-radius actions.
8. **OTel GenAI-conformant tracing** (`invoke_agent` → `plan` → `chat`/`execute_tool`) tied to a single trace ID per task, with PII redacted before any span/log persists.
9. **Capacity planning by concurrency** (Little's Law) with fan-out-adjusted downstream provisioning, not naive QPS extrapolation.
10. **Cost-per-completed-task** as the primary economic metric, with cache-hit rate and output/input token ratio as the two highest-leverage cost levers.

---

## Sources

- [1] [ReAct paper site](https://react-lm.github.io/) — Original ReAct paradigm description and results
- [2] [Google Research Blog — ReAct](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/) — ReAct mechanics and design rationale
- [3] [IBM — What is a ReAct Agent?](https://www.ibm.com/think/topics/react-agent) — ReAct framework explainer
- [4] [OpenAI Agents SDK — Running Agents](https://openai.github.io/openai-agents-python/running_agents/) — Official agent loop documentation
- [5] [OpenAI Agents SDK — Runner Reference](https://openai.github.io/openai-agents-python/ref/run/) — Runner loop mechanics
- [6] [DeepWiki — Runner and Execution Flow](https://deepwiki.com/openai/openai-agents-python/3.2-runner-and-execution-flow) — Turn-loop architecture deep dive
- [7] [Google ADK — Event Loop docs](https://github.com/google/adk-docs/blob/5331a07f/docs/runtime/event-loop.md) — Official ADK runtime event loop spec
- [8] [The New Stack — Google ADK Architectural Tour](https://thenewstack.io/what-is-googles-agent-development-kit-an-architectural-tour/) — ADK Runner/Event architecture explainer
- [9] [Google Developers Blog — Why we built ADK 2.0](https://developers.googleblog.com/why-we-built-adk-20/) — ADK 2.0 Workflow/DAG support
- [10] [pratikdhanave.com — ADK Runtime & Events](https://pratikdhanave.com/blog/posts/adk-10-runtime-and-events.html) — ADK event-log durability model
- [11] [Inside Claude Code — Agent Loop & QueryEngine](https://y-agent.github.io/inside-claude-code/02-agent-loop-query-engine.html) — Claude Code async-generator loop architecture
- [12] [Claude Code from Source — Ch 5. The Agent Loop](https://claude-code-from-source.com/ch05-agent-loop/) — query.ts internals and context compaction layers
- [13] [VILA-Lab/Dive-into-Claude-Code](https://github.com/VILA-Lab/Dive-into-Claude-Code) — Reverse-engineered Claude Code architecture breakdown
- [14] [Sid Bharath — Anatomy of Claude Code](https://sidbharath.com/blog/the-anatomy-of-claude-code/) — Agent harness state object and compaction
- [15] [LangGraph graph-api.md](https://docs.langchain.com/oss/python/langgraph/graph-api.md) — Official State/Nodes/Edges reference
- [16] [LangChain Blog — LangGraph](https://www.langchain.com/blog/langgraph) — LangGraph StateGraph introduction
- [17] [LangGraph use-graph-api](https://docs.langchain.com/oss/python/langgraph/use-graph-api) — Loops, branches, Send API, parallel execution
- [18] [Anthropic — Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents) — Workflows vs. agents distinction
- [19] [Anthropic Architecture Patterns eBook](https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf) — Extended agent architecture guidance
- [20] [LangChain — Plan-and-Execute Agents](https://www.langchain.com/blog/planning-agents) — Plan-and-Execute, ReWOO, LLMCompiler architectures
- [21] [arXiv 2509.08646 — Secure Plan-then-Execute](https://arxiv.org/pdf/2509.08646) — P-t-E security/control-flow integrity guide
- [22] [arXiv 2503.09572 — Plan-and-Act](https://arxiv.org/html/2503.09572v2) — Trained Planner/Executor for long-horizon tasks
- [23] [Agent Patterns Catalog — Composable Termination Conditions](https://www.agentpatternscatalog.org/patterns/composable-termination-conditions/) — Termination primitive design
- [24] [Agent Patterns Catalog — Stop Hook](https://www.agentpatternscatalog.org/patterns/stop-hook/) — Programmatic stop predicate pattern
- [25] [aiarch.dev — Bounded Agentic Loop](https://aiarch.dev/patterns/bounded-agentic-loop) — Budget guard and kill-switch architecture
- [26] [DEV Community — Stopping Conditions](https://dev.to/multigrid/stopping-conditions-preventing-infinite-agent-loops-1377) — Practical termination condition table
- [27] [DoiT — Cost per Task vs Cost per Token](https://www.doit.com/blog/cost-per-task-vs-cost-per-token) — Reliability-adjusted cost economics
- [28] [Cockroach Labs — Managing Agentic AI Costs at Scale](https://www.cockroachlabs.com/blog/agentic-ai-costs-at-scale/) — Gartner 5-30x token multiplier
- [29] [Augment Code — AI Agent Loop Token Costs](https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints) — Quadratic token growth and monitoring metrics
- [30] [dreaming.press — Unit Economics Worksheet](https://dreaming.press/posts/what-an-ai-agent-costs-per-task-unit-economics-worksheet.html) — Full per-task cost formula
- [31] [DigitalOcean — p50 vs p99 Latency](https://www.digitalocean.com/community/tutorials/p50-vs-p99-latency-llm-inference) — Measured TTFT/completion latency benchmarks
- [32] [isimplifyme — Agent SLOs](https://isimplifyme.com/blog/agent-slos) — SLI/SLO design for agent workflows
- [33] [tianpan.co — Latency Budgets for Multi-Step Agents](https://tianpan.co/blog/2026-05-01-latency-budgets-multi-step-agents) — P99/P50 ratio analysis
- [34] [tianpan.co — Latency Budget Nobody Allocated](https://tianpan.co/blog/2026-07-05-the-latency-budget-nobody-allocated-across-agent-hops) — Tail-at-scale applied to agent hops
- [35] [tianpan.co — Capacity Planning for Agents](https://tianpan.co/blog/2026-06-04-capacity-planning-for-agents-why-concurrency-not-qps-is-your-real-unit) — Little's Law concurrency sizing
- [36] [tianpan.co — Rate Limit Became a Product Decision](https://tianpan.co/blog/2026-05-17-rate-limit-became-product-decision) — TPM/RPM as connection-pool analogy
- [37] [LangGraph checkpointers docs](https://docs.langchain.com/oss/python/langgraph/checkpointers) — Official checkpointer architecture
- [38] [LangGraph checkpoints reference](https://reference.langchain.com/python/langgraph/checkpoints) — PostgresSaver schema and pending-writes recovery
- [39] [Temporal — Agent Harness](https://temporal.io/blog/temporal-agent-harness-durable-agent-infrastructure) — Durable agent infrastructure design
- [40] [niteagent — Durable AI Agents with Temporal](https://niteagent.com/blog/2026-06-29-durable-ai-agents-temporal-guide/) — Temporal activity-level checkpointing patterns
- [41] [Temporal — Durable Flexible Multi-Agent Systems](https://temporal.io/blog/durable-flexible-multi-agent-systems) — Checkpoint-boundary caveat, LangGraph vs Temporal scope
- [42] [Zylos — AI Agent Workflow Checkpointing](https://zylos.ai/research/2026-03-04-ai-agent-workflow-checkpointing-resumability) — Continue-As-New pattern
- [43] [tianpan.co — Race Conditions in Concurrent Agent Systems](https://tianpan.co/blog/2026-04-12-race-conditions-in-concurrent-agent-systems) — Optimistic locking and vector clocks
- [44] [ninelayer.in — The Agentic Mutex](https://ninelayer.in/blog/agent-mutex) — Semantic lock pattern for shared agent state
- [45] [ECOA AI — Multi-Agent Shared State Problems](https://ecoaai.com/multi-agent-shared-state-problems-redis-orchestration/) — Redis optimistic locking case study
- [46] [arXiv 2606.15376 — CoAgent](https://arxiv.org/html/2606.15376) — Formal concurrency-control benchmarks for multi-agent systems
- [47] [Agent Patterns Catalog — Circuit Breaker](https://www.agentpatternscatalog.org/patterns/circuit-breaker/) — Dispatch-boundary circuit breaker pattern
- [48] [Chanl — Circuit Breakers for AI Agents](https://www.channel.tel/blog/ai-agent-circuit-breakers-reliability-production) — Structured CIRCUIT_OPEN error contract
- [49] [reaatech/circuit-breaker-agents](https://github.com/reaatech/circuit-breaker-agents) — Per-tool isolation, confidence/cost-aware tripping
- [50] [niteagent — Reliable Agent Error Handling](https://niteagent.com/blog/2026-07-14-building-reliable-agent-error-handling-guide/) — Layered retry/fallback/breaker stack
- [51] [Digital Applied — Agentic AI RBAC](https://www.digitalapplied.com/blog/agentic-ai-rbac-design-patterns-implementation-2026) — Per-tool scoping design patterns
- [52] [SSOJet — AI Agent Identity and Access Control](https://ssojet.com/blog/ai-agent-identity-and-access-control-a-framework-for-agentic-b2b-applications) — RBAC/ABAC/FGA maturity ladder
- [53] [runcycles.io — Zero Trust for AI Agents](https://runcycles.io/blog/zero-trust-for-ai-agents-why-every-tool-call-needs-a-policy-decision) — Per-tool-call policy decision architecture
- [54] [Cubernet/agentzt](https://github.com/Cubernet/agentzt) — Reference zero-trust gateway with JIT elevation
- [55] [Zentera — Zero Trust Architecture for Agentic AI](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai) — Enclave network-isolation pattern
- [56] [TealTiger — Audit and Redaction](https://docs.tealtiger.ai/concepts/audit-and-redaction) — Redact-before-persistence audit event design
- [57] [Truto — PII Redaction via MCP](https://truto.one/blog/how-to-implement-pii-redaction-when-passing-saas-data-to-llms-via-mcp/) — Session-scoped token vault pattern
- [58] [ARMO — Minimum Viable Audit Trail](https://www.armosec.io/blog/minimum-viable-audit-trail/) — Hash-anchored audit proof design
- [59] [noqta.tn — AI Agent Sandboxes 2026](https://noqta.tn/en/blog/ai-agent-sandbox-secure-code-execution-2026) — Firecracker microVM isolation specs
- [60] [manveerc — Sandboxing AI Agents Guide](https://manveerc.substack.com/p/ai-agent-sandboxing-guide) — Isolation-tier comparison
- [61] [agentpatterns.ai — Sandbox Runtime Comparison](https://agentpatterns.ai/security/sandbox-runtime-comparison/) — Container/microVM/OS-isolator trade-offs, CVE-2026-1386
- [62] [AWS — AgentFlo Bedrock AgentCore case study](https://aws.amazon.com/blogs/architecture/how-agentflo-built-ai-sales-agents-with-amazon-bedrock-agentcore-part-1/) — Multi-merchant microVM isolation at scale
- [63] [AWS — Axonius Bedrock AgentCore case study](https://aws.amazon.com/blogs/machine-learning/how-axonius-built-secure-multi-tenant-ai-agents-on-bedrock-agentcore/) — Siloed multi-tenant agent architecture
- [64] [Greptime — OpenTelemetry GenAI Semantic Conventions](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) — Agent/tool/plan span conventions
- [65] [OTel semantic-conventions-genai PR #97](https://github.com/open-telemetry/semantic-conventions-genai/pull/97) — New `plan` operation specification
- [66] [vectara/awesome-agent-failures — $47K infinite loop case study](https://github.com/vectara/awesome-agent-failures/blob/main/docs/case-studies/langchain-a2a-47k-infinite-loop.md) — Canonical infinite-loop production post-mortem
- [67] [Clyro.dev — $47K Loop Forensic Analysis](https://clyro.dev/blog/the-47k-loop-a-complete-forensic-analysis/) — Detailed incident reconstruction and preventing controls
- [68] [dev.to prashar32 — $47K Agent Loop](https://dev.to/prashar32/the-47k-agent-loop-why-logging-monitoring-and-maxtokens-all-failed-to-stop-it-19ch) — Deterministic/pre-call/per-run enforcement principles
- [69] [RockB — Agent Cost Circuit Breaker Guide](https://baeseokjae.github.io/posts/agent-cost-circuit-breaker-pattern-guide-2026/) — Cost-incident comparison table
- [70] [Medium — Why 74% of AI Agents Get Rolled Back](https://medium.com/@wasowski.jarek/why-74-of-ai-agents-get-rolled-back-in-production-a-runtime-post-mortem-d43e089abd27) — Production rollback survey data
- [71] [futureagi.substack — Why Multi-Agent LLM Systems Fail](https://futureagi.substack.com/p/why-do-multi-agent-llm-systems-fail) — MAST failure taxonomy
- [72] [Zylos — Long-Running AI Agents and Task Decomposition](https://zylos.ai/research/2026-01-16-long-running-ai-agents/) — 35-minute degradation, planner-worker cost reduction
- [73] [clawRxiv — Task Decomposition Granularity Phase Diagram](https://clawrxiv.io/abs/2604.00690) — DGI formula and complexity-dependent optimal windows
- [74] [dev.to nickghost — Contracts, Not Tasks](https://dev.to/nickghost/contracts-not-tasks-22m7) — Decomposition granularity vs. gate strength
- [75] [clawRxiv — Tool-Use Failures Cluster Around State Tracking](https://clawrxiv.io/abs/2604.01216) — 50K trajectory failure analysis, StateGuard
- [76] [arXiv 2607.18316 — Binding Drift in Multi-Step Tool-Augmented Agents](https://arxiv.org/html/2607.18316v1) — Entity-lock amplification and re-verification fix
- [77] [Medium — How Devin AI Actually Thinks](https://medium.com/@nitinmatani22/how-devin-ai-actually-thinks-autonomous-planning-dag-execution-and-dynamic-re-planning-explained-997be175a475) — DAG-based planning and persistent working memory
- [78] [Devin — Agentic MapReduce](https://devin.ai/blog/agentic-map-reduce) — Plan/Shard/Map/Reduce architecture for repo-scale tasks
- [79] [Fastio — Inside Devin AI Architecture](https://fast.io/resources/cognition-devin-ai-architecture/) — Brain/devbox hybrid model, Devin Fusion, self-correction loop
- [80] [Devin — Introducing Devin Outposts](https://devin.ai/blog/introducing-devin-outposts/) — Cloud agent loop / customer-hosted execution split
- [81] [laxaar.com — Agent Planning Tradeoffs](https://laxaar.com/blog/agent-planning-react-vs-plan-and-execute-1749470001700) — ReAct vs P-t-E crossover point analysis
- [82] [dasroot.net — Agent Architectures Comparison](https://dasroot.net/posts/2026/04/agent-architectures-react-plan-execute-graph-agents/) — ReAct/Plan-Execute/Graph cost and latency data
- [83] [Atlan — ReAct vs Plan-and-Execute Guide](https://atlan.com/know/ai-agent/react-vs-plan-and-execute-agent-architecture/) — Workflow design and latency qualification
- [84] [dreaming.press — ReAct vs Plan-and-Execute vs Reflexion](https://dreaming.press/posts/react-vs-plan-and-execute-vs-reflexion.html) — Unified "commitment dial" framing
- [85] [UTurn Data Solutions — Choosing the Right Agentic AI Pattern](https://www.uturndata.com/insights/choosing-the-right-agentic-ai-pattern-for-genai-implementation) — ReWOO, Graph pattern, framework fit table
- [86] [Vinayaka Jyothi — Scaling AI Agents to 100K Users](https://vinayakajyothi.com/blog/2026-05-08-llm-agent-scaling-strategies/) — Scaling-stage playbook and KPIs
- [87] [Enggist — Salesforce Agentforce Case Study](https://enggist.vercel.app/post/f9408c82-7ceb-45a1-bf60-828de026bbd6) — Message-queue architecture under RPM constraints
- [88] [agentnative.dev — LLM Rate Limit Calculator](https://www.agentnative.dev/tools/rate-limit-calculator) — TPM/RPM binding-limit formula
