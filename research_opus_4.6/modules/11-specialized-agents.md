# Module 11: Specialized Agents — Coding, Research, Browser, Data, Domain-Specific, Benchmarks, and Production Deployments

**Scope**: Coding agents (Claude Code, Codex CLI, Cursor, Devin), research agents (deep research systems), browser/computer use agents, data analysis agents, domain-specific agents (customer service, legal, healthcare, finance, supply chain), agent specialization patterns, and benchmarks (SWE-bench, WebArena, GAIA, TAU-bench, OSWorld).
**Prerequisite**: Module 04 (Agent Architecture), Module 05 (Agent Frameworks).
**Last updated**: 2026-08-21 | **Sources consulted**: 70+

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Task Router     │  │  Model Selector  │  │  Budget &        │  │  HITL Gate       │  │
 │  │  - Classify task │  │  - Frontier for  │  │  Sandbox Ctrl    │  │  - Approval for  │  │
 │  │    type (code,   │  │    planning      │  │  - Per-task      │  │    destructive   │  │
 │  │    research,     │  │  - Smaller for   │  │    token cap     │  │    ops (deploy,  │  │
 │  │    browse, data) │  │    execution     │  │  - Kernel-level  │  │    write, send)  │  │
 │  │  - Domain match  │  │  - Domain fine-  │  │    isolation     │  │  - Risk-tiered   │  │
 │  │    to specialist │  │    tuned models  │  │  - Network deny  │  │    classification│  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                      DATA PLANE: SPECIALIZED AGENT EXECUTION                       │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  CODING AGENTS                                                           │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Claude Code  │  │ Codex CLI    │  │ Cursor /     │  │ Devin      │  │      │    │
 │  │  │  │ - While-loop │  │ - Rust agent │  │ Devin Desktop│  │ - Full VM  │  │      │    │
 │  │  │  │ - 7 perm     │  │ - Kernel     │  │ - VS Code    │  │ - Self-    │  │      │    │
 │  │  │  │   modes      │  │   sandbox    │  │   fork       │  │   healing  │  │      │    │
 │  │  │  │ - Subagents  │  │ - No network │  │ - Supermaven │  │ - PR auto  │  │      │    │
 │  │  │  │ - MCP tools  │  │   by default │  │ - Background │  │ - Legacy   │  │      │    │
 │  │  │  │              │  │              │  │   Agents     │  │   migration│  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  RESEARCH & KNOWLEDGE AGENTS                                             │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │      │    │
 │  │  │  │ Gemini Deep  │  │ OpenAI Deep  │  │ Perplexity   │                   │      │    │
 │  │  │  │ Research     │  │ Research     │  │ Deep Research│                   │      │    │
 │  │  │  │ - Plan →     │  │ - Adaptive   │  │ - "Search as │                   │      │    │
 │  │  │  │   Search →   │  │   path       │  │   Code"      │                   │      │    │
 │  │  │  │   Synthesize │  │ - Multimodal │  │ - 2-4 min    │                   │      │    │
 │  │  │  │ - MCP + API  │  │ - PDF/images │  │ - 100-300    │                   │      │    │
 │  │  │  │              │  │              │  │   sources    │                   │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                   │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  BROWSER / COMPUTER USE / DATA AGENTS                                    │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ DOM-Driven   │  │ Vision-Driven│  │ Computer Use │  │ Code       │  │      │    │
 │  │  │  │ Browser      │  │ Browser      │  │ (Desktop)    │  │ Interpreter│  │      │    │
 │  │  │  │ - Playwright │  │ - Screenshot │  │ - Screenshot │  │ - Sandbox  │  │      │    │
 │  │  │  │ - Stagehand  │  │   → coords   │  │   → coords   │  │   Python   │  │      │    │
 │  │  │  │ - 92% reliab │  │ - 75-78%     │  │ - Mouse/key  │  │ - File I/O │  │      │    │
 │  │  │  │              │  │   reliability│  │ - OS-level   │  │ - Viz gen  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  DOMAIN-SPECIFIC AGENTS                                                  │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Customer     │  │ Legal        │  │ Healthcare   │  │ Finance /  │  │      │    │
 │  │  │  │ Service      │  │ (Harvey)     │  │ (Hippocratic)│  │ Supply Chn │  │      │    │
 │  │  │  │ (Agentforce) │  │ - 500+ agent │  │ - FDA adj.   │  │ - Route    │  │      │    │
 │  │  │  │ - $1.2B ARR  │  │   use cases  │  │ - 60-80% FTE │  │   optimize │  │      │    │
 │  │  │  │ - Pay-per-   │  │ - FDE deploy │  │   reduction  │  │ - 190% ROI │  │      │    │
 │  │  │  │   resolution │  │ - $11B val.  │  │ - 23% to prod│  │ - Exception│  │      │    │
 │  │  │  │ - 213% ROI   │  │              │  │              │  │   handling │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ MCP Gateway   │  │ Sandbox       │  │ Domain Tool   │  │ Output Valid. │       │    │
 │  │  │ - Tool routing│  │ - Docker/OCI  │  │ Auth          │  │ - Schema chk  │       │    │
 │  │  │ - Rate limit  │  │ - Kernel isol │  │ - Per-domain  │  │ - PII filter  │       │    │
 │  │  │ - Schema val  │  │ - Network deny│  │   scopes      │  │ - Compliance  │       │    │
 │  │  │ - Circuit brk │  │ - File scope  │  │ - API keys    │  │   gate        │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Domain Knowledge  │  │ Agent State &     │  │ Research Corpus   │  │ WORM Audit Log │  │
 │  │ Store             │  │ Checkpoints       │  │ - Search results  │  │ - Tool calls   │  │
 │  │ - RAG indexes     │  │ - Conversation    │  │ - Synthesized     │  │ - Agent actions │  │
 │  │ - Fine-tune data  │  │   history         │  │   reports         │  │ - Compliance   │  │
 │  │ - Precedent libs  │  │ - Tool outputs    │  │ - Source citations│  │   evidence     │  │
 │  │ - Skills/routines │  │ - Session state   │  │ - Cached queries  │  │ - Immutable    │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Task Completion   │  │ Benchmark Eval    │  │ Cost & Usage      │  │ Domain Quality │  │
 │  │ Metrics           │  │ - SWE-bench       │  │ - Per-agent token │  │ - Resolution   │  │
 │  │ - Pass rate       │  │ - WebArena        │  │   spend           │  │   rate (CS)    │  │
 │  │ - Pass^k reliab.  │  │ - GAIA            │  │ - $/task by       │  │ - Accuracy     │  │
 │  │ - Trajectory      │  │ - OSWorld         │  │   domain          │  │   (legal/med)  │  │
 │  │   efficiency      │  │ - TAU-bench       │  │ - Model tier      │  │ - CSAT/ROI     │  │
 │  │ - Scaffold impact │  │ - DABStep         │  │   breakdown       │  │   tracking     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Task Classification**: An incoming request hits the **Task Router**, which classifies the task type: code change, deep research, web interaction, data analysis, or domain-specific query. The classifier matches the task to the appropriate specialized agent category and selects the right domain knowledge base.

