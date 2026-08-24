# 05 — Agent Frameworks

**Scope:** LangGraph, OpenAI Agents SDK, Google ADK, and CrewAI.  
**Study goal:** Select a framework by control, state, recovery, security, and operations semantics—not demo ergonomics or unsupported benchmark claims.

All four can call models and tools. The decisive questions are: who owns the next transition, what state is authoritative, where checkpoints occur, what resumes after failure, and how external effects are deduplicated and verified.

## 1. System Topology & Data Flow

### Reference topology

```text
                                DOMAIN CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ policy/tool registry │ prompt/model versions │ budgets/evals│
│ graph/schema versions │ deployment/rollback │ secrets │ retention/residency │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │ pinned invocation metadata
                                   ▼
                               FRAMEWORK LAYER
┌──────────────────┬──────────────────┬──────────────────┬────────────────────┐
│ LangGraph        │ OpenAI Agents SDK│ Google ADK 2.0   │ CrewAI            │
│ nodes/edges/state│ Agent/Runner loop│ Workflow/Runner  │ Flow + bounded Crew│
│ reducers/checkptr│ tools/handoffs   │ Events/services  │ tasks/roles/routes │
└─────────┬────────┴─────────┬────────┴─────────┬────────┴─────────┬──────────┘
          │                  │                  │                  │
          └──────────────────┴──────────┬───────┴──────────────────┘
                                       ▼
                              DOMAIN EXECUTION PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Policy/tool/MCP gateway │ APIs/search/browser/code │ human approval         │
│ schema/domain validation│ short-lived credentials │ postcondition verifier │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                            PERSISTENCE LAYER
┌──────────────────────────────────▼───────────────────────────────────────────┐
│ Domain run table │ effect intent/result ledger │ checkpoint/session backend│
│ outbox/queue     │ immutable artifacts/receipts │ memory/store by tenant    │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │ traces, counters, immutable audit
                                   ▼
                           TELEMETRY / OBSERVABILITY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OTel │ framework traces/events │ cost/quality/recovery metrics │ WORM/SIEM  │
└──────────────────────────────────────────────────────────────────────────────┘
```

Framework state is orchestration state. A payment provider, claim system, ticket database, or domain ledger remains authoritative for business effects. Framework roles, agent instructions, node names, and Flow routes are not authorization.

### Framework control models

| Framework | Primary abstraction | Who normally chooses the next step? | State center | Production path |
|---|---|---|---|---|
| LangGraph | graph/functional APIs, nodes, edges, typed state, reducers | application graph; conditional/model routing where chosen | per-thread checkpoints plus cross-thread Store | self-managed Agent Server or LangSmith Deployment |
| OpenAI Agents SDK | `Agent`, `Runner`, tools, handoffs, guardrails | Runner’s model/tool/handoff loop; code outside loop | run items/serializable `RunState`; optional Session history | application runtime; documented durable integrations for long work |
| Google ADK | agent/function/tool nodes, `Workflow`, `Runner`, `Event` | graph or code-driven workflow; collaborative agents in bounded nodes | Session events/state, MemoryService, ArtifactService | Agent Runtime, Cloud Run, GKE, or containers |
| CrewAI | Agents/Tasks/Crews plus event-driven Flows | Flow routes for control; Crew for autonomous collaboration | typed/dict Flow state and persistence; Crew memory/knowledge | self-hosted Python or CrewAI AMP |

### Request flow

1. An API gateway authenticates the user/workload, derives tenant/purpose/scopes, admits weighted work, creates the domain `run_id`, and pins framework, graph, model, prompt, tool, policy, reducer, and state-schema versions.
2. The domain run table commits the request and idempotency identity before a framework worker is scheduled. The framework receives server-derived identity metadata, never authority inferred from prompt text.
3. The selected framework advances its graph/Runner/Workflow/Flow. It projects the minimum model context from authoritative state and emits events, state deltas, or run items.
4. A proposed tool call leaves the framework through a common gateway. The gateway validates schema and business state, authorizes the actual resource, obtains exact-command approval, mints a short-lived credential, and records intent before dispatch.
5. API/browser/code workers return sanitized observations plus authoritative receipts. The effect ledger commits the result before the framework is allowed to continue.
6. Framework-specific persistence records the orchestration position: LangGraph checkpoint, Agents SDK supported `RunState`/session item, ADK event/state delta, or CrewAI Flow state/checkpoint.
7. A deterministic postcondition validator—not model final text—decides success. The domain run table commits terminal status and publishes an outbox status event.
8. A crash-restored worker acquires a fenced lease, validates pinned code/schema compatibility, restores framework state, reconciles any unknown external effects, and resumes. It never silently starts a new run after a restore miss.
9. OTel/framework spans capture metadata and correlations. Raw prompts/tool results live under separate access, retention, residency, and encryption controls; traces are not the transaction ledger.

## 2. Core Mechanics & Algorithms

### 2.1 LangGraph

