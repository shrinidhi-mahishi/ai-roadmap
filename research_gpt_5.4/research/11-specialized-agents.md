# Research: Specialized Agents - Coding, browser, research, data agents

**Date researched**: 2026-08-21
**Sources consulted**: 8

---

## 1. System Topology & Mechanics

`Specialized agents` in the local research set are best understood as **role-bounded orchestration patterns** rather than a separate framework category. The common pattern is: keep a coordinator or parent run in control, then give a worker agent a narrower tool surface, a smaller context slice, and a clearer completion contract (`05-agent-frameworks.md`, `09-multi-agent-systems.md`). That is why the current guidance consistently favors bounded specialists over one general agent with every tool attached (`09-multi-agent-systems.md`) [inferred].

For `coding agents`, the dominant topology is a **tool-centric execution loop**. The local notes show OpenAI-style agents mixing function tools, MCP servers, hosted shells, and code interpreters under one `tools` surface, while Anthropic separates client-executed tools from server-executed code execution (`03-tool-use.md`, `05-agent-frameworks.md`). In practice, coding specialists are usually "planner + code toolchain + validation" loops rather than free-form conversational workers (`03-tool-use.md`, `04-agent-architecture.md`) [inferred].

For `browser agents`, the topology is more explicitly **observe -> act -> observe**. OpenAI's computer-use flow is iterative and requires the caller to execute returned actions, capture a fresh screenshot, and continue until the model stops producing computer calls; Anthropic's browser/computer toolsets likewise operate through structured page or desktop actions emitted by the model and executed by the application (`03-tool-use.md`). That makes browser specialists more like remote-control loops over a visual environment than normal API agents.

For `research agents`, the strongest local pattern is **planner/retriever/verifier** rather than one-shot search. The RAG and planning notes describe agentic retrieval as LLM-based query decomposition plus parallel subqueries, while LangGraph-style agentic RAG adds explicit relevance grading and query rewrite nodes (`06-rag.md`, `08-planning-reasoning.md`). This means a research specialist is usually a controlled evidence-gathering loop with references and activity traces, not just a search API wrapper [inferred].

For `data agents`, the topology is usually **bounded computation over structured state**. The tool-use notes show server-side code execution and reusable containers as the compact execution path for file analysis, plotting, and transformations, while the memory and RAG notes show that durable facts, retrieval indexes, and query-time artifacts should stay outside the model transcript when possible (`03-tool-use.md`, `06-rag.md`, `07-memory.md`). The clean design is: let the agent decide what computation or retrieval to perform, but keep the actual data plane in sandboxes, indexes, or storage services [inferred].

Across all four specialist types, the architecture boundary is consistent: **specialization is created by narrowing tools, context, and authority**, not merely by changing the system prompt (`04-agent-architecture.md`, `09-multi-agent-systems.md`) [inferred].

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for apples-to-apples `p50/p95/p99` latency comparisons between coding, browser, research, and data specialists in the local research set. The local evidence is much stronger on structural cost drivers, cache behavior, published tool overhead, and workload-shape trade-offs.

The core economic rule is that specialization is only worth it when it reduces either `duplicated_context_tokens`, `critical_path_latency`, or `unsafe broad-tool exposure`; otherwise, extra specialists simply add orchestration turns (`09-multi-agent-systems.md`, `04-agent-architecture.md`) [inferred].

A useful first-order model is:

```text
specialized_agent_run_cost
  ~= coordinator_turns
   + specialist_turns
   + tool_surface_tokens
   + retrieved_or_generated_working_set
   + execution_surcharges
```

(`03-tool-use.md`, `06-rag.md`, `09-multi-agent-systems.md`) [inferred]

The local notes make `browser agents` the clearest high-overhead specialist type. Anthropic's browser toolset adds about `6,610-6,670` input tokens before the user task, screenshots, results, and output; the desktop computer toolset adds about `4,520-4,590` input tokens, with optional members changing that budget (`03-tool-use.md`). That is why browser specialists should usually be invoked only when the target system lacks a clean API (`03-tool-use.md`) [inferred].

`Coding agents` have a different cost shape. OpenAI documents that tool definitions count as input tokens, and hosted execution can add fixed runtime charges such as `$0.03` per 20-minute `1 GB` container with a 5-minute minimum; the local note also derives an approximate `1 GB` fresh execution floor of about `$7.50 / 1k` short-lived runs (`03-tool-use.md`). That makes container reuse, small tool surfaces, and stable cached prefixes major cost levers for code specialists (`03-tool-use.md`, `05-agent-frameworks.md`) [inferred].