**Step 2 — Model & Agent Selection**: The **Model Selector** chooses the model tier: frontier model for planning/complex reasoning, smaller model for execution steps. For domain-specific agents, fine-tuned or domain-specialized models (Harvey's legal models, Hippocratic's healthcare models) are preferred over general-purpose models.

**Step 3 — Sandbox & Budget Enforcement**: Before execution, the **Budget Controller** sets per-task token caps. The sandbox enforcer configures isolation: kernel-level for coding agents (no network, file-scoped), Docker/OCI for tool execution, VM-level for computer use agents. Destructive operations require HITL approval.

**Step 4 — Agent Execution Loop**: The specialized agent runs its core loop. Coding agents: while-loop of model call → tool use → repeat. Research agents: plan → search → read → reflect → iterate → synthesize. Browser agents: screenshot → coordinate → action → observe. Domain agents: classify → retrieve domain knowledge → reason → act → verify. All tool calls route through the **Tool Proxy Layer** with per-domain authorization.

**Step 5 — Output Verification**: Agent output passes through domain-specific validation. Coding agents: run tests, lint, type-check. Legal agents: verify citations against precedent library. Healthcare agents: check against clinical guidelines. Data agents: verify calculations against source data.

**Step 6 — Audit & Metrics**: All actions logged to WORM audit storage. Benchmark evaluation runs periodically against held-out test sets. Domain quality metrics (resolution rate, accuracy, CSAT) tracked for production monitoring. Pass^k reliability testing ensures consistency across runs.

---

## 2. Core Mechanics & Algorithms

### 2.1 Coding Agent Architecture Comparison

| Agent | Architecture | Sandbox Model | SWE-bench Verified | Key Differentiator |
|-------|-------------|:-------------:|:------------------:|-------------------|
| **Claude Code** | While-loop + 7 permission modes + ML classifier + 5-layer compaction | Process boundary; user-controlled permissions | 87.6% (Opus 4.7) | Subagent orchestration; Agent SDK; MCP ecosystem |
| **Codex CLI** | Rust agent loop; prompt → model → response | Kernel-level: no network, file-scoped | 85.0% (GPT-5.3) | Strongest sandbox isolation; 3M WAU |
| **Cursor** | VS Code fork + Supermaven autocomplete | Editor-level; Background Agents in cloud | N/A (IDE, not standalone) | 72% acceptance rate; $2B ARR; Background Agents |
| **Devin** | Full VM (shell + editor + browser) | VM-level: dedicated instance per task | 45.8% (conservative pass@1) | Autonomous end-to-end; legacy migration; $26B valuation |

**Key insight**: The scaffold matters more than the model. The same base model in different scaffolds varies by 15+ percentage points on SWE-bench. Claude Code's while-loop + permission system + context compaction pipeline accounts for more variance than switching between frontier models.

### 2.2 Research Agent Pattern: Plan → Search → Synthesize

All mature deep research systems share a common architectural pattern:

```
  Goal ──▶ ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌───────────┐
           │ Plan    │──▶│ Search  │──▶│ Read &  │──▶│ Reflect  │──▶│ Synthesize│
           │ - Break │  │ - Web   │  │ Analyze │  │ - Assess │  │ - Report  │
           │   into  │  │ - APIs  │  │ - Parse │  │   gaps   │  │ - Cite    │
           │   sub-  │  │ - MCP   │  │ - Extract│  │ - Replan │  │   sources │
           │   queries│  │ - Docs  │  │   facts │  │ - Iterate│  │ - Format  │
           └─────────┘  └─────────┘  └─────────┘  └──────────┘  └───────────┘
                                                        │
                                                        │ (gaps found)
                                                        └──▶ Loop back to Search
```

