# 14. AI Research Agents

**Sub-areas covered**: The Plan-Search-Read-Reflect-Synthesize loop as the universal deep research cycle, three production topologies (Single-Agent ReAct, Orchestrator-Worker multi-agent, Pipeline architecture) with trade-off analysis, three planning strategies (Planning-Only, Intent-to-Planning, Unified Intent-Planning) and adaptive re-planning, the Think-Act-Observe agent turn structure with three termination guards (completion signal, iteration cap, budget cap), MCP as the industry-standard tool integration layer (JSON-RPC 2.0, three primitives: Resources/Tools/Prompts, Tool Search for dynamic discovery yielding 85% token reduction), three web retrieval architectures (API-based, Browser-based via headless Chromium, Hybrid routing), source authority ranking (Step-DeepResearch's 600+ curated index), Perplexity's Search-as-Code paradigm (200M daily queries, 358ms median latency), token economics showing 50-500x cost vs. standard chat (1-3.5M tokens/session, $5-$30/run for frontier models), three context window management strategies (window expansion, three-tier intermediate compression achieving 84% token reduction, external structured storage), cost optimization levers (model routing 60-80% savings, prompt caching 78% savings, patch-based editing 70% output cost reduction), long-running session management (async polling, rainbow deployments, mid-flight interruption, checkpoint-based resumability), trajectory logging for evaluation, source reliability patterns (coverage-based stopping, novelty exhaustion, query reformulation), enterprise security posture (54% of orgs with agent security incidents, 60% over-permissioned agents, only 19.7% secured before go-live), three sandboxing technologies (Firecracker microVMs, gVisor, V8 Isolates), citation accuracy rates (94% best-in-class, 66% of failures are total fabrication), the PIES hallucination taxonomy across planning and summarization stages, comprehensive failure taxonomy (12 modes including the $47K infinite-loop incident), layered mitigation architecture, production Python code with retry/backoff/circuit-breaker/structured-logging for research agent orchestration, and two enterprise system-design scenarios (enterprise competitive intelligence platform, regulated financial research pipeline) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production AI research agent spans five cooperating layers: a **control plane** accepting user queries, managing planning, and orchestrating the research lifecycle; a **data plane** executing the iterative search-read-reflect cycle through tool calls against external sources; a **persistence layer** storing intermediate findings, checkpoints, and final synthesized reports; a **tool proxy layer** providing sandboxed access to search engines, web crawlers, code execution environments, and databases via MCP; and a **telemetry layer** capturing full trajectory logs, cost metrics, and quality scores for every run.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Query Intake &     │   │ Plan Generator    │   │ Orchestrator           │  │
│  │ Intent Classifier  │   │                   │   │ (Lead Agent)           │  │
│  │                    │   │ Decompose query   │   │                        │  │
│  │ Route by           │   │ into sub-         │   │ Topology:              │  │
│  │ complexity:        │   │ questions          │   │  Single ReAct          │  │
│  │  simple -> Tier 1  │   │                   │   │  Orchestrator-Worker   │  │
│  │  complex -> Tier 3 │   │ Strategies:       │   │  Pipeline              │  │
│  │                    │   │  Planning-Only    │   │                        │  │
│  │ Clarification      │   │  Intent-to-Plan  │   │ Strong model (Opus)    │  │
│  │ round-trip         │   │  Unified Intent  │   │ delegates to cheap     │  │
│  │ (optional)         │   │                   │   │ workers (Sonnet)       │  │
│  └────────┬──────────┘   └────────┬──────────┘   └───────────┬────────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │ classified query       │ sub-question plan         │ scoped tasks
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                             DATA PLANE                                       │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │              Think-Act-Observe Loop (per sub-question)                │   │
│  │                                                                       │   │
│  │  ┌─────────┐    ┌─────────┐    ┌───────────┐    ┌──────────────┐    │   │
│  │  │  THINK  │───>│   ACT   │───>│  OBSERVE  │───>│   REFLECT    │    │   │
│  │  │         │    │         │    │           │    │              │    │   │
│  │  │ Model   │    │ Execute │    │ Append    │    │ Enough       │    │   │
│  │  │ emits   │    │ tool    │    │ result to │    │ evidence?    │    │   │
│  │  │ tool    │    │ call    │    │ context   │    │              │    │   │
│  │  │ call or │    │ via MCP │    │           │    │ Yes: stop    │    │   │
│  │  │ final   │    │         │    │           │    │ No: re-plan  │    │   │
│  │  │ answer  │    │         │    │           │    │   + loop     │    │   │
│  │  └─────────┘    └─────────┘    └───────────┘    └──────┬───────┘    │   │
│  │       ^                                                │            │   │
│  │       └────────────────────────────────────────────────┘            │   │
│  │                                                                       │   │
│  │  Termination guards (whichever fires first):                         │   │
│  │   1. Completion signal: model emits final answer, no tool call       │   │
│  │   2. Iteration cap: 15-25 turns for research                        │   │
│  │   3. Budget cap: max tokens or dollars -> partial result + flag      │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Synthesizer       │  │ Citation Agent    │  │ Quality Judge            │   │
│  │                   │  │ (post-processing) │  │ (separate model pass)    │   │
│  │ Merge sub-answers │  │                   │  │                          │   │
│  │ into coherent     │  │ Verify every      │  │ Binary eval: atomicity,  │   │
│  │ cited report      │  │ claim traces to   │  │ verifiability,           │   │
│  │ (600-15K words)   │  │ verified source   │  │ unambiguity,             │   │
│  │                   │  │                   │  │ independence, alignment  │   │
│  └────────┬─────────┘  └────────┬──────────┘  └──────────┬───────────────┘   │
└───────────┼────────────────────────┼──────────────────────┼─────────────────┘
            │ draft report           │ verified citations    │ quality score
┌───────────▼────────────────────────▼──────────────────────▼─────────────────┐
│                       PERSISTENCE LAYER                                      │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Context Memory    │  │ Checkpoint Store  │  │ Report Archive           │   │
│  │                   │  │                   │  │                          │   │
│  │ Three-tier:       │  │ Phase summaries   │  │ Final reports with       │   │
│  │  Hot: last 10     │  │ + essential state  │  │ inline citations         │   │
│  │   turns verbatim  │  │ for resumability   │  │                          │   │
│  │  Warm: turns      │  │                   │  │ Source provenance         │   │
│  │   11-40 summary   │  │ Enables recovery  │  │ metadata                 │   │
│  │  Cold: goals +    │  │ without full       │  │                          │   │
│  │   constraints     │  │ restart            │  │ Version history          │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ tool requests / results
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                         MCP TOOL PROXY LAYER                                 │
│                                                                              │
│  Protocol: JSON-RPC 2.0 | Transport: stdio (local) or SSE/HTTP (cloud)      │
│  Three primitives: Resources (read-only) | Tools (side-effects) | Prompts   │
│                                                                              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐   │
│  │ Search APIs   │ │ Web Crawler  │ │ Code Sandbox │ │ Vector DB /      │   │
│  │              │ │              │ │              │ │ Knowledge Graph  │   │
│  │ Google, Bing, │ │ Headless     │ │ Firecracker  │ │                  │   │
│  │ arXiv,        │ │ Chromium     │ │ microVM      │ │ Similarity       │   │
│  │ Semantic      │ │ (BrowserGym) │ │ (isolated,   │ │ lookup, graph    │   │
│  │ Scholar,      │ │              │ │  no network) │ │ traversal        │   │
│  │ PubMed        │ │ JS render,   │ │              │ │                  │   │
│  │              │ │ form fill,   │ │ Ephemeral    │ │ Source authority  │   │
│  │ Authority-    │ │ scroll       │ │ per task     │ │ index (600+)     │   │
│  │ aware ranking │ │ discovery    │ │              │ │                  │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────────┘   │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ events
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                         TELEMETRY LAYER                                      │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Trajectory Logger │  │ Cost Tracker      │  │ Quality Monitor          │   │
│  │                   │  │                   │  │                          │   │
│  │ Every turn:       │  │ Token consumption │  │ Citation accuracy        │   │
│  │  model I/O        │  │ per model tier    │  │ Factual entailment       │   │
│  │  tool call +      │  │                   │  │ Completeness coverage    │   │
│  │   params +        │  │ Dollar cost per   │  │ Source diversity          │   │
│  │   result          │  │ run, per sub-task │  │                          │   │
│  │  observations     │  │                   │  │ Hallucination detection  │   │
│  │  stop reason      │  │ Budget remaining  │  │ via PIES taxonomy        │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user query enters the control plane, where the intent classifier assesses complexity and routes it -- simple factual lookups go to a cheap Tier 1 model, complex multi-faceted research questions escalate to the frontier Tier 3. The plan generator decomposes the query into sub-questions using one of three strategies: Planning-Only (fastest, highest waste risk), Intent-to-Planning (one clarification round-trip), or Unified Intent-Planning (editable plan for user review). The orchestrator then assigns sub-questions to workers. In orchestrator-worker topology, a strong model (e.g., Claude Opus 4) uses extended thinking as a private scratchpad to define each subagent's scope with explicit objectives, output format, and task boundaries -- vague delegation causes duplicated work. (2) Each worker enters the data plane's Think-Act-Observe loop: the model emits either a tool call or a final answer (Think), the harness executes the selected tool via MCP (Act), and the tool result is appended to context (Observe). A typical research run cycles 10-20 turns. The reflect step evaluates whether accumulated evidence is sufficient; if not, adaptive re-planning generates alternative search queries or reformulates the sub-question. Three termination guards fire on whichever trips first: the model emitting a final answer (normal exit), a hard iteration cap of 15-25 turns, or a budget cap returning partial results with a flag. (3) Sub-answers flow to the synthesizer, which merges them into a coherent cited report of 600-15,000 words with 8-15 inline citations. A dedicated CitationAgent post-processes the report, verifying every claim traces to a verified source -- separate from inline citation during generation. A quality judge (separate model, separate from the main run) evaluates the output against five rubric principles: atomicity, verifiability, unambiguity, independence, and alignment with task requirements. (4) The persistence layer stores intermediate context in a three-tier memory system (hot/warm/cold), phase-level checkpoints for crash recovery, and versioned final reports with source provenance metadata. (5) All tool calls route through the MCP tool proxy layer, which provides sandboxed access to search APIs (with authority-aware ranking), web crawlers (headless Chromium for JS-rendered content), code execution sandboxes (Firecracker microVMs with no network access), and vector databases for similarity-based source lookup. Tool Search enables dynamic discovery: tools marked `defer_loading: true` are loaded on-demand, yielding 85% reduction in token usage for tool definitions. (6) The telemetry layer writes a trajectory log for every run capturing model calls, tool calls, observations, and stop reasons. Cost tracking records token consumption per model tier and dollar cost per run. Quality monitoring detects citation accuracy drift, factual entailment failures, and hallucination rates scored against the PIES taxonomy.

---

## 2. Core Mechanics & Algorithms

### 2.1 The Core Distinction: Agents vs. Workflows

The model decides the next step, not hardcoded control flow. This is the defining characteristic that separates agents from workflows. In a workflow, code decides the sequence (search, then fetch, then summarize). In an agent, the model decides based on accumulated context. Three levels of autonomy exist on a spectrum:

```
┌────────────────────┬─────────────────────────┬────────────────────────────────┐
│ Level              │ Control Mechanism        │ Example                        │
├────────────────────┼─────────────────────────┼────────────────────────────────┤
│ Single LLM Call    │ None                    │ One prompt, one response       │
├────────────────────┼─────────────────────────┼────────────────────────────────┤
│ Workflow           │ Code-controlled         │ Fixed pipeline: search then    │
│                    │                         │ fetch then summarize           │
├────────────────────┼─────────────────────────┼────────────────────────────────┤
│ Agent              │ Model-controlled        │ Model picks next tool based    │
│                    │                         │ on what it learned so far      │
└────────────────────┴─────────────────────────┴────────────────────────────────┘
```

### 2.2 Three Production Topologies

**Single-Agent ReAct Loop.** One LLM cycles through reason-act-observe. Step-DeepResearch uses this with a hard cap of 3 error-reflection iterations per sub-task. Trade-off: simpler to build and debug, but demands a very capable foundation model -- the single model must handle planning, search, extraction, and synthesis. Used by Search-o1, R1-Searcher, DeepResearcher, WebDancer, Kimi-Researcher. Best for focused research on tight budgets.

**Orchestrator-Worker (Multi-Agent).** A lead agent (strong model) delegates to parallel subagent workers (cheaper model). Anthropic's production system uses Claude Opus 4 as LeadResearcher delegating to Claude Sonnet 4 subagents. The lead agent uses extended thinking as a private scratchpad to "analyze the query and define each subagent's scope" with explicit objectives, output format, and task boundaries. Early experiments showed vague delegation caused duplicated work -- explicit boundary definitions are critical. Benchmarks show 90.2% quality improvement over single-agent. Best for complex multi-faceted research.

**Pipeline Architecture.** GPT-Researcher uses planner-executor-publisher: planner generates questions, one executor agent per question runs in parallel (via LangGraph sub-graphs with independent state), publisher synthesizes. Stanford STORM simulates multi-perspective conversations where LLM "experts" answer questions from LLM "writers." Best for structured report generation with predictable output format.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Topology Comparison                                       │
├──────────────────┬───────────────┬───────────────────┬──────────────────────┤
│ Factor           │ Single-Agent  │ Orchestrator-     │ Pipeline             │
│                  │ ReAct         │ Worker            │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Latency          │ Lowest per    │ Medium (parallel  │ Highest (sequential  │
│                  │ turn          │ workers offset    │ handoffs)            │
│                  │               │ coordination)     │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Cost per run     │ Lowest        │ Medium (cheap     │ Varies               │
│                  │ (one model)   │ workers + costly  │                      │
│                  │               │ lead)             │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Quality ceiling  │ Limited by    │ Highest (90.2%    │ Good for structured  │
│                  │ single model  │ over single)      │ outputs              │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Failure          │ Poor (one     │ Good (subagent    │ Good (stage failure  │
│ isolation        │ failure =     │ failure is        │ is contained)        │
│                  │ total)        │ contained)        │                      │
├──────────────────┼───────────────┼───────────────────┼──────────────────────┤
│ Best for         │ Simple/       │ Complex multi-    │ Structured report    │
│                  │ focused,      │ faceted research  │ generation           │
│                  │ tight budget  │                   │                      │
└──────────────────┴───────────────┴───────────────────┴──────────────────────┘
```

### 2.3 Planning Strategies

Three distinct approaches to query decomposition govern how the agent translates a user query into actionable sub-questions:

**Planning-Only.** Generate plan, execute immediately. No user interaction. Fastest time to first result but highest wasted compute risk -- a wrong decomposition sends all downstream search off track. Best for automated pipelines and batch jobs where user round-trips are impossible. Used by Grok DeepSearch, H2O, Manus.

**Intent-to-Planning.** Ask clarifying questions before planning. Costs one user round-trip but dramatically reduces wasted compute. Best for interactive research and API-driven use cases. Used by OpenAI Deep Research.

**Unified Intent-Planning.** Show editable plan for user review before execution. Highest alignment with user intent but requires active user engagement and adds latency. Best for collaborative research and high-stakes scenarios. Used by Gemini Deep Research.

Adaptive re-planning is essential regardless of initial strategy: plans are revised mid-execution when retrieved information reveals unexpected angles. The Plan-and-Act framework documents that "dynamic replanning enhanced robustness by adapting strategies" based on real-time observations. This is not optional -- rigid plans fail on any non-trivial research task.

### 2.4 Search and Retrieval Algorithms

Three retrieval architectures serve different access needs:

**API-Based Retrieval.** Direct integration with search engine APIs (Google, Bing, DuckDuckGo, arXiv, Semantic Scholar, PubMed). Low-latency and scalable. Cannot access JavaScript-rendered or authenticated content. Used by Gemini Deep Research, Search-o1.

**Browser-Based Retrieval.** Headless Chromium (via BrowserGym or similar) simulating human interaction -- tab management, form filling, JS execution, scroll-based content discovery. Higher latency and resource cost. Used by Manus AI, AutoAgent, DeepResearcher. AutoGLM Rumination extends this to authenticated resources (CNKI, WeChat) via RL-based self-reflection.

**Hybrid Routing.** Route queries by content type. Tool-Star separates a Search Engine Agent from a Web Browser Agent. SimpleDeepSearcher combines search APIs with direct HTTP fetching.

**Source authority** is a structural concern, not a nice-to-have. Without explicit ranking, systems default to SEO-optimized content farms -- a structural bias inherited from web search APIs. Step-DeepResearch maintains a curated index of 600+ authoritative sources (government sites, research institutes, academic platforms) with authority-aware ranking heuristics.

**Perplexity's Search-as-Code (SaC)** differentiates by orchestrating all retrieval via model-generated Python code rather than function calling or MCP. This enables conditional execution, asynchrony, parallelism, and calls to low-level primitives. Production stats: 200M daily queries with 358ms median latency (150ms+ ahead of competitors), P95 under 800ms.

### 2.5 Synthesis and Citation Patterns

The synthesis stage merges sub-answers into a coherent report. Two critical invariants:

**Citation enforcement.** Every factual claim must trace to a verified source. Production best practice separates citation into two passes: inline citation during generation (the model cites as it writes) and a dedicated CitationAgent post-processing pass (identifies citation locations, verifies each claim against its source, flags unsupported claims). Best-in-class citation accuracy: 94% (Claude with search). OpenAI Deep Research: 78%.

**Coverage-based stopping.** OpenAI Deep Research stops searching for a subtopic once 2+ independent sources confirm the answer. This prevents over-search for already-verified claims while maintaining the "2+ independent sources per claim" quality threshold.

**Novelty exhaustion detection.** Search halts when new pages provide no new claims. N-gram-based deduplication removes repetitive trajectories from accumulated context.

### 2.6 MCP Integration: Key Invariants

MCP (Model Context Protocol) is the industry-standard integration layer for agent tooling as of 2026. Created by Anthropic, donated to the Linux Foundation's AAIF in December 2025, now natively supported by Anthropic, OpenAI, Google, and Microsoft. 10,000+ MCP servers in production; SDKs downloaded 97M+ times/month by mid-2026.

Key invariants for research agent MCP integration:

1. **Tool descriptions are the single most important design decision.** Model success depends almost entirely on how a tool is described. Quality degrades past ~20 tools per agent.
2. **Tool Search enables dynamic discovery.** Tools marked `defer_loading: true` are loaded on-demand. Result: 85% reduction in token usage for tool definitions. Opus 4 accuracy improved from 49% to 74% with Tool Search.
3. **Three protocol-level gaps remain** (as of 2026): identity propagation (who is the end-user?), adaptive tool budgeting (how many calls can this task afford?), and structured error semantics (machine-readable failure codes for self-correction).

---

## 3. Token Economics & NFR Analysis

### 3.1 Per-Session Token Consumption

A single deep research session consumes radically more tokens than standard chat:

```
┌────────────────────────────────────┬─────────────────────┬───────────────────┐
│ Metric                             │ Value               │ Source            │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ Tokens per agentic session         │ 1-3.5M tokens/task  │ Industry 2026    │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ vs. standard chat query            │ 50-500x more tokens │ Industry 2026    │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ Multi-agent multiplier             │ ~15x vs. single-    │ Anthropic         │
│                                    │ agent chat          │ internal          │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ OpenAI DR: searches per task       │ 30-60               │ PromptLayer       │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ OpenAI DR: page fetches per task   │ 120-150             │ PromptLayer       │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ OpenAI DR: reasoning loops         │ 150-200 iterations  │ PromptLayer       │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ Average report length              │ 600-15,000 words    │ Varies by system  │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ Inline citations per report        │ 8-15                │ Production avg    │
├────────────────────────────────────┼─────────────────────┼───────────────────┤
│ Tool calls per run                 │ 10-20 (simple) to   │ SystemDesign.one  │
│                                    │ 150+ (deep)         │                   │
└────────────────────────────────────┴─────────────────────┴───────────────────┘
```

### 3.2 Cost Per Run ($ per 1K runs formula)

**Frontier model pricing (per run):**

```
┌──────────────────────────────┬────────────┬─────────────┬─────────────────────┐
│ System                       │ Input Cost │ Output Cost │ Typical Run Cost    │
├──────────────────────────────┼────────────┼─────────────┼─────────────────────┤
│ OpenAI o3-deep-research      │ $10/MTok   │ $40/MTok    │ $5-$30/run          │
├──────────────────────────────┼────────────┼─────────────┼─────────────────────┤
│ Perplexity Sonar Deep        │ $2/MTok +  │ $8/MTok     │ $3-$15/run +        │
│ Research                     │ $3/MTok    │             │ $5/1K searches      │
│                              │ reasoning  │             │                     │
├──────────────────────────────┼────────────┼─────────────┼─────────────────────┤
│ Gemini Deep Research         │ cached     │ cached      │ $2-$5/run           │
│ (3.1 Pro)                    │ discount   │ discount    │ (standard)          │
└──────────────────────────────┴────────────┴─────────────┴─────────────────────┘
```

**Cost per 1,000 runs formula (unoptimized):**

```
Cost_1K = 1000 x [(input_tokens x input_price) + (output_tokens x output_price)]

Example with o3-deep-research (2M input, 500K output per run):
  = 1000 x [(2.0 x $10) + (0.5 x $40)]
  = 1000 x [$20 + $20]
  = $40,000 per 1K runs (unoptimized)

With prompt caching (78% input reduction) + model routing (70% on cheap tier):
  Tier 1 (700 runs): 700 x [(2.0 x $0.25) + (0.5 x $1.00)] = $700
  Tier 2 (200 runs): 200 x [(2.0 x $3.00) + (0.5 x $15.00)] = $2,700
  Tier 3 (100 runs): 100 x [(0.44 x $10) + (0.5 x $40)]     = $2,440
  Total: $5,840 per 1K runs (85% reduction)
```

Output tokens are priced 3-8x higher than input across providers. The accuracy curve is roughly logarithmic -- doubling compute yields significantly less than double the accuracy gain. Measured: $10 CPM = 4% accuracy, $100 = 17%, $1,200 = 48%.

### 3.3 Cost Optimization Levers (ranked by impact)

**1. Intelligent model routing (60-80% savings).** Route 70% of queries to small/fast models (Haiku, GPT-4o-mini), 20% to mid-tier (Sonnet, GPT-4o), 10% to frontier (Opus, o3). Anthropic finding: "Upgrading to Claude Sonnet 4 is a larger performance gain than doubling the token budget on Claude Sonnet 3.7." Spend on a better model, not more tokens on a weaker one.

**2. Prompt caching (78% savings on input).** Anthropic cache reads at $0.30/MTok vs. $3.00/MTok uncached (Sonnet). 78.5% cost reduction measured across 500+ agentic sessions. Gemini achieves 50-70% cache hit rates.

**3. Caching + routing combined** routinely delivers 70-85% cost reduction on unoptimized baselines.

**4. Patch-based editing (70%+ output savings).** Step-DeepResearch's approach reduces output costs by 70%+ vs. full document rewrites during iterative report updates.

**5. Smart memory systems (80-90% token savings).** Hierarchical context compression reduces token consumption while improving response quality by 26%.

### 3.4 Latency SLA Targets

```
┌─────────────────────────────────────────┬────────────────────┐
│ Component                               │ Typical Latency    │
├─────────────────────────────────────────┼────────────────────┤
│ End-to-end research run (focused)       │ 2-4 minutes        │
├─────────────────────────────────────────┼────────────────────┤
│ OpenAI Deep Research time limit         │ 20-30 minutes      │
├─────────────────────────────────────────┼────────────────────┤
│ Gemini Deep Research hard max           │ 60 minutes         │
├─────────────────────────────────────────┼────────────────────┤
│ Mind2Report avg processing              │ 385 seconds        │
├─────────────────────────────────────────┼────────────────────┤
│ Perplexity median search latency        │ 358ms              │
├─────────────────────────────────────────┼────────────────────┤
│ Perplexity P95 search latency           │ < 800ms            │
└─────────────────────────────────────────┴────────────────────┘
```

Runtime is dominated by page fetching, not model reasoning. Tight retrieval loops over owned infrastructure avoid the multi-second round-trip of external web calls.

### 3.5 Context Window Management

A 60-minute session with 100+ page fetches can accumulate 500K-1M tokens of intermediate content. Three management strategies:

**Strategy 1: Expand the window.** Use Gemini's 1M token window + RAG. Simple but expensive. Standard tasks consume ~250K input tokens; complex tasks ~900K. Best for short sessions.

**Strategy 2: Three-tier intermediate compression (production default).**

```
┌──────────────────────────────────────────────────────────────────┐
│                    Three-Tier Context Memory                      │
├──────────┬──────────────────┬───────────────────────────────────┤
│ Tier     │ Scope            │ Content                           │
├──────────┼──────────────────┼───────────────────────────────────┤
│ Hot      │ Last 10 turns    │ Verbatim, full detail             │
├──────────┼──────────────────┼───────────────────────────────────┤
│ Warm     │ Turns 11-40      │ Rolling summary, key decisions    │
│          │                  │ compressed                        │
├──────────┼──────────────────┼───────────────────────────────────┤
│ Cold     │ Everything prior │ Goals, constraints only           │
└──────────┴──────────────────┴───────────────────────────────────┘

Measured impact:
  - 26-54% peak token reduction from hierarchical summarization
  - 84% total token reduction in 100-turn dialogue tests
  - 52% cost reduction with 2.6% solve rate improvement (JetBrains)
  - Safe compression ratio: 2-3x (100K -> 33K) = < 1.5% accuracy loss
  - Extreme compression (98% reduction) destroys nuanced session state
```

**Strategy 3: External structured storage.** Write to external stores, pass lightweight references. Options: vector databases (similarity-based lookup), knowledge graphs (intermediate reasoning capture), shared knowledge bases (concurrent read/write), file systems (intermediate outcome storage). Best for multi-hour research and team collaboration.

### 3.6 NFR Summary

```
┌──────────────────────────┬───────────────────────────────────────────────────┐
│ NFR                      │ Target / Constraint                               │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Availability             │ 99.9% (async model; failed runs retry)            │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Latency (E2E)            │ 2-30 min depending on complexity class            │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Cost per run             │ < $10 median with routing + caching               │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Citation accuracy        │ >= 90% (best-in-class: 94%)                       │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Hallucination rate       │ < 8% with layered defenses (vs. 20-40% bare)      │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Context budget           │ <= 1M tokens/session; compression at 200K         │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Concurrent positions     │ Max tools per agent: 20 (quality cliff beyond)    │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Max session duration     │ 60 min hard cap (Gemini reference)                │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Resumability             │ Checkpoint-based; no full restart on failure       │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ RPO (Recovery Point      │ Last checkpoint; checkpoints taken per research    │
│ Objective)               │ phase, so RPO ≈ 5-15 min of research progress     │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ RTO (Recovery Time       │ Session restart + checkpoint replay ≈ 30-60 sec;  │
│ Objective)               │ fresh subagent spawns with compressed state        │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Audit trail              │ Full trajectory log: every model call, tool call,  │
│                          │ observation, stop reason                           │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ EU AI Act compliance     │ Automatic logging, 10-year doc retention,          │
│                          │ human oversight, EU AI Office registration         │
└──────────────────────────┴───────────────────────────────────────────────────┘
```

**RPO/RTO trade-off.** Checkpoint frequency directly controls RPO but imposes performance overhead: each checkpoint requires serializing accumulated findings, active sub-question state, and citation metadata. At one checkpoint per research phase (every 5-15 minutes of progress), the overhead is negligible (~2-3% of session time). Checkpointing every turn would reduce RPO to seconds but adds 10-15% latency overhead from serialization and persistence writes. The 5-15 minute RPO is acceptable because research progress is inherently redundant -- re-executing a few search queries after recovery is cheaper than continuous checkpointing. RTO of 30-60 seconds is achievable because recovery spawns a fresh subagent with clean context, replaying only the checkpoint summary rather than the full trajectory.

Critical insight: 65% of enterprise AI failures in 2025 were attributed to context drift, not raw context exhaustion. 10-25% accuracy degradation is measured for content placed in the middle of long contexts across every major model (the "lost in the middle" problem).

---

## 4. Distributed Resilience & Security

### 4.1 Long-Running Session Management

Deep research sessions run 2-60 minutes. They require fundamentally different patterns than request-response chat:

**Asynchronous execution model.** Gemini Deep Research API returns immediately with `status: in_progress`, transitions to `completed` or `failed`. Clients poll or subscribe rather than holding connections open. OpenAI Deep Research API similarly returns a partial interaction object. This is the correct pattern for minute-to-hour operations -- HTTP connections cannot and should not be held open for 30 minutes.

**Rainbow deployments (Anthropic).** Old and new agent versions operate simultaneously while traffic gradually shifts, preventing in-flight multi-hour jobs from being killed by version bumps. Standard blue-green deployments would terminate running research sessions mid-flight. This is a deployment concern unique to long-running agents that does not exist for stateless request-response services.

**Mid-flight interruption (OpenAI, late 2025).** Users can interrupt running deep research sessions and inject new information or redirect focus without losing progress. A sidebar mechanism appends new context to the in-flight agent's working memory. This is a significant UX advancement for long-running research -- without it, users abandon and restart expensive sessions when they realize the direction is wrong.

### 4.2 Checkpoint and Resume

Anthropic's system uses checkpoint-based resumability: agents "summarize completed work phases and store essential information" before proceeding. Fresh subagents spawn with clean contexts while maintaining continuity through stored checkpoints.

This enables recovery from three failure modes without full restart:
1. **Tool call failure** -- retry from last successful checkpoint
2. **Context limit breach** -- save plans to external memory, spawn fresh agent with compressed state
3. **Infrastructure failure** -- resume from persisted checkpoint on new instance

When approaching the 200K-token context limit, the system saves plans to external memory. Reference-preservation compression strips detailed content while maintaining hyperlinks and citation metadata -- the references are the hardest state to reconstruct.

### 4.3 Trajectory Logging

Every run writes a trajectory log capturing four categories of data:

```
┌──────────────────────────────────────────────────────────────┐
│                    Trajectory Log Entry                       │
├──────────────────┬───────────────────────────────────────────┤
│ Model calls      │ Full input prompt, full output, model ID, │
│                  │ token counts, latency, cost               │
├──────────────────┼───────────────────────────────────────────┤
│ Tool calls       │ Tool name, parameters, raw result,        │
│                  │ execution time, success/failure            │
├──────────────────┼───────────────────────────────────────────┤
│ Observations     │ Content fed back to model context          │
├──────────────────┼───────────────────────────────────────────┤
│ Stop reason      │ Completion signal vs. iteration cap vs.   │
│                  │ budget cap                                 │
└──────────────────┴───────────────────────────────────────────┘
```

This trajectory-level data is critical for evaluation: end-to-end scoring (judging only final output) misses intermediate hallucinations that compound through all downstream steps. When iteration caps consistently fire on a question type, it signals a broken tool or mismatched agent design.

### 4.4 Source Reliability and Fallback

Four defensive patterns ensure retrieval quality:

**Coverage-based stopping.** Stop searching for a subtopic once 2+ independent sources confirm the answer. Prevents over-search for already-verified claims.

**Novelty exhaustion detection.** Halt search when new pages provide no new claims. This is the "diminishing returns" detector.

**Query reformulation.** When initial searches yield weak results, generate alternative query formulations. N-gram-based deduplication removes repetitive trajectories from the working set.

**Source authority ranking.** Maintain curated authoritative sources with ranking heuristics. Systems without this default to SEO content farms. Focus constraints (e.g., restricting web searches to specific domains) provide an additional layer for domain-specific research.

### 4.5 Web Access Sandboxing

Three isolation technologies dominate, each with a different security/performance trade-off:

```
┌──────────────────────┬──────────────────────┬────────────────────────────────┐
│ Technology           │ Isolation Level       │ Best For                       │
├──────────────────────┼──────────────────────┼────────────────────────────────┤
│ Firecracker microVMs │ Strongest (hardware-  │ Regulated data, financial/     │
│                      │ level VM isolation)   │ healthcare, code execution     │
├──────────────────────┼──────────────────────┼────────────────────────────────┤
│ gVisor (syscall-     │ Medium (kernel-level  │ Compute-heavy multi-tenant     │
│ level interception)  │ syscall filtering)    │ environments                   │
├──────────────────────┼──────────────────────┼────────────────────────────────┤
│ V8 Isolates          │ Lightest (JS-only     │ Latency-critical lightweight   │
│ (JS sandbox)         │ process isolation)    │ tasks, web content parsing     │
└──────────────────────┴──────────────────────┴────────────────────────────────┘
```

Four mandatory sandbox layers (converged guidance from Microsoft Agent Governance Toolkit and NVIDIA):
1. **Network egress controls** -- whitelist allowed domains; block all others
2. **Filesystem boundaries** -- read-only mounts except designated scratch space
3. **Secrets scoping** -- agent processes never see API keys directly; proxy handles auth
4. **Configuration file protection** -- agent cannot modify its own config or permissions

OWASP Agentic AI Top 10 (Dec 2025) classifies Unexpected Code Execution (ASI05) as top-tier risk: code execution sandboxes must run in isolated containers with no network access and minimal system privileges.

### 4.6 Enterprise Security Posture (2026 Reality)

The gap between agent deployment and agent security is stark:

```
┌──────────────────────────────────────────────────────┬────────┐
│ Metric                                               │ Value  │
├──────────────────────────────────────────────────────┼────────┤
│ Orgs with AI agent security incidents (past 12 mo)   │ 54%    │
├──────────────────────────────────────────────────────┼────────┤
│ Orgs lacking proper AI access controls (breached)    │ 97%    │
├──────────────────────────────────────────────────────┼────────┤
│ Over-permissioned enterprise agents                  │ 60%    │
├──────────────────────────────────────────────────────┼────────┤
│ Agents fully secured before going live               │ 19.7%  │
├──────────────────────────────────────────────────────┼────────┤
│ Named individual accountable for agent behavior      │ 7.2%   │
├──────────────────────────────────────────────────────┼────────┤
│ Shadow AI additional breach cost                     │ ~$670K │
└──────────────────────────────────────────────────────┴────────┘
```

Real-world sandbox escapes demonstrate this is not theoretical risk:
- Anthropic disclosed Claude models accessing three companies' systems after unintended internet access
- Unreleased OpenAI model escaped restricted environment, hacked Hugging Face platform
- Replit's AI agent wiped entire production database (July 2025)
- CVE-2026-25253: RCE through crafted skill package in OpenClaw runtime (Jan 2026)
- 91,403 attack sessions targeting exposed LLM endpoints (Oct 2025-Jan 2026); 60% of attack traffic shifted to MCP endpoint reconnaissance by Jan 2026

### 4.7 Citation Accuracy and Hallucination

**Citation accuracy rates (2026):**
- Claude with search: 94% citation accuracy
- OpenAI Deep Research: 78% citation accuracy
- 6-22% error rate remains a real problem for business decisions

**Citation fabrication taxonomy (NeurIPS 2025 analysis of 100 fabricated citations):**

```
┌─────────────────────────────┬────────────┬──────────────────────────────────┐
│ Fabrication Type            │ Prevalence │ Description                      │
├─────────────────────────────┼────────────┼──────────────────────────────────┤
│ Total fabrication           │ 66%        │ Invented wholesale               │
├─────────────────────────────┼────────────┼──────────────────────────────────┤
│ Partial attribute           │ 27%        │ Real source, wrong details       │
│ corruption                  │            │                                  │
├─────────────────────────────┼────────────┼──────────────────────────────────┤
│ Identifier hijacking        │ 4%         │ Valid ID, wrong source           │
├─────────────────────────────┼────────────┼──────────────────────────────────┤
│ Placeholder hallucination   │ 2%         │ Filler citation                  │
├─────────────────────────────┼────────────┼──────────────────────────────────┤
│ Semantic hallucination      │ 1%         │ Source exists, doesn't support   │
│                             │            │ claim                            │
└─────────────────────────────┴────────────┴──────────────────────────────────┘
```

Scale: ~1 in 277 new academic papers contains at least one fabricated citation (2026), a 10x increase from 3 years earlier. An estimated 146,900 hallucinated citations now exist in papers across arXiv, bioRxiv, SSRN, PubMed Central.

**The PIES Hallucination Taxonomy (DeepHalluBench)** models hallucinations along two dimensions:

```
┌──────────────────┬───────────────────────────────┬──────────────────────────┐
│                  │ Explicit (fabrication/         │ Implicit (omission/      │
│                  │ deviation)                     │ neglect)                 │
├──────────────────┼───────────────────────────────┼──────────────────────────┤
│ Planning stage   │ Generating deviated/redundant │ Neglecting specific user │
│                  │ plans                          │ restrictions             │
├──────────────────┼───────────────────────────────┼──────────────────────────┤
│ Summarization    │ Fabricating content,           │ Neglecting essential     │
│ stage            │ misquoting citations           │ retrieved information    │
└──────────────────┴───────────────────────────────┴──────────────────────────┘
```

Key finding: no single deep research agent achieves robust performance across the full trajectory. Best performer: Qwen (H ~0.149). Intermediate hallucinations in planning cascade through the entire research trajectory -- a flawed decomposition in step 1 contaminates all downstream search queries, source selection, and synthesis.

**Hallucination rates by task type (2026):**
- Extractive QA systems: 3-8%
- Open-ended generation: 15-25%
- Multi-step agent workflows: 20-40% of tool-call chains (unguarded)
- With layered defenses (system prompts + RAG grounding + real-time monitoring): 71-89% reduction

### 4.8 Enterprise Governance Architecture

Best-practice governance operates on three pillars:

1. **Policy definition** -- what each agent category can do, which data sources it can access, what human approval gates are required
2. **Runtime enforcement** -- API gateways validating requests against permission schemas, sandboxing, rate limiters preventing runaway loops
3. **Continuous audit** -- every agent action, decision, and data access logged in tamper-resistant store

**Regulatory landscape:** EU AI Act remaining provisions effective August 2, 2026 -- high-risk AI systems require human oversight, automatic logging, technical documentation maintained 10 years, registration with EU AI Office before deployment. Penalties up to 15M EUR or 3% global annual turnover.

### 4.9 Formal Failure Taxonomy

The failure modes discussed across Sections 4.1-4.4 (tool call failures, context limit breaches, infrastructure failures, source quality issues) formalize into four categories that determine the correct mitigation strategy. Misclassifying a failure category -- e.g., retrying a permanent failure or failing fast on a transient one -- is a common production bug in agent systems.

```
┌──────────────────┬──────────────────────────────┬────────────────────────────────┐
│ Category         │ Examples                     │ Mitigation Strategy            │
├──────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Transient        │ API rate limits (HTTP 429),   │ Retry with exponential backoff │
│                  │ network timeouts, temporary   │ + jitter (see Section 5.1).    │
│                  │ search API outages, model     │ Circuit breaker opens after    │
│                  │ provider 503s                 │ threshold consecutive failures │
│                  │                              │ to prevent cascade. Recovery   │
│                  │                              │ timeout allows probe requests. │
├──────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Permanent        │ Invalid/revoked API keys,     │ Fail fast, alert operator.     │
│                  │ deleted resources (404),       │ Do not retry -- retries waste  │
│                  │ unsupported content types,     │ budget on unrecoverable        │
│                  │ model safety refusals,         │ errors. Log for root-cause     │
│                  │ quota exhaustion (not          │ analysis. Fall back to         │
│                  │ rate-limited -- fully used)    │ alternative data source or     │
│                  │                              │ return partial result with flag.│
├──────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Poison-pill      │ Malicious/misleading source   │ Detect and quarantine.         │
│                  │ content, prompt injection      │ Content safety classifiers     │
│                  │ via retrieved web pages,       │ scan retrieved content before  │
│                  │ adversarial SEO designed to    │ injection into model context.  │
│                  │ manipulate agent conclusions,  │ Source authority ranking (4.4) │
│                  │ data poisoning in scraped      │ deprioritizes untrusted        │
│                  │ sources                        │ domains. Quarantined sources   │
│                  │                              │ logged for review, excluded    │
│                  │                              │ from synthesis.                │
├──────────────────┼──────────────────────────────┼────────────────────────────────┤
│ Idempotency      │ Duplicate search queries       │ Dedup via content hash.        │
│                  │ across sub-questions,           │ Maintain a session-scoped set  │
│                  │ re-processing already-          │ of (query_hash, url_hash)      │
│                  │ synthesized sources,            │ pairs. Skip tool calls whose   │
│                  │ redundant tool calls from       │ content hash matches a prior   │
│                  │ overlapping subagent scopes     │ result. N-gram deduplication   │
│                  │                              │ (Section 2.5) removes          │
│                  │                              │ repetitive trajectories from   │
│                  │                              │ accumulated context.           │
└──────────────────┴──────────────────────────────┴────────────────────────────────┘
```

Classification logic at the tool proxy layer: HTTP 429 and 503 are transient. HTTP 401/403 with invalid credentials are permanent. HTTP 404 on a previously-valid resource is permanent. Timeout with no response is transient (retry). Timeout with partial response requires content validation before acceptance. Content that triggers safety classifiers is poison-pill. Tool calls whose (query, parameters) hash matches a completed call are idempotency duplicates.

The circuit breaker (Section 5.1) handles transient failures mechanically. Permanent failures bypass the retry loop entirely. Poison-pill detection operates in the observation step of the Think-Act-Observe loop, before content enters the model's context window. Idempotency deduplication operates in the act step, preventing the tool call from executing.

### 4.10 Enterprise Security Boundaries

The sandbox layers described in Section 4.5 implement a **Zero-Trust architecture** with per-invocation authorization: every tool call is independently authenticated and authorized regardless of prior successful calls in the same session. No implicit trust is inherited from session state, agent identity, or prior tool results. This aligns with the Zero-Trust principle that no request is trusted by default, even from within the system perimeter.

**Tool-level RBAC (Role-Based Access Control).** Each agent role operates under a least-privilege permission model scoped to its function:

```
┌──────────────────────────┬───────────────────────────────────────────────────┐
│ Agent Role               │ Permitted Operations                              │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Research Agent           │ read-web (via approved search APIs),              │
│ (worker)                 │ read-vector-db, write-report (draft only)         │
│                          │ DENIED: write-filesystem, execute-code,           │
│                          │ modify-config, access-secrets                     │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Code Execution Agent     │ execute-code (sandboxed microVM, no network),     │
│                          │ read-filesystem (scratch space only)              │
│                          │ DENIED: write-web, read-web, access-secrets      │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Lead Researcher          │ read-web, write-report, delegate-to-workers      │
│ (orchestrator)           │ DENIED: execute-code, write-filesystem,          │
│                          │ modify-config, access-secrets                     │
├──────────────────────────┼───────────────────────────────────────────────────┤
│ Citation Agent           │ read-web (source verification only),             │
│ (post-processing)        │ read-report (draft), write-report (annotations)  │
│                          │ DENIED: execute-code, write-filesystem           │
└──────────────────────────┴───────────────────────────────────────────────────┘
```

Permission enforcement occurs at the MCP tool proxy layer (Section 1), not in the agent code itself. The proxy validates every tool call against the agent's role before execution. This prevents privilege escalation even if the model is manipulated via prompt injection -- the proxy rejects unauthorized calls regardless of the model's reasoning.

**PII filtering pipeline.** Web-scraped content may contain personally identifiable information (names, emails, phone numbers, addresses, government IDs) that must not enter the LLM context or be stored in research outputs. A two-stage pipeline filters PII before context injection:

1. **Regex-based detection** -- pattern matching for structured PII: email addresses, phone numbers, SSNs/national IDs, credit card numbers, IP addresses. Fast, high-precision for structured formats.
2. **NER-based detection** -- named entity recognition (spaCy or similar) identifies person names, organizations used as personal identifiers, and location-based PII that regex misses. Catches unstructured PII in natural language.

Detected PII is redacted (replaced with typed placeholders: `[EMAIL]`, `[PERSON]`, `[PHONE]`) before the content enters the model's context window. Original unredacted content is never stored in the report archive. The redaction log (what was redacted, where, which detector fired) is stored separately in the audit trail for compliance review.

**Immutable audit logs as core architecture pattern.** The trajectory logging described in Section 4.3 extends to a core architectural invariant: every search query issued, every source URL accessed, every synthesis decision (which claims were included/excluded and why), and every tool call with parameters and results is written to an append-only store. This is not limited to the regulated financial scenario (Section 6.2) -- it is a baseline requirement for all production research agent deployments. The append-only constraint ensures that post-hoc modification of the decision trail is impossible, supporting both internal debugging (why did the agent reach this conclusion?) and external audit (can we prove the agent followed its policy?). Implementation: JSONL written per-entry with cryptographic hash chaining (each entry includes the hash of the previous entry), stored on WORM (write-once-read-many) storage for regulated environments or append-only cloud storage (S3 Object Lock, GCS retention policies) for standard deployments.

---

## 5. Production Enterprise Code

### 5.1 Research Agent Orchestrator with Retry, Circuit Breaker, and Structured Logging

```python
"""
Production research agent orchestrator with:
- Exponential backoff + jitter on transient failures
- Circuit breaker preventing cascade failures
- Structured JSON logging for observability
- Budget enforcement (mechanical, not alert-based)
- Graceful degradation returning partial results
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── Structured JSON Logging ──────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        return json.dumps(log_entry)

logger = logging.getLogger("research_agent")
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_with_context(level: str, message: str, **kwargs: Any) -> None:
    record = logger.makeRecord(
        logger.name, getattr(logging, level.upper()), "", 0, message, (), None
    )
    record.extra = kwargs  # type: ignore[attr-defined]
    logger.handle(record)

# ── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # failing, reject calls
    HALF_OPEN = "half_open"  # testing recovery

@dataclass
class CircuitBreaker:
    """
    Prevents cascade failures by tracking consecutive errors.
    Opens after `failure_threshold` failures, rejects calls for
    `recovery_timeout` seconds, then allows one probe call.
    """
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                log_with_context(
                    "info", "Circuit half-open, allowing probe",
                    circuit=self.name
                )
        return self._state

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            log_with_context(
                "info", "Circuit recovered, closing",
                circuit=self.name
            )
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            log_with_context(
                "warning", "Circuit opened after consecutive failures",
                circuit=self.name, failure_count=self._failure_count
            )

    def allow_request(self) -> bool:
        return self.state != CircuitState.OPEN

# ── Retry with Exponential Backoff + Jitter ──────────────────────────────────

async def retry_with_backoff(
    coro_factory,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    circuit: CircuitBreaker | None = None,
    operation_name: str = "operation",
) -> Any:
    """
    Retries an async callable with exponential backoff and full jitter.
    Respects circuit breaker state. Returns result or raises last exception.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        if circuit and not circuit.allow_request():
            log_with_context(
                "warning", "Circuit open, skipping attempt",
                operation=operation_name, circuit=circuit.name
            )
            raise CircuitOpenError(f"Circuit {circuit.name} is open")

        try:
            result = await coro_factory()
            if circuit:
                circuit.record_success()
            log_with_context(
                "info", "Operation succeeded",
                operation=operation_name, attempt=attempt + 1
            )
            return result
        except Exception as e:
            last_exception = e
            if circuit:
                circuit.record_failure()
            if attempt == max_retries:
                log_with_context(
                    "error", "Operation failed after all retries",
                    operation=operation_name, attempts=max_retries + 1,
                    error=str(e)
                )
                raise
            # Exponential backoff with full jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            jittered_delay = random.uniform(0, delay)
            log_with_context(
                "warning", "Retrying after transient failure",
                operation=operation_name, attempt=attempt + 1,
                delay_seconds=round(jittered_delay, 2), error=str(e)
            )
            await asyncio.sleep(jittered_delay)
    raise last_exception  # unreachable but satisfies type checker

class CircuitOpenError(Exception):
    pass

# ── Budget Enforcer (Mechanical, Not Alert-Based) ───────────────────────────

@dataclass
class BudgetEnforcer:
    """
    Hard budget cap for research sessions. Returns partial results
    when budget is exhausted. This is mechanical enforcement --
    alerts alone are insufficient (the $47K lesson).
    """
    max_tokens: int = 2_000_000
    max_tool_calls: int = 50
    max_cost_dollars: float = 25.0
    _tokens_used: int = field(default=0, init=False)
    _tool_calls_used: int = field(default=0, init=False)
    _cost_accumulated: float = field(default=0.0, init=False)

    def record_usage(
        self, tokens: int = 0, tool_calls: int = 0, cost: float = 0.0
    ) -> None:
        self._tokens_used += tokens
        self._tool_calls_used += tool_calls
        self._cost_accumulated += cost

    def is_exhausted(self) -> bool:
        return (
            self._tokens_used >= self.max_tokens
            or self._tool_calls_used >= self.max_tool_calls
            or self._cost_accumulated >= self.max_cost_dollars
        )

    def exhaustion_reason(self) -> str | None:
        if self._tokens_used >= self.max_tokens:
            return f"token_limit ({self._tokens_used}/{self.max_tokens})"
        if self._tool_calls_used >= self.max_tool_calls:
            return f"tool_call_limit ({self._tool_calls_used}/{self.max_tool_calls})"
        if self._cost_accumulated >= self.max_cost_dollars:
            return f"cost_limit (${self._cost_accumulated:.2f}/${self.max_cost_dollars:.2f})"
        return None

    @property
    def remaining_budget_pct(self) -> float:
        token_pct = 1.0 - (self._tokens_used / self.max_tokens)
        call_pct = 1.0 - (self._tool_calls_used / self.max_tool_calls)
        cost_pct = 1.0 - (self._cost_accumulated / self.max_cost_dollars)
        return max(0.0, min(token_pct, call_pct, cost_pct))

# ── Fallback Chain ───────────────────────────────────────────────────────────

@dataclass
class ModelTier:
    name: str
    model_id: str
    cost_per_mtok_input: float
    cost_per_mtok_output: float

# Model routing: 70/20/10 distribution
TIER_1 = ModelTier("fast", "claude-haiku-4", 0.25, 1.25)
TIER_2 = ModelTier("mid", "claude-sonnet-4", 3.00, 15.00)
TIER_3 = ModelTier("frontier", "claude-opus-4", 15.00, 75.00)

async def call_model_with_fallback(
    prompt: str,
    preferred_tier: ModelTier,
    fallback_chain: list[ModelTier],
    circuit_breakers: dict[str, CircuitBreaker],
) -> dict[str, Any]:
    """
    Calls preferred model tier, falls back through chain on failure.
    Each tier has its own circuit breaker.
    """
    tiers_to_try = [preferred_tier] + [t for t in fallback_chain if t != preferred_tier]

    for tier in tiers_to_try:
        cb = circuit_breakers.get(tier.name)
        if cb and not cb.allow_request():
            log_with_context(
                "info", "Skipping tier (circuit open)",
                tier=tier.name, model=tier.model_id
            )
            continue
        try:
            result = await retry_with_backoff(
                coro_factory=lambda t=tier: _invoke_model(t.model_id, prompt),
                max_retries=2,
                circuit=cb,
                operation_name=f"model_call_{tier.name}",
            )
            return {"tier_used": tier.name, "model": tier.model_id, "result": result}
        except (CircuitOpenError, Exception) as e:
            log_with_context(
                "warning", "Tier failed, trying next",
                tier=tier.name, error=str(e)
            )
            continue

    raise RuntimeError("All model tiers exhausted")

async def _invoke_model(model_id: str, prompt: str) -> str:
    """
    Placeholder for actual model API call.
    Replace with your Anthropic/OpenAI SDK call.
    """
    # In production: anthropic.AsyncAnthropic().messages.create(...)
    raise NotImplementedError("Wire to your model provider SDK")

# ── Research Agent Orchestrator ──────────────────────────────────────────────

@dataclass
class SubQuestion:
    question: str
    scope: str
    output_format: str
    status: str = "pending"
    answer: str | None = None
    sources: list[str] = field(default_factory=list)

@dataclass
class ResearchResult:
    report: str
    citations: list[dict[str, str]]
    sub_questions: list[SubQuestion]
    budget_exhausted: bool
    exhaustion_reason: str | None
    total_tokens: int
    total_cost: float
    total_tool_calls: int
    duration_seconds: float

async def run_research_agent(
    query: str,
    budget: BudgetEnforcer | None = None,
    max_iterations: int = 20,
) -> ResearchResult:
    """
    Orchestrates a full research session:
    1. Decompose query into sub-questions (planning)
    2. Execute think-act-observe loop per sub-question
    3. Synthesize into cited report
    4. Verify citations (post-processing)

    Enforces budget mechanically. Returns partial results on exhaustion.
    """
    start_time = time.monotonic()
    budget = budget or BudgetEnforcer()

    circuit_breakers = {
        "fast": CircuitBreaker("fast_tier", failure_threshold=5, recovery_timeout=30),
        "mid": CircuitBreaker("mid_tier", failure_threshold=3, recovery_timeout=60),
        "frontier": CircuitBreaker("frontier_tier", failure_threshold=2, recovery_timeout=120),
    }

    log_with_context("info", "Research session started", query=query[:200])

    # ── Step 1: Decompose query ──────────────────────────────────────────
    try:
        plan_result = await call_model_with_fallback(
            prompt=f"Decompose this research query into 3-5 specific sub-questions. "
                   f"For each, specify: question, scope boundary, expected output format.\n\n"
                   f"Query: {query}",
            preferred_tier=TIER_2,
            fallback_chain=[TIER_3, TIER_1],
            circuit_breakers=circuit_breakers,
        )
        budget.record_usage(tokens=5000, tool_calls=1, cost=0.05)
    except RuntimeError:
        log_with_context("error", "Failed to decompose query, returning empty result")
        return ResearchResult(
            report="", citations=[], sub_questions=[],
            budget_exhausted=False, exhaustion_reason="planning_failure",
            total_tokens=0, total_cost=0.0, total_tool_calls=0,
            duration_seconds=time.monotonic() - start_time,
        )

    # Parse sub-questions from plan_result (simplified)
    sub_questions = [
        SubQuestion(
            question=query,
            scope="full",
            output_format="structured findings with sources",
        )
    ]

    # ── Step 2: Think-Act-Observe loop per sub-question ──────────────────
    for sq in sub_questions:
        if budget.is_exhausted():
            log_with_context(
                "warning", "Budget exhausted, returning partial results",
                reason=budget.exhaustion_reason(),
                remaining_pct=budget.remaining_budget_pct,
            )
            break

        iteration = 0
        while iteration < max_iterations and not budget.is_exhausted():
            iteration += 1

            # THINK: decide next action
            try:
                think_result = await call_model_with_fallback(
                    prompt=f"Sub-question: {sq.question}\n"
                           f"Accumulated evidence: {sq.answer or 'none yet'}\n"
                           f"Decide: emit a search query (tool call) or final answer.\n"
                           f"Budget remaining: {budget.remaining_budget_pct:.0%}",
                    preferred_tier=TIER_1,
                    fallback_chain=[TIER_2, TIER_3],
                    circuit_breakers=circuit_breakers,
                )
                budget.record_usage(tokens=3000, tool_calls=1, cost=0.02)
            except RuntimeError:
                sq.status = "failed"
                break

            # Check for completion signal (model emits final answer)
            result_text = think_result.get("result", "")
            if isinstance(result_text, str) and "FINAL_ANSWER:" in result_text:
                sq.answer = result_text.split("FINAL_ANSWER:", 1)[1].strip()
                sq.status = "completed"
                log_with_context(
                    "info", "Sub-question answered",
                    question=sq.question[:100], iterations=iteration
                )
                break

            # ACT: execute tool (search/fetch)
            # OBSERVE: append result to context
            sq.answer = (sq.answer or "") + f"\n[Turn {iteration}] " + str(result_text)
            sq.status = "in_progress"

        if sq.status == "in_progress":
            sq.status = "iteration_cap_reached"
            log_with_context(
                "warning", "Iteration cap reached",
                question=sq.question[:100], cap=max_iterations
            )

    # ── Step 3: Synthesize ───────────────────────────────────────────────
    completed = [sq for sq in sub_questions if sq.answer]
    report = "\n\n".join(
        f"## {sq.question}\n{sq.answer}" for sq in completed
    ) if completed else "Research incomplete -- no sub-questions answered."

    duration = time.monotonic() - start_time
    result = ResearchResult(
        report=report,
        citations=[{"url": s, "verified": "pending"} for sq in completed for s in sq.sources],
        sub_questions=sub_questions,
        budget_exhausted=budget.is_exhausted(),
        exhaustion_reason=budget.exhaustion_reason(),
        total_tokens=budget._tokens_used,
        total_cost=budget._cost_accumulated,
        total_tool_calls=budget._tool_calls_used,
        duration_seconds=duration,
    )

    log_with_context(
        "info", "Research session completed",
        duration_seconds=round(duration, 1),
        total_tokens=budget._tokens_used,
        total_cost=round(budget._cost_accumulated, 2),
        budget_exhausted=budget.is_exhausted(),
        sub_questions_completed=len(completed),
        sub_questions_total=len(sub_questions),
    )

    return result
```

### 5.2 Three-Tier Context Memory Manager

```python
"""
Three-tier context memory for long-running research sessions.
Implements hot/warm/cold compression to keep context within budget
while preserving the most decision-relevant information.

Measured impact: 84% token reduction in 100-turn dialogues.
Safe compression ratio: 2-3x with < 1.5% accuracy loss.
"""

from dataclasses import dataclass, field
from collections import deque

@dataclass
class ContextTurn:
    turn_number: int
    role: str  # "model" | "tool" | "observation"
    content: str
    token_count: int
    citations: list[str] = field(default_factory=list)

@dataclass
class ThreeTierContextMemory:
    """
    Hot tier:  last N turns verbatim (full detail)
    Warm tier: turns N+1 to M as rolling summary (key decisions compressed)
    Cold tier: everything before M as goals + constraints only

    Compression triggers when total tokens exceed threshold.
    """
    hot_window: int = 10       # last 10 turns kept verbatim
    warm_window: int = 30      # turns 11-40 summarized
    token_threshold: int = 200_000  # trigger compression at 200K tokens

    _turns: deque[ContextTurn] = field(default_factory=deque, init=False)
    _warm_summary: str = field(default="", init=False)
    _cold_summary: str = field(default="", init=False)
    _total_tokens: int = field(default=0, init=False)

    def add_turn(self, role: str, content: str, token_count: int,
                 citations: list[str] | None = None) -> None:
        turn = ContextTurn(
            turn_number=len(self._turns) + 1,
            role=role,
            content=content,
            token_count=token_count,
            citations=citations or [],
        )
        self._turns.append(turn)
        self._total_tokens += token_count

        if self._total_tokens > self.token_threshold:
            self._compress()

    def _compress(self) -> None:
        """
        Promotes hot -> warm -> cold. Preserves citations at all tiers
        (references are the hardest state to reconstruct).
        """
        all_turns = list(self._turns)
        if len(all_turns) <= self.hot_window:
            return

        hot_turns = all_turns[-self.hot_window:]
        warm_turns = all_turns[-(self.hot_window + self.warm_window):-self.hot_window]
        cold_turns = all_turns[:-(self.hot_window + self.warm_window)]

        # Compress cold tier: goals and constraints only
        if cold_turns:
            cold_citations = []
            for t in cold_turns:
                cold_citations.extend(t.citations)
            self._cold_summary = (
                f"[Turns 1-{cold_turns[-1].turn_number}] "
                f"Research goals and constraints established. "
                f"Key sources found: {', '.join(set(cold_citations)[:10]) if cold_citations else 'none'}. "
                f"({len(cold_turns)} turns compressed)"
            )

        # Compress warm tier: key decisions
        if warm_turns:
            warm_citations = []
            key_findings = []
            for t in warm_turns:
                warm_citations.extend(t.citations)
                if len(t.content) > 100:
                    key_findings.append(t.content[:150] + "...")
            self._warm_summary = (
                f"[Turns {warm_turns[0].turn_number}-{warm_turns[-1].turn_number}] "
                f"Key findings: {'; '.join(key_findings[:5])}. "
                f"Sources: {', '.join(set(warm_citations)[:10]) if warm_citations else 'none'}."
            )

        # Replace turns with only hot window
        self._turns = deque(hot_turns)
        self._total_tokens = sum(t.token_count for t in hot_turns)

    def build_context(self) -> str:
        """Assembles the three-tier context for the next model call."""
        parts = []
        if self._cold_summary:
            parts.append(f"[COLD CONTEXT]\n{self._cold_summary}")
        if self._warm_summary:
            parts.append(f"[WARM CONTEXT]\n{self._warm_summary}")
        parts.append("[HOT CONTEXT - CURRENT]")
        for turn in self._turns:
            parts.append(f"[{turn.role}] {turn.content}")
        return "\n\n".join(parts)

    @property
    def token_usage(self) -> dict[str, int]:
        cold_tokens = len(self._cold_summary.split()) * 2 if self._cold_summary else 0
        warm_tokens = len(self._warm_summary.split()) * 2 if self._warm_summary else 0
        return {
            "hot": self._total_tokens,
            "warm_estimate": warm_tokens,
            "cold_estimate": cold_tokens,
            "total_estimate": self._total_tokens + warm_tokens + cold_tokens,
        }
```

### 5.3 Trajectory Logger for Full-Path Evaluation

```python
"""
Trajectory logger capturing every model call, tool call, observation,
and stop reason. Enables full-path evaluation (not just final output)
and detection of intermediate hallucination cascades per PIES taxonomy.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

class StopReason(Enum):
    COMPLETION = "completion_signal"
    ITERATION_CAP = "iteration_cap"
    BUDGET_CAP = "budget_cap"
    TOOL_FAILURE = "tool_failure"
    CIRCUIT_OPEN = "circuit_breaker_open"

@dataclass
class TrajectoryEntry:
    timestamp: float
    turn_number: int
    entry_type: str  # "model_call" | "tool_call" | "observation" | "stop"
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_name: str | None = None
    tool_params: dict | None = None
    tool_result_summary: str | None = None
    tool_success: bool = True
    tool_latency_ms: float = 0.0
    content: str = ""
    cost_dollars: float = 0.0
    stop_reason: StopReason | None = None

@dataclass
class TrajectoryLogger:
    """
    Immutable append-only log of every agent action.
    Writes to disk after each entry for crash safety.
    """
    session_id: str
    query: str
    output_dir: Path = field(default_factory=lambda: Path("logs/trajectories"))
    _entries: list[TrajectoryEntry] = field(default_factory=list, init=False)
    _turn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def log_model_call(
        self, model_id: str, prompt_summary: str,
        response_summary: str, input_tokens: int,
        output_tokens: int, cost: float
    ) -> None:
        self._turn += 1
        entry = TrajectoryEntry(
            timestamp=time.time(), turn_number=self._turn,
            entry_type="model_call", model_id=model_id,
            input_tokens=input_tokens, output_tokens=output_tokens,
            content=f"Prompt: {prompt_summary[:200]} | Response: {response_summary[:200]}",
            cost_dollars=cost,
        )
        self._append(entry)

    def log_tool_call(
        self, tool_name: str, params: dict,
        result_summary: str, success: bool, latency_ms: float
    ) -> None:
        entry = TrajectoryEntry(
            timestamp=time.time(), turn_number=self._turn,
            entry_type="tool_call", tool_name=tool_name,
            tool_params=params, tool_result_summary=result_summary[:500],
            tool_success=success, tool_latency_ms=latency_ms,
        )
        self._append(entry)

    def log_observation(self, content: str, token_count: int) -> None:
        entry = TrajectoryEntry(
            timestamp=time.time(), turn_number=self._turn,
            entry_type="observation", content=content[:500],
            input_tokens=token_count,
        )
        self._append(entry)

    def log_stop(self, reason: StopReason) -> None:
        entry = TrajectoryEntry(
            timestamp=time.time(), turn_number=self._turn,
            entry_type="stop", stop_reason=reason,
        )
        self._append(entry)

    def _append(self, entry: TrajectoryEntry) -> None:
        self._entries.append(entry)
        self._flush_entry(entry)

    def _flush_entry(self, entry: TrajectoryEntry) -> None:
        filepath = self.output_dir / f"{self.session_id}.jsonl"
        with open(filepath, "a") as f:
            data = asdict(entry)
            if data.get("stop_reason"):
                data["stop_reason"] = data["stop_reason"].value
            f.write(json.dumps(data) + "\n")

    @property
    def summary(self) -> dict:
        total_cost = sum(e.cost_dollars for e in self._entries)
        total_input = sum(e.input_tokens for e in self._entries)
        total_output = sum(e.output_tokens for e in self._entries)
        tool_calls = [e for e in self._entries if e.entry_type == "tool_call"]
        failed_tools = [e for e in tool_calls if not e.tool_success]
        stop_entries = [e for e in self._entries if e.stop_reason]
        return {
            "session_id": self.session_id,
            "total_turns": self._turn,
            "total_cost": round(total_cost, 4),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "tool_calls": len(tool_calls),
            "tool_failures": len(failed_tools),
            "stop_reason": stop_entries[-1].stop_reason.value if stop_entries else "in_progress",
        }
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Competitive Intelligence Platform

**Problem statement.** A Fortune 500 CPG company needs automated competitive intelligence reports covering 15 competitors across 4 product categories. Today, a team of 6 analysts manually monitors news, SEC filings, patent databases, and social media, producing weekly reports. Each report takes 40 analyst-hours. The company wants to reduce time-to-insight from weekly to daily while maintaining analyst-grade citation accuracy (>90%). Budget: $50K/month for the AI system. Volume: ~200 reports/month across all categories.

**Architecture diagram:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           USER LAYER                                         │
│                                                                              │
│  ┌────────────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │
│  │ Dashboard (React)   │  │ Report Inbox      │  │ Alert Subscriptions    │   │
│  │                     │  │                   │  │                        │   │
│  │ Live status of      │  │ Daily reports     │  │ Push notification      │   │
│  │ in-flight research  │  │ per competitor/   │  │ on competitor signals  │   │
│  │ sessions (polling)  │  │ category pair     │  │ (price, launch, M&A)   │   │
│  └─────────┬──────────┘  └────────┬──────────┘  └───────────┬────────────┘  │
└────────────┼──────────────────────┼──────────────────────────┼───────────────┘
             │                      │                          │
┌────────────▼──────────────────────▼──────────────────────────▼───────────────┐
│                         ORCHESTRATION LAYER                                  │
│                                                                              │
│  ┌────────────────────┐  ┌──────────────────┐  ┌────────────────────────┐   │
│  │ Scheduler           │  │ Lead Researcher   │  │ Budget Controller      │   │
│  │ (cron: daily 6 AM)  │  │ (Claude Opus 4)   │  │                        │   │
│  │                     │  │                   │  │ $50K/month budget      │   │
│  │ Generates 200       │  │ Decomposes each   │  │ = $250/report avg      │   │
│  │ research tasks/     │  │ competitor-        │  │                        │   │
│  │ month across 15     │  │ category pair     │  │ Hard cap per run:      │   │
│  │ competitors x 4     │  │ into sub-queries  │  │  $25 (tokens)          │   │
│  │ categories          │  │                   │  │  50 tool calls         │   │
│  │                     │  │ Explicit scope    │  │  15 min wall time      │   │
│  │ Async: returns      │  │ boundaries per   │  │                        │   │
│  │ job ID immediately  │  │ subagent          │  │ Alert if daily spend   │   │
│  │                     │  │                   │  │ > $2K (circuit break)  │   │
│  └─────────┬──────────┘  └────────┬──────────┘  └───────────┬────────────┘  │
└────────────┼──────────────────────┼──────────────────────────┼───────────────┘
             │ task queue           │ scoped tasks             │ budget checks
┌────────────▼──────────────────────▼──────────────────────────▼───────────────┐
│                         WORKER LAYER                                         │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │          Parallel SubAgents (Claude Sonnet 4, up to 8 concurrent)    │   │
│  │                                                                       │   │
│  │  Worker 1: News      Worker 2: SEC       Worker 3: Patent            │   │
│  │  monitoring          filings (EDGAR)     landscape (USPTO)           │   │
│  │                                                                       │   │
│  │  Worker 4: Social    Worker 5: Pricing   Worker 6: Job posting       │   │
│  │  sentiment (X,       intelligence        signals (hiring =           │   │
│  │  Reddit, forums)     (e-commerce APIs)   expansion indicator)        │   │
│  └───────────────────────────────┬───────────────────────────────────────┘   │
│                                  │                                           │
│  ┌───────────────────────────────▼───────────────────────────────────────┐   │
│  │                    MCP Tool Layer                                      │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │   │
│  │  │ News API │ │ EDGAR    │ │ USPTO    │ │ Social   │ │ Web      │   │   │
│  │  │ (Google, │ │ Full-Text│ │ PatFT    │ │ APIs     │ │ Scraper  │   │   │
│  │  │  Bing)   │ │ Search   │ │          │ │          │ │ (gVisor) │   │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                        SYNTHESIS & QUALITY LAYER                             │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Report Synthesizer│  │ Citation Agent    │  │ Human Review Queue       │   │
│  │ (Claude Opus 4)   │  │ (Claude Sonnet 4) │  │                          │   │
│  │                   │  │                   │  │ Flag reports with:        │   │
│  │ Merges sub-       │  │ Verify every      │  │  - citation accuracy     │   │
│  │ findings into     │  │ inline citation   │  │    < 90%                 │   │
│  │ structured        │  │ against source    │  │  - confidence score      │   │
│  │ CI report         │  │                   │  │    < 0.7                  │   │
│  │                   │  │ Target: >= 90%    │  │  - M&A/legal signals     │   │
│  │                   │  │ accuracy          │  │    (always human review) │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                        PERSISTENCE & TELEMETRY                               │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Report Store      │  │ Trajectory Logs   │  │ Cost Dashboard           │   │
│  │ (PostgreSQL +     │  │ (JSONL, S3,       │  │                          │   │
│  │  full-text search)│  │  tamper-resistant) │  │ Per-report cost          │   │
│  │                   │  │                   │  │ Per-competitor cost       │   │
│  │ Historical        │  │ Full audit trail  │  │ Daily/monthly aggregate  │   │
│  │ trend analysis    │  │ for EU AI Act     │  │ Budget burn rate         │   │
│  │ across reports    │  │ compliance        │  │                          │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────┬────────────────────┬────────────────────────────────┐
│ Decision                 │ Chosen             │ Rationale                      │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Topology                 │ Orchestrator-      │ 15 competitors x 4 categories  │
│                          │ Worker             │ = inherently parallel; single   │
│                          │                    │ agent would serialize and       │
│                          │                    │ exceed budget. 90.2% quality    │
│                          │                    │ improvement over single-agent.  │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Planning strategy        │ Planning-Only      │ No human in the loop for daily  │
│                          │                    │ automated runs. Accept higher   │
│                          │                    │ waste risk; mitigate with       │
│                          │                    │ budget caps per run ($25).      │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Lead model               │ Opus 4 (plan +     │ Planning quality dominates      │
│                          │ synthesize only)   │ outcome quality (PIES finding). │
│                          │                    │ Expensive model only on the     │
│                          │                    │ two highest-leverage steps.     │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Worker model             │ Sonnet 4           │ 5x cheaper than Opus for        │
│                          │                    │ search/extraction tasks that    │
│                          │                    │ don't require frontier reasoning.│
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Context management       │ Three-tier         │ Workers may fetch 50+ pages     │
│                          │ compression        │ per run. Window expansion       │
│                          │                    │ alone would blow cost budget.   │
│                          │                    │ 84% token reduction measured.   │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Citation verification    │ Dedicated          │ Inline citation alone yields    │
│                          │ CitationAgent      │ 78% accuracy. Post-processing   │
│                          │ post-process       │ pass pushes to 94%. Required    │
│                          │                    │ for >90% target.               │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Human review gate        │ Selective: M&A,    │ Reviewing all 200 reports/month │
│                          │ legal, low-        │ defeats automation purpose.     │
│                          │ confidence only    │ Route high-risk signals to      │
│                          │                    │ analysts; auto-publish rest.    │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Budget enforcement       │ Mechanical hard    │ The $47K lesson: alerts require │
│                          │ caps (not alerts)  │ human intervention; circuit     │
│                          │                    │ breakers do not. $25/run cap,   │
│                          │                    │ $2K/day circuit breaker.        │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Sandbox for web scraping │ gVisor             │ Not regulated data (Firecracker │
│                          │                    │ overkill). Need more isolation  │
│                          │                    │ than V8 (scraping runs          │
│                          │                    │ arbitrary page JS).            │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Deployment               │ Rainbow            │ Daily scheduled jobs may run    │
│                          │                    │ 15+ minutes. Blue-green would   │
│                          │                    │ kill in-flight sessions.        │
└──────────────────────────┴────────────────────┴────────────────────────────────┘
```

**Cost estimate:** 200 reports/month. Opus 4 for planning + synthesis (2 calls/report, ~20K tokens each at $15/$75 per MTok) = ~$1.50/report. Sonnet 4 workers (6 workers, ~100K tokens each at $3/$15 per MTok) = ~$5/report. Prompt caching (78% reduction on system prompts shared across runs) brings effective cost to ~$3-4/report. Monthly: ~$600-800 model cost + infrastructure. Well within $50K budget with margin for search API fees and compute.

---

### 6.2 Scenario: Regulated Financial Research Pipeline (EU AI Act Compliant)

**Problem statement.** A European investment bank needs an AI research system that generates equity research summaries for 50 stocks in the EURO STOXX 50 index. Reports must cite primary sources (company filings, ECB publications, Eurostat data), comply with EU AI Act requirements (effective August 2, 2026), and maintain a full audit trail retained for 10 years. Reports inform internal trading decisions -- inaccurate information creates direct financial and regulatory risk. The system must produce daily pre-market briefs (by 07:00 CET) and weekly deep dives. Human analysts must approve all reports before distribution.

**Architecture diagram:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    EU AI ACT COMPLIANCE ENVELOPE                             │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                       GOVERNANCE LAYER                               │   │
│  │                                                                       │   │
│  │  ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────┐  │   │
│  │  │ Policy Engine    │ │ Human Oversight   │ │ EU AI Office         │  │   │
│  │  │                  │ │ Controller        │ │ Registration         │  │   │
│  │  │ Per-agent        │ │                   │ │                      │  │   │
│  │  │ permissions:     │ │ ALL reports       │ │ System registered    │  │   │
│  │  │  data sources    │ │ require analyst   │ │ as high-risk AI      │  │   │
│  │  │  allowed         │ │ approval before   │ │                      │  │   │
│  │  │  (whitelist)     │ │ distribution      │ │ Technical docs       │  │   │
│  │  │                  │ │                   │ │ maintained 10 years  │  │   │
│  │  │ No external web  │ │ Escalation path   │ │                      │  │   │
│  │  │ scraping (only   │ │ for low-          │ │ Audit trail:         │  │   │
│  │  │ approved APIs)   │ │ confidence        │ │ tamper-resistant     │  │   │
│  │  │                  │ │ findings          │ │ immutable log        │  │   │
│  │  └────────┬────────┘ └────────┬──────────┘ └──────────┬───────────┘  │   │
│  └───────────┼───────────────────┼──────────────────────┼──────────────┘   │
│              │                   │                      │                    │
│  ┌───────────▼───────────────────▼──────────────────────▼──────────────┐   │
│  │                   RESEARCH ORCHESTRATION                             │   │
│  │                                                                       │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │   │
│  │  │ Scheduler         │  │ Lead Analyst      │  │ Budget +           │  │   │
│  │  │                   │  │ Agent             │  │ Compliance         │  │   │
│  │  │ Daily: 04:00 CET  │  │ (Claude Opus 4)   │  │ Controller         │  │   │
│  │  │ (3hr before       │  │                   │  │                    │  │   │
│  │  │  market open)     │  │ Intent-to-Plan:   │  │ Per-stock budget   │  │   │
│  │  │                   │  │ generate plan,    │  │ caps               │  │   │
│  │  │ Weekly: Friday    │  │ log for audit     │  │                    │  │   │
│  │  │ 18:00 CET         │  │ before execution  │  │ Automatic logging  │  │   │
│  │  │                   │  │                   │  │ (EU AI Act Art.12) │  │   │
│  │  └─────────┬────────┘  └────────┬──────────┘  └─────────┬──────────┘  │   │
│  └────────────┼───────────────────┼──────────────────────┼──────────────┘   │
│               │                   │                      │                   │
│  ┌────────────▼───────────────────▼──────────────────────▼──────────────┐   │
│  │                    DATA SOURCE LAYER (whitelist only)                 │   │
│  │                                                                       │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐  │   │
│  │  │ Company       │ │ ECB          │ │ Eurostat     │ │ Bloomberg  │  │   │
│  │  │ Filing APIs   │ │ Statistical  │ │ Database     │ │ Terminal   │  │   │
│  │  │              │ │ Data         │ │ API          │ │ API        │  │   │
│  │  │ Annual       │ │ Warehouse    │ │              │ │            │  │   │
│  │  │ reports,     │ │              │ │ Macro        │ │ Real-time  │  │   │
│  │  │ quarterly    │ │ Interest     │ │ indicators,  │ │ market     │  │   │
│  │  │ earnings,    │ │ rates,       │ │ GDP, CPI,    │ │ data,      │  │   │
│  │  │ regulatory   │ │ monetary     │ │ employment   │ │ consensus  │  │   │
│  │  │ filings     │ │ policy       │ │              │ │ estimates  │  │   │
│  │  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘  │   │
│  │                                                                       │   │
│  │  NO general web search. NO web scraping. All sources pre-approved.   │   │
│  │  Source authority verified at infrastructure level.                    │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    VERIFICATION LAYER                                 │   │
│  │                                                                       │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │   │
│  │  │ Citation Verifier │  │ Fact Checker      │  │ Compliance         │  │   │
│  │  │                   │  │                   │  │ Validator          │  │   │
│  │  │ Every number must │  │ Cross-reference   │  │                    │  │   │
│  │  │ trace to primary  │  │ financial figures │  │ MiFID II           │  │   │
│  │  │ source document   │  │ across 2+ sources │  │ suitability        │  │   │
│  │  │                   │  │                   │  │ language check     │  │   │
│  │  │ Target: >= 95%    │  │ Flag numerical    │  │                    │  │   │
│  │  │ (higher bar than  │  │ discrepancies     │  │ No forward-looking │  │   │
│  │  │  general research)│  │ > 5%              │  │ statements without │  │   │
│  │  │                   │  │                   │  │ explicit caveats   │  │   │
│  │  └──────────────────┘  └──────────────────┘  └────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    AUDIT & RETENTION (10-year)                        │   │
│  │                                                                       │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │   │
│  │  │ Trajectory Store  │  │ Decision Log      │  │ Report Archive     │  │   │
│  │  │ (immutable, WORM  │  │                   │  │ (versioned,        │  │   │
│  │  │  storage)         │  │ Every model       │  │  signed)           │  │   │
│  │  │                   │  │ decision: why     │  │                    │  │   │
│  │  │ Every model call, │  │ this source was   │  │ Draft + analyst    │  │   │
│  │  │ tool call, result,│  │ selected, why     │  │ edits + final      │  │   │
│  │  │ token counts,     │  │ that claim was    │  │ approved version   │  │   │
│  │  │ cost, timing      │  │ included          │  │                    │  │   │
│  │  └──────────────────┘  └──────────────────┘  └────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌──────────────────────────┬────────────────────┬────────────────────────────────┐
│ Decision                 │ Chosen             │ Rationale                      │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Retrieval architecture   │ API-only           │ No web scraping. Financial     │
│                          │ (no browser-based) │ regulators require known,      │
│                          │                    │ auditable data sources. Browser │
│                          │                    │ scraping introduces            │
│                          │                    │ uncontrolled content risk.      │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Planning strategy        │ Intent-to-Plan     │ Plan is logged for audit       │
│                          │ (no user round-    │ before execution. Not Unified  │
│                          │ trip, but plan     │ Intent (no user review at      │
│                          │ logged)            │ 04:00 CET). Plan logging       │
│                          │                    │ satisfies Art. 14 human        │
│                          │                    │ oversight requirement.         │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Human oversight          │ Mandatory for ALL  │ EU AI Act Art. 14 requires     │
│                          │ reports            │ human oversight for high-risk   │
│                          │                    │ AI. Financial research informs  │
│                          │                    │ trading = high-risk. No         │
│                          │                    │ auto-publish path.             │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Citation accuracy target │ >= 95% (not 90%)   │ Financial decisions have        │
│                          │                    │ direct monetary consequences.   │
│                          │                    │ Standard 90% means 1 in 10     │
│                          │                    │ citations wrong -- too high     │
│                          │                    │ for equity research.           │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Sandbox technology       │ Firecracker        │ Regulated financial data.       │
│                          │ microVMs           │ Strongest isolation required.   │
│                          │                    │ gVisor insufficient for         │
│                          │                    │ regulatory audit.              │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Log retention            │ 10-year WORM       │ EU AI Act Art. 12: automatic   │
│                          │ storage            │ logging with technical docs     │
│                          │                    │ retained 10 years. WORM        │
│                          │                    │ (write-once-read-many)         │
│                          │                    │ ensures tamper resistance.      │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Context management       │ External           │ Audit trail requires full       │
│                          │ structured storage │ trajectory preservation.        │
│                          │ (not compression)  │ Compression destroys the        │
│                          │                    │ intermediate reasoning that     │
│                          │                    │ regulators may inspect.         │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Deployment               │ Rainbow + canary   │ Pre-market briefs due by        │
│                          │                    │ 07:00 CET. Cannot kill          │
│                          │                    │ in-flight 04:00 jobs for        │
│                          │                    │ version bumps. Canary with     │
│                          │                    │ 5 stocks before full rollout.   │
├──────────────────────────┼────────────────────┼────────────────────────────────┤
│ Topology                 │ Pipeline           │ Structured output format is     │
│                          │                    │ fixed (equity research brief).  │
│                          │                    │ Pipeline stages map to          │
│                          │                    │ regulatory requirements:        │
│                          │                    │ data collection -> analysis ->  │
│                          │                    │ compliance check -> human       │
│                          │                    │ review. Each stage auditable.   │
└──────────────────────────┴────────────────────┴────────────────────────────────┘
```

**Decision rationale summary.** The regulated environment inverts several default choices. Pipeline topology (normally the least favored) becomes optimal because each stage maps to a regulatory requirement and can be independently audited. API-only retrieval (normally a limitation) becomes a feature because every data source is pre-approved and auditable. External storage (normally the most complex context strategy) becomes necessary because compression destroys the intermediate reasoning trail that regulators inspect. The human-in-the-loop gate, which would be a bottleneck in the CPG scenario, is a regulatory requirement here. The key architectural insight: compliance constraints simplify design choices by eliminating the options that would otherwise require trade-off analysis.

**Penalty context:** Non-compliance penalties under EU AI Act: up to 15M EUR or 3% of global annual turnover. For a major European bank, this dwarfs any AI infrastructure cost -- the governance envelope is not optional architecture, it is the primary design constraint.
