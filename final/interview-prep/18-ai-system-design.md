# Module 18: AI System Design

## What Is This?

Traditional system design interviews ask "Design YouTube" -- the focus is data models, API design, scaling, and consistency. AI system design interviews ask "Design ChatGPT" or "Design GitHub Copilot" -- the focus shifts to **model selection, RAG pipelines, guardrails, evaluation, cost/latency tradeoffs, safety, and feedback loops**.

An analogy: traditional system design is like designing a factory -- you control every machine, every input, every output. AI system design is like designing a factory where one of the machines is a very talented but unreliable employee who sometimes makes things up, occasionally ignores instructions, and whose output quality changes without warning. Your architecture must account for that unreliability at every layer.

This is a **composition round**: you assemble modules (RAG, embeddings, prompt engineering, LLMOps, guardrails, agents) into a working product under 45-60 minutes. Four whiteboard archetypes: **chatbot**, **search**, **copilot**, **moderation**. Guardrails and Zero-Trust MCP are **default boxes on every board**, drawn in the HLD -- not polish in the last two minutes. **The model is never the PDP (Policy Decision Point).**

---

## Part 1: System Topology & Data Flow

### Five-Plane Architecture

Five planes, not a blob of intelligence. Draw the generic product first. Archetype deltas add/drop boxes.

```
                         TELEMETRY / OBSERVABILITY SINKS
         +--------------------------------------------------------------+
         |  traces (ACL-redacted spans)   $/tokens   queue depth         |
         |  nDCG@k / task metric SIDECAR -- judge OFF user p99           |
         |  WORM: (ts, actor_id, action, policy_hash, scores, prev_hash)|
         |  flag assignment: salt, pct, thread_id stickiness             |
         +--------^---------------------^------------------^------------+
                  | spans               | meters            | audit
+-- CONTROL PLANE (LLM-free at admit: policy, identity, blast radius) --+
|  +----------+ +----------+ +----------+ +----------+ +---------+      |
|  | IdP->PEP | | PAP/PDP  | | bundle   | | hop cap/ | | HITL    |     |
|  | JWT      | | Cedar/OPA| | alias    | | spend    | | queue   |     |
|  | tenant   | | side     | | sticky   | | ledger   | | pause   |     |
|  +----+-----+ +----+-----+ +----+-----+ +----+-----+ +----+----+    |
|       v            v            v            v            v           |
|  +----------------------------------------------------------------+  |
|  | Resolve principal + bundle + policy_hash. Flag SDK local.      |  |
|  | MCP allowlist + toolSurfaceHash. Judge is NOT on this path.    |  |
|  | Pin: model_id, prompt_hash, schema_hash, decoding_params,      |  |
|  |      index_gen, eval_suite_id. Tag production = POINTER.       |  |
|  +----------------------------------------------------------------+  |
+------------------------------+----------------------------------------+
                               | resolved principal + bundle + policy hash
                               v
+-- DATA PLANE (untrusted token stream: model proposes, PEP disposes) --+
|  client -> gateway -> orchestrator -> model / tools / index           |
|           input rails -> generate/stream -> output rails + DLP        |
|                                                                       |
|  +-- TOOL PROXIES (Zero-Trust MCP -- identity from JWT) -----------+ |
|  | MCP behind gateway PEP: retrieve_* | query_index | execute      | |
|  |                         create_ticket | record_decision         | |
|  |   ns / k / include_values from verified token -- NOT model JSON | |
|  |   OAuth 2.1 + PKCE, RFC 8707 audience, RFC 8693 NO passthrough  | |
|  |   hash-pin tool JSON + server digest. MCP is not the PDP        | |
|  +-----------------------------------------------------------------+ |
+------+------------------------+-------------------+-------------------+
       v                        v                   v
+-- PERSISTENCE --+  +-- SANDBOX / EGRESS --+  +-- HUMAN / APPEAL ----+
| checkpointer    |  | Firecracker/gVisor/  |  | HITL interrupt       |
| index alias+ACL |  | WASM/MXC sandbox     |  | reviewer RBAC!=user  |
| WORM decision   |  | dest allowlist       |  | DSA Art.20 appeals   |
| ingest watermark|  | creds OUTSIDE guest  |  | dual-control CSAM    |
+-----------------+  +----------------------+  +----------------------+
```

| Plane | Owns | LLM-Free? | Failure If Coupled |
|---|---|---|---|
| **Control** | IdP, PDP, bundle alias, hop cap, spend, HITL queue, MCP allowlist | Yes at admit; judges offline | Playground string in prod; model picks `tenant_id` |
| **Data** | Rails, retrieve/tools/generate, stream | No (untrusted stream) | Judge on user p99; reindex on query replica |
| **Persistence** | Checkpointer, index alias, WORM | Yes | InMemorySaver; mutable moderation "status" |
| **Tool Proxies** | MCP behind gateway; identity from JWT | Yes for authz | Omnibus `search(collection)`; host shell |
| **Telemetry** | Spans, $/tokens, WORM, sidecar eval | Yes | Logging PII/CSAM "for debug" |

### 45-Minute Interview Motion

| Min | Phase | Exit Criterion |
|---|---|---|
| **0-6** | Clarify + NFR questions that **fork architecture** | Corpus size, latency class, tenancy, regulated data, online vs batch, HITL, tools |
| **6-10** | Capacity envelope | QPS, concurrent sessions, ingest embeddings, judge off user p99 |
| **10-22** | High-level boxes | Generic topology + archetype deltas; guardrails + ZT MCP drawn now |
| **22-38** | Deep dive 1-2 components | The fork (ACL bitmap, sandbox, cascade, checkpointer) |
| **38-50** | Failure / 10x scale / wrap | Wrong-archetype trap, cost cliff, dual-running bundles |