`Research agents` often pay in `fan-out`. Azure's agentic retrieval example, summarized in the local notes, assumes `3` subqueries and `50` reranked chunks per subquery, producing `150M` reranking tokens across `2,000` retrievals (`06-rag.md`, `08-planning-reasoning.md`). The cost advantage of a research specialist therefore depends on whether decomposition improves answer quality enough to justify planning and reranking overhead [inferred].

`Data agents` usually become economical when they move repeated large-context reasoning into computation or indexed retrieval. The local notes show exact-prefix caching can become cheaper on first reuse for the common `1.25x` write / `0.1x` read structure, and ADK-style compaction plus artifact isolation prevents repetitive replay of bulky data payloads (`03-tool-use.md`, `05-agent-frameworks.md`, `07-memory.md`). In other words, a good data specialist turns "reason over raw transcript repeatedly" into "reuse cached prefix, artifact, or retrieval result" [inferred].

For throughput, the research set suggests four practical NFR heuristics:

- `Coding agents`: optimize around cache reuse, container reuse, and bounded validation loops (`03-tool-use.md`, `05-agent-frameworks.md`).
- `Browser agents`: assume the slowest critical path because action execution, screenshots, and visual re-grounding are inherently sequential (`03-tool-use.md`, `04-agent-architecture.md`) [inferred].
- `Research agents`: parallel subqueries can reduce end-to-end latency when the decomposition is high quality (`06-rag.md`, `08-planning-reasoning.md`).
- `Data agents`: throughput improves when the agent delegates heavy work to code execution, retrieval layers, or artifact stores instead of replaying raw inputs in every turn (`03-tool-use.md`, `07-memory.md`) [inferred].

## 3. Distributed Resilience & State

Specialized agents only stay reliable in production if their state boundary matches their role boundary. The local framework notes consistently separate `workflow/session state` from `tool/resource state`, and the interoperability note makes the same point directly: keep workflow state in sessions, checkpoints, or workflow history, and capability access in MCP or other tool protocols (`05-agent-frameworks.md`, `10-mcp-interoperability.md`).

For `coding agents`, the most relevant resilience primitives are checkpointed or resumable execution plus sandbox state reuse. LangGraph persists checkpoint state by super-step, including pending writes from successful sibling nodes, while OpenAI approval pauses return resumable run state; Anthropic code execution can also preserve interpreter state when the same container is reused (`03-tool-use.md`, `04-agent-architecture.md`, `05-agent-frameworks.md`). This is a workable durability story for "write, test, revise" loops, but it is weaker than a true external workflow engine for multi-hour jobs (`04-agent-architecture.md`) [inferred].

For `browser agents`, the resilience issue is not only persistence but **environment drift**. The execution loop depends on the current screenshot, page state, tab state, or desktop state, so retries can become invalid if the UI changed between observations (`03-tool-use.md`). That makes browser specialists naturally brittle under concurrent human interaction or long delays between steps [inferred].

For `research agents`, the strongest resilience pattern is explicit intermediate evidence. Azure-style agentic retrieval returns references and an activity log, while LangGraph-style research loops separate retrieval, grading, rewrite, and answer generation into distinct stages (`06-rag.md`, `08-planning-reasoning.md`). That makes it easier to resume or debug at the "bad retrieval plan" layer rather than only at the final answer layer [inferred].

For `data agents`, the local notes argue for separating `working memory`, `durable semantic memory`, `retrieval memory`, and `cache memory` (`07-memory.md`). That is especially important for data specialists because the same workflow may need ephemeral notebook state, durable user facts, and large external corpora at once. Collapsing all of that into one transcript produces both resilience and cost problems (`07-memory.md`) [inferred].

The cleanest production pattern is therefore:

- Use `sessions/checkpoints/run state` for specialist workflow continuity (`04-agent-architecture.md`, `05-agent-frameworks.md`).
- Use `containers`, `indexes`, or `knowledge bases` for specialist data-plane state (`03-tool-use.md`, `06-rag.md`, `07-memory.md`).
- Use interoperable protocols like `MCP` only for capability access, not as the sole durable state layer (`10-mcp-interoperability.md`) [inferred].

## 4. Enterprise Security & Governance

`Browser agents` carry the highest direct action risk in the local source set. OpenAI recommends isolated browsers or VMs, empty environment variables where possible, limited filesystem access, and human oversight for high-impact actions; Anthropic likewise recommends dedicated VMs or containers, minimal privileges, internet allowlists, and confirmation for sensitive actions such as transactions or terms acceptance (`03-tool-use.md`). That makes browser specialists the clearest case for default-deny execution plus approval gates [inferred].

