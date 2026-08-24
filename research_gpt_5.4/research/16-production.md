# Research: Production - Docker, Kubernetes, APIs, queues, scaling, reliability

**Date researched**: 2026-08-21
**Sources consulted**: 13

---

## 1. System Topology & Mechanics

`Production` appears in the local research corpus less as one deployment product choice and more as a layered separation between `workflow control`, `tool/API execution`, `state persistence`, and `remote interoperability`. The most consistent architectural split is `control plane` for routing, approvals, checkpoints, tracing, and retry policy, versus `data plane` for model inference, tool execution, containerized code runs, and external API I/O (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `14-observability.md`) [inferred].

For `APIs`, the local notes are unambiguous that typed function or MCP-backed tools are the preferred production surface when a stable interface exists. They keep actions structured, reduce ambiguity, and preserve a clean place to insert schema validation, approval checks, and audit capture (`03-tool-use.md`, `10-mcp-interoperability.md`, `13-security-guardrails.md`). The production hierarchy implied by the corpus is: `strict API/function tool` first, `MCP` when capability reuse and standardized auth/discovery matter, `browser/computer automation` only when no viable API exists (`03-tool-use.md`, `10-mcp-interoperability.md`, `11-specialized-agents.md`, `13-security-guardrails.md`) [inferred].

For `queues` and asynchronous boundaries, the local corpus is stronger on workflow and protocol semantics than on named brokers such as Kafka or SQS. Temporal is described through append-only event history plus `Signals`, `Queries`, and `Updates`, while A2A/MCP notes describe remote delegation as introducing transport queues, webhook delivery, coordinator state, and polling/streaming/push update modes (`04-agent-architecture.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`). That means the production queueing surface is best understood here as `workflow/event history + remote transport buffering`, not as one mandated message-bus product [inferred].

For `containers`, the local evidence is focused on execution isolation rather than cluster orchestration. The corpus documents server-side code execution in sandboxed containers, reusable hosted containers, isolated browsers or VMs for computer use, and dedicated minimal-privilege VMs/containers for browser-style automation (`03-tool-use.md`, `11-specialized-agents.md`, `13-security-guardrails.md`). It also notes self-hosted/open-weight serving control surfaces such as `vLLM` when operator control matters more than hosted simplicity (`01-llm-foundations.md`, `15-inference-optimization.md`).

Literal `Docker` and `Kubernetes` mechanics are only weakly represented in the local set. The strongest nearby signal is that ADK's deployment posture is framed around Google Cloud runtime options such as Cloud Run/GKE in the framework note, not around detailed cluster-operability guidance (`05-agent-frameworks.md`).  
> ⚠️ Limited public data available in the local research set for concrete `Docker image`, `Kubernetes controller`, `service mesh`, or `autoscaler` design patterns. The corpus is materially stronger on runtime boundaries, state handling, and tool/API production surfaces than on cluster-level implementation detail.

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable end-to-end `p50/p95/p99` latency of production agent systems in the local research set. The notes are much stronger on billing formulas, throughput ceilings, tool overhead, cache economics, and workflow-structure effects than on production percentile SLAs.

The cleanest production cost model synthesized from the local notes is:

```text
production_run_cost
  ~= model_input_cost
   + cached_read_cost
   + cache_write_cost
   + output_cost
   + tool_or_retrieval_fees
   + container_or_sandbox_fees
   + persistence / tracing overhead
```

(`03-tool-use.md`, `04-agent-architecture.md`, `12-evaluation.md`, `14-observability.md`) [inferred]

For `APIs` and `tool surfaces`, the local corpus repeatedly warns that schemas and tool definitions are themselves part of the token budget. Large tool catalogs, browser tool declarations, policy prefixes, and tracing metadata all increase input size and can lengthen the critical path (`03-tool-use.md`, `13-security-guardrails.md`, `14-observability.md`). The most expensive fixed overhead in the current corpus is browser-style automation: Anthropic browser-tool declarations add about `6,610-6,670` input tokens and computer-tool declarations about `4,520-4,590` before screenshots or task content (`03-tool-use.md`, `11-specialized-agents.md`).

