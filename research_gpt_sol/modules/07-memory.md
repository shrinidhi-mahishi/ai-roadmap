# 07 - Agent Memory

**Scope:** Short-term, long-term, semantic, episodic, and memory retrieval.  
**Study goal:** Build memory as typed, provenance-bound application state with explicit formation, recall, correction, and deletion semantics.

Memory is not synonymous with chat history or a vector database. Short-term describes thread scope, not storage durability. Long-term describes survival across threads, not automatic prompt injection. A production memory system must preserve who said what, when it was valid, why it may be used, and whether it is fact, experience, or procedure.

## 1. System Topology & Data Flow

### Reference topology

```text
                                   CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ purpose/consent │ write/retention policy │ schema/versions  │
│ model/prompt/index registry │ quotas │ release/eval gates │ keys/residency  │
└───────────────────┬─────────────────────────────────────┬────────────────────┘
                    │ trusted invocation                  │ policy decisions
                    ▼                                     ▼
                                ONLINE DATA PLANE
┌──────────────┐ auth/thread ┌──────────────┐ typed state ┌──────────────────┐
│ Client/agent ├────────────►│ Session API  ├────────────►│ Thread event log │
└──────────────┘             │ + checkpoint│             │ + checkpoints    │
                             └──────┬───────┘             └──────────────────┘
                                    │ recall/propose
                    ┌───────────────▼────────────────┐
                    │ Memory policy/tool/MCP gateway │
                    │ namespace + purpose + authority│
                    └───────────────┬────────────────┘
                                    │
                     ┌──────────────┼───────────────┐
                     ▼              ▼               ▼
              ┌────────────┐ ┌────────────┐  ┌──────────────┐
              │ Semantic   │ │ Episodic   │  │ Procedural   │
              │ facts      │ │ outcomes   │  │ reviewed/read│
              └─────┬──────┘ └─────┬──────┘  └──────────────┘
                    └───────────────┼────────────────┐
                                    ▼                │
                          ┌──────────────────┐        │
                          │ Lexical/vector/ │        │
                          │ temporal indexes│        │
                          └────────┬─────────┘        │
                                   │ typed memories  │
                                   ▼                 │
                          ┌──────────────────┐        │
                          │ Context assembly│◄───────┘
                          │ authority labels│
                          └──────────────────┘

                              ASYNC FORMATION PLANE
┌──────────────┐ Kafka/outbox ┌──────────────┐ validate ┌────────────────────┐
│ Events/tool  ├─────────────►│ Extractor +  ├─────────►│ Canonical memory DB│
│ receipts     │              │ classifier   │          │ + deletion ledger │
└──────────────┘              └──────┬───────┘          └─────────┬──────────┘
                                     │ DLQ/repair                  │ projections
                                     ▼                             ▼
                              ┌──────────────┐              ┌──────────────┐
                              │ Review queue │              │ Search/graph │
                              └──────────────┘              └──────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ TELEMETRY: OTel │ write/recall quality │ cost/freshness │ tamper-evident audit│
└──────────────────────────────────────────────────────────────────────────────┘
```

The enterprise system of record remains authoritative for balances, entitlements, clinical facts, orders, policies, and permissions. Memory may help locate or personalize, but it cannot replace a fresh authoritative lookup or grant an action.

### End-to-end request and formation flow

1. The gateway authenticates actor, tenant, subject, thread, device context, and purpose. It creates a correlation ID and pins memory schema, write policy, retrieval policy, embedding/reranker, prompt, and model versions.
2. The session service appends a user/tool/approval event and updates typed task state by compare-and-swap. Prompt-facing messages do not own authoritative workflow fields.
3. A recall router decides whether cross-thread memory is useful. The memory gateway derives namespaces and allowed kinds from server identity; model text and client namespace strings cannot expand scope.
4. Canonical active facts and episodes are filtered by tenant, subject/team, purpose, kind, validity, consent, sensitivity, and origin authority. Lexical/vector candidates are fused, reranked, diversified, and capped by tokens.
5. Context assembly emits typed records with ID, kind, source, validity, and authority labels. Developer procedure, authenticated preference, verified tool outcome, another agent's conclusion, and web-derived speculation remain visibly different.
6. After the turn, deterministic rules or an extractor propose memory candidates. Policy checks necessity, consent, sensitivity, origin, source entailment, duplicates, conflicts, and quota before commit.
7. The canonical item and outbox event commit transactionally. Projection workers update lexical/vector/graph indexes at least once using deterministic IDs. Read-your-writes comes from canonical storage until the projection high-water mark catches up.
8. Correction supersedes an old fact with validity lineage. Expiry removes it from recall. Legal/product deletion follows a ledger across canonical rows, indexes, summaries, caches, shared copies, traces, and backup policy.
9. OTel captures stage latency and counts; immutable audit captures IDs, versions, policy decisions, before/after state, selected memories, downstream correlation, correction, and deletion without logging raw content by default.

## 2. Core Mechanics & Algorithms

### 2.1 Taxonomy and ownership

| Type | Scope | Representation | Update semantics | Authority |
|---|---|---|---|---|
| Working/short-term | invocation or thread | event sequence, messages, typed variables, checkpoint | append events; CAS task state; compact with source range | task-owned, not cross-thread truth |
| Semantic long-term | user/team/project/application | atomic fact or structured profile | validate, deduplicate, supersede, expire, delete | follows original source |
| Episodic long-term | prior session/task | trajectory, outcome, feedback, artifact references | append-oriented; corrections annotate history | verified observations outrank reflections |
| Procedural long-term | application/team | instruction, checklist, skill, exemplar | reviewed versioned release and rollback | developer/approved publisher only |