### NFR Questions That Fork Architecture

Ask 5-8. Each answer drops or adds a box.

| Question | If They Say... | Architecture Fork |
|---|---|---|
| **Corpus size** | < ~200k tokens (~500 pages), static | **Drop ANN/RAG.** Stuff + prompt-cache (Anthropic skip-RAG) |
| | Multi-million chunks, multi-tenant | Hybrid retrieve + ACL **predicate** |
| **Latency class** | Ghost-text / IntelliSense | Completions: cancel, HTTP/2, colocate. NOT 8-turn RAG |
| | Support chat "a few seconds" | Streaming TTFT + retrieve timeout 200-500ms |
| | Batch / overnight | Flex/Batch tier; judge may run on job clock |
| **Tenancy** | Single enterprise | One index alias; still per-user ACL |
| | B2B SaaS, N tenants | Namespace-per-tenant (RU cliff) or RLS; never "filter in the prompt" |
| **Regulated data** | PHI / PCI / secrets | PII detect-redact-audit before embed; residency; WORM |
| **HITL** | Refund / shell / promote | PDP **then** pause. Model is not policy |
| | Read-only FAQ | Maybe no interrupt; still egress allowlist |
| **Tools** | None | Chat/search |
| | Shell / MCP / tickets | Sandbox + ZT MCP gateway. Never host shell |

### Request Flow Narrative (30 seconds while drawing)

1. **Admit.** Client presents IdP token. Gateway authenticates; does not trust body `user_id`.
2. **Control.** Resolve feature-flag bundle. Sticky `thread_id`. Judge off this path.
3. **Input rails.** Injection sensors cut likelihood; they do not authorize tools.
4. **Orchestrator.** One graph. Retrieve / tools / generate as the archetype requires.
5. **Tool / MCP.** Gateway PEP, hash-pin, RFC 8707 audience, no token passthrough. Namespace from JWT.
6. **Stream** tokens (SSE). Persist checkpoint if the product has a thread.
7. **Telemetry.** Spans without secrets; WORM of allow|deny|HITL.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 Four Archetypes

#### Archetype A: Chatbot / Assistant (Support, Internal Q&A)

| Pull | Drop |
|---|---|
| Stream, checkpointer, Adaptive-RAG (skip retrieve on chitchat), prefix cache, hop cap 1-2 | Uncapped ReAct, SC N=40, GraphRAG (unless "themes"), training loop |

**Adaptive-RAG**: router decides chitchat -> no retrieve; exact ID (`TS-999`) -> lexical; paraphrase FAQ -> hybrid. Retrieve timeout 200-500ms policy.

**Memory is a batch job.** OpenAI "Dreaming" (background profile curation, ~5x cheaper). The model is stateless; memory is retrieve-or-inject scaffolding. Do not write memory on every turn on the user clock.

**Escalation design**: A system that escalates a beat too early (with full context) reads as competent. A system that escalates a beat too late (after wrong answers) reads as broken regardless of model quality. Pass full transcript + sources to human agent. Triggers: out-of-scope, explicit human request, frustrated language, billing dispute, low confidence.

#### Archetype B: Search (Enterprise Retrieve + Answer)

| Pull | Drop |
|---|---|
| Hybrid BM25+dense in parallel, ACL bitmap pre-filter, rerank, cite | ANN if corpus < ~200k tokens; sequential BM25-then-dense; ACL in the prompt |

**Skip-ANN rule**: Anthropic -- knowledge base < 200,000 tokens (~500 pages) -> include whole KB in prompt; prompt cache >2x latency cut, up to 90% cost cut. Past that, contextual embeddings + BM25 (-49% top-20 miss) + rerank (-67%). Microsoft semantic index: **permission trimming** -- results only if the signed-in user already has RBAC access.

**Hybrid search**: Pure semantic fails on "cheap flights to Paris" when the database says "affordable airfare." Pure keyword misses synonyms. Hybrid gives both precision and understanding. Retrieve 50-150 candidates, rerank, send 5-20 passages to generation.

#### Archetype C: Copilot (IDE / Workspace Agent)

**Two products on one board -- name which:**

1. **Completions (ghost text):** Latency war with IntelliSense. GitHub Copilot: authenticating proxy, short-lived signed tokens, HTTP/2 multiplex + cancel typed-through (~half of issued requests), colocate proxy with Azure model. **>400M completions/day**, peak ~8,000 RPS, **mean < 200ms** (Cheney, InfoQ). That is **mean**, not p99, not an SLO.
2. **Agentic session:** Tools, sandbox, HITL. GitHub 2026-06-02: local sandbox (MXC) and cloud ephemeral Linux. Hooks: `preToolUse` can deny. `--allow-all` is a product switch, not a policy.

| Pull | Drop |
|---|---|
| H2 cancel, MXC/cloud sandbox, Rule of Two, PDP then HITL, interrupt map | Host shell, "model is policy", `permissions=` as MCP gateway, MemorySaver in prod |

**PDP vs HITL**: Draw PDP first, interrupt second. Anthropic coding-agent: users approve ~93% of prompts; sandboxing cut prompts 84%. Meta Agents Rule of Two: a session may have at most two of [A untrusted input, B sensitive data, C state-change/egress]; if all three, no autonomy.

**Fill-in-the-Middle (FIM)**: Model sees code before and after cursor. GitHub A/B tests found FIM lifted accepted completions by ~10%.

