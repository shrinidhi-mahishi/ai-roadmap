# Module 09: Multi-Agent Architecture

### What Is This?

Multi-agent architecture is the design discipline for systems where multiple LLM-powered agents collaborate to accomplish tasks too large, too complex, or too sensitive for a single agent. Instead of one agent doing everything, you decompose work across specialists -- a lead agent plans and synthesizes while workers execute in parallel, or agents hand off control to each other based on domain. The core tension is between the quality gains from specialization and parallelism versus the compounding cost of reliability decay, token amplification, and coordination overhead. In practice, multi-agent is never the default: you only reach for it when a single agent hits a hard limit on context size, needs true parallelism, or requires role/tool/permission isolation.

---

## 1. System Topology & Data Flow

A production multi-agent system spans six cooperating layers: a **control plane** routing requests to the correct topology and enforcing agent-level resource budgets; an **agent coordination plane** managing inter-agent communication, handoffs, and topology-specific routing; an **agent execution plane** where individual agents run their reasoning loops with scoped tool access and isolated context; a **tool proxy layer** mediating all external tool calls through MCP with per-agent allowlists and circuit breakers; a **persistence layer** checkpointing agent state, coordination metadata, and provenance records; and a **telemetry layer** correlating traces across agent boundaries.

```
+----------------------------------------------------------------------------------+
|                              CONTROL PLANE                                        |
|                                                                                   |
|  +---------------------+  +-----------------------+  +------------------------+  |
|  | Topology Selector    |  | Agent Budget Enforcer |  | A2A Gateway            |  |
|  |                      |  |                       |  |                        |  |
|  | Decision tree:       |  | Three-level caps:     |  | Signed Agent Card      |  |
|  |  Context overflow?   |  |  L1: step ceiling     |  | verification.          |  |
|  |   -> parallel split  |  |      (25 default)     |  | Multi-tenant routing.  |  |
|  |  Independent tasks?  |  |  L2: token budget     |  | Version negotiation    |  |
|  |   -> orch-worker     |  |      (100K default)   |  | (v0.3 <-> v1.0).      |  |
|  |  Sequential + audit? |  |  L3: dollar ceiling   |  | Protocol bindings:     |  |
|  |   -> pipeline        |  |      ($5.00 default)  |  |  JSON-RPC + gRPC.     |  |
|  |  80+ domains?        |  |                       |  |                        |  |
|  |   -> hierarchical    |  | Any breach ->         |  | External agents enter  |  |
|  |  None of the above?  |  | terminate + log       |  | system here.           |  |
|  |   -> single agent    |  |                       |  |                        |  |
|  +----------+-----------+  +----------+------------+  +----------+-------------+  |
+-------------+---------------------------+----------------------------+-----------+
              | topology + config         | budget envelope            | verified identity
+--------------v--------------------------v----------------------------v-----------+
|                       AGENT COORDINATION PLANE                                    |
|                                                                                   |
|  +------------------------+  +---------------------+  +------------------------+ |
|  | Orchestrator / Router   |  | Message Bus          |  | Handoff Manager        | |
|  |                         |  |                      |  |                        | |
|  | Supervisor pattern:     |  | Typed schemas        |  | Explicit control       | |
|  |  decompose(task)        |  | (Pydantic) at every  |  | transfer between       | |
|  |  -> assign(worker_i)   |  | boundary.            |  | agents via function    | |
|  |  -> collect(results)   |  |                      |  | returns.               | |
|  |  -> synthesize()       |  | Patterns:            |  |                        | |
|  |                         |  |  Shared state        |  | Runner maintains       | |
|  | Workers invoked as      |  |  (Redis/DB)          |  | shared conversation    | |
|  | tool calls -- no        |  |  Direct messages     |  | history across all     | |
|  | inter-worker comms.     |  |  Blackboard writes   |  | handoffs.              | |
|  |                         |  |  Tool-call delegn    |  |                        | |
|  +-----------+-------------+  +----------+----------+  +-----------+------------+ |
+--------------+---------------------------+----------------------------+-----------+
               | scoped task               | typed messages             | control token
+--------------v--------------------------v----------------------------v-----------+
|                        AGENT EXECUTION PLANE                                      |
|                                                                                   |
|  +----------------+  +----------------+  +----------------+  +------------------+ |
|  | Worker Agent    |  | Worker Agent    |  | Worker Agent    |  | Verifier Agent   | |
|  | (Specialist)    |  | (Specialist)    |  | (Specialist)    |  | Schema valid.    | |
|  | Own model.      |  | Own model.      |  | Own model.      |  | Confidence +     | |
|  | Own tools       |  | Own tools       |  | Own tools       |  | groundedness +   | |
|  | (allowlisted).  |  | (allowlisted).  |  | (allowlisted).  |  | completeness.    | |
|  | Own sandbox.    |  | Own sandbox.    |  | Own sandbox.    |  |                  | |
|  | Isolated ctx.   |  | Isolated ctx.   |  | Isolated ctx.   |  |                  | |
|  +--------+-------+  +--------+-------+  +--------+-------+  +--------+---------+ |
+-----------+-------------------+--------------------+-----------------------+------+
            |                   |                    |                       |
+-----------v-------------------v--------------------v-----------------------v------+
|                          TOOL PROXY LAYER (MCP)                                   |
|                                                                                   |
|  +------------------+  +-------------------+  +-----------------+  +------------+ |
|  | Per-Agent Tool    |  | Per-Backend       |  | Idempotency     |  | Result     | |
|  | Allowlist         |  | Circuit Breaker   |  | Guard           |  | Size Cap   | |
|  |                   |  |                   |  |                 |  | (100KB)    | |
|  | Agent A: [search, |  | CLOSED -> OPEN    |  | Key = (thread,  |  | Truncate   | |
|  |  read_file]       |  | -> HALF_OPEN      |  | worker, tool,   |  | oversized  | |
|  | Agent B: [db_qry] |  | -> CLOSED         |  | args_hash)      |  | responses. | |
|  | Agent C: [email]  |  |                   |  |                 |  |            | |
|  +------------------+  +-------------------+  +-----------------+  +------------+ |
+----------------------------------+------------------------------------------------+
                                   |
+----------------------------------v------------------------------------------------+
|                         PERSISTENCE LAYER                                          |
|                                                                                    |
|  +---------------------+  +-----------------------+  +---------------------------+ |
|  | Agent Checkpoints    |  | Coordination State     |  | Provenance Log           | |
|  |                      |  |                        |  |                          | |
|  | LangGraph: full      |  | Task decomposition     |  | Append-only:             | |
|  |  state each node.    |  | tree. Worker assigns.   |  |  agent_id, action,       | |
|  |  DeltaChannel for    |  | Completion status.      |  |  timestamp, input_hash,  | |
|  |  incremental deltas  |  | Handoff history.        |  |  output_hash,            | |
|  |  (beta, 41x less).   |  | Budget consumed.        |  |  parent_span_id.         | |
|  |                      |  |                        |  |                          | |
|  | Temporal: event log  |  |                        |  | Satisfies SOC 2 CC6.1   | |
|  |  + deterministic     |  |                        |  | + pipeline audit trail.  | |
|  |  replay.             |  |                        |  |                          | |
|  +---------------------+  +-----------------------+  +---------------------------+ |
+----------------------------------+------------------------------------------------+
                                   |
+----------------------------------v------------------------------------------------+
|                  TELEMETRY / OBSERVABILITY LAYER                                   |
|                                                                                    |
|  OpenTelemetry GenAI semantic conventions:                                         |
|    invoke_agent | execute_tool | create_agent | invoke_workflow spans               |
|  W3C Trace Context propagation via MCP _meta field (SEP-414)                      |
|  Per-agent token consumption + latency | Cross-agent trace correlation            |
|  Budget burn rate vs ceiling alerts | Context drift detector (objective hash diff) |
|  Cascading failure propagation tracker across agent boundaries                    |
+-----------------------------------------------------------------------------------+
```

