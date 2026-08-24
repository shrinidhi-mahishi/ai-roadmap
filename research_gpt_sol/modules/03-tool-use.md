# 03 — Tool Use

**Scope:** APIs, function calling, browser automation, and code execution.  
**Study goal:** Turn probabilistic tool proposals into authorized, idempotent, observable, and verifiably successful external operations.

The governing contract is: **propose → validate → authorize → approve → execute once → verify → observe**. A model-issued tool call is not execution, a schema-valid argument is not permission, and a model’s claim of success is not a postcondition.

## 1. System Topology & Data Flow

### Reference topology

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Tool/spec registry │ schemas/adapters │ RBAC/ABAC │ approvals │ budgets      │
│ model/tool evals   │ version rollout │ egress    │ secrets   │ kill switches │
└──────────────┬──────────────────┬──────────────────┬────────────────┬─────────┘
               │ eligible tools   │ policy/version   │ short token    │ limits
               ▼                  ▼                  ▼                ▼
                                       DATA PLANE
┌──────────┐   ┌──────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│ API/WAF  ├──►│ Run/model    ├──►│ Model inference ├──►│ Tool gateway        │
│ identity │   │ router       │   │ typed proposal  │   │ schema/domain/authz │
└──────────┘   └──────┬───────┘   └────────┬────────┘   │ approve/idempotency │
                      │                    │            └───┬────┬────┬──────┘
                      │                    │                │    │    │
                      │                    │          ┌─────▼┐ ┌─▼────┐ ┌▼─────────┐
                      │                    │          │ API  │ │Search│ │ Browser  │
                      │                    │          │proxy │ │fetch │ │ worker   │
                      │                    │          └──┬───┘ └──┬───┘ └────┬─────┘
                      │                    │             │        │          │
                      │                    │             │     ┌──▼──────────▼──┐
                      │                    │             │     │ Code sandbox   │
                      │                    │             │     │ broker/microVM │
                      │                    │             │     └───────┬────────┘
                      │                    │             │             │
                      │                    └─────────────┴──────┬──────┘
                      │                 normalized, sanitized observation
                      │                                           │
                      └─────────────────────────────── next turn ◄─┘
                                PERSISTENCE LAYER
┌──────────────────────────────────────────────────────────────────────────────┐
│ Run trajectory │ call/idempotency ledger │ approvals │ external IDs/receipts│
│ browser milestones │ sandbox image/input/output digests │ encrypted artifacts│
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ spans, counters, audit events
                                ▼
                       TELEMETRY / OBSERVABILITY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OTel traces │ structured logs │ task/tool/cost metrics │ WORM audit + SIEM   │
└──────────────────────────────────────────────────────────────────────────────┘
```

The control plane owns which capabilities exist, who may use them, adapter/schema versions, budgets, approvals, egress, credentials, and rollout. The data plane executes one pinned decision. Model-generated code, browser pages, API responses, search results, filenames, and stdout cannot bypass the gateway or modify control-plane authority.

### Request flow

1. The edge authenticates the principal and assigns `run_id`, `trace_id`, tenant, purpose, deadline, and spend envelope.
2. Policy intersects intent-retrieved tool candidates with principal permissions, workflow state, tenant rules, risk class, and quota. Only this minimum schema set is shown to the model.
3. The model returns a final answer or one or more `{tool_name, arguments, call_id}` proposals. Strict schemas reduce malformed arguments but do not establish meaning or permission.
4. The gateway resolves the exact tool/schema/adapter version, validates syntax and business invariants, resolves human labels to canonical resource IDs, re-authorizes the concrete resource, and obtains signed approval at the point of risk.
5. Before dispatch, it writes `call_proposed/validated/approved` events and reserves an idempotency key bound to tenant, call, tool version, canonical arguments, and approval.
6. The worker receives a short-lived audience-restricted credential, deadline, fencing token, resource limits, and the idempotency key. API, search, browser, and code workers run in separate bulkheads.
7. The adapter validates the hostile external response, limits rows/bytes, strips active content, and verifies a postcondition or external receipt. A browser verifies origin, account, fields, and confirmation; a sandbox validates artifact type, path, size, content, and digest before export.
8. The gateway commits the normalized result and receipt before returning a `tool_result` tied to the original `call_id`. Unknown mutation status triggers reconciliation, never blind retry or model inference.
9. The model may continue with the observation. Max steps, repeated-call hashes, wall time, tokens, tool calls, paid operations, browser actions, sandbox resources, and spend guarantee a terminal outcome.
10. Every boundary emits redacted traces, structured logs, token/tool/cost counters, policy and approval IDs, and append-only audit evidence.

## 2. Core Mechanics & Algorithms

### 2.1 API tools and contracts

OpenAPI describes operations, schemas, and security schemes, but it is neither a model-facing toolset nor authorization policy ([OpenAPI 3.2](https://spec.openapis.org/oas/latest.html)). Generate narrow, reviewed tools from selected operations:

- preserve source `operationId`, spec digest, owner, and adapter version;
- remove authentication/server fields and inject credentials only at execution;
- split reads from mutations, for example `get_invoice` and `refund_invoice`;
- bound strings, arrays, pagination, numeric ranges, result rows, and bytes;
- expose canonical IDs and typed error/result shapes;
- set strict object schemas with required properties and `additionalProperties: false` where supported.

JSON Schema proves shape, not ownership, referential integrity, freshness, or a valid state transition. Domain validation runs before authorization, and authorization is repeated immediately before the effect.

HTTP idempotency describes intended semantics: safe reads and PUT/DELETE are idempotent by definition, while arbitrary POST must not be automatically replayed unless the client knows it is safe ([RFC 9110](https://datatracker.ietf.org/doc/html/rfc9110)). A mutation uses an application idempotency key:

```text
K = SHA-256(tenant || principal_scope || call_id || tool_version ||
            canonical_arguments || approval_id)
