# AI Interview Prep -- Consolidated Study Guide

Consolidated from three independent research sources (GPT, Grok, Opus) into a single definitive reference. Each file merges ALL unique content -- code samples, architecture diagrams, metrics, formulas, trade-offs, interview Q&A, and system design scenarios.

Research frozen **2026-09-02**. Package pin **`deepagents==0.7.12`**.

## Recommended Study Order

### Phase 1: Core AI System Design

1. [RAG](01-rag.md) -- Retrieval-augmented generation: chunking, embedding, retrieval, reranking, hybrid search, evaluation
2. [Fine-tuning](02-fine-tuning.md) -- When and how to fine-tune vs prompt engineering vs RAG; LoRA, QLoRA, RLHF, DPO
3. [Caching](03-caching.md) -- Semantic caching, prompt caching (Anthropic 5m/1h TTL), KV cache, cost optimization
4. [Evals](04-evals.md) -- Evaluation frameworks, online/offline evals, LLM-as-judge, human eval, metrics, regression testing
5. [Observability](05-observability.md) -- LangSmith, OTel, tracing, metrics, dashboards, dual-instrument pitfalls, cost tracking
6. [Agent Feedback Loops](06-agent-feedback-loops.md) -- Self-correction, reflection, iterative refinement, error recovery patterns
7. [Guardrails](07-guardrails.md) -- Input/output validation, content filtering, PII detection, Zero-Trust MCP, OWASP LLM Top 10

### Phase 2: Deep Agents

8. [Deep Agents Architecture](08-deep-agents-architecture.md) -- Harness assembly, middleware stack, `create_deep_agent`, state graph, slot ordering, token economics
9. [Deep Agents Execution Environment](09-deep-agents-execution.md) -- Sandbox backends, filesystem backends, VFS, `execute`, `BaseSandbox`, `LocalShellBackend`
10. [Deep Agents Tools & MCP](10-deep-agents-tools-and-mcp.md) -- Tool registration, MCP adapters, OAuth 2.1, RFC 8707, hash-pin, gateway PEP
11. [Deep Agents Context & Memory](11-deep-agents-context-and-memory.md) -- Context Hub, Store, memory lifecycle, namespace scoping, prompt caching integration
12. [Deep Agents Delegation & Planning](12-deep-agents-delegation.md) -- Subagent specs, declarative vs compiled vs async, task delegation, GP planner, interpreter
13. [Deep Agents Steering, HITL & Production](13-deep-agents-steering-and-production.md) -- `interrupt_on`, HITL middleware, permission model, Agent Server, deployment, retry layers, streaming, circuit breakers

---

## Fast Review Paths

### 60-Minute Interview Cram

Focus on the highest-yield files that cover the most common interview questions:

1. **[RAG](01-rag.md)** -- Nearly every AI system design interview includes a RAG component
2. **[Evals](04-evals.md)** -- "How do you know it works?" is the question that separates senior from staff
3. **[Guardrails](07-guardrails.md)** -- Safety, PII, prompt injection -- mandatory for production systems
4. **[Deep Agents Architecture](08-deep-agents-architecture.md)** -- The harness, middleware, token economics
5. **[Deep Agents Steering, HITL & Production](13-deep-agents-steering-and-production.md)** -- Human oversight, deployment, failure modes

### System Design Interview

For "Design an AI agent system that..." questions:

1. **[Deep Agents Architecture](08-deep-agents-architecture.md)** -- Start here for the harness and state graph
2. **[Deep Agents Tools & MCP](10-deep-agents-tools-and-mcp.md)** -- Tool integration and security
3. **[Deep Agents Delegation & Planning](12-deep-agents-delegation.md)** -- Multi-agent coordination
4. **[Deep Agents Steering, HITL & Production](13-deep-agents-steering-and-production.md)** -- Human oversight and deployment
5. **[RAG](01-rag.md)** -- If the system needs knowledge retrieval
6. **[Guardrails](07-guardrails.md)** -- Security and safety layer

### Operations & Production Interview

For "How do you run this in production?" questions:

1. **[Observability](05-observability.md)** -- Tracing, metrics, cost tracking
2. **[Deep Agents Steering, HITL & Production](13-deep-agents-steering-and-production.md)** -- Agent Server, retry layers, durability, streaming
3. **[Guardrails](07-guardrails.md)** -- PII pipeline, Zero-Trust MCP
4. **[Evals](04-evals.md)** -- Online evals, regression testing, monitoring
5. **[Caching](03-caching.md)** -- Cost optimization at scale

---

## Interview Theme Map

### "Design a system that..."

