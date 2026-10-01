# AI Engineering — Study Notes

17 topics from [Neo Kim’s reading list](https://substack.com/@systemdesignone/note/c-347791340), written for personal study and Principal AI Architect interview prep.

Each topic has a cited research note in `research/` and a study module in `modules/`. Every module has the same six parts: system topology, mechanics, token economics and SLAs, resilience and security, runnable Python, and two design scenarios with trade-off matrices.

Study the modules. Open the research note when you need the source behind a number.

## Modules

| # | Topic | Study module | Source article |
|---|-------|--------------|----------------|
| 01 | Spec Driven Development for AI Agents | [module](modules/01-spec-driven-development-for-ai-agents.md) | [article](https://newsletter.systemdesign.one/p/spec-driven-development-ai-agents) |
| 02 | How AI Agents Work | [module](modules/02-how-ai-agents-work.md) | [article](https://newsletter.systemdesign.one/p/ai-agents-explained) |
| 03 | Incident Response AI Agent | [module](modules/03-incident-response-ai-agent.md) | [article](https://newsletter.systemdesign.one/p/how-do-ai-agents-work) |
| 04 | How MCP Works | [module](modules/04-how-mcp-works.md) | [article](https://newsletter.systemdesign.one/p/how-mcp-works) |
| 05 | LLM Concepts | [module](modules/05-llm-concepts.md) | [article](https://newsletter.systemdesign.one/p/llm-concepts) |
| 06 | How RAG Works | [module](modules/06-how-rag-works.md) | [article](https://newsletter.systemdesign.one/p/how-rag-works) |
| 07 | Prompt Engineering | [module](modules/07-prompt-engineering.md) | [article](https://newsletter.systemdesign.one/p/prompt-engineering-guide) |
| 08 | Agentic Patterns | [module](modules/08-agentic-patterns.md) | [article](https://newsletter.systemdesign.one/p/agentic-design-patterns) |
| 09 | Multi-Agent Architecture | [module](modules/09-multi-agent-architecture.md) | [article](https://newsletter.systemdesign.one/p/multi-agent-system) |
| 10 | Memory, State & Consistency | [module](modules/10-ai-agent-memory-state-consistency.md) | [article](https://newsletter.systemdesign.one/p/ai-agent-memory) |
| 11 | Vector Databases | [module](modules/11-how-vector-databases-work.md) | [article](https://newsletter.systemdesign.one/p/what-is-a-vector-database) |
| 12 | Fine-Tuning | [module](modules/12-how-fine-tuning-works.md) | [article](https://newsletter.systemdesign.one/p/fine-tuning-ai-models) |
| 13 | Evals, Guardrails & Security | [module](modules/13-ai-evals-guardrails-security.md) | [article](https://newsletter.systemdesign.one/p/llm-evaluation-and-guardrails) |
| 14 | AI Research Agent | [module](modules/14-ai-research-agent.md) | [article](https://newsletter.systemdesign.one/p/how-to-build-an-ai-research-agent-with-mcp) |
| 15 | How OpenClaw Works | [module](modules/15-how-openclaw-works.md) | [article](https://newsletter.systemdesign.one/p/openclaw-architecture) |
| 16 | A2A Protocol | [module](modules/16-how-a2a-protocol-works.md) | [article](https://newsletter.systemdesign.one/p/agent-to-agent-protocol) |
| 17 | Personal AI Chat Assistant | [module](modules/17-personal-ai-chat-assistant.md) | [article](https://newsletter.systemdesign.one/p/ai-chat-assistant) |

## Suggested order

1. **Foundations:** 05 LLM concepts → 07 Prompt engineering → 12 Fine-tuning
2. **Retrieval:** 11 Vector databases → 06 RAG
3. **Agents:** 02 How agents work → 08 Patterns → 10 Memory → 09 Multi-agent → 04 MCP → 16 A2A
4. **Production:** 13 Evals and guardrails → 01 Spec-driven development → 03 Incident response → 14 Research agent → 15 OpenClaw → 17 Chat assistant

Where a vendor does not publish latency percentiles, the module keeps that gap and shows an inferred millisecond budget with the assumptions written out.