LangGraph is a low-level orchestration runtime: nodes perform work, edges route, state schemas define shared state, and reducers merge updates ([overview](https://docs.langchain.com/oss/python/langgraph/overview), [graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api)). It deliberately leaves prompt and agent architecture choices to the application.

- `StateGraph` makes transition topology inspectable and property-testable.
- Checkpointers store thread/step snapshots; Store holds separately governed cross-thread data.
- Pending writes retain successful branch outputs when another node in the same super-step fails.
- An interrupt persists a pause, but resume restarts the interrupted node from its beginning; pre-interrupt effects must be idempotent.
- The functional API persists task results, yet an incomplete task can run again.

For graph `G=(V,E)`, topological scheduling is `O(|V|+|E|)`. A reducer joining `b` branch outputs is at least `O(b)` and must be deterministic under duplication and arrival-order changes. A last-value reducer is incorrect for concurrent mutations unless overwrite semantics are intentional.

**Fit:** strongest native emphasis on explicit topology, state, reducers, checkpoints, branches, and human interrupts.  
**Cost of control:** the team owns state schema, reducer correctness, graph evolution, side-effect discipline, and deployment choices.

### 2.2 OpenAI Agents SDK

An `Agent` packages instructions, model, tools, handoffs, guardrails, and output contract. `Runner` owns a bounded loop: model call, final output, handoff to a new active agent, or tool execution followed by another turn ([agents](https://openai.github.io/openai-agents-python/agents/), [runner](https://openai.github.io/openai-agents-python/running_agents/)).

- `Agent.as_tool()` lets a manager retain control and consume a bounded specialist result.
- A handoff changes the active agent and passes filtered conversation history.
- Sessions maintain conversation history; they cannot be mixed in the same run with the documented server continuation mechanisms.
- Tool approvals interrupt and serialize `RunState` for approve/reject/resume.
- `max_turns=None` disables that loop bound, so production needs independent time, token, tool, nested-call, and currency limits.
- Provider adapters are best-effort and can differ in tool, structured-output, multimodal, and usage behavior.

An `n`-turn serial loop has `O(n)` model/tool transitions. Nested agents-as-tools can hide another loop, so run-wide complexity is the sum of outer and nested calls, not the top-level `max_turns` alone.

**Fit:** shortest surface for OpenAI-first model/tool/handoff flows with integrated approval and tracing.  
**Boundary:** Sessions are not durable schedulers; long-running crash recovery uses an application workflow or documented integrations such as Temporal, Dapr, Restate, or DBOS.

### 2.3 Google ADK 2.0

ADK 2.0 Python/Go uses a graph-oriented Workflow Runtime in which agents, functions, and tools are nodes. `Runner` consumes an asynchronous `Event` stream, commits events/state changes through SessionService, and yields them to callers ([2.0 migration](https://adk.dev/2.0/), [runtime](https://adk.dev/runtime/event-loop/)).

- Graph workflows express branches, joins, loops, human input, agents, functions, and tools.
- Dynamic workflows retain code-driven control; collaborative workflows isolate branch context and return results to a parent.
- Session is chronological events/state; Memory is cross-session search; ArtifactService versions binary objects separately.
- Plugins run global lifecycle hooks before local callbacks for policy, telemetry, caching, and metrics.
- Persistent service implementations determine survival; in-memory services are development-only.
- Current documented graph limitations, including live-streaming and integration gaps, must be checked against the pinned release and language.

Event append/replay is `O(events since snapshot/index)` for reconstruction; parallel branches reduce critical path only when their isolated inputs and deterministic join semantics are correct. Blocking sync I/O can stall the async Runner.

**Fit:** strong for Google Cloud/Gemini-aligned systems using event/service state and mixed deterministic/collaborative workflows.  
**Migration risk:** ADK 2.0 changed events and extension points; test 1.x custom-agent logic, storage schemas, and language parity explicitly.

### 2.4 CrewAI

CrewAI separates role-based autonomous **Crews** from event-driven **Flows**. Production guidance places a Flow around bounded Crew work ([Agents](https://docs.crewai.com/core-concepts/Agents), [production architecture](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.12.2/en/concepts/production-architecture.mdx)).

- Agents/Tasks/Crews provide role, goal, tool, delegation, iteration, time, rate, callback, and model controls.
- Flows provide `start/listen/router` control and typed or dictionary state.
- `@persist` can save every or selected methods; the documented default store is SQLite and is unsuitable for replicated production workers.
- Restore/checkpoint behavior is state-ID driven; a restore miss can fall back to new/default behavior, which a domain run table must reject.
- `@human_feedback` may use an LLM to classify free text into routing outcomes. High-impact approval must retain the raw human decision and bind a structured signature to exact arguments.

For `a` agents averaging `i` iterations and `t` delegated tasks, model-call work can grow on the order of `O(a·i+t)` before retries and nested Crew calls. Per-agent `max_iter` is not a Flow-wide budget.

**Fit:** highest-level role/team vocabulary and rapid research/content collaboration.  
**Production discipline:** typed Flow first, bounded Crews for analysis/proposal, explicit global budgets, intentional persistence boundaries, and a framework-independent effect ledger.

### 2.5 State and convergence comparison

| Concern | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---|---|---|---|---|
| Conversation continuity | thread/messages | Session or provider continuation | Session events/state | Crew memory or Flow state |
| Step position | graph checkpoint | run items / supported `RunState` | Event/state delta and resume facilities | Flow persistence/checkpoint |
| Cross-run memory | Store | application/session service | MemoryService | memory/knowledge integration |
| Large artifacts | external reference | files/application storage | ArtifactService | application/tool storage |
| Parallel merge | explicit reducers | code-owned outside loop | graph join/collaboration parent | Flow/application merge |
| Durable long waits | native with durable checkpointer | external durable runtime for robust long work | persistent services/runtime-dependent | production persistence around Flow |

**Portable invariants**

- A framework run maps to exactly one authorized domain `run_id`; a restore miss never starts fresh work silently.
- Terminal domain state never returns active except through an explicit child/retry run.
- Every effect has durable intent, stable idempotency key, result/unknown status, and external receipt.
- Replay reuses committed observations and reconciles unknown effects before re-execution.
- State schema, graph/reducer, framework, model, prompt, tool, and policy versions are pinned.
- Completion requires domain postconditions; framework “finished,” agent final output, or Flow completion is not sufficient.
- Turns, nested calls, delegation depth/width, tools, tokens, cost, time, retries, and no-progress are bounded independently, guaranteeing runtime termination in a typed state.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

Framework libraries do not remove inference, tool, state, trace, worker, or human-review cost:

```text
C_1000 = Σ(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + tool/search/sandbox + checkpoint/artifact I/O
       + queue/worker/control plane + trace retention + human repair

cost_per_success = total production-shaped cost / verified successes
```

**Point-in-time assumptions, 2026-08-21:** across 1,000 identical production-shaped runs, all candidate implementations generate 12M uncached input, 20M cached-prefix reads, 80,000 cache-write tokens, and 3M output. No tool/infrastructure cost is included. This is an architecture-controlled comparison, not observed framework performance. Rates come from the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No-cache input + output / 1K | Cached trajectory / 1K | Saving |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(32M×$5)+(3M×$30)` = **$250.00** | `$60+$10+$0.50+$90` = **$160.50** | 35.8% |
| `gpt-5.6-terra` | `(32M×$2)+(3M×$12)` = **$100.00** | `$24+$4+$0.20+$36` = **$64.20** | 35.8% |
| `gpt-5.6-luna` | `(32M×$0.20)+(3M×$1.20)` = **$10.00** | `$2.40+$0.40+$0.02+$3.60` = **$6.42** | 35.8% |

Framework choice changes trajectory multipliers, not token prices. As a worked `terra` comparison, an intentionally fixed three-call architecture with 2,500 uncached input and 500 output tokens/call costs `(7.5M×$2)+(1.5M×$12) = $33/1K runs`; an eight-call role/team design with the same per-call shape costs `(20M×$2)+(4M×$12) = $88/1K`. This is a call-count illustration, **not** evidence that any named framework causes either trajectory.

Checkpoint and trace economics must be explicit. Eight 25-KiB checkpoints and twelve 5-KiB trace events per run produce roughly 254 MiB of raw data per 1,000 runs before replication/indexing; apply the chosen database/object/telemetry retention price. Count history repeated on handoffs, isolated branch context, event/checkpoint duplication, nested specialists, retries, and failed work. Report cost per verified success.

### 3.2 Latency bake-off targets

No owner publishes an audited apples-to-apples runtime benchmark with identical model, tools, state backend, region, instance, workflow, and versions. The following are **shared application targets** for a controlled bake-off, not framework performance claims:

```text
serial stage = Σ(model + tool + hook/plugin/callback + persistence)
parallel stage = dispatch + max(branch latency) + join/reducer
end-to-end = queue + critical path + approval wait + recovery
```

| Workload | p50 | p95 | p99 | Required tail test |
|---|---:|---:|---:|---|
| Short interactive tool loop | ≤ 1.5 s | ≤ 5 s | ≤ 10 s | Slow consumer/disconnect, tool timeout, trace disabled/enabled. |
| Durable four-node graph | ≤ 3 s | ≤ 12 s | ≤ 25 s | Checkpoint backend latency, crash/resume, cold worker. |
| Four independent agent branches | ≤ 4 s | ≤ 15 s | ≤ 35 s | Straggler/join timeout, branch failure, merge conflict. |
| Machine part of approval flow | ≤ 2.5 s | ≤ 8 s | ≤ 15 s | Durable pause/resume; human wait reported separately. |

Measure first useful event and verified terminal time separately. Pin streaming mode: ADK graph workflow limitations, Runner event schemas, LangGraph streams, and CrewAI streams are not equivalent. Include hook/plugin/guardrail time, persistence, trace ingestion, serialization, and recovery in the same load test.

ADK Arena’s 2026 preprint evaluates LLM-generated implementations across many framework APIs and uses generation effort as an API-usability proxy. It does not establish production runtime latency, cost, durability, or security for these four; do not use it as a ranking ([paper](https://arxiv.org/abs/2606.05548)).

### 3.3 Throughput and back-pressure

```text
model_calls/s      = admitted_runs/s × mean_model_calls
tool_calls/s       = admitted_runs/s × mean_tool_calls
checkpoint_writes/s= admitted_runs/s × mean_checkpoints
active_runs        = admitted_runs/s × mean_machine_duration_s
trace_bytes/s      = events/s × mean_redacted_event_bytes
```

At `100 runs/s`, five model calls, three tools, 12 checkpoints, and 8 seconds mean machine duration, plan for `500 model calls/s`, `300 tool calls/s`, `1,200 checkpoint writes/s`, and `800 active machine runs`. At 25 KiB/checkpoint, raw checkpoint ingress is about `30 MiB/s` before indexes and replication. A four-branch stage on 10 runs/s adds up to 40 concurrent branch starts/s.

Use weighted admission based on predicted model calls, uncached tokens, branch/delegation width, tool class, state writes, and trace bytes—not request count. Bound interactive, batch, approval, mutation, framework-worker, checkpoint, and telemetry queues separately. Slow stream consumers receive bounded buffers and resumable event IDs; they do not hold unbounded worker memory. One layer owns retries. Shed optional evaluators/planning/branches and verbose traces before mutation/status/approval capacity.

### 3.4 NFR and framework-selection scorecard

| NFR | Target / evidence | Trade-off |
|---|---|---|
| Outcome | verified task success, false-success rate, `pass^k`, human acceptance | More postcondition checks can increase incomplete outcomes. |
| Availability | 99.9% run plane; 99.99% status/approval/cancel | Durable status/effect services cost more than framework-only workers. |
| RPO | 0 domain run/effect/approval/receipt; ≤ 5 min aggregate metrics | Framework session/checkpoint alone is insufficient authority. |
| RTO | ≤ 15 min run/effect plane; ≤ 60 min analytics | Version registry, state migrations, and restore drills are required. |
| Performance | shared p50/p95/p99 targets, queue/cold/stream/recovery spans | A lower-level framework may require more application engineering. |
| Economics | cost per verified success, tokens, calls, I/O, traces, human repair | High-level autonomy can conceal nested call multipliers. |
| State | restore correctness, concurrent conflict rate, checkpoint growth, migration success | More frequent checkpoints improve RPO but add I/O/latency. |
| Security | zero unauthorized effects/cross-tenant state; injection and approval tests | Narrow tools and complete mediation reduce convenience. |
| Operability | deploy/rollback, stuck-run repair, alert coverage, version upgrade bake-off | Managed planes reduce toil but add cost/coupling. |

Run the same workload with pinned model/tool/state/region/instances, and include a framework upgrade in the bake-off. Library license, hosted control plane, model provider, trace platform, and support are separate procurement decisions.

## 4. Distributed Resilience & Security

### 4.1 Recovery boundaries by framework

**LangGraph.** Checkpoints persist super-step state and successful pending writes. Replay from a checkpoint can re-execute downstream nodes, and incomplete tasks can run again. Put non-idempotent effects behind durable domain intent and destination reconciliation. Authorize server-generated `thread_id` ownership and test reducer duplication/order.

**OpenAI Agents SDK.** Session preserves conversation history; serialized `RunState` supports approval interruption. Neither is a general job queue/transaction log. Place long work inside a documented durable runtime, allow one owner to advance a run, and make tools idempotent.

**Google ADK.** Persistent SessionService determines process survival; resume can skip completed workflow tasks and restart incomplete work. Rewind does not atomically restore external dependencies or all app/user state/artifact/event updates. Treat it as session/debug history, not distributed rollback.

**CrewAI.** Flow persistence/checkpoints restore state by ID. Validate the domain run exists and the expected framework state was loaded; fail closed on restore miss. Replace default local SQLite with a production database and persist at intentional committed transitions.

### 4.2 Common durable envelope

```text
┌──────────────┐ admit/CAS ┌──────────────┐ lease      ┌──────────────┐
│ API + domain ├──────────►│ Durable queue├───────────►│ Framework    │
│ run table    │◄─status───┤ /workflow    │            │ worker       │
└──────┬───────┘           └──────────────┘            └───┬──────┬───┘
       │                                                    │      │
       │                                           checkpoint│      │ command
       ▼                                                    ▼      ▼
┌──────────────┐ outbox      ┌──────────────┐       ┌──────────────┐
│ Effect ledger├────────────►│ Tool gateway ├──────►│ Business API │
│ intent/result│◄──receipt───┤ authz/approve│       │ idempotent   │
└──────┬───────┘             └──────────────┘       └──────────────┘
       │
       └────────► artifacts/WORM/OTel/status webhooks
```

Use optimistic state versions, one fenced executor per run, terminal-state invariants, bounded retries owned by one layer, and DLQ/manual-repair states. For mutations: commit canonical command/actor/policy/plan version/key, outbox dispatch, destination deduplication, receipt commit, and reconciliation after ambiguity. Drain or version-pin in-flight work during deployment; never deserialize old state under new semantics implicitly.

### 4.3 Failure taxonomy and degradation

| Failure | Framework manifestation | Recovery |
|---|---|---|
| Session mistaken for durability | history survives but job/effect disappears | durable queue/workflow and effect ledger |
| Duplicate effect on resume | replay/restarted task repeats mutation | stable key, destination lookup, compensation |
| Retry amplification | framework + SDK + mesh all retry | one owner, aggregate budget, jitter/breaker |
| State schema skew | old checkpoint/event/Flow state fails under upgrade | pin in-flight code; migration reader/writer and replay test |
| Fan-out/delegation explosion | branches, handoffs, Crews, loops multiply calls | global turns/depth/width/tool/cost/time bounds |
| Restore miss | new default state starts duplicate process | verify domain run + exact checkpoint ID; fail closed |
| Parallel merge conflict | reducer/join overwrites state | namespaced branch state, deterministic conflict-aware merge |
| Poison state/event | malformed/injected restore repeatedly crashes | durable attempts, quarantine/DLQ, digest, migration/manual repair |
| Provider adapter drift | tools/usage/structured output changes | exact contract suite and canary per adapter/model |
| Trace sink outage | worker blocks or loses audit assumptions | bounded async telemetry; authority in domain ledger |

Breakers are keyed by model/provider, tool, state backend, and telemetry sink. State/identity/policy/effect-ledger outages fail closed for mutations. Optional trace export degrades to bounded local metadata buffering; an immutable audit event still commits. Degrade by removing optional evaluator/planning/parallel work, selecting a pinned deterministic read workflow, converting mutations to drafts, queueing with status URL, then returning a resumable failure. Do not switch framework mid-run; create a child/migration run with lineage.

### 4.4 Zero-Trust MCP and framework-specific security

```text
┌──────────────┐ proposal ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ Framework    ├─────────►│ Policy/tool/MCP├───────────►│ MCP server / │
│ node/agent   │          │ gateway        │            │ adapter      │
└──────┬───────┘          └───────┬────────┘            └──────┬───────┘
       │ untrusted state/event     │ trusted identity            │ scoped token
       ▼                           ▼                             ▼
┌──────────────┐           ┌──────────────┐              ┌──────────────┐
│ Context      │◄──────────┤ Domain ledger│              │ API/browser/ │
│ projection   │           │ + approvals  │              │ code sandbox │
└──────────────┘           └──────────────┘              └──────────────┘
```

- **LangGraph:** protect standalone servers at an API gateway/service mesh; some development servers have no auth by default. Authorize thread/run IDs, namespace stores, and avoid unsafe serializers for untrusted state.
- **OpenAI Agents SDK:** use blocking admission before effectful work, tool guardrails per custom tool, and downstream authorization. Input/output guardrails do not automatically mediate all internal tools/handoffs.
- **ADK:** global Plugins are cross-cutting hooks, but downstream IAM remains final. Put tenant/subject in trusted invocation metadata; keep access/refresh tokens out of model-editable Session state and use secret/auth services.
- **CrewAI:** open-source deployments own identity, tenancy, secrets, audit, and isolation. `allow_delegation`, roles, and backstories are orchestration—not security. Raw high-impact human approval must not be replaced by an LLM-classified route.

For all four, authenticate the actor/workload and MCP server, allowlist capabilities/egress, expose minimum tools per node, re-authorize concrete `(actor, action, resource, conditions)`, bind approval to canonical arguments, and mint a short-lived audience token. Treat user, retrieval, tool, memory, peer-agent, checkpoint, and handoff content as untrusted. Sandbox browser/code tools and red-team poisoned restores and inter-agent context.

### 4.5 PII, supply chain, and audit

PII detection/redaction covers framework state, messages/events, tool arguments/results, artifacts, and traces before model/telemetry use. Combine schema-aware rules with NER, store reversible token maps in a segregated vault, and record detector/action/version. Encrypt state/artifacts with tenant keys and apply retention/deletion per data class.

Pin framework and adapter versions, generate SBOMs, scan/sign images, verify licenses, and test serialized-state migration before rollout. Own domain schemas, tool/auth/idempotency protocols, run/effect ledger, eval corpus, version registry, artifacts, and postconditions outside framework APIs. This limits migration cost without pretending graphs are portable.

Audit records include `run_id/trace_id`, tenant pseudonym, framework/version, graph/agent/Flow/node, state/event/schema version, model/prompt/tool/policy versions, command digest, policy/approval/key, receipt/result digest, parent event, tokens/cost, and timing. Hash-chain/sign WORM batches and log access. Framework traces aid debugging; raw content capture is separately authorized and never substitutes for the effect ledger.

## 5. Production Enterprise Code

This Python 3.11 standard-library program is a framework-neutral durable envelope. A real adapter can invoke LangGraph, OpenAI Agents SDK, ADK, or CrewAI while domain state, budgets, retries, model fallback, and verification stay portable. The executable demo provides CAS transitions, append-only events, full-jitter retry, closed/open/half-open breakers, a primary/secondary model chain inside the adapter, structured correlation logs, source verification, and deterministic `NEEDS_REVIEW` degradation. Run with `python framework_envelope.py`.

```python
from __future__ import annotations

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


class PermanentError(RuntimeError):
    """Contract, state, or policy failure."""


class CircuitOpen(TransientError):
    """Dependency fails fast during the recovery interval."""


class Status(Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


TERMINAL = {Status.SUCCEEDED, Status.NEEDS_REVIEW, Status.FAILED}


@dataclass(frozen=True)
class ModelResult:
    raw: str
    input_tokens: int
    output_tokens: int
    cost_micros: int


@dataclass(frozen=True)
class Proposal:
    summary: str
    source_ids: tuple[str, ...]
    model: str

    @classmethod
    def parse(cls, raw: str, model: str) -> "Proposal":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("model returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"summary", "source_ids"}:
            raise PermanentError("proposal violates exact schema")
        if not isinstance(value["summary"], str) or not value["summary"].strip():
            raise PermanentError("summary must be non-empty text")
        sources = value["source_ids"]
        if not isinstance(sources, list) or not sources or any(
            not isinstance(item, str) for item in sources
        ):
            raise PermanentError("source_ids must be a non-empty string list")
        return cls(value["summary"].strip(), tuple(sources), model)


@dataclass
class DomainRun:
    run_id: str
    trace_id: str
    tenant_id: str
    framework: str
    framework_version: str
    state_schema_version: int
    status: Status
    state_version: int
    model_attempts: int
    max_model_attempts: int
    cost_micros: int
    max_cost_micros: int
    evidence_ids: tuple[str, ...]
    proposal: Proposal | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for field in ("trace_id", "run_id", "framework", "model", "attempt", "status"):
            if hasattr(record, field):
                value[field] = getattr(record, field)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("framework_envelope")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class CircuitBreaker:
    def __init__(self, threshold: int = 3, recovery_s: float = 10.0):
        if threshold < 1 or recovery_s <= 0:
            raise ValueError("invalid breaker configuration")
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
                    raise CircuitOpen("circuit is open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe:
                    raise CircuitOpen("half-open probe already running")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probe = False
            self._state = "closed"

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


class DomainStore:
    def __init__(self):
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def transition(self, run: DomainRun, expected: int, status: Status,
                   event_type: str, payload: dict[str, object]) -> None:
        with self._lock:
            if run.state_version != expected:
                raise PermanentError("optimistic state-version conflict")
            if run.status in TERMINAL:
                raise PermanentError("terminal run cannot transition")
            run.status = status
            run.state_version += 1
            self._events.append({"event_id": str(uuid.uuid4()),
                                 "run_id": run.run_id,
                                 "version": run.state_version,
                                 "type": event_type,
                                 "payload": payload})

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(self._events)


class Model(Protocol):
    name: str

    def generate(self, request: str, timeout_s: float) -> ModelResult: ...


class FrameworkAdapter(Protocol):
    name: str
    version: str

    def execute(self, run: DomainRun, request: str, deadline: float) -> Proposal | None: ...


class ResilientModelChain:
    def __init__(self, models: Sequence[Model]):
        if not models:
            raise ValueError("at least one model is required")
        self._models = tuple(models)
        self._breakers = {model.name: CircuitBreaker() for model in models}

    def generate(self, run: DomainRun, request: str, deadline: float) -> Proposal | None:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 4):
                if run.model_attempts >= run.max_model_attempts:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0 or run.cost_micros >= run.max_cost_micros:
                    return None
                run.model_attempts += 1
                try:
                    breaker.before()
                    result = model.generate(request, min(remaining, 5.0))
                    breaker.success()
                    proposal = Proposal.parse(result.raw, model.name)
                except CircuitOpen:
                    break
                except PermanentError:
                    break
                except (TimeoutError, ConnectionError, TransientError) as exc:
                    breaker.failure()
                    logger.warning("transient model failure",
                                   extra={"trace_id": run.trace_id,
                                          "run_id": run.run_id,
                                          "framework": run.framework,
                                          "model": model.name,
                                          "attempt": attempt})
                    if attempt == 3:
                        break
                    delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
                    if delay >= deadline - time.monotonic():
                        return None
                    time.sleep(delay)
                    continue
                run.cost_micros += result.cost_micros
                if run.cost_micros > run.max_cost_micros:
                    return None
                return proposal
        return None


class PortableAnalysisAdapter:
    """Framework-node adapter; swap its invocation body for a pinned SDK call."""

    def __init__(self, name: str, version: str, models: ResilientModelChain):
        self.name = name
        self.version = version
        self._models = models

    def execute(self, run: DomainRun, request: str, deadline: float) -> Proposal | None:
        context = json.dumps({"request": request,
                              "allowed_sources": list(run.evidence_ids)},
                             separators=(",", ":"), sort_keys=True)
        return self._models.generate(run, context, deadline)


class DurableEnvelope:
    def __init__(self, store: DomainStore, adapter: FrameworkAdapter):
        self._store = store
        self._adapter = adapter

    def run(self, run: DomainRun, request: str, timeout_s: float) -> DomainRun:
        if run.status is not Status.CREATED:
            raise PermanentError("run must begin in CREATED")
        if (run.framework, run.framework_version) != (
            self._adapter.name, self._adapter.version
        ):
            raise PermanentError("pinned framework adapter mismatch")
        self._store.transition(run, run.state_version, Status.RUNNING,
                               "RUN_STARTED",
                               {"framework": run.framework,
                                "version": run.framework_version,
                                "schema": run.state_schema_version})
        deadline = time.monotonic() + timeout_s
        proposal = self._adapter.execute(run, request, deadline)
        if proposal is None:
            self._store.transition(run, run.state_version, Status.NEEDS_REVIEW,
                                   "RUN_DEGRADED",
                                   {"reason": "model_budget_or_dependency"})
            return run
        if not set(proposal.source_ids).issubset(run.evidence_ids):
            self._store.transition(run, run.state_version, Status.NEEDS_REVIEW,
                                   "RUN_DEGRADED",
                                   {"reason": "unverified_source"})
            return run
        run.proposal = proposal
        self._store.transition(run, run.state_version, Status.SUCCEEDED,
                               "RUN_VERIFIED",
                               {"source_ids": list(proposal.source_ids),
                                "model": proposal.model})
        logger.info("run terminal",
                    extra={"trace_id": run.trace_id, "run_id": run.run_id,
                           "framework": run.framework,
                           "status": run.status.value})
        return run


class DemoModel:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def generate(self, request: str, timeout_s: float) -> ModelResult:
        if timeout_s <= 0 or not self._available:
            raise TimeoutError("model unavailable")
        raw = json.dumps({"summary": "Claim requires documented manual review.",
                          "source_ids": ["claim-42"]})
        return ModelResult(raw, input_tokens=400, output_tokens=80,
                           cost_micros=1_200)


def main() -> None:
    adapter = PortableAnalysisAdapter(
        "langgraph", "1.0-pinned",
        ResilientModelChain([DemoModel("primary-region", False),
                             DemoModel("secondary-region", True)]),
    )
    run = DomainRun(str(uuid.uuid4()), str(uuid.uuid4()), "tenant-a",
                    "langgraph", "1.0-pinned", 3, Status.CREATED, 0,
                    0, 8, 0, 20_000, ("claim-42",))
    store = DomainStore()
    result = DurableEnvelope(store, adapter).run(
        run, "Assess claim 42", timeout_s=3.0
    )
    print(json.dumps({"run": asdict(result), "events": store.events},
                     separators=(",", ":"), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
```

Adapter mapping: invoke a compiled LangGraph graph with a server-authorized `thread_id`; invoke OpenAI `Runner` inside a durable activity and serialize only supported approval state; consume ADK `Runner` events through persistent services; or launch a typed CrewAI Flow only after confirming its domain run/checkpoint ID. Keep retries in one layer. The sample degrades to `NEEDS_REVIEW` when both models fail, budget is exhausted, or citations are outside the authorized evidence set; it never switches framework mid-run.

## 6. Architectural System Design Scenarios

### Scenario 1 — Regulated claims adjudication workflow

**Problem statement.** Design a claims system handling 300 cases/second. Its state machine is known: authenticate, gather evidence, investigate anomalies, calculate policy eligibility, obtain exact-command approval for high-value claims, execute payment, verify ledger state, and notify. Machine p99 must be ≤ 20 seconds excluding human wait; RPO is 0 for state/approval/payment; recovery RTO is 15 minutes; in-flight workflows may last 90 days; every effect needs seven-year custody.

**Proposed architecture and choice.** Select LangGraph with PostgreSQL-backed checkpoints for explicit nodes, reducers, interrupts, and inspectable transition topology, deployed in a non-scale-to-zero worker service. Keep the claim/payment domain run and effect ledger in PostgreSQL outside graph state. Temporal or a durable queue owns long scheduling/leases if the chosen LangGraph deployment does not. Investigation is an LLM node; eligibility, approval binding, execution, compensation, and verification are deterministic. MCP/tool calls pass through a zero-trust gateway. Large evidence stays in encrypted object storage by digest; WORM stores audit lineage.

```text
┌──────────────┐ OIDC       ┌──────────────┐ schedule   ┌──────────────┐
│ Claimant/ops ├───────────►│ Domain API + ├───────────►│ Durable queue│
│ approver     │◄──status───┤ run/effect DB│            │ /lease       │
└──────────────┘            └──────┬───────┘            └──────┬───────┘
                                    │                            ▼
                           ┌────────▼───────┐             ┌──────────────┐
                           │ Object/WORM    │             │ LangGraph    │
                           │ evidence/audit │             │ checkpoints  │
                           └────────────────┘             └───┬──────┬───┘
                                                             │      │
                                                    investigate│      │ deterministic
                                                             ▼      ▼
                                                      ┌──────────┐ ┌──────────────┐
                                                      │ LLM node │ │ Policy +     │
                                                      │ read-only│ │ approval     │
                                                      └────┬─────┘ └──────┬───────┘
                                                           │              │ intent/key
                                                           └──────┬───────┘
                                                                  ▼
                                                           ┌──────────────┐
                                                           │ Tool gateway │
                                                           │ payment/check│
                                                           └──────────────┘
```

At 300 cases/s, if 70% use four graph nodes and 30% anomaly cases use eight, average node rate is `300×(0.7×4+0.3×8)=1,560 node executions/s`. At six checkpoints/case, the store must sustain 1,800 writes/s plus replay/headroom. Approval waits persist without occupying workers. Mutation/status/checkpoint pools are isolated from optional investigation/evaluation work.

**Trade-off evaluation.**

| Candidate | Cost | Latency | Ops complexity | Security/durability | Scalability ceiling |
|---|---|---|---|---|---|
| **LangGraph + durable domain envelope** | Medium; explicit checkpoints/storage | Predictable graph; LLM only anomaly node | High: reducers, migrations, runtime | Strong explicit state/interrupt/replay control | High with partitioned workers/store |
| OpenAI Agents SDK inside Temporal | Medium; simpler model nodes plus workflow | Good, extra integration boundary | High: two runtimes/state mapping | Strong when Session is not mistaken for durability | High with durable scheduler |
| Google ADK 2.0 on GKE | Medium; event/service storage | Good async path; verify graph constraints | High migration/service design | Strong with persistent services and IAM | High in Google-aligned stack |

**Decision rationale.** LangGraph wins because explicit topology, reducers, checkpoint boundaries, and interrupts match the known regulated state machine. This is not a claim that it is faster: the decision follows control/recovery fit. The separate domain envelope prevents checkpoint semantics from becoming payment semantics and keeps migration options open.

### Scenario 2 — OpenAI-first specialist customer support

**Problem statement.** Design an interactive support system at 1,000 conversations/second with triage, billing, technical, and retention specialists; file/web retrieval; tool approvals; p95 ≤ 6 seconds and p99 ≤ 15 seconds for ordinary turns; no cross-tenant history; and resumable approval decisions. Ninety-five percent of turns finish within one request/worker, while the remainder may become long-running escalations.

**Proposed architecture and choice.** Select OpenAI Agents SDK for the short path. Use handoff when a specialist should own the conversation and `Agent.as_tool()` when triage must synthesize. Use one server-authorized database Session mechanism, blocking admission, per-tool guardrails, `max_turns`, and an outer token/time/cost budget. Serialize supported `RunState` for approval. The 5% long-running cases become child runs in Temporal with lineage; they do not rely on Session as a scheduler. Tool execution uses the same framework-independent authorization/effect gateway and verified postconditions.

```text
┌──────────────┐ tenant auth ┌──────────────┐ short path ┌──────────────┐
│ Support UI   ├────────────►│ API/session  ├───────────►│ Agents SDK   │
│ approval     │◄─stream─────┤ + budgets    │            │ triage Runner│
└──────────────┘             └──────┬───────┘            └───┬──────┬───┘
                                    │                         │      │
                                    │                 handoff │      │ agent-as-tool
                                    │                         ▼      ▼
                                    │                  ┌────────┐ ┌──────────┐
                                    │                  │Billing/│ │ Retrieval│
                                    │                  │Tech    │ │specialist│
                                    │                  └────┬───┘ └────┬─────┘
                                    │                       └────┬──────┘
                             long 5%│                            ▼
                                    ▼                     ┌──────────────┐
                             ┌──────────────┐              │ Tool gateway │
                             │ Temporal child│◄────────────┤ approve/verify│
                             │ run/effect DB │              └──────────────┘
                             └──────────────┘
```

At 1,000 conversations/s and 2.5 mean top-level model calls, base demand is 2,500 model calls/s. If 20% invoke one specialist with two internal calls, add 400 calls/s. Capacity tests must expose nested calls, not report only Runner invocations. The 50 long-running cases/s use separate queues and quotas so durable escalations cannot starve interactive sessions.

**Trade-off evaluation.**

| Candidate | Cost | Latency | Ops complexity | Security/durability | Scalability ceiling |
|---|---|---|---|---|---|
| **OpenAI Agents SDK + Temporal only for long path** | Low-medium short-path overhead; nested calls visible | Best abstraction fit for tool/handoff loop | Medium: Session plus child-run boundary | Strong with blocking/tool guards and external ledger | High, provider/quota dependent |
| LangGraph | Similar model cost; more graph code | Competitive only after identical test | High for simple conversational route | Strong checkpoints/control | High; more control than short path needs |
| CrewAI | Potentially more role/delegation calls | Variable collaboration tail | Medium | Flow-first can control, but role semantics add little here | Medium-high with strict global bounds |

**Decision rationale.** OpenAI Agents SDK wins on abstraction fit for short OpenAI-first specialist routing, integrated approvals, and traces—not on a nonexistent universal benchmark. Temporal is introduced only where work outlives the request, preserving simple latency for 95% while supplying real durability for the tail. The domain gateway prevents handoffs from broadening authority.

## Interview Review

1. **Which framework is best?** None universally; select by control topology, state/replay semantics, provider/cloud alignment, security boundary, and measured workload fit.
2. **What is the difference between session and durability?** Session preserves conversational state; durable execution schedules/replays work and reconciles effects after failure.
3. **When does LangGraph fit?** When explicit graph topology, state reducers, branches, checkpoints, and interrupts are first-class requirements.
4. **When does OpenAI Agents SDK fit?** For an OpenAI-first model/tool/handoff loop, with external durable scheduling for long work.
5. **When do ADK and CrewAI fit?** ADK for graph/event/service workflows and Google alignment; CrewAI for role-oriented autonomous pockets inside typed Flows.
6. **How should frameworks be benchmarked?** Same pinned model, tools, backend, deployment, state, tracing, dataset, failure injection, verified outcome, p95/p99, and cost per success.

## Primary References

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenAI Agents SDK runner](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK multi-agent patterns](https://openai.github.io/openai-agents-python/multi_agent/)
- [Google ADK 2.0](https://adk.dev/2.0/)
- [Google ADK workflows](https://adk.dev/workflows/)
- [Google ADK event loop](https://adk.dev/runtime/event-loop/)
- [CrewAI Agents](https://docs.crewai.com/core-concepts/Agents)
- [CrewAI Flow state](https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx)
- [ADK Arena preprint](https://arxiv.org/abs/2606.05548)
- [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