#### Archetype D: Content Moderation (UGC Cascade)

Not a chatbot with a toxicity prompt. It is a **cascade + audit log + appeal**.

| Pull | Drop |
|---|---|
| Hash-first (PhotoDNA/CSAI/PDQ), keyword sync, dual classifiers, async LLM queue, human review, WORM, DSA appeals | LLM on every item, Perspective as 2027 SoT, model as PDP for CSAM |

**Published cascade shapes:**
- Discord AutoMod keyword/regex: **>45M** messages blocked before post
- Reddit AutoMod: **58.9%** of 2021 moderator removals, **103.6M** pieces
- UniGuard-Cascade: 87% less LLM usage vs GPT-4-everywhere, 5.7x cost reduction

**CSAM**: Detection of known material is hash-matching (PhotoDNA, CSAI Match, PDQ) -- not an LLM. US ESPs report to NCMEC CyberTipline (18 U.S.C. 2258A). Never design "the model looks at the image and decides."

**Two-tier moderation architecture:**
```
Every item -> Hash match (known CSAM) -> Keyword/regex -> Fast classifier (<50ms)
                                                              |
                                              CLEAR (85%)  VIOLATION  UNCERTAIN (13%)
                                                |           (2%)         |
                                           Publish      Block+log    LLM Analysis (async)
                                                        +appeal         |
                                                                   CLEAR / VIOLATION / UNCERTAIN
                                                                              |
                                                                        Human Queue (<5%)
```

### 2.2 RAG Pipeline: The 2026 Default

| Layer | Function | Key Decision |
|---|---|---|
| **1. Ingestion** | Parse PDFs, HTML, slides, tables; normalize; deduplicate | Format-specific parsers vs multimodal |
| **2. Indexing** | BM25 (lexical) + dense embeddings (semantic) | Chunk size: 512-1024 tokens, 50-100 overlap |
| **3. Query Processing** | Classify intent, rewrite if ambiguous, decompose if multi-hop | Rewrite adds latency but improves retrieval |
| **4. Retrieval** | Hybrid search with RRF or weighted scoring | Hybrid outperforms pure semantic by 15-30% |
| **5. Reranking** | Cross-encoder on top-K results | Cohere Rerank, BGE-Reranker |
| **6. Generation + Eval** | LLM consumes context; eval scores groundedness | Guardrail blocks unsafe outputs |

### 2.3 Seven Cross-Cutting Patterns

| Pattern | What It Does | Used In |
|---|---|---|
| **RAG** | Ground LLM in external knowledge | Chatbot, search, copilot |
| **Tool-Augmented Generation** | LLM calls APIs, DBs, calculators | Copilot, agentic systems |
| **Human-in-the-Loop** | Route uncertain cases to humans, capture corrections | Moderation, doc processing, chatbot |
| **Streaming** | Token-by-token output, reduces perceived latency | Chat, copilot |
| **Multi-Model Routing** | Cheapest model that can handle each request | All high-volume systems |
| **Caching** | Semantic, KV, exact-match; bypass LLM on hit | All systems |
| **Eval & Feedback** | Offline eval, CI gate, production sampling, user signals | All systems |

### 2.4 Interview Checklist (One Page)

- [ ] Corpus < 200k? Skip ANN.
- [ ] Tenant isolation = index **predicate**, not prompt.
- [ ] Latency class: completion vs chat vs batch.
- [ ] Guardrails + ZT MCP drawn in HLD, not wrap.
- [ ] HITL != PDP. Model is never the PDP.
- [ ] Judge off user p99.
- [ ] Dual-run `index_gen` / prompt hash on changes.
- [ ] Capacity: QPS, concurrent sessions, embed ingest, eval tax.
- [ ] One cost cliff named (RU cliff or SC N=40).
- [ ] One wrong-archetype named.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Cost Per 1K Runs by Archetype

**Chatbot** -- 1k sessions, 8 turns, 2,000 in / 400 out per turn:

| Variant | $/1k Sessions |
|---|---|
| Luna, no retrieve | **$7.04** |
| Luna + RAG on 40% of turns | **$7.55** |
| Luna, prefix cached (1,500/2,000 @ 0.1x) | **$4.88** |
| Sonnet uncached | **$96.00** |
| Sonnet, prefix cached | **$63.60** |
| SC N=40 as chat default | **~$780/1k calls** -- NEVER |

**Search** -- 1k queries (stateless):

| Variant | $/1k |
|---|---|
| Terra generate + ANN index | **$9.87** |
| Luna generate + index | **$1.05** |
| Skip-ANN: 200k corpus stuffed, Luna cached | **$4.48** |
| Skip-ANN: 200k corpus, Sonnet cached | **$60.00** |

**Key insight**: Anthropic skip-RAG is simplicity + quality on a small static KB, not always cheaper. At high QPS, $60/1k Sonnet stuffed vs ~$10/1k Terra+RAG -- retrieval can win on cost even under 200k.

**Copilot** -- 1k sessions:

| Variant | $/1k |
|---|---|
| Agent session: 12 tool loops, Terra (3,000 in / 200 out each) | **$100.80** |
| Same, Luna | **$10.08** |
| 30 loops uncapped, Terra | **$252.00** |
| Completions: 500 in / 50 out, Luna | **$0.16** |

**Moderation** -- 1k UGC items (~100 tokens):