Short-term state can persist for months and still be short-term because its scope is one thread. Long-term memory normally remains outside the prompt until retrieval selects it. Trace/audit storage is not automatically agent-readable memory.

### 2.2 Short-term memory and compaction

A thread is an ordered event log plus a typed state projection:

```text
event: (thread_id, sequence, type, actor, payload_ref, timestamp, digest)
state: (thread_id, version, intent, plan, artifacts, budgets, approvals)
summary: (event_start, event_end, source_root, model/prompt version, status)
```

Appending an event is `O(1)` amortized; reconstructing from the beginning is `O(n)` in event count, reduced to `O(events since checkpoint)` by periodic snapshots. A snapshot improves read latency but increases storage and migration work. Delta checkpoints reduce repeated large-channel writes at the cost of replay work.

Compaction reduces prompt tokens, not historical authority. Every summary carries its covered event range and source hash/Merkle root. Late correction or deletion invalidates every summary covering the changed event. Periodically reconstruct from source events rather than recursively summarizing summaries; otherwise distortion compounds. Approval, amount, authenticated account, and tool receipt remain typed fields, never facts re-parsed from prose.

### 2.3 Semantic memory

Two shapes suit different workloads:

- A **profile document** is cheap to inspect and inject but creates whole-record overwrite and concurrent-merge risk. Use JSON Patch/field CAS, not last-write-wins.
- An **atomic fact collection** stores `(subject, predicate, value, validity, provenance, authority, status)` per item. It supports temporal correction and targeted retrieval but requires deduplication and contradiction handling.

Use deterministic identity from `(tenant, subject, source_event, extractor_version, kind, predicate)` so at-least-once formation becomes upsert. A new trusted value does not erase history: close the prior fact's `valid_to`, link `superseded_by`, and activate the new fact. Conflicting trusted sources create an explicit conflict state; semantic similarity is not a conflict-resolution policy.

Canonical semantic facts converge when all events are processed because item identity is deterministic, supersession is monotonic, and one fenced/CAS writer orders changes for a predicate where order matters. Arbitrary last-write-wins does not provide this invariant.

### 2.4 Episodic memory and reflection

An episode records goal, observations, actions, result, feedback, time, environment/policy version, and artifact/trace references. The raw verified outcome is immutable evidence. A model-generated lesson or reflection is a separate low-authority hypothesis linked to the episode; it never overwrites what occurred and requires review before promotion to procedure.

Episode retrieval should match task conditions and outcome, not only topic. A previously successful deployment may be harmful when runtime, region, schema, or policy differs. Diversity prevents five near-identical failed attempts from dominating context. Episodic writes are append-oriented and naturally idempotent by execution/run ID.

Consolidation, compaction, supersession, expiry, and deletion are distinct:

- **compact** prompt representation while retaining event links;
- **consolidate** equivalent items while retaining all provenance;
- **supersede** end a fact's validity without rewriting history;
- **expire** remove an item from normal recall under retention/use policy;
- **delete** fulfill erasure across all governed representations.

A heuristic forgetting score can control retrieval or storage tiering. It cannot prove legal deletion.

### 2.5 Memory retrieval

Recall makes four decisions: whether memory is needed, which scope/types are authorized, how candidates rank, and how much enters context. Hard filters precede scoring:

```text
tenant/subject/team + purpose + kind + status=ACTIVE + valid_at(now)
+ consent + sensitivity + origin-authority requirement + projection generation
```

Candidate methods include exact predicate lookup, metadata/time filtering, BM25, dense ANN, hybrid RRF, graph traversal, and episode-summary search. For `m` total branch hits and `u` unique items, RRF accumulation is `O(m)` and sorting is `O(u log u)`. Exact dense scan is `O(Nd)` for `N` vectors of dimension `d`; ANN trades exactness for latency and must be audited against exact samples.

After hard filters, a type-aware rank can be:

```text
score(m,q) = w_r·relevance(m,q)
           + w_t(kind)·time_utility(m, now)
           + w_a·origin_authority(m)
           + w_o·outcome_similarity(m, task)
           + w_i·declared_importance(m)
           - w_d·redundancy(m, selected)
```

`time_utility` may use `exp(-lambda_kind·age)`, but a stable preference and an incident episode need different decay. Recent is not necessarily true; important is not necessarily supported. Select greedily under a token budget with per-source/type caps. Retrieval returns typed evidence, not a hidden prompt fragment.

### 2.6 State machines and invariants

```text
PROPOSED ─► VALIDATED ─► COMMITTED ─► INDEXED ─► ACTIVE
    │             │                                     │
    └─REJECTED────┘                         ┌───────────┼───────────┐
                                           ▼           ▼           ▼
                                      SUPERSEDED    EXPIRED    TOMBSTONED
```

**Required invariants**

- Every memory has tenant, subject/scope, kind, source event, immutable origin authority, sensitivity, validity, status, schema version, and deterministic identity.
- No memory transformation upgrades origin authority; five agents repeating one source are not five independent sources.
- Retrieval filters authorization, purpose, validity, and status before content reaches ranking, a model, or cache.
- A semantic correction creates one current active fact per governed predicate or an explicit conflict; an episode remains historical.
- A session/checkpoint ID is a security-sensitive cursor derived and authorized by the server.
- Memory cannot change system policy, approvals, credentials, or tool RBAC. Consequential actions reauthenticate and query systems of record.
- Formation and recall terminate under bounded candidates, tokens, retries, time, spend, and consolidation depth.
- Tombstoned items cannot reappear from a stale replica, summary, cache, graph edge, shared agent, or restored backup.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

