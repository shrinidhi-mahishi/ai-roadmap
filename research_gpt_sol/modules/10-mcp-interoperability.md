# 10 - MCP and Interoperability

**Scope:** Tools, resources, MCP servers, MCP clients, hosts, prompts, transports, lifecycle, and adjacent protocols.
**Study goal:** Treat MCP as a versioned integration protocol, not a workflow runtime or trust boundary.

This module targets the generally available MCP revision `2026-07-28` as of 2026-08-21. It labels legacy lifecycle behavior explicitly because dual-era clients and servers remain operationally relevant. Protocol compliance standardizes framing and capability exchange; it does not make a server, tool description, resource, result, or model-selected action trustworthy.

## 1. System Topology & Data Flow

### Reference topology

```text
                                  CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ server registry/digests │ revision policy │ tool/resource ACL│
│ OAuth/PKCE/audience │ approval/data-flow rules │ budgets │ conformance/evals │
└────────────────────┬────────────────────────────────────┬────────────────────┘
                     │ trusted host policy                │ server policy
                     ▼                                    ▼
                           HOST / AI APPLICATION DATA PLANE
┌──────────────┐ request    ┌──────────────┐  model loop ┌───────────────────┐
│ User/UI      ├───────────►│ MCP host     ├────────────►│ Model/provider    │
│ approval     │◄─result────┤ context/policy│◄───────────┤ tool proposal     │
└──────────────┘            └──────┬───────┘             └───────────────────┘
                                   │ one protocol client per server
                     ┌─────────────┼──────────────────────┐
                     ▼             ▼                      ▼
              ┌────────────┐ ┌────────────┐       ┌──────────────┐
              │ MCP client │ │ MCP client │       │ Catalog/policy│
              │ local      │ │ remote     │       │ gateway client│
              └─────┬──────┘ └─────┬──────┘       └──────┬───────┘
                    │ stdio         │ Streamable HTTP      │ HTTP
                    ▼               ▼                      ▼
              ┌────────────┐ ┌────────────┐       ┌──────────────┐
              │ MCP server │ │ MCP server │       │ MCP gateway  │
              │ sandboxed  │ │ stateless  │       │ many servers │
              └─────┬──────┘ └─────┬──────┘       └──────┬───────┘
                    └───────────────┼──────────────────────┘
                                    │ domain adapters
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TOOL/RESOURCE PLANE: APIs │ files │ search │ databases │ workflow/queues   │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE: domain DB/effect ledger │ explicit handles │ catalog/cache     │
│ OAuth/token store │ subscriptions/event bus │ artifacts │ immutable audit   │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TELEMETRY: OTel/trace context │ protocol/domain errors │ cost/SLO │ SIEM    │
└──────────────────────────────────────────────────────────────────────────────┘
```

The **host** owns the model loop, consent UI, policy, context, and configured servers. Each **client** speaks MCP to one server. A **server** exposes capabilities around existing domain logic. The server does not own the host's reasoning loop, and MCP does not provide durable agent-to-agent task delegation.

### Modern end-to-end request flow

1. The host authenticates principal, tenant, purpose, and workload. It selects an allowlisted canonical server endpoint/artifact digest and establishes stdio process isolation or TLS/OAuth for HTTP.
2. A modern client may call the server-required `server/discover` method to learn revisions, self-reported identity, capabilities, instructions, and cache hints. Security decisions use configured endpoint, authenticated identity, registry policy, and digest, never self-reported names.
3. Each functional request carries the slash-qualified keys `params._meta["io.modelcontextprotocol/protocolVersion"]` and `params._meta["io.modelcontextprotocol/clientCapabilities"]`; optional `io.modelcontextprotocol/clientInfo` is descriptive, not authenticated identity. Modern HTTP also carries matching `MCP-Protocol-Version`, `Mcp-Method`, method-specific `Mcp-Name`, and any selected `x-mcp-header` argument values as `Mcp-Param-*` headers.
4. The host lists and paginates only authorized tools/resources/prompts, assigns stable server namespaces to colliding names, honors `ttlMs`/`cacheScope`, and gives the model the smallest task-relevant schema subset.
5. The model may propose a tool call; the trusted host validates selection, data-flow policy, risk, and approval. Resources remain application-selected context. Prompts are user-selected templates.
6. The server validates JSON-RPC, revision/capabilities, header/body agreement, schema depth/size, tenant/purpose, concrete arguments, scopes, and business invariants before handler execution.
7. A read returns bounded structured content/resources. A write records durable intent and a domain idempotency key, executes, reconciles ambiguous outcomes, and returns the backend operation/receipt. Tool-domain failure uses a normal result with `isError: true`; protocol failure uses JSON-RPC error.
8. A supported call may return `resultType: input_required`. The host gathers approved elicitation/model/root input and retries the original method under a new JSON-RPC ID with exact opaque `requestState` and bounded `inputResponses`.
9. Clients wanting changes open `subscriptions/listen` with filters. On reconnect they re-subscribe and reread authoritative lists/resources because modern HTTP subscriptions are not `Last-Event-ID` resumable.
10. OTel records endpoint/digest, era/revision/extensions, principal, catalog digest, request/method/name, policy/approval, validated input hash, result/error class, MRTR/cache/retry/cancel, backend ID, tokens, and timing.

## 2. Core Mechanics & Algorithms

### 2.1 Current versus legacy lifecycle

| Concern | Modern `2026-07-28` | Legacy `2024-10-07` through `2025-11-25` |
|---|---|---|
| Negotiation | optional `server/discover`; functional request may be first | `initialize` then `notifications/initialized` |
| Request metadata | revision and client capabilities on every request | negotiated primarily during connection setup |
| Protocol session | none; no `Mcp-Session-Id` | HTTP sessions may be allocated; connection state matters |
| Server-initiated request | removed from modern core | sampling/roots/elicitation over open channel |
| Additional input | MRTR `input_required`, retry original request with `requestState` | server request/response patterns |
| Change events | explicit filtered `subscriptions/listen` | legacy notifications/connection behavior |
| HTTP scale | any self-describing request can hit compatible replica | session affinity/state may be required |

