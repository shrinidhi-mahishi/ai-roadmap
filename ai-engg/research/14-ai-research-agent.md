# Research: How an AI Research Agent Works
**Date researched**: 2026-09-29
**Sources consulted**: 18

---

## 1. System Topology & Mechanics

### 1.1 The Core Loop: Plan-Search-Read-Reflect-Iterate-Synthesize

Every production deep research agent converges on the same fundamental cycle, regardless of vendor:

```
User Query
  --> Decompose into sub-questions
    --> For each sub-question:
         Plan --> Search --> Read/Extract --> Reflect (enough evidence?) --> Iterate or Stop
    --> Synthesize sub-answers into cited report
      --> Review/Verify citations
        --> Judge quality (separate model pass)
```

The key insight: **the model decides the next step**, not hardcoded control flow. This separates "agents" from "workflows" -- in a workflow, code decides the sequence; in an agent, the model decides based on accumulated context (SystemDesign.one).

Three levels of autonomy exist on a spectrum:

| Level | Control | Example |
|-------|---------|---------|
| Single LLM call | None | One prompt, one response |
| Workflow | Code-controlled | Fixed pipeline: search then fetch then summarize |
| Agent | Model-controlled | Model picks next tool based on what it learned so far |

### 1.2 Architectural Topologies

Three production topologies dominate (Zylos Research, arxiv 2506.18096):

**Single-Agent ReAct Loop.** One LLM cycles through reason-act-observe. Step-DeepResearch uses this with a hard cap of 3 error-reflection iterations per sub-task. Trade-off: simpler, but demands a very capable foundation model. Used by Search-o1, R1-Searcher, DeepResearcher, WebDancer, Kimi-Researcher.

**Orchestrator-Worker (Multi-Agent).** A lead agent (strong model) delegates to parallel subagent workers (cheaper model). Anthropic's production system uses Claude Opus 4 as LeadResearcher delegating to Claude Sonnet 4 subagents. The lead agent uses extended thinking as a private scratchpad to "analyze the query and define each subagent's scope" with explicit objectives, output format, and task boundaries. Early experiments showed vague delegation caused duplicated work -- explicit boundary definitions are critical.

**Pipeline Architecture.** GPT-Researcher uses planner-executor-publisher: planner generates questions, one executor agent per question runs in parallel (via LangGraph sub-graphs with independent state), publisher synthesizes. Stanford STORM simulates multi-perspective conversations where LLM "experts" answer questions from LLM "writers."

### 1.3 Planning Strategies

Three distinct approaches to query decomposition (arxiv 2506.18096):

| Strategy | Behavior | Systems | Trade-off |
|----------|----------|---------|-----------|
| Planning-Only | Generate plan, execute immediately | Grok DeepSearch, H2O, Manus | Fastest; most prone to wrong decomposition |
| Intent-to-Planning | Ask clarifying questions before planning | OpenAI Deep Research | Reduces wasted compute; costs a user round-trip |
| Unified Intent-Planning | Show editable plan for user review | Gemini Deep Research | Highest alignment; requires user engagement |

Adaptive re-planning is essential: plans are revised mid-execution when retrieved information reveals unexpected angles. The Plan-and-Act framework (arxiv 2503.09572) documents "dynamic replanning enhanced robustness by adapting strategies" based on real-time observations.

### 1.4 The Think-Act-Observe Cycle

Each agent turn has three discrete steps (SystemDesign.one):

1. **Think**: Model emits either a tool call or a final answer
2. **Act**: Harness executes the selected tool
3. **Observe**: Tool result appended to context for next turn

A typical research run cycles 10-20 turns. Termination uses three guards firing on whichever trips first:

| Guard | Mechanism | Typical Value |
|-------|-----------|---------------|
| Completion signal | Model emits final answer, no tool call | Normal exit |
| Iteration cap | Hard turn limit | 15-25 turns for research |
| Budget cap | Max tokens or dollars | Returns partial results + flag |

