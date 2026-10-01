# 09. Multi-Agent Architecture

**Sub-areas covered**: When multi-agent is justified vs single-agent default (context overflow, parallelism, specialization), six canonical topologies across chain-of-command and decentralized families (orchestrator-worker, pipeline, hierarchical, swarm, mesh, handoffs), inter-agent communication patterns (shared state, message passing, blackboard, tool-call delegation), interoperability protocols (MCP for agent-to-tool, A2A v1.0 for agent-to-agent with signed agent cards and multi-tenancy), framework comparison (LangGraph v1.1, CrewAI, Microsoft Agent Framework 1.0, Google ADK 2.0, OpenAI Agents SDK) with topology model and state management, token amplification (3-15x overhead, 285% in centralized orchestration), compound reliability decay (5 agents at 95% = 77% end-to-end), the 17x error amplification rule (Google DeepMind, 180 configurations), the 45% saturation point for multi-agent ROI, MAST failure taxonomy (NeurIPS 2025, 1,642 traces: 41.8% specification, 36.9% coordination, 21.3% verification), seven production failure patterns (cascading errors, coordination deadlock, context drift, infinite agentic loops, silent partial failure, inter-agent misalignment, tool/data corruption), durable execution with checkpointed state and delta channels, prompt injection propagation across agent chains, EchoLeak CVE-2025-32711, privilege escalation via implicit trust, least-agency principle (OWASP Agentic Top 10 2026), invocation-bound capability tokens with Biscuit/Datalog policies, compliance landscape (SOC 2 Type II agent-specific controls, EU AI Act phased obligations, NIST NCCoE), OpenTelemetry GenAI semantic conventions for multi-agent tracing, production Python code for multi-agent orchestrator with worker pool management, inter-agent message bus with schema validation, coordinated circuit breakers, budget enforcement, and graceful degradation, and two enterprise system-design scenarios (multi-agent document processing pipeline, real-time customer service escalation platform) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production multi-agent system spans six cooperating layers: a **control plane** routing incoming requests to the correct topology and enforcing agent-level resource budgets; an **agent coordination plane** managing inter-agent communication, handoffs, and topology-specific routing (supervisor dispatch, pipeline sequencing, or blackboard writes); an **agent execution plane** where individual agents run their reasoning loops with scoped tool access and isolated context; a **tool proxy layer** mediating all external tool calls through MCP with per-agent allowlists, circuit breakers, and idempotency guards; a **persistence layer** checkpointing agent state, inter-agent message history, and coordination metadata for durable execution; and a **telemetry layer** correlating traces across agent boundaries using W3C Trace Context propagation via OpenTelemetry GenAI semantic conventions.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                               CONTROL PLANE                                      │
│                                                                                  │
│  ┌─────────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐  │
│  │ Topology Selector    │  │ Agent Budget          │  │ A2A Gateway            │  │
│  │                      │  │ Enforcer              │  │                        │  │
│  │ Decision tree:       │  │                       │  │ Signed Agent Card      │  │
│  │  Context overflow?   │  │ Three-level caps:     │  │ verification.          │  │
│  │   -> parallel split  │  │  L1: step ceiling     │  │ Multi-tenant routing.  │  │
│  │  Independent tasks?  │  │      (25 default)     │  │ Version negotiation    │  │
│  │   -> orch-worker     │  │  L2: token budget     │  │ (v0.3 <-> v1.0).      │  │
│  │  Sequential + audit? │  │      (100K default)   │  │ Protocol bindings:     │  │
│  │   -> pipeline        │  │  L3: dollar ceiling   │  │  JSON-RPC + gRPC.     │  │
│  │  80+ domains?        │  │      ($5.00 default)  │  │                        │  │
│  │   -> hierarchical    │  │                       │  │ External agents enter  │  │
│  │  None of the above?  │  │ Any breach ->         │  │ system here.           │  │
│  │   -> single agent    │  │ terminate + log       │  │                        │  │
│  └──────────┬───────────┘  └──────────┬───────────┘  └───────────┬────────────┘  │
└─────────────┼──────────────────────────┼──────────────────────────┼──────────────┘
              │ topology + config        │ budget envelope          │ verified identity
┌─────────────▼──────────────────────────▼──────────────────────────▼──────────────┐
│                       AGENT COORDINATION PLANE                                    │
│                                                                                   │
│  ┌───────────────────────┐  ┌────────────────────┐  ┌──────────────────────────┐ │
│  │ Orchestrator          │  │ Message Bus         │  │ Handoff Manager          │ │
│  │                       │  │                     │  │                          │ │
│  │ Supervisor pattern:   │  │ Typed schemas       │  │ Explicit control         │ │
│  │  decompose(task)      │  │ (Pydantic) at       │  │ transfer between         │ │
│  │  -> assign(worker_i)  │  │ every boundary.     │  │ agents via function      │ │
│  │  -> collect(results)  │  │                     │  │ returns.                 │ │
│  │  -> synthesize()      │  │ Patterns:           │  │                          │ │
│  │                       │  │  Shared state       │  │ Maintains shared         │ │
│  │ Workers invoked as    │  │  (Redis/DB)         │  │ conversation history.    │ │
│  │ tool calls -- no      │  │  Direct messages    │  │                          │ │
│  │ inter-worker comms.   │  │  Blackboard writes  │  │ Stateless between        │ │
│  │                       │  │  Tool-call delegn   │  │ handoffs.                │ │
│  └───────────┬───────────┘  └─────────┬──────────┘  └──────────┬───────────────┘ │
└──────────────┼──────────────────────────┼──────────────────────────┼──────────────┘
               │ scoped task              │ typed messages           │ control token
┌──────────────▼──────────────────────────▼──────────────────────────▼──────────────┐
│                        AGENT EXECUTION PLANE                                      │
│                                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Worker Agent  │  │ Worker Agent  │  │ Worker Agent  │  │ Verifier Agent      │ │
│  │ (Specialist)  │  │ (Specialist)  │  │ (Specialist)  │  │                     │ │
│  │               │  │               │  │               │  │ Schema validation   │ │
│  │ Own model     │  │ Own model     │  │ Own model     │  │ at every handoff.   │ │
│  │ (may differ). │  │ (may differ). │  │ (may differ). │  │ Confidence +        │ │
│  │ Own tools     │  │ Own tools     │  │ Own tools     │  │ groundedness +      │ │
│  │ (allowlisted).│  │ (allowlisted).│  │ (allowlisted).│  │ completeness        │ │
│  │ Own sandbox.  │  │ Own sandbox.  │  │ Own sandbox.  │  │ scoring.            │ │
│  │ Isolated ctx. │  │ Isolated ctx. │  │ Isolated ctx. │  │                     │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘ │
└─────────┼──────────────────┼────────────────┼──────────────────────┼─────────────┘
          │                  │                │                      │
┌─────────▼──────────────────▼────────────────▼──────────────────────▼─────────────┐
│                          TOOL PROXY LAYER (MCP)                                   │
│                                                                                   │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  ┌─────────────┐ │
│  │ Per-Agent        │  │ Per-Backend      │  │ Idempotency    │  │ Result Size │ │
│  │ Tool Allowlist   │  │ Circuit Breaker  │  │ Guard          │  │ Cap (100KB) │ │
│  │                  │  │                  │  │                │  │             │ │
│  │ Agent A: [search,│  │ CLOSED -> OPEN   │  │ Check key      │  │ Truncate    │ │
│  │  read_file]      │  │ -> HALF_OPEN     │  │ before side    │  │ oversized   │ │
│  │ Agent B: [db_qry]│  │ -> CLOSED        │  │ effects.       │  │ tool        │ │
│  │ Agent C: [email] │  │                  │  │ Critical for   │  │ responses.  │ │
│  │                  │  │ Shared across    │  │ checkpoint-    │  │             │ │
│  │ No shell access  │  │ agents hitting   │  │ resume.        │  │             │ │
│  │ unless explicit. │  │ same backend.    │  │                │  │             │ │
│  └─────────────────┘  └──────────────────┘  └────────────────┘  └─────────────┘ │
└─────────────────────────────────┬────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                         PERSISTENCE LAYER                                        │
│                                                                                  │
│  ┌────────────────────┐  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │ Agent Checkpoints   │  │ Coordination State    │  │ Provenance Log          │ │
│  │                     │  │                       │  │                         │ │
│  │ LangGraph:          │  │ Task decomposition    │  │ Append-only record:     │ │
│  │  Full state at      │  │ tree. Worker          │  │  agent_id, action,      │ │
│  │  every node.        │  │ assignments.          │  │  timestamp, input_hash, │ │
│  │  DeltaChannel for   │  │ Completion status.    │  │  output_hash,           │ │
│  │  incremental        │  │ Handoff history.      │  │  parent_span_id.        │ │
│  │  deltas (beta).     │  │ Budget consumed       │  │                         │ │
│  │                     │  │ per agent.             │  │ Satisfies SOC 2 CC6.1  │ │
│  │ Temporal:           │  │                       │  │ + pipeline audit trail. │ │
│  │  Event log +        │  │                       │  │                         │ │
│  │  deterministic      │  │                       │  │                         │ │
│  │  replay.            │  │                       │  │                         │ │
│  └────────────────────┘  └──────────────────────┘  └──────────────────────────┘ │
└─────────────────────────────────┬────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────────────┐
│                  TELEMETRY / OBSERVABILITY LAYER                                 │
│                                                                                  │
│  OpenTelemetry GenAI semantic conventions:                                       │
│    invoke_agent | execute_tool | create_agent | invoke_workflow spans             │
│  W3C Trace Context propagation via MCP _meta field (SEP-414)                    │
│  Per-agent token consumption + latency  |  Cross-agent trace correlation        │
│  Budget burn rate vs ceiling alerts  |  Parallel trace stitching                │
│  Loop count anomaly detection  |  Context drift detector (objective hash diff)  │
│  Cascading failure propagation tracker across agent boundaries                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user request enters the control plane. The topology selector evaluates three criteria: does the task overflow a single context window? Are there genuinely independent subtasks? Do different subtasks need different models, tools, or permission boundaries? If none apply, it routes to a single agent. (2) If multi-agent is warranted, the A2A gateway verifies signed agent cards for any external agents and the budget enforcer wraps the entire execution in a three-level envelope (step ceiling, token budget, dollar ceiling). (3) The request enters the agent coordination plane, where the topology-specific coordinator takes over. In orchestrator-worker mode, the supervisor decomposes the task and invokes workers as tool calls. In pipeline mode, the message bus sequences handoffs with typed schema validation at each boundary. In hierarchical mode, top-level managers delegate to mid-level managers who delegate to workers; no level-skipping is allowed. (4) Each worker agent executes in isolation in the agent execution plane with its own model, scoped tool allowlist, and sandboxed context. A verifier agent optionally scores outputs at every handoff for confidence, groundedness, and completeness, catching the silent partial failure pattern before it propagates. (5) All tool calls exit through the MCP-based tool proxy layer, where per-agent allowlists enforce least-agency, per-backend circuit breakers prevent agent fleets from DDoSing shared services, and idempotency guards ensure checkpoint-resume safety. (6) At every coordination state change, the persistence layer checkpoints agent state, coordination metadata, and provenance records. The provenance log creates an append-only audit trail mapping every agent action to its inputs, outputs, and parent trace span. (7) Throughout execution, the telemetry layer correlates traces across agent boundaries using W3C Trace Context propagation. Budget burn rate alerts fire when consumption trajectories predict ceiling breaches. Context drift detection compares objective field hashes at each handoff to catch the telephone-game degradation pattern.