```

The server or ledger binds `K` to an argument digest. A repeated identical request returns the recorded outcome; key reuse with different arguments fails. If the network times out after send, query by key or resource state before retrying.

### 2.2 Function-calling orchestration

Function calling is a message protocol: schema in, typed proposal out, application execution, result bound to `call_id`, then another model turn ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)). `none`, `auto`, required-call modes, and allowed-tool lists should constrain choice structurally.

```text
┌───────────┐ proposal ┌───────────┐ valid/authz ┌───────────┐ approval ┌──────────┐
│ REQUESTED ├─────────►│ PROPOSED  ├────────────►│ VALIDATED ├─────────►│ APPROVED │
└───────────┘          └─────┬─────┘             └─────┬─────┘          └────┬─────┘
                             │ deny/invalid             │ deny                 │ lease
                             ▼                          ▼                      ▼
                        ┌─────────┐                ┌─────────┐            ┌──────────┐
                        │ REJECTED│                │ REJECTED│            │ EXECUTING│
                        └─────────┘                └─────────┘            └────┬─────┘
                                                                            │
                                                 ┌──────────────────────────┼────────┐
                                                 │ success                  │ unknown│ transient
                                                 ▼                          ▼        ▼
                                           ┌──────────┐              ┌──────────┐ ┌───────┐
                                           │ VERIFIED │              │RECONCILE │ │ RETRY │
                                           └────┬─────┘              └────┬─────┘ └───┬───┘
                                                │ commit                   │ receipt     │ bounded
                                                ▼                          └──────┬───────┘
                                           ┌──────────┐  observation              │
                                           │ RECORDED ├───────────────────────────┘
                                           └──────────┘
```

**Invariants**

- Only a pinned, policy-visible tool version can reach `EXECUTING`.
- `call_id` uniquely binds proposal, approval, result, and next-turn observation.
- Mutation execution requires durable reservation and a server-recognized idempotency key.
- `RECORDED` requires output validation plus an external postcondition/receipt; model self-report is irrelevant.
- Replay uses recorded model/tool results. A deliberate re-execution is a new linked attempt.
- The loop ends at final answer, rejection/escalation, or a hard step/time/token/tool/spend bound.

Parallelize only independent reads or commutative operations. For `n` independent calls, serial tool time is approximately `ΣT_i`; parallel critical-path time is `max(T_i)` plus scheduling, but quota demand and partial-failure probability increase. Calls with data dependencies, approvals, or conflicting writes must be sequenced. Programmatic tool execution is useful for bounded filter/join/rank/aggregate work when intermediate results need not drive semantic decisions; direct calls preserve inspection, citations, and approval boundaries.

Tool selection itself is evaluated separately from argument construction and result use. BFCL covers serial, parallel, abstention, and stateful behavior; ToolSandbox tests intermediate state and arbitrary trajectories ([BFCL](https://proceedings.mlr.press/v267/patil25a.html), [ToolSandbox](https://arxiv.org/abs/2408.04682)).

### 2.3 Search, browser, and computer use

Search/fetch, DOM automation, and visual computer use expose increasingly broad action spaces:

| Mode | Observation/action | Best use | Required proof |
|---|---|---|---|
| Search/fetch API | query, URL, text, citation metadata | current public evidence | query, canonical URL, time, span, digest |
| DOM browser | semantic locators, accessibility tree, navigation | legacy site without API | origin/account, target, before/after DOM state, receipt |
| Visual control | screenshot, pointer/keyboard | canvas/desktop/visual-only UI | screenshot plus semantic state assertion |

Search evidence is not authority. Claims must link to sources actually returned, and cited spans must support them. Resolve redirects and enforce scheme/domain/IP/byte policy before fetch. Keep source title, publisher, canonical URL, retrieval time, span, and content digest.

Browser control is a serialized observation-action loop:

```text
observe DOM/screenshot → propose action → validate origin/target/account
→ execute → re-observe → verify postcondition → continue/stop
```

After `s` actions, latency is at least the sum of `s` model, browser render, network, and capture cycles; parallelizing actions on one mutable page usually violates causality. Prefer stable semantic locators and API responses over coordinates. Detect a stuck loop with a digest of `{origin, URL, salient DOM/accessibility state}`: terminate after the same digest/action pair repeats beyond a small bound.

Use one fresh, non-persistent Playwright `BrowserContext` per tenant/run. Stored auth state can impersonate a user and must be encrypted, short-lived, worker-only, and absent from source control ([Playwright isolation](https://playwright.dev/docs/browser-contexts), [auth-state warning](https://playwright.dev/docs/auth)). Never bypass CAPTCHA, MFA, HTTPS warnings, or site safety controls; hand off to the user.

### 2.4 Code execution

Choose the smallest sufficient execution mode:

```text
calculator/DSL < provider interpreter < isolated container/application kernel
               < microVM < local shell
```

A calculator/DSL has a small grammar and deterministic resource bound. Provider interpreters reduce platform operations but impose fixed runtimes, retention, package, network, and artifact semantics. Self-hosted sandboxes provide control but make kernel isolation, supply chain, patching, quotas, and incident response your responsibility. Local shell execution inherits user/host files and credentials and is suitable only for explicitly trusted developer workflows.

For hostile code, require non-root execution, read-only image, dropped capabilities, seccomp/AppArmor, user/network namespaces, cgroup CPU/memory/process limits, wall deadline, ephemeral filesystem quota, no host mounts/socket, and deny-by-default egress. Privileged containers defeat important controls; multi-tenant adversarial code warrants an application-kernel or microVM boundary ([Kubernetes seccomp](https://kubernetes.io/docs/reference/node/seccomp/)).

Inputs mount read-only by digest. Network-off is the default; necessary egress goes through a method/domain/IP/byte-limited proxy with no internal or metadata routes. Packages come from a pinned scanned mirror with lockfile/SBOM. The sandbox identity is distinct from every business-tool identity.

Code output is hostile. The broker rejects path traversal/symlinks, enforces file count/type/size, scans archives and active content, refuses unsafe deserialization, computes digests, and copies only accepted artifacts to durable object storage before the ephemeral environment expires.

**Routing invariant:** prefer no tool for in-context transformation, a deterministic library for bounded computation, a typed API for reliable state, search for current evidence, DOM browser only when no usable API exists, visual control only when structure is unavailable, and general code only when a narrower tool cannot solve the task.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 successful runs

```text
C_1000 = (I_u·P_in + I_c·P_cache + I_w·P_write + O·P_out)/1,000,000
       + N_search·P_search + N_api·P_vendor
       + browser_hours·P_browser + container_minutes·P_container
       + storage + egress