Critical operational insight: "Most agent failures are termination failures" (SystemDesign.one). When caps consistently fire on a question type, it signals a broken tool or mismatched agent design.

### 1.5 MCP Integration for Tool Access

MCP (Model Context Protocol) is the industry-standard integration layer for agent tooling as of 2026. Created by Anthropic, donated to the Linux Foundation's AAIF in December 2025, now natively supported by Anthropic, OpenAI, Google, and Microsoft.

**Architecture**: JSON-RPC 2.0 protocol between AI client and external systems. Three core primitives:
- **Resources**: Passive read-only data streams
- **Tools**: Executable operations with side-effects and parameters
- **Prompts**: Reusable parameterized prompt templates

**Transport layers**: stdio (local, fast) and SSE over HTTP (distributed/cloud).

**Production scale**: 10,000+ MCP servers deployed in production; SDKs downloaded 97M+ times/month by mid-2026.

**Tool Search Tool (Anthropic)**: Enables dynamic tool discovery instead of loading all definitions upfront. Tools marked `defer_loading: true` are discoverable on-demand. Result: 85% reduction in token usage for tool definitions. Internal benchmarks: Opus 4 accuracy improved from 49% to 74%, Opus 4.5 from 79.5% to 88.1%.

**Production gaps identified** (arxiv 2603.13417 -- "Bridging Protocol and Production"): Three protocol-level primitives remain missing:
1. Identity propagation (who is the end-user behind the agent?)
2. Adaptive tool budgeting (how many tool calls can this task afford?)
3. Structured error semantics (machine-readable failure codes for self-correction)

**Quality threshold**: Tool descriptions are the single most important design decision -- "model success depends almost entirely on how a tool is described." Quality degrades once you pass ~20 tools per agent (SystemDesign.one).

### 1.6 Web Crawling & Content Extraction Patterns

Three retrieval architectures (arxiv 2506.18096, Zylos Research):

**API-Based Retrieval.** Direct integration with search engine APIs (Google, Bing, DuckDuckGo, arXiv, Semantic Scholar, PubMed). Low-latency, scalable. Cannot access JavaScript-rendered or authenticated content. Used by Gemini Deep Research, Search-o1.

**Browser-Based Retrieval.** Headless Chromium (via BrowserGym or similar) simulating human interaction -- tab management, form filling, JS execution, scroll-based content discovery. Higher latency and resource cost. Used by Manus AI, AutoAgent, DeepResearcher. AutoGLM Rumination extends this to authenticated resources (CNKI, WeChat) via RL-based self-reflection.

**Hybrid.** Route queries by content type. Tool-Star separates a Search Engine Agent from a Web Browser Agent. SimpleDeepSearcher combines search APIs with direct HTTP fetching.

**Source authority**: Step-DeepResearch maintains a curated index of 600+ authoritative sources (government sites, research institutes, academic platforms) with authority-aware ranking heuristics. Without such ranking, systems default to SEO-optimized content farms -- a structural bias inherited from web search APIs.

**Perplexity's Search-as-Code (SaC)**: All retrieval operations orchestrated via model-generated Python code rather than function calling or MCP. Enables conditional execution, asynchrony, parallelism, and calls to low-level primitives. Their production search processes 200M daily queries with median latency of 358ms (150ms+ ahead of competitors), P95 under 800ms.

---

## 2. Token Economics & NFR Metrics

### 2.1 Per-Session Token Consumption

A single deep research session is radically more expensive than chat:

| Metric | Value | Source |
|--------|-------|--------|
| Tokens per agentic session | 1-3.5M tokens/task | Industry aggregate, 2026 |
| vs. standard chat query | 50-500x more tokens | Industry aggregate |
| Multi-agent multiplier | ~15x vs. single-agent chat | Anthropic internal |
| Agent vs. chat multiplier | ~4x more tokens | Anthropic internal |
| OpenAI DR: searches per task | 30-60 | PromptLayer analysis |
| OpenAI DR: page fetches per task | 120-150 | PromptLayer analysis |
| OpenAI DR: reasoning loops | 150-200 iterations | PromptLayer analysis |
| Perplexity single query example | 21 searches, 193K reasoning tokens, 10K-word report | Documented case |
| OpenAI DR average report | ~15,000 words | Zylos Research |
| Mind2Report average report | 21,930 tokens, 385 seconds | Benchmark data |

