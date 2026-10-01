# Module 09 — Multi-Agent Architecture

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 09 (platform topology after agentic control-flow patterns)  
**Grounded in**: `research/09-multi-agent-architecture.md` (22 sources, 2026-09-30)

Multi-agent architecture answers a **platform** question: who coordinates, how control and state move, and what fan-out costs. Leave a single agent when the task hits **context overflow**, needs **true parallelism** across independent threads, or requires **role/tool/permission specialization** one agent cannot safely hold ([System Design Newsletter — Multi-Agent Architectures](https://newsletter.systemdesign.one/p/multi-agent-system); [Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)). Counterexample: Cognition’s Devin processed **5M lines of COBOL** across **500GB** of repos and raised PR merge rate **34% → 67%** without multi-agent fan-out ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Control-flow patterns (ReAct, chaining) live in module 08. **A2A (Agent-to-Agent)** is a later roadmap topic — noted here only as a cross-vendor interoperability boundary, not specified.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  Supervisor / lead · handoff tools · graph edges         │
                         │  fan-out caps (1 / 2–4 / >10) · max_turns · recursion    │
                         │  Sequential / Parallel / Loop workflow agents            │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Supervisor │  │ Swarm      │  │ Effort / budget    │  │
                         │  │ + workers  │  │ active_agt │  │ enforcer           │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  Per-agent contexts · shared messages / session.state     │
                         │  plan + subagent results · artifact refs · citations     │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  MCP servers (tools) │  │  checkpointer       │  │  interaction     │
              │  per-specialist RBAC │  │  active_agent       │  │  structure traces│
              │  AgentTool sandbox   │  │  Memory / plan      │  │  tokens · $ · ms │
              │  schema validate     │  │  artifact store     │  │  correlation IDs │
              │  before_tool policy  │  │  thread_id / sess   │  │  fan-out count   │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities** [inferred from framework docs + research §1]

| Plane | Role in multi-agent systems |
| --- | --- |
| **CONTROL PLANE** | Supervisor/lead routing, handoff tools (`transfer_to_*`, `transfer_to_agent`), graph edges, workflow agents (`Sequential`/`Parallel`/`Loop`), recursion/`max_turns` caps, fan-out effort budgets |
| **DATA PLANE** | Per-agent context windows, shared `messages` / `session.state`, artifact stores / filesystem refs, citation passes |
| **PERSISTENCE** | Checkpointer (incl. swarm `active_agent`), Memory for plans beyond **200k** tokens, artifact store for large worker outputs |
| **TOOL PROXIES** | MCP-style tool servers (agent↔tool/data — **not** peer agent messaging); per-specialist allowlists; `AgentTool` isolated child sessions |
| **TELEMETRY** | Decision/interaction structure (not raw conversation contents in Anthropic’s production framing); tokens, $, wall-clock, fan-out, breaker state, correlation IDs |

**Protocol boundary:** MCP connects agents to tools/data. **A2A** (later topic) is the emerging agent-to-agent interoperability layer across vendors — do not treat MCP as peer agent messaging ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

### Canonical topologies

| Topology | Coordination | Framework analogues |
| --- | --- | --- |
| **Orchestrator-worker** | Lead decomposes; workers parallel; no worker↔worker chat; results return to lead | Anthropic Research; OpenAI **agents-as-tools**; ADK `AgentTool` / `ParallelAgent`; LangGraph supervisor `parallel_tool_calls=True` |
| **Pipeline / DAG** | Fixed stage order; stage contracts | ADK `SequentialAgent`; Stripe-style agent DAG; CrewAI `Process.sequential` |
| **Hierarchical** | Tree of supervisors → workers; summaries climb | Nested LangGraph supervisors; ADK multi-level `sub_agents`; watsonx Orchestrate (**80+** domain agents) |
| **Swarm / peer handoff** | Agents hand off control; last-active resumes | LangGraph Swarm (`active_agent` + checkpointer); OpenAI **handoffs** |
| **Mesh / blackboard** | Shared store, no direct peer messages | Redis/DB/vector blackboard [limited public primary-source depth] |

Sources: [Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py); [langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py); [OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration); [ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md).

### End-to-end request-flow — supervisor fan-out vs handoff

**Path A — Supervisor / orchestrator-worker (manager keeps ownership)**

1. **Ingress** — Request arrives with goal, tenant id, optional `thread_id`. CONTROL PLANE loads checkpoint (if any) and remaining session budget ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Cookbook — coordinator](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).
2. **Lead plans** — Frontier lead (e.g. Opus-class) decomposes work; persists plan to Memory when context may exceed **200k** tokens ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
3. **Fan-out** — CONTROL PLANE spawns workers under effort rules: simple → **1** agent; comparisons → **2–4**; complex research → **>10** with clear division. Prefer **3–5** parallel workers (not serial) and **3+** parallel tools per worker to cut wall-clock by up to **90%** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). LangGraph: `parallel_tool_calls=True` for multi-agent handoff at once ([create_supervisor](https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor)).
4. **Workers execute (DATA PLANE)** — Isolated contexts; TOOL PROXIES enforce specialist RBAC (search vs sandbox vs CRM). Large outputs land in artifact store; workers return lightweight refs (avoid “telephone”) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
5. **Sync wait** — Today’s Anthropic Research lead waits for the cohort (simplifies coordination; blocks mid-flight steering). Async fan-out is flagged as future work ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
6. **Synthesize** — Lead merges; optional CitationAgent; `output_mode='last_message'` vs `'full_history'` trades tokens vs fidelity ([langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py)).
7. **Persist & egress** — Checkpoint state; TELEMETRY emits fan-out count, tokens, $, correlation id. Manager owns the final answer (OpenAI **agents-as-tools** / ADK `AgentTool`) ([OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration)).

**Path B — Handoff / swarm (specialist becomes active agent)**

