# Agentic AI Roadmap 2026 — Study Guide

17-topic interview prep for Agentic AI, researched across 5 independent model passes. Each version brings a different strength — this guide tells you which to read first per topic.

## Consolidated Study Guide (Start Here)

**[`consolidated_study_guide.md`](consolidated_study_guide.md)** — A single 8,300-line document covering all 17 topics, synthesized from the best sources (winner + runner-up) for each topic.

Every module follows a consistent structure:

| Section | Purpose |
|---------|---------|
| **What Is This?** | Plain-language explanation with analogies and examples — no jargon assumed |
| **Why It Matters** | Practical importance in 2-3 sentences |
| **Core Content** | Architecture patterns, trade-offs, production details, ASCII diagrams |
| **Common Failure Modes** | Structured table: cause, detection, mitigation |
| **Key Takeaways** | 5-8 bullet-point summary |
| **Interview Q&A** | 10-12 first-person Q&A pairs you can practice out loud |
| **Key Numbers to Memorize** | Categorized statistics and benchmarks |
| **Quick Reference** | Decision trees, checklists, cheat sheets |

**How to use it:**
- **Learning from scratch?** Read the "What Is This?" sections first for all 17 topics to build the mental map, then go deep per module.
- **Interview prep?** Focus on the Interview Q&A and Key Numbers sections — cover the answer, try to answer from memory.
- **Night-before revision?** Skim the Quick Reference and Key Takeaways across all modules.

### Also available: `final/` (deep-dive per-topic files)

The `final/` directory contains 16 individual module files (missing Module 15) with even more depth — more code examples, larger failure mode tables, and exhaustive reference material. Use these when the consolidated guide's coverage of a specific topic isn't enough.

## Raw Research Versions

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

### Recommended Flow

1. **First pass** — Read `consolidated_study_guide.md` end to end. The "What Is This?" intros build foundational understanding; the core content covers architecture and production patterns.
2. **Active recall** — Go back through the Interview Q&A sections. Cover the answer, attempt the question, check yourself.
3. **Memorization** — Drill the Key Numbers tables. These are the statistics interviewers expect you to know.
4. **Deep dives** — For topics where you want more depth, read the `final/` file or the raw research versions below.
5. **Night before** — Skim Quick Reference sections and Key Takeaways across all 17 modules.

### If You Only Have 3 Days

| Day | Topics | What to Read |
|-----|--------|--------------|
| 1 | 01 LLM Foundations, 02 Context Engineering, 03 Tool Use, 04 Agent Architecture | Consolidated guide modules 01-04 (basics + core + Q&A) |
| 2 | 06 RAG, 07 Memory, 09 Multi-Agent, 10 MCP, 12 Evaluation | Consolidated guide modules 06-12 |
| 3 | 13 Security, 15 Inference Optimization, 16 Production, 17 Autonomous Agents | Consolidated guide modules 13-17 + all Key Numbers tables |

### Interview Prep Tips

- For each module, extract: 3 architecture patterns, 3 failure modes, 2 security risks, 1 trade-off you can defend out loud.
- Practice the Interview Q&A sections out loud — the answers are written in first person, ready to speak verbatim.
- Practice whiteboarding the ASCII topology diagrams from memory (control plane / data plane / tool proxies / persistence / telemetry).
- Be ready to talk in layers: model → context → tools → orchestration → memory → evaluation → security → production.
- Anchor answers on trade-offs, not tool names. "I'd choose X because of Y constraint" beats "I'd use LangGraph."

### Going Deeper (Raw Research)

For any topic where the consolidated guide isn't enough, read the raw research versions:

**Modules 01–05 (Foundations):** Read `research_opus_4.6` — it's 2–3x deeper than everything else on foundational topics.

**Modules 06–17 (Applied & Production):** Read `research_cursor_grok` — consistently the deepest and most interview-grounded. Supplement with `research_gpt_sol` for its concise "Interview Review" Q&A bullets.

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
