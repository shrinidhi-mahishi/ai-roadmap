# Module 17: LLMOps & CI/CD for AI

## What Is This?

Traditional software ships code. Traditional ML ships model binaries. LLMOps ships **behavior** -- prompts, retrieval configs, model provider settings, and evaluation thresholds -- artifacts where a single-word change in a system prompt can shift output quality more than a full model retrain ever would. Behavior has no compiler, no type checker, and no stack trace when it goes wrong. A prompt change that makes responses 20% more sycophantic produces zero errors and zero alerts. Your HTTP dashboards glow green while Twitter becomes your production alerting system.

Think of LLMOps as a **container registry**, not a chat playground. The deployable unit is not just a model checkpoint; it is a **release bundle**: `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`. Changing any field is a new release. Environment tags (`production`, `staging`, `@champion`) are **mutable pointers**, not artifacts. Serving `latest` or unpinned `main` is **not** a release. CI is fail-closed on a versioned golden suite; online eval is fail-open with coverage%. The control plane owns git, CI, registries, aliases, and flags. The data plane owns live inference -- the judge runs **off** user p99.

**85% of ML models never reach production. 30%+ of GenAI projects are abandoned after POC (Gartner 2025). 42% of companies abandoned AI initiatives in 2024-2025 due to governance gaps.** The LLM observability market alone is $2.69B in 2026, projected $9.26B by 2030.

---

## Part 1: System Topology & Data Flow

### Five-Plane Architecture

Five planes, not a single "push to prod." Collapsing any two causes the failures listed in the rightmost column.

```
                         TELEMETRY / OBSERVABILITY SINKS
         +--------------------------------------------------------------+
         |  eval experiment_id, canary vs control task metric,           |
         |  alias audit: who/prev_hash/new_hash/ticket_id,              |
         |  coverage% of online sidecar, spend-cap alerts,              |
         |  WORM: (ts, actor, promote|rollback, release tuple, exp)     |
         +--------^---------------------^------------------^------------+
                  | spans               | meters            | audit
                  |                     |                   |
+--Plane 1: CONTROL (promotion, LLM-free at request admit; judges in CI)--+
|                                                                          |
|  +----------+ +----------+ +----------+ +----------+ +---------+        |
|  | git SoT  | | CI golden| | registry | | alias/tag| | flags   |        |
|  | prompt + | | fail-    | | immutable| | staging /| | 5%      |        |
|  | schema + | | closed   | | version  | | @champion| | canary  |        |
|  | eval YAML| | miller N | | SHA/ver  | | pointer  | | kill=0  |        |
|  +----+-----+ +----+-----+ +----+-----+ +----+-----+ +----+----+       |
|       |            |            |            |            |              |
|       v            v            v            v            v              |
|  +------------------------------------------------------------------+   |
|  | PR -> gate -> register -> staging alias -> 5% canary -> promote  |   |
|  | Pin: model_id, prompt_hash, schema_hash, decoding_params,        |   |
|  |      index_gen, eval_suite_id. Tag production is a POINTER.      |   |
|  +------------------------------------------------------------------+   |
+------------------------------+-------------------------------------------+
                               | pinned bundle + alias / flag variation
                               v
+--Plane 2: DATA (live inference, judge OFF user p99)-----------------------+
|                                                                            |
|  flag SDK (local) -> sticky thread_id -> resolved bundle -> model_id      |
|                   -> prompt_hash in prefix -> schema_hash decoder          |
|                   -> index_gen retrieve                                    |
|                                                                            |
|  +-- Plane 3: TOOL PROXIES (least privilege, not a prod Hub token) -----+ |
|  | MLflow / UC / W&B / Hub / SageMaker / Vertex / GitLab registry APIs  | |
|  | MCP behind gateway PEP:  eval.run | registry.set_alias | promote_tag | |
|  |   eval.run: staging suite only for assistants                        | |
|  |   set_alias / promote_tag: human + break-glass -- NEVER the model    | |
|  |   Identity = verified token / RunContext. No token passthrough       | |
|  +----------------------------------------------------------------------+ |
|  Online judge = sidecar sampler. Spend cap pauses evaluator, not agent.   |
+------+--------+----------+------------+----------------------------------+
       |        |          |            |
+--Plane 4: PERSISTENCE (commits != serving path; rollback = retag)--------+
|                                                                           |
|  +-----------+ +------------+ +----------+ +-----------+                  |
|  | Artifact  | | Experiment | | Eval     | | Hub / git |                  |
|  | store:    | | DB: runs,  | | snapshots| | commits:  |                  |
|  | weights,  | | traces,    | | suite_id | | prompt    |                  |
|  | LoRA, URI | | params     | | @ as_of  | | SHA       |                  |
|  +-----------+ +------------+ +----------+ +-----------+                  |
|  Alias `production` is mutable. Serving reads alias -> logs the tuple.    |
|  GC retains last N production versions. Never delete what prod names.     |
+---------------------------------------------------------------------------+
```

| Plane | Owns | LLM-Free? | Failure If Coupled |
|---|---|---|---|
| **Control** | git, CI gate, register, alias, flags, MCP allowlist | Yes at admit; judges run offline in CI | Playground string in prod; judge on user p99 |
| **Data** | Flag resolve, inference, tools | No (untrusted stream) | Letting the model pick `alias` or skip the gate |
| **Tool Proxies** | Registry SDKs + MCP promote tools behind gateway | Yes for authz | Omnibus `set_alias(production)` from an assistant |
| **Persistence** | Artifacts, experiments, snapshots, Hub commits | Yes | Treating `latest` as a replay key |
| **Telemetry** | experiment_id, canary metric, alias WORM | Yes | Logging golden PII / prompt bodies "for debug" |

### Canonical 7-Step Path (How a Change Becomes a Release)