---

## 2. Core Mechanics & Algorithms

### 2.1 When Multi-Agent Is Justified

Multi-agent is never the default. Three hard limits -- and only these three -- justify the transition from single-agent:

```
┌───────────────────────────────────────────────────────────────────────────┐
│                    MULTI-AGENT JUSTIFICATION GATE                         │
│                                                                           │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────────────┐│
│  │ Context Overflow │   │ Parallelism     │   │ Specialization          ││
│  │                  │   │                 │   │                         ││
│  │ Single window    │   │ Independent     │   │ Different subtasks      ││
│  │ cannot hold all  │   │ subtasks that   │   │ need different models,  ││
│  │ necessary info   │   │ should not      │   │ tools, sandboxes, or    ││
│  │ AND compression  │   │ serialize.      │   │ permission boundaries.  ││
│  │ alone cannot     │   │ N agents finish │   │                         ││
│  │ fix it.          │   │ in wall-clock   │   │ Example: code agent     ││
│  │                  │   │ time of the     │   │ needs shell; search     ││
│  │ Example: 5M      │   │ slowest.        │   │ agent needs web; DB     ││
│  │ lines of COBOL   │   │                 │   │ agent needs SQL-only.   ││
│  │ across 500GB     │   │ Example: 10     │   │                         ││
│  │ (Cognition Devin │   │ independent     │   │                         ││
│  │ -- but they used │   │ research        │   │                         ││
│  │ single agent).   │   │ queries.        │   │                         ││
│  └─────────────────┘   └─────────────────┘   └─────────────────────────┘│
│                                                                           │
│  If NONE apply: single agent. Devin's 5M-line COBOL PR merge rate went   │
│  from 34% to 67% with a single agent.                                    │
└───────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Six Canonical Topologies

Two families, six topologies. Each has a distinct state machine governing control flow.

**Chain-of-command family** (predictable, auditable):

**Topology 1: Orchestrator-Worker (Hub-and-Spoke)**

~70% of production deployments use this pattern. Central agent decomposes, assigns, synthesizes. Workers have zero inter-worker communication.

```
State Machine:

┌──────────┐    ┌───────────────────┐    ┌───────────────────────────────┐
│  RECEIVE │───▶│ DECOMPOSE         │───▶│ DISPATCH                      │
│  (task)  │    │                   │    │                               │
└──────────┘    │ Orchestrator LLM  │    │ For each subtask:             │
                │ breaks task into  │    │   invoke worker as tool call  │
                │ independent       │    │   Workers run in parallel     │
                │ subtasks.         │    │                               │
                └───────────────────┘    └──────────────┬────────────────┘
                                                        │ all workers complete
                                         ┌──────────────▼────────────────┐
                                         │ SYNTHESIZE                    │
                                         │                               │
                                         │ Orchestrator merges worker    │
                                         │ outputs into final answer.    │
                                         │ Verifier scores result.       │
                                         └──────────────┬────────────────┘
                                                        │
                                         ┌──────────────▼────────────────┐
                                         │ RESPOND                       │
                                         └───────────────────────────────┘

Complexity: O(max(T_worker)) + O(T_orchestrator) wall-clock
            O(N * T_worker_avg) total compute
Bottleneck: Orchestrator (~3s/call overhead)
Throughput: ~3s/call x 20 workers = ~7 tasks/sec ceiling
```

**Topology 2: Pipeline (DAG / Assembly Line)**

Fixed predetermined order. Each agent's output is the next agent's input. Structured as a DAG with no loops. Strongest audit trail of all topologies.

```
State Machine:

┌─────────┐   schema   ┌─────────┐   schema   ┌─────────┐   schema   ┌─────────┐
│ Agent_1  │──validate─▶│ Agent_2  │──validate─▶│ Agent_3  │──validate─▶│ Agent_N  │
│ (stage1) │   pass?    │ (stage2) │   pass?    │ (stage3) │   pass?    │ (stageN) │
└─────────┘   fail?     └─────────┘   fail?     └─────────┘   fail?     └─────────┘
               ↓                       ↓                       ↓
          reject + log           reject + log            reject + log

Complexity: O(sum(T_stage_i)) wall-clock  -- strictly additive
            5 stages x 2s avg = 10s minimum
Latency:    Predictable, no parallelism
Audit:      Every decision traceable to exactly one stage
```

**Topology 3: Hierarchical (Tree)**

Minimum 2 levels. Top-level manager delegates to mid-level managers who delegate to workers. No level-skipping. No single agent holds full context.

```
State Machine:

                     ┌──────────────┐
                     │  Top Manager  │
                     │  (routes by   │
                     │   domain)     │
                     └──────┬───────┘
                   ┌────────┼────────┐
                   ▼        ▼        ▼
            ┌──────────┐ ┌──────────┐ ┌──────────┐
            │ Mid Mgr  │ │ Mid Mgr  │ │ Mid Mgr  │
            │ (HR)     │ │ (Sales)  │ │ (Procure)│
            └────┬─────┘ └────┬─────┘ └────┬─────┘
              ┌──┼──┐      ┌──┼──┐      ┌──┼──┐
              ▼  ▼  ▼      ▼  ▼  ▼      ▼  ▼  ▼
             W1 W2 W3     W4 W5 W6     W7 W8 W9

Complexity: O(depth * T_avg) latency per direction
            Orders flow down, reports flow up
            Information loss at each level (context compression)
Scaling:    IBM watsonx Orchestrate: 80+ domain agents
```

**Decentralized family** (harder to debug, more resilient to partial failures):

**Topology 4: Swarm**

Agents operate as equals with no hierarchy. Communication is indirect via shared blackboard (Redis, database, vector store). No direct inter-agent messages.

```
State Machine:

        ┌─────────┐     ┌─────────┐     ┌─────────┐
        │ Agent A  │     │ Agent B  │     │ Agent C  │
        │          │     │          │     │          │
        │ read ->  │     │ read ->  │     │ read ->  │
        │ process  │     │ process  │     │ process  │
        │ -> write │     │ -> write │     │ -> write │
        └────┬─────┘     └────┬─────┘     └────┬─────┘
             │                │                │
             ▼                ▼                ▼
        ┌────────────────────────────────────────────┐
        │          SHARED BLACKBOARD                  │
        │  (Redis / DB / Vector Store)                │
        │                                             │
        │  Agents post partial results.               │
        │  Coordinator synthesizes when converged.    │
        │  Race conditions at scale.                  │
        └─────────────────────────────────────────────┘

Risk:   No central bottleneck, but requires conflict resolution
        Stale reads when parallel agents write simultaneously
```

**Topology 5: Mesh**

Fully connected peer-to-peer. Every agent can communicate with every other agent. The most dangerous topology: 17x error amplification risk (Google DeepMind).

```
State Machine:

        ┌─────────┐◄──────►┌─────────┐
        │ Agent A  │        │ Agent B  │
        │          │◄──┐    │          │
        └────┬─────┘   │    └────┬─────┘
             │    ┌────►│◄───────┘
             │    │     │
             ▼    │     ▼
        ┌─────────┐◄──────►┌─────────┐
        │ Agent C  │        │ Agent D  │
        └─────────┘        └─────────┘

