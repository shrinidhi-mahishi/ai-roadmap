# Agentic AI Roadmap 2026

This folder contains a full study set for agentic AI interview preparation:

- `research/`: raw research notes
- `modules/`: polished study modules

## Module Index

1. [`modules/01-llm-foundations.md`](modules/01-llm-foundations.md)
2. [`modules/02-context-engineering.md`](modules/02-context-engineering.md)
3. [`modules/03-tool-use.md`](modules/03-tool-use.md)
4. [`modules/04-agent-architecture.md`](modules/04-agent-architecture.md)
5. [`modules/05-agent-frameworks.md`](modules/05-agent-frameworks.md)
6. [`modules/06-rag.md`](modules/06-rag.md)
7. [`modules/07-memory.md`](modules/07-memory.md)
8. [`modules/08-planning-reasoning.md`](modules/08-planning-reasoning.md)
9. [`modules/09-multi-agent-systems.md`](modules/09-multi-agent-systems.md)
10. [`modules/10-mcp-interoperability.md`](modules/10-mcp-interoperability.md)
11. [`modules/11-specialized-agents.md`](modules/11-specialized-agents.md)
12. [`modules/12-evaluation.md`](modules/12-evaluation.md)
13. [`modules/13-security-guardrails.md`](modules/13-security-guardrails.md)
14. [`modules/14-observability.md`](modules/14-observability.md)
15. [`modules/15-inference-optimization.md`](modules/15-inference-optimization.md)
16. [`modules/16-production.md`](modules/16-production.md)
17. [`modules/17-advanced-autonomous-agents.md`](modules/17-advanced-autonomous-agents.md)

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

**Day 1**
- Read `01-llm-foundations.md`
- Summarize transformers, reasoning tokens, function calling, and structured output in your own words
- Practice answering: "How do you separate model reasoning from application-side tool execution?"

**Day 2**
- Read `02-context-engineering.md`
- Review prompt packing, compression, prompt caching, and context-window failure modes
- Practice answering: "How do you keep context useful without blowing up cost and latency?"

**Day 3**
- Read `03-tool-use.md`
- Focus on tool schemas, tool routing, browser/code execution, and validation layers
- Practice answering: "What breaks first when LLM tools are moved into production?"

**Day 4**
- Read `04-agent-architecture.md`
- Compare ReAct, planner-executor, DAG workflows, and durable state patterns
- Practice drawing a control-plane/data-plane split for an agent runtime

**Day 5**
- Read `05-agent-frameworks.md`
- Compare `LangGraph`, `OpenAI Agents SDK`, `Google ADK`, and `CrewAI`
- Practice answering: "How do you choose a framework for enterprise use?"

**Day 6**
- Read `06-rag.md`
- Focus on hybrid retrieval, reranking, agentic RAG, and graph RAG trade-offs
- Practice answering: "When is Graph RAG worth the complexity?"

**Day 7**
- Read `07-memory.md` and `08-planning-reasoning.md`
- Connect memory, decomposition, reflection, and replanning into a single agent loop
- Practice answering: "What state should be persisted between agent steps?"

### Week 2: Multi-Agent, Evaluation, Security, and Production

**Day 8**
- Read `09-multi-agent-systems.md`
- Focus on supervisor-worker patterns, delegation, and failure containment
- Practice answering: "When should one agent become many?"

**Day 9**
- Read `10-mcp-interoperability.md`
- Focus on MCP tools, resources, servers/clients, and interoperability boundaries
- Practice answering: "Why is MCP important for enterprise agents?"

**Day 10**
- Read `11-specialized-agents.md`
- Compare coding, browser, research, and data agents
- Practice answering: "What changes when the agent is specialized instead of general-purpose?"

**Day 11**
- Read `12-evaluation.md`
- Focus on task success, trajectory quality, tool accuracy, cost, and latency
- Practice answering: "How do you know an agent is actually improving?"

**Day 12**
- Read `13-security-guardrails.md` and `14-observability.md`
- Focus on prompt injection, sandboxing, permissions, tracing, and auditability
- Practice answering: "How do you make an agent safe and debuggable at the same time?"

**Day 13**
- Read `15-inference-optimization.md` and `16-production.md`
- Focus on routing, batching, caching, scaling, queues, and reliability
- Practice answering: "How do you reduce cost without sacrificing quality?"

**Day 14**
- Read `17-advanced-autonomous-agents.md`
- Revisit all diagrams, cost formulas, failure modes, and design scenarios across the full set
- Run a mock interview:
  - Explain one end-to-end agent architecture
  - Defend a framework choice
  - Design a production-safe multi-agent system
  - Walk through security, observability, and evaluation

## Interview Prep Tips

- For each module, extract:
  - 3 architecture patterns
  - 3 failure modes
  - 2 security risks
  - 1 trade-off you can defend out loud
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