**Protocol boundary.** MCP connects agents to tools/data (vertical). A2A (Agent-to-Agent Protocol v1.0) is the emerging agent-to-agent interoperability layer across vendors (horizontal). Do not treat MCP as peer agent messaging.

### Six Canonical Topologies

Two families, six topologies. Each has a distinct state machine.

**Chain-of-command family** (predictable, auditable):

| # | Topology | How It Works | Framework Examples | Complexity |
|---|----------|-------------|-------------------|------------|
| 1 | **Orchestrator-Worker** (Hub-and-Spoke) | Central lead decomposes, workers run in parallel, no worker-to-worker chat, results return to lead for synthesis. ~70% of production deployments. | Anthropic Research; OpenAI agents-as-tools; ADK AgentTool/ParallelAgent; LangGraph supervisor | O(max(T_worker)) + O(T_orch) wall-clock. Bottleneck: ~3s/call, ~7 tasks/sec ceiling with 20 workers. |
| 2 | **Pipeline / DAG** | Fixed stage order. Each agent's output is next agent's input with schema validation. Strongest audit trail. | ADK SequentialAgent; Stripe agent DAG; CrewAI Process.sequential | O(sum(T_stage_i)) -- strictly additive. 5 stages x 2s = 10s minimum. |
| 3 | **Hierarchical** (Tree) | Minimum 2 levels. Top manager -> mid-level managers -> workers. No level-skipping. No single agent holds full context. | Nested LangGraph supervisors; ADK multi-level sub_agents; IBM watsonx Orchestrate (80+ domain agents) | O(depth * T_avg) per direction. Information loss at each level. |

**Decentralized family** (harder to debug, more resilient to partial failures):

| # | Topology | How It Works | Framework Examples | Risk |
|---|----------|-------------|-------------------|------|
| 4 | **Swarm / Peer Handoff** | Agents hand off control via function returns. Active agent resumes on next turn. Requires checkpointer for `active_agent`. | LangGraph Swarm; OpenAI handoffs; OpenAI Agents SDK | Default shared messages merges all transcripts -- privacy/token risk. |
| 5 | **Mesh** | Fully connected peer-to-peer. Every agent can communicate with every other. Most dangerous: 17x error amplification risk (Google DeepMind). | Research/debate scenarios only | O(N^2) communication paths. Convergence-dependent latency. |
| 6 | **Blackboard** | Shared store (Redis/DB/vector), no direct peer messages. Agents post partial results; coordinator reads when converged. | Redis/DB/vector blackboard patterns | Race conditions at scale. Stale reads with N writers > 1. |

### Communication Pattern Algorithms

| Pattern | Algorithm | Trade-off |
|---------|-----------|-----------|
| **Shared State** | read(key) -> process -> write(key, result). Concurrency: optimistic locking or CAS. | Simple but race conditions at scale. Stale reads with N > 1 writers. |
| **Message Passing** | send(agent_id, typed_msg) via orchestrator or A2A. Each hop validates schema. | Clean boundaries. Higher latency per hop. O(hops) added latency. |
| **Blackboard** | Agents post partial results to shared surface. Coordinator reads when convergence condition met. | Good for heterogeneous agents. Requires conflict resolution protocol. |
| **Tool-Call Delegation** | Orchestrator invokes worker as tool_call(name, args). Worker returns structured output. | Cleanest isolation. Worker is a black box. Orchestrator is single point of failure. |

### Interoperability: MCP vs A2A

```
+------------------------------------------------------------------+
|                      AGENT LAYER                                  |
|                                                                   |
|  Agent_A <----- A2A Protocol (v1.0) -----> Agent_B               |
|  (Your system)  Horizontal: agent-to-agent  (External system)    |
|                                                                   |
|  Features (v1.0, early 2026):                                    |
|    - Signed Agent Cards (cryptographic identity)                 |
|    - Multi-tenancy (single endpoint, multiple tenant agents)     |
|    - Multi-protocol bindings (JSON-RPC + gRPC)                   |
|    - Version negotiation (v0.3 <-> v1.0 backward compat)        |
|    - SDKs: Python, JavaScript, Java, Go, .NET                   |
|    - 150+ organizations (Linux Foundation since June 2025)       |
|                                                                   |
+-------------------------------------------------------------------+
|                      TOOL LAYER                                   |
|                                                                   |
|  Agent <----- MCP (Model Context Protocol) -----> Tool/Service   |
|               Vertical: agent-to-tool                             |
|  Standardizes tool discovery and invocation.                     |
|  Anthropic (late 2024). Adopted by OpenAI, Google, Microsoft.    |
+-------------------------------------------------------------------+
```

### Request-Flow Narratives

**Path A -- Supervisor / Orchestrator-Worker (lead keeps ownership):**

1. **Ingress** -- Request arrives with goal, tenant id, optional thread_id. Control plane loads checkpoint and remaining session budget.
2. **Lead plans** -- Frontier lead (e.g., Opus-class) decomposes work; persists plan to Memory when context may exceed 200k tokens.
3. **Fan-out** -- Control plane spawns workers under effort rules: simple -> 1 agent; comparisons -> 2-4; complex research -> >10. Prefer 3-5 parallel workers and 3+ parallel tools per worker to cut wall-clock by up to 90%.
4. **Workers execute** -- Isolated contexts. Tool proxies enforce specialist RBAC. Large outputs land in artifact store; workers return lightweight refs (avoid "telephone").
5. **Sync wait** -- Lead waits for the cohort (Anthropic Research pattern; async fan-out flagged as future work).
6. **Synthesize** -- Lead merges; optional citation agent. `output_mode='last_message'` vs `'full_history'` trades tokens vs fidelity.
7. **Persist and egress** -- Checkpoint state; telemetry emits fan-out count, tokens, $, correlation id.