Risk:   O(N^2) communication paths
        Error amplification up to 17.2x (DeepMind, 180 configurations)
        Convergence-dependent latency
Use:    Research / debate scenarios only, with heavy instrumentation
```

**Topology 6: Handoffs (OpenAI Agents SDK pattern)**

Agents pass control explicitly to the next agent via function returns. Stateless between calls. Shared conversation history maintained by the runner, not by individual agents.

```
State Machine:

┌─────────┐  handoff()  ┌─────────┐  handoff()  ┌─────────┐
│ Agent A  │────────────▶│ Agent B  │────────────▶│ Agent C  │
│          │             │          │             │          │
│ Decides  │             │ Decides  │             │ Decides  │
│ it cannot│             │ it cannot│             │ to       │
│ handle   │             │ handle   │             │ respond  │
│ this     │             │ this     │             │          │
└─────────┘             └─────────┘             └─────────┘
                                                      │
Runner maintains shared conversation history          ▼
across all handoffs. Each agent is stateless.     ┌─────────┐
                                                  │ RESPOND  │
                                                  └─────────┘
```

### 2.3 Communication Pattern Algorithms

Four patterns governing how agents exchange information:

```
┌───────────────────┬─────────────────────────────┬────────────────────────────┐
│ Pattern           │ Algorithm                   │ Invariant / Trade-off      │
├───────────────────┼─────────────────────────────┼────────────────────────────┤
│ Shared State      │ read(key) -> process ->     │ Simple but race conditions │
│                   │ write(key, result)           │ at scale. Stale reads      │
│                   │ Concurrency: optimistic      │ when N writers > 1.        │
│                   │ locking or CAS.              │                            │
├───────────────────┼─────────────────────────────┼────────────────────────────┤
│ Message Passing   │ send(agent_id, typed_msg)   │ Clean boundaries.          │
│                   │ via orchestrator or A2A.     │ Higher latency per hop.    │
│                   │ Each hop validates schema.   │ O(hops) added latency.     │
├───────────────────┼─────────────────────────────┼────────────────────────────┤
│ Blackboard        │ Agents post partial results  │ Good for heterogeneous     │
│                   │ to shared surface.           │ agents. Requires conflict  │
│                   │ Coordinator reads when       │ resolution protocol.       │
│                   │ convergence condition met.   │                            │
├───────────────────┼─────────────────────────────┼────────────────────────────┤
│ Tool-Call         │ Orchestrator invokes worker  │ Cleanest isolation.        │
│ Delegation        │ as tool_call(name, args).    │ Worker is a black box.     │
│                   │ Worker returns structured    │ Orchestrator is single     │
│                   │ output.                      │ point of failure.          │
└───────────────────┴─────────────────────────────┴────────────────────────────┘
```

### 2.4 Interoperability Protocol Stack

MCP and A2A are complementary, not competing. They form a two-layer protocol stack:

```
┌──────────────────────────────────────────────────────────────────┐
│                      AGENT LAYER                                  │
│                                                                   │
│  Agent_A ◄───── A2A Protocol (v1.0) ─────► Agent_B               │
│  (Your system)  Horizontal: agent-to-agent  (External system)    │
│                                                                   │
│  Features (v1.0, early 2026):                                    │
│    - Signed Agent Cards (cryptographic identity)                 │
│    - Multi-tenancy (single endpoint, multiple tenant agents)     │
│    - Multi-protocol bindings (JSON-RPC + gRPC)                   │
│    - Version negotiation (v0.3 <-> v1.0 backward compat)        │
│    - SDKs: Python, JavaScript, Java, Go, .NET                   │
│    - 150+ organizations (Linux Foundation since June 2025)       │
│                                                                   │
├───────────────────────────────────────────────────────────────────┤
│                      TOOL LAYER                                   │
│                                                                   │
│  Agent ◄───── MCP (Model Context Protocol) ─────► Tool/Service   │
│               Vertical: agent-to-tool                             │
│                                                                   │
│  Standardizes tool discovery and invocation.                     │
│  No custom integration code per tool.                            │
│  Anthropic (late 2024). Adopted by OpenAI, Google, Microsoft.    │
└──────────────────────────────────────────────────────────────────┘
```

### 2.5 Key Invariants

**Compound reliability**: If each agent in a chain has accuracy `p`, the end-to-end accuracy for `N` serial agents is `p^N`. This is the fundamental constraint on multi-agent design.

```
p = 0.95 (per-agent accuracy)

N=1:   0.95^1  = 95.0%
N=3:   0.95^3  = 85.7%
N=5:   0.95^5  = 77.4%
N=10:  0.95^10 = 59.9%
N=20:  0.95^20 = 35.8%
```

**The 4-agent ceiling**: Performance plateaus around ~4 agents for most configurations (DeepMind, 180 configurations across 5 architectures and 3 LLM families).

**The 45% saturation point**: Multi-agent coordination yields highest returns when single-agent baseline is below 45%. Above ~80% base performance, adding agents introduces more noise than value.

**Sub-agent capability dominance**: A low-capability orchestrator + high-capability sub-agents scored 0.42 vs 0.32 for all-high-capability (+31% improvement). Invest in sub-agent quality over orchestrator sophistication.

---

## 3. Token Economics & NFR Analysis

### 3.1 Token Amplification

Every hop in a multi-agent system re-sends context. Tokens compound, they do not simply add.

```
┌──────────────────────────────────────────────────────────────────────┐
│                   TOKEN AMPLIFICATION BENCHMARKS                      │
│                                                                       │
│  Source                          │ Amplification vs Single Agent      │
│  ────────────────────────────────┼────────────────────────────────────│
│  Anthropic production research   │ ~15x tokens vs single chat         │
│  Independent multi-agent (avg)   │ ~1.58x (58% overhead)              │
│  Centralized orchestration (avg) │ ~3.85x (285% overhead)             │
│  Supervisor vs single mega-agent │ ~3x cost for 18pp success lift     │
│  OpenRouter weekly volume growth │ 0.4T to 27.0T in 15 months (68x)  │
└──────────────────────────────────┴────────────────────────────────────┘
```

### 3.2 Cost Formulas

**Naive multi-agent cost per run (no optimization)**:

```
C_naive = sum_over_agents(
    sum_over_steps(
        input_tokens_i * price_per_input_token +
        output_tokens_i * price_per_output_token
    )
)

Where input_tokens_i for step k includes ALL prior context (O(k) growth).
A 10-step agent with 2K tokens per step:
  Step 1: 2K input, Step 2: 4K, Step 3: 6K, ..., Step 10: 20K
  Total input: 2K * (1+2+...+10) = 2K * 55 = 110K tokens
  vs 2K * 10 = 20K if context didn't accumulate (5.5x overhead)
```

**Cost per 1K runs with optimization levers**:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Scenario: 3-agent orchestrator-worker, 5 steps avg per agent           │
│ Model: Claude Sonnet ($3/$15 per 1M input/output tokens)               │
│                                                                         │
│ Baseline (no optimization):                                             │
│   Orchestrator: ~50K input + 5K output per run                         │
│   Workers (x2): ~30K input + 3K output each per run                    │
│   Total per run: 110K input + 11K output                               │
│   Cost per run:  (110K * $3 + 11K * $15) / 1M = $0.495                │
│   Cost per 1K runs: $495                                                │
│                                                                         │
│ Optimization 1: Cheap supervisor (gpt-4o-mini at $0.15/$0.60)          │
│   Orchestrator cost drops ~90%.                                         │
│   Total cost per run: ~$0.21                                            │
│   Cost per 1K runs: $210  (savings: ~$285, -58%)                       │
│   Trade-off: ~4pp routing accuracy loss                                 │
│                                                                         │
│ Optimization 2: + Context pruning between hops                         │
│   Reduce input accumulation by ~40%.                                    │
│   Cost per 1K runs: ~$140  (savings: ~$355, -72%)                      │
│                                                                         │
│ Optimization 3: + DeltaChannel (incremental deltas, not full state)    │
│   Checkpoint storage cost drops. Run cost unchanged.                   │
│   Infrastructure savings: ~60% reduction in state storage.             │
│                                                                         │
│ Nuclear option: Hard budget ceiling ($5 per run) to prevent            │
│   infinite agentic loops. A naive setup can turn a $0.05 task into     │
│   a $5.00 infinite loop without triggering a single error.             │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Latency SLA Targets

```
┌──────────────────┬───────────┬───────────┬───────────┬────────────────────┐
│ Topology         │ p50       │ p95       │ p99       │ Driver             │
├──────────────────┼───────────┼───────────┼───────────┼────────────────────┤
│ Pipeline         │ N*2s      │ N*4s      │ N*8s      │ Additive per stage.│
│ (N stages)       │ (5 stg:   │ (5 stg:   │ (5 stg:   │ Predictable.       │
│                  │  10s)     │  20s)     │  40s)     │                    │
├──────────────────┼───────────┼───────────┼───────────┼────────────────────┤
│ Orchestrator-    │ max(W)    │ max(W)    │ max(W)    │ Wall-clock =       │
│ Worker (parallel)│ + 3s      │ + 6s      │ + 12s     │ slowest worker +   │
│                  │ (~5s)     │ (~10s)    │ (~20s)    │ orchestrator.      │
│                  │           │           │           │ Up to 90% faster   │
│                  │           │           │           │ than sequential.   │
├──────────────────┼───────────┼───────────┼───────────┼────────────────────┤
│ Hierarchical     │ 2*D*T_avg │ 2*D*4s    │ 2*D*8s    │ Down + up. Info    │
│ (D levels)       │ (2 lvl:   │ (2 lvl:   │ (2 lvl:   │ loss each level.   │
│                  │  8s)      │  16s)     │  32s)     │                    │
├──────────────────┼───────────┼───────────┼───────────┼────────────────────┤
│ Swarm / Mesh     │ Unpredict │ Unpredict │ Unpredict │ Convergence-       │
│                  │ -able     │ -able     │ -able     │ dependent. Debate  │
│                  │ (5-30s)   │ (30-120s) │ (120s+)   │ rounds multiply.   │
└──────────────────┴───────────┴───────────┴───────────┴────────────────────┘
```

### 3.4 Throughput Capacity Planning

```
Orchestrator bottleneck calculation:
  ~3s per orchestrator call (LLM inference + routing logic)
  With 20 workers: ~7 tasks/sec ceiling
  With rate-limited API (100 RPM): 1.67 tasks/sec shared across all agents