```text
C_memory = extraction + validation/consolidation + embedding/indexing
         + canonical/projection storage + retrieval/rerank
         + injected prompt tokens + reconciliation/deletion + human correction

C_1000 = Σ(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + memory infrastructure + allocated maintenance

cost_per_memory_helped_success = total lifecycle memory cost /
                                 verified tasks improved by memory
```

**Illustrative point-in-time assumptions, 2026-08-21:** 1,000 memory-enabled runs consume 4M uncached input, 5M cached stable-prefix reads, 40,000 cache-write tokens, and 1.5M output. Canonical storage, retrieval, embedding, and allocated maintenance are assumed at `$1.80/1K runs`; extraction retries and human correction are excluded. Rates use the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No prompt cache / 1K | Cached model cost / 1K | Total with $1.80 memory |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(9M×$5)+(1.5M×$30)` = **$90.00** | `$20+$2.50+$0.25+$45` = **$67.75** | **$69.55** |
| `gpt-5.6-terra` | `(9M×$2)+(1.5M×$12)` = **$36.00** | `$8+$1+$0.10+$18` = **$27.10** | **$28.90** |
| `gpt-5.6-luna` | `(9M×$0.20)+(1.5M×$1.20)` = **$3.60** | `$0.80+$0.10+$0.01+$1.80` = **$2.71** | **$4.51** |

Cache only a stable instruction/procedural prefix permitted by the provider contract. User memories, subject identity, validity, and current authorization belong outside shared cache prefixes. Result-cache keys include tenant, subject-policy digest, memory generation/high-water mark, query, retrieval policy, and prompt version; correction or deletion invalidates them.

Always-in-context versus retrieval is measurable. An 800-token profile repeated across four calls for 1,000 runs adds `3.2M` input tokens, costing `$6.40` at uncached `terra` input rates or `$0.64` only if the exact prefix qualifies for cached pricing. If 40% of runs retrieve 500 tokens once, injected memory is `0.2M` tokens, or `$0.40` uncached, plus retrieval infrastructure and miss risk. Keep a tiny read-only core only when recall failure costs more than repeated tokens.

If 25% of sessions run one background extraction with 1,000 input and 150 output tokens, `terra` extraction costs `250×[(1,000×$2 + 150×$12)/1M] = $0.95/1K sessions` before embeddings/storage. A compactor processing 2,500 input and producing 400 output tokens for 10% of sessions adds `100×[(2,500×$2 + 400×$12)/1M] = $0.98/1K`. Report these maintenance paths and failed work, not only reader tokens.

### 3.2 Latency SLOs

```text
T_turn = T_session_read + T_need_memory + T_candidate_search + T_rerank
       + T_context_assembly + T_model/tools + T_session_commit
       + T_sync_write(optional)