```

Input includes tool schemas and tool/search/browser/code observations returned to the model. Output includes reasoning and generated code when billed. Multiply attempt cost by observed **calls per successful task**, not submitted runs.

**Assumptions as of 2026-08-21:** 1,000 successful research runs each use 6,500 input tokens and 800 output tokens and issue two web searches. Of the input, 3,000 stable schema/instruction tokens are written once and hit 999 times; 3,500 remain uncached. Search is `$10/1,000` calls. No browser, container, third-party API, retry, storage, or egress cost. Token prices are current [standard prices](https://developers.openai.com/api/docs/pricing).

| Tier | Input/cache read/write/output per 1M | No cache incl. search / 1K | One write + 999 hits incl. search / 1K | Saving |
|---|---|---:|---:|---:|
| `gpt-5.6-sol` | $5 / $0.50 / $6.25 / $30 | `$32.50+$24+$20` = **$76.50** | `$0.0188+$1.4985+$17.50+$24+$20` = **$63.02** | 17.6% |
| `gpt-5.6-terra` | $2 / $0.20 / $2.50 / $12 | `$13+$9.60+$20` = **$42.60** | `$0.0075+$0.5994+$7+$9.60+$20` = **$37.21** | 12.7% |
| `gpt-5.6-luna` | $0.20 / $0.02 / $0.25 / $1.20 | `$1.30+$0.96+$20` = **$22.26** | `$0.0008+$0.0599+$0.70+$0.96+$20` = **$21.72** | 2.4% |

The search charge dominates `luna`; optimizing another few input tokens has little value. A 10% retry/failure overhead raises the `terra` cached path from `$37.21` to about **$40.93/1K accepted runs** if costs scale linearly. Tool definitions should be small, canonical, policy-filtered, and prefix-cacheable. Return compact typed observations and store large result bodies/artifacts externally by digest without removing IDs or evidence required for verification.

For code, add session economics explicitly. At a listed `$0.03` per 20-minute 1-GiB container, 1,000 full sessions add `$30`; if a provider bills eligible use by minute with a five-minute minimum, apply the documented minute rule to measured session duration. Browser compute, paid API transactions, screenshots, artifacts, and outbound bytes remain separate line items.

### 3.2 Latency objectives

No portable hosted-provider trajectory SLA exists. Establish application targets from production-shaped load:

```text
T_total = Σ(T_model_queue + T_prefill + T_decode)
        + critical_path(T_policy + T_approval + T_tool_queue + T_tool_exec)
        + Σ(T_browser_render + T_capture + T_network)
        + T_sandbox_allocate + T_code + T_artifact_export + T_retries