For `containers`, the local notes provide one concrete hosted-runtime cost signal: OpenAI hosted shell/code-execution containers range from `$0.03` to `$1.92` per 20-minute session by memory tier, and fresh short-lived `1 GB` executions imply an approximate floor of `$7.50 / 1k` runs if every run starts a new billable session (`03-tool-use.md`, `11-specialized-agents.md`, `12-evaluation.md`). That makes `container reuse` a first-order production optimization, not just a convenience [inferred].

For `scaling`, the strongest economic lever remains reducing repeated high-end reasoning turns. Planner/executor and DAG-style decomposition can cut critical-path latency and overall cost when subtasks are independent; the local architecture note cites `LLMCompiler` at up to `3.7x` lower latency and `6.7x` lower cost than ReAct under dependency-aware parallelism (`04-agent-architecture.md`, `08-planning-reasoning.md`, `15-inference-optimization.md`). The practical reading is that production scaling is usually won first through `topology and cache discipline`, not through raw infrastructure multiplication [inferred].

Throughput constraints are framed more concretely than latency distributions. The local notes converge on RPM/TPM-style provider ceilings, token-bucket rate limiting, and cache-aware admission effects. One cited Anthropic example yields about `10,000,000` effective total input tokens/minute from a `2,000,000 ITPM` limit at `80%` cache hit rate because cache-read tokens often do not count toward ITPM (`04-agent-architecture.md`, `15-inference-optimization.md`). A useful first-order planning approximation is:

```text
max_completed_runs_per_minute
  ~= min(
       provider_rpm / avg_model_turns_per_run,
       provider_tpm / avg_total_tokens_per_run
     )
```

(`04-agent-architecture.md`, `15-inference-optimization.md`) [inferred]

## 3. Distributed Resilience & State

The local corpus is strongest on one production rule: keep `workflow continuity` separate from `tool/API capability access`. Checkpoints, sessions, run state, or workflow history should hold the durable control plane, while APIs, MCP servers, browsers, and execution sandboxes should remain replaceable capability surfaces (`04-agent-architecture.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`, `11-specialized-agents.md`, `14-observability.md`). This separation is what makes retries, replay, and partial failure diagnosable [inferred].

For `reliability`, Temporal is the clearest durable-execution reference in the local set. The architecture note describes exclusive workflow state ownership, append-only event history, deterministic replay, and the rule that external side effects belong in Activities whose results are reused during replay rather than re-executed (`04-agent-architecture.md`). Relative to pure transcript replay, that is the strongest local model for long-running jobs, pause/resume workflows, and human-in-the-loop automation [inferred].

The framework-level alternatives are weaker but still meaningful:

- `LangGraph`: checkpoints per super-step plus pending writes from successful siblings (`04-agent-architecture.md`, `05-agent-frameworks.md`, `14-observability.md`)
- `OpenAI Agents SDK`: session persistence plus serializable `RunState` around approval pauses (`04-agent-architecture.md`, `05-agent-frameworks.md`)
- `Google ADK`: explicit `Session`/`State`/`Memory` split plus in-process and row-level locking for session updates (`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`)
- `CrewAI`: workflow persistence with resume/fork semantics and production guidance that favors PostgreSQL over SQLite for multi-instance deployment (`04-agent-architecture.md`, `05-agent-frameworks.md`)

For `queues` and cross-service reliability, the local interoperability notes consistently treat remote agents and tool servers as distinct failure domains with their own deadlines, retries, auth refresh, and transport behavior (`09-multi-agent-systems.md`, `10-mcp-interoperability.md`). The practical production implication is `treat each remote boundary as a bulkhead`, not as an in-process function call [inferred].

