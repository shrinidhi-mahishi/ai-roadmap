# Agent Architecture Study Notes

Personal study + interview prep. Researched **2026-09-23**. Pipeline: [roadmap-researcher](../.claude/agents/roadmap-researcher.md) → [roadmap-writer](../.claude/agents/roadmap-writer.md) → [roadmap-auditor](../.claude/agents/roadmap-auditor.md). Every study module is **STATUS: APPROVED** on all six Principal Architect criteria (ASCII topology, `$ / 1k` + p50/p95/p99, NFR trade-offs, durable execution, Zero-Trust MCP / RBAC / PII / WORM, exactly two design scenarios).

List prices, RPM/TPM, and SKUs are captured from vendor pages on that date. Figures marked **[inferred]** are arithmetic from a stated workload × published rates, not a vendor SKU. Do not quote inferred `$ / 1k` as list prices.

**Start here for interviews.** Read the numbered files in this folder in order. Use `research/` when you need the citation dump; use `audits/` only if you want the pass/fail evidence.

---

## How to use these notes

1. **First pass (product sense):** read the opening “What / Why / traps” plus §1 topology and the two §6 scenarios. That is enough to answer “design a copilot / RAG / multi-agent support desk.”
2. **Second pass (mechanics):** §2 + the runnable Python in §5. Recite the state machines and the fuses (`max_turns` vs `recursion_limit` vs `max_budget_usd`).
3. **Third pass (numbers):** §3 cost tables and latency mitigations. Always say the **workload skeleton** before the dollar figure.
4. **Red-team pass:** §4 failure taxonomy + security. Interviews fail here more often than on transformers.

Topic 05 **picks LangGraph** and goes deep (CrewAI roles and OpenAI Agents SDK handoffs appear only as contrast).

---

## Curriculum

| # | Topic | Study module | Research | Audit | Sources | Module lines |
|---|--------|--------------|----------|-------|---------|--------------|
| 1 | Python & LLM Foundations | [01-python-llm-foundations.md](01-python-llm-foundations.md) | [research](research/01-python-llm-foundations.md) | [APPROVED](audits/01-python-llm-foundations.md) | 96 | 1,475 |
| 2 | Context Engineering | [02-context-engineering.md](02-context-engineering.md) | [research](research/02-context-engineering.md) | [APPROVED](audits/02-context-engineering.md) | 98 | 1,376 |
| 3 | Tool Calling | [03-tool-calling.md](03-tool-calling.md) | [research](research/03-tool-calling.md) | [APPROVED](audits/03-tool-calling.md) | 97 | 1,336 |
| 4 | Agent Loop Patterns | [04-agent-loop-patterns.md](04-agent-loop-patterns.md) | [research](research/04-agent-loop-patterns.md) | [APPROVED](audits/04-agent-loop-patterns.md) | 94 | 1,392 |
| 5 | Master One Framework — LangGraph | [05-langgraph-state-machines.md](05-langgraph-state-machines.md) | [research](research/05-langgraph-state-machines.md) | [APPROVED](audits/05-langgraph-state-machines.md) | 98 | 1,259 |
| 6 | Memory Systems | [06-memory-systems.md](06-memory-systems.md) | [research](research/06-memory-systems.md) | [APPROVED](audits/06-memory-systems.md) | 106 | 1,376 |
| 7 | Agentic RAG | [07-agentic-rag.md](07-agentic-rag.md) | [research](research/07-agentic-rag.md) | [APPROVED](audits/07-agentic-rag.md) | 100 | 1,396 |
| 8 | MCP & Integrations | [08-mcp-integrations.md](08-mcp-integrations.md) | [research](research/08-mcp-integrations.md) | [APPROVED](audits/08-mcp-integrations.md) | 96 | 1,338 |
| 9 | Multi-Agent Orchestration | [09-multi-agent-orchestration.md](09-multi-agent-orchestration.md) | [research](research/09-multi-agent-orchestration.md) | [APPROVED](audits/09-multi-agent-orchestration.md) | 92 | 1,265 |
| 10 | Evals & Observability | [10-evals-observability.md](10-evals-observability.md) | [research](research/10-evals-observability.md) | [APPROVED](audits/10-evals-observability.md) | 99 | 1,323 |
| 11 | Production Deployment | [11-production-deployment.md](11-production-deployment.md) | [research](research/11-production-deployment.md) | [APPROVED](audits/11-production-deployment.md) | 100 | 1,228 |

**Totals:** 1,076 cited sources · ~14.8k lines of study modules · ~6.3k lines of research.

Identical copies of the study modules also live under [`modules/`](modules/) (writer output). Prefer the numbered files in this folder.

---

## Interview-critical numbers (do not mix workloads)

These are the figures you should be able to say out loud. Each one is tied to a **stated skeleton** in the module. Recheck vendor pages before a loop; SKUs moved between OpenAI pricing HTML vs docs on 2026-09-23.