Scaling strategies:
  1. Horizontal: Multiple orchestrator instances with task partitioning
  2. Cheap routing: Use fast classifier (gpt-4o-mini, <500ms) for dispatch,
     expensive model (Opus/GPT-4o) only for workers
  3. Batch: Group independent subtasks, dispatch as batch to worker pool
  4. Cache: Memoize identical subtask results (20-35% cost reduction)
```

### 3.5 NFR Summary

```
┌──────────────────┬───────────────────────────────────────────────────────┐
│ NFR              │ Target / Constraint                                   │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Reliability      │ Design for p^N decay. 3 agents at 95% = 85.7%.      │
│                  │ Add verifier agents to raise per-agent p, not more   │
│                  │ agents to the chain.                                  │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Cost ceiling     │ Hard limit at 3 levels: step (25), token (100K),     │
│                  │ dollar ($5). Any breach = terminate + log.           │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Latency SLA      │ Pipeline: N*2s p50. Orch-worker: max(W)+3s p50.     │
│                  │ Set wall-clock timeouts per agent (30s default).     │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Throughput        │ 7 tasks/sec ceiling with single orchestrator.       │
│                  │ Scale horizontally for >10 tasks/sec.               │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Observability    │ OpenTelemetry GenAI spans at every agent boundary.   │
│                  │ W3C Trace Context across MCP calls (SEP-414).       │
├──────────────────┼───────────────────────────────────────────────────────┤
│ Auditability     │ Append-only provenance log per agent action.         │
│                  │ Pipeline: best. Swarm/Mesh: requires external        │
│                  │ provenance tracking in structured task objects.      │
└──────────────────┴───────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution Patterns

Five approaches to state persistence in multi-agent systems, each with different consistency guarantees:

```
┌──────────────────────┬──────────────────────────────┬────────────────────────┐
│ Approach             │ Mechanism                     │ Framework              │
├──────────────────────┼──────────────────────────────┼────────────────────────┤
│ Checkpointed State   │ Full state serialized at      │ LangGraph (primary)    │
│                      │ each node. Supports replay    │                        │
│                      │ and time-travel debugging.    │                        │
├──────────────────────┼──────────────────────────────┼────────────────────────┤
│ Delta Channels       │ Only incremental diffs per    │ LangGraph DeltaChannel │
│                      │ checkpoint. Explicit fix for  │ (beta, May 2026)       │
│                      │ context accumulation cost.    │                        │
├──────────────────────┼──────────────────────────────┼────────────────────────┤
│ Event-Sourced        │ All state changes recorded    │ CrewAI Flows API       │
│                      │ as immutable events. Full     │                        │
│                      │ replay from event log.        │                        │
├──────────────────────┼──────────────────────────────┼────────────────────────┤
│ Durable Execution    │ State persistence + native    │ OpenAI Agents SDK      │
│                      │ tracing. Built-in durability. │                        │
├──────────────────────┼──────────────────────────────┼────────────────────────┤
│ Ephemeral            │ No state persistence. State-  │ OpenAI Swarm (orig.)   │
│                      │ less by design. Crash = lost. │ (educational only)     │
└──────────────────────┴──────────────────────────────┴────────────────────────┘
```

**State consistency challenges**: Race conditions when parallel agents write simultaneously. Context drift via free-text handoffs (telephone game: "summarize Q3 earnings" morphs into "extract key quotes" then becomes "bullet list of revenue figures"). Mitigation: externalize shared state into a structured, canonical task object with typed fields. Make the `objective` field immutable and hash it for drift detection at each handoff.

### 4.2 Failure Taxonomy (MAST, NeurIPS 2025)

From 1,642 execution traces across 7 frameworks, inter-rater agreement kappa = 0.88:

```
┌────────────────────────────────────────────────────────────────────────────┐
│              MAST FAILURE TAXONOMY (14 modes, 3 categories)                │
│                                                                            │
│  ┌─────────────────────────────────────────────────────┐                  │
│  │ SPECIFICATION PROBLEMS          41.77% of failures   │                  │
│  │                                                      │                  │
│  │ Role ambiguity, unclear task definitions, missing    │                  │
│  │ constraints. Agents don't know what success means.   │                  │
│  └─────────────────────────────────────────────────────┘                  │
│  ┌─────────────────────────────────────────────────────┐                  │
│  │ COORDINATION FAILURES           36.94% of failures   │                  │
│  │                                                      │                  │
│  │ Communication breakdowns, state sync issues,         │                  │
│  │ conflicting objectives between agents.               │                  │
│  └─────────────────────────────────────────────────────┘                  │
│  ┌─────────────────────────────────────────────────────┐                  │
│  │ VERIFICATION GAPS               21.30% of failures   │                  │
│  │                                                      │                  │
│  │ Inadequate testing, missing validation, absent       │                  │
│  │ output quality checks at handoff boundaries.         │                  │
│  └─────────────────────────────────────────────────────┘                  │
│                                                                            │
│  Key finding: 79% of production breakdowns trace to specification and     │
│  coordination, NOT model errors. Better models alone are insufficient.    │
│  Multi-agent LLM systems fail 41-86.7% of the time in production.        │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 The Seven Production Failure Modes

```
┌────┬────────────────────────┬──────────┬─────────────────────────────────────┐
│ #  │ Failure Mode           │ Severity │ Mitigation                          │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 1  │ Cascading Errors       │ High     │ Schema validation + verifier at     │
│    │ Small inaccuracy       │          │ every handoff. Typed I/O (Pydantic) │
│    │ compounds through      │          │ with retry logic.                   │
│    │ pipeline.              │          │                                     │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 2  │ Coordination Deadlock  │ High     │ Explicit orchestration topology.    │
│    │ Circular deps: A->B   │          │ Hard timeouts on every agent call.  │
│    │ ->C->A. Entire         │          │ DAG structure prevents cycles.      │
│    │ workflow hangs.        │          │                                     │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 3  │ Context Drift          │ Med-High │ Immutable objective fields.         │
│    │ Goals degrade through  │          │ Provenance tracking. Hash objective │
│    │ free-text handoffs.    │          │ at each boundary.                   │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 4  │ Infinite Agentic Loops │ Critical │ Triple budget: step limit, token    │
│    │ Unbounded critique-    │          │ cap, dollar ceiling. Terminate if   │
│    │ refine cycles. 68      │          │ improvement <5% over last 3 iters. │
│    │ confirmed in 47        │          │ IAL-Scan: 91.9% detection prec.    │
│    │ projects (6,549 repos).│          │                                     │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 5  │ Silent Partial Failure │ High     │ Output evaluation gates at every    │
│    │ Agent hits 503, logs   │ (most    │ handoff scoring confidence,         │
│    │ it, pipeline completes │ insid-   │ groundedness, completeness.         │
│    │ with green dashboards. │ ious)    │ "Score outputs, not success codes." │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 6  │ Inter-Agent            │ Med-High │ Explicit output contracts with      │
│    │ Misalignment           │          │ specific parameters. Alignment      │
│    │ Conflicting implicit   │          │ tests before integration. Agent A   │
│    │ definitions from       │          │ returns 50 docs but Agent B expects │
│    │ vague role prompts.    │          │ 3-5 highly relevant.                │
├────┼────────────────────────┼──────────┼─────────────────────────────────────┤
│ 7  │ Tool / Data            │ Critical │ MCP with strict validation. Least-  │
│    │ Corruption             │          │ privilege scoping. Tool output      │
│    │ Stale/poisoned data.   │          │ schema validation. Per-agent tool   │
│    │ Prompt injection       │          │ allowlists.                         │
│    │ through tool I/O.      │          │                                     │
└────┴────────────────────────┴──────────┴─────────────────────────────────────┘
```

### 4.4 The 17x Error Amplification Rule (Google DeepMind)

From 180 configurations across 5 architectures and 3 LLM families:

```
Unstructured multi-agent (mesh):     Up to 17.2x error amplification
Centralized coordination:             ~4.4x (acts as circuit breaker)
Practical agent ceiling:              ~4 agents

