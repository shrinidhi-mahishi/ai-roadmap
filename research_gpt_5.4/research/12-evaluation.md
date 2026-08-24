# Research: Evaluation - Task success, trajectory, tool accuracy, quality, cost, latency

**Date researched**: 2026-08-21
**Sources consulted**: 11

---

## 1. System Topology & Mechanics

`Evaluation` appears in the local research corpus less as one standalone framework feature and more as a **multi-layer control plane** wrapped around agent execution. The relevant layers are: `task-outcome evaluation`, `trajectory/trace evaluation`, `tool-call validation`, `grounding or retrieval-quality evaluation`, and `cost/latency telemetry` (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `06-rag.md`, `08-planning-reasoning.md`) [inferred].

For `task success`, the cleanest pattern in the local notes is to evaluate against **explicit workflow state transitions or bounded outputs**, not only free-form final text. LangGraph and LangChain-style graphs expose node boundaries and persisted state, OpenAI Agents SDK surfaces structured run usage and approvals, and Azure agentic retrieval returns references plus an activity log, all of which make "did the task succeed?" more inspectable than a single final answer string (`04-agent-architecture.md`, `05-agent-frameworks.md`, `06-rag.md`, `08-planning-reasoning.md`).

For `trajectory evaluation`, the corpus strongly favors **stepwise traces over opaque transcripts**. ReAct exposes an interleaved reason/act/observe loop, planner-executor systems split planning from execution, verifier/rewrite loops expose explicit retry branches, and LangGraph persists checkpoints at super-step boundaries (`04-agent-architecture.md`, `08-planning-reasoning.md`). That means trajectory quality can be judged from measurable signals like branch count, retry count, loop depth, checkpoint reuse, and whether execution converges without repeated replanning [inferred] (`04-agent-architecture.md`, `08-planning-reasoning.md`).

