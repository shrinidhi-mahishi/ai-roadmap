# AI Engineering — Consolidated Study Notes

17 topics from [Neo Kim's reading list](https://substack.com/@systemdesignone/note/c-347791340), consolidated from two independent research passes (`ai-engg-grok/` and `ai-engg/`) into a single comprehensive set. Built for Principal AI Architect / Director-level interview prep.

Each module follows a consistent structure:

| Section | Purpose |
|---------|---------|
| **What Is This?** | Plain-language explanation with analogies — no jargon assumed |
| **System Topology** | Architecture with ASCII diagrams, control/data plane split |
| **Mechanics** | How it actually works: algorithms, protocols, trade-offs |
| **Token Economics & SLAs** | Cost formulas, latency budgets, throughput targets |
| **Resilience & Security** | Failure handling, zero-trust patterns, compliance |
| **Failure Modes** | Structured table: cause, detection, mitigation |
| **Code** | Production-style Python examples you can run and adapt |
| **System Design Scenarios** | 2-4 interview-style design problems with trade-off matrices |
| **Interview Q&A** | 10+ first-person Q&A pairs to practice out loud |
| **Key Numbers** | Statistics and benchmarks interviewers expect you to know |
| **Quick Reference** | Decision trees, checklists, cheat sheets |

## Modules

| # | Topic | File | Lines |
|---|-------|------|-------|
| 01 | Spec-Driven Development for AI Agents | [module](01-spec-driven-development.md) | 1,032 |
| 02 | How AI Agents Work | [module](02-how-ai-agents-work.md) | 1,437 |
| 03 | Incident Response AI Agent | [module](03-incident-response-ai-agent.md) | 1,370 |
| 04 | How MCP Works | [module](04-how-mcp-works.md) | 1,653 |
| 05 | LLM Concepts | [module](05-llm-concepts.md) | 713 |
| 06 | How RAG Works | [module](06-how-rag-works.md) | 609 |
| 07 | Prompt Engineering | [module](07-prompt-engineering.md) | 614 |
| 08 | Agentic Patterns | [module](08-agentic-patterns.md) | 970 |
| 09 | Multi-Agent Architecture | [module](09-multi-agent-architecture.md) | 947 |
| 10 | Memory, State & Consistency | [module](10-ai-agent-memory-state-consistency.md) | 1,217 |
| 11 | Vector Databases | [module](11-how-vector-databases-work.md) | 1,536 |
| 12 | Fine-Tuning | [module](12-how-fine-tuning-works.md) | 2,323 |
| 13 | Evals, Guardrails & Security | [module](13-ai-evals-guardrails-security.md) | 2,391 |
| 14 | AI Research Agent | [module](14-ai-research-agent.md) | 949 |
| 15 | How OpenClaw Works | [module](15-how-openclaw-works.md) | 1,365 |
| 16 | A2A Protocol | [module](16-how-a2a-protocol-works.md) | 1,721 |
| 17 | Personal AI Chat Assistant | [module](17-personal-ai-chat-assistant.md) | 1,556 |

**Total: 22,403 lines across 17 modules**

## Suggested Study Order

### 1. Foundations (start here)
05 LLM Concepts → 07 Prompt Engineering → 12 Fine-Tuning

### 2. Retrieval & Memory
11 Vector Databases → 06 How RAG Works → 10 Memory, State & Consistency

### 3. Agents & Orchestration
02 How AI Agents Work → 08 Agentic Patterns → 09 Multi-Agent Architecture → 04 How MCP Works → 16 A2A Protocol

### 4. Production & Security
13 Evals, Guardrails & Security → 01 Spec-Driven Development → 03 Incident Response Agent → 14 AI Research Agent → 15 OpenClaw → 17 Personal AI Chat Assistant

## Fast Review Paths

### 60-Minute Interview Cram

Read these in order for highest-yield coverage:

1. [LLM Concepts](05-llm-concepts.md) — transformer basics, attention, tokens
2. [How RAG Works](06-how-rag-works.md) — retrieval pipeline, hybrid search
3. [How AI Agents Work](02-how-ai-agents-work.md) — ReAct, Plan-Execute, state machines
4. [How MCP Works](04-how-mcp-works.md) — tool protocol, JSON-RPC, transports
5. [Evals, Guardrails & Security](13-ai-evals-guardrails-security.md) — eval framework, prompt injection defense
6. [Multi-Agent Architecture](09-multi-agent-architecture.md) — supervisor, swarm, hierarchical

### Deep Architecture Path

For system-design interview rounds:

1. [LLM Concepts](05-llm-concepts.md)
2. [Agentic Patterns](08-agentic-patterns.md)
3. [How MCP Works](04-how-mcp-works.md)
4. [A2A Protocol](16-how-a2a-protocol-works.md)
5. [Multi-Agent Architecture](09-multi-agent-architecture.md)
6. [Memory, State & Consistency](10-ai-agent-memory-state-consistency.md)
7. [Vector Databases](11-how-vector-databases-work.md)
8. [Evals, Guardrails & Security](13-ai-evals-guardrails-security.md)

### Production & Operations Path

For reliability, cost, and deployment questions:

1. [Spec-Driven Development](01-spec-driven-development.md)
2. [Incident Response AI Agent](03-incident-response-ai-agent.md)
3. [Fine-Tuning](12-how-fine-tuning-works.md)
4. [Evals, Guardrails & Security](13-ai-evals-guardrails-security.md)
5. [How OpenClaw Works](15-how-openclaw-works.md)
6. [Personal AI Chat Assistant](17-personal-ai-chat-assistant.md)

## Interview Theme Map

- **"How do LLMs actually work?"**
  → [05 LLM Concepts](05-llm-concepts.md), [07 Prompt Engineering](07-prompt-engineering.md), [12 Fine-Tuning](12-how-fine-tuning-works.md)

- **"Design a RAG system"**
  → [06 RAG](06-how-rag-works.md), [11 Vector Databases](11-how-vector-databases-work.md), [10 Memory](10-ai-agent-memory-state-consistency.md)

- **"How would you build an AI agent?"**
  → [02 How Agents Work](02-how-ai-agents-work.md), [08 Agentic Patterns](08-agentic-patterns.md), [04 MCP](04-how-mcp-works.md)

- **"How do agents coordinate?"**
  → [09 Multi-Agent](09-multi-agent-architecture.md), [16 A2A Protocol](16-how-a2a-protocol-works.md), [04 MCP](04-how-mcp-works.md)

- **"How do you evaluate and secure AI systems?"**
  → [13 Evals & Security](13-ai-evals-guardrails-security.md), [03 Incident Response](03-incident-response-ai-agent.md)

- **"Walk me through a production AI architecture"**
  → [01 Spec-Driven Dev](01-spec-driven-development.md), [15 OpenClaw](15-how-openclaw-works.md), [17 Chat Assistant](17-personal-ai-chat-assistant.md)

- **"How do you fine-tune models?"**
  → [12 Fine-Tuning](12-how-fine-tuning-works.md), [05 LLM Concepts](05-llm-concepts.md)

## Study Tips

1. **First pass** — Read the "What Is This?" section of all 17 modules to build the mental map.
2. **Active recall** — Use the Interview Q&A sections: cover the answer, speak your response, then check.
3. **Numbers drill** — Memorize the Key Numbers tables — interviewers expect specific figures.
4. **Whiteboard** — Practice drawing the ASCII topology diagrams from memory.
5. **Night before** — Skim Quick Reference cards across all modules.

## Sources

Each module consolidates 4 source files:
- `ai-engg-grok/modules/` — study modules (15,747 lines)
- `ai-engg-grok/research/` — cited research notes (5,142 lines)
- `ai-engg/modules/` — study modules (27,325 lines)
- `ai-engg/research/` — cited research notes (8,357 lines)

Total source material: ~56,571 lines → consolidated to 22,403 lines.
