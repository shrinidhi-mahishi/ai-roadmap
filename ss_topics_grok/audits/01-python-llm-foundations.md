## Module: 01-python-llm-foundations.md

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
- Diagram present with box-drawing characters (`┌─┐│└─┘├┤┬┴─`) at §1.1 lines 40–124.
- Planes mapped in the diagram and the accompanying plane table (§1.1 lines 126–135): **Control** (lines 47–64), **Data** generation + embedding (lines 69–89), **Tool proxies** (lines 94–105), **Persistence** (lines 95–109), **Telemetry** (lines 111–123).
- Request-flow narrative is §1.2 lines 137–150 (12-step ingress → policy → count → compile → dispatch → prefill/encode → decode → stream parse → tool proxy → cancel → persist → audit).

**2. Token Cost Economics & SLA — PASS**
- Explicit `$ per 1k runs` formula and assumptions at §3.1 lines 330–356: workload **W** = 2,000 input + 800 output, 80% cache hit (1,600 cache-read / 400 uncached), warm cache (no write), n = 1,000; formula \(C = n \cdot (T_{\mathrm{miss}} P_{\mathrm{miss}} + T_{\mathrm{hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}) / 10^{6}\); table gives **$/1k runs** (e.g. Sonnet 5 cached **$9.12**, uncached **$12.00**, Batch **$6.00**; GPT-5.4 **$13.40**).
- Latency tiers with mitigations at §3.2 lines 380–388: **p50 TTFT < 800 ms**, **p95 TTFT < 2 s**, **p99 TTFT/hang** fail-closed on idle gap (watchdog / `asyncio.timeout`; default 600 s × 3 ≈ 30 min footgun called out). Also p50 TPOT and p95 time-to-final (16 s @ 50 tok/s, 32 s @ 25 tok/s).
- Throughput and back-pressure at §3.3 lines 392–428: OpenAI/Anthropic tier RPM/TPM/OTPM tables, 50-stream and 50k RPM worked examples, admit-on-breaker+semaphore+token-bucket, bulkheads, bounded embed `Queue`, shed path (Batch → degrade model → deterministic JSON).

**3. NFR Trade-offs — PASS**
- Availability, RPO, RTO, compliance stated with working targets at §3.4 lines 430–442 (availability 99.9% gateway; RPO=0 for irreversible tools / KV cache minutes; RTO < 1 s interactive failover; ZDR/geo compliance).
- Competing-NFR tensions in the same table: multi-vendor fallback vs output-distribution drift; KV-as-RPO=0; failover vs bit-identical tokens (T>0); residency vs latency vs price; cost vs latency (Haiku **$4.56/1k** vs Opus 5 **$22.80/1k** vs Fast/Batch); cache hit-rate vs tenancy leak.

**4. Distributed Resilience — PASS**
- Durable execution: Temporal (workflow = conversation, activities idempotent, `workflow-id = tenant:thread_id`, checkpointing, DLQ) and Kafka (outbox, embed ingest, compaction, poison skip) at §4.1 lines 446–477.
- Failure taxonomy: HTTP retry map plus **transient / permanent / poison pill / semantic / partial stream** at §4.2 lines 479–504; Stripe-style idempotency keys for side effects (not token streams).
- Circuit breaker closed → open → half-open (probe cheap Haiku / GPT-4.1-nano; do not trip on 429-with-Retry-After) at §4.3 lines 506–522, including ASCII state diagram.
- Fallback chain: primary (Sonnet 5 / GPT-5.4) → secondary vendor/model → **deterministic schema-valid JSON** at §4.3 lines 524 and encoded in `FallbackChain` / `deterministic_invoice` (§5 lines 814–823, 1083–1123).

**5. Enterprise Security Boundaries — PASS**
- Zero-Trust MCP at §4.4 lines 530–536: model as untrusted planner; short-lived audience-bound tickets; identity from gateway token/RunContext not model JSON; private egress; block instance metadata; allowlists by method+resource.
- Tool-level RBAC / least privilege per turn at §4.4 lines 538 (attach only requested tools; disable parallel writes; HITL on irreversible tools).
- PII pipeline detect → redact → audit at §4.4 lines 540–546 (pre-tokenize/pre-embed DLP; stable placeholders; WORM redaction map; vectors treated as derived personal data / Vec2Text).
- Immutable telemetry / chain-of-custody at §4.4 lines 548 (WORM: correlation_id, tenant, model, request_id, usage, stop_reason, tool names, SHA-256 of redacted prompt, policy snapshot, ticket id, breaker state; reconstruct policy + model + sampled turn + tool results + HITL). Topology telemetry sinks at §1.1 lines 111–122 (`Audit (WORM)`).

**6. Design Case Studies — PASS**
- Exactly two scenarios in §6 (header at line 1332: “Exactly two enterprise designs”).
- **Scenario 1** (§6 lines 1334–1388): B2B copilot 50k streaming chat req/min + 10M-chunk nightly embed; ASCII component diagram lines 1340–1372; 3-way matrix (A/B/C) across cost, latency, ops complexity, security, scalability lines 1378–1386; rationale recommends **B** (multi-provider bulkheaded gateway + Batch embed) lines 1388.
- **Scenario 2** (§6 lines 1390–1439): AP invoice extract 200/min, PII/ZDR, schema-valid JSON ≥99%, p95 < 8 s; ASCII component diagram lines 1396–1423; 3-way matrix lines 1429–1437; rationale recommends **B** (native strict parse, sync-only, Temporal, PII-before-tokenize, deterministic last mile) lines 1439.

### STATUS: APPROVED