Task-specific results:
  Highly decomposable (Finance-Agent): Centralized +80.8% vs single
  Dynamic web nav (BrowseComp-Plus):   Decentralized +9.2%
  Business planning (WorkBench):       Decentralized +5.7%
  Strictly sequential (PlanCraft):     ALL MAS variants DEGRADED (-39% to -70%)
```

### 4.5 Multi-Agent Threat Model

Three compounding risks specific to multi-agent that do not exist in single-agent:

**1. Prompt Injection Propagation**: Injection in one agent propagates across the chain. Intermediate agents can reformat malicious instructions to be more effective downstream. Per-hop filtering alone is insufficient. ICLR 2025 finding: LLMs cannot reliably separate instructions from data; external architectural enforcement is mandatory.

**2. Privilege Escalation via Implicit Trust**: A compromised low-privilege agent can influence a higher-privilege agent to perform unsafe actions. Without mutual cryptographic authentication at agent-to-agent interfaces, a lower-tier agent can spoof a high-privilege orchestration agent.

**3. Data Leakage Across Domain Boundaries**: Shared context or RAG retrieval channels leak regulated data across agent boundaries.

**Real-world attacks**:
- **EchoLeak (CVE-2025-32711)**: Zero-click prompt injection against Microsoft 365 Copilot. Single crafted email caused Copilot to access files across mailbox, OneDrive, SharePoint, Teams, then exfiltrate to attacker-controlled server.
- **Self-replicating email infections**: Payloads processed by one LLM agent append themselves to all outgoing messages. >80% harmful action success rate using GPT-4o in simulated environments.

### 4.6 Permission Boundaries & Trust Propagation

**Least Agency principle** (OWASP Agentic Top 10, 2026):

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    PERMISSION BOUNDARY MODEL                              │
│                                                                           │
│  Per-agent tool allowlist:                                                │
│    Agent_search: [web_search, read_file]     -- no shell, no DB          │
│    Agent_db:     [db_query, read_file]        -- no shell, no web        │
│    Agent_email:  [send_email]                 -- nothing else            │
│                                                                           │
│  Every agent-to-agent boundary = trust boundary (like external API).     │
│  Cap result sizes per tool (100KB limit).                                │
│  Agents needing one DB table must not access the shell.                  │
│                                                                           │
│  Authorization token chain (IBCTs):                                      │
│    JWT for single-hop.                                                    │
│    Biscuit tokens with Datalog policies for multi-hop delegation.        │
│    0.049ms verification latency.                                          │
│    100% adversarial rejection across 600 attack attempts.                │
│                                                                           │
│  Signed Agent Cards (A2A v1.0):                                          │
│    Cryptographic identity verification at every agent boundary.          │
│    Prevents lower-tier agent from spoofing orchestrator.                 │
│                                                                           │
│  Mitigations for trust propagation:                                      │
│    - Zero-trust for non-human identities                                 │
│    - Bidirectional runtime guardrails                                    │
│    - Log-based auditing with LSTM/autoencoder anomaly detection          │
│    - Real-time trust scoring among agents                                │
└──────────────────────────────────────────────────────────────────────────┘
```

### 4.7 Compliance Landscape

```
┌────────────────────┬──────────────────────────────────────────────────────┐
│ Regulation          │ Agent-Relevant Requirements                        │
├────────────────────┼──────────────────────────────────────────────────────┤
│ SOC 2 Type II       │ CC6.1: Every agent under named service account.   │
│                     │ CC6.2: Permissions registered, scoped, revocable.  │
│                     │ CC9.2: LLM API providers as subservice orgs.      │
├────────────────────┼──────────────────────────────────────────────────────┤
│ EU AI Act           │ Phased: bans Feb 2025, GPAI Aug 2025,             │
│                     │ high-risk Aug 2026.                                │
├────────────────────┼──────────────────────────────────────────────────────┤
│ OWASP Agentic       │ Least Agency framework. Tool whitelisting.        │
│ Top 10 (2026)       │ Permission scoping per agent.                     │
├────────────────────┼──────────────────────────────────────────────────────┤
│ NIST NCCoE          │ Concept paper on standards-based AI agent         │
│ (Feb 2026)          │ identity and authorization (concept-stage only).  │
├────────────────────┼──────────────────────────────────────────────────────┤
│ OCC Bulletin        │ Acknowledges agentic AI as "novel and rapidly     │
│ 2026-13             │ evolving" -- not yet scoped in model risk.        │
└────────────────────┴──────────────────────────────────────────────────────┘

Key gap: Regulatory frameworks exist but agent-specific implementation
guidance is still catching up. Organizations building without governance
in 2025-2026 are the ones canceling projects in 2027.
```

### 4.8 Audit Trails

Pipeline topology provides the strongest audit trail: every decision traceable to exactly one step. Stripe's business verification agents achieved 96% helpfulness rating with full per-step audit.

For hierarchical and swarm topologies, externalize provenance tracking in structured task objects:

```python
@dataclass
class ProvenanceRecord:
    agent_id: str
    action: str
    timestamp: float
    input_hash: str
    output_hash: str
    parent_span_id: str
    objective_hash: str   # detect context drift

# Every modification appended to immutable provenance list
task.provenance.append(record)
```

OpenTelemetry GenAI semantic conventions (`invoke_agent`, `execute_tool`, `create_agent`, `invoke_workflow` spans) provide the instrumentation standard. W3C Trace Context propagation via MCP `_meta` field (SEP-414) enables correlation of parallel traces across agent boundaries.

---

## 5. Production Enterprise Code

### 5.1 Multi-Agent Orchestrator with Worker Pool, Budget Enforcement, and Graceful Degradation