| Say this | Number | Caveat | Module |
|---|---|---|---|
| Sonnet 5 list | **$2 / $10 per MTok**; cache read **0.1×** | 5-min write 1.25×, 1-hour 2× | 01 |
| GPT-5.4 list | **$2.50 / $0.25 cached / $15**; **>272K** input → 2× in / 1.5× out **whole session** | Two concurrent OpenAI pricing pages existed on research day | 01 |
| Batch | **50% off** both vendors | Voyage embeddings 33% | 01 |
| Tokenizer | **Never tiktoken for Claude** (~+30% on 4.7+) | `POST /v1/messages/count_tokens` | 01 |
| Cache prefix | Anthropic **tools → system → messages**, max **4** breakpoints | Unstable tool list = miss | 02 |
| Thinking tokens | Billed as **output**; `display: omitted` does **not** cut the bill | OpenAI reasoning **summaries** are free; raw CoT is hidden | 02 |
| Schema tax | Sonnet 5 hidden **354 / 474** tokens every turn | Computer-use ~4,590 | 02 / 03 |
| Deferred MCP schemas | Cursor A/B **−46.9%** agent tokens on MCP-calling runs | Not a guaranteed saving on every build | 03 / 08 |
| Tool catalog | Anthropic ~**55k** for a 5-server catalog; cut **>85%** with search | Pin image + `count_tokens` | 03 / 08 |
| Turn ≠ turn | OpenAI `max_turns` default **10** (model invocations); Claude Agent SDK default **unlimited** (tool trips); LangGraph `recursion_limit` default **1000** super-steps since 1.0.6 | Old “25” is the prior runnable default | 04 / 05 |
| ReAct signature failure | **47%** reasoning/loop vs **0%** hallucination (Yao labels) | Caps 7/5 steps | 04 |
| Self-correction without a checker | GPT-4 GSM8K **95.5 → 89.0** (Huang) | Oracle-label loops look good because they block correct→incorrect | 04 |
| LangGraph ticket skeleton | GPT-4.1 **$37.20 / 1k** (6 nodes / 3 LLM) | **[inferred]** | 05 |
| Send N=8 research | **$124 / 1k** | **[inferred]**; workers share one super-step | 05 |
| Memory constructor vs stuff | ~**$11–21 / 1k** vs stuff **$59** vs 115k **$237** | **[inferred]** from 01 prices | 06 |
| Mem0 paper p95 | **1.44 s** vs full-context **17.1 s** | Platform v3 scores ≠ 2025 paper | 06 |
| RAG e2e | Hybrid + Voyage rerank + Sonnet 5 ≈ **$14 / 1k** | Embed is pennies; rerank + generate dominate | 07 |
| Citations | Constrain IDs to the **ACL-filtered retrieved set**; ALCE ~**50%** unsupported when you only prompt “cite” | | 07 |
| MCP SKU | **$0**; extra cost is **schema tax + extra RTT** | No vendor `tools/call` p99 | 08 |
| Token passthrough | **Forbidden** — mint a new upstream token (RFC 8707 `resource`) | CVE-2025-66414 DNS rebinding on localhost HTTP | 08 |
| Supervisor + 2 specialists | **$24–32 / 1k** | Anthropic research eval **15×** chat ≈ $135–240 / 1k | 09 |
| HITL | LangGraph interrupt has **no TTL** → timeout-**deny** (AISVS) | Missing `interrupt_on` key auto-approves | 09 |
| Judge | GPT-4 vs human **>80%** agreement; position consistency ~**65%**; JudgeDeceiver ASR ~**90%** (paper) | Swap-and-tie; never a naked p-value gate | 10 |
| RAG CI all-in | ~**$37 / 1k** items | Online 5% judge at 100 QPS is a **$k/day** provider bill | 10 |
| Production mix | 92/6/2 primary/secondary/deterministic ≈ **$19.20 / 1k** simple turns | **Never retry spend-cap 429** | 11 |
| Tools | **At-least-once**; exactly-once is the downstream store (`runId+activityId`) | Dual checkpointer + Temporal = restore mismatch | 05 / 11 |

---

## Module structure (every file)

1. **System Topology & Data Flow** — ASCII control / data / persistence / tool proxies / telemetry + request-flow.
2. **Core Mechanics & Algorithms** — state machines, invariants, complexity.
3. **Token Economics & NFR Analysis** — `$ / 1k`, p50/p95/p99, throughput, availability / RPO / RTO / compliance.
4. **Distributed Resilience & Security** — Temporal/Kafka/checkpointer, breaker, fallbacks, Zero-Trust MCP, RBAC, PII, WORM.
5. **Production Enterprise Code** — retries + jitter, circuit breaker, fallback chain, structured logs, graceful degradation (plus topic-specific Python; several modules include an offline self-test).
6. **Architectural System Design Scenarios** — exactly two, each with ASCII + trade-off matrix + rationale.

---

## Folder layout

```
ss_topics_grok/
  00-index.md                          ← this file
  01-python-llm-foundations.md         ← study (interview-ready)
  …
  11-production-deployment.md
  research/                            ← cited findings (6 dimensions)
  modules/                             ← writer output (same as numbered files)
  audits/                              ← auditor verdicts (all APPROVED)
```

---

## Cross-links (do not recopy)

| If you are in… | Point at, don’t rewrite |
|---|---|
| 02 cache $/token | 01 list prices |
| 03 schema tax / retries | 02 packing; 01 SDK backoff |
| 04 hop `$ / 1k` | 01 prices; 03 tool IDs |
| 05 Pregel / checkpointer | 04 for ReAct paper math |
| 06 Store vs memory product | 05 Store API; 02 compact triggers |
| 07 RAG vs memory | 06 (writable per-user) vs 07 (shared corpus) |
| 08 MCP protocol | 03 JSON Schema / pagination / idempotency |
| 09 multi-agent | 04 loops, 05 graphs, 08 MCP |
| 10 CI gates | consumed by 11 |
| 11 durability | 05 checkpointer; 10 eval gates |