The corpus also highlights a direct `throughput versus durability` tension. LangGraph's `sync`, `async`, and `exit` durability modes trade recovery strength against latency, and the local optimization note calls out a checkpoint-backlog failure pattern when execution outruns persistence throughput (`04-agent-architecture.md`, `05-agent-frameworks.md`, `15-inference-optimization.md`). That is effectively a queueing failure in the persistence plane, even when the model plane is healthy [inferred].

The local coverage gap remains explicit here too.  
> ⚠️ Limited public data available in the local research set for exactly-once side effects across heterogeneous APIs, queue-broker-specific delivery guarantees, or cluster-level failover behavior for `Docker`/`Kubernetes` deployments.

## 4. Enterprise Security & Governance

For `production APIs`, the dominant governance pattern in the local corpus is `strict schema -> policy check -> optional approval -> execution`. Typed API or MCP tools are preferred because they preserve narrow authority, keep validation legible, and create explicit audit boundaries around side effects (`03-tool-use.md`, `04-agent-architecture.md`, `10-mcp-interoperability.md`, `13-security-guardrails.md`).

For `inter-service auth`, `MCP` is the clearest Zero-Trust baseline in the local notes: OAuth-style authorization, PKCE, protected-resource metadata, and resource-bound tokens are treated as the protocol-level default for HTTP-based capability access (`04-agent-architecture.md`, `10-mcp-interoperability.md`, `13-security-guardrails.md`, `14-observability.md`). The clean production consequence is that authz and identity propagation should live at the capability boundary, not be hidden inside prompts [inferred].

For `containers` and execution isolation, the local hierarchy is consistent: `API/function tools` are lowest risk, `server-side code execution` is more isolated but more operationally constrained, and `browser/computer automation` is the highest-risk surface because it mixes untrusted visual content with direct action capability (`03-tool-use.md`, `11-specialized-agents.md`, `13-security-guardrails.md`). The safest production rule is `choose the narrowest tool surface that can complete the task` [inferred].

The local notes also treat `retrieved content`, `tool outputs`, `browser pages`, and `screenshots` as low-trust inputs that must not be promoted into high-trust policy channels or durable memory without validation (`07-memory.md`, `08-planning-reasoning.md`, `13-security-guardrails.md`). That matters operationally because production agents often fail through trust-boundary collapse long before they fail through raw model quality [inferred].

The biggest governance gaps remain:

> ⚠️ Limited public data available in the local research set for first-party `PII redaction` internals, immutable `audit-log schemas`, formal cross-system `RBAC` hierarchies, or hard isolation guarantees comparing containers, VMs, processes, and WASM (`05-agent-frameworks.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`, `13-security-guardrails.md`).

## 5. Production Failure Modes

### API-valid but policy-invalid actions

The security and architecture notes both stress that schema validity is not the same as authorization. A request can be perfectly well-formed and still target the wrong tenant, exceed scope, or violate business policy (`04-agent-architecture.md`, `08-planning-reasoning.md`, `13-security-guardrails.md`). In production, strict schemas reduce parser failures; they do not replace authz [inferred].

### Replay ambiguity after retries or approvals

Checkpointed and resumable systems improve recoverability, but resumed nodes or runs can still replay non-idempotent actions if checkpoint boundaries, approval state, or retry handling are misaligned (`04-agent-architecture.md`, `05-agent-frameworks.md`, `14-observability.md`). The durable production requirement is to record both `attempted action` and `confirmed external effect`, not just logical intent [inferred].

### Persistence backlog and queue pressure

The local optimization and architecture notes show a classic production failure where async durability or fan-out throughput outruns the persistence layer, creating checkpoint backlog and delayed recovery (`04-agent-architecture.md`, `05-agent-frameworks.md`, `15-inference-optimization.md`). Remote A2A or MCP boundaries add analogous transport queues, polling delays, and webhook/update failure surfaces (`09-multi-agent-systems.md`, `10-mcp-interoperability.md`) [inferred].

### Cascading timeouts and partial remote failure