```python
"""
Production multi-agent orchestrator implementing:
  - Orchestrator-worker topology with parallel worker dispatch
  - Three-level budget enforcement (steps, tokens, dollars)
  - Per-worker circuit breakers
  - Typed inter-agent message schemas (Pydantic)
  - Structured logging with OpenTelemetry-compatible trace IDs
  - Graceful degradation when workers fail
  - Context drift detection via objective hashing
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field


# ── Structured Logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s [%(name)s]",
)
logger = logging.getLogger("multi_agent_orchestrator")


def structured_log(
    level: str,
    event: str,
    trace_id: str,
    **kwargs: Any,
) -> None:
    """Emit structured log entry compatible with OpenTelemetry ingestion."""
    entry = {"event": event, "trace_id": trace_id, **kwargs}
    getattr(logger, level)(json.dumps(entry, default=str))


# ── Budget Enforcement ──────────────────────────────────────────────────


class BudgetExhausted(Exception):
    """Raised when any budget level (step, token, dollar) is breached."""

    def __init__(self, level: str, consumed: float, ceiling: float):
        self.level = level
        self.consumed = consumed
        self.ceiling = ceiling
        super().__init__(
            f"Budget exhausted: {level} consumed={consumed} ceiling={ceiling}"
        )


@dataclass
class BudgetEnvelope:
    """Three-level budget enforcement. Any breach terminates execution."""

    max_steps: int = 25
    max_tokens: int = 100_000
    max_dollars: float = 5.00

    _steps_used: int = field(default=0, init=False)
    _tokens_used: int = field(default=0, init=False)
    _dollars_used: float = field(default=0.0, init=False)

    def record_step(self, tokens: int, cost_dollars: float) -> None:
        self._steps_used += 1
        self._tokens_used += tokens
        self._dollars_used += cost_dollars
        self._check()

    def _check(self) -> None:
        if self._steps_used > self.max_steps:
            raise BudgetExhausted("steps", self._steps_used, self.max_steps)
        if self._tokens_used > self.max_tokens:
            raise BudgetExhausted("tokens", self._tokens_used, self.max_tokens)
        if self._dollars_used > self.max_dollars:
            raise BudgetExhausted("dollars", self._dollars_used, self.max_dollars)

    @property
    def remaining(self) -> dict[str, float]:
        return {
            "steps": self.max_steps - self._steps_used,
            "tokens": self.max_tokens - self._tokens_used,
            "dollars": round(self.max_dollars - self._dollars_used, 4),
        }


# ── Circuit Breaker (per-worker) ────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


@dataclass
class CircuitBreaker:
    """Per-worker circuit breaker. Prevents one failing worker from
    consuming budget while repeatedly failing."""

    worker_id: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 30.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._last_failure_time
            >= self.recovery_timeout_s
        ):
            self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        self._consecutive_failures = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
        elif self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN

    def allow_request(self) -> bool:
        return self.state != CircuitState.OPEN


# ── Inter-Agent Message Schemas ─────────────────────────────────────────


class SubTask(BaseModel):
    """Typed schema for orchestrator -> worker communication."""

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    description: str
    objective: str  # immutable -- copied from original request
    objective_hash: str  # SHA-256 of objective for drift detection
    context: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = 10
    assigned_worker: str = ""


class WorkerResult(BaseModel):
    """Typed schema for worker -> orchestrator communication."""

    task_id: str
    worker_id: str
    status: str  # "success" | "partial" | "failed"
    output: Any = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    tokens_used: int = 0
    cost_dollars: float = 0.0
    error: str | None = None
    objective_hash: str = ""  # for drift verification


# ── Worker Agent ────────────────────────────────────────────────────────


@dataclass
class WorkerAgent:
    """Individual worker agent with scoped tool access and isolated context.
    In production, each worker wraps an LLM call with tool-calling enabled.
    This implementation shows the execution envelope; replace execute_task
    with your LLM client call."""

    worker_id: str
    model: str  # e.g., "claude-sonnet-4-20250514"
    allowed_tools: list[str] = field(default_factory=list)
    circuit_breaker: CircuitBreaker = field(init=False)

    def __post_init__(self) -> None:
        self.circuit_breaker = CircuitBreaker(worker_id=self.worker_id)

    async def execute(
        self,
        subtask: SubTask,
        trace_id: str,
    ) -> WorkerResult:
        """Execute a subtask within budget and circuit breaker constraints.
        Replace the body with your LLM client call in production."""
        if not self.circuit_breaker.allow_request():
            structured_log(
                "warning",
                "worker_circuit_open",
                trace_id,
                worker_id=self.worker_id,
                task_id=subtask.task_id,
            )
            return WorkerResult(
                task_id=subtask.task_id,
                worker_id=self.worker_id,
                status="failed",
                error="Circuit breaker OPEN -- worker unavailable",
                objective_hash=subtask.objective_hash,
            )

        start = time.monotonic()
        try:
            # ── Replace with actual LLM call ──
            # result = await llm_client.chat(
            #     model=self.model,
            #     messages=[{"role": "user", "content": subtask.description}],
            #     tools=self.allowed_tools,
            # )
            # Simulated execution for demonstration:
            await asyncio.sleep(0.1)
            output = f"[{self.worker_id}] processed: {subtask.description[:80]}"
            tokens = 1500
            cost = tokens * 3.0 / 1_000_000  # $3/1M input tokens estimate
            # ── End replacement block ──

            elapsed_ms = (time.monotonic() - start) * 1000
            self.circuit_breaker.record_success()

            structured_log(
                "info",
                "worker_task_complete",
                trace_id,
                worker_id=self.worker_id,
                task_id=subtask.task_id,
                latency_ms=round(elapsed_ms, 1),
                tokens=tokens,
            )

            return WorkerResult(
                task_id=subtask.task_id,
                worker_id=self.worker_id,
                status="success",
                output=output,
                confidence=0.85,
                tokens_used=tokens,
                cost_dollars=cost,
                objective_hash=subtask.objective_hash,
            )

        except Exception as exc:
            self.circuit_breaker.record_failure()
            elapsed_ms = (time.monotonic() - start) * 1000

            structured_log(
                "error",
                "worker_task_failed",
                trace_id,
                worker_id=self.worker_id,
                task_id=subtask.task_id,
                error=str(exc),
                latency_ms=round(elapsed_ms, 1),
            )

            return WorkerResult(
                task_id=subtask.task_id,
                worker_id=self.worker_id,
                status="failed",
                error=str(exc),
                objective_hash=subtask.objective_hash,
            )


# ── Orchestrator ────────────────────────────────────────────────────────


def _hash_objective(objective: str) -> str:
    """Deterministic hash of the immutable objective for drift detection."""
    return hashlib.sha256(objective.encode()).hexdigest()[:16]


@dataclass
class Orchestrator:
    """Multi-agent orchestrator implementing hub-and-spoke topology.

    Responsibilities:
      1. Decompose incoming task into independent subtasks
      2. Dispatch subtasks to workers in parallel
      3. Enforce budget at every step
      4. Detect context drift via objective hash comparison
      5. Synthesize worker results, degrading gracefully on partial failure
      6. Log provenance for audit trail
    """

    workers: dict[str, WorkerAgent] = field(default_factory=dict)
    budget: BudgetEnvelope = field(default_factory=BudgetEnvelope)
    min_confidence: float = 0.5  # below this, result treated as partial failure

    def register_worker(self, worker: WorkerAgent) -> None:
        self.workers[worker.worker_id] = worker

    async def run(
        self,
        task: str,
        decompose_fn: Callable[[str], Awaitable[list[dict[str, Any]]]],
    ) -> dict[str, Any]:
        """Execute full orchestrator-worker cycle.

        Args:
            task: Natural language task description.
            decompose_fn: Async function that takes a task string and returns
                a list of dicts with keys 'description' and 'assigned_worker'.
                In production, this wraps an LLM call that produces the
                decomposition plan.
        """
        trace_id = uuid.uuid4().hex[:16]
        objective_hash = _hash_objective(task)

        structured_log("info", "orchestration_start", trace_id, task=task[:200])

        # ── Step 1: Decompose ──
        try:
            subtask_specs = await decompose_fn(task)
        except Exception as exc:
            structured_log(
                "error", "decomposition_failed", trace_id, error=str(exc)
            )
            return {"status": "failed", "error": f"Decomposition failed: {exc}"}

        subtasks = []
        for spec in subtask_specs:
            worker_id = spec.get("assigned_worker", "")
            if worker_id not in self.workers:
                structured_log(
                    "warning",
                    "unknown_worker_in_plan",
                    trace_id,
                    worker_id=worker_id,
                    available=list(self.workers.keys()),
                )
                continue

            subtasks.append(
                SubTask(
                    description=spec["description"],
                    objective=task,
                    objective_hash=objective_hash,
                    context=spec.get("context", {}),
                    assigned_worker=worker_id,
                )
            )

        if not subtasks:
            return {"status": "failed", "error": "No valid subtasks after decomposition"}

        structured_log(
            "info",
            "dispatch_plan",
            trace_id,
            subtask_count=len(subtasks),
            workers=[s.assigned_worker for s in subtasks],
        )

        # ── Step 2: Parallel dispatch ──
        async def _run_worker(subtask: SubTask) -> WorkerResult:
            worker = self.workers[subtask.assigned_worker]
            result = await worker.execute(subtask, trace_id)

            # Budget accounting per worker result
            self.budget.record_step(
                tokens=result.tokens_used,
                cost_dollars=result.cost_dollars,
            )
            return result

        results: list[WorkerResult] = await asyncio.gather(
            *[_run_worker(st) for st in subtasks],
            return_exceptions=False,
        )

        # ── Step 3: Drift detection ──
        drifted = [
            r for r in results
            if r.objective_hash and r.objective_hash != objective_hash
        ]
        if drifted:
            structured_log(
                "warning",
                "context_drift_detected",
                trace_id,
                drifted_workers=[r.worker_id for r in drifted],
            )

        # ── Step 4: Synthesize with graceful degradation ──
        successful = [r for r in results if r.status == "success"]
        partial = [
            r for r in results
            if r.status == "success" and r.confidence < self.min_confidence
        ]
        failed = [r for r in results if r.status != "success"]

        if partial:
            structured_log(
                "warning",
                "low_confidence_results",
                trace_id,
                workers=[r.worker_id for r in partial],
                confidences=[r.confidence for r in partial],
            )

        # Graceful degradation: proceed with whatever succeeded
        if not successful:
            structured_log("error", "all_workers_failed", trace_id)
            return {
                "status": "failed",
                "error": "All workers failed",
                "failures": [
                    {"worker": r.worker_id, "error": r.error} for r in failed
                ],
                "trace_id": trace_id,
                "budget_remaining": self.budget.remaining,
            }

        synthesis = {
            "status": "success" if not failed else "partial",
            "results": [
                {
                    "worker": r.worker_id,
                    "output": r.output,
                    "confidence": r.confidence,
                }
                for r in successful
            ],
            "failed_workers": [r.worker_id for r in failed],
            "trace_id": trace_id,
            "budget_remaining": self.budget.remaining,
        }

        structured_log(
            "info",
            "orchestration_complete",
            trace_id,
            status=synthesis["status"],
            successful=len(successful),
            failed=len(failed),
            budget_remaining=self.budget.remaining,
        )

        return synthesis


# ── Retry with Exponential Backoff and Decorrelated Jitter ──────────────


async def retry_with_backoff(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    jitter_factor: float = 0.5,
    retryable: tuple[type[Exception], ...] = (TimeoutError, ConnectionError),
    trace_id: str = "",
    **kwargs: Any,
) -> Any:
    """Retry with exponential backoff and decorrelated jitter (AWS-style).

    Uses full jitter: delay = random(0, min(max_delay, base * 2^attempt)).
    Prevents thundering herd when multiple agents retry simultaneously.
    """
    last_exc: Exception | None = None

    for attempt in range(1 + max_retries):
        try:
            return await fn(*args, **kwargs)
        except retryable as exc:
            last_exc = exc
            if attempt == max_retries:
                structured_log(
                    "error",
                    "retry_exhausted",
                    trace_id or "unknown",
                    attempts=attempt + 1,
                    error=str(exc),
                )
                raise

            delay = min(max_delay_s, base_delay_s * (2 ** attempt))
            jittered = delay * (1.0 - jitter_factor + jitter_factor * random.random())

            structured_log(
                "warning",
                "retry_attempt",
                trace_id or "unknown",
                attempt=attempt + 1,
                delay_s=round(jittered, 2),
                error=str(exc),
            )
            await asyncio.sleep(jittered)

    raise last_exc  # type: ignore[misc]


# ── Fallback Chain ──────────────────────────────────────────────────────


async def fallback_chain(
    strategies: list[tuple[str, Callable[..., Awaitable[Any]]]],
    *args: Any,
    trace_id: str = "",
    **kwargs: Any,
) -> Any:
    """Try strategies in order. First success wins. All fail = raise last error.

    Each strategy is a (name, async_callable) pair. Useful for:
      - Primary model -> cheaper fallback model
      - Live API -> cached result -> parametric approximation
    """
    last_exc: Exception | None = None

    for name, fn in strategies:
        try:
            result = await fn(*args, **kwargs)
            structured_log(
                "info",
                "fallback_success",
                trace_id,
                strategy=name,
            )
            return result
        except Exception as exc:
            last_exc = exc
            structured_log(
                "warning",
                "fallback_failed",
                trace_id,
                strategy=name,
                error=str(exc),
            )

    raise last_exc  # type: ignore[misc]


# ── Usage Example ───────────────────────────────────────────────────────


async def main() -> None:
    """Demonstrate the orchestrator with 3 workers processing a task."""

    # Register workers with different capabilities
    orch = Orchestrator(
        budget=BudgetEnvelope(max_steps=50, max_tokens=200_000, max_dollars=10.0),
        min_confidence=0.5,
    )

    orch.register_worker(WorkerAgent(
        worker_id="research",
        model="claude-sonnet-4-20250514",
        allowed_tools=["web_search", "read_file"],
    ))
    orch.register_worker(WorkerAgent(
        worker_id="analysis",
        model="claude-sonnet-4-20250514",
        allowed_tools=["db_query", "calculate"],
    ))
    orch.register_worker(WorkerAgent(
        worker_id="writer",
        model="claude-haiku-4-20250414",
        allowed_tools=["format_output"],
    ))

    # Decomposition function (replace with LLM call in production)
    async def decompose(task: str) -> list[dict[str, Any]]:
        return [
            {
                "description": "Research recent market data for the query",
                "assigned_worker": "research",
            },
            {
                "description": "Analyze trends and compute statistics",
                "assigned_worker": "analysis",
            },
            {
                "description": "Write a summary report from findings",
                "assigned_worker": "writer",
            },
        ]

    result = await orch.run(
        task="Analyze Q3 2026 cloud infrastructure spending trends",
        decompose_fn=decompose,
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
```