1. **Author** in git: prompt template, JSON Schema / GBNF, decoding params, eval suite pointer. Hash = `prompt_hash` / `schema_hash`. DSPy `compile` if used is an **offline job** whose output JSON is the artifact -- this module only **promotes** it.
2. **CI golden-set gate** (fail-closed): pin `eval_suite_id` + dataset `as_of`. Majority-of-3 judges on **ship gates only**. Non-zero exit **blocks merge**.
3. **Register:** immutable version in MLflow / W&B / Hub / SageMaker / Vertex / GitLab. Do **not** retag `production` yet.
4. **Stage alias:** `staging` / `@challenger` / SageMaker `PendingManualApproval`. Preview deploy.
5. **Canary / shadow** via **flags**, not by rewriting the serving binary. Sticky assignment on `user_id` / `thread_id` + experiment salt.
6. **Promote:** move `production` tag / `@champion` / `Approved` + flag 100%. Rollback = **retag previous hash**, not rewrite the blob. **Flag first.**
7. **Observe:** online evals + traces. Kill switch is the **flag**, not a 20-minute image rebuild.

---

## Part 2: Core Mechanics & Algorithms

### 2.1 The Six-Tuple Release Pin

```
release = {
  model_id,          # provider ID or registry URI@alias resolved to version
  prompt_hash,       # git blob / Hub commit / MLflow prompt version
  schema_hash,       # JSON Schema / GBNF / response_format
  decoding_params,   # temperature, top_p, max_tokens, seed, thinking budget
  index_gen,         # embedding model + index alias generation
  eval_suite_id,     # dataset version + scorer versions + k
}
```

**Invariants:**
- **I1.** Any change to any field = new release. Env tag is a pointer. Serving `latest` is not a release.
- **I2.** CI is fail-closed on the versioned golden suite. Online eval is fail-open with coverage%. A red CI with green prod is **correct**.
- **I3.** Traffic split is a control-plane decision. Data plane sees only a resolved bundle. Sticky hash on `thread_id`; do not switch mid-conversation.
- **I4.** Rollback RTO: flag 0% (seconds), then alias retag (MLflow alias cache 60s), then image rebuild (~20 min). Order is mandatory.
- **I5.** Changing `index_gen` / embedding `model_id` without dual-running indexes is a **retrieval incident**, not a prompt hotfix.

### 2.2 Model & Prompt Registry Comparison

| System | Immutable Unit | Promotion Primitive | Key Caveats |
|---|---|---|---|
| **MLflow OSS** | Registered model **version** (integer) | Legacy **stages** (deprecated ~2.9); migrate to aliases `@champion`/`@challenger` | URI: `models:/name@champion`. Alias cache TTL = 60s. Apache 2.0, 55%+ share |
| **Databricks UC** | `catalog.schema.model` version | **Aliases** (Champion/Challenger) + env catalogs (dev/staging/prod) | `copy_model_version` staging->prod. No stages. New versions **require a signature** |
| **W&B Registry** | Artifact **version** v0, v1... linked into a **collection** | **Aliases** unique per collection (`latest`, `production`). **Protected aliases** = Admin-only | `run.link_artifact(..., aliases=["production"])`. Tags are labels, not pointers |
| **HF Hub** | Git **commit SHA** (full 40-char) | **revision** = branch/tag/SHA. Tags are git tags (moveable) | Short 7-char hashes are not the production pin. `main` moves |
| **SageMaker** | **Model package** version | `ModelApprovalStatus`: PendingManualApproval -> Approved/Rejected | Deploy only when Approved. IAM is the PEP. Not prompt-native |
| **Vertex AI** | Model resource + numeric **version_id** | User aliases (`staging`, `production`). `mergeVersionAliases` | **Footgun:** deleting the default version auto-reassigns to most recent |
| **Bedrock PT** | **Not a registry.** Capacity SKU: Model Units billed hourly | Purchase throughput for a model_id you already chose | Interview trap: versioning lives in git/SageMaker, not in PT |
| **GitLab** | Semver versions, MLflow-client compatible | Link version -> CI job. Reporter+ to modify/delete | 5 GB/file via Package Registry |

**MLflow Prompt Registry**: `register_prompt` / `load_prompt`; versions immutable; aliases `@production` / `@latest`; version cache TTL infinite; alias cache TTL 60s (`MLFLOW_ALIAS_PROMPT_CACHE_TTL_SECONDS`). `model_config` (temperature, max_tokens) can sit on the prompt version.

### 2.3 Prompt Versioning: Git SoT vs Hub

| Pattern | Promote Mechanic | When It Wins | Failure Mode |
|---|---|---|---|
| **Git SoT** | PR -> CI eval -> merge -> CD sets Hub/MLflow alias | Audit, CODEOWNERS, `schema_hash` in same diff | Hub playground save bypasses git = **silent drift** |
| **Hub SoT** | Playground commit -> webhook -> CI -> tag `production` | PMs iterate without PRs | CI must pull commit hash, not `latest` |
| **Dual-write** | Git merge pushes Hub; Hub webhook no-ops if hash in git | Migration period | Two writers without hash equality checks |

**LangSmith Hub specifics:** Every save = immutable commit with hash. Commit tags are mutable labels. Reserved tags `staging` and `production` are environments, not freeform. **Owners-only** restricts who can tag/promote/delete. One webhook per workspace. Historical gap: programmatic commit tagging from CI required UI until recently -- verify SDK version.

**OpenAI reusable prompts (`v1/prompts`):** Deprecation announced 2026-06-03; shutdown 2026-11-30. Do not design a 2027 control plane on OpenAI prompt objects.

### 2.4 Model Serving Engines (2026)

| Engine | Status | Throughput (H100) | Best For |
|---|---|---|---|
| **vLLM** | De facto standard, 70% share | ~2,380 tok/s (Llama 70B FP8) | Default for new projects |
| **SGLang** | Strong contender | Competitive, wins on structured output | Agent workloads, tool calls |
| **TGI** | Maintenance mode (Dec 2025) | Lower GPU util (68-74%) | Legacy only. Migrate away |
| **Triton + TRT-LLM** | NVIDIA enterprise | 20-40% higher raw throughput | Fixed model, sustained concurrency |

All four expose OpenAI-compatible APIs. Switching servers = changing a base URL.

### 2.5 Three-Tier Evaluation Architecture