**Path B -- Handoff / Swarm (specialist becomes active agent):**

1. **Ingress** -- Same as Path A; swarm requires a checkpointer so `active_agent` survives the turn.
2. **Active specialist** -- Current agent runs; may call `transfer_to_<name>` returning `Command(goto=..., update={...})`.
3. **Ownership transfer** -- Control plane sets `active_agent`; next user turn resumes that specialist.
4. **Guardrail asymmetry** -- In OpenAI SDK, input guardrails apply only to the first agent; output guardrails only to the agent that produces final output.
5. **Terminate** -- Final specialist answers user, or `max_turns` / `recursion_limit` fires.

Wall-clock: Path A = T_plan + max(T_worker_i) + T_synth (parallel). Path B = sum of serial handoff hops.

### Framework Comparison

| Framework | Topology Model | State Management | Key Differentiator |
|-----------|---------------|-----------------|-------------------|
| **LangGraph v1.1** | Graph-of-nodes with conditional edges; supervisor + swarm patterns | Checkpointed state per super-step; DeltaChannel (beta) for incremental deltas (41x reduction) | Most flexible graph composition; time-travel debugging |
| **OpenAI Agents SDK** | Handoffs (specialist becomes active) + agents-as-tools (manager keeps control) | RunState pause/resume; max_turns=10 default | Native OpenAI model integration; clean handoff semantics |
| **Google ADK 2.0** | SequentialAgent, ParallelAgent, LoopAgent, AgentTool; multi-level sub_agents | SessionService via events/state_delta; temp: invocation-scoped; app: session-scoped | Built-in agent types for common topologies |
| **CrewAI** | Process.sequential and Process.hierarchical | Event-sourced Flows API | High-level abstractions; quick prototyping |
| **Microsoft Agent Framework 1.0** | Autogen-based; group chat and selector patterns | Distributed state with agent runtime | Enterprise integration focus |

---

## 2. Core Mechanics & Math

### When Multi-Agent Is Justified

Multi-agent is never the default. Three hard limits -- and only these three -- justify the transition:

