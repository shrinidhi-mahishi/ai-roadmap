# Module 01: Spec-Driven Development for AI Agents

**Scope**: SDD methodology, spec lifecycle (Spec Kit, Kiro, OpenSpec, Blitzy), control/data plane separation, task-graph scheduling, context retrieval (GraphRAG), convergence/adherence loops, token economics, durable execution, zero-trust MCP, production failure modes, and enterprise system design.

---

### What Is This?

Spec-Driven Development (SDD) inverts the traditional "code is truth" model: a version-controlled specification -- not the code -- becomes the single source of truth. Think of it like an architect's blueprint for a building: the blueprint defines what gets built, and the contractor (the AI coding agent) executes according to that blueprint. If the building deviates from the blueprint, you fix the building, not the blueprint. SDD emerged in 2025 as a direct response to "vibe coding" (term coined by Andrej Karpathy, Feb 2025), where developers describe intent to an AI and ship whatever comes back. By 2026, every major AI coding platform has shipped its own SDD flavor: GitHub Spec Kit, AWS Kiro, OpenSpec, Blitzy, BMAD, Tessl, and Google Antigravity.

Three complementary roles of a specification appear across tooling:

| Role | Description |
|------|-------------|
| **Spec-first** | Written before implementation -- defines what to build |
| **Spec-anchored** | Remains connected to code post-implementation to guide future changes |
| **Spec-as-source** | Human-edited source from which plans, tasks, and tests are regenerated |

---

## 1. System Topology & Data Flow

### Architecture Diagram

```
                          CONTROL PLANE
 +----------------------------------------------------------------------+
 |                                                                      |
 |  +-------------+    +--------------+    +----------------------+    |
 |  | Spec         |-->| Planner      |-->| Task Decomposer      |    |
 |  | Registry     |   | Agent        |   | (DAG Generator)       |    |
 |  | (Git-backed  |<--|              |<--| [P] parallel waves    |    |
 |  |  Markdown)   |   +--------------+   +----------+------------+    |
 |  +------+-------+                                 |                 |
 |         |            +--------------+             |                 |
 |         +----------->| Coordinator  |<------------+                 |
 |                      | (Supervisor) |                               |
 |                      +------+-------+                               |
 |                             |                                       |
 |              +--------------+---------------+                       |
 |              v              v               v                       |
 |  +---------------+ +---------------+ +---------------+              |
 |  | Implementor A | | Implementor B | | Verifier      |              |
 |  | (Worker Agent)| | (Worker Agent)| | Agent         |              |
 |  +-------+-------+ +-------+-------+ +-------+-------+              |
 |          |                 |                 |                       |
 +----------+-----------------+-----------------+-----------------------+
            |                 |                 |
 +----------------------------------------------------------------------+
 |                          DATA PLANE                                  |
 |                                                                      |
 |  +--------------+   +--------------+   +----------------------+     |
 |  | LLM Router   |   | Tool Proxy   |   | Context Window       |     |
 |  | (Model Tier  |   | (MCP Server) |   | Manager              |     |
 |  |  Selector)   |   |              |   | (Hot/Warm/Cold)      |     |
 |  +------+-------+   +------+-------+   +----------+----------+     |
 |         |                  |                       |                |
 |         v                  v                       v                |
 |  +--------------+   +--------------+   +----------------------+     |
 |  | Prompt Cache |   | PII Filter   |   | Semantic Cache       |     |
 |  | (KV Tensor   |   | Gateway      |   | (Embedding-based)    |     |
 |  |  Reuse)      |   |              |   |                      |     |
 |  +--------------+   +--------------+   +----------------------+     |
 +----------------------------------------------------------------------+
            |                  |                       |
 +----------------------------------------------------------------------+
 |                     PERSISTENCE LAYER                                |
 |  +--------------+   +--------------+   +----------------------+     |
 |  | specs/[br]/  |   | Event Store  |   | Knowledge Graph      |     |
 |  | openspec/    |   | (Append-only |   | (Cross-repo           |     |
 |  | AAP docs     |   |  Audit Log)  |   |  Relationships)      |     |
 |  | Temporal     |   | changes/     |   | Dynamic KG +         |     |
 |  | (optional)   |   | archive/     |   | GraphRAG             |     |
 |  +--------------+   +--------------+   +----------------------+     |
 +----------------------------------------------------------------------+
            |                  |                       |
 +----------------------------------------------------------------------+
 |                   TELEMETRY & OBSERVABILITY                          |
 |  +--------------+   +--------------+   +----------------------+     |
 |  | Structured   |   | Distributed  |   | Alert Engine          |     |
 |  | Audit Log    |   | Tracing      |   | (Step count, Token    |     |
 |  | (Immutable)  |   | (Corr. IDs)  |   |  burn, Fail rate)    |     |
 |  | Credit/line$ |   | Project Guide|   | Checklist gates      |     |
 |  +--------------+   +--------------+   +----------------------+     |
 +----------------------------------------------------------------------+
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
|-------|----------------|--------------|
| **Control plane** | Durable Markdown artifact graph + human gates: constitution/steering, requirements, design, tasks | Spec Kit constitution -> specify -> plan -> tasks; Kiro EARS requirements -> design -> tasks; OpenSpec proposal -> specs -> design -> tasks; Blitzy Technical Spec + AAP |
| **Data plane** | Agent tool loop consuming artifacts as structured context (not chat archaeology); model routing, PII filtering, caching | File edits, tests, MCP tools, sandboxes; LLM Router selects model tier |
| **Persistence** | Versioned feature dirs, delta/archive folders, approved AAPs, event history | `specs/[branch]/`, `openspec/changes/<id>/` -> `archive/`, AAP documents, Temporal workflow history |
| **Tool proxies** | MCP servers, sandboxes, Skills, issue bridges | Kiro MCP + AGENTS.md + Skills; Spec Kit `/taskstoissues` via GitHub MCP; domain-allowlisted sandboxes |
| **Telemetry** | Credit/line meters, PR Project Guides, checklist gates, org dashboards | Kiro usage/cost controls; Blitzy Project Guide per PR; Spec Kit checkbox/converge state |

### End-to-End Request Flow

**Step 1 -- Spec Ingestion.** A developer commits a Markdown spec to the Spec Registry (Git-backed). The spec defines objective, scope, requirements, edge cases, and acceptance criteria using EARS notation. Spec Kit runs `/speckit.constitution` (if missing) then `/speckit.specify`; Kiro Spec mode blocks coding until requirements/design/tasks exist; OpenSpec starts at Propose; Blitzy begins from codebase onboarding + Technical Spec.

**Step 2 -- Clarify / Quality Gate.** Ambiguities become `[NEEDS CLARIFICATION]` markers, not silent guesses. Spec Kit `/speckit.clarify` asks up to 5 targeted questions per run; Kiro EARS + automated contradiction checks flag gaps; Blitzy rejects incomplete AAPs with "TBD".

**Step 3 -- Plan & Task Graph.** Control plane emits design + dependency-ordered tasks (Setup -> Foundational -> per-user-story -> Polish). Tasks are marked with `[P]` for parallel waves. Optional `/speckit.analyze` provides read-only consistency checks across `spec.md` / `plan.md` / `tasks.md`.

**Step 4 -- Human Checkpoint.** No phase advances until the current one is validated. Checklist unchecked items block implement; Blitzy requires AAP approval before generation; Kiro reviews specs before coding.

**Step 5 -- Data-Plane Execution.** Implement agents load only the high-signal Markdown slice for the current task (Anthropic: finite attention budget; JIT identifiers over corpus stuffing). The LLM Router selects the model tier (Haiku for simple, Sonnet for standard, Opus for complex). Tool proxies open sandboxes (clone -> execute -> tear down), call MCP tools, run tests. Independent task nodes fan out in parallel.

**Step 6 -- Brownfield Context Retrieval.** For existing codebases, a Dynamic Knowledge Graph + GraphRAG traversal bounds blast radius before planning. Forward edges identify downstream impact; reverse edges find dependents. Kiro regenerates `structure.md` / `tech.md` / `product.md` from the repo.

**Step 7 -- Verification.** The Verifier Agent checks each Implementor's output against the sub-spec. This is the most underused pattern in SDD -- assigning a separate agent to verify rather than trusting self-verification. Blitzy spec-adherence agents continuously check generated work against approved requirements.

**Step 8 -- Converge.** Spec Kit `/speckit.converge` (append-only gap assessment -> Convergence tasks -> re-implement) closes the loop until Converged. OpenSpec archives deltas into main specs under a dated `changes/archive/`.

**Step 9 -- Persist & Audit.** Every state transition is recorded. Blitzy emits Project Guide on the PR; Spec Kit leaves `tasks.md` + optional GitHub issues as the trail. Credits/lines consumed, sandbox lifecycle events, gate outcomes, and PR mappings land in dashboards.

**Message style**: synchronous, human-gated, artifact-passing pipelines. Parallelism is at the task-graph layer, not a peer A2A mesh.

---

## 2. Core Mechanics & Algorithms

### 2.1 The SDD State Machine

SDD enforces a strict phase-gate state machine. The spec is never stale -- if implementation changes diverge from the spec, the system transitions back to SPECIFY. This prevents the **Assumption Propagation Problem** where a bad assumption multiplies across files and tasks exponentially.

```
                    +------------------+
                    |   CONSTITUTION   |----------+
                    +--------+---------+          | (immutable principles
                             |                    |  evaluated every phase)
                             v                    |
                    +------------------+          |
           +------->    SPECIFY       |<----------+
           |        +--------+---------+          |
           |                 | human validates    |
           |                 v                    |
           |        +------------------+          |
           |        |    CLARIFY*      |--answers->spec.md
           |        +--------+---------+          |
           |                 v                    |
           |        +------------------+          |
           |        |      PLAN        |----------+
           |        +--------+---------+          |
           |                 | human validates    |
           |                 v                    |
           |        +------------------+          |
           |        |   CHECKLIST*     |--gate----+
           |        +--------+---------+          |
           |                 v                    |
           |        +------------------+          |
           |        |     TASKS        |--[P] DAG-+
           |        +--------+---------+          |
           |                 | human validates    |
           |                 v                    |
           |        +------------------+          |
           |        |    ANALYZE*      |--read-only
           |        +--------+---------+          |
           |                 v                    |
           |        +------------------+          |
           |        |   IMPLEMENT      |--dep order
           |        +--------+---------+          |
           |                 v                    |
           |        +------------------+          |
           +--------|    CONVERGE      |--append gaps
   spec-code drift  +------------------+
   detected              Converged | Convergence tasks -> re-implement
         * = optional