| System | Speed | Source Count | Multimodal | API Access | DeepResearch Bench |
|--------|:-----:|:-----------:|:----------:|:----------:|:-----------------:|
| **Gemini Deep Research** | Moderate | Broad web + internal (MCP) | Text only | Interactions API (July 2026) | 48.9 (leader) |
| **OpenAI Deep Research** | 20+ min | 20–50 cited | Text + images + PDF | GPT-5.x with web search | 46.5 |
| **Perplexity Deep Research** | 2–4 min | 100–300 cited | Code-driven retrieval | Sonar API | Competitive |

### 2.3 Browser Agent Modality Comparison

| Approach | Mechanism | Reliability | Best For |
|----------|-----------|:-----------:|---------|
| **DOM-driven** (Playwright + AI) | Parsed HTML / structured DOM → DOM operations | 89–92% | Deterministic web automation with AI fallback |
| **Vision-driven** (Computer Use) | Screenshot → coordinate grid → mouse/keyboard | 75–78% | When DOM access unavailable; native apps |
| **Hybrid** (recommended) | AI on deterministic Playwright; vision fallback | 92%+ | Production web automation |

**OSWorld performance trajectory** (desktop computer use):

| Date | Agent | OSWorld Score |
|------|-------|:------------:|
| Apr 2024 | Best at launch | 7–12% |
| Jan 2025 | OpenAI CUA (Operator) | 38.1% |
| Sept 2025 | Claude Sonnet 4.5 | 61.4% |
| Dec 2025 | Simular Agent S2 | 72.6% (first > human) |
| Aug 2026 | Qwen3.8 Max | **86.1%** (leader) |

**The reality gap**: 85% on OSWorld but only **20.6%** of real long-horizon workflows (OSWorld 2.0). Cross-application workflows achieve 12–20% success rates. Benchmark scores ≠ production readiness.

### 2.4 Agent Specialization Patterns

**ReAct** (most widely adopted): Alternates thought → action → observation. Auditable, grounds decisions in real-world feedback.

**Plan-and-Execute**: Separates planning (frontier model) from execution (smaller model). 92% task completion with 3.6× speedup. Dominant production pattern.

**Reflection / Self-Critique**: Agent evaluates own output against criteria, then revises. Lifted HumanEval from 80% → 91%. Combined with external validators (test runners), gains can exceed 30 percentage points.

**Writer-Critic**: Agent-writer generates; agent-critic (different model, different prompt) checks. Catches 60–80% of errors a single agent misses.

### 2.5 Knowledge Injection Trade-offs

| Approach | Changes Reasoning? | Knowledge Currency | Token Cost | Best For |
|----------|:-----------------:|:-----------------:|:----------:|---------|
| **RAG** | No — adds facts only | Current (live retrieval) | Low (per-query) | Domain data, frequently changing knowledge |
| **Prompting / Few-shot** | No — steers behavior | Frozen in prompt | High (repeated tokens) | Prototyping, format control |
| **Fine-tuning** | Yes — changes capability | Frozen at training time | Low (inference) | Strict formats, proprietary APIs, reasoning depth |
| **Prefix caching** | No — reduces cost | N/A | 40–70% savings | Production cost optimization |

**Recommendation**: Start with RAG + prompting. Fine-tune only when RAG adds facts but the model can't reason about them correctly. Mix rehearsal data to prevent catastrophic forgetting.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Per-Task Economics by Agent Type

| Agent Type | Avg Tokens/Task | Cost/Task (Sonnet $3/$15) | Cost/Task (Haiku $0.80/$4) | Notes |
|-----------|:--------------:|:------------------------:|:-------------------------:|-------|
| Coding (simple fix) | 15K | $0.27 | $0.07 | Single file edit + test |
| Coding (complex feature) | 100K | $1.80 | $0.48 | Multi-file, subagents |
| Deep research | 193K (documented) | $3.47 | N/A (frontier only) | Perplexity example: 21 searches |
| Browser automation (3 steps) | 10K | $0.18 | $0.05 | DOM-driven, deterministic |
| Computer use (5 steps) | 25K | $0.45 | $0.12 | Screenshots + coordinates |
| Customer service resolution | 8K | $0.14 | $0.04 | Salesforce: ~$0.10/action |
| Legal analysis | 50K | $0.90 | N/A | Harvey: domain model |
| Data analysis | 20K | $0.36 | $0.10 | Code interpreter + viz |

**Cost optimization mechanisms**:
- **Model cascading**: Frontier for planning (5% of tokens), smaller for execution (95%). Reduces cost by up to 90%.
- **Prefix caching**: 40–70% cost reduction on system prompts and tool schemas across multiple agent calls.
- **Pay-per-resolution** (Salesforce model): No charge if customer escalates or gives negative feedback — aligns incentives.
- **Domain fine-tuning**: Replaces expensive few-shot prompting with cheaper inference on a tuned model.

### 3.2 Latency SLA Targets

