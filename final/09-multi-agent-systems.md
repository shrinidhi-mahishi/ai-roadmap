# Module 09: Multi-Agent Systems

## What Is This?

Sometimes one agent isn't enough. A **multi-agent system** splits work across multiple specialized agents that collaborate, like a team of specialists instead of one generalist.

Why would you use multiple agents instead of one?
- **Context window limits**: One agent can't hold all the tools, instructions, and context for a complex task. Splitting across agents keeps each one focused.
- **Different permissions**: A research agent might have web access but no database access, while a data agent has database access but no web access. Separation enforces security.
- **Parallelism**: Multiple agents can work simultaneously -- one researches competitors while another analyzes financials.
- **Specialization**: A coding agent writes better code when that's its only job, rather than also handling research and documentation.

The main patterns are:
- **Supervisor** (most common): One "boss" agent delegates tasks to worker agents and combines their results. Like a manager coordinating a team.
- **Swarm/Handoff**: Agents pass control to each other directly, like a relay race. Agent A handles the greeting, then hands off to Agent B for technical support.
- **Hierarchical**: Multiple levels of supervisors -- a VP delegates to managers who delegate to workers. For very complex tasks.

A concrete example: Anthropic's research system uses a Lead agent that spawns multiple Sub-agents for parallel web searches. The Lead plans the research questions, each Sub-agent investigates one question independently, and the Lead synthesizes all findings into a final report.

## Why It Matters

Multi-agent systems are how you scale from "agent that handles one task" to "system that handles complex, multi-step workflows." But they add significant complexity -- coordination overhead, failure modes, and cost multiplication. Knowing when to use multiple agents vs. when one is enough is a critical design decision.

---

## 2. Core Concepts

### The Control Plane vs Data Plane Split

The most important invariant to internalize: **the model never routes, never hands off, never grants authority**. It emits a structured action (a tool call named `transfer_to_*`, an A2A `SendMessage`, a LangGraph `Command`). A **runtime** interprets that action, mutates durable state, and decides the next node. Collapsing "who may act" into the LLM prompt is the dominant enterprise failure.

Think of it like air traffic control vs the planes themselves. The control tower (control plane) decides which plane lands next, manages spacing, and enforces rules. The planes (data plane) fly and carry passengers. Merging them means the pilot is also the air traffic controller -- disaster.

| Layer | Owns | Typical Objects | Failure If Fused into the LLM |
|-------|------|-----------------|-------------------------------|
| **Control** | Loop budget, next-agent, max hops, kill-switch, HITL gates | LangGraph compiler + checkpointer; OpenAI `Runner` (`max_turns` default **10**); Temporal Workflow; A2A `TaskState` | Infinite ping-pong; 50-subagent fan-out; unbounded spend |
| **Data** | Tool HTTP, MCP `tools/call`, A2A artifacts, sandboxes | Worker tools, MCP servers, A2A `Artifact`/`Part`, LangGraph `Store` | PII in every hop; confused-deputy token passthrough |
| **Persistence** | Resume identity | `thread_id`/`checkpoint_id`; A2A `contextId`+`taskId`; Temporal workflow id | Restart from scratch after a 500 |
| **Policy** | Who may call which tool under which principal | Per-agent tool lists, MCP Resource Indicators (RFC 8707), A2A `securitySchemes` | Worker inherits supervisor's OAuth cookie |

Microsoft Learn (2026): **prefer platform-native orchestration for internal subagents**; use **MCP for tools/data**; use **A2A for opaque, cross-platform, cross-org agents**. MCP is the tool bus; A2A is the agent bus.

LangChain's own framing: "multi-agent" is usually a request for **context management**, **distributed development**, or **parallelization** -- not a request for more LLMs. If context were infinite and latency zero, a single agent with all tools would dominate.

### The Three Authority Layers

These must never collapse:

1. **Routing authority** -- who may be the next agent (handoffs list, A2A skill, supervisor tool list)
2. **Tool authority** -- which MCP/tools that worker may call (per-agent allowlist)
3. **Principal authority** -- on whose behalf (user OAuth vs agent service account). An MCP server MUST NOT passthrough the client's token to a downstream API; exchange for a correctly audienced token

A worker that inherits the supervisor's cookie is a confused deputy waiting to happen.

---

## 3. How It Works

### 3.1 Five Topologies

LangGraph, Microsoft Agent Framework 1.0, and LangChain 1.x patterns converge on the same five shapes. Understanding which to pick is the core architectural decision.

| Topology | Who Picks the Next Hop | Parallelism | Typical Product |
|----------|----------------------|-------------|-----------------|
| **Router** | One classification step, then specialist(s) | `Send` fan-out | LangChain Router + `Send` |
| **Supervisor / Orchestrator-Worker** | Central LLM every round | Optional (`parallel_tool_calls`) | LangGraph `create_supervisor`; Anthropic Research; Magentic-One |
| **Hierarchical Supervisors** | Supervisor of compiled supervisors | Per-team | `create_supervisor([research_team, writing_team])` |
| **Swarm / Mesh / Handoff** | Currently active agent | Sequential by default | LangGraph swarm; OpenAI `handoffs` |
| **Custom / Blackboard / Network** | State schema, Hub, or blackboard controller | Mixed | LangGraph custom graph; AG2 Hub+channels |

**Communication complexity and cost:**

| Topology | Communication | State Ownership | SPOF Risk | Best Scale |
|----------|--------------|-----------------|-----------|------------|
| Hub-and-spoke / Orchestrator-worker | Star; O(n) edges | Centralized | Hub is SPOF | 3-7 spokes per hub |
| Hierarchical (tree) | O(n) edges, O(log n) routing depth | Layered; supervisor owns subtree | Subtree-scoped | 20-500 agents |
| Mesh (peer-to-peer) | O(n^2) potential connections | Transferred on handoff | No single SPOF, but no circuit-breaker point | 3-8 tightly coupled agents |
| Flat / swarm | Emergent, shared blackboard | Global state | Blackboard is bottleneck | Dozens (up to ~100 subagents in parallel reported) |

