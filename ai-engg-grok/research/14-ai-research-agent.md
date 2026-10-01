# Research: How an AI Research Agent Works

**Date researched**: 2026-09-30
**Sources consulted**: 16
**Scope note**: Research-agent *workload* — search → read → cite → synthesize — and how MCP `search`/`fetch` tools plug into that loop. MCP protocol internals and general multi-agent platform design are covered in earlier topics; this file treats orchestrator-worker / CitationAgent only as they appear in the research product path.

> ⚠️ Primary article ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) is partially paywalled. Free/teaser sections plus Anthropic / OpenAI primary docs and citation-eval papers supply the quantitative backbone below. Paywalled sections on full MCP server code and binary-judge implementation are noted where inferred.

## 1. System Topology & Mechanics

### Why research needs an agent (not one-shot RAG)

Static RAG fetches a fixed chunk set for a query and generates once. Research is path-dependent: intermediate findings change the next query ([Anthropic — Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)). A passive model cannot decide mid-answer that it needs more information and go get it; an agent decides the next action, calls a tool, observes the result, and continues until it has enough ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

Anthropic’s definition (restated in the newsletter): an agent is an LLM that uses tools in a loop, autonomously, until the task is done. Remove tools → chat assistant; remove the loop → one-shot function call; remove the model → workflow ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Research pipeline shape

Canonical build (newsletter design target):

| Stage | Role |
| --- | --- |
| Decompose | Lead breaks user question into focused sub-questions |
| Parallel worker loops | Each worker runs think–act–observe over MCP `search` / `fetch` |
| Synthesize | Lead (or writer) merges findings into a structured report |
| Review | In-run reviewer checks claim↔citation coupling |
| Judge | Separate post-report evaluation (binary / rubric) |

Output design targets (explicitly *not* measured from a specific run): **600–1,200** words, **2–3** sections, **8–15** inline citations, confidence per section; **10–20** tool calls; **2–4** minutes wall-clock, almost all spent reading fetched pages ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

### Think–act–observe loop

Each turn ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)):

1. **Think** — model emits a tool call or a final answer.
2. **Act** — harness executes the chosen tool (MCP or native).
3. **Observe** — tool result is appended to context for the next turn.

Typical single-agent research run: **10–20** turns before the model stops. Loop patterns used on top of the same cycle: ReAct (interleaved reasoning + tools), Plan-and-Execute (plan then execute; replan on failure), Reflection/Reflexion (critic pass when quality plateaus) ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

Three stop conditions (first trip wins):

| Guard | Behavior |
| --- | --- |
| Completion signal | Final answer with no tool call |
| Iteration cap | Newsletter build: **10** turns hard stop; general research guidance: **15–25** turns |
| Budget cap | Max tokens / dollars → partial result + failure flag |

Without a hard stop, one stuck task can burn on the order of **$50** of tokens ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

### Anthropic production Research topology

Orchestrator-worker + dedicated citation pass ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)):

1. **LeadResearcher** plans approach; saves plan to Memory (context can exceed **200,000** tokens and truncate).
2. Spawns **Subagents** with isolated context windows; each iteratively searches, uses interleaved thinking on tool results, returns condensed findings.
3. Lead synthesizes; may spawn more subagents or refine strategy.
4. **CitationAgent** (separate pass) walks documents + report and attaches claim-level citations.
5. Cited research returned to user.

Fan-out heuristics embedded in prompts ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)):

| Query class | Subagents | Tool calls |
| --- | --- | --- |
| Simple fact-finding | **1** | **3–10** |
| Direct comparisons | **2–4** | **10–15** each |
| Complex research | **>10** with clear division of labor | Divided responsibilities |

Parallelism that cut research wall-clock by **up to 90%**: lead spins **3–5** subagents in parallel; each subagent uses **3+** tools in parallel ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Lead execution of subagents is **synchronous** today (waits for each wave) — simplifies coordination but blocks mid-wave steering ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

Subagents should write large artifacts to a filesystem / external store and return lightweight references — avoids “game of telephone” through the lead’s context ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### MCP tools in the research loop