| Agent Type | p50 | p95 | p99 | Mitigation |
|-----------|-----|-----|-----|------------|
| Coding (simple) | 5s | 15s | 30s | Stream partial edits; limit tool calls |
| Coding (complex) | 30s | 120s | 300s | Subagent parallelism; checkpoint per step |
| Deep research | 120s (Perplexity) | 600s | 1200s (OpenAI) | Parallel search; streaming; progress updates |
| Browser (DOM) | 2s/step | 8s/step | 15s/step | Timeout per navigation; deterministic fallback |
| Computer use (vision) | 3s/step | 10s/step | 20s/step | Screenshot caching; action confirmation |
| Customer service | 2s | 8s | 15s | Model routing (Haiku for simple, Sonnet for complex) |
| Legal analysis | 10s | 30s | 60s | Pre-computed RAG; domain model caching |

**p50 mitigation**: Streaming for all user-facing agents. Deterministic tools where possible (DOM > vision).
**p95 mitigation**: Per-step timeout with graceful degradation. Parallel subagents for independent subtasks.
**p99 mitigation**: Hard wall-clock timeout per task. Circuit breaker on downstream tools. Return partial results with gap notification.

### 3.3 Throughput & Back-Pressure

**Coding agents at scale**: Cursor serves 2M+ users with Background Agents running in cloud VMs. Devin runs dedicated VMs per task, limiting throughput by VM pool size.

**Customer service at scale**: Salesforce Agentforce delivered 2.4B Agentic Work Units in FY26 (57% quarterly growth). Pay-per-resolution model naturally limits cost explosion — agent doesn't charge if it fails.

**Back-pressure**:
- Per-task token cap prevents runaway coding agent loops (the $47K incident).
- Queue-based admission for high-volume domains (customer service).
- Model cascade under load: downgrade from Sonnet → Haiku to maintain throughput.
- Timeout escalation: if agent exceeds 2× expected duration, alert ops.

### 3.4 RPO/RTO by Agent Domain

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Coding agent state** | Per-file-write (atomic) | <1s (rerun from last edit) | File system is the checkpoint |
| **Research corpus** | Per-search (results cached) | <5s (resume from cache) | Search result persistence |
| **Customer service session** | Per-message | <2s (reload session state) | Platform session store |
| **Legal analysis work product** | Per-step | <5s (resume from checkpoint) | Durable workflow engine |
| **Domain knowledge (RAG)** | 0 (replicated) | <1s | Vector DB with replication |
| **Audit trail** | 0 (append-only) | <1s | WORM storage |

### 3.5 Benchmark Score vs. Production Value

| Benchmark | SOTA Score | Production Reality | Gap Factor |
|-----------|:---------:|:------------------:|:----------:|
| SWE-bench Verified | 97.0% | 75–80% after contamination adjustment | 1.25× |
| OSWorld | 86.1% | 20.6% on long-horizon (OSWorld 2.0) | 4.2× |
| Pass@1 vs. Pass^4 | — | Pass^4 runs 15–25 points below pass@1 | — |
| DABStep (Easy vs. Hard) | 76.4% Easy | 14.6% Hard | 5.2× |

---

## 4. Distributed Resilience & Security

### 4.1 Domain-Specific Failure Modes

| Domain | Critical Failure | Class | Impact |
|--------|-----------------|-------|--------|
| **Coding** | Runaway loop ($47K incident) | **Permanent** (design) | Unbounded cost; corrupted codebase |
| **Coding** | Silent test-passing bug | **Transient** | Deployed code with hidden defects |
| **Research** | Hallucinated citations | **Transient** | Fabricated sources in reports |
| **Browser** | Screenshot misinterpretation | **Transient** | Wrong element clicked; cascading errors |
| **Computer use** | OS-level action on wrong window | **Permanent** (irreversible) | Data loss; sent wrong message |
| **Customer service** | Incorrect refund/cancellation | **Permanent** (financial) | Revenue loss; customer impact |
| **Legal** | Hallucinated case law | **Permanent** (professional) | Malpractice risk; sanctions |
| **Healthcare** | Wrong clinical recommendation | **Permanent** (safety) | Patient harm; liability |
| **Finance** | Unauthorized trade execution | **Permanent** (regulatory) | Regulatory violation; financial loss |

