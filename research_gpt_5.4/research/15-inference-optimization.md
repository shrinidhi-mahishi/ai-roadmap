# Research: Inference & Optimization - Caching, routing, batching, quantization

**Date researched**: 2026-08-21
**Sources consulted**: 11

---

## 1. System Topology & Mechanics

`Inference optimization` shows up across the local research corpus less as one isolated serving trick and more as a layered control-plane strategy around model calls. The recurring mechanisms are: `exact-prefix prompt caching`, `semantic caching`, `context compaction`, `artifact lazy-loading`, `node/result caching`, `planner-to-executor routing`, and `parallel branch execution` (`01-llm-foundations.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `07-memory.md`, `08-planning-reasoning.md`) [inferred].

At the request level, the cleanest topology is `parallel prefill, serial decode`, then optimize everything around that expensive decode path by shrinking repeated prompt state and reducing unnecessary turns. The local corpus ties this directly to prompt caching, structured continuation, and history shaping rather than to a first-party "automatic optimizer" abstraction (`01-llm-foundations.md`, `05-agent-frameworks.md`) [inferred].

The strongest documented optimization pattern is to split static and dynamic context. `OpenAI Agents SDK` can continue runs via `conversation_id` or `previous_response_id` instead of resending the entire transcript, `Google ADK` compacts older history once token or turn thresholds are hit, and ADK artifacts keep large blobs out of the default prompt until explicitly loaded (`05-agent-frameworks.md`, `07-memory.md`). Mechanically, that means inference optimization often starts with `send less`, not with faster model kernels [inferred].

`LangGraph` adds another topology: cache work at node boundaries. The local framework note documents `CachePolicy(ttl=...)` and cached node returns via `__metadata__.cached = True`, while checkpoints at super-step boundaries let the graph resume without recomputing already-persisted sibling outputs (`05-agent-frameworks.md`). In practice, this turns the optimization unit from "whole conversation" into "reusable subgraph result" [inferred].

For `routing`, the corpus repeatedly converges on `strong planner, cheaper bounded executors`. Planner/executor and DAG systems reduce repeated expensive reasoning turns by planning once, then routing bounded steps to narrower workers or cheaper models; `LLMCompiler` is the clearest benchmark-backed expression of that pattern in the local notes (`04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`).

For `batching`, the local evidence is architectural rather than kernel-level. `LangGraph` can execute nodes receiving messages in the same super-step in parallel, `ADK` exposes `ParallelAgent`, Azure-style agentic retrieval fans out subqueries in parallel, and multi-agent supervisor systems reduce wall-clock time when worker branches overlap (`04-agent-architecture.md`, `05-agent-frameworks.md`, `06-rag.md`, `09-multi-agent-systems.md`). This is closer to `workflow batching / parallel fan-out` than to provider-documented GPU microbatching internals [inferred].

`Quantization` is the weakest part of the local corpus. The existing notes discuss self-hosted/open-weight control surfaces such as `vLLM` structured outputs, but they do not provide concrete local coverage of `INT8`, `4-bit`, `AWQ`, `GPTQ`, KV-cache quantization, or production accuracy/latency trade-offs (`01-llm-foundations.md`).  
> ⚠️ Limited public data available in the local research set for low-level quantization mechanics, kernel choices, or benchmarked quality-loss trade-offs. The strongest local evidence is on caching, routing, batching/parallelism, and prompt-state reduction rather than weight-compression internals.

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable end-to-end `p50/p95/p99` latency for inference-optimization stacks in the local research set. The notes are much stronger on billing math, throughput ceilings, cache economics, and structural latency trade-offs than on percentile SLAs.

The first-order cost formula synthesized from the local notes is:

```text
optimized_run_cost
  ~= uncached_input_cost
   + cached_read_cost
   + cache_write_cost
   + output_cost
   + tool_or_retrieval_surcharges
   + orchestration_overhead
```

(`01-llm-foundations.md`, `03-tool-use.md`, `04-agent-architecture.md`, `12-evaluation.md`) [inferred]

`Prompt caching` is the clearest published economic win. The local corpus already establishes that OpenAI GPT-5.6+ and Anthropic 5-minute caches use roughly `1.25x` write pricing and `0.1x` read pricing, so a stable cacheable prefix becomes cheaper on the first reuse; Anthropic's longer `1 hour` cache tier becomes cheaper on the second reuse because writes cost `2x` base input (`01-llm-foundations.md`, `04-agent-architecture.md`, `07-memory.md`, `12-evaluation.md`).

Caching is also a throughput lever, not just a cost lever. The architecture and foundations notes show that OpenAI still counts cached tokens toward TPM, but Anthropic often excludes `cache_read_input_tokens` from ITPM, and one documented example yields about `10,000,000` effective total input tokens/minute from a `2,000,000 ITPM` ceiling at `80%` cache hit rate (`01-llm-foundations.md`, `04-agent-architecture.md`). That means identical cache hit rates can improve one provider's admission envelope more than another's [inferred].

`Routing` matters because model choice can dominate run economics more than framework choice. The local planning and architecture notes both argue that the cheapest pattern is usually `strong planner, cheaper executor`, because the expensive reasoning model is called fewer times than in pure ReAct (`04-agent-architecture.md`, `08-planning-reasoning.md`). The strongest quantified evidence remains `LLMCompiler`, which reported up to `3.7x` lower latency and `6.7x` lower cost than ReAct when dependency-aware parallelism was possible (`04-agent-architecture.md`, `08-planning-reasoning.md`, `12-evaluation.md`).

A useful routing-aware planning formula is:

```text
effective_total_tokens_per_run
  ~= planner_tokens
   + executor_tokens
   + verifier_tokens
   + replayed_history
   + tool_outputs
   - cached_or_compacted_prefix
```

(`05-agent-frameworks.md`, `07-memory.md`, `08-planning-reasoning.md`) [inferred]

For `batching` and throughput, the local notes expose workflow-level rather than provider-internal batching signals. `LangGraph` parallel super-steps shorten the critical path, Azure-style agentic retrieval parallelizes subqueries, and multi-agent workers can overlap while the overall throughput ceiling is still constrained by provider RPM/TPM plus orchestration overhead (`05-agent-frameworks.md`, `06-rag.md`, `09-multi-agent-systems.md`). In other words, batching helps most when the workload can be decomposed into independent branches; it helps least when every action depends on the previous model output [inferred].

The local capacity formulas that generalize best are:

```text
max_completed_runs_per_minute
  ~= min(
       provider_rpm / avg_model_turns_per_run,
       provider_tpm / avg_total_tokens_per_run
     )
```

(`04-agent-architecture.md`, `08-planning-reasoning.md`) [inferred]

```text
critical_path_latency
  ~= planning_latency
   + max(parallel_branch_durations)
   + verification_latency
   + answer_synthesis_latency
```

(`05-agent-frameworks.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`) [inferred]

`Quantization` remains a local evidence gap. None of the current `research_cursor/research` notes provides benchmarked numbers for memory-footprint reduction, throughput increase, or quality degradation from lower-precision inference.  
> ⚠️ Limited public data available in the local research set for `quantization ROI`, `KV-cache compression`, or `tokens/sec` improvements from reduced precision. Current local evidence does not support fabricating a cost or latency table for quantized serving.

## 3. Distributed Resilience & State

Optimization features only work reliably if their state model is stable. The memory note is explicit that `exact-prefix cache` retrieval is brittle: OpenAI-style caches depend on a byte-stable shared prefix, and Anthropic-style caches are sensitive to block position and backward lookup windows. When serialization drifts, optimization silently degrades into repeated cache writes and fresh-input misses (`07-memory.md`, `03-tool-use.md`). That makes prompt formatting discipline part of system resilience, not just prompt aesthetics [inferred].

`Semantic caching` has the opposite resilience profile. Redis-style semantic cache entries can widen hit rates through similarity matching, TTLs, and eviction policies, but the failure class becomes `incorrect reuse` rather than `missed reuse` (`07-memory.md`). Operationally, exact-prefix caches fail toward higher cost, while semantic caches can fail toward wrong answers [inferred].

For `routing` and `batching`, durable execution matters because optimization often creates more branches, not fewer. `LangGraph` persists checkpoints at super-step boundaries and stores pending writes from successful sibling nodes, `ADK` documents in-process plus row-level locking for session updates, and `OpenAI Agents SDK` persists session state plus serializable `RunState` around approval pauses (`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`). Without those state substrates, retries can erase the gains from parallel decomposition or mis-replay routed steps [inferred].

The clean resilience split synthesized from the corpus is:

- keep `workflow continuity` in sessions, checkpoints, or workflow history (`04-agent-architecture.md`, `05-agent-frameworks.md`)
- keep `cache and artifact state` in explicit cache/store layers (`05-agent-frameworks.md`, `07-memory.md`)
- keep `capability access` in MCP or tool protocols rather than conflating transport state with run state (`10-mcp-interoperability.md`)

This matters because `cache drift`, `route drift`, and `tool/auth drift` are different outages even when they all present as "the run got slower or failed" [inferred].

The framework note also surfaces a direct optimization-vs-durability trade-off in `LangGraph`: `sync` durability gives stronger recovery but adds overhead, `async` overlaps persistence with the next step, and `exit` maximizes performance while weakening mid-run recovery (`05-agent-frameworks.md`). This implies that aggressive batching or fan-out can outpace the persistence layer unless durability mode and backend capacity are sized together [inferred].

`Quantization` again has almost no resilience coverage in the local corpus. There is no documented local discussion of quantized checkpoint compatibility, mixed-precision rollback behavior, or serving-fleet version skew.  
> ⚠️ Limited public data available in the local research set for resilience implications of quantized inference, including version skew between quantized and non-quantized replicas or replay consistency across precision changes.

## 4. Enterprise Security & Governance

Inference optimization changes the governance surface because optimized context is still sensitive context. The local notes already warn that traces can include prompts, tool inputs/outputs, cached-token counters, and reasoning metadata, and that tracing/privacy decisions can conflict with data-retention constraints (`03-tool-use.md`, `05-agent-frameworks.md`, `14-observability.md`). Optimizing a run by caching or reusing context does not reduce the need to classify and govern that context [inferred].

`Caching` especially interacts with trust boundaries. The memory and guardrails notes argue that stable policy blocks, schemas, and server metadata are the easiest parts of a workflow to cache, but they also warn that low-trust tool outputs or retrieved content should not be promoted into high-trust instruction channels (`07-memory.md`, `13-security-guardrails.md`). The safe rule is `cache stable trusted scaffolding aggressively; validate low-trust dynamic content before any durable reuse` [inferred].

For `routing`, governance is really authorization plus observability. Multi-agent and planning notes show that router decisions are usually application logic rather than a first-party universal policy engine, while OpenAI-style approvals and MCP approval controls provide the actual execution gate for high-impact actions (`08-planning-reasoning.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`). A route can be efficient and still unauthorized; optimization should never bypass the approval plane [inferred].

For `batching` and shared retrieval/capability layers, permission-aware backends matter. The RAG, memory, and interoperability notes all emphasize that retrieval exposed through `retrieve` or `MCP` still depends on role- or key-based controls in the backing system (`06-rag.md`, `07-memory.md`, `10-mcp-interoperability.md`). Otherwise a shared cache, shared knowledge plane, or shared worker pool can become a cross-tenant leakage path [inferred].

The local corpus remains weak on the governance internals of compressed serving stacks.  
> ⚠️ Limited public data available in the local research set for `quantized model governance`, `PII redaction before cache writes`, `RBAC over cache entries`, or immutable audit schemas for optimization-layer decisions such as route selection and cache invalidation.

## 5. Production Failure Modes

### Exact-prefix cache thrash

The memory and tool-use notes describe the simplest optimization failure: the system keeps paying cache-write cost but earns few cache hits because serialization changes, tool schemas move, or the stable prefix no longer reaches the required breakpoint (`03-tool-use.md`, `07-memory.md`). This usually surfaces as a silent cost and latency regression rather than a visible crash [inferred].

### Semantic-cache false positives

Semantic caching broadens hit rates, but the local memory note is explicit that similarity-based reuse can return "close enough" prior answers whose hidden constraints differ from the new request (`07-memory.md`). This is a correctness failure disguised as an optimization win.

### Wrong-model or wrong-worker routing

The multi-agent note warns that ambiguous specialist descriptions and fuzzy delegation criteria degrade routing quality, while the planning note says universal complexity routers are generally not first-party framework features (`08-planning-reasoning.md`, `09-multi-agent-systems.md`). In production, bad routing can look like random cost spikes, inconsistent latency, or weak answers because the system sent the request to the wrong capability tier [inferred].

### Over-decomposition and fan-out burn

The planning and RAG notes both warn that decomposition is not free. Query rewrite loops, multi-subquery retrieval, and planner/executor expansion can add cost and latency faster than they add quality when the task was not actually decomposable (`06-rag.md`, `08-planning-reasoning.md`, `12-evaluation.md`). The failure mode is "optimized architecture" that is objectively worse than a simpler baseline [inferred].

### Parallelism outrunning durability

The framework and architecture notes document that async durability improves throughput but can create operational pressure if persistence lags execution, and LangGraph even needed a fix for checkpoint-task backlog under this pattern (`04-agent-architecture.md`, `05-agent-frameworks.md`). This is the classic optimization failure where throughput work creates a new queueing bottleneck [inferred].

### Context-window bloat despite optimization

The memory and framework notes show that optimization can fail simply because too much history, too many tool schemas, or too many artifacts still get replayed. ADK compaction, artifact isolation, OpenAI session shaping, and LangGraph caching exist precisely because raw transcript growth can erase the benefits of other inference optimizations (`05-agent-frameworks.md`, `07-memory.md`, `13-security-guardrails.md`).

### Quantization blind spot

The current local corpus does not provide evidence strong enough to identify specific production failure modes for quantized inference such as accuracy cliffs, unsupported kernels, or mixed-precision rollback bugs.  
> ⚠️ Limited public data available for quantization-specific incident patterns in the local research set; current evidence supports discussing this as a gap rather than claiming concrete failure behavior.

## 6. Enterprise System Design Scenarios

### 6.1 Optimization pattern matrix

| Pattern | Best fit | Strongest documented benefits | Main trade-offs |
| --- | --- | --- | --- |
| `Exact-prefix caching` | Stable long instruction blocks, reusable schemas, repeated enterprise workflows | First documented economic win in the local corpus; first-reuse break-even under common `1.25x` write / `0.1x` read pricing (`01-llm-foundations.md`, `04-agent-architecture.md`, `07-memory.md`) | Brittle to serialization drift; weak for semantically similar but non-identical requests |
| `Semantic caching` | FAQ/support workloads with paraphrased repeats | Potentially higher hit rate than exact prefix matching (`07-memory.md`) | False-positive reuse risk and threshold-tuning burden |
| `Planner/executor routing` | Multi-step tasks where strong reasoning can be amortized | Lower latency and cost than serial ReAct when steps are bounded or parallelizable (`04-agent-architecture.md`, `08-planning-reasoning.md`, `12-evaluation.md`) | More orchestration state, routing ambiguity, and branch-management complexity |
| `Parallel fan-out / workflow batching` | Independent subtasks, retrieval subqueries, supervisor-worker systems | Shorter critical path through overlapping branches (`05-agent-frameworks.md`, `06-rag.md`, `09-multi-agent-systems.md`) | Higher persistence, observability, and coordination burden |
| `Quantized serving` | Self-hosted open-weight inference stacks | Not well-covered in the local corpus; likely attractive when hardware efficiency dominates [inferred] (`01-llm-foundations.md`) | Local research set does not provide enough evidence for a defensible trade-off matrix |

### 6.2 Recommended deployment patterns

**Pattern A: API-first SaaS copilot**

Use `exact-prefix caching + strong planner / cheaper executor routing` before introducing heavier topology changes. The local notes suggest the biggest early wins come from keeping policy/tool scaffolding stable, bounding history growth, and reducing unnecessary high-end model turns (`04-agent-architecture.md`, `05-agent-frameworks.md`, `07-memory.md`, `08-planning-reasoning.md`) [inferred].

**Pattern B: Retrieval-heavy enterprise assistant**

Use `parallel subquery fan-out` only when the question is truly multi-part, and keep references or activity logs so cost growth can be tied back to better grounding rather than hidden inside one total-latency number (`06-rag.md`, `12-evaluation.md`, `14-observability.md`) [inferred].

**Pattern C: Multi-agent operations workflow**

Prefer centralized supervision with narrow workers so routing remains legible, policy enforcement stays centralized, and worker prompts stay small enough to preserve cacheability and context isolation (`09-multi-agent-systems.md`, `11-specialized-agents.md`, `14-observability.md`) [inferred].

**Pattern D: Long-running or human-gated automation**

Treat `optimization state` and `durable workflow state` separately. Use caching and compaction to reduce token load, but rely on checkpoints, sessions, or run-state serialization for pause/resume and replay safety (`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`).

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
cache_value
  improves when stable_prefix_tokens
  are large,
  reuse frequency is high,
  and serialization remains stable
```

(`03-tool-use.md`, `07-memory.md`, `13-security-guardrails.md`) [inferred]

```text
routing_roi
  improves when planner_cost
  < avoided_repeated_high_end_turns
  and worker context << full transcript
```

(`08-planning-reasoning.md`, `09-multi-agent-systems.md`) [inferred]

```text
parallelization_value
  improves when branch dependencies are low
  and persistence / synthesis overhead
  < serial latency avoided
```

(`05-agent-frameworks.md`, `06-rag.md`, `09-multi-agent-systems.md`) [inferred]

### 6.4 Strongest practical conclusions

1. The strongest locally supported inference optimizations are `cache stable prompt state`, `compact or externalize bulky context`, and `route expensive reasoning sparingly`.
2. `Batching` is best understood in this corpus as `parallel workload decomposition`, not as a well-documented vendor GPU kernel primitive.
3. `Routing` becomes worthwhile when it reduces repeated high-end reasoning turns or isolates workers with much smaller context than the full transcript.
4. `Quantization` is the largest unresolved topic gap in the local research set; the current corpus does not support a precise enterprise recommendation beyond noting that self-hosted stacks likely need it when hardware efficiency is the binding constraint [inferred].

## Sources

- [1] `01-llm-foundations.md` - Local research note covering transformer inference shape, provider cache pricing, reasoning-token economics, and open-weight serving control surfaces such as vLLM structured outputs.
- [2] `03-tool-use.md` - Local research note covering prompt-caching behavior, tool-surface token overhead, rate limits, cache failure triggers, and approval/tracing implications around external actions.
- [3] `04-agent-architecture.md` - Local research note covering ReAct vs planner/executor vs DAG patterns, `LLMCompiler` cost/latency benchmarks, durable execution, and cache-aware cost formulas.
- [4] `05-agent-frameworks.md` - Local research note covering LangGraph node caching, ADK compaction and artifacts, usage accounting, routing support, durability modes, and framework-level throughput constraints.
- [5] `06-rag.md` - Local research note covering hybrid retrieval, agentic retrieval fan-out, reranking cost, and parallel subquery execution as a throughput/latency optimization.
- [6] `07-memory.md` - Local research note covering exact-prefix cache economics, semantic-cache trade-offs, context compaction, cache-thrash failure modes, and memory-layer governance risks.
- [7] `08-planning-reasoning.md` - Local research note covering planner/executor economics, verifier/rewrite loops, replanning overhead, and the `strong planner + bounded executors` optimization pattern.
- [8] `09-multi-agent-systems.md` - Local research note covering supervisor-worker routing, critical-path latency, context isolation benefits, delegation overhead, and wrong-route failure modes.
- [9] `12-evaluation.md` - Local research note covering cost/latency as first-class evaluation axes, cache-aware run accounting, and measurement of coordination efficiency versus answer quality.
- [10] `13-security-guardrails.md` - Local research note covering stable policy prefixes as cache-friendly structures and the governance consequences of optimization choices around retrieval and reuse.
- [11] `14-observability.md` - Local research note covering runtime telemetry for cached tokens, reasoning tokens, routing lineage, and the distinction between optimization signals and final task success.