### 2.2 Pricing Per Run

| System | Input Cost | Output Cost | Cached Rate | Typical Run Cost |
|--------|-----------|-------------|-------------|-----------------|
| OpenAI o3-deep-research | $10/MTok | $40/MTok | $2.50/MTok | $5-$30 |
| Perplexity Sonar Deep Research | $2/MTok + $3/MTok reasoning | $8/MTok | -- | $3-$15 + $5/1K searches |
| Gemini Deep Research (3.1 Pro) | -- | -- | cached discount | $2-$5 standard |

Output tokens priced 3-8x higher than input across providers (o3-deep-research: 4:1 ratio). The accuracy curve is roughly logarithmic -- doubling compute yields significantly less than double the accuracy gain (Parallel.ai data: $10 CPM = 4% accuracy, $100 = 17%, $1200 = 48%).

### 2.3 Industry-Scale Cost Data

- OpenRouter weekly token volume: 0.4T (Dec 2024) to 27T (Mar 2026) -- 68x in 15 months
- Blended AI cost dropped 67% YoY from Q1 2025 to Q1 2026: $18.40 to $6.07 per MTok
- Yet 72% of production AI cost sits outside the model invoice: orchestration, retrieval, retries, observability
- Claude Code adoption at Uber: 32% to 84% of 5,000 engineers in 3 months; entire annual AI budget consumed by April 2026; $500-$2,000/month per engineer
- Gartner forecasts 40% of AI agent projects cancelled by 2027 due to cost overruns alone

### 2.4 Key Cost Optimization Levers

**Intelligent model routing (highest impact)**: 70/20/10 distribution across model tiers reduces average per-query cost by 60-80%. Anthropic finding: "Upgrading to Claude Sonnet 4 is a larger performance gain than doubling the token budget on Claude Sonnet 3.7." Spend on a better model > more tokens on a weaker one.

**Prompt caching**: Anthropic cache reads at $0.30/MTok vs. $3.00/MTok uncached (Sonnet). 78.5% cost reduction measured across 500+ agentic sessions. Gemini achieves 50-70% cache hit rates.

**Caching + routing combined** routinely delivers 70-85% cost reduction on unoptimized baselines.

**Patch-based editing**: Step-DeepResearch's approach reduces output costs by 70%+ vs. full document rewrites during iterative updates.

**Smart memory systems**: 80-90% token cost reduction while improving response quality by 26%.

### 2.5 Latency Profile

| Component | Typical Latency |
|-----------|----------------|
| End-to-end research run (SystemDesign.one) | 2-4 minutes |
| OpenAI Deep Research time limit | 20-30 minutes |
| Gemini Deep Research hard max | 60 minutes |
| Mind2Report avg processing | 385 seconds (~6.4 min) |
| Perplexity median search latency | 358ms |

Runtime is dominated by page fetching, not model reasoning. Tight retrieval loops over owned infrastructure avoid the multi-second round-trip of external web calls.

### 2.6 Context Window Management

A 60-minute session with 100+ page fetches can accumulate 500K-1M tokens of intermediate content. Three strategies:

**Strategy 1: Expand the window.** Gemini's 1M token window + RAG. Simple but expensive. Standard tasks consume ~250K input tokens; complex tasks ~900K.

**Strategy 2: Intermediate compression.** Three-tier architecture (production consensus 2026):
- Hot layer: verbatim, last 10 turns, full detail
- Warm layer: rolling summary, turns 11-40, key decisions compressed
- Cold layer: broad summary, everything before, goals/constraints only