Once tools or workers are remote, the system inherits deadline, retry, and auth-refresh failure modes that local function calls do not have. The local notes repeatedly recommend treating each remote server or agent as a separate bulkhead with its own timeout and fallback path (`09-multi-agent-systems.md`, `10-mcp-interoperability.md`, `14-observability.md`) [inferred].

### Context, cache, and routing degradation

The local production-adjacent notes repeatedly describe exact-prefix cache thrash, context-window bloat, wrong-worker routing, and over-decomposition as common ways an architecture becomes more expensive and less reliable without visibly crashing (`07-memory.md`, `09-multi-agent-systems.md`, `15-inference-optimization.md`). These are especially dangerous because they often appear first as `cost growth`, `latency drift`, or `quality variance`, not outright errors [inferred].

### Prompt injection on high-authority surfaces

Browser pages, screenshots, retrieved documents, and tool outputs can all carry hostile instructions. The local notes treat this as the defining failure mode for browser/computer automation and a major reason to prefer typed APIs whenever possible (`03-tool-use.md`, `11-specialized-agents.md`, `13-security-guardrails.md`).

### Incident coverage

> ⚠️ Limited public data available for detailed RCA-style production incidents focused specifically on agent `queue backlogs`, `Kubernetes autoscaling failures`, `API outage handling`, or `cluster runtime regressions` in the local research set. Most evidence is architecture guidance rather than published post-mortems.

## 6. Enterprise System Design Scenarios

### 6.1 Production pattern matrix

| Pattern | Best fit | Strongest locally supported benefits | Main trade-offs |
| --- | --- | --- | --- |
| `API-first SaaS copilot` | Internal systems with stable CRUD/service APIs | Lowest ambiguity, strongest schema validation, easy approval insertion, better cost profile than visual tools (`03-tool-use.md`, `13-security-guardrails.md`) | Still needs authz, rate-limit handling, and durable run state [inferred] |
| `Workflow engine + agent workers` | Long-running back-office jobs, human approvals, retries | Temporal-style event history and replay are stronger than transcript-only resume (`04-agent-architecture.md`) | More orchestration complexity and persistence infrastructure [inferred] |
| `MCP capability platform` | Shared enterprise tools/resources reused by many agents | Standardized auth/discovery/contracts, clean separation of capability access from workflow state (`10-mcp-interoperability.md`, `14-observability.md`) | Remote timeouts, auth misconfiguration, and cross-system observability burden |
| `Remote A2A / delegated workers` | Cross-team or cross-vendor automation | Transport choice, partial independence, polling/stream/push updates, graceful degradation options (`09-multi-agent-systems.md`) | Highest distributed-systems complexity and timeout surface |
| `Self-hosted structured-output gateway` | Open-weight serving where operator control matters | More control over structured output and serving behavior via `vLLM` (`01-llm-foundations.md`) | Local corpus does not provide enough evidence for detailed Kubernetes/ops guidance |

### 6.2 Recommended deployment patterns

**Pattern A: Customer-facing API agent**

Use `strict API/function tools`, keep workflow state in sessions or checkpoints, and add tracing plus approval gates around side-effecting writes (`03-tool-use.md`, `05-agent-frameworks.md`, `14-observability.md`). This is the cleanest production path when the business systems already expose good APIs [inferred].

**Pattern B: Multi-hour internal automation**

Use a durable workflow engine above the agent runtime. The local corpus is most confident when Temporal-like workflow history owns retries and replay, while LangGraph, ADK, or OpenAI/CrewAI runtimes handle bounded task logic (`04-agent-architecture.md`, `05-agent-frameworks.md`, `11-specialized-agents.md`) [inferred].

**Pattern C: Multi-system enterprise platform**

Expose internal tools and knowledge through `MCP`, but keep approval policy, session/checkpoint state, and audit IDs in the host runtime rather than in each tool server (`10-mcp-interoperability.md`, `13-security-guardrails.md`, `14-observability.md`). This preserves interoperability without pushing business control logic into every endpoint [inferred].