1. **Context overflow** -- Single window cannot hold all necessary information AND compression alone cannot fix it. (Counterexample: Cognition's Devin processed 5M lines of COBOL across 500GB of repos and raised PR merge rate 34% -> 67% without multi-agent fan-out.)
2. **True parallelism** -- Independent subtasks that should not serialize. N agents finish in wall-clock time of the slowest.
3. **Specialization** -- Different subtasks need different models, tools, sandboxes, or permission boundaries.

**If NONE apply: stay single-agent.**

### Compound Reliability Decay

If each agent in a chain has accuracy p, the end-to-end accuracy for N serial agents is p^N. This is the fundamental constraint on multi-agent design.

```
p = 0.95 (per-agent accuracy)

N=1:   0.95^1  = 95.0%
N=3:   0.95^3  = 85.7%
N=5:   0.95^5  = 77.4%
N=10:  0.95^10 = 59.9%
N=20:  0.95^20 = 35.8%
```

**Real-world implication**: A 5-agent pipeline where each agent is 95% accurate delivers only 77.4% end-to-end accuracy. This is why you add verifier agents to raise per-agent p, not more agents to the chain.

### Key Quantitative Invariants

| Property | Value | Source |
|----------|-------|--------|
| **Token amplification (single agent)** | ~4x chat tokens | Anthropic Research |
| **Token amplification (multi-agent)** | ~15x chat tokens | Anthropic Research |
| **Centralized orchestration overhead** | ~3.85x (285%) | Independent benchmarks |
| **Quality vs tokens** | Token usage alone explains ~80% of performance variance on BrowseComp | Anthropic Research |
| **Eval lift** | Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by 90.2% | Anthropic internal eval |
| **Wall-clock improvement** | Parallel subagents + parallel tools -> up to 90% research-time cut | Anthropic Research |
| **Model efficiency** | Sonnet 3.7 -> 4 beat doubling token budget on 3.7 | Anthropic Research |
| **Supervisor capacity (serial)** | ~3s/call x 20 workers = ~7 tasks/sec ceiling | System Design Newsletter |
| **4-agent ceiling** | Performance plateaus around ~4 agents (DeepMind, 180 configs across 5 architectures, 3 LLM families) | Google DeepMind |
| **17x error amplification** | Mesh topologies amplify errors up to 17.2x | Google DeepMind |
| **45% saturation point** | Multi-agent yields highest ROI when single-agent baseline is below 45%. Above ~80%, adding agents introduces more noise than value | Google DeepMind |
| **Sub-agent capability dominance** | Low-capability orchestrator + high-capability sub-agents: 0.42 vs 0.32 for all-high (+31%). Invest in sub-agent quality over orchestrator sophistication | Google DeepMind |

### MAST Failure Taxonomy (NeurIPS 2025)

From 1,642 execution traces across 7 frameworks, inter-rater agreement kappa = 0.88:

| Category | % of Failures | Description |
|----------|--------------|-------------|
| **Specification** | 41.8% | Role ambiguity, unclear task definitions, missing constraints. Agents do not know what success means. |
| **Coordination** | 36.9% | Communication breakdowns, state sync issues, conflicting objectives between agents. |
| **Verification** | 21.3% | No agent validated the output. Silent errors passed downstream. |

**Implication**: Nearly 80% of multi-agent failures are specification + coordination problems, not model capability issues. Fix your task definitions and handoff schemas before adding more agents.

### Capacity Formulas

**Orchestrator-worker cost**:
```
cost = C_lead(plan + synth) + sum_i(C_worker_i(context_i + tools_i))
wall-clock = T_plan + max(T_worker_i) + T_synth   (parallel sync cohort)
```

**Naive multi-agent cost accumulation**: A 10-step agent with 2K tokens per step accumulates context quadratically:
```
Step 1: 2K input, Step 2: 4K, ..., Step 10: 20K
Total input: 2K * (1+2+...+10) = 2K * 55 = 110K tokens
vs 2K * 10 = 20K if context didn't accumulate (5.5x overhead)
```

### Loop / Recursion Guards

| System | Guard | Default |
|--------|-------|---------|
| LangGraph | `recursion_limit` -> `GraphRecursionError` | 1000 super-steps (since v1.0.6) |
| OpenAI Agents SDK | `max_turns` -> `MaxTurnsExceeded` | DEFAULT_MAX_TURNS = 10; None disables |
| ADK LoopAgent | `max_iterations` and/or `escalate` | Example: 10 |
| Anthropic Research | Prompt effort budgets | Cap simple at 1 agent / 3-10 tools. Early bug: 50 subagents on simple queries |

---

## 3. Token Economics & NFR Analysis

### Cost Formula: $ per 1K Runs

**Assumptions** (labeled -- not live quotes):

| Symbol | Value | Meaning |
|--------|-------|---------|
| P_in | $3/MTok | App-model input (Sonnet-class assumption) |
| P_out | $15/MTok | App-model output (assumption) |
| T_chat | 4,000 in + 500 out | Single-turn chat reference (assumption) |
| M_agent | ~4x chat tokens | Single agent vs chat (Anthropic) |
| M_multi | ~15x chat tokens | Multi-agent vs chat (Anthropic) |
| Coordinator mix | 84-98% of team input at worker rate | Anthropic Cookbook |

**Chat baseline**: C_chat = (4000/1M) * $3 + (500/1M) * $15 = $0.012 + $0.0075 = **$0.0195/run**

**Single-agent**: C_agent = 4 * $0.0195 = $0.078 -> **$78 / 1K runs**

**Multi-agent**: C_multi = 15 * $0.0195 = $0.2925 -> **$292.50 / 1K runs**

**Optimized multi-agent (3-agent orchestrator-worker)**:

| Optimization | Cost/1K Runs | Savings |
|-------------|-------------|---------|
| Baseline (no optimization) | $495 | -- |
| Cheap supervisor (gpt-4o-mini at $0.15/$0.60) | $210 | -58% |
| + Context pruning between hops | ~$140 | -72% |
| + DeltaChannel (incremental deltas) | Infrastructure savings (~60% state storage) | -- |
| Hard budget ceiling ($5/run) | Prevents infinite loop turning $0.05 task into $5.00 | Safety net |

### Latency SLA Targets

| Topology | p50 | p95 | p99 | Driver |
|----------|-----|-----|-----|--------|
| **Pipeline** (N stages) | N*2s (5 stg: 10s) | N*4s (5 stg: 20s) | N*8s (5 stg: 40s) | Additive per stage. Predictable. |
| **Orchestrator-Worker** (parallel) | max(W)+3s (~5s) | max(W)+6s (~10s) | max(W)+12s (~20s) | Wall-clock = slowest worker + orchestrator. Up to 90% faster than sequential. |
| **Hierarchical** (D levels) | 2*D*T_avg (2 lvl: 8s) | 2*D*4s (2 lvl: 16s) | 2*D*8s (2 lvl: 32s) | Down + up. Info loss each level. |
| **Swarm / Mesh** | 5-30s (unpredictable) | 30-120s | 120s+ | Convergence-dependent. Debate rounds multiply. |

### Throughput and Back-Pressure

| Lever | Behavior |
|-------|----------|
| **Fan-out caps** | Query class -> 1 / 2-4 / >10 subagents; tool-call budgets 3-10 / 10-15 per agent |
| **Session budget** | Hard $/token ceiling as fan-out guardrail |
| **Parallel vs serial supervisor** | Serial handoffs bottleneck (~7 tasks/sec with 3s calls x 20 workers); prefer parallel tool calls |
| **Token-saving** | `last_message` / forward_message; do not re-feed full worker histories; artifact refs instead of inlining |
| **Cheap routing** | Fast classifier (gpt-4o-mini, <500ms) for dispatch; expensive model only for workers |
| **Cache** | Memoize identical subtask results (20-35% cost reduction) |
| **Shed load** | Under TPM pressure: cut N toward 1, fall back multi -> single -> deterministic |

### NFR Summary

| NFR | Target |
|-----|--------|
| **Reliability** | Design for p^N decay. 3 agents at 95% = 85.7%. Add verifier agents to raise per-agent p, not more agents. |
| **Cost ceiling** | Hard limit at 3 levels: step (25), token (100K), dollar ($5). Any breach = terminate + log. |
| **Availability** | 99.9% on ingress + supervisor. Worker paths may degrade. Breaker open does not equal total outage. |
| **RPO** | Checkpoint every super-step (sync). Swarm must persist active_agent. |
| **RTO** | Resume thread_id + checkpoint; pending writes skip successful siblings. |
| **Compliance** | Immutable delegation logs; PII redact before persist; tool RBAC per specialist. |
| **Observability** | OpenTelemetry GenAI spans at every agent boundary. W3C Trace Context via MCP (SEP-414). |
| **Auditability** | Append-only provenance log per agent action. Pipeline: best. Swarm/Mesh: requires external tracking. |

---

## 4. Distributed Resilience & Security

### Durable Execution Patterns

| Approach | Mechanism | Framework |
|----------|-----------|-----------|
| **Checkpointed State** | Full state serialized at each node. Supports replay and time-travel debugging. | LangGraph (primary) |
| **Delta Channels** | Only incremental diffs per checkpoint. 41x reduction in state storage. | LangGraph DeltaChannel (beta, May 2026) |
| **Event-Sourced** | All state changes as immutable events. Full replay from event log. | CrewAI Flows API |
| **Durable Execution** | State persistence + native tracing. Built-in durability. | OpenAI Agents SDK |
| **Ephemeral** | No state persistence. Crash = lost. | OpenAI Swarm (educational only) |

**Key consistency challenges**: Race conditions when parallel agents write simultaneously. Context drift via free-text handoffs ("summarize Q3 earnings" morphs into "extract key quotes" then becomes "bullet list of revenue figures"). **Mitigation**: Externalize shared state into a structured, canonical task object with typed fields. Make the `objective` field immutable and hash it for drift detection at each handoff.

### Circuit Breaker (closed -> open -> half-open)

```
          success                    recovery_timeout
     +-------------+   fail>=N   +------+   elapsed   +-----------+
     |    CLOSED   |------------>| OPEN |------------>| HALF_OPEN |
     +------^------+             +------+             +-----+-----+
            | success                                        |
            +------------------------------------------------+
                         fail -> OPEN
```

When breaker opens: route to fallback chain.

### Fallback Chain (multi-agent -> single agent -> deterministic)

| Stage | Behavior |
|-------|----------|
| **Multi-agent** | Fan-out under caps; role-scoped tools; full synthesis / handoff chain |
| **Single agent** | One context, one tool allowlist (~4x chat tokens); no peer handoffs |
| **Deterministic** | No LLM; template/rules/cached answer or human queue; always terminates |

### Failure Taxonomy

| Class | Examples | Detection / Response |
|-------|----------|---------------------|
| **Transient** | Tool/API 429/5xx; slow worker stalls cohort | Retry + jitter; per-worker timeout; circuit breaker |
| **Permanent** | Schema-invalid handoff; auth deny; unknown agent name | Fail-closed; schema validation at every boundary |
| **Poison-pill / Runaway** | 50 subagents on simple queries; endless search; peer handoff loops | Effort budgets; max_turns=10; recursion_limit; dollar ceiling |
| **Topology-specific** | Duplicated work; telephone paraphrase; context truncation at 200k; parallel state races; deploy skew | Rich task briefs; artifact refs; Memory; distinct state keys; rainbow deploys; schema validation |

**Compound-error property**: A minor step failure can divert the entire trajectory. Prototype-to-production gap is larger than for ordinary software.

**Idempotency**: Key side effects by `(thread_id, worker_id, tool_name, args_hash)` so checkpoint replay does not double-book.

### Enterprise Security

**Zero-Trust MCP across hops**: Authenticate every tool call (mTLS/OAuth); deny-by-default MCP registry; per-request scoped tokens; no ambient credentials in prompts; re-auth on handoff -- specialist B does not inherit A's token ambiently.

**Tool RBAC per specialist**: Research agent -> web search; code agent -> sandbox (no network); customer agent -> user data but not prod DB credentials. Coordinator with no tools + tool-scoped workers hardens blast radius. ADK: validate via deterministic `ToolContext` + `before_tool`.

**PII pipeline**: Detect -> redact -> audit before persisting shared messages, checkpoints, and artifact metadata. OpenAI nested handoff history does not redact tool args/results. Swarm default shared transcript exposes all agents' internals.

**Immutable delegation logs**: Append-only records of (correlation_id, from_agent, to_agent, reason, tool_grants, fan_out_n, breaker_state, disposition). Chain-of-custody for who delegated what.

**Invocation-Bound Capability Tokens (IBCTs)**: Use Biscuit/Datalog policies to scope what each agent can do. Each token is short-lived, tied to a specific invocation, and non-transferable.

**Prompt injection propagation**: In multi-agent systems, injection in one agent's context can propagate through handoffs to other agents. The EchoLeak vulnerability (CVE-2025-32711) demonstrated cross-agent data exfiltration through prompt injection. Dual-LLM pattern (privileged model holds tools but never reads untrusted content) mitigates this.

**OWASP Agentic Top 10 (2026)**: Least-Agency Principle -- agents should have the minimum permissions needed for their specific task.

**Compliance**: SOC 2 Type II agent-specific controls; EU AI Act phased obligations for high-risk multi-agent systems; NIST NCCoE guidance on agent trust boundaries.

---

## 5. Production Enterprise Code

```python
#!/usr/bin/env python3
"""Multi-agent supervisor with enterprise resilience primitives.
Deterministic fake models -- no API keys, no network, no stubs.
Demonstrates: circuit breaker, retry with jitter, fallback chain,
fan-out caps, PII redaction, checkpointing, correlation IDs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# -- Structured logging with correlation IDs --

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(), "level": record.levelname,
            "msg": record.getMessage(), "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "agent": getattr(record, "agent", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "fan_out": getattr(record, "fan_out", None),
            "path": getattr(record, "path", None),
        }
        return json.dumps(payload, default=str)


LOG = logging.getLogger("multiagent")
if not LOG.handlers:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    LOG.addHandler(h)
    LOG.setLevel(logging.INFO)


# -- Circuit Breaker: closed -> open -> half-open --

class BreakerState(str, Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0; self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


# -- Retry with exponential backoff + full jitter --

class TransientError(Exception): pass
class PermanentError(Exception): pass


def retry_with_jitter(fn, *, max_attempts=4, base_s=0.01, max_s=0.08, cid=""):
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except PermanentError:
            raise
        except Exception as exc:
            last = exc
            if attempt == max_attempts:
                break
            delay = min(max_s, base_s * (2 ** (attempt - 1)))
            time.sleep(random.uniform(0, delay))
    raise last


# -- PII: detect -> redact -> audit --

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
AUDIT_LOG: list[dict] = []


def redact_pii(text: str, cid: str) -> str:
    hits = EMAIL_RE.findall(text)
    if hits:
        AUDIT_LOG.append({"event": "pii_redacted", "cid": cid, "count": len(hits)})
    return EMAIL_RE.sub("[REDACTED_EMAIL]", text)


# -- Checkpointer (active_agent + plan + worker results) --

@dataclass
class Checkpoint:
    thread_id: str
    active_agent: str
    plan: list[str]
    worker_results: dict[str, str]


class MemoryCheckpointer:
    def __init__(self): self._store: dict[str, Checkpoint] = {}
    def save(self, ckpt: Checkpoint): self._store[ckpt.thread_id] = ckpt
    def load(self, tid: str): return self._store.get(tid)


# -- Fake models (deterministic, no API keys) --

class FakeLeadModel:
    def __init__(self, fail_times=0): self._fails = fail_times

    def plan(self, query: str, max_workers: int) -> list[str]:
        if self._fails > 0:
            self._fails -= 1; raise TransientError("lead transient outage")
        q = query.lower()
        if "compare" in q or " vs " in q:
            return [f"angle {c} for: {query}" for c in "ABC"][:max_workers]
        elif "complex" in q:
            return [f"slice {i} of: {query}" for i in range(1, 8)][:max_workers]
        return [f"fact-find: {query}"]

    def synthesize(self, query: str, results: dict[str, str]) -> str:
        return f"Synthesis of {len(results)} worker outputs for '{query}'"


class FakeWorkerModel:
    def __init__(self, name="worker", fail_times=0):
        self.name = name; self._fails = fail_times

    def execute(self, brief: str) -> str:
        if self._fails > 0:
            self._fails -= 1; raise TransientError(f"{self.name} transient")
        return f"[{self.name}] result for: {brief}"


# -- Supervisor with fan-out, breaker, fallback --

@dataclass
class SupervisorConfig:
    max_workers: int = 5
    max_parallel: int = 4
    session_budget_usd: float = 5.0

BREAKER = CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.05)
CHECKPOINTER = MemoryCheckpointer()


def run_multi_agent(query: str, lead: FakeLeadModel, workers: list[FakeWorkerModel],
                    config: SupervisorConfig) -> dict[str, Any]:
    """Full supervisor fan-out with resilience primitives."""
    cid = str(uuid.uuid4())
    query = redact_pii(query, cid)

    # Fan-out: plan
    plan = retry_with_jitter(lambda: lead.plan(query, config.max_workers), cid=cid)
    LOG.info("plan_created", extra={"correlation_id": cid, "fan_out": len(plan)})

    # Fan-out: workers (parallel, capped)
    results: dict[str, str] = {}
    pool_size = min(len(plan), config.max_parallel, len(workers))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = {}
        for i, brief in enumerate(plan):
            w = workers[i % len(workers)]
            futures[pool.submit(
                retry_with_jitter, lambda w=w, b=brief: w.execute(b), cid=cid
            )] = (w.name, brief)
        for fut in as_completed(futures):
            name, brief = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:
                results[name] = f"[FAILED] {exc}"

    # Synthesize
    answer = lead.synthesize(query, results)

    # Checkpoint
    CHECKPOINTER.save(Checkpoint(
        thread_id=cid, active_agent="lead",
        plan=plan, worker_results=results,
    ))
    return {"cid": cid, "answer": answer, "fan_out": len(plan),
            "results": results, "path": "multi_agent"}


def run_single_agent(query: str, worker: FakeWorkerModel) -> dict[str, Any]:
    cid = str(uuid.uuid4())
    result = retry_with_jitter(lambda: worker.execute(query), cid=cid)
    return {"cid": cid, "answer": result, "path": "single_agent"}


def deterministic_handler(query: str) -> dict[str, Any]:
    return {"cid": str(uuid.uuid4()), "answer": f"[DETERMINISTIC] {query}",
            "path": "deterministic"}


def handle_request(query: str, lead: FakeLeadModel, workers: list[FakeWorkerModel],
                   config: SupervisorConfig) -> dict[str, Any]:
    """Fallback chain: multi-agent -> single agent -> deterministic."""
    # Try multi-agent
    if BREAKER.allow():
        try:
            result = run_multi_agent(query, lead, workers, config)
            BREAKER.record_success()
            return result
        except Exception:
            BREAKER.record_failure()
    # Fallback: single agent
    try:
        return run_single_agent(query, workers[0])
    except Exception:
        pass
    # Fallback: deterministic
    return deterministic_handler(query)


def _demo():
    random.seed(42)
    lead = FakeLeadModel()
    workers = [FakeWorkerModel(f"w{i}") for i in range(4)]
    cfg = SupervisorConfig(max_workers=5, max_parallel=4)

    # Happy path: comparison query fans out to 3 workers
    r1 = handle_request("compare Python vs Rust vs Go", lead, workers, cfg)
    assert r1["path"] == "multi_agent" and r1["fan_out"] == 3

    # Simple query: 1 worker
    r2 = handle_request("what is LoRA?", lead, workers, cfg)
    assert r2["path"] == "multi_agent" and r2["fan_out"] == 1

    # Force breaker open, falls back to single agent
    bad_lead = FakeLeadModel(fail_times=10)
    for _ in range(3):
        handle_request("test", bad_lead, workers, cfg)
    r3 = handle_request("test after breaker", bad_lead, workers, cfg)
    assert r3["path"] in ("single_agent", "deterministic")

    # PII redaction
    r4 = handle_request("contact user@example.com about refund", lead, workers, cfg)
    assert "REDACTED" in r4["answer"] or len(AUDIT_LOG) > 0

    print("OK", json.dumps({"decisions": len(AUDIT_LOG), "tests": "passed"}))


if __name__ == "__main__":
    _demo()
```

---

## 6. Architectural System Design Scenarios

### Scenario A -- Multi-Agent Document Processing Pipeline (Regulated Financial Services)

**Problem**: A bank processes 10,000 loan applications/day. Each requires: (1) document extraction from PDFs/images, (2) data validation against regulatory rules, (3) risk scoring, (4) compliance check against AML/KYC databases, (5) decision recommendation with citations. Current manual process takes 45 minutes per application. Must maintain full audit trail for SOC 2 Type II and EU AI Act high-risk classification.

**Architecture**:

```
+-------------------------------- CONTROL PLANE --------------------------------+
|  Topology: Pipeline (5 stages)                                                 |
|  Budget: $2.50/application, 60s timeout/stage, 5-min total                    |
|  Compliance: SOC 2 CC6.1 audit trail, EU AI Act high-risk gate                |
+--------+-----------------------------+-----------------------------+----------+
         |                             |                             |
+--------v-----------------------------v-----------------------------v----------+
|                            PIPELINE STAGES                                     |
|                                                                                |
|  Stage 1         Stage 2           Stage 3         Stage 4        Stage 5      |
|  [Extractor] --> [Validator] -->  [Risk Scorer] --> [Compliance] --> [Decision]|
|  Vision model    Rules engine      Scoring model    AML/KYC API     Synthesis  |
|  + OCR           + schema check    + credit data    + PEP screen    + citation |
|                                                                                |
|  Schema validation at every stage boundary.                                    |
|  Verifier agent scores completeness at stages 2 and 4.                        |
+--------+----------------------------------------------------------------------+
         |
+--------v----------------------------------------------------------------------+
|  PERSISTENCE: Append-only provenance log per stage. Full OTEL trace.           |
|  AUDIT: Every decision traceable to exactly one stage. 7-year retention.       |
+-------------------------------------------------------------------------------+
```

**Trade-off matrix**:

| Alternative | Cost | Latency | Ops | Security | Audit |
|------------|------|---------|-----|----------|-------|
| **A1. Pipeline (chosen)** | ~$1.50/app (5 cheap models) | 10-15s total (5 stages additive) | Medium (5 stages to maintain) | Strong -- each stage isolated, schema-validated | Best -- every decision traces to one stage |
| **A2. Single monolithic agent** | ~$0.80/app (one large model) | 8-12s | Low | Weak -- one context holds all data | Poor -- decisions not separable |
| **A3. Orchestrator-worker (parallel)** | ~$2.00/app | 5-8s (parallel) | High | Good -- per-worker RBAC | Medium -- synthesis obscures provenance |

**Decision rationale**: Pipeline chosen for auditability. Financial regulations require tracing every decision to a specific processing stage. Pipeline provides this by design. The additive latency (10-15s) is acceptable for batch processing. Single agent fails the audit requirement. Orchestrator-worker is faster but the synthesis step obscures provenance.

---

### Scenario B -- Real-Time Customer Service Escalation Platform

**Problem**: E-commerce company handles 50,000 support interactions/day across 8 product categories. Requirements: sub-5-second first response, automatic escalation from FAQ to specialist to human, tool access (order lookup, refund processing, shipping updates), and PII protection. Current system uses a single agent that frequently hallucinates product features and cannot handle complex multi-product issues.

**Architecture**:

```
+------- CONTROL PLANE --------+
|  Topology: Handoff/Swarm      |
|  max_turns=10 per interaction |
|  Budget: $0.15/interaction    |
|  Checkpointer: required       |
+-------+----------------------+
        |
+-------v----------------------+
|  TRIAGE AGENT (fast, cheap)   |
|  Model: gpt-4o-mini (<500ms)  |
|  Tools: [classify_ticket]     |
|                                |
|  Routes to 1 of 8 specialists |
|  via transfer_to_<product>    |
+-------+----------------------+
        | handoff
+-------v----------------------+
|  SPECIALIST AGENTS (x8)       |
|  Model: Sonnet-class          |
|  Tools per specialist:        |
|   - order_lookup (read-only)  |
|   - refund_process (<$50)     |
|   - shipping_update           |
|  RBAC: no cross-product data  |
|                                |
|  Can escalate to human via    |
|  transfer_to_human_queue      |
+-------+----------------------+
        |
+-------v----------------------+
|  TOOL PROXY (MCP)             |
|  PEP: PDP evaluates each      |
|  tool call. Refund >$50 ->    |
|  allow_with_signoff.           |
|  Circuit breaker per backend.  |
+-------------------------------+
```

**Trade-off matrix**:

| Alternative | Cost/Interaction | First Response | Escalation Quality | PII Risk |
|------------|-----------------|---------------|-------------------|----------|
| **B1. Handoff/Swarm (chosen)** | ~$0.08 (triage) + $0.05 (specialist) = $0.13 | <2s (triage) + <3s (specialist) = <5s | Natural -- specialist has full category context | Low -- per-specialist RBAC, PII redacted before checkpoint |
| **B2. Single agent with all tools** | ~$0.15 | <3s | No escalation -- one agent handles everything | High -- single agent holds all tool permissions |
| **B3. Orchestrator-worker** | ~$0.20 | 5-8s (plan + dispatch + synthesize) | Over-engineered for sequential support flow | Medium -- orchestrator sees all data |

**Decision rationale**: Handoff/swarm chosen because customer support is inherently sequential (one issue at a time) with clear domain boundaries (product categories). The triage agent is cheap and fast (<500ms with gpt-4o-mini). Per-specialist RBAC ensures the billing agent cannot access shipping data. Key risk: shared conversation history exposes all agents' internals. Mitigation: custom input_filter on handoffs to redact sensitive tool results from previous specialist.

**Production case studies**: Stripe reduced average handling time by 26% with multi-agent support. Klarna handles 2/3 of customer chats (~2.3M conversations/month) with agents. Spotify cut ad planning from 15 minutes to 5 seconds.

---

## Common Failure Modes

| # | Failure Mode | Symptom | Detection | Mitigation |
|---|-------------|---------|-----------|------------|
| 1 | **Cascading errors** | One agent's bad output corrupts downstream agents | Cross-agent trace correlation; output quality scoring | Schema validation at every boundary; verifier agent |
| 2 | **Coordination deadlock** | Agents wait for each other indefinitely | Timeout monitoring per agent; hang detection | Per-agent timeouts; max_turns; deadlock-breaking escalation |
| 3 | **Context drift (telephone game)** | Task objective morphs through handoffs | Hash objective field at each handoff; diff detection | Immutable objective in structured task object |
| 4 | **Infinite agentic loops** | Agent loops consuming tokens without progress | Budget burn rate alerts; step count monitoring | max_turns; recursion_limit; dollar ceiling |
| 5 | **Silent partial failure** | Agent returns plausible but wrong output | Verifier agent at synthesis; outcome-based grading | Add verification step; do not trust intermediate outputs |
| 6 | **Privilege creep** | Agents accumulate tools across handoffs | Tool grant audit per agent | Re-auth on handoff; no ambient authority inheritance |
| 7 | **Runaway fan-out** | Lead spawns 50 subagents for simple query | Fan-out count alerting; effort budgets | Classify query complexity before fan-out; cap N per class |
| 8 | **Prompt injection propagation** | Injection in one agent propagates through chain | Anomaly detection on agent outputs; guardrail rails | Dual-LLM pattern; treat all inter-agent data as untrusted |
| 9 | **Deploy skew** | Mid-flight agents hit updated code during rolling deploy | Version mismatch detection | Rainbow deploys; agents complete on version they started |

---

## Key Takeaways for Interviews

- Multi-agent is never the default. Three hard limits justify it: context overflow, true parallelism, and specialization. If none apply, use a single agent.
- Compound reliability decay (p^N) is the fundamental constraint. 5 agents at 95% accuracy = 77.4% end-to-end. Add verifier agents to raise per-agent p, not more agents.
- Performance plateaus around 4 agents (DeepMind, 180 configurations). Above the 45% saturation point in base performance, more agents add more noise than value.
- Multi-agent costs ~15x chat tokens vs ~4x for single agent. Use cheap workers for read-heavy legs and budget ceilings to prevent runaway costs.
- The MAST taxonomy shows 42% of failures are specification problems and 37% are coordination -- fix task definitions and schemas before blaming model capability.
- MCP is agent-to-tool (vertical); A2A is agent-to-agent (horizontal). They are complementary, not competing protocols.
- Circuit breaker pattern with fallback chain (multi-agent -> single agent -> deterministic) provides graceful degradation under failure.
- Schema validation at every agent boundary is non-negotiable. Context drift through free-text handoffs is the "telephone game" failure mode.

---

## Interview Q&A

**Q1: When would you choose multi-agent over a single agent?**
A: I reach for multi-agent only when hitting one of three hard limits. First, context overflow -- when the total information needed exceeds a single context window and compression alone cannot solve it. Second, true parallelism -- when I have genuinely independent subtasks that should not serialize (for example, 10 independent research queries). Third, specialization -- when different subtasks need different models, tools, or permission boundaries (code agent needs shell access, but search agent should only have web access). If none of these apply, I keep it as a single agent. Cognition's Devin processed 5 million lines of COBOL with a single agent and raised PR merge rate from 34% to 67%.

**Q2: Explain compound reliability decay and how it constrains multi-agent design.**
A: If each agent in a serial chain has accuracy p, end-to-end accuracy is p^N. At p=0.95 with 5 agents, you get 0.95^5 = 77.4%. This drops to 60% at 10 agents and 36% at 20. The practical implication is that you should add verifier agents to raise per-agent p rather than adding more agents. Google DeepMind found a 4-agent ceiling where performance plateaus across 180 configurations. Above ~80% single-agent baseline, adding agents introduces more noise than value.

**Q3: Compare orchestrator-worker and handoff/swarm topologies.**
A: Orchestrator-worker has a central lead that decomposes, dispatches workers in parallel, and synthesizes results. The lead maintains ownership throughout. Wall-clock time is T_plan + max(T_worker) + T_synth. It is the dominant production pattern (~70% of deployments) because it has a single policy chokepoint that is easy to govern. Handoff/swarm transfers ownership to specialist agents sequentially. The active agent resumes on the next user turn. It requires a checkpointer to persist active_agent state. It is better for customer support flows where domain handoffs are natural. The key risk is shared conversation history exposing all agents' internals unless you use input filters.

**Q4: How does A2A differ from MCP?**
A: MCP is vertical -- it connects an agent to tools and data sources. A2A is horizontal -- it connects agents to other agents across vendor boundaries. A2A v1.0 (early 2026) introduces signed agent cards for cryptographic identity, multi-tenancy support, multi-protocol bindings (JSON-RPC + gRPC), and version negotiation. Over 150 organizations are part of the A2A specification under the Linux Foundation. You use both together: MCP for tool access within your system, A2A for interoperating with external agent systems.

**Q5: Walk through how you would design a circuit breaker for multi-agent systems.**
A: The circuit breaker has three states. CLOSED: multi-agent path accepts traffic, failures counted in a sliding window. OPEN: after N failures (for example, 3 in 60 seconds), reject multi-agent path and start recovery timer, routing to the fallback chain. HALF_OPEN: after the recovery timeout, allow one probe through multi-agent; success closes the circuit, failure reopens it. The fallback chain degrades gracefully: first try single agent (~4x chat tokens instead of ~15x), then deterministic handler (rules/templates, no LLM). The key design decision is that breaker open does not equal total outage -- the fallback keeps the service available at reduced capability.

**Q6: What is the MAST failure taxonomy?**
A: MAST is a failure taxonomy from NeurIPS 2025 based on 1,642 execution traces across 7 frameworks. It categorizes multi-agent failures into three buckets: specification problems (41.8%) where agents do not know what success means due to role ambiguity or unclear task definitions; coordination failures (36.9%) where communication breaks down, state sync fails, or agents have conflicting objectives; and verification gaps (21.3%) where no agent validates the output and silent errors pass downstream. The key insight is that nearly 80% of failures are specification plus coordination problems, not model capability issues. Fix your task definitions and handoff schemas first.

**Q7: How do you control token costs in multi-agent systems?**
A: Multiple levers. First, use cheap workers for read-heavy legs -- the Anthropic Cookbook shows 84-98% of team input tokens come from workers, so using a cheaper model there has outsized impact. Second, use output_mode='last_message' instead of feeding full worker histories to the lead. Third, use artifact references instead of inlining large worker outputs. Fourth, apply a hard three-level budget ceiling (step count, token count, dollar amount). Fifth, classify query complexity before fan-out to assign 1/2-4/>10 workers appropriately. Sixth, memoize identical subtask results for 20-35% cost reduction on repeated patterns.

**Q8: How do you handle prompt injection in multi-agent systems?**
A: Multi-agent systems amplify prompt injection risk because injection in one agent's context can propagate through handoffs to other agents. The EchoLeak vulnerability (CVE-2025-32711) demonstrated this. My defense-in-depth approach includes: per-agent tool RBAC so a compromised agent has limited blast radius, schema validation at every agent boundary so injected content that changes the output format is caught, a coordinator agent with no tools so it never processes raw untrusted content, and treating all inter-agent data as untrusted. For high-security paths, I use the dual-LLM pattern where the privileged model holds tools but never reads untrusted content directly.

**Q9: What are the key differences between LangGraph and OpenAI Agents SDK for multi-agent?**
A: LangGraph uses a graph-of-nodes model with conditional edges, supporting both supervisor and swarm patterns. Its key strength is checkpointed state at every node with time-travel debugging and the new DeltaChannel for incremental deltas (41x storage reduction). OpenAI Agents SDK uses two patterns: handoffs where the specialist becomes the active agent, and agents-as-tools where the manager keeps ownership. Its key strength is clean handoff semantics with built-in guardrail asymmetry (input guardrails on first agent, output guardrails on final agent). LangGraph is more flexible for complex topologies; OpenAI SDK is simpler for linear escalation flows.

**Q10: Describe the 45% saturation point and the 4-agent ceiling.**
A: Google DeepMind tested 180 configurations across 5 architectures and 3 LLM families. They found that multi-agent coordination yields the highest returns when the single-agent baseline is below 45% accuracy. Above ~80%, adding agents introduces more noise than value. Performance plateaus around ~4 agents for most configurations. They also found that investing in sub-agent quality matters more than orchestrator sophistication: a low-capability orchestrator with high-capability sub-agents scored 0.42 versus 0.32 for all-high-capability agents, a 31% improvement. The practical takeaway: use at most 3-5 agents, invest your budget in worker model quality, and do not add agents if your single-agent baseline already exceeds 80%.

---

## Key Numbers to Memorize

| Metric | Value |
|--------|-------|
| Token amplification (single agent vs chat) | ~4x |
| Token amplification (multi-agent vs chat) | ~15x |
| Compound reliability: 5 agents at 95% | 77.4% end-to-end |
| 4-agent ceiling | Performance plateaus around 4 agents (DeepMind) |
| 45% saturation point | Multi-agent ROI highest below 45% baseline |
| 17x error amplification | Mesh topology worst case (DeepMind) |
| MAST failure split | 42% specification / 37% coordination / 21% verification |
| Wall-clock improvement (parallel) | Up to 90% research-time cut |
| Serial supervisor throughput | ~7 tasks/sec (3s/call x 20 workers) |
| Cost: single agent per 1K runs | ~$78 (Sonnet-class assumptions) |
| Cost: multi-agent per 1K runs | ~$292.50 (Sonnet-class assumptions) |
| Coordinator worker token share | 84-98% of team input at worker rate |
| LangGraph DeltaChannel reduction | 41x less state storage |
| Anthropic eval lift (multi vs single) | 90.2% improvement |
| Default max_turns (OpenAI SDK) | 10 |
| Default recursion_limit (LangGraph) | 1000 |

---

## Quick Reference

```
WHEN TO GO MULTI-AGENT:
  Context overflow AND compression fails -> Yes
  Independent parallel subtasks -> Yes
  Different tools/models/permissions needed -> Yes
  None of the above -> Stay single-agent

TOPOLOGY SELECTION:
  Predictable + auditable -> Pipeline
  Parallel independent work -> Orchestrator-Worker
  80+ domains -> Hierarchical
  Sequential domain handoffs -> Swarm/Handoff
  Debate/research -> Mesh (with heavy instrumentation)

COST CONTROL:
  3-level budget: steps (25) + tokens (100K) + dollars ($5)
  Cheap supervisor + expensive workers
  output_mode='last_message'
  Artifact refs, not inline text
  Memoize repeated subtasks

RELIABILITY:
  p^N decay -> minimize N, maximize p
  Verifier agents at critical boundaries
  Schema validation at every handoff
  Fallback: multi-agent -> single -> deterministic

SECURITY:
  Per-agent tool RBAC (no ambient authority)
  Re-auth on handoff
  PII: detect -> redact -> audit before checkpoint
  Immutable delegation logs
  MCP = agent-to-tool; A2A = agent-to-agent
```

---

## Sources

1. System Design Newsletter -- Multi-Agent Architectures
2. Anthropic -- Multi-agent research system (engineering blog)
3. Anthropic Cookbook -- Managed Agents (CMA): Plan Big, Execute Small
4. LangGraph Supervisor (langgraph-supervisor-py)
5. LangGraph Swarm (langgraph-swarm-py)
6. OpenAI Agents SDK -- Orchestration, Handoffs, Running Agents
7. Google ADK -- Multi-agents, State management, Safety
8. Google DeepMind -- 180-configuration multi-agent study (4-agent ceiling, 17x amplification, 45% saturation)
9. MAST Failure Taxonomy (NeurIPS 2025, 1,642 traces)
10. EchoLeak CVE-2025-32711
11. OWASP Agentic Top 10 (2026) -- Least-Agency Principle
12. A2A Protocol v1.0 specification
13. OpenTelemetry GenAI semantic conventions (SEP-414)
14. IBM watsonx Orchestrate (80+ domain agents)
15. Stripe -- 26% AHT reduction with multi-agent
16. Spotify -- 15 min to 5s ad planning
17. Klarna -- 2.3M conversations/month with agents
18. Cognition Devin -- 5M lines COBOL, 34% to 67% merge rate (single agent)