1. **Ingress** — Same as Path A; swarm **requires a checkpointer** so `active_agent` survives the turn ([langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)).
2. **Active specialist** — Current agent runs; may call `transfer_to_<name>` / `create_handoff_tool` returning `Command(goto=..., update={...})` ([LangGraph multi-agent](https://github.com/langchain-ai/langgraphjs/blob/86389fa3/docs/docs/agents/multi-agent.md); [OpenAI handoffs](https://openai.github.io/openai-agents-python/handoffs/)).
3. **Ownership transfer** — CONTROL PLANE sets `active_agent`; next user turn resumes that specialist. Default shared `messages` merges all transcripts (privacy/token cost) ([langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)).
4. **Guardrail asymmetry** — OpenAI: input guardrails apply only to the **first** agent; output guardrails only to the agent that produces **final** output; use tool guardrails per tool ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)). Nested history compaction does **not** redact sensitive tool args/results ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)).
5. **Terminate** — Final specialist answers user, or `max_turns` / `recursion_limit` fires. Unlike Path A, the lead does **not** necessarily synthesize.

Message style: Path A = **central fan-out then fan-in**; Path B = **serial ownership hops**. Neither is A2A cross-vendor messaging.

---

## Part 2 — Core Mechanics & Algorithms

### When multi-agent is justified

| Signal | Prefer |
| --- | --- |
| Context overflow / breadth-first parallelizable research | Orchestrator-worker (Anthropic Research) |
| Fixed regulated stages | Pipeline / Sequential DAG |
| Large domain catalog (**80+** agents) | Hierarchical supervisors |
| Conversational ownership transfer (support triage) | Handoff / swarm |
| Shared-context coding / most CRUD | **Stay single-agent** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration)) |

### Ownership fork (OpenAI)

| Pattern | Control ownership | Mechanism |
| --- | --- | --- |
| **Handoffs** | Specialist becomes active for rest of turn | `transfer_to_<agent_name>`; optional `input_filter`, `input_type`, `is_enabled`, `on_handoff` |
| **Agents as tools** | Manager keeps final-answer ownership | `Agent.as_tool()` — bounded specialist calls |

([OpenAI orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration); [multi_agent](https://openai.github.io/openai-agents-python/multi_agent/)).

### Supervisor vs swarm state machines

**Supervisor fan-out (sync cohort)**

```
                    ┌────────────┐
                    │   INGRESS  │
                    └──────┬─────┘
                           ▼
                    ┌────────────┐     persist plan
                    │ LEAD PLAN  │──────────────────► Memory / PERSISTENCE
                    └──────┬─────┘
                           │ fan-out N ∈ {1, 2–4, >10}
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      ┌─────────┐    ┌─────────┐    ┌─────────┐
      │ Worker1 │    │ Worker2 │ …  │ WorkerN │  (no peer chat)
      └────┬────┘    └────┬────┘    └────┬────┘
           │               │               │
           └───────────────┼───────────────┘
                           ▼
                    ┌────────────┐
                    │ SYNTHESIZE │──► Citation? ──► EGRESS
                    └────────────┘
         wall-clock ≈ T_plan + max(T_worker_i) + T_synth  (parallel sync)
```

**Swarm / handoff**

```
  ┌─────────┐  handoff tool   ┌─────────────┐
  │ Agent A │────────────────►│ active_agent│──ckpt──► PERSISTENCE
  └─────────┘                 │   = B       │
                              └──────┬──────┘
                                     ▼
                              ┌─────────────┐
                              │   Agent B   │──final──► EGRESS
                              └─────────────┘
         next user turn resumes active_agent (requires checkpointer)
```

### Loop / recursion guards

| System | Guard | Default / example |
| --- | --- | --- |
| LangGraph | `recursion_limit` → `GraphRecursionError` | Default **1000** super-steps since v1.0.6 |
| OpenAI Agents SDK | `max_turns` → `MaxTurnsExceeded` | `DEFAULT_MAX_TURNS = **10**`; `None` disables |
| ADK `LoopAgent` | `max_iterations` and/or `escalate` | Example **10** |
| Anthropic Research | Prompt effort budgets | Cap simple at 1 agent / 3–10 tools; early bug: **50** subagents on simple queries |

### Complexity & invariants

| Property | Statement |
| --- | --- |
| Cost multiplicity | Multi-agent ≈ **~15×** chat tokens; single agent ≈ **~4×** chat ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Quality vs tokens | BrowseComp: token usage alone ≈ **80%** of performance variance; tokens + tools + model ≈ **95%** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Model efficiency | Sonnet **3.7 → 4** beat doubling token budget on 3.7 ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Eval lift | Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by **90.2%** on internal research eval ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Wall-clock | Parallel subagents + parallel tools → up to **90%** research-time cut ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Capacity (serial supervisor) | If each orchestrator↔worker call ≈ **3s** and **20** workers wait, ceiling ≈ **~7 tasks/s** through the center ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |
| Pipeline latency | Sum of stages: 5 × 2s → **10s** before output ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) |
| Invariant | Workers do not peer-chat under orchestrator-worker; swarm must checkpoint `active_agent` |

**Capacity formula [inferred]** for orchestrator-worker research:

\[
\text{cost} \approx C_{\text{lead}}(\text{plan}+\text{synth}) + \sum_i C_{\text{worker}_i}(\text{context}_i + \text{tools}_i)
\]

\[
\text{wall-clock} \approx T_{\text{plan}} + \max_i(T_{\text{worker}_i}) + T_{\text{synth}}
\quad\text{(parallel sync cohort; × iterations if lead re-spawns)}
\]

---

## Part 3 — Token Economics & NFR Analysis

### Cost formula: `$ per 1k runs`

**Stated assumptions (labeled)**

| Symbol | Value | Meaning |
| --- | --- | --- |
| Baseline model | Claude Sonnet-class | List rates used for arithmetic only |
| \(P_{\text{in}}\) | **$3 / MTok** | Assumed input price (*assumption*, not a live quote) |
| \(P_{\text{out}}\) | **$15 / MTok** | Assumed output price (*assumption*) |
| \(T_{\text{chat}}\) | 4,000 in + 500 out | Single-turn chat reference shape (*assumption*) |
| \(M_{\text{agent}}\) | **~4×** chat tokens | Anthropic Research: single agent vs chat ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| \(M_{\text{multi}}\) | **~15×** chat tokens | Anthropic Research: multi-agent vs chat |
| Coordinator mix | Frontier lead + cheap workers | Cookbook: **84–98%** of team **input** tokens at worker rate ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) |