### 4.2 Circuit Breaker for Specialized Agents

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Halt     │    │ Route 2    │
         │ agent   │    │ domain;  │    │ test tasks │
         │ ops     │    │ escalate │    │ from held- │
         │         │    │ to human │    │ out set    │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 60s
              │     recovery timeout
              │     (60s → 120s → 240s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 failures (task failure, hallucination detected, compliance violation) within 120s window.
- **Open duration**: 60s initial recovery timeout with exponential backoff (60s → 120s → 240s).
- **Half-Open → Closed**: 2 consecutive successful probe tasks from a held-out evaluation set.
- **Domain-specific fallbacks**: Coding → return partial diff with manual review note. Customer service → escalate to human agent. Legal → flag as "unverified" and route to attorney. Healthcare → refuse to answer and direct to provider.

#### 4.2.2 Per-Domain Breaker Applications

| Domain | Failure Type | Class | Fallback Strategy |
|--------|-------------|-------|-------------------|
| Coding agent (API timeout) | Provider down | **Transient** | Retry; switch model provider; queue edit |
| Coding agent (infinite loop) | Design flaw | **Permanent** | Kill after token cap; return partial work |
| Research agent (no results) | Search failure | **Transient** | Retry with rephrased query; broaden scope |
| Browser agent (DOM change) | Website update | **Transient** | Fall back to vision-driven; retry |
| Customer service (wrong domain) | Misroute | **Transient** | Re-classify; route to correct specialist |
| Legal agent (hallucinated citation) | Quality failure | **Permanent** | Block output; flag for attorney review |
| Healthcare agent (clinical error) | Safety violation | **Permanent** | Refuse; direct to provider; log for audit |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Agent runaway loop | **Permanent** (design) | Token counter; step limit; identical tool call detection | Hard per-task cap; loop detector |
| Hallucinated citations | **Transient** | Cross-reference against source DB | Citation verification step; only cite retrievable sources |
| Domain knowledge drift | **Transient** | Periodic eval against held-out set | RAG index refresh; model re-evaluation cadence |
| Benchmark overfitting | **Permanent** (architecture) | Held-out evaluation; pass^k testing | Private eval sets; contamination-resistant benchmarks |
| OS-level destructive action | **Permanent** (irreversible) | HITL gate for destructive ops | VM isolation; snapshot before action; approval required |
| Compliance violation (regulated domain) | **Permanent** (legal) | Output compliance gate; domain rules engine | Hard-block non-compliant outputs; audit trail |
| Silent error propagation | **Transient** | Semantic quality checks; output validation | Writer-Critic pattern; domain-specific validators |
| Model provider outage | **Transient** | Health check; response timeout | Multi-provider routing; model cascade |

### 4.3.1 Idempotency in Specialized Agent Operations

Coding agents write files, run commands, and create PRs — all potentially non-idempotent. On crash recovery:

```
Agent writes file → runs tests → creates PR:
                                    │
                          ┌─────────▼──────────┐
                          │ Idempotency Guard   │
                          │ key = hash(task_id  │
                          │   + file_path       │
                          │   + content_hash    │
                          │   + operation)      │
                          └─────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ IF key in completed_ops:     │
                     │   SKIP (already applied)     │
                     │ ELSE:                        │
                     │   execute operation           │
                     │   store key + result          │
                     └─────────────────────────────┘
```

**Domain-specific idempotency**: Customer service agents must not send duplicate refunds — log refund ID before execution. Legal agents must not file duplicate motions — check filing system before submission. Healthcare agents must not place duplicate orders — verify order system state.

### 4.3.2 Poison-Pill Detection for Specialized Agents

A poison pill in specialized agents is domain-specific: a malicious input that causes the agent to produce harmful domain output.

**Detection heuristics by domain**:
- **Coding**: PR contains credentials, API keys, or exfiltration patterns in committed code.
- **Customer service**: Agent response contains unauthorized discount/refund promises.
- **Legal**: Output cites non-existent case law or statutes.
- **Healthcare**: Recommendation contradicts clinical guidelines or drug interaction databases.

**Quarantine**: Halt output delivery. Flag for domain expert review. Log full trace for forensic analysis.

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Zero-Trust Specialized Agent Deployment

1. **Kernel-level sandbox for code execution**: Coding agents (Codex CLI model) disable network during execution by default. File operations scoped to working directory tree. No access to ~/.ssh, ~/.aws, or credential stores.

2. **Domain-specific tool scoping**: Customer service agent gets CRM read + write but no payment system access. Legal agent gets document search but no filing capability without attorney approval. Healthcare agent gets clinical reference but no prescription authority.

3. **Output compliance gate**: Every domain agent output passes through a domain rules engine before delivery. Legal: citation verification. Healthcare: clinical guideline check. Finance: regulatory compliance scan.

4. **VM/container isolation for computer use**: Computer use agents run in isolated VMs with snapshot-before-action. Destructive operations require explicit user confirmation. ML classifiers flag prompt injections in screenshots.

5. **Immutable audit trail**: All agent actions, tool calls, and outputs logged to WORM storage. Required for SOC2, HIPAA, EU AI Act compliance. Enables forensic reconstruction of any agent interaction.

---

## 5. Production Enterprise Code

### 5.1 Specialized Agent Router with Domain Knowledge Injection

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AgentDomain(Enum):
    CODING = "coding"
    RESEARCH = "research"
    BROWSER = "browser"
    CUSTOMER_SERVICE = "customer_service"
    LEGAL = "legal"
    DATA_ANALYSIS = "data_analysis"


@dataclass
class DomainConfig:
    domain: AgentDomain
    model: str
    system_prompt: str
    tools: list[str]
    max_tokens: int
    requires_approval: bool = False
    compliance_gate: bool = False
    knowledge_base_id: Optional[str] = None


DOMAIN_CONFIGS = {
    AgentDomain.CODING: DomainConfig(
        domain=AgentDomain.CODING,
        model="claude-sonnet-4-20250514",
        system_prompt="You are a coding agent. Write clean, tested code.",
        tools=["file_read", "file_write", "bash", "grep", "test_runner"],
        max_tokens=100_000,
    ),
    AgentDomain.CUSTOMER_SERVICE: DomainConfig(
        domain=AgentDomain.CUSTOMER_SERVICE,
        model="claude-haiku-4-5-20251001",
        system_prompt="You are a customer service agent. Follow company policy.",
        tools=["crm_read", "crm_write", "knowledge_base", "escalate"],
        max_tokens=8_000,
        compliance_gate=True,
        knowledge_base_id="cs-kb-v3",
    ),
    AgentDomain.LEGAL: DomainConfig(
        domain=AgentDomain.LEGAL,
        model="claude-opus-4-20250918",
        system_prompt="You are a legal research agent. Cite only verifiable sources.",
        tools=["case_search", "statute_lookup", "document_draft"],
        max_tokens=50_000,
        requires_approval=True,
        compliance_gate=True,
        knowledge_base_id="legal-precedents-v2",
    ),
}


class SpecializedAgentRouter:
    def __init__(self, llm_client, domain_configs: dict, knowledge_store,
                 compliance_engine):
        self.llm = llm_client
        self.configs = domain_configs
        self.knowledge = knowledge_store
        self.compliance = compliance_engine

    async def route_and_execute(self, task: str, user_context: dict) -> dict:
        domain = await self._classify_domain(task)
        config = self.configs[domain]

        domain_context = ""
        if config.knowledge_base_id:
            domain_context = await self.knowledge.retrieve(
                config.knowledge_base_id, task, top_k=10
            )

        response = self.llm.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            system=f"{config.system_prompt}\n\nDomain knowledge:\n{domain_context}",
            messages=[{"role": "user", "content": task}],
        )

        output = response.content[0].text

        if config.compliance_gate:
            compliance_result = await self.compliance.check(
                domain=domain, output=output, context=user_context
            )
            if not compliance_result.passed:
                return {
                    "status": "blocked",
                    "domain": domain.value,
                    "reason": compliance_result.reason,
                    "requires": "human_review",
                }

        return {
            "status": "completed",
            "domain": domain.value,
            "output": output,
            "model": config.model,
            "tokens": response.usage.input_tokens + response.usage.output_tokens,
            "requires_approval": config.requires_approval,
        }

    async def _classify_domain(self, task: str) -> AgentDomain:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": (
                f"Classify this task into one domain: "
                f"coding, research, browser, customer_service, legal, "
                f"data_analysis.\nTask: {task}"
            )}],
        )
        return AgentDomain(response.content[0].text.strip().lower())
