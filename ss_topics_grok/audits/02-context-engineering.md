## Module: 02-context-engineering.md

### Criteria Results
| # | Criterion                        | Verdict |
|---|----------------------------------|---------|
| 1 | ASCII System Topology            | ✅  |
| 2 | Token Cost Economics & SLA        | ✅  |
| 3 | NFR Trade-offs                   | ✅  |
| 4 | Distributed Resilience           | ✅  |
| 5 | Enterprise Security Boundaries   | ✅  |
| 6 | Design Case Studies              | ✅  |

### Deficiencies (if any)
None. All six criteria are met at the cited sections/line ranges below.

**1. ASCII System Topology — PASS**
- Diagram present with box-drawing characters (`┌─┐│└─┘┬┴─`) at §1.1 lines 42–139 (not tables-only; plane table at lines 141–150 is accompanying).
- Planes mapped in the diagram: **Control** (lines 50–73: ingress, policy, event load, few-shot, budget, assembler, compaction, breaker, fallback compile), **Data** generation + thinking stream (lines 79–102: tokenizer → prefill/KV write → prefix reuse → decode/parser; separate thinking stream), **Tool proxies** (lines 108–119: STS/signed scope, sandbox, `defer_loading`), **Persistence** (lines 108–123: durable events, soft KV caches, filesystem offload), **Telemetry** (lines 126–138: WORM audit, metrics, traces, usage/invoice).
- Request-flow narrative is §1.2 lines 152–165 (12-step ingress → policy/delimit → load events → few-shot select → budget allocate → assemble → cache-key/breakpoints → prefill/KV → decode+thinking → tool proxy → compact/offload → emit+audit).

**2. Token Cost Economics & SLA — PASS**
- Explicit `$ per 1k runs` with assumptions at §3.1 lines 331–365. Formula \(C = n \cdot (T_{\mathrm{miss}} P_{\mathrm{miss}} + T_{\mathrm{hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}) / 10^{6}\) (lines 333–335); \(T_{\mathrm{out}}\) includes thinking. Workloads: **C-schema** Sonnet 5 4,354 in / 800 out → uncached **$16.71/1k**, 5m-cached **$9.77/1k** (lines 341–344); **C-agent** table **$15.44 / $55.00 / $21.75** per 1k turns plus **+$20/1k** thinking (lines 348–355); **C-copilot** **$16.20 / $30.60** per 1k (line 363); **C-code** **$11.20 vs $160** per 200-turn session (line 365). Cache write/read multipliers and implicit-breakpoint 1.25× write called out (line 357).
- Latency tiers with mitigations at §3.2 lines 383–391: **p50 TTFT < 1.2 s**, **p95 TTFT < 2.0 s**, **p99 TTFT/hang** fail-closed on overflow (compact/offload at 70%/150k/85%; 20-block interior breakpoint; stream idle watchdog; never non-stream 128k with 600 s × 3). Measured hit P50/P95 table at lines 373–379.
- Throughput and back-pressure at §3.3 lines 393–409: OpenAI **>~15 rpm per `prompt_cache_key`** overflow; Anthropic stampede (N first requests = N writes); compact as second sampled request; schema-deploy 100% miss+write spike; admit iff breaker ∈ {closed, half-open} and packed < cliff; compact before dispatch; split cache-key suffixes; shed (thinking off → names-only tools → drop RAG middle → deterministic stub).

**3. NFR Trade-offs — PASS**
- Availability, RPO, RTO, compliance stated with working targets at §3.4 lines 411–421 (availability 99.9% gateway + fallback compiler; RPO=0 event log / memory files, KV minutes 5m/30m/1h; RTO assembler vN **< 1 s** CPU; packed prompt as data store, DLP + retention, Bedrock/Vertex org-level KV share).
- Competing-NFR tensions in the same table: failover **busts** prefix vs availability; treating KV/packed blob as RPO=0; fast failover vs bit-identical tokens (T>0) vs cache warm; hit rate vs tenant isolation; `pause_after` vs latency; C-agent cached **$15.44/1k** vs uncached **$55** vs thinking **+$20/1k** vs stuff-400k **$160**/200 turns; global cached policy vs tenant docs after breakpoint.

**4. Distributed Resilience — PASS**
- Durable execution: Temporal (workflow = `tenant:thread_id`; activities `load_events` / `assemble` / `model_turn` / `tool_exec` / `compact`; lock; checkpoint hashes; compaction as lossy event; DLQ) and Kafka (`llm.events` outbox, `llm.packed`, `llm.dlq`; assembler as projector; poison skip) at §4.1 lines 430–456.
- Failure taxonomy: **transient / permanent / poison pill / semantic / prefix miss / overflow** at §4.2 lines 458–467; idempotency keys `sha256(tenant|thread_id|tool_name|canonical_json(args)|turn_index)` at line 469; failover map at line 471.
- Circuit breaker closed → open → half-open (do not trip on cache miss / 429+RA / compact; half-open probe cheap Haiku / GPT-4.1, short prefix, thinking off) at §4.3 lines 473–489, including ASCII state diagram.
- Fallback chain: primary (Sonnet 5 / GPT-5.4) → secondary vendor **recompiled** from same IR → degraded pack (drop RAG, names-only tools, thinking off) → deterministic stub (`needles_lost: true`) at §4.3 lines 491; encoded in `FallbackChain` (§5 lines 1040–1083).

**5. Enterprise Security Boundaries — PASS**
- Zero-Trust MCP at §4.4 lines 497–503: model as untrusted planner; short-lived audience-bound tickets; identity from gateway token / RunContext not prompt `tenant_id`; private egress; block instance metadata; allowlists by method+resource; no mid-loop tool add / system promote.
- Tool-level RBAC / least privilege per turn at §4.4 lines 505: attach only this turn’s tools; no convenience `execute_sql`; `defer_loading`; HITL on `bash` / `write_file` / payments; `disable_parallel_tool_use` for writes.
- PII pipeline detect → redact → audit at §4.4 lines 507–512 (edge detect before assemble; redact to stable placeholders; audit placeholder→hash on WORM; compaction/memory/`conversation_history.md` as DLP targets). Code: `redact_pii` §5 lines 605–621.
- Immutable telemetry / chain-of-custody at §4.4 line 523 (per-call immutable: timestamp, `correlation_id`, tenant, model, `request_id`, segment counts, cache r/w, compact trigger, Shield `attackDetected`, SHA-256 of packed **redacted** prompt, tools, ticket id, breaker state; reconstruct policy snapshot + assembler vN + events + packed hash + sampled turn + tool results + HITL). Kafka full log as chain-of-custody (§4.1 line 454). Topology `Audit (WORM)` (§1.1 lines 128–136). Cache isolation units table lines 515–521.

**6. Design Case Studies — PASS**
- Exactly two scenarios in §6 (header at line 1250: “Exactly two enterprise designs”).
- **Scenario 1** (§6 lines 1252–1301): multi-tenant copilot, 200 tenants × 1,000 turns/tenant/day = 200k turns/day; ASCII architecture lines 1258–1285; 3-way matrix (A/B/C) across cost, latency, ops complexity, security, scalability lines 1291–1299; rationale recommends **B** (global policy cached; tenant RAG after breakpoint + workspace/salt isolation) line 1301.
- **Scenario 2** (§6 lines 1303–1342): 200-turn coding agent, 80k avg in / 1k out, 20k tool I/O, GPT-5.4 272K cliff; ASCII architecture lines 1309–1328; 3-way matrix lines 1332–1340; rationale recommends **B** (filesystem offload + 85%/150k compact + names-only tools) line 1342.

### STATUS: APPROVED