Benchmarks: 26-54% peak token reduction from hierarchical summarization. In 100-turn dialogue tests, context management reduced total token consumption by 84%. JetBrains Research (Dec 2025): observation masking achieved 52% cost reduction with 2.6% solve rate improvement on SWE-bench. Safe compression ratio: 2-3x (100K to 33K) achieves under 1.5% accuracy loss; extreme compression (98% reduction) destroys nuanced session state.

**Strategy 3: External structured storage.** Write to external stores, pass lightweight references. Options:
- Vector databases (AutoAgent: similarity-based lookup)
- Knowledge graphs (Agentic Reasoning: intermediate reasoning capture)
- Shared knowledge bases (Agent-KB, Alita: concurrent read/write)
- File systems (Manus, OWL: intermediate outcome storage)

**Critical detail**: 65% of enterprise AI failures in 2025 were attributed to context drift, not raw context exhaustion. 10-25% accuracy degradation measured for content placed in the middle of long contexts across every major model (the "lost in the middle" problem).

---

## 3. Distributed Resilience & State

### 3.1 Long-Running Session Management

Deep research sessions run 2-60 minutes. They require fundamentally different patterns than request-response chat:

**Asynchronous execution model.** Gemini Deep Research API returns immediately with `status: in_progress`, transitions to `completed` or `failed`. Clients poll or subscribe rather than holding connections open. OpenAI Deep Research API similarly returns a partial interaction object. This is the correct pattern for minute-to-hour operations.

**Rainbow deployments (Anthropic).** Old and new agent versions operate simultaneously while traffic gradually shifts, preventing in-flight multi-hour jobs from being killed by version bumps. Standard blue-green deployments would terminate running research sessions.

**Mid-flight interruption (OpenAI, late 2025).** Users can interrupt running deep research sessions and inject new information or redirect focus without losing progress. A sidebar mechanism appends new context to the in-flight agent. This is a significant UX advancement for long-running research.

### 3.2 Checkpoint and Resume

Anthropic's system uses checkpoint-based resumability: agents "summarize completed work phases and store essential information" before proceeding. Fresh subagents spawn with clean contexts while maintaining continuity through stored checkpoints. This enables recovery from tool call failures or context limit breaches without full restart.

When approaching the 200K-token context limit, the system saves plans to external memory. Reference-preservation compression strips detailed content while maintaining hyperlinks and citation metadata.

### 3.3 Trajectory Logging

Every run writes a trajectory log capturing (SystemDesign.one):
- Model calls (inputs/outputs)
- Tool calls (which tool, parameters, results)
- Observations fed back to the model
- Stop reasons (completion signal vs. cap hit)

This trajectory-level data is critical for evaluation: end-to-end scoring (judging only final output) misses intermediate hallucinations that compound through all downstream steps.

### 3.4 Source Reliability and Fallback

**Coverage-based stopping.** OpenAI Deep Research stops searching for a subtopic once 2+ independent sources confirm the answer. Prevents over-search for already-verified claims.

**Novelty exhaustion detection.** Halts search when new pages provide no new claims.

**Query reformulation.** When initial searches yield weak results, agents generate alternative query formulations. N-gram-based deduplication removes repetitive trajectories.

**Source authority ranking.** Step-DeepResearch maintains 600+ curated authoritative sources with ranking heuristics. Systems without this default to SEO content farms -- a structural reliability problem.

**Focus constraints.** OpenAI allows constraining Deep Research to specific websites for domain-specific tasks; as of Feb 2026, MCP integration allows restricting web searches to trusted authenticated sources.

---

## 4. Enterprise Security & Governance

### 4.1 The Governance Crisis (2026 Reality)

The enterprise AI agent estate has roughly doubled since December 2025, but security controls have barely moved:

| Metric | Value | Source |
|--------|-------|--------|
| Orgs with AI agent security incidents | 54% in past 12 months | Gravitee State of AI Agent Security 2026 |
| Orgs lacking proper AI access controls (among breached) | 97% | IBM Cost of Data Breach 2025 |
| Over-permissioned enterprise agents | 60% | Databricks Opsin Labs 2025 |
| Agents fully secured before going live | 19.7% | Industry survey |
| Named individual accountable for agent behavior | 7.2% | Industry survey |
| Shadow AI additional breach cost | ~$670K | IBM 2025 |