```

| Path | p50 | p95 | p99 | Tail control |
|---|---:|---:|---:|---|
| One typed read API + synthesis | ≤ 1.5 s | ≤ 4 s | ≤ 8 s | Cached schema, colocated proxy, bounded result, one region fallback. |
| Three parallel independent reads | ≤ 2 s | ≤ 6 s | ≤ 12 s | Bulkhead and per-call deadline; return typed partial failure. |
| DOM browser read/form workflow | ≤ 12 s | ≤ 45 s | ≤ 90 s | Warm browser pool, semantic checkpoints, action cap, stuck detector. |
| Code analysis + one artifact | ≤ 4 s | ≤ 20 s | ≤ 45 s | Warm sandbox/image, resource cap, bounded output, async export. |

Approval wait is measured separately from machine latency. Track tool selection, abstention, schema pass, domain rejection, policy denial, calls/steps per success, retry suppression, browser actions, sandbox cold start, artifact rejection, total success per dollar, and p50/p95/p99 by tool/operation/region.

### 3.3 Throughput and back-pressure

```text
model_turns/sec     = task_RPS × mean_model_turns
tool_ops/sec        = task_RPS × mean_tool_calls
browser_slots       = browser_task_RPS × p95_browser_duration_seconds
sandbox_slots       = code_task_RPS × p95_sandbox_duration_seconds
uncached_input_TPM  = task_RPS × 60 × mean_uncached_input_tokens
vendor_spend/hour   = calls/hour × vendor_price_per_call
```

At `100 task RPS`, `2.2` model turns/task, `1.8` tool calls/task, 6,500 uncached input tokens/task, and 800 output tokens/task, plan for `220 model turns/s`, `180 tool ops/s`, `39M input TPM`, and `4.8M output TPM`. If `5 RPS` are browser tasks with `45 s` p95 duration, reserve `225` browser slots; if `3 RPS` are code tasks with `20 s` p95, reserve `60` sandbox slots, before failover headroom.

Use independent bounded queues and quotas for model tokens, read APIs, mutations, paid search/vendor calls, browser slots by origin, sandbox CPU/GiB-minutes/processes, artifact bytes, egress, and compaction. Admission reserves the worst-case remaining budget before a mutation. Shed read-only low priority work before approval/status/audit paths. Honor `Retry-After`; use one retry owner, full jitter, and an aggregate deadline/spend budget. Queue resumable noninteractive work; reject work whose deadline cannot be met.

### 3.4 NFR targets and trade-offs

| Requirement | Target | Consequence / trade-off |
|---|---|---|
| Availability | 99.9% read trajectories; 99.99% status/approval API | Read fallback/partial results are allowed; mutations stop when authority or reconciliation is unavailable. |
| Durability | Every execution has prior durable reservation and terminal/unknown result | Commit-before-call adds latency but closes duplicate ambiguity. |
| RPO | 0 for call ledger, approvals, external IDs, receipts; ≤ 5 min telemetry | Synchronous replicated authority; async aggregate metrics. |
| RTO | ≤ 15 min gateway/workflow; ≤ 60 min browser/sandbox analytics | Warm control state matters; ephemeral workers can be recreated from digests. |
| Security | Zero cross-tenant sessions/artifacts; no ambient credentials; 100% mutations re-authorized | Strong isolation and approval reduce convenience and throughput. |
| Quality | Schema ≥ 99.9%; unauthorized effects = 0; task/postcondition thresholds by risk | More deterministic validation increases false rejection but prevents silent harm. |
| Audit | Proposal-to-receipt chain for every call; seven-year retention where regulated | Raw browser/tool evidence increases privacy exposure; store encrypted evidence plus redacted digests. |
| Compliance | Residency, purpose, retention, consent, vendor controls by tool feature | Provider-hosted search/browser/code may have different retention/ZDR terms from model inference. |

## 4. Distributed Resilience & Security

### 4.1 Durable execution and exactly-once effect

Append events for `call_proposed`, `validated`, `denied`, `approval_requested`, `approved`, `leased`, `started`, `succeeded|failed|unknown`, `result_committed`, and `observation_sent`. Record run/call IDs, pinned versions, canonical argument digest, principal/tenant, policy and approval IDs, idempotency key, deadline, lease/fencing token, external request/entity ID, artifact/result digest, token/tool cost, and parent event.

Temporal can orchestrate model, API, browser, and sandbox activities and replay recorded completions after failure. Kafka can fan out call/result events by `run_id`; at-least-once delivery still needs unique `(tenant_id, idempotency_key)` storage and an outbox/inbox. Neither system makes an arbitrary external POST exactly once.

Lease calls with expiry and fencing tokens; update run state with CAS. Never hold a database/distributed lock through inference, browser activity, approval, or network calls. Browser checkpoints are semantic: origin, URL, authenticated role, completed action IDs, entity IDs, DOM/state digest, and durable artifact links. Cookies and sandbox memory are disposable leases.

For an unknown mutation result, reconcile by external idempotency key, request ID, or canonical resource state. Only retry when absence is proven or the downstream service guarantees deduplication. Parallel reads return per-call success/failure. Avoid parallel writes; where unavoidable, define a saga and surface compensation failure to an operator.

### 4.2 Failure taxonomy

| Class | Examples | Handling |
|---|---|---|
| Transient | 429/503, connection reset, sandbox allocation failure | Bounded exponential backoff/full jitter, `Retry-After`, remaining deadline, per-dependency breaker. |
| Permanent | schema/domain error, auth denial, unsupported browser action, deterministic code error | No unchanged retry; correct input, request approval, or terminate. |
| Poison observation/job | parser crash, hostile archive, repeated browser/code failure | Durable attempt count, quarantine to DLQ, redacted digest, destroy worker, human triage. |
| Ambiguous effect | timeout after mutation send or browser submit | Reconcile external state/receipt before any replay. |
| Partial parallel batch | some reads succeed, others fail | Commit each call independently; return typed gaps without replaying successes. |
| UI/API drift | locator/contract errors rise after deployment | Pinned adapter/spec digest, contract tests, canary, semantic re-observe, rollback. |
| Resource attack | fork bomb, output flood, redirect loop, huge API page | cgroup/process/file/row/byte/action limits; terminate sandbox/session. |
| Infinite trajectory | repeated call+args or state digest, no progress | Step/time/token/tool/spend cap and explicit terminal escalation. |

Circuit breakers are keyed by provider, region, tool, and operation class. `CLOSED` records transient failures, `OPEN` fails fast, and limited `HALF_OPEN` probes test recovery. Search failure must not block internal reads; a mutation endpoint must not be failed over to a semantically different service. Optional cache failure bypasses to source. Missing identity, policy, approval, ledger, reconciliation, or audit persistence fails closed for mutations.

Graceful degradation order: same approved read API; approved read-only region/provider; still-fresh validated result with provenance; resumable queue with same idempotency identity; explicit partial result listing unavailable tools; typed unavailability. Never convert a failed API mutation into an unapproved browser click or local shell action.

### 4.3 Zero-Trust MCP and least capability

```text
┌──────────────┐ proposal  ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ Model host   ├──────────►│ Tool/MCP proxy ├───────────►│ MCP server / │
│ no secrets   │           │ policy + ledger│            │ adapter      │
└──────────────┘           └───────┬────────┘            └──────┬───────┘
                                   │ signed decision              │ capability
                                   ▼                              ▼
                           ┌──────────────┐                ┌──────────────┐
                           │ Approval +   │                │ API/browser/ │
                           │ token mint   │                │ microVM      │
                           └──────┬───────┘                └──────┬───────┘
                                  │                               │ hostile result
                                  └──────────────┬────────────────┘
                                                 ▼
                                          ┌──────────────┐
                                          │ Output gate  │
                                          │ verify/audit │
                                          └──────────────┘
```

- Authenticate user/workload and server identity; use encrypted transport, allowlisted server capabilities, and deny-by-default egress.
- Compute tool RBAC/ABAC from principal permissions, tenant, purpose, resource attributes, risk, workflow state, signed approval, and spend. Filter visible tools, then re-authorize the concrete call.
- Mint a short-lived, audience-bound token after authorization. No secrets appear in prompt, browser page, code environment, schema arguments, or trajectory.
- Use propose/commit tools for sensitive operations. Commit accepts a signed proposal ID, not regenerated free-form arguments.
- Treat every search/page/API/stdout/artifact observation as untrusted evidence. Direct user intent, not page text, authorizes action. Red-team with indirect-injection cases such as [AgentDojo](https://arxiv.org/abs/2406.13352).

### 4.4 Browser/code isolation, PII, and chain of custody

Browser egress blocks loopback, link-local, metadata, internal control planes, `file:`, and unsafe schemes; validates every redirect/download; and allowlists origins/methods. Approval binds action, origin, account, resource, payload digest, price/quantity, and expiry. Reconfirm after origin, account, field, or price change. Capture before/after state and external confirmation.

Sandboxes run non-root without privileged mode, host mounts/socket, ambient credentials, or default network. Mount inputs read-only; export only via a scanning broker. Destroy the environment after durable copy. A replay uses recorded image, package/SBOM, code/input digests, not residual memory.

```text
┌────────────┐   ┌────────────────┐   ┌──────────────┐   ┌──────────────┐
│ Input/result├─►│ regex + NER +  ├──►│ block/tokenize├──►│ scoped worker│
│ fields      │  │ schema scanner │   │ /mask         │   │ /model      │
└────────────┘   └───────┬────────┘   └──────┬───────┘   └──────┬───────┘
                         │ detector/version   │ vault map          │
                         └────────────────────┴─────────────►┌─────▼────────┐
                                                            │ WORM audit  │
                                                            └──────────────┘