| Variant | $/1k |
|---|---|
| OpenAI omni-moderation | **$0** (rate-limited) |
| AWS Comprehend toxicity | **$1.50** |
| Azure Content Safety | **$0.38** |
| LLM-every, Luna | **$0.34** |
| LLM-every, Sonnet | **$4.65** |
| Cascade 2.5% LLM, Luna | **$0.0085** + classifier |

**Detailed chatbot cost optimization** (500K conversations/day):

| Approach | Monthly Cost |
|---|---|
| All GPT-4o | $585,000 |
| 80/20 routing (80% mini, 20% GPT-4o) | $136,500 |
| + 25% semantic cache | $102,375 |
| + 20% API-only (no LLM) | **$82,000** (86% savings) |

### 3.2 Latency Per Archetype

| Archetype | Clock | p50 | p95 | p99 | Mitigations |
|---|---|---|---|---|---|
| **Chatbot** | TTFT (stream) | 400 ms | 1,200 ms | 3,000 ms | Prefix cache; skip retrieve on chitchat |
| | E2E answer | 2,000 ms | 6,000 ms | 12,000 ms | Hop cap 1-2 |
| **Search** | Retrieve+rerank | 80-400 ms | 120 ms-1.5s | 250 ms-3s fail-closed | Hybrid in parallel; circuit-break index independently |
| | Answer e2e | 1,300 ms | 4,200 ms | 12,300 ms | Generate dominates |
| **Copilot completions** | Ghost-text RTT | mean < 200 ms (Cheney) | -- | -- | HTTP/2 cancel; colocate |
| | Policy if SLO demanded | 150 ms | 350 ms | 800 ms | Do NOT invent p99 80ms |
| **Copilot agent** | One tool-loop | 8s | 45s | 180s | Hop cap; sandbox warm pool |
| **Moderation** keyword/hash | | < 10 ms | 20 ms | 50 ms | In-process |
| **Moderation** classifier | | 20 ms | 40 ms | 100 ms | Async if > budget |
| **Moderation** LLM slow-path | | 800 ms | 2,500 ms | 8,000 ms | Queue, not inline |
| **Control (all)** | Flag SDK | 1 ms | 10 ms | 50 ms | Fail-open last bundle |

**Key caveat**: No vendor publishes product-level percentiles. Copilot mean < 200ms is Cheney's completion path, not p99, not an SLO. Do not invent GitHub Copilot p99 80ms.

### 3.3 Throughput & Capacity Planning

| Scale | Architecture Pattern |
|---|---|
| 100 QPS | Single vLLM instance, single vector DB replica, Redis cache |
| 1K QPS | Load-balanced vLLM cluster (4-8 GPUs), vector DB 3 replicas, semantic cache |
| 10K QPS | Multi-model routing (80% fast), aggressive caching (30%+ hit), queue autoscaling |
| 100K QPS | Regional deployment, tiered (cache -> keyword -> vector -> LLM), custom embeddings |

**Published throughput references**:
- ChatGPT: 900M WAU, ~2.5B prompts/day (~29k prompts/s average) -- capacity anecdote, not your B2B SLO
- GitHub Copilot: >400M completions/day, peak ~8,000 RPS. Budget issued QPS including ~50% typed-through cancels
- B2B chatbot envelope: 10k DAU, 3 sessions/user/day, 8 turns -> avg 2.8 QPS, peak 28 QPS
- Moderation: 1M comments/day = ~12 QPS classifier. 2.5% escalate = 125 reviewer-hours/day

### 3.4 Availability & RPO/RTO

| Component | Availability | RPO | RTO |
|---|---|---|---|
| LLM serving | 99.95% | N/A (stateless) | < 30s (multi-provider failover) |
| Vector store | 99.9% | < 1 min | < 15 min (rebuild from source) |
| Conversation state | 99.9% | 0 (Redis AOF) | < 5 min |
| Eval datasets | 99.99% | 0 (Git-backed) | < 5 min |
| Canary kill | -- | -- | seconds (flag 0%) |

**Per-archetype RPO/RTO**:

| Archetype | RPO | RTO | Trade-off |
|---|---|---|---|
| **Chatbot** | Last checkpoint | Postgres replicas | Checkpoint every turn vs $/write |
| **Search** | Last compacted slab | Alias flip minutes; rebuild hours | Dual-run index_gen during migrate |
| **Copilot completions** | None (stateless) | Hedge region; cancel | No thread to restore |
| **Copilot agent** | Last checkpoint + sandbox TTL | Resume HITL; expire-deny | Expire-approve is a confused deputy |
| **Moderation** | Last WORM append | Replay queue | Losing appeal log is a compliance incident |

**Availability caveats**: OpenAI Fast mode 99.9% is **throughput** (tok/s + uptime), not chatbot p99. Bedrock Reserved 99.5% is **capacity**, not a registry or product SLO.

---

## Part 4: Distributed Resilience & Security

### 4.1 Circuit Breaker: Independent per Component

**Retrieve independently of generate.** Index 5xx must not freeze the FM; FM 429 must not skip ACL retrieve. **Classifier independently of LLM queue** in moderation.

| Path | Fallback | Refuse If |
|---|---|---|
| Search / RAG chat | Last-good retrieve cache -> BM25-only -> `retrieval_degraded` | Policy forbids ungrounded |
| Bundle / flags | Last-good bundle (fail-open flags) | Serving `latest` |
| Sandbox pool empty | Never host exec | -- |
| Moderation classifier 5xx | Fail-closed on CSAM/hash; product choice on spam | LLM as CSAM control |
| Completions | Hedge region; cancel typed-through | Waiting for agent p99 |

### 4.2 Production Failure Modes