Modern per-request statelessness does not remove application state. Cross-call workflows use explicit server-minted handles and durable stores. A dual-era client may probe `server/discover`, then fall back to legacy initialization; a pinned modern client fails instead of downgrading. Record every fallback because it can silently remove MRTR, modern cache/header routing, subscriptions, and sessionless assumptions.

The current core allows client requests/notifications, server responses, and request-scoped/subscribed notifications; servers no longer initiate standalone JSON-RPC requests. Every successful modern result has `resultType`, normally `complete` or supported `input_required`.

The request `_meta` entries are flat, slash-qualified keys, not a nested `io.modelcontextprotocol` object. Discovery returns `supportedVersions` (not a singular `protocolVersion`), required `ttlMs` and `cacheScope`, capabilities, and self-reported identity at `_meta["io.modelcontextprotocol/serverInfo"]`. Servers should include that identity key on every successful result; clients must never treat it as authenticated identity.

### 2.2 Tools, resources, and prompts

**Tools** are model-selectable operations discovered through paginated `tools/list` and invoked by `tools/call`. Definitions contain name, description, JSON Schema 2020-12 input schema, optional output schema, icons, and annotations. Input schemas keep an object root; output/`structuredContent` may be any JSON value. Annotations such as read-only, destructive, idempotent, and open-world are hints, not enforcement.

**Resources** are host/application-selected context addressed by URIs through `resources/list`, `resources/templates/list`, and `resources/read`. Templates parameterize URIs. The server controls URI mapping; custom schemes are not network instructions. Canonicalize `file://` paths, constrain `https://` direct retrieval, bound binary/text size, and attach MIME/provenance/freshness.

**Prompts** are user-controlled templates discovered with `prompts/list` and fetched through `prompts/get`. They can improve portability but remain server-supplied semantic content; the host decides whether and where they enter context.

Use a resource when the host should select/filter/display passive data. Use a tool for computation, parameterized search, side effects, or audited action. A search operation is usually a tool; a selected result can be a resource. Never disguise mutation as a resource read.

For `n` catalog entries with average schema size `s`, list bytes and potential model context are `O(ns)` before pagination/filtering. JSON parsing and validation are at least `O(message bytes)`; bound schema nesting/reference expansion and output bytes. A paginated client should enforce page and total-item limits and detect repeated cursors.

### 2.3 Servers, clients, hosts, and naming

A server is a narrow adapter around domain services. It owns handler validation, tenant authorization, business rules, bounded outputs, durable effects, and audit. It should not duplicate host planning. A remote replica must not keep required MRTR or workflow state only in process memory.

A host commonly maintains one client per configured server, translates schemas to its model provider, filters capabilities, namespaces collisions, caches by authorization context, and mediates approval. Tool names are unique only within a server. Stable host-assigned names such as `billing.invoice.get` prevent two self-named `search` tools from colliding.

Hosted model-platform MCP connectors can reduce client-loop code but move transport, catalog fetching, approval, retention, and error visibility into the platform contract. Wire compliance and product security/operations are separate evaluations.

### 2.4 JSON-RPC and error planes

MCP uses UTF-8 JSON-RPC 2.0 requests, responses, errors, and notifications. Request IDs correlate responses; they are not business idempotency keys. Notifications have no JSON-RPC response and must not be used when the caller requires acknowledgment.

- **Protocol error:** parse, invalid request/params, unknown method/tool, unsupported revision, or HTTP header mismatch. Return JSON-RPC error, with HTTP 400/`-32020 HeaderMismatch` for modern header/body disagreement.
- **Tool-domain error:** the handler ran but the operation failed. Return a successful protocol response whose result has `isError: true` and machine-readable error class/details.
- **Unknown effect:** transport timed out after a possible commit. Do not classify it as a normal failure; query status by idempotency/backend operation ID before retry.

Validate response ID, result schema, `resultType`, and output limits. Preserve original HTTP/JSON-RPC/domain codes in telemetry rather than exposing only a model-friendly string.

### 2.5 MRTR, subscriptions, caching, and transports

MRTR is bounded continuation, not server authority. Treat `requestState` as attacker-controlled unless integrity-protected; bind principal, originating request, arguments digest, expiry, and one-time redemption when required. Cap rounds, repeated-state fingerprints, input bytes, elapsed time, and cost. A user can abort.

Subscriptions are invalidation hints, not a durable database log. A dropped Streamable HTTP subscription can miss events, so reconnect, re-list/re-read, and compare catalog/resource versions.

Modern list/read/discovery results carry TTL and public/private cache scope. Partition private entries by principal/tenant/endpoint/revision/parameters. Stable ordering helps catalog and prompt-cache reuse. TTL permits staleness; it does not guarantee the host cached. A change notification invalidates but never replaces authoritative reread.

| Transport | Mechanics | Best fit | Required controls |
|---|---|---|---|
| stdio | newline-delimited JSON-RPC on stdin/stdout; logs stderr | local IDE/desktop adapter | pinned package/digest, process group, sandbox, mounts/env/network caps, stdout purity |
| Streamable HTTP | one POST/message, JSON or request SSE; separate subscription SSE | shared/horizontally scaled services | TLS/OAuth; verify version/method/name and selected `Mcp-Param-*` header/body mirrors; rate limits; proxy streaming/timeouts |
| Custom | preserves JSON-RPC and per-request metadata | constrained established bus | explicit interoperability/security/conformance burden |

Legacy HTTP+SSE is deprecated. Roots, protocol sampling, and protocol logging are deprecated; use explicit tool/resource configuration, direct model-provider integration, and OTel/stderr. Modern HTTP does not support SSE resume through `Last-Event-ID`.

### 2.6 Protocol invariants

