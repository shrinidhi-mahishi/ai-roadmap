# Research: Production — Docker, Kubernetes, APIs, Queues, Scaling, and Reliability

**Date researched**: 2026-08-21  
**Sources consulted**: 50

Putting an agentic or LLM system into production is not synonymous with putting a container on Kubernetes. Production is a chain of explicit contracts:

- an **artifact contract** says exactly what code, model adapter, libraries, configuration, provenance, and vulnerabilities are being deployed;
- an **admission contract** decides whether a request may consume tokens, tools, money, data, and scarce accelerator time;
- an **execution contract** distinguishes synchronous inference from durable asynchronous work and replayable batch work;
- an **ownership contract** says who owns a queued job, when ownership expires, and what makes retry safe;
- a **capacity contract** connects demand and service objectives to pod, node, accelerator, model, and provider capacity;
- a **release contract** defines health, progressive exposure, automated gates, rollback, and schema compatibility;
- a **recovery contract** defines SLO, recovery point objective (RPO), recovery time objective (RTO), backups, failover, and tested restoration.

This note treats Docker, Kubernetes, APIs, queues, scaling, and reliability as parts of those contracts. They are separable: a service may run correctly without Kubernetes, and a Kubernetes Deployment can still lose work, amplify retries, cross tenant boundaries, or fail its latency SLO.

## 1. System Topology & Mechanics

### 1.1 Separate the three workload paths

```text
                                    control plane
                    artifact registry, policy, releases, schemas,
                    model routing, autoscaling, SLOs, audit, DR
                                          |
 client / agent -- edge/WAF -- API gateway -- authn/authz/quota/admission
                                          |
                  +-----------------------+------------------------+
                  |                        |                        |
          synchronous path         durable agent path        offline batch
         deadline + streaming      accept -> job ID/event      manifest/shards
                  |                        |                        |
         inference gateway           durable queue/log          job controller
          + model workers          workflow/orchestrator         batch workers
                  |                        |                        |
          bounded tool calls       checkpoint + activities       output commit
                  +-----------------------+------------------------+
                                          |
                    DB/object store/vector store/event history

 data plane: requests, tokens, messages, checkpoints, outputs
 control plane: desired state and policy; must not sit in every hot request path
```

**Synchronous inference** is deadline-bound and may stream. Queueing must be short and admission should reject or degrade before a request can no longer finish inside its deadline. An HTTP disconnect does not prove the provider stopped billing or the model stopped generating; cancellation must be propagated explicitly. Return a bounded error (`429` or `503` plus retry advice) instead of accumulating an unbounded in-memory queue `[inferred]`.