```

### 5.2 Benchmark Evaluation Harness with Pass^k Reliability

```python
import asyncio
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class BenchmarkResult:
    task_id: str
    passed: bool
    tokens_used: int
    latency_ms: float
    trajectory_length: int


class PassKEvaluator:
    def __init__(self, agent, k: int = 4):
        self.agent = agent
        self.k = k

    async def evaluate_suite(self, tasks: list[dict]) -> dict:
        all_results = defaultdict(list)

        for task in tasks:
            for trial in range(self.k):
                result = await self._run_single(task)
                all_results[task["id"]].append(result)

        pass_1 = sum(
            1 for task_id, results in all_results.items()
            if any(r.passed for r in results)
        ) / len(tasks)

        pass_k = sum(
            1 for task_id, results in all_results.items()
            if all(r.passed for r in results)
        ) / len(tasks)

        avg_tokens = sum(
            r.tokens_used for results in all_results.values() for r in results
        ) / sum(len(results) for results in all_results.values())

        return {
            "pass_at_1": round(pass_1 * 100, 1),
            f"pass_at_{self.k}": round(pass_k * 100, 1),
            "reliability_gap": round((pass_1 - pass_k) * 100, 1),
            "avg_tokens_per_trial": int(avg_tokens),
            "total_tasks": len(tasks),
            "total_trials": len(tasks) * self.k,
        }

    async def _run_single(self, task: dict) -> BenchmarkResult:
        import time
        start = time.time()
        result = await self.agent.solve(task)
        elapsed_ms = (time.time() - start) * 1000

        return BenchmarkResult(
            task_id=task["id"],
            passed=result["passed"],
            tokens_used=result["tokens_used"],
            latency_ms=elapsed_ms,
            trajectory_length=result.get("steps", 0),
        )
```

### 5.3 Domain Compliance Gate

```python
import re
from dataclasses import dataclass


@dataclass
class ComplianceResult:
    passed: bool
    reason: str = ""
    violations: list[str] = None

    def __post_init__(self):
        if self.violations is None:
            self.violations = []