- Every modern functional request carries exact supported revision and declared client capabilities; modern HTTP header values agree with JSON body method/name/version.
- Security identity comes from OS/process configuration or verified OAuth/workload identity, never `clientInfo` or `serverInfo`.
- Authorized catalog entries are deterministically namespaced and cached only within their declared authorization scope.
- Tool/resource input and output pass bounded schema, size, URI/path, and policy validation before model or backend exposure.
- Tool annotations, descriptions, prompts, resources, errors, and results are untrusted semantic data.
- JSON-RPC ID correlates one exchange; domain idempotency and status reconciliation own exactly-once effects.
- Modern replicas require no transport session affinity; required cross-call state is explicit and durable.
- MRTR preserves opaque state exactly, remains principal/request/expiry-bound, and terminates under hard round/time/cost limits.
- Subscription reconnect always refreshes authoritative state.
- A2A owns remote-agent task lifecycle; MCP equips a host/agent with bounded capabilities. A direct API may be simpler when portability/discovery is unnecessary.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

MCP has no protocol fee and defines no model price:

```text
C_1000 = Σ(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + catalog/auth/gateway/server/backend + MRTR/retry + human review

cost_per_verified_success = total host + MCP + backend lifecycle cost /
                            verified user outcomes
```

**Illustrative point-in-time assumptions, 2026-08-21:** 1,000 MCP-enabled model runs consume 6M uncached input tokens, 14M cached stable tool-schema/instruction prefix reads, 50,000 cache-write tokens, and 2M output tokens. Gateway, OAuth, server compute, backend APIs, state, and traces cost `$6/1K`; human approval is excluded. Rates use the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No prompt cache / 1K | Cached model cost / 1K | Total with $6 integration |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(20M×$5)+(2M×$30)` = **$160.00** | `$30+$7+$0.31+$60` = **$97.31** | **$103.31** |
| `gpt-5.6-terra` | `(20M×$2)+(2M×$12)` = **$64.00** | `$12+$2.80+$0.13+$24` = **$38.93** | **$44.93** |
| `gpt-5.6-luna` | `(20M×$0.20)+(2M×$1.20)` = **$6.40** | `$1.20+$0.28+$0.01+$2.40` = **$3.89** | **$9.89** |

Cache only exact stable catalog/schema/instruction prefixes under the provider contract. Principal, tenant, scopes, private resources, approval, tool results, and current backend state remain request-specific. MCP cache keys include canonical endpoint/artifact digest, negotiated era/revision/extensions, authorization partition, method/params, and catalog/resource version.

Catalog tax is material. Fifty tools at 300 transformed model tokens each add 15,000 input tokens/run, or `$30/1K runs` at uncached `terra` input rates. Filtering to eight relevant tools adds 2,400 tokens/run, or `$4.80/1K`; the `$25.20/1K` saving also improves tool selection. Measure tokens after host/provider transformation, not tool count.

One extra MRTR model round with 2,000 input and 300 output tokens costs `$7.60/1K affected terra runs`; if 10% of runs need it, portfolio increment is `$0.76/1K`, before human time. An unbounded MRTR loop or verbose resource can dominate transport cost.

### 3.2 Latency SLOs

```text
T_run = T_auth/discovery/list + Σcritical_path(T_model + T_call/read + T_MRTR)
      + T_queue/retry + T_approval
```

These are internal design targets, not public MCP ecosystem benchmarks:

| Operation | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Cached discover/list | ≤ 10 ms | ≤ 50 ms | ≤ 150 ms | scoped cache, stable ordering, background refresh |
| Authorized resource read | ≤ 50 ms | ≤ 200 ms | ≤ 500 ms | bounded excerpt, nearby replica, version cache |
| Read-only tool | ≤ 100 ms | ≤ 400 ms | ≤ 1 s | backend deadline, breaker, bounded output |
| Write tool, machine time | ≤ 250 ms | ≤ 1 s | ≤ 3 s | idempotency/status, async task for long work |
| One MRTR machine round | ≤ 1 s | ≤ 5 s | ≤ 15 s | max rounds, stable state, prompt/schema cache |
| Subscription first change | ≤ 250 ms | ≤ 2 s | ≤ 5 s | no proxy buffering, keepalive, event-bus capacity |

Report human approval wait separately. Measure discovery/list/call/read, time to first progress and final result, OAuth challenge, cache, MRTR rounds, backend, retry, cancellation, subscription reconnect, and verified end-to-end outcome. Track negotiated era because silent legacy fallback changes the path.

### 3.3 Throughput and back-pressure

At 500 functional calls/s with 80% reads, 20% writes, 5% catalog refresh, average 8-KiB response, and 10% MRTR:

```text
reads/s              = 500×0.80 = 400
writes/s             = 500×0.20 = 100
catalog list/s       = 500×0.05 = 25
response ingress/s   = 500×8 KiB ≈ 3.9 MiB/s
extra MRTR requests/s= 500×0.10 = 50
```

Size OAuth/token introspection, gateway, per-server/tool/backend pools, JSON/schema validation CPU, response bytes, SSE connections, and trace ingestion independently. Modern HTTP scales without session affinity, but subscriptions and explicit long-work handles still need shared event/domain state.

Use tenant/tool weighted admission by backend cost, result-size limit, MRTR rounds, stream count, and risk. Bulkhead read/write, high-cost tools, servers, tenants, subscriptions, and telemetry. Reserve status/cancel/idempotency reconciliation capacity. Bound catalogs/pages, schemas, resources/results, concurrent calls/SSE, queues, and retry attempts.

Back-pressure toward host admission: reduce catalog subset, queue long work with start/status/cancel, reject overload with structured retry metadata, or degrade to read-only/status. Never let each host/client/gateway/server/backend retry independently. Slow consumers get bounded buffers and must reconnect/reread after loss.

### 3.4 NFR scorecard

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% tool/read plane; 99.99% auth/status/cancel/idempotency ledger | More gateway/domain redundancy costs money. |
| RPO | 0 effects/idempotency/approval/audit; ≤ 5 min catalog/aggregate telemetry | Catalog/resources can reread; committed effects cannot be guessed. |
| RTO | ≤ 15 min auth/gateway/status; ≤ 60 min catalog/analytics | Explicit state and version registry are required. |
| Interoperability | pinned/dual-era matrix, conformance pass, schema/method compatibility | Dual era doubles lifecycle/state/security tests. |
| Reliability | duplicate effects, protocol/domain errors, MRTR loops, subscription gaps | Strong idempotency and refresh add domain work. |
| Security | zero cross-tenant/tool-scope/audience escape in adversarial suite | Least privilege and host filtering limit convenience. |
| Compliance | server/vendor inventory, data flow, residency, retention, approvals, deletion | Hosted connectors and remote servers expand subprocessors. |
| Operability | catalog/version rollback, kill switch, DLQ/unknown-effect repair, conformance/load tests | More controls increase deployment complexity. |

There is no normalized public benchmark for MCP catalog tokens, p95/p99, sustainable calls/s, cache hit, availability, incident rates, or current-revision adoption. Set SLOs through conformance, load, fault-injection, model-selection, and security tests on the exact host/server pair.

## 4. Distributed Resilience & Security

### 4.1 Explicit state and durable work

```text
┌──────────────┐ modern POST ┌──────────────┐ route      ┌──────────────┐
│ Host/client  ├────────────►│ MCP gateway  ├───────────►│ Stateless    │
│ approval/cache│◄─response──┤ auth/policy  │            │ server replica│
└──────┬───────┘             └──────────────┘            └──────┬───────┘
       │                                                        │ explicit handle
       ▼                                                        ▼