| Tier | When | Metric Class | Fail Mode |
|---|---|---|---|
| **Offline (Pre-Deployment)** | PR / merge / pre-promote | Hard oracle first (schema, DB goal-state, tests). Soft rubric only after hard pass. Majority-of-3 on CI ship. Pin dataset version/tag | Flake vs real drop: temp 0, structured judge, order-swap pairwise. Braintrust default reporter fails on **exceptions only** -- custom `Reporter.reportRun` required |
| **CI/CD Gates** | Every PR touching prompts/models/retrieval | Quality thresholds block merge. Keep core gate **under 10 minutes** (45-min suites get skipped). Full suite runs overnight | Blocked: metric drop AND exceptions. A red CI with green prod is correct -- never skip |
| **Production Monitoring** | Live traffic | Reference-free: safety, JSON schema, latency, sampled LLM-as-judge (5-10%). Fail-open + coverage%. SLO burn-rate alerts, not threshold breaches | Spend cap pauses evaluator, not agent. Coverage collapse != quality green |

**LLM-as-Judge Calibration**: Measure agreement against 20-30 human-labeled examples before using as a CI gate. A judge < 80% agreement with human evaluators is not reliable enough for automated blocking. The dataset matters more than the metric -- most teams spend 80% of effort on metrics and 20% on the dataset. This ratio is backwards.

### 2.6 A/B, Canary, Shadow, and Feature Flags

**Sticky assignment:** Hash `experiment_salt + user_id` (or `thread_id` for multi-turn). Per-request coin flips mix two `prompt_hash` values in one conversation and contaminate session metrics. Change salt between experiments so the same 5% are not perennial guinea pigs.

**Canary:** Flag serves candidate bundle to p% (often 1-5%). Kill = set p=0; RTO is flag TTL, not image deploy. Bundle model + prompt + decoding in one variation.

**Shadow / dark launch:** Candidate runs on sampled live inputs; user still gets control output. Cost = 2x model $ on the sampled slice. Use for fine-tunes and embedding-model swaps where wrong answers must not ship.

**Why LLM A/B needs more N than classic CTR:** Task-unit power (Miller: **n ~ 969** independent questions to detect 3 pp at alpha=0.05, beta=0.20). A 50-item golden set **cannot** support a 3 pp ship claim. LLM output variance stacks on user-behavior variance. CUPED (Deng et al., WSDM 2013) cut Bing metric variance ~50%. Sequential / always-valid p-values (Johari et al., mSPRT) exist because peeking inflates Type I error.

### 2.7 Deployment Strategies

**Blue-Green:** Maintain parallel GPU environments. Green pre-loads and warms before switch. Session-aware load balancing is mandatory -- mid-conversation users cannot be rerouted.

**Canary soak time:** 2 hours on a Tuesday morning is not a canary. Hold for 24-72 hours to cover traffic distribution. LLM-specific canary metrics: output length distribution, hallucination rate, coherence scores, cost per request. HTTP 200 does not mean correct.

**Rollback:** A routing change, not a redeployment. Load balancer shifts 100% back to stable. ~60% of production incidents end in rollback, ~40% in forward-fix. Test rollbacks monthly.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Cost Per 1K CI Tasks (Inferred from Published Rates)

Reference loop: task model GPT-5.6 Luna $0.20/$1.20 per 1M in/out. Judge: Claude Sonnet 4.6 $3/$15 per 1M. Shape: 2,000 in / 400 out per task; judge 1,500 in / 150 out per sample.

| Configuration | Task $ | Judge $ | **Total / 1k Tasks** |
|---|---|---|---|
| Luna only, k=1, no judge | **$0.88** | $0 | **$0.88** |
| Luna + Luna judge x1 | $0.88 | $0.48 | **$1.36** |
| Luna + Luna judge majority-of-3 | $0.88 | $1.44 | **$2.32** |
| Luna + Sonnet 4.6 majority-of-3 | $0.88 | $20.25 | **$21.13** |
| + 10% flake retries on judge only | -- | x1.1 | **$2.46 / $23.16** |

**Miller n ~ 969 budgeted:** ~$2.25 (Luna-m3) or ~$20.50 (Sonnet-m3) for a powered gate. A 50-item PR smoke (`bt eval --first 20`) is ~$0.04-$1.06 but **cannot** underwrite a 3 pp promote claim.

### 3.2 Multi-Model Routing Impact

| Component | Unit Cost | Cost per 1K Runs | Notes |
|---|---|---|---|
| GPT-4o (500 in / 800 out) | $2.50/1M in, $10/1M out | **$9.25** | Frontier reasoning |
| GPT-4o mini (500 in / 800 out) | $0.15/1M in, $0.60/1M out | **$0.56** | 80% routing tier |
| Claude Sonnet 4 (500 in / 800 out) | $3/1M in, $15/1M out | **$13.50** | Complex multi-step |
| Self-hosted vLLM (H100 @ $2.50/hr) | ~$2.50/hr GPU | **$0.69** | At 1 req/s sustained |
| Semantic cache hit | ~$0.0001 (Redis lookup) | **$0.10** | Bypasses LLM entirely |
| Embedding (text-embedding-3-small) | $0.02/1M tokens | **$0.01** | 500 tokens avg |

Route 80% to GPT-4o mini, 20% to GPT-4o: Without routing = $9.25/1k. With routing = $2.30/1k (**75% savings**). Add 25% semantic cache hits: effective cost = **$1.73/1k**.

### 3.3 Canary / Shadow Costs (1M req/mo Luna Baseline = $880/mo)

| Policy | Extra Model $ / Month | Notes |
|---|---|---|
| 5% canary, **same SKU** (prompt-only) | ~$0 | Split does not duplicate tokens |
| 5% canary, treatment 2x SKU | +$44 | 5% of $880 if treatment is 2x |
| 5% shadow | +$44 | 2x model $ on the 5% slice |
| 20% shadow | +$176 | -- |
| 100% shadow forever | **+$880** | Graduate or kill; shadow is a phase |
| 100% promote, 2x SKU | +$880 | Full cutover |

### 3.4 Platform Pricing (2026)

| Platform | Pricing | Key Limits |
|---|---|---|
| **LangSmith** Plus | $39/seat/mo | 10k base traces, extended $.50/trace (400d), base $.05/trace (14d). Ingest: 500k events/hr, 5k POST/runs/min |
| **Braintrust** Pro | $249/mo | 5 GB, 50k scores, 30-day; overage $3/GB, $1.50/1k scores |
| **W&B** Pro | from $60/mo | <=10 seats, 100 GB, 1.5 GB/mo Weave; overage $0.10/MB Weave |
| **HF Hub** Enterprise | $50/user/mo | Inference Endpoints from $0.033/hr (CPU) to $9.25/hr (B200) |
| **MLflow** OSS | $0 (self-host) | You pay compute. Databricks UC: platform SKU |