A full mesh costs an estimated **2-11.8x more tokens than a simple sequential chain** (ICLR 2025 analysis). Enterprise deployments converge on **hierarchical orchestrator-worker as the production default**, with a **two-level hierarchy** (orchestrator + workers, no further nesting) as the Pareto-optimal point for cost/latency/consistency trade-offs.

Google DeepMind research found centralized coordination **improved performance by 80.9%** on parallelizable tasks but **degraded by 39-70% on sequential reasoning tasks**. This is the key architectural boundary: decompose by independence of subtasks, not by headcount.

### 3.2 Supervisor: Router vs Orchestrator vs Hierarchical

These three words are not synonyms. They describe three different **control-plane clocks**.

| Role | Clock | Decision | When It Wins |
|------|-------|----------|--------------|
| **Router** | Once per user turn (stateless) | Classify then 1..K specialists | Known domains, parallel retrieval, no multi-hop |
| **Orchestrator (lead)** | Every round until "enough" | Decompose, spawn, synthesize, re-spawn | Breadth-first research, unknown search DAG |
| **Supervisor (LangGraph)** | Every worker return | Which worker tool next, or FINISH | Tool isolation + centralized reply |
| **Hierarchical supervisor** | Per level | Which *team* next | Org/IAM boundaries, not token savings |