┌──────────────┐ invalidate  ┌──────────────┐            ┌──────────────┐
│ Subscription │◄────────────┤ Event bus    │            │ Temporal/DB │
│ reconnect/read│            │ hints        │            │ task/effect │
└──────────────┘             └──────────────┘            └──────────────┘
```

The host owns conversation/context/approval; client owns negotiated revision and scoped caches; domain services own cross-call workflow/effects; server owns catalog truth; secure token stores own OAuth state. Modern requests can hit any replica. MRTR state is explicit, integrity-protected, principal/request/expiry-bound, and durably marked consumed for one-time operations.

Put long OCR/export/payment or other work in Temporal/Kafka/queue infrastructure. Expose negotiated Tasks extension or bounded `start/status/cancel` tools with a visible domain task ID. Persist leases, checkpoints, retry classification, result, and cancellation. A normal MCP call is not a workflow engine.

### 4.2 Retry, idempotency, notifications, and rollout

For writes, persist `(principal, tool, idempotency_key, canonical_args_digest) -> outcome/operation_id`; duplicate same-argument calls return the prior result, and key reuse with different arguments is rejected. On timeout, call status/read before one bounded retry. Cancellation is cooperative and cannot undo a commit.

Classify auth, authorization, protocol, schema, domain, dependency, timeout, cancelled, policy-denied, and unknown effect. Retry only known transient failures with exponential full jitter, aggregate deadline, per-server/tool/backend breaker, and one retry owner. Poison messages/tasks enter a DLQ; ambiguous effects remain repairable, never silently failed.

Subscriptions use an inter-replica bus but remain invalidation hints. On disconnect, discard assumptions, re-subscribe, and reread. Disable reverse-proxy buffering, configure idle timeouts/keepalive, and bound streams/events.

Canary modern-only and dual-era compatibility combinations across stdio/HTTP JSON/SSE, auth modes, pagination/cache/MRTR/subscriptions/cancellation. Track negotiated era and fallback. Roll out server capability/schema and replica versions atomically enough to avoid discovery/call contradictions; pin modern when required semantics cannot tolerate downgrade.

### 4.3 Zero-Trust OAuth, MCP policy, and semantic threats

```text
┌──────────────┐ OAuth/token ┌────────────────┐ scoped token ┌──────────────┐
│ Host/client  ├────────────►│ MCP policy     ├─────────────►│ MCP server  │
│ trusted UI   │             │ gateway        │              │ untrusted I/O│
└──────┬───────┘             └───────┬────────┘              └──────┬───────┘
       │ exact approval              │                              │ backend token
       ▼                             ▼                              ▼