**For 8 engineers**: LangSmith Plus = $312/mo (8 x $39) before traces. Braintrust Pro = $249/mo. LangSmith 1k-example experiment = ~$5 platform cost (1k extended traces at $.50 each).

### 3.5 Latency -- Two Clocks

**Clock A -- User path (must not include judge):** Flag SDK evaluation is local/cached.

| Component | p50 | p95 | p99 |
|---|---|---|---|
| Flag SDK overhead | 1 ms | 10 ms | 50 ms |
| Customer-facing chat (total) | < 500 ms | < 1.5s | < 3s |
| Internal tools (total) | < 1s | < 3s | < 5s |
| Async batch (total) | < 5s | < 15s | < 30s |
| Inline code completion (total) | < 100 ms | < 200 ms | < 500 ms |

**Clock B -- CI eval job (1k golden, Luna + m3, parallel):**

| Component | p50 | p95 | p99 |
|---|---|---|---|
| PR smoke `--first N` | 30-60 s | -- | -- |
| Full CI golden (1k Luna-m3) | 3 min | 10 min | 30 min |
| LangGraph `/ok` poll | 30s then fail | -- | Health, not eval SLO |

**Promote TTL (time until replicas see the new pointer):**

| Mechanism | Time | Use |
|---|---|---|
| Feature flag | **seconds** (SDK cache TTL) | Incident kill; prompt-only |
| MLflow alias cache | **60s** | Alias retag |
| Hub client cache | ~5 min (unverified) | Do not bet RTO on it |
| Vertex/SageMaker endpoint | **minutes-class** | Weight/endpoint deploy |
| Image rebuild | **~20 min** | Last resort |

**Rollback order: flag first, alias second, image last.**

### 3.6 Throughput & Capacity Planning

- **vLLM**: 85-92% GPU utilization, ~100-150 concurrent requests per GPU. KV cache wastes < 4% of memory.
- **Dynamic batching**: Hold requests 5-15ms. GPU utilization: ~40% -> 75-85%. Per-request p95 latency drops 25-35%.
- **KV cache-aware routing** (llm-d): 87% cache hit rate, 88% faster TTFT for warm hits.
- **Semantic cache**: Cosine similarity > 0.95 bypasses LLM entirely. 25% hit rate is typical for support.

### 3.7 Availability & RPO/RTO

| Component | Availability | RPO | RTO |
|---|---|---|---|
| LLM Gateway | 99.95% | N/A (stateless) | < 30s (failover) |
| Prompt Registry | 99.99% | 0 (Git-backed) | < 5 min (git clone) |
| Vector Store | 99.9% | < 1 min | < 15 min (rebuild) |
| Eval Dataset | 99.99% | 0 (version-controlled) | < 5 min |
| Telemetry / Traces | 99.5% | < 5 min | < 30 min |
| Canary kill | -- | -- | **seconds** (flag 0%) |
| Alias retag | -- | -- | 60s class (MLflow) |
| Full image rollback | -- | -- | ~20 min |

**Serving during control-plane outage**: last resolved bundle via fail-open flags. CI red / prod green is **correct** -- the gate is not a liveness probe.

### 3.8 Caching on Prompt Promote

A new `prompt_hash` changes the system prefix -> compulsory miss and write-rate (Anthropic 1.25x, OpenAI 2x) on the new prefix until cache fills. Semantic caches that omit `prompt_hash` from the key will **serve the old completion**. Include `(model_id, prompt_hash, schema_hash, decoding_params)` in the application cache key. On promote, expect a **stampede** of cache writes -- pre-warm the new prefix if the system prompt is large.

---

## Part 4: Distributed Resilience & Security

### 4.1 Failure Taxonomy

| Class | Examples | Detection | Handling |
|---|---|---|---|
| **Silent quality regression** | Provider-side silent model change, prompt drift | Production eval sampling, output distribution drift | SLO burn-rate alerts, pin model versions, continuous eval |
| **Transient (flake)** | Judge 429/5xx, LangSmith 5k POST/min, TPM | Error-rate bucket separate from quality | Full-jitter retry on idempotent score HTTP. Never retry the user path. Never skip the merge gate |
| **Real metric drop** | Hard-oracle / task metric down vs pinned suite | CI gate; paired A vs B on same task IDs | Fail-closed; do not merge. Majority-of-3 for CI, not prod |
| **Poison golden** | PAN in fixture; MTEB items in gate set; employees' favorite examples | Hold-out drop; online != CI; every PR red -> teams skip | Detect-redact-audit before git/Hub. Separate optimize vs gate suites |
| **Agentic cascade** | Wrong first tool cascades into bad state | Trace analysis, step-level eval | Per-step validation, circuit breakers |
| **Cost explosion** | Uncontrolled reasoning tokens, 100% shadow forever | Per-request cost tracking, daily anomaly alerts | Token budgets, kill switch for agent loops |
| **Idempotency** | Two `set_alias` races; promote twice | Split-brain alias | CAS / If-Match; single-writer Temporal |
| **Denial of wallet** | Nightly extended-trace evals; Sonnet-m3 on every PR | Invoice | `--first N` on PR; Flex/Batch for offline |

### 4.2 Sample Size Requirements for Eval

| Effect Size | Required Samples | Practical Implication |
|---|---|---|
| Large (10%+ change) | 50-100 | Quick sanity check |
| Moderate (5-10%) | 200-500 | Standard CI gate |
| Small (1-3%) | 500-2,000+ | Production A/B test |

Formula: `n >= 16 * p * (1-p) / delta^2` at 80% power, 5% significance, plus 1.5x buffer. Miller: **n ~ 969** for 3 pp. A test with 30 examples that reports +0.03 delta has resolved nothing.

### 4.3 Circuit Breaker: Closed -> Open -> Half-Open

Independent breakers on the **CI judge API** and the **registry API**. A 429 on the judge must not stall prod serving and must not skip the merge gate.