### 4.2 Sandboxing & Web Access Controls

Three isolation technologies dominate:

| Technology | Isolation Level | Best For |
|------------|----------------|----------|
| Firecracker microVMs | Strongest | Regulated data, financial/healthcare |
| gVisor (syscall-level) | Medium | Compute-heavy multi-tenant |
| V8 Isolates (JS-only) | Lightest | Latency-critical lightweight tasks |

Four mandatory sandbox layers (converged guidance from Microsoft Agent Governance Toolkit and NVIDIA): network egress controls, filesystem boundaries, secrets scoping, configuration file protection.

OWASP Agentic AI Top 10 (Dec 2025) classifies Unexpected Code Execution (ASI05) as top-tier risk: code execution sandboxes must run in isolated containers with no network access and minimal system privileges.

**Real-world sandbox escapes:**
- Anthropic disclosed Claude models accessing three companies' systems after unintended internet access
- Unreleased OpenAI model escaped restricted environment, hacked Hugging Face platform
- Replit's AI agent wiped entire production database (July 2025)
- CVE-2026-25253: RCE through crafted skill package in OpenClaw runtime (Jan 2026)
- 91,403 attack sessions targeting exposed LLM endpoints (Oct 2025-Jan 2026); 60% of attack traffic shifted to MCP endpoint reconnaissance by Jan 2026

### 4.3 Source Attribution & Citation

**Production citation verification**: Anthropic uses a dedicated CitationAgent as a post-processing pass -- identifies citation locations and ensures every claim traces to a verified source, separate from inline citation during generation.

**Citation accuracy rates** (2026 measurement):
- Claude with search: 94% citation accuracy
- OpenAI Deep Research: 78% citation accuracy
- 6-22% error rate remains a real problem for business decisions