┌──────────────┐             ┌──────────────┐               ┌──────────────┐
│ Consent/     │             │ Data-flow + │               │ Domain API   │
│ policy log   │             │ tool RBAC   │               │ row policy   │
└──────────────┘             └──────────────┘               └──────────────┘
```

For HTTP, use protected-resource metadata, PKCE with `S256`, exact redirect validation, issuer checks, RFC 8707 resource indicators, audience validation, minimal scopes, targeted step-up, secure storage, rotation, and revocation. Never pass the inbound MCP token to an upstream API; exchange for a separate audience token. Machine client-credentials is an optional negotiated extension, not delegated-user identity.

OAuth does not implement tool/row authorization. Enforce `(principal, tenant, tool/resource, concrete arguments, risk, scope, purpose)` on every call/read. Tool-level RBAC separates discover/list, read, propose/preview, mutate, approve, status/cancel, and administer. `clientInfo`, tenant arguments, annotations, and model text never grant access.

Treat server instructions, tool names/descriptions/schemas/annotations, prompts, resources, errors, and results as untrusted semantic data. Label provenance, cap content, keep policy outside server context, and block prohibited cross-server flows. Validate shell/SQL/path/URL/template arguments conventionally; canonicalize paths and constrain scheme/host/IP to prevent traversal/SSRF. Approval occurs in trusted host UI after policy and shows actual principal/tool/destination/arguments/effect.

### 4.4 Local supply chain, PII, and audit

stdio executes local code. Require allowlisted publisher/repository, exact version/digest, lockfile/SBOM/signature/provenance, dependency/static scan, tool-schema review, staged promotion, and kill switch. Sandbox with read-only filesystem, explicit mounts, stripped environment/secrets, denied network by default, CPU/memory/process/time/output limits, and process-group termination. Stdout contains only protocol frames; stderr is logging.

The official registry is metadata/discovery, not a curated security authority. Pin executable integrity independently and reapprove definition-digest changes. Keep Inspector, local proxies, bridges, SDKs, and dependencies patched; remote semantic content plus a privileged local launcher can collapse boundaries.

The PII path is `classify -> detect -> minimize/redact/tokenize -> authorize server/tool -> transmit -> rehydrate only at allowed boundary -> audit/delete`. Apply it to arguments, resources, tool results, MRTR input, caches, prompts, traces, evals, and backups. Hosted connectors/remote servers require DPA, residency, retention/training, subprocessors, deletion, encryption, and incident review.

Immutable audit records principal/tenant/workload, canonical endpoint/artifact digest, era/revision/extensions/catalog digest, JSON-RPC/host trace IDs, method/name, policy/approval, argument hash and permitted fields, result/error class, latency/tokens, backend operation/idempotency, OAuth challenges/scopes, MRTR, cache, retry, cancellation, install/config/definition change, and incident. Propagate W3C trace context in `_meta`, never secrets/PII in baggage, hash-chain/sign WORM batches, and log audit access.

### 4.5 Failure taxonomy

| Failure | Detection | Containment |
|---|---|---|
| era mismatch/silent downgrade | negotiation/fallback metric, compatibility tests | auto only where safe; pin required modern revision |
| malformed metadata/header mismatch | JSON-RPC/HTTP code and gateway comparison | reject; refresh schema before one bounded retry |
| stale/skewed catalog | digest, method-not-found by deployment | bounded TTL, notification invalidate, atomic rollout |
| name collision/rug pull | namespace/digest comparison | stable host namespace; review/reapprove/kill switch |
| invalid/oversized input/output | schema/byte/token/depth counters | strict caps, pagination/artifact links, reject |
| duplicate/unknown side effect | backend ledger/status/readback | domain idempotency, reconcile before retry |
| MRTR replay/loop | signature/redemption/round/fingerprint metrics | bind and expire state; one-time store; hard stop |
| replica-local continuation | replica traces/intermittent not-found | external durable state or signed handle |
| subscription gap/proxy buffer | reconnect gap/first-event tail | re-list/read, no buffering, keepalive |
| path traversal/SSRF/injection | policy tests, sandbox/egress logs | canonicalization, allowlists, reference monitor |
| audience/token confusion | issuer/audience rejection metrics | resource-bound and separate upstream tokens |
| stdout corruption/orphan process | framing/process ownership monitors | stderr logs, process group, close/kill timeout |

Test contract, modern/legacy conformance, policy/security, failure/restart/ambiguous commit, load/catalog/streams, and end-to-end model selection. A development Inspector is not a security scanner.

## 5. Production Enterprise Code

This Python 3.11 standard-library program implements a small modern MCP host/client/server boundary. It validates JSON-RPC, per-request revision metadata, modern HTTP method/name/version header mirroring, resource and tool authorization, output schemas, trusted-host approval, and refund idempotency. `invoice.assess` uses primary -> secondary -> deterministic `manual_review` model fallback with full-jitter retry and closed/open/half-open breakers. Logs carry trace and request IDs. Run with `python modern_mcp_boundary.py`.

```python
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Protocol, Sequence


REVISION = "2026-07-28"
CLIENT_INFO = {"name": "billing-host", "version": "1.0"}
SERVER_INFO = {"name": "billing", "version": "1.0"}


class TransientError(RuntimeError):
    """A retryable dependency failure."""


class PermanentError(RuntimeError):
    """A policy, schema, or protocol failure."""