class DomainComplianceEngine:
    def __init__(self):
        self._domain_rules = {
            "legal": self._check_legal,
            "customer_service": self._check_customer_service,
            "healthcare": self._check_healthcare,
        }

    async def check(self, domain, output: str, context: dict) -> ComplianceResult:
        domain_key = domain.value if hasattr(domain, "value") else str(domain)
        checker = self._domain_rules.get(domain_key)
        if not checker:
            return ComplianceResult(passed=True)
        return checker(output, context)

    def _check_legal(self, output: str, context: dict) -> ComplianceResult:
        violations = []
        citation_pattern = r"\b\d+\s+[A-Z][a-z]+\.?\s*\d*[a-z]*\.\s*\d+"
        citations = re.findall(citation_pattern, output)
        if not citations and len(output) > 500:
            violations.append("Legal output >500 chars with no case citations")

        disclaimer_needed = any(
            phrase in output.lower()
            for phrase in ["legal advice", "recommend", "should file", "must comply"]
        )
        if disclaimer_needed and "not legal advice" not in output.lower():
            violations.append("Advisory language without disclaimer")

        return ComplianceResult(
            passed=len(violations) == 0,
            reason="; ".join(violations) if violations else "",
            violations=violations,
        )

    def _check_customer_service(self, output: str, context: dict) -> ComplianceResult:
        violations = []
        unauthorized_patterns = [
            r"(?i)full\s+refund",
            r"(?i)free\s+(upgrade|month|year)",
            r"(?i)waive\s+(the\s+)?fee",
        ]
        for pattern in unauthorized_patterns:
            if re.search(pattern, output):
                agent_tier = context.get("agent_tier", "basic")
                if agent_tier != "manager":
                    violations.append(
                        f"Unauthorized promise: {pattern} (agent tier: {agent_tier})"
                    )

        return ComplianceResult(
            passed=len(violations) == 0,
            reason="; ".join(violations) if violations else "",
            violations=violations,
        )

    def _check_healthcare(self, output: str, context: dict) -> ComplianceResult:
        violations = []
        prescription_patterns = [
            r"(?i)prescribe\s+\w+",
            r"(?i)take\s+\d+\s*mg",
            r"(?i)dosage\s+of\s+\d+",
        ]
        for pattern in prescription_patterns:
            if re.search(pattern, output):
                violations.append("Output contains prescription-like language")

        if "consult" not in output.lower() and "healthcare provider" not in output.lower():
            if any(term in output.lower() for term in ["diagnosis", "treatment", "medication"]):
                violations.append("Clinical content without provider referral")

        return ComplianceResult(
            passed=len(violations) == 0,
            reason="; ".join(violations) if violations else "",
            violations=violations,
        )
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Coding Agent Platform for 500 Developers

**Business context**: A technology company with 500 developers wants to deploy an AI coding agent platform. Requirements: support for 5 programming languages (Python, TypeScript, Go, Java, Rust), code must pass existing CI/CD pipeline, developers retain full control over what gets committed, $200K/month AI budget, SOC2 compliance, and no proprietary code leaves the corporate network.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     CODING AGENT PLATFORM                                │
 │                                                                          │
 │  Developer ──▶ ┌──────────────┐  ┌────────────────────────────────────┐ │
 │  (IDE/CLI)     │ Agent Router │  │         EXECUTION LAYER            │ │
 │                │              │  │                                    │ │
 │                │ Simple edit  │──▶│  Haiku (fast, inline completion) │ │
 │                │ Complex feat │──▶│  Sonnet (multi-file, subagents)  │ │
 │                │ Architecture │──▶│  Opus (design, review, planning) │ │
 │                │              │  │                                    │ │
 │                └──────────────┘  └────────────────┬───────────────────┘ │
 │                                                   │                     │
 │                                        ┌──────────▼──────────┐         │
 │                                        │ Validation Pipeline │         │
 │                                        │ - Lint + type check │         │
 │                                        │ - Unit tests        │         │
 │                                        │ - Security scan     │         │
 │                                        │ - Diff review (HITL)│         │
 │                                        └──────────┬──────────┘         │
 │                                                   │                     │
 │                                        ┌──────────▼──────────┐         │
 │                                        │ PR Creation         │         │
 │                                        │ - Developer approval│         │
 │                                        │ - CI/CD pipeline    │         │
 │                                        └─────────────────────┘         │
 │                                                                         │
 │  ┌───────────────────────────────────────────────────────────────────┐  │
 │  │  INFRASTRUCTURE                                                   │  │
 │  │  - On-prem model serving (no code egress)                        │  │
 │  │  - Kernel sandbox per execution (Codex model)                    │  │
 │  │  - Per-developer token budget + audit log                        │  │
 │  └───────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Cloud-Hosted IDE (Cursor/Windsurf) | B: On-Prem Agent SDK + Model Cascade (Recommended) | C: Self-Hosted Open-Source (OpenHands) |
|-----------|--------------------------------------|---------------------------------------------------|---------------------------------------|
| **Code security (no egress)** | ⬛⬜⬜ — Code sent to vendor cloud | ⬛⬛⬛ — All inference on-prem; code never leaves network | ⬛⬛⬛ — Fully self-hosted |
| **Quality (SWE-bench equiv.)** | ⬛⬛⬛ — 87%+ with frontier models | ⬛⬛⬛ — Same models via API or on-prem serving | ⬛⬛⬜ — 72% (OpenHands); gap to frontier |
| **Cost at 500 devs** | ⬛⬛⬜ — $20–40/dev/month = $10–20K/month (fixed) + usage | ⬛⬛⬛ — Pay-per-token with cascade; ~$150K/month at heavy use | ⬛⬛⬛ — Infra cost only; ~$80K/month for GPU cluster |
| **Developer experience** | ⬛⬛⬛ — Polished IDE; zero setup | ⬛⬛⬜ — CLI/SDK; requires integration work | ⬛⬛⬜ — Variable UX; community-maintained |
| **SOC2 compliance** | ⬛⬛⬜ — Vendor SOC2; data processing agreement needed | ⬛⬛⬛ — On-prem; full control; audit trail | ⬛⬛⬜ — Self-managed compliance |
| **Multi-language support** | ⬛⬛⬛ — All languages | ⬛⬛⬛ — All languages (model-dependent) | ⬛⬛⬜ — Best for Python; variable for others |

**Recommended approach**: **B (On-Prem Agent SDK + Model Cascade)**.