```

These are internal design targets, not public vendor benchmarks:

| Operation | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Thread load/checkpoint | ≤ 15 ms | ≤ 50 ms | ≤ 120 ms | partition by tenant/thread, delta state, primary read after write |
| Semantic fact recall | ≤ 40 ms | ≤ 150 ms | ≤ 400 ms | exact/profile fast path, bounded ANN/RRF, cached metadata |
| Episodic hybrid recall | ≤ 60 ms | ≤ 250 ms | ≤ 700 ms | summary index, type/time filters, candidate cap |
| Complete memory augmentation | ≤ 120 ms | ≤ 500 ms | ≤ 1.2 s | parallel branches, deadline rerank, graceful no-memory mode |
| Synchronous validated write | ≤ 30 ms | ≤ 120 ms | ≤ 300 ms | canonical commit first; async projection |
| Background time-to-searchable | ≤ 5 s | ≤ 30 s | ≤ 120 s | backlog autoscale, priority corrections, canonical read-through |

Measure model/tool latency separately so recall regressions remain visible. Also measure compaction, projection lag, correction, TTL, and end-to-end deletion. Track memories considered/retrieved/injected, injected tokens, no-memory rate, index generation, stale-use, and degradation. Batching embeddings increases throughput but adds visibility lag; use deadline/priority-aware micro-batches.

### 3.3 Throughput and back-pressure

At 1,000 turns/s, suppose each turn appends four events, 35% request recall, each recall reranks 20 candidates, 15% propose a durable item, and 5% enqueue compaction:

```text
event appends/s       = 1,000×4 = 4,000
memory recalls/s      = 1,000×0.35 = 350
rerank items/s        = 350×20 = 7,000
formation proposals/s = 1,000×0.15 = 150
compaction jobs/s     = 1,000×0.05 = 50
```

At 6 KiB/event, raw append ingress is about `23.4 MiB/s` before indexes and replication. Size hot-subject partitions and deletion bursts, not only averages. A single celebrity/team namespace can dominate vector candidates and CAS conflicts.

Use weighted admission by history bytes, candidate count, injected tokens, extraction/compaction model calls, sensitivity, and deletion fan-out. Separate online session, recall, canonical write, projection, compaction, correction/delete, and telemetry queues. Reserve capacity for correction and deletion. Back-pressure toward admission with per-tenant quotas, bounded buffers, deadline propagation, and `202 + status` for background formation.

Shed optional reflection, graph linking, compaction, and low-value extraction before session commit, correction, deletion, or current-authority lookup. If recall is unavailable, clearly run without long-term personalization; never substitute another namespace, a stale cache, or model invention.

### 3.4 NFR scorecard

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% memory augmentation; 99.99% session/correction/delete APIs | No-memory degradation reduces personalization but preserves isolation. |
| RPO | 0 canonical events, facts, episodes, corrections, tombstones, audit; ≤ 5 min aggregate metrics | More durable writes increase latency/storage. |
| RTO | ≤ 15 min session/canonical lookup; ≤ 4 h projection rebuild | Rebuildable projections reduce backup complexity but require tested manifests. |
| Freshness | read-your-write canonical; projection p99 ≤ 120 s; correction/delete hidden from recall ≤ 60 s | Faster indexing costs compute; canonical read-through adds complexity. |
| Quality | write precision/recall, Recall@k, temporal accuracy, stale/contradiction rate, task-success lift | Aggressive writing improves recall but worsens noise/privacy. |
| Privacy | purpose/consent, category minimization, view/correct/export/delete, retention proof | Stateless or opt-in designs may reduce personalization. |
| Security | zero cross-tenant recall, authority upgrade, or memory-authorized effects in adversarial suite | Strong isolation costs indexes/keys and operations. |
| Compliance | residency, encryption, DPA, lawful basis, deletion and backup policy | Embeddings/search metadata remain threat-modelled cleartext. |

No stable audited benchmark compares short-term, semantic, episodic, graph, and learned memory under one write policy, model, authorization scheme, hardware, deletion behavior, and p50/p95/p99/cost model. Reproduce current production code paths with no-memory and full-context baselines.

## 4. Distributed Resilience & Security

### 4.1 Durable event log and rebuildable projections

```text
┌──────────────┐ append/CAS ┌──────────────┐ outbox    ┌──────────────┐
│ Session/tool ├───────────►│ Canonical DB ├─────────►│ Kafka        │
│ source event │            │ event/memory │          │ partitions   │
└──────────────┘            └──────┬───────┘          └──────┬───────┘
                                   │ audit/tombstone           │ at least once
                                   ▼                           ▼
                            ┌──────────────┐            ┌──────────────┐
                            │ WORM/deletion│            │ Temporal     │
                            │ ledger       │            │ projections  │
                            └──────────────┘            └───┬──────┬───┘
                                                            │      │
                                                            ▼      ▼
                                                       lexical/  vector/graph/
                                                       profile   summaries/cache
```

The durable unit is an observed event or explicit authenticated memory command. Canonical facts/episodes commit with an outbox record; Kafka/Temporal drives extraction, embedding, consolidation, compaction, and deletion. Deterministic IDs make at-least-once delivery idempotent. Each worker stores input/output digests, stage, attempts, and high-water mark; poison events enter a DLQ with repair/quarantine state.

Use append-only events, item-level upsert, and version/ETag CAS for mutable profiles. Do not hold a distributed lock during an LLM call: read version `v`, extract a structured proposal, then commit only at `v` or rebase with a bounded retry. A fenced lease permits only one projection publisher/consolidator for a subject generation. One layer owns retries.

Projection visibility is explicit: `COMMITTED` may be read canonically; `INDEXED` carries projection generation/high-water mark. Migrations dual-build a new embedding/schema/graph generation, shadow-read an eval and authorization set, atomically switch the namespace alias, retain rollback briefly, then delete under policy. Never mix incompatible embedding dimensions.

### 4.2 Failure taxonomy and recovery

| Failure | Class | Recovery/degradation |
|---|---|---|
| session store unavailable | transient/critical | fail stateful mutation; explicit stateless/read-only mode only when safe |
| semantic index timeout | transient | exact/profile/keyword lookup with degraded flag |
| extractor/model outage | transient | queue event; secondary extractor; deterministic no-write |
| malformed/poison source | permanent/adversarial | reject/quarantine by digest; DLQ; do not repeatedly extract |
| lost profile update | concurrency | CAS structured patch, rebase, conflict record |
| projection lag | consistency | canonical read-through and visible freshness status |
| corrupt summary | derived-data | invalidate and rebuild from covered source events |
| stale replica/cache | consistency/security | version-aware primary read; reject old generation |
| partial deletion | privacy incident | block subject recall; continue reconciliation until proven complete |
| poison reflection | quality/security | preserve low origin, quarantine, require promotion review |

Breakers are keyed by session store, canonical DB, embedding/index, reranker, extractor/model, graph, and telemetry sink. Use exponential backoff with full jitter, aggregate deadlines, and capped attempts. Canonical identity, authorization, correction, and tombstone failures fail closed. Optional semantic ranking degrades to exact/keyword; optional reflection stops; model extraction degrades to no durable write.

Backups include canonical events/facts/episodes, namespace and policy metadata, tombstones, schemas, projection manifests, WORM audit, and key-recovery procedure. Restore drills prove point-in-time recovery without namespace collision or resurrection of deleted/superseded memory. Reconciliation compares counts/digests across canonical, lexical, vector, graph, summaries, caches, and shared-agent copies.

### 4.3 Zero-Trust memory MCP and origin-bound authority

```text
┌──────────────┐ recall/write ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ Agent/model  ├─────────────►│ Memory MCP     ├───────────►│ Canonical +  │
│ untrusted I/O│              │ policy gateway │            │ search stores│
└──────┬───────┘              └───────┬────────┘            └──────┬───────┘
       │ proposal only                │ trusted identity            │ filtered rows
       ▼                              ▼                             ▼