**Durable agent work** can outlive a connection, pod, deployment, or region. An accept operation returns a stable operation/job ID; the client polls, subscribes to events, or receives a callback. The execution record stores input version, policy, model/tool versions, state/checkpoints, attempts, budget consumed, output, and terminal reason. A queue transports work, but a durable workflow engine records control-flow history and timers. Temporal, for example, documents replay-based durable execution across process and infrastructure failure and separately advises that Activities be idempotent because they can re-execute [[30]](https://docs.temporal.io/) [[49]](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx).

**Offline batch** optimizes throughput, determinism, resumability, and cost rather than interactive tail latency. A manifest should define immutable inputs, shard identity, artifact/model/prompt versions, output schema, retry policy, and commit protocol. A Kubernetes Job manages pods until a specified completion condition, but application-level checkpoints and idempotent output commits remain necessary [[16]](https://kubernetes.io/docs/concepts/workloads/controllers/job/).

Do not silently convert one path into another. A synchronous request that times out while a server continues processing can create an invisible expensive job. A long agent loop held only in pod memory is not durable. An offline batch routed through the interactive deployment can exhaust its error budget and accelerator headroom.

### 1.2 Docker and OCI: build an immutable, attestable runtime artifact

The OCI image specification packages a content-addressed manifest, configuration, and filesystem layers, while the OCI runtime specification defines execution of the unpacked bundle [[1]](https://opencontainers.org/about/overview/). Content addressing answers “which bytes?” but not “were these bytes built from the reviewed source by the approved process?” Add provenance, signature verification, vulnerability policy, and deployment admission.

A conservative multi-stage pattern is:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:<reviewed-digest> AS build
WORKDIR /build
COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --require-hashes --wheel-dir=/wheels -r requirements.lock

FROM python:3.13-slim@sha256:<same-or-reviewed-runtime-digest>
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
COPY --chown=app:app src/ ./src/
USER app
ENTRYPOINT ["python", "-m", "src.service"]
```

Docker recommends multi-stage builds, small trusted bases, `.dockerignore`, rebuilding often, and pinning base images by digest when auditability/reproducibility is required [[2]](https://docs.docker.com/build/building/best-practices/). Digest pinning deliberately opts out of automatically receiving a newer patched tag; dependency automation must propose, test, scan, sign, and roll out updated digests. Never bake provider keys, cluster credentials, source-control tokens, or tenant data into a layer or build argument.

The release unit should identify `[inferred]`:

```text
image digest + source commit + lockfile digest + build provenance
+ SBOM + scan result + signature + policy version
+ model/adapter/tokenizer digest + prompt/tool/schema versions
+ database migration compatibility + deployment manifest digest
```

SLSA v1.2 defines source and build tracks and provenance expectations for incrementally stronger supply-chain assurance [[5]](https://slsa.dev/spec/v1.2/). Sigstore Cosign supports signing container images by digest; verify identity/issuer, transparency-log evidence where required, and attestation predicates at admission rather than accepting any syntactically valid signature [[6]](https://docs.sigstore.dev/cosign/signing/signing_with_containers/).

At runtime, use a read-only root filesystem where possible, a non-root user, dropped Linux capabilities, bounded PID/memory/CPU/ephemeral storage, and a default seccomp profile. Docker rootless mode runs both daemon and containers without root in a user namespace [[3]](https://docs.docker.com/engine/security/rootless/). Docker's default seccomp allowlist currently denies about 44 of 300+ syscalls and its documentation recommends not disabling the profile [[4]](https://docs.docker.com/engine/security/seccomp/). A container is process isolation sharing a kernel, not a security boundary equivalent to a dedicated machine; execute untrusted agent-generated code in a stronger sandbox/runtime class with no ambient credentials and explicit egress policy `[inferred]`.

### 1.3 Kubernetes: reconciliation, scheduling, health, and rollout

Use workload controllers according to state and completion semantics; a Pod is the disposable scheduling/execution unit, while controllers reconcile collections of Pods toward desired state [[7]](https://kubernetes.io/docs/concepts/workloads/pods/):

| Primitive | Use | Avoid assuming |
|---|---|---|
| `Deployment` | stateless API, router, consumer, inference replica | pod identity or durable local state |
| `StatefulSet` | stable identity/storage/order is intrinsic | storage replication or application consistency is automatic |
| `Job` / `CronJob` | bounded, retryable completion / scheduled work | side effects are exactly once |
| DaemonSet | node-local agent/device/log/network component | ordinary horizontally scaled business service |
| managed external service | DB, broker, object store, model provider when operationally preferable | provider outage or quota disappears |

Kubernetes schedules from declared **resource requests**, not real future usage; limits are enforced differently by resource and runtime [[12]](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). Node autoscalers likewise provision from pending-pod scheduling constraints, especially requests; under-requesting can produce contention while over-requesting can prevent consolidation [[14]](https://kubernetes.io/docs/concepts/cluster-administration/node-autoscaling/). Define CPU, memory, ephemeral-storage, accelerator, topology, affinity, taint/toleration, and volume constraints. Kubernetes Dynamic Resource Allocation can describe structured device claims and allocation, but its presence does not create scarce accelerators or predict model capacity [[46]](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/). Keep model-serving pools separate from general agent/API pools so CPU scale-out cannot consume reserved GPU nodes `[inferred]`.

Health signals have distinct meanings. A startup probe suppresses liveness and readiness until initialization succeeds. Readiness controls traffic eligibility; liveness restarts an unrecoverably stuck container. Kubernetes warns that a bad liveness probe can cause cascading failure by restarting containers under load and shifting more traffic to survivors [[9]](https://kubernetes.io/docs/concepts/workloads/pods/probes/). For a model server, readiness should require the assigned model/adapter and critical runtime to be loaded, but liveness should not fail merely because a remote provider or database is briefly slow `[inferred]`.

Graceful termination sequence `[inferred]`:

1. Mark the pod not ready and stop new admission.
2. Stop pulling new queue items; extend leases for accepted work when supported.
3. Drain HTTP/gRPC streams within a bounded grace period.
4. Checkpoint or relinquish unfinished durable work.
5. Flush bounded telemetry, then exit before `terminationGracePeriodSeconds`.

A `PodDisruptionBudget` limits approved voluntary evictions using `minAvailable` or `maxUnavailable`; it does not prevent involuntary node failures or by itself constrain every Deployment rolling update [[10]](https://kubernetes.io/docs/reference/kubernetes-api/policy/pod-disruption-budget-v1/) [[47]](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/). Spread replicas across nodes and zones. Topology spread constraints express this, but Kubernetes notes that constraints can become imbalanced after scale-down and zero-sized topology domains may be invisible unless the node autoscaler understands them [[11]](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/).

The native Deployment `RollingUpdate` defaults to 25% `maxUnavailable` and 25% `maxSurge`; a rollout that makes no progress for the default 600 seconds is reported stalled [[8]](https://kubernetes.io/docs/tasks/run-application/update-deployment-rolling/). Those defaults are mechanics, not a safety decision. For an expensive GPU model, `maxSurge` may be impossible without reserved capacity; for a one-replica API, nonzero unavailability creates downtime. Database, queue-event, API, prompt, tool, and output-schema changes need backward/forward compatibility across the mixed-version window.

Recommended release flow `[inferred]`:

```text
build -> tests/evals -> scan -> sign/attest -> policy admission
 -> deploy dark/shadow -> readiness/model warmup
 -> canary tenant/traffic cohort -> SLO + quality + cost gates
 -> staged expansion -> bake -> complete -> retain known-good rollback
```

Google defines canarying as a partial, time-limited deployment evaluated against a control. Its worked example shows that a 20% failure affecting a 5% canary population produces a 1% overall error rate under uniform-load assumptions; real canary size and duration must represent traffic and failure modes [[33]](https://sre.google/workbook/canarying-releases/). For agents, gate not only HTTP errors and latency but task success, tool denials, side-effect duplicates, tokens/cost per accepted result, loop terminations, queue age, checkpoint errors, and policy violations.

An error-budget policy can halt ordinary releases after the budget is exhausted while still permitting emergency/security changes [[50]](https://sre.google/workbook/error-budget-policy/).

### 1.4 API contracts: admission before expensive execution

OpenAPI 3.2.0 is the current published, language-independent HTTP interface specification as of this research date [[17]](https://spec.openapis.org/oas/v3.2.0.html). Generate validation/tests/clients where useful, but treat the reviewed specification and compatibility policy as authoritative. Version behavior, not only URL strings: clients need deprecation and sunset policy, supported schemas, and a migration window.

For every public operation define:

- authentication and tenant identity;
- object-, field-, and function-level authorization;
- input/output schema, size, token, attachment, URL, tool, and model constraints;
- idempotency and deduplication behavior;
- absolute deadline and server-side work budget;
- streaming/event resume and cancellation behavior;
- rate, concurrency, spend, and provider-quota admission;
- retryable/non-retryable errors and `Retry-After`;
- privacy, retention, residency, and audit semantics;
- operation status and terminal reason for asynchronous work.

OAuth 2.0 deployments should follow the current security best current practice, including avoiding deprecated/insecure modes and applying sender-constrained or replay-resistant protections where the risk requires them [[23]](https://www.rfc-editor.org/rfc/rfc9700.html). HTTP `RateLimit` fields standardize how a server can communicate quota policy and remaining capacity, but they supplement rather than replace server-side admission and authorization [[22]](https://www.rfc-editor.org/rfc/rfc9331.html).

HTTP defines PUT, DELETE, and safe methods as idempotent and warns clients/proxies not to automatically retry non-idempotent requests without knowledge that replay is safe or the original was not applied [[18]](https://www.rfc-editor.org/rfc/rfc9110.html). A `POST /agent-runs` with side effects should therefore require an idempotency key scoped to tenant + operation + canonical request hash. Persist `key -> request hash -> operation/result` atomically, reject key reuse with different input, and keep records longer than the maximum client retry horizon `[inferred]`.

Use machine-readable failures. RFC 9457 defines `application/problem+json`, including `type`, `title`, `status`, `detail`, and `instance`, and permits problem types to specify `Retry-After` [[19]](https://www.rfc-editor.org/rfc/rfc9457.html). Distinguish invalid input, denied policy, quota exhaustion, concurrency saturation, dependency unavailable, deadline exceeded, and internal failure; do not make clients parse prose.

Deadlines must be end-to-end. gRPC has no deadline by default and recommends explicit deadlines; servers should stop spawned work when cancellation/deadline occurs [[20]](https://grpc.io/docs/guides/deadlines/). gRPC retry policy supports bounded attempts, backoff, retryable status codes, and commitment semantics, but transport retry does not make an application side effect idempotent [[21]](https://grpc.io/docs/guides/retry/). Allocate the outer deadline across queue, model, tools, verification, response, and cancellation cleanup; each child receives the smaller of its budget and remaining parent time `[inferred]`.

Admission order `[inferred]`:

```text
parse/minimal size checks -> authenticate -> authorize tenant/object/action
-> validate schema/content -> policy/tool/URL allowlists
-> idempotency lookup -> rate/concurrency/spend/provider quota
-> estimate work + deadline feasibility -> enqueue/execute
```

Reject early. OWASP's API Security Top 10 2023 highlights broken object/function/property authorization, unrestricted resource consumption, SSRF, inventory failures, and unsafe consumption of upstream APIs [[42]](https://owasp.org/API-Security/editions/2023/en/0x11-t10/). An LLM API adds variable token and tool cost: enforce input bytes, rendered tokens, maximum output tokens, tool steps, wall time, concurrent runs, callback destinations, URL/DNS/IP policy, and budget reservations before starting.

### 1.5 Queue mechanics: delivery is not processing

A broker transfer and an application commit are different events. Choose semantics deliberately:

| Contract | Mechanism | Consequence |
|---|---|---|
| At-most-once | remove/ack before or without durable processing | work may be lost; no broker duplicate |
| At-least-once | ack only after durable result; lease/visibility expiry redelivers | no loss under stated broker durability; duplicates expected |
| Ordered | partition/message-group key plus serialized ownership | throughput limited per key; cross-key order absent |
| Effectively once | at-least-once + idempotent effect/dedupe/transaction | guarantee scoped to a defined state transition |
| Kafka exactly-once | transactional read-process-write to Kafka + committed reads | does not make an arbitrary external API exactly once |

Amazon SQS standard queues provide at-least-once delivery and can duplicate or reorder messages; consumers must be idempotent [[24]](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-queue-types.html). Visibility is a renewable ownership lease: if processing is not followed by deletion before expiry, the message becomes visible. AWS states that duplicates remain possible even inside the visibility window [[25]](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html).

RabbitMQ separates consumer acknowledgements from publisher confirms. Confirms tell a publisher the broker accepted responsibility; manual consumer acknowledgements transfer responsibility only after processing, while prefetch bounds outstanding deliveries [[26]](https://www.rabbitmq.com/docs/next/confirms) [[27]](https://www.rabbitmq.com/docs/reliability). RabbitMQ quorum queues confirm after a quorum accepts a message and include poison-message handling/delivery limits; these broker guarantees still do not atomically commit an external database or tool side effect [[28]](https://www.rabbitmq.com/docs/next/quorum-queues).

Kafka's current design documentation distinguishes at-least-once retries from transactional exactly-once processing. `read_committed` consumers plus a transactional producer can atomically read, process, and write Kafka records; default `read_uncommitted` exposes records from aborted transactions, and external systems remain outside that transaction [[29]](https://kafka.apache.org/43/design/design/).

Robust worker pattern:

```text
receive message + attempt + lease
 -> validate envelope/schema/tenant/version
 -> acquire idempotency/operation record
 -> if committed: acknowledge duplicate
 -> mark attempt running; heartbeat/extend lease
 -> perform restartable steps; checkpoint after durable boundaries
 -> commit output/effect + operation terminal state atomically where possible
 -> publish resulting event through transactional outbox/CDC
 -> acknowledge input
```

The transactional outbox writes business state and an event row in one database transaction; a relay later publishes committed rows. AWS notes that the relay/broker can duplicate, so consumers remain idempotent, and ordering needs timestamps or sequence numbers [[31]](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html). For irreversible external effects (email, payment, write API, infrastructure change), pass an idempotency key to the effect provider, record intent/result, and reconcile ambiguous timeouts instead of blindly retrying `[inferred]`.

Every queue needs bounded attempts, exponential backoff with jitter, lease heartbeats, maximum age, poison classification, dead-letter quarantine, redrive authorization, and replay tooling. Alert on **oldest ready age**, not only depth. Depth may be stable while one tenant or ordered partition is stuck; large messages and long token jobs make “one message” a poor work unit. Track estimated tokens/tool steps or measured service time by class `[inferred]`.

## 2. Token Economics & NFR Metrics

### 2.1 Define user-visible service levels

An SLI measures an outcome; an SLO sets its target; an SLA is the external agreement/consequence. Google recommends ratio-style SLIs and defines error budget as `100% - SLO`; its example shows that a 99.9% target over 3 million requests permits 3,000 bad requests [[32]](https://sre.google/workbook/implementing-slos/). Do not use pod uptime as the primary user SLI.

Useful AI production SLIs `[inferred]`:

| Path | Good event | Latency/freshness | Correctness/quality | Resource/economic guardrail |
|---|---|---|---|---|
| sync inference | accepted request returns valid completion | TTFT and end-to-end under threshold | schema valid; policy compliant; sampled task score | tokens and cost per accepted completion |
| durable agent | accepted run reaches correct terminal state | queue age, completion deadline | task success; no duplicate side effects | steps/tokens/tools/cost per successful run |
| batch | item produces committed output | completion by business deadline | validation/eval pass | compute/provider cost per valid item |
| platform | API admits/rejects correctly | auth/admission p99 | correct tenant/policy decision | rejected work consumes negligible accelerator time |

Count overload rejections, timeouts, invalid structured outputs, policy aborts, and retry-exhausted work according to the user contract. A fallback is “good” only if it meets the defined minimum capability and quality. Separate provider-caused and application-caused indicators for diagnosis, but measure the end-to-end promise for the customer.

### 2.2 Capacity and cost model

Measure demand by work class, not request count alone:

```text
offered_input_tokens/s   = arrival_rate * mean(rendered_input_tokens)
offered_output_tokens/s  = arrival_rate * mean(generated_tokens)
offered_tool_time/s      = arrival_rate * mean(sum(tool durations))

required_replicas ~= ceil(
  peak_offered_work_per_s / measured_goodput_per_replica_at_complete_SLO
) + failure_and_rollout_headroom

effective_cost_per_success =
  (accelerators + CPU + provider tokens + storage + queue + network
   + observability + idle headroom + retries + failed work)
  / policy-compliant successful outcomes
```

These are sizing frames, not guarantees `[inferred]`. Replay the joint distribution of prompt/output length, streaming, tool latency, prefix locality, model/adapter, tenant priority, retry, and burst. “Tokens per second” without TTFT/latency/quality constraints is throughput, not SLO goodput.

Little's Law gives `concurrency ~= arrival_rate * mean_time_in_system` in stable conditions. If mean job time is 40 seconds at 2 arrivals/s, mean in-system work is about 80 jobs, but burst and tail distributions require more than average capacity. Approximate drain time as `backlog_work / (available_service_rate - incoming_work_rate)` only when service exceeds arrival; otherwise overload is growing `[inferred]`.

### 2.3 Autoscaling is a two-loop, delayed control system

Kubernetes HPA periodically applies approximately:

```text
desiredReplicas = ceil(currentReplicas * currentMetric / desiredMetric)
```

The documented default controller sync interval is 15 seconds; multiple metrics choose the largest replica recommendation, missing metrics are handled conservatively, and the default scale-down stabilization window is 300 seconds [[13]](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/). HPA changes workload replicas. A node autoscaler separately provisions/consolidates machines for unschedulable pods and can fail because of quota, incompatible constraints, provisioning limits, or cloud capacity [[14]](https://kubernetes.io/docs/concepts/cluster-administration/node-autoscaling/).

This creates delayed stages:

```text
metric collection -> pod decision -> pending pod -> node/accelerator provision
-> image/model download -> runtime/model warmup -> readiness -> useful capacity
```

Scale on the bottleneck closest to user pain:

- APIs: admitted concurrency, request queue time, active streams, deadline slack;
- queue consumers: oldest-message age and estimated remaining work, with depth as supporting signal;
- inference: queued tokens/prefill work, active decode sequences/KV pressure, SLO goodput, model/adapter locality;
- tools: provider concurrency/quota and latency, not merely local CPU;
- batch: work remaining versus completion deadline and budget.

KEDA connects external event-source metrics to HPA and directly handles zero-to-one activation. Current KEDA documentation uses a default 30-second polling interval and 300-second cooldown to zero; once above one replica, HPA handles scaling [[15]](https://keda.sh/docs/2.21/reference/scaledobject-spec/). Scale-to-zero is unsuitable when model/node startup exceeds acceptable queue age. Keep warm minimums, scheduled/predictive headroom, pre-pulled artifacts, or admission capable of returning an honest asynchronous/retry response `[inferred]`.

Bound `maxReplicas` by downstream/provider quota, database connections, broker partitions, GPU supply, and spend. More consumers can make a dependency outage worse. Use per-tenant and priority-class queues, admission, and weighted fairness so a batch spike cannot starve interactive work. Kubernetes pod priority/preemption helps scheduling but can evict lower-priority pods; it does not implement business-level fair queueing [[48]](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/).

### 2.4 Production measurement and public-data limits

Measure distributions and cohorts:

- p50/p95/p99 admission, queue, TTFT, end-to-end, tool, and completion latency;
- success, correctness/eval, policy-denial, cancellation, timeout, retry, duplicate, DLQ, and ambiguous-effect rates;
- arrival work, admitted work, useful throughput, SLO goodput, utilization, saturation, and headroom;
- image pull, scheduling, accelerator allocation, model load, readiness, rollout, rollback, and failover time;
- prompt/output/cached/reasoning tokens, tool/provider charges, egress, storage, accelerator-hours, and cost per success;
- regional/zone/tenant/model/tool/queue/priority/software-version breakdowns with cardinality controls.

> ⚠️ Limited public data available for this dimension. There is no portable “requests per GPU/pod” or Kubernetes replica count for agent systems. Model, precision, engine, accelerator, prompt/output distribution, tool waits, prefix locality, SLO, quality gate, and rollout/failure headroom dominate. Capacity must come from a versioned production-shaped replay on the target stack.

> ⚠️ Limited public data available for this dimension. Public cloud and provider prices, quotas, and availability vary by date, region, contract, model, and tenant. The defensible economic artifact is a dated internal bill-of-materials and cost-per-policy-compliant-success dashboard, not a universal price claim.

## 3. Distributed Resilience & State

### 3.1 Classify state and assign recovery ownership

| State | Source of truth | Durability/recovery | Key hazard |
|---|---|---|---|
| container filesystem | none; disposable | rebuild image/recreate pod | accidental hidden state |
| request/session | DB or signed client state | expiry + replication | sticky pod fate-sharing |
| operation/workflow | durable DB/event history | checkpoint/replay/reconcile | duplicate side effects |
| queue/log | broker replicas | retention, ack/offset, cross-region plan | loss, duplicate, poison, order |
| relational/vector data | managed DB/object store | PITR/backups/replication | corrupt or stale index |
| model/prompt/tool artifact | immutable registry | replicated digest + provenance | silent version drift |
| cache/KV | reconstructable | evict/recompute | treating cache as authoritative |
| secrets/config/policy | secret/config authority | version, rotation, replicated recovery | region drift/stale credentials |
| audit/telemetry | append-oriented store | retention/WORM where required | privacy leak or missing evidence |

Checkpoint at semantic boundaries: after a durable read snapshot, before/after a side effect, after a tool result, and before transferring ownership. Include workflow/schema version and enough input identity to validate replay. Never serialize live sockets, opaque SDK clients, or unversioned code assumptions as durable state `[inferred]`.

Retry budgets must be hierarchical. Client, gateway, service, queue, workflow, SDK, and provider retries can multiply. With three attempts at four layers, one user action can theoretically produce `3^4 = 81` downstream attempts. Permit one layer to own retry for each failure class, propagate attempt/deadline/idempotency context, use exponential backoff with jitter, cap total elapsed time, and stop when the caller no longer benefits `[inferred]`.

### 3.2 Overload, partition, and graceful degradation

Overload policy precedes autoscaling because scaling reacts after observation and may be unable to obtain accelerators. Use bounded queues, admission concurrency, per-tenant budgets, load shedding, and priority. Return a truthful overload response; do not retry synchronously across every replica. Google SRE's cascading-failure guidance emphasizes load shedding, controlled retries, and avoiding retry amplification when a system is already overloaded [[34]](https://sre.google/sre-book/addressing-cascading-failures/). Circuit breakers should open on dependency-specific evidence and use controlled probes to recover. Bulkhead pools prevent a slow tool/model/tenant from consuming all worker slots `[inferred]`.

Degradation ladder example:

1. preserve auth, policy, tenant isolation, idempotency, and audit;
2. reject new low-priority batch work or defer it durably;
3. reduce optional tools/verifiers or switch to a pre-evaluated fallback model;
4. cap output/steps only where the API contract permits;
5. shed new interactive work before accepted work misses all deadlines;
6. never bypass authorization or safety to improve availability.

Readiness must not recursively probe every dependency because one outage can remove all endpoints. Expose dependency health separately; decide which dependencies are required for which route. Liveness should reflect local progress, not downstream health. During broker/database partition, stop accepting work whose durable ownership cannot be recorded.

### 3.3 Multi-zone and multi-region design

Multi-zone is the normal high-availability baseline where supported: replicate stateless services and stateful dependencies across failure domains, use topology spread, retain spare capacity for one-domain loss, and verify the remaining domains can meet the SLO. Cross-zone replication does not address region-wide failure, account/control-plane lockout, destructive writes, or bad releases.

AWS reliability guidance distinguishes backup/restore, pilot light, warm standby, and active-active in increasing cost/complexity and decreasing RTO/RPO; its illustrative ranges are backup/restore RPO in hours and RTO up to 24 hours, pilot-light minutes/tens of minutes, warm-standby seconds/minutes, and active-active near-zero/potentially zero. These are AWS pattern ranges, not guarantees for an application [[36]](https://docs.aws.amazon.com/wellarchitected/2023-10-03/framework/rel_planning_for_recovery_disaster_recovery.html).

Choose per business objective:

| Pattern | Traffic/data | Benefit | Principal complexity |
|---|---|---|---|
| backup/restore | no serving recovery stack; restore artifacts/data | lowest steady cost | long restore, capacity and credential dependencies |
| pilot light | core data/control minimum always on | faster than rebuild | scale-up and configuration drift |
| warm standby | reduced but functional second region | bounded failover | continuously test replication and scale-up |
| active-passive hot | full passive capacity, explicit promotion | fast, simpler single writer | idle cost, failover/failback correctness |
| active-active | both regions serve | latency and region-failure availability | write conflict, global quota, consistency, routing, cost |

AWS warns that cross-region dependencies can weaken reliability and recommends verifying each region can operate independently [[35]](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_fault_isolation_multiaz_region_system.html). Replicate container/model artifacts, policy/config, schemas, secrets/identity trust, queues or event logs, databases, observability, quotas, runbooks, and infrastructure definitions. Pre-provision accelerator/provider quota; a YAML copy in another region is not recoverable capacity.

For active-active, define a write owner or conflict rule for every entity: home region, globally consistent database, partitioned ownership, CRDT/merge semantics, or explicitly rejected concurrent write. Keep requests region-affine where workflows, caches, tool residency, or ordered queues require it. A regional queue does not fail over merely because global DNS changes. Decide whether to drain, replicate, replay, or abandon its accepted work, and prevent dual consumers during failover `[inferred]`.

### 3.4 SLO, RPO, RTO, and disaster recovery

- **SLO**: target quality of normal service over a measurement window.
- **RPO**: maximum tolerable age/loss of recoverable data at a disruption.
- **RTO**: maximum tolerable time to restore the required service level.

NIST SP 800-34 provides the contingency-planning lifecycle and ties recovery requirements to business-impact analysis rather than technology fashion [[37]](https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final). Specify RPO/RTO by state and event, not one number for “the platform” `[inferred]`:

| State/event | Example objective question | Proof |
|---|---|---|
| accepted operation | Can any acknowledged job disappear? | broker/DB failure and reconciliation test |
| workflow checkpoints | How many completed steps may replay? | kill/restart/replay test |
| user/source data | What write loss and stale read are tolerable? | point-in-time restore |
| vector index | Can it be rebuilt from versioned source? By when? | full rebuild/repoint |
| artifacts/config | How fast can a clean region reproduce exact versions? | digest inventory + isolated deploy |
| audit evidence | What loss violates investigation/compliance? | immutable-store restore/query |
| provider outage | What minimum product capability must return? | dependency game day |

Backups are not recovery until restored. Encrypt, isolate credentials/accounts, retain immutable or versioned copies against corruption/ransomware, validate checksums, and test full restore plus application-level consistency. Kubernetes etcd snapshots recover Kubernetes API state, not external databases, broker contents, object storage, provider-side work, or uncommitted in-memory jobs [[44]](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/). Track measured recovery point and recovery time from exercises; alert when replication lag or restore capacity makes objectives impossible.

Failover runbook `[inferred]`:

```text
declare incident and authority -> freeze unsafe writes/releases
-> establish source-of-truth region and queue ownership
-> verify data/artifact/config/secret recovery point
-> promote/scale dependencies and capacity
-> route a controlled cohort -> validate auth, correctness, policy, SLO
-> expand traffic -> reconcile ambiguous/in-flight operations
-> preserve evidence -> plan safe failback without split brain
```

> ⚠️ Limited public data available for this dimension. A defensible RPO/RTO for the user's system cannot be inferred from Kubernetes or a cloud pattern. It depends on business impact, each data store's replication/backup contract, provider and accelerator capacity, operation semantics, and tested restore/failover measurements.

## 4. Enterprise Security & Governance

### 4.1 Layered trust boundaries

Security controls align to different threats:

1. **Source/build:** protected branches, reviewed dependencies, isolated/ephemeral builders, lockfiles, secret scanning, SBOM, vulnerability/licence policy, SLSA provenance.
2. **Registry/admission:** immutable digest, signature and attestation verification, approved registries/builders, vulnerability exception expiry, policy-as-code.
3. **Kubernetes control plane:** strong identity, short-lived credentials, least-privilege RBAC, admission, audit, encrypted etcd/secrets, upgrade/patch policy.
4. **Pod/runtime:** non-root, no privilege escalation, read-only FS, dropped capabilities, seccomp/AppArmor/SELinux, resource limits, runtime class/sandbox, metadata blocking.
5. **Network/service:** default-deny ingress/egress, TLS/mTLS as appropriate, DNS/URL/IP allowlists, service identity, private endpoints, egress proxy. Kubernetes NetworkPolicy governs pod ingress/egress only when the installed network implementation enforces it [[43]](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
6. **Application/API:** tenant/object/tool authorization, schema validation, idempotency, quotas, deadlines, content/tool policy, SSRF defense.
7. **Data/model:** encryption, residency, retention/deletion, tenant namespaces, key rotation, provenance, safe model serialization.
8. **Operations:** separation of duties, break-glass with expiry/audit, deploy approvals, incident access, evidence retention.

Kubernetes API access follows authentication, authorization, admission, validation, then persistence; any admission controller can reject a mutating request, and audit provides a chronological security record [[41]](https://kubernetes.io/docs/concepts/security/controlling-access/). Admission policies should reject unsigned/unapproved images, mutable tags, privileged pods, host namespaces/paths, missing resources, broad service accounts, disallowed registries, and missing tenant/network labels `[inferred]`.

The Kubernetes Pod Security Standards define Privileged, Baseline, and Restricted profiles; Restricted follows current hardening practices but does not itself define whether a pod uses a stronger sandbox runtime [[38]](https://kubernetes.io/docs/concepts/security/pod-security-standards/). Enforce rather than only label policy. Isolate untrusted code execution from model/API services and nodes; use a dedicated identity, runtime class, node pool, filesystem, network, and destruction lifecycle.

Kubernetes Secrets are stored unencrypted in etcd by default unless encryption at rest is configured; the project recommends least-privilege access and considering external secret stores [[40]](https://kubernetes.io/docs/concepts/configuration/secret/). Prefer workload identity and short-lived credentials over static secrets. Do not expose node metadata credentials to pods; Kubernetes security guidance specifically recommends restricting metadata API access and instance permissions [[39]](https://kubernetes.io/docs/tasks/administer-cluster/securing-a-cluster/).

### 4.2 API, queue, and tenant governance

Authentication proves a principal; authorization must still be checked on every object, property, function, tool, model, dataset, and administrative operation. Bind tenant identity server-side, never trust a tenant ID in the body. Authorize callback and tool destinations; resolve DNS and validate all redirects/IP ranges at connection time to resist SSRF and rebinding `[inferred]`.

Use quotas at several dimensions: requests/time, concurrent streams/runs, queued work, input/output tokens, tool calls, spend, storage, and provider/model allocation. Reserve worst-case or predicted budget atomically at admission, charge actual use, release the remainder, and cap run-level budgets inside the executor. Rate limits alone do not stop one accepted request from looping for hours.

Queue envelopes should contain opaque references rather than unnecessary sensitive prompts. Encrypt transport and storage; restrict producer, consumer, admin, redrive, purge, and DLQ inspection independently. Sign or authenticate messages across trust domains, validate schema/version on consumption, and record original principal/tenant/policy context. A DLQ often contains the most sensitive failed payloads; apply normal data retention, access, and deletion rules.

Redrive is a privileged release. Before replay, fix/classify the cause, select messages, pin corrected code/schema/policy, dry-run where possible, throttle by downstream capacity, preserve original identity/idempotency key, and audit operator/reason/result `[inferred]`. Never purge a production queue as an ordinary mitigation without evidence preservation and an explicit data-loss decision.

### 4.3 Governance artifacts

Maintain:

- service owner, on-call, data owner, threat model, dependency and API inventory;
- versioned OpenAPI/event schemas, compatibility and deprecation policy;
- artifact/model/prompt/tool provenance and approval evidence;
- data classification, region, retention, deletion, backup, and recovery mapping;
- SLI/SLO/error-budget and capacity policy;
- rollout, rollback, redrive, failover, failback, key-rotation, and break-glass runbooks;
- vulnerability and policy exceptions with owner, rationale, compensating control, and expiry;
- audit records linking user operation, queue attempts, tools, model/provider, release, policy, and cost without logging secrets or raw sensitive content unnecessarily.

## 5. Production Failure Modes

| Failure | Mechanism / symptom | Prevent, contain, recover | Detection / test |
|---|---|---|---|
| Mutable/untrusted image | same tag changes or compromised build deploys | digest pin, provenance/signature admission, rebuild/patch cadence | verify admission rejects altered/unsigned artifact |
| Secret in image/log | layer or telemetry leaks provider/tenant credential | secret scanning, workload identity, redaction, rotation | layer/SBOM/log scan; credential game day |
| Privileged escape path | host mount/capability/runtime exposes node | Restricted policy, seccomp, non-root, sandboxed untrusted code | admission tests and runtime escape review |
| Bad readiness | pod receives traffic before model warmup | readiness tied to serving capability; startup probe | cold rollout with large model |
| Bad liveness | dependency/load spike restarts all pods | local-progress liveness, tolerant thresholds | dependency outage and overload test |
| Rolling capacity collapse | surge cannot schedule or mixed versions conflict | reserved rollout headroom, compatibility, progressive gate | canary under peak + zone loss |
| PDB false confidence | involuntary failure or rollout still reduces capacity | topology, spare capacity, rollout settings, chaos test | node/AZ termination |
| HPA oscillation | delayed/noisy metric and rapid scale-down | stabilization, multiple signals, headroom, rate limits | burst/decay replay |
| Pod/node scale mismatch | pods pending while GPU nodes/images/models start | node quota, prewarm/pull, minimum pool, admission | cold zero-to-peak exercise |
| CPU metric blindness | queue/deadline misses while CPU moderate | queue age/work/concurrency/SLO metrics | long-tool or long-sequence load |
| Scale-out overloads dependency | more workers exhaust DB/provider quota | max replicas from downstream budget, bulkhead | dependency capacity fault injection |
| Unbounded retry storm | retries multiply across layers after timeout | retry ownership, global budget, jitter, deadline, idempotency | inject 429/503/timeout and count attempts |
| Lost acknowledged job | API responds before durable operation/queue commit | transactional persistence before `202 Accepted` | kill between accept and publish |
| Duplicate side effect | lease expires after effect but before ack | effect idempotency key, intent/result, reconciliation | kill at every commit boundary |
| Poison-message loop | deterministic bad input repeatedly consumes workers | max attempts, quarantine/DLQ, schema validation | malformed/version-skew replay |
| Visibility too short | live worker loses lease, concurrent duplicate runs | heartbeat/extend, checkpoint, fenced ownership | slow job beyond initial lease |
| Visibility too long | crashed job waits too long to retry | adaptive bounded lease and failure signal | kill worker after receive |
| Queue metric lies | depth low but oldest item/partition stuck | age/partition/tenant/work metrics | hot-key and ordered-partition test |
| Transactional outbox lag | business state commits but event delayed | relay HA, age alert, CDC/replay | stop relay, restore without duplication |
| Streaming disconnect leak | downstream work continues after client leaves | cancellation propagation and billing/reconciliation | disconnect mid-generation/tool |
| Deadline mismatch | gateway times out while worker/tool continues | propagated absolute deadline and child budgets | dependency delay beyond remaining time |
| Tenant starvation | one customer/batch monopolizes workers/GPU | per-tenant admission, queues, weighted fairness | adversarial heavy-tenant load |
| Regional split brain | both regions own writes/queues after partition | fencing epoch, single ownership/conflict rule | isolate control links during failover |
| Replication lag exceeds RPO | failover loses accepted state | lag alert, stop unsafe acceptance, restore/reconcile | promote at controlled lag |
| Corrupt state replicated | active-active copies deletion/corruption | PITR/immutable backup independent of replication | corruption restore exercise |
| DR capacity missing | manifests exist but GPU/provider quota unavailable | reserved capacity/quota, periodic scale/failover test | isolated recovery-region load test |
| Control-plane outage | Kubernetes/API/registry unavailable during incident | running data plane independence, cached artifacts, runbook | block control plane while serving |
| Bad global release | same defect reaches all zones/regions | staged cohort/region rollout, known-good artifact, config rollback | intentional canary failure |
| Schema/version skew | old consumer cannot parse new event/tool output | additive evolution, version envelope, contract tests | mixed-version rollout/replay |
| Observability overload/leak | high-cardinality/raw prompts raise cost/privacy risk | sampling, cardinality budgets, redaction, access/retention | load and privacy audit |

Failure handling must define a terminal state. `FAILED_RETRYABLE` without a bounded next attempt is not terminal; `UNKNOWN` after an external timeout requires reconciliation; cancellation may be `REQUESTED`, `CANCELLED_BEFORE_EFFECT`, or `COMPLETED_DESPITE_CANCEL` depending on what actually occurred `[inferred]`.

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A: multi-tenant interactive inference API

**Requirements:** streaming chat, strict tenant isolation, variable prompts, p99 latency SLO, one-zone loss, provider/self-hosted fallback.

**Design `[inferred]`:** edge WAF and API gateway authenticate, authorize, validate, rate-limit, and assign an absolute deadline. Admission reserves tenant token/spend/concurrency capacity. A stateless API routes by model capability/residency and sends to inference pools spread across zones. The Kubernetes Gateway API Inference Extension is one current implementation pattern for model/pool resources and inference-aware endpoint selection [[45]](https://gateway-api-inference-extension.sigs.k8s.io/). Readiness requires model load; startup probes tolerate cold initialization. HPA uses admitted concurrency/queued tokens/SLO goodput, while node autoscaling manages GPU nodes. Maintain warm replicas and enough N+1 capacity; do not expose scale-from-zero delay as ordinary latency. Cancellation propagates through the inference gateway. A pre-evaluated fallback may be used only for eligible request classes. Canary by tenant/traffic cohort and gate TTFT/E2E, valid output, policy, token cost, OOM, and fallback rate.

**Interview trade-off:** explain why CPU utilization is a weak sole signal, why `maxSurge` requires costly GPU headroom, and why fallback success must be part of the API/quality contract rather than an availability trick.

### 6.2 Scenario B: durable research/coding agent

**Requirements:** 30-minute to multi-hour runs, browser/code tools, restarts and deployments, cancellation, no duplicate external writes.

**Design `[inferred]`:** `POST /runs` uses a tenant-scoped idempotency key, atomically creates the operation, and returns `202` with status/event URLs after durable commit. A workflow history holds state and timers; activities use idempotency keys, explicit timeouts, heartbeats, and bounded retries. Sandboxed tool workers have per-run filesystem, no ambient cluster/cloud credential, allowlisted egress, CPU/memory/PID/disk/network budgets, and are destroyed after the run. Checkpoint at tool boundaries. Queue priority separates interactive from scheduled research. Cancellation stops new activities, attempts supported downstream cancellation, and reports whether an already-issued side effect completed. Every run has token/tool/time/spend ceilings and a terminal reason.

**Interview trade-off:** a queue alone does not persist branching/timers/checkpoints; workflow replay does not make external activities exactly once. The design combines durable orchestration with idempotent or reconcilable effects.

### 6.3 Scenario C: high-volume offline evaluation or enrichment

**Requirements:** millions of records, completion deadline, restartability, schema-versioned output, low cost, no impact on online traffic.

**Design `[inferred]`:** immutable input manifest shards by stable item ID. Separate queue and Kubernetes Jobs use lower priority and a distinct node/provider quota. Workers commit outputs to a staging key containing run/item/version, then conditionally mark the item complete. Reprocessing is safe. Autoscaling uses remaining estimated tokens/work, oldest age, time to deadline, and budget; cap against provider quota. DLQ by error class. Canary a representative shard, validate output schema and sampled quality, then widen. Finalization verifies counts/hashes and atomically publishes a dataset manifest. Retain source/output/artifact/prompt/model/eval lineage.

**Interview trade-off:** FIFO ordering is normally unnecessary and would constrain throughput; determinism and idempotent per-item commit matter more. Batch SLO is a completion deadline, not a p99 request latency.

### 6.4 Scenario D: regulated warm-standby multi-region platform

**Requirements:** residency controls, audited access, regional failure, defined RPO/RTO, controlled failback.

**Design `[inferred]`:** each permitted region has independent network, Kubernetes cluster, identities, artifact replica, policy/config, observability, database replica, and minimum operational model/API capacity. Tenant data remains in allowed regions. One region owns writes and queue consumption using a fencing epoch; the warm region continuously receives supported replication and runs synthetic validation. Immutable backups are separate from replication. Failover freezes unsafe writes, confirms recovery point/ownership, promotes state, scales capacity, validates a small cohort, and changes global routing. Reconcile in-flight/ambiguous jobs before full traffic. Failback is another controlled migration, not an automatic DNS reversal.

**Interview trade-off:** warm standby costs more than backup/restore but reduces dependence on creating scarce capacity during disaster. Active-active is rejected unless near-zero recovery justifies write-conflict, routing, quota, and consistency complexity.

### 6.5 Technology decision matrix

| Decision | Prefer | When not to |
|---|---|---|
| Kubernetes | many services, mixed pools, policy, scheduling, controlled rollouts justify platform cost | small product can meet needs with managed containers/functions |
| managed broker | durability/HA/patching outweigh customization | compliance, protocol, latency, or cost requires operated broker |
| durable workflow engine | long-lived branching/timers/retries/human waits | simple independent jobs with idempotent DB state suffice |
| active-active regions | near-zero regional recovery and global latency justify complexity | single writer/warm standby meets objectives |
| scale to zero | asynchronous or latency-tolerant work with acceptable cold start | interactive model load/node provision exceeds SLO |
| native rolling update | stateless compatible change and enough surge capacity | risky model/policy/schema changes need progressive traffic controller |

### 6.6 Production-readiness and interview checklist

1. **Artifact:** Can you map a running pod to image/model/prompt/tool/source digests, provenance, SBOM, scan, signature, and approval?
2. **API:** Are authz, schema, deadline, idempotency, quota, cancellation, retry, and error contracts explicit?
3. **Queue:** Who owns work, when does ownership expire, when is it acknowledged, how are duplicates/poison/order handled, and how is redrive governed?
4. **State:** What survives a pod, node, zone, region, and bad deploy? Where is every source of truth?
5. **Scale:** What metric represents work/SLO pain, what is each control-loop delay, what is the downstream cap, and where is failure/rollout headroom?
6. **Release:** Is mixed-version compatibility tested? What canary cohort, gates, bake time, and automated rollback are used?
7. **Security:** Are build, admission, runtime, network, API, tool, tenant, secret, and data boundaries independently enforced?
8. **Reliability:** What are the user SLIs/SLOs and error-budget actions? What are RPO/RTO per state?
9. **Failure proof:** Have retries, kill points, poison work, provider throttling, zone loss, control-plane outage, bad release, restore, failover, and failback been exercised?
10. **Economics:** Is cost measured per compliant successful outcome, including idle headroom, retries, failures, queue/storage/network, and observability?

The strongest production answer links every technology choice to a failure or service contract. “Use Docker, Kubernetes, and a queue” is incomplete; explain the immutable artifact, admission, work ownership, idempotency boundary, scaling signal, rollout gate, and measured recovery proof.

## Sources

- [1] https://opencontainers.org/about/overview/ — OCI image, runtime, and distribution specification roles.
- [2] https://docs.docker.com/build/building/best-practices/ — Docker multi-stage, base image, digest, rebuild, and context practices.
- [3] https://docs.docker.com/engine/security/rootless/ — Docker rootless daemon and container execution.
- [4] https://docs.docker.com/engine/security/seccomp/ — Docker default seccomp profile and syscall scope.
- [5] https://slsa.dev/spec/v1.2/ — Current SLSA supply-chain specification and tracks.
- [6] https://docs.sigstore.dev/cosign/signing/signing_with_containers/ — Cosign container signing and identity workflow.
- [7] https://kubernetes.io/docs/concepts/workloads/pods/ — Pod lifecycle, security context, and workload unit.
- [8] https://kubernetes.io/docs/tasks/run-application/update-deployment-rolling/ — Deployment rolling-update defaults, progress detection, and rollback.
- [9] https://kubernetes.io/docs/concepts/workloads/pods/probes/ — Startup, readiness, and liveness semantics and hazards.
- [10] https://kubernetes.io/docs/reference/kubernetes-api/policy/pod-disruption-budget-v1/ — PodDisruptionBudget API semantics.
- [11] https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/ — Failure-domain spreading and autoscaling limitations.
- [12] https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ — Container requests, limits, and scheduling/resource behavior.
- [13] https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/ — HPA algorithm, metrics, timing, and stabilization.
- [14] https://kubernetes.io/docs/concepts/cluster-administration/node-autoscaling/ — Node provisioning inputs, constraints, and failure conditions.
- [15] https://keda.sh/docs/2.21/reference/scaledobject-spec/ — KEDA event-driven activation, polling, cooldown, and HPA integration.
- [16] https://kubernetes.io/docs/concepts/workloads/controllers/job/ — Kubernetes Job completion and retry controller.
- [17] https://spec.openapis.org/oas/v3.2.0.html — Current OpenAPI interface-description specification.
- [18] https://www.rfc-editor.org/rfc/rfc9110.html — HTTP method safety/idempotency and retry semantics.
- [19] https://www.rfc-editor.org/rfc/rfc9457.html — HTTP Problem Details machine-readable error contract.
- [20] https://grpc.io/docs/guides/deadlines/ — gRPC explicit deadline and cancellation guidance.
- [21] https://grpc.io/docs/guides/retry/ — gRPC retry policy, backoff, and commitment semantics.
- [22] https://www.rfc-editor.org/rfc/rfc9331.html — RateLimit fields for HTTP.
- [23] https://www.rfc-editor.org/rfc/rfc9700.html — OAuth 2.0 security best current practice.
- [24] https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-queue-types.html — SQS standard/FIFO and at-least-once behavior.
- [25] https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html — Visibility leases, duplicates, heartbeats, and in-flight work.
- [26] https://www.rabbitmq.com/docs/next/confirms — RabbitMQ consumer acknowledgements, publisher confirms, and prefetch.
- [27] https://www.rabbitmq.com/docs/reliability — RabbitMQ responsibility transfer and redelivery.
- [28] https://www.rabbitmq.com/docs/next/quorum-queues — Quorum replication and poison-message handling.
- [29] https://kafka.apache.org/43/design/design/ — Kafka delivery and transactional exactly-once scope.
- [30] https://docs.temporal.io/ — Temporal durable execution model.
- [31] https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html — Transactional outbox, ordering, and duplicate considerations.
- [32] https://sre.google/workbook/implementing-slos/ — SLI/SLO/error-budget design and calculation.
- [33] https://sre.google/workbook/canarying-releases/ — Progressive release evaluation and scoped canary example.
- [34] https://sre.google/sre-book/addressing-cascading-failures/ — Overload, load shedding, retry, and cascading-failure practices.
- [35] https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_fault_isolation_multiaz_region_system.html — Multi-location independence and regional design trade-offs.
- [36] https://docs.aws.amazon.com/wellarchitected/2023-10-03/framework/rel_planning_for_recovery_disaster_recovery.html — DR strategies and illustrative RPO/RTO ranges.
- [37] https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final — NIST contingency planning and business-impact recovery process.
- [38] https://kubernetes.io/docs/concepts/security/pod-security-standards/ — Privileged, Baseline, and Restricted pod policies.
- [39] https://kubernetes.io/docs/tasks/administer-cluster/securing-a-cluster/ — Cluster, kubelet, metadata, network, and secret hardening.
- [40] https://kubernetes.io/docs/concepts/configuration/secret/ — Kubernetes Secret storage and access cautions.
- [41] https://kubernetes.io/docs/concepts/security/controlling-access/ — API authentication, authorization, admission, persistence, and audit flow.
- [42] https://owasp.org/API-Security/editions/2023/en/0x11-t10/ — OWASP API Security Top 10 2023.
- [43] https://kubernetes.io/docs/concepts/services-networking/network-policies/ — Kubernetes pod ingress/egress policy model.
- [44] https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/ — etcd snapshot and restore mechanics.
- [45] https://gateway-api-inference-extension.sigs.k8s.io/ — Kubernetes inference-aware routing and endpoint selection.
- [46] https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/ — Structured device/accelerator allocation in Kubernetes.
- [47] https://kubernetes.io/docs/concepts/workloads/pods/disruptions/ — Voluntary/involuntary disruptions and PDB scope.
- [48] https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/ — Workload priority and preemption behavior.
- [49] https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx — Temporal Activity retry and idempotency expectations.
- [50] https://sre.google/workbook/error-budget-policy/ — Example change/release action when an error budget is exhausted.