class CircuitOpen(TransientError):
    """A dependency is temporarily disabled."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("trace_id", "request_id", "method", "tool_name", "stage",
                    "attempt"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("mcp")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str
    scopes: frozenset[str]


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


class AssessmentModel(Protocol):
    name: str

    def assess(self, invoice: dict[str, object], timeout_s: float) -> str:
        raise RuntimeError("AssessmentModel is an interface")


class AssessmentChain:
    def __init__(self, models: Sequence[AssessmentModel]):
        if len(models) < 2:
            raise ValueError("primary and secondary models required")
        self._models = tuple(models)
        self._breakers = {model.name: Breaker() for model in models}

    def assess(self, invoice: dict[str, object], deadline: float,
               trace_id: str, request_id: str) -> dict[str, str]:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"risk": "manual_review", "model": "deterministic"}
                try:
                    breaker.before()
                    raw = model.assess(invoice, min(remaining, 3.0))
                    value = json.loads(raw)
                    if (not isinstance(value, dict) or set(value) != {"risk"}
                            or value["risk"] not in {"low", "high"}):
                        raise PermanentError("model output violates schema")
                    breaker.success()
                    return {"risk": value["risk"], "model": model.name}
                except CircuitOpen:
                    break
                except (json.JSONDecodeError, PermanentError):
                    breaker.failure()
                    break
                except (TimeoutError, ConnectionError, TransientError):
                    breaker.failure()
                    logger.warning("assessment model failure", extra={
                        "trace_id": trace_id, "request_id": request_id,
                        "stage": model.name, "attempt": attempt})
                    if attempt == 2:
                        break
                    delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
                    if time.monotonic() + delay >= deadline:
                        return {"risk": "manual_review", "model": "deterministic"}
                    time.sleep(delay)
        return {"risk": "manual_review", "model": "deterministic"}


class Ledger:
    def __init__(self):
        self.invoices = {("tenant-a", "inv-7"): {"invoiceId": "inv-7",
                         "amount": 100, "currency": "USD"}}
        self._refunds: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}
        self._lock = threading.Lock()

    def invoice(self, principal: Principal, invoice_id: str) -> dict[str, object]:
        try:
            return dict(self.invoices[(principal.tenant, invoice_id)])
        except KeyError as exc:
            raise PermanentError("invoice not found") from exc

    def refund(self, principal: Principal, invoice_id: str, amount: int,
               key: str) -> dict[str, object]:
        invoice = self.invoice(principal, invoice_id)
        if amount <= 0 or amount > int(invoice["amount"]):
            raise PermanentError("invalid refund amount")
        digest = hashlib.sha256(
            f"{principal.tenant}|{invoice_id}|{amount}".encode()
        ).hexdigest()
        ledger_key = (principal.subject, key)
        with self._lock:
            existing = self._refunds.get(ledger_key)
            if existing:
                if existing[0] != digest:
                    raise PermanentError("idempotency key reused with different arguments")
                return dict(existing[1])
            result = {"refundId": "rf-" + uuid.uuid4().hex[:10],
                      "invoiceId": invoice_id, "amount": amount,
                      "status": "committed"}
            self._refunds[ledger_key] = (digest, result)
            return dict(result)


class ApprovalStore:
    def __init__(self):
        self._values: dict[str, tuple[str, str, int, str, float]] = {}

    def issue(self, principal: Principal, invoice_id: str, amount: int,
              idempotency_key: str) -> str:
        token = uuid.uuid4().hex
        self._values[token] = (principal.subject, invoice_id, amount,
                               idempotency_key, time.time()+60)
        return token

    def validate(self, token: str, principal: Principal, invoice_id: str,
                 amount: int, idempotency_key: str) -> None:
        value = self._values.get(token)
        if value is None:
            raise PermanentError("approval invalid, changed, or expired")
        subject, approved_invoice, approved_amount, approved_key, expires_at = value
        if ((subject, approved_invoice, approved_amount, approved_key) !=
                (principal.subject, invoice_id, amount, idempotency_key)
                or expires_at < time.time()):
            raise PermanentError("approval invalid, changed, or expired")


def protocol_error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class McpServer:
    def __init__(self, ledger: Ledger, approvals: ApprovalStore,
                 assessment: AssessmentChain):
        self._ledger = ledger
        self._approvals = approvals
        self._assessment = assessment

    @staticmethod
    def _validate_envelope(headers: dict[str, str], request: dict[str, object]) -> None:
        if request.get("jsonrpc") != "2.0" or "id" not in request:
            raise PermanentError("invalid JSON-RPC request")
        method = request.get("method")
        params = request.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise PermanentError("invalid method or params")
        meta = params.get("_meta")
        if (not isinstance(meta, dict)
                or meta.get("io.modelcontextprotocol/protocolVersion") != REVISION
                or not isinstance(meta.get(
                    "io.modelcontextprotocol/clientCapabilities"), dict)):
            raise PermanentError("missing modern per-request metadata")
        if headers.get("MCP-Protocol-Version") != REVISION \
                or headers.get("Mcp-Method") != method:
            raise PermanentError("HeaderMismatch")
        name = params.get("name") or params.get("uri")
        if name is not None and headers.get("Mcp-Name") != str(name):
            raise PermanentError("HeaderMismatch")

    def handle(self, headers: dict[str, str], request: dict[str, object],
               principal: Principal, trace_id: str) -> dict[str, object]:
        request_id = request.get("id")
        try:
            self._validate_envelope(headers, request)
            method = str(request["method"])
            params = request["params"]
            assert isinstance(params, dict)
            if method == "server/discover":
                result = {"resultType": "complete",
                          "supportedVersions": [REVISION],
                          "capabilities": {"tools": {}, "resources": {}},
                          "ttlMs": 3_600_000, "cacheScope": "public",
                          "_meta": {"io.modelcontextprotocol/serverInfo":
                                    dict(SERVER_INFO)}}
            elif method == "resources/read":
                if "invoice.read" not in principal.scopes:
                    raise PermanentError("policy denied resource")
                uri = params.get("uri")
                if not isinstance(uri, str) or not uri.startswith("invoice://"):
                    raise PermanentError("invalid resource URI")
                invoice = self._ledger.invoice(principal, uri.removeprefix("invoice://"))
                result = {"resultType": "complete", "contents": [{"uri": uri,
                          "mimeType": "application/json",
                          "text": json.dumps(invoice, sort_keys=True)}]}
            elif method == "tools/call":
                result = self._call_tool(params, principal, trace_id, str(request_id))
            else:
                return protocol_error(request_id, -32601, "Method not found")
            result_meta = result.setdefault("_meta", {})
            if not isinstance(result_meta, dict):
                raise PermanentError("invalid result metadata")
            result_meta.setdefault("io.modelcontextprotocol/serverInfo",
                                   dict(SERVER_INFO))
            logger.info("mcp request complete", extra={
                "trace_id": trace_id, "request_id": request_id,
                "method": method, "tool_name": params.get("name"),
                "stage": "complete"})
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except PermanentError as exc:
            if str(exc) == "HeaderMismatch":
                code = -32020
            elif str(exc) == "unknown tool":
                code = -32601
            elif str(exc).startswith("policy denied"):
                code = -32003
            else:
                code = -32602
            return protocol_error(request_id, code, str(exc))

    def _call_tool(self, params: dict[str, object], principal: Principal,
                   trace_id: str, request_id: str) -> dict[str, object]:
        name, args = params.get("name"), params.get("arguments")
        if not isinstance(name, str) or not isinstance(args, dict):
            raise PermanentError("invalid tool call")
        if name == "invoice.assess":
            if "invoice.read" not in principal.scopes:
                raise PermanentError("policy denied tool")
            invoice_id = args.get("invoiceId")
            if not isinstance(invoice_id, str):
                raise PermanentError("invoiceId must be string")
            invoice = self._ledger.invoice(principal, invoice_id)
            assessment = self._assessment.assess(
                invoice, time.monotonic()+1.0, trace_id, request_id
            )
            return {"resultType": "complete", "isError": False,
                    "content": [], "structuredContent": assessment}
        if name == "refund.execute":
            if "refund.write" not in principal.scopes:
                raise PermanentError("policy denied tool")
            required = (args.get("invoiceId"), args.get("amount"),
                        args.get("idempotencyKey"), args.get("approvalToken"))
            if (not isinstance(required[0], str) or not isinstance(required[1], int)
                    or not isinstance(required[2], str) or not isinstance(required[3], str)):
                raise PermanentError("refund arguments invalid")
            try:
                self._approvals.validate(required[3], principal, required[0],
                                         required[1], required[2])
                refund = self._ledger.refund(principal, required[0], required[1], required[2])
                return {"resultType": "complete", "isError": False,
                        "content": [], "structuredContent": refund}
            except PermanentError as exc:
                return {"resultType": "complete", "isError": True,
                        "content": [{"type": "text", "text": str(exc)}],
                        "errorClass": "tool-domain", "message": str(exc)}
        raise PermanentError("unknown tool")


class McpClient:
    def __init__(self, server: McpServer, principal: Principal):
        self._server = server
        self._principal = principal

    def request(self, method: str, *, name: str | None = None,
                arguments: dict[str, object] | None = None) -> dict[str, object]:
        request_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
        params: dict[str, object] = {"_meta": {
            "io.modelcontextprotocol/protocolVersion": REVISION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": dict(CLIENT_INFO)}}
        if method == "resources/read":
            params["uri"] = name
        elif name is not None:
            params["name"] = name
            params["arguments"] = arguments or {}
        request = {"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params}
        headers = {"MCP-Protocol-Version": REVISION, "Mcp-Method": method}
        if name is not None:
            headers["Mcp-Name"] = name
        response = self._server.handle(headers, request, self._principal, trace_id)
        if response.get("id") != request_id:
            raise PermanentError("response correlation mismatch")
        result = response.get("result")
        if result is not None and (not isinstance(result, dict)
                                   or result.get("resultType") not in {
                                       "complete", "input_required"}):
            raise PermanentError("invalid result envelope")
        if isinstance(result, dict):
            result_meta = result.get("_meta")
            server_info = (result_meta.get("io.modelcontextprotocol/serverInfo")
                           if isinstance(result_meta, dict) else None)
            if (not isinstance(server_info, dict)
                    or not isinstance(server_info.get("name"), str)
                    or not isinstance(server_info.get("version"), str)):
                raise PermanentError("missing server result metadata")
            if method == "server/discover" and (
                    not isinstance(result.get("supportedVersions"), list)
                    or REVISION not in result["supportedVersions"]
                    or not isinstance(result.get("capabilities"), dict)
                    or not isinstance(result.get("ttlMs"), int)
                    or result.get("cacheScope") not in {"private", "public"}):
                raise PermanentError("invalid discovery result")
        return response


class DemoModel:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def assess(self, invoice: dict[str, object], timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TimeoutError("assessment model unavailable")
        return json.dumps({"risk": "low" if int(invoice["amount"]) <= 500 else "high"})


def main() -> None:
    principal = Principal("user-42", "tenant-a",
                          frozenset({"invoice.read", "refund.write"}))
    ledger, approvals = Ledger(), ApprovalStore()
    server = McpServer(ledger, approvals,
                       AssessmentChain((DemoModel("primary", False),
                                        DemoModel("secondary", True))))
    client = McpClient(server, principal)
    discovery = client.request("server/discover")
    resource = client.request("resources/read", name="invoice://inv-7")
    assessment = client.request("tools/call", name="invoice.assess",
                                arguments={"invoiceId": "inv-7"})
    approval = approvals.issue(principal, "inv-7", 25, "run-9:refund")
    refund_args = {"invoiceId": "inv-7", "amount": 25,
                   "idempotencyKey": "run-9:refund", "approvalToken": approval}
    refund = client.request("tools/call", name="refund.execute",
                            arguments=refund_args)
    refund_replay = client.request("tools/call", name="refund.execute",
                                   arguments=refund_args)
    print(json.dumps({"discovery": discovery["result"],
                      "resource": resource["result"],
                      "assessment": assessment["result"],
                      "refund": refund["result"],
                      "refundReplayMatches":
                          refund_replay["result"] == refund["result"]},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The demo sends no `initialize` or session ID: each call is a complete modern request. The primary assessment model opens its breaker after bounded failures, the secondary succeeds, and an all-model outage returns deterministic `manual_review`. The resource and tool paths both enforce tenant/scopes. Refund approval is bound to subject, invoice, amount, idempotency key, and expiry; the domain ledger owns effect idempotency, so a response-lost retry returns the original refund. Protocol errors remain JSON-RPC errors, while refund business failures are normal tool results with `isError: true`.

## 6. Architectural System Design Scenarios

### Scenario 1 - Sandboxed local coding server

**Problem statement.** Design MCP integration for 20,000 developer workstations. The assistant must read only the checked-out repository, run formatter check/apply, keep p95 tool time under 2 seconds, work offline, prevent package/tool poisoning and secret/network access, and preserve an auditable confirmation for writes.

**Proposed architecture.** Use one pinned modern stdio server per IDE host, installed by artifact digest and launched in an OS sandbox. Expose repository files as resources under one canonical `file://` root; expose separate `format.check` and `format.apply` tools instead of a shell. Mount only the workspace, strip inherited secrets/environment, deny network, cap CPU/memory/process/time/output, own the child process group, and write logs only to stderr. The trusted IDE policy shows exact changed files/diff before issuing a short-lived approval for `format.apply`.

```text
┌──────────────┐ trusted UI  ┌──────────────┐ stdio      ┌──────────────┐
│ Developer IDE├────────────►│ MCP host +   ├───────────►│ Pinned MCP   │
│ diff approve │◄─resource───┤ local client │            │ server       │
└──────────────┘             └──────────────┘            └──────┬───────┘
                                                               │ sandbox mount
                                                               ▼
                                                        ┌──────────────┐
                                                        │ Workspace    │
                                                        │ no network/  │
                                                        │ secrets      │
                                                        └──────────────┘
```

At 20,000 hosts, fleet concurrency is decentralized, but package rollout, definition digests, crashes/orphans, p95/p99, and kill-switch health are centralized telemetry concerns. Per host, cap one formatter write, four reads, resource bytes, and child lifetime. A server crash restarts cleanly; an ambiguous file write verifies the digest/diff before any retry.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Pinned stdio server in sandbox** | Low network/cloud cost | Lowest local/offline path | High fleet/package controls | Strong with mounts/env/network limits | High decentralized hosts |
| Local HTTP daemon | Similar compute | Loopback overhead; reusable process | Higher port/service lifecycle | Larger listening/CSRF/proxy surface | High but persistent attack surface |
| Remote formatting MCP service | Cloud/egress cost | Network-dependent | Central service easier update | Source code crosses boundary | Quota/network limited |

**Decision rationale.** stdio matches one trusted host and one local capability process without a listening service. It is selected only with executable-integrity and sandbox controls; “local” alone is not safe. Narrow tools and application-selected resources remove the need for a generic shell.

### Scenario 2 - Multi-tenant enterprise MCP gateway

**Problem statement.** Design a gateway serving 5,000 model hosts and 200 internal MCP servers at 2,000 functional calls/s. It must filter catalogs and data per user/tenant, namespace collisions, support modern-only and approved legacy backends, keep read p95 under 400 ms and write p99 under 3 seconds, achieve RPO 0 for effects/approval, and prevent token/cross-server data confusion.

**Proposed architecture.** Place stateless Streamable HTTP gateway replicas behind OAuth/TLS. The gateway validates RFC 8707 audience, issuer, PKCE/user or workload identity, modern headers/body, method/name, schema, catalog digest, quotas, and data-flow policy; it exchanges separate narrow upstream tokens. A versioned registry pins endpoint/artifact/era/tool definitions. Private catalog caches include principal, tenant, revision, scopes, params, and digest. Temporal/Kafka and a domain ledger own long tasks, idempotency, status, and subscription events. Every backend has circuit/bulkhead/kill switch; legacy is isolated and observable, never silent fallback.

```text
┌──────────────┐ OAuth/TLS  ┌──────────────┐ route/policy ┌──────────────┐
│ Model hosts  ├───────────►│ Stateless MCP├─────────────►│ Modern MCP   │
│ + approvals  │◄─results───┤ gateway fleet│              │ servers      │
└──────────────┘            └──────┬───────┘              └──────────────┘
                                    │ isolated dual-era
                                    ├─────────────────────►┌──────────────┐
                                    │                      │ Legacy bridge│
                                    ▼                      └──────────────┘
                             ┌──────────────┐
                             │ Registry/cache│
                             │ Temporal/ledger│
                             └──────────────┘
```

At 2,000 calls/s with the illustrative 80/20 mix, size 1,600 reads/s and 400 writes/s plus MRTR, catalog refresh, auth, SSE, and trace load. Isolate writes/status from catalog and subscription floods. Queue long work; reserve reconciliation/cancel capacity; enforce per-tenant/server/tool fairness. A gateway outage must not lose backend effects because the domain ledger is authoritative.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/governance | Scalability ceiling |
|---|---|---|---|---|---|
| Hosts connect directly to every server | Low central gateway cost | One fewer hop | Very high host compatibility/config | Inconsistent policy/token/data flow | Fragmented by host/server matrix |
| **Policy-aware MCP gateway** | Gateway/cache/control cost | Extra hop, cache benefit | High central platform | Strong uniform authz, namespaces, audit | High with stateless replicas/bulkheads |
| One generic super-server | Lower discovery complexity | Simple single endpoint | Medium initially | Excess privilege/blast radius/catalog bloat | Backend coupling bottleneck |

**Decision rationale.** A gateway is justified by 5,000 hosts, 200 servers, collision handling, dual-era containment, and uniform data-flow policy. Stateless modern routing supports horizontal scale; durable business state remains outside MCP replicas. The design explicitly mitigates the gateway's confused-deputy and blast-radius concentration risks.

## Interview Review

1. **What does MCP standardize?** JSON-RPC capability discovery/invocation for host-mediated tools, resources, prompts, and subscriptions across transports.
2. **Tool versus resource?** A tool is a model-selectable operation; a resource is host-selected URI context. Neither label enforces safety.
3. **What changed in `2026-07-28`?** Per-request metadata/discovery replace initialization, protocol sessions disappear, MRTR replaces server requests, and filtered subscriptions replace unsolicited updates.
4. **Does MCP provide exactly once?** No. JSON-RPC IDs correlate messages; domain idempotency and status/readback own effects.
5. **How does authorization work?** Verified OAuth/workload identity plus per-tool/resource/row policy; `clientInfo`, annotations, and descriptions are not authority.
6. **MCP versus A2A?** MCP exposes bounded capabilities to a host/agent; A2A carries remote-agent task/message/artifact lifecycle.

## Primary References

- [MCP `2026-07-28` announcement](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP base protocol](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/index.mdx)
- [MCP discovery](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/discover.mdx)
- [MCP tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx)
- [MCP resources](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/resources.mdx)
- [MCP Streamable HTTP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)
- [MCP stdio](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/stdio.mdx)
- [MCP MRTR](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/mrtr.mdx)
- [MCP subscriptions](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/subscriptions.mdx)
- [MCP authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx)
- [MCP canonical schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.json)
- [Official TypeScript SDK v2](https://ts.sdk.modelcontextprotocol.io/v2/)
- [MCP SDK tiers](https://modelcontextprotocol.io/community/sdk-tiers)
- [OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
- [A2A and MCP](https://a2a-protocol.org/latest/)
- [RFC 8707 resource indicators](https://www.rfc-editor.org/rfc/rfc8707.html)
- [RFC 9728 protected resource metadata](https://www.rfc-editor.org/rfc/rfc9728.html)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