`Coding agents` are safer when they operate through strict schemas, approvals, and sandboxed execution. The architecture and tool-use notes emphasize strict tool or function schemas, validation before side effects, and guardrail/approval planes around sensitive actions (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`). When a coding specialist can both reason and mutate systems, the governance requirement is not just "tool available" but "tool callable only through validated, reviewable paths" [inferred].

`Research agents` and `data agents` shift the security question from action execution to **trustworthy evidence and access control**. The RAG and memory notes describe Azure-style knowledge bases as permission-aware and accessible through either `retrieve` actions or `MCP`, with role- or key-based access controls (`06-rag.md`, `07-memory.md`). The interoperability note extends that into a Zero-Trust pattern: authorization, approvals, and policy enforcement should sit above the protocol boundary rather than being assumed because the protocol is standardized (`10-mcp-interoperability.md`).

The local corpus is also clear that specialization does not remove prompt-injection risk. Planning/verifier loops may consume tool outputs, retrieved passages, or browser content, and both the memory and planning notes warn that low-trust external content must not be promoted into high-trust instruction channels (`07-memory.md`, `08-planning-reasoning.md`). For research and data specialists, the most dangerous governance bug is often poisoned context rather than an obviously dangerous click [inferred].

`OpenAI` tracing and approval surfaces, `MCP` OAuth/PKCE/resource-binding requirements, and permission-aware retrieval together form the strongest governance stack described in the local notes (`03-tool-use.md`, `04-agent-architecture.md`, `10-mcp-interoperability.md`). But the same local notes repeatedly flag gaps in public documentation for built-in `PII redaction`, immutable audit-log schemas, and hard sandbox-isolation guarantees across frameworks (`03-tool-use.md`, `05-agent-frameworks.md`, `10-mcp-interoperability.md`).

## 5. Production Failure Modes

### Wrong specialization choice

The local notes repeatedly imply that specialization has overhead, so the first failure mode is using a specialist where a simpler pattern would do. Multi-agent decomposition adds extra prompts, traces, and approval surfaces, and browser or retrieval specialists add large tool or planning overheads (`03-tool-use.md`, `09-multi-agent-systems.md`). A browser agent for an API-available workflow or a research agent for a single lookup is usually an architecture mistake [inferred].

### Coding-agent replay and unsafe mutation

Checkpointed or resumable systems can still replay non-idempotent code paths. The architecture notes explicitly warn that resumed nodes may re-run from checkpoint boundaries, and the tool-use notes point to approvals and traces rather than exactly-once guarantees (`03-tool-use.md`, `04-agent-architecture.md`). Without idempotent writes and validation layers, a coding specialist can duplicate edits, rerun commands, or reissue external side effects [inferred].

### Browser-agent prompt injection and observation drift

The tool-use notes treat page content and screenshots as untrusted input and call prompt injection a first-class failure mode for visual agents (`03-tool-use.md`). Browser specialists can also fail because the visible page state changes between screenshot, action planning, and execution, so even correct actions become stale by the time they run [inferred].

### Research-agent replanning storms and retrieval starvation

The planning and RAG notes describe two complementary failure modes: verifier/rewrite loops can thrash by repeatedly rewriting or decomposing without improving the answer, and rerankers cannot recover evidence that first-stage retrieval never surfaced (`06-rag.md`, `08-planning-reasoning.md`). A research specialist therefore fails either by overthinking or by confidently grounding on an underfilled candidate set [inferred].

### Data-agent memory poisoning and cost blow-up

The memory note highlights semantic-memory poisoning, exact-prefix cache thrash, and graph-memory economic blow-up as distinct risks (`07-memory.md`). For data specialists, those translate into three operational bugs: bad facts become reusable memory, cache instability erases the expected savings, and overly heavy indexing or computation makes the workflow economically non-viable [inferred].

### Timeout and partial-failure cascades

Once specialists depend on remote tools, MCP servers, or delegated agents, the local interoperability and multi-agent notes recommend treating each boundary as a bulkhead with its own timeout and fallback (`09-multi-agent-systems.md`, `10-mcp-interoperability.md`). Specialized agents are not just prompts with job titles; they are distributed systems components with independent failure surfaces [inferred].

### Incident coverage

> ⚠️ Limited public data available for RCA-style incident reports focused specifically on coding agents, browser agents, research agents, or data agents in the local research set. Most evidence is architectural guidance, not detailed outage analysis.

## 6. Enterprise System Design Scenarios

### 6.1 Specialist selection matrix

| Specialist type | Best fit | Strongest documented strengths | Main trade-offs |
| --- | --- | --- | --- |
| `Coding agent` | Repositories, shells, code transformation, tests, bounded execution | Structured tools, sandboxed code execution, resumable approvals, reusable containers (`03-tool-use.md`, `05-agent-frameworks.md`) | Non-idempotent replay risk, tool-schema sprawl, mutation safety burden |
| `Browser agent` | Web UIs without good APIs, page navigation, visual verification | Structured browser/computer loops, page-aware actions, stronger fit than desktop-only automation when DOM-level controls exist (`03-tool-use.md`) | Highest prompt-injection risk, high token overhead, sequential critical path |
| `Research agent` | Citation-heavy synthesis, multi-source lookup, decomposable information tasks | Parallel subqueries, verifier/rewrite loops, references and activity logs (`06-rag.md`, `08-planning-reasoning.md`) | Planning overhead, reranker dependence on first-stage recall, rewrite thrash |
| `Data agent` | Analysis, ETL-like reasoning, file processing, corpus-backed answers | Code execution, caching/compaction levers, retrieval-memory separation, artifact-based context reduction (`03-tool-use.md`, `07-memory.md`) | Memory poisoning risk, storage/index complexity, weak public benchmark comparability |

### 6.2 Recommended deployment patterns

**Pattern A: Product engineering copilot**

Use a `coding agent` for repository mutation and bounded shell/code execution, but keep approvals and final ownership in a supervising runtime (`05-agent-frameworks.md`, `09-multi-agent-systems.md`). Add `browser` capability only for flows that truly require UI verification or API-less configuration [inferred].

**Pattern B: GTM or operations workflow across SaaS apps**

Prefer a `browser agent` only when the underlying SaaS lacks reliable APIs or when visual confirmation is itself the task (`03-tool-use.md`). If stable APIs exist, the local tool-use note argues that structured function or MCP access is lower-friction and lower-risk than screen-driven automation (`03-tool-use.md`) [inferred].

**Pattern C: Executive research assistant**

Use a `research agent` with retrieval decomposition, evidence verification, and activity traces when the task spans multiple documents or subquestions (`06-rag.md`, `08-planning-reasoning.md`). Combine it with permission-aware retrieval if the corpus is internal or multi-tenant (`06-rag.md`, `07-memory.md`).

**Pattern D: Analyst or finance ops assistant**

Use a `data agent` when the job is mostly transformation, calculation, aggregation, or grounded retrieval over structured corpora (`03-tool-use.md`, `07-memory.md`). The key design rule is to keep bulky artifacts and datasets outside the prompt and load them only when needed [inferred].

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
browser_agent_cost_floor
  ~= browser_or_computer_tool_overhead
   + screenshot/observation turns
   + output tokens
```

(`03-tool-use.md`) [inferred]

```text
research_agent_token_load
  ~= subqueries * reranked_candidates * avg_candidate_tokens
```

(`06-rag.md`, `08-planning-reasoning.md`) [inferred]

```text
data_agent_efficiency
  improves when large inputs move into
  caches, artifacts, indexes, or containers
  instead of staying in the transcript
```

(`03-tool-use.md`, `05-agent-frameworks.md`, `07-memory.md`) [inferred]

### 6.4 Strongest practical conclusions

1. `Specialized agents` are most useful when they narrow `tools`, `context`, and `authority`, not when they simply restyle the same general-purpose loop.
2. `Browser agents` are the most operationally expensive and security-sensitive specialist type in the local source set.
3. `Research agents` and `data agents` win primarily through better control planes around retrieval, verification, computation, and memory rather than through raw model capability alone.
4. The cleanest enterprise design keeps governance and durable workflow state above the specialist, while keeping execution sandboxes, knowledge bases, and tool protocols below it (`05-agent-frameworks.md`, `10-mcp-interoperability.md`) [inferred].

## Sources

- [1] `03-tool-use.md` - Local research note covering function tools, browser/computer use loops, code execution, prompt caching, pricing, rate limits, and tool-related failure modes.
- [2] `04-agent-architecture.md` - Local research note covering ReAct and planner/executor control loops, checkpoint/replay semantics, strict schemas, and control-plane/data-plane separation.
- [3] `05-agent-frameworks.md` - Local research note comparing LangGraph, OpenAI Agents SDK, Google ADK, and CrewAI on sessions, approvals, usage accounting, persistence, and interoperability.
- [4] `06-rag.md` - Local research note covering hybrid retrieval, agentic retrieval, query decomposition, reranking, references, and activity logs.
- [5] `07-memory.md` - Local research note covering working/semantic/retrieval/cache memory splits, poisoning risks, compaction, and durable memory design.
- [6] `08-planning-reasoning.md` - Local research note covering verifier/rewrite loops, planning overhead, decomposition trade-offs, and governed execution.
- [7] `09-multi-agent-systems.md` - Local research note covering supervisor-specialist delegation, narrow specialist routing, timeout surfaces, and multi-agent overhead.
- [8] `10-mcp-interoperability.md` - Local research note covering MCP boundaries, Zero-Trust authorization, workflow-state separation, and remote-failure bulkheads.
