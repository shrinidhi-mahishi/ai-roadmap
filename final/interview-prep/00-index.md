# Interview Prep — Consolidated Study Guide

19-topic interview prep for AI/ML engineering roles, consolidated from three independent model passes (GPT, Grok, Opus). Each module merges the best content from all three sources into a single, comprehensive reference.

**Total**: 19 modules, 14,039 lines, 912 KB

## Module Structure

Every module follows a consistent format:

| Section | Purpose |
|---------|---------|
| **What Is This?** | Plain-language explanation with analogies — no jargon assumed |
| **Core Content** | Architecture, patterns, trade-offs, code examples, comparison tables |
| **Common Failure Modes** | Structured table: cause, detection, mitigation |
| **System Design Scenarios** | Concrete interview-style design problems with trade-off analysis |
| **Interview Q&A** | 10-15 first-person Q&A pairs you can practice out loud |
| **Key Numbers to Memorize** | Categorized statistics and benchmarks |
| **Quick Reference** | Decision trees, checklists, cheat sheets |

---

## Phase 1: Core AI Systems (01-07)

Start here for general AI architecture interviews.

1. [RAG](01-rag.md) — Retrieval-Augmented Generation: embeddings, hybrid search, reranking, chunking, Agentic RAG
2. [Fine-Tuning](02-fine-tuning.md) — SFT, PEFT/LoRA, preference tuning (DPO/RLHF), when to fine-tune vs prompt vs RAG
3. [Caching](03-caching.md) — KV cache, prefix caching, hosted prompt cache, semantic cache, cost optimization
4. [Evals](04-evals.md) — Dual-oracle, pass@k vs pass^k, LLM-as-judge, trajectory evaluation, benchmarks
5. [Observability](05-observability.md) — Tracing, metrics, agent trajectories, tail sampling, vendor comparison
6. [Agent Feedback Loops](06-agent-feedback-loops.md) — Self-correction, reflection, tool retry, human feedback integration
7. [Guardrails](07-guardrails.md) — Prompt injection defense, PDP/PEP, sandboxing, egress control, fail-closed policies

## Phase 2: Deep Agents (08-14)

LangChain's production agent platform — architecture, execution, delegation, and production deployment.

8. [Deep Agents Architecture](08-deep-agents-architecture.md) — Harness, `create_deep_agent`, control plane vs data plane
9. [Deep Agents Execution](09-deep-agents-execution.md) — Sandbox, filesystem, permissions, code execution, streaming
10. [Deep Agents Tools & MCP](10-deep-agents-tools-mcp.md) — Tool sources, MCP integration, A2A protocol, zero-trust security
11. [Deep Agents Context & Memory](11-deep-agents-context-memory.md) — Context management, skills, memory, summarization, prompt caching
12. [Deep Agents Delegation](12-deep-agents-delegation.md) — Task planning, subagents, async delegation, fan-out patterns
13. [Deep Agents Steering & HITL](13-deep-agents-steering-hitl.md) — `interrupt_on`, permission modes, approval flows, human-in-the-loop
14. [Deep Agents Production](14-deep-agents-production.md) — Agent Server, deployment, ecosystem (ACP/A2A), durability, scaling

## Phase 3: Advanced Platform (15-19)

Infrastructure, release engineering, and system design for AI-native platforms.

15. [Embeddings & Vector Databases](15-embeddings-vector-databases.md) — Embedding models, ANN indexes, vector DB comparison, hybrid search
16. [Prompt Engineering](16-prompt-engineering.md) — Few-shot, chain-of-thought, system prompts, structured output patterns
17. [LLMOps & CI/CD](17-llmops-cicd.md) — Model versioning, eval gates, deployment pipelines, rollback, governance
18. [AI System Design](18-ai-system-design.md) — Chatbot, search, copilot, moderation archetypes, 45-minute interview framework
19. [Model Context Protocol](19-model-context-protocol.md) — MCP spec, three primitives, transports, zero-trust, OWASP MCP Top 10

---

## Study Strategy

### Recommended Flow

1. **First pass**: Read all 19 "What Is This?" sections to build the mental map (~30 min)
2. **Deep read**: Work through Phase 1 (01-07) → Phase 2 (08-14) → Phase 3 (15-19)
3. **Active recall**: Practice Interview Q&A sections out loud — cover the answer, attempt the question
4. **Memorization**: Drill Key Numbers tables
5. **Night before**: Skim Quick Reference and Key Takeaways across all modules

### If You Only Have 3 Days

| Day | Topics | Focus |
|-----|--------|-------|
| 1 | 01 RAG, 02 Fine-Tuning, 03 Caching, 04 Evals | Core AI systems — the "what and why" |
| 2 | 05-07 Observability/Feedback/Guardrails, 08-10 Deep Agents core | Operations + agent architecture |
| 3 | 15 Embeddings, 18 AI System Design, 19 MCP + all Q&A sections | Platform + interview practice |

### 60-Minute Interview Cram

Read only these modules' Interview Q&A and Quick Reference sections:

1. [RAG](01-rag.md)
2. [Evals](04-evals.md)
3. [Guardrails](07-guardrails.md)
4. [Deep Agents Architecture](08-deep-agents-architecture.md)
5. [AI System Design](18-ai-system-design.md)
6. [Model Context Protocol](19-model-context-protocol.md)

### Interview Theme Map

| Interview Question Theme | Read These |
|--------------------------|-----------|
| RAG vs fine-tuning vs caching | 01, 02, 03 |
| How do you evaluate AI systems? | 04, 05 |
| How do you keep agents safe? | 06, 07, 13 |
| How do Deep Agents work under the hood? | 08, 09, 10 |
| How do agents manage context and memory? | 11, 03 |
| How do subagents and delegation work? | 12, 14 |
| How do embeddings and vector stores work? | 15, 01 |
| How do you deploy and operate LLM systems? | 14, 17 |
| AI system design round | 18, 01, 07 |
| What is MCP and why does it matter? | 10, 19 |

## Sources

| Source | Files | Total Lines | Key Strength |
|--------|-------|-------------|--------------|
| `interview-prep-gpt/` | 28 | 4,035 | Clean structure, good index, granular topics |
| `interview-prep-grok/` | 18 | 21,847 | Deepest technical detail, production metrics, CVE coverage |
| `interview-prep-opus/` | 17 | 15,603 | Strong code examples, architectural depth, trade-off matrices |
| **Consolidated** | **19** | **14,039** | **Best of all three: accessible + deep + interview-ready** |