```

PII filtering covers tool arguments/results, screenshots/OCR, code/stdout, artifacts, prompts, and logs. Structured tool parameters need schema-aware scanning because provider filters may not inspect them. Keep reversible token maps in a separate vault.

Audit events preserve proposal, validation, policy, approval, execution, reconciliation, result, observation, origin, artifact/image/input digests, and external receipt with `trace_id/run_id/call_id`. Sign or hash-chain batches into WORM storage and log reads. Store raw sensitive evidence separately under tenant encryption and governed retention; general telemetry uses redacted fields/digests ([OTel GenAI tool conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)).

## 5. Production Enterprise Code

This Python 3.11 standard-library program demonstrates a typed planner chain, strict call parsing, least-tool filtering, signed approval, canonical idempotency, durable-style call ledger, ambiguous mutation reconciliation, retries with full jitter, closed/open/half-open circuit breakers, JSON logs with correlation IDs, primary-to-secondary planning fallback, and deterministic no-action degradation. Run with `python guarded_tools.py`.

```python
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """Retryable dependency failure."""


class AmbiguousCommit(TransientError):
    """The mutation may have committed before transport failed."""


class PermanentError(RuntimeError):
    """Schema, policy, approval, or business-rule failure."""


class CircuitOpen(TransientError):
    """Dependency is failing fast during its recovery window."""


class CallInProgress(TransientError):
    """Another fenced worker owns the durable call reservation."""


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class Decision:
    kind: str
    call: ToolCall | None
    message: str
    planner: str
    degraded: bool = False

    @classmethod
    def parse(cls, raw: str, planner: str, allowed: frozenset[str]) -> "Decision":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("planner returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("kind") not in {"call", "answer"}:
            raise PermanentError("planner decision violates schema")
        if value["kind"] == "answer":
            if set(value) != {"kind", "message"} or not isinstance(value["message"], str):
                raise PermanentError("answer decision violates schema")
            return cls("answer", None, value["message"], planner)
        if set(value) != {"kind", "call_id", "name", "arguments"}:
            raise PermanentError("call decision violates exact schema")
        if value["name"] not in allowed:
            raise PermanentError("planner selected a non-visible tool")
        if not isinstance(value["call_id"], str) or not isinstance(value["arguments"], dict):
            raise PermanentError("call fields have invalid types")
        call = ToolCall(value["call_id"], value["name"], value["arguments"])
        return cls("call", call, "", planner)


@dataclass(frozen=True)
class Approval:
    approval_id: str
    call_id: str
    args_digest: str
    approver: str
    expires_at: float
    signature: str


@dataclass(frozen=True)
class ToolResult:
    status: str
    external_id: str
    data: dict[str, object]
    replayed: bool


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for field in ("correlation_id", "planner", "tool", "attempt", "state"):
            if hasattr(record, field):
                value[field] = getattr(record, field)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("guarded_tools")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sign_approval(
    secret: bytes, call: ToolCall, approver: str, ttl_s: float = 300.0
) -> Approval:
    if ttl_s <= 0:
        raise ValueError("approval TTL must be positive")
    approval_id = str(uuid.uuid4())
    expires = time.time() + ttl_s
    args_digest = sha256(canonical_json(call.arguments))
    payload = f"{approval_id}|{call.call_id}|{args_digest}|{approver}|{expires:.6f}"
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return Approval(approval_id, call.call_id, args_digest, approver, expires, signature)


def verify_approval(secret: bytes, call: ToolCall, approval: Approval) -> None:
    args_digest = sha256(canonical_json(call.arguments))
    payload = (
        f"{approval.approval_id}|{approval.call_id}|{approval.args_digest}|"
        f"{approval.approver}|{approval.expires_at:.6f}"
    )
    expected = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, approval.signature):
        raise PermanentError("approval signature is invalid")
    if approval.call_id != call.call_id or approval.args_digest != args_digest:
        raise PermanentError("approval is not bound to this call and arguments")
    if approval.expires_at <= time.time():
        raise PermanentError("approval expired")


class Planner(Protocol):
    name: str

    def plan(self, request: str, allowed_tools: Sequence[str], timeout_s: float) -> str: ...


class Driver(Protocol):
    name: str

    def execute(self, args: dict[str, object], idempotency_key: str,
                timeout_s: float) -> ToolResult: ...

    def reconcile(self, idempotency_key: str) -> ToolResult | None: ...


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, threshold: int = 3, recovery_s: float = 10.0):
        if threshold < 1 or recovery_s <= 0:
            raise ValueError("invalid breaker configuration")
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = BreakerState.CLOSED
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit is open")
                self._state = BreakerState.HALF_OPEN
            if self._state is BreakerState.HALF_OPEN:
                if self._probe:
                    raise CircuitOpen("half-open probe already running")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probe = False
            self._state = BreakerState.CLOSED

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state.value