### 5.2 Inter-Agent Message Validation with Context Drift Detection

```python
"""
Schema-enforced inter-agent message validation.
Catches the silent partial failure and context drift patterns
at every handoff boundary.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pydantic import BaseModel, Field, model_validator


class HandoffMessage(BaseModel):
    """Validated message passed between agents at every handoff.

    The objective_hash field enables drift detection: if the hash
    changes between sender and receiver, the objective has been
    mutated during free-text processing (the telephone game).
    """

    sender_agent_id: str
    receiver_agent_id: str
    original_objective: str
    objective_hash: str
    payload: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    groundedness: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    timestamp: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def verify_objective_integrity(self) -> "HandoffMessage":
        expected = hashlib.sha256(
            self.original_objective.encode()
        ).hexdigest()[:16]
        if self.objective_hash != expected:
            raise ValueError(
                f"Context drift detected: objective_hash mismatch. "
                f"Expected {expected}, got {self.objective_hash}. "
                f"The objective was mutated during handoff."
            )
        return self


class HandoffGate:
    """Quality gate applied at every agent-to-agent boundary.

    Rejects handoffs that fail confidence, groundedness, or
    completeness thresholds. Catches silent partial failures
    before they propagate downstream.
    """

    def __init__(
        self,
        min_confidence: float = 0.6,
        min_groundedness: float = 0.5,
        min_completeness: float = 0.4,
    ):
        self.min_confidence = min_confidence
        self.min_groundedness = min_groundedness
        self.min_completeness = min_completeness

    def evaluate(self, message: HandoffMessage) -> tuple[bool, list[str]]:
        """Returns (passed, list_of_violations)."""
        violations: list[str] = []

        if message.confidence < self.min_confidence:
            violations.append(
                f"confidence {message.confidence:.2f} < {self.min_confidence}"
            )
        if message.groundedness < self.min_groundedness:
            violations.append(
                f"groundedness {message.groundedness:.2f} < {self.min_groundedness}"
            )
        if message.completeness < self.min_completeness:
            violations.append(
                f"completeness {message.completeness:.2f} < {self.min_completeness}"
            )

        return len(violations) == 0, violations
```

### 5.3 Diminishing Returns Detector (Infinite Loop Prevention)

```python
"""
Detects infinite agentic loops by tracking improvement across iterations.
IAL-Scan found 68 confirmed infinite loop failures across 47 projects
(6,549 repos examined). This is not a corner case -- it is a design
pattern shipped to production regularly.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class DiminishingReturnsDetector:
    """Terminate critique-refine loops when improvement drops below threshold.

    Tracks a rolling window of quality scores. If the improvement across
    the last `window_size` iterations is below `min_improvement_pct`,
    signals termination.
    """

    window_size: int = 3
    min_improvement_pct: float = 5.0  # terminate if <5% improvement
    max_iterations: int = 25          # hard ceiling regardless of improvement

    _scores: deque[float] = field(init=False)
    _iteration: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._scores = deque(maxlen=self.window_size + 1)

    def record(self, quality_score: float) -> None:
        """Record the quality score for the current iteration."""
        self._iteration += 1
        self._scores.append(quality_score)

    def should_terminate(self) -> tuple[bool, str]:
        """Check if the loop should be terminated.

        Returns (should_stop, reason).
        """
        if self._iteration >= self.max_iterations:
            return True, f"Hard ceiling reached: {self.max_iterations} iterations"

        if len(self._scores) <= self.window_size:
            return False, "Insufficient data for trend analysis"

        oldest = self._scores[0]
        newest = self._scores[-1]

        if oldest == 0:
            return False, "Cannot compute improvement from zero baseline"

        improvement_pct = ((newest - oldest) / abs(oldest)) * 100

        if improvement_pct < self.min_improvement_pct:
            return True, (
                f"Diminishing returns: {improvement_pct:.1f}% improvement "
                f"over last {self.window_size} iterations "
                f"(threshold: {self.min_improvement_pct}%)"
            )

        return False, f"Improvement {improvement_pct:.1f}% above threshold"


# Usage in a critique-refine loop:
#
# detector = DiminishingReturnsDetector(window_size=3, min_improvement_pct=5.0)
#
# for i in range(50):
#     result = await refine_agent.execute(draft)
#     score = await evaluator_agent.score(result)
#     detector.record(score)
#
#     should_stop, reason = detector.should_terminate()
#     if should_stop:
#         logger.info(f"Terminating loop: {reason}")
#         break
#
#     draft = result
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Agent Document Processing Pipeline (Regulated Financial Services)

**Problem statement**: A financial services firm processes 50,000 regulatory filings per month. Each filing must be classified (10 document types), key entities extracted (counterparties, amounts, dates, obligations), cross-referenced against an internal compliance database, and routed for human review if risk indicators are present. Current manual process: 8 minutes per document, 15 FTE. Target: reduce to <60 seconds end-to-end with full audit trail for SOC 2 compliance.

**Architecture (Pipeline topology -- chosen for auditability)**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DOCUMENT PROCESSING PIPELINE                        │
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Stage 1:     │  │ Stage 2:     │  │ Stage 3:      │  │ Stage 4:          │ │
│  │ Classifier   │  │ Extractor    │  │ Cross-Ref     │  │ Risk Router       │ │
│  │              │  │              │  │               │  │                   │ │
│  │ Model:       │  │ Model:       │  │ Model:        │  │ Model:            │ │
│  │ Haiku (fast, │  │ Sonnet       │  │ Sonnet        │  │ Haiku (fast       │ │
│  │ cheap for    │──▶│ (structured  │──▶│ (RAG over     │──▶│ classification)  │ │
│  │ 10-class)    │  │ extraction)  │  │ compliance    │  │                   │ │
│  │              │  │              │  │ DB + entity   │  │ Route:            │ │
│  │ Tools: none  │  │ Tools:       │  │ matching)     │  │  Low risk -> auto │ │
│  │              │  │ [pdf_parse]  │  │               │  │  Med risk -> queue│ │
│  │ Output:      │  │              │  │ Tools:        │  │  High risk ->     │ │
│  │ doc_type +   │  │ Output:      │  │ [db_query,    │  │   sync human gate │ │
│  │ confidence   │  │ entities{}   │  │  vector_search│  │                   │ │
│  │              │  │              │  │  ]            │  │                   │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘ │
│         │                 │                  │                   │            │
│    Schema Gate       Schema Gate        Schema Gate         Schema Gate      │
│    (doc_type in      (all required      (match_score       (risk_level in   │
│     valid set,       entities present,  >= 0.7 or          {low,med,high},  │
│     conf >= 0.8)     Pydantic valid)    flag for review)   decision logged) │
│         │                 │                  │                   │            │
│    Reject: re-       Reject: retry      Reject: manual     Reject: human   │
│    classify with     extraction with    review queue       escalation       │
│    Sonnet fallback   broader prompt                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PERSISTENCE + AUDIT                                  │
│                                                                              │
│  Every stage writes to append-only provenance log:                          │
│    {stage, agent_id, input_hash, output_hash, timestamp, model, tokens}    │
│  Satisfies SOC 2 CC6.1 (named agent identity) and CC6.2 (scoped perms).   │
│  Full replay from any checkpoint for time-travel debugging.                │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌─────────────────────┬─────────────────────────────────────────────────────┐
│ Dimension            │ Decision + Rationale                               │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Topology             │ Pipeline over orchestrator-worker. Regulatory      │
│                      │ requirement for step-by-step audit trail. Every    │
│                      │ decision traceable to exactly one stage.           │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Model selection      │ Haiku for classification/routing (fast, cheap).    │
│                      │ Sonnet for extraction/cross-ref (accuracy).        │
│                      │ Mixed-model pipeline cuts cost ~45% vs all-Sonnet. │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Parallelism          │ None within pipeline (sequential by design).       │
│                      │ Parallelism across documents (50 concurrent).      │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Latency              │ 4 stages * ~3s avg = ~12s p50. Acceptable vs      │
│                      │ 8-minute manual baseline. p99 ~45s with retries.  │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Cost                 │ ~$0.08/doc (Haiku stages ~$0.005, Sonnet ~$0.035  │
│                      │ each). 50K docs/month = ~$4,000. vs 15 FTE.      │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Failure handling     │ Schema gates at every boundary. Fallback chain:   │
│                      │ Haiku classifier fails -> retry with Sonnet.      │
│                      │ Extraction fails -> broader prompt + retry.       │
│                      │ Cross-ref DB down -> circuit breaker, queue doc.  │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Why not orchestrator │ Audit trail is primary requirement. Pipeline      │
│ -worker?             │ provides strongest auditability. Orchestrator     │
│                      │ pattern adds routing complexity without benefit   │
│                      │ since stages are inherently sequential.           │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Why not single agent │ Different stages need different models (cost),    │
│                      │ different tool access (security), and different   │
│                      │ validation logic (reliability). Specialization    │
│                      │ criterion is met.                                  │
└─────────────────────┴─────────────────────────────────────────────────────┘
```

