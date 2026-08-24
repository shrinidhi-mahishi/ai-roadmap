# Agentic AI Roadmap 2026 — Study Guide

17-topic interview prep for Agentic AI, researched across 5 independent model passes. Each version brings a different strength — this guide tells you which to read first per topic.

## Versions Available

| Folder | Model | Total Lines | Key Strength |
|--------|-------|-------------|--------------|
| `research_cursor_grok/` | Grok (via Cursor) | 20,539 | Deepest overall; 78–93 sources/topic; explicit interview framing; production vendor names & metrics |
| `research_opus_4.6/` | Claude Opus 4.6 | 19,656 | Extraordinarily deep on modules 01–05; "Key Numbers to Memorize" appendix; massive code examples |
| `research_gpt_sol/` | GPT Sol | 15,582 | Explicit "Interview Review" Q&A sections; clean trade-off matrices; "Study goal" framing |
| `research_cursor_sonnet/` | Sonnet (via Cursor) | 15,302 | Most consistent quality across all 17 modules; strong system-design scenarios |
| `research_gpt_5.4/` | GPT 5.4 | 14,818 | Cleanest prose; easiest to absorb quickly; thinnest on enterprise detail |

## Recommendation Table

For each topic: **Winner** = read this first for deepest understanding. **Runner-Up** = supplement with this for a second perspective or quick revision.

| # | Topic | Winner | Runner-Up |
|---|-------|--------|-----------|
| 01 | LLM Foundations | **`research_opus_4.6`** (1438 lines, deepest transformer internals, "Key Numbers" appendix) | `research_cursor_grok` (1208 lines, 93 sources, interview-explicit framing) |
| 02 | Context Engineering | **`research_opus_4.6`** (2746 lines — massively deeper than all others) | `research_cursor_grok` (1071 lines, production-grounded with vendor specifics) |
| 03 | Tool Use | **`research_opus_4.6`** (2191 lines, 4-stage pipeline, browser automation plane) | `research_cursor_grok` (1243 lines, MCP protocol details, sandbox comparisons) |
| 04 | Agent Architecture | **`research_opus_4.6`** (1779 lines, exceptional depth on orchestration patterns) | `research_cursor_grok` (1177 lines, strong enterprise topology) |
| 05 | Agent Frameworks | **`research_opus_4.6`** (1156 lines) | `research_cursor_grok` (1113 lines, real framework comparisons with source backing) |
| 06 | RAG | **`research_cursor_grok`** (1257 lines, 78 sources, ingest/query plane split, graph RAG depth) | `research_opus_4.6` (894 lines — notably thinner here) |
| 07 | Memory | **`research_cursor_grok`** (1398 lines, 89 sources, write/read plane split, CoALA framework, GDPR/Art.17) | `research_gpt_5.4` (1098 lines, good narrative flow) |
| 08 | Planning & Reasoning | **`research_cursor_grok`** (1087 lines, grounded in latest reasoning research) | `research_gpt_5.4` (913 lines, clean explanations) |
| 09 | Multi-Agent Systems | **`research_cursor_grok`** (1372 lines, deepest multi-agent patterns) | `research_cursor_sonnet` (946 lines, consistent quality) |
| 10 | MCP & Interoperability | **`research_cursor_grok`** (1208 lines, protocol-level detail) | `research_cursor_sonnet` (1006 lines, strong architecture) |
| 11 | Specialized Agents | **`research_cursor_grok`** (937 lines) | `research_gpt_sol` (935 lines, good trade-off matrices + interview review) |
| 12 | Evaluation | **`research_cursor_grok`** (1228 lines, 68 sources, dual oracle concept, LangSmith/Braintrust specifics) | `research_gpt_sol` (924 lines, clean "Interview Review" Q&A) |
| 13 | Security & Guardrails | **`research_cursor_grok`** (1223 lines, deepest threat model coverage) | `research_gpt_sol` (1000 lines, OWASP mapping + interview review) |
| 14 | Observability | **`research_cursor_grok`** (1281 lines, comprehensive telemetry patterns) | `research_gpt_5.4` (962 lines, clear narrative) |
| 15 | Inference Optimization | **`research_cursor_grok`** (1331 lines, disaggregated serving, NIXL/Mooncake specifics) | `research_gpt_5.4` (1081 lines, accessible caching & routing explanation) |
| 16 | Production | **`research_gpt_sol`** (1171 lines, strongest trade-off matrices + explicit interview review) | `research_cursor_grok` (1111 lines, broad production patterns) |
| 17 | Advanced Autonomous Agents | **`research_cursor_grok`** (1294 lines, deepest autonomous agent coverage) | `research_gpt_sol` (1027 lines, interview review section) |

