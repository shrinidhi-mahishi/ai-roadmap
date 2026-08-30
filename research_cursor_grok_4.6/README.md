# Agentic AI Roadmap 2026 (Cursor Grok study set)

Personal study notes and interview prep for the 17-topic Agentic AI Roadmap. Each topic went through researcher → writer → auditor until `STATUS: APPROVED`.

- `research/`: sourced research notes (6 architecture dimensions)
- `modules/`: interview-ready study modules (6-part structure)

## Module Index

1. [`modules/01-llm-foundations.md`](modules/01-llm-foundations.md) — Transformers, reasoning, function calling, structured output
2. [`modules/02-context-engineering.md`](modules/02-context-engineering.md) — Prompting, context management, compression, caching
3. [`modules/03-tool-use.md`](modules/03-tool-use.md) — APIs, function calling, browser, code execution
4. [`modules/04-agent-architecture.md`](modules/04-agent-architecture.md) — ReAct, loops, planning, state, workflows
5. [`modules/05-agent-frameworks.md`](modules/05-agent-frameworks.md) — LangGraph, OpenAI Agents SDK, Google ADK, CrewAI
6. [`modules/06-rag.md`](modules/06-rag.md) — Hybrid search, reranking, Agentic RAG, Graph RAG
7. [`modules/07-memory.md`](modules/07-memory.md) — Short/long-term, semantic, episodic, memory retrieval
8. [`modules/08-planning-reasoning.md`](modules/08-planning-reasoning.md) — Decomposition, reflection, verification, replanning
9. [`modules/09-multi-agent-systems.md`](modules/09-multi-agent-systems.md) — Supervisor, worker, collaboration, delegation
10. [`modules/10-mcp-interoperability.md`](modules/10-mcp-interoperability.md) — Tools, resources, MCP servers/clients
11. [`modules/11-specialized-agents.md`](modules/11-specialized-agents.md) — Coding, browser, research, data agents
12. [`modules/12-evaluation.md`](modules/12-evaluation.md) — Task success, trajectory, tool accuracy, quality, cost, latency
13. [`modules/13-security-guardrails.md`](modules/13-security-guardrails.md) — Prompt injection, permissions, sandboxing, policies
14. [`modules/14-observability.md`](modules/14-observability.md) — Tracing, logging, monitoring, agent trajectories
15. [`modules/15-inference-optimization.md`](modules/15-inference-optimization.md) — Caching, routing, batching, quantization
16. [`modules/16-production.md`](modules/16-production.md) — Docker, Kubernetes, APIs, queues, scaling, reliability
17. [`modules/17-advanced-autonomous-agents.md`](modules/17-advanced-autonomous-agents.md) — Autonomous agents, long-horizon tasks, agent environments

## Suggested Reading Order

Read in this order if you want to move from fundamentals to production systems:

1. `01-llm-foundations`
2. `02-context-engineering`
3. `03-tool-use`
4. `04-agent-architecture`
5. `05-agent-frameworks`
6. `06-rag`
7. `07-memory`
8. `08-planning-reasoning`
9. `09-multi-agent-systems`
10. `10-mcp-interoperability`
11. `11-specialized-agents`
12. `12-evaluation`
13. `13-security-guardrails`
14. `14-observability`
15. `15-inference-optimization`
16. `16-production`
17. `17-advanced-autonomous-agents`

## 2-Week Study Plan

### Week 1: Foundations to Core Agent Systems

**Day 1** — `01-llm-foundations.md`  
Summarize transformers, reasoning tokens, function calling, and structured output. Practice: "How do you separate model reasoning from application-side tool execution?"

**Day 2** — `02-context-engineering.md`  
Prompt packing, compression, prompt caching, context-window failure modes. Practice: "How do you keep context useful without blowing up cost and latency?"

**Day 3** — `03-tool-use.md`  
Tool schemas, routing, browser/code execution, validation. Practice: "What breaks first when LLM tools move into production?"

**Day 4** — `04-agent-architecture.md`  
ReAct, planner-executor, DAG workflows, durable state. Practice drawing a control-plane/data-plane split for an agent runtime.

**Day 5** — `05-agent-frameworks.md`  
Compare LangGraph, OpenAI Agents SDK, Google ADK, CrewAI. Practice: "How do you choose a framework for enterprise use?"

**Day 6** — `06-rag.md`  
Hybrid retrieval, reranking, agentic RAG, Graph RAG. Practice: "When is Graph RAG worth the complexity?"

**Day 7** — `07-memory.md` and `08-planning-reasoning.md`  
Connect memory, decomposition, reflection, and replanning. Practice: "What state should be persisted between agent steps?"

### Week 2: Multi-Agent, Evaluation, Security, and Production

**Day 8** — `09-multi-agent-systems.md`  
Supervisor-worker, delegation, failure containment. Practice: "When should one agent become many?"

**Day 9** — `10-mcp-interoperability.md`  
MCP tools, resources, servers/clients, interoperability. Practice: "Why is MCP important for enterprise agents?"

**Day 10** — `11-specialized-agents.md`  
Coding, browser, research, and data agents. Practice: "What changes when the agent is specialized instead of general-purpose?"

**Day 11** — `12-evaluation.md`  
Task success, trajectory quality, tool accuracy, cost, latency. Practice: "How do you know an agent is actually improving?"

**Day 12** — `13-security-guardrails.md` and `14-observability.md`  
Prompt injection, sandboxing, tracing, auditability. Practice: "How do you make an agent safe and debuggable at the same time?"

**Day 13** — `15-inference-optimization.md` and `16-production.md`  
Routing, batching, caching, scaling, queues, reliability. Practice: "How do you reduce cost without sacrificing quality?"

**Day 14** — `17-advanced-autonomous-agents.md`  
Revisit diagrams, cost formulas, failure modes, and design scenarios. Run a mock interview: one end-to-end architecture, a framework choice, a production-safe multi-agent system, then security, observability, and evaluation.

## Interview Prep Tips

- For each module, extract: 3 architecture patterns, 3 failure modes, 2 security risks, 1 trade-off you can defend out loud.
- Practice whiteboarding the ASCII diagrams from memory.
- Be ready to talk in layers: model, context, tools, orchestration, memory, evaluation, security, and production.
- When giving answers, anchor on trade-offs instead of naming tools only.

## Fast Revision Pass

If you only have 1 day before an interview, focus on:

1. `01-llm-foundations.md`
2. `02-context-engineering.md`
3. `04-agent-architecture.md`
4. `06-rag.md`
5. `09-multi-agent-systems.md`
6. `12-evaluation.md`
7. `13-security-guardrails.md`
8. `16-production.md`

Then skim:

- `10-mcp-interoperability.md`
- `14-observability.md`
- `15-inference-optimization.md`
- `17-advanced-autonomous-agents.md`
