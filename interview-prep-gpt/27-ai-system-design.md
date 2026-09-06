# AI System Design

## Why It Matters
AI system design interviews reward clear decomposition more than vendor trivia. The best answers start from workload, user impact, latency, failure tolerance, and data sensitivity, then decide whether the system should only answer, retrieve and cite, or also take actions.

Public end-to-end latency benchmarks across full AI products are limited and rarely transfer cleanly. That is why good interview answers focus on architecture invariants, measurable contracts, and explicit trade-offs instead of pretending one vendor's p95 is universal.

## Mental Model
Start with three early forks:
- answer only vs retrieve vs act
- synchronous response vs asynchronous job
- low-risk assistance vs consequential action

Then design across three planes:
- control plane: routing, quotas, approvals, rollout gates
- data plane: prompts, retrieval, tool I/O, outputs, state
- safety plane: authz, sandboxing, DLP, and human approval

The key design insight is that "can act" changes everything: state management, durability, rollback, and approval become first-class.

## Architecture / Flow
```text
define product goal, users, SLA, success metric, and data sensitivity
  -> choose product shape:
     FAQ chat | RAG search | action-taking copilot | moderation
  -> choose sync response or async job
  -> add retrieval, tools, memory, and state only where needed
  -> add safety and approval layers
  -> instrument evals and observability
  -> canary and rollback
```

Most real answers also reduce to a simple request path:

```text
user -> auth -> router -> cache/retrieve/tools/model -> validator
     -> response or async job ID
```

## Key Concepts
- Reusable answer template:
  - product goal and user
  - inputs and outputs
  - SLOs and business success metric
  - failure tolerance and escalation path
  - data sensitivity and tenancy model
- Synchronous vs asynchronous fork:
  - chat and search often stay synchronous
  - long-running copilots, multi-step research, and approval-heavy workflows usually need async runs with status polling or streaming
- Chatbot and FAQ design:
  - simple prompt plus cache plus retrieval often beats a full agent
  - do not add planning loops unless the task actually needs tool orchestration
- Search and RAG design:
  - separate ingest plane from query plane
  - use hybrid retrieval, reranking, grounded generation, and citations
  - push ACLs before retrieval, not after top-k
- Copilot and agent design:
  - planner or orchestrator
  - tool RBAC and bounded loop caps
  - checkpointing and durable execution
  - human approval for irreversible actions
- Content moderation design:
  - screen inputs before expensive work
  - screen outputs before delivery or action
  - define severity thresholds and human escalation
- Tenancy and access control:
  - namespaces, RLS, ACL pushdown, audit logs, and residency are architecture decisions, not post-processing
- Safety layer:
  - deterministic authorization, sandboxing, DLP, and approval should sit outside the model
  - prompt text alone is not a policy engine
- Evals and observability:
  - offline task sets protect releases
  - online traces, business metrics, and user feedback catch drift
  - production failures should flow back into datasets
- Cost and capacity:
  - route easy tasks to cheaper models
  - use caching, batching, and queue backpressure
  - measure cost per successful outcome, not just per request
- Rollout:
  - use canary or champion-challenger patterns
  - keep rollback paths simple and fast

## Metrics and Formulas to Memorize
- Small-corpus shortcut from Anthropic: below about `~200k` tokens or `~500` pages, stuffing or caching may beat building full RAG
- Typical retrieval funnel: retrieve `50-150` candidates, rerank, send `5-20` passages to generation
- Reliability compounds:
  - `0.95^10 = 59.9%`
  - a 10-step workflow with 95% per-step success is not "basically reliable"
- `99.9%` SLO over `3M` requests means `3,000` bad requests in the budget
- Azure AI Content Safety severity levels: `0`, `2`, `4`, `6`
- Interactive rule of thumb: p95 TTFT under `~1-2s` is a good target; slower paths need strong streaming UX or async handling
- Async rule of thumb: tasks expected to run beyond `~10-30s` usually deserve a job or run model with polling or event streaming
- Public end-to-end p95 and p99 numbers across retrieval + model + tools remain limited; measure locally

## Trade-offs and Failure Modes
- Drawing `LLM + vector DB` without specifying workload, SLA, and failure handling
- Using an agentic architecture for a FAQ problem that a simpler RAG stack already solves
- No async or durable path for multi-step tasks, so users sit on hanging HTTP requests
- Retrieval answers ship without citation discipline or ACL pushdown
- Guardrails exist only as prompt text, not as deterministic policy enforcement
- There is no human handoff for ambiguous or high-risk actions
- No post-deploy evaluation loop, so the system drifts while offline scores stay static
- One expensive frontier model is used for routing, extraction, reasoning, and judging, blowing both latency and budget

## Interview Q&A
**Q: What is the first thing you do in an AI system design interview?**  
A: Define the product contract: user, goal, input/output, success metric, latency target, failure tolerance, and data sensitivity. Architecture choices are downstream of that.

**Q: When should a design go async?**  
A: When work is multi-step, tool-heavy, approval-heavy, or likely to exceed about `10-30s`. Long-running tasks should return a job or run ID, not hold open a fragile HTTP request.

**Q: When is an agent overkill?**  
A: For FAQ, support lookup, or simple extraction where prompt plus cache plus retrieval already solves the task. Agents are justified when the system must plan, use tools, recover, or act.

**Q: RAG or long context?**  
A: Use long context for small, mostly static corpora. Use RAG when knowledge is large, mutable, access-controlled, or must be cited.

**Q: How do you explain multi-tenancy cleanly?**  
A: Enforce tenant and ACL boundaries before retrieval or tool execution. Namespaces, RLS, and audit logs are part of the core design, not an afterthought.

**Q: How would you design a safe action-taking copilot?**  
A: Planner plus bounded tools plus deterministic authz plus sandboxing plus HITL for consequential actions, all backed by durable execution and auditable logs.

**Q: What does a strong rollout plan look like?**  
A: Offline evals first, then low-traffic canary or champion-challenger, online monitoring for safety and cost, and a fast rollback path by alias or config.

**Q: Biggest anti-pattern in AI system design answers?**  
A: Naming vendors instead of contracts. A good answer explains workload shape, failure handling, and safety boundaries before it explains product choices.

## Sources
- Local anchors:
  - `ai-roadmap/interview-prep-gpt/01-rag.md`
  - `ai-roadmap/interview-prep-gpt/04-evals.md`
  - `ai-roadmap/interview-prep-gpt/05-observability.md`
  - `ai-roadmap/interview-prep-gpt/07-guardrails.md`
  - `ai-roadmap/interview-prep-gpt/10-tools-and-mcp.md`
  - `ai-roadmap/interview-prep-gpt/21-task-planning.md`
  - `ai-roadmap/final/ai-concepts/16-production.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [Anthropic Managed Agents engineering](https://www.anthropic.com/engineering/managed-agents)
  - [OpenAI building agents](https://developers.openai.com/tracks/building-agents)
  - [OpenAI file search guide](https://developers.openai.com/api/docs/guides/tools-file-search)
  - [OpenAI eval-driven system design](https://developers.openai.com/cookbook/examples/partners/eval_driven_system_design/receipt_inspection)
  - [Google Cloud RAG architecture reference](https://docs.cloud.google.com/architecture/rag-genai-gemini-enterprise-vertexai)
  - [AWS generative AI security reference architecture](https://docs.aws.amazon.com/prescriptive-guidance/latest/security-reference-architecture-generative-ai/gen-ai-sra.html)
  - [Azure AI Content Safety overview](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/overview)
  - [Amazon Bedrock Guardrails docs](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