**Pattern D: API-less web workflow**

Use browser automation only when the target lacks a safe API, isolate the execution environment, and assume page content is adversarial by default (`03-tool-use.md`, `11-specialized-agents.md`, `13-security-guardrails.md`). This is the highest-friction production pattern in the local corpus, both economically and operationally [inferred].

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
critical_path_latency
  ~= planning_or_routing
   + max(parallel_branch_durations)
   + approvals
   + remote_transport_overhead
   + persistence / tracing
```

(`09-multi-agent-systems.md`, `10-mcp-interoperability.md`, `14-observability.md`) [inferred]

```text
production_reliability
  improves when workflow state
  is stored outside protocol sessions
  and each remote boundary has its own
  timeout, retry, and audit policy
```

(`04-agent-architecture.md`, `10-mcp-interoperability.md`, `13-security-guardrails.md`) [inferred]

```text
scaling_roi
  is usually higher from better decomposition,
  cache stability, and bounded workers
  than from adding more raw agent turns
```

(`08-planning-reasoning.md`, `09-multi-agent-systems.md`, `15-inference-optimization.md`) [inferred]

### 6.4 Strongest practical conclusions

1. The strongest production pattern in the local corpus is `stateful workflow control above narrow execution surfaces`: checkpoints/sessions/history above, APIs/MCP/tools/containers below.
2. `API-first` remains the safest and cheapest default, while `browser/computer automation` is a last resort for systems without usable APIs (`03-tool-use.md`, `11-specialized-agents.md`, `13-security-guardrails.md`) [inferred].
3. `Scaling` is primarily a topology problem in this corpus: bounded workers, cache-stable prefixes, and parallelizable graphs beat naive serial loop expansion.
4. The biggest unresolved local gaps are precise `Docker/Kubernetes` operating guidance, queue-broker-specific semantics, and benchmarked cluster-level reliability data.

## Sources

- [1] `01-llm-foundations.md` - Local research note covering self-hosted/open-weight serving control surfaces such as `vLLM` and known gaps around runtime-isolation internals.
- [2] `03-tool-use.md` - Local research note covering API/function tools, hosted containers, browser/computer automation, rate limits, cache behavior, and production failure surfaces.
- [3] `04-agent-architecture.md` - Local research note covering control-plane/data-plane separation, Temporal durable workflows, rate-limit and replay mechanics, and topology-level scaling patterns.
- [4] `05-agent-frameworks.md` - Local research note covering framework persistence, approvals, deployment posture, durability modes, and multi-instance production considerations.
- [5] `07-memory.md` - Local research note covering cache instability, memory-layer governance, and context-management risks that show up in production operations.
- [6] `08-planning-reasoning.md` - Local research note covering planner/executor economics, bounded execution, and approval-gated reasoning patterns.
- [7] `09-multi-agent-systems.md` - Local research note covering supervisor/worker design, remote delegation, timeout/update modes, transport-level failure domains, and production scale heuristics.
- [8] `10-mcp-interoperability.md` - Local research note covering MCP auth/discovery, workflow-state versus capability-access separation, and remote reliability bulkheads.
- [9] `11-specialized-agents.md` - Local research note covering specialist deployment trade-offs, container/state reuse, and when browser or coding specialists are production-appropriate.
- [10] `12-evaluation.md` - Local research note covering cost accounting, container/tool fees, and production evaluation signals beyond final-answer quality.
- [11] `13-security-guardrails.md` - Local research note covering trust boundaries, sandbox hierarchy, approval patterns, and governance gaps in production agent systems.
- [12] `14-observability.md` - Local research note covering trajectory/resource/evidence observability, checkpoint lineage, and the need to log confirmed external effects.
- [13] `15-inference-optimization.md` - Local research note covering caching, batching, throughput ceilings, durability pressure, and scaling trade-offs.