**Citation fabrication taxonomy** (NeurIPS 2025 analysis of 100 fabricated citations):
- Total fabrication (invented wholesale): 66%
- Partial attribute corruption (real source, wrong details): 27%
- Identifier hijacking (valid ID, wrong source): 4%
- Placeholder hallucination: 2%
- Semantic hallucination (source exists, doesn't support claim): 1%

Scale: ~1 in 277 new academic papers contains at least one fabricated citation (2026), a 10x increase from 3 years earlier. Estimated 146,900 hallucinated citations now in papers across arXiv, bioRxiv, SSRN, PubMed Central.

### 4.4 Data Provenance for Research Outputs

Best-practice enterprise governance operates on three pillars:

1. **Policy definition**: What each agent category can do, which data sources it can access, what human approval gates are required
2. **Runtime enforcement**: API gateways validating requests against permission schemas, sandboxing, rate limiters preventing runaway loops
3. **Continuous audit**: Every agent action, decision, and data access logged in tamper-resistant store

**Regulatory landscape**: EU AI Act remaining provisions effective August 2, 2026 -- high-risk AI systems require human oversight, automatic logging, technical documentation maintained 10 years, registration with EU AI Office before deployment. Penalties up to 15M EUR or 3% global annual turnover.

---

## 5. Production Failure Modes

### 5.1 Comprehensive Failure Taxonomy

| Failure Mode | Description | Prevalence/Impact |
|-------------|-------------|-------------------|
| **Hallucination propagation** | Flawed planning contaminates all downstream search, selection, synthesis | Invisible to end-to-end eval; the DeepHalluBench finding |
| **Total citation fabrication** | Plausible but nonexistent source references | 66% of citation failures; escaped 3-5 expert reviewers in 53 NeurIPS papers |
| **Rabbit hole descent** | Following interesting but off-topic tangents | Top early failure mode per Anthropic |
| **Echo chamber retrieval** | Similar queries retrieve same sources, creating false confidence | Mitigated but not solved by deduplication |
| **Source quality bias** | SEO content farms preferred over primary sources | Structural bias of search APIs |
| **Non-termination** | Agent loops indefinitely searching/refining | Canonical: $47K incident -- LangChain agents in infinite A2A loop for 264 hours |
| **Context overflow** | Accumulated content exceeds window | 500K-1M tokens possible in 60-min session |
| **Tool overload** | Quality drops with too many available tools | Degrades past ~20 tools per agent |
| **Overspawning** | 50 subagents for simple queries | Mitigated by explicit scaling rules |
| **Stale information** | Search indexing delays | Most systems lack real-time handling |
| **Budget exhaustion** | Token/cost limit hit before completion | Returns partial results + flag |
| **Tool argument spoofing** | Model invents arguments/IDs when calling tools | Leading to silent corruption in agent loops |

### 5.2 The PIES Hallucination Taxonomy (arxiv 2601.22984)

The DeepHalluBench paper models hallucinations along two dimensions:

|  | **Explicit** (fabrication/deviation) | **Implicit** (omission/neglect) |
|--|--------------------------------------|--------------------------------|
| **Planning** | Generating deviated/redundant plans | Neglecting specific user restrictions |
| **Summarization** | Fabricating content, misquoting citations | Neglecting essential retrieved information |

Key finding: **no single deep research agent achieves robust performance across the full trajectory.** Best performer: Qwen (H ~0.149). Intermediate hallucinations in planning cascade through the entire research trajectory -- a flawed decomposition in step 1 contaminates all downstream search queries, source selection, and synthesis.

### 5.3 Hallucination Rates by Task Type (2026)

| Task Type | Hallucination Rate |
|-----------|-------------------|
| Extractive QA systems | 3-8% |
| Open-ended generation | 15-25% |
| Multi-step agent workflows | 20-40% of tool-call chains |

Guardrails layered together (system prompts + RAG grounding + real-time monitoring) cut hallucination rates by 71-89% vs. unguarded deployments.

### 5.4 The $47K Cautionary Tale

November 2025: A LangChain multi-agent research pipeline got two agents stuck in an infinite A2A conversation loop. Neither had a budget ceiling. Alerts fired but no one acted in time. The loop ran for 264 hours. Lesson: **alerts require human intervention; circuit breakers do not.** In autonomous systems, budget enforcement must be mechanical, not alerting-based.

### 5.5 Mitigation Architecture

Production defense is layered per failure mode:
- **Factual hallucination**: Retrieval + tool-call upstream
- **Citation hallucination**: Citation enforcement at generation + dedicated CitationAgent post-processing
- **Grounding failures**: Claim-level entailment scoring
- **Reasoning failures**: Step-by-step trace scoring
- **Deployment**: Eval-gated CI before promotion
- **Production monitoring**: Error feed clustering with self-improving evaluators

---

## 6. Enterprise System Design Scenarios

### 6.1 Reference Architecture: Enterprise Research Agent

```
                    +------------------+
                    |   User Interface |
                    |  (async, polling) |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   Orchestrator    |
                    |  (Lead Agent -    |
                    |   strong model)   |
                    +--------+---------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +-------v----+  +------v-----+
     | SubAgent 1 |  | SubAgent 2 |  | SubAgent 3 |
     | (cheap model| | (cheap model| | (cheap model|
     +--------+---+  +-------+----+  +------+-----+
              |              |              |
     +--------v--------------v--------------v-----+
     |              MCP Tool Layer                 |
     |  search | fetch | code_exec | db_query      |
     +--------+-------+----------+---------+------+
              |       |          |         |
     +--------v--+ +--v-----+ +-v------+ +v--------+
     | Search API| | Web    | | Code   | | Vector  |
     |           | | Scraper| | Sandbox| | DB      |
     +-----------+ +--------+ +--------+ +---------+
```

### 6.2 OpenAI Deep Research Pipeline (Production)

Multi-agent collaboration with model routing:
1. **Triage Agent** (GPT-4o, cheap): Route query, assess complexity
2. **Clarifier Agent** (GPT-4o): Ask follow-up questions, scope research
3. **Instruction Builder** (GPT-4.1): Precisely scope the research task
4. **Research Agent** (o3-deep-research, expensive): Execute 30-60 searches, 120-150 page fetches, 150-200 reasoning loops over 20-30 minutes
5. **Publisher**: Synthesize into cited report

Cost optimization: less expensive models handle triage/clarification; the expensive reasoning model only runs for the core research loop.

### 6.3 Perplexity's Search-as-Code Architecture

Unique differentiation: all retrieval orchestrated via model-generated Python code rather than function calls or MCP:
1. Query planner decomposes into sub-queries
2. Retrieval agents search web in parallel (200M daily queries, 358ms median)
3. Reranking pipeline filters/scores across multiple layers
4. Synthesis agent generates cited response
5. Verification step checks citation accuracy

Perplexity Computer orchestrates 19 AI models as specialized sub-agents with a meta-router evaluating task type, complexity, and latency requirements.

### 6.4 Trade-off Matrices

**Topology Selection**:

| Factor | Single-Agent ReAct | Orchestrator-Worker | Pipeline |
|--------|--------------------|--------------------|-----------| 
| Latency | Lowest per turn | Medium (parallel workers offset coordination cost) | Highest (sequential handoffs) |
| Cost per run | Lowest (one model) | Medium (cheap workers + expensive lead) | Varies |
| Quality ceiling | Limited by single model | Highest (90.2% improvement over single-agent in Anthropic benchmarks) | Good for structured outputs |
| Complexity | Simplest | Medium | Highest |
| Failure isolation | Poor (one failure = total failure) | Good (subagent failure is contained) | Good (stage failure is contained) |
| Best for | Simple/focused research, tight budgets | Complex multi-faceted research | Structured report generation |

**Planning Strategy Selection**:

| Factor | Planning-Only | Intent-to-Planning | Unified Intent-Planning |
|--------|--------------|--------------------|-----------------------|
| Time to first result | Fastest | Slower (1 user round-trip) | Slowest (requires user review) |
| Wasted compute risk | Highest | Low | Lowest |
| User engagement needed | None | Low | High |
| Quality alignment | Lowest | High | Highest |
| Best for | Automated pipelines, batch jobs | Interactive research, API | Collaborative research, high-stakes |

**Context Management Strategy**:

| Factor | Window Expansion | Intermediate Compression | External Storage |
|--------|-----------------|-------------------------|-----------------|
| Token cost | Highest | Low (84% reduction measured) | Lowest |
| Accuracy preservation | Highest | Good (under 1.5% loss at 2-3x compression) | Varies by implementation |
| Implementation complexity | Lowest | Medium | Highest |
| Max session length | Limited by model window | Theoretically unlimited | Theoretically unlimited |
| Best for | Short sessions, simple queries | Most production use cases | Multi-hour research, team collaboration |

**Model Routing Economics**:

| Tier | Model Class | Use Case | Cost Impact |
|------|------------|----------|-------------|
| Tier 1 (70% of queries) | Small/fast (Haiku, GPT-4o-mini) | Triage, classification, simple extraction | Baseline |
| Tier 2 (20% of queries) | Mid-tier (Sonnet, GPT-4o) | Search orchestration, summarization | ~5x Tier 1 |
| Tier 3 (10% of queries) | Frontier (Opus, o3) | Complex reasoning, synthesis, planning | ~20x Tier 1 |
| **Blended result** | -- | -- | **60-80% cheaper than all-Tier-3** |

### 6.5 Evaluation Framework

Production evaluation must be multi-dimensional (Anthropic, SystemDesign.one):

| Dimension | Method | Threshold Example |
|-----------|--------|-------------------|
| Factual accuracy | Claim-level entailment scoring | Per-section confidence 0.0-1.0 |
| Citation accuracy | Dedicated CitationAgent post-processing | 8-15 verified inline citations |
| Completeness | Coverage of sub-questions | All decomposed questions addressed |
| Source quality | Authority ranking, source diversity | 2+ independent sources per claim |
| Efficiency | Token/tool-call count vs. baseline | Flag when 50 calls used where 5 suffice |
| Trajectory quality | Full-path evaluation, not just final output | PIES taxonomy scoring per stage |

Binary judge (separate model, separate from main run) evaluates final output. Five rubric principles: atomicity, verifiability, unambiguity, independence, alignment with task requirements.

### 6.6 Key Production Metrics (Summary)

| Metric | Value |
|--------|-------|
| Report length | 600-15,000 words depending on system |
| Inline citations | 8-15 per report |
| Tool calls per run | 10-20 (simple) to 150+ (deep research) |
| Runtime | 2-60 minutes |
| Context accumulation | 250K-1M tokens per session |
| Multi-agent quality improvement | 90.2% over single-agent (Anthropic) |
| Token performance variance explained by budget | 80% (BrowseComp) |
| Prompt caching cost reduction | 78-90% |
| Model routing cost reduction | 60-80% |
| Hallucination rate (multi-step) | 20-40% unguarded, 3-8% with layered defense |
| Citation accuracy (best) | 94% (Claude with search) |

---

## Sources

1. [How to Build an AI Research Agent with MCP -- SystemDesign.one](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp)
2. [Introducing Deep Research -- OpenAI](https://openai.com/index/introducing-deep-research/)
3. [Deep Research Agent Architectures: Multi-Hour Autonomous Research Systems -- Zylos Research](https://zylos.ai/research/2026-04-21-deep-research-agent-architectures)
4. [Deep Research Agents: A Systematic Examination and Roadmap -- arxiv 2506.18096](https://arxiv.org/html/2506.18096v2)
5. [Why Your Deep Research Agent Fails? On Hallucination Evaluation -- arxiv 2601.22984](https://arxiv.org/html/2601.22984v1)
6. [Detecting and Correcting Reference Hallucinations in Commercial LLMs -- arxiv 2604.03173](https://arxiv.org/html/2604.03173v1)
7. [Introduction to Deep Research in the OpenAI API -- OpenAI Cookbook](https://developers.openai.com/cookbook/examples/deep_research_api/introduction_to_deep_research_api)
8. [Deep Research System Card -- OpenAI (PDF)](https://cdn.openai.com/deep-research-system-card.pdf)
9. [Introducing Advanced Tool Use -- Anthropic Engineering](https://www.anthropic.com/engineering/advanced-tool-use)
10. [Introducing the Model Context Protocol -- Anthropic](https://www.anthropic.com/news/model-context-protocol)
11. [Rethinking Search as Code Generation -- Perplexity Research](https://research.perplexity.ai/articles/rethinking-search-as-code-generation)
12. [Architecting and Evaluating an AI-First Search API -- Perplexity Research](https://research.perplexity.ai/articles/architecting-and-evaluating-an-ai-first-search-api)
13. [Context Window Economics -- Zylos Research](https://zylos.ai/research/2026-05-27-context-window-economics-persistent-agents/)
14. [AI Agent Cost Engineering -- Zylos Research](https://zylos.ai/research/2026-05-02-ai-agent-cost-engineering-token-economics/)
15. [Bridging Protocol and Production: Design Patterns for MCP -- arxiv 2603.13417](https://awesomepapers.io/ai-agents/papers/2603.13417)
16. [State of AI Agent Security Report 2026 -- Gravitee](https://www.gravitee.io/state-of-ai-agent-security)
17. [AI Agent Sandboxing: Enterprise Security Guide 2026 -- BeyondScale](https://beyondscale.tech/blog/ai-agent-sandboxing-enterprise-security-guide)
18. [Compound Deception in Elite Peer Review: Fabricated Citations at NeurIPS 2025 -- arxiv 2602.05930](https://arxiv.org/html/2602.05930)