Research agents do not need a large tool surface. The industry-standard deep-research connector contract is two read-only MCP tools ([OpenAI — Building MCP servers](https://developers.openai.com/api/docs/mcp); [OpenAI search/fetch standard](https://github.com/openai/skills/blob/82d2c5b4/skills/.curated/chatgpt-apps/references/search-fetch-standard.md)):

| Tool | Input | Output (structured) | Role in loop |
| --- | --- | --- | --- |
| `search` | `query: string` | `{ results: [{ id, title, url }] }` | Discover candidate sources |
| `fetch` | `id: string` | `{ id, title, text, url, metadata? }` | Read full document for synthesis + citation |

ChatGPT creates citation metadata only when `url` is a non-empty string; title-only results stay ordinary tool output ([OpenAI MCP docs](https://developers.openai.com/api/docs/mcp)). Deep Research connectors (Team/Enterprise/Pro) use the same `search`/`fetch` pair against internal systems (SQL, contract repos, etc.) alongside web search ([OpenAI for Business — custom deep research connectors](https://www.linkedin.com/posts/openai-for-business_yesterday-we-launched-custom-deep-research-activity-7336428401160790016-t6GC)).

Newsletter practical build: MCP server exposing `search` + `fetch`, native function-calling agent loop, then deep-research flow that decomposes → fans out → synthesizes → reviews ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). Tool-count heuristic: quality drops once the model sees roughly **>20** tools ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

**Control vs data plane** [inferred]:

| Plane | Research-agent contents |
| --- | --- |
| Control | Iteration/budget caps, decompose/fan-out policy, reviewer/judge gates, MCP tool allowlist |
| Data | Per-worker contexts, fetched page text, Memory plan, citation graph / report artifacts, trajectory log |

## 2. Token Economics & NFR Metrics

### Published multipliers (Anthropic)

| Metric | Value | Source |
| --- | --- | --- |
| Multi-agent vs single Opus 4 (internal research eval) | **+90.2%** relative performance | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Agents vs chat token use | **~4×** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Multi-agent vs chat token use | **~15×** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| BrowseComp variance from token usage alone | **80%** of performance variance | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Token usage + tool calls + model choice | **95%** of BrowseComp variance | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Parallelization wall-clock cut (complex queries) | **up to 90%** | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Tool-description rewrite via testing agent | **40%** decrease in task completion time | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |

Implication Anthropic states explicitly: multi-agent systems work mainly because they help **spend enough tokens** to solve the problem — subagents add parallel context windows that compress insights back to the lead ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Economic viability requires task value high enough to absorb the **15×** chat baseline.

### Coordinator / worker rate split (Claude cookbook)

Web research is the extreme case: verifying many facts means pulling **hundreds of thousands** of tokens of page text through a model; at frontier rates the reading bill dominates ([Claude Cookbook — Coordinator pattern](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)). Pattern: frontier coordinator plans/synthesizes and **never** touches raw pages; cheap workers hold `web_search` + `web_fetch` only. On authors’ runs, **84–98%** of team input tokens billed at the worker rate ([Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

### Newsletter design targets (not production SLAs)

| NFR | Design target |
| --- | --- |
| Wall-clock | **2–4** minutes / research report |
| Tool calls | **10–20** (single deep-research style build) |
| Report size | **600–1,200** words, **8–15** citations |
| Loop cap (build) | **10** turns |
| Stuck-task cost (illustrative) | **~$50** without hard stop |

([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp))

### Cost formula [inferred from published multipliers]

For a chat baseline of \(C\) tokens (or dollars) per interaction:

\[
C_{\text{research-agent}} \approx 4C,\quad C_{\text{multi-agent-research}} \approx 15C
\]

Dollar absolute costs depend on model tier and page-fetch volume; Anthropic does not publish a fixed \$/query for Research. Coordinator pattern further approximates:

\[
\text{Cost} \approx C_{\text{lead-plan+synth}} + \sum_i C_{\text{worker}_i}(\text{fetched pages})
\]

with most spend in \(\sum_i C_{\text{worker}_i}\) ([Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

### Caching / routing

- **Prompt / tool-description quality** is a first-order cost lever: rewriting MCP tool descriptions after adversarial self-testing cut completion time **40%** ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- **Model routing**: Opus (or frontier) lead + Sonnet (or cheaper) workers is the documented pattern; upgrading the worker model can beat doubling the token budget on an older model ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Semantic caching of search/fetch results is common in open MCP research servers (e.g. TTL caches on fetch) but **no vendor-published hit-rate** for Claude Research or ChatGPT Deep Research was found.

> ⚠️ Limited public data available for this dimension on **p50/p95/p99 latency SLAs**, **RPM/TPM product limits** specific to Research/Deep Research, and **prompt-cache hit rates** for research workloads. Use Anthropic token multipliers + newsletter design targets; do not fabricate percentile latencies.

## 3. Distributed Resilience & State

### Durable state for long research runs

| Mechanism | Behavior | Source |
| --- | --- | --- |
| Memory plan persistence | Lead saves plan before context truncation at **200k** tokens | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Phase summarization | Summarize completed phases; store essentials externally; spawn fresh subagents with clean contexts | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Artifact store | Subagents persist large outputs; return refs to lead | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Trajectory log | Every model call, tool call, observation, stop reason | [System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp) |
| Resume-on-error | Resume from checkpoint rather than restart; combine Claude adaptability with retries + regular checkpoints | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |
| Rainbow deployments | Old + new versions run concurrently so in-flight agents are not broken mid-run | [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) |

### Failure handling in the tool loop

- Surface tool failures to the model and let it adapt (works surprisingly well) plus deterministic retries ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Synchronous subagent waves: system blocked until the slowest worker finishes — async fan-out flagged as future work with harder state/error propagation ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Session / run budget as fan-out guardrail in Managed Agents coordinator pattern ([Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

### Fetch-layer resilience [inferred + open-source practice]

Production `fetch` for agents should enforce: HTTP(S)-only, private-IP / metadata blocklists, DNS pin + revalidate every redirect, body size + timeout caps, rate limits ([agent-fetch](https://github.com/Parassharmaa/agent-fetch/); [LangChain deepagents fetch](https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/tools.py)). BrowseComp-style eval harnesses truncate fetched content (e.g. **8,000** chars) to bound context ([BrowseComp env](https://github.com/EnvCommons/BrowseComp)).

> ⚠️ Limited public data available for this dimension on Anthropic/OpenAI **internal** Temporal/Kafka/event-sourcing choices, distributed locks, or circuit-breaker thresholds for Research. Public material covers Memory checkpoints, rainbow deploys, trajectory logs, and sync-wave bottlenecks — not a published breaker tuning guide.

## 4. Enterprise Security & Governance

### Least-privilege tool surface

Research agents are safest when workers can **only** search, fetch, and report. Claude Cookbook scopes workers to `web_search` + `web_fetch` with other tools disabled — workers read untrusted web pages; coordinator has **no** tools and only reads distilled reports ([Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)). Newsletter rationale for building your own: expose only tools you allow; a tool that does not exist cannot be called ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

OpenAI deep-research MCP compatibility: `search`/`fetch` are **read-only**; approval can be skipped for those tools while write/consequential tools keep approval ([OpenAI MCP docs](https://developers.openai.com/api/docs/mcp)).

### MCP connector trust boundary

Custom Deep Research connectors attach internal knowledge (SQL backends, contract stores) via remote MCP; admins enable connectors per research session ([OpenAI for Business](https://www.linkedin.com/posts/openai-for-business_yesterday-we-launched-custom-deep-research-activity-7336428401160790016-t6GC)). [inferred] Enterprise deployments should treat connector auth, network egress, and tool RBAC as the primary trust boundary — not the model prompt.

### SSRF and fetch sandboxing

Unrestricted agent HTTP is a classic SSRF vector (loopback, RFC1918, cloud metadata, DNS rebinding). Hardening pattern: resolve → validate public IPs → pin connection → revalidate redirects → disable env proxies ([agent-fetch](https://github.com/Parassharmaa/agent-fetch/); [deepagents tools](https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/tools.py)).

### Audit & evaluation trail

| Artifact | Purpose |
| --- | --- |
| Trajectory log (owned runtime) | Replay model decisions + tool I/O for regulated industries ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| High-level decision tracing (vendor) | Diagnose “not finding obvious information” without logging conversation contents ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Citation pass + judge | Separate attribution verification from synthesis |

### PII / content governance

> ⚠️ Limited public data available for this dimension on **research-product-specific** PII redaction pipelines (regex/NER schemas) or SOC2/HIPAA mappings for Claude Research / ChatGPT Deep Research. General enterprise expectations [inferred]: redact before logging trajectories; treat fetched pages as untrusted; scope connectors to least privilege.

## 5. Production Failure Modes

### Termination and over-exploration

Most agent failures are **termination** failures — endless search/refine without deciding “enough” ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). Early Anthropic agents: spawned **50** subagents for simple queries, scoured endlessly for nonexistent sources, distracted each other with excessive updates ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Mitigations: effort-scaling rules in prompts, iteration + budget caps, clear subagent task boundaries.

### Coordination / duplication

Vague lead instructions (“research the semiconductor shortage”) caused duplicate searches and gaps (one subagent on 2021 automotive chips, two others on 2025 supply chains) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Fix: each subagent brief must include objective, output format, tool/source guidance, and task boundaries.

### Source-quality bias

Human testers found early agents preferred SEO content farms over authoritative PDFs/blogs; fixed with source-quality heuristics in prompts ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Search strategy: start wide/short queries, then narrow ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Citation / grounding failures (the research-specific hard problem)

Separate CitationAgent exists because synthesis alone does not guarantee attribution ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Empirical citation quality on deep-research Markdown reports ([Onweller et al., “Cited but Not Verified”](https://arxiv.org/html/2605.06635)):

| Dimension | Frontier range | Meaning |
| --- | --- | --- |
| Link Works | **>94%** (most models) | URL resolves |
| Relevant Content | **>80%** (frontier) | Source is on-topic |
| Fact Check | **39–77%** | Claim actually supported by source |
| Citation hallucination (commercial, cited prior) | **11–57%** | Fabricated / wrong refs |

Ablation: Fact Check accuracy drops **~42%** on average as tool calls scale from **2 → 150**, while Link Works / Relevant Content stay stable — more retrieval can **hurt** factual synthesis via information overload ([Onweller et al.](https://arxiv.org/html/2605.06635)). Example model points: Claude Opus 4.5 Fact Check **76.8%** vs GPT-5 Mini **38.9%** at high link validity ([Onweller et al.](https://arxiv.org/html/2605.06635)).

Agentic deep-research error localization: searcher snippets can be far more reliable (~**3.8%** mistakes) than researcher notes consolidating many docs (~**70.8%** mistakes) — prefer feeding orchestrators single-document extracts over multi-doc notes ([“Who is the Agent to Blame?”](https://arxiv.org/pdf/2608.24306)).

### Context overflow & telephone game

Long runs truncate past **200k** tokens; without Memory, the plan is lost ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Passing full subagent outputs through the lead loses fidelity — use artifact refs ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Bad tool descriptions / hallucinated tool use

MCP compounds tool-selection risk: agents meet unseen tools with uneven descriptions ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)). Hallucinated parameters [inferred]: enforce JSON schemas on `search`/`fetch` I/O; return recoverable errors so the model retries with corrected IDs/queries (newsletter tool-design theme; OpenAI structuredContent schemas).

### Cascading timeouts [inferred]

Sync subagent waves + multi-second web RTTs → wall-clock dominated by slowest fetch. Mitigate with per-fetch timeouts, truncated page text, parallel tool calls inside workers, and run-level budgets ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [BrowseComp](https://github.com/EnvCommons/BrowseComp)).

### Efficiency failure (correct but wasteful)

Newsletter evaluation warning: scoring only the final answer misses the failure mode where the agent is right in **50** tool calls when **5** would suffice ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)). Anthropic LLM-as-judge rubric includes **tool efficiency** alongside factual accuracy, citation accuracy, completeness, and source quality ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

## 6. Enterprise System Design Scenarios

### When to build vs buy

| Choice | Prefer when |
| --- | --- |
| Hosted (ChatGPT Deep Research, Perplexity, Claude Research) | Generic web research; no audit/cost ownership; public data ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| Own harness + MCP tools | Need CRM/warehouse/PubMed-first ranking, custom stop rules, owned trajectory logs, citation gates ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)) |
| Multi-agent (lead + workers + CitationAgent) | Breadth-first, high-value, parallelizable research; info exceeds one context ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |
| Single agent / workflow | Known steps; high inter-step dependency; coding-like tasks ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)) |

### Reference architectures

**A. Minimal MCP research agent** (newsletter): one loop + MCP `search`/`fetch` + citation-required report + reviewer + offline judge ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).

**B. Anthropic Research product**: LeadResearcher → parallel Subagents → CitationAgent; Memory for plan; sync waves; rainbow deploy ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

**C. Coordinator economics**: frontier planner + tool-scoped cheap readers; meter per-thread cost; session budget as fan-out guard ([Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small)).

**D. Enterprise knowledge Deep Research**: remote MCP `search`/`fetch` over internal stores + web; read-only connector; admin-enabled per session ([OpenAI MCP](https://developers.openai.com/api/docs/mcp); [OpenAI for Business](https://www.linkedin.com/posts/openai-for-business_yesterday-we-launched-custom-deep-research-activity-7336428401160790016-t6GC)).

### Trade-off matrix

| Approach | Cost | Latency | Citation rigor | Ops complexity | Best fit |
| --- | --- | --- | --- | --- | --- |
| One-shot RAG | Low | Low | Weak (static chunks) | Low | FAQ / known corpus |
| Single ReAct + MCP search/fetch | ~**4×** chat | Medium (serial) | Prompt-dependent | Medium | Narrow research |
| Orchestrator + parallel workers + CitationAgent | ~**15×** chat | Lower wall-clock via parallelism (**≤90%** cut) | Highest (separate pass) | High | Breadth-first enterprise research |
| Hosted Deep Research | Vendor-priced | Vendor SLA (undisclosed) | Vendor pipeline | Low | Generic / internal via connector |

### Capacity planning [inferred from published numbers]

- Budget tokens at **15×** chat for multi-agent research; **4×** for single research agent ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Cap concurrent subagents (prompt guidance **3–5** per wave; avoid **50**-spawn failure mode) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
- Cap tool calls: simple **3–10**, comparison **10–15**/worker; watch Fact Check degradation past very deep trajectories (**~150** calls) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [Onweller et al.](https://arxiv.org/html/2605.06635)).
- Wall-clock planning: newsletter **2–4** min design target for scoped reports; complex Anthropic queries measured in “minutes instead of hours” after parallelization ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp); [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

### Evaluation stack for production research agents

1. Start with ~**20** real usage queries (large effect sizes early) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
2. LLM-as-judge rubric: factual accuracy, citation accuracy, completeness, source quality, tool efficiency; single 0–1 score + pass/fail aligned best with humans ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).
3. Citation closed-loop: AST-extract claim↔URL → Link Works / Relevant Content / Fact Check ([Onweller et al.](https://arxiv.org/html/2605.06635)).
4. Trajectory efficiency: penalize correct-but-wasteful paths ([System Design Newsletter #168](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)).
5. BrowseComp-class browsing evals for hard multi-hop findability (token spend dominates variance) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system); [BrowseComp](https://github.com/EnvCommons/BrowseComp)).

Anthropic Clio usage plot (Research feature): top categories include specialized software systems (**10%**), professional content (**8%**), business growth strategies (**8%**), academic/educational material (**7%**), people/places/orgs verification (**5%**) ([Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)).

## Sources

- [1] https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp — System Design Newsletter #168: build AI research agent with MCP (partially paywalled; loop, design targets, search/fetch build)
- [2] https://www.anthropic.com/engineering/multi-agent-research-system — Anthropic engineering: multi-agent Research system (architecture, 90.2%, 15× tokens, CitationAgent)
- [3] https://developers.openai.com/api/docs/mcp — OpenAI: MCP `search`/`fetch` schemas for deep research / company knowledge
- [4] https://github.com/openai/skills/blob/82d2c5b4/skills/.curated/chatgpt-apps/references/search-fetch-standard.md — OpenAI search-and-fetch standard for connectors
- [5] https://platform.claude.com/cookbook/managed-agents-cma-plan-big-execute-small — Claude Cookbook: coordinator vs cheap web workers (84–98% tokens at worker rate)
- [6] https://arxiv.org/html/2605.06635 — Onweller et al.: citation Link Works / Relevant / Fact Check evaluation for deep research agents
- [7] https://arxiv.org/pdf/2608.24306 — Localizing faithfulness and citation mistakes across deep-research agent roles
- [8] https://www.linkedin.com/posts/openai-for-business_yesterday-we-launched-custom-deep-research-activity-7336428401160790016-t6GC — OpenAI for Business: custom Deep Research connectors via MCP
- [9] https://github.com/EnvCommons/BrowseComp — BrowseComp browsing/research eval environment (search/fetch tools, multi-hop)
- [10] https://github.com/Parassharmaa/agent-fetch/ — SSRF-hardened HTTP fetch for agents
- [11] https://github.com/langchain-ai/deepagents/blob/d60560d6/libs/code/deepagents_code/tools.py — DeepAgents pinned-DNS fetch with redirect revalidation
- [12] https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration — Claude Managed Agents multiagent orchestration (research-relevant coordinator APIs)
- [13] https://developers.openai.com/cookbook/examples/deep_research_api/how_to_build_a_deep_research_mcp_server/readme — OpenAI cookbook: Deep Research–style MCP search/fetch server
- [14] https://arxiv.org/html/2604.01432v3 — Citation granularity study (paragraph-level attribution peaks)
- [15] https://github.com/gabrimatic/mcp-web-search-tool — Example MCP web_search + fetch_url with citations
- [16] https://pypi.org/project/mcp-research/ — mcp-research: compound search → parallel fetch → cited synthesize pipeline