For `tool accuracy`, the local notes draw a consistent distinction between **schema validity** and **semantic correctness**. OpenAI and Anthropic both publish strict schema/tool modes, but the same notes warn that schema-valid tool calls can still choose the wrong parameters or wrong business action (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`). A good tool-accuracy evaluator therefore needs at least two checks: "was the call structurally valid?" and "did it act on the right target with the right arguments?" [inferred].

For `quality evaluation`, retrieval-heavy notes provide the strongest topology. Azure agentic retrieval and LangGraph agentic RAG both expose relevance-grading and query-rewrite stages, while the memory note separates retrieval quality from cache-hit behavior and from final answer correctness (`06-rag.md`, `07-memory.md`, `08-planning-reasoning.md`). In practice, the evaluation stack should treat `retrieval quality`, `answer quality`, and `memory/cache behavior` as separate dimensions rather than one blended score [inferred].

The local framework notes also imply an architectural split between **inline evaluators** and **post-run evaluators**. Guardrails, strict schemas, and approvals act inline before or during execution, while usage accounting, traces, references, and activity logs support post-run scoring and audit (`03-tool-use.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable `p50/p95/p99` evaluation benchmarks of full agent runs in the local research set. The strongest local evidence is on cost formulas, throughput constraints, structural latency trade-offs, and a small number of benchmark deltas rather than standardized evaluation suites.

The local corpus makes `cost` and `latency` first-class evaluation axes because both are already instrumented by the underlying platforms. OpenAI usage surfaces `input_tokens`, `output_tokens`, `cached_tokens`, `cache_write_tokens`, and `reasoning_tokens`; CrewAI exposes aggregated flow metrics including cached and reasoning tokens; ADK usage metadata includes prompt, candidate, thought, tool-use prompt, and cached-content token counts (`05-agent-frameworks.md`, `08-planning-reasoning.md`). That means cost evaluation can be attached directly to runtime telemetry rather than inferred from logs after the fact.

A reusable local formula for execution economics is:

```text
evaluation_run_cost
  ~= model_input_cost
   + cached_read_cost
   + cache_write_cost
   + output_cost
   + tool_or_retrieval_surcharges
   + sandbox/container_fees
```

(`01-llm-foundations.md`, `03-tool-use.md`, `04-agent-architecture.md`, `06-rag.md`, `08-planning-reasoning.md`) [inferred]

The corpus also gives concrete published numbers that are useful for evaluation baselines:

- OpenAI hosted web search is `$10 / 1k calls`, file search is `$2.50 / 1k calls`, and hosted containers range from `$0.03` to `$1.92` per 20-minute session depending on memory tier (`03-tool-use.md`).
- OpenAI GPT-5.6+ and Anthropic 5-minute caches both use roughly `1.25x` write cost and `0.1x` read cost, so a stable prefix becomes cheaper on the first reuse (`03-tool-use.md`, `04-agent-architecture.md`, `07-memory.md`) [inferred].
- Anthropic publishes browser-tool overhead of roughly `6,610-6,670` input tokens and computer-tool overhead of roughly `4,520-4,590` input tokens before screenshots and user task content, making tool-surface cost itself an evaluable NFR (`03-tool-use.md`, `11-specialized-agents.md`).
- Azure's agentic retrieval example assumes `2,000` retrievals, `3` subqueries, `50` reranked chunks per subquery, and `500` tokens per chunk, for `150M` reranking tokens and a hypothetical `$4.32` combined planning+rereanking total in the worked example (`06-rag.md`, `08-planning-reasoning.md`).

For `latency evaluation`, the strongest local conclusion is structural rather than provider-SLA-based: serial ReAct-like loops lengthen latency roughly with iteration count, while planner/executor and DAG systems reduce the **critical path** by amortizing planning or parallelizing independent steps (`04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`) [inferred]. `LLMCompiler` is the clearest local benchmark datapoint, reporting up to `3.7x` lower latency, `6.7x` lower cost, and about `9%` higher accuracy than ReAct on its benchmark suite when dependency-aware parallelism is possible (`04-agent-architecture.md`, `08-planning-reasoning.md`).

The local notes also warn against collapsing all evaluation into one "quality" score. Anthropic reports an average `11%` quality improvement with `24%` fewer input tokens for programmatic tool calling on BrowseComp and DeepSearchQA, while context-engineering notes distinguish retrieval success from end-task reasoning success and long-context quality from raw recall (`02-context-engineering.md`, `03-tool-use.md`). So the practical metric set should at minimum track `task success`, `trajectory efficiency`, `tool correctness`, `quality`, `cost`, and `latency` separately [inferred].

## 3. Distributed Resilience & State

The local corpus implies that evaluation is only reliable if the system preserves enough state to **reconstruct what happened**. LangGraph checkpoints persist a full `StateSnapshot` per super-step plus pending writes from successful sibling nodes; OpenAI exposes resumable run patterns and session/conversation continuity; ADK separates Session, State, and Memory; Azure agentic retrieval returns an activity log and references (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`, `06-rag.md`, `08-planning-reasoning.md`). These are not just runtime features; they are the data plane required for trustworthy post-run evaluation [inferred].

For `trajectory` specifically, persisted checkpoints matter because they distinguish **one long successful plan** from **many hidden retries that happened to end well**. If the evaluator sees only the final output, it misses replanning storms, loop caps, dead branches, and repeated tool invocations; if it sees checkpoint history and branch structure, it can score convergence efficiency directly (`04-agent-architecture.md`, `08-planning-reasoning.md`) [inferred].

For `tool accuracy`, replayable state is also essential. The tool-use and framework notes both warn that side-effecting tool loops can replay non-idempotent actions unless the surrounding system has approvals, validation, and durable run state (`03-tool-use.md`, `05-agent-frameworks.md`, `11-specialized-agents.md`). Evaluation therefore has to separate:

- `argument correctness`
- `execution success`
- `idempotent replay safety`
- `business correctness after the side effect`

(`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`) [inferred]

For `quality` in retrieval-heavy systems, the memory and RAG notes show why state must be layered. Retrieval systems hold `index-time state`, `query-time state`, and often cache or activity-log state; failures can come from any of those layers, including retrieval starvation, stale memory, semantic-cache false positives, or cost-heavy graph-memory builds (`06-rag.md`, `07-memory.md`). A resilient evaluation setup therefore needs artifact capture at the layer where the failure actually occurred, not only a final answer transcript [inferred].

The practical resilience pattern across the local notes is:

- Keep `workflow/trajectory state` in sessions, checkpoints, or workflow history (`04-agent-architecture.md`, `05-agent-frameworks.md`).
- Keep `evidence artifacts` such as tool results, references, retrieval candidates, and usage telemetry attached to the run (`05-agent-frameworks.md`, `06-rag.md`, `08-planning-reasoning.md`) [inferred].
- Keep `capability access` behind structured protocols and approvals, not as invisible side effects in free text (`03-tool-use.md`, `10-mcp-interoperability.md`).

## 4. Enterprise Security & Governance

The local notes repeatedly show that a high `quality` or `task success` score is **not the same thing as governed execution**. Planning notes state this explicitly: correctness verification is different from authorization for a side effect, so evaluation must keep `task success` and `policy compliance` as separate checks (`08-planning-reasoning.md`). A run can be "successful" from the model's perspective and still violate approval, RBAC, or data-handling requirements [inferred].

For `tool accuracy` and `trajectory` governance, strict schemas are the minimum control surface. OpenAI recommends `strict: true`, `additionalProperties: false`, and fully required fields for structured outputs, while Anthropic strict tool use guarantees schema-valid tool names and inputs (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`). That improves structural correctness, but the local notes are clear that schema-valid calls can still be semantically wrong, so business-rule validation or human review remains necessary [inferred].

For `evidence quality`, the memory and planning notes both warn against promoting untrusted tool output or retrieved text into high-trust instruction channels (`07-memory.md`, `08-planning-reasoning.md`). This matters directly for evaluation because judge inputs are only trustworthy if the evaluated run preserved source boundaries. If browser content, retrieval snippets, or tool results are mixed back into instructions, later quality judgments can be poisoned by the same prompt-injection problem they are trying to detect [inferred].

For `interoperable evaluation surfaces`, the strongest Zero-Trust baseline in the local corpus remains MCP authorization: OAuth, Protected Resource Metadata, Resource Indicators, and PKCE are treated as the protocol-level standard for external capability access (`10-mcp-interoperability.md`, `08-planning-reasoning.md`). That means evaluation pipelines that consume MCP-backed traces or tool results should inherit the same auth and approval boundaries instead of treating "read-only evaluation" as automatically safe [inferred].

The corpus remains weak on several governance dimensions that matter for enterprise evaluation:

> ⚠️ Limited public data available in the local research set for built-in `PII redaction` of traces, immutable audit-log schemas for evaluator decisions, and framework-native RBAC hierarchies over evaluation artifacts. The documentation is much stronger on approvals, auth, traces, and checkpointing than on compliance-grade evaluation governance (`03-tool-use.md`, `05-agent-frameworks.md`, `09-multi-agent-systems.md`, `10-mcp-interoperability.md`).

## 5. Production Failure Modes

### Task-success inflation

The local notes repeatedly warn that narrow success signals can be misleading. Context-engineering research distinguishes retrieval success from end-task reasoning success, and planning notes warn that verifier loops can keep operating without materially improving the answer (`02-context-engineering.md`, `08-planning-reasoning.md`). An evaluation stack that scores only "final answer exists" or "retrieval found something" will overestimate real task completion [inferred].

### Trajectory thrash hidden by a correct final answer

`Replanning storms`, repeated query rewrites, and long serial ReAct loops are explicit failure modes in the planning and architecture notes (`04-agent-architecture.md`, `08-planning-reasoning.md`). A run can eventually succeed while still being operationally bad because it used too many branches, retries, or tool turns; this is why trajectory efficiency needs its own metric family [inferred].

### Schema-valid but wrong tool actions

The corpus is direct on this point: strict schemas reduce malformed arguments, but they do not guarantee the selected values are correct or aligned with user intent (`04-agent-architecture.md`, `08-planning-reasoning.md`). Tool accuracy evaluation must therefore detect false positives where the call parsed correctly but acted on the wrong record, wrong resource, or wrong downstream action [inferred].

### Retrieval-quality false confidence

RAG and memory notes describe `retrieval starvation`, bounded reranking over only the top `50` results, semantic-cache false positives, and global-question failure for naive RAG (`06-rag.md`, `07-memory.md`). These create a specific evaluation trap: answer quality can look fluent and well-grounded while the evidence set was incomplete or mismatched [inferred].

### Cost and latency regressions from "quality" optimizations

The local notes document several ways that quality-oriented architecture choices can degrade NFRs: extra tool surfaces add prompt cost, browser/computer loops add fixed token overhead, agentic retrieval adds planning and reranking latency, and over-decomposition can add noise and spend rather than value (`03-tool-use.md`, `06-rag.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). Evaluation has to watch for quality gains that are not worth the added cost or delay [inferred].

### Long-horizon brittleness

The local research base treats this as an open challenge. BFCL says memory, dynamic decision-making, and long-horizon reasoning remain open problems, while `Lost in the Middle` and `RULER` show that long context by itself does not guarantee reliable use of buried evidence (`01-llm-foundations.md`, `02-context-engineering.md`, `08-planning-reasoning.md`). So trajectory and quality evaluation both need long-horizon test cases rather than only single-turn correctness checks [inferred].

### Governance mismatch in multi-agent settings

The multi-agent note adds an evaluation warning that single-agent safety or quality does not automatically transfer to coordinated groups. Anthropic's cited research found that groups of individually aligned agents can behave less ethically than a single agent even when they are more effective (`09-multi-agent-systems.md`). In enterprise terms, evaluator coverage must extend from the `individual run` to the `organization of runs` [inferred].

## 6. Enterprise System Design Scenarios

### 6.1 Recommended evaluation matrix

| Evaluation axis | What to measure | Strongest local evidence source | Main trap |
| --- | --- | --- | --- |
| `Task success` | Final state reached, acceptance criteria satisfied, grounded completion | Workflow state, references, activity logs (`04-agent-architecture.md`, `06-rag.md`, `08-planning-reasoning.md`) | Counting fluent answers as success |
| `Trajectory` | Iterations, retries, branch count, loop caps, checkpoint reuse | ReAct/planner traces, checkpoints (`04-agent-architecture.md`, `08-planning-reasoning.md`) | Hiding thrash behind final success |
| `Tool accuracy` | Schema validity plus semantic correctness of tool arguments/results | Strict schemas, approvals, validation loops (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`) | Treating parse success as task correctness |
| `Quality` | Groundedness, retrieval relevance, evidence sufficiency, citation or reference fidelity | RAG grader/rewrite loops, memory-layer diagnostics (`06-rag.md`, `07-memory.md`, `08-planning-reasoning.md`) | Confusing retrieval quality with answer quality |
| `Cost` | Input/output/reasoning/cache/tool/container spend per run | Usage telemetry and pricing formulas (`01-llm-foundations.md`, `03-tool-use.md`, `05-agent-frameworks.md`) | Ignoring fixed tool-surface and cache-write overhead |
| `Latency` | Critical-path runtime, ideally `p50/p95/p99` where locally measurable | Structural loop analysis, parallelism benchmarks (`04-agent-architecture.md`, `05-agent-frameworks.md`, `09-multi-agent-systems.md`) | Summing all branch time instead of critical path |

### 6.2 Recommended deployment patterns

**Pattern A: API-first operations agent**

Evaluate `task success` and `tool accuracy` jointly, because these workflows usually fail through incorrect external actions rather than poor prose. Strict schemas, approval checkpoints, and per-tool usage accounting are the most relevant controls in the local corpus (`03-tool-use.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`).

**Pattern B: Retrieval-heavy enterprise assistant**

Separate `retrieval quality` from `answer quality`, and keep references plus activity logs with the run. The RAG and planning notes show that decomposition, reranking, and rewrite loops improve auditability precisely because they make intermediate judgments explicit (`06-rag.md`, `08-planning-reasoning.md`).

**Pattern C: Multi-agent workflow**

Track both `task success` and `coordination efficiency`. Multi-agent notes show that specialization can improve quality or latency when subtasks parallelize, but also adds extra prompts, routing ambiguity, and governance surfaces (`09-multi-agent-systems.md`, `11-specialized-agents.md`). A flat success metric will miss whether the orchestration design itself is the problem [inferred].

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
trajectory_efficiency
  ~= successful_runs / total_tool_or_plan_steps
```

(`04-agent-architecture.md`, `08-planning-reasoning.md`) [inferred]

```text
tool_accuracy
  ~= schema_valid_calls
     x semantically_correct_calls
```

(`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`) [inferred]

```text
critical_path_latency
  ~= planning
   + max(parallel_branch_durations)
   + verification
   + synthesis
```

(`04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`) [inferred]

```text
true_run_value
  improves when quality gains
  exceed added cost, latency, and governance burden
```

(`03-tool-use.md`, `06-rag.md`, `09-multi-agent-systems.md`) [inferred]

### 6.4 Strongest practical conclusions

1. `Evaluation` in production agents is a **stack of metrics**, not a single score: task success, trajectory quality, tool correctness, answer quality, cost, and latency all fail differently.
2. The local corpus is strongest when evaluation is attached to **structured runtime artifacts** such as checkpoints, traces, usage entries, references, and activity logs rather than only final answers.
3. `Strict schemas` improve tool-call correctness, but they are only the floor for evaluation; semantic correctness, policy compliance, and side-effect safety still need separate checks.
4. The biggest local evidence gap is standardized, public, apples-to-apples `p50/p95/p99` evaluation suites for long-horizon agent runs across frameworks.

## Sources

- [1] `01-llm-foundations.md` - Local research note covering pricing formulas, reasoning-effort trade-offs, and BFCL evidence that long-horizon decision-making remains an open challenge.
- [2] `02-context-engineering.md` - Local research note covering grounding quality, long-context degradation, compression trade-offs, and the distinction between retrieval success and end-task success.
- [3] `03-tool-use.md` - Local research note covering strict schemas, tool costs, cache behavior, browser/computer overhead, programmatic tool-calling quality gains, and tool-loop failure modes.
- [4] `04-agent-architecture.md` - Local research note covering ReAct, planner/executor and DAG topologies, checkpoints, cost/latency formulas, and strict tool-validation patterns.
- [5] `05-agent-frameworks.md` - Local research note covering OpenAI/CrewAI/ADK/LangGraph usage metrics, traces, persistence, and framework-level observability surfaces.
- [6] `06-rag.md` - Local research note covering retrieval, reranking, agentic retrieval activity logs, grounding quality, and retrieval-cost trade-offs.
- [7] `07-memory.md` - Local research note covering trajectory memory, semantic cache risks, retrieval starvation, and memory-layer failure diagnostics.
- [8] `08-planning-reasoning.md` - Local research note covering verifier/rewrite loops, replanning storms, planning cost/latency structure, and the distinction between correctness and authorization.
- [9] `09-multi-agent-systems.md` - Local research note covering delegation overhead, critical-path latency, coordination quality, and group-level safety/evaluation caveats.
- [10] `10-mcp-interoperability.md` - Local research note covering approval/auth boundaries, protocol-level governance, and state-versus-capability separation for interoperable tool systems.
- [11] `11-specialized-agents.md` - Local research note covering specialist-specific cost shapes, tool-risk differences, and role-bounded orchestration trade-offs relevant to evaluation design.