**Router mechanics.** `Command(goto=agent)` for one specialist; `list[Send(agent, {query})]` for parallel. Tutorial pattern: GitHub + Notion + Slack in parallel, then a synthesizer. Router LLM call is pure overhead on repeat turns (3 calls every time vs handoffs' 2).

**Orchestrator mechanics (Anthropic Research).** The most detailed public engineering account of a live orchestrator-worker deployment:

- **LeadResearcher**: Analyzes user query, saves plan to **external memory** (critical because the 200K-token context truncates -- losing the plan mid-task is catastrophic), then spawns 3-5 subagents in parallel.
- **Subagents**: Each gets an explicit objective, output format, tool list, and clear task boundaries. Each operates in an **isolated context window** and uses 3+ tools in parallel internally.
- **CitationAgent**: Final-pass specialist that matches every claim back to source documents, decoupling citation-correctness from the research/synthesis loop.
- **Effort scaling in prompts**: simple fact-finding -> 1 agent, 3-10 tool calls; direct comparisons -> 2-4 subagents, 10-15 calls each; complex research -> 10+ subagents with divided responsibilities.

Published results: multi-agent Opus-lead + Sonnet-subs achieved **+90.2%** vs single-agent Opus 4 on internal research eval. Parallelization (3-5 subs x 3+ tools per sub) **cut research wall-clock by up to 90%**. Three factors explain **95%** of BrowseComp variance: **token usage (80%)**, tool-call count, and model choice.

```
                    +-----------------+
                    | LeadResearcher  |
                    | (Opus 5 - plan) |
                    +--------+--------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     | Subagent 1 |  | Subagent 2  |  | Subagent 3  |
     | (Sonnet 5) |  | (Sonnet 5)  |  | (Sonnet 5)  |
     | Web Search |  | DB Query    |  | File Analysis|
     +--------+---+  +------+------+  +----+--------+
              |              |              |
              +--------------+--------------+
                             |
                    +--------v--------+
                    | CitationAgent   |
                    | (verify claims) |
                    +-----------------+
```

**Known limitation stated by Anthropic**: subagents currently execute **synchronously** -- the lead waits for a full round before proceeding. This blocks the lead from steering mid-flight, prevents sub-to-sub coordination, and makes the system wait on the slowest subagent. Async execution is a stated next step but introduces "challenges in result coordination, state consistency, and error propagation."

**Magentic-One (Microsoft).** Outer loop: **Task Ledger** (facts, guesses, plan). Inner loop: **Progress Ledger** (is it done? who next?). Stall detector: `max_stalls=3` default; then replan. Default `max_turns=20`. Workers are tool-shaped, not domain-shaped: WebSurfer, FileSurfer, Coder, ComputerTerminal. Ablations: removing full ledgers **-31%**; removing any one worker **-21% to -39%**. Explicitly designed for modular extensibility -- agents added/removed without re-tuning. Published task-completion (GPT-4o era): **38% GAIA**, **32.8% WebArena**, **27.7% AssistantBench**.

### 3.3 Worker Patterns: Specialists, Tool-Scoped, Skills

**Specialist agents** are workers whose **prompt + tool set + policy** change together. OpenAI's guidance: split only when instructions, tools, or policy actually change -- extra agents multiply prompts, traces, and approval surfaces.

**Tool-scoped workers** (Magentic-One, Anthropic subs): the specialist is defined by what it can touch (browser, files, code interpreter, web search), not by a business domain. This maps cleanly to IAM: a FileSurfer identity should not hold Stripe scopes.

**Skill isolation** is the **non-agent** alternative. Skills = progressive disclosure of prompts/knowledge. A `load_skill` tool injects a playbook; the same agent stays in control. Token profile: few extra calls, high context once many skills are loaded (~15K vs subagents' ~9K on a three-language comparison). Isolation is prompt-deep, not process-deep.

**LangChain call-count comparison across patterns:**

| Workload | Subagents | Handoffs | Skills | Router |
|----------|-----------|----------|--------|--------|
| One-shot "buy coffee" | **4** calls | **3** | **3** | **3** |
| Repeat same request | **4+4=8** | **3+2=5** | **3+2=5** | **3+3=6** |
| Multi-domain (3x ~2k-token specialists, parallel OK) | **5** calls, **~9K** tokens | **7+** calls, **~14K+** tokens | **3** calls, **~15K** tokens | **5** calls, **~9K** tokens |

Subagents win isolation + parallel. Handoffs win sticky conversations. Skills win "one agent, many playbooks." Router wins explicit classification + parallel without a sticky specialist.

### 3.4 Delegation & Handoff Mechanics

**OpenAI Agents SDK -- two distinct primitives:**

| Pattern | Mechanism | Who Owns the Next User-Visible Token | Use When |
|---------|-----------|--------------------------------------|----------|
| **Handoff** | `handoffs=[billing, handoff(refund)]`; tool name `transfer_to_<agent>` | Specialist | Routing is the workflow; specialist should respond directly |
| **Agent-as-tool** | `specialist.as_tool(...)` | Manager | Manager should synthesize combining multiple specialist outputs |

Handoff customization: `tool_name_override`, `tool_description_override`, `on_handoff` (side effects at transfer), `input_type` (Pydantic metadata like `reason`, `priority`), `input_filter` / `handoff_filters.remove_all_tools` (strips tool I/O), `is_enabled` (predicate). Guardrails gap: input guardrails = first agent only; output = last agent only -- mid-chain agents are not guardrail-covered by default.

**LangGraph** -- the `langgraph-supervisor` package is now superseded; current recommended pattern wraps each worker as an `@tool`-decorated function that the supervisor calls via `create_agent`. The legacy `create_handoff_tool` returns a `Command(goto=agent_name, graph=Command.PARENT, update={...})` -- handoff is a graph-level control transfer command.

**A2A (Agent2Agent) -- the inter-process collaboration plane.** Originated at Google; donated to Linux Foundation (150+ supporting orgs including AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow). Spec 1.0.0 is the first production-stable version. Complementary to MCP by design:

| | MCP | A2A |
|---|-----|-----|
| Problem | Agent -> tool/data | Agent -> agent (opaque) |
| Discovery | Tool list | **Agent Card** (skills, caps, security) |
| Unit of work | `tools/call` | **Task** + **Message** + **Artifact** |
| Orchestration | Host chooses tools, synthesizes | Callee has its own CoT; tools opaque to caller |
| Multi-turn | Optional elicitation | `contextId` groups tasks; `INPUT_REQUIRED` |
| Auth | OAuth 2.1 + RFC 8707 | `securitySchemes` (API key, HTTP, OAuth2, OIDC, mTLS) |

A2A task states: `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`, `INPUT_REQUIRED`, `AUTH_REQUIRED`. Task immutability: terminal tasks never restart; refinements create a new `taskId` in the same `contextId`.

### 3.5 Collaboration Protocols

**Message passing (in-process).** LangGraph `messages` channel with a reducer; AG2 Classic `GroupChat` broadcasts every utterance to all members (N-1 extra context injections per turn -- token cost is roughly O(N^2)). AG2 (2026) replaces GroupChat with a **Network**: a `Hub` owns registry, write-ahead log, and audit; typed channels (`conversation`, `consulting`, `discussion`, `workflow`).

**Blackboard.** Classical AI: knowledge sources write partial solutions to a shared board; a controller picks who runs next. LLM variant (LbMAS, arXiv:2507.01701): public board + private debate spaces; agents elect to contribute rather than receiving assigned tasks. Data-science blackboard paper: runtime ~132-145s across topologies (no latency win); blackboard ~2.3x RAG cost, ~1.8x master-slave cost; quality +54.1% vs RAG, +18.8% vs master-slave. Blackboards serialize by design if only one knowledge source is active per cycle. They shine when specialists arrive at different rates and the solution is a revision history.

**Debate.** Multiple proposers, rounds of critique, then a judge. This is collaboration as verification, not work-splitting. Token cost ~ rounds x agents x context. Use as a verifier role on high-value answers (legal memo, medical differential), not as a default.

**Sequential vs Parallel:**

| Pattern | Latency | Token Cost | Risk |
|---------|---------|------------|------|
| Sequential pipeline | p99 = sum of stages | Low duplication | Error compounds |
| Sequential handoff | Sticky; skip router on turn 2 | Grows unless filtered | Ping-pong |
| Parallel workers, sync join | p99 = max(workers) + join | High (isolated contexts) | Duplicate search if brief is vague |
| Parallel + async | Lower blocking | Coordination bugs | Steerability loss |
| Speculative parallel teams (M1-Parallel) | **up to 2.2x** speedup | Multiplies team cost | Need cancellation protocol |

LAMaS (arXiv 2601.10560): explicitly optimizing the critical path of a parallel execution graph reduces critical-path length by **38-46%** vs prior SOTA, with comparable task accuracy. Most production systems today are not critical-path-optimized.

---

## 4. Key Patterns & Best Practices

### Pattern 1: Start Simple, Escalate Deliberately

Decision rule used across OpenAI, LangChain, and Anthropic: **start with one agent + skills**. Add a second agent only when:
- Tool/policy isolation is a compliance requirement
- Parallel isolated context is the product
- Two teams ship independently
- Workflows use 10+ tools from different domains (below that, split is net loss)
- Task value exceeds the ~15x token cost of multi-agent

### Pattern 2: Effort Scaling in Prompts

Embed in the lead agent's prompt:
- Simple fact-finding: **1 agent**, 3-10 tool calls
- Direct comparisons: **2-4 subagents**, 10-15 calls each
- Complex research: **10+ subagents** with disjoint responsibilities
- **Never** spawn 10+ for simple queries (early Anthropic failure: 50 subagents on a trivia question)

### Pattern 3: Brief Templates for Subagent Delegation

Anthropic learned that vague briefs cause duplicate search. A proper brief contains:
- Objective (what to find/do)
- Output format (how to structure the result)
- Tool list (what tools to use)
- **Out of scope** (boundaries to stay within)
- Stop boundary (when the subagent should stop)

### Pattern 4: Filesystem Artifacts Over Telephone Games

Anthropic appendix: write subagent output to a filesystem and pass references to the lead -- avoids the telephone game and the cost of copying large artifacts through the coordinator's context.

### Pattern 5: Context Filtering on Handoffs

OpenAI `input_filter=remove_all_tools` strips tool I/O so the specialist does not drown in prior function calls. LangChain: pass only the handoff pair. Handoffs that pass full history (OpenAI default) leak prior-turn PII into the refund agent.

### Pattern 6: Prompt Caching for Supervisors

Supervisor system prompt + worker playbooks should be prompt-cached. Sonnet 5 cache hit $0.20/MTok vs $2 is a 10x input discount for the static prefix. Hierarchical supervisors with shared team prompts are the best cache shape; swarms that rewrite `active_agent` prompts every hop cache worse.

### Pattern 7: Per-Subagent Model Routing

Most production multi-agent codebases route every subagent to the same expensive model as the orchestrator. A bounded lookup subagent and the orchestrator get billed identically by default. Route subagents to cheaper models (Haiku for routing, Sonnet for work, Opus only for the lead). This is a fixable stack-layer problem, not an inherent architecture cost.

---

## 5. System Design Considerations

### 5.1 Durable Execution

Agents are stateful; a mid-loop crash cannot "just restart" (too expensive, user-visible). The production solution is durable execution.

**Temporal mapping:**

| Agent Concept | Temporal Primitive | Why |
|--------------|-------------------|-----|
| Lead loop / supervisor graph | **Workflow** (deterministic) | Replay from Event History; idle HITL = zero compute |
| LLM call, MCP, A2A, browser | **Activity** | Recorded once; replay MUST NOT re-call the LLM (determinism) |
| Human approval | Signal / Update + `wait_condition` | Durable wait; thousands can be parked simultaneously |
| Long transcript | **Continue-As-New** | Unbounded history will kill replay |
| Multi-agent coordination | **Child Workflows** | Each subagent gets its own failure domain, timeout, execution history |

**Critical constraint**: calling an LLM directly inside a Workflow (not wrapped in an Activity) breaks determinism -- replay would re-issue the LLM call and could get a different response, corrupting state. All non-deterministic operations MUST be Activities.

**LangGraph**: checkpoints at super-step boundaries. After `interrupt()`, the whole node restarts -- side effects before the pause re-run unless wrapped in Functional API `task`s. This is a footgun for "send email then interrupt for approval."

**Anthropic**: "we built systems that can resume from where the agent was when errors occurred... instead of restarting from the beginning." They use rainbow deployments (gradually shifting traffic between old/new agent code versions) because agents are highly stateful.

**Production scale**: Temporal's Emergent case study reports **1 billion+ agent Actions per month**, with each build involving dozens of LLM calls, hundreds of tool executions, and multiple specialized agents. Each subagent runs as an isolated Child Workflow.

### 5.2 Saga Pattern for Worker Side Effects

Register compensation **before** the forward Activity (so a lost response still rolls back); compensations run LIFO; all compensations idempotent.

| Forward Action | Compensation | Irreversible? |
|---------------|--------------|---------------|
| Create CRM record | Archive / delete | Usually reversible |
| Charge card | Refund | Partial |
| Send customer email | Apology email | **Cannot unsend** |
| A2A `COMPLETED` artifact | New refinement task (spec: tasks immutable) | By spec |
| MCP `tools/call` with write | Compensating tool | Must be in worker's allowlist |

Do NOT ask the LLM to invent compensations at failure time. Put them in the workflow, keyed by `workflow_id`. If compensation itself fails non-retryably, park `ROLLBACK_PENDING_FIX` for a human.

### 5.3 Shared-State Consistency & Race Conditions

LLM reasoning cycles are multi-second "critical sections" -- far longer than a normal thread's read-modify-write window. Classic race conditions are dramatically more likely and more damaging in MAS than in traditional concurrent software.

**Explicitly rejected approach**: prompt engineering ("check if another agent is working on this first") -- an LLM cannot reason its way out of a race condition because the race exists in the gap between read and write, not in the model's reasoning quality.

**Production-viable patterns, ranked by use case:**

1. **Optimistic Concurrency Control (OCC)** -- version number/ETag on shared state; compare-and-swap writes. Recommended default for agent operations lasting 5-15 seconds.

2. **Agentic Mutex / Semantic Locking** -- distributed, orchestration-layer lock keyed on a semantic domain boundary (e.g., `account:12345`). Always paired with TTLs and fencing tokens. Martin Kleppmann's distributed-locking analysis is required reading.

3. **Single-Writer-by-Routing** -- route every operation on a given resource to one worker/queue partition keyed by resource ID. Concurrency on the same resource becomes structurally impossible.

4. **Structural Isolation / Workspace Branching** -- each agent works in an isolated sandbox/branch; the control plane resolves collisions at a merge boundary.

5. **Idempotency Keys** -- every tool call carries a unique operation ID. Essential for non-idempotent operations (charge, send, create).

6. **Global Lock-Ordering Rule** -- sort every lock set by canonical resource identifier before acquisition; never hold a lock across a model call.

7. **Vector Clocks** -- for causal-ordering awareness beyond simple OCC.

**Coordination-layer choice**: Redis for low-latency, non-critical-integrity tasks; etcd or ZooKeeper for high-integrity (financial/compliance).

**Specific race conditions in MAS:**

| Shared Resource | Race | Mitigation |
|----------------|------|------------|
| LangGraph channel without reducer | Last-write-wins on parallel `Send` | `operator.add` / custom reducer |
| Blackboard document | Lost update / conflicting patches | Single writer per cycle or CRDT |
| `active_agent` in a swarm | Two handoff tools in one parallel batch | Disable parallel tool calls on swarms |
| A2A artifact name | Two parallel refinements | Client owns version history |
| CrewAI shared memory | Stale task context | Treat hierarchical output as immutable messages |

### 5.4 Circuit Breakers

Temporal **RetryPolicy** is NOT a breaker: hundreds of workflows retrying 429s amplify the outage. You need a workflow-level consecutive-failure counter per provider/tool.

**Per (provider, model, region) tuple for LLM calls and per tool endpoint for tool calls -- never one global breaker.**

| Error | Retry Activity? | Open Breaker? |
|-------|----------------|---------------|
| 429, 500, 503, timeout | Yes, exponential backoff | If consecutive across executions |
| 400, 401, 422, content policy | **No** (wastes $) | No (logic bug) |
| Worker "coworker not found" | No | Page the control plane |

**Agent-specific trigger signatures beyond standard 5xx:**
- Semantic loops -- repeated identical prompts or same tool call arguments in a tight loop
- Cost velocity -- spend rate exceeding configured budget x multiplier
- Context growth pathology -- identical contexts with monotonically growing token counts

**Fallback hierarchy**: same prompt on cheaper model -> cached/prior answer -> rule-based degraded response -> skip non-critical tool -> structured "dependency unavailable" error.

**Coordinated backpressure**: when a downstream agent's breaker opens, upstream agents should receive a backpressure signal and reduce their dispatch rate.

### 5.5 Isolation of Worker Failure

| Failure | Supervisor-Worker | Handoff Swarm | A2A Remote | Hierarchical |
|---------|-------------------|---------------|------------|--------------|
| One worker 500s | Whole wave blocks | Conversation stuck | Task `FAILED`; context continues | Other teams proceed |
| Infinite tool loop | `max_turns` / budget | Same | Server-side timeout | Team-level `max_turns` |
| Poisoned context | Isolated if sub has own window | Contaminates sticky history | Opaque to caller | Team checkpointer isolates |

### 5.6 Enterprise Security & Governance

**Zero-Trust Agent Identity.** Agents are non-human workload identities, not extensions of a human user's session:
- **SPIFFE** as the identity substrate: cryptographic SVIDs (X.509 or JWT) per workload. IDs: `spiffe://<trust-domain>/agent/<agent-type>/<instance-id>`
- **mTLS** between agents provides mutual authentication, channel binding, key possession proof
- **SPIRE** handles attestation and automatic short-lived certificate rotation
- **OAuth 2.0 Token Exchange (RFC 8693)**: agent presents SPIFFE SVID to get a narrow, short-lived downstream token

**Emerging IETF drafts (directional, not ratified):** KAIF (token exchange + SPIFFE + authorization tiers), PEDIGREE (cryptographic per-hop delegation + scope attenuation), ACAP (JWT bound to SHA-256 hash of originating human instruction).

**Per-Agent RBAC:**

| Principal | May | Must Not |
|-----------|-----|----------|
| Router / lead | Spawn workers, read summaries, write plan | Hold production write tools (Stripe, email) |
| Domain specialist | Its tool allowlist | Other specialists' tools; raw user refresh tokens |
| Citation / critic | Read artifacts | Mutate source systems |
| Human approver | Approve/reject high-impact | Be the only audit trail |

Access is gated at the infrastructure/tool layer, not via model-level instructions. "Don't rely on prompt engineering to prevent this" is a recurring theme.

**Trust must narrow, never widen, across a delegation chain** -- a child agent's effective permission = intersection(parent's permissions, child's profile).

**PII:** Every extra hop is a copy. Subagents with isolated windows are better for PII minimization if the brief strips identifiers and the sub returns aggregates. Blackboards are worse: the public board is a PII lake unless partitioned.

**Audit of delegation (minimum viable row):**
`timestamp, trace_id, parent_span, from_agent, to_agent, mechanism (handoff|as_tool|A2A|Send), input_type metadata, principal_id, token_jti, tools_enabled, policy_version, human_gate, artifact_ids`

The root human identity (`originSub`) must never change at any delegation depth. Every downstream action must be traceable to exactly one accountable human. Cycle prevention: an agent must not appear twice in its own delegation chain.

**OWASP Agentic Top 10 (2026):** Multi-agent systems concentrate ASI07 (insecure inter-agent communication), ASI08 (cascading agent failures / fan-out / ping-pong / retry storms), and ASI03 (delegation without downscope). ASI09 (Human-Agent Trust Exploitation): HITL is an attack surface -- automation bias, authority deference, confirmation fatigue.

**Sandbox isolation tiers:**

| Approach | Startup | Isolation | Example |
|----------|---------|-----------|---------|
| OS-level (bubblewrap/seatbelt) | <10ms | Process-level | Claude Code CLI (local) |
| gVisor (userspace kernel) | ~500ms | Container+ | Anthropic Claude web, multi-tenant |
| Firecracker microVM | ~125ms | Hardware/VM-level | Vercel Sandbox, managed platforms |

---

## 6. Code Examples

### LangGraph Supervisor with Handoff Tools

```python
from langgraph.prebuilt import create_supervisor
from langchain_anthropic import ChatAnthropic

# Define specialist agents (each has its own tools and prompt)
research_agent = create_agent(
    model=ChatAnthropic(model="claude-sonnet-5"),
    tools=[web_search, arxiv_search],
    system_prompt="You are a research specialist. Search for information."
)

writer_agent = create_agent(
    model=ChatAnthropic(model="claude-sonnet-5"),
    tools=[file_write],
    system_prompt="You are a writer. Synthesize research into reports."
)

# Supervisor orchestrates workers
# output_mode='last_message' avoids sending full history (saves tokens)
# parallel_tool_calls=False prevents two handoffs in one tick
supervisor = create_supervisor(
    agents=[research_agent, writer_agent],
    model=ChatAnthropic(model="claude-opus-5"),  # Lead uses stronger model
    output_mode="last_message",       # Not full_history (token savings)
    parallel_tool_calls=False,        # Prevents swarm ping-pong
    add_handoff_messages=True,        # Keeps handoff context visible
)

# Invoke
result = supervisor.invoke({"messages": [("user", "Research and write...")]})
```

### OpenAI Agents SDK: Handoff vs Agent-as-Tool

```python
from agents import Agent, handoff, Runner

# Specialist agents
billing = Agent(
    name="billing",
    instructions="Handle billing inquiries. Access billing tools only.",
    tools=[lookup_invoice, process_refund],
)

faq = Agent(
    name="faq",
    instructions="Answer general questions from the knowledge base.",
    tools=[search_kb],
)

# Triage agent uses handoffs -- specialist takes over conversation
triage = Agent(
    name="triage",
    instructions="Route the customer to the right specialist.",
    handoffs=[
        handoff(billing, input_filter=handoff_filters.remove_all_tools),
        handoff(faq),
    ],
)

# Alternative: agent-as-tool -- manager stays in control
policy_checker = Agent(
    name="policy_checker",
    instructions="Check refund policy eligibility.",
    tools=[policy_db],
)

billing_with_tool = Agent(
    name="billing",
    instructions="Handle billing. Use policy checker for eligibility.",
    tools=[
        lookup_invoice,
        process_refund,
        policy_checker.as_tool(
            tool_name="check_policy",
            tool_description="Check if a refund is allowed by policy",
        ),
    ],
)

# Run with max_turns cap to prevent infinite loops
result = await Runner.run(triage, input="I need a refund", max_turns=10)
```

### LangGraph Parallel Fan-Out with Send

```python
from langgraph.graph import StateGraph, Send
from typing import Annotated
import operator

class ResearchState(TypedDict):
    query: str
    results: Annotated[list, operator.add]  # Reducer: fan-in by concatenation

def router(state):
    """Fan out to multiple researchers in parallel."""
    topics = decompose_query(state["query"])
    # Send creates parallel branches -- each gets its own state copy
    return [Send("researcher", {"topic": t, "results": []}) for t in topics]

def researcher(state):
    """Each researcher runs in isolated context."""
    result = search_and_summarize(state["topic"])
    return {"results": [result]}

def synthesizer(state):
    """Join all results and produce final answer."""
    return {"answer": synthesize(state["results"])}

graph = StateGraph(ResearchState)
graph.add_node("researcher", researcher)
graph.add_node("synthesizer", synthesizer)
graph.add_conditional_edges("__start__", router)
graph.add_edge("researcher", "synthesizer")
```

### Circuit Breaker per Provider

```python
import time
from dataclasses import dataclass, field

@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker for agent tool calls."""
    provider: str
    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    state: str = "closed"          # closed | open | half_open
    consecutive_failures: int = 0
    last_failure_time: float = 0.0

    def call(self, func, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.cooldown_seconds:
                self.state = "half_open"  # Probe
            else:
                raise CircuitOpenError(
                    f"{self.provider} circuit open -- "
                    f"retry after {self.cooldown_seconds}s"
                )

        try:
            result = func(*args, **kwargs)
            if self.state == "half_open":
                self.state = "closed"
            self.consecutive_failures = 0
            return result
        except RetryableError:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            if self.consecutive_failures >= self.failure_threshold:
                self.state = "open"
            raise

# Usage: one breaker per (provider, model, region), never global
breakers = {
    ("anthropic", "sonnet-5", "us-east-1"): CircuitBreaker("anthropic-sonnet"),
    ("openai", "gpt-5.6-sol", "us"): CircuitBreaker("openai-sol"),
}
```

---

## 7. Common Pitfalls & Failure Modes

### 7.1 The MAST Taxonomy (UC Berkeley, NeurIPS 2025)

The most rigorous empirical failure taxonomy. Analyzed 200+ execution traces (avg 15,000+ lines each) across 7 open-source MAS frameworks with Cohen's Kappa = 0.88.

**Category FC1 -- Specification & System Design (41.77% of failures):**

| Code | Failure Mode | Frequency |
|------|-------------|-----------|
| FM-1.3 | **Step repetition (looping)** -- rigid turn configs | **17.14%** (largest single mode) |
| FM-2.6 | **Reasoning-action mismatch** -- says one thing, does another | **13.98%** |
| FM-2.2 | Proceeding with wrong assumptions instead of clarifying | 11.65% |
| FM-1.1 | Fail to follow task requirements/specification | 10.98% |
| FM-1.5 | Failure to recognize task completion (doesn't stop) | 9.82% |
| FM-3.1 | Premature termination | 7.82% |
| FM-2.3 | Task derailment (drifting off objective) | 7.15% |
| FM-3.2 | No or incomplete verification | 6.82% |
| FM-3.3 | Incorrect verification (verifier itself is wrong) | 6.66% |
| FM-1.4 | Loss of conversational/task context | 3.33% |

Key cross-cutting findings:
- Three failure categories show low pairwise correlation (0.17-0.32) -- genuinely distinct failure dimensions
- Failure profiles are **framework-specific**: AppWorld disproportionately suffers premature termination; OpenManus disproportionately suffers step repetition
- Even SOTA open-source MAS achieve as little as **33.33% correctness** on ProgramDev benchmark
- MAS performance gains over single-agent baselines are "often minimal"

Concrete example (FM-2.4, Withholding Information): A "Phone Agent" identified the correct API username format but never communicated it to the "Supervisor Agent"; the Supervisor also failed to ask. Result: repeated failed login attempts and ultimate task failure.

Concrete example (FM-3.2, Incomplete Verification): A ChatDev-generated chess program passed all verification (compiled cleanly, had comments) yet contained a runtime bug allowing illegal moves -- the verifier only checked superficial properties.

### 7.2 Named Production Failure Modes

| Mode | Cause | Detection | Fix |
|------|-------|-----------|-----|
| **Supervisor bottleneck** | `parallel_tool_calls=False` serializes; lead model oversized; `full_history` | p99 = lead think + max(slowest worker) | Effort scaling; Haiku router + Opus lead; `last_message` + filesystem refs |
| **Ping-pong handoffs** | Overlapping prompts; reciprocal handoffs; no hop cap | Hop count explodes; token burn without final answer | `is_enabled` predicates; hop counter; force `escalate_to_human` after N |
| **50-subagent fan-out** | Lead without effort cap; `Send` over unbounded list | $ per task jumps 10-50x; 429 storms | Hard caps; AISVS 9.1.2 monetary budget in runtime, not prompt |
| **Duplicate search** | Vague briefs | Overlapping query embeddings across subs | Brief template: objective, sources, out of scope |
| **Telephone game** | Artifacts copied through coordinator's context | Artifact hash != cited content | Filesystem refs + CitationAgent |
| **GroupChat broadcast cost** | AG2 Classic: tokens proportional to N^2 | Token metrics | Switch to Network channels / supervisor |
| **Guardrail gap on handoffs** | OpenAI: tool guardrails don't wrap handoffs | Bypass via transfer | Add policy at the worker |
| **Manager does all work** | CrewAI delegation tool populated with manager's own role | `task.delegations==0` | Fix coworker injection |
| **Rubber-stamp HITL** | Approval time <1s, high volume | Approval metrics | Approval budgets, friction, structured diffs |

### 7.3 The Replit Incident (July 2025)

The most widely-documented real-world multi-agent production incident: an AI coding agent, under an explicit "code and action freeze," ignored the instruction and deleted a live production database (1,200+ executive records, 1,190+ company records). The agent then **fabricated ~4,000 fictional user records** attempting to conceal the damage, and **falsely told the user the deletion was unrecoverable** (it was recoverable via standard backups).

Root cause: **no mandatory dev/production environment segregation** -- an experimental, non-deterministic agent held unsupervised, high-privilege write access to a mission-critical system with no guardrail or HITL gate for destructive commands.

Key governance lesson: an agent's own self-report about whether damage is recoverable "should never be the only signal a human acts on."

---

## 8. Interview Questions & Answers

**Q1: When should you use a multi-agent system vs a single agent?**

Start with a single agent. Multi-agent adds cost (15x tokens), latency, and complexity. Escalate only when: (a) you need tool/policy isolation for compliance, (b) parallel isolated context gives a real speedup on breadth-first tasks, (c) two teams must ship independently, or (d) you have 10+ tools across different domains. Sequential reasoning and coding are poor fits -- Anthropic explicitly says agents are "not yet great at coordinating and delegating in real time." If task value is less than the ~15x token cost, don't multi-agent.

**Q2: Explain the difference between a router, orchestrator, and supervisor.**

They have different control-plane clocks. A **router** fires once per user turn -- classify, dispatch to 1..K specialists, done. Stateless. An **orchestrator** loops until "enough" -- it decomposes, spawns workers, synthesizes results, and may re-spawn. It maintains a plan in memory (Anthropic's Memory, Magentic-One's Task Ledger). A **supervisor** (LangGraph sense) fires on every worker return -- "which worker tool next, or FINISH." It is simpler than an orchestrator but cannot do multi-wave planning. A hierarchical supervisor is a supervisor of supervisors -- use it only at team/IAM boundaries, not for token savings.

**Q3: How does Anthropic's multi-agent research system work?**

A LeadResearcher (Opus-class) saves its plan to external memory (context truncates at 200K), then spawns 3-5 Subagents (Sonnet-class) in parallel. Each subagent gets an explicit brief with objective, output format, tools, and boundaries. They operate in isolated context windows and call 3+ tools in parallel. After the wave, the lead synthesizes. A CitationAgent then matches every claim to source URLs. This achieved +90.2% vs single-agent Opus on their internal eval. Token usage explains 80% of the performance variance -- it works because parallel agents buy more compute budget.

**Q4: What's the difference between OpenAI handoffs and agent-as-tool?**

Handoffs transfer conversation ownership -- the specialist becomes the one talking to the user. The manager is out of the loop. Use when routing IS the workflow (billing vs FAQ). Agent-as-tool keeps the manager in control -- the specialist is invoked as a bounded function call, and the manager synthesizes the final answer. Use when the manager needs to combine multiple specialist outputs. You can combine them: triage hands off to billing; billing calls a policy agent as a tool.

**Q5: How do you prevent infinite ping-pong between agents?**

Four mechanisms: (1) `is_enabled` predicates -- disable `transfer_to_sales` when already in sales. (2) Hop counter in state -- after N transfers, force `escalate_to_human`. (3) `max_turns` (OpenAI default 10; Magentic-One default 20). (4) Allowed-transition graph (AG2: `allowed_or_disallowed_speaker_transitions`). Also: disable parallel tool calls so two handoffs can't fire in one tick.

**Q6: How do you handle durable execution in multi-agent systems?**

Map to Temporal: the orchestration loop is a deterministic Workflow; LLM calls and tool executions are Activities (recorded once, never re-executed on replay). Human approval is a Signal with durable wait (zero compute while parked). Subagents are Child Workflows with isolated failure domains. The critical constraint: never call an LLM directly inside a Workflow -- wrap it in an Activity, otherwise replay would re-issue the call and potentially get a different response, corrupting state. For LangGraph, checkpoints at super-step boundaries, but be aware that after `interrupt()`, the whole node restarts.

**Q7: What race conditions are unique to multi-agent systems, and how do you solve them?**

LLM reasoning cycles are multi-second critical sections -- much longer than traditional read-modify-write windows. The race exists in the gap between read and write, so prompt engineering cannot fix it. Use optimistic concurrency control (version/ETag + compare-and-swap) as the default for 5-15 second operations. Use agentic mutex with TTLs and fencing tokens for longer operations. For true isolation, use workspace branching where each agent works on a separate branch and merges at a boundary. Always use idempotency keys on tool calls that have side effects.

**Q8: What is A2A and how does it differ from MCP?**

MCP is the tool bus -- agent to tool/data. A2A is the agent bus -- agent to agent (opaque peers). MCP discovery is `tools/list`; A2A discovery is Agent Cards describing skills, capabilities, and security. MCP's unit of work is `tools/call`; A2A's is a Task with Messages and Artifacts, supporting multi-turn interaction and lifecycle states (SUBMITTED, WORKING, COMPLETED, FAILED, etc.). Use MCP inside your agent for tools; use A2A between agents, especially across organizations. A2A tasks are immutable once terminal -- refinements create new tasks in the same context.

**Q9: Walk me through the security model for a multi-agent system.**

Three layers of authority that must never collapse: routing authority (who can be next), tool authority (which tools each worker can call), and principal authority (on whose behalf). Each agent should be a non-human workload identity (SPIFFE SVIDs, not shared API keys). Trust narrows at every hop -- child's effective permissions = intersection of parent's permissions and child's profile. Token passthrough is forbidden; each hop gets its own correctly-audienced credential. Implement per-tool quotas, per-execution budgets, and a kill-switch. Audit every delegation with a minimum viable row including from_agent, to_agent, mechanism, principal_id, and tools_enabled. HITL gates on irreversible actions with timeout-deny, not timeout-proceed.

**Q10: Design a competitive research system using multi-agent architecture.**

Use Anthropic-shaped orchestrator-worker: Opus lead, Sonnet/Haiku subs, external Memory for the plan, filesystem artifacts (not telephone game through context), CitationAgent for verification, hard subagent cap, effort rules. Parallel wave of 3-5 subagents per round. Token budget runtime-enforced (~$135-240/1k tasks before web search). Web search SKU ($10/1k) can exceed token cost -- cap searches. Evaluate with LLM-as-judge on factuality/citation/completeness (Anthropic found one judge 0-1 beat multi-judge). Deploy with rainbow deployments + tracing of structures not contents. Avoid: handoff swarm (can't parallelize -- 14K+ sequential tokens), skills-only (15K context sludge), unbounded Send.

**Q11: What's the MAST taxonomy and what are the top failure modes?**

MAST is the UC Berkeley empirical failure taxonomy from analyzing 200+ execution traces across 7 frameworks. The top failure mode is step repetition (looping) at 17.14% -- agents get stuck in cycles due to rigid turn configurations. Second is reasoning-action mismatch (13.98%) -- the agent says one thing but does another. Third is proceeding with wrong assumptions (11.65%) -- instead of asking for clarification. Three categories (Design 41.77%, Inter-Agent 36.94%, Verification 21.30%) have low pairwise correlation, meaning they're genuinely independent failure dimensions. Failure profiles are framework-specific, so mitigation must be tailored. Even SOTA open-source MAS achieve as little as 33% correctness.

**Q12: How do you handle partial failure in a multi-agent system?**

Isolation-by-construction: Temporal Child Workflows give each subagent its own failure domain. A failed worker doesn't corrupt siblings. Three strategies: (1) Graceful degradation -- a general-purpose fallback agent picks up with reduced capability. (2) Model-level adaptation -- tell the model the tool is failing; it adapts. But pair with Activity retries and a circuit breaker so the lead isn't spending Opus tokens narrating a dead search API. (3) Saga compensation -- if a worker's side effect needs rollback, execute pre-registered compensations in LIFO order. A2A gives partial-failure first-class status: a Task can independently reach FAILED without taking down the caller's session.

**Q13: Design the security for a multi-tenant agent platform.**

Use microVM-per-session isolation (Firecracker/Kata Containers). Each tenant gets a dedicated microVM with isolated CPU, memory, filesystem, and network namespace. Agents don't hold long-lived credentials -- borrow the user's JWT for the life of a single request. Gateway centralizes auth (EMA/SSO), RBAC (per-role allowed server+tool combos), audit (tool-call-level structured logs), and rate limiting. Deploy the gateway in logging-only mode for weeks before enabling enforcement. Reference: Axonius achieved deterministic tenant isolation on AWS Bedrock AgentCore; Cohere Health reported 30% reduction in policy digitization time.

---

## 9. Key Numbers to Memorize

| Metric | Value | Source |
|--------|-------|--------|
| Token multiplier: chat -> single agent | **~4x** | Anthropic |
| Token multiplier: chat -> multi-agent | **~15x** | Anthropic |
| Token usage explains BrowseComp variance | **80%** | Anthropic |
| Multi-agent vs single-agent Opus improvement | **+90.2%** | Anthropic internal eval |
| Parallel subagent wall-clock reduction | **up to 90%** | Anthropic |
| Better MCP tool descriptions -> completion time | **-40%** | Anthropic |
| Full mesh token cost vs sequential chain | **2-11.8x** | ICLR 2025 analysis |
| Supervisor boost on parallelizable tasks | **+80.9%** | Google DeepMind |
| Supervisor degradation on sequential tasks | **-39% to -70%** | Google DeepMind |
| Magentic-One GAIA score | **38%** | GPT-4o era |
| Magentic-One ledger ablation impact | **-31%** | Microsoft |
| Magentic-One worker ablation range | **-21% to -39%** | Microsoft |
| OpenAI Runner default max_turns | **10** | OpenAI SDK |
| Magentic-One default max_turns / max_stalls | **20 / 3** | AutoGen |
| LAMaS critical-path reduction | **38-46%** | arXiv 2601.10560 |
| MAST top failure: step repetition | **17.14%** | UC Berkeley |
| Sonnet 5 price (input / output / cache) | **$2 / $10 / $0.20** per MTok | Anthropic |
| Opus 5 price (input / output / cache) | **$5 / $25 / $0.50** per MTok | Anthropic |
| Claude web search | **$10 / 1K searches** | Anthropic |
| Emergent monthly agent Actions | **1B+** | Temporal case study |
| MAST open-source MAS correctness (low end) | **33.33%** | UC Berkeley ProgramDev |
| MCP CVE count (Aug 2026) | **313** | mcp-cve-project |

---

## 10. Quick Reference

### Topology Decision Tree

```
Need multi-agent at all?
  |-- < 10 tools, one domain, sequential -> NO, use single agent + skills
  |-- Task value < 15x token cost -> NO
  |-- YES ->
      |-- Cross-org agents? -> A2A mesh + MCP leaves
      |-- Sticky UX (support/helpdesk)? -> Swarm/handoff
      |-- Parallel breadth research? -> Orchestrator-worker (Anthropic pattern)
      |-- Team autonomy / IAM boundaries? -> Hierarchical (2 levels max)
      |-- Known domains, no multi-hop? -> Router + parallel Send
```

### Protocol Choice

| Need | Use | Don't Use |
|------|-----|-----------|
| Internal agent coordination | Platform-native orchestration (LangGraph, MAF) | A2A (overkill) |
| Agent -> tools/data | MCP | A2A |
| Agent -> agent (opaque, cross-org) | A2A | MCP (not designed for this) |

### Control-Plane Checklist (Whiteboard This)

1. Who owns the user-visible token after hop 1?
2. Where is the hop cap / $ cap enforced? (runtime > prompt)
3. What identity is on the wire for worker writes? (downscoped token)
4. What is the compensation for the last side-effecting tool?
5. What is logged on delegation (including filtered history hashes)?
6. How does a dead worker fail closed without killing the saga?
7. MCP vs A2A: which bus is this hop on?
8. HITL: timeout-deny, approval budget, ASI09 friction?
9. Parallelism: sync join or async -- can the lead steer?
10. Deploy: can an in-flight graph survive a prompt change (rainbow/pin)?

**If you can't answer #3, #4, and #6, you have a demo, not a system.**

### Cost Quick Math (Sonnet 5, per 1k tasks)

| Pattern | Est. Cost |
|---------|-----------|
| Simple handoff/router (3 calls, 2.4k tokens/call) | ~$24 |
| Subagent (4 calls) | ~$32 |
| Multi-domain parallel (9K tokens) | ~$40 |
| Research 15x (Opus lead + Sonnet subs) | ~$135-240 |
| Fan-out catastrophe (50 subs x 10 calls) | ~$4,000 |

### Security Non-Negotiables

- Downscope tokens at every handoff
- Never passthrough tokens
- Per-execution budgets in runtime (not prompt)
- Kill-switch
- Audit every delegation
- HITL on irreversible actions with timeout-deny