| # | Failure Mode | Detection | Mitigation |
|---|---|---|---|
| 1 | **Hallucination** | Groundedness scoring, citation verification | Hybrid retrieval, reranking, "I don't know" |
| 2 | **Context overflow** | Token counting before LLM call | Summarize history, limit chunks |
| 3 | **Retrieval failure** | Low scores, empty results, user regen | Hybrid search (BM25 catches what embeddings miss) |
| 4 | **Latency spikes** | p95/p99 monitoring, queue depth | Dynamic batching, model downgrade, caching |
| 5 | **Cost explosion** | Per-request tracking, daily anomaly | Token budgets, routing, kill switch |
| 6 | **Silent quality regression** | Production eval sampling, user feedback | SLO burn-rate alerts, A/B before rollout |
| 7 | **Adversarial attacks** | Injection classifiers, anomalous patterns | Sanitization, defense in depth |

### 4.3 Zero-Trust AI Architecture

Microsoft's Zero Trust for AI reference (RSAC 2026): IBM 2025 Cost of a Data Breach found **97% of organizations with AI-related breaches lacked proper AI access controls**.

**Five core principles:**
1. **Verify every request** (user identity + agent identity): Every API call carries both end-user and calling component identity.
2. **Least-privilege access**: LLM gateway cannot access raw database. Retriever cannot call external APIs.
3. **Encrypt data at rest and in transit**: mTLS between components. Embedding vectors are sensitive (invertible).
4. **Assume breach**: Anomaly detection on tool call patterns, unusual query volumes.
5. **Network microsegmentation**: Guardrails isolated from retrieval. Eval plane has read-only access.

### 4.4 Zero-Trust MCP Per Archetype

OAuth 2.1 + PKCE, RFC 8707 resource = this MCP server, RFC 8693 no passthrough, hash-pin tools + server digest, identity from verified JWT. MCP is not the PDP.

| Archetype | MCP Verbs Behind PEP | Deny |
|---|---|---|
| **Chatbot** | `retrieve_public_kb`, `retrieve_hr`, `create_ticket` (HITL) | Host `bash`; ns from tool args |
| **Search** | `query_index` (ns from JWT) | `include_values` for assistants (inversion surface) |
| **Copilot** | `execute` in sandbox only; `preToolUse` deny | Host `bash`; `permissions=` as gateway |
| **Moderation** | `enqueue_review`, `record_decision` | `set_alias` production; `fetch_values` of CSAM |

### 4.5 RBAC for AI Systems

| Role | Permissions |
|---|---|
| End User | Submit queries, view responses, provide feedback |
| Support Agent | View conversation history, escalation queue, override guardrails with justification |
| ML Engineer | Modify prompts, update RAG index, configure routing, view eval dashboards |
| Platform Admin | Manage infrastructure, configure rate limits, modify RBAC, deploy models |
| Auditor | Read-only access to all traces, conversations, guardrail decisions, cost reports |

### 4.6 PII and Data Security

- **Detect-redact-audit before embed**: Vec2Text recovers 92% exact on 32 tokens, 89% MIMIC names. Redacting in the prompt after embedding is too late.
- **EchoLeak** (CVE-2025-32711, CVSS 9.3): Zero-click XPIA through retrieved email. RAG is an untrusted-input leg (Rule of Two [A]).
- **Tenant isolation**: index predicate/namespace, not prompt. Microsoft Copilot: only surfaces results if user already has RBAC access.
- **Moderation**: reviewer ACL != user ACL. CSAM viewers: dual-control, no MCP `fetch_values`, minimized retention.

### 4.7 Regulatory Compliance

| Regulation | What It Forces |
|---|---|
| **HIPAA** | BAA, PII before embed, CMEK, no PHI in Anthropic schema |
| **GDPR** | Delete blob AND digest; residency; LangSmith goldens have indefinite retention |
| **EU DSA** | Art. 15 accuracy/error/safeguards; Art. 17 statements of reasons; Art. 20 appeals; 2026 template cycle |
| **18 U.S.C. 2258A** | Hash-first known CSAM + CyberTipline for US ESPs; NOT an LLM classifier |
| **SOC 2** | Tenant isolation = predicate; WORM of allow/deny |

---

## Part 5: Production Enterprise Code