**Decision rationale**: The hard constraint is no code egress — proprietary code cannot leave the corporate network. This eliminates Option A (cloud-hosted IDE). On-prem deployment using Claude Agent SDK (or equivalent) with self-hosted model serving gives full control. Model cascade (Haiku for inline completion at ~70% of requests, Sonnet for multi-file features at ~25%, Opus for architecture review at ~5%) keeps cost at ~$150K/month for 500 developers — well within the $200K budget. The kernel-level sandbox (Codex model) prevents file access outside working directory and disables network during execution. Each developer gets a per-session token budget, and all agent actions log to the WORM audit trail for SOC2. Option C (open-source) saves on model cost but the 72% SWE-bench score vs. 87%+ creates a measurable quality gap that 500 developers would notice daily.

### 6.2 Scenario: Domain-Specific AI Agent for Insurance Claims Processing

**Business context**: An insurance company processes 50K claims/month across auto, property, and health lines. Current process: claims examiners manually review documentation, verify coverage, assess damage, and issue payments — averaging 14 days per claim. Requirements: reduce average processing time to <3 days, maintain 98%+ accuracy on coverage decisions, regulatory compliance (state insurance laws), $500K/year AI budget, and human oversight for claims >$50K.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     CLAIMS PROCESSING AGENT                              │
 │                                                                          │
 │  Claim ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐   │
 │            │ Intake Agent │     │ Assessment   │     │ Decision   │   │
 │            │ (Haiku)      │     │ Agent        │     │ Agent      │   │
 │            │              │     │ (Sonnet)     │     │ (Opus)     │   │
 │            │ - Extract    │     │              │     │            │   │
 │            │   claim data │     │ - Verify     │     │ - Apply    │   │
 │            │ - Classify   │     │   coverage   │     │   policy   │   │
 │            │   line (auto/│     │ - Assess     │     │   rules    │   │
 │            │   prop/health│     │   damage     │     │ - Calc     │   │
 │            │ - Priority   │     │ - Detect     │     │   payout   │   │
 │            │   score      │     │   fraud      │     │ - Generate │   │
 │            │              │     │   patterns   │     │   decision │   │
 │            └──────────────┘     └──────────────┘     └──────┬─────┘   │
 │                                                             │         │
 │                                              ┌──────────────▼───────┐ │
 │                                              │ Compliance Gate      │ │
 │                                              │ - State law check    │ │
 │                                              │ - >$50K → human     │ │
 │                                              │ - Fraud flag → SIU  │ │
 │                                              │ - Audit trail       │ │
 │                                              └──────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Horizontal AI + Custom Rules Engine | B: Domain-Specific Agent Pipeline (Recommended) | C: Fine-Tuned Single Model |
|-----------|---------------------------------------|------------------------------------------------|---------------------------|
| **Accuracy on coverage decisions** | ⬛⬛⬜ — 92% (general model lacks insurance domain) | ⬛⬛⬛ — 98.5% (domain RAG + policy rules engine + compliance gate) | ⬛⬛⬜ — 96% (good but rigid; can't adapt to policy changes) |
| **Processing time** | ⬛⬛⬜ — 5 days (manual rules engine tuning bottleneck) | ⬛⬛⬛ — <3 days (automated pipeline, human review only for >$50K) | ⬛⬛⬛ — <2 days (single fast inference) |
| **Regulatory compliance** | ⬛⬛⬜ — Rules engine handles known cases; novel cases fall through | ⬛⬛⬛ — Compliance gate checks every decision; audit trail; state-law-aware | ⬛⬜⬜ — Black box; hard to explain decisions to regulators |
| **Adaptability to policy changes** | ⬛⬛⬜ — Rules engine requires manual update | ⬛⬛⬛ — RAG index updated with new policies; no retraining | ⬛⬜⬜ — Requires retraining for policy changes |
| **Cost at 50K claims/month** | ⬛⬛⬛ — ~$100K/year (rules engine + minimal AI) | ⬛⬛⬛ — ~$400K/year (model cascade + RAG + compliance) | ⬛⬛⬛ — ~$200K/year (single model inference) |
| **Fraud detection** | ⬛⬜⬜ — Pattern-based only | ⬛⬛⬛ — AI-driven anomaly detection + pattern matching | ⬛⬛⬜ — Model detects but can't explain why |

**Recommended approach**: **B (Domain-Specific Agent Pipeline)**.

**Decision rationale**: The 98%+ accuracy requirement on coverage decisions eliminates Option A (92% with a general model) and makes Option C risky (96% but can't explain decisions to regulators). The three-stage pipeline (Intake → Assessment → Decision) uses model cascade: Haiku for fast intake classification ($0.04/claim), Sonnet for coverage verification and damage assessment ($0.90/claim), Opus for final decision with policy reasoning ($2.50/claim). Total: ~$3.44/claim × 50K = ~$172K/year on inference, plus RAG infrastructure and compliance engine for ~$400K total — within the $500K budget. The compliance gate checks every decision against state insurance laws, routes claims >$50K to human examiners, and flags fraud patterns for the Special Investigations Unit. The domain RAG index contains current policy wordings, state regulatory requirements, and historical claim precedents — updated when policies change, with no model retraining needed. Option C's fine-tuned model achieves better speed but its black-box nature conflicts with regulatory requirements for explainable coverage decisions.

---

*Module 11 complete. Covers coding agent architectures, deep research systems, browser/computer use agents, domain-specific production deployments across customer service/legal/healthcare/finance/supply chain, specialization patterns, and benchmark evaluation methodology.*