## Study Strategy

### Primary Reading Plan

**Modules 01–05 (Foundations):** Read `research_opus_4.6` first — it's 2–3x deeper than everything else on these foundational topics. Then read the corresponding `research_cursor_grok` module for interview-specific framing and enterprise production context.

**Modules 06–17 (Applied & Production):** Read `research_cursor_grok` as your primary source — it's consistently the deepest and most interview-grounded across all these topics. Then skim `research_gpt_sol` for its concise "Interview Review" Q&A bullets at the end of each module — perfect for last-minute recall.

### Quick Revision (Night Before)

The `research_gpt_sol` "Interview Review" sections distill each topic into 5–6 sharp one-liner answers. Skim all 17 of those sections in sequence for a rapid refresher.

### If You Only Have 3 Days

| Day | Topics | Primary Source |
|-----|--------|----------------|
| 1 | 01 LLM Foundations, 02 Context Engineering, 03 Tool Use | `research_opus_4.6` |
| 2 | 04 Agent Architecture, 06 RAG, 07 Memory, 09 Multi-Agent | `research_cursor_grok` |
| 3 | 12 Evaluation, 13 Security, 15 Inference Optimization, 16 Production | `research_cursor_grok` + `research_gpt_sol` interview review sections |

### Interview Prep Tips

- For each module, extract: 3 architecture patterns, 3 failure modes, 2 security risks, 1 trade-off you can defend out loud.
- Practice whiteboarding the ASCII topology diagrams from memory (control plane / data plane / tool proxies / persistence / telemetry).
- Be ready to talk in layers: model → context → tools → orchestration → memory → evaluation → security → production.
- Anchor answers on trade-offs, not tool names. "I'd choose X because of Y constraint" beats "I'd use LangGraph."
- The `research_cursor_grok` modules explicitly frame content around "interview answers that fail when the follow-up is..." — use those as self-test prompts.

## Module Index (All Topics)

1. **LLM Foundations** — Transformers, reasoning, function calling, structured output
2. **Context Engineering** — Prompting, context management, compression, caching
3. **Tool Use** — APIs, function calling, browser automation, code execution
4. **Agent Architecture** — ReAct, loops, planning, state, workflows
5. **Agent Frameworks** — LangGraph, OpenAI Agents SDK, Google ADK, CrewAI
6. **RAG** — Hybrid search, reranking, Agentic RAG, Graph RAG
7. **Memory** — Short/long-term, semantic, episodic, memory retrieval
8. **Planning & Reasoning** — Decomposition, reflection, verification, replanning
9. **Multi-Agent Systems** — Supervisor, worker, collaboration, delegation
10. **MCP & Interoperability** — Tools, resources, MCP servers/clients
11. **Specialized Agents** — Coding, browser, research, data agents
12. **Evaluation** — Task success, trajectory, tool accuracy, quality, cost, latency
13. **Security & Guardrails** — Prompt injection, permissions, sandboxing, policies
14. **Observability** — Tracing, logging, monitoring, agent trajectories
15. **Inference Optimization** — Caching, routing, batching, quantization
16. **Production** — Docker, Kubernetes, APIs, queues, scaling, reliability
17. **Advanced Autonomous Agents** — Long-horizon tasks, agent environments, self-improvement