Chat baseline cost per run:

\[
C_{\text{chat}} = \frac{4000}{10^6}\cdot \$3 + \frac{500}{10^6}\cdot \$15
= \$0.012 + \$0.0075 = \mathbf{\$0.0195}
\]

**Single-agent path** — apply **~4×**:

\[
C_{\text{agent}} = 4 \times C_{\text{chat}} = \$0.078
\quad\Rightarrow\quad
C_{\text{1k, agent}} = 1000 \times \$0.078 = \mathbf{\$78\ /\ 1k\ runs}
\]

**Multi-agent path** — apply **~15×**:

\[
C_{\text{multi}} = 15 \times C_{\text{chat}} = \$0.2925
\quad\Rightarrow\quad
C_{\text{1k, multi}} = 1000 \times \$0.2925 = \mathbf{\$292.50\ /\ 1k\ runs}
\]

**Delta (multi vs single agent)** under these assumptions:

\[
C_{\text{1k, multi}} - C_{\text{1k, agent}} = \$292.50 - \$78 = \mathbf{\$214.50\ /\ 1k\ runs}
\]

> No stable published “multi-agent unit price.” Cost is run-dependent (subagent count × per-agent tokens × model mix). Use live provider metering (`usage.list_cost`) rather than a fixed catalog SKU ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