┌──────────────┐              ┌──────────────┐              ┌──────────────┐
│ Validation/  │              │ Consent/RBAC │              │ Typed memory │
│ quarantine   │              │ purpose log  │              │ + authority  │
└──────────────┘              └──────────────┘              └──────────────┘
```

The MCP server and caller authenticate mutually. The gateway derives tenant, subject, allowed kinds, purpose, and sensitivity from server context; it allowlists operations and rate limits writes. Tool-level RBAC distinguishes `recall`, `propose`, `correct-own`, `review-shared`, `publish-procedure`, `export`, and `delete`. A subagent receives minimum delegated namespaces and cannot inherit supervisor/user memory implicitly.

Origin authority is immutable: developer policy, verified enterprise record, authenticated user statement, verified tool result, peer-agent output, model inference, and external content remain separate. Summarization, consensus, or a trusted tool echo cannot upgrade origin. Retrieved memory is delimited evidence and cannot alter system instructions, stop limits, approvals, credentials, or gateway policy.

Before a consequential action, reauthenticate, query the current system of record, run deterministic policy, show the exact effect, obtain approval when required, execute with an idempotency key, and audit. A remembered bank change, refund preference, deployment command, or medical statement is never authorization.

### 4.4 Privacy, PII, and audit

The PII path is `classify purpose -> detect category -> minimize/deny/redact/tokenize -> validate consent -> store/index -> authorize recall -> rehydrate only for allowed display -> audit/delete`. Apply it to messages, tool outputs, facts, episodes, embeddings, graph properties, summaries, caches, traces, and eval sets. Do not infer and persist protected attributes merely because a model can.

Use tenant/subject-derived namespaces, private networking, least-privilege service identities, TLS, managed keys, and field encryption. Recognize that searchable metadata and vectors may remain visible to the retrieval layer. High-assurance tenants use separate indexes/encryption scopes. Admin/support export or impersonation requires approval and immutable audit.

Users need controls to view, correct, export, disable, and delete cross-session memory. Service history, personalization, safety audit, and training datasets have separate purpose and retention. A deletion ledger proves canonical removal/tombstone, projection and summary rebuild, cache/shared-copy invalidation, trace/eval handling, and backup expiry or cryptographic deletion.

Audit records include actor/service, tenant/subject, operation/purpose, source event, immutable origin, before/after version, model/prompt/extractor/index versions, candidate ranks/selected IDs, consent/policy decision, downstream correlation, expiry/correction/deletion, and incident lineage. Store hashes/IDs by default, hash-chain/sign WORM batches, and separate audit readers from memory operators.

Procedural memory is code/configuration: version control, review, signed release, read-only runtime mount, separation of duties, evaluation, and rollback. Shared semantic memory requires writer ACLs, provenance, promotion review, quotas, and rollback; low-trust workers cannot publish supervisor instructions.

## 5. Production Enterprise Code

This Python 3.11 standard-library program demonstrates a canonical memory service with deterministic IDs, immutable origin, validated semantic versus episodic writes, CAS supersession, tenant/subject/purpose filtering before rank, temporal validity, token-capped recall, structured correlation logs, full-jitter retry, closed/open/half-open breakers, primary -> secondary -> deterministic no-write extraction, and semantic-index -> keyword -> empty recall degradation. Replace in-memory adapters with transactional database/search implementations while preserving the contracts. Run with `python memory_service.py`.

```python
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """A retryable dependency failure."""


class PermanentError(RuntimeError):
    """A policy, validation, or concurrency failure."""


class CircuitOpen(TransientError):
    """A dependency is temporarily disabled."""