```
        judge 429/5xx | registry 5xx | alias CAS conflict | ingest 429
  +----------+  ------------------------------------------------->  +----------+
  |  CLOSED  |                                                       |   OPEN   |
  |  judge / |  success resets consecutive count                     | FAIL FAST|
  |  registry|                                                       | fallback |
  +----+-----+                                                       +----+-----+
       ^                                                                  | cooldown
       | probe OK                                                   +-----v------+
       +-------------- probe allow -------------------------------- | HALF-OPEN  |
                       probe fail -> stay OPEN                      | 1 canary   |
                                                                    | (not user) |
                                                                    +------------+
```

| Trip | Closed -> Open | Fallback |
|---|---|---|
| **CI judge API** 429/5xx | consecutive >= 5 | Retry with jitter; if still open: **fail the job** (merge blocked). Never skip the merge gate |
| **Online sidecar judge** | same | Skip score, decrement coverage%. Prod stays up |
| **Registry API** 5xx/timeout | consecutive >= 3 | Serve last-known bundle from local cache. Fail-open flags |
| **Canary hard-oracle / 5xx** | n/a | Flag 0% within SDK TTL. Keep `@champion` undeleted |

**Fallback chain:** last-known bundle (fail-open flags) -> prior `@champion` alias -> previous Deployment revision / endpoint variant.

### 4.4 Zero-Trust MCP for Promotion

Zero-Trust for promotion lives **here**, not in a separate module. Tools: `eval.run`, `registry.set_alias`, `promote_tag`.

| Control | On This Promotion Layer |
|---|---|
| **Transport** | OAuth 2.1 + PKCE S256. RFC 8707 `resource` = canonical MCP server URI. MUST NOT passthrough client token to Hub/MLflow/SageMaker; obtain new token (RFC 8693 exchange) scoped to upstream |
| **Capability** | `initialize` + tool list. Allowlist: `eval.run` on staging suites for assistants; `registry.set_alias` / `promote_tag` = human + break-glass only |
| **Hash-pin** | `toolSurfaceHash` over canonical JSON of name+description+inputSchema. Re-verify every `tools/call`. Mismatch -> session pause |
| **Identity** | Verified access token from IdP. Never the LLM. Tool arg `actor` is discarded |

**Tool-Level RBAC:**

| Tool | Who | Allowed | Forbidden |
|---|---|---|---|
| `eval.run` | CI; assistants staging only | Pinned suite + pinned VERSION | Prod `@latest`; promote-from-smoke |
| `registry.set_alias` | Release manager + HITL | If-Match retag; `production` only with ticket + experiment_id | Assistant; deleting version prod names |
| `promote_tag` | Release manager + HITL | Hub reserved env tags after gate | Skipping CI; shared "prod" API key |
| Read version / `get` | Runtime / assistant | Already-promoted hash | Draft hashes; other tenants |
| Delete version | GC job + break-glass | Versions not named by prod aliases | Model-called delete |

### 4.5 RBAC for LLM Infrastructure

| Role | Permissions | Example |
|---|---|---|
| Inference Consumer | Execute queries against approved models | Application service accounts |
| Prompt Engineer | Modify system prompts, retrieval configs | ML engineers, product managers |
| Model Administrator | Deploy, update, remove model weights | Platform team |
| Auditor | Read-only: inference logs, security events | Compliance, security |

### 4.6 PII Pipeline: Detect -> Redact -> Audit Before Goldens Enter Git/Hub

Golden examples are training-adjacent. LangSmith dataset retention is **indefinite** once added.

1. **Detection (control plane):** Dual-gate: regex (email, PAN, SSN, phones) + ML NER if available. Scan git fixtures, Hub commits, MLflow prompt text, eval dataset rows. If ML is down: **fail closed** on PAN/SSN -- do not "add it and DLP later."
2. **Redaction:** Stable tokens (`[EMAIL_<hash12>]`, `[PAN]`) so task labels still match. Do not store raw prod traces as goldens if Hub/LangSmith ACL is weaker than source.
3. **Audit trail (WORM):** Decisions, not values: `content_sha256` pre/post, entity types + counts, action, detector, correlation_id. Log the data category, not the PII itself.

### 4.7 Regulatory Compliance

- **EU AI Act**: High-risk system obligations landing Aug 2026
- **GDPR/CCPA**: PII detection and removal in both LLM inputs and outputs
- **Key frameworks**: NIST AI RMF, ISO/IEC 42001:2023, OWASP Top 10 for Agentic Apps (Dec 2025), CSA Agentic Trust Framework (Feb 2026)
- **IBM 2025 Cost of a Data Breach**: 97% of organizations with AI-related breaches lacked proper AI access controls

---

## Part 5: Production Enterprise Code