| Question Pattern | Primary Files | Key Concepts |
| --- | --- | --- |
| "Design a RAG pipeline for..." | [01-RAG](01-rag.md), [03-Caching](03-caching.md), [04-Evals](04-evals.md) | Chunking strategy, hybrid search, reranking, eval metrics |
| "Design an AI agent that..." | [08-Architecture](08-deep-agents-architecture.md), [10-Tools](10-deep-agents-tools-and-mcp.md), [12-Delegation](12-deep-agents-delegation.md) | Harness, tool registration, subagent patterns |
| "Design a multi-agent system..." | [12-Delegation](12-deep-agents-delegation.md), [08-Architecture](08-deep-agents-architecture.md), [11-Context](11-deep-agents-context-and-memory.md) | Delegation patterns, shared memory, planning |
| "How do you add human oversight?" | [13-Steering & Production](13-deep-agents-steering-and-production.md), [07-Guardrails](07-guardrails.md) | `interrupt_on`, four-tier risk, tiered escalation, HITL != PDP |
| "How do you deploy agents to production?" | [13-Steering & Production](13-deep-agents-steering-and-production.md), [05-Observability](05-observability.md) | Agent Server, four retry layers, durability modes, circuit breakers |

### "How do you handle..."

| Question Pattern | Primary Files | Key Concepts |
| --- | --- | --- |
| "How do you handle failures?" | [13-Steering & Production](13-deep-agents-steering-and-production.md), [06-Feedback Loops](06-agent-feedback-loops.md) | Retry layers, circuit breakers, tiered recovery (retry->fallback->resume->compensate->dead-letter) |
| "How do you handle PII/security?" | [07-Guardrails](07-guardrails.md), [13-Steering & Production](13-deep-agents-steering-and-production.md) | detect->redact->audit, Zero-Trust MCP, HITL PII on cards |
| "How do you control costs?" | [03-Caching](03-caching.md), [08-Architecture](08-deep-agents-architecture.md), [13-Steering & Production](13-deep-agents-steering-and-production.md) | Prompt caching, token economics, $ per 1k runs, trace SKUs |
| "How do you evaluate agent quality?" | [04-Evals](04-evals.md), [05-Observability](05-observability.md) | Online/offline evals, LLM-as-judge, trace-based metrics |
| "How do you handle tool use safely?" | [10-Tools](10-deep-agents-tools-and-mcp.md), [07-Guardrails](07-guardrails.md), [13-Steering & Production](13-deep-agents-steering-and-production.md) | MCP gateway, hash-pin, permission model, sandbox vs HITL |

### "What's the difference between..."

| Question Pattern | Primary Files | Key Concepts |
| --- | --- | --- |
| "RAG vs fine-tuning?" | [01-RAG](01-rag.md), [02-Fine-tuning](02-fine-tuning.md) | When to use each, cost/latency/freshness tradeoffs |
| "Declarative vs compiled subagents?" | [12-Delegation](12-deep-agents-delegation.md) | Inheritance, isolation, interrupt propagation |
| "`reject` vs `respond`?" | [13-Steering & Production](13-deep-agents-steering-and-production.md) | Error vs success status; respond is NOT a deny |
| "Sandbox vs HITL?" | [09-Execution](09-deep-agents-execution.md), [13-Steering & Production](13-deep-agents-steering-and-production.md) | Sandbox bounds blast radius; HITL bounds when to ask |
| "Agent Server vs Temporal?" | [13-Steering & Production](13-deep-agents-steering-and-production.md) | thread_id=workflow_id, checkpoints=history, sweeper=2min |

### Numbers You Must Know

| Number | What | File |
| --- | --- | --- |
| $223 / 1k | Baseline model cost (10-call Sonnet 4.6, cached) | [08](08-deep-agents-architecture.md), [13](13-deep-agents-steering-and-production.md) |
| 93% | Users approve permission prompts (Anthropic analog) | [13](13-deep-agents-steering-and-production.md) |
| 9,999 | Compiled `recursion_limit` (not 10000 sentinel) | [08](08-deep-agents-architecture.md), [13](13-deep-agents-steering-and-production.md) |
| 56.6% | Multi-agent task success rate across 4.5M runs | [13](13-deep-agents-steering-and-production.md) |
| 2 min | Worker sweeper interval for crash recovery | [13](13-deep-agents-steering-and-production.md) |
| 4 retry layers | HTTP(6) -> Node(3) -> PG(3) -> Middleware(2) | [13](13-deep-agents-steering-and-production.md) |
| ~$384/mo | Dedicated Small infrastructure floor | [13](13-deep-agents-steering-and-production.md) |
| 10k/week | GTM agent traffic shape (only named anecdote) | [13](13-deep-agents-steering-and-production.md) |