**Decision rationale**: Pipeline was chosen over orchestrator-worker because the regulatory environment demands step-by-step traceability. The pipeline's "every decision traceable to exactly one step" property directly satisfies SOC 2 CC6.1 requirements. Orchestrator-worker would provide parallelism within a single document, but document-level parallelism (50 concurrent pipelines) already meets throughput targets. The mixed-model strategy (Haiku for cheap classification, Sonnet for accuracy-critical extraction) follows the cost optimization pattern of swapping cheap models into non-critical stages, achieving ~45% cost reduction with <2pp accuracy loss on classification.

---

### Scenario 2: Real-Time Customer Service Escalation Platform

**Problem statement**: An e-commerce platform handles 200,000 customer interactions per day across chat, email, and voice. Current pain: 40% of customers are misrouted to the wrong specialist, resolution time averages 12 minutes, and CSAT is 68%. Target: reduce misrouting to <5%, resolution time to <3 minutes for L1 issues, and reach 85%+ CSAT. The platform must handle 100+ products, returns, payments, account management, and technical support, with real-time escalation to human agents when confidence drops.

**Architecture (Orchestrator-Worker with Handoff Escalation)**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CUSTOMER SERVICE ESCALATION PLATFORM                       │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                       CONTROL PLANE                                    │ │
│  │  ┌──────────────┐  ┌───────────────────┐  ┌─────────────────────────┐│ │
│  │  │ Intent        │  │ Budget Enforcer    │  │ Escalation Policy      ││ │
│  │  │ Classifier    │  │                    │  │                        ││ │
│  │  │ (Haiku, <1s)  │  │ Max 10 steps/conv  │  │ Confidence < 0.6:     ││ │
│  │  │               │  │ Max $0.50/conv     │  │   -> human handoff    ││ │
│  │  │ Routes to     │  │ 60s wall-clock     │  │ 2+ failed resolutions:││ │
│  │  │ domain worker │  │ timeout per turn   │  │   -> supervisor human ││ │
│  │  └──────┬────────┘  └────────┬──────────┘  └───────────┬────────────┘│ │
│  └─────────┼────────────────────┼──────────────────────────┼────────────┘ │
│            │                    │                          │              │
│  ┌─────────▼────────────────────▼──────────────────────────▼────────────┐ │
│  │                    AGENT COORDINATION PLANE                           │ │
│  │                                                                      │ │
│  │  ┌──────────────────────────────────────────────────────────────┐   │ │
│  │  │                    ORCHESTRATOR (Sonnet)                      │   │ │
│  │  │                                                               │   │ │
│  │  │  1. Receives classified intent + customer context            │   │ │
│  │  │  2. Selects specialist worker (or parallel if ambiguous)     │   │ │
│  │  │  3. Monitors worker confidence scores in real-time           │   │ │
│  │  │  4. Triggers handoff to human if confidence drops            │   │ │
│  │  │  5. Synthesizes response with context for next turn          │   │ │
│  │  └──────────────────────┬───────────────────────────────────────┘   │ │
│  └─────────────────────────┼──────────────────────────────────────────┘ │
│                 ┌──────────┼──────────┬────────────┐                    │
│                 ▼          ▼          ▼            ▼                    │
│  ┌────────────────┐┌────────────┐┌──────────┐┌──────────────────────┐  │
│  │ Returns Worker  ││ Payments   ││ Account  ││ Technical Support    │  │
│  │                 ││ Worker     ││ Worker   ││ Worker               │  │
│  │ Model: Sonnet   ││            ││          ││                      │  │
│  │ Tools:          ││ Tools:     ││ Tools:   ││ Tools:               │  │
│  │  [order_lookup, ││ [payment_  ││ [account_││ [kb_search,          │  │
│  │   return_initia-││  status,   ││  lookup, ││  diagnostic_run,     │  │
│  │   te, shipping_ ││  refund_   ││  update_ ││  ticket_create]      │  │
│  │   track]        ││  process]  ││  profile]││                      │  │
│  │                 ││            ││          ││ Fallback: human       │  │
│  │ No payment or   ││ No account ││ No order ││ escalation for       │  │
│  │ account access. ││ mutations. ││ access.  ││ unresolved after 3   │  │
│  │                 ││            ││          ││ attempts.             │  │
│  └────────────────┘└────────────┘└──────────┘└──────────────────────┘  │
│                 │          │          │            │                    │
│                 ▼          ▼          ▼            ▼                    │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    HUMAN ESCALATION LAYER                          │ │
│  │                                                                    │ │
│  │  Agent receives full context: conversation history, worker        │ │
│  │  outputs, confidence scores, tools used, customer profile.        │ │
│  │  Resolution time target: <5 min (vs 12 min without AI triage).   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌─────────────────────┬─────────────────────────────────────────────────────┐
│ Dimension            │ Decision + Rationale                               │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Topology             │ Orchestrator-worker over pipeline. Customer        │
│                      │ intent is ambiguous at entry -- dynamic routing    │
│                      │ needed. Cannot pre-define fixed stage sequence.    │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Model selection      │ Haiku for intent classification (<1s, $0.001).    │
│                      │ Sonnet for orchestrator + workers (accuracy).      │
│                      │ Cheap classifier at front = 90%+ routing accuracy │
│                      │ at minimal cost.                                   │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Parallelism          │ Single worker per turn (most intents are clear).  │
│                      │ Parallel dispatch only for ambiguous intents       │
│                      │ (e.g., "return and refund" -> returns + payments). │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Latency              │ p50: classifier (0.5s) + orchestrator (1.5s) +    │
│                      │ worker (2s) + tool calls (1s) = ~5s per turn.     │
│                      │ Acceptable for chat. Email is async.              │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Cost                 │ ~$0.15/conversation (avg 5 turns). 200K/day =     │
│                      │ ~$30K/day = ~$900K/month. vs hiring equivalent    │
│                      │ human agents at $2M+/month (Klarna-scale math).   │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Escalation design    │ Confidence-gated. AI handles L1 (70% of volume)  │
│                      │ autonomously. L2 escalates to human with full     │
│                      │ AI-prepared context. Klarna lesson: "gone too     │
│                      │ far" with AI-only; hybrid model is the target.    │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Permission           │ Per-worker tool allowlists prevent lateral moves. │
│ boundaries           │ Returns worker cannot process refunds. Payments   │
│                      │ worker cannot modify accounts. Least agency.      │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Why not pipeline?    │ Customer conversations are non-linear. The next   │
│                      │ step depends on customer response, not a fixed    │
│                      │ sequence. Dynamic routing is mandatory.           │
├─────────────────────┼─────────────────────────────────────────────────────┤
│ Why not single agent │ 100+ products across 4 domains. Different domains │
│ with all tools?      │ need different tool access (security). Single     │
│                      │ agent with all tools = 60% over-permissioned      │
│                      │ (OWASP finding). Specialization criterion met.    │
└─────────────────────┴─────────────────────────────────────────────────────┘
```

**Decision rationale**: Orchestrator-worker was chosen because customer intents are inherently ambiguous and require dynamic routing that pipelines cannot provide. The Klarna case study informed the hybrid design: pure AI replacement failed ("gone too far"), so the architecture includes a confidence-gated human escalation layer. Per-worker tool allowlists enforce the OWASP least-agency principle: each worker can only access the tools relevant to its domain, eliminating the 60% over-permissioning risk identified in production audits. The intent classifier uses Haiku ($0.001/classification) as a cheap routing layer, following the cost optimization pattern of using fast models for dispatch and expensive models for execution. The 4-agent ceiling (DeepMind) is respected: 1 orchestrator + 4 domain workers stays within the empirically validated practical limit.