**Token-saving controls**: LangGraph `output_mode='last_message'`, `create_forward_message_tool` (avoid re-feeding full worker histories); artifact refs instead of inlining large worker text; cheap workers for read-heavy legs ([langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

### Latency SLA targets

> ⚠️ **Gap**: Research lacks vendor-published **p50/p95/p99** milliseconds for multi-agent framework topologies as production SLAs. Published shapes are qualitative (slowest worker + synth; sum of pipeline stages; serial handoff hops) plus case studies (Stripe AHT **−26%**; Spotify ad planning **15 min → 5 s**) ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Do not treat the table below as vendor SLAs.

**[inferred] latency budget** — arithmetic for a research-class orchestrator-worker run (engineering targets, not measured):

| Component | Simple (N=1) | Comparison (N=3 parallel) | Complex (N=10, 2 sync waves of 5) |
| --- | --- | --- | --- |
| Lead plan | 800 ms | 1,200 ms | 1,800 ms |
| Workers (parallel sync) | \(1\times 2{,}500\) = 2,500 ms | \(\max=4{,}000\) ms | \(2\times 5{,}000\) = 10,000 ms |
| Tools inside workers | folded into worker | folded | folded |
| Lead synthesize + cite | 600 ms | 1,200 ms | 2,000 ms |
| Checkpoint / persist | 50 ms | 80 ms | 120 ms |
| **E2E point estimate** | ≈ **3,950 ms** | ≈ **6,480 ms** | ≈ **13,920 ms** |

Pipeline contrast (published shape): 5 stages × 2 s = **10,000 ms** additive ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

| Tier | **[inferred] target** | Covers | Mitigations |
| --- | --- | --- | --- |
| **p50** | **≤ 5,000 ms** | Simple N=1 or warm comparison with fast tools | Cap fan-out at **1** for fact-finding; parallel tools (**3+**) inside the worker; artifact refs ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **p95** | **≤ 15,000 ms** | Typical 2–4 worker comparison / shallow research | Cohort size **3–5**; `output_mode='last_message'`; per-worker timeout + overall deadline **[inferred]**; session budget as fan-out guardrail ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) |
| **p99** | **≤ 45,000 ms** | Complex >10 workers, slow tools, re-spawn iteration | Effort budgets (prevent 50-subagent runaway); circuit breaker → single-agent → deterministic; rainbow deploys so mid-flight agents survive updates ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |

### Throughput & back-pressure

> ⚠️ Limited public data for prompt-cache hit rates, TTLs, or multi-tenant RPM/TPM of agent clusters. Apply provider prompt caching and org RPM/TPM outside the orchestration layer ([research §2](../research/09-multi-agent-architecture.md)).

| Lever | Behavior |
| --- | --- |
| **Fan-out caps** | Query class → **1** / **2–4** / **>10** subagents; tool-call budgets **3–10** / **10–15** per agent ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Session budget** | Hard $ / token ceiling as fan-out guardrail ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) |
| **Parallel vs serial supervisor** | Serial handoffs bottleneck (~**7 tasks/s** example with 3 s calls × 20 waiting workers); prefer parallel tool calls when model supports ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system); [langgraph-supervisor](https://github.com/langchain-ai/langgraph-supervisor-py)) |
| **Local tool concurrency** | OpenAI `max_function_tool_concurrency` caps parallel local tools; default starts all (`None`) ([Running agents](https://openai.github.io/openai-agents-python/running_agents/)) |
| **Token-saving** | `last_message` / forward_message; do not re-feed full worker histories |
| **Shed load** | Under TPM pressure: cut N toward **1**, fall back multi→single→deterministic, queue non-interactive research **[inferred]** |
| **Breakers** | Open multi-agent path → single agent → deterministic (Part 4) |

### NFR trade-offs

| NFR | Target / posture | Multi-agent implication |
| --- | --- | --- |
| **Availability** | Design **99.9%** on ingress + supervisor; worker paths may degrade | Breaker open ≠ total outage if single-agent / deterministic fallback remains healthy **[inferred]** |
| **RPO** | Checkpoint every super-step (`sync`) → RPO ≈ last completed step; swarm must persist `active_agent` | Lose in-flight worker without pending-writes; plan lost past **200k** unless Memory ([LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)) |
| **RTO** | Resume `thread_id` + checkpoint; pending writes skip successful siblings | Resume-from-failure preferred over full restart ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **Compliance** | Immutable delegation logs; PII redact before persist; tool RBAC per specialist | Supervisor topologies = single policy chokepoint; swarm shared `messages` risks history leakage ([langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py); research §4) |

**Explicit trade-off — parallel quality vs token burn**: Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by **90.2%** on Anthropic’s research eval, and parallelism can cut wall-clock by up to **90%**, but multi-agent burns **~15×** chat tokens vs **~4×** for a typical single agent — roughly **\$292.50 vs \$78 per 1k runs** under the labeled price assumptions above. Price the tax against task value; use cheap workers for read-heavy legs ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution & checkpointer (`active_agent`)

| System | What is checkpointed | Notes |
| --- | --- | --- |
| **LangGraph** | State snapshot each **super-step**; per-node pending writes | Modes `"exit"` (weaker mid-crash) vs `"sync"` (stronger); Postgres; DynamoDB + S3 for payloads ≥ **350 KB** ([checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [AWS blog](https://aws.amazon.com/blogs/database/build-durable-ai-agents-with-langgraph-and-amazon-dynamodb/)) |
| **LangGraph Swarm** | Must include **`active_agent`** (+ history) | Without checkpointer, swarm forgets which agent was active ([langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)) |
| **Anthropic Research** | Plan in Memory; resume-from-failure; rainbow deploys | Summarize completed phases; spawn fresh subagents with clean context ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| **ADK** | `SessionService` via events/`state_delta`; `temp:` invocation-scoped | `ParallelAgent` children share `session.state` — distinct keys to avoid races ([ADK State](https://adk.dev/sessions/state/); [ADK multi-agents](https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md)) |
| **OpenAI Agents SDK** | `RunState` pause/resume; sessions / `conversation_id` | Handoff `input_filter` unsupported on server-managed conversations ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)) |

> ⚠️ **Gap**: Primary multi-agent framework docs do not publish circuit-breaker thresholds, half-open probe strategies, or Temporal/Kafka topologies specific to agent fan-out. **[inferred]** Enterprise deployments wrap the agent graph in an external workflow engine (Temporal/Step Functions) for saga-style compensation — outside these SDKs’ documented multi-agent APIs.

### Failure taxonomy

| Class | Examples (documented) | Detection / response |
| --- | --- | --- |
| **Transient** | Tool/API 429/5xx; slow worker stalls cohort | Retry + jitter; per-worker timeout; model adapts on tool fail ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)); breaker counts failures **[inferred]** |
| **Permanent** | Schema-invalid handoff/`input_type`; auth deny; unknown agent name | Fail-closed; ADK `before_tool`; OpenAI `input_type` ([ADK Safety](https://adk.dev/safety/); [Handoffs](https://openai.github.io/openai-agents-python/handoffs/)) |
| **Poison-pill / runaway** | **50** subagents on simple queries; endless search; peer handoff loops | Effort budgets; `max_turns=10`; `recursion_limit`; `LoopAgent.max_iterations` ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [OpenAI](https://openai.github.io/openai-agents-python/running_agents/); [LangGraph](https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit)) |
| **Topology-specific** | Duplicated worker work; telephone paraphrase; context truncation at **200k**; ADK parallel state races; deploy skew; SEO source bias; hallucinated handoff args | Rich task briefs; artifact refs; Memory; distinct state keys; rainbow deploys; source heuristics; schema validation ([research §5](../research/09-multi-agent-architecture.md)) |

Idempotency **[inferred]**: key side effects by `(thread_id, worker_id, tool_name, args_hash)` so checkpoint replay does not double-book.

Compound-error property: a minor step failure can divert the entire trajectory — prototype→production gap is larger than for ordinary software ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Circuit breaker: closed → open → half-open

```
          success                    recovery_timeout
     ┌──────────────┐  fail≥N   ┌──────┐  elapsed   ┌───────────┐
     │    CLOSED    │──────────►│ OPEN │───────────►│ HALF_OPEN │
     └──────▲───────┘           └──────┘            └─────┬─────┘
            │ success                                      │
            └──────────────────────────────────────────────┘
                         fail → OPEN
```

1. **CLOSED** — Multi-agent path (supervisor fan-out or swarm) accepts traffic; failures in sliding window counted.  
2. **OPEN** — After ≥ \(N\) failures (or half-open probe fail), reject multi-agent path; start recovery timer; route to fallback.  
3. **HALF_OPEN** — Allow one probe through multi-agent; success → CLOSED; fail → OPEN.

No peer-reviewed multi-agent-specific breaker curves in consulted sources — tune \(N\)/timeout from your SLOs **[inferred]**.

### Fallback chains (multi-agent → single agent → deterministic)

```
  Multi-agent (supervisor / swarm)  ──fail/breaker/budget──►  Single agent
                 │                                                  │
                 │                                                  ▼
                 └──────────────────►  Deterministic handler (rules, FAQ, human queue)
```

| Stage | Behavior |
| --- | --- |
| **Multi-agent** | Fan-out under caps; role-scoped tools; full synthesis / handoff chain |
| **Single agent** | One context, one tool allowlist (~**4×** chat tokens); no peer handoffs |
| **Deterministic** | No LLM; template/rules/cached answer or human queue; always terminates |

### Enterprise security

> ⚠️ Limited public data for Zero-Trust MCP mutual auth, formal PII NER schemas, or immutable audit-log schemas **specific to agent-to-agent hops**. Treat MCP auth and PII redaction as **cross-cutting platform** concerns; for multi-agent, enforce **least-privilege tool allowlists per agent** and audit **which agent** invoked **which tool** ([research §4](../research/09-multi-agent-architecture.md)). Rows marked **[inferred]** below fill that thin area for interview-grade posture — not vendor SLAs.

**Zero-Trust MCP across hops** **[inferred]**: authenticate every tool call (mTLS/OAuth); deny-by-default MCP registry; per-request scoped tokens; no ambient credentials in prompts; re-auth on handoff — specialist B does not inherit A’s token ambiently. MCP remains agent↔tool; peer agent networking is **A2A** (later topic).

**Tool RBAC per specialist**: research agent → web search; code agent → sandbox (no network); customer agent → user data but not prod DB credentials ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Coordinator with **no tools** + tool-scoped workers hardens blast radius of untrusted web content ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)). ADK: validate via deterministic `ToolContext` + `before_tool` — do not trust model-supplied args alone ([ADK Safety](https://adk.dev/safety/)).

**PII pipeline**: **detect → redact → audit** before persisting shared `messages`, checkpoints, and artifact metadata. OpenAI nested handoff history **does not redact** tool args/results — use explicit `input_filter` / sanitizers ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)). Swarm default shared transcript exposes all agents’ internals unless custom schemas ([langgraph-swarm](https://github.com/langchain-ai/langgraph-swarm-py)).

**Immutable delegation logs** **[inferred]**: append-only records of `(correlation_id, from_agent, to_agent, reason, tool_grants, fan_out_n, breaker_state, disposition)` — chain-of-custody for who delegated what. Prefer tracing **decision patterns / interaction structures** without monitoring conversation contents ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Supervisor topologies are easier to govern (single policy chokepoint) than mesh/swarm peer handoffs **[inferred]**.

Risks: prompt injection, **context contamination** across agents, **privilege creep** when agents accumulate tools ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)).

---

## Part 5 — Production Enterprise Code

Runnable Python: **supervisor with worker cap**, retries + full jitter, circuit breaker (closed → open → half-open), fallback (multi-agent → single agent → deterministic), correlation IDs, structured logging. **Deterministic fake models** — no API keys, no network, no stub placeholders.

```python
#!/usr/bin/env python3
"""Multi-agent supervisor with enterprise resilience primitives."""

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
            "agent": getattr(record, "agent", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "degraded": getattr(record, "degraded", None),
            "fan_out": getattr(record, "fan_out", None),
            "path": getattr(record, "path", None),
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


LOG = build_logger("multiagent.supervisor")


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
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05  # short for demo; 30–60s in prod
    window_s: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def allow(self) -> bool:
        now = time.time()
        if self.state == BreakerState.OPEN:
            if self.opened_at is not None and now - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.time()
        self.failures = [t for t in self.failures if now - t <= self.window_s]
        self.failures.append(now)
        if self.state == BreakerState.HALF_OPEN or len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries with exponential backoff + full jitter
# ---------------------------------------------------------------------------

def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    max_attempts: int,
    base_s: float,
    max_s: float,
    correlation_id: str,
    agent: str,
    should_retry: Callable[[Exception], bool],
) -> Any:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            last = exc
            LOG.warning(
                "attempt_failed",
                extra={
                    "correlation_id": correlation_id,
                    "agent": agent,
                    "attempt": attempt,
                },
            )
            if not should_retry(exc) or attempt == max_attempts:
                break
            sleep_s = min(max_s, base_s * (2 ** (attempt - 1)))
            time.sleep(random.uniform(0, sleep_s))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# PII: detect → redact → audit
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass
class AuditLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def append(self, entry: dict[str, Any]) -> None:
        self.entries.append({"ts": time.time(), **entry})


def redact_pii(text: str, audit: AuditLog, correlation_id: str) -> str:
    hits = EMAIL_RE.findall(text)
    if hits:
        audit.append(
            {
                "event": "pii_redacted",
                "correlation_id": correlation_id,
                "count": len(hits),
                "kinds": ["email"],
            }
        )
    return EMAIL_RE.sub("[REDACTED_EMAIL]", text)


# ---------------------------------------------------------------------------
# Checkpointer (active_agent + plan + worker results)
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    thread_id: str
    active_agent: str
    plan: list[str]
    worker_results: dict[str, str]
    disposition: str | None = None


class MemoryCheckpointer:
    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}

    def save(self, ckpt: Checkpoint) -> None:
        self._store[ckpt.thread_id] = ckpt

    def load(self, thread_id: str) -> Checkpoint | None:
        return self._store.get(thread_id)


# ---------------------------------------------------------------------------
# Deterministic fake models (no API keys)
# ---------------------------------------------------------------------------

class FakeLeadModel:
    """Decomposes a query into worker briefs. Inject failures via fail_times."""

    def __init__(self, fail_times: int = 0) -> None:
        self._remaining_fails = fail_times

    def plan(self, query: str, max_workers: int) -> list[str]:
        if self._remaining_fails > 0:
            self._remaining_fails -= 1
            raise AgentError("lead transient outage", FailureKind.TRANSIENT)
        q = query.lower()
        if "compare" in q or " vs " in q:
            briefs = [
                f"research angle A for: {query}",
                f"research angle B for: {query}",
                f"research angle C for: {query}",
            ]
        elif "complex" in q or "diligence" in q:
            briefs = [f"slice {i} of: {query}" for i in range(1, 8)]
        else:
            briefs = [f"fact-find: {query}"]
        return briefs[:max_workers]

    def synthesize(self, query: str, results: dict[str, str]) -> str:
        parts = [results[k] for k in sorted(results)]
        return f"SYNTH[{query}]: " + " | ".join(parts)


class FakeWorkerModel:
    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self._remaining_fails = fail_times

    def run(self, brief: str) -> str:
        if self._remaining_fails > 0:
            self._remaining_fails -= 1
            raise AgentError(f"{self.name} tool timeout", FailureKind.TRANSIENT)
        digest = hashlib.sha256(brief.encode()).hexdigest()[:8]
        return f"{self.name}:{digest}:{brief[:48]}"


class FakeSingleAgent:
    def run(self, query: str) -> str:
        digest = hashlib.sha256(query.encode()).hexdigest()[:8]
        return f"SINGLE[{digest}]: {query}"


def deterministic_fallback(query: str) -> str:
    return f"DETERMINISTIC: queued for human review — {query[:80]}"


# ---------------------------------------------------------------------------
# Fan-out policy (Anthropic bands: 1 / 2–4 / >10 → capped)
# ---------------------------------------------------------------------------

def fan_out_cap(query: str, hard_cap: int) -> int:
    q = query.lower()
    if "complex" in q or "diligence" in q:
        n = min(hard_cap, 10)
    elif "compare" in q or " vs " in q:
        n = min(hard_cap, 4)
    else:
        n = 1
    return max(1, n)


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    answer: str
    path: str
    fan_out: int
    correlation_id: str
    degraded: bool
    checkpoint: Checkpoint


class Supervisor:
    def __init__(
        self,
        *,
        lead: FakeLeadModel,
        workers: list[FakeWorkerModel],
        single: FakeSingleAgent,
        checkpointer: MemoryCheckpointer,
        breaker: CircuitBreaker,
        audit: AuditLog,
        hard_worker_cap: int = 4,
        max_attempts: int = 3,
    ) -> None:
        if hard_worker_cap < 1:
            raise ValueError("hard_worker_cap must be >= 1")
        self.lead = lead
        self.workers = workers
        self.single = single
        self.checkpointer = checkpointer
        self.breaker = breaker
        self.audit = audit
        self.hard_worker_cap = hard_worker_cap
        self.max_attempts = max_attempts

    def _should_retry(self, exc: Exception) -> bool:
        return isinstance(exc, AgentError) and exc.kind == FailureKind.TRANSIENT

    def _run_worker(self, worker: FakeWorkerModel, brief: str, correlation_id: str) -> tuple[str, str]:
        def call() -> str:
            return worker.run(brief)

        result = retry_with_jitter(
            call,
            max_attempts=self.max_attempts,
            base_s=0.001,
            max_s=0.01,
            correlation_id=correlation_id,
            agent=worker.name,
            should_retry=self._should_retry,
        )
        return worker.name, redact_pii(result, self.audit, correlation_id)

    def _multi_agent(self, query: str, thread_id: str, correlation_id: str) -> RunResult:
        n = fan_out_cap(query, self.hard_worker_cap)
        if n > len(self.workers):
            n = len(self.workers)

        def plan_call() -> list[str]:
            return self.lead.plan(query, n)

        briefs = retry_with_jitter(
            plan_call,
            max_attempts=self.max_attempts,
            base_s=0.001,
            max_s=0.01,
            correlation_id=correlation_id,
            agent="lead",
            should_retry=self._should_retry,
        )
        briefs = briefs[:n]
        n = len(briefs)
        if n < 1:
            raise AgentError("lead produced empty plan", FailureKind.PERMANENT)

        self.audit.append(
            {
                "event": "delegation",
                "correlation_id": correlation_id,
                "from_agent": "lead",
                "to_agents": [w.name for w in self.workers[:n]],
                "fan_out": n,
                "briefs": briefs,
            }
        )

        ckpt = Checkpoint(
            thread_id=thread_id,
            active_agent="lead",
            plan=briefs,
            worker_results={},
        )
        self.checkpointer.save(ckpt)

        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {
                pool.submit(self._run_worker, self.workers[i], briefs[i], correlation_id): i
                for i in range(n)
            }
            for fut in as_completed(futures):
                name, text = fut.result()
                results[name] = text
                ckpt.worker_results[name] = text
                self.checkpointer.save(ckpt)

        answer = self.lead.synthesize(query, results)
        answer = redact_pii(answer, self.audit, correlation_id)
        ckpt.active_agent = "lead"
        ckpt.disposition = "multi_agent_ok"
        self.checkpointer.save(ckpt)

        LOG.info(
            "multi_agent_complete",
            extra={
                "correlation_id": correlation_id,
                "agent": "lead",
                "fan_out": n,
                "path": "multi_agent",
                "breaker_state": self.breaker.state.value,
                "degraded": False,
            },
        )
        return RunResult(
            answer=answer,
            path="multi_agent",
            fan_out=n,
            correlation_id=correlation_id,
            degraded=False,
            checkpoint=ckpt,
        )

    def _single_agent(self, query: str, thread_id: str, correlation_id: str) -> RunResult:
        answer = redact_pii(self.single.run(query), self.audit, correlation_id)
        ckpt = Checkpoint(
            thread_id=thread_id,
            active_agent="single",
            plan=[query],
            worker_results={"single": answer},
            disposition="single_agent_ok",
        )
        self.checkpointer.save(ckpt)
        self.audit.append(
            {
                "event": "fallback",
                "correlation_id": correlation_id,
                "to_path": "single_agent",
            }
        )
        return RunResult(
            answer=answer,
            path="single_agent",
            fan_out=1,
            correlation_id=correlation_id,
            degraded=True,
            checkpoint=ckpt,
        )

    def run(self, query: str, thread_id: str | None = None) -> RunResult:
        correlation_id = str(uuid.uuid4())
        thread_id = thread_id or str(uuid.uuid4())
        query = redact_pii(query, self.audit, correlation_id)

        if not self.breaker.allow():
            LOG.warning(
                "breaker_open_skip_multi",
                extra={
                    "correlation_id": correlation_id,
                    "breaker_state": self.breaker.state.value,
                    "degraded": True,
                    "path": "fallback",
                },
            )
            try:
                return self._single_agent(query, thread_id, correlation_id)
            except Exception:  # noqa: BLE001
                answer = deterministic_fallback(query)
                ckpt = Checkpoint(
                    thread_id=thread_id,
                    active_agent="deterministic",
                    plan=[],
                    worker_results={},
                    disposition="deterministic",
                )
                self.checkpointer.save(ckpt)
                return RunResult(
                    answer=answer,
                    path="deterministic",
                    fan_out=0,
                    correlation_id=correlation_id,
                    degraded=True,
                    checkpoint=ckpt,
                )

        try:
            result = self._multi_agent(query, thread_id, correlation_id)
            self.breaker.record_success()
            return result
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure()
            LOG.error(
                "multi_agent_failed",
                extra={
                    "correlation_id": correlation_id,
                    "breaker_state": self.breaker.state.value,
                    "degraded": True,
                    "path": "multi_agent",
                    "agent": "lead",
                },
            )
            self.audit.append(
                {
                    "event": "breaker_transition",
                    "correlation_id": correlation_id,
                    "breaker_state": self.breaker.state.value,
                    "error": str(exc),
                }
            )
            try:
                return self._single_agent(query, thread_id, correlation_id)
            except Exception:  # noqa: BLE001
                answer = deterministic_fallback(query)
                ckpt = Checkpoint(
                    thread_id=thread_id,
                    active_agent="deterministic",
                    plan=[],
                    worker_results={},
                    disposition="deterministic",
                )
                self.checkpointer.save(ckpt)
                self.audit.append(
                    {
                        "event": "fallback",
                        "correlation_id": correlation_id,
                        "to_path": "deterministic",
                    }
                )
                return RunResult(
                    answer=answer,
                    path="deterministic",
                    fan_out=0,
                    correlation_id=correlation_id,
                    degraded=True,
                    checkpoint=ckpt,
                )


# ---------------------------------------------------------------------------
# Demo (deterministic)
# ---------------------------------------------------------------------------

def main() -> None:
    random.seed(0)
    audit = AuditLog()
    ckpt = MemoryCheckpointer()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_s=0.05)

    # Workers never fail; lead fails once then recovers — exercises retry.
    supervisor = Supervisor(
        lead=FakeLeadModel(fail_times=1),
        workers=[FakeWorkerModel(f"w{i}") for i in range(4)],
        single=FakeSingleAgent(),
        checkpointer=ckpt,
        breaker=breaker,
        audit=audit,
        hard_worker_cap=4,
    )

    r1 = supervisor.run("compare alpha vs beta for contact@example.com", thread_id="t-1")
    assert r1.path == "multi_agent"
    assert r1.fan_out == 3
    assert "[REDACTED_EMAIL]" in r1.answer
    assert ckpt.load("t-1") is not None

    # Force multi-agent path to fail hard → single-agent fallback.
    supervisor.lead = FakeLeadModel(fail_times=99)
    r2 = supervisor.run("simple fact", thread_id="t-2")
    assert r2.path == "single_agent"
    assert r2.degraded is True
    assert breaker.state == BreakerState.OPEN

    # Breaker open → skip multi, go single (or deterministic).
    r3 = supervisor.run("another fact", thread_id="t-3")
    assert r3.path in {"single_agent", "deterministic"}
    assert r3.degraded is True

    # Wait out recovery; half-open probe still fails (lead broken) → stays degraded.
    time.sleep(0.06)
    r4 = supervisor.run("still broken", thread_id="t-4")
    assert r4.degraded is True

    # Heal lead; after success breaker closes.
    supervisor.lead = FakeLeadModel(fail_times=0)
    # Drain open → half-open via timeout
    time.sleep(0.06)
    r5 = supervisor.run("compare x vs y", thread_id="t-5")
    assert r5.path == "multi_agent"
    assert breaker.state == BreakerState.CLOSED

    print(
        json.dumps(
            {
                "ok": True,
                "paths": [r1.path, r2.path, r3.path, r4.path, r5.path],
                "fan_out_r1": r1.fan_out,
                "audit_events": [e["event"] for e in audit.entries],
                "active_agent_t1": ckpt.load("t-1").active_agent if ckpt.load("t-1") else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
```

Save as `supervisor_resilience.py` and run: `python3 supervisor_resilience.py`. Expected: JSON with `"ok": true`, PII redaction on the first run, fallback paths under breaker pressure, and `active_agent` restored from the in-memory checkpointer.

---

## Part 6 — Architectural System Design Scenarios

### Scenario 1 — Enterprise research / diligence desk

**Problem statement**: Design a multi-tenant diligence assistant for an investment firm. Analysts run breadth-first queries across filings, news, and internal notes. Peak **200 interactive research jobs/hour**. Quality must beat a solo frontier agent on hard multi-source questions; compliance requires knowing **which specialist** touched **which source**. Budget pressure: cannot pay frontier rates for every page fetched. Sub-minute interactive p95 preferred; overnight batch may run longer.

**Proposed architecture (recommended: orchestrator-worker + cheap workers)**

```
                    ┌─────────────────────────────────────────┐
                    │           CONTROL PLANE                 │
                    │  LeadResearcher (frontier, no tools)    │
                    │  fan-out policy 1 / 2–4 / ≤5 cohort     │
                    │  session $ budget · breaker · tenant QoS│
                    └───────────────┬─────────────────────────┘
                                    │ plan + spawn
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
         ┌─────────┐          ┌─────────┐          ┌─────────┐
         │ Worker  │          │ Worker  │          │ Worker  │
         │ search  │          │ fetch   │          │ notes   │
         └────┬────┘          └────┬────┘          └────┬────┘
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────────┐
                    │ DATA: synthesize + CitationAgent        │
                    └───────┬─────────────┬───────────┬───────┘
                            │             │           │
                   ┌────────┴───┐  ┌──────┴────┐  ┌───┴────────┐
                   │ TOOL PROXY │  │PERSISTENCE│  │ TELEMETRY  │
                   │ MCP search │  │ Memory    │  │ structure  │
                   │ RBAC/scope │  │ artifacts │  │ tokens /$  │
                   └────────────┘  └───────────┘  └────────────┘
```

**Trade-off matrix**

| Dimension | A: Stay single-agent frontier | B: Orchestrator-worker (recommended) | C: Mesh / blackboard peers |
| --- | --- | --- | --- |
| **Cost** | ~**4×** chat; all tokens at frontier | ~**15×** chat tokens but **84–98%** input at worker rate ([Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)) | High + unpredictable peer chatter |
| **Latency** | Serial; no 90% parallel cut | ≈ slowest worker + plan/synth; up to **90%** faster vs serial research ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) | Contended blackboard; hard to SLA |
| **Ops** | Lowest | High (prompts, tracing, rainbow deploys) | Highest (emergent coordination) |
| **Security** | One allowlist — large blast radius | Lead tool-less; per-worker RBAC; chokepoint audit | Privilege creep; weak chokepoint |
| **Scalability** | Context overflow on broad diligence | Horizontal workers; cap cohort **3–5** | Coordination does not scale cleanly |

**Decision rationale**: Breadth-first diligence matches Anthropic’s multi-agent sweet spot; **90.2%** eval lift vs solo Opus justifies ~**\$292.50 / 1k** vs ~**\$78 / 1k** under labeled assumptions when deal value dwarfs token tax. Coordinator-without-tools + scoped workers contain injection from the open web. Mesh is rejected: governance and latency SLOs suffer. Single-agent remains the fallback when breaker opens or query class is simple (fan-out **1**).

---

### Scenario 2 — Regulated KYC / case-review pipeline

**Problem statement**: Design an agent system for KYC analysts reviewing onboarding cases. Stages are known (extract → screen → risk narrative → reviewer pack). Regulators demand stage contracts and replayable evidence. Stripe-style results cited: agent DAG cut average handling time **26%**; reviewers rated outputs **96%** helpful ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)). Interactive desk: p95 under **20 s** for a five-stage pack; no free-form agent peer chat that mixes PII across roles.

**Proposed architecture (recommended: Sequential / DAG pipeline)**

```
     ┌──────────────────────────────────────────────────────────┐
     │                    CONTROL PLANE                         │
     │  SequentialAgent / DAG rails · stage gates · max_iters   │
     │  no peer handoff · HITL before submit                    │
     └───────────┬──────────┬──────────┬──────────┬─────────────┘
                 ▼          ▼          ▼          ▼
            ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
            │Extract │►│Screen  │►│Narrative│►│Pack   │
            │ agent  │ │ agent  │ │ agent  │ │ agent │
            └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
                │          │          │          │
                └──────────┴─────┬────┴──────────┘
                                 ▼
              ┌──────────┐  ┌────────────┐  ┌────────────┐
              │TOOL PROXY│  │PERSISTENCE │  │ TELEMETRY  │
              │ KYC APIs │  │ stage ckpt │  │ AHT · gate │
              │ RBAC/PII │  │ audit pack │  │ pass rates │
              └──────────┘  └────────────┘  └────────────┘
```

**Trade-off matrix**

| Dimension | A: Handoff / swarm specialists | B: Sequential DAG pipeline (recommended) | C: Hierarchical supervisors (80+ agents) |
| --- | --- | --- | --- |
| **Cost** | Medium–high; full history by default | Moderate (stage sum; predictable) | High (summary tax each level) |
| **Latency** | Serial specialist hops; variable | Additive but bounded (5×2s → **10s** shape) ([Newsletter](https://newsletter.systemdesign.one/p/multi-agent-system)) | Deep trees add hops |
| **Ops** | Medium; needs `active_agent` checkpointer | Medium; excellent stage auditability | High catalog ops |
| **Security** | History leakage; guardrails only on first/final ([Handoffs](https://openai.github.io/openai-agents-python/handoffs/)) | Strong stage contracts; PII per stage | Policy layers exist but detail loss risk |
| **Scalability** | Poor for fixed KYC rails | Horizontal per-stage workers; clear back-pressure | Fits huge domain catalogs, not 5-stage KYC |

**Decision rationale**: KYC stages are known a priori — ADK `SequentialAgent` / Stripe-style DAG beats LLM-driven swarm ownership transfer. Auditability and PII stage isolation dominate over research-style parallel fan-out. Hierarchical **80+** agent catalogs are overkill. Keep a single-agent extract path as breaker fallback; do not introduce A2A cross-vendor hops in v1.

---

### Interview prompts (after both scenarios)

1. When would you refuse multi-agent and stay single-agent despite stakeholder pressure for “an agent army”?
2. Derive `$ / 1k runs` from chat baseline using **~4×** and **~15×**; what changes if **84–98%** of input tokens bill at the worker tier?
3. Why must a LangGraph Swarm compile with a checkpointer? What state key is load-bearing?
4. Sketch closed → open → half-open around a supervisor and name the fallback chain.
5. How do OpenAI handoff guardrails differ for input vs output agents, and what does that imply for PII?
6. Give fan-out caps for simple / comparison / complex queries and a runaway failure you are preventing.
7. Where does MCP stop and A2A begin in your topology diagram?
8. Pipeline latency is a sum; orchestrator-worker latency is roughly a max — when does each SLO win?

---

## Sources (from research)

- [1] https://newsletter.systemdesign.one/p/multi-agent-system
- [2] https://www.anthropic.com/engineering/multi-agent-research-system
- [3] https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small
- [4] https://github.com/langchain-ai/langgraph-supervisor-py
- [5] https://github.com/langchain-ai/langgraph-swarm-py
- [6] https://reference.langchain.com/python/langgraph-supervisor/supervisor/create_supervisor
- [7] https://docs.langchain.com/oss/python/langgraph/checkpointers
- [8] https://docs.langchain.com/oss/python/langgraph/graph-api#recursion-limit
- [9] https://openai.github.io/openai-agents-python/handoffs/
- [10] https://developers.openai.com/api/docs/guides/agents/orchestration
- [11] https://openai.github.io/openai-agents-python/running_agents/
- [12] https://github.com/google/adk-docs/blob/main/docs/agents/multi-agents.md
- [13] https://adk.dev/safety/
- [14] https://adk.dev/sessions/state/
- [15] https://aws.amazon.com/blogs/database/build-durable-ai-agents-with-langgraph-and-amazon-dynamodb/