```

**Gate function**: At each gate, validation is: `valid(phase_output, spec) -> {PASS, REJECT(reason)}`. REJECT returns control to the current or an earlier phase with the reason, creating a feedback loop that converges on correctness.

### 2.2 Canonical SDD Process Topologies

| Stack | Process Shape | Orchestration Model |
|-------|--------------|---------------------|
| **GitHub Spec Kit** | Constitution -> Specify -> Clarify -> Plan -> Checklist -> Tasks -> Analyze -> Implement -> Converge | Agent-driven slash-command pipeline; works with 38 agent integrations; 130K+ GitHub stars, 270+ contributors |
| **AWS Kiro** | Requirements (EARS) -> Design -> Tasks -> implement with parallel agents; hooks on save/create/delete | Spec mode delays coding until requirements/design/tasks exist; tasks run in dependency-ordered parallel waves |
| **OpenSpec** | Explore (optional) -> Propose -> Apply -> Archive; delta specs (ADDED/MODIFIED/REMOVED) | Lightweight agreement layer; "enablers, not gates" -- wrong design mid-flight -> edit `design.md` and continue |
| **Blitzy** | Onboard -> Technical Spec -> Generation prompt -> AAP review -> multi-agent execute -> validate -> Project Guide / PR | Orchestrator sequences specialized agents (architecture, impl, QA, debug, validate, integrate) |

### 2.3 EARS Notation for Acceptance Criteria

Five sentence patterns that make requirements parseable by both humans and LLMs:

| Pattern | Template | Example |
|---------|----------|---------|
| **Ubiquitous** | The system shall [action] | The system shall log every tool invocation |
| **Event-driven** | When [event], the system shall [action] | When a tool call fails, the system shall retry with backoff |
| **State-driven** | While [state], the system shall [action] | While circuit is OPEN, the system shall reject requests |
| **Unwanted** | If [condition], the system shall [action] | If PII is detected, the system shall redact before forwarding |
| **Optional** | Where [feature], the system shall [action] | Where caching is enabled, the system shall check semantic cache |

### 2.4 Task-Graph Scheduling Algorithm

1. Parse `tasks.md` / AAP into nodes with `depends_on` edges and optional `[P]` / independent flags.
2. Topologically sort (Kahn or DFS). **Complexity**: O(V+E) for sort; detecting cycles O(V+E).
3. Emit **waves**: all ready nodes with satisfied deps and mutual file/interface independence -> parallel wave; else serialize.
4. Gate: Spec Kit implement must not flip checklist markers; unchecked checklist items prompt before proceed.

**Invariant (parallel safety)**: two `[P]` tasks may run concurrently only if their write sets are disjoint (different files/interfaces). Overlap without locking -> merge conflicts / duplicated assumptions.

### 2.5 Orchestration Pattern Selection Algorithm

The choice of orchestration pattern follows a decision tree based on task characteristics:

```
Is the task decomposable into independent subtasks?
+-- YES: Are subtasks genuinely parallel (no shared state)?
|   +-- YES: Fan-Out/Fan-In
|   |   Complexity: O(max(T_i)) latency, O(sum(T_i)) cost
|   +-- NO: DAG-Based
|       Complexity: O(critical_path) latency
+-- NO: Does it require dynamic replanning?
    +-- YES: Is there a natural supervisor role?
    |   +-- YES: Supervisor-Worker with ReAct inner loop
    |   +-- NO: Handoff / Peer-to-Peer
    +-- NO: Plan-and-Execute (single agent)
        Complexity: O(n) latency, cheapest model for execution
