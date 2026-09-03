# AI and Deep Agents Study Index

This folder now has two linked tracks:

- `01` to `07`: general AI systems and interview fundamentals
- `08` to `23`: Deep Agents concepts from the LangChain Python OSS docs

Use this index when you want a clear read order instead of jumping between files.

## Recommended Order

### Phase 1: Core AI System Design

Start here if your goal is general AI architecture interviews.

1. [RAG](01-rag.md)
2. [Fine-tuning](02-fine-tuning.md)
3. [Caching](03-caching.md)
4. [Evals](04-evals.md)
5. [Observability](05-observability.md)
6. [Agent Feedback Loops](06-agent-feedback-loops.md)
7. [Guardrails](07-guardrails.md)

Why this order:

- `RAG`, `Fine-tuning`, and `Caching` define how knowledge, behavior, and latency/cost are shaped.
- `Evals` and `Observability` explain how you know the system works in practice.
- `Agent Feedback Loops` and `Guardrails` cover control, iteration, and safety.

### Phase 2: Deep Agents Foundations

Start this phase after you understand the general AI system concepts above.

8. [Deep Agents Overview](08-deep-agents-overview.md)
9. [Execution Environment](09-execution-environment.md)
10. [Tools and MCP](10-tools-and-mcp.md)
11. [Virtual Filesystem](11-virtual-filesystem.md)
12. [Filesystem Permissions](12-filesystem-permissions.md)
13. [Code Execution](13-code-execution.md)
14. [Streaming](14-streaming.md)

Why this order:

- `Overview` gives the harness mental model.
- `Execution Environment` and `Tools and MCP` explain how the agent acts.
- `Virtual Filesystem`, `Filesystem Permissions`, and `Code Execution` define the operating surface and safety boundary.
- `Streaming` shows how runs are observed in real time.

### Phase 3: Deep Agents Context and Orchestration

Finish with the parts that explain how Deep Agents manage long tasks and human control.

15. [Context Management](15-context-management.md)
16. [Skills](16-skills.md)
17. [Memory](17-memory.md)
18. [Summarization and Context Offloading](18-summarization-and-context-offloading.md)
19. [Prompt Caching](19-prompt-caching.md)
20. [Delegation](20-delegation.md)
21. [Task Planning](21-task-planning.md)
22. [Subagents](22-subagents.md)
23. [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

Why this order:

- `Context Management` is the umbrella concept for what the agent knows and keeps.
- `Skills`, `Memory`, `Summarization`, and `Prompt Caching` explain the four main context layers.
- `Delegation`, `Task Planning`, and `Subagents` explain how Deep Agents break work apart.
- `Steering and Human-in-the-Loop` closes with runtime control and approvals.

## Fast Review Paths

### 60-minute interview cram

Read these if you need the highest-yield concepts quickly:

1. [RAG](01-rag.md)
2. [Caching](03-caching.md)
3. [Evals](04-evals.md)
4. [Observability](05-observability.md)
5. [Guardrails](07-guardrails.md)
6. [Deep Agents Overview](08-deep-agents-overview.md)
7. [Tools and MCP](10-tools-and-mcp.md)
8. [Subagents](22-subagents.md)
9. [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

### Deep Agents implementation path

Use this when the interview is likely to ask how you would build or customize a Deep Agents app:

1. [Deep Agents Overview](08-deep-agents-overview.md)
2. [Execution Environment](09-execution-environment.md)
3. [Tools and MCP](10-tools-and-mcp.md)
4. [Virtual Filesystem](11-virtual-filesystem.md)
5. [Filesystem Permissions](12-filesystem-permissions.md)
6. [Code Execution](13-code-execution.md)
7. [Context Management](15-context-management.md)
8. [Skills](16-skills.md)
9. [Memory](17-memory.md)
10. [Subagents](22-subagents.md)
11. [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

### Operations and production path

Use this when you want the reliability and safety story:

1. [Caching](03-caching.md)
2. [Evals](04-evals.md)
3. [Observability](05-observability.md)
4. [Guardrails](07-guardrails.md)
5. [Filesystem Permissions](12-filesystem-permissions.md)
6. [Code Execution](13-code-execution.md)
7. [Streaming](14-streaming.md)
8. [Summarization and Context Offloading](18-summarization-and-context-offloading.md)
9. [Prompt Caching](19-prompt-caching.md)
10. [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

## Interview Theme Map

Use this when you want to answer a specific style of question.

- `RAG vs fine-tuning vs caching`:
  [RAG](01-rag.md), [Fine-tuning](02-fine-tuning.md), [Caching](03-caching.md)

- `How do you evaluate and operate AI systems in production?`:
  [Evals](04-evals.md), [Observability](05-observability.md), [Caching](03-caching.md), [Guardrails](07-guardrails.md)

- `How do you keep agents from going off the rails?`:
  [Agent Feedback Loops](06-agent-feedback-loops.md), [Guardrails](07-guardrails.md), [Filesystem Permissions](12-filesystem-permissions.md), [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

- `How does Deep Agents actually work under the hood?`:
  [Deep Agents Overview](08-deep-agents-overview.md), [Execution Environment](09-execution-environment.md), [Tools and MCP](10-tools-and-mcp.md), [Virtual Filesystem](11-virtual-filesystem.md), [Code Execution](13-code-execution.md)

- `How does Deep Agents manage context over long runs?`:
  [Context Management](15-context-management.md), [Skills](16-skills.md), [Memory](17-memory.md), [Summarization and Context Offloading](18-summarization-and-context-offloading.md), [Prompt Caching](19-prompt-caching.md)

- `How do subagents, planning, and approvals fit together?`:
  [Delegation](20-delegation.md), [Task Planning](21-task-planning.md), [Subagents](22-subagents.md), [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)

## Suggested Study Cadence

### Pass 1: Read for structure

- Read `01` to `07` once without memorizing details.
- Read `08` to `23` once with attention to exact Deep Agents APIs and config names.

### Pass 2: Rehearse orally

- Use the `Interview Q&A` section in each note.
- Answer out loud before looking at the written answer.

### Pass 3: Build mental comparisons

- Compare `RAG` vs `Fine-tuning`
- Compare `Caching` vs `Prompt Caching`
- Compare `Memory` vs `Skills`
- Compare `Delegation` vs `Subagents`
- Compare `Permissions` vs `Steering`

### Pass 4: Whiteboard mode

Practice explaining these flows from memory:

- A production RAG pipeline
- A Deep Agents execution environment
- A safe tool call with MCP plus human approval
- A long-running agent with memory, summarization, and subagents

## High-Yield Files

If you only revisit a handful before an interview, use:

1. [RAG](01-rag.md)
2. [Caching](03-caching.md)
3. [Evals](04-evals.md)
4. [Observability](05-observability.md)
5. [Guardrails](07-guardrails.md)
6. [Deep Agents Overview](08-deep-agents-overview.md)
7. [Tools and MCP](10-tools-and-mcp.md)
8. [Context Management](15-context-management.md)
9. [Subagents](22-subagents.md)
10. [Steering and Human-in-the-Loop](23-steering-and-human-in-the-loop.md)