```python
"""LLMOps promotion control plane: CI gate, sticky canary, alias CAS,
last-known bundle fallback, PII detection, circuit breaker.
Run: python llmops_promote_runtime.py
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
MLFLOW_ALIAS_TTL_S = 60.0
METRIC_FLOOR = 0.85

# ---- Release Bundle (the 6-tuple pin) ------------------------------------
@dataclass(frozen=True)
class ReleaseBundle:
    model_id: str; prompt_hash: str; schema_hash: str
    decoding_params: str; index_gen: str; eval_suite_id: str
    version: int = 0
    def tuple_id(self) -> str:
        raw = "|".join([self.model_id, self.prompt_hash, self.schema_hash,
                        self.decoding_params, self.index_gen,
                        self.eval_suite_id, str(self.version)])
        return "rel_" + hashlib.sha256(raw.encode()).hexdigest()[:16]

@dataclass(frozen=True)
class AuthContext:
    principal: str; roles: frozenset[str]; tenant_id: str

# ---- Circuit Breaker (closed -> open -> half-open) ------------------------
class CircuitState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

class CircuitOpenError(RuntimeError): pass
class GateFailed(RuntimeError): pass

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

# ---- PII Pipeline: detect -> redact -> audit ------------------------------
def pii_detect_redact(text: str, *, audit: list[dict], cid: str,
                      tenant: str, sink: str) -> str:
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE))
             if rx.search(text)]
    pre_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    if "pan" in kinds and sink in {"golden", "hub_commit"}:
        audit.append({"cid": cid, "tenant": tenant, "sink": sink,
                      "action": "block", "kinds": kinds, "pre": pre_hash})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": cid, "tenant": tenant, "sink": sink,
                  "action": "redact" if redacted != text else "allow",
                  "kinds": kinds, "pre": pre_hash})
    return redacted

# ---- Sticky Canary Assignment ---------------------------------------------
def sticky_canary(thread_id: str, salt: str, pct: int) -> bool:
    if pct <= 0: return False
    if pct >= 100: return True
    h = int(hashlib.sha256(f"{salt}:{thread_id}".encode()).hexdigest()[:8], 16)
    return (h % 100) < pct

@dataclass
class FlagStore:
    pct: int = 0; salt: str = "exp-1"
    candidate: ReleaseBundle | None = None
    _resolved: ReleaseBundle | None = None
    def assign(self, thread_id: str, control: ReleaseBundle) -> ReleaseBundle:
        cand = self.candidate or control
        return cand if sticky_canary(thread_id, self.salt, self.pct) else control
    def kill(self) -> None:
        self.pct = 0

# ---- Registry with CAS Promote -------------------------------------------
@dataclass
class Registry:
    versions: dict[int, ReleaseBundle] = field(default_factory=dict)
    aliases: dict[str, int] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    _next: int = 1
    def register(self, bundle: ReleaseBundle) -> ReleaseBundle:
        ver = self._next; self._next += 1
        pinned = replace(bundle, version=ver)
        self.versions[ver] = pinned; return pinned
    def set_alias(self, name: str, version: int, *, etag: str | None,
                  actor: str) -> str:
        if version not in self.versions:
            raise KeyError("unknown_version")
        current = self.etags.get(name, "")
        if etag is not None and current and etag != current:
            raise ValueError(f"cas_lost:{name}")  # CAS conflict
        self.aliases[name] = version
        new_etag = hashlib.sha256(f"{name}:{version}:{actor}".encode()).hexdigest()[:12]
        self.etags[name] = new_etag; return new_etag
    def resolve(self, name: str) -> ReleaseBundle:
        if name not in self.aliases: raise KeyError(f"no_alias:{name}")
        return self.versions[self.aliases[name]]

# ---- Promotion Runtime ---------------------------------------------------
@dataclass
class PromoteRuntime:
    registry: Registry; flags: FlagStore
    judge_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("ci_judge"))
    reg_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("registry", failure_threshold=3))
    audit: list[dict] = field(default_factory=list)
    worm: list[dict] = field(default_factory=list)
    last_known: ReleaseBundle | None = None

    def ci_gate(self, *, bundle: ReleaseBundle, score: float,
                cid: str, experiment_id: str) -> None:
        """Fail-closed CI gate. Never skip the merge gate."""
        if score < METRIC_FLOOR:
            raise GateFailed(f"metric_drop:{score:.3f}<{METRIC_FLOOR}")

    def promote(self, *, auth: AuthContext, alias: str, bundle: ReleaseBundle,
                prev_etag: str, experiment_id: str, ticket_id: str,
                cid: str) -> str:
        if "promote" not in auth.roles:
            raise PermissionError("promote_denied")
        pinned = bundle if bundle.version else self.registry.register(bundle)
        etag = self.registry.set_alias(alias, pinned.version,
                                       etag=prev_etag or None, actor=auth.principal)
        self.last_known = pinned
        self.worm.append({
            "ts": time.time(), "actor_id": auth.principal, "action": "promote",
            "prompt_hash": pinned.prompt_hash, "alias": alias,
            "version": pinned.version, "experiment_id": experiment_id,
            "prev_etag": prev_etag, "ticket_id": ticket_id,
        })
        return etag

    def serve(self, *, thread_id: str, control: ReleaseBundle) -> ReleaseBundle:
        try:
            champion = self.registry.resolve("champion")
            self.last_known = champion
        except (KeyError, TimeoutError):
            champion = self.last_known or control  # fail-open last-known
        return self.flags.assign(thread_id, champion)

    def rollback_flag_first(self) -> None:
        self.flags.kill()  # RTO = seconds

def cache_key(bundle: ReleaseBundle, user_hash: str) -> str:
    """Include prompt_hash in cache key or stale completions survive promote."""
    return "|".join([bundle.model_id, bundle.prompt_hash,
                     bundle.schema_hash, bundle.decoding_params, user_hash])

if __name__ == "__main__":
    rt = PromoteRuntime(registry=Registry(), flags=FlagStore())
    control = rt.registry.register(ReleaseBundle(
        model_id="gpt-5.6-luna", prompt_hash="p_aaa", schema_hash="s_v1",
        decoding_params="t0_max400", index_gen="idx-3", eval_suite_id="gate-v4",
    ))
    rt.registry.set_alias("champion", control.version, etag=None, actor="bootstrap")
    rt.last_known = control
    # CI gate pass
    rt.ci_gate(bundle=control, score=0.91, cid="cid-1", experiment_id="exp-77")
    # PII block on golden set
    try:
        pii_detect_redact("card 4111 1111 1111 1111", audit=rt.audit,
                          cid="cid-pii", tenant="acme", sink="golden")
    except PermissionError: pass
    assert any(r["action"] == "block" for r in rt.audit)
    # Sticky canary
    rt.flags.candidate = rt.registry.register(ReleaseBundle(
        "gpt-5.6-luna", "p_bbb", "s_v1", "t0_max400", "idx-3", "gate-v4"))
    rt.flags.pct, rt.flags.salt = 5, "salt-a"
    a = rt.serve(thread_id="thr-1", control=control)
    b = rt.serve(thread_id="thr-1", control=control)
    assert a.tuple_id() == b.tuple_id()  # sticky
    # Rollback
    rt.rollback_flag_first()
    assert rt.flags.pct == 0
    print(f"ok: worm={len(rt.worm)} audit={len(rt.audit)} cache_key={cache_key(control, 'u1')}")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Prompt-Only SaaS with Weekly Iteration

**Problem:** B2B copilot, one provider model_id (Luna), 8 product teams iterating on prompts 5-10 times per week. No quality gate -- prompt changes go live via feature flags, and regressions are caught by customer complaints. PMs want Hub playground velocity. Security wants git audit and CODEOWNERS. Need: unified CI/CD, 5% blast-radius, flag kill in seconds. Nobody should rebuild Agent Server to change a system prompt.

**Architecture:** Git SoT + Hub tags + owners-only + CI golden + 5% flag canary.

**Trade-off matrix:**

| Decision | Option A: Playground Hub as SoT | Option B: Git SoT + Hub tags + flags | Chosen | Rationale |
|---|---|---|---|---|
| Source of truth | Hub playground | Git + CODEOWNERS | **B** | Playground save = silent drift; git has audit |
| Eval gate | None | CI fail-closed golden + m3 | **B** | 45-min suite = no suite; keep under 10 min |
| Rollout | 100% deploy | 5% canary via flags | **B** | Flag kill in seconds; ~$0 extra same-SKU |
| Cache | Ignore prompt_hash | Key includes prompt_hash | **B** | Stale completions survive promote otherwise |
| Promote target | Agent Server revision | Flag in front of graph | **B** | Prompt-only does not need image rebuild |

### Scenario 2: Multi-Model Cost Optimization for High-Volume Support

**Problem:** 500K customer support conversations/day through single GPT-4o. Monthly spend $180K, growing 15% MoM. CFO mandated 60% cost reduction without degrading CSAT (currently 4.2/5.0).

**Architecture:** Semantic cache (25% hit) + intent classifier + multi-model routing (65% mini, 15% GPT-4o, 20% API lookup bypassing LLM). Eval sampler on 5% of live traffic.

| Component | Before (Monthly) | After (Monthly) |
|---|---|---|
| All traffic through GPT-4o | $180,000 | -- |
| Semantic cache (25% hit rate) | -- | $200 (Redis) |
| GPT-4o mini (65% of remaining) | -- | $10,530 |
| GPT-4o (15% of remaining) | -- | $34,690 |
| API lookups, no LLM (20%) | -- | $500 |
| Classifier overhead | -- | $2,000 |
| **Total** | **$180,000** | **$47,920 (73% savings)** |

**Trade-off matrix:**

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Cache threshold | 0.90 (higher hit rate) | 0.95 (higher precision) | 0.95 | False cache hits in support are worse than misses |
| Cheap model | GPT-4o mini | Self-hosted Llama 70B | GPT-4o mini | Operational simplicity; self-hosted adds $8K/mo |
| Quality monitoring | Sample 5% | Sample 10% | 5% | 25K scored samples/day is statistically sufficient |
| Fallback on GPT-4o outage | Queue and retry | Route to Claude | Route to Claude | Support SLA requires < 5s response |

---

## Common Failure Modes

| Failure | Mechanism | Mitigation |
|---|---|---|
| **Silent prompt drift** | Playground/Hub save; git unchanged; app pulls `latest` | Git SoT + owners-only + webhook-gated CI |
| **Eval-set overfitting** | Prompt compiled against the gate set (judge-as-loss) | Separate optimize vs gate suites; freeze gate eval_suite_id |
| **50-item 3 pp claim** | Smoke suite as ship proof | Miller n~969; `--first N` is non-final |
| **A/B peeking** | Dashboard p<0.05 early stop inflates ship rate | Always-valid / mSPRT (Johari); pre-registered N |
| **Judge on user p99** | Online m3 on the request thread | Sidecar; coverage%; never inflate extraction SLO |
| **Skip merge gate on judge 429** | "CI flake, merge anyway" | Circuit open -> fail the job; skip score only on online sidecar |
| **Canary too small** | 1% of low-traffic; N << 969 | Canary is blast-radius limiter; power from golden + CUPED |
| **Cache serves old completion** | App cache omits prompt_hash | Key = (model_id, prompt_hash, schema_hash, decoding_params) |
| **MLflow stages in 2026** | `models:/name/Production` | Deprecated since ~2.9; use `@champion` / `@challenger` |
| **Bedrock PT as "registry"** | Team thinks MU purchase versioned the model | PT = capacity; version in git/SageMaker |
| **Mid-thread promote** | Alias TTL expires; next turn gets new hash | Sticky thread_id; drain |
| **100% shadow forever** | Fear of cutover | +$880/mo on 1M Luna; graduate or kill |
| **MCP promote from the model** | Tool descriptions in context | Gateway PEP; assistants eval.run staging only |
| **PII golden in LangSmith** | Raw prod trace -> dataset with indefinite retention | Detect-redact-audit before add; GDPR deletes blob AND digest |
| **OpenAI Evals hard outage** | Read-only 2026-10-31, shutdown 2026-11-30 | Migrate gate to Promptfoo / pytest-langsmith / Braintrust |
| **Braintrust green on exceptions-only** | Default reporter ignores score drop | Custom Reporter.reportRun |
| **Extended-trace surprise bill** | Online evaluator retention upgrade; experiments extended-by-default | Opt out per evaluator; spend limits |
| **schema_hash mismatch** | Hub response_format != deployed GBNF | CI equality; decoder constraint in same PR as prompt |

---

## Interview Q&A

**Q1: What is LLMOps, in one sentence?**
I treat it as a promotion control plane: git authors the bundle, CI is a fail-closed gate, a registry stores immutable versions, aliases are mutable pointers, and feature flags are the blast-radius dial. I pin `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`. Tags are pointers. `latest` is not a release. The judge runs off user p99.

**Q2: Walk me through your CI/CD path.**
Author in git. PR smoke `--first N`. Merge: full golden, fail-closed, majority-of-3 if the metric is an LLM-judge. Register an immutable version; do not retag production. Stage alias / `@challenger`. Flag 5% sticky on `thread_id`. Promote with CAS (who, prev_hash, new_hash, experiment_id) plus flag 100%. Observe sidecar traces. Kill = flag 0% in seconds. I would not rebuild Agent Server to change a prompt tag.

**Q3: MLflow stages vs aliases vs Bedrock PT?**
Stages None/Staging/Production/Archived are deprecated since ~2.9; I use `@champion` and `@challenger` so two versions can run. UC has no stages; Champion/Challenger aliases plus catalog copy. W&B uses protected aliases (Admin-only). HF I pin the full 40-char SHA. SageMaker is PendingManualApproval -> Approved. Vertex `default` auto-moves on delete -- a footgun. Bedrock PT is capacity, not a registry.

**Q4: What is a run vs an experiment vs a release?**
A run is one execution of a pinned bundle on one example (offline) or one live request (online). The experiment is the matrix of runs. The release is the alias move after the experiment clears the gate. Hub commit is immutable; Hub tag is a pointer. I would not log a prompt edit as a metric on yesterday's `.pkl` run.

**Q5: Give me dollar-per-1k for the CI loop.**
Luna only $0.88/1k. Luna + Luna judge $1.36. Luna + Luna majority-of-3 $2.32. Luna + Sonnet m3 $21.13. Miller n~969 costs ~$2.25 (Luna-m3) or ~$20.50 (Sonnet-m3) for a powered gate. A 50-item smoke cannot underwrite a 3 pp ship claim. Sonnet m3 as the default ship judge is the cost trap: $21.13 vs $2.32 per 1k.

**Q6: Two clocks -- what latency do you quote?**
User path: flag SDK p50 1 / p95 10 / p99 50 ms on top of extraction p50 1,200 / p95 4,000 / p99 12,000 ms -- I will not inflate that with a judge. CI 1k Luna-m3: p50 3 min / p95 10 min / p99 30 min. Promote TTL: flags seconds, MLflow alias 60s, endpoints minutes-class, image rebuild ~20 min. Rollback order: flag first, alias second, image last.

**Q7: How do you handle the circuit breaker?**
Independent breakers on CI judge API and registry API. Judge outage: fail the CI job -- never skip merge. Online sidecar: skip score, decrement coverage%. Registry outage: serve last-known bundle, fail-open flags. Canary kill = flag 0%. Fallback chain: last-known bundle -> prior `@champion` -> previous revision/endpoint. I would not serve `latest` as a fallback.

**Q8: What about A/B testing for LLMs?**
Miller n~969 for 3 pp at alpha=0.05, beta=0.20. A 50-item golden set cannot support that. CUPED cut Bing metric variance ~50% using pre-experiment covariates. Peeking without mSPRT inflates Type I error. A 5% canary on a low-traffic product is underpowered -- it is a blast-radius limiter, not the statistical proof. Power comes from the golden N.

**Q9: How do you handle prompt caching across promotes?**
New prompt_hash busts the prefix cache -- compulsory miss plus Anthropic/OpenAI write-rate 1.25x/2x. App cache key must include (model_id, prompt_hash, schema_hash, decoding_params) or it serves stale completions. MLflow alias cache is 60s -- replicas can serve the old pointer for up to 60s after set_prompt_alias. Pre-warm the new prefix if the system prompt is large.

**Q10: Zero-Trust MCP for promotion?**
I put the PEP here. Tools: eval.run, registry.set_alias, promote_tag. OAuth 2.1, RFC 8707 resource = this MCP server, no token passthrough to Hub or MLflow (RFC 8693 exchange), hash-pin tool JSON and server digest. Identity from RunContext, not JSON `actor`. Assistants get eval.run on staging only. Promote is human + break-glass. MCP is not the registry PEP -- IAM, UC grants, owners-only, and protected aliases are.

**Q11: What is your biggest anti-pattern?**
Treating models as the only artifact that matters. In practice, prompts, tool schemas, evaluators, and datasets break reproducibility just as easily. A close second: playground-as-SoT causing silent drift, and Sonnet majority-of-3 as the default ship judge at $21.13/1k when Luna-m3 at $2.32/1k is sufficient for most gates.

**Q12: How do you use production failures constructively?**
Sample and score traces, queue ambiguous ones for review, then add confirmed failures back into the dataset so CI catches them next time. But redact PII first -- LangSmith dataset retention is indefinite once added. A raw prod trace promoted to golden without redaction is a retention extension forever.

---

## Key Numbers to Memorize

| Number | What |
|---|---|
| **6-tuple release pin** | `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)` |
| **`@champion` / `@challenger`** | MLflow aliases; stages deprecated since ~2.9 |
| **40-char SHA** | HF production pin; 7-char / `main` are not |
| **n ~ 969** | Miller: independent questions for 3 pp at alpha=0.05, beta=0.20 |
| **CUPED ~50%** | Bing metric variance reduction (Deng et al., WSDM 2013) |
| **$0.88 / $2.32 / $21.13** | Luna-only / Luna-m3 / Sonnet-m3 per 1k CI tasks |
| **$880/mo** | 1M req/mo Luna 2k/400 baseline |
| **~$0 / +$44 / +$880** | 5% same-SKU canary / 5% shadow / 100% shadow monthly delta |
| **60s** | MLflow alias prompt cache TTL |
| **seconds / 60s / ~20 min** | Flag RTO / alias retag RTO / image rebuild RTO |
| **0.95** | Semantic cache cosine threshold |
| **73%** | Cost savings from routing + cache + API bypass |
| **2026-11-30** | OpenAI hosted Evals shutdown |
| **5k POST/runs/min** | LangSmith ingest rate limit |
| **99.5%** | Bedrock Reserved capacity target -- not a registry SLO |

---

## Quick Reference

- **Release**: 6-tuple pin. Tags are pointers. `latest` is not a release.
- **Path**: git -> CI fail-closed -> register -> stage -> 5% canary -> promote -> observe.
- **Rollback**: flag first (seconds), alias second (60s), image last (~20 min).
- **Two clocks**: user path (flag SDK ~1ms, never includes judge) vs CI path (3-30 min).
- **Registry**: MLflow aliases, not stages. HF full SHA, not 7-char. Vertex default auto-moves. Bedrock PT = capacity.
- **A/B**: Miller n~969. CUPED ~50%. mSPRT for peeking. 50-item smoke is non-final.
- **Cost**: Sonnet-m3 $21.13/1k vs Luna-m3 $2.32/1k. 100% shadow = +$880/mo.
- **Circuit breaker**: CI judge outage = fail the job. Online sidecar = skip score. Registry = last-known bundle.
- **Zero-Trust MCP**: promote_tag = human only. eval.run = staging only for assistants. No token passthrough.
- **PII**: detect-redact-audit BEFORE goldens enter git/Hub. LangSmith retention is indefinite.
- **Cache**: Include prompt_hash in key. Pre-warm on promote. Expect write stampede.