```

### 2.6 The Reliability Multiplication Problem

For a pipeline of n sequential steps, each with independent reliability p:

```
P(end-to-end success) = p^n
```

At p = 0.85 and n = 10: `0.85^10 = 0.197` -- roughly **20% end-to-end success**.

**Why verification agents matter**: By adding a verification step after each implementation step, the effective per-step reliability increases from p to `1 - (1-p)(1-p_v)` where p_v is the verifier's detection rate.

With p = 0.85 and p_v = 0.90:

```
p_effective = 1 - (0.15)(0.10) = 0.985
P(e2e, n=10) = 0.985^10 = 0.860
```

The system goes from **20% to 86%** end-to-end reliability by adding verification agents. This is why the Coordinator-Implementor-Verifier pattern is the most underused but most impactful SDD pattern.

### 2.7 Context Window Degradation Model

Factory AI finding: agents lose coherent access to original task objectives at approximately the **60% context utilization mark**. The degradation is not linear -- it follows a cliff pattern:

```
Coherence
  100% |################
       |                ####
       |                    ####
       |                        ##
   50% |                          ##
       |                            #
       |                             ##
       |                               ######
    0% |------------------------------------
       0%    20%    40%    60%    80%    100%
                  Context Utilization
```

**Three-tier mitigation** (hot/warm/cold):
- **Hot**: Last 10 turns, verbatim. Full fidelity.
- **Warm**: Turns 11-40, rolling summary. Key facts preserved, phrasing compressed.
- **Cold**: Everything earlier, broad summary. Only objectives and critical constraints.

Benchmarks show 26-54% reduction in peak token usage. **But**: Factory AI found that compressing to the 99th percentile causes agents to re-fetch forgotten information, triggering additional tool calls. **The cost of forgetting exceeds the cost of remembering.**

### 2.8 Dynamic Knowledge Graph (GraphRAG)

Bidirectional graph traversal for brownfield context retrieval on a Dynamic Knowledge Graph (control flow, call graphs, inheritance, module dependencies):

1. Seed nodes from change-spec path/symbols.
2. **Forward** edges -> downstream blast radius; **reverse** -> dependents.
3. Bound hop depth / node budget to protect the attention window.

This is structurally superior to flat RAG (cosine similarity over chunks) because it preserves **relationship semantics** -- inheritance, call graphs, module dependencies -- that cosine similarity destroys.

**Complexity**: BFS/DFS to depth d is O(b^d) worst-case fan-out; production systems must hard-cap nodes/tokens per planning call.

### 2.9 Key Invariants & Convergence

| Invariant | Enforcement |
|-----------|-------------|
| Spec precedes HOW/stack in specify phase | Templates forbid premature tech in `spec.md` |
| No silent assumptions | `[NEEDS CLARIFICATION]` mandatory |
| Analyze is non-mutating | `/speckit.analyze` never edits files |
| Converge is append-only | Gaps -> Convergence tasks; never delete code |
| AAP completeness | "TBD" / incomplete plans rejected at review |
| Constitution/rules bind all phases | Spec Kit constitution; Blitzy Rules; Kiro steering |
| Atomic task size | Prefer 1-2 files / task; quality collapses at 10+ files |

**Convergence property**: Desired = Observed when converge reports Converged AND adherence/Project Guide map every requirement to artifacts. The converge loop is a desired-state controller implemented via Markdown append rather than an orchestrator DB.

**Canonical failure motivator**: Tests can pass while intent fails. Example: notification migration where a disabled-email user still gets mailed -- tests verified local correctness, not preserved preferences/intent. Verification is not validation.

---

## 3. Token Economics & NFR Analysis

### 3.1 The 2026 Cost Paradox

Token prices fell 80% between 2025-2026, yet enterprise AI bills went up. Enterprise LLM API spend passed $8.4B in 2025, on track to double. A single agentic session consumes 1-3.5M tokens per task -- 50-500x the footprint of a traditional chat interaction.

### 3.2 SDD Tool Pricing

**Kiro credit path:**

| Tier | Credits Included | List Price |
|------|-----------------|------------|
| Free | 50 | $0 |
| Pro | 1,000 | $20/user/mo |
| Pro+ | 2,000 | $40 |
| Pro Max | 5,000 | $100 |
| Power | 10,000 | $200 |

Add-on credits (paid tiers): **$0.04/credit**. Model multipliers vs Auto 1.0x: `deepseek-3.2` 0.25x; `claude-haiku-4.5` 0.4x; `claude-sonnet-*` 1.3x; `claude-opus-*` ~2.0-2.2x; `gpt-5.6-sol` 4.4x; `claude-fable-5.1` 6.0x.

**Blitzy line path:** Onboard: **$0.10/line**; Generate: **$0.20/line**. Commercial ~$500K/yr; Enterprise ~$5M/yr; POC $50K / 2 mo.

**Spec Kit / OpenSpec:** Process tooling is free; cost = whatever the attached coding agent bills. Spec Kit supports 38 agent integrations without lock-in.

### 3.3 Cost Formulas -- $ per 1k runs

Define one **feature run** = specify -> plan -> tasks -> implement (one user story wave) -> optional converge pass.

**Agent token path (Spec Kit / OpenSpec + agent):**

| Phase | Input Tokens | Output Tokens |
|-------|-------------|---------------|
| Specify+clarify | 8,000 | 3,000 |
| Plan+tasks | 12,000 | 4,000 |
| Implement wave | 40,000 | 12,000 |
| Converge | 15,000 | 4,000 |
| **Total / run** | **75,000** | **23,000** |

Using Sonnet-class rates ($3.00 / 1M input, $15.00 / 1M output):

```
Cost_1run = (75k/1M)(3) + (23k/1M)(15) = $0.225 + $0.345 = $0.57
Cost_1k_runs = ~$570
```

**Three-tier routing (70% Haiku, 20% Sonnet, 10% Opus) with 2M tokens/run:**

```
Naive (all Opus):   1000 * ((1.5M * $15 + 0.5M * $75) / 1M) = $60,000
Three-tier routed:  $2,240 + $2,400 + $6,000 = $10,640  (82% reduction)
With prompt cache:  ~$5,500  (91% reduction from naive)
With semantic cache: ~$3,800 (94% reduction from naive)
```

**Kiro credit path (1k runs at add-on rate):**

| Scenario | Formula | $ / 1k runs |
|----------|---------|-------------|
| Haiku-class (0.4x) | 1000 x 20 x 0.4 x $0.04 | **$320** |
| Auto/Sonnet (1.3x) | 1000 x 20 x 1.3 x $0.04 | **$1,040** |
| Opus-class (2.1x) | 1000 x 20 x 2.1 x $0.04 | **$1,680** |

### 3.4 Latency SLA Targets

| Tier | Target | What It Covers | Mitigations |
|------|--------|---------------|-------------|
| **p50** | <=45s control-plane turn | Single-agent Markdown rewrite | Slim templates; Skills over constitution bloat; Haiku/0.4x routing |
| **p95** | <=8 min implement task (1-2 files) | Tool loop + tests | Stage phases; atomic tasks; JIT retrieval |
| **p99** | <=25 min user-story wave / sandbox job | Parallel wave + CI | Dependency waves; sandbox tear-down per task; fail closed on credit exhaustion |

Additional component-level targets:
- Checkpoint write p50: 0.3ms, p95: 0.8ms
- Hot cache read: ~4 microseconds
- Policy decision p99: <=20ms
- LLM proxy overhead p95: 8ms at 1K RPS

### 3.5 Throughput & Back-Pressure

| Mechanism | Behavior |
|-----------|----------|
| **Kiro** | No daily/weekly rate limits; back-pressure = credit exhaustion -> pause until add-on or monthly reset |
| **Blitzy** | Jobs not cancelable once submitted; consume assigned quota on submit |
| **Spec Kit / OpenSpec** | No platform TPM/RPM -- limits = underlying agent |

**Capacity planning**: Size on artifact review bandwidth + credit/line budgets, not only model RPM. Token-bucket per tenant on implement waves; queue excess tasks; shed optional analyze/clarify under load.

### 3.6 NFR Trade-Offs

| NFR | Target / Posture | Trade-off |
|-----|-----------------|-----------|
| **Availability** | Control plane = git + Markdown (high); data plane = vendor-dependent | Spec Kit offline/firewall for air-gapped orgs; Blitzy inbound-only VPC |
| **RPO** | Spec artifacts in git -> RPO ~ last push (seconds-minutes) | Chat-only workflows have worse RPO (lost context) |
| **RTO** | Restore branch + `specs/` -> minutes; rehydrate sandboxes -> minutes | Blitzy in-flight jobs not cancelable -- design RTO around job boundaries |
| **Compliance** | Blitzy: SOC 2 Type II + ISO 27001; Kiro: IAM/SSO, IP indemnity | Heavier governance raises ops complexity and $ |
| **Quality vs cost** | EPAM: expect 60-80% generated code usable after review | Cheaper/faster models raise review load |
| **Context vs autonomy** | Externalize specs to free coding context | Large constitutions raise token $ and lower quality |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

| Pattern | SDD Mapping |
|---------|------------|
| **Workflow as artifacts** | Feature state under `specs/[branch]/`; OpenSpec `changes/<id>/` -> dated `archive/`; Blitzy AAP = approved execution blueprint |
| **Checkpoints** | Human gates: plan/analyze/checklist; AAP approval; Kiro spec review before code |
| **Replay** | Re-run `/speckit.implement` from remaining unchecked tasks; converge appends new tasks without wiping history |
| **Ephemeral workers** | Kiro Web: sandbox per task -> clone -> execute -> tear down; domain allowlists |
| **Orchestrator upgrade path** | Wrap task DAG in Temporal/Kafka: each task = activity with idempotency key = `(feature_id, task_id, attempt)` |

**Temporal dominates durable execution**: 3,000+ paying customers (Nvidia, Netflix, OpenAI). Four guarantees: (1) State survives crashes; (2) Exactly-once execution via idempotency keys; (3) Suspend/resume across arbitrary delays; (4) Deterministic replay for time-travel debugging.

**Key distinction**: Checkpointers are NOT durable execution. Durable execution flips the model -- the runtime owns retry, resume, and dedup; the developer writes ordinary code.

### 4.2 Failure Taxonomy

| Class | Examples in SDD | Handling |
|-------|----------------|---------|
| **Transient** | Model 429/5xx, sandbox boot flake, MCP timeout | Exponential backoff + jitter; circuit breaker; retry same idempotency key |
| **Permanent** | Invalid AAP ("TBD"), constitution violation, missing clarification | Fail closed; return to control plane; do not retry implement |
| **Poison pill** | Task that always fails (bad fixture, contradictory EARS, infinite edit loop) | Max attempts -> DLQ; quarantine task; require human rewrite |
| **Semantic / intent** | Tests green, preference/invariant violated | Property tests, acceptance criteria, adherence agents, converge -- not more unit retries |
| **Silent** | Tool returns HTTP 200 with empty payload, model returns plausible but wrong output | Output schema validation, semantic assertion checks, verifier agent |
| **Cascading** | Agent A passes degraded context to Agent B | Cross-agent assertion contracts, end-to-end integration checks; halt pipeline, roll back |
| **Idempotency** | Re-drive implement after crash | Key on task id + content hash; checklist must not auto-flip |

### 4.3 Circuit Breaker: Closed -> Open -> Half-Open

Apply per **dependency** (model endpoint, MCP tool, sandbox pool) -- not per whole pipeline:

1. **Closed** -- traffic flows; count consecutive failures / error rate in sliding window.
2. **Open** -- after threshold (e.g., 5 failures / 60s): short-circuit calls; enqueue tasks or fail fast.
3. **Half-open** -- after cool-down, allow probe task; success -> closed; failure -> open.

Do not open the breaker on permanent schema/policy failures -- those are not dependency health signals.

### 4.4 Fallback Chains

```
primary model (quality) -> secondary (cheaper/faster, e.g. Haiku 0.4x) -> deterministic fallback
```

Deterministic fallbacks for SDD: (1) stop implement and open clarify/checklist; (2) convert remaining tasks to GitHub issues via MCP for human execution; (3) apply only scaffolding from templates without LLM.

### 4.5 Zero-Trust MCP Architecture

MCP's native specification mandates neither cryptographic identity verification nor access control bounds. MCP ships with **no built-in access controls**.

**Four controls for Zero-Trust MCP:**

1. **Authenticate every MCP server** -- mutual TLS or signed tokens between IDE/orchestrator and tool host.
2. **Treat retrieved code/comments/docs as data, not instructions** -- prompt-injection posture (OWASP LLM/Agentic Top 10).
3. **Network isolation** -- domain allowlists in sandboxes; Blitzy inbound-only VPC never initiates outbound.
4. **Path exclusion** -- `.blitzyignore`-style deny lists for secrets and irrelevant trees.

**Per-invocation authorization (Level 4 maturity)**: Every tool invocation carries its own signed token validated against current policy. Agent identified via SPIFFE ID; token represents "User X, via Agent Y" with scoped permissions. Policy via external PDP (OPA/Cerbos).

### 4.6 Tool-Level RBAC (Least Privilege)

| Role | Allowed Tools | Denied |
|------|--------------|--------|
| Spec author | Read repo, write `specs/**` / `openspec/**` | Prod deploy, secret exfil channels |
| Implement agent | Edit paths in task write-set, run tests | Broad `rm -rf`, unrestricted egress |
| Adherence/QA agent | Read-only + test runner | Merge to main |
| Orchestrator | Enqueue tasks, flip non-checklist metadata | Customer raw secrets beyond injected env |

Kiro warns: agents may exfiltrate secrets via code, logs, or external requests -- inject **least-privilege** secrets only. Escalate HITL for auth/session, migrations, external integrations, regulatory logic.

### 4.7 PII Pipeline: Detection -> Redaction -> Audit

1. **Detect** -- scan specs, prompts, tool payloads, logs for PII/secrets before model/MCP egress.
2. **Redact** -- replace with stable tokens (`[PII:email:3f2a]`) in model context; keep mapping in a sealed vault.
3. **Audit** -- append-only event: `{correlation_id, actor, artifact_hash, tool, decision, pii_tokens_redacted}` to immutable store (WORM object lock / hash-chained log).

Three architectural approaches (composable):
- **Gateway-layer**: PII detection before any model request (TrueFoundry, Gravitee).
- **Dynamic routing**: PII detected -> reroute to on-prem model instead of cloud.
- **Tool-level DLP**: Redaction on every MCP tool call (Strac).

### 4.8 Immutable Audit & Chain-of-Custody

- OpenSpec dated `changes/archive/` = completed-change audit trail.
- Blitzy Project Guide on each PR maps work -> requirements.
- Spec Kit `tasks.md` + optional issues = execution trail.
- Enterprise extension: sign artifact hashes at each gate so agent decisions have chain-of-custody for regulated reviews.

Human oversight models: **HITL** (mandatory AAP / checklist) vs **HOTL** (QA agents + hooks while humans merge).

---

## 5. Production Enterprise Code

Runnable Python demonstrating retries with exponential backoff + jitter, circuit breaker (closed -> open -> half-open), fallback chain, structured logging with correlation IDs, graceful degradation, and PII redaction.

```python
#!/usr/bin/env python3
"""SDD implement-wave executor with enterprise resilience primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emits JSON lines with agent context for SIEM/observability."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "feature_id": getattr(record, "feature_id", None),
            "task_id": getattr(record, "task_id", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "provider": getattr(record, "provider", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = build_logger("sdd.executor")


def log_extra(**kwargs: Any) -> dict[str, Any]:
    return kwargs


# ---------------------------------------------------------------------------
# PII redaction pipeline
# ---------------------------------------------------------------------------

_PII_PATTERNS = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")),
    ("credit_card", re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    """Replace PII with hashed placeholders; return (redacted, audit_trail)."""
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m, _label=label):
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    POISON = "poison"


class AgentError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed -> open -> half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    window_sec: float = 60.0
    state: BreakerState = BreakerState.CLOSED
    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def _prune(self, now: float) -> None:
        self.failures = [t for t in self.failures if now - t <= self.window_sec]

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state is BreakerState.OPEN:
            assert self.opened_at is not None
            if now - self.opened_at >= self.recovery_timeout_sec:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures.clear()
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self.failures.append(now)
        if self.state is BreakerState.HALF_OPEN:
            self.state = BreakerState.OPEN
            self.opened_at = now
            return
        if len(self.failures) >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = now


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay_sec: float = 0.5
    max_delay_sec: float = 20.0
    poison_after: int = 5

    def delay(self, attempt: int) -> float:
        # AWS-style full jitter: sleep ~ U(0, min(cap, base * 2^attempt))
        ceiling = min(self.max_delay_sec, self.base_delay_sec * (2**attempt))
        return random.uniform(0.0, ceiling)


# ---------------------------------------------------------------------------
# Model / tool providers + fallback chain
# ---------------------------------------------------------------------------

class ImplementProvider(Protocol):
    name: str

    def run_task(self, task: "Task", correlation_id: str) -> "TaskResult":
        ...


@dataclass(frozen=True)
class Task:
    feature_id: str
    task_id: str
    write_paths: tuple[str, ...]
    prompt: str
    idempotency_key: str


@dataclass(frozen=True)
class TaskResult:
    ok: bool
    provider: str
    files_touched: tuple[str, ...]
    detail: str
    degraded: bool = False


class PrimaryCodingAgent:
    """Simulates a frontier model. Fails transiently a configurable number of times."""
    name = "primary"

    def __init__(self, fail_times: int = 0) -> None:
        self._remaining_failures = fail_times

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        # Redact PII from the prompt before sending to model
        redacted_prompt, pii_audit = redact_pii(task.prompt)
        if pii_audit:
            LOG.info("pii_redacted", extra=log_extra(
                correlation_id=correlation_id,
                pii_count=len(pii_audit),
            ))
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AgentError("model 503", FailureKind.TRANSIENT)
        return TaskResult(True, self.name, task.write_paths, "implemented")


class SecondaryCodingAgent:
    """Cheaper/faster fallback model."""
    name = "secondary"

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        return TaskResult(True, self.name, task.write_paths, "implemented-secondary")


class DeterministicFallback:
    """Graceful degradation: emit human-issue stub without LLM."""
    name = "deterministic"

    def run_task(self, task: Task, correlation_id: str) -> TaskResult:
        issue = {
            "title": f"[SDD fallback] {task.feature_id}/{task.task_id}",
            "body": task.prompt,
            "idempotency_key": task.idempotency_key,
            "correlation_id": correlation_id,
        }
        return TaskResult(
            ok=True,
            provider=self.name,
            files_touched=(),
            detail=json.dumps(issue),
            degraded=True,
        )


# ---------------------------------------------------------------------------
# Executor with retries, circuit breaker, fallback chain
# ---------------------------------------------------------------------------

@dataclass
class ImplementWaveExecutor:
    providers: list[ImplementProvider]
    breaker: CircuitBreaker
    retry: RetryPolicy
    sleep_fn: Callable[[float], None] = time.sleep

    def execute(self, task: Task, correlation_id: str | None = None) -> TaskResult:
        cid = correlation_id or str(uuid.uuid4())
        for provider in self.providers:
            if not self.breaker.allow():
                LOG.warning("circuit_open_skip", extra=log_extra(
                    correlation_id=cid, task_id=task.task_id,
                    breaker_state=self.breaker.state.value,
                ))
                continue

            for attempt in range(self.retry.max_attempts):
                LOG.info("attempt_start", extra=log_extra(
                    correlation_id=cid, task_id=task.task_id,
                    attempt=attempt, provider=provider.name,
                ))
                try:
                    result = provider.run_task(task, cid)
                    self.breaker.record_success()
                    return result
                except AgentError as exc:
                    if exc.kind is FailureKind.PERMANENT:
                        break  # next provider
                    if exc.kind is FailureKind.POISON or attempt + 1 >= self.retry.poison_after:
                        self.breaker.record_failure()
                        break
                    self.breaker.record_failure()
                    self.sleep_fn(self.retry.delay(attempt))

        # All providers exhausted -- deterministic last resort
        det = next((p for p in self.providers if p.name == "deterministic"), None)
        if det is not None:
            LOG.warning("graceful_degradation", extra=log_extra(
                correlation_id=cid, task_id=task.task_id,
            ))
            return det.run_task(task, cid)
        raise AgentError("all providers failed", FailureKind.POISON)


def demo() -> None:
    task = Task(
        feature_id="feat-0142",
        task_id="T3.2",
        write_paths=("src/notify/prefs.py",),
        prompt="Preserve email opt-out for user john@example.com when migrating.",
        idempotency_key="feat-0142:T3.2:sha256:abc",
    )
    executor = ImplementWaveExecutor(
        providers=[
            PrimaryCodingAgent(fail_times=2),
            SecondaryCodingAgent(),
            DeterministicFallback(),
        ],
        breaker=CircuitBreaker(failure_threshold=3, recovery_timeout_sec=0.01),
        retry=RetryPolicy(max_attempts=4, base_delay_sec=0.01, max_delay_sec=0.05),
        sleep_fn=lambda _: None,
    )
    result = executor.execute(task, correlation_id="corr-demo-001")
    print(json.dumps({
        "ok": result.ok,
        "provider": result.provider,
        "degraded": result.degraded,
        "detail": result.detail,
    }))


if __name__ == "__main__":
    demo()
```

Run: `python3 01-sdd-executor.py`. Expected path: PII in the prompt is redacted before model call; primary fails twice (transient), then succeeds. Raise `fail_times` higher to exercise secondary or deterministic fallback.

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Repo Brownfield Change with Blast-Radius Control

**Problem statement**: A regulated payments org maintains 12 services (~8M LOC). Product needs a cross-cutting "notification preference" fix: agents previously shipped green tests that emailed users who opted out. Constraints: SOC 2 evidence, no vendor training on source, p95 implement task <=8 min for 1-2 file tasks, HITL on auth and messaging paths, budget either IDE-credit scale or six-figure platform.

**Proposed architecture:**

```
+-------------+   delta specs    +------------------+   GraphRAG slice   +-------------+
| OpenSpec /  |----------------->| Knowledge graph  |------------------>| Plan+tasks  |
| Spec Kit    |   constitution   | (call/deps)      |   hop-capped      | human gate  |
+-------------+                  +------------------+                   +------+------+
                                                                               |
                     +-----------------------------+--------------------------+
                     v                             v
              +--------------+             +--------------+
              | Sandbox wave |             | Adherence +  |
              | MCP allowlist|             | property tests|
              +------+-------+             +------+-------+
                     +-------------+---+-----------+
                                   v
                          +----------------+
                          | PR + Project   |
                          | Guide / archive|
                          +----------------+
```

**Trade-off matrix:**

| Dimension | Alt 1: Spec Kit + existing agents | Alt 2: OpenSpec deltas + agents | Alt 3: Blitzy platform |
|-----------|----------------------------------|--------------------------------|----------------------|
| **Cost** | Agent tokens only (~$570/1k runs) | Same token shape; less rewrite churn | $0.10/$0.20 per line + ~$500K/yr |
| **Latency** | Strong for atomic tasks | Fast incremental edits | High coordination overhead |
| **Ops complexity** | Low-medium (Markdown + CI) | Lowest CLI footprint | High (VPC/on-prem, onboarding) |
| **Security** | Offline/firewall capable; DIY audit | Lightweight archive trail | SOC2/ISO, air-gap, inbound-only VPC |
| **Scalability** | Bounded by review + agent RPM | Excellent for many small deltas | ~3.6k agents / 229K-LOC demonstrated |

**Decision rationale**: Year-one recommendation: **Alt 2 (OpenSpec) + Spec Kit constitution** for org rules, GraphRAG only on the services touched by notification prefs, HITL checklist on messaging, property tests for opt-out invariant. Choose Blitzy (Alt 3) only if multi-repo autonomy + compliance packaging outweighs lock-in and line economics.

---

### Scenario 2: Greenfield Platform Team with Parallel Agents and Credit Budgets

**Problem statement**: A 40-engineer platform group builds a new internal developer portal (greenfield). They want Spec mode (no code before requirements/design/tasks), parallel implement waves, property-based tests, and spend controls. Target: <=$1,040 / 1k Sonnet-equivalent feature runs, no surprise locks mid-sprint.

**Proposed architecture:**

```
+--------------+  EARS+design+tasks  +-------------+  waves [P]  +--------------+
| Kiro Spec    |-------------------->| Task graph  |------------>| Parallel     |
| mode + Auto  |  multipliers 0.25x  | scheduler   |             | sandboxes    |
+------+-------+                     +-------------+             +------+-------+
       | hooks (secret scan)                                            |
       v                                                                v
+--------------+                                              +----------------+
| Steering     |                                              | Circuit+credit |
| structure/   |                                              | breaker/meter  |
| tech/product |                                              | -> GH issues   |
+--------------+                                              +----------------+
```

**Trade-off matrix:**

| Dimension | Alt 1: Kiro Spec mode + credits | Alt 2: Spec Kit + Copilot/Claude/Codex | Alt 3: Chat-only coding agents |
|-----------|-------------------------------|---------------------------------------|------------------------------|
| **Cost** | Pro $20-Power $200 + $0.04/credit; ~$1,040/1k runs | Agent tokens ~$570/1k; no credit lock-in | Hidden context-rot cost; rework dominates |
| **Security** | Sandboxes, IAM/SSO, secret warnings | Org catalogs, CI/Architecture Guard | Weak gates; prompt injection via pasted code |
| **Scalability** | No rate limits; credit back-pressure | Scales with agent contracts | Collapses beyond 10-20 min tasks |

**Decision rationale**: **Alt 1 (Kiro)** when the team wants integrated Spec mode, parallel agents, hooks, and explicit credit multipliers with Auto routing. **Alt 2 (Spec Kit)** if agent diversity / anti-lock-in dominates. **Reject Alt 3**: the notification-intent failure mode and context-window degradation are exactly what SDD externalization prevents.

---

### Common Failure Modes

| Failure Mode | Detection | Mitigation |
|-------------|-----------|------------|
| Tests pass, intent fails (canonical SDD motivator) | Property-based tests, acceptance criteria, adherence agents | Use EARS notation; converge/adherence loops; verifier agents |
| Context window degradation (cliff at ~60%) | Quality benchmarks at turn 10, 20, 30 | Externalize specs to Markdown; stage implement phases; JIT retrieval |
| Infinite / unbounded execution loops | Step count > 50; repeated tool+arg hash | max_turns; credit/quota caps; dependency-ordered tasks with human gates |
| Spec-code drift / state divergence | `/speckit.analyze` (pre) + `/speckit.converge` (post) | Spec-adherence agents; bidirectional sync; archive merges |
| Hallucinated assumptions / tool parameters | `[NEEDS CLARIFICATION]` markers; EARS contradictions | `/speckit.clarify`; property-based tests; formal specs as instructions |
| Cascading timeouts / multi-agent failure | Sandbox lifecycle events; task timeout metrics | Smaller tasks; sequential phases; per-task sandbox teardown |
| Instruction bloat / context rot | Constitution token count monitoring | Extract reusable guidance into Skills; load detail only when needed |
| Credit/quota exhaustion | Credit meter alerts at 80% | Fail closed (pause, not retry storm); deterministic GitHub-issue fallback |

---

### Key Takeaways for Interviews

- **SDD prevents the Assumption Propagation Problem.** A bad assumption in vibe coding multiplies across files exponentially. SDD's phase-gate state machine catches drift at each boundary. The spec is the single source of truth -- not the code.

- **Verification is not validation.** Tests can pass while intent fails. The notification migration vignette proves this: disabled-email user still mailed. Add acceptance criteria, property tests, and converge/adherence loops.

- **Context is a scarce NFR.** Agents hit a coherence cliff at ~60% context utilization. High-signal specs + JIT retrieval beats chat archaeology. Factory AI: the cost of forgetting exceeds the cost of remembering.

- **The Coordinator-Implementor-Verifier pattern boosts reliability from 20% to 86%.** Adding verification agents after each step increases effective per-step reliability from 0.85 to 0.985 (10-step pipeline).

- **Size autonomy to blast radius.** HITL for auth/data/regulatory; HOTL for reversible low-risk tasks. Escalation guidance: raise human oversight for auth/session, data migrations, external integrations, regulatory logic.

- **When code and spec disagree, spec is authority.** Enterprise policy must be spec-as-source until a deliberate constitution amendment. Converge is append-only -- never deletes code.

- **Start with the simplest SDD tool that fits.** OpenSpec deltas for brownfield incremental changes; Spec Kit for tool-agnostic org process; Kiro for AWS-aligned greenfield with parallel agents; Blitzy for enterprise multi-repo autonomy with compliance packaging.

---

### Interview Q&A

**Q1: What is SDD and why does it matter for AI agents?**

SDD inverts code-as-truth: specifications become the primary artifact and code is generated from them. It matters because without structured specs, AI agents suffer the Assumption Propagation Problem where a bad assumption multiplies across files exponentially. SDD prevents this with phase-gate state machines, human checkpoints, and convergence loops that catch drift before it compounds.

**Q2: Explain the control plane vs data plane separation in SDD.**

The control plane is the durable Markdown artifact graph -- constitution, requirements, design, tasks -- plus human approval gates. The data plane is the coding agent's tool loop that consumes those artifacts as structured context. This separation means planning artifacts outlive individual chat sessions, agent context stays clean for coding, and multiple agents can share the same authoritative spec.

**Q3: What is the Coordinator-Implementor-Verifier pattern and why is it underused?**

It assigns a separate agent to verify each implementor's output against the sub-spec rather than trusting self-verification. It is the most impactful SDD pattern because it raises a 10-step pipeline from 20% to 86% end-to-end reliability. It is underused because teams default to self-verify, which has a reliability of only p -- not 1-(1-p)(1-p_v).

**Q4: How does context window degradation affect SDD and what are the mitigations?**

Agents hit a coherence cliff at approximately 60% context utilization -- quality drops sharply, not linearly. Mitigations: externalize plans to Markdown (SDD's core value proposition), stage implement phases (Setup+Foundational first, then stories), JIT retrieval instead of upfront corpus loading, extract reusable guidance into Skills instead of monolithic constitutions.

**Q5: Compare Spec Kit, Kiro, OpenSpec, and Blitzy.**

Spec Kit is an open-source specification/process layer that works with 38 agent integrations -- lowest lock-in, strongest for tool-agnostic teams. Kiro is AWS-aligned with EARS requirements, parallel agents, hooks, and credit-based pricing -- best for greenfield. OpenSpec uses delta specs (ADDED/MODIFIED/REMOVED) optimized for brownfield incremental changes -- lightest footprint. Blitzy is an enterprise platform with multi-agent orchestration, knowledge graphs, SOC 2 compliance, and line-based pricing -- highest autonomy but highest lock-in.

**Q6: What is the convergence property in SDD?**

Desired state = spec. Observed state = code. Converge is the reconciliation loop: Spec Kit's `/speckit.converge` compares spec to code, identifies gaps, appends Convergence tasks, and re-implements. It is append-only -- never deletes code. The process repeats until Converged. This is analogous to a Kubernetes desired-state controller but implemented via Markdown task append.

**Q7: How do you handle the "tests pass but intent fails" problem?**

This is the canonical SDD motivator. Three defenses: (1) EARS acceptance criteria that encode intent as parseable, testable statements; (2) Property-based tests that assert invariants across randomized inputs, not just example unit tests; (3) Spec-adherence agents that continuously check generated work against approved requirements across file-to-E2E levels.

**Q8: What is the cost model for SDD and how do you optimize it?**

Cost depends on the SDD tool: Kiro uses credits ($0.04/credit with model multipliers), Blitzy uses lines ($0.10-$0.20/line), Spec Kit/OpenSpec are free but agent tokens apply. Optimization: three-tier model routing (82% reduction), prompt caching (90% on cached prefix), semantic caching (31% bypass), and context window management (26-54% token reduction). The hardest optimization is sizing constitutions -- instruction bloat causes context rot.

**Q9: When would you choose OpenSpec over Spec Kit?**

Choose OpenSpec for brownfield incremental changes. Its delta specs (ADDED/MODIFIED/REMOVED) are explicitly optimized for editing existing systems without rewriting the whole system spec. Spec Kit is better when the team needs a richer orchestration pipeline with constitution, analyze, and converge commands across an existing agent toolchain.

**Q10: What durable execution patterns apply to SDD?**

SDD's durability is primarily artifact-based: feature state under `specs/[branch]/`, human gates as checkpoints, converge as replay. For production, wrap the task DAG in Temporal: each task = activity with idempotency key `(feature_id, task_id, attempt)`. Temporal adds process-death recovery, exactly-once side-effects, and deterministic replay. Key insight: SDD's converge loop is a desired-state controller implemented via Markdown append.

**Q11: What are the security concerns specific to SDD?**

Three main concerns: (1) MCP tool servers may be untrusted -- authenticate every server, treat retrieved content as data not instructions; (2) Agents may exfiltrate secrets via generated code, logs, or external requests -- inject least-privilege secrets only; (3) Context windows may contain PII from specs -- implement gateway-layer PII redaction before model calls. For regulated orgs, sign artifact hashes at each gate for chain-of-custody evidence.

**Q12: How do you prevent runaway costs in SDD?**

Three layers: (1) Credit/quota caps with fail-closed behavior (Kiro pauses on exhaustion rather than retrying blindly); (2) Circuit breakers per dependency with fallback to deterministic GitHub-issue creation; (3) Atomic task sizing (1-2 files per task) with human gates between waves. Never auto-purchase credits without policy approval. Back-pressure design: token-bucket per tenant on implement waves.

---

### Key Numbers to Memorize

```
 +---------------------------------------------------------------+
 | 60%    -- context utilization cliff (coherence drops sharply)  |
 | 20%    -- e2e success for 10 steps at 0.85/step without verify|
 | 86%    -- e2e success with verification agents (same pipeline) |
 | 60-80% -- generated code usable after review (EPAM)           |
 | 82%    -- cost reduction from three-tier model routing         |
 | 90%    -- max prompt cache discount (Anthropic)                |
 | 31%    -- queries with semantic similarity (cache opportunity) |
 | $0.04  -- Kiro add-on credit price                             |
 | $570   -- per 1k runs (Spec Kit + Sonnet-class agent tokens)  |
 | $1,040 -- per 1k runs (Kiro Sonnet add-on credits)            |
 | 38     -- agent integrations supported by Spec Kit             |
 | 130K+  -- GitHub stars for Spec Kit                            |
 | 3,600+ -- agents coordinated in Blitzy BCC case (229K LOC)    |
 | 1-2    -- files per task (atomic size for quality)             |
 +---------------------------------------------------------------+
```

### Quick Reference

```
SDD = spec is truth, code is generated expression
  Three roles: spec-first, spec-anchored, spec-as-source
  State machine: specify -> plan -> tasks -> implement -> converge
  Control plane = durable Markdown; Data plane = agent tool loop
  Convergence: desired (spec) = observed (code)

Tools:
  Spec Kit   -- open-source, 38 agents, constitution+converge
  Kiro       -- EARS, parallel agents, hooks, credit pricing
  OpenSpec   -- delta specs, brownfield, lightest footprint
  Blitzy     -- enterprise, knowledge graph, SOC2, line pricing

Key patterns:
  Coordinator-Implementor-Verifier -> 20% to 86% reliability
  GraphRAG context retrieval -> relationship-aware, hop-bounded
  Converge loop -> desired-state controller via Markdown append
  Fallback chain -> primary model -> secondary -> deterministic issue

Cost levers:
  Model routing (82%) > prompt caching (90%) > semantic cache (31%)
  Context management: hot/warm/cold (26-54% token reduction)

Security:
  Zero-Trust MCP, RBAC per tool, PII redaction pipeline
  HITL for auth/data/regulatory; HOTL for reversible tasks
```