def retry_plan(planner: Planner, breaker: CircuitBreaker, request: str,
               allowed: frozenset[str], deadline: float,
               correlation_id: str) -> Decision:
    for attempt in range(1, 4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransientError("planning deadline exhausted")
        breaker.before()
        try:
            raw = planner.plan(request, sorted(allowed), min(remaining, 5.0))
            breaker.success()
            return Decision.parse(raw, planner.name, allowed)
        except PermanentError:
            raise
        except (TimeoutError, ConnectionError, TransientError) as exc:
            breaker.failure()
            logger.warning("transient planner failure",
                           extra={"correlation_id": correlation_id,
                                  "planner": planner.name, "attempt": attempt,
                                  "state": breaker.state})
            if attempt == 3:
                raise TransientError("planner retry budget exhausted") from exc
            delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
            if delay >= deadline - time.monotonic():
                raise TransientError("insufficient planning deadline") from exc
            time.sleep(delay)
    raise AssertionError("bounded planner retry did not terminate")


class PlannerChain:
    def __init__(self, planners: Sequence[Planner]):
        if not planners:
            raise ValueError("at least one planner is required")
        self._planners = tuple(planners)
        self._breakers = {p.name: CircuitBreaker() for p in planners}

    def decide(self, request: str, allowed: frozenset[str], timeout_s: float) -> Decision:
        correlation_id = str(uuid.uuid4())
        deadline = time.monotonic() + timeout_s
        for planner in self._planners:
            try:
                return retry_plan(planner, self._breakers[planner.name], request,
                                  allowed, deadline, correlation_id)
            except (TransientError, PermanentError) as exc:
                logger.error(f"planner rejected: {type(exc).__name__}",
                             extra={"correlation_id": correlation_id,
                                    "planner": planner.name,
                                    "state": self._breakers[planner.name].state})
        return Decision("answer", None,
                        "No approved automated action is currently available.",
                        "deterministic-no-action-v1", True)


class CallLedger:
    def __init__(self):
        self._entries: dict[str, tuple[str, ToolResult | None]] = {}
        self._lock = threading.Lock()

    def reserve(self, key: str, args_digest: str) -> ToolResult | None:
        with self._lock:
            prior = self._entries.get(key)
            if prior is None:
                self._entries[key] = (args_digest, None)
                return None
            if prior[0] != args_digest:
                raise PermanentError("idempotency key reused with different arguments")
            if prior[1] is None:
                raise CallInProgress("tool call already has an in-flight reservation")
            return prior[1]

    def commit(self, key: str, args_digest: str, result: ToolResult) -> None:
        with self._lock:
            prior = self._entries.get(key)
            if prior is None or prior[0] != args_digest:
                raise PermanentError("call was not reserved with matching arguments")
            self._entries[key] = (args_digest, result)


class RefundDriver:
    name = "refund-api-v2"

    def __init__(self):
        self._results: dict[str, ToolResult] = {}
        self._fail_after_first_commit = True
        self._lock = threading.Lock()

    def execute(self, args: dict[str, object], idempotency_key: str,
                timeout_s: float) -> ToolResult:
        if timeout_s <= 0:
            raise TimeoutError("tool deadline expired")
        with self._lock:
            existing = self._results.get(idempotency_key)
            if existing is not None:
                return ToolResult(existing.status, existing.external_id,
                                  existing.data, True)
            result = ToolResult("succeeded", f"refund-{uuid.uuid4()}",
                                {"invoice_id": args["invoice_id"],
                                 "amount_cents": args["amount_cents"]}, False)
            self._results[idempotency_key] = result
            if self._fail_after_first_commit:
                self._fail_after_first_commit = False
                raise AmbiguousCommit("connection lost after downstream commit")
            return result

    def reconcile(self, idempotency_key: str) -> ToolResult | None:
        with self._lock:
            result = self._results.get(idempotency_key)
            if result is None:
                return None
            return ToolResult(result.status, result.external_id, result.data, True)


class ToolGateway:
    def __init__(self, ledger: CallLedger, approval_secret: bytes,
                 authorized_approvers: frozenset[str], refund_driver: Driver):
        if not authorized_approvers:
            raise ValueError("at least one authorized approver is required")
        self._ledger = ledger
        self._secret = approval_secret
        self._authorized_approvers = authorized_approvers
        self._driver = refund_driver
        self._breaker = CircuitBreaker()

    @staticmethod
    def visible_tools(principal: Principal) -> frozenset[str]:
        return frozenset({"refund_invoice"}) if "billing_refunder" in principal.roles else frozenset()

    @staticmethod
    def _validate(call: ToolCall) -> None:
        if call.name != "refund_invoice":
            raise PermanentError("unknown tool")
        if set(call.arguments) != {"invoice_id", "amount_cents", "reason"}:
            raise PermanentError("arguments violate exact schema")
        invoice = call.arguments["invoice_id"]
        amount = call.arguments["amount_cents"]
        reason = call.arguments["reason"]
        if not isinstance(invoice, str) or not invoice.startswith("inv_"):
            raise PermanentError("invalid invoice ID")
        if type(amount) is not int or not 1 <= amount <= 100_000:
            raise PermanentError("refund amount is outside policy")
        if not isinstance(reason, str) or not 3 <= len(reason) <= 200:
            raise PermanentError("refund reason violates policy")

    def execute(self, principal: Principal, call: ToolCall, approval: Approval,
                timeout_s: float = 5.0) -> ToolResult:
        if call.name not in self.visible_tools(principal):
            raise PermanentError("principal is not authorized for this tool")
        self._validate(call)
        verify_approval(self._secret, call, approval)
        if approval.approver not in self._authorized_approvers:
            raise PermanentError("approval signer is not authorized")
        args_json = canonical_json(call.arguments)
        args_digest = sha256(args_json)
        key = sha256("|".join((principal.tenant_id, principal.principal_id,
                               call.call_id, call.name, args_json,
                               approval.approval_id)))
        prior = self._ledger.reserve(key, args_digest)
        if prior is not None:
            return ToolResult(prior.status, prior.external_id, prior.data, True)

        correlation_id = str(uuid.uuid4())
        deadline = time.monotonic() + timeout_s
        for attempt in range(1, 4):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransientError("tool deadline exhausted")
            self._breaker.before()
            try:
                result = self._driver.execute(call.arguments, key, min(remaining, 3.0))
                self._breaker.success()
            except AmbiguousCommit:
                # Reconciliation precedes retry for every unknown mutation outcome.
                result = self._driver.reconcile(key)
                if result is None:
                    self._breaker.failure()
                    raise TransientError("mutation status remains unknown")
                self._breaker.success()
            except (TimeoutError, ConnectionError, TransientError) as exc:
                self._breaker.failure()
                logger.warning("transient tool failure",
                               extra={"correlation_id": correlation_id,
                                      "tool": call.name, "attempt": attempt,
                                      "state": self._breaker.state})
                if attempt == 3:
                    raise TransientError("tool retry budget exhausted") from exc
                delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
                if delay >= deadline - time.monotonic():
                    raise TransientError("insufficient tool retry deadline") from exc
                time.sleep(delay)
                continue
            if result.status != "succeeded" or not result.external_id.startswith("refund-"):
                raise PermanentError("tool postcondition/receipt validation failed")
            self._ledger.commit(key, args_digest, result)
            logger.info("tool result committed",
                        extra={"correlation_id": correlation_id, "tool": call.name,
                               "attempt": attempt, "state": self._breaker.state})
            return result
        raise AssertionError("bounded tool retry did not terminate")


class DemoPlanner:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def plan(self, request: str, allowed_tools: Sequence[str], timeout_s: float) -> str:
        if timeout_s <= 0 or not self._available:
            raise TimeoutError("planner unavailable")
        if "refund_invoice" not in allowed_tools:
            return canonical_json({"kind": "answer", "message": "Approval role required."})
        return canonical_json({"kind": "call", "call_id": "call-001",
                               "name": "refund_invoice",
                               "arguments": {"invoice_id": "inv_1042",
                                             "amount_cents": 2500,
                                             "reason": "Duplicate charge"}})


def main() -> None:
    secret = b"demo-approval-secret-change-in-production"
    principal = Principal("user-7", "tenant-a", frozenset({"billing_refunder"}))
    gateway = ToolGateway(CallLedger(), secret, frozenset({"manager-3"}), RefundDriver())
    chain = PlannerChain([DemoPlanner("primary-region", False),
                          DemoPlanner("secondary-region", True)])
    decision = chain.decide("Refund duplicate invoice charge",
                            gateway.visible_tools(principal), timeout_s=3.0)
    if decision.call is None:
        print(canonical_json(asdict(decision)))
        return
    approval = sign_approval(secret, decision.call, approver="manager-3")
    result = gateway.execute(principal, decision.call, approval)
    print(canonical_json({"decision": asdict(decision), "result": asdict(result)}))


if __name__ == "__main__":
    main()
```

The in-memory ledger and demo driver make the example executable; production adapters must persist fenced reservations/results transactionally and call a downstream API that recognizes the same idempotency key. Approval keys and authorized signer policy belong in a KMS/HSM-backed identity service, planner/tool credentials are separate short-lived identities, and browser/code workers remain external isolated services rather than subprocesses inside this gateway.

## 6. Architectural System Design Scenarios

### Scenario 1 — Global service desk across APIs and a legacy browser

**Problem statement.** Design a service-desk agent for 20,000 employees at 250 tasks/second. It reads tickets/assets from typed APIs, proposes credential resets, updates cases, and uses a legacy HR web app only where no supported API exists. Read tasks require p99 ≤ 8 seconds; browser workflows require p95 ≤ 60 seconds. Every mutation needs user confirmation, RPO 0, exactly-once business effect, and a verifiable receipt. No generic admin credential may reach the model.

**Proposed architecture and technologies.** An OIDC gateway resolves employee/tenant identity. A policy service retrieves a small eligible toolset from a versioned registry. The model emits strict calls to a Go/Python tool gateway. Read adapters fan out concurrently to pinned ServiceNow/asset APIs. Mutations run as Temporal activities behind signed proposals, PostgreSQL idempotency/outbox records, short-lived OAuth audience tokens, and reconciliation. The HR fallback uses one non-persistent Playwright context per run in an egress-restricted Kubernetes browser pool; origin, active employee, form digest, and approval are rechecked before submit. Kafka distributes audit/adapter events; OTel traces the trajectory.

```text
┌──────────────┐ OIDC       ┌──────────────┐ eligible tools ┌──────────────┐
│ Employee UI  ├───────────►│ Policy/API   ├───────────────►│ Model        │
│ + approval   │◄──status───┤ admission    │                │ typed calls  │
└──────┬───────┘            └──────────────┘                └──────┬───────┘
       │ signed proposal                                            ▼
       │             ┌────────────────┐  activities       ┌──────────────┐
       └────────────►│ Temporal +     │◄──────────────────┤ Tool gateway │
                     │ Postgres ledger│                   │ RBAC/approve │
                     └───────┬────────┘                   └───┬──────┬───┘
                             │                                │      │
                     ┌───────▼───────┐                ┌───────▼─┐ ┌──▼────────┐
                     │ Kafka/OTel/   │                │ API     │ │Playwright │
                     │ WORM audit    │                │adapters │ │browser pool│
                     └───────────────┘                └────┬────┘ └─────┬─────┘
                                                          │ receipt     │ DOM receipt
                                                          └──────┬──────┘
                                                                 ▼
                                                          ┌──────────────┐
                                                          │ Result gate  │
                                                          └──────────────┘
```

At 250 task RPS, 2.0 model turns and 1.6 tool calls per task require about 500 model turns/s and 400 tool operations/s. If 4% use the browser and p95 duration is 60 seconds, `250×0.04×60 = 600` browser slots are required before reserve; per-origin caps protect the legacy backend. Independent reads have separate bulkheads from mutations and browser work.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Typed APIs first + isolated browser exception + Temporal** | Medium platform/browser cost | Fast reads; browser only for gap | High: adapters, workflow, browser pool | Strongest complete mediation, receipt and idempotency | High; browser backend remains bottleneck |
| Browser for every system | High browser/model-step cost | Slow serialized trajectories | Medium-high | Large session, phishing, UI-drift surface | Low due long-lived browser slots |
| Direct model-to-vendor SDKs | Low initial engineering | Fast happy path | Low initially | Unacceptable ambient credentials, weak approval/replay | Medium; failures become unsafe state |

**Decision rationale.** APIs win on typed contracts, latency, reconciliation, and scale; browser automation is retained only for the unsupported HR operation. Temporal plus the durable ledger closes crash/retry gaps, while approval-bound arguments and postcondition receipts prevent the model or a page from asserting authority or completion.

### Scenario 2 — Multi-tenant cited research and data-analysis agent

**Problem statement.** Design a platform processing 50 research jobs/second. Each job may issue up to four web searches, fetch ten documents, run Python over uploaded CSV/PDF/XLSX files, and return a cited report plus downloadable charts. Interactive jobs require p95 ≤ 30 seconds and p99 ≤ 90 seconds. Uploaded data is tenant-confidential, network access from code is denied, artifacts must survive ephemeral sandbox expiry, and monthly compute/search spend has a hard tenant budget.

**Proposed architecture and technologies.** The API scans and stores uploads by digest in tenant-encrypted object storage. A Temporal workflow calls a policy-controlled search/fetch service that canonicalizes URLs, blocks internal IP ranges, records source spans/digests, and prefers primary sources. A model produces a bounded analysis plan and Python. Firecracker/gVisor-style isolated workers mount inputs read-only, use pinned images/packages, run network-off under CPU/memory/process/wall quotas, and export only through an artifact scanning broker. A claim/evidence validator checks citations and deterministic calculations before PostgreSQL commits the report manifest. Search, model, sandbox, bytes, and artifacts have separate token buckets.

```text
┌──────────────┐ uploads/jobs ┌──────────────┐ workflow  ┌──────────────┐
│ Tenant UI    ├─────────────►│ API + scan   ├──────────►│ Temporal     │
│ downloads    │◄─signed URL──┤ + budgets    │           │ job          │
└──────────────┘              └──────┬───────┘           └───┬──────┬───┘
                                     │ encrypted inputs       │      │
                              ┌──────▼───────┐          ┌─────▼──┐ ┌─▼─────────┐
                              │ Object store │          │Search/ │ │ Model plan│
                              │ by digest    │          │fetch   │ │ + code    │
                              └──────▲───────┘          └────┬───┘ └────┬──────┘
                                     │ accepted artifacts     │ sources       │ code
                              ┌──────┴───────┐                └──────┬───────┘
                              │ Artifact     │                       ▼
                              │ scan/broker  │◄──────────────┌──────────────┐
                              └──────▲───────┘               │ MicroVM pool │
                                     │                       │ network-off  │
                                     │                       └──────┬───────┘
                                     │                              │ result digest
                                     │                       ┌──────▼───────┐
                                     └───────────────────────┤ Claim/result │
                                                             │ validator    │
                                                             └──────┬───────┘
                                                                    ▼
                                                             ┌──────────────┐
                                                             │ Report + audit│
                                                             └──────────────┘
```

At 50 jobs/s, four searches reserve 200 search calls/s and ten fetches reserve 500 fetches/s. With a 20-second p95 sandbox duration, Little’s Law requires `50×20 = 1,000` sandbox slots if every job executes code; routing deterministic arithmetic away from Python directly reduces that pool. Admission reserves maximum remaining search and sandbox spend, then refunds unused budget. Large source bodies and stdout stay in object storage; only cited spans and compact observations enter model context.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Controlled search + isolated code + artifact broker** | Medium-high search/compute; explicit budgets | Parallel fetch; bounded sandbox path | High: egress policy, images, scanning, validators | Strong tenant/network/artifact isolation and citation custody | High with large sandbox fleet |
| Provider-hosted search + interpreter only | Usage-priced, lower platform ops | Good cold-start profile; provider-dependent | Low-medium | Provider boundary/retention and fixed environment | Quota/provider constrained |
| General browser + local shell worker | Low initial platform work | Many serialized steps; host contention | Medium | Unacceptable session, host credential and exfiltration surface | Low and operationally unsafe |

**Decision rationale.** The controlled search and isolated-code design wins because confidential uploads and reproducible artifacts require stronger identity separation and custody than a local shell, while 50 jobs/s requires parallel structured fetch rather than browser navigation. The higher platform cost buys enforceable network-off computation, durable artifacts, source provenance, spend admission, and evaluator-derived completion.

## Interview Review

1. **What does function calling guarantee?** A typed proposal shape on supported strict schemas; the application still validates, authorizes, approves, executes, reconciles, and verifies.
2. **When are parallel calls safe?** For independent reads or commutative effects with per-call failure handling; not for dependent or approval-bearing mutations.
3. **Why prefer an API over a browser?** Typed contracts, lower latency, explicit IDs, idempotency, and reliable postconditions. Browser state is serialized and UI/auth drift is common.
4. **What makes code execution production-safe?** A narrow mode, strong isolation, resource and egress limits, no ambient credentials, immutable inputs/runtime, output scanning, and durable artifact export.
5. **What is exactly once?** A business property created by downstream idempotency or transactional deduplication plus reconciliation, not by a queue or workflow alone.
6. **What is the correct success metric?** Verified task/postcondition success per dollar with security, abstention, calls, latency, and artifacts—not schema accuracy or model self-report alone.

## Primary References

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAPI 3.2](https://spec.openapis.org/oas/latest.html)
- [HTTP semantics and idempotency](https://datatracker.ietf.org/doc/html/rfc9110)
- [OAuth 2.0 Security BCP](https://datatracker.ietf.org/doc/html/rfc9700)
- [Programmatic tool calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
- [BFCL](https://proceedings.mlr.press/v267/patil25a.html)
- [ToolSandbox](https://arxiv.org/abs/2408.04682)
- [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [Playwright browser contexts](https://playwright.dev/docs/browser-contexts)
- [OpenAI Code Interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter)
- [Kubernetes seccomp](https://kubernetes.io/docs/reference/node/seccomp/)
- [Temporal documentation](https://docs.temporal.io/)
- [OWASP excessive agency](https://genai.owasp.org/llmrisk2023-24/llm08-excessive-agency/)