```python
"""AI system design control flow: NFR fork (skip ANN <200k tokens),
sticky bundle, dual circuit breakers (retrieve vs generate), BM25 fallback,
PII detect-redact-audit before embed, MCP deny include_values/host_shell.
Run: python ai_system_design_runtime.py
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

SKIP_ANN_TOKENS = 200_000

# ---- Circuit Breaker (independent per component) -------------------------
class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

class CircuitOpenError(RuntimeError): pass

@dataclass
class CircuitBreaker:
    name: str; failure_threshold: int = 5; cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0; _opened_at: float = 0.0
    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else: raise CircuitOpenError(f"circuit_open:{self.name}")
    def record_success(self) -> None:
        self._failures, self._state = 0, CircuitState.CLOSED
    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# ---- PII Pipeline: detect -> redact -> audit BEFORE embed ----------------
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_detect_redact(text: str, *, audit: list[dict], cid: str,
                      tenant: str, sink: str) -> str:
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE))
             if rx.search(text)]
    pre_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    if "pan" in kinds and sink in {"embed_ingest", "mcp_args"}:
        audit.append({"cid": cid, "tenant": tenant, "sink": sink,
                      "action": "block", "kinds": kinds})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": cid, "tenant": tenant, "sink": sink,
                  "action": "redact" if redacted != text else "allow"})
    return redacted

# ---- MCP PEP: deny include_values, host_shell ----------------------------
MCP_ALLOW = frozenset({"retrieve_kb", "query_index", "create_ticket",
                        "record_decision"})
MCP_DENY = frozenset({"include_values", "host_shell", "bash",
                       "fetch_values", "set_alias", "promote_tag"})

def mcp_pep(verb: str, args: dict, tenant: str) -> dict:
    if verb in MCP_DENY or args.get("include_values") is True:
        raise PermissionError(f"mcp_deny:{verb}")
    if verb not in MCP_ALLOW:
        raise PermissionError(f"mcp_unknown:{verb}")
    return {"ok": True, "verb": verb, "ns": f"tenant-{tenant}"}

# ---- BM25 Fallback Index -------------------------------------------------
class BM25Index:
    def __init__(self):
        self.postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.doclen: dict[str, int] = {}
        self.df: dict[str, int] = defaultdict(int)
    def upsert(self, doc_id: str, text: str) -> None:
        toks = text.lower().split()
        self.doclen[doc_id] = max(1, len(toks))
        for tok in set(toks):
            self.postings[tok][doc_id] += 1
            self.df[tok] += 1
    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        toks = query.lower().split()
        scores: dict[str, float] = defaultdict(float)
        n = max(1, len(self.doclen))
        avgdl = sum(self.doclen.values()) / n
        for tok in toks:
            df = self.df.get(tok, 0)
            idf = max(0.0, (n - df + 0.5) / (df + 0.5))
            for doc_id, tf in self.postings.get(tok, {}).items():
                dl = self.doclen[doc_id]
                scores[doc_id] += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]

# ---- Design Runtime: NFR fork, dual breakers, sticky bundle --------------
@dataclass(frozen=True)
class Bundle:
    model_id: str; prompt_hash: str; schema_hash: str
    decoding_params: str; index_gen: str; eval_suite_id: str
    def digest(self) -> str:
        blob = "|".join([self.model_id, self.prompt_hash, self.schema_hash,
                         self.decoding_params, self.index_gen, self.eval_suite_id])
        return "b_" + hashlib.sha256(blob.encode()).hexdigest()[:16]

@dataclass
class DesignRuntime:
    corpus_tokens: int; champion: Bundle
    bm25: BM25Index = field(default_factory=BM25Index)
    retrieve_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("retrieve"))
    generate_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("generate"))
    sticky: dict[str, Bundle] = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)

    def resolve_bundle(self, thread_id: str) -> Bundle:
        """Sticky bundle on thread_id -- canary does not flip mid-conversation."""
        if thread_id not in self.sticky:
            self.sticky[thread_id] = self.champion
        return self.sticky[thread_id]

    def handle(self, tenant: str, thread_id: str, query: str, cid: str) -> dict:
        bundle = self.resolve_bundle(thread_id)
        clean = pii_detect_redact(query, audit=self.audit, cid=cid,
                                  tenant=tenant, sink="query")
        # NFR fork: skip ANN if corpus < 200k tokens
        skip_ann = self.corpus_tokens < SKIP_ANN_TOKENS
        if skip_ann:
            source = "stuffed"
            context_ids = [f"full_corpus:{tenant}"]
        else:
            try:
                self.retrieve_breaker.allow()
                # Production: Pinecone query(namespace=tenant, include_values=False)
                results = self.bm25.search(clean, k=5)
                self.retrieve_breaker.record_success()
                context_ids = [doc_id for doc_id, _ in results]
                source = "hybrid"
            except (CircuitOpenError, TimeoutError):
                self.retrieve_breaker.record_failure()
                results = self.bm25.search(clean, k=5)
                context_ids = [doc_id for doc_id, _ in results]
                source = "bm25_fallback"
        # Generate (independent breaker)
        try:
            self.generate_breaker.allow()
            answer = f"ok:{bundle.prompt_hash[:8]}:{hashlib.sha256('|'.join(context_ids).encode()).hexdigest()[:8]}"
            self.generate_breaker.record_success()
        except CircuitOpenError:
            answer = "retrieval_degraded_refusal"
            source = "generate_down"
        return {"answer": answer, "source": source, "skip_ann": skip_ann,
                "bundle": bundle.digest(), "thread_id": thread_id}

if __name__ == "__main__":
    champ = Bundle("luna", "p_aaa", "s_v1", "t0", "idx_1", "eval_v1")
    # Small corpus: skip ANN
    rt = DesignRuntime(corpus_tokens=50_000, champion=champ)
    rt.bm25.upsert("c1", "Reset MFA via the security desk")
    r = rt.handle("acme", "t1", "how to reset MFA?", "cid-1")
    assert r["skip_ann"] is True and r["source"] == "stuffed"
    # Same thread gets same bundle (sticky)
    r2 = rt.handle("acme", "t1", "again", "cid-2")
    assert r2["bundle"] == r["bundle"]
    # MCP deny
    try: mcp_pep("query_index", {"include_values": True}, "acme")
    except PermissionError: pass
    try: mcp_pep("host_shell", {"cmd": "ls"}, "acme")
    except PermissionError: pass
    print(f"ok: audit={len(rt.audit)} skip_ann={r['skip_ann']} source={r['source']}")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Multi-Tenant B2B Support Assistant (Chat + RAG)

**Problem:** SaaS help-center. 500 tenants. Each tenant has manuals + SKU tables + error codes. Users: customer (public KB only) and CS agent (public + account tools). NFRs: tenant isolation SOC2, p95 "a few seconds," exact-ID AND paraphrase. Peak ~28 QPS turns. Some tenants have <200k tok KB; a few have millions of chunks.

**Architecture:** Hybrid retrieve + namespace-per-tenant + stream + hop cap. Per tenant: if KB < ~200k and static, skip ANN and cache; if larger, hybrid + namespace-per-tenant. Route chitchat vs factoid. Refund/ticket = PDP + HITL.

**Trade-off matrix:**

| Decision | Option A: Stuff all KBs + Sonnet | Option B: Hybrid + ACL ns + Luna/Terra route | Option C: Per-tenant agent (uncapped) | Chosen |
|---|---|---|---|---|
| **Cost** | Small: OK; large: $60/1k stuffed Sonnet | Luna $7.04-$7.55/1k; avoids $263k/mo fat namespace | $100.80/1k Terra 12-loop | **B** |
| **Latency** | Prefill 200k dominates TTFT | e2e p50 2s / p95 6s / p99 12s | Agent p95 45s (wrong for L1) | **B** |
| **Security** | Cross-tenant if one prompt | Predicate + scoped MCP; EchoLeak mitigated | Refund without PDP = confused deputy | **B** |
| **Scalability** | QPS x 200k tokens = $ cliff | 50 QPS-class proven; peak 28 QPS | Ambient GTM shape, not support | **B** |

### Scenario 2: UGC Content Moderation Platform (1M Comments/Day)

**Problem:** Consumer community, 1M comments/day (~12 QPS) + image posts. Policies: spam, hate, sexual, CSAM (known-hash), appeals for EU users (DSA). Cannot put an LLM on every post. Perspective dead 2026-12-31.

**Architecture:** Hash-first for known CSAM + keyword/sync + dual independent classifiers + async LLM on ambiguous + human queue + WORM appeals.

**Trade-off matrix:**

| Decision | Option A: LLM-every (Sonnet) | Option B: Cascade + dual-model + hash | Option C: Keyword-only | Chosen |
|---|---|---|---|---|
| **Cost** | $4.65/1k -> $4,650/day at 1M | Omni $0 + 2.5% Luna $8.50/day | Cheap | **B** |
| **Latency** | LLM p99 8s on post path | Fast path p50 20 / p95 40 / p99 100ms; LLM queued | Inline microseconds | **B** |
| **Compliance** | Nothing accurate for DSA Art. 15(e) | Art. 15/17/20 templates; WORM appeals | No nuance for DSA | **B** |
| **Security** | Model as PDP; CSAM via LLM = wrong control | Hash + 2258A; dual-control reviewers | Regex bypass | **B** |

**Cost analysis**: LLM-every at 1M/day = $4,650/day. Cascade (2.5% LLM Luna) = ~$8.50/day + classifier cost. 85% classified at tier 1, 13% to LLM, 5% to human. Human review at 2.5% escalation = 125 reviewer-hours/day.

---

## Common Failure Modes

| They Built... | On a Problem That Needed... | What Breaks |
|---|---|---|
| Chatbot with no retrieval | Private/changing corpus | Confident wrong; EchoLeak if they "just add Drive" |
| Search with no ACL predicate | Multi-user corpus | Cross-tenant leak; "prompt said don't show HR" |
| Copilot with host shell | IDE agent | Lethal trifecta complete |
| Moderation LLM-only | UGC at QPS | p99 and $ explode; CSAM missed; DSA has nothing to report |
| Completions latency on agent board | Ghost-text | Quote 200ms mean on 12-tool-loop product |
| Skip-ANN stuffed Sonnet at 50 QPS | High-QPS lookup | $60/1k vs RAG ~$10/1k |
| SC N=40 on L1 chat | Support widget | ~$780/1k Sonnet calls |
| Judge on user path | Any interactive SLO | Extraction p99 12,000ms |
| Fat namespace + metadata ACL | 20M x 1536-d @ 50 QPS | $263k/mo vs $8.3k/mo |
| Safety as wrap-up boxes | Any archetype | Rails + ZT MCP were default; model treated as PDP |
| Typed-through completions without cancel | Ghost-text | Pay for ~50% unused decode |
| HITL expire-approve | Copilot agent | Confused deputy |
| Serving `latest` index | Search | Silent recall death |

---

## Interview Q&A

**Q1: What is this round?**
I compose all prior modules into a user-facing product. Control owns IdP, PDP, bundle alias, hop cap, spend, HITL queue, MCP allowlist. Data owns rails, retrieve/tools/generate, stream. Persistence is checkpointer, index alias, WORM. Tool proxies are MCP behind a gateway -- identity from JWT. Telemetry is ACL-redacted traces, $/tokens, judge sidecar. This is not Twitter HLD, not a training loop, not PagedAttention. Guardrails and Zero-Trust MCP are default boxes. The model is never the PDP.

**Q2: What do you ask in the first 6 minutes?**
NFR questions that fork boxes. Corpus size -- under ~200k static tokens I drop ANN. Latency class -- ghost-text vs "a few seconds" vs batch. Tenancy -- namespace-per-tenant or RLS, never ACL in the prompt. Regulated data -- PII before embed, residency, WORM. HITL -- refund/shell means PDP then pause. Tools -- sandbox + ZT MCP, never host shell. Then I size QPS with judge off p99.

**Q3: Walk a request through the diagram.**
Gateway binds tenant from JWT, not body `user_id`. Flag SDK resolves a sticky bundle. Input rails cut injection likelihood -- they do not authorize tools. Orchestrator runs one graph. Tools hit MCP PEP: hash-pin, RFC 8707, namespace from JWT. Stream. Checkpoint if there is a thread. Log hashes, not bodies. Judge is a sidecar, never on this path.

**Q4: Four archetypes -- what do you pull vs drop?**
Chatbot: stream, checkpointer, Adaptive-RAG; drop uncapped ReAct and SC N=40. Search: hybrid + ACL predicate before ANN; drop ANN under ~200k. Copilot: I name completions vs agent; pull H2 cancel, MXC sandbox, Rule of Two; drop host shell. Moderation: cascade hash -> keyword -> classifier -> async LLM -> human; drop LLM-every and Perspective.

**Q5: Give me $/1k for all four archetypes.**
Chat Luna 8-turn: $7.04 / +RAG $7.55 / cached $4.88 / Sonnet $96. Search Terra+index $9.87; skip-ANN Luna $4.48 vs Sonnet $60. Copilot 12-loop Terra $100.80 / Luna $10.08 / completions Luna $0.16. Moderation omni $0 / Comprehend $1.50 / LLM-every Sonnet $4.65 / cascade $0.0085. RU cliff $263k vs $8.3k/mo.

**Q6: What latency do you quote?**
Nobody publishes product percentiles. I contract policy: chatbot TTFT 400/1,200/3,000ms, e2e 2,000/6,000/12,000ms. Copilot completions: Cheney mean < 200ms only; if they demand SLO, policy 150/350/800ms -- I will not invent p99 80ms. Agent 8/45/180s. Moderation classifier 20/40/100ms; LLM queued. Fast mode 99.9% is throughput; Bedrock 99.5% is capacity.

**Q7: Circuit breaker and fallback?**
Independent breakers: retrieve vs generate, classifier vs LLM queue. Index 5xx must not freeze FM; FM 429 must not skip ACL retrieve. Fallback: last-good cache -> BM25-only -> retrieval_degraded refusal. Flags: last-good bundle. Sandbox pool empty: never host exec. Moderation: fail-closed on CSAM/hash; spam is a product choice I state.

**Q8: When is an agent overkill?**
For FAQ, support lookup, or simple extraction where prompt + cache + retrieval already solves the task. Agents are justified when the system must plan, use tools, recover, or act. A 12-tool-loop agent at $100.80/1k Terra for L1 support chat is the wrong archetype.

**Q9: RAG or long context?**
Corpus < ~200k tokens and static: stuff + prompt cache. Knowledge large, mutable, access-controlled, or must be cited: RAG. But skip-ANN is simplicity + quality, not always cheaper -- at high QPS, $60/1k Sonnet stuffed vs ~$10/1k Terra+RAG. Retrieval wins on cost at scale.

**Q10: Zero-Trust MCP?**
OAuth 2.1 + PKCE, RFC 8707 resource = this server, no token passthrough, hash-pin tools. Identity from JWT, never the model. Chatbot: retrieve_kb scoped by JWT namespace. Search: deny include_values. Copilot: execute in sandbox only. Moderation: enqueue_review, record_decision. Never promote_tag from an assistant.

---

## Key Numbers to Memorize

| Number | What |
|---|---|
| **< ~200k tokens (~500 pages)** | Skip ANN; Anthropic stuff + cache |
| **0.95^10 = 59.9%** | 10-step workflow at 95% per-step is not "reliable" |
| **>400M/day, ~8k RPS, mean < 200ms** | Cheney Copilot completions -- mean, not p99 |
| **~50% typed-through** | Cheney cancel; budget issued QPS |
| **>45M** | Discord AutoMod blocked before post |
| **2026-12-31** | Perspective sunset |
| **$7.04 / $96** | Chat Luna vs Sonnet per 1k sessions |
| **$9.87 / $60** | Search Terra+RAG vs skip-ANN Sonnet |
| **$100.80 / $0.16** | Copilot 12-loop Terra vs completions Luna per 1k |
| **$0 / $4.65 / $0.0085** | Omni / LLM-every Sonnet / cascade Luna per 1k |
| **$263k vs $8.3k/mo** | Fat namespace vs tenant namespaces (RU cliff) |
| **~$780/1k** | SC N=40 Sonnet calls -- never a chat default |
| **CVE-2025-32711 / CVSS 9.3** | EchoLeak -- RAG as untrusted input |
| **Rule of Two** | Meta 2025: at most two of [untrusted, sensitive, state-change] |

---

## Quick Reference

- **First 6 minutes**: Ask NFR questions that fork boxes. Corpus, latency, tenancy, regulated, HITL, tools.
- **Archetypes**: chatbot (stream + checkpoint + Adaptive-RAG), search (hybrid + ACL predicate), copilot (name completions vs agent), moderation (cascade + audit + appeal).
- **Skip ANN**: Corpus < ~200k tokens. But not always cheaper at high QPS -- retrieval can win on $.
- **Model is never the PDP**: Schema-valid JSON is not authorization. HITL is a pause, not policy.
- **Guardrails + ZT MCP**: Default HLD boxes, not wrap. Draw them in minutes 10-22.
- **Judge off p99**: Eval is a sidecar. Never on the user request path.
- **Circuit breakers**: Independent per component. Retrieve != generate. Classifier != LLM queue.
- **PII before embed**: Vec2Text 92% exact. Redacting in the prompt after embedding is too late.
- **Sticky thread_id**: Canary must not flip mid-conversation.
- **Cost traps**: SC N=40 as chat default. Fat namespace. LLM-every on UGC. Uncapped tool loops.
- **Latency traps**: Quoting Fast 99.9% or Bedrock 99.5% as product SLO. Inventing p99 80ms.