class Kind(Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"


class Origin(Enum):
    USER = "authenticated_user"
    TOOL = "verified_tool"
    AGENT = "agent_inference"
    EXTERNAL = "external_untrusted"


class Status(Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    subject_id: str
    purpose: str
    allowed_sensitivity: frozenset[str]


@dataclass(frozen=True)
class SourceEvent:
    event_id: str
    tenant_id: str
    subject_id: str
    origin: Origin
    text: str
    occurred_at: float
    memory_consent: bool


@dataclass(frozen=True)
class Candidate:
    kind: Kind
    predicate: str
    value: str
    sensitivity: str


@dataclass(frozen=True)
class Memory:
    memory_id: str
    tenant_id: str
    subject_id: str
    kind: Kind
    predicate: str
    value: str
    source_event_id: str
    origin: Origin
    sensitivity: str
    valid_from: float
    valid_to: float | None
    status: Status
    version: int


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for field in ("trace_id", "tenant_id", "subject_id", "stage", "attempt"):
            if hasattr(record, field):
                value[field] = getattr(record, field)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("memory")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 5.0):
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = "closed"
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe:
                    raise CircuitOpen("half-open probe busy")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._probe = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


class Extractor(Protocol):
    name: str

    def extract(self, event: SourceEvent, timeout_s: float) -> str: ...


class ExtractionChain:
    def __init__(self, extractors: Sequence[Extractor]):
        if len(extractors) < 2:
            raise ValueError("primary and secondary extractors required")
        self._extractors = tuple(extractors)
        self._breakers = {item.name: Breaker() for item in extractors}

    @staticmethod
    def parse(raw: str) -> Candidate:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("extractor returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {
            "kind", "predicate", "value", "sensitivity"
        }:
            raise PermanentError("candidate violates exact schema")
        try:
            kind = Kind(value["kind"])
        except (ValueError, TypeError) as exc:
            raise PermanentError("unsupported memory kind") from exc
        fields = (value["predicate"], value["value"], value["sensitivity"])
        if any(not isinstance(item, str) or not item.strip() for item in fields):
            raise PermanentError("candidate fields must be non-empty strings")
        return Candidate(kind, *(item.strip() for item in fields))

    def extract(self, event: SourceEvent, deadline: float,
                trace_id: str) -> Candidate | None:
        for extractor in self._extractors:
            breaker = self._breakers[extractor.name]
            for attempt in range(1, 3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    breaker.before()
                    raw = extractor.extract(event, min(remaining, 3.0))
                    candidate = self.parse(raw)
                    breaker.success()
                    return candidate
                except CircuitOpen:
                    break
                except PermanentError:
                    breaker.failure()
                    break
                except (TimeoutError, ConnectionError, TransientError):
                    breaker.failure()
                    logger.warning("extractor failure", extra={
                        "trace_id": trace_id, "tenant_id": event.tenant_id,
                        "subject_id": event.subject_id, "stage": extractor.name,
                        "attempt": attempt})
                    if attempt == 2:
                        break
                    delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
                    if time.monotonic() + delay >= deadline:
                        return None
                    time.sleep(delay)
        return None


class CanonicalStore:
    def __init__(self):
        self._items: dict[str, Memory] = {}
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    @staticmethod
    def identity(event: SourceEvent, candidate: Candidate) -> str:
        raw = "|".join((event.tenant_id, event.subject_id, event.event_id,
                        "extractor-v1", candidate.kind.value, candidate.predicate))
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def commit(self, event: SourceEvent, candidate: Candidate) -> Memory:
        memory_id = self.identity(event, candidate)
        with self._lock:
            existing = self._items.get(memory_id)
            if existing is not None:
                return existing
            if candidate.kind is Kind.SEMANTIC and event.origin not in {
                Origin.USER, Origin.TOOL
            }:
                raise PermanentError("low-authority source cannot create fact")
            if candidate.kind is Kind.SEMANTIC and not event.memory_consent:
                raise PermanentError("semantic memory lacks consent")
            if candidate.sensitivity not in {"normal", "restricted"}:
                raise PermanentError("sensitivity policy denied write")
            item = Memory(memory_id, event.tenant_id, event.subject_id,
                          candidate.kind, candidate.predicate, candidate.value,
                          event.event_id, event.origin, candidate.sensitivity,
                          event.occurred_at, None, Status.ACTIVE, 1)
            self._items[memory_id] = item
            self._events.append({"type": "MEMORY_COMMITTED",
                                 "memory_id": memory_id,
                                 "source_event_id": event.event_id,
                                 "version": 1})
            return item

    def supersede(self, memory_id: str, expected_version: int,
                  at: float) -> Memory:
        with self._lock:
            item = self._items[memory_id]
            if item.version != expected_version or item.status is not Status.ACTIVE:
                raise PermanentError("memory CAS conflict")
            updated = replace(item, valid_to=at, status=Status.SUPERSEDED,
                              version=item.version + 1)
            self._items[memory_id] = updated
            self._events.append({"type": "MEMORY_SUPERSEDED",
                                 "memory_id": memory_id,
                                 "version": updated.version})
            return updated

    def active(self, principal: Principal, now: float) -> tuple[Memory, ...]:
        with self._lock:
            return tuple(item for item in self._items.values()
                         if item.tenant_id == principal.tenant_id
                         and item.subject_id == principal.subject_id
                         and item.sensitivity in principal.allowed_sensitivity
                         and item.status is Status.ACTIVE
                         and item.valid_from <= now
                         and (item.valid_to is None or now < item.valid_to))

    @property
    def audit(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(self._events)


class SemanticIndex:
    def __init__(self, available: bool = True):
        self._available = available

    def scores(self, query: str, items: Sequence[Memory]) -> dict[str, float]:
        if not self._available:
            raise TimeoutError("semantic index unavailable")
        q = {token[:5] for token in re.findall(r"[a-z0-9]+", query.lower())}
        result = {}
        for item in items:
            d = {token[:5] for token in re.findall(
                r"[a-z0-9]+", f"{item.predicate} {item.value}".lower())}
            result[item.memory_id] = len(q & d) / max(1, len(q | d))
        return result


class MemoryService:
    def __init__(self, store: CanonicalStore, index: SemanticIndex,
                 extraction: ExtractionChain):
        self._store = store
        self._index = index
        self._extraction = extraction
        self._index_breaker = Breaker()

    def remember(self, event: SourceEvent, timeout_s: float = 1.0) -> Memory | None:
        trace_id = str(uuid.uuid4())
        candidate = self._extraction.extract(
            event, time.monotonic() + timeout_s, trace_id
        )
        if candidate is None:
            logger.info("memory deterministic no-write", extra={
                "trace_id": trace_id, "tenant_id": event.tenant_id,
                "subject_id": event.subject_id, "stage": "no_write"})
            return None
        if candidate.value.lower() not in event.text.lower():
            raise PermanentError("candidate is not entailed by source text")
        item = self._store.commit(event, candidate)
        logger.info("memory committed", extra={
            "trace_id": trace_id, "tenant_id": event.tenant_id,
            "subject_id": event.subject_id, "stage": "committed"})
        return item

    def recall(self, principal: Principal, query: str, limit: int = 4,
               token_budget: int = 120) -> tuple[tuple[Memory, ...], bool]:
        if principal.purpose not in {"support.personalize", "support.resolve"}:
            raise PermanentError("purpose cannot access memory")
        now = time.time()
        allowed_kinds = ({Kind.SEMANTIC} if principal.purpose == "support.personalize"
                         else {Kind.SEMANTIC, Kind.EPISODIC})
        items = tuple(item for item in self._store.active(principal, now)
                      if item.kind in allowed_kinds)
        degraded = False
        try:
            self._index_breaker.before()
            semantic = self._index.scores(query, items)
            self._index_breaker.success()
        except CircuitOpen:
            semantic = {item.memory_id: 0.0 for item in items}
            degraded = True
        except (TimeoutError, TransientError):
            self._index_breaker.failure()
            semantic = {item.memory_id: 0.0 for item in items}
            degraded = True

        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        ranked = []
        for item in items:
            words = set(re.findall(
                r"[a-z0-9]+", f"{item.predicate} {item.value}".lower()))
            lexical = len(terms & words) / max(1, len(terms))
            authority = {Origin.USER: 1.0, Origin.TOOL: 0.9,
                         Origin.AGENT: 0.3, Origin.EXTERNAL: 0.1}[item.origin]
            ranked.append((0.6 * max(lexical, semantic[item.memory_id])
                           + 0.4 * authority, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))

        selected: list[Memory] = []
        used = 0
        for score, item in ranked:
            estimate = len(item.value.split()) * 2
            if score <= 0.4 or used + estimate > token_budget:
                continue
            selected.append(item)
            used += estimate
            if len(selected) == limit:
                break
        return tuple(selected), degraded


class DemoExtractor:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def extract(self, event: SourceEvent, timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TimeoutError("extractor unavailable")
        return json.dumps({"kind": "semantic",
                           "predicate": "communication_style",
                           "value": "concise technical explanations",
                           "sensitivity": "normal"})


def main() -> None:
    store = CanonicalStore()
    chain = ExtractionChain((DemoExtractor("primary", False),
                             DemoExtractor("secondary", True)))
    service = MemoryService(store, SemanticIndex(available=False), chain)
    now = datetime.now(timezone.utc).timestamp()
    event = SourceEvent("evt-918", "tenant-a", "user-42", Origin.USER,
                        "I prefer concise technical explanations", now, True)
    saved = service.remember(event)
    recalled, degraded = service.recall(
        Principal("tenant-a", "user-42", "support.personalize",
                  frozenset({"normal"})),
        "How should explanations be written?"
    )
    print(json.dumps({"saved": saved.memory_id if saved else None,
                      "recalled": [item.memory_id for item in recalled],
                      "degraded_keyword_only": degraded,
                      "audit": store.audit},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The demo fails the primary extractor, succeeds with the secondary, validates that the fact value occurs in an authenticated user source, commits it idempotently, and recalls it through the keyword fallback when semantic search is unavailable. If both extractors fail, `remember` returns `None`: deterministic no-write is safer than inventing persistent identity. A wrong tenant/subject produces no candidates, and an unapproved purpose fails before content ranking.

## 6. Architectural System Design Scenarios

### Scenario 1 - Multi-channel customer support memory

**Problem statement.** Design memory for 25 million customers and 5,000 support agents at 2,000 turns/s. It must continue active cases across channels, remember consented preferences, retrieve verified prior-case outcomes, propagate correction/deletion from recall within 60 seconds, maintain p95 memory augmentation under 500 ms and p99 under 1.2 seconds, and never use memory to authorize a refund or account change.

**Proposed architecture.** Store active ticket events and typed case state in a partitioned PostgreSQL/session service. Keep atomic semantic facts only for consented communication preferences and customer-provided stable context. Store resolved cases as episodes with issue, verified action, outcome, agent feedback, and ticket/receipt references. Live CRM/order/payment APIs remain authoritative. Kafka/Temporal drives background extraction, episodic summarization, projections, and deletion. Hybrid filtered recall reranks by problem/outcome, time validity, and origin; the prompt receives typed items and exact sources. Customers can view, correct, disable, export, and delete personalization memory.

```text
┌──────────────┐ auth/channel ┌──────────────┐ append/CAS ┌──────────────┐
│ Customer +   ├────────────►│ Support API  ├───────────►│ Thread/case  │
│ support agent│◄─answer─────┤ purpose/RBAC │            │ event/state  │
└──────────────┘             └──────┬───────┘            └──────────────┘
                                    │ recall/propose
                                    ▼
                             ┌──────────────┐ filtered  ┌──────────────┐
                             │ Memory      ├──────────►│ Semantic +   │
                             │ gateway     │           │ episodic DB  │
                             └──────┬───────┘           └──────┬───────┘
                                    │                          │ Kafka/Temporal
                                    ▼                          ▼
                             ┌──────────────┐           ┌──────────────┐
                             │ Live CRM/   │           │ Search/index │
                             │ order/refund│           │ delete ledger│
                             └──────────────┘           └──────────────┘
```

At 2,000 turns/s, four event appends/turn require 8,000 appends/s. If 40% recall and rerank 20 candidates, serve 800 recalls/s and 16,000 candidate scores/s. Partition by tenant/customer hash, isolate correction/delete queues, and cap hot-account concurrency. A semantic-index outage uses exact/keyword facts and labels incomplete prior-case recall; CRM or authorization outage blocks effects.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/privacy | Scalability ceiling |
|---|---|---|---|---|---|
| Full transcript on every turn | Highest repeated tokens | Tail grows with history | Low initially | Broad exposure; deletion copies hard | Poor for long histories |
| **Typed semantic + episodic retrieval** | Medium storage/index/model | Bounded recall and context | High lifecycle governance | Strong provenance, consent, correction | High with partitioned stores |
| Stateless support | Lowest memory cost | Lowest memory overhead | Low | Best minimization; weak continuity | High but more human rework |

**Decision rationale.** Typed retrieval earns its complexity because continuity and prior resolution outcomes are explicit requirements, while full-history prompting scales poorly and obscures authority. Stateless remains the risk baseline for sensitive categories. Separating episodes from semantic preferences prevents a prior refund from becoming a standing entitlement.

### Scenario 2 - Multi-agent coding and operations memory

**Problem statement.** Design memory for 20,000 repositories and 10,000 concurrent coding/operations runs. Agents should reuse verified repository conventions and incident lessons, reduce repeated diagnosis, isolate project/user/global scope, keep recall p95 under 250 ms, preserve RPO 0 for approvals/tool receipts, and prevent repository prompt injection or a low-trust worker from changing global procedures.

**Proposed architecture.** Keep each run's plan, shell results, approval, and budget in durable thread state. Store repository semantic facts only when verified against current files/CI configuration, and record build/deploy trajectories as episodes with command, environment, exit status, logs, rollback, and policy version. Workers use invocation-local scratch; a supervisor validates and consolidates proposals. Reviewed procedures live in a signed Git/config release mounted read-only. Current repository and CI/policy data override recall. A zero-trust memory MCP enforces project namespaces, origin labels, and read/propose versus procedure-publish RBAC.

```text
┌──────────────┐ task/scope ┌──────────────┐ delegate  ┌──────────────┐
│ Developer/CI ├───────────►│ Supervisor   ├──────────►│ Worker agents│
│ approval     │◄─status────┤ thread state │◄─findings─┤ local scratch│
└──────────────┘            └──────┬───────┘           └──────────────┘
                                    │ validate/recall
                                    ▼
                             ┌──────────────┐           ┌──────────────┐
                             │ Memory MCP  ├──────────►│ Project facts│
                             │ origin/RBAC │           │ + episodes   │
                             └──────┬───────┘           └──────┬───────┘
                                    │                          │ reviewed promotion
                                    ▼                          ▼
                             ┌──────────────┐           ┌──────────────┐
                             │ Repo/CI/live │           │ Signed, read-│
                             │ policy truth │           │ only procedure│
                             └──────────────┘           └──────────────┘
```

At 10,000 concurrent runs averaging one recall every 20 seconds, baseline demand is 500 recalls/s before bursts. If 30% of completed runs propose an episode, formation is controlled by the completion rate and separate quotas, not worker count. Disable durable writes for throwaway reviewers or route proposals through the supervisor. A memory/search outage continues with current repository facts; a CI/policy outage blocks deploy actions.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| Repository Markdown memory writable by agents | Low | Fast file read | Low | Weak: repository injection can persist/escalate | High storage, low trust ceiling |
| **Verified project facts + episodes + reviewed procedures** | Medium-high | Bounded indexed recall | High validation/promotion | Strong scope and authority separation | High with namespace sharding |
| Central shared vector memory for all projects | Medium | Fast broad search | Medium | Poor isolation/lineage; poison blast radius | Search scales, governance does not |

**Decision rationale.** The recommended split matches each datum's authority: current repository/CI is truth, tool receipts are verified episodes, extracted conventions are project facts, and procedures are reviewed releases. It contains compromised worker/repository content and preserves reusable outcomes without treating self-reflection as policy.

## Interview Review

1. **Is persistent chat history long-term memory?** Conceptually it remains short-term when scoped to one thread; durability and scope are different axes.
2. **Semantic versus episodic?** Semantic memory stores current facts; episodic memory preserves situated events, actions, outcomes, and feedback.
3. **What does origin-bound authority prevent?** Summarization, repetition, or agent consensus cannot upgrade untrusted content into policy or verified fact.
4. **How should memory be retrieved?** Hard-filter tenant, purpose, kind, validity, consent, and authority; then hybrid-rank, diversify, and cap tokens.
5. **Can memory authorize tools?** No. Reauthenticate, query the current system of record, run policy, obtain approval, and execute idempotently.
6. **How is forgetting different from deletion?** Forgetting/expiry changes recall; deletion reconciles every governed copy and proves completion.

## Primary References

- [LangChain memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenAI Agents SDK sessions](https://openai.github.io/openai-agents-python/sessions/)
- [Google ADK sessions](https://adk.dev/sessions/)
- [Google ADK memory](https://adk.dev/sessions/memory/)
- [MemGPT](https://arxiv.org/abs/2310.08560)
- [Generative Agents](https://arxiv.org/abs/2304.03442)
- [Reflexion](https://arxiv.org/abs/2303.11366)
- [LongMemEval](https://arxiv.org/abs/2410.10813)
- [LoCoMo](https://arxiv.org/abs/2402.17753)
- [MemoryAgentBench](https://arxiv.org/abs/2507.05257)
- [Mem0](https://arxiv.org/abs/2504.19413)
- [CoALA](https://arxiv.org/abs/2309.02427)
- [OWASP memory and context poisoning](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/)
- [Sleeper memory poisoning](https://arxiv.org/abs/2605.15338)
- [Origin-bound authority analysis](https://arxiv.org/abs/2606.24322)
- [NIST AI RMF](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
